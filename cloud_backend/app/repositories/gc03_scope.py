"""Shared GC-03 client ownership guards for strict cloud commands."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ..repository import RepositoryError


def _text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


@dataclass(frozen=True)
class ClientBinding:
    client_id: str | None
    event_line_id: str | None


def require_active_client(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    client_id: Any,
) -> sqlite3.Row:
    normalized = _text(client_id)
    if normalized is None:
        raise RepositoryError(422, "client_id_required", "请选择项目")
    row = connection.execute(
        "SELECT * FROM clients WHERE id=? AND scope_id=? "
        "AND lifecycle_state='active'",
        (normalized, scope_id),
    ).fetchone()
    if row is None:
        raise RepositoryError(422, "client_scope_invalid", "项目不存在或已不可用")
    return row


def require_active_event_line(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    event_line_id: Any,
) -> sqlite3.Row:
    normalized = _text(event_line_id)
    if normalized is None:
        raise RepositoryError(422, "event_line_id_required", "请选择事件线")
    row = connection.execute(
        "SELECT * FROM event_lines WHERE id=? AND scope_id=? "
        "AND record_kind='line' AND lifecycle_state='active'",
        (normalized, scope_id),
    ).fetchone()
    if row is None:
        raise RepositoryError(422, "event_line_scope_invalid", "事件线不存在或已不可用")
    return row


def validate_task_client_binding(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    client_id: Any,
    event_line_id: Any,
) -> ClientBinding:
    """Organization tasks may be unscoped; event-line tasks may not."""

    normalized_client = _text(client_id)
    normalized_event_line = _text(event_line_id)
    if normalized_client is not None:
        require_active_client(
            connection,
            scope_id=scope_id,
            client_id=normalized_client,
        )
    if normalized_event_line is None:
        return ClientBinding(normalized_client, None)
    if normalized_client is None:
        raise RepositoryError(
            422,
            "task_event_line_client_required",
            "挂入事件线的任务必须同时属于该事件线的项目",
        )
    event_line = require_active_event_line(
        connection,
        scope_id=scope_id,
        event_line_id=normalized_event_line,
    )
    if str(event_line["client_id"]) != normalized_client:
        raise RepositoryError(
            409,
            "task_event_line_client_mismatch",
            "任务与事件线不属于同一项目",
        )
    return ClientBinding(normalized_client, normalized_event_line)


def validate_meeting_client_binding(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    client_id: Any,
    event_line_id: Any,
) -> ClientBinding:
    """Meetings always belong to a client and may optionally bind its event line."""

    normalized_client = _text(client_id)
    normalized_event_line = _text(event_line_id)
    require_active_client(
        connection,
        scope_id=scope_id,
        client_id=normalized_client,
    )
    if normalized_event_line is not None:
        event_line = require_active_event_line(
            connection,
            scope_id=scope_id,
            event_line_id=normalized_event_line,
        )
        if str(event_line["client_id"]) != normalized_client:
            raise RepositoryError(
                409,
                "meeting_event_line_client_mismatch",
                "会议与事件线不属于同一项目",
            )
    return ClientBinding(normalized_client, normalized_event_line)
