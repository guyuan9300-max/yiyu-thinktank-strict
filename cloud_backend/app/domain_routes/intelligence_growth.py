from typing import Any

from fastapi import Depends, FastAPI, Header, Request

from ..repository import CloudRepository
from ..repositories.intelligence_growth import IntelligenceGrowthRepository


def register_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    domain = IntelligenceGrowthRepository(repository)

    @app.get("/api/v2/intelligence-growth/query")
    def query_intelligence_growth(
        request: Request,
        identity: Any = Depends(identity_dependency),
    ) -> Any:
        resource_path = str(request.query_params.get("resourcePath") or "").strip("/")
        query = {
            key: value
            for key, value in request.query_params.items()
            if key != "resourcePath"
        }
        return domain.query(
            identity,
            resource_path=resource_path,
            query=query,
        )

    @app.get("/api/v2/intelligence-growth/version")
    def intelligence_growth_version(
        request: Request,
        identity: Any = Depends(identity_dependency),
    ) -> dict[str, Any]:
        resource_path = str(request.query_params.get("resourcePath") or "").strip("/")
        query = {
            key: value
            for key, value in request.query_params.items()
            if key != "resourcePath"
        }
        return domain.version(
            identity,
            resource_path=resource_path,
            query=query,
        )

    @app.post("/api/v2/intelligence-growth/command")
    async def command_intelligence_growth(
        request: Request,
        identity: Any = Depends(identity_dependency),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
        resource_path = str(body.get("resourcePath") or "").strip("/")
        method = str(body.get("method") or "POST").strip().upper()
        payload = body.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        return domain.command(
            identity,
            resource_path=resource_path,
            method=method,
            payload=payload,
            idempotency_key=idempotency_key,
        )
