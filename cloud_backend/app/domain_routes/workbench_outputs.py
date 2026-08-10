from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, status

from strict_common.ids import new_id

from ..repositories import workbench_outputs as domain_repository
from ..repositories import agent_skills as skill_repository
from ..repositories import strategic_support
from ..repository import CloudRepository, RepositoryError, SessionIdentity


def register_strategic_profile_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    """Expose only the approved GC-12 customer-profile read surface."""
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]

    @app.get("/api/v2/workbench/projects/{project_id}/narrative")
    def project_narrative(project_id: str, identity: Identity) -> dict[str, Any]:
        return domain_repository.project_narrative(
            repository,
            identity,
            project_id=project_id,
        )

    @app.get("/api/v2/workbench/projects/{project_id}/memory-manifest")
    def project_memory_manifest(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        from ..repositories.memory_sync import get_memory_manifest

        return get_memory_manifest(
            repository,
            identity,
            project_id=project_id,
        )

    @app.put("/api/v2/workbench/projects/{project_id}/memory-manifest")
    def synchronize_project_memory_manifest(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        from ..repositories.memory_sync import put_memory_manifest

        return put_memory_manifest(
            repository,
            identity,
            project_id=project_id,
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/official-website")
    def project_official_website(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        from ..repositories.gc15_official_website import official_website_status

        return official_website_status(
            repository,
            identity,
            project_id=project_id,
        )

    @app.post("/api/v2/workbench/projects/{project_id}/official-website/captures")
    def capture_project_official_website(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        from ..repositories.gc15_official_website import capture_official_website

        return capture_official_website(
            repository,
            identity,
            project_id=project_id,
            pages=(payload or {}).get("pages") or [],
            fact_candidates=(payload or {}).get("factCandidates") or [],
            research_progress=(payload or {}).get("researchProgress") or {},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workbench/projects/{project_id}/official-website/auto-verify")
    def auto_verify_project_official_facts(
        project_id: str,
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        from ..repositories.gc15_official_website import auto_verify_official_facts

        return auto_verify_official_facts(
            repository,
            identity,
            project_id=project_id,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workbench/projects/{project_id}/strategic-profile/rebuild")
    def rebuild_strategic_profile(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        from ..repositories.gc14_strategic_profile import rebuild_strategic_profile

        return rebuild_strategic_profile(
            repository,
            identity,
            project_id=project_id,
            idempotency_key=idempotency_key or new_id(),
            prepared_profile=(payload or {}).get("profile"),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/narrative-clarifications")
    def strategic_profile_clarifications(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        from ..repositories.gc12_corrections import (
            list_strategic_profile_clarifications,
        )

        return list_strategic_profile_clarifications(
            repository,
            identity,
            project_id=project_id,
        )

    @app.post("/api/v2/workbench/projects/{project_id}/narrative-clarifications")
    def clarify_strategic_profile(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        from ..repositories.gc12_corrections import (
            create_strategic_profile_clarification,
        )

        return create_strategic_profile_clarification(
            repository,
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/strategic-thoughts")
    def list_strategic_thoughts(
        identity: Identity,
        clientId: str = "",
        includeDismissed: bool = False,
        includeDeleted: bool = False,
        limit: int = 24,
    ) -> dict[str, Any]:
        from ..repositories.strategic_thoughts import list_thoughts

        return list_thoughts(
            repository,
            identity,
            client_id=clientId,
            include_dismissed=includeDismissed,
            include_deleted=includeDeleted,
            limit=limit,
        )

    @app.post("/api/v2/workbench/strategic-thoughts/refresh")
    def refresh_strategic_thoughts(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        from ..repositories.strategic_thoughts import refresh_thoughts

        return refresh_thoughts(
            repository,
            identity,
            client_id=str((payload or {}).get("clientId") or ""),
            limit=int((payload or {}).get("limit") or 8),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workbench/strategic-thoughts/{thought_id}/state")
    def change_strategic_thought_state(
        thought_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        from ..repositories.strategic_thoughts import (
            resolve_client_id,
            update_thought_state,
        )

        client_id = str((payload or {}).get("clientId") or "") or resolve_client_id(
            repository, identity, thought_id=thought_id
        )
        return update_thought_state(
            repository,
            identity,
            thought_id=thought_id,
            client_id=client_id,
            action=str((payload or {}).get("action") or ""),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workbench/strategic-thoughts/{thought_id}/review")
    def review_strategic_thought(
        thought_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        from ..repositories.strategic_thoughts import resolve_client_id, review_thought

        client_id = str((payload or {}).get("clientId") or "") or resolve_client_id(
            repository, identity, thought_id=thought_id
        )
        return review_thought(
            repository,
            identity,
            thought_id=thought_id,
            client_id=client_id,
            action=str((payload or {}).get("action") or ""),
            note=str((payload or {}).get("note") or ""),
            task_id=str((payload or {}).get("taskId") or "") or None,
            idempotency_key=idempotency_key or new_id(),
        )

    def writing_style_dto(item: dict[str, Any]) -> dict[str, Any]:
        instructions = list(item.get("instructions") or [])
        return {
            "id": item["skillId"],
            "name": item.get("shortName") or "",
            "description": item.get("description") or "",
            "distilledMd": instructions[0] if instructions else "",
            "isBuiltin": False,
            "sortOrder": 0,
            "version": int(item.get("version") or 1),
            "createdAt": item.get("createdAt"),
            "updatedAt": item.get("updatedAt"),
        }

    def writing_style_draft(
        payload: dict[str, Any],
        current: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        current = current or {}
        current_instructions = list(current.get("instructions") or [])
        distilled = str(
            payload.get("distilledMd")
            or (current_instructions[0] if current_instructions else "")
        ).strip()
        return {
            "skillType": "writing_style",
            "shortName": str(payload.get("name") or current.get("shortName") or "").strip(),
            "description": str(payload.get("description") or current.get("description") or "").strip(),
            "instructions": [distilled] if distilled else [],
            "outputTemplate": None,
            "allowedToolIds": [],
            "visibility": "private",
            "granteeMembershipIds": [],
            "agentKinds": ["project_workspace"],
        }

    # Writing style is a typed private automation rule.  It stays separate in
    # the retained UI while sharing the same 88-table Skill authority, grants,
    # CAS and audit/outbox lane; no second knowledge library is created.
    @app.get("/api/v2/workbench/libraries/writing_skill")
    def list_writing_skills(identity: Identity) -> list[dict[str, Any]]:
        result = skill_repository.list_agent_skills(
            repository,
            identity,
            agent_kind="project_workspace",
            enabled_only=True,
            skill_type="writing_style",
        )
        return [writing_style_dto(item) for item in result["items"]]

    @app.post(
        "/api/v2/workbench/libraries/writing_skill",
        status_code=status.HTTP_201_CREATED,
    )
    def create_writing_skill(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        saved = skill_repository.publish_agent_skill(
            repository,
            identity,
            payload=writing_style_draft(payload),
            idempotency_key=idempotency_key or new_id(),
        )
        return writing_style_dto(saved)

    @app.put("/api/v2/workbench/libraries/writing_skill/{item_id}")
    def update_writing_skill(
        item_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        current = skill_repository.get_agent_skill(
            repository,
            identity,
            skill_id=item_id,
        )
        if current.get("skillType") != "writing_style":
            raise RepositoryError(404, "writing_style_missing", "写作风格不存在")
        saved = skill_repository.update_agent_skill(
            repository,
            identity,
            skill_id=item_id,
            payload={
                **writing_style_draft(payload, current),
                "expectedVersion": int(current.get("version") or 1),
            },
            idempotency_key=idempotency_key or new_id(),
        )
        return writing_style_dto(saved)

    @app.delete("/api/v2/workbench/libraries/writing_skill/{item_id}")
    def delete_writing_skill(
        item_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        del payload
        current = skill_repository.get_agent_skill(
            repository,
            identity,
            skill_id=item_id,
        )
        if current.get("skillType") != "writing_style":
            raise RepositoryError(404, "writing_style_missing", "写作风格不存在")
        skill_repository.set_agent_skill_enabled(
            repository,
            identity,
            skill_id=item_id,
            enabled=False,
            expected_version=int(current.get("version") or 1),
            idempotency_key=idempotency_key or new_id(),
        )
        return {"deleted": True, "id": item_id}


def register_strategic_support_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    """Register only the 88-table support surface consumed by strategy UI."""

    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]
    Idempotency = Annotated[str | None, Header(alias="Idempotency-Key")]

    @app.get("/api/v2/workbench/projects/{project_id}/workspace")
    def project_workspace(project_id: str, identity: Identity) -> dict[str, Any]:
        return domain_repository.project_workspace(repository, identity, project_id=project_id)

    @app.get("/api/v2/workbench/projects/{project_id}/knowledge-status")
    def project_knowledge_status(project_id: str, identity: Identity) -> dict[str, Any]:
        return strategic_support.knowledge_status(repository, identity, project_id=project_id)

    @app.get("/api/v2/workbench/projects/{project_id}/insights")
    def project_insights(project_id: str, identity: Identity) -> dict[str, list[dict[str, Any]]]:
        return strategic_support.project_insights(repository, identity, project_id=project_id)

    @app.get("/api/v2/workbench/projects/{project_id}/texts")
    def project_text_items(project_id: str, identity: Identity) -> dict[str, dict[str, Any]]:
        return strategic_support.project_text_items(repository, identity, project_id=project_id)

    @app.put("/api/v2/workbench/projects/{project_id}/texts/{key:path}")
    def save_project_text(
        project_id: str,
        key: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.save_project_text(
            repository,
            identity,
            project_id=project_id,
            key=key,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete("/api/v2/workbench/projects/{project_id}/texts/{key:path}")
    def archive_project_text(
        project_id: str,
        key: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.archive_project_text(
            repository,
            identity,
            project_id=project_id,
            key=key,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workbench/analysis-jobs")
    def register_analysis_job(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.register_analysis_job(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/analysis-jobs/{job_id}")
    def analysis_job_detail(job_id: str, identity: Identity) -> dict[str, Any]:
        return strategic_support.analysis_job_detail(repository, identity, job_id=job_id)

    @app.get("/api/v2/workbench/analysis-jobs/{job_id}/stages")
    def analysis_job_stages(job_id: str, identity: Identity) -> list[dict[str, Any]]:
        return strategic_support.analysis_job_stages(repository, identity, job_id=job_id)

    @app.get("/api/v2/workbench/projects/{project_id}/suggestion-log")
    def suggestion_log(project_id: str, identity: Identity) -> dict[str, Any]:
        return strategic_support.suggestion_log(repository, identity, project_id=project_id)

    @app.post("/api/v2/workbench/projects/{project_id}/suggestion-log")
    def write_suggestion_log(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.write_suggestion_log(
            repository,
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete("/api/v2/workbench/projects/{project_id}/suggestion-log/{fingerprint}")
    def archive_suggestion_log(
        project_id: str,
        fingerprint: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.write_suggestion_log(
            repository,
            identity,
            project_id=project_id,
            fingerprint=fingerprint,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
            archive=True,
        )

    @app.post("/api/v2/workbench/projects/{project_id}/todos/{task_id}/{action}")
    def task_todo_action(
        project_id: str,
        task_id: str,
        action: str,
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.task_todo_action(
            repository,
            identity,
            project_id=project_id,
            task_id=task_id,
            action=action,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/meeting-action-items")
    def meeting_action_items(project_id: str, identity: Identity) -> dict[str, Any]:
        return strategic_support.meeting_action_items(repository, identity, project_id=project_id)


def register_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]
    Idempotency = Annotated[str | None, Header(alias="Idempotency-Key")]

    @app.get("/api/v2/workbench/projects/{project_id}/workspace")
    def project_workspace(project_id: str, identity: Identity) -> dict[str, Any]:
        return domain_repository.project_workspace(
            repository,
            identity,
            project_id=project_id,
        )

    @app.get("/api/v2/workbench/projects/{project_id}/knowledge-status")
    def project_knowledge_status(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return strategic_support.knowledge_status(
            repository,
            identity,
            project_id=project_id,
        )

    @app.get("/api/v2/workbench/projects/{project_id}/analysis-status")
    def project_analysis_status(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return domain_repository.analysis_status(
            repository,
            identity,
            project_id=project_id,
        )

    @app.post(
        "/api/v2/workbench/projects/{project_id}/analysis-runs/{run_id}/cancel"
    )
    def cancel_project_analysis_run(
        project_id: str,
        run_id: str,
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.cancel_analysis_run(
            repository,
            identity,
            project_id=project_id,
            run_id=run_id,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/answers/{answer_id}")
    def answer_detail(answer_id: str, identity: Identity) -> dict[str, Any]:
        return domain_repository.answer_detail(
            repository,
            identity,
            answer_id=answer_id,
        )

    @app.delete("/api/v2/workbench/answers/{answer_id}")
    def archive_answer(
        answer_id: str,
        identity: Identity,
        payload: Annotated[dict[str, Any], Body()],
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.archive_answer(
            repository,
            identity,
            answer_id=answer_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/workbench/favorites",
        status_code=status.HTTP_201_CREATED,
    )
    def create_favorite(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.create_favorite(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete("/api/v2/workbench/favorites/{favorite_id}")
    def delete_favorite(
        favorite_id: str,
        identity: Identity,
        payload: Annotated[dict[str, Any], Body()],
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.delete_favorite(
            repository,
            identity,
            favorite_id=favorite_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/dna")
    def list_dna_modules(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return strategic_support.list_dna_modules(
            repository,
            identity,
            project_id=project_id,
        )

    @app.put("/api/v2/workbench/projects/{project_id}/dna/{module_key}")
    def save_dna_module(
        project_id: str,
        module_key: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.save_dna_module(
            repository,
            identity,
            project_id=project_id,
            module_key=module_key,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/narrative")
    def project_narrative(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return domain_repository.project_narrative(
            repository,
            identity,
            project_id=project_id,
        )

    @app.get("/api/v2/workbench/projects/{project_id}/reports")
    def list_reports(project_id: str, identity: Identity) -> list[dict[str, Any]]:
        return domain_repository.list_reports(
            repository,
            identity,
            project_id=project_id,
        )

    @app.post("/api/v2/workbench/reports", status_code=status.HTTP_201_CREATED)
    def create_report(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.create_report(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/reports/{report_id}")
    def report_detail(report_id: str, identity: Identity) -> dict[str, Any]:
        return domain_repository.report_detail(
            repository,
            identity,
            report_id=report_id,
        )

    @app.get("/api/v2/workbench/reports/{report_id}/versions")
    def report_versions(
        report_id: str,
        identity: Identity,
    ) -> list[dict[str, Any]]:
        return domain_repository.report_versions(
            repository,
            identity,
            report_id=report_id,
        )

    @app.patch("/api/v2/workbench/reports/{report_id}")
    def update_report(
        report_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.update_report(
            repository,
            identity,
            report_id=report_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workbench/reports/{report_id}/restore")
    def restore_report(
        report_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        try:
            target_version = int(
                payload.get("version")
                or payload.get("restoreVersion")
                or payload.get("restore_version")
            )
        except (TypeError, ValueError):
            target_version = 0
        return domain_repository.update_report(
            repository,
            identity,
            report_id=report_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
            restored_from_version=target_version,
        )

    @app.get("/api/v2/workbench/dashboard")
    def dashboard(identity: Identity) -> dict[str, Any]:
        return domain_repository.dashboard(repository, identity)

    @app.get("/api/v2/workbench/digital-assets")
    def digital_assets(identity: Identity) -> dict[str, Any]:
        return domain_repository.digital_assets(repository, identity)

    @app.get("/api/v2/workbench/projects/{project_id}/digital-assets")
    def project_digital_assets(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return domain_repository.digital_assets(
            repository,
            identity,
            project_id=project_id,
        )

    @app.get("/api/v2/workbench/organization-dna")
    def organization_dna(identity: Identity) -> dict[str, Any]:
        return domain_repository.organization_dna(repository, identity)

    @app.get("/api/v2/workbench/libraries/{kind}")
    def list_library(kind: str, identity: Identity) -> list[dict[str, Any]]:
        return domain_repository.list_library(
            repository,
            identity,
            kind=kind,
        )

    @app.get("/api/v2/workbench/libraries/{kind}/{item_id}")
    def library_item(
        kind: str,
        item_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return domain_repository.library_item(
            repository,
            identity,
            kind=kind,
            item_id=item_id,
        )

    @app.post(
        "/api/v2/workbench/libraries/{kind}",
        status_code=status.HTTP_201_CREATED,
    )
    def create_library_item(
        kind: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.save_library_item(
            repository,
            identity,
            kind=kind,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.put("/api/v2/workbench/libraries/{kind}/{item_id}")
    def update_library_item(
        kind: str,
        item_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.save_library_item(
            repository,
            identity,
            kind=kind,
            item_id=item_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete("/api/v2/workbench/libraries/{kind}/{item_id}")
    def delete_library_item(
        kind: str,
        item_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.delete_library_item(
            repository,
            identity,
            kind=kind,
            item_id=item_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/texts")
    def project_text_items(
        project_id: str,
        identity: Identity,
    ) -> dict[str, dict[str, Any]]:
        return strategic_support.project_text_items(
            repository,
            identity,
            project_id=project_id,
        )

    @app.put("/api/v2/workbench/projects/{project_id}/texts/{key:path}")
    def save_project_text(
        project_id: str,
        key: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.save_project_text(
            repository,
            identity,
            project_id=project_id,
            key=key,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete("/api/v2/workbench/projects/{project_id}/texts/{key:path}")
    def archive_project_text(
        project_id: str,
        key: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.archive_project_text(
            repository,
            identity,
            project_id=project_id,
            key=key,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workbench/analysis-jobs")
    def register_analysis_job(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.register_analysis_job(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/analysis-jobs/{job_id}")
    def analysis_job_detail(job_id: str, identity: Identity) -> dict[str, Any]:
        return strategic_support.analysis_job_detail(
            repository,
            identity,
            job_id=job_id,
        )

    @app.get("/api/v2/workbench/analysis-jobs/{job_id}/stages")
    def analysis_job_stages(
        job_id: str,
        identity: Identity,
    ) -> list[dict[str, Any]]:
        return strategic_support.analysis_job_stages(
            repository,
            identity,
            job_id=job_id,
        )

    @app.post(
        "/api/v2/workbench/projects/{project_id}/context-refresh-events"
    )
    def register_context_refresh(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.register_context_refresh(
            repository,
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/goals")
    def list_project_goals(
        project_id: str,
        identity: Identity,
    ) -> list[dict[str, Any]]:
        return strategic_support.list_project_goals(
            repository,
            identity,
            project_id=project_id,
        )

    @app.post("/api/v2/workbench/projects/{project_id}/goals")
    def create_project_goal(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.create_project_goal(
            repository,
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/structure")
    def project_structure(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return strategic_support.project_structure(
            repository,
            identity,
            project_id=project_id,
        )

    @app.post("/api/v2/workbench/projects/{project_id}/structure/{item_kind}")
    def create_project_structure_item(
        project_id: str,
        item_kind: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.write_project_structure_item(
            repository,
            identity,
            project_id=project_id,
            item_kind=item_kind,
            item_id=None,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch(
        "/api/v2/workbench/projects/{project_id}/structure/{item_kind}/{item_id}"
    )
    def update_project_structure_item(
        project_id: str,
        item_kind: str,
        item_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.write_project_structure_item(
            repository,
            identity,
            project_id=project_id,
            item_kind=item_kind,
            item_id=item_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete(
        "/api/v2/workbench/projects/{project_id}/structure/{item_kind}/{item_id}"
    )
    def archive_project_structure_item(
        project_id: str,
        item_kind: str,
        item_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.write_project_structure_item(
            repository,
            identity,
            project_id=project_id,
            item_kind=item_kind,
            item_id=item_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
            archive=True,
        )

    @app.get(
        "/api/v2/workbench/projects/{project_id}/narrative-clarifications"
    )
    def narrative_clarifications(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return domain_repository.narrative_clarifications(
            repository,
            identity,
            project_id=project_id,
        )

    @app.post(
        "/api/v2/workbench/projects/{project_id}/narrative-clarifications"
    )
    def create_narrative_clarification(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.create_narrative_clarification(
            repository,
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/suggestion-log")
    def suggestion_log(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return strategic_support.suggestion_log(
            repository,
            identity,
            project_id=project_id,
        )

    @app.post("/api/v2/workbench/projects/{project_id}/suggestion-log")
    def write_suggestion_log(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.write_suggestion_log(
            repository,
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete(
        "/api/v2/workbench/projects/{project_id}/suggestion-log/{fingerprint}"
    )
    def archive_suggestion_log(
        project_id: str,
        fingerprint: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return strategic_support.write_suggestion_log(
            repository,
            identity,
            project_id=project_id,
            fingerprint=fingerprint,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
            archive=True,
        )

    @app.get("/api/v2/workbench/value-validation-sessions")
    def value_validation_sessions(
        identity: Identity,
        project_id: str | None = None,
        limit: int = 20,
    ) -> Any:
        return domain_repository.value_validation_sessions(
            repository,
            identity,
            project_id=project_id,
            limit=limit,
        )

    @app.post("/api/v2/workbench/value-validation-sessions")
    def create_value_validation_session(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.create_value_validation_session(
            repository,
            identity,
            project_id=str(payload.get("projectId") or ""),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/value-validation-sessions/{session_id}")
    def value_validation_session(
        session_id: str,
        identity: Identity,
    ) -> Any:
        return domain_repository.value_validation_sessions(
            repository,
            identity,
            session_id=session_id,
        )

    @app.post(
        "/api/v2/workbench/value-validation-sessions/{session_id}/complete-question"
    )
    def complete_value_validation_question(
        session_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.update_value_validation_session(
            repository,
            identity,
            session_id=session_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
            finish=False,
        )

    @app.post(
        "/api/v2/workbench/value-validation-sessions/{session_id}/finish"
    )
    def finish_value_validation_session(
        session_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.update_value_validation_session(
            repository,
            identity,
            session_id=session_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
            finish=True,
        )

    @app.get("/api/v2/workbench/projects/{project_id}/insights")
    def project_insights(
        project_id: str,
        identity: Identity,
    ) -> dict[str, list[dict[str, Any]]]:
        return strategic_support.project_insights(
            repository,
            identity,
            project_id=project_id,
        )

    @app.get("/api/v2/workbench/retrieval-shadow-runs")
    def retrieval_shadow_runs(
        identity: Identity,
        projectId: str | None = None,
        limit: int = 60,
    ) -> list[dict[str, Any]]:
        return domain_repository.retrieval_shadow_runs(
            repository,
            identity,
            project_id=projectId,
            limit=limit,
        )

    @app.get("/api/v2/workbench/retrieval-shadow-summary")
    def retrieval_shadow_summary(
        identity: Identity,
        projectId: str | None = None,
    ) -> dict[str, Any]:
        return domain_repository.retrieval_shadow_summary(
            repository,
            identity,
            project_id=projectId,
        )

    @app.get("/api/v2/workbench/report-runs/{report_id}")
    def report_run(report_id: str, identity: Identity) -> dict[str, Any]:
        return domain_repository.report_run(
            repository,
            identity,
            report_id=report_id,
        )

    @app.post("/api/v2/workbench/answers/{answer_id}/actions/{action_type}")
    def answer_task_action(
        answer_id: str,
        action_type: str,
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.answer_task_action(
            repository,
            identity,
            answer_id=answer_id,
            action_type=action_type,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workbench/projects/{project_id}/todos/{task_id}/{action}")
    def task_todo_action(
        project_id: str,
        task_id: str,
        action: str,
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.task_todo_action(
            repository,
            identity,
            project_id=project_id,
            task_id=task_id,
            action=action,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/retrieval-settings")
    def retrieval_settings(identity: Identity) -> dict[str, Any]:
        return domain_repository.retrieval_settings(repository, identity)

    @app.post("/api/v2/workbench/retrieval-settings")
    def save_retrieval_settings(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.save_retrieval_settings(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/proposal-drafts")
    def list_proposal_drafts(
        project_id: str,
        identity: Identity,
    ) -> list[dict[str, Any]]:
        return domain_repository.list_proposal_drafts(
            repository,
            identity,
            project_id=project_id,
        )

    @app.post("/api/v2/workbench/projects/{project_id}/proposal-drafts")
    def create_proposal_draft(
        project_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.create_proposal_draft(
            repository,
            identity,
            project_id=project_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/workbench/projects/{project_id}/meetings/"
        "{meeting_id}/proposal-drafts/{phase}"
    )
    def create_meeting_proposal_draft(
        project_id: str,
        meeting_id: str,
        phase: str,
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        meeting_phase = {
            "prepare": "meeting_prep",
            "follow-up": "meeting_followup",
        }.get(phase)
        if meeting_phase is None:
            raise RepositoryError(422, "meeting_proposal_phase_invalid", "会议提案阶段无效")
        return domain_repository.create_proposal_draft(
            repository,
            identity,
            project_id=project_id,
            payload={},
            idempotency_key=idempotency_key or new_id(),
            meeting_id=meeting_id,
            meeting_phase=meeting_phase,
        )

    @app.get("/api/v2/workbench/projects/{project_id}/meeting-action-items")
    def meeting_action_items(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return strategic_support.meeting_action_items(
            repository,
            identity,
            project_id=project_id,
        )

    @app.get("/api/v2/workbench/answer-value-reviews")
    def answer_value_reviews(
        identity: Identity,
        projectId: str | None = None,
        limit: int = 120,
    ) -> list[dict[str, Any]]:
        return domain_repository.list_answer_value_reviews(
            repository,
            identity,
            project_id=projectId,
            limit=limit,
        )

    @app.post(
        "/api/v2/workbench/answer-value-reviews",
        status_code=status.HTTP_201_CREATED,
    )
    def create_answer_value_review(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.create_answer_value_review(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/projects/{project_id}/answer-value-summary")
    def answer_value_summary(
        project_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return domain_repository.answer_value_summary(
            repository,
            identity,
            project_id=project_id,
        )

    @app.get("/api/v2/workbench/judgments/{judgment_id}")
    def judgment_detail(
        judgment_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return domain_repository.judgment_detail(
            repository,
            identity,
            judgment_id=judgment_id,
        )

    @app.post("/api/v2/workbench/answers/{answer_id}/judgment")
    def promote_answer_to_judgment(
        answer_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.promote_answer_to_judgment(
            repository,
            identity,
            answer_id=answer_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/workbench/judgments/{judgment_id}/confirm")
    def confirm_judgment(
        judgment_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.confirm_judgment(
            repository,
            identity,
            judgment_id=judgment_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/workbench/answer-quality-failures")
    def answer_quality_failures(
        identity: Identity,
        projectId: str | None = None,
        limit: int = 80,
    ) -> list[dict[str, Any]]:
        return domain_repository.list_answer_quality_failures(
            repository,
            identity,
            project_id=projectId,
            limit=limit,
        )

    @app.get("/api/v2/workbench/answer-quality-failures/{failure_id}")
    def answer_quality_failure_detail(
        failure_id: str,
        identity: Identity,
    ) -> dict[str, Any]:
        return domain_repository.answer_quality_failure_detail(
            repository,
            identity,
            failure_id=failure_id,
        )

    @app.post("/api/v2/workbench/answer-quality-failures/{failure_id}/resolve")
    def resolve_answer_quality_failure(
        failure_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.resolve_answer_quality_failure(
            repository,
            identity,
            failure_id=failure_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/workbench/dna-deltas",
        status_code=status.HTTP_201_CREATED,
    )
    def create_dna_delta(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency = None,
    ) -> dict[str, Any]:
        return domain_repository.create_dna_delta(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )
