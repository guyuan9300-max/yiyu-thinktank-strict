from __future__ import annotations

from typing import Any, Mapping

from strict_common.ids import sha256_text

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .platform_configurations import PlatformConfigurationRepository


class MobileDeviceRepository:
    """Register a mobile push endpoint without placing its token in SQLite."""

    def __init__(self, repository: CloudRepository):
        self.configurations = PlatformConfigurationRepository(repository)

    def register(self, identity: SessionIdentity, *, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        token = str(payload.get("pushToken") or "").strip()
        device_id = str(payload.get("deviceId") or "").strip()
        if not token or not device_id:
            raise RepositoryError(422, "mobile_push_identity_required", "缺少移动设备或推送凭据")
        current = self.configurations.read_exact(identity, configuration_kind="mobile_push_device", scope_kind="personal", defaults={})
        result = self.configurations.upsert(
            identity,
            configuration_kind="mobile_push_device",
            scope_kind="personal",
            provider=str(payload.get("provider") or "expo_push"),
            public_config={"deviceId": device_id, "platform": str(payload.get("platform") or "android"), "pushTokenFingerprint": sha256_text(token)},
            expected_version=int(current.get("version") or 0),
            idempotency_key=idempotency_key,
            secret_bundle={"pushToken": token},
            secret_action="replace",
        )
        return {"state": "registered", "version": result["version"], "deviceId": device_id, "pushTokenFingerprint": sha256_text(token)}
