from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.config import LocalConfig
from backend.app.local_ai_governor import MachineHealth
from backend.app.local_asr import TranscriptionResult, TranscriptionSegment
from backend.app.local_asr.downloader import DownloadProgress
from backend.app.main import create_app as create_local_app
from backend.app.platform_integrations_local import LocalPlatformOperationRepository
from backend.app.runtime import LocalRuntimeError
from backend.app.ui_domains import platform_integrations
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app as create_cloud_app
from cloud_backend.app.repositories import (
    platform_integrations as cloud_platform_integrations,
)
from strict_common.schema import runtime_connection
from tests.strict_cloud_test_factory import (
    provision_test_organization,
    strict_cloud_test_client,
)


@pytest.fixture(autouse=True)
def _stub_feishu_oauth_relay_registration(monkeypatch):
    monkeypatch.setattr(
        cloud_platform_integrations.PlatformIntegrationsRepository,
        "_register_feishu_oauth_relay_session",
        lambda self, **kwargs: None,
    )


def _cloud(tmp_path: Path) -> tuple[TestClient, Path]:
    client, database, _ = strict_cloud_test_client(
        tmp_path,
        bootstrap_token="platform-bootstrap",
        cloud_instance_id="cloud-platform-integrations-test",
    )
    return client, database


def _bootstrap(client: TestClient) -> dict[str, Any]:
    return provision_test_organization(
        client,
        organization_name="平台能力测试组织",
        display_name="平台管理员",
        email="platform-admin@example.com",
        password="12345678",
    )


def _member(
    client: TestClient,
    admin: dict[str, Any],
    *,
    email: str,
) -> dict[str, Any]:
    department = client.post(
        "/api/v2/organization/departments",
        headers={
            "Authorization": f"Bearer {admin['accessToken']}",
            "Idempotency-Key": f"department-{email}",
        },
        json={"name": "平台成员部", "expectedOrganizationVersion": 1},
    )
    assert department.status_code == 201, department.text
    invite = client.post(
        "/api/v2/organization/invites",
        headers={"Authorization": f"Bearer {admin['accessToken']}"},
        json={
            "inviteKind": "department",
            "targetId": department.json()["id"],
        },
    )
    assert invite.status_code == 201, invite.text
    joined = client.post(
        "/api/v2/auth/join",
        json={
            "inviteCode": invite.json()["inviteCode"],
            "displayName": "平台普通成员",
            "email": email,
            "password": "member-password",
        },
    )
    assert joined.status_code == 201, joined.text
    return joined.json()


def _local(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    data_dir = tmp_path / "local"
    config = LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="platform-local-token",
        secret_namespace="test.strict.platform",
        test_mode=True,
    )
    app = create_local_app(config)
    app.state.runtime.pinned_workspace_context = (  # type: ignore[method-assign]
        lambda: nullcontext()
    )
    return (
        TestClient(app),
        {"X-Yiyu-Desktop-Token": config.desktop_token},
    )


def _platform_query(
    client: TestClient,
    auth: dict[str, str],
    resource_path: str,
    authorization_scope: str = "organization",
    **query: str,
):
    return client.get(
        "/api/v2/platform-integrations/query",
        headers=auth,
        params={
            "resourcePath": resource_path,
            "authorizationScope": authorization_scope,
            **query,
        },
    )


def _platform_command(
    client: TestClient,
    auth: dict[str, str],
    resource_path: str,
    payload: dict[str, Any],
    idempotency_key: str,
    authorization_scope: str = "organization",
    method: str = "POST",
):
    return client.post(
        "/api/v2/platform-integrations/command",
        headers={**auth, "Idempotency-Key": idempotency_key},
        json={
            "resourcePath": resource_path,
            "authorizationScope": authorization_scope,
            "method": method,
            "query": {},
            "payload": payload,
        },
    )


class _FeishuResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200):
        self.payload = payload
        self.status_code = status_code
        self.request = None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", cloud_platform_integrations.FEISHU_TENANT_TOKEN_URL)
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                "provider rejected request",
                request=request,
                response=response,
            )

    def json(self) -> dict[str, Any]:
        return self.payload


class _FeishuClient:
    def __init__(
        self,
        calls: list[dict[str, Any]],
        payload: dict[str, Any],
        **_: Any,
    ):
        self.calls = calls
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        **_: Any,
    ) -> _FeishuResponse:
        self.calls.append({"url": url, "json": json})
        return _FeishuResponse(self.payload)


def _install_feishu_provider(
    monkeypatch,
    *,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cloud_platform_integrations,
        "FEISHU_HTTP_CLIENT_FACTORY",
        lambda **kwargs: _FeishuClient(calls, payload, **kwargs),
    )
    return calls


class _FeishuCalendarClient:
    def __init__(
        self,
        calls: list[dict[str, Any]],
        calendar_outcomes: list[Any],
        **_: Any,
    ):
        self.calls = calls
        self.calendar_outcomes = calendar_outcomes

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def _calendar_response(
        self,
        *,
        method: str,
        url: str,
        json: dict[str, Any],
        params: dict[str, Any] | None,
        headers: dict[str, Any] | None,
    ) -> _FeishuResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": json,
                "params": dict(params or {}),
                "authorized": bool((headers or {}).get("Authorization")),
            }
        )
        if not self.calendar_outcomes:
            raise AssertionError("unexpected Feishu calendar request")
        outcome = self.calendar_outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _FeishuResponse(outcome)

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        if url == cloud_platform_integrations.FEISHU_TENANT_TOKEN_URL:
            self.calls.append({"method": "POST", "url": url, "tokenRequest": True})
            return _FeishuResponse(
                {
                    "code": 0,
                    "tenant_access_token": "temporary-calendar-token",
                    "expire": 7200,
                }
            )
        if url == (
            f"{cloud_platform_integrations.FEISHU_CALENDAR_API_ROOT}/primary"
        ):
            self.calls.append(
                {
                    "method": "POST",
                    "url": url,
                    "primaryCalendarRequest": True,
                    "authorized": bool((headers or {}).get("Authorization")),
                }
            )
            return _FeishuResponse(
                {
                    "code": 0,
                    "data": {
                        "calendars": [
                            {
                                "calendar": {
                                    "calendar_id": (
                                        "feishu.test@group.calendar.feishu.cn"
                                    ),
                                    "type": "primary",
                                    "role": "owner",
                                }
                            }
                        ]
                    },
                }
            )
        if json is None:
            raise AssertionError("calendar event request must have JSON")
        return self._calendar_response(
            method="POST",
            url=url,
            json=json,
            params=params,
            headers=headers,
        )

    def patch(
        self,
        url: str,
        *,
        json: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        return self._calendar_response(
            method="PATCH",
            url=url,
            json=json,
            params=None,
            headers=headers,
        )


def _install_feishu_calendar_provider(
    monkeypatch,
    *,
    calendar_outcomes: list[Any],
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    outcomes = list(calendar_outcomes)
    monkeypatch.setattr(
        cloud_platform_integrations,
        "FEISHU_HTTP_CLIENT_FACTORY",
        lambda **kwargs: _FeishuCalendarClient(calls, outcomes, **kwargs),
    )
    return calls


def test_platform_router_covers_the_complete_54_operation_inventory() -> None:
    routes = platform_integrations.router.routes
    inventoried_routes = [
        route for route in routes if route.pattern != "logs/export"
    ]
    assert len(inventoried_routes) == 54
    covered = {(route.method, route.pattern) for route in routes}
    assert ("GET", "system/source-integrity") in covered
    assert ("GET", "audio-transcription-jobs/recent") in covered
    assert ("GET", "system/active-background-tasks") in covered
    assert ("GET", "local-ai/health") in covered
    assert ("GET", "ollama/health") in covered
    assert ("GET", "org-integrations/feishu") in covered
    assert ("GET", "logs/export") in covered


def test_cloud_exposes_canonical_platform_query_and_command_only(
    tmp_path: Path,
) -> None:
    client, _database = _cloud(tmp_path)
    route_paths = {
        route.path
        for route in client.app.routes
        if "platform" in route.path
        or route.path.startswith("/api/v2/ui/feishu")
        or route.path.startswith("/api/v2/ui/org-integrations/feishu")
        or route.path.startswith("/api/v2/ui/support-requests")
        or route.path.startswith("/api/v2/ui/software-feedback")
    }
    assert "/api/v2/platform-integrations/query" in route_paths
    assert "/api/v2/platform-integrations/command" in route_paths
    assert all(not path.startswith("/api/v2/ui/") for path in route_paths)


def test_unconnected_high_frequency_statuses_are_explicit_and_stop_polling(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        platform_integrations,
        "_ollama_health",
        lambda: {
            "running": False,
            "baseUrl": "http://127.0.0.1:11434",
            "installedModels": [],
            "error": "本机 Ollama 未运行",
            "version": None,
            "state": "not_connected",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    client, headers = _local(tmp_path)
    with client:
        audio = client.get(
            "/api/v2/ui/audio-transcription-jobs/recent",
            headers=headers,
        )
        background = client.get(
            "/api/v2/ui/system/active-background-tasks",
            headers=headers,
        )
        local_ai = client.get("/api/v2/ui/local-ai/health", headers=headers)
        tools = client.get("/api/v2/ui/tool-registry", headers=headers)
        ollama = client.get("/api/v2/ui/ollama/health", headers=headers)
        asr = client.get("/api/v2/ui/local-asr/model/status", headers=headers)

    for response in (audio, background, ollama, asr):
        assert response.status_code == 200, response.text
        assert response.json()["pollingEnabled"] is False
        assert response.json()["state"] in {
            "not_connected",
            "blocked",
        }
    assert local_ai.status_code == 200, local_ai.text
    assert local_ai.json()["state"] == "ready"
    assert local_ai.json()["verdict"] == "skip"
    assert local_ai.json()["pollingEnabled"] is False
    local_ai_tool = next(
        item
        for item in tools.json()["tools"]
        if item["tool_name"] == "local_ai"
    )
    assert local_ai_tool["status"] == "available"
    assert local_ai_tool["external_side_effect"] == "local_device_only"
    assert tools.json()["schema_completeness"]["localAiQueueAuthority"] is True
    assert audio.json()["jobs"] == []
    assert "state" in audio.json()
    assert background.json()["tasks"] == []
    assert "state" in background.json()


def test_private_ai_platform_actions_execute_and_keep_private_input_out_of_db(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, headers = _local(tmp_path)
    database = tmp_path / "local" / "strict-local.db"
    calls: list[dict[str, str]] = []

    def complete(*, system_prompt: str, prompt: str, creativity_mode: str):
        calls.append(
            {
                "systemPrompt": system_prompt,
                "prompt": prompt,
                "creativityMode": creativity_mode,
            }
        )
        if "会议纪要助手" in system_prompt:
            content = '{"title":"项目周会","minutesMd":"## 行动项\\n- 跟进资料"}'
        elif "任务规划助手" in system_prompt:
            content = (
                '```json\n{"steps":[{"action":"核对资料","basis":"用户要求",'
                '"deliverable":"核对清单"}]}\n```'
            )
        elif "任务标签助手" in system_prompt:
            content = '{"suggestedTags":["项目资料","跟进"]}'
        else:
            content = "OK"
        return {
            "content": content,
            "modelName": "strict-private-test",
            "sourceScope": "member_local_private_request",
            "persistedToOrganizationCloud": False,
        }

    private_marker = "private-input-never-persist-7842"
    monkeypatch.setattr(client.app.state.runtime, "private_ai_completion", complete)
    with client:
        health = client.post(
            "/api/v2/ui/runtime/llm-healthcheck",
            headers={**headers, "Idempotency-Key": "private-health-1"},
            json={"prompt": private_marker},
        )
        minutes = client.post(
            "/api/v2/ui/recordings/summarize-meeting-minutes",
            headers={**headers, "Idempotency-Key": "private-minutes-1"},
            json={"transcript": f"会议转写 {private_marker}"},
        )
        steps = client.post(
            "/api/v2/ui/ai-command/parse-steps",
            headers={**headers, "Idempotency-Key": "private-steps-1"},
            json={"text": f"先核对资料 {private_marker}"},
        )
        tags = client.post(
            "/api/v2/ui/local/tasks/tag-suggestions",
            headers={**headers, "Idempotency-Key": "private-tags-1"},
            json={
                "title": f"资料跟进 {private_marker}",
                "desc": "形成核对清单",
                "collaboratorNames": ["成员甲"],
                "module": "tasks",
            },
        )

    assert health.status_code == 200, health.text
    assert health.json()["success"] is True
    assert health.json()["state"] == "completed"
    assert health.json()["probeExecuted"] is True
    assert minutes.status_code == 200, minutes.text
    assert minutes.json()["title"] == "项目周会"
    assert minutes.json()["state"] == "completed"
    assert steps.status_code == 200, steps.text
    assert steps.json()["steps"][0]["action"] == "核对资料"
    assert steps.json()["state"] == "completed"
    assert tags.status_code == 200, tags.text
    assert tags.json()["suggestedTags"] == ["项目资料", "跟进"]
    assert tags.json()["state"] == "completed"
    assert len(calls) == 4
    assert all(call["creativityMode"] == "strict" for call in calls)
    assert private_marker.encode() not in database.read_bytes()
    with runtime_connection(database, "local") as connection:
        rows = connection.execute(
            """
            SELECT command_type, sandbox_id, payload_json
            FROM command_envelopes
            WHERE command_type IN (
              'runtime.llm_healthcheck',
              'recordings.summarize_meeting_minutes',
              'ai_command.parse_steps',
              'tasks.suggest_tags'
            )
            """
        ).fetchall()
        assert {str(row["command_type"]) for row in rows} == {
            "runtime.llm_healthcheck",
            "recordings.summarize_meeting_minutes",
            "ai_command.parse_steps",
            "tasks.suggest_tags",
        }
        assert len({str(row["sandbox_id"]) for row in rows}) == 1
    assert all(private_marker not in str(row["payload_json"]) for row in rows)


def test_meeting_minutes_accept_plain_markdown_model_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, headers = _local(tmp_path)

    def complete(*, system_prompt: str, prompt: str, creativity_mode: str):
        assert "会议纪要助手" in system_prompt
        assert "既有任务详情" in prompt
        assert creativity_mode == "strict"
        return {
            "content": "## 讨论结论\n- 保留既有任务详情。\n\n## 行动项\n- 跟进资料。",
            "modelName": "plain-markdown-test",
            "sourceScope": "member_local_private_request",
            "persistedToOrganizationCloud": False,
        }

    monkeypatch.setattr(client.app.state.runtime, "private_ai_completion", complete)
    with client:
        response = client.post(
            "/api/v2/ui/recordings/summarize-meeting-minutes",
            headers={**headers, "Idempotency-Key": "plain-minutes-1"},
            json={
                "transcript": "这是一段录音转写，讨论了既有任务详情和后续资料。",
                "taskTitleHint": "既有任务详情",
            },
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True
    assert payload["state"] == "completed"
    assert payload["title"] == "讨论结论"
    assert payload["minutesMd"].startswith("## 讨论结论")
    assert "跟进资料" in payload["minutesMd"]


def test_private_ai_platform_failures_are_classified_and_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, headers = _local(tmp_path)
    calls = 0

    def configuration_missing(**_: Any):
        nonlocal calls
        calls += 1
        raise LocalRuntimeError(
            409,
            "organization_ai_not_ready",
            "组织模型尚未配置",
        )

    monkeypatch.setattr(
        client.app.state.runtime,
        "private_ai_completion",
        configuration_missing,
    )
    with client:
        first = client.post(
            "/api/v2/ui/runtime/llm-healthcheck",
            headers={**headers, "Idempotency-Key": "private-blocked-1"},
            json={"prompt": "ping"},
        )
        replay = client.post(
            "/api/v2/ui/runtime/llm-healthcheck",
            headers={**headers, "Idempotency-Key": "private-blocked-1"},
            json={"prompt": "ping"},
        )
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "blocked"
    assert first.json()["probeExecuted"] is False
    assert replay.status_code == 200, replay.text
    assert replay.json()["state"] == "blocked"
    assert replay.json()["idempotentReplay"] is True
    assert calls == 1

    def unavailable(**_: Any):
        raise LocalRuntimeError(503, "ai_unreachable", "暂时无法连接大模型服务")

    monkeypatch.setattr(client.app.state.runtime, "private_ai_completion", unavailable)
    with client:
        failed = client.post(
            "/api/v2/ui/ai-command/parse-steps",
            headers={**headers, "Idempotency-Key": "private-failed-1"},
            json={"text": "形成一份项目资料核对清单"},
        )
    assert failed.status_code == 200, failed.text
    assert failed.json()["state"] == "failed_retryable"
    assert failed.json()["retryable"] is True
    assert failed.json()["steps"] == []


def test_bot_summaries_are_derived_from_current_cloud_identity_visibility() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def cloud_query(self, path: str, *, query=None):
            self.paths.append(path)
            if path == "/api/v2/authorization/current":
                return {
                    "membershipId": "member-1",
                    "systemRole": "member",
                }
            if path.endswith("/bots"):
                return {
                    "items": [
                        {
                            "id": "bot-1",
                            "display_name": "资料机器人",
                        }
                    ]
                }
            if path.endswith("/bots/bot-1"):
                return {
                    "id": "bot-1",
                    "actor_id": "principal-bot-1",
                    "display_name": "资料机器人",
                    "department_id": "department-1",
                    "department_name": "项目部",
                }
            if path.endswith("/bots/bot-1/task-plans"):
                return {
                    "items": [
                        {
                            "id": "plan-1",
                            "plan_title": "核对资料",
                            "human_initiator_id": "member-1",
                            "client_id": "project-1",
                            "status": "approved",
                            "execution_state": "completed",
                            "created_at": "2026-07-28T10:00:00",
                        }
                    ]
                }
            if path.endswith("/bots/task-plans/plan-1/progress"):
                return {
                    "execution_status": "success",
                    "subtasks": [
                        {
                            "module": "documents.verify",
                            "status": "success",
                            "durationMs": 120,
                        }
                    ],
                }
            raise AssertionError(path)

        def business_snapshot(self, *, refresh: bool):
            assert refresh is False
            return {
                "tasks": [
                    {
                        "createdByMembershipId": "member-1",
                        "createdAt": "2026-07-29T10:00:00",
                    }
                ]
            }

    runtime = Runtime()
    compatibility = type("Compatibility", (), {"runtime": runtime})()
    request = UiRequest(
        method="GET",
        path="",
        query={"week": "2026-W31"},
        body={},
        idempotency_key="",
    )
    bot_match = re.fullmatch(r"(?P<bot_id>[^/]+)", "bot-1")
    user_match = re.fullmatch(r"(?P<user_id>[^/]+)", "member-1")
    assert bot_match is not None
    assert user_match is not None

    bot = platform_integrations.bot_weekly_summary(
        compatibility,
        request,
        bot_match,
    )
    user = platform_integrations.user_ai_delegations(
        compatibility,
        request,
        user_match,
    )

    assert bot["state"] == "ready"
    assert bot["plans_received"][0]["plan_id"] == "plan-1"
    assert bot["actions_summary"] == {"documents.verify": 1}
    assert bot["success_rate"] == 1
    assert user["state"] == "ready"
    assert user["summary"] == {
        "total_plans": 1,
        "approved": 1,
        "executing": 0,
        "completed": 1,
        "failed": 0,
    }
    assert user["user_manual_tasks"] == 1
    assert user["ai_collaboration_score"] == 0.5
    assert all(
        path == "/api/v2/authorization/current"
        or path.startswith("/api/v2/organization-access/")
        for path in runtime.paths
    )

    other_match = re.fullmatch(r"(?P<user_id>[^/]+)", "member-2")
    assert other_match is not None
    blocked = platform_integrations.user_ai_delegations(
        compatibility,
        request,
        other_match,
    )
    assert blocked["state"] == "blocked"
    assert blocked["errorCode"] == "ai_delegation_read_forbidden"
    assert blocked["plans"] == []


def test_local_docx_adapter_reads_authoritative_local_body_and_uses_personal_scope(
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []

    class Runtime:
        def cloud_command(
            self,
            method: str,
            path: str,
            *,
            payload: dict[str, Any],
            idempotency_key: str,
            refresh_business: bool,
        ) -> dict[str, Any]:
            calls.append(
                {
                    "method": method,
                    "path": path,
                    "payload": payload,
                    "idempotencyKey": idempotency_key,
                    "refreshBusiness": refresh_business,
                }
            )
            return {
                "result": {
                    "localType": "document",
                    "localId": "document-local-authority",
                    "remoteType": "docx_document",
                    "status": "synced",
                    "message": "已同步到飞书文档",
                    "updatedAt": "2026-07-31T10:00:00.000Z",
                    "details": {},
                }
            }

    class Materials:
        def __init__(self, runtime: Any):
            assert isinstance(runtime, Runtime)

        def document_text(self, document_id: str) -> dict[str, Any]:
            assert document_id == "document-local-authority"
            return {
                "title": "本机权威标题",
                "content": "本机权威正文",
            }

    monkeypatch.setattr(
        platform_integrations,
        "LocalProjectMaterialsRepository",
        Materials,
    )
    compatibility = type(
        "Compatibility",
        (),
        {"runtime": Runtime()},
    )()
    request = UiRequest(
        method="POST",
        path="feishu-sync/documents",
        query={},
        body={
            "localId": "document-local-authority",
            "title": "renderer 伪造标题",
            "content": "renderer 伪造正文",
            "clientId": "project-local-authority",
        },
        idempotency_key="local-docx-authority-1",
    )
    result = platform_integrations.feishu_sync_document(
        compatibility,
        request,
        None,
    )

    assert result["status"] == "synced"
    assert len(calls) == 1
    forwarded = calls[0]["payload"]
    assert forwarded["authorizationScope"] == "personal"
    assert forwarded["payload"]["title"] == "本机权威标题"
    assert forwarded["payload"]["content"] == "本机权威正文"
    assert "renderer 伪造" not in str(forwarded)


def test_feishu_secret_is_encrypted_and_successful_validation_is_idempotent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_feishu_provider(
        monkeypatch,
        payload={
            "code": 0,
            "tenant_access_token": "temporary-tenant-token",
            "expire": 7200,
        },
    )
    client, database = _cloud(tmp_path)
    secret = "feishu-secret-must-not-persist"
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        saved = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_platform_test",
                "appSecret": secret,
                "callbackMode": "cloud_relay",
            },
            "feishu-config-1",
        )
        repeated = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_platform_test",
                "appSecret": secret,
                "callbackMode": "cloud_relay",
            },
            "feishu-config-1",
        )

    assert saved.status_code == 200, saved.text
    assert repeated.status_code == 200, repeated.text
    payload = saved.json()["result"]
    assert payload["state"] == "ready"
    assert payload["enabled"] is True
    assert payload["hasAppSecret"] is True
    assert payload["authorizationBlockedReason"] is None
    assert payload["lastValidationStatus"] == "succeeded"
    assert repeated.json()["result"]["state"] == "ready"
    assert len(calls) == 1
    assert calls[0]["url"] == cloud_platform_integrations.FEISHU_TENANT_TOKEN_URL
    assert calls[0]["json"] == {
        "app_id": "cli_platform_test",
        "app_secret": secret,
    }
    assert secret not in saved.text
    assert "temporary-tenant-token" not in saved.text
    persisted = b"".join(
        path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file()
    )
    assert secret.encode() not in persisted
    assert b"temporary-tenant-token" not in persisted
    with runtime_connection(database, "cloud") as connection:
        configuration = connection.execute(
            """
            SELECT scope_kind, configuration_kind, public_config_json,
                   encrypted_secret_bundle
            FROM scoped_configuration_records
            WHERE configuration_kind = ?
            """,
            (cloud_platform_integrations.FEISHU_CONFIGURATION_KIND,),
        ).fetchone()
        assert configuration is not None
        assert configuration["scope_kind"] == "organization"
        assert secret not in str(configuration["public_config_json"])
        assert configuration["encrypted_secret_bundle"] is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM external_provider_resources"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM external_side_effects"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM operation_attempts"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM operation_dead_letters"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM reconciliation_runs"
        ).fetchone()[0] == 0


def test_feishu_effective_configuration_supports_personal_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_feishu_provider(
        monkeypatch,
        payload={
            "code": 0,
            "tenant_access_token": "temporary-token",
            "expire": 7200,
        },
    )
    client, database = _cloud(tmp_path)
    organization_secret = "organization-feishu-secret"
    personal_secret = "personal-feishu-secret"
    with client:
        admin = _bootstrap(client)
        member = _member(
            client,
            admin,
            email="platform-member@example.com",
        )
        admin_auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        member_auth = {"Authorization": f"Bearer {member['accessToken']}"}
        organization_saved = _platform_command(
            client,
            admin_auth,
            "org-integrations/feishu/validate-and-save",
            {
                "scopeKind": "organization",
                "appId": "cli_organization",
                "appSecret": organization_secret,
            },
            "feishu-organization-default",
        )
        inherited = _platform_query(
            client,
            member_auth,
            "org-integrations/feishu",
        )
        personal_saved = _platform_command(
            client,
            member_auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_personal",
                "appSecret": personal_secret,
            },
            "feishu-personal-override",
        )
        personal_effective = _platform_query(
            client,
            member_auth,
            "org-integrations/feishu",
        )
        organization_effective = _platform_query(
            client,
            admin_auth,
            "org-integrations/feishu",
        )
        forbidden = _platform_command(
            client,
            member_auth,
            "org-integrations/feishu/validate-and-save",
            {
                "scopeKind": "organization",
                "appId": "cli_forbidden",
                "appSecret": "forbidden-secret",
                "expectedVersion": 1,
            },
            "feishu-member-organization-forbidden",
        )

    assert organization_saved.status_code == 200, organization_saved.text
    assert inherited.status_code == 200, inherited.text
    assert inherited.json()["resource"]["appId"] == "cli_organization"
    assert inherited.json()["resource"]["effectiveScopeKind"] == "organization"
    assert inherited.json()["resource"]["defaultWriteScope"] == "personal"
    assert personal_saved.status_code == 200, personal_saved.text
    assert personal_saved.json()["result"]["state"] == "ready"
    assert personal_saved.json()["result"]["effectiveScopeKind"] == "personal"
    assert personal_effective.json()["resource"]["appId"] == "cli_personal"
    assert personal_effective.json()["resource"]["effectiveScopeKind"] == "personal"
    assert organization_effective.json()["resource"]["appId"] == "cli_organization"
    assert organization_effective.json()["resource"]["effectiveScopeKind"] == (
        "organization"
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "admin_required"
    assert len(calls) == 2
    persisted = database.read_bytes()
    assert organization_secret.encode() not in persisted
    assert personal_secret.encode() not in persisted
    with runtime_connection(database, "cloud", read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT scope_kind, principal_id, membership_id,
                   encrypted_secret_bundle
            FROM scoped_configuration_records
            WHERE configuration_kind = ?
            ORDER BY scope_kind
            """,
            (cloud_platform_integrations.FEISHU_CONFIGURATION_KIND,),
        ).fetchall()
        validation_scopes = {
            str(row["scope_kind"])
            for row in connection.execute(
                """
                SELECT DISTINCT s.scope_kind
                FROM operation_attempts AS a
                JOIN authorization_scopes AS s ON s.scope_id = a.scope_id
                JOIN command_envelopes AS c ON c.command_id = a.command_id
                WHERE c.command_type = 'feishu.validate_and_save'
                """
            ).fetchall()
        }
    assert [row["scope_kind"] for row in rows] == ["organization", "personal"]
    personal_row = next(row for row in rows if row["scope_kind"] == "personal")
    assert personal_row["principal_id"] == member["principalId"]
    assert personal_row["membership_id"] == member["membershipId"]
    assert all(row["encrypted_secret_bundle"] for row in rows)
    assert validation_scopes == {"organization", "personal"}


def test_feishu_provider_rejection_is_failed_retryable_without_secret_leak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_feishu_provider(
        monkeypatch,
        payload={"code": 10003, "msg": "invalid app secret"},
    )
    client, database = _cloud(tmp_path)
    secret = "rejected-feishu-secret"
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        saved = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_rejected",
                "appSecret": secret,
                "callbackMode": "cloud_relay",
            },
            "feishu-rejected-1",
        )
        queried = _platform_query(
            client,
            auth,
            "org-integrations/feishu",
        )

    assert saved.status_code == 200, saved.text
    assert queried.status_code == 200, queried.text
    result = saved.json()["result"]
    assert result["state"] == "failed_retryable"
    assert result["enabled"] is False
    assert result["hasAppSecret"] is True
    assert result["authorizationBlockedReason"] == "feishu_tenant_token_rejected"
    assert queried.json()["resource"]["state"] == "failed_retryable"
    assert len(calls) == 1
    assert secret not in saved.text
    assert "invalid app secret" not in saved.text
    persisted = b"".join(
        path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file()
    )
    assert secret.encode() not in persisted
    assert b"invalid app secret" not in persisted
    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM operation_dead_letters
            WHERE error_code = 'feishu_tenant_token_rejected'
            """
        ).fetchone()[0] == 1


def test_personal_feishu_scope_is_separate_and_profiles_are_audited(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_feishu_provider(
        monkeypatch,
        payload={
            "code": 0,
            "tenant_access_token": "personal-scope-test-token",
        },
    )
    monkeypatch.setattr(
        cloud_platform_integrations.PlatformIntegrationsRepository,
        "_register_feishu_oauth_relay_session",
        lambda self, **kwargs: None,
    )
    client, database = _cloud(tmp_path)
    presented_mobile = "+86-188-0000-1234"
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        configured = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_personal_scope",
                "appSecret": "personal-scope-org-secret",
            },
            "personal-scope-org-configuration",
        )
        organization = _platform_query(
            client,
            auth,
            "org-integrations/feishu",
        )
        personal = _platform_query(
            client,
            auth,
            "me/feishu-authorization",
            "personal",
        )
        mismatch = _platform_query(
            client,
            auth,
            "org-integrations/feishu",
            "personal",
        )
        started = _platform_command(
            client,
            auth,
            "me/feishu-authorization/start",
            {},
            "personal-feishu-auth-1",
            "personal",
        )
        import_status = _platform_query(
            client,
            auth,
            "feishu-doc-import/status",
        )
        delivery = _platform_command(
            client,
            auth,
            "me/feishu-delivery-profile",
            {"mobile": presented_mobile},
            "personal-feishu-delivery-1",
            "personal",
        )

    assert configured.status_code == 200, configured.text
    assert configured.json()["result"]["state"] == "ready"
    assert organization.status_code == 200
    assert organization.json()["authorizationScope"] == "organization"
    assert organization.json()["resource"]["authorizationScope"] == "organization"
    assert organization.json()["resource"]["state"] == "ready"
    assert personal.status_code == 200
    assert personal.json()["authorizationScope"] == "personal"
    assert personal.json()["resource"]["authorizationScope"] == "personal"
    assert mismatch.status_code == 409
    assert started.status_code == 200, started.text
    assert started.json()["result"]["qrReady"] is True
    assert started.json()["result"]["authorizeUrl"].startswith(
        cloud_platform_integrations.FEISHU_OAUTH_AUTHORIZE_URL
    )
    assert started.json()["result"]["authorizationScope"] == "personal"
    assert import_status.status_code == 200, import_status.text
    # One-time link import uses the configured organization application.  It
    # remains available while this member's separate notification/OAuth
    # authorization is still pending.
    assert import_status.json()["resource"]["ready"] is True
    assert import_status.json()["resource"]["linked"] is True
    assert import_status.json()["resource"]["state"] == "ready"
    assert import_status.json()["resource"]["blockerType"] is None
    assert (
        import_status.json()["resource"]["accessMode"]
        == "organization_application_one_time_copy"
    )
    assert delivery.status_code == 200, delivery.text
    assert delivery.json()["result"]["authorizationScope"] == "personal"
    assert delivery.json()["result"]["mobile"] == "+8618800001234"
    assert delivery.json()["result"]["deliveryStatus"] == "failed"
    persisted = b"".join(
        path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file()
    )
    assert presented_mobile.encode() not in persisted
    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM provider_resources r
            WHERE r.owner_kind = 'membership'
              AND r.resource_kind IN (
                'feishu_member_oauth_authorization',
                'feishu_member_delivery_profile'
              )
            """
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM provider_resources r
            WHERE r.resource_kind = ?
              AND r.owner_kind = 'organization'
            """,
            (cloud_platform_integrations.FEISHU_CONFIGURATION_KIND,),
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM provider_resources r
            WHERE r.resource_kind = ?
              AND r.owner_kind = 'membership'
            """,
            (cloud_platform_integrations.FEISHU_CONFIGURATION_KIND,),
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM provider_resources r
            WHERE r.resource_kind = ?
              AND r.owner_kind = 'membership'
            """,
            (
                cloud_platform_integrations
                .FEISHU_MEMBER_AUTHORIZATION_KIND,
            ),
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM operation_attempts a
            JOIN commands c ON c.id = a.command_id
            WHERE c.command_type IN (
                'feishu.personal_authorization.start',
                'feishu.personal_delivery_profile.verify'
            )
            """
        ).fetchone()[0] == 2


class _FeishuOAuthDocumentClient:
    def __init__(self, calls: list[dict[str, Any]], **_: Any):
        self.calls = calls
        self.refresh_count = sum(
            call.get("kind") == "refresh" for call in calls
        )

    def __enter__(self):
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        body = dict(json or {})
        if url == cloud_platform_integrations.FEISHU_TENANT_TOKEN_URL:
            self.calls.append({"kind": "tenant_validation", "url": url})
            return _FeishuResponse(
                {
                    "code": 0,
                    "tenant_access_token": "tenant-token-never-persist",
                }
            )
        if url == cloud_platform_integrations.FEISHU_OAUTH_TOKEN_URL:
            grant_type = str(body.get("grant_type") or "")
            if grant_type == "authorization_code":
                self.calls.append({"kind": "exchange", "url": url})
                return _FeishuResponse(
                    {
                        "code": 0,
                        "access_token": "oauth-access-token-0",
                        "refresh_token": "oauth-refresh-token-static",
                        "expires_in": 1,
                        "refresh_token_expires_in": 7200,
                        "scope": "offline_access docx:document:readonly",
                    }
                )
            refresh_no = (
                sum(call.get("kind") == "refresh" for call in self.calls)
                + 1
            )
            self.calls.append(
                {"kind": "refresh", "url": url, "number": refresh_no}
            )
            return _FeishuResponse(
                {
                    "code": 0,
                    "access_token": f"oauth-access-token-{refresh_no}",
                    "refresh_token": "oauth-refresh-token-static",
                    "expires_in": 1,
                    "refresh_token_expires_in": 7200,
                    "scope": "offline_access docx:document:readonly",
                }
            )
        if url == cloud_platform_integrations.FEISHU_DOCUMENT_SEARCH_URL:
            self.calls.append(
                {
                    "kind": "search",
                    "url": url,
                    "authorized": bool(
                        (headers or {}).get("Authorization")
                    ),
                }
            )
            return _FeishuResponse(
                {
                    "code": 0,
                    "data": {
                        "docs_entities": [
                            {
                                "docs_token": "docx-token-1",
                                "docs_type": "docx",
                                "title": "飞书项目资料",
                                "url": (
                                    "https://example.feishu.cn/docx/"
                                    "docx-token-1"
                                ),
                            }
                        ]
                    },
                }
            )
        if url == cloud_platform_integrations.FEISHU_MESSAGE_CREATE_URL:
            self.calls.append(
                {
                    "kind": "message",
                    "url": url,
                    "authorized": bool(
                        (headers or {}).get("Authorization")
                    ),
                    "receiveId": str(body.get("receive_id") or ""),
                }
            )
            return _FeishuResponse(
                {
                    "code": 0,
                    "data": {"message_id": "om-strict-meeting-1"},
                }
            )
        if url == cloud_platform_integrations.FEISHU_CONTACT_LOOKUP_URL:
            mobile = str((body.get("mobiles") or [""])[0])
            receive_id = {
                "+8613900001111": "ou-mobile-old",
                "+8613900002222": "ou-mobile-new",
            }.get(mobile, "ou-mobile-profile")
            self.calls.append(
                {
                    "kind": "contact_lookup",
                    "url": url,
                    "mobiles": list(body.get("mobiles") or []),
                }
            )
            return _FeishuResponse(
                {
                    "code": 0,
                    "data": {
                        "user_list": [
                            {"open_id": receive_id}
                        ]
                    },
                }
            )
        raise AssertionError(f"unexpected POST {url}")

    def get(
        self,
        url: str,
        *,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        del params
        if url == cloud_platform_integrations.FEISHU_USER_INFO_URL:
            self.calls.append({"kind": "user_info", "url": url})
            return _FeishuResponse(
                {
                    "code": 0,
                    "data": {
                        "open_id": "ou-test-member",
                        "union_id": "on-test-member",
                        "user_id": "feishu-user-1",
                        "name": "飞书成员",
                    },
                }
            )
        if url.endswith("/docx-token-1/raw_content"):
            self.calls.append(
                {
                    "kind": "raw_content",
                    "url": url,
                    "authorized": bool(
                        (headers or {}).get("Authorization")
                    ),
                }
            )
            return _FeishuResponse(
                {
                    "code": 0,
                    "data": {
                        "content": (
                            "FEISHU_RAW_BODY_LOCAL_ONLY_7391"
                        )
                    },
                }
            )
        raise AssertionError(f"unexpected GET {url}")


class _FeishuConcurrentRefreshClient(_FeishuOAuthDocumentClient):
    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        body = dict(json or {})
        if (
            url == cloud_platform_integrations.FEISHU_OAUTH_TOKEN_URL
            and body.get("grant_type") == "refresh_token"
        ):
            refresh_no = (
                sum(call.get("kind") == "refresh" for call in self.calls)
                + 1
            )
            self.calls.append(
                {"kind": "refresh", "url": url, "number": refresh_no}
            )
            return _FeishuResponse(
                {
                    "code": 0,
                    "access_token": f"concurrent-access-{refresh_no}",
                    "refresh_token": "oauth-refresh-token-static",
                    "expires_in": 7200,
                    "refresh_token_expires_in": 7200,
                    "scope": "offline_access docx:document:readonly",
                }
            )
        return super().post(
            url,
            json=body,
            headers=headers,
            params=params,
        )


class _FeishuDocxClient(_FeishuOAuthDocumentClient):
    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        body = dict(json or {})
        if url == f"{cloud_platform_integrations.FEISHU_API_ROOT}/docx/v1/documents":
            self.calls.append(
                {
                    "kind": "docx_create",
                    "title": body.get("title"),
                    "params": dict(params or {}),
                }
            )
            return _FeishuResponse(
                {
                    "code": 0,
                    "data": {
                        "document": {"document_id": "docx-created-1"}
                    },
                }
            )
        if url.endswith("/docx/v1/documents/blocks/convert"):
            self.calls.append(
                {
                    "kind": "docx_convert",
                    "content": body.get("content"),
                }
            )
            return _FeishuResponse(
                {
                    "code": 0,
                    "data": {
                        "blocks": [
                            {
                                "block_type": 2,
                                "text": {
                                    "elements": [
                                        {
                                            "text_run": {
                                                "content": "converted"
                                            }
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                }
            )
        if url.endswith("/children"):
            self.calls.append(
                {
                    "kind": "docx_append",
                    "children": list(body.get("children") or []),
                }
            )
            return _FeishuResponse({"code": 0, "data": {}})
        if "/drive/v1/permissions/" in url and url.endswith("/members"):
            self.calls.append(
                {
                    "kind": "docx_member_permission",
                    "memberId": body.get("member_id"),
                }
            )
            return _FeishuResponse({"code": 0, "data": {}})
        if url.endswith("/members/transfer_owner"):
            self.calls.append(
                {
                    "kind": "docx_transfer_owner",
                    "memberId": body.get("member_id"),
                }
            )
            return _FeishuResponse({"code": 0, "data": {}})
        return super().post(
            url,
            json=body,
            headers=headers,
            params=params,
        )

    def patch(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        del headers
        self.calls.append(
            {
                "kind": (
                    "docx_public_permission"
                    if url.endswith("/public")
                    else "docx_title_update"
                ),
                "json": dict(json or {}),
                "params": dict(params or {}),
            }
        )
        return _FeishuResponse({"code": 0, "data": {}})

    def request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        del headers
        self.calls.append(
            {
                "kind": "docx_clear",
                "method": method,
                "url": url,
                "json": dict(json or {}),
                "params": dict(params or {}),
            }
        )
        return _FeishuResponse({"code": 0, "data": {}})

    def get(
        self,
        url: str,
        *,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        if url.endswith("/docx-created-1/blocks"):
            self.calls.append({"kind": "docx_blocks"})
            return _FeishuResponse(
                {
                    "code": 0,
                    "data": {
                        "items": [
                            {
                                "block_id": "docx-created-1",
                                "children": ["block-existing-1"],
                            }
                        ]
                    },
                }
            )
        return super().get(url, headers=headers, params=params)


class _FeishuDocxFailingClient(_FeishuDocxClient):
    def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> _FeishuResponse:
        if url.endswith("/children"):
            self.calls.append({"kind": "docx_append_failed"})
            return _FeishuResponse({"code": 99991663, "data": {}})
        return super().post(
            url,
            json=json,
            headers=headers,
            params=params,
        )


def test_feishu_oauth_search_fetch_refresh_and_one_time_state_are_strict(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cloud_platform_integrations,
        "FEISHU_HTTP_CLIENT_FACTORY",
        lambda **kwargs: _FeishuOAuthDocumentClient(calls, **kwargs),
    )
    client, database = _cloud(tmp_path)
    oauth_code = "oauth-code-must-not-persist"
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        configured = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_oauth_document",
                "appSecret": "oauth-app-secret-never-plaintext",
            },
            "oauth-document-configure",
        )
        started = client.post(
            "/api/v2/organization-access/feishu/member-authorization/start",
            headers={
                **auth,
                "Idempotency-Key": "oauth-document-start",
            },
        )
        state = started.json()["state"]
        callback = client.get(
            "/api/v2/organization-access/feishu/"
            "member-authorization/callback",
            params={"state": state, "code": oauth_code},
        )
        status = client.get(
            "/api/v2/organization-access/feishu/member-authorization",
            headers=auth,
        )
        first_search = _platform_command(
            client,
            auth,
            "feishu-doc-import/search",
            {"query": "项目资料", "pageSize": 20},
            "oauth-document-search-1",
            "personal",
        )
        second_search = _platform_command(
            client,
            auth,
            "feishu-doc-import/search",
            {"query": "项目资料", "pageSize": 20},
            "oauth-document-search-2",
            "personal",
        )
        fetched = _platform_command(
            client,
            auth,
            "feishu-doc-import/fetch",
            {
                "items": [
                    {
                        "token": "docx-token-1",
                        "type": "docx",
                        "title": "飞书项目资料",
                        "url": (
                            "https://example.feishu.cn/docx/docx-token-1"
                        ),
                    }
                ]
            },
            "oauth-document-fetch-1",
            "personal",
        )
        notice_marker = "MEETING_NOTICE_EXTERNAL_ONLY_6118"
        sent = _platform_command(
            client,
            auth,
            "me/feishu-message/send",
            {
                "text": notice_marker,
                "localType": "meeting",
                "localId": "meeting-strict-1",
            },
            "oauth-message-send-1",
            "personal",
        )
        sent_replay = _platform_command(
            client,
            auth,
            "me/feishu-message/send",
            {
                "text": notice_marker,
                "localType": "meeting",
                "localId": "meeting-strict-1",
            },
            "oauth-message-send-1",
            "personal",
        )
        replayed_callback = client.get(
            "/api/v2/organization-access/feishu/"
            "member-authorization/callback",
            params={"state": state, "code": oauth_code},
        )

    assert configured.status_code == 200, configured.text
    assert started.status_code == 200, started.text
    assert started.json()["qrReady"] is True
    assert "client_id=cli_oauth_document" in started.json()["authorizeUrl"]
    assert callback.status_code == 200
    assert "授权成功" in callback.text
    assert status.status_code == 200, status.text
    assert status.json()["linked"] is True
    assert status.json()["state"] == "ready"
    assert first_search.status_code == 200, first_search.text
    assert second_search.status_code == 200, second_search.text
    assert (
        first_search.json()["result"]["items"][0]["token"]
        == "docx-token-1"
    )
    assert fetched.status_code == 200, fetched.text
    assert (
        fetched.json()["result"]["items"][0]["content"]
        == "FEISHU_RAW_BODY_LOCAL_ONLY_7391"
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["result"]["status"] == "sent"
    assert sent_replay.json()["result"] == sent.json()["result"]
    assert "授权失败" in replayed_callback.text
    assert sum(call.get("kind") == "refresh" for call in calls) == 3
    assert sum(call.get("kind") == "message" for call in calls) == 1
    persisted = database.read_bytes()
    for marker in (
        oauth_code,
        "oauth-app-secret-never-plaintext",
        "oauth-access-token-0",
        "oauth-access-token-1",
        "oauth-access-token-2",
        "oauth-access-token-3",
        "oauth-refresh-token-static",
        "FEISHU_RAW_BODY_LOCAL_ONLY_7391",
        notice_marker,
    ):
        assert marker.encode() not in persisted
    with runtime_connection(database, "cloud") as connection:
        grant = connection.execute(
            """
            SELECT g.status, g.grant_generation
            FROM authorization_grants AS g
            JOIN authorization_resources AS r
              ON r.resource_id = g.resource_id
            WHERE r.resource_kind = 'feishu_member_authorization'
            ORDER BY g.created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert grant is not None
        assert grant["status"] == "active"
        assert int(grant["grant_generation"]) == 1
        refresh_commands = connection.execute(
            """
            SELECT COUNT(*)
            FROM command_envelopes
            WHERE command_type = ?
            """,
            (
                "configuration."
                f"{cloud_platform_integrations.FEISHU_MEMBER_AUTHORIZATION_KIND}"
                ".saved",
            ),
        ).fetchone()[0]
        assert refresh_commands >= 4


def test_feishu_docx_sync_is_personal_idempotent_updates_and_keeps_body_transient(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cloud_platform_integrations,
        "FEISHU_HTTP_CLIENT_FACTORY",
        lambda **kwargs: _FeishuDocxClient(calls, **kwargs),
    )
    client, database = _cloud(tmp_path)
    first_body = "DOCX_EXTERNAL_BODY_ONLY_4177"
    second_body = "DOCX_EXTERNAL_BODY_UPDATED_4178"
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        other = _member(
            client,
            admin,
            email="docx-personal-isolation@example.com",
        )
        other_auth = {
            "Authorization": f"Bearer {other['accessToken']}"
        }
        configured = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_docx_sync",
                "appSecret": "docx-sync-secret",
            },
            "docx-sync-configure",
        )
        blocked = _platform_command(
            client,
            auth,
            "feishu-sync/documents",
            {
                "localType": "document",
                "localId": "document-personal-1",
                "title": "未授权文档",
                "content": first_body,
            },
            "docx-sync-blocked",
            "personal",
        )
        started = client.post(
            "/api/v2/organization-access/feishu/member-authorization/start",
            headers={**auth, "Idempotency-Key": "docx-sync-oauth-start"},
        )
        callback = client.get(
            "/api/v2/organization-access/feishu/"
            "member-authorization/callback",
            params={
                "state": started.json()["state"],
                "code": "docx-sync-oauth-code",
            },
        )
        first = _platform_command(
            client,
            auth,
            "feishu-sync/documents",
            {
                "localType": "document",
                "localId": "document-personal-1",
                "title": "日慈资料背景",
                "content": first_body,
                "clientId": "project-richi",
            },
            "docx-sync-first",
            "personal",
        )
        replay = _platform_command(
            client,
            auth,
            "feishu-sync/documents",
            {
                "localType": "document",
                "localId": "document-personal-1",
                "title": "日慈资料背景",
                "content": first_body,
                "clientId": "project-richi",
            },
            "docx-sync-first",
            "personal",
        )
        updated = _platform_command(
            client,
            auth,
            "feishu-sync/documents",
            {
                "localType": "document",
                "localId": "document-personal-1",
                "title": "日慈资料背景（更新）",
                "content": second_body,
                "clientId": "project-richi",
            },
            "docx-sync-update",
            "personal",
        )
        status = _platform_query(
            client,
            auth,
            "feishu-sync/status",
            "personal",
            localType="document",
            localId="document-personal-1",
            remoteType="docx_document",
        )
        other_status = _platform_query(
            client,
            other_auth,
            "feishu-sync/status",
            "personal",
            localType="document",
            localId="document-personal-1",
            remoteType="docx_document",
        )
        mapping = _platform_command(
            client,
            auth,
            "feishu-doc-import/register-mapping",
            {
                "documentId": "document-imported-1",
                "remoteId": "docx-imported-1",
                "remoteType": "docx",
                "remoteUrl": "https://feishu.cn/docx/docx-imported-1",
            },
            "docx-import-mapping",
            "personal",
        )
        imported_status = _platform_query(
            client,
            auth,
            "feishu-sync/status",
            "personal",
            localType="document",
            localId="document-imported-1",
            remoteType="docx_document",
        )

    assert configured.status_code == 200, configured.text
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["result"]["status"] == "blocked"
    assert (
        blocked.json()["result"]["details"]["blockerType"]
        == "member_authorization_required"
    )
    assert callback.status_code == 200, callback.text
    assert first.status_code == 200, first.text
    assert first.json()["result"]["status"] == "synced"
    assert first.json()["result"]["remoteId"] == "docx-created-1"
    assert replay.json()["result"] == first.json()["result"]
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"]["details"]["action"] == "update"
    assert status.json()["resource"]["remoteId"] == "docx-created-1"
    assert other_status.json()["resource"]["status"] == "idle"
    assert mapping.status_code == 200, mapping.text
    assert imported_status.json()["resource"]["status"] == "synced"
    assert (
        imported_status.json()["resource"]["remoteType"]
        == "docx_document"
    )
    assert (
        imported_status.json()["resource"]["remoteId"]
        == "docx-imported-1"
    )
    assert sum(call.get("kind") == "docx_create" for call in calls) == 1
    assert sum(call.get("kind") == "docx_clear" for call in calls) == 1
    permissions = [
        call
        for call in calls
        if call.get("kind") == "docx_member_permission"
    ]
    assert len(permissions) == 2
    assert all(call["memberId"] == "ou-test-member" for call in permissions)
    public_permissions = [
        call
        for call in calls
        if call.get("kind") == "docx_public_permission"
    ]
    assert len(public_permissions) == 2
    assert all(
        call["json"] == {
            "link_share_entity": "closed",
            "external_access_entity": "closed",
        }
        for call in public_permissions
    )
    assert "tenant_editable" not in str(public_permissions)
    persisted = database.read_bytes()
    for marker in (
        first_body,
        second_body,
        "docx-sync-oauth-code",
        "docx-sync-secret",
    ):
        assert marker.encode() not in persisted
    with runtime_connection(database, "cloud") as connection:
        personal_syncs = connection.execute(
            """
            SELECT COUNT(*)
            FROM command_envelopes AS c
            JOIN authorization_scopes AS s ON s.scope_id = c.scope_id
            WHERE c.command_type = 'feishu.sync.docx_document'
              AND s.scope_kind = 'personal'
            """
        ).fetchone()[0]
        assert personal_syncs == 3
        assert connection.execute(
            """
            SELECT COUNT(*)
            FROM external_side_effects AS e
            WHERE e.effect_kind = 'feishu.docx_document.sync'
              AND e.outcome = 'succeeded'
            """
        ).fetchone()[0] == 2


def test_feishu_docx_provider_failure_is_retryable_and_preserves_remote_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cloud_platform_integrations,
        "FEISHU_HTTP_CLIENT_FACTORY",
        lambda **kwargs: _FeishuDocxFailingClient(calls, **kwargs),
    )
    client, database = _cloud(tmp_path)
    body_marker = "DOCX_FAILED_BODY_MUST_NOT_PERSIST_9864"
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_docx_failure",
                "appSecret": "docx-failure-secret",
            },
            "docx-failure-configure",
        )
        started = client.post(
            "/api/v2/organization-access/feishu/member-authorization/start",
            headers={**auth, "Idempotency-Key": "docx-failure-oauth-start"},
        )
        callback = client.get(
            "/api/v2/organization-access/feishu/"
            "member-authorization/callback",
            params={
                "state": started.json()["state"],
                "code": "docx-failure-code",
            },
        )
        assert callback.status_code == 200, callback.text
        failed = _platform_command(
            client,
            auth,
            "feishu-sync/documents",
            {
                "localType": "document",
                "localId": "document-failure-1",
                "title": "失败可重试文档",
                "content": body_marker,
            },
            "docx-failure-sync",
            "personal",
        )
        status = _platform_query(
            client,
            auth,
            "feishu-sync/status",
            "personal",
            localType="document",
            localId="document-failure-1",
            remoteType="docx_document",
        )

    assert failed.status_code == 200, failed.text
    result = failed.json()["result"]
    assert result["status"] == "failed_retryable"
    assert result["state"] == "failed_retryable"
    assert result["retryable"] is True
    assert result["remoteId"] == "docx-created-1"
    assert status.json()["resource"] == result
    assert body_marker.encode() not in database.read_bytes()
    with runtime_connection(database, "cloud") as connection:
        effect = connection.execute(
            """
            SELECT outcome
            FROM external_side_effects
            WHERE effect_kind = 'feishu.docx_document.sync'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert effect["outcome"] == "failed_retryable"


def test_feishu_docx_crash_window_reuses_active_lease_then_reclaims_expired(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cloud_platform_integrations,
        "FEISHU_HTTP_CLIENT_FACTORY",
        lambda **kwargs: _FeishuDocxClient(calls, **kwargs),
    )
    client, database = _cloud(tmp_path)
    original_finalize = (
        cloud_platform_integrations.PlatformIntegrationsRepository
        ._finalize_feishu_docx_sync
    )
    crash_once = True

    def crash_after_external_side_effect(self, *args: Any, **kwargs: Any):
        nonlocal crash_once
        if crash_once and kwargs.get("outcome") == "succeeded":
            crash_once = False
            raise RuntimeError("simulated crash before docx finalize")
        return original_finalize(self, *args, **kwargs)

    monkeypatch.setattr(
        cloud_platform_integrations.PlatformIntegrationsRepository,
        "_finalize_feishu_docx_sync",
        crash_after_external_side_effect,
    )
    payload = {
        "localType": "document",
        "localId": "document-crash-window-1",
        "title": "崩溃窗口恢复",
        "content": "DOCX_CRASH_WINDOW_BODY_5019",
    }
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_docx_crash",
                "appSecret": "docx-crash-secret",
            },
            "docx-crash-configure",
        )
        started = client.post(
            "/api/v2/organization-access/feishu/member-authorization/start",
            headers={**auth, "Idempotency-Key": "docx-crash-oauth-start"},
        )
        callback = client.get(
            "/api/v2/organization-access/feishu/"
            "member-authorization/callback",
            params={
                "state": started.json()["state"],
                "code": "docx-crash-code",
            },
        )
        assert callback.status_code == 200, callback.text
        with pytest.raises(
            RuntimeError,
            match="simulated crash before docx finalize",
        ):
            _platform_command(
                client,
                auth,
                "feishu-sync/documents",
                payload,
                "docx-crash-sync",
                "personal",
            )
        active = _platform_command(
            client,
            auth,
            "feishu-sync/documents",
            payload,
            "docx-crash-sync",
            "personal",
        )
        assert active.status_code == 200, active.text
        assert active.json()["result"]["status"] == "syncing"
        assert sum(
            call.get("kind") == "docx_create" for call in calls
        ) == 1
        with runtime_connection(database, "cloud") as connection:
            lease = connection.execute(
                """
                SELECT a.lease_owner, a.lease_until, a.transport_state
                FROM operation_attempts AS a
                JOIN command_envelopes AS c
                  ON c.command_id = a.command_id
                 AND c.scope_id = a.scope_id
                WHERE c.command_type = 'feishu.sync.docx_document'
                  AND c.idempotency_key = 'docx-crash-sync'
                ORDER BY a.attempt_no DESC
                LIMIT 1
                """
            ).fetchone()
            assert lease["lease_owner"]
            assert lease["lease_until"]
            assert lease["transport_state"] == "processing"
            connection.execute(
                """
                UPDATE operation_attempts
                SET lease_until = '2000-01-01T00:00:00.000Z'
                WHERE command_id IN (
                    SELECT command_id
                    FROM command_envelopes
                    WHERE command_type = 'feishu.sync.docx_document'
                      AND idempotency_key = 'docx-crash-sync'
                )
                """
            )
            connection.commit()
        monkeypatch.setattr(
            cloud_platform_integrations.PlatformIntegrationsRepository,
            "_finalize_feishu_docx_sync",
            original_finalize,
        )
        recovered = _platform_command(
            client,
            auth,
            "feishu-sync/documents",
            payload,
            "docx-crash-sync",
            "personal",
        )

    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["result"]["status"] == "synced"
    assert recovered.json()["result"]["remoteId"] == "docx-created-1"
    creates = [
        call for call in calls if call.get("kind") == "docx_create"
    ]
    assert len(creates) == 2
    assert creates[0]["params"]["client_token"]
    assert (
        creates[0]["params"]["client_token"]
        == creates[1]["params"]["client_token"]
    )
    with runtime_connection(database, "cloud") as connection:
        attempts = connection.execute(
            """
            SELECT attempt_no, transport_state, lease_owner, lease_until
            FROM operation_attempts AS a
            JOIN command_envelopes AS c
              ON c.command_id = a.command_id AND c.scope_id = a.scope_id
            WHERE c.command_type = 'feishu.sync.docx_document'
              AND c.idempotency_key = 'docx-crash-sync'
            ORDER BY attempt_no
            """
        ).fetchall()
        assert [row["attempt_no"] for row in attempts] == [1, 2]
        assert all(row["transport_state"] == "succeeded" for row in attempts)
        assert all(row["lease_owner"] is None for row in attempts)
        assert all(row["lease_until"] is None for row in attempts)
        processing = connection.execute(
            """
            SELECT state, started_at, finished_at
            FROM processing_attempts
            WHERE processing_kind = 'feishu.sync.docx_document'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
        assert processing["state"] == "completed"
        assert processing["started_at"]
        assert processing["finished_at"]
    assert b"DOCX_CRASH_WINDOW_BODY_5019" not in database.read_bytes()


def test_retired_feishu_keyword_search_does_not_refresh_personal_tokens(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cloud_platform_integrations,
        "FEISHU_HTTP_CLIENT_FACTORY",
        lambda **kwargs: _FeishuConcurrentRefreshClient(
            calls,
            **kwargs,
        ),
    )
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_refresh_cas",
                "appSecret": "refresh-cas-secret",
            },
            "refresh-cas-configure",
        )
        started = client.post(
            "/api/v2/organization-access/feishu/member-authorization/start",
            headers={**auth, "Idempotency-Key": "refresh-cas-start"},
        )
        callback = client.get(
            "/api/v2/organization-access/feishu/"
            "member-authorization/callback",
            params={
                "state": started.json()["state"],
                "code": "refresh-cas-code",
            },
        )
        assert callback.status_code == 200

        def search(index: int):
            return _platform_command(
                client,
                auth,
                "feishu-doc-import/search",
                {"query": "并发刷新", "pageSize": 20},
                f"refresh-cas-search-{index}",
                "personal",
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(search, (1, 2)))

    assert all(response.status_code == 410 for response in results)
    assert all(
        response.json()["error"]["code"] == "feishu_import_action_invalid"
        for response in results
    )
    assert sum(call.get("kind") == "refresh" for call in calls) == 0
    persisted = database.read_bytes()
    assert b"concurrent-access-1" not in persisted
    assert b"refresh-cas-code" not in persisted


def test_feishu_custom_delivery_mobile_is_encrypted_restart_safe_and_personal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cloud_platform_integrations,
        "FEISHU_HTTP_CLIENT_FACTORY",
        lambda **kwargs: _FeishuOAuthDocumentClient(calls, **kwargs),
    )
    database = tmp_path / "strict-cloud.db"
    master_key = Fernet.generate_key().decode()
    config = CloudConfig(
        data_dir=tmp_path,
        database_path=database,
        bootstrap_token="platform-bootstrap",
        master_key=master_key,
        cloud_instance_id=None,
    )
    client = TestClient(create_cloud_app(config))
    custom_mobile = "+86 177-0000-9911"
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        member = _member(
            client,
            admin,
            email="delivery-isolation@example.com",
        )
        configured = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_delivery_profile",
                "appSecret": "delivery-profile-secret",
            },
            "delivery-profile-configure",
        )
        saved = client.post(
            "/api/v2/organization-access/feishu/delivery-profile",
            headers={
                **auth,
                "Idempotency-Key": "delivery-profile-save",
            },
            json={"mobile": custom_mobile},
        )
        other_member = client.get(
            "/api/v2/organization-access/feishu/delivery-profile",
            headers={
                "Authorization": f"Bearer {member['accessToken']}"
            },
        )
    assert configured.status_code == 200, configured.text
    assert saved.status_code == 200, saved.text
    assert saved.json()["mobile"] == "+8617700009911"
    assert saved.json()["deliveryStatus"] == "matched"
    assert saved.json()["receiveId"] == "ou-mobile-profile"
    lookup = next(
        call for call in calls if call.get("kind") == "contact_lookup"
    )
    assert lookup["mobiles"] == ["+8617700009911"]
    assert other_member.status_code == 200, other_member.text
    assert other_member.json()["mobile"] == ""
    assert other_member.json()["receiveId"] is None
    assert custom_mobile.encode() not in database.read_bytes()
    assert b"+8617700009911" not in database.read_bytes()

    restarted = TestClient(create_cloud_app(config))
    with restarted:
        restored = restarted.get(
            "/api/v2/organization-access/feishu/delivery-profile",
            headers=auth,
        )
    assert restored.status_code == 200, restored.text
    assert restored.json()["mobile"] == "+8617700009911"
    assert restored.json()["receiveId"] == "ou-mobile-profile"

    other_dir = tmp_path / "other-organization-cloud"
    other_client, other_database = _cloud(other_dir)
    with other_client:
        other_admin = _bootstrap(other_client)
        other_profile = other_client.get(
            "/api/v2/organization-access/feishu/delivery-profile",
            headers={
                "Authorization": f"Bearer {other_admin['accessToken']}"
            },
        )
    assert other_profile.status_code == 200, other_profile.text
    assert other_profile.json()["mobile"] == ""
    assert b"+8617700009911" not in other_database.read_bytes()


def test_feishu_mobile_change_replaces_old_recipient_after_oauth_unlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        cloud_platform_integrations,
        "FEISHU_HTTP_CLIENT_FACTORY",
        lambda **kwargs: _FeishuOAuthDocumentClient(calls, **kwargs),
    )
    client, database = _cloud(tmp_path)
    old_mobile = "+86 139-0000-1111"
    new_mobile = "+86 139-0000-2222"
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "appId": "cli_delivery_change",
                "appSecret": "delivery-change-secret",
            },
            "delivery-change-configure",
        )
        started = client.post(
            "/api/v2/organization-access/feishu/member-authorization/start",
            headers={**auth, "Idempotency-Key": "delivery-change-start"},
        )
        callback = client.get(
            "/api/v2/organization-access/feishu/"
            "member-authorization/callback",
            params={
                "state": started.json()["state"],
                "code": "delivery-change-code",
            },
        )
        assert callback.status_code == 200
        old_saved = client.post(
            "/api/v2/organization-access/feishu/delivery-profile",
            headers={**auth, "Idempotency-Key": "delivery-change-old"},
            json={"mobile": old_mobile},
        )
        new_saved = client.post(
            "/api/v2/organization-access/feishu/delivery-profile",
            headers={**auth, "Idempotency-Key": "delivery-change-new"},
            json={"mobile": new_mobile},
        )
        cleared = client.delete(
            "/api/v2/organization-access/feishu/member-authorization",
            headers={**auth, "Idempotency-Key": "delivery-change-unlink"},
        )
        profile = client.get(
            "/api/v2/organization-access/feishu/delivery-profile",
            headers=auth,
        )
        sent = _platform_command(
            client,
            auth,
            "me/feishu-message/send",
            {
                "text": "新手机号接收人验证",
                "localType": "meeting",
                "localId": "meeting-after-mobile-change",
            },
            "delivery-change-send",
            "personal",
        )

    assert old_saved.status_code == 200, old_saved.text
    assert new_saved.status_code == 200, new_saved.text
    assert cleared.status_code == 200, cleared.text
    assert profile.status_code == 200, profile.text
    assert profile.json()["receiveId"] == "ou-mobile-new"
    assert sent.status_code == 200, sent.text
    assert sent.json()["result"]["status"] == "sent"
    lookups = [
        call["mobiles"]
        for call in calls
        if call.get("kind") == "contact_lookup"
    ]
    assert lookups == [["+8613900001111"], ["+8613900002222"]]
    messages = [call for call in calls if call.get("kind") == "message"]
    assert messages[-1]["receiveId"] == "ou-mobile-new"
    persisted = database.read_bytes()
    for marker in (old_mobile, new_mobile, "+8613900001111", "+8613900002222"):
        assert marker.encode() not in persisted


def test_support_and_feedback_use_durable_provider_and_outbox_objects(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        support = _platform_command(
            client,
            auth,
            "support-requests",
            {
                "targetScope": "organization",
                "requestType": "clarification",
                "urgency": "medium",
                "summary": "需要平台接通协助",
            },
            "support-1",
        )
        feedback = _platform_command(
            client,
            auth,
            "software-feedback",
            {
                "category": "bug",
                "severity": "high",
                "title": "平台状态需要准确显示",
                "description": "不能用假健康掩盖未连接",
                "screenshotRequested": True,
                "screenshotObjectId": "feedback-screenshot:local-object",
                "screenshotContentHash": "a" * 64,
                "screenshotMediaType": "image/png",
                "screenshotByteSize": 1024,
            },
            "feedback-1",
        )
        listed = _platform_query(client, auth, "support-requests")
        support_result = support.json()["result"]
        resolved = _platform_command(
            client,
            auth,
            f"support-requests/{support_result['id']}/resolve",
            {
                "status": "resolved",
                "resolutionNote": "已给出接通说明",
            },
            "support-resolve-1",
        )
        listed_after_resolve = _platform_query(client, auth, "support-requests")

    assert support.status_code == 200, support.text
    assert support_result["status"] == "open"
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["result"]["queued"] is False
    assert feedback.json()["result"]["state"] == "not_connected"
    assert (
        feedback.json()["result"]["record"]["screenshotState"]
        == "local_saved"
    )
    assert (
        feedback.json()["result"]["record"]["screenshotObjectId"]
        == "feedback-screenshot:local-object"
    )
    assert feedback.json()["result"]["record"]["screenshotPath"] is None
    assert listed.status_code == 200
    assert listed.json()["resource"]["items"][0]["summary"] == "需要平台接通协助"
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["result"]["status"] == "resolved"
    assert (
        listed_after_resolve.json()["resource"]["items"][0]["resolutionNote"]
        == "已给出接通说明"
    )
    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM external_provider_resources
            WHERE provider IN ('yiyu_support', 'yiyu_feedback')
            """
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*) FROM delivery_outbox
            WHERE aggregate_type IN ('support_request', 'software_feedback')
            """
        ).fetchone()[0] == 3


def test_feishu_blocked_sync_retry_is_idempotent_and_auditable(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        first = _platform_command(
            client,
            auth,
            "feishu-sync/calendar/tasks/task-retry",
            {"notify": False},
            "feishu-sync-first",
        )
        duplicate = _platform_command(
            client,
            auth,
            "feishu-sync/calendar/tasks/task-retry",
            {"notify": False},
            "feishu-sync-first",
        )
        retried = _platform_command(
            client,
            auth,
            "feishu-sync/calendar/tasks/task-retry",
            {"notify": False},
            "feishu-sync-retry",
        )

    for response in (first, duplicate, retried):
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["status"] == "not_configured"
        assert result["details"]["retryable"] is True
    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM command_envelopes
            WHERE command_type = 'feishu.sync.calendar_event'
            """
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*) FROM operation_attempts a
            JOIN command_envelopes c ON c.command_id = a.command_id
            WHERE c.command_type = 'feishu.sync.calendar_event'
            """
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*) FROM operation_dead_letters
            WHERE aggregate_type = 'task' AND aggregate_id = 'task-retry'
            """
        ).fetchone()[0] == 2


def test_feishu_task_calendar_sync_is_real_idempotent_and_object_isolated(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_feishu_calendar_provider(
        monkeypatch,
        calendar_outcomes=[
            {
                "code": 0,
                "data": {
                    "event": {
                        "event_id": "evt-task-one",
                        "app_link": "https://applink.feishu.cn/event/task-one",
                    }
                },
            },
            {
                "code": 0,
                "data": {
                    "event": {
                        "event_id": "evt-task-two",
                        "app_link": "https://applink.feishu.cn/event/task-two",
                    }
                },
            },
            {
                "code": 0,
                "data": {"event": {"event_id": "evt-task-one"}},
            },
        ],
    )
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        configured = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "scopeKind": "organization",
                "appId": "cli_calendar_sync",
                "appSecret": "calendar-sync-secret",
            },
            "calendar-sync-configuration",
        )
        task_one = client.post(
            "/api/v2/tasks",
            headers={**auth, "Idempotency-Key": "calendar-task-one"},
            json={
                "title": "日慈项目资料核对",
                "description": "核对组织共享摘要",
                "dueDate": "2026-08-02",
            },
        )
        task_two = client.post(
            "/api/v2/tasks",
            headers={**auth, "Idempotency-Key": "calendar-task-two"},
            json={
                "title": "星丛项目排期",
                "description": "确认项目排期",
                "dueDate": "2026-08-03",
            },
        )
        assert task_one.status_code == 201, task_one.text
        assert task_two.status_code == 201, task_two.text
        task_one_id = task_one.json()["task"]["taskId"]
        task_two_id = task_two.json()["task"]["taskId"]
        before_two = _platform_query(
            client,
            auth,
            "feishu-sync/status",
            localType="task",
            localId=task_two_id,
            remoteType="calendar_event",
        )
        synced_one = _platform_command(
            client,
            auth,
            f"feishu-sync/calendar/tasks/{task_one_id}",
            {"notify": False},
            "calendar-sync-task-one",
        )
        replay_one = _platform_command(
            client,
            auth,
            f"feishu-sync/calendar/tasks/{task_one_id}",
            {"notify": False},
            "calendar-sync-task-one",
        )
        after_one = _platform_query(
            client,
            auth,
            "feishu-sync/status",
            localType="task",
            localId=task_one_id,
            remoteType="calendar_event",
        )
        still_unsynced_two = _platform_query(
            client,
            auth,
            "feishu-sync/status",
            localType="task",
            localId=task_two_id,
            remoteType="calendar_event",
        )
        synced_two = _platform_command(
            client,
            auth,
            f"feishu-sync/calendar/tasks/{task_two_id}",
            {"notify": True},
            "calendar-sync-task-two",
        )
        after_two = _platform_query(
            client,
            auth,
            "feishu-sync/status",
            localType="task",
            localId=task_two_id,
            remoteType="calendar_event",
        )
        updated_task_one = client.patch(
            f"/api/v2/tasks/{task_one_id}",
            headers={**auth, "Idempotency-Key": "calendar-task-one-update"},
            json={
                "expectedVersion": task_one.json()["task"]["version"],
                "title": "日慈项目资料复核",
                "dueDate": "2026-08-05",
            },
        )
        assert updated_task_one.status_code == 200, updated_task_one.text
        resynced_one = _platform_command(
            client,
            auth,
            f"feishu-sync/calendar/tasks/{task_one_id}",
            {"notify": False},
            "calendar-sync-task-one-update",
        )

    assert configured.status_code == 200, configured.text
    assert before_two.json()["resource"]["status"] == "idle"
    assert synced_one.status_code == 200, synced_one.text
    assert replay_one.status_code == 200, replay_one.text
    first_result = synced_one.json()["result"]
    assert first_result["status"] == "synced"
    assert first_result["remoteId"] == "evt-task-one"
    assert replay_one.json()["result"] == first_result
    assert after_one.json()["resource"]["remoteId"] == "evt-task-one"
    assert still_unsynced_two.json()["resource"]["status"] == "idle"
    assert still_unsynced_two.json()["resource"]["remoteId"] is None
    assert synced_two.json()["result"]["remoteId"] == "evt-task-two"
    assert after_two.json()["resource"]["remoteId"] == "evt-task-two"
    assert resynced_one.json()["result"]["remoteId"] == "evt-task-one"
    calendar_calls = [
        call
        for call in calls
        if "/events" in call["url"]
    ]
    assert len(calendar_calls) == 3
    assert [call["method"] for call in calendar_calls] == [
        "POST",
        "POST",
        "PATCH",
    ]
    assert all(call["authorized"] is True for call in calendar_calls)
    assert calendar_calls[0]["json"]["start_time"] == {"date": "2026-08-02"}
    assert calendar_calls[0]["json"]["end_time"] == {"date": "2026-08-03"}
    assert calendar_calls[0]["json"]["need_notification"] is False
    assert calendar_calls[1]["json"]["need_notification"] is True
    assert (
        calendar_calls[0]["params"]["idempotency_key"]
        != calendar_calls[1]["params"]["idempotency_key"]
    )
    assert calendar_calls[2]["json"]["summary"] == "日慈项目资料复核"
    assert calendar_calls[2]["json"]["start_time"] == {"date": "2026-08-05"}
    persisted = database.read_bytes()
    assert b"temporary-calendar-token" not in persisted
    assert b"calendar-sync-secret" not in persisted
    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM command_envelopes
            WHERE command_type = 'feishu.sync.calendar_event'
            """
        ).fetchone()[0] == 3
        assert connection.execute(
            """
            SELECT COUNT(*) FROM external_side_effects
            WHERE effect_kind = 'feishu.sync.calendar_event'
              AND outcome = 'succeeded'
            """
        ).fetchone()[0] == 3
        assert connection.execute(
            """
            SELECT COUNT(*) FROM delivery_outbox
            WHERE event_type = 'feishu.sync.calendar_event'
              AND status = 'delivered'
            """
        ).fetchone()[0] == 3
        sync_payloads = connection.execute(
            """
            SELECT payload_json FROM command_envelopes
            WHERE command_type = 'feishu.sync.calendar_event'
            """
        ).fetchall()
        assert all("titleHash" in str(row["payload_json"]) for row in sync_payloads)
        assert all("日慈" not in str(row["payload_json"]) for row in sync_payloads)
        assert all("星丛" not in str(row["payload_json"]) for row in sync_payloads)


def test_feishu_task_sync_timeout_rejection_and_retry_have_exact_states(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_feishu_calendar_provider(
        monkeypatch,
        calendar_outcomes=[
            httpx.ReadTimeout(
                "provider timeout",
                request=httpx.Request(
                    "POST",
                    (
                        f"{cloud_platform_integrations.FEISHU_CALENDAR_API_ROOT}"
                        "/primary/events"
                    ),
                ),
            ),
            {"code": 19001, "msg": "calendar permission denied"},
            {
                "code": 0,
                "data": {"event": {"event_id": "evt-retry-success"}},
            },
        ],
    )
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        configured = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "scopeKind": "organization",
                "appId": "cli_calendar_retry",
                "appSecret": "calendar-retry-secret",
            },
            "calendar-retry-configuration",
        )
        task = client.post(
            "/api/v2/tasks",
            headers={**auth, "Idempotency-Key": "calendar-retry-task"},
            json={"title": "飞书失败重试", "dueDate": "2026-08-04"},
        )
        assert task.status_code == 201, task.text
        task_id = task.json()["task"]["taskId"]
        timed_out = _platform_command(
            client,
            auth,
            f"feishu-sync/calendar/tasks/{task_id}",
            {"notify": False},
            "calendar-retry-timeout",
        )
        after_timeout = _platform_query(
            client,
            auth,
            "feishu-sync/status",
            localType="task",
            localId=task_id,
            remoteType="calendar_event",
        )
        rejected = _platform_command(
            client,
            auth,
            f"feishu-sync/calendar/tasks/{task_id}",
            {"notify": False},
            "calendar-retry-rejected",
        )
        succeeded = _platform_command(
            client,
            auth,
            f"feishu-sync/calendar/tasks/{task_id}",
            {"notify": False},
            "calendar-retry-success",
        )
        final_status = _platform_query(
            client,
            auth,
            "feishu-sync/status",
            localType="task",
            localId=task_id,
            remoteType="calendar_event",
        )

    assert configured.status_code == 200, configured.text
    timeout_result = timed_out.json()["result"]
    assert timeout_result["status"] == "failed_retryable"
    assert timeout_result["details"]["state"] == "failed_retryable"
    assert timeout_result["details"]["errorCode"] == "feishu_sync_timeout"
    assert after_timeout.json()["resource"] == timeout_result
    rejected_result = rejected.json()["result"]
    assert rejected_result["status"] == "failed_retryable"
    assert (
        rejected_result["details"]["errorCode"]
        == "feishu_sync_provider_rejected"
    )
    assert succeeded.json()["result"]["status"] == "synced"
    assert succeeded.json()["result"]["remoteId"] == "evt-retry-success"
    assert final_status.json()["resource"]["remoteId"] == "evt-retry-success"
    calendar_calls = [
        call
        for call in calls
        if "/events" in call["url"]
    ]
    assert len(calendar_calls) == 3
    assert len(
        {call["params"]["idempotency_key"] for call in calendar_calls}
    ) == 1
    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM operation_dead_letters
            WHERE aggregate_type = 'task' AND aggregate_id = ?
            """,
            (task_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*) FROM external_side_effects
            WHERE effect_kind = 'feishu.sync.calendar_event'
              AND outcome = 'failed_retryable'
            """
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*) FROM external_side_effects
            WHERE effect_kind = 'feishu.sync.calendar_event'
              AND outcome = 'succeeded'
            """
        ).fetchone()[0] == 1


def test_feishu_task_sync_without_authoritative_time_is_blocked_without_request(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = _install_feishu_calendar_provider(
        monkeypatch,
        calendar_outcomes=[],
    )
    client, _database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        configured = _platform_command(
            client,
            auth,
            "org-integrations/feishu/validate-and-save",
            {
                "scopeKind": "organization",
                "appId": "cli_calendar_time_block",
                "appSecret": "calendar-time-block-secret",
            },
            "calendar-time-block-configuration",
        )
        task = client.post(
            "/api/v2/tasks",
            headers={**auth, "Idempotency-Key": "calendar-time-block-task"},
            json={"title": "没有日期的任务"},
        )
        assert task.status_code == 201, task.text
        blocked = _platform_command(
            client,
            auth,
            f"feishu-sync/calendar/tasks/{task.json()['task']['taskId']}",
            {"notify": False},
            "calendar-time-block-sync",
        )

    assert configured.status_code == 200, configured.text
    assert blocked.status_code == 200, blocked.text
    result = blocked.json()["result"]
    assert result["status"] == "time_invalid"
    assert result["details"]["state"] == "blocked"
    assert result["details"]["errorCode"] == "feishu_task_time_missing"
    assert all(
        call["url"] == cloud_platform_integrations.FEISHU_TENANT_TOKEN_URL
        for call in calls
    )


def test_renderer_gates_platform_polling_after_status_probe() -> None:
    root = Path(__file__).resolve().parents[1]
    app_source = (root / "src/renderer/App.tsx").read_text(encoding="utf-8")
    local_ai_source = (
        root / "src/renderer/components/data_center/LocalAiHealthCard.tsx"
    ).read_text(encoding="utf-8")
    deep_read_source = (
        root / "src/renderer/components/data_center/DeepReadSettingsCard.tsx"
    ).read_text(encoding="utf-8")
    asr_source = (
        root / "src/renderer/components/settings/LocalAsrModelPanel.tsx"
    ).read_text(encoding="utf-8")
    ollama_source = (
        root / "src/renderer/components/settings/OllamaQuickPullPanel.tsx"
    ).read_text(encoding="utf-8")

    assert "if (!cancelled && pollingEnabled)" in app_source
    assert "if (active && pollingEnabled)" in app_source
    assert "health?.pollingEnabled !== true" in app_source
    assert (
        "health?.platformCapabilities?.audioTranscription?.pollingEnabled === true"
        in app_source
    )
    assert "h.pollingEnabled === true || q.pollingEnabled === true" in local_ai_source
    assert "if (alive && pollingEnabled)" in deep_read_source
    assert "localAiPollingEnabled" in deep_read_source
    assert asr_source.count("status?.pollingEnabled === true") == 2
    assert "pullStatus?.pollingEnabled === true" in ollama_source


def test_local_platform_ledger_is_sandbox_scoped_and_executes_asr(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeDownloadManager:
        def status(self) -> DownloadProgress:
            return DownloadProgress(in_progress=True)

        def start(
            self,
            model_names: str | list[str],
            *,
            prefer_mirror: bool,
        ) -> tuple[bool, str]:
            assert model_names
            assert prefer_mirror is True
            return True, "已开始下载"

        def cancel(self) -> bool:
            return True

    monkeypatch.setattr(
        platform_integrations,
        "get_download_manager",
        lambda _root: FakeDownloadManager(),
    )
    monkeypatch.setattr(
        platform_integrations,
        "transcribe_audio",
        lambda _root, _path, language: TranscriptionResult(
            text="严格新版本机转写",
            segments=[
                TranscriptionSegment(
                    start_ms=0,
                    end_ms=1000,
                    text="严格新版本机转写",
                )
            ],
            language=language,
            duration_ms=1000,
            elapsed_ms=12,
        ),
    )
    client, headers = _local(tmp_path)
    database = tmp_path / "local" / "strict-local.db"
    audio = tmp_path / "speech.wav"
    audio.write_bytes(b"mock-audio")
    with client:
        download = client.post(
            "/api/v2/ui/local-asr/model/download",
            headers={**headers, "Idempotency-Key": "asr-download-1"},
            json={},
        )
        transcribe = client.post(
            "/api/v2/ui/local-asr/transcribe-test",
            headers={**headers, "Idempotency-Key": "asr-test-1"},
            json={"language": "auto", "audioPath": str(audio)},
        )
        recent = client.get(
            "/api/v2/ui/audio-transcription-jobs/recent",
            headers=headers,
        )

    assert download.status_code == 200, download.text
    assert download.json()["state"] == "processing"
    assert download.json()["started"] is True
    assert download.json()["pollingEnabled"] is True
    assert transcribe.status_code == 200, transcribe.text
    assert transcribe.json()["state"] == "completed"
    assert transcribe.json()["success"] is True
    assert transcribe.json()["text"] == "严格新版本机转写"
    assert transcribe.json()["pollingEnabled"] is False
    assert recent.status_code == 200, recent.text
    assert len(recent.json()["jobs"]) == 1
    assert recent.json()["jobs"][0]["operationId"] == transcribe.json()["operationId"]
    assert recent.json()["pollingEnabled"] is False

    with runtime_connection(database, "local") as connection:
        active_sandbox = connection.execute(
            "SELECT sandbox_id FROM workspace_sandboxes WHERE is_active = 1"
        ).fetchone()[0]
        for table in (
            "command_envelopes",
            "operation_attempts",
            "delivery_outbox",
            "audit_events",
        ):
            rows = connection.execute(
                f"SELECT DISTINCT sandbox_id FROM {table}"
            ).fetchall()
            assert {str(row[0]) for row in rows} == {str(active_sandbox)}
        assert connection.execute(
            "SELECT COUNT(*) FROM operation_dead_letters"
        ).fetchone()[0] == 0
        assert connection.execute(
            """
            SELECT COUNT(*) FROM command_envelopes
            WHERE sandbox_id = ?
              AND command_type IN (
                'local_asr.model.download',
                'local_asr.transcribe_test'
              )
            """,
            (active_sandbox,),
        ).fetchone()[0] == 2

    handler_source = (
        Path(__file__).resolve().parents[1]
        / "backend/app/ui_domains/platform_integrations.py"
    ).read_text(encoding="utf-8")
    assert "SELECT " not in handler_source
    assert "INSERT " not in handler_source
    assert "UPDATE " not in handler_source


def test_local_ai_settings_queue_coverage_and_execution_are_real_and_restart_safe(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    class FakeMaterials:
        summary = ""

        def __init__(self, _runtime: Any):
            pass

        def optimization_candidates(
            self,
            project_ids: list[str],
        ) -> list[dict[str, Any]]:
            assert project_ids == ["project-local-ai"]
            return [
                {
                    "projectId": "project-local-ai",
                    "documentId": "document-local-ai",
                    "title": "本机项目背景",
                    "contentHash": "content-hash",
                    "summaryKind": (
                        "ai_summary" if self.summary else "text_excerpt"
                    ),
                    "deepRead": bool(self.summary),
                }
            ]

        def document_text(self, document_id: str) -> dict[str, Any]:
            assert document_id == "document-local-ai"
            return {"content": "只保存在当前设备上的项目资料正文"}

        def update_ai_summary(
            self,
            document_id: str,
            *,
            summary: str,
            model_name: str,
        ) -> dict[str, Any]:
            assert document_id == "document-local-ai"
            assert model_name == "organization-model"
            FakeMaterials.summary = summary
            return {
                "summaryHash": "summary-hash",
                "summaryKind": "ai_summary",
            }

    monkeypatch.setattr(
        platform_integrations,
        "LocalProjectMaterialsRepository",
        FakeMaterials,
    )
    monkeypatch.setattr(
        platform_integrations,
        "_local_ai_projects",
        lambda _compatibility: ["project-local-ai"],
    )
    client, headers = _local(tmp_path)
    with client:
        client.app.state.runtime.pinned_workspace_context = (  # type: ignore[method-assign]
            lambda: nullcontext()
        )
        monkeypatch.setattr(
            client.app.state.runtime,
            "private_ai_completion",
            lambda **_kwargs: {
                "content": "深度摘要",
                "modelName": "organization-model",
            },
        )
        updated = client.put(
            "/api/v2/ui/local-ai/settings",
            headers={**headers, "Idempotency-Key": "local-ai-settings-1"},
            json={"enabled": True, "paused": False, "manualActive": True},
        )
        coverage_before = client.get(
            "/api/v2/ui/local-ai/coverage",
            headers=headers,
        )
        queued = client.post(
            "/api/v2/ui/local-ai/backfill",
            headers={**headers, "Idempotency-Key": "local-ai-backfill-1"},
        )
        queue_before = client.get(
            "/api/v2/ui/local-ai/queue",
            headers=headers,
        )
        executed = client.post(
            "/api/v2/ui/local-ai/run-now?force=true",
            headers={**headers, "Idempotency-Key": "local-ai-run-1"},
        )
        queue_after = client.get(
            "/api/v2/ui/local-ai/queue",
            headers=headers,
        )
        coverage_after = client.get(
            "/api/v2/ui/local-ai/coverage",
            headers=headers,
        )

    assert updated.status_code == 200, updated.text
    assert updated.json()["state"] == "ready"
    assert updated.json()["enabled"] is True
    assert coverage_before.json()["totalDocuments"] == 1
    assert coverage_before.json()["totalDeepRead"] == 0
    assert queued.status_code == 200, queued.text
    assert queued.json()["created"] == 1
    assert queue_before.json()["totalByStatus"]["queued"] == 1
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "completed"
    assert queue_after.json()["totalByStatus"]["completed"] == 1
    assert coverage_after.json()["totalDeepRead"] == 1
    assert FakeMaterials.summary == "深度摘要"

    with TestClient(client.app) as restarted:
        persisted = restarted.get(
            "/api/v2/ui/local-ai/settings",
            headers=headers,
        )
        persisted_queue = restarted.get(
            "/api/v2/ui/local-ai/queue",
            headers=headers,
        )
    assert persisted.json()["enabled"] is True
    assert persisted_queue.json()["totalByStatus"]["completed"] == 1


def test_local_ai_health_uses_machine_governor_and_execution_is_single_flight(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        platform_integrations,
        "collect_machine_health",
        lambda: MachineHealth(
            thermal_state=0,
            cpu_speed_limit=100,
            user_idle_seconds=900,
            battery_percent=62,
            on_ac_power=False,
            memory_pressure="normal",
        ),
    )
    client, headers = _local(tmp_path)
    with client:
        client.app.state.runtime.pinned_workspace_context = (  # type: ignore[method-assign]
            lambda: nullcontext()
        )
        updated = client.put(
            "/api/v2/ui/local-ai/settings",
            headers={
                **headers,
                "Idempotency-Key": "local-ai-governor-settings",
            },
            json={
                "enabled": True,
                "paused": False,
                "manualActive": True,
                "requireACPower": True,
                "minIdleSeconds": 300,
            },
        )
        health = client.get("/api/v2/ui/local-ai/health", headers=headers)
        assert platform_integrations._LOCAL_AI_EXECUTION_LOCK.acquire(
            blocking=False
        )
        try:
            concurrent = client.post(
                "/api/v2/ui/local-ai/run-now?force=true",
                headers={
                    **headers,
                    "Idempotency-Key": "local-ai-concurrent-run",
                },
            )
        finally:
            platform_integrations._LOCAL_AI_EXECUTION_LOCK.release()

    assert updated.status_code == 200, updated.text
    assert health.status_code == 200, health.text
    assert health.json()["verdict"] == "wait"
    assert health.json()["on_ac_power"] is False
    assert health.json()["battery_percent"] == 62
    assert "电源" in health.json()["reason"]
    assert concurrent.status_code == 200, concurrent.text
    assert concurrent.json()["status"] == "processing"
    assert concurrent.json()["skipped"] == 1


def test_local_ai_run_holds_pinned_workspace_for_entire_executor(
    monkeypatch: Any,
) -> None:
    active = {"pinned": False}

    class Runtime:
        @contextmanager
        def pinned_workspace_context(self):
            active["pinned"] = True
            try:
                yield
            finally:
                active["pinned"] = False

    def execute(_compatibility: Any, _request: UiRequest) -> dict[str, Any]:
        assert active["pinned"] is True
        return {"status": "completed", "processed": 1}

    monkeypatch.setattr(
        platform_integrations,
        "_local_ai_run_now_pinned",
        execute,
    )
    result = platform_integrations.local_ai_run_now(
        SimpleNamespace(runtime=Runtime()),
        UiRequest(
            method="POST",
            path="local-ai/run-now",
            query={"force": "false"},
            body={},
            idempotency_key="pinned-local-ai-run",
        ),
        None,
    )

    assert result["status"] == "completed"
    assert active["pinned"] is False


def test_local_platform_repository_begin_get_update_uses_real_strict_db(
    tmp_path: Path,
) -> None:
    client, _headers = _local(tmp_path)
    database = tmp_path / "local" / "strict-local.db"
    with client:
        operations = LocalPlatformOperationRepository(client.app.state.runtime)
        started = operations.begin(
            idempotency_key="direct-ledger-1",
            command_type="ollama.delete",
            aggregate_type="local_model",
            aggregate_id="qwen-direct:test",
            payload={"modelName": "qwen-direct:test"},
            initial_result={
                "state": "processing",
                "pollingEnabled": False,
                "retryable": True,
            },
        )
        replayed = operations.begin(
            idempotency_key="direct-ledger-1",
            command_type="ollama.delete",
            aggregate_type="local_model",
            aggregate_id="qwen-direct:test",
            payload={"modelName": "qwen-direct:test"},
            initial_result={
                "state": "processing",
                "pollingEnabled": False,
                "retryable": True,
            },
        )
        loaded = operations.get(str(started["operationId"]))
        completed = operations.update(
            operation_id=str(started["operationId"]),
            state="completed",
            result_patch={"success": True},
        )
        retry_started = operations.begin(
            idempotency_key="direct-ledger-retry",
            command_type="local_ai.document_card_generation",
            aggregate_type="local_knowledge_document",
            aggregate_id="document-retry",
            payload={"documentId": "document-retry"},
            initial_result={
                "state": "queued",
                "pollingEnabled": True,
                "retryable": True,
            },
        )
        operations.update(
            operation_id=str(retry_started["operationId"]),
            state="failed_retryable",
            result_patch={"failedAt": "2026-07-31T00:00:00Z"},
            error_code="temporary_failure",
            error_message="临时失败",
        )
        retried = operations.retry(
            operation_id=str(retry_started["operationId"])
        )

        def concurrent_begin() -> dict[str, Any]:
            return operations.begin(
                idempotency_key="direct-ledger-concurrent",
                command_type="ollama.delete",
                aggregate_type="local_model",
                aggregate_id="qwen-concurrent:test",
                payload={"modelName": "qwen-concurrent:test"},
                initial_result={
                    "state": "processing",
                    "pollingEnabled": False,
                    "retryable": True,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent = list(pool.map(lambda _index: concurrent_begin(), range(2)))

    assert replayed["idempotentReplay"] is True
    assert replayed["operationId"] == started["operationId"]
    assert len({item["operationId"] for item in concurrent}) == 1
    assert sum(bool(item.get("idempotentReplay")) for item in concurrent) == 1
    assert loaded is not None
    assert loaded["commandType"] == "ollama.delete"
    assert loaded["aggregateType"] == "local_model"
    assert loaded["aggregateId"] == "qwen-direct:test"
    assert loaded["payload"] == {"modelName": "qwen-direct:test"}
    assert completed["state"] == "completed"
    assert completed["pollingEnabled"] is False
    assert retried["state"] == "queued"
    assert retried["errorCode"] is None
    assert retried["pollingEnabled"] is True
    with runtime_connection(database, "local") as connection:
        envelope = connection.execute(
            """
            SELECT c.aggregate_type, c.aggregate_id, c.command_type,
                   c.actor_principal_id,
                   json_extract(m.receipt, '$.payload') AS payload_json,
                   c.status
            FROM commands AS c
            JOIN object_manifests AS m ON m.id=c.payload_object_manifest_id
            WHERE c.operation_id = ?
            """,
            (started["operationId"],),
        ).fetchone()
        assert envelope["aggregate_type"] == "local_model"
        assert envelope["aggregate_id"] == "qwen-direct:test"
        assert envelope["command_type"] == "ollama.delete"
        assert str(envelope["actor_principal_id"]).startswith("local_device_")
        assert envelope["payload_json"] == '{"modelName":"qwen-direct:test"}'
        assert envelope["status"] == "settled"
        assert connection.execute(
            """
            SELECT status FROM outbox_events
            WHERE operation_id = ?
            """,
            (started["operationId"],),
        ).fetchone()[0] == "published"
        assert connection.execute(
            """
            SELECT status FROM idempotency_records
            WHERE scope_id=(SELECT scope_id FROM commands WHERE operation_id=?)
              AND idempotency_key='direct-ledger-1'
            """,
            (started["operationId"],),
        ).fetchone()[0] == "settled"
        assert connection.execute(
            """
            SELECT COUNT(*) FROM commands
            WHERE command_type = 'ollama.delete'
              AND idempotency_key = 'direct-ledger-concurrent'
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM operation_attempts
            WHERE command_id = ?
            """,
            (retry_started["commandId"],),
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*) FROM dead_letters
            WHERE operation_id = ? AND status = 'open'
            """,
            (retry_started["operationId"],),
        ).fetchone()[0] == 0


def test_ollama_pull_restart_reconciles_to_retryable_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class DeferredThread:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def start(self) -> None:
            return None

    monkeypatch.setattr(platform_integrations, "_PlatformThread", DeferredThread)
    monkeypatch.setattr(
        platform_integrations,
        "_ollama_health",
        lambda: {
            "running": True,
            "baseUrl": "http://127.0.0.1:11434",
            "installedModels": [],
            "error": None,
            "version": "test",
            "state": "ready",
            "retryable": False,
            "pollingEnabled": True,
        },
    )
    with platform_integrations._OLLAMA_LOCK:
        platform_integrations._OLLAMA_PULL.update(
            {
                "inProgress": False,
                "modelName": "",
                "status": "idle",
                "bytesDownloaded": 0,
                "bytesTotal": 0,
                "elapsedSeconds": 0,
                "completed": False,
                "error": None,
                "operationId": None,
            }
        )

    client, headers = _local(tmp_path)
    database = tmp_path / "local" / "strict-local.db"
    with client:
        started = client.post(
            "/api/v2/ui/ollama/pull",
            headers={**headers, "Idempotency-Key": "ollama-pull-restart"},
            json={"modelName": "qwen-test:latest"},
        )
        assert started.status_code == 200, started.text
        assert started.json()["state"] == "processing"
        assert started.json()["pollingEnabled"] is True

        with platform_integrations._OLLAMA_LOCK:
            platform_integrations._OLLAMA_PULL["inProgress"] = False
            platform_integrations._OLLAMA_PULL["operationId"] = None
        reconciled = client.get(
            "/api/v2/ui/ollama/pull/status",
            headers=headers,
        )

    assert reconciled.status_code == 200, reconciled.text
    assert reconciled.json()["state"] == "failed_retryable"
    assert reconciled.json()["errorCode"] == "ollama_pull_worker_interrupted"
    assert reconciled.json()["reconciledAfterRestart"] is True
    assert reconciled.json()["pollingEnabled"] is False
    with runtime_connection(database, "local") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM dead_letters d
            JOIN commands c ON c.operation_id = d.operation_id
            WHERE c.command_type = 'ollama.pull'
            """
        ).fetchone()[0] == 1


def test_feedback_processing_and_runtime_diagnostics_are_real_projections(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        auth = {"Authorization": f"Bearer {admin['accessToken']}"}
        snapshot = client.get("/api/v2/business/snapshot", headers=auth)
        assert snapshot.status_code == 200, snapshot.text
        project_id = snapshot.json()["projects"][0]["projectId"]
        answer = client.post(
            "/api/v2/workbench/answers",
            headers={**auth, "Idempotency-Key": "platform-answer-1"},
            json={
                "projectId": project_id,
                "question": "当前平台链路是否可靠？",
                "answerMarkdown": "严格对象已登记。",
                "sourceManifest": {
                    "projectId": project_id,
                    "documentIds": ["document-evidence"],
                },
                "modelName": "strict-test-model",
            },
        )
        assert answer.status_code == 201, answer.text
        answer_id = answer.json()["answer"]["answerId"]
        review = client.post(
            "/api/v2/workbench/answer-value-reviews",
            headers={**auth, "Idempotency-Key": "platform-review-1"},
            json={
                "clientId": project_id,
                "messageId": answer_id,
                "prompt": "当前平台链路是否可靠？",
                "answerMode": "grounded_fallback",
                "userVisibleQualityStatus": "usable_with_boundary",
                "shouldShowRetryBanner": True,
                "reviewerNote": "边界已准确说明",
            },
        )
        assert review.status_code == 201, review.text
        feedback = _platform_command(
            client,
            auth,
            "software-feedback",
            {"title": "平台投递测试", "description": "可靠队列"},
            "platform-feedback-processing-1",
        )
        feedback_replay = _platform_command(
            client,
            auth,
            "software-feedback",
            {"title": "平台投递测试", "description": "可靠队列"},
            "platform-feedback-processing-1",
        )
        generation = _platform_query(
            client,
            auth,
            "runtime/generation-state",
            clientId=project_id,
        )
        chat = _platform_query(
            client,
            auth,
            "runtime/workspace-chat-diagnostics",
            clientId=project_id,
        )
        value = _platform_query(
            client,
            auth,
            "runtime/workspace-answer-value-diagnostics",
            clientId=project_id,
        )
        reset = _platform_command(
            client,
            auth,
            "runtime/generation-state/reset",
            {"clientId": project_id, "answerIntent": "general"},
            "platform-generation-reset-1",
        )

    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["result"]["state"] == "not_connected"
    assert feedback.json()["result"]["processingAttemptId"] is None
    assert feedback_replay.status_code == 200, feedback_replay.text
    assert feedback_replay.json()["result"]["idempotentReplay"] is True
    assert (
        feedback_replay.json()["result"]["operationId"]
        == feedback.json()["result"]["operationId"]
    )
    assert generation.status_code == 200, generation.text
    assert generation.json()["resource"]["state"] == "ready"
    assert generation.json()["resource"]["recentSuccesses"] == 1
    assert chat.status_code == 200, chat.text
    assert chat.json()["resource"]["state"] == "ready"
    assert chat.json()["resource"]["recentMessages"] == 1
    assert value.status_code == 200, value.text
    assert value.json()["resource"]["state"] == "ready"
    assert (
        value.json()["resource"]["answerModeDistribution"]["grounded_fallback"]
        == 1
    )
    assert value.json()["resource"]["retryBannerWouldShowCount"] == 1
    assert reset.status_code == 200, reset.text
    assert reset.json()["result"]["state"] == "reset"
    assert reset.json()["result"]["recentTotal"] == 0
    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM processing_attempts
            WHERE processing_kind = 'software_feedback.delivery'
            """
        ).fetchone()[0] == 0
