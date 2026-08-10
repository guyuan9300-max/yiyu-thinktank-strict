from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from backend.app.ui_domains import build_default_registry
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.repositories.platform_integrations import (
    FEISHU_CONFIGURATION_KIND,
    PlatformIntegrationsRepository,
)
from cloud_backend.app.repository import RepositoryError
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository


class _TenantTokenResponse:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return dict(self._payload)


class _TenantTokenClient:
    def __init__(
        self,
        calls: list[dict[str, Any]],
        response: dict[str, Any],
        **_kwargs: Any,
    ) -> None:
        self.calls = calls
        self.response = response

    def __enter__(self) -> _TenantTokenClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, Any]) -> _TenantTokenResponse:
        self.calls.append({"url": url, "json": dict(json)})
        return _TenantTokenResponse(self.response)


def test_local_feishu_validate_and_save_entry_reaches_cloud_platform_command() -> None:
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
                    "state": "ready",
                    "enabled": True,
                    "lastValidationStatus": "succeeded",
                }
            }

    compatibility = type("Compatibility", (), {"runtime": Runtime()})()
    result = build_default_registry().dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="org-integrations/feishu/validate-and-save",
            query={},
            body={
                "appId": "cli_feishu_entry",
                "appSecret": "transient-secret",
                "scopeKind": "organization",
                "expectedVersion": 0,
            },
            idempotency_key="feishu-entry-88",
        ),
    )

    assert result["state"] == "ready"
    assert calls == [
        {
            "method": "POST",
            "path": "/api/v2/platform-integrations/command",
            "payload": {
                "resourcePath": "org-integrations/feishu/validate-and-save",
                "authorizationScope": "organization",
                "method": "POST",
                "query": {},
                "payload": {
                    "appId": "cli_feishu_entry",
                    "appSecret": "transient-secret",
                    "scopeKind": "organization",
                    "expectedVersion": 0,
                },
            },
            "idempotencyKey": "feishu-entry-88",
            "refreshBusiness": False,
        }
    ]


def test_cloud_feishu_validation_uses_provider_resources_and_external_secret_store(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    calls: list[dict[str, Any]] = []
    secret = "secret-must-stay-outside-sqlite"
    platform = PlatformIntegrationsRepository(
        repository,
        feishu_http_client_factory=lambda **kwargs: _TenantTokenClient(
            calls,
            {"code": 0, "tenant_access_token": "transient-token"},
            **kwargs,
        ),
    )

    saved = platform.save_feishu(
        identity,
        {
            "appId": "cli_feishu_88",
            "appSecret": secret,
            "scopeKind": "organization",
            "expectedVersion": 0,
        },
        "feishu-cloud-88",
    )

    assert saved["state"] == "ready"
    assert saved["lastValidationStatus"] == "succeeded"
    assert len(calls) == 1
    assert calls[0]["json"] == {
        "app_id": "cli_feishu_88",
        "app_secret": secret,
    }
    assert secret.encode() not in repository.database_path.read_bytes()
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT public_config,secret_reference,version "
            "FROM provider_resources WHERE resource_kind=?",
            (FEISHU_CONFIGURATION_KIND,),
        ).fetchone()
        assert row is not None
        assert "cli_feishu_88" in str(row["public_config"])
        assert row["secret_reference"]
        assert int(row["version"]) == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("SELECT 1 FROM scoped_configuration_records")
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("SELECT 1 FROM command_envelopes")


def test_cloud_feishu_missing_secret_and_provider_rejection_are_not_fake_success(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    platform = PlatformIntegrationsRepository(
        repository,
        feishu_http_client_factory=lambda **kwargs: _TenantTokenClient(
            [],
            {"code": 10003, "msg": "invalid app secret"},
            **kwargs,
        ),
    )

    with pytest.raises(RepositoryError) as missing:
        platform.save_feishu(
            identity,
            {"appId": "cli_missing_secret", "scopeKind": "organization"},
            "feishu-missing-secret",
        )
    assert missing.value.status_code == 422
    assert missing.value.code == "feishu_app_secret_required"

    rejected = platform.save_feishu(
        identity,
        {
            "appId": "cli_rejected",
            "appSecret": "rejected-secret",
            "scopeKind": "organization",
            "expectedVersion": 0,
        },
        "feishu-rejected",
    )
    assert rejected["state"] == "failed_retryable"
    assert rejected["enabled"] is False
    assert rejected["lastValidationStatus"] == "failed_retryable"
    assert rejected["authorizationBlockedReason"] == "feishu_tenant_token_rejected"
