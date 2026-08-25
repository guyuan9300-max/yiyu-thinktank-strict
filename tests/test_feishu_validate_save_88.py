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
from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repository import RepositoryError
from strict_common.schema import runtime_connection
from tests.test_gc04_gc05_tasks import _member
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


def test_task_notification_to_self_has_exactly_one_recipient(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    tasks = GC04TaskRepository(repository)
    platform = PlatformIntegrationsRepository(repository)
    created = tasks.create_task(
        identity,
        payload={
            "title": "仅通知当前负责人的隔离验收任务",
            "priority": "normal",
            "ownerMembershipId": identity.membership_id,
            "collaboratorMembershipIds": [],
        },
        idempotency_key="feishu-task-self-create",
    )
    calls: list[dict[str, str]] = []

    monkeypatch.setattr(
        platform,
        "personal_feishu_delivery_profile",
        lambda _target: {"readyForNotifications": True},
    )

    def fake_send(target: object, **kwargs: str) -> dict[str, Any]:
        calls.append(
            {
                "membershipId": str(getattr(target, "membership_id")),
                "idempotencyKey": kwargs["idempotency_key"],
            }
        )
        return {
            "state": "succeeded",
            "message": "隔离测试已发送",
            "remoteId": "isolated-self-message",
        }

    monkeypatch.setattr(platform, "send_personal_feishu_text", fake_send)
    result = platform.deliver_task_notifications(
        identity,
        result=created,
        event="created",
        idempotency_key="feishu-task-self-create",
    )

    assert result["state"] == "completed"
    assert result["requestedRecipients"] == 1
    assert result["deliveryCount"] == 1
    assert calls == [
        {
            "membershipId": identity.membership_id,
            "idempotencyKey": (
                "feishu-task-self-create:feishu:created:1:"
                f"{identity.membership_id}"
            ),
        }
    ]


def test_task_notification_card_states_actor_role_and_changes() -> None:
    card = PlatformIntegrationsRepository._task_notification_card(
        task={
            "title": "飞书卡片字段验收",
            "priority": "high",
            "scheduled_start_at": "2026-08-25T09:00:00+08:00",
            "scheduled_end_at": "2026-08-25T10:00:00+08:00",
        },
        action_label="任务已修改",
        sender_name="林佳维",
        recipient_role="负责人",
        event="updated",
        field_changes={
            "title": {"old": "飞书卡片验收", "new": "飞书卡片字段验收"},
            "time": {
                "old": "2026-08-25 09:00—10:00",
                "new": "2026-08-25 09:00—11:00",
            },
            "priority": {"old": "普通", "new": "高"},
        },
    )

    assert card["header"]["title"]["content"] == "任务已修改"
    content = card["elements"][0]["text"]["content"]
    assert "任务名称（修改）：** 飞书卡片验收 → **飞书卡片字段验收**" in content
    assert "时间（修改）：** 2026-08-25 09:00—10:00 → **2026-08-25 09:00—11:00**" in content
    assert "优先级（修改）：** 普通 → **高**" in content
    assert "修改项" not in content
    assert "任务说明" not in content
    assert "你的身份：** 负责人" in content
    assert "操作者：** 林佳维" in content
    assert content.index("你的身份：** 负责人") < content.index("时间（修改）：**")
    assert content.index("时间（修改）：**") < content.index("优先级（修改）：**")
    assert "你有一项任务需要处理" not in content
    assert "来自" not in content

    role_card = PlatformIntegrationsRepository._task_notification_card(
        task={"title": "身份变更验收", "priority": "normal"},
        action_label="任务已修改",
        sender_name="林佳维",
        recipient_role="负责人",
        event="updated",
        role_change={"old": "协作者", "new": "负责人"},
    )
    role_content = role_card["elements"][0]["text"]["content"]
    assert "你的身份（修改）：** 协作者 → **负责人**" in role_content


def test_task_notification_holds_collaborators_until_owner_accepts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, creator, _payload = _repository(tmp_path)
    owner = _member(
        repository,
        creator,
        suffix="feishu_owner_gate",
        display_name="隔离测试负责人",
    )
    collaborator = _member(
        repository,
        creator,
        suffix="feishu_collaborator_gate",
        display_name="隔离测试协作者",
    )
    tasks = GC04TaskRepository(repository)
    platform = PlatformIntegrationsRepository(repository)
    created = tasks.create_task(
        creator,
        payload={
            "title": "负责人接收前不通知协作者",
            "priority": "normal",
            "ownerMembershipId": owner.membership_id,
            "collaboratorMembershipIds": [collaborator.membership_id],
        },
        idempotency_key="feishu-task-owner-gate",
    )
    recipients: list[str] = []
    monkeypatch.setattr(
        platform,
        "personal_feishu_delivery_profile",
        lambda _target: {"readyForNotifications": True},
    )

    def fake_send(target: object, **_kwargs: str) -> dict[str, Any]:
        recipients.append(str(getattr(target, "membership_id")))
        return {
            "state": "succeeded",
            "message": "隔离测试已发送",
            "remoteId": "isolated-owner-message",
        }

    monkeypatch.setattr(platform, "send_personal_feishu_text", fake_send)
    result = platform.deliver_task_notifications(
        creator,
        result=created,
        event="created",
        idempotency_key="feishu-task-owner-gate",
    )

    assert result["requestedRecipients"] == 1
    assert recipients == [owner.membership_id]
    assert collaborator.membership_id not in recipients


def test_feishu_task_update_uses_member_delta_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    platform = PlatformIntegrationsRepository(repository)
    calls: list[dict[str, Any]] = []

    with platform._connection() as connection:
        connection.execute(
            "INSERT INTO feishu_mappings "
            "(id,scope_id,external_side_effect_id,remote_id,remote_receipt,status,"
            "mapping_kind,local_resource_id,bound_membership_id,created_at,revoked_at,"
            "version,lifecycle_state,updated_at,deleted_at) "
            "VALUES (?,?,NULL,?,?,'active','task_v2_task',?,NULL,?,NULL,1,'active',?,NULL)",
            (
                "feishu-map-update-test",
                identity.scope_id,
                "remote-task-1",
                '{"remoteUrl":"https://example.invalid/task/remote-task-1"}',
                "local-task-1",
                "2026-08-25T00:00:00Z",
                "2026-08-25T00:00:00Z",
            ),
        )
        connection.commit()
    monkeypatch.setattr(platform, "_feishu_configuration", lambda _identity: {})
    monkeypatch.setattr(
        platform,
        "_feishu_tenant_access_token",
        lambda _identity, _configuration: "tenant-token",
    )

    def fake_provider(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(
            {
                "method": method,
                "url": url,
                "payload": kwargs.get("payload"),
            }
        )
        if method == "GET":
            return {
                "data": {
                    "task": {
                        "guid": "remote-task-1",
                        "members": [
                            {"id": "old-open-id", "type": "user", "role": "assignee"}
                        ],
                    }
                }
            }
        return {
            "data": {
                "task": {
                    "guid": "remote-task-1",
                    "url": "https://example.invalid/task/remote-task-1",
                }
            }
        }

    monkeypatch.setattr(platform, "_feishu_provider_json", fake_provider)
    monkeypatch.setattr(
        platform,
        "_record_command",
        lambda *_args, **kwargs: dict(kwargs.get("result_details") or {}),
    )
    monkeypatch.setattr(platform, "_record_feishu_mapping", lambda *_args, **_kwargs: None)

    result = platform._project_task_to_feishu(
        identity,
        task={
            "id": "local-task-1",
            "title": "任务更新投影",
            "description": "只用正式字段更新任务",
            "version": 2,
            "scheduled_start_at": "2026-08-24T09:00",
            "scheduled_end_at": "2026-08-24T10:00",
        },
        member_open_ids=["new-open-id"],
        idempotency_key="feishu-task-update-member-delta",
        event="updated",
    )

    patch_call = next(call for call in calls if call["method"] == "PATCH")
    assert "members" not in patch_call["payload"]["task"]
    assert "members" not in patch_call["payload"]["update_fields"]
    add_call = next(call for call in calls if call["url"].endswith("/add_members"))
    remove_call = next(call for call in calls if call["url"].endswith("/remove_members"))
    assert add_call["payload"]["members"] == [
        {"id": "new-open-id", "type": "user", "role": "assignee"}
    ]
    assert remove_call["payload"]["members"] == [
        {"id": "old-open-id", "type": "user", "role": "assignee"}
    ]
    assert result["state"] == "completed"


def test_feishu_task_non_create_never_falls_back_to_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    platform = PlatformIntegrationsRepository(repository)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        platform,
        "_feishu_provider_json",
        lambda method, url, **kwargs: calls.append({"method": method, "url": url}) or {},
    )
    monkeypatch.setattr(
        platform,
        "_record_command",
        lambda *_args, **kwargs: dict(kwargs.get("result_details") or {}),
    )

    result = platform._project_task_to_feishu(
        identity,
        task={"id": "missing-map-task", "title": "不可降级新建", "version": 3},
        member_open_ids=["open-id"],
        idempotency_key="missing-map-complete",
        event="completed",
    )

    assert result["state"] == "failed_retryable"
    assert calls == []


def test_feishu_task_complete_and_reopen_patch_same_mapping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    platform = PlatformIntegrationsRepository(repository)
    with platform._connection() as connection:
        connection.execute(
            "INSERT INTO feishu_mappings "
            "(id,scope_id,external_side_effect_id,remote_id,remote_receipt,status,"
            "mapping_kind,local_resource_id,bound_membership_id,created_at,revoked_at,"
            "version,lifecycle_state,updated_at,deleted_at) "
            "VALUES (?,?,NULL,?,?,'active','task_v2_task',?,NULL,?,NULL,1,'active',?,NULL)",
            (
                "feishu-map-completion-test",
                identity.scope_id,
                "same-remote-task",
                '{"remoteUrl":"https://example.invalid/task/same-remote-task"}',
                "completion-task",
                "2026-08-25T00:00:00Z",
                "2026-08-25T00:00:00Z",
            ),
        )
        connection.commit()
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(platform, "_feishu_configuration", lambda _identity: {})
    monkeypatch.setattr(
        platform, "_feishu_tenant_access_token", lambda _identity, _configuration: "token"
    )

    def fake_provider(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        calls.append({"method": method, "url": url, "payload": kwargs.get("payload")})
        return {"data": {"task": {"guid": "same-remote-task"}}}

    monkeypatch.setattr(platform, "_feishu_provider_json", fake_provider)
    monkeypatch.setattr(
        platform,
        "_record_command",
        lambda *_args, **kwargs: {
            "operationId": "completion-operation",
            **dict(kwargs.get("result_details") or {}),
        },
    )
    monkeypatch.setattr(platform, "_record_feishu_mapping", lambda *_args, **_kwargs: None)

    for event in ("completed", "reopened"):
        result = platform._project_task_to_feishu(
            identity,
            task={"id": "completion-task", "title": "同一飞书任务", "version": 4},
            member_open_ids=[],
            idempotency_key=f"same-map-{event}",
            event=event,
        )
        assert result["remoteId"] == "same-remote-task"

    assert all(call["method"] == "PATCH" for call in calls)
    assert all(call["url"].endswith("/same-remote-task") for call in calls)
    assert calls[0]["payload"]["update_fields"] == ["completed_at"]
    assert calls[1]["payload"]["task"]["completed_at"] == "0"
