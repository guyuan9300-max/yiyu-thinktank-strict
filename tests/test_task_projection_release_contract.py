from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

from backend.app.runtime import LocalRuntimeError, WorkspaceRuntime
from backend.app.ui_domains.gc04_tasks import (
    _require_task_view_projection_contract,
    _task_ui,
)
from cloud_backend.app.domain_routes.gc04_tasks import register_gc04_task_routes
from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repository import SessionIdentity
from tests.test_gc14_workbench_answer import _repository


def _create_personal_task(
    domain: GC04TaskRepository,
    identity: SessionIdentity,
    *,
    key: str,
) -> dict:
    return domain.create_task(
        identity,
        payload={
            "title": "验证月历投影与计时能力不会互相覆盖",
            "priority": "normal",
            "ownerMembershipId": identity.membership_id,
            "visibilityScope": "participants",
            "scheduledStartAt": "2026-08-27T09:00:00Z",
            "scheduledEndAt": "2026-08-27T10:00:00Z",
        },
        idempotency_key=key,
    )


def test_old_cloud_task_projection_is_rejected_instead_of_rendered_empty() -> None:
    with pytest.raises(LocalRuntimeError) as error:
        _require_task_view_projection_contract({"tasks": [{"id": "task-old"}]})

    assert error.value.status_code == 502
    assert error.value.code == "task_view_projection_contract_mismatch"


def test_local_adapter_preserves_projection_timer_and_command_route() -> None:
    task = _task_ui(
        {
            "id": "task-cumulative",
            "viewer_surfaces": {
                "personal_list": True,
                "personal_calendar": True,
                "collaboration_inbox": False,
                "event_line_detail": True,
            },
            "viewer_capabilities": {"can_view": True, "can_track_time": True},
            "task_timer": {"state": "paused", "elapsedSeconds": 42, "version": 2},
        }
    )
    assert task["viewerSurfaces"]["personalCalendar"] is True
    assert task["viewerCapabilities"]["canTrackTime"] is True
    assert task["timer"] == {"state": "paused", "elapsedSeconds": 42, "version": 2}
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST", "/api/v2/domain/tasks/task-cumulative/timer/start"
    ) is True


def test_board_contract_keeps_calendar_projection_and_timer_together(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _repository(tmp_path)
    domain = GC04TaskRepository(repository)
    created = _create_personal_task(domain, identity, key="cumulative-task-create")
    task_id = str(created["task"]["id"])

    board = domain.board(identity)
    assert board["viewerProjectionContract"] == {
        "schema": "yiyu.task-viewer-projection.v1",
        "schemaVersion": 1,
        "requiredTaskFields": ["viewer_surfaces", "viewer_capabilities"],
    }
    task = next(item for item in board["tasks"] if item["id"] == task_id)
    assert task["viewer_surfaces"]["personal_list"] is True
    assert task["viewer_surfaces"]["personal_calendar"] is True
    assert task["viewer_capabilities"]["can_track_time"] is True
    assert task["task_timer"]["state"] == "idle"
    assert any(item.get("task_id") == task_id for item in board["calendarEntries"])


def test_timer_route_and_idempotent_command_remain_in_the_same_release(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _repository(tmp_path)
    domain = GC04TaskRepository(repository)
    created = _create_personal_task(domain, identity, key="timer-contract-create")
    task_id = str(created["task"]["id"])

    started = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-contract-start",
    )
    replay = domain.update_task_timer(
        identity,
        task_id=task_id,
        action="start",
        expected_timer_version=0,
        idempotency_key="timer-contract-start",
    )
    assert replay == started
    assert started["taskTimer"]["state"] == "running"
    assert started["taskTimer"]["version"] == 1

    app = FastAPI()

    def current_identity() -> SessionIdentity:
        return identity

    register_gc04_task_routes(app, repository, current_identity)
    assert "/api/v2/domain/tasks/{task_id}/timer/{action}" in {
        route.path for route in app.routes
    }
