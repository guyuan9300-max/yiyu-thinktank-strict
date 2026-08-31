from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

import cloud_backend.app.repositories.gc04_tasks as gc04_tasks_repository
from backend.app.gc04_tasks_local import LocalGC04TaskProjection
from backend.app.runtime import WorkspaceRuntime
from backend.app.ui_domains.gc04_tasks import (
    _deterministic_context_narrative,
    _relationship_is_clear,
    _task_ui,
)
from cloud_backend.app.domain_routes.gc04_tasks import register_gc04_task_routes
from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repositories.gc06_planning import (
    create_event_line,
    transition_event_line,
)
from cloud_backend.app.repository import RepositoryError, SessionIdentity
from strict_common.ids import utc_now
from strict_common.schema import initialize_database, runtime_connection
from tests.test_gc14_workbench_answer import _repository


def _member(
    repository: object,
    identity: SessionIdentity,
    *,
    suffix: str,
    display_name: str,
) -> SessionIdentity:
    now = utc_now()
    principal_id = f"principal_gc04_{suffix}"
    membership_id = f"membership_gc04_{suffix}"
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,"
            "principal_kind,display_name,version,lifecycle_state,created_at,deleted_at) "
            "VALUES (?,'active',1,?,'person',?,"
            "1,'active',?,NULL)",
            (principal_id, now, display_name, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
            "status,version,record_kind,visibility_scope,lifecycle_state,created_at,"
            "updated_at,deleted_at) VALUES (?,?,?,'member','active',1,'membership','organization',"
            "'active',?,?,NULL)",
            (membership_id, identity.scope_id, principal_id, now, now),
        )
        connection.commit()
    return SessionIdentity(
        session_id=f"session_gc04_{suffix}",
        principal_id=principal_id,
        membership_id=membership_id,
        organization_id=identity.organization_id,
        cloud_instance_id=identity.cloud_instance_id,
        scope_id=identity.scope_id,
        system_role="member",
        visibility_scope="organization",
        display_name=display_name,
    )


def _second_member(repository: object, identity: SessionIdentity) -> SessionIdentity:
    return _member(
        repository,
        identity,
        suffix="peer",
        display_name="GC04协作者",
    )


def _grant_read_only_project_access(
    repository: object,
    identity: SessionIdentity,
    project_id: str,
) -> None:
    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        policy = connection.execute(
            "SELECT id FROM policy_versions WHERE scope_id=? "
            "AND secured_resource_id=? AND lifecycle_state='active' "
            "ORDER BY version DESC, created_at DESC, id DESC LIMIT 1",
            (identity.scope_id, project_id),
        ).fetchone()
        assert policy is not None
        connection.execute(
            "INSERT INTO object_grants (id,scope_id,secured_resource_id,policy_version_id,"
            "subject_principal_id,subject_membership_id,capability_set_schema_version,"
            "capability_set,grant_generation,status,grant_source_set_id,created_at,updated_at,"
            "revoked_at,version,lifecycle_state,deleted_at) VALUES (?,?,?, ?,NULL,?,'1',"
            "'{\"read\":true,\"write\":false,\"contributeKnowledge\":false}',1,"
            "'active',NULL,?,?,NULL,1,'active',NULL)",
            (
                f"grant_gc04_read_only_{identity.membership_id}",
                identity.scope_id,
                project_id,
                str(policy["id"]),
                identity.membership_id,
                now,
                now,
            ),
        )
        connection.commit()


def _create(
    domain: GC04TaskRepository,
    identity: SessionIdentity,
    key: str,
    title: str,
    **extra: object,
) -> dict:
    return domain.create_task(
        identity,
        payload={"title": title, "priority": "normal", **extra},
        idempotency_key=key,
    )


def test_task_context_relationship_requires_task_specific_evidence() -> None:
    sources = [
        {
            "title": "心益计划项目资料",
            "summary": "心益计划通过培训大学生志愿者开展儿童心理活动课。",
        }
    ]
    focused_hint = {
        "title": "整理心益计划志愿者培训报告",
        "description": "核对培训对象和实施方式",
        "clientName": "日慈基金会",
    }
    generic_hint = {
        "title": "推进日慈基金会协作任务",
        "description": "完成本轮验收",
        "clientName": "日慈基金会",
    }
    assert _relationship_is_clear(sources, hint=focused_hint) is True
    assert _relationship_is_clear(sources, hint=generic_hint) is False
    fallback = _deterministic_context_narrative(
        hint=generic_hint,
        selected=sources,
        relationship_clear=False,
    )
    assert "尚不足以判断" in fallback
    assert "- " not in fallback


def test_agent_weekly_plan_uses_task_control_rule_and_replays(tmp_path: Path) -> None:
    repository, admin, _ = _repository(tmp_path)
    domain = GC04TaskRepository(repository)
    payload = {
        "summary": "本周只推进一项真实工作",
        "planItems": [
            {
                "title": "完成严格链路核验",
                "rationale": "以正式任务为证据",
                "scheduleHint": "周五前",
                "status": "planned",
            }
        ],
    }
    saved = domain.save_agent_weekly_plan(
        admin,
        week_label="2026-W32",
        agent_key="tech_development",
        payload=payload,
        idempotency_key="agent-plan-save-1",
    )
    replay = domain.save_agent_weekly_plan(
        admin,
        week_label="2026-W32",
        agent_key="tech_development",
        payload=payload,
        idempotency_key="agent-plan-save-1",
    )
    assert replay == saved
    projection = domain.agent_coordination(admin, week_label="2026-W32")
    assert projection["weeklyPlans"] == [saved["weeklyPlan"]]
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT record_kind,trigger_spec,action_spec FROM automation_rules "
            "WHERE id=?",
            (saved["weeklyPlan"]["planId"],),
        ).fetchone()
        assert row["record_kind"] == "task_control"
        assert json.loads(row["trigger_spec"])["weekLabel"] == "2026-W32"
        assert json.loads(row["action_spec"])["planItems"][0]["title"] == "完成严格链路核验"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_task_unrelated_fields_remain_editable_after_event_line_archive(
    tmp_path: Path,
) -> None:
    repository, admin, seed_payload = _repository(tmp_path)
    event_line_id = "event_line_gc04_archived_edit"
    create_event_line(
        repository,
        admin,
        payload={
            "eventLineId": event_line_id,
            "clientId": seed_payload["projectId"],
            "name": "已归档事件线上的既有任务",
        },
        idempotency_key="gc04-archived-edit-line-create",
    )
    domain = GC04TaskRepository(repository)
    created = _create(
        domain,
        admin,
        "gc04-archived-edit-task-create",
        "归档后仍可编辑的任务",
        clientId=seed_payload["projectId"],
        eventLineId=event_line_id,
    )
    transition_event_line(
        repository,
        admin,
        event_line_id=event_line_id,
        transition="archive",
        expected_version=1,
        idempotency_key="gc04-archived-edit-line-archive",
    )

    updated = domain.update_task(
        admin,
        task_id=created["task"]["id"],
        payload={
            "expectedVersion": 1,
            "clientId": seed_payload["projectId"],
            "eventLineId": event_line_id,
            "description": "同时修改描述",
            "priority": "high",
            "dueDate": "2026-08-09",
        },
        idempotency_key="gc04-archived-edit-task-update",
    )["task"]

    assert updated["client_id"] == seed_payload["projectId"]
    assert updated["event_line_id"] == event_line_id
    assert updated["description"] == "同时修改描述"
    assert updated["priority"] == "high"
    assert updated["due_date"] == "2026-08-09"


def test_project_access_does_not_leak_participant_task(
    tmp_path: Path,
) -> None:
    repository, admin, seed_payload = _repository(tmp_path)
    peer = _second_member(repository, admin)
    domain = GC04TaskRepository(repository)
    project_id = seed_payload["projectId"]

    # Make the peer the project owner to prove that even explicit project
    # write access is not inherited by participant-only task records.
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "UPDATE clients SET owner_membership_id=?,visibility_scope='organization' "
            "WHERE id=? AND scope_id=?",
            (peer.membership_id, project_id, admin.scope_id),
        )
        connection.commit()

    private_task = _create(
        domain,
        admin,
        "gc04-project-permission-isolation",
        "仅发起人与负责人可见",
        clientId=project_id,
        ownerMembershipId=admin.membership_id,
        visibilityScope="participants",
    )["task"]

    assert private_task["client_id"] == project_id
    assert [item["id"] for item in domain.board(peer)["tasks"]] == []
    with pytest.raises(RepositoryError, match="任务不存在或已不可用"):
        domain.task_detail(peer, task_id=private_task["id"])
    with pytest.raises(RepositoryError, match="任务不存在或已不可用"):
        domain.update_task(
            peer,
            task_id=private_task["id"],
            payload={"expectedVersion": 1, "title": "项目负责人不得越权修改"},
            idempotency_key="gc04-project-permission-isolation-write",
        )

    shared_task = _create(
        domain,
        admin,
        "gc04-organization-visible-task",
        "明确组织可见任务",
        clientId=project_id,
        ownerMembershipId=admin.membership_id,
        visibilityScope="organization",
    )["task"]
    assert [item["id"] for item in domain.board(peer)["tasks"]] == [
        shared_task["id"]
    ]


def test_read_only_project_member_can_reference_project_from_tasks(
    tmp_path: Path,
) -> None:
    repository, admin, seed_payload = _repository(tmp_path)
    peer = _second_member(repository, admin)
    domain = GC04TaskRepository(repository)
    project_id = seed_payload["projectId"]

    with pytest.raises(RepositoryError, match="无法访问该项目"):
        _create(
            domain,
            peer,
            "gc04-unreadable-project-reference",
            "不可读项目不能被任务引用",
            clientId=project_id,
        )

    unbound = _create(
        domain,
        peer,
        "gc04-read-only-project-unbound",
        "稍后关联只读项目",
    )["task"]
    _grant_read_only_project_access(repository, peer, project_id)

    created = _create(
        domain,
        peer,
        "gc04-read-only-project-create",
        "创建时关联只读项目",
        clientId=project_id,
    )["task"]
    assert created["client_id"] == project_id

    updated = domain.update_task(
        peer,
        task_id=unbound["id"],
        payload={"expectedVersion": 1, "clientId": project_id},
        idempotency_key="gc04-read-only-project-update",
    )["task"]
    assert updated["client_id"] == project_id

    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT * FROM tasks WHERE id=? AND scope_id=?",
            (updated["id"], peer.scope_id),
        ).fetchone()
        assert row is not None
        domain._revalidate_normalized_patch(  # noqa: SLF001
            connection,
            peer,
            row,
            {"client_id": project_id},
        )
        with pytest.raises(RepositoryError, match="无权执行该项目操作"):
            repository._require_project_access(  # noqa: SLF001
                connection,
                peer,
                project_id=project_id,
                capability="project_write",
            )


def test_administrator_does_not_bypass_participant_task_visibility(
    tmp_path: Path,
) -> None:
    repository, admin, _ = _repository(tmp_path)
    peer = _second_member(repository, admin)
    domain = GC04TaskRepository(repository)

    private_task = _create(
        domain,
        peer,
        "gc04-admin-no-content-bypass",
        "成员私有参与者任务",
        ownerMembershipId=peer.membership_id,
        visibilityScope="participants",
    )["task"]

    assert [item["id"] for item in domain.board(admin)["tasks"]] == []
    with pytest.raises(RepositoryError, match="任务不存在或已不可用"):
        domain.task_detail(admin, task_id=private_task["id"])


def test_gc04_owner_gate_return_to_creator_and_resend(tmp_path: Path) -> None:
    repository, creator, _ = _repository(tmp_path)
    owner = _member(
        repository,
        creator,
        suffix="owner_gate",
        display_name="GC04负责人",
    )
    collaborator = _member(
        repository,
        creator,
        suffix="watcher_gate",
        display_name="GC04普通协作者",
    )
    domain = GC04TaskRepository(repository)
    created = _create(
        domain,
        creator,
        "gc04-owner-gate-create",
        "负责人确认后再通知协作者",
        ownerMembershipId=owner.membership_id,
        collaboratorMembershipIds=[collaborator.membership_id],
        scheduledStartAt="2026-08-20T09:00:00Z",
        scheduledEndAt="2026-08-20T10:00:00Z",
    )
    task_id = str(created["task"]["id"])
    owner_row = next(
        item for item in created["task"]["collaborators"] if item["role_key"] == "owner"
    )
    staged_row = next(
        item
        for item in created["task"]["collaborators"]
        if item["role_key"] == "collaborator"
    )
    assert owner_row["inbox_status"] == "pending"
    assert owner_row["assignment_state"] == "assigned"
    assert staged_row["inbox_status"] == "pending"
    assert staged_row["assignment_state"] == "awaiting_owner"
    assert [item["role_key"] for item in created["task"]["collaborators"]] == [
        "owner",
        "collaborator",
    ]
    assert all(item["id"] != task_id for item in domain.board(collaborator)["tasks"])

    returned = domain.handle_inbox(
        owner,
        task_id=task_id,
        action="return",
        expected_version=int(owner_row["version"]),
        reason="请发起人补充说明",
        idempotency_key="gc04-owner-gate-return",
    )
    assert returned["restoredOwnerCollaborator"] is None
    creator_task = next(
        item for item in domain.board(creator)["tasks"] if item["id"] == task_id
    )
    assert creator_task["returned_to_creator"] is True
    assert _task_ui(creator_task)["status"] == "rejected"
    assert all(item["id"] != task_id for item in domain.board(owner)["tasks"])
    assert all(item["id"] != task_id for item in domain.board(collaborator)["tasks"])

    resent = domain.update_task(
        creator,
        task_id=task_id,
        payload={
            "expectedVersion": int(creator_task["version"]),
            "title": "负责人确认后再通知协作者（已补充）",
            "ownerMembershipId": owner.membership_id,
            "collaboratorMembershipIds": [collaborator.membership_id],
        },
        idempotency_key="gc04-owner-gate-resend",
    )
    resent_owner = next(
        item for item in resent["task"]["collaborators"] if item["role_key"] == "owner"
    )
    assert resent_owner["inbox_status"] == "pending"
    assert all(item["id"] != task_id for item in domain.board(collaborator)["tasks"])

    accepted = domain.handle_inbox(
        owner,
        task_id=task_id,
        action="accept",
        expected_version=int(resent_owner["version"]),
        reason=None,
        idempotency_key="gc04-owner-gate-accept",
    )
    assert accepted["collaborator"]["inbox_status"] == "accepted"
    collaborator_board = domain.board(collaborator)
    collaborator_task = next(
        item for item in collaborator_board["tasks"] if item["id"] == task_id
    )
    assert collaborator_task["viewer_role_key"] == "collaborator"
    assert collaborator_task["viewer_inbox_status"] == "pending"
    assert _task_ui(collaborator_task)["status"] == "todo"
    assert any(
        item.get("task_id") == task_id
        for item in collaborator_board["calendarEntries"]
    )


def test_gc04_task_cas_collaboration_calendar_list_lifecycle_and_proposal(
    tmp_path: Path,
) -> None:
    repository, admin, seed_payload = _repository(tmp_path)
    with runtime_connection(repository.database_path, "cloud") as connection:
        structure_before = dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )
    peer = _second_member(repository, admin)
    domain = GC04TaskRepository(repository)

    created = _create(
        domain,
        admin,
        "gc04-create",
        "组织级任务",
        collaboratorMembershipIds=[peer.membership_id],
        ownerName="不得按名称覆盖稳定负责人",
        clientName="不得按项目名称猜client_id",
        scheduledStartAt="2026-08-08T09:00:00Z",
        scheduledEndAt="2026-08-08T10:00:00Z",
    )
    replay = _create(
        domain,
        admin,
        "gc04-create",
        "组织级任务",
        collaboratorMembershipIds=[peer.membership_id],
        scheduledStartAt="2026-08-08T09:00:00Z",
        scheduledEndAt="2026-08-08T10:00:00Z",
    )
    task_id = created["task"]["id"]
    assert replay == created
    assert created["task"]["client_id"] is None
    assert created["task"]["event_line_id"] is None
    assert created["task"]["list"] is None
    assert next(
        item for item in created["task"]["collaborators"] if item["role_key"] == "owner"
    )["subject_membership_id"] == admin.membership_id
    assert created["task"]["creator_display_name"] == admin.display_name
    assert created["notificationResult"]["state"] == "not_connected"
    assert created["notificationResult"]["partialSuccess"] is False
    pending = next(
        item
        for item in created["task"]["collaborators"]
        if item["subject_membership_id"] == peer.membership_id
    )
    assert pending["inbox_status"] == "pending"

    # 发起人本身就是已接受负责人，因此普通协作者的待阅不阻塞同一任务
    # 进入列表/月历；“已阅”只确认通知，不创建第二条任务。
    pending_board = domain.board(peer)
    pending_task = next(item for item in pending_board["tasks"] if item["id"] == task_id)
    assert pending_task["viewer_inbox_status"] == "pending"
    assert any(
        item.get("task_id") == task_id for item in pending_board["calendarEntries"]
    )

    accepted = domain.handle_inbox(
        peer,
        task_id=task_id,
        action="accept",
        expected_version=int(pending["version"]),
        reason=None,
        idempotency_key="gc04-accept",
    )
    assert accepted["collaborator"]["inbox_status"] == "accepted"
    # 普通协作者点“已阅”只确认本人通知，不再派生第二条通知。
    assert accepted["notificationResult"]["state"] == "not_requested"
    accepted_board = domain.board(peer)
    assert any(
        item.get("task_id") == task_id for item in accepted_board["calendarEntries"]
    )

    owner = next(
        item for item in created["task"]["collaborators"] if item["role_key"] == "owner"
    )
    transferred = domain.transfer_task(
        admin,
        task_id=task_id,
        target_membership_id=peer.membership_id,
        expected_owner_version=int(owner["version"]),
        idempotency_key="gc04-transfer",
    )
    assert transferred["ownerCollaborator"]["inbox_status"] == "pending"
    assert transferred["notificationResult"]["state"] == "not_connected"
    sender_task = _task_ui(next(
        item for item in domain.board(admin)["tasks"] if item["id"] == task_id
    ))
    receiver_task = _task_ui(next(
        item for item in domain.board(peer)["tasks"] if item["id"] == task_id
    ))
    assert sender_task["creatorId"] == admin.membership_id
    assert sender_task["ownerId"] == peer.membership_id
    assert sender_task["status"] == "todo"
    assert receiver_task["creatorId"] == admin.membership_id
    assert receiver_task["viewerInboxStatus"] == "pending"
    assert receiver_task["status"] == "inbox"
    returned = domain.handle_inbox(
        peer,
        task_id=task_id,
        action="return",
        expected_version=int(transferred["ownerCollaborator"]["version"]),
        reason="暂不接任",
        idempotency_key="gc04-return-owner",
    )
    assert returned["collaborator"]["inbox_status"] == "returned"
    assert returned["restoredOwnerCollaborator"] is None
    creator_copy = next(
        item for item in domain.board(admin)["tasks"] if item["id"] == task_id
    )
    assert creator_copy["returned_to_creator"] is True

    completed = domain.update_task(
        admin,
        task_id=task_id,
        payload={"expectedVersion": 1, "progressStatus": "done"},
        idempotency_key="gc04-complete",
    )
    assert completed["task"]["version"] == 2
    assert completed["task"]["progress_status"] == "done"
    reopened = domain.update_task(
        admin,
        task_id=task_id,
        payload={"expectedVersion": 2, "progressStatus": "todo"},
        idempotency_key="gc04-reopen",
    )
    assert reopened["task"]["version"] == 3
    assert reopened["task"]["progress_status"] == "todo"
    completed_again = domain.update_task(
        admin,
        task_id=task_id,
        payload={"expectedVersion": 3, "progressStatus": "done"},
        idempotency_key="gc04-complete-again",
    )
    assert completed_again["task"]["version"] == 4
    with pytest.raises(RepositoryError, match="任务已更新"):
        domain.update_task(
            admin,
            task_id=task_id,
            payload={"expectedVersion": 1, "title": "过期覆盖"},
            idempotency_key="gc04-stale",
        )

    proposal = domain.create_agent_proposal(
        admin,
        task_id=task_id,
        payload={
            "expectedVersion": 4,
            "summary": "建议重开",
            "proposedPatch": {"progressStatus": "todo"},
        },
        idempotency_key="gc04-agent-proposal",
    )
    assert proposal["proposal"]["taskWritePerformed"] is False
    assert proposal["proposal"]["status"] == "pending_confirmation"

    with pytest.raises(RepositoryError, match="默认收集箱"):
        domain.create_list(
            admin,
            payload={"name": "默认", "isDefault": True},
            idempotency_key="gc04-default-list",
        )
    task_list = domain.create_list(
        admin,
        payload={"name": "本周推进", "scope": "personal", "color": "#ff0000"},
        idempotency_key="gc04-list",
    )
    assert task_list["colorPersisted"] is False
    list_id = task_list["taskList"]["id"]
    updated_list = domain.update_list(
        admin,
        list_id=list_id,
        payload={"expectedVersion": 1, "name": "本周重点推进", "sortOrder": 2},
        idempotency_key="gc04-update-list",
    )
    assert updated_list["taskList"]["version"] == 2
    assigned = domain.update_task(
        admin,
        task_id=task_id,
        payload={"expectedVersion": 4, "taskListId": list_id},
        idempotency_key="gc04-assign-list",
    )
    assert assigned["task"]["task_list_id"] == list_id
    deleted_list = domain.delete_list(
        admin,
        list_id=list_id,
        expected_version=2,
        idempotency_key="gc04-delete-list",
    )
    assert deleted_list["affectedTaskIds"] == [task_id]

    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
            "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES ('view_gc04_test',?,'task_view','active',1,"
            "'task_view',?,?,NULL,'cloud',?)",
            (admin.scope_id, now, now, admin.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO task_views (id,scope_id,task_list_id,viewer_principal_id,"
            "viewer_membership_id,filter_spec,version,record_kind,filter_spec_schema_version,"
            "lifecycle_state,created_at,updated_at,deleted_at) VALUES "
            "('view_gc04_test',?,NULL,NULL,?,'{\"name\":\"我的真实视图\"}',1,'view',"
            "'gc04.task-view.v1','active',?,?,NULL)",
            (admin.scope_id, admin.membership_id, now, now),
        )
        connection.commit()
    board = domain.board(admin)
    assert [item["id"] for item in board["taskViews"]] == ["view_gc04_test"]
    organization_context = domain.task_context(admin, task_id=task_id)
    assert organization_context["clientId"] is None
    assert organization_context["organizationProjectKnowledge"] == []
    assert organization_context["personalProjectMemory"] == []
    assert organization_context["taskPlanAgent"]["state"] == "not_connected"

    project_task = _create(
        domain,
        admin,
        "gc04-project-task",
        "稳定项目ID任务",
        clientId=seed_payload["projectId"],
    )
    project_context = domain.task_context(
        admin, task_id=project_task["task"]["id"]
    )
    assert project_context["clientId"] == seed_payload["projectId"]
    assert isinstance(project_context["organizationProjectKnowledge"], list)
    assert isinstance(project_context["personalProjectMemory"], list)
    assert project_context["taskPlanAgent"]["canWriteTask"] is False

    deleted = domain.delete_task(
        admin,
        task_id=task_id,
        expected_version=6,
        idempotency_key="gc04-delete-task",
    )
    assert deleted["deleted"] is True
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT lifecycle_state FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0] == "deleted"
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE secured_resource_id=?",
            (task_id,),
        ).fetchone()[0] == 1
        notification = connection.execute(
            "SELECT status,channel,lifecycle_state FROM notification_deliveries"
        ).fetchone()
        assert tuple(notification) == ("blocked", "feishu", "deleted")
        assert connection.execute(
            "SELECT COUNT(*) FROM ai_proposals"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM calendar_entries WHERE task_id=?", (task_id,)
        ).fetchone()[0] >= 6
        assert connection.execute(
            "SELECT COUNT(*) FROM calendar_entries WHERE task_id=? "
            "AND invalidated_at IS NULL",
            (task_id,),
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        structure_after = dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )
    assert structure_after == structure_before


def test_gc05_preflight_is_non_business_write_and_commit_is_per_item_idempotent(
    tmp_path: Path,
) -> None:
    repository, admin, _ = _repository(tmp_path)
    domain = GC04TaskRepository(repository)
    first = _create(domain, admin, "gc05-create-a", "批量A")
    second = _create(domain, admin, "gc05-create-b", "批量B")
    first_id = first["task"]["id"]
    second_id = second["task"]["id"]

    preflight = domain.bulk_preflight(
        admin,
        payload={
            "atomicityMode": "per_item",
            "items": [
                {
                    "itemKey": "a",
                    "taskId": first_id,
                    "expectedVersion": 1,
                    "patch": {"title": "批量A已改"},
                },
                {
                    "itemKey": "b",
                    "taskId": second_id,
                    "expectedVersion": 999,
                    "patch": {"title": "批量B不会改"},
                },
            ],
        },
        idempotency_key="gc05-preflight",
    )
    assert preflight["businessWrites"] == 0
    assert [item["preflightResult"] for item in preflight["items"]] == [
        "ready",
        "conflict",
    ]
    with runtime_connection(repository.database_path, "cloud") as connection:
        versions = dict(
            connection.execute(
                "SELECT id,version FROM tasks WHERE id IN (?,?)", (first_id, second_id)
            ).fetchall()
        )
    assert versions == {first_id: 1, second_id: 1}

    commit = domain.bulk_commit(
        admin,
        bulk_operation_id=preflight["bulkOperationId"],
        payload={"preflightSnapshotHash": preflight["preflightSnapshotHash"]},
        idempotency_key="gc05-commit",
    )
    replay = domain.bulk_commit(
        admin,
        bulk_operation_id=preflight["bulkOperationId"],
        payload={"preflightSnapshotHash": preflight["preflightSnapshotHash"]},
        idempotency_key="gc05-commit",
    )
    assert replay == commit
    assert commit["status"] == "committed_partial"
    assert commit["successCount"] == 1
    assert commit["failureCount"] == 1
    assert [item["result"] for item in commit["items"]] == [
        "succeeded",
        "conflict",
    ]
    with runtime_connection(repository.database_path, "cloud") as connection:
        rows = dict(
            connection.execute(
                "SELECT id,title FROM tasks WHERE id IN (?,?)", (first_id, second_id)
            ).fetchall()
        )
        assert rows[first_id] == "批量A已改"
        assert rows[second_id] == "批量B"
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE command_type='task_bulk.committed'"
        ).fetchone()[0] == 1
        assert connection.execute(
                "SELECT COUNT(*) FROM commands WHERE command_type='task.bulk_updated'"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc04_detached_cloud_routes_register_without_shared_main(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _repository(tmp_path)
    app = FastAPI()

    def current_identity() -> SessionIdentity:
        return identity

    register_gc04_task_routes(app, repository, current_identity)
    paths = {route.path for route in app.routes}
    assert "/api/v2/domain/tasks" in paths
    assert "/api/v2/domain/tasks/{task_id}/timer/{action}" in paths
    assert "/api/v2/domain/tasks/timers/pause-running" in paths
    assert "/api/v2/domain/tasks/{task_id}/agent-proposals" in paths
    assert "/api/v2/domain/task-bulk/preflight" in paths
    assert "/api/v2/domain/task-bulk/{bulk_operation_id}/commit" in paths


def test_task_timer_cloud_commands_are_on_the_connected_golden_chain() -> None:
    for action in ("start", "pause", "stop"):
        assert WorkspaceRuntime._connected_cloud_path_allowed(
            "POST", f"/api/v2/domain/tasks/task-1/timer/{action}"
        )
    assert not WorkspaceRuntime._connected_cloud_path_allowed(
        "POST", "/api/v2/domain/tasks/task-1/timer/reset"
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST", "/api/v2/domain/tasks/timers/pause-running"
    )


def test_task_timer_uses_execution_runs_and_is_idempotent(tmp_path: Path) -> None:
    repository, identity, _ = _repository(tmp_path)
    domain = GC04TaskRepository(repository)
    task = _create(domain, identity, "timer-task-create", "记录真实投入时间")
    task_id = str(task["task"]["id"])

    assert task["task"]["task_timer"]["state"] == "idle"
    assert task["task"]["task_timer"]["version"] == 0

    started = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-start-1",
    )
    replay = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-start-1",
    )
    assert replay == started
    assert started["taskTimer"]["state"] == "running"
    assert started["taskTimer"]["version"] == 1

    repeated_start = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-start-repeated-click",
    )
    assert repeated_start["taskTimer"]["state"] == "running"
    assert repeated_start["taskTimer"]["version"] == 1

    ten_seconds_ago = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "UPDATE execution_runs SET started_at=? WHERE id=?",
            (ten_seconds_ago, started["taskTimer"]["latestRunId"]),
        )
        connection.commit()

    paused = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="pause",
        expected_timer_version=1,
        idempotency_key="timer-pause-1",
    )
    assert paused["taskTimer"]["state"] == "paused"
    assert paused["taskTimer"]["version"] == 2
    assert paused["taskTimer"]["elapsedSeconds"] >= 9

    resumed = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="start",
        expected_timer_version=2,
        idempotency_key="timer-resume-1",
    )
    stopped = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="stop",
        expected_timer_version=3,
        idempotency_key="timer-stop-1",
    )
    assert resumed["taskTimer"]["state"] == "running"
    assert stopped["taskTimer"]["state"] == "stopped"
    assert stopped["taskTimer"]["version"] == 4
    assert stopped["taskTimer"]["elapsedSeconds"] >= paused["taskTimer"]["elapsedSeconds"]

    peer = _second_member(repository, identity)
    with pytest.raises(RepositoryError) as error:
        domain.update_task_timer(
            peer,
            task_id=task_id,
            action="start",
            expected_timer_version=0,
            idempotency_key="timer-peer-forbidden",
        )
    # Non-participants must not learn that the task exists through the timer API.
    assert error.value.code == "task_missing"

    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs WHERE task_id=? AND run_kind='task_focus_session'",
            (task_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE aggregate_id=? "
            "AND command_type LIKE 'task.timer_%'",
            (task_id,),
        ).fetchone()[0] == 5
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE target_resource_id=? "
            "AND action LIKE 'task.timer_%'",
            (task_id,),
        ).fetchone()[0] == 5
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_task_timer_keeps_rapid_segments_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, identity, _ = _repository(tmp_path)
    domain = GC04TaskRepository(repository)
    task = _create(domain, identity, "timer-rapid-create", "快速暂停恢复")
    task_id = str(task["task"]["id"])
    fixed_now = "2026-08-24T10:00:00.000Z"
    monkeypatch.setattr(gc04_tasks_repository, "utc_now", lambda: fixed_now)

    started = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-rapid-start",
    )
    paused = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="pause",
        expected_timer_version=int(started["taskTimer"]["version"]),
        idempotency_key="timer-rapid-pause",
    )
    resumed = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="start",
        expected_timer_version=int(paused["taskTimer"]["version"]),
        idempotency_key="timer-rapid-resume",
    )
    stopped = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="stop",
        expected_timer_version=int(resumed["taskTimer"]["version"]),
        idempotency_key="timer-rapid-stop",
    )

    assert resumed["taskTimer"]["state"] == "running"
    assert stopped["taskTimer"]["state"] == "stopped"
    assert stopped["taskTimer"]["version"] == 4
    with runtime_connection(repository.database_path, "cloud") as connection:
        created_at = [
            str(row["created_at"])
            for row in connection.execute(
                "SELECT created_at FROM execution_runs WHERE task_id=? "
                "ORDER BY created_at,id",
                (task_id,),
            ).fetchall()
        ]
    assert created_at == [fixed_now, "2026-08-24T10:00:00.001Z"]


def test_desktop_close_pauses_only_current_members_running_timers(
    tmp_path: Path,
) -> None:
    repository, owner, _ = _repository(tmp_path)
    peer = _second_member(repository, owner)
    domain = GC04TaskRepository(repository)
    created = _create(
        domain,
        owner,
        "timer-close-create",
        "退出软件时暂停个人用时",
        ownerMembershipId=owner.membership_id,
        collaboratorMembershipIds=[peer.membership_id],
        visibilityScope="participants",
    )
    task_id = str(created["task"]["id"])
    peer_assignment = next(
        item
        for item in created["task"]["collaborators"]
        if item["subject_membership_id"] == peer.membership_id
    )
    domain.handle_inbox(
        peer,
        task_id=task_id,
        action="accept",
        expected_version=int(peer_assignment["version"]),
        reason=None,
        idempotency_key="timer-close-peer-accept",
    )
    domain.update_task_timer(
        owner,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-close-owner-start",
    )
    domain.update_task_timer(
        peer,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-close-peer-start",
    )

    paused = domain.pause_running_task_timers(
        owner,
        reason="app_quit",
        idempotency_key="timer-close-owner-pause",
    )
    replay = domain.pause_running_task_timers(
        owner,
        reason="app_quit",
        idempotency_key="timer-close-owner-pause",
    )

    assert replay == paused
    assert paused["pausedCount"] == 1
    assert paused["taskIds"] == [task_id]
    assert domain.task_detail(owner, task_id=task_id)["task"]["task_timer"]["state"] == "paused"
    assert domain.task_detail(peer, task_id=task_id)["task"]["task_timer"]["state"] == "running"


def test_completing_task_pauses_every_running_personal_timer(
    tmp_path: Path,
) -> None:
    repository, owner, _ = _repository(tmp_path)
    peer = _second_member(repository, owner)
    domain = GC04TaskRepository(repository)
    created = _create(
        domain,
        owner,
        "timer-complete-create",
        "完成任务时收口个人用时",
        ownerMembershipId=owner.membership_id,
        collaboratorMembershipIds=[peer.membership_id],
        visibilityScope="participants",
    )
    task_id = str(created["task"]["id"])
    peer_assignment = next(
        item
        for item in created["task"]["collaborators"]
        if item["subject_membership_id"] == peer.membership_id
    )
    domain.handle_inbox(
        peer,
        task_id=task_id,
        action="accept",
        expected_version=int(peer_assignment["version"]),
        reason=None,
        idempotency_key="timer-complete-peer-accept",
    )
    domain.update_task_timer(
        owner,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-complete-owner-start",
    )
    domain.update_task_timer(
        peer,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-complete-peer-start",
    )
    current = domain.task_detail(owner, task_id=task_id)["task"]
    domain.update_task(
        owner,
        task_id=task_id,
        payload={
            "expectedVersion": int(current["version"]),
            "progressStatus": "done",
        },
        idempotency_key="timer-complete-task",
    )

    assert domain.task_detail(owner, task_id=task_id)["task"]["task_timer"]["state"] == "paused"
    assert domain.task_detail(peer, task_id=task_id)["task"]["task_timer"]["state"] == "paused"


class _ProjectionRuntime:
    def __init__(self, database_path: Path, context: object):
        self.database_path = database_path
        self._context_value = context

    def _current_context(self, *, require_ready: bool) -> object:
        assert require_ready is True
        return self._context_value

    def _connection(self):
        return runtime_connection(self.database_path, "local")

    @staticmethod
    def _local_object_scope_id(connection: object, sandbox_id: str) -> str:
        row = connection.execute(
            "SELECT scope_id FROM sandboxes WHERE id=?", (sandbox_id,)
        ).fetchone()
        return str(row[0])


def test_gc04_local_projection_uses_stable_ids_and_marks_missing_snapshot_stale(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    db_identity = initialize_database(database, "local")
    now = utc_now()
    scope_id = "scope_gc04_local"
    sandbox_id = "sandbox_gc04_local"
    with runtime_connection(database, "local") as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,record_kind,"
            "name,created_at,deleted_at,projection_state,projected_at) VALUES "
            "('org_gc04_local','active',1,?,'organization','GC04本机组织',?,NULL,'current',?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,policy_version,"
            "created_at,updated_at,status,version,lifecycle_state,deleted_at,projection_state,"
            "projected_at) VALUES (?,'organization','org_gc04_local',1,?,?,'active',1,"
            "'active',NULL,'current',?)",
            (scope_id, now, now, now),
        )
        for principal_id, membership_id, name, role in (
            ("principal_gc04_local", "membership_gc04_local", "本机成员", "admin"),
            ("principal_gc04_local_peer", "membership_gc04_local_peer", "本机协作者", "member"),
        ):
            connection.execute(
                "INSERT INTO principals (id,status,identity_version,updated_at,principal_kind,"
                "display_name,version,lifecycle_state,created_at,deleted_at,projection_state,"
                "projected_at) VALUES (?,'active',1,?,'person',?,1,'active',?,NULL,'current',?)",
                (principal_id, now, name, now, now),
            )
            connection.execute(
                "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,"
                "version,record_kind,visibility_scope,lifecycle_state,created_at,updated_at,"
                "deleted_at,projection_state,projected_at) VALUES (?,?,?,?,'active',1,"
                "'membership','organization','active',?,?,NULL,'current',?)",
                (membership_id, scope_id, principal_id, role, now, now, now),
            )
        connection.execute(
            "INSERT INTO sandboxes (id,scope_id,principal_id,membership_id,record_kind,"
            "cloud_instance_id,database_generation_id,sandbox_kind,display_name,runtime_status,"
            "manifest_hash,version,lifecycle_state,created_at,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES (?,?,?,?,'sandbox','cli_gc04_local',?,"
            "'organization','GC04工作空间','ready',?,1,'active',?,?,NULL,'local',?)",
            (
                sandbox_id,
                scope_id,
                "principal_gc04_local",
                "membership_gc04_local",
                db_identity.database_generation_id,
                db_identity.manifest_hash,
                now,
                now,
                db_identity.database_generation_id,
            ),
        )
        connection.commit()
    context = SimpleNamespace(
        sandbox_id=sandbox_id,
        membership_id="membership_gc04_local",
        cloud_instance_id="cli_gc04_local",
    )
    projector = LocalGC04TaskProjection(_ProjectionRuntime(database, context))
    task_id = "task_gc04_projection"
    projection = {
        "tasks": [
            {
                "id": task_id,
                "scope_id": "cloud_scope_must_not_leak",
                "creator_membership_id": "membership_gc04_local",
                "lifecycle_state": "active",
                "version": 3,
                "title": "投影任务",
                "priority": "normal",
                "visibility_scope": "participants",
                "created_at": now,
                "updated_at": now,
            }
        ],
        "task_collaborators": [
            {
                "id": "task_member_gc04_projection",
                "scope_id": "cloud_scope_must_not_leak",
                "task_id": task_id,
                "subject_membership_id": "membership_gc04_local_peer",
                "role_key": "collaborator",
                "assignment_state": "assigned",
                "inbox_status": "pending",
                "version": 1,
                "lifecycle_state": "active",
                "created_at": now,
                "updated_at": now,
            }
        ],
        "calendar_entries": [
            {
                "id": "cal_gc04_projection",
                "scope_id": "cloud_scope_must_not_leak",
                "task_id": task_id,
                "starts_at": "2026-08-08T09:00:00Z",
                "version": 3,
                "target_kind": "task",
                "source_version": 3,
                "generated_at": now,
            }
        ],
        "execution_runs": [
            {
                "id": "task_focus_gc04_projection",
                "scope_id": "cloud_scope_must_not_leak",
                "task_id": task_id,
                "status": "running",
                "initiator_membership_id": "membership_gc04_local",
                "run_kind": "task_focus_session",
                "started_at": now,
                "version": 1,
                "lifecycle_state": "active",
                "created_at": now,
                "updated_at": now,
            }
        ],
    }
    applied = projector.apply(projection)
    assert applied["counts"]["tasks"] == 1
    assert applied["counts"]["execution_runs"] == 1
    assert projector.task_version(task_id) == 3
    with runtime_connection(database, "local") as connection:
        row = connection.execute(
            "SELECT scope_id,sandbox_id,source_version,projection_state FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        assert tuple(row) == (scope_id, sandbox_id, 3, "current")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    # A non-admin cold sandbox can receive a visible task before its referenced
    # client/event-line/member projections.  The cloud task remains usable by
    # consumers; the local cache row is deferred without inventing a parent or
    # invalidating the last confirmed snapshot.
    deferred = projector.apply(
        {
            "tasks": [
                {
                    "id": "task_gc04_missing_dependency",
                    "creator_membership_id": "membership_gc04_local",
                    "client_id": "client_not_projected_yet",
                    "lifecycle_state": "active",
                    "version": 1,
                    "title": "依赖尚未到达的可见任务",
                    "created_at": now,
                    "updated_at": now,
                }
            ]
        },
        replace_snapshot=True,
    )
    assert deferred["state"] == "pending_retry"
    assert deferred["deferred"] == {
        "tasks": ["task_gc04_missing_dependency"]
    }
    with runtime_connection(database, "local") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE id='task_gc04_missing_dependency'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT projection_state FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0] == "current"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    projector.apply({}, replace_snapshot=True)
    with runtime_connection(database, "local") as connection:
        assert connection.execute(
            "SELECT projection_state FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0] == "stale"
        assert connection.execute(
            "SELECT invalidated_at IS NOT NULL FROM calendar_entries WHERE id=?",
            ("cal_gc04_projection",),
        ).fetchone()[0] == 1


@pytest.mark.parametrize("role", ["local", "cloud"])
def test_gc04_gc05_strict_schema_stays_exactly_88_tables(
    tmp_path: Path, role: str
) -> None:
    database = tmp_path / f"strict-{role}.db"
    initialize_database(database, role)
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "contracts" / f"strict-{role}-schema-manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    allowed = {str(item["name"]) for item in manifest["allowedTables"]}
    with runtime_connection(database, role) as connection:
        before = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        }
        assert len(before) == 88
        assert set(before) == allowed
        for table in before:
            assert {
                str(row[2])
                for row in connection.execute(
                    f'PRAGMA foreign_key_list("{table}")'
                ).fetchall()
            } <= allowed
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        after = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        }
    assert after == before
