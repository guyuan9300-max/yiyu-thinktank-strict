from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.runtime import LocalRuntimeError
from backend.app.ui_domains.gc04_tasks import _require_task_view_projection_contract
from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repositories.gc06_planning import (
    create_event_line,
    event_line_detail,
)
from cloud_backend.app.repository import SessionIdentity
from strict_common.ids import utc_now
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository


def test_local_adapter_rejects_old_cloud_task_projection_contract() -> None:
    with pytest.raises(LocalRuntimeError) as error:
        _require_task_view_projection_contract(
            {
                "tasks": [{"id": "task_from_old_cloud"}],
            }
        )

    assert error.value.status_code == 502
    assert error.value.code == "task_view_projection_contract_mismatch"


def test_local_adapter_accepts_task_projection_contract_v1() -> None:
    _require_task_view_projection_contract(
        {
            "viewerProjectionContract": {
                "schema": "yiyu.task-viewer-projection.v1",
                "schemaVersion": 1,
                "requiredTaskFields": [
                    "viewer_surfaces",
                    "viewer_capabilities",
                    "owner_department_resolution",
                    "owner_department_id",
                    "owner_department_name",
                    "owner_departments",
                ],
            },
            "tasks": [
                {
                    "id": "task_v1",
                    "viewer_surfaces": {},
                    "viewer_capabilities": {},
                    "owner_department_resolution": "unassigned",
                    "owner_department_id": None,
                    "owner_department_name": None,
                    "owner_departments": [],
                }
            ],
        }
    )


def _member(
    repository: object,
    identity: SessionIdentity,
    *,
    suffix: str,
    display_name: str,
) -> SessionIdentity:
    now = utc_now()
    principal_id = f"principal_task_aggregation_{suffix}"
    membership_id = f"membership_task_aggregation_{suffix}"
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,"
            "principal_kind,display_name,version,lifecycle_state,created_at,deleted_at) "
            "VALUES (?,'active',1,?,'person',?,1,'active',?,NULL)",
            (principal_id, now, display_name, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
            "status,version,record_kind,visibility_scope,lifecycle_state,created_at,"
            "updated_at,deleted_at) VALUES (?,?,?,'member','active',1,'membership',"
            "'organization','active',?,?,NULL)",
            (membership_id, identity.scope_id, principal_id, now, now),
        )
        connection.commit()
    return SessionIdentity(
        session_id=f"session_task_aggregation_{suffix}",
        principal_id=principal_id,
        membership_id=membership_id,
        organization_id=identity.organization_id,
        cloud_instance_id=identity.cloud_instance_id,
        scope_id=identity.scope_id,
        system_role="member",
        visibility_scope="organization",
        display_name=display_name,
    )


def _assign_department(
    repository: object,
    identity: SessionIdentity,
    member: SessionIdentity,
) -> None:
    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,"
            "record_kind,parent_record_id,name,created_at,deleted_at) VALUES "
            "('department_task_aggregation','active',1,?,'department',?,"
            "'产品部',?,NULL)",
            (now, identity.organization_id, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
            "status,version,record_kind,parent_membership_id,department_id,"
            "visibility_scope,lifecycle_state,created_at,updated_at,deleted_at) VALUES "
            "('department_assignment_task_aggregation',?,?, 'member','active',1,"
            "'department_assignment',?,'department_task_aggregation','department',"
            "'active',?,?,NULL)",
            (
                identity.scope_id,
                member.principal_id,
                member.membership_id,
                now,
                now,
            ),
        )
        connection.commit()


def _grant_project_read(
    repository: object,
    identity: SessionIdentity,
    project_id: str,
) -> None:
    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        policy = connection.execute(
            "SELECT id FROM policy_versions WHERE scope_id=? AND secured_resource_id=? "
            "AND lifecycle_state='active' ORDER BY version DESC LIMIT 1",
            (identity.scope_id, project_id),
        ).fetchone()
        assert policy is not None
        connection.execute(
            "INSERT INTO object_grants (id,scope_id,secured_resource_id,policy_version_id,"
            "subject_principal_id,subject_membership_id,capability_set_schema_version,"
            "capability_set,grant_generation,status,grant_source_set_id,created_at,"
            "updated_at,revoked_at,version,lifecycle_state,deleted_at) VALUES "
            "(?,?,?,?,NULL,?,'1','{\"read\":true,\"write\":false}',1,'active',"
            "NULL,?,?,NULL,1,'active',NULL)",
            (
                f"grant_task_aggregation_{identity.membership_id}",
                identity.scope_id,
                project_id,
                str(policy["id"]),
                identity.membership_id,
                now,
                now,
            ),
        )
        connection.commit()


def test_board_projects_personal_surfaces_and_owner_department(tmp_path: Path) -> None:
    repository, creator, _ = _repository(tmp_path)
    owner = _member(repository, creator, suffix="owner", display_name="负责人")
    outsider = _member(repository, creator, suffix="outsider", display_name="旁观成员")
    _assign_department(repository, creator, owner)
    domain = GC04TaskRepository(repository)

    created = domain.create_task(
        creator,
        payload={
            "title": "按负责人部门聚合的任务",
            "priority": "normal",
            "ownerMembershipId": owner.membership_id,
            "visibilityScope": "organization",
            "scheduledStartAt": "2026-08-25T09:00:00Z",
            "scheduledEndAt": "2026-08-25T10:00:00Z",
        },
        idempotency_key="task-aggregation-create",
    )
    task_id = str(created["task"]["id"])
    owner_assignment = next(
        item
        for item in created["task"]["collaborators"]
        if item["role_key"] == "owner"
    )

    creator_task = next(
        item for item in domain.board(creator)["tasks"] if item["id"] == task_id
    )
    outsider_board = domain.board(outsider)
    outsider_task = next(
        item for item in outsider_board["tasks"] if item["id"] == task_id
    )
    pending_owner_task = next(
        item for item in domain.board(owner)["tasks"] if item["id"] == task_id
    )

    assert creator_task["viewer_surfaces"]["personal_list"] is False
    assert creator_task["viewer_capabilities"]["can_edit"] is True
    assert outsider_task["viewer_surfaces"]["personal_calendar"] is False
    assert outsider_task["viewer_capabilities"]["can_edit"] is False
    assert pending_owner_task["viewer_surfaces"]["collaboration_inbox"] is True
    assert pending_owner_task["viewer_surfaces"]["personal_list"] is False
    assert all(item.get("task_id") != task_id for item in outsider_board["calendarEntries"])

    domain.handle_inbox(
        owner,
        task_id=task_id,
        action="accept",
        expected_version=int(owner_assignment["version"]),
        reason=None,
        idempotency_key="task-aggregation-owner-accept",
    )
    owner_board = domain.board(owner)
    owner_task = next(item for item in owner_board["tasks"] if item["id"] == task_id)

    assert owner_task["viewer_surfaces"]["personal_list"] is True
    assert owner_task["viewer_surfaces"]["personal_calendar"] is True
    assert owner_task["owner_department_resolution"] == "resolved"
    assert owner_task["owner_department_id"] == "department_task_aggregation"
    assert owner_task["owner_department_name"] == "产品部"
    assert any(item.get("task_id") == task_id for item in owner_board["calendarEntries"])


def test_event_line_detail_returns_all_line_tasks_with_task_level_capabilities(
    tmp_path: Path,
) -> None:
    repository, creator, payload = _repository(tmp_path)
    viewer = _member(repository, creator, suffix="line_viewer", display_name="事件线访客")
    _grant_project_read(repository, viewer, payload["projectId"])
    line = create_event_line(
        repository,
        creator,
        payload={
            "eventLineId": "event_line_task_aggregation",
            "clientId": payload["projectId"],
            "name": "完整任务事件线",
        },
        idempotency_key="task-aggregation-line-create",
    )["eventLine"]
    domain = GC04TaskRepository(repository)
    created = domain.create_task(
        creator,
        payload={
            "title": "事件线内但不属于访客个人任务",
            "priority": "normal",
            "clientId": payload["projectId"],
            "eventLineId": line["id"],
            "ownerMembershipId": creator.membership_id,
            "visibilityScope": "participants",
        },
        idempotency_key="task-aggregation-line-task-create",
    )["task"]

    assert domain.board(viewer)["tasks"] == []
    detail = event_line_detail(repository, viewer, event_line_id=line["id"])
    line_task = next(item for item in detail["tasks"] if item["id"] == created["id"])

    assert line_task["viewer_surfaces"]["event_line_detail"] is True
    assert line_task["viewer_surfaces"]["personal_list"] is False
    assert line_task["viewer_capabilities"]["can_view"] is True
    assert line_task["viewer_capabilities"]["can_edit"] is False
