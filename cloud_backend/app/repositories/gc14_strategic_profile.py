from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from strict_common.agent_memory import AgentRunReceipt, builtin_agent_id
from strict_common.ids import canonical_json, sha256_text, utc_now


DIMENSIONS = (
    "essence",
    "business_intro",
    "cooperation",
    "people",
    "timeline",
    "next_steps",
)


def _expires_at() -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(days=30))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _statement_from_receipt(raw: str) -> str:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, Mapping):
        return ""
    for key in (
        "statement",
        "factText",
        "normalizedText",
        "claim",
        "summary",
        "content",
        "text",
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            return value[:4_000]
    return ""


def _dimension_for(statement: str) -> str:
    text = statement.lower()
    rules = (
        (
            "people",
            (
                "秘书长",
                "负责人",
                "创始人",
                "理事长",
                "主任",
                "经理",
                "联系人",
                "任职",
                "现任",
                "老师",
            ),
        ),
        (
            "cooperation",
            ("合作", "共创", "陪伴", "交付", "合同", "益语", "服务关系"),
        ),
        (
            "timeline",
            ("启动", "成立", "开始", "完成", "里程碑", "阶段", "至今"),
        ),
        (
            "business_intro",
            ("项目", "计划", "课程", "服务对象", "活动", "业务", "产品", "品牌"),
        ),
        (
            "next_steps",
            ("下一步", "目标", "建议", "风险", "战略", "应当", "需要"),
        ),
    )
    for dimension, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return dimension
    if re.search(r"(?:19|20)\d{2}[年./-]|\d{1,2}月", statement):
        return "timeline"
    return "essence"


def _normalize_prepared_profile(
    raw: Mapping[str, Any] | None,
    *,
    project_name: str,
    bot_id: str,
    generated_at: str,
    facts: list[dict[str, Any]],
) -> dict[str, Any]:
    from ..repository import RepositoryError

    if not isinstance(raw, Mapping):
        raise RepositoryError(
            409,
            "strategic_profile_local_evidence_required",
            "客户档案必须由持有本机项目资料的设备完成提炼",
        )
    if str(raw.get("schema") or "") != "yiyu.strategic-client-profile.v2":
        raise RepositoryError(422, "strategic_profile_schema_invalid", "客户档案结构版本无效")
    if str(raw.get("generator") or "") != "strategy_companion_local_wiki_v1":
        raise RepositoryError(422, "strategic_profile_generator_invalid", "客户档案生成器无效")

    forbidden_keys = {"path", "originalPath", "storageKey", "excerpt", "content", "fileBody"}

    def reject_private(value: Any) -> None:
        if isinstance(value, Mapping):
            if forbidden_keys.intersection(str(key) for key in value):
                raise RepositoryError(422, "strategic_profile_private_data_rejected", "客户档案不得上传本机正文或路径")
            for child in value.values():
                reject_private(child)
        elif isinstance(value, list):
            for child in value:
                reject_private(child)

    reject_private(raw.get("sourceDocuments") or [])
    raw_dimensions = raw.get("dimensions")
    if not isinstance(raw_dimensions, list):
        raise RepositoryError(422, "strategic_profile_dimensions_invalid", "客户档案栏目无效")
    by_dimension = {
        str(item.get("dimension") or ""): item
        for item in raw_dimensions
        if isinstance(item, Mapping)
    }
    if set(by_dimension) != set(DIMENSIONS):
        raise RepositoryError(422, "strategic_profile_dimensions_invalid", "客户档案必须包含六个标准栏目")
    dimensions: list[dict[str, Any]] = []
    for dimension in DIMENSIONS:
        item = by_dimension[dimension]
        narrative = str(item.get("narrative") or "").strip()
        if len(narrative) > 8_000:
            raise RepositoryError(422, "strategic_profile_narrative_too_long", "客户档案栏目内容过长")
        references = []
        for reference in item.get("references") or []:
            if not isinstance(reference, Mapping):
                continue
            source_type = str(reference.get("sourceType") or "")
            source_id = str(reference.get("sourceId") or "").strip()
            if source_type not in {"local_document", "verified_project_fact", "official_website"} or not source_id:
                continue
            normalized_reference = {
                "sourceType": source_type,
                "sourceId": source_id,
                "label": str(reference.get("label") or "")[:300],
                "confidence": "high",
            }
            if source_type == "official_website":
                source_url = str(reference.get("sourceUrl") or "").strip()
                if source_url.startswith(("https://", "http://")):
                    normalized_reference["sourceUrl"] = source_url
            references.append(normalized_reference)
        dimensions.append(
            {
                "dimension": dimension,
                "narrative": narrative,
                "confidence": "high" if references else "low",
                "confidenceReason": (
                    "由本机项目资料提炼，并以人工确认事实校正"
                    if references
                    else "当前资料不足"
                ),
                "references": references,
                "dataLayerGap": "" if narrative else "当前资料不足，尚未形成可靠结论",
                "openClarifications": [],
            }
        )
    documents: list[dict[str, Any]] = []
    for item in raw.get("sourceDocuments") or []:
        if not isinstance(item, Mapping):
            continue
        source_id = str(item.get("sourceObjectId") or "").strip()
        content_hash = str(item.get("contentHash") or "").strip().lower()
        if not source_id or (content_hash and (len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash))):
            raise RepositoryError(422, "strategic_profile_source_invalid", "客户档案来源回执无效")
        documents.append(
            {
                "sourceObjectId": source_id,
                "sourceObjectKind": "source_asset",
                "sourceVersion": max(1, int(item.get("sourceVersion") or 1)),
                "contentHash": content_hash,
                "knowledgeDocumentId": str(item.get("knowledgeDocumentId") or ""),
                "documentVersionId": str(item.get("documentVersionId") or ""),
                "title": str(item.get("title") or "本机资料")[:300],
            }
        )
    if not documents:
        raise RepositoryError(409, "strategic_profile_local_evidence_required", "客户档案没有本机资料证据")
    source_facts = [
        {"factId": item["id"], "version": item["version"], "factHash": item["fact_hash"]}
        for item in facts
    ]
    raw_coverage = raw.get("coverage") if isinstance(raw.get("coverage"), Mapping) else {}
    eligible_count = max(0, int(raw_coverage.get("eligibleDocumentCount") or len(documents)))
    scanned_count = max(0, int(raw_coverage.get("scannedDocumentCount") or len(documents)))
    cited_count = max(0, int(raw_coverage.get("citedDocumentCount") or 0))
    if scanned_count > eligible_count or cited_count > scanned_count:
        raise RepositoryError(422, "strategic_profile_coverage_invalid", "客户档案资料覆盖回执无效")
    content = {
        "schema": "yiyu.strategic-client-profile.v2",
        "clientName": project_name,
        "generator": "strategy_companion_local_wiki_v1",
        "processingAgentId": bot_id,
        "modelName": str(raw.get("modelName") or "")[:200],
        "dimensions": dimensions,
        "overallConfidence": round(sum(bool(item["narrative"]) for item in dimensions) / len(dimensions), 3),
        "openClarificationsCount": 0,
        "dataLayerGaps": [item["dataLayerGap"] for item in dimensions if item["dataLayerGap"]],
        "contributors": [],
        "sourceDocuments": documents,
        "sourceFacts": source_facts,
        "coverage": {
            "eligibleDocumentCount": eligible_count,
            "scannedDocumentCount": scanned_count,
            "citedDocumentCount": cited_count,
        },
        "generatedAt": generated_at,
    }
    content["inputFingerprint"] = sha256_text(canonical_json(content))
    return content


def rebuild_strategic_profile(
    repository: Any,
    identity: Any,
    *,
    project_id: str,
    idempotency_key: str,
    prepared_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from ..repository import RepositoryError

    command_type = "gc14.strategic_profile.rebuilt"
    bot_id = builtin_agent_id(identity.organization_id, "strategy_companion")
    with repository._connection() as connection:  # noqa: SLF001
        project = repository._require_project_access(  # noqa: SLF001
            connection,
            identity,
            project_id=project_id,
            capability="knowledge_write",
        )
        if connection.execute(
            "SELECT 1 FROM bot_definitions AS bot "
            "JOIN authorization_scopes AS agent_scope ON agent_scope.id=bot.scope_id "
            "WHERE bot.id=? AND agent_scope.organization_id=? "
            "AND bot.agent_kind='strategy_companion' AND bot.enabled=1 "
            "AND bot.lifecycle_state='active' AND agent_scope.status='active' "
            "AND agent_scope.lifecycle_state='active'",
            (bot_id, identity.organization_id),
        ).fetchone() is None:
            raise RepositoryError(503, "strategy_companion_unavailable", "客户档案整理暂未就绪")
        rows = connection.execute(
            """
            SELECT fact.id, fact.version, fact.fact_hash, fact.updated_at,
                   manifest.receipt
            FROM atomic_facts AS fact
            JOIN source_sets AS sources
              ON sources.id=fact.source_set_id AND sources.scope_id=fact.scope_id
             AND sources.client_id=? AND sources.publication_state='published'
             AND sources.lifecycle_state='active'
            JOIN object_manifests AS manifest
              ON manifest.id=fact.fact_object_manifest_id
             AND manifest.scope_id=fact.scope_id
             AND manifest.lifecycle_state='active'
            WHERE fact.scope_id=? AND fact.verification_state='verified'
              AND fact.lifecycle_state='active'
            ORDER BY fact.updated_at, fact.id
            """,
            (project_id, identity.scope_id),
        ).fetchall()
    facts = [
        {
            "id": str(row["id"]),
            "version": int(row["version"] or 1),
            "fact_hash": str(row["fact_hash"] or ""),
            "updated_at": str(row["updated_at"] or ""),
            "statement": _statement_from_receipt(str(row["receipt"] or "")),
        }
        for row in rows
    ]
    facts = [item for item in facts if item["statement"]]
    now = utc_now()
    content = _normalize_prepared_profile(
        prepared_profile,
        project_name=str(project["name"] or ""),
        bot_id=bot_id,
        generated_at=now,
        facts=facts,
    )
    input_fingerprint = str(content["inputFingerprint"])
    normalized = {
        "projectId": project_id,
        "inputFingerprint": input_fingerprint,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id, command_type, idempotency_key
    )
    profile_id = repository._record_id(  # noqa: SLF001
        "narrative", f"{identity.scope_id}:{project_id}", "strategic-profile"
    )

    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = repository._existing_command(  # noqa: SLF001
                connection,
                scope_id=identity.scope_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                payload_hash=payload_hash,
            )
            if replay is not None:
                connection.commit()
                from .workbench_outputs import project_narrative

                result = project_narrative(repository, identity, project_id=project_id)
                result["idempotentReplay"] = True
                return result

            current = connection.execute(
                "SELECT current_version, version, created_at FROM narrative_outputs "
                "WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                (profile_id, identity.scope_id),
            ).fetchone()
            current_content_version = int(current["current_version"] or 0) if current else 0
            current_aggregate_version = int(current["version"] or 0) if current else 0
            next_content_version = current_content_version + 1
            next_aggregate_version = current_aggregate_version + 1
            receipt = canonical_json(content)
            receipt_hash = sha256_text(receipt)
            source_set_id = repository._record_id(  # noqa: SLF001
                "source_set", profile_id, input_fingerprint
            )
            manifest_id = repository._record_id(  # noqa: SLF001
                "manifest", profile_id, f"version-{next_content_version}"
            )
            artifact_version_id = repository._record_id(  # noqa: SLF001
                "artifact_version", profile_id, str(next_content_version)
            )
            lineage_id = repository._record_id(  # noqa: SLF001
                "lineage", profile_id, str(next_content_version)
            )
            connection.execute(
                """
                INSERT INTO source_sets (
                    id, scope_id, client_id, security_label_set_version,
                    source_count, version, purpose_kind, publication_state,
                    created_by_principal_id, created_at, expires_at,
                    lifecycle_state, updated_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, ?, 'organization-v1', ?, 1,
                          'strategic_profile_generation', 'published', ?, ?, NULL,
                          'active', ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_count=excluded.source_count, updated_at=excluded.updated_at,
                    lifecycle_state='active', deleted_at=NULL
                """,
                (
                    source_set_id,
                    identity.scope_id,
                    project_id,
                    len(facts) + len(content["sourceDocuments"]),
                    identity.principal_id,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            for ordinal, fact in enumerate(facts):
                member_id = repository._record_id(  # noqa: SLF001
                    "source_member", source_set_id, str(fact["id"])
                )
                connection.execute(
                    """
                    INSERT INTO source_set_members (
                        id, scope_id, source_set_id, source_object_id,
                        source_version, policy_version, source_object_kind,
                        ordinal, added_at, removed_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 1, 'atomic_fact', ?, ?, NULL, 1,
                              'active', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_version=excluded.source_version, ordinal=excluded.ordinal,
                        removed_at=NULL, lifecycle_state='active',
                        updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (
                        member_id,
                        identity.scope_id,
                        source_set_id,
                        fact["id"],
                        fact["version"],
                        ordinal,
                        now,
                        now,
                        now,
                        repository.cloud_instance_id,
                    ),
                )
            for offset, source in enumerate(content["sourceDocuments"], start=len(facts)):
                member_id = repository._record_id(  # noqa: SLF001
                    "source_member", source_set_id, str(source["sourceObjectId"])
                )
                connection.execute(
                    """
                    INSERT INTO source_set_members (
                        id, scope_id, source_set_id, source_object_id,
                        source_version, policy_version, source_object_kind,
                        ordinal, added_at, removed_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 1, 'source_asset', ?, ?, NULL, 1,
                              'active', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_version=excluded.source_version, ordinal=excluded.ordinal,
                        removed_at=NULL, lifecycle_state='active',
                        updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (
                        member_id,
                        identity.scope_id,
                        source_set_id,
                        source["sourceObjectId"],
                        source["sourceVersion"],
                        offset,
                        now,
                        now,
                        now,
                        repository.cloud_instance_id,
                    ),
                )
            connection.execute(
                """
                INSERT INTO object_manifests (
                    id, scope_id, storage_key, content_hash, lifecycle_state,
                    receipt, holder_role, holder_instance_id, storage_kind,
                    byte_size, media_type, availability_state, receipt_hash,
                    created_at, verified_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud_strategic_profile', ?,
                          'metadata_receipt', ?,
                          'application/vnd.yiyu.strategic-client-profile+json',
                          'ready', ?, ?, ?, NULL, 'cloud', ?)
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
                INSERT INTO secured_resources (
                    id, scope_id, resource_kind, lifecycle_state, version,
                    resource_type_key, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, 'narrative_output', 'active', ?,
                          'strategic_client_profile', ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    lifecycle_state='active', version=excluded.version,
                    updated_at=excluded.updated_at, deleted_at=NULL
                """,
                (
                    profile_id,
                    identity.scope_id,
                    next_aggregate_version,
                    str(current["created_at"] or now) if current else now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO narrative_outputs (
                    id, scope_id, client_id, source_set_id, current_version,
                    lifecycle_state, title, artifact_kind, visibility_scope,
                    publication_state, owner_membership_id, published_at,
                    version, created_at, updated_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, 'strategic_profile',
                          'organization', 'published', ?, ?, ?, ?, ?, NULL,
                          'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_set_id=excluded.source_set_id,
                    current_version=excluded.current_version,
                    lifecycle_state='active', title=excluded.title,
                    publication_state='published', published_at=excluded.published_at,
                    version=excluded.version, updated_at=excluded.updated_at,
                    deleted_at=NULL
                """,
                (
                    profile_id,
                    identity.scope_id,
                    project_id,
                    source_set_id,
                    next_content_version,
                    f"{str(project['name'] or '项目')}客户档案",
                    identity.membership_id,
                    now,
                    next_aggregate_version,
                    str(current["created_at"] or now) if current else now,
                    now,
                    repository.cloud_instance_id,
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
                    artifact_version_id,
                    identity.scope_id,
                    profile_id,
                    next_content_version,
                    receipt_hash,
                    manifest_id,
                    source_set_id,
                    identity.membership_id,
                    now,
                    repository.cloud_instance_id,
                    sha256_text(
                        f"{profile_id}|{next_content_version}|{receipt_hash}|{source_set_id}"
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO derivation_lineage (
                    id, scope_id, source_set_id, policy_version_id,
                    grant_generation, derivative_kind, derivative_object_id,
                    generator_version, generated_at, invalidated_at,
                    source_version, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, NULL, 1, 'narrative_output', ?,
                          'strategy_companion_local_wiki_v1', ?, NULL, ?,
                          'cloud', ?)
                """,
                (
                    lineage_id,
                    identity.scope_id,
                    source_set_id,
                    profile_id,
                    now,
                    next_content_version,
                    repository.cloud_instance_id,
                ),
            )
            result_hash = receipt_hash
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
                ) VALUES (?, ?, ?, ?, 'narrative_output', ?, ?, ?, ?, NULL,
                          'settled', ?, ?, ?, ?, ?, 'cloud', ?)
                """,
                (
                    repository._record_id("cmd", operation_id, command_type),  # noqa: SLF001
                    identity.scope_id,
                    operation_id,
                    idempotency_key,
                    profile_id,
                    command_type,
                    identity.principal_id,
                    current_aggregate_version,
                    identity.membership_id,
                    manifest_id,
                    payload_hash,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            run_id = repository._record_id("run", operation_id, bot_id)  # noqa: SLF001
            connection.execute(
                """
                INSERT INTO execution_runs (
                    id, scope_id, bot_id, rule_id, task_id, operation_id,
                    status, initiator_membership_id, proposal_id, run_kind,
                    progress_object_manifest_id, result_object_manifest_id,
                    started_at, finished_at, version, lifecycle_state,
                    created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, NULL, NULL, ?, 'completed', ?, NULL,
                          'strategic_profile_rebuild', NULL, ?, ?, ?, 1, 'active',
                          ?, ?, NULL)
                """,
                (
                    run_id,
                    identity.scope_id,
                    bot_id,
                    operation_id,
                    identity.membership_id,
                    manifest_id,
                    now,
                    now,
                    now,
                    now,
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
                          'strategic_profile_projection_v1', ?, ?, ?, ?, ?,
                          'active', ?, ?, NULL, 'cloud', ?)
                """,
                (
                    repository._record_id("recon", operation_id, profile_id),  # noqa: SLF001
                    identity.scope_id,
                    operation_id,
                    repository.cloud_instance_id,
                    manifest_id,
                    now,
                    now,
                    next_content_version,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            event_type = "gc14.strategic_profile.updated"
            event_hash = sha256_text(
                f"{operation_id}|{profile_id}|{next_content_version}|{receipt_hash}"
            )
            connection.execute(
                """
                INSERT INTO outbox_events (
                    id, scope_id, operation_id, aggregate_version, event_type,
                    status, aggregate_type, aggregate_id,
                    event_object_manifest_id, event_hash, available_at,
                    published_at, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, 'published', 'narrative_output', ?, ?, ?,
                          ?, ?, 'cloud', ?)
                """,
                (
                    repository._record_id("evt", operation_id, event_type),  # noqa: SLF001
                    identity.scope_id,
                    operation_id,
                    next_aggregate_version,
                    event_type,
                    profile_id,
                    manifest_id,
                    event_hash,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                "UPDATE outbox_events SET status='published', published_at=? "
                "WHERE scope_id=? AND aggregate_type='client' AND aggregate_id=? "
                "AND event_type='gc13.project_knowledge.strategic_profile_requested' "
                "AND status='pending'",
                (now, identity.scope_id, project_id),
            )
            audit_id = repository._record_id(  # noqa: SLF001
                "audit", operation_id, "strategic-profile-updated"
            )
            connection.execute(
                """
                INSERT INTO audit_events (
                    id, scope_id, operation_id, actor_id, action, event_hash,
                    actor_membership_id, target_resource_id,
                    details_object_manifest_id, occurred_at, origin_instance_id,
                    created_at, integrity_hash, authority_role
                ) VALUES (?, ?, ?, ?, 'strategic_profile.updated', ?, ?, ?, ?, ?, ?,
                          ?, ?, 'cloud')
                """,
                (
                    audit_id,
                    identity.scope_id,
                    operation_id,
                    identity.principal_id,
                    event_hash,
                    identity.membership_id,
                    profile_id,
                    manifest_id,
                    now,
                    repository.cloud_instance_id,
                    now,
                    sha256_text(f"{audit_id}|{event_hash}|{now}"),
                ),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise

    from .workbench_outputs import project_narrative

    result = project_narrative(repository, identity, project_id=project_id)
    result["idempotentReplay"] = False
    result["agentRun"] = AgentRunReceipt(
        agent_kind="strategy_companion",
        run_id=run_id,
        state="completed",
        stage="profile_ready",
        message="已按证据更新受影响的客户档案栏目",
        result_version=next_content_version,
    ).as_dict()
    return result
