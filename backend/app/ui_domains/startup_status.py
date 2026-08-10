"""Small startup router backed by the already-migrated strict handlers.

The registry mounts only a few high-frequency status/configuration endpoints
from this module.  Keep those endpoints responsive, but do not replace real
88-table state with protective empty placeholders: delegate to the strict
platform/organization adapters that own the corresponding capability.
"""

from __future__ import annotations

from typing import Any, Mapping

from .organization_access import update_organization_model
from .platform_integrations import (
    _sensevoice_status,
    active_background_tasks as _active_background_tasks,
    recent_audio_jobs as _recent_audio_jobs,
)
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("strict_startup_status", pin_workspace=False)


def _current(compatibility: Any) -> dict[str, Any]:
    value = compatibility.runtime.current()
    return dict(value) if isinstance(value, Mapping) else {}


def _sandbox_id(current: Mapping[str, Any]) -> str:
    sandbox = current.get("sandbox")
    return (
        str(sandbox.get("sandboxId") or "")
        if isinstance(sandbox, Mapping)
        else ""
    )


@router.get(r"system/source-integrity")
def source_integrity(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    identity = compatibility.runtime.identity
    return {
        "runningBackendRoot": str(compatibility.runtime.database_path.parent),
        "workspaceBackendRoot": None,
        "runningHash": str(identity.manifest_hash or ""),
        "workspaceHash": None,
        "match": None,
        "warning": "严格安装版只报告当前运行数据库与构建身份；未猜测源码工作区",
        "buildVersion": str(identity.build_id or ""),
        "gitCommit": None,
        "runtimeMode": "packaged",
        "frontendBuildVersion": request.query.get("frontendBuildVersion"),
        "frontendGitCommit": request.query.get("frontendGitCommit"),
        "workspaceBuildVersion": None,
        "workspaceGitCommit": None,
        "state": "ready",
        "pollingEnabled": False,
    }


@router.get(r"local-asr/model/status")
def local_asr_status(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    return _sensevoice_status(compatibility)


@router.get(r"audio-transcription-jobs/recent")
def recent_audio_jobs(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _recent_audio_jobs(compatibility, request, match)


@router.get(r"system/active-background-tasks")
def active_background_tasks(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _active_background_tasks(compatibility, request, match)


@router.get(r"settings/transcription-preference")
def transcription_preference(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    current = _current(compatibility)
    return {
        "provider": "local",
        "sandboxId": _sandbox_id(current),
        "state": "not_connected",
        "message": "个人转写偏好尚未迁入88表；本次按本机转写执行",
        "pollingEnabled": False,
        "retryable": True,
    }


def _organization_profile(current: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = current.get("sessionSnapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    organization = snapshot.get("organization")
    organization = organization if isinstance(organization, Mapping) else {}
    departments = snapshot.get("departments")
    departments = departments if isinstance(departments, list) else []
    updated_at = str(
        organization.get("updatedAt")
        or current.get("databaseIdentity", {}).get("buildId")
        or ""
    )
    return {
        "organization": {
            "organizationId": str(organization.get("organizationId") or ""),
            "name": str(organization.get("name") or ""),
            "annualGoal": "",
            "annualStrategyYear": "",
            "annualStrategy": "",
            "quarterPlans": [],
            "quarterlyFocus": [],
            "leaderUserId": None,
            "leaderName": "",
            "introDocument": None,
            "managementUserIds": [],
            "updatedAt": updated_at,
        },
        "departments": [
            {
                "id": str(item.get("departmentId") or ""),
                "name": str(item.get("name") or ""),
                "color": str(item.get("color") or ""),
                "mission": "",
                "businessContext": "",
                "teamContext": "",
                "quarterlyFocus": [],
                "collaborationDepartmentIds": [],
                "quarterPlan": None,
                "updatedAt": updated_at,
            }
            for item in departments
            if isinstance(item, Mapping) and item.get("departmentId")
        ],
        "roles": [],
        "bindings": [],
        "reportingLines": [],
        "taskControlRules": [],
        "roleProcessTemplates": [],
        "focusItems": [],
        "departmentPlans": [],
        "updatedAt": updated_at,
        "state": "not_connected",
        "message": (
            "当前仅显示88表身份与部门投影；组织模型扩展字段尚未接通，"
            "未读取冻结旧配置表"
        ),
        "authorityStates": {
            "identityStructure": {
                "state": "ready",
                "authority": ["organizations", "organization_memberships", "principals"],
            },
            "organizationPlans": {
                "state": "not_connected",
                "authority": ["planning_cycles", "decision_actions"],
            },
            "unfrozenSemanticFields": {
                "state": "blocked",
                "reasonCode": "strict_org_model_projection_not_connected",
                "message": "扩展组织模型尚未迁入88表",
                "fields": [],
            },
            "roleProcessAutomation": {
                "state": "blocked",
                "reasonCode": "strict_org_model_projection_not_connected",
                "message": "岗位流程自动化尚未接通",
                "configurationAuthority": "automation_rules",
                "executionAttemptCreated": False,
            },
        },
        "pollingEnabled": False,
        "retryable": True,
    }


@router.get(r"settings/org-model/profile")
def organization_model_profile(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    # Startup identity/department state already lives in the atomically
    # projected 88-table session snapshot.  Never call the retired aggregate
    # organization model here: a failure in that legacy route used to erase a
    # valid workspace and degrade the planning page to “当前组织”.
    return _organization_profile(_current(compatibility))


@router.post(r"settings/org-model/profile")
def update_organization_model_profile(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return update_organization_model(compatibility, request, match)


__all__ = ["router"]
