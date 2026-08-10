from __future__ import annotations

from typing import Any

from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("strict_data_center_support", pin_workspace=True)


@router.post(r"data-center/resolve")
def resolve(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "POST", "/api/v2/data-center-support/resolve", payload=request.body,
        idempotency_key=request.idempotency_key, refresh_business=False,
    )


@router.get(r"data-center/team-sync/stats")
def team_sync_stats(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    return compatibility.runtime.cloud_query("/api/v2/data-center-support/team-sync/stats")


@router.get(r"data-center/evidence-quality")
def evidence_quality(compatibility: Any, request: UiRequest, _: Any) -> list[dict[str, Any]]:
    return compatibility.runtime.cloud_query(
        "/api/v2/data-center-support/evidence-quality", query=request.query,
    )


@router.post(r"data-center/evidence-quality/([^/]+)/label")
def label_evidence(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "POST",
        f"/api/v2/data-center-support/evidence-quality/{match.group(1)}/label",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
