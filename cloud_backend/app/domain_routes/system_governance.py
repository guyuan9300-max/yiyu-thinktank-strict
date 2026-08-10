from __future__ import annotations

from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, Query

from strict_common.ids import new_id

from ..repositories.system_governance import SystemGovernanceRepository
from ..repository import CloudRepository, SessionIdentity


def register_system_governance_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    governance = SystemGovernanceRepository(repository)
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]
    Idempotency = Annotated[str | None, Header(alias="Idempotency-Key")]

    @app.get("/api/v2/system-governance/recovery-sets")
    def list_recovery_sets(
        identity: Identity,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        return {"items": governance.list_recovery_sets(identity, limit=limit)}

    @app.post("/api/v2/system-governance/recovery-sets")
    def create_recovery_set(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return governance.create_database_backup(
            identity,
            idempotency_key=idempotency_key or new_id(),
            retention_days=int((payload or {}).get("retentionDays") or 30),
        )

    @app.get("/api/v2/system-governance/release-gates")
    def list_release_gates(
        identity: Identity,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        return {"items": governance.list_release_gates(identity, limit=limit)}

    @app.post("/api/v2/system-governance/release-gates")
    def decide_release_gate(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return governance.decide_release_gate(
            identity,
            candidate_version=str((payload or {}).get("candidateVersion") or ""),
            recovery_set_id=str((payload or {}).get("recoverySetId") or ""),
            evidence_version=str((payload or {}).get("evidenceVersion") or ""),
            evidence_hash=str((payload or {}).get("evidenceHash") or ""),
            decision=str((payload or {}).get("decision") or ""),
            blocking_reason=(payload or {}).get("blockingReason"),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/system-governance/git-mappings")
    def list_git_mappings(
        identity: Identity,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        return {"items": governance.list_git_mappings(identity, limit=limit)}

    @app.post("/api/v2/system-governance/git-mappings")
    def record_git_mapping(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return governance.record_git_mapping(
            identity,
            repository_ref=str((payload or {}).get("repositoryRef") or ""),
            commit_ref=str((payload or {}).get("commitRef") or ""),
            remote_receipt=str((payload or {}).get("remoteReceipt") or ""),
            status=str((payload or {}).get("status") or ""),
            executed_by_instance_id=str((payload or {}).get("executedByInstanceId") or ""),
            idempotency_key=idempotency_key or new_id(),
        )
