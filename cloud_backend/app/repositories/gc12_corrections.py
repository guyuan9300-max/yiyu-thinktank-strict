from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from strict_common.ids import canonical_json, sha256_text, utc_now


def _expires_at() -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(days=30))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _propagate_project_knowledge_consumers(
    repository: Any,
    identity: Any,
    *,
    project_id: str,
    fact_id: str,
    fact_version: int,
    operation_id: str,
    source_event_type: str,
) -> dict[str, Any]:
    """Invalidate stale derived reads without rolling back the formal fact."""

    now = utc_now()
    reconciliation_id = repository._record_id(  # noqa: SLF001
        "recon", operation_id, "project-knowledge-consumers"
    )
    manifest_id = repository._record_id(  # noqa: SLF001
        "manifest", reconciliation_id, "result"
    )
    direct_consumers = [
        "project_knowledge_context",
        "workbench_next_answer",
        "task_project_background",
    ]
    pending_consumers = ["strategic_client_profile", "project_reports"]
    try:
        with repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT r.status, m.receipt FROM reconciliation_runs AS r "
                "LEFT JOIN object_manifests AS m ON m.id=r.result_object_manifest_id "
                "WHERE r.id=? AND r.scope_id=? AND r.lifecycle_state='active'",
                (reconciliation_id, identity.scope_id),
            ).fetchone()
            if existing is not None and str(existing["status"] or "") == "completed":
                import json

                receipt = json.loads(str(existing["receipt"] or "{}"))
                profile_event = connection.execute(
                    "SELECT status FROM outbox_events WHERE scope_id=? AND operation_id=? "
                    "AND event_type='gc13.project_knowledge.strategic_profile_requested'",
                    (identity.scope_id, operation_id),
                ).fetchone()
                profile_already_ready = (
                    profile_event is not None
                    and str(profile_event["status"] or "") == "published"
                )
                connection.commit()
                strategic_profile = (
                    {"state": "completed", "profileId": None, "version": None, "updatedAt": None}
                    if profile_already_ready
                    else _rebuild_strategic_profile_consumer(
                        repository,
                        identity,
                        project_id=project_id,
                        operation_id=operation_id,
                    )
                )
                return {
                    "state": "completed",
                    "retryable": False,
                    "message": "项目知识已保存，客户档案与项目报告正在整理",
                    "directConsumers": direct_consumers,
                    "pendingConsumers": (
                        ["project_reports"]
                        if profile_already_ready
                        else pending_consumers
                    ),
                    "invalidatedAiContextCount": int(
                        receipt.get("invalidatedAiContextCount") or 0
                    ),
                    "invalidatedLineageCount": int(
                        receipt.get("invalidatedLineageCount") or 0
                    ),
                    "invalidatedCacheCount": int(
                        receipt.get("invalidatedCacheCount") or 0
                    ),
                    "strategicProfile": strategic_profile,
                    "idempotentReplay": True,
                }

            project_context_ids = [
                str(row["ai_context_manifest_id"])
                for row in connection.execute(
                    "SELECT DISTINCT ai_context_manifest_id FROM ai_answers "
                    "WHERE scope_id=? AND client_id=? AND lifecycle_state='active' "
                    "AND ai_context_manifest_id IS NOT NULL",
                    (identity.scope_id, project_id),
                ).fetchall()
            ]
            context_count = 0
            if project_context_ids:
                placeholders = ",".join("?" for _ in project_context_ids)
                cursor = connection.execute(
                    f"UPDATE ai_context_manifests SET status='invalidated', invalidated_at=? "
                    f"WHERE scope_id=? AND id IN ({placeholders}) AND invalidated_at IS NULL",
                    (now, identity.scope_id, *project_context_ids),
                )
                context_count = int(cursor.rowcount or 0)

            project_narrative_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM narrative_outputs WHERE scope_id=? AND client_id=? "
                    "AND lifecycle_state='active'",
                    (identity.scope_id, project_id),
                ).fetchall()
            ]
            derivative_ids = project_context_ids + project_narrative_ids
            lineage_ids: list[str] = []
            if derivative_ids:
                placeholders = ",".join("?" for _ in derivative_ids)
                lineage_ids = [
                    str(row["id"])
                    for row in connection.execute(
                        f"SELECT id FROM derivation_lineage WHERE scope_id=? "
                        f"AND derivative_object_id IN ({placeholders}) "
                        "AND invalidated_at IS NULL",
                        (identity.scope_id, *derivative_ids),
                    ).fetchall()
                ]
            lineage_count = 0
            cache_count = 0
            if lineage_ids:
                placeholders = ",".join("?" for _ in lineage_ids)
                lineage_count = int(
                    connection.execute(
                        f"UPDATE derivation_lineage SET invalidated_at=? WHERE scope_id=? "
                        f"AND id IN ({placeholders}) AND invalidated_at IS NULL",
                        (now, identity.scope_id, *lineage_ids),
                    ).rowcount
                    or 0
                )
                cache_count = int(
                    connection.execute(
                        f"UPDATE cache_entries SET invalidated_at=? WHERE scope_id=? "
                        f"AND lineage_id IN ({placeholders}) AND invalidated_at IS NULL",
                        (now, identity.scope_id, *lineage_ids),
                    ).rowcount
                    or 0
                )

            receipt_value = {
                "schema": "yiyu.project-knowledge-consumer-propagation.v1",
                "projectId": project_id,
                "factId": fact_id,
                "factVersion": fact_version,
                "state": "completed",
                "directConsumers": direct_consumers,
                "pendingConsumers": pending_consumers,
                "invalidatedAiContextCount": context_count,
                "invalidatedLineageCount": lineage_count,
                "invalidatedCacheCount": cache_count,
                "propagatedAt": now,
            }
            receipt = canonical_json(receipt_value)
            receipt_hash = sha256_text(receipt)
            connection.execute(
                """
                INSERT INTO object_manifests (
                    id, scope_id, storage_key, content_hash, lifecycle_state,
                    receipt, holder_role, holder_instance_id, storage_kind,
                    byte_size, media_type, availability_state, receipt_hash,
                    created_at, verified_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud_consumer_propagation', ?,
                          'metadata_receipt', ?,
                          'application/vnd.yiyu.project-knowledge-propagation+json',
                          'ready', ?, ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    content_hash=excluded.content_hash, receipt=excluded.receipt,
                    byte_size=excluded.byte_size, availability_state='ready',
                    receipt_hash=excluded.receipt_hash, verified_at=excluded.verified_at
                """,
                (
                    manifest_id,
                    identity.scope_id,
                    receipt_hash,
                    receipt,
                    repository.cloud_instance_id,
                    len(receipt.encode("utf-8")),
                    receipt_hash,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO reconciliation_runs (
                    id, scope_id, operation_id, registry_state_id,
                    mismatch_count, status, reconciliation_kind,
                    target_instance_id, result_object_manifest_id,
                    started_at, completed_at, version, lifecycle_state,
                    created_at, updated_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, ?, NULL, 0, 'completed',
                          'project_knowledge_consumer_invalidation_v1', ?, ?, ?, ?, ?,
                          'active', ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    mismatch_count=0, status='completed',
                    result_object_manifest_id=excluded.result_object_manifest_id,
                    completed_at=excluded.completed_at, version=excluded.version,
                    lifecycle_state='active', updated_at=excluded.updated_at,
                    deleted_at=NULL
                """,
                (
                    reconciliation_id,
                    identity.scope_id,
                    operation_id,
                    repository.cloud_instance_id,
                    manifest_id,
                    now,
                    now,
                    fact_version,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                "UPDATE outbox_events SET status='published', published_at=? "
                "WHERE scope_id=? AND operation_id=? AND event_type=?",
                (now, identity.scope_id, operation_id, source_event_type),
            )
            for event_type, status, published_at in (
                ("gc13.project_knowledge.consumers_invalidated", "published", now),
                ("gc13.project_knowledge.strategic_profile_requested", "pending", None),
                ("gc13.project_knowledge.project_reports_requested", "pending", None),
            ):
                event_id = repository._record_id(  # noqa: SLF001
                    "evt", operation_id, event_type
                )
                event_hash = sha256_text(
                    f"{operation_id}|{event_type}|{project_id}|{fact_id}|{fact_version}"
                )
                connection.execute(
                    """
                    INSERT INTO outbox_events (
                        id, scope_id, operation_id, aggregate_version, event_type,
                        status, aggregate_type, aggregate_id,
                        event_object_manifest_id, event_hash, available_at,
                        published_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, ?, 'client', ?, ?, ?, ?, ?, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status=excluded.status, event_object_manifest_id=excluded.event_object_manifest_id,
                        event_hash=excluded.event_hash, available_at=excluded.available_at,
                        published_at=excluded.published_at
                    """,
                    (
                        event_id,
                        identity.scope_id,
                        operation_id,
                        fact_version,
                        event_type,
                        status,
                        project_id,
                        manifest_id,
                        event_hash,
                        now,
                        published_at,
                        repository.cloud_instance_id,
                    ),
                )
            connection.commit()
            strategic_profile = _rebuild_strategic_profile_consumer(
                repository,
                identity,
                project_id=project_id,
                operation_id=operation_id,
            )
            return {
                "state": "completed",
                "retryable": False,
                "message": "项目知识已保存，客户档案与项目报告正在整理",
                "directConsumers": direct_consumers,
                "pendingConsumers": (
                    ["project_reports"]
                    if strategic_profile.get("state") == "completed"
                    else pending_consumers
                ),
                "invalidatedAiContextCount": context_count,
                "invalidatedLineageCount": lineage_count,
                "invalidatedCacheCount": cache_count,
                "strategicProfile": strategic_profile,
                "idempotentReplay": False,
            }
    except Exception:
        try:
            with repository._connection() as connection:  # noqa: SLF001
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO reconciliation_runs (
                        id, scope_id, operation_id, registry_state_id,
                        mismatch_count, status, reconciliation_kind,
                        target_instance_id, result_object_manifest_id,
                        started_at, completed_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, NULL, 1, 'failed_retryable',
                              'project_knowledge_consumer_invalidation_v1', ?, NULL,
                              ?, ?, ?, 'active', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        mismatch_count=1, status='failed_retryable',
                        completed_at=excluded.completed_at, version=excluded.version,
                        lifecycle_state='active', updated_at=excluded.updated_at,
                        deleted_at=NULL
                    """,
                    (
                        reconciliation_id,
                        identity.scope_id,
                        operation_id,
                        repository.cloud_instance_id,
                        now,
                        now,
                        fact_version,
                        now,
                        now,
                        repository.cloud_instance_id,
                    ),
                )
                connection.commit()
        except Exception:
            pass
        return {
            "state": "failed_retryable",
            "retryable": True,
            "message": "项目知识已保存，相关页面更新失败，可以重试",
            "directConsumers": direct_consumers,
            "pendingConsumers": pending_consumers,
            "invalidatedAiContextCount": 0,
            "invalidatedLineageCount": 0,
            "invalidatedCacheCount": 0,
            "idempotentReplay": False,
        }


def _rebuild_strategic_profile_consumer(
    repository: Any,
    identity: Any,
    *,
    project_id: str,
    operation_id: str,
) -> dict[str, Any]:
    del repository, identity, project_id, operation_id
    # The organization cloud never receives member file bodies.  It can only
    # mark the consumer pending; the device holding the local Wiki performs
    # the extraction and publishes the safe profile version afterward.
    return {
        "state": "pending_local_wiki",
        "profileId": None,
        "version": None,
        "updatedAt": None,
    }


def list_strategic_profile_clarifications(
    repository: Any,
    identity: Any,
    *,
    project_id: str,
) -> dict[str, Any]:
    """List member-confirmed facts submitted from the client-profile UI."""

    from ..repository import RepositoryError

    with repository._connection() as connection:  # noqa: SLF001
        project = repository._require_project_access(  # noqa: SLF001
            connection,
            identity,
            project_id=project_id,
        )
        rows = connection.execute(
            """
            SELECT fact.id, fact.version, fact.updated_at, manifest.receipt,
                   principal.display_name
            FROM atomic_facts AS fact
            JOIN source_sets AS sources
              ON sources.id=fact.source_set_id AND sources.scope_id=fact.scope_id
             AND sources.client_id=?
             AND sources.purpose_kind='strategic_profile_clarification'
             AND sources.publication_state='published'
             AND sources.lifecycle_state='active'
            JOIN object_manifests AS manifest
              ON manifest.id=fact.fact_object_manifest_id
             AND manifest.scope_id=fact.scope_id
             AND manifest.lifecycle_state='active'
            LEFT JOIN organization_memberships AS membership
              ON membership.id=fact.confirmed_by_membership_id
             AND membership.scope_id=fact.scope_id
            LEFT JOIN principals AS principal
              ON principal.id=membership.principal_id
            WHERE fact.scope_id=? AND fact.verification_state='verified'
              AND fact.lifecycle_state='active'
            ORDER BY fact.updated_at DESC, fact.id
            """,
            (project_id, identity.scope_id),
        ).fetchall()
        current = connection.execute(
            "SELECT current_version FROM narrative_outputs "
            "WHERE scope_id=? AND client_id=? AND artifact_kind='strategic_profile' "
            "AND lifecycle_state='active' ORDER BY updated_at DESC LIMIT 1",
            (identity.scope_id, project_id),
        ).fetchone()
    current_rev = int(current["current_version"] or 0) if current else 0
    clarifications: list[dict[str, Any]] = []
    import json

    for row in rows:
        try:
            receipt = json.loads(str(row["receipt"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(receipt, Mapping):
            continue
        clarifications.append(
            {
                "id": str(row["id"]),
                "clientId": project_id,
                "basedOnRev": int(receipt.get("basedOnRev") or 0),
                "dimension": str(receipt.get("dimension") or "essence"),
                "question": str(receipt.get("question") or ""),
                "askedBy": str(receipt.get("confirmedByMembershipId") or ""),
                "answer": str(receipt.get("statement") or ""),
                "answeredByUserId": str(receipt.get("confirmedByMembershipId") or "") or None,
                "answeredByDisplayName": str(row["display_name"] or ""),
                "answeredAt": str(receipt.get("createdAt") or row["updated_at"] or ""),
                "resultedInRev": current_rev or None,
                "status": "applied",
                "version": int(row["version"] or 1),
            }
        )
    return {"clarifications": clarifications}


def create_strategic_profile_clarification(
    repository: Any,
    identity: Any,
    *,
    project_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Promote the profile's own clarification input to formal project knowledge."""

    from ..repository import RepositoryError

    dimension = str(payload.get("dimension") or "").strip()
    statement = str(payload.get("answer") or "").strip()
    question = str(payload.get("question") or "").strip()
    based_on_rev = int(payload.get("basedOnRev") or 0)
    if dimension not in {"essence", "business_intro", "cooperation", "people", "timeline", "next_steps"}:
        raise RepositoryError(422, "strategic_profile_dimension_invalid", "客户档案栏目无效")
    if not statement or len(statement) > 4_000:
        raise RepositoryError(422, "strategic_profile_clarification_invalid", "补充内容不能为空且不能超过4000字")

    statement_hash = sha256_text(statement)
    command_type = "gc12.strategic_profile.clarified"
    fact_id = "fact_" + sha256_text(
        f"strategic-profile-clarification\x1f{identity.scope_id}\x1f{project_id}\x1f"
        f"{dimension}\x1f{statement_hash}"
    )[:30]
    source_set_id = repository._record_id("source_set", fact_id, "profile-clarification")  # noqa: SLF001
    normalized = {
        "projectId": project_id,
        "dimension": dimension,
        "question": question,
        "statement": statement,
        "statementHash": statement_hash,
        "basedOnRev": based_on_rev,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    operation_id = repository._operation_id(identity.scope_id, command_type, idempotency_key)  # noqa: SLF001

    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            project = repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=project_id,
                capability="knowledge_write",
            )
            narrative = connection.execute(
                "SELECT id, current_version FROM narrative_outputs "
                "WHERE scope_id=? AND client_id=? AND artifact_kind='strategic_profile' "
                "AND lifecycle_state='active' ORDER BY updated_at DESC LIMIT 1",
                (identity.scope_id, project_id),
            ).fetchone()
            if narrative is None:
                raise RepositoryError(409, "strategic_profile_missing", "请先生成客户档案再补充")
            if based_on_rev <= 0:
                based_on_rev = int(narrative["current_version"] or 1)
                normalized["basedOnRev"] = based_on_rev
                payload_hash = sha256_text(canonical_json(normalized))
            replay = repository._existing_command(  # noqa: SLF001
                connection,
                scope_id=identity.scope_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                payload_hash=payload_hash,
            )
            if replay is not None:
                row = connection.execute(
                    "SELECT version, updated_at FROM atomic_facts "
                    "WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                    (fact_id, identity.scope_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(503, "strategic_profile_clarification_replay_incomplete", "补充回执尚未完整落地，可以重试")
                connection.commit()
                result = {
                    "id": fact_id,
                    "clientId": project_id,
                    "basedOnRev": based_on_rev,
                    "dimension": dimension,
                    "question": question,
                    "askedBy": identity.membership_id,
                    "answer": statement,
                    "answeredByUserId": identity.membership_id,
                    "answeredByDisplayName": "",
                    "answeredAt": str(row["updated_at"] or ""),
                    "resultedInRev": None,
                    "status": "applied",
                    "version": int(row["version"] or 1),
                    "idempotentReplay": True,
                }
                result["consumerPropagation"] = _propagate_project_knowledge_consumers(
                    repository,
                    identity,
                    project_id=project_id,
                    fact_id=fact_id,
                    fact_version=int(row["version"] or 1),
                    operation_id=operation_id,
                    source_event_type=command_type,
                )
                return result

            existing_fact = connection.execute(
                "SELECT version, updated_at FROM atomic_facts "
                "WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                (fact_id, identity.scope_id),
            ).fetchone()
            if existing_fact is not None:
                original = connection.execute(
                    "SELECT operation_id FROM commands WHERE scope_id=? "
                    "AND aggregate_type='atomic_fact' AND aggregate_id=? "
                    "AND command_type=? ORDER BY submitted_at LIMIT 1",
                    (identity.scope_id, fact_id, command_type),
                ).fetchone()
                connection.commit()
                original_operation_id = (
                    str(original["operation_id"] or "") if original is not None else operation_id
                )
                result = {
                    "id": fact_id,
                    "clientId": project_id,
                    "basedOnRev": based_on_rev,
                    "dimension": dimension,
                    "question": question,
                    "askedBy": identity.membership_id,
                    "answer": statement,
                    "answeredByUserId": identity.membership_id,
                    "answeredByDisplayName": "",
                    "answeredAt": str(existing_fact["updated_at"] or ""),
                    "resultedInRev": None,
                    "status": "applied",
                    "version": int(existing_fact["version"] or 1),
                    "idempotentReplay": True,
                }
                result["consumerPropagation"] = _propagate_project_knowledge_consumers(
                    repository,
                    identity,
                    project_id=project_id,
                    fact_id=fact_id,
                    fact_version=int(existing_fact["version"] or 1),
                    operation_id=original_operation_id,
                    source_event_type=command_type,
                )
                return result

            now = utc_now()
            manifest_id = repository._record_id("manifest", fact_id, "version-1")  # noqa: SLF001
            receipt = canonical_json(
                {
                    "schema": "yiyu.strategic-profile-clarification.v1",
                    "clientId": project_id,
                    "factId": fact_id,
                    "factVersion": 1,
                    "dimension": dimension,
                    "question": question,
                    "statement": statement,
                    "statementHash": statement_hash,
                    "basedOnRev": based_on_rev,
                    "verificationState": "verified_member_clarification",
                    "confirmedByMembershipId": identity.membership_id,
                    "createdAt": now,
                }
            )
            receipt_hash = sha256_text(receipt)
            connection.execute(
                """INSERT INTO object_manifests (
                    id, scope_id, storage_key, content_hash, lifecycle_state, receipt,
                    holder_role, holder_instance_id, storage_kind, byte_size, media_type,
                    availability_state, receipt_hash, created_at, verified_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud_formal_fact', ?,
                          'metadata_receipt', ?, 'application/vnd.yiyu.strategic-profile-clarification+json',
                          'ready', ?, ?, ?, NULL, 'cloud', ?)""",
                (manifest_id, identity.scope_id, statement_hash, receipt,
                 repository.cloud_instance_id, len(receipt.encode("utf-8")), receipt_hash,
                 now, now, repository.cloud_instance_id),
            )
            connection.execute(
                """INSERT INTO source_sets (
                    id, scope_id, client_id, security_label_set_version, source_count,
                    version, purpose_kind, publication_state, created_by_principal_id,
                    created_at, expires_at, lifecycle_state, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, ?, 'organization-v1', 1, 1,
                          'strategic_profile_clarification', 'published', ?, ?, NULL,
                          'active', ?, NULL, 'cloud', ?)""",
                (source_set_id, identity.scope_id, project_id, identity.principal_id,
                 now, now, repository.cloud_instance_id),
            )
            source_member_id = repository._record_id("source_member", source_set_id, str(narrative["id"]))  # noqa: SLF001
            connection.execute(
                """INSERT INTO source_set_members (
                    id, scope_id, source_set_id, source_object_id, source_version,
                    policy_version, source_object_kind, ordinal, added_at, removed_at,
                    version, lifecycle_state, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, 1, 'narrative_output', 0, ?, NULL, 1,
                          'active', ?, ?, NULL, 'cloud', ?)""",
                (source_member_id, identity.scope_id, source_set_id, str(narrative["id"]),
                 int(narrative["current_version"] or 1), now, now, now,
                 repository.cloud_instance_id),
            )
            connection.execute(
                """INSERT INTO secured_resources (
                    id, scope_id, resource_kind, lifecycle_state, version,
                    resource_type_key, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, 'atomic_fact', 'active', 1,
                          'verified_profile_clarification', ?, ?, NULL, 'cloud', ?)""",
                (fact_id, identity.scope_id, now, now, repository.cloud_instance_id),
            )
            connection.execute(
                """INSERT INTO atomic_facts (
                    id, scope_id, chunk_id, fact_hash, confidence, version, source_set_id,
                    fact_object_manifest_id, verification_state, confirmed_by_membership_id,
                    confirmed_at, lifecycle_state, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, NULL, ?, NULL, 1, ?, ?, 'verified', ?, ?, 'active',
                          ?, ?, NULL, 'cloud', ?)""",
                (fact_id, identity.scope_id, statement_hash, source_set_id, manifest_id,
                 identity.membership_id, now, now, now, repository.cloud_instance_id),
            )
            locator = canonical_json(
                {"schema": "yiyu.strategic-profile-dimension.v1", "dimension": dimension,
                 "basedOnRev": based_on_rev}
            )
            connection.execute(
                """INSERT INTO evidence_links (
                    id, scope_id, fact_id, source_object_id, source_version, locator,
                    source_object_kind, locator_kind, page_no, paragraph_no, locator_hash,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'narrative_output', 'profile_dimension',
                          NULL, NULL, ?, ?)""",
                (repository._record_id("evidence", fact_id, "version-1"),  # noqa: SLF001
                 identity.scope_id, fact_id, str(narrative["id"]),
                 int(narrative["current_version"] or 1), locator, sha256_text(locator), now),
            )
            result = {
                "id": fact_id,
                "clientId": project_id,
                "basedOnRev": based_on_rev,
                "dimension": dimension,
                "question": question,
                "askedBy": identity.membership_id,
                "answer": statement,
                "answeredByUserId": identity.membership_id,
                "answeredByDisplayName": "",
                "answeredAt": now,
                "resultedInRev": None,
                "status": "applied",
                "version": 1,
                "idempotentReplay": False,
            }
            result_hash = sha256_text(canonical_json(result))
            connection.execute(
                """INSERT INTO idempotency_records (
                    id, scope_id, idempotency_key, payload_hash, result_hash, expires_at,
                    result_object_manifest_id, status, created_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'settled', ?, 'cloud', ?)""",
                (repository._record_id("idem", operation_id, command_type), identity.scope_id,  # noqa: SLF001
                 idempotency_key, payload_hash, result_hash, _expires_at(), manifest_id,
                 now, repository.cloud_instance_id),
            )
            connection.execute(
                """INSERT INTO commands (
                    id, scope_id, operation_id, idempotency_key, aggregate_type,
                    aggregate_id, command_type, actor_principal_id,
                    expected_aggregate_version, device_command_sequence, status,
                    actor_membership_id, payload_object_manifest_id, payload_hash,
                    submitted_at, settled_at, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, 'atomic_fact', ?, ?, ?, 0, NULL, 'settled', ?, ?,
                          ?, ?, ?, 'cloud', ?)""",
                (repository._record_id("cmd", operation_id, command_type), identity.scope_id,  # noqa: SLF001
                 operation_id, idempotency_key, fact_id, command_type, identity.principal_id,
                 identity.membership_id, manifest_id, payload_hash, now, now,
                 repository.cloud_instance_id),
            )
            event_hash = sha256_text(f"{operation_id}|{fact_id}|1|{statement_hash}")
            connection.execute(
                """INSERT INTO outbox_events (
                    id, scope_id, operation_id, aggregate_version, event_type, status,
                    aggregate_type, aggregate_id, event_object_manifest_id, event_hash,
                    available_at, published_at, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, 1, ?, 'pending', 'atomic_fact', ?, ?, ?, ?, NULL,
                          'cloud', ?)""",
                (repository._record_id("evt", operation_id, command_type), identity.scope_id,  # noqa: SLF001
                 operation_id, command_type, fact_id, manifest_id, event_hash, now,
                 repository.cloud_instance_id),
            )
            audit_id = repository._record_id("audit", operation_id, "profile-clarified")  # noqa: SLF001
            connection.execute(
                """INSERT INTO audit_events (
                    id, scope_id, operation_id, actor_id, action, event_hash,
                    actor_membership_id, target_resource_id, details_object_manifest_id,
                    occurred_at, origin_instance_id, created_at, integrity_hash,
                    authority_role
                ) VALUES (?, ?, ?, ?, 'strategic_profile.clarified', ?, ?, ?, ?, ?, ?, ?, ?,
                          'cloud')""",
                (audit_id, identity.scope_id, operation_id, identity.principal_id, event_hash,
                 identity.membership_id, fact_id, manifest_id, now,
                 repository.cloud_instance_id, now,
                 sha256_text(f"{audit_id}|{event_hash}|{now}|{repository.cloud_instance_id}")),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    result["consumerPropagation"] = _propagate_project_knowledge_consumers(
        repository,
        identity,
        project_id=project_id,
        fact_id=fact_id,
        fact_version=1,
        operation_id=operation_id,
        source_event_type=command_type,
    )
    return result


def correct_answer_fact(
    repository: Any,
    identity: Any,
    *,
    answer_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Write explicit project knowledge without an approval side path."""

    from ..repository import RepositoryError

    project_id = str(payload.get("projectId") or "").strip()
    correction_kind = str(payload.get("correctionKind") or "").strip().lower()
    selected_text_hash = str(payload.get("selectedTextHash") or "").strip().lower()
    statement = str(payload.get("statement") or "").strip()
    statement_hash = str(payload.get("statementHash") or "").strip().lower()
    origin_instance_id = str(payload.get("originInstanceId") or "").strip()
    expected_raw = payload.get("expectedVersion")
    expected_version = int(expected_raw) if expected_raw is not None else 0
    if not project_id or correction_kind not in {"correction", "supplement", "remember"}:
        raise RepositoryError(422, "answer_fact_correction_invalid", "纠错信息不完整")
    statement_limit = 20_000 if correction_kind == "remember" else 4_000
    if not statement or len(statement) > statement_limit:
        raise RepositoryError(
            422,
            "answer_fact_statement_invalid",
            f"项目知识不能为空且不能超过 {statement_limit} 字",
        )
    if statement_hash != sha256_text(statement):
        raise RepositoryError(422, "answer_fact_statement_hash_invalid", "纠错内容校验失败")
    for value in (selected_text_hash, statement_hash):
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise RepositoryError(422, "answer_fact_hash_invalid", "纠错来源哈希无效")
    if not origin_instance_id:
        raise RepositoryError(422, "origin_instance_required", "缺少来源设备标识")

    action_key = "answer-remember" if correction_kind == "remember" else "answer-correction"
    source_purpose = "answer_remember" if correction_kind == "remember" else "answer_correction"
    resource_type = (
        "verified_project_memory"
        if correction_kind == "remember"
        else "verified_member_correction"
    )
    command_type = (
        "gc12.answer_fact.remembered"
        if correction_kind == "remember"
        else "gc12.answer_fact.corrected"
    )
    fact_id = "fact_" + sha256_text(
        f"{action_key}\x1f{identity.scope_id}\x1f{project_id}\x1f"
        f"{answer_id}\x1f{selected_text_hash}"
    )[:30]
    source_set_id = repository._record_id(  # noqa: SLF001
        "source_set",
        fact_id,
        action_key,
    )
    normalized = {
        "answerId": answer_id,
        "projectId": project_id,
        "factId": fact_id,
        "correctionKind": correction_kind,
        "selectedTextHash": selected_text_hash,
        "statement": statement,
        "statementHash": statement_hash,
        "expectedVersion": expected_version,
        "originInstanceId": origin_instance_id,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id,
        command_type,
        idempotency_key,
    )

    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=project_id,
                capability="knowledge_write",
            )
            replay = repository._existing_command(  # noqa: SLF001
                connection,
                scope_id=identity.scope_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                payload_hash=payload_hash,
            )
            if replay is not None:
                row = connection.execute(
                    "SELECT version, fact_object_manifest_id, updated_at "
                    "FROM atomic_facts WHERE id=? AND scope_id=? "
                    "AND lifecycle_state='active'",
                    (fact_id, identity.scope_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(
                        503,
                        "answer_fact_replay_incomplete",
                        "项目知识回执尚未完整落地，可以重试",
                    )
                connection.commit()
                result = {
                    "clientId": project_id,
                    "answerId": answer_id,
                    "factId": fact_id,
                    "sourceSetId": source_set_id,
                    "factObjectManifestId": str(row["fact_object_manifest_id"] or ""),
                    "correctionKind": correction_kind,
                    "version": int(row["version"] or 1),
                    "verificationState": "verified",
                    "cloudState": "ready",
                    "contextInvalidated": True,
                    "updatedAt": row["updated_at"],
                    "idempotentReplay": True,
                }
                result["consumerPropagation"] = _propagate_project_knowledge_consumers(
                    repository,
                    identity,
                    project_id=project_id,
                    fact_id=fact_id,
                    fact_version=int(row["version"] or 1),
                    operation_id=operation_id,
                    source_event_type=command_type,
                )
                return result

            answer = connection.execute(
                "SELECT id, version, ai_context_manifest_id FROM ai_answers "
                "WHERE id=? AND scope_id=? AND client_id=? "
                "AND lifecycle_state='active'",
                (answer_id, identity.scope_id, project_id),
            ).fetchone()
            if answer is None:
                raise RepositoryError(
                    404,
                    "answer_project_mismatch",
                    "当前项目没有该工作台回答",
                )
            current = connection.execute(
                "SELECT version, created_at FROM atomic_facts "
                "WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                (fact_id, identity.scope_id),
            ).fetchone()
            current_version = int(current["version"] or 0) if current else 0
            if current_version != expected_version:
                raise RepositoryError(
                    409,
                    "answer_fact_version_conflict",
                    "该事实已被更新，请刷新后再纠正",
                )
            next_version = current_version + 1
            now = utc_now()
            created_at = str(current["created_at"] or now) if current else now
            manifest_id = repository._record_id(  # noqa: SLF001
                "manifest",
                fact_id,
                f"version-{next_version}",
            )
            receipt = canonical_json(
                {
                    "schema": "yiyu.project-answer-knowledge.v1",
                    "clientId": project_id,
                    "answerId": answer_id,
                    "factId": fact_id,
                    "factVersion": next_version,
                    "correctionKind": correction_kind,
                    "selectedTextHash": selected_text_hash,
                    "statement": statement,
                    "statementHash": statement_hash,
                    "verificationState": resource_type,
                    "confirmedByMembershipId": identity.membership_id,
                    "createdAt": now,
                }
            )
            receipt_hash = sha256_text(receipt)
            connection.execute(
                """
                INSERT INTO object_manifests (
                    id, scope_id, storage_key, content_hash, lifecycle_state,
                    receipt, holder_role, holder_instance_id, storage_kind,
                    byte_size, media_type, availability_state, receipt_hash,
                    created_at, verified_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud_formal_fact', ?,
                          'metadata_receipt', ?,
                          'application/vnd.yiyu.project-answer-knowledge+json',
                          'ready', ?, ?, ?, NULL, 'cloud', ?)
                """,
                (
                    manifest_id,
                    identity.scope_id,
                    statement_hash,
                    receipt,
                    repository.cloud_instance_id,
                    len(receipt.encode("utf-8")),
                    receipt_hash,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO source_sets (
                    id, scope_id, client_id, security_label_set_version,
                    source_count, version, purpose_kind, publication_state,
                    created_by_principal_id, created_at, expires_at,
                    lifecycle_state, updated_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, ?, 'organization-v1', 1, ?,
                          ?, 'published', ?, ?, NULL,
                          'active', ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    version=excluded.version, publication_state='published',
                    lifecycle_state='active', updated_at=excluded.updated_at,
                    deleted_at=NULL, authority_role='cloud',
                    origin_instance_id=excluded.origin_instance_id
                """,
                (
                    source_set_id,
                    identity.scope_id,
                    project_id,
                    next_version,
                    source_purpose,
                    identity.principal_id,
                    created_at,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            source_member_id = repository._record_id(  # noqa: SLF001
                "source_member",
                source_set_id,
                answer_id,
            )
            connection.execute(
                """
                INSERT INTO source_set_members (
                    id, scope_id, source_set_id, source_object_id,
                    source_version, policy_version, source_object_kind,
                    ordinal, added_at, removed_at, version, lifecycle_state,
                    created_at, updated_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, 1, 'ai_answer', 0, ?, NULL, ?,
                          'active', ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_version=excluded.source_version,
                    removed_at=NULL, version=excluded.version,
                    lifecycle_state='active', updated_at=excluded.updated_at,
                    deleted_at=NULL, authority_role='cloud',
                    origin_instance_id=excluded.origin_instance_id
                """,
                (
                    source_member_id,
                    identity.scope_id,
                    source_set_id,
                    answer_id,
                    int(answer["version"] or 1),
                    now,
                    next_version,
                    created_at,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO secured_resources (
                    id, scope_id, resource_kind, lifecycle_state, version,
                    resource_type_key, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, 'atomic_fact', 'active', ?,
                          ?, ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    lifecycle_state='active', version=excluded.version,
                    resource_type_key=excluded.resource_type_key,
                    updated_at=excluded.updated_at, deleted_at=NULL,
                    authority_role='cloud',
                    origin_instance_id=excluded.origin_instance_id
                """,
                (
                    fact_id,
                    identity.scope_id,
                    next_version,
                    resource_type,
                    created_at,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO atomic_facts (
                    id, scope_id, chunk_id, fact_hash, confidence, version,
                    source_set_id, fact_object_manifest_id,
                    verification_state, confirmed_by_membership_id,
                    confirmed_at, lifecycle_state, created_at, updated_at,
                    deleted_at, authority_role, origin_instance_id
                ) VALUES (?, ?, NULL, ?, NULL, ?, ?, ?, 'verified', ?, ?,
                          'active', ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    fact_hash=excluded.fact_hash, version=excluded.version,
                    source_set_id=excluded.source_set_id,
                    fact_object_manifest_id=excluded.fact_object_manifest_id,
                    verification_state='verified',
                    confirmed_by_membership_id=excluded.confirmed_by_membership_id,
                    confirmed_at=excluded.confirmed_at,
                    lifecycle_state='active', updated_at=excluded.updated_at,
                    deleted_at=NULL, authority_role='cloud',
                    origin_instance_id=excluded.origin_instance_id
                """,
                (
                    fact_id,
                    identity.scope_id,
                    statement_hash,
                    next_version,
                    source_set_id,
                    manifest_id,
                    identity.membership_id,
                    now,
                    created_at,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            locator = canonical_json(
                {
                    "schema": "yiyu.answer-selection-hash.v1",
                    "selectedTextHash": selected_text_hash,
                    "correctionKind": correction_kind,
                    "factVersion": next_version,
                }
            )
            connection.execute(
                """
                INSERT INTO evidence_links (
                    id, scope_id, fact_id, source_object_id, source_version,
                    locator, source_object_kind, locator_kind, page_no,
                    paragraph_no, locator_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'ai_answer',
                          'answer_selection_hash', NULL, NULL, ?, ?)
                """,
                (
                    repository._record_id(  # noqa: SLF001
                        "evidence",
                        fact_id,
                        f"version-{next_version}",
                    ),
                    identity.scope_id,
                    fact_id,
                    answer_id,
                    int(answer["version"] or 1),
                    locator,
                    sha256_text(locator),
                    now,
                ),
            )
            result = {
                "clientId": project_id,
                "answerId": answer_id,
                "factId": fact_id,
                "sourceSetId": source_set_id,
                "factObjectManifestId": manifest_id,
                "correctionKind": correction_kind,
                "version": next_version,
                "verificationState": "verified",
                "cloudState": "ready",
                "contextInvalidated": True,
                "updatedAt": now,
                "idempotentReplay": False,
            }
            result_hash = sha256_text(canonical_json(result))
            connection.execute(
                """
                INSERT INTO idempotency_records (
                    id, scope_id, idempotency_key, payload_hash, result_hash,
                    expires_at, result_object_manifest_id, status, created_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'settled', ?, 'cloud', ?)
                """,
                (
                    repository._record_id("idem", operation_id, command_type),  # noqa: SLF001
                    identity.scope_id,
                    idempotency_key,
                    payload_hash,
                    result_hash,
                    _expires_at(),
                    manifest_id,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO commands (
                    id, scope_id, operation_id, idempotency_key,
                    aggregate_type, aggregate_id, command_type,
                    actor_principal_id, expected_aggregate_version,
                    device_command_sequence, status, actor_membership_id,
                    payload_object_manifest_id, payload_hash, submitted_at,
                    settled_at, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, 'atomic_fact', ?, ?, ?, ?, NULL,
                          'settled', ?, ?, ?, ?, ?, 'cloud', ?)
                """,
                (
                    repository._record_id("cmd", operation_id, command_type),  # noqa: SLF001
                    identity.scope_id,
                    operation_id,
                    idempotency_key,
                    fact_id,
                    command_type,
                    identity.principal_id,
                    expected_version,
                    identity.membership_id,
                    manifest_id,
                    payload_hash,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            event_hash = sha256_text(
                f"{operation_id}|{fact_id}|{next_version}|{statement_hash}"
            )
            connection.execute(
                """
                INSERT INTO outbox_events (
                    id, scope_id, operation_id, aggregate_version, event_type,
                    status, aggregate_type, aggregate_id,
                    event_object_manifest_id, event_hash, available_at,
                    published_at, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, 'pending',
                          'atomic_fact', ?, ?, ?, ?, NULL, 'cloud', ?)
                """,
                (
                    repository._record_id("evt", operation_id, command_type),  # noqa: SLF001
                    identity.scope_id,
                    operation_id,
                    next_version,
                    command_type,
                    fact_id,
                    manifest_id,
                    event_hash,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            audit_id = repository._record_id(  # noqa: SLF001
                "audit",
                operation_id,
                "answer-fact-remembered" if correction_kind == "remember" else "answer-fact-corrected",
            )
            connection.execute(
                """
                INSERT INTO audit_events (
                    id, scope_id, operation_id, actor_id, action, event_hash,
                    actor_membership_id, target_resource_id,
                    details_object_manifest_id, occurred_at,
                    origin_instance_id, created_at, integrity_hash,
                    authority_role
                ) VALUES (?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, 'cloud')
                """,
                (
                    audit_id,
                    identity.scope_id,
                    operation_id,
                    identity.principal_id,
                    (
                        "workbench.answer_fact.remembered"
                        if correction_kind == "remember"
                        else "workbench.answer_fact.corrected"
                    ),
                    event_hash,
                    identity.membership_id,
                    fact_id,
                    manifest_id,
                    now,
                    repository.cloud_instance_id,
                    now,
                    sha256_text(
                        f"{audit_id}|{event_hash}|{now}|{repository.cloud_instance_id}"
                    ),
                ),
            )
            connection.commit()
            result["consumerPropagation"] = _propagate_project_knowledge_consumers(
                repository,
                identity,
                project_id=project_id,
                fact_id=fact_id,
                fact_version=next_version,
                operation_id=operation_id,
                source_event_type=command_type,
            )
            return result
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
