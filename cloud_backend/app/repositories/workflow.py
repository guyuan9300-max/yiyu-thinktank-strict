"""Strict authority operations for tasks, event lines, plans, and reviews."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from xml.etree import ElementTree

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.security import payload_fingerprint

from ..repository import CloudRepository, RepositoryError, SessionIdentity


def _text(value: Any) -> str:
    return str(value or "").strip()


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class WorkflowRepository:
    """Domain repository that reuses the root task/event authority helpers."""

    def __init__(self, repository: CloudRepository):
        self.root = repository

    def _connection(self):
        return self.root._connection()  # noqa: SLF001 - shared strict repository

    @staticmethod
    def _require_admin(identity: SessionIdentity) -> None:
        if not identity.is_admin:
            raise RepositoryError(403, "admin_required", "仅组织管理员可管理机器人周计划")

    def _receipt(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT payload_hash, result_json
            FROM command_idempotency
            WHERE scope_id = ? AND actor_principal_id = ?
              AND command_type = ? AND idempotency_key = ?
            """,
            (
                identity.scope_id,
                identity.principal_id,
                command_type,
                idempotency_key,
            ),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"]) != payload_fingerprint(dict(payload)):
            raise RepositoryError(409, "idempotency_conflict", "操作标识已用于不同内容")
        return json.loads(str(row["result_json"]))

    def _record(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_version: int | None,
        before_version: int | None,
        after_version: int,
        payload: Mapping[str, Any],
        result: Mapping[str, Any],
        policy_evidence: list[dict[str, Any]] | None = None,
    ) -> None:
        now = utc_now()
        operation_id = new_id()
        payload_document = dict(payload)
        result_document = dict(result)
        payload_json = canonical_json(payload_document)
        result_json = canonical_json(result_document)
        payload_hash = payload_fingerprint(payload_document)
        connection.execute(
            """
            INSERT INTO command_envelopes (
                command_id, scope_id, organization_id, operation_id,
                idempotency_key, aggregate_type, aggregate_id, command_type,
                actor_principal_id, expected_version, payload_json, payload_hash,
                status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?)
            """,
            (
                new_id(),
                identity.scope_id,
                identity.organization_id,
                operation_id,
                idempotency_key,
                aggregate_type,
                aggregate_id,
                command_type,
                identity.principal_id,
                expected_version,
                payload_json,
                payload_hash,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO command_idempotency (
                record_id, scope_id, actor_principal_id, command_type,
                idempotency_key, payload_hash, result_hash, result_json,
                expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '9999-12-31T23:59:59.999Z', ?)
            """,
            (
                new_id(),
                identity.scope_id,
                identity.principal_id,
                command_type,
                idempotency_key,
                payload_hash,
                sha256_text(result_json),
                result_json,
                now,
            ),
        )
        self.root._insert_audit(  # noqa: SLF001
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            actor_id=identity.principal_id,
            action=command_type,
            resource_type=aggregate_type,
            resource_id=aggregate_id,
            before_version=before_version,
            after_version=after_version,
            summary={
                **payload_document,
                **(
                    {"taskControlRules": policy_evidence}
                    if policy_evidence
                    else {}
                ),
            },
        )
        self.root._insert_outbox(  # noqa: SLF001
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=after_version,
            event_type=command_type,
            payload={
                "resourceType": aggregate_type,
                "resourceId": aggregate_id,
                "version": after_version,
            },
        )

    @staticmethod
    def _expected(payload: Mapping[str, Any], *, code: str) -> int:
        expected = _integer(
            payload.get("expectedVersion", payload.get("expected_version")),
        )
        if expected < 1:
            raise RepositoryError(428, code, "该写入必须携带 expectedVersion")
        return expected

    @staticmethod
    def _assert_version(
        row: sqlite3.Row,
        expected: int,
        *,
        code: str,
        message: str,
    ) -> int:
        current = int(row["version"])
        if current != expected:
            raise RepositoryError(409, code, message)
        return current

    def _task_payload(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        task = self.root._task_payload(connection, row)  # noqa: SLF001
        task["listMemberships"] = [
            {
                "taskListId": item["task_list_id"],
                "orderIndex": int(item["order_index"]),
            }
            for item in connection.execute(
                """
                SELECT task_list_id, order_index
                FROM task_list_memberships
                WHERE organization_id = ? AND task_id = ?
                ORDER BY order_index, task_list_id
                """,
                (row["organization_id"], row["task_id"]),
            ).fetchall()
        ]
        task["tags"] = [
            {
                "taskTagId": item["task_tag_id"],
                "name": item["name"],
                "color": item["color"],
                "scopeKind": item["scope_kind"],
                "ownerMembershipId": item["owner_membership_id"],
                "updatedAt": item["updated_at"],
                "archivedAt": item["archived_at"],
            }
            for item in connection.execute(
                """
                SELECT tt.task_tag_id, tt.name, tt.color, tt.scope_kind,
                       tt.owner_membership_id, tt.updated_at, tt.archived_at
                FROM task_tag_assignments tta
                JOIN task_tags tt ON tt.task_tag_id = tta.task_tag_id
                WHERE tta.organization_id = ? AND tta.task_id = ?
                  AND tt.lifecycle_state = 'active'
                ORDER BY tt.name, tt.task_tag_id
                """,
                (row["organization_id"], row["task_id"]),
            ).fetchall()
        ]
        event_link = connection.execute(
            """
            SELECT event_line_id, is_milestone, version
            FROM event_line_task_links
            WHERE organization_id = ? AND task_id = ? AND link_state = 'active'
            """,
            (row["organization_id"], row["task_id"]),
        ).fetchone()
        task["eventLineId"] = event_link["event_line_id"] if event_link else None
        task["eventLineMilestone"] = bool(event_link["is_milestone"]) if event_link else False
        task["eventLineLinkVersion"] = int(event_link["version"]) if event_link else None
        task["attachments"] = []
        for item in connection.execute(
            """
            SELECT sa.*
            FROM evidence_links el
            JOIN source_assets sa ON sa.source_asset_id = el.source_id
            WHERE el.organization_id = ? AND el.target_type = 'task'
              AND el.target_id = ? AND el.source_type = 'source_asset'
              AND el.lifecycle_state = 'active'
              AND sa.lifecycle_state = 'active'
            ORDER BY sa.updated_at DESC, sa.source_asset_id
            """,
            (row["organization_id"], row["task_id"]),
        ).fetchall():
            attempt = connection.execute(
                """
                SELECT processing_attempt_id, state, error_code, error_message
                FROM processing_attempts
                WHERE organization_id = ? AND source_asset_id = ?
                  AND processing_kind = 'transcription'
                ORDER BY attempt_no DESC, created_at DESC
                LIMIT 1
                """,
                (row["organization_id"], item["source_asset_id"]),
            ).fetchone()
            transcript = connection.execute(
                """
                SELECT kd.document_id, dv.preview_text
                FROM knowledge_documents kd
                LEFT JOIN document_versions dv
                  ON dv.document_id = kd.document_id
                 AND dv.organization_id = kd.organization_id
                 AND dv.version = kd.current_version
                WHERE kd.organization_id = ? AND kd.source_asset_id = ?
                  AND kd.lifecycle_state = 'active'
                ORDER BY kd.updated_at DESC, kd.document_id
                LIMIT 1
                """,
                (row["organization_id"], item["source_asset_id"]),
            ).fetchone()
            processing_status = (
                "ready"
                if transcript is not None
                else (
                    "failed"
                    if attempt is not None and attempt["state"] == "failed"
                    else attempt["state"]
                    if attempt is not None
                    else "not_requested"
                )
            )
            task["attachments"].append(
                {
                    "id": item["source_asset_id"],
                    "sourceAssetId": item["source_asset_id"],
                    "name": item["file_name"],
                    "title": item["file_name"],
                    "mediaType": item["media_type"],
                    "size": int(item["byte_size"]),
                    "contentHash": item["content_hash"],
                    "sourceKind": item["source_kind"],
                    "lifecycleState": item["lifecycle_state"],
                    "version": int(item["version"]),
                    "processingStatus": processing_status,
                    "processingError": (
                        attempt["error_message"]
                        if attempt is not None
                        else None
                    ),
                    "processingErrorCode": (
                        attempt["error_code"]
                        if attempt is not None
                        else None
                    ),
                    "transcriptAttachmentId": (
                        item["source_asset_id"]
                        if transcript is not None
                        else None
                    ),
                    "transcriptDocumentId": (
                        transcript["document_id"]
                        if transcript is not None
                        else None
                    ),
                    "transcriptPreview": (
                        transcript["preview_text"]
                        if transcript is not None
                        else None
                    ),
                    "createdAt": item["created_at"],
                    "updatedAt": item["updated_at"],
                }
            )
        try:
            task["attributes"] = json.loads(str(row["attributes_json"]))
        except (TypeError, ValueError):
            task["attributes"] = {}
        return task

    @staticmethod
    def _list_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "taskListId": row["task_list_id"],
            "name": row["name"],
            "color": row["color"],
            "scopeKind": row["scope_kind"],
            "ownerMembershipId": row["owner_membership_id"],
            "description": row["description"],
            "sortOrder": int(row["sort_order"]),
            "isDefault": bool(row["is_default"]),
            "lifecycleState": row["lifecycle_state"],
            "version": int(row["version"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _tag_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "taskTagId": row["task_tag_id"],
            "name": row["name"],
            "color": row["color"],
            "scopeKind": row["scope_kind"],
            "ownerMembershipId": row["owner_membership_id"],
            "lifecycleState": row["lifecycle_state"],
            "version": int(row["version"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def board(self, identity: SessionIdentity) -> dict[str, Any]:
        visible = self.root.business_snapshot(identity)
        visible_ids = {
            _text(item.get("taskId")) for item in visible.get("tasks") or []
        }
        with self._connection() as connection:
            tasks: list[dict[str, Any]] = []
            if visible_ids:
                placeholders = ",".join("?" for _ in visible_ids)
                rows = connection.execute(
                    f"""
                    SELECT * FROM task_records
                    WHERE organization_id = ? AND task_id IN ({placeholders})
                    ORDER BY updated_at DESC, task_id
                    """,
                    (identity.organization_id, *sorted(visible_ids)),
                ).fetchall()
                tasks = [self._task_payload(connection, row) for row in rows]
            lists = [
                self._list_payload(row)
                for row in connection.execute(
                    """
                    SELECT * FROM task_lists
                    WHERE organization_id = ?
                      AND (
                        scope_kind = 'organization'
                        OR owner_membership_id = ?
                      )
                    ORDER BY lifecycle_state = 'archived', sort_order, name
                    """,
                    (identity.organization_id, identity.membership_id),
                ).fetchall()
            ]
            tags = [
                self._tag_payload(row)
                for row in connection.execute(
                    """
                    SELECT * FROM task_tags
                    WHERE organization_id = ?
                      AND (
                        scope_kind = 'organization'
                        OR owner_membership_id = ?
                      )
                    ORDER BY lifecycle_state = 'archived', name
                    """,
                    (identity.organization_id, identity.membership_id),
                ).fetchall()
            ]
        return {"tasks": tasks, "lists": lists, "tags": tags}

    def clients_pulse(self, identity: SessionIdentity) -> dict[str, Any]:
        """Project activity pulse derived from visible strict-v4 authorities."""
        snapshot = self.root.business_snapshot(identity)
        projects = [
            item
            for item in snapshot.get("projects") or []
            if _text(item.get("lifecycleState")) == "active"
        ]
        project_ids = {
            _text(item.get("projectId"))
            for item in projects
            if _text(item.get("projectId"))
        }
        visible_task_ids = {
            _text(item.get("taskId"))
            for item in snapshot.get("tasks") or []
            if _text(item.get("taskId"))
            and _text(item.get("projectId")) in project_ids
        }
        visible_event_ids = {
            _text(item.get("eventLineId"))
            for item in snapshot.get("eventLines") or []
            if _text(item.get("eventLineId"))
            and _text(item.get("projectId")) in project_ids
        }
        now = datetime.now(timezone.utc)
        generated_at = now.isoformat().replace("+00:00", "Z")
        week_start = (now - timedelta(days=7)).isoformat()
        today = now.date().isoformat()
        counters = {
            project_id: {
                "documents": 0,
                "tasks": 0,
                "evidence": 0,
                "blockers": 0,
                "overdue": 0,
            }
            for project_id in project_ids
        }
        task_projects: dict[str, str] = {}
        source_visible_task_ids: set[str] = set()
        event_projects: dict[str, str] = {}
        source_visible_event_ids: set[str] = set()
        document_projects: dict[str, str] = {}
        document_version_projects: dict[str, str] = {}
        source_asset_projects: dict[str, str] = {}
        event_activity_projects: dict[str, str] = {}
        with self._connection() as connection:
            department_ids = {
                _text(row["department_id"])
                for row in connection.execute(
                    """
                    SELECT department_id
                    FROM department_memberships
                    WHERE organization_id = ? AND membership_id = ?
                      AND status = 'active'
                    """,
                    (identity.organization_id, identity.membership_id),
                ).fetchall()
                if _text(row["department_id"])
            }
            participant_project_ids = {
                _text(row["project_id"])
                for row in connection.execute(
                    """
                    SELECT id AS project_id
                    FROM clients
                    WHERE scope_id = ? AND owner_membership_id = ?
                      AND lifecycle_state != 'deleted'
                    UNION
                    SELECT secured_resource_id AS project_id
                    FROM object_grants
                    WHERE scope_id = ? AND subject_membership_id = ?
                      AND status = 'active' AND lifecycle_state = 'active'
                    """,
                    (
                        identity.scope_id,
                        identity.membership_id,
                        identity.scope_id,
                        identity.membership_id,
                    ),
                ).fetchall()
                if _text(row["project_id"])
            }
            task_participation_ids = {
                _text(row["task_id"])
                for row in connection.execute(
                    """
                    SELECT task_id
                    FROM task_collaborators
                    WHERE organization_id = ? AND membership_id = ?
                      AND inbox_state != 'returned'
                    """,
                    (identity.organization_id, identity.membership_id),
                ).fetchall()
                if _text(row["task_id"])
            }
            if visible_task_ids:
                placeholders = ",".join("?" for _ in visible_task_ids)
                rows = connection.execute(
                    f"""
                    SELECT task_id, project_id, lifecycle_state, due_date,
                           deadline_at, created_at, attributes_json,
                           visibility_scope, created_by_membership_id
                    FROM task_records
                    WHERE organization_id = ?
                      AND task_id IN ({placeholders})
                    """,
                    (identity.organization_id, *sorted(visible_task_ids)),
                ).fetchall()
                for row in rows:
                    project_id = _text(row["project_id"])
                    if project_id not in counters:
                        continue
                    task_id = _text(row["task_id"])
                    task_projects[task_id] = project_id
                    if (
                        _text(row["visibility_scope"]) == "organization"
                        or _text(row["created_by_membership_id"])
                        == identity.membership_id
                        or task_id in task_participation_ids
                    ):
                        source_visible_task_ids.add(task_id)
                    lifecycle = _text(row["lifecycle_state"])
                    if (
                        lifecycle != "archived"
                        and _text(row["created_at"]) >= week_start
                    ):
                        counters[project_id]["tasks"] += 1
                    if lifecycle in {
                        "completed",
                        "cancelled",
                        "archived",
                    }:
                        continue
                    due = _text(row["due_date"]) or _text(row["deadline_at"])[:10]
                    if due and due < today:
                        counters[project_id]["overdue"] += 1
                    try:
                        attributes = json.loads(
                            str(row["attributes_json"] or "{}")
                        )
                    except (TypeError, ValueError):
                        attributes = {}
                    if _text(attributes.get("currentBlocker")):
                        counters[project_id]["blockers"] += 1
            if project_ids:
                project_placeholders = ",".join("?" for _ in project_ids)
                document_rows = connection.execute(
                    f"""
                    SELECT document_id, project_id, owner_membership_id,
                           department_id, visibility_scope, created_at
                    FROM knowledge_documents
                    WHERE organization_id = ?
                      AND project_id IN ({project_placeholders})
                      AND lifecycle_state = 'active'
                    """,
                    (identity.organization_id, *sorted(project_ids)),
                ).fetchall()
                for row in document_rows:
                    document_id = _text(row["document_id"])
                    project_id = _text(row["project_id"])
                    visibility = _text(row["visibility_scope"])
                    is_owner = (
                        _text(row["owner_membership_id"])
                        == identity.membership_id
                    )
                    source_visible = (
                        visibility == "organization"
                        or is_owner
                        or (
                            visibility == "department"
                            and _text(row["department_id"]) in department_ids
                        )
                        or (
                            visibility == "participants"
                            and project_id in participant_project_ids
                        )
                    )
                    if not source_visible:
                        continue
                    document_projects[document_id] = project_id
                    if _text(row["created_at"]) >= week_start:
                        counters[project_id]["documents"] += 1
                if document_projects:
                    placeholders = ",".join(
                        "?" for _ in document_projects
                    )
                    version_rows = connection.execute(
                        f"""
                        SELECT document_version_id, document_id
                        FROM document_versions
                        WHERE organization_id = ?
                          AND document_id IN ({placeholders})
                        """,
                        (
                            identity.organization_id,
                            *sorted(document_projects),
                        ),
                    ).fetchall()
                    document_version_projects = {
                        _text(row["document_version_id"]): document_projects[
                            _text(row["document_id"])
                        ]
                        for row in version_rows
                    }
                asset_rows = connection.execute(
                    f"""
                    SELECT source_asset_id, project_id
                    FROM source_assets
                    WHERE organization_id = ?
                      AND project_id IN ({project_placeholders})
                      AND lifecycle_state = 'active'
                    """,
                    (identity.organization_id, *sorted(project_ids)),
                ).fetchall()
                source_asset_projects = {
                    _text(row["source_asset_id"]): _text(row["project_id"])
                    for row in asset_rows
                }
            if visible_event_ids:
                placeholders = ",".join("?" for _ in visible_event_ids)
                event_rows = connection.execute(
                    f"""
                    SELECT event_line_id, project_id, created_by_membership_id,
                           department_id, visibility_scope
                    FROM event_line_records
                    WHERE organization_id = ?
                      AND event_line_id IN ({placeholders})
                    """,
                    (identity.organization_id, *sorted(visible_event_ids)),
                ).fetchall()
                event_participation_ids = {
                    _text(row["event_line_id"])
                    for row in connection.execute(
                        """
                        SELECT event_line_id
                        FROM event_line_participants
                        WHERE organization_id = ? AND membership_id = ?
                          AND status = 'active'
                        """,
                        (identity.organization_id, identity.membership_id),
                    ).fetchall()
                    if _text(row["event_line_id"])
                }
                for row in event_rows:
                    event_id = _text(row["event_line_id"])
                    project_id = _text(row["project_id"])
                    event_projects[event_id] = project_id
                    if (
                        _text(row["visibility_scope"]) == "organization"
                        or _text(row["created_by_membership_id"])
                        == identity.membership_id
                        or event_id in event_participation_ids
                        or (
                            _text(row["visibility_scope"]) == "department"
                            and _text(row["department_id"]) in department_ids
                        )
                    ):
                        source_visible_event_ids.add(event_id)
                if source_visible_event_ids:
                    placeholders = ",".join(
                        "?" for _ in source_visible_event_ids
                    )
                    activity_rows = connection.execute(
                        f"""
                        SELECT event_line_activity_id, event_line_id
                        FROM event_line_activities
                        WHERE organization_id = ?
                          AND event_line_id IN ({placeholders})
                        """,
                        (
                            identity.organization_id,
                            *sorted(source_visible_event_ids),
                        ),
                    ).fetchall()
                    event_activity_projects = {
                        _text(row["event_line_activity_id"]): event_projects[
                            _text(row["event_line_id"])
                        ]
                        for row in activity_rows
                    }
            if visible_task_ids or visible_event_ids:
                rows = connection.execute(
                    """
                    SELECT source_type, source_id, target_type, target_id
                    FROM evidence_links
                    WHERE organization_id = ?
                      AND lifecycle_state = 'active'
                      AND created_at >= ?
                      AND target_type IN ('task', 'event_line')
                    """,
                    (identity.organization_id, week_start),
                ).fetchall()
                for row in rows:
                    target_type = _text(row["target_type"])
                    target_id = _text(row["target_id"])
                    project_id = (
                        task_projects.get(target_id)
                        if target_type == "task"
                        else event_projects.get(target_id)
                    )
                    source_type = _text(row["source_type"])
                    source_id = _text(row["source_id"])
                    source_project_id = (
                        source_asset_projects.get(source_id)
                        if source_type == "source_asset"
                        else document_version_projects.get(source_id)
                        if source_type == "document_version"
                        else task_projects.get(source_id)
                        if (
                            source_type == "task"
                            and source_id in source_visible_task_ids
                        )
                        else event_activity_projects.get(source_id)
                        if source_type == "event_line_activity"
                        else None
                    )
                    if (
                        project_id in counters
                        and source_project_id == project_id
                    ):
                        counters[project_id]["evidence"] += 1

        summaries = []
        for project in projects:
            project_id = _text(project.get("projectId"))
            count = counters.get(project_id) or {}
            new_documents = int(count.get("documents") or 0)
            new_tasks = int(count.get("tasks") or 0)
            new_evidence = int(count.get("evidence") or 0)
            blockers = int(count.get("blockers") or 0)
            overdue = int(count.get("overdue") or 0)
            if overdue:
                top_signal = f"{overdue} 项任务已逾期"
            elif blockers >= 3:
                top_signal = f"{blockers} 处主线长期停滞"
            elif new_documents >= 3:
                top_signal = f"本周新增 {new_documents} 份资料待消化"
            elif new_tasks and new_documents:
                top_signal = (
                    f"本周 +{new_tasks} 任务 / +{new_documents} 资料"
                )
            elif blockers:
                top_signal = f"{blockers} 处卡点待处理"
            elif new_tasks:
                top_signal = f"本周新增 {new_tasks} 项任务"
            elif new_documents:
                top_signal = f"本周新增 {new_documents} 份资料"
            elif new_evidence:
                top_signal = f"本周新增 {new_evidence} 条事实"
            else:
                top_signal = "本周无动态"
            summaries.append(
                {
                    "clientId": project_id,
                    "clientName": _text(project.get("name")),
                    "clientStage": _text(project.get("lifecycleState")),
                    "weeklyNewDocumentCount": new_documents,
                    "weeklyNewTaskCount": new_tasks,
                    "weeklyNewEvidenceCount": new_evidence,
                    "currentBlockerCount": blockers,
                    "overdueTodoCount": overdue,
                    "hasActivity": any(
                        (
                            new_documents,
                            new_tasks,
                            new_evidence,
                            blockers,
                            overdue,
                        )
                    ),
                    "topSignal": top_signal,
                }
            )
        summaries.sort(
            key=lambda item: (
                0 if item["hasActivity"] else 1,
                -(
                    item["weeklyNewDocumentCount"]
                    + item["weeklyNewTaskCount"]
                    + item["weeklyNewEvidenceCount"]
                ),
                -item["currentBlockerCount"],
                item["clientName"],
            )
        )
        return {"summaries": summaries, "generatedAt": generated_at}

    def task_detail(self, identity: SessionIdentity, task_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = self.root._task_row(  # noqa: SLF001
                connection,
                identity,
                task_id,
                require_edit=False,
            )
            return {"task": self._task_payload(connection, row)}

    def mutate_named_collection(
        self,
        identity: SessionIdentity,
        *,
        kind: str,
        item_id: str | None,
        payload: Mapping[str, Any],
        idempotency_key: str,
        action: str,
    ) -> dict[str, Any]:
        if kind not in {"list", "tag"}:
            raise RepositoryError(404, "workflow_collection_unknown", "未知任务分类")
        table = "task_lists" if kind == "list" else "task_tags"
        id_column = "task_list_id" if kind == "list" else "task_tag_id"
        aggregate_type = f"task_{kind}"
        command_type = f"{aggregate_type}.{action}"
        normalized = dict(payload)
        normalized["name"] = _text(payload.get("name"))
        if action != "archive" and not normalized["name"]:
            raise RepositoryError(422, f"task_{kind}_name_required", "请输入名称")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                now = utc_now()
                if action == "create":
                    aggregate_id = item_id or new_id()
                    scope_kind = _text(payload.get("scopeKind") or payload.get("scope"))
                    scope_kind = (
                        "organization"
                        if scope_kind in {"org", "organization"}
                        else "personal"
                    )
                    if scope_kind == "organization" and not identity.is_admin:
                        raise RepositoryError(
                            403,
                            f"task_{kind}_organization_forbidden",
                            "只有管理员可新建组织级分类",
                        )
                    owner_id = (
                        None if scope_kind == "organization" else identity.membership_id
                    )
                    if kind == "list":
                        connection.execute(
                            """
                            INSERT INTO task_lists (
                                task_list_id, organization_id, name, color,
                                scope_kind, owner_membership_id, description,
                                sort_order, is_default, lifecycle_state, version,
                                created_at, updated_at, archived_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, NULL)
                            """,
                            (
                                aggregate_id,
                                identity.organization_id,
                                normalized["name"],
                                _text(payload.get("color")) or "#5B7BFE",
                                scope_kind,
                                owner_id,
                                _text(payload.get("description")),
                                _integer(payload.get("sortOrder")),
                                int(bool(payload.get("isDefault"))),
                                now,
                                now,
                            ),
                        )
                    else:
                        connection.execute(
                            """
                            INSERT INTO task_tags (
                                task_tag_id, organization_id, name, color,
                                scope_kind, owner_membership_id, lifecycle_state,
                                version, created_at, updated_at, archived_at
                            ) VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, NULL)
                            """,
                            (
                                aggregate_id,
                                identity.organization_id,
                                normalized["name"],
                                _text(payload.get("color")) or "#5B7BFE",
                                scope_kind,
                                owner_id,
                                now,
                                now,
                            ),
                        )
                    before_version = None
                    after_version = 1
                    expected_version = None
                else:
                    if not item_id:
                        raise RepositoryError(404, f"task_{kind}_missing", "分类不存在")
                    row = connection.execute(
                        f"""
                        SELECT * FROM {table}
                        WHERE organization_id = ? AND {id_column} = ?
                        """,
                        (identity.organization_id, item_id),
                    ).fetchone()
                    if row is None:
                        raise RepositoryError(404, f"task_{kind}_missing", "分类不存在")
                    if (
                        row["scope_kind"] == "organization"
                        and not identity.is_admin
                    ) or (
                        row["scope_kind"] == "personal"
                        and row["owner_membership_id"] != identity.membership_id
                        and not identity.is_admin
                    ):
                        raise RepositoryError(403, f"task_{kind}_forbidden", "无权修改该分类")
                    expected_version = self._expected(
                        payload,
                        code=f"task_{kind}_expected_version_required",
                    )
                    before_version = self._assert_version(
                        row,
                        expected_version,
                        code=f"task_{kind}_version_conflict",
                        message="分类已被更新，请刷新后重试",
                    )
                    aggregate_id = item_id
                    if action == "archive":
                        changed = connection.execute(
                            f"""
                            UPDATE {table}
                            SET lifecycle_state = 'archived', archived_at = ?,
                                version = version + 1, updated_at = ?
                            WHERE organization_id = ? AND {id_column} = ? AND version = ?
                            """,
                            (
                                now,
                                now,
                                identity.organization_id,
                                item_id,
                                expected_version,
                            ),
                        )
                    elif kind == "list":
                        changed = connection.execute(
                            """
                            UPDATE task_lists
                            SET name = ?, color = ?, description = ?, sort_order = ?,
                                is_default = ?, lifecycle_state = ?,
                                archived_at = CASE WHEN ? = 'archived' THEN ? ELSE NULL END,
                                version = version + 1, updated_at = ?
                            WHERE organization_id = ? AND task_list_id = ? AND version = ?
                            """,
                            (
                                normalized["name"],
                                _text(payload.get("color")) or row["color"],
                                _text(payload.get("description")) or row["description"],
                                _integer(payload.get("sortOrder"), int(row["sort_order"])),
                                int(bool(payload.get("isDefault", row["is_default"]))),
                                "archived" if payload.get("archived") else "active",
                                "archived" if payload.get("archived") else "active",
                                now,
                                now,
                                identity.organization_id,
                                item_id,
                                expected_version,
                            ),
                        )
                    else:
                        changed = connection.execute(
                            """
                            UPDATE task_tags
                            SET name = ?, color = ?, lifecycle_state = ?,
                                archived_at = CASE WHEN ? = 'archived' THEN ? ELSE NULL END,
                                version = version + 1, updated_at = ?
                            WHERE organization_id = ? AND task_tag_id = ? AND version = ?
                            """,
                            (
                                normalized["name"],
                                _text(payload.get("color")) or row["color"],
                                "archived" if payload.get("archived") else "active",
                                "archived" if payload.get("archived") else "active",
                                now,
                                now,
                                identity.organization_id,
                                item_id,
                                expected_version,
                            ),
                        )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            f"task_{kind}_version_conflict",
                            "分类已被更新，请刷新后重试",
                        )
                    after_version = before_version + 1
                row = connection.execute(
                    f"SELECT * FROM {table} WHERE {id_column} = ?",
                    (aggregate_id,),
                ).fetchone()
                item = (
                    self._list_payload(row)
                    if kind == "list"
                    else self._tag_payload(row)
                )
                result = {kind: item, "deleted": action == "archive"}
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    expected_version=expected_version,
                    before_version=before_version,
                    after_version=after_version,
                    payload=normalized,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def archive_task(
        self,
        identity: SessionIdentity,
        task_id: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "task.archived"
        expected = self._expected(payload, code="task_expected_version_required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self.root._task_row(  # noqa: SLF001
                    connection,
                    identity,
                    task_id,
                    require_edit=True,
                )
                current = self._assert_version(
                    row,
                    expected,
                    code="task_version_conflict",
                    message="任务已被更新，请刷新后重试",
                )
                policy = self.root._task_control_rule(  # noqa: SLF001
                    connection,
                    identity,
                    row,
                    action="cancel",
                )
                now = utc_now()
                changed = connection.execute(
                    """
                    UPDATE task_records
                    SET lifecycle_state = 'archived', archived_at = ?,
                        version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND task_id = ? AND version = ?
                    """,
                    (now, now, identity.organization_id, task_id, expected),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "task_version_conflict",
                        "任务已被更新，请刷新后重试",
                    )
                connection.execute(
                    """
                    INSERT INTO task_activity_events (
                        task_activity_id, organization_id, task_id,
                        actor_membership_id, event_type, payload_json, happened_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        task_id,
                        identity.membership_id,
                        command_type,
                        canonical_json(dict(payload)),
                        now,
                    ),
                )
                next_row = connection.execute(
                    "SELECT * FROM task_records WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                result = {"task": self._task_payload(connection, next_row), "deleted": True}
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    expected_version=expected,
                    before_version=current,
                    after_version=current + 1,
                    payload=payload,
                    result=result,
                    policy_evidence=[policy] if policy is not None else None,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def set_task_classification(
        self,
        identity: SessionIdentity,
        task_id: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "task.classification_updated"
        expected = self._expected(payload, code="task_expected_version_required")
        list_ids = sorted(
            {_text(value) for value in payload.get("taskListIds") or [] if _text(value)}
        )
        tag_ids = sorted(
            {_text(value) for value in payload.get("taskTagIds") or [] if _text(value)}
        )
        normalized = {
            "expectedVersion": expected,
            "taskListIds": list_ids,
            "taskTagIds": tag_ids,
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self.root._task_row(  # noqa: SLF001
                    connection,
                    identity,
                    task_id,
                    require_edit=True,
                )
                current = self._assert_version(
                    row,
                    expected,
                    code="task_version_conflict",
                    message="任务已被更新，请刷新后重试",
                )
                policy = self.root._task_control_rule(  # noqa: SLF001
                    connection,
                    identity,
                    row,
                    action="content",
                )
                for table, id_column, values in (
                    ("task_lists", "task_list_id", list_ids),
                    ("task_tags", "task_tag_id", tag_ids),
                ):
                    if not values:
                        continue
                    placeholders = ",".join("?" for _ in values)
                    found = connection.execute(
                        f"""
                        SELECT {id_column}, scope_kind, owner_membership_id
                        FROM {table}
                        WHERE organization_id = ? AND {id_column} IN ({placeholders})
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, *values),
                    ).fetchall()
                    found_ids = {str(item[id_column]) for item in found}
                    if found_ids != set(values):
                        raise RepositoryError(
                            422,
                            "task_classification_invalid",
                            "任务清单或标签不存在、已归档",
                        )
                    if any(
                        item["scope_kind"] == "personal"
                        and item["owner_membership_id"] != identity.membership_id
                        and not identity.is_admin
                        for item in found
                    ):
                        raise RepositoryError(
                            403,
                            "task_classification_forbidden",
                            "无权使用其他成员的个人分类",
                        )
                now = utc_now()
                connection.execute(
                    """
                    DELETE FROM task_list_memberships
                    WHERE organization_id = ? AND task_id = ?
                    """,
                    (identity.organization_id, task_id),
                )
                for order, task_list_id in enumerate(list_ids):
                    connection.execute(
                        """
                        INSERT INTO task_list_memberships (
                            task_id, task_list_id, organization_id, order_index,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            task_list_id,
                            identity.organization_id,
                            order,
                            now,
                            now,
                        ),
                    )
                connection.execute(
                    """
                    DELETE FROM task_tag_assignments
                    WHERE organization_id = ? AND task_id = ?
                    """,
                    (identity.organization_id, task_id),
                )
                for task_tag_id in tag_ids:
                    connection.execute(
                        """
                        INSERT INTO task_tag_assignments (
                            task_id, task_tag_id, organization_id,
                            assigned_by_membership_id, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            task_tag_id,
                            identity.organization_id,
                            identity.membership_id,
                            now,
                        ),
                    )
                changed = connection.execute(
                    """
                    UPDATE task_records
                    SET version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND task_id = ? AND version = ?
                    """,
                    (now, identity.organization_id, task_id, expected),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "task_version_conflict",
                        "任务已被更新，请刷新后重试",
                    )
                connection.execute(
                    """
                    INSERT INTO task_activity_events (
                        task_activity_id, organization_id, task_id,
                        actor_membership_id, event_type, payload_json, happened_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        task_id,
                        identity.membership_id,
                        command_type,
                        canonical_json(normalized),
                        now,
                    ),
                )
                next_row = connection.execute(
                    "SELECT * FROM task_records WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                result = {"task": self._task_payload(connection, next_row)}
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    expected_version=expected,
                    before_version=current,
                    after_version=current + 1,
                    payload=normalized,
                    result=result,
                    policy_evidence=[policy] if policy is not None else None,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _decode_attachment(
        self,
        payload: Mapping[str, Any],
    ) -> tuple[bytes, dict[str, Any]]:
        encoded = _text(payload.get("contentBase64"))
        file_name = Path(_text(payload.get("fileName"))).name
        media_type = _text(payload.get("mediaType")) or "application/octet-stream"
        content_hash = _text(payload.get("contentHash")).lower()
        byte_size = _integer(payload.get("byteSize"), -1)
        if not encoded or not file_name or not content_hash or byte_size < 0:
            raise RepositoryError(422, "attachment_payload_invalid", "附件信息不完整")
        if byte_size > 100 * 1024 * 1024:
            raise RepositoryError(413, "attachment_too_large", "单个附件不得超过 100MB")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise RepositoryError(422, "attachment_content_invalid", "附件内容无法校验") from exc
        actual_hash = hashlib.sha256(content).hexdigest()
        if len(content) != byte_size or actual_hash != content_hash:
            raise RepositoryError(422, "attachment_hash_mismatch", "附件大小或校验和不一致")
        return content, {
            "fileName": file_name,
            "mediaType": media_type,
            "contentHash": actual_hash,
            "byteSize": byte_size,
            "title": _text(payload.get("title")) or file_name,
            "purpose": _text(payload.get("purpose")),
            "sourceKind": _text(payload.get("sourceKind")) or "task_attachment",
            "expectedVersion": _integer(payload.get("expectedVersion")),
        }

    def _write_managed_object(
        self,
        identity: SessionIdentity,
        object_id: str,
        content: bytes,
    ) -> tuple[str, Path]:
        organization_segment = sha256_text(identity.organization_id)[:24]
        relative_key = f"workflow-objects/{organization_segment}/{object_id}"
        target = (self.root.database_path.parent / relative_key).resolve()
        managed_root = (self.root.database_path.parent / "workflow-objects").resolve()
        if managed_root not in target.parents:
            raise RepositoryError(500, "attachment_storage_path_invalid", "附件存储路径无效")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{object_id}.",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, target)
            target.chmod(0o600)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return relative_key, target

    def save_attachment(
        self,
        identity: SessionIdentity,
        *,
        target_type: str,
        target_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if target_type not in {"task", "event_line"}:
            raise RepositoryError(422, "attachment_target_invalid", "附件目标无效")
        content, normalized = self._decode_attachment(payload)
        expected = self._expected(normalized, code=f"{target_type}_expected_version_required")
        command_type = f"{target_type}.attachment_added"
        receipt_payload = dict(normalized)
        with self._connection() as connection:
            receipt = self._receipt(
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=receipt_payload,
            )
        if receipt is not None:
            return receipt
        object_id = new_id()
        source_asset_id = new_id()
        storage_key, managed_path = self._write_managed_object(
            identity,
            object_id,
            content,
        )
        committed = False
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    receipt = self._receipt(
                        connection,
                        identity,
                        command_type=command_type,
                        idempotency_key=idempotency_key,
                        payload=receipt_payload,
                    )
                    if receipt is not None:
                        connection.rollback()
                        return receipt
                    if target_type == "task":
                        target = self.root._task_row(  # noqa: SLF001
                            connection,
                            identity,
                            target_id,
                            require_edit=True,
                        )
                        project_id = target["project_id"]
                    else:
                        target = self._event_row(
                            connection,
                            identity,
                            target_id,
                            require_edit=True,
                        )
                        project_id = target["project_id"]
                    current = self._assert_version(
                        target,
                        expected,
                        code=f"{target_type}_version_conflict",
                        message="附件目标已被更新，请刷新后重试",
                    )
                    now = utc_now()
                    connection.execute(
                        """
                        INSERT INTO storage_objects (
                            object_id, scope_id, organization_id, storage_key,
                            content_hash, media_type, byte_size, lifecycle_state,
                            storage_receipt, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?)
                        """,
                        (
                            object_id,
                            identity.scope_id,
                            identity.organization_id,
                            storage_key,
                            normalized["contentHash"],
                            normalized["mediaType"],
                            normalized["byteSize"],
                            f"managed:{object_id}",
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO source_assets (
                            source_asset_id, organization_id, project_id,
                            storage_object_id, file_name, media_type, byte_size,
                            content_hash, source_kind, source_locator,
                            lifecycle_state, created_by_membership_id, version,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?)
                        """,
                        (
                            source_asset_id,
                            identity.organization_id,
                            project_id,
                            object_id,
                            normalized["fileName"],
                            normalized["mediaType"],
                            normalized["byteSize"],
                            normalized["contentHash"],
                            normalized["sourceKind"],
                            f"storage-object:{object_id}",
                            identity.membership_id,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO evidence_links (
                            evidence_link_id, organization_id, source_type,
                            source_id, target_type, target_id, relation_kind,
                            lifecycle_state, linked_by_membership_id, version,
                            created_at, updated_at
                        ) VALUES (?, ?, 'source_asset', ?, ?, ?, 'attachment',
                                  'active', ?, 1, ?, ?)
                        """,
                        (
                            new_id(),
                            identity.organization_id,
                            source_asset_id,
                            target_type,
                            target_id,
                            identity.membership_id,
                            now,
                            now,
                        ),
                    )
                    event_attachment_id = None
                    if target_type == "event_line":
                        event_attachment_id = new_id()
                        connection.execute(
                            """
                            INSERT INTO event_line_attachments (
                                event_line_attachment_id, organization_id,
                                event_line_id, source_asset_id, title, purpose,
                                created_by_membership_id, lifecycle_state, version,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
                            """,
                            (
                                event_attachment_id,
                                identity.organization_id,
                                target_id,
                                source_asset_id,
                                normalized["title"],
                                normalized["purpose"],
                                identity.membership_id,
                                now,
                                now,
                            ),
                        )
                    table = "task_records" if target_type == "task" else "event_line_records"
                    id_column = "task_id" if target_type == "task" else "event_line_id"
                    changed = connection.execute(
                        f"""
                        UPDATE {table}
                        SET version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND {id_column} = ? AND version = ?
                        """,
                        (now, identity.organization_id, target_id, expected),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            f"{target_type}_version_conflict",
                            "附件目标已被更新，请刷新后重试",
                        )
                    attachment = {
                        "id": event_attachment_id or source_asset_id,
                        "sourceAssetId": source_asset_id,
                        "storageObjectId": object_id,
                        "name": normalized["fileName"],
                        "title": normalized["title"],
                        "mediaType": normalized["mediaType"],
                        "size": normalized["byteSize"],
                        "contentHash": normalized["contentHash"],
                        "sourceKind": normalized["sourceKind"],
                        "lifecycleState": "active",
                        "version": 1,
                        "parseStatus": "uploaded",
                        "createdAt": now,
                        "updatedAt": now,
                    }
                    result: dict[str, Any] = {"attachment": attachment}
                    if target_type == "task":
                        next_row = connection.execute(
                            "SELECT * FROM task_records WHERE task_id = ?",
                            (target_id,),
                        ).fetchone()
                        result["task"] = self._task_payload(connection, next_row)
                    else:
                        next_row = connection.execute(
                            "SELECT * FROM event_line_records WHERE event_line_id = ?",
                            (target_id,),
                        ).fetchone()
                        result["eventLine"] = self._event_payload(connection, next_row)
                    self._record(
                        connection,
                        identity,
                        command_type=command_type,
                        idempotency_key=idempotency_key,
                        aggregate_type=target_type,
                        aggregate_id=target_id,
                        expected_version=expected,
                        before_version=current,
                        after_version=current + 1,
                        payload=receipt_payload,
                        result=result,
                    )
                    connection.commit()
                    committed = True
                    return result
                except Exception:
                    connection.rollback()
                    raise
        finally:
            if not committed:
                managed_path.unlink(missing_ok=True)

    def archive_task_attachment(
        self,
        identity: SessionIdentity,
        task_id: str,
        attachment_id: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "task.attachment_archived"
        expected = self._expected(payload, code="task_expected_version_required")
        normalized = {
            "expectedVersion": expected,
            "attachmentId": attachment_id,
            "syncKnowledge": bool(payload.get("syncKnowledge")),
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                task = self.root._task_row(  # noqa: SLF001
                    connection,
                    identity,
                    task_id,
                    require_edit=True,
                )
                current = self._assert_version(
                    task,
                    expected,
                    code="task_version_conflict",
                    message="任务已被更新，请刷新后重试",
                )
                link = connection.execute(
                    """
                    SELECT el.evidence_link_id, sa.source_asset_id,
                           sa.storage_object_id, sa.version AS asset_version
                    FROM evidence_links el
                    JOIN source_assets sa ON sa.source_asset_id = el.source_id
                    WHERE el.organization_id = ? AND el.target_type = 'task'
                      AND el.target_id = ? AND el.source_type = 'source_asset'
                      AND el.source_id = ? AND el.lifecycle_state = 'active'
                      AND sa.lifecycle_state = 'active'
                    """,
                    (identity.organization_id, task_id, attachment_id),
                ).fetchone()
                if link is None:
                    raise RepositoryError(404, "task_attachment_missing", "任务附件不存在")
                now = utc_now()
                connection.execute(
                    """
                    UPDATE evidence_links
                    SET lifecycle_state = 'revoked', version = version + 1,
                        updated_at = ?
                    WHERE evidence_link_id = ?
                    """,
                    (now, link["evidence_link_id"]),
                )
                connection.execute(
                    """
                    UPDATE source_assets
                    SET lifecycle_state = 'archived', version = version + 1,
                        updated_at = ?
                    WHERE organization_id = ? AND source_asset_id = ?
                    """,
                    (now, identity.organization_id, attachment_id),
                )
                if link["storage_object_id"]:
                    connection.execute(
                        """
                        UPDATE storage_objects
                        SET lifecycle_state = 'archived', version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND object_id = ?
                        """,
                        (
                            now,
                            identity.organization_id,
                            link["storage_object_id"],
                        ),
                    )
                if bool(payload.get("syncKnowledge")):
                    connection.execute(
                        """
                        UPDATE knowledge_documents
                        SET lifecycle_state = 'archived', version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND source_asset_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (now, identity.organization_id, attachment_id),
                    )
                connection.execute(
                    """
                    UPDATE task_records
                    SET version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND task_id = ? AND version = ?
                    """,
                    (now, identity.organization_id, task_id, expected),
                )
                result = {
                    "deleted": True,
                    "knowledgeDeleted": bool(payload.get("syncKnowledge")),
                    "fileDeleted": False,
                    "lifecycleState": "archived",
                }
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    expected_version=expected,
                    before_version=current,
                    after_version=current + 1,
                    payload=normalized,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _attachment_bytes(
        self,
        connection: sqlite3.Connection,
        asset: sqlite3.Row,
    ) -> bytes:
        storage = connection.execute(
            """
            SELECT storage_key, content_hash, byte_size
            FROM storage_objects
            WHERE object_id = ? AND organization_id = ?
              AND lifecycle_state = 'active'
            """,
            (
                asset["storage_object_id"],
                asset["organization_id"],
            ),
        ).fetchone()
        if storage is None:
            raise RepositoryError(
                409,
                "attachment_source_missing",
                "附件受管原件不存在，无法解析",
            )
        data_root = self.root.database_path.resolve().parent
        managed_root = (data_root / "workflow-objects").resolve()
        source = (data_root / str(storage["storage_key"])).resolve()
        if managed_root not in source.parents:
            raise RepositoryError(
                409,
                "attachment_storage_path_invalid",
                "附件受管路径越界，已阻止解析",
            )
        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise RepositoryError(
                409,
                "attachment_source_missing",
                "附件受管原件无法读取",
            ) from exc
        if (
            len(raw) != int(storage["byte_size"])
            or hashlib.sha256(raw).hexdigest() != str(storage["content_hash"])
            or hashlib.sha256(raw).hexdigest() != str(asset["content_hash"])
        ):
            raise RepositoryError(
                409,
                "attachment_source_hash_mismatch",
                "附件受管原件校验失败，未生成解析结果",
            )
        return raw

    def _attachment_text(
        self,
        connection: sqlite3.Connection,
        asset: sqlite3.Row,
    ) -> str:
        raw = self._attachment_bytes(connection, asset)
        suffix = Path(str(asset["file_name"])).suffix.lower()
        media_type = _text(asset["media_type"]).lower()
        if suffix == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as package:
                    document_xml = package.read("word/document.xml")
                root = ElementTree.fromstring(document_xml)
            except (
                OSError,
                KeyError,
                zipfile.BadZipFile,
                ElementTree.ParseError,
            ) as exc:
                raise RepositoryError(
                    422,
                    "attachment_document_invalid",
                    "Word 附件结构无效，无法解析",
                ) from exc
            namespace = (
                "{http://schemas.openxmlformats.org/"
                "wordprocessingml/2006/main}"
            )
            paragraphs = []
            for paragraph in root.iter(f"{namespace}p"):
                text = "".join(
                    node.text or ""
                    for node in paragraph.iter(f"{namespace}t")
                )
                if text.strip():
                    paragraphs.append(text.strip())
            content = "\n\n".join(paragraphs)
        elif media_type.startswith("text/") or suffix in {
            ".md",
            ".txt",
            ".json",
            ".csv",
            ".tsv",
            ".xml",
            ".html",
        }:
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RepositoryError(
                    415,
                    "attachment_encoding_unsupported",
                    "附件不是 UTF-8 文本，无法安全解析",
                ) from exc
        else:
            raise RepositoryError(
                415,
                "attachment_parse_format_unsupported",
                "当前严格解析器仅支持 UTF-8 文本、Markdown 和 Word",
            )
        normalized = content.strip()
        if not normalized:
            raise RepositoryError(
                422,
                "attachment_parse_empty",
                "附件没有可解析正文",
            )
        return normalized[:500_000]

    def queue_attachment_processing(
        self,
        identity: SessionIdentity,
        *,
        target_type: str,
        target_id: str,
        attachment_id: str,
        processing_kind: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if processing_kind == "transcription":
            raise RepositoryError(
                409,
                f"attachment_{processing_kind}_executor_not_connected",
                (
                    "当前严格新版没有可消费该附件处理任务的执行器；"
                    "未创建假的 queued 处理记录"
                ),
            )
        command_type = (
            f"{target_type}.attachment_{processing_kind}_completed"
            if processing_kind == "parse"
            else f"{target_type}.attachment_{processing_kind}_queued"
        )
        expected = self._expected(payload, code="attachment_expected_version_required")
        normalized = {
            "attachmentId": attachment_id,
            "processingKind": processing_kind,
            "expectedVersion": expected,
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                event_attachment = None
                if target_type == "task":
                    self.root._task_row(  # noqa: SLF001
                        connection,
                        identity,
                        target_id,
                        require_edit=True,
                    )
                    asset = connection.execute(
                        """
                        SELECT sa.*
                        FROM evidence_links el
                        JOIN source_assets sa ON sa.source_asset_id = el.source_id
                        WHERE el.organization_id = ? AND el.target_type = 'task'
                          AND el.target_id = ? AND el.source_id = ?
                          AND el.lifecycle_state = 'active'
                          AND sa.lifecycle_state = 'active'
                        """,
                        (identity.organization_id, target_id, attachment_id),
                    ).fetchone()
                elif target_type == "event_line":
                    self._event_row(
                        connection,
                        identity,
                        target_id,
                        require_edit=True,
                    )
                    event_attachment = connection.execute(
                        """
                        SELECT * FROM event_line_attachments
                        WHERE organization_id = ? AND event_line_id = ?
                          AND event_line_attachment_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, target_id, attachment_id),
                    ).fetchone()
                    asset = (
                        connection.execute(
                            """
                            SELECT * FROM source_assets
                            WHERE organization_id = ? AND source_asset_id = ?
                              AND lifecycle_state = 'active'
                            """,
                            (
                                identity.organization_id,
                                event_attachment["source_asset_id"],
                            ),
                        ).fetchone()
                        if event_attachment is not None
                        else None
                    )
                else:
                    raise RepositoryError(422, "attachment_target_invalid", "附件目标无效")
                if asset is None:
                    raise RepositoryError(404, "attachment_missing", "附件不存在")
                version_row = event_attachment or asset
                current = self._assert_version(
                    version_row,
                    expected,
                    code="attachment_version_conflict",
                    message="附件已被更新，请刷新后重试",
                )
                latest_attempt = connection.execute(
                    """
                    SELECT MAX(attempt_no) AS attempt_no
                    FROM processing_attempts
                    WHERE organization_id = ? AND source_asset_id = ?
                      AND processing_kind = ?
                    """,
                    (
                        identity.organization_id,
                        asset["source_asset_id"],
                        processing_kind,
                    ),
                ).fetchone()
                attempt_no = int(latest_attempt["attempt_no"] or 0) + 1
                attempt_id = new_id()
                now = utc_now()
                document_id: str | None = None
                parsed_text = ""
                parse_error: RepositoryError | None = None
                if processing_kind == "parse":
                    try:
                        parsed_text = self._attachment_text(connection, asset)
                    except RepositoryError as exc:
                        parse_error = exc
                    if parse_error is None:
                        document = connection.execute(
                            """
                            SELECT *
                            FROM knowledge_documents
                            WHERE organization_id = ? AND source_asset_id = ?
                              AND lifecycle_state = 'active'
                            ORDER BY updated_at DESC, document_id
                            LIMIT 1
                            """,
                            (
                                identity.organization_id,
                                asset["source_asset_id"],
                            ),
                        ).fetchone()
                        content_hash = sha256_text(parsed_text)
                        if document is None:
                            document_id = new_id()
                            document_version = 1
                            document_aggregate_version = 1
                            project_id = asset["project_id"]
                            connection.execute(
                                """
                                INSERT INTO knowledge_documents (
                                    document_id, organization_id, project_id,
                                    project_assignment_state, source_asset_id,
                                    owner_membership_id, department_id, title,
                                    document_kind, visibility_scope,
                                    parse_state, lifecycle_state,
                                    current_version, version,
                                    created_at, updated_at
                                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?,
                                          'participants', 'ready', 'active',
                                          1, 1, ?, ?)
                                """,
                                (
                                    document_id,
                                    identity.organization_id,
                                    project_id,
                                    (
                                        "assigned"
                                        if project_id
                                        else "unassigned"
                                    ),
                                    asset["source_asset_id"],
                                    asset["created_by_membership_id"],
                                    asset["file_name"],
                                    f"{target_type}_attachment_text",
                                    now,
                                    now,
                                ),
                            )
                        else:
                            document_id = str(document["document_id"])
                            document_version = (
                                int(document["current_version"]) + 1
                            )
                            document_aggregate_version = (
                                int(document["version"]) + 1
                            )
                            connection.execute(
                                """
                                UPDATE knowledge_documents
                                SET title = ?, document_kind = ?,
                                    parse_state = 'ready',
                                    current_version = ?, version = ?,
                                    updated_at = ?
                                WHERE document_id = ? AND organization_id = ?
                                  AND version = ?
                                """,
                                (
                                    asset["file_name"],
                                    f"{target_type}_attachment_text",
                                    document_version,
                                    document_aggregate_version,
                                    now,
                                    document_id,
                                    identity.organization_id,
                                    int(document["version"]),
                                ),
                            )
                        connection.execute(
                            """
                            INSERT INTO document_versions (
                                document_version_id, organization_id,
                                document_id, version, content_hash,
                                preview_text, markdown_content,
                                section_count, chunk_count,
                                generator_version, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                                      'strict_attachment_parser_v2', ?)
                            """,
                            (
                                new_id(),
                                identity.organization_id,
                                document_id,
                                document_version,
                                content_hash,
                                parsed_text[:2000],
                                parsed_text,
                                max(
                                    len(
                                        [
                                            value
                                            for value in re.split(
                                                r"\n\s*\n",
                                                parsed_text,
                                            )
                                            if value.strip()
                                        ]
                                    ),
                                    1,
                                ),
                                max(
                                    (len(parsed_text) + 1999) // 2000,
                                    1,
                                ),
                                now,
                            ),
                        )
                connection.execute(
                    """
                    INSERT INTO processing_attempts (
                        processing_attempt_id, organization_id, source_asset_id,
                        document_id, processing_kind, state, attempt_no,
                        error_code, error_message, started_at, finished_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        identity.organization_id,
                        asset["source_asset_id"],
                        document_id,
                        processing_kind,
                        (
                            "failed"
                            if parse_error is not None
                            else (
                                "completed"
                                if processing_kind == "parse"
                                else "queued"
                            )
                        ),
                        attempt_no,
                        parse_error.code if parse_error is not None else "",
                        (
                            parse_error.message
                            if parse_error is not None
                            else ""
                        ),
                        now if processing_kind == "parse" else None,
                        now if processing_kind == "parse" else None,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE source_assets
                    SET version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND source_asset_id = ?
                    """,
                    (now, identity.organization_id, asset["source_asset_id"]),
                )
                if event_attachment is not None:
                    connection.execute(
                        """
                        UPDATE event_line_attachments
                        SET version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND event_line_attachment_id = ?
                          AND version = ?
                        """,
                        (
                            now,
                            identity.organization_id,
                            attachment_id,
                            expected,
                        ),
                    )
                result = {
                    "status": (
                        "failed"
                        if parse_error is not None
                        else (
                            "completed"
                            if processing_kind == "parse"
                            else "queued"
                        )
                    ),
                    "state": (
                        "blocked"
                        if parse_error is not None
                        and parse_error.status_code < 500
                        else "failed_retryable"
                        if parse_error is not None
                        else "ready"
                        if processing_kind == "parse"
                        else "processing"
                    ),
                    "attachmentId": attachment_id,
                    "sourceAssetId": asset["source_asset_id"],
                    "documentId": document_id,
                    "jobId": attempt_id,
                    "attemptNo": attempt_no,
                    "version": current + 1,
                    "errorCode": (
                        parse_error.code
                        if parse_error is not None
                        else None
                    ),
                    "errorMessage": (
                        parse_error.message
                        if parse_error is not None
                        else None
                    ),
                }
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="source_asset",
                    aggregate_id=str(asset["source_asset_id"]),
                    expected_version=expected,
                    before_version=current,
                    after_version=current + 1,
                    payload=normalized,
                    result=result,
                )
                if document_id is not None:
                    operation = connection.execute(
                        """
                        SELECT operation_id
                        FROM command_envelopes
                        WHERE scope_id = ? AND actor_principal_id = ?
                          AND command_type = ? AND idempotency_key = ?
                        """,
                        (
                            identity.scope_id,
                            identity.principal_id,
                            command_type,
                            idempotency_key,
                        ),
                    ).fetchone()
                    self.root._insert_audit(  # noqa: SLF001
                        connection,
                        scope_id=identity.scope_id,
                        organization_id=identity.organization_id,
                        operation_id=str(operation["operation_id"]),
                        actor_id=identity.principal_id,
                        action="knowledge_document.attachment_parsed",
                        resource_type="knowledge_document",
                        resource_id=document_id,
                        before_version=(
                            int(document["version"])
                            if document is not None
                            else None
                        ),
                        after_version=document_aggregate_version,
                        summary={
                            "contentHash": sha256_text(parsed_text),
                            "sourceAssetId": asset["source_asset_id"],
                        },
                    )
                    self.root._insert_outbox(  # noqa: SLF001
                        connection,
                        scope_id=identity.scope_id,
                        organization_id=identity.organization_id,
                        operation_id=str(operation["operation_id"]),
                        aggregate_type="knowledge_document",
                        aggregate_id=document_id,
                        aggregate_version=document_aggregate_version,
                        event_type="knowledge_document.attachment_parsed",
                        payload={
                            "documentId": document_id,
                            "sourceAssetId": asset["source_asset_id"],
                            "contentVersion": document_version,
                        },
                    )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def task_attachment_content(
        self,
        identity: SessionIdentity,
        task_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            self.root._task_row(  # noqa: SLF001
                connection,
                identity,
                task_id,
                require_edit=True,
            )
            asset = connection.execute(
                """
                SELECT sa.*
                FROM evidence_links el
                JOIN source_assets sa ON sa.source_asset_id = el.source_id
                WHERE el.organization_id = ? AND el.target_type = 'task'
                  AND el.target_id = ? AND el.source_id = ?
                  AND el.lifecycle_state = 'active'
                  AND sa.lifecycle_state = 'active'
                """,
                (identity.organization_id, task_id, attachment_id),
            ).fetchone()
            if asset is None:
                raise RepositoryError(
                    404,
                    "task_attachment_missing",
                    "任务附件不存在",
                )
            raw = self._attachment_bytes(connection, asset)
            return {
                "attachmentId": attachment_id,
                "fileName": asset["file_name"],
                "mediaType": asset["media_type"],
                "byteSize": len(raw),
                "contentHash": hashlib.sha256(raw).hexdigest(),
                "contentBase64": base64.b64encode(raw).decode("ascii"),
                "version": int(asset["version"]),
                "sourceBoundary": "organization_managed_attachment",
            }

    def complete_task_transcription(
        self,
        identity: SessionIdentity,
        task_id: str,
        attachment_id: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        expected = self._expected(
            payload,
            code="attachment_expected_version_required",
        )
        text = str(payload.get("text") or "").strip()
        if not text:
            raise RepositoryError(
                422,
                "transcription_text_required",
                "本机转写没有生成可保存文本",
            )
        normalized = {
            "attachmentId": attachment_id,
            "expectedVersion": expected,
            "textHash": sha256_text(text),
            "textLength": len(text),
            "modelName": _text(payload.get("modelName"))
            or "device_local_asr",
            "language": _text(payload.get("language")) or "auto",
            "segmentCount": max(
                _integer(payload.get("segmentCount")),
                0,
            ),
        }
        command_type = "task.attachment_transcription_completed"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self.root._task_row(  # noqa: SLF001
                    connection,
                    identity,
                    task_id,
                    require_edit=True,
                )
                asset = connection.execute(
                    """
                    SELECT sa.*
                    FROM evidence_links el
                    JOIN source_assets sa ON sa.source_asset_id = el.source_id
                    WHERE el.organization_id = ? AND el.target_type = 'task'
                      AND el.target_id = ? AND el.source_id = ?
                      AND el.lifecycle_state = 'active'
                      AND sa.lifecycle_state = 'active'
                    """,
                    (identity.organization_id, task_id, attachment_id),
                ).fetchone()
                if asset is None:
                    raise RepositoryError(
                        404,
                        "task_attachment_missing",
                        "任务附件不存在",
                    )
                current = self._assert_version(
                    asset,
                    expected,
                    code="attachment_version_conflict",
                    message="附件已被更新，请刷新后重试",
                )
                document = connection.execute(
                    """
                    SELECT *
                    FROM knowledge_documents
                    WHERE organization_id = ? AND source_asset_id = ?
                      AND lifecycle_state = 'active'
                    ORDER BY updated_at DESC, document_id
                    LIMIT 1
                    """,
                    (identity.organization_id, attachment_id),
                ).fetchone()
                now = utc_now()
                content_hash = sha256_text(text)
                if document is None:
                    document_id = new_id()
                    content_version = 1
                    document_version = 1
                    project_id = asset["project_id"]
                    connection.execute(
                        """
                        INSERT INTO knowledge_documents (
                            document_id, organization_id, project_id,
                            project_assignment_state, source_asset_id,
                            owner_membership_id, department_id, title,
                            document_kind, visibility_scope, parse_state,
                            lifecycle_state, current_version, version,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?,
                                  'task_transcript', 'participants', 'ready',
                                  'active', 1, 1, ?, ?)
                        """,
                        (
                            document_id,
                            identity.organization_id,
                            project_id,
                            "assigned" if project_id else "unassigned",
                            attachment_id,
                            asset["created_by_membership_id"],
                            f"{asset['file_name']} 转写",
                            now,
                            now,
                        ),
                    )
                else:
                    document_id = str(document["document_id"])
                    content_version = int(document["current_version"]) + 1
                    document_version = int(document["version"]) + 1
                    connection.execute(
                        """
                        UPDATE knowledge_documents
                        SET title = ?, document_kind = 'task_transcript',
                            parse_state = 'ready', current_version = ?,
                            version = ?, updated_at = ?
                        WHERE organization_id = ? AND document_id = ?
                          AND version = ?
                        """,
                        (
                            f"{asset['file_name']} 转写",
                            content_version,
                            document_version,
                            now,
                            identity.organization_id,
                            document_id,
                            int(document["version"]),
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO document_versions (
                        document_version_id, organization_id, document_id,
                        version, content_hash, preview_text, markdown_content,
                        section_count, chunk_count, generator_version,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        document_id,
                        content_version,
                        content_hash,
                        text[:2000],
                        text,
                        max((len(text) + 1999) // 2000, 1),
                        normalized["modelName"],
                        now,
                    ),
                )
                attempt_no = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(attempt_no), 0)
                        FROM processing_attempts
                        WHERE organization_id = ? AND source_asset_id = ?
                          AND processing_kind = 'transcription'
                        """,
                        (identity.organization_id, attachment_id),
                    ).fetchone()[0]
                ) + 1
                attempt_id = new_id()
                connection.execute(
                    """
                    INSERT INTO processing_attempts (
                        processing_attempt_id, organization_id,
                        source_asset_id, document_id, processing_kind, state,
                        attempt_no, error_code, error_message, started_at,
                        finished_at, created_at
                    ) VALUES (?, ?, ?, ?, 'transcription', 'completed', ?,
                              '', '', ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        identity.organization_id,
                        attachment_id,
                        document_id,
                        attempt_no,
                        now,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE source_assets
                    SET version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND source_asset_id = ?
                      AND version = ?
                    """,
                    (
                        now,
                        identity.organization_id,
                        attachment_id,
                        expected,
                    ),
                )
                result = {
                    "status": "completed",
                    "state": "ready",
                    "attachmentId": attachment_id,
                    "transcriptAttachmentId": attachment_id,
                    "transcriptDocumentId": document_id,
                    "contentVersion": content_version,
                    "documentVersion": document_version,
                    "jobId": attempt_id,
                    "version": current + 1,
                    "textHash": content_hash,
                }
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="source_asset",
                    aggregate_id=attachment_id,
                    expected_version=expected,
                    before_version=current,
                    after_version=current + 1,
                    payload=normalized,
                    result=result,
                )
                operation = connection.execute(
                    """
                    SELECT operation_id
                    FROM command_envelopes
                    WHERE scope_id = ? AND actor_principal_id = ?
                      AND command_type = ? AND idempotency_key = ?
                    """,
                    (
                        identity.scope_id,
                        identity.principal_id,
                        command_type,
                        idempotency_key,
                    ),
                ).fetchone()
                self.root._insert_audit(  # noqa: SLF001
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=str(operation["operation_id"]),
                    actor_id=identity.principal_id,
                    action="knowledge_document.transcription_completed",
                    resource_type="knowledge_document",
                    resource_id=document_id,
                    before_version=(
                        int(document["version"])
                        if document is not None
                        else None
                    ),
                    after_version=document_version,
                    summary={
                        "contentHash": content_hash,
                        "sourceAssetId": attachment_id,
                        "modelName": normalized["modelName"],
                    },
                )
                self.root._insert_outbox(  # noqa: SLF001
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=str(operation["operation_id"]),
                    aggregate_type="knowledge_document",
                    aggregate_id=document_id,
                    aggregate_version=document_version,
                    event_type="knowledge_document.transcription_completed",
                    payload={
                        "documentId": document_id,
                        "sourceAssetId": attachment_id,
                        "contentVersion": content_version,
                    },
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def task_transcript(
        self,
        identity: SessionIdentity,
        task_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            self.root._task_row(  # noqa: SLF001
                connection,
                identity,
                task_id,
                require_edit=False,
            )
            link = connection.execute(
                """
                SELECT 1 FROM evidence_links
                WHERE organization_id = ? AND target_type = 'task'
                  AND target_id = ? AND source_type = 'source_asset'
                  AND source_id = ? AND lifecycle_state = 'active'
                """,
                (identity.organization_id, task_id, attachment_id),
            ).fetchone()
            if link is None:
                raise RepositoryError(404, "task_attachment_missing", "任务附件不存在")
            document = connection.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE organization_id = ? AND source_asset_id = ?
                  AND lifecycle_state = 'active'
                ORDER BY updated_at DESC, document_id
                LIMIT 1
                """,
                (identity.organization_id, attachment_id),
            ).fetchone()
            if document is None or int(document["current_version"]) < 1:
                raise RepositoryError(404, "task_transcript_missing", "该附件尚无转写文本")
            first = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE organization_id = ? AND document_id = ?
                ORDER BY version ASC LIMIT 1
                """,
                (identity.organization_id, document["document_id"]),
            ).fetchone()
            latest = connection.execute(
                """
                SELECT * FROM document_versions
                WHERE organization_id = ? AND document_id = ? AND version = ?
                """,
                (
                    identity.organization_id,
                    document["document_id"],
                    document["current_version"],
                ),
            ).fetchone()
            return {
                "transcript": {
                    "sourceAttachmentId": attachment_id,
                    "transcriptAttachmentId": attachment_id,
                    "transcriptDocumentId": document["document_id"],
                    "originalText": first["markdown_content"] if first else "",
                    "currentText": latest["markdown_content"] if latest else "",
                    "version": int(document["current_version"]),
                    "documentVersion": int(document["version"]),
                }
            }

    def update_task_transcript(
        self,
        identity: SessionIdentity,
        task_id: str,
        attachment_id: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "task.transcript_updated"
        task_expected = self._expected(payload, code="task_expected_version_required")
        transcript_expected = _integer(payload.get("expectedTranscriptVersion"))
        text = str(payload.get("text") or "")
        if transcript_expected < 1:
            raise RepositoryError(
                428,
                "transcript_expected_version_required",
                "更新转写必须携带当前文本版本",
            )
        normalized = {
            "attachmentId": attachment_id,
            "expectedVersion": task_expected,
            "expectedTranscriptVersion": transcript_expected,
            "textHash": sha256_text(text),
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                task = self.root._task_row(  # noqa: SLF001
                    connection,
                    identity,
                    task_id,
                    require_edit=True,
                )
                task_current = self._assert_version(
                    task,
                    task_expected,
                    code="task_version_conflict",
                    message="任务已被更新，请刷新后重试",
                )
                document = connection.execute(
                    """
                    SELECT kd.*
                    FROM evidence_links el
                    JOIN knowledge_documents kd ON kd.source_asset_id = el.source_id
                    WHERE el.organization_id = ? AND el.target_type = 'task'
                      AND el.target_id = ? AND el.source_id = ?
                      AND el.lifecycle_state = 'active'
                      AND kd.lifecycle_state = 'active'
                    ORDER BY kd.updated_at DESC, kd.document_id
                    LIMIT 1
                    """,
                    (identity.organization_id, task_id, attachment_id),
                ).fetchone()
                if document is None:
                    raise RepositoryError(404, "task_transcript_missing", "该附件尚无转写文本")
                if int(document["current_version"]) != transcript_expected:
                    raise RepositoryError(
                        409,
                        "transcript_version_conflict",
                        "转写文本已被更新，请刷新后重试",
                    )
                next_transcript_version = transcript_expected + 1
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO document_versions (
                        document_version_id, organization_id, document_id,
                        version, content_hash, preview_text, markdown_content,
                        section_count, chunk_count, generator_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, 'manual-transcript-v2', ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        document["document_id"],
                        next_transcript_version,
                        sha256_text(text),
                        text[:1000],
                        text,
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE knowledge_documents
                    SET current_version = ?, version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND document_id = ?
                      AND current_version = ?
                    """,
                    (
                        next_transcript_version,
                        now,
                        identity.organization_id,
                        document["document_id"],
                        transcript_expected,
                    ),
                )
                connection.execute(
                    """
                    UPDATE task_records
                    SET version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND task_id = ? AND version = ?
                    """,
                    (now, identity.organization_id, task_id, task_expected),
                )
                result = {
                    "transcript": {
                        "sourceAttachmentId": attachment_id,
                        "transcriptAttachmentId": attachment_id,
                        "transcriptDocumentId": document["document_id"],
                        "originalText": "",
                        "currentText": text,
                        "version": next_transcript_version,
                        "documentVersion": int(document["version"]) + 1,
                    }
                }
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    expected_version=task_expected,
                    before_version=task_current,
                    after_version=task_current + 1,
                    payload=normalized,
                    result=result,
                )
                operation = connection.execute(
                    """
                    SELECT operation_id FROM command_envelopes
                    WHERE scope_id = ? AND actor_principal_id = ?
                      AND command_type = ? AND idempotency_key = ?
                    """,
                    (
                        identity.scope_id,
                        identity.principal_id,
                        command_type,
                        idempotency_key,
                    ),
                ).fetchone()
                self.root._insert_audit(  # noqa: SLF001
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=str(operation["operation_id"]),
                    actor_id=identity.principal_id,
                    action="knowledge_document.transcript_updated",
                    resource_type="knowledge_document",
                    resource_id=str(document["document_id"]),
                    before_version=int(document["version"]),
                    after_version=int(document["version"]) + 1,
                    summary={"contentHash": sha256_text(text)},
                )
                self.root._insert_outbox(  # noqa: SLF001
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=str(operation["operation_id"]),
                    aggregate_type="knowledge_document",
                    aggregate_id=str(document["document_id"]),
                    aggregate_version=int(document["version"]) + 1,
                    event_type="knowledge_document.transcript_updated",
                    payload={
                        "documentId": document["document_id"],
                        "version": int(document["version"]) + 1,
                        "contentVersion": next_transcript_version,
                    },
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def task_action(
        self,
        identity: SessionIdentity,
        task_id: str,
        *,
        action: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = f"task.{action}"
        expected = self._expected(payload, code="task_expected_version_required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self.root._task_row(  # noqa: SLF001
                    connection,
                    identity,
                    task_id,
                    require_edit=True,
                )
                current = self._assert_version(
                    row,
                    expected,
                    code="task_version_conflict",
                    message="任务已被更新，请刷新后重试",
                )
                policy = None
                if action not in {"confirmed", "rejected"}:
                    policy = self.root._task_control_rule(  # noqa: SLF001
                        connection,
                        identity,
                        row,
                        action=(
                            "cancel"
                            if action == "cancelled"
                            else "approve"
                            if action in {"review_approved", "review_returned"}
                            else "content"
                        ),
                    )
                now = utc_now()
                attributes = json.loads(str(row["attributes_json"] or "{}"))
                archive_returned_task = False
                lifecycle_override = None
                if action == "confirmed":
                    collaborator = connection.execute(
                        """
                        SELECT collaborator_role FROM task_collaborators
                        WHERE organization_id = ? AND task_id = ? AND membership_id = ?
                          AND inbox_state = 'pending'
                        """,
                        (identity.organization_id, task_id, identity.membership_id),
                    ).fetchone()
                    if collaborator is None:
                        raise RepositoryError(409, "task_confirmation_not_pending", "该任务无需确认")
                    inbox_state = (
                        "accepted"
                        if collaborator["collaborator_role"] == "owner"
                        else "acknowledged"
                    )
                    connection.execute(
                        """
                        UPDATE task_collaborators
                        SET inbox_state = ?, handled_at = ?, version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND task_id = ? AND membership_id = ?
                        """,
                        (
                            inbox_state,
                            now,
                            now,
                            identity.organization_id,
                            task_id,
                            identity.membership_id,
                        ),
                    )
                elif action == "rejected":
                    reason = _text(payload.get("reason"))
                    if not reason:
                        raise RepositoryError(422, "task_reject_reason_required", "请填写退回原因")
                    collaborator = connection.execute(
                        """
                        SELECT collaborator_role FROM task_collaborators
                        WHERE organization_id = ? AND task_id = ? AND membership_id = ?
                          AND inbox_state != 'returned'
                        """,
                        (identity.organization_id, task_id, identity.membership_id),
                    ).fetchone()
                    if collaborator is None:
                        raise RepositoryError(403, "task_reject_forbidden", "无权退回该任务")
                    connection.execute(
                        """
                        UPDATE task_collaborators
                        SET inbox_state = 'returned', return_reason = ?, handled_at = ?,
                            version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND task_id = ? AND membership_id = ?
                        """,
                        (
                            reason,
                            now,
                            now,
                            identity.organization_id,
                            task_id,
                            identity.membership_id,
                        ),
                    )
                    if collaborator["collaborator_role"] == "owner":
                        connection.execute(
                            """
                            INSERT INTO task_return_notices (
                                notice_id, organization_id, deleted_task_id, task_title,
                                creator_membership_id, returned_by_membership_id,
                                return_reason, read_at, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                            ON CONFLICT (organization_id, deleted_task_id) DO UPDATE SET
                                returned_by_membership_id = excluded.returned_by_membership_id,
                                return_reason = excluded.return_reason,
                                read_at = NULL,
                                created_at = excluded.created_at
                            """,
                            (
                                new_id(),
                                identity.organization_id,
                                task_id,
                                row["title"],
                                row["created_by_membership_id"],
                                identity.membership_id,
                                reason,
                                now,
                            ),
                        )
                        attributes["returnState"] = "owner_returned"
                        archive_returned_task = True
                elif action == "review_approved":
                    attributes["reviewStatus"] = "approved"
                    attributes["reviewReason"] = ""
                elif action == "review_returned":
                    attributes["reviewStatus"] = "returned"
                    attributes["reviewReason"] = _text(payload.get("reason"))
                elif action == "note_saved":
                    attributes["note"] = _text(payload.get("note"))
                elif action == "smart_brief_adopted":
                    action_key = _text(payload.get("actionKey"))
                    created_task_id = _text(payload.get("createdTaskId"))
                    if not action_key or not created_task_id:
                        raise RepositoryError(
                            422,
                            "smart_brief_adoption_invalid",
                            "采纳记录缺少动作或新任务标识",
                        )
                    adopted = dict(attributes.get("adoptedSmartBriefActions") or {})
                    adopted[action_key] = {
                        "createdTaskId": created_task_id,
                        "actionText": _text(payload.get("actionText")),
                        "adoptedAt": now,
                        "adoptedByMembershipId": identity.membership_id,
                    }
                    attributes["adoptedSmartBriefActions"] = adopted
                elif action == "started":
                    if row["lifecycle_state"] not in {"todo", "in_progress"}:
                        raise RepositoryError(
                            409,
                            "task_start_invalid",
                            "当前任务状态不可开始",
                        )
                    lifecycle_override = "in_progress"
                elif action == "cancelled":
                    if row["lifecycle_state"] == "archived":
                        raise RepositoryError(
                            409,
                            "task_cancel_invalid",
                            "已归档任务不可取消",
                        )
                    lifecycle_override = "cancelled"
                else:
                    raise RepositoryError(404, "task_action_unknown", "未知任务动作")
                if archive_returned_task:
                    changed = connection.execute(
                        """
                        UPDATE task_records
                        SET attributes_json = ?, lifecycle_state = 'archived',
                            archived_at = ?, version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND task_id = ? AND version = ?
                        """,
                        (
                            canonical_json(attributes),
                            now,
                            now,
                            identity.organization_id,
                            task_id,
                            expected,
                        ),
                    )
                elif lifecycle_override is not None:
                    changed = connection.execute(
                        """
                        UPDATE task_records
                        SET attributes_json = ?, lifecycle_state = ?,
                            version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND task_id = ? AND version = ?
                        """,
                        (
                            canonical_json(attributes),
                            lifecycle_override,
                            now,
                            identity.organization_id,
                            task_id,
                            expected,
                        ),
                    )
                else:
                    changed = connection.execute(
                        """
                        UPDATE task_records
                        SET attributes_json = ?, version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND task_id = ? AND version = ?
                        """,
                        (
                            canonical_json(attributes),
                            now,
                            identity.organization_id,
                            task_id,
                            expected,
                        ),
                    )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "task_version_conflict",
                        "任务已被更新，请刷新后重试",
                    )
                connection.execute(
                    """
                    INSERT INTO task_activity_events (
                        task_activity_id, organization_id, task_id,
                        actor_membership_id, event_type, payload_json, happened_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        task_id,
                        identity.membership_id,
                        command_type,
                        canonical_json(dict(payload)),
                        now,
                    ),
                )
                next_row = connection.execute(
                    "SELECT * FROM task_records WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                result = {"task": self._task_payload(connection, next_row)}
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    expected_version=expected,
                    before_version=current,
                    after_version=current + 1,
                    payload=payload,
                    result=result,
                    policy_evidence=[policy] if policy is not None else None,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _event_row(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        event_line_id: str,
        *,
        require_edit: bool,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT * FROM event_line_records
            WHERE organization_id = ? AND event_line_id = ?
            """,
            (identity.organization_id, event_line_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "event_line_missing", "事件线不存在")
        participant = connection.execute(
            """
            SELECT 1 FROM event_line_participants
            WHERE organization_id = ? AND event_line_id = ? AND membership_id = ?
              AND status = 'active'
            """,
            (identity.organization_id, event_line_id, identity.membership_id),
        ).fetchone()
        department_member = (
            connection.execute(
                """
                SELECT 1 FROM department_memberships
                WHERE organization_id = ? AND department_id = ?
                  AND membership_id = ? AND status = 'active'
                """,
                (
                    identity.organization_id,
                    row["department_id"],
                    identity.membership_id,
                ),
            ).fetchone()
            if row["department_id"]
            else None
        )
        creator = row["created_by_membership_id"] == identity.membership_id
        permitted = (
            identity.is_admin
            or creator
            or participant is not None
            or (
                not require_edit
                and (
                    identity.visibility_scope == "organization"
                    or row["visibility_scope"] == "organization"
                    or department_member is not None
                )
            )
        )
        if not permitted:
            raise RepositoryError(403, "event_line_forbidden", "无权访问该事件线")
        return row

    def _event_payload(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        participants = [
            item["membership_id"]
            for item in connection.execute(
                """
                SELECT membership_id FROM event_line_participants
                WHERE organization_id = ? AND event_line_id = ? AND status = 'active'
                ORDER BY membership_id
                """,
                (row["organization_id"], row["event_line_id"]),
            ).fetchall()
        ]
        counts = connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM event_line_task_links
               WHERE organization_id = ? AND event_line_id = ? AND link_state = 'active')
                AS task_count,
              (SELECT COUNT(*) FROM event_line_task_links
               WHERE organization_id = ? AND event_line_id = ?
                 AND link_state = 'active' AND is_milestone = 1) AS milestone_count,
              (SELECT COUNT(*) FROM event_line_attachments
               WHERE organization_id = ? AND event_line_id = ?
                 AND lifecycle_state = 'active') AS attachment_count
            """,
            (
                row["organization_id"],
                row["event_line_id"],
                row["organization_id"],
                row["event_line_id"],
                row["organization_id"],
                row["event_line_id"],
            ),
        ).fetchone()
        return {
            "eventLineId": row["event_line_id"],
            "projectId": row["project_id"],
            "projectAssignmentState": row["project_assignment_state"],
            "createdByMembershipId": row["created_by_membership_id"],
            "departmentId": row["department_id"],
            "name": row["name"],
            "goal": row["goal"],
            "background": row["background"],
            "visibilityScope": row["visibility_scope"],
            "lifecycleState": row["lifecycle_state"],
            "participantMembershipIds": participants,
            "version": int(row["version"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "taskCount": int(counts["task_count"]),
            "milestoneCount": int(counts["milestone_count"]),
            "attachmentCount": int(counts["attachment_count"]),
        }

    def event_lines(self, identity: SessionIdentity) -> dict[str, Any]:
        visible = self.root.business_snapshot(identity)
        visible_ids = {
            _text(item.get("eventLineId")) for item in visible.get("eventLines") or []
        }
        with self._connection() as connection:
            if not visible_ids:
                return {"eventLines": []}
            placeholders = ",".join("?" for _ in visible_ids)
            rows = connection.execute(
                f"""
                SELECT * FROM event_line_records
                WHERE organization_id = ? AND event_line_id IN ({placeholders})
                ORDER BY updated_at DESC, event_line_id
                """,
                (identity.organization_id, *sorted(visible_ids)),
            ).fetchall()
            return {
                "eventLines": [self._event_payload(connection, row) for row in rows]
            }

    def event_detail(
        self,
        identity: SessionIdentity,
        event_line_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            row = self._event_row(
                connection,
                identity,
                event_line_id,
                require_edit=False,
            )
            task_rows = connection.execute(
                """
                SELECT t.*, l.is_milestone, l.milestone_order, l.version AS link_version
                FROM event_line_task_links l
                JOIN task_records t ON t.task_id = l.task_id
                WHERE l.organization_id = ? AND l.event_line_id = ?
                  AND l.link_state = 'active'
                ORDER BY l.is_milestone DESC, l.milestone_order, t.updated_at DESC
                """,
                (identity.organization_id, event_line_id),
            ).fetchall()
            activities = [
                {
                    "eventLineActivityId": item["event_line_activity_id"],
                    "sourceType": item["source_type"],
                    "sourceId": item["source_id"],
                    "happenedAt": item["happened_at"],
                    "actorMembershipId": item["actor_membership_id"],
                    "title": item["title"],
                    "summary": item["summary"],
                    "associationState": item["association_state"],
                    "includeInNarrative": bool(item["include_in_narrative"]),
                }
                for item in connection.execute(
                    """
                    SELECT * FROM event_line_activities
                    WHERE organization_id = ? AND event_line_id = ?
                      AND association_state != 'revoked'
                    ORDER BY happened_at DESC, event_line_activity_id
                    """,
                    (identity.organization_id, event_line_id),
                ).fetchall()
            ]
            attachments = []
            for item in connection.execute(
                    """
                    SELECT * FROM event_line_attachments
                    WHERE organization_id = ? AND event_line_id = ?
                    ORDER BY updated_at DESC, event_line_attachment_id
                    """,
                    (identity.organization_id, event_line_id),
                ).fetchall():
                attempt = connection.execute(
                    """
                    SELECT state, error_code, error_message,
                           processing_attempt_id, attempt_no
                    FROM processing_attempts
                    WHERE organization_id = ? AND source_asset_id = ?
                      AND processing_kind = 'parse'
                    ORDER BY attempt_no DESC, created_at DESC
                    LIMIT 1
                    """,
                    (identity.organization_id, item["source_asset_id"]),
                ).fetchone()
                parsed_document = connection.execute(
                    """
                    SELECT kd.document_id, dv.preview_text
                    FROM knowledge_documents kd
                    LEFT JOIN document_versions dv
                      ON dv.document_id = kd.document_id
                     AND dv.organization_id = kd.organization_id
                     AND dv.version = kd.current_version
                    WHERE kd.organization_id = ? AND kd.source_asset_id = ?
                      AND kd.lifecycle_state = 'active'
                    ORDER BY kd.updated_at DESC, kd.document_id
                    LIMIT 1
                    """,
                    (identity.organization_id, item["source_asset_id"]),
                ).fetchone()
                attachments.append(
                    {
                        "eventLineAttachmentId": item["event_line_attachment_id"],
                        "sourceAssetId": item["source_asset_id"],
                        "title": item["title"],
                        "purpose": item["purpose"],
                        "lifecycleState": item["lifecycle_state"],
                        "version": int(item["version"]),
                        "updatedAt": item["updated_at"],
                        "parseStatus": (
                            "ready"
                            if attempt is not None
                            and attempt["state"] == "completed"
                            else attempt["state"]
                            if attempt is not None
                            else "uploaded"
                        ),
                        "parseError": (
                            attempt["error_message"] if attempt is not None else None
                        ),
                        "parseJobId": (
                            attempt["processing_attempt_id"]
                            if attempt is not None
                            else None
                        ),
                        "documentId": (
                            parsed_document["document_id"]
                            if parsed_document is not None
                            else None
                        ),
                        "parsedPreview": (
                            parsed_document["preview_text"]
                            if parsed_document is not None
                            else None
                        ),
                    }
                )
            tasks = []
            for task_row in task_rows:
                task = self._task_payload(connection, task_row)
                task["eventLineMilestone"] = bool(task_row["is_milestone"])
                task["milestoneOrder"] = task_row["milestone_order"]
                task["eventLineLinkVersion"] = int(task_row["link_version"])
                tasks.append(task)
            return {
                "eventLine": self._event_payload(connection, row),
                "tasks": tasks,
                "activities": activities,
                "attachments": attachments,
            }

    def mutate_event_line(
        self,
        identity: SessionIdentity,
        event_line_id: str,
        *,
        action: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = f"event_line.{action}"
        expected = self._expected(payload, code="event_line_expected_version_required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self._event_row(
                    connection,
                    identity,
                    event_line_id,
                    require_edit=True,
                )
                current = self._assert_version(
                    row,
                    expected,
                    code="event_line_version_conflict",
                    message="事件线已被更新，请刷新后重试",
                )
                now = utc_now()
                updates: dict[str, Any] = {}
                if action == "updated":
                    field_map = {
                        "name": "name",
                        "intent": "goal",
                        "goal": "goal",
                        "summary": "background",
                        "background": "background",
                        "primaryDepartmentId": "department_id",
                        "departmentId": "department_id",
                    }
                    for source, target in field_map.items():
                        if source in payload:
                            updates[target] = payload[source]
                    if "name" in updates and not _text(updates["name"]):
                        raise RepositoryError(
                            422,
                            "event_line_name_required",
                            "请输入事件线名称",
                        )
                    if "visibilityScope" in payload:
                        visibility = _text(payload.get("visibilityScope"))
                        updates["visibility_scope"] = {
                            "project_public": "organization",
                            "private": "participants",
                        }.get(visibility, visibility)
                    participant_ids = payload.get(
                        "participantMembershipIds",
                        payload.get("participantIds"),
                    )
                    if participant_ids is not None:
                        member_ids = {_text(value) for value in participant_ids if _text(value)}
                        self.root._ensure_memberships(connection, identity, member_ids)  # noqa: SLF001
                        existing = {
                            str(item["membership_id"])
                            for item in connection.execute(
                                """
                                SELECT membership_id FROM event_line_participants
                                WHERE organization_id = ? AND event_line_id = ?
                                """,
                                (identity.organization_id, event_line_id),
                            ).fetchall()
                        }
                        for membership_id in existing | member_ids:
                            connection.execute(
                                """
                                INSERT INTO event_line_participants (
                                    event_line_id, organization_id, membership_id,
                                    status, version, created_at, updated_at
                                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                                ON CONFLICT (event_line_id, membership_id) DO UPDATE SET
                                  status = excluded.status,
                                  version = event_line_participants.version + 1,
                                  updated_at = excluded.updated_at
                                """,
                                (
                                    event_line_id,
                                    identity.organization_id,
                                    membership_id,
                                    "active" if membership_id in member_ids else "revoked",
                                    now,
                                    now,
                                ),
                            )
                elif action == "reparented":
                    project_id = _text(
                        payload.get("projectId") or payload.get("targetClientId")
                    )
                    if not project_id:
                        raise RepositoryError(
                            422,
                            "event_line_project_required",
                            "请选择目标项目",
                        )
                    self.root._ensure_project(connection, identity, project_id)  # noqa: SLF001
                    updates["project_id"] = project_id
                    updates["project_assignment_state"] = "assigned"
                elif action == "closed":
                    updates["lifecycle_state"] = "completed"
                elif action == "reopened":
                    if row["lifecycle_state"] not in {"completed", "paused"}:
                        raise RepositoryError(
                            409,
                            "event_line_reopen_invalid",
                            "当前事件线状态不可恢复",
                        )
                    updates["lifecycle_state"] = "active"
                    updates["archived_at"] = None
                elif action == "archived":
                    updates["lifecycle_state"] = "archived"
                    updates["archived_at"] = now
                else:
                    raise RepositoryError(404, "event_line_action_unknown", "未知事件线动作")
                assignments = [f"{column} = ?" for column in updates]
                values = list(updates.values())
                assignments.extend(["version = version + 1", "updated_at = ?"])
                values.extend(
                    [now, identity.organization_id, event_line_id, expected]
                )
                changed = connection.execute(
                    f"""
                    UPDATE event_line_records
                    SET {", ".join(assignments)}
                    WHERE organization_id = ? AND event_line_id = ? AND version = ?
                    """,
                    values,
                )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "event_line_version_conflict",
                        "事件线已被更新，请刷新后重试",
                    )
                next_row = connection.execute(
                    "SELECT * FROM event_line_records WHERE event_line_id = ?",
                    (event_line_id,),
                ).fetchone()
                result = {"eventLine": self._event_payload(connection, next_row)}
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="event_line",
                    aggregate_id=event_line_id,
                    expected_version=expected,
                    before_version=current,
                    after_version=current + 1,
                    payload=payload,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def link_task(
        self,
        identity: SessionIdentity,
        event_line_id: str,
        task_id: str,
        *,
        milestone_only: bool,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = (
            "event_line.task_milestone_changed"
            if milestone_only
            else "event_line.task_linked"
        )
        expected = self._expected(payload, code="event_line_expected_version_required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                event_row = self._event_row(
                    connection,
                    identity,
                    event_line_id,
                    require_edit=True,
                )
                current = self._assert_version(
                    event_row,
                    expected,
                    code="event_line_version_conflict",
                    message="事件线已被更新，请刷新后重试",
                )
                self.root._task_row(  # noqa: SLF001
                    connection,
                    identity,
                    task_id,
                    require_edit=True,
                )
                now = utc_now()
                active = connection.execute(
                    """
                    SELECT * FROM event_line_task_links
                    WHERE organization_id = ? AND task_id = ? AND link_state = 'active'
                    """,
                    (identity.organization_id, task_id),
                ).fetchone()
                if milestone_only:
                    if active is None or active["event_line_id"] != event_line_id:
                        raise RepositoryError(
                            409,
                            "event_line_task_link_missing",
                            "任务尚未关联到该事件线",
                        )
                    connection.execute(
                        """
                        UPDATE event_line_task_links
                        SET is_milestone = ?, version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND event_line_id = ? AND task_id = ?
                        """,
                        (
                            int(bool(payload.get("isMilestone"))),
                            now,
                            identity.organization_id,
                            event_line_id,
                            task_id,
                        ),
                    )
                else:
                    if (
                        active is not None
                        and active["event_line_id"] != event_line_id
                        and not bool(payload.get("allowReassign"))
                    ):
                        raise RepositoryError(
                            409,
                            "task_event_line_conflict",
                            "任务已关联其他事件线",
                        )
                    if active is not None and active["event_line_id"] != event_line_id:
                        connection.execute(
                            """
                            UPDATE event_line_task_links
                            SET link_state = 'revoked', version = version + 1,
                                updated_at = ?
                            WHERE organization_id = ? AND event_line_id = ? AND task_id = ?
                            """,
                            (
                                now,
                                identity.organization_id,
                                active["event_line_id"],
                                task_id,
                            ),
                        )
                    connection.execute(
                        """
                        INSERT INTO event_line_task_links (
                            event_line_id, task_id, organization_id, link_state,
                            is_milestone, milestone_order, linked_by_membership_id,
                            version, created_at, updated_at
                        ) VALUES (?, ?, ?, 'active', 0, NULL, ?, 1, ?, ?)
                        ON CONFLICT (event_line_id, task_id) DO UPDATE SET
                          link_state = 'active',
                          linked_by_membership_id = excluded.linked_by_membership_id,
                          version = event_line_task_links.version + 1,
                          updated_at = excluded.updated_at
                        """,
                        (
                            event_line_id,
                            task_id,
                            identity.organization_id,
                            identity.membership_id,
                            now,
                            now,
                        ),
                    )
                connection.execute(
                    """
                    UPDATE event_line_records
                    SET version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND event_line_id = ? AND version = ?
                    """,
                    (now, identity.organization_id, event_line_id, expected),
                )
                connection.execute(
                    """
                    INSERT INTO event_line_activities (
                        event_line_activity_id, organization_id, event_line_id,
                        source_type, source_id, happened_at, actor_membership_id,
                        title, summary, association_state, include_in_narrative,
                        attributes_json, created_at
                    ) VALUES (?, ?, ?, 'task', ?, ?, ?, ?, '', 'confirmed', 1, '{}', ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        event_line_id,
                        task_id,
                        now,
                        identity.membership_id,
                        "调整任务里程碑" if milestone_only else "关联任务",
                        now,
                    ),
                )
                next_event = connection.execute(
                    "SELECT * FROM event_line_records WHERE event_line_id = ?",
                    (event_line_id,),
                ).fetchone()
                task_row = connection.execute(
                    "SELECT * FROM task_records WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                result = {
                    "eventLine": self._event_payload(connection, next_event),
                    "task": self._task_payload(connection, task_row),
                }
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="event_line",
                    aggregate_id=event_line_id,
                    expected_version=expected,
                    before_version=current,
                    after_version=current + 1,
                    payload=payload,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def preview_event_merge(
        self,
        identity: SessionIdentity,
        target_id: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        normalized_sources = sorted(
            {source_id for source_id in source_ids if source_id and source_id != target_id}
        )
        if not normalized_sources:
            raise RepositoryError(422, "event_line_merge_sources_required", "请选择待合并事件线")
        with self._connection() as connection:
            target = self._event_row(
                connection,
                identity,
                target_id,
                require_edit=True,
            )
            sources = [
                self._event_row(
                    connection,
                    identity,
                    source_id,
                    require_edit=True,
                )
                for source_id in normalized_sources
            ]
            if any(row["project_id"] != target["project_id"] for row in sources):
                raise RepositoryError(
                    409,
                    "event_line_merge_project_conflict",
                    "只能合并同一项目下的事件线",
                )
            source_versions = {
                str(row["event_line_id"]): int(row["version"]) for row in sources
            }
            task_count = connection.execute(
                f"""
                SELECT COUNT(DISTINCT task_id)
                FROM event_line_task_links
                WHERE organization_id = ? AND link_state = 'active'
                  AND event_line_id IN ({",".join("?" for _ in normalized_sources)})
                """,
                (identity.organization_id, *normalized_sources),
            ).fetchone()[0]
            activity_count = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM event_line_activities
                WHERE organization_id = ? AND association_state != 'revoked'
                  AND event_line_id IN ({",".join("?" for _ in normalized_sources)})
                """,
                (identity.organization_id, *normalized_sources),
            ).fetchone()[0]
            attachment_count = connection.execute(
                f"""
                SELECT COUNT(*)
                FROM event_line_attachments
                WHERE organization_id = ? AND lifecycle_state = 'active'
                  AND event_line_id IN ({",".join("?" for _ in normalized_sources)})
                """,
                (identity.organization_id, *normalized_sources),
            ).fetchone()[0]
        return {
            "targetId": target_id,
            "sourceIds": normalized_sources,
            "targetVersion": int(target["version"]),
            "sourceExpectedVersions": source_versions,
            "taskCount": int(task_count),
            "activityCount": int(activity_count),
            "attachmentCount": int(attachment_count),
            "projectId": target["project_id"],
        }

    def merge_event_lines(
        self,
        identity: SessionIdentity,
        target_id: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "event_line.merged"
        target_expected = self._expected(
            payload,
            code="event_line_expected_version_required",
        )
        source_ids = sorted(
            {
                _text(value)
                for value in payload.get("sourceIds") or []
                if _text(value) and _text(value) != target_id
            }
        )
        raw_source_versions = payload.get("sourceExpectedVersions")
        if not isinstance(raw_source_versions, Mapping):
            raise RepositoryError(
                428,
                "event_line_source_versions_required",
                "合并必须携带每条来源事件线的版本",
            )
        source_versions = {
            source_id: _integer(raw_source_versions.get(source_id))
            for source_id in source_ids
        }
        if not source_ids or any(version < 1 for version in source_versions.values()):
            raise RepositoryError(
                428,
                "event_line_source_versions_required",
                "合并必须携带每条来源事件线的版本",
            )
        normalized = {
            "expectedVersion": target_expected,
            "sourceIds": source_ids,
            "sourceExpectedVersions": source_versions,
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                target = self._event_row(
                    connection,
                    identity,
                    target_id,
                    require_edit=True,
                )
                target_current = self._assert_version(
                    target,
                    target_expected,
                    code="event_line_version_conflict",
                    message="目标事件线已被更新，请刷新后重试",
                )
                sources = []
                for source_id in source_ids:
                    row = self._event_row(
                        connection,
                        identity,
                        source_id,
                        require_edit=True,
                    )
                    self._assert_version(
                        row,
                        source_versions[source_id],
                        code="event_line_source_version_conflict",
                        message="来源事件线已被更新，请重新预览后再合并",
                    )
                    if row["project_id"] != target["project_id"]:
                        raise RepositoryError(
                            409,
                            "event_line_merge_project_conflict",
                            "只能合并同一项目下的事件线",
                        )
                    sources.append(row)
                now = utc_now()
                for source_id in source_ids:
                    links = connection.execute(
                        """
                        SELECT * FROM event_line_task_links
                        WHERE organization_id = ? AND event_line_id = ?
                          AND link_state = 'active'
                        """,
                        (identity.organization_id, source_id),
                    ).fetchall()
                    for link in links:
                        existing = connection.execute(
                            """
                            SELECT * FROM event_line_task_links
                            WHERE organization_id = ? AND event_line_id = ?
                              AND task_id = ?
                            """,
                            (
                                identity.organization_id,
                                target_id,
                                link["task_id"],
                            ),
                        ).fetchone()
                        if existing is None:
                            connection.execute(
                                """
                                UPDATE event_line_task_links
                                SET event_line_id = ?, version = version + 1,
                                    updated_at = ?
                                WHERE organization_id = ? AND event_line_id = ?
                                  AND task_id = ? AND link_state = 'active'
                                """,
                                (
                                    target_id,
                                    now,
                                    identity.organization_id,
                                    source_id,
                                    link["task_id"],
                                ),
                            )
                        else:
                            connection.execute(
                                """
                                UPDATE event_line_task_links
                                SET link_state = 'revoked', version = version + 1,
                                    updated_at = ?
                                WHERE organization_id = ? AND event_line_id = ?
                                  AND task_id = ?
                                """,
                                (
                                    now,
                                    identity.organization_id,
                                    source_id,
                                    link["task_id"],
                                ),
                            )
                            if bool(link["is_milestone"]) and not bool(existing["is_milestone"]):
                                connection.execute(
                                    """
                                    UPDATE event_line_task_links
                                    SET is_milestone = 1, version = version + 1,
                                        updated_at = ?
                                    WHERE organization_id = ? AND event_line_id = ?
                                      AND task_id = ?
                                    """,
                                    (
                                        now,
                                        identity.organization_id,
                                        target_id,
                                        link["task_id"],
                                    ),
                                )
                    connection.execute(
                        """
                        UPDATE event_line_activities
                        SET event_line_id = ?
                        WHERE organization_id = ? AND event_line_id = ?
                        """,
                        (target_id, identity.organization_id, source_id),
                    )
                    connection.execute(
                        """
                        UPDATE event_line_attachments
                        SET event_line_id = ?, version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND event_line_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (target_id, now, identity.organization_id, source_id),
                    )
                    changed = connection.execute(
                        """
                        UPDATE event_line_records
                        SET lifecycle_state = 'archived', archived_at = ?,
                            version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND event_line_id = ? AND version = ?
                        """,
                        (
                            now,
                            now,
                            identity.organization_id,
                            source_id,
                            source_versions[source_id],
                        ),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "event_line_source_version_conflict",
                            "来源事件线已被更新，请重新预览后再合并",
                        )
                connection.execute(
                    """
                    UPDATE event_line_records
                    SET version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND event_line_id = ? AND version = ?
                    """,
                    (now, identity.organization_id, target_id, target_expected),
                )
                next_target = connection.execute(
                    "SELECT * FROM event_line_records WHERE event_line_id = ?",
                    (target_id,),
                ).fetchone()
                result = {
                    "eventLine": self._event_payload(connection, next_target),
                    "mergedSourceIds": source_ids,
                }
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="event_line",
                    aggregate_id=target_id,
                    expected_version=target_expected,
                    before_version=target_current,
                    after_version=target_current + 1,
                    payload=normalized,
                    result=result,
                )
                operation = connection.execute(
                    """
                    SELECT operation_id FROM command_envelopes
                    WHERE scope_id = ? AND actor_principal_id = ?
                      AND command_type = ? AND idempotency_key = ?
                    """,
                    (
                        identity.scope_id,
                        identity.principal_id,
                        command_type,
                        idempotency_key,
                    ),
                ).fetchone()
                for source in sources:
                    source_id = str(source["event_line_id"])
                    self.root._insert_audit(  # noqa: SLF001
                        connection,
                        scope_id=identity.scope_id,
                        organization_id=identity.organization_id,
                        operation_id=str(operation["operation_id"]),
                        actor_id=identity.principal_id,
                        action="event_line.merged_source_archived",
                        resource_type="event_line",
                        resource_id=source_id,
                        before_version=int(source["version"]),
                        after_version=int(source["version"]) + 1,
                        summary={"mergedIntoEventLineId": target_id},
                    )
                    self.root._insert_outbox(  # noqa: SLF001
                        connection,
                        scope_id=identity.scope_id,
                        organization_id=identity.organization_id,
                        operation_id=str(operation["operation_id"]),
                        aggregate_type="event_line",
                        aggregate_id=source_id,
                        aggregate_version=int(source["version"]) + 1,
                        event_type="event_line.merged_source_archived",
                        payload={
                            "eventLineId": source_id,
                            "mergedIntoEventLineId": target_id,
                            "version": int(source["version"]) + 1,
                        },
                    )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def task_plan_link(
        self,
        identity: SessionIdentity,
        task_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            row = self.root._task_row(  # noqa: SLF001
                connection,
                identity,
                task_id,
                require_edit=False,
            )
            attributes = json.loads(str(row["attributes_json"] or "{}"))
            item_id = attributes.get("departmentPlanItemId")
            focus_id = attributes.get("focusItemId")
            return {
                "planLink": (
                    {
                        "taskId": task_id,
                        "departmentPlanItemId": item_id,
                        "focusItemId": focus_id,
                        "linkedBy": attributes.get("planLinkedBy") or "manual",
                        "confidence": float(attributes.get("planLinkConfidence") or 1),
                        "updatedAt": row["updated_at"],
                        "version": int(row["version"]),
                    }
                    if item_id or focus_id
                    else None
                )
            }

    def patch_task_plan_link(
        self,
        identity: SessionIdentity,
        task_id: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "task.plan_link_updated"
        expected = self._expected(payload, code="task_expected_version_required")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self.root._task_row(  # noqa: SLF001
                    connection,
                    identity,
                    task_id,
                    require_edit=True,
                )
                current = self._assert_version(
                    row,
                    expected,
                    code="task_version_conflict",
                    message="任务已被更新，请刷新后重试",
                )
                plan_item_id = payload.get("departmentPlanItemId")
                if plan_item_id:
                    item = connection.execute(
                        """
                        SELECT 1 FROM organization_plan_items
                        WHERE organization_id = ? AND plan_item_id = ?
                          AND status = 'active'
                        """,
                        (identity.organization_id, plan_item_id),
                    ).fetchone()
                    if item is None:
                        raise RepositoryError(404, "plan_item_missing", "计划项不存在")
                attributes = json.loads(str(row["attributes_json"] or "{}"))
                attributes.update(
                    {
                        "departmentPlanItemId": plan_item_id,
                        "focusItemId": payload.get("focusItemId"),
                        "planLinkedBy": _text(payload.get("linkedBy")) or "manual",
                        "planLinkConfidence": float(payload.get("confidence") or 1),
                    }
                )
                now = utc_now()
                changed = connection.execute(
                    """
                    UPDATE task_records
                    SET attributes_json = ?, version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND task_id = ? AND version = ?
                    """,
                    (
                        canonical_json(attributes),
                        now,
                        identity.organization_id,
                        task_id,
                        expected,
                    ),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "task_version_conflict",
                        "任务已被更新，请刷新后重试",
                    )
                result = {
                    "planLink": (
                        {
                            "taskId": task_id,
                            "departmentPlanItemId": plan_item_id,
                            "focusItemId": payload.get("focusItemId"),
                            "linkedBy": attributes["planLinkedBy"],
                            "confidence": attributes["planLinkConfidence"],
                            "updatedAt": now,
                            "version": current + 1,
                        }
                        if plan_item_id or payload.get("focusItemId")
                        else None
                    )
                }
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    expected_version=expected,
                    before_version=current,
                    after_version=current + 1,
                    payload=payload,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def tasks_for_plan_item(
        self,
        identity: SessionIdentity,
        plan_item_id: str | None = None,
    ) -> dict[str, Any]:
        board = self.board(identity)
        counts: dict[str, int] = {}
        matches = []
        for task in board["tasks"]:
            item_id = (task.get("attributes") or {}).get("departmentPlanItemId")
            if item_id:
                counts[str(item_id)] = counts.get(str(item_id), 0) + 1
            if plan_item_id and str(item_id or "") == plan_item_id:
                matches.append(task)
        return {"tasks": matches, "counts": counts}

    def agent_weekly_plans(
        self,
        identity: SessionIdentity,
        *,
        week_label: str | None = None,
        agent_key: str | None = None,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        parameters: list[Any] = [identity.organization_id]
        clauses = [
            "organization_id = ?",
            "status != 'archived'",
            "json_type(attributes_json, '$.agentKey') IS NOT NULL",
        ]
        if week_label:
            clauses.append("period_label = ?")
            parameters.append(week_label)
        if agent_key:
            clauses.append("json_extract(attributes_json, '$.agentKey') = ?")
            parameters.append(agent_key)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM organization_plans
                WHERE {" AND ".join(clauses)}
                ORDER BY period_label DESC, updated_at DESC, plan_id
                """,
                tuple(parameters),
            ).fetchall()
            plans = []
            for row in rows:
                attributes = json.loads(str(row["attributes_json"] or "{}"))
                items = [
                    {
                        "id": item["plan_item_id"],
                        "title": item["title"],
                        "rationale": item["statement"],
                        "scheduleHint": item["expected_output"],
                        "status": item["status"],
                        "version": int(item["version"]),
                    }
                    for item in connection.execute(
                        """
                        SELECT * FROM organization_plan_items
                        WHERE organization_id = ? AND plan_id = ?
                          AND status != 'archived'
                        ORDER BY sort_order, plan_item_id
                        """,
                        (identity.organization_id, row["plan_id"]),
                    ).fetchall()
                ]
                plans.append(
                    {
                        "planId": row["plan_id"],
                        "agentKey": attributes.get("agentKey"),
                        "agentName": attributes.get("agentName") or attributes.get("agentKey"),
                        "departmentName": attributes.get("departmentName") or "",
                        "color": attributes.get("color") or "#5B7BFE",
                        "weekLabel": row["period_label"],
                        "summary": row["summary"],
                        "planItems": items,
                        "sourcePolicy": attributes.get("sourcePolicy") or {
                            "authority": "organization_plans",
                        },
                        "version": int(row["version"]),
                        "updatedAt": row["updated_at"],
                    }
                )
        return {"weeklyPlans": plans}

    def save_agent_weekly_plan(
        self,
        identity: SessionIdentity,
        week_label: str,
        agent_key: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        command_type = "organization_plan.agent_weekly_saved"
        week_label = _text(week_label)
        agent_key = _text(agent_key)
        if not week_label or not agent_key:
            raise RepositoryError(422, "agent_weekly_plan_identity_required", "周计划身份不完整")
        normalized = {
            "weekLabel": week_label,
            "agentKey": agent_key,
            "summary": _text(payload.get("summary")),
            "planItems": [
                {
                    "title": _text(item.get("title")),
                    "rationale": _text(item.get("rationale")),
                    "scheduleHint": _text(item.get("scheduleHint")),
                    "status": _text(item.get("status")) or "active",
                }
                for item in payload.get("planItems") or []
                if isinstance(item, Mapping) and _text(item.get("title"))
            ],
        }
        if "expectedVersion" in payload:
            normalized["expectedVersion"] = _integer(payload.get("expectedVersion"))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = connection.execute(
                    """
                    SELECT * FROM organization_plans
                    WHERE organization_id = ? AND period_label = ?
                      AND json_extract(attributes_json, '$.agentKey') = ?
                      AND status != 'archived'
                    ORDER BY updated_at DESC, plan_id
                    LIMIT 1
                    """,
                    (identity.organization_id, week_label, agent_key),
                ).fetchone()
                now = utc_now()
                attributes = {
                    "orgModelKind": "agent_weekly_plan",
                    "agentKey": agent_key,
                    "agentName": _text(payload.get("agentName")) or agent_key,
                    "departmentName": _text(payload.get("departmentName")),
                    "color": _text(payload.get("color")) or "#5B7BFE",
                    "sourcePolicy": payload.get("sourcePolicy")
                    or {"authority": "organization_plans"},
                }
                if row is None:
                    plan_id = new_id()
                    expected_version = None
                    before_version = None
                    after_version = 1
                    connection.execute(
                        """
                        INSERT INTO organization_plans (
                            plan_id, organization_id, department_id, period_label,
                            owner_membership_id, summary, status, attributes_json,
                            version, created_at, updated_at
                        ) VALUES (?, ?, NULL, ?, ?, ?, 'active', ?, 1, ?, ?)
                        """,
                        (
                            plan_id,
                            identity.organization_id,
                            week_label,
                            identity.membership_id,
                            normalized["summary"],
                            canonical_json(attributes),
                            now,
                            now,
                        ),
                    )
                else:
                    if (
                        row["owner_membership_id"] != identity.membership_id
                        and not identity.is_admin
                    ):
                        raise RepositoryError(
                            403,
                            "agent_weekly_plan_forbidden",
                            "无权修改该周计划",
                        )
                    plan_id = str(row["plan_id"])
                    expected_version = self._expected(
                        payload,
                        code="organization_plan_expected_version_required",
                    )
                    before_version = self._assert_version(
                        row,
                        expected_version,
                        code="organization_plan_version_conflict",
                        message="周计划已被更新，请刷新后重试",
                    )
                    changed = connection.execute(
                        """
                        UPDATE organization_plans
                        SET summary = ?, attributes_json = ?, version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND plan_id = ? AND version = ?
                        """,
                        (
                            normalized["summary"],
                            canonical_json(attributes),
                            now,
                            identity.organization_id,
                            plan_id,
                            expected_version,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "organization_plan_version_conflict",
                            "周计划已被更新，请刷新后重试",
                        )
                    after_version = before_version + 1
                    connection.execute(
                        """
                        UPDATE organization_plan_items
                        SET status = 'archived', version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND plan_id = ?
                          AND status != 'archived'
                        """,
                        (now, identity.organization_id, plan_id),
                    )
                for order, item in enumerate(normalized["planItems"]):
                    item_status = {
                        "todo": "active",
                        "in_progress": "active",
                        "done": "completed",
                    }.get(item["status"], item["status"])
                    if item_status not in {"active", "completed", "cancelled"}:
                        item_status = "active"
                    connection.execute(
                        """
                        INSERT INTO organization_plan_items (
                            plan_item_id, organization_id, plan_id, title,
                            statement, owner_membership_id, expected_output,
                            status, sort_order, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            new_id(),
                            identity.organization_id,
                            plan_id,
                            item["title"],
                            item["rationale"],
                            identity.membership_id,
                            item["scheduleHint"],
                            item_status,
                            order,
                            now,
                            now,
                        ),
                    )
                saved_plan = connection.execute(
                    "SELECT * FROM organization_plans WHERE plan_id = ?",
                    (plan_id,),
                ).fetchone()
                saved_items = connection.execute(
                    """
                    SELECT * FROM organization_plan_items
                    WHERE organization_id = ? AND plan_id = ? AND status != 'archived'
                    ORDER BY sort_order, plan_item_id
                    """,
                    (identity.organization_id, plan_id),
                ).fetchall()
                result = {
                    "weeklyPlan": {
                        "planId": plan_id,
                        "agentKey": agent_key,
                        "agentName": attributes["agentName"],
                        "departmentName": attributes["departmentName"],
                        "color": attributes["color"],
                        "weekLabel": week_label,
                        "summary": saved_plan["summary"],
                        "planItems": [
                            {
                                "id": item["plan_item_id"],
                                "title": item["title"],
                                "rationale": item["statement"],
                                "scheduleHint": item["expected_output"],
                                "status": item["status"],
                                "version": int(item["version"]),
                            }
                            for item in saved_items
                        ],
                        "sourcePolicy": attributes["sourcePolicy"],
                        "version": int(saved_plan["version"]),
                        "updatedAt": saved_plan["updated_at"],
                    }
                }
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="organization_plan",
                    aggregate_id=plan_id,
                    expected_version=expected_version,
                    before_version=before_version,
                    after_version=after_version,
                    payload=normalized,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def task_context(
        self,
        identity: SessionIdentity,
        task_id: str,
    ) -> dict[str, Any]:
        detail = self.task_detail(identity, task_id)["task"]
        project_id = detail.get("projectId")
        event_line_id = detail.get("eventLineId")
        snapshot = self.root.business_snapshot(identity)
        project = next(
            (
                {
                    "project_id": item.get("projectId"),
                    "name": item.get("name"),
                    "summary": item.get("summary") or "",
                    "version": int(item.get("version") or 1),
                    "updated_at": item.get("updatedAt"),
                }
                for item in snapshot.get("projects") or []
                if item.get("projectId") == project_id
            ),
            None,
        )
        event_line = next(
            (
                {
                    "event_line_id": item.get("eventLineId"),
                    "name": item.get("name"),
                    "goal": item.get("goal") or "",
                    "background": item.get("background") or "",
                    "version": int(item.get("version") or 1),
                    "updated_at": item.get("updatedAt"),
                }
                for item in snapshot.get("eventLines") or []
                if item.get("eventLineId") == event_line_id
            ),
            None,
        )
        documents = [
            {
                "documentId": item.get("documentId"),
                "title": item.get("title"),
                "documentKind": item.get("documentKind"),
                "lifecycleState": item.get("lifecycleState"),
                "version": int(item.get("version") or 1),
                "updatedAt": item.get("updatedAt"),
            }
            for item in snapshot.get("documents") or []
            if project_id
            and item.get("projectId") == project_id
            and item.get("lifecycleState") == "active"
        ][:20]

        knowledge_payload: dict[str, Any] | None = None
        if project_id:
            knowledge_payload = self.root.project_knowledge_context(
                identity,
                project_id=str(project_id),
            )
            if (
                knowledge_payload.get("cloudInstanceId")
                != identity.cloud_instance_id
                or knowledge_payload.get("organizationId")
                != identity.organization_id
                or (knowledge_payload.get("project") or {}).get("projectId")
                != project_id
            ):
                raise RepositoryError(
                    502,
                    "project_knowledge_identity_mismatch",
                    "项目知识上下文身份不匹配",
                )

        shared_summary_items: list[dict[str, Any]] = []
        for item in (
            (knowledge_payload or {}).get("organizationSharedKnowledge") or []
        ):
            source_type = _text(item.get("sourceType")).lower()
            summary = _text(item.get("summary"))
            if (
                item.get("sourceScope") != "organization_shared"
                or not summary
                or not (
                    source_type.endswith("_summary")
                    or source_type == "structured_intelligence_summary"
                )
            ):
                continue
            shared_summary_items.append(
                {
                    "sourceScope": "organization_shared",
                    "sourceType": source_type,
                    "sourceId": item.get("sourceId"),
                    "sourceVersion": int(item.get("sourceVersion") or 1),
                    "contentHash": item.get("contentHash"),
                    "title": item.get("title") or "组织共享项目摘要",
                    "summary": summary,
                    "sourceDescription": item.get("sourceDescription") or "",
                    "updatedAt": item.get("updatedAt"),
                }
            )
        known_source_ids = {
            _text(item.get("sourceId")) for item in shared_summary_items
        }
        if project_id:
            with self._connection() as connection:
                smart_import_rows = connection.execute(
                    """
                    SELECT intelligence_id, title, summary, version, updated_at
                    FROM intelligence_records
                    WHERE organization_id = ?
                      AND project_id = ?
                      AND record_kind = 'smart_import_reviewed'
                      AND status = 'accepted'
                      AND visibility_scope = 'organization'
                      AND trim(summary) != ''
                    ORDER BY updated_at DESC, intelligence_id
                    """,
                    (identity.organization_id, project_id),
                ).fetchall()
            for row in smart_import_rows:
                source_id = _text(row["intelligence_id"])
                if source_id in known_source_ids:
                    continue
                summary = _text(row["summary"])[:2000]
                shared_summary_items.append(
                    {
                        "sourceScope": "organization_shared",
                        "sourceType": "smart_import_summary",
                        "sourceId": source_id,
                        "sourceVersion": int(row["version"]),
                        "contentHash": sha256_text(
                            canonical_json(
                                {
                                    "sourceId": source_id,
                                    "sourceVersion": int(row["version"]),
                                    "title": row["title"],
                                    "summary": summary,
                                }
                            )
                        ),
                        "title": row["title"] or "已审阅智能导入摘要",
                        "summary": summary,
                        "sourceDescription": (
                            "已接受并设为组织共享的智能导入受限摘要"
                        ),
                        "updatedAt": row["updated_at"],
                    }
                )
                known_source_ids.add(source_id)
        summary_excerpts = [
            {
                "sourceScope": item["sourceScope"],
                "sourceType": item["sourceType"],
                "sourceId": item["sourceId"],
                "title": item["title"],
                "summary": item["summary"],
            }
            for item in shared_summary_items
        ]
        project_knowledge = {
            "projectId": project_id,
            "organizationId": identity.organization_id,
            "state": "ready" if summary_excerpts else "empty",
            "items": shared_summary_items,
            "summaryExcerpts": summary_excerpts,
            "materialBoundary": (
                (knowledge_payload or {}).get("materialBoundary")
                or {
                    "sourceFileContentIncluded": False,
                    "sourceFilePathsIncluded": False,
                    "storageLocatorsIncluded": False,
                    "unpublishedDocumentContentIncluded": False,
                }
            ),
        }

        sources: list[dict[str, Any]] = []
        if project:
            sources.append(
                {
                    "type": "project",
                    "id": project["project_id"],
                    "title": project["name"],
                }
            )
        if event_line:
            sources.append(
                {
                    "type": "event_line",
                    "id": event_line["event_line_id"],
                    "title": event_line["name"],
                }
            )
        sources.extend(
            {"type": "document", "id": item["documentId"], "title": item["title"]}
            for item in documents
        )
        sources.extend(
            {
                "type": item["sourceType"],
                "scope": item["sourceScope"],
                "id": item["sourceId"],
                "title": item["title"],
                "summary": item["summary"],
                "contentHash": item["contentHash"],
                "version": item["sourceVersion"],
            }
            for item in shared_summary_items
        )
        brief_parts = [detail.get("description") or ""]
        if project:
            brief_parts.append(
                f"所属项目：{project['name']}。{project.get('summary') or ''}"
            )
        if event_line:
            brief_parts.append(
                f"关联事件线：{event_line['name']}。目标：{event_line['goal']}。"
                f"背景：{event_line['background']}"
            )
        if summary_excerpts:
            brief_parts.append(
                "组织共享项目背景：\n"
                + "\n".join(
                    f"- {item['title']}：{item['summary']}"
                    for item in summary_excerpts
                )
            )
        if documents:
            brief_parts.append(
                "可用资料：" + "、".join(item["title"] for item in documents)
            )
        brief = "\n".join(part for part in brief_parts if _text(part))
        material_hash = sha256_text(canonical_json(sources))
        return {
            "cloudInstanceId": identity.cloud_instance_id,
            "organizationId": identity.organization_id,
            "task": detail,
            "project": project,
            "eventLine": event_line,
            "documents": documents,
            "projectKnowledge": project_knowledge,
            "summaryExcerpts": summary_excerpts,
            "sources": sources,
            "brief": brief,
            "materialPackHash": material_hash,
        }

    def meeting_context(
        self,
        identity: SessionIdentity,
        meeting_id: str,
    ) -> dict[str, Any]:
        """Resolve meeting context only through strict source/link facts.

        Strict v2 intentionally has no second meeting authority table. A meeting
        is therefore resolvable only when a strict task, source asset, or event
        activity explicitly carries its stable meeting id.
        """
        snapshot = self.root.business_snapshot(identity)
        visible_project_ids = {
            _text(item.get("projectId"))
            for item in snapshot.get("projects") or []
            if _text(item.get("projectId"))
        }
        visible_document_ids = {
            _text(item.get("documentId"))
            for item in snapshot.get("documents") or []
            if _text(item.get("documentId"))
        }
        visible_event_ids = {
            _text(item.get("eventLineId"))
            for item in snapshot.get("eventLines") or []
            if _text(item.get("eventLineId"))
        }
        with self._connection() as connection:
            task_rows = connection.execute(
                """
                SELECT * FROM task_records
                WHERE organization_id = ? AND source_type = 'meeting' AND source_id = ?
                ORDER BY updated_at DESC, task_id
                """,
                (identity.organization_id, meeting_id),
            ).fetchall()
            visible_tasks = []
            for row in task_rows:
                try:
                    self.root._task_row(  # noqa: SLF001
                        connection,
                        identity,
                        str(row["task_id"]),
                        require_edit=False,
                    )
                except RepositoryError as exc:
                    if exc.status_code == 403:
                        continue
                    raise
                visible_tasks.append(self._task_payload(connection, row))
            asset_rows = connection.execute(
                """
                SELECT sa.source_asset_id, sa.project_id,
                       sa.created_by_membership_id, sa.file_name,
                       sa.source_kind, sa.lifecycle_state, sa.version,
                       sa.updated_at, kd.document_id, kd.title AS document_title,
                       kd.document_kind, kd.lifecycle_state AS document_state,
                       kd.version AS document_version, kd.updated_at AS document_updated_at
                FROM source_assets sa
                LEFT JOIN knowledge_documents kd
                  ON kd.source_asset_id = sa.source_asset_id
                 AND kd.organization_id = sa.organization_id
                WHERE sa.organization_id = ?
                  AND (
                    sa.source_locator = ?
                    OR (
                      sa.source_kind IN ('meeting', 'meeting_note', 'meeting_recording')
                      AND sa.source_locator = ?
                    )
                  )
                ORDER BY sa.updated_at DESC, sa.source_asset_id
                """,
                (identity.organization_id, meeting_id, meeting_id),
            ).fetchall()
            asset_rows = [
                row
                for row in asset_rows
                if (
                    (
                        row["document_id"]
                        and str(row["document_id"]) in visible_document_ids
                    )
                    or (
                        not row["document_id"]
                        and row["project_id"]
                        and str(row["project_id"]) in visible_project_ids
                    )
                    or (
                        not row["document_id"]
                        and not row["project_id"]
                        and row["created_by_membership_id"]
                        == identity.membership_id
                    )
                )
            ]
            activity_rows = connection.execute(
                """
                SELECT ela.*, el.project_id, el.name AS event_line_name
                FROM event_line_activities ela
                JOIN event_line_records el ON el.event_line_id = ela.event_line_id
                WHERE ela.organization_id = ? AND ela.source_type = 'meeting'
                  AND ela.source_id = ? AND ela.association_state = 'confirmed'
                ORDER BY ela.happened_at DESC, ela.event_line_activity_id
                """,
                (identity.organization_id, meeting_id),
            ).fetchall()
            activity_rows = [
                row
                for row in activity_rows
                if str(row["event_line_id"]) in visible_event_ids
            ]
            if not visible_tasks and not asset_rows and not activity_rows:
                raise RepositoryError(
                    404,
                    "meeting_context_missing",
                    "严格新版没有找到该会议的稳定来源事实",
                )
            project_ids = {
                _text(task.get("projectId"))
                for task in visible_tasks
                if _text(task.get("projectId"))
            }
            project_ids.update(
                _text(row["project_id"]) for row in asset_rows if _text(row["project_id"])
            )
            project_ids.update(
                _text(row["project_id"]) for row in activity_rows if _text(row["project_id"])
            )
            projects = []
            if project_ids:
                placeholders = ",".join("?" for _ in project_ids)
                projects = [
                    dict(row)
                    for row in connection.execute(
                        f"""
                        SELECT id AS project_id, name, summary, version, updated_at
                        FROM clients
                        WHERE scope_id = ? AND id IN ({placeholders})
                          AND lifecycle_state != 'deleted'
                        """,
                        (identity.scope_id, *sorted(project_ids)),
                    ).fetchall()
                ]
        documents = [
            {
                "documentId": row["document_id"],
                "sourceAssetId": row["source_asset_id"],
                "projectId": row["project_id"],
                "title": row["document_title"] or row["file_name"],
                "documentKind": row["document_kind"] or row["source_kind"],
                "lifecycleState": row["document_state"] or row["lifecycle_state"],
                "version": int(row["document_version"] or row["version"]),
                "updatedAt": row["document_updated_at"] or row["updated_at"],
            }
            for row in asset_rows
        ]
        activities = [
            {
                "eventLineActivityId": row["event_line_activity_id"],
                "eventLineId": row["event_line_id"],
                "eventLineName": row["event_line_name"],
                "projectId": row["project_id"],
                "title": row["title"],
                "summary": row["summary"],
                "happenedAt": row["happened_at"],
            }
            for row in activity_rows
        ]
        sources = [
            {"type": "task", "id": task["taskId"], "title": task["title"]}
            for task in visible_tasks
        ]
        sources.extend(
            {
                "type": "document",
                "id": document["documentId"] or document["sourceAssetId"],
                "title": document["title"],
            }
            for document in documents
        )
        sources.extend(
            {
                "type": "event_line_activity",
                "id": activity["eventLineActivityId"],
                "title": activity["title"],
            }
            for activity in activities
        )
        return {
            "meetingId": meeting_id,
            "projects": projects,
            "tasks": visible_tasks,
            "documents": documents,
            "activities": activities,
            "sources": sources,
            "materialPackHash": sha256_text(canonical_json(sources)),
        }

    def reviews(
        self,
        identity: SessionIdentity,
        query: Mapping[str, str],
    ) -> dict[str, Any]:
        snapshot = self.root.business_snapshot(identity)
        visible_tasks = {
            _text(item.get("taskId")): item
            for item in snapshot.get("tasks") or []
            if _text(item.get("taskId"))
        }
        requested = _text(query.get("weekLabel"))
        reviews = snapshot.get("weeklyReviews") or []
        if requested:
            reviews = [item for item in reviews if item.get("weekLabel") == requested]
        with self._connection() as connection:
            enriched = []
            for review in reviews:
                review_id = _text(review.get("weeklyReviewId"))
                is_owner = (
                    _text(review.get("membershipId"))
                    == identity.membership_id
                )
                sections = [
                    {
                        "sectionType": row["section_type"],
                        "content": row["content"],
                        "contentDomain": row["content_domain"],
                        "visibilityScope": row["visibility_scope"],
                    }
                    for row in connection.execute(
                        """
                        SELECT * FROM weekly_review_sections
                        WHERE organization_id = ? AND weekly_review_id = ?
                        ORDER BY created_at, weekly_review_section_id
                        """,
                        (identity.organization_id, review_id),
                    ).fetchall()
                    if row["visibility_scope"] != "self" or is_owner
                ]
                task_links = []
                for row in connection.execute(
                        """
                        SELECT * FROM weekly_review_task_links
                        WHERE organization_id = ? AND weekly_review_id = ?
                        ORDER BY reviewed_at, weekly_review_task_link_id
                        """,
                        (identity.organization_id, review_id),
                    ).fetchall():
                    task_id = _text(row["task_id"])
                    task = visible_tasks.get(task_id)
                    if task is None:
                        continue
                    structured_note = json.loads(row["structured_note_json"])
                    content_domain = _text(
                        (
                            structured_note
                            if isinstance(structured_note, Mapping)
                            else {}
                        ).get("contentDomain")
                    )
                    if content_domain not in {"work", "personal"}:
                        content_domain = (
                            "personal"
                            if _text(task.get("visibilityScope")) == "self"
                            else "work"
                        )
                    if (
                        not is_owner
                        and isinstance(structured_note, Mapping)
                        and content_domain == "personal"
                    ):
                        continue
                    task_links.append(
                        {
                            "weeklyReviewTaskLinkId": row[
                                "weekly_review_task_link_id"
                            ],
                            "taskId": task_id,
                            "contentDomain": content_domain,
                            "note": row["note"],
                            "structuredNote": structured_note,
                            "reviewedAt": row["reviewed_at"],
                        }
                    )
                enriched.append({**review, "sections": sections, "taskLinks": task_links})
        return {"reviews": enriched}

    def save_review(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        lifecycle_state: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if lifecycle_state not in {"draft", "submitted"}:
            raise RepositoryError(422, "review_state_invalid", "复盘状态无效")
        week_label = _text(payload.get("weekLabel"))
        if not week_label:
            raise RepositoryError(422, "review_week_required", "请选择复盘周")
        command_type = f"weekly_review.{lifecycle_state}"
        normalized = dict(payload)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = connection.execute(
                    """
                    SELECT * FROM weekly_reviews
                    WHERE organization_id = ? AND membership_id = ? AND week_label = ?
                    """,
                    (identity.organization_id, identity.membership_id, week_label),
                ).fetchone()
                now = utc_now()
                if row is None:
                    review_id = new_id()
                    before_version = None
                    after_version = 1
                    expected_version = None
                    connection.execute(
                        """
                        INSERT INTO weekly_reviews (
                            weekly_review_id, organization_id, membership_id,
                            week_label, work_progress, work_blocker, work_direction,
                            next_week_focus, support_needed, work_free_note,
                            personal_growth_note, private_note, personal_visibility,
                            lifecycle_state, version, submitted_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                        """,
                        (
                            review_id,
                            identity.organization_id,
                            identity.membership_id,
                            week_label,
                            _text(payload.get("workProgress")),
                            _text(payload.get("workBlocker")),
                            _text(payload.get("workDirection")),
                            _text(payload.get("nextWeekFocus")),
                            _text(payload.get("supportNeeded")),
                            _text(payload.get("workFreeNote")),
                            _text(payload.get("personalGrowthNote")),
                            _text(payload.get("personalPrivateNote")),
                            _text(payload.get("personalVisibility")) or "self",
                            lifecycle_state,
                            now if lifecycle_state == "submitted" else None,
                            now,
                            now,
                        ),
                    )
                else:
                    review_id = str(row["weekly_review_id"])
                    expected_version = self._expected(
                        payload,
                        code="weekly_review_expected_version_required",
                    )
                    before_version = self._assert_version(
                        row,
                        expected_version,
                        code="weekly_review_version_conflict",
                        message="本周复盘已被更新，请刷新后重试",
                    )
                    changed = connection.execute(
                        """
                        UPDATE weekly_reviews
                        SET work_progress = ?, work_blocker = ?, work_direction = ?,
                            next_week_focus = ?, support_needed = ?, work_free_note = ?,
                            personal_growth_note = ?, private_note = ?,
                            personal_visibility = ?, lifecycle_state = ?,
                            submitted_at = ?, version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND weekly_review_id = ? AND version = ?
                        """,
                        (
                            _text(payload.get("workProgress")),
                            _text(payload.get("workBlocker")),
                            _text(payload.get("workDirection")),
                            _text(payload.get("nextWeekFocus")),
                            _text(payload.get("supportNeeded")),
                            _text(payload.get("workFreeNote")),
                            _text(payload.get("personalGrowthNote")),
                            _text(payload.get("personalPrivateNote")),
                            _text(payload.get("personalVisibility")) or row["personal_visibility"],
                            lifecycle_state,
                            now if lifecycle_state == "submitted" else row["submitted_at"],
                            now,
                            identity.organization_id,
                            review_id,
                            expected_version,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "weekly_review_version_conflict",
                            "本周复盘已被更新，请刷新后重试",
                        )
                    after_version = before_version + 1
                    connection.execute(
                        """
                        DELETE FROM weekly_review_sections
                        WHERE organization_id = ? AND weekly_review_id = ?
                        """,
                        (identity.organization_id, review_id),
                    )
                    connection.execute(
                        """
                        DELETE FROM weekly_review_task_links
                        WHERE organization_id = ? AND weekly_review_id = ?
                        """,
                        (identity.organization_id, review_id),
                    )
                section_values = [
                    ("work_progress", payload.get("workProgress"), "work", "organization"),
                    ("work_blocker", payload.get("workBlocker"), "work", "organization"),
                    ("work_direction", payload.get("workDirection"), "work", "organization"),
                    ("next_week_focus", payload.get("nextWeekFocus"), "work", "organization"),
                    ("support_needed", payload.get("supportNeeded"), "work", "organization"),
                    ("work_free_note", payload.get("workFreeNote"), "work", "department"),
                    (
                        "personal_growth_note",
                        payload.get("personalGrowthNote"),
                        "personal",
                        _text(payload.get("personalVisibility")) or "self",
                    ),
                ]
                for section_type, content, domain, visibility in section_values:
                    if not _text(content):
                        continue
                    connection.execute(
                        """
                        INSERT INTO weekly_review_sections (
                            weekly_review_section_id, organization_id,
                            weekly_review_id, section_type, content,
                            content_domain, visibility_scope, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id(),
                            identity.organization_id,
                            review_id,
                            section_type,
                            _text(content),
                            domain,
                            visibility,
                            now,
                        ),
                    )
                for entry in payload.get("taskEntries") or []:
                    if not isinstance(entry, Mapping) or bool(entry.get("delete")):
                        continue
                    task_id = _text(entry.get("taskId"))
                    if not task_id:
                        continue
                    task_row = self.root._task_row(  # noqa: SLF001
                        connection,
                        identity,
                        task_id,
                        require_edit=False,
                    )
                    structured_note = dict(
                        entry.get("structuredNote") or {}
                    )
                    requested_content_domain = _text(
                        structured_note.get("contentDomain")
                    )
                    structured_note["contentDomain"] = (
                        requested_content_domain
                        if requested_content_domain in {"work", "personal"}
                        else (
                            "personal"
                            if _text(task_row["visibility_scope"]) == "self"
                            else "work"
                        )
                    )
                    connection.execute(
                        """
                        INSERT INTO weekly_review_task_links (
                            weekly_review_task_link_id, organization_id,
                            weekly_review_id, task_id, note, structured_note_json,
                            reviewed_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_id(),
                            identity.organization_id,
                            review_id,
                            task_id,
                            _text(entry.get("note")),
                            canonical_json(structured_note),
                            now,
                            now,
                            now,
                        ),
                    )
                saved = connection.execute(
                    "SELECT * FROM weekly_reviews WHERE weekly_review_id = ?",
                    (review_id,),
                ).fetchone()
                result = {
                    "review": {
                        "weeklyReviewId": review_id,
                        "membershipId": saved["membership_id"],
                        "weekLabel": saved["week_label"],
                        "workProgress": saved["work_progress"],
                        "workBlocker": saved["work_blocker"],
                        "workDirection": saved["work_direction"],
                        "nextWeekFocus": saved["next_week_focus"],
                        "supportNeeded": saved["support_needed"],
                        "workFreeNote": saved["work_free_note"],
                        "personalGrowthNote": saved["personal_growth_note"],
                        "lifecycleState": saved["lifecycle_state"],
                        "version": int(saved["version"]),
                        "updatedAt": saved["updated_at"],
                    }
                }
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="weekly_review",
                    aggregate_id=review_id,
                    expected_version=expected_version,
                    before_version=before_version,
                    after_version=after_version,
                    payload=normalized,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def report_artifacts(
        self,
        identity: SessionIdentity,
        event_line_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            self._event_row(
                connection,
                identity,
                event_line_id,
                require_edit=False,
            )
            rows = connection.execute(
                """
                SELECT * FROM narrative_outputs
                WHERE organization_id = ? AND event_line_id = ?
                  AND lifecycle_state != 'archived'
                ORDER BY updated_at DESC, narrative_output_id
                """,
                (identity.organization_id, event_line_id),
            ).fetchall()
            return {
                "artifacts": [
                    {
                        "id": row["narrative_output_id"],
                        "eventLineId": row["event_line_id"],
                        "title": row["title"],
                        "outputKind": row["output_kind"],
                        "status": row["status"],
                        "version": int(row["version"]),
                        "updatedAt": row["updated_at"],
                    }
                    for row in rows
                ]
            }

    def dispatch(
        self,
        identity: SessionIdentity,
        *,
        method: str,
        path: str,
        query: Mapping[str, str],
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        path = path.strip("/")
        if method == "GET" and path == "board":
            return self.board(identity)
        if method == "GET" and path == "event-lines":
            return self.event_lines(identity)
        if method == "GET" and path == "reviews":
            return self.reviews(identity, query)
        if method == "GET" and path == "clients-pulse":
            return self.clients_pulse(identity)
        if method == "GET" and path == "plan-item-tasks":
            return self.tasks_for_plan_item(identity, query.get("planItemId"))
        if method == "GET" and path == "agent-weekly-plans":
            return self.agent_weekly_plans(
                identity,
                week_label=query.get("weekLabel"),
                agent_key=query.get("agentKey"),
            )
        match = re.fullmatch(r"tasks/([^/]+)", path)
        if method == "GET" and match:
            return self.task_detail(identity, match.group(1))
        if method == "POST" and match and query.get("action") == "archive":
            return self.archive_task(
                identity,
                match.group(1),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"tasks/([^/]+)/actions/([^/]+)", path)
        if method == "POST" and match:
            return self.task_action(
                identity,
                match.group(1),
                action=match.group(2),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"tasks/([^/]+)/classification", path)
        if method == "PATCH" and match:
            return self.set_task_classification(
                identity,
                match.group(1),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"tasks/([^/]+)/plan-link", path)
        if method == "GET" and match:
            return self.task_plan_link(identity, match.group(1))
        if method == "PATCH" and match:
            return self.patch_task_plan_link(
                identity,
                match.group(1),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"tasks/([^/]+)/context", path)
        if method == "GET" and match:
            return self.task_context(identity, match.group(1))
        match = re.fullmatch(r"tasks/([^/]+)/attachments", path)
        if method == "POST" and match:
            return self.save_attachment(
                identity,
                target_type="task",
                target_id=match.group(1),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"tasks/([^/]+)/attachments/([^/]+)", path)
        if method == "DELETE" and match:
            return self.archive_task_attachment(
                identity,
                match.group(1),
                match.group(2),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(
            r"tasks/([^/]+)/attachments/([^/]+)/content",
            path,
        )
        if method == "GET" and match:
            return self.task_attachment_content(
                identity,
                match.group(1),
                match.group(2),
            )
        match = re.fullmatch(
            r"tasks/([^/]+)/attachments/([^/]+)/transcription-complete",
            path,
        )
        if method == "POST" and match:
            return self.complete_task_transcription(
                identity,
                match.group(1),
                match.group(2),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(
            r"tasks/([^/]+)/attachments/([^/]+)/retry-transcription",
            path,
        )
        if method == "POST" and match:
            return self.queue_attachment_processing(
                identity,
                target_type="task",
                target_id=match.group(1),
                attachment_id=match.group(2),
                processing_kind="transcription",
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"tasks/([^/]+)/attachments/([^/]+)/transcript", path)
        if method == "GET" and match:
            return self.task_transcript(identity, match.group(1), match.group(2))
        if method == "PUT" and match:
            return self.update_task_transcript(
                identity,
                match.group(1),
                match.group(2),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"meetings/([^/]+)/context", path)
        if method == "GET" and match:
            return self.meeting_context(identity, match.group(1))
        match = re.fullmatch(r"(lists|tags)(?:/([^/]+))?", path)
        if match and method in {"POST", "PATCH", "DELETE"}:
            action = (
                "create"
                if method == "POST" and match.group(2) is None
                else "update"
                if method == "PATCH"
                else "archive"
            )
            return self.mutate_named_collection(
                identity,
                kind="list" if match.group(1) == "lists" else "tag",
                item_id=match.group(2),
                payload=payload,
                idempotency_key=idempotency_key,
                action=action,
            )
        match = re.fullmatch(r"event-lines/([^/]+)", path)
        if method == "GET" and match:
            return self.event_detail(identity, match.group(1))
        if match and method in {"PATCH", "DELETE", "POST"}:
            action = _text(query.get("action")) or (
                "updated" if method == "PATCH" else "archived"
            )
            return self.mutate_event_line(
                identity,
                match.group(1),
                action=action,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"event-lines/([^/]+)/tasks/([^/]+)", path)
        if match and method in {"POST", "PATCH"}:
            return self.link_task(
                identity,
                match.group(1),
                match.group(2),
                milestone_only=method == "PATCH",
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"event-lines/([^/]+)/report-artifacts", path)
        if method == "GET" and match:
            return self.report_artifacts(identity, match.group(1))
        match = re.fullmatch(r"event-lines/([^/]+)/attachments", path)
        if method == "POST" and match:
            return self.save_attachment(
                identity,
                target_type="event_line",
                target_id=match.group(1),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(
            r"event-lines/([^/]+)/attachments/([^/]+)/retry-parse",
            path,
        )
        if method == "POST" and match:
            return self.queue_attachment_processing(
                identity,
                target_type="event_line",
                target_id=match.group(1),
                attachment_id=match.group(2),
                processing_kind="parse",
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"event-lines/([^/]+)/merge-preview", path)
        if method == "POST" and match:
            return self.preview_event_merge(
                identity,
                match.group(1),
                [
                    _text(value)
                    for value in payload.get("sourceIds") or []
                    if _text(value)
                ],
            )
        match = re.fullmatch(r"event-lines/([^/]+)/merge", path)
        if method == "POST" and match:
            return self.merge_event_lines(
                identity,
                match.group(1),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        match = re.fullmatch(r"agent-weekly-plans/([^/]+)/([^/]+)", path)
        if method == "PUT" and match:
            return self.save_agent_weekly_plan(
                identity,
                match.group(1),
                match.group(2),
                payload=payload,
                idempotency_key=idempotency_key,
            )
        if method == "POST" and path in {"reviews/weekly", "reviews/weekly/draft"}:
            return self.save_review(
                identity,
                payload=payload,
                lifecycle_state="draft" if path.endswith("/draft") else "submitted",
                idempotency_key=idempotency_key,
            )
        raise RepositoryError(
            501,
            "workflow_capability_not_connected",
            f"严格新版尚未接通该 workflow 云操作：{method} {path}",
        )
