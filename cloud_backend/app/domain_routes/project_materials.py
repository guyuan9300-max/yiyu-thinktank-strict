from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, Query, status

from strict_common.ids import new_id

from ..repositories.project_materials import (
    GC07ProjectMaterialsRepository,
)
from ..repository import CloudRepository, SessionIdentity


def register_gc07_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    """Expose only the approved GC-07 project and local-metadata surface."""
    domain = GC07ProjectMaterialsRepository(repository)

    from ..repositories.gc12_intelligence import GC12IntelligenceRepository

    intelligence = GC12IntelligenceRepository(repository)

    from ..repositories.knowledge_governance_88 import KnowledgeGovernance88Repository

    governance = KnowledgeGovernance88Repository(repository)

    @app.get("/api/v2/domain/project-materials/projects")
    def list_projects(
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.list_projects(identity)

    @app.get("/api/v2/domain/project-materials/intelligence")
    def list_intelligence(
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        content_kind: Annotated[str | None, Query(alias="contentKind")] = None,
        work_object_id: Annotated[str | None, Query(alias="workObjectId")] = None,
        page: int = 1,
        page_size: Annotated[int, Query(alias="pageSize")] = 20,
    ) -> dict[str, Any]:
        return intelligence.list_items(
            identity,
            {
                "contentKind": content_kind,
                "workObjectId": work_object_id,
                "page": page,
                "pageSize": page_size,
            },
        )

    @app.get("/api/v2/domain/project-materials/intelligence/items/{intelligence_id}")
    def get_intelligence_item(
        intelligence_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return intelligence.get_item(identity, intelligence_id=intelligence_id)

    @app.post(
        "/api/v2/domain/project-materials/intelligence/{intelligence_id}/attention"
    )
    def set_intelligence_attention(
        intelligence_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return intelligence.set_attention(
            identity,
            intelligence_id=intelligence_id,
            action=str(payload.get("action") or ""),
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/domain/project-materials/intelligence/refresh-runs")
    def list_intelligence_refresh_runs(
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        content_kind: Annotated[str | None, Query(alias="contentKind")] = None,
        scope_id: Annotated[str | None, Query(alias="scopeId")] = None,
        active_only: Annotated[str | None, Query(alias="activeOnly")] = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return intelligence.list_refresh_runs(
            identity,
            {
                "contentKind": content_kind,
                "scopeId": scope_id,
                "activeOnly": active_only,
                "limit": limit,
            },
        )

    @app.get("/api/v2/domain/project-materials/intelligence/strategy-extract")
    def intelligence_strategy_extract(
        client_id: Annotated[str, Query(alias="clientId")],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return intelligence.strategy_extract(identity, project_id=client_id)

    @app.post("/api/v2/domain/project-materials/intelligence/external-capture")
    def commit_intelligence_external_capture(
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return intelligence.commit_external_capture(
            identity,
            project_id=str(payload.get("projectId") or ""),
            capture_id=str(payload.get("captureId") or idempotency_key or new_id()),
            content_kind=str(payload.get("contentKind") or "public_opinion"),
            capture_kind=str(payload.get("captureKind") or "manual_intelligence"),
            items=[item for item in payload.get("items") or [] if isinstance(item, dict)],
            research_receipt=(
                dict(payload.get("researchReceipt") or {})
                if isinstance(payload.get("researchReceipt"), dict)
                else {}
            ),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/domain/project-materials/intelligence/focus-directives")
    def list_intelligence_focus_directives(
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> list[dict[str, Any]]:
        return intelligence.list_focus_directives(identity)

    @app.put("/api/v2/domain/project-materials/intelligence/focus-directives")
    def save_intelligence_focus_directive(
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return intelligence.upsert_rule(
            identity,
            rule_kind="focus",
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/domain/project-materials/intelligence/refresh-cycle-settings")
    def get_intelligence_refresh_cycle_settings(
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return intelligence.refresh_cycle_settings(identity)

    @app.put("/api/v2/domain/project-materials/intelligence/refresh-cycle-settings")
    def save_intelligence_refresh_cycle_settings(
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return intelligence.upsert_rule(
            identity,
            rule_kind="cycle",
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/domain/project-materials/intelligence/verification-rules")
    def list_intelligence_verification_rules(
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> list[dict[str, Any]]:
        return intelligence.list_verification_rules(identity)

    @app.put("/api/v2/domain/project-materials/intelligence/verification-rules")
    def save_intelligence_verification_rule(
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return intelligence.upsert_rule(
            identity,
            rule_kind="verification",
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/domain/project-materials/intelligence/items/{intelligence_id}/answers"
    )
    def record_intelligence_answer(
        intelligence_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return intelligence.record_item_answer(
            identity,
            intelligence_id=intelligence_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

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

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}/mobile-recording-summary",
        status_code=status.HTTP_201_CREATED,
    )
    def publish_mobile_recording_summary(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return domain.publish_mobile_recording_summary(
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/documents/{document_id}/reading-preview"
    )
    def local_material_metadata(
        project_id: str,
        document_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
    ) -> dict[str, Any]:
        return domain.local_material_metadata(
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
    def delete_local_material_metadata(
        project_id: str,
        document_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key"),
        ] = None,
    ) -> dict[str, Any]:
        return domain.delete_local_material_metadata(
            identity,
            project_id=project_id,
            document_id=document_id,
            expected_version=int(payload.get("expectedVersion") or 0),
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

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/entities"
    )
    def project_entities(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        entity_type: Annotated[str | None, Query(alias="type")] = None,
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        return domain.list_entities(
            identity,
            project_id=project_id,
            entity_type=str(entity_type or ""),
            query=str(q or ""),
            limit=limit,
            offset=offset,
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/entity-merge-candidates"
    )
    def project_entity_merge_candidates(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        limit: int = 50,
    ) -> dict[str, Any]:
        return domain.entity_merge_candidates(
            identity,
            project_id=project_id,
            limit=limit,
        )

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/glossary"
    )
    def project_glossary(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        q: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        return domain.list_glossary(
            identity,
            project_id=project_id,
            query=str(q or ""),
            limit=limit,
            offset=offset,
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}/glossary",
        status_code=status.HTTP_201_CREATED,
    )
    def create_project_glossary_entry(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return domain.create_glossary_entry(
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch("/api/v2/domain/project-materials/glossary/{entry_id}")
    def update_project_glossary_entry(
        entry_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return domain.update_glossary_entry(
            identity,
            entry_id=entry_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete("/api/v2/domain/project-materials/glossary/{entry_id}")
    def delete_project_glossary_entry(
        entry_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return domain.delete_glossary_entry(
            identity,
            entry_id=entry_id,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/domain/project-materials/entities/{entity_id}/verify")
    def verify_project_entity(
        entity_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return domain.verify_entity(
            identity,
            entity_id=entity_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/domain/project-materials/entities/{merged_id}/merge")
    def merge_project_entity(
        merged_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return domain.merge_entity(
            identity,
            merged_id=merged_id,
            surviving_id=str(payload.get("survivingEntityId") or ""),
            reason=str(payload.get("mergeReason") or ""),
            idempotency_key=idempotency_key or new_id(),
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

    @app.get(
        "/api/v2/domain/project-materials/projects/{project_id}/governance-decisions"
    )
    def list_knowledge_governance_decisions(
        project_id: str,
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        decision_kind: Annotated[str | None, Query(alias="decisionKind")] = None,
    ) -> dict[str, Any]:
        return governance.list_decisions(
            identity, project_id=project_id, decision_kind=decision_kind
        )

    @app.post(
        "/api/v2/domain/project-materials/projects/{project_id}"
        "/governance-decisions/{derived_id}"
    )
    def record_knowledge_governance_decision(
        project_id: str,
        derived_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Annotated[SessionIdentity, Depends(identity_dependency)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return governance.record_decision(
            identity,
            project_id=project_id,
            derived_id=derived_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )


# The pre-blueprint register_routes implementation was frozen under
# legacy_frozen/v8_pre_gc02.  This module exposes only register_gc07_routes.
