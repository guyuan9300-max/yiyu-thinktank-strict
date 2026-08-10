from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, Query

from strict_common.ids import new_id

from ..repositories import gc14_proposals
from ..repository import CloudRepository, SessionIdentity


def register_gc14_proposal_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]
    Idempotency = Annotated[str | None, Header(alias="Idempotency-Key")]

    @app.get("/api/v2/ai-proposals")
    def list_proposals(
        identity: Identity,
        client_id: Annotated[str | None, Query(alias="clientId")] = None,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        return gc14_proposals.list_proposals(
            repository,
            identity,
            client_id=client_id,
            status=status,
            kind=kind,
            limit=limit,
        )

    @app.post("/api/v2/ai-proposals")
    def create_proposal(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return gc14_proposals.create_proposal(
            repository,
            identity,
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/ai-proposals/{proposal_id}")
    def get_proposal(proposal_id: str, identity: Identity) -> dict[str, Any]:
        return gc14_proposals.get_proposal(
            repository, identity, proposal_id=proposal_id
        )

    @app.post("/api/v2/ai-proposals/{proposal_id}/approve")
    def approve_proposal(
        proposal_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return gc14_proposals.decide_proposal(
            repository,
            identity,
            proposal_id=proposal_id,
            decision="approved",
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/ai-proposals/{proposal_id}/reject")
    def reject_proposal(
        proposal_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return gc14_proposals.decide_proposal(
            repository,
            identity,
            proposal_id=proposal_id,
            decision="rejected",
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/ai-proposals/{proposal_id}/execution-preview")
    def execution_preview(proposal_id: str, identity: Identity) -> dict[str, Any]:
        return gc14_proposals.execution_preview(
            repository, identity, proposal_id=proposal_id
        )

    @app.post("/api/v2/ai-proposals/{proposal_id}/execute")
    @app.post("/api/v2/ai-proposals/{proposal_id}/execution-ticket")
    def execute_proposal(
        proposal_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return gc14_proposals.execute_proposal(
            repository,
            identity,
            proposal_id=proposal_id,
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/ai-execution-runs")
    def list_execution_runs(
        identity: Identity,
        client_id: Annotated[str | None, Query(alias="clientId")] = None,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        return gc14_proposals.list_execution_runs(
            repository, identity, client_id=client_id, limit=limit
        )
