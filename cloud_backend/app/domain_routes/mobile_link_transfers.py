from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, Query, status

from strict_common.ids import new_id

from ..repository import CloudRepository, SessionIdentity
from ..repositories.mobile_link_transfers import MobileLinkTransferRepository


def register_mobile_link_transfer_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    domain = MobileLinkTransferRepository(repository)
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]

    @app.post(
        "/api/v2/mobile-link-transfers",
        status_code=status.HTTP_202_ACCEPTED,
    )
    def submit(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.submit(
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/mobile-link-transfers")
    def list_transfers(
        identity: Identity,
        project_id: Annotated[str | None, Query(alias="projectId")] = None,
    ) -> dict[str, Any]:
        return domain.list(identity, project_id=project_id)

    @app.get("/api/v2/mobile-link-transfers/pending")
    def pending(
        identity: Identity,
        project_id: Annotated[str, Query(alias="projectId")],
    ) -> dict[str, Any]:
        return domain.list(identity, project_id=project_id, pending_only=True)

    @app.post("/api/v2/mobile-link-transfers/{run_id}/claim")
    def claim(run_id: str, identity: Identity) -> dict[str, Any]:
        return domain.claim(identity, run_id=run_id)

    @app.post("/api/v2/mobile-link-transfers/{run_id}/settle")
    def settle(
        run_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
    ) -> dict[str, Any]:
        return domain.settle(identity, run_id=run_id, payload=payload)
