from __future__ import annotations

from typing import Any, Annotated

from fastapi import Depends, FastAPI, Header, Query

from strict_common.ids import new_id

from ..repository import CloudRepository, SessionIdentity
from ..repositories.data_center_support_88 import DataCenterSupportRepository


def register_data_center_support_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    support = DataCenterSupportRepository(repository)

    @app.post("/api/v2/data-center-support/resolve")
    def resolve(payload: dict[str, Any], identity: SessionIdentity = Depends(identity_dependency)) -> dict[str, Any]:
        return support.resolve(identity, payload)

    @app.get("/api/v2/data-center-support/team-sync/stats")
    def team_sync_stats(identity: SessionIdentity = Depends(identity_dependency)) -> dict[str, Any]:
        return support.team_sync_stats(identity)

    @app.get("/api/v2/data-center-support/evidence-quality")
    def evidence_quality(
        label: str | None = Query(default=None),
        limit: Annotated[int, Query(ge=1, le=200)] = 120,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> list[dict[str, Any]]:
        return support.evidence_labels(identity, label=label, limit=limit)

    @app.post("/api/v2/data-center-support/evidence-quality/{annotation_id}/label")
    def label_evidence(
        annotation_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return support.label_evidence(
            identity,
            annotation_id=annotation_id,
            label=str(payload.get("label") or ""),
            note=str(payload.get("note") or ""),
            idempotency_key=idempotency_key or new_id(),
        )
