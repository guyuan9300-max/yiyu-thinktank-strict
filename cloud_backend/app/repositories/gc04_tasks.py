"""GC-04/GC-05 task authority implemented only on the frozen 88-table schema.

The module is intentionally independent from the frozen workflow repository.
It owns no route registration and performs no DDL.  Integration code may mount
the companion route module after review.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .gc03_scope import validate_task_client_binding


PRIORITIES = frozenset({"low", "normal", "high"})
TASK_VISIBILITIES = frozenset({"self", "participants", "organization"})
LIST_VISIBILITIES = frozenset({"personal", "organization"})
ACTIVE_INBOX_STATES = frozenset({"pending", "accepted"})
TASK_VIEW_PROJECTION_CONTRACT = {
    "schema": "yiyu.task-viewer-projection.v1",
    "schemaVersion": 1,
    "requiredTaskFields": [
        "viewer_surfaces",
        "viewer_capabilities",
        "owner_department_resolution",
        "owner_department_id",
        "owner_department_name",
        "owner_departments",
    ],
}
TASK_FOCUS_RUN_KIND = "task_focus_session"
TASK_TIMER_STATES = frozenset({"running", "paused", "stopped"})


def _text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _integer(value: Any, *, minimum: int = 0) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(422, "integer_invalid", "数字字段格式无效") from exc
    if parsed < minimum:
        raise RepositoryError(422, "integer_invalid", "数字字段超出允许范围")
    return parsed


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_" + sha256_text("\x1f".join(parts))[:30]


def _payload_hash(payload: Mapping[str, Any]) -> str:
    return sha256_text(canonical_json(dict(payload)))


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


class GC04TaskRepository:
    """Cloud task authority, collaboration state machine and GC-05 batch lane."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    # ------------------------------------------------------------------
    # Reliable command helpers
    # ------------------------------------------------------------------
    def _receipt(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT i.payload_hash, i.result_hash, m.receipt
            FROM idempotency_records AS i
            LEFT JOIN object_manifests AS m
              ON m.scope_id=i.scope_id AND m.id=i.result_object_manifest_id
            WHERE i.scope_id=? AND i.idempotency_key=?
            """,
            (identity.scope_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"] or "") != payload_hash:
            raise RepositoryError(409, "idempotency_conflict", "操作标识已用于不同内容")
        receipt = str(row["receipt"] or "")
        if not receipt or sha256_text(receipt) != str(row["result_hash"] or ""):
            raise RepositoryError(500, "idempotency_receipt_invalid", "操作回执校验失败")
        result = json.loads(receipt)
        if not isinstance(result, dict):
            raise RepositoryError(500, "idempotency_receipt_invalid", "操作回执结构无效")
        return result

    def _store_manifest(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        storage_kind: str,
        value: Mapping[str, Any],
        now: str,
    ) -> tuple[str, str, str]:
        manifest_id = new_id()
        receipt = canonical_json(dict(value))
        receipt_hash = sha256_text(receipt)
        connection.execute(
            """
            INSERT INTO object_manifests (
                id, scope_id, storage_key, content_hash, lifecycle_state,
                receipt, holder_role, holder_instance_id, storage_kind,
                byte_size, media_type, availability_state, receipt_hash,
                created_at, verified_at, deleted_at, authority_role,
                origin_instance_id
            ) VALUES (?, ?, NULL, ?, 'active', ?, 'organization_cloud', ?, ?,
                      ?, 'application/json', 'ready', ?, ?, ?, NULL, 'cloud', ?)
            """,
            (
                manifest_id,
                identity.scope_id,
                receipt_hash,
                receipt,
                identity.cloud_instance_id,
                storage_kind,
                len(receipt.encode("utf-8")),
                receipt_hash,
                now,
                now,
                identity.cloud_instance_id,
            ),
        )
        return manifest_id, receipt_hash, receipt

    def _record_command(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
        payload_hash: str,
        command_type: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        expected_version: int | None,
        result: Mapping[str, Any],
        now: str,
        event_type: str | None = None,
    ) -> tuple[str, str]:
        operation_id = new_id()
        manifest_id, result_hash, _ = self._store_manifest(
            connection,
            identity,
            storage_kind="command_receipt",
            value=result,
            now=now,
        )
        connection.execute(
            """
            INSERT INTO idempotency_records (
                id, scope_id, idempotency_key, payload_hash, result_hash,
                expires_at, result_object_manifest_id, status, created_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, '9999-12-31T23:59:59.999Z', ?,
                      'completed', ?, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, idempotency_key, payload_hash,
                result_hash, manifest_id, now, identity.cloud_instance_id,
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'committed', ?, ?, ?,
                      ?, ?, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, operation_id, idempotency_key,
                aggregate_type, aggregate_id, command_type, identity.principal_id,
                expected_version, identity.membership_id, manifest_id, payload_hash,
                now, now, identity.cloud_instance_id,
            ),
        )
        event_hash = sha256_text(
            canonical_json(
                {
                    "operationId": operation_id,
                    "commandType": command_type,
                    "aggregateType": aggregate_type,
                    "aggregateId": aggregate_id,
                    "aggregateVersion": aggregate_version,
                    "resultHash": result_hash,
                }
            )
        )
        secured_target = connection.execute(
            "SELECT id FROM secured_resources WHERE id=? AND scope_id=?",
            (aggregate_id, identity.scope_id),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO audit_events (
                id, scope_id, operation_id, actor_id, action, event_hash,
                actor_membership_id, target_resource_id,
                details_object_manifest_id, occurred_at, origin_instance_id,
                created_at, integrity_hash, authority_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cloud')
            """,
            (
                new_id(), identity.scope_id, operation_id, identity.principal_id,
                command_type, event_hash, identity.membership_id,
                aggregate_id if secured_target is not None else None,
                manifest_id, now, identity.cloud_instance_id, now, event_hash,
            ),
        )
        connection.execute(
            """
            INSERT INTO outbox_events (
                id, scope_id, operation_id, aggregate_version, event_type,
                status, aggregate_type, aggregate_id, event_object_manifest_id,
                event_hash, available_at, published_at, authority_role,
                origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, NULL, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, operation_id, aggregate_version,
                event_type or command_type, aggregate_type, aggregate_id,
                manifest_id, event_hash, now, identity.cloud_instance_id,
            ),
        )
        return operation_id, manifest_id

    def _record_child_task_command(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        parent_operation_id: str,
        parent_manifest_id: str,
        idempotency_key: str,
        payload_hash: str,
        task_id: str,
        task_version: int,
        expected_version: int,
        now: str,
    ) -> None:
        operation_id = _stable_id("op", parent_operation_id, task_id)
        result_row = connection.execute(
            "SELECT content_hash FROM object_manifests WHERE id=? AND scope_id=?",
            (parent_manifest_id, identity.scope_id),
        ).fetchone()
        result_hash = str(result_row["content_hash"] or "")
        connection.execute(
            """
            INSERT INTO idempotency_records (
                id, scope_id, idempotency_key, payload_hash, result_hash,
                expires_at, result_object_manifest_id, status, created_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, '9999-12-31T23:59:59.999Z', ?,
                      'completed', ?, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, idempotency_key, payload_hash,
                result_hash, parent_manifest_id, now, identity.cloud_instance_id,
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
            ) VALUES (?, ?, ?, ?, 'task', ?, 'task.bulk_updated', ?, ?, NULL,
                      'committed', ?, ?, ?, ?, ?, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, operation_id, idempotency_key,
                task_id, identity.principal_id, expected_version,
                identity.membership_id, parent_manifest_id, payload_hash,
                now, now, identity.cloud_instance_id,
            ),
        )
        event_hash = sha256_text(
            f"{operation_id}|task.bulk_updated|{task_id}|{task_version}|{result_hash}"
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, scope_id, operation_id, actor_id, action, event_hash,
                actor_membership_id, target_resource_id,
                details_object_manifest_id, occurred_at, origin_instance_id,
                created_at, integrity_hash, authority_role
            ) VALUES (?, ?, ?, ?, 'task.bulk_updated', ?, ?, ?, ?, ?, ?, ?, ?, 'cloud')
            """,
            (
                new_id(), identity.scope_id, operation_id, identity.principal_id,
                event_hash, identity.membership_id, task_id, parent_manifest_id,
                now, identity.cloud_instance_id, now, event_hash,
            ),
        )
        connection.execute(
            """
            INSERT INTO outbox_events (
                id, scope_id, operation_id, aggregate_version, event_type,
                status, aggregate_type, aggregate_id, event_object_manifest_id,
                event_hash, available_at, published_at, authority_role,
                origin_instance_id
            ) VALUES (?, ?, ?, ?, 'task.bulk_updated', 'pending', 'task', ?, ?, ?,
                      ?, NULL, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, operation_id, task_version, task_id,
                parent_manifest_id, event_hash, now, identity.cloud_instance_id,
            ),
        )

    # ------------------------------------------------------------------
    # Robot coworker coordination (strict bot/rule/run/task objects only)
    # ------------------------------------------------------------------
    @staticmethod
    def _agent_meta(agent_key: str) -> tuple[str, str, str]:
        values = {
            "strategy_design": ("策略设计助手", "咨询策略部", "#F59E0B"),
            "tech_development": ("技术研发助手", "技术创新部", "#5B7BFE"),
            "info_data": ("信息研究助手", "信息数据部", "#10B981"),
        }
        if agent_key not in values:
            raise RepositoryError(422, "agent_key_invalid", "机器人岗位标识无效")
        return values[agent_key]

    @staticmethod
    def _week_bounds(week_label: str) -> tuple[str, str]:
        try:
            year_text, week_text = week_label.split("-W", 1)
            monday = date.fromisocalendar(int(year_text), int(week_text), 1)
        except (TypeError, ValueError) as exc:
            raise RepositoryError(422, "agent_week_invalid", "周标识必须为 YYYY-Www") from exc
        return monday.isoformat(), (monday + timedelta(days=6)).isoformat()

    def agent_coordination(
        self,
        identity: SessionIdentity,
        *,
        week_label: str | None = None,
        month: str | None = None,
        department_name: str | None = None,
    ) -> dict[str, Any]:
        if month and not __import__("re").fullmatch(r"\d{4}-\d{2}", month):
            raise RepositoryError(422, "agent_worklog_month_invalid", "月份必须为 YYYY-MM")
        with self.repository._connection() as connection:  # noqa: SLF001
            rules = connection.execute(
                "SELECT * FROM automation_rules WHERE scope_id=? "
                "AND record_kind='task_control' AND template_key LIKE 'agent_weekly_plan:%' "
                "AND lifecycle_state='active' ORDER BY updated_at DESC,id",
                (identity.scope_id,),
            ).fetchall()
            weekly_plans: list[dict[str, Any]] = []
            for row in rules:
                try:
                    trigger = json.loads(str(row["trigger_spec"] or "{}"))
                    action = json.loads(str(row["action_spec"] or "{}"))
                except json.JSONDecodeError:
                    continue
                agent_key = str(action.get("agentKey") or "")
                plan_week = str(trigger.get("weekLabel") or "")
                if week_label and plan_week != week_label:
                    continue
                try:
                    agent_name, department, color = self._agent_meta(agent_key)
                except RepositoryError:
                    continue
                if department_name and department != department_name:
                    continue
                items = [
                    {
                        "id": _stable_id("agent_plan_item", str(row["id"]), str(index)),
                        "title": str(item.get("title") or ""),
                        "rationale": str(item.get("rationale") or ""),
                        "scheduleHint": str(item.get("scheduleHint") or ""),
                        "status": str(item.get("status") or "planned"),
                    }
                    for index, item in enumerate(action.get("planItems") or [])
                    if isinstance(item, Mapping) and str(item.get("title") or "").strip()
                ]
                weekly_plans.append({
                    "planId": str(row["id"]), "agentKey": agent_key,
                    "agentName": agent_name, "departmentName": department,
                    "color": color, "weekLabel": plan_week,
                    "summary": str(action.get("summary") or ""), "planItems": items,
                    "sourcePolicy": {"authority": "automation_rules", "manualOverride": True},
                    "version": int(row["version"] or 1), "updatedAt": row["updated_at"],
                })
            bot_rows = connection.execute(
                "SELECT * FROM bot_definitions WHERE scope_id=? AND lifecycle_state='active' "
                "AND enabled=1 ORDER BY updated_at DESC,id",
                (identity.scope_id,),
            ).fetchall()
            bot_by_id = {str(row["id"]): row for row in bot_rows}
            plan_keys = {str(item["agentKey"]) for item in weekly_plans}
            run_rows = connection.execute(
                "SELECT * FROM execution_runs WHERE scope_id=? AND lifecycle_state='active' "
                "ORDER BY COALESCE(started_at,created_at) DESC,id LIMIT 500",
                (identity.scope_id,),
            ).fetchall()
            worklogs: list[dict[str, Any]] = []
            for run in run_rows:
                bot = bot_by_id.get(str(run["bot_id"] or ""))
                if bot is None:
                    continue
                handle = str(bot["handle"] or "")
                agent_key = next((key for key in ("strategy_design", "tech_development", "info_data") if key in handle), "")
                if not agent_key:
                    continue
                agent_name, department, color = self._agent_meta(agent_key)
                if department_name and department != department_name:
                    continue
                occurred = str(run["started_at"] or run["created_at"] or "")
                if month and not occurred.startswith(month):
                    continue
                run_week = ""
                try:
                    parsed = datetime.fromisoformat(occurred.replace("Z", "+00:00")).date()
                    iso = parsed.isocalendar()
                    run_week = f"{iso.year}-W{iso.week:02d}"
                except ValueError:
                    pass
                if week_label and run_week != week_label:
                    continue
                worklogs.append({
                    "id": str(run["id"]), "agentKey": agent_key,
                    "agentName": agent_name, "departmentName": department, "color": color,
                    "date": occurred[:10], "weekLabel": run_week,
                    "title": str(run["run_kind"] or "机器人执行"),
                    "summary": f"执行状态：{str(run['status'] or 'unknown')}",
                    "detailLines": [], "sourceType": "workspace_sync", "createdAt": occurred,
                })
                plan_keys.add(agent_key)
            digests = []
            for key in sorted(plan_keys):
                agent_name, department, color = self._agent_meta(key)
                related = [item for item in worklogs if item["agentKey"] == key]
                plan = next((item for item in weekly_plans if item["agentKey"] == key), None)
                digests.append({
                    "agentKey": key, "agentName": agent_name, "departmentName": department,
                    "color": color, "weekLabel": week_label or (plan or {}).get("weekLabel") or "",
                    "summary": (plan or {}).get("summary") or f"本期形成 {len(related)} 条真实执行记录",
                    "focusItems": [item["title"] for item in (plan or {}).get("planItems", [])],
                    "evidenceCount": len(related), "sourcePolicy": {"authority": "execution_runs"},
                })
            board = self.board(identity)
            owner_memberships = {
                str(row["owner_membership_id"]): next(
                    (key for key in ("strategy_design", "tech_development", "info_data") if key in str(row["handle"] or "")),
                    "",
                )
                for row in bot_rows if row["owner_membership_id"]
            }
            agent_tasks = []
            start, end = self._week_bounds(week_label) if week_label else (None, None)
            for task in board["tasks"]:
                owners = [
                    str(item.get("subject_membership_id") or "")
                    for item in task.get("collaborators") or []
                    if str(item.get("role_key") or "") == "owner"
                ]
                key = next((owner_memberships.get(owner) for owner in owners if owner_memberships.get(owner)), "")
                if not key:
                    continue
                agent_name, department, _ = self._agent_meta(key)
                if department_name and department != department_name:
                    continue
                due = str(task.get("due_date") or "")[:10]
                if start and due and not (start <= due <= end):
                    continue
                task["agent_key"] = key
                task["agent_name"] = agent_name
                agent_tasks.append(task)
            return {"tasks": agent_tasks, "worklogs": worklogs, "weeklyDigests": digests, "weeklyPlans": weekly_plans}

    def save_agent_weekly_plan(
        self,
        identity: SessionIdentity,
        *,
        week_label: str,
        agent_key: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not identity.is_admin:
            raise RepositoryError(403, "agent_weekly_plan_admin_required", "仅组织管理员可维护机器人周计划")
        self._week_bounds(week_label)
        agent_name, department, color = self._agent_meta(agent_key)
        items = [
            {"title": str(item.get("title") or "").strip()[:300],
             "rationale": str(item.get("rationale") or "").strip()[:2000],
             "scheduleHint": str(item.get("scheduleHint") or "").strip()[:500],
             "status": str(item.get("status") or "planned")}
            for item in payload.get("planItems") or []
            if isinstance(item, Mapping) and str(item.get("title") or "").strip()
        ]
        action = {"agentKey": agent_key, "summary": str(payload.get("summary") or "").strip()[:4000], "planItems": items}
        trigger = {"weekLabel": week_label}
        normalized = {"agentKey": agent_key, "weekLabel": week_label, **action}
        payload_hash = _payload_hash(normalized)
        rule_id = _stable_id("rule", identity.scope_id, "agent_weekly_plan", week_label, agent_key)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                now = utc_now()
                row = connection.execute("SELECT * FROM automation_rules WHERE id=? AND scope_id=?", (rule_id, identity.scope_id)).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                        "VALUES (?,?,'automation_rule','active',1,'task_control',?,?,NULL,'cloud',?)",
                        (rule_id, identity.scope_id, now, now, identity.cloud_instance_id),
                    )
                    version = 1
                    connection.execute(
                        "INSERT INTO automation_rules (id,scope_id,template_key,rule_version,trigger_spec,record_kind,trigger_spec_schema_version,action_spec_schema_version,action_spec,trusted_source_pattern,enabled,effective_at,version,lifecycle_state,created_at,updated_at,deleted_at) "
                        "VALUES (?,?,?,1,?,'task_control','agent-weekly-plan.v1','agent-weekly-plan.v1',?,NULL,1,?,1,'active',?,?,NULL)",
                        (rule_id, identity.scope_id, f"agent_weekly_plan:{week_label}:{agent_key}", canonical_json(trigger), canonical_json(action), now, now, now),
                    )
                    expected = None
                else:
                    expected = int(payload.get("expectedVersion") or row["version"] or 1)
                    if int(row["version"] or 1) != expected:
                        raise RepositoryError(409, "agent_weekly_plan_version_conflict", "机器人周计划已更新，请刷新后重试")
                    connection.execute(
                        "UPDATE automation_rules SET trigger_spec=?,action_spec=?,enabled=1,version=version+1,updated_at=? WHERE id=? AND scope_id=? AND version=?",
                        (canonical_json(trigger), canonical_json(action), now, rule_id, identity.scope_id, expected),
                    )
                    version = expected + 1
                weekly_plan = {"planId": rule_id, "agentKey": agent_key, "agentName": agent_name, "departmentName": department, "color": color, "weekLabel": week_label, "summary": action["summary"], "planItems": [{"id": _stable_id("agent_plan_item", rule_id, str(i)), **item} for i,item in enumerate(items)], "sourcePolicy": {"authority": "automation_rules", "manualOverride": True}, "version": version, "updatedAt": now}
                result = {"weeklyPlan": weekly_plan}
                self._record_command(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash, command_type="agent_weekly_plan.saved", aggregate_type="automation_rule", aggregate_id=rule_id, aggregate_version=version, expected_version=expected, result=result, now=now)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # Permission, identity and shape validation
    # ------------------------------------------------------------------
    @staticmethod
    def _active_members(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        membership_ids: Iterable[str],
    ) -> dict[str, sqlite3.Row]:
        requested = sorted({_text(item) for item in membership_ids} - {None})
        if not requested:
            return {}
        placeholders = ",".join("?" for _ in requested)
        rows = connection.execute(
            f"SELECT membership.*, principal.display_name FROM organization_memberships "
            f"AS membership JOIN principals AS principal ON principal.id=membership.principal_id "
            f"WHERE membership.scope_id=? AND membership.id IN ({placeholders}) "
            "AND membership.record_kind='membership' AND membership.status='active' "
            "AND membership.lifecycle_state='active'",
            (identity.scope_id, *requested),
        ).fetchall()
        found = {str(row["id"]): row for row in rows}
        if set(requested) != set(found):
            raise RepositoryError(422, "task_member_invalid", "任务成员不属于当前组织")
        return found

    def _require_project_reference_access(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        client_id: str | None,
    ) -> None:
        """Allow a task to reference any project the member may read.

        Linking a task to a project does not mutate the project itself.  Project
        write permission remains required by the project mutation endpoints.
        """
        if client_id is None:
            return
        self.repository._require_project_access(  # noqa: SLF001
            connection,
            identity,
            project_id=client_id,
            capability="read",
        )

    @staticmethod
    def _task_row(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_id: str,
        *,
        include_deleted: bool = False,
    ) -> sqlite3.Row:
        suffix = "" if include_deleted else " AND lifecycle_state!='deleted'"
        row = connection.execute(
            f"SELECT * FROM tasks WHERE id=? AND scope_id=?{suffix}",
            (task_id, identity.scope_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "task_missing", "任务不存在或已不可用")
        return row

    def _can_read_task(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
    ) -> bool:
        # A client/project is context for a task, not its permission parent.
        # Project access must never make every project task visible.  Normal
        # task views follow the task's own participants/visibility contract;
        # administrative audit access belongs in a separate, explicit lane.
        if str(row["creator_membership_id"] or "") == identity.membership_id:
            return True
        collaborator = connection.execute(
            "SELECT 1 FROM task_collaborators WHERE scope_id=? AND task_id=? "
            "AND subject_membership_id=? AND lifecycle_state='active' "
            "AND assignment_state='assigned' "
            "AND inbox_status IN ('pending','accepted') LIMIT 1",
            (identity.scope_id, row["id"], identity.membership_id),
        ).fetchone()
        if collaborator is not None:
            return True
        return str(row["visibility_scope"] or "participants") == "organization"

    def _require_task_read(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_id: str,
    ) -> sqlite3.Row:
        row = self._task_row(connection, identity, task_id)
        if not self._can_read_task(connection, identity, row):
            raise RepositoryError(404, "task_missing", "任务不存在或已不可用")
        return row

    def _can_write_task(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
    ) -> bool:
        if str(row["creator_membership_id"] or "") == identity.membership_id:
            return True
        owner = connection.execute(
            "SELECT 1 FROM task_collaborators WHERE scope_id=? AND task_id=? "
            "AND subject_membership_id=? AND role_key='owner' "
            "AND assignment_state='assigned' AND inbox_status='accepted' "
            "AND lifecycle_state='active' LIMIT 1",
            (identity.scope_id, row["id"], identity.membership_id),
        ).fetchone()
        if owner is not None:
            return True
        return False

    def _require_task_write(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_id: str,
    ) -> sqlite3.Row:
        row = self._require_task_read(connection, identity, task_id)
        if not self._can_write_task(connection, identity, row):
            raise RepositoryError(403, "task_forbidden", "当前成员无权修改该任务")
        return row

    @staticmethod
    def _parse_timer_timestamp(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _can_track_task_time(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
    ) -> bool:
        if str(row["creator_membership_id"] or "") == identity.membership_id:
            return True
        participant = connection.execute(
            "SELECT 1 FROM task_collaborators WHERE scope_id=? AND task_id=? "
            "AND subject_membership_id=? AND role_key IN ('owner','collaborator') "
            "AND assignment_state='assigned' AND inbox_status='accepted' "
            "AND lifecycle_state='active' LIMIT 1",
            (identity.scope_id, row["id"], identity.membership_id),
        ).fetchone()
        return participant is not None

    def _task_timer_summary(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_id: str,
        *,
        observed_at: str | None = None,
    ) -> dict[str, Any]:
        observed_text = observed_at or utc_now()
        observed = self._parse_timer_timestamp(observed_text) or datetime.now(timezone.utc)
        rows = connection.execute(
            "SELECT * FROM execution_runs WHERE scope_id=? AND task_id=? "
            "AND initiator_membership_id=? AND run_kind=? "
            "AND lifecycle_state='active' ORDER BY created_at,id",
            (
                identity.scope_id,
                task_id,
                identity.membership_id,
                TASK_FOCUS_RUN_KIND,
            ),
        ).fetchall()
        elapsed_seconds = 0
        for run in rows:
            started = self._parse_timer_timestamp(run["started_at"])
            if started is None:
                continue
            status = str(run["status"] or "")
            finished = self._parse_timer_timestamp(run["finished_at"])
            end = observed if status == "running" and finished is None else finished
            if end is not None:
                elapsed_seconds += max(0, int((end - started).total_seconds()))
        latest = rows[-1] if rows else None
        latest_state = str(latest["status"] or "") if latest is not None else "idle"
        if latest_state not in TASK_TIMER_STATES:
            latest_state = "idle"
        return {
            "state": latest_state,
            "elapsedSeconds": elapsed_seconds,
            "activeStartedAt": (
                latest["started_at"]
                if latest is not None and latest_state == "running"
                else None
            ),
            "latestRunId": str(latest["id"]) if latest is not None else None,
            "version": sum(int(run["version"] or 1) for run in rows),
            "observedAt": observed_text,
        }

    @staticmethod
    def _require_expected_version(payload: Mapping[str, Any]) -> int:
        try:
            version = int(payload.get("expectedVersion") or payload.get("expected_version") or 0)
        except (TypeError, ValueError):
            version = 0
        if version <= 0:
            raise RepositoryError(422, "expected_version_required", "缺少有效的 expectedVersion")
        return version

    @staticmethod
    def _validate_schedule(
        scheduled_start_at: str | None,
        scheduled_end_at: str | None,
    ) -> None:
        if not scheduled_start_at or not scheduled_end_at:
            return
        try:
            starts = datetime.fromisoformat(scheduled_start_at.replace("Z", "+00:00"))
            ends = datetime.fromisoformat(scheduled_end_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RepositoryError(422, "task_schedule_invalid", "任务时间格式无效") from exc
        if ends <= starts:
            raise RepositoryError(422, "task_schedule_invalid", "任务结束时间必须晚于开始时间")

    def _validate_list(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_list_id: str | None,
    ) -> str | None:
        if task_list_id is None:
            return None
        row = connection.execute(
            "SELECT * FROM task_lists WHERE id=? AND scope_id=? "
            "AND lifecycle_state='active'",
            (task_list_id, identity.scope_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(422, "task_list_invalid", "任务清单不存在或已归档")
        if (
            str(row["visibility_scope"] or "personal") != "organization"
            and str(row["owner_membership_id"] or "") != identity.membership_id
            and not identity.is_admin
        ):
            raise RepositoryError(403, "task_list_forbidden", "当前成员无权使用该任务清单")
        return str(row["id"])

    @staticmethod
    def _validate_planning_cycle(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        planning_cycle_id: str | None,
        client_id: str | None,
    ) -> str | None:
        if planning_cycle_id is None:
            return None
        row = connection.execute(
            "SELECT * FROM planning_cycles WHERE id=? AND scope_id=? "
            "AND lifecycle_state='active' AND status!='archived'",
            (planning_cycle_id, identity.scope_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(422, "planning_cycle_invalid", "关联计划不存在或已归档")
        plan_client = _text(row["client_id"])
        if plan_client and plan_client != client_id:
            raise RepositoryError(409, "planning_cycle_client_mismatch", "任务项目必须与关联计划项目一致")
        from .gc06_planning import _require_plan_permission
        _require_plan_permission(
            connection, identity, record_kind=str(row["record_kind"]),
            department_id=row["department_id"], write=False,
        )
        return str(row["id"])

    def _validate_tags(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        tag_ids: Iterable[Any],
    ) -> list[str]:
        normalized = sorted({_text(item) for item in tag_ids if _text(item)})
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        rows = connection.execute(
            f"SELECT view.id,view.assigned_by_membership_id,resource.resource_type_key "
            f"FROM task_views view JOIN secured_resources resource ON resource.id=view.id "
            f"AND resource.scope_id=view.scope_id WHERE view.scope_id=? "
            f"AND view.id IN ({placeholders}) AND view.record_kind='tag' "
            "AND view.lifecycle_state='active'",
            (identity.scope_id, *normalized),
        ).fetchall()
        found = {str(row["id"]): row for row in rows}
        if set(found) != set(normalized):
            raise RepositoryError(422, "task_tag_invalid", "任务标签不存在或已归档")
        if any(
            str(row["resource_type_key"] or "") == "task_tag_personal"
            and str(row["assigned_by_membership_id"] or "") != identity.membership_id
            and not identity.is_admin
            for row in rows
        ):
            raise RepositoryError(403, "task_tag_forbidden", "当前成员无权使用该任务标签")
        return normalized

    def _replace_task_tags(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        task_id: str,
        tag_ids: Iterable[str],
        now: str,
    ) -> None:
        desired = set(tag_ids)
        existing = connection.execute(
            "SELECT * FROM task_views WHERE scope_id=? AND record_kind='tag_assignment' "
            "AND task_id=? AND lifecycle_state='active'",
            (identity.scope_id, task_id),
        ).fetchall()
        for row in existing:
            tag_id = str(row["tag_id"] or "")
            if tag_id in desired:
                desired.remove(tag_id)
                continue
            connection.execute(
                "UPDATE task_views SET lifecycle_state='deleted',deleted_at=?,"
                "version=version+1,updated_at=? WHERE id=? AND scope_id=?",
                (now, now, row["id"], identity.scope_id),
            )
            connection.execute(
                "UPDATE secured_resources SET lifecycle_state='deleted',deleted_at=?,"
                "version=version+1,updated_at=? WHERE id=? AND scope_id=?",
                (now, now, row["id"], identity.scope_id),
            )
        for tag_id in sorted(desired):
            assignment_id = _stable_id("task_tag", identity.scope_id, task_id, tag_id)
            connection.execute(
                "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
                "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
                "origin_instance_id) VALUES (?,?,'task_view','active',1,'task_tag_assignment',"
                "?,?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET lifecycle_state='active',"
                "deleted_at=NULL,version=secured_resources.version+1,updated_at=excluded.updated_at",
                (assignment_id, identity.scope_id, now, now, identity.cloud_instance_id),
            )
            connection.execute(
                "INSERT INTO task_views (id,scope_id,task_list_id,viewer_principal_id,"
                "viewer_membership_id,filter_spec,version,record_kind,"
                "filter_spec_schema_version,tag_name,tag_color,task_id,tag_id,"
                "assigned_by_membership_id,lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES (?,?,NULL,NULL,NULL,NULL,1,'tag_assignment',NULL,"
                "NULL,NULL,?,?,?,'active',?,?,NULL) "
                "ON CONFLICT(id) DO UPDATE SET lifecycle_state='active',deleted_at=NULL,"
                "version=task_views.version+1,assigned_by_membership_id=excluded.assigned_by_membership_id,"
                "updated_at=excluded.updated_at",
                (
                    assignment_id, identity.scope_id, task_id, tag_id,
                    identity.membership_id, now, now,
                ),
            )

    def _task_tags(
        self, connection: sqlite3.Connection, identity: SessionIdentity, task_id: str
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT tag.*,resource.resource_type_key FROM task_views assignment JOIN task_views tag "
            "ON tag.id=assignment.tag_id AND tag.scope_id=assignment.scope_id "
            "JOIN secured_resources resource ON resource.id=tag.id AND resource.scope_id=tag.scope_id "
            "WHERE assignment.scope_id=? AND assignment.task_id=? "
            "AND assignment.record_kind='tag_assignment' "
            "AND assignment.lifecycle_state='active' AND tag.record_kind='tag' "
            "AND tag.lifecycle_state='active' ORDER BY tag.tag_name,tag.id",
            (identity.scope_id, task_id),
        ).fetchall()
        return [
            {
                "taskTagId": str(row["id"]),
                "name": str(row["tag_name"] or ""),
                "color": str(row["tag_color"] or "#5B7BFE"),
                "scopeKind": "personal" if row["resource_type_key"] == "task_tag_personal" else "organization",
                "ownerMembershipId": row["assigned_by_membership_id"],
                "version": int(row["version"] or 1),
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def _normalize_create(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise RepositoryError(422, "task_title_required", "请输入任务标题")
        priority = str(payload.get("priority") or "normal").strip().lower()
        if priority not in PRIORITIES:
            raise RepositoryError(422, "task_priority_invalid", "任务优先级无效")
        visibility = str(
            payload.get("visibilityScope")
            or ("self" if payload.get("scopeMode") == "PERSONAL_ONLY" else "participants")
        ).strip()
        if visibility not in TASK_VISIBILITIES:
            raise RepositoryError(422, "task_visibility_invalid", "任务可见范围无效")
        client_id = _text(payload.get("clientId") if "clientId" in payload else payload.get("projectId"))
        event_line_id = _text(payload.get("eventLineId"))
        binding = validate_task_client_binding(
            connection,
            scope_id=identity.scope_id,
            client_id=client_id,
            event_line_id=event_line_id,
        )
        self._require_project_reference_access(
            connection, identity, client_id=binding.client_id
        )
        list_value = payload.get("taskListId") if "taskListId" in payload else payload.get("listId")
        task_list_id = self._validate_list(connection, identity, _text(list_value))
        planning_cycle_id = self._validate_planning_cycle(
            connection, identity, _text(payload.get("planningCycleId")), binding.client_id
        )
        tag_ids = self._validate_tags(connection, identity, payload.get("tagIds") or [])
        owner_value = payload.get("ownerMembershipId") if "ownerMembershipId" in payload else payload.get("ownerId")
        owner_id = _text(owner_value) or identity.membership_id
        collaborator_values = (
            payload.get("collaboratorMembershipIds")
            if "collaboratorMembershipIds" in payload
            else payload.get("collaboratorIds")
        ) or []
        collaborator_ids = sorted(
            {_text(item) for item in collaborator_values if _text(item)} - {owner_id}
        )
        self._active_members(connection, identity, [owner_id, *collaborator_ids])
        scheduled_start = _text(payload.get("scheduledStartAt") or payload.get("startDate"))
        scheduled_end = _text(payload.get("scheduledEndAt"))
        self._validate_schedule(scheduled_start, scheduled_end)
        return {
            "title": title,
            "description": str(payload.get("description") if "description" in payload else payload.get("desc") or "").strip(),
            "priority": priority,
            "task_kind": _text(payload.get("taskKind")) or "standard",
            "visibility_scope": visibility,
            "task_list_id": task_list_id,
            "tag_ids": tag_ids,
            "client_id": binding.client_id,
            "event_line_id": binding.event_line_id,
            "planning_cycle_id": planning_cycle_id,
            "due_date": _text(payload.get("dueDate") or payload.get("deadlineAt") or payload.get("ddl")),
            "scheduled_start_at": scheduled_start,
            "scheduled_end_at": scheduled_end,
            "duration_minutes": _integer(payload.get("durationMinutes"), minimum=0),
            "source_type": _text(payload.get("sourceType")) or "manual",
            "source_id": _text(payload.get("sourceId")),
            "owner_membership_id": owner_id,
            "collaborator_membership_ids": collaborator_ids,
        }

    def _normalize_patch(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        if "title" in payload:
            title = str(payload.get("title") or "").strip()
            if not title:
                raise RepositoryError(422, "task_title_required", "请输入任务标题")
            patch["title"] = title
        if "description" in payload or "desc" in payload:
            patch["description"] = str(
                payload.get("description") if "description" in payload else payload.get("desc") or ""
            ).strip()
        if "priority" in payload:
            priority = str(payload.get("priority") or "").strip().lower()
            if priority not in PRIORITIES:
                raise RepositoryError(422, "task_priority_invalid", "任务优先级无效")
            patch["priority"] = priority
        if "taskKind" in payload:
            task_kind = _text(payload.get("taskKind")) or "task"
            if task_kind not in {"standard", "task", "review_pending", "review_returned"}:
                raise RepositoryError(422, "task_kind_invalid", "任务类型无效")
            patch["task_kind"] = task_kind
        for source, column in (
            ("dueDate", "due_date"),
            ("scheduledStartAt", "scheduled_start_at"),
            ("scheduledEndAt", "scheduled_end_at"),
            ("completionNote", "completion_note"),
        ):
            if source in payload:
                patch[column] = _text(payload.get(source))
        if "scheduledStartAt" not in payload and "startDate" in payload:
            patch["scheduled_start_at"] = _text(payload.get("startDate"))
        if "durationMinutes" in payload:
            patch["duration_minutes"] = _integer(payload.get("durationMinutes"), minimum=0)
        if "taskListId" in payload or "listId" in payload:
            raw = payload.get("taskListId") if "taskListId" in payload else payload.get("listId")
            patch["task_list_id"] = self._validate_list(connection, identity, _text(raw))
        if "planningCycleId" in payload:
            patch["planning_cycle_id"] = self._validate_planning_cycle(
                connection,
                identity,
                _text(payload.get("planningCycleId")),
                _text(patch.get("client_id", row["client_id"])),
            )
        if "tagIds" in payload:
            patch["tag_ids"] = self._validate_tags(
                connection, identity, payload.get("tagIds") or []
            )
        if "visibilityScope" in payload or "scopeMode" in payload:
            visibility = str(
                payload.get("visibilityScope")
                or ("self" if payload.get("scopeMode") == "PERSONAL_ONLY" else "participants")
            ).strip()
            if visibility not in TASK_VISIBILITIES:
                raise RepositoryError(422, "task_visibility_invalid", "任务可见范围无效")
            patch["visibility_scope"] = visibility
        if "clientId" in payload or "projectId" in payload or "eventLineId" in payload:
            client_id = (
                _text(payload.get("clientId") if "clientId" in payload else payload.get("projectId"))
                if ("clientId" in payload or "projectId" in payload)
                else _text(row["client_id"])
            )
            event_line_id = (
                _text(payload.get("eventLineId"))
                if "eventLineId" in payload
                else _text(row["event_line_id"])
            )
            current_client_id = _text(row["client_id"])
            current_event_line_id = _text(row["event_line_id"])
            if (
                client_id != current_client_id
                or event_line_id != current_event_line_id
            ):
                binding = validate_task_client_binding(
                    connection,
                    scope_id=identity.scope_id,
                    client_id=client_id,
                    event_line_id=event_line_id,
                )
                self._require_project_reference_access(
                    connection, identity, client_id=binding.client_id
                )
                patch["client_id"] = binding.client_id
                patch["event_line_id"] = binding.event_line_id
        progress = _text(payload.get("progressStatus") or payload.get("status"))
        if progress:
            if progress in {"done", "completed"}:
                patch["completed_at"] = _text(payload.get("completedAt")) or utc_now()
            elif progress in {"todo", "active"}:
                patch["completed_at"] = None
                if "completionNote" not in payload:
                    patch["completion_note"] = None
            else:
                raise RepositoryError(
                    422,
                    "task_progress_state_unsupported",
                    "严格任务合同当前只支持完成或重开，不保存伪进行中状态",
                )
        current_start = patch.get("scheduled_start_at", row["scheduled_start_at"])
        current_end = patch.get("scheduled_end_at", row["scheduled_end_at"])
        self._validate_schedule(_text(current_start), _text(current_end))
        owner_present = "ownerMembershipId" in payload or "ownerId" in payload
        collaborators_present = (
            "collaboratorMembershipIds" in payload or "collaboratorIds" in payload
        )
        if owner_present or collaborators_present:
            current_owner = connection.execute(
                "SELECT subject_membership_id FROM task_collaborators WHERE scope_id=? "
                "AND task_id=? AND role_key='owner' "
                "AND assignment_state IN ('assigned','returned') "
                "AND lifecycle_state='active' ORDER BY updated_at DESC, id LIMIT 1",
                (identity.scope_id, row["id"]),
            ).fetchone()
            owner_raw = payload.get("ownerMembershipId") if "ownerMembershipId" in payload else payload.get("ownerId")
            owner_id = (
                _text(owner_raw)
                if owner_present
                else _text(current_owner["subject_membership_id"] if current_owner else None)
            ) or identity.membership_id
            if collaborators_present:
                values = (
                    payload.get("collaboratorMembershipIds")
                    if "collaboratorMembershipIds" in payload
                    else payload.get("collaboratorIds")
                ) or []
                collaborator_ids = sorted(
                    {_text(item) for item in values if _text(item)} - {owner_id}
                )
            else:
                collaborator_ids = [
                    str(item["subject_membership_id"])
                    for item in connection.execute(
                        "SELECT subject_membership_id FROM task_collaborators "
                        "WHERE scope_id=? AND task_id=? AND role_key='collaborator' "
                        "AND lifecycle_state='active' "
                        "AND assignment_state IN ('assigned','awaiting_owner')",
                        (identity.scope_id, row["id"]),
                    ).fetchall()
                    if str(item["subject_membership_id"] or "") != owner_id
                ]
            self._active_members(connection, identity, [owner_id, *collaborator_ids])
            patch["owner_membership_id"] = owner_id
            patch["collaborator_membership_ids"] = sorted(set(collaborator_ids))
        return patch

    def _revalidate_normalized_patch(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
        patch: Mapping[str, Any],
    ) -> None:
        """Recheck a frozen GC-05 item immediately before its business write."""

        client_id = _text(patch.get("client_id", row["client_id"]))
        event_line_id = _text(patch.get("event_line_id", row["event_line_id"]))
        binding = validate_task_client_binding(
            connection,
            scope_id=identity.scope_id,
            client_id=client_id,
            event_line_id=event_line_id,
        )
        self._require_project_reference_access(
            connection, identity, client_id=binding.client_id
        )
        if "task_list_id" in patch:
            self._validate_list(connection, identity, _text(patch["task_list_id"]))
        if "planning_cycle_id" in patch:
            self._validate_planning_cycle(
                connection,
                identity,
                _text(patch["planning_cycle_id"]),
                binding.client_id,
            )
        self._validate_schedule(
            _text(patch.get("scheduled_start_at", row["scheduled_start_at"])),
            _text(patch.get("scheduled_end_at", row["scheduled_end_at"])),
        )
        if "owner_membership_id" in patch:
            self._active_members(
                connection,
                identity,
                [
                    str(patch["owner_membership_id"]),
                    *[str(item) for item in patch.get("collaborator_membership_ids") or []],
                ],
            )

    # ------------------------------------------------------------------
    # Projection payloads
    # ------------------------------------------------------------------
    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in row.keys()}

    def _collaborators(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_id: str,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT collaborator.*, principal.display_name
            FROM task_collaborators AS collaborator
            LEFT JOIN organization_memberships AS membership
              ON membership.scope_id=collaborator.scope_id
             AND membership.id=collaborator.subject_membership_id
            LEFT JOIN principals AS principal ON principal.id=membership.principal_id
            WHERE collaborator.scope_id=? AND collaborator.task_id=?
              AND collaborator.lifecycle_state='active'
              AND collaborator.assignment_state IN ('assigned','awaiting_owner','returned')
            ORDER BY CASE collaborator.role_key WHEN 'owner' THEN 0 ELSE 1 END,
                     CASE collaborator.assignment_state
                       WHEN 'assigned' THEN 0
                       WHEN 'awaiting_owner' THEN 1
                       ELSE 2
                     END,
                     collaborator.assigned_at, collaborator.id
            """,
            (identity.scope_id, task_id),
        ).fetchall()
        return [
            {
                **self._row_dict(row),
                "display_name": str(row["display_name"] or "未命名成员"),
            }
            for row in rows
        ]

    def _owner_departments_by_task(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_ids: Iterable[str],
    ) -> dict[str, list[dict[str, str]]]:
        ids = sorted({_text(item) for item in task_ids} - {None})
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""
            SELECT collaborator.task_id,
                   department.id AS department_id,
                   department.name AS department_name
            FROM task_collaborators AS collaborator
            LEFT JOIN organization_memberships AS assignment
              ON assignment.scope_id=collaborator.scope_id
             AND assignment.parent_membership_id=collaborator.subject_membership_id
             AND assignment.record_kind='department_assignment'
             AND assignment.status='active'
             AND assignment.lifecycle_state='active'
            LEFT JOIN organizations AS department
              ON department.id=assignment.department_id
             AND department.record_kind='department'
             AND department.lifecycle_state='active'
            WHERE collaborator.scope_id=?
              AND collaborator.task_id IN ({placeholders})
              AND collaborator.role_key='owner'
              AND collaborator.assignment_state='assigned'
              AND collaborator.inbox_status='accepted'
              AND collaborator.lifecycle_state='active'
            ORDER BY collaborator.task_id, department.name, department.id
            """,
            (identity.scope_id, *ids),
        ).fetchall()
        result: dict[str, list[dict[str, str]]] = {task_id: [] for task_id in ids}
        seen: dict[str, set[str]] = {task_id: set() for task_id in ids}
        for row in rows:
            task_id = str(row["task_id"] or "")
            department_id = str(row["department_id"] or "")
            if not task_id or not department_id or department_id in seen[task_id]:
                continue
            seen[task_id].add(department_id)
            result[task_id].append(
                {
                    "id": department_id,
                    "name": str(row["department_name"] or "未命名部门"),
                }
            )
        return result

    def _task_payload(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
        *,
        owner_departments_by_task: Mapping[str, list[dict[str, str]]] | None = None,
        event_line_detail: bool = False,
    ) -> dict[str, Any]:
        task = self._row_dict(row)
        creator = connection.execute(
            """
            SELECT principal.display_name
            FROM organization_memberships AS membership
            LEFT JOIN principals AS principal ON principal.id=membership.principal_id
            WHERE membership.scope_id=? AND membership.id=?
            """,
            (identity.scope_id, row["creator_membership_id"]),
        ).fetchone()
        task["creator_display_name"] = (
            str(creator["display_name"] or "") if creator is not None else ""
        )
        task["collaborators"] = self._collaborators(
            connection, identity, str(row["id"])
        )
        list_row = None
        if row["task_list_id"]:
            list_row = connection.execute(
                "SELECT * FROM task_lists WHERE id=? AND scope_id=?",
                (row["task_list_id"], identity.scope_id),
            ).fetchone()
        client = None
        if row["client_id"]:
            client = connection.execute(
                "SELECT id,name,lifecycle_state,version FROM clients "
                "WHERE id=? AND scope_id=?",
                (row["client_id"], identity.scope_id),
            ).fetchone()
        event_line = None
        if row["event_line_id"]:
            event_line = connection.execute(
                "SELECT id,name,client_id,version FROM event_lines "
                "WHERE id=? AND scope_id=? AND record_kind='line'",
                (row["event_line_id"], identity.scope_id),
            ).fetchone()
        task["list"] = self._row_dict(list_row) if list_row is not None else None
        task["client"] = self._row_dict(client) if client is not None else None
        task["event_line"] = self._row_dict(event_line) if event_line is not None else None
        attachment_rows = connection.execute(
            "SELECT id,display_name,media_type,byte_size,content_hash,"
            "availability_state,version,created_at,updated_at "
            "FROM source_assets WHERE scope_id=? AND source_kind='task_attachment_metadata' "
            "AND source_locator_nonlocal=? AND lifecycle_state='active' "
            "ORDER BY created_at,id",
            (identity.scope_id, f"task:{row['id']}"),
        ).fetchall()
        task["attachments"] = [
            {
                "id": str(item["id"]),
                "title": str(item["display_name"] or "任务附件"),
                "fileName": str(item["display_name"] or "任务附件"),
                "mediaType": str(item["media_type"] or "application/octet-stream"),
                "byteSize": int(item["byte_size"] or 0),
                "contentHash": str(item["content_hash"] or ""),
                "localAvailable": False,
                "sourceScope": "member_local_metadata",
                "isAudio": str(item["media_type"] or "").startswith("audio/"),
                "availabilityState": str(item["availability_state"] or "local_only"),
                "version": int(item["version"] or 1),
                "createdAt": item["created_at"],
                "updatedAt": item["updated_at"],
            }
            for item in attachment_rows
        ]
        task["tags"] = self._task_tags(connection, identity, str(row["id"]))
        task["viewer_inbox_status"] = next(
            (
                item["inbox_status"]
                for item in task["collaborators"]
                if item.get("subject_membership_id") == identity.membership_id
                and item.get("inbox_status") in {"pending", "accepted", "returned"}
            ),
            None,
        )
        task["viewer_role_key"] = next(
            (
                item["role_key"]
                for item in task["collaborators"]
                if item.get("subject_membership_id") == identity.membership_id
            ),
            None,
        )
        personal_surface_participant = any(
            item.get("subject_membership_id") == identity.membership_id
            and item.get("assignment_state") == "assigned"
            and item.get("role_key") in {"owner", "collaborator"}
            and (
                item.get("inbox_status") == "accepted"
                or (
                    item.get("role_key") == "collaborator"
                    and item.get("inbox_status") == "pending"
                )
            )
            for item in task["collaborators"]
        )
        pending_participant = any(
            item.get("subject_membership_id") == identity.membership_id
            and item.get("assignment_state") == "assigned"
            and item.get("inbox_status") == "pending"
            and item.get("role_key") in {"owner", "collaborator"}
            for item in task["collaborators"]
        )
        task["viewer_surfaces"] = {
            # 负责人待确认会阻塞正式分配；普通协作者的“待阅”只是通知确认，
            # 任务已分配后仍属于其个人列表/月历，不能因此造成日历数据消失。
            "personal_list": personal_surface_participant,
            "personal_calendar": personal_surface_participant,
            "collaboration_inbox": pending_participant,
            "event_line_detail": event_line_detail,
        }
        can_write = self._can_write_task(connection, identity, row)
        task["viewer_capabilities"] = {
            "can_view": True,
            "can_edit": can_write,
            "can_complete": can_write,
            "can_manage_collaborators": can_write,
            "can_track_time": self._can_track_task_time(
                connection, identity, row
            ),
        }
        task["task_timer"] = self._task_timer_summary(
            connection, identity, str(row["id"])
        )
        owner_departments = list(
            (owner_departments_by_task or {}).get(str(row["id"]), [])
        )
        task["owner_departments"] = owner_departments
        if len(owner_departments) == 1:
            task["owner_department_resolution"] = "resolved"
            task["owner_department_id"] = owner_departments[0]["id"]
            task["owner_department_name"] = owner_departments[0]["name"]
        elif owner_departments:
            task["owner_department_resolution"] = "ambiguous"
            task["owner_department_id"] = None
            task["owner_department_name"] = None
        else:
            task["owner_department_resolution"] = "unassigned"
            task["owner_department_id"] = None
            task["owner_department_name"] = None
        task["returned_to_creator"] = (
            str(row["creator_membership_id"] or "") == identity.membership_id
            and any(
                item.get("role_key") == "owner"
                and item.get("assignment_state") == "returned"
                and item.get("inbox_status") == "returned"
                for item in task["collaborators"]
            )
        )
        task["progress_status"] = "done" if row["completed_at"] else "todo"
        return task

    @staticmethod
    def _notification_result(*, requested_recipients: int) -> dict[str, Any]:
        return {
            "state": "not_connected",
            "requestedRecipients": requested_recipients,
            "deliveryCount": 0,
            "partialSuccess": False,
            "message": (
                "任务事实已保存；通知通道尚未接入，未伪造送达结果"
                if requested_recipients > 0
                else "本次没有待通知成员"
            ),
        }

    def _record_notification_intents(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        task_id: str,
        membership_ids: Iterable[str],
        now: str,
    ) -> dict[str, Any]:
        recipients = sorted(
            {
                str(value)
                for value in membership_ids
                if str(value or "") and str(value) != identity.membership_id
            }
        )
        for membership_id in recipients:
            delivery_id = "notify_" + sha256_text(
                f"{identity.scope_id}\x1f{task_id}\x1f{membership_id}\x1ffeishu"
            )[:30]
            recipient_hash = sha256_text(
                f"{identity.scope_id}\x1f{membership_id}"
            )
            connection.execute(
                """
                INSERT INTO notification_deliveries (
                    id,scope_id,external_side_effect_id,channel,remote_receipt,
                    status,recipient_ref_hash,sent_at,delivered_at,next_retry_at,
                    version,lifecycle_state,created_at,updated_at,deleted_at
                ) VALUES (?,?,NULL,'feishu',NULL,'blocked',?,NULL,NULL,NULL,1,
                          'active',?,?,NULL)
                ON CONFLICT(id) DO UPDATE SET status='blocked',
                    recipient_ref_hash=excluded.recipient_ref_hash,
                    version=notification_deliveries.version+1,
                    lifecycle_state='active',updated_at=excluded.updated_at,
                    deleted_at=NULL
                """,
                (delivery_id, identity.scope_id, recipient_hash, now, now),
            )
        result = self._notification_result(requested_recipients=len(recipients))
        return {
            **result,
            "deliveryRecordCount": len(recipients),
            "state": "not_connected" if recipients else "not_requested",
        }

    def _projection_for_tasks(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_ids: Iterable[str],
    ) -> dict[str, list[dict[str, Any]]]:
        ids = sorted({_text(item) for item in task_ids} - {None})
        if not ids:
            return {
                "tasks": [],
                "task_collaborators": [],
                "calendar_entries": [],
                "execution_runs": [],
            }
        placeholders = ",".join("?" for _ in ids)
        tasks = connection.execute(
            f"SELECT * FROM tasks WHERE scope_id=? AND id IN ({placeholders})",
            (identity.scope_id, *ids),
        ).fetchall()
        collaborators = connection.execute(
            f"SELECT * FROM task_collaborators WHERE scope_id=? AND task_id IN ({placeholders})",
            (identity.scope_id, *ids),
        ).fetchall()
        calendar = connection.execute(
            f"SELECT * FROM calendar_entries WHERE scope_id=? AND task_id IN ({placeholders})",
            (identity.scope_id, *ids),
        ).fetchall()
        focus_runs = connection.execute(
            f"SELECT * FROM execution_runs WHERE scope_id=? "
            f"AND task_id IN ({placeholders}) AND initiator_membership_id=? "
            "AND run_kind=? AND lifecycle_state='active'",
            (
                identity.scope_id,
                *ids,
                identity.membership_id,
                TASK_FOCUS_RUN_KIND,
            ),
        ).fetchall()
        return {
            "tasks": [self._row_dict(row) for row in tasks],
            "task_collaborators": [self._row_dict(row) for row in collaborators],
            "calendar_entries": [self._row_dict(row) for row in calendar],
            "execution_runs": [self._row_dict(row) for row in focus_runs],
        }

    def _rebuild_task_calendar(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
        *,
        now: str,
    ) -> None:
        connection.execute(
            "UPDATE calendar_entries SET invalidated_at=? WHERE scope_id=? "
            "AND target_kind='task' AND task_id=? AND invalidated_at IS NULL",
            (now, identity.scope_id, row["id"]),
        )
        starts_at = _text(row["scheduled_start_at"] or row["due_date"])
        if str(row["lifecycle_state"]) == "deleted" or starts_at is None:
            return
        version = int(row["version"] or 1)
        connection.execute(
            """
            INSERT INTO calendar_entries (
                id, scope_id, task_id, meeting_id, starts_at, version,
                target_kind, ends_at, timezone, display_state, source_version,
                generated_at, invalidated_at
            ) VALUES (?, ?, ?, NULL, ?, ?, 'task', ?, NULL, ?, ?, ?, NULL)
            """,
            (
                _stable_id("cal_task", identity.scope_id, str(row["id"]), str(version)),
                identity.scope_id,
                row["id"],
                starts_at,
                version,
                _text(row["scheduled_end_at"]),
                "completed" if row["completed_at"] else "active",
                version,
                now,
            ),
        )

    def _replace_collaborators(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        task_id: str,
        owner_membership_id: str,
        collaborator_membership_ids: Iterable[str],
        creator_membership_id: str,
        now: str,
    ) -> int:
        desired = {(owner_membership_id, "owner")}
        desired.update((item, "collaborator") for item in collaborator_membership_ids)
        active = connection.execute(
            "SELECT * FROM task_collaborators WHERE scope_id=? AND task_id=? "
            "AND lifecycle_state='active'",
            (identity.scope_id, task_id),
        ).fetchall()
        active_by_key = {
            (str(row["subject_membership_id"] or ""), str(row["role_key"] or "")): row
            for row in active
        }
        current_owner = active_by_key.get((owner_membership_id, "owner"))
        owner_accepted = owner_membership_id == creator_membership_id or bool(
            current_owner is not None
            and str(current_owner["assignment_state"] or "") == "assigned"
            and str(current_owner["inbox_status"] or "") == "accepted"
        )
        changed = 0
        for row in active:
            key = (str(row["subject_membership_id"] or ""), str(row["role_key"] or ""))
            if key in desired:
                continue
            connection.execute(
                "UPDATE task_collaborators SET assignment_state='removed', "
                "lifecycle_state='deleted', deleted_at=?, version=COALESCE(version,1)+1, "
                "updated_at=? WHERE id=? AND scope_id=?",
                (now, now, row["id"], identity.scope_id),
            )
            changed += 1
        for membership_id, role_key in sorted(desired):
            collaborator_id = _stable_id(
                "task_member", identity.scope_id, task_id, membership_id, role_key
            )
            existing = active_by_key.get((membership_id, role_key))
            if existing is None:
                existing = connection.execute(
                    "SELECT * FROM task_collaborators WHERE id=? AND scope_id=?",
                    (collaborator_id, identity.scope_id),
                ).fetchone()
            if role_key == "owner":
                assignment_state = "assigned"
                inbox_status = "accepted" if owner_accepted else "pending"
            else:
                assignment_state = "assigned" if owner_accepted else "awaiting_owner"
                inbox_status = (
                    "accepted"
                    if owner_accepted
                    and existing is not None
                    and str(existing["assignment_state"] or "") == "assigned"
                    and str(existing["inbox_status"] or "") == "accepted"
                    else "pending"
                )
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO task_collaborators (
                        id, scope_id, task_id, subject_principal_id,
                        subject_membership_id, role_key, assignment_state,
                        inbox_status, assigned_at, responded_at, version,
                        lifecycle_state, created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 1,
                              'active', ?, ?, NULL)
                    """,
                    (
                        collaborator_id, identity.scope_id, task_id, membership_id,
                        role_key, assignment_state, inbox_status, now,
                        now if inbox_status == "accepted" else None, now, now,
                    ),
                )
            else:
                already_current = (
                    str(existing["assignment_state"] or "") == assignment_state
                    and str(existing["inbox_status"] or "") == inbox_status
                    and str(existing["lifecycle_state"] or "") == "active"
                )
                if already_current:
                    continue
                connection.execute(
                    "UPDATE task_collaborators SET assignment_state=?, "
                    "inbox_status=?, assigned_at=?, responded_at=?, "
                    "version=COALESCE(version,1)+1, lifecycle_state='active', "
                    "updated_at=?, deleted_at=NULL WHERE id=? AND scope_id=?",
                    (
                        assignment_state, inbox_status, now,
                        now if inbox_status == "accepted" else None,
                        now, collaborator_id, identity.scope_id,
                    ),
                )
            changed += 1
        return changed

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    def board(self, identity: SessionIdentity) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            task_rows = connection.execute(
                "SELECT * FROM tasks WHERE scope_id=? AND lifecycle_state!='deleted' "
                "ORDER BY updated_at DESC, id",
                (identity.scope_id,),
            ).fetchall()
            visible = [
                row for row in task_rows if self._can_read_task(connection, identity, row)
            ]
            lists = connection.execute(
                "SELECT * FROM task_lists WHERE scope_id=? AND lifecycle_state!='deleted' "
                "AND (visibility_scope='organization' OR owner_membership_id=?) "
                "ORDER BY COALESCE(sort_order,0), name, id",
                (identity.scope_id, identity.membership_id),
            ).fetchall()
            views = connection.execute(
                "SELECT * FROM task_views WHERE scope_id=? AND lifecycle_state!='deleted' "
                "AND (record_kind!='view' OR viewer_membership_id=?) "
                "ORDER BY created_at, id",
                (identity.scope_id, identity.membership_id),
            ).fetchall()
            tags: list[dict[str, Any]] = []
            for row in views:
                if str(row["record_kind"] or "") != "tag":
                    continue
                resource = connection.execute(
                    "SELECT resource_type_key FROM secured_resources WHERE id=? AND scope_id=?",
                    (row["id"], identity.scope_id),
                ).fetchone()
                scope_kind = (
                    "personal"
                    if resource is not None and str(resource["resource_type_key"] or "") == "task_tag_personal"
                    else "organization"
                )
                if (
                    scope_kind == "personal"
                    and str(row["assigned_by_membership_id"] or "") != identity.membership_id
                    and not identity.is_admin
                ):
                    continue
                tag = self._row_dict(row)
                tag["scope_kind"] = scope_kind
                tags.append(tag)
            calendar = connection.execute(
                "SELECT * FROM calendar_entries WHERE scope_id=? AND target_kind='task' "
                "AND invalidated_at IS NULL ORDER BY starts_at, id",
                (identity.scope_id,),
            ).fetchall()
            visible_ids = {str(row["id"]) for row in visible}
            owner_departments_by_task = self._owner_departments_by_task(
                connection, identity, visible_ids
            )
            task_payloads = [
                self._task_payload(
                    connection,
                    identity,
                    row,
                    owner_departments_by_task=owner_departments_by_task,
                )
                for row in visible
            ]
            # 待接收协作任务必须只进入协作收件箱。任务本体仍随 board
            # 返回，供收件箱呈现和接受/退回；但在当前成员接受前，不得进入
            # 常规清单或日历投影。接受后仍复用同一 tasks 权威行和 task_id。
            standard_view_ids = {
                str(task["id"])
                for task in task_payloads
                if bool((task.get("viewer_surfaces") or {}).get("personal_calendar"))
            }
            calendar = [
                row
                for row in calendar
                if str(row["task_id"] or "") in standard_view_ids
            ]
            projection = self._projection_for_tasks(connection, identity, visible_ids)
            projection["task_lists"] = [self._row_dict(row) for row in lists]
            projection["task_views"] = [self._row_dict(row) for row in views]
            projection["calendar_entries"] = [self._row_dict(row) for row in calendar]
            return {
                "tasks": task_payloads,
                "taskLists": [self._row_dict(row) for row in lists],
                "taskViews": [self._row_dict(row) for row in views],
                "taskTags": tags,
                "calendarEntries": [self._row_dict(row) for row in calendar],
                "projection": projection,
                "viewerProjectionContract": dict(TASK_VIEW_PROJECTION_CONTRACT),
                "generatedAt": utc_now(),
            }

    def task_detail(self, identity: SessionIdentity, *, task_id: str) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            row = self._require_task_read(connection, identity, task_id)
            return {
                "task": self._task_payload(connection, identity, row),
                "projection": self._projection_for_tasks(connection, identity, [task_id]),
            }

    def task_context(self, identity: SessionIdentity, *, task_id: str) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            row = self._require_task_read(connection, identity, task_id)
            task = self._task_payload(connection, identity, row)
        client_id = _text(task.get("client_id"))
        if client_id is None:
            return {
                "taskId": task_id,
                "clientId": None,
                "state": "ready",
                "organizationProjectKnowledge": [],
                "personalProjectMemory": [],
                "materialBoundary": {},
                "taskPlanAgent": {
                    "state": "not_connected",
                    "canWriteTask": False,
                    "proposalRequired": True,
                    "message": "组织任务没有项目背景；任务CRUD仍可正常使用",
                },
                "generatedAt": utc_now(),
            }
        knowledge = self.repository.project_knowledge_context(
            identity, project_id=client_id
        )
        saved = list(knowledge.get("savedMemories") or [])
        formal_memory_kinds = {
            "answer_remember", "answer_correction", "strategic_profile_clarification"
        }
        formal_memory = [
            item for item in saved if str(item.get("sourceKind") or "") in formal_memory_kinds
        ]
        personal = [item for item in saved if item not in formal_memory]
        organization = [
            *list(knowledge.get("organizationSharedKnowledge") or []),
            *list(knowledge.get("officialWebsiteFacts") or []),
            *formal_memory,
        ]
        return {
            "taskId": task_id,
            "clientId": client_id,
            "state": str(knowledge.get("state") or "ready"),
            "organizationProjectKnowledge": organization,
            "personalProjectMemory": personal,
            "relationshipCards": list(knowledge.get("relationshipCards") or []),
            "materialBoundary": dict(knowledge.get("materialBoundary") or {}),
            "taskPlanAgent": {
                "state": "not_connected",
                "canWriteTask": False,
                "proposalRequired": True,
                "message": "任务计划Agent尚未接入执行运行；背景读取可用，任务变化必须走可见提案或正式命令",
            },
            "generatedAt": utc_now(),
        }

    # ------------------------------------------------------------------
    # GC-04 task commands
    # ------------------------------------------------------------------
    def create_task(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                normalized = self._normalize_create(connection, identity, payload)
                payload_hash = _payload_hash(normalized)
                replay = self._receipt(
                    connection, identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                task_id = new_id()
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO secured_resources (
                        id, scope_id, resource_kind, lifecycle_state, version,
                        resource_type_key, created_at, updated_at, deleted_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, 'task', 'active', 1, 'task', ?, ?, NULL,
                              'cloud', ?)
                    """,
                    (task_id, identity.scope_id, now, now, identity.cloud_instance_id),
                )
                connection.execute(
                    """
                    INSERT INTO tasks (
                        id, scope_id, creator_principal_id, creator_membership_id,
                        task_list_id, client_id, event_line_id, planning_cycle_id, lifecycle_state,
                        version, title, description, priority, task_kind,
                        visibility_scope, due_date, scheduled_start_at,
                        scheduled_end_at, duration_minutes, completion_note,
                        completed_at, source_type, source_id, archived_at,
                        created_at, updated_at, deleted_at
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'active', 1, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, ?, ?, NULL)
                    """,
                    (
                        task_id, identity.scope_id, identity.membership_id,
                        normalized["task_list_id"], normalized["client_id"],
                        normalized["event_line_id"], normalized["planning_cycle_id"], normalized["title"],
                        normalized["description"], normalized["priority"],
                        normalized["task_kind"], normalized["visibility_scope"],
                        normalized["due_date"], normalized["scheduled_start_at"],
                        normalized["scheduled_end_at"], normalized["duration_minutes"],
                        normalized["source_type"], normalized["source_id"], now, now,
                    ),
                )
                self._replace_collaborators(
                    connection, identity, task_id=task_id,
                    owner_membership_id=normalized["owner_membership_id"],
                    collaborator_membership_ids=normalized["collaborator_membership_ids"],
                    creator_membership_id=identity.membership_id,
                    now=now,
                )
                self._replace_task_tags(
                    connection,
                    identity,
                    task_id=task_id,
                    tag_ids=normalized["tag_ids"],
                    now=now,
                )
                row = self._task_row(connection, identity, task_id)
                self._rebuild_task_calendar(connection, identity, row, now=now)
                notification = self._record_notification_intents(
                    connection,
                    identity,
                    task_id=task_id,
                    membership_ids=[
                        str(item["subject_membership_id"])
                        for item in self._collaborators(connection, identity, task_id)
                        if item.get("assignment_state") == "assigned"
                        and item.get("inbox_status") == "pending"
                    ],
                    now=now,
                )
                result = {
                    "task": self._task_payload(connection, identity, row),
                    "notificationResult": notification,
                    "projection": self._projection_for_tasks(connection, identity, [task_id]),
                    "planLink": None,
                }
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task.created",
                    aggregate_type="task", aggregate_id=task_id,
                    aggregate_version=1, expected_version=None, result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _apply_patch(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        row: sqlite3.Row,
        patch: Mapping[str, Any],
        expected_version: int,
        now: str,
    ) -> sqlite3.Row:
        assignments: list[str] = []
        values: list[Any] = []
        collaborator_patch = {
            key: patch[key]
            for key in ("owner_membership_id", "collaborator_membership_ids", "tag_ids")
            if key in patch
        }
        for column, value in patch.items():
            if column in collaborator_patch:
                continue
            assignments.append(f"{column}=?")
            values.append(value)
        assignments.extend(["version=version+1", "updated_at=?"])
        values.extend([now, row["id"], identity.scope_id, expected_version])
        updated = connection.execute(
            f"UPDATE tasks SET {', '.join(assignments)} "
            "WHERE id=? AND scope_id=? AND version=? AND lifecycle_state!='deleted'",
            tuple(values),
        )
        if updated.rowcount != 1:
            raise RepositoryError(409, "task_version_conflict", "任务已更新，请刷新后重试")
        if patch.get("completed_at") and not row["completed_at"]:
            # Completion is the terminal boundary of the current work session.
            # Close every participant's running focus segment in the same
            # transaction so an app/device that has not refreshed cannot keep
            # accumulating time after the task is complete.
            connection.execute(
                "UPDATE execution_runs SET status='paused',finished_at=?,"
                "version=version+1,updated_at=? WHERE scope_id=? AND task_id=? "
                "AND run_kind=? AND status='running' AND lifecycle_state='active'",
                (
                    now,
                    now,
                    identity.scope_id,
                    row["id"],
                    TASK_FOCUS_RUN_KIND,
                ),
            )
        if collaborator_patch:
            if "owner_membership_id" in collaborator_patch:
                owner_id = str(collaborator_patch["owner_membership_id"])
                collaborator_ids = list(collaborator_patch["collaborator_membership_ids"])
                self._replace_collaborators(
                    connection, identity, task_id=str(row["id"]),
                    owner_membership_id=owner_id,
                    collaborator_membership_ids=collaborator_ids,
                    creator_membership_id=str(
                        row["creator_membership_id"] or identity.membership_id
                    ),
                    now=now,
                )
            if "tag_ids" in collaborator_patch:
                self._replace_task_tags(
                    connection,
                    identity,
                    task_id=str(row["id"]),
                    tag_ids=collaborator_patch["tag_ids"],
                    now=now,
                )
        current = self._task_row(connection, identity, str(row["id"]))
        self._rebuild_task_calendar(connection, identity, current, now=now)
        return current

    def update_task(
        self,
        identity: SessionIdentity,
        *,
        task_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        expected = self._require_expected_version(payload)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._require_task_write(connection, identity, task_id)
                patch = self._normalize_patch(connection, identity, row, payload)
                normalized = {"taskId": task_id, "expectedVersion": expected, "patch": patch}
                payload_hash = _payload_hash(normalized)
                replay = self._receipt(
                    connection, identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                if not patch:
                    raise RepositoryError(422, "task_patch_empty", "没有可保存的任务变化")
                if int(row["version"] or 1) != expected:
                    raise RepositoryError(409, "task_version_conflict", "任务已更新，请刷新后重试")
                now = utc_now()
                current = self._apply_patch(
                    connection, identity, row=row, patch=patch,
                    expected_version=expected, now=now,
                )
                collaborators_changed = "owner_membership_id" in patch
                notification = (
                    self._record_notification_intents(
                        connection,
                        identity,
                        task_id=task_id,
                        membership_ids=[
                            str(item["subject_membership_id"])
                            for item in self._collaborators(connection, identity, task_id)
                            if item.get("assignment_state") == "assigned"
                            and item.get("inbox_status") == "pending"
                        ],
                        now=now,
                    )
                    if collaborators_changed
                    else self._notification_result(requested_recipients=0)
                )
                result = {
                    "task": self._task_payload(connection, identity, current),
                    "notificationResult": notification,
                    "projection": self._projection_for_tasks(connection, identity, [task_id]),
                }
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task.updated",
                    aggregate_type="task", aggregate_id=task_id,
                    aggregate_version=int(current["version"]),
                    expected_version=expected, result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def update_task_timer(
        self,
        identity: SessionIdentity,
        *,
        task_id: str,
        action: str,
        expected_timer_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"start", "pause", "stop"}:
            raise RepositoryError(422, "task_timer_action_invalid", "计时操作无效")
        if expected_timer_version < 0:
            raise RepositoryError(422, "task_timer_version_invalid", "计时版本无效")
        normalized = {
            "taskId": task_id,
            "action": normalized_action,
            "expectedTimerVersion": expected_timer_version,
        }
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                task_row = self._require_task_read(connection, identity, task_id)
                if not self._can_track_task_time(connection, identity, task_row):
                    raise RepositoryError(
                        403,
                        "task_timer_forbidden",
                        "只有任务创建人或已接受任务的参与成员可以计时",
                    )
                now = utc_now()
                current_timer = self._task_timer_summary(
                    connection, identity, task_id, observed_at=now
                )
                current_version = int(current_timer["version"] or 0)
                current_state = str(current_timer["state"] or "idle")
                action_already_applied = (
                    (normalized_action == "start" and current_state == "running")
                    or (normalized_action == "pause" and current_state == "paused")
                    or (normalized_action == "stop" and current_state == "stopped")
                )
                if current_version != expected_timer_version and not action_already_applied:
                    raise RepositoryError(
                        409,
                        "task_timer_version_conflict",
                        "任务计时状态已变化，请按当前状态继续操作",
                    )
                if action_already_applied:
                    pass
                elif normalized_action == "start":
                    if task_row["completed_at"]:
                        raise RepositoryError(
                            409,
                            "task_timer_completed",
                            "已完成任务不能重新开始计时",
                        )
                    latest_created_row = connection.execute(
                        "SELECT MAX(created_at) AS created_at FROM execution_runs "
                        "WHERE scope_id=? AND task_id=? AND initiator_membership_id=? "
                        "AND run_kind=? AND lifecycle_state='active'",
                        (
                            identity.scope_id,
                            task_id,
                            identity.membership_id,
                            TASK_FOCUS_RUN_KIND,
                        ),
                    ).fetchone()
                    latest_created = self._parse_timer_timestamp(
                        latest_created_row["created_at"] if latest_created_row else None
                    )
                    started_at = self._parse_timer_timestamp(now) or datetime.now(
                        timezone.utc
                    )
                    if latest_created is not None and started_at <= latest_created:
                        # utc_now is millisecond-based. Keep focus segments strictly
                        # ordered even when pause/resume lands in the same millisecond.
                        started_at = latest_created + timedelta(milliseconds=1)
                    now = started_at.isoformat(timespec="milliseconds").replace(
                        "+00:00", "Z"
                    )
                    connection.execute(
                        """
                        INSERT INTO execution_runs (
                            id,scope_id,bot_id,rule_id,task_id,operation_id,status,
                            initiator_membership_id,proposal_id,run_kind,
                            progress_object_manifest_id,result_object_manifest_id,
                            started_at,finished_at,version,lifecycle_state,
                            created_at,updated_at,deleted_at
                        ) VALUES (?,?,NULL,NULL,?,NULL,'running',?,NULL,?,
                                  NULL,NULL,?,NULL,1,'active',?,?,NULL)
                        """,
                        (
                            new_id(),
                            identity.scope_id,
                            task_id,
                            identity.membership_id,
                            TASK_FOCUS_RUN_KIND,
                            now,
                            now,
                            now,
                        ),
                    )
                else:
                    latest_run_id = str(current_timer.get("latestRunId") or "")
                    expected_state = "running" if normalized_action == "pause" else None
                    if not latest_run_id or current_state == "idle":
                        raise RepositoryError(
                            409,
                            "task_timer_not_started",
                            "该任务尚未开始计时",
                        )
                    if expected_state and current_state != expected_state:
                        raise RepositoryError(
                            409,
                            "task_timer_not_running",
                            "只有正在计时的任务可以暂停",
                        )
                    if normalized_action == "stop" and current_state not in {"running", "paused"}:
                        raise RepositoryError(
                            409,
                            "task_timer_not_active",
                            "当前没有可停止的任务计时",
                        )
                    next_status = "paused" if normalized_action == "pause" else "stopped"
                    finished_at = now if current_state == "running" else None
                    latest_run = connection.execute(
                        "SELECT version FROM execution_runs WHERE id=? AND scope_id=? "
                        "AND task_id=? AND initiator_membership_id=? AND run_kind=? "
                        "AND lifecycle_state='active'",
                        (
                            latest_run_id,
                            identity.scope_id,
                            task_id,
                            identity.membership_id,
                            TASK_FOCUS_RUN_KIND,
                        ),
                    ).fetchone()
                    if latest_run is None:
                        raise RepositoryError(
                            409,
                            "task_timer_version_conflict",
                            "任务计时状态已变化，请按当前状态继续操作",
                        )
                    cursor = connection.execute(
                        "UPDATE execution_runs SET status=?,"
                        "finished_at=COALESCE(?,finished_at),version=version+1,updated_at=? "
                        "WHERE id=? AND scope_id=? AND task_id=? "
                        "AND initiator_membership_id=? AND run_kind=? "
                        "AND version=? AND lifecycle_state='active'",
                        (
                            next_status,
                            finished_at,
                            now,
                            latest_run_id,
                            identity.scope_id,
                            task_id,
                            identity.membership_id,
                            TASK_FOCUS_RUN_KIND,
                            int(latest_run["version"] or 1),
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "task_timer_version_conflict",
                            "任务计时状态已变化，请按当前状态继续操作",
                        )
                timer = self._task_timer_summary(
                    connection, identity, task_id, observed_at=now
                )
                task_payload = self._task_payload(connection, identity, task_row)
                task_payload["task_timer"] = timer
                result = {
                    "task": task_payload,
                    "taskTimer": timer,
                    "projection": self._projection_for_tasks(
                        connection, identity, [task_id]
                    ),
                }
                self._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type=f"task.timer_{normalized_action}",
                    aggregate_type="task_timer",
                    aggregate_id=task_id,
                    aggregate_version=int(timer["version"] or 1),
                    expected_version=expected_timer_version or None,
                    result=result,
                    now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def pause_running_task_timers(
        self,
        identity: SessionIdentity,
        *,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_reason = str(reason or "app_closed").strip()[:80] or "app_closed"
        normalized = {
            "membershipId": identity.membership_id,
            "reason": normalized_reason,
        }
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                now = utc_now()
                rows = connection.execute(
                    "SELECT id,task_id FROM execution_runs WHERE scope_id=? "
                    "AND initiator_membership_id=? AND run_kind=? "
                    "AND status='running' AND lifecycle_state='active' "
                    "ORDER BY created_at,id",
                    (
                        identity.scope_id,
                        identity.membership_id,
                        TASK_FOCUS_RUN_KIND,
                    ),
                ).fetchall()
                run_ids = [str(row["id"]) for row in rows]
                task_ids = sorted({str(row["task_id"]) for row in rows})
                if run_ids:
                    placeholders = ",".join("?" for _ in run_ids)
                    connection.execute(
                        f"UPDATE execution_runs SET status='paused',finished_at=?,"
                        f"version=version+1,updated_at=? WHERE id IN ({placeholders}) "
                        "AND scope_id=? AND initiator_membership_id=? "
                        "AND run_kind=? AND status='running' "
                        "AND lifecycle_state='active'",
                        (
                            now,
                            now,
                            *run_ids,
                            identity.scope_id,
                            identity.membership_id,
                            TASK_FOCUS_RUN_KIND,
                        ),
                    )
                version_row = connection.execute(
                    "SELECT COALESCE(SUM(version),0) AS version FROM execution_runs "
                    "WHERE scope_id=? AND initiator_membership_id=? AND run_kind=? "
                    "AND lifecycle_state='active'",
                    (
                        identity.scope_id,
                        identity.membership_id,
                        TASK_FOCUS_RUN_KIND,
                    ),
                ).fetchone()
                aggregate_version = int(version_row["version"] or 0)
                result = {
                    "state": "paused",
                    "pausedCount": len(run_ids),
                    "taskIds": task_ids,
                    "reason": normalized_reason,
                    "observedAt": now,
                }
                self._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="task.timer_pause_running",
                    aggregate_type="task_timer_session",
                    aggregate_id=identity.membership_id,
                    aggregate_version=aggregate_version,
                    expected_version=None,
                    result=result,
                    now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def delete_task(
        self,
        identity: SessionIdentity,
        *,
        task_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = {"taskId": task_id, "expectedVersion": expected_version}
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection, identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                row = self._require_task_write(connection, identity, task_id)
                if int(row["version"] or 1) != expected_version:
                    raise RepositoryError(409, "task_version_conflict", "任务已更新，请刷新后重试")
                now = utc_now()
                notification_members = [
                    str(item["subject_membership_id"])
                    for item in connection.execute(
                        "SELECT subject_membership_id FROM task_collaborators "
                        "WHERE scope_id=? AND task_id=? AND lifecycle_state='active'",
                        (identity.scope_id, task_id),
                    ).fetchall()
                ]
                cursor = connection.execute(
                    "UPDATE tasks SET lifecycle_state='deleted', deleted_at=?, "
                    "version=version+1, updated_at=? WHERE id=? AND scope_id=? "
                    "AND version=? AND lifecycle_state!='deleted'",
                    (now, now, task_id, identity.scope_id, expected_version),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(409, "task_version_conflict", "任务已更新，请刷新后重试")
                connection.execute(
                    "UPDATE secured_resources SET lifecycle_state='deleted', deleted_at=?, "
                    "version=COALESCE(version,1)+1, updated_at=? WHERE id=? AND scope_id=?",
                    (now, now, task_id, identity.scope_id),
                )
                connection.execute(
                    "UPDATE task_collaborators SET lifecycle_state='deleted', deleted_at=?, "
                    "assignment_state='removed', version=COALESCE(version,1)+1, updated_at=? "
                    "WHERE scope_id=? AND task_id=? AND lifecycle_state='active'",
                    (now, now, identity.scope_id, task_id),
                )
                for membership_id in notification_members:
                    delivery_id = "notify_" + sha256_text(
                        f"{identity.scope_id}\x1f{task_id}\x1f{membership_id}\x1ffeishu"
                    )[:30]
                    connection.execute(
                        "UPDATE notification_deliveries SET lifecycle_state='deleted',"
                        "deleted_at=?,version=version+1,updated_at=? "
                        "WHERE id=? AND scope_id=? AND lifecycle_state!='deleted'",
                        (now, now, delivery_id, identity.scope_id),
                    )
                current = self._task_row(connection, identity, task_id, include_deleted=True)
                self._rebuild_task_calendar(connection, identity, current, now=now)
                result = {
                    "deleted": True,
                    "taskId": task_id,
                    "version": int(current["version"]),
                    "affectedDecisionActionIds": [],
                    "projection": self._projection_for_tasks(connection, identity, [task_id]),
                }
                operation_id, _ = self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task.deleted",
                    aggregate_type="task", aggregate_id=task_id,
                    aggregate_version=int(current["version"]),
                    expected_version=expected_version, result=result, now=now,
                )
                event_hash = sha256_text(
                    f"{operation_id}|task|{task_id}|active|deleted|{current['version']}"
                )
                connection.execute(
                    """
                    INSERT INTO lifecycle_events (
                        id, scope_id, operation_id, secured_resource_id,
                        from_state, to_state, tombstone_version, actor_id,
                        reason_code, occurred_at, origin_instance_id,
                        created_at, integrity_hash
                    ) VALUES (?, ?, ?, ?, 'active', 'deleted', ?, ?,
                              'user_delete', ?, ?, ?, ?)
                    """,
                    (
                        new_id(), identity.scope_id, operation_id, task_id,
                        int(current["version"]), identity.principal_id, now,
                        identity.cloud_instance_id, now, event_hash,
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def handle_inbox(
        self,
        identity: SessionIdentity,
        *,
        task_id: str,
        action: str,
        expected_version: int,
        reason: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if action not in {"accept", "return"}:
            raise RepositoryError(422, "task_inbox_action_invalid", "协作收件箱动作无效")
        normalized = {
            "taskId": task_id,
            "action": action,
            "expectedCollaboratorVersion": expected_version,
            "reason": _text(reason),
        }
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection, identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                task = self._require_task_read(connection, identity, task_id)
                collaborator = connection.execute(
                    "SELECT * FROM task_collaborators WHERE scope_id=? AND task_id=? "
                    "AND subject_membership_id=? AND lifecycle_state='active' "
                    "AND assignment_state='assigned' "
                    "AND inbox_status='pending' ORDER BY CASE role_key WHEN 'owner' THEN 0 ELSE 1 END, id LIMIT 1",
                    (identity.scope_id, task_id, identity.membership_id),
                ).fetchone()
                if collaborator is None:
                    raise RepositoryError(409, "task_inbox_not_pending", "该协作邀请已处理或不可用")
                current_version = int(collaborator["version"] or 1)
                if current_version != expected_version:
                    raise RepositoryError(409, "task_collaborator_version_conflict", "协作状态已变化，请刷新后重试")
                now = utc_now()
                next_status = "accepted" if action == "accept" else "returned"
                next_assignment = "assigned" if action == "accept" else "returned"
                cursor = connection.execute(
                    "UPDATE task_collaborators SET inbox_status=?, assignment_state=?, "
                    "responded_at=?, version=COALESCE(version,1)+1, updated_at=? "
                    "WHERE id=? AND scope_id=? AND version=? AND inbox_status='pending'",
                    (
                        next_status, next_assignment, now, now, collaborator["id"],
                        identity.scope_id, expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(409, "task_collaborator_version_conflict", "协作状态已变化，请刷新后重试")
                changed = connection.execute(
                    "SELECT * FROM task_collaborators WHERE id=?",
                    (collaborator["id"],),
                ).fetchone()
                notification_memberships: list[str] = []
                if str(collaborator["role_key"] or "") == "owner":
                    if action == "accept":
                        staged = connection.execute(
                            "SELECT subject_membership_id FROM task_collaborators "
                            "WHERE scope_id=? AND task_id=? AND role_key='collaborator' "
                            "AND assignment_state='awaiting_owner' "
                            "AND lifecycle_state='active' ORDER BY assigned_at,id",
                            (identity.scope_id, task_id),
                        ).fetchall()
                        connection.execute(
                            "UPDATE task_collaborators SET assignment_state='assigned', "
                            "version=COALESCE(version,1)+1, updated_at=? "
                            "WHERE scope_id=? AND task_id=? AND role_key='collaborator' "
                            "AND assignment_state='awaiting_owner' "
                            "AND lifecycle_state='active'",
                            (now, identity.scope_id, task_id),
                        )
                        notification_memberships = [
                            str(item["subject_membership_id"] or "")
                            for item in staged
                            if str(item["subject_membership_id"] or "")
                        ]
                    else:
                        # 负责人退回的业务去向始终是 tasks.creator_membership_id。
                        # 普通协作者继续被负责人闸门挡住，不提前看到任务。
                        connection.execute(
                            "UPDATE task_collaborators SET assignment_state='awaiting_owner', "
                            "inbox_status='pending', responded_at=NULL, "
                            "version=COALESCE(version,1)+1, updated_at=? "
                            "WHERE scope_id=? AND task_id=? AND role_key='collaborator' "
                            "AND lifecycle_state='active'",
                            (now, identity.scope_id, task_id),
                        )
                        creator_membership_id = str(task["creator_membership_id"] or "")
                        if creator_membership_id:
                            notification_memberships = [creator_membership_id]
                elif action == "return":
                    creator_membership_id = str(task["creator_membership_id"] or "")
                    if creator_membership_id:
                        notification_memberships = [creator_membership_id]
                result = {
                    "task": self._task_payload(connection, identity, task),
                    "collaborator": self._row_dict(changed),
                    "restoredOwnerCollaborator": None,
                    "notificationResult": self._record_notification_intents(
                        connection,
                        identity,
                        task_id=task_id,
                        membership_ids=notification_memberships,
                        now=now,
                    ),
                    "projection": self._projection_for_tasks(connection, identity, [task_id]),
                }
                command_type = f"task.inbox_{'accepted' if action == 'accept' else 'returned'}"
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type=command_type,
                    aggregate_type="task_collaborator", aggregate_id=str(collaborator["id"]),
                    aggregate_version=int(changed["version"] or 1),
                    expected_version=expected_version, result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def transfer_task(
        self,
        identity: SessionIdentity,
        *,
        task_id: str,
        target_membership_id: str,
        expected_owner_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        target = _text(target_membership_id)
        if target is None:
            raise RepositoryError(422, "task_transfer_target_required", "请选择新的负责人")
        normalized = {
            "taskId": task_id,
            "targetMembershipId": target,
            "expectedOwnerVersion": expected_owner_version,
        }
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection, identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                task = self._require_task_write(connection, identity, task_id)
                self._active_members(connection, identity, [target])
                owner = connection.execute(
                    "SELECT * FROM task_collaborators WHERE scope_id=? AND task_id=? "
                    "AND role_key='owner' AND assignment_state='assigned' "
                    "AND lifecycle_state='active' ORDER BY updated_at DESC, id LIMIT 1",
                    (identity.scope_id, task_id),
                ).fetchone()
                if owner is None:
                    raise RepositoryError(409, "task_owner_missing", "任务当前没有可转交负责人")
                if int(owner["version"] or 1) != expected_owner_version:
                    raise RepositoryError(409, "task_collaborator_version_conflict", "负责人状态已变化，请刷新后重试")
                if str(owner["subject_membership_id"] or "") == target:
                    raise RepositoryError(422, "task_transfer_target_same", "新负责人不能与当前负责人相同")
                now = utc_now()
                connection.execute(
                    "UPDATE task_collaborators SET assignment_state='transferred', "
                    "inbox_status='returned', responded_at=?, version=version+1, "
                    "updated_at=? WHERE id=? AND scope_id=? AND version=?",
                    (now, now, owner["id"], identity.scope_id, expected_owner_version),
                )
                owner_accepted = target == str(task["creator_membership_id"] or "")
                connection.execute(
                    "UPDATE task_collaborators SET assignment_state='removed', "
                    "lifecycle_state='deleted', deleted_at=?, "
                    "version=COALESCE(version,1)+1, updated_at=? "
                    "WHERE scope_id=? AND task_id=? AND role_key='collaborator' "
                    "AND subject_membership_id=? AND lifecycle_state='active'",
                    (now, now, identity.scope_id, task_id, target),
                )
                if owner_accepted:
                    connection.execute(
                        "UPDATE task_collaborators SET assignment_state='assigned', "
                        "version=COALESCE(version,1)+1, updated_at=? "
                        "WHERE scope_id=? AND task_id=? AND role_key='collaborator' "
                        "AND assignment_state='awaiting_owner' AND lifecycle_state='active'",
                        (now, identity.scope_id, task_id),
                    )
                else:
                    connection.execute(
                        "UPDATE task_collaborators SET assignment_state='awaiting_owner', "
                        "inbox_status='pending', responded_at=NULL, "
                        "version=COALESCE(version,1)+1, updated_at=? "
                        "WHERE scope_id=? AND task_id=? AND role_key='collaborator' "
                        "AND lifecycle_state='active'",
                        (now, identity.scope_id, task_id),
                    )
                new_owner_id = _stable_id(
                    "task_member", identity.scope_id, task_id, target, "owner"
                )
                existing = connection.execute(
                    "SELECT * FROM task_collaborators WHERE id=? AND scope_id=?",
                    (new_owner_id, identity.scope_id),
                ).fetchone()
                inbox_status = "accepted" if owner_accepted else "pending"
                if existing is None:
                    connection.execute(
                        "INSERT INTO task_collaborators (id,scope_id,task_id,"
                        "subject_principal_id,subject_membership_id,role_key,assignment_state,"
                        "inbox_status,assigned_at,responded_at,version,lifecycle_state,"
                        "created_at,updated_at,deleted_at) VALUES (?,?,?,NULL,?,'owner',"
                        "'assigned',?,?,?,1,'active',?,?,NULL)",
                        (
                            new_owner_id, identity.scope_id, task_id, target,
                            inbox_status, now, now if inbox_status == "accepted" else None,
                            now, now,
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE task_collaborators SET assignment_state='assigned',"
                        "inbox_status=?,assigned_at=?,responded_at=?,version=COALESCE(version,1)+1,"
                        "lifecycle_state='active',updated_at=?,deleted_at=NULL WHERE id=? AND scope_id=?",
                        (
                            inbox_status, now, now if inbox_status == "accepted" else None,
                            now, new_owner_id, identity.scope_id,
                        ),
                    )
                changed_owner = connection.execute(
                    "SELECT * FROM task_collaborators WHERE id=?",
                    (new_owner_id,),
                ).fetchone()
                result = {
                    "task": self._task_payload(connection, identity, task),
                    "ownerCollaborator": self._row_dict(changed_owner),
                    "notificationResult": self._notification_result(
                        requested_recipients=0 if target == identity.membership_id else 1
                    ),
                    "projection": self._projection_for_tasks(connection, identity, [task_id]),
                }
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task.owner_transferred",
                    aggregate_type="task_collaborator", aggregate_id=new_owner_id,
                    aggregate_version=int(changed_owner["version"] or 1),
                    expected_version=expected_owner_version, result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # Optional lists and task views
    # ------------------------------------------------------------------
    def create_list(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if payload.get("isDefault"):
            raise RepositoryError(422, "default_task_list_forbidden", "严格新版不创建默认收集箱")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise RepositoryError(422, "task_list_name_required", "请输入清单名称")
        visibility = "organization" if payload.get("scope") == "org" else "personal"
        normalized = {
            "name": name,
            "visibilityScope": visibility,
            "sortOrder": _integer(payload.get("sortOrder"), minimum=0),
        }
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection, identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                list_id = new_id()
                now = utc_now()
                connection.execute(
                    "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
                    "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?,'task_list','active',1,'task_list',?,?,NULL,"
                    "'cloud',?)",
                    (list_id, identity.scope_id, now, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "INSERT INTO task_lists (id,scope_id,owner_principal_id,owner_membership_id,"
                    "name,version,sort_order,visibility_scope,archived_at,lifecycle_state,"
                    "created_at,updated_at,deleted_at) VALUES (?,?,NULL,?,?,1,?,?,NULL,'active',?,?,NULL)",
                    (
                        list_id, identity.scope_id, identity.membership_id, name,
                        normalized["sortOrder"], visibility, now, now,
                    ),
                )
                row = connection.execute("SELECT * FROM task_lists WHERE id=?", (list_id,)).fetchone()
                result = {
                    "taskList": self._row_dict(row),
                    "colorPersisted": False,
                    "projection": {"task_lists": [self._row_dict(row)]},
                }
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task_list.created",
                    aggregate_type="task_list", aggregate_id=list_id,
                    aggregate_version=1, expected_version=None, result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def update_list(
        self,
        identity: SessionIdentity,
        *,
        list_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        expected = self._require_expected_version(payload)
        if payload.get("isDefault"):
            raise RepositoryError(422, "default_task_list_forbidden", "严格新版不创建默认收集箱")
        normalized = {
            "listId": list_id,
            "expectedVersion": expected,
            "name": str(payload.get("name") or "").strip() if "name" in payload else None,
            "sortOrder": _integer(payload.get("sortOrder"), minimum=0) if "sortOrder" in payload else None,
            "visibilityScope": (
                "organization" if payload.get("scope") == "org" else "personal"
            ) if "scope" in payload else None,
            "archived": bool(payload.get("archived")) if "archived" in payload else None,
        }
        if "name" in payload and not normalized["name"]:
            raise RepositoryError(422, "task_list_name_required", "请输入清单名称")
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                row = connection.execute(
                    "SELECT * FROM task_lists WHERE id=? AND scope_id=? AND lifecycle_state!='deleted'",
                    (list_id, identity.scope_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "task_list_missing", "任务清单不存在")
                if not identity.is_admin and str(row["owner_membership_id"] or "") != identity.membership_id:
                    raise RepositoryError(403, "task_list_forbidden", "当前成员无权修改该清单")
                if int(row["version"] or 1) != expected:
                    raise RepositoryError(409, "task_list_version_conflict", "任务清单已更新，请刷新后重试")
                assignments: list[str] = []
                values: list[Any] = []
                for key, column in (
                    ("name", "name"), ("sortOrder", "sort_order"),
                    ("visibilityScope", "visibility_scope"),
                ):
                    if normalized[key] is not None:
                        assignments.append(f"{column}=?")
                        values.append(normalized[key])
                if normalized["archived"] is not None:
                    assignments.extend(["lifecycle_state=?", "archived_at=?"])
                    values.extend([
                        "archived" if normalized["archived"] else "active",
                        utc_now() if normalized["archived"] else None,
                    ])
                if not assignments:
                    raise RepositoryError(422, "task_list_patch_empty", "没有可保存的清单变化")
                now = utc_now()
                assignments.extend(["version=version+1", "updated_at=?"])
                values.extend([now, list_id, identity.scope_id, expected])
                cursor = connection.execute(
                    f"UPDATE task_lists SET {', '.join(assignments)} WHERE id=? AND scope_id=? AND version=?",
                    tuple(values),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(409, "task_list_version_conflict", "任务清单已更新，请刷新后重试")
                current = connection.execute("SELECT * FROM task_lists WHERE id=?", (list_id,)).fetchone()
                result = {
                    "taskList": self._row_dict(current),
                    "colorPersisted": False,
                    "projection": {"task_lists": [self._row_dict(current)]},
                }
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task_list.updated",
                    aggregate_type="task_list", aggregate_id=list_id,
                    aggregate_version=int(current["version"]), expected_version=expected,
                    result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def delete_list(
        self,
        identity: SessionIdentity,
        *,
        list_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = {"listId": list_id, "expectedVersion": expected_version}
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                row = connection.execute(
                    "SELECT * FROM task_lists WHERE id=? AND scope_id=? AND lifecycle_state!='deleted'",
                    (list_id, identity.scope_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "task_list_missing", "任务清单不存在")
                if not identity.is_admin and str(row["owner_membership_id"] or "") != identity.membership_id:
                    raise RepositoryError(403, "task_list_forbidden", "当前成员无权删除该清单")
                if int(row["version"] or 1) != expected_version:
                    raise RepositoryError(409, "task_list_version_conflict", "任务清单已更新，请刷新后重试")
                now = utc_now()
                connection.execute(
                    "UPDATE task_lists SET lifecycle_state='deleted',deleted_at=?,version=version+1,"
                    "updated_at=? WHERE id=? AND scope_id=? AND version=?",
                    (now, now, list_id, identity.scope_id, expected_version),
                )
                affected_rows = connection.execute(
                    "SELECT * FROM tasks WHERE scope_id=? AND task_list_id=? AND lifecycle_state!='deleted'",
                    (identity.scope_id, list_id),
                ).fetchall()
                for task in affected_rows:
                    connection.execute(
                        "UPDATE tasks SET task_list_id=NULL,version=version+1,updated_at=? "
                        "WHERE id=? AND scope_id=? AND version=?",
                        (now, task["id"], identity.scope_id, task["version"]),
                    )
                    current_task = self._task_row(
                        connection, identity, str(task["id"])
                    )
                    self._rebuild_task_calendar(
                        connection, identity, current_task, now=now
                    )
                deleted_list = connection.execute(
                    "SELECT * FROM task_lists WHERE id=? AND scope_id=?",
                    (list_id, identity.scope_id),
                ).fetchone()
                projection = self._projection_for_tasks(
                    connection, identity, [str(item["id"]) for item in affected_rows]
                )
                projection["task_lists"] = [self._row_dict(deleted_list)]
                result = {
                    "deleted": True,
                    "taskListId": list_id,
                    "affectedTaskIds": [str(item["id"]) for item in affected_rows],
                    "projection": projection,
                }
                operation_id, manifest_id = self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task_list.deleted",
                    aggregate_type="task_list", aggregate_id=list_id,
                    aggregate_version=expected_version + 1, expected_version=expected_version,
                    result=result, now=now,
                )
                for task in affected_rows:
                    version = int(task["version"] or 1) + 1
                    event_hash = sha256_text(f"{operation_id}|task.list_cleared|{task['id']}|{version}")
                    connection.execute(
                        "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,"
                        "event_type,status,aggregate_type,aggregate_id,event_object_manifest_id,"
                        "event_hash,available_at,published_at,authority_role,origin_instance_id) "
                        "VALUES (?,?,?,?, 'task.list_cleared','pending','task',?,?,?, ?,NULL,'cloud',?)",
                        (
                            new_id(), identity.scope_id, operation_id, version, task["id"],
                            manifest_id, event_hash, now, identity.cloud_instance_id,
                        ),
                    )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def create_tag(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise RepositoryError(422, "task_tag_name_required", "请输入标签名称")
        color = str(payload.get("color") or "#5B7BFE").strip()
        personal = str(payload.get("scope") or "self") != "org"
        normalized = {"name": name, "color": color, "personal": personal}
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                tag_id = new_id()
                now = utc_now()
                connection.execute(
                    "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
                    "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?,'task_view','active',1,?,?,?,NULL,'cloud',?)",
                    (
                        tag_id, identity.scope_id,
                        "task_tag_personal" if personal else "task_tag_organization",
                        now, now, identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO task_views (id,scope_id,task_list_id,viewer_principal_id,"
                    "viewer_membership_id,filter_spec,version,record_kind,"
                    "filter_spec_schema_version,tag_name,tag_color,task_id,tag_id,"
                    "assigned_by_membership_id,lifecycle_state,created_at,updated_at,deleted_at) "
                    "VALUES (?,?,NULL,NULL,NULL,NULL,1,'tag',NULL,?,?,NULL,NULL,?,"
                    "'active',?,?,NULL)",
                    (
                        tag_id, identity.scope_id,
                        name, color, identity.membership_id, now, now,
                    ),
                )
                row = connection.execute("SELECT * FROM task_views WHERE id=?", (tag_id,)).fetchone()
                tag_payload = {**self._row_dict(row), "scope_kind": "personal" if personal else "organization"}
                result = {"taskTag": tag_payload, "projection": {"task_views": [self._row_dict(row)]}}
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task_tag.created",
                    aggregate_type="task_view", aggregate_id=tag_id,
                    aggregate_version=1, expected_version=None, result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def update_tag(
        self,
        identity: SessionIdentity,
        *,
        tag_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        expected = self._require_expected_version(payload)
        name = str(payload.get("name") or "").strip()
        color = str(payload.get("color") or "#5B7BFE").strip()
        if not name:
            raise RepositoryError(422, "task_tag_name_required", "请输入标签名称")
        normalized = {"tagId": tag_id, "name": name, "color": color, "expectedVersion": expected}
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                row = connection.execute(
                    "SELECT view.*,resource.resource_type_key FROM task_views view "
                    "JOIN secured_resources resource ON resource.id=view.id AND resource.scope_id=view.scope_id "
                    "WHERE view.id=? AND view.scope_id=? AND view.record_kind='tag' "
                    "AND view.lifecycle_state='active'", (tag_id, identity.scope_id)
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "task_tag_missing", "任务标签不存在")
                if str(row["resource_type_key"] or "") == "task_tag_personal" and str(row["assigned_by_membership_id"] or "") != identity.membership_id and not identity.is_admin:
                    raise RepositoryError(403, "task_tag_forbidden", "当前成员无权修改该标签")
                if int(row["version"] or 1) != expected:
                    raise RepositoryError(409, "task_tag_version_conflict", "任务标签已更新，请刷新后重试")
                now = utc_now()
                connection.execute(
                    "UPDATE task_views SET tag_name=?,tag_color=?,version=version+1,updated_at=? "
                    "WHERE id=? AND scope_id=? AND version=?",
                    (name, color, now, tag_id, identity.scope_id, expected),
                )
                current = connection.execute("SELECT * FROM task_views WHERE id=?", (tag_id,)).fetchone()
                tag_payload = {
                    **self._row_dict(current),
                    "scope_kind": "personal" if row["resource_type_key"] == "task_tag_personal" else "organization",
                }
                result = {"taskTag": tag_payload, "projection": {"task_views": [self._row_dict(current)]}}
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task_tag.updated",
                    aggregate_type="task_view", aggregate_id=tag_id,
                    aggregate_version=expected + 1, expected_version=expected,
                    result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def delete_tag(
        self,
        identity: SessionIdentity,
        *,
        tag_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = {"tagId": tag_id, "expectedVersion": expected_version}
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                row = connection.execute(
                    "SELECT view.*,resource.resource_type_key FROM task_views view "
                    "JOIN secured_resources resource ON resource.id=view.id AND resource.scope_id=view.scope_id "
                    "WHERE view.id=? AND view.scope_id=? AND view.record_kind='tag' "
                    "AND view.lifecycle_state='active'", (tag_id, identity.scope_id)
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "task_tag_missing", "任务标签不存在")
                if str(row["resource_type_key"] or "") == "task_tag_personal" and str(row["assigned_by_membership_id"] or "") != identity.membership_id and not identity.is_admin:
                    raise RepositoryError(403, "task_tag_forbidden", "当前成员无权删除该标签")
                if int(row["version"] or 1) != expected_version:
                    raise RepositoryError(409, "task_tag_version_conflict", "任务标签已更新，请刷新后重试")
                now = utc_now()
                connection.execute(
                    "UPDATE task_views SET lifecycle_state='deleted',deleted_at=?,version=version+1,updated_at=? "
                    "WHERE id=? AND scope_id=?", (now, now, tag_id, identity.scope_id)
                )
                connection.execute(
                    "UPDATE secured_resources SET lifecycle_state='deleted',deleted_at=?,"
                    "version=version+1,updated_at=? WHERE id=? AND scope_id=?",
                    (now, now, tag_id, identity.scope_id),
                )
                assignment_ids = [
                    str(item["id"])
                    for item in connection.execute(
                        "SELECT id FROM task_views WHERE scope_id=? "
                        "AND record_kind='tag_assignment' AND tag_id=? "
                        "AND lifecycle_state='active'",
                        (identity.scope_id, tag_id),
                    ).fetchall()
                ]
                connection.execute(
                    "UPDATE task_views SET lifecycle_state='deleted',deleted_at=?,version=version+1,updated_at=? "
                    "WHERE scope_id=? AND record_kind='tag_assignment' AND tag_id=? "
                    "AND lifecycle_state='active'", (now, now, identity.scope_id, tag_id)
                )
                for assignment_id in assignment_ids:
                    connection.execute(
                        "UPDATE secured_resources SET lifecycle_state='deleted',deleted_at=?,"
                        "version=version+1,updated_at=? WHERE id=? AND scope_id=?",
                        (now, now, assignment_id, identity.scope_id),
                    )
                result = {"deleted": True, "taskTagId": tag_id}
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task_tag.deleted",
                    aggregate_type="task_view", aggregate_id=tag_id,
                    aggregate_version=expected_version + 1,
                    expected_version=expected_version, result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # Task-plan Agent proposal boundary (proposal only, never task write)
    # ------------------------------------------------------------------
    def create_agent_proposal(
        self,
        identity: SessionIdentity,
        *,
        task_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        expected = self._require_expected_version(payload)
        proposed_patch = dict(payload.get("proposedPatch") or {})
        if not proposed_patch:
            raise RepositoryError(422, "task_proposal_empty", "任务建议没有可确认的变化")
        normalized = {
            "taskId": task_id,
            "expectedTaskVersion": expected,
            "proposedPatch": proposed_patch,
            "summary": str(payload.get("summary") or "").strip(),
        }
        payload_hash = _payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                task = self._require_task_read(connection, identity, task_id)
                if int(task["version"] or 1) != expected:
                    raise RepositoryError(409, "task_version_conflict", "任务已更新，请重新生成建议")
                # Validate without applying.  This is the hard proposal boundary.
                self._normalize_patch(connection, identity, task, proposed_patch)
                now = utc_now()
                proposal_manifest_id, proposal_hash, _ = self._store_manifest(
                    connection, identity, storage_kind="task_agent_proposal",
                    value=normalized, now=now,
                )
                proposal_id = new_id()
                connection.execute(
                    "INSERT INTO ai_proposals (id,scope_id,answer_id,operation_kind,payload_hash,"
                    "status,payload_object_manifest_id,risk_level,expires_at,version,lifecycle_state,"
                    "created_at,updated_at,deleted_at) VALUES (?,?,NULL,'task.update',?,"
                    "'pending_confirmation',?,'business_write',NULL,1,'active',?,?,NULL)",
                    (
                        proposal_id, identity.scope_id, proposal_hash,
                        proposal_manifest_id, now, now,
                    ),
                )
                result = {
                    "proposal": {
                        "proposalId": proposal_id,
                        "taskId": task_id,
                        "expectedTaskVersion": expected,
                        "status": "pending_confirmation",
                        "riskLevel": "business_write",
                        "summary": normalized["summary"],
                        "proposedPatch": proposed_patch,
                        "taskWritePerformed": False,
                        "createdAt": now,
                    }
                }
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task_agent.proposal_created",
                    aggregate_type="ai_proposal", aggregate_id=proposal_id,
                    aggregate_version=1, expected_version=None, result=result, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    # ------------------------------------------------------------------
    # GC-05 preflight, idempotent commit, per-item results
    # ------------------------------------------------------------------
    def bulk_preflight(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        raw_items = payload.get("items") or []
        if not isinstance(raw_items, list) or not raw_items:
            raise RepositoryError(422, "bulk_items_required", "请选择至少一个任务")
        if len(raw_items) > 200:
            raise RepositoryError(422, "bulk_items_too_many", "单次最多预检200个任务")
        request_shape = {
            "atomicityMode": str(payload.get("atomicityMode") or "per_item"),
            "items": raw_items,
        }
        if request_shape["atomicityMode"] != "per_item":
            raise RepositoryError(422, "bulk_atomicity_invalid", "严格批量任务仅支持逐项独立结算")
        payload_hash = _payload_hash(request_shape)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                now = utc_now()
                bulk_id = new_id()
                normalized_items: list[dict[str, Any]] = []
                seen_keys: set[str] = set()
                for index, raw in enumerate(raw_items):
                    if not isinstance(raw, Mapping):
                        raw = {}
                    item_key = _text(raw.get("itemKey")) or f"item-{index + 1}"
                    task_id = _text(raw.get("taskId"))
                    try:
                        expected = int(raw.get("expectedVersion") or 0)
                    except (TypeError, ValueError):
                        expected = 0
                    patch_source = raw.get("patch") if isinstance(raw.get("patch"), Mapping) else {}
                    code = "ready"
                    reason = "可提交"
                    normalized_patch: dict[str, Any] = {}
                    current_version: int | None = None
                    if item_key in seen_keys:
                        code, reason = "invalid", "itemKey重复"
                    elif task_id is None or expected <= 0 or not patch_source:
                        code, reason = "invalid", "缺少taskId、expectedVersion或patch"
                    else:
                        try:
                            row = self._require_task_write(connection, identity, task_id)
                            current_version = int(row["version"] or 1)
                            normalized_patch = self._normalize_patch(
                                connection, identity, row, patch_source
                            )
                            if current_version != expected:
                                code, reason = "conflict", "任务版本已变化"
                        except RepositoryError as exc:
                            code = (
                                "forbidden" if exc.status_code == 403
                                else "missing" if exc.status_code == 404
                                else "invalid"
                            )
                            reason = exc.message
                    seen_keys.add(item_key)
                    normalized_items.append(
                        {
                            "itemKey": item_key,
                            "taskId": task_id,
                            "expectedVersion": expected,
                            "observedVersion": current_version,
                            "patch": normalized_patch,
                            "preflightResult": code,
                            "reason": reason,
                        }
                    )
                snapshot_hash = sha256_text(canonical_json(normalized_items))
                ready_count = sum(1 for item in normalized_items if item["preflightResult"] == "ready")
                status = "preflight_ready" if ready_count else "preflight_blocked"
                connection.execute(
                    "INSERT INTO bulk_operations (id,scope_id,operation_id,preflight_snapshot_hash,"
                    "atomicity_mode,status,preflight_object_manifest_id,created_by_membership_id,"
                    "created_at,committed_at,version,lifecycle_state,updated_at,deleted_at) "
                    "VALUES (?,?,NULL,?,'per_item',?,NULL,?,?,NULL,1,'active',?,NULL)",
                    (bulk_id, identity.scope_id, snapshot_hash, status, identity.membership_id, now, now),
                )
                for item in normalized_items:
                    connection.execute(
                        "INSERT INTO bulk_operation_items (id,scope_id,bulk_operation_id,item_key,"
                        "preflight_result,commit_result,conflict_code,target_object_id,result_hash,"
                        "version,lifecycle_state,created_at,updated_at,deleted_at) VALUES (?,?,?,?,?,"
                        "NULL,?, ?,NULL,1,'active',?,?,NULL)",
                        (
                            new_id(), identity.scope_id, bulk_id, item["itemKey"],
                            item["preflightResult"],
                            item["preflightResult"] if item["preflightResult"] != "ready" else None,
                            item["taskId"], now, now,
                        ),
                    )
                result = {
                    "bulkOperationId": bulk_id,
                    "status": status,
                    "atomicityMode": "per_item",
                    "preflightSnapshotHash": snapshot_hash,
                    "readyCount": ready_count,
                    "blockedCount": len(normalized_items) - ready_count,
                    "items": normalized_items,
                    "businessWrites": 0,
                    "createdAt": now,
                }
                operation_id, manifest_id = self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task_bulk.preflighted",
                    aggregate_type="bulk_operation", aggregate_id=bulk_id,
                    aggregate_version=1, expected_version=None, result=result, now=now,
                )
                connection.execute(
                    "UPDATE bulk_operations SET operation_id=?,preflight_object_manifest_id=? "
                    "WHERE id=? AND scope_id=?",
                    (operation_id, manifest_id, bulk_id, identity.scope_id),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def bulk_commit(
        self,
        identity: SessionIdentity,
        *,
        bulk_operation_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        requested_snapshot_hash = _text(payload.get("preflightSnapshotHash"))
        if requested_snapshot_hash is None:
            raise RepositoryError(422, "preflight_snapshot_required", "缺少预检快照")
        normalized_request = {
            "bulkOperationId": bulk_operation_id,
            "preflightSnapshotHash": requested_snapshot_hash,
        }
        payload_hash = _payload_hash(normalized_request)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                bulk = connection.execute(
                    "SELECT * FROM bulk_operations WHERE id=? AND scope_id=? "
                    "AND lifecycle_state='active'",
                    (bulk_operation_id, identity.scope_id),
                ).fetchone()
                if bulk is None:
                    raise RepositoryError(404, "bulk_operation_missing", "批量预检不存在")
                if str(bulk["preflight_snapshot_hash"] or "") != requested_snapshot_hash:
                    raise RepositoryError(409, "bulk_preflight_stale", "预检快照不匹配，请重新预检")
                if str(bulk["status"] or "").startswith("committed") or bulk["committed_at"]:
                    raise RepositoryError(409, "bulk_already_committed", "该批量预检已提交，请使用原操作标识重放")
                manifest = connection.execute(
                    "SELECT receipt,content_hash FROM object_manifests WHERE id=? AND scope_id=? "
                    "AND lifecycle_state='active'",
                    (bulk["preflight_object_manifest_id"], identity.scope_id),
                ).fetchone()
                if manifest is None or sha256_text(str(manifest["receipt"] or "")) != str(manifest["content_hash"] or ""):
                    raise RepositoryError(500, "bulk_preflight_receipt_invalid", "批量预检回执无法校验")
                preflight = json.loads(str(manifest["receipt"] or "{}"))
                items = preflight.get("items") if isinstance(preflight, Mapping) else None
                if not isinstance(items, list):
                    raise RepositoryError(500, "bulk_preflight_receipt_invalid", "批量预检回执结构无效")
                now = utc_now()
                item_results: list[dict[str, Any]] = []
                succeeded: list[tuple[str, int, int, str]] = []
                for item_index, item in enumerate(items):
                    item_key = str(item.get("itemKey") or "")
                    task_id = _text(item.get("taskId"))
                    expected = int(item.get("expectedVersion") or 0)
                    code = str(item.get("preflightResult") or "invalid")
                    reason = str(item.get("reason") or "预检未通过")
                    task_payload: dict[str, Any] | None = None
                    if code == "ready" and task_id:
                        savepoint = f"gc05_item_{item_index}"
                        connection.execute(f"SAVEPOINT {savepoint}")
                        try:
                            row = self._require_task_write(connection, identity, task_id)
                            if int(row["version"] or 1) != expected:
                                raise RepositoryError(409, "task_version_conflict", "提交时任务版本已变化")
                            patch = dict(item.get("patch") or {})
                            self._revalidate_normalized_patch(
                                connection, identity, row, patch
                            )
                            current = self._apply_patch(
                                connection, identity, row=row, patch=patch,
                                expected_version=expected, now=now,
                            )
                            code, reason = "succeeded", "已提交"
                            task_payload = self._task_payload(connection, identity, current)
                            succeeded.append((task_id, expected, int(current["version"]), _payload_hash(patch)))
                            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                        except RepositoryError as exc:
                            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                            code = "conflict" if exc.status_code == 409 else "forbidden" if exc.status_code == 403 else "failed"
                            reason = exc.message
                    result_hash = sha256_text(
                        canonical_json(
                            {
                                "itemKey": item_key,
                                "taskId": task_id,
                                "result": code,
                                "taskVersion": task_payload.get("version") if task_payload else None,
                            }
                        )
                    )
                    connection.execute(
                        "UPDATE bulk_operation_items SET commit_result=?,conflict_code=?,"
                        "result_hash=?,version=COALESCE(version,1)+1,updated_at=? "
                        "WHERE scope_id=? AND bulk_operation_id=? AND item_key=?",
                        (
                            code,
                            None if code == "succeeded" else code,
                            result_hash,
                            now,
                            identity.scope_id,
                            bulk_operation_id,
                            item_key,
                        ),
                    )
                    item_results.append(
                        {
                            "itemKey": item_key,
                            "taskId": task_id,
                            "result": code,
                            "reason": reason,
                            "task": task_payload,
                            "retryable": code in {"conflict", "failed"},
                        }
                    )
                success_count = sum(1 for item in item_results if item["result"] == "succeeded")
                if success_count == len(item_results):
                    final_status = "committed"
                elif success_count:
                    final_status = "committed_partial"
                else:
                    final_status = "committed_failed"
                connection.execute(
                    "UPDATE bulk_operations SET status=?,committed_at=?,version=COALESCE(version,1)+1,"
                    "updated_at=? WHERE id=? AND scope_id=?",
                    (final_status, now, now, bulk_operation_id, identity.scope_id),
                )
                result = {
                    "bulkOperationId": bulk_operation_id,
                    "status": final_status,
                    "preflightSnapshotHash": requested_snapshot_hash,
                    "successCount": success_count,
                    "failureCount": len(item_results) - success_count,
                    "items": item_results,
                    "projection": self._projection_for_tasks(
                        connection, identity, [item[0] for item in succeeded]
                    ),
                    "notificationResult": self._notification_result(requested_recipients=0),
                    "committedAt": now,
                }
                operation_id, manifest_id = self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="task_bulk.committed",
                    aggregate_type="bulk_operation", aggregate_id=bulk_operation_id,
                    aggregate_version=int(bulk["version"] or 1) + 1,
                    expected_version=int(bulk["version"] or 1), result=result, now=now,
                )
                for task_id, expected, version, patch_hash in succeeded:
                    self._record_child_task_command(
                        connection, identity, parent_operation_id=operation_id,
                        parent_manifest_id=manifest_id,
                        idempotency_key=f"{idempotency_key}:{task_id}",
                        payload_hash=patch_hash, task_id=task_id,
                        task_version=version, expected_version=expected, now=now,
                    )
                connection.execute(
                    "UPDATE bulk_operations SET operation_id=? WHERE id=? AND scope_id=?",
                    (operation_id, bulk_operation_id, identity.scope_id),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise


__all__ = ["GC04TaskRepository"]
