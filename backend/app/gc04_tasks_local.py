"""Local projections for the cloud-authoritative GC-04 task domain.

This adapter performs no DDL and never invents projects, lists, members or
notification outcomes.  It only applies rows returned by the formal cloud
commands to the matching sandbox projection in the frozen local schema.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from strict_common.ids import utc_now

from .runtime import LocalRuntimeError, WorkspaceRuntime


_PROJECTION_TABLES = (
    "task_lists",
    "tasks",
    "task_views",
    "task_collaborators",
    "calendar_entries",
)
_RESOURCE_KINDS = {
    "task_lists": "task_list",
    "tasks": "task",
    "task_views": "task_view",
}


def _lease_after_24h() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=24)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class LocalGC04TaskProjection:
    """Apply and inspect only the task-related local projection tables."""

    def __init__(self, runtime: WorkspaceRuntime):
        self.runtime = runtime

    def _context(self) -> tuple[Any, str]:
        context = self.runtime._current_context(require_ready=True)  # noqa: SLF001
        return context, context.sandbox_id

    @staticmethod
    def _columns(connection: Any, table: str) -> set[str]:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @staticmethod
    def _source_version(row: Mapping[str, Any]) -> int:
        return max(1, int(row.get("version") or row.get("source_version") or 1))

    def _upsert_resource(
        self,
        connection: Any,
        *,
        scope_id: str,
        cloud_instance_id: str,
        resource_id: str,
        resource_kind: str,
        version: int,
        lifecycle_state: str,
        created_at: str,
        updated_at: str,
        deleted_at: str | None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO secured_resources (
                id,scope_id,resource_kind,lifecycle_state,version,
                resource_type_key,created_at,updated_at,deleted_at,
                authority_role,origin_instance_id
            ) VALUES (?,?,?,?,?,?,?,?,?,'cloud_projection',?)
            ON CONFLICT(id) DO UPDATE SET
                scope_id=excluded.scope_id,
                resource_kind=excluded.resource_kind,
                lifecycle_state=excluded.lifecycle_state,
                version=excluded.version,
                resource_type_key=excluded.resource_type_key,
                updated_at=excluded.updated_at,
                deleted_at=excluded.deleted_at,
                authority_role='cloud_projection',
                origin_instance_id=excluded.origin_instance_id
            """,
            (
                resource_id,
                scope_id,
                resource_kind,
                lifecycle_state,
                version,
                resource_kind,
                created_at,
                updated_at,
                deleted_at,
                cloud_instance_id,
            ),
        )

    def _upsert_row(
        self,
        connection: Any,
        *,
        table: str,
        row: Mapping[str, Any],
        scope_id: str,
        sandbox_id: str,
        now: str,
    ) -> None:
        columns = self._columns(connection, table)
        data = {key: value for key, value in row.items() if key in columns}
        object_id = str(data.get("id") or "").strip()
        if not object_id:
            raise LocalRuntimeError(
                409, "task_projection_id_missing", f"{table} 投影缺少稳定ID"
            )
        data["scope_id"] = scope_id
        if "sandbox_id" in columns:
            data["sandbox_id"] = sandbox_id
        if "source_version" in columns:
            data["source_version"] = self._source_version(row)
        if "projection_state" in columns:
            data["projection_state"] = "current"
        if "projected_at" in columns:
            data["projected_at"] = now
        if "stale_at" in columns:
            data["stale_at"] = None
        if "lease_expires_at" in columns:
            data["lease_expires_at"] = _lease_after_24h()
        names = list(data)
        assignments = [
            f"{name}=excluded.{name}" for name in names if name != "id"
        ]
        placeholders = ",".join("?" for _ in names)
        connection.execute(
            f"INSERT INTO {table} ({','.join(names)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {','.join(assignments)}",
            tuple(data[name] for name in names),
        )

    def apply(
        self,
        projection: Mapping[str, Any] | None,
        *,
        replace_snapshot: bool = False,
    ) -> dict[str, Any]:
        incoming = projection if isinstance(projection, Mapping) else {}
        context, sandbox_id = self._context()
        now = utc_now()
        counts: dict[str, int] = {table: 0 for table in _PROJECTION_TABLES}
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                normalized_rows: dict[str, list[Mapping[str, Any]]] = {}
                for table in _PROJECTION_TABLES:
                    raw = incoming.get(table) or []
                    normalized_rows[table] = [
                        item for item in raw if isinstance(item, Mapping)
                    ] if isinstance(raw, list) else []
                for table in ("task_lists", "tasks", "task_views"):
                    for row in normalized_rows[table]:
                        resource_kind = _RESOURCE_KINDS[table]
                        self._upsert_resource(
                            connection,
                            scope_id=scope_id,
                            cloud_instance_id=context.cloud_instance_id,
                            resource_id=str(row.get("id") or ""),
                            resource_kind=resource_kind,
                            version=self._source_version(row),
                            lifecycle_state=str(row.get("lifecycle_state") or "active"),
                            created_at=str(row.get("created_at") or now),
                            updated_at=str(row.get("updated_at") or now),
                            deleted_at=row.get("deleted_at"),
                        )
                        self._upsert_row(
                            connection,
                            table=table,
                            row=row,
                            scope_id=scope_id,
                            sandbox_id=sandbox_id,
                            now=now,
                        )
                        counts[table] += 1
                for table in ("task_collaborators", "calendar_entries"):
                    for row in normalized_rows[table]:
                        self._upsert_row(
                            connection,
                            table=table,
                            row=row,
                            scope_id=scope_id,
                            sandbox_id=sandbox_id,
                            now=now,
                        )
                        counts[table] += 1
                if replace_snapshot:
                    self._mark_absent_stale(
                        connection,
                        rows=normalized_rows,
                        scope_id=scope_id,
                        sandbox_id=sandbox_id,
                        now=now,
                    )
                connection.commit()
            except Exception as exc:
                connection.rollback()
                if isinstance(exc, LocalRuntimeError):
                    raise
                raise LocalRuntimeError(
                    409,
                    "task_projection_apply_failed",
                    "任务云投影缺少本机稳定身份、项目或事件线依赖，未写入假投影",
                ) from exc
        return {
            "state": "current",
            "scopeId": scope_id,
            "sandboxId": sandbox_id,
            "counts": counts,
            "projectedAt": now,
        }

    @staticmethod
    def _ids(rows: Iterable[Mapping[str, Any]]) -> list[str]:
        return sorted(
            {
                str(row.get("id") or "").strip()
                for row in rows
                if str(row.get("id") or "").strip()
            }
        )

    def _mark_absent_stale(
        self,
        connection: Any,
        *,
        rows: Mapping[str, list[Mapping[str, Any]]],
        scope_id: str,
        sandbox_id: str,
        now: str,
    ) -> None:
        for table in ("task_lists", "tasks", "task_views", "task_collaborators"):
            ids = self._ids(rows[table])
            params: list[Any] = [now, scope_id, sandbox_id]
            exclusion = ""
            if ids:
                exclusion = f" AND id NOT IN ({','.join('?' for _ in ids)})"
                params.extend(ids)
            connection.execute(
                f"UPDATE {table} SET projection_state='stale',stale_at=? "
                "WHERE scope_id=? AND sandbox_id=? AND projection_state='current'"
                + exclusion,
                tuple(params),
            )
        calendar_ids = self._ids(rows["calendar_entries"])
        params = [now, scope_id]
        exclusion = ""
        if calendar_ids:
            exclusion = f" AND id NOT IN ({','.join('?' for _ in calendar_ids)})"
            params.extend(calendar_ids)
        connection.execute(
            "UPDATE calendar_entries SET invalidated_at=? WHERE scope_id=? "
            "AND target_kind='task' AND invalidated_at IS NULL" + exclusion,
            tuple(params),
        )

    def task_version(self, task_id: str) -> int:
        context, sandbox_id = self._context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            row = connection.execute(
                "SELECT version FROM tasks WHERE id=? AND scope_id=? AND sandbox_id=? "
                "AND lifecycle_state!='deleted' AND projection_state='current'",
                (task_id, scope_id, sandbox_id),
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(
                409, "task_projection_missing", "本机没有该任务的当前版本，请先刷新"
            )
        return int(row["version"] or 1)

    def collaborator_version(self, task_id: str) -> int:
        context, sandbox_id = self._context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            row = connection.execute(
                "SELECT version FROM task_collaborators WHERE scope_id=? AND task_id=? "
                "AND subject_membership_id=? AND inbox_status='pending' "
                "AND lifecycle_state='active' AND sandbox_id=? "
                "AND projection_state='current' ORDER BY updated_at DESC,id LIMIT 1",
                (scope_id, task_id, context.membership_id, sandbox_id),
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(
                409, "task_inbox_projection_missing", "协作邀请已处理或尚未同步"
            )
        return int(row["version"] or 1)

    def task_binding(self, task_id: str) -> tuple[str | None, str | None]:
        """Return the last confirmed cloud client/event-line binding."""
        _, sandbox_id = self._context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            row = connection.execute(
                "SELECT client_id,event_line_id FROM tasks "
                "WHERE id=? AND scope_id=? AND sandbox_id=? "
                "AND lifecycle_state!='deleted' AND projection_state='current'",
                (task_id, scope_id, sandbox_id),
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(
                409, "task_projection_missing", "本机没有该任务的当前版本，请先刷新"
            )
        return (
            str(row["client_id"]).strip() if row["client_id"] else None,
            str(row["event_line_id"]).strip() if row["event_line_id"] else None,
        )

    def task_context_hint(self, task_id: str) -> dict[str, Any]:
        """Read the current task subject from the strict local projection.

        The cloud remains authoritative.  This is only a bounded read of the
        already-confirmed task/client/event-line projection so context ranking
        does not issue one additional cloud task-board request per visible card.
        """
        _, sandbox_id = self._context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            row = connection.execute(
                """
                SELECT task.id,task.title,task.description,task.client_id,
                       task.event_line_id,client.name AS client_name,
                       line.name AS event_line_name
                FROM tasks AS task
                LEFT JOIN clients AS client
                  ON client.scope_id=task.scope_id AND client.id=task.client_id
                 AND client.lifecycle_state='active'
                LEFT JOIN event_lines AS line
                  ON line.scope_id=task.scope_id AND line.id=task.event_line_id
                 AND line.record_kind='line' AND line.lifecycle_state='active'
                WHERE task.id=? AND task.scope_id=? AND task.sandbox_id=?
                  AND task.lifecycle_state!='deleted'
                  AND task.projection_state='current'
                """,
                (task_id, scope_id, sandbox_id),
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(
                409, "task_projection_missing", "本机没有该任务的当前投影，请先刷新"
            )
        return {
            "taskId": str(row["id"]),
            "title": str(row["title"] or ""),
            "description": str(row["description"] or ""),
            "clientId": str(row["client_id"] or "") or None,
            "clientName": str(row["client_name"] or "") or None,
            "eventLineId": str(row["event_line_id"] or "") or None,
            "eventLineName": str(row["event_line_name"] or "") or None,
        }

    def list_version(self, list_id: str) -> int:
        context, sandbox_id = self._context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            row = connection.execute(
                "SELECT version FROM task_lists WHERE id=? AND scope_id=? AND sandbox_id=? "
                "AND lifecycle_state!='deleted' AND projection_state='current'",
                (list_id, scope_id, sandbox_id),
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(
                409, "task_list_projection_missing", "本机没有该清单的当前版本，请先刷新"
            )
        return int(row["version"] or 1)


__all__ = ["LocalGC04TaskProjection"]
