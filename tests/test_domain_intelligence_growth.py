from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from backend.app.intelligence_capture_local import PublicCaptureItem
from backend.app.runtime import LocalRuntimeError
from backend.app.ui_domains import intelligence_growth as local_intelligence_growth
from backend.app.ui_domains.intelligence_growth import _private_chat, router
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from cloud_backend.app.repositories.intelligence_growth import (
    IntelligenceGrowthRepository,
)
from strict_common.ids import new_id, utc_now
from strict_common.schema import runtime_connection


BLOCKED_OPERATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "POST",
        "consultation/knowledge-requests/process-pending",
        "没有冻结的 knowledge request/run 权威对象",
    ),
    (
        "POST",
        "data-center/kernel-primary-rollout/start",
        "release_gates 不能承载 rollout run 生命周期",
    ),
    (
        "POST",
        "data-center/kernel-primary-rollout/run_1/complete",
        "release_gates 不能承载 rollout run 生命周期",
    ),
    (
        "POST",
        "data-center/kernel-primary-rollout/run_1/rollback",
        "release_gates 不能承载 rollout run 生命周期",
    ),
    (
        "POST",
        "data-center/rollback-drill",
        "recovery_sets 不能证明实际 rollback drill 已执行",
    ),
    (
        "POST",
        "data-center/schema/ensure",
        "严格合同禁止运行时 DDL",
    ),
    (
        "POST",
        "data-center/team-sync/enqueue-all",
        "没有 team-sync job/provider worker 权威对象",
    ),
    (
        "POST",
        "data-center/team-sync/run-once",
        "没有 team-sync job/provider worker 权威对象",
    ),
    (
        "POST",
        "intelligence/items/intel_1/chat",
        "没有模型回答与来源清单的落库合同",
    ),
    (
        "POST",
        "intelligence/items/intel_1/task-draft",
        "没有模型任务草案的落库合同",
    ),
    (
        "POST",
        "intelligence/items/intel_1/tasks",
        "没有任务批量 CAS 物化合同",
    ),
    (
        "POST",
        "intelligence/profiles/profile_1/refresh",
        "没有 profile 调度运行对象",
    ),
    (
        "POST",
        "intelligence/profiles/profile_1/trial-run",
        "没有 profile 试跑运行对象",
    ),
    (
        "POST",
        "intelligence/profiles/run-due",
        "没有 profile 调度运行对象",
    ),
    (
        "POST",
        "topics/candidates/topic_1/chat",
        "没有模型回答与来源清单的落库合同",
    ),
    (
        "POST",
        "topics/candidates/topic_1/insights",
        "没有模型提炼结果的落库合同",
    ),
    (
        "POST",
        "topics/candidates/topic_1/promote-tasks",
        "没有任务批量 CAS 物化合同",
    ),
    (
        "POST",
        "topics/candidates/topic_1/task-plan",
        "没有模型任务计划的落库合同",
    ),
    (
        "POST",
        "topics/radars",
        "当前严格云冻结表没有 radar 配置权威对象",
    ),
    (
        "DELETE",
        "topics/radars/radar_1",
        "当前严格云冻结表没有 radar 配置权威对象",
    ),
    (
        "PUT",
        "topics/radars/radar_1",
        "当前严格云冻结表没有 radar 配置权威对象",
    ),
    (
        "POST",
        "topics/radars/radar_1/capture",
        "当前严格云冻结表没有 radar identity/run 权威对象",
    ),
    (
        "POST",
        "topics/radars/assist",
        "没有模型草案结果合同",
    ),
    (
        "POST",
        "topics/radars/generate-title",
        "没有模型标题结果合同",
    ),
    (
        "POST",
        "topics/radars/source-label",
        "没有来源标注结果合同",
    ),
)

BLOCKED_ROUTE_KEYS: set[tuple[str, str]] = set()


def _cloud(tmp_path: Path) -> tuple[TestClient, Path]:
    database = tmp_path / "strict-cloud.db"
    config = CloudConfig(
        data_dir=tmp_path,
        database_path=database,
        bootstrap_token="bootstrap-test",
        master_key=Fernet.generate_key().decode(),
        cloud_instance_id=None,
    )
    return TestClient(create_app(config)), database


def _bootstrap(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v2/auth/bootstrap-organization",
        json={
            "organizationName": "情报成长测试组织",
            "displayName": "管理员",
            "email": "intelligence@example.com",
            "password": "12345678",
            "bootstrapToken": "bootstrap-test",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _join_member(
    client: TestClient,
    admin: dict[str, Any],
) -> dict[str, Any]:
    department = client.post(
        "/api/v2/organization/departments",
        headers=_headers(admin, "intelligence-department"),
        json={"name": "研究部", "expectedOrganizationVersion": 1},
    )
    assert department.status_code == 201, department.text
    invite = client.post(
        "/api/v2/organization/invites",
        headers=_headers(admin),
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
            "displayName": "研究成员",
            "email": "intelligence-member@example.com",
            "password": "member-password",
        },
    )
    assert joined.status_code == 201, joined.text
    return joined.json()


def _seed_domain_facts(database: Path, session: dict[str, Any]) -> None:
    organization_id = str(session["organizationId"])
    membership_id = str(session["membershipId"])
    now = utc_now()
    with runtime_connection(database, "cloud") as connection:
        project_id = str(
            connection.execute(
                """
                SELECT project_id FROM work_projects
                WHERE organization_id = ?
                ORDER BY created_at, project_id
                LIMIT 1
                """,
                (organization_id,),
            ).fetchone()["project_id"]
        )
        connection.execute(
            """
            INSERT INTO intelligence_records (
                intelligence_id, organization_id, project_id, title, summary,
                source_url, record_kind, status, visibility_scope,
                created_by_membership_id, source_payload_json, version,
                created_at, updated_at
            ) VALUES (
                'intel_topic', ?, ?, '真实议题', '来自权威情报事实的摘要',
                'https://example.invalid/topic', 'topic_candidate', 'candidate',
                'organization', ?, '{"sentiment":"positive","sourceName":"权威来源"}',
                1, ?, ?
            )
            """,
            (organization_id, project_id, membership_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO intelligence_revisions (
                intelligence_revision_id, organization_id, intelligence_id,
                revision, title, summary, revised_by_membership_id, created_at
            ) VALUES (
                'intel_topic_rev_1', ?, 'intel_topic', 1, '真实议题',
                '来自权威情报事实的摘要', ?, ?
            )
            """,
            (organization_id, membership_id, now),
        )
        connection.execute(
            """
            INSERT INTO intelligence_records (
                intelligence_id, organization_id, project_id, title, summary,
                source_url, record_kind, status, visibility_scope,
                created_by_membership_id, source_payload_json, version,
                created_at, updated_at
            ) VALUES (
                'proposal_1', ?, ?, '真实提案', '以情报事实为来源的提案',
                '', 'proposal_draft', 'inbox', 'organization', ?,
                '{"taskDrafts":[{"title":"执行真实提案"}]}', 1, ?, ?
            )
            """,
            (organization_id, project_id, membership_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO intelligence_revisions (
                intelligence_revision_id, organization_id, intelligence_id,
                revision, title, summary, revised_by_membership_id, created_at
            ) VALUES (
                'proposal_rev_1', ?, 'proposal_1', 1, '真实提案',
                '以情报事实为来源的提案', ?, ?
            )
            """,
            (organization_id, membership_id, now),
        )
        connection.execute(
            """
            INSERT INTO growth_signals (
                growth_signal_id, organization_id, membership_id, source_type,
                source_id, week_label, raw_text, context_json, dedupe_key,
                lifecycle_state, version, created_at, updated_at
            ) VALUES (
                'growth_signal_1', ?, ?, 'task', 'task-source', '2026-W31',
                '完成了真实任务', '{}', 'growth-signal-dedupe-1',
                'candidate', 1, ?, ?
            )
            """,
            (organization_id, membership_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO growth_evidence (
                growth_evidence_id, organization_id, growth_signal_id,
                membership_id, ability_key, evidence_type, level, confidence,
                reason, task_id, validation_state, attributes_json, version,
                created_at, updated_at
            ) VALUES (
                'growth_evidence_1', ?, 'growth_signal_1', ?, '研究',
                'task_completion', 'practiced', 'high', '有权威任务证据',
                NULL, 'candidate', '{}', 1, ?, ?
            )
            """,
            (organization_id, membership_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO experience_quotes (
                experience_quote_id, organization_id, author_membership_id,
                quote_text, source_excerpt, source_type, source_id, category,
                lifecycle_state, contribution_score, created_at, updated_at, version
            ) VALUES (
                'quote_1', ?, ?, '先验证事实，再形成判断', '真实复盘摘录',
                'weekly_review', 'review-source', '方法论', 'active', 2.5, ?, ?, 1
            )
            """,
            (organization_id, membership_id, now, now),
        )
        connection.commit()


def _headers(session: dict[str, Any], key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {session['accessToken']}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _command(
    client: TestClient,
    session: dict[str, Any],
    *,
    path: str,
    payload: dict[str, Any],
    key: str,
    method: str = "POST",
):
    return client.post(
        "/api/v2/intelligence-growth/command",
        headers=_headers(session, key),
        json={
            "resourcePath": path,
            "method": method,
            "payload": payload,
        },
    )


def test_ui_domain_has_the_full_112_operation_denominator() -> None:
    assert len(router.routes) == 112
    assert len({(route.method, route.pattern) for route in router.routes}) == 112
    assert any(
        route.method == "GET" and route.pattern == r"data-center/shadow-summary"
        for route in router.routes
    )


def test_local_handlers_forward_queries_and_versioned_commands() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, Any]]] = []
            self.pin_depth = 0
            self.pin_count = 0

        @contextmanager
        def pinned_workspace_context(self):
            self.pin_count += 1
            self.pin_depth += 1
            try:
                yield
            finally:
                self.pin_depth -= 1

        def cloud_query(
            self,
            path: str,
            *,
            query: dict[str, str],
        ) -> dict[str, Any]:
            assert self.pin_depth == 1
            self.calls.append(("GET", path, dict(query)))
            if path.endswith("/version"):
                return {"expectedVersion": 7}
            return {"items": [{"id": "authority-item"}]}

        def cloud_command(
            self,
            method: str,
            path: str,
            *,
            payload: dict[str, Any],
            idempotency_key: str,
        ) -> dict[str, Any]:
            assert self.pin_depth == 1
            self.calls.append(
                (
                    method,
                    path,
                    {**payload, "idempotencyKey": idempotency_key},
                )
            )
            return {"ok": True}

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

    compatibility = Compatibility()
    query_result = router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="data-center/shadow-summary",
            query={},
            body={},
            idempotency_key="",
        ),
    )
    assert query_result["items"][0]["id"] == "authority-item"
    command_result = router.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="intelligence/items/intel_topic/follow",
            query={},
            body={},
            idempotency_key="follow-local-1",
        ),
    )
    assert command_result == {"ok": True}
    command_payload = compatibility.runtime.calls[-1][2]["payload"]
    assert command_payload["expectedVersion"] == 7
    strategy_result = router.dispatch(
        compatibility,
        UiRequest(
            method="PUT",
            path="intelligence/brand-mirror/strategy-extract",
            query={},
            body={
                "clientId": "project-a",
                "strategicObjective": "真实战略",
                "methodology": "真实方法",
            },
            idempotency_key="strategy-local-1",
        ),
    )
    assert strategy_result == {"ok": True}
    strategy_payload = compatibility.runtime.calls[-1][2]["payload"]
    assert strategy_payload["expectedVersion"] == 7
    assert compatibility.runtime.pin_count == 3


def test_local_refresh_without_any_project_is_explicitly_not_connected() -> None:
    class Runtime:
        @contextmanager
        def pinned_workspace_context(self):
            yield

        def cloud_query(
            self,
            _path: str,
            *,
            query: dict[str, str],
        ) -> list[dict[str, Any]]:
            assert query["resourcePath"] == "intelligence/work-objects"
            return []

    class Compatibility:
        runtime = Runtime()

    with pytest.raises(LocalRuntimeError) as raised:
        router.dispatch(
            Compatibility(),
            UiRequest(
                method="POST",
                path="intelligence/refresh",
                query={},
                body={
                    "scopeType": "all",
                    "scopeId": None,
                    "contentKind": "timely_intelligence",
                },
                idempotency_key="refresh-without-project",
            ),
        )
    assert raised.value.status_code == 409
    assert raised.value.code == "intelligence_capture_target_not_connected"


def test_local_public_capture_executes_search_and_commits_cloud_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[dict[str, Any]] = []

    class Operations:
        def __init__(self, _runtime: Any) -> None:
            pass

        def begin(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs["payload"]["specs"][0]["queryHash"]
            assert "测试项目" not in json.dumps(
                kwargs["payload"],
                ensure_ascii=False,
            )
            return {
                **kwargs["initial_result"],
                "operationId": "local-capture-operation",
                "sandboxId": "sandbox-a",
            }

        def update(self, **kwargs: Any) -> dict[str, Any]:
            updates.append(dict(kwargs))
            return dict(kwargs["result_patch"])

    class Runtime:
        def __init__(self) -> None:
            self.command_payload: dict[str, Any] | None = None

        @contextmanager
        def pinned_workspace_context(self):
            yield

        def cloud_query(
            self,
            _path: str,
            *,
            query: dict[str, str],
        ) -> Any:
            if query["resourcePath"] == "intelligence/work-objects":
                return [
                    {
                        "type": "client",
                        "id": "project-a",
                        "name": "测试项目",
                    }
                ]
            if query["resourcePath"] == "intelligence/items":
                return {
                    "items": [
                        {
                            "id": "intel-captured",
                            "contentKind": "timely_intelligence",
                            "title": "公开进展",
                        }
                    ]
                }
            raise AssertionError(query)

        def cloud_command(
            self,
            _method: str,
            _path: str,
            *,
            payload: dict[str, Any],
            idempotency_key: str,
        ) -> dict[str, Any]:
            assert idempotency_key == "refresh-local:authority-commit"
            self.command_payload = payload
            item = payload["payload"]["items"][0]
            return {
                "captureId": "local-capture-operation",
                "insertedCount": 1,
                "duplicateCount": 0,
                "items": [
                    {
                        "clientItemKey": item["clientItemKey"],
                        "status": "inserted",
                        "intelligenceId": "intel-captured",
                    }
                ],
            }

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

    monkeypatch.setattr(
        local_intelligence_growth,
        "LocalPlatformOperationRepository",
        Operations,
    )
    monkeypatch.setattr(
        local_intelligence_growth,
        "capture_public_web",
        lambda *_args, **_kwargs: [
            PublicCaptureItem(
                title="公开进展",
                summary="公开搜索摘要",
                source_name="example.org",
                source_url="https://example.org/update",
                captured_at=utc_now(),
                published_at=None,
                sentiment="neutral",
                sentiment_reason="保持中性",
                content_hash="content-hash",
            )
        ],
    )
    compatibility = Compatibility()
    result = router.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="intelligence/refresh",
            query={},
            body={
                "scopeType": "client",
                "scopeId": "project-a",
                "contentKind": "timely_intelligence",
            },
            idempotency_key="refresh-local",
        ),
    )

    assert result["status"] == "completed"
    assert result["totals"]["candidateCount"] == 1
    assert result["totals"]["promotedCount"] == 1
    assert result["externalCollectionExecuted"] is True
    assert result["sourceBodyStored"] is False
    assert compatibility.runtime.command_payload is not None
    command_item = compatibility.runtime.command_payload["payload"]["items"][0]
    assert command_item["title"] == "公开进展"
    assert command_item["summary"] == "公开搜索摘要"
    assert "body" not in command_item
    assert updates[-1]["state"] == "completed"
    stored_output = updates[-1]["result_patch"]["output"]
    assert "公开进展" not in json.dumps(stored_output, ensure_ascii=False)
    assert "公开搜索摘要" not in json.dumps(stored_output, ensure_ascii=False)


def test_private_chat_replays_local_idempotent_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, Any] = {}

    class Operations:
        def __init__(self, _runtime: Any) -> None:
            pass

        def begin(self, **kwargs: Any) -> dict[str, Any]:
            if stored:
                return {**stored, "idempotentReplay": True}
            return {
                **kwargs["initial_result"],
                "operationId": "operation-chat-1",
                "sandboxId": "sandbox-a",
            }

        def update(self, **kwargs: Any) -> dict[str, Any]:
            stored.update(
                {
                    **kwargs["result_patch"],
                    "state": kwargs["state"],
                    "operationId": kwargs["operation_id"],
                    "sandboxId": kwargs["captured_sandbox_id"],
                }
            )
            return dict(stored)

    class Runtime:
        def __init__(self) -> None:
            self.completions = 0

        def private_ai_completion(self, **_: Any) -> dict[str, Any]:
            self.completions += 1
            return {
                "content": "只执行一次的回答",
                "modelName": "local-model",
                "sourceScope": "organization",
            }

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

    monkeypatch.setattr(
        local_intelligence_growth,
        "LocalPlatformOperationRepository",
        Operations,
    )
    compatibility = Compatibility()
    request = UiRequest(
        method="POST",
        path="intelligence/items/intel_topic/chat",
        query={},
        body={"question": "如何判断？", "history": []},
        idempotency_key="private-chat-1",
    )
    first = _private_chat(
        compatibility,
        object_kind="item",
        object_id="intel_topic",
        title="议题",
        summary="摘要",
        request=request,
    )
    second = _private_chat(
        compatibility,
        object_kind="item",
        object_id="intel_topic",
        title="议题",
        summary="摘要",
        request=request,
    )
    assert compatibility.runtime.completions == 1
    assert second == first


@pytest.mark.parametrize(
    ("error_code", "status_code", "expected_state"),
    (
        ("organization_ai_secret_missing", 409, "blocked"),
        ("local_ai_profile_not_ready", 409, "blocked"),
        ("ai_request_rejected", 401, "blocked"),
        ("ai_routes_exhausted", 503, "failed_retryable"),
    ),
)
def test_private_chat_maps_configuration_and_runtime_failures_to_five_states(
    monkeypatch: pytest.MonkeyPatch,
    error_code: str,
    status_code: int,
    expected_state: str,
) -> None:
    updates: list[dict[str, Any]] = []

    class Operations:
        def __init__(self, _runtime: Any) -> None:
            pass

        def begin(self, **kwargs: Any) -> dict[str, Any]:
            return {
                **kwargs["initial_result"],
                "operationId": "operation-chat-failure",
                "sandboxId": "sandbox-a",
            }

        def update(self, **kwargs: Any) -> dict[str, Any]:
            updates.append(dict(kwargs))
            return dict(kwargs)

    class Runtime:
        def private_ai_completion(self, **_: Any) -> dict[str, Any]:
            raise LocalRuntimeError(status_code, error_code, "模型不可用")

    class Compatibility:
        runtime = Runtime()

    monkeypatch.setattr(
        local_intelligence_growth,
        "LocalPlatformOperationRepository",
        Operations,
    )
    with pytest.raises(LocalRuntimeError) as raised:
        _private_chat(
            Compatibility(),
            object_kind="item",
            object_id="intel_topic",
            title="议题",
            summary="摘要",
            request=UiRequest(
                method="POST",
                path="intelligence/items/intel_topic/chat",
                query={},
                body={"question": "如何判断？", "history": []},
                idempotency_key=f"private-chat-{error_code}",
            ),
        )
    assert raised.value.code == error_code
    assert updates[-1]["state"] == expected_state


def test_admin_only_intelligence_operations_reject_members(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        member = _join_member(client, admin)
        _seed_domain_facts(database, admin)
        for index, (path, payload) in enumerate(
            (
                (
                    "approvals/proposal_1/approve",
                    {"expectedVersion": 1},
                ),
                (
                    "approvals/decide",
                    {
                        "approvalId": "proposal_1",
                        "decision": "approve",
                        "expectedVersion": 1,
                    },
                ),
                (
                    "proposals/proposal_1/approve",
                    {"expectedVersion": 1},
                ),
                ("intelligence/profiles/run-due", {}),
            )
        ):
            denied = _command(
                client,
                member,
                path=path,
                payload=payload,
                key=f"member-admin-only-{index}",
            )
            assert denied.status_code == 403, denied.text
            assert denied.json()["error"]["code"] == (
                "organization_admin_required"
            )


def test_candidate_task_promotion_has_replayable_per_item_receipt(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)
        prepare = _command(
            client,
            session,
            path="topics/candidates/intel_topic/promote-tasks",
            payload={
                "phase": "prepare",
                "expectedVersion": 1,
                "tasks": [
                    {"title": "任务一", "draftHash": "hash-1"},
                    {"title": "任务二", "draftHash": "hash-2"},
                ],
            },
            key="candidate-promote-prepare",
        )
        assert prepare.status_code == 200, prepare.text
        assert prepare.json()["status"] == "accepted"
        assert prepare.json()["atomicityMode"] == "per_item"
        replayed_prepare = _command(
            client,
            session,
            path="topics/candidates/intel_topic/promote-tasks",
            payload={
                "phase": "prepare",
                "expectedVersion": 1,
                "tasks": [
                    {"title": "任务一", "draftHash": "hash-1"},
                    {"title": "任务二", "draftHash": "hash-2"},
                ],
            },
            key="candidate-promote-prepare",
        )
        assert replayed_prepare.json() == prepare.json()

        finalized = _command(
            client,
            session,
            path="topics/candidates/intel_topic/promote-tasks",
            payload={
                "phase": "finalize",
                "bulkOperationId": prepare.json()["bulkOperationId"],
                "expectedVersion": 1,
                "itemResults": [
                    {
                        "itemKey": "0",
                        "status": "committed",
                        "taskId": "task-authority-1",
                    },
                    {
                        "itemKey": "1",
                        "status": "failed",
                        "errorCode": "task_write_failed",
                    },
                ],
            },
            key="candidate-promote-finalize",
        )
        assert finalized.status_code == 200, finalized.text
        assert finalized.json()["status"] == "partial"
        assert finalized.json()["succeeded"] == 1
        assert finalized.json()["failed"] == 1
        replayed_finalize = _command(
            client,
            session,
            path="topics/candidates/intel_topic/promote-tasks",
            payload={
                "phase": "finalize",
                "bulkOperationId": prepare.json()["bulkOperationId"],
                "expectedVersion": 1,
                "itemResults": [
                    {
                        "itemKey": "0",
                        "status": "committed",
                        "taskId": "task-authority-1",
                    },
                    {
                        "itemKey": "1",
                        "status": "failed",
                        "errorCode": "task_write_failed",
                    },
                ],
            },
            key="candidate-promote-finalize",
        )
        assert replayed_finalize.json() == finalized.json()

    with runtime_connection(database, "cloud", read_only=True) as connection:
        row = connection.execute(
            """
            SELECT atomicity_mode, status, version
            FROM bulk_operations WHERE bulk_operation_id = ?
            """,
            (prepare.json()["bulkOperationId"],),
        ).fetchone()
        assert tuple(row) == ("per_item", "partial", 2)
        items = connection.execute(
            """
            SELECT item_key, commit_result, conflict_code
            FROM bulk_operation_items WHERE bulk_operation_id = ?
            ORDER BY item_key
            """,
            (prepare.json()["bulkOperationId"],),
        ).fetchall()
        assert [tuple(item) for item in items] == [
            ("0", "committed", None),
            ("1", "failed", "task_write_failed"),
        ]


def test_growth_cards_respect_self_department_and_organization_visibility(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        member = _join_member(client, admin)
        now = utc_now()
        with runtime_connection(database, "cloud") as connection:
            department_id = str(
                connection.execute(
                    """
                    SELECT department_id FROM department_memberships
                    WHERE membership_id = ?
                    """,
                    (member["membershipId"],),
                ).fetchone()["department_id"]
            )
            connection.execute(
                """
                INSERT INTO department_memberships (
                    department_membership_id, organization_id, department_id,
                    membership_id, is_department_lead, status, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 'active', 1, ?, ?)
                """,
                (
                    new_id(),
                    admin["organizationId"],
                    department_id,
                    admin["membershipId"],
                    now,
                    now,
                ),
            )
            for suffix, visibility, lifecycle in (
                ("self", "self", "active"),
                ("department", "department", "active"),
                ("organization", "organization", "active"),
                ("archived", "organization", "archived"),
            ):
                connection.execute(
                    """
                    INSERT INTO growth_cards (
                        growth_card_id, organization_id, membership_id,
                        weekly_review_id, content_domain, visibility_scope,
                        summary_json, suggestions_json, lifecycle_state,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, NULL, 'personal', ?, '{}', '[]', ?, 1, ?, ?)
                    """,
                    (
                        f"card-{suffix}",
                        admin["organizationId"],
                        member["membershipId"],
                        visibility,
                        lifecycle,
                        now,
                        now,
                    ),
                )
            connection.commit()
        domain = IntelligenceGrowthRepository(client.app.state.repository)
        admin_identity = client.app.state.repository.session_from_access(
            admin["accessToken"]
        )
        member_identity = client.app.state.repository.session_from_access(
            member["accessToken"]
        )
        assert {item["id"] for item in domain._growth_cards(admin_identity)} == {
            "card-department",
            "card-organization",
        }
        assert {item["id"] for item in domain._growth_cards(member_identity)} == {
            "card-self",
            "card-department",
            "card-organization",
        }


def test_personal_growth_projection_never_counts_other_members_evidence(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        member = _join_member(client, admin)
        now = utc_now()
        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                INSERT INTO growth_signals (
                    growth_signal_id, organization_id, membership_id,
                    source_type, source_id, week_label, raw_text, context_json,
                    dedupe_key, lifecycle_state, version, created_at, updated_at
                ) VALUES (
                    'member-growth-signal', ?, ?, 'task', 'member-task',
                    '2026-W31', '成员完成任务', '{}', 'member-growth-dedupe',
                    'confirmed', 1, ?, ?
                )
                """,
                (admin["organizationId"], member["membershipId"], now, now),
            )
            connection.execute(
                """
                INSERT INTO growth_evidence (
                    growth_evidence_id, organization_id, growth_signal_id,
                    membership_id, ability_key, evidence_type, level,
                    confidence, reason, task_id, validation_state,
                    attributes_json, version, created_at, updated_at
                ) VALUES (
                    'member-growth-evidence', ?, 'member-growth-signal', ?,
                    '研究', 'task_completion', 'practiced', 'high',
                    '成员个人证据', NULL, 'confirmed', '{}', 1, ?, ?
                )
                """,
                (admin["organizationId"], member["membershipId"], now, now),
            )
            connection.commit()

        admin_overview = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(admin),
            params={"resourcePath": "growth/overview"},
        )
        member_overview = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(member),
            params={"resourcePath": "growth/overview"},
        )
        assert admin_overview.status_code == 200, admin_overview.text
        assert member_overview.status_code == 200, member_overview.text
        assert admin_overview.json()["userId"] == admin["membershipId"]
        assert admin_overview.json()["totalXp"] == 0
        assert member_overview.json()["userId"] == member["membershipId"]
        assert member_overview.json()["totalXp"] == 1
        assert member_overview.json()["recentEntries"][0]["userId"] == (
            member["membershipId"]
        )


def test_all_112_routes_reach_a_cloud_operation_or_declared_501(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)
        blocked: set[tuple[str, str]] = set()
        for index, route in enumerate(router.routes):
            path = route.pattern.replace("[^/]+", "missing")
            if route.method == "GET":
                response = client.get(
                    "/api/v2/intelligence-growth/query",
                    headers=_headers(session),
                    params={"resourcePath": path},
                )
            else:
                response = _command(
                    client,
                    session,
                    path=path,
                    method=route.method,
                    payload={},
                    key=f"denominator-operation-{index}",
                )
            body = response.json()
            error = (body.get("error") or {}) if isinstance(body, dict) else {}
            assert error.get("code") not in {
                "intelligence_growth_query_unknown",
                "intelligence_growth_command_unknown",
            }, (route.method, route.pattern, response.text)
            if response.status_code == 501:
                blocked.add((route.method, route.pattern))
        assert blocked == BLOCKED_ROUTE_KEYS


def test_radar_operational_and_rollout_gaps_use_existing_authorities(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)
        with runtime_connection(database, "cloud", read_only=True) as connection:
            project_id = str(
                connection.execute(
                    """
                    SELECT project_id FROM work_projects
                    WHERE organization_id = ?
                    ORDER BY created_at LIMIT 1
                    """,
                    (session["organizationId"],),
                ).fetchone()["project_id"]
            )
        radar = _command(
            client,
            session,
            path="topics/radars",
            method="POST",
            payload={
                "title": "公益政策雷达",
                "prompt": "关注公益政策变化",
                "timeRange": "7d",
                "preferredSources": [
                    {"url": "https://example.org", "label": "示例来源"}
                ],
            },
            key="radar-create",
        )
        assert radar.status_code == 200, radar.text
        radar_id = radar.json()["id"]
        topics = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={"resourcePath": "topics"},
        )
        configured = next(
            item for item in topics.json()["radars"] if item["id"] == radar_id
        )
        assert configured["derived"] is False
        assert configured["preferredSources"][0]["label"] == "示例来源"
        updated = _command(
            client,
            session,
            path=f"topics/radars/{radar_id}",
            method="PUT",
            payload={
                "expectedVersion": 1,
                "title": "公益政策与资金雷达",
                "prompt": "关注政策和资金变化",
                "timeRange": "30d",
                "preferredSources": [],
            },
            key="radar-update",
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == 2
        capture = _command(
            client,
            session,
            path=f"topics/radars/{radar_id}/capture",
            payload={},
            key="radar-capture",
        )
        assert capture.status_code == 409
        assert (
            capture.json()["error"]["code"]
            == "local_public_capture_required"
        )
        schema = _command(
            client,
            session,
            path="data-center/schema/ensure",
            payload={},
            key="schema-verify",
        )
        assert schema.status_code == 200, schema.text
        assert schema.json()["ddlExecuted"] is False
        assert schema.json()["missingTables"] == []
        team_sync = _command(
            client,
            session,
            path="data-center/team-sync/run-once",
            payload={},
            key="team-sync",
        )
        assert team_sync.status_code == 200, team_sync.text
        assert team_sync.json()["processedCount"] == 0
        assert team_sync.json()["verifiedMemberCount"] == 1
        assert team_sync.json()["state"] == "verified"
        assert team_sync.json()["externalDirectorySyncExecuted"] is False
        rollout = _command(
            client,
            session,
            path="data-center/kernel-primary-rollout/start",
            payload={
                "stage": "stage_1_client",
                "clientIds": [project_id],
                "note": "严格主链验证",
            },
            key="rollout-start",
        )
        assert rollout.status_code == 200, rollout.text
        rollout_id = rollout.json()["id"]
        completed = _command(
            client,
            session,
            path=f"data-center/kernel-primary-rollout/{rollout_id}/complete",
            payload={"expectedVersion": 1, "verdict": "pass"},
            key="rollout-complete",
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        rollouts = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={"resourcePath": "data-center/kernel-primary-rollout"},
        )
        assert rollouts.status_code == 200
        authority_run = next(
            item for item in rollouts.json() if item["id"] == rollout_id
        )
        assert authority_run["clientIds"] == [project_id]
        assert authority_run["version"] == 2
    with runtime_connection(database, "cloud", read_only=True) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM intelligence_records
            WHERE record_kind = 'topic_radar'
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM organization_plans
            WHERE period_label = 'kernel_primary_rollout'
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM reconciliation_runs
            WHERE registry_state_id = 'team_sync'
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM command_envelopes
            WHERE idempotency_key IN (
              'radar-create', 'radar-update', 'schema-verify',
              'team-sync', 'rollout-start', 'rollout-complete'
            )
            """
        ).fetchone()[0] == 6


def test_external_capture_commits_metadata_to_authority_and_filters_views(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        snapshot = client.get(
            "/api/v2/business/snapshot",
            headers=_headers(session),
        )
        project_id = str(snapshot.json()["projects"][0]["projectId"])
        base_item = {
            "clientItemKey": "sentiment:0",
            "title": "项目发布阶段进展",
            "summary": "公开页面摘要显示项目已经启动。",
            "sourceName": "example.org",
            "sourceUrl": "https://example.org/project-update",
            "capturedAt": utc_now(),
            "publishedAt": None,
            "sentiment": "positive",
            "sentimentReason": "公开摘要命中进展词：启动",
            "contentKind": "public_opinion",
            "recordKind": "public_opinion_capture",
            "projectId": project_id,
            "queryHash": "a" * 64,
        }
        first = _command(
            client,
            session,
            path="intelligence/external-capture/commit",
            payload={"captureId": "capture-1", "items": [base_item]},
            key="external-capture-1",
        )
        assert first.status_code == 200, first.text
        assert first.json()["insertedCount"] == 1
        assert first.json()["duplicateCount"] == 0
        intelligence_id = first.json()["items"][0]["intelligenceId"]

        replay = _command(
            client,
            session,
            path="intelligence/external-capture/commit",
            payload={"captureId": "capture-1", "items": [base_item]},
            key="external-capture-1",
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == first.json()

        duplicate = _command(
            client,
            session,
            path="intelligence/external-capture/commit",
            payload={"captureId": "capture-2", "items": [base_item]},
            key="external-capture-2",
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["insertedCount"] == 0
        assert duplicate.json()["duplicateCount"] == 1
        assert duplicate.json()["items"][0]["intelligenceId"] == intelligence_id

        timely = _command(
            client,
            session,
            path="intelligence/external-capture/commit",
            payload={
                "captureId": "capture-3",
                "items": [
                    {
                        **base_item,
                        "clientItemKey": "timely:0",
                        "sourceUrl": "https://example.org/policy-update",
                        "contentKind": "timely_intelligence",
                        "recordKind": "timely_external_capture",
                    }
                ],
            },
            key="external-capture-3",
        )
        assert timely.status_code == 200, timely.text
        assert timely.json()["insertedCount"] == 1

        public_items = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={
                "resourcePath": "intelligence/items",
                "contentKind": "public_opinion",
                "workObjectId": project_id,
            },
        )
        assert public_items.status_code == 200, public_items.text
        assert [item["id"] for item in public_items.json()["items"]] == [
            intelligence_id
        ]
        sentiment = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={
                "resourcePath": "intelligence/sentiment/items",
                "clientId": project_id,
            },
        )
        assert sentiment.status_code == 200, sentiment.text
        assert [item["id"] for item in sentiment.json()["items"]] == [
            intelligence_id
        ]

    with runtime_connection(database, "cloud", read_only=True) as connection:
        audit = connection.execute(
            """
            SELECT summary_json FROM audit_events
            WHERE resource_id = 'capture-1'
            """
        ).fetchone()
        outbox = connection.execute(
            """
            SELECT payload_json FROM delivery_outbox
            WHERE aggregate_id = 'capture-1'
            """
        ).fetchone()
        assert audit is not None
        assert outbox is not None
        assert "项目发布阶段进展" not in str(audit["summary_json"])
        assert "项目发布阶段进展" not in str(outbox["payload_json"])
        assert int(
            connection.execute(
                """
                SELECT COUNT(*) FROM intelligence_records
                WHERE intelligence_id = ?
                """,
                (intelligence_id,),
            ).fetchone()[0]
        ) == 1


def test_authority_views_are_rebuilt_from_frozen_tables(tmp_path: Path) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)
        headers = _headers(session)

        topics = client.get(
            "/api/v2/intelligence-growth/query",
            headers=headers,
            params={"resourcePath": "topics"},
        )
        assert topics.status_code == 200, topics.text
        assert [item["id"] for item in topics.json()["candidates"]] == ["intel_topic"]
        assert topics.json()["authoritySource"] == (
            "intelligence_records/intelligence_revisions"
        )

        growth = client.get(
            "/api/v2/intelligence-growth/query",
            headers=headers,
            params={"resourcePath": "growth/workbench"},
        )
        assert growth.status_code == 200, growth.text
        assert growth.json()["reasoningTrace"]["evidenceRefs"] == [
            "growth_evidence_1"
        ]
        assert growth.json()["genericLessons"][0]["id"] == "quote_1"

        overview = client.get(
            "/api/v2/intelligence-growth/query",
            headers=headers,
            params={"resourcePath": "growth/overview"},
        )
        assert overview.status_code == 200, overview.text
        assert overview.json()["pendingCaptures"][0]["id"] == "growth_signal_1"
        assert overview.json()["sourceCoverage"]["taskSignals"] == 1

        proposals = client.get(
            "/api/v2/intelligence-growth/query",
            headers=headers,
            params={"resourcePath": "proposals"},
        )
        assert proposals.status_code == 200, proposals.text
        assert proposals.json()[0]["proposalId"] == "proposal_1"
        assert proposals.json()[0]["taskDrafts"][0]["title"] == (
            "执行真实提案"
        )

        shadow = client.get(
            "/api/v2/intelligence-growth/query",
            headers=headers,
            params={"resourcePath": "data-center/shadow-summary"},
        )
        assert shadow.status_code == 200, shadow.text
        assert shadow.json()["total"] == 0
        assert shadow.json()["failures"] == 0


def test_intelligence_command_is_cas_idempotent_audited_and_outboxed(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)

        first = _command(
            client,
            session,
            path="intelligence/items/intel_topic/follow",
            payload={"expectedVersion": 1},
            key="follow-intelligence-1",
        )
        assert first.status_code == 200, first.text
        assert first.json()["status"] == "accepted"
        assert first.json()["version"] == 2

        repeated = _command(
            client,
            session,
            path="intelligence/items/intel_topic/follow",
            payload={"expectedVersion": 1},
            key="follow-intelligence-1",
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json() == first.json()

        conflict = _command(
            client,
            session,
            path="intelligence/items/intel_topic/follow",
            payload={"expectedVersion": 2},
            key="follow-intelligence-1",
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency_payload_conflict"

        stale = _command(
            client,
            session,
            path="intelligence/items/intel_topic/dismiss",
            payload={"expectedVersion": 1},
            key="dismiss-stale-1",
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "version_conflict"

    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM command_envelopes
            WHERE command_type = 'intelligence.items.intel_topic.follow.post'
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM intelligence_revisions
            WHERE intelligence_id = 'intel_topic'
            """
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*) FROM audit_events
            WHERE resource_id = 'intel_topic'
              AND action = 'intelligence.items.intel_topic.follow.post'
            """
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM delivery_outbox
            WHERE aggregate_id = 'intel_topic'
              AND event_type = 'intelligence.accepted'
            """
        ).fetchone()[0] == 1


def test_growth_confirmation_reaction_and_reconciliation_snapshot(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)

        confirmed = _command(
            client,
            session,
            path="growth/pending-captures/growth_signal_1/state",
            payload={"state": "confirmed", "expectedVersion": 1},
            key="growth-confirm-1",
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["state"] == "confirmed"

        liked = _command(
            client,
            session,
            path="growth/experience-wall/quote_1/like",
            payload={},
            key="quote-like-1",
        )
        assert liked.status_code == 200, liked.text
        assert liked.json()["active"] is True
        assert liked.json()["count"] == 1

        snapshot = _command(
            client,
            session,
            path="data-center/evidence-quality/snapshots",
            payload={},
            key="evidence-snapshot-1",
        )
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["total"] == 1
        assert snapshot.json()["statusCounts"] == {"candidate": 1}

        snapshots = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={"resourcePath": "data-center/evidence-quality/snapshots"},
        )
        assert snapshots.status_code == 200, snapshots.text
        assert snapshots.json()[0]["labelCounts"] == {"candidate": 1}

    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT lifecycle_state FROM growth_signals
            WHERE growth_signal_id = 'growth_signal_1'
            """
        ).fetchone()[0] == "confirmed"
        assert connection.execute(
            """
            SELECT COUNT(*) FROM experience_reactions
            WHERE experience_quote_id = 'quote_1' AND reaction_type = 'like'
            """
        ).fetchone()[0] == 1


def test_bulk_approval_and_execution_ticket_use_operation_authority(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)

        approved = _command(
            client,
            session,
            path="proposals/batch-approve",
            payload={
                "proposalIds": ["proposal_1"],
                "itemVersions": {"proposal_1": 1},
            },
            key="proposal-bulk-approve-1",
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "committed"
        assert approved.json()["atomicityMode"] == "all_or_nothing"
        assert approved.json()["total"] == 1
        assert approved.json()["succeeded"] == 1
        assert approved.json()["items"] == [
            {
                "id": "proposal_1",
                "beforeVersion": 1,
                "afterVersion": 2,
                "status": "accepted",
            }
        ]

        ticket = _command(
            client,
            session,
            path="proposals/proposal_1/execution-ticket",
            payload={"expectedVersion": 2},
            key="proposal-ticket-1",
        )
        assert ticket.status_code == 200, ticket.text
        ticket_id = ticket.json()["executionTicket"]["id"]
        assert ticket.json()["executionTicket"]["status"] == "pending"

        executed_ticket = _command(
            client,
            session,
            path=f"execution-tickets/{ticket_id}/execute",
            payload={},
            key="proposal-ticket-execute-1",
        )
        assert executed_ticket.status_code == 200, executed_ticket.text
        assert executed_ticket.json()["executionTicket"]["status"] == "executed"
        created_task_ids = executed_ticket.json()["executionTicket"]["result"][
            "createdTaskIds"
        ]
        assert len(created_task_ids) == 1
        replayed_ticket = _command(
            client,
            session,
            path=f"execution-tickets/{ticket_id}/execute",
            payload={},
            key="proposal-ticket-execute-1",
        )
        assert replayed_ticket.status_code == 200
        assert replayed_ticket.json() == executed_ticket.json()

        direct = _command(
            client,
            session,
            path="proposals/proposal_1/execute",
            payload={"expectedVersion": 3},
            key="proposal-direct-execute-1",
        )
        assert direct.status_code == 409, direct.text
        assert direct.json()["error"]["code"] == (
            "proposal_already_executed"
        )

        tickets = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={"resourcePath": "execution-tickets"},
        )
        assert tickets.status_code == 200, tickets.text
        assert {item["id"] for item in tickets.json()} == {ticket_id}
        assert tickets.json()[0]["status"] == "executed"
        assert tickets.json()[0]["result"]["createdTaskIds"] == created_task_ids

    with runtime_connection(database, "cloud") as connection:
        bulk_id = approved.json()["bulkOperationId"]
        assert connection.execute(
            """
            SELECT status FROM bulk_operations
            WHERE bulk_operation_id = ?
            """,
            (bulk_id,),
        ).fetchone()[0] == "committed"
        assert connection.execute(
            """
            SELECT commit_result FROM bulk_operation_items
            WHERE bulk_operation_id = ? AND item_key = 'proposal_1'
            """,
            (bulk_id,),
        ).fetchone()[0] == "committed"
        assert connection.execute(
            """
            SELECT COUNT(*) FROM operation_attempts
            WHERE command_id = ?
            """,
            (ticket_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM task_records
            WHERE organization_id = ? AND source_type = 'proposal_execution'
              AND source_id = ?
            """,
            (session["organizationId"], ticket_id),
        ).fetchone()[0] == 1
        proposal_payload = json.loads(
            connection.execute(
                """
                SELECT source_payload_json FROM intelligence_records
                WHERE intelligence_id = 'proposal_1'
                """
            ).fetchone()[0]
        )
        assert proposal_payload["executionTicketId"] == ticket_id


def test_direct_proposal_execution_creates_tasks_once(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)
        approved = _command(
            client,
            session,
            path="proposals/proposal_1/approve",
            payload={"expectedVersion": 1},
            key="proposal-direct-approve-1",
        )
        assert approved.status_code == 200, approved.text
        executed = _command(
            client,
            session,
            path="proposals/proposal_1/execute",
            payload={"expectedVersion": 2},
            key="proposal-direct-execute-1",
        )
        assert executed.status_code == 200, executed.text
        ticket = executed.json()["executionTicket"]
        assert executed.json()["proposal"]["version"] == 3
        assert executed.json()["proposal"]["executionTicketId"] == ticket["id"]
        assert ticket["status"] == "executed"
        assert len(ticket["result"]["createdTaskIds"]) == 1
        replay = _command(
            client,
            session,
            path="proposals/proposal_1/execute",
            payload={"expectedVersion": 2},
            key="proposal-direct-execute-1",
        )
        assert replay.status_code == 200
        assert replay.json() == executed.json()
    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM task_records
            WHERE organization_id = ? AND source_type = 'proposal_execution'
            """,
            (session["organizationId"],),
        ).fetchone()[0] == 1


def test_strategy_extract_save_uses_narrative_cas_audit_and_outbox(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)
        with runtime_connection(database, "cloud", read_only=True) as connection:
            project_id = str(
                connection.execute(
                    """
                    SELECT project_id FROM work_projects
                    WHERE organization_id = ?
                    ORDER BY created_at, project_id LIMIT 1
                    """,
                    (session["organizationId"],),
                ).fetchone()["project_id"]
            )

        missing_project = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={
                "resourcePath": "intelligence/brand-mirror/strategy-extract",
                "clientId": "project_missing",
            },
        )
        assert missing_project.status_code == 404
        assert missing_project.json()["error"]["code"] == (
            "strategy_extract_project_missing"
        )

        missing_project_save = _command(
            client,
            session,
            path="intelligence/brand-mirror/strategy-extract",
            method="PUT",
            payload={
                "clientId": "project_missing",
                "strategicObjective": "不得创建孤立提炼",
                "methodology": "必须先验证项目",
                "expectedVersion": 0,
            },
            key="strategy-extract-project-missing",
        )
        assert missing_project_save.status_code == 404
        assert missing_project_save.json()["error"]["code"] == (
            "strategy_extract_project_missing"
        )
        missing_project_derive = _command(
            client,
            session,
            path="intelligence/brand-mirror/strategy-extract",
            method="POST",
            payload={"clientId": "project_missing"},
            key="strategy-extract-project-missing-derive",
        )
        assert missing_project_derive.status_code == 404
        assert missing_project_derive.json()["error"]["code"] == (
            "strategy_extract_project_missing"
        )

        second_project = client.post(
            "/api/v2/domain/project-materials/projects",
            headers=_headers(session, "strategy-second-project"),
            json={"name": "第二项目"},
        )
        assert second_project.status_code == 201, second_project.text
        second_project_id = second_project.json()["project"]["projectId"]

        saved = _command(
            client,
            session,
            path="intelligence/brand-mirror/strategy-extract",
            method="PUT",
            payload={
                "clientId": project_id,
                "strategicObjective": "成为可信赖的研究伙伴",
                "methodology": "先核验证据，再形成战略行动",
                "expectedVersion": 0,
            },
            key="strategy-extract-save-1",
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["extract"]["clientId"] == project_id
        assert saved.json()["extract"]["confirmedBy"] == "管理员"

        read_back = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={
                "resourcePath": "intelligence/brand-mirror/strategy-extract",
                "clientId": project_id,
            },
        )
        assert read_back.status_code == 200, read_back.text
        assert read_back.json()["extract"]["methodology"] == (
            "先核验证据，再形成战略行动"
        )
        isolated_second = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={
                "resourcePath": "intelligence/brand-mirror/strategy-extract",
                "clientId": second_project_id,
            },
        )
        assert isolated_second.status_code == 200, isolated_second.text
        assert isolated_second.json()["extract"].get("methodology") != (
            "先核验证据，再形成战略行动"
        )

        version = client.get(
            "/api/v2/intelligence-growth/version",
            headers=_headers(session),
            params={
                "resourcePath": "intelligence/brand-mirror/strategy-extract",
                "clientId": project_id,
            },
        )
        assert version.status_code == 200, version.text
        assert version.json()["expectedVersion"] == 1

        stale = _command(
            client,
            session,
            path="intelligence/brand-mirror/strategy-extract",
            method="PUT",
            payload={
                "clientId": project_id,
                "strategicObjective": "冲突版本",
                "methodology": "冲突版本",
                "expectedVersion": 0,
            },
            key="strategy-extract-stale-1",
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "version_conflict"

    with runtime_connection(database, "cloud") as connection:
        narrative = connection.execute(
            """
            SELECT narrative_output_id, latest_version, version
            FROM narrative_outputs
            WHERE organization_id = ? AND output_kind = 'strategy_report'
            """,
            (session["organizationId"],),
        ).fetchone()
        assert narrative is not None
        assert (narrative["latest_version"], narrative["version"]) == (1, 1)
        assert connection.execute(
            """
            SELECT COUNT(*) FROM narrative_output_versions
            WHERE narrative_output_id = ?
            """,
            (narrative["narrative_output_id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM audit_events
            WHERE resource_id = ? AND action =
              'intelligence.brand-mirror.strategy-extract.put'
            """,
            (narrative["narrative_output_id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM delivery_outbox
            WHERE aggregate_id = ? AND event_type = 'strategy_extract.saved'
            """,
            (narrative["narrative_output_id"],),
        ).fetchone()[0] == 1


def test_brand_audit_get_and_recompute_match_renderer_contract(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)
        now = utc_now()
        with runtime_connection(database, "cloud") as connection:
            project_id = str(
                connection.execute(
                    """
                    SELECT project_id FROM work_projects
                    WHERE organization_id = ?
                    ORDER BY created_at, project_id LIMIT 1
                    """,
                    (session["organizationId"],),
                ).fetchone()["project_id"]
            )
            connection.execute(
                """
                INSERT INTO intelligence_records (
                    intelligence_id, organization_id, project_id, title,
                    summary, source_url, record_kind, status,
                    visibility_scope, created_by_membership_id,
                    source_payload_json, version, created_at, updated_at
                ) VALUES (
                    'sentiment_contract_1', ?, ?, '公开正向评价',
                    '公开渠道认可项目专业性', 'https://example.invalid/public',
                    'public_sentiment', 'accepted', 'organization', ?,
                    '{"contentKind":"public_opinion","sentiment":"positive","sourceName":"公开来源"}',
                    1, ?, ?
                )
                """,
                (
                    session["organizationId"],
                    project_id,
                    session["membershipId"],
                    now,
                    now,
                ),
            )
            connection.commit()

        loaded = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={
                "resourcePath": "intelligence/sentiment/audit",
                "clientId": project_id,
            },
        )
        assert loaded.status_code == 200, loaded.text
        assert set(loaded.json()) == {"audit", "recomputeNote"}
        audit = loaded.json()["audit"]
        assert set(audit) == {
            "id",
            "scopeType",
            "scopeId",
            "headline",
            "narrativeMd",
            "tensions",
            "recommendations",
            "contentAngles",
            "evidenceThemeIds",
            "computedAt",
            "expiresAt",
        }
        assert audit["scopeType"] == "client"
        assert audit["scopeId"] == project_id
        assert audit["evidenceThemeIds"] == ["kind:public_sentiment"]

        recomputed = _command(
            client,
            session,
            path="intelligence/sentiment/audit/recompute",
            payload={"clientId": project_id},
            key="brand-audit-contract-recompute",
        )
        assert recomputed.status_code == 200, recomputed.text
        assert set(recomputed.json()) == {"ok", "audit"}
        assert recomputed.json()["ok"] is True
        assert recomputed.json()["audit"]["id"] == audit["id"]

        isolated = client.post(
            "/api/v2/domain/project-materials/projects",
            headers=_headers(session, "brand-audit-isolated-project"),
            json={"name": "无舆情项目"},
        )
        assert isolated.status_code == 201, isolated.text
        isolated_loaded = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={
                "resourcePath": "intelligence/sentiment/audit",
                "clientId": isolated.json()["project"]["projectId"],
            },
        )
        assert isolated_loaded.status_code == 200, isolated_loaded.text
        assert set(isolated_loaded.json()) == {"audit", "recomputeNote"}
        assert isolated_loaded.json()["audit"] is None
        assert isolated_loaded.json()["recomputeNote"].startswith(
            "too_few_items:"
        )


def test_recompute_views_match_reachable_renderer_contracts_and_scope(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        member = _join_member(client, admin)
        _seed_domain_facts(database, admin)
        isolated = client.post(
            "/api/v2/domain/project-materials/projects",
            headers=_headers(admin, "derived-view-isolated-project"),
            json={"name": "隔离项目"},
        )
        assert isolated.status_code == 201, isolated.text
        isolated_project_id = isolated.json()["project"]["projectId"]
        now = utc_now()
        with runtime_connection(database, "cloud") as connection:
            primary_project_id = str(
                connection.execute(
                    """
                    SELECT project_id FROM work_projects
                    WHERE organization_id = ? AND project_id != ?
                    ORDER BY created_at, project_id LIMIT 1
                    """,
                    (admin["organizationId"], isolated_project_id),
                ).fetchone()["project_id"]
            )
            connection.executemany(
                """
                INSERT INTO intelligence_records (
                    intelligence_id, organization_id, project_id, title,
                    summary, source_url, record_kind, status,
                    visibility_scope, created_by_membership_id,
                    source_payload_json, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'organization', ?, ?, 1, ?, ?)
                """,
                (
                    (
                        "sentiment_recompute_contract",
                        admin["organizationId"],
                        primary_project_id,
                        "公开评价",
                        "公开渠道认可项目执行",
                        "https://example.invalid/sentiment",
                        "public_sentiment",
                        "accepted",
                        admin["membershipId"],
                        json.dumps(
                            {
                                "contentKind": "public_opinion",
                                "sentiment": "positive",
                            }
                        ),
                        now,
                        now,
                    ),
                    (
                        "strategic_primary_contract",
                        admin["organizationId"],
                        primary_project_id,
                        "项目一研判",
                        "只属于项目一",
                        "",
                        "strategic_thought",
                        "candidate",
                        admin["membershipId"],
                        "{}",
                        now,
                        now,
                    ),
                    (
                        "strategic_isolated_contract",
                        admin["organizationId"],
                        isolated_project_id,
                        "项目二研判",
                        "只属于项目二",
                        "",
                        "strategic_thought",
                        "candidate",
                        admin["membershipId"],
                        "{}",
                        now,
                        now,
                    ),
                ),
            )
            connection.commit()

        themes = _command(
            client,
            member,
            path="intelligence/sentiment/themes/recompute",
            payload={"clientId": primary_project_id},
            key="member-themes-recompute-contract",
        )
        assert themes.status_code == 200, themes.text
        assert set(themes.json()) == {"ok", "themes"}
        assert themes.json()["ok"] is True
        assert [item["id"] for item in themes.json()["themes"]] == [
            "kind:public_sentiment"
        ]

        empty_themes = _command(
            client,
            member,
            path="intelligence/sentiment/themes/recompute",
            payload={"clientId": isolated_project_id},
            key="member-empty-themes-recompute-contract",
        )
        assert empty_themes.status_code == 200, empty_themes.text
        assert set(empty_themes.json()) == {"ok", "reason", "themes"}
        assert empty_themes.json()["ok"] is False
        assert empty_themes.json()["themes"] == []
        assert empty_themes.json()["reason"].startswith("too_few_items:")

        thoughts = _command(
            client,
            member,
            path="strategic/thoughts/refresh",
            payload={"clientId": primary_project_id, "limit": 1},
            key="member-strategic-refresh-contract",
        )
        assert thoughts.status_code == 200, thoughts.text
        assert set(thoughts.json()) == {
            "items",
            "total",
            "generatedAt",
            "selectedClientId",
            "selectedProjectModuleId",
            "usingMockData",
        }
        assert thoughts.json()["selectedClientId"] == primary_project_id
        assert thoughts.json()["total"] == 1
        assert [item["id"] for item in thoughts.json()["items"]] == [
            "strategic_primary_contract"
        ]


def test_strategy_extract_owner_editor_allowed_but_viewer_forbidden(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        member = _join_member(client, admin)
        _seed_domain_facts(database, admin)
        now = utc_now()
        with runtime_connection(database, "cloud") as connection:
            project_id = str(
                connection.execute(
                    """
                    SELECT project_id FROM work_projects
                    WHERE organization_id = ?
                    ORDER BY created_at, project_id LIMIT 1
                    """,
                    (admin["organizationId"],),
                ).fetchone()["project_id"]
            )
            connection.execute(
                """
                INSERT INTO project_participants (
                    project_id, organization_id, membership_id,
                    participant_role, status, version, created_at, updated_at
                ) VALUES (?, ?, ?, 'owner', 'active', 1, ?, ?)
                """,
                (
                    project_id,
                    admin["organizationId"],
                    member["membershipId"],
                    now,
                    now,
                ),
            )
            connection.commit()

        owner_save = _command(
            client,
            member,
            path="intelligence/brand-mirror/strategy-extract",
            method="PUT",
            payload={
                "clientId": project_id,
                "strategicObjective": "项目负责人确认的战略主张",
                "methodology": "以权威事实和项目协作为依据",
                "expectedVersion": 0,
            },
            key="strategy-extract-owner-save",
        )
        assert owner_save.status_code == 200, owner_save.text
        assert owner_save.json()["extract"]["confirmedBy"] == "研究成员"

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                UPDATE project_participants
                SET participant_role = 'editor', version = 2, updated_at = ?
                WHERE project_id = ? AND membership_id = ?
                """,
                (utc_now(), project_id, member["membershipId"]),
            )
            connection.commit()
        editor_save = _command(
            client,
            member,
            path="intelligence/brand-mirror/strategy-extract",
            method="PUT",
            payload={
                "clientId": project_id,
                "strategicObjective": "项目编辑者修订的战略主张",
                "methodology": "继续以权威事实和项目协作为依据",
                "expectedVersion": 1,
            },
            key="strategy-extract-editor-save",
        )
        assert editor_save.status_code == 200, editor_save.text

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                UPDATE project_participants
                SET participant_role = 'viewer', version = 3, updated_at = ?
                WHERE project_id = ? AND membership_id = ?
                """,
                (utc_now(), project_id, member["membershipId"]),
            )
            connection.commit()
        viewer_save = _command(
            client,
            member,
            path="intelligence/brand-mirror/strategy-extract",
            method="PUT",
            payload={
                "clientId": project_id,
                "strategicObjective": "只读成员不得覆盖",
                "methodology": "只读成员不得覆盖",
                "expectedVersion": 2,
            },
            key="strategy-extract-viewer-save",
        )
        assert viewer_save.status_code == 403
        assert viewer_save.json()["error"]["code"] == (
            "strategy_extract_edit_forbidden"
        )

        read_back = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(member),
            params={
                "resourcePath": "intelligence/brand-mirror/strategy-extract",
                "clientId": project_id,
            },
        )
        assert read_back.status_code == 200, read_back.text
        assert read_back.json()["extract"]["strategicObjective"] == (
            "项目编辑者修订的战略主张"
        )

    with runtime_connection(database, "cloud", read_only=True) as connection:
        narrative = connection.execute(
            """
            SELECT narrative_output_id, latest_version, version
            FROM narrative_outputs
            WHERE organization_id = ? AND project_id = ?
              AND output_kind = 'strategy_report'
            """,
            (admin["organizationId"], project_id),
        ).fetchone()
        assert narrative is not None
        assert (narrative["latest_version"], narrative["version"]) == (2, 2)
        assert connection.execute(
            """
            SELECT COUNT(*) FROM audit_events
            WHERE resource_id = ? AND action =
              'intelligence.brand-mirror.strategy-extract.put'
            """,
            (narrative["narrative_output_id"],),
        ).fetchone()[0] == 2
        assert connection.execute(
            """
            SELECT COUNT(*) FROM delivery_outbox
            WHERE aggregate_id = ? AND event_type = 'strategy_extract.saved'
            """,
            (narrative["narrative_output_id"],),
        ).fetchone()[0] == 2


def test_previous_25_hard_gaps_no_longer_return_capability_501(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        _seed_domain_facts(database, session)

        assert len(BLOCKED_OPERATIONS) == 25
        for index, (method, path, _reason) in enumerate(BLOCKED_OPERATIONS):
            response = _command(
                client,
                session,
                path=path,
                method=method,
                payload={},
                key=f"blocked-operation-{index}",
            )
            assert response.status_code != 501, (
                method,
                path,
                response.text,
            )

    with runtime_connection(database, "cloud") as connection:
        assert int(connection.execute(
            """
            SELECT COUNT(*) FROM command_envelopes
            WHERE idempotency_key LIKE 'blocked-operation-%'
            """
        ).fetchone()[0]) > 0


def test_schema_verification_rejects_unexpected_runtime_table(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE unexpected_runtime_table (id TEXT PRIMARY KEY) STRICT"
            )
        with pytest.raises(RuntimeError, match="schema table mismatch"):
            _command(
                client,
                session,
                path="data-center/schema/ensure",
                payload={},
                key="schema-drift-negative",
            )
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM command_envelopes
            WHERE idempotency_key = 'schema-drift-negative'
            """
        ).fetchone()[0] == 0


def test_rollback_drill_verifies_real_isolated_backup_and_hash(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    backup = tmp_path / "verified-backup.db"
    with client:
        session = _bootstrap(client)
        with sqlite3.connect(database) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        backup_hash = hashlib.sha256(backup.read_bytes()).hexdigest()
        now = utc_now()
        recovery_set_id = new_id()
        backup_id = new_id()
        with runtime_connection(database, "cloud") as connection:
            schema = connection.execute(
                """
                SELECT build_id, database_generation_id, manifest_hash
                FROM meta_schema_builds
                ORDER BY created_at DESC LIMIT 1
                """
            ).fetchone()
            connection.execute(
                """
                INSERT INTO recovery_sets (
                    recovery_set_id, candidate_version, schema_build_id,
                    database_generation_id, schema_manifest_hash,
                    component_manifest_hash, database_hash,
                    object_manifest_hash, deployment_manifest_hash,
                    status, created_at, verified_at
                ) VALUES (?, 'test-v3', ?, ?, ?, 'component-test', ?,
                          'objects-test', 'deployment-test', 'verified', ?, ?)
                """,
                (
                    recovery_set_id,
                    schema["build_id"],
                    schema["database_generation_id"],
                    schema["manifest_hash"],
                    backup_hash,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO backup_catalog (
                    backup_id, recovery_set_id, component_kind, backup_kind,
                    storage_location, checksum, content_hash, byte_size,
                    retention_until, verified, status, created_at, verified_at
                ) VALUES (?, ?, 'database', 'sqlite_backup', ?, ?, ?, ?,
                          ?, 1, 'available', ?, ?)
                """,
                (
                    backup_id,
                    recovery_set_id,
                    str(backup),
                    backup_hash,
                    backup_hash,
                    backup.stat().st_size,
                    now,
                    now,
                    now,
                ),
            )
            connection.commit()
        response = _command(
            client,
            session,
            path="data-center/rollback-drill",
            payload={},
            key="rollback-drill-real",
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "verified"
        assert response.json()["quickCheck"] == "ok"
        assert response.json()["foreignKeyViolationCount"] == 0
        assert response.json()["restoredCopyRemoved"] is True
        backup.write_bytes(backup.read_bytes() + b"tampered")
        mismatched = _command(
            client,
            session,
            path="data-center/rollback-drill",
            payload={},
            key="rollback-drill-tampered",
        )
        assert mismatched.status_code == 409, mismatched.text
        assert (
            mismatched.json()["error"]["code"]
            == "recovery_backup_hash_mismatch"
        )
