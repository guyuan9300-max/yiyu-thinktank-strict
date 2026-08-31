from __future__ import annotations

from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from backend.app.ui_domains import gc06_planning as gc06_ui_domain
from backend.app.ui_domains.gc06_planning import (
    _basic_review_dashboard,
    _apply_weekly_event_grouping_overrides,
    _enforce_weekly_explicit_event_groups,
    _event_line_ui,
    _event_line_narrative,
    _event_line_timeline_nodes,
    _merge_review_task_entries,
    _normalize_weekly_event_cards,
    _weekly_review_evidence_packs,
    router as gc06_ui_router,
)
from backend.app.ui_domains.workflow import router as workflow_ui_router
from backend.app.ui_domains.routing import UiRequest
from backend.app.gc06_planning_local import LocalGC06PlanningProjection
from backend.app.runtime import LocalRuntimeError, WorkspaceRuntime
from cloud_backend.app.domain_routes.gc06_planning import register_gc06_planning_routes
from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repositories.gc06_planning import (
    attach_task_to_event_line,
    clients_pulse,
    convert_action_to_primary_task,
    create_decision_action,
    create_event_line,
    create_meeting,
    create_planning_cycle,
    delete_planning_cycle,
    derive_task_calendar_projection,
    event_line_detail,
    list_calendar_entries,
    list_event_lines,
    list_planning_cycles,
    list_plan_item_tasks,
    migrate_meeting_to_task,
    get_task_plan_link,
    merge_event_lines,
    reparent_event_line,
    save_weekly_review_draft,
    transition_event_line,
    transition_meeting_collaboration,
    transition_weekly_review,
    update_decision_action,
    update_event_line,
    update_meeting,
    update_planning_cycle,
    set_task_plan_link,
)
from cloud_backend.app.repositories.gc06_task_command_port import (
    GC04_FORMAL_TASK_COMMAND_PORT,
)
from cloud_backend.app.repositories.gc13_growth import _sync_weekly_formal_evidence
from cloud_backend.app.repositories.project_materials import GC07ProjectMaterialsRepository
from cloud_backend.app.repository import RepositoryError
from strict_common.ids import sha256_text, utc_now
from strict_common.schema import initialize_database, runtime_connection
from tests.test_gc04_gc05_tasks import _ProjectionRuntime
from tests.test_gc14_workbench_answer import _repository


def _schema_fingerprint(connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,COALESCE(sql,'') FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
    ).fetchall()
    return sha256_text(repr([tuple(row) for row in rows]))


def _create_line(repository, identity, client_id: str, *, suffix: str = "one"):
    return create_event_line(
        repository,
        identity,
        payload={
            "eventLineId": f"event_line_gc06_{suffix}",
            "clientId": client_id,
            "name": f"GC-06 事件线 {suffix}",
            "goal": "完成计划闭环",
        },
        idempotency_key=f"gc06-event-line-{suffix}",
    )["eventLine"]


def _seed_event_line_participant(repository, identity, *, suffix: str) -> str:
    principal_id = f"principal_event_line_{suffix}"
    membership_id = f"membership_event_line_{suffix}"
    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,principal_kind,"
            "display_name,version,lifecycle_state,created_at,deleted_at) VALUES "
            "(?,'active',1,?,'person',?,1,'active',?,NULL)",
            (principal_id, now, f"事件线参与者 {suffix}", now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,"
            "version,record_kind,visibility_scope,lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,'member','active',1,'membership','organization','active',?,?,NULL)",
            (membership_id, identity.scope_id, principal_id, now, now),
        )
        connection.commit()
    return membership_id


def test_retained_review_dashboard_uses_only_strict_review_plan_and_task_facts() -> None:
    dashboard = _basic_review_dashboard(
        cycles=[{
            "id": "cycle_review_dashboard",
            "recordKind": "organization_plan",
            "title": "本周计划",
            "summary": "",
            "status": "published",
            "ownerMembershipId": "membership_review_dashboard",
            "periodStart": "2026-08-01",
            "periodEnd": "2026-08-31",
        }],
        reviews=[{
            "id": "review_dashboard",
            "membershipId": "membership_review_dashboard",
            "planningCycleId": "cycle_review_dashboard",
            "currentDraftVersionId": "review_version_dashboard",
            "currentSubmittedVersionId": None,
            "createdAt": "2026-08-06T09:00:00Z",
            "updatedAt": "2026-08-06T09:00:00Z",
            "versions": [{
                "id": "review_version_dashboard",
                "businessState": "draft",
                "content": {"summary": "完成了真实复盘草稿"},
                "createdAt": "2026-08-06T09:00:00Z",
            }],
        }],
        task_rows=[{
            "id": "task_review_dashboard",
            "title": "本周真实任务",
            "description": "",
            "priority": "normal",
            "due_date": "2026-08-07",
            "visibility_scope": "organization",
            "created_at": "2026-08-05T09:00:00Z",
            "updated_at": "2026-08-05T09:00:00Z",
            "version": 1,
            "collaborators": [{
                "subject_membership_id": "membership_review_dashboard",
                "display_name": "复盘成员",
                "role_key": "owner",
                "inbox_status": "accepted",
                "version": 1,
            }],
        }],
        membership_id="membership_review_dashboard",
        user_name="复盘成员",
        requested_week="2026-W32",
    )
    assert dashboard["currentReview"]["workProgress"] == "完成了真实复盘草稿"
    assert [item["taskId"] for item in dashboard["workItems"]] == [
        "task_review_dashboard"
    ]
    assert dashboard["workAnalysis"] is None
    assert dashboard["weeklyMainlineCards"] is None
    assert dashboard["weeklyOverviewGenerationStatus"]["status"] == "idle"
    empty = _basic_review_dashboard(
        cycles=[],
        reviews=[],
        task_rows=[],
        membership_id="membership_review_dashboard",
        user_name="复盘成员",
        requested_week="2026-W32",
    )
    assert empty["currentReview"] is None
    assert empty["workItems"] == []
    assert empty["personalItems"] == []
    assert empty["plans"] == []

    merged = _merge_review_task_entries(
        {"taskEntries": [
            {"taskId": "task_kept", "note": "保留"},
            {"taskId": "task_updated", "note": "旧值"},
        ]},
        [{"taskId": "task_updated", "note": "新值"}],
    )
    assert {item["taskId"]: item["note"] for item in merged} == {
        "task_kept": "保留",
        "task_updated": "新值",
    }


def test_retained_review_dashboard_finds_review_period_without_formal_plan() -> None:
    dashboard = _basic_review_dashboard(
        cycles=[{
            "id": "review_period_hidden",
            "recordKind": "cycle",
            "periodKind": "weekly_review",
            "periodStart": "2026-08-24",
            "periodEnd": "2026-08-30",
            "title": "2026-W35 复盘周期",
        }],
        reviews=[{
            "id": "review_without_plan",
            "membershipId": "membership_review_dashboard",
            "planningCycleId": "review_period_hidden",
            "currentDraftVersionId": "review_version_without_plan",
            "currentSubmittedVersionId": None,
            "createdAt": "2026-08-27T09:00:00Z",
            "updatedAt": "2026-08-27T09:00:00Z",
            "versions": [{
                "id": "review_version_without_plan",
                "businessState": "draft",
                "content": {
                    "weekLabel": "2026-W35",
                    "summary": "没有计划也能复盘",
                },
                "createdAt": "2026-08-27T09:00:00Z",
            }],
        }],
        task_rows=[],
        membership_id="membership_review_dashboard",
        user_name="复盘成员",
        requested_week="2026-W35",
    )
    assert dashboard["currentReview"]["workProgress"] == "没有计划也能复盘"
    assert dashboard["currentReview"]["relatedPlanIds"] == []
    assert dashboard["plans"] == []


def test_weekly_review_can_be_saved_without_formal_plan(tmp_path: Path) -> None:
    repository, identity, _ = _repository(tmp_path)
    first = save_weekly_review_draft(
        repository,
        identity,
        payload={
            "reviewId": "weekly_review_without_plan",
            "weekLabel": "2026-W35",
            "content": {"summary": "计划只是可选参考"},
        },
        idempotency_key="gc06-weekly-review-without-plan-v1",
    )["weeklyReview"]
    assert first["planningCycleId"].startswith("review_period_")
    assert first["versions"][0]["content"]["weekLabel"] == "2026-W35"

    second = save_weekly_review_draft(
        repository,
        identity,
        payload={
            "planningCycleId": first["planningCycleId"],
            "weekLabel": "2026-W35",
            "expectedVersion": first["version"],
            "content": {"summary": "无计划也能继续保存"},
        },
        idempotency_key="gc06-weekly-review-without-plan-v2",
    )["weeklyReview"]
    assert second["id"] == first["id"]
    assert second["versions"][-1]["content"]["summary"] == "无计划也能继续保存"
    assert list_planning_cycles(repository, identity) == []
    review_periods = list_planning_cycles(
        repository,
        identity,
        include_review_periods=True,
    )
    assert [(item["recordKind"], item["periodKind"]) for item in review_periods] == [
        ("cycle", "weekly_review")
    ]
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_retained_review_save_does_not_require_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gc06_ui_domain,
        "_retained_review_sources",
        lambda *_args, **_kwargs: ([], [], {"tasks": []}),
    )
    monkeypatch.setattr(
        gc06_ui_domain,
        "_retained_dashboard",
        lambda *_args, **_kwargs: {"weekLabel": "2026-W35"},
    )
    captured = {}

    class Runtime:
        @staticmethod
        def _current_context(*, require_ready: bool):
            assert require_ready is True
            return SimpleNamespace(membership_id="membership_without_plan")

        @staticmethod
        def cloud_command(method, path, *, payload, **_kwargs):
            captured.update({"method": method, "path": path, "payload": payload})
            return {
                "weeklyReview": {
                    "id": "review_without_plan",
                    "version": 1,
                }
            }

    result = gc06_ui_domain._save_retained_review(
        SimpleNamespace(runtime=Runtime()),
        UiRequest(
            method="POST",
            path="reviews/weekly/draft",
            query={},
            body={"weekLabel": "2026-W35", "workFreeNote": "直接复盘"},
            idempotency_key="weekly-review-without-plan-ui",
        ),
        submit=False,
    )
    assert result == {"weekLabel": "2026-W35"}
    assert captured["payload"]["weekLabel"] == "2026-W35"
    assert "planningCycleId" not in captured["payload"]


def test_weekly_event_agent_grouping_does_not_use_date_as_a_split_or_merge_rule() -> None:
    tasks = [
        {
            "taskId": "task_date_prepare",
            "title": "准备日期验证样本",
            "scheduledStartAt": "2026-08-25T09:00:00Z",
            "eventLineId": None,
        },
        {
            "taskId": "task_date_verify",
            "title": "复核日期验证结果",
            "scheduledStartAt": "2026-08-27T09:00:00Z",
            "eventLineId": None,
        },
        {
            "taskId": "task_unrelated_same_day",
            "title": "整理客户会议名单",
            "scheduledStartAt": "2026-08-27T10:00:00Z",
            "eventLineId": None,
        },
    ]
    payload, groups = _normalize_weekly_event_cards(
        [{
            "title": "日期验证",
            "taskIds": ["task_date_prepare", "task_date_verify"],
            "groupReason": "两项任务共同完成一次验证工作的准备与复核",
            "confidence": "high",
            "reflectionPromptText": "这次验证最终确认了什么？",
        }],
        tasks=tasks,
        week_label="2026-W35",
    )
    assert groups[0]["taskIds"] == ["task_date_prepare", "task_date_verify"]
    assert groups[0]["cardKind"] == "task_cluster"
    assert groups[1]["taskIds"] == ["task_unrelated_same_day"]
    assert groups[1]["cardKind"] == "needs_assignment"
    assert payload["evidenceMeta"]["schemaVersion"] == "weekly_review_event_agent_v4"


def test_weekly_event_unlinked_siblings_wait_for_manual_merge() -> None:
    tasks = [
        {
            "taskId": "task_multi_date",
            "title": "日期验证｜多日仅日期",
            "eventLineId": None,
        },
        {
            "taskId": "task_single_date",
            "title": "日期验证｜单日仅日期",
            "eventLineId": None,
        },
        {
            "taskId": "task_feishu",
            "title": "飞书字段内联修改验收｜20260825",
            "eventLineId": None,
        },
    ]
    groups = _enforce_weekly_explicit_event_groups(
        [
            {"title": "日期验证", "taskIds": ["task_multi_date", "task_single_date"]},
            {"title": "飞书验收", "taskIds": ["task_feishu"]},
        ],
        tasks=tasks,
    )
    assert [item["taskIds"] for item in groups] == [
        ["task_multi_date"],
        ["task_single_date"],
        ["task_feishu"],
    ]
    assert all("成员决定" in item["groupReason"] for item in groups[:2])


def test_weekly_review_evidence_pack_joins_task_plan_project_event_and_review() -> None:
    packs = _weekly_review_evidence_packs(
        event_groups=[{
            "id": "event_group_one",
            "title": "试点验证",
            "taskIds": ["task_one"],
            "groupReason": "共同交付物",
            "confidence": "high",
        }],
        tasks=[{
            "taskId": "task_one",
            "title": "复核试点数据",
            "planningCycleId": "plan_one",
            "clientId": "project_one",
            "eventLineId": "event_line_one",
        }],
        plans=[{"id": "plan_one", "title": "本周试点计划"}],
        project_contexts=[{"clientId": "project_one", "sources": [{"id": "knowledge_one"}]}],
        event_contexts=[{"eventLineId": "event_line_one", "goal": "完成试点验证"}],
        current_review={"workFreeNote": "本周已完成第一轮核对"},
    )
    assert packs == [{
        "eventGroupId": "event_group_one",
        "eventTitle": "试点验证",
        "groupReason": "共同交付物",
        "confidence": "high",
        "tasks": [{
            "taskId": "task_one",
            "title": "复核试点数据",
            "planningCycleId": "plan_one",
            "clientId": "project_one",
            "eventLineId": "event_line_one",
        }],
        "linkedPlans": [{"id": "plan_one", "title": "本周试点计划"}],
        "linkedProjects": [{"clientId": "project_one", "sources": [{"id": "knowledge_one"}]}],
        "linkedEventLines": [{"eventLineId": "event_line_one", "goal": "完成试点验证"}],
        "memberReview": {"workFreeNote": "本周已完成第一轮核对"},
    }]


def test_weekly_event_manual_grouping_saves_without_plan_or_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = {
        "weekLabel": "2026-W35",
        "activePerspective": "mine",
        "activeDepartmentId": None,
        "workItems": [
            {"taskId": "task_multi", "taskSnapshot": {"title": "日期验证｜多日仅日期", "status": "done"}},
            {"taskId": "task_single", "taskSnapshot": {"title": "日期验证｜单日仅日期", "status": "done"}},
        ],
        "weeklyOverviewGenerationStatus": {
            "weekLabel": "2026-W35",
            "perspective": "mine",
            "departmentId": None,
            "viewerUserId": "membership_one",
            "status": "idle",
        },
    }
    monkeypatch.setattr(gc06_ui_domain, "_retained_dashboard", lambda *_args, **_kwargs: dashboard)
    saved: dict[str, object] = {}

    class Projector:
        @staticmethod
        def load_weekly_overview(**_kwargs):
            return None

        @staticmethod
        def save_weekly_overview(**kwargs):
            saved.update(kwargs)
            return {}

    monkeypatch.setattr(gc06_ui_domain, "_planning_projector", lambda _compatibility: Projector())
    result = gc06_ui_domain._save_weekly_event_grouping(
        SimpleNamespace(runtime=object()),
        UiRequest(
            method="POST",
            path="reviews/weekly-overview/refresh",
            query={},
            body={
                "weekLabel": "2026-W35",
                "perspective": "mine",
                "groupingOnly": True,
                "eventGroupingOverrides": [{
                    "id": "human-date",
                    "title": "日期验证",
                    "taskIds": ["task_multi", "task_single"],
                }],
            },
            idempotency_key="manual-grouping-without-plan",
        ),
    )
    assert result["status"] == "succeeded"
    event_cards = saved["payload"]["eventCards"]["cards"]
    assert event_cards[0]["taskIds"] == ["task_multi", "task_single"]
    assert event_cards[0]["generatedBy"] == "human"


def test_member_event_grouping_override_replaces_agent_group_without_new_table() -> None:
    tasks = [
        {"taskId": "task_one", "title": "任务一", "eventLineId": None},
        {"taskId": "task_two", "title": "任务二", "eventLineId": None},
    ]
    original_payload, original_groups = _normalize_weekly_event_cards(
        [{
            "title": "Agent 归并",
            "taskIds": ["task_one", "task_two"],
            "groupReason": "模型判断",
            "confidence": "medium",
            "reflectionPromptText": "请复盘",
        }],
        tasks=tasks,
        week_label="2026-W35",
    )
    payload, groups = _apply_weekly_event_grouping_overrides(
        original_payload,
        original_groups,
        overrides=[
            {"id": "human_one", "title": "任务一", "taskIds": ["task_one"]},
            {"id": "human_two", "title": "任务二", "taskIds": ["task_two"]},
        ],
        tasks=tasks,
        week_label="2026-W35",
    )
    assert [item["taskIds"] for item in groups] == [["task_one"], ["task_two"]]
    assert all(item["generatedBy"] == "human" for item in groups)
    assert payload["generatedBy"] == "human"


def test_weekly_review_generation_uses_event_then_evidence_pack_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dashboard = {
        "weekLabel": "2026-W35",
        "activePerspective": "mine",
        "activeDepartmentId": None,
        "activeDepartmentName": None,
        "currentReview": {"id": "review_one", "workFreeNote": "验证需要形成结论"},
        "plans": [{"id": "plan_one", "title": "验证计划"}],
        "workItems": [
            {
                "taskId": "task_prepare",
                "taskSnapshot": {
                    "title": "准备日期验证样本",
                    "description": "用于确认任务详情是否被读取并进入复盘证据包。",
                    "status": "done",
                    "scheduledStartAt": "2026-08-25T09:00:00Z",
                    "clientId": "project_one",
                    "clientName": "试点项目",
                    "planningCycleId": "plan_one",
                },
                "note": "已准备样本",
                "structuredNote": {},
            },
            {
                "taskId": "task_verify",
                "taskSnapshot": {
                    "title": "复核日期验证结果",
                    "status": "done",
                    "scheduledStartAt": "2026-08-27T09:00:00Z",
                    "clientId": "project_one",
                    "clientName": "试点项目",
                    "planningCycleId": "plan_one",
                },
                "note": "完成复核",
                "structuredNote": {},
            },
        ],
        "weeklyOverviewGenerationStatus": {
            "weekLabel": "2026-W35",
            "perspective": "mine",
            "departmentId": None,
            "viewerUserId": "membership_one",
            "status": "idle",
        },
    }
    monkeypatch.setattr(gc06_ui_domain, "_retained_dashboard", lambda *_args, **_kwargs: dashboard)
    completions = []

    class Runtime:
        @staticmethod
        def project_knowledge_context(project_id: str):
            assert project_id == "project_one"
            return {
                "savedMemories": [{
                    "id": "knowledge_one",
                    "title": "项目目标",
                    "summary": "需要验证日期处理是否可靠。",
                }]
            }

        @staticmethod
        def private_ai_completion(**kwargs):
            completions.append(kwargs)
            if len(completions) == 1:
                return {
                    "content": '{"eventGroups":[{"title":"日期验证","taskIds":["task_prepare","task_verify"],'
                    '"groupReason":"共同完成一次验证","confidence":"high",'
                    '"reflectionPromptText":"这次验证确认了什么？"}]}',
                    "modelName": "grouping-model",
                }
            return {
                "content": '{"summaryText":"本周完成了日期验证并形成可靠性判断。","mainlines":[{'
                '"title":"日期验证","taskIds":["task_prepare","task_verify"],'
                '"narrativeText":"准备样本与结果复核共同完成了一轮验证，项目知识表明其目标是确认日期处理可靠性。",'
                '"nextMoveText":"把结论写入验收标准。","openQuestions":[],"evidenceRefs":['
                '{"type":"plan","id":"plan_one","label":"验证计划"}]}]}',
                "modelName": "review-model",
            }

    saved = {}

    class Projector:
        @staticmethod
        def load_weekly_overview(**_kwargs):
            return None

        @staticmethod
        def save_weekly_overview(**kwargs):
            saved.update(kwargs)
            return {}

    monkeypatch.setattr(gc06_ui_domain, "_planning_projector", lambda _compatibility: Projector())
    result = gc06_ui_domain._generate_weekly_overview(
        SimpleNamespace(runtime=Runtime()),
        UiRequest(
            method="POST",
            path="reviews/weekly-overview/refresh",
            query={},
            body={"weekLabel": "2026-W35", "perspective": "mine", "force": True},
            idempotency_key="weekly-review-agent-v2",
        ),
    )
    assert result["status"] == "succeeded"
    assert len(completions) == 2
    assert "日期只用于核验" in completions[0]["system_prompt"]
    assert "用于确认任务详情是否被读取" in completions[0]["prompt"]
    assert "evidencePacks" in completions[1]["prompt"]
    assert "用于确认任务详情是否被读取" in completions[1]["prompt"]
    assert "summaryText 是最重要的输出" in completions[1]["system_prompt"]
    assert "previousWeek" in completions[1]["prompt"]
    assert [card["taskIds"] for card in saved["payload"]["eventCards"]["cards"]] == [
        ["task_prepare"],
        ["task_verify"],
    ]
    mainline = saved["payload"]["cards"]["mainlines"][0]
    assert mainline["narrativeText"].startswith("准备样本")
    assert "whyText" not in mainline
    assert saved["payload"]["cards"]["evidenceMeta"]["schemaVersion"] == "weekly_review_agent_v3"


def test_clients_pulse_uses_visible_strict_project_activity(tmp_path: Path) -> None:
    repository, identity, seed = _repository(tmp_path)
    GC04TaskRepository(repository).create_task(
        identity,
        payload={
            "title": "客户脉搏任务",
            "priority": "normal",
            "clientId": seed["projectId"],
            "dueDate": "2026-08-01",
        },
        idempotency_key="gc06-clients-pulse-task",
    )
    result = clients_pulse(repository, identity)
    item = next(value for value in result["summaries"] if value["clientId"] == seed["projectId"])
    assert item["weeklyNewTaskCount"] == 1
    assert item["overdueTodoCount"] == 1
    assert item["hasActivity"] is True


def test_event_line_reparent_moves_formal_task_to_target_client(tmp_path: Path) -> None:
    repository, identity, seed = _repository(tmp_path)
    target = GC07ProjectMaterialsRepository(repository).create_project(
        identity,
        payload={"name": "目标客户项目"},
        idempotency_key="gc06-reparent-target-project",
    )["project"]
    line = _create_line(repository, identity, seed["projectId"], suffix="reparent")
    task = GC04TaskRepository(repository).create_task(
        identity,
        payload={"title": "随事件线迁移", "clientId": seed["projectId"]},
        idempotency_key="gc06-reparent-task",
    )["task"]
    attached = attach_task_to_event_line(
        repository,
        identity,
        event_line_id=line["id"],
        task_id=task["id"],
        expected_task_version=task["version"],
        allow_reassign=False,
        idempotency_key="gc06-reparent-attach",
        task_command_port=GC04_FORMAL_TASK_COMMAND_PORT,
    )
    detail_task = event_line_detail(
        repository, identity, event_line_id=line["id"]
    )["tasks"][0]
    assert detail_task["title"] == "随事件线迁移"
    assert detail_task["viewer_surfaces"]["event_line_detail"] is True
    assert detail_task["viewer_capabilities"]["can_view"] is True
    result = reparent_event_line(
        repository,
        identity,
        event_line_id=line["id"],
        target_client_id=target["projectId"],
        expected_version=line["version"],
        idempotency_key="gc06-reparent-command",
        task_command_port=GC04_FORMAL_TASK_COMMAND_PORT,
    )
    assert result["eventLine"]["clientId"] == target["projectId"]
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT client_id,event_line_id FROM tasks WHERE id=?",
            (attached["task"]["id"],),
        ).fetchone()
        assert tuple(row) == (target["projectId"], line["id"])
        activity_clients = connection.execute(
            "SELECT DISTINCT client_id FROM event_lines WHERE parent_event_line_id=?",
            (line["id"],),
        ).fetchall()
        assert [item[0] for item in activity_clients] == [target["projectId"]]


def test_event_line_merge_moves_task_and_archives_source(tmp_path: Path) -> None:
    repository, identity, seed = _repository(tmp_path)
    target = _create_line(repository, identity, seed["projectId"], suffix="merge_target")
    participant_id = _seed_event_line_participant(repository, identity, suffix="merge")
    source = create_event_line(
        repository,
        identity,
        payload={
            "eventLineId": "event_line_gc06_merge_source",
            "clientId": seed["projectId"],
            "name": "GC-06 事件线 merge_source",
            "goal": "完成计划闭环",
            "participantMembershipIds": [participant_id],
        },
        idempotency_key="gc06-event-line-merge_source",
    )["eventLine"]
    assert source["participantMembershipIds"] == [participant_id]
    task = GC04TaskRepository(repository).create_task(
        identity,
        payload={"title": "合并迁移任务", "clientId": seed["projectId"]},
        idempotency_key="gc06-merge-task",
    )["task"]
    attach_task_to_event_line(
        repository,
        identity,
        event_line_id=source["id"],
        task_id=task["id"],
        expected_task_version=task["version"],
        allow_reassign=False,
        idempotency_key="gc06-merge-attach",
        task_command_port=GC04_FORMAL_TASK_COMMAND_PORT,
    )
    result = merge_event_lines(
        repository,
        identity,
        target_event_line_id=target["id"],
        source_event_line_ids=[source["id"]],
        expected_version=target["version"],
        idempotency_key="gc06-merge-command",
        task_command_port=GC04_FORMAL_TASK_COMMAND_PORT,
    )
    assert result["moved"]["tasks"] == 1
    assert result["eventLine"]["participantMembershipIds"] == [participant_id]
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT event_line_id FROM tasks WHERE id=?", (task["id"],)
        ).fetchone()[0] == target["id"]
        assert connection.execute(
            "SELECT lifecycle_state FROM event_lines WHERE id=?", (source["id"],)
        ).fetchone()[0] == "archived"


def test_event_line_creator_can_add_and_remove_persisted_participants(tmp_path: Path) -> None:
    repository, identity, seed = _repository(tmp_path)
    participant_id = _seed_event_line_participant(repository, identity, suffix="manage")
    invited_id = _seed_event_line_participant(repository, identity, suffix="invited")
    created = create_event_line(
        repository,
        identity,
        payload={
            "eventLineId": "event_line_gc06_participants",
            "clientId": seed["projectId"],
            "name": "协作事件线",
            "participantIds": [participant_id],
        },
        idempotency_key="gc06-event-line-participants-create",
    )["eventLine"]
    assert created["createdByMembershipId"] == identity.membership_id
    assert created["participantMembershipIds"] == [participant_id]
    assert event_line_detail(
        repository, identity, event_line_id=created["id"]
    )["eventLine"]["participantMembershipIds"] == [participant_id]

    participant_identity = replace(
        identity,
        principal_id="principal_event_line_manage",
        membership_id=participant_id,
        system_role="member",
        display_name="事件线参与者 manage",
    )
    assert created["id"] in {
        item["id"] for item in list_event_lines(repository, participant_identity)
    }
    assert event_line_detail(
        repository, participant_identity, event_line_id=created["id"]
    )["eventLine"]["participantMembershipIds"] == [participant_id]
    invited = update_event_line(
        repository,
        participant_identity,
        event_line_id=created["id"],
        payload={
            "expectedVersion": created["version"],
            "participantIds": [participant_id, invited_id],
        },
        idempotency_key="gc06-event-line-participants-invite",
    )["eventLine"]
    assert invited["participantMembershipIds"] == [invited_id, participant_id]
    with pytest.raises(RepositoryError, match="只有事件线创建人可以移除参与者"):
        update_event_line(
            repository,
            participant_identity,
            event_line_id=created["id"],
            payload={
                "expectedVersion": invited["version"],
                "participantIds": [participant_id],
            },
            idempotency_key="gc06-event-line-participants-remove-forbidden",
        )

    updated = update_event_line(
        repository,
        identity,
        event_line_id=created["id"],
        payload={"expectedVersion": invited["version"], "participantIds": []},
        idempotency_key="gc06-event-line-participants-remove",
    )["eventLine"]
    assert updated["participantMembershipIds"] == []


def test_visible_event_line_ai_draft_buttons_use_organization_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gc06_ui_domain,
        "_strict_event_line_detail",
        lambda *_args, **_kwargs: {
            "eventLine": {
                "id": "line_ai_draft",
                "clientId": "client_ai_draft",
                "name": "试点进展",
                "goal": "完成试点",
                "background": "",
            },
            "tasks": [],
            "activities": [],
        },
    )

    class Runtime:
        def private_ai_completion(self, **kwargs):
            return {"content": "模型生成草稿", "modelName": "organization-model"}

        def project_knowledge_context(self, project_id: str):
            assert project_id == "client_ai_draft"
            return {
                "organizationSharedKnowledge": [{
                    "sourceId": "knowledge_one",
                    "sourceDescription": "项目摘要",
                    "summary": "试点已经启动。",
                }]
            }

    compatibility = SimpleNamespace(runtime=Runtime())
    request = UiRequest(
        method="POST",
        path="event-lines/line_ai_draft/goal-polish",
        query={},
        body={"text": "完成试点"},
        idempotency_key="event-line-ai-draft",
    )
    goal = gc06_ui_domain.polish_event_line_goal(
        compatibility, request, SimpleNamespace(group=lambda _name: "line_ai_draft")
    )
    assert goal["draft"] == "模型生成草稿"
    background = gc06_ui_domain.draft_event_line_background(
        compatibility,
        request,
        SimpleNamespace(group=lambda _name: "line_ai_draft"),
    )
    assert background["draft"] == "模型生成草稿"
    assert background["citations"] == [{
        "id": "knowledge_one",
        "type": "organizationSharedKnowledge",
        "title": "项目摘要",
    }]


def test_event_line_evidence_upload_keeps_body_local_and_registers_safe_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gc06_ui_domain,
        "_strict_event_line_detail",
        lambda *_args, **_kwargs: {
            "eventLine": {"id": "line_upload", "clientId": "client_upload"},
            "activities": [],
        },
    )

    class Store:
        def __init__(self):
            self.bound = []

        def import_paths(self, **kwargs):
            source = Path(kwargs["paths"][0])
            assert source.read_bytes() == b"LOCAL_EVENT_EVIDENCE_BODY"
            return {"materials": [{
                "localSourceId": "local_event_evidence",
                "fileName": source.name,
                "contentHash": "event-evidence-hash",
                "byteSize": source.stat().st_size,
                "mediaType": "text/plain",
            }]}

        def bind_pending_materials(self, **kwargs):
            self.bound.append(("pending", kwargs))

        def bind_cloud_documents(self, **kwargs):
            self.bound.append(("cloud", kwargs))

        def process_pending_documents(self, **kwargs):
            return {"items": [{"parseStatus": "ready"}]}

    store = Store()
    monkeypatch.setattr(gc06_ui_domain, "_material_store", lambda _: store)

    class Runtime:
        database_path = Path("/tmp/gc06-upload-test.db")

        def __init__(self):
            self.commands = []

        def cloud_command(self, method, path, *, payload, **kwargs):
            self.commands.append((method, path, payload))
            if path.endswith("/materials/register-metadata"):
                assert "LOCAL_EVENT_EVIDENCE_BODY" not in repr(payload)
                return {"documents": [{
                    "localSourceId": "local_event_evidence",
                    "documentId": "source_asset_event_evidence",
                }]}
            return {"activity": {"id": "activity_event_evidence"}}

    runtime = Runtime()
    upload = SimpleNamespace(
        filename="证据.txt",
        content_type="text/plain",
        file=BytesIO(b"LOCAL_EVENT_EVIDENCE_BODY"),
    )
    request = UiRequest(
        method="POST",
        path="event-lines/line_upload/attachments",
        query={},
        body={"file": upload, "title": "试点证据", "purpose": "核验试点"},
        idempotency_key="event-evidence-upload",
    )
    result = gc06_ui_domain.upload_event_line_attachment(
        SimpleNamespace(runtime=runtime),
        request,
        SimpleNamespace(group=lambda _name: "line_upload"),
    )
    assert result == {
        "id": "source_asset_event_evidence",
        "documentId": "source_asset_event_evidence",
        "parseStatus": "ready",
        "parseError": None,
        "activityId": "activity_event_evidence",
        "localState": "ready",
        "cloudMetadataState": "ready",
    }
    assert runtime.commands[-1][1].endswith("/event-lines/line_upload/activities")


def test_retained_event_line_reads_build_only_traceable_fact_views() -> None:
    detail = {
        "eventLine": {
            "id": "event_line_retained",
            "name": "真实事件线",
            "goal": "完成闭环",
            "background": "来自正式事件线",
            "version": 3,
            "updatedAt": "2026-08-07T10:00:00Z",
        },
        "activities": [{
            "id": "activity_retained",
            "eventLineId": "event_line_retained",
            "sourceType": "weekly_review",
            "sourceId": "review_retained",
            "happenedAt": "2026-08-07T09:00:00Z",
            "title": "周复盘已提交",
            "summary": "本周完成真实闭环",
            "includeInNarrative": True,
        }],
        "tasks": [],
    }
    nodes = _event_line_timeline_nodes(detail)
    assert [item["sourceActivityIds"] for item in nodes] == [["activity_retained"]]
    narrative = _event_line_narrative(detail)
    assert narrative["generator"] == "strict_deterministic_event_line_v1"
    assert narrative["nodes"][0]["linkedActivityIds"] == ["activity_retained"]
    assert narrative["formalReady"] is True
    assert narrative["modelName"] == ""


def _create_plan(repository, identity, client_id: str, event_line_id: str):
    return create_planning_cycle(
        repository,
        identity,
        payload={
            "planningCycleId": "planning_cycle_gc06",
            "recordKind": "organization_plan",
            "clientId": client_id,
            "eventLineId": event_line_id,
            "periodKind": "week",
            "periodStart": "2026-08-03",
            "periodEnd": "2026-08-09",
            "title": "GC-06 周计划",
            "status": "published",
        },
        idempotency_key="gc06-planning-cycle",
    )["planningCycle"]


def test_planning_cycle_delete_and_archive_are_mutually_exclusive(tmp_path: Path) -> None:
    repository, identity, seed = _repository(tmp_path)
    unused = create_planning_cycle(
        repository,
        identity,
        payload={
            "planningCycleId": "unused_plan_lifecycle",
            "recordKind": "organization_plan",
            "periodKind": "month",
            "periodStart": "2026-08-01",
            "periodEnd": "2026-08-31",
            "title": "尚未关联任务的计划",
            "status": "published",
        },
        idempotency_key="create-unused-plan-lifecycle",
    )["planningCycle"]
    with pytest.raises(RepositoryError) as archive_error:
        update_planning_cycle(
            repository,
            identity,
            planning_cycle_id=unused["id"],
            payload={"expectedVersion": unused["version"], "status": "archived"},
            idempotency_key="archive-unused-plan-lifecycle",
        )
    assert archive_error.value.code == "unused_planning_cycle_must_be_deleted"
    deleted = delete_planning_cycle(
        repository,
        identity,
        planning_cycle_id=unused["id"],
        expected_version=unused["version"],
        idempotency_key="delete-unused-plan-lifecycle",
    )["planningCycle"]
    assert deleted["lifecycleState"] == "deleted"

    linked = create_planning_cycle(
        repository,
        identity,
        payload={
            "planningCycleId": "linked_plan_lifecycle",
            "recordKind": "organization_plan",
            "periodKind": "month",
            "periodStart": "2026-09-01",
            "periodEnd": "2026-09-30",
            "title": "已有任务承接的计划",
            "status": "published",
        },
        idempotency_key="create-linked-plan-lifecycle",
    )["planningCycle"]
    _seed_task(repository, identity, task_id="linked_plan_task", client_id=seed["projectId"])
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "UPDATE tasks SET planning_cycle_id=? WHERE id=?",
            (linked["id"], "linked_plan_task"),
        )
        connection.commit()
    with pytest.raises(RepositoryError) as delete_error:
        delete_planning_cycle(
            repository,
            identity,
            planning_cycle_id=linked["id"],
            expected_version=linked["version"],
            idempotency_key="delete-linked-plan-lifecycle",
        )
    assert delete_error.value.code == "planning_cycle_has_dependants"
    archived = update_planning_cycle(
        repository,
        identity,
        planning_cycle_id=linked["id"],
        payload={"expectedVersion": linked["version"], "status": "archived"},
        idempotency_key="archive-linked-plan-lifecycle",
    )["planningCycle"]
    assert archived["lifecycleState"] == "archived"


def test_planning_cycle_update_can_correct_period_without_recreating_record(tmp_path: Path) -> None:
    repository, identity, _ = _repository(tmp_path)
    monthly = create_planning_cycle(
        repository,
        identity,
        payload={
            "planningCycleId": "misclassified_monthly_plan",
            "recordKind": "organization_plan",
            "period": "2026-08",
            "periodKind": "month",
            "periodStart": "2026-08-01",
            "periodEnd": "2026-08-31",
            "timezone": "Asia/Shanghai",
            "title": "误识别为月度的组织计划",
            "status": "published",
        },
        idempotency_key="create-misclassified-monthly-plan",
    )["planningCycle"]

    payload = {
        "expectedVersion": monthly["version"],
        "period": "2026-Q4",
        "periodKind": "quarter",
        "periodStart": "2026-10-01",
        "periodEnd": "2026-12-31",
        "timezone": "Asia/Shanghai",
    }
    updated = update_planning_cycle(
        repository,
        identity,
        planning_cycle_id=monthly["id"],
        payload=payload,
        idempotency_key="correct-misclassified-monthly-plan",
    )["planningCycle"]

    assert updated["id"] == monthly["id"]
    assert updated["version"] == monthly["version"] + 1
    assert updated["period"] == "2026-Q4"
    assert updated["periodKind"] == "quarter"
    assert updated["periodStart"] == "2026-10-01"
    assert updated["periodEnd"] == "2026-12-31"

    replay = update_planning_cycle(
        repository,
        identity,
        planning_cycle_id=monthly["id"],
        payload=payload,
        idempotency_key="correct-misclassified-monthly-plan",
    )["planningCycle"]
    assert replay == updated


def _seed_task(
    repository,
    identity,
    *,
    task_id: str,
    client_id: str | None,
    scheduled: bool = False,
) -> None:
    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,"
            "lifecycle_state,version,resource_type_key,created_at,updated_at,"
            "deleted_at,authority_role,origin_instance_id) VALUES "
            "(?,?,'task','active',1,'organization_task',?,?,NULL,'cloud',?)",
            (task_id, identity.scope_id, now, now, repository.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO tasks (id,scope_id,creator_principal_id,"
            "creator_membership_id,client_id,event_line_id,lifecycle_state,version,"
            "title,due_date,scheduled_start_at,scheduled_end_at,created_at,updated_at,"
            "deleted_at) VALUES (?,?,NULL,?,?,NULL,'active',1,?,?,?,?,?,?,NULL)",
            (
                task_id,
                identity.scope_id,
                identity.membership_id,
                client_id,
                "GC-06 任务",
                "2026-08-10" if scheduled else None,
                "2026-08-10T09:00:00+08:00" if scheduled else None,
                "2026-08-10T10:00:00+08:00" if scheduled else None,
                now,
                now,
            ),
        )
        connection.commit()


def test_event_line_requires_client_is_idempotent_and_has_cas_lifecycle(
    tmp_path: Path,
) -> None:
    repository, identity, payload = _repository(tmp_path)
    with pytest.raises(RepositoryError) as missing:
        create_event_line(
            repository,
            identity,
            payload={"name": "没有客户"},
            idempotency_key="gc06-line-no-client",
        )
    assert missing.value.code == "event_line_client_required"

    line = _create_line(repository, identity, payload["projectId"])
    replay = create_event_line(
        repository,
        identity,
        payload={
            "eventLineId": line["id"],
            "clientId": payload["projectId"],
            "name": "GC-06 事件线 one",
            "goal": "完成计划闭环",
        },
        idempotency_key="gc06-event-line-one",
    )
    assert replay["idempotentReplay"] is True
    assert len(list_event_lines(repository, identity)) == 1

    updated = update_event_line(
        repository,
        identity,
        event_line_id=line["id"],
        payload={"expectedVersion": 1, "name": "新的事件线名称"},
        idempotency_key="gc06-event-line-update",
    )["eventLine"]
    assert (updated["name"], updated["version"]) == ("新的事件线名称", 2)
    with pytest.raises(RepositoryError) as conflict:
        update_event_line(
            repository,
            identity,
            event_line_id=line["id"],
            payload={"expectedVersion": 1, "name": "过期写入"},
            idempotency_key="gc06-event-line-update-stale",
        )
    assert conflict.value.code == "event_line_version_conflict"

    archived = transition_event_line(
        repository,
        identity,
        event_line_id=line["id"],
        transition="archive",
        expected_version=2,
        idempotency_key="gc06-event-line-archive",
    )["eventLine"]
    assert archived["lifecycleState"] == "archived"
    reopened = transition_event_line(
        repository,
        identity,
        event_line_id=line["id"],
        transition="reopen",
        expected_version=3,
        idempotency_key="gc06-event-line-reopen",
    )["eventLine"]
    assert reopened["lifecycleState"] == "active"


def test_plan_weekly_review_evidence_and_decision_action_are_versioned(
    tmp_path: Path,
) -> None:
    repository, identity, payload = _repository(tmp_path)
    line = _create_line(repository, identity, payload["projectId"])
    plan = _create_plan(repository, identity, payload["projectId"], line["id"])
    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,"
            "record_kind,parent_record_id,name,created_at,deleted_at) VALUES "
            "('department_gc06','active',1,?,'department',?,'GC-06 部门',?,NULL)",
            (now, identity.organization_id, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,"
            "role_key,status,version,record_kind,parent_membership_id,"
            "department_id,visibility_scope,lifecycle_state,created_at,"
            "updated_at,deleted_at) VALUES ('department_assignment_gc06',?,?,"
            "'department_lead','active',1,'department_assignment',?,"
            "'department_gc06','department','active',?,?,NULL)",
            (
                identity.scope_id,
                identity.principal_id,
                identity.membership_id,
                now,
                now,
            ),
        )
        connection.commit()
    department_lead = replace(identity, system_role="member")
    department_plan = create_planning_cycle(
        repository,
        department_lead,
        payload={
            "planningCycleId": "department_planning_cycle_gc06",
            "recordKind": "department_plan",
            "departmentId": "department_gc06",
            "periodStart": "2026-08-03",
            "periodEnd": "2026-08-09",
            "title": "GC-06 部门周计划",
        },
        idempotency_key="gc06-department-planning-cycle",
    )["planningCycle"]
    assert (department_plan["recordKind"], department_plan["parentPlanId"]) == (
        "department_plan",
        None,
    )

    first = save_weekly_review_draft(
        repository,
        identity,
        payload={
            "reviewId": "weekly_review_gc06",
            "planningCycleId": plan["id"],
            "content": {"progress": ["完成事件线内核"]},
            "evidence": [{
                "sourceObjectKind": "event_line",
                "sourceObjectId": line["id"],
                "sourceVersion": line["version"],
                "locatorKind": "object",
                "locator": "event-line:summary",
            }],
        },
        idempotency_key="gc06-weekly-review-v1",
    )["weeklyReview"]
    second = save_weekly_review_draft(
        repository,
        identity,
        payload={
            "planningCycleId": plan["id"],
            "expectedVersion": first["version"],
            "content": {"progress": ["完成事件线内核", "完成周复盘版本"]},
        },
        idempotency_key="gc06-weekly-review-v2",
    )["weeklyReview"]
    assert second["id"] == first["id"]
    assert [item["version"] for item in second["versions"]] == [1, 2]

    submitted = transition_weekly_review(
        repository,
        identity,
        review_id=first["id"],
        transition="submit",
        expected_version=second["version"],
        idempotency_key="gc06-weekly-review-submit",
    )["weeklyReview"]
    assert submitted["id"] == first["id"]
    assert submitted["status"] == "submitted"
    assert [item["businessState"] for item in submitted["versions"]] == [
        "draft", "draft", "submitted",
    ]

    action = create_decision_action(
        repository,
        identity,
        payload={
            "actionId": "decision_action_gc06",
            "planningCycleId": plan["id"],
            "clientId": payload["projectId"],
            "decisionState": "confirmed",
            "title": "由计划形成下一行动",
            "statement": "行动先进入 decision_actions",
            "reviewVersionId": submitted["currentSubmittedVersionId"],
        },
        idempotency_key="gc06-decision-action",
    )["decisionAction"]
    assert action["recordKind"] == "decision"
    assert action["taskId"] is None

    with pytest.raises(RepositoryError) as unavailable:
        convert_action_to_primary_task(
            repository,
            identity,
            action_id=action["id"],
            expected_version=1,
            idempotency_key="gc06-action-to-task",
        )
    assert (unavailable.value.status_code, unavailable.value.code) == (
        501,
        "task_command_not_connected",
    )

    action = update_decision_action(
        repository,
        identity,
        action_id=action["id"],
        payload={
            "expectedVersion": 1,
            "decisionState": "completed",
            "expectedOutput": "GC-06 可集成交付",
        },
        idempotency_key="gc06-decision-action-complete",
    )["decisionAction"]
    assert (action["decisionState"], action["version"]) == ("completed", 2)

    draft_action = create_decision_action(
        repository,
        identity,
        payload={
            "actionId": "decision_action_gc06_draft",
            "planningCycleId": plan["id"],
            "title": "待补证据行动",
        },
        idempotency_key="gc06-decision-action-draft",
    )["decisionAction"]
    with pytest.raises(RepositoryError) as evidence_required:
        update_decision_action(
            repository,
            identity,
            action_id=draft_action["id"],
            payload={"expectedVersion": 1, "decisionState": "confirmed"},
            idempotency_key="gc06-decision-action-confirm-no-evidence",
        )
    assert evidence_required.value.code == "decision_action_evidence_required"
    confirmed_draft = update_decision_action(
        repository,
        identity,
        action_id=draft_action["id"],
        payload={
            "expectedVersion": 1,
            "decisionState": "confirmed",
            "evidence": [{
                "sourceObjectKind": "weekly_review_version",
                "sourceObjectId": submitted["currentSubmittedVersionId"],
                "sourceVersion": 1,
            }],
        },
        idempotency_key="gc06-decision-action-confirm-with-evidence",
    )["decisionAction"]
    assert confirmed_draft["decisionState"] == "confirmed"

    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM weekly_reviews").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM weekly_review_versions").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM evidence_links").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM decision_actions").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_task_link_uses_formal_port_and_calendar_is_source_derived(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    line = _create_line(repository, identity, payload["projectId"])
    _seed_task(
        repository,
        identity,
        task_id="task_gc06_client",
        client_id=payload["projectId"],
    )
    with pytest.raises(RepositoryError) as unavailable:
        attach_task_to_event_line(
            repository,
            identity,
            event_line_id=line["id"],
            task_id="task_gc06_client",
            expected_task_version=1,
            allow_reassign=False,
            idempotency_key="gc06-task-event-line",
        )
    assert unavailable.value.code == "task_command_not_connected"

    _seed_task(
        repository,
        identity,
        task_id="task_gc06_org",
        client_id=None,
        scheduled=True,
    )
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT client_id,event_line_id FROM tasks WHERE id='task_gc06_client'"
        ).fetchone()
        assert tuple(row) == (payload["projectId"], None)
        derived = derive_task_calendar_projection(
            connection,
            scope_id=identity.scope_id,
            task_id="task_gc06_org",
        )
        replayed_projection = derive_task_calendar_projection(
            connection,
            scope_id=identity.scope_id,
            task_id="task_gc06_org",
        )
        connection.commit()
    assert derived is not None
    assert replayed_projection is not None
    assert replayed_projection["id"] == derived["id"]
    assert (derived["target_kind"], derived["task_id"]) == ("task", "task_gc06_org")
    assert len(list_calendar_entries(repository, identity)) == 1


def test_meeting_requires_client_binds_event_line_and_rebuilds_calendar(
    tmp_path: Path,
) -> None:
    repository, identity, payload = _repository(tmp_path)
    line = _create_line(repository, identity, payload["projectId"])
    with pytest.raises(RepositoryError) as missing:
        create_meeting(
            repository,
            identity,
            payload={
                "title": "无客户会议",
                "startsAt": "2026-08-08T09:00:00+08:00",
                "endsAt": "2026-08-08T10:00:00+08:00",
            },
            idempotency_key="gc06-meeting-no-client",
        )
    assert missing.value.code == "meeting_client_required"

    meeting = create_meeting(
        repository,
        identity,
        payload={
            "meetingId": "meeting_gc06",
            "clientId": payload["projectId"],
            "eventLineId": line["id"],
            "title": "计划推进会议",
            "agenda": "确认周计划",
            "startsAt": "2026-08-08T09:00:00+08:00",
            "endsAt": "2026-08-08T10:00:00+08:00",
        },
        idempotency_key="gc06-meeting-create",
    )["meeting"]
    assert meeting["clientId"] == line["clientId"]
    calendar = list_calendar_entries(repository, identity)
    assert [(item["target_kind"], item["source_version"]) for item in calendar] == [
        ("meeting", 1)
    ]

    # Renderer may preserve the Asia/Shanghai offset for the start while
    # serializing the calculated end as UTC.  Compare instants, not strings.
    meeting = update_meeting(
        repository,
        identity,
        meeting_id=meeting["id"],
        payload={
            "expectedVersion": 1,
            "startsAt": "2026-08-08T09:00:00+08:00",
            "endsAt": "2026-08-08T02:00:00.000Z",
        },
        idempotency_key="gc06-meeting-offset-update",
    )["meeting"]
    assert meeting["version"] == 2

    cancelled = update_meeting(
        repository,
        identity,
        meeting_id=meeting["id"],
        payload={"expectedVersion": 2, "status": "cancelled"},
        idempotency_key="gc06-meeting-cancel",
    )["meeting"]
    assert cancelled["status"] == "cancelled"
    assert list_calendar_entries(repository, identity) == []


def test_meeting_migration_reuses_task_authority_and_is_replay_safe(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    meeting = create_meeting(
        repository,
        identity,
        payload={
            "meetingId": "meeting_to_task_gc06",
            "clientId": payload["projectId"],
            "title": "统一日程对象",
            "agenda": "会议信息迁入任务",
            "startsAt": "2026-08-08T09:00:00+08:00",
            "endsAt": "2026-08-08T10:00:00+08:00",
        },
        idempotency_key="gc06-meeting-to-task-create",
    )["meeting"]
    first = migrate_meeting_to_task(
        repository,
        identity,
        meeting_id=meeting["id"],
        idempotency_key="gc06-meeting-to-task",
    )
    replay = migrate_meeting_to_task(
        repository,
        identity,
        meeting_id=meeting["id"],
        idempotency_key="gc06-meeting-to-task-replay",
    )
    assert first["task"]["id"] == replay["task"]["id"]
    assert first["task"]["source_type"] == "meeting_migration"
    assert first["meeting"]["status"] == "cancelled"
    assert replay["meeting"]["status"] == "cancelled"
    with runtime_connection(repository.database_path, "cloud") as connection:
        meeting_state = connection.execute(
            "SELECT status FROM meetings WHERE id=?", (meeting["id"],)
        ).fetchone()["status"]
        task_count = connection.execute(
            "SELECT COUNT(*) AS total FROM tasks WHERE source_type='meeting_migration' AND source_id=?",
            (meeting["id"],),
        ).fetchone()["total"]
    assert meeting_state == "cancelled"
    assert task_count == 1
    assert [item["target_kind"] for item in list_calendar_entries(repository, identity)] == ["task"]


def test_meeting_owner_invitation_and_plan_link_use_existing_88_tables(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    line = _create_line(repository, identity, payload["projectId"])
    plan = _create_plan(repository, identity, payload["projectId"], line["id"])
    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,principal_kind,"
            "display_name,version,lifecycle_state,created_at,deleted_at) VALUES "
            "('principal_meeting_invitee','active',1,?,'person','受邀同事',1,'active',?,NULL)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,"
            "version,record_kind,visibility_scope,lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES ('membership_meeting_invitee',?,'principal_meeting_invitee','member','active',"
            "1,'membership','organization','active',?,?,NULL)",
            (identity.scope_id, now, now),
        )
        connection.commit()
    created = create_meeting(
        repository,
        identity,
        payload={
            "meetingId": "meeting_collaboration_gc06",
            "clientId": payload["projectId"],
            "title": "协作计划会议",
            "startsAt": "2026-08-08T09:00:00+08:00",
            "endsAt": "2026-08-08T10:00:00+08:00",
            "organizerMembershipId": "membership_meeting_invitee",
            "planningCycleId": plan["id"],
        },
        idempotency_key="gc06-meeting-collaboration-create",
    )["meeting"]
    assert created["createdByMembershipId"] == identity.membership_id
    owner = next(item for item in created["collaborators"] if item["roleKey"] == "owner")
    assert (owner["membershipId"], owner["inboxStatus"]) == (
        "membership_meeting_invitee", "pending"
    )
    assert created["planLink"]["planningCycleId"] == plan["id"]
    assert created["planLink"]["decisionActionId"] is None
    assert "sourceSetId" not in created["planLink"]
    invitee = replace(
        identity,
        principal_id="principal_meeting_invitee",
        membership_id="membership_meeting_invitee",
        system_role="member",
        display_name="受邀同事",
    )
    accepted = transition_meeting_collaboration(
        repository,
        invitee,
        meeting_id=created["id"],
        action="accept",
        payload={"expectedGrantVersion": owner["version"]},
        idempotency_key="gc06-meeting-collaboration-accept",
    )["meeting"]
    assert next(
        item for item in accepted["collaborators"]
        if item["membershipId"] == invitee.membership_id
    )["inboxStatus"] == "accepted"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0] == 88


def test_gc06_keeps_strict_schema_at_88_without_legacy_objects(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    with runtime_connection(repository.database_path, "cloud") as connection:
        before = _schema_fingerprint(connection)
    line = _create_line(repository, identity, payload["projectId"])
    _create_plan(repository, identity, payload["projectId"], line["id"])
    with runtime_connection(repository.database_path, "cloud") as connection:
        names = {
            str(row[0]) for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        assert len(names) == 88
        assert {"event_line_records", "task_records", "weekly_review_records"}.isdisjoint(names)
        assert _schema_fingerprint(connection) == before
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc03_to_gc06_one_record_vertical_path_uses_one_task_authority(
    tmp_path: Path,
) -> None:
    """One compact record set proves GC-03/04/05/06 can share the 88-table lane."""

    repository, identity, seed = _repository(tmp_path)
    line = _create_line(repository, identity, seed["projectId"], suffix="vertical")
    plan = create_planning_cycle(
        repository,
        identity,
        payload={
            "planningCycleId": "planning_cycle_vertical",
            "recordKind": "organization_plan",
            "clientId": seed["projectId"],
            "eventLineId": line["id"],
            "periodKind": "week",
            "periodStart": "2026-08-03",
            "periodEnd": "2026-08-09",
            "title": "一条真实计划",
            "status": "published",
        },
        idempotency_key="gc06-vertical-plan",
    )["planningCycle"]
    review = save_weekly_review_draft(
        repository,
        identity,
        payload={
            "reviewId": "weekly_review_vertical",
            "planningCycleId": plan["id"],
            "content": {"summary": "完成一条黄金链纵向验证"},
            "evidence": [{
                "sourceObjectKind": "event_line",
                "sourceObjectId": line["id"],
                "sourceVersion": line["version"],
            }],
        },
        idempotency_key="gc06-vertical-review-draft",
    )["weeklyReview"]
    review = transition_weekly_review(
        repository,
        identity,
        review_id=review["id"],
        transition="submit",
        expected_version=review["version"],
        idempotency_key="gc06-vertical-review-submit",
    )["weeklyReview"]
    action = create_decision_action(
        repository,
        identity,
        payload={
            "actionId": "decision_action_vertical",
            "planningCycleId": plan["id"],
            "recordKind": "plan_action",
            "decisionState": "confirmed",
            "title": "把复盘行动交给正式任务",
            "reviewVersionId": review["currentSubmittedVersionId"],
        },
        idempotency_key="gc06-vertical-action",
    )["decisionAction"]
    linked_action = convert_action_to_primary_task(
        repository,
        identity,
        action_id=action["id"],
        expected_version=action["version"],
        idempotency_key="gc06-vertical-action-task",
        task_command_port=GC04_FORMAL_TASK_COMMAND_PORT,
    )
    task = linked_action["task"]
    assert list_plan_item_tasks(repository, identity)["counts"] == {}
    assert get_task_plan_link(repository, identity, task_id=task["id"]) is None
    assert set_task_plan_link(
        repository,
        identity,
        task_id=task["id"],
        action_id=None,
        idempotency_key="gc06-vertical-plan-link-clear",
    ) is None
    assert list_plan_item_tasks(repository, identity)["counts"] == {}
    restored_link = set_task_plan_link(
        repository,
        identity,
        task_id=task["id"],
        action_id=plan["id"],
        idempotency_key="gc06-vertical-plan-link-restore",
    )
    assert restored_link["planningCycleId"] == plan["id"]
    assert list_plan_item_tasks(repository, identity)["counts"] == {plan["id"]: 1}
    attached = attach_task_to_event_line(
        repository,
        identity,
        event_line_id=line["id"],
        task_id=task["id"],
        expected_task_version=restored_link["version"],
        allow_reassign=False,
        idempotency_key="gc03-vertical-task-event-line",
        task_command_port=GC04_FORMAL_TASK_COMMAND_PORT,
    )
    attached_task = attached["task"]
    task_domain = GC04TaskRepository(repository)
    preflight = task_domain.bulk_preflight(
        identity,
        payload={
            "atomicityMode": "per_item",
            "items": [{
                "itemKey": "only",
                "taskId": attached_task["id"],
                "expectedVersion": attached_task["version"],
                "patch": {"priority": "high"},
            }],
        },
        idempotency_key="gc05-vertical-preflight",
    )
    committed = task_domain.bulk_commit(
        identity,
        bulk_operation_id=preflight["bulkOperationId"],
        payload={"preflightSnapshotHash": preflight["preflightSnapshotHash"]},
        idempotency_key="gc05-vertical-commit",
    )
    assert committed["status"] == "committed"
    assert committed["successCount"] == 1
    with runtime_connection(repository.database_path, "cloud") as connection:
        task_row = connection.execute(
            "SELECT client_id,event_line_id,priority FROM tasks WHERE id=?",
            (attached_task["id"],),
        ).fetchone()
        assert tuple(task_row) == (seed["projectId"], line["id"], "high")
        assert connection.execute(
            "SELECT COUNT(*) FROM task_collaborators WHERE task_id=?",
            (attached_task["id"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM bulk_operations WHERE id=?",
            (preflight["bulkOperationId"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM bulk_operation_items WHERE bulk_operation_id=?",
            (preflight["bulkOperationId"],),
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    # 删除一条已关联计划的任务不会占用计划；同一计划仍可继续承接新任务。
    with runtime_connection(repository.database_path, "cloud") as connection:
        deleted_task_version = connection.execute(
            "SELECT version FROM tasks WHERE id=?", (attached_task["id"],)
        ).fetchone()[0]
    task_domain.delete_task(
        identity,
        task_id=attached_task["id"],
        expected_version=int(deleted_task_version),
        idempotency_key="gc06-vertical-task-delete",
    )
    replacement = task_domain.create_task(
        identity,
        payload={
            "title": "墓碑关系解除后的正式任务",
            "priority": "normal",
            "clientId": seed["projectId"],
            "planningCycleId": plan["id"],
        },
        idempotency_key="gc06-vertical-task-replacement",
    )["task"]
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT planning_cycle_id FROM tasks WHERE id=?", (replacement["id"],)
        ).fetchone()[0] == plan["id"]
        assert connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE planning_cycle_id=? AND lifecycle_state!='deleted'",
            (plan["id"],),
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_submitted_weekly_review_is_consumed_by_growth_companion_without_approval(
    tmp_path: Path,
) -> None:
    repository, identity, seed = _repository(tmp_path)
    line = _create_line(repository, identity, seed["projectId"], suffix="growth")
    plan = create_planning_cycle(
        repository,
        identity,
        payload={
            "planningCycleId": "planning_cycle_growth_candidate",
            "recordKind": "organization_plan",
            "clientId": seed["projectId"],
            "eventLineId": line["id"],
            "periodKind": "week",
            "periodStart": "2026-08-03",
            "periodEnd": "2026-08-09",
            "title": "成长候选验证周期",
            "status": "published",
        },
        idempotency_key="gc06-growth-candidate-plan",
    )["planningCycle"]
    draft = save_weekly_review_draft(
        repository,
        identity,
        payload={
            "reviewId": "weekly_review_growth_candidate",
            "planningCycleId": plan["id"],
            "content": {"summary": "我把零散经验整理成了一套可以复用的复盘方法。"},
            "evidence": [{
                "sourceObjectKind": "event_line",
                "sourceObjectId": line["id"],
                "sourceVersion": line["version"],
            }],
        },
        idempotency_key="gc06-growth-candidate-draft",
    )["weeklyReview"]
    submitted = transition_weekly_review(
        repository,
        identity,
        review_id=draft["id"],
        transition="submit",
        expected_version=draft["version"],
        idempotency_key="gc06-growth-candidate-submit",
    )
    assert submitted["growthCandidate"] == {
        "status": "scheduled",
        "sourceType": "weekly_review",
        "reviewId": draft["id"],
        "reviewVersionId": submitted["weeklyReview"]["currentSubmittedVersionId"],
        "agentKind": "growth_companion",
    }
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM growth_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0] == 0
    assert _sync_weekly_formal_evidence(repository, identity) == 1
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM growth_evidence").fetchone()[0] == 1
        evidence = connection.execute(
            "SELECT source_type,source_id FROM growth_evidence"
        ).fetchone()
        assert tuple(evidence) == ("weekly_review", draft["id"])
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert _sync_weekly_formal_evidence(repository, identity) == 0


def test_gc06_local_projection_keeps_cloud_rows_in_the_matching_88_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    identity = initialize_database(database, "local")
    now = utc_now()
    scope_id = "scope_gc06_local"
    sandbox_id = "sandbox_gc06_local"
    membership_id = "membership_gc06_local"
    client_id = "client_gc06_local"
    with runtime_connection(database, "local") as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,"
            "record_kind,name,created_at,deleted_at,projection_state,projected_at) "
            "VALUES ('org_gc06_local','active',1,?,'organization','GC06组织',"
            "?,NULL,'current',?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,"
            "policy_version,created_at,updated_at,status,version,lifecycle_state,"
            "deleted_at,projection_state,projected_at) VALUES (?, 'organization',"
            "'org_gc06_local',1,?,?,'active',1,'active',NULL,'current',?)",
            (scope_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,"
            "principal_kind,display_name,version,lifecycle_state,created_at,"
            "deleted_at,projection_state,projected_at) VALUES "
            "('principal_gc06_local','active',1,?,'person','GC06成员',1,"
            "'active',?,NULL,'current',?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
            "status,version,record_kind,visibility_scope,lifecycle_state,created_at,"
            "updated_at,deleted_at,projection_state,projected_at) VALUES (?,?,"
            "'principal_gc06_local','admin','active',1,'membership','organization',"
            "'active',?,?,NULL,'current',?)",
            (membership_id, scope_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO sandboxes (id,scope_id,principal_id,membership_id,record_kind,"
            "cloud_instance_id,database_generation_id,sandbox_kind,display_name,"
            "runtime_status,manifest_hash,version,lifecycle_state,created_at,updated_at,"
            "deleted_at,authority_role,origin_instance_id) VALUES (?,?,"
            "'principal_gc06_local',?,'sandbox','cli_gc06_local',?,'organization',"
            "'GC06工作空间','ready',?,1,'active',?,?,NULL,'local',?)",
            (
                sandbox_id,
                scope_id,
                membership_id,
                identity.database_generation_id,
                identity.manifest_hash,
                now,
                now,
                identity.database_generation_id,
            ),
        )
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
            "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES (?,?,'client','active',1,'client',?,?,NULL,"
            "'cloud_projection','cli_gc06_local')",
            (client_id, scope_id, now, now),
        )
        connection.execute(
            "INSERT INTO clients (id,scope_id,owner_membership_id,lifecycle_state,"
            "version,name,created_at,updated_at,deleted_at,sandbox_id,source_version,"
            "projection_state,projected_at) VALUES (?,?,?,'active',1,'GC06项目',"
            "?,?,NULL,?,1,'current',?)",
            (client_id, scope_id, membership_id, now, now, sandbox_id, now),
        )
        connection.commit()
    projector = LocalGC06PlanningProjection(
        _ProjectionRuntime(
            database,
            SimpleNamespace(
                sandbox_id=sandbox_id,
                membership_id=membership_id,
                cloud_instance_id="cli_gc06_local",
            ),
        )
    )
    line_id = "event_line_gc06_local"
    plan_id = "planning_cycle_gc06_local"
    review_id = "weekly_review_gc06_local"
    review_version_id = "weekly_review_version_gc06_local"
    action_id = "decision_action_gc06_local"
    activity_id = "event_line_activity_gc06_local"
    projector.apply_event_lines([{
        "id": line_id,
        "clientId": client_id,
        "name": "本机投影事件线",
        "kind": "project_line",
        "lifecycleState": "active",
        "version": 1,
        "createdByMembershipId": membership_id,
        "createdAt": now,
        "updatedAt": now,
    }])
    projector.apply_event_activities([{
        "id": activity_id,
        "eventLineId": line_id,
        "clientId": client_id,
        "sourceType": "manual_note",
        "sourceId": "note_gc06_local",
        "happenedAt": now,
        "title": "本机投影活动",
        "summary": "事件线活动仍写入同一 event_lines 表。",
        "associationState": "confirmed",
        "includeInNarrative": True,
        "version": 1,
    }])
    projector.apply_planning_cycles([{
        "id": plan_id,
        "recordKind": "organization_plan",
        "clientId": client_id,
        "eventLineId": line_id,
        "ownerMembershipId": membership_id,
        "periodKind": "week",
        "periodStart": "2026-08-03",
        "periodEnd": "2026-08-09",
        "title": "本机投影计划",
        "status": "published",
        "version": 1,
        "lifecycleState": "active",
        "createdAt": now,
        "updatedAt": now,
    }])
    projector.apply_weekly_reviews([{
        "id": review_id,
        "membershipId": membership_id,
        "planningCycleId": plan_id,
        "currentDraftVersionId": review_version_id,
        "currentSubmittedVersionId": None,
        "status": "draft",
        "version": 1,
        "lifecycleState": "active",
        "createdAt": now,
        "updatedAt": now,
        "versions": [{
            "id": review_version_id,
            "version": 1,
            "businessState": "draft",
            "contentHash": "content_hash_gc06_local",
            "createdAt": now,
        }],
    }])
    projector.apply_decision_actions([{
        "id": action_id,
        "planningCycleId": plan_id,
        "clientId": client_id,
        "decisionState": "draft",
        "recordKind": "plan_action",
        "title": "本机投影行动",
        "ownerMembershipId": membership_id,
        "version": 1,
        "lifecycleState": "active",
        "createdAt": now,
        "updatedAt": now,
    }])
    with runtime_connection(database, "local") as connection:
        assert connection.execute(
            "SELECT projection_state FROM event_lines WHERE id=?", (line_id,)
        ).fetchone()[0] == "current"
        assert connection.execute(
            "SELECT parent_event_line_id FROM event_lines WHERE id=?",
            (activity_id,),
        ).fetchone()[0] == line_id
        assert connection.execute(
            "SELECT projection_state FROM planning_cycles WHERE id=?", (plan_id,)
        ).fetchone()[0] == "current"
        assert connection.execute(
            "SELECT current_draft_version_id FROM weekly_reviews WHERE id=?",
            (review_id,),
        ).fetchone()[0] == review_version_id
        assert connection.execute(
            "SELECT projection_state FROM decision_actions WHERE id=?", (action_id,)
        ).fetchone()[0] == "current"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0] == 88

    # A valid empty cloud snapshot means that the previous projection became
    # stale.  It must reconcile cleanly instead of surfacing a false
    # "dependency incomplete" error to the organization-plan page.
    reconciled = projector.reconcile_planning_cycles([])
    assert reconciled["count"] == 1
    with runtime_connection(database, "local") as connection:
        row = connection.execute(
            "SELECT lifecycle_state,projection_state FROM planning_cycles WHERE id=?",
            (plan_id,),
        ).fetchone()
        assert tuple(row) == ("deleted", "stale")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_plan_item_tasks_use_strict_task_adapter_without_business_snapshot() -> None:
    class Runtime:
        @staticmethod
        def cloud_query(path: str, *, query=None):
            assert path == "/api/v2/gc06/plan-item-tasks"
            assert query == {"planItemId": "action_gc06"}
            return {
                "tasks": [{
                    "id": "task_gc06",
                    "title": "严格计划挂接任务",
                    "description": "不读取冻结快照",
                    "priority": "normal",
                    "visibility_scope": "participants",
                    "version": 3,
                    "collaborators": [],
                }],
                "counts": {"action_gc06": 1},
            }

    compatibility = SimpleNamespace(
        runtime=Runtime(),
        _snapshot=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("business snapshot must stay frozen")
        ),
    )
    result = workflow_ui_router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="org-model/plan-items/action_gc06/tasks",
            query={},
            body={},
            idempotency_key="gc06-plan-item-read",
        ),
    )
    assert len(result) == 1
    assert result[0]["id"] == "task_gc06"
    assert result[0]["title"] == "严格计划挂接任务"
    assert result[0]["version"] == 3


def test_gc06_detached_cloud_and_ui_registrars_are_complete(tmp_path: Path) -> None:
    repository, identity, _ = _repository(tmp_path)
    app = FastAPI()

    def current_identity():
        return identity

    register_gc06_planning_routes(app, repository, current_identity)
    cloud_paths = [
        str(route.path)
        for route in app.routes
        if str(getattr(route, "path", "")).startswith("/api/v2/gc06/")
    ]
    assert len(cloud_paths) == 30
    assert "/api/v2/gc06/meetings/{meeting_id}/collaboration/{action}" in cloud_paths
    assert "/api/v2/gc06/plan-item-tasks" in cloud_paths
    assert "/api/v2/gc06/tasks/{task_id}/plan-link" in cloud_paths
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "GET", "/api/v2/gc06/tasks/task_gc06/plan-link"
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "PATCH", "/api/v2/gc06/tasks/task_gc06/plan-link"
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "DELETE", "/api/v2/gc06/planning-cycles/plan_gc06"
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST", "/api/v2/gc06/meetings/meeting_gc06/collaboration/accept"
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST", "/api/v2/gc06/meetings/meeting_gc06/migrate-to-task"
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST", "/api/v2/gc06/event-lines/line_gc06/reparent"
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST", "/api/v2/gc06/event-lines/line_gc06/merge"
    )
    assert cloud_paths.index("/api/v2/gc06/event-lines/{event_line_id}/activities") < (
        cloud_paths.index("/api/v2/gc06/event-lines/{event_line_id}/{transition}")
    )
    assert len(gc06_ui_router.routes) == 62
    assert sum(
        route.pattern.startswith("gc06/") for route in gc06_ui_router.routes
    ) == 24
    assert {
        (route.method, route.pattern)
        for route in gc06_ui_router.routes
        if not route.pattern.startswith("gc06/")
    } == {
        ("GET", "reviews"),
        ("POST", "reviews/weekly/draft"),
        ("POST", "reviews/weekly"),
        ("GET", "reviews/history"),
        ("GET", "reviews/clients-pulse"),
        ("POST", "reviews/weekly-overview/refresh"),
        ("GET", "reviews/weekly-overview/status"),
        ("GET", "reviews/department-signals"),
        ("GET", "reviews/dashboard/drill-target"),
        ("POST", "tasks/agent/plan-step-draft"),
        ("GET", r"event-lines/(?P<event_line_id>[^/]+)/report-snapshot"),
        ("GET", r"event-lines/(?P<event_line_id>[^/]+)/report-draft"),
        ("GET", r"event-lines/(?P<event_line_id>[^/]+)/report-artifacts"),
        ("GET", r"event-lines/(?P<event_line_id>[^/]+)/legacy-report-runs"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/goal-polish"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/background-draft"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/clarification-draft"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/retry-sync"),
        ("GET", r"event-lines/(?P<event_line_id>[^/]+)/reparent-preview"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/reparent"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/merge-preview"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/merge"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/attachments"),
        (
            "POST",
            r"event-lines/(?P<event_line_id>[^/]+)/attachments/"
            r"(?P<attachment_id>[^/]+)/retry-parse",
        ),
        (
            "POST",
            r"event-lines/(?P<event_line_id>[^/]+)/attachments/retry-failed",
        ),
        ("GET", r"event-lines/(?P<event_line_id>[^/]+)/timeline-narrative"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/timeline-narrative/regenerate"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/readiness-analysis"),
        ("GET", r"event-lines/(?P<event_line_id>[^/]+)/task-candidates"),
        ("GET", "event-lines"),
        ("POST", "event-lines"),
        ("GET", r"event-lines/(?P<event_line_id>[^/]+)"),
        ("PATCH", r"event-lines/(?P<event_line_id>[^/]+)"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/close"),
        ("POST", r"event-lines/(?P<event_line_id>[^/]+)/reopen"),
        ("DELETE", r"event-lines/(?P<event_line_id>[^/]+)"),
        (
            "POST",
            r"event-lines/(?P<event_line_id>[^/]+)/tasks/(?P<task_id>[^/]+)/link",
        ),
        (
            "PATCH",
            r"event-lines/(?P<event_line_id>[^/]+)/tasks/"
            r"(?P<task_id>[^/]+)/milestone",
        ),
    }


def test_retained_event_line_adapter_resolves_creator_project_and_permissions() -> None:
    class ClientRow:
        def execute(self, statement, params):
            assert "FROM clients" in statement
            assert params == ("project_yiyu", "scope_yiyu", "sandbox_yiyu")
            return self

        def fetchone(self):
            return {"name": "益语智库"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Runtime:
        def _current_context(self, *, require_ready):
            assert require_ready is True
            return SimpleNamespace(scope_id="scope_yiyu", sandbox_id="sandbox_yiyu")

        def _connection(self):
            return ClientRow()

    class Compatibility:
        runtime = Runtime()

        def _member_names(self):
            return {"membership_creator": "顾源源"}

        def auth_state(self):
            return {
                "user": {
                    "id": "membership_creator",
                    "primaryRole": "member",
                    "fullName": "顾源源",
                },
            }

    mapped = _event_line_ui(Compatibility(), {
        "id": "line_yiyu",
        "clientId": "project_yiyu",
        "name": "产品迭代",
        "goal": "完成新版本验证",
        "background": "围绕任务体验持续优化",
        "taskCount": 2,
        "activityCount": 1,
        "createdByMembershipId": "membership_creator",
        "participantMembershipIds": ["membership_colleague"],
        "version": 3,
    })

    assert mapped["createdByName"] == "顾源源"
    assert mapped["ownerName"] == "顾源源"
    assert mapped["primaryClientName"] == "益语智库"
    assert mapped["readinessLevel"] == "substantial"
    assert mapped["participantIds"] == ["membership_colleague"]
    assert mapped["viewerCapabilities"]["canManageStructure"] is True
    assert mapped["viewerCapabilities"]["canReparentProject"] is True
    assert mapped["viewerCapabilities"]["canAddParticipants"] is True
    assert mapped["viewerCapabilities"]["canManageParticipants"] is True


def test_department_signals_reject_personal_scope_before_querying_data() -> None:
    with pytest.raises(LocalRuntimeError) as error:
        gc06_ui_router.dispatch(
            SimpleNamespace(),
            UiRequest(
                method="GET",
                path="reviews/department-signals",
                query={"weekLabel": "2026-W35", "perspective": "mine"},
                body={},
                idempotency_key="department-signals-personal-scope",
            ),
        )
    assert error.value.status_code == 403
    assert error.value.code == "department_signals_management_scope_required"


def test_department_signals_return_explainable_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gc06_ui_domain,
        "_retained_dashboard",
        lambda *_args, **_kwargs: {
            "weekLabel": "2026-W35",
            "activePerspective": "department",
            "activeDepartmentId": "department-1",
            "activeDepartmentName": "研究部",
            "activeDepartmentLeaderName": "负责人",
            "availablePerspectives": [
                {"key": "department", "departmentId": "department-1"},
                {"key": "mine"},
            ],
            "currentReview": {"workBlocker": "等待客户确认", "nextWeekFocus": "完成交付"},
            "plans": [{"id": "plan-1"}],
            "workItems": [
                {
                    "taskSnapshot": {"status": "done"},
                    "note": "形成第一版结论",
                    "structuredNote": {},
                },
                {
                    "taskSnapshot": {"status": "doing"},
                    "note": "",
                    "structuredNote": {"blockerReason": "缺少材料"},
                },
            ],
        },
    )
    result = gc06_ui_router.dispatch(
        SimpleNamespace(),
        UiRequest(
            method="GET",
            path="reviews/department-signals",
            query={
                "weekLabel": "2026-W35",
                "perspective": "department",
                "departmentId": "department-1",
            },
            body={},
            idempotency_key="department-signals-explainable",
        ),
    )
    row = result["departmentScoreboard"][0]
    assert row["taskTotalCount"] == 2
    assert row["taskCompletedCount"] == 1
    assert row["taskPendingCount"] == 1
    assert row["fulfillmentRatePct"] == 50
    assert row["reviewedTaskCount"] == 2
    assert row["blockerCount"] == 2
    assert row["activePlanCount"] == 1
    assert "valueProductionScore" not in row
    assert [item["key"] for item in result["healthIndicators"]] == [
        "weekly_tasks",
        "completed_tasks",
        "pending_tasks",
        "completion_rate",
        "reviewed_tasks",
        "active_plans",
        "blockers",
    ]
    assert result["sourceSummary"].startswith("基于当前权限范围内 2 条任务")
