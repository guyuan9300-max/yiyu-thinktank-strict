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


def _organization_profile(
    current: Mapping[str, Any],
    *,
    directory_members: list[dict[str, Any]] | None = None,
    management_titles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    snapshot = current.get("sessionSnapshot")
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    organization = snapshot.get("organization")
    organization = organization if isinstance(organization, Mapping) else {}
    departments = snapshot.get("departments")
    departments = departments if isinstance(departments, list) else []
    members = snapshot.get("members")
    members = members if isinstance(members, list) else []
    assignments = snapshot.get("departmentAssignments")
    assignments = assignments if isinstance(assignments, list) else []
    if directory_members is not None:
        members = [
            {
                "membershipId": str(item.get("id") or ""),
                "displayName": str(item.get("fullName") or ""),
                "systemRole": str(item.get("primaryRole") or "employee"),
                "status": str(item.get("membershipStatus") or "active"),
                "version": int(item.get("version") or 1),
            }
            for item in directory_members
            if item.get("id")
        ]
        assignments = [
            {
                "membershipId": str(item.get("id") or ""),
                "departmentId": str(item.get("departmentId") or ""),
                "assignmentRole": (
                    "department_lead"
                    if bool(item.get("isDepartmentLead"))
                    else "member"
                ),
                "status": str(item.get("membershipStatus") or "active"),
                "lifecycleState": "active",
                "version": int(item.get("version") or 1),
                "titleId": item.get("managementTitleId"),
                "visibilityScope": item.get("visibilityScope"),
            }
            for item in directory_members
            if item.get("id")
            and (item.get("departmentId") or item.get("managementTitleId"))
            and str(item.get("accountStatus") or "active")
            in {"active", "approved"}
            and str(item.get("membershipStatus") or "active")
            in {"active", "approved"}
        ]
    members_by_id = {
        str(item.get("membershipId") or ""): item
        for item in members
        if isinstance(item, Mapping) and item.get("membershipId")
    }
    active_assignments = [
        item
        for item in assignments
        if isinstance(item, Mapping)
        and item.get("membershipId")
        and (item.get("departmentId") or item.get("titleId"))
        and str(item.get("status") or "active") == "active"
        and str(item.get("lifecycleState") or "active") == "active"
    ]
    lead_by_department = {
        str(item.get("departmentId") or ""): item
        for item in active_assignments
        if str(item.get("assignmentRole") or item.get("roleKey") or "")
        == "department_lead"
    }
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
            (lambda lead: {
                "id": str(item.get("departmentId") or ""),
                "version": int(item.get("version") or 1),
                "name": str(item.get("name") or ""),
                "color": str(item.get("color") or ""),
                "leaderUserId": (
                    str(lead.get("membershipId") or "") if lead else None
                ),
                "leaderName": str(
                    members_by_id.get(
                        str((lead or {}).get("membershipId") or ""), {}
                    ).get("displayName")
                    or ""
                ),
                "mission": "",
                "businessContext": "",
                "teamContext": "",
                "quarterlyFocus": [],
                "collaborationDepartmentIds": [],
                "quarterPlan": None,
                "active": str(item.get("lifecycleState") or "active") == "active",
                "updatedAt": updated_at,
            })(lead_by_department.get(str(item.get("departmentId") or "")))
            for item in departments
            if isinstance(item, Mapping) and item.get("departmentId")
        ],
        "roles": [
            {
                "id": str(item.get("id") or ""),
                "version": int(item.get("version") or 1),
                "departmentId": None,
                "name": str(item.get("name") or "未命名管理层头衔"),
                "level": "organization_lead",
                "visibilityScope": "organization",
                "managerRoleId": None,
                "isManager": True,
                "goal": "",
                "responsibilities": [],
                "shouldAvoid": [],
                "collaborationRoleIds": [],
                "taskEditScope": "organization",
                "canApproveTasks": False,
                "canReassignTasks": False,
                "canChangeDeadline": False,
                "sortOrder": index,
                "active": str(item.get("state") or "active") == "active",
                "holderBotId": None,
                "updatedAt": str(item.get("updatedAt") or updated_at),
            }
            for index, item in enumerate(management_titles or [])
            if item.get("id")
        ],
        "bindings": [
            {
                "userId": str(item.get("membershipId") or ""),
                "version": int(item.get("version") or 1),
                "departmentId": str(item.get("departmentId") or ""),
                "primaryRoleId": item.get("titleId"),
                "managerUserId": None,
                "isManager": str(
                    item.get("assignmentRole") or item.get("roleKey") or ""
                ) == "department_lead",
                "visibilityScope": str(
                    item.get("visibilityScope") or "department"
                ),
                "projectRoleLabels": [],
                "currentFocus": "",
                "taskEditScope": "self",
                "canApproveTasks": False,
                "canReassignTasks": False,
                "canChangeDeadline": False,
                "updatedAt": updated_at,
            }
            for item in active_assignments
        ],
        "reportingLines": [],
        "taskControlRules": [],
        "roleProcessTemplates": [],
        "focusItems": [],
        "departmentPlans": [],
        "updatedAt": updated_at,
        "state": "partial",
        "message": "组织身份、在职成员和部门结构来自严格88表权威投影",
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
                "state": "not_connected",
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
    runtime = compatibility.runtime
    directory_members: list[dict[str, Any]] | None = None
    management_titles: list[dict[str, Any]] | None = None
    cloud_query = getattr(runtime, "cloud_query", None)
    if callable(cloud_query):
        try:
            member_result = cloud_query("/api/v2/organization-access/members")
            directory_members = list(member_result.get("items") or [])
        except Exception:
            # Non-admin users retain the atomically projected session directory.
            directory_members = None
        try:
            title_result = cloud_query(
                "/api/v2/organization-access/management-titles"
            )
            management_titles = list(title_result.get("items") or [])
        except Exception:
            management_titles = None
    return _organization_profile(
        _current(compatibility),
        directory_members=directory_members,
        management_titles=management_titles,
    )


@router.post(r"settings/org-model/profile")
def update_organization_model_profile(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return update_organization_model(compatibility, request, match)


__all__ = ["router"]
