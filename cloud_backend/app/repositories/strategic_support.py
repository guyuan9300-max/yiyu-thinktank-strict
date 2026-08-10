"""Strict 88-table support for retained strategic-accompaniment controls.

These handlers are deliberately narrow.  They replace the remaining legacy
column/table assumptions in the workbench compatibility surface without
introducing a second strategic database or a generic payload table.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .gc04_tasks import GC04TaskRepository


PROJECT_TEXT_KIND_PREFIX = "workbench_project_text:"
PROJECT_TEXT_KINDS = {
    "brand_proposition": "品牌主张",
    "strategic_doc:methodology": "方法论",
    "strategic_doc:strategy": "战略文档",
}


def _require_project(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    project_id: str,
    *,
    write: bool = False,
) -> sqlite3.Row:
    return repository._require_project_access(  # noqa: SLF001
        connection,
        identity,
        project_id=project_id,
        capability="knowledge_write" if write else "read",
    )


def _manifest_value(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        value = json.loads(str(row["receipt"] or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _secured_resource(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    resource_id: str,
    resource_kind: str,
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
            resource_kind,
            now,
            now,
            identity.cloud_instance_id,
        ),
    )


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
            SELECT d.*, v.content_hash, m.receipt
            FROM knowledge_documents AS d
            JOIN document_versions AS v
              ON v.scope_id=d.scope_id AND v.document_id=d.id
             AND v.version=d.current_version
            LEFT JOIN object_manifests AS m
              ON m.scope_id=v.scope_id AND m.id=v.object_manifest_id
            WHERE d.scope_id=? AND d.client_id=?
              AND d.document_kind LIKE ? AND d.lifecycle_state='active'
            ORDER BY d.updated_at DESC, d.id
            """,
            (identity.scope_id, project_id, f"{PROJECT_TEXT_KIND_PREFIX}%"),
        ).fetchall()
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row["document_kind"])[len(PROJECT_TEXT_KIND_PREFIX) :]
        value = _manifest_value(row)
        items.setdefault(
            key,
            {
                "key": key,
                "documentId": row["id"],
                "title": row["title"],
                "markdownContent": str(value.get("markdownContent") or ""),
                "contentHash": row["content_hash"],
                "version": int(row["version"] or 1),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            },
        )
    return items


def knowledge_status(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    """Project knowledge readiness from strict documents and attempts."""

    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        documents = connection.execute(
            """
            SELECT d.*, v.content_hash, v.id AS document_version_id
            FROM knowledge_documents AS d
            LEFT JOIN document_versions AS v
              ON v.scope_id=d.scope_id AND v.document_id=d.id
             AND v.version=d.current_version
            WHERE d.scope_id=? AND d.client_id=? AND d.lifecycle_state='active'
            ORDER BY d.updated_at DESC, d.id
            """,
            (identity.scope_id, project_id),
        ).fetchall()
        asset_ids = [str(row["source_asset_id"]) for row in documents if row["source_asset_id"]]
        attempt_rows: list[sqlite3.Row] = []
        if asset_ids:
            placeholders = ",".join("?" for _ in asset_ids)
            attempt_rows = connection.execute(
                f"SELECT * FROM processing_attempts WHERE scope_id=? "
                f"AND source_asset_id IN ({placeholders}) "
                "ORDER BY started_at DESC, id",
                (identity.scope_id, *asset_ids),
            ).fetchall()
    attempts = [
        {
            "processingAttemptId": row["id"],
            "sourceAssetId": row["source_asset_id"],
            "processingKind": row["processor_kind"],
            "state": row["status"],
            "attemptNo": int(row["attempt_no"] or 1),
            "errorCode": row["error_code"],
            "errorMessage": row["error_message_safe"],
            "startedAt": row["started_at"],
            "finishedAt": row["finished_at"],
            "createdAt": row["started_at"],
        }
        for row in attempt_rows
    ]
    items = [
        {
            "documentId": row["id"],
            "projectId": project_id,
            "sourceAssetId": row["source_asset_id"],
            "title": row["title"],
            "documentKind": row["document_kind"],
            "parseState": row["parse_state"],
            "publicationState": row["publication_state"],
            "currentVersion": int(row["current_version"] or 0),
            "version": int(row["version"] or 1),
            "contentHash": row["content_hash"],
            "documentVersionId": row["document_version_id"],
            "updatedAt": row["updated_at"],
        }
        for row in documents
    ]
    counts = {
        "total": len(items),
        "ready": sum(1 for row in documents if row["parse_state"] == "ready"),
        "partial": sum(1 for row in documents if row["parse_state"] == "partial_ready"),
        "failed": sum(1 for row in documents if row["parse_state"] in {"failed", "missing_source"}),
        "pending": sum(1 for row in documents if row["parse_state"] in {None, "not_requested", "queued", "processing"}),
    }
    return {
        "projectId": project_id,
        "state": "ready" if counts["ready"] else "partial" if counts["partial"] else "failed" if counts["failed"] else "empty",
        "counts": counts,
        "documents": items,
        "processingAttempts": attempts,
        "generatedAt": utc_now(),
    }


def list_dna_modules(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    items = project_text_items(repository, identity, project_id=project_id)
    modules = []
    for key, item in items.items():
        if not key.startswith("dna_term:"):
            continue
        module_key = key[len("dna_term:") :]
        markdown = str(item.get("markdownContent") or "")
        modules.append(
            {
                "clientId": project_id,
                "moduleKey": module_key,
                "title": item.get("title") or module_key,
                "markdownContent": markdown,
                "normalizedText": " ".join(markdown.split()),
                "summary": " ".join(markdown.split())[:1200],
                "fileName": None,
                "contentHash": item.get("contentHash"),
                "sourceKind": "manual",
                "missingInfo": [],
                "updatedAt": item.get("updatedAt"),
                "updatedBy": None,
                "hasDocument": True,
                "documentId": item.get("documentId"),
                "version": item.get("version"),
            }
        )
    return {"modules": modules}


def list_project_goals(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    tasks = GC04TaskRepository(repository).board(identity).get("tasks") or []
    return [
        {
            "id": item["id"],
            "clientId": project_id,
            "title": item.get("title") or "",
            "quarter": "",
            "progress": 100 if item.get("completed_at") else 0,
            "ownerName": next(
                (
                    member.get("display_name") or ""
                    for member in item.get("collaborators") or []
                    if member.get("role_key") == "owner"
                ),
                "",
            ),
            "status": "completed" if item.get("completed_at") else "active",
            "version": int(item.get("version") or 1),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
            "authorityType": "tasks(task_kind=goal)",
        }
        for item in tasks
        if str(item.get("client_id") or "") == project_id
        and str(item.get("task_kind") or "") == "goal"
    ]


def project_structure(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    workspace = __import__(
        "cloud_backend.app.repositories.workbench_outputs",
        fromlist=["project_workspace"],
    ).project_workspace(repository, identity, project_id=project_id)
    modules = []
    flows = []
    for line in workspace.get("eventLines") or []:
        line_id = str(line.get("eventLineId") or "")
        linked = [
            task
            for task in workspace.get("tasks") or []
            if str(task.get("eventLineId") or "") == line_id
        ]
        modules.append(
            {
                "id": line_id,
                "clientId": project_id,
                "name": line.get("name") or "",
                "alias": None,
                "goal": line.get("goal") or "",
                "description": line.get("background") or "",
                "ownerName": None,
                "deliverables": [task.get("title") or "" for task in linked],
                "keywords": [],
                "templateTasksJson": None,
                "createdAt": line.get("createdAt"),
                "updatedAt": line.get("updatedAt"),
                "authorityType": "event_lines",
                "version": line.get("version"),
            }
        )
        flows.extend(
            {
                "id": task["taskId"],
                "clientId": project_id,
                "moduleId": line_id,
                "moduleName": line.get("name") or "",
                "name": task.get("title") or "",
                "description": task.get("description") or "",
                "authorityType": "tasks.event_line_id",
                "version": task.get("version"),
            }
            for task in linked
        )
    return {"clientId": project_id, "modules": modules, "flows": flows}


def project_insights(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, list[dict[str, Any]]]:
    with repository._connection() as connection:  # noqa: SLF001
        _require_project(repository, connection, identity, project_id)
        rows = connection.execute(
            """
            SELECT i.*, m.receipt
            FROM intelligence_records AS i
            LEFT JOIN object_manifests AS m
              ON m.scope_id=i.scope_id AND m.id=i.summary_object_manifest_id
            WHERE i.scope_id=? AND i.client_id=? AND i.lifecycle_state='active'
            ORDER BY i.updated_at DESC, i.id
            """,
            (identity.scope_id, project_id),
        ).fetchall()
    judgments = []
    for row in rows:
        value = _manifest_value(row)
        if value.get("recordKind") == "suggestion_action":
            continue
        judgments.append(
            {
                "id": row["id"],
                "clientId": project_id,
                "title": row["title"] or "项目情报",
                "summary": str(value.get("summary") or value.get("statement") or ""),
                "status": row["verification_state"],
                "version": int(row["version"] or 1),
                "updatedAt": row["updated_at"],
                "authorityType": "intelligence_records",
            }
        )
    return {"judgments": judgments, "topics": [], "conflicts": [], "openQuestions": []}


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
    title = str(payload.get("title") or PROJECT_TEXT_KINDS.get(key) or "DNA 术语").strip()
    normalized = {
        "projectId": project_id,
        "key": key,
        "title": title,
        "markdownContent": markdown,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    command_type = f"workbench.project_text.{key.replace(':', '.')}.saved"
    task_repository = GC04TaskRepository(repository)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = task_repository._receipt(  # noqa: SLF001
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )
            if replay is not None:
                connection.rollback()
                return replay
            _require_project(repository, connection, identity, project_id, write=True)
            kind = f"{PROJECT_TEXT_KIND_PREFIX}{key}"
            row = connection.execute(
                "SELECT * FROM knowledge_documents WHERE scope_id=? AND client_id=? "
                "AND document_kind=? AND lifecycle_state='active' "
                "ORDER BY updated_at DESC, id LIMIT 1",
                (identity.scope_id, project_id, kind),
            ).fetchone()
            now = utc_now()
            if row is None:
                if expected != 0:
                    raise RepositoryError(409, "project_text_version_conflict", "项目文本尚未创建")
                document_id = new_id()
                aggregate_version = document_version = 1
                _secured_resource(
                    connection,
                    identity,
                    resource_id=document_id,
                    resource_kind="knowledge_document",
                    now=now,
                )
            else:
                current = int(row["version"] or 1)
                if expected != current:
                    raise RepositoryError(409, "project_text_version_conflict", "项目文本已被其他成员更新")
                document_id = str(row["id"])
                aggregate_version = current + 1
                document_version = int(row["current_version"] or 0) + 1
            manifest_value = {
                "schema": "yiyu.workbench-project-text.v1",
                "projectId": project_id,
                "key": key,
                "markdownContent": markdown,
            }
            manifest_id, content_hash, _ = task_repository._store_manifest(  # noqa: SLF001
                connection,
                identity,
                storage_kind="organization_shared_project_text",
                value=manifest_value,
                now=now,
            )
            if row is None:
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        id, scope_id, source_asset_id, client_id, current_version,
                        owner_membership_id, title, document_kind, visibility_scope,
                        parse_state, publication_state, published_at, version,
                        lifecycle_state, created_at, updated_at, deleted_at
                    ) VALUES (?, ?, NULL, ?, 1, ?, ?, ?, 'organization', 'ready',
                              'published', ?, 1, 'active', ?, ?, NULL)
                    """,
                    (
                        document_id,
                        identity.scope_id,
                        project_id,
                        identity.membership_id,
                        title,
                        kind,
                        now,
                        now,
                        now,
                    ),
                )
            else:
                changed = connection.execute(
                    "UPDATE knowledge_documents SET title=?, current_version=?, "
                    "version=version+1, parse_state='ready', publication_state='published', "
                    "published_at=?, updated_at=? WHERE scope_id=? AND id=? AND version=?",
                    (
                        title,
                        document_version,
                        now,
                        now,
                        identity.scope_id,
                        document_id,
                        expected,
                    ),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(409, "project_text_version_conflict", "项目文本已被其他成员更新")
            connection.execute(
                """
                INSERT INTO document_versions (
                    id, scope_id, document_id, version, content_hash, created_at,
                    object_manifest_id, source_asset_version, publication_state,
                    created_by_membership_id, origin_instance_id, integrity_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'published', ?, ?, ?)
                """,
                (
                    new_id(),
                    identity.scope_id,
                    document_id,
                    document_version,
                    content_hash,
                    now,
                    manifest_id,
                    identity.membership_id,
                    identity.cloud_instance_id,
                    content_hash,
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
            task_repository._record_command(  # noqa: SLF001
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                command_type=command_type,
                aggregate_type="knowledge_document",
                aggregate_id=document_id,
                aggregate_version=aggregate_version,
                expected_version=expected if row is not None else None,
                result=result,
                now=now,
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
    expected = int(payload.get("expectedVersion") or 0)
    if expected < 1:
        raise RepositoryError(422, "expected_version_required", "缺少有效的 expectedVersion")
    normalized = {"projectId": project_id, "key": key, "expectedVersion": expected}
    payload_hash = sha256_text(canonical_json(normalized))
    task_repository = GC04TaskRepository(repository)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = task_repository._receipt(  # noqa: SLF001
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )
            if replay is not None:
                connection.rollback()
                return replay
            _require_project(repository, connection, identity, project_id, write=True)
            row = connection.execute(
                "SELECT * FROM knowledge_documents WHERE scope_id=? AND client_id=? "
                "AND document_kind=? AND lifecycle_state='active' LIMIT 1",
                (identity.scope_id, project_id, f"{PROJECT_TEXT_KIND_PREFIX}{key}"),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "project_text_missing", "项目文本不存在")
            if int(row["version"] or 1) != expected:
                raise RepositoryError(409, "project_text_version_conflict", "项目文本已被其他成员更新")
            now = utc_now()
            changed = connection.execute(
                "UPDATE knowledge_documents SET lifecycle_state='deleted', deleted_at=?, "
                "version=version+1, updated_at=? WHERE scope_id=? AND id=? AND version=?",
                (now, now, identity.scope_id, row["id"], expected),
            )
            if changed.rowcount != 1:
                raise RepositoryError(409, "project_text_version_conflict", "项目文本已被其他成员更新")
            result = {"ok": True, "key": key, "documentId": row["id"], "version": expected + 1}
            task_repository._record_command(  # noqa: SLF001
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                command_type=f"workbench.project_text.{key.replace(':', '.')}.archived",
                aggregate_type="knowledge_document",
                aggregate_id=str(row["id"]),
                aggregate_version=expected + 1,
                expected_version=expected,
                result=result,
                now=now,
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _suggestion_id(identity: SessionIdentity, project_id: str, fingerprint: str) -> str:
    return "intel_" + sha256_text(
        f"suggestion\x1f{identity.scope_id}\x1f{project_id}\x1f{fingerprint}"
    )[:30]


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
            SELECT i.*, m.receipt
            FROM intelligence_records AS i
            JOIN object_manifests AS m
              ON m.scope_id=i.scope_id AND m.id=i.summary_object_manifest_id
            WHERE i.scope_id=? AND i.client_id=? AND i.lifecycle_state='active'
              AND json_extract(m.receipt, '$.recordKind')='suggestion_action'
            ORDER BY i.updated_at DESC, i.id
            """,
            (identity.scope_id, project_id),
        ).fetchall()
    result: dict[str, Any] = {
        "clientId": project_id,
        "promoted": [],
        "completed": [],
        "dismissed": [],
    }
    for row in rows:
        value = _manifest_value(row)
        action = str(value.get("action") or "promoted")
        item = {
            "fingerprint": value.get("fingerprint") or row["title"],
            "actor": value.get("actor") or "",
            "suggestionText": value.get("suggestionText") or "",
            "sourceDocTitle": value.get("sourceDocTitle") or "",
            "sourceDocId": value.get("sourceDocId") or "",
            "createdAt": row["created_at"],
            "id": row["id"],
            "version": int(row["version"] or 1),
        }
        result[action if action in {"promoted", "completed", "dismissed"} else "promoted"].append(item)
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
        "recordKind": "suggestion_action",
        "projectId": project_id,
        "fingerprint": normalized_fingerprint,
        "action": action,
        "actor": str(payload.get("actor") or ""),
        "suggestionText": str(payload.get("suggestionText") or ""),
        "sourceDocTitle": str(payload.get("sourceDocTitle") or ""),
        "sourceDocId": str(payload.get("sourceDocId") or ""),
        "archive": archive,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    record_id = _suggestion_id(identity, project_id, normalized_fingerprint)
    task_repository = GC04TaskRepository(repository)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = task_repository._receipt(  # noqa: SLF001
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )
            if replay is not None:
                connection.rollback()
                return replay
            _require_project(repository, connection, identity, project_id, write=True)
            row = connection.execute(
                "SELECT * FROM intelligence_records WHERE scope_id=? AND id=?",
                (identity.scope_id, record_id),
            ).fetchone()
            now = utc_now()
            if archive:
                if row is None or row["lifecycle_state"] != "active":
                    raise RepositoryError(404, "suggestion_log_missing", "建议处置记录不存在")
                before = int(row["version"] or 1)
                after = before + 1
                connection.execute(
                    "UPDATE intelligence_records SET lifecycle_state='deleted', deleted_at=?, "
                    "version=?, updated_at=? WHERE scope_id=? AND id=? AND version=?",
                    (now, after, now, identity.scope_id, record_id, before),
                )
                result = {"ok": True, "id": record_id, "version": after}
            else:
                manifest_id, _, _ = task_repository._store_manifest(  # noqa: SLF001
                    connection,
                    identity,
                    storage_kind="strategic_suggestion_action",
                    value=normalized,
                    now=now,
                )
                if row is None:
                    _secured_resource(
                        connection,
                        identity,
                        resource_id=record_id,
                        resource_kind="intelligence_record",
                        now=now,
                    )
                    before = None
                    after = 1
                    connection.execute(
                        """
                        INSERT INTO intelligence_records (
                            id, scope_id, client_id, event_line_id,
                            verification_state, version, source_set_id, title,
                            summary_object_manifest_id, trust_rule_id,
                            confirmed_by_membership_id, confirmed_at,
                            published_document_id, lifecycle_state,
                            created_at, updated_at, deleted_at
                        ) VALUES (?, ?, ?, NULL, 'verified', 1, NULL, ?, ?, NULL,
                                  ?, ?, NULL, 'active', ?, ?, NULL)
                        """,
                        (
                            record_id,
                            identity.scope_id,
                            project_id,
                            normalized_fingerprint,
                            manifest_id,
                            identity.membership_id,
                            now,
                            now,
                            now,
                        ),
                    )
                else:
                    before = int(row["version"] or 1)
                    after = before + 1
                    connection.execute(
                        "UPDATE intelligence_records SET summary_object_manifest_id=?, "
                        "verification_state='verified', confirmed_by_membership_id=?, "
                        "confirmed_at=?, lifecycle_state='active', deleted_at=NULL, "
                        "version=?, updated_at=? WHERE scope_id=? AND id=? AND version=?",
                        (
                            manifest_id,
                            identity.membership_id,
                            now,
                            after,
                            now,
                            identity.scope_id,
                            record_id,
                            before,
                        ),
                    )
                result = {"ok": True, "id": record_id, "version": after}
            task_repository._record_command(  # noqa: SLF001
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                command_type=(
                    "workbench.suggestion_action.archived"
                    if archive
                    else "workbench.suggestion_action.saved"
                ),
                aggregate_type="intelligence_record",
                aggregate_id=record_id,
                aggregate_version=after,
                expected_version=before,
                result=result,
                now=now,
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
        SELECT pa.*, sa.client_id, sa.display_name, sa.content_hash,
               a.id AS ai_answer_id
        FROM processing_attempts AS pa
        LEFT JOIN source_assets AS sa
          ON sa.scope_id=pa.scope_id AND sa.id=pa.source_asset_id
        LEFT JOIN ai_answers AS a
          ON a.scope_id=pa.scope_id AND a.id=pa.id
        WHERE pa.scope_id=? AND pa.id=?
        """,
        (identity.scope_id, job_id),
    ).fetchone()


def _analysis_job_payload(row: sqlite3.Row) -> dict[str, Any]:
    state = str(row["status"] or "queued")
    status = {"processing": "running", "partial": "completed"}.get(state, state)
    terminal = {"completed", "partial", "failed", "cancelled"}
    return {
        "id": row["id"],
        "jobType": row["processor_kind"],
        "clientId": row["client_id"] or "",
        "scopeType": "client",
        "scopeId": row["client_id"] or "",
        "status": status,
        "priority": "normal",
        "triggerType": "workbench_ai_answer" if row["ai_answer_id"] else "strict_processing_attempt",
        "intentProfile": "client_overview",
        "question": row["display_name"] or row["processor_kind"],
        "sourceSnapshot": row["ai_answer_id"] or row["source_asset_id"] or "",
        "sourceSnapshotHash": row["content_hash"] or "",
        "dedupeKey": f"{row['processor_kind']}:{row['source_asset_id'] or row['id']}",
        "featureFlags": {},
        "progress": 100 if state in terminal else 50 if state == "processing" else 0,
        "stageLabel": row["processor_kind"],
        "runLogId": row["id"],
        "error": row["error_message_safe"] or None,
        "lockedBy": None,
        "lockedAt": row["started_at"],
        "lockExpiresAt": None,
        "attemptCount": int(row["attempt_no"] or 1),
        "lastError": row["error_message_safe"] or None,
        "createdAt": row["started_at"],
        "updatedAt": row["finished_at"] or row["started_at"],
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
        raise RepositoryError(422, "analysis_job_identity_required", "分析任务缺少 answerId 或固定项目 WorkspaceContext")
    normalized = {"answerId": answer_id, "projectId": project_id, "jobType": job_type}
    payload_hash = sha256_text(canonical_json(normalized))
    task_repository = GC04TaskRepository(repository)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = task_repository._receipt(  # noqa: SLF001
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )
            if replay is not None:
                connection.rollback()
                return replay
            _require_project(repository, connection, identity, project_id)
            answer = connection.execute(
                "SELECT id,client_id FROM ai_answers WHERE scope_id=? AND id=? "
                "AND lifecycle_state='active'",
                (identity.scope_id, answer_id),
            ).fetchone()
            if answer is None or str(answer["client_id"] or "") != project_id:
                raise RepositoryError(404, "analysis_answer_missing", "分析任务对应的工作台回答不存在")
            if _analysis_job_row(connection, identity, answer_id) is not None:
                raise RepositoryError(409, "analysis_job_identity_conflict", "分析任务 ID 已被其他处理记录占用")
            now = utc_now()
            source_asset_id = new_id()
            _secured_resource(
                connection,
                identity,
                resource_id=source_asset_id,
                resource_kind="source_asset",
                now=now,
            )
            manifest_id, content_hash, _ = task_repository._store_manifest(  # noqa: SLF001
                connection,
                identity,
                storage_kind="workbench_analysis_job",
                value=normalized,
                now=now,
            )
            result = {
                "id": answer_id,
                "jobType": job_type,
                "clientId": project_id,
                "scopeType": "client",
                "scopeId": project_id,
                "status": "completed",
                "priority": "normal",
                "triggerType": "workbench_ai_answer",
                "intentProfile": "client_overview",
                "question": "工作台分析任务",
                "sourceSnapshot": answer_id,
                "sourceSnapshotHash": content_hash,
                "dedupeKey": f"{job_type}:{answer_id}",
                "featureFlags": {},
                "progress": 100,
                "stageLabel": job_type,
                "runLogId": answer_id,
                "error": None,
                "lockedBy": None,
                "lockedAt": now,
                "lockExpiresAt": None,
                "attemptCount": 1,
                "lastError": None,
                "createdAt": now,
                "updatedAt": now,
                "startedAt": now,
                "finishedAt": now,
                "authorityType": "processing_attempts",
            }
            operation_id, _ = task_repository._record_command(  # noqa: SLF001
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                command_type="workbench.analysis_job.completed",
                aggregate_type="processing_attempt",
                aggregate_id=answer_id,
                aggregate_version=1,
                expected_version=None,
                result=result,
                now=now,
            )
            connection.execute(
                """
                INSERT INTO source_assets (
                    id, scope_id, client_id, object_manifest_id, content_hash,
                    record_kind, source_kind, display_name, media_type, byte_size,
                    source_locator_nonlocal, parent_folder_id, asset_id, folder_id,
                    created_by_membership_id, availability_state, archived_at,
                    version, lifecycle_state, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, 'asset', 'workbench_analysis_job',
                          '工作台分析任务', 'application/json', 0, NULL, NULL, NULL,
                          NULL, ?, 'ready', NULL, 1, 'active', ?, ?, NULL, 'cloud', ?)
                """,
                (
                    source_asset_id,
                    identity.scope_id,
                    project_id,
                    manifest_id,
                    content_hash,
                    identity.membership_id,
                    now,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO processing_attempts (
                    id, scope_id, operation_id, source_asset_id, recording_id,
                    attempt_no, status, error_code, processor_kind,
                    provider_resource_id, error_message_safe, next_retry_at,
                    started_at, finished_at, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, NULL, 1, 'completed', NULL, ?, NULL, NULL,
                          NULL, ?, ?, 'cloud', ?)
                """,
                (
                    answer_id,
                    identity.scope_id,
                    operation_id,
                    source_asset_id,
                    job_type,
                    now,
                    now,
                    identity.cloud_instance_id,
                ),
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
        project_id = str(row["client_id"] or "")
        if not project_id:
            raise RepositoryError(409, "analysis_job_workspace_missing", "该处理记录没有固定项目 WorkspaceContext")
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
    return [
        {
            "id": job_id,
            "jobId": job_id,
            "stageName": job["jobType"],
            "status": status,
            "provider": "strict_processing_attempt",
            "modelName": None,
            "lane": "cloud_final",
            "cacheKey": None,
            "cacheHit": False,
            "degraded": False,
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
            SELECT proposal.*, manifest.receipt
            FROM ai_proposals AS proposal
            JOIN object_manifests AS manifest
              ON manifest.scope_id=proposal.scope_id
             AND manifest.id=proposal.payload_object_manifest_id
            WHERE proposal.scope_id=?
              AND proposal.operation_kind='meeting_action_candidate'
              AND proposal.status IN ('draft','pending_confirmation')
              AND proposal.lifecycle_state='active'
            ORDER BY proposal.updated_at DESC, proposal.id
            """,
            (identity.scope_id,),
        ).fetchall()
        task_rows = connection.execute(
            """
            SELECT task.id,task.title,task.description,task.due_date,task.lifecycle_state AS status,
                   task.source_id,task.version,task.created_at,meeting.title AS meeting_title
            FROM tasks AS task
            JOIN meetings AS meeting
              ON meeting.id=task.source_id AND meeting.scope_id=task.scope_id
             AND meeting.client_id=task.client_id
            WHERE task.scope_id=? AND task.client_id=?
              AND task.source_type='meeting'
              AND task.lifecycle_state='active' AND meeting.lifecycle_state='active'
            ORDER BY task.updated_at DESC,task.id
            """,
            (identity.scope_id, project_id),
        ).fetchall()
    high: list[dict[str, Any]] = []
    medium: list[dict[str, Any]] = []
    for row in rows:
        receipt = _manifest_value(row)
        if str(receipt.get("clientId") or "") != project_id:
            continue
        confidence = "high"
        item = {
            "actor": str(receipt.get("ownerHint") or identity.display_name),
            "text": str(receipt.get("title") or ""),
            "confidence": confidence,
            "sourceDocTitle": "正式会议纪要",
            "sourceDocId": str(receipt.get("minutesDocumentId") or ""),
            "sourceChunkIndex": 0,
            "importedAt": row["created_at"],
            "fingerprint": str(row["id"]),
            "proposalId": str(row["id"]),
            "meetingId": str(receipt.get("meetingId") or ""),
            "description": str(receipt.get("description") or ""),
            "dueDate": str(receipt.get("dueDate") or ""),
            "status": str(row["status"] or "pending_confirmation"),
            "taskWritePerformed": False,
            "version": int(row["version"] or 1),
        }
        (high if confidence == "high" else medium).append(item)
    for row in task_rows:
        high.append(
            {
                "actor": identity.display_name,
                "text": str(row["title"] or ""),
                "confidence": "high",
                "sourceDocTitle": str(row["meeting_title"] or "客户会议"),
                "sourceDocId": str(row["source_id"] or ""),
                "sourceChunkIndex": 0,
                "importedAt": row["created_at"],
                "fingerprint": f"meeting-task:{row['id']}",
                "proposalId": None,
                "taskId": str(row["id"]),
                "meetingId": str(row["source_id"] or ""),
                "description": str(row["description"] or ""),
                "dueDate": str(row["due_date"] or ""),
                "status": str(row["status"] or "todo"),
                "taskWritePerformed": True,
                "version": int(row["version"] or 1),
            }
        )
    return {
        "clientId": project_id,
        "high": high,
        "medium": medium,
        "totalHigh": len(high),
        "totalMedium": len(medium),
    }


__all__ = [
    "analysis_job_detail",
    "analysis_job_stages",
    "archive_project_text",
    "knowledge_status",
    "list_dna_modules",
    "list_project_goals",
    "meeting_action_items",
    "project_insights",
    "project_structure",
    "project_text_items",
    "register_analysis_job",
    "save_project_text",
    "suggestion_log",
    "write_suggestion_log",
]
