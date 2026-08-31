from __future__ import annotations

from typing import Any

from .routing import NOT_HANDLED, UiDomainRouter, UiRequest, UiRouteSpec


class UiDomainRegistry:
    def __init__(self, routers: list[UiDomainRouter]):
        self.routers = tuple(routers)

    def dispatch(self, compatibility: Any, request: UiRequest) -> Any:
        for router in self.routers:
            result = router.dispatch(compatibility, request)
            if result is not NOT_HANDLED:
                return result
        return NOT_HANDLED

    def requires_workspace_pin(self, request: UiRequest) -> bool:
        return any(
            router.requires_workspace_pin(request)
            for router in self.routers
        )

    @property
    def routes(self) -> tuple[UiRouteSpec, ...]:
        return tuple(route for router in self.routers for route in router.routes)


def build_default_registry() -> UiDomainRegistry:
    # GC-07 project material + the reviewed GC-14 phase-7 answer surface.
    # Every other handler remains frozen until its mainline phase is approved.
    from .project_materials import router as project_materials_router
    from .workbench_outputs import router as workbench_outputs_router
    from .gc08_meetings import router as gc08_meeting_media_router
    from .gc13_growth import register_gc13_growth_ui_domain
    from .gc14_proposals import router as gc14_proposal_router
    from .platform_integrations import router as platform_integrations_router
    from .platform_device_runtime import router as platform_device_runtime_router
    from .organization_access import router as organization_access_router
    from .startup_status import router as startup_status_router
    from .system_governance import router as system_governance_router
    from .gc04_tasks import router as gc04_gc05_tasks_router
    from .task_attachments import router as task_attachments_router
    from .gc06_planning import router as gc06_planning_router
    from .gc10_consumers import router as gc10_consumer_router
    from .gc12_intelligence import router as gc12_intelligence_router
    from .intelligence_growth import router as intelligence_growth_router
    from .workflow import router as workflow_router
    from .data_center_support_88 import router as data_center_support_router

    allowed = {
        ("GET", r"clients"),
        ("POST", r"clients"),
        ("PUT", r"clients/(?P<project_id>[^/]+)"),
        ("GET", r"clients/(?P<project_id>[^/]+)/workspace"),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/documents/local-filename-state",
        ),
        ("POST", r"imports"),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/materials/process-pending",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/knowledge/progress",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/knowledge/search",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/knowledge/presentation",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/knowledge-context",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/knowledge-status",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/fact-bundle",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/documents/"
            r"(?P<document_id>[^/]+)/retry-processing",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/documents/"
            r"(?P<document_id>[^/]+)/reading-preview",
        ),
        (
            "DELETE",
            r"clients/(?P<project_id>[^/]+)/documents/"
            r"(?P<document_id>[^/]+)",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/document-recycle-bin",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/document-recycle-bin/"
            r"(?P<document_id>[^/]+)/restore",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/documents/ai-action",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/link-materials/import-runs",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/link-materials/import-runs/latest",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/link-materials/import-runs/(?P<run_id>[^/]+)",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/link-materials/import-runs/(?P<run_id>[^/]+)/cancel",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/link-materials/import-runs/(?P<run_id>[^/]+)/retry",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/link-materials/import/start",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/mobile-link-transfers/pending",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/mobile-link-transfers/"
            r"(?P<run_id>[^/]+)/claim",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/mobile-link-transfers/"
            r"(?P<run_id>[^/]+)/settle",
        ),
        ("GET", r"documents/(?P<document_id>[^/]+)/text"),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/glossary-attributes",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/glossary-drift-alerts",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/glossary-drift-alerts/"
            r"(?P<alert_id>[^/]+)/resolve",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/contradictions",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/contradictions/"
            r"(?P<contradiction_id>[^/]+)/review",
        ),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/duplicate-documents",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/duplicate-documents/resolve",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/glossary-attributes/"
            r"(?P<attribute_id>[^/]+)/verify",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/glossary-attributes/"
            r"(?P<attribute_id>[^/]+)/reject",
        ),
        ("GET", r"clients/(?P<project_id>[^/]+)/entities"),
        (
            "GET",
            r"clients/(?P<project_id>[^/]+)/entity-merge-candidates",
        ),
        ("GET", r"clients/(?P<project_id>[^/]+)/glossary"),
        ("POST", r"clients/(?P<project_id>[^/]+)/glossary"),
        ("PATCH", r"glossary/(?P<entry_id>[^/]+)"),
        ("DELETE", r"glossary/(?P<entry_id>[^/]+)"),
        ("POST", r"entities/(?P<entity_id>[^/]+)/verify"),
        ("POST", r"entities/(?P<merged_id>[^/]+)/merge"),
        ("POST", r"clients/(?P<project_id>[^/]+)/documents/from-text"),
        ("PATCH", r"documents/(?P<document_id>[^/]+)/content"),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/documents/"
            r"(?P<document_id>[^/]+)/move-folder",
        ),
        ("POST", r"clients/(?P<project_id>[^/]+)/folders"),
        (
            "PATCH",
            r"clients/(?P<project_id>[^/]+)/folders/(?P<folder_id>[^/]+)",
        ),
        (
            "DELETE",
            r"clients/(?P<project_id>[^/]+)/folders/(?P<folder_id>[^/]+)",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/documents/fill-template/start",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/feishu-doc-import/import",
        ),
        ("POST", r"smart-import/sessions"),
        ("GET", r"smart-import/sessions/(?P<session_id>[^/]+)"),
        ("PATCH", r"smart-import/sessions/(?P<session_id>[^/]+)"),
        ("DELETE", r"smart-import/sessions/(?P<session_id>[^/]+)"),
        ("POST", r"smart-import/sessions/(?P<session_id>[^/]+)/files"),
        ("DELETE", r"smart-import/files/(?P<file_id>[^/]+)"),
        ("PATCH", r"smart-import/files/(?P<file_id>[^/]+)/assign"),
        ("POST", r"smart-import/sessions/(?P<session_id>[^/]+)/chunks"),
        ("PATCH", r"smart-import/chunks/(?P<chunk_id>[^/]+)"),
        ("DELETE", r"smart-import/chunks/(?P<chunk_id>[^/]+)"),
        ("POST", r"smart-import/chunks/(?P<chunk_id>[^/]+)/parse"),
        ("PATCH", r"smart-import/chunks/(?P<chunk_id>[^/]+)/parsed"),
        ("GET", r"smart-import/sessions/(?P<session_id>[^/]+)/preview"),
        ("POST", r"smart-import/sessions/(?P<session_id>[^/]+)/commit"),
        ("DELETE", r"clients/(?P<project_id>[^/]+)"),
        ("GET", r"clients/(?P<project_id>[^/]+)/delete-preview"),
        ("POST", r"clients/(?P<project_id>[^/]+)/folders/recommend"),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/folders/apply-recommendation",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/documents/auto-repair/preview",
        ),
        (
            "POST",
            r"clients/(?P<project_id>[^/]+)/documents/auto-repair/apply",
        ),
    }
    gc07_router = UiDomainRouter("gc07_project_import", pin_workspace=True)
    for route in project_materials_router.routes:
        if (route.method, route.pattern) in allowed:
            gc07_router.route(route.method, route.pattern)(route.handler)
    if len(gc07_router.routes) != len(allowed):
        raise RuntimeError("GC-07 phase-2 route registry is incomplete")
    gc07_sync_allowed = {
        ("POST", r"clients/([^/]+)/sync"),
    }
    gc07_sync_router = UiDomainRouter(
        "gc07_material_metadata_sync",
        pin_workspace=True,
    )
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in gc07_sync_allowed:
            gc07_sync_router.route(route.method, route.pattern)(route.handler)
    if len(gc07_sync_router.routes) != len(gc07_sync_allowed):
        raise RuntimeError("GC-07 material sync route registry is incomplete")
    gc14_allowed = {
        ("POST", r"clients/([^/]+)/workspace/chat/plan"),
        ("POST", r"clients/([^/]+)/workspace/chat/start"),
        ("GET", r"clients/([^/]+)/workspace/chat/messages/([^/]+)"),
        ("GET", r"clients/([^/]+)/workspace/chat/threads/([^/]+)"),
        ("GET", r"clients/([^/]+)/analysis-runs/([^/]+)"),
        ("POST", r"workspace-answer-action-cards/([^/]+)/create-proposal"),
        ("GET", r"clients/([^/]+)/page-context"),
        ("GET", r"clients/([^/]+)/project-structure"),
        ("GET", r"clients/([^/]+)/data-gaps"),
        ("POST", r"clients/([^/]+)/knowledge/export-answer"),
        (
            "POST",
            r"workspace-answer-action-cards/([^/]+)/(create-task|request-evidence)",
        ),
        ("GET", r"clients/([^/]+)/template-fill-runs/([^/]+)"),
    }
    gc14_router = UiDomainRouter("gc14_workbench_answer", pin_workspace=True)
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in gc14_allowed:
            gc14_router.route(route.method, route.pattern)(route.handler)
    if len(gc14_router.routes) != len(gc14_allowed):
        raise RuntimeError("GC-14 phase-7 route registry is incomplete")
    platform_status_router = UiDomainRouter(
        "platform_integration_status",
        pin_workspace=True,
    )
    platform_status_allowed = {
        ("GET", r"org-integrations/feishu"),
        ("POST", r"org-integrations/feishu/validate-and-save"),
        ("GET", r"feishu-sync/status"),
        ("GET", r"logs"),
        ("GET", r"logs/dates"),
        ("GET", r"logs/export"),
        ("GET", r"agent-run-logs"),
        ("GET", r"software-feedback"),
        ("POST", r"software-feedback"),
        ("GET", r"support-requests"),
        ("POST", r"support-requests"),
        ("POST", r"support-requests/(?P<request_id>[^/]+)/resolve"),
    }
    startup_status_allowed = {
        ("GET", r"system/source-integrity"),
        ("GET", r"system/active-background-tasks"),
        ("GET", r"audio-transcription-jobs/recent"),
        ("GET", r"local-asr/model/status"),
    }
    for route in platform_integrations_router.routes:
        if (route.method, route.pattern) in platform_status_allowed:
            platform_status_router.route(route.method, route.pattern)(route.handler)
    if len(platform_status_router.routes) != len(platform_status_allowed):
        raise RuntimeError("platform integration status route registry is incomplete")
    startup_status_domain = UiDomainRouter(
        "strict_startup_status",
        pin_workspace=False,
    )
    for route in startup_status_router.routes:
        if (route.method, route.pattern) in startup_status_allowed:
            startup_status_domain.route(route.method, route.pattern)(route.handler)
    if len(startup_status_domain.routes) != len(startup_status_allowed):
        raise RuntimeError("strict startup status route registry is incomplete")
    platform_device_domain = UiDomainRouter(
        "strict_platform_device_runtime",
        pin_workspace=platform_device_runtime_router.pin_workspace,
    )
    for route in platform_device_runtime_router.routes:
        # These four status paths are already delegated by startup_status to
        # the same real handlers.  Keep one route owner while mounting every
        # remaining device operation from the narrow 88-table adapter.
        if (route.method, route.pattern) not in startup_status_allowed:
            platform_device_domain.route(route.method, route.pattern)(route.handler)
    if len(platform_device_domain.routes) != len(platform_device_runtime_router.routes) - len(startup_status_allowed):
        raise RuntimeError("strict platform device route registry is incomplete")
    personal_runtime_router = UiDomainRouter(
        "personal_runtime_settings",
        pin_workspace=True,
    )
    personal_runtime_allowed = {
        ("GET", r"settings/transcription-preference"),
        ("PUT", r"settings/transcription-preference"),
        ("GET", r"settings/organization-brand"),
        ("POST", r"settings/organization-brand"),
        ("GET", r"settings/tasks"),
        ("POST", r"settings/tasks"),
        ("GET", r"settings/client-workspace"),
        ("POST", r"settings/client-workspace"),
        ("GET", r"settings/topics"),
        ("POST", r"settings/topics"),
        ("GET", r"settings/analysis-workbench"),
        ("POST", r"settings/analysis-workbench"),
        ("GET", r"settings/handbook"),
        ("POST", r"settings/handbook"),
        ("GET", r"settings/speech-model"),
        ("PUT", r"settings/speech-model"),
        ("POST", r"settings/speech-model/test"),
        ("GET", r"settings/object-storage"),
        ("PUT", r"settings/object-storage"),
        ("POST", r"settings/object-storage/test"),
        ("GET", r"settings/main-chain-stability"),
        ("POST", r"settings/main-chain-stability"),
        ("GET", r"settings/feishu-bot"),
        ("POST", r"settings/feishu-bot"),
        ("GET", r"settings/feishu-user-binding"),
        ("POST", r"settings/feishu-user-binding/start"),
        ("DELETE", r"settings/feishu-user-binding"),
        ("GET", r"me/feishu-authorization"),
        ("POST", r"me/feishu-authorization/start"),
        ("POST", r"me/feishu-authorization/claim"),
        ("DELETE", r"me/feishu-authorization"),
        ("GET", r"me/feishu-delivery-profile"),
        ("POST", r"me/feishu-delivery-profile"),
    }
    for route in organization_access_router.routes:
        if (route.method, route.pattern) in personal_runtime_allowed:
            personal_runtime_router.route(route.method, route.pattern)(route.handler)
    if len(personal_runtime_router.routes) != len(personal_runtime_allowed):
        raise RuntimeError("personal runtime settings route registry is incomplete")
    organization_directory_allowed = {
        ("GET", r"admin/employees"),
        ("GET", r"employees/mention-candidates"),
        ("POST", r"admin/employees/([^/]+)/disable"),
        ("POST", r"admin/employees/([^/]+)/enable"),
        ("PATCH", r"admin/employees/([^/]+)/role"),
        ("PATCH", r"admin/employees/([^/]+)/department"),
        ("POST", r"admin/employees/transfer-admin"),
        ("POST", r"admin/employees/([^/]+)/approve"),
        ("POST", r"admin/employees/([^/]+)/reject"),
        ("POST", r"admin/employees/([^/]+)/reset-password"),
        ("GET", r"me/org-membership"),
        ("GET", r"me/org-membership/admin-claim-status"),
        ("POST", r"me/org-membership/apply"),
        ("POST", r"me/org-membership/admin-claim"),
        ("POST", r"organization-directory/sync"),
        ("GET", r"settings/logs"),
        ("GET", r"settings/system-admin"),
        ("POST", r"settings/system-admin"),
        ("POST", r"settings/org-model/intro-document"),
        ("GET", r"org/bots"),
        ("POST", r"org/bots"),
        ("GET", r"org/bots/resolve"),
        ("GET", r"org/bots/([^/]+)"),
        ("PATCH", r"org/bots/([^/]+)"),
        ("POST", r"org/bots/([^/]+)/rotate-token"),
        ("GET", r"org/bots/([^/]+)/permissions"),
        ("GET", r"org/bots/([^/]+)/task-plans"),
        ("POST", r"org/bots/([^/]+)/task-plans"),
        ("POST", r"org/bots/task-plans/([^/]+)/decide"),
        ("GET", r"org/bots/task-plans/([^/]+)/progress"),
    }
    organization_directory_router = UiDomainRouter(
        "strict_organization_directory",
        pin_workspace=True,
    )
    for route in organization_access_router.routes:
        if (route.method, route.pattern) in organization_directory_allowed:
            organization_directory_router.route(route.method, route.pattern)(route.handler)
    if len(organization_directory_router.routes) != len(organization_directory_allowed):
        raise RuntimeError("strict organization directory route registry is incomplete")
    org_model_status_router = UiDomainRouter(
        "strict_org_model_status",
        pin_workspace=False,
    )
    org_model_status_allowed = {
        ("GET", r"settings/org-model/profile"),
        ("POST", r"settings/org-model/profile"),
    }
    for route in startup_status_router.routes:
        if (route.method, route.pattern) in org_model_status_allowed:
            org_model_status_router.route(route.method, route.pattern)(route.handler)
    if len(org_model_status_router.routes) != len(org_model_status_allowed):
        raise RuntimeError("strict organization model status route registry is incomplete")
    gc15_allowed = {
        ("GET", r"clients/([^/]+)/knowledge/memory-sync"),
        ("POST", r"clients/([^/]+)/knowledge/memory-sync"),
        ("POST", r"clients/([^/]+)/knowledge/vectorize-answer"),
        ("DELETE", r"clients/([^/]+)/knowledge/memory-cards/by-message/([^/]+)"),
    }
    gc15_router = UiDomainRouter("gc15_workbench_memory", pin_workspace=True)
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in gc15_allowed:
            gc15_router.route(route.method, route.pattern)(route.handler)
    if len(gc15_router.routes) != len(gc15_allowed):
        raise RuntimeError("GC-15 phase-9 route registry is incomplete")
    gc12_allowed = {
        (
            "POST",
            r"clients/([^/]+)/workspace/chat/messages/([^/]+)/facts/corrections",
        ),
    }
    gc12_router = UiDomainRouter("gc12_answer_corrections", pin_workspace=True)
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in gc12_allowed:
            gc12_router.route(route.method, route.pattern)(route.handler)
    if len(gc12_router.routes) != len(gc12_allowed):
        raise RuntimeError("GC-12 phase-11 route registry is incomplete")
    strategic_profile_allowed = {
        ("GET", r"clients/([^/]+)/narrative"),
        ("GET", r"clients/([^/]+)/narrative/stale-status"),
        ("GET", r"clients/([^/]+)/narrative/clarifications"),
        ("POST", r"clients/([^/]+)/narrative/clarifications"),
        ("POST", r"clients/([^/]+)/narrative/regenerate"),
        ("GET", r"clients/([^/]+)/official-website"),
        ("POST", r"clients/([^/]+)/official-website/refresh"),
        ("GET", r"clients/([^/]+)/clarification-context"),
        ("GET", r"clients/([^/]+)/strategic-docs"),
        ("POST", r"clients/([^/]+)/strategic-docs"),
        ("DELETE", r"clients/([^/]+)/strategic-docs/([^/]+)"),
        ("GET", r"clients/([^/]+)/next-steps"),
        ("POST", r"clients/([^/]+)/next-steps-background"),
        ("GET", r"clients/([^/]+)/suggestions/log"),
        ("POST", r"clients/([^/]+)/suggestions/log"),
        ("DELETE", r"clients/([^/]+)/suggestions/log/([^/]+)"),
        ("GET", r"clients/([^/]+)/todos/unified"),
        ("POST", r"clients/([^/]+)/todos/([^/]+)/promote-to-task"),
        ("POST", r"clients/([^/]+)/todos/([^/]+)/dismiss"),
        ("GET", r"clients/([^/]+)/meeting-action-items"),
        ("GET", r"analysis/jobs/([^/]+)"),
        ("GET", r"analysis/jobs/([^/]+)/stages"),
        ("POST", r"analysis/jobs"),
        ("GET", r"clients/([^/]+)/dna-documents"),
        ("GET", r"clients/([^/]+)/dna-documents/([^/]+)"),
        ("POST", r"clients/([^/]+)/dna-documents/([^/]+)"),
        ("GET", r"clients/([^/]+)/knowledge/parse-failures"),
        ("GET", r"clients/([^/]+)/knowledge/vector-index/status"),
        ("GET", r"clients/([^/]+)/agent-state"),
        ("GET", r"clients/([^/]+)/runtime-run-logs"),
        ("GET", r"clients/([^/]+)/data-center/mobile-snapshot"),
        ("GET", r"clients/([^/]+)/strategic-pulse"),
        ("GET", r"clients/([^/]+)/(judgments|topics|conflicts|open-questions)"),
        ("GET", r"clients/([^/]+)/project-modules/([^/]+)"),
        ("GET", r"clients/([^/]+)/project-flows/([^/]+)"),
        ("POST", r"clients/([^/]+)/project-modules"),
        ("PATCH", r"clients/([^/]+)/project-modules/([^/]+)"),
        ("DELETE", r"clients/([^/]+)/project-modules/([^/]+)"),
        ("POST", r"clients/([^/]+)/project-flows"),
        ("PATCH", r"clients/([^/]+)/project-flows/([^/]+)"),
        ("DELETE", r"clients/([^/]+)/project-flows/([^/]+)"),
        ("POST", r"clients/([^/]+)/goals"),
        ("POST", r"clients/([^/]+)/dna"),
        ("GET", r"clients/([^/]+)/brand-proposition"),
        ("PATCH", r"clients/([^/]+)/brand-proposition"),
        ("GET", r"clients/([^/]+)/strategic-cockpit"),
        ("POST", r"clients/([^/]+)/strategic-cockpit/confirm"),
        ("POST", r"clients/([^/]+)/strategic-cockpit/meeting-pack"),
        (
            "POST",
            r"clients/([^/]+)/strategic-cockpit/meeting-pack/([^/]+)/apply",
        ),
        ("DELETE", r"clients/([^/]+)/workspace/chat/messages/([^/]+)"),
        ("GET", r"clients/([^/]+)/workspace/context-refresh-events"),
        ("POST", r"clients/([^/]+)/workspace/context-refresh-events"),
        ("GET", r"clients/([^/]+)/workspace/data-center-readiness"),
        ("POST", r"clients/([^/]+)/workspace/data-center-readiness/actions"),
        ("GET", r"clients/([^/]+)/digital-assets"),
        ("POST", r"clients/([^/]+)/digital-assets/narrative/refresh"),
        ("GET", r"handbook/([^/]+)"),
        ("POST", r"handbook"),
        ("POST", r"clients/([^/]+)/meetings/([^/]+)/proposals/(follow-up|prepare)"),
        ("POST", r"clients/([^/]+)/meetings/launch-feishu"),
    }
    strategic_profile_router = UiDomainRouter(
        "gc12_strategic_profile_projection",
        pin_workspace=True,
    )
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in strategic_profile_allowed:
            strategic_profile_router.route(route.method, route.pattern)(
                route.handler
            )
    if len(strategic_profile_router.routes) != len(strategic_profile_allowed):
        raise RuntimeError("strategic profile route registry is incomplete")
    strategic_thought_allowed = {
        ("GET", r"strategic/thoughts"),
        ("POST", r"strategic/thoughts/refresh"),
        ("POST", r"strategic/thoughts/[^/]+/state"),
        ("POST", r"strategic/thoughts/[^/]+/review"),
    }
    strategic_thought_router = UiDomainRouter(
        "gc12_strategic_thoughts",
        pin_workspace=True,
    )
    for route in intelligence_growth_router.routes:
        if (route.method, route.pattern) in strategic_thought_allowed:
            strategic_thought_router.route(route.method, route.pattern)(
                route.handler
            )
    if len(strategic_thought_router.routes) != len(strategic_thought_allowed):
        raise RuntimeError("strategic thought route registry is incomplete")
    agent_skill_allowed = {
        ("GET", r"agent-skills"),
        ("POST", r"agent-skills"),
        ("PATCH", r"agent-skills/([^/]+)"),
        ("PATCH", r"agent-skills/([^/]+)/enabled"),
        ("DELETE", r"agent-skills/([^/]+)"),
        ("POST", r"agent-skills/([^/]+)/delete"),
    }
    agent_skill_router = UiDomainRouter("gc11_agent_skill", pin_workspace=True)
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in agent_skill_allowed:
            agent_skill_router.route(route.method, route.pattern)(route.handler)
    if len(agent_skill_router.routes) != len(agent_skill_allowed):
        raise RuntimeError("GC-11 Agent Skill route registry is incomplete")
    writing_skill_allowed = {
        ("GET", r"writing-skills"),
        ("POST", r"writing-skills"),
        ("PUT", r"writing-skills/([^/]+)"),
        ("DELETE", r"writing-skills/([^/]+)"),
        ("POST", r"writing-skills/distill"),
    }
    writing_skill_router = UiDomainRouter(
        "gc11_writing_skill_compatibility",
        pin_workspace=True,
    )
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in writing_skill_allowed:
            writing_skill_router.route(route.method, route.pattern)(route.handler)
    if len(writing_skill_router.routes) != len(writing_skill_allowed):
        raise RuntimeError("GC-11 writing Skill route registry is incomplete")
    gc09_report_allowed = {
        ("GET", r"clients/([^/]+)/report-artifacts"),
        ("GET", r"report-artifacts/([^/]+)"),
        ("GET", r"report-artifacts/([^/]+)/versions"),
        ("PATCH", r"report-artifacts/([^/]+)"),
        ("POST", r"report-artifacts/([^/]+)/restore"),
        ("POST", r"report-artifacts/([^/]+)/render"),
        ("POST", r"reports/draft-blueprint"),
        ("GET", r"reports/([^/]+)"),
        ("PATCH", r"reports/([^/]+)/blueprint"),
        ("POST", r"reports/([^/]+)/draft-sections"),
        ("POST", r"reports/([^/]+)/save"),
        ("POST", r"reports/([^/]+)/render"),
    }
    gc09_report_router = UiDomainRouter("gc09_versioned_reports", pin_workspace=True)
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in gc09_report_allowed:
            gc09_report_router.route(route.method, route.pattern)(route.handler)
    if len(gc09_report_router.routes) != len(gc09_report_allowed):
        raise RuntimeError("GC-09 report route registry is incomplete")
    gc08_meeting_shell_allowed = {
        ("POST", r"clients/([^/]+)/meetings"),
        (
            "POST",
            r"clients/([^/]+)/meetings/([^/]+)/(extract|ingest|publish|resolve)",
        ),
    }
    gc08_meeting_shell_router = UiDomainRouter(
        "gc08_workbench_meeting_shell",
        pin_workspace=True,
    )
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in gc08_meeting_shell_allowed:
            gc08_meeting_shell_router.route(route.method, route.pattern)(
                route.handler
            )
    if len(gc08_meeting_shell_router.routes) != len(gc08_meeting_shell_allowed):
        raise RuntimeError("GC-08 meeting shell route registry is incomplete")
    workbench_maintenance_allowed = {
        ("POST", r"clients/([^/]+)/analysis-runs/([^/]+)/cancel"),
        (
            "POST",
            r"clients/([^/]+)/knowledge/"
            r"(parse-failures/retry|rebuild|reindex-vector)",
        ),
        ("POST", r"clients/([^/]+)/workspace/backfill-imports"),
    }
    workbench_maintenance_router = UiDomainRouter(
        "strict_workbench_maintenance",
        pin_workspace=True,
    )
    for route in workbench_outputs_router.routes:
        if (route.method, route.pattern) in workbench_maintenance_allowed:
            workbench_maintenance_router.route(route.method, route.pattern)(
                route.handler
            )
    if len(workbench_maintenance_router.routes) != len(
        workbench_maintenance_allowed
    ):
        raise RuntimeError("strict workbench maintenance registry is incomplete")
    planning_workshop_allowed = {
        ("POST", r"org-model/plans/parse"),
        ("POST", r"plan-link/predict-from-text"),
        ("GET", r"org-model/plan-item-task-counts"),
        ("GET", r"org-model/plan-items/([^/]+)/tasks"),
        ("GET", r"tasks/([^/]+)/plan-link"),
        ("PATCH", r"tasks/([^/]+)/plan-link"),
        ("POST", r"tasks/([^/]+)/plan-link/recompute"),
        ("GET", r"tasks/([^/]+)/page-context"),
        ("GET", r"tasks/([^/]+)/context-brief"),
        ("GET", r"tasks/([^/]+)/context-preview"),
        ("GET", r"tasks/([^/]+)/smart-brief"),
        ("GET", r"tasks/([^/]+)/understanding"),
        ("GET", r"tasks/([^/]+)/prep-pack"),
        ("POST", r"tasks/([^/]+)/prep-pack/proposals"),
        ("POST", r"tasks/([^/]+)/smart-brief-actions/([^/]+)/adopt"),
        ("POST", r"tasks/ai-parse"),
        ("POST", r"tasks/smart-briefs"),
    }
    planning_workshop_router = UiDomainRouter(
        "gc04_gc06_planning_workshop",
        pin_workspace=True,
    )
    for route in workflow_router.routes:
        if (route.method, route.pattern) in planning_workshop_allowed:
            planning_workshop_router.route(route.method, route.pattern)(route.handler)
    if len(planning_workshop_router.routes) != len(planning_workshop_allowed):
        raise RuntimeError("planning workshop route registry is incomplete")
    routers = [
        gc04_gc05_tasks_router,
        task_attachments_router,
        gc06_planning_router,
        planning_workshop_router,
        gc07_router,
        gc07_sync_router,
        gc14_proposal_router,
        gc14_router,
        gc15_router,
        gc12_router,
        strategic_profile_router,
        strategic_thought_router,
        agent_skill_router,
        writing_skill_router,
        gc09_report_router,
        gc08_meeting_shell_router,
        workbench_maintenance_router,
        gc08_meeting_media_router,
        gc10_consumer_router,
        gc12_intelligence_router,
        startup_status_domain,
        platform_status_router,
        personal_runtime_router,
        organization_directory_router,
        org_model_status_router,
        platform_device_domain,
        system_governance_router,
        data_center_support_router,
    ]
    register_gc13_growth_ui_domain(routers)
    return UiDomainRegistry(routers)
