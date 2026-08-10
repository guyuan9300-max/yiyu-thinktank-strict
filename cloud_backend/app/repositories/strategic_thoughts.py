from __future__ import annotations

import json
from typing import Any, Mapping

from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import canonical_json, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from . import gc14_proposals
from .workbench_outputs import project_narrative


_DIMENSION_META: dict[str, tuple[str, str, str]] = {
    "essence": ("narrative_upgrade", "机构定位出现值得持续验证的信号", "结合后续材料持续核验机构定位与核心价值。"),
    "business_intro": ("operating_model", "业务与项目组合呈现新的观察信号", "对照服务对象、项目方法和成效证据继续验证。"),
    "cooperation": ("opportunity_window", "合作关系中出现值得跟进的窗口", "明确合作目标、边界和下一步可验证行动。"),
    "people": ("operating_model", "关键人物与职责分工值得持续关注", "核对关键人物、职责变化及其对项目推进的影响。"),
    "timeline": ("strategic_shift", "关键进展可能反映阶段变化", "沿时间线核对阶段变化、里程碑和未决事项。"),
    "next_steps": ("execution_bottleneck", "下一步行动中存在需要聚焦的判断", "将建议拆成可核验的行动，并由用户决定是否转为任务。"),
}


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _record_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_" + sha256_text("\x1f".join(parts))[:30]


def _derived_items(profile: Mapping[str, Any], *, limit: int) -> list[dict[str, Any]]:
    profile_id = str(profile.get("id") or "")
    profile_version = int(profile.get("rev") or 0)
    client_id = str(profile.get("clientId") or "")
    client_name = str(profile.get("clientName") or "")
    generated_at = str(profile.get("generatedAt") or profile.get("updatedAt") or utc_now())
    items: list[dict[str, Any]] = []
    for raw in profile.get("dimensions") or []:
        if not isinstance(raw, Mapping):
            continue
        dimension = str(raw.get("dimension") or "essence")
        narrative = str(raw.get("narrative") or "").strip()
        if not narrative:
            continue
        insight_type, line, recommendation = _DIMENSION_META.get(
            dimension, _DIMENSION_META["essence"]
        )
        references = [
            dict(value)
            for value in raw.get("references") or []
            if isinstance(value, Mapping)
        ]
        fingerprint = sha256_text(
            canonical_json(
                {
                    "profileId": profile_id,
                    "profileVersion": profile_version,
                    "dimension": dimension,
                    "narrative": narrative,
                    "references": references,
                }
            )
        )
        sources = []
        for reference in references:
            source_type = str(reference.get("sourceType") or "knowledge")
            sources.append(
                {
                    "sourceType": (
                        "document"
                        if source_type == "local_document"
                        else "knowledge"
                    ),
                    "sourceId": str(reference.get("sourceId") or "") or None,
                    "label": str(reference.get("label") or "客户档案证据"),
                    "detail": str(reference.get("sourceUrl") or "") or None,
                }
            )
        if not sources:
            sources = [
                {
                    "sourceType": "client_dna",
                    "sourceId": profile_id or None,
                    "label": "客户档案",
                    "detail": dimension,
                }
            ]
        items.append(
            {
                "id": _record_id("thought", client_id, fingerprint),
                "scope": "client",
                "clientId": client_id,
                "clientName": client_name,
                "projectModuleId": None,
                "projectModuleName": None,
                "line": line,
                "observation": narrative,
                "suggestion": recommendation,
                "confidence": None,
                "confidenceLevel": str(raw.get("confidence") or "medium"),
                "status": "draft",
                "isSystem": False,
                "dueDateHint": "",
                "tags": [dimension],
                "sources": sources,
                "evidenceCount": max(1, len(references)),
                "generatedAt": generated_at,
                "evidenceLevel": "strong" if references else "medium",
                "reason": str(raw.get("confidenceReason") or "来自严格客户档案"),
                "insightType": insight_type,
                "insightText": narrative,
                "futureJudgment": line,
                "whyItMatters": line,
                "recommendedAction": recommendation,
                "evidenceSummary": narrative,
                "evidenceLabels": [str(item.get("label") or "") for item in sources],
                "sourceFingerprint": fingerprint,
                "isFavorite": False,
                "isDeleted": False,
                "review": None,
                "version": 0,
                "profileId": profile_id,
                "profileVersion": profile_version,
                "dimension": dimension,
            }
        )
    return items[: max(1, min(limit, 24))]


def _proposal_thought(item: Mapping[str, Any], proposal: Mapping[str, Any]) -> dict[str, Any]:
    status = str(proposal.get("status") or "draft")
    review_status = {
        "approved": "confirmed",
        "rejected": "dismissed",
        "executed": "confirmed",
    }.get(status, "draft")
    return {
        **dict(item),
        "id": str(proposal.get("id") or item.get("id") or ""),
        "status": review_status,
        "version": int(proposal.get("version") or 1),
        "review": (
            {
                "thoughtId": str(proposal.get("id") or ""),
                "status": review_status,
                "note": str(
                    proposal.get("decisionNote")
                    or proposal.get("rejectedReason")
                    or ""
                ),
                "taskId": None,
                "judgmentId": str(proposal.get("id") or ""),
                "reviewedAt": proposal.get("decidedAt"),
                "reviewedBy": proposal.get("decidedBy"),
            }
            if status in {"approved", "rejected", "executed"}
            else None
        ),
    }


def _thought_from_proposal(proposal: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = proposal.get("payload")
    if not isinstance(payload, Mapping) or payload.get("recordKind") != "strategic_thought":
        return None
    thought = payload.get("thought")
    if not isinstance(thought, Mapping):
        return None
    result = _proposal_thought(thought, proposal)
    result["isDeleted"] = str(proposal.get("lifecycleState") or "active") != "active"
    return result


def _all_strategic_proposals(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    client_id: str,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT proposal.*,manifest.receipt FROM ai_proposals AS proposal "
            "JOIN object_manifests AS manifest ON manifest.scope_id=proposal.scope_id "
            "AND manifest.id=proposal.payload_object_manifest_id "
            "WHERE proposal.scope_id=? AND proposal.operation_kind='judgment_review' "
            "ORDER BY proposal.updated_at DESC,proposal.id LIMIT 200",
            (identity.scope_id,),
        ).fetchall()
        result = []
        for row in rows:
            receipt = _json(row["receipt"], {})
            if not isinstance(receipt, Mapping) or str(receipt.get("clientId") or "") != client_id:
                continue
            payload = receipt.get("payload")
            if not isinstance(payload, Mapping) or payload.get("recordKind") != "strategic_thought":
                continue
            try:
                repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=client_id
                )
            except RepositoryError:
                continue
            proposal = gc14_proposals._proposal_payload(connection, identity, row)  # noqa: SLF001
            approval = gc14_proposals._latest_approval(  # noqa: SLF001
                connection, identity, str(row["id"])
            )
            proposal["decisionNote"] = (
                str(approval["decision_note"] or "") if approval is not None else ""
            )
            proposal["lifecycleState"] = str(row["lifecycle_state"] or "active")
            result.append(proposal)
        return result


def _favorite_ids(
    repository: CloudRepository,
    identity: SessionIdentity,
    proposal_ids: list[str],
) -> set[str]:
    if not proposal_ids:
        return set()
    placeholders = ",".join("?" for _ in proposal_ids)
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            f"""
            SELECT members.source_object_id
            FROM source_sets AS sets
            JOIN source_set_members AS members
              ON members.scope_id=sets.scope_id AND members.source_set_id=sets.id
             AND members.lifecycle_state='active' AND members.removed_at IS NULL
            WHERE sets.scope_id=? AND sets.created_by_principal_id=?
              AND sets.purpose_kind='strategic_thought_favorite'
              AND sets.lifecycle_state='active'
              AND members.source_object_kind='ai_proposal'
              AND members.source_object_id IN ({placeholders})
            """,
            (identity.scope_id, identity.principal_id, *proposal_ids),
        ).fetchall()
    return {str(row["source_object_id"]) for row in rows}


def list_thoughts(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    client_id: str,
    include_dismissed: bool = False,
    include_deleted: bool = False,
    limit: int = 24,
) -> dict[str, Any]:
    if not client_id:
        return {
            "items": [],
            "total": 0,
            "generatedAt": utc_now(),
            "selectedClientId": None,
            "selectedProjectModuleId": None,
            "usingMockData": False,
        }
    profile = project_narrative(repository, identity, project_id=client_id)
    derived = _derived_items(profile, limit=max(limit, 24))
    proposals = _all_strategic_proposals(
        repository, identity, client_id=client_id
    )
    persisted: list[dict[str, Any]] = []
    fingerprints: set[str] = set()
    for proposal in proposals:
        thought = _thought_from_proposal(proposal)
        if thought is None:
            continue
        persisted.append(thought)
        fingerprint = str(thought.get("sourceFingerprint") or "")
        if fingerprint:
            fingerprints.add(fingerprint)
    items = persisted + [
        item for item in derived if str(item.get("sourceFingerprint") or "") not in fingerprints
    ]
    favorite_ids = _favorite_ids(
        repository, identity, [str(item["id"]) for item in persisted]
    )
    for item in items:
        item["isFavorite"] = str(item["id"]) in favorite_ids
    if not include_dismissed:
        items = [item for item in items if item.get("status") != "dismissed"]
    if not include_deleted:
        items = [item for item in items if not item.get("isDeleted")]
    items = items[: max(1, min(limit, 200))]
    return {
        "items": items,
        "total": len(items),
        "generatedAt": utc_now(),
        "selectedClientId": client_id,
        "selectedProjectModuleId": None,
        "usingMockData": False,
    }


def _materialize(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    thought_id: str,
    client_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    def ensure_secured(proposal: Mapping[str, Any]) -> dict[str, Any]:
        now = utc_now()
        lifecycle_state = str(proposal.get("lifecycleState") or "active")
        deleted_at = now if lifecycle_state == "deleted" else None
        with repository._connection() as connection:  # noqa: SLF001
            connection.execute(
                """INSERT INTO secured_resources
                (id,scope_id,resource_kind,lifecycle_state,version,resource_type_key,
                 created_at,updated_at,deleted_at,authority_role,origin_instance_id)
                VALUES (?,?,'ai_proposal',?,?,'strategic_thought',?,?,?,'cloud',?)
                ON CONFLICT(id) DO UPDATE SET version=excluded.version,
                 lifecycle_state=excluded.lifecycle_state,deleted_at=excluded.deleted_at,
                 updated_at=excluded.updated_at""",
                (
                    str(proposal["id"]),
                    identity.scope_id,
                    lifecycle_state,
                    int(proposal.get("version") or 1),
                    str(proposal.get("createdAt") or now),
                    now,
                    deleted_at,
                    repository.cloud_instance_id,
                ),
            )
            connection.commit()
        return dict(proposal)

    proposals = gc14_proposals.list_proposals(
        repository, identity, client_id=client_id, kind="judgment_review", limit=200
    )
    for proposal in proposals:
        if str(proposal.get("id") or "") == thought_id:
            return ensure_secured(proposal)
        thought = _thought_from_proposal(proposal)
        if thought and str(thought.get("virtualId") or "") == thought_id:
            return ensure_secured(proposal)
    with repository._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            "SELECT proposal.*,manifest.receipt FROM ai_proposals AS proposal "
            "JOIN object_manifests AS manifest ON manifest.scope_id=proposal.scope_id "
            "AND manifest.id=proposal.payload_object_manifest_id "
            "WHERE proposal.scope_id=? AND proposal.id=?",
            (identity.scope_id, thought_id),
        ).fetchone()
        if row is not None:
            receipt = _json(row["receipt"], {})
            if isinstance(receipt, Mapping) and str(receipt.get("clientId") or "") == client_id:
                repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=client_id
                )
                proposal = gc14_proposals._proposal_payload(connection, identity, row)  # noqa: SLF001
                proposal["lifecycleState"] = str(row["lifecycle_state"] or "active")
                return ensure_secured(proposal)
    current = list_thoughts(
        repository,
        identity,
        client_id=client_id,
        include_dismissed=True,
        include_deleted=True,
        limit=200,
    )
    thought = next((item for item in current["items"] if item["id"] == thought_id), None)
    if thought is None:
        raise RepositoryError(404, "strategic_thought_missing", "战略判断不存在")
    proposal = gc14_proposals.create_proposal(
        repository,
        identity,
        payload={
            "clientId": client_id,
            "kind": "judgment_review",
            "title": str(thought.get("line") or "战略判断"),
            "summary": str(thought.get("insightText") or thought.get("observation") or ""),
            "rationale": str(thought.get("reason") or "来自严格客户档案与证据"),
            "riskLevel": "low",
            "sourceRefs": [
                f"{item.get('sourceType')}:{item.get('sourceId') or ''}"
                for item in thought.get("sources") or []
                if isinstance(item, Mapping)
            ],
            "boundaryNotes": ["判断确认不会暗中创建或修改任务"],
            "payload": {
                "recordKind": "strategic_thought",
                "virtualId": thought_id,
                "thought": {**dict(thought), "virtualId": thought_id},
            },
        },
        idempotency_key=f"{idempotency_key}:materialize:{thought_id}",
    )
    return ensure_secured(proposal)


def resolve_client_id(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    thought_id: str,
) -> str:
    with repository._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            "SELECT manifest.receipt FROM ai_proposals AS proposal "
            "JOIN object_manifests AS manifest ON manifest.scope_id=proposal.scope_id "
            "AND manifest.id=proposal.payload_object_manifest_id "
            "WHERE proposal.scope_id=? AND proposal.id=?",
            (identity.scope_id, thought_id),
        ).fetchone()
        client_rows = connection.execute(
            "SELECT id FROM clients WHERE scope_id=? AND lifecycle_state='active'",
            (identity.scope_id,),
        ).fetchall()
    if row is not None:
        receipt = _json(row["receipt"], {})
        client_id = str(receipt.get("clientId") or "") if isinstance(receipt, Mapping) else ""
        if client_id:
            with repository._connection() as connection:  # noqa: SLF001
                repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=client_id
                )
            return client_id
    for client_row in client_rows:
        client_id = str(client_row["id"])
        try:
            profile = project_narrative(repository, identity, project_id=client_id)
        except RepositoryError:
            continue
        if any(
            str(item.get("id") or "") == thought_id
            for item in _derived_items(profile, limit=24)
        ):
            return client_id
    raise RepositoryError(404, "strategic_thought_missing", "战略判断不存在")


def refresh_thoughts(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    client_id: str,
    limit: int,
    idempotency_key: str,
) -> dict[str, Any]:
    if not client_id:
        raise RepositoryError(422, "strategic_thought_client_required", "请选择项目后刷新战略判断")
    current = list_thoughts(
        repository, identity, client_id=client_id, include_dismissed=True, limit=limit
    )
    materialized_ids: list[str] = []
    for item in current["items"]:
        if int(item.get("version") or 0) > 0:
            materialized_ids.append(str(item["id"]))
            continue
        proposal = _materialize(
            repository,
            identity,
            thought_id=str(item["id"]),
            client_id=client_id,
            idempotency_key=idempotency_key,
        )
        materialized_ids.append(str(proposal["id"]))
    if materialized_ids:
        bot_id = builtin_agent_id(identity.organization_id, "strategy_companion")
        now = utc_now()
        with repository._connection() as connection:  # noqa: SLF001
            bot = connection.execute(
                "SELECT id FROM bot_definitions WHERE id=? AND agent_kind='strategy_companion' "
                "AND enabled=1 AND lifecycle_state='active'",
                (bot_id,),
            ).fetchone()
            anchor = connection.execute(
                "SELECT proposal.payload_object_manifest_id,command.operation_id "
                "FROM ai_proposals AS proposal JOIN commands AS command "
                "ON command.scope_id=proposal.scope_id AND command.aggregate_id=proposal.id "
                "WHERE proposal.scope_id=? AND proposal.id=? "
                "ORDER BY command.submitted_at DESC LIMIT 1",
                (identity.scope_id, materialized_ids[0]),
            ).fetchone()
            if bot is not None and anchor is not None:
                run_id = _record_id(
                    "run", identity.scope_id, "strategic-thought-refresh", idempotency_key
                )
                connection.execute(
                    """INSERT INTO execution_runs
                    (id,scope_id,bot_id,rule_id,task_id,operation_id,status,
                     initiator_membership_id,proposal_id,run_kind,
                     progress_object_manifest_id,result_object_manifest_id,started_at,
                     finished_at,version,lifecycle_state,created_at,updated_at,deleted_at)
                    VALUES (?,?,?,NULL,NULL,?,'completed',?,?,
                            'strategic_thought_authority_projection',NULL,?,?,?,1,'active',?,?,NULL)
                    ON CONFLICT(id) DO UPDATE SET status='completed',finished_at=excluded.finished_at,
                     updated_at=excluded.updated_at""",
                    (
                        run_id,
                        identity.scope_id,
                        bot_id,
                        str(anchor["operation_id"]),
                        identity.membership_id,
                        materialized_ids[0],
                        str(anchor["payload_object_manifest_id"]),
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                connection.commit()
    return list_thoughts(repository, identity, client_id=client_id, limit=limit)


def review_thought(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    thought_id: str,
    client_id: str,
    action: str,
    note: str,
    task_id: str | None,
    idempotency_key: str,
) -> dict[str, Any]:
    if action not in {"confirm", "dismiss", "mark_task_created"}:
        raise RepositoryError(422, "strategic_thought_review_invalid", "战略判断审阅动作无效")
    proposal = _materialize(
        repository,
        identity,
        thought_id=thought_id,
        client_id=client_id,
        idempotency_key=idempotency_key,
    )
    if action == "mark_task_created":
        raise RepositoryError(
            409,
            "strategic_thought_task_command_required",
            "转为任务必须使用正式任务命令；本审阅入口不会暗中修改任务",
        )
    status = str(proposal.get("status") or "draft")
    if status != "draft":
        thought = _thought_from_proposal(proposal)
        if thought is None:
            raise RepositoryError(409, "strategic_thought_receipt_invalid", "战略判断回执无效")
        return thought
    decided = gc14_proposals.decide_proposal(
        repository,
        identity,
        proposal_id=str(proposal["id"]),
        decision="approved" if action == "confirm" else "rejected",
        payload={"expectedVersion": proposal["version"], "note": note},
        idempotency_key=f"{idempotency_key}:review:{action}",
    )
    thought = _thought_from_proposal(decided)
    if thought is None:
        raise RepositoryError(500, "strategic_thought_receipt_invalid", "战略判断回执无效")
    if thought.get("review"):
        thought["review"]["note"] = note
    if task_id:
        thought["review"]["taskId"] = task_id
    return thought


def update_thought_state(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    thought_id: str,
    client_id: str,
    action: str,
    idempotency_key: str,
) -> dict[str, Any]:
    if action not in {"favorite", "unfavorite", "delete", "restore"}:
        raise RepositoryError(422, "strategic_thought_state_invalid", "战略判断状态动作无效")
    proposal = _materialize(
        repository,
        identity,
        thought_id=thought_id,
        client_id=client_id,
        idempotency_key=idempotency_key,
    )
    proposal_id = str(proposal["id"])
    now = utc_now()
    command_type = f"gc10.strategic_thought.{action}"
    payload_hash = sha256_text(
        canonical_json(
            {
                "thoughtId": proposal_id,
                "clientId": client_id,
                "action": action,
                "proposalVersion": int(proposal.get("version") or 1),
            }
        )
    )
    with repository._connection() as connection:  # noqa: SLF001
        replay = repository._existing_command(  # noqa: SLF001
            connection,
            scope_id=identity.scope_id,
            idempotency_key=idempotency_key,
            command_type=command_type,
            payload_hash=payload_hash,
        )
    if replay is not None:
        current = list_thoughts(
            repository,
            identity,
            client_id=client_id,
            include_dismissed=True,
            include_deleted=True,
            limit=200,
        )
        item = next((value for value in current["items"] if value["id"] == proposal_id), None)
        if item is None:
            raise RepositoryError(409, "strategic_thought_replay_missing", "战略判断重放回执已失效")
        item["idempotentReplay"] = True
        return item
    favorite_set_id = _record_id(
        "source_set", identity.scope_id, identity.principal_id, proposal_id, "favorite"
    )
    member_id = _record_id("source_member", favorite_set_id, proposal_id)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            repository._require_project_access(  # noqa: SLF001
                connection, identity, project_id=client_id, capability="project_write"
            )
            proposal_row = connection.execute(
                "SELECT payload_object_manifest_id,version FROM ai_proposals "
                "WHERE id=? AND scope_id=?",
                (proposal_id, identity.scope_id),
            ).fetchone()
            if proposal_row is None:
                raise RepositoryError(404, "strategic_thought_missing", "战略判断不存在")
            next_version = int(proposal_row["version"] or 1) + 1
            aggregate_id = (
                favorite_set_id if action in {"favorite", "unfavorite"} else proposal_id
            )
            operation_id = gc14_proposals._record_command(  # noqa: SLF001
                connection,
                repository,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type=(
                    "source_set" if action in {"favorite", "unfavorite"} else "ai_proposal"
                ),
                aggregate_id=aggregate_id,
                expected_version=int(proposal_row["version"] or 1),
                aggregate_version=next_version,
                payload_hash=payload_hash,
                result_hash=sha256_text(f"{proposal_id}|{action}|{next_version}"),
                result_manifest_id=str(proposal_row["payload_object_manifest_id"]),
                target_resource_id=client_id,
                now=now,
            )
            if action == "favorite":
                connection.execute(
                    """INSERT INTO source_sets
                    (id,scope_id,client_id,security_label_set_version,source_count,version,
                     purpose_kind,publication_state,created_by_principal_id,created_at,expires_at,
                     lifecycle_state,updated_at,deleted_at,authority_role,origin_instance_id)
                    VALUES (?,?,?,'organization-v1',1,1,'strategic_thought_favorite','draft',?,?,NULL,
                            'active',?,NULL,'cloud',?)
                    ON CONFLICT(id) DO UPDATE SET lifecycle_state='active',deleted_at=NULL,
                            updated_at=excluded.updated_at,version=source_sets.version+1""",
                    (
                        favorite_set_id,
                        identity.scope_id,
                        client_id,
                        identity.principal_id,
                        now,
                        now,
                        repository.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """INSERT INTO source_set_members
                    (id,scope_id,source_set_id,source_object_id,source_version,policy_version,
                     source_object_kind,ordinal,added_at,removed_at,version,lifecycle_state,
                     created_at,updated_at,deleted_at,authority_role,origin_instance_id)
                    VALUES (?,?,?,?,?,1,'ai_proposal',0,?,NULL,1,'active',?,?,NULL,'cloud',?)
                    ON CONFLICT(id) DO UPDATE SET source_version=excluded.source_version,
                     removed_at=NULL,lifecycle_state='active',deleted_at=NULL,
                     updated_at=excluded.updated_at,version=source_set_members.version+1""",
                    (
                        member_id,
                        identity.scope_id,
                        favorite_set_id,
                        proposal_id,
                        int(proposal.get("version") or 1),
                        now,
                        now,
                        now,
                        repository.cloud_instance_id,
                    ),
                )
            elif action == "unfavorite":
                connection.execute(
                    "UPDATE source_sets SET lifecycle_state='archived',deleted_at=?,updated_at=?,"
                    "version=version+1 WHERE id=? AND scope_id=? AND created_by_principal_id=?",
                    (now, now, favorite_set_id, identity.scope_id, identity.principal_id),
                )
                connection.execute(
                    "UPDATE source_set_members SET lifecycle_state='archived',removed_at=?,deleted_at=?,"
                    "updated_at=?,version=version+1 WHERE id=? AND scope_id=?",
                    (now, now, now, member_id, identity.scope_id),
                )
            else:
                desired = "deleted" if action == "delete" else "active"
                deleted_at = now if action == "delete" else None
                cursor = connection.execute(
                    "UPDATE ai_proposals SET lifecycle_state=?,deleted_at=?,updated_at=?,version=version+1 "
                    "WHERE id=? AND scope_id=?",
                    (desired, deleted_at, now, proposal_id, identity.scope_id),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(404, "strategic_thought_missing", "战略判断不存在")
                lifecycle_id = _record_id("lifecycle", proposal_id, action, idempotency_key)
                connection.execute(
                    """INSERT INTO lifecycle_events
                    (id,scope_id,operation_id,secured_resource_id,from_state,to_state,
                     tombstone_version,actor_id,reason_code,occurred_at,origin_instance_id,
                     created_at,integrity_hash)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        lifecycle_id,
                        identity.scope_id,
                        operation_id,
                        proposal_id,
                        "active" if action == "delete" else "deleted",
                        "deleted" if action == "delete" else "active",
                        next_version,
                        identity.principal_id,
                        "strategic_thought_user_delete"
                        if action == "delete"
                        else "strategic_thought_user_restore",
                        now,
                        repository.cloud_instance_id,
                        now,
                        sha256_text(
                            f"{lifecycle_id}|{proposal_id}|{action}|{next_version}|{now}"
                        ),
                    ),
                )
                connection.execute(
                    "UPDATE secured_resources SET lifecycle_state=?,version=?,updated_at=?,"
                    "deleted_at=? WHERE id=? AND scope_id=?",
                    (desired, next_version, now, deleted_at, proposal_id, identity.scope_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    current = list_thoughts(
        repository,
        identity,
        client_id=client_id,
        include_dismissed=True,
        include_deleted=True,
        limit=200,
    )
    result = next((item for item in current["items"] if item["id"] == proposal_id), None)
    if result is None and action == "delete":
        result = _proposal_thought(
            dict((proposal.get("payload") or {}).get("thought") or {}), proposal
        )
        result["id"] = proposal_id
        result["isDeleted"] = True
    if result is None:
        raise RepositoryError(404, "strategic_thought_missing", "战略判断状态更新后不可见")
    result["isFavorite"] = action == "favorite" or (
        action not in {"unfavorite"} and bool(result.get("isFavorite"))
    )
    return result
