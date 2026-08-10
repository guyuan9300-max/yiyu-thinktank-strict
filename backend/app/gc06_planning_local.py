"""Local, disposable projections for the cloud-authoritative GC-03/GC-06 lane."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from strict_common.ids import utc_now

from .runtime import LocalRuntimeError, WorkspaceRuntime


def _lease_after_24h() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=24)
    ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class LocalGC06PlanningProjection:
    """Project verified cloud rows into the matching local 88-table objects."""

    WEEKLY_OVERVIEW_MEDIA_TYPE = "application/vnd.yiyu.gc06-weekly-overview+json"

    def __init__(self, runtime: WorkspaceRuntime):
        self.runtime = runtime

    def _context(self) -> tuple[Any, str]:
        context = self.runtime._current_context(require_ready=True)  # noqa: SLF001
        return context, context.sandbox_id

    def _weekly_overview_identity(
        self,
        *,
        membership_id: str,
        week_label: str,
        perspective: str,
        department_id: str | None,
    ) -> tuple[str, str]:
        fingerprint = hashlib.sha256(
            "\x1f".join(
                (
                    membership_id,
                    week_label,
                    perspective,
                    department_id or "",
                )
            ).encode("utf-8")
        ).hexdigest()[:32]
        return (
            f"gc06-weekly-overview:{fingerprint}",
            f"managed/gc06/weekly-overviews/{fingerprint}.json",
        )

    def load_weekly_overview(
        self,
        *,
        membership_id: str,
        week_label: str,
        perspective: str,
        department_id: str | None,
    ) -> dict[str, Any] | None:
        _context, sandbox_id = self._context()
        object_id, storage_key = self._weekly_overview_identity(
            membership_id=membership_id,
            week_label=week_label,
            perspective=perspective,
            department_id=department_id,
        )
        row = self.runtime.local_storage_object_get(
            sandbox_id=sandbox_id,
            object_id=object_id,
            storage_key=storage_key,
        )
        if row is None or str(row.get("lifecycle_state") or "") != "active":
            return None
        data_root = Path(self.runtime.database_path).resolve().parent
        path = (data_root / storage_key).resolve()
        if data_root not in path.parents:
            raise LocalRuntimeError(409, "gc06_weekly_overview_path_invalid", "周复盘草稿路径无效")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def save_weekly_overview(
        self,
        *,
        membership_id: str,
        week_label: str,
        perspective: str,
        department_id: str | None,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        _context, sandbox_id = self._context()
        object_id, storage_key = self._weekly_overview_identity(
            membership_id=membership_id,
            week_label=week_label,
            perspective=perspective,
            department_id=department_id,
        )
        serialized = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        content_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        data_root = Path(self.runtime.database_path).resolve().parent
        path = (data_root / storage_key).resolve()
        if data_root not in path.parents:
            raise LocalRuntimeError(409, "gc06_weekly_overview_path_invalid", "周复盘草稿路径无效")
        with self.runtime.local_storage_object_lock(
            sandbox_id=sandbox_id,
            object_id=object_id,
        ):
            current = self.runtime.local_storage_object_get(
                sandbox_id=sandbox_id,
                object_id=object_id,
            )
            expected_version = int(current.get("version") or 0) if current else 0
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".tmp-{os.getpid()}")
            temporary.write_text(serialized, encoding="utf-8")
            os.replace(temporary, path)
            return self.runtime.local_storage_object_put(
                sandbox_id=sandbox_id,
                object_id=object_id,
                storage_key=storage_key,
                content_hash=content_hash,
                media_type=self.WEEKLY_OVERVIEW_MEDIA_TYPE,
                byte_size=len(serialized.encode("utf-8")),
                expected_version=expected_version,
            )

    @staticmethod
    def _columns(connection: Any, table: str) -> set[str]:
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }

    @staticmethod
    def _upsert(connection: Any, table: str, data: Mapping[str, Any]) -> None:
        columns = LocalGC06PlanningProjection._columns(connection, table)
        filtered = {key: value for key, value in data.items() if key in columns}
        if not str(filtered.get("id") or "").strip():
            raise LocalRuntimeError(
                409,
                "gc06_projection_id_missing",
                f"{table} 投影缺少稳定 ID",
            )
        names = list(filtered)
        placeholders = ",".join("?" for _ in names)
        assignments = ",".join(
            f"{name}=excluded.{name}" for name in names if name != "id"
        )
        connection.execute(
            f"INSERT INTO {table} ({','.join(names)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {assignments}",
            tuple(filtered[name] for name in names),
        )

    @staticmethod
    def _projected(
        row: Mapping[str, Any],
        *,
        scope_id: str,
        sandbox_id: str,
        now: str,
    ) -> dict[str, Any]:
        version = max(1, int(row.get("version") or row.get("source_version") or 1))
        return {
            "scope_id": scope_id,
            "sandbox_id": sandbox_id,
            "source_version": version,
            "projection_state": "current",
            "projected_at": now,
            "stale_at": None,
            "lease_expires_at": _lease_after_24h(),
        }

    @staticmethod
    def _ensure_resource(
        connection: Any,
        *,
        scope_id: str,
        cloud_instance_id: str,
        resource_id: str,
        resource_kind: str,
        resource_type_key: str,
        lifecycle_state: str,
        version: int,
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
                resource_type_key,
                created_at,
                updated_at,
                deleted_at,
                cloud_instance_id,
            ),
        )

    def _transaction(self, apply: Any) -> dict[str, Any]:
        context, sandbox_id = self._context()
        now = utc_now()
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            connection.execute("BEGIN IMMEDIATE")
            try:
                count = int(
                    apply(
                        connection,
                        context,
                        scope_id,
                        sandbox_id,
                        now,
                    )
                    or 0
                )
                connection.commit()
            except Exception as exc:
                connection.rollback()
                if isinstance(exc, LocalRuntimeError):
                    raise
                raise LocalRuntimeError(
                    409,
                    "gc06_projection_apply_failed",
                    "计划与事件线云投影依赖不完整，未写入假数据",
                ) from exc
        return {
            "state": "current",
            "scopeId": scope_id,
            "sandboxId": sandbox_id,
            "count": count,
            "projectedAt": now,
        }

    def apply_event_lines(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        def apply(connection: Any, context: Any, scope_id: str, sandbox_id: str, now: str) -> int:
            count = 0
            for row in rows:
                resource_id = str(row.get("id") or "")
                lifecycle = str(row.get("lifecycleState") or "active")
                version = max(1, int(row.get("version") or 1))
                created_at = str(row.get("createdAt") or now)
                updated_at = str(row.get("updatedAt") or now)
                deleted_at = row.get("deletedAt")
                self._ensure_resource(
                    connection,
                    scope_id=scope_id,
                    cloud_instance_id=context.cloud_instance_id,
                    resource_id=resource_id,
                    resource_kind="event_line",
                    resource_type_key=str(row.get("kind") or "project_line"),
                    lifecycle_state=lifecycle,
                    version=version,
                    created_at=created_at,
                    updated_at=updated_at,
                    deleted_at=deleted_at,
                )
                self._upsert(
                    connection,
                    "event_lines",
                    {
                        "id": resource_id,
                        "client_id": row.get("clientId"),
                        "lifecycle_state": lifecycle,
                        "version": version,
                        "record_kind": "line",
                        "parent_event_line_id": None,
                        "created_by_membership_id": row.get("createdByMembershipId"),
                        "name": row.get("name"),
                        "goal": row.get("goal"),
                        "background": row.get("background"),
                        "visibility_scope": row.get("visibilityScope"),
                        "source_type": row.get("kind"),
                        "created_at": created_at,
                        "updated_at": updated_at,
                        "deleted_at": deleted_at,
                        **self._projected(
                            row,
                            scope_id=scope_id,
                            sandbox_id=sandbox_id,
                            now=now,
                        ),
                    },
                )
                count += 1
            return count

        return self._transaction(apply)

    def apply_event_activities(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> dict[str, Any]:
        def apply(
            connection: Any,
            context: Any,
            scope_id: str,
            sandbox_id: str,
            now: str,
        ) -> int:
            for row in rows:
                activity_id = str(row.get("id") or "")
                version = max(1, int(row.get("version") or 1))
                self._ensure_resource(
                    connection,
                    scope_id=scope_id,
                    cloud_instance_id=context.cloud_instance_id,
                    resource_id=activity_id,
                    resource_kind="event_line_activity",
                    resource_type_key=str(row.get("sourceType") or "manual_note"),
                    lifecycle_state="active",
                    version=version,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
                self._upsert(
                    connection,
                    "event_lines",
                    {
                        "id": activity_id,
                        "client_id": row.get("clientId"),
                        "lifecycle_state": "active",
                        "version": version,
                        "record_kind": "activity",
                        "parent_event_line_id": row.get("eventLineId"),
                        "created_by_membership_id": None,
                        "source_type": row.get("sourceType") or "manual_note",
                        "source_id": row.get("sourceId"),
                        "happened_at": row.get("happenedAt") or now,
                        "title": row.get("title"),
                        "summary": row.get("summary"),
                        "association_state": row.get("associationState") or "confirmed",
                        "include_in_narrative": int(
                            bool(row.get("includeInNarrative", True))
                        ),
                        "created_at": now,
                        "updated_at": now,
                        "deleted_at": None,
                        **self._projected(
                            row,
                            scope_id=scope_id,
                            sandbox_id=sandbox_id,
                            now=now,
                        ),
                    },
                )
            return len(rows)

        return self._transaction(apply)

    def list_planning_cycles(self) -> list[dict[str, Any]]:
        """Return the current-sandbox last-confirmed strict projections."""
        _, sandbox_id = self._context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            rows = connection.execute(
                """
                SELECT * FROM planning_cycles
                WHERE scope_id=? AND sandbox_id=?
                  AND projection_state='current'
                  AND lifecycle_state!='deleted'
                ORDER BY period_start DESC, updated_at DESC, id
                """,
                (scope_id, sandbox_id),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "recordKind": str(row["record_kind"]),
                "clientId": row["client_id"],
                "eventLineId": row["event_line_id"],
                "departmentId": row["department_id"],
                "ownerMembershipId": row["owner_membership_id"],
                "period": row["period"],
                "periodKind": row["period_kind"],
                "periodStart": str(row["period_start"]),
                "periodEnd": str(row["period_end"]),
                "title": str(row["title"] or ""),
                "summary": str(row["summary"] or ""),
                "status": str(row["status"]),
                "version": int(row["version"] or row["source_version"] or 1),
                "lifecycleState": str(row["lifecycle_state"]),
            }
            for row in rows
        ]

    def list_decision_actions(self) -> list[dict[str, Any]]:
        """Return plan actions from the same current-sandbox projection lease."""
        _, sandbox_id = self._context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            rows = connection.execute(
                """
                SELECT * FROM decision_actions
                WHERE scope_id=? AND sandbox_id=?
                  AND projection_state='current'
                  AND lifecycle_state!='deleted'
                ORDER BY updated_at DESC, id
                """,
                (scope_id, sandbox_id),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "recordKind": str(row["record_kind"]),
                "planningCycleId": str(row["planning_cycle_id"] or ""),
                "clientId": row["client_id"],
                "taskId": row["task_id"],
                "decisionState": str(row["decision_state"]),
                "title": str(row["title"] or ""),
                "statement": str(row["statement"] or ""),
                "expectedOutput": str(row["expected_output"] or ""),
                "ownerMembershipId": row["owner_membership_id"],
                "version": int(row["version"] or row["source_version"] or 1),
            }
            for row in rows
        ]

    def list_meetings(self, *, client_id: str | None = None) -> list[dict[str, Any]]:
        """Return last-confirmed formal meetings; local rows are projections only."""
        _, sandbox_id = self._context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            scope_id = self.runtime._local_object_scope_id(  # noqa: SLF001
                connection, sandbox_id
            )
            sql = """
                SELECT * FROM meetings
                WHERE scope_id=? AND sandbox_id=?
                  AND projection_state='current'
                  AND lifecycle_state!='deleted'
            """
            parameters: list[Any] = [scope_id, sandbox_id]
            if client_id:
                sql += " AND client_id=?"
                parameters.append(client_id)
            sql += " ORDER BY starts_at DESC, updated_at DESC, id"
            rows = connection.execute(sql, parameters).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                grants = connection.execute(
                    "SELECT object_grants.*,principals.display_name FROM object_grants "
                    "JOIN organization_memberships ON organization_memberships.id=object_grants.subject_membership_id "
                    "AND organization_memberships.scope_id=object_grants.scope_id "
                    "JOIN principals ON principals.id=organization_memberships.principal_id "
                    "WHERE object_grants.scope_id=? AND object_grants.secured_resource_id=? "
                    "AND object_grants.capability_set_schema_version='yiyu.meeting-collaboration.v1' "
                    "AND object_grants.lifecycle_state='active' AND object_grants.status!='revoked' "
                    "ORDER BY object_grants.created_at,object_grants.id",
                    (scope_id, row["id"]),
                ).fetchall()
                collaborators: list[dict[str, Any]] = []
                created_by: str | None = None
                for grant in grants:
                    try:
                        capabilities = json.loads(str(grant["capability_set"] or "{}"))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        capabilities = {}
                    role_key = str(capabilities.get("roleKey") or "collaborator")
                    if role_key in {"creator", "creator_owner"}:
                        created_by = str(grant["subject_membership_id"] or "")
                    collaborators.append({
                        "grantId": str(grant["id"]),
                        "membershipId": str(grant["subject_membership_id"] or ""),
                        "displayName": str(grant["display_name"] or "组织成员"),
                        "roleKey": "owner" if role_key in {"owner", "creator_owner"} else role_key,
                        "inboxStatus": "accepted" if str(grant["status"]) == "active" else str(grant["status"]),
                        "version": int(grant["version"] or 1),
                    })
                lineage = connection.execute(
                    "SELECT * FROM derivation_lineage WHERE scope_id=? AND derivative_kind='meeting_plan_link' "
                    "AND derivative_object_id=? AND invalidated_at IS NULL ORDER BY generated_at DESC,id DESC LIMIT 1",
                    (scope_id, row["id"]),
                ).fetchone()
                plan_link = None
                if lineage is not None:
                    members = connection.execute(
                        "SELECT * FROM source_set_members WHERE scope_id=? AND source_set_id=? "
                        "AND lifecycle_state='active' AND removed_at IS NULL",
                        (scope_id, lineage["source_set_id"]),
                    ).fetchall()
                    by_kind = {str(item["source_object_kind"]): str(item["source_object_id"]) for item in members}
                    plan_link = {
                        "sourceSetId": str(lineage["source_set_id"]),
                        "planningCycleId": by_kind.get("planning_cycle"),
                        "decisionActionId": by_kind.get("decision_action"),
                    }
                result.append({
                "id": str(row["id"]),
                "clientId": str(row["client_id"] or ""),
                "eventLineId": row["event_line_id"],
                "title": str(row["title"] or ""),
                "agenda": str(row["agenda"] or ""),
                "startsAt": row["starts_at"],
                "endsAt": row["ends_at"],
                "timezone": str(row["timezone"] or "Asia/Shanghai"),
                "organizerMembershipId": row["organizer_membership_id"],
                "createdByMembershipId": created_by,
                "collaborators": collaborators,
                "planLink": plan_link,
                "visibilityScope": row["visibility_scope"],
                "status": str(row["status"] or "scheduled"),
                "version": int(row["version"] or row["source_version"] or 1),
                "lifecycleState": str(row["lifecycle_state"]),
                "createdAt": str(row["created_at"] or ""),
                "updatedAt": str(row["updated_at"] or ""),
                })
        return result

    def apply_planning_cycles(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        def apply(connection: Any, context: Any, scope_id: str, sandbox_id: str, now: str) -> int:
            count = 0
            pending = list(rows)
            while pending:
                progressed = False
                for row in list(pending):
                    parent_id = row.get("parentPlanId")
                    if parent_id and not connection.execute(
                        "SELECT 1 FROM planning_cycles WHERE id=? AND scope_id=?",
                        (parent_id, scope_id),
                    ).fetchone():
                        continue
                    resource_id = str(row.get("id") or "")
                    lifecycle = str(row.get("lifecycleState") or "active")
                    version = max(1, int(row.get("version") or 1))
                    created_at = str(row.get("createdAt") or now)
                    updated_at = str(row.get("updatedAt") or now)
                    deleted_at = row.get("deletedAt")
                    self._ensure_resource(
                        connection,
                        scope_id=scope_id,
                        cloud_instance_id=context.cloud_instance_id,
                        resource_id=resource_id,
                        resource_kind="planning_cycle",
                        resource_type_key=str(row.get("recordKind") or "organization_plan"),
                        lifecycle_state=lifecycle,
                        version=version,
                        created_at=created_at,
                        updated_at=updated_at,
                        deleted_at=deleted_at,
                    )
                    self._upsert(
                        connection,
                        "planning_cycles",
                        {
                            "id": resource_id,
                            "event_line_id": row.get("eventLineId"),
                            "period": row.get("period"),
                            "plan_version": row.get("planVersion") or 1,
                            "status": row.get("status") or "draft",
                            "record_kind": row.get("recordKind") or "organization_plan",
                            "client_id": row.get("clientId"),
                            "parent_plan_id": parent_id,
                            "department_id": row.get("departmentId"),
                            "owner_membership_id": row.get("ownerMembershipId"),
                            "period_kind": row.get("periodKind"),
                            "period_start": row.get("periodStart"),
                            "period_end": row.get("periodEnd"),
                            "timezone": row.get("timezone"),
                            "title": row.get("title"),
                            "summary": row.get("summary"),
                            "published_at": row.get("publishedAt"),
                            "archived_at": row.get("archivedAt"),
                            "version": version,
                            "lifecycle_state": lifecycle,
                            "created_at": created_at,
                            "updated_at": updated_at,
                            "deleted_at": deleted_at,
                            **self._projected(
                                row,
                                scope_id=scope_id,
                                sandbox_id=sandbox_id,
                                now=now,
                            ),
                        },
                    )
                    pending.remove(row)
                    progressed = True
                    count += 1
                if not progressed:
                    raise LocalRuntimeError(
                        409,
                        "gc06_parent_plan_projection_missing",
                        "部门计划缺少组织计划投影",
                    )
            return count

        return self._transaction(apply)

    def apply_weekly_reviews(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        def apply(connection: Any, _: Any, scope_id: str, sandbox_id: str, now: str) -> int:
            count = 0
            for row in rows:
                review_id = str(row.get("id") or "")
                shared = self._projected(
                    row,
                    scope_id=scope_id,
                    sandbox_id=sandbox_id,
                    now=now,
                )
                self._upsert(
                    connection,
                    "weekly_reviews",
                    {
                        "id": review_id,
                        "planning_cycle_id": row.get("planningCycleId"),
                        "current_submitted_version_id": None,
                        "membership_id": row.get("membershipId"),
                        "current_draft_version_id": None,
                        "status": row.get("status") or "draft",
                        "version": row.get("version") or 1,
                        "lifecycle_state": row.get("lifecycleState") or "active",
                        "created_at": row.get("createdAt") or now,
                        "updated_at": row.get("updatedAt") or now,
                        "deleted_at": row.get("deletedAt"),
                        **shared,
                    },
                )
                for version in row.get("versions") or []:
                    if not isinstance(version, Mapping):
                        continue
                    version_number = max(1, int(version.get("version") or 1))
                    self._upsert(
                        connection,
                        "weekly_review_versions",
                        {
                            "id": version.get("id"),
                            "review_id": review_id,
                            "source_set_id": None,
                            "version": version_number,
                            "business_state": version.get("businessState") or "draft",
                            "based_on_version_id": version.get("basedOnVersionId"),
                            "effective_at": version.get("effectiveAt"),
                            "source_command_id": None,
                            "record_kind": version.get("recordKind") or "version",
                            "content_object_manifest_id": None,
                            "content_hash": version.get("contentHash"),
                            "review_note": version.get("reviewNote"),
                            "submitted_at": version.get("submittedAt"),
                            "origin_instance_id": None,
                            "created_at": version.get("createdAt") or now,
                            "integrity_hash": version.get("contentHash"),
                            **self._projected(
                                {"version": version_number},
                                scope_id=scope_id,
                                sandbox_id=sandbox_id,
                                now=now,
                            ),
                        },
                    )
                connection.execute(
                    "UPDATE weekly_reviews SET current_submitted_version_id=?, "
                    "current_draft_version_id=? WHERE id=? AND scope_id=?",
                    (
                        row.get("currentSubmittedVersionId"),
                        row.get("currentDraftVersionId"),
                        review_id,
                        scope_id,
                    ),
                )
                count += 1
            return count

        return self._transaction(apply)

    def apply_decision_actions(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        def apply(connection: Any, _: Any, scope_id: str, sandbox_id: str, now: str) -> int:
            for row in rows:
                self._upsert(
                    connection,
                    "decision_actions",
                    {
                        "id": row.get("id"),
                        "source_set_id": None,
                        "task_id": row.get("taskId"),
                        "decision_state": row.get("decisionState"),
                        "version": row.get("version") or 1,
                        "record_kind": row.get("recordKind"),
                        "planning_cycle_id": row.get("planningCycleId"),
                        "client_id": row.get("clientId"),
                        "title": row.get("title"),
                        "statement": row.get("statement"),
                        "expected_output": row.get("expectedOutput"),
                        "owner_membership_id": row.get("ownerMembershipId"),
                        "confirmed_at": row.get("confirmedAt"),
                        "lifecycle_state": row.get("lifecycleState") or "active",
                        "created_at": row.get("createdAt") or now,
                        "updated_at": row.get("updatedAt") or now,
                        "deleted_at": row.get("deletedAt"),
                        **self._projected(
                            row,
                            scope_id=scope_id,
                            sandbox_id=sandbox_id,
                            now=now,
                        ),
                    },
                )
            return len(rows)

        return self._transaction(apply)

    def apply_meetings(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        def apply(connection: Any, context: Any, scope_id: str, sandbox_id: str, now: str) -> int:
            for row in rows:
                meeting_id = str(row.get("id") or "")
                lifecycle = str(row.get("lifecycleState") or "active")
                version = max(1, int(row.get("version") or 1))
                created_at = str(row.get("createdAt") or now)
                updated_at = str(row.get("updatedAt") or now)
                self._ensure_resource(
                    connection,
                    scope_id=scope_id,
                    cloud_instance_id=context.cloud_instance_id,
                    resource_id=meeting_id,
                    resource_kind="meeting",
                    resource_type_key="meeting",
                    lifecycle_state=lifecycle,
                    version=version,
                    created_at=created_at,
                    updated_at=updated_at,
                    deleted_at=row.get("deletedAt"),
                )
                self._upsert(
                    connection,
                    "meetings",
                    {
                        "id": meeting_id,
                        "client_id": row.get("clientId"),
                        "event_line_id": row.get("eventLineId"),
                        "lifecycle_state": lifecycle,
                        "title": row.get("title"),
                        "agenda": row.get("agenda"),
                        "starts_at": row.get("startsAt"),
                        "ends_at": row.get("endsAt"),
                        "timezone": row.get("timezone"),
                        "organizer_membership_id": row.get("organizerMembershipId"),
                        "visibility_scope": row.get("visibilityScope"),
                        "status": row.get("status"),
                        "version": version,
                        "created_at": created_at,
                        "updated_at": updated_at,
                        "deleted_at": row.get("deletedAt"),
                        **self._projected(
                            row,
                            scope_id=scope_id,
                            sandbox_id=sandbox_id,
                            now=now,
                        ),
                    },
                )
                collaborators = list(row.get("collaborators") or [])
                desired_grant_ids = {str(item.get("grantId") or "") for item in collaborators}
                for current in connection.execute(
                    "SELECT id FROM object_grants WHERE scope_id=? AND secured_resource_id=? "
                    "AND capability_set_schema_version='yiyu.meeting-collaboration.v1' "
                    "AND lifecycle_state='active'",
                    (scope_id, meeting_id),
                ).fetchall():
                    if str(current["id"]) not in desired_grant_ids:
                        connection.execute(
                            "UPDATE object_grants SET status='revoked',lifecycle_state='archived',"
                            "revoked_at=?,updated_at=? WHERE id=?",
                            (now, now, current["id"]),
                        )
                for item in collaborators:
                    membership_id = str(item.get("membershipId") or "")
                    membership = connection.execute(
                        "SELECT principal_id FROM organization_memberships WHERE scope_id=? AND id=?",
                        (scope_id, membership_id),
                    ).fetchone()
                    if membership is None:
                        continue
                    role_key = str(item.get("roleKey") or "collaborator")
                    if role_key == "owner" and membership_id == str(row.get("createdByMembershipId") or ""):
                        role_key = "creator_owner"
                    elif membership_id == str(row.get("createdByMembershipId") or ""):
                        role_key = "creator"
                    inbox_status = str(item.get("inboxStatus") or "pending")
                    self._upsert(
                        connection,
                        "object_grants",
                        {
                            "id": item.get("grantId"),
                            "secured_resource_id": meeting_id,
                            "policy_version_id": None,
                            "subject_principal_id": membership["principal_id"],
                            "subject_membership_id": membership_id,
                            "capability_set_schema_version": "yiyu.meeting-collaboration.v1",
                            "capability_set": json.dumps({
                                "roleKey": role_key,
                                "read": True,
                                "respond": True,
                                "manage": role_key in {"creator", "creator_owner", "owner"},
                            }, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                            "grant_generation": 1,
                            "status": "active" if inbox_status == "accepted" else inbox_status,
                            "grant_source_set_id": None,
                            "created_at": created_at,
                            "updated_at": updated_at,
                            "revoked_at": None,
                            "version": int(item.get("version") or 1),
                            "lifecycle_state": "active",
                            "deleted_at": None,
                            **self._projected(
                                item,
                                scope_id=scope_id,
                                sandbox_id=sandbox_id,
                                now=now,
                            ),
                        },
                    )
                plan_link = row.get("planLink") if isinstance(row.get("planLink"), Mapping) else None
                connection.execute(
                    "UPDATE derivation_lineage SET invalidated_at=? WHERE scope_id=? "
                    "AND derivative_kind='meeting_plan_link' AND derivative_object_id=? "
                    "AND invalidated_at IS NULL",
                    (now, scope_id, meeting_id),
                )
                if plan_link and plan_link.get("sourceSetId") and plan_link.get("planningCycleId"):
                    source_set_id = str(plan_link["sourceSetId"])
                    principal = connection.execute(
                        "SELECT principal_id FROM sandboxes WHERE id=?",
                        (sandbox_id,),
                    ).fetchone()
                    kinds = [("planning_cycle", str(plan_link["planningCycleId"]))]
                    if plan_link.get("decisionActionId"):
                        kinds.append(("decision_action", str(plan_link["decisionActionId"])))
                    self._upsert(connection, "source_sets", {
                        "id": source_set_id,
                        "scope_id": scope_id,
                        "client_id": row.get("clientId"),
                        "security_label_set_version": None,
                        "source_count": len(kinds),
                        "version": version,
                        "purpose_kind": "meeting_plan_link",
                        "publication_state": "published",
                        "created_by_principal_id": principal["principal_id"] if principal else None,
                        "created_at": created_at,
                        "expires_at": None,
                        "lifecycle_state": "active",
                        "updated_at": updated_at,
                        "deleted_at": None,
                        "authority_role": "cloud",
                        "origin_instance_id": context.cloud_instance_id,
                    })
                    for ordinal, (kind, object_id) in enumerate(kinds):
                        member_id = "source_member_" + hashlib.sha256(
                            f"{meeting_id}\x1fmeeting_plan:{kind}".encode("utf-8")
                        ).hexdigest()[:30]
                        source_row = connection.execute(
                            f"SELECT version FROM {'planning_cycles' if kind == 'planning_cycle' else 'decision_actions'} WHERE id=?",
                            (object_id,),
                        ).fetchone()
                        self._upsert(connection, "source_set_members", {
                            "id": member_id,
                            "scope_id": scope_id,
                            "source_set_id": source_set_id,
                            "source_object_id": object_id,
                            "source_version": int(source_row["version"] or 1) if source_row else 1,
                            "policy_version": None,
                            "source_object_kind": kind,
                            "ordinal": ordinal,
                            "added_at": updated_at,
                            "removed_at": None,
                            "version": version,
                            "lifecycle_state": "active",
                            "created_at": created_at,
                            "updated_at": updated_at,
                            "deleted_at": None,
                            "authority_role": "cloud",
                            "origin_instance_id": context.cloud_instance_id,
                        })
                    lineage_id = "lineage_" + hashlib.sha256(
                        f"{meeting_id}\x1fmeeting_plan".encode("utf-8")
                    ).hexdigest()[:30]
                    self._upsert(connection, "derivation_lineage", {
                        "id": lineage_id,
                        "scope_id": scope_id,
                        "source_set_id": source_set_id,
                        "policy_version_id": None,
                        "grant_generation": None,
                        "derivative_kind": "meeting_plan_link",
                        "derivative_object_id": meeting_id,
                        "generator_version": "yiyu.meeting-collaboration.v1",
                        "generated_at": updated_at,
                        "invalidated_at": None,
                        "source_version": version,
                        "authority_role": "cloud",
                        "origin_instance_id": context.cloud_instance_id,
                    })
            return len(rows)

        return self._transaction(apply)

    def apply_calendar(self, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        def apply(connection: Any, _: Any, scope_id: str, __: str, ___: str) -> int:
            for row in rows:
                self._upsert(connection, "calendar_entries", {**row, "scope_id": scope_id})
            return len(rows)

        return self._transaction(apply)


__all__ = ["LocalGC06PlanningProjection"]
