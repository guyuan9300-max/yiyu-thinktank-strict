from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .workbench_outputs import _safe_manifest


REPORT_KINDS = frozenset({"event_line_report", "weekly_report", "strategy_report"})


def _record_operation(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    command_type: str,
    idempotency_key: str,
    aggregate_id: str,
    expected_version: int | None,
    aggregate_version: int,
    payload_hash: str,
    result_manifest_id: str,
    result_hash: str,
    now: str,
) -> None:
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id, command_type, idempotency_key
    )
    connection.execute(
        """
        INSERT INTO idempotency_records (
            id, scope_id, idempotency_key, payload_hash, result_hash,
            expires_at, result_object_manifest_id, status, created_at,
            authority_role, origin_instance_id
        ) VALUES (?, ?, ?, ?, ?, '9999-12-31T23:59:59.999Z', ?,
                  'settled', ?, 'cloud', ?)
        """,
        (
            repository._record_id("idem", operation_id, command_type),  # noqa: SLF001
            identity.scope_id, idempotency_key, payload_hash, result_hash,
            result_manifest_id, now, repository.cloud_instance_id,
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
        ) VALUES (?, ?, ?, ?, 'narrative_output', ?, ?, ?, ?, NULL,
                  'settled', ?, ?, ?, ?, ?, 'cloud', ?)
        """,
        (
            repository._record_id("cmd", operation_id, command_type),  # noqa: SLF001
            identity.scope_id, operation_id, idempotency_key, aggregate_id,
            command_type, identity.principal_id, expected_version,
            identity.membership_id, result_manifest_id, payload_hash,
            now, now, repository.cloud_instance_id,
        ),
    )
    event_hash = sha256_text(
        f"{command_type}|{aggregate_id}|{aggregate_version}|{result_hash}"
    )
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
            repository._record_id("audit", operation_id, command_type),  # noqa: SLF001
            identity.scope_id, operation_id, identity.principal_id,
            command_type, event_hash, identity.membership_id, aggregate_id,
            result_manifest_id, now, repository.cloud_instance_id, now,
            event_hash,
        ),
    )
    connection.execute(
        """
        INSERT INTO outbox_events (
            id, scope_id, operation_id, aggregate_version, event_type,
            status, aggregate_type, aggregate_id, event_object_manifest_id,
            event_hash, available_at, published_at, authority_role,
            origin_instance_id
        ) VALUES (?, ?, ?, ?, ?, 'published', 'narrative_output', ?, ?, ?,
                  ?, ?, 'cloud', ?)
        """,
        (
            repository._record_id("evt", operation_id, command_type),  # noqa: SLF001
            identity.scope_id, operation_id, aggregate_version,
            command_type, aggregate_id, result_manifest_id, event_hash,
            now, now, repository.cloud_instance_id,
        ),
    )


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _expected_version(payload: Mapping[str, Any]) -> int:
    try:
        value = int(payload.get("expectedVersion") or payload.get("expected_version"))
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise RepositoryError(422, "expected_version_required", "缺少有效的 expectedVersion")
    return value


def _report_row(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    report_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT n.*, v.id AS artifact_version_id,
               v.version AS content_version, v.content_hash,
               v.object_manifest_id, v.source_set_id AS version_source_set_id,
               v.publication_state AS version_publication_state,
               v.created_at AS version_created_at, manifest.receipt
        FROM narrative_outputs AS n
        JOIN artifact_versions AS v
          ON v.scope_id=n.scope_id AND v.artifact_id=n.id
         AND v.version=n.current_version
        JOIN object_manifests AS manifest
          ON manifest.scope_id=v.scope_id AND manifest.id=v.object_manifest_id
        WHERE n.scope_id=? AND n.id=? AND n.lifecycle_state!='deleted'
        """,
        (identity.scope_id, report_id),
    ).fetchone()


def _payload(row: sqlite3.Row) -> dict[str, Any]:
    receipt = _json(row["receipt"], {})
    content_json = receipt.get("contentJson") if isinstance(receipt, Mapping) else {}
    content_json = dict(content_json) if isinstance(content_json, Mapping) else {}
    markdown = str(receipt.get("contentMarkdown") or "") if isinstance(receipt, Mapping) else ""
    change_summary = str(receipt.get("changeSummary") or "") if isinstance(receipt, Mapping) else ""
    version_title = (
        str(receipt.get("title") or "").strip()
        if isinstance(receipt, Mapping)
        else ""
    ) or str(row["title"] or "项目报告")
    current_version = int(row["current_version"] or 1)
    latest = {
        "id": str(row["artifact_version_id"]),
        "artifact_id": str(row["id"]),
        "version": current_version,
        "title": version_title,
        "content_markdown": markdown,
        "content_payload": content_json,
        "source_set_id": str(row["version_source_set_id"] or ""),
        "narrative_id": str(row["id"]),
        "narrative_rev": current_version,
        "event_line_version": int(content_json.get("eventLineVersion") or 0),
        "input_fingerprint": str(content_json.get("inputFingerprint") or ""),
        "security_label_set_version": "",
        "content_hash": str(row["content_hash"] or ""),
        "change_summary": change_summary,
        "created_by_display_name": "",
        "restored_from_version": content_json.get("restoredFromVersion"),
        "created_at": str(row["version_created_at"] or row["updated_at"]),
    }
    lifecycle = str(row["lifecycle_state"] or "active")
    return {
        "id": str(row["id"]),
        "event_line_id": content_json.get("eventLineId"),
        "client_id": str(row["client_id"] or ""),
        "title": version_title,
        "status": lifecycle if lifecycle in {"active", "stale", "archived"} else "active",
        "latest_version": current_version,
        "is_stale": lifecycle == "stale",
        "availability_status": "stale" if lifecycle == "stale" else "blocked" if lifecycle == "blocked" else "ready",
        "availability_reason": "权威输入已有更新" if lifecycle == "stale" else "产物当前不可用" if lifecycle == "blocked" else "",
        "stale_reasons": ["权威输入已有更新"] if lifecycle == "stale" else [],
        "updated_at": str(row["updated_at"]),
        "aggregateVersion": int(row["version"] or current_version),
        "outputKind": str(row["artifact_kind"] or "strategy_report"),
        "latest": latest,
    }


def _saved_run(artifact: Mapping[str, Any]) -> dict[str, Any]:
    latest = artifact.get("latest") or {}
    content_json = latest.get("content_payload") or {}
    sections = content_json.get("sections") if isinstance(content_json, Mapping) else []
    sections = sections if isinstance(sections, list) else []
    return {
        "id": artifact["id"], "client_id": artifact.get("client_id"),
        "event_line_id": content_json.get("eventLineId"),
        "period_start": content_json.get("period_start"),
        "period_end": content_json.get("period_end"),
        "intent_hint": content_json.get("intent_hint"), "status": "saved",
        "blueprint": content_json.get("blueprint"),
        "sections_status": ["done" for _ in sections], "sections": sections,
        "body_markdown": latest.get("content_markdown") or "", "warnings": [],
        "source_set_id": latest.get("source_set_id") or "",
        "narrative_id": artifact["id"],
        "narrative_rev": artifact.get("latest_version") or 1,
        "event_line_version": int(content_json.get("eventLineVersion") or 0),
        "input_fingerprint": content_json.get("inputFingerprint") or "",
        "artifact": dict(artifact), "saved_at": artifact.get("updated_at"),
        "error_message": None, "output_files": {}, "total_llm_tokens": 0,
        "created_at": latest.get("created_at") or artifact.get("updated_at"),
        "updated_at": artifact.get("updated_at"),
    }


def _safe_sources(content_json: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = content_json.get("sourceManifest") or content_json.get("source_manifest") or []
    if isinstance(raw, Mapping):
        raw = raw.get("sources") or []
    if not isinstance(raw, list):
        return []
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        source_id = str(item.get("sourceId") or item.get("id") or "").strip()
        source_kind = str(item.get("sourceType") or item.get("kind") or "knowledge_document").strip()
        try:
            version = max(1, int(item.get("version") or 1))
        except (TypeError, ValueError):
            version = 1
        if not source_id or not source_kind:
            continue
        key = (source_kind, source_id, version)
        if key in seen:
            continue
        seen.add(key)
        sources.append({"id": source_id, "kind": source_kind[:80], "version": version})
    return sources[:100]


def _insert_source_set(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    project_id: str,
    content_json: Mapping[str, Any],
    now: str,
) -> tuple[str, str, str]:
    source_set_id = new_id()
    sources = _safe_sources(content_json)
    connection.execute(
        """
        INSERT INTO source_sets (
            id, scope_id, client_id, security_label_set_version,
            source_count, version, purpose_kind, publication_state,
            created_by_principal_id, created_at, expires_at,
            lifecycle_state, updated_at, deleted_at, authority_role,
            origin_instance_id
        ) VALUES (?, ?, ?, NULL, ?, 1, 'report_generation', 'published',
                  ?, ?, NULL, 'active', ?, NULL, 'cloud', ?)
        """,
        (
            source_set_id,
            identity.scope_id,
            project_id,
            len(sources),
            identity.principal_id,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    for ordinal, source in enumerate(sources):
        connection.execute(
            """
            INSERT INTO source_set_members (
                id, scope_id, source_set_id, source_object_id, source_version,
                policy_version, source_object_kind, ordinal, added_at,
                removed_at, version, lifecycle_state, created_at, updated_at,
                deleted_at, authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, 1, 'active', ?, ?,
                      NULL, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, source_set_id, source["id"],
                source["version"], source["kind"], ordinal, now, now, now,
                repository.cloud_instance_id,
            ),
        )
    return source_set_id


def _insert_version(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    report_id: str,
    project_id: str,
    version: int,
    title: str,
    content_markdown: str,
    content_json: Mapping[str, Any],
    change_summary: str,
    source_set_id: str,
    now: str,
) -> str:
    manifest_id = new_id()
    artifact_version_id = new_id()
    safe_content = _safe_manifest(dict(content_json))
    receipt = canonical_json(
        {
            "title": title,
            "contentMarkdown": content_markdown,
            "contentJson": safe_content,
            "changeSummary": change_summary,
        }
    )
    content_hash = sha256_text(content_markdown)
    receipt_hash = sha256_text(receipt)
    connection.execute(
        """
        INSERT INTO object_manifests (
            id, scope_id, storage_key, content_hash, lifecycle_state, receipt,
            holder_role, holder_instance_id, storage_kind, byte_size,
            media_type, availability_state, receipt_hash, created_at,
            verified_at, deleted_at, authority_role, origin_instance_id
        ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud_report', ?,
                  'metadata_receipt', ?, 'text/markdown', 'ready', ?, ?, ?,
                  NULL, 'cloud', ?)
        """,
        (
            manifest_id, identity.scope_id, content_hash, receipt,
            repository.cloud_instance_id, len(content_markdown.encode("utf-8")),
            receipt_hash, now, now, repository.cloud_instance_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO artifact_versions (
            id, scope_id, artifact_id, version, content_hash,
            object_manifest_id, source_set_id, publication_state,
            created_by_membership_id, created_at, origin_instance_id,
            integrity_hash, authority_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'published', ?, ?, ?, ?, 'cloud')
        """,
        (
            artifact_version_id, identity.scope_id, report_id, version,
            content_hash, manifest_id, source_set_id, identity.membership_id,
            now, repository.cloud_instance_id,
            sha256_text(f"{report_id}|{version}|{content_hash}|{source_set_id}"),
        ),
    )
    connection.execute(
        """
        INSERT INTO derivation_lineage (
            id, scope_id, source_set_id, policy_version_id, grant_generation,
            derivative_kind, derivative_object_id, generator_version,
            generated_at, invalidated_at, source_version, authority_role,
            origin_instance_id
        ) VALUES (?, ?, ?, NULL, 1, 'narrative_output', ?,
                  'project_workspace_report_v1', ?, NULL, ?, 'cloud', ?)
        """,
        (
            new_id(), identity.scope_id, source_set_id, report_id, now,
            version, repository.cloud_instance_id,
        ),
    )
    return artifact_version_id, manifest_id, receipt_hash


def list_reports(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        repository._require_project_access(connection, identity, project_id=project_id)  # noqa: SLF001
        rows = connection.execute(
            "SELECT id FROM narrative_outputs WHERE scope_id=? AND client_id=? "
            "AND artifact_kind IN ('event_line_report','weekly_report','strategy_report') "
            "AND lifecycle_state!='deleted' ORDER BY updated_at DESC, id",
            (identity.scope_id, project_id),
        ).fetchall()
        result = []
        for item in rows:
            row = _report_row(connection, identity, str(item["id"]))
            if row is not None:
                result.append(_payload(row))
        return result


def report_detail(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    report_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = _report_row(connection, identity, report_id)
        if row is None:
            raise RepositoryError(404, "report_missing", "报告不存在")
        repository._require_project_access(  # noqa: SLF001
            connection, identity, project_id=str(row["client_id"])
        )
        return _payload(row)


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
            SELECT v.*, m.receipt, n.title
            FROM artifact_versions AS v
            JOIN narrative_outputs AS n
              ON n.scope_id=v.scope_id AND n.id=v.artifact_id
            JOIN object_manifests AS m
              ON m.scope_id=v.scope_id AND m.id=v.object_manifest_id
            WHERE v.scope_id=? AND v.artifact_id=?
            ORDER BY v.version DESC
            """,
            (identity.scope_id, report_id),
        ).fetchall()
    result = []
    for row in rows:
        receipt = _json(row["receipt"], {})
        content_json = receipt.get("contentJson") if isinstance(receipt, Mapping) else {}
        content_json = dict(content_json) if isinstance(content_json, Mapping) else {}
        result.append(
            {
                "id": str(row["id"]), "artifact_id": report_id,
                "version": int(row["version"]),
                "title": str(receipt.get("title") or row["title"] or "项目报告"),
                "content_markdown": str(receipt.get("contentMarkdown") or ""),
                "content_payload": content_json,
                "source_set_id": str(row["source_set_id"] or ""),
                "narrative_id": report_id, "narrative_rev": int(row["version"]),
                "event_line_version": int(content_json.get("eventLineVersion") or 0),
                "input_fingerprint": str(content_json.get("inputFingerprint") or ""),
                "security_label_set_version": "", "content_hash": str(row["content_hash"] or ""),
                "change_summary": str(receipt.get("changeSummary") or ""),
                "created_by_display_name": "",
                "restored_from_version": content_json.get("restoredFromVersion"),
                "created_at": str(row["created_at"] or ""),
                "isCurrent": int(row["version"]) == int(current["latest_version"]),
            }
        )
    return result


def create_report(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    project_id = str(payload.get("projectId") or "").strip()
    report_id = str(payload.get("reportId") or "").strip() or new_id()
    title = str(payload.get("title") or "项目报告").strip()
    artifact_kind = str(payload.get("outputKind") or "strategy_report").strip()
    markdown = str(payload.get("contentMarkdown") or "").strip()
    raw_content = payload.get("contentJson") or {}
    content_json = dict(raw_content) if isinstance(raw_content, Mapping) else {}
    if not project_id or not markdown:
        raise RepositoryError(422, "report_content_required", "项目和报告正文不能为空")
    if artifact_kind not in REPORT_KINDS:
        raise RepositoryError(422, "report_kind_invalid", "报告类型无效")
    normalized = {"reportId": report_id, "projectId": project_id, "title": title,
                  "outputKind": artifact_kind, "contentHash": sha256_text(markdown),
                  "contentJson": _safe_manifest(content_json)}
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            payload_hash = sha256_text(canonical_json(normalized))
            repository._require_project_access(connection, identity, project_id=project_id, capability="project_write")  # noqa: SLF001
            existing = repository._existing_command(  # noqa: SLF001
                connection, scope_id=identity.scope_id,
                idempotency_key=idempotency_key,
                command_type="workbench.report.created",
                payload_hash=payload_hash,
            )
            if existing is not None:
                row = _report_row(connection, identity, str(existing["aggregate_id"]))
                if row is None:
                    raise RepositoryError(409, "report_replay_unavailable", "原报告写入结果已不可用")
                result = _saved_run(_payload(row))
                result["idempotentReplay"] = True
                connection.commit()
                return result
            if _report_row(connection, identity, report_id) is not None:
                raise RepositoryError(409, "report_identity_conflict", "报告 ID 已存在")
            now = utc_now()
            source_set_id = _insert_source_set(repository, connection, identity, project_id=project_id, content_json=content_json, now=now)
            connection.execute(
                "INSERT INTO secured_resources (id, scope_id, resource_kind, lifecycle_state, version, resource_type_key, created_at, updated_at, deleted_at, authority_role, origin_instance_id) "
                "VALUES (?, ?, 'narrative_output', 'active', 1, ?, ?, ?, NULL, 'cloud', ?)",
                (report_id, identity.scope_id, artifact_kind, now, now, repository.cloud_instance_id),
            )
            connection.execute(
                "INSERT INTO narrative_outputs (id, scope_id, client_id, source_set_id, current_version, lifecycle_state, title, artifact_kind, visibility_scope, publication_state, owner_membership_id, published_at, version, created_at, updated_at, deleted_at, authority_role, origin_instance_id) "
                "VALUES (?, ?, ?, ?, 1, 'active', ?, ?, 'organization', 'published', ?, ?, 1, ?, ?, NULL, 'cloud', ?)",
                (report_id, identity.scope_id, project_id, source_set_id, title, artifact_kind, identity.membership_id, now, now, now, repository.cloud_instance_id),
            )
            _, manifest_id, receipt_hash = _insert_version(repository, connection, identity, report_id=report_id, project_id=project_id, version=1, title=title, content_markdown=markdown, content_json=content_json, change_summary="人工确认并首次保存", source_set_id=source_set_id, now=now)
            row = _report_row(connection, identity, report_id)
            if row is None:
                raise RepositoryError(500, "report_write_lost", "报告保存后无法读取")
            artifact = _payload(row)
            sections = content_json.get("sections") if isinstance(content_json.get("sections"), list) else []
            result = _saved_run(artifact)
            _record_operation(repository, connection, identity, command_type="workbench.report.created", idempotency_key=idempotency_key, aggregate_id=report_id, expected_version=None, aggregate_version=1, payload_hash=payload_hash, result_manifest_id=manifest_id, result_hash=receipt_hash, now=now)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def update_report(
    repository: CloudRepository,
    identity: SessionIdentity,
    *, report_id: str, payload: Mapping[str, Any], idempotency_key: str,
    restored_from_version: int | None = None,
) -> dict[str, Any]:
    expected = _expected_version(payload)
    command_type = "workbench.report.restored" if restored_from_version is not None else "workbench.report.updated"
    normalized = {"reportId": report_id, "expectedVersion": expected, "restoredFromVersion": restored_from_version,
                  "contentHash": sha256_text(str(payload.get("contentMarkdown") or ""))}
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            payload_hash = sha256_text(canonical_json(normalized))
            project_ref = connection.execute(
                "SELECT client_id FROM narrative_outputs WHERE scope_id=? "
                "AND id=? AND lifecycle_state='active'",
                (identity.scope_id, report_id),
            ).fetchone()
            if project_ref is None:
                raise RepositoryError(404, "report_missing", "报告不存在")
            project_id = str(project_ref["client_id"])
            repository._require_project_access(connection, identity, project_id=project_id, capability="project_write")  # noqa: SLF001
            existing = repository._existing_command(  # noqa: SLF001
                connection, scope_id=identity.scope_id,
                idempotency_key=idempotency_key, command_type=command_type,
                payload_hash=payload_hash,
            )
            if existing is not None:
                row = _report_row(connection, identity, report_id)
                if row is None:
                    raise RepositoryError(409, "report_replay_unavailable", "原报告写入结果已不可用")
                result = _payload(row)
                result["idempotentReplay"] = True
                connection.commit()
                return result
            current = _report_row(connection, identity, report_id)
            if current is None:
                raise RepositoryError(404, "report_missing", "报告不存在")
            if int(current["version"] or 0) != expected:
                raise RepositoryError(409, "report_version_conflict", "报告已被其他成员更新")
            current_receipt = _json(current["receipt"], {})
            if restored_from_version is not None:
                source = connection.execute(
                    "SELECT v.*, m.receipt FROM artifact_versions v JOIN object_manifests m ON m.scope_id=v.scope_id AND m.id=v.object_manifest_id WHERE v.scope_id=? AND v.artifact_id=? AND v.version=?",
                    (identity.scope_id, report_id, restored_from_version),
                ).fetchone()
                if source is None:
                    raise RepositoryError(404, "report_version_missing", "要恢复的报告版本不存在")
                restored = _json(source["receipt"], {})
                markdown = str(restored.get("contentMarkdown") or "")
                content_json = dict(restored.get("contentJson") or {})
                content_json["restoredFromVersion"] = restored_from_version
                title = str(
                    restored.get("title")
                    or current["title"]
                    or "项目报告"
                )
                change_summary = f"恢复自版本 {restored_from_version}"
                source_set_id = str(source["source_set_id"] or current["source_set_id"] or "")
            else:
                markdown = str(payload.get("contentMarkdown") or payload.get("content_markdown") or "").strip()
                if not markdown:
                    raise RepositoryError(422, "report_content_required", "报告正文不能为空")
                raw_content = payload.get("contentJson") or payload.get("content_payload") or current_receipt.get("contentJson") or {}
                content_json = dict(raw_content) if isinstance(raw_content, Mapping) else {}
                title = str(payload.get("title") or current["title"] or "项目报告").strip()
                change_summary = str(payload.get("changeSummary") or payload.get("change_summary") or "更新报告正文").strip()
                source_set_id = str(current["source_set_id"] or "")
            next_version = int(current["current_version"] or 0) + 1
            next_aggregate = expected + 1
            now = utc_now()
            _, manifest_id, receipt_hash = _insert_version(repository, connection, identity, report_id=report_id, project_id=project_id, version=next_version, title=title, content_markdown=markdown, content_json=content_json, change_summary=change_summary, source_set_id=source_set_id, now=now)
            cursor = connection.execute(
                "UPDATE narrative_outputs SET title=?, current_version=?, version=?, lifecycle_state='active', publication_state='published', updated_at=?, deleted_at=NULL WHERE scope_id=? AND id=? AND version=?",
                (title, next_version, next_aggregate, now, identity.scope_id, report_id, expected),
            )
            connection.execute(
                "UPDATE secured_resources SET version=?, lifecycle_state='active', updated_at=?, deleted_at=NULL WHERE scope_id=? AND id=?",
                (next_aggregate, now, identity.scope_id, report_id),
            )
            if cursor.rowcount != 1:
                raise RepositoryError(409, "report_version_conflict", "报告已被其他成员更新")
            updated = _report_row(connection, identity, report_id)
            if updated is None:
                raise RepositoryError(500, "report_update_lost", "报告更新后无法读取")
            result = _payload(updated)
            _record_operation(repository, connection, identity, command_type=command_type, idempotency_key=idempotency_key, aggregate_id=report_id, expected_version=expected, aggregate_version=next_aggregate, payload_hash=payload_hash, result_manifest_id=manifest_id, result_hash=receipt_hash, now=now)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _export_grant_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "grantId": str(row["id"]),
        "reportId": str(row["report_id"]),
        "reportVersion": int(row["report_version"] or 1),
        "sourceSetId": str(row["source_set_id"] or ""),
        "lineageId": str(row["lineage_id"] or ""),
        "exportKind": str(row["export_kind"] or "docx"),
        "status": str(row["status"] or "active"),
        "expiresAt": str(row["expires_at"] or ""),
        "version": int(row["version"] or 1),
        "retryable": False,
        "message": "导出授权已就绪",
    }


def _export_grant_row(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    grant_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT g.*, n.id AS report_id, n.current_version AS report_version
        FROM export_grants AS g
        JOIN derivation_lineage AS d
          ON d.scope_id=g.scope_id AND d.id=g.lineage_id
        JOIN narrative_outputs AS n
          ON n.scope_id=d.scope_id AND n.id=d.derivative_object_id
        WHERE g.scope_id=? AND g.id=? AND g.lifecycle_state='active'
        """,
        (identity.scope_id, grant_id),
    ).fetchone()


def issue_export_grant(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    report_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    export_kind = str(payload.get("exportKind") or "docx").strip().lower()
    if export_kind not in {"docx", "pdf", "markdown"}:
        raise RepositoryError(422, "report_export_kind_invalid", "不支持该导出格式")
    command_type = "workbench.report.export_grant.issued"
    normalized = {"reportId": report_id, "exportKind": export_kind}
    payload_hash = sha256_text(canonical_json(normalized))
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id, command_type, idempotency_key
    )
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            current = _report_row(connection, identity, report_id)
            if current is None:
                raise RepositoryError(404, "report_missing", "报告不存在")
            project_id = str(current["client_id"] or "")
            repository._require_project_access(  # noqa: SLF001
                connection, identity, project_id=project_id
            )
            existing = repository._existing_command(  # noqa: SLF001
                connection,
                scope_id=identity.scope_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                payload_hash=payload_hash,
            )
            if existing is not None:
                row = _export_grant_row(
                    connection, identity, str(existing["aggregate_id"])
                )
                if row is None:
                    raise RepositoryError(
                        409,
                        "report_export_grant_replay_unavailable",
                        "原导出授权已失效，请重新申请",
                    )
                result = _export_grant_payload(row)
                result["idempotentReplay"] = True
                connection.commit()
                return result
            source_set_id = str(
                current["version_source_set_id"] or current["source_set_id"] or ""
            )
            lineage = connection.execute(
                "SELECT id FROM derivation_lineage WHERE scope_id=? "
                "AND source_set_id=? AND derivative_kind='narrative_output' "
                "AND derivative_object_id=? AND invalidated_at IS NULL "
                "ORDER BY generated_at DESC,id DESC LIMIT 1",
                (identity.scope_id, source_set_id, report_id),
            ).fetchone()
            if lineage is None:
                raise RepositoryError(
                    409,
                    "report_export_lineage_missing",
                    "报告缺少可核验来源血统，暂不能导出",
                )
            now = utc_now()
            expires_at = (
                datetime.now(timezone.utc) + timedelta(minutes=30)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            grant_id = repository._record_id(  # noqa: SLF001
                "export_grant", operation_id, report_id
            )
            connection.execute(
                """
                INSERT INTO export_grants (
                    id,scope_id,source_set_id,lineage_id,grant_generation,
                    expires_at,status,grantee_principal_id,
                    grantee_membership_id,export_kind,revoked_at,version,
                    lifecycle_state,created_at,updated_at,deleted_at
                ) VALUES (?,?,?,?,1,?,'active',?,?,?,NULL,1,'active',?,?,NULL)
                """,
                (
                    grant_id,
                    identity.scope_id,
                    source_set_id,
                    str(lineage["id"]),
                    expires_at,
                    identity.principal_id,
                    identity.membership_id,
                    export_kind,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO idempotency_records "
                "(id,scope_id,idempotency_key,payload_hash,result_hash,expires_at,"
                "result_object_manifest_id,status,created_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?,?,?,NULL,'settled',?,'cloud',?)",
                (
                    repository._record_id("idem", operation_id, command_type),  # noqa: SLF001
                    identity.scope_id,
                    idempotency_key,
                    payload_hash,
                    sha256_text(grant_id),
                    expires_at,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            command_id = repository._record_id(  # noqa: SLF001
                "cmd", operation_id, command_type
            )
            connection.execute(
                "INSERT INTO commands "
                "(id,scope_id,operation_id,idempotency_key,aggregate_type,aggregate_id,"
                "command_type,actor_principal_id,expected_aggregate_version,"
                "device_command_sequence,status,actor_membership_id,payload_object_manifest_id,"
                "payload_hash,submitted_at,settled_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?, 'export_grant',?,?,?,NULL,NULL,'settled',?,NULL,?,?,?,'cloud',?)",
                (
                    command_id,
                    identity.scope_id,
                    operation_id,
                    idempotency_key,
                    grant_id,
                    command_type,
                    identity.principal_id,
                    identity.membership_id,
                    payload_hash,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            event_hash = sha256_text(
                f"{grant_id}|{report_id}|{current['current_version']}|{export_kind}"
            )
            publish_record_id = repository._record_id(  # noqa: SLF001
                "publish", operation_id, grant_id
            )
            connection.execute(
                "INSERT INTO publish_records "
                "(id,scope_id,external_side_effect_id,artifact_hash,target,status,"
                "artifact_kind,release_version,published_at,revoked_at,version,"
                "lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES (?,?,NULL,?,?,'authorized',?,?,?,NULL,1,'active',?,?,NULL)",
                (
                    publish_record_id,
                    identity.scope_id,
                    event_hash,
                    f"member_export:{identity.membership_id}",
                    f"report_{export_kind}",
                    str(current["current_version"] or 1),
                    now,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO outbox_events "
                "(id,scope_id,operation_id,aggregate_version,event_type,status,aggregate_type,"
                "aggregate_id,event_object_manifest_id,event_hash,available_at,published_at,"
                "authority_role,origin_instance_id) "
                "VALUES (?,?,?,1,'report.export_grant.issued','published','export_grant',?,"
                "NULL,?,?,?,'cloud',?)",
                (
                    repository._record_id("evt", operation_id, command_type),  # noqa: SLF001
                    identity.scope_id,
                    operation_id,
                    grant_id,
                    event_hash,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events "
                "(id,scope_id,operation_id,actor_id,action,event_hash,actor_membership_id,"
                "target_resource_id,details_object_manifest_id,occurred_at,origin_instance_id,"
                "created_at,integrity_hash,authority_role) "
                "VALUES (?,?,?,?, 'report.export_grant.issue',?,?,?,NULL,?,?,?,?, 'cloud')",
                (
                    repository._record_id("audit", operation_id, command_type),  # noqa: SLF001
                    identity.scope_id,
                    operation_id,
                    identity.principal_id,
                    event_hash,
                    identity.membership_id,
                    report_id,
                    now,
                    repository.cloud_instance_id,
                    now,
                    event_hash,
                ),
            )
            row = _export_grant_row(connection, identity, grant_id)
            if row is None:
                raise RepositoryError(
                    500, "report_export_grant_lost", "导出授权创建后无法读取"
                )
            result = _export_grant_payload(row)
            result["idempotentReplay"] = False
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
