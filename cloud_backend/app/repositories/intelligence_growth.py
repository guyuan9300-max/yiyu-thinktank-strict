"""Strict authority and rebuildable views for intelligence and growth.

This module deliberately uses only the frozen cloud tables.  Topics, sentiment,
strategy, proposals, and data-center panels are projections over those facts;
they are never persisted as a second cache.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from strict_common.contracts import CLOUD_CONTRACT
from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.schema import database_identity
from strict_common.security import payload_fingerprint

from ..repository import CloudRepository, RepositoryError, SessionIdentity


PROPOSAL_KINDS = frozenset(
    {"proposal", "proposal_draft", "external_evidence_proposal"}
)
EXTERNAL_EVIDENCE_KINDS = frozenset(
    {"external_evidence", "external_evidence_card"}
)
STRATEGIC_KINDS = frozenset({"strategic_thought", "strategy_observation"})
INTERNAL_CONFIGURATION_KINDS = frozenset(
    {
        "focus_directive",
        "intelligence_profile",
        "refresh_cycle_setting",
        "verification_rule",
        "consultation_knowledge_request",
    }
)
CONSULTATION_REQUEST_KIND = "consultation_knowledge_request"
CONSULTATION_MAX_RETRIES = 3


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _expiry() -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(days=30))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _path_id(resource_path: str, prefix: str, suffix: str = "") -> str | None:
    match = re.fullmatch(
        rf"{re.escape(prefix)}/([^/]+){re.escape(suffix)}",
        resource_path,
    )
    return match.group(1) if match else None


def _require_admin(identity: SessionIdentity) -> None:
    if not identity.is_admin:
        raise RepositoryError(
            403,
            "organization_admin_required",
            "该操作需要组织管理员权限",
        )


def _require_expected(payload: Mapping[str, Any], current_version: int) -> int:
    value = payload.get("expectedVersion")
    if value is None:
        raise RepositoryError(
            409,
            "expected_version_required",
            "该写入需要基于已读取版本提交",
        )
    try:
        expected = int(value)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(
            422,
            "expected_version_invalid",
            "expectedVersion 必须是整数",
        ) from exc
    if expected != current_version:
        raise RepositoryError(
            409,
            "version_conflict",
            f"对象版本已变化，当前版本为 {current_version}",
        )
    return expected


@dataclass
class Mutation:
    aggregate_type: str
    aggregate_id: str
    before_version: int | None
    after_version: int
    result: dict[str, Any]
    event_type: str
    summary: dict[str, Any] = field(default_factory=dict)
    outbox_payload: dict[str, Any] = field(default_factory=dict)
    children: list["Mutation"] = field(default_factory=list)
    queue_execution: bool = False
    operation_id: str | None = None


class IntelligenceGrowthRepository:
    def __init__(self, repository: CloudRepository):
        self.repository = repository

    # ---------- common reads ----------

    def _snapshot(self, identity: SessionIdentity) -> dict[str, Any]:
        return self.repository.business_snapshot(identity)

    def _require_visible_project(
        self,
        identity: SessionIdentity,
        project_id: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            try:
                project = self.repository._require_project_access(  # noqa: SLF001
                    connection,
                    identity,
                    project_id=project_id,
                    capability="read",
                )
            except RepositoryError as exc:
                if exc.status_code == 404:
                    raise RepositoryError(
                        404,
                        "strategy_extract_project_missing",
                        "战略提炼必须关联当前组织内可用项目",
                    ) from exc
                raise
        return dict(project)

    def _require_project_editor(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        project_id: str,
    ) -> sqlite3.Row:
        try:
            return self.repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=project_id,
                capability="knowledge_write",
            )
        except RepositoryError as exc:
            if exc.status_code == 404:
                raise RepositoryError(
                    404,
                    "strategy_extract_project_missing",
                    "战略提炼必须关联当前组织内可用项目",
                ) from exc
            if exc.status_code != 403:
                raise
            raise RepositoryError(
                403,
                "strategy_extract_edit_forbidden",
                "仅项目创建人、项目负责人、项目编辑者或组织管理员可确认战略提炼",
            ) from exc

    # ---------- consultation knowledge requests ----------

    @staticmethod
    def _consultation_content(
        payload: Mapping[str, Any],
    ) -> tuple[str, list[str], str]:
        if payload.get("shareConfirmed") is not True:
            raise RepositoryError(
                409,
                "consultation_share_confirmation_required",
                "咨询内容只有在成员明确确认可共享后才能沉淀为组织项目知识",
            )
        summary = _text(
            payload.get("shareableSummary")
            if "shareableSummary" in payload
            else payload.get("answer")
        )[:12000]
        if not summary:
            raise RepositoryError(
                422,
                "consultation_shareable_summary_required",
                "请提供已确认可共享的咨询摘要",
            )
        raw_facts = payload.get("shareableFacts") or []
        if not isinstance(raw_facts, list):
            raise RepositoryError(
                422,
                "consultation_shareable_facts_invalid",
                "shareableFacts 必须是文本数组",
            )
        facts = [
            _text(value)[:500]
            for value in raw_facts[:20]
            if _text(value)
        ]
        source_hash = sha256_text(
            canonical_json({"summary": summary, "facts": facts})
        )
        return summary, facts, source_hash

    @staticmethod
    def _consultation_safe_command_payload(
        resource_path: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        safe = {
            key: payload.get(key)
            for key in (
                "organizationId",
                "projectId",
                "requestedByMembershipId",
                "answerId",
                "sourceRequestId",
                "target",
                "taskId",
                "eventLineId",
                "expectedVersion",
                "batchSize",
            )
            if payload.get(key) is not None
        }
        if resource_path == "consultation/knowledge-requests":
            summary = _text(
                payload.get("shareableSummary")
                if "shareableSummary" in payload
                else payload.get("answer")
            )[:12000]
            facts = payload.get("shareableFacts") or []
            normalized_facts = (
                [
                    _text(value)[:500]
                    for value in facts[:20]
                    if _text(value)
                ]
                if isinstance(facts, list)
                else []
            )
            safe.update(
                {
                    "shareConfirmed": payload.get("shareConfirmed") is True,
                    "contentHash": (
                        sha256_text(
                            canonical_json(
                                {
                                    "summary": summary,
                                    "facts": normalized_facts,
                                }
                            )
                        )
                        if summary
                        else ""
                    ),
                    "shareableFactCount": len(normalized_facts),
                    "questionHash": (
                        sha256_text(_text(payload.get("question")))
                        if _text(payload.get("question"))
                        else ""
                    ),
                }
            )
        return safe

    @staticmethod
    def _consultation_state(row: Mapping[str, Any]) -> str:
        payload = _json(row["source_payload_json"], {})
        state = _text(payload.get("requestState"))
        if state:
            return state
        return {
            "candidate": "pending",
            "inbox": "processing",
            "accepted": "completed",
            "returned": "failed_retryable",
            "archived": "blocked",
        }.get(_text(row["status"]), "blocked")

    def _consultation_view(
        self,
        row: Mapping[str, Any],
        *,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        source = _json(row["source_payload_json"], {})
        state = self._consultation_state(row)
        is_owner = (
            _text(row["created_by_membership_id"]) == identity.membership_id
        )
        return {
            "id": row["intelligence_id"],
            "requestId": row["intelligence_id"],
            "answerId": _text(source.get("answerId")),
            "organizationId": row["organization_id"],
            "projectId": row["project_id"],
            "clientId": row["project_id"],
            "clientName": _text(source.get("projectName")),
            "target": _text(source.get("target")) or "document_archive",
            "status": (
                state
                if state in {"pending", "processing", "completed"}
                else "failed"
            ),
            "state": state,
            "retryable": state == "failed_retryable",
            "requestedByMembershipId": row["created_by_membership_id"],
            "requestedByUserId": _text(source.get("requestedByPrincipalId")),
            "requestedByName": _text(source.get("requestedByName")),
            "taskId": source.get("taskId"),
            "eventLineId": source.get("eventLineId"),
            # Questions are intentionally never persisted.  Confirmed shareable
            # content is returned only to its requester; administrators can
            # operate on another member's request without receiving its text.
            "question": "",
            "answer": str(row["summary"]) if is_owner else "",
            "contentRedacted": not is_owner,
            "contentHash": _text(source.get("contentHash")),
            "errorCode": source.get("lastErrorCode"),
            "errorMessage": source.get("lastErrorMessage"),
            "knowledgeDocumentId": source.get("knowledgeDocumentId"),
            "documentVersionId": source.get("documentVersionId"),
            "localDocumentId": source.get("knowledgeDocumentId"),
            "localDocumentPath": None,
            "completedAt": source.get("completedAt"),
            "retryCount": int(source.get("retryCount") or 0),
            "maxRetries": int(
                source.get("maxRetries") or CONSULTATION_MAX_RETRIES
            ),
            "version": int(row["version"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    def _consultation_requests(
        self,
        identity: SessionIdentity,
        query: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        clauses = [
            "organization_id = ?",
            "record_kind = ?",
        ]
        parameters: list[Any] = [
            identity.organization_id,
            CONSULTATION_REQUEST_KIND,
        ]
        if not identity.is_admin:
            clauses.append("created_by_membership_id = ?")
            parameters.append(identity.membership_id)
        project_id = _text(query.get("projectId") or query.get("clientId"))
        if project_id:
            clauses.append("project_id = ?")
            parameters.append(project_id)
        with self.repository._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM intelligence_records
                WHERE {" AND ".join(clauses)}
                ORDER BY updated_at DESC, intelligence_id
                """,
                parameters,
            ).fetchall()
        status = _text(query.get("status") or query.get("state"))
        items = [
            self._consultation_view(row, identity=identity)
            for row in rows
        ]
        if status:
            items = [
                item
                for item in items
                if item["status"] == status or item["state"] == status
            ]
        try:
            limit = max(min(int(query.get("limit") or 100), 200), 1)
        except (TypeError, ValueError):
            limit = 100
        return items[:limit]

    def _validate_consultation_refs(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        project_id: str,
        task_id: str,
        event_line_id: str,
    ) -> sqlite3.Row:
        try:
            project = self._require_project_editor(
                connection,
                identity,
                project_id,
            )
        except RepositoryError as exc:
            if exc.code == "strategy_extract_project_missing":
                raise RepositoryError(
                    404,
                    "consultation_project_missing",
                    "咨询知识请求必须关联当前组织内可用项目",
                ) from exc
            if exc.code == "strategy_extract_edit_forbidden":
                raise RepositoryError(
                    403,
                    "consultation_project_publish_forbidden",
                    "仅项目创建人、负责人、编辑者或组织管理员可发布项目咨询知识",
                ) from exc
            raise
        if task_id:
            task = connection.execute(
                """
                SELECT project_id, lifecycle_state
                FROM task_records
                WHERE organization_id = ? AND task_id = ?
                """,
                (identity.organization_id, task_id),
            ).fetchone()
            if (
                task is None
                or _text(task["project_id"]) != project_id
                or _text(task["lifecycle_state"]) in {"archived", "cancelled"}
            ):
                raise RepositoryError(
                    422,
                    "consultation_task_project_mismatch",
                    "咨询来源任务必须属于同一当前项目",
                )
        if event_line_id:
            event_line = connection.execute(
                """
                SELECT project_id, lifecycle_state
                FROM event_line_records
                WHERE organization_id = ? AND event_line_id = ?
                """,
                (identity.organization_id, event_line_id),
            ).fetchone()
            if (
                event_line is None
                or _text(event_line["project_id"]) != project_id
                or _text(event_line["lifecycle_state"]) == "archived"
            ):
                raise RepositoryError(
                    422,
                    "consultation_event_line_project_mismatch",
                    "咨询来源事件线必须属于同一当前项目",
                )
        return project

    def _create_consultation_request(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        payload: Mapping[str, Any],
    ) -> Mutation:
        organization_id = _text(payload.get("organizationId"))
        membership_id = _text(payload.get("requestedByMembershipId"))
        project_id = _text(payload.get("projectId"))
        answer_id = _text(payload.get("answerId"))
        if not organization_id or not membership_id or not project_id:
            raise RepositoryError(
                422,
                "consultation_scope_required",
                "咨询知识请求必须明确绑定 organizationId、projectId 和 requestedByMembershipId",
            )
        if (
            organization_id != identity.organization_id
            or membership_id != identity.membership_id
        ):
            raise RepositoryError(
                403,
                "consultation_scope_mismatch",
                "咨询知识请求身份与当前组织会话不一致",
            )
        if not answer_id:
            raise RepositoryError(
                422,
                "consultation_answer_id_required",
                "咨询知识请求必须携带来源回答 ID",
            )
        target = _text(payload.get("target")) or "document_archive"
        if target not in {"vector_memory", "document_archive"}:
            raise RepositoryError(
                422,
                "consultation_target_invalid",
                "咨询知识目标类型无效",
            )
        summary, facts, content_hash = self._consultation_content(payload)
        task_id = _text(payload.get("taskId"))
        event_line_id = _text(payload.get("eventLineId"))
        project = self._validate_consultation_refs(
            connection,
            identity,
            project_id=project_id,
            task_id=task_id,
            event_line_id=event_line_id,
        )
        now = utc_now()
        request_id = new_id()
        source = {
            "schemaVersion": 1,
            "requestState": "pending",
            "organizationId": identity.organization_id,
            "projectId": project_id,
            "projectName": _text(project["name"]),
            "requestedByMembershipId": identity.membership_id,
            "requestedByPrincipalId": identity.principal_id,
            "requestedByName": identity.display_name,
            "answerId": answer_id[:200],
            "sourceRequestId": _text(payload.get("sourceRequestId"))[:200],
            "target": target,
            "taskId": task_id or None,
            "eventLineId": event_line_id or None,
            "questionHash": (
                sha256_text(_text(payload.get("question")))
                if _text(payload.get("question"))
                else ""
            ),
            "shareConfirmed": True,
            "shareableFacts": facts,
            "contentHash": content_hash,
            "retryCount": 0,
            "maxRetries": CONSULTATION_MAX_RETRIES,
            "lastErrorCode": None,
            "lastErrorMessage": None,
            "knowledgeDocumentId": None,
            "documentVersionId": None,
            "completedAt": None,
        }
        title = f"咨询知识沉淀 · {_text(project['name'])}"[:300]
        connection.execute(
            """
            INSERT INTO intelligence_records (
                intelligence_id, organization_id, project_id, title, summary,
                source_url, record_kind, status, visibility_scope,
                created_by_membership_id, source_payload_json, version,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '', ?, 'candidate', 'self', ?, ?, 1, ?, ?)
            """,
            (
                request_id,
                identity.organization_id,
                project_id,
                title,
                summary,
                CONSULTATION_REQUEST_KIND,
                identity.membership_id,
                canonical_json(source),
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO intelligence_revisions (
                intelligence_revision_id, organization_id, intelligence_id,
                revision, title, summary, revised_by_membership_id, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                new_id(),
                identity.organization_id,
                request_id,
                title,
                summary,
                identity.membership_id,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM intelligence_records WHERE intelligence_id = ?",
            (request_id,),
        ).fetchone()
        assert row is not None
        result = self._consultation_view(row, identity=identity)
        return Mutation(
            aggregate_type="consultation_knowledge_request",
            aggregate_id=request_id,
            before_version=None,
            after_version=1,
            result=result,
            event_type="consultation_knowledge.requested",
            summary={
                "requestId": request_id,
                "projectId": project_id,
                "answerId": answer_id[:200],
                "contentHash": content_hash,
                "shareableFactCount": len(facts),
                "shareConfirmed": True,
            },
            outbox_payload={
                "requestId": request_id,
                "projectId": project_id,
                "answerId": answer_id[:200],
                "contentHash": content_hash,
                "state": "pending",
            },
        )

    def _publish_consultation_request(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
    ) -> tuple[dict[str, Any], list[Mutation]]:
        source = _json(row["source_payload_json"], {})
        project_id = _text(row["project_id"])
        self._validate_consultation_refs(
            connection,
            identity,
            project_id=project_id,
            task_id=_text(source.get("taskId")),
            event_line_id=_text(source.get("eventLineId")),
        )
        summary = _text(row["summary"])[:12000]
        facts = [
            _text(value)[:500]
            for value in (source.get("shareableFacts") or [])[:20]
            if _text(value)
        ]
        expected_hash = sha256_text(
            canonical_json({"summary": summary, "facts": facts})
        )
        if (
            source.get("shareConfirmed") is not True
            or expected_hash != _text(source.get("contentHash"))
        ):
            raise RepositoryError(
                409,
                "consultation_content_integrity_failed",
                "咨询可共享内容确认或内容哈希校验失败",
            )
        before = int(row["version"])
        now = utc_now()
        document_id = new_id()
        document_version_id = new_id()
        sections = [
            "# 咨询知识沉淀",
            "",
            "## 已确认可共享摘要",
            summary,
        ]
        if facts:
            sections.extend(
                [
                    "",
                    "## 已确认事实",
                    *[f"- {fact}" for fact in facts],
                ]
            )
        source_request_id = _text(source.get("sourceRequestId"))
        if source_request_id:
            sections.extend(
                ["", f"来源请求：{source_request_id}"]
            )
        markdown = "\n".join(sections).strip()
        document_content_hash = sha256_text(markdown)
        connection.execute(
            """
            INSERT INTO knowledge_documents (
                document_id, organization_id, project_id,
                project_assignment_state, source_asset_id,
                owner_membership_id, department_id, title,
                document_kind, visibility_scope, parse_state,
                lifecycle_state, current_version, version,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'assigned', NULL, ?, NULL, ?,
                      'consultation_summary', 'organization', 'ready',
                      'active', 1, 1, ?, ?)
            """,
            (
                document_id,
                identity.organization_id,
                project_id,
                row["created_by_membership_id"],
                row["title"],
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO document_versions (
                document_version_id, organization_id, document_id,
                version, content_hash, preview_text, markdown_content,
                section_count, chunk_count, generator_version, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 1,
                      'consultation-knowledge-v4', ?)
            """,
            (
                document_version_id,
                identity.organization_id,
                document_id,
                document_content_hash,
                markdown[:2000],
                markdown,
                2 if facts else 1,
                now,
            ),
        )
        updated_source = {
            **source,
            "requestState": "completed",
            "lastErrorCode": None,
            "lastErrorMessage": None,
            "knowledgeDocumentId": document_id,
            "documentVersionId": document_version_id,
            "documentContentHash": document_content_hash,
            "completedAt": now,
        }
        changed = connection.execute(
            """
            UPDATE intelligence_records
            SET status = 'accepted', source_payload_json = ?,
                version = ?, updated_at = ?
            WHERE intelligence_id = ? AND organization_id = ?
              AND version = ? AND status IN ('candidate', 'returned')
            """,
            (
                canonical_json(updated_source),
                before + 1,
                now,
                row["intelligence_id"],
                identity.organization_id,
                before,
            ),
        )
        if changed.rowcount != 1:
            raise RepositoryError(
                409,
                "consultation_request_version_conflict",
                "咨询知识请求版本已变化，请刷新后重试",
            )
        connection.execute(
            """
            INSERT INTO intelligence_revisions (
                intelligence_revision_id, organization_id, intelligence_id,
                revision, title, summary, revised_by_membership_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                identity.organization_id,
                row["intelligence_id"],
                before + 1,
                row["title"],
                row["summary"],
                identity.membership_id,
                now,
            ),
        )
        updated_row = connection.execute(
            "SELECT * FROM intelligence_records WHERE intelligence_id = ?",
            (row["intelligence_id"],),
        ).fetchone()
        assert updated_row is not None
        request_result = self._consultation_view(
            updated_row,
            identity=identity,
        )
        children = [
            Mutation(
                aggregate_type="consultation_knowledge_request",
                aggregate_id=str(row["intelligence_id"]),
                before_version=before,
                after_version=before + 1,
                result=request_result,
                event_type="consultation_knowledge.completed",
                summary={
                    "requestId": row["intelligence_id"],
                    "projectId": project_id,
                    "knowledgeDocumentId": document_id,
                    "contentHash": expected_hash,
                },
                outbox_payload={
                    "requestId": row["intelligence_id"],
                    "projectId": project_id,
                    "knowledgeDocumentId": document_id,
                    "documentVersionId": document_version_id,
                    "contentHash": document_content_hash,
                    "state": "completed",
                },
            ),
            Mutation(
                aggregate_type="knowledge_document",
                aggregate_id=document_id,
                before_version=None,
                after_version=1,
                result={
                    "documentId": document_id,
                    "documentVersionId": document_version_id,
                },
                event_type="project_knowledge.consultation_summary_published",
                summary={
                    "requestId": row["intelligence_id"],
                    "projectId": project_id,
                    "documentVersionId": document_version_id,
                    "contentHash": document_content_hash,
                    "sourceType": "consultation_summary",
                },
                outbox_payload={
                    "requestId": row["intelligence_id"],
                    "projectId": project_id,
                    "documentId": document_id,
                    "documentVersionId": document_version_id,
                    "contentHash": document_content_hash,
                    "sourceType": "consultation_summary",
                },
            ),
        ]
        return request_result, children

    def _mark_consultation_failure(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> tuple[dict[str, Any], Mutation]:
        source = _json(row["source_payload_json"], {})
        before = int(row["version"])
        retries = int(source.get("retryCount") or 0) + 1
        retryable = retryable and retries < int(
            source.get("maxRetries") or CONSULTATION_MAX_RETRIES
        )
        state = "failed_retryable" if retryable else "blocked"
        now = utc_now()
        updated_source = {
            **source,
            "requestState": state,
            "retryCount": retries,
            "lastErrorCode": code[:120],
            "lastErrorMessage": message[:500],
        }
        changed = connection.execute(
            """
            UPDATE intelligence_records
            SET status = 'returned', source_payload_json = ?,
                version = ?, updated_at = ?
            WHERE intelligence_id = ? AND organization_id = ? AND version = ?
            """,
            (
                canonical_json(updated_source),
                before + 1,
                now,
                row["intelligence_id"],
                identity.organization_id,
                before,
            ),
        )
        if changed.rowcount != 1:
            raise RepositoryError(
                409,
                "consultation_request_version_conflict",
                "咨询知识请求版本已变化，请刷新后重试",
            )
        updated_row = connection.execute(
            "SELECT * FROM intelligence_records WHERE intelligence_id = ?",
            (row["intelligence_id"],),
        ).fetchone()
        assert updated_row is not None
        result = self._consultation_view(updated_row, identity=identity)
        child = Mutation(
            aggregate_type="consultation_knowledge_request",
            aggregate_id=str(row["intelligence_id"]),
            before_version=before,
            after_version=before + 1,
            result=result,
            event_type=f"consultation_knowledge.{state}",
            summary={
                "requestId": row["intelligence_id"],
                "projectId": row["project_id"],
                "state": state,
                "errorCode": code[:120],
                "retryCount": retries,
            },
            outbox_payload={
                "requestId": row["intelligence_id"],
                "projectId": row["project_id"],
                "state": state,
                "errorCode": code[:120],
                "retryCount": retries,
            },
        )
        return result, child

    def _process_consultation_requests(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        payload: Mapping[str, Any],
    ) -> Mutation:
        try:
            batch_size = max(min(int(payload.get("batchSize") or 20), 20), 1)
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                422,
                "consultation_batch_size_invalid",
                "batchSize 必须是 1 到 20 的整数",
            ) from exc
        clauses = [
            "organization_id = ?",
            "record_kind = ?",
            "status IN ('candidate', 'returned')",
        ]
        parameters: list[Any] = [
            identity.organization_id,
            CONSULTATION_REQUEST_KIND,
        ]
        if not identity.is_admin:
            clauses.append("created_by_membership_id = ?")
            parameters.append(identity.membership_id)
        rows = connection.execute(
            f"""
            SELECT * FROM intelligence_records
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at, intelligence_id
            """,
            parameters,
        ).fetchall()
        eligible = [
            row
            for row in rows
            if self._consultation_state(row) in {"pending", "failed_retryable"}
        ]
        selected = eligible[:batch_size]
        items: list[dict[str, Any]] = []
        children: list[Mutation] = []
        completed = 0
        failed = 0
        for index, row in enumerate(selected):
            savepoint = f"consultation_request_{index}"
            connection.execute(f"SAVEPOINT {savepoint}")
            try:
                item, request_children = self._publish_consultation_request(
                    connection,
                    identity,
                    row,
                )
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                items.append(item)
                children.extend(request_children)
                completed += 1
            except RepositoryError as exc:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                item, child = self._mark_consultation_failure(
                    connection,
                    identity,
                    row,
                    code=exc.code,
                    message=exc.message,
                    retryable=exc.status_code >= 500,
                )
                items.append(item)
                children.append(child)
                failed += 1
            except Exception:
                connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                item, child = self._mark_consultation_failure(
                    connection,
                    identity,
                    row,
                    code="consultation_processing_failed",
                    message="咨询知识沉淀暂时失败，请重试",
                    retryable=True,
                )
                items.append(item)
                children.append(child)
                failed += 1
        now = utc_now()
        batch_id = new_id()
        result = {
            "totalPending": len(eligible),
            "processedCount": len(selected),
            "completedCount": completed,
            "failedCount": failed,
            "skippedCount": max(len(eligible) - len(selected), 0),
            "updatedAt": now,
            "items": items,
        }
        return Mutation(
            aggregate_type="consultation_knowledge_batch",
            aggregate_id=batch_id,
            before_version=None,
            after_version=1,
            result=result,
            event_type="consultation_knowledge.batch_processed",
            summary={
                "batchId": batch_id,
                "totalPending": len(eligible),
                "processedCount": len(selected),
                "completedCount": completed,
                "failedCount": failed,
            },
            outbox_payload={
                "batchId": batch_id,
                "processedCount": len(selected),
                "completedCount": completed,
                "failedCount": failed,
            },
            children=children,
        )

    def _retry_consultation_request(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        request_id: str,
        payload: Mapping[str, Any],
    ) -> Mutation:
        row = self._intelligence_row(connection, identity, request_id)
        if _text(row["record_kind"]) != CONSULTATION_REQUEST_KIND:
            raise RepositoryError(
                404,
                "consultation_request_missing",
                "咨询知识请求不存在",
            )
        before = int(row["version"])
        _require_expected(payload, before)
        source = _json(row["source_payload_json"], {})
        if self._consultation_state(row) not in {
            "failed_retryable",
            "blocked",
        }:
            raise RepositoryError(
                409,
                "consultation_request_not_retryable",
                "只有失败或受阻的咨询知识请求可以重试",
            )
        self._validate_consultation_refs(
            connection,
            identity,
            project_id=_text(row["project_id"]),
            task_id=_text(source.get("taskId")),
            event_line_id=_text(source.get("eventLineId")),
        )
        if int(source.get("retryCount") or 0) >= int(
            source.get("maxRetries") or CONSULTATION_MAX_RETRIES
        ):
            raise RepositoryError(
                409,
                "consultation_retry_limit_reached",
                "咨询知识请求已达到重试上限",
            )
        now = utc_now()
        updated_source = {
            **source,
            "requestState": "pending",
            "lastErrorCode": None,
            "lastErrorMessage": None,
        }
        changed = connection.execute(
            """
            UPDATE intelligence_records
            SET status = 'candidate', source_payload_json = ?,
                version = ?, updated_at = ?
            WHERE intelligence_id = ? AND organization_id = ? AND version = ?
            """,
            (
                canonical_json(updated_source),
                before + 1,
                now,
                request_id,
                identity.organization_id,
                before,
            ),
        )
        if changed.rowcount != 1:
            raise RepositoryError(
                409,
                "consultation_request_version_conflict",
                "咨询知识请求版本已变化，请刷新后重试",
            )
        updated_row = connection.execute(
            "SELECT * FROM intelligence_records WHERE intelligence_id = ?",
            (request_id,),
        ).fetchone()
        assert updated_row is not None
        result = self._consultation_view(updated_row, identity=identity)
        return Mutation(
            aggregate_type="consultation_knowledge_request",
            aggregate_id=request_id,
            before_version=before,
            after_version=before + 1,
            result=result,
            event_type="consultation_knowledge.retry_requested",
            summary={
                "requestId": request_id,
                "projectId": row["project_id"],
                "retryCount": int(source.get("retryCount") or 0),
            },
            outbox_payload={
                "requestId": request_id,
                "projectId": row["project_id"],
                "state": "pending",
            },
        )

    def _intelligence_facts(
        self,
        identity: SessionIdentity,
    ) -> list[dict[str, Any]]:
        snapshot = self._snapshot(identity)
        visible = {
            _text(item.get("intelligenceId")): item
            for item in snapshot.get("intelligence") or []
        }
        if not visible:
            return []
        placeholders = ",".join("?" for _ in visible)
        with self.repository._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM intelligence_records
                WHERE organization_id = ?
                  AND intelligence_id IN ({placeholders})
                ORDER BY updated_at DESC, intelligence_id
                """,
                (identity.organization_id, *visible),
            ).fetchall()
            revision_rows = connection.execute(
                f"""
                SELECT * FROM intelligence_revisions
                WHERE organization_id = ?
                  AND intelligence_id IN ({placeholders})
                ORDER BY intelligence_id, revision DESC
                """,
                (identity.organization_id, *visible),
            ).fetchall()
        revisions: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in revision_rows:
            revisions[str(row["intelligence_id"])].append(
                {
                    "revisionId": row["intelligence_revision_id"],
                    "revision": row["revision"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "revisedByMembershipId": row["revised_by_membership_id"],
                    "createdAt": row["created_at"],
                }
            )
        return [
            {
                "id": row["intelligence_id"],
                "intelligenceId": row["intelligence_id"],
                "projectId": row["project_id"],
                "title": row["title"],
                "summary": row["summary"],
                "sourceUrl": row["source_url"],
                "recordKind": row["record_kind"],
                "status": row["status"],
                "visibilityScope": row["visibility_scope"],
                "createdByMembershipId": row["created_by_membership_id"],
                "sourcePayload": _json(row["source_payload_json"], {}),
                "version": row["version"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "revisions": revisions.get(str(row["intelligence_id"]), []),
            }
            for row in rows
        ]

    def _growth_cards(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM growth_cards AS card
                WHERE card.organization_id = ?
                  AND card.lifecycle_state = 'active'
                  AND (
                    card.membership_id = ?
                    OR card.visibility_scope = 'organization'
                    OR (
                      card.visibility_scope = 'department'
                      AND EXISTS (
                        SELECT 1
                        FROM department_memberships AS owner_department
                        JOIN department_memberships AS viewer_department
                          ON viewer_department.organization_id =
                             owner_department.organization_id
                         AND viewer_department.department_id =
                             owner_department.department_id
                         AND viewer_department.membership_id = ?
                         AND viewer_department.status = 'active'
                        WHERE owner_department.organization_id =
                              card.organization_id
                          AND owner_department.membership_id =
                              card.membership_id
                          AND owner_department.status = 'active'
                      )
                    )
                  )
                ORDER BY card.updated_at DESC, card.growth_card_id
                """,
                (
                    identity.organization_id,
                    identity.membership_id,
                    identity.membership_id,
                ),
            ).fetchall()
        return [
            {
                "id": row["growth_card_id"],
                "growthCardId": row["growth_card_id"],
                "membershipId": row["membership_id"],
                "weeklyReviewId": row["weekly_review_id"],
                "contentDomain": row["content_domain"],
                "visibilityScope": row["visibility_scope"],
                "summary": _json(row["summary_json"], {}),
                "suggestions": _json(row["suggestions_json"], []),
                "lifecycleState": row["lifecycle_state"],
                "version": row["version"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def _experience_wall(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        snapshot = self._snapshot(identity)
        quotes = snapshot.get("experienceQuotes") or []
        quote_ids = [_text(item.get("experienceQuoteId")) for item in quotes]
        reactions: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"likeCount": 0, "saveCount": 0, "likedByMe": False, "savedByMe": False}
        )
        if quote_ids:
            placeholders = ",".join("?" for _ in quote_ids)
            with self.repository._connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT experience_quote_id, membership_id, reaction_type
                    FROM experience_reactions
                    WHERE organization_id = ?
                      AND experience_quote_id IN ({placeholders})
                    """,
                    (identity.organization_id, *quote_ids),
                ).fetchall()
            for row in rows:
                state = reactions[str(row["experience_quote_id"])]
                reaction = str(row["reaction_type"])
                state[f"{reaction}Count"] += 1
                if str(row["membership_id"]) == identity.membership_id:
                    state["likedByMe" if reaction == "like" else "savedByMe"] = True
        return [
            {
                "id": item.get("experienceQuoteId"),
                "source": "exp_wall",
                "text": item.get("quoteText") or "",
                "summary": item.get("sourceExcerpt") or item.get("quoteText") or "",
                "sourceType": item.get("sourceType") or "",
                "sourceObjectId": item.get("sourceId") or "",
                "sourceTitle": item.get("sourceExcerpt") or None,
                "category": item.get("category") or "方法论",
                "authorUserId": item.get("authorMembershipId"),
                "authorUserName": None,
                "clientId": None,
                "clientName": None,
                "reuseCount": reactions[
                    _text(item.get("experienceQuoteId"))
                ]["saveCount"],
                "contributionScore": item.get("contributionScore") or 0,
                "version": item.get("version"),
                "linkedContexts": [],
                "createdAt": item.get("updatedAt"),
                "currentUserLiked": reactions[
                    _text(item.get("experienceQuoteId"))
                ]["likedByMe"],
                **reactions[_text(item.get("experienceQuoteId"))],
            }
            for item in quotes
        ]

    def _intelligence_item(self, item: Mapping[str, Any]) -> dict[str, Any]:
        payload = item.get("sourcePayload") or {}
        strict_status = _text(item.get("status"))
        content_kind = _text(payload.get("contentKind"))
        if content_kind not in {
            "brand_mirror",
            "timely_intelligence",
            "public_opinion",
        }:
            content_kind = (
                "public_opinion"
                if "sentiment" in _text(item.get("recordKind"))
                else "brand_mirror"
                if "brand" in _text(item.get("recordKind"))
                else "timely_intelligence"
            )
        return {
            "id": item.get("id"),
            "contentKind": content_kind,
            "scopeType": "client" if item.get("projectId") else None,
            "scopeId": item.get("projectId"),
            "clientId": item.get("projectId"),
            "projectModuleId": None,
            "title": item.get("title") or "",
            "summary": item.get("summary") or "",
            "keyPoints": payload.get("keyPoints") or [],
            "analysis": payload.get("analysis") or item.get("summary") or "",
            "impact": payload.get("impact") or "",
            "intelligenceType": item.get("recordKind"),
            "timelinessLabel": payload.get("timelinessLabel"),
            "relevanceReason": payload.get("relevanceReason") or "",
            "suggestedAction": payload.get("suggestedAction") or "",
            "followupQuestions": payload.get("followupQuestions") or [],
            "tags": payload.get("tags") or [],
            "source": payload.get("sourceName") or item.get("recordKind") or "",
            "sourceUrl": item.get("sourceUrl") or None,
            "publishedAt": payload.get("publishedAt"),
            "capturedAt": item.get("createdAt"),
            "verifiedAt": (
                item.get("updatedAt") if strict_status == "accepted" else None
            ),
            "dataCenterIngestEventId": payload.get("dataCenterIngestEventId"),
            "externalEvidenceCardId": payload.get("externalEvidenceCardId"),
            "topicCandidateId": payload.get("topicCandidateId"),
            "convertedTaskId": payload.get("convertedTaskId"),
            "verificationStatus": (
                "verified"
                if strict_status == "accepted"
                else "rejected"
                if strict_status == "returned"
                else "pending"
            ),
            "verificationReason": payload.get("verificationReason") or "",
            "userStatus": (
                "following"
                if strict_status == "accepted"
                else "dismissed"
                if strict_status == "archived"
                else "active"
            ),
            "version": item.get("version"),
            "createdAt": item.get("createdAt"),
            "updatedAt": item.get("updatedAt"),
        }

    def _proposal(self, item: Mapping[str, Any]) -> dict[str, Any]:
        payload = item.get("sourcePayload") or {}
        authority_status = _text(item.get("status"))
        status = {
            "candidate": "draft",
            "inbox": "pending_review",
            "accepted": "approved",
            "returned": "rejected",
            "archived": "failed",
        }.get(authority_status, "draft")
        proposal_kind = _text(payload.get("kind"))
        if proposal_kind not in {
            "task_prep",
            "meeting_prep",
            "meeting_followup",
            "evidence_request",
            "judgment_review",
            "context_refresh",
        }:
            proposal_kind = "context_refresh"
        return {
            "id": item.get("id"),
            "proposalId": item.get("id"),
            "draftId": item.get("id"),
            "clientId": item.get("projectId") or "",
            "kind": proposal_kind,
            "title": item.get("title"),
            "summary": item.get("summary"),
            "description": item.get("summary"),
            "projectId": item.get("projectId"),
            "sourceType": payload.get("sourceType") or item.get("recordKind"),
            "sourceId": payload.get("sourceId"),
            "status": status,
            "authorityStatus": authority_status,
            "reviewState": (
                "approved"
                if authority_status == "accepted"
                else "rejected"
                if authority_status in {"returned", "archived"}
                else "pending"
            ),
            "riskLevel": payload.get("riskLevel") or "medium",
            "rationale": payload.get("rationale") or item.get("summary") or "",
            "targetRefs": payload.get("targetRefs") or [],
            "sourceRefs": payload.get("sourceRefs") or [],
            "boundaryNotes": payload.get("boundaryNotes") or [],
            "payload": payload,
            "createdBy": item.get("createdByMembershipId") or "",
            "decidedBy": payload.get("decidedBy"),
            "decidedAt": (
                item.get("updatedAt")
                if authority_status in {"accepted", "returned"}
                else None
            ),
            "rejectedReason": payload.get("rejectedReason"),
            "executionTicketId": payload.get("executionTicketId"),
            "taskDrafts": payload.get("taskDrafts") or [],
            "evidenceRefs": payload.get("evidenceRefs") or [],
            "version": item.get("version"),
            "createdAt": item.get("createdAt"),
            "updatedAt": item.get("updatedAt"),
        }

    def _proposal_from_row(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return self._proposal(
            {
                "id": row["intelligence_id"],
                "projectId": row["project_id"],
                "title": row["title"],
                "summary": row["summary"],
                "recordKind": row["record_kind"],
                "status": row["status"],
                "createdByMembershipId": row["created_by_membership_id"],
                "sourcePayload": _json(row["source_payload_json"], {}),
                "version": row["version"],
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
        )

    def _execute_proposal_task_drafts(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        proposal_row: Mapping[str, Any],
        execution_source_id: str,
    ) -> tuple[list[str], list[Mutation]]:
        proposal_id = str(proposal_row["intelligence_id"])
        source_payload = _json(proposal_row["source_payload_json"], {})
        raw_drafts = source_payload.get("taskDrafts") or []
        drafts = [
            dict(item)
            for item in raw_drafts
            if isinstance(item, Mapping) and _text(item.get("title"))
        ]
        if not drafts:
            raise RepositoryError(
                409,
                "proposal_has_no_task_drafts",
                "提案没有可执行任务草案",
            )
        existing = connection.execute(
            """
            SELECT task_id
            FROM task_records
            WHERE organization_id = ?
              AND source_type = 'proposal_execution'
              AND source_id = ?
              AND lifecycle_state != 'archived'
            ORDER BY created_at, task_id
            """,
            (identity.organization_id, execution_source_id),
        ).fetchall()
        if existing:
            return [str(row["task_id"]) for row in existing], []

        project_id = _text(proposal_row["project_id"]) or None
        self.repository._ensure_project(connection, identity, project_id)
        now = utc_now()
        task_ids: list[str] = []
        mutations: list[Mutation] = []
        for index, draft in enumerate(drafts):
            try:
                duration_minutes = max(
                    int(draft.get("durationMinutes") or 60),
                    1,
                )
            except (TypeError, ValueError) as exc:
                raise RepositoryError(
                    422,
                    "proposal_task_draft_duration_invalid",
                    f"第 {index + 1} 条任务草案的时长必须是整数",
                ) from exc
            owner_id = _text(
                draft.get("ownerMembershipId")
                or draft.get("assigneeMembershipId")
                or draft.get("ownerId")
            ) or identity.membership_id
            collaborator_ids = {
                _text(value)
                for value in draft.get("collaboratorMembershipIds") or []
                if _text(value)
            }
            collaborator_ids.discard(owner_id)
            self.repository._ensure_memberships(
                connection,
                identity,
                {owner_id, *collaborator_ids},
            )
            task_id = new_id()
            attributes = {
                "proposalId": proposal_id,
                "proposalVersion": int(proposal_row["version"]),
                "draftIndex": index,
                "executionSourceId": execution_source_id,
            }
            connection.execute(
                """
                INSERT INTO task_records (
                    task_id, organization_id, project_id, title, description,
                    created_by_membership_id, priority, lifecycle_state,
                    task_kind, visibility_scope, start_date, due_date,
                    scheduled_start_at, scheduled_end_at, deadline_at,
                    duration_minutes, completion_note, completed_at,
                    source_type, source_id, attributes_json, version,
                    created_at, updated_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'todo', 'task', ?, ?, ?, ?, ?,
                          ?, ?, '', NULL, 'proposal_execution', ?, ?, 1, ?, ?,
                          NULL)
                """,
                (
                    task_id,
                    identity.organization_id,
                    project_id,
                    _text(draft.get("title")),
                    _text(draft.get("description") or draft.get("summary")),
                    identity.membership_id,
                    _text(draft.get("priority")) or "normal",
                    _text(draft.get("visibilityScope")) or "participants",
                    draft.get("startDate"),
                    draft.get("dueDate"),
                    draft.get("scheduledStartAt"),
                    draft.get("scheduledEndAt"),
                    draft.get("deadlineAt"),
                    duration_minutes,
                    execution_source_id,
                    canonical_json(attributes),
                    now,
                    now,
                ),
            )
            members = [
                (owner_id, "owner", 0),
                *[
                    (membership_id, "collaborator", order + 1)
                    for order, membership_id in enumerate(
                        sorted(collaborator_ids)
                    )
                ],
            ]
            for membership_id, role, order_index in members:
                inbox_state = (
                    "accepted"
                    if role == "owner"
                    and membership_id == identity.membership_id
                    else (
                        "acknowledged"
                        if role == "collaborator"
                        and membership_id == identity.membership_id
                        else "pending"
                    )
                )
                connection.execute(
                    """
                    INSERT INTO task_collaborators (
                        task_id, organization_id, membership_id,
                        collaborator_role, inbox_state, order_index,
                        return_reason, handled_at, version, created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, '', ?, 1, ?, ?)
                    """,
                    (
                        task_id,
                        identity.organization_id,
                        membership_id,
                        role,
                        inbox_state,
                        order_index,
                        now if inbox_state != "pending" else None,
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO task_activity_events (
                    task_activity_id, organization_id, task_id,
                    actor_membership_id, event_type, payload_json, happened_at
                ) VALUES (?, ?, ?, ?, 'task.created_from_proposal', ?, ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    task_id,
                    identity.membership_id,
                    canonical_json(
                        {
                            "proposalId": proposal_id,
                            "proposalVersion": int(proposal_row["version"]),
                            "draftIndex": index,
                        }
                    ),
                    now,
                ),
            )
            task_ids.append(task_id)
            mutations.append(
                Mutation(
                    aggregate_type="task",
                    aggregate_id=task_id,
                    before_version=None,
                    after_version=1,
                    result={"taskId": task_id},
                    event_type="task.created_from_proposal",
                    summary={
                        "proposalId": proposal_id,
                        "draftIndex": index,
                    },
                    outbox_payload={
                        "taskId": task_id,
                        "proposalId": proposal_id,
                        "projectId": project_id,
                    },
                )
            )
        return task_ids, mutations

    def _mark_proposal_executed(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        proposal_row: Mapping[str, Any],
        ticket_id: str,
    ) -> Mutation:
        proposal_id = str(proposal_row["intelligence_id"])
        before = int(proposal_row["version"])
        source_payload = _json(proposal_row["source_payload_json"], {})
        existing_ticket = _text(source_payload.get("executionTicketId"))
        if existing_ticket and existing_ticket != ticket_id:
            raise RepositoryError(
                409,
                "proposal_already_executed",
                "该提案已通过另一张执行票据生成任务",
            )
        if existing_ticket == ticket_id:
            return Mutation(
                aggregate_type="proposal",
                aggregate_id=proposal_id,
                before_version=before,
                after_version=before,
                result={
                    "proposalId": proposal_id,
                    "executionTicketId": ticket_id,
                },
                event_type="proposal.execution_replayed",
                summary={
                    "proposalId": proposal_id,
                    "ticketId": ticket_id,
                },
            )
        after = before + 1
        updated_payload = {
            **source_payload,
            "executionTicketId": ticket_id,
            "executionStatus": "executed",
            "executedAt": utc_now(),
        }
        now = utc_now()
        changed = connection.execute(
            """
            UPDATE intelligence_records
            SET source_payload_json = ?, version = ?, updated_at = ?
            WHERE intelligence_id = ? AND organization_id = ? AND version = ?
            """,
            (
                canonical_json(updated_payload),
                after,
                now,
                proposal_id,
                identity.organization_id,
                before,
            ),
        )
        if changed.rowcount != 1:
            raise RepositoryError(
                409,
                "version_conflict",
                "提案版本已变化，请刷新后重试",
            )
        connection.execute(
            """
            INSERT INTO intelligence_revisions (
                intelligence_revision_id, organization_id, intelligence_id,
                revision, title, summary, revised_by_membership_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                identity.organization_id,
                proposal_id,
                after,
                proposal_row["title"],
                proposal_row["summary"],
                identity.membership_id,
                now,
            ),
        )
        return Mutation(
            aggregate_type="proposal",
            aggregate_id=proposal_id,
            before_version=before,
            after_version=after,
            result={
                "proposalId": proposal_id,
                "executionTicketId": ticket_id,
            },
            event_type="proposal.executed",
            summary={
                "proposalId": proposal_id,
                "ticketId": ticket_id,
            },
            outbox_payload={
                "proposalId": proposal_id,
                "ticketId": ticket_id,
                "version": after,
            },
        )

    def _execution_ticket(
        self,
        identity: SessionIdentity,
        row: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = _json(row.get("payload_json"), {})
        resource_path = _text(payload.get("resourcePath"))
        proposal_match = re.fullmatch(
            r"proposals/([^/]+)/(?:execute|execution-ticket)",
            resource_path,
        )
        proposal_id = (
            _text(payload.get("proposalId"))
            or (proposal_match.group(1) if proposal_match else "")
        )
        ticket_id = _text(row.get("command_id"))
        with self.repository._connection() as connection:
            task_rows = connection.execute(
                """
                SELECT task_id
                FROM task_records
                WHERE organization_id = ?
                  AND source_type = 'proposal_execution'
                  AND source_id = ?
                  AND lifecycle_state != 'archived'
                ORDER BY created_at, task_id
                """,
                (identity.organization_id, ticket_id),
            ).fetchall()
        created_task_ids = [str(item["task_id"]) for item in task_rows]
        result = _json(row.get("result_json"), {})
        result["createdTaskIds"] = created_task_ids
        return {
            "id": ticket_id,
            "proposalId": proposal_id,
            "clientId": _text(payload.get("clientId")),
            "executionType": "proposal_tasks",
            "status": (
                "failed"
                if row.get("latest_error_code")
                else "executed"
                if created_task_ids
                else "running"
                if row.get("latest_transport_state") in {"queued", "running"}
                else "executed"
                if row.get("latest_transport_state") in {"delivered", "success"}
                else "pending"
            ),
            "payload": payload,
            "result": {
                "resultType": (
                    "tasks_created" if created_task_ids else "recorded_only"
                ),
                "summary": (
                    f"已创建 {len(created_task_ids)} 条严格任务"
                    if created_task_ids
                    else "已登记严格执行票据，等待明确执行"
                ),
                "createdTaskIds": created_task_ids,
                "artifactRefs": result.get("artifactRefs") or [],
            },
            "idempotencyKey": row.get("idempotency_key"),
            "retryCount": max(int(row.get("attempt_count") or 0) - 1, 0),
            "maxRetries": 3,
            "lastError": row.get("latest_error_code"),
            "lastAttemptAt": row.get("last_attempt_at"),
            "errorMessage": row.get("latest_error_code"),
            "executedAt": None,
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }

    def _proposals(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        return [
            self._proposal(item)
            for item in self._intelligence_facts(identity)
            if _text(item.get("recordKind")) in PROPOSAL_KINDS
        ]

    def _operations(self, identity: SessionIdentity) -> dict[str, Any]:
        _require_admin(identity)
        with self.repository._connection() as connection:
            command_rows = connection.execute(
                """
                SELECT c.*, COUNT(a.attempt_id) AS attempt_count,
                       MAX(a.created_at) AS last_attempt_at,
                       (
                         SELECT latest.transport_state
                         FROM operation_attempts latest
                         WHERE latest.command_id = c.command_id
                         ORDER BY latest.attempt_no DESC
                         LIMIT 1
                       ) AS latest_transport_state,
                       (
                         SELECT latest.error_code
                         FROM operation_attempts latest
                         WHERE latest.command_id = c.command_id
                         ORDER BY latest.attempt_no DESC
                         LIMIT 1
                       ) AS latest_error_code
                FROM command_envelopes c
                LEFT JOIN operation_attempts a ON a.command_id = c.command_id
                WHERE c.organization_id = ?
                GROUP BY c.command_id
                ORDER BY c.updated_at DESC, c.command_id
                """,
                (identity.organization_id,),
            ).fetchall()
            outbox_rows = connection.execute(
                """
                SELECT * FROM delivery_outbox
                WHERE organization_id = ?
                ORDER BY updated_at DESC, event_id
                """,
                (identity.organization_id,),
            ).fetchall()
            dead_rows = connection.execute(
                """
                SELECT * FROM operation_dead_letters
                WHERE organization_id = ?
                ORDER BY created_at DESC, dead_letter_id
                """,
                (identity.organization_id,),
            ).fetchall()
            reconciliation_rows = connection.execute(
                """
                SELECT * FROM reconciliation_runs
                WHERE organization_id = ?
                ORDER BY started_at DESC, run_id
                """,
                (identity.organization_id,),
            ).fetchall()
            bulk_rows = connection.execute(
                """
                SELECT * FROM bulk_operations
                WHERE organization_id = ?
                ORDER BY updated_at DESC, bulk_operation_id
                """,
                (identity.organization_id,),
            ).fetchall()
        return {
            "commands": [dict(row) for row in command_rows],
            "outbox": [dict(row) for row in outbox_rows],
            "deadLetters": [dict(row) for row in dead_rows],
            "reconciliationRuns": [dict(row) for row in reconciliation_rows],
            "bulkOperations": [dict(row) for row in bulk_rows],
        }

    # ---------- rebuildable domain views ----------

    def _topic_view(self, identity: SessionIdentity) -> dict[str, Any]:
        all_facts = self._intelligence_facts(identity)
        configured_radars = [
            item
            for item in all_facts
            if item["recordKind"] == "topic_radar"
            and item["status"] != "archived"
        ]
        facts = [
            item
            for item in all_facts
            if item["recordKind"] not in INTERNAL_CONFIGURATION_KINDS
            and item["recordKind"] not in PROPOSAL_KINDS
            and item["recordKind"] not in {"topic_radar", "profile_run"}
            and (item.get("sourcePayload") or {}).get("contentKind")
            != "public_opinion"
        ]
        projects = {
            _text(item.get("projectId")): item
            for item in self._snapshot(identity).get("projects") or []
        }
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in facts:
            grouped[_text(item.get("projectId")) or "organization"].append(item)
        radars = [
            {
                "id": item["id"],
                "title": item["title"],
                "prompt": item["summary"],
                "timeRange": (item.get("sourcePayload") or {}).get(
                    "timeRange"
                )
                or "7d",
                "preferredSources": (
                    item.get("sourcePayload") or {}
                ).get("preferredSources")
                or [],
                "createdAt": item["createdAt"],
                "updatedAt": item["updatedAt"],
                "version": item["version"],
                "derived": False,
            }
            for item in configured_radars
        ]
        for key, items in sorted(grouped.items()):
            project = projects.get(key) or {}
            radars.append(
                {
                    "id": f"derived:{key}",
                    "title": project.get("name") or "组织情报雷达",
                    "prompt": project.get("summary") or "",
                    "projectId": None if key == "organization" else key,
                    "candidateCount": sum(
                        item["status"] in {"candidate", "inbox"} for item in items
                    ),
                    "acceptedCount": sum(item["status"] == "accepted" for item in items),
                    "derived": True,
                    "updatedAt": max(
                        (_text(item.get("updatedAt")) for item in items),
                        default=None,
                    ),
                }
            )
        candidates = [
            {
                "id": item["id"],
                "radarId": (item.get("sourcePayload") or {}).get("radarId")
                or f"derived:{_text(item.get('projectId')) or 'organization'}",
                "title": item["title"],
                "summary": item["summary"],
                "source": (item.get("sourcePayload") or {}).get("sourceName")
                or item["recordKind"],
                "sourceUrl": item["sourceUrl"],
                "publishedAt": (item.get("sourcePayload") or {}).get("publishedAt"),
                "captureMethod": (item.get("sourcePayload") or {}).get("captureMethod")
                or "strict_v2_authority",
                "status": item["status"],
                "insightStatus": "ready" if item["summary"] else "source_only",
                "clientId": item["projectId"],
                "projectId": item["projectId"],
                "version": item["version"],
                "createdAt": item["createdAt"],
                "updatedAt": item["updatedAt"],
            }
            for item in facts
            if item["status"] != "archived"
        ]
        return {
            "radars": radars,
            "candidates": candidates,
            "intelligenceProfiles": self._profiles(identity),
            "derivedAt": utc_now(),
            "authoritySource": "intelligence_records/intelligence_revisions",
        }

    def _profiles(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        profiles = []
        for item in self._intelligence_facts(identity):
            if item["recordKind"] != "intelligence_profile":
                continue
            payload = item.get("sourcePayload") or {}
            project_id = _text(item.get("projectId"))
            focus = payload.get("focus") or payload.get("queries") or []
            exclude_terms = (
                payload.get("excludeTerms")
                or payload.get("exclude")
                or []
            )
            priority_urls = (
                payload.get("priorityUrls")
                or [
                    source.get("url")
                    for source in payload.get("sources") or []
                    if isinstance(source, Mapping) and source.get("url")
                ]
            )
            profiles.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "radarId": payload.get("radarId"),
                    "radarTitle": payload.get("radarTitle"),
                    "profileKind": payload.get("profileKind") or "custom",
                    "scopeType": "client" if project_id else "organization",
                    "scopeId": project_id or None,
                    "clientId": project_id or None,
                    "projectModuleId": None,
                    "status": item["status"],
                    "profileReadiness": (
                        "ready"
                        if item["summary"] or focus
                        else "needs_context"
                    ),
                    "summary": item["summary"],
                    "effectiveSummary": (
                        payload.get("adminSummaryOverride")
                        or item["summary"]
                    ),
                    "adminSummaryOverride": payload.get(
                        "adminSummaryOverride"
                    ),
                    "adminFocus": focus,
                    "adminExcludeTerms": exclude_terms,
                    "adminPriorityUrls": priority_urls,
                    "adminProfileRefreshEnabled": bool(
                        payload.get("profileRefreshEnabled")
                    ),
                    "adminProfileRefreshFrequency": (
                        payload.get("profileRefreshFrequency")
                        or "manual"
                    ),
                    "adminPushEnabled": bool(payload.get("pushEnabled")),
                    "adminPushFrequency": (
                        payload.get("pushFrequency") or "manual"
                    ),
                    "materialCount": int(payload.get("materialCount") or 0),
                    "materialSummary": payload.get("materialSummary") or [],
                    "workContext": payload.get("workContext") or [],
                    "priorityNeeds": payload.get("priorityNeeds") or [],
                    "targetBeneficiaries": payload.get(
                        "targetBeneficiaries"
                    )
                    or [],
                    "regions": payload.get("regions") or [],
                    "opportunityTypes": payload.get("opportunityTypes") or [],
                    "materialGaps": payload.get("materialGaps") or [],
                    "groundingFacts": payload.get("groundingFacts") or [],
                    "backgroundEnrichments": payload.get(
                        "backgroundEnrichments"
                    )
                    or [],
                    "lastFetch": payload.get("lastFetch"),
                    "nextProfileRefreshAt": payload.get(
                        "nextProfileRefreshAt"
                    ),
                    "nextIntelligenceFetchAt": payload.get(
                        "nextIntelligenceFetchAt"
                    ),
                    "lastAutomationResult": payload.get(
                        "lastAutomationResult"
                    ),
                    "deletedAt": None,
                    "version": item["version"],
                    "createdAt": item["createdAt"],
                    "updatedAt": item["updatedAt"],
                }
            )
        return profiles

    def _growth_view(self, identity: SessionIdentity) -> dict[str, Any]:
        snapshot = self._snapshot(identity)
        # Growth overview, ledger, pending confirmations, and badges are personal
        # views. Administrators may read organization-wide facts in the shared
        # snapshot, but those facts must never be counted as the administrator's
        # own XP or badge evidence.
        signals = [
            item
            for item in snapshot.get("growthSignals") or []
            if _text(item.get("membershipId")) == identity.membership_id
        ]
        evidence = [
            item
            for item in snapshot.get("growthEvidence") or []
            if _text(item.get("membershipId")) == identity.membership_id
        ]
        cards = self._growth_cards(identity)
        wall = self._experience_wall(identity)
        ability_counts = Counter(
            _text(item.get("abilityKey"))
            for item in evidence
            if item.get("validationState") == "confirmed"
        )
        confirmed_signal_count = sum(
            item.get("lifecycleState") == "confirmed" for item in signals
        )
        pending_signals = [
            {
                "id": item.get("growthSignalId"),
                "rawText": item.get("rawText") or "",
                "sourceType": item.get("sourceType"),
                "sourceId": item.get("sourceId"),
                "weekLabel": item.get("weekLabel"),
                "state": item.get("lifecycleState"),
                "version": item.get("version"),
                "updatedAt": item.get("updatedAt"),
            }
            for item in signals
            if item.get("lifecycleState") == "candidate"
        ]
        recommendations = [
            {
                "id": item.get("growthEvidenceId"),
                "abilityKey": item.get("abilityKey"),
                "level": item.get("level"),
                "confidence": item.get("confidence"),
                "reason": item.get("reason") or "",
                "taskId": item.get("taskId"),
                "state": item.get("validationState"),
                "version": item.get("version"),
                "updatedAt": item.get("updatedAt"),
            }
            for item in evidence
            if item.get("validationState") == "candidate"
        ]
        badges = [
            {
                "id": f"ability:{ability}",
                "name": ability,
                "abilityKey": ability,
                "evidenceCount": count,
                "level": (
                    "advanced" if count >= 5 else "practiced" if count >= 2 else "started"
                ),
                "derived": True,
            }
            for ability, count in sorted(ability_counts.items())
        ]
        return {
            "signals": signals,
            "evidence": evidence,
            "cards": cards,
            "experienceWall": wall,
            "pendingCaptures": pending_signals,
            "recommendations": recommendations,
            "badges": badges,
            "stats": {
                "signalCount": len(signals),
                "confirmedSignalCount": confirmed_signal_count,
                "evidenceCount": len(evidence),
                "confirmedEvidenceCount": sum(
                    item.get("validationState") == "confirmed" for item in evidence
                ),
                "cardCount": len(cards),
                "experienceQuoteCount": len(wall),
            },
            "derivedAt": utc_now(),
            "authoritySource": (
                "growth_signals/growth_evidence/growth_cards/"
                "experience_quotes/experience_reactions"
            ),
        }

    def _growth_ledger_entries(
        self,
        identity: SessionIdentity,
        growth: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        signal_by_id = {
            _text(item.get("growthSignalId")): item
            for item in growth.get("signals") or []
        }
        entries = []
        for item in growth.get("evidence") or []:
            signal = signal_by_id.get(_text(item.get("growthSignalId"))) or {}
            confirmed = item.get("validationState") == "confirmed"
            delta = 1 if confirmed else 0
            entries.append(
                {
                    "id": item.get("growthEvidenceId"),
                    "userId": item.get("membershipId"),
                    "userName": (
                        identity.display_name
                        if item.get("membershipId") == identity.membership_id
                        else ""
                    ),
                    "abilityKey": item.get("abilityKey") or "system_thinking",
                    "abilityLabel": item.get("abilityKey") or "成长证据",
                    "evidenceId": item.get("growthEvidenceId"),
                    "xpType": item.get("evidenceType") or "growth_evidence",
                    "delta": delta,
                    "baseXp": delta,
                    "premiumRate": 0,
                    "premiumXp": 0,
                    "totalXp": delta,
                    "reason": item.get("reason") or "",
                    "sourceType": signal.get("sourceType") or item.get("evidenceType"),
                    "sourceId": signal.get("sourceId") or "",
                    "sourceTitle": signal.get("rawText") or None,
                    "handbookEntryId": None,
                    "taskId": item.get("taskId"),
                    "meetingId": None,
                    "reviewId": None,
                    "clientId": None,
                    "clientName": None,
                    "eventLineId": None,
                    "eventLineName": None,
                    "businessCategory": None,
                    "projectStage": None,
                    "sourceRoute": [
                        value
                        for value in [
                            _text(signal.get("sourceType")),
                            _text(signal.get("sourceId")),
                        ]
                        if value
                    ],
                    "evidenceRefs": [str(item.get("growthEvidenceId"))],
                    "contextSummary": signal.get("rawText") or item.get("reason") or "",
                    "strategicLink": None,
                    "linkedContexts": [],
                    "contributionTags": [],
                    "validationState": item.get("validationState"),
                    "orgContributionScore": delta,
                    "weekLabel": signal.get("weekLabel") or "",
                    "createdAt": item.get("updatedAt"),
                    "reversedAt": (
                        item.get("updatedAt")
                        if item.get("validationState") == "revoked"
                        else None
                    ),
                }
            )
        return entries

    def _growth_overview(
        self,
        identity: SessionIdentity,
        growth: Mapping[str, Any],
    ) -> dict[str, Any]:
        entries = self._growth_ledger_entries(identity, growth)
        total_xp = sum(int(item["totalXp"]) for item in entries)
        ability_entries: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            ability_entries[_text(entry.get("abilityKey"))].append(entry)
        abilities = [
            {
                "abilityKey": ability,
                "label": ability,
                "currentScore": sum(int(item["totalXp"]) for item in items),
                "previousScore": 0,
                "totalXp": sum(int(item["totalXp"]) for item in items),
                "weeklyXp": sum(int(item["totalXp"]) for item in items),
                "stage": "已有证据" if items else "待积累",
                "nextStage": "持续验证",
                "evidence": f"{len(items)} 条权威成长证据",
            }
            for ability, items in sorted(ability_entries.items())
        ]
        source_counts = Counter(
            _text(item.get("sourceType")) for item in growth.get("signals") or []
        )
        return {
            "userId": identity.membership_id,
            "userName": identity.display_name,
            "totalXp": total_xp,
            "weeklyXp": total_xp,
            "weeklyBaseXp": total_xp,
            "weeklyPremiumXp": 0,
            "level": total_xp,
            "stageLabel": "已形成成长证据" if total_xp else "待确认成长证据",
            "xpToNext": 1,
            "rank": {
                "key": "evidence",
                "name": "成长证据",
                "division": None,
                "fullLabel": f"{total_xp} 条已确认",
                "progress": total_xp,
                "nextName": "新增一条已确认成长证据",
                "xpToNext": 1,
            },
            "abilities": abilities,
            "recentEntries": entries[:20],
            "recommendations": growth.get("recommendations") or [],
            "sourceCoverage": {
                "taskSignals": source_counts.get("task", 0),
                "meetingSignals": source_counts.get("meeting", 0),
                "strategicSignals": source_counts.get("strategy", 0),
                "reviewSignals": source_counts.get("weekly_review", 0),
                "handbookSignals": source_counts.get("handbook", 0),
                "expWallSignals": source_counts.get("experience_quote", 0),
                "memorySignals": source_counts.get("memory", 0),
                "documentSignals": source_counts.get("document", 0),
                "clientCount": 0,
            },
            "projectGrowthHighlights": [],
            "eventLineGrowthHighlights": [],
            "strategicAlignmentHighlights": [],
            "pendingCaptures": growth.get("pendingCaptures") or [],
            "currentFocusActions": [],
            "abilityGaps": [],
            "updatedAt": growth.get("derivedAt"),
            "derivation": "confirmed growth_evidence count; no synthetic XP cache",
        }

    def _badge_board(
        self,
        growth: Mapping[str, Any],
    ) -> dict[str, Any]:
        categories = []
        total = 0
        lit = 0
        for badge in growth.get("badges") or []:
            count = int(badge.get("evidenceCount") or 0)
            state = "unlocked" if count else "locked"
            lit += int(bool(count))
            total += 1
            ability = _text(badge.get("abilityKey"))
            categories.append(
                {
                    "id": f"category:{ability}",
                    "label": ability,
                    "abilityKey": ability,
                    "abilityLabel": ability,
                    "litCount": int(bool(count)),
                    "totalCount": 1,
                    "badges": [
                        {
                            "id": badge.get("id"),
                            "code": badge.get("id"),
                            "name": badge.get("name"),
                            "categoryId": f"category:{ability}",
                            "categoryLabel": ability,
                            "abilityKey": ability,
                            "abilityLabel": ability,
                            "roles": [],
                            "xp": count,
                            "iconMotif": "evidence",
                            "description": f"{count} 条已确认权威证据",
                            "whyItMatters": "仅由已确认成长证据点亮",
                            "systemHowText": "growth_evidence confirmed count",
                            "state": state,
                            "progressValue": count,
                            "progressTarget": 1,
                            "progressPercent": min(count * 100, 100),
                            "progressText": f"{count}/1",
                            "nextActionText": "继续积累并确认成长证据",
                            "actionLinks": [],
                            "evidence": [],
                            "linkedContexts": [],
                            "missingSignals": [] if count else ["confirmed_evidence"],
                            "unlockedAt": None,
                            "masteryLevel": count,
                            "historical": False,
                        }
                    ],
                }
            )
        return {
            "overview": {
                "totalBadges": total,
                "litBadges": lit,
                "readyBadges": lit,
                "inProgressBadges": total - lit,
                "monthlyNewBadges": 0,
                "totalXp": sum(
                    int(item.get("evidenceCount") or 0)
                    for item in growth.get("badges") or []
                ),
                "upcomingBadgeIds": [
                    str(item.get("id"))
                    for item in growth.get("badges") or []
                    if not item.get("evidenceCount")
                ],
            },
            "categories": categories,
            "updatedAt": growth.get("derivedAt"),
        }

    def _growth_workbench(
        self,
        identity: SessionIdentity,
        growth: Mapping[str, Any],
    ) -> dict[str, Any]:
        snapshot = self._snapshot(identity)
        project_names = {
            _text(item.get("projectId")): _text(item.get("name"))
            for item in snapshot.get("projects") or []
        }
        tasks = [
            {
                "id": item.get("taskId"),
                "title": item.get("title"),
                "project": project_names.get(_text(item.get("projectId")), ""),
                "clientName": project_names.get(_text(item.get("projectId"))) or None,
                "eventLineName": None,
                "deadline": item.get("dueDate") or item.get("deadlineAt") or "",
                "urgency": item.get("priority") or "normal",
                "urgencyColor": "",
                "phase": item.get("lifecycleState") or "",
                "risks": [],
                "nextAdvice": "",
                "robotReady": False,
                "robotReasons": ["需要用户确认后执行"],
                "recommendationId": None,
                "linkedTaskId": item.get("taskId"),
                "linkedContexts": [],
                "xpReward": 0,
                "contextSummary": item.get("description") or "",
                "projectModuleName": None,
                "projectFlowName": None,
                "projectStage": None,
                "businessCategory": None,
                "sourceEvidence": [],
                "currentBlocker": None,
                "missingSignals": [],
                "hasBackground": bool(item.get("description")),
                "hasDeadline": bool(item.get("dueDate") or item.get("deadlineAt")),
                "isCrossDepartment": False,
                "needsReview": False,
                "evidenceCount": 0,
                "pendingCollaborations": sum(
                    collaborator.get("inboxState") == "pending"
                    for collaborator in item.get("collaborators") or []
                ),
                "taskIntent": {},
                "universalSkills": [],
                "projectContextPack": {},
                "actionPlan": [],
                "materialRefs": [],
            }
            for item in snapshot.get("tasks") or []
            if item.get("lifecycleState") not in {"completed", "archived", "cancelled"}
        ]
        lessons = [
            {
                "id": item.get("id"),
                "title": item.get("category") or "经验",
                "judgment": item.get("text") or "",
                "applicableScene": item.get("sourceType") or "",
                "whyItWorks": item.get("summary") or "",
                "reuseHint": "复用前请结合当前项目事实核验",
                "linkedContext": None,
            }
            for item in growth.get("experienceWall") or []
        ]
        return {
            "tasks": tasks,
            "activeTaskId": tasks[0]["id"] if tasks else None,
            "learningSummary": {
                "headline": (
                    f"{len(growth.get('evidence') or [])} 条成长证据可复盘"
                ),
                "whyItMatters": "仅基于严格成长事实与当前任务生成",
                "immediateMove": "确认候选证据并选择下一步任务",
                "generator": "rules",
                "confidence": "high" if growth.get("evidence") else "low",
            },
            "genericLessons": lessons,
            "projectGuidance": [],
            "reasoningTrace": {
                "mode": "rules_only",
                "usedInputs": [],
                "evidenceRefs": [
                    str(item.get("growthEvidenceId"))
                    for item in growth.get("evidence") or []
                ],
                "missingContext": [],
                "aiContribution": [],
                "modelLabel": None,
                "confidence": "high" if growth.get("evidence") else "low",
            },
            "robotAssist": {
                "ready": False,
                "canDelegate": [],
                "mustStayHuman": ["成长确认与任务执行"],
                "why": ["严格新版未伪造自动执行结果"],
            },
            "afterActionCapture": {
                "title": "行动后确认成长事实",
                "summary": "保存前核对来源、能力与证据状态",
                "experienceType": "growth_evidence",
                "recommendedWriteback": "growth_signals/growth_evidence",
            },
            "processSteps": [],
            "activeProcessId": None,
            "actionsBefore": [],
            "actionsDuring": [],
            "actionsAfter": [],
            "supportMaterials": [],
            "checklistItems": [],
            "supportCopy": {
                "title": "成长工作台",
                "intro": "以下内容由严格权威事实现场计算",
                "bullets": [],
            },
            "robotPlan": [],
            "sourceMode": "task" if tasks else "growth_seed" if lessons else "empty",
            "scopeMode": "global",
            "scopeClientId": None,
            "scopeClientName": None,
            "updatedAt": growth.get("derivedAt"),
        }

    def _sentiment_view(
        self,
        identity: SessionIdentity,
        *,
        project_id: str = "",
    ) -> dict[str, Any]:
        facts = [
            item
            for item in self._intelligence_facts(identity)
            if item["recordKind"] not in INTERNAL_CONFIGURATION_KINDS
            and (
                (item.get("sourcePayload") or {}).get("contentKind")
                == "public_opinion"
                or "sentiment" in _text(item.get("recordKind")).lower()
            )
            and (
                not project_id
                or _text(item.get("projectId")) == project_id
            )
        ]
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in facts:
            payload = item.get("sourcePayload") or {}
            sentiment = _text(payload.get("sentiment")).lower()
            if sentiment not in {"positive", "neutral", "negative"}:
                sentiment = (
                    "positive"
                    if item["status"] == "accepted"
                    else "negative"
                    if item["status"] == "returned"
                    else "neutral"
                )
            buckets[sentiment].append(item)
        themes = []
        by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in facts:
            by_kind[item["recordKind"]].append(item)
        for kind, items in sorted(by_kind.items()):
            themes.append(
                {
                    "id": f"kind:{kind}",
                    "themeId": f"kind:{kind}",
                    "name": kind,
                    "itemCount": len(items),
                    "positiveCount": sum(item in buckets["positive"] for item in items),
                    "negativeCount": sum(item in buckets["negative"] for item in items),
                    "updatedAt": max(
                        (_text(item.get("updatedAt")) for item in items),
                        default=None,
                    ),
                    "derived": True,
                }
            )
        return {
            "items": facts,
            "themes": themes,
            "profile": {
                "total": len(facts),
                "positive": len(buckets["positive"]),
                "neutral": len(buckets["neutral"]),
                "negative": len(buckets["negative"]),
                "positiveRate": (
                    round(len(buckets["positive"]) / len(facts), 4) if facts else 0
                ),
                "negativeRate": (
                    round(len(buckets["negative"]) / len(facts), 4) if facts else 0
                ),
            },
            "derivedAt": utc_now(),
            "authoritySource": "intelligence_records/source_payload_json",
        }

    def _brand_audit_view(
        self,
        identity: SessionIdentity,
        *,
        scope_type: str,
        scope_id: str,
    ) -> dict[str, Any]:
        sentiment = self._sentiment_view(
            identity,
            project_id=scope_id,
        )
        facts = sentiment["items"]
        if not facts:
            return {
                "audit": None,
                "recomputeNote": (
                    "too_few_items: 当前项目尚无公开舆情情报事实，"
                    "请先完成真实公开采集"
                ),
            }

        theme_facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in facts:
            theme_facts[_text(item.get("recordKind"))].append(item)
        themes = [
            {
                "id": f"kind:{kind}",
                "label": kind,
                "items": items,
                "positive": sum(
                    _text((item.get("sourcePayload") or {}).get("sentiment"))
                    == "positive"
                    or (
                        not _text(
                            (item.get("sourcePayload") or {}).get("sentiment")
                        )
                        and item.get("status") == "accepted"
                    )
                    for item in items
                ),
                "negative": sum(
                    _text((item.get("sourcePayload") or {}).get("sentiment"))
                    == "negative"
                    or (
                        not _text(
                            (item.get("sourcePayload") or {}).get("sentiment")
                        )
                        and item.get("status") == "returned"
                    )
                    for item in items
                ),
            }
            for kind, items in sorted(theme_facts.items())
        ]
        positive_themes = [
            item for item in themes if item["positive"] > item["negative"]
        ]
        negative_themes = [
            item for item in themes if item["negative"] > item["positive"]
        ]
        profile = sentiment["profile"]
        dominant = max(
            themes,
            key=lambda item: (len(item["items"]), item["label"]),
        )
        scope_label = "项目" if scope_type == "client" else "业务线"
        headline = (
            f"{scope_label}公开印象主要集中在“{dominant['label']}”"
        )
        narrative = (
            f"已核验 {profile['total']} 条公开情报事实："
            f"正向 {profile['positive']} 条、中性 {profile['neutral']} 条、"
            f"负向 {profile['negative']} 条。"
            f"当前最集中主题为“{dominant['label']}”，"
            "本速读只归纳严格权威摘要，不补造网页正文或模型判断。"
        )
        tensions = [
            {
                "statement": f"主题“{item['label']}”存在负向公开信号",
                "selfAnchor": "待结合已确认项目战略进一步核验",
                "publicAnchor": _text(item["items"][0].get("summary")),
            }
            for item in negative_themes[:3]
        ]
        recommendations = [
            {
                "action": f"核验并回应“{item['label']}”相关公开信号",
                "rationale": (
                    f"该主题包含 {item['negative']} 条负向权威情报事实"
                ),
                "priority": "high" if item["negative"] >= 2 else "medium",
            }
            for item in negative_themes[:3]
        ]
        audit_id = (
            "derived:"
            + sha256_text(
                canonical_json(
                    {
                        "organizationId": identity.organization_id,
                        "scopeType": scope_type,
                        "scopeId": scope_id,
                        "intelligenceIds": sorted(
                            _text(item.get("id")) for item in facts
                        ),
                    }
                )
            )[:24]
        )
        return {
            "audit": {
                "id": audit_id,
                "scopeType": scope_type,
                "scopeId": scope_id,
                "headline": headline,
                "narrativeMd": narrative,
                "tensions": tensions,
                "recommendations": recommendations,
                "contentAngles": {
                    "amplify": [
                        item["label"] for item in positive_themes[:5]
                    ],
                    "new": [
                        item["label"] for item in negative_themes[:5]
                    ],
                },
                "evidenceThemeIds": [item["id"] for item in themes],
                "computedAt": sentiment["derivedAt"],
                "expiresAt": _expiry(),
            },
            "recomputeNote": (
                "由 intelligence_records/source_payload_json 现场确定性提炼"
            ),
        }

    def _strategy_view(self, identity: SessionIdentity) -> dict[str, Any]:
        snapshot = self._snapshot(identity)
        facts = self._intelligence_facts(identity)
        plans = snapshot.get("plans") or []
        reports = [
            item
            for item in snapshot.get("reports") or []
            if item.get("outputKind") == "strategy_report"
        ]
        thoughts = [
            {
                "id": item["id"],
                "title": item["title"],
                "summary": item["summary"],
                "state": item["status"],
                "projectId": item["projectId"],
                "version": item["version"],
                "sourceType": item["recordKind"],
                "updatedAt": item["updatedAt"],
            }
            for item in facts
            if item["recordKind"] in STRATEGIC_KINDS
        ]
        for plan in plans:
            for plan_item in plan.get("items") or []:
                thoughts.append(
                    {
                        "id": f"plan-item:{plan_item.get('planItemId')}",
                        "title": plan_item.get("title"),
                        "summary": plan_item.get("statement") or "",
                        "state": plan_item.get("status"),
                        "projectId": None,
                        "version": plan_item.get("version"),
                        "sourceType": "organization_plan_item",
                        "updatedAt": plan_item.get("updatedAt"),
                        "derived": True,
                    }
                )
        accepted = [item for item in facts if item["status"] == "accepted"]
        return {
            "thoughts": thoughts,
            "brandMirror": {
                "projectCount": len(snapshot.get("projects") or []),
                "planCount": len(plans),
                "acceptedIntelligenceCount": len(accepted),
                "activeStrategyReportCount": sum(
                    item.get("lifecycleState") in {"active", "draft"}
                    for item in reports
                ),
                "strengths": [
                    item["title"] for item in accepted[:5] if item.get("title")
                ],
                "gaps": [
                    item["title"]
                    for item in facts
                    if item["status"] in {"candidate", "returned"}
                ][:5],
                "derivedAt": utc_now(),
            },
            "strategyExtract": {
                "reports": reports,
                "planStatements": [
                    {
                        "planId": plan.get("planId"),
                        "periodLabel": plan.get("periodLabel"),
                        "summary": plan.get("summary"),
                        "items": plan.get("items") or [],
                    }
                    for plan in plans
                ],
                "evidence": [
                    {
                        "intelligenceId": item["id"],
                        "title": item["title"],
                        "summary": item["summary"],
                    }
                    for item in accepted
                ],
                "derivedAt": utc_now(),
            },
            "authoritySource": (
                "organization_plans/organization_plan_items/"
                "intelligence_records/narrative_outputs"
            ),
        }

    def _data_center_view(self, identity: SessionIdentity) -> dict[str, Any]:
        operations = self._operations(identity)
        snapshot = self._snapshot(identity)
        with self.repository._connection() as connection:
            counts = {
                "sourceAssets": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM source_assets WHERE organization_id = ?",
                        (identity.organization_id,),
                    ).fetchone()[0]
                ),
                "knowledgeDocuments": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM knowledge_documents WHERE organization_id = ?",
                        (identity.organization_id,),
                    ).fetchone()[0]
                ),
                "evidenceLinks": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM evidence_links WHERE organization_id = ?",
                        (identity.organization_id,),
                    ).fetchone()[0]
                ),
                "storageObjects": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM storage_objects
                        WHERE scope_id = ?
                        """,
                        (identity.scope_id,),
                    ).fetchone()[0]
                ),
            }
            schema_row = connection.execute(
                """
                SELECT build_id, schema_family, contract_version,
                       manifest_hash, database_generation_id, created_at
                FROM meta_schema_builds
                ORDER BY created_at DESC, build_id DESC
                LIMIT 1
                """
            ).fetchone()
            table_names = [
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                ).fetchall()
            ]
            release_rows = connection.execute(
                """
                SELECT * FROM release_gates
                ORDER BY decided_at DESC, gate_id
                """
            ).fetchall()
            recovery_rows = connection.execute(
                """
                SELECT * FROM recovery_sets
                ORDER BY created_at DESC, recovery_set_id
                """
            ).fetchall()
        outbox_status = Counter(_text(item.get("status")) for item in operations["outbox"])
        command_status = Counter(
            _text(item.get("status")) for item in operations["commands"]
        )
        return {
            "generatedAt": utc_now(),
            "schema": dict(schema_row) if schema_row is not None else None,
            "tables": table_names,
            "counts": {
                **counts,
                "projects": len(snapshot.get("projects") or []),
                "tasks": len(snapshot.get("tasks") or []),
                "intelligence": len(snapshot.get("intelligence") or []),
                "growthSignals": len(snapshot.get("growthSignals") or []),
                "growthEvidence": len(snapshot.get("growthEvidence") or []),
            },
            "commandStatus": dict(command_status),
            "outboxStatus": dict(outbox_status),
            "deadLetterCount": sum(
                item.get("status") == "open" for item in operations["deadLetters"]
            ),
            "commands": operations["commands"],
            "outbox": operations["outbox"],
            "deadLetters": operations["deadLetters"],
            "reconciliationRuns": operations["reconciliationRuns"],
            "bulkOperations": operations["bulkOperations"],
            "releaseGates": [dict(row) for row in release_rows],
            "recoverySets": [dict(row) for row in recovery_rows],
            "authoritySource": (
                "meta_schema_builds/command_envelopes/operation_attempts/"
                "delivery_outbox/operation_dead_letters/reconciliation_runs/"
                "release_gates/recovery_sets"
            ),
        }

    # ---------- public queries ----------

    def _derived_brand_strategy_extract(
        self,
        identity: SessionIdentity,
        *,
        client_id: str,
    ) -> dict[str, Any]:
        strategy = self._strategy_view(identity)["strategyExtract"]
        plans = strategy["planStatements"]
        first_plan = plans[0] if plans else {}
        return {
            "clientId": client_id,
            "strategicObjective": first_plan.get("summary") or "",
            "strategicObjectiveSources": [
                f"plan:{item.get('planId')}" for item in plans
            ],
            "methodology": "\n".join(
                _text(item.get("summary")) for item in plans if item.get("summary")
            ),
            "methodologySources": [
                f"plan:{item.get('planId')}" for item in plans
            ],
            "stakeholders": [],
            "sourceStrategyMdHash": sha256_text(canonical_json(plans)),
            "sourceMethodologyMdHash": sha256_text(
                canonical_json(strategy["evidence"])
            ),
            "llmModel": "deterministic-authority-view",
            "error": None,
            "extractedAt": strategy["derivedAt"],
            "confirmedBy": None,
            "confirmedAt": None,
            "isStale": False,
        }

    def _brand_strategy_extract(
        self,
        identity: SessionIdentity,
        *,
        client_id: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            rows = connection.execute(
                """
                SELECT n.version AS authority_version, v.content_json
                FROM narrative_outputs AS n
                JOIN narrative_output_versions AS v
                  ON v.narrative_output_id = n.narrative_output_id
                 AND v.version = n.latest_version
                WHERE n.organization_id = ?
                  AND n.output_kind = 'strategy_report'
                  AND n.lifecycle_state != 'archived'
                ORDER BY n.updated_at DESC, n.narrative_output_id
                """,
                (identity.organization_id,),
            ).fetchall()
        for row in rows:
            content = _json(row["content_json"], {})
            if not isinstance(content, dict):
                continue
            if _text(content.get("clientId")) != client_id:
                continue
            return {
                **self._derived_brand_strategy_extract(
                    identity,
                    client_id=client_id,
                ),
                **content,
                "isStale": False,
            }
        return self._derived_brand_strategy_extract(
            identity,
            client_id=client_id,
        )

    def _strategic_thoughts_view(
        self,
        identity: SessionIdentity,
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        client_id = _text(query.get("clientId"))
        project_module_id = _text(query.get("projectModuleId"))
        include_dismissed = _text(query.get("includeDismissed")).lower() in {
            "1",
            "true",
            "yes",
        }
        include_deleted = _text(query.get("includeDeleted")).lower() in {
            "1",
            "true",
            "yes",
        }
        try:
            limit = max(min(int(query.get("limit") or 50), 200), 1)
        except (TypeError, ValueError):
            limit = 50
        thoughts = self._strategy_view(identity)["thoughts"]
        if client_id:
            thoughts = [
                item
                for item in thoughts
                if _text(item.get("projectId")) == client_id
            ]
        elif project_module_id:
            # Strict v4 has no project-module authority for strategic thoughts.
            thoughts = []
        items = [
            {
                "id": item["id"],
                "scope": "client" if item.get("projectId") else "system",
                "clientId": item.get("projectId"),
                "clientName": "",
                "projectModuleId": None,
                "projectModuleName": None,
                "line": item.get("title") or "",
                "observation": item.get("summary") or "",
                "suggestion": "",
                "confidence": None,
                "confidenceLevel": "none",
                "status": (
                    "confirmed"
                    if item.get("state") in {"accepted", "completed"}
                    else "dismissed"
                    if item.get("state")
                    in {"returned", "archived", "cancelled"}
                    else "draft"
                ),
                "isSystem": not bool(item.get("projectId")),
                "dueDateHint": "",
                "tags": [],
                "sources": [
                    {
                        "sourceType": (
                            "strategic_cockpit"
                            if item.get("sourceType")
                            in {"strategic_thought", "strategy_observation"}
                            else "system"
                        ),
                        "sourceId": item["id"],
                        "label": item.get("sourceType") or "authority_fact",
                        "detail": item.get("summary") or "",
                    }
                ],
                "evidenceCount": 1,
                "generatedAt": item.get("updatedAt") or utc_now(),
                "isFavorite": False,
                "isDeleted": item.get("state") == "archived",
                "review": None,
                "version": item.get("version"),
            }
            for item in thoughts
        ]
        if not include_deleted:
            items = [item for item in items if not item["isDeleted"]]
        if not include_dismissed:
            items = [item for item in items if item["status"] != "dismissed"]
        total = len(items)
        items = items[:limit]
        return {
            "items": items,
            "total": total,
            "generatedAt": utc_now(),
            "selectedClientId": client_id or None,
            "selectedProjectModuleId": project_module_id or None,
            "usingMockData": False,
        }

    def query(
        self,
        identity: SessionIdentity,
        *,
        resource_path: str,
        query: Mapping[str, str],
    ) -> Any:
        if resource_path == "consultation/knowledge-requests":
            return self._consultation_requests(identity, query)
        if resource_path == "topics":
            return self._topic_view(identity)
        if resource_path == "intelligence/items":
            page = max(int(query.get("page") or 1), 1)
            page_size = max(min(int(query.get("pageSize") or 50), 200), 1)
            facts = [
                item
                for item in self._intelligence_facts(identity)
                if item["recordKind"] not in INTERNAL_CONFIGURATION_KINDS
                and item["recordKind"] not in {"topic_radar", "profile_run"}
            ]
            content_kind = _text(query.get("contentKind"))
            if content_kind:
                facts = [
                    item
                    for item in facts
                    if self._intelligence_item(item)["contentKind"]
                    == content_kind
                ]
            work_object_id = _text(
                query.get("workObjectId")
                or query.get("scopeId")
            )
            if work_object_id:
                facts = [
                    item
                    for item in facts
                    if _text(item.get("projectId")) == work_object_id
                ]
            sort = _text(query.get("sort"))
            if sort in {"published_asc", "captured_asc"}:
                facts.reverse()
            items = [self._intelligence_item(item) for item in facts]
            start = (page - 1) * page_size
            return {
                "items": items[start : start + page_size],
                "candidateSamples": [],
                "total": len(items),
                "page": page,
                "pageSize": page_size,
            }
        if resource_path == "intelligence/work-objects":
            snapshot = self._snapshot(identity)
            counts = Counter(
                _text(item.get("projectId"))
                for item in snapshot.get("intelligence") or []
            )
            return [
                {
                    "type": "client",
                    "id": item.get("projectId"),
                    "clientId": item.get("projectId"),
                    "projectModuleId": None,
                    "name": item.get("name") or "",
                    "subtitle": item.get("summary") or item.get("domain") or "",
                    "color": item.get("color") or "#5B7BFE",
                    "updatedAt": item.get("updatedAt"),
                    "searchIntentStatus": "ready" if counts[item.get("projectId")] else "missing",
                    "searchIntentHint": None,
                    "sourceCoverageStatus": "ready" if counts[item.get("projectId")] else "missing",
                    "candidateRefreshStatus": "ready" if counts[item.get("projectId")] else "missing",
                    "candidateRefreshHint": None,
                    "lastCandidateFetchAt": max(
                        (
                            _text(record.get("updatedAt"))
                            for record in snapshot.get("intelligence") or []
                            if record.get("projectId") == item.get("projectId")
                        ),
                        default=None,
                    ),
                    "candidateCounts": {
                        "total": counts[item.get("projectId")],
                    },
                }
                for item in snapshot.get("projects") or []
            ]
        if resource_path == "intelligence/source-diagnostics":
            facts = self._intelligence_facts(identity)
            scope_type = query.get("scopeType") or "client"
            scope_id = query.get("scopeId") or ""
            if scope_id:
                facts = [
                    item
                    for item in facts
                    if _text(item.get("projectId")) == scope_id
                ]
            by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for item in facts:
                by_source[
                    _text((item.get("sourcePayload") or {}).get("sourceName"))
                    or _text(item.get("recordKind"))
                ].append(item)
            candidate_counts = Counter(_text(item.get("status")) for item in facts)
            return {
                "scopeType": scope_type,
                "scopeId": scope_id,
                "contentKind": query.get("contentKind"),
                "sourceCoverageStatus": "ready" if facts else "missing",
                "candidateRefreshStatus": "ready" if facts else "missing",
                "candidateRefreshHint": None if facts else "尚无权威情报事实",
                "lastCandidateFetchAt": max(
                    (_text(item.get("updatedAt")) for item in facts),
                    default=None,
                ),
                "candidateCounts": dict(candidate_counts),
                "officialSiteDiscoveredCount": sum(
                    bool(item.get("sourceUrl")) for item in facts
                ),
                "coverageGaps": (
                    ["missing_source_url"]
                    if any(not item.get("sourceUrl") for item in facts)
                    else []
                ),
                "sources": [
                    {
                        "id": f"derived:{sha256_text(source)[:12]}",
                        "sourceType": items[0].get("recordKind"),
                        "sourceName": source,
                        "sourceUrlTemplate": items[0].get("sourceUrl") or "",
                        "contentKinds": sorted(
                            {
                                self._intelligence_item(item)["contentKind"]
                                for item in items
                            }
                        ),
                        "region": "",
                        "reliabilityTier": "authority_fact",
                        "priority": len(items),
                        "enabled": True,
                        "discoverySource": "intelligence_records",
                        "discoveryReason": "权威情报事实中已有来源",
                        "discoverySamples": [],
                        "healthScore": round(
                            100
                            * sum(item["status"] == "accepted" for item in items)
                            / len(items)
                        ),
                        "successCount": sum(
                            item["status"] == "accepted" for item in items
                        ),
                        "failureCount": sum(
                            item["status"] == "returned" for item in items
                        ),
                        "candidateCount": len(items),
                        "promotedCount": sum(
                            item["status"] == "accepted" for item in items
                        ),
                        "duplicateCount": 0,
                        "lastStatus": items[0]["status"],
                        "lastCheckedAt": items[0]["updatedAt"],
                        "lastSuccessAt": next(
                            (
                                item["updatedAt"]
                                for item in items
                                if item["status"] == "accepted"
                            ),
                            None,
                        ),
                        "lastFailureAt": next(
                            (
                                item["updatedAt"]
                                for item in items
                                if item["status"] == "returned"
                            ),
                            None,
                        ),
                        "nextDueAt": None,
                    }
                    for source, items in sorted(by_source.items())
                ],
                "recentFetchJobs": [],
                "officialSiteDiscoverySamples": [],
            }
        if resource_path == "intelligence/focus-directives":
            return [
                {
                    "id": item["id"],
                    "scopeType": (item.get("sourcePayload") or {}).get("scopeType")
                    or "global",
                    "scopeId": (item.get("sourcePayload") or {}).get("scopeId"),
                    "profileCompletionFocus": (
                        item.get("sourcePayload") or {}
                    ).get("profileCompletionFocus")
                    or [],
                    "timelyIntelligenceFocus": (
                        item.get("sourcePayload") or {}
                    ).get("timelyIntelligenceFocus")
                    or [],
                    "exclude": (item.get("sourcePayload") or {}).get("exclude") or [],
                    "createdAt": item["createdAt"],
                    "updatedAt": item["updatedAt"],
                    "version": item["version"],
                }
                for item in self._intelligence_facts(identity)
                if item["recordKind"] == "focus_directive"
                and item["status"] != "archived"
            ]
        if resource_path == "intelligence/verification-rules":
            return [
                {
                    "id": item["id"],
                    "scopeType": (item.get("sourcePayload") or {}).get("scopeType")
                    or "global",
                    "scopeId": (item.get("sourcePayload") or {}).get("scopeId"),
                    "positiveRules": (item.get("sourcePayload") or {}).get(
                        "positiveRules"
                    )
                    or [],
                    "excludeRules": (item.get("sourcePayload") or {}).get(
                        "excludeRules"
                    )
                    or [],
                    "identityAnchors": (item.get("sourcePayload") or {}).get(
                        "identityAnchors"
                    )
                    or [],
                    "clarificationExamples": (
                        item.get("sourcePayload") or {}
                    ).get("clarificationExamples")
                    or [],
                    "createdAt": item["createdAt"],
                    "updatedAt": item["updatedAt"],
                    "version": item["version"],
                }
                for item in self._intelligence_facts(identity)
                if item["recordKind"] == "verification_rule"
                and item["status"] != "archived"
            ]
        if resource_path == "intelligence/refresh-cycle-settings":
            items = [
                item
                for item in self._intelligence_facts(identity)
                if item["recordKind"] == "refresh_cycle_setting"
                and item["status"] != "archived"
            ]
            payload = (items[0].get("sourcePayload") or {}) if items else {}
            return {
                "profileCompletionHours": int(
                    payload.get("profileCompletionHours") or 0
                ),
                "timelyIntelligenceHours": int(
                    payload.get("timelyIntelligenceHours") or 0
                ),
                "version": items[0].get("version") if items else None,
            }
        if resource_path == "intelligence/refresh-runs":
            with self.repository._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM audit_events
                    WHERE organization_id = ?
                      AND action IN (
                        'intelligence.refresh',
                        'intelligence.profile.refresh',
                        'intelligence.profile.run_due'
                      )
                    ORDER BY created_at DESC, audit_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            return [
                {
                    "id": row["audit_id"],
                    "scopeType": "",
                    "scopeId": None,
                    "clientId": None,
                    "projectModuleId": None,
                    "contentKind": "timely_intelligence",
                    "triggerSource": row["action"],
                    "status": "completed",
                    "stage": "derived_view_recompute",
                    "message": "严格权威事实派生视图已重算",
                    "result": _json(row["summary_json"], {}),
                    "rejectionSummary": {},
                    "createdAt": row["created_at"],
                    "updatedAt": row["created_at"],
                    "startedAt": row["created_at"],
                    "finishedAt": row["created_at"],
                }
                for row in rows
            ]
        if resource_path.startswith("intelligence/sentiment/"):
            sentiment = self._sentiment_view(
                identity,
                project_id=_text(
                    query.get("clientId")
                    or query.get("projectModuleId")
                    or query.get("scopeId")
                ),
            )
            sentiment_items = [
                {
                    "id": item["id"],
                    "title": item["title"],
                    "summary": item["summary"],
                    "source": (item.get("sourcePayload") or {}).get("sourceName")
                    or item["recordKind"],
                    "sourceUrl": item["sourceUrl"] or "",
                    "capturedAt": item["createdAt"],
                    "sentimentLabel": (
                        (item.get("sourcePayload") or {}).get("sentiment")
                        if (item.get("sourcePayload") or {}).get("sentiment")
                        in {"positive", "neutral", "negative"}
                        else "positive"
                        if item["status"] == "accepted"
                        else "negative"
                        if item["status"] == "returned"
                        else "neutral"
                    ),
                    "sentimentReason": (
                        item.get("sourcePayload") or {}
                    ).get("sentimentReason")
                    or "",
                    "tags": (item.get("sourcePayload") or {}).get("tags") or [],
                    "userStatus": self._intelligence_item(item)["userStatus"],
                }
                for item in sentiment["items"]
            ]
            theme_items_by_id = {
                f"kind:{kind}": [
                    item
                    for item in sentiment_items
                    if next(
                        (
                            fact["recordKind"]
                            for fact in sentiment["items"]
                            if fact["id"] == item["id"]
                        ),
                        "",
                    )
                    == kind
                ]
                for kind in {
                    item["recordKind"] for item in sentiment["items"]
                }
            }
            themes = [
                {
                    "id": theme["id"],
                    "themeLabel": theme["name"],
                    "themeSummary": (
                        f"{theme['itemCount']} 条同类权威情报事实"
                    ),
                    "sentimentTone": (
                        "negative"
                        if theme["negativeCount"] > theme["positiveCount"]
                        else "positive"
                        if theme["positiveCount"] > theme["negativeCount"]
                        else "neutral"
                    ),
                    "itemCount": theme["itemCount"],
                    "representativeQuote": (
                        theme_items_by_id.get(theme["id"], [{}])[0].get("summary", "")
                        if theme_items_by_id.get(theme["id"])
                        else ""
                    ),
                    "representativeItemId": (
                        theme_items_by_id.get(theme["id"], [{}])[0].get("id")
                        if theme_items_by_id.get(theme["id"])
                        else None
                    ),
                    "itemIds": [
                        item["id"] for item in theme_items_by_id.get(theme["id"], [])
                    ],
                    "computedAt": theme.get("updatedAt") or sentiment["derivedAt"],
                    "expiresAt": theme.get("updatedAt") or sentiment["derivedAt"],
                }
                for theme in sentiment["themes"]
            ]
            if resource_path == "intelligence/sentiment/items":
                return {"items": sentiment_items, "total": len(sentiment_items)}
            if resource_path == "intelligence/sentiment/profile":
                profile = sentiment["profile"]
                source_counts = Counter(item["source"] for item in sentiment_items)
                negative_sources = Counter(
                    item["source"]
                    for item in sentiment_items
                    if item["sentimentLabel"] == "negative"
                )
                return {
                    "withinDays": int(query.get("withinDays") or 30),
                    "totalMentions": profile["total"],
                    "sentimentScore": (
                        round(
                            100
                            * (profile["positive"] - profile["negative"])
                            / profile["total"]
                        )
                        if profile["total"]
                        else 0
                    ),
                    "negativeCount": profile["negative"],
                    "neutralCount": profile["neutral"],
                    "positiveCount": profile["positive"],
                    "topNegativeSources": [
                        {"source": source, "count": count}
                        for source, count in negative_sources.most_common(5)
                    ],
                    "topSources": [
                        {"source": source, "count": count}
                        for source, count in source_counts.most_common(5)
                    ],
                }
            if resource_path == "intelligence/sentiment/themes":
                return {
                    "themes": themes,
                    "total": len(themes),
                    "recomputeNote": "由 intelligence_records 现场分组",
                }
            theme_id = _path_id(
                resource_path,
                "intelligence/sentiment/themes",
                "/items",
            )
            if theme_id is not None:
                kind = theme_id.removeprefix("kind:")
                return {
                    "ok": True,
                    "theme": next(
                        (item for item in themes if item["id"] == theme_id),
                        {
                            "id": theme_id,
                            "themeLabel": kind,
                            "themeSummary": "",
                            "sentimentTone": "neutral",
                            "itemCount": 0,
                            "representativeQuote": "",
                            "representativeItemId": None,
                            "itemIds": [],
                            "computedAt": sentiment["derivedAt"],
                            "expiresAt": sentiment["derivedAt"],
                        },
                    ),
                    "items": theme_items_by_id.get(theme_id, []),
                }
            if resource_path == "intelligence/sentiment/gap":
                profile = sentiment["profile"]
                return {
                    "ok": True,
                    "reason": "由情报事实主题与接受/退回状态现场比较",
                    "propositions": [],
                    "themes": themes,
                    "alignments": [],
                    "unexpectedThemes": [
                        {"id": item["id"], "label": item["themeLabel"]}
                        for item in themes
                        if item["sentimentTone"] == "negative"
                    ],
                }
            if resource_path == "intelligence/sentiment/audit":
                scope_type = (
                    "client" if _text(query.get("clientId")) else "project_module"
                )
                scope_id = _text(
                    query.get("clientId") or query.get("projectModuleId")
                )
                return self._brand_audit_view(
                    identity,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
        if resource_path == "intelligence/brand-mirror/analyze":
            strategy = self._strategy_view(identity)
            mirror = strategy["brandMirror"]
            facts = [
                item
                for item in self._intelligence_facts(identity)
                if item["recordKind"] not in INTERNAL_CONFIGURATION_KINDS
            ]
            accepted = [item for item in facts if item["status"] == "accepted"]
            return {
                "id": f"derived:{identity.organization_id}",
                "corpusDocCount": len(facts),
                "corpusCharCount": sum(
                    len(_text(item.get("summary"))) for item in facts
                ),
                "websiteAuditId": None,
                "selfPresentation": [
                    {
                        "label": title,
                        "score": round(
                            100
                            * sum(item["title"] == title for item in accepted)
                            / max(len(accepted), 1)
                        ),
                        "rationale": "来自已接受情报事实",
                    }
                    for title in mirror["strengths"]
                ],
                "blindspots": [
                    {
                        "label": title,
                        "rationale": "存在候选或退回情报，仍需核验",
                    }
                    for title in mirror["gaps"]
                ],
                "consistency": (
                    "已有计划与接受情报可交叉核验"
                    if mirror["planCount"] and accepted
                    else "权威事实不足，暂不下结论"
                ),
                "mediaCoverage": [
                    {
                        "source": (item.get("sourcePayload") or {}).get("sourceName")
                        or item["recordKind"],
                        "tone": (
                            "positive"
                            if item["status"] == "accepted"
                            else "negative"
                            if item["status"] == "returned"
                            else "neutral"
                        ),
                        "summary": item["summary"],
                    }
                    for item in facts[:20]
                ],
                "partners": [],
                "wordCloud": [],
                "llmModel": "deterministic-authority-view",
                "error": None,
                "createdAt": mirror["derivedAt"],
            }
        if resource_path == "intelligence/brand-mirror/strategy-extract":
            client_id = _text(query.get("clientId"))
            self._require_visible_project(identity, client_id)
            return {
                "extract": self._brand_strategy_extract(
                    identity,
                    client_id=client_id,
                )
            }
        if resource_path == "growth/overview":
            growth = self._growth_view(identity)
            return self._growth_overview(identity, growth)
        if resource_path == "growth/workbench":
            growth = self._growth_view(identity)
            return self._growth_workbench(identity, growth)
        if resource_path == "growth/experience-wall":
            return {
                "items": self._growth_view(identity)["experienceWall"],
                "refreshedFromCloud": True,
                "cloudSyncError": None,
            }
        if resource_path == "growth/badges":
            return self._badge_board(self._growth_view(identity))
        if resource_path == "growth/ledger":
            growth = self._growth_view(identity)
            return {"entries": self._growth_ledger_entries(identity, growth)}
        if resource_path == "proposals":
            proposals = self._proposals(identity)
            status = query.get("status")
            if status:
                proposals = [item for item in proposals if item["status"] == status]
            return proposals
        proposal_id = _path_id(resource_path, "proposals")
        if proposal_id is not None:
            proposal = next(
                (item for item in self._proposals(identity) if item["id"] == proposal_id),
                None,
            )
            if proposal is None:
                raise RepositoryError(404, "proposal_not_found", "提案不存在或不可见")
            return proposal
        proposal_id = _path_id(resource_path, "proposals", "/execution-preview")
        if proposal_id is not None:
            proposal = next(
                (item for item in self._proposals(identity) if item["id"] == proposal_id),
                None,
            )
            if proposal is None:
                raise RepositoryError(404, "proposal_not_found", "提案不存在或不可见")
            return {
                "proposalId": proposal_id,
                "executionType": "proposal_tasks",
                "riskLevel": proposal["riskLevel"],
                "willCreateTask": bool(proposal["taskDrafts"]),
                "willCreatePrepArtifact": False,
                "willCreateEvidenceRequest": False,
                "willUpdateEventLine": False,
                "summary": (
                    f"将按严格 bulk/CAS 处理 {len(proposal['taskDrafts'])} 个任务草案"
                ),
                "warnings": (
                    []
                    if proposal["status"] == "approved"
                    else ["提案尚未批准，不能进入执行队列"]
                ),
            }
        if resource_path == "approvals":
            pending = [
                item
                for item in self._proposals(identity)
                if item["authorityStatus"] in {"candidate", "inbox"}
            ]
            return [
                {
                    "id": item["id"],
                    "client_id": item["clientId"] or None,
                    "action_type": "proposal_review",
                    "actor_type": "membership",
                    "actor_id": item["createdBy"],
                    "target_resource": "proposal_record",
                    "payload": item["payload"],
                    "reason": item["rationale"],
                    "status": "pending",
                    "agent_run_id": None,
                    "created_at": item["createdAt"],
                    "version": item["version"],
                }
                for item in pending
            ]
        if resource_path == "external-evidence-cards":
            return [
                {
                    "id": item["id"],
                    "sourceUrl": item["sourceUrl"] or "",
                    "sourceDomain": (
                        (item.get("sourcePayload") or {}).get("sourceDomain") or ""
                    ),
                    "sourceTier": (
                        (item.get("sourcePayload") or {}).get("sourceTier")
                        or "unknown"
                    ),
                    "title": item["title"],
                    "publishedAt": (
                        item.get("sourcePayload") or {}
                    ).get("publishedAt"),
                    "factExcerpt": item["summary"],
                    "summary": item["summary"],
                    "tags": (item.get("sourcePayload") or {}).get("tags") or [],
                    "relatedScopeType": (
                        item.get("sourcePayload") or {}
                    ).get("relatedScopeType")
                    or ("client" if item.get("projectId") else "organization"),
                    "relatedScopeId": (
                        item.get("sourcePayload") or {}
                    ).get("relatedScopeId")
                    or item.get("projectId")
                    or identity.organization_id,
                    "confidence": float(
                        (item.get("sourcePayload") or {}).get("confidence") or 0
                    ),
                    "status": (
                        "accepted"
                        if item["status"] == "accepted"
                        else "rejected"
                        if item["status"] in {"returned", "archived"}
                        else "candidate"
                    ),
                    "reviewedBy": (
                        item.get("revisions") or [{}]
                    )[0].get("revisedByMembershipId"),
                    "reviewedAt": (
                        item.get("revisions") or [{}]
                    )[0].get("createdAt"),
                    "reviewNote": "",
                    "linkedProposalIds": (
                        item.get("sourcePayload") or {}
                    ).get("linkedProposalIds")
                    or [],
                    "createdAt": item["createdAt"],
                    "updatedAt": item["updatedAt"],
                    "version": item["version"],
                }
                for item in self._intelligence_facts(identity)
                if item["recordKind"] in EXTERNAL_EVIDENCE_KINDS
                and item["status"] != "archived"
            ]
        if resource_path == "strategic/thoughts":
            return self._strategic_thoughts_view(identity, query)
        if resource_path == "data-center/proposal-drafts":
            return [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "title": item["title"],
                    "summary": item["summary"],
                    "rationale": item["rationale"],
                    "riskLevel": item["riskLevel"],
                    "targetRefs": item["targetRefs"],
                    "sourceRefs": item["sourceRefs"],
                    "boundaryNotes": item["boundaryNotes"],
                    "payload": item["payload"],
                    "requiresApproval": True,
                    "status": (
                        "reviewed"
                        if item["authorityStatus"] == "inbox"
                        else "rejected"
                        if item["authorityStatus"] == "returned"
                        else "draft"
                    ),
                    "clientId": item["clientId"] or None,
                    "reviewedAt": (
                        item["updatedAt"]
                        if item["authorityStatus"] == "inbox"
                        else None
                    ),
                    "createdAt": item["createdAt"],
                    "updatedAt": item["updatedAt"],
                    "version": item["version"],
                }
                for item in self._proposals(identity)
                if item["authorityStatus"] in {"candidate", "inbox", "returned"}
            ]
        if resource_path.startswith("data-center/"):
            data = self._data_center_view(identity)
            if resource_path == "data-center/schema/status":
                return {
                    "generatedAt": data["generatedAt"],
                    "ensuredTables": data["tables"],
                    "missingTables": [],
                    "errors": [] if data["schema"] else ["schema build identity missing"],
                    "permissionDiagnostics": {
                        "commandCount": len(data["commands"]),
                        "deadLetterCount": data["deadLetterCount"],
                    },
                }
            if resource_path == "data-center/diagnose":
                return data
            if resource_path == "data-center/operational-status":
                blocking = []
                if data["deadLetterCount"]:
                    blocking.append(
                        f"{data['deadLetterCount']} 个未解决 operation dead letter"
                    )
                if data["outboxStatus"].get("failed"):
                    blocking.append(
                        f"{data['outboxStatus']['failed']} 个 outbox 事件失败"
                    )
                latest_reconciliation = (
                    data["reconciliationRuns"][0]
                    if data["reconciliationRuns"]
                    else None
                )
                return {
                    "fullRegressionVerdict": "hold" if blocking else "pass",
                    "p22StrictPass": not blocking,
                    "p23StrictPass": not blocking,
                    "rolloutStage": (
                        data["releaseGates"][0].get("candidate_version")
                        if data["releaseGates"]
                        else ""
                    ),
                    "rolloutLatestVerdict": (
                        data["releaseGates"][0].get("decision")
                        if data["releaseGates"]
                        else ""
                    ),
                    "retryAlerts": blocking,
                    "latestSnapshotAt": (
                        latest_reconciliation.get("started_at")
                        if latest_reconciliation
                        else None
                    ),
                    "rollbackDrillPass": any(
                        item.get("registry_state_id") == "rollback_drill"
                        and item.get("status") == "completed"
                        for item in data["reconciliationRuns"]
                    ),
                    "releaseReportVerdict": "hold" if blocking else "pass",
                    "blockingIssues": blocking,
                }
            if resource_path == "data-center/artifact-status":
                items = [
                    {
                        "key": key,
                        "label": key,
                        "path": "",
                        "exists": count > 0,
                        "verdict": "pass" if count > 0 else "unknown",
                        "stale": False,
                        "generatedAt": data["generatedAt"],
                        "gitCommit": None,
                        "backendBuildHash": (
                            (data.get("schema") or {}).get("build_id")
                        ),
                        "runtimeMode": "strict_cloud",
                        "dataDir": None,
                        "sourceRunId": None,
                        "blockingIssues": [],
                    }
                    for key, count in sorted(data["counts"].items())
                ]
                return {
                    "generatedAt": data["generatedAt"],
                    "overallPass": all(item["exists"] for item in items),
                    "items": items,
                }
            if resource_path == "data-center/execution-retry-metrics":
                ticket_commands = [
                    item
                    for item in data["commands"]
                    if item.get("aggregate_type") == "execution_ticket"
                ]
                retried = sum(
                    int(item.get("attempt_count") or 0) > 1
                    for item in ticket_commands
                )
                failed_reasons = Counter(
                    _text(item.get("error_code"))
                    for item in data["deadLetters"]
                    if item.get("error_code")
                )
                return {
                    "windowDays": int(query.get("days") or 7),
                    "totalTickets": len(ticket_commands),
                    "failedTickets": data["deadLetterCount"],
                    "retriedTickets": retried,
                    "retryExhaustedTickets": 0,
                    "retrySuccessRate": (
                        round(
                            100
                            * sum(
                                item.get("status") == "committed"
                                for item in ticket_commands
                            )
                            / len(ticket_commands),
                            2,
                        )
                        if ticket_commands
                        else 0
                    ),
                    "avgRetryCount": (
                        round(
                            sum(
                                max(int(item.get("attempt_count") or 0) - 1, 0)
                                for item in ticket_commands
                            )
                            / len(ticket_commands),
                            2,
                        )
                        if ticket_commands
                        else 0
                    ),
                    "oldestFailedTicketAgeHours": 0,
                    "failureReasonTopN": [
                        {"key": key, "count": count}
                        for key, count in failed_reasons.most_common(5)
                    ],
                    "failedStageTopN": [],
                    "alerts": [
                        {
                            "level": "warning",
                            "message": (
                                f"{data['deadLetterCount']} 个执行操作待人工处置"
                            ),
                        }
                    ]
                    if data["deadLetterCount"]
                    else [],
                }
            if resource_path == "data-center/kernel-primary-rollout":
                configured_runs = []
                with self.repository._connection() as connection:
                    plan_rows = connection.execute(
                        """
                        SELECT * FROM organization_plans
                        WHERE organization_id = ?
                          AND period_label = 'kernel_primary_rollout'
                        ORDER BY updated_at DESC, plan_id
                        """,
                        (identity.organization_id,),
                    ).fetchall()
                for row in plan_rows:
                    attributes = _json(row["attributes_json"], {})
                    if not isinstance(attributes, dict):
                        attributes = {}
                    configured_runs.append(
                        {
                            "id": row["plan_id"],
                            "stage": attributes.get("stage")
                            or "stage_1_client",
                            "clientIds": attributes.get("clientIds") or [],
                            "status": attributes.get("runStatus")
                            or row["status"],
                            "metricsBefore": attributes.get("metricsBefore")
                            or {},
                            "metricsAfter": attributes.get("metricsAfter")
                            or {},
                            "verdict": attributes.get("verdict"),
                            "recommendedAction": attributes.get(
                                "recommendedAction"
                            ),
                            "note": attributes.get("note") or row["summary"],
                            "rollbackReason": attributes.get("rollbackReason"),
                            "startedAt": attributes.get("startedAt"),
                            "completedAt": attributes.get("completedAt"),
                            "createdAt": row["created_at"],
                            "updatedAt": row["updated_at"],
                            "version": row["version"],
                        }
                    )
                legacy_release_evidence = [
                    {
                        "id": item.get("gate_id"),
                        "stage": "stage_1_client",
                        "clientIds": [],
                        "status": (
                            "completed"
                            if item.get("decision") == "go"
                            else "failed"
                        ),
                        "metricsBefore": {},
                        "metricsAfter": {},
                        "note": item.get("owner") or "",
                        "startedAt": item.get("decided_at"),
                        "completedAt": item.get("decided_at"),
                        "createdAt": item.get("decided_at"),
                        "updatedAt": item.get("decided_at"),
                    }
                    for item in data["releaseGates"]
                ]
                return [*configured_runs, *legacy_release_evidence]
            if resource_path == "data-center/shadow-runs":
                return [
                    {
                        "id": item.get("run_id"),
                        "scopeType": "organization",
                        "scopeId": identity.organization_id,
                        "page": item.get("registry_state_id") or "",
                        "mode": "strict_reconciliation",
                        "prompt": "",
                        "baseline": {},
                        "candidate": _json(item.get("report_json"), {}),
                        "routeDecision": {},
                        "retrievalTrace": {},
                        "answerPlan": {},
                        "answerQuality": {},
                        "actionSuggestion": [],
                        "overlapRate": 0,
                        "candidateFailed": item.get("status") == "failed",
                        "failureReason": (
                            "reconciliation_failed"
                            if item.get("status") == "failed"
                            else None
                        ),
                        "createdAt": item.get("started_at"),
                    }
                    for item in data["reconciliationRuns"]
                ]
            if resource_path == "data-center/shadow-summary":
                runs = data["reconciliationRuns"]
                failed = sum(item.get("status") == "failed" for item in runs)
                completed = sum(item.get("status") == "completed" for item in runs)
                return {
                    "total": len(runs),
                    "answerQualityPassRate": (
                        round(completed / len(runs), 4) if runs else 0
                    ),
                    "directAnswerPassRate": 0,
                    "evidenceListOnlyFailRate": 0,
                    "candidateBetterRate": 0,
                    "candidateBetterByGradeRate": 0,
                    "gradeDeltaAvg": 0,
                    "independentChainPassRate": (
                        round(completed / len(runs), 4) if runs else 0
                    ),
                    "overlapRateAvg": 0,
                    "failures": failed,
                }
            if resource_path == "data-center/team-sync/stats":
                team_events = [
                    item
                    for item in data["outbox"]
                    if "team" in _text(item.get("event_type")).lower()
                    or "member" in _text(item.get("event_type")).lower()
                ]
                statuses = Counter(_text(item.get("status")) for item in team_events)
                return {
                    "total": len(team_events),
                    "statusCounts": dict(statuses),
                    "events": team_events,
                }
            if resource_path == "data-center/evidence-quality":
                evidence = self._snapshot(identity).get("growthEvidence") or []
                return evidence
            if resource_path == "data-center/evidence-quality/snapshots":
                return [
                    {
                        "id": item.get("run_id"),
                        "windowStart": item.get("started_at"),
                        "windowEnd": item.get("finished_at") or item.get("started_at"),
                        "labelCounts": _json(
                            item.get("report_json"), {}
                        ).get("statusCounts")
                        or {},
                        "usefulExamples": [],
                        "noiseExamples": [],
                        "needsReviewExamples": [],
                        "recommendedRules": [],
                        "createdAt": item.get("started_at"),
                    }
                    for item in data["reconciliationRuns"]
                    if item.get("registry_state_id") == "evidence_quality"
                ]
        if resource_path == "execution-tickets":
            operations = self._operations(identity)
            return [
                self._execution_ticket(identity, item)
                for item in operations["commands"]
                if _text(item.get("aggregate_type")) == "execution_ticket"
            ]
        ticket_id = _path_id(resource_path, "execution-tickets", "/logs")
        if ticket_id is not None:
            _require_admin(identity)
            with self.repository._connection() as connection:
                command = connection.execute(
                    """
                    SELECT * FROM command_envelopes
                    WHERE command_id = ? AND organization_id = ?
                    """,
                    (ticket_id, identity.organization_id),
                ).fetchone()
                if command is None:
                    raise RepositoryError(
                        404, "execution_ticket_not_found", "执行票据不存在"
                    )
                attempts = connection.execute(
                    """
                    SELECT * FROM operation_attempts
                    WHERE command_id = ?
                    ORDER BY attempt_no, created_at
                    """,
                    (ticket_id,),
                ).fetchall()
            return [
                {
                    "id": row["attempt_id"],
                    "ticketId": ticket_id,
                    "stage": "retry" if int(row["attempt_no"]) > 1 else "validate",
                    "status": (
                        "failed"
                        if row["error_code"]
                        else "started"
                        if row["transport_state"] in {"queued", "running"}
                        else "success"
                    ),
                    "message": row["error_message"]
                    or f"transport_state={row['transport_state']}",
                    "payload": {
                        "attemptNo": row["attempt_no"],
                        "nextRetryAt": row["next_retry_at"],
                    },
                    "createdAt": row["created_at"],
                }
                for row in attempts
            ]
        raise RepositoryError(
            404,
            "intelligence_growth_query_unknown",
            f"未知的情报成长查询：{resource_path}",
        )

    def version(
        self,
        identity: SessionIdentity,
        *,
        resource_path: str,
        query: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        consultation_request_id = _path_id(
            resource_path,
            "consultation/knowledge-requests",
            "/retry",
        )
        if consultation_request_id is not None:
            with self.repository._connection() as connection:
                row = self._intelligence_row(
                    connection,
                    identity,
                    consultation_request_id,
                )
            if _text(row["record_kind"]) != CONSULTATION_REQUEST_KIND:
                raise RepositoryError(
                    404,
                    "consultation_request_missing",
                    "咨询知识请求不存在",
                )
            return {"expectedVersion": int(row["version"])}
        if resource_path == "intelligence/brand-mirror/strategy-extract":
            client_id = _text((query or {}).get("clientId"))
            self._require_visible_project(identity, client_id)
            with self.repository._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT n.version, v.content_json
                    FROM narrative_outputs AS n
                    JOIN narrative_output_versions AS v
                      ON v.narrative_output_id = n.narrative_output_id
                     AND v.version = n.latest_version
                    WHERE n.organization_id = ?
                      AND n.output_kind = 'strategy_report'
                      AND n.lifecycle_state != 'archived'
                    ORDER BY n.updated_at DESC, n.narrative_output_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            for row in rows:
                content = _json(row["content_json"], {})
                if (
                    isinstance(content, dict)
                    and _text(content.get("clientId")) == client_id
                ):
                    return {"expectedVersion": int(row["version"])}
            return {"expectedVersion": 0}
        resource_id: str | None = None
        table = "intelligence_records"
        id_column = "intelligence_id"
        for prefix, suffix in (
            ("approvals", "/approve"),
            ("approvals", "/reject"),
            ("data-center/proposal-drafts", "/mark-reviewed"),
            ("data-center/proposal-drafts", "/promote"),
            ("data-center/proposal-drafts", "/reject"),
            ("external-evidence-cards", "/accept"),
            ("external-evidence-cards", "/create-proposal-draft"),
            ("external-evidence-cards", "/reject"),
            ("intelligence/items", "/dismiss"),
            ("intelligence/items", "/follow"),
            ("intelligence/profiles", ""),
            ("intelligence/profiles", "/refresh"),
            ("intelligence/profiles", "/trial-run"),
            ("proposals", "/approve"),
            ("proposals", "/execute"),
            ("proposals", "/execution-ticket"),
            ("proposals", "/reject"),
            ("strategic/thoughts", "/review"),
            ("strategic/thoughts", "/state"),
            ("topic-candidates", "/external-evidence-card"),
            ("topics/candidates", ""),
            ("topics/radars", ""),
        ):
            resource_id = _path_id(resource_path, prefix, suffix)
            if resource_id is not None:
                break
        if resource_id is None:
            resource_id = _path_id(resource_path, "growth/pending-captures", "/state")
            if resource_id is not None:
                table, id_column = "growth_signals", "growth_signal_id"
        if resource_id is None:
            for suffix in ("/accept", "/dismiss"):
                resource_id = _path_id(resource_path, "growth/recommendations", suffix)
                if resource_id is not None:
                    table, id_column = "growth_evidence", "growth_evidence_id"
                    break
        if resource_id is None:
            resource_id = _path_id(
                resource_path,
                "data-center/evidence-quality",
                "/label",
            )
            if resource_id is not None:
                table, id_column = "growth_evidence", "growth_evidence_id"
        if resource_id is None:
            return {"expectedVersion": None}
        with self.repository._connection() as connection:
            row = connection.execute(
                f"""
                SELECT version FROM {table}
                WHERE {id_column} = ? AND organization_id = ?
                """,
                (resource_id, identity.organization_id),
            ).fetchone()
        if row is None:
            raise RepositoryError(404, "resource_not_found", "对象不存在或不可见")
        return {"expectedVersion": int(row["version"])}

    # ---------- strict command transaction ----------

    def _receipt(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload_hash: str,
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
        if str(row["payload_hash"]) != payload_hash:
            raise RepositoryError(
                409,
                "idempotency_payload_conflict",
                "同一幂等键不能用于不同请求",
            )
        return _json(row["result_json"], {})

    def _record_command(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        normalized: dict[str, Any],
        payload_hash: str,
        mutation: Mutation,
    ) -> None:
        now = utc_now()
        operation_id = mutation.operation_id or new_id()
        command_id = new_id()
        if mutation.aggregate_type == "execution_ticket":
            command_id = mutation.aggregate_id or command_id
            mutation.aggregate_id = command_id
            proposal = mutation.result.get("proposal") or {}
            created_task_ids = [
                _text(value)
                for value in mutation.result.get("createdTaskIds") or []
                if _text(value)
            ]
            execution_completed = bool(created_task_ids)
            ticket = {
                "id": command_id,
                "proposalId": proposal.get("id") or normalized.get("proposalId") or "",
                "clientId": proposal.get("clientId") or "",
                "executionType": "proposal_tasks",
                "status": (
                    "executed"
                    if execution_completed
                    else "running"
                    if mutation.queue_execution
                    else "pending"
                ),
                "payload": {
                    "taskDrafts": proposal.get("taskDrafts") or [],
                    "sourceVersion": proposal.get("version"),
                },
                "result": {
                    "resultType": (
                        "tasks_created"
                        if execution_completed
                        else "recorded_only"
                    ),
                    "summary": (
                        f"已创建 {len(created_task_ids)} 条严格任务"
                        if execution_completed
                        else "已进入严格执行队列"
                        if mutation.queue_execution
                        else "已登记严格执行票据"
                    ),
                    "createdTaskIds": created_task_ids,
                    "artifactRefs": [],
                },
                "idempotencyKey": idempotency_key,
                "retryCount": 0,
                "maxRetries": 3,
                "lastError": None,
                "lastAttemptAt": (
                    now
                    if mutation.queue_execution or execution_completed
                    else None
                ),
                "errorMessage": None,
                "executedAt": now if execution_completed else None,
                "createdAt": now,
                "updatedAt": now,
            }
            mutation.result["executionTicket"] = ticket
            mutation.outbox_payload["ticketId"] = command_id
        if mutation.aggregate_type == "bulk_operation":
            item_results = mutation.result.get("items") or []
            snapshot = {
                str(item.get("id")): int(item.get("beforeVersion") or 0)
                for item in item_results
            }
            connection.execute(
                """
                INSERT INTO bulk_operations (
                    bulk_operation_id, scope_id, organization_id, operation_id,
                    preflight_snapshot_hash, atomicity_mode, status, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'all_or_nothing', 'committed', 1, ?, ?)
                """,
                (
                    mutation.aggregate_id,
                    identity.scope_id,
                    identity.organization_id,
                    operation_id,
                    sha256_text(canonical_json(snapshot)),
                    now,
                    now,
                ),
            )
            for item in item_results:
                connection.execute(
                    """
                    INSERT INTO bulk_operation_items (
                        bulk_item_id, bulk_operation_id, item_key,
                        preflight_result, commit_result, conflict_code, result_json
                    ) VALUES (?, ?, ?, 'ready', 'committed', NULL, ?)
                    """,
                    (
                        new_id(),
                        mutation.aggregate_id,
                        str(item.get("id")),
                        canonical_json(item),
                    ),
                )
        result_json = canonical_json(mutation.result)
        connection.execute(
            """
            INSERT INTO command_envelopes (
                command_id, scope_id, organization_id, operation_id,
                idempotency_key, aggregate_type, aggregate_id, command_type,
                actor_principal_id, expected_version, payload_json,
                payload_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', ?, ?)
            """,
            (
                command_id,
                identity.scope_id,
                identity.organization_id,
                operation_id,
                idempotency_key,
                mutation.aggregate_type,
                mutation.aggregate_id,
                command_type,
                identity.principal_id,
                normalized.get("expectedVersion"),
                canonical_json(normalized),
                payload_hash,
                now,
                now,
            ),
        )
        if mutation.aggregate_type == "execution_ticket" and mutation.queue_execution:
            connection.execute(
                """
                INSERT INTO operation_attempts (
                    attempt_id, scope_id, command_id, attempt_no,
                    transport_state, lease_owner, lease_until,
                    permission_revalidated_at, next_retry_at, error_code,
                    error_message, created_at
                ) VALUES (?, ?, ?, 1, 'queued', NULL, NULL, ?, NULL, NULL, NULL, ?)
                """,
                (
                    new_id(),
                    identity.scope_id,
                    command_id,
                    now,
                    now,
                ),
            )
        self.repository._insert_audit(
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            actor_id=identity.principal_id,
            action=command_type,
            resource_type=mutation.aggregate_type,
            resource_id=mutation.aggregate_id,
            before_version=mutation.before_version,
            after_version=mutation.after_version,
            summary=mutation.summary or normalized,
        )
        self.repository._insert_outbox(
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            aggregate_type=mutation.aggregate_type,
            aggregate_id=mutation.aggregate_id,
            aggregate_version=mutation.after_version,
            event_type=mutation.event_type,
            payload=mutation.outbox_payload
            or {
                "resourceId": mutation.aggregate_id,
                "version": mutation.after_version,
            },
        )
        for child in mutation.children:
            self.repository._insert_audit(
                connection,
                scope_id=identity.scope_id,
                organization_id=identity.organization_id,
                operation_id=operation_id,
                actor_id=identity.principal_id,
                action=child.event_type,
                resource_type=child.aggregate_type,
                resource_id=child.aggregate_id,
                before_version=child.before_version,
                after_version=child.after_version,
                summary=child.summary,
            )
            self.repository._insert_outbox(
                connection,
                scope_id=identity.scope_id,
                organization_id=identity.organization_id,
                operation_id=operation_id,
                aggregate_type=child.aggregate_type,
                aggregate_id=child.aggregate_id,
                aggregate_version=child.after_version,
                event_type=child.event_type,
                payload=child.outbox_payload
                or {
                    "resourceId": child.aggregate_id,
                    "version": child.after_version,
                },
            )
        connection.execute(
            """
            INSERT INTO command_idempotency (
                record_id, scope_id, actor_principal_id, command_type,
                idempotency_key, payload_hash, result_hash, result_json,
                expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                _expiry(),
                now,
            ),
        )

    def _intelligence_row(
        self,
        connection: Any,
        identity: SessionIdentity,
        intelligence_id: str,
    ) -> Any:
        row = connection.execute(
            """
            SELECT * FROM intelligence_records
            WHERE intelligence_id = ? AND organization_id = ?
            """,
            (intelligence_id, identity.organization_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "intelligence_not_found", "情报对象不存在")
        if not identity.is_admin and str(row["created_by_membership_id"] or "") not in {
            "",
            identity.membership_id,
        }:
            raise RepositoryError(403, "intelligence_write_forbidden", "无权修改该情报")
        return row

    def _update_intelligence_status(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        intelligence_id: str,
        payload: Mapping[str, Any],
        target_status: str,
        event_type: str,
    ) -> Mutation:
        row = self._intelligence_row(connection, identity, intelligence_id)
        before = int(row["version"])
        _require_expected(payload, before)
        after = before + 1
        now = utc_now()
        title = _text(payload.get("title")) or str(row["title"])
        summary = (
            _text(payload.get("summary"))
            if "summary" in payload
            else str(row["summary"])
        )
        connection.execute(
            """
            UPDATE intelligence_records
            SET title = ?, summary = ?, status = ?, version = ?, updated_at = ?
            WHERE intelligence_id = ? AND organization_id = ? AND version = ?
            """,
            (
                title,
                summary,
                target_status,
                after,
                now,
                intelligence_id,
                identity.organization_id,
                before,
            ),
        )
        connection.execute(
            """
            INSERT INTO intelligence_revisions (
                intelligence_revision_id, organization_id, intelligence_id,
                revision, title, summary, revised_by_membership_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                identity.organization_id,
                intelligence_id,
                after,
                title,
                summary,
                identity.membership_id,
                now,
            ),
        )
        result = {
            "id": intelligence_id,
            "intelligenceId": intelligence_id,
            "title": title,
            "summary": summary,
            "status": target_status,
            "version": after,
            "updatedAt": now,
        }
        return Mutation(
            aggregate_type="intelligence",
            aggregate_id=intelligence_id,
            before_version=before,
            after_version=after,
            result=result,
            event_type=event_type,
            summary={"status": target_status, "previousStatus": row["status"]},
        )

    def _insert_intelligence(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        title: str,
        summary: str,
        record_kind: str,
        source_payload: Mapping[str, Any],
        project_id: str | None = None,
        source_url: str = "",
        status: str = "candidate",
        event_type: str,
    ) -> Mutation:
        normalized_title = title.strip()
        if not normalized_title:
            raise RepositoryError(422, "intelligence_title_required", "情报标题不能为空")
        now = utc_now()
        intelligence_id = new_id()
        connection.execute(
            """
            INSERT INTO intelligence_records (
                intelligence_id, organization_id, project_id, title, summary,
                source_url, record_kind, status, visibility_scope,
                created_by_membership_id, source_payload_json, version,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'organization', ?, ?, 1, ?, ?)
            """,
            (
                intelligence_id,
                identity.organization_id,
                project_id,
                normalized_title,
                summary.strip(),
                source_url.strip(),
                record_kind,
                status,
                identity.membership_id,
                canonical_json(dict(source_payload)),
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO intelligence_revisions (
                intelligence_revision_id, organization_id, intelligence_id,
                revision, title, summary, revised_by_membership_id, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                new_id(),
                identity.organization_id,
                intelligence_id,
                normalized_title,
                summary.strip(),
                identity.membership_id,
                now,
            ),
        )
        result = {
            "id": intelligence_id,
            "intelligenceId": intelligence_id,
            "title": normalized_title,
            "summary": summary.strip(),
            "recordKind": record_kind,
            "status": status,
            "projectId": project_id,
            "version": 1,
            "createdAt": now,
            "updatedAt": now,
        }
        return Mutation(
            aggregate_type="intelligence",
            aggregate_id=intelligence_id,
            before_version=None,
            after_version=1,
            result=result,
            event_type=event_type,
            summary={"recordKind": record_kind, "status": status},
        )

    def _mutate_growth(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        table: str,
        id_column: str,
        resource_id: str,
        state_column: str,
        target_state: str,
        payload: Mapping[str, Any],
        aggregate_type: str,
        event_type: str,
    ) -> Mutation:
        row = connection.execute(
            f"""
            SELECT * FROM {table}
            WHERE {id_column} = ? AND organization_id = ?
            """,
            (resource_id, identity.organization_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "growth_resource_not_found", "成长对象不存在")
        if not identity.is_admin and str(row["membership_id"]) != identity.membership_id:
            raise RepositoryError(403, "growth_write_forbidden", "无权修改该成长对象")
        before = int(row["version"])
        _require_expected(payload, before)
        after = before + 1
        now = utc_now()
        connection.execute(
            f"""
            UPDATE {table}
            SET {state_column} = ?, version = ?, updated_at = ?
            WHERE {id_column} = ? AND organization_id = ? AND version = ?
            """,
            (
                target_state,
                after,
                now,
                resource_id,
                identity.organization_id,
                before,
            ),
        )
        return Mutation(
            aggregate_type=aggregate_type,
            aggregate_id=resource_id,
            before_version=before,
            after_version=after,
            result={
                "id": resource_id,
                "state": target_state,
                "version": after,
                "updatedAt": now,
            },
            event_type=event_type,
            summary={"state": target_state, "previousState": row[state_column]},
        )

    def _mutate_reaction(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        quote_id: str,
        reaction_type: str,
    ) -> Mutation:
        quote = connection.execute(
            """
            SELECT * FROM experience_quotes
            WHERE experience_quote_id = ? AND organization_id = ?
              AND lifecycle_state = 'active'
            """,
            (quote_id, identity.organization_id),
        ).fetchone()
        if quote is None:
            raise RepositoryError(404, "experience_quote_not_found", "经验卡不存在")
        existing = connection.execute(
            """
            SELECT experience_reaction_id FROM experience_reactions
            WHERE organization_id = ? AND experience_quote_id = ?
              AND membership_id = ? AND reaction_type = ?
            """,
            (
                identity.organization_id,
                quote_id,
                identity.membership_id,
                reaction_type,
            ),
        ).fetchone()
        now = utc_now()
        active = existing is None
        if existing is None:
            connection.execute(
                """
                INSERT INTO experience_reactions (
                    experience_reaction_id, organization_id,
                    experience_quote_id, membership_id, reaction_type, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    quote_id,
                    identity.membership_id,
                    reaction_type,
                    now,
                ),
            )
        else:
            connection.execute(
                "DELETE FROM experience_reactions WHERE experience_reaction_id = ?",
                (existing["experience_reaction_id"],),
            )
        count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM experience_reactions
                WHERE organization_id = ? AND experience_quote_id = ?
                  AND reaction_type = ?
                """,
                (identity.organization_id, quote_id, reaction_type),
            ).fetchone()[0]
        )
        version = int(quote["version"])
        return Mutation(
            aggregate_type="experience_quote",
            aggregate_id=quote_id,
            before_version=version,
            after_version=version,
            result={
                "id": quote_id,
                "reactionType": reaction_type,
                "active": active,
                "count": count,
            },
            event_type=f"experience_quote.{reaction_type}_{'added' if active else 'removed'}",
            summary={"reactionType": reaction_type, "active": active},
        )

    def _create_evidence_snapshot(
        self,
        connection: Any,
        identity: SessionIdentity,
    ) -> Mutation:
        _require_admin(identity)
        rows = connection.execute(
            """
            SELECT validation_state, COUNT(*) AS count
            FROM growth_evidence
            WHERE organization_id = ?
            GROUP BY validation_state
            """,
            (identity.organization_id,),
        ).fetchall()
        status_counts = {str(row["validation_state"]): int(row["count"]) for row in rows}
        run_id = new_id()
        operation_id = new_id()
        now = utc_now()
        report = {
            "statusCounts": status_counts,
            "total": sum(status_counts.values()),
            "generatedAt": now,
            "authoritySource": "growth_evidence",
        }
        connection.execute(
            """
            INSERT INTO reconciliation_runs (
                run_id, scope_id, organization_id, operation_id,
                registry_state_id, mismatch_count, status, report_json,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, 'evidence_quality', ?, 'completed', ?, ?, ?)
            """,
            (
                run_id,
                identity.scope_id,
                identity.organization_id,
                operation_id,
                status_counts.get("candidate", 0),
                canonical_json(report),
                now,
                now,
            ),
        )
        return Mutation(
            aggregate_type="reconciliation_run",
            aggregate_id=run_id,
            before_version=None,
            after_version=1,
            result={"id": run_id, "runId": run_id, **report},
            event_type="evidence_quality.snapshot_created",
            summary=report,
        )

    def _resolve_data_center(
        self,
        connection: Any,
        identity: SessionIdentity,
    ) -> Mutation:
        _require_admin(identity)
        mismatch_count = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM operation_dead_letters
                WHERE organization_id = ? AND status = 'open'
                """,
                (identity.organization_id,),
            ).fetchone()[0]
        )
        run_id = new_id()
        operation_id = new_id()
        now = utc_now()
        report = {
            "openDeadLetterCount": mismatch_count,
            "action": "diagnostic_only",
            "message": (
                "存在待人工处置的死信"
                if mismatch_count
                else "未发现可确定修复的操作异常"
            ),
            "generatedAt": now,
        }
        connection.execute(
            """
            INSERT INTO reconciliation_runs (
                run_id, scope_id, organization_id, operation_id,
                registry_state_id, mismatch_count, status, report_json,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, 'data_center_kernel', ?, 'completed', ?, ?, ?)
            """,
            (
                run_id,
                identity.scope_id,
                identity.organization_id,
                operation_id,
                mismatch_count,
                canonical_json(report),
                now,
                now,
            ),
        )
        return Mutation(
            aggregate_type="reconciliation_run",
            aggregate_id=run_id,
            before_version=None,
            after_version=1,
            result={"runId": run_id, **report},
            event_type="data_center.reconciliation_completed",
            summary=report,
        )

    def _update_intelligence_payload(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        intelligence_id: str,
        payload: Mapping[str, Any],
        title: str | None = None,
        summary: str | None = None,
        source_payload: Mapping[str, Any] | None = None,
        status: str | None = None,
        event_type: str,
    ) -> Mutation:
        row = self._intelligence_row(connection, identity, intelligence_id)
        before = int(row["version"])
        _require_expected(payload, before)
        after = before + 1
        now = utc_now()
        next_title = (title or str(row["title"])).strip()
        next_summary = (
            summary if summary is not None else str(row["summary"])
        ).strip()
        next_status = status or str(row["status"])
        current_payload = _json(row["source_payload_json"], {})
        if not isinstance(current_payload, dict):
            current_payload = {}
        next_payload = {**current_payload, **dict(source_payload or {})}
        updated = connection.execute(
            """
            UPDATE intelligence_records
            SET title = ?, summary = ?, status = ?, source_payload_json = ?,
                version = ?, updated_at = ?
            WHERE intelligence_id = ? AND organization_id = ? AND version = ?
            """,
            (
                next_title,
                next_summary,
                next_status,
                canonical_json(next_payload),
                after,
                now,
                intelligence_id,
                identity.organization_id,
                before,
            ),
        )
        if updated.rowcount != 1:
            raise RepositoryError(
                409,
                "version_conflict",
                "情报对象版本已变化，请刷新后重试",
            )
        connection.execute(
            """
            INSERT INTO intelligence_revisions (
                intelligence_revision_id, organization_id, intelligence_id,
                revision, title, summary, revised_by_membership_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                identity.organization_id,
                intelligence_id,
                after,
                next_title,
                next_summary,
                identity.membership_id,
                now,
            ),
        )
        result = {
            "id": intelligence_id,
            "intelligenceId": intelligence_id,
            "title": next_title,
            "summary": next_summary,
            "status": next_status,
            "sourcePayload": next_payload,
            "version": after,
            "updatedAt": now,
        }
        return Mutation(
            aggregate_type="intelligence",
            aggregate_id=intelligence_id,
            before_version=before,
            after_version=after,
            result=result,
            event_type=event_type,
            summary={"status": next_status, "recordKind": row["record_kind"]},
        )

    def _operational_reconciliation(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        registry_state_id: str,
        report: Mapping[str, Any],
        event_type: str,
        mismatch_count: int = 0,
        status: str = "completed",
    ) -> Mutation:
        run_id = new_id()
        operation_id = new_id()
        now = utc_now()
        normalized_report = {**dict(report), "generatedAt": now}
        connection.execute(
            """
            INSERT INTO reconciliation_runs (
                run_id, scope_id, organization_id, operation_id,
                registry_state_id, mismatch_count, status, report_json,
                started_at, finished_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                identity.scope_id,
                identity.organization_id,
                operation_id,
                registry_state_id,
                mismatch_count,
                status,
                canonical_json(normalized_report),
                now,
                now if status != "running" else None,
            ),
        )
        return Mutation(
            aggregate_type="reconciliation_run",
            aggregate_id=run_id,
            before_version=None,
            after_version=1,
            result={"runId": run_id, **normalized_report},
            event_type=event_type,
            summary={
                "registryStateId": registry_state_id,
                "mismatchCount": mismatch_count,
                "status": status,
            },
            operation_id=operation_id,
        )

    def _kernel_rollout(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        resource_path: str,
        payload: Mapping[str, Any],
    ) -> Mutation:
        _require_admin(identity)
        now = utc_now()
        if resource_path == "data-center/kernel-primary-rollout/start":
            stage = _text(payload.get("stage")) or "stage_1_client"
            if stage not in {
                "stage_1_client",
                "stage_3_clients",
                "stage_10_clients",
            }:
                raise RepositoryError(
                    422,
                    "kernel_rollout_stage_invalid",
                    "内核主链灰度阶段无效",
                )
            client_ids = list(
                dict.fromkeys(
                    _text(value)
                    for value in payload.get("clientIds") or []
                    if _text(value)
                )
            )
            if not client_ids:
                raise RepositoryError(
                    422,
                    "kernel_rollout_clients_required",
                    "请选择灰度项目",
                )
            placeholders = ",".join("?" for _ in client_ids)
            visible = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*) FROM clients
                    WHERE scope_id = ?
                      AND id IN ({placeholders})
                      AND lifecycle_state != 'deleted'
                    """,
                    (identity.scope_id, *client_ids),
                ).fetchone()[0]
            )
            if visible != len(client_ids):
                raise RepositoryError(
                    404,
                    "kernel_rollout_project_missing",
                    "灰度项目不存在或已归档",
                )
            plan_id = new_id()
            attributes = {
                "orgModelKind": "kernel_primary_rollout",
                "stage": stage,
                "clientIds": client_ids,
                "runStatus": "running",
                "metricsBefore": payload.get("metricsBefore") or {},
                "metricsAfter": {},
                "verdict": None,
                "recommendedAction": None,
                "note": _text(payload.get("note")),
                "rollbackReason": None,
                "startedAt": now,
                "completedAt": None,
            }
            connection.execute(
                """
                INSERT INTO organization_plans (
                    plan_id, organization_id, department_id, period_label,
                    owner_membership_id, summary, status, attributes_json,
                    version, created_at, updated_at
                ) VALUES (?, ?, NULL, 'kernel_primary_rollout', ?, ?,
                          'active', ?, 1, ?, ?)
                """,
                (
                    plan_id,
                    identity.organization_id,
                    identity.membership_id,
                    f"{stage} 内核主链灰度",
                    canonical_json(attributes),
                    now,
                    now,
                ),
            )
            return Mutation(
                aggregate_type="organization_plan",
                aggregate_id=plan_id,
                before_version=None,
                after_version=1,
                result={
                    "id": plan_id,
                    **attributes,
                    "status": "running",
                    "createdAt": now,
                    "updatedAt": now,
                    "version": 1,
                },
                event_type="kernel_primary_rollout.started",
                summary={"stage": stage, "clientCount": len(client_ids)},
            )
        match = re.fullmatch(
            r"data-center/kernel-primary-rollout/([^/]+)/(complete|rollback)",
            resource_path,
        )
        if match is None:
            raise RepositoryError(404, "kernel_rollout_unknown", "灰度操作不存在")
        plan_id, action = match.groups()
        row = connection.execute(
            """
            SELECT * FROM organization_plans
            WHERE organization_id = ? AND plan_id = ?
              AND period_label = 'kernel_primary_rollout'
            """,
            (identity.organization_id, plan_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "kernel_rollout_missing", "灰度记录不存在")
        before = int(row["version"])
        _require_expected(payload, before)
        attributes = _json(row["attributes_json"], {})
        if not isinstance(attributes, dict):
            attributes = {}
        if attributes.get("runStatus") != "running":
            raise RepositoryError(409, "kernel_rollout_not_running", "该灰度已结束")
        target = "completed" if action == "complete" else "rolled_back"
        attributes.update(
            {
                "runStatus": target,
                "metricsAfter": payload.get("metricsAfter") or {},
                "verdict": payload.get("verdict")
                or ("pass" if action == "complete" else "fail"),
                "recommendedAction": (
                    "keep" if action == "complete" else "rollback"
                ),
                "rollbackReason": (
                    _text(payload.get("reason")) if action == "rollback" else None
                ),
                "completedAt": now,
            }
        )
        after = before + 1
        updated = connection.execute(
            """
            UPDATE organization_plans
            SET status = 'completed', attributes_json = ?, version = ?,
                updated_at = ?
            WHERE organization_id = ? AND plan_id = ? AND version = ?
            """,
            (
                canonical_json(attributes),
                after,
                now,
                identity.organization_id,
                plan_id,
                before,
            ),
        )
        if updated.rowcount != 1:
            raise RepositoryError(
                409,
                "kernel_rollout_version_conflict",
                "灰度记录版本已变化",
            )
        return Mutation(
            aggregate_type="organization_plan",
            aggregate_id=plan_id,
            before_version=before,
            after_version=after,
            result={
                "id": plan_id,
                **attributes,
                "status": target,
                "createdAt": row["created_at"],
                "updatedAt": now,
                "version": after,
            },
            event_type=f"kernel_primary_rollout.{target}",
            summary={"status": target},
        )

    def _commit_external_capture(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
    ) -> Mutation:
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise RepositoryError(
                422,
                "external_capture_items_required",
                "公开采集提交必须包含结构化线索列表",
            )
        if len(raw_items) > 100:
            raise RepositoryError(
                422,
                "external_capture_batch_too_large",
                "单次最多提交 100 条公开线索",
            )
        capture_id = _text(payload.get("captureId")) or new_id()
        allowed_kinds = {
            "public_opinion_capture",
            "timely_external_capture",
            "topic_candidate",
        }
        allowed_content_kinds = {
            "brand_mirror",
            "public_opinion",
            "timely_intelligence",
        }
        children: list[Mutation] = []
        item_results: list[dict[str, Any]] = []
        duplicate_count = 0
        project_ids: set[str] = set()
        content_kinds: set[str] = set()
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, Mapping):
                raise RepositoryError(
                    422,
                    "external_capture_item_invalid",
                    f"第 {index + 1} 条公开线索格式无效",
                )
            title = _text(raw.get("title"))[:240]
            summary = _text(raw.get("summary"))[:4000]
            source_url = _text(raw.get("sourceUrl"))[:2048]
            if (
                not title
                or not summary
                or not re.match(r"^https?://", source_url, re.IGNORECASE)
            ):
                raise RepositoryError(
                    422,
                    "external_capture_item_incomplete",
                    f"第 {index + 1} 条公开线索缺少标题、摘要或来源链接",
                )
            record_kind = _text(raw.get("recordKind"))
            content_kind = _text(raw.get("contentKind"))
            if (
                record_kind not in allowed_kinds
                or content_kind not in allowed_content_kinds
            ):
                raise RepositoryError(
                    422,
                    "external_capture_kind_invalid",
                    f"第 {index + 1} 条公开线索类型不受支持",
                )
            project_id = _text(raw.get("projectId"))
            if project_id:
                self._require_visible_project(identity, project_id)
                project_ids.add(project_id)
            content_kinds.add(content_kind)
            client_item_key = (
                _text(raw.get("clientItemKey"))[:160]
                or f"item:{index}"
            )
            content_hash = sha256_text(
                "\n".join((title, summary, source_url))
            )
            existing = connection.execute(
                """
                SELECT intelligence_id, version
                FROM intelligence_records
                WHERE organization_id = ?
                  AND COALESCE(project_id, '') = ?
                  AND record_kind = ?
                  AND source_url = ?
                  AND status != 'archived'
                ORDER BY updated_at DESC, intelligence_id
                LIMIT 1
                """,
                (
                    identity.organization_id,
                    project_id,
                    record_kind,
                    source_url,
                ),
            ).fetchone()
            if existing is not None:
                duplicate_count += 1
                item_results.append(
                    {
                        "clientItemKey": client_item_key,
                        "status": "duplicate",
                        "intelligenceId": str(existing["intelligence_id"]),
                        "version": int(existing["version"]),
                    }
                )
                continue
            sentiment = _text(raw.get("sentiment")).lower()
            if sentiment not in {"negative", "neutral", "positive"}:
                sentiment = "neutral"
            mutation = self._insert_intelligence(
                connection,
                identity,
                title=title,
                summary=summary,
                record_kind=record_kind,
                project_id=project_id or None,
                source_url=source_url,
                source_payload={
                    "captureId": capture_id,
                    "captureMethod": "local_public_search_metadata",
                    "contentKind": content_kind,
                    "sourceName": _text(raw.get("sourceName"))[:160],
                    "publishedAt": _text(raw.get("publishedAt")) or None,
                    "capturedAt": _text(raw.get("capturedAt")) or utc_now(),
                    "sentiment": sentiment,
                    "sentimentReason": _text(
                        raw.get("sentimentReason")
                    )[:500],
                    "tags": [
                        _text(value)[:80]
                        for value in raw.get("tags") or []
                        if _text(value)
                    ][:20],
                    "radarId": _text(raw.get("radarId")) or None,
                    "profileId": _text(raw.get("profileId")) or None,
                    "queryHash": _text(raw.get("queryHash"))[:64],
                    "sourceContentHash": content_hash,
                    "externalCollectionExecuted": True,
                    "modelAnalysisExecuted": False,
                    "sourceBodyStored": False,
                },
                status="candidate",
                event_type="external_intelligence.candidate_created",
            )
            mutation.outbox_payload = {
                "intelligenceId": mutation.aggregate_id,
                "captureId": capture_id,
                "contentKind": content_kind,
                "sourceContentHash": content_hash,
            }
            children.append(mutation)
            item_results.append(
                {
                    "clientItemKey": client_item_key,
                    "status": "inserted",
                    "intelligenceId": mutation.aggregate_id,
                    "version": 1,
                }
            )
        return Mutation(
            aggregate_type="external_intelligence_capture",
            aggregate_id=capture_id,
            before_version=None,
            after_version=1,
            result={
                "captureId": capture_id,
                "fetchedCount": len(raw_items),
                "insertedCount": len(children),
                "duplicateCount": duplicate_count,
                "items": item_results,
                "externalCollectionExecuted": True,
                "modelAnalysisExecuted": False,
                "sourceBodyStored": False,
            },
            event_type="external_intelligence.capture_committed",
            summary={
                "fetchedCount": len(raw_items),
                "insertedCount": len(children),
                "duplicateCount": duplicate_count,
                "projectCount": len(project_ids),
                "contentKinds": sorted(content_kinds),
                "sourceBodyStored": False,
            },
            outbox_payload={
                "captureId": capture_id,
                "insertedCount": len(children),
                "duplicateCount": duplicate_count,
            },
            children=children,
        )

    def _dispatch_mutation(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        resource_path: str,
        method: str,
        payload: Mapping[str, Any],
    ) -> Mutation:
        if (
            resource_path == "consultation/knowledge-requests"
            and method == "POST"
        ):
            return self._create_consultation_request(
                connection,
                identity,
                payload,
            )
        if (
            resource_path
            == "consultation/knowledge-requests/process-pending"
            and method == "POST"
        ):
            return self._process_consultation_requests(
                connection,
                identity,
                payload,
            )
        consultation_request_id = _path_id(
            resource_path,
            "consultation/knowledge-requests",
            "/retry",
        )
        if consultation_request_id is not None and method == "POST":
            return self._retry_consultation_request(
                connection,
                identity,
                consultation_request_id,
                payload,
            )
        if (
            resource_path == "intelligence/external-capture/commit"
            and method == "POST"
        ):
            return self._commit_external_capture(
                connection,
                identity,
                payload=payload,
            )
        candidate_id = _path_id(
            resource_path,
            "topics/candidates",
            "/promote-tasks",
        )
        if candidate_id is not None:
            phase = _text(payload.get("phase")).lower()
            if phase == "prepare":
                candidate = self._intelligence_row(
                    connection,
                    identity,
                    candidate_id,
                )
                if str(candidate["record_kind"]) != "topic_candidate":
                    raise RepositoryError(
                        422,
                        "task_promotion_source_invalid",
                        "只有议题候选可以批量晋升为任务",
                    )
                _require_expected(payload, int(candidate["version"]))
                raw_tasks = payload.get("tasks") or []
                if not isinstance(raw_tasks, list) or not raw_tasks:
                    raise RepositoryError(
                        422,
                        "task_promotion_items_required",
                        "请选择至少一个任务草案",
                    )
                tasks: list[dict[str, Any]] = []
                for raw in raw_tasks:
                    if not isinstance(raw, Mapping) or not _text(raw.get("title")):
                        raise RepositoryError(
                            422,
                            "task_promotion_item_invalid",
                            "每个任务草案都必须包含标题",
                        )
                    tasks.append(
                        {
                            "title": _text(raw.get("title"))[:120],
                            "draftHash": _text(raw.get("draftHash")),
                        }
                    )
                bulk_operation_id = new_id()
                operation_id = new_id()
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO bulk_operations (
                        bulk_operation_id, scope_id, organization_id,
                        operation_id, preflight_snapshot_hash,
                        atomicity_mode, status, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'per_item', 'accepted', 1, ?, ?)
                    """,
                    (
                        bulk_operation_id,
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                        sha256_text(
                            canonical_json(
                                {
                                    "candidateId": candidate_id,
                                    "candidateVersion": int(candidate["version"]),
                                    "tasks": tasks,
                                }
                            )
                        ),
                        now,
                        now,
                    ),
                )
                for index, task in enumerate(tasks):
                    connection.execute(
                        """
                        INSERT INTO bulk_operation_items (
                            bulk_item_id, bulk_operation_id, item_key,
                            preflight_result, commit_result, conflict_code,
                            result_json
                        ) VALUES (?, ?, ?, 'ready', NULL, NULL, ?)
                        """,
                        (
                            new_id(),
                            bulk_operation_id,
                            str(index),
                            canonical_json(
                                {
                                    "title": task["title"],
                                    "draftHash": task["draftHash"],
                                    "status": "pending",
                                }
                            ),
                        ),
                    )
                return Mutation(
                    aggregate_type="task_promotion_batch",
                    aggregate_id=bulk_operation_id,
                    before_version=None,
                    after_version=1,
                    result={
                        "bulkOperationId": bulk_operation_id,
                        "candidateId": candidate_id,
                        "status": "accepted",
                        "atomicityMode": "per_item",
                        "version": 1,
                        "items": [
                            {"itemKey": str(index), "status": "pending"}
                            for index in range(len(tasks))
                        ],
                    },
                    event_type="topic_candidate.task_promotion_prepared",
                    summary={
                        "candidateId": candidate_id,
                        "itemCount": len(tasks),
                    },
                    outbox_payload={
                        "candidateId": candidate_id,
                        "bulkOperationId": bulk_operation_id,
                    },
                    operation_id=operation_id,
                )
            if phase == "finalize":
                bulk_operation_id = _text(payload.get("bulkOperationId"))
                bulk = connection.execute(
                    """
                    SELECT operation.*, command.actor_principal_id
                    FROM bulk_operations AS operation
                    JOIN command_envelopes AS command
                      ON command.operation_id = operation.operation_id
                    WHERE operation.bulk_operation_id = ?
                      AND operation.organization_id = ?
                      AND command.actor_principal_id = ?
                    """,
                    (
                        bulk_operation_id,
                        identity.organization_id,
                        identity.principal_id,
                    ),
                ).fetchone()
                if bulk is None:
                    raise RepositoryError(
                        404,
                        "task_promotion_batch_missing",
                        "任务晋升批次不存在或不可见",
                    )
                _require_expected(payload, int(bulk["version"]))
                if str(bulk["status"]) != "accepted":
                    raise RepositoryError(
                        409,
                        "task_promotion_batch_not_pending",
                        "任务晋升批次已完成，请读取已有回执",
                    )
                rows = connection.execute(
                    """
                    SELECT item_key FROM bulk_operation_items
                    WHERE bulk_operation_id = ?
                    ORDER BY CAST(item_key AS INTEGER), item_key
                    """,
                    (bulk_operation_id,),
                ).fetchall()
                raw_results = payload.get("itemResults") or []
                if not isinstance(raw_results, list):
                    raise RepositoryError(
                        422,
                        "task_promotion_results_invalid",
                        "逐项执行回执格式无效",
                    )
                results = {
                    _text(item.get("itemKey")): dict(item)
                    for item in raw_results
                    if isinstance(item, Mapping)
                    and _text(item.get("itemKey"))
                }
                expected_keys = {str(row["item_key"]) for row in rows}
                if set(results) != expected_keys:
                    raise RepositoryError(
                        422,
                        "task_promotion_results_incomplete",
                        "逐项执行回执与预检项目不一致",
                    )
                succeeded = 0
                finalized_items: list[dict[str, Any]] = []
                for item_key in sorted(expected_keys, key=int):
                    raw_result = results[item_key]
                    committed = (
                        _text(raw_result.get("status")).lower() == "committed"
                        and bool(_text(raw_result.get("taskId")))
                    )
                    if committed:
                        succeeded += 1
                    result = {
                        "itemKey": item_key,
                        "status": "committed" if committed else "failed",
                        "taskId": (
                            _text(raw_result.get("taskId"))
                            if committed
                            else None
                        ),
                        "errorCode": (
                            None
                            if committed
                            else _text(raw_result.get("errorCode"))
                            or "task_creation_failed"
                        ),
                    }
                    connection.execute(
                        """
                        UPDATE bulk_operation_items
                        SET commit_result = ?, conflict_code = ?,
                            result_json = ?
                        WHERE bulk_operation_id = ? AND item_key = ?
                        """,
                        (
                            result["status"],
                            result["errorCode"],
                            canonical_json(result),
                            bulk_operation_id,
                            item_key,
                        ),
                    )
                    finalized_items.append(result)
                status = (
                    "committed"
                    if succeeded == len(finalized_items)
                    else "rejected"
                    if succeeded == 0
                    else "partial"
                )
                now = utc_now()
                updated = connection.execute(
                    """
                    UPDATE bulk_operations
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE bulk_operation_id = ? AND version = ?
                    """,
                    (
                        status,
                        now,
                        bulk_operation_id,
                        int(bulk["version"]),
                    ),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "version_conflict",
                        "任务晋升批次版本已变化",
                    )
                return Mutation(
                    aggregate_type="task_promotion_batch",
                    aggregate_id=bulk_operation_id,
                    before_version=int(bulk["version"]),
                    after_version=int(bulk["version"]) + 1,
                    result={
                        "bulkOperationId": bulk_operation_id,
                        "candidateId": candidate_id,
                        "status": status,
                        "atomicityMode": "per_item",
                        "version": int(bulk["version"]) + 1,
                        "total": len(finalized_items),
                        "succeeded": succeeded,
                        "failed": len(finalized_items) - succeeded,
                        "items": finalized_items,
                    },
                    event_type="topic_candidate.task_promotion_finalized",
                    summary={
                        "candidateId": candidate_id,
                        "status": status,
                        "succeeded": succeeded,
                        "failed": len(finalized_items) - succeeded,
                    },
                    outbox_payload={
                        "candidateId": candidate_id,
                        "bulkOperationId": bulk_operation_id,
                        "status": status,
                    },
                )
            raise RepositoryError(
                422,
                "task_promotion_phase_invalid",
                "任务晋升必须明确 prepare 或 finalize 阶段",
            )
        for prefix, suffix, state, event in (
            ("approvals", "/approve", "accepted", "approval.approved"),
            ("approvals", "/reject", "returned", "approval.rejected"),
            (
                "data-center/proposal-drafts",
                "/mark-reviewed",
                "inbox",
                "proposal_draft.reviewed",
            ),
            (
                "data-center/proposal-drafts",
                "/promote",
                "accepted",
                "proposal_draft.promoted",
            ),
            (
                "data-center/proposal-drafts",
                "/reject",
                "returned",
                "proposal_draft.rejected",
            ),
            (
                "external-evidence-cards",
                "/accept",
                "accepted",
                "external_evidence.accepted",
            ),
            (
                "external-evidence-cards",
                "/reject",
                "returned",
                "external_evidence.rejected",
            ),
            (
                "intelligence/items",
                "/dismiss",
                "archived",
                "intelligence.archived",
            ),
            (
                "intelligence/items",
                "/follow",
                "accepted",
                "intelligence.accepted",
            ),
            ("proposals", "/approve", "accepted", "proposal.approved"),
            ("proposals", "/reject", "returned", "proposal.rejected"),
            (
                "strategic/thoughts",
                "/review",
                "accepted",
                "strategic_thought.reviewed",
            ),
            (
                "strategic/thoughts",
                "/state",
                _text(payload.get("state")) or "inbox",
                "strategic_thought.state_changed",
            ),
            (
                "topics/candidates",
                "",
                "archived",
                "topic_candidate.archived",
            ),
        ):
            resource_id = _path_id(resource_path, prefix, suffix)
            if resource_id is not None:
                if method == "DELETE" and prefix != "topics/candidates":
                    continue
                if prefix in {"approvals", "proposals"}:
                    _require_admin(identity)
                return self._update_intelligence_status(
                    connection,
                    identity,
                    intelligence_id=resource_id,
                    payload=payload,
                    target_status=state,
                    event_type=event,
                )
        signal_id = _path_id(resource_path, "growth/pending-captures", "/state")
        if signal_id is not None:
            requested = _text(payload.get("state") or payload.get("status")).lower()
            state = {
                "accept": "confirmed",
                "accepted": "confirmed",
                "confirm": "confirmed",
                "confirmed": "confirmed",
                "dismiss": "revoked",
                "reject": "revoked",
                "rejected": "revoked",
                "revoked": "revoked",
                "candidate": "candidate",
            }.get(requested)
            if state is None:
                raise RepositoryError(422, "growth_state_invalid", "成长确认状态无效")
            return self._mutate_growth(
                connection,
                identity,
                table="growth_signals",
                id_column="growth_signal_id",
                resource_id=signal_id,
                state_column="lifecycle_state",
                target_state=state,
                payload=payload,
                aggregate_type="growth_signal",
                event_type=f"growth_signal.{state}",
            )
        for suffix, state in (("/accept", "confirmed"), ("/dismiss", "rejected")):
            evidence_id = _path_id(resource_path, "growth/recommendations", suffix)
            if evidence_id is not None:
                return self._mutate_growth(
                    connection,
                    identity,
                    table="growth_evidence",
                    id_column="growth_evidence_id",
                    resource_id=evidence_id,
                    state_column="validation_state",
                    target_state=state,
                    payload=payload,
                    aggregate_type="growth_evidence",
                    event_type=f"growth_evidence.{state}",
                )
        quote_id = _path_id(resource_path, "growth/experience-wall", "/like")
        if quote_id is not None:
            return self._mutate_reaction(
                connection,
                identity,
                quote_id=quote_id,
                reaction_type="like",
            )
        quote_id = _path_id(resource_path, "growth/handbook", "/mark-reused")
        if quote_id is not None:
            return self._mutate_reaction(
                connection,
                identity,
                quote_id=quote_id,
                reaction_type="save",
            )
        evidence_id = _path_id(
            resource_path,
            "data-center/evidence-quality",
            "/label",
        )
        if evidence_id is not None:
            label = _text(
                payload.get("label")
                or payload.get("validationState")
                or payload.get("state")
            ).lower()
            target = {
                "accept": "confirmed",
                "accepted": "confirmed",
                "confirmed": "confirmed",
                "valid": "confirmed",
                "useful": "confirmed",
                "reject": "rejected",
                "rejected": "rejected",
                "invalid": "rejected",
                "noise": "rejected",
                "candidate": "candidate",
                "needs_review": "candidate",
                "revoke": "revoked",
                "revoked": "revoked",
            }.get(label)
            if target is None:
                raise RepositoryError(
                    422,
                    "evidence_quality_label_invalid",
                    "证据质量标签无效",
                )
            return self._mutate_growth(
                connection,
                identity,
                table="growth_evidence",
                id_column="growth_evidence_id",
                resource_id=evidence_id,
                state_column="validation_state",
                target_state=target,
                payload=payload,
                aggregate_type="growth_evidence",
                event_type=f"growth_evidence.{target}",
            )
        source_id = _path_id(
            resource_path,
            "topic-candidates",
            "/external-evidence-card",
        )
        if source_id is not None:
            source = self._intelligence_row(connection, identity, source_id)
            _require_expected(payload, int(source["version"]))
            return self._insert_intelligence(
                connection,
                identity,
                title=_text(payload.get("title")) or str(source["title"]),
                summary=_text(payload.get("summary")) or str(source["summary"]),
                record_kind="external_evidence_card",
                project_id=source["project_id"],
                source_url=str(source["source_url"]),
                source_payload={
                    "sourceType": "topic_candidate",
                    "sourceId": source_id,
                    "sourceVersion": source["version"],
                    "evidenceRefs": payload.get("evidenceRefs") or [],
                },
                event_type="external_evidence.created",
            )
        source_id = _path_id(
            resource_path,
            "external-evidence-cards",
            "/create-proposal-draft",
        )
        if source_id is not None:
            source = self._intelligence_row(connection, identity, source_id)
            _require_expected(payload, int(source["version"]))
            return self._insert_intelligence(
                connection,
                identity,
                title=_text(payload.get("title")) or str(source["title"]),
                summary=_text(payload.get("summary")) or str(source["summary"]),
                record_kind="proposal_draft",
                project_id=source["project_id"],
                source_url=str(source["source_url"]),
                source_payload={
                    "sourceType": "external_evidence_card",
                    "sourceId": source_id,
                    "sourceVersion": source["version"],
                    "taskDrafts": payload.get("taskDrafts") or [],
                    "evidenceRefs": payload.get("evidenceRefs") or [],
                },
                event_type="proposal_draft.created",
            )
        if resource_path in {
            "intelligence/focus-directives",
            "intelligence/verification-rules",
            "intelligence/refresh-cycle-settings",
            "intelligence/sentiment/feedback",
            "intelligence/verification-feedback",
        }:
            _require_admin(identity)
            kind = {
                "intelligence/focus-directives": "focus_directive",
                "intelligence/verification-rules": "verification_rule",
                "intelligence/refresh-cycle-settings": "refresh_cycle_setting",
                "intelligence/sentiment/feedback": "sentiment_feedback",
                "intelligence/verification-feedback": "verification_feedback",
            }[resource_path]
            title = (
                _text(payload.get("title"))
                or _text(payload.get("name"))
                or {
                    "focus_directive": "重点关注",
                    "verification_rule": "核验规则",
                    "refresh_cycle_setting": "刷新周期",
                    "sentiment_feedback": "舆情反馈",
                    "verification_feedback": "情报核验反馈",
                }[kind]
            )
            return self._insert_intelligence(
                connection,
                identity,
                title=title,
                summary=_text(payload.get("summary"))
                or _text(payload.get("content"))
                or _text(payload.get("feedback")),
                record_kind=kind,
                project_id=_text(payload.get("projectId")) or None,
                source_payload=dict(payload),
                status="accepted",
                event_type=f"{kind}.saved",
            )
        profile_id = _path_id(resource_path, "intelligence/profiles")
        if profile_id is not None and method == "PATCH":
            row = self._intelligence_row(connection, identity, profile_id)
            _require_admin(identity)
            before = int(row["version"])
            _require_expected(payload, before)
            after = before + 1
            previous_payload = _json(row["source_payload_json"], {})
            merged_payload = {**previous_payload, **dict(payload)}
            merged_payload.pop("expectedVersion", None)
            title = (
                _text(payload.get("title"))
                or _text(payload.get("name"))
                or str(row["title"])
            )
            summary = (
                _text(payload.get("summary"))
                if "summary" in payload
                else _text(payload.get("description"))
                if "description" in payload
                else str(row["summary"])
            )
            now = utc_now()
            connection.execute(
                """
                UPDATE intelligence_records
                SET title = ?, summary = ?, source_payload_json = ?,
                    version = ?, updated_at = ?
                WHERE intelligence_id = ? AND organization_id = ? AND version = ?
                """,
                (
                    title,
                    summary,
                    canonical_json(merged_payload),
                    after,
                    now,
                    profile_id,
                    identity.organization_id,
                    before,
                ),
            )
            connection.execute(
                """
                INSERT INTO intelligence_revisions (
                    intelligence_revision_id, organization_id, intelligence_id,
                    revision, title, summary, revised_by_membership_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    profile_id,
                    after,
                    title,
                    summary,
                    identity.membership_id,
                    now,
                ),
            )
            return Mutation(
                aggregate_type="intelligence_profile",
                aggregate_id=profile_id,
                before_version=before,
                after_version=after,
                result={
                    "id": profile_id,
                    "name": title,
                    "description": summary,
                    "version": after,
                    **merged_payload,
                },
                event_type="intelligence_profile.updated",
            )
        if resource_path == "data-center/evidence-quality/snapshots":
            return self._create_evidence_snapshot(connection, identity)
        if resource_path == "data-center/resolve":
            return self._resolve_data_center(connection, identity)
        if resource_path == "approvals/decide":
            _require_admin(identity)
            approval_id = _text(
                payload.get("approvalId")
                or payload.get("proposalId")
                or payload.get("id")
            )
            if not approval_id:
                raise RepositoryError(422, "approval_id_required", "请选择审批对象")
            decision = _text(payload.get("decision") or payload.get("action")).lower()
            target = (
                "accepted"
                if decision in {"approve", "approved", "accept"}
                else "returned"
                if decision in {"reject", "rejected", "return"}
                else None
            )
            if target is None:
                raise RepositoryError(422, "approval_decision_invalid", "审批决定无效")
            return self._update_intelligence_status(
                connection,
                identity,
                intelligence_id=approval_id,
                payload=payload,
                target_status=target,
                event_type=(
                    "approval.approved" if target == "accepted" else "approval.rejected"
                ),
            )
        if resource_path in {
            "proposals/batch-approve",
            "proposals/batch-reject",
        }:
            _require_admin(identity)
            raw_ids = (
                payload.get("proposalIds")
                or payload.get("ids")
                or payload.get("selectedIds")
                or []
            )
            proposal_ids = [
                _text(item.get("id") if isinstance(item, Mapping) else item)
                for item in raw_ids
                if _text(item.get("id") if isinstance(item, Mapping) else item)
            ]
            proposal_ids = list(dict.fromkeys(proposal_ids))
            if not proposal_ids:
                raise RepositoryError(
                    422,
                    "bulk_proposal_ids_required",
                    "请选择至少一个提案",
                )
            item_versions = payload.get("itemVersions")
            if not isinstance(item_versions, Mapping):
                item_versions = {}
            rows = []
            for proposal_id in proposal_ids:
                row = self._intelligence_row(connection, identity, proposal_id)
                if str(row["record_kind"]) not in PROPOSAL_KINDS:
                    raise RepositoryError(
                        422,
                        "bulk_item_not_proposal",
                        f"{proposal_id} 不是提案对象",
                    )
                expected = item_versions.get(proposal_id)
                _require_expected(
                    {"expectedVersion": expected},
                    int(row["version"]),
                )
                rows.append(row)
            target = (
                "accepted"
                if resource_path == "proposals/batch-approve"
                else "returned"
            )
            children = [
                self._update_intelligence_status(
                    connection,
                    identity,
                    intelligence_id=str(row["intelligence_id"]),
                    payload={"expectedVersion": int(row["version"])},
                    target_status=target,
                    event_type=(
                        "proposal.approved"
                        if target == "accepted"
                        else "proposal.rejected"
                    ),
                )
                for row in rows
            ]
            bulk_id = new_id()
            return Mutation(
                aggregate_type="bulk_operation",
                aggregate_id=bulk_id,
                before_version=None,
                after_version=1,
                result={
                    "bulkOperationId": bulk_id,
                    "status": "committed",
                    "atomicityMode": "all_or_nothing",
                    "total": len(children),
                    "succeeded": len(children),
                    "failed": 0,
                    "failedIds": [],
                    "items": [
                        {
                            "id": child.aggregate_id,
                            "beforeVersion": child.before_version,
                            "afterVersion": child.after_version,
                            "status": child.result["status"],
                        }
                        for child in children
                    ],
                },
                event_type=(
                    "proposal.batch_approved"
                    if target == "accepted"
                    else "proposal.batch_rejected"
                ),
                summary={"itemCount": len(children), "status": target},
                children=children,
            )
        proposal_id: str | None = None
        queue_execution = False
        for suffix in ("/execution-ticket", "/execute"):
            proposal_id = _path_id(resource_path, "proposals", suffix)
            if proposal_id is not None:
                queue_execution = suffix == "/execute"
                break
        if proposal_id is not None:
            _require_admin(identity)
            row = self._intelligence_row(connection, identity, proposal_id)
            if str(row["record_kind"]) not in PROPOSAL_KINDS:
                raise RepositoryError(422, "resource_not_proposal", "对象不是提案")
            _require_expected(payload, int(row["version"]))
            if str(row["status"]) != "accepted":
                raise RepositoryError(
                    409,
                    "proposal_not_approved",
                    "提案批准后才能创建执行票据",
                )
            source_payload = _json(row["source_payload_json"], {})
            if not source_payload.get("taskDrafts"):
                raise RepositoryError(
                    409,
                    "proposal_has_no_task_drafts",
                    "提案没有可执行任务草案",
                )
            ticket_id = new_id()
            created_task_ids: list[str] = []
            task_mutations: list[Mutation] = []
            proposal_execution: Mutation | None = None
            proposal_result_row = row
            if queue_execution:
                proposal_execution = self._mark_proposal_executed(
                    connection,
                    identity,
                    proposal_row=row,
                    ticket_id=ticket_id,
                )
                created_task_ids, task_mutations = (
                    self._execute_proposal_task_drafts(
                        connection,
                        identity,
                        proposal_row=row,
                        execution_source_id=ticket_id,
                    )
                )
                proposal_result_row = self._intelligence_row(
                    connection,
                    identity,
                    proposal_id,
                )
            return Mutation(
                aggregate_type="execution_ticket",
                aggregate_id=ticket_id,
                before_version=int(row["version"]),
                after_version=int(row["version"]),
                result={
                    "proposal": self._proposal_from_row(proposal_result_row),
                    "createdTaskIds": created_task_ids,
                },
                event_type=(
                    "proposal.execution_completed"
                    if queue_execution
                    else "proposal.execution_ticket.created"
                ),
                summary={
                    "proposalId": proposal_id,
                    "sourceVersion": row["version"],
                    "queueExecution": queue_execution,
                },
                outbox_payload={
                    "proposalId": proposal_id,
                    "createdTaskIds": created_task_ids,
                },
                children=[
                    *([proposal_execution] if proposal_execution else []),
                    *task_mutations,
                ],
            )
        for suffix in ("/execute", "/retry"):
            ticket_id = _path_id(resource_path, "execution-tickets", suffix)
            if ticket_id is None:
                continue
            _require_admin(identity)
            ticket = connection.execute(
                """
                SELECT * FROM command_envelopes
                WHERE command_id = ? AND organization_id = ?
                  AND aggregate_type = 'execution_ticket'
                """,
                (ticket_id, identity.organization_id),
            ).fetchone()
            if ticket is None:
                raise RepositoryError(
                    404,
                    "execution_ticket_not_found",
                    "执行票据不存在",
                )
            ticket_payload = _json(ticket["payload_json"], {})
            ticket_path = _text(ticket_payload.get("resourcePath"))
            proposal_match = re.fullmatch(
                r"proposals/([^/]+)/(?:execute|execution-ticket)",
                ticket_path,
            )
            proposal_id = (
                _text(ticket_payload.get("proposalId"))
                or (proposal_match.group(1) if proposal_match else "")
            )
            if not proposal_id:
                raise RepositoryError(
                    409,
                    "execution_ticket_source_invalid",
                    "执行票据缺少可核验的提案来源",
                )
            proposal_row = self._intelligence_row(
                connection,
                identity,
                proposal_id,
            )
            if str(proposal_row["status"]) != "accepted":
                raise RepositoryError(
                    409,
                    "proposal_not_approved",
                    "提案批准后才能执行",
                )
            proposal_execution = self._mark_proposal_executed(
                connection,
                identity,
                proposal_row=proposal_row,
                ticket_id=ticket_id,
            )
            task_ids, task_mutations = self._execute_proposal_task_drafts(
                connection,
                identity,
                proposal_row=proposal_row,
                execution_source_id=ticket_id,
            )
            latest_attempt = connection.execute(
                """
                SELECT COALESCE(MAX(attempt_no), 0)
                FROM operation_attempts
                WHERE command_id = ?
                """,
                (ticket_id,),
            ).fetchone()
            attempt_no = int(latest_attempt[0]) + 1
            now = utc_now()
            connection.execute(
                """
                INSERT INTO operation_attempts (
                    attempt_id, scope_id, command_id, attempt_no,
                    transport_state, lease_owner, lease_until,
                    permission_revalidated_at, next_retry_at, error_code,
                    error_message, created_at
                ) VALUES (?, ?, ?, ?, 'delivered', NULL, NULL, ?, NULL,
                          NULL, NULL, ?)
                """,
                (
                    new_id(),
                    identity.scope_id,
                    ticket_id,
                    attempt_no,
                    now,
                    now,
                ),
            )
            return Mutation(
                aggregate_type="execution_ticket_execution",
                aggregate_id=ticket_id,
                before_version=attempt_no - 1,
                after_version=attempt_no,
                result={
                    "executionTicket": {
                        "id": ticket_id,
                        "proposalId": proposal_id,
                        "clientId": proposal_row["project_id"],
                        "executionType": "proposal_tasks",
                        "status": "executed",
                        "result": {
                            "resultType": "tasks_created",
                            "summary": f"已创建 {len(task_ids)} 条严格任务",
                            "createdTaskIds": task_ids,
                            "artifactRefs": [],
                        },
                        "executedAt": now,
                    }
                },
                event_type="proposal.execution_completed",
                summary={
                    "ticketId": ticket_id,
                    "proposalId": proposal_id,
                    "createdTaskCount": len(task_ids),
                },
                outbox_payload={
                    "ticketId": ticket_id,
                    "proposalId": proposal_id,
                    "createdTaskIds": task_ids,
                },
                children=[proposal_execution, *task_mutations],
            )
        if (
            resource_path == "intelligence/brand-mirror/strategy-extract"
            and method == "PUT"
        ):
            client_id = _text(payload.get("clientId"))
            project_row = self._require_project_editor(
                connection,
                identity,
                client_id,
            )
            strategic_objective = _text(payload.get("strategicObjective"))
            methodology = _text(payload.get("methodology"))
            if not strategic_objective or not methodology:
                raise RepositoryError(
                    422,
                    "strategy_extract_content_required",
                    "战略主张和方法学都不能为空",
                )
            if len(strategic_objective) + len(methodology) > 200:
                raise RepositoryError(
                    422,
                    "strategy_extract_content_too_long",
                    "战略主张和方法学合计不能超过 200 字",
                )

            rows = connection.execute(
                """
                SELECT n.*, v.content_json
                FROM narrative_outputs AS n
                JOIN narrative_output_versions AS v
                  ON v.narrative_output_id = n.narrative_output_id
                 AND v.version = n.latest_version
                WHERE n.organization_id = ?
                  AND n.output_kind = 'strategy_report'
                  AND n.lifecycle_state != 'archived'
                ORDER BY n.updated_at DESC, n.narrative_output_id
                """,
                (identity.organization_id,),
            ).fetchall()
            row = next(
                (
                    item
                    for item in rows
                    if _text(_json(item["content_json"], {}).get("clientId"))
                    == client_id
                ),
                None,
            )
            before = int(row["version"]) if row is not None else 0
            _require_expected(payload, before)
            after = before + 1
            now = utc_now()

            previous = (
                _json(row["content_json"], {})
                if row is not None
                else self._derived_brand_strategy_extract(
                    identity,
                    client_id=client_id,
                )
            )
            if not isinstance(previous, dict):
                previous = {}
            extract = {
                **self._derived_brand_strategy_extract(
                    identity,
                    client_id=client_id,
                ),
                **previous,
                "clientId": client_id,
                "strategicObjective": strategic_objective,
                "methodology": methodology,
                "confirmedBy": identity.display_name or identity.membership_id,
                "confirmedAt": now,
                "isStale": False,
            }
            content_json = canonical_json(extract)
            content_markdown = (
                f"# 战略主张\n\n{strategic_objective}\n\n"
                f"# 方法学\n\n{methodology}\n"
            )
            content_hash = sha256_text(content_json)

            if row is None:
                narrative_output_id = new_id()
                connection.execute(
                    """
                    INSERT INTO narrative_outputs (
                        narrative_output_id, organization_id, project_id,
                        event_line_id, output_kind, title, lifecycle_state,
                        latest_version, created_by_membership_id, version,
                        created_at, updated_at, archived_at
                    ) VALUES (
                        ?, ?, ?, NULL, 'strategy_report', '品牌战略提炼',
                        'active', 1, ?, 1, ?, ?, NULL
                    )
                    """,
                    (
                        narrative_output_id,
                        identity.organization_id,
                        str(project_row["project_id"]),
                        identity.membership_id,
                        now,
                        now,
                    ),
                )
                narrative_version = 1
            else:
                narrative_output_id = str(row["narrative_output_id"])
                narrative_version = int(row["latest_version"]) + 1
                updated = connection.execute(
                    """
                    UPDATE narrative_outputs
                    SET lifecycle_state = 'active', latest_version = ?,
                        version = ?, updated_at = ?
                    WHERE narrative_output_id = ? AND organization_id = ?
                      AND version = ?
                    """,
                    (
                        narrative_version,
                        after,
                        now,
                        narrative_output_id,
                        identity.organization_id,
                        before,
                    ),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "version_conflict",
                        "战略提炼版本已变化，请重新读取后提交",
                    )
            connection.execute(
                """
                INSERT INTO narrative_output_versions (
                    narrative_output_version_id, organization_id,
                    narrative_output_id, version, content_markdown,
                    content_json, input_fingerprint, content_hash,
                    change_summary, created_by_membership_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    narrative_output_id,
                    narrative_version,
                    content_markdown,
                    content_json,
                    payload_fingerprint(
                        {
                            "clientId": client_id,
                            "sourceStrategyMdHash": extract[
                                "sourceStrategyMdHash"
                            ],
                            "sourceMethodologyMdHash": extract[
                                "sourceMethodologyMdHash"
                            ],
                        }
                    ),
                    content_hash,
                    "用户确认品牌战略提炼",
                    identity.membership_id,
                    now,
                ),
            )
            return Mutation(
                aggregate_type="narrative_output",
                aggregate_id=narrative_output_id,
                before_version=before if row is not None else None,
                after_version=after,
                result={"extract": extract},
                event_type="strategy_extract.saved",
                summary={
                    "clientId": client_id,
                    "narrativeVersion": narrative_version,
                    "contentHash": content_hash,
                },
                outbox_payload={
                    "clientId": client_id,
                    "narrativeOutputId": narrative_output_id,
                    "narrativeVersion": narrative_version,
                },
            )
        if resource_path == "topics/radars" and method == "POST":
            title = _text(payload.get("title"))
            prompt = _text(payload.get("prompt"))
            if not title or not prompt:
                raise RepositoryError(
                    422,
                    "topic_radar_content_required",
                    "雷达标题和关注方向不能为空",
                )
            preferred_sources = [
                {
                    "url": _text(item.get("url")),
                    "label": _text(item.get("label")),
                }
                for item in payload.get("preferredSources") or []
                if isinstance(item, Mapping) and _text(item.get("url"))
            ]
            mutation = self._insert_intelligence(
                connection,
                identity,
                title=title,
                summary=prompt,
                record_kind="topic_radar",
                project_id=None,
                source_payload={
                    "timeRange": _text(payload.get("timeRange")) or "7d",
                    "preferredSources": preferred_sources,
                },
                status="accepted",
                event_type="topic_radar.created",
            )
            mutation.result = {
                "id": mutation.aggregate_id,
                "title": title,
                "prompt": prompt,
                "timeRange": _text(payload.get("timeRange")) or "7d",
                "preferredSources": preferred_sources,
                "createdAt": mutation.result["createdAt"],
                "updatedAt": mutation.result["updatedAt"],
                "version": 1,
            }
            return mutation
        radar_match = re.fullmatch(r"topics/radars/([^/]+)", resource_path)
        if radar_match and method in {"PUT", "DELETE"}:
            radar_id = radar_match.group(1)
            row = self._intelligence_row(connection, identity, radar_id)
            if str(row["record_kind"]) != "topic_radar":
                raise RepositoryError(404, "topic_radar_missing", "情报雷达不存在")
            if method == "DELETE":
                mutation = self._update_intelligence_payload(
                    connection,
                    identity,
                    intelligence_id=radar_id,
                    payload=payload,
                    status="archived",
                    event_type="topic_radar.archived",
                )
                mutation.result = {"deleted": True, "id": radar_id}
                return mutation
            title = _text(payload.get("title"))
            prompt = _text(payload.get("prompt"))
            if not title or not prompt:
                raise RepositoryError(
                    422,
                    "topic_radar_content_required",
                    "雷达标题和关注方向不能为空",
                )
            mutation = self._update_intelligence_payload(
                connection,
                identity,
                intelligence_id=radar_id,
                payload=payload,
                title=title,
                summary=prompt,
                source_payload={
                    "timeRange": _text(payload.get("timeRange")) or "7d",
                    "preferredSources": [
                        {
                            "url": _text(item.get("url")),
                            "label": _text(item.get("label")),
                        }
                        for item in payload.get("preferredSources") or []
                        if isinstance(item, Mapping) and _text(item.get("url"))
                    ],
                },
                event_type="topic_radar.updated",
            )
            source = mutation.result["sourcePayload"]
            mutation.result = {
                "id": radar_id,
                "title": title,
                "prompt": prompt,
                "timeRange": source.get("timeRange") or "7d",
                "preferredSources": source.get("preferredSources") or [],
                "createdAt": row["created_at"],
                "updatedAt": mutation.result["updatedAt"],
                "version": mutation.after_version,
            }
            return mutation
        radar_capture = re.fullmatch(
            r"topics/radars/([^/]+)/capture",
            resource_path,
        )
        if radar_capture:
            radar_id = radar_capture.group(1)
            row = self._intelligence_row(connection, identity, radar_id)
            if str(row["record_kind"]) != "topic_radar":
                raise RepositoryError(404, "topic_radar_missing", "情报雷达不存在")
            raise RepositoryError(
                409,
                "local_public_capture_required",
                (
                    "雷达公开采集必须经本机安全读取器执行，再把结构化线索"
                    "提交到组织云权威对象"
                ),
            )
        profile_match = re.fullmatch(
            r"intelligence/profiles/([^/]+)/(refresh|trial-run)",
            resource_path,
        )
        if profile_match:
            profile_id, action = profile_match.groups()
            row = self._intelligence_row(connection, identity, profile_id)
            if str(row["record_kind"]) != "intelligence_profile":
                raise RepositoryError(404, "intelligence_profile_missing", "情报画像不存在")
            project_id = _text(row["project_id"])
            fact_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM intelligence_records
                    WHERE organization_id = ?
                      AND (? = '' OR project_id = ?)
                      AND record_kind NOT IN ('intelligence_profile', 'topic_radar')
                      AND status != 'archived'
                    """,
                    (identity.organization_id, project_id, project_id),
                ).fetchone()[0]
            )
            _require_expected(payload, int(row["version"]))
            return self._operational_reconciliation(
                connection,
                identity,
                registry_state_id=f"intelligence_profile_{action}",
                report={
                    "profileId": profile_id,
                    "status": "blocked",
                    "state": "not_connected",
                    "factCount": fact_count,
                    "profileVersion": int(row["version"]),
                    "message": (
                        "画像配置可用，但组织尚未配置外部情报采集执行器"
                    ),
                },
                event_type=(
                    f"intelligence_profile.{action.replace('-', '_')}_blocked"
                ),
                mismatch_count=1,
                status="failed",
            )
        if resource_path == "intelligence/profiles/run-due":
            _require_admin(identity)
            profiles = connection.execute(
                """
                SELECT intelligence_id, project_id, source_payload_json, version
                FROM intelligence_records
                WHERE organization_id = ?
                  AND record_kind = 'intelligence_profile'
                  AND status != 'archived'
                ORDER BY updated_at, intelligence_id
                """,
                (identity.organization_id,),
            ).fetchall()
            due = [
                row
                for row in profiles
                if bool(
                    _json(row["source_payload_json"], {}).get(
                        "profileRefreshEnabled"
                    )
                )
            ]
            return self._operational_reconciliation(
                connection,
                identity,
                registry_state_id="intelligence_profile_due_run",
                report={
                    "totalProfiles": len(profiles),
                    "dueProfiles": len(due),
                    "processedCount": 0,
                    "state": (
                        "not_connected"
                        if due
                        else "completed"
                    ),
                    "message": (
                        "存在到期画像，但组织尚未配置外部采集执行器"
                        if due
                        else "当前没有需要外部采集的到期画像"
                    ),
                },
                event_type="intelligence_profiles.due_checked",
                mismatch_count=len(due),
            )
        if resource_path.startswith("data-center/kernel-primary-rollout/"):
            return self._kernel_rollout(
                connection,
                identity,
                resource_path=resource_path,
                payload=payload,
            )
        if resource_path == "data-center/schema/ensure":
            _require_admin(identity)
            schema = connection.execute(
                """
                SELECT schema_family, contract_version, manifest_hash, build_id
                FROM meta_schema_builds
                ORDER BY created_at DESC, build_id DESC LIMIT 1
                """
            ).fetchone()
            tables = [
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                ).fetchall()
            ]
            if schema is None:
                raise RepositoryError(
                    409,
                    "schema_identity_missing",
                    "严格数据库缺少 schema 身份，运行时不会创建表冒充成功",
                )
            table_set = set(tables)
            missing_tables = sorted(CLOUD_CONTRACT.allowed_tables - table_set)
            unexpected_tables = sorted(table_set - CLOUD_CONTRACT.allowed_tables)
            column_errors: list[str] = []
            for table_name, required_keys in CLOUD_CONTRACT.required_keys.items():
                if table_name not in table_set:
                    continue
                columns = {
                    str(row["name"])
                    for row in connection.execute(
                        f'PRAGMA table_info("{table_name}")'
                    ).fetchall()
                }
                missing_keys = sorted(required_keys - columns)
                if missing_keys:
                    column_errors.append(
                        f"{table_name}:missing={','.join(missing_keys)}"
                    )
            identity_errors = []
            if str(schema["schema_family"]) != CLOUD_CONTRACT.schema_family:
                identity_errors.append("schema_family")
            if str(schema["contract_version"]) != CLOUD_CONTRACT.contract_version:
                identity_errors.append("contract_version")
            if str(schema["manifest_hash"]) != CLOUD_CONTRACT.manifest_hash:
                identity_errors.append("manifest_hash")
            errors = [
                *[f"unexpected_table:{value}" for value in unexpected_tables],
                *column_errors,
                *[f"identity:{value}" for value in identity_errors],
            ]
            if missing_tables or errors:
                raise RepositoryError(
                    409,
                    "strict_schema_drift",
                    "严格数据库结构或身份与冻结 manifest 不一致",
                )
            return Mutation(
                aggregate_type="schema_build",
                aggregate_id=str(schema["build_id"]),
                before_version=int(schema["contract_version"]),
                after_version=int(schema["contract_version"]),
                result={
                    "generatedAt": utc_now(),
                    "ensuredTables": tables,
                    "missingTables": missing_tables,
                    "errors": errors,
                    "schemaFamily": schema["schema_family"],
                    "contractVersion": schema["contract_version"],
                    "manifestHash": schema["manifest_hash"],
                    "ddlExecuted": False,
                },
                event_type="strict_schema.verified",
                summary={
                    "contractVersion": schema["contract_version"],
                    "tableCount": len(tables),
                    "ddlExecuted": False,
                },
            )
        if resource_path == "data-center/rollback-drill":
            _require_admin(identity)
            recovery = connection.execute(
                """
                SELECT r.*, b.storage_location, b.content_hash
                FROM recovery_sets r
                JOIN backup_catalog b
                  ON b.recovery_set_id = r.recovery_set_id
                WHERE r.status IN ('created', 'verified', 'restored')
                  AND b.status = 'available' AND b.verified = 1
                ORDER BY r.created_at DESC, b.created_at DESC LIMIT 1
                """
            ).fetchone()
            if recovery is None:
                raise RepositoryError(
                    409,
                    "verified_recovery_set_required",
                    "没有可验证的 recovery set，不能把未执行的回滚演练显示为通过",
                )
            source_path = Path(str(recovery["storage_location"]))
            if not source_path.is_file():
                raise RepositoryError(
                    409,
                    "recovery_backup_missing",
                    "恢复集登记的数据库备份文件不存在",
                )
            digest = hashlib.sha256()
            with source_path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            source_hash = digest.hexdigest()
            if (
                source_hash != str(recovery["content_hash"])
                or source_hash != str(recovery["database_hash"])
            ):
                raise RepositoryError(
                    409,
                    "recovery_backup_hash_mismatch",
                    "恢复集登记哈希与实际数据库备份不一致",
                )
            with tempfile.TemporaryDirectory(
                prefix="yiyu-strict-rollback-drill-"
            ) as temporary_dir:
                restored_path = Path(temporary_dir) / "restored.db"
                shutil.copy2(source_path, restored_path)
                with sqlite3.connect(restored_path) as restored:
                    quick_check = str(
                        restored.execute("PRAGMA quick_check").fetchone()[0]
                    )
                    foreign_key_violations = len(
                        restored.execute("PRAGMA foreign_key_check").fetchall()
                    )
                    restored_tables = {
                        str(row[0])
                        for row in restored.execute(
                            """
                            SELECT name FROM sqlite_master
                            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                            """
                        ).fetchall()
                    }
                restored_identity = database_identity(restored_path, "cloud")
            if (
                quick_check != "ok"
                or foreign_key_violations
                or restored_tables != CLOUD_CONTRACT.allowed_tables
                or restored_identity.schema_family != CLOUD_CONTRACT.schema_family
                or restored_identity.contract_version
                != CLOUD_CONTRACT.contract_version
                or restored_identity.manifest_hash != CLOUD_CONTRACT.manifest_hash
                or str(recovery["schema_manifest_hash"])
                != CLOUD_CONTRACT.manifest_hash
            ):
                raise RepositoryError(
                    409,
                    "rollback_drill_verification_failed",
                    "恢复副本未通过完整性、外键、表清单或 schema 身份检查",
                )
            return self._operational_reconciliation(
                connection,
                identity,
                registry_state_id="rollback_drill",
                report={
                    "recoverySetId": recovery["recovery_set_id"],
                    "databaseGenerationId": recovery[
                        "database_generation_id"
                    ],
                    "manifestHash": recovery["schema_manifest_hash"],
                    "status": "verified",
                    "quickCheck": quick_check,
                    "foreignKeyViolationCount": foreign_key_violations,
                    "tableCount": len(restored_tables),
                    "restoredCopyRemoved": True,
                    "restoredProduction": False,
                },
                event_type="rollback_drill.verified",
            )
        if resource_path in {
            "data-center/team-sync/enqueue-all",
            "data-center/team-sync/run-once",
        }:
            _require_admin(identity)
            member_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM organization_memberships
                    WHERE organization_id = ? AND status = 'active'
                    """,
                    (identity.organization_id,),
                ).fetchone()[0]
            )
            return self._operational_reconciliation(
                connection,
                identity,
                registry_state_id="team_sync",
                report={
                    "mode": (
                        "enqueue_all"
                        if resource_path.endswith("enqueue-all")
                        else "run_once"
                    ),
                    "memberCount": member_count,
                    "processedCount": 0,
                    "verifiedMemberCount": member_count,
                    "state": "verified",
                    "authoritySource": "organization_memberships",
                    "externalDirectorySyncExecuted": False,
                },
                event_type=(
                    "team_authority.verified"
                    if resource_path.endswith("enqueue-all")
                    else "team_authority.reconciled"
                ),
            )
        if resource_path in {
            "intelligence/refresh",
            "topics/capture",
            "intelligence/sentiment/refresh",
        }:
            raise RepositoryError(
                409,
                "local_public_capture_required",
                (
                    "外部公开采集必须经本机安全读取器执行；"
                    "云端只接受结构化线索提交，不伪装成已抓取"
                ),
            )
        if resource_path in {
            "intelligence/sentiment/themes/recompute",
            "intelligence/sentiment/audit/recompute",
            "intelligence/brand-mirror/analyze",
            "intelligence/brand-mirror/strategy-extract",
            "strategic/thoughts/refresh",
        } and not (
            resource_path == "intelligence/brand-mirror/strategy-extract"
            and method != "POST"
        ):
            # These routes only rebuild projections from existing strict authority.
            # They never claim external collection or model execution.
            if resource_path in {
                "intelligence/brand-mirror/analyze",
                "intelligence/brand-mirror/strategy-extract",
            }:
                _require_admin(identity)
            if resource_path == "intelligence/sentiment/themes/recompute":
                scoped_query = {
                    key: _text(payload.get(key))
                    for key in ("clientId", "projectModuleId")
                    if _text(payload.get(key))
                }
                theme_view = self.query(
                    identity,
                    resource_path="intelligence/sentiment/themes",
                    query=scoped_query,
                )
                themes = list(theme_view.get("themes") or [])
                result = {"ok": bool(themes), "themes": themes}
                if not themes:
                    result["reason"] = (
                        "too_few_items: 当前范围尚无公开舆情情报事实，"
                        "请先完成真实公开采集"
                    )
            elif resource_path == "strategic/thoughts/refresh":
                result = self._strategic_thoughts_view(identity, payload)
            elif resource_path == "intelligence/brand-mirror/strategy-extract":
                self._require_visible_project(
                    identity,
                    _text(payload.get("clientId")),
                )
                result = self._derived_brand_strategy_extract(
                    identity,
                    client_id=_text(payload.get("clientId")),
                )
            elif resource_path == "intelligence/sentiment/audit/recompute":
                scope_type = (
                    "client"
                    if _text(payload.get("clientId"))
                    else "project_module"
                )
                scope_id = _text(
                    payload.get("clientId") or payload.get("projectModuleId")
                )
                audit_view = self._brand_audit_view(
                    identity,
                    scope_type=scope_type,
                    scope_id=scope_id,
                )
                result = {
                    "ok": audit_view["audit"] is not None,
                    "audit": audit_view["audit"],
                }
                if audit_view["audit"] is None:
                    result["reason"] = audit_view["recomputeNote"]
            else:
                result = {
                    "status": "verified",
                    "state": "ready",
                    "resourcePath": resource_path,
                    "externalCollectionExecuted": False,
                    "modelAnalysisExecuted": False,
                    "view": self.query(
                        identity,
                        resource_path=(
                            "topics"
                            if resource_path == "topics/capture"
                            else "strategic/thoughts"
                            if resource_path == "strategic/thoughts/refresh"
                            else "intelligence/brand-mirror/analyze"
                            if resource_path == "intelligence/brand-mirror/analyze"
                            else "intelligence/sentiment/audit"
                        ),
                        query={},
                    ),
                }
            return Mutation(
                aggregate_type="derived_view",
                aggregate_id=resource_path,
                before_version=None,
                after_version=1,
                result=result,
                event_type=f"{resource_path.replace('/', '.')}.recomputed",
                summary={
                    "derivedFromAuthority": True,
                    "externalCollectionExecuted": False,
                    "modelAnalysisExecuted": False,
                },
            )
        if resource_path in {
            "topics/radars/assist",
            "topics/radars/generate-title",
            "topics/radars/source-label",
        }:
            prompt = _text(payload.get("prompt"))
            if resource_path == "topics/radars/source-label":
                url = _text(payload.get("url"))
                hostname = re.sub(
                    r"^www\.",
                    "",
                    re.sub(r"^https?://", "", url).split("/", 1)[0],
                )
                if not hostname or "." not in hostname:
                    raise RepositoryError(
                        422,
                        "source_url_invalid",
                        "请输入有效的网页地址",
                    )
                result = {"url": url, "label": hostname}
            else:
                if not prompt:
                    raise RepositoryError(
                        422,
                        "radar_prompt_required",
                        "请输入雷达关注方向",
                    )
                title = prompt.splitlines()[0][:30]
                result = {"title": title}
                if resource_path.endswith("/assist"):
                    result.update(
                        {
                            "prompt": prompt,
                            "queries": [
                                value
                                for value in dict.fromkeys(
                                    [
                                        title,
                                        *re.findall(
                                            r"[\u4e00-\u9fffA-Za-z0-9]{2,20}",
                                            prompt,
                                        ),
                                    ]
                                )
                                if value
                            ][:6],
                        }
                    )
            return Mutation(
                aggregate_type="derived_view",
                aggregate_id=resource_path,
                before_version=None,
                after_version=1,
                result=result,
                event_type=f"{resource_path.replace('/', '.')}.generated",
                summary={"deterministic": True},
            )
        if re.fullmatch(
            r"(?:intelligence/items|topics/candidates)/[^/]+/"
            r"(?:chat|insights|task-plan|promote-tasks|task-draft|tasks)",
            resource_path,
        ):
            raise RepositoryError(
                409,
                "local_orchestration_required",
                "该动作必须由本机适配层调用组织模型或严格任务命令，云端不接收成员私有提示正文",
            )
        raise RepositoryError(
            404,
            "intelligence_growth_command_unknown",
            f"未知的情报成长命令：{method} {resource_path}",
        )

    def command(
        self,
        identity: SessionIdentity,
        *,
        resource_path: str,
        method: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        key = idempotency_key.strip()
        if not key:
            raise RepositoryError(
                422,
                "idempotency_key_required",
                "严格云命令必须携带幂等键",
            )
        safe_payload = (
            self._consultation_safe_command_payload(resource_path, payload)
            if resource_path == "consultation/knowledge-requests"
            or resource_path.startswith("consultation/knowledge-requests/")
            else dict(payload)
        )
        normalized = {
            "resourcePath": resource_path,
            "method": method,
            **safe_payload,
        }
        command_type = resource_path.replace("/", ".") + "." + method.lower()
        payload_hash = payload_fingerprint(normalized)
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                mutation = self._dispatch_mutation(
                    connection,
                    identity,
                    resource_path=resource_path,
                    method=method,
                    payload=payload,
                )
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=key,
                    normalized=normalized,
                    payload_hash=payload_hash,
                    mutation=mutation,
                )
                connection.commit()
                return mutation.result
            except Exception:
                connection.rollback()
                raise
