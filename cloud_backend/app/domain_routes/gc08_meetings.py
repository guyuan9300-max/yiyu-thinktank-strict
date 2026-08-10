"""Detached GC-08 cloud route registrar for the integration thread."""

from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header

from strict_common.ids import new_id

from ..repositories.gc08_meetings import GC08MeetingMinutesRepository
from ..repository import CloudRepository, SessionIdentity


def register_gc08_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    domain = GC08MeetingMinutesRepository(repository)

    @app.post(
        "/api/v2/domain/gc08/projects/{project_id}/meetings/{meeting_id}/minutes"
    )
    def publish_minutes(
        project_id: str,
        meeting_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.publish_minutes(
            identity,
            project_id=project_id,
            meeting_id=meeting_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )
