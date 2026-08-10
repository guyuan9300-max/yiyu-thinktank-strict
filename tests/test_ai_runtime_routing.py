from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

import httpx
import pytest

from backend.app.runtime import (
    LocalRuntimeError,
    WorkspaceContext,
    WorkspaceRuntime,
)
from backend.app.secret_store import MemorySecretStore


def _context(suffix: str) -> WorkspaceContext:
    return WorkspaceContext(
        sandbox_id=f"sandbox-{suffix}",
        cloud_instance_id=f"cloud-{suffix}",
        organization_id=f"org-{suffix}",
        cloud_api_url=f"https://cloud-{suffix}.invalid",
        principal_id=f"principal-{suffix}",
        membership_id=f"membership-{suffix}",
        access_token=f"access-{suffix}",
        refresh_token=f"refresh-{suffix}",
        access_expires_at=None,
        refresh_expires_at=None,
    )


def _runtime_with_routing(
    *,
    mode: str,
    profiles: dict[str, dict[str, Any]],
) -> tuple[WorkspaceRuntime, WorkspaceContext]:
    context = _context("a")
    runtime = object.__new__(WorkspaceRuntime)
    runtime_document = {
        "state": "ready_direct",
        "provider": "openai_compatible",
        "baseUrl": "https://main-model.invalid/v1",
        "modelName": "main-model",
        "routing": {
            "state": "ready",
            "enabled": True,
            "mode": mode,
            "effectiveScopeKind": "organization",
            "precedence": "personal_over_organization",
            "profiles": profiles,
        },
    }
    runtime.__dict__["_current_context"] = lambda **_: context
    runtime.__dict__["_connection"] = lambda: nullcontext(object())
    runtime.__dict__["_current_ai_runtime"] = lambda *_: runtime_document
    runtime.__dict__["secret_store"] = MemorySecretStore()
    runtime.secret_store.set(runtime._ai_ref(context.sandbox_id), "main-secret")
    return runtime, context


class _Response:
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


def _provider_client(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: dict[str, _Response | Exception],
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []

    class FakeClient:
        def __init__(self, **kwargs: Any):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def post(self, url: str, **kwargs: Any) -> _Response:
            requests.append({"url": url, **kwargs})
            outcome = outcomes[url]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    monkeypatch.setattr("backend.app.runtime.httpx.Client", FakeClient)
    return requests


def test_local_first_selects_local_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, context = _runtime_with_routing(
        mode="local_first",
        profiles={
            "online_primary": {
                "enabled": True,
                "provider": "openai_compatible",
                "baseUrl": "https://online.invalid/v1",
                "model": "online-model",
            },
            "local_text_deep": {
                "enabled": True,
                "provider": "ollama",
                "baseUrl": "http://127.0.0.1:11434/v1",
                "model": "local-deep",
            },
        },
    )
    runtime.secret_store.set(
        runtime._ai_profile_ref(context.sandbox_id, "online_primary"),
        "online-secret",
    )
    requests = _provider_client(
        monkeypatch,
        {
            "http://127.0.0.1:11434/v1/chat/completions": _Response(
                200,
                {"choices": [{"message": {"content": "local answer"}}]},
            ),
            "https://online.invalid/v1/chat/completions": _Response(
                200,
                {"choices": [{"message": {"content": "online answer"}}]},
            ),
        },
    )

    result = WorkspaceRuntime.private_ai_completion(
        runtime,
        system_prompt="system",
        prompt="question",
    )

    assert result["content"] == "local answer"
    assert result["routeProfile"] == "local_text_deep"
    assert result["routingMode"] == "local_first"
    assert [item["url"] for item in requests] == [
        "http://127.0.0.1:11434/v1/chat/completions"
    ]


def test_online_first_falls_back_only_after_retryable_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, context = _runtime_with_routing(
        mode="online_first",
        profiles={
            "online_primary": {
                "enabled": True,
                "provider": "openai_compatible",
                "baseUrl": "https://online.invalid/v1",
                "model": "online-model",
            },
            "local_text_deep": {
                "enabled": True,
                "provider": "ollama",
                "baseUrl": "http://localhost:11434/v1",
                "model": "local-deep",
            },
        },
    )
    runtime.secret_store.set(
        runtime._ai_profile_ref(context.sandbox_id, "online_primary"),
        "online-secret",
    )
    requests = _provider_client(
        monkeypatch,
        {
            "https://online.invalid/v1/chat/completions": _Response(
                503,
                {"error": {"message": "temporary"}},
            ),
            "http://localhost:11434/v1/chat/completions": _Response(
                200,
                {"choices": [{"message": {"content": "fallback answer"}}]},
            ),
        },
    )

    result = WorkspaceRuntime.private_ai_completion(
        runtime,
        system_prompt="system",
        prompt="question",
    )

    assert result["content"] == "fallback answer"
    assert result["routeProfile"] == "local_text_deep"
    assert [item["url"] for item in requests] == [
        "https://online.invalid/v1/chat/completions",
        "http://localhost:11434/v1/chat/completions",
    ]


def test_auth_rejection_does_not_fallback_or_leak_provider_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, context = _runtime_with_routing(
        mode="online_first",
        profiles={
            "online_primary": {
                "enabled": True,
                "provider": "openai_compatible",
                "baseUrl": "https://online.invalid/v1",
                "model": "online-model",
            },
            "local_text_deep": {
                "enabled": True,
                "provider": "ollama",
                "baseUrl": "http://localhost:11434/v1",
                "model": "local-deep",
            },
        },
    )
    secret = "must-never-appear-in-errors"
    runtime.secret_store.set(
        runtime._ai_profile_ref(context.sandbox_id, "online_primary"),
        secret,
    )
    requests = _provider_client(
        monkeypatch,
        {
            "https://online.invalid/v1/chat/completions": _Response(
                401,
                {"error": {"message": f"bad credential {secret}"}},
            ),
            "http://localhost:11434/v1/chat/completions": _Response(
                200,
                {"choices": [{"message": {"content": "must not run"}}]},
            ),
        },
    )

    with pytest.raises(LocalRuntimeError) as error:
        WorkspaceRuntime.private_ai_completion(
            runtime,
            system_prompt="system",
            prompt="question",
        )

    assert error.value.code == "ai_request_rejected"
    assert secret not in error.value.message
    assert len(requests) == 1


def test_local_only_without_loopback_profile_never_opens_provider_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, context = _runtime_with_routing(
        mode="local_only",
        profiles={
            "online_primary": {
                "enabled": True,
                "provider": "openai_compatible",
                "baseUrl": "https://online.invalid/v1",
                "model": "online-model",
            },
            "local_text_deep": {
                "enabled": True,
                "provider": "openai_compatible",
                "baseUrl": "https://remote-deep.invalid/v1",
                "model": "remote-deep",
            },
        },
    )
    runtime.secret_store.set(
        runtime._ai_profile_ref(context.sandbox_id, "online_primary"),
        "online-secret",
    )
    runtime.secret_store.set(
        runtime._ai_profile_ref(context.sandbox_id, "local_text_deep"),
        "remote-deep-secret",
    )
    opened = {"value": False}

    class ForbiddenClient:
        def __init__(self, **_kwargs: Any):
            opened["value"] = True
            raise AssertionError("local_only must not open a remote provider")

    monkeypatch.setattr("backend.app.runtime.httpx.Client", ForbiddenClient)

    with pytest.raises(LocalRuntimeError) as error:
        WorkspaceRuntime.private_ai_completion(
            runtime,
            system_prompt="system",
            prompt="question",
        )

    assert error.value.code == "local_ai_profile_not_ready"
    assert opened["value"] is False


def test_routing_sync_is_sandbox_isolated_and_persists_no_plaintext_secret() -> None:
    runtime = object.__new__(WorkspaceRuntime)
    runtime.__dict__["secret_store"] = MemorySecretStore()
    snapshots: dict[str, dict[str, Any]] = {}
    secrets = {
        "sandbox-a": ("main-secret-a", "profile-secret-a"),
        "sandbox-b": ("main-secret-b", "profile-secret-b"),
    }

    class FakeCloud:
        def __init__(self, context: WorkspaceContext):
            self.context = context

        def ai_runtime_secret(self, _access_token: str) -> dict[str, Any]:
            main_secret, _ = secrets[self.context.sandbox_id]
            return {
                "cloudInstanceId": self.context.cloud_instance_id,
                "organizationId": self.context.organization_id,
                "provider": "openai_compatible",
                "baseUrl": f"https://main-{self.context.organization_id}.invalid/v1",
                "modelName": "main-model",
                "apiKey": main_secret,
                "keyFingerprint": f"fingerprint-{self.context.organization_id}",
                "configVersion": 1,
            }

        def ai_routing_runtime_secret(
            self,
            _access_token: str,
        ) -> dict[str, Any]:
            _, profile_secret = secrets[self.context.sandbox_id]
            return {
                "cloudInstanceId": self.context.cloud_instance_id,
                "organizationId": self.context.organization_id,
                "advancedAiRoutingEnabled": True,
                "aiModelMode": "online_first",
                "effectiveScopeKind": "personal",
                "version": 3,
                "profiles": {
                    "online_primary": {
                        "enabled": True,
                        "provider": "openai_compatible",
                        "baseUrl": (
                            f"https://profile-{self.context.organization_id}"
                            ".invalid/v1"
                        ),
                        "model": "profile-model",
                        "apiKey": profile_secret,
                    }
                },
            }

    def authenticated_call(
        context: WorkspaceContext,
        operation: Any,
    ) -> tuple[dict[str, Any], WorkspaceContext]:
        return operation(FakeCloud(context), context), context

    runtime.__dict__["_authenticated_cloud_call"] = authenticated_call
    runtime.__dict__["_connection"] = lambda: nullcontext(object())
    runtime.__dict__["_current_ai_runtime"] = (
        lambda _connection, sandbox_id: snapshots.get(
            sandbox_id,
            {"state": "not_ready"},
        )
    )
    runtime.__dict__["_write_ai_runtime"] = (
        lambda sandbox_id, document: snapshots.__setitem__(
            sandbox_id,
            document,
        )
    )

    first = _context("a")
    second = _context("b")
    WorkspaceRuntime._sync_ai_for_context(runtime, first)
    WorkspaceRuntime._sync_ai_for_context(runtime, second)

    assert runtime.secret_store.get(runtime._ai_ref(first.sandbox_id)) == (
        "main-secret-a"
    )
    assert runtime.secret_store.get(runtime._ai_ref(second.sandbox_id)) == (
        "main-secret-b"
    )
    assert runtime.secret_store.get(
        runtime._ai_profile_ref(first.sandbox_id, "online_primary")
    ) == "profile-secret-a"
    assert runtime.secret_store.get(
        runtime._ai_profile_ref(second.sandbox_id, "online_primary")
    ) == "profile-secret-b"
    serialized = json.dumps(snapshots, ensure_ascii=False)
    for main_secret, profile_secret in secrets.values():
        assert main_secret not in serialized
        assert profile_secret not in serialized
    assert snapshots[first.sandbox_id]["routing"]["effectiveScopeKind"] == (
        "personal"
    )
    assert snapshots[first.sandbox_id]["routing"]["precedence"] == (
        "personal_over_organization"
    )

    first_candidates, _ = WorkspaceRuntime._ai_candidates(
        runtime,
        first,
        snapshots[first.sandbox_id],
        capability="deep_analysis",
    )
    second_candidates, _ = WorkspaceRuntime._ai_candidates(
        runtime,
        second,
        snapshots[second.sandbox_id],
        capability="deep_analysis",
    )
    assert first_candidates[0]["apiKey"] == "profile-secret-a"
    assert second_candidates[0]["apiKey"] == "profile-secret-b"
    assert first_candidates[0]["baseUrl"] != second_candidates[0]["baseUrl"]
