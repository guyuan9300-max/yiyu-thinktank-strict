"""Thin mobile projection orchestration over the strict 88-table authority.

This module does not own business facts.  It composes already-authorized
domain projections and uses outbox/lifecycle ordering as a resumable cursor.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
from typing import Any, Iterable

from strict_common.ids import utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .gc04_tasks import GC04TaskRepository
from .project_materials import GC07ProjectMaterialsRepository
from . import gc06_planning


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _encode_cursor(available_at: str, event_id: str) -> str:
    return base64.urlsafe_b64encode(f"{available_at}\x1f{event_id}".encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[str, str]:
    if not cursor:
        return "", ""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        available_at, event_id = raw.split("\x1f", 1)
        return available_at, event_id
    except (ValueError, UnicodeError):
        raise RepositoryError(422, "mobile_sync_cursor_invalid", "同步游标无效") from None


class MobileSyncRepository:
    def __init__(self, repository: CloudRepository):
        self.repository = repository
        self.tasks = GC04TaskRepository(repository)
        self.projects = GC07ProjectMaterialsRepository(repository)

    @staticmethod
    def _in_window(value: Any, *, lower: datetime, upper: datetime) -> bool:
        parsed = _parse_time(value)
        return parsed is None or lower <= parsed <= upper

    def _filter_tasks(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        lower, upper = now - timedelta(days=90), now + timedelta(days=365)
        result: list[dict[str, Any]] = []
        for row in rows:
            date = row.get("scheduled_start_at") or row.get("scheduledStartAt") or row.get("due_date") or row.get("dueDate")
            # Undated and unresolved items are always necessary on a phone.
            if not date or not row.get("completed_at") and not row.get("completedAt"):
                result.append(row)
            elif self._in_window(date, lower=lower, upper=upper):
                result.append(row)
        return result

    def _filter_meetings(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        lower, upper = now - timedelta(days=90), now + timedelta(days=365)
        return [
            row
            for row in rows
            if str(row.get("status") or "scheduled") != "cancelled"
            and self._in_window(
                row.get("starts_at") or row.get("startsAt"),
                lower=lower,
                upper=upper,
            )
        ]

    def _cursor_and_events(
        self,
        identity: SessionIdentity,
        *,
        after: tuple[str, str] = ("", ""),
        limit: int = 500,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        with self.repository._connection() as connection:  # noqa: SLF001
            events = [dict(row) for row in connection.execute(
                """
                SELECT id, operation_id AS operationId, aggregate_version AS aggregateVersion,
                       event_type AS eventType, status, aggregate_type AS aggregateType,
                       aggregate_id AS aggregateId, available_at AS availableAt,
                       published_at AS publishedAt
                FROM outbox_events
                WHERE scope_id=? AND (available_at>? OR (available_at=? AND id>?))
                ORDER BY available_at, id LIMIT ?
                """,
                (identity.scope_id, after[0], after[0], after[1], limit),
            ).fetchall()]
            lifecycle = [dict(row) for row in connection.execute(
                """
                SELECT id, secured_resource_id AS resourceId, from_state AS fromState,
                       to_state AS toState, tombstone_version AS tombstoneVersion,
                       reason_code AS reasonCode, occurred_at AS occurredAt
                FROM lifecycle_events WHERE scope_id=? AND occurred_at>=?
                ORDER BY occurred_at, id LIMIT ?
                """,
                (identity.scope_id, after[0] or "0000-01-01T00:00:00Z", limit),
            ).fetchall()]
            tail = connection.execute(
                "SELECT available_at, id FROM outbox_events WHERE scope_id=? ORDER BY available_at DESC, id DESC LIMIT 1",
                (identity.scope_id,),
            ).fetchone()
        cursor = _encode_cursor(str(tail["available_at"]), str(tail["id"])) if tail else _encode_cursor(utc_now(), "none")
        return cursor, events, lifecycle

    def bootstrap(self, identity: SessionIdentity) -> dict[str, Any]:
        organization = self.repository.organization_snapshot(identity)
        projects = self.projects.list_projects(identity).get("projects", [])
        projects = [
            {**project, "projectId": str(project.get("projectId") or project.get("id")), "version": int(project.get("version") or 1)}
            for project in projects
        ]
        task_board = self.tasks.board(identity)
        meetings = gc06_planning.list_meetings(self.repository, identity)
        meeting_calendar = gc06_planning.list_calendar_entries(self.repository, identity)
        # Mobile projection needs archived parents as FK-safe, read-only rows;
        # the UI decides whether they are selectable for new writes.
        plans = gc06_planning.list_planning_cycles(self.repository, identity, include_archived=True)
        event_lines = gc06_planning.list_event_lines(self.repository, identity, include_archived=True)
        project_knowledge: list[dict[str, Any]] = []
        for project in projects:
            project_id = str(project.get("id") or project.get("projectId") or "")
            if not project_id:
                continue
            context = self.repository.project_knowledge_context(identity, project_id=project_id)
            project_knowledge.append({
                "projectId": project_id,
                "organizationSharedKnowledge": context.get("organizationSharedKnowledge", []),
                "officialWebsiteFacts": context.get("officialWebsiteFacts", []),
                "savedMemories": context.get("savedMemories", []),
                "relationshipCards": context.get("relationshipCards", []),
                "materialBoundary": context.get("materialBoundary", {}),
                "generatedAt": context.get("generatedAt"),
            })
        cursor, events, lifecycle = self._cursor_and_events(identity)
        return {
            "schema": "yiyu.strict.mobile-bootstrap.v1",
            "generatedAt": utc_now(),
            "identity": {
                "organizationId": identity.organization_id,
                "scopeId": identity.scope_id,
                "principalId": identity.principal_id,
                "membershipId": identity.membership_id,
                "cloudInstanceId": identity.cloud_instance_id,
            },
            "organization": organization,
            "projects": projects,
            "tasks": self._filter_tasks(task_board.get("tasks", [])),
            "taskCollaborators": task_board.get("projection", {}).get("task_collaborators", []),
            "taskLists": task_board.get("taskLists", []),
            "calendarEntries": [*task_board.get("calendarEntries", []), *meeting_calendar],
            "meetings": self._filter_meetings(meetings),
            "planningCycles": plans,
            "eventLines": event_lines,
            "projectKnowledge": project_knowledge,
            "changes": events,
            "tombstones": lifecycle,
            "cursor": cursor,
            "window": {"pastDays": 90, "futureDays": 365},
        }

    def delta(self, identity: SessionIdentity, *, cursor: str, limit: int = 500) -> dict[str, Any]:
        after = _decode_cursor(cursor)
        next_cursor, events, lifecycle = self._cursor_and_events(identity, after=after, limit=max(1, min(limit, 1000)))
        # Returning the current authorized slice makes a missed event harmless;
        # the event list still lets the client apply removals before upserts.
        if events or lifecycle:
            snapshot = self.bootstrap(identity)
            next_cursor = snapshot["cursor"]
        else:
            snapshot = {}
        return {
            "schema": "yiyu.strict.mobile-delta.v1",
            "generatedAt": utc_now(),
            "cursor": next_cursor,
            "changes": events,
            "tombstones": lifecycle,
            "projection": snapshot,
        }
