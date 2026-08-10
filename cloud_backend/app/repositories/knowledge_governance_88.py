from __future__ import annotations

import json
from typing import Any, Mapping

from strict_common.ids import canonical_json, sha256_text, utc_now
from strict_common.security import payload_fingerprint

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .project_materials import GC07ProjectMaterialsRepository


class KnowledgeGovernance88Repository:
    """Durable human decisions for derived knowledge alerts on the 88-table schema."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    def list_decisions(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        decision_kind: str | None = None,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self.repository._require_project_access(  # noqa: SLF001
                connection, identity, project_id=project_id, capability="knowledge_read"
            )
            rows = connection.execute(
                """
                SELECT p.id,p.operation_kind,p.status,p.version,p.updated_at,m.receipt
                FROM ai_proposals AS p
                JOIN object_manifests AS m
                  ON m.id=p.payload_object_manifest_id AND m.scope_id=p.scope_id
                WHERE p.scope_id=? AND p.lifecycle_state='active'
                  AND p.operation_kind IN ('glossary_drift_review','fact_contradiction_review')
                ORDER BY p.updated_at DESC
                """,
                (identity.scope_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            try:
                receipt = json.loads(str(row["receipt"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(receipt, Mapping) or str(receipt.get("clientId") or "") != project_id:
                continue
            kind = str(receipt.get("decisionKind") or "")
            if decision_kind and kind != decision_kind:
                continue
            items.append(
                {
                    "id": str(receipt.get("derivedId") or row["id"]),
                    "decisionKind": kind,
                    "status": str(receipt.get("reviewStatus") or row["status"]),
                    "acceptedFactId": receipt.get("acceptedFactId"),
                    "resolutionNote": str(receipt.get("resolutionNote") or ""),
                    "reviewedAt": str(receipt.get("reviewedAt") or row["updated_at"]),
                    "reviewedBy": receipt.get("reviewedBy"),
                    "version": int(row["version"] or 1),
                }
            )
        return {"decisions": items, "count": len(items)}

    def record_decision(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        derived_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        decision_kind = str(payload.get("decisionKind") or "").strip()
        review_status = str(payload.get("reviewStatus") or "").strip()
        if decision_kind not in {"glossary_drift", "fact_contradiction"}:
            raise RepositoryError(422, "governance_decision_kind_invalid", "知识治理类型无效")
        if review_status not in {"resolved", "dismissed"}:
            raise RepositoryError(422, "governance_decision_status_invalid", "知识治理状态无效")
        accepted_fact_id = str(payload.get("acceptedFactId") or "").strip() or None
        normalized = {
            "clientId": project_id,
            "derivedId": derived_id,
            "decisionKind": decision_kind,
            "reviewStatus": review_status,
            "acceptedFactId": accepted_fact_id,
            "resolutionNote": " ".join(str(payload.get("resolutionNote") or "").split())[:1000],
        }
        payload_hash = payload_fingerprint(normalized)
        domain = GC07ProjectMaterialsRepository(self.repository)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                project = self.repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=project_id, capability="knowledge_write"
                )
                replay = domain._receipt(  # noqa: SLF001
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                now = utc_now()
                proposal_id = self.repository._record_id(  # noqa: SLF001
                    "knowledge-governance",
                    f"{identity.scope_id}:{project_id}",
                    derived_id,
                )
                current = connection.execute(
                    "SELECT version FROM ai_proposals WHERE id=? AND scope_id=?",
                    (proposal_id, identity.scope_id),
                ).fetchone()
                next_version = int(current["version"] or 0) + 1 if current else 1
                receipt = {
                    "schema": "yiyu.knowledge-governance-decision.v1",
                    **normalized,
                    "reviewedAt": now,
                    "reviewedBy": identity.membership_id,
                    "version": next_version,
                }
                serialized = canonical_json(receipt)
                receipt_hash = sha256_text(serialized)
                manifest_id = self.repository._record_id(  # noqa: SLF001
                    "manifest", proposal_id, f"{next_version}:{receipt_hash}"
                )
                connection.execute(
                    """
                    INSERT INTO object_manifests (
                        id,scope_id,storage_key,content_hash,lifecycle_state,receipt,
                        holder_role,holder_instance_id,storage_kind,byte_size,media_type,
                        availability_state,receipt_hash,created_at,verified_at,deleted_at,
                        authority_role,origin_instance_id
                    ) VALUES (?,?,NULL,?,'active',?,'organization_cloud',?,
                              'knowledge_governance_decision',?,'application/json','ready',
                              ?,?,?,NULL,'cloud',?)
                    """,
                    (
                        manifest_id,
                        identity.scope_id,
                        receipt_hash,
                        serialized,
                        identity.cloud_instance_id,
                        len(serialized.encode("utf-8")),
                        receipt_hash,
                        now,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
                operation_kind = f"{decision_kind}_review"
                connection.execute(
                    """
                    INSERT INTO ai_proposals (
                        id,scope_id,answer_id,operation_kind,payload_hash,status,
                        payload_object_manifest_id,risk_level,expires_at,version,
                        lifecycle_state,created_at,updated_at,deleted_at
                    ) VALUES (?,?,NULL,?,?,?,?, 'knowledge_governance',NULL,?,
                              'active',?,?,NULL)
                    ON CONFLICT(id) DO UPDATE SET payload_hash=excluded.payload_hash,
                        status=excluded.status,
                        payload_object_manifest_id=excluded.payload_object_manifest_id,
                        version=excluded.version,lifecycle_state='active',
                        updated_at=excluded.updated_at,deleted_at=NULL
                    """,
                    (
                        proposal_id,
                        identity.scope_id,
                        operation_kind,
                        receipt_hash,
                        review_status,
                        manifest_id,
                        next_version,
                        now,
                        now,
                    ),
                )
                approval_id = self.repository._record_id(  # noqa: SLF001
                    "knowledge-governance-approval", proposal_id, str(next_version)
                )
                connection.execute(
                    """
                    INSERT INTO ai_approvals (
                        id,scope_id,proposal_id,approver_principal_id,decision,
                        decided_at,approver_membership_id,decision_note,approved_rule_id,
                        version,lifecycle_state,created_at,updated_at,deleted_at
                    ) VALUES (?,?,?,?,?,?,?,?,NULL,1,'active',?,?,NULL)
                    """,
                    (
                        approval_id,
                        identity.scope_id,
                        proposal_id,
                        identity.principal_id,
                        review_status,
                        now,
                        identity.membership_id,
                        normalized["resolutionNote"],
                        now,
                        now,
                    ),
                )
                result = {
                    "ok": True,
                    "id": derived_id,
                    "status": review_status,
                    "decisionKind": decision_kind,
                    "acceptedFactId": accepted_fact_id,
                    "version": next_version,
                }
                domain._record_command(  # noqa: SLF001
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type=f"knowledge_governance.{decision_kind}.reviewed",
                    aggregate_type="client",
                    aggregate_id=project_id,
                    aggregate_version=int(project["version"] or 1),
                    expected_aggregate_version=int(project["version"] or 1),
                    result=result,
                    target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
