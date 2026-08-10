from __future__ import annotations

from typing import Any

from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("strict_system_governance", pin_workspace=True)


@router.post(r"settings/backup")
def create_backup(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    result = compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/system-governance/recovery-sets",
        payload={"retentionDays": int(request.body.get("retentionDays") or 30)},
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    return {
        **result,
        "backupPath": result.get("backupPath") or "strict-recovery://verified",
        "createdAt": result.get("createdAt"),
    }
