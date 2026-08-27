from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, Query

from strict_common.ids import new_id

from ..repository import CloudRepository, SessionIdentity
from ..repositories.mobile_sync import MobileSyncRepository
from ..repositories.platform_configurations import PlatformConfigurationRepository


def register_mobile_sync_routes(app: FastAPI, repository: CloudRepository, identity_dependency: Any) -> None:
    domain = MobileSyncRepository(repository)
    platform_configurations = PlatformConfigurationRepository(repository)
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]

    @app.post("/api/v2/mobile-sync/bootstrap")
    def bootstrap(identity: Identity) -> dict[str, Any]:
        return domain.bootstrap(identity)

    @app.get("/api/v2/mobile-sync/delta")
    def delta(
        identity: Identity,
        cursor: Annotated[str, Query(min_length=1)],
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
    ) -> dict[str, Any]:
        return domain.delta(identity, cursor=cursor, limit=limit)

    @app.post("/api/v2/mobile-devices/push-registration")
    def register_push_device(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        token = str(payload.get("pushToken") or "").strip()
        device_id = str(payload.get("deviceId") or "").strip()
        platform = str(payload.get("platform") or "android").strip()
        provider = str(payload.get("provider") or "expo_push").strip()
        if not token or not device_id:
            from ..repository import RepositoryError

            raise RepositoryError(422, "mobile_push_registration_invalid", "缺少移动推送设备信息")
        configuration_kind = f"mobile_push_device:{device_id}"
        current = platform_configurations.read_exact(
            identity,
            configuration_kind=configuration_kind,
            scope_kind="personal",
        )
        saved = platform_configurations.upsert(
            identity,
            configuration_kind=configuration_kind,
            scope_kind="personal",
            provider=provider,
            public_config={
                "deviceId": device_id,
                "platform": platform,
                "provider": provider,
                "deliveryState": "registered",
            },
            expected_version=int(current.get("version") or 0),
            idempotency_key=idempotency_key or new_id(),
            secret_bundle={"pushToken": token},
            secret_action="replace",
        )
        return {
            "status": "registered",
            "deviceId": device_id,
            "provider": provider,
            "version": saved.get("version"),
            "deliveryState": "registered",
        }
