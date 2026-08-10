from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .gc03_scope import (
    require_active_client,
    require_active_event_line,
    validate_meeting_client_binding,
    validate_task_client_binding,
)
from .gc06_task_command_port import (
    FormalTaskCommandPort,
    UNAVAILABLE_FORMAL_TASK_COMMAND_PORT,
)
from .gc04_tasks import GC04TaskRepository


EVENT_LINE_KINDS = frozenset(
    {"project_line", "issue_line", "coordination_line", "case_line", "custom"}
)
EVENT_ACTIVITY_SOURCES = frozenset(
    {"task", "meeting", "weekly_review", "manual_note", "decision_action"}
)
PLAN_KINDS = frozenset({"organization_plan", "department_plan"})
PLAN_STATUSES = frozenset({"draft", "published", "archived"})
ACTION_KINDS = frozenset({"decision", "plan_action"})
ACTION_STATES = frozenset({"draft", "confirmed", "completed", "dropped"})
ACTION_TRANSITIONS = {
    "draft": frozenset({"draft", "confirmed", "dropped"}),
    "confirmed": frozenset({"confirmed", "completed", "dropped"}),
    "completed": frozenset({"completed"}),
    "dropped": frozenset({"dropped"}),
}
MEETING_STATUSES = frozenset({"scheduled", "completed", "cancelled"})
MEETING_COLLABORATION_SCHEMA = "yiyu.meeting-collaboration.v1"
MEETING_PLAN_LINK_PURPOSE = "meeting_plan_link"
MAX_TEXT = 100_000


def _text(value: Any, *, limit: int = MAX_TEXT) -> str:
    return str(value or "").strip()[:limit]


def _required_text(value: Any, code: str, message: str, *, limit: int = MAX_TEXT) -> str:
    result = _text(value, limit=limit)
    if not result:
        raise RepositoryError(422, code, message)
    return result


def _positive_int(value: Any, *, code: str = "expected_version_required") -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = 0
    if result <= 0:
        raise RepositoryError(422, code, "缺少有效的 expectedVersion")
    return result


def _iso_datetime(value: Any, *, field: str) -> str:
    result = _required_text(value, f"{field}_required", f"{field} 不能为空", limit=64)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RepositoryError(422, f"{field}_invalid", f"{field} 不是有效时间") from exc
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).isoformat(
            timespec="milliseconds"
        ).replace("+00:00", "Z")
    return result


def _datetime_instant(value: str) -> datetime:
    """Compare ISO timestamps by instant instead of their textual offsets."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed


def _iso_date(value: Any, *, field: str) -> str:
    result = _required_text(value, f"{field}_required", f"{field} 不能为空", limit=10)
    try:
        date.fromisoformat(result)
    except ValueError as exc:
        raise RepositoryError(422, f"{field}_invalid", f"{field} 不是有效日期") from exc
    return result


def _operation_id(scope_id: str, command_type: str, idempotency_key: str) -> str:
    return "op_" + sha256_text(
        f"gc06\x1f{scope_id}\x1f{command_type}\x1f{idempotency_key}"
    )[:30]


def _replay_result(
    connection: sqlite3.Connection,
    command: sqlite3.Row,
) -> dict[str, Any]:
    if str(command["status"] or "") != "settled":
        raise RepositoryError(409, "command_in_progress", "同一命令仍在结算中")
    manifest_id = _text(command["payload_object_manifest_id"])
    row = connection.execute(
        "SELECT receipt FROM object_manifests WHERE id=? AND scope_id=?",
        (manifest_id, str(command["scope_id"])),
    ).fetchone()
    if row is None:
        raise RepositoryError(409, "command_replay_unavailable", "原命令回执已不可用")
    try:
        payload = json.loads(str(row["receipt"] or "{}"))
    except json.JSONDecodeError as exc:
        raise RepositoryError(409, "command_replay_invalid", "原命令回执损坏") from exc
    if not isinstance(payload, dict):
        raise RepositoryError(409, "command_replay_invalid", "原命令回执结构无效")
    return {**payload, "idempotentReplay": True}


def _start_command(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    command_type: str,
    idempotency_key: str,
    aggregate_type: str,
    aggregate_id: str,
    expected_version: int | None,
    payload_hash: str,
    now: str,
) -> tuple[str, str, dict[str, Any] | None]:
    key = _required_text(
        idempotency_key,
        "idempotency_key_required",
        "写入必须提供 Idempotency-Key",
        limit=200,
    )
    existing = repository._existing_command(  # noqa: SLF001
        connection,
        scope_id=identity.scope_id,
        idempotency_key=key,
        command_type=command_type,
        payload_hash=payload_hash,
    )
    if existing is not None:
        return str(existing["id"]), str(existing["operation_id"]), _replay_result(
            connection, existing
        )
    operation_id = _operation_id(identity.scope_id, command_type, key)
    command_id = repository._record_id("cmd", operation_id, command_type)  # noqa: SLF001
    connection.execute(
        """
        INSERT INTO idempotency_records (
            id, scope_id, idempotency_key, payload_hash, result_hash, expires_at,
            result_object_manifest_id, status, created_at, authority_role,
            origin_instance_id
        ) VALUES (?, ?, ?, ?, NULL, '9999-12-31T23:59:59.999Z', NULL,
                  'accepted', ?, 'cloud', ?)
        """,
        (
            repository._record_id("idem", operation_id, command_type),  # noqa: SLF001
            identity.scope_id,
            key,
            payload_hash,
            now,
            repository.cloud_instance_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO commands (
            id, scope_id, operation_id, idempotency_key, aggregate_type,
            aggregate_id, command_type, actor_principal_id,
            expected_aggregate_version, device_command_sequence, status,
            actor_membership_id, payload_object_manifest_id, payload_hash,
            submitted_at, settled_at, authority_role, origin_instance_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'accepted', ?, NULL, ?, ?,
                  NULL, 'cloud', ?)
        """,
        (
            command_id,
            identity.scope_id,
            operation_id,
            key,
            aggregate_type,
            aggregate_id,
            command_type,
            identity.principal_id,
            expected_version,
            identity.membership_id,
            payload_hash,
            now,
            repository.cloud_instance_id,
        ),
    )
    return command_id, operation_id, None


def _settle_command(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    command_id: str,
    operation_id: str,
    idempotency_key: str,
    command_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    payload_hash: str,
    result: Mapping[str, Any],
    target_resource_id: str | None,
    now: str,
) -> dict[str, Any]:
    receipt = canonical_json(dict(result))
    result_hash = sha256_text(receipt)
    manifest_id = repository._record_id("manifest", operation_id, "result")  # noqa: SLF001
    connection.execute(
        """
        INSERT INTO object_manifests (
            id, scope_id, storage_key, content_hash, lifecycle_state, receipt,
            holder_role, holder_instance_id, storage_kind, byte_size, media_type,
            availability_state, receipt_hash, created_at, verified_at, deleted_at,
            authority_role, origin_instance_id
        ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud_command_receipt', ?,
                  'metadata_receipt', ?, 'application/json', 'ready', ?, ?, ?,
                  NULL, 'cloud', ?)
        """,
        (
            manifest_id,
            identity.scope_id,
            result_hash,
            receipt,
            repository.cloud_instance_id,
            len(receipt.encode("utf-8")),
            result_hash,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    connection.execute(
        "UPDATE commands SET status='settled', payload_object_manifest_id=?, "
        "settled_at=? WHERE id=?",
        (manifest_id, now, command_id),
    )
    updated_idempotency = connection.execute(
        "UPDATE idempotency_records SET result_hash=?, "
        "result_object_manifest_id=?, status='settled' "
        "WHERE scope_id=? AND idempotency_key=? AND payload_hash=?",
        (
            result_hash,
            manifest_id,
            identity.scope_id,
            idempotency_key,
            payload_hash,
        ),
    )
    if updated_idempotency.rowcount != 1:
        raise RepositoryError(409, "idempotency_record_missing", "命令幂等记录不存在")
    event_hash = sha256_text(
        f"{command_type}|{aggregate_id}|{aggregate_version}|{result_hash}"
    )
    connection.execute(
        """
        INSERT INTO audit_events (
            id, scope_id, operation_id, actor_id, action, event_hash,
            actor_membership_id, target_resource_id, details_object_manifest_id,
            occurred_at, origin_instance_id, created_at, integrity_hash,
            authority_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cloud')
        """,
        (
            repository._record_id("audit", operation_id, command_type),  # noqa: SLF001
            identity.scope_id,
            operation_id,
            identity.principal_id,
            command_type,
            event_hash,
            identity.membership_id,
            target_resource_id,
            manifest_id,
            now,
            repository.cloud_instance_id,
            now,
            event_hash,
        ),
    )
    connection.execute(
        """
        INSERT INTO outbox_events (
            id, scope_id, operation_id, aggregate_version, event_type, status,
            aggregate_type, aggregate_id, event_object_manifest_id, event_hash,
            available_at, published_at, authority_role, origin_instance_id
        ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, NULL, 'cloud', ?)
        """,
        (
            repository._record_id("evt", operation_id, command_type),  # noqa: SLF001
            identity.scope_id,
            operation_id,
            aggregate_version,
            command_type,
            aggregate_type,
            aggregate_id,
            manifest_id,
            event_hash,
            now,
            repository.cloud_instance_id,
        ),
    )
    return {**dict(result), "idempotentReplay": False}


def _insert_secured_resource(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    resource_id: str,
    resource_kind: str,
    resource_type_key: str,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO secured_resources (
            id, scope_id, resource_kind, lifecycle_state, version,
            resource_type_key, created_at, updated_at, deleted_at,
            authority_role, origin_instance_id
        ) VALUES (?, ?, ?, 'active', 1, ?, ?, ?, NULL, 'cloud', ?)
        """,
        (
            resource_id,
            identity.scope_id,
            resource_kind,
            resource_type_key,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )


def _event_line_row(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    event_line_id: str,
    *,
    include_deleted: bool = False,
) -> sqlite3.Row:
    deleted_clause = "" if include_deleted else "AND lifecycle_state!='deleted'"
    row = connection.execute(
        f"""
        SELECT * FROM event_lines
        WHERE scope_id=? AND id=? AND parent_event_line_id IS NULL
          AND record_kind='line' {deleted_clause}
        """,  # noqa: S608 - fixed clause only
        (identity.scope_id, event_line_id),
    ).fetchone()
    if row is None:
        raise RepositoryError(404, "event_line_missing", "事件线不存在")
    return row


def _event_line_payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    task_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE scope_id=? AND event_line_id=? "
            "AND lifecycle_state!='deleted'",
            (row["scope_id"], row["id"]),
        ).fetchone()[0]
    )
    meeting_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM meetings WHERE scope_id=? AND event_line_id=? "
            "AND lifecycle_state!='deleted'",
            (row["scope_id"], row["id"]),
        ).fetchone()[0]
    )
    activity_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM event_lines WHERE scope_id=? "
            "AND parent_event_line_id=? AND record_kind='activity' "
            "AND lifecycle_state!='deleted'",
            (row["scope_id"], row["id"]),
        ).fetchone()[0]
    )
    lifecycle = str(row["lifecycle_state"] or "active")
    return {
        "id": str(row["id"]),
        "clientId": str(row["client_id"]),
        "name": str(row["name"] or ""),
        "kind": str(row["source_type"] or "project_line"),
        "goal": str(row["goal"] or ""),
        "background": str(row["background"] or ""),
        "visibilityScope": str(row["visibility_scope"] or "project_public"),
        "lifecycleState": lifecycle,
        "status": "archived" if lifecycle == "archived" else lifecycle,
        "version": int(row["version"] or 1),
        "taskCount": task_count,
        "meetingCount": meeting_count,
        "activityCount": activity_count,
        "createdByMembershipId": row["created_by_membership_id"],
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
        "deletedAt": row["deleted_at"],
    }


def _activity_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "eventLineId": str(row["parent_event_line_id"]),
        "clientId": str(row["client_id"]),
        "sourceType": str(row["source_type"] or "manual_note"),
        "sourceId": str(row["source_id"] or ""),
        "happenedAt": str(row["happened_at"] or row["created_at"]),
        "title": str(row["title"] or ""),
        "summary": str(row["summary"] or ""),
        "associationState": str(row["association_state"] or "confirmed"),
        "includeInNarrative": bool(row["include_in_narrative"]),
        "version": int(row["version"] or 1),
    }


def _upsert_event_activity(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    event_line: sqlite3.Row,
    source_type: str,
    source_id: str,
    happened_at: str,
    title: str,
    summary: str,
    include_in_narrative: bool,
    now: str,
) -> sqlite3.Row:
    if source_type not in EVENT_ACTIVITY_SOURCES:
        raise RepositoryError(422, "event_activity_source_invalid", "事件活动来源无效")
    activity_id = "ela_" + sha256_text(
        f"{identity.scope_id}|{event_line['id']}|{source_type}|{source_id}"
    )[:30]
    previous = connection.execute(
        "SELECT * FROM event_lines WHERE scope_id=? AND record_kind='activity' "
        "AND source_type=? AND source_id=? AND lifecycle_state!='deleted'",
        (identity.scope_id, source_type, source_id),
    ).fetchone()
    if previous is not None and str(previous["id"]) != activity_id:
        connection.execute(
            "UPDATE event_lines SET lifecycle_state='deleted', deleted_at=?, "
            "updated_at=?, version=version+1 WHERE id=?",
            (now, now, str(previous["id"])),
        )
        connection.execute(
            "UPDATE secured_resources SET lifecycle_state='deleted', deleted_at=?, "
            "updated_at=?, version=version+1 WHERE id=?",
            (now, now, str(previous["id"])),
        )
    row = connection.execute(
        "SELECT * FROM event_lines WHERE scope_id=? AND id=?",
        (identity.scope_id, activity_id),
    ).fetchone()
    if row is None:
        _insert_secured_resource(
            repository,
            connection,
            identity,
            resource_id=activity_id,
            resource_kind="event_line_activity",
            resource_type_key=source_type,
            now=now,
        )
        connection.execute(
            """
            INSERT INTO event_lines (
                id, scope_id, client_id, lifecycle_state, version, record_kind,
                parent_event_line_id, created_by_membership_id, name, goal,
                background, visibility_scope, source_type, source_id, happened_at,
                title, summary, association_state, include_in_narrative,
                created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, 'active', 1, 'activity', ?, ?, NULL, NULL, NULL,
                      NULL, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, NULL)
            """,
            (
                activity_id,
                identity.scope_id,
                str(event_line["client_id"]),
                str(event_line["id"]),
                identity.membership_id,
                source_type,
                source_id,
                happened_at,
                title,
                summary,
                int(include_in_narrative),
                now,
                now,
            ),
        )
    else:
        connection.execute(
            """
            UPDATE event_lines SET lifecycle_state='active', deleted_at=NULL,
                happened_at=?, title=?, summary=?, association_state='confirmed',
                include_in_narrative=?, updated_at=?, version=version+1
            WHERE id=? AND scope_id=?
            """,
            (
                happened_at,
                title,
                summary,
                int(include_in_narrative),
                now,
                activity_id,
                identity.scope_id,
            ),
        )
        connection.execute(
            "UPDATE secured_resources SET lifecycle_state='active', deleted_at=NULL, "
            "updated_at=?, version=version+1 WHERE id=?",
            (now, activity_id),
        )
    return connection.execute(
        "SELECT * FROM event_lines WHERE id=?", (activity_id,)
    ).fetchone()


def list_event_lines(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    client_id: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        clauses = [
            "scope_id=?",
            "parent_event_line_id IS NULL",
            "record_kind='line'",
            "lifecycle_state!='deleted'",
        ]
        params: list[Any] = [identity.scope_id]
        if client_id:
            clauses.append("client_id=?")
            params.append(client_id)
        if not include_archived:
            clauses.append("lifecycle_state='active'")
        rows = connection.execute(
            "SELECT * FROM event_lines WHERE " + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, id DESC",
            params,
        ).fetchall()
        visible: list[dict[str, Any]] = []
        for row in rows:
            try:
                repository._require_project_access(  # noqa: SLF001
                    connection,
                    identity,
                    project_id=str(row["client_id"]),
                )
            except RepositoryError:
                continue
            visible.append(_event_line_payload(connection, row))
        return visible


def event_line_detail(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    event_line_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = _event_line_row(connection, identity, event_line_id)
        repository._require_project_access(  # noqa: SLF001
            connection, identity, project_id=str(row["client_id"])
        )
        activities = connection.execute(
            "SELECT * FROM event_lines WHERE scope_id=? AND parent_event_line_id=? "
            "AND record_kind='activity' AND lifecycle_state!='deleted' "
            "ORDER BY happened_at, created_at, id",
            (identity.scope_id, event_line_id),
        ).fetchall()
        tasks = connection.execute(
            "SELECT id, client_id, event_line_id, title, lifecycle_state, version, "
            "due_date, scheduled_start_at, scheduled_end_at, completed_at, updated_at "
            "FROM tasks WHERE scope_id=? AND event_line_id=? "
            "AND lifecycle_state!='deleted' ORDER BY updated_at DESC, id DESC",
            (identity.scope_id, event_line_id),
        ).fetchall()
        meetings = connection.execute(
            "SELECT * FROM meetings WHERE scope_id=? AND event_line_id=? "
            "AND lifecycle_state!='deleted' ORDER BY starts_at, id",
            (identity.scope_id, event_line_id),
        ).fetchall()
        return {
            "eventLine": _event_line_payload(connection, row),
            "activities": [_activity_payload(item) for item in activities],
            "tasks": [dict(item) for item in tasks],
            "meetings": [_meeting_payload(connection, item) for item in meetings],
        }


def create_event_line(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    event_line_id = _text(payload.get("eventLineId") or payload.get("id"), limit=200) or new_id()
    client_id = _required_text(
        payload.get("clientId") or payload.get("primaryClientId"),
        "event_line_client_required",
        "事件线必须明确选择客户项目",
        limit=200,
    )
    name = _required_text(payload.get("name"), "event_line_name_required", "事件线名称不能为空", limit=500)
    kind = _text(payload.get("kind"), limit=40) or "project_line"
    if kind not in EVENT_LINE_KINDS:
        raise RepositoryError(422, "event_line_kind_invalid", "事件线类型无效")
    normalized = {
        "eventLineId": event_line_id,
        "clientId": client_id,
        "name": name,
        "kind": kind,
        "goal": _text(payload.get("goal") or payload.get("intent")),
        "background": _text(payload.get("background") or payload.get("summary")),
        "visibilityScope": _text(payload.get("visibilityScope"), limit=40)
        or "project_public",
    }
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.event_line.created",
                idempotency_key=idempotency_key,
                aggregate_type="event_line",
                aggregate_id=event_line_id,
                expected_version=None,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            require_active_client(
                connection,
                scope_id=identity.scope_id,
                client_id=client_id,
            )
            repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=client_id,
                capability="project_write",
            )
            if connection.execute(
                "SELECT 1 FROM event_lines WHERE id=?", (event_line_id,)
            ).fetchone():
                raise RepositoryError(409, "event_line_identity_conflict", "事件线 ID 已存在")
            _insert_secured_resource(
                repository,
                connection,
                identity,
                resource_id=event_line_id,
                resource_kind="event_line",
                resource_type_key=kind,
                now=now,
            )
            connection.execute(
                """
                INSERT INTO event_lines (
                    id, scope_id, client_id, lifecycle_state, version, record_kind,
                    parent_event_line_id, created_by_membership_id, name, goal,
                    background, visibility_scope, source_type, source_id,
                    happened_at, title, summary, association_state,
                    include_in_narrative, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, 'active', 1, 'line', NULL, ?, ?, ?, ?, ?, ?,
                          NULL, NULL, NULL, NULL, NULL, 0, ?, ?, NULL)
                """,
                (
                    event_line_id,
                    identity.scope_id,
                    client_id,
                    identity.membership_id,
                    name,
                    normalized["goal"],
                    normalized["background"],
                    normalized["visibilityScope"],
                    kind,
                    now,
                    now,
                ),
            )
            row = require_active_event_line(
                connection,
                scope_id=identity.scope_id,
                event_line_id=event_line_id,
            )
            result = {"eventLine": _event_line_payload(connection, row)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.event_line.created",
                aggregate_type="event_line",
                aggregate_id=event_line_id,
                aggregate_version=1,
                payload_hash=payload_hash,
                result=result,
                target_resource_id=event_line_id,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def update_event_line(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    event_line_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    expected = _positive_int(payload.get("expectedVersion"))
    normalized = {
        "eventLineId": event_line_id,
        "expectedVersion": expected,
        "clientId": _text(payload.get("clientId") or payload.get("primaryClientId"), limit=200),
        "name": _text(payload.get("name"), limit=500),
        "kind": _text(payload.get("kind"), limit=40),
        "goal": _text(payload.get("goal") or payload.get("intent")),
        "background": _text(payload.get("background") or payload.get("summary")),
        "visibilityScope": _text(payload.get("visibilityScope"), limit=40),
    }
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.event_line.updated",
                idempotency_key=idempotency_key,
                aggregate_type="event_line",
                aggregate_id=event_line_id,
                expected_version=expected,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            row = require_active_event_line(
                connection,
                scope_id=identity.scope_id,
                event_line_id=event_line_id,
            )
            repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=str(row["client_id"]),
                capability="project_write",
            )
            if int(row["version"]) != expected:
                raise RepositoryError(409, "event_line_version_conflict", "事件线已被其他成员更新")
            target_client = normalized["clientId"] or str(row["client_id"])
            if target_client != str(row["client_id"]):
                require_active_client(
                    connection,
                    scope_id=identity.scope_id,
                    client_id=target_client,
                )
                repository._require_project_access(  # noqa: SLF001
                    connection,
                    identity,
                    project_id=target_client,
                    capability="project_write",
                )
                linked = connection.execute(
                    "SELECT (SELECT COUNT(*) FROM tasks WHERE scope_id=? AND "
                    "event_line_id=? AND lifecycle_state!='deleted') + "
                    "(SELECT COUNT(*) FROM meetings WHERE scope_id=? AND "
                    "event_line_id=? AND lifecycle_state!='deleted')",
                    (identity.scope_id, event_line_id, identity.scope_id, event_line_id),
                ).fetchone()[0]
                if int(linked):
                    raise RepositoryError(
                        409,
                        "event_line_reparent_has_links",
                        "事件线已有任务或会议，不能直接更换客户项目",
                    )
            kind = normalized["kind"] or str(row["source_type"] or "project_line")
            if kind not in EVENT_LINE_KINDS:
                raise RepositoryError(422, "event_line_kind_invalid", "事件线类型无效")
            update_cursor = connection.execute(
                """
                UPDATE event_lines SET client_id=?, name=?, source_type=?, goal=?,
                    background=?, visibility_scope=?, version=version+1,
                    updated_at=? WHERE id=? AND scope_id=? AND version=?
                """,
                (
                    target_client,
                    normalized["name"] or str(row["name"] or ""),
                    kind,
                    normalized["goal"] if "goal" in payload or "intent" in payload else row["goal"],
                    normalized["background"] if "background" in payload or "summary" in payload else row["background"],
                    normalized["visibilityScope"] or row["visibility_scope"],
                    now,
                    event_line_id,
                    identity.scope_id,
                    expected,
                ),
            )
            if update_cursor.rowcount != 1:
                raise RepositoryError(409, "event_line_version_conflict", "事件线已被其他成员更新")
            if target_client != str(row["client_id"]):
                connection.execute(
                    "UPDATE event_lines SET client_id=?,version=version+1,updated_at=? "
                    "WHERE scope_id=? AND parent_event_line_id=? "
                    "AND record_kind='activity' AND lifecycle_state!='deleted'",
                    (target_client, now, identity.scope_id, event_line_id),
                )
            connection.execute(
                "UPDATE secured_resources SET version=version+1, resource_type_key=?, "
                "updated_at=? WHERE id=? AND scope_id=?",
                (kind, now, event_line_id, identity.scope_id),
            )
            updated = _event_line_row(connection, identity, event_line_id)
            result = {"eventLine": _event_line_payload(connection, updated)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.event_line.updated",
                aggregate_type="event_line",
                aggregate_id=event_line_id,
                aggregate_version=int(updated["version"]),
                payload_hash=payload_hash,
                result=result,
                target_resource_id=event_line_id,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def transition_event_line(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    event_line_id: str,
    transition: str,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, Any]:
    states = {"archive": "archived", "reopen": "active", "delete": "deleted"}
    if transition not in states:
        raise RepositoryError(422, "event_line_transition_invalid", "事件线生命周期动作无效")
    expected = _positive_int(expected_version)
    target = states[transition]
    normalized = {"eventLineId": event_line_id, "transition": transition, "expectedVersion": expected}
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    command_type = f"gc06.event_line.{transition}d"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="event_line",
                aggregate_id=event_line_id,
                expected_version=expected,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            row = _event_line_row(connection, identity, event_line_id, include_deleted=True)
            repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=str(row["client_id"]),
                capability="project_write",
            )
            if int(row["version"]) != expected:
                raise RepositoryError(409, "event_line_version_conflict", "事件线已被其他成员更新")
            if transition == "delete":
                active_refs = int(
                    connection.execute(
                        "SELECT (SELECT COUNT(*) FROM tasks WHERE scope_id=? AND "
                        "event_line_id=? AND lifecycle_state!='deleted') + "
                        "(SELECT COUNT(*) FROM meetings WHERE scope_id=? AND "
                        "event_line_id=? AND lifecycle_state!='deleted') + "
                        "(SELECT COUNT(*) FROM planning_cycles WHERE scope_id=? AND "
                        "event_line_id=? AND lifecycle_state!='deleted')",
                        (
                            identity.scope_id,
                            event_line_id,
                            identity.scope_id,
                            event_line_id,
                            identity.scope_id,
                            event_line_id,
                        ),
                    ).fetchone()[0]
                )
                if active_refs:
                    raise RepositoryError(
                        409,
                        "event_line_delete_has_links",
                        "事件线仍关联任务、会议或计划，只能先归档",
                    )
            deleted_at = now if target == "deleted" else None
            connection.execute(
                "UPDATE event_lines SET lifecycle_state=?, deleted_at=?, "
                "version=version+1, updated_at=? WHERE id=? AND scope_id=? AND version=?",
                (target, deleted_at, now, event_line_id, identity.scope_id, expected),
            )
            connection.execute(
                "UPDATE secured_resources SET lifecycle_state=?, deleted_at=?, "
                "version=version+1, updated_at=? WHERE id=? AND scope_id=?",
                (target, deleted_at, now, event_line_id, identity.scope_id),
            )
            if target == "deleted":
                activity_ids = [
                    str(item["id"])
                    for item in connection.execute(
                        "SELECT id FROM event_lines WHERE scope_id=? AND "
                        "parent_event_line_id=? AND lifecycle_state!='deleted'",
                        (identity.scope_id, event_line_id),
                    ).fetchall()
                ]
                connection.execute(
                    "UPDATE event_lines SET lifecycle_state='deleted', deleted_at=?, "
                    "updated_at=?, version=version+1 WHERE scope_id=? AND "
                    "parent_event_line_id=? AND lifecycle_state!='deleted'",
                    (now, now, identity.scope_id, event_line_id),
                )
                for activity_id in activity_ids:
                    connection.execute(
                        "UPDATE secured_resources SET lifecycle_state='deleted', "
                        "deleted_at=?, updated_at=?, version=version+1 WHERE id=?",
                        (now, now, activity_id),
                    )
            updated = _event_line_row(connection, identity, event_line_id, include_deleted=True)
            result = {"eventLine": _event_line_payload(connection, updated)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                aggregate_type="event_line",
                aggregate_id=event_line_id,
                aggregate_version=int(updated["version"]),
                payload_hash=payload_hash,
                result=result,
                target_resource_id=event_line_id,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def record_event_line_activity(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    event_line_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    source_type = _required_text(payload.get("sourceType"), "event_activity_source_required", "活动来源不能为空", limit=40)
    source_id = _required_text(payload.get("sourceId"), "event_activity_source_id_required", "活动来源 ID 不能为空", limit=200)
    normalized = {
        "eventLineId": event_line_id,
        "sourceType": source_type,
        "sourceId": source_id,
        "happenedAt": _text(payload.get("happenedAt"), limit=64) or utc_now(),
        "title": _required_text(payload.get("title"), "event_activity_title_required", "活动标题不能为空", limit=500),
        "summary": _text(payload.get("summary")),
        "includeInNarrative": bool(payload.get("includeInNarrative", True)),
    }
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            line = require_active_event_line(
                connection,
                scope_id=identity.scope_id,
                event_line_id=event_line_id,
            )
            requested_line_version = payload.get("expectedVersion")
            if (
                requested_line_version is not None
                and int(line["version"]) != _positive_int(requested_line_version)
            ):
                raise RepositoryError(
                    409,
                    "event_line_version_conflict",
                    "事件线已被其他成员更新",
                )
            repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=str(line["client_id"]),
                capability="project_write",
            )
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.event_line.activity_recorded",
                idempotency_key=idempotency_key,
                aggregate_type="event_line",
                aggregate_id=event_line_id,
                expected_version=int(line["version"]),
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            if source_type == "task":
                source = connection.execute(
                    "SELECT client_id,event_line_id FROM tasks WHERE scope_id=? AND id=? "
                    "AND lifecycle_state!='deleted'",
                    (identity.scope_id, source_id),
                ).fetchone()
                if source is None:
                    raise RepositoryError(404, "task_missing", "任务不存在")
                validate_task_client_binding(
                    connection,
                    scope_id=identity.scope_id,
                    client_id=source["client_id"],
                    event_line_id=event_line_id,
                )
                if str(source["event_line_id"] or "") != event_line_id:
                    raise RepositoryError(409, "task_event_line_not_attached", "任务尚未通过正式命令挂入事件线")
            if source_type == "meeting":
                source = connection.execute(
                    "SELECT client_id,event_line_id FROM meetings WHERE scope_id=? AND id=? "
                    "AND lifecycle_state!='deleted'",
                    (identity.scope_id, source_id),
                ).fetchone()
                if source is None:
                    raise RepositoryError(404, "meeting_missing", "会议不存在")
                validate_meeting_client_binding(
                    connection,
                    scope_id=identity.scope_id,
                    client_id=source["client_id"],
                    event_line_id=source["event_line_id"],
                )
                if str(source["event_line_id"] or "") != event_line_id:
                    raise RepositoryError(409, "meeting_event_line_not_attached", "会议尚未挂入当前事件线")
            activity = _upsert_event_activity(
                repository,
                connection,
                identity,
                event_line=line,
                source_type=source_type,
                source_id=source_id,
                happened_at=normalized["happenedAt"],
                title=normalized["title"],
                summary=normalized["summary"],
                include_in_narrative=normalized["includeInNarrative"],
                now=now,
            )
            result = {"activity": _activity_payload(activity)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.event_line.activity_recorded",
                aggregate_type="event_line",
                aggregate_id=event_line_id,
                aggregate_version=int(line["version"]),
                payload_hash=payload_hash,
                result=result,
                target_resource_id=event_line_id,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def attach_task_to_event_line(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    event_line_id: str,
    task_id: str,
    expected_task_version: int,
    allow_reassign: bool,
    idempotency_key: str,
    task_command_port: FormalTaskCommandPort = UNAVAILABLE_FORMAL_TASK_COMMAND_PORT,
) -> dict[str, Any]:
    expected = _positive_int(expected_task_version)
    with repository._connection() as connection:  # noqa: SLF001
        line = require_active_event_line(
            connection,
            scope_id=identity.scope_id,
            event_line_id=event_line_id,
        )
        repository._require_project_access(  # noqa: SLF001
            connection,
            identity,
            project_id=str(line["client_id"]),
            capability="project_write",
        )
        task = connection.execute(
            "SELECT * FROM tasks WHERE scope_id=? AND id=? AND lifecycle_state!='deleted'",
            (identity.scope_id, task_id),
        ).fetchone()
        if task is None:
            raise RepositoryError(404, "task_missing", "任务不存在")
        validate_task_client_binding(
            connection,
            scope_id=identity.scope_id,
            client_id=task["client_id"],
            event_line_id=event_line_id,
        )
        if task["event_line_id"] and str(task["event_line_id"]) != event_line_id and not allow_reassign:
            raise RepositoryError(409, "task_event_line_reassign_required", "任务已挂入其他事件线")
    receipt = task_command_port.attach_event_line(
        repository,
        identity,
        task_id=task_id,
        event_line_id=event_line_id,
        expected_version=expected,
        allow_reassign=allow_reassign,
        idempotency_key=f"{idempotency_key}:formal-task-link",
    )
    with repository._connection() as connection:  # noqa: SLF001
        task = connection.execute(
            "SELECT * FROM tasks WHERE scope_id=? AND id=? AND lifecycle_state!='deleted'",
            (identity.scope_id, task_id),
        ).fetchone()
        if task is None or str(task["event_line_id"] or "") != event_line_id:
            raise RepositoryError(502, "formal_task_receipt_invalid", "正式任务命令未形成事件线关联")
    activity = record_event_line_activity(
        repository,
        identity,
        event_line_id=event_line_id,
        payload={
            "sourceType": "task",
            "sourceId": task_id,
            "happenedAt": utc_now(),
            "title": f"任务归入事件线：{str(task['title'])}",
            "summary": "任务已通过正式任务命令建立关联",
            "includeInNarrative": True,
        },
        idempotency_key=f"{idempotency_key}:event-activity",
    )
    return {
        "eventLine": event_line_detail(repository, identity, event_line_id=event_line_id)["eventLine"],
        "task": dict(task),
        "taskCommandReceipt": dict(receipt),
        "activity": activity["activity"],
    }


def reparent_event_line(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    event_line_id: str,
    target_client_id: str,
    expected_version: int,
    idempotency_key: str,
    task_command_port: FormalTaskCommandPort = UNAVAILABLE_FORMAL_TASK_COMMAND_PORT,
) -> dict[str, Any]:
    """Move an event line and its formal task/meeting children without dual authority."""

    expected = _positive_int(expected_version)
    target_client = _required_text(
        target_client_id,
        "event_line_target_client_required",
        "请选择目标客户项目",
        limit=200,
    )
    with repository._connection() as connection:  # noqa: SLF001
        line = _event_line_row(connection, identity, event_line_id)
        repository._require_project_access(  # noqa: SLF001
            connection, identity, project_id=str(line["client_id"]), capability="project_write"
        )
        repository._require_project_access(  # noqa: SLF001
            connection, identity, project_id=target_client, capability="project_write"
        )
        if int(line["version"]) != expected:
            raise RepositoryError(409, "event_line_version_conflict", "事件线已被其他成员更新")
        if str(line["client_id"]) == target_client:
            return {
                "eventLine": _event_line_payload(connection, line),
                "taskCommandReceipts": [],
                "meetingCommandReceipts": [],
                "idempotentReplay": True,
            }
        tasks = [
            dict(item)
            for item in connection.execute(
                "SELECT * FROM tasks WHERE scope_id=? AND event_line_id=? "
                "AND lifecycle_state!='deleted' ORDER BY id",
                (identity.scope_id, event_line_id),
            ).fetchall()
        ]
        meetings = [
            dict(item)
            for item in connection.execute(
                "SELECT * FROM meetings WHERE scope_id=? AND event_line_id=? "
                "AND lifecycle_state!='deleted' ORDER BY id",
                (identity.scope_id, event_line_id),
            ).fetchall()
        ]
    task_receipts: list[dict[str, Any]] = []
    meeting_receipts: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        receipt = task_command_port.move_task_scope(
            repository,
            identity,
            task_id=str(task["id"]),
            client_id=str(task["client_id"]),
            event_line_id=None,
            expected_version=int(task["version"]),
            idempotency_key=f"{idempotency_key}:task:{index}:detach",
        )
        task_receipts.append(dict(receipt))
    for index, meeting in enumerate(meetings):
        receipt = update_meeting(
            repository,
            identity,
            meeting_id=str(meeting["id"]),
            payload={"expectedVersion": int(meeting["version"]), "eventLineId": None},
            idempotency_key=f"{idempotency_key}:meeting:{index}:detach",
        )
        meeting_receipts.append(receipt)
    moved = update_event_line(
        repository,
        identity,
        event_line_id=event_line_id,
        payload={"expectedVersion": expected, "clientId": target_client},
        idempotency_key=f"{idempotency_key}:event-line",
    )["eventLine"]
    for index, task in enumerate(tasks):
        detached = task_receipts[index].get("task") or {}
        receipt = task_command_port.move_task_scope(
            repository,
            identity,
            task_id=str(task["id"]),
            client_id=target_client,
            event_line_id=event_line_id,
            expected_version=int(detached.get("version") or int(task["version"]) + 1),
            idempotency_key=f"{idempotency_key}:task:{index}:attach",
        )
        task_receipts.append(dict(receipt))
    for index, meeting in enumerate(meetings):
        detached = meeting_receipts[index].get("meeting") or {}
        receipt = update_meeting(
            repository,
            identity,
            meeting_id=str(meeting["id"]),
            payload={
                "expectedVersion": int(detached.get("version") or int(meeting["version"]) + 1),
                "clientId": target_client,
                "eventLineId": event_line_id,
            },
            idempotency_key=f"{idempotency_key}:meeting:{index}:attach",
        )
        meeting_receipts.append(receipt)
    return {
        "eventLine": event_line_detail(
            repository, identity, event_line_id=event_line_id
        )["eventLine"],
        "taskCommandReceipts": task_receipts,
        "meetingCommandReceipts": meeting_receipts,
        "movedEventLineVersion": moved["version"],
    }


def merge_event_lines(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    target_event_line_id: str,
    source_event_line_ids: Sequence[str],
    expected_version: int,
    idempotency_key: str,
    task_command_port: FormalTaskCommandPort = UNAVAILABLE_FORMAL_TASK_COMMAND_PORT,
) -> dict[str, Any]:
    expected = _positive_int(expected_version)
    source_ids = sorted({str(value) for value in source_event_line_ids if str(value)})
    if not source_ids or target_event_line_id in source_ids:
        raise RepositoryError(422, "event_line_merge_sources_invalid", "请选择其他事件线作为合并来源")
    with repository._connection() as connection:  # noqa: SLF001
        target = _event_line_row(connection, identity, target_event_line_id)
        repository._require_project_access(  # noqa: SLF001
            connection, identity, project_id=str(target["client_id"]), capability="project_write"
        )
        if int(target["version"]) != expected:
            raise RepositoryError(409, "event_line_version_conflict", "目标事件线已被其他成员更新")
        sources = [_event_line_row(connection, identity, source_id) for source_id in source_ids]
        for source in sources:
            if str(source["client_id"]) != str(target["client_id"]):
                raise RepositoryError(409, "event_line_merge_client_mismatch", "只能合并同一客户项目内的事件线")
        source_details = [
            event_line_detail(repository, identity, event_line_id=source_id)
            for source_id in source_ids
        ]
    moved_tasks = 0
    moved_meetings = 0
    moved_activities = 0
    task_receipts: list[dict[str, Any]] = []
    for source_index, detail in enumerate(source_details):
        source_line = detail["eventLine"]
        for task_index, task in enumerate(detail.get("tasks") or []):
            task_receipt = task_command_port.attach_event_line(
                repository,
                identity,
                task_id=str(task["id"]),
                event_line_id=target_event_line_id,
                expected_version=int(task["version"]),
                allow_reassign=True,
                idempotency_key=f"{idempotency_key}:source:{source_index}:task:{task_index}",
            )
            task_receipts.append(dict(task_receipt))
            moved_tasks += 1
        for meeting_index, meeting in enumerate(detail.get("meetings") or []):
            update_meeting(
                repository,
                identity,
                meeting_id=str(meeting["id"]),
                payload={
                    "expectedVersion": int(meeting["version"]),
                    "eventLineId": target_event_line_id,
                },
                idempotency_key=(
                    f"{idempotency_key}:source:{source_index}:meeting:{meeting_index}"
                ),
            )
            moved_meetings += 1
        for activity_index, activity in enumerate(detail.get("activities") or []):
            if str(activity.get("sourceType") or "") in {"task", "meeting"}:
                continue
            record_event_line_activity(
                repository,
                identity,
                event_line_id=target_event_line_id,
                payload={
                    "sourceType": activity.get("sourceType"),
                    "sourceId": activity.get("sourceId"),
                    "happenedAt": activity.get("happenedAt"),
                    "title": activity.get("title"),
                    "summary": activity.get("summary"),
                    "includeInNarrative": activity.get("includeInNarrative", True),
                },
                idempotency_key=(
                    f"{idempotency_key}:source:{source_index}:activity:{activity_index}"
                ),
            )
            moved_activities += 1
        record_event_line_activity(
            repository,
            identity,
            event_line_id=target_event_line_id,
            payload={
                "sourceType": "manual_note",
                "sourceId": f"merge:{source_line['id']}",
                "happenedAt": utc_now(),
                "title": f"合并来源事件线：{source_line['name']}",
                "summary": "源事件线已归档，原身份与历史仍可追溯",
                "includeInNarrative": True,
            },
            idempotency_key=f"{idempotency_key}:source:{source_index}:trace",
        )
        transition_event_line(
            repository,
            identity,
            event_line_id=str(source_line["id"]),
            transition="archive",
            expected_version=int(source_line["version"]),
            idempotency_key=f"{idempotency_key}:source:{source_index}:archive",
        )
    return {
        "eventLine": event_line_detail(
            repository, identity, event_line_id=target_event_line_id
        )["eventLine"],
        "sourceEventLineIds": source_ids,
        "moved": {
            "tasks": moved_tasks,
            "meetings": moved_meetings,
            "activities": moved_activities,
        },
        "taskCommandReceipts": task_receipts,
    }


def _membership_row(
    connection: sqlite3.Connection, identity: SessionIdentity, membership_id: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM organization_memberships WHERE scope_id=? AND id=? "
        "AND status='active' AND lifecycle_state='active'",
        (identity.scope_id, membership_id),
    ).fetchone()
    if row is None:
        raise RepositoryError(404, "membership_missing", "组织成员身份不存在")
    return row


def _require_plan_permission(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    record_kind: str,
    department_id: str | None,
    write: bool,
) -> None:
    _membership_row(connection, identity, identity.membership_id)
    if identity.is_admin:
        return
    if record_kind == "organization_plan" and write:
        raise RepositoryError(403, "organization_plan_forbidden", "只有管理员可发布组织计划")
    if record_kind == "department_plan":
        assignment = connection.execute(
            "SELECT role_key FROM organization_memberships "
            "WHERE scope_id=? AND record_kind='department_assignment' "
            "AND parent_membership_id=? AND department_id=? "
            "AND status='active' AND lifecycle_state='active' "
            "ORDER BY version DESC,updated_at DESC,id DESC LIMIT 1",
            (identity.scope_id, identity.membership_id, department_id),
        ).fetchone()
        if assignment is None or (
            write and str(assignment["role_key"] or "") != "department_lead"
        ):
            raise RepositoryError(403, "department_plan_forbidden", "无权访问或维护其他部门计划")


def _planning_row(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    planning_cycle_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM planning_cycles WHERE scope_id=? AND id=? "
        "AND lifecycle_state!='deleted'",
        (identity.scope_id, planning_cycle_id),
    ).fetchone()
    if row is None:
        raise RepositoryError(404, "planning_cycle_missing", "计划周期不存在")
    return row


def _planning_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "recordKind": str(row["record_kind"]),
        "clientId": row["client_id"],
        "eventLineId": row["event_line_id"],
        "parentPlanId": row["parent_plan_id"],
        "departmentId": row["department_id"],
        "ownerMembershipId": row["owner_membership_id"],
        "period": row["period"],
        "periodKind": row["period_kind"],
        "periodStart": str(row["period_start"]),
        "periodEnd": str(row["period_end"]),
        "timezone": row["timezone"],
        "title": str(row["title"] or ""),
        "summary": str(row["summary"] or ""),
        "status": str(row["status"]),
        "planVersion": int(row["plan_version"] or 1),
        "version": int(row["version"] or 1),
        "lifecycleState": str(row["lifecycle_state"]),
        "publishedAt": row["published_at"],
        "archivedAt": row["archived_at"],
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
    }


def list_planning_cycles(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT * FROM planning_cycles WHERE scope_id=? AND "
            "lifecycle_state!='deleted' "
            + ("" if include_archived else "AND lifecycle_state='active' ")
            + "ORDER BY period_start DESC, created_at DESC, id DESC",
            (identity.scope_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                _require_plan_permission(
                    connection,
                    identity,
                    record_kind=str(row["record_kind"]),
                    department_id=row["department_id"],
                    write=False,
                )
            except RepositoryError:
                continue
            result.append(_planning_payload(row))
        return result


def create_planning_cycle(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    planning_cycle_id = _text(payload.get("planningCycleId") or payload.get("id"), limit=200) or new_id()
    record_kind = _text(payload.get("recordKind"), limit=40) or "organization_plan"
    if record_kind not in PLAN_KINDS:
        raise RepositoryError(422, "planning_cycle_kind_invalid", "计划层级无效")
    status = _text(payload.get("status"), limit=30) or "draft"
    if status not in PLAN_STATUSES:
        raise RepositoryError(422, "planning_cycle_status_invalid", "计划状态无效")
    period_start = _iso_date(payload.get("periodStart"), field="period_start")
    period_end = _iso_date(payload.get("periodEnd"), field="period_end")
    if period_end < period_start:
        raise RepositoryError(422, "planning_cycle_period_invalid", "计划结束日期不能早于开始日期")
    department_id = _text(payload.get("departmentId"), limit=200) or None
    if record_kind == "department_plan" and not department_id:
        raise RepositoryError(422, "department_plan_department_required", "部门计划必须指定部门")
    normalized = {
        "planningCycleId": planning_cycle_id,
        "recordKind": record_kind,
        "clientId": _text(payload.get("clientId"), limit=200) or None,
        "eventLineId": _text(payload.get("eventLineId"), limit=200) or None,
        "parentPlanId": _text(payload.get("parentPlanId"), limit=200) or None,
        "departmentId": department_id,
        "ownerMembershipId": _text(payload.get("ownerMembershipId"), limit=200)
        or identity.membership_id,
        "period": _text(payload.get("period"), limit=100) or f"{period_start}/{period_end}",
        "periodKind": _text(payload.get("periodKind"), limit=40) or "custom",
        "periodStart": period_start,
        "periodEnd": period_end,
        "timezone": _text(payload.get("timezone"), limit=80) or "Asia/Shanghai",
        "title": _required_text(payload.get("title"), "planning_cycle_title_required", "计划标题不能为空", limit=500),
        "summary": _text(payload.get("summary")),
        "status": status,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.planning_cycle.created",
                idempotency_key=idempotency_key,
                aggregate_type="planning_cycle",
                aggregate_id=planning_cycle_id,
                expected_version=None,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            _require_plan_permission(
                connection,
                identity,
                record_kind=record_kind,
                department_id=department_id,
                write=True,
            )
            if department_id:
                department = connection.execute(
                    "SELECT 1 FROM organizations WHERE id=? AND parent_record_id=? "
                    "AND lifecycle_state='active' AND record_kind='department'",
                    (department_id, identity.organization_id),
                ).fetchone()
                if department is None:
                    raise RepositoryError(404, "department_missing", "部门不存在")
            _membership_row(connection, identity, normalized["ownerMembershipId"])
            if normalized["parentPlanId"]:
                parent = _planning_row(connection, identity, normalized["parentPlanId"])
                if str(parent["record_kind"]) != "organization_plan":
                    raise RepositoryError(409, "parent_plan_invalid", "部门计划只能挂在组织计划下")
            client_id = normalized["clientId"]
            if client_id:
                require_active_client(
                    connection,
                    scope_id=identity.scope_id,
                    client_id=client_id,
                )
                repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=client_id
                )
            if normalized["eventLineId"]:
                line = require_active_event_line(
                    connection,
                    scope_id=identity.scope_id,
                    event_line_id=normalized["eventLineId"],
                )
                if not client_id or str(line["client_id"]) != client_id:
                    raise RepositoryError(409, "planning_event_line_client_mismatch", "计划与事件线客户项目不一致")
            duplicate = connection.execute(
                """
                SELECT id FROM planning_cycles WHERE scope_id=? AND record_kind=?
                  AND department_id IS ? AND client_id IS ? AND period_start=?
                  AND period_end=? AND lifecycle_state!='deleted'
                """,
                (
                    identity.scope_id,
                    record_kind,
                    department_id,
                    client_id,
                    period_start,
                    period_end,
                ),
            ).fetchone()
            if duplicate is not None:
                raise RepositoryError(409, "planning_cycle_duplicate", "相同层级和周期的计划已存在")
            _insert_secured_resource(
                repository,
                connection,
                identity,
                resource_id=planning_cycle_id,
                resource_kind="planning_cycle",
                resource_type_key=record_kind,
                now=now,
            )
            published_at = now if status == "published" else None
            connection.execute(
                """
                INSERT INTO planning_cycles (
                    id, scope_id, event_line_id, period, plan_version, status,
                    record_kind, client_id, parent_plan_id, department_id,
                    owner_membership_id, period_kind, period_start, period_end,
                    timezone, title, summary, published_at, archived_at, version,
                    lifecycle_state, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          NULL, 1, 'active', ?, ?, NULL)
                """,
                (
                    planning_cycle_id,
                    identity.scope_id,
                    normalized["eventLineId"],
                    normalized["period"],
                    status,
                    record_kind,
                    client_id,
                    normalized["parentPlanId"],
                    department_id,
                    normalized["ownerMembershipId"],
                    normalized["periodKind"],
                    period_start,
                    period_end,
                    normalized["timezone"],
                    normalized["title"],
                    normalized["summary"],
                    published_at,
                    now,
                    now,
                ),
            )
            row = _planning_row(connection, identity, planning_cycle_id)
            result = {"planningCycle": _planning_payload(row)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.planning_cycle.created",
                aggregate_type="planning_cycle",
                aggregate_id=planning_cycle_id,
                aggregate_version=1,
                payload_hash=payload_hash,
                result=result,
                target_resource_id=planning_cycle_id,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def update_planning_cycle(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    planning_cycle_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    expected = _positive_int(payload.get("expectedVersion"))
    normalized = {
        "planningCycleId": planning_cycle_id,
        "expectedVersion": expected,
        "title": _text(payload.get("title"), limit=500),
        "summary": _text(payload.get("summary")),
        "status": _text(payload.get("status"), limit=30),
    }
    if normalized["status"] and normalized["status"] not in PLAN_STATUSES:
        raise RepositoryError(422, "planning_cycle_status_invalid", "计划状态无效")
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.planning_cycle.updated",
                idempotency_key=idempotency_key,
                aggregate_type="planning_cycle",
                aggregate_id=planning_cycle_id,
                expected_version=expected,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            row = _planning_row(connection, identity, planning_cycle_id)
            _require_plan_permission(
                connection,
                identity,
                record_kind=str(row["record_kind"]),
                department_id=row["department_id"],
                write=True,
            )
            if int(row["version"] or 1) != expected:
                raise RepositoryError(409, "planning_cycle_version_conflict", "计划已被其他成员更新")
            status = normalized["status"] or str(row["status"])
            lifecycle = "archived" if status == "archived" else "active"
            published_at = row["published_at"] or (now if status == "published" else None)
            archived_at = now if status == "archived" else None
            connection.execute(
                """
                UPDATE planning_cycles SET title=?, summary=?, status=?,
                    published_at=?, archived_at=?, lifecycle_state=?,
                    plan_version=plan_version+1, version=version+1, updated_at=?
                WHERE id=? AND scope_id=? AND version=?
                """,
                (
                    normalized["title"] or row["title"],
                    normalized["summary"] if "summary" in payload else row["summary"],
                    status,
                    published_at,
                    archived_at,
                    lifecycle,
                    now,
                    planning_cycle_id,
                    identity.scope_id,
                    expected,
                ),
            )
            connection.execute(
                "UPDATE secured_resources SET lifecycle_state=?, version=version+1, "
                "updated_at=? WHERE id=?",
                (lifecycle, now, planning_cycle_id),
            )
            updated = _planning_row(connection, identity, planning_cycle_id)
            result = {"planningCycle": _planning_payload(updated)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.planning_cycle.updated",
                aggregate_type="planning_cycle",
                aggregate_id=planning_cycle_id,
                aggregate_version=int(updated["version"]),
                payload_hash=payload_hash,
                result=result,
                target_resource_id=planning_cycle_id,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def _safe_review_content(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RepositoryError(422, "weekly_review_content_invalid", "周复盘正文必须是结构化对象")
    result = dict(value)
    encoded = canonical_json(result)
    if len(encoded.encode("utf-8")) > 512_000:
        raise RepositoryError(413, "weekly_review_content_too_large", "周复盘正文过大")
    forbidden_keys = {"localpath", "filepath", "absolutepath", "token", "password", "secret", "apikey"}

    def inspect(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if str(key).replace("_", "").lower() in forbidden_keys:
                    raise RepositoryError(422, "weekly_review_private_field_forbidden", "周复盘不得上传本机路径或秘密字段")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)

    inspect(result)
    return result


def _insert_evidence_source_set(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    client_id: str | None,
    evidence: Sequence[Any],
    purpose_kind: str,
    now: str,
) -> str | None:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int, str]] = set()
    for raw in evidence[:100]:
        if not isinstance(raw, Mapping):
            continue
        source_kind = _text(raw.get("sourceObjectKind"), limit=80)
        source_id = _text(raw.get("sourceObjectId"), limit=200)
        if not source_kind or not source_id:
            continue
        try:
            source_version = max(1, int(raw.get("sourceVersion") or 1))
        except (TypeError, ValueError):
            source_version = 1
        locator = _text(raw.get("locator"), limit=2_000)
        if locator.startswith("file://") or locator.startswith("/") or ":\\" in locator:
            raise RepositoryError(422, "cloud_evidence_local_path_forbidden", "云端证据不得保存本机路径")
        key = (source_kind, source_id, source_version, locator)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "sourceObjectKind": source_kind,
                "sourceObjectId": source_id,
                "sourceVersion": source_version,
                "locator": locator or None,
                "locatorKind": _text(raw.get("locatorKind"), limit=40) or None,
                "pageNo": raw.get("pageNo"),
                "paragraphNo": raw.get("paragraphNo"),
            }
        )
    if not normalized:
        return None
    source_set_id = new_id()
    connection.execute(
        """
        INSERT INTO source_sets (
            id, scope_id, client_id, security_label_set_version, source_count,
            version, purpose_kind, publication_state, created_by_principal_id,
            created_at, expires_at, lifecycle_state, updated_at, deleted_at,
            authority_role, origin_instance_id
        ) VALUES (?, ?, ?, NULL, ?, 1, ?, 'published', ?, ?, NULL, 'active', ?,
                  NULL, 'cloud', ?)
        """,
        (
            source_set_id,
            identity.scope_id,
            client_id,
            len(normalized),
            purpose_kind,
            identity.principal_id,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    for ordinal, item in enumerate(normalized):
        evidence_id = new_id()
        locator_hash = sha256_text(
            f"{item['sourceObjectKind']}|{item['sourceObjectId']}|"
            f"{item['sourceVersion']}|{item['locator'] or ''}"
        )
        connection.execute(
            """
            INSERT INTO evidence_links (
                id, scope_id, fact_id, source_object_id, source_version, locator,
                source_object_kind, locator_kind, page_no, paragraph_no,
                locator_hash, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                identity.scope_id,
                item["sourceObjectId"],
                item["sourceVersion"],
                item["locator"],
                item["sourceObjectKind"],
                item["locatorKind"],
                item["pageNo"],
                item["paragraphNo"],
                locator_hash,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO source_set_members (
                id, scope_id, source_set_id, source_object_id, source_version,
                policy_version, source_object_kind, ordinal, added_at, removed_at,
                version, lifecycle_state, created_at, updated_at, deleted_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, 1, 1, 'evidence_link', ?, ?, NULL, 1,
                      'active', ?, ?, NULL, 'cloud', ?)
            """,
            (
                new_id(),
                identity.scope_id,
                source_set_id,
                evidence_id,
                ordinal,
                now,
                now,
                now,
                repository.cloud_instance_id,
            ),
        )
    return source_set_id


def _insert_review_manifest(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    content: Mapping[str, Any],
    now: str,
) -> tuple[str, str]:
    receipt = canonical_json({"schema": "yiyu.gc06.weekly-review.v1", "content": dict(content)})
    content_hash = sha256_text(receipt)
    manifest_id = new_id()
    connection.execute(
        """
        INSERT INTO object_manifests (
            id, scope_id, storage_key, content_hash, lifecycle_state, receipt,
            holder_role, holder_instance_id, storage_kind, byte_size, media_type,
            availability_state, receipt_hash, created_at, verified_at, deleted_at,
            authority_role, origin_instance_id
        ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud_weekly_review', ?,
                  'metadata_receipt', ?, 'application/json', 'ready', ?, ?, ?,
                  NULL, 'cloud', ?)
        """,
        (
            manifest_id,
            identity.scope_id,
            content_hash,
            receipt,
            repository.cloud_instance_id,
            len(receipt.encode("utf-8")),
            content_hash,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    return manifest_id, content_hash


def _review_row(
    connection: sqlite3.Connection, identity: SessionIdentity, review_id: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM weekly_reviews WHERE scope_id=? AND id=? "
        "AND lifecycle_state!='deleted'",
        (identity.scope_id, review_id),
    ).fetchone()
    if row is None:
        raise RepositoryError(404, "weekly_review_missing", "周复盘不存在")
    if str(row["membership_id"]) != identity.membership_id and not identity.is_admin:
        raise RepositoryError(403, "weekly_review_forbidden", "无权修改其他成员的周复盘")
    return row


def _review_version_payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    content: dict[str, Any] = {}
    if row["content_object_manifest_id"]:
        manifest = connection.execute(
            "SELECT receipt FROM object_manifests WHERE scope_id=? AND id=?",
            (row["scope_id"], row["content_object_manifest_id"]),
        ).fetchone()
        if manifest is not None:
            try:
                decoded = json.loads(str(manifest["receipt"] or "{}"))
                if isinstance(decoded, Mapping) and isinstance(decoded.get("content"), Mapping):
                    content = dict(decoded["content"])
            except json.JSONDecodeError:
                content = {}
    return {
        "id": str(row["id"]),
        "reviewId": row["review_id"],
        "sourceSetId": row["source_set_id"],
        "version": int(row["version"] or 1),
        "businessState": str(row["business_state"] or "draft"),
        "basedOnVersionId": row["based_on_version_id"],
        "effectiveAt": row["effective_at"],
        "recordKind": row["record_kind"],
        "contentHash": row["content_hash"],
        "content": content,
        "reviewNote": str(row["review_note"] or ""),
        "submittedAt": row["submitted_at"],
        "createdAt": row["created_at"],
    }


def _review_payload(connection: sqlite3.Connection, row: sqlite3.Row, *, include_versions: bool = True) -> dict[str, Any]:
    result = {
        "id": str(row["id"]),
        "membershipId": str(row["membership_id"]),
        "planningCycleId": str(row["planning_cycle_id"]),
        "currentDraftVersionId": row["current_draft_version_id"],
        "currentSubmittedVersionId": row["current_submitted_version_id"],
        "status": str(row["status"]),
        "version": int(row["version"] or 1),
        "lifecycleState": str(row["lifecycle_state"]),
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
    }
    if include_versions:
        versions = connection.execute(
            "SELECT * FROM weekly_review_versions WHERE scope_id=? AND review_id=? "
            "ORDER BY version, created_at, id",
            (row["scope_id"], row["id"]),
        ).fetchall()
        result["versions"] = [_review_version_payload(connection, item) for item in versions]
    return result


def list_weekly_reviews(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    planning_cycle_id: str | None = None,
    membership_id: str | None = None,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        clauses = ["scope_id=?", "lifecycle_state!='deleted'"]
        params: list[Any] = [identity.scope_id]
        if planning_cycle_id:
            clauses.append("planning_cycle_id=?")
            params.append(planning_cycle_id)
        target_membership = membership_id or (None if identity.is_admin else identity.membership_id)
        if target_membership:
            if target_membership != identity.membership_id and not identity.is_admin:
                raise RepositoryError(403, "weekly_review_forbidden", "无权读取其他成员复盘")
            clauses.append("membership_id=?")
            params.append(target_membership)
        rows = connection.execute(
            "SELECT * FROM weekly_reviews WHERE " + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, id DESC",
            params,
        ).fetchall()
        return [_review_payload(connection, row) for row in rows]


def save_weekly_review_draft(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    planning_cycle_id = _required_text(payload.get("planningCycleId"), "planning_cycle_required", "周复盘必须属于计划周期", limit=200)
    membership_id = _text(payload.get("membershipId"), limit=200) or identity.membership_id
    content = _safe_review_content(payload.get("content") or {})
    expected_raw = payload.get("expectedVersion")
    try:
        expected = int(expected_raw or 0)
    except (TypeError, ValueError):
        expected = -1
    normalized = {
        "planningCycleId": planning_cycle_id,
        "membershipId": membership_id,
        "expectedVersion": expected,
        "content": content,
        "reviewNote": _text(payload.get("reviewNote"), limit=10_000),
        "evidence": list(payload.get("evidence") or []),
    }
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            cycle = _planning_row(connection, identity, planning_cycle_id)
            _require_plan_permission(
                connection,
                identity,
                record_kind=str(cycle["record_kind"]),
                department_id=cycle["department_id"],
                write=False,
            )
            _membership_row(connection, identity, membership_id)
            if membership_id != identity.membership_id and not identity.is_admin:
                raise RepositoryError(403, "weekly_review_forbidden", "不能代写其他成员复盘")
            existing = connection.execute(
                "SELECT * FROM weekly_reviews WHERE scope_id=? AND membership_id=? "
                "AND planning_cycle_id=? AND lifecycle_state!='deleted'",
                (identity.scope_id, membership_id, planning_cycle_id),
            ).fetchone()
            if existing is not None and expected <= 0:
                raise RepositoryError(422, "expected_version_required", "已有周复盘必须提供 expectedVersion")
            if existing is None and expected not in {0}:
                raise RepositoryError(409, "weekly_review_version_conflict", "周复盘稳定身份尚未建立")
            review_id = str(existing["id"]) if existing is not None else (_text(payload.get("reviewId"), limit=200) or new_id())
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.weekly_review.draft_saved",
                idempotency_key=idempotency_key,
                aggregate_type="weekly_review",
                aggregate_id=review_id,
                expected_version=expected or None,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            if existing is not None and int(existing["version"] or 1) != expected:
                raise RepositoryError(409, "weekly_review_version_conflict", "周复盘已被更新")
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO weekly_reviews (
                        id, scope_id, planning_cycle_id,
                        current_submitted_version_id, membership_id,
                        current_draft_version_id, status, version,
                        lifecycle_state, created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, NULL, ?, NULL, 'draft', 1, 'active', ?, ?, NULL)
                    """,
                    (review_id, identity.scope_id, planning_cycle_id, membership_id, now, now),
                )
                aggregate_version = 1
                based_on = None
            else:
                aggregate_version = int(existing["version"] or 1) + 1
                based_on = existing["current_draft_version_id"] or existing["current_submitted_version_id"]
            version_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM weekly_review_versions "
                    "WHERE scope_id=? AND review_id=?",
                    (identity.scope_id, review_id),
                ).fetchone()[0]
            )
            source_set_id = _insert_evidence_source_set(
                repository,
                connection,
                identity,
                client_id=str(cycle["client_id"]) if cycle["client_id"] else None,
                evidence=normalized["evidence"],
                purpose_kind="weekly_review_evidence",
                now=now,
            )
            manifest_id, content_hash = _insert_review_manifest(
                repository, connection, identity, content=content, now=now
            )
            version_id = new_id()
            connection.execute(
                """
                INSERT INTO weekly_review_versions (
                    id, scope_id, review_id, source_set_id, version,
                    business_state, based_on_version_id, effective_at,
                    source_command_id, record_kind, section_type,
                    content_object_manifest_id, content_hash, task_id,
                    review_note, submitted_at, origin_instance_id, created_at,
                    integrity_hash
                    ) VALUES (?, ?, ?, ?, ?, 'draft', ?, NULL, ?, 'version',
                          'structured_review', ?, ?, NULL, ?, NULL, ?, ?, ?)
                """,
                (
                    version_id,
                    identity.scope_id,
                    review_id,
                    source_set_id,
                    version_no,
                    based_on,
                    command_id,
                    manifest_id,
                    content_hash,
                    normalized["reviewNote"],
                    repository.cloud_instance_id,
                    now,
                    sha256_text(f"{review_id}|{version_no}|{content_hash}|draft"),
                ),
            )
            connection.execute(
                "UPDATE weekly_reviews SET current_draft_version_id=?, status='draft', "
                "version=?, updated_at=? WHERE id=? AND scope_id=?",
                (version_id, aggregate_version, now, review_id, identity.scope_id),
            )
            review = _review_row(connection, identity, review_id)
            result = {"weeklyReview": _review_payload(connection, review)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.weekly_review.draft_saved",
                aggregate_type="weekly_review",
                aggregate_id=review_id,
                aggregate_version=aggregate_version,
                payload_hash=payload_hash,
                result=result,
                target_resource_id=None,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def _copy_review_version(
    connection: sqlite3.Connection,
    *,
    source: sqlite3.Row,
    version_id: str,
    version_no: int,
    business_state: str,
    command_id: str,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO weekly_review_versions (
            id, scope_id, review_id, source_set_id, version, business_state,
            based_on_version_id, effective_at, source_command_id, record_kind,
            section_type, content_object_manifest_id, content_hash, task_id,
            review_note, submitted_at, origin_instance_id, created_at,
            integrity_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            source["scope_id"],
            source["review_id"],
            source["source_set_id"],
            version_no,
            business_state,
            source["id"],
            now if business_state == "submitted" else None,
            command_id,
            source["record_kind"],
            source["section_type"],
            source["content_object_manifest_id"],
            source["content_hash"],
            source["task_id"],
            source["review_note"],
            now if business_state == "submitted" else None,
            source["origin_instance_id"],
            now,
            sha256_text(
                f"{source['review_id']}|{version_no}|{source['content_hash']}|{business_state}"
            ),
        ),
    )


def transition_weekly_review(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    review_id: str,
    transition: str,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, Any]:
    if transition not in {"submit", "return", "reopen"}:
        raise RepositoryError(422, "weekly_review_transition_invalid", "复盘状态动作无效")
    expected = _positive_int(expected_version)
    normalized = {"reviewId": review_id, "transition": transition, "expectedVersion": expected}
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    command_type = f"gc06.weekly_review.{transition}ed"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            review = _review_row(connection, identity, review_id)
            if transition == "return" and not identity.is_admin:
                raise RepositoryError(403, "weekly_review_return_forbidden", "只有管理员可退回复盘")
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="weekly_review",
                aggregate_id=review_id,
                expected_version=expected,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            if int(review["version"] or 1) != expected:
                raise RepositoryError(409, "weekly_review_version_conflict", "周复盘已被更新")
            source_id = (
                review["current_draft_version_id"]
                if transition == "submit"
                else review["current_submitted_version_id"]
            )
            if not source_id:
                raise RepositoryError(409, "weekly_review_transition_source_missing", "当前没有可用于该动作的复盘版本")
            source = connection.execute(
                "SELECT * FROM weekly_review_versions WHERE scope_id=? AND id=?",
                (identity.scope_id, source_id),
            ).fetchone()
            if source is None:
                raise RepositoryError(409, "weekly_review_version_missing", "复盘版本不存在")
            version_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version),0)+1 FROM weekly_review_versions "
                    "WHERE scope_id=? AND review_id=?",
                    (identity.scope_id, review_id),
                ).fetchone()[0]
            )
            version_id = new_id()
            state = "submitted" if transition == "submit" else transition + "ed"
            _copy_review_version(
                connection,
                source=source,
                version_id=version_id,
                version_no=version_no,
                business_state=state,
                command_id=command_id,
                now=now,
            )
            next_aggregate = expected + 1
            if transition == "submit":
                connection.execute(
                    "UPDATE weekly_reviews SET current_submitted_version_id=?, "
                    "current_draft_version_id=NULL, status='submitted', version=?, "
                    "updated_at=? WHERE id=?",
                    (version_id, next_aggregate, now, review_id),
                )
            else:
                status = "returned" if transition == "return" else "draft"
                connection.execute(
                    "UPDATE weekly_reviews SET current_draft_version_id=?, status=?, "
                    "version=?, updated_at=? WHERE id=?",
                    (version_id, status, next_aggregate, now, review_id),
                )
            updated = _review_row(connection, identity, review_id)
            growth_candidate = None
            if transition == "submit":
                cycle = _planning_row(
                    connection,
                    identity,
                    str(updated["planning_cycle_id"]),
                )
                if cycle["event_line_id"]:
                    line = require_active_event_line(
                        connection,
                        scope_id=identity.scope_id,
                        event_line_id=cycle["event_line_id"],
                    )
                    _upsert_event_activity(
                        repository,
                        connection,
                        identity,
                        event_line=line,
                        source_type="weekly_review",
                        source_id=review_id,
                        happened_at=now,
                        title=f"周复盘已提交：{str(cycle['title'] or '')}",
                        summary=str(source["review_note"] or ""),
                        include_in_narrative=True,
                        now=now,
                    )
                # 正式周复盘由成长陪伴 Agent 在成长读模型刷新时自动消费。
                # 不再创建要求成员逐条确认的 ai_proposals，也不在任务日程页暴露
                # “个人成长权威”审批流程。
                growth_candidate = {
                    "status": "scheduled",
                    "sourceType": "weekly_review",
                    "reviewId": review_id,
                    "reviewVersionId": version_id,
                    "agentKind": "growth_companion",
                }
            result = {
                "weeklyReview": _review_payload(connection, updated),
                "growthCandidate": growth_candidate,
            }
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                aggregate_type="weekly_review",
                aggregate_id=review_id,
                aggregate_version=next_aggregate,
                payload_hash=payload_hash,
                result=result,
                target_resource_id=None,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def _action_row(
    connection: sqlite3.Connection, identity: SessionIdentity, action_id: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM decision_actions WHERE scope_id=? AND id=? "
        "AND lifecycle_state!='deleted'",
        (identity.scope_id, action_id),
    ).fetchone()
    if row is None:
        raise RepositoryError(404, "decision_action_missing", "决策行动不存在")
    return row


def _action_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "recordKind": str(row["record_kind"]),
        "planningCycleId": row["planning_cycle_id"],
        "clientId": row["client_id"],
        "sourceSetId": row["source_set_id"],
        "taskId": row["task_id"],
        "decisionState": str(row["decision_state"]),
        "title": str(row["title"]),
        "statement": str(row["statement"] or ""),
        "expectedOutput": str(row["expected_output"] or ""),
        "ownerMembershipId": row["owner_membership_id"],
        "confirmedAt": row["confirmed_at"],
        "version": int(row["version"]),
        "lifecycleState": str(row["lifecycle_state"]),
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
    }


def _source_set_evidence(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    source_set_id: str | None,
) -> list[dict[str, Any]]:
    if not source_set_id:
        return []
    rows = connection.execute(
        """
        SELECT evidence.source_object_kind, evidence.source_object_id,
               evidence.source_version, evidence.locator,
               evidence.locator_kind, evidence.page_no, evidence.paragraph_no
        FROM source_set_members AS member
        JOIN evidence_links AS evidence
          ON evidence.scope_id=member.scope_id
         AND evidence.id=member.source_object_id
        WHERE member.scope_id=? AND member.source_set_id=?
          AND member.source_object_kind='evidence_link'
          AND member.lifecycle_state='active'
        ORDER BY member.ordinal, member.id
        """,
        (scope_id, source_set_id),
    ).fetchall()
    return [
        {
            "sourceObjectKind": str(row["source_object_kind"]),
            "sourceObjectId": str(row["source_object_id"]),
            "sourceVersion": int(row["source_version"] or 1),
            "locator": row["locator"],
            "locatorKind": row["locator_kind"],
            "pageNo": row["page_no"],
            "paragraphNo": row["paragraph_no"],
        }
        for row in rows
    ]


def list_decision_actions(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    planning_cycle_id: str | None = None,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        clauses = ["scope_id=?", "lifecycle_state!='deleted'"]
        params: list[Any] = [identity.scope_id]
        if planning_cycle_id:
            clauses.append("planning_cycle_id=?")
            params.append(planning_cycle_id)
        rows = connection.execute(
            "SELECT * FROM decision_actions WHERE " + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, id DESC",
            params,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            if row["planning_cycle_id"]:
                cycle = _planning_row(connection, identity, str(row["planning_cycle_id"]))
                try:
                    _require_plan_permission(
                        connection,
                        identity,
                        record_kind=str(cycle["record_kind"]),
                        department_id=cycle["department_id"],
                        write=False,
                    )
                except RepositoryError:
                    continue
            result.append(_action_payload(row))
        return result


def create_decision_action(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    action_id = _text(payload.get("actionId") or payload.get("id"), limit=200) or new_id()
    planning_cycle_id = _required_text(payload.get("planningCycleId"), "planning_cycle_required", "决策行动必须属于计划周期", limit=200)
    record_kind = _text(payload.get("recordKind"), limit=30) or "plan_action"
    state = _text(payload.get("decisionState"), limit=30) or "draft"
    if record_kind not in ACTION_KINDS or state not in {"draft", "confirmed"}:
        raise RepositoryError(422, "decision_action_state_invalid", "决策行动类型或状态无效")
    evidence = list(payload.get("evidence") or [])
    review_version_id = _text(payload.get("reviewVersionId"), limit=200)
    if review_version_id:
        evidence.append(
            {
                "sourceObjectKind": "weekly_review_version",
                "sourceObjectId": review_version_id,
                "sourceVersion": 1,
            }
        )
    if state == "confirmed" and not evidence:
        raise RepositoryError(422, "decision_action_evidence_required", "确认行动必须保留证据")
    normalized = {
        "actionId": action_id,
        "planningCycleId": planning_cycle_id,
        "recordKind": record_kind,
        "decisionState": state,
        "title": _required_text(payload.get("title"), "decision_action_title_required", "行动标题不能为空", limit=500),
        "statement": _text(payload.get("statement")),
        "expectedOutput": _text(payload.get("expectedOutput")),
        "ownerMembershipId": _text(payload.get("ownerMembershipId"), limit=200)
        or identity.membership_id,
        "clientId": _text(payload.get("clientId"), limit=200) or None,
        "evidence": evidence,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            cycle = _planning_row(connection, identity, planning_cycle_id)
            _require_plan_permission(
                connection,
                identity,
                record_kind=str(cycle["record_kind"]),
                department_id=cycle["department_id"],
                write=True,
            )
            cycle_client = str(cycle["client_id"]) if cycle["client_id"] else None
            if cycle_client:
                require_active_client(
                    connection,
                    scope_id=identity.scope_id,
                    client_id=cycle_client,
                )
            if normalized["clientId"] and normalized["clientId"] != cycle_client:
                raise RepositoryError(409, "decision_action_client_mismatch", "行动与计划客户项目不一致")
            _membership_row(connection, identity, normalized["ownerMembershipId"])
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.decision_action.created",
                idempotency_key=idempotency_key,
                aggregate_type="decision_action",
                aggregate_id=action_id,
                expected_version=None,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            source_set_id = _insert_evidence_source_set(
                repository,
                connection,
                identity,
                client_id=cycle_client,
                evidence=evidence,
                purpose_kind="decision_action_evidence",
                now=now,
            )
            connection.execute(
                """
                INSERT INTO decision_actions (
                    id, scope_id, source_set_id, task_id, decision_state,
                    version, record_kind, planning_cycle_id, client_id, title,
                    statement, expected_output, owner_membership_id, confirmed_at,
                    lifecycle_state, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, NULL, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 'active',
                          ?, ?, NULL)
                """,
                (
                    action_id,
                    identity.scope_id,
                    source_set_id,
                    state,
                    record_kind,
                    planning_cycle_id,
                    cycle_client,
                    normalized["title"],
                    normalized["statement"],
                    normalized["expectedOutput"],
                    normalized["ownerMembershipId"],
                    now if state == "confirmed" else None,
                    now,
                    now,
                ),
            )
            action = _action_row(connection, identity, action_id)
            if state == "confirmed" and cycle["event_line_id"]:
                line = require_active_event_line(
                    connection,
                    scope_id=identity.scope_id,
                    event_line_id=cycle["event_line_id"],
                )
                _upsert_event_activity(
                    repository,
                    connection,
                    identity,
                    event_line=line,
                    source_type="decision_action",
                    source_id=action_id,
                    happened_at=now,
                    title=normalized["title"],
                    summary=normalized["statement"],
                    include_in_narrative=True,
                    now=now,
                )
            result = {"decisionAction": _action_payload(action)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.decision_action.created",
                aggregate_type="decision_action",
                aggregate_id=action_id,
                aggregate_version=1,
                payload_hash=payload_hash,
                result=result,
                target_resource_id=None,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def update_decision_action(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    action_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    expected = _positive_int(payload.get("expectedVersion"))
    normalized = {
        "actionId": action_id,
        "expectedVersion": expected,
        "decisionState": _text(payload.get("decisionState"), limit=30),
        "title": _text(payload.get("title"), limit=500),
        "statement": _text(payload.get("statement")),
        "expectedOutput": _text(payload.get("expectedOutput")),
        "evidence": list(payload.get("evidence") or []),
    }
    if normalized["decisionState"] and normalized["decisionState"] not in ACTION_STATES:
        raise RepositoryError(422, "decision_action_state_invalid", "决策行动状态无效")
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            action = _action_row(connection, identity, action_id)
            cycle = _planning_row(
                connection,
                identity,
                str(action["planning_cycle_id"]),
            )
            _require_plan_permission(
                connection,
                identity,
                record_kind=str(cycle["record_kind"]),
                department_id=cycle["department_id"],
                write=True,
            )
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.decision_action.updated",
                idempotency_key=idempotency_key,
                aggregate_type="decision_action",
                aggregate_id=action_id,
                expected_version=expected,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            if int(action["version"]) != expected:
                raise RepositoryError(409, "decision_action_version_conflict", "行动已被更新")
            state = normalized["decisionState"] or str(action["decision_state"])
            current_state = str(action["decision_state"])
            if state not in ACTION_TRANSITIONS.get(current_state, frozenset()):
                raise RepositoryError(
                    409,
                    "decision_action_transition_invalid",
                    "决策行动状态不能执行该跳转",
                )
            source_set_id = action["source_set_id"]
            if normalized["evidence"]:
                combined_evidence = _source_set_evidence(
                    connection,
                    scope_id=identity.scope_id,
                    source_set_id=str(source_set_id) if source_set_id else None,
                ) + normalized["evidence"]
                source_set_id = _insert_evidence_source_set(
                    repository,
                    connection,
                    identity,
                    client_id=str(action["client_id"]) if action["client_id"] else None,
                    evidence=combined_evidence,
                    purpose_kind="decision_action_evidence",
                    now=now,
                )
            if state in {"confirmed", "completed"} and not source_set_id:
                raise RepositoryError(
                    422,
                    "decision_action_evidence_required",
                    "确认行动必须保留证据",
                )
            confirmed_at = action["confirmed_at"]
            if state == "confirmed" and not confirmed_at:
                confirmed_at = now
            update_cursor = connection.execute(
                "UPDATE decision_actions SET source_set_id=?, decision_state=?, "
                "title=?, statement=?, expected_output=?, confirmed_at=?, "
                "version=version+1, updated_at=? WHERE id=? AND scope_id=? AND version=?",
                (
                    source_set_id,
                    state,
                    normalized["title"] or action["title"],
                    normalized["statement"] if "statement" in payload else action["statement"],
                    normalized["expectedOutput"] if "expectedOutput" in payload else action["expected_output"],
                    confirmed_at,
                    now,
                    action_id,
                    identity.scope_id,
                    expected,
                ),
            )
            if update_cursor.rowcount != 1:
                raise RepositoryError(409, "decision_action_version_conflict", "行动已被更新")
            updated = _action_row(connection, identity, action_id)
            if state in {"confirmed", "completed"} and cycle["event_line_id"]:
                line = require_active_event_line(
                    connection,
                    scope_id=identity.scope_id,
                    event_line_id=cycle["event_line_id"],
                )
                _upsert_event_activity(
                    repository,
                    connection,
                    identity,
                    event_line=line,
                    source_type="decision_action",
                    source_id=action_id,
                    happened_at=now,
                    title=str(updated["title"]),
                    summary=str(updated["statement"] or ""),
                    include_in_narrative=True,
                    now=now,
                )
            result = {"decisionAction": _action_payload(updated)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.decision_action.updated",
                aggregate_type="decision_action",
                aggregate_id=action_id,
                aggregate_version=int(updated["version"]),
                payload_hash=payload_hash,
                result=result,
                target_resource_id=None,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def convert_action_to_primary_task(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    action_id: str,
    expected_version: int,
    idempotency_key: str,
    task_command_port: FormalTaskCommandPort = UNAVAILABLE_FORMAL_TASK_COMMAND_PORT,
) -> dict[str, Any]:
    expected = _positive_int(expected_version)
    with repository._connection() as connection:  # noqa: SLF001
        action = _action_row(connection, identity, action_id)
        if int(action["version"]) != expected:
            raise RepositoryError(409, "decision_action_version_conflict", "行动已被更新")
        if str(action["decision_state"]) != "confirmed":
            raise RepositoryError(
                409,
                "decision_action_not_confirmed",
                "只有已确认行动可以转为正式任务",
            )
        if action["task_id"]:
            task = connection.execute(
                "SELECT * FROM tasks WHERE scope_id=? AND id=?",
                (identity.scope_id, action["task_id"]),
            ).fetchone()
            return {
                "decisionAction": _action_payload(action),
                "task": dict(task) if task is not None else None,
                "idempotentReplay": True,
            }
        cycle = _planning_row(connection, identity, str(action["planning_cycle_id"]))
        _require_plan_permission(
            connection,
            identity,
            record_kind=str(cycle["record_kind"]),
            department_id=cycle["department_id"],
            write=True,
        )
        action_payload = _action_payload(action)
    receipt = task_command_port.create_primary_task_for_action(
        repository,
        identity,
        action=action_payload,
        idempotency_key=f"{idempotency_key}:formal-task-create",
    )
    task_payload = receipt.get("task") if isinstance(receipt, Mapping) else None
    task_id = _text(
        (task_payload or {}).get("id") if isinstance(task_payload, Mapping) else receipt.get("taskId")
        if isinstance(receipt, Mapping)
        else None,
        limit=200,
    )
    if not task_id:
        raise RepositoryError(502, "formal_task_receipt_invalid", "正式任务命令未返回任务 ID")
    # GC-04 的正式创建命令在 sourceType=decision_action 时已经在同一事务
    # 把任务挂回该行动。此处只兼容尚未具备原子绑定的旧命令端口，不能再写一次
    # 导致行动版本冲突。
    with repository._connection() as connection:  # noqa: SLF001
        attached_action = _action_row(connection, identity, action_id)
        if str(attached_action["task_id"] or "") == task_id:
            task = connection.execute(
                "SELECT * FROM tasks WHERE scope_id=? AND id=? AND lifecycle_state!='deleted'",
                (identity.scope_id, task_id),
            ).fetchone()
            return {
                "decisionAction": _action_payload(attached_action),
                "task": dict(task) if task is not None else dict(task_payload or {}),
                "taskCommandReceipt": dict(receipt),
                "idempotentReplay": bool(receipt.get("idempotentReplay")) if isinstance(receipt, Mapping) else False,
            }
    normalized = {"actionId": action_id, "expectedVersion": expected, "taskId": task_id}
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.decision_action.primary_task_attached",
                idempotency_key=idempotency_key,
                aggregate_type="decision_action",
                aggregate_id=action_id,
                expected_version=expected,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            action = _action_row(connection, identity, action_id)
            if int(action["version"]) != expected:
                raise RepositoryError(409, "decision_action_version_conflict", "行动已被更新")
            task = connection.execute(
                "SELECT * FROM tasks WHERE scope_id=? AND id=? AND lifecycle_state!='deleted'",
                (identity.scope_id, task_id),
            ).fetchone()
            if task is None:
                raise RepositoryError(502, "formal_task_receipt_invalid", "正式任务命令未持久化任务")
            action_client = str(action["client_id"] or "")
            task_client = str(task["client_id"] or "")
            if action_client != task_client:
                raise RepositoryError(409, "decision_action_task_client_mismatch", "承接任务与行动客户项目不一致")
            used = connection.execute(
                "SELECT id FROM decision_actions WHERE scope_id=? AND task_id=? AND id!=?",
                (identity.scope_id, task_id, action_id),
            ).fetchone()
            if used is not None:
                raise RepositoryError(409, "task_already_primary_for_action", "该任务已承接另一项主要行动")
            connection.execute(
                "UPDATE decision_actions SET task_id=?, version=version+1, "
                "updated_at=? WHERE id=? AND scope_id=? AND version=?",
                (task_id, now, action_id, identity.scope_id, expected),
            )
            updated = _action_row(connection, identity, action_id)
            result = {
                "decisionAction": _action_payload(updated),
                "task": dict(task),
                "taskCommandReceipt": dict(receipt),
            }
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.decision_action.primary_task_attached",
                aggregate_type="decision_action",
                aggregate_id=action_id,
                aggregate_version=int(updated["version"]),
                payload_hash=payload_hash,
                result=result,
                target_resource_id=None,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def list_plan_item_tasks(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    plan_item_id: str | None = None,
) -> dict[str, Any]:
    """Read the strict action→task relation without reviving legacy task attributes."""

    task_repository = GC04TaskRepository(repository)
    board = task_repository.board(identity)
    visible_tasks = {
        str(task.get("id") or ""): task
        for task in board.get("tasks") or []
        if str(task.get("id") or "")
    }
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT id, task_id FROM decision_actions WHERE scope_id=? "
            "AND lifecycle_state='active' AND decision_state!='dropped' "
            "AND task_id IS NOT NULL ORDER BY updated_at DESC, id",
            (identity.scope_id,),
        ).fetchall()
    counts: dict[str, int] = {}
    matches: list[dict[str, Any]] = []
    for row in rows:
        action_id = str(row["id"])
        task = visible_tasks.get(str(row["task_id"] or ""))
        if task is None:
            continue
        counts[action_id] = counts.get(action_id, 0) + 1
        if plan_item_id and action_id == plan_item_id:
            matches.append(task)
    return {"tasks": matches, "counts": counts}


def get_task_plan_link(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    task_id: str,
) -> dict[str, Any] | None:
    task_repository = GC04TaskRepository(repository)
    with repository._connection() as connection:  # noqa: SLF001
        task_repository._require_task_read(connection, identity, task_id)  # noqa: SLF001
        action = connection.execute(
            "SELECT * FROM decision_actions WHERE scope_id=? AND task_id=? "
            "AND lifecycle_state='active' AND decision_state!='dropped' "
            "ORDER BY updated_at DESC, id LIMIT 1",
            (identity.scope_id, task_id),
        ).fetchone()
    if action is None:
        return None
    return {
        "taskId": task_id,
        "departmentPlanItemId": str(action["id"]),
        "focusItemId": None,
        "linkedBy": "manager",
        "confidence": 1.0,
        "version": int(action["version"] or 1),
        "updatedAt": str(action["updated_at"] or ""),
    }


def set_task_plan_link(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    task_id: str,
    action_id: str | None,
    idempotency_key: str,
) -> dict[str, Any] | None:
    """Attach one formal task to at most one primary decision action."""

    normalized = {"taskId": task_id, "actionId": action_id}
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    task_repository = GC04TaskRepository(repository)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            task = task_repository._require_task_write(  # noqa: SLF001
                connection, identity, task_id
            )
            current = connection.execute(
                "SELECT * FROM decision_actions WHERE scope_id=? AND task_id=? "
                "AND lifecycle_state='active' AND decision_state!='dropped' "
                "ORDER BY updated_at DESC, id LIMIT 1",
                (identity.scope_id, task_id),
            ).fetchone()
            target = None
            if action_id:
                target = _action_row(connection, identity, action_id)
                cycle = _planning_row(
                    connection, identity, str(target["planning_cycle_id"])
                )
                _require_plan_permission(
                    connection,
                    identity,
                    record_kind=str(cycle["record_kind"]),
                    department_id=cycle["department_id"],
                    write=True,
                )
                action_client = str(target["client_id"] or "")
                task_client = str(task["client_id"] or "")
                if action_client and action_client != task_client:
                    raise RepositoryError(
                        409,
                        "decision_action_task_client_mismatch",
                        "任务与计划行动的客户项目不一致",
                    )
                if target["task_id"] and str(target["task_id"]) != task_id:
                    raise RepositoryError(
                        409,
                        "decision_action_primary_task_exists",
                        "该计划项已经关联另一条主要任务",
                    )
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.task_plan_link.updated",
                idempotency_key=idempotency_key,
                aggregate_type="task",
                aggregate_id=task_id,
                expected_version=int(task["version"] or 1),
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay.get("planLink")
            if current is not None and (target is None or str(current["id"]) != str(target["id"])):
                connection.execute(
                    "UPDATE decision_actions SET task_id=NULL, version=version+1, "
                    "updated_at=? WHERE id=? AND scope_id=? AND version=?",
                    (now, current["id"], identity.scope_id, int(current["version"])),
                )
            if target is not None and str(target["task_id"] or "") != task_id:
                cursor = connection.execute(
                    "UPDATE decision_actions SET task_id=?, version=version+1, "
                    "updated_at=? WHERE id=? AND scope_id=? AND version=?",
                    (task_id, now, target["id"], identity.scope_id, int(target["version"])),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(409, "decision_action_version_conflict", "计划项已被更新")
            updated = None
            if target is not None:
                updated = _action_row(connection, identity, str(target["id"]))
            plan_link = None if updated is None else {
                "taskId": task_id,
                "departmentPlanItemId": str(updated["id"]),
                "focusItemId": None,
                "linkedBy": "manager",
                "confidence": 1.0,
                "version": int(updated["version"] or 1),
                "updatedAt": str(updated["updated_at"] or now),
            }
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.task_plan_link.updated",
                aggregate_type="task",
                aggregate_id=task_id,
                aggregate_version=int(task["version"] or 1),
                payload_hash=payload_hash,
                result={"planLink": plan_link},
                target_resource_id=None,
                now=now,
            )
            connection.commit()
            return settled.get("planLink")
        except Exception:
            connection.rollback()
            raise


def _meeting_row(
    connection: sqlite3.Connection, identity: SessionIdentity, meeting_id: str
) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM meetings WHERE scope_id=? AND id=? AND lifecycle_state!='deleted'",
        (identity.scope_id, meeting_id),
    ).fetchone()
    if row is None:
        raise RepositoryError(404, "meeting_missing", "会议不存在")
    return row


def _meeting_collaboration_payload(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    meeting_id: str,
) -> tuple[str | None, list[dict[str, Any]]]:
    rows = connection.execute(
        """
        SELECT object_grants.*, principals.display_name
        FROM object_grants
        JOIN organization_memberships
          ON organization_memberships.id=object_grants.subject_membership_id
         AND organization_memberships.scope_id=object_grants.scope_id
        JOIN principals ON principals.id=organization_memberships.principal_id
        WHERE object_grants.scope_id=?
          AND object_grants.secured_resource_id=?
          AND object_grants.capability_set_schema_version=?
          AND object_grants.lifecycle_state='active'
          AND object_grants.status!='revoked'
        ORDER BY object_grants.created_at, object_grants.id
        """,
        (scope_id, meeting_id, MEETING_COLLABORATION_SCHEMA),
    ).fetchall()
    creator_membership_id: str | None = None
    collaborators: list[dict[str, Any]] = []
    for grant in rows:
        try:
            capabilities = json.loads(str(grant["capability_set"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            capabilities = {}
        role_key = _text(capabilities.get("roleKey"), limit=40) or "collaborator"
        membership_id = str(grant["subject_membership_id"] or "")
        if role_key in {"creator", "creator_owner"}:
            creator_membership_id = membership_id
        status = str(grant["status"] or "pending")
        collaborators.append({
            "grantId": str(grant["id"]),
            "membershipId": membership_id,
            "displayName": str(grant["display_name"] or "组织成员"),
            "roleKey": "owner" if role_key in {"owner", "creator_owner"} else role_key,
            "inboxStatus": "accepted" if status == "active" else status,
            "version": int(grant["version"] or 1),
        })
    return creator_membership_id, collaborators


def _meeting_plan_link_payload(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    meeting_id: str,
) -> dict[str, Any] | None:
    lineage = connection.execute(
        "SELECT * FROM derivation_lineage WHERE scope_id=? AND derivative_kind=? "
        "AND derivative_object_id=? AND invalidated_at IS NULL ORDER BY generated_at DESC,id DESC LIMIT 1",
        (scope_id, MEETING_PLAN_LINK_PURPOSE, meeting_id),
    ).fetchone()
    if lineage is None:
        return None
    members = connection.execute(
        "SELECT * FROM source_set_members WHERE scope_id=? AND source_set_id=? "
        "AND lifecycle_state='active' AND removed_at IS NULL ORDER BY ordinal,id",
        (scope_id, lineage["source_set_id"]),
    ).fetchall()
    by_kind = {str(item["source_object_kind"]): str(item["source_object_id"]) for item in members}
    return {
        "sourceSetId": str(lineage["source_set_id"]),
        "planningCycleId": by_kind.get("planning_cycle"),
        "decisionActionId": by_kind.get("decision_action"),
    }


def _meeting_payload(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    creator_membership_id, collaborators = _meeting_collaboration_payload(
        connection,
        scope_id=str(row["scope_id"]),
        meeting_id=str(row["id"]),
    )
    return {
        "id": str(row["id"]),
        "clientId": str(row["client_id"]),
        "eventLineId": row["event_line_id"],
        "title": str(row["title"] or ""),
        "agenda": str(row["agenda"] or ""),
        "startsAt": str(row["starts_at"]),
        "endsAt": str(row["ends_at"]),
        "timezone": str(row["timezone"] or "Asia/Shanghai"),
        "organizerMembershipId": row["organizer_membership_id"],
        "createdByMembershipId": creator_membership_id,
        "collaborators": collaborators,
        "planLink": _meeting_plan_link_payload(
            connection,
            scope_id=str(row["scope_id"]),
            meeting_id=str(row["id"]),
        ),
        "visibilityScope": row["visibility_scope"],
        "status": str(row["status"]),
        "version": int(row["version"] or 1),
        "lifecycleState": str(row["lifecycle_state"]),
        "createdAt": str(row["created_at"]),
        "updatedAt": str(row["updated_at"]),
    }


def _sync_meeting_collaborators(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    meeting_id: str,
    organizer_membership_id: str,
    collaborator_membership_ids: Sequence[str],
    now: str,
    preserve_creator: bool,
) -> None:
    existing = connection.execute(
        "SELECT * FROM object_grants WHERE scope_id=? AND secured_resource_id=? "
        "AND capability_set_schema_version=? AND lifecycle_state='active'",
        (identity.scope_id, meeting_id, MEETING_COLLABORATION_SCHEMA),
    ).fetchall()
    existing_by_member = {
        str(row["subject_membership_id"]): row
        for row in existing
        if row["subject_membership_id"]
    }
    creator_membership_id = identity.membership_id
    if preserve_creator:
        for row in existing:
            try:
                capability = json.loads(str(row["capability_set"] or "{}"))
            except (TypeError, ValueError, json.JSONDecodeError):
                capability = {}
            if capability.get("roleKey") in {"creator", "creator_owner"}:
                creator_membership_id = str(row["subject_membership_id"])
                break

    desired_roles: dict[str, str] = {creator_membership_id: "creator"}
    desired_roles[organizer_membership_id] = (
        "creator_owner" if organizer_membership_id == creator_membership_id else "owner"
    )
    for membership_id in collaborator_membership_ids:
        normalized = _text(membership_id, limit=200)
        if normalized and normalized not in desired_roles:
            desired_roles[normalized] = "collaborator"

    membership_rows: dict[str, sqlite3.Row] = {}
    for membership_id in desired_roles:
        membership_rows[membership_id] = _membership_row(connection, identity, membership_id)

    for membership_id, row in existing_by_member.items():
        if membership_id not in desired_roles:
            connection.execute(
                "UPDATE object_grants SET status='revoked',lifecycle_state='archived',"
                "revoked_at=?,updated_at=?,version=version+1 WHERE id=?",
                (now, now, str(row["id"])),
            )

    for membership_id, role_key in desired_roles.items():
        member = membership_rows[membership_id]
        current = existing_by_member.get(membership_id)
        previous_role = ""
        if current is not None:
            try:
                previous_role = str(json.loads(str(current["capability_set"] or "{}")).get("roleKey") or "")
            except (TypeError, ValueError, json.JSONDecodeError):
                previous_role = ""
        status = "active" if membership_id in {creator_membership_id, identity.membership_id} else "pending"
        if current is not None and previous_role == role_key and str(current["status"]) in {"active", "pending"}:
            status = str(current["status"])
        capability_set = canonical_json({
            "roleKey": role_key,
            "read": True,
            "respond": membership_id != creator_membership_id or role_key == "creator_owner",
            "manage": role_key in {"creator", "creator_owner", "owner"},
            "invitedByMembershipId": identity.membership_id,
        })
        if current is None:
            grant_id = repository._record_id("grant", meeting_id, f"meeting:{membership_id}")  # noqa: SLF001
            connection.execute(
                """
                INSERT INTO object_grants (
                    id,scope_id,secured_resource_id,policy_version_id,
                    subject_principal_id,subject_membership_id,
                    capability_set_schema_version,capability_set,grant_generation,
                    status,grant_source_set_id,created_at,updated_at,revoked_at,
                    version,lifecycle_state,deleted_at
                ) VALUES (?,?,?,NULL,?,?,?, ?,1,?,NULL,?,?,NULL,1,'active',NULL)
                """,
                (
                    grant_id,
                    identity.scope_id,
                    meeting_id,
                    member["principal_id"],
                    membership_id,
                    MEETING_COLLABORATION_SCHEMA,
                    capability_set,
                    status,
                    now,
                    now,
                ),
            )
        else:
            connection.execute(
                "UPDATE object_grants SET subject_principal_id=?,capability_set=?,status=?,"
                "lifecycle_state='active',revoked_at=NULL,deleted_at=NULL,updated_at=?,version=version+1 "
                "WHERE id=?",
                (member["principal_id"], capability_set, status, now, str(current["id"])),
            )


def _sync_meeting_plan_link(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    meeting_id: str,
    client_id: str,
    planning_cycle_id: str | None,
    decision_action_id: str | None,
    now: str,
) -> None:
    source_set_id = repository._record_id("source_set", meeting_id, "meeting_plan")  # noqa: SLF001
    lineage_id = repository._record_id("lineage", meeting_id, "meeting_plan")  # noqa: SLF001
    connection.execute(
        "UPDATE derivation_lineage SET invalidated_at=? WHERE scope_id=? "
        "AND derivative_kind=? AND derivative_object_id=? AND invalidated_at IS NULL",
        (now, identity.scope_id, MEETING_PLAN_LINK_PURPOSE, meeting_id),
    )
    connection.execute(
        "UPDATE source_set_members SET lifecycle_state='deleted',removed_at=?,deleted_at=?,updated_at=?,version=version+1 "
        "WHERE scope_id=? AND source_set_id=? AND lifecycle_state='active'",
        (now, now, now, identity.scope_id, source_set_id),
    )
    if not planning_cycle_id:
        connection.execute(
            "UPDATE source_sets SET lifecycle_state='deleted',deleted_at=?,updated_at=?,version=version+1 "
            "WHERE scope_id=? AND id=? AND lifecycle_state!='deleted'",
            (now, now, identity.scope_id, source_set_id),
        )
        return
    cycle = _planning_row(connection, identity, planning_cycle_id)
    action = None
    if decision_action_id:
        action = _decision_action_row(connection, identity, decision_action_id)
        if str(action["planning_cycle_id"]) != planning_cycle_id:
            raise RepositoryError(422, "meeting_plan_action_mismatch", "计划行动不属于所选计划周期")
        if action["client_id"] and str(action["client_id"]) != client_id:
            raise RepositoryError(422, "meeting_plan_client_mismatch", "计划行动与会议项目不一致")
    members = [("planning_cycle", planning_cycle_id, int(cycle["version"] or 1))]
    if action is not None:
        members.append(("decision_action", decision_action_id, int(action["version"] or 1)))
    connection.execute(
        """
        INSERT INTO source_sets (
            id,scope_id,client_id,security_label_set_version,source_count,version,
            purpose_kind,publication_state,created_by_principal_id,created_at,expires_at,
            lifecycle_state,updated_at,deleted_at,authority_role,origin_instance_id
        ) VALUES (?,?,?,NULL,?,1,?,'published',?,?,NULL,'active',?,NULL,'cloud',?)
        ON CONFLICT(id) DO UPDATE SET client_id=excluded.client_id,
            source_count=excluded.source_count,version=source_sets.version+1,
            publication_state='published',lifecycle_state='active',updated_at=excluded.updated_at,
            deleted_at=NULL
        """,
        (
            source_set_id,
            identity.scope_id,
            client_id,
            len(members),
            MEETING_PLAN_LINK_PURPOSE,
            identity.principal_id,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    for ordinal, (kind, object_id, source_version) in enumerate(members):
        member_id = repository._record_id("source_member", meeting_id, f"meeting_plan:{kind}")  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO source_set_members (
                id,scope_id,source_set_id,source_object_id,source_version,policy_version,
                source_object_kind,ordinal,added_at,removed_at,version,lifecycle_state,
                created_at,updated_at,deleted_at,authority_role,origin_instance_id
            ) VALUES (?,?,?,?,?,NULL,?,?,?,NULL,1,'active',?,?,NULL,'cloud',?)
            ON CONFLICT(id) DO UPDATE SET source_object_id=excluded.source_object_id,
                source_version=excluded.source_version,ordinal=excluded.ordinal,added_at=excluded.added_at,
                removed_at=NULL,version=source_set_members.version+1,lifecycle_state='active',
                updated_at=excluded.updated_at,deleted_at=NULL
            """,
            (
                member_id,
                identity.scope_id,
                source_set_id,
                object_id,
                source_version,
                kind,
                ordinal,
                now,
                now,
                now,
                repository.cloud_instance_id,
            ),
        )
    connection.execute(
        """
        INSERT INTO derivation_lineage (
            id,scope_id,source_set_id,policy_version_id,grant_generation,
            derivative_kind,derivative_object_id,generator_version,generated_at,
            invalidated_at,source_version,authority_role,origin_instance_id
        ) VALUES (?,?,?,NULL,NULL,?,?,?, ?,NULL,1,'cloud',?)
        ON CONFLICT(id) DO UPDATE SET source_set_id=excluded.source_set_id,
            generator_version=excluded.generator_version,generated_at=excluded.generated_at,
            invalidated_at=NULL,source_version=derivation_lineage.source_version+1
        """,
        (
            lineage_id,
            identity.scope_id,
            source_set_id,
            MEETING_PLAN_LINK_PURPOSE,
            meeting_id,
            MEETING_COLLABORATION_SCHEMA,
            now,
            repository.cloud_instance_id,
        ),
    )


def _replace_calendar_projection(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    target_kind: str,
    target_id: str,
    starts_at: str | None,
    ends_at: str | None,
    timezone: str | None,
    source_version: int,
    display_state: str,
    now: str,
) -> dict[str, Any] | None:
    id_column = "task_id" if target_kind == "task" else "meeting_id"
    other_column = "meeting_id" if target_kind == "task" else "task_id"
    connection.execute(
        f"UPDATE calendar_entries SET invalidated_at=? WHERE scope_id=? AND "
        f"target_kind=? AND {id_column}=? AND invalidated_at IS NULL",  # noqa: S608 - fixed identifier
        (now, scope_id, target_kind, target_id),
    )
    if not starts_at:
        return None
    entry_id = "cal_" + sha256_text(
        f"{scope_id}|{target_kind}|{target_id}|{source_version}"
    )[:30]
    connection.execute(
        f"""
        INSERT INTO calendar_entries (
            id, scope_id, {id_column}, {other_column}, starts_at, version,
            target_kind, ends_at, timezone, display_state, source_version,
            generated_at, invalidated_at
        ) VALUES (?, ?, ?, NULL, ?, 1, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(id) DO UPDATE SET
            starts_at=excluded.starts_at,
            ends_at=excluded.ends_at,
            timezone=excluded.timezone,
            display_state=excluded.display_state,
            source_version=excluded.source_version,
            generated_at=excluded.generated_at,
            invalidated_at=NULL
        """,  # noqa: S608 - fixed identifier
        (
            entry_id,
            scope_id,
            target_id,
            starts_at,
            target_kind,
            ends_at,
            timezone,
            display_state,
            source_version,
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM calendar_entries WHERE id=?", (entry_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def derive_task_calendar_projection(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    task_id: str,
    generated_at: str | None = None,
) -> dict[str, Any] | None:
    """Called by the GC-04 formal task transaction after its authoritative write."""

    task = connection.execute(
        "SELECT * FROM tasks WHERE scope_id=? AND id=?", (scope_id, task_id)
    ).fetchone()
    if task is None:
        raise RepositoryError(404, "task_missing", "任务不存在")
    now = generated_at or utc_now()
    active = str(task["lifecycle_state"] or "") == "active"
    starts_at = (
        str(task["scheduled_start_at"] or task["due_date"] or "") or None
        if active
        else None
    )
    return _replace_calendar_projection(
        connection,
        scope_id=scope_id,
        target_kind="task",
        target_id=task_id,
        starts_at=starts_at,
        ends_at=str(task["scheduled_end_at"] or "") or None,
        timezone=None,
        source_version=int(task["version"] or 1),
        display_state="completed" if task["completed_at"] else "scheduled",
        now=now,
    )


def clients_pulse(
    repository: CloudRepository,
    identity: SessionIdentity,
) -> dict[str, Any]:
    """Visible client activity derived directly from the frozen 88 tables."""

    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=7)).isoformat().replace("+00:00", "Z")
    today = now.date().isoformat()
    generated_at = now.isoformat().replace("+00:00", "Z")
    summaries: list[dict[str, Any]] = []
    with repository._connection() as connection:  # noqa: SLF001
        projects = connection.execute(
            "SELECT * FROM clients WHERE scope_id=? "
            "AND lifecycle_state!='deleted' ORDER BY name,id",
            (identity.scope_id,),
        ).fetchall()
        for project in projects:
            project_id = str(project["id"])
            try:
                repository._require_project_access(  # noqa: SLF001
                    connection,
                    identity,
                    project_id=project_id,
                )
            except RepositoryError as exc:
                if exc.status_code == 404:
                    continue
                raise
            new_tasks = int(connection.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE scope_id=? "
                "AND client_id=? AND lifecycle_state!='deleted' AND created_at>=?",
                (identity.scope_id, project_id, week_start),
            ).fetchone()["count"])
            overdue = int(connection.execute(
                "SELECT COUNT(*) AS count FROM tasks WHERE scope_id=? "
                "AND client_id=? AND lifecycle_state='active' "
                "AND completed_at IS NULL AND due_date IS NOT NULL AND due_date<?",
                (identity.scope_id, project_id, today),
            ).fetchone()["count"])
            new_documents = int(connection.execute(
                "SELECT COUNT(*) AS count FROM knowledge_documents WHERE scope_id=? "
                "AND client_id=? AND lifecycle_state='active' AND created_at>=?",
                (identity.scope_id, project_id, week_start),
            ).fetchone()["count"])
            fact_join = (
                " FROM atomic_facts "
                "JOIN content_chunks ON content_chunks.id=atomic_facts.chunk_id "
                "AND content_chunks.scope_id=atomic_facts.scope_id "
                "JOIN document_versions ON document_versions.id=content_chunks.document_version_id "
                "AND document_versions.scope_id=content_chunks.scope_id "
                "JOIN knowledge_documents ON knowledge_documents.id=document_versions.document_id "
                "AND knowledge_documents.scope_id=document_versions.scope_id "
                "WHERE atomic_facts.scope_id=? AND knowledge_documents.client_id=? "
                "AND atomic_facts.lifecycle_state='active' "
                "AND content_chunks.lifecycle_state='active' "
                "AND knowledge_documents.lifecycle_state='active' "
            )
            new_evidence = int(connection.execute(
                "SELECT COUNT(DISTINCT atomic_facts.id) AS count" + fact_join
                + "AND atomic_facts.created_at>=?",
                (identity.scope_id, project_id, week_start),
            ).fetchone()["count"])
            current_blockers = int(connection.execute(
                "SELECT COUNT(DISTINCT atomic_facts.id) AS count" + fact_join
                + "AND atomic_facts.verification_state IN ('conflicted','disputed')",
                (identity.scope_id, project_id),
            ).fetchone()["count"])
            if overdue:
                top_signal = f"{overdue} 项任务已逾期"
            elif current_blockers:
                top_signal = f"{current_blockers} 条事实待澄清"
            elif new_documents:
                top_signal = f"本周新增 {new_documents} 份知识资料"
            elif new_tasks:
                top_signal = f"本周新增 {new_tasks} 项任务"
            elif new_evidence:
                top_signal = f"本周新增 {new_evidence} 条事实"
            else:
                top_signal = "本周无动态"
            summaries.append({
                "clientId": project_id,
                "clientName": str(project["name"] or "未命名项目"),
                "clientStage": str(project["lifecycle_state"] or "active"),
                "weeklyNewDocumentCount": new_documents,
                "weeklyNewTaskCount": new_tasks,
                "weeklyNewEvidenceCount": new_evidence,
                "currentBlockerCount": current_blockers,
                "overdueTodoCount": overdue,
                "hasActivity": any((new_documents, new_tasks, new_evidence, current_blockers, overdue)),
                "topSignal": top_signal,
            })
    summaries.sort(key=lambda item: (
        0 if item["hasActivity"] else 1,
        -item["overdueTodoCount"],
        -item["currentBlockerCount"],
        -(item["weeklyNewDocumentCount"] + item["weeklyNewTaskCount"] + item["weeklyNewEvidenceCount"]),
        item["clientName"],
    ))
    return {"summaries": summaries, "generatedAt": generated_at}


def list_meetings(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    client_id: str | None = None,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT * FROM meetings WHERE scope_id=? AND lifecycle_state!='deleted' "
            + ("AND client_id=? " if client_id else "")
            + "ORDER BY starts_at, id",
            (identity.scope_id, client_id) if client_id else (identity.scope_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=str(row["client_id"])
                )
            except RepositoryError:
                continue
            result.append(_meeting_payload(connection, row))
        return result


def create_meeting(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    meeting_id = _text(payload.get("meetingId") or payload.get("id"), limit=200) or new_id()
    client_id = _required_text(payload.get("clientId"), "meeting_client_required", "会议必须明确选择客户项目", limit=200)
    starts_at = _iso_datetime(payload.get("startsAt"), field="starts_at")
    ends_at = _iso_datetime(payload.get("endsAt"), field="ends_at")
    starts_instant = _datetime_instant(starts_at)
    ends_instant = _datetime_instant(ends_at)
    if starts_instant.tzinfo is None and ends_instant.tzinfo is not None:
        starts_instant = starts_instant.replace(tzinfo=ends_instant.tzinfo)
    elif ends_instant.tzinfo is None and starts_instant.tzinfo is not None:
        ends_instant = ends_instant.replace(tzinfo=starts_instant.tzinfo)
    if ends_instant <= starts_instant:
        raise RepositoryError(422, "meeting_period_invalid", "会议结束时间必须晚于开始时间")
    status = _text(payload.get("status"), limit=30) or "scheduled"
    if status not in MEETING_STATUSES:
        raise RepositoryError(422, "meeting_status_invalid", "会议状态无效")
    normalized = {
        "meetingId": meeting_id,
        "clientId": client_id,
        "eventLineId": _text(payload.get("eventLineId"), limit=200) or None,
        "title": _required_text(payload.get("title"), "meeting_title_required", "会议标题不能为空", limit=500),
        "agenda": _text(payload.get("agenda")),
        "startsAt": starts_at,
        "endsAt": ends_at,
        "timezone": _text(payload.get("timezone"), limit=80) or "Asia/Shanghai",
        "organizerMembershipId": _text(payload.get("organizerMembershipId"), limit=200)
        or identity.membership_id,
        "collaboratorMembershipIds": sorted({
            _text(item, limit=200)
            for item in (payload.get("collaboratorMembershipIds") or [])
            if _text(item, limit=200)
        }),
        "planningCycleId": _text(payload.get("planningCycleId"), limit=200) or None,
        "decisionActionId": _text(payload.get("decisionActionId"), limit=200) or None,
        "visibilityScope": _text(payload.get("visibilityScope"), limit=40)
        or "project_public",
        "status": status,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.meeting.created",
                idempotency_key=idempotency_key,
                aggregate_type="meeting",
                aggregate_id=meeting_id,
                expected_version=None,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            binding = validate_meeting_client_binding(
                connection,
                scope_id=identity.scope_id,
                client_id=client_id,
                event_line_id=normalized["eventLineId"],
            )
            repository._require_project_access(  # noqa: SLF001
                connection, identity, project_id=client_id, capability="project_write"
            )
            _membership_row(connection, identity, normalized["organizerMembershipId"])
            line = None
            if binding.event_line_id:
                line = require_active_event_line(
                    connection,
                    scope_id=identity.scope_id,
                    event_line_id=binding.event_line_id,
                )
            _insert_secured_resource(
                repository,
                connection,
                identity,
                resource_id=meeting_id,
                resource_kind="meeting",
                resource_type_key="meeting",
                now=now,
            )
            connection.execute(
                """
                INSERT INTO meetings (
                    id, scope_id, client_id, event_line_id, lifecycle_state,
                    title, agenda, starts_at, ends_at, timezone,
                    organizer_membership_id, visibility_scope, status, version,
                    created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL)
                """,
                (
                    meeting_id,
                    identity.scope_id,
                    client_id,
                    normalized["eventLineId"],
                    normalized["title"],
                    normalized["agenda"],
                    starts_at,
                    ends_at,
                    normalized["timezone"],
                    normalized["organizerMembershipId"],
                    normalized["visibilityScope"],
                    status,
                    now,
                    now,
                ),
            )
            _sync_meeting_collaborators(
                repository,
                connection,
                identity,
                meeting_id=meeting_id,
                organizer_membership_id=str(normalized["organizerMembershipId"]),
                collaborator_membership_ids=normalized["collaboratorMembershipIds"],
                now=now,
                preserve_creator=False,
            )
            _sync_meeting_plan_link(
                repository,
                connection,
                identity,
                meeting_id=meeting_id,
                client_id=client_id,
                planning_cycle_id=normalized["planningCycleId"],
                decision_action_id=normalized["decisionActionId"],
                now=now,
            )
            _replace_calendar_projection(
                connection,
                scope_id=identity.scope_id,
                target_kind="meeting",
                target_id=meeting_id,
                starts_at=starts_at,
                ends_at=ends_at,
                timezone=normalized["timezone"],
                source_version=1,
                display_state=status,
                now=now,
            )
            if line is not None:
                _upsert_event_activity(
                    repository,
                    connection,
                    identity,
                    event_line=line,
                    source_type="meeting",
                    source_id=meeting_id,
                    happened_at=starts_at,
                    title=normalized["title"],
                    summary=normalized["agenda"],
                    include_in_narrative=True,
                    now=now,
                )
            meeting = _meeting_row(connection, identity, meeting_id)
            result = {"meeting": _meeting_payload(connection, meeting)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.meeting.created",
                aggregate_type="meeting",
                aggregate_id=meeting_id,
                aggregate_version=1,
                payload_hash=payload_hash,
                result=result,
                target_resource_id=meeting_id,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def update_meeting(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    meeting_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    expected = _positive_int(payload.get("expectedVersion"))
    normalized = {
        "meetingId": meeting_id,
        "expectedVersion": expected,
        "clientId": _text(payload.get("clientId"), limit=200) if "clientId" in payload else None,
        "eventLineId": _text(payload.get("eventLineId"), limit=200) if "eventLineId" in payload else None,
        "title": _text(payload.get("title"), limit=500),
        "agenda": _text(payload.get("agenda")),
        "startsAt": _text(payload.get("startsAt"), limit=64),
        "endsAt": _text(payload.get("endsAt"), limit=64),
        "status": _text(payload.get("status"), limit=30),
        "organizerMembershipId": _text(payload.get("organizerMembershipId"), limit=200)
        if "organizerMembershipId" in payload else None,
        "collaboratorMembershipIds": sorted({
            _text(item, limit=200)
            for item in (payload.get("collaboratorMembershipIds") or [])
            if _text(item, limit=200)
        }) if "collaboratorMembershipIds" in payload else None,
        "planningCycleId": (_text(payload.get("planningCycleId"), limit=200) or None)
        if "planningCycleId" in payload else None,
        "decisionActionId": (_text(payload.get("decisionActionId"), limit=200) or None)
        if "decisionActionId" in payload else None,
        "collaborationTouched": "organizerMembershipId" in payload or "collaboratorMembershipIds" in payload,
        "planLinkTouched": "planningCycleId" in payload or "decisionActionId" in payload,
    }
    if normalized["status"] and normalized["status"] not in MEETING_STATUSES:
        raise RepositoryError(422, "meeting_status_invalid", "会议状态无效")
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type="gc06.meeting.updated",
                idempotency_key=idempotency_key,
                aggregate_type="meeting",
                aggregate_id=meeting_id,
                expected_version=expected,
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            row = _meeting_row(connection, identity, meeting_id)
            repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=str(row["client_id"]),
                capability="project_write",
            )
            if int(row["version"] or 1) != expected:
                raise RepositoryError(409, "meeting_version_conflict", "会议已被其他成员更新")
            starts_at = _iso_datetime(
                normalized["startsAt"] or str(row["starts_at"]),
                field="starts_at",
            )
            ends_at = _iso_datetime(
                normalized["endsAt"] or str(row["ends_at"]),
                field="ends_at",
            )
            starts_instant = _datetime_instant(starts_at)
            ends_instant = _datetime_instant(ends_at)
            if starts_instant.tzinfo is None and ends_instant.tzinfo is not None:
                starts_instant = starts_instant.replace(tzinfo=ends_instant.tzinfo)
            elif ends_instant.tzinfo is None and starts_instant.tzinfo is not None:
                ends_instant = ends_instant.replace(tzinfo=starts_instant.tzinfo)
            if ends_instant <= starts_instant:
                raise RepositoryError(422, "meeting_period_invalid", "会议结束时间必须晚于开始时间")
            client_id = normalized["clientId"] if "clientId" in payload else row["client_id"]
            repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=str(client_id),
                capability="project_write",
            )
            event_line_id = normalized["eventLineId"] if "eventLineId" in payload else row["event_line_id"]
            organizer_membership_id = (
                normalized["organizerMembershipId"]
                if "organizerMembershipId" in payload
                else row["organizer_membership_id"]
            ) or identity.membership_id
            _membership_row(connection, identity, str(organizer_membership_id))
            binding = validate_meeting_client_binding(
                connection,
                scope_id=identity.scope_id,
                client_id=client_id,
                event_line_id=event_line_id,
            )
            line = None
            if binding.event_line_id:
                line = require_active_event_line(
                    connection,
                    scope_id=identity.scope_id,
                    event_line_id=binding.event_line_id,
                )
            next_version = expected + 1
            status = normalized["status"] or str(row["status"])
            connection.execute(
                """
                UPDATE meetings SET client_id=?, event_line_id=?, title=?, agenda=?, starts_at=?,
                    ends_at=?, organizer_membership_id=?, status=?, version=?, updated_at=?
                WHERE id=? AND scope_id=? AND version=?
                """,
                (
                    binding.client_id,
                    event_line_id,
                    normalized["title"] or row["title"],
                    normalized["agenda"] if "agenda" in payload else row["agenda"],
                    starts_at,
                    ends_at,
                    organizer_membership_id,
                    status,
                    next_version,
                    now,
                    meeting_id,
                    identity.scope_id,
                    expected,
                ),
            )
            if normalized["collaborationTouched"]:
                collaborator_ids = normalized["collaboratorMembershipIds"]
                if collaborator_ids is None:
                    _, current_collaborators = _meeting_collaboration_payload(
                        connection,
                        scope_id=identity.scope_id,
                        meeting_id=meeting_id,
                    )
                    collaborator_ids = [
                        str(item["membershipId"])
                        for item in current_collaborators
                        if item["roleKey"] == "collaborator"
                    ]
                _sync_meeting_collaborators(
                    repository,
                    connection,
                    identity,
                    meeting_id=meeting_id,
                    organizer_membership_id=str(organizer_membership_id),
                    collaborator_membership_ids=collaborator_ids,
                    now=now,
                    preserve_creator=True,
                )
            if normalized["planLinkTouched"]:
                _sync_meeting_plan_link(
                    repository,
                    connection,
                    identity,
                    meeting_id=meeting_id,
                    client_id=str(binding.client_id),
                    planning_cycle_id=normalized["planningCycleId"],
                    decision_action_id=normalized["decisionActionId"],
                    now=now,
                )
            connection.execute(
                "UPDATE secured_resources SET version=version+1, updated_at=? WHERE id=?",
                (now, meeting_id),
            )
            _replace_calendar_projection(
                connection,
                scope_id=identity.scope_id,
                target_kind="meeting",
                target_id=meeting_id,
                starts_at=starts_at if status != "cancelled" else None,
                ends_at=ends_at,
                timezone=str(row["timezone"] or "Asia/Shanghai"),
                source_version=next_version,
                display_state=status,
                now=now,
            )
            if line is not None:
                _upsert_event_activity(
                    repository,
                    connection,
                    identity,
                    event_line=line,
                    source_type="meeting",
                    source_id=meeting_id,
                    happened_at=starts_at,
                    title=normalized["title"] or str(row["title"] or ""),
                    summary=normalized["agenda"] if "agenda" in payload else str(row["agenda"] or ""),
                    include_in_narrative=True,
                    now=now,
                )
            meeting = _meeting_row(connection, identity, meeting_id)
            result = {"meeting": _meeting_payload(connection, meeting)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type="gc06.meeting.updated",
                aggregate_type="meeting",
                aggregate_id=meeting_id,
                aggregate_version=next_version,
                payload_hash=payload_hash,
                result=result,
                target_resource_id=meeting_id,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def transition_meeting_collaboration(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    meeting_id: str,
    action: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if action not in {"accept", "reject"}:
        raise RepositoryError(404, "meeting_collaboration_action_missing", "会议协作操作不存在")
    normalized = {
        "meetingId": meeting_id,
        "action": action,
        "expectedGrantVersion": int(payload.get("expectedGrantVersion") or 0),
    }
    payload_hash = sha256_text(canonical_json(normalized))
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            meeting = _meeting_row(connection, identity, meeting_id)
            command_type = f"gc06.meeting.collaboration_{action}ed"
            command_id, operation_id, replay = _start_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="meeting",
                aggregate_id=meeting_id,
                expected_version=int(meeting["version"] or 1),
                payload_hash=payload_hash,
                now=now,
            )
            if replay is not None:
                connection.commit()
                return replay
            grant = connection.execute(
                "SELECT * FROM object_grants WHERE scope_id=? AND secured_resource_id=? "
                "AND subject_membership_id=? AND capability_set_schema_version=? "
                "AND lifecycle_state='active' AND status='pending' ORDER BY created_at DESC,id DESC LIMIT 1",
                (
                    identity.scope_id,
                    meeting_id,
                    identity.membership_id,
                    MEETING_COLLABORATION_SCHEMA,
                ),
            ).fetchone()
            if grant is None:
                raise RepositoryError(409, "meeting_collaboration_not_pending", "该会议邀请已处理，请刷新")
            expected_grant_version = normalized["expectedGrantVersion"]
            if expected_grant_version and int(grant["version"] or 1) != expected_grant_version:
                raise RepositoryError(409, "meeting_collaboration_version_conflict", "会议邀请已变化，请刷新")
            next_status = "active" if action == "accept" else "rejected"
            connection.execute(
                "UPDATE object_grants SET status=?,updated_at=?,version=version+1 WHERE id=?",
                (next_status, now, str(grant["id"])),
            )
            current = _meeting_row(connection, identity, meeting_id)
            result = {"meeting": _meeting_payload(connection, current)}
            settled = _settle_command(
                repository,
                connection,
                identity,
                command_id=command_id,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                aggregate_type="meeting",
                aggregate_id=meeting_id,
                aggregate_version=int(current["version"] or 1),
                payload_hash=payload_hash,
                result=result,
                target_resource_id=meeting_id,
                now=now,
            )
            connection.commit()
            return settled
        except Exception:
            connection.rollback()
            raise


def list_calendar_entries(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    starts_from: str | None = None,
    starts_to: str | None = None,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        clauses = ["scope_id=?", "invalidated_at IS NULL"]
        params: list[Any] = [identity.scope_id]
        if starts_from:
            clauses.append("starts_at>=?")
            params.append(starts_from)
        if starts_to:
            clauses.append("starts_at<=?")
            params.append(starts_to)
        rows = connection.execute(
            "SELECT * FROM calendar_entries WHERE " + " AND ".join(clauses)
            + " ORDER BY starts_at, id",
            params,
        ).fetchall()
        return [dict(row) for row in rows]
