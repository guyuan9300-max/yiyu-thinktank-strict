from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, Query, status

from strict_common.ids import new_id

from ..repositories.project_materials import (
    GC07ProjectMaterialsRepository,
    ProjectMaterialsRepository,
)
from ..repository import CloudRepository, SessionIdentity


def register_gc07_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    """Expose only the approved GC-07 project and local-metadata surface."""
    domain = GC07ProjectMaterialsRepository(repository)

    @app.get("/api/v2/domain/project-materials/projects")
    def list_projects(
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.list_projects(identity)

    @app.get("/api/v2/domain/project-materials/projects/{project_id}")
    def project_detail(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.project_detail(identity, project_id=project_id)

    @app.post(
        "/api/v2/domain/project-materials/projects",
        status_code=status.HTTP_201_CREATED,
    )
    def create_project(
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.create_project(
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.put("/api/v2/domain/project-materials/projects/{project_id}")
    def update_project(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.update_project(
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/materials/register-metadata",
        status_code=status.HTTP_201_CREATED,
    )
    def register_material_metadata(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.register_local_material_metadata(
            identity,
            project_id=project_id,
            materials=payload.get("materials") or [],
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/glossary-attributes"
    )
    def official_fact_candidates(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        attribute_status: Annotated[str | None, Query(alias="status")] = None,
    ) -> dict[str, Any]:
        from ..repositories.gc15_official_website import official_fact_candidates

        return official_fact_candidates(
            repository,
            identity,
            project_id=project_id,
            status=attribute_status,
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/glossary-attributes/{attribute_id}/review"
    )
    def review_official_fact_candidate(
        project_id: str,
        attribute_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        from ..repositories.gc15_official_website import (
            review_official_fact_candidate,
        )

        return review_official_fact_candidate(
            repository,
            identity,
            project_id=project_id,
            fact_id=attribute_id,
            review_status=str(payload.get("reviewStatus") or ""),
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )


def register_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    domain = ProjectMaterialsRepository(repository)

    @app.get("/api/v2/domain/project-materials/projects")
    def list_projects(
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.list_projects(identity)

    @app.get("/api/v2/domain/project-materials/projects/{project_id}")
    def project_detail(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.project_detail(identity, project_id=project_id)

    @app.post(
        "/api/v2/domain/project-materials/projects",
        status_code=status.HTTP_201_CREATED,
    )
    def create_project(
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.create_project(
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.put("/api/v2/domain/project-materials/projects/{project_id}")
    def update_project(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.update_project(
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}/lifecycle"
    )
    def transition_project(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.transition_project(
            identity,
            project_id=project_id,
            target_state=str(payload.get("targetState") or ""),
            expected_version=int(payload.get("expectedVersion") or 0),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/delete-preview"
    )
    def delete_preview(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.delete_preview(identity, project_id=project_id)

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/knowledge-status"
    )
    def knowledge_status(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.knowledge_status(identity, project_id=project_id)

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/fact-bundle"
    )
    def fact_bundle(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
        lite: bool = False,
    ) -> dict[str, Any]:
        return domain.fact_bundle(
            identity,
            project_id=project_id,
            include_archived=include_archived,
            lite=lite,
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/duplicate-documents"
    )
    def duplicate_documents(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.duplicate_documents(identity, project_id=project_id)

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/duplicate-documents/resolve"
    )
    def resolve_duplicate_documents(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.resolve_duplicate_documents(
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/entities"
    )
    def entities(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        entity_type: Annotated[str | None, Query(alias="type")] = None,
        q: str = "",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return domain.derived_entities(
            identity,
            project_id=project_id,
            entity_type=entity_type,
            query=q,
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/entity-merge-candidates"
    )
    def entity_merge_candidates(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        return domain.entity_merge_candidates(
            identity,
            project_id=project_id,
            limit=limit,
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/glossary"
    )
    def glossary(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        q: str = "",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return domain.derived_glossary(
            identity,
            project_id=project_id,
            query=q,
            limit=limit,
            offset=offset,
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}/glossary",
        status_code=status.HTTP_201_CREATED,
    )
    def create_glossary_entry(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.create_glossary_entry(
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch(
        "/api/v2/domain/project-materials/glossary/{entry_id}"
    )
    def update_glossary_entry(
        entry_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.update_glossary_entry(
            identity,
            entry_id=entry_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete(
        "/api/v2/domain/project-materials/glossary/{entry_id}"
    )
    def delete_glossary_entry(
        entry_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.delete_glossary_entry(
            identity,
            entry_id=entry_id,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/domain/project-materials/entities/{entity_id}/verify"
    )
    def verify_entity(
        entity_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.verify_entity(
            identity,
            entity_id=entity_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/domain/project-materials/entities/{merged_id}/merge"
    )
    def merge_entity(
        merged_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.merge_entity(
            identity,
            merged_id=merged_id,
            surviving_entity_id=str(
                payload.get("survivingEntityId") or ""
            ),
            reason=str(payload.get("mergeReason") or ""),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/glossary-attributes"
    )
    def glossary_attributes(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        attribute_status: Annotated[str | None, Query(alias="status")] = None,
    ) -> dict[str, Any]:
        return domain.glossary_attributes(
            identity,
            project_id=project_id,
            status=attribute_status,
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/glossary-attributes/{attribute_id}/review"
    )
    def review_glossary_attribute(
        project_id: str,
        attribute_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.review_glossary_attribute(
            identity,
            project_id=project_id,
            attribute_id=attribute_id,
            review_status=str(payload.get("reviewStatus") or ""),
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/glossary-drift-alerts"
    )
    def glossary_drift_alerts(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        alert_status: Annotated[str, Query(alias="status")] = "pending",
    ) -> dict[str, Any]:
        return domain.glossary_drift_alerts(
            identity,
            project_id=project_id,
            status=alert_status,
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/glossary-drift-alerts/{alert_id}/review"
    )
    def review_glossary_drift(
        project_id: str,
        alert_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.review_glossary_drift(
            identity,
            project_id=project_id,
            alert_id=alert_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/contradictions"
    )
    def contradictions(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        review_status: Annotated[str, Query(alias="status")] = "pending",
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> dict[str, Any]:
        return domain.derived_contradictions(
            identity,
            project_id=project_id,
            status=review_status,
            limit=limit,
            offset=offset,
        )

    @app.post(
        "/api/v2/domain/project-materials/contradictions/"
        "{contradiction_id}/review"
    )
    def review_contradiction(
        contradiction_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.review_contradiction(
            identity,
            contradiction_id=contradiction_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/folder-recommendation"
    )
    def folder_recommendation(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.folder_recommendation_plan(
            identity,
            project_id=project_id,
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/auto-repair-preview"
    )
    def auto_repair_preview(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.auto_repair_preview(
            identity,
            project_id=project_id,
            document_ids=payload.get("documentIds") or [],
            limit=int(payload.get("limit") or 100),
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/auto-repair-queue"
    )
    def auto_repair_queue(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.queue_auto_repair(
            identity,
            project_id=project_id,
            document_ids=payload.get("documentIds") or [],
            include_human_required=bool(
                payload.get("includeHumanRequired")
            ),
            limit=int(payload.get("limit") or 100),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/materials/register-metadata",
        status_code=status.HTTP_201_CREATED,
    )
    def register_material_metadata(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.register_local_material_metadata(
            identity,
            project_id=project_id,
            materials=payload.get("materials") or [],
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/smart-import/publish",
        status_code=status.HTTP_201_CREATED,
    )
    def publish_smart_import(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.publish_smart_import(
            identity,
            project_id=project_id,
            title=str(payload.get("title") or "智能导入"),
            parsed=payload.get("parsed") or {},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/documents/{document_id}/reading-preview"
    )
    def document_reading_preview(
        project_id: str,
        document_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.document_reading_preview(
            identity,
            project_id=project_id,
            document_id=document_id,
        )

    @app.patch(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/documents/{document_id}/local-metadata"
    )
    def update_local_material_metadata(
        project_id: str,
        document_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.update_local_material_metadata(
            identity,
            project_id=project_id,
            document_id=document_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/documents/{document_id}/publish-local-summary"
    )
    def publish_local_material_summary(
        project_id: str,
        document_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.publish_local_material_summary(
            identity,
            project_id=project_id,
            document_id=document_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/documents/{document_id}"
    )
    def archive_document(
        project_id: str,
        document_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.archive_document(
            identity,
            project_id=project_id,
            document_id=document_id,
            expected_version=int(payload.get("expectedVersion") or 0),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/documents/{document_id}/text"
    )
    def document_text(
        document_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.document_text(identity, document_id=document_id)

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/link-import-runs"
    )
    def start_link_import(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.start_link_import(
            identity,
            project_id=project_id,
            url=str(payload.get("url") or ""),
            use_browser_cookies=bool(payload.get("useBrowserCookies")),
            cookie_browser=str(payload.get("cookieBrowser") or "firefox"),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/link-import-runs"
    )
    def link_import_runs(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        return domain.link_import_runs(
            identity,
            project_id=project_id,
            limit=limit,
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/link-import-runs/{run_id}"
    )
    def link_import_run(
        project_id: str,
        run_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.link_import_runs(
            identity,
            project_id=project_id,
            run_id=run_id,
            limit=1,
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/link-import-runs/{run_id}/cancel"
    )
    def cancel_link_import(
        project_id: str,
        run_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.cancel_link_import(
            identity,
            project_id=project_id,
            run_id=run_id,
            idempotency_key=idempotency_key or new_id(),
        )
