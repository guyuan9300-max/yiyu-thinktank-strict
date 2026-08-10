"""Unregistered GC-15 lifecycle routes for the shared integration thread."""

from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header

from ..repositories.gc15_lifecycle import GC15LifecycleRepository
from ..repository import CloudRepository, SessionIdentity


def register_gc15_lifecycle_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    domain = GC15LifecycleRepository(repository)
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]
    IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key")]

    @app.post("/api/v2/domain/lifecycle/resources/{resource_id}/legal-holds")
    def place_legal_hold(
        resource_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.place_legal_hold(
            identity,
            resource_id=resource_id,
            reason=str(payload.get("reason") or ""),
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/domain/lifecycle/legal-holds/{hold_id}/release")
    def release_legal_hold(
        hold_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.release_legal_hold(
            identity,
            hold_id=hold_id,
            expected_version=int(payload.get("expectedVersion") or 0),
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/domain/lifecycle/resources/{resource_id}/purge")
    def settle_purge(
        resource_id: str,
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.settle_purge(
            identity,
            resource_id=resource_id,
            idempotency_key=idempotency_key,
        )


__all__ = ["register_gc15_lifecycle_routes"]
