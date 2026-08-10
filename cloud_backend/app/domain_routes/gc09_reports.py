from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, status

from strict_common.ids import new_id

from ..repositories import gc09_reports
from ..repository import CloudRepository, SessionIdentity


def register_gc09_report_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]
    Idempotency = Annotated[str | None, Header(alias="Idempotency-Key")]

    @app.get("/api/v2/workbench/projects/{project_id}/reports")
    def list_reports(project_id: str, identity: Identity) -> list[dict[str, Any]]:
        return gc09_reports.list_reports(repository, identity, project_id=project_id)

    @app.post("/api/v2/workbench/reports", status_code=status.HTTP_201_CREATED)
    def create_report(payload: Annotated[dict[str, Any], Body()], identity: Identity, idempotency_key: Idempotency = None) -> dict[str, Any]:
        return gc09_reports.create_report(repository, identity, payload=payload, idempotency_key=idempotency_key or new_id())

    @app.get("/api/v2/workbench/reports/{report_id}")
    def report_detail(report_id: str, identity: Identity) -> dict[str, Any]:
        return gc09_reports.report_detail(repository, identity, report_id=report_id)

    @app.get("/api/v2/workbench/reports/{report_id}/versions")
    def report_versions(report_id: str, identity: Identity) -> list[dict[str, Any]]:
        return gc09_reports.report_versions(repository, identity, report_id=report_id)

    @app.patch("/api/v2/workbench/reports/{report_id}")
    def update_report(report_id: str, payload: Annotated[dict[str, Any], Body()], identity: Identity, idempotency_key: Idempotency = None) -> dict[str, Any]:
        return gc09_reports.update_report(repository, identity, report_id=report_id, payload=payload, idempotency_key=idempotency_key or new_id())

    @app.post("/api/v2/workbench/reports/{report_id}/restore")
    def restore_report(report_id: str, payload: Annotated[dict[str, Any], Body()], identity: Identity, idempotency_key: Idempotency = None) -> dict[str, Any]:
        try:
            version = int(payload.get("restoreVersion") or payload.get("restore_version") or 0)
        except (TypeError, ValueError):
            version = 0
        return gc09_reports.update_report(repository, identity, report_id=report_id, payload=payload, idempotency_key=idempotency_key or new_id(), restored_from_version=version)

    @app.post("/api/v2/workbench/reports/{report_id}/export-grants")
    def issue_export_grant(
        report_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return gc09_reports.issue_export_grant(
            repository,
            identity,
            report_id=report_id,
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )
