from typing import Any

from fastapi import Depends, FastAPI, Header, Request
from strict_common.ids import new_id

from ..repositories.platform_integrations import PlatformIntegrationsRepository
from ..repository import CloudRepository, RepositoryError
from ..repositories.platform_configurations import PlatformConfigurationRepository


def register_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    platform = PlatformIntegrationsRepository(repository)
    configurations = PlatformConfigurationRepository(repository)

    @app.get("/api/v2/platform-integrations/query")
    def query_platform_integrations(
        request: Request,
        identity: Any = Depends(identity_dependency),
    ) -> dict[str, Any]:
        resource_path = str(request.query_params.get("resourcePath") or "").strip("/")
        authorization_scope = str(
            request.query_params.get("authorizationScope") or "organization"
        ).strip()
        query = {
            key: value
            for key, value in request.query_params.items()
            if key not in {"resourcePath", "authorizationScope"}
        }
        return {
            "resourcePath": resource_path,
            "authorizationScope": authorization_scope,
            "resource": platform.query(
                identity,
                resource_path=resource_path,
                authorization_scope=authorization_scope,
                query=query,
            ),
        }

    @app.post("/api/v2/platform-integrations/command")
    async def command_platform_integrations(
        request: Request,
        identity: Any = Depends(identity_dependency),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            body = {}
        resource_path = str(body.get("resourcePath") or "").strip("/")
        authorization_scope = str(
            body.get("authorizationScope") or "organization"
        ).strip()
        method = str(body.get("method") or "POST").strip().upper()
        query = body.get("query")
        if not isinstance(query, dict):
            query = {}
        payload = body.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        return {
            "resourcePath": resource_path,
            "authorizationScope": authorization_scope,
            "method": method,
            "result": platform.command(
                identity,
                resource_path=resource_path,
                authorization_scope=authorization_scope,
                method=method,
                query=query,
                payload=payload,
                idempotency_key=idempotency_key,
            ),
        }

    # Personal transcription preference is loaded during every retained
    # settings-page startup.  Keep this narrow route beside the already
    # registered platform adapter so production does not need to mount the
    # broad pre-blueprint organization-access registrar.
    @app.get("/api/v2/organization-access/settings/transcription-preference")
    def transcription_preference(
        identity: Any = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return configurations.read(
            identity,
            configuration_kind="transcription_preference",
            defaults={"provider": "local"},
            personal_only=True,
        )

    @app.post("/api/v2/organization-access/settings/transcription-preference")
    def update_transcription_preference(
        payload: dict[str, Any],
        identity: Any = Depends(identity_dependency),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        provider = str(payload.get("provider") or "local").strip()
        if provider not in {"local", "organization_cloud"}:
            raise RepositoryError(
                422,
                "transcription_provider_invalid",
                "转写偏好必须是 local 或 organization_cloud",
            )
        try:
            expected_version = int(payload.get("expectedVersion") or 0)
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                422,
                "expected_version_invalid",
                "expectedVersion 无效",
            ) from exc
        return configurations.upsert(
            identity,
            configuration_kind="transcription_preference",
            scope_kind="personal",
            provider=provider,
            public_config={"provider": provider},
            expected_version=expected_version,
            idempotency_key=idempotency_key or new_id(),
        )
