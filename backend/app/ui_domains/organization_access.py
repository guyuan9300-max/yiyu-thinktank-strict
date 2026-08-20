from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from strict_common.ids import sha256_text

from backend.app.cloud_client import CloudClient, CloudClientError
from backend.app.local_input_memory import (
    LocalInputMemoryStore,
    PersonalSecretBoundaryRequired,
)
from backend.app.project_materials_local import LocalProjectMaterialsRepository
from backend.app.runtime import LocalRuntimeError
from backend.app.ui_idempotency import replayable_cloud_mutation

from .routing import UiDomainRouter, UiRequest


def _requires_pinned_organization_workspace(request: UiRequest) -> bool:
    return (
        request.method == "POST"
        and request.path
        in {
            "settings",
            "local-input-memory/ai",
            "local-input-memory/feishu",
        }
    )


router = UiDomainRouter(
    "organization_access",
    pin_workspace=_requires_pinned_organization_workspace,
)


def _local_ai_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").casefold()
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


def _text(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _setting_missing(path: str, objects: str) -> None:
    raise LocalRuntimeError(
        501,
        "settings_authority_missing",
        (
            f"{path} 没有严格 schema 权威承载；现有可用对象仅为 {objects}。"
            "未写入假设置，需冻结 schema/生命周期/CAS/权限后再接通。"
        ),
    )


def _capability_missing(path: str, evidence: str) -> None:
    raise LocalRuntimeError(
        501,
        "capability_not_connected",
        f"{path} 尚无严格权威链路：{evidence}",
    )


def _cloud_public_get(
    cloud_api_url: str,
    path: str,
    query: Mapping[str, str],
) -> dict[str, Any]:
    if not cloud_api_url:
        raise LocalRuntimeError(422, "cloud_url_required", "请先填写组织云地址")
    try:
        return CloudClient(cloud_api_url)._request(
            "GET",
            path,
            query_params=dict(query),
        )
    except CloudClientError as exc:
        raise LocalRuntimeError(exc.status_code, exc.code, exc.message) from exc


def _refresh_organization(compatibility: Any) -> None:
    current = compatibility.runtime.current()
    sandbox = current.get("sandbox") or {}
    if sandbox.get("kind") == "organization":
        compatibility.runtime.switch(str(sandbox["sandboxId"]))


def _cloud_command(
    compatibility: Any,
    request: UiRequest,
    method: str,
    path: str,
    *,
    payload: Mapping[str, Any] | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    result = compatibility.runtime.cloud_command(
        method,
        path,
        payload=payload or request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    if refresh:
        _refresh_organization(compatibility)
    return result


def _cloud_get_captured(
    compatibility: Any,
    path: str,
    *,
    query: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], Any]:
    captured = compatibility.runtime.capture_sandbox_context()
    result = compatibility.runtime.cloud_query(path, query=dict(query or {}))
    compatibility.runtime.capture_sandbox_context(
        expected_sandbox_id=captured.sandbox_id,
    )
    if not isinstance(result, dict):
        raise LocalRuntimeError(
            502,
            "cloud_response_invalid",
            "组织云返回结构不完整",
        )
    return result, captured


def _cloud_cas_command(
    compatibility: Any,
    request: UiRequest,
    *,
    method: str,
    read_path: str,
    command_path: str | None = None,
    payload: Mapping[str, Any] | None = None,
    scope_aware: bool = False,
) -> dict[str, Any]:
    captured = compatibility.runtime.capture_sandbox_context()

    def command_payload_factory() -> dict[str, Any]:
        current, _ = _cloud_get_captured(compatibility, read_path)
        command_payload = dict(payload or request.body)
        if "expectedVersion" not in command_payload:
            if scope_aware:
                scope_kind = _text(
                    command_payload,
                    "scopeKind",
                    "scope_kind",
                ) or str(current.get("defaultWriteScope") or "personal")
                versions = current.get("scopeVersions")
                if not isinstance(versions, Mapping):
                    versions = {}
                command_payload["scopeKind"] = scope_kind
                command_payload["expectedVersion"] = int(
                    versions.get(scope_kind) or 0
                )
            else:
                command_payload["expectedVersion"] = int(
                    current.get("expectedVersion", current.get("version", 0)) or 0
                )
        return command_payload

    result = replayable_cloud_mutation(
        compatibility.runtime,
        idempotency_key=request.idempotency_key,
        command_type="organization_access.cas_command",
        aggregate_type="organization_configuration",
        aggregate_id=command_path or read_path,
        method=method,
        path=command_path or read_path,
        request_payload=dict(payload or request.body),
        cloud_payload_factory=command_payload_factory,
        refresh_business=False,
    )
    compatibility.runtime.capture_sandbox_context(
        expected_sandbox_id=captured.sandbox_id,
    )
    _refresh_organization(compatibility)
    return result


def _reauth_state(
    *,
    code: str,
    message: str,
    organization_id: str = "",
    cloud_instance_id: str = "",
) -> dict[str, Any]:
    return {
        "authenticated": False,
        "user": None,
        "message": message,
        "sessionMode": "cloud",
        "requiresLocalIdentitySetup": False,
        "localIdentityStatus": "none",
        "organizationSelectionRequired": False,
        "organizationSelectionToken": None,
        "organizations": [],
        "reauthRequired": True,
        "actionRequired": "reauth",
        "redirectTo": "login",
        "reasonCode": code,
        "requestedIdentity": {
            "cloudInstanceId": cloud_instance_id or None,
            "organizationId": organization_id or None,
        },
    }


def _workspace_reauth(
    compatibility: Any,
    *,
    code: str,
    message: str,
) -> dict[str, Any]:
    return {
        **compatibility.workspaces(),
        "reauthRequired": True,
        "actionRequired": "reauth",
        "redirectTo": "login",
        "reasonCode": code,
        "message": message,
    }


def _input_memory_store(compatibility: Any) -> LocalInputMemoryStore:
    try:
        context = compatibility.runtime._current_context(require_ready=False)
        auth = compatibility.auth_state()
        user = auth.get("user") or {}
        verified = bool(
            auth.get("authenticated")
            and _text(user, "id") == context.membership_id
            and _text(user, "organizationId") == context.organization_id
            and user.get("accountStatus") == "active"
        )
    except LocalRuntimeError:
        context = None
        verified = False
    return LocalInputMemoryStore(
        compatibility.runtime.secret_store,
        cloud_instance_id=context.cloud_instance_id if verified else "",
        organization_id=context.organization_id if verified else "",
        principal_id=context.principal_id if verified else "",
        membership_id=context.membership_id if verified else "",
    )


@router.get(r"auth/me")
def auth_state(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    return compatibility.auth_state()


@router.post(r"auth/login")
def login(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    compatibility.runtime.login(
        cloud_api_url=_text(request.body, "cloudApiUrl"),
        identifier=_text(request.body, "identifier", "email"),
        password=_text(request.body, "password"),
        idempotency_key=request.idempotency_key,
    )
    return compatibility.auth_state()


def _register_or_join(compatibility: Any, request: UiRequest) -> dict[str, Any]:
    invite_code = _text(request.body, "inviteCode")
    organization_name = _text(request.body, "organizationName")
    cloud_api_url = _text(request.body, "cloudApiUrl")
    if invite_code:
        compatibility.runtime.join(
            cloud_api_url=cloud_api_url,
            invite_code=invite_code,
            display_name=_text(request.body, "fullName", "displayName"),
            email=_text(request.body, "email") or None,
            phone=_text(request.body, "phone") or None,
            password=_text(request.body, "password"),
        )
    elif organization_name:
        bootstrap_token = _text(request.body, "bootstrapToken")
        if not bootstrap_token:
            raise LocalRuntimeError(
                409,
                "organization_bootstrap_credential_missing",
                "创建组织需要该云实例的一次性 bootstrap 凭据",
            )
        compatibility.runtime.create_organization(
            cloud_api_url=cloud_api_url,
            bootstrap_token=bootstrap_token,
            organization_name=organization_name,
            display_name=_text(request.body, "fullName", "displayName"),
            email=_text(request.body, "email") or None,
            phone=_text(request.body, "phone") or None,
            password=_text(request.body, "password"),
        )
    else:
        raise LocalRuntimeError(
            422,
            "organization_join_or_create_required",
            "严格新版注册需要邀请码或组织创建信息；本机草稿不保存账号凭据",
        )
    return compatibility.auth_state()


@router.post(r"auth/register")
def register(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    return _register_or_join(compatibility, request)


@router.post(r"local-auth/register")
def local_register(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    return _register_or_join(compatibility, request)


@router.post(r"local-auth/login")
def local_login(_: Any, request: UiRequest, __: Any) -> dict[str, Any]:
    del request
    return _reauth_state(
        code="cloud_reauthentication_required",
        message="严格本机库不保存账号密码；请在组织登录页填写云地址并重新登录",
    )


@router.get(r"auth/invite-code/resolve")
def resolve_invite(_: Any, request: UiRequest, __: Any) -> dict[str, Any]:
    return _cloud_public_get(
        _text(request.query, "cloudApiUrl"),
        "/api/v2/organization-access/invite/resolve",
        {"code": _text(request.query, "code")},
    )


@router.get(r"auth/department-options")
def department_options(compatibility: Any, request: UiRequest, _: Any) -> list[dict[str, Any]]:
    invite_code = _text(request.query, "inviteCode")
    cloud_api_url = _text(request.query, "cloudApiUrl")
    if invite_code:
        result = _cloud_public_get(
            cloud_api_url,
            "/api/v2/organization-access/invite/departments",
            {"code": invite_code},
        )
        return list(result.get("items") or [])
    session = compatibility.runtime.current().get("sessionSnapshot") or {}
    return [
        {
            "id": item.get("departmentId"),
            "name": item.get("name") or "未命名部门",
            "color": item.get("color") or "#5B7CFA",
        }
        for item in session.get("departments") or []
        if isinstance(item, Mapping) and item.get("departmentId")
    ]


@router.post(r"auth/organizations/join")
def join_organization(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    body = request.body
    required = ("displayName", "password")
    if any(not _text(body, field) for field in required):
        return _reauth_state(
            code="organization_join_credentials_missing",
            message="加入另一组织需要重新输入姓名和密码，不能复用当前组织会话",
            organization_id=_text(body, "organizationId"),
            cloud_instance_id=_text(body, "cloudInstanceId"),
        )
    compatibility.runtime.join(
        cloud_api_url=_text(body, "cloudApiUrl"),
        invite_code=_text(body, "inviteCode"),
        display_name=_text(body, "displayName"),
        email=_text(body, "email") or None,
        phone=_text(body, "phone") or None,
        password=_text(body, "password"),
    )
    return compatibility.auth_state()


@router.post(r"auth/organizations/create")
def create_organization(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    body = request.body
    if any(
        not _text(body, field)
        for field in ("bootstrapToken", "password", "displayName")
    ):
        return _reauth_state(
            code="organization_create_credentials_missing",
            message="创建组织需要 bootstrap 凭据、姓名和密码，不能从当前用户猜取",
            organization_id=_text(body, "organizationId"),
            cloud_instance_id=_text(body, "cloudInstanceId"),
        )
    compatibility.runtime.create_organization(
        cloud_api_url=_text(body, "cloudApiUrl"),
        bootstrap_token=_text(body, "bootstrapToken"),
        organization_name=_text(body, "organizationName"),
        display_name=_text(body, "displayName"),
        email=_text(body, "email") or None,
        phone=_text(body, "phone") or None,
        password=_text(body, "password"),
    )
    return compatibility.auth_state()


@router.post(r"auth/select-organization")
def select_organization(_: Any, request: UiRequest, __: Any) -> dict[str, Any]:
    return _reauth_state(
        code="organization_selection_reauthentication_required",
        message=(
            "选择组织必须由 cloud_instance_id + organization_id 的已验证凭据建立，"
            "请重新登录目标组织"
        ),
        organization_id=_text(request.body, "organizationId"),
        cloud_instance_id=_text(request.body, "cloudInstanceId"),
    )


@router.post(r"auth/logout")
def logout(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    compatibility.runtime.logout(idempotency_key=request.idempotency_key)
    return compatibility.auth_state()


@router.post(r"auth/change-password")
def change_password(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/password",
        refresh=False,
    )


@router.patch(r"auth/me")
def update_profile(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    _cloud_command(
        compatibility,
        request,
        "PATCH",
        "/api/v2/organization-access/profile",
    )
    return compatibility.auth_state()


@router.get(r"workspaces")
def workspaces(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    return compatibility.workspaces()


@router.get(r"workspaces/current")
def current_workspace(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    return compatibility.current_workspace()


@router.post(r"workspaces")
def create_workspace(compatibility: Any, request: UiRequest, __: Any) -> dict[str, Any]:
    del request
    return _workspace_reauth(
        compatibility,
        code="workspace_authentication_required",
        message=(
            "组织工作空间只能由登录、加入或创建组织成功后建立；"
            "未连接草稿不会进入可切换组织"
        ),
    )


@router.patch(r"workspaces/([^/]+)")
def update_workspace(compatibility: Any, request: UiRequest, __: Any) -> dict[str, Any]:
    del request
    return _workspace_reauth(
        compatibility,
        code="workspace_binding_immutable",
        message="工作空间绑定不能脱离严格身份握手修改；请重新登录目标组织",
    )


@router.post(r"workspaces/([^/]+)/activate")
def activate_workspace(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    compatibility.runtime.switch(
        match.group(1),
        idempotency_key=request.idempotency_key,
        request_seq=request.request_seq,
    )
    return compatibility.workspaces()


@router.post(r"workspaces/current/repair-task-mirrors")
def repair_task_mirrors(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    current = compatibility.runtime.current()
    return {
        "sandboxId": (current.get("sandbox") or {}).get("sandboxId"),
        "buildFingerprint": "strict-v2",
        "status": "skipped",
        "ran": False,
        "scannedCount": 0,
        "removedCount": 0,
        "preservedCount": 0,
        "reason": "严格新版只使用 sandbox_id 投影，不存在旧任务镜像",
    }


@router.get(r"admin/employees")
def employees(compatibility: Any, _: UiRequest, __: Any) -> list[dict[str, Any]]:
    result = compatibility.runtime.cloud_query(
        "/api/v2/organization-access/members"
    )
    return list(result.get("items") or [])


@router.get(r"employees/mention-candidates")
def mention_candidates(compatibility: Any, request: UiRequest, _: Any) -> list[dict[str, Any]]:
    term = _text(request.query, "q").lower()
    snapshot = compatibility.runtime.current().get("sessionSnapshot") or {}
    current_id = _text(snapshot.get("membership") or {}, "membershipId")
    # The login snapshot is an identity receipt, not the live organization
    # directory.  It may legitimately omit peers and must never make the task
    # editor look as if the organization has no other members.
    result = compatibility.runtime.cloud_query(
        "/api/v2/organization-access/member-candidates"
    )
    members = list(result.get("items") or [])
    return [
        {
            "id": str(item.get("id") or item.get("membershipId") or ""),
            "fullName": item.get("fullName") or item.get("displayName") or "未命名成员",
            "email": item.get("email") or "",
            "primaryRole": (
                "admin" if item.get("systemRole") == "admin" else "employee"
            ),
            "isSelf": str(item.get("id") or item.get("membershipId") or "") == current_id,
        }
        for item in members
        if (item.get("id") or item.get("membershipId"))
        and str(item.get("membershipStatus") or item.get("status") or "active") == "active"
        if not term
        or term in str(item.get("fullName") or item.get("displayName") or "").lower()
    ]


def _member_command(
    compatibility: Any,
    request: UiRequest,
    membership_id: str,
    suffix: str,
    *,
    method: str,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        method,
        f"/api/v2/organization-access/members/{membership_id}/{suffix}",
    )


@router.post(r"admin/employees/([^/]+)/reset-password")
def admin_reset_password(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    return _member_command(
        compatibility, request, match.group(1), "reset-password", method="POST"
    )


@router.post(r"admin/employees/([^/]+)/approve")
def approve_employee(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    return _member_command(
        compatibility, request, match.group(1), "approve", method="POST"
    )


@router.post(r"admin/employees/([^/]+)/reject")
def reject_employee(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    return _member_command(
        compatibility, request, match.group(1), "reject", method="POST"
    )


@router.post(r"admin/employees/([^/]+)/disable")
def disable_employee(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    return _member_command(
        compatibility, request, match.group(1), "disable", method="POST"
    )


@router.post(r"admin/employees/([^/]+)/enable")
def enable_employee(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    return _member_command(
        compatibility, request, match.group(1), "enable", method="POST"
    )


@router.patch(r"admin/employees/([^/]+)/role")
def update_employee_role(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    return _member_command(
        compatibility, request, match.group(1), "role", method="PATCH"
    )


@router.patch(r"admin/employees/([^/]+)/department")
def update_employee_department(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    return _member_command(
        compatibility, request, match.group(1), "department", method="PATCH"
    )


@router.patch(r"admin/employees/([^/]+)/management-title")
def update_employee_management_title(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    return _member_command(
        compatibility,
        request,
        match.group(1),
        "management-title",
        method="PATCH",
    )


@router.post(r"admin/employees/transfer-admin")
def transfer_admin(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/admin/transfer",
    )


@router.post(r"organization-directory/sync")
def sync_directory(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    items = employees(compatibility, request, None)
    return {
        "status": "verified",
        "state": "ready",
        "organizationId": _text(
            compatibility.runtime.current().get("sandbox") or {},
            "organizationId",
        ),
        "memberCount": len(items),
        "source": "strict_organization_authority",
        "mutationExecuted": False,
        "verificationKind": "read_only_authority_check",
        "message": "已只读核验组织成员权威；严格新版没有待执行的目录复制任务",
    }


@router.get(r"settings")
def get_settings(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    result = compatibility.settings()
    settings = result["settings"]
    ai = compatibility.ai_runtime()
    settings["aiConfigVersion"] = int(ai.get("configVersion") or 0)
    try:
        routing = compatibility.runtime.cloud_query(
            "/api/v2/organization-access/settings/ai-routing"
        )
    except LocalRuntimeError as exc:
        if exc.code not in {
            "organization_required",
            "workspace_not_ready",
            "workspace_secret_missing",
            "workspace_session_invalid",
        }:
            raise
        routing = {
            "advancedAiRoutingEnabled": False,
            "aiModelMode": "auto",
            "aiModelProfiles": {},
            "executionState": "not_connected",
            "executionReason": exc.code,
            "activeExecutionAuthority": "organization_ai_configs",
        }
    settings.update(
        {
            "advancedAiRoutingEnabled": bool(
                routing.get("advancedAiRoutingEnabled")
            ),
            "aiModelMode": routing.get("aiModelMode") or "auto",
            "aiModelProfiles": routing.get("aiModelProfiles") or {},
            "advancedAiRoutingExecutionState": routing.get(
                "executionState"
            ),
            "advancedAiRoutingExecutionReason": routing.get(
                "executionReason"
            ),
            "activeAiExecutionAuthority": routing.get(
                "activeExecutionAuthority"
            ),
            "aiRoutingVersion": int(routing.get("version") or 0),
        }
    )
    user = compatibility.auth_state().get("user") or {}
    if user.get("primaryRole") == "admin":
        backups = compatibility.runtime.cloud_query(
            "/api/v2/system-governance/recovery-sets?limit=20"
        )
        backup_items = list(backups.get("items") or [])
        result["backupCatalog"] = backup_items
        result["lastBackupAt"] = (
            backup_items[0].get("createdAt") if backup_items else None
        )
    else:
        result["backupCatalog"] = []
        result["lastBackupAt"] = None
        result["backupCatalogAccess"] = "admin_required"
    result["authorityEvidence"] = {
        "authoritativeObjects": [
            "organization_ai_configs",
            "organization_memberships",
            "workspace_bindings",
            "workspace_session_snapshots",
            "recovery_sets",
            "backup_catalog",
            "provider_resources",
        ],
        "derivedFields": [
            "currentOperatorId",
            "cloudApiUrl",
            "operators",
            "health",
            "lastCloudAiSyncStatus",
        ],
        "missingSettingsAuthorities": [
            "foldersRootLabel",
            "demoDataLoaded",
        ],
        "advancedAiRoutingExecutionState": routing.get("executionState"),
    }
    return result


@router.post(r"settings")
def update_settings(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    allowed = {
        "currentOperatorId",
        "cloudApiUrl",
        "aiProvider",
        "aiProviderLabel",
        "aiBaseUrl",
        "aiModel",
        "aiConfigVersion",
        "apiKey",
        "clearApiKey",
        "advancedAiRoutingEnabled",
        "aiModelMode",
        "aiModelProfiles",
        "aiModelProfileApiKeys",
        "clearAiModelProfileApiKeys",
    }
    unsupported = sorted(set(request.body) - allowed)
    if unsupported:
        _setting_missing(
            request.path,
            "organization_ai_configs（不承载 "
            + ", ".join(unsupported)
            + "）",
        )
    current = compatibility.settings()["settings"]
    current_ai = compatibility.ai_runtime()
    current["aiConfigVersion"] = int(current_ai.get("configVersion") or 0)
    provider = _text(request.body, "aiProvider") or str(
        current.get("aiProvider") or "openai_compatible"
    )
    base_url = _text(request.body, "aiBaseUrl") or str(
        current.get("aiBaseUrl") or ""
    )
    model_name = _text(request.body, "aiModel") or str(
        current.get("aiModel") or ""
    )
    main_changed = any(
        (
            provider != str(current.get("aiProvider") or ""),
            base_url.rstrip("/") != str(current.get("aiBaseUrl") or "").rstrip("/"),
            model_name != str(current.get("aiModel") or ""),
        )
    )
    api_key = _text(request.body, "apiKey")
    main_committed = False
    main_result: dict[str, Any] | None = None
    try:
        expected_ai_version = int(
            request.body.get(
                "aiConfigVersion",
                current.get("aiConfigVersion", 0),
            )
            or 0
        )
    except (TypeError, ValueError) as exc:
        raise LocalRuntimeError(
            422,
            "organization_ai_version_invalid",
            "主模型配置版本必须是非负整数",
        ) from exc
    if expected_ai_version < 0:
        raise LocalRuntimeError(
            422,
            "organization_ai_version_invalid",
            "主模型配置版本必须是非负整数",
        )
    if api_key:
        main_result = compatibility.runtime.save_ai_config(
            provider=provider,
            base_url=base_url,
            model_name=model_name,
            api_key=api_key,
            expected_version=expected_ai_version,
            idempotency_key=f"{request.idempotency_key}:main-ai",
        )
        main_committed = True
    elif main_changed:
        if _local_ai_url(base_url) and bool(request.body.get("clearApiKey")):
            main_result = compatibility.runtime.save_ai_config(
                provider=provider,
                base_url=base_url,
                model_name=model_name,
                api_key="",
                expected_version=expected_ai_version,
                idempotency_key=f"{request.idempotency_key}:main-ai",
            )
            main_committed = True
        else:
            raise LocalRuntimeError(
                422,
                "organization_ai_key_required",
                "更换远端主模型必须提供 API Key；主模型未变时会保留原凭据",
            )
    routing_requested = any(
        key in request.body
        for key in (
            "advancedAiRoutingEnabled",
            "aiModelMode",
            "aiModelProfiles",
            "aiModelProfileApiKeys",
            "clearAiModelProfileApiKeys",
        )
    )
    try:
        if routing_requested:
            routing = compatibility.runtime.cloud_query(
                "/api/v2/organization-access/settings/ai-routing"
            )
            compatibility.runtime.cloud_command(
                "POST",
                "/api/v2/organization-access/settings/ai-routing",
                payload={
                    "advancedAiRoutingEnabled": bool(
                        request.body.get("advancedAiRoutingEnabled")
                    ),
                    "aiModelMode": request.body.get("aiModelMode") or "auto",
                    "aiModelProfiles": request.body.get("aiModelProfiles") or {},
                    "aiModelProfileApiKeys": (
                        request.body.get("aiModelProfileApiKeys") or {}
                    ),
                    "clearAiModelProfileApiKeys": (
                        request.body.get("clearAiModelProfileApiKeys") or []
                    ),
                    "expectedVersion": int(routing.get("version") or 0),
                },
                idempotency_key=f"{request.idempotency_key}:ai-routing",
                refresh_business=False,
            )
            compatibility.runtime.sync_ai()
    except LocalRuntimeError as exc:
        if not main_committed:
            raise
        partial = get_settings(compatibility, request, None)
        partial["mutationOutcome"] = {
            "state": "partial",
            "mainAiConfig": "committed",
            "mainAiConfigVersion": int(
                (main_result or {}).get("configVersion")
                or partial["settings"].get("aiConfigVersion")
                or 0
            ),
            "advancedAiRouting": "failed",
            "errorCode": exc.code,
            "message": exc.message,
            "retryable": exc.status_code >= 500 or exc.status_code == 409,
        }
        return partial
    result = get_settings(compatibility, request, None)
    result["mutationOutcome"] = {
        "state": "completed",
        "mainAiConfig": "committed" if main_committed else "unchanged",
        "advancedAiRouting": (
            "committed" if routing_requested else "unchanged"
        ),
        "retryable": False,
    }
    return result


@router.get(r"settings/org-ai-runtime")
def ai_runtime(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    return compatibility.ai_runtime()


@router.post(r"settings/org-ai-runtime/sync")
def sync_ai_runtime(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    del request
    return compatibility.ai_runtime()


@router.post(r"settings/org-ai-config/sync-to-cloud")
def sync_ai_to_cloud(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    runtime = compatibility.runtime.sync_ai()
    ready = runtime.get("state") == "ready_direct"
    return {
        "state": runtime.get("state") or "not_ready",
        "at": runtime.get("syncedAt"),
        "reason": (
            None
            if ready
            else runtime.get("message") or "组织管理员尚未配置统一模型"
        ),
        "provider": runtime.get("provider") or None,
        "providerLabel": runtime.get("provider") or None,
        "model": runtime.get("modelName") or None,
        "baseUrl": runtime.get("baseUrl") or None,
        "hasApiKey": ready,
        "fingerprint": runtime.get("keyFingerprint") or None,
        "authorityMode": "organization_direct",
        "uploadRequired": False,
    }


@router.get(r"settings/org-model/profile")
def get_organization_model(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(
        "/api/v2/organization-access/model"
    )


@router.post(r"settings/org-model/profile")
def update_organization_model(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "PUT",
        "/api/v2/organization-access/model",
        refresh=False,
    )


@router.post(r"settings/backup")
def create_backup(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/recovery-sets",
        payload={
            "retentionDays": request.body.get("retentionDays", 30),
        },
        refresh=False,
    )


@router.get(r"settings/feishu-bot")
def get_feishu_bot(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(
        "/api/v2/organization-access/feishu/bot"
    )


@router.post(r"settings/feishu-bot")
def save_feishu_bot(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/feishu/bot",
        payload={
            "appId": _text(request.body, "appId"),
            "appSecret": _text(request.body, "appSecret"),
            "callbackMode": _text(request.body, "callbackMode") or "cloud_relay",
            "customCallbackUrl": _text(request.body, "customCallbackUrl"),
            **(
                {"expectedVersion": request.body["expectedVersion"]}
                if request.body.get("expectedVersion") is not None
                else {}
            ),
        },
        refresh=False,
    )


def _member_feishu_authorization(compatibility: Any) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(
        "/api/v2/organization-access/feishu/member-authorization"
    )


@router.get(r"settings/feishu-user-binding")
def get_feishu_user_binding(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _member_feishu_authorization(compatibility)


@router.post(r"settings/feishu-user-binding/start")
def start_feishu_user_binding(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/feishu/member-authorization/start",
        payload={},
        refresh=False,
    )


@router.delete(r"settings/feishu-user-binding")
def clear_feishu_user_binding(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "DELETE",
        "/api/v2/organization-access/feishu/member-authorization",
        payload={},
        refresh=False,
    )


@router.get(r"me/feishu-authorization")
def get_feishu_authorization(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _member_feishu_authorization(compatibility)


@router.post(r"me/feishu-authorization/start")
def start_feishu_authorization(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return start_feishu_user_binding(compatibility, request, None)


@router.post(r"me/feishu-authorization/claim")
def claim_feishu_authorization(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/feishu/member-authorization/claim",
        payload={},
        refresh=False,
    )


@router.delete(r"me/feishu-authorization")
def clear_feishu_authorization(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return clear_feishu_user_binding(compatibility, request, None)


@router.get(r"me/feishu-delivery-profile")
def get_feishu_delivery_profile(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(
        "/api/v2/organization-access/feishu/delivery-profile"
    )


@router.post(r"me/feishu-delivery-profile")
def save_feishu_delivery_profile(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/feishu/delivery-profile",
        payload={"mobile": _text(request.body, "mobile")},
        refresh=False,
    )


@router.get(r"settings/logs")
def activity_logs(compatibility: Any, _: UiRequest, __: Any) -> list[dict[str, Any]]:
    result = compatibility.runtime.cloud_query(
        "/api/v2/organization-access/activity-logs"
    )
    return list(result.get("items") or [])


@router.get(r"me/org-membership")
def membership_summary(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    summary = compatibility.org_membership()
    return {
        **summary,
        "applicationState": "none",
        "applicationMessage": None,
    }


@router.get(r"me/org-membership/admin-claim-status")
def admin_claim_status(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    user = compatibility.auth_state().get("user") or {}
    summary = compatibility.org_membership()
    is_admin = user.get("primaryRole") == "admin"
    return {
        "hasOrganization": bool(summary.get("hasOrganization")),
        "organizationId": summary.get("organizationId"),
        "organizationName": summary.get("organizationName"),
        "hasAdmin": True,
        "canClaim": False,
        "reason": None if is_admin else "严格组织创建时已确定管理员，禁止绕过授权自行认领",
        "currentUserRole": user.get("primaryRole"),
        "currentUserMembershipStatus": user.get("membershipStatus"),
    }


@router.post(r"me/org-membership/apply")
def apply_membership(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    summary = compatibility.org_membership()
    if not summary.get("hasOrganization"):
        raise LocalRuntimeError(
            409,
            "organization_join_required",
            "请先通过组织邀请码完成加入，再申请调整部门或岗位",
        )
    application = _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/membership-applications",
        payload={
            "inviteCode": _text(request.body, "inviteCode"),
            "departmentId": _text(request.body, "departmentId"),
            "managementTitleId": _text(request.body, "managementTitleId"),
            "jobTitle": _text(request.body, "jobTitle"),
            "managerName": _text(request.body, "managerName"),
            "currentFocus": _text(request.body, "currentFocus"),
        },
        refresh=False,
    )
    return {
        **summary,
        "applicationId": application.get("applicationId"),
        "applicationState": application.get("applicationState"),
        "applicationVersion": application.get("version"),
        "applicationMessage": "组织身份调整申请已提交，等待管理员确认",
        "membershipSubmittedAt": application.get("submittedAt"),
        "membershipRejectedReason": application.get("rejectionReason"),
    }


@router.post(r"me/org-membership/admin-claim")
def claim_admin(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    auth = compatibility.auth_state()
    user = auth.get("user") or {}
    if user.get("primaryRole") == "admin":
        return {
            **auth,
            "claimState": "already_admin",
            "claimMessage": "当前成员已经是组织管理员",
        }
    raise LocalRuntimeError(
        403,
        "admin_transfer_required",
        "组织已有管理员；请由现任管理员通过授权移交，不允许自行认领",
    )


@router.post(r"settings/org-model/backfill-task-links")
def reconcile_task_authority_links(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/model/backfill-task-links",
        payload={},
        refresh=False,
    )


@router.post(r"settings/legacy-scan")
def legacy_scan_policy(
    compatibility: Any,
    request: UiRequest,
    __: Any,
) -> dict[str, Any]:
    compatibility.runtime._current_context(require_ready=True)
    requested_path = _text(request.body, "path")
    return {
        "path": requested_path,
        "found": [],
        "entries": [],
        "message": "严格新版禁止扫描或读取旧数据库路径；未访问所填路径",
        "state": "blocked",
        "reasonCode": "legacy_read_forbidden",
        "retryable": False,
        "pathAccessed": False,
    }


@router.get(r"local-input-memory")
def local_input_memory(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    store = _input_memory_store(compatibility)
    authority_state = "ready"
    authority_message = ""
    try:
        public = compatibility.runtime.cloud_query(
            "/api/v2/organization-access/settings/local-input-memory"
        )
    except LocalRuntimeError as exc:
        if exc.code not in {
            "organization_required",
            "workspace_not_ready",
            "workspace_secret_missing",
            "workspace_session_invalid",
        }:
            raise
        public = None
        authority_state = "not_connected"
        authority_message = exc.message
    if public is not None and int(public.get("version") or 0) == 0:
        public = None
    return {
        **store.read(public),
        "authorityState": authority_state,
        "authorityMessage": authority_message,
        "publicPreferencePersisted": authority_state == "ready",
        "retryable": authority_state != "ready",
    }


def _save_input_memory(
    compatibility: Any,
    request: UiRequest,
    *,
    section: str,
) -> dict[str, Any]:
    store = _input_memory_store(compatibility)
    if section in {"aiSettings", "feishuIntegration"} and not (
        store.has_personal_boundary
    ):
        raise LocalRuntimeError(
            401,
            "personal_secret_identity_required",
            "保存个人 AI 或飞书凭据前必须重新登录并固定当前组织身份",
        )
    cloud_connected = True
    authority_message = ""
    try:
        current = compatibility.runtime.cloud_query(
            "/api/v2/organization-access/settings/local-input-memory"
        )
    except LocalRuntimeError as exc:
        if exc.code not in {
            "organization_required",
            "workspace_not_ready",
            "workspace_secret_missing",
            "workspace_session_invalid",
        }:
            raise
        cloud_connected = False
        authority_message = exc.message
        current = store.cached_public()
    if section == "cloudAuth":
        public = store.cloud_auth_public(current, request.body)
    elif section == "aiSettings":
        public = store.ai_public(current, request.body)
    else:
        public = store.feishu_public(current, request.body)
    cloud_public = {
        **public,
        "aiSettings": {
            "rememberCredential": bool(
                (public.get("aiSettings") or {}).get("rememberApiKey")
            )
        },
    }
    saved = (
        compatibility.runtime.cloud_command(
            "POST",
            "/api/v2/organization-access/settings/local-input-memory",
            payload={
                **cloud_public,
                "expectedVersion": int(current.get("version") or 0),
            },
            idempotency_key=request.idempotency_key,
            refresh_business=False,
        )
        if cloud_connected
        else public
    )
    try:
        if section == "cloudAuth":
            store.apply_cloud_auth_secret(request.body)
            store.cache_device_cloud_auth(saved)
        elif section == "aiSettings":
            store.apply_ai_secret(request.body)
        else:
            store.apply_feishu_secret(request.body)
    except PersonalSecretBoundaryRequired as exc:
        raise LocalRuntimeError(
            401,
            "personal_secret_identity_required",
            "保存个人凭据前必须重新登录并固定当前组织身份",
        ) from exc
    return {
        **store.read(saved),
        "authorityState": "ready" if cloud_connected else "not_connected",
        "authorityMessage": authority_message,
        "publicPreferencePersisted": cloud_connected,
        "secretPersistedLocally": True,
        "retryable": not cloud_connected,
    }


@router.post(r"local-input-memory/cloud-auth")
def save_cloud_input(_: Any, request: UiRequest, __: Any) -> dict[str, Any]:
    return _save_input_memory(_, request, section="cloudAuth")


@router.post(r"local-input-memory/ai")
def save_ai_input(_: Any, request: UiRequest, __: Any) -> dict[str, Any]:
    return _save_input_memory(_, request, section="aiSettings")


@router.post(r"local-input-memory/feishu")
def save_feishu_input(_: Any, request: UiRequest, __: Any) -> dict[str, Any]:
    return _save_input_memory(_, request, section="feishuIntegration")


def _maintenance_identity(compatibility: Any) -> tuple[Any, dict[str, Any]]:
    context = compatibility.runtime._current_context(require_ready=True)
    auth = compatibility.auth_state()
    user = auth.get("user") or {}
    if (
        not auth.get("authenticated")
        or user.get("accountStatus") != "active"
        or _text(user, "id") != context.membership_id
        or _text(user, "organizationId") != context.organization_id
    ):
        raise LocalRuntimeError(
            403,
            "maintenance_active_membership_required",
            "维护模式要求当前固定 WorkspaceContext 对应 active membership",
        )
    return context, user


def _maintenance_status(compatibility: Any) -> dict[str, Any]:
    try:
        context, user = _maintenance_identity(compatibility)
    except LocalRuntimeError as exc:
        if exc.status_code not in {401, 403, 409}:
            raise
        return {
            "available": False,
            "active": False,
            "canEnter": False,
            "canManagePermissions": False,
            "organizationId": None,
            "userId": None,
            "reason": exc.message,
        }
    is_official = context.organization_id == "org_yiyu_default"
    state = compatibility.maintenance_mode()
    return {
        **state,
        "available": is_official,
        "active": bool(state.get("active")) if is_official else False,
        "canEnter": is_official,
        "canManagePermissions": False,
        "organizationId": context.organization_id,
        "userId": _text(user, "id"),
        "reason": (
            None
            if is_official
            else "维护模式仅对 organization_id=org_yiyu_default 的 active membership 开放"
        ),
    }


def _set_maintenance_mode(
    compatibility: Any,
    *,
    active: bool,
) -> dict[str, Any]:
    context, user = _maintenance_identity(compatibility)
    if context.organization_id != "org_yiyu_default":
        raise LocalRuntimeError(
            403,
            "maintenance_official_organization_required",
            "维护模式仅对 organization_id=org_yiyu_default 的 active membership 开放",
        )
    before = compatibility.maintenance_mode()
    result = compatibility.maintenance_mode(active=active)
    try:
        with compatibility.runtime._connection() as connection:
            compatibility.runtime._insert_audit(
                connection,
                sandbox_id=context.sandbox_id,
                action=(
                    "maintenance.session.enter"
                    if active
                    else "maintenance.session.exit"
                ),
                resource_type="maintenance_session",
                resource_id=context.membership_id,
                actor_id=context.principal_id,
                summary={
                    "cloudInstanceId": context.cloud_instance_id,
                    "organizationId": context.organization_id,
                    "membershipId": context.membership_id,
                    "active": active,
                },
            )
            connection.commit()
    except Exception:
        compatibility.maintenance_mode(active=bool(before.get("active")))
        raise
    return {
        **result,
        "available": True,
        "canEnter": True,
        "canManagePermissions": False,
        "organizationId": context.organization_id,
        "userId": _text(user, "id"),
        "reason": None,
    }


@router.get(r"maintenance-mode/status")
def maintenance_status(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _maintenance_status(compatibility)


@router.post(r"maintenance-mode/enter")
def enter_maintenance_mode(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _set_maintenance_mode(compatibility, active=True)


@router.post(r"maintenance-mode/exit")
def exit_maintenance_mode(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    return _set_maintenance_mode(compatibility, active=False)


def _register_personal_setting(path_name: str) -> None:
    cloud_path = f"/api/v2/organization-access/settings/{path_name}"

    def read_setting(
        compatibility: Any,
        _: UiRequest,
        __: Any,
    ) -> dict[str, Any]:
        result, captured = _cloud_get_captured(compatibility, cloud_path)
        if path_name == "transcription-preference":
            result["sandboxId"] = captured.sandbox_id
        return result

    def save_setting(
        compatibility: Any,
        request: UiRequest,
        __: Any,
    ) -> dict[str, Any]:
        result = _cloud_cas_command(
            compatibility,
            request,
            method="POST",
            read_path=cloud_path,
        )
        if path_name == "transcription-preference":
            context = compatibility.runtime._current_context(require_ready=True)
            result["sandboxId"] = context.sandbox_id
        return result

    router.route("GET", rf"settings/{path_name}")(read_setting)
    router.route(
        "PUT" if path_name == "transcription-preference" else "POST",
        rf"settings/{path_name}",
    )(save_setting)


for _personal_setting_name in (
    "tasks",
    "client-workspace",
    "topics",
    "analysis-workbench",
    "handbook",
    "transcription-preference",
):
    _register_personal_setting(_personal_setting_name)


def _provider_settings_path(kind: str) -> str:
    return f"/api/v2/organization-access/settings/{kind}/effective"


@router.get(r"settings/speech-model")
def speech_model_settings(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        _provider_settings_path("speech-model"),
    )
    return result


@router.put(r"settings/speech-model")
def save_speech_model_settings(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _cloud_cas_command(
        compatibility,
        request,
        method="PUT",
        read_path=_provider_settings_path("speech-model"),
        scope_aware=True,
    )


@router.post(r"settings/speech-model/test")
def test_speech_model_settings(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/settings/speech-model/test",
        refresh=False,
    )


@router.get(r"settings/object-storage")
def object_storage_settings(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        _provider_settings_path("object-storage"),
    )
    return result


@router.put(r"settings/object-storage")
def save_object_storage_settings(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _cloud_cas_command(
        compatibility,
        request,
        method="PUT",
        read_path=_provider_settings_path("object-storage"),
        scope_aware=True,
    )


@router.post(r"settings/object-storage/test")
def test_object_storage_settings(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/settings/object-storage/test",
        refresh=False,
    )


@router.get(r"settings/main-chain-stability")
def main_chain_stability_settings(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        "/api/v2/organization-access/settings/main-chain-stability",
    )
    return result


@router.post(r"settings/main-chain-stability")
def save_main_chain_stability_settings(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _cloud_cas_command(
        compatibility,
        request,
        method="POST",
        read_path="/api/v2/organization-access/settings/main-chain-stability",
        scope_aware=True,
    )


@router.get(r"settings/system-admin")
def system_admin_settings(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        "/api/v2/organization-access/settings/system-admin",
    )
    return result


@router.post(r"settings/system-admin")
def save_system_admin_settings(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _cloud_cas_command(
        compatibility,
        request,
        method="POST",
        read_path="/api/v2/organization-access/settings/system-admin",
    )


@router.get(r"org/bots")
def bot_members(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        "/api/v2/organization-access/bots",
        query=request.query,
    )
    return result


@router.post(r"settings/org-model/intro-document")
def save_organization_intro_document(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    del compatibility
    file_path = _text(request.body, "filePath")
    markdown = str(request.body.get("markdownContent") or "")
    file_name = _text(request.body, "fileName", "title")
    file_type = ""
    if file_path:
        source = Path(file_path).expanduser().resolve()
        try:
            byte_size = source.stat().st_size
        except OSError as exc:
            raise LocalRuntimeError(
                404,
                "organization_intro_source_missing",
                "所选组织介绍资料不存在或无法读取",
            ) from exc
        if byte_size > 2 * 1024 * 1024:
            raise LocalRuntimeError(
                413,
                "organization_intro_source_too_large",
                "组织介绍资料不得超过 2 MiB",
            )
        file_name = source.name
        file_type = source.suffix.casefold().lstrip(".") or "txt"
        if source.suffix.casefold() == ".docx":
            markdown = LocalProjectMaterialsRepository._docx_text(source)
        elif source.suffix.casefold() in {
            ".md",
            ".txt",
            ".json",
            ".csv",
            ".tsv",
            ".xml",
            ".html",
        }:
            try:
                markdown = source.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise LocalRuntimeError(
                    415,
                    "organization_intro_encoding_unsupported",
                    "组织介绍资料必须是 UTF-8 文本或有效 Word 文档",
                ) from exc
            except OSError as exc:
                raise LocalRuntimeError(
                    422,
                    "organization_intro_source_unreadable",
                    "无法读取所选组织介绍资料",
                ) from exc
        else:
            raise LocalRuntimeError(
                415,
                "organization_intro_format_unsupported",
                "当前组织介绍仅支持 Markdown、UTF-8 文本和 Word 文档",
            )
    if not markdown.strip():
        raise LocalRuntimeError(
            422,
            "organization_intro_content_required",
            "组织介绍资料没有可保存的正文",
        )
    if len(markdown.encode("utf-8")) > 2 * 1024 * 1024:
        raise LocalRuntimeError(
            413,
            "organization_intro_content_too_large",
            "组织介绍正文不得超过 2 MiB",
        )
    file_name = file_name or "组织介绍.md"
    file_type = file_type or Path(file_name).suffix.casefold().lstrip(".") or "md"
    normalized = " ".join(markdown.split())
    return {
        "fileName": file_name,
        "fileType": file_type,
        "markdownContent": markdown,
        "normalizedText": normalized,
        "summary": normalized[:500],
        "contentHash": sha256_text(markdown),
        "uploadedBy": "",
        "uploadedAt": (
            datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        ),
        "authorityState": "local_draft",
        "sourceBodyStoredLocally": True,
        "sourceBodySentToCloud": False,
    }


@router.post(r"org/bots")
def create_bot_member(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/organization-access/bots",
    )


@router.get(r"org/bots/resolve")
def resolve_bot_member(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        "/api/v2/organization-access/bots/resolve",
        query={"handle": _text(request.query, "handle")},
    )
    return result


@router.get(r"org/bots/([^/]+)")
def bot_member(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        f"/api/v2/organization-access/bots/{match.group(1)}",
    )
    return result


@router.patch(r"org/bots/([^/]+)")
def update_bot_member(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    path = f"/api/v2/organization-access/bots/{match.group(1)}"
    return _cloud_cas_command(
        compatibility,
        request,
        method="PATCH",
        read_path=path,
    )


@router.post(r"org/bots/([^/]+)/rotate-token")
def rotate_bot_member_token(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    base = f"/api/v2/organization-access/bots/{match.group(1)}"
    return _cloud_cas_command(
        compatibility,
        request,
        method="POST",
        read_path=base,
        command_path=f"{base}/rotate-token",
    )


@router.get(r"org/bots/([^/]+)/permissions")
def bot_member_permissions(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        f"/api/v2/organization-access/bots/{match.group(1)}/permissions",
    )
    return result


@router.get(r"org/bots/([^/]+)/task-plans")
def bot_member_task_plans(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        f"/api/v2/organization-access/bots/{match.group(1)}/task-plans",
        query=request.query,
    )
    return result


@router.post(r"org/bots/([^/]+)/task-plans")
def create_bot_member_task_plan(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/organization-access/bots/{match.group(1)}/task-plans",
    )


@router.post(r"org/bots/task-plans/([^/]+)/decide")
def decide_bot_member_task_plan(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    plan_id = match.group(1)
    return _cloud_cas_command(
        compatibility,
        request,
        method="POST",
        read_path=(
            f"/api/v2/organization-access/bots/task-plans/{plan_id}/progress"
        ),
        command_path=(
            f"/api/v2/organization-access/bots/task-plans/{plan_id}/decide"
        ),
    )


@router.get(r"org/bots/task-plans/([^/]+)/progress")
def bot_member_task_plan_progress(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    result, _ = _cloud_get_captured(
        compatibility,
        (
            "/api/v2/organization-access/bots/task-plans/"
            f"{match.group(1)}/progress"
        ),
    )
    return result


def _register_missing(
    method: str,
    pattern: str,
    *,
    evidence: str,
    settings: bool = False,
) -> None:
    def handler(_: Any, request: UiRequest, __: Any) -> None:
        if settings:
            _setting_missing(request.path, evidence)
        _capability_missing(request.path, evidence)

    router.route(method, pattern)(handler)


for _method, _pattern, _evidence, _is_setting in (
    ("POST", r"settings/demo-data/load", "严格权威库禁止加载假演示数据", True),
    ("POST", r"settings/demo-data/clear", "严格权威库没有演示数据标记", True),
):
    _register_missing(
        _method,
        _pattern,
        evidence=_evidence,
        settings=_is_setting,
    )
