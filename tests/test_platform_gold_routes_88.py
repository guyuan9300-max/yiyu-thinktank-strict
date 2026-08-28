from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import LocalConfig
from backend.app.main import create_app as create_local_app
from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repositories.platform_configurations import (
    PlatformConfigurationRepository,
)
from cloud_backend.app.repositories.platform_integrations import (
    FEISHU_CONFIGURATION_KIND,
    FEISHU_MEMBER_AUTHORIZATION_KIND,
    PlatformIntegrationsRepository,
)
from cloud_backend.app.repositories.platform_runtime_diagnostics import (
    PlatformRuntimeDiagnosticsRepository,
)
from cloud_backend.app.repository import RepositoryError
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository


def _configure_feishu(repository, identity) -> PlatformIntegrationsRepository:
    configurations = PlatformConfigurationRepository(repository)
    configurations.upsert(
        identity,
        configuration_kind=FEISHU_CONFIGURATION_KIND,
        scope_kind="organization",
        provider="feishu",
        public_config={
            "appId": "cli_gold_routes",
            "callbackMode": "cloud_relay",
            "lastValidationStatus": "succeeded",
            "lastValidationMessage": "",
        },
        expected_version=0,
        idempotency_key="gold-feishu-organization",
        secret_bundle={"appSecret": "test-secret"},
        secret_action="replace",
    )
    configurations.upsert(
        identity,
        configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
        scope_kind="personal",
        provider="feishu",
        public_config={
            "linked": True,
            "authorizationState": "ready",
            "openId": "ou_gold_member",
        },
        expected_version=0,
        idempotency_key="gold-feishu-member",
        secret_bundle={"accessToken": "member-token"},
        secret_action="replace",
    )
    return PlatformIntegrationsRepository(repository)


def test_feishu_task_and_document_sync_execute_and_settle_in_88_tables(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository, identity, _ = _repository(tmp_path)
    platform = _configure_feishu(repository, identity)
    task = GC04TaskRepository(repository).create_task(
        identity,
        payload={"title": "同步到飞书", "dueDate": "2026-08-08"},
        idempotency_key="gold-task-create",
    )["task"]
    monkeypatch.setattr(
        platform,
        "_execute_feishu_calendar_event",
        lambda *_args, **_kwargs: {
            "remoteId": "evt_gold",
            "remoteUrl": "https://feishu.cn/calendar/evt_gold",
            "calendarId": "primary",
        },
    )
    monkeypatch.setattr(
        platform,
        "_execute_feishu_docx_sync",
        lambda *_args, **_kwargs: {
            "remoteId": "docx_gold",
            "remoteUrl": "https://feishu.cn/docx/docx_gold",
        },
    )

    calendar = platform.request_feishu_sync(
        identity,
        local_type="task",
        local_id=str(task["id"]),
        remote_type="calendar_event",
        payload={"notify": False},
        idempotency_key="gold-calendar-sync",
    )
    calendar_replay = platform.request_feishu_sync(
        identity,
        local_type="task",
        local_id=str(task["id"]),
        remote_type="calendar_event",
        payload={"notify": False},
        idempotency_key="gold-calendar-sync",
    )
    document = platform.request_feishu_sync(
        identity,
        local_type="document",
        local_id="local-document-gold",
        remote_type="docx_document",
        payload={"title": "同步文档", "content": "正文只在执行请求中短暂使用"},
        idempotency_key="gold-document-sync",
    )

    assert calendar["state"] == "ready"
    assert calendar["remoteId"] == "evt_gold"
    assert calendar_replay["idempotentReplay"] is True
    assert document["state"] == "ready"
    assert document["remoteId"] == "docx_gold"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE command_type IN "
            "('feishu.sync.calendar_event','feishu.sync.docx_document')"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT owner_membership_id FROM provider_resources "
            "WHERE resource_kind='docx_document' AND remote_id='docx_gold'"
        ).fetchone()[0] == identity.membership_id
        mappings = connection.execute(
            "SELECT mapping_kind, local_resource_id, remote_id, "
            "bound_membership_id FROM feishu_mappings ORDER BY mapping_kind"
        ).fetchall()
        assert [tuple(row) for row in mappings] == [
            ("calendar_event", str(task["id"]), "evt_gold", None),
            ("docx_document", "local-document-gold", "docx_gold", identity.membership_id),
        ]
        assert connection.execute(
            "SELECT COUNT(*) FROM saga_operations WHERE outcome='succeeded' "
            "AND current_step='mapping_recorded'"
        ).fetchone()[0] == 2
        assert "正文只在执行请求中短暂使用".encode() not in repository.database_path.read_bytes()


def test_feishu_document_search_is_retired_in_favor_of_one_time_link_import(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository, identity, _ = _repository(tmp_path)
    platform = _configure_feishu(repository, identity)
    monkeypatch.setattr(platform, "_feishu_member_access_token", lambda _identity: "token")
    monkeypatch.setattr(
        platform,
        "_feishu_provider_json",
        lambda *_args, **_kwargs: {
            "data": {
                "items": [
                    {"document_id": "doc_gold", "type": "docx", "title": "项目资料"}
                ]
            }
        },
    )
    with pytest.raises(RepositoryError) as retired:
        platform.request_feishu_import(
            identity,
            action="search",
            payload={"query": "项目资料", "pageSize": 10},
            idempotency_key="gold-feishu-search-retired",
        )
    assert retired.value.code == "feishu_import_action_invalid"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE command_type='feishu.import.search'"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_runtime_diagnostics_read_only_88_tables_and_generation_reset_receipt(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _repository(tmp_path)
    platform = PlatformIntegrationsRepository(repository)
    diagnostics = PlatformRuntimeDiagnosticsRepository(repository)
    before = diagnostics.generation_state(identity, {"clientId": "client-gold"})
    reset = platform.command(
        identity,
        resource_path="runtime/generation-state/reset",
        authorization_scope="organization",
        method="POST",
        query={},
        payload={"clientId": "client-gold", "answerIntent": "general"},
        idempotency_key="gold-generation-reset",
    )
    after = platform.query(
        identity,
        resource_path="runtime/generation-state",
        authorization_scope="organization",
        query={"clientId": "client-gold", "answerIntent": "general"},
    )
    background = diagnostics.active_background_tasks(identity)
    metrics = diagnostics.analysis_metrics(identity)
    assert before["state"] == "ready_empty"
    assert reset["state"] == "reset"
    assert after["resetBoundary"]
    assert background["state"] == "ready"
    assert metrics["state"] in {"ready", "ready_empty"}


def test_local_gold_platform_routes_are_http_registered_and_execute(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data_dir = tmp_path / "local"
    app = create_local_app(
        LocalConfig(
            data_dir=data_dir,
            database_path=data_dir / "strict-local.db",
            host="127.0.0.1",
            port=47929,
            desktop_token="gold-platform-token",
            secret_namespace="test.gold.platform",
            test_mode=True,
        )
    )
    app.state.runtime.pinned_workspace_context = lambda: nullcontext()
    monkeypatch.setattr(
        app.state.runtime,
        "private_ai_completion",
        lambda **kwargs: {
            "content": (
                '{"steps":[{"action":"核对资料","basis":"用户指令",'
                '"deliverable":"核对结果"}]}'
                if "步骤" in str(kwargs.get("system_prompt") or "")
                else '{"suggestedTags":["资料核对"]}'
            ),
            "modelName": "test-model",
        },
    )
    headers = {"X-Yiyu-Desktop-Token": "gold-platform-token"}
    with TestClient(app) as client:
        tools = client.get("/api/v2/ui/tool-registry", headers=headers)
        parsed = client.post(
            "/api/v2/ui/ai-command/parse-steps",
            headers={**headers, "Idempotency-Key": "gold-parse"},
            json={"text": "核对项目资料"},
        )
        tags = client.post(
            "/api/v2/ui/local/tasks/tag-suggestions",
            headers={**headers, "Idempotency-Key": "gold-tags"},
            json={"title": "核对项目资料"},
        )
    assert tools.status_code == 200, tools.text
    assert parsed.status_code == 200, parsed.text
    assert parsed.json()["steps"][0]["action"] == "核对资料"
    assert tags.status_code == 200, tags.text
    assert tags.json()["suggestedTags"] == ["资料核对"]
