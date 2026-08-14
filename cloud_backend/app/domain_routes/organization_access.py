from __future__ import annotations

from html import escape
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import HTMLResponse

from strict_common.ids import new_id

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from ..repositories.organization_access import OrganizationAccessRepository
from ..repositories.organization_directory_88 import StrictOrganizationDirectoryRepository
from ..repositories.platform_configurations import PlatformConfigurationRepository
from ..repositories.platform_integrations import PlatformIntegrationsRepository
from ..repositories.system_governance import SystemGovernanceRepository
from ..repositories.gc06_planning import list_decision_actions, list_planning_cycles


def _text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _expected_version(payload: dict[str, Any]) -> int:
    value = payload.get("expectedVersion", payload.get("expected_version"))
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(
            428,
            "expected_version_required",
            "该设置写入必须携带 expectedVersion",
        ) from exc
    if normalized < 0:
        raise RepositoryError(422, "expected_version_invalid", "expectedVersion 无效")
    return normalized


def _optional_expected_version(payload: dict[str, Any]) -> int | None:
    value = payload.get("expectedVersion", payload.get("expected_version"))
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(
            422,
            "expected_version_invalid",
            "expectedVersion 无效",
        ) from exc
    if normalized < 1:
        raise RepositoryError(422, "expected_version_invalid", "expectedVersion 无效")
    return normalized


_AI_ROUTING_PROFILE_KEYS = {
    "online_primary",
    "local_text_deep",
    "local_vision_ocr",
    "local_fast",
}
_AI_ROUTING_MODES = {"auto", "online_first", "local_first", "local_only"}


def _local_ai_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").casefold()
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


def register_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    authority = OrganizationAccessRepository(repository)
    strict_directory = StrictOrganizationDirectoryRepository(repository)
    configurations = PlatformConfigurationRepository(repository)
    platform = PlatformIntegrationsRepository(repository)
    governance = SystemGovernanceRepository(repository)

    def strict_organization_model(identity: SessionIdentity) -> dict[str, Any]:
        """Compose the legacy-shaped view from strict 88-table authorities."""
        snapshot = repository.organization_snapshot(identity)
        organization = dict(snapshot.get("organization") or {})
        departments = list(snapshot.get("departments") or [])
        assignments = list(snapshot.get("departmentAssignments") or [])
        assignments_by_department: dict[str, list[dict[str, Any]]] = {}
        for item in assignments:
            department_id = str(item.get("departmentId") or "")
            if department_id:
                assignments_by_department.setdefault(department_id, []).append(item)
        cycles = list_planning_cycles(repository, identity, include_archived=True)
        actions = list_decision_actions(repository, identity)
        actions_by_cycle: dict[str, list[dict[str, Any]]] = {}
        for action in actions:
            cycle_id = str(action.get("planningCycleId") or "")
            if cycle_id:
                actions_by_cycle.setdefault(cycle_id, []).append(action)
        department_plans = [
            {
                "id": cycle.get("id"),
                "version": cycle.get("version"),
                "departmentId": cycle.get("departmentId"),
                "clientId": cycle.get("clientId"),
                "weekLabel": cycle.get("period") or cycle.get("periodStart") or "",
                "periodKind": cycle.get("periodKind"),
                "periodStart": cycle.get("periodStart"),
                "periodEnd": cycle.get("periodEnd"),
                "ownerUserId": cycle.get("ownerMembershipId"),
                "summary": cycle.get("summary") or "",
                "status": cycle.get("status") or "draft",
                "items": actions_by_cycle.get(str(cycle.get("id") or ""), []),
                "updatedAt": cycle.get("updatedAt"),
            }
            for cycle in cycles
        ]
        return {
            "organization": {
                "organizationId": organization.get("organizationId"),
                "name": organization.get("name") or "",
                "annualGoal": "",
                "annualStrategyYear": "",
                "annualStrategy": "",
                "quarterPlans": [],
                "quarterlyFocus": [],
                "leaderUserId": None,
                "leaderName": "",
                "introDocument": None,
                "managementUserIds": [],
                "updatedAt": organization.get("updatedAt") or "",
            },
            "departments": [
                {
                    "id": item.get("departmentId"),
                    "name": item.get("name") or "",
                    "color": item.get("color") or "",
                    "leaderUserId": next(
                        (
                            assignment.get("membershipId")
                            for assignment in assignments_by_department.get(
                                str(item.get("departmentId") or ""), []
                            )
                            if assignment.get("assignmentRole") == "department_lead"
                        ),
                        None,
                    ),
                    "leaderName": "",
                    "introDocument": None,
                    "mission": "",
                    "businessContext": "",
                    "teamContext": "",
                    "quarterlyFocus": [],
                    "collaborationDepartmentIds": [],
                    "quarterPlan": None,
                    "active": item.get("lifecycleState") == "active",
                    "updatedAt": item.get("updatedAt") or "",
                }
                for item in departments
            ],
            "roles": [],
            "bindings": [],
            "reportingLines": [],
            "taskControlRules": [],
            "roleProcessTemplates": [],
            "focusItems": [],
            "departmentPlans": department_plans,
            "updatedAt": organization.get("updatedAt") or "",
            "state": "ready",
            "authorityStates": {
                "identityStructure": {
                    "state": "ready",
                    "authority": ["organizations", "organization_memberships", "principals"],
                },
                "organizationPlans": {
                    "state": "ready",
                    "authority": ["planning_cycles", "decision_actions"],
                },
            },
        }

    def feishu_bot_projection(identity: SessionIdentity) -> dict[str, Any]:
        integration = platform.feishu_integration(identity)
        return {
            **integration,
            "ready": bool(integration.get("enabled")),
            "receiveIdType": "open_id",
            "receiverId": "",
            "botName": "",
            "userBindingCallbackUrl": str(
                integration.get("effectiveCallbackUrl") or ""
            ),
            "secretSource": (
                "provider_resource_secret_store"
                if integration.get("hasAppSecret")
                else "not_configured"
            ),
            "secretFingerprint": None,
            "lastConnectionStatus": integration.get("lastValidationStatus"),
            "lastConnectionMessage": integration.get("lastValidationMessage"),
            "lastConnectedAt": (
                integration.get("updatedAt")
                if integration.get("enabled")
                else None
            ),
            "lastTestMessageAt": integration.get("updatedAt"),
            "authorityEvidence": {
                "configuration": "provider_resources",
                "credentials": "provider_resources.secret_reference",
                "attempts": "operation_attempts+external_side_effects",
                "credentialStored": bool(integration.get("hasAppSecret")),
            },
        }

    personal_settings: dict[str, tuple[dict[str, Any], set[str]]] = {
        "tasks": (
            {
                "defaultListId": None,
                "defaultPriority": "normal",
                "defaultDueDatePreset": "today",
                "defaultViewMode": "list",
                "listSortMode": "manual",
                "showCompletedTasks": False,
                "defaultReviewScope": "work",
                "autoAssignSelf": True,
            },
            {
                "defaultListId",
                "defaultPriority",
                "defaultDueDatePreset",
                "defaultViewMode",
                "listSortMode",
                "showCompletedTasks",
                "defaultReviewScope",
                "autoAssignSelf",
            },
        ),
        "client-workspace": (
            {
                "meetingPublishDefaultListId": None,
                "meetingPublishDefaultPriority": "normal",
                "defaultGoalQuarter": "",
                "defaultMeetingTitlePrefix": "客户会议",
                "clientDnaModeLabel": "DNA",
            },
            {
                "meetingPublishDefaultListId",
                "meetingPublishDefaultPriority",
                "defaultGoalQuarter",
                "defaultMeetingTitlePrefix",
                "clientDnaModeLabel",
            },
        ),
        "topics": (
            {
                "chineseOnly": True,
                "requireInsightBeforeActions": True,
                "defaultTaskOwnerMode": "self",
                "defaultTimeRange": "3_days",
                "defaultSourceStrategy": "google_bing_news",
            },
            {
                "chineseOnly",
                "requireInsightBeforeActions",
                "defaultTaskOwnerMode",
                "defaultTimeRange",
                "defaultSourceStrategy",
            },
        ),
        "analysis-workbench": (
            {
                "enabledTemplateIds": [],
                "defaultTemplateId": None,
                "defaultTitlePrefix": "系统分析",
                "allowEmployeeTemplateEditing": True,
            },
            {
                "enabledTemplateIds",
                "defaultTemplateId",
                "defaultTitlePrefix",
                "allowEmployeeTemplateEditing",
            },
        ),
        "handbook": (
            {
                "defaultTags": [],
                "defaultCategory": "组织沉淀",
                "allowTaskSource": True,
                "allowAnalysisSource": True,
                "visibilityBoundary": "organization_and_personal",
            },
            {
                "defaultTags",
                "defaultCategory",
                "allowTaskSource",
                "allowAnalysisSource",
                "visibilityBoundary",
            },
        ),
        "transcription-preference": (
            {"provider": "local"},
            {"provider"},
        ),
        "local-input-memory": (
            {
                "cloudAuth": {
                    "rememberInputs": True,
                    "lastEmail": None,
                    "accounts": [],
                },
                "aiSettings": {"rememberCredential": False},
                "feishuIntegration": {
                    "rememberInputs": False,
                    "appId": "",
                    "callbackMode": "cloud_relay",
                    "customCallbackUrl": "",
                },
            },
            {
                "cloudAuth",
                "aiSettings",
                "feishuIntegration",
            },
        ),
    }
    configuration_kinds = {
        "tasks": "task_settings",
        "client-workspace": "client_workspace_settings",
        "topics": "topics_settings",
        "analysis-workbench": "analysis_workbench_settings",
        "handbook": "handbook_settings",
        "transcription-preference": "transcription_preference",
        "local-input-memory": "local_input_memory",
    }

    def read_ai_routing(identity: SessionIdentity) -> dict[str, Any]:
        record = configurations.read(
            identity,
            configuration_kind="organization_ai_routing",
            defaults={
                "advancedAiRoutingEnabled": False,
                "aiModelMode": "auto",
                "aiModelProfiles": {},
            },
        )
        secret = configurations.server_secret(
            identity,
            configuration_kind="organization_ai_routing",
        ) or {}
        profile_keys = secret.get("profileApiKeys")
        if not isinstance(profile_keys, dict):
            profile_keys = {}
        profiles = record.get("aiModelProfiles")
        if not isinstance(profiles, dict):
            profiles = {}
        return {
            **record,
            "aiModelProfiles": {
                key: {
                    **dict(value),
                    "hasApiKey": bool(profile_keys.get(key)),
                }
                for key, value in profiles.items()
                if key in _AI_ROUTING_PROFILE_KEYS
                and isinstance(value, dict)
            },
            "executionState": (
                "ready"
                if bool(record.get("advancedAiRoutingEnabled"))
                else "disabled"
            ),
            "executionReason": None,
            "activeExecutionAuthority": "organization_ai_configs",
        }

    def save_ai_routing(
        identity: SessionIdentity,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        current = read_ai_routing(identity)
        mode = _text(payload, "aiModelMode") or str(
            current.get("aiModelMode") or "auto"
        )
        if mode not in _AI_ROUTING_MODES:
            raise RepositoryError(
                422,
                "ai_model_mode_invalid",
                "高级模型路由模式无效",
            )
        raw_profiles = payload.get(
            "aiModelProfiles",
            current.get("aiModelProfiles") or {},
        )
        if not isinstance(raw_profiles, dict):
            raise RepositoryError(
                422,
                "ai_model_profiles_invalid",
                "高级模型配置必须是对象",
            )
        profiles: dict[str, dict[str, Any]] = {}
        for key, raw in raw_profiles.items():
            if key not in _AI_ROUTING_PROFILE_KEYS or not isinstance(raw, dict):
                raise RepositoryError(
                    422,
                    "ai_model_profile_invalid",
                    "高级模型配置项无效",
                )
            base_url = _text(raw, "baseUrl")
            model = _text(raw, "model")
            enabled = bool(raw.get("enabled"))
            if enabled and (not base_url or not model):
                raise RepositoryError(
                    422,
                    "ai_model_profile_incomplete",
                    "启用的高级模型必须填写 Base URL 和模型名",
                )
            profiles[key] = {
                "enabled": enabled,
                "provider": _text(raw, "provider") or "openai_compatible",
                "providerLabel": _text(raw, "providerLabel"),
                "baseUrl": base_url,
                "model": model,
                "capability": _text(raw, "capability") or key,
                "isLocal": _local_ai_url(base_url),
            }
        existing_secret = configurations.server_secret(
            identity,
            configuration_kind="organization_ai_routing",
        ) or {}
        existing_keys = existing_secret.get("profileApiKeys")
        if not isinstance(existing_keys, dict):
            existing_keys = {}
        merged_keys = {
            key: str(value)
            for key, value in existing_keys.items()
            if key in _AI_ROUTING_PROFILE_KEYS and str(value)
        }
        raw_keys = payload.get("aiModelProfileApiKeys")
        if raw_keys is not None and not isinstance(raw_keys, dict):
            raise RepositoryError(
                422,
                "ai_model_profile_keys_invalid",
                "高级模型凭据必须是对象",
            )
        for key, value in dict(raw_keys or {}).items():
            if key not in _AI_ROUTING_PROFILE_KEYS:
                raise RepositoryError(
                    422,
                    "ai_model_profile_key_invalid",
                    "高级模型凭据项无效",
                )
            if str(value).strip():
                merged_keys[key] = str(value).strip()
        clear_keys = {
            str(value)
            for value in payload.get("clearAiModelProfileApiKeys") or []
        }
        if not clear_keys.issubset(_AI_ROUTING_PROFILE_KEYS):
            raise RepositoryError(
                422,
                "ai_model_profile_key_invalid",
                "高级模型凭据项无效",
            )
        for key in clear_keys:
            merged_keys.pop(key, None)
        for key, profile in profiles.items():
            if (
                profile["enabled"]
                and key != "online_primary"
                and not profile["isLocal"]
                and not merged_keys.get(key)
            ):
                raise RepositoryError(
                    422,
                    "remote_ai_profile_key_required",
                    "远端高级模型必须配置 API Key；本地 Ollama 可不填",
                )
        secret_changed = bool(raw_keys is not None or clear_keys)
        result = configurations.upsert(
            identity,
            configuration_kind="organization_ai_routing",
            scope_kind="organization",
            provider="strict_explicit_routing",
            public_config={
                "advancedAiRoutingEnabled": bool(
                    payload.get(
                        "advancedAiRoutingEnabled",
                        current.get("advancedAiRoutingEnabled", False),
                    )
                ),
                "aiModelMode": mode,
                "aiModelProfiles": profiles,
            },
            expected_version=_expected_version(payload),
            idempotency_key=idempotency_key,
            secret_bundle=(
                {"profileApiKeys": merged_keys}
                if secret_changed and merged_keys
                else None
            ),
            secret_action=(
                "replace"
                if secret_changed and merged_keys
                else "clear"
                if secret_changed
                else "preserve"
            ),
        )
        return {
            **result,
            "aiModelProfiles": {
                key: {
                    **profile,
                    "hasApiKey": bool(merged_keys.get(key)),
                }
                for key, profile in profiles.items()
            },
            "executionState": (
                "ready"
                if result["advancedAiRoutingEnabled"]
                else "disabled"
            ),
            "executionReason": None,
            "activeExecutionAuthority": "organization_ai_configs",
        }

    @app.get(
        "/api/v2/organization-access/settings/ai-routing/runtime-secret"
    )
    def ai_routing_runtime_secret(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        record = read_ai_routing(identity)
        secret = configurations.server_secret(
            identity,
            configuration_kind="organization_ai_routing",
        ) or {}
        profile_keys = secret.get("profileApiKeys")
        if not isinstance(profile_keys, dict):
            profile_keys = {}
        profiles = record.get("aiModelProfiles")
        if not isinstance(profiles, dict):
            profiles = {}
        return {
            "cloudInstanceId": identity.cloud_instance_id,
            "organizationId": identity.organization_id,
            "advancedAiRoutingEnabled": bool(
                record.get("advancedAiRoutingEnabled")
            ),
            "aiModelMode": str(record.get("aiModelMode") or "auto"),
            "effectiveScopeKind": record.get("effectiveScopeKind"),
            "version": int(record.get("version") or 0),
            "profiles": {
                key: {
                    **dict(value),
                    "apiKey": str(profile_keys.get(key) or ""),
                }
                for key, value in profiles.items()
                if key in _AI_ROUTING_PROFILE_KEYS
                and isinstance(value, dict)
            },
        }

    def read_personal_setting(
        name: str,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        defaults, _ = personal_settings[name]
        result = configurations.read(
            identity,
            configuration_kind=configuration_kinds[name],
            defaults=defaults,
            personal_only=True,
        )
        if name == "analysis-workbench":
            result.update(
                {
                    "authorityStates": {
                        "personalPreferences": {
                            "state": "ready",
                            "authority": "provider_resources",
                        },
                        "businessKnowledge": {
                            "state": "blocked",
                            "reasonCode": (
                                "workbench_business_objects_not_preferences"
                            ),
                            "message": (
                                "诊断正文和知识库不属于个人偏好；"
                                "必须从工作台权威对象读取"
                            ),
                        },
                    },
                    "unsupportedFields": [
                        "diagnosisProfiles",
                        "organizationRiskDna",
                        "fundraisingKnowledgeLibrary",
                        "deepDnaLibrary",
                        "coachCaseLibrary",
                        "coachReminderRules",
                        "orgWritingNorms",
                    ],
                }
            )
        return result

    def save_personal_setting(
        name: str,
        identity: SessionIdentity,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        defaults, allowed = personal_settings[name]
        if name == "analysis-workbench":
            business_fields = {
                "diagnosisProfiles",
                "organizationRiskDna",
                "fundraisingKnowledgeLibrary",
                "deepDnaLibrary",
                "coachCaseLibrary",
                "coachReminderRules",
                "orgWritingNorms",
            }
            if any(payload.get(key) not in (None, [], {}) for key in business_fields):
                raise RepositoryError(
                    422,
                    "workbench_business_objects_not_preferences",
                    "知识库与诊断正文必须写入 workbench 权威对象，不能塞入个人偏好",
                )
        current = read_personal_setting(name, identity)
        public = {
            key: payload.get(key, current.get(key, default))
            for key, default in defaults.items()
            if key in allowed
        }
        if name == "transcription-preference" and public["provider"] not in {
            "local",
            "organization_cloud",
        }:
            raise RepositoryError(
                422,
                "transcription_provider_invalid",
                "转写偏好必须是 local 或 organization_cloud",
            )
        result = configurations.upsert(
            identity,
            configuration_kind=configuration_kinds[name],
            scope_kind="personal",
            provider=str(public.get("provider") or ""),
            public_config=public,
            expected_version=_expected_version(payload),
            idempotency_key=idempotency_key,
        )
        if name == "analysis-workbench":
            result.update(
                {
                    "authorityStates": {
                        "personalPreferences": {
                            "state": "ready",
                            "authority": "provider_resources",
                        },
                        "businessKnowledge": {
                            "state": "blocked",
                            "reasonCode": (
                                "workbench_business_objects_not_preferences"
                            ),
                            "message": (
                                "诊断正文和知识库不属于个人偏好；"
                                "必须从工作台权威对象读取"
                            ),
                        },
                    },
                    "unsupportedFields": sorted(business_fields),
                }
            )
        return result

    @app.get("/api/v2/organization-access/invite/resolve")
    def resolve_invite(
        code: Annotated[str, Query(min_length=1, max_length=256)],
    ) -> dict[str, Any]:
        return authority.resolve_invite(code)

    @app.get("/api/v2/organization-access/invite/departments")
    def invite_departments(
        code: Annotated[str, Query(min_length=1, max_length=256)],
    ) -> dict[str, Any]:
        return {"items": authority.invite_departments(code)}

    @app.get("/api/v2/organization-access/members")
    def members(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return {"items": strict_directory.members(identity)}

    @app.get("/api/v2/organization-access/member-candidates")
    def member_candidates(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return {"items": strict_directory.member_candidates(identity)}

    @app.get("/api/v2/organization-access/membership-applications/me")
    def current_membership_application(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        application = strict_directory.membership_application(identity)
        return {"application": application}

    @app.post("/api/v2/organization-access/membership-applications")
    def submit_membership_application(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.submit_membership_application(
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post(
        "/api/v2/organization-access/membership-applications/"
        "{application_id}/decide"
    )
    def decide_membership_application(
        application_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.decide_membership_application(
            identity,
            application_id=application_id,
            decision=str(payload.get("decision") or ""),
            rejection_reason=str(payload.get("reason") or ""),
            expected_version=_optional_expected_version(payload),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/departments")
    def departments(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return {"items": strict_directory.departments(identity)}

    @app.get("/api/v2/organization-access/management-titles")
    def management_titles(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return {"items": strict_directory.management_titles(identity)}

    @app.get("/api/v2/organization-access/model")
    def organization_model(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return strict_organization_model(identity)

    @app.put("/api/v2/organization-access/model")
    def update_organization_model(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        del payload, idempotency_key
        raise RepositoryError(
            409,
            "aggregate_organization_model_retired",
            "组织与部门计划请通过严格规划周期和行动命令分别保存",
        )

    @app.post("/api/v2/organization-access/model/backfill-task-links")
    def reconcile_task_authority_links(
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return authority.reconcile_task_authority_links(
            identity,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/recovery-sets")
    def recovery_sets(
        identity: SessionIdentity = Depends(identity_dependency),
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        return {"items": governance.list_recovery_sets(identity, limit=limit)}

    @app.post("/api/v2/organization-access/recovery-sets")
    def create_recovery_set(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        retention_days = payload.get("retentionDays", 30)
        try:
            normalized_retention_days = int(retention_days)
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                422,
                "backup_retention_invalid",
                "备份保留天数必须是整数",
            ) from exc
        return governance.create_database_backup(
            identity,
            idempotency_key=idempotency_key or new_id(),
            retention_days=normalized_retention_days,
        )

    @app.get("/api/v2/organization-access/settings/{setting_name}")
    def get_personal_setting(
        setting_name: str,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        if setting_name == "ai-routing":
            return read_ai_routing(identity)
        if setting_name == "system-admin":
            # The generic settings route is registered before the concrete
            # system-admin route, so it must use the strict 88-table directory
            # as well.  Calling the frozen authority here would otherwise try
            # to read organization_records before the concrete route can run.
            return strict_directory.system_admin_settings(identity)
        if setting_name == "main-chain-stability":
            return configurations.read(
                identity,
                configuration_kind="main_chain_stability",
                defaults={
                    "latestJudgmentsShadowOff": False,
                    "backfillPaused": False,
                    "workerCounters": {
                        "claimCounts": {},
                        "lockContention": {},
                        "backfillThrottle": {},
                    },
                    "lastCanaryObservation": None,
                },
            )
        if setting_name not in personal_settings:
            raise RepositoryError(
                404,
                "personal_setting_kind_missing",
                "个人设置种类不存在",
            )
        return read_personal_setting(setting_name, identity)

    @app.post("/api/v2/organization-access/settings/{setting_name}")
    def update_personal_setting(
        setting_name: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        if setting_name == "ai-routing":
            return save_ai_routing(
                identity,
                payload,
                idempotency_key or new_id(),
            )
        if setting_name == "system-admin":
            return strict_directory.update_system_admin_settings(
                identity,
                payload=payload,
                idempotency_key=idempotency_key or new_id(),
            )
        if setting_name == "main-chain-stability":
            current = configurations.read(
                identity,
                configuration_kind="main_chain_stability",
                defaults={
                    "latestJudgmentsShadowOff": False,
                    "backfillPaused": False,
                    "workerCounters": {
                        "claimCounts": {},
                        "lockContention": {},
                        "backfillThrottle": {},
                    },
                    "lastCanaryObservation": None,
                },
            )
            return configurations.upsert(
                identity,
                configuration_kind="main_chain_stability",
                scope_kind="organization",
                provider="",
                public_config={
                    "latestJudgmentsShadowOff": bool(
                        payload.get(
                            "latestJudgmentsShadowOff",
                            current["latestJudgmentsShadowOff"],
                        )
                    ),
                    "backfillPaused": bool(
                        payload.get("backfillPaused", current["backfillPaused"])
                    ),
                    "workerCounters": current["workerCounters"],
                    "lastCanaryObservation": payload.get(
                        "lastCanaryObservation",
                        current["lastCanaryObservation"],
                    ),
                },
                expected_version=_expected_version(payload),
                idempotency_key=idempotency_key or new_id(),
            )
        if setting_name not in personal_settings:
            raise RepositoryError(
                404,
                "personal_setting_kind_missing",
                "个人设置种类不存在",
            )
        return save_personal_setting(
            setting_name,
            identity,
            payload,
            idempotency_key or new_id(),
        )

    def read_provider_configuration(
        identity: SessionIdentity,
        *,
        kind: str,
    ) -> dict[str, Any]:
        defaults = (
            {
                "provider": "",
                "modelId": "",
                "extraConfig": {},
                "enabled": False,
            }
            if kind == "speech_model"
            else {
                "provider": "",
                "extraConfig": {},
                "enabled": False,
            }
        )
        record = configurations.read(
            identity,
            configuration_kind=kind,
            defaults=defaults,
        )
        return {
            **record,
            "credentials": {},
            "managedByCloud": True,
            "configuredBy": record.get("effectiveScopeKind"),
        }

    def save_provider_configuration(
        identity: SessionIdentity,
        *,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        current = read_provider_configuration(identity, kind=kind)
        scope_kind = _text(payload, "scopeKind", "scope_kind") or str(
            current["defaultWriteScope"]
        )
        provider = _text(payload, "provider")
        allowed_providers = (
            {"", "volcano", "openai_whisper", "aliyun_tongyi", "xunfei"}
            if kind == "speech_model"
            else {"", "volcano_tos", "aliyun_oss", "aws_s3"}
        )
        if provider not in allowed_providers:
            raise RepositoryError(422, "provider_invalid", "配置服务商无效")
        extra_config = payload.get("extraConfig", current.get("extraConfig", {}))
        if not isinstance(extra_config, dict):
            raise RepositoryError(422, "extra_config_invalid", "扩展配置必须是对象")
        public: dict[str, Any] = {
            "provider": provider,
            "extraConfig": extra_config,
            "enabled": bool(payload.get("enabled", current.get("enabled", False))),
        }
        if kind == "speech_model":
            public["modelId"] = _text(payload, "modelId") or str(
                current.get("modelId") or ""
            )
        raw_credentials = payload.get("credentials")
        if raw_credentials is not None and not isinstance(raw_credentials, dict):
            raise RepositoryError(422, "credentials_invalid", "凭据必须是对象")
        credentials = {
            str(key): str(value)
            for key, value in dict(raw_credentials or {}).items()
            if str(value)
        }
        secret_action = (
            "clear"
            if bool(payload.get("clearCredentials"))
            else "replace"
            if credentials
            else "preserve"
        )
        result = configurations.upsert(
            identity,
            configuration_kind=kind,
            scope_kind=scope_kind,
            provider=provider,
            public_config=public,
            expected_version=_expected_version(payload),
            idempotency_key=idempotency_key,
            secret_bundle=credentials,
            secret_action=secret_action,
        )
        return {
            **result,
            "credentials": {},
            "managedByCloud": True,
            "configuredBy": scope_kind,
        }

    @app.get("/api/v2/organization-access/settings/speech-model/effective")
    def get_speech_model(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return read_provider_configuration(identity, kind="speech_model")

    @app.put("/api/v2/organization-access/settings/speech-model/effective")
    def update_speech_model(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return save_provider_configuration(
            identity,
            kind="speech_model",
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/settings/speech-model/test")
    def test_speech_model(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        del payload
        return configurations.request_probe(
            identity,
            configuration_kind="speech_model",
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/settings/object-storage/effective")
    def get_object_storage(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return read_provider_configuration(identity, kind="object_storage")

    @app.put("/api/v2/organization-access/settings/object-storage/effective")
    def update_object_storage(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return save_provider_configuration(
            identity,
            kind="object_storage",
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/settings/object-storage/test")
    def test_object_storage(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        del payload
        return configurations.request_probe(
            identity,
            configuration_kind="object_storage",
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/settings/main-chain-stability")
    def get_main_chain_stability(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return configurations.read(
            identity,
            configuration_kind="main_chain_stability",
            defaults={
                "latestJudgmentsShadowOff": False,
                "backfillPaused": False,
                "workerCounters": {
                    "claimCounts": {},
                    "lockContention": {},
                    "backfillThrottle": {},
                },
                "lastCanaryObservation": None,
            },
        )

    @app.post("/api/v2/organization-access/settings/main-chain-stability")
    def update_main_chain_stability(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        current = get_main_chain_stability(identity)
        public = {
            "latestJudgmentsShadowOff": bool(
                payload.get(
                    "latestJudgmentsShadowOff",
                    current["latestJudgmentsShadowOff"],
                )
            ),
            "backfillPaused": bool(
                payload.get("backfillPaused", current["backfillPaused"])
            ),
            "workerCounters": current["workerCounters"],
            "lastCanaryObservation": payload.get(
                "lastCanaryObservation",
                current["lastCanaryObservation"],
            ),
        }
        return configurations.upsert(
            identity,
            configuration_kind="main_chain_stability",
            scope_kind="organization",
            provider="",
            public_config=public,
            expected_version=_expected_version(payload),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/settings/system-admin")
    def get_system_admin_settings(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return strict_directory.system_admin_settings(identity)

    @app.post("/api/v2/organization-access/settings/system-admin")
    def update_system_admin_settings(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.update_system_admin_settings(
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/settings/org-model/intro-document")
    def get_organization_intro_document(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return authority.organization_intro_document(identity)

    @app.post("/api/v2/organization-access/settings/org-model/intro-document")
    def save_organization_intro_document(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return authority.save_organization_intro_document(
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/feishu/bot")
    def feishu_bot(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return feishu_bot_projection(identity)

    @app.post("/api/v2/organization-access/feishu/bot")
    def save_feishu_bot(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        platform.save_feishu(
            identity,
            {**payload, "scopeKind": "organization"},
            idempotency_key or new_id(),
        )
        return feishu_bot_projection(identity)

    @app.get("/api/v2/organization-access/feishu/member-authorization")
    def feishu_member_authorization(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return platform.personal_feishu_authorization(identity)

    @app.post("/api/v2/organization-access/feishu/member-authorization/start")
    def start_feishu_member_authorization(
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return platform.start_personal_feishu_authorization(
            identity,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/feishu/member-authorization/claim")
    def claim_feishu_member_authorization(
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return platform.claim_personal_feishu_authorization(
            identity,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get(
        "/api/v2/organization-access/feishu/member-authorization/callback",
        response_class=HTMLResponse,
    )
    def complete_feishu_member_authorization_callback(
        code: str | None = Query(default=None),
        state_token: str | None = Query(default=None, alias="state"),
        provider_error: str | None = Query(default=None, alias="error"),
    ) -> HTMLResponse:
        title = "成员飞书授权失败"
        message = "飞书没有返回完整的授权结果，请回到软件重新发起。"
        success = False
        if provider_error:
            message = "飞书未完成本次授权，请回到软件重新发起。"
        elif state_token and code:
            try:
                platform.complete_personal_feishu_authorization(
                    state_token=state_token.strip(),
                    code=code.strip(),
                )
            except RepositoryError as exc:
                message = exc.message
            else:
                title = "成员飞书授权成功"
                message = "授权已安全保存，现在可以回到桌面软件继续导入文档。"
                success = True
        color = "#047857" if success else "#be123c"
        return HTMLResponse(
            "<!doctype html><html lang=\"zh-CN\"><head>"
            "<meta charset=\"utf-8\"><meta name=\"viewport\" "
            "content=\"width=device-width,initial-scale=1\">"
            f"<title>{escape(title)}</title></head>"
            "<body style=\"font-family:-apple-system,BlinkMacSystemFont,"
            "'Segoe UI',sans-serif;background:#f8fafc;padding:48px\">"
            "<main style=\"max-width:560px;margin:0 auto;background:white;"
            "border-radius:20px;padding:32px;box-shadow:0 16px 45px "
            "rgba(15,23,42,.08)\">"
            f"<h1 style=\"color:{color};font-size:24px\">{escape(title)}</h1>"
            f"<p style=\"color:#475569;line-height:1.8\">{escape(message)}</p>"
            "</main></body></html>"
        )

    @app.delete("/api/v2/organization-access/feishu/member-authorization")
    def clear_feishu_member_authorization(
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return platform.clear_personal_feishu_authorization(
            identity,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/feishu/delivery-profile")
    def feishu_delivery_profile(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return platform.personal_feishu_delivery_profile(identity)

    @app.post("/api/v2/organization-access/feishu/delivery-profile")
    def save_feishu_delivery_profile(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return platform.save_personal_feishu_delivery_profile(
            identity,
            mobile=_text(payload, "mobile"),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch("/api/v2/organization-access/profile")
    def update_profile(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return authority.update_profile(
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/password")
    def change_password(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return authority.change_password(
            identity,
            current_password=_text(payload, "currentPassword"),
            new_password=_text(payload, "newPassword"),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/members/{membership_id}/reset-password")
    def reset_password(
        membership_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.reset_password(
            identity,
            membership_id=membership_id,
            new_password=_text(payload, "newPassword"),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/members/{membership_id}/enable")
    def enable_member(
        membership_id: str,
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.set_member_status(
            identity,
            membership_id=membership_id,
            enabled=True,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/members/{membership_id}/disable")
    def disable_member(
        membership_id: str,
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.set_member_status(
            identity,
            membership_id=membership_id,
            enabled=False,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch("/api/v2/organization-access/members/{membership_id}/role")
    def update_role(
        membership_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        role = _text(payload, "role")
        if role not in {"admin", "employee", "member"}:
            raise RepositoryError(422, "role_invalid", "成员角色无效")
        return strict_directory.set_member_role(
            identity,
            membership_id=membership_id,
            role=role,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch("/api/v2/organization-access/members/{membership_id}/department")
    def update_department(
        membership_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        department_id = payload.get("departmentId")
        return strict_directory.set_member_department(
            identity,
            membership_id=membership_id,
            department_id=(
                str(department_id).strip() if department_id is not None else None
            )
            or None,
            department_lead=bool(payload.get("departmentLead")),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch("/api/v2/organization-access/members/{membership_id}/management-title")
    def update_management_title(
        membership_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        title_id = payload.get("managementTitleId")
        return strict_directory.set_member_management_title(
            identity,
            membership_id=membership_id,
            title_id=(str(title_id).strip() if title_id is not None else None) or None,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/admin/transfer")
    def transfer_admin(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        target = _text(payload, "targetUserId")
        if not target:
            raise RepositoryError(422, "target_user_required", "请选择新管理员")
        action = _text(payload, "currentAdminAction") or "keep_admin"
        if action not in {"keep_admin", "demote_to_member", "disable_self"}:
            raise RepositoryError(422, "admin_action_invalid", "管理员移交方式无效")
        return strict_directory.transfer_admin(
            identity,
            target_membership_id=target,
            current_admin_action=action,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/activity-logs")
    def activity_logs(
        identity: SessionIdentity = Depends(identity_dependency),
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> dict[str, Any]:
        return {"items": strict_directory.activity_logs(identity, limit=limit)}

    @app.get("/api/v2/organization-access/bots")
    def bots(
        identity: SessionIdentity = Depends(identity_dependency),
        status: str | None = Query(default=None),
    ) -> dict[str, Any]:
        items = strict_directory.bots(identity, status=status)
        return {"total": len(items), "items": items}

    @app.post("/api/v2/organization-access/bots")
    def create_bot(
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.create_bot(
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/bots/resolve")
    def resolve_bot(
        handle: Annotated[str, Query(min_length=1, max_length=64)],
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        record = strict_directory.bot(identity, handle=handle.casefold().lstrip("@"))
        reporting = record.get("reporting") or {}
        approvers: list[dict[str, Any]] = []
        seen: set[str] = set()
        for key, role in (
            ("creator_user_ids", "creator"),
            ("department_leader_user_ids", "department_lead"),
            ("ceo_user_ids", "organization_admin"),
        ):
            for membership_id in reporting.get(key) or []:
                normalized = str(membership_id)
                if normalized not in seen:
                    seen.add(normalized)
                    approvers.append({"user_id": normalized, "role": role})
        return {
            "bot_member_id": record["id"],
            "display_name": record["display_name"],
            "handle": record["handle"],
            "actor_type": record["actor_type"],
            "actor_id": record["actor_id"],
            "department_id": record.get("department_id"),
            "department_name": record.get("department_name"),
            "reporting_approvers": approvers,
            "enabled_capabilities": [
                item["capability_key"]
                for item in record.get("capabilities") or []
                if item.get("enabled")
            ],
            "status": record["status"],
            "version": record["version"],
        }

    @app.get("/api/v2/organization-access/bots/{bot_id}")
    def bot(
        bot_id: str,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return strict_directory.bot(identity, bot_id=bot_id)

    @app.patch("/api/v2/organization-access/bots/{bot_id}")
    def update_bot(
        bot_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.update_bot(
            identity,
            bot_id=bot_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/bots/{bot_id}/rotate-token")
    def rotate_bot_token(
        bot_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.rotate_bot_token(
            identity,
            bot_id=bot_id,
            expected_version=_expected_version(payload),
            presented_token=_text(payload, "new_token", "newToken") or None,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/bots/{bot_id}/permissions")
    def bot_permissions(
        bot_id: str,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return strict_directory.bot_permissions(identity, bot_id=bot_id)

    @app.get("/api/v2/organization-access/bots/{bot_id}/task-plans")
    def bot_plans(
        bot_id: str,
        identity: SessionIdentity = Depends(identity_dependency),
        status: str | None = Query(default=None),
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        items = strict_directory.bot_plans(
            identity,
            bot_id=bot_id,
            status=status,
            limit=limit,
        )
        return {"bot_member_id": bot_id, "total": len(items), "items": items}

    @app.post("/api/v2/organization-access/bots/{bot_id}/task-plans")
    def create_bot_plan(
        bot_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.create_bot_plan(
            identity,
            bot_id=bot_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/bots/task-plans/{plan_id}/decide")
    def decide_bot_plan(
        plan_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return strict_directory.decide_bot_plan(
            identity,
            plan_id=plan_id,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/organization-access/bots/task-plans/{plan_id}/progress")
    def bot_plan_progress(
        plan_id: str,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return strict_directory.bot_plan_progress(identity, plan_id=plan_id)

    @app.post("/api/v2/organization-access/members/{membership_id}/approve")
    def approve_member(
        membership_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        application = strict_directory.membership_application(
            identity,
            membership_id=membership_id,
        )
        if application is not None and application["applicationState"] == "pending":
            strict_directory.decide_membership_application(
                identity,
                application_id=str(application["applicationId"]),
                decision="approve",
                rejection_reason="",
                expected_version=int(application["version"]),
                idempotency_key=idempotency_key or new_id(),
            )
            return strict_directory.member(identity, membership_id)
        return strict_directory.set_member_status(
            identity,
            membership_id=membership_id,
            enabled=True,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/organization-access/members/{membership_id}/reject")
    def reject_member(
        membership_id: str,
        payload: dict[str, Any],
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        application = strict_directory.membership_application(
            identity,
            membership_id=membership_id,
        )
        if application is None or application["applicationState"] != "pending":
            raise RepositoryError(
                409,
                "membership_application_not_pending",
                "该成员没有待审批的组织身份申请",
            )
        strict_directory.decide_membership_application(
            identity,
            application_id=str(application["applicationId"]),
            decision="reject",
            rejection_reason=str(payload.get("reason") or ""),
            expected_version=int(application["version"]),
            idempotency_key=idempotency_key or new_id(),
        )
        return strict_directory.member(identity, membership_id)
