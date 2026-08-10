from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.security import payload_fingerprint

from ..repository import CloudRepository, RepositoryError, SessionIdentity


DNA_MODULES: dict[str, str] = {
    "organization_intro": "组织介绍",
    "business_intro": "项目介绍",
    "team_intro": "团队介绍",
    "market_intro": "市场背景介绍",
}
DNA_KIND_PREFIX = "dna_module:"
REPORT_KINDS = frozenset({"event_line_report", "weekly_report", "strategy_report"})
ORGANIZATION_LIBRARY_KINDS = frozenset(
    {
        "analysis_template",
        "fundraising_case",
        "fundraising_dna",
        "fundraising_norm",
        "fundraising_reminder",
        "handbook",
        "writing_skill",
    }
)
LIBRARY_KIND_PREFIX = "workbench_library:"
PROJECT_TEXT_KINDS = {
    "brand_proposition": "品牌主张",
    "strategic_doc:methodology": "方法论",
    "strategic_doc:strategy": "战略文档",
}
PROJECT_TEXT_KIND_PREFIX = "workbench_project_text:"
RETRIEVAL_CONFIGURATION_KIND = "workbench_retrieval_model"
PROPOSAL_RECORD_KINDS = frozenset(
    {"proposal", "proposal_draft", "external_evidence_proposal"}
)
PROPOSAL_KINDS = frozenset(
    {
        "task_prep",
        "meeting_prep",
        "meeting_followup",
        "evidence_request",
        "judgment_review",
        "context_refresh",
    }
)
ANSWER_VALUE_REVIEW_KIND = "workspace_answer_value_review"
JUDGMENT_CONTENT_KIND = "judgment_version"
DNA_DELTA_RECORD_KIND = "dna_delta"
_RETRIEVAL_REQUIRED_FIELDS = frozenset(
    {
        "embeddingProvider",
        "embeddingModel",
        "embeddingDimension",
        "embeddingMode",
        "routerEnabled",
        "routerProvider",
        "routerModel",
        "rerankEnabled",
        "rerankProvider",
        "shadowMode",
    }
)
_RETRIEVAL_FIELDS = frozenset(
    {
        *_RETRIEVAL_REQUIRED_FIELDS,
        "embeddingProfile",
        "embeddingProjection",
        "routerMode",
        "routerConfidenceThreshold",
        "rerankModel",
        "answerLayerEnabled",
        "dataCenterKernelEnabled",
        "chatKernelPrimaryEnabled",
        "chatKernelPrimaryClientAllowlist",
        "qualityGateMode",
    }
)
_FORBIDDEN_MANIFEST_KEYS = frozenset(
    {
        "path",
        "filepath",
        "file_path",
        "sourcepath",
        "sourcelocator",
        "storagekey",
        "originalpath",
        "managedpath",
    }
)


def _json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _safe_manifest(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_manifest(item)
            for key, item in value.items()
            if str(key).replace("_", "").lower() not in _FORBIDDEN_MANIFEST_KEYS
        }
    if isinstance(value, list):
        return [_safe_manifest(item) for item in value]
    return value


def _expected_version(payload: Mapping[str, Any], *, default: int | None = None) -> int:
    value = payload.get("expectedVersion", payload.get("expected_version", default))
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(422, "expected_version_required", "缺少有效的 expectedVersion") from exc
    if parsed < 1:
        raise RepositoryError(422, "expected_version_required", "缺少有效的 expectedVersion")
    return parsed


def _require_project(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    project_id: str,
) -> dict[str, Any]:
    project = repository._require_project_access(  # noqa: SLF001 - strict repository boundary
        connection,
        identity,
        project_id=project_id,
        capability="read",
    )
    return dict(project)


def _require_project_editor(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    project_id: str,
) -> dict[str, Any]:
    try:
        project = repository._require_project_access(  # noqa: SLF001
            connection,
            identity,
            project_id=project_id,
            capability="knowledge_write",
        )
    except RepositoryError as exc:
        if exc.status_code == 403:
            raise RepositoryError(
                403,
                "project_edit_forbidden",
                "无权修改该项目的产物",
            ) from exc
        raise
    return dict(project)


def _receipt(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    command_type: str,
    idempotency_key: str,
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    payload_hash = payload_fingerprint(dict(payload))
    existing = repository._task_receipt(  # noqa: SLF001 - shared strict command ledger
        connection,
        identity,
        command_type=command_type,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
    )
    return existing, payload_hash


def _record_command(
    repository: CloudRepository,
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
    audit_summary: Mapping[str, Any],
) -> None:
    now = utc_now()
    operation_id = new_id()
    payload_json = canonical_json(dict(payload))
    payload_hash = payload_fingerprint(dict(payload))
    result_json = canonical_json(dict(result))
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
    repository._insert_audit(  # noqa: SLF001 - shared strict audit chain
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
        summary=dict(audit_summary),
    )
    repository._insert_outbox(  # noqa: SLF001 - shared strict outbox
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


def _answer_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "answerId": row["ai_answer_id"],
        "projectId": row["project_id"],
        "membershipId": row["membership_id"],
        "question": row["question"],
        "answerMarkdown": row["answer_markdown"],
        "sourceManifest": _safe_manifest(_json(row["source_manifest_json"], {})),
        "modelName": row["model_name"],
        "lifecycleState": row["lifecycle_state"],
        "version": row["version"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _document_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "documentId": row["document_id"],
        "projectId": row["project_id"],
        "sourceAssetId": row["source_asset_id"],
        "title": row["title"],
        "documentKind": row["document_kind"],
        "visibilityScope": row["visibility_scope"],
        "parseState": row["parse_state"],
        "lifecycleState": row["lifecycle_state"],
        "currentVersion": row["current_version"],
        "version": row["version"],
        "updatedAt": row["updated_at"],
        "contentHash": row["content_hash"] if "content_hash" in row.keys() else None,
        "previewText": row["preview_text"] if "preview_text" in row.keys() else "",
    }


def _report_payload(row: sqlite3.Row) -> dict[str, Any]:
    content_json = _json(row["content_json"], {})
    return {
        "id": row["narrative_output_id"],
        "event_line_id": row["event_line_id"],
        "client_id": row["project_id"],
        "title": row["title"],
        "status": (
            row["lifecycle_state"]
            if row["lifecycle_state"] in {"active", "stale", "archived"}
            else "active"
        ),
        "latest_version": row["latest_version"],
        "is_stale": row["lifecycle_state"] == "stale",
        "availability_status": (
            "blocked"
            if row["lifecycle_state"] == "blocked"
            else "stale"
            if row["lifecycle_state"] == "stale"
            else "ready"
        ),
        "availability_reason": (
            "产物已标记为过期"
            if row["lifecycle_state"] == "stale"
            else "产物当前不可用"
            if row["lifecycle_state"] == "blocked"
            else ""
        ),
        "stale_reasons": ["权威输入已有更新"] if row["lifecycle_state"] == "stale" else [],
        "updated_at": row["updated_at"],
        "aggregateVersion": row["aggregate_version"],
        "outputKind": row["output_kind"],
        "latest": {
            "id": row["narrative_output_version_id"],
            "artifact_id": row["narrative_output_id"],
            "version": row["content_version"],
            "title": row["title"],
            "content_markdown": row["content_markdown"],
            "content_payload": content_json,
            "source_set_id": "",
            "narrative_id": row["narrative_output_id"],
            "narrative_rev": row["content_version"],
            "event_line_version": int(content_json.get("eventLineVersion") or 0),
            "input_fingerprint": row["input_fingerprint"],
            "security_label_set_version": "",
            "content_hash": row["content_hash"],
            "change_summary": row["change_summary"],
            "created_by_display_name": row["creator_name"] or "",
            "restored_from_version": content_json.get("restoredFromVersion"),
            "created_at": row["version_created_at"],
        },
    }


def _report_select() -> str:
    return """
        SELECT n.narrative_output_id, n.organization_id, n.project_id,
               n.event_line_id, n.output_kind, n.title, n.lifecycle_state,
               n.latest_version, n.version AS aggregate_version,
               n.created_by_membership_id, n.created_at, n.updated_at,
               v.narrative_output_version_id,
               v.version AS content_version, v.content_markdown,
               v.content_json, v.input_fingerprint, v.content_hash,
               v.change_summary, v.created_at AS version_created_at,
               p.display_name AS creator_name
        FROM narrative_outputs n
        JOIN narrative_output_versions v
          ON v.narrative_output_id = n.narrative_output_id
         AND v.version = n.latest_version
        LEFT JOIN organization_memberships m
          ON m.membership_id = v.created_by_membership_id
        LEFT JOIN identity_principals p ON p.principal_id = m.principal_id
    """


def _project_processing_attempts(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    project_id: str,
    visible_document_ids: set[str],
) -> list[dict[str, Any]]:
    document_clause = ""
    parameters: list[Any] = [identity.organization_id, project_id]
    if visible_document_ids:
        placeholders = ",".join("?" for _ in visible_document_ids)
        document_clause = f"OR pa.document_id IN ({placeholders})"
        parameters.extend(sorted(visible_document_ids))
    rows = connection.execute(
        f"""
        SELECT pa.processing_attempt_id AS processingAttemptId,
               pa.source_asset_id AS sourceAssetId,
               pa.document_id AS documentId,
               pa.processing_kind AS processingKind,
               pa.state, pa.attempt_no AS attemptNo,
               pa.error_code AS errorCode,
               pa.error_message AS errorMessage,
               pa.started_at AS startedAt,
               pa.finished_at AS finishedAt,
               pa.created_at AS createdAt
        FROM processing_attempts pa
        LEFT JOIN source_assets sa
          ON sa.organization_id = pa.organization_id
         AND sa.source_asset_id = pa.source_asset_id
        WHERE pa.organization_id = ?
          AND (
            (
              sa.project_id = ?
              AND sa.source_kind IN (
                'workbench_analysis_job',
                'workbench_context_refresh'
              )
            )
            {document_clause}
          )
        ORDER BY pa.created_at DESC, pa.processing_attempt_id
        """,
        tuple(parameters),
    ).fetchall()
    return [dict(row) for row in rows]


def project_workspace(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    # Strategic accompaniment is a retained, high-frequency consumer.  It must
    # not fan out through the frozen generic ``business_snapshot`` facade.  A
    # single project-scoped read keeps every row on the approved 88-table
    # authority and makes the access decision before any child object is read.
    from .gc04_tasks import GC04TaskRepository

    with repository._connection() as connection:  # noqa: SLF001
        project_row = _require_project(repository, connection, identity, project_id)
        document_rows = connection.execute(
            """
            SELECT d.*, v.content_hash
            FROM knowledge_documents AS d
            LEFT JOIN document_versions AS v
              ON v.scope_id=d.scope_id AND v.document_id=d.id
             AND v.version=d.current_version
            WHERE d.scope_id=? AND d.client_id=?
              AND d.lifecycle_state='active'
            ORDER BY d.updated_at DESC, d.id
            """,
            (identity.scope_id, project_id),
        ).fetchall()
        event_rows = connection.execute(
            """
            SELECT * FROM event_lines
            WHERE scope_id=? AND client_id=? AND record_kind='line'
              AND lifecycle_state!='deleted'
            ORDER BY updated_at DESC, id
            """,
            (identity.scope_id, project_id),
        ).fetchall()
        report_rows = connection.execute(
            """
            SELECT * FROM narrative_outputs
            WHERE scope_id=? AND client_id=? AND lifecycle_state!='deleted'
            ORDER BY updated_at DESC, id
            """,
            (identity.scope_id, project_id),
        ).fetchall()
        source_asset_ids = [
            str(row["source_asset_id"])
            for row in document_rows
            if row["source_asset_id"]
        ]
        attempts: list[dict[str, Any]] = []
        if source_asset_ids:
            placeholders = ",".join("?" for _ in source_asset_ids)
            attempt_rows = connection.execute(
                f"""
                SELECT * FROM processing_attempts
                WHERE scope_id=? AND source_asset_id IN ({placeholders})
                ORDER BY started_at DESC, id
                """,
                (identity.scope_id, *source_asset_ids),
            ).fetchall()
            attempts = [
                {
                    "processingAttemptId": row["id"],
                    "sourceAssetId": row["source_asset_id"],
                    "documentId": None,
                    "processingKind": row["processor_kind"],
                    "state": row["status"],
                    "attemptNo": row["attempt_no"],
                    "errorCode": row["error_code"],
                    "errorMessage": row["error_message_safe"],
                    "startedAt": row["started_at"],
                    "finishedAt": row["finished_at"],
                    "createdAt": row["started_at"],
                }
                for row in attempt_rows
            ]
    board = GC04TaskRepository(repository).board(identity)
    task_rows = [
        item
        for item in board.get("tasks") or []
        if str(item.get("client_id") or "") == project_id
    ]
    project = {
        "projectId": project_row["id"],
        "name": project_row["name"],
        "alias": project_row.get("alias") or "",
        "summary": project_row.get("summary") or "",
        "domain": project_row.get("domain") or "",
        "color": project_row.get("color") or "",
        "lifecycleState": project_row["lifecycle_state"],
        "version": int(project_row["version"]),
        "createdAt": project_row["created_at"],
        "updatedAt": project_row["updated_at"],
    }
    documents = [
        {
            "documentId": row["id"],
            "projectId": row["client_id"],
            "sourceAssetId": row["source_asset_id"],
            "title": row["title"],
            "documentKind": row["document_kind"],
            "visibilityScope": row["visibility_scope"],
            "parseState": row["parse_state"],
            "publicationState": row["publication_state"],
            "lifecycleState": row["lifecycle_state"],
            "currentVersion": int(row["current_version"] or 0),
            "version": int(row["version"] or 1),
            "contentHash": row["content_hash"],
            "updatedAt": row["updated_at"],
        }
        for row in document_rows
    ]
    tasks = []
    for item in task_rows:
        collaborators = [
            {
                "membershipId": collaborator.get("subject_membership_id"),
                "displayName": collaborator.get("display_name") or "",
                "role": collaborator.get("role_key"),
                "inboxStatus": collaborator.get("inbox_status"),
            }
            for collaborator in item.get("collaborators") or []
        ]
        owner = next(
            (
                collaborator.get("membershipId")
                for collaborator in collaborators
                if collaborator.get("role") == "owner"
            ),
            None,
        )
        tasks.append(
            {
                "taskId": item["id"],
                "projectId": item.get("client_id"),
                "eventLineId": item.get("event_line_id"),
                "title": item.get("title") or "",
                "description": item.get("description") or "",
                "priority": item.get("priority") or "normal",
                "ownerMembershipId": owner,
                "collaborators": collaborators,
                "dueDate": item.get("due_date"),
                "deadlineAt": item.get("due_date"),
                "lifecycleState": "completed" if item.get("completed_at") else item.get("lifecycle_state"),
                "version": int(item.get("version") or 1),
                "createdAt": item.get("created_at"),
                "updatedAt": item.get("updated_at"),
            }
        )
    event_lines = [
        {
            "eventLineId": row["id"],
            "projectId": row["client_id"],
            "name": row["name"] or row["title"] or "",
            "goal": row["goal"] or "",
            "background": row["background"] or row["summary"] or "",
            "lifecycleState": row["lifecycle_state"],
            "createdByMembershipId": row["created_by_membership_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in event_rows
    ]
    return {
        "organizationId": identity.organization_id,
        "project": project,
        "documents": documents,
        "answers": [],
        "favorites": [],
        "reports": [
            {
                "narrativeOutputId": row["id"],
                "projectId": row["client_id"],
                "outputKind": row["artifact_kind"],
                "title": row["title"],
                "lifecycleState": row["lifecycle_state"],
                "latestVersion": row["current_version"],
                "version": row["version"],
                "updatedAt": row["updated_at"],
            }
            for row in report_rows
        ],
        "tasks": tasks,
        "eventLines": event_lines,
        "processingAttempts": attempts,
        "generatedAt": utc_now(),
    }


def answer_detail(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    answer_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            """
            SELECT answer.*, context.question_hash
            FROM ai_answers AS answer
            JOIN ai_context_manifests AS context
              ON context.id=answer.ai_context_manifest_id
            WHERE answer.scope_id = ? AND answer.id = ?
              AND answer.lifecycle_state='active'
            """,
            (identity.scope_id, answer_id),
        ).fetchone()
        if row is not None:
            repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=str(row["client_id"]),
                capability="project_read",
            )
    if row is None:
        raise RepositoryError(404, "answer_missing", "工作台回答不存在")
    return {
        "answer": {
            "answerId": str(row["id"]),
            "projectId": str(row["client_id"]),
            "threadId": str(row["thread_id"] or ""),
            "questionHash": str(row["question_hash"] or ""),
            "answerHash": str(row["answer_hash"] or ""),
            "sourceSetId": str(row["source_set_id"] or ""),
            "sourceCount": int(row["source_count"] or 0),
            "modelName": str(row["model_name"] or ""),
            "lifecycleState": str(row["lifecycle_state"] or "active"),
            "version": int(row["version"] or 1),
            # L0 question/answer body remains on the generating member device.
            "materialBoundary": {"answerBodyReturned": False},
        }
    }


def archive_answer(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    answer_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    command_type = "workbench.answer.archived"
    expected = _expected_version(payload)
    normalized = {"answerId": answer_id, "expectedVersion": expected}
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = connection.execute(
                """
                SELECT * FROM ai_answers
                WHERE organization_id = ? AND membership_id = ? AND ai_answer_id = ?
                """,
                (identity.organization_id, identity.membership_id, answer_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "answer_missing", "工作台回答不存在")
            current_version = int(row["version"])
            if current_version != expected:
                raise RepositoryError(409, "answer_version_conflict", "回答已被其他操作更新")
            now = utc_now()
            connection.execute(
                """
                UPDATE ai_answers
                SET lifecycle_state = 'archived', version = version + 1, updated_at = ?
                WHERE organization_id = ? AND ai_answer_id = ? AND version = ?
                """,
                (now, identity.organization_id, answer_id, expected),
            )
            result = {
                "answerId": answer_id,
                "archived": True,
                "version": expected + 1,
                "updatedAt": now,
            }
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="ai_answer",
                aggregate_id=answer_id,
                expected_version=expected,
                before_version=expected,
                after_version=expected + 1,
                payload=normalized,
                result=result,
                audit_summary={"lifecycleState": "archived"},
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _favorite_target(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    target_type: str,
    target_id: str,
) -> tuple[str, int]:
    if target_type == "ai_answer":
        row = connection.execute(
            """
            SELECT question AS title, version
            FROM ai_answers
            WHERE organization_id = ? AND membership_id = ?
              AND ai_answer_id = ? AND lifecycle_state = 'active'
            """,
            (identity.organization_id, identity.membership_id, target_id),
        ).fetchone()
    elif target_type == "knowledge_document":
        visible_ids = {
            item["documentId"] for item in repository.business_snapshot(identity)["documents"]
        }
        if target_id not in visible_ids:
            row = None
        else:
            row = connection.execute(
                """
                SELECT title, version FROM knowledge_documents
                WHERE organization_id = ? AND document_id = ?
                  AND lifecycle_state = 'active'
                """,
                (identity.organization_id, target_id),
            ).fetchone()
    elif target_type == "narrative_output":
        visible_ids = {
            item["narrativeOutputId"]
            for item in repository.business_snapshot(identity)["reports"]
        }
        if target_id not in visible_ids:
            row = None
        else:
            row = connection.execute(
                """
                SELECT title, version FROM narrative_outputs
                WHERE organization_id = ? AND narrative_output_id = ?
                  AND lifecycle_state != 'archived'
                """,
                (identity.organization_id, target_id),
            ).fetchone()
    else:
        raise RepositoryError(422, "favorite_target_invalid", "收藏对象类型不受支持")
    if row is None:
        raise RepositoryError(404, "favorite_target_missing", "收藏对象不存在或不可见")
    return str(row["title"]), int(row["version"])


def create_favorite(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    command_type = "workbench.favorite.created"
    target_type = str(payload.get("targetType") or "").strip()
    target_id = str(payload.get("targetId") or "").strip()
    requested_title = str(payload.get("title") or "").strip()
    expected = _expected_version(payload)
    normalized = {
        "targetType": target_type,
        "targetId": target_id,
        "title": requested_title,
        "expectedVersion": expected,
    }
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            target_title, target_version = _favorite_target(
                repository,
                connection,
                identity,
                target_type,
                target_id,
            )
            if target_version != expected:
                raise RepositoryError(409, "favorite_target_version_conflict", "收藏对象已更新")
            duplicate = connection.execute(
                """
                SELECT * FROM workbench_favorites
                WHERE organization_id = ? AND membership_id = ?
                  AND target_type = ? AND target_id = ?
                """,
                (identity.organization_id, identity.membership_id, target_type, target_id),
            ).fetchone()
            if duplicate is not None:
                return {
                    "favorite": {
                        "favoriteId": duplicate["favorite_id"],
                        "targetType": duplicate["target_type"],
                        "targetId": duplicate["target_id"],
                        "title": duplicate["title"],
                        "version": 1,
                        "createdAt": duplicate["created_at"],
                    }
                }
            now = utc_now()
            favorite_id = new_id()
            title = requested_title or target_title
            connection.execute(
                """
                INSERT INTO workbench_favorites (
                    favorite_id, organization_id, membership_id,
                    target_type, target_id, title, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    favorite_id,
                    identity.organization_id,
                    identity.membership_id,
                    target_type,
                    target_id,
                    title,
                    now,
                ),
            )
            result = {
                "favorite": {
                    "favoriteId": favorite_id,
                    "targetType": target_type,
                    "targetId": target_id,
                    "title": title,
                    "version": 1,
                    "createdAt": now,
                }
            }
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="workbench_favorite",
                aggregate_id=favorite_id,
                expected_version=expected,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={"targetType": target_type, "targetId": target_id},
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def delete_favorite(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    favorite_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    command_type = "workbench.favorite.removed"
    expected = _expected_version(payload)
    normalized = {"favoriteId": favorite_id, "expectedVersion": expected}
    if expected != 1:
        raise RepositoryError(409, "favorite_version_conflict", "收藏记录版本不匹配")
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = connection.execute(
                """
                SELECT * FROM workbench_favorites
                WHERE organization_id = ? AND membership_id = ? AND favorite_id = ?
                """,
                (identity.organization_id, identity.membership_id, favorite_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "favorite_missing", "收藏记录不存在")
            connection.execute(
                """
                DELETE FROM workbench_favorites
                WHERE organization_id = ? AND membership_id = ? AND favorite_id = ?
                """,
                (identity.organization_id, identity.membership_id, favorite_id),
            )
            result = {"favoriteId": favorite_id, "removed": True, "version": 2}
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="workbench_favorite",
                aggregate_id=favorite_id,
                expected_version=1,
                before_version=1,
                after_version=2,
                payload=normalized,
                result=result,
                audit_summary={
                    "targetType": row["target_type"],
                    "targetId": row["target_id"],
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def knowledge_status(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    snapshot = repository.business_snapshot(identity)
    if not any(item["projectId"] == project_id for item in snapshot["projects"]):
        raise RepositoryError(404, "project_missing", "当前成员无法访问该项目")
    documents = [
        item for item in snapshot["documents"] if item["projectId"] == project_id
    ]
    document_ids = {item["documentId"] for item in documents}
    attempts: list[dict[str, Any]] = []
    version_by_document: dict[str, dict[str, Any]] = {}
    with repository._connection() as connection:  # noqa: SLF001
        attempts = _project_processing_attempts(
            connection,
            identity,
            project_id=project_id,
            visible_document_ids={str(value) for value in document_ids},
        )
        if document_ids:
            placeholders = ",".join("?" for _ in document_ids)
            version_by_document = {
                str(row["documentId"]): dict(row)
                for row in connection.execute(
                    f"""
                    SELECT d.document_id AS documentId,
                           v.content_hash AS contentHash,
                           v.preview_text AS previewText,
                           v.section_count AS sectionCount,
                           v.chunk_count AS chunkCount,
                           v.generator_version AS generatorVersion,
                           v.created_at AS versionCreatedAt
                    FROM knowledge_documents d
                    JOIN document_versions v
                      ON v.document_id = d.document_id
                     AND v.version = d.current_version
                    WHERE d.organization_id = ?
                      AND d.document_id IN ({placeholders})
                    """,
                    (identity.organization_id, *sorted(document_ids)),
                ).fetchall()
            }
    current_attempt: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        current_attempt.setdefault(str(attempt["documentId"]), attempt)
    states = {
        "ready": sum(1 for item in documents if item["parseState"] == "ready"),
        "partial": sum(1 for item in documents if item["parseState"] == "partial_ready"),
        "failed": sum(
            1 for item in documents if item["parseState"] in {"failed", "missing_source"}
        ),
        "pending": sum(
            1
            for item in documents
            if item["parseState"] in {"not_requested", "queued", "processing"}
        ),
    }
    return {
        "projectId": project_id,
        "state": "ready" if documents else "empty",
        "documents": [
            {
                **item,
                **version_by_document.get(item["documentId"], {}),
                "latestProcessingAttempt": current_attempt.get(item["documentId"]),
            }
            for item in documents
        ],
        "processingAttempts": attempts,
        "counts": {
            "total": len(documents),
            "totalSections": sum(
                int(item.get("sectionCount") or 0)
                for item in version_by_document.values()
            ),
            "totalChunks": sum(
                int(item.get("chunkCount") or 0)
                for item in version_by_document.values()
            ),
            **states,
        },
        "generatedAt": utc_now(),
    }


def analysis_status(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    workspace = project_workspace(repository, identity, project_id=project_id)
    attempts = workspace["processingAttempts"]
    analysis_attempts = [
        item
        for item in attempts
        if any(
            marker in str(item.get("processingKind") or "").lower()
            for marker in ("analysis", "extract", "parse", "transcrib", "summary")
        )
    ]
    answer_ids = {item["answerId"] for item in workspace["answers"]}
    report_ids = {item["narrativeOutputId"] for item in workspace["reports"]}
    with repository._connection() as connection:  # noqa: SLF001
        evidence_count = 0
        targets = answer_ids | report_ids
        if targets:
            placeholders = ",".join("?" for _ in targets)
            evidence_count = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*) FROM evidence_links
                    WHERE organization_id = ? AND target_id IN ({placeholders})
                      AND lifecycle_state = 'active'
                    """,
                    (identity.organization_id, *sorted(targets)),
                ).fetchone()[0]
            )
    return {
        "projectId": project_id,
        "state": (
            "ready"
            if analysis_attempts or workspace["answers"] or workspace["reports"]
            else "empty"
        ),
        "attempts": analysis_attempts,
        "counts": {
            "attempts": len(analysis_attempts),
            "answers": len(workspace["answers"]),
            "reports": len(workspace["reports"]),
            "evidenceLinks": evidence_count,
            "failedAttempts": sum(
                1 for item in analysis_attempts if item.get("state") == "failed"
            ),
            "runningAttempts": sum(
                1
                for item in analysis_attempts
                if item.get("state") in {"queued", "processing"}
            ),
        },
        "generatedAt": utc_now(),
    }


def cancel_analysis_run(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    run_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    command_type = "processing_attempt.cancelled"
    payload = {"projectId": project_id, "runId": run_id}
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=payload,
            )
            if existing is not None:
                connection.rollback()
                return existing
            _require_project_editor(
                repository,
                connection,
                identity,
                project_id,
            )
            row = connection.execute(
                """
                SELECT pa.*
                FROM processing_attempts pa
                LEFT JOIN knowledge_documents d
                  ON d.organization_id = pa.organization_id
                 AND d.document_id = pa.document_id
                LEFT JOIN source_assets a
                  ON a.organization_id = pa.organization_id
                 AND a.source_asset_id = pa.source_asset_id
                WHERE pa.organization_id = ?
                  AND pa.processing_attempt_id = ?
                  AND COALESCE(d.project_id, a.project_id) = ?
                """,
                (identity.organization_id, run_id, project_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(
                    404,
                    "analysis_run_missing",
                    "分析记录不存在或不属于该项目",
                )
            state = str(row["state"])
            if state in {"completed", "failed"}:
                raise RepositoryError(
                    409,
                    "analysis_run_terminal",
                    "该分析已结束，不能取消；结果仍保留在严格组织云",
                )
            now = utc_now()
            if state != "cancelled":
                changed = connection.execute(
                    """
                    UPDATE processing_attempts
                    SET state = 'cancelled', finished_at = ?,
                        error_code = '', error_message = ''
                    WHERE organization_id = ?
                      AND processing_attempt_id = ?
                      AND state IN ('queued', 'processing', 'partial')
                    """,
                    (now, identity.organization_id, run_id),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "analysis_run_state_conflict",
                        "分析状态已变化，请刷新后重试",
                    )
            result = {
                "processingAttemptId": run_id,
                "projectId": project_id,
                "processingKind": row["processing_kind"],
                "state": "cancelled",
                "attemptNo": row["attempt_no"],
                "finishedAt": row["finished_at"] or now,
            }
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="processing_attempt",
                aggregate_id=run_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=payload,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "processingKind": row["processing_kind"],
                    "state": "cancelled",
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def list_dna_modules(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        rows = connection.execute(
            """
            SELECT d.*, v.content_hash, v.preview_text, v.markdown_content,
                   v.generator_version, p.display_name AS updated_by
            FROM knowledge_documents d
            JOIN document_versions v
              ON v.document_id = d.document_id AND v.version = d.current_version
            LEFT JOIN organization_memberships m
              ON m.membership_id = d.owner_membership_id
            LEFT JOIN identity_principals p ON p.principal_id = m.principal_id
            WHERE d.organization_id = ? AND d.project_id = ?
              AND d.document_kind LIKE ? AND d.lifecycle_state = 'active'
            ORDER BY d.updated_at DESC, d.document_id
            """,
            (identity.organization_id, project_id, f"{DNA_KIND_PREFIX}%"),
        ).fetchall()
    latest_by_key: dict[str, sqlite3.Row] = {}
    for row in rows:
        module_key = str(row["document_kind"])[len(DNA_KIND_PREFIX) :]
        latest_by_key.setdefault(module_key, row)
    modules = []
    for key, title in DNA_MODULES.items():
        row = latest_by_key.get(key)
        modules.append(
            {
                "clientId": project_id,
                "moduleKey": key,
                "title": title,
                "markdownContent": row["markdown_content"] if row else "",
                "normalizedText": row["preview_text"] if row else "",
                "summary": row["preview_text"] if row else "",
                "fileName": None,
                "contentHash": row["content_hash"] if row else None,
                "sourceKind": (
                    "generated"
                    if row and row["generator_version"] != "manual-dna-v1"
                    else "manual"
                ),
                "missingInfo": [],
                "updatedAt": row["updated_at"] if row else None,
                "updatedBy": row["updated_by"] if row else None,
                "hasDocument": row is not None,
                "documentId": row["document_id"] if row else None,
                "version": int(row["version"]) if row else 0,
            }
        )
    return {"modules": modules}


def save_dna_module(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    module_key: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if module_key not in DNA_MODULES:
        raise RepositoryError(422, "dna_module_invalid", "DNA 模块不存在")
    markdown = str(payload.get("markdownContent") or "").strip()
    if not markdown:
        raise RepositoryError(422, "dna_content_required", "DNA 内容不能为空")
    expected = int(payload.get("expectedVersion") or 0)
    title = DNA_MODULES[module_key]
    normalized = {
        "projectId": project_id,
        "moduleKey": module_key,
        "title": title,
        "markdownContent": markdown,
        "expectedVersion": expected,
    }
    command_type = "workbench.dna.saved"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            _require_project_editor(repository, connection, identity, project_id)
            row = connection.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE organization_id = ? AND project_id = ?
                  AND document_kind = ? AND lifecycle_state = 'active'
                ORDER BY updated_at DESC, document_id
                LIMIT 1
                """,
                (identity.organization_id, project_id, f"{DNA_KIND_PREFIX}{module_key}"),
            ).fetchone()
            now = utc_now()
            content_hash = sha256_text(markdown)
            preview = " ".join(markdown.split())[:1200]
            if row is None:
                if expected != 0:
                    raise RepositoryError(409, "dna_version_conflict", "DNA 文档尚未创建")
                document_id = new_id()
                document_version = 1
                aggregate_version = 1
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        document_id, organization_id, project_id,
                        project_assignment_state, source_asset_id,
                        owner_membership_id, department_id, title, document_kind,
                        visibility_scope, parse_state, lifecycle_state,
                        current_version, version, created_at, updated_at
                    ) VALUES (?, ?, ?, 'assigned', NULL, ?, NULL, ?, ?,
                              'organization', 'ready', 'active', 1, 1, ?, ?)
                    """,
                    (
                        document_id,
                        identity.organization_id,
                        project_id,
                        identity.membership_id,
                        title,
                        f"{DNA_KIND_PREFIX}{module_key}",
                        now,
                        now,
                    ),
                )
                before_version = None
            else:
                aggregate_version = int(row["version"])
                if expected != aggregate_version:
                    raise RepositoryError(409, "dna_version_conflict", "DNA 文档已被其他成员更新")
                document_id = str(row["document_id"])
                document_version = int(row["current_version"]) + 1
                connection.execute(
                    """
                    UPDATE knowledge_documents
                    SET title = ?, parse_state = 'ready',
                        current_version = ?, version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND document_id = ? AND version = ?
                    """,
                    (
                        title,
                        document_version,
                        now,
                        identity.organization_id,
                        document_id,
                        expected,
                    ),
                )
                aggregate_version += 1
                before_version = expected
            connection.execute(
                """
                INSERT INTO document_versions (
                    document_version_id, organization_id, document_id, version,
                    content_hash, preview_text, markdown_content, section_count,
                    chunk_count, generator_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 'manual-dna-v1', ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    document_id,
                    document_version,
                    content_hash,
                    preview,
                    markdown,
                    now,
                ),
            )
            result = {
                "clientId": project_id,
                "moduleKey": module_key,
                "title": title,
                "markdownContent": markdown,
                "normalizedText": preview,
                "summary": preview,
                "fileName": str(payload.get("fileName") or "") or None,
                "contentHash": content_hash,
                "sourceKind": "manual",
                "missingInfo": [],
                "updatedAt": now,
                "updatedBy": identity.display_name,
                "hasDocument": True,
                "documentId": document_id,
                "version": aggregate_version,
            }
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="knowledge_document",
                aggregate_id=document_id,
                expected_version=expected,
                before_version=before_version,
                after_version=aggregate_version,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "moduleKey": module_key,
                    "contentHash": content_hash,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def list_reports(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        rows = connection.execute(
            _report_select()
            + """
            WHERE n.organization_id = ?
              AND n.project_id = ?
              AND n.output_kind IN ('event_line_report', 'weekly_report', 'strategy_report')
              AND COALESCE(json_extract(v.content_json, '$.workbenchKind'), '') != ?
            ORDER BY n.updated_at DESC, n.narrative_output_id
            """,
            (identity.organization_id, project_id, JUDGMENT_CONTENT_KIND),
        ).fetchall()
    return [_report_payload(row) for row in rows]


def report_detail(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    report_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            _report_select()
            + """
            WHERE n.organization_id = ? AND n.narrative_output_id = ?
            """,
            (identity.organization_id, report_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "report_missing", "报告不存在")
        if row["project_id"]:
            _require_project(
                repository,
                connection,
                identity,
                str(row["project_id"]),
            )
        else:
            visible_ids = {
                item["narrativeOutputId"]
                for item in repository.business_snapshot(identity)["reports"]
            }
            if report_id not in visible_ids:
                raise RepositoryError(404, "report_missing", "报告不存在或当前成员不可见")
        return _report_payload(row)


def report_versions(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    report_id: str,
) -> list[dict[str, Any]]:
    current = report_detail(repository, identity, report_id=report_id)
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            """
            SELECT v.*, n.title, p.display_name AS creator_name
            FROM narrative_output_versions v
            JOIN narrative_outputs n
              ON n.narrative_output_id = v.narrative_output_id
            LEFT JOIN organization_memberships m
              ON m.membership_id = v.created_by_membership_id
            LEFT JOIN identity_principals p ON p.principal_id = m.principal_id
            WHERE v.organization_id = ? AND v.narrative_output_id = ?
            ORDER BY v.version DESC
            """,
            (identity.organization_id, report_id),
        ).fetchall()
    return [
        {
            "id": row["narrative_output_version_id"],
            "artifact_id": report_id,
            "version": row["version"],
            "title": row["title"],
            "content_markdown": row["content_markdown"],
            "content_payload": _json(row["content_json"], {}),
            "source_set_id": "",
            "narrative_id": report_id,
            "narrative_rev": row["version"],
            "event_line_version": int(
                _json(row["content_json"], {}).get("eventLineVersion") or 0
            ),
            "input_fingerprint": row["input_fingerprint"],
            "security_label_set_version": "",
            "content_hash": row["content_hash"],
            "change_summary": row["change_summary"],
            "created_by_display_name": row["creator_name"] or "",
            "restored_from_version": _json(row["content_json"], {}).get(
                "restoredFromVersion"
            ),
            "created_at": row["created_at"],
            "isCurrent": row["version"] == current["latest_version"],
        }
        for row in rows
    ]


def _require_report_editor(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    report_id: str,
) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT * FROM narrative_outputs
        WHERE organization_id = ? AND narrative_output_id = ?
        """,
        (identity.organization_id, report_id),
    ).fetchone()
    if row is None:
        raise RepositoryError(404, "report_missing", "报告不存在")
    if row["project_id"]:
        _require_project_editor(
            repository,
            connection,
            identity,
            str(row["project_id"]),
        )
    elif not identity.is_admin and row["created_by_membership_id"] != identity.membership_id:
        raise RepositoryError(403, "report_edit_forbidden", "无权修改该报告")
    return row


def create_report(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    requested_report_id = str(payload.get("reportId") or "").strip()
    project_id = str(payload.get("projectId") or "").strip()
    event_line_id = str(payload.get("eventLineId") or "").strip() or None
    title = str(payload.get("title") or "报告草稿").strip()
    output_kind = str(payload.get("outputKind") or "strategy_report").strip()
    content_markdown = str(
        payload.get("contentMarkdown") or "# 报告草稿"
    ).strip()
    raw_content_json = payload.get("contentJson") or {}
    content_json = (
        _safe_manifest(dict(raw_content_json))
        if isinstance(raw_content_json, Mapping)
        else {}
    )
    if not project_id:
        raise RepositoryError(
            422,
            "report_project_required",
            "创建报告需要固定项目 WorkspaceContext",
        )
    if output_kind not in REPORT_KINDS:
        raise RepositoryError(422, "report_kind_invalid", "报告类型无效")
    normalized = {
        "projectId": project_id,
        "reportId": requested_report_id or None,
        "eventLineId": event_line_id,
        "title": title,
        "outputKind": output_kind,
        "contentMarkdown": content_markdown,
        "contentJson": content_json,
    }
    command_type = "workbench.report.created"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            _require_project_editor(
                repository,
                connection,
                identity,
                project_id,
            )
            if event_line_id is not None:
                event_line = connection.execute(
                    """
                    SELECT event_line_id
                    FROM event_line_records
                    WHERE organization_id = ? AND event_line_id = ?
                      AND project_id = ? AND lifecycle_state != 'archived'
                    """,
                    (identity.organization_id, event_line_id, project_id),
                ).fetchone()
                if event_line is None:
                    raise RepositoryError(
                        404,
                        "event_line_missing",
                        "事件线不存在、不可见或不属于该项目",
                    )
            report_id = requested_report_id or new_id()
            if requested_report_id:
                conflict = connection.execute(
                    """
                    SELECT 1 FROM narrative_outputs
                    WHERE organization_id = ? AND narrative_output_id = ?
                    """,
                    (identity.organization_id, report_id),
                ).fetchone()
                if conflict is not None:
                    raise RepositoryError(
                        409,
                        "report_identity_conflict",
                        "报告 ID 已存在，请使用原幂等请求重试",
                    )
            version_id = new_id()
            now = utc_now()
            content_hash = sha256_text(content_markdown)
            input_fingerprint = payload_fingerprint(
                {
                    "projectId": project_id,
                    "contentJson": content_json,
                }
            )
            connection.execute(
                """
                INSERT INTO narrative_outputs (
                    narrative_output_id, organization_id, project_id,
                    event_line_id, output_kind, title, lifecycle_state,
                    latest_version, created_by_membership_id, version,
                    created_at, updated_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, 1, ?, ?, NULL)
                """,
                (
                    report_id,
                    identity.organization_id,
                    project_id,
                    event_line_id,
                    output_kind,
                    title,
                    identity.membership_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO narrative_output_versions (
                    narrative_output_version_id, organization_id,
                    narrative_output_id, version, content_markdown,
                    content_json, input_fingerprint, content_hash,
                    change_summary, created_by_membership_id, created_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, '保存报告', ?, ?)
                """,
                (
                    version_id,
                    identity.organization_id,
                    report_id,
                    content_markdown,
                    canonical_json(content_json),
                    input_fingerprint,
                    content_hash,
                    identity.membership_id,
                    now,
                ),
            )
            artifact = {
                "id": report_id,
                "event_line_id": event_line_id,
                "client_id": project_id,
                "title": title,
                "status": "active",
                "latest_version": 1,
                "is_stale": False,
                "availability_status": "ready",
                "availability_reason": "",
                "stale_reasons": [],
                "updated_at": now,
                "aggregateVersion": 1,
                "outputKind": output_kind,
                "latest": {
                    "id": version_id,
                    "artifact_id": report_id,
                    "version": 1,
                    "title": title,
                    "content_markdown": content_markdown,
                    "content_payload": content_json,
                    "source_set_id": "",
                    "narrative_id": report_id,
                    "narrative_rev": 1,
                    "event_line_version": int(
                        content_json.get("eventLineVersion") or 0
                    ),
                    "input_fingerprint": input_fingerprint,
                    "security_label_set_version": "",
                    "content_hash": content_hash,
                    "change_summary": "保存报告",
                    "created_by_display_name": "",
                    "restored_from_version": None,
                    "created_at": now,
                },
            }
            sections = content_json.get("sections")
            sections = sections if isinstance(sections, list) else []
            result = {
                "id": report_id,
                "client_id": project_id,
                "event_line_id": event_line_id,
                "period_start": content_json.get("period_start"),
                "period_end": content_json.get("period_end"),
                "intent_hint": content_json.get("intent_hint"),
                "status": "saved",
                "blueprint": content_json.get("blueprint"),
                "sections_status": ["done" for _ in sections],
                "sections": sections,
                "body_markdown": content_markdown,
                "warnings": [],
                "source_set_id": "",
                "narrative_id": report_id,
                "narrative_rev": 1,
                "event_line_version": int(
                    content_json.get("eventLineVersion") or 0
                ),
                "input_fingerprint": input_fingerprint,
                "artifact": artifact,
                "saved_at": now,
                "error_message": None,
                "output_files": {},
                "total_llm_tokens": 0,
                "created_at": now,
                "updated_at": now,
            }
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="narrative_output",
                aggregate_id=report_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "outputKind": output_kind,
                    "contentHash": content_hash,
                    "contentVersion": 1,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def update_report(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    report_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    restored_from_version: int | None = None,
) -> dict[str, Any]:
    command_type = (
        "workbench.report.restored"
        if restored_from_version is not None
        else "workbench.report.updated"
    )
    expected = _expected_version(payload)
    safe_request = _safe_manifest(
        {
            key: value
            for key, value in payload.items()
            if key not in {"expectedVersion", "expected_version"}
        }
    )
    normalized_request = {
        "reportId": report_id,
        "restoredFromVersion": restored_from_version,
        "request": safe_request,
    }
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized_request,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = _require_report_editor(
                repository,
                connection,
                identity,
                report_id,
            )
            current_version = int(row["version"])
            if current_version != expected:
                raise RepositoryError(409, "report_version_conflict", "报告已被其他成员更新")
            if restored_from_version is not None:
                source = connection.execute(
                    """
                    SELECT * FROM narrative_output_versions
                    WHERE organization_id = ? AND narrative_output_id = ? AND version = ?
                    """,
                    (identity.organization_id, report_id, restored_from_version),
                ).fetchone()
                if source is None:
                    raise RepositoryError(404, "report_version_missing", "要恢复的报告版本不存在")
                content_markdown = str(source["content_markdown"])
                content_json = _json(source["content_json"], {})
                content_json["restoredFromVersion"] = restored_from_version
                title = str(row["title"])
                change_summary = f"恢复自版本 {restored_from_version}"
            else:
                content_markdown = str(
                    payload.get("contentMarkdown")
                    or payload.get("content_markdown")
                    or ""
                ).strip()
                if not content_markdown:
                    raise RepositoryError(422, "report_content_required", "报告正文不能为空")
                raw_content_json = safe_request.get(
                    "contentJson",
                    safe_request.get("content_payload", {}),
                )
                content_json = (
                    _safe_manifest(dict(raw_content_json))
                    if isinstance(raw_content_json, Mapping)
                    else {}
                )
                title = str(safe_request.get("title") or row["title"]).strip()
                change_summary = str(
                    safe_request.get("changeSummary")
                    or safe_request.get("change_summary")
                    or "更新报告正文"
                ).strip()
            next_content_version = int(row["latest_version"]) + 1
            next_aggregate_version = current_version + 1
            now = utc_now()
            content_hash = sha256_text(content_markdown)
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
                    report_id,
                    next_content_version,
                    content_markdown,
                    canonical_json(content_json),
                    str(payload.get("inputFingerprint") or ""),
                    content_hash,
                    change_summary,
                    identity.membership_id,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE narrative_outputs
                SET title = ?, lifecycle_state = 'active', latest_version = ?,
                    version = version + 1, updated_at = ?
                WHERE organization_id = ? AND narrative_output_id = ? AND version = ?
                """,
                (
                    title,
                    next_content_version,
                    now,
                    identity.organization_id,
                    report_id,
                    expected,
                ),
            )
            updated_row = connection.execute(
                _report_select()
                + """
                WHERE n.organization_id = ? AND n.narrative_output_id = ?
                """,
                (identity.organization_id, report_id),
            ).fetchone()
            if updated_row is None:
                raise RepositoryError(500, "report_update_lost", "报告更新后无法读取")
            result = _report_payload(updated_row)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="narrative_output",
                aggregate_id=report_id,
                expected_version=expected,
                before_version=expected,
                after_version=next_aggregate_version,
                payload=normalized_request,
                result=result,
                audit_summary={
                    "title": title,
                    "contentHash": content_hash,
                    "restoredFromVersion": restored_from_version,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _profile_updates_from_correction_rows(
    correction_rows: list[Mapping[str, Any]] | list[sqlite3.Row],
) -> list[dict[str, Any]]:
    profile_updates: list[dict[str, Any]] = []
    for correction_row in correction_rows:
        receipt = _json(str(correction_row["receipt"] or ""), {})
        if not isinstance(receipt, Mapping):
            continue
        statement = str(receipt.get("statement") or "").strip()
        if not statement:
            continue
        correction_kind = str(
            receipt.get("correctionKind") or "correction"
        ).strip().lower()
        if correction_kind not in {"correction", "supplement", "remember"}:
            correction_kind = "correction"
        profile_updates.append(
            {
                "id": str(correction_row["id"]),
                "updateKind": correction_kind,
                "title": (
                    "明确记住"
                    if correction_kind == "remember"
                    else ("人工纠错" if correction_kind == "correction" else "人工补充")
                ),
                "statement": statement,
                "authority": "organization_cloud",
                "visibility": "organization",
                "incorporationState": "formal_fact_ready",
                "sourceAnswerId": str(correction_row["source_answer_id"] or "") or None,
                "version": int(correction_row["version"] or 1),
                "updatedAt": correction_row["updated_at"],
            }
        )
    return profile_updates


def project_narrative(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        project = repository._require_project_access(  # noqa: SLF001
            connection,
            identity,
            project_id=project_id,
        )
        row = connection.execute(
            """
            SELECT n.id AS narrative_output_id,
                   n.current_version AS content_version,
                   n.source_set_id,
                   n.artifact_kind AS output_kind,
                   n.lifecycle_state, n.version AS aggregate_version,
                   n.updated_at, v.created_at AS version_created_at,
                   manifest.receipt, manifest.content_hash
            FROM narrative_outputs AS n
            JOIN artifact_versions AS v
              ON v.scope_id=n.scope_id AND v.artifact_id=n.id
             AND v.version=n.current_version
            LEFT JOIN object_manifests AS manifest
              ON manifest.scope_id=v.scope_id
             AND manifest.id=v.object_manifest_id
             AND manifest.lifecycle_state='active'
            WHERE n.scope_id=? AND n.client_id=?
              AND n.artifact_kind IN (
                'event_line_mainline', 'strategy_report', 'strategic_profile'
              )
              AND n.lifecycle_state='active'
            ORDER BY n.artifact_kind='strategic_profile' DESC,
                     n.updated_at DESC, n.id
            LIMIT 1
            """,
            (identity.scope_id, project_id),
        ).fetchone()
        correction_rows = connection.execute(
            """
            SELECT fact.id, fact.version, fact.updated_at, manifest.receipt,
                   (
                     SELECT member.source_object_id
                     FROM source_set_members AS member
                     WHERE member.scope_id=fact.scope_id
                       AND member.source_set_id=fact.source_set_id
                       AND member.source_object_kind='ai_answer'
                       AND member.lifecycle_state='active'
                     ORDER BY member.ordinal, member.id
                     LIMIT 1
                   ) AS source_answer_id
            FROM atomic_facts AS fact
            JOIN source_sets AS sources
              ON sources.scope_id=fact.scope_id
             AND sources.id=fact.source_set_id
             AND sources.client_id=?
             AND sources.purpose_kind IN ('answer_correction', 'answer_remember')
             AND sources.lifecycle_state='active'
            JOIN object_manifests AS manifest
              ON manifest.scope_id=fact.scope_id
             AND manifest.id=fact.fact_object_manifest_id
             AND manifest.lifecycle_state='active'
            WHERE fact.scope_id=? AND fact.lifecycle_state='active'
              AND fact.verification_state='verified'
            ORDER BY fact.updated_at DESC, fact.id
            """,
            (project_id, identity.scope_id),
        ).fetchall()
    profile_updates = _profile_updates_from_correction_rows(correction_rows)
    if row is None:
        summary = str(project["summary"] or "").strip()
        return {
            "id": f"project-metadata:{project_id}",
            "clientId": project_id,
            "clientName": project["name"],
            "rev": 0,
            "generator": "strict_project_metadata_projection",
            "generatedAt": "",
            "modelName": "",
            "dimensions": [
                {
                    "dimension": "essence",
                    "narrative": summary,
                    "confidence": "low",
                    "confidenceReason": (
                        "仅来自严格项目元数据，尚未生成组织共享叙事"
                    ),
                    "references": [],
                    "dataLayerGap": "尚无已保存的严格新版叙事产物",
                    "openClarifications": [],
                }
            ],
            "overallConfidence": 0.2 if summary else 0.0,
            "openClarificationsCount": 0,
            "dataLayerGaps": ["尚无已保存的严格新版叙事产物"],
            "contributors": [],
            "updatedAt": project["updated_at"],
            "aggregateVersion": 0,
            "lifecycleState": "not_connected",
            "sourceSetId": None,
            "sourceDocuments": [],
            "sourceFacts": [],
            "coverage": {
                "eligibleDocumentCount": 0,
                "scannedDocumentCount": 0,
                "citedDocumentCount": 0,
            },
            "profileUpdates": profile_updates,
            "narrativeNeedsRefresh": bool(profile_updates),
        }
    content_json = _json(str(row["receipt"] or ""), {})
    content = str(
        content_json.get("contentMarkdown")
        or content_json.get("content_markdown")
        or content_json.get("content")
        or ""
    )
    raw_dimensions = content_json.get("dimensions")
    dimensions = raw_dimensions if isinstance(raw_dimensions, list) else []
    if not dimensions:
        dimensions = [
            {
                "dimension": "essence",
                "narrative": content,
                "confidence": "medium",
                "confidenceReason": "来自已保存的严格新版叙事产物",
                "references": [],
                "dataLayerGap": "",
                "openClarifications": [],
            }
        ]
    return {
        "id": row["narrative_output_id"],
        "clientId": project_id,
        "clientName": project["name"],
        "rev": row["content_version"],
        "generator": str(content_json.get("generator") or "strict_saved_narrative"),
        "generatedAt": row["version_created_at"],
        "modelName": str(content_json.get("modelName") or ""),
        "dimensions": dimensions,
        "overallConfidence": float(content_json.get("overallConfidence") or 0.6),
        "openClarificationsCount": int(
            content_json.get("openClarificationsCount") or 0
        ),
        "dataLayerGaps": list(content_json.get("dataLayerGaps") or []),
        "contributors": list(content_json.get("contributors") or []),
        "updatedAt": row["updated_at"],
        "aggregateVersion": row["aggregate_version"],
        "lifecycleState": row["lifecycle_state"],
        "sourceSetId": row["source_set_id"],
        "sourceDocuments": list(content_json.get("sourceDocuments") or []),
        "sourceFacts": list(content_json.get("sourceFacts") or []),
        "coverage": dict(content_json.get("coverage") or {}),
        "profileUpdates": profile_updates,
        "narrativeNeedsRefresh": any(
            str(item.get("updatedAt") or "") > str(row["version_created_at"] or "")
            for item in profile_updates
        ),
    }


def dashboard(
    repository: CloudRepository,
    identity: SessionIdentity,
) -> dict[str, Any]:
    snapshot = repository.business_snapshot(identity)
    projects = snapshot["projects"]
    return {
        "generatedAt": snapshot["generatedAt"],
        "pulse": {
            "memoryCount": len(snapshot["aiAnswers"]),
            "docCount": len(snapshot["documents"]),
            "taskCount": len(snapshot["tasks"]),
            "chatCount": len(snapshot["aiAnswers"]),
            "eventLineCount": len(snapshot["eventLines"]),
            "dnaCount": sum(
                1
                for item in snapshot["documents"]
                if str(item.get("documentKind") or "").startswith(DNA_KIND_PREFIX)
            ),
            "badgeCount": 0,
            "handbookCount": 0,
            "daysAccompanied": 0,
            "reviewCount": len(snapshot["weeklyReviews"]),
            "meetingCount": 0,
            "weeklyNewFacts": 0,
        },
        "clients": [
            {
                "id": project["projectId"],
                "name": project["name"],
                "confidence": 1,
                "stage": project["lifecycleState"],
                "intro": project["summary"],
                "docs": sum(
                    1
                    for item in snapshot["documents"]
                    if item["projectId"] == project["projectId"]
                ),
                "dna": sum(
                    1
                    for item in snapshot["documents"]
                    if item["projectId"] == project["projectId"]
                    and str(item.get("documentKind") or "").startswith(DNA_KIND_PREFIX)
                ),
                "eventLines": sum(
                    1
                    for item in snapshot["eventLines"]
                    if item["projectId"] == project["projectId"]
                ),
                "memoryFacts": sum(
                    1
                    for item in snapshot["aiAnswers"]
                    if item["projectId"] == project["projectId"]
                ),
            }
            for project in projects
        ],
        "source": "strict_authority_projection",
        "state": {
            "overall": "blocked",
            "availableResources": "ready",
            "blockedResources": [
                "badges",
                "handbook",
                "meetings",
                "accompanied_days",
                "weekly_fact_projection",
            ],
            "message": "项目、资料、任务、问答与正式产物已接通；其余指标缺少严格权威对象。",
        },
    }


def organization_dna(
    repository: CloudRepository,
    identity: SessionIdentity,
) -> dict[str, Any]:
    snapshot = repository.business_snapshot(identity)
    project_ids = {item["projectId"] for item in snapshot["projects"]}
    items: list[dict[str, Any]] = []
    with repository._connection() as connection:  # noqa: SLF001
        if project_ids:
            placeholders = ",".join("?" for _ in project_ids)
            rows = connection.execute(
                f"""
                SELECT d.document_id, d.project_id, d.document_kind, d.title,
                       d.updated_at, v.content_hash, v.preview_text, v.created_at
                FROM knowledge_documents d
                JOIN document_versions v
                  ON v.document_id = d.document_id AND v.version = d.current_version
                WHERE d.organization_id = ? AND d.project_id IN ({placeholders})
                  AND d.document_kind LIKE ? AND d.lifecycle_state = 'active'
                ORDER BY d.updated_at DESC, d.document_id
                """,
                (
                    identity.organization_id,
                    *sorted(project_ids),
                    f"{DNA_KIND_PREFIX}%",
                ),
            ).fetchall()
        else:
            rows = []
    for row in rows:
        items.append(
            {
                "id": row["document_id"],
                "moduleKind": "stable_dna",
                "title": row["title"],
                "contentMarkdown": row["preview_text"],
                "summary": row["preview_text"],
                "status": "confirmed",
                "evidenceLevel": "internal",
                "sourceType": "knowledge_document",
                "sourceId": row["document_id"],
                "sourceLabel": row["title"],
                "observedAt": row["created_at"],
                "sourceCreatedAt": row["created_at"],
                "lastSeenAt": row["updated_at"],
                "validUntil": None,
                "confidenceScore": 1,
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "projectId": row["project_id"],
                "contentHash": row["content_hash"],
            }
        )
    return {
        "generatedAt": utc_now(),
        "stableItems": items,
        "evolvingItems": [],
        "gapItems": [],
        "riskItems": [],
        "itemCounts": {
            "stable_dna": len(items),
            "evolving_dna": 0,
            "gap_dna": 0,
            "risk_dna": 0,
        },
        "confirmedCount": len(items),
        "candidateCount": 0,
        "staleCount": 0,
        "latestRun": None,
        "updatedAt": max((item["updatedAt"] for item in items), default=None),
        "state": "ready" if items else "empty",
    }


def digital_assets(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    snapshot = repository.business_snapshot(identity)
    projects = [
        item
        for item in snapshot["projects"]
        if project_id is None or item["projectId"] == project_id
    ]
    if project_id and not projects:
        raise RepositoryError(404, "project_missing", "当前成员无法访问该项目")
    summaries: list[dict[str, Any]] = []
    for project in projects:
        pid = project["projectId"]
        documents = [item for item in snapshot["documents"] if item["projectId"] == pid]
        reports = [item for item in snapshot["reports"] if item["projectId"] == pid]
        answers = [item for item in snapshot["aiAnswers"] if item["projectId"] == pid]
        ready = sum(1 for item in documents if item["parseState"] == "ready")
        score = min(100, ready * 12 + len(reports) * 18 + len(answers) * 4)
        summaries.append(
            {
                "id": pid,
                "name": project["name"],
                "stage": project["lifecycleState"],
                "intro": project["summary"],
                "assetCompletionScore": score,
                "understandingScore": score,
                "understandingStatement": (
                    f"严格新版已确认 {ready} 份可用资料、{len(reports)} 份正式产物。"
                ),
                "depositedValueLevel": "已沉淀" if documents else "待沉淀",
                "nextValueSpace": "补充可解析资料并形成正式产物",
                "depositXp": ready + len(reports),
                "assetProfileType": "strict_authority",
                "secondaryProfileTypes": [],
                "maturityScore": score,
                "depositThickness": len(documents),
                "scoreMethodVersion": "strict-authority-v1",
                "scoreBreakdown": {
                    "deposited": len(documents),
                    "understood": ready,
                    "computable": len(answers),
                    "compounding": len(reports),
                    "structuralCompleteness": score,
                    "evidenceChain": 0,
                    "timeContinuity": 0,
                    "resultFeedbackLoop": 0,
                },
                "scoreRationale": [
                    f"资料 {len(documents)} 份",
                    f"解析就绪 {ready} 份",
                    f"正式产物 {len(reports)} 份",
                ],
                "materialMaturityRows": [],
                "assetStage": "authority_connected",
                "assetTrackTitle": "严格权威资料",
                "growthMode": "均衡成长",
                "stageProgress": score,
                "nextStage": "形成更多可追溯正式产物",
                "unlockedCapabilities": ["工作台问答"] if answers else [],
                "stageBlockers": [
                    item["title"]
                    for item in documents
                    if item["parseState"] in {"failed", "missing_source"}
                ],
                "nextBestDeposits": [],
                "assetMapNodes": [],
                "assetDimensionCount": 0,
                "strongestDimensions": [],
                "highValueSignals": [item["title"] for item in reports[:3]],
                "criticalGaps": [],
                "nextDeposits": [],
                "metrics": [
                    {"key": "documents", "label": "资料", "value": len(documents)},
                    {"key": "reports", "label": "正式产物", "value": len(reports)},
                    {"key": "answers", "label": "问答", "value": len(answers)},
                ],
                "emptyState": not (documents or reports or answers),
                "updatedAt": project["updatedAt"],
            }
        )
    generated_at = utc_now()
    dashboard_payload = {
        "generatedAt": generated_at,
        "pulse": {
            "headline": "严格新版权威资料沉淀",
            "daysAccompanied": 0,
            "weeklyNewFacts": 0,
            "weeklyNewDocuments": 0,
            "weeklyNewEvidenceCards": 0,
            "weeklyNewJudgments": 0,
            "digestionFunnel": [
                {
                    "key": "documents",
                    "label": "资料",
                    "value": len(snapshot["documents"]),
                },
                {
                    "key": "reports",
                    "label": "正式产物",
                    "value": len(snapshot["reports"]),
                },
                {
                    "key": "answers",
                    "label": "问答",
                    "value": len(snapshot["aiAnswers"]),
                },
            ],
            "activeOrganizations": [
                {
                    "clientId": item["id"],
                    "name": item["name"],
                    "assetProfileType": item["assetProfileType"],
                    "maturityScore": item["maturityScore"],
                    "depositThickness": item["depositThickness"],
                    "weeklyNewFacts": 0,
                    "weeklyNewDocuments": 0,
                    "weeklyNewEvidenceCards": 0,
                    "summary": item["understandingStatement"],
                }
                for item in summaries
            ],
            "learningHighlights": [],
            "assetAlerts": [],
        },
        "clients": summaries,
        "state": {
            "overall": "blocked",
            "availableResources": "ready",
            "blockedResources": [
                "asset_dimension_authority",
                "weekly_fact_projection",
                "evidence_card_projection",
                "judgment_projection",
            ],
            "message": "成熟度仅由严格资料、问答与正式产物计算；缺失维度未伪造成空成功。",
        },
    }
    if project_id:
        return {
            **summaries[0],
            "dimensions": [],
            "valueInsights": [],
            "depositSuggestions": [],
            "sourceMetrics": summaries[0]["metrics"],
            "aiNarrative": None,
            "generatedAt": generated_at,
            "state": dashboard_payload["state"],
        }
    return dashboard_payload


def _library_kind(kind: str) -> str:
    if kind not in ORGANIZATION_LIBRARY_KINDS:
        raise RepositoryError(422, "workbench_library_kind_invalid", "知识库资源类型无效")
    return f"{LIBRARY_KIND_PREFIX}{kind}"


def _library_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = _safe_manifest(_json(row["markdown_content"], {}))
    if not isinstance(payload, Mapping):
        payload = {}
    return {
        **dict(payload),
        "id": row["document_id"],
        "title": payload.get("title") or payload.get("name") or row["title"],
        "version": int(row["version"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_library(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    kind: str,
) -> list[dict[str, Any]]:
    document_kind = _library_kind(kind)
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            """
            SELECT d.*, v.markdown_content
            FROM knowledge_documents d
            JOIN document_versions v
              ON v.document_id = d.document_id AND v.version = d.current_version
            WHERE d.organization_id = ?
              AND d.project_assignment_state = 'unassigned'
              AND d.document_kind = ? AND d.lifecycle_state = 'active'
            ORDER BY d.updated_at DESC, d.document_id
            """,
            (identity.organization_id, document_kind),
        ).fetchall()
    return [_library_payload(row) for row in rows]


def library_item(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    kind: str,
    item_id: str,
) -> dict[str, Any]:
    document_kind = _library_kind(kind)
    with repository._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            """
            SELECT d.*, v.markdown_content
            FROM knowledge_documents d
            JOIN document_versions v
              ON v.document_id = d.document_id AND v.version = d.current_version
            WHERE d.organization_id = ? AND d.document_id = ?
              AND d.project_assignment_state = 'unassigned'
              AND d.document_kind = ? AND d.lifecycle_state = 'active'
            """,
            (identity.organization_id, item_id, document_kind),
        ).fetchone()
    if row is None:
        raise RepositoryError(404, "workbench_library_item_missing", "知识库条目不存在")
    return _library_payload(row)


def save_library_item(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    kind: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    item_id: str | None = None,
) -> dict[str, Any]:
    if not identity.is_admin:
        raise RepositoryError(403, "organization_library_write_forbidden", "仅组织管理员可修改组织知识库")
    document_kind = _library_kind(kind)
    safe_payload = _safe_manifest(dict(payload))
    if not isinstance(safe_payload, Mapping):
        safe_payload = {}
    title = str(
        safe_payload.get("title")
        or safe_payload.get("name")
        or safe_payload.get("label")
        or ""
    ).strip()
    if not title:
        raise RepositoryError(422, "workbench_library_title_required", "知识库条目标题不能为空")
    expected_raw = safe_payload.get("expectedVersion")
    expected = int(expected_raw or 0)
    normalized = {
        "kind": kind,
        "itemId": item_id,
        "payload": {
            key: value
            for key, value in safe_payload.items()
            if key not in {"expectedVersion", "version", "createdAt", "updatedAt"}
        },
    }
    command_type = f"workbench.library.{kind}.saved"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = None
            if item_id:
                row = connection.execute(
                    """
                    SELECT * FROM knowledge_documents
                    WHERE organization_id = ? AND document_id = ?
                      AND project_assignment_state = 'unassigned'
                      AND document_kind = ? AND lifecycle_state = 'active'
                    """,
                    (identity.organization_id, item_id, document_kind),
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "workbench_library_item_missing", "知识库条目不存在")
            now = utc_now()
            content = canonical_json(
                {
                    key: value
                    for key, value in safe_payload.items()
                    if key not in {"expectedVersion", "version", "createdAt", "updatedAt"}
                }
            )
            if row is None:
                if expected != 0:
                    raise RepositoryError(409, "workbench_library_version_conflict", "知识库条目尚未创建")
                document_id = new_id()
                document_version = 1
                aggregate_version = 1
                before_version = None
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        document_id, organization_id, project_id,
                        project_assignment_state, source_asset_id,
                        owner_membership_id, department_id, title, document_kind,
                        visibility_scope, parse_state, lifecycle_state,
                        current_version, version, created_at, updated_at
                    ) VALUES (?, ?, NULL, 'unassigned', NULL, ?, NULL, ?, ?,
                              'organization', 'ready', 'active', 1, 1, ?, ?)
                    """,
                    (
                        document_id,
                        identity.organization_id,
                        identity.membership_id,
                        title,
                        document_kind,
                        now,
                        now,
                    ),
                )
            else:
                current_version = int(row["version"])
                if expected != current_version:
                    raise RepositoryError(409, "workbench_library_version_conflict", "知识库条目已被其他成员更新")
                document_id = str(row["document_id"])
                document_version = int(row["current_version"]) + 1
                aggregate_version = current_version + 1
                before_version = current_version
                changed = connection.execute(
                    """
                    UPDATE knowledge_documents
                    SET title = ?, current_version = ?, version = version + 1,
                        parse_state = 'ready', updated_at = ?
                    WHERE organization_id = ? AND document_id = ? AND version = ?
                    """,
                    (
                        title,
                        document_version,
                        now,
                        identity.organization_id,
                        document_id,
                        current_version,
                    ),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(409, "workbench_library_version_conflict", "知识库条目已被其他成员更新")
            content_hash = sha256_text(content)
            connection.execute(
                """
                INSERT INTO document_versions (
                    document_version_id, organization_id, document_id, version,
                    content_hash, preview_text, markdown_content, section_count,
                    chunk_count, generator_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0,
                          'workbench-library-v1', ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    document_id,
                    document_version,
                    content_hash,
                    title[:1200],
                    content,
                    now,
                ),
            )
            result = {
                **_json(content, {}),
                "id": document_id,
                "title": title,
                "version": aggregate_version,
                "createdAt": row["created_at"] if row is not None else now,
                "updatedAt": now,
            }
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="knowledge_document",
                aggregate_id=document_id,
                expected_version=expected if row is not None else None,
                before_version=before_version,
                after_version=aggregate_version,
                payload=normalized,
                result=result,
                audit_summary={
                    "libraryKind": kind,
                    "title": title,
                    "contentHash": content_hash,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def delete_library_item(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    kind: str,
    item_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if not identity.is_admin:
        raise RepositoryError(403, "organization_library_write_forbidden", "仅组织管理员可修改组织知识库")
    document_kind = _library_kind(kind)
    expected = _expected_version(payload)
    normalized = {"kind": kind, "itemId": item_id}
    command_type = f"workbench.library.{kind}.archived"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = connection.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE organization_id = ? AND document_id = ?
                  AND project_assignment_state = 'unassigned'
                  AND document_kind = ? AND lifecycle_state = 'active'
                """,
                (identity.organization_id, item_id, document_kind),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "workbench_library_item_missing", "知识库条目不存在")
            if int(row["version"]) != expected:
                raise RepositoryError(409, "workbench_library_version_conflict", "知识库条目已被其他成员更新")
            now = utc_now()
            changed = connection.execute(
                """
                UPDATE knowledge_documents
                SET lifecycle_state = 'archived', version = version + 1, updated_at = ?
                WHERE organization_id = ? AND document_id = ? AND version = ?
                """,
                (now, identity.organization_id, item_id, expected),
            )
            if changed.rowcount != 1:
                raise RepositoryError(409, "workbench_library_version_conflict", "知识库条目已被其他成员更新")
            result = {"deleted": True, "id": item_id, "version": expected + 1}
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="knowledge_document",
                aggregate_id=item_id,
                expected_version=expected,
                before_version=expected,
                after_version=expected + 1,
                payload=normalized,
                result=result,
                audit_summary={"libraryKind": kind, "archived": True},
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def project_text_items(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        rows = connection.execute(
            """
            SELECT d.*, v.markdown_content, v.content_hash
            FROM knowledge_documents d
            JOIN document_versions v
              ON v.document_id = d.document_id AND v.version = d.current_version
            WHERE d.organization_id = ? AND d.project_id = ?
              AND d.document_kind LIKE ? AND d.lifecycle_state = 'active'
            ORDER BY d.updated_at DESC, d.document_id
            """,
            (
                identity.organization_id,
                project_id,
                f"{PROJECT_TEXT_KIND_PREFIX}%",
            ),
        ).fetchall()
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["document_kind"])[len(PROJECT_TEXT_KIND_PREFIX) :]
        items.setdefault(
            key,
            {
                "key": key,
                "documentId": row["document_id"],
                "title": row["title"],
                "markdownContent": row["markdown_content"],
                "contentHash": row["content_hash"],
                "version": int(row["version"]),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            },
        )
    return items


def save_project_text(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    key: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if key not in PROJECT_TEXT_KINDS and not key.startswith("dna_term:"):
        raise RepositoryError(422, "project_text_kind_invalid", "项目文本类型无效")
    markdown = str(payload.get("markdownContent") or "").strip()
    if not markdown:
        raise RepositoryError(422, "project_text_content_required", "项目文本不能为空")
    expected = int(payload.get("expectedVersion") or 0)
    title = str(
        payload.get("title")
        or PROJECT_TEXT_KINDS.get(key)
        or "DNA 术语"
    ).strip()
    normalized = {
        "projectId": project_id,
        "key": key,
        "title": title,
        "markdownContent": markdown,
    }
    command_type = f"workbench.project_text.{key.replace(':', '.')}.saved"
    document_kind = f"{PROJECT_TEXT_KIND_PREFIX}{key}"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            _require_project_editor(repository, connection, identity, project_id)
            row = connection.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE organization_id = ? AND project_id = ?
                  AND document_kind = ? AND lifecycle_state = 'active'
                ORDER BY updated_at DESC, document_id LIMIT 1
                """,
                (identity.organization_id, project_id, document_kind),
            ).fetchone()
            now = utc_now()
            if row is None:
                if expected != 0:
                    raise RepositoryError(409, "project_text_version_conflict", "项目文本尚未创建")
                document_id = new_id()
                document_version = 1
                aggregate_version = 1
                before_version = None
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        document_id, organization_id, project_id,
                        project_assignment_state, source_asset_id,
                        owner_membership_id, department_id, title, document_kind,
                        visibility_scope, parse_state, lifecycle_state,
                        current_version, version, created_at, updated_at
                    ) VALUES (?, ?, ?, 'assigned', NULL, ?, NULL, ?, ?,
                              'participants', 'ready', 'active', 1, 1, ?, ?)
                    """,
                    (
                        document_id,
                        identity.organization_id,
                        project_id,
                        identity.membership_id,
                        title,
                        document_kind,
                        now,
                        now,
                    ),
                )
            else:
                current_version = int(row["version"])
                if expected != current_version:
                    raise RepositoryError(409, "project_text_version_conflict", "项目文本已被其他成员更新")
                document_id = str(row["document_id"])
                document_version = int(row["current_version"]) + 1
                aggregate_version = current_version + 1
                before_version = current_version
                changed = connection.execute(
                    """
                    UPDATE knowledge_documents
                    SET title = ?, current_version = ?, version = version + 1,
                        parse_state = 'ready', updated_at = ?
                    WHERE organization_id = ? AND document_id = ? AND version = ?
                    """,
                    (
                        title,
                        document_version,
                        now,
                        identity.organization_id,
                        document_id,
                        current_version,
                    ),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(409, "project_text_version_conflict", "项目文本已被其他成员更新")
            content_hash = sha256_text(markdown)
            connection.execute(
                """
                INSERT INTO document_versions (
                    document_version_id, organization_id, document_id, version,
                    content_hash, preview_text, markdown_content, section_count,
                    chunk_count, generator_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0,
                          'workbench-project-text-v1', ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    document_id,
                    document_version,
                    content_hash,
                    " ".join(markdown.split())[:1200],
                    markdown,
                    now,
                ),
            )
            result = {
                "key": key,
                "documentId": document_id,
                "title": title,
                "markdownContent": markdown,
                "contentHash": content_hash,
                "version": aggregate_version,
                "createdAt": row["created_at"] if row is not None else now,
                "updatedAt": now,
            }
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="knowledge_document",
                aggregate_id=document_id,
                expected_version=expected if row is not None else None,
                before_version=before_version,
                after_version=aggregate_version,
                payload=normalized,
                result=result,
                audit_summary={"projectId": project_id, "textKind": key, "contentHash": content_hash},
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def archive_project_text(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    key: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if key not in PROJECT_TEXT_KINDS and not key.startswith("dna_term:"):
        raise RepositoryError(422, "project_text_kind_invalid", "项目文本类型无效")
    expected = _expected_version(payload)
    normalized = {"projectId": project_id, "key": key}
    command_type = f"workbench.project_text.{key.replace(':', '.')}.archived"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            _require_project_editor(repository, connection, identity, project_id)
            row = connection.execute(
                """
                SELECT * FROM knowledge_documents
                WHERE organization_id = ? AND project_id = ? AND document_kind = ?
                  AND lifecycle_state = 'active'
                ORDER BY updated_at DESC, document_id LIMIT 1
                """,
                (
                    identity.organization_id,
                    project_id,
                    f"{PROJECT_TEXT_KIND_PREFIX}{key}",
                ),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "project_text_missing", "项目文本不存在")
            if int(row["version"]) != expected:
                raise RepositoryError(409, "project_text_version_conflict", "项目文本已被其他成员更新")
            now = utc_now()
            connection.execute(
                """
                UPDATE knowledge_documents
                SET lifecycle_state = 'archived', version = version + 1, updated_at = ?
                WHERE organization_id = ? AND document_id = ? AND version = ?
                """,
                (now, identity.organization_id, row["document_id"], expected),
            )
            result = {
                "ok": True,
                "key": key,
                "documentId": row["document_id"],
                "version": expected + 1,
            }
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="knowledge_document",
                aggregate_id=str(row["document_id"]),
                expected_version=expected,
                before_version=expected,
                after_version=expected + 1,
                payload=normalized,
                result=result,
                audit_summary={"projectId": project_id, "textKind": key, "archived": True},
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _analysis_job_row(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    job_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT pa.*,
               COALESCE(d.project_id, sa.project_id, a.project_id) AS project_id,
               d.title AS document_title,
               sa.file_name AS source_title,
               sa.content_hash AS source_content_hash,
               a.question AS answer_question,
               a.ai_answer_id
        FROM processing_attempts pa
        LEFT JOIN knowledge_documents d
          ON d.organization_id = pa.organization_id
         AND d.document_id = pa.document_id
        LEFT JOIN source_assets sa
          ON sa.organization_id = pa.organization_id
         AND sa.source_asset_id = pa.source_asset_id
        LEFT JOIN ai_answers a
          ON a.organization_id = pa.organization_id
         AND a.ai_answer_id = pa.processing_attempt_id
        WHERE pa.organization_id = ? AND pa.processing_attempt_id = ?
        """,
        (identity.organization_id, job_id),
    ).fetchone()


def _analysis_job_payload(row: sqlite3.Row) -> dict[str, Any]:
    project_id = str(row["project_id"] or "")
    job_id = str(row["processing_attempt_id"])
    state = str(row["state"])
    status_map = {
        "processing": "running",
        "partial": "completed",
    }
    status = status_map.get(state, state)
    progress = 100 if state in {"completed", "partial", "failed", "cancelled"} else 50 if state == "processing" else 0
    return {
        "id": row["processing_attempt_id"],
        "jobType": row["processing_kind"],
        "clientId": project_id,
        "scopeType": "client",
        "scopeId": project_id,
        "status": status,
        "priority": "normal",
        "triggerType": (
            "workbench_ai_answer"
            if row["ai_answer_id"]
            else "strict_processing_attempt"
        ),
        "intentProfile": "client_overview",
        "question": (
            row["answer_question"]
            or row["document_title"]
            or row["source_title"]
            or row["processing_kind"]
        ),
        "sourceSnapshot": (
            row["ai_answer_id"]
            or row["document_id"]
            or row["source_asset_id"]
            or ""
        ),
        "sourceSnapshotHash": (
            str(row["source_content_hash"] or "")
        ),
        "dedupeKey": f"{row['processing_kind']}:{row['document_id'] or row['source_asset_id'] or job_id}",
        "featureFlags": {},
        "progress": progress,
        "stageLabel": row["processing_kind"],
        "runLogId": row["processing_attempt_id"],
        "error": row["error_message"] or None,
        "lockedBy": None,
        "lockedAt": row["started_at"],
        "lockExpiresAt": None,
        "attemptCount": int(row["attempt_no"]),
        "lastError": row["error_message"] or None,
        "createdAt": row["created_at"],
        "updatedAt": row["finished_at"] or row["started_at"] or row["created_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "authorityType": "processing_attempts",
    }


def register_analysis_job(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    answer_id = str(payload.get("answerId") or "").strip()
    project_id = str(payload.get("projectId") or "").strip()
    job_type = str(payload.get("jobType") or "workbench_analysis").strip()
    if not answer_id or not project_id:
        raise RepositoryError(
            422,
            "analysis_job_identity_required",
            "分析任务缺少 answerId 或固定项目 WorkspaceContext",
        )
    normalized = {
        "answerId": answer_id,
        "projectId": project_id,
        "jobType": job_type,
    }
    command_type = "workbench.analysis_job.completed"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project(repository, connection, identity, project_id)
            answer = connection.execute(
                """
                SELECT ai_answer_id, project_id
                FROM ai_answers
                WHERE organization_id = ? AND ai_answer_id = ?
                  AND lifecycle_state = 'active'
                """,
                (identity.organization_id, answer_id),
            ).fetchone()
            if answer is None or str(answer["project_id"] or "") != project_id:
                raise RepositoryError(
                    404,
                    "analysis_answer_missing",
                    "分析任务对应的工作台回答不存在",
                )
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            collision = _analysis_job_row(connection, identity, answer_id)
            if collision is not None:
                raise RepositoryError(
                    409,
                    "analysis_job_identity_conflict",
                    "分析任务 ID 已被其他处理记录占用",
                )
            now = utc_now()
            source_asset_id = new_id()
            connection.execute(
                """
                INSERT INTO source_assets (
                    source_asset_id, organization_id, project_id,
                    storage_object_id, file_name, media_type, byte_size,
                    content_hash, source_kind, source_locator,
                    lifecycle_state, created_by_membership_id, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, NULL, '工作台分析任务',
                          'application/vnd.yiyu.workbench-analysis+json',
                          0, ?, 'workbench_analysis_job', '', 'active',
                          ?, 1, ?, ?)
                """,
                (
                    source_asset_id,
                    identity.organization_id,
                    project_id,
                    sha256_text(canonical_json(normalized)),
                    identity.membership_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO processing_attempts (
                    processing_attempt_id, organization_id,
                    source_asset_id, document_id, processing_kind,
                    state, attempt_no, error_code, error_message,
                    started_at, finished_at, created_at
                ) VALUES (?, ?, ?, NULL, ?, 'completed', 1, '', '', ?, ?, ?)
                """,
                (
                    answer_id,
                    identity.organization_id,
                    source_asset_id,
                    job_type,
                    now,
                    now,
                    now,
                ),
            )
            row = _analysis_job_row(connection, identity, answer_id)
            if row is None:
                raise RepositoryError(
                    500,
                    "analysis_job_create_lost",
                    "分析任务保存后无法读取",
                )
            result = _analysis_job_payload(row)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="processing_attempt",
                aggregate_id=answer_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "answerId": answer_id,
                    "jobType": job_type,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def analysis_job_detail(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    job_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = _analysis_job_row(connection, identity, job_id)
        if row is None:
            raise RepositoryError(404, "analysis_job_missing", "分析任务不存在")
        project_id = str(row["project_id"] or "")
        if not project_id:
            raise RepositoryError(
                409,
                "analysis_job_workspace_missing",
                "该处理记录没有固定项目 WorkspaceContext",
            )
        _require_project(repository, connection, identity, project_id)
    return _analysis_job_payload(row)


def analysis_job_stages(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    job_id: str,
) -> list[dict[str, Any]]:
    job = analysis_job_detail(repository, identity, job_id=job_id)
    status = str(job["status"])
    stage_status = (
        "running"
        if status == "running"
        else "failed"
        if status == "failed"
        else "queued"
        if status == "queued"
        else "completed"
    )
    return [
        {
            "id": job_id,
            "jobId": job_id,
            "stageName": job["jobType"],
            "status": stage_status,
            "provider": "strict_processing_attempt",
            "modelName": None,
            "lane": "cloud_final",
            "cacheKey": None,
            "cacheHit": False,
            "degraded": status == "completed" and bool(job.get("lastError")),
            "evidenceCount": 0,
            "topicCount": 0,
            "conflictCount": 0,
            "contextTimeRange": None,
            "metrics": {"attempt": job["attemptCount"], "progress": job["progress"]},
            "detail": job.get("lastError"),
            "correlationId": job_id,
            "startedAt": job.get("startedAt"),
            "finishedAt": job.get("finishedAt"),
            "createdAt": job["createdAt"],
            "updatedAt": job["updatedAt"],
        }
    ]


def register_context_refresh(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    normalized = {
        "projectId": project_id,
        "state": str(payload.get("state") or "empty"),
        "counts": {
            str(key): int(value)
            for key, value in dict(payload.get("counts") or {}).items()
            if isinstance(value, (int, float))
        },
        "materialPackHash": str(payload.get("materialPackHash") or ""),
    }
    command_type = "workbench.context_refreshed"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project(repository, connection, identity, project_id)
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            now = utc_now()
            source_asset_id = new_id()
            attempt_id = new_id()
            content_hash = sha256_text(canonical_json(normalized))
            connection.execute(
                """
                INSERT INTO source_assets (
                    source_asset_id, organization_id, project_id,
                    storage_object_id, file_name, media_type, byte_size,
                    content_hash, source_kind, source_locator,
                    lifecycle_state, created_by_membership_id, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, NULL, '项目上下文刷新回执',
                          'application/vnd.yiyu.context-refresh+json',
                          0, ?, 'workbench_context_refresh', '', 'active',
                          ?, 1, ?, ?)
                """,
                (
                    source_asset_id,
                    identity.organization_id,
                    project_id,
                    content_hash,
                    identity.membership_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO processing_attempts (
                    processing_attempt_id, organization_id,
                    source_asset_id, document_id, processing_kind,
                    state, attempt_no, error_code, error_message,
                    started_at, finished_at, created_at
                ) VALUES (?, ?, ?, NULL, 'context_refresh', 'completed',
                          1, '', '', ?, ?, ?)
                """,
                (
                    attempt_id,
                    identity.organization_id,
                    source_asset_id,
                    now,
                    now,
                    now,
                ),
            )
            result = {
                "id": attempt_id,
                "clientId": project_id,
                "status": "completed",
                "state": normalized["state"],
                "counts": normalized["counts"],
                "materialPackHash": normalized["materialPackHash"],
                "receiptHash": content_hash,
                "createdAt": now,
                "authorityType": "processing_attempts",
            }
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="processing_attempt",
                aggregate_id=attempt_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "state": normalized["state"],
                    "counts": normalized["counts"],
                    "materialPackHash": normalized["materialPackHash"],
                    "receiptHash": content_hash,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _goal_payload(row: sqlite3.Row) -> dict[str, Any]:
    attributes = _json(row["attributes_json"], {})
    return {
        "id": row["task_id"],
        "clientId": row["project_id"],
        "title": row["title"],
        "quarter": str(attributes.get("quarter") or ""),
        "progress": int(attributes.get("progress") or 0),
        "ownerName": str(attributes.get("ownerName") or ""),
        "status": row["lifecycle_state"],
        "version": int(row["version"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "authorityType": "task_records(task_kind=goal)",
    }


def list_project_goals(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        rows = connection.execute(
            """
            SELECT *
            FROM task_records
            WHERE organization_id = ? AND project_id = ?
              AND task_kind = 'goal'
              AND lifecycle_state != 'archived'
            ORDER BY created_at, task_id
            """,
            (identity.organization_id, project_id),
        ).fetchall()
    return [_goal_payload(row) for row in rows]


def create_project_goal(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise RepositoryError(422, "goal_title_required", "请输入目标标题")
    try:
        progress = int(payload.get("progress") or 0)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(
            422,
            "goal_progress_invalid",
            "目标进度必须是 0 到 100 的整数",
        ) from exc
    if progress < 0 or progress > 100:
        raise RepositoryError(
            422,
            "goal_progress_invalid",
            "目标进度必须是 0 到 100 的整数",
        )
    attributes = {
        "quarter": str(payload.get("quarter") or "").strip(),
        "progress": progress,
        "ownerName": str(payload.get("ownerName") or "").strip(),
    }
    normalized = {
        "projectId": project_id,
        "title": title,
        **attributes,
    }
    command_type = "workbench.project_goal.created"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project_editor(repository, connection, identity, project_id)
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            task_id = new_id()
            now = utc_now()
            connection.execute(
                """
                INSERT INTO task_records (
                    task_id, organization_id, project_id, title, description,
                    created_by_membership_id, priority, lifecycle_state,
                    task_kind, visibility_scope, duration_minutes,
                    completion_note, source_type, source_id, attributes_json,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '', ?, 'normal', 'todo', 'goal',
                          'participants', 0, '', 'workbench_goal', NULL, ?,
                          1, ?, ?)
                """,
                (
                    task_id,
                    identity.organization_id,
                    project_id,
                    title,
                    identity.membership_id,
                    canonical_json(attributes),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO task_collaborators (
                    task_id, organization_id, membership_id,
                    collaborator_role, inbox_state, order_index,
                    return_reason, handled_at, version, created_at, updated_at
                ) VALUES (?, ?, ?, 'owner', 'accepted', 0, '', ?, 1, ?, ?)
                """,
                (
                    task_id,
                    identity.organization_id,
                    identity.membership_id,
                    now,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO task_activity_events (
                    task_activity_id, organization_id, task_id,
                    actor_membership_id, event_type, payload_json, happened_at
                ) VALUES (?, ?, ?, ?, 'goal.created', ?, ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    task_id,
                    identity.membership_id,
                    canonical_json(attributes),
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM task_records WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            result = _goal_payload(row)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="task",
                aggregate_id=task_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "taskKind": "goal",
                    "progress": progress,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def project_structure(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    workspace = project_workspace(repository, identity, project_id=project_id)
    tasks = workspace["tasks"]
    task_ids = {str(item["taskId"]) for item in tasks}
    event_ids = {str(item["eventLineId"]) for item in workspace["eventLines"]}
    event_id_by_task: dict[str, str] = {}
    if task_ids and event_ids:
        task_placeholders = ",".join("?" for _ in task_ids)
        event_placeholders = ",".join("?" for _ in event_ids)
        with repository._connection() as connection:  # noqa: SLF001
            event_id_by_task = {
                str(row["task_id"]): str(row["event_line_id"])
                for row in connection.execute(
                    f"""
                    SELECT task_id, event_line_id
                    FROM event_line_task_links
                    WHERE organization_id = ? AND link_state = 'active'
                      AND task_id IN ({task_placeholders})
                      AND event_line_id IN ({event_placeholders})
                    """,
                    (
                        identity.organization_id,
                        *sorted(task_ids),
                        *sorted(event_ids),
                    ),
                ).fetchall()
            }
    modules = []
    flows = []
    for line in workspace["eventLines"]:
        line_id = str(line["eventLineId"])
        linked_tasks = [
            task
            for task in tasks
            if event_id_by_task.get(str(task["taskId"])) == line_id
        ]
        modules.append(
            {
                "id": line_id,
                "clientId": project_id,
                "name": line["name"],
                "alias": None,
                "goal": line.get("goal") or "",
                "description": line.get("background") or "",
                "ownerName": None,
                "deliverables": [item.get("title") or "" for item in linked_tasks],
                "keywords": [],
                "templateTasksJson": None,
                "createdAt": line.get("createdAt") or line.get("updatedAt"),
                "updatedAt": line.get("updatedAt"),
                "authorityType": "event_line_records",
                "version": line.get("version"),
            }
        )
        for task in linked_tasks:
            flows.append(
                {
                    "id": task["taskId"],
                    "clientId": project_id,
                    "moduleId": line_id,
                    "moduleName": line["name"],
                    "name": task.get("title") or "",
                    "description": task.get("description") or "",
                    "scenario": task.get("lifecycleState") or "todo",
                    "triggerCondition": "",
                    "steps": [task.get("description") or task.get("title") or ""],
                    "inputs": [],
                    "outputs": [],
                    "collaborators": [],
                    "riskPoints": [],
                    "createdAt": task.get("createdAt") or task.get("updatedAt"),
                    "updatedAt": task.get("updatedAt"),
                    "authorityType": "task_records+event_line_task_links",
                    "version": task.get("version"),
                }
            )
    with repository._connection() as connection:  # noqa: SLF001
        plan_rows = connection.execute(
            """
            SELECT * FROM organization_plans
            WHERE organization_id = ? AND status != 'archived'
              AND json_extract(attributes_json, '$.projectId') = ?
              AND json_extract(attributes_json, '$.orgModelKind')
                  IN ('project_module', 'project_flow')
            ORDER BY created_at, plan_id
            """,
            (identity.organization_id, project_id),
        ).fetchall()
    custom_modules: list[dict[str, Any]] = []
    custom_flows: list[dict[str, Any]] = []
    for row in plan_rows:
        attributes = _json(row["attributes_json"], {})
        common = {
            "id": row["plan_id"],
            "clientId": project_id,
            "name": attributes.get("name") or row["summary"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "authorityType": "organization_plans",
            "version": int(row["version"]),
        }
        if attributes.get("orgModelKind") == "project_module":
            custom_modules.append(
                {
                    **common,
                    "alias": attributes.get("alias"),
                    "goal": attributes.get("goal") or "",
                    "description": attributes.get("description") or row["summary"],
                    "ownerName": attributes.get("ownerName"),
                    "deliverables": list(attributes.get("deliverables") or []),
                    "keywords": list(attributes.get("keywords") or []),
                    "templateTasksJson": attributes.get("templateTasksJson"),
                }
            )
        else:
            custom_flows.append(
                {
                    **common,
                    "moduleId": attributes.get("moduleId") or "",
                    "moduleName": attributes.get("moduleName"),
                    "description": attributes.get("description") or row["summary"],
                    "scenario": attributes.get("scenario") or "",
                    "triggerCondition": attributes.get("triggerCondition") or "",
                    "steps": list(attributes.get("steps") or []),
                    "inputs": list(attributes.get("inputs") or []),
                    "outputs": list(attributes.get("outputs") or []),
                    "collaborators": list(attributes.get("collaborators") or []),
                    "riskPoints": list(attributes.get("riskPoints") or []),
                }
            )
    modules.extend(custom_modules)
    module_names = {
        str(item.get("id")): str(item.get("name") or "")
        for item in modules
    }
    for flow in custom_flows:
        if not flow.get("moduleName"):
            flow["moduleName"] = module_names.get(str(flow.get("moduleId"))) or None
    flows.extend(custom_flows)
    return {"modules": modules, "flows": flows}


def _project_structure_item(
    row: sqlite3.Row,
) -> dict[str, Any]:
    attributes = _json(row["attributes_json"], {})
    project_id = str(attributes.get("projectId") or "")
    common = {
        "id": row["plan_id"],
        "clientId": project_id,
        "name": attributes.get("name") or row["summary"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "authorityType": "organization_plans",
        "version": int(row["version"]),
    }
    if attributes.get("orgModelKind") == "project_module":
        return {
            **common,
            "alias": attributes.get("alias"),
            "goal": attributes.get("goal") or "",
            "description": attributes.get("description") or row["summary"],
            "ownerName": attributes.get("ownerName"),
            "deliverables": list(attributes.get("deliverables") or []),
            "keywords": list(attributes.get("keywords") or []),
            "templateTasksJson": attributes.get("templateTasksJson"),
        }
    return {
        **common,
        "moduleId": attributes.get("moduleId") or "",
        "moduleName": attributes.get("moduleName"),
        "description": attributes.get("description") or row["summary"],
        "scenario": attributes.get("scenario") or "",
        "triggerCondition": attributes.get("triggerCondition") or "",
        "steps": list(attributes.get("steps") or []),
        "inputs": list(attributes.get("inputs") or []),
        "outputs": list(attributes.get("outputs") or []),
        "collaborators": list(attributes.get("collaborators") or []),
        "riskPoints": list(attributes.get("riskPoints") or []),
    }


def write_project_structure_item(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    item_kind: str,
    item_id: str | None,
    payload: Mapping[str, Any],
    idempotency_key: str,
    archive: bool = False,
) -> dict[str, Any]:
    if item_kind not in {"project_module", "project_flow"}:
        raise RepositoryError(422, "project_structure_kind_invalid", "项目结构类型无效")
    title = str(payload.get("name") or "").strip()
    if not archive and not title:
        raise RepositoryError(422, "project_structure_name_required", "项目结构名称不能为空")
    expected = _expected_version(payload) if item_id else None
    command_type = (
        f"workbench.{item_kind}.archived"
        if archive
        else f"workbench.{item_kind}.updated"
        if item_id
        else f"workbench.{item_kind}.created"
    )
    normalized = {
        "projectId": project_id,
        "itemKind": item_kind,
        "itemId": item_id,
        "archive": archive,
        "expectedVersion": expected,
        "fields": {
            key: value
            for key, value in payload.items()
            if key not in {"expectedVersion", "expected_version", "version"}
        },
    }
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project_editor(repository, connection, identity, project_id)
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            now = utc_now()
            if item_id:
                row = connection.execute(
                    """
                    SELECT * FROM organization_plans
                    WHERE organization_id = ? AND plan_id = ?
                      AND json_extract(attributes_json, '$.projectId') = ?
                      AND json_extract(attributes_json, '$.orgModelKind') = ?
                    """,
                    (
                        identity.organization_id,
                        item_id,
                        project_id,
                        item_kind,
                    ),
                ).fetchone()
                if row is None:
                    raise RepositoryError(
                        404,
                        "project_structure_item_missing",
                        "项目结构记录不存在",
                    )
                before = int(row["version"])
                if expected != before:
                    raise RepositoryError(
                        409,
                        "project_structure_version_conflict",
                        "项目结构已被其他成员更新",
                    )
                attributes = _json(row["attributes_json"], {})
                attributes.update(normalized["fields"])
                attributes.update(
                    {
                        "orgModelKind": item_kind,
                        "projectId": project_id,
                    }
                )
                next_status = "archived" if archive else "active"
                cursor = connection.execute(
                    """
                    UPDATE organization_plans
                    SET summary = ?, status = ?, attributes_json = ?,
                        version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND plan_id = ? AND version = ?
                    """,
                    (
                        str(attributes.get("description") or attributes.get("name") or ""),
                        next_status,
                        canonical_json(attributes),
                        now,
                        identity.organization_id,
                        item_id,
                        before,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "project_structure_version_conflict",
                        "项目结构已被其他成员更新",
                    )
                after = before + 1
                saved = connection.execute(
                    "SELECT * FROM organization_plans WHERE plan_id = ?",
                    (item_id,),
                ).fetchone()
            else:
                before = None
                after = 1
                item_id = new_id()
                attributes = {
                    **normalized["fields"],
                    "orgModelKind": item_kind,
                    "projectId": project_id,
                }
                connection.execute(
                    """
                    INSERT INTO organization_plans (
                        plan_id, organization_id, department_id, period_label,
                        owner_membership_id, summary, status, attributes_json,
                        version, created_at, updated_at
                    ) VALUES (?, ?, NULL, 'project_structure', ?, ?, 'active',
                              ?, 1, ?, ?)
                    """,
                    (
                        item_id,
                        identity.organization_id,
                        identity.membership_id,
                        str(attributes.get("description") or title),
                        canonical_json(attributes),
                        now,
                        now,
                    ),
                )
                saved = connection.execute(
                    "SELECT * FROM organization_plans WHERE plan_id = ?",
                    (item_id,),
                ).fetchone()
            result = (
                {"status": "archived", "id": item_id, "version": after}
                if archive
                else _project_structure_item(saved)
            )
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type=item_kind,
                aggregate_id=item_id,
                expected_version=expected,
                before_version=before,
                after_version=after,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "itemKind": item_kind,
                    "archived": archive,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _clarification_payload(row: sqlite3.Row) -> dict[str, Any]:
    source = _json(row["source_payload_json"], {})
    return {
        "id": row["intelligence_id"],
        "clientId": row["project_id"],
        "basedOnRev": int(source.get("basedOnRev") or 0),
        "dimension": source.get("dimension") or "organization_intro",
        "question": source.get("question") or row["title"],
        "askedBy": source.get("askedBy") or "",
        "answer": source.get("answer") or row["summary"],
        "answeredByUserId": row["created_by_membership_id"],
        "answeredByDisplayName": source.get("answeredByDisplayName") or "",
        "answeredAt": row["created_at"],
        "resultedInRev": source.get("resultedInRev"),
        "status": {
            "candidate": "pending",
            "inbox": "pending",
            "accepted": "applied",
            "returned": "discarded",
            "archived": "discarded",
        }.get(str(row["status"]), "pending"),
        "version": int(row["version"]),
    }


def narrative_clarifications(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        rows = connection.execute(
            """
            SELECT * FROM intelligence_records
            WHERE organization_id = ? AND project_id = ?
              AND record_kind = 'narrative_clarification'
              AND status != 'archived'
            ORDER BY created_at DESC, intelligence_id
            """,
            (identity.organization_id, project_id),
        ).fetchall()
    return {"clarifications": [_clarification_payload(row) for row in rows]}


def create_narrative_clarification(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    answer = str(payload.get("answer") or "").strip()
    dimension = str(payload.get("dimension") or "").strip()
    if not answer or not dimension:
        raise RepositoryError(422, "narrative_clarification_required", "澄清维度和回答不能为空")
    normalized = {
        "projectId": project_id,
        "dimension": dimension,
        "question": str(payload.get("question") or "").strip(),
        "answer": answer,
        "basedOnRev": int(payload.get("basedOnRev") or 0),
    }
    command_type = "workbench.narrative_clarification.created"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project_editor(repository, connection, identity, project_id)
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            if normalized["basedOnRev"] <= 0:
                narrative = connection.execute(
                    """
                    SELECT latest_version FROM narrative_outputs
                    WHERE organization_id = ? AND project_id = ?
                      AND lifecycle_state != 'archived'
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (identity.organization_id, project_id),
                ).fetchone()
                normalized["basedOnRev"] = int(narrative["latest_version"]) if narrative else 0
            now = utc_now()
            clarification_id = new_id()
            source = {
                **normalized,
                "askedBy": identity.membership_id,
                "answeredByDisplayName": "",
                "resultedInRev": None,
            }
            connection.execute(
                """
                INSERT INTO intelligence_records (
                    intelligence_id, organization_id, project_id, title, summary,
                    source_url, record_kind, status, visibility_scope,
                    created_by_membership_id, source_payload_json, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', 'narrative_clarification',
                          'candidate', 'participants', ?, ?, 1, ?, ?)
                """,
                (
                    clarification_id,
                    identity.organization_id,
                    project_id,
                    normalized["question"] or f"{dimension} 澄清",
                    answer,
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
                    clarification_id,
                    normalized["question"] or f"{dimension} 澄清",
                    answer,
                    identity.membership_id,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM intelligence_records WHERE intelligence_id = ?",
                (clarification_id,),
            ).fetchone()
            result = _clarification_payload(row)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="intelligence",
                aggregate_id=clarification_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "dimension": dimension,
                    "basedOnRev": normalized["basedOnRev"],
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _suggestion_payload(row: sqlite3.Row) -> dict[str, Any]:
    source = _json(row["source_payload_json"], {})
    return {
        "fingerprint": source.get("fingerprint") or row["title"],
        "actor": source.get("actor") or "",
        "suggestionText": source.get("suggestionText") or row["summary"],
        "sourceDocTitle": source.get("sourceDocTitle") or "",
        "sourceDocId": source.get("sourceDocId") or "",
        "createdAt": row["created_at"],
        "action": source.get("action") or "promoted",
        "id": row["intelligence_id"],
        "version": int(row["version"]),
    }


def suggestion_log(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        rows = connection.execute(
            """
            SELECT * FROM intelligence_records
            WHERE organization_id = ? AND project_id = ?
              AND record_kind = 'suggestion_action' AND status != 'archived'
            ORDER BY created_at DESC, intelligence_id
            """,
            (identity.organization_id, project_id),
        ).fetchall()
    result = {
        "clientId": project_id,
        "promoted": [],
        "completed": [],
        "dismissed": [],
    }
    for row in rows:
        item = _suggestion_payload(row)
        action = str(item.pop("action"))
        result[action if action in result else "promoted"].append(item)
    return result


def write_suggestion_log(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    fingerprint: str | None = None,
    archive: bool = False,
) -> dict[str, Any]:
    normalized_fingerprint = str(fingerprint or payload.get("fingerprint") or "").strip()
    action = str(payload.get("action") or "promoted").strip()
    if not normalized_fingerprint:
        raise RepositoryError(422, "suggestion_fingerprint_required", "建议指纹不能为空")
    if not archive and action not in {"promoted", "completed", "dismissed"}:
        raise RepositoryError(422, "suggestion_action_invalid", "建议处置动作无效")
    normalized = {
        "projectId": project_id,
        "fingerprint": normalized_fingerprint,
        "action": action,
        "actor": str(payload.get("actor") or ""),
        "suggestionText": str(payload.get("suggestionText") or ""),
        "sourceDocTitle": str(payload.get("sourceDocTitle") or ""),
        "sourceDocId": str(payload.get("sourceDocId") or ""),
        "archive": archive,
    }
    command_type = (
        "workbench.suggestion_action.archived"
        if archive
        else "workbench.suggestion_action.saved"
    )
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project_editor(repository, connection, identity, project_id)
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = connection.execute(
                """
                SELECT * FROM intelligence_records
                WHERE organization_id = ? AND project_id = ?
                  AND record_kind = 'suggestion_action'
                  AND json_extract(source_payload_json, '$.fingerprint') = ?
                ORDER BY updated_at DESC LIMIT 1
                """,
                (identity.organization_id, project_id, normalized_fingerprint),
            ).fetchone()
            now = utc_now()
            if archive:
                if row is None or row["status"] == "archived":
                    raise RepositoryError(404, "suggestion_log_missing", "建议处置记录不存在")
                before = int(row["version"])
                connection.execute(
                    """
                    UPDATE intelligence_records
                    SET status = 'archived', version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND intelligence_id = ? AND version = ?
                    """,
                    (now, identity.organization_id, row["intelligence_id"], before),
                )
                aggregate_id = str(row["intelligence_id"])
                after = before + 1
                result = {"ok": True}
            elif row is None:
                aggregate_id = new_id()
                before = None
                after = 1
                connection.execute(
                    """
                    INSERT INTO intelligence_records (
                        intelligence_id, organization_id, project_id, title, summary,
                        source_url, record_kind, status, visibility_scope,
                        created_by_membership_id, source_payload_json, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '', 'suggestion_action', 'accepted',
                              'participants', ?, ?, 1, ?, ?)
                    """,
                    (
                        aggregate_id,
                        identity.organization_id,
                        project_id,
                        normalized_fingerprint,
                        normalized["suggestionText"],
                        identity.membership_id,
                        canonical_json(normalized),
                        now,
                        now,
                    ),
                )
                result = {"ok": True}
            else:
                aggregate_id = str(row["intelligence_id"])
                before = int(row["version"])
                after = before + 1
                connection.execute(
                    """
                    UPDATE intelligence_records
                    SET title = ?, summary = ?, status = 'accepted',
                        source_payload_json = ?, version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND intelligence_id = ? AND version = ?
                    """,
                    (
                        normalized_fingerprint,
                        normalized["suggestionText"],
                        canonical_json(normalized),
                        now,
                        identity.organization_id,
                        aggregate_id,
                        before,
                    ),
                )
                result = {"ok": True}
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="intelligence",
                aggregate_id=aggregate_id,
                expected_version=before,
                before_version=before,
                after_version=after,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "fingerprint": normalized_fingerprint,
                    "action": action,
                    "archived": archive,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


_VALUE_VALIDATION_QUESTIONS = [
    {"id": f"wvq_{index:02d}", "prompt": prompt}
    for index, prompt in enumerate(
        (
            "这个客户是谁？",
            "核心业务是什么？",
            "最新战略是什么？",
            "当前合作推进到哪了？",
            "现在最大的风险是什么？",
            "下一步建议先做什么？",
            "系统内有哪些已批准正式判断？",
            "这个判断有什么证据？",
            "最近会议留下了哪些行动项？",
            "还有哪些资料缺口？",
        ),
        start=1,
    )
]


def _value_validation_summary(
    session_id: str,
    project_id: str,
    entries: Mapping[str, Any],
) -> dict[str, Any]:
    completed = len(entries)
    usable = sum(bool((item or {}).get("usableAnswer")) for item in entries.values())
    retry = sum(bool((item or {}).get("retryBannerShown")) for item in entries.values())
    timed = [
        item
        for item in entries.values()
        if (item or {}).get("manualBaselineMinutes") is not None
        and (item or {}).get("dataCenterReviewMinutes") is not None
    ]
    saved = sum(
        float(item["manualBaselineMinutes"]) > float(item["dataCenterReviewMinutes"])
        for item in timed
    )
    usable_rate = usable / completed if completed else 0.0
    retry_rate = retry / completed if completed else 0.0
    saved_rate = saved / len(timed) if timed else 0.0
    verdict = (
        "fail"
        if retry_rate > 0.20
        else "pass"
        if completed >= len(_VALUE_VALIDATION_QUESTIONS)
        and usable_rate >= 0.75
        and retry_rate <= 0.10
        else "hold"
    )
    return {
        "sessionId": session_id,
        "clientId": project_id,
        "completed": completed,
        "usableAnswerRate": usable_rate,
        "estimatedTimeSavedRate": saved_rate,
        "retryBannerRate": retry_rate,
        "proposalCreatedCount": sum(
            bool((item or {}).get("proposalCreated")) for item in entries.values()
        ),
        "executionTicketCreatedCount": sum(
            bool((item or {}).get("executionTicketCreated")) for item in entries.values()
        ),
        "verdict": verdict,
    }


def _value_validation_payload(row: sqlite3.Row) -> dict[str, Any]:
    source = _json(row["source_payload_json"], {})
    entries = source.get("entries")
    if not isinstance(entries, Mapping):
        entries = {}
    completed_ids = [
        item["id"] for item in _VALUE_VALIDATION_QUESTIONS if item["id"] in entries
    ]
    status = str(source.get("sessionStatus") or "running")
    return {
        "id": row["intelligence_id"],
        "clientId": row["project_id"],
        "status": status if status in {"running", "completed", "failed"} else "running",
        "questionSet": list(_VALUE_VALIDATION_QUESTIONS),
        "completedQuestionIds": completed_ids,
        "summary": _value_validation_summary(
            str(row["intelligence_id"]),
            str(row["project_id"]),
            entries,
        ),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "version": int(row["version"]),
    }


def value_validation_sessions(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]] | dict[str, Any]:
    clauses = [
        "organization_id = ?",
        "record_kind = 'value_validation_session'",
        "status != 'archived'",
    ]
    parameters: list[Any] = [identity.organization_id]
    if project_id:
        clauses.append("project_id = ?")
        parameters.append(project_id)
    if session_id:
        clauses.append("intelligence_id = ?")
        parameters.append(session_id)
    parameters.append(max(1, min(limit, 100)))
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            f"""
            SELECT * FROM intelligence_records
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC, intelligence_id
            LIMIT ?
            """,
            tuple(parameters),
        ).fetchall()
        for row in rows:
            _require_project(repository, connection, identity, str(row["project_id"]))
    payloads = [_value_validation_payload(row) for row in rows]
    if session_id:
        if not payloads:
            raise RepositoryError(404, "value_validation_session_missing", "价值验证会话不存在")
        return payloads[0]
    return payloads


def create_value_validation_session(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    normalized = {"projectId": project_id}
    command_type = "workbench.value_validation_session.created"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project_editor(repository, connection, identity, project_id)
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            now = utc_now()
            session_id = new_id()
            source = {"entries": {}, "sessionStatus": "running"}
            connection.execute(
                """
                INSERT INTO intelligence_records (
                    intelligence_id, organization_id, project_id, title, summary,
                    source_url, record_kind, status, visibility_scope,
                    created_by_membership_id, source_payload_json, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, '工作台价值验证', '', '',
                          'value_validation_session', 'candidate', 'participants',
                          ?, ?, 1, ?, ?)
                """,
                (
                    session_id,
                    identity.organization_id,
                    project_id,
                    identity.membership_id,
                    canonical_json(source),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM intelligence_records WHERE intelligence_id = ?",
                (session_id,),
            ).fetchone()
            result = _value_validation_payload(row)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="intelligence",
                aggregate_id=session_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={"projectId": project_id, "questionCount": 10},
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def update_value_validation_session(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    session_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    finish: bool,
) -> dict[str, Any]:
    command_type = (
        "workbench.value_validation_session.finished"
        if finish
        else "workbench.value_validation_question.completed"
    )
    normalized = {"sessionId": session_id, "finish": finish, **dict(payload)}
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = connection.execute(
                """
                SELECT * FROM intelligence_records
                WHERE organization_id = ? AND intelligence_id = ?
                  AND record_kind = 'value_validation_session'
                  AND status != 'archived'
                """,
                (identity.organization_id, session_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "value_validation_session_missing", "价值验证会话不存在")
            _require_project_editor(
                repository,
                connection,
                identity,
                str(row["project_id"]),
            )
            source = _json(row["source_payload_json"], {})
            entries = source.get("entries")
            if not isinstance(entries, dict):
                entries = {}
            if finish:
                summary = _value_validation_summary(
                    session_id,
                    str(row["project_id"]),
                    entries,
                )
                source["sessionStatus"] = (
                    "failed" if summary["verdict"] == "fail" else "completed"
                )
            else:
                question_id = str(payload.get("questionId") or "")
                if question_id not in {item["id"] for item in _VALUE_VALIDATION_QUESTIONS}:
                    raise RepositoryError(
                        422,
                        "value_validation_question_missing",
                        "价值验证问题不存在",
                    )
                review_id = str(payload.get("reviewId") or "")
                message_id = str(payload.get("messageId") or "")
                if not review_id or not message_id:
                    raise RepositoryError(
                        422,
                        "review_message_question_mismatch",
                        "完成问题必须引用对应回答评审",
                    )
                review = connection.execute(
                    """
                    SELECT * FROM intelligence_records
                    WHERE organization_id = ? AND intelligence_id = ?
                      AND project_id = ?
                      AND record_kind = 'workspace_answer_value_review'
                      AND status != 'archived'
                    """,
                    (
                        identity.organization_id,
                        review_id,
                        row["project_id"],
                    ),
                ).fetchone()
                if review is None:
                    raise RepositoryError(
                        422,
                        "review_message_question_mismatch",
                        "回答评审不存在或不属于当前项目",
                    )
                review_payload = _json(review["source_payload_json"], {})
                if str(review_payload.get("messageId") or "") != message_id:
                    raise RepositoryError(
                        422,
                        "review_message_question_mismatch",
                        "回答评审与消息不匹配",
                    )
                entries[question_id] = {
                    "reviewId": review_id,
                    "messageId": message_id,
                    "usableAnswer": review_payload.get("usableAnswer"),
                    "retryBannerShown": bool(
                        review_payload.get("shouldShowRetryBanner")
                    ),
                    "manualBaselineMinutes": review_payload.get(
                        "manualBaselineMinutes"
                    ),
                    "dataCenterReviewMinutes": review_payload.get(
                        "dataCenterReviewMinutes"
                    ),
                    "proposalCreated": bool(payload.get("proposalCreated")),
                    "executionTicketCreated": bool(
                        payload.get("executionTicketCreated")
                    ),
                    "reviewerNote": review_payload.get("reviewerNote")
                    or str(payload.get("reviewerNote") or ""),
                    "updatedAt": utc_now(),
                }
                source["entries"] = entries
            before = int(row["version"])
            after = before + 1
            now = utc_now()
            connection.execute(
                """
                UPDATE intelligence_records
                SET source_payload_json = ?, version = version + 1, updated_at = ?
                WHERE organization_id = ? AND intelligence_id = ? AND version = ?
                """,
                (
                    canonical_json(source),
                    now,
                    identity.organization_id,
                    session_id,
                    before,
                ),
            )
            saved = connection.execute(
                "SELECT * FROM intelligence_records WHERE intelligence_id = ?",
                (session_id,),
            ).fetchone()
            result = _value_validation_payload(saved)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="intelligence",
                aggregate_id=session_id,
                expected_version=before,
                before_version=before,
                after_version=after,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": row["project_id"],
                    "finish": finish,
                    "completedCount": len(entries),
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def project_insights(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, list[dict[str, Any]]]:
    reports = list_reports(repository, identity, project_id=project_id)
    collections: dict[str, list[dict[str, Any]]] = {
        "judgments": [],
        "topics": [],
        "conflicts": [],
        "openQuestions": [],
    }
    key_aliases = {
        "judgments": ("judgments", "latestJudgments"),
        "topics": ("topics", "themeClusters"),
        "conflicts": ("conflicts", "conflictGroups"),
        "openQuestions": ("openQuestions", "open_questions"),
    }
    for report in reports:
        payload = (report.get("latest") or {}).get("content_payload") or {}
        if not isinstance(payload, Mapping):
            continue
        for target, aliases in key_aliases.items():
            for alias in aliases:
                values = payload.get(alias)
                if not isinstance(values, list):
                    continue
                for index, value in enumerate(values):
                    if isinstance(value, Mapping):
                        collections[target].append(
                            {
                                **_safe_manifest(dict(value)),
                                "id": value.get("id")
                                or f"{report['id']}:{target}:{index}",
                                "clientId": project_id,
                                "sourceNarrativeOutputId": report["id"],
                                "createdAt": value.get("createdAt")
                                or (report.get("latest") or {}).get("created_at"),
                                "updatedAt": value.get("updatedAt")
                                or report.get("updated_at"),
                            }
                        )
                break
    collections["judgments"].extend(
        list_project_judgments(repository, identity, project_id=project_id)
    )
    for item in collections["judgments"]:
        item.setdefault("targetType", "client")
        item.setdefault("targetId", project_id)
        item.setdefault("topic", item.get("title") or "正式产物判断")
        item.setdefault("version", 1)
        item.setdefault("status", "awaiting_review")
        item.setdefault("originType", "projection")
        item.setdefault("authorityLevel", "candidate")
        item.setdefault("qualityTier", "normalized")
        item.setdefault("sourceSnapshotHash", "")
        item.setdefault("summary", item.get("content") or "")
        item.setdefault("evidenceIds", [])
        item.setdefault("riskLevel", "medium")
        item.setdefault("confidence", "medium")
    for item in collections["topics"]:
        item.setdefault("scopeType", "client")
        item.setdefault("scopeId", project_id)
        item.setdefault("originType", "projection")
        item.setdefault("authorityLevel", "candidate")
        item.setdefault("qualityTier", "normalized")
        item.setdefault("themeKey", item.get("key") or item["id"])
        item.setdefault("title", item.get("name") or "正式产物主题")
        item.setdefault("supportIds", [])
        item.setdefault("opposeIds", [])
        item.setdefault("gapSummary", "")
        item.setdefault("latestChangeSummary", "")
        item.setdefault("evidenceCount", len(item["supportIds"]))
        item.setdefault("version", 1)
    for item in collections["conflicts"]:
        item.setdefault("scopeType", "client")
        item.setdefault("scopeId", project_id)
        item.setdefault("originType", "projection")
        item.setdefault("authorityLevel", "candidate")
        item.setdefault("qualityTier", "normalized")
        item.setdefault("conflictType", "narrative")
        item.setdefault("title", "正式产物冲突")
        item.setdefault("summary", "")
        item.setdefault("evidenceIds", [])
        item.setdefault("unresolvedQuestionIds", [])
        item.setdefault("resolutionStatus", "awaiting_review")
        item.setdefault("severity", "medium")
    for item in collections["openQuestions"]:
        item.setdefault("scopeType", "client")
        item.setdefault("scopeId", project_id)
        item.setdefault("originType", "projection")
        item.setdefault("authorityLevel", "candidate")
        item.setdefault("qualityTier", "normalized")
        item.setdefault("themeKey", "narrative_gap")
        item.setdefault("question", item.get("title") or "待补充问题")
        item.setdefault("reason", "")
        item.setdefault("blockerLevel", "medium")
        item.setdefault("status", "awaiting_review")
    return collections


def retrieval_shadow_runs(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    visible_ids = {
        item["projectId"] for item in repository.business_snapshot(identity)["projects"]
    }
    if project_id is not None and project_id not in visible_ids:
        raise RepositoryError(404, "project_missing", "当前成员无法访问该项目")
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            """
            SELECT pa.*, d.project_id
            FROM processing_attempts pa
            JOIN knowledge_documents d
              ON d.organization_id = pa.organization_id
             AND d.document_id = pa.document_id
            WHERE pa.organization_id = ?
              AND pa.processing_kind LIKE 'retrieval_shadow%'
            ORDER BY pa.created_at DESC, pa.processing_attempt_id
            """,
            (identity.organization_id,),
        ).fetchall()
    return [
        {
            "id": row["processing_attempt_id"],
            "clientId": row["project_id"],
            "page": "client_workspace",
            "prompt": "",
            "baselineSummary": {"authority": "document_versions"},
            "candidateSummary": {
                "processingKind": row["processing_kind"],
                "state": row["state"],
                "attemptNo": row["attempt_no"],
            },
            "overlapRate": 0,
            "candidateBetter": row["state"] in {"completed", "partial"},
            "failureReason": row["error_message"] or None,
            "createdAt": row["created_at"],
        }
        for row in rows
        if str(row["project_id"]) in visible_ids
        and (project_id is None or str(row["project_id"]) == project_id)
    ][: max(1, min(200, limit))]


def retrieval_shadow_summary(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str | None = None,
) -> dict[str, Any]:
    items = retrieval_shadow_runs(
        repository,
        identity,
        project_id=project_id,
        limit=200,
    )
    total = len(items)
    completed = sum(1 for item in items if item["candidateBetter"])
    failures = sum(1 for item in items if item.get("failureReason"))
    return {
        "total": total,
        "candidateBetterRate": completed / total if total else 0,
        "overlapRateAvg": (
            sum(float(item["overlapRate"]) for item in items) / total if total else 0
        ),
        "latencyDeltaMsAvg": 0,
        "failures": failures,
        "authority": "processing_attempts",
    }


def report_run(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    report_id: str,
) -> dict[str, Any]:
    report = report_detail(repository, identity, report_id=report_id)
    latest = report.get("latest") or {}
    payload = latest.get("content_payload") or {}
    blueprint = payload.get("blueprint") if isinstance(payload, Mapping) else None
    sections = payload.get("sections") if isinstance(payload, Mapping) else None
    sections = sections if isinstance(sections, list) else []
    return {
        "id": report["id"],
        "client_id": report.get("client_id"),
        "event_line_id": report.get("event_line_id"),
        "period_start": payload.get("period_start") if isinstance(payload, Mapping) else None,
        "period_end": payload.get("period_end") if isinstance(payload, Mapping) else None,
        "intent_hint": payload.get("intent_hint") if isinstance(payload, Mapping) else None,
        "status": "saved",
        "blueprint": blueprint,
        "sections_status": ["done" for _ in sections],
        "sections": sections,
        "body_markdown": latest.get("content_markdown") or "",
        "warnings": [],
        "source_set_id": latest.get("source_set_id") or "",
        "narrative_id": report["id"],
        "narrative_rev": latest.get("version") or report.get("latest_version"),
        "event_line_version": latest.get("event_line_version") or 0,
        "input_fingerprint": latest.get("input_fingerprint") or "",
        "artifact": report,
        "saved_at": report.get("updated_at"),
        "error_message": None,
        "output_files": {},
        "total_llm_tokens": 0,
        "created_at": latest.get("created_at"),
        "updated_at": report.get("updated_at"),
    }


def answer_task_action(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    answer_id: str,
    action_type: str,
    idempotency_key: str,
) -> dict[str, Any]:
    answer = answer_detail(repository, identity, answer_id=answer_id)["answer"]
    if answer["lifecycleState"] != "active":
        raise RepositoryError(409, "answer_archived", "已归档回答不能创建行动项")
    if not answer.get("projectId"):
        raise RepositoryError(409, "answer_workspace_missing", "回答没有固定项目 WorkspaceContext")
    if action_type == "create_task":
        title = "跟进工作台回答"
        description = f"来源工作台回答：{answer_id}"
        summary = "已从工作台回答创建严格任务"
    elif action_type == "request_evidence":
        title = "补充工作台回答证据"
        description = f"请补充支持工作台回答 {answer_id} 的权威资料。"
        summary = "已创建严格证据补充任务"
    else:
        raise RepositoryError(422, "answer_action_invalid", "回答行动类型无效")
    # The retained workbench action must enter the same GC-04 task authority as
    # the task editor.  CloudRepository intentionally has no legacy task
    # facade; using it here used to leave the visible button dependent on a
    # frozen path.  Import lazily to avoid coupling repository registration.
    from .gc04_tasks import GC04TaskRepository

    created = GC04TaskRepository(repository).create_task(
        identity,
        payload={
            "clientId": answer["projectId"],
            "title": title,
            "description": description,
            "priority": "normal",
            "visibilityScope": "participants",
            "sourceType": "ai_answer",
            "sourceId": answer_id,
        },
        idempotency_key=idempotency_key,
    )
    task = created["task"]
    return {
        "messageId": answer_id,
        "actionType": action_type,
        "status": "created",
        "summary": summary,
        "taskId": task.get("id") or task.get("taskId"),
        "autoApproved": True,
        "autoExecuted": True,
    }


def task_todo_action(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    task_id: str,
    action: str,
    idempotency_key: str,
) -> dict[str, Any]:
    from .gc04_tasks import GC04TaskRepository

    task_repository = GC04TaskRepository(repository)
    detail = task_repository.task_detail(identity, task_id=task_id)["task"]
    if str(detail.get("projectId") or "") != project_id:
        raise RepositoryError(404, "task_missing", "项目待办不存在")
    if action == "promote":
        return {
            "ok": True,
            "newTaskId": task_id,
            "source": "tasks",
            "status": "reused",
        }
    if action not in {"complete", "cancel"}:
        raise RepositoryError(
            404,
            "task_todo_action_unknown",
            "未知的项目待办动作",
        )
    if action == "cancel":
        deleted = task_repository.delete_task(
            identity,
            task_id=task_id,
            expected_version=int(detail["version"]),
            idempotency_key=idempotency_key,
        )
        return {
            "ok": True,
            "id": task_id,
            "source": "tasks",
            "action": action,
            "deleted": True,
            **deleted,
        }
    updated = task_repository.update_task(
        identity,
        task_id=task_id,
        payload={
            "expectedVersion": int(detail["version"]),
            "status": "completed",
            "completionNote": "由工作台统一待办完成",
        },
        idempotency_key=idempotency_key,
    )
    return {
        "ok": True,
        "id": task_id,
        "source": "tasks",
        "action": action,
        "task": updated["task"],
    }


def _retrieval_settings_payload(row: sqlite3.Row) -> dict[str, Any]:
    settings = _json(row["public_config_json"], {})
    if not isinstance(settings, Mapping):
        settings = {}
    return {
        **{key: settings.get(key) for key in _RETRIEVAL_FIELDS if key in settings},
        "version": int(row["version"]),
        "updatedAt": row["updated_at"],
    }


def retrieval_settings(
    repository: CloudRepository,
    identity: SessionIdentity,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            """
            SELECT *
            FROM scoped_configuration_records
            WHERE organization_id = ? AND scope_kind = 'organization'
              AND configuration_kind = ? AND lifecycle_state = 'active'
            """,
            (identity.organization_id, RETRIEVAL_CONFIGURATION_KIND),
        ).fetchone()
    if row is None:
        raise RepositoryError(
            404,
            "retrieval_settings_missing",
            "当前组织尚未保存检索模型设置",
        )
    return _retrieval_settings_payload(row)


def save_retrieval_settings(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if not identity.is_admin:
        raise RepositoryError(403, "organization_admin_required", "仅组织管理员可修改检索设置")
    requested = {
        key: value
        for key, value in payload.items()
        if key in _RETRIEVAL_FIELDS
    }
    normalized_request = {"settings": requested}
    command_type = "workbench.retrieval_settings.saved"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized_request,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = connection.execute(
                """
                SELECT *
                FROM scoped_configuration_records
                WHERE organization_id = ? AND scope_kind = 'organization'
                  AND configuration_kind = ?
                """,
                (identity.organization_id, RETRIEVAL_CONFIGURATION_KIND),
            ).fetchone()
            current = (
                dict(_json(row["public_config_json"], {}))
                if row is not None
                and isinstance(_json(row["public_config_json"], {}), Mapping)
                else {}
            )
            merged = {**current, **requested}
            missing = sorted(
                key for key in _RETRIEVAL_REQUIRED_FIELDS if key not in merged
            )
            if missing:
                raise RepositoryError(
                    422,
                    "retrieval_settings_incomplete",
                    f"检索设置缺少字段：{', '.join(missing)}",
                )
            try:
                dimension = int(merged["embeddingDimension"])
            except (TypeError, ValueError) as exc:
                raise RepositoryError(
                    422,
                    "retrieval_dimension_invalid",
                    "embeddingDimension 必须为正整数",
                ) from exc
            if dimension < 1:
                raise RepositoryError(
                    422,
                    "retrieval_dimension_invalid",
                    "embeddingDimension 必须为正整数",
                )
            merged["embeddingDimension"] = dimension
            now = utc_now()
            if row is None:
                if payload.get("expectedVersion") not in {None, 0, "0"}:
                    raise RepositoryError(
                        409,
                        "retrieval_settings_version_conflict",
                        "检索设置尚不存在，expectedVersion 必须为空或 0",
                    )
                configuration_id = new_id()
                connection.execute(
                    """
                    INSERT INTO scoped_configuration_records (
                        configuration_id, scope_id, scope_kind, organization_id,
                        principal_id, membership_id, configuration_kind, provider,
                        public_config_json, encrypted_secret_bundle,
                        secret_fingerprint, secret_envelope_version,
                        lifecycle_state, version, updated_by_membership_id,
                        created_at, updated_at
                    ) VALUES (?, ?, 'organization', ?, NULL, NULL, ?, ?, ?,
                              NULL, NULL, 1, 'active', 1, ?, ?, ?)
                    """,
                    (
                        configuration_id,
                        identity.scope_id,
                        identity.organization_id,
                        RETRIEVAL_CONFIGURATION_KIND,
                        str(merged.get("embeddingProvider") or ""),
                        canonical_json(merged),
                        identity.membership_id,
                        now,
                        now,
                    ),
                )
                before_version = None
                after_version = 1
                expected_version = None
            else:
                configuration_id = str(row["configuration_id"])
                current_version = int(row["version"])
                expected_version = _expected_version(payload)
                if expected_version != current_version:
                    raise RepositoryError(
                        409,
                        "retrieval_settings_version_conflict",
                        "检索设置已被其他成员更新",
                    )
                cursor = connection.execute(
                    """
                    UPDATE scoped_configuration_records
                    SET provider = ?, public_config_json = ?,
                        lifecycle_state = 'active', version = version + 1,
                        updated_by_membership_id = ?, updated_at = ?
                    WHERE organization_id = ? AND configuration_id = ?
                      AND version = ?
                    """,
                    (
                        str(merged.get("embeddingProvider") or ""),
                        canonical_json(merged),
                        identity.membership_id,
                        now,
                        identity.organization_id,
                        configuration_id,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "retrieval_settings_version_conflict",
                        "检索设置已被其他成员更新",
                    )
                before_version = expected_version
                after_version = expected_version + 1
            saved = connection.execute(
                """
                SELECT *
                FROM scoped_configuration_records
                WHERE organization_id = ? AND configuration_id = ?
                """,
                (identity.organization_id, configuration_id),
            ).fetchone()
            if saved is None:
                raise RepositoryError(500, "retrieval_settings_lost", "检索设置保存后无法读取")
            result = _retrieval_settings_payload(saved)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="scoped_configuration",
                aggregate_id=configuration_id,
                expected_version=expected_version,
                before_version=before_version,
                after_version=after_version,
                payload=normalized_request,
                result=result,
                audit_summary={
                    "configurationKind": RETRIEVAL_CONFIGURATION_KIND,
                    "changedFields": sorted(requested),
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _proposal_payload(row: sqlite3.Row) -> dict[str, Any]:
    source = _json(row["source_payload_json"], {})
    if not isinstance(source, Mapping):
        source = {}
    authority_status = str(row["status"])
    return {
        "id": row["intelligence_id"],
        "proposalId": row["intelligence_id"],
        "draftId": row["intelligence_id"],
        "clientId": row["project_id"],
        "projectId": row["project_id"],
        "kind": source.get("kind") or "context_refresh",
        "title": row["title"],
        "summary": row["summary"],
        "rationale": source.get("rationale") or row["summary"],
        "riskLevel": source.get("riskLevel") or "medium",
        "targetRefs": list(source.get("targetRefs") or []),
        "sourceRefs": list(source.get("sourceRefs") or []),
        "boundaryNotes": list(source.get("boundaryNotes") or []),
        "payload": dict(source),
        "requiresApproval": True,
        "status": {
            "candidate": "draft",
            "inbox": "reviewed",
            "returned": "rejected",
            "accepted": "promoted",
            "archived": "expired",
        }.get(authority_status, "draft"),
        "authorityStatus": authority_status,
        "version": int(row["version"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_proposal_drafts(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        placeholders = ",".join("?" for _ in PROPOSAL_RECORD_KINDS)
        rows = connection.execute(
            f"""
            SELECT *
            FROM intelligence_records
            WHERE organization_id = ? AND project_id = ?
              AND record_kind IN ({placeholders})
              AND status != 'archived'
            ORDER BY updated_at DESC, intelligence_id
            """,
            (
                identity.organization_id,
                project_id,
                *sorted(PROPOSAL_RECORD_KINDS),
            ),
        ).fetchall()
    return [_proposal_payload(row) for row in rows]


def _meeting_source_summary(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    project_id: str,
    meeting_id: str,
) -> dict[str, Any]:
    task_rows = connection.execute(
        """
        SELECT task_id, title, lifecycle_state, version, updated_at
        FROM task_records
        WHERE organization_id = ? AND project_id = ?
          AND source_type = 'meeting' AND source_id = ?
        ORDER BY updated_at DESC, task_id
        """,
        (identity.organization_id, project_id, meeting_id),
    ).fetchall()
    asset_rows = connection.execute(
        """
        SELECT source_asset_id, file_name, source_kind, version, updated_at
        FROM source_assets
        WHERE organization_id = ? AND project_id = ? AND source_locator = ?
        ORDER BY updated_at DESC, source_asset_id
        """,
        (identity.organization_id, project_id, meeting_id),
    ).fetchall()
    activity_rows = connection.execute(
        """
        SELECT ela.event_line_activity_id, ela.happened_at, ela.created_at,
               el.event_line_id
        FROM event_line_activities ela
        JOIN event_line_records el
          ON el.organization_id = ela.organization_id
         AND el.event_line_id = ela.event_line_id
        WHERE ela.organization_id = ? AND el.project_id = ?
          AND ela.source_type = 'meeting' AND ela.source_id = ?
          AND ela.association_state = 'confirmed'
        ORDER BY ela.happened_at DESC, ela.event_line_activity_id
        """,
        (identity.organization_id, project_id, meeting_id),
    ).fetchall()
    if not task_rows and not asset_rows and not activity_rows:
        raise RepositoryError(
            404,
            "meeting_context_missing",
            "该项目没有与此 meetingId 绑定的严格来源事实",
        )
    refs = [
        f"task:{row['task_id']}@{row['version']}" for row in task_rows
    ] + [
        f"source_asset:{row['source_asset_id']}@{row['version']}"
        for row in asset_rows
    ] + [
        f"event_line_activity:{row['event_line_activity_id']}@{row['created_at']}"
        for row in activity_rows
    ]
    return {
        "meetingId": meeting_id,
        "taskIds": [row["task_id"] for row in task_rows],
        "sourceAssetIds": [row["source_asset_id"] for row in asset_rows],
        "eventLineActivityIds": [
            row["event_line_activity_id"] for row in activity_rows
        ],
        "sourceRefs": refs,
        "materialPackHash": sha256_text(canonical_json(refs)),
    }


def create_proposal_draft(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    meeting_id: str | None = None,
    meeting_phase: str | None = None,
) -> dict[str, Any]:
    kind = str(
        meeting_phase
        or payload.get("kind")
        or "task_prep"
    ).strip()
    if kind not in PROPOSAL_KINDS:
        raise RepositoryError(422, "proposal_kind_invalid", "提案类型无效")
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    if meeting_id:
        default_phase = "会前准备" if kind == "meeting_prep" else "会后跟进"
        title = title or f"{default_phase}提案"
        summary = summary or (
            f"基于 meetingId={meeting_id} 的严格任务、资料与事件事实生成，"
            "需审批后执行。"
        )
    if not title:
        raise RepositoryError(422, "proposal_title_required", "提案标题不能为空")
    if not summary:
        raise RepositoryError(422, "proposal_summary_required", "提案摘要不能为空")
    risk_level = str(payload.get("riskLevel") or "medium")
    if risk_level not in {"low", "medium", "high"}:
        raise RepositoryError(422, "proposal_risk_invalid", "提案风险等级无效")
    source_refs = [
        str(item)
        for item in (payload.get("sourceRefs") or [])
        if str(item).strip()
    ]
    target_refs = list(payload.get("targetRefs") or [])
    boundary_notes = list(payload.get("boundaryNotes") or [])
    proposal_body = (
        dict(payload.get("payload") or {})
        if isinstance(payload.get("payload"), Mapping)
        else {}
    )
    normalized = {
        "projectId": project_id,
        "meetingId": meeting_id,
        "kind": kind,
        "title": title,
        "summary": summary,
        "rationale": str(payload.get("rationale") or summary),
        "riskLevel": risk_level,
        "targetRefs": target_refs,
        "sourceRefs": source_refs,
        "boundaryNotes": boundary_notes,
        "scopeType": payload.get("scopeType") or "client",
        "scopeId": payload.get("scopeId") or project_id,
        "sourceMessageId": payload.get("sourceMessageId"),
        "payload": proposal_body,
    }
    command_type = "workbench.proposal_draft.created"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project_editor(repository, connection, identity, project_id)
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            meeting_context: dict[str, Any] | None = None
            if meeting_id:
                meeting_context = _meeting_source_summary(
                    connection,
                    identity,
                    project_id=project_id,
                    meeting_id=meeting_id,
                )
            if meeting_context:
                source_refs = list(
                    dict.fromkeys(
                        [*source_refs, *meeting_context["sourceRefs"]]
                    )
                )
            source_payload = {
                "kind": kind,
                "sourceType": (
                    "meeting" if meeting_id else payload.get("sourceType") or "manual"
                ),
                "sourceId": meeting_id or payload.get("sourceMessageId"),
                "rationale": str(payload.get("rationale") or summary),
                "riskLevel": risk_level,
                "targetRefs": target_refs,
                "sourceRefs": source_refs,
                "boundaryNotes": boundary_notes,
                "scopeType": payload.get("scopeType") or "client",
                "scopeId": payload.get("scopeId") or project_id,
                "taskDrafts": list(proposal_body.get("taskDrafts") or []),
                "proposalPayload": proposal_body,
                "meetingContext": meeting_context,
            }
            now = utc_now()
            proposal_id = new_id()
            connection.execute(
                """
                INSERT INTO intelligence_records (
                    intelligence_id, organization_id, project_id, title, summary,
                    source_url, record_kind, status, visibility_scope,
                    created_by_membership_id, source_payload_json, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', 'proposal_draft', 'candidate',
                          'participants', ?, ?, 1, ?, ?)
                """,
                (
                    proposal_id,
                    identity.organization_id,
                    project_id,
                    title,
                    summary,
                    identity.membership_id,
                    canonical_json(source_payload),
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
                    proposal_id,
                    title,
                    summary,
                    identity.membership_id,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM intelligence_records
                WHERE organization_id = ? AND intelligence_id = ?
                """,
                (identity.organization_id, proposal_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(500, "proposal_create_lost", "提案创建后无法读取")
            result = _proposal_payload(row)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="intelligence",
                aggregate_id=proposal_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "kind": kind,
                    "sourceRefs": source_refs,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def meeting_action_items(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        rows = connection.execute(
            """
            SELECT t.*, sa.source_asset_id, sa.file_name
            FROM task_records t
            LEFT JOIN source_assets sa
              ON sa.organization_id = t.organization_id
             AND sa.project_id = t.project_id
             AND sa.source_locator = t.source_id
            WHERE t.organization_id = ? AND t.project_id = ?
              AND t.source_type = 'meeting' AND t.source_id != ''
              AND t.lifecycle_state NOT IN ('completed', 'cancelled', 'archived')
            ORDER BY t.deadline_at IS NULL, t.deadline_at, t.updated_at DESC,
                     t.task_id
            """,
            (identity.organization_id, project_id),
        ).fetchall()
    high: list[dict[str, Any]] = []
    medium: list[dict[str, Any]] = []
    for row in rows:
        confidence = "high" if row["source_asset_id"] else "medium"
        item = {
            "actor": identity.display_name,
            "text": row["title"],
            "confidence": confidence,
            "sourceDocTitle": row["file_name"] or f"会议任务 {row['source_id']}",
            "sourceDocId": row["source_asset_id"] or row["task_id"],
            "sourceChunkIndex": 0,
            "importedAt": row["created_at"],
            "fingerprint": sha256_text(
                f"{row['task_id']}:{row['version']}:{row['source_id']}"
            ),
            "taskId": row["task_id"],
            "meetingId": row["source_id"],
            "version": row["version"],
        }
        (high if confidence == "high" else medium).append(item)
    return {
        "clientId": project_id,
        "high": high,
        "medium": medium,
        "totalHigh": len(high),
        "totalMedium": len(medium),
    }


def _answer_value_review_payload(row: sqlite3.Row) -> dict[str, Any]:
    source = _json(row["source_payload_json"], {})
    baseline = source.get("manualBaselineMinutes")
    review = source.get("dataCenterReviewMinutes")
    saved = None
    if isinstance(baseline, (int, float)) and isinstance(review, (int, float)):
        saved = max(float(baseline) - float(review), 0)
    return {
        "id": row["intelligence_id"],
        "clientId": row["project_id"],
        "messageId": source.get("messageId"),
        "prompt": source.get("prompt") or "",
        "answerMode": source.get("answerMode") or "",
        "userVisibleQualityStatus": source.get("userVisibleQualityStatus") or "ready",
        "shouldShowRetryBanner": bool(source.get("shouldShowRetryBanner")),
        "usableAnswer": source.get("usableAnswer"),
        "reviewerNote": source.get("reviewerNote") or "",
        "manualBaselineMinutes": baseline,
        "dataCenterReviewMinutes": review,
        "savedMinutes": saved,
        "version": row["version"],
        "createdAt": row["created_at"],
    }


def list_answer_value_reviews(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str | None = None,
    limit: int = 120,
) -> list[dict[str, Any]]:
    snapshot = repository.business_snapshot(identity)
    visible_ids = {item["projectId"] for item in snapshot["projects"]}
    if project_id is not None and project_id not in visible_ids:
        raise RepositoryError(404, "project_missing", "当前成员无法访问该项目")
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            """
            SELECT *
            FROM intelligence_records
            WHERE organization_id = ? AND record_kind = ?
              AND created_by_membership_id = ?
              AND status != 'archived'
            ORDER BY created_at DESC, intelligence_id
            LIMIT ?
            """,
            (
                identity.organization_id,
                ANSWER_VALUE_REVIEW_KIND,
                identity.membership_id,
                max(1, min(500, limit)),
            ),
        ).fetchall()
    return [
        _answer_value_review_payload(row)
        for row in rows
        if str(row["project_id"]) in visible_ids
        and (project_id is None or str(row["project_id"]) == project_id)
    ]


def create_answer_value_review(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    project_id = str(payload.get("clientId") or "").strip()
    answer_id = str(payload.get("messageId") or "").strip()
    if not project_id or not answer_id:
        raise RepositoryError(
            422,
            "answer_review_context_required",
            "clientId 与 messageId 不能为空",
        )
    quality_status = str(payload.get("userVisibleQualityStatus") or "ready")
    if quality_status not in {
        "ready",
        "usable_with_boundary",
        "degraded",
        "needs_retry",
    }:
        raise RepositoryError(422, "answer_review_status_invalid", "回答质量状态无效")
    source_payload = {
        "messageId": answer_id,
        "prompt": str(payload.get("prompt") or ""),
        "answerMode": str(payload.get("answerMode") or ""),
        "userVisibleQualityStatus": quality_status,
        "shouldShowRetryBanner": bool(payload.get("shouldShowRetryBanner")),
        "usableAnswer": payload.get("usableAnswer"),
        "reviewerNote": str(payload.get("reviewerNote") or ""),
        "manualBaselineMinutes": payload.get("manualBaselineMinutes"),
        "dataCenterReviewMinutes": payload.get("dataCenterReviewMinutes"),
    }
    normalized = {"projectId": project_id, **source_payload}
    command_type = "workbench.answer_value_review.created"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project(repository, connection, identity, project_id)
            answer = connection.execute(
                """
                SELECT question, version
                FROM ai_answers
                WHERE organization_id = ? AND membership_id = ?
                  AND ai_answer_id = ? AND project_id = ?
                  AND lifecycle_state = 'active'
                """,
                (
                    identity.organization_id,
                    identity.membership_id,
                    answer_id,
                    project_id,
                ),
            ).fetchone()
            if answer is None:
                raise RepositoryError(404, "answer_missing", "工作台回答不存在")
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            now = utc_now()
            review_id = new_id()
            title = f"回答价值评审：{str(answer['question'])[:80]}"
            summary = source_payload["reviewerNote"] or quality_status
            connection.execute(
                """
                INSERT INTO intelligence_records (
                    intelligence_id, organization_id, project_id, title, summary,
                    source_url, record_kind, status, visibility_scope,
                    created_by_membership_id, source_payload_json, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', ?, 'candidate', 'participants',
                          ?, ?, 1, ?, ?)
                """,
                (
                    review_id,
                    identity.organization_id,
                    project_id,
                    title,
                    summary,
                    ANSWER_VALUE_REVIEW_KIND,
                    identity.membership_id,
                    canonical_json(source_payload),
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
                    review_id,
                    title,
                    summary,
                    identity.membership_id,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM intelligence_records
                WHERE organization_id = ? AND intelligence_id = ?
                """,
                (identity.organization_id, review_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(500, "answer_review_lost", "回答评审保存后无法读取")
            result = _answer_value_review_payload(row)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="intelligence",
                aggregate_id=review_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "answerId": answer_id,
                    "answerVersion": answer["version"],
                    "qualityStatus": quality_status,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def answer_value_summary(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    items = list_answer_value_reviews(
        repository,
        identity,
        project_id=project_id,
        limit=500,
    )
    count = len(items)
    usable = sum(1 for item in items if item.get("usableAnswer") is True)
    retry = sum(1 for item in items if item.get("shouldShowRetryBanner"))
    baselines = [
        float(item["manualBaselineMinutes"])
        for item in items
        if isinstance(item.get("manualBaselineMinutes"), (int, float))
    ]
    review_times = [
        float(item["dataCenterReviewMinutes"])
        for item in items
        if isinstance(item.get("dataCenterReviewMinutes"), (int, float))
    ]
    paired = [
        (
            float(item["manualBaselineMinutes"]),
            float(item["dataCenterReviewMinutes"]),
        )
        for item in items
        if isinstance(item.get("manualBaselineMinutes"), (int, float))
        and isinstance(item.get("dataCenterReviewMinutes"), (int, float))
        and float(item["manualBaselineMinutes"]) > 0
    ]
    saved_rate = (
        sum(max(baseline - reviewed, 0) / baseline for baseline, reviewed in paired)
        / len(paired)
        if paired
        else 0
    )
    return {
        "clientId": project_id,
        "reviewCount": count,
        "usableAnswerRate": usable / count if count else 0,
        "retryBannerRate": retry / count if count else 0,
        "averageManualBaselineMinutes": (
            sum(baselines) / len(baselines) if baselines else 0
        ),
        "averageDataCenterReviewMinutes": (
            sum(review_times) / len(review_times) if review_times else 0
        ),
        "estimatedTimeSavedRate": saved_rate,
        "positiveReviewCount": sum(
            1
            for item in items
            if item.get("usableAnswer") is True
            or item.get("userVisibleQualityStatus") in {"ready", "usable_with_boundary"}
        ),
        "negativeReviewCount": sum(
            1
            for item in items
            if item.get("usableAnswer") is False
            or item.get("userVisibleQualityStatus") in {"degraded", "needs_retry"}
        ),
        "lastReviewedAt": items[0]["createdAt"] if items else None,
        "proposalCreatedFromAnswerCount": 0,
        "executionTicketCreatedFromAnswerCount": 0,
        "metricErrors": [],
    }


def _dna_delta_payload(row: sqlite3.Row) -> dict[str, Any]:
    source = _json(row["source_payload_json"], {})
    if not isinstance(source, Mapping):
        source = {}
    return {
        "id": row["intelligence_id"],
        "clientId": row["project_id"],
        "dimension": source.get("dimension") or "",
        "previousVersion": source.get("previousVersion"),
        "originType": source.get("originType") or "human_override",
        "authorityLevel": source.get("authorityLevel") or "candidate",
        "qualityTier": source.get("qualityTier") or "normalized",
        "supersedesId": source.get("supersedesId"),
        "sourceSnapshotHash": source.get("sourceSnapshotHash") or "",
        "staleReason": source.get("staleReason"),
        "invalidatedBy": source.get("invalidatedBy"),
        "proposedChange": source.get("proposedChange") or "",
        "summary": row["summary"],
        "evidenceIds": list(source.get("evidenceIds") or []),
        "confidence": source.get("confidence") or "medium",
        "status": source.get("reviewStatus") or "awaiting_review",
        "contextPackId": source.get("contextPackId"),
        "version": int(row["version"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def create_dna_delta(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    project_id = str(payload.get("clientId") or "").strip()
    dimension = str(payload.get("dimension") or "").strip()
    proposed_change = str(payload.get("proposedChange") or "").strip()
    if not project_id or not dimension or not proposed_change:
        raise RepositoryError(
            422,
            "dna_delta_fields_required",
            "clientId、dimension 与 proposedChange 不能为空",
        )
    evidence_ids = [
        str(item)
        for item in (payload.get("evidenceIds") or [])
        if str(item).strip()
    ]
    confidence = str(payload.get("confidence") or "medium")
    if confidence not in {"low", "medium", "high"}:
        raise RepositoryError(422, "dna_delta_confidence_invalid", "DNA 置信度无效")
    command_type = "workbench.dna_delta.created"
    summary = str(payload.get("summary") or proposed_change).strip()
    normalized = {
        "projectId": project_id,
        "dimension": dimension,
        "proposedChange": proposed_change,
        "summary": summary,
        "evidenceIds": evidence_ids,
        "confidence": confidence,
        "contextPackId": payload.get("contextPackId"),
    }
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            _require_project_editor(repository, connection, identity, project_id)
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            if evidence_ids:
                placeholders = ",".join("?" for _ in evidence_ids)
                valid_ids = {
                    str(row["evidence_link_id"])
                    for row in connection.execute(
                        f"""
                        SELECT e.evidence_link_id
                        FROM evidence_links e
                        LEFT JOIN task_records t
                          ON e.target_type = 'task' AND t.task_id = e.target_id
                        LEFT JOIN event_line_records el
                          ON e.target_type = 'event_line'
                         AND el.event_line_id = e.target_id
                        LEFT JOIN narrative_outputs n
                          ON e.target_type = 'narrative_output'
                         AND n.narrative_output_id = e.target_id
                        WHERE e.organization_id = ?
                          AND e.evidence_link_id IN ({placeholders})
                          AND e.lifecycle_state = 'active'
                          AND COALESCE(t.project_id, el.project_id, n.project_id) = ?
                        """,
                        (
                            identity.organization_id,
                            *evidence_ids,
                            project_id,
                        ),
                    ).fetchall()
                }
                if valid_ids != set(evidence_ids):
                    raise RepositoryError(
                        422,
                        "dna_delta_evidence_invalid",
                        "DNA delta 引用了不属于当前项目的证据",
                    )
            previous = connection.execute(
                """
                SELECT *
                FROM intelligence_records
                WHERE organization_id = ? AND project_id = ?
                  AND record_kind = ? AND status != 'archived'
                  AND json_extract(source_payload_json, '$.dimension') = ?
                ORDER BY created_at DESC, intelligence_id
                LIMIT 1
                """,
                (
                    identity.organization_id,
                    project_id,
                    DNA_DELTA_RECORD_KIND,
                    dimension,
                ),
            ).fetchone()
            previous_id = str(previous["intelligence_id"]) if previous else None
            snapshot_hash = sha256_text(
                canonical_json(
                    {
                        "projectId": project_id,
                        "dimension": dimension,
                        "previousId": previous_id,
                        "evidenceIds": evidence_ids,
                    }
                )
            )
            source = {
                "dimension": dimension,
                "previousVersion": previous_id,
                "originType": "human_override",
                "authorityLevel": "candidate",
                "qualityTier": "normalized",
                "supersedesId": previous_id,
                "sourceSnapshotHash": snapshot_hash,
                "proposedChange": proposed_change,
                "evidenceIds": evidence_ids,
                "confidence": confidence,
                "reviewStatus": "awaiting_review",
                "contextPackId": payload.get("contextPackId"),
            }
            delta_id = new_id()
            now = utc_now()
            title = f"DNA 变化提案：{dimension}"
            connection.execute(
                """
                INSERT INTO intelligence_records (
                    intelligence_id, organization_id, project_id, title, summary,
                    source_url, record_kind, status, visibility_scope,
                    created_by_membership_id, source_payload_json, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', ?, 'candidate', 'participants',
                          ?, ?, 1, ?, ?)
                """,
                (
                    delta_id,
                    identity.organization_id,
                    project_id,
                    title,
                    summary,
                    DNA_DELTA_RECORD_KIND,
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
                    delta_id,
                    title,
                    summary,
                    identity.membership_id,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM intelligence_records
                WHERE organization_id = ? AND intelligence_id = ?
                """,
                (identity.organization_id, delta_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(500, "dna_delta_lost", "DNA delta 保存后无法读取")
            result = _dna_delta_payload(row)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="intelligence",
                aggregate_id=delta_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "dimension": dimension,
                    "supersedesId": previous_id,
                    "evidenceIds": evidence_ids,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _answer_quality_failure_payload(row: sqlite3.Row) -> dict[str, Any] | None:
    source = _json(row["source_payload_json"], {})
    if not isinstance(source, Mapping):
        return None
    quality_status = str(source.get("userVisibleQualityStatus") or "ready")
    unusable = source.get("usableAnswer") is False
    retry = bool(source.get("shouldShowRetryBanner"))
    if not unusable and not retry and quality_status not in {"degraded", "needs_retry"}:
        return None
    failure_type = (
        "retry_banner"
        if retry or quality_status == "needs_retry"
        else "user_marked_not_usable"
        if unusable
        else "no_direct_answer"
    )
    return {
        "id": row["intelligence_id"],
        "clientId": row["project_id"],
        "messageId": source.get("messageId"),
        "prompt": source.get("prompt") or "",
        "failureType": failure_type,
        "severity": "high" if quality_status == "needs_retry" else "medium",
        "details": {
            "answerMode": source.get("answerMode") or "",
            "qualityStatus": quality_status,
            "reviewerNote": source.get("reviewerNote") or "",
            "resolutionNote": source.get("resolutionNote") or "",
        },
        "status": source.get("resolutionStatus") or "open",
        "version": int(row["version"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_answer_quality_failures(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str | None = None,
    limit: int = 80,
) -> list[dict[str, Any]]:
    visible_ids = {
        item["projectId"] for item in repository.business_snapshot(identity)["projects"]
    }
    if project_id is not None and project_id not in visible_ids:
        raise RepositoryError(404, "project_missing", "当前成员无法访问该项目")
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            """
            SELECT *
            FROM intelligence_records
            WHERE organization_id = ? AND record_kind = ?
              AND created_by_membership_id = ? AND status != 'archived'
            ORDER BY created_at DESC, intelligence_id
            LIMIT ?
            """,
            (
                identity.organization_id,
                ANSWER_VALUE_REVIEW_KIND,
                identity.membership_id,
                max(1, min(500, limit)),
            ),
        ).fetchall()
    items = [_answer_quality_failure_payload(row) for row in rows]
    return [
        item
        for item in items
        if item is not None
        and str(item["clientId"]) in visible_ids
        and (project_id is None or item["clientId"] == project_id)
    ]


def answer_quality_failure_detail(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    failure_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            """
            SELECT *
            FROM intelligence_records
            WHERE organization_id = ? AND intelligence_id = ?
              AND record_kind = ? AND created_by_membership_id = ?
            """,
            (
                identity.organization_id,
                failure_id,
                ANSWER_VALUE_REVIEW_KIND,
                identity.membership_id,
            ),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "answer_quality_failure_missing", "回答质量失败不存在")
        _require_project(
            repository,
            connection,
            identity,
            str(row["project_id"]),
        )
        result = _answer_quality_failure_payload(row)
    if result is None:
        raise RepositoryError(404, "answer_quality_failure_missing", "该评审不是质量失败")
    return result


def resolve_answer_quality_failure(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    failure_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    expected = _expected_version(payload)
    note = str(payload.get("note") or "").strip()
    normalized = {
        "failureId": failure_id,
        "note": note,
    }
    command_type = "workbench.answer_quality_failure.resolved"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = connection.execute(
                """
                SELECT *
                FROM intelligence_records
                WHERE organization_id = ? AND intelligence_id = ?
                  AND record_kind = ? AND created_by_membership_id = ?
                """,
                (
                    identity.organization_id,
                    failure_id,
                    ANSWER_VALUE_REVIEW_KIND,
                    identity.membership_id,
                ),
            ).fetchone()
            if row is None or _answer_quality_failure_payload(row) is None:
                raise RepositoryError(
                    404,
                    "answer_quality_failure_missing",
                    "回答质量失败不存在",
                )
            _require_project(
                repository,
                connection,
                identity,
                str(row["project_id"]),
            )
            if int(row["version"]) != expected:
                raise RepositoryError(
                    409,
                    "answer_quality_failure_version_conflict",
                    "回答质量失败已被其他操作更新",
                )
            source = _json(row["source_payload_json"], {})
            source = dict(source) if isinstance(source, Mapping) else {}
            source.update(
                {
                    "resolutionStatus": "resolved",
                    "resolutionNote": note,
                    "resolvedAt": utc_now(),
                    "resolvedByMembershipId": identity.membership_id,
                }
            )
            now = utc_now()
            next_version = expected + 1
            cursor = connection.execute(
                """
                UPDATE intelligence_records
                SET source_payload_json = ?, version = version + 1, updated_at = ?
                WHERE organization_id = ? AND intelligence_id = ? AND version = ?
                """,
                (
                    canonical_json(source),
                    now,
                    identity.organization_id,
                    failure_id,
                    expected,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryError(
                    409,
                    "answer_quality_failure_version_conflict",
                    "回答质量失败已被其他操作更新",
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
                    failure_id,
                    next_version,
                    row["title"],
                    f"{row['summary']}；已处理：{note}" if note else row["summary"],
                    identity.membership_id,
                    now,
                ),
            )
            updated = connection.execute(
                """
                SELECT * FROM intelligence_records
                WHERE organization_id = ? AND intelligence_id = ?
                """,
                (identity.organization_id, failure_id),
            ).fetchone()
            if updated is None:
                raise RepositoryError(500, "answer_quality_failure_lost", "失败记录更新后无法读取")
            result = _answer_quality_failure_payload(updated)
            if result is None:
                raise RepositoryError(500, "answer_quality_failure_lost", "失败记录更新后无法读取")
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="intelligence",
                aggregate_id=failure_id,
                expected_version=expected,
                before_version=expected,
                after_version=next_version,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": row["project_id"],
                    "resolutionStatus": "resolved",
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _judgment_payload(row: sqlite3.Row) -> dict[str, Any]:
    content = _json(row["content_json"], {})
    judgment = content.get("judgment") if isinstance(content, Mapping) else {}
    if not isinstance(judgment, Mapping):
        judgment = {}
    return {
        "id": row["narrative_output_id"],
        "clientId": row["project_id"],
        "targetType": judgment.get("targetType") or "client",
        "targetId": judgment.get("targetId") or row["project_id"],
        "topic": judgment.get("topic") or row["title"],
        "version": int(row["content_version"]),
        "status": judgment.get("status") or "awaiting_review",
        "originType": judgment.get("originType") or "analysis",
        "authorityLevel": judgment.get("authorityLevel") or "candidate",
        "qualityTier": judgment.get("qualityTier") or "normalized",
        "supersedesId": judgment.get("supersedesId"),
        "sourceSnapshotHash": judgment.get("sourceSnapshotHash") or "",
        "staleReason": judgment.get("staleReason"),
        "invalidatedBy": judgment.get("invalidatedBy"),
        "summary": judgment.get("summary") or row["content_markdown"],
        "evidenceIds": list(judgment.get("evidenceIds") or []),
        "contextPackId": judgment.get("contextPackId"),
        "riskLevel": judgment.get("riskLevel") or "medium",
        "confidence": judgment.get("confidence") or "medium",
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "aggregateVersion": int(row["aggregate_version"]),
        "sourceAnswerId": content.get("sourceAnswerId"),
        "reviewNote": judgment.get("reviewNote") or "",
    }


def _judgment_row(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    judgment_id: str,
    *,
    require_edit: bool,
) -> sqlite3.Row:
    row = connection.execute(
        _report_select()
        + """
        WHERE n.organization_id = ? AND n.narrative_output_id = ?
          AND n.output_kind = 'strategy_report'
          AND json_extract(v.content_json, '$.workbenchKind') = ?
        """,
        (identity.organization_id, judgment_id, JUDGMENT_CONTENT_KIND),
    ).fetchone()
    if row is None:
        raise RepositoryError(404, "judgment_missing", "判断版本不存在")
    if require_edit:
        _require_project_editor(
            repository,
            connection,
            identity,
            str(row["project_id"]),
        )
    else:
        _require_project(
            repository,
            connection,
            identity,
            str(row["project_id"]),
        )
    return row


def judgment_detail(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    judgment_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = _judgment_row(
            repository,
            connection,
            identity,
            judgment_id,
            require_edit=False,
        )
        return _judgment_payload(row)


def list_project_judgments(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        rows = connection.execute(
            _report_select()
            + """
            WHERE n.organization_id = ? AND n.project_id = ?
              AND n.output_kind = 'strategy_report'
              AND n.lifecycle_state != 'archived'
              AND json_extract(v.content_json, '$.workbenchKind') = ?
            ORDER BY n.updated_at DESC, n.narrative_output_id
            """,
            (identity.organization_id, project_id, JUDGMENT_CONTENT_KIND),
        ).fetchall()
    return [_judgment_payload(row) for row in rows]


def promote_answer_to_judgment(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    answer_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    command_type = "workbench.answer.promoted_to_judgment"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            answer = connection.execute(
                """
                SELECT *
                FROM ai_answers
                WHERE organization_id = ? AND membership_id = ?
                  AND ai_answer_id = ? AND lifecycle_state = 'active'
                """,
                (identity.organization_id, identity.membership_id, answer_id),
            ).fetchone()
            if answer is None:
                raise RepositoryError(404, "answer_missing", "工作台回答不存在")
            project_id = str(answer["project_id"] or "")
            if not project_id:
                raise RepositoryError(
                    422,
                    "answer_project_context_required",
                    "回答没有固定项目 WorkspaceContext",
                )
            _require_project_editor(repository, connection, identity, project_id)
            source_manifest = _json(answer["source_manifest_json"], {})
            evidence_ids = (
                list(source_manifest.get("evidenceLinkIds") or [])
                if isinstance(source_manifest, Mapping)
                else []
            )
            note = str(payload.get("note") or "").strip()
            snapshot_hash = sha256_text(
                canonical_json(
                    {
                        "answerId": answer_id,
                        "answerVersion": answer["version"],
                        "answerMarkdown": answer["answer_markdown"],
                        "evidenceIds": evidence_ids,
                    }
                )
            )
            normalized = {
                "answerId": answer_id,
                "answerVersion": int(answer["version"]),
                "projectId": project_id,
                "note": note,
                "sourceSnapshotHash": snapshot_hash,
            }
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            judgment_id = new_id()
            now = utc_now()
            topic = str(answer["question"]).strip()[:200] or "工作台回答判断"
            summary = str(answer["answer_markdown"]).strip()
            judgment = {
                "targetType": "client",
                "targetId": project_id,
                "topic": topic,
                "status": "awaiting_review",
                "originType": "analysis",
                "authorityLevel": "candidate",
                "qualityTier": "normalized",
                "sourceSnapshotHash": snapshot_hash,
                "summary": summary,
                "evidenceIds": evidence_ids,
                "riskLevel": "medium",
                "confidence": "medium",
                "reviewNote": note,
            }
            content = {
                "workbenchKind": JUDGMENT_CONTENT_KIND,
                "sourceAnswerId": answer_id,
                "sourceAnswerVersion": int(answer["version"]),
                "judgment": judgment,
            }
            connection.execute(
                """
                INSERT INTO narrative_outputs (
                    narrative_output_id, organization_id, project_id,
                    event_line_id, output_kind, title, lifecycle_state,
                    latest_version, created_by_membership_id, version,
                    created_at, updated_at, archived_at
                ) VALUES (?, ?, ?, NULL, 'strategy_report', ?, 'draft',
                          1, ?, 1, ?, ?, NULL)
                """,
                (
                    judgment_id,
                    identity.organization_id,
                    project_id,
                    topic,
                    identity.membership_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO narrative_output_versions (
                    narrative_output_version_id, organization_id,
                    narrative_output_id, version, content_markdown,
                    content_json, input_fingerprint, content_hash,
                    change_summary, created_by_membership_id, created_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    judgment_id,
                    summary,
                    canonical_json(content),
                    snapshot_hash,
                    sha256_text(summary),
                    "由严格工作台回答提升为候选判断",
                    identity.membership_id,
                    now,
                ),
            )
            row = _judgment_row(
                repository,
                connection,
                identity,
                judgment_id,
                require_edit=False,
            )
            result = _judgment_payload(row)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="narrative_output",
                aggregate_id=judgment_id,
                expected_version=None,
                before_version=None,
                after_version=1,
                payload=normalized,
                result=result,
                audit_summary={
                    "projectId": project_id,
                    "sourceAnswerId": answer_id,
                    "sourceSnapshotHash": snapshot_hash,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def confirm_judgment(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    judgment_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    decision = str(payload.get("action") or "").strip()
    target_status = {
        "approved": "approved",
        "rejected": "rejected",
        "returned_for_revision": "awaiting_revision",
    }.get(decision)
    if target_status is None:
        raise RepositoryError(422, "judgment_decision_invalid", "判断审批动作无效")
    expected = _expected_version(payload)
    note = str(payload.get("note") or "").strip()
    normalized = {
        "judgmentId": judgment_id,
        "action": decision,
        "note": note,
    }
    command_type = "workbench.judgment.confirmed"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing, _ = _receipt(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=normalized,
            )
            if existing is not None:
                connection.rollback()
                return existing
            row = _judgment_row(
                repository,
                connection,
                identity,
                judgment_id,
                require_edit=True,
            )
            current_aggregate_version = int(row["aggregate_version"])
            if current_aggregate_version != expected:
                raise RepositoryError(
                    409,
                    "judgment_version_conflict",
                    "判断已被其他成员更新",
                )
            content = _json(row["content_json"], {})
            if not isinstance(content, Mapping):
                content = {}
            content = dict(content)
            judgment = content.get("judgment")
            judgment = dict(judgment) if isinstance(judgment, Mapping) else {}
            judgment.update(
                {
                    "status": target_status,
                    "authorityLevel": (
                        "approved" if target_status == "approved" else "candidate"
                    ),
                    "qualityTier": (
                        "reviewed" if target_status in {"approved", "rejected"} else "normalized"
                    ),
                    "reviewNote": note,
                    "reviewedByMembershipId": identity.membership_id,
                    "reviewedAt": utc_now(),
                }
            )
            content["judgment"] = judgment
            next_content_version = int(row["content_version"]) + 1
            next_aggregate_version = current_aggregate_version + 1
            now = utc_now()
            content_markdown = str(row["content_markdown"])
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
                    judgment_id,
                    next_content_version,
                    content_markdown,
                    canonical_json(content),
                    str(row["input_fingerprint"]),
                    sha256_text(content_markdown),
                    f"判断审批：{decision}",
                    identity.membership_id,
                    now,
                ),
            )
            cursor = connection.execute(
                """
                UPDATE narrative_outputs
                SET lifecycle_state = ?, latest_version = ?,
                    version = version + 1, updated_at = ?
                WHERE organization_id = ? AND narrative_output_id = ?
                  AND version = ?
                """,
                (
                    "active" if target_status == "approved" else "blocked",
                    next_content_version,
                    now,
                    identity.organization_id,
                    judgment_id,
                    expected,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryError(
                    409,
                    "judgment_version_conflict",
                    "判断已被其他成员更新",
                )
            updated = _judgment_row(
                repository,
                connection,
                identity,
                judgment_id,
                require_edit=False,
            )
            result = _judgment_payload(updated)
            _record_command(
                repository,
                connection,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="narrative_output",
                aggregate_id=judgment_id,
                expected_version=expected,
                before_version=expected,
                after_version=next_aggregate_version,
                payload=normalized,
                result=result,
                audit_summary={
                    "decision": decision,
                    "projectId": row["project_id"],
                    "contentVersion": next_content_version,
                },
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
