from typing import Any

from fastapi import Body, Depends, FastAPI, Header, Request

from strict_common.ids import new_id

from ..repository import CloudRepository
from ..repositories.workflow import WorkflowRepository


def register_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    workflow = WorkflowRepository(repository)

    @app.api_route(
        "/api/v2/workflow/{workflow_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def workflow_operation(
        workflow_path: str,
        request: Request,
        payload: dict[str, Any] | None = Body(default=None),
        identity: Any = Depends(identity_dependency),
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
        ),
    ) -> dict[str, Any]:
        return workflow.dispatch(
            identity,
            method=request.method,
            path=workflow_path,
            query=dict(request.query_params),
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )
