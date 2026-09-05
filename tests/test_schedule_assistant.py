from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from cloud_backend.app.provisioning import provision_cloud_instance
from cloud_backend.app.repositories.schedule_assistant import (
    ScheduleAssistantRepository,
    _validated_ranked_ids,
    build_schedule_fact_pack,
)
from cloud_backend.app.repository import SessionIdentity


NOW = datetime(2026, 9, 4, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _task(
    task_id: str,
    title: str,
    *,
    owner: str,
    collaborators: list[str],
    start: str = "2026-09-04T10:00:00+08:00",
    end: str = "2026-09-04T11:00:00+08:00",
    priority: str = "normal",
) -> dict:
    return {
        "id": task_id,
        "title": title,
        "priority": priority,
        "scheduled_start_at": start,
        "scheduled_end_at": end,
        "completed_at": None,
        "collaborators": [
            {"display_name": owner, "role_key": "owner", "assignment_state": "assigned"},
            *[
                {"display_name": name, "role_key": "collaborator", "assignment_state": "assigned"}
                for name in collaborators
            ],
        ],
    }


TASKS = [
    _task("shared", "共同事项", owner="顾源源", collaborators=["乐乐"]),
    _task("viewer-only", "本人事项", owner="顾源源", collaborators=[]),
    _task("lele-only", "乐乐事项", owner="乐乐", collaborators=[]),
]


def test_collaboration_phrases_and_traditional_names_have_the_same_grounded_result() -> None:
    questions = [
        "今天我跟乐乐一起有哪些相关任务？",
        "今天我和乐乐一起有哪些相关任务？",
        "今天我与乐乐一起有哪些相关任务？",
        "我跟樂樂今天有哪些事情要去做？",
    ]
    packs = [
        build_schedule_fact_pack(TASKS, question=question, viewer_name="顾源源", now=NOW)
        for question in questions
    ]
    assert [[item["id"] for item in pack["tasks"]] for pack in packs] == [["shared"]] * 4
    assert [[item["taskId"] for item in pack["relatedTasks"]] for pack in packs] == [["shared"]] * 4
    assert all(pack["relatedTasks"][0]["viewerRole"] == "负责人" for pack in packs)
    assert all(pack["relatedTasks"][0]["selectedPersonRole"] == "协作者" for pack in packs)


def test_time_conflicts_are_only_reported_for_people_sharing_both_tasks() -> None:
    tasks = [
        _task("mine-a", "我的上午事项", owner="顾源源", collaborators=[]),
        _task("mine-b", "我的冲突事项", owner="顾源源", collaborators=[], start="2026-09-04T10:30:00+08:00", end="2026-09-04T11:30:00+08:00"),
        _task("other", "他人的同时事项", owner="乐乐", collaborators=[]),
    ]
    pack = build_schedule_fact_pack(tasks, question="今天有哪些重点？", viewer_name="顾源源", now=NOW)
    conflicts = [risk for risk in pack["risks"] if risk["kind"] == "time_conflict"]
    assert len(conflicts) == 1
    assert set(conflicts[0]["taskIds"]) == {"mine-a", "mine-b"}


def test_date_only_deadline_remains_valid_until_end_of_local_day() -> None:
    task = _task(
        "due-today",
        "今天截止",
        owner="顾源源",
        collaborators=[],
        start="",
        end="",
    )
    task["due_date"] = "2026-09-04"

    pack = build_schedule_fact_pack(
        [task], question="我今天有哪些重点？", viewer_name="顾源源", now=NOW,
    )

    assert pack["tasks"][0]["timeLabel"] == "截止 09月04日"
    assert all(risk["kind"] != "overdue" for risk in pack["risks"])


def test_only_currently_assigned_people_count_as_task_participants() -> None:
    task = _task(
        "lifecycle",
        "协作者生命周期",
        owner="顾源源",
        collaborators=[],
    )
    task["collaborators"].extend(
        [
            {
                "display_name": "乐乐",
                "role_key": "collaborator",
                "assignment_state": "awaiting_owner",
            },
            {
                "display_name": "小王",
                "role_key": "collaborator",
                "assignment_state": "returned",
            },
        ]
    )

    pack = build_schedule_fact_pack(
        [task],
        question="今天我和乐乐一起有哪些相关任务？",
        viewer_name="顾源源",
        now=NOW,
    )

    assert pack["tasks"] == []
    assert pack["relatedTasks"] == []
    assert pack["availablePeople"] == []


def test_returned_owner_is_missing_instead_of_being_reported_as_current_owner() -> None:
    task = _task(
        "returned-owner",
        "已退回负责人",
        owner="乐乐",
        collaborators=["顾源源"],
    )
    task["collaborators"][0]["assignment_state"] = "returned"
    task["collaborators"][0]["inbox_status"] = "returned"
    task["collaborators"][1]["assignment_state"] = "awaiting_owner"
    task["returned_to_creator"] = True

    pack = build_schedule_fact_pack(
        [task], question="今天有哪些重点？", viewer_name="顾源源", now=NOW,
    )

    assert pack["tasks"][0]["owner"] == ""
    assert any(risk["kind"] == "missing_owner" for risk in pack["risks"])


def test_returned_task_does_not_invent_a_collaboration_relationship() -> None:
    task = _task(
        "returned-owner",
        "已退回负责人",
        owner="乐乐",
        collaborators=["顾源源"],
    )
    task["collaborators"][0]["assignment_state"] = "returned"
    task["collaborators"][1]["assignment_state"] = "awaiting_owner"
    task["returned_to_creator"] = True

    pack = build_schedule_fact_pack(
        [task],
        question="今天我和乐乐一起有哪些相关任务？",
        viewer_name="顾源源",
        now=NOW,
    )

    assert pack["tasks"] == []
    assert pack["relatedTasks"] == []


def test_high_priority_task_with_only_a_deadline_is_still_unscheduled() -> None:
    task = _task(
        "deadline-only",
        "仅有截止日期的高优先级任务",
        owner="顾源源",
        collaborators=[],
        start="",
        end="",
        priority="high",
    )
    task["due_date"] = "2026-09-04"

    pack = build_schedule_fact_pack(
        [task], question="我今天有哪些重点？", viewer_name="顾源源", now=NOW,
    )

    assert any(
        risk["kind"] == "unscheduled_high_priority"
        for risk in pack["risks"]
    )


def test_high_priority_task_with_only_an_end_time_is_still_unscheduled() -> None:
    task = _task(
        "end-only",
        "只有结束时间的高优先级任务",
        owner="顾源源",
        collaborators=[],
        start="",
        end="2026-09-04T11:00:00+08:00",
        priority="high",
    )

    pack = build_schedule_fact_pack(
        [task], question="我的重点是什么？", viewer_name="顾源源", now=NOW,
    )

    assert any(
        risk["kind"] == "unscheduled_high_priority"
        for risk in pack["risks"]
    )


def test_fact_pack_keeps_the_authoritative_task_status() -> None:
    task = _task(
        "in-progress",
        "正在推进",
        owner="顾源源",
        collaborators=[],
    )
    task["progress_status"] = "in_progress"

    pack = build_schedule_fact_pack(
        [task], question="我今天有哪些重点？", viewer_name="顾源源", now=NOW,
    )

    assert pack["tasks"][0]["status"] == "in_progress"


def test_generic_question_is_scoped_to_the_authenticated_viewer() -> None:
    pack = build_schedule_fact_pack(
        TASKS,
        question="今天有哪些重点？",
        viewer_name="顾源源",
        now=NOW,
    )

    assert [item["id"] for item in pack["tasks"]] == ["shared", "viewer-only"]
    assert pack["requestedPeople"] == ["顾源源"]


class _TaskBoard:
    def board(self, _identity: SessionIdentity) -> dict:
        return {"tasks": TASKS}


class _NoModelRepository:
    def ai_config(self, _identity: SessionIdentity, *, include_secret: bool) -> dict:
        assert include_secret is True
        return {"status": "not_configured"}


class _GroundedOnlyAssistant(ScheduleAssistantRepository):
    def _rank_with_model(self, _identity: SessionIdentity, _pack: dict):
        # The action shape accepts only task ids; fabricated prose has no return channel.
        return ["shared"], "test-model"


def _identity() -> SessionIdentity:
    return SessionIdentity(
        session_id="session", principal_id="principal", membership_id="member",
        organization_id="org", cloud_instance_id="cloud", scope_id="scope",
        system_role="member", visibility_scope="organization", display_name="顾源源",
    )


def test_authenticated_identity_overrides_spoofed_viewer_name_and_model_cannot_write_facts() -> None:
    assistant = _GroundedOnlyAssistant(
        _NoModelRepository(), task_repository=_TaskBoard(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    answer = assistant.ask(
        _identity(),
        payload={"question": "今天我跟乐乐一起有哪些相关任务？", "viewerName": "冒充人员"},
    )
    assert answer["mode"] == "doubao_grounded"
    assert answer["sourceTaskIds"] == ["shared"]
    assert answer["relatedTasks"][0]["viewerRole"] == "负责人"
    serialized = str(answer)
    assert "冒充人员" not in serialized
    assert "不存在的会议" not in serialized
    assert answer["qualityChecks"] == {
        "allTaskIdsGrounded": True,
        "countsComputedByRules": True,
        "conflictsComputedByRules": True,
    }


def test_model_unavailable_returns_useful_local_evidence_instead_of_blank_content() -> None:
    assistant = ScheduleAssistantRepository(
        _NoModelRepository(), task_repository=_TaskBoard(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    answer = assistant.ask(_identity(), payload={"question": "我今天的重点是什么"})
    assert answer["status"] == "partial_success"
    assert answer["mode"] == "local_evidence"
    assert answer["sourceTaskIds"] == ["shared", "viewer-only"]
    assert answer["priorities"]


def test_unknown_collaborator_returns_empty_without_fuzzy_title_matching() -> None:
    pack = build_schedule_fact_pack(
        TASKS,
        question="今天我和不存在的同事一起有哪些相关任务？",
        viewer_name="顾源源",
        now=NOW,
    )
    assert pack["unknownCollaborator"] is True
    assert pack["tasks"] == []
    assert pack["relatedTasks"] == []


class _InvalidModelAssistant(ScheduleAssistantRepository):
    def _rank_with_model(self, _identity: SessionIdentity, _pack: dict):
        raise ValueError("model returned fabricated task ids")


def test_model_validation_failure_uses_the_same_grounded_local_facts() -> None:
    assistant = _InvalidModelAssistant(
        _NoModelRepository(), task_repository=_TaskBoard(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    answer = assistant.ask(_identity(), payload={"question": "我今天的重点是什么？"})
    assert answer["status"] == "partial_success"
    assert answer["mode"] == "local_evidence"
    assert answer["sourceTaskIds"] == ["shared", "viewer-only"]
    assert all(item["taskId"] in answer["sourceTaskIds"] for item in answer["priorities"])


def test_model_mixing_real_and_fabricated_ids_is_a_validation_failure() -> None:
    with pytest.raises(ValueError, match="ungrounded"):
        _validated_ranked_ids(
            {"taskIds": ["shared", "fabricated-task"]},
            {"shared", "viewer-only"},
        )


def test_http_route_is_registered_and_requires_a_real_login(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")
    app = create_app(
        CloudConfig(
            data_dir=tmp_path,
            database_path=database,
            bootstrap_token="bootstrap-test",
            master_key=Fernet.generate_key().decode(),
            cloud_instance_id="cloud-a",
        )
    )

    assert any(
        route.path == "/api/v2/ui/tasks/schedule-assistant/ask"
        and "POST" in (route.methods or set())
        for route in app.routes
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/ui/tasks/schedule-assistant/ask",
            json={"question": "今天的重点是什么？", "viewerName": "冒充人员"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authorization_required"
