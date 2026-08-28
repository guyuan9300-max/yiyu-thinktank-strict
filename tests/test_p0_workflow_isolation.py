from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from types import MethodType
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.runtime import LocalRuntimeError, WorkspaceContext, WorkspaceRuntime
from backend.app.ui_domains import UiRequest, build_default_registry
from backend.app.ui_domains.project_materials import (
    router as project_materials_router,
)
from backend.app.ui_domains.organization_access import (
    router as organization_access_router,
)
from backend.app.ui_domains.platform_integrations import (
    router as platform_integrations_router,
)
from backend.app.ui_domains.routing import UiDomainRouter
from backend.app.ui_domains.workbench_outputs import (
    router as workbench_outputs_router,
)
from backend.app.ui_domains.workflow import (
    _requires_pinned_workspace,
    router as workflow_router,
)
from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from strict_common.schema import runtime_connection
from tests.strict_cloud_test_factory import (
    provision_test_organization,
    strict_cloud_test_client,
)


def _cloud(tmp_path: Path) -> tuple[TestClient, Path]:
    client, database, _ = strict_cloud_test_client(
        tmp_path,
        bootstrap_token="p0-workflow-bootstrap",
        cloud_instance_id="cloud-p0-workflow-test",
        database_name="strict-p0-workflow.db",
    )
    return client, database


def _auth(session: dict[str, Any], key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {session['accessToken']}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _bootstrap(client: TestClient) -> dict[str, Any]:
    return provision_test_organization(
        client,
        organization_name="P0 权限测试组织",
        display_name="管理员",
        email="p0-admin@example.com",
        password="admin-password",
    )


def _member(client: TestClient, admin: dict[str, Any]) -> dict[str, Any]:
    department = client.post(
        "/api/v2/organization/departments",
        headers=_auth(admin, "p0-department"),
        json={"name": "项目部", "expectedOrganizationVersion": 1},
    )
    assert department.status_code == 201, department.text
    invite = client.post(
        "/api/v2/organization/invites",
        headers=_auth(admin),
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
            "displayName": "普通成员",
            "email": "p0-member@example.com",
            "password": "member-password",
        },
    )
    assert joined.status_code == 201, joined.text
    return joined.json()


def test_weekly_review_self_content_is_visible_only_to_owner(
    tmp_path: Path,
) -> None:
    client, _ = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        member = _member(client, admin)
        project_id = client.get(
            "/api/v2/business/snapshot",
            headers=_auth(member),
        ).json()["projects"][0]["projectId"]
        task = client.post(
            "/api/v2/tasks",
            headers=_auth(member, "p0-personal-task"),
            json={"title": "成员个人复盘任务", "projectId": project_id},
        )
        assert task.status_code == 201, task.text
        task_id = task.json()["task"]["taskId"]
        saved = client.post(
            "/api/v2/workflow/reviews/weekly",
            headers=_auth(member, "p0-personal-review"),
            json={
                "weekLabel": "2026-W31",
                "workProgress": "可见工作进展",
                "personalGrowthNote": "只允许本人看到的成长笔记",
                "personalVisibility": "self",
                "taskEntries": [
                    {
                        "taskId": task_id,
                        "note": "只允许本人看到的任务笔记",
                        "structuredNote": {"contentDomain": "personal"},
                    }
                ],
            },
        )
        assert saved.status_code == 200, saved.text

        owner_view = client.get(
            "/api/v2/workflow/reviews?weekLabel=2026-W31",
            headers=_auth(member),
        )
        assert owner_view.status_code == 200, owner_view.text
        owner_review = owner_view.json()["reviews"][0]
        assert owner_review["personalGrowthNote"] == "只允许本人看到的成长笔记"
        assert any(
            item["sectionType"] == "personal_growth_note"
            for item in owner_review["sections"]
        )
        assert owner_review["taskLinks"][0]["note"] == "只允许本人看到的任务笔记"

        admin_view = client.get(
            "/api/v2/workflow/reviews?weekLabel=2026-W31",
            headers=_auth(admin),
        )
        assert admin_view.status_code == 200, admin_view.text
        admin_review = admin_view.json()["reviews"][0]
        assert admin_review["workProgress"] == "可见工作进展"
        assert admin_review["personalGrowthNote"] == ""
        assert not any(
            item["sectionType"] == "personal_growth_note"
            for item in admin_review["sections"]
        )
        assert admin_review["taskLinks"] == []
        assert "只允许本人看到" not in json.dumps(
            admin_review,
            ensure_ascii=False,
        )


def test_meeting_context_excludes_invisible_project_assets(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        member = _member(client, admin)
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers=_auth(admin, "p0-private-project"),
            json={"name": "仅管理员参与项目"},
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["project"]["projectId"]
        current = client.get(
            "/api/v2/session/current",
            headers=_auth(admin),
        ).json()
        now = "2026-07-30T12:00:00Z"
        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                INSERT INTO source_assets (
                  source_asset_id, organization_id, project_id,
                  storage_object_id, file_name, media_type, byte_size,
                  content_hash, source_kind, source_locator, lifecycle_state,
                  created_by_membership_id, version, created_at, updated_at
                ) VALUES (
                  'p0-private-meeting-asset', ?, ?, NULL, 'private-meeting.md',
                  'text/markdown', 0, '', 'meeting_note',
                  'meeting-private-1', 'active', ?, 1, ?, ?
                )
                """,
                (
                    current["organizationId"],
                    project_id,
                    current["membershipId"],
                    now,
                    now,
                ),
            )
            connection.commit()

        owner_view = client.get(
            "/api/v2/workflow/meetings/meeting-private-1/context",
            headers=_auth(admin),
        )
        assert owner_view.status_code == 200, owner_view.text
        assert owner_view.json()["documents"][0]["sourceAssetId"] == (
            "p0-private-meeting-asset"
        )

        denied = client.get(
            "/api/v2/workflow/meetings/meeting-private-1/context",
            headers=_auth(member),
        )
        assert denied.status_code == 404
        assert denied.json()["error"]["code"] == "meeting_context_missing"
        assert "private-meeting" not in denied.text


def test_agent_weekly_plans_are_admin_only(tmp_path: Path) -> None:
    client, _ = _cloud(tmp_path)
    with client:
        admin = _bootstrap(client)
        member = _member(client, admin)
        created = client.put(
            "/api/v2/workflow/agent-weekly-plans/2026-W31/research",
            headers=_auth(admin, "p0-agent-plan"),
            json={
                "summary": "管理员配置的机器人周计划",
                "planItems": [{"title": "检索资料"}],
            },
        )
        assert created.status_code == 200, created.text

        denied_read = client.get(
            "/api/v2/workflow/agent-weekly-plans",
            headers=_auth(member),
        )
        assert denied_read.status_code == 403
        assert denied_read.json()["error"]["code"] == "admin_required"
        member_snapshot = client.get(
            "/api/v2/business/snapshot",
            headers=_auth(member),
        )
        assert member_snapshot.status_code == 200, member_snapshot.text
        assert not any(
            plan.get("summary") == "管理员配置的机器人周计划"
            for plan in member_snapshot.json()["plans"]
        )

        denied_write = client.put(
            "/api/v2/workflow/agent-weekly-plans/2026-W31/research",
            headers=_auth(member, "p0-agent-plan-denied"),
            json={
                "expectedVersion": 1,
                "summary": "普通成员不得覆盖",
                "planItems": [],
            },
        )
        assert denied_write.status_code == 403
        assert denied_write.json()["error"]["code"] == "admin_required"


def test_all_workflow_routes_require_one_pinned_workspace() -> None:
    assert workflow_router.routes
    assert all(
        _requires_pinned_workspace(route.method, route.pattern)
        for route in workflow_router.routes
    )


def test_long_running_local_domains_pin_one_workspace_context() -> None:
    assert project_materials_router.pin_workspace is True
    assert workbench_outputs_router.pin_workspace is True
    active = {"pinned": False}

    class Runtime:
        @contextmanager
        def pinned_workspace_context(self):
            active["pinned"] = True
            try:
                yield
            finally:
                active["pinned"] = False

    probe = UiDomainRouter("probe", pin_workspace=True)

    @probe.get(r"probe")
    def handler(_compatibility: Any, _request: UiRequest, _match: Any):
        assert active["pinned"] is True
        return {"state": "ready"}

    result = probe.dispatch(
        type("Compatibility", (), {"runtime": Runtime()})(),
        UiRequest(
            method="GET",
            path="probe",
            query={},
            body={},
            idempotency_key="probe",
        ),
    )
    assert result == {"state": "ready"}
    assert active["pinned"] is False

    assert platform_integrations_router.pin_workspace(
        UiRequest(
            method="POST",
            path="software-feedback",
            query={},
            body={},
            idempotency_key="feedback",
        )
    )
    assert platform_integrations_router.pin_workspace(
        UiRequest(
            method="POST",
            path="recordings/transcribe-local-audio",
            query={},
            body={},
            idempotency_key="asr",
        )
    )
    assert organization_access_router.pin_workspace(
        UiRequest(
            method="POST",
            path="settings",
            query={},
            body={},
            idempotency_key="settings",
        )
    )


def _context(suffix: str) -> WorkspaceContext:
    return WorkspaceContext(
        sandbox_id=f"sandbox-{suffix}",
        cloud_instance_id=f"cloud-{suffix}",
        organization_id=f"organization-{suffix}",
        cloud_api_url=f"https://{suffix}.invalid",
        principal_id=f"principal-{suffix}",
        membership_id=f"membership-{suffix}",
        access_token=f"access-{suffix}",
        refresh_token=f"refresh-{suffix}",
        access_expires_at=None,
        refresh_expires_at=None,
    )


def test_pinned_workspace_rejects_a_mid_request_identity_change() -> None:
    runtime = WorkspaceRuntime.__new__(WorkspaceRuntime)
    runtime._state_lock = threading.RLock()
    active = {"context": _context("a")}

    def current(
        _: WorkspaceRuntime,
        *,
        require_ready: bool = True,
    ) -> WorkspaceContext:
        assert require_ready
        return active["context"]

    runtime._current_context = MethodType(current, runtime)
    with pytest.raises(LocalRuntimeError) as changed:
        with runtime.pinned_workspace_context():
            active["context"] = _context("b")
    assert changed.value.status_code == 409
    assert changed.value.code == "workspace_context_changed"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "GET",
            "clients/project-a/workspace/chat/messages/answer-b",
            {},
        ),
        (
            "GET",
            "clients/project-a/workspace/chat/threads/answer-b",
            {},
        ),
        (
            "DELETE",
            "clients/project-a/workspace/chat/messages/answer-b",
            {},
        ),
        (
            "POST",
            "clients/project-a/knowledge/vectorize-answer",
            {"messageId": "answer-b"},
        ),
        (
            "GET",
            "clients/project-a/analysis-runs/answer-b",
            {},
        ),
    ],
)
def test_project_scoped_workbench_routes_reject_an_answer_from_another_project(
    method: str,
    path: str,
    body: dict[str, Any],
) -> None:
    class Runtime:
        def __init__(self) -> None:
            self.commands: list[tuple[Any, ...]] = []

        def cloud_query(
            self,
            cloud_path: str,
            *,
            query: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            assert cloud_path == "/api/v2/workbench/answers/answer-b"
            assert query is None
            return {
                "answer": {
                    "answerId": "answer-b",
                    "projectId": "project-b",
                    "question": "B 项目问题",
                    "answerMarkdown": "B 项目回答",
                    "version": 1,
                }
            }

        def cloud_command(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.commands.append((args, kwargs))
            raise AssertionError("跨项目校验失败后不得继续写入")

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

    compatibility = Compatibility()
    with pytest.raises(LocalRuntimeError) as mismatch:
        build_default_registry().dispatch(
            compatibility,
            UiRequest(
                method=method,
                path=path,
                query={},
                body=body,
                idempotency_key="p0-cross-project-answer",
            ),
        )
    assert mismatch.value.status_code == 404
    assert mismatch.value.code == "answer_project_mismatch"
    assert compatibility.runtime.commands == []
