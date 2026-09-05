from types import SimpleNamespace

import cloud_backend.app.repositories.mobile_sync as mobile_sync_module
from cloud_backend.app.repositories.mobile_sync import MobileSyncRepository
from cloud_backend.app.repository import SessionIdentity


def _identity() -> SessionIdentity:
    return SessionIdentity(
        session_id="session",
        principal_id="principal",
        membership_id="member",
        organization_id="organization",
        cloud_instance_id="cloud",
        scope_id="scope",
        system_role="member",
        visibility_scope="organization",
        display_name="测试成员",
    )


def test_mobile_bootstrap_preserves_authorized_task_tags(monkeypatch) -> None:
    repository = SimpleNamespace(organization_snapshot=lambda _identity: {"id": "organization"})
    domain = MobileSyncRepository(repository)  # type: ignore[arg-type]
    domain.projects = SimpleNamespace(list_projects=lambda _identity: {"projects": []})
    domain.tasks = SimpleNamespace(
        board=lambda _identity: {
            "tasks": [],
            "projection": {"task_collaborators": []},
            "taskLists": [{"taskListId": "list"}],
            "taskTags": [{"taskTagId": "tag", "name": "重要"}],
            "calendarEntries": [],
        }
    )
    monkeypatch.setattr(domain, "_cursor_and_events", lambda _identity: ("cursor", [], []))
    monkeypatch.setattr(mobile_sync_module.gc06_planning, "list_meetings", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(mobile_sync_module.gc06_planning, "list_calendar_entries", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(mobile_sync_module.gc06_planning, "list_planning_cycles", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(mobile_sync_module.gc06_planning, "list_event_lines", lambda *_args, **_kwargs: [])

    result = domain.bootstrap(_identity())

    assert result["taskTags"] == [{"taskTagId": "tag", "name": "重要"}]
