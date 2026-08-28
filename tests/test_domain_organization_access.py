from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.cloud_client import CloudClientError
from backend.app.local_input_memory import (
    LocalInputMemoryStore,
    PersonalSecretBoundaryRequired,
)
from backend.app.runtime import LocalRuntimeError, WorkspaceRuntime
from backend.app.secret_store import EncryptedFileSecretStore, MemorySecretStore
from backend.app.ui_compat import StrictUiCompatibility
from backend.app.ui_domains.organization_access import router as organization_router
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from cloud_backend.app.repositories.platform_integrations import (
    FEISHU_CONFIGURATION_KIND,
    PlatformIntegrationsRepository,
)
from strict_common.ids import sha256_text
from strict_common.schema import runtime_connection
from tests.strict_cloud_test_factory import (
    provision_test_organization,
    strict_cloud_test_client,
)


def _cloud(tmp_path: Path, name: str) -> tuple[TestClient, Path]:
    data_dir = tmp_path / name
    client, database, _ = strict_cloud_test_client(
        data_dir,
        bootstrap_token=f"{name}-bootstrap",
        cloud_instance_id=f"cloud-organization-access-{name.lower()}",
    )
    client.__enter__()
    return client, database


def _bootstrap(client: TestClient, name: str) -> dict[str, Any]:
    return provision_test_organization(
        client,
        organization_name=f"{name}组织",
        display_name=f"{name}管理员",
        email=f"{name.lower()}-admin@example.com",
        password="admin-password",
    )


def _auth(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _member(
    client: TestClient,
    admin: dict[str, Any],
    *,
    email: str,
) -> dict[str, Any]:
    department = client.post(
        "/api/v2/organization/departments",
        headers=_auth(admin["accessToken"], "department-create"),
        json={"name": "项目部", "expectedOrganizationVersion": 1},
    )
    assert department.status_code == 201, department.text
    invite = client.post(
        "/api/v2/organization/invites",
        headers=_auth(admin["accessToken"]),
        json={
            "inviteKind": "department",
            "targetId": department.json()["id"],
        },
    )
    assert invite.status_code == 201, invite.text
    joined = client.post(
        "/api/v2/auth/join",
        json={
            "inviteCode": invite.json()["inviteCode"],
            "displayName": "普通成员",
            "email": email,
            "password": "member-password",
        },
    )
    assert joined.status_code == 201, joined.text
    return {
        **joined.json(),
        "inviteCode": invite.json()["inviteCode"],
        "departmentId": department.json()["id"],
    }


def test_identity_isolation_permissions_and_member_commands(tmp_path: Path) -> None:
    cloud_a, _ = _cloud(tmp_path, "A")
    cloud_b, _ = _cloud(tmp_path, "B")
    try:
        admin_a = _bootstrap(cloud_a, "A")
        admin_b = _bootstrap(cloud_b, "B")
        member_a = _member(cloud_a, admin_a, email="member-a@example.com")

        resolved_a = cloud_a.get(
            "/api/v2/organization-access/invite/resolve",
            params={"code": member_a["inviteCode"]},
        )
        resolved_b = cloud_b.get(
            "/api/v2/organization-access/invite/resolve",
            params={"code": member_a["inviteCode"]},
        )
        assert resolved_a.json()["organizationId"] == admin_a["organizationId"]
        assert resolved_b.json() == {"valid": False, "message": "邀请码无效"}

        cross_cloud = cloud_b.get(
            "/api/v2/organization-access/members",
            headers=_auth(member_a["accessToken"]),
        )
        assert cross_cloud.status_code == 401
        assert cross_cloud.json()["error"]["code"] == "invalid_session"

        member_denied = cloud_a.post(
            f"/api/v2/organization-access/members/{admin_a['membershipId']}/disable",
            headers=_auth(member_a["accessToken"], "member-denied"),
        )
        assert member_denied.status_code == 403
        assert member_denied.json()["error"]["code"] == "admin_required"

        assigned = cloud_a.patch(
            f"/api/v2/organization-access/members/{member_a['membershipId']}/department",
            headers=_auth(admin_a["accessToken"], "member-department"),
            json={"departmentId": member_a["departmentId"]},
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["departmentId"] == member_a["departmentId"]

        promoted = cloud_a.patch(
            f"/api/v2/organization-access/members/{member_a['membershipId']}/role",
            headers=_auth(admin_a["accessToken"], "member-role"),
            json={"role": "admin"},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["primaryRole"] == "admin"
        idempotency_conflict = cloud_a.patch(
            f"/api/v2/organization-access/members/{member_a['membershipId']}/role",
            headers=_auth(admin_a["accessToken"], "member-role"),
            json={"role": "employee"},
        )
        assert idempotency_conflict.status_code == 409
        assert idempotency_conflict.json()["error"]["code"] == "idempotency_conflict"

        wrong_org_target = cloud_b.patch(
            f"/api/v2/organization-access/members/{member_a['membershipId']}/role",
            headers=_auth(admin_b["accessToken"], "wrong-org-target"),
            json={"role": "admin"},
        )
        assert wrong_org_target.status_code == 404
        assert wrong_org_target.json()["error"]["code"] == "membership_missing"
    finally:
        cloud_a.__exit__(None, None, None)
        cloud_b.__exit__(None, None, None)


def test_password_profile_relogin_and_secret_redaction(tmp_path: Path) -> None:
    client, database = _cloud(tmp_path, "Password")
    new_password = "new-member-password"
    try:
        admin = _bootstrap(client, "Password")
        member = _member(client, admin, email="profile-old@example.com")
        member_headers = _auth(member["accessToken"], "profile-update")

        profile = client.patch(
            "/api/v2/organization-access/profile",
            headers=member_headers,
            json={
                "fullName": "更新后的成员",
                "email": "profile-new@example.com",
                "phone": "13900000001",
            },
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["fullName"] == "更新后的成员"
        assert profile.json()["email"] == "profile-new@example.com"

        changed = client.post(
            "/api/v2/organization-access/password",
            headers=_auth(member["accessToken"], "password-change"),
            json={
                "currentPassword": "member-password",
                "newPassword": new_password,
            },
        )
        assert changed.status_code == 200, changed.text
        old_login = client.post(
            "/api/v2/auth/login",
            json={
                "identifier": "profile-new@example.com",
                "password": "member-password",
            },
        )
        new_login = client.post(
            "/api/v2/auth/login",
            json={
                "identifier": "profile-new@example.com",
                "password": new_password,
            },
        )
        assert old_login.status_code == 401
        assert new_login.status_code == 200

        raw_database = database.read_bytes()
        assert b"member-password" not in raw_database
        assert new_password.encode() not in raw_database
        with runtime_connection(database, "cloud") as connection:
            audit_text = "\n".join(
                str(row[0])
                for row in connection.execute(
                    "SELECT summary_json FROM audit_events"
                ).fetchall()
            )
        assert "member-password" not in audit_text
        assert new_password not in audit_text
    finally:
        client.__exit__(None, None, None)


class _AsgiCloud:
    def __init__(self, client: TestClient):
        self.client = client

    def _call(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = (
            {"Authorization": f"Bearer {access_token}"} if access_token else {}
        )
        response = self.client.request(method, path, headers=headers, json=json_body)
        if response.status_code >= 400:
            error = response.json()["error"]
            raise CloudClientError(
                response.status_code,
                error["code"],
                error["message"],
            )
        return response.json() if response.content else {}

    def handshake(self) -> dict[str, Any]:
        return self._call("GET", "/api/v2/handshake")

    def create_organization(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call(
            "POST",
            "/api/v2/auth/bootstrap-organization",
            json_body=payload,
        )

    def current_session(self, access_token: str) -> dict[str, Any]:
        return self._call(
            "GET",
            "/api/v2/session/current",
            access_token=access_token,
        )

    def business_snapshot(self, access_token: str) -> dict[str, Any]:
        return self._call(
            "GET",
            "/api/v2/business/snapshot",
            access_token=access_token,
        )

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        return self._call(
            "POST",
            "/api/v2/auth/refresh",
            json_body={"refreshToken": refresh_token},
        )

    def save_ai_config(
        self,
        access_token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.request_v2(
            "PUT",
            "/api/v2/settings/org-ai-config",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def ai_runtime_secret(self, access_token: str) -> dict[str, Any]:
        return self._call(
            "GET",
            "/api/v2/settings/org-ai-config/runtime-secret",
            access_token=access_token,
        )

    def request_v2(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        json_body: dict[str, Any] | None = None,
        query_params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        allow_array: bool = False,
    ) -> Any:
        headers = (
            {"Authorization": f"Bearer {access_token}"} if access_token else {}
        )
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        response = self.client.request(
            method,
            path,
            headers=headers,
            params=query_params,
            json=json_body,
        )
        if response.status_code >= 400:
            error = response.json()["error"]
            raise CloudClientError(
                response.status_code,
                error["code"],
                error["message"],
            )
        payload = response.json() if response.content else {}
        if not isinstance(payload, dict) and not (
            allow_array and isinstance(payload, list)
        ):
            raise CloudClientError(
                502,
                "cloud_response_invalid",
                "组织云响应结构不正确",
            )
        return payload


def test_workspace_missing_secret_requires_reauth_and_never_lists_draft(
    tmp_path: Path,
) -> None:
    cloud, _ = _cloud(tmp_path, "Workspace")
    try:
        adapter = _AsgiCloud(cloud)
        secrets = MemorySecretStore()
        runtime = WorkspaceRuntime(
            tmp_path / "strict-local.db",
            secrets,
            cloud_factory=lambda _: adapter,
        )
        runtime.create_organization(
            cloud_api_url="https://workspace.invalid",
            bootstrap_token="Workspace-bootstrap",
            organization_name="工作空间组织",
            display_name="管理员",
            email="workspace-admin@example.com",
            phone=None,
            password="workspace-password",
        )
        compatibility = StrictUiCompatibility(runtime)
        workspaces = compatibility.workspaces()
        assert len(workspaces["workspaces"]) == 1
        assert all(item["kind"] == "organization" for item in workspaces["workspaces"])
        assert workspaces["localDraftSummary"]["available"] is False
        sandbox_id = workspaces["workspaces"][0]["id"]

        secrets.delete(f"workspace-session:{sandbox_id}")
        with pytest.raises(LocalRuntimeError) as failure:
            compatibility.dispatch(
                "POST",
                f"workspaces/{sandbox_id}/activate",
                query={},
                body={},
                idempotency_key="switch-without-secret",
            )
        assert failure.value.code == "workspace_secret_missing"
        assert runtime.current()["runtimeStatus"] == "needs_login"
        listed = compatibility.workspaces()["workspaces"]
        assert listed[0]["requiresLogin"] is True
        assert listed[0]["cloudConnectionStatus"] == "needs_login"

    finally:
        cloud.__exit__(None, None, None)


def test_all_routes_are_owned_and_unconnected_settings_keep_evidence(
    tmp_path: Path,
) -> None:
    runtime = WorkspaceRuntime(
        tmp_path / "strict-local.db",
        MemorySecretStore(),
        cloud_factory=lambda _: (_ for _ in ()).throw(AssertionError("no cloud")),
    )
    compatibility = StrictUiCompatibility(runtime)
    routes = compatibility.domain_registry.routers[4].routes
    assert routes
    assert len({(route.method, route.pattern) for route in routes}) == len(routes)
    assert compatibility.workspaces()["workspaces"] == []

    with pytest.raises(LocalRuntimeError) as settings_error:
        compatibility.dispatch(
            "POST",
            "settings/tasks",
            query={},
            body={"defaultPriority": "high"},
            idempotency_key="settings-missing",
        )
    assert settings_error.value.code == "workspace_not_ready"

    with pytest.raises(LocalRuntimeError) as missing_evidence:
        compatibility.dispatch(
            "POST",
            "settings/org-model/backfill-task-links",
            query={},
            body={},
            idempotency_key="task-link-backfill-missing",
        )
    assert missing_evidence.value.code == "capability_not_connected"

    remembered = compatibility.dispatch(
        "POST",
        "local-input-memory/cloud-auth",
        query={},
        body={
            "rememberInputs": True,
            "email": "member@example.com",
            "password": "must-stay-in-local-secret-store",
        },
        idempotency_key="secret-memory-local",
    )
    assert remembered["cloudAuth"]["accounts"][0]["password"] == (
        "must-stay-in-local-secret-store"
    )
    assert remembered["authorityState"] == "ready"
    assert remembered["publicPreferencePersisted"] is False
    assert remembered["credentialBoundary"]["cloudAuthPasswords"] == "device"
    assert b"must-stay-in-local-secret-store" not in runtime.database_path.read_bytes()


def test_local_input_memory_personal_secrets_require_and_follow_composite_identity() -> None:
    secrets = MemorySecretStore()
    public = {
        "aiSettings": {"rememberCredential": True},
        "feishuIntegration": {
            "rememberInputs": True,
            "appId": "cli_personal",
        },
    }
    identity_a = {
        "cloud_instance_id": "cloud-a",
        "organization_id": "org-a",
        "principal_id": "principal-a",
        "membership_id": "member-a",
    }
    identity_b = {
        **identity_a,
        "membership_id": "member-b",
    }
    identity_other_org = {
        **identity_a,
        "organization_id": "org-b",
    }
    store_a = LocalInputMemoryStore(secrets, **identity_a)
    store_a.apply_ai_secret({"rememberApiKey": True, "apiKey": "ai-secret-a"})
    store_a.apply_feishu_secret(
        {"rememberInputs": True, "appSecret": "feishu-secret-a"}
    )
    assert store_a.read(public)["aiSettings"]["apiKey"] == "ai-secret-a"
    assert (
        store_a.read(public)["feishuIntegration"]["appSecret"]
        == "feishu-secret-a"
    )

    for isolated_identity in (identity_b, identity_other_org):
        isolated = LocalInputMemoryStore(secrets, **isolated_identity).read(public)
        assert isolated["aiSettings"]["apiKey"] == ""
        assert isolated["feishuIntegration"]["appSecret"] == ""

    unauthenticated = LocalInputMemoryStore(secrets)
    assert unauthenticated.read(public)["aiSettings"]["apiKey"] == ""
    assert unauthenticated.read(public)["feishuIntegration"]["appSecret"] == ""
    with pytest.raises(PersonalSecretBoundaryRequired):
        unauthenticated.apply_ai_secret(
            {"rememberApiKey": True, "apiKey": "must-not-overwrite"}
        )
    with pytest.raises(PersonalSecretBoundaryRequired):
        unauthenticated.apply_feishu_secret(
            {"rememberInputs": True, "appSecret": "must-not-overwrite"}
        )


def test_remembered_cloud_login_survives_the_authenticated_boundary() -> None:
    secrets = MemorySecretStore()
    identity = {
        "cloud_instance_id": "cloud-a",
        "organization_id": "org-a",
        "principal_id": "principal-a",
        "membership_id": "member-a",
    }
    authenticated = LocalInputMemoryStore(secrets, **identity)
    public = authenticated.cloud_auth_public(
        {},
        {
            "rememberInputs": True,
            "email": "member@example.com",
            "identifier": "member@example.com",
            "fullName": "成员",
        },
    )
    authenticated.apply_cloud_auth_secret(
        {
            "rememberInputs": True,
            "identifier": "member@example.com",
            "password": "remembered-password",
        }
    )
    LocalInputMemoryStore(secrets).cache_device_cloud_auth(
        {"cloudAuth": {"rememberInputs": False, "accounts": []}}
    )

    migrated_pre_authentication = LocalInputMemoryStore(secrets).read()
    migrated_account = migrated_pre_authentication["cloudAuth"]["accounts"][0]
    assert migrated_account["identifier"] == "member@example.com"
    assert migrated_account["password"] == "remembered-password"

    authenticated.read(public)

    pre_authentication = LocalInputMemoryStore(secrets).read()
    account = pre_authentication["cloudAuth"]["accounts"][0]
    assert account["identifier"] == "member@example.com"
    assert account["password"] == "remembered-password"

    cleared = authenticated.cloud_auth_public(
        public,
        {"rememberInputs": False},
    )
    authenticated.apply_cloud_auth_secret({"rememberInputs": False})
    authenticated.read(cleared)
    assert LocalInputMemoryStore(secrets).read()["cloudAuth"]["accounts"] == []


def test_gc01_compatibility_remembers_cloud_login_without_cloud_settings(
    tmp_path: Path,
) -> None:
    runtime = WorkspaceRuntime(
        tmp_path / "strict-local.db",
        MemorySecretStore(),
        cloud_factory=lambda _: (_ for _ in ()).throw(AssertionError("no cloud")),
    )
    compatibility = StrictUiCompatibility(runtime)
    remembered = compatibility.dispatch(
        "POST",
        "local-input-memory/cloud-auth",
        query={},
        body={
            "rememberInputs": True,
            "email": "member@example.com",
            "identifier": "member@example.com",
            "password": "remembered-password",
        },
        idempotency_key="remember-cloud-login",
    )
    assert remembered["cloudAuth"]["accounts"][0]["password"] == (
        "remembered-password"
    )
    reread = compatibility.dispatch(
        "GET",
        "local-input-memory",
        query={},
        body={},
        idempotency_key="read-cloud-login",
    )
    assert reread["cloudAuth"]["accounts"][0]["identifier"] == (
        "member@example.com"
    )
    assert reread["cloudAuth"]["accounts"][0]["password"] == (
        "remembered-password"
    )
    assert reread["publicPreferencePersisted"] is False
    assert b"remembered-password" not in runtime.database_path.read_bytes()


def test_recovery_catalog_creates_verified_database_component(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path, "Recovery")
    try:
        admin = _bootstrap(client, "Recovery")
        created = client.post(
            "/api/v2/organization-access/recovery-sets",
            headers=_auth(admin["accessToken"], "recovery-create"),
            json={"retentionDays": 7},
        )
        assert created.status_code == 200, created.text
        payload = created.json()
        assert payload["databaseVerified"] is True
        assert payload["wholeSystemVerified"] is False
        assert payload["coverage"] == "cloud_database_only"
        assert payload["backupPath"].startswith("strict-recovery://")

        repeated = client.post(
            "/api/v2/organization-access/recovery-sets",
            headers=_auth(admin["accessToken"], "recovery-create"),
            json={"retentionDays": 7},
        )
        assert repeated.status_code == 200
        replayed = repeated.json()
        assert replayed["idempotentReplay"] is True
        assert replayed["recoverySetId"] == payload["recoverySetId"]
        assert replayed["backupId"] == payload["backupId"]

        listed = client.get(
            "/api/v2/organization-access/recovery-sets",
            headers=_auth(admin["accessToken"]),
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["recoverySetId"] == payload["recoverySetId"]

        with runtime_connection(database, "cloud") as connection:
            row = connection.execute(
                """
                SELECT b.backup_ref, b.verified, r.status
                FROM backup_catalog b
                JOIN recovery_sets r
                  ON r.id = b.recovery_set_id
                WHERE b.id = ?
                """,
                (payload["backupId"],),
            ).fetchone()
        assert row is not None
        backup_path = Path(str(row["backup_ref"]))
        assert backup_path.is_file()
        assert backup_path.stat().st_size > 0
        assert int(row["verified"]) == 1
        assert row["status"] == "verified"
        assert oct(backup_path.stat().st_mode & 0o777) == "0o600"
    finally:
        client.__exit__(None, None, None)


def test_task_authority_reconciliation_is_audited_and_idempotent(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path, "TaskAuthority")
    try:
        admin = _bootstrap(client, "TaskAuthority")
        snapshot = client.get(
            "/api/v2/business/snapshot",
            headers=_auth(admin["accessToken"]),
        )
        project_id = snapshot.json()["projects"][0]["projectId"]
        task = client.post(
            "/api/v2/tasks",
            headers=_auth(admin["accessToken"], "task-authority-create"),
            json={"title": "核对直接任务权威", "projectId": project_id},
        )
        assert task.status_code == 201, task.text

        headers = _auth(admin["accessToken"], "task-authority-reconcile")
        first = client.post(
            "/api/v2/organization-access/model/backfill-task-links",
            headers=headers,
        )
        repeated = client.post(
            "/api/v2/organization-access/model/backfill-task-links",
            headers=headers,
        )
        assert first.status_code == 200, first.text
        assert repeated.json() == first.json()
        assert first.json()["state"] == "completed"
        assert first.json()["totalTasks"] == 1
        assert first.json()["linkedTasks"] == 1
        assert first.json()["createdLinks"] == 0
        assert first.json()["legacyLinkTableRequired"] is False

        with runtime_connection(database, "cloud", read_only=True) as connection:
            run_count = connection.execute(
                """
                SELECT COUNT(*) FROM reconciliation_runs
                WHERE registry_state_id = 'task_direct_authority'
                """
            ).fetchone()[0]
            audit_count = connection.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE action = 'organization.task_authority.reconciled'
                """
            ).fetchone()[0]
        assert run_count == 1
        assert audit_count == 1
    finally:
        client.__exit__(None, None, None)


def test_connected_membership_compatibility_and_legacy_policy_are_explicit(
    tmp_path: Path,
) -> None:
    cloud, database = _cloud(tmp_path, "MembershipCompat")
    try:
        adapter = _AsgiCloud(cloud)
        runtime = WorkspaceRuntime(
            tmp_path / "strict-local.db",
            MemorySecretStore(),
            cloud_factory=lambda _: adapter,
        )
        runtime.create_organization(
            cloud_api_url="https://membership.invalid",
            bootstrap_token="MembershipCompat-bootstrap",
            organization_name="成员兼容组织",
            display_name="管理员",
            email="membership-admin@example.com",
            phone=None,
            password="membership-password",
        )
        compatibility = StrictUiCompatibility(runtime)

        applied = compatibility.dispatch(
            "POST",
            "me/org-membership/apply",
            query={},
            body={"currentFocus": "验证组织身份调整闭环"},
            idempotency_key="membership-apply-active",
        )
        assert applied["membershipStatus"] == "approved"
        assert applied["applicationState"] == "pending"
        assert applied["applicationId"]

        summary = compatibility.dispatch(
            "GET",
            "me/org-membership",
            query={},
            body={},
            idempotency_key=None,
        )
        assert summary["membershipStatus"] == "approved"
        assert summary["applicationState"] == "pending"

        members = compatibility.dispatch(
            "GET",
            "admin/employees",
            query={},
            body={},
            idempotency_key=None,
        )
        current_user_id = compatibility.auth_state()["user"]["id"]
        current = next(item for item in members if item["id"] == current_user_id)
        assert current["membershipStatus"] == "approved"
        assert current["membershipApplicationState"] == "pending"

        approved = compatibility.dispatch(
            "POST",
            f"admin/employees/{current['id']}/approve",
            query={},
            body={"role": "admin"},
            idempotency_key="membership-adjustment-approve",
        )
        assert approved["membershipStatus"] == "approved"
        assert approved["membershipApplicationState"] == "approved"

        replay = compatibility.dispatch(
            "POST",
            f"admin/employees/{current['id']}/approve",
            query={},
            body={"role": "admin"},
            idempotency_key="membership-adjustment-approve",
        )
        assert replay["membershipApplicationState"] == "approved"

        with runtime_connection(database, "cloud") as connection:
            command_payload = connection.execute(
                """
                SELECT payload_json FROM command_envelopes
                WHERE command_type = 'organization.membership_application.submit'
                """
            ).fetchone()[0]
            application_count = connection.execute(
                "SELECT COUNT(*) FROM organization_membership_applications"
            ).fetchone()[0]
        persisted_payload = json.loads(command_payload)
        assert "inviteCode" not in persisted_payload
        assert "inviteCodeHash" in persisted_payload
        assert application_count == 1

        claimed = compatibility.dispatch(
            "POST",
            "me/org-membership/admin-claim",
            query={},
            body={},
            idempotency_key="admin-claim-existing",
        )
        assert claimed["authenticated"] is True
        assert claimed["claimState"] == "already_admin"

        legacy = compatibility.dispatch(
            "POST",
            "settings/legacy-scan",
            query={},
            body={"path": "/must/not/be/read"},
            idempotency_key="legacy-policy",
        )
        assert legacy["state"] == "blocked"
        assert legacy["reasonCode"] == "legacy_read_forbidden"
        assert legacy["pathAccessed"] is False
    finally:
        cloud.__exit__(None, None, None)


def test_membership_adjustment_approval_applies_title_manager_and_focus(
    tmp_path: Path,
) -> None:
    cloud, database = _cloud(tmp_path, "MembershipAdjustment")
    try:
        admin = _bootstrap(cloud, "MembershipAdjustment")
        member = _member(
            cloud,
            admin,
            email="membership-adjustment-member@example.com",
        )
        now = "2026-07-31T12:00:00+00:00"
        title_id = "title-membership-adjustment"
        with runtime_connection(database, "cloud") as connection:
            department_id = str(
                connection.execute(
                    """
                    SELECT department_id FROM department_memberships
                    WHERE organization_id = ? AND membership_id = ?
                      AND status = 'active'
                    """,
                    (admin["organizationId"], member["membershipId"]),
                ).fetchone()["department_id"]
            )
            connection.execute(
                """
                INSERT INTO management_titles (
                    title_id, organization_id, name, department_id,
                    lifecycle_state, version, created_at, updated_at
                ) VALUES (?, ?, '项目负责人', ?, 'active', 1, ?, ?)
                """,
                (
                    title_id,
                    admin["organizationId"],
                    department_id,
                    now,
                    now,
                ),
            )
            connection.commit()

        submitted = cloud.post(
            "/api/v2/organization-access/membership-applications",
            headers=_auth(member["accessToken"], "membership-adjustment-submit"),
            json={
                "managementTitleId": title_id,
                "managerName": "MembershipAdjustment管理员",
                "currentFocus": "完成日慈项目知识闭环",
            },
        )
        assert submitted.status_code == 200, submitted.text
        application = submitted.json()
        assert application["applicationState"] == "pending"
        assert application["requestedManagementTitleId"] == title_id

        decided = cloud.post(
            (
                "/api/v2/organization-access/membership-applications/"
                f"{application['applicationId']}/decide"
            ),
            headers=_auth(admin["accessToken"], "membership-adjustment-decide"),
            json={
                "decision": "approve",
                "expectedVersion": application["version"],
            },
        )
        assert decided.status_code == 200, decided.text
        assert decided.json()["applicationState"] == "approved"

        with runtime_connection(database, "cloud") as connection:
            title_holder = connection.execute(
                """
                SELECT status FROM management_title_memberships
                WHERE organization_id = ? AND title_id = ?
                  AND membership_id = ?
                """,
                (
                    admin["organizationId"],
                    title_id,
                    member["membershipId"],
                ),
            ).fetchone()
            reporting = connection.execute(
                """
                SELECT manager_membership_id, lifecycle_state
                FROM organization_reporting_lines
                WHERE organization_id = ? AND report_membership_id = ?
                  AND line_type = 'business'
                """,
                (admin["organizationId"], member["membershipId"]),
            ).fetchone()
            focus = connection.execute(
                """
                SELECT current_focus FROM organization_memberships
                WHERE organization_id = ? AND membership_id = ?
                """,
                (admin["organizationId"], member["membershipId"]),
            ).fetchone()["current_focus"]
            audit_count = connection.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE action = 'organization.membership_application.approve'
                  AND resource_id = ?
                """,
                (application["applicationId"],),
            ).fetchone()[0]
            outbox_count = connection.execute(
                """
                SELECT COUNT(*) FROM delivery_outbox
                WHERE event_type =
                  'organization.membership_application.approve.committed'
                  AND aggregate_id = ?
                """,
                (application["applicationId"],),
            ).fetchone()[0]
        assert title_holder["status"] == "active"
        assert reporting["manager_membership_id"] == admin["membershipId"]
        assert reporting["lifecycle_state"] == "active"
        assert focus == "完成日慈项目知识闭环"
        assert audit_count == 1
        assert outbox_count == 1
    finally:
        cloud.__exit__(None, None, None)


def test_feishu_registry_is_scoped_and_never_persists_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        PlatformIntegrationsRepository,
        "_verify_feishu_tenant_token",
        lambda self, **kwargs: (True, None, "飞书应用凭据验证成功"),
    )
    registered_relay: dict[str, str] = {}
    monkeypatch.setattr(
        PlatformIntegrationsRepository,
        "_register_feishu_oauth_relay_session",
        lambda self, **kwargs: registered_relay.update(kwargs),
    )
    cloud_a, database_a = _cloud(tmp_path, "FeishuA")
    cloud_b, _ = _cloud(tmp_path, "FeishuB")
    secret = "feishu-secret-must-not-persist"
    try:
        admin_a = _bootstrap(cloud_a, "FeishuA")
        admin_b = _bootstrap(cloud_b, "FeishuB")
        member_a = _member(
            cloud_a,
            admin_a,
            email="feishu-member-a@example.com",
        )

        configured = cloud_a.post(
            "/api/v2/organization-access/feishu/bot",
            headers=_auth(admin_a["accessToken"], "feishu-app-register"),
            json={"appId": "cli_feishu_a", "appSecret": secret},
        )
        assert configured.status_code == 200, configured.text
        assert configured.json()["appId"] == "cli_feishu_a"
        assert configured.json()["ready"] is True
        assert configured.json()["hasAppSecret"] is True
        assert configured.json()["state"] == "ready"
        assert configured.json()["secretSource"] == "scoped_configuration_encrypted"
        platform_status = cloud_a.get(
            "/api/v2/platform-integrations/query",
            headers=_auth(admin_a["accessToken"]),
            params={
                "resourcePath": "org-integrations/feishu",
                "authorizationScope": "organization",
            },
        )
        assert platform_status.status_code == 200, platform_status.text
        assert platform_status.json()["resource"]["appId"] == "cli_feishu_a"
        assert platform_status.json()["resource"]["state"] == "ready"

        member_denied = cloud_a.post(
            "/api/v2/organization-access/feishu/bot",
            headers=_auth(member_a["accessToken"], "member-feishu-app"),
            json={"appId": "cli_forbidden", "appSecret": secret},
        )
        assert member_denied.status_code == 403
        assert member_denied.json()["error"]["code"] == "admin_required"

        started = cloud_a.post(
            "/api/v2/organization-access/feishu/member-authorization/start",
            headers=_auth(member_a["accessToken"], "member-feishu-start"),
            json={},
        )
        assert started.status_code == 200, started.text
        assert started.json()["qrReady"] is True
        assert started.json()["retryable"] is True
        assert started.json()["qrBlockedReason"] is None
        assert started.json()["authorizeUrl"].startswith(
            "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
        )
        assert started.json()["callbackUrl"] == (
            "https://yiyu.love/oauth/feishu/member/callback"
        )
        authorize_query = parse_qs(urlparse(started.json()["authorizeUrl"]).query)
        assert authorize_query["redirect_uri"] == [started.json()["callbackUrl"]]
        assert registered_relay["state_token"] == started.json()["state"]
        assert registered_relay["claim_secret"]

        member_status = cloud_a.get(
            "/api/v2/organization-access/feishu/member-authorization",
            headers=_auth(member_a["accessToken"]),
        )
        platform_member_status = cloud_a.get(
            "/api/v2/platform-integrations/query",
            headers=_auth(member_a["accessToken"]),
            params={
                "resourcePath": "me/feishu-authorization",
                "authorizationScope": "personal",
            },
        )
        admin_status = cloud_a.get(
            "/api/v2/organization-access/feishu/member-authorization",
            headers=_auth(admin_a["accessToken"]),
        )
        assert member_status.json()["state"] == "processing"
        assert (
            member_status.json()["blockedReason"]
            == "authorization_pending"
        )
        assert platform_member_status.status_code == 200
        assert (
            platform_member_status.json()["resource"]["state"]
            == "processing"
        )
        assert admin_status.json()["state"] == "not_connected"

        monkeypatch.setattr(
            PlatformIntegrationsRepository,
            "_claim_feishu_oauth_relay_code",
            lambda self, **kwargs: {
                "status": "authorized",
                "code": "relay-authorization-code",
            },
        )

        def feishu_oauth_result(self, method, url, **kwargs):
            if url.endswith("/oauth/token"):
                return {
                    "access_token": "relay-user-access-token",
                    "refresh_token": "relay-user-refresh-token",
                    "expires_in": 3600,
                    "refresh_token_expires_in": 7200,
                    "scope": "offline_access docx:document:readonly",
                }
            if url.endswith("/user_info"):
                return {
                    "data": {
                        "open_id": "ou_relay_member",
                        "name": "Relay 成员",
                    }
                }
            raise AssertionError(f"unexpected Feishu OAuth URL: {url}")

        monkeypatch.setattr(
            PlatformIntegrationsRepository,
            "_feishu_provider_json",
            feishu_oauth_result,
        )
        claimed = cloud_a.post(
            "/api/v2/organization-access/feishu/member-authorization/claim",
            headers=_auth(member_a["accessToken"], "member-feishu-claim"),
            json={},
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["linked"] is True
        assert claimed.json()["openId"] == "ou_relay_member"

        other_cloud = cloud_b.get(
            "/api/v2/organization-access/feishu/bot",
            headers=_auth(admin_b["accessToken"]),
        )
        assert other_cloud.status_code == 200
        assert other_cloud.json()["appId"] == ""

        delivery = cloud_a.post(
            "/api/v2/organization-access/feishu/delivery-profile",
            headers=_auth(member_a["accessToken"], "member-delivery"),
            json={"mobilePresented": True},
        )
        assert delivery.status_code == 200
        assert delivery.json()["readyForNotifications"] is True
        assert delivery.json()["retryable"] is False

        with runtime_connection(database_a, "cloud") as connection:
            resources = connection.execute(
                """
                SELECT resource_kind, remote_id, retention_state
                FROM external_provider_resources
                WHERE organization_id = ? AND provider = 'feishu'
                ORDER BY resource_kind
                """,
                (admin_a["organizationId"],),
            ).fetchall()
            effect_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM external_side_effects
                WHERE organization_id = ?
                """,
                (admin_a["organizationId"],),
            ).fetchone()[0]
        assert {row["resource_kind"] for row in resources} == {
            "application",
            "member_authorization",
            "member_delivery_profile",
        }
        assert effect_count == 4
        with runtime_connection(database_a, "cloud") as connection:
            configuration = connection.execute(
                """
                SELECT scope_kind, encrypted_secret_bundle
                FROM scoped_configuration_records
                WHERE organization_id = ? AND configuration_kind = ?
                """,
                (admin_a["organizationId"], FEISHU_CONFIGURATION_KIND),
            ).fetchone()
            assert configuration is not None
            assert configuration["scope_kind"] == "organization"
            assert configuration["encrypted_secret_bundle"] is not None
        assert secret.encode() not in database_a.read_bytes()
        assert b"relay-user-access-token" not in database_a.read_bytes()
        assert b"relay-user-refresh-token" not in database_a.read_bytes()
    finally:
        cloud_a.__exit__(None, None, None)
        cloud_b.__exit__(None, None, None)


def test_org_model_updates_authoritative_structure_and_plans_with_cas(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path, "OrgModel")
    try:
        admin = _bootstrap(client, "OrgModel")
        member = _member(
            client,
            admin,
            email="org-model-member@example.com",
        )
        model_response = client.get(
            "/api/v2/organization-access/model",
            headers=_auth(admin["accessToken"]),
        )
        assert model_response.status_code == 200, model_response.text
        model = model_response.json()
        assert model["authorityStates"]["identityStructure"]["state"] == "ready"
        assert (
            model["authorityStates"]["unfrozenSemanticFields"]["state"]
            == "ready"
        )
        assert (
            model["authorityStates"]["roleProcessAutomation"]["state"]
            == "blocked"
        )
        intro_markdown = "# 更新后的组织\n\n组织使命与服务对象。"
        model["organization"].update(
            {
                "name": "更新后的组织",
                "annualGoal": "完成严格新版闭环",
                "annualStrategyYear": "2026",
                "annualStrategy": "以组织云权威驱动协作",
                    "quarterlyFocus": ["接通任务治理", "验证组织隔离"],
                    "leaderUserId": admin["membershipId"],
                    "leaderName": model["organization"]["leaderName"],
                "introDocument": {
                    "fileName": "组织介绍.md",
                    "fileType": "md",
                    "markdownContent": intro_markdown,
                    "normalizedText": "更新后的组织 组织使命与服务对象。",
                    "summary": "组织使命与服务对象。",
                    "contentHash": sha256_text(intro_markdown),
                    "uploadedBy": admin["membershipId"],
                    "uploadedAt": "",
                },
                "quarterPlans": [
                    {
                        "id": "org_quarter_plan_strict_1",
                        "year": "2026",
                        "quarter": "Q3",
                        "theme": "全面接通",
                        "objective": "完成严格新版组织治理",
                        "keyResults": ["任务规则可执行"],
                        "keyActions": ["迁移 v4"],
                        "majorRisks": ["跨组织引用"],
                        "updatedAt": "",
                    }
                ],
            }
        )
        department_model = next(
            item
            for item in model["departments"]
            if item["id"] == member["departmentId"]
        )
        department_model.update(
            {
                "color": "#123456",
                "mission": "交付真实闭环",
                "businessContext": "严格新版",
                "teamContext": "组织协作",
                "quarterlyFocus": ["任务治理"],
                "collaborationDepartmentIds": [],
                "quarterPlan": {
                    "year": "2026",
                    "quarter": "Q3",
                    "objective": "完成部门验收",
                    "deliverables": ["验收报告"],
                    "successMetrics": ["零假保存"],
                    "majorRisks": ["权限错位"],
                    "updatedAt": "",
                },
            }
        )
        role_id = "role_strict_1"
        model["roles"].append(
            {
                "id": role_id,
                "departmentId": member["departmentId"],
                "name": "项目主管",
                "level": "supervisor",
                "visibilityScope": "department",
                "managerRoleId": None,
                "isManager": True,
                "goal": "完成任务治理",
                "responsibilities": ["审批任务"],
                "shouldAvoid": ["跨组织修改"],
                "collaborationRoleIds": [],
                "taskEditScope": "manager",
                "canApproveTasks": True,
                "canReassignTasks": True,
                "canChangeDeadline": True,
                "sortOrder": 1,
                "active": True,
                "holderBotId": None,
                "updatedAt": "",
            }
        )
        member_binding = next(
            item
            for item in model["bindings"]
            if item["userId"] == member["membershipId"]
        )
        member_binding.update(
            {
                "managerUserId": admin["membershipId"],
                "primaryRoleId": role_id,
                "projectRoleLabels": ["项目负责人"],
                "currentFocus": "严格接通",
                "taskEditScope": "self",
            }
        )
        model["reportingLines"] = [
            {
                "id": "reporting_strict_1",
                "managerUserId": admin["membershipId"],
                "reportUserId": member["membershipId"],
                "lineType": "business",
                "approvesTasks": True,
                "canAdjustTasks": True,
                "canChangeDeadline": True,
                "canReassignTasks": True,
                "isCrossDepartmentApprover": False,
                "active": True,
                "updatedAt": "",
            }
        ]
        model["taskControlRules"] = [
            {
                "id": "task_rule_strict_1",
                "name": "直属负责人控制",
                "controlLevel": "leader_control",
                "departmentId": member["departmentId"],
                "roleTemplateId": role_id,
                "contentEditableBy": "manager",
                "deadlineEditableBy": "manager",
                "ownerEditableBy": "manager",
                "cancellableBy": "manager",
                "requireCollabConfirmation": True,
                "defaultApproverUserId": admin["membershipId"],
                "active": True,
                "updatedAt": "",
            }
        ]
        model["roleProcessTemplates"] = [
            {
                "id": "role_process_strict_1",
                "roleTemplateId": role_id,
                "name": "任务创建跟进",
                "triggerType": "task_created",
                "triggerCondition": "创建任务后",
                "keySteps": ["确认负责人", "跟进交付"],
                "collaborationStep": "邀请协作者",
                "approvalStep": "负责人审批",
                "outputArtifact": "任务回执",
                "commonBlockers": ["资料不足"],
                "active": True,
                "updatedAt": "",
            }
        ]
        model["focusItems"] = [
            {
                "id": "focus_strict_1",
                "periodKey": "2026-Q3",
                "title": "完成严格接通",
                "statement": "只保存权威可承载字段",
                "ownerUserId": member["membershipId"],
                "priority": "high",
                "status": "active",
                "evidenceKeywords": ["strict", "authority"],
                "updatedAt": "",
            }
        ]
        model["departmentPlans"] = [
            {
                "id": "department_plan_strict_1",
                "departmentId": member["departmentId"],
                "weekLabel": "2026-W31",
                "ownerUserId": member["membershipId"],
                "summary": "本周严格计划",
                "majorRisks": ["schema 漂移"],
                "dependencies": ["审计"],
                "status": "active",
                "items": [
                    {
                        "id": "department_plan_item_strict_1",
                        "focusItemId": "focus_strict_1",
                        "title": "跑完严格审计",
                        "statement": "不得假保存",
                        "ownerUserId": member["membershipId"],
                        "status": "active",
                        "expectedOutput": "审计通过",
                        "sortOrder": 0,
                        "updatedAt": "",
                    }
                ],
                "updatedAt": "",
            }
        ]

        saved = client.put(
            "/api/v2/organization-access/model",
            headers=_auth(admin["accessToken"], "org-model-save"),
            json=model,
        )
        assert saved.status_code == 200, saved.text
        saved_model = saved.json()
        assert saved_model["organization"]["name"] == "更新后的组织"
        assert saved_model["organization"]["annualGoal"] == "完成严格新版闭环"
        assert saved_model["organization"]["quarterPlans"][0]["id"] == (
            "org_quarter_plan_strict_1"
        )
        assert saved_model["organization"]["introDocument"]["contentHash"] == (
            sha256_text(intro_markdown)
        )
        assert saved_model["reportingLines"][0]["id"] == "reporting_strict_1"
        assert saved_model["taskControlRules"][0]["id"] == "task_rule_strict_1"
        assert saved_model["roleProcessTemplates"][0]["id"] == (
            "role_process_strict_1"
        )
        assert saved_model["focusItems"][0]["id"] == "focus_strict_1"
        assert saved_model["focusItems"][0]["version"] == 1
        assert (
            saved_model["departmentPlans"][0]["items"][0]["focusItemId"]
            == "focus_strict_1"
        )

        member_snapshot = client.get(
            "/api/v2/business/snapshot",
            headers=_auth(member["accessToken"]),
        )
        assert member_snapshot.status_code == 200, member_snapshot.text
        project_id = member_snapshot.json()["projects"][0]["projectId"]
        task_response = client.post(
            "/api/v2/tasks",
            headers=_auth(member["accessToken"], "governed-task-create"),
            json={"title": "受组织规则控制的任务", "projectId": project_id},
        )
        assert task_response.status_code == 201, task_response.text
        task = task_response.json()["task"]
        self_edit = client.patch(
            f"/api/v2/tasks/{task['taskId']}",
            headers=_auth(member["accessToken"], "governed-task-self-edit"),
            json={
                "expectedVersion": task["version"],
                "title": "不应由本人改写",
            },
        )
        assert self_edit.status_code == 403
        assert self_edit.json()["error"]["code"] == "task_control_rule_forbidden"
        manager_edit = client.patch(
            f"/api/v2/tasks/{task['taskId']}",
            headers=_auth(admin["accessToken"], "governed-task-manager-edit"),
            json={
                "expectedVersion": task["version"],
                "title": "由直属负责人改写",
            },
        )
        assert manager_edit.status_code == 200, manager_edit.text
        reviewed = client.post(
            f"/api/v2/workflow/tasks/{task['taskId']}/actions/review_approved",
            headers=_auth(admin["accessToken"], "governed-task-manager-review"),
            json={"expectedVersion": manager_edit.json()["task"]["version"]},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["task"]["version"] == (
            manager_edit.json()["task"]["version"] + 1
        )
        with runtime_connection(database, "cloud", read_only=True) as connection:
            audit = connection.execute(
                """
                SELECT summary_json
                FROM audit_events
                WHERE organization_id = ? AND action = 'task.updated'
                  AND resource_id = ?
                ORDER BY created_at DESC, audit_id DESC
                LIMIT 1
                """,
                (admin["organizationId"], task["taskId"]),
            ).fetchone()
            review_audit = connection.execute(
                """
                SELECT summary_json
                FROM audit_events
                WHERE organization_id = ? AND action = 'task.review_approved'
                  AND resource_id = ?
                ORDER BY created_at DESC, audit_id DESC
                LIMIT 1
                """,
                (admin["organizationId"], task["taskId"]),
            ).fetchone()
        assert audit is not None
        assert json.loads(str(audit["summary_json"]))["taskControlRules"] == [
            {
                "action": "content",
                "requiredActorScope": "manager",
                "ruleId": "task_rule_strict_1",
                "ruleVersion": 1,
            }
        ]
        assert review_audit is not None
        assert json.loads(str(review_audit["summary_json"]))["taskControlRules"] == [
            {
                "action": "approve",
                "requiredActorScope": "authorized_approver",
                "ruleId": "task_rule_strict_1",
                "ruleVersion": 1,
            }
        ]

        replayed = client.put(
            "/api/v2/organization-access/model",
            headers=_auth(admin["accessToken"], "org-model-save"),
            json=model,
        )
        assert replayed.status_code == 200
        assert replayed.json()["organization"]["version"] == saved_model[
            "organization"
        ]["version"]

        stale_model = client.put(
            "/api/v2/organization-access/model",
            headers=_auth(admin["accessToken"], "org-model-stale-save"),
            json=model,
        )
        assert stale_model.status_code == 409
        assert stale_model.json()["error"]["code"] == "organization_version_conflict"

        denied = client.put(
            "/api/v2/organization-access/model",
            headers=_auth(member["accessToken"], "member-org-model"),
            json=saved_model,
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "admin_required"

        invalid_reporting = {
            **saved_model,
            "reportingLines": [
                {
                    **saved_model["reportingLines"][0],
                    "managerUserId": member["membershipId"],
                    "reportUserId": member["membershipId"],
                }
            ],
        }
        rejected = client.put(
            "/api/v2/organization-access/model",
            headers=_auth(admin["accessToken"], "org-model-invalid-reporting"),
            json=invalid_reporting,
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["code"] == "reporting_line_self_reference"

        with runtime_connection(database, "cloud") as connection:
            plan_kinds = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT json_extract(attributes_json, '$.orgModelKind')
                    FROM organization_plans
                    WHERE organization_id = ?
                    """,
                    (admin["organizationId"],),
                ).fetchall()
            }
        assert plan_kinds == {
            "focus_item",
            "department_plan",
            "organization_quarter_plan",
            "department_quarter_plan",
        }
    finally:
        client.__exit__(None, None, None)


def test_org_intro_upload_is_read_locally_before_model_save(
    tmp_path: Path,
) -> None:
    source = tmp_path / "组织介绍.md"
    source.write_text("# 日慈基金会\n\n项目背景正文。", encoding="utf-8")

    draft = organization_router.dispatch(
        object(),
        UiRequest(
            method="POST",
            path="settings/org-model/intro-document",
            query={},
            body={"filePath": str(source), "title": "组织介绍"},
            idempotency_key="local-intro-read",
        ),
    )

    assert draft["fileName"] == "组织介绍.md"
    assert draft["markdownContent"] == "# 日慈基金会\n\n项目背景正文。"
    assert draft["contentHash"] == sha256_text(draft["markdownContent"])
    assert draft["authorityState"] == "local_draft"
    assert draft["sourceBodyStoredLocally"] is True
    assert draft["sourceBodySentToCloud"] is False


def test_legacy_identity_actions_return_reauth_redirect_dtos(
    tmp_path: Path,
) -> None:
    runtime = WorkspaceRuntime(
        tmp_path / "strict-local.db",
        MemorySecretStore(),
        cloud_factory=lambda _: (_ for _ in ()).throw(AssertionError("no cloud")),
    )
    compatibility = StrictUiCompatibility(runtime)

    local_login = compatibility.dispatch(
        "POST",
        "local-auth/login",
        query={},
        body={"email": "legacy@example.com", "password": "not-stored"},
        idempotency_key="legacy-login",
    )
    assert local_login["authenticated"] is False
    assert local_login["reauthRequired"] is True
    assert local_login["redirectTo"] == "login"

    selected = compatibility.dispatch(
        "POST",
        "auth/select-organization",
        query={},
        body={
            "cloudInstanceId": "cloud_exact",
            "organizationId": "org_exact",
        },
        idempotency_key="legacy-select",
    )
    assert selected["reasonCode"] == (
        "organization_selection_reauthentication_required"
    )
    assert selected["requestedIdentity"] == {
        "cloudInstanceId": "cloud_exact",
        "organizationId": "org_exact",
    }

    workspace = compatibility.dispatch(
        "POST",
        "workspaces",
        query={},
        body={"name": "未连接组织草稿"},
        idempotency_key="legacy-workspace",
    )
    assert workspace["workspaces"] == []
    assert workspace["actionRequired"] == "reauth"


def test_maintenance_uses_exact_official_org_active_membership_and_audits() -> None:
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        def commit(self) -> None:
            return None

    class FakeRuntime:
        def __init__(self, organization_id: str):
            self.context = SimpleNamespace(
                sandbox_id="sandbox_exact",
                cloud_instance_id="cloud_exact",
                organization_id=organization_id,
                principal_id="principal_exact",
                membership_id="membership_exact",
            )
            self.audits: list[dict[str, Any]] = []

        def _current_context(self, *, require_ready: bool = True):
            assert require_ready is True
            return self.context

        def _connection(self) -> FakeConnection:
            return FakeConnection()

        def _insert_audit(self, _: Any, **payload: Any) -> None:
            self.audits.append(payload)

    class FakeCompatibility:
        def __init__(self, organization_id: str, *, active: bool = True):
            self.runtime = FakeRuntime(organization_id)
            self.active_membership = active
            self.state = False

        def auth_state(self) -> dict[str, Any]:
            return {
                "authenticated": True,
                "user": {
                    "id": "membership_exact",
                    "organizationId": self.runtime.context.organization_id,
                    "accountStatus": (
                        "active" if self.active_membership else "disabled"
                    ),
                    "primaryRole": "employee",
                },
            }

        def maintenance_mode(
            self,
            *,
            active: bool | None = None,
        ) -> dict[str, Any]:
            if active is not None:
                self.state = active
            return {
                "available": True,
                "active": self.state,
                "canEnter": True,
                "canManagePermissions": False,
            }

    def dispatch(
        compatibility: Any,
        method: str,
        path: str,
    ) -> dict[str, Any]:
        return organization_router.dispatch(
            compatibility,
            UiRequest(
                method=method,
                path=path,
                query={},
                body={},
                idempotency_key=f"{method}:{path}",
            ),
        )

    official = FakeCompatibility("org_yiyu_default")
    entered = dispatch(official, "POST", "maintenance-mode/enter")
    assert entered["active"] is True
    assert entered["canEnter"] is True
    assert entered["canManagePermissions"] is False
    assert official.runtime.audits[0]["action"] == "maintenance.session.enter"
    assert official.runtime.audits[0]["summary"] == {
        "cloudInstanceId": "cloud_exact",
        "organizationId": "org_yiyu_default",
        "membershipId": "membership_exact",
        "active": True,
    }

    exited = dispatch(official, "POST", "maintenance-mode/exit")
    assert exited["active"] is False
    assert official.runtime.audits[1]["action"] == "maintenance.session.exit"

    other = FakeCompatibility("org_star_cluster")
    other_status = dispatch(other, "GET", "maintenance-mode/status")
    assert other_status["available"] is False
    assert other_status["canEnter"] is False
    with pytest.raises(LocalRuntimeError) as wrong_org:
        dispatch(other, "POST", "maintenance-mode/enter")
    assert wrong_org.value.code == "maintenance_official_organization_required"

    inactive = FakeCompatibility("org_yiyu_default", active=False)
    with pytest.raises(LocalRuntimeError) as inactive_member:
        dispatch(inactive, "POST", "maintenance-mode/enter")
    assert inactive_member.value.code == "maintenance_active_membership_required"


def test_scoped_settings_encrypt_secrets_and_isolate_personal_overrides(
    tmp_path: Path,
) -> None:
    cloud_a, database_a = _cloud(tmp_path, "ScopedA")
    cloud_b, _ = _cloud(tmp_path, "ScopedB")
    organization_secret = "organization-speech-secret-value"
    personal_secret = "personal-speech-secret-value"
    try:
        admin_a = _bootstrap(cloud_a, "ScopedA")
        admin_b = _bootstrap(cloud_b, "ScopedB")
        member_a = _member(
            cloud_a,
            admin_a,
            email="scoped-member-a@example.com",
        )

        initial = cloud_a.get(
            "/api/v2/organization-access/settings/tasks",
            headers=_auth(member_a["accessToken"]),
        )
        assert initial.status_code == 200, initial.text
        assert initial.json()["defaultPriority"] == "normal"
        assert initial.json()["expectedVersion"] == 0

        saved_task = cloud_a.post(
            "/api/v2/organization-access/settings/tasks",
            headers=_auth(member_a["accessToken"], "personal-task-setting"),
            json={"defaultPriority": "high", "expectedVersion": 0},
        )
        assert saved_task.status_code == 200, saved_task.text
        assert saved_task.json()["defaultPriority"] == "high"
        assert saved_task.json()["version"] == 1
        replayed_task = cloud_a.post(
            "/api/v2/organization-access/settings/tasks",
            headers=_auth(member_a["accessToken"], "personal-task-setting"),
            json={"defaultPriority": "high", "expectedVersion": 0},
        )
        assert replayed_task.status_code == 200
        assert replayed_task.json() == saved_task.json()

        admin_personal = cloud_a.get(
            "/api/v2/organization-access/settings/tasks",
            headers=_auth(admin_a["accessToken"]),
        )
        assert admin_personal.status_code == 200
        assert admin_personal.json()["defaultPriority"] == "normal"
        assert admin_personal.json()["version"] == 0

        workbench_preferences = cloud_a.get(
            "/api/v2/organization-access/settings/analysis-workbench",
            headers=_auth(member_a["accessToken"]),
        )
        assert workbench_preferences.status_code == 200
        workbench_payload = workbench_preferences.json()
        assert (
            workbench_payload["authorityStates"]["personalPreferences"]["state"]
            == "ready"
        )
        assert (
            workbench_payload["authorityStates"]["businessKnowledge"]["state"]
            == "blocked"
        )
        assert "diagnosisProfiles" not in workbench_payload
        assert "diagnosisProfiles" in workbench_payload["unsupportedFields"]

        intro = cloud_a.post(
            "/api/v2/organization-access/settings/org-model/intro-document",
            headers=_auth(admin_a["accessToken"], "organization-intro"),
            json={
                "fileName": "组织介绍.md",
                "markdownContent": "# 组织介绍\n\n严格权威正文。",
                "expectedVersion": 0,
            },
        )
        assert intro.status_code == 200, intro.text
        assert intro.json()["contentHash"]
        assert intro.json()["uploadedBy"] == admin_a["membershipId"]
        intro_read = cloud_a.get(
            "/api/v2/organization-access/settings/org-model/intro-document",
            headers=_auth(admin_a["accessToken"]),
        )
        assert intro_read.status_code == 200
        assert intro_read.json()["markdownContent"] == "# 组织介绍\n\n严格权威正文。"
        member_intro = cloud_a.get(
            "/api/v2/organization-access/settings/org-model/intro-document",
            headers=_auth(member_a["accessToken"]),
        )
        assert member_intro.status_code == 403

        system_admin = cloud_a.post(
            "/api/v2/organization-access/settings/system-admin",
            headers=_auth(admin_a["accessToken"], "system-admin-policy"),
            json={
                "allowBusinessSettingsForEmployees": False,
                "expectedVersion": 0,
            },
        )
        assert system_admin.status_code == 200, system_admin.text
        assert system_admin.json()["allowBusinessSettingsForEmployees"] is False
        member_system_admin = cloud_a.get(
            "/api/v2/organization-access/settings/system-admin",
            headers=_auth(member_a["accessToken"]),
        )
        assert member_system_admin.status_code == 403

        organization_speech = cloud_a.put(
            "/api/v2/organization-access/settings/speech-model/effective",
            headers=_auth(admin_a["accessToken"], "org-speech"),
            json={
                "scopeKind": "organization",
                "provider": "volcano",
                "modelId": "speech-v1",
                "extraConfig": {"region": "cn-north-1"},
                "enabled": True,
                "credentials": {"apiKey": organization_secret},
                "expectedVersion": 0,
            },
        )
        assert organization_speech.status_code == 200, organization_speech.text
        assert organization_speech.json()["credentials"] == {}
        assert organization_speech.json()["hasCredentials"] is True
        assert organization_secret not in organization_speech.text

        probe = cloud_a.post(
            "/api/v2/organization-access/settings/speech-model/test",
            headers=_auth(admin_a["accessToken"], "speech-probe"),
            json={"credentials": {"apiKey": "must-not-be-recorded"}},
        )
        assert probe.status_code == 200, probe.text
        assert probe.json()["state"] == "registered_not_probed"
        assert probe.json()["success"] is False
        probe_replay = cloud_a.post(
            "/api/v2/organization-access/settings/speech-model/test",
            headers=_auth(admin_a["accessToken"], "speech-probe"),
            json={"credentials": {"apiKey": "different-transient-value"}},
        )
        assert probe_replay.status_code == 200
        assert probe_replay.json() == probe.json()

        inherited = cloud_a.get(
            "/api/v2/organization-access/settings/speech-model/effective",
            headers=_auth(member_a["accessToken"]),
        )
        assert inherited.status_code == 200
        assert inherited.json()["effectiveScopeKind"] == "organization"
        assert inherited.json()["modelId"] == "speech-v1"
        assert inherited.json()["credentials"] == {}

        personal_speech = cloud_a.put(
            "/api/v2/organization-access/settings/speech-model/effective",
            headers=_auth(member_a["accessToken"], "personal-speech"),
            json={
                "scopeKind": "personal",
                "provider": "xunfei",
                "modelId": "personal-model",
                "extraConfig": {},
                "enabled": True,
                "credentials": {"apiKey": personal_secret},
                "expectedVersion": 0,
            },
        )
        assert personal_speech.status_code == 200, personal_speech.text
        assert personal_speech.json()["credentials"] == {}
        effective_personal = cloud_a.get(
            "/api/v2/organization-access/settings/speech-model/effective",
            headers=_auth(member_a["accessToken"]),
        )
        assert effective_personal.json()["effectiveScopeKind"] == "personal"
        assert effective_personal.json()["modelId"] == "personal-model"

        member_org_write = cloud_a.put(
            "/api/v2/organization-access/settings/speech-model/effective",
            headers=_auth(member_a["accessToken"], "forbidden-org-speech"),
            json={
                "scopeKind": "organization",
                "provider": "volcano",
                "modelId": "forbidden",
                "extraConfig": {},
                "enabled": True,
                "credentials": {},
                "expectedVersion": 1,
            },
        )
        assert member_org_write.status_code == 403
        assert member_org_write.json()["error"]["code"] == "admin_required"

        stale = cloud_a.put(
            "/api/v2/organization-access/settings/speech-model/effective",
            headers=_auth(admin_a["accessToken"], "stale-org-speech"),
            json={
                "scopeKind": "organization",
                "provider": "volcano",
                "modelId": "stale",
                "extraConfig": {},
                "enabled": True,
                "credentials": {},
                "expectedVersion": 0,
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "configuration_version_conflict"

        other_cloud = cloud_b.get(
            "/api/v2/organization-access/settings/speech-model/effective",
            headers=_auth(admin_b["accessToken"]),
        )
        assert other_cloud.status_code == 200
        assert other_cloud.json()["provider"] == ""
        assert other_cloud.json()["hasCredentials"] is False

        raw = database_a.read_bytes()
        assert organization_secret.encode() not in raw
        assert personal_secret.encode() not in raw
        with runtime_connection(database_a, "cloud") as connection:
            rows = connection.execute(
                """
                SELECT scope_kind, encrypted_secret_bundle, secret_fingerprint
                FROM scoped_configuration_records
                WHERE configuration_kind = 'speech_model'
                ORDER BY scope_kind
                """
            ).fetchall()
            audit_text = "\n".join(
                str(row[0])
                for row in connection.execute(
                    "SELECT summary_json FROM audit_events"
                ).fetchall()
            )
            command_text = "\n".join(
                str(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM command_envelopes"
                ).fetchall()
            )
        assert {row["scope_kind"] for row in rows} == {
            "organization",
            "personal",
        }
        assert all(row["encrypted_secret_bundle"] for row in rows)
        assert all(row["secret_fingerprint"] for row in rows)
        assert organization_secret not in audit_text + command_text
        assert personal_secret not in audit_text + command_text
    finally:
        cloud_a.__exit__(None, None, None)
        cloud_b.__exit__(None, None, None)


def test_ai_routing_is_real_encrypted_org_config_with_explicit_execution_state(
    tmp_path: Path,
) -> None:
    cloud, database = _cloud(tmp_path, "AiRouting")
    remote_secret = "remote-profile-secret-must-not-leak"
    try:
        admin = _bootstrap(cloud, "AiRouting")
        member = _member(
            cloud,
            admin,
            email="ai-routing-member@example.com",
        )
        payload = {
            "advancedAiRoutingEnabled": True,
            "aiModelMode": "local_first",
            "aiModelProfiles": {
                "local_text_deep": {
                    "enabled": True,
                    "provider": "openai_compatible",
                    "providerLabel": "远端深度模型",
                    "baseUrl": "https://models.example.com/v1",
                    "model": "deep-model",
                    "capability": "deep_analysis",
                    "isLocal": False,
                },
                "local_fast": {
                    "enabled": True,
                    "provider": "openai_compatible",
                    "providerLabel": "本地 Ollama",
                    "baseUrl": "http://127.0.0.1:11434/v1",
                    "model": "qwen3:8b",
                    "capability": "fast_structured",
                    "isLocal": True,
                },
            },
            "aiModelProfileApiKeys": {
                "local_text_deep": remote_secret,
            },
            "clearAiModelProfileApiKeys": [],
            "expectedVersion": 0,
        }
        headers = _auth(admin["accessToken"], "ai-routing-save")
        saved = cloud.post(
            "/api/v2/organization-access/settings/ai-routing",
            headers=headers,
            json=payload,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["executionState"] == "ready"
        assert saved.json()["activeExecutionAuthority"] == (
            "organization_ai_configs"
        )
        assert saved.json()["aiModelProfiles"]["local_fast"]["hasApiKey"] is False
        assert remote_secret not in saved.text

        replay = cloud.post(
            "/api/v2/organization-access/settings/ai-routing",
            headers=headers,
            json={**payload, "expectedVersion": 1},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == saved.json()

        inherited = cloud.get(
            "/api/v2/organization-access/settings/ai-routing",
            headers=_auth(member["accessToken"]),
        )
        assert inherited.status_code == 200, inherited.text
        assert inherited.json()["aiModelMode"] == "local_first"
        assert remote_secret not in inherited.text
        runtime_secret = cloud.get(
            (
                "/api/v2/organization-access/settings/ai-routing/"
                "runtime-secret"
            ),
            headers=_auth(member["accessToken"]),
        )
        assert runtime_secret.status_code == 200, runtime_secret.text
        assert runtime_secret.json()["organizationId"] == member["organizationId"]
        assert (
            runtime_secret.json()["profiles"]["local_text_deep"]["apiKey"]
            == remote_secret
        )
        denied = cloud.post(
            "/api/v2/organization-access/settings/ai-routing",
            headers=_auth(member["accessToken"], "member-routing-denied"),
            json={**payload, "expectedVersion": 1},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "admin_required"

        remote_without_key = cloud.post(
            "/api/v2/organization-access/settings/ai-routing",
            headers=_auth(admin["accessToken"], "remote-key-clear"),
            json={
                **payload,
                "aiModelProfileApiKeys": {},
                "clearAiModelProfileApiKeys": ["local_text_deep"],
                "expectedVersion": 1,
            },
        )
        assert remote_without_key.status_code == 422
        assert remote_without_key.json()["error"]["code"] == (
            "remote_ai_profile_key_required"
        )
    finally:
        cloud.__exit__(None, None, None)

    raw = database.read_bytes()
    assert remote_secret.encode() not in raw
    with runtime_connection(database, "cloud", read_only=True) as connection:
        row = connection.execute(
            """
            SELECT encrypted_secret_bundle, public_config_json
            FROM scoped_configuration_records
            WHERE configuration_kind = 'organization_ai_routing'
            """
        ).fetchone()
        audit = "\n".join(
            str(item[0])
            for item in connection.execute(
                """
                SELECT summary_json FROM audit_events
                WHERE action =
                    'configuration.organization_ai_routing.saved'
                """
            ).fetchall()
        )
        commands = "\n".join(
            str(item[0])
            for item in connection.execute(
                """
                SELECT payload_json FROM command_envelopes
                WHERE command_type =
                    'configuration.organization_ai_routing.saved'
                """
            ).fetchall()
        )
    assert row is not None
    assert row["encrypted_secret_bundle"]
    assert remote_secret not in str(row["public_config_json"])
    assert remote_secret not in audit
    assert remote_secret not in commands


def test_local_input_memory_splits_personal_public_and_local_secrets(
    tmp_path: Path,
) -> None:
    cloud, database = _cloud(tmp_path, "InputMemory")
    secret_dir = tmp_path / "local-secrets"
    password = "remembered-password-value"
    api_key = "remembered-ai-key-value"
    app_secret = "remembered-feishu-secret-value"
    try:
        adapter = _AsgiCloud(cloud)
        secrets = EncryptedFileSecretStore(secret_dir, "input-memory-test")
        runtime = WorkspaceRuntime(
            tmp_path / "input-memory-local.db",
            secrets,
            cloud_factory=lambda _: adapter,
        )
        runtime.create_organization(
            cloud_api_url="https://input-memory.invalid",
            bootstrap_token="InputMemory-bootstrap",
            organization_name="输入记忆组织",
            display_name="管理员",
            email="input-memory-admin@example.com",
            phone=None,
            password="admin-password",
        )
        compatibility = StrictUiCompatibility(runtime)
        directory = compatibility.dispatch(
            "POST",
            "organization-directory/sync",
            query={},
            body={},
            idempotency_key="directory-read-only-verification",
        )
        assert directory["status"] == "verified"
        assert directory["state"] == "ready"
        assert directory["mutationExecuted"] is False
        assert directory["verificationKind"] == "read_only_authority_check"
        cloud_saved = compatibility.dispatch(
            "POST",
            "local-input-memory/cloud-auth",
            query={},
            body={
                "rememberInputs": True,
                "email": "input-memory-admin@example.com",
                "identifier": "input-memory-admin@example.com",
                "fullName": "输入记忆管理员",
                "password": password,
            },
            idempotency_key="input-memory-cloud",
        )
        ai_saved = compatibility.dispatch(
            "POST",
            "local-input-memory/ai",
            query={},
            body={"rememberApiKey": True, "apiKey": api_key},
            idempotency_key="input-memory-ai",
        )
        feishu_saved = compatibility.dispatch(
            "POST",
            "local-input-memory/feishu",
            query={},
            body={
                "rememberInputs": True,
                "appId": "cli_input_memory",
                "callbackMode": "cloud_relay",
                "customCallbackUrl": "",
                "appSecret": app_secret,
            },
            idempotency_key="input-memory-feishu",
        )
        assert cloud_saved["cloudAuth"]["accounts"][0]["password"] == password
        assert ai_saved["aiSettings"]["apiKey"] == api_key
        assert feishu_saved["feishuIntegration"]["appSecret"] == app_secret
        assert cloud_saved["authorityState"] == "ready"
        assert ai_saved["credentialBoundary"]["aiApiKey"] == "personal_workspace"
        assert (
            feishu_saved["credentialBoundary"]["feishuAppSecret"]
            == "personal_workspace"
        )

        replay = compatibility.dispatch(
            "POST",
            "local-input-memory/feishu",
            query={},
            body={
                "rememberInputs": True,
                "appId": "cli_input_memory",
                "callbackMode": "cloud_relay",
                "customCallbackUrl": "",
                "appSecret": app_secret,
            },
            idempotency_key="input-memory-feishu",
        )
        assert replay == feishu_saved

        restored_runtime = WorkspaceRuntime(
            runtime.database_path,
            EncryptedFileSecretStore(secret_dir, "input-memory-test"),
            cloud_factory=lambda _: adapter,
        )
        restored = StrictUiCompatibility(restored_runtime).dispatch(
            "GET",
            "local-input-memory",
            query={},
            body={},
            idempotency_key="input-memory-read",
        )
        assert restored["cloudAuth"]["accounts"][0]["password"] == password
        assert restored["aiSettings"]["apiKey"] == api_key
        assert restored["feishuIntegration"]["appSecret"] == app_secret
    finally:
        cloud.__exit__(None, None, None)

    assert password.encode() not in runtime.database_path.read_bytes()
    assert api_key.encode() not in runtime.database_path.read_bytes()
    assert app_secret.encode() not in runtime.database_path.read_bytes()
    raw_cloud = database.read_bytes()
    assert password.encode() not in raw_cloud
    assert api_key.encode() not in raw_cloud
    assert app_secret.encode() not in raw_cloud
    with runtime_connection(database, "cloud", read_only=True) as connection:
        row = connection.execute(
            """
            SELECT scope_kind, public_config_json, encrypted_secret_bundle
            FROM scoped_configuration_records
            WHERE configuration_kind = 'local_input_memory'
            """
        ).fetchone()
    assert row is not None
    assert row["scope_kind"] == "personal"
    assert row["encrypted_secret_bundle"] is None
    assert "input-memory-admin@example.com" in row["public_config_json"]


def test_full_settings_uses_main_ai_cas_and_reports_partial_routing_failure(
    tmp_path: Path,
) -> None:
    cloud, database = _cloud(tmp_path, "SettingsCas")
    try:
        adapter = _AsgiCloud(cloud)
        runtime = WorkspaceRuntime(
            tmp_path / "settings-cas-local.db",
            MemorySecretStore(),
            cloud_factory=lambda _: adapter,
        )
        runtime.create_organization(
            cloud_api_url="https://settings-cas.invalid",
            bootstrap_token="SettingsCas-bootstrap",
            organization_name="设置 CAS 组织",
            display_name="管理员",
            email="settings-cas-admin@example.com",
            phone=None,
            password="admin-password",
        )
        compatibility = StrictUiCompatibility(runtime)
        original_command = runtime.cloud_command

        def fail_routing(
            method: str,
            path: str,
            **kwargs: Any,
        ) -> dict[str, Any]:
            if path.endswith("/settings/ai-routing"):
                raise LocalRuntimeError(
                    409,
                    "configuration_version_conflict",
                    "高级路由已被其他管理员更新",
                )
            return original_command(method, path, **kwargs)

        runtime.cloud_command = fail_routing  # type: ignore[method-assign]
        partial = compatibility.dispatch(
            "POST",
            "settings",
            query={},
            body={
                "aiProvider": "openai_compatible",
                "aiBaseUrl": "https://settings-cas.example.com/v1",
                "aiModel": "strict-main",
                "aiConfigVersion": 0,
                "apiKey": "settings-cas-secret",
                "advancedAiRoutingEnabled": True,
                "aiModelMode": "auto",
                "aiModelProfiles": {},
            },
            idempotency_key="settings-cas-partial",
        )
        assert partial["mutationOutcome"] == {
            "state": "partial",
            "mainAiConfig": "committed",
            "mainAiConfigVersion": 1,
            "advancedAiRouting": "failed",
            "errorCode": "configuration_version_conflict",
            "message": "高级路由已被其他管理员更新",
            "retryable": True,
        }
        assert partial["settings"]["aiConfigVersion"] == 1

        runtime.cloud_command = original_command  # type: ignore[method-assign]
        with pytest.raises(LocalRuntimeError) as stale:
            compatibility.dispatch(
                "POST",
                "settings",
                query={},
                body={
                    "aiProvider": "openai_compatible",
                    "aiBaseUrl": "https://stale.example.com/v1",
                    "aiModel": "stale-main",
                    "aiConfigVersion": 0,
                    "apiKey": "stale-secret",
                },
                idempotency_key="settings-cas-stale",
            )
        assert stale.value.code == "version_conflict"

        with pytest.raises(LocalRuntimeError) as invalid:
            compatibility.dispatch(
                "POST",
                "settings",
                query={},
                body={
                    "aiConfigVersion": "not-a-version",
                    "aiProvider": "openai_compatible",
                    "aiBaseUrl": "https://invalid.example.com/v1",
                    "aiModel": "invalid-main",
                    "apiKey": "invalid-secret",
                },
                idempotency_key="settings-cas-invalid-version",
            )
        assert invalid.value.status_code == 422
        assert invalid.value.code == "organization_ai_version_invalid"
    finally:
        cloud.__exit__(None, None, None)

    with runtime_connection(database, "cloud", read_only=True) as connection:
        row = connection.execute(
            """
            SELECT config_version, base_url
            FROM organization_ai_configs
            """
        ).fetchone()
    assert row is not None
    assert row["config_version"] == 1
    assert row["base_url"] == "https://settings-cas.example.com/v1"


def test_full_renderer_settings_post_preserves_unchanged_main_ai_key(
    tmp_path: Path,
) -> None:
    cloud, database = _cloud(tmp_path, "FullSettings")
    main_secret = "main-ai-secret-must-be-preserved"
    try:
        adapter = _AsgiCloud(cloud)
        runtime = WorkspaceRuntime(
            tmp_path / "full-settings-local.db",
            MemorySecretStore(),
            cloud_factory=lambda _: adapter,
        )
        runtime.create_organization(
            cloud_api_url="https://full-settings.invalid",
            bootstrap_token="FullSettings-bootstrap",
            organization_name="完整设置组织",
            display_name="管理员",
            email="full-settings-admin@example.com",
            phone=None,
            password="admin-password",
        )
        compatibility = StrictUiCompatibility(runtime)
        profiles = {
            "local_fast": {
                "enabled": True,
                "provider": "openai_compatible",
                "providerLabel": "本地快速模型",
                "baseUrl": "http://127.0.0.1:11434/v1",
                "model": "qwen3:8b",
                "capability": "fast_structured",
                "isLocal": True,
            }
        }
        first = compatibility.dispatch(
            "POST",
            "settings",
            query={},
            body={
                "currentOperatorId": "",
                "cloudApiUrl": "https://full-settings.invalid",
                "aiProvider": "openai_compatible",
                "aiProviderLabel": "组织主模型",
                "aiBaseUrl": "https://main-model.example.com/v1",
                "aiModel": "main-model",
                "apiKey": main_secret,
                "clearApiKey": False,
                "advancedAiRoutingEnabled": True,
                "aiModelMode": "local_first",
                "aiModelProfiles": profiles,
                "aiModelProfileApiKeys": {},
                "clearAiModelProfileApiKeys": [],
            },
            idempotency_key="full-settings-first",
        )
        assert first["settings"]["advancedAiRoutingEnabled"] is True
        assert first["settings"]["advancedAiRoutingExecutionState"] == (
            "ready"
        )
        fingerprint = first["settings"]["aiFingerprint"]

        second = compatibility.dispatch(
            "POST",
            "settings",
            query={},
            body={
                "currentOperatorId": "",
                "cloudApiUrl": "https://full-settings.invalid",
                "aiProvider": "openai_compatible",
                "aiProviderLabel": "组织主模型",
                "aiBaseUrl": "https://main-model.example.com/v1",
                "aiModel": "main-model",
                "clearApiKey": False,
                "advancedAiRoutingEnabled": True,
                "aiModelMode": "local_first",
                "aiModelProfiles": profiles,
                "clearAiModelProfileApiKeys": [],
            },
            idempotency_key="full-settings-second",
        )
        assert second["settings"]["aiFingerprint"] == fingerprint
        assert second["settings"]["aiBaseUrl"] == (
            "https://main-model.example.com/v1"
        )
        assert second["settings"]["advancedAiRoutingExecutionState"] == (
            "ready"
        )

        local_main = compatibility.dispatch(
            "POST",
            "settings",
            query={},
            body={
                "aiProvider": "openai_compatible",
                "aiBaseUrl": "http://127.0.0.1:11434/v1",
                "aiModel": "qwen3:8b",
                "clearApiKey": True,
                "advancedAiRoutingEnabled": False,
                "aiModelMode": "local_only",
                "aiModelProfiles": profiles,
                "clearAiModelProfileApiKeys": [],
            },
            idempotency_key="full-settings-local-main",
        )
        assert local_main["settings"]["aiBaseUrl"] == (
            "http://127.0.0.1:11434/v1"
        )
        assert local_main["settings"]["aiConfigured"] is True
    finally:
        cloud.__exit__(None, None, None)

    with runtime_connection(database, "cloud", read_only=True) as connection:
        main = connection.execute(
            """
            SELECT config_version, base_url, encrypted_api_key
            FROM organization_ai_configs
            """
        ).fetchone()
        commands = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM command_envelopes
            WHERE command_type = 'organization_ai.update'
            """
        ).fetchone()
    assert main is not None
    assert main["config_version"] == 2
    assert main["base_url"] == "http://127.0.0.1:11434/v1"
    assert main["encrypted_api_key"]
    assert int(commands["count"]) == 2
    assert main_secret.encode() not in database.read_bytes()


def test_bot_authority_idempotency_permissions_plans_and_cross_org_isolation(
    tmp_path: Path,
) -> None:
    cloud_a, database_a = _cloud(tmp_path, "BotA")
    cloud_b, _ = _cloud(tmp_path, "BotB")
    try:
        admin_a = _bootstrap(cloud_a, "BotA")
        admin_b = _bootstrap(cloud_b, "BotB")
        member_a = _member(cloud_a, admin_a, email="bot-member-a@example.com")
        create_payload = {
            "display_name": "严格机器人",
            "department_id": member_a["departmentId"],
            "description": "只使用冻结授权对象",
            "report_to_creator": True,
            "enabled_capabilities": [
                "clarification_resolution.propose",
                "workspace_file_write.request",
            ],
        }
        created = cloud_a.post(
            "/api/v2/organization-access/bots",
            headers=_auth(admin_a["accessToken"], "bot-create-idempotent"),
            json=create_payload,
        )
        assert created.status_code == 200, created.text
        first = created.json()
        assert len(first["token_plain"]) >= 32
        assert first["tokenAlreadyIssued"] is False
        first_token = first["token_plain"]

        replayed = cloud_a.post(
            "/api/v2/organization-access/bots",
            headers=_auth(admin_a["accessToken"], "bot-create-idempotent"),
            json=create_payload,
        )
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["id"] == first["id"]
        assert "token_plain" not in replayed.json()
        assert replayed.json()["tokenAlreadyIssued"] is True

        listed = cloud_a.get(
            "/api/v2/organization-access/bots",
            headers=_auth(member_a["accessToken"]),
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()["total"] == 1
        assert listed.json()["items"][0]["actor_type"] == "organization_bot"

        member_create = cloud_a.post(
            "/api/v2/organization-access/bots",
            headers=_auth(member_a["accessToken"], "member-bot-create"),
            json=create_payload,
        )
        assert member_create.status_code == 403
        assert member_create.json()["error"]["code"] == "admin_required"

        cross_org = cloud_b.get(
            f"/api/v2/organization-access/bots/{first['id']}",
            headers=_auth(admin_b["accessToken"]),
        )
        assert cross_org.status_code == 404
        assert cross_org.json()["error"]["code"] == "bot_missing"

        permissions = cloud_a.get(
            f"/api/v2/organization-access/bots/{first['id']}/permissions",
            headers=_auth(member_a["accessToken"]),
        )
        assert permissions.status_code == 200, permissions.text
        enabled = {
            item["capability_key"]
            for item in permissions.json()["capabilities"]
            if item["enabled"]
        }
        assert enabled == {
            "clarification_resolution.propose",
            "workspace_file_write.request",
        }

        rotated = cloud_a.post(
            f"/api/v2/organization-access/bots/{first['id']}/rotate-token",
            headers=_auth(admin_a["accessToken"], "bot-rotate-idempotent"),
            json={"expectedVersion": 1},
        )
        assert rotated.status_code == 200, rotated.text
        second_token = rotated.json()["token_plain"]
        assert second_token != first_token
        assert rotated.json()["tokenAlreadyIssued"] is False
        rotate_replay = cloud_a.post(
            f"/api/v2/organization-access/bots/{first['id']}/rotate-token",
            headers=_auth(admin_a["accessToken"], "bot-rotate-idempotent"),
            json={"expectedVersion": 1},
        )
        assert rotate_replay.status_code == 200, rotate_replay.text
        assert "token_plain" not in rotate_replay.json()
        assert rotate_replay.json()["tokenAlreadyIssued"] is True
        assert rotate_replay.json()["version"] == 2

        plan_payload = {
            "plan_title": "形成澄清方案",
            "plan_text": "仅形成提案，不执行外部副作用",
            "required_modules": [],
            "steps": [{"module": "clarify", "action": "提问"}],
            "expected_outputs": ["澄清清单"],
            "approval_required": True,
        }
        planned = cloud_a.post(
            f"/api/v2/organization-access/bots/{first['id']}/task-plans",
            headers=_auth(member_a["accessToken"], "bot-plan-idempotent"),
            json=plan_payload,
        )
        assert planned.status_code == 200, planned.text
        assert planned.json()["status"] == "pending_approval"
        plan_id = planned.json()["ai_task_plan_id"]
        plan_replay = cloud_a.post(
            f"/api/v2/organization-access/bots/{first['id']}/task-plans",
            headers=_auth(member_a["accessToken"], "bot-plan-idempotent"),
            json=plan_payload,
        )
        assert plan_replay.status_code == 200
        assert plan_replay.json()["ai_task_plan_id"] == plan_id

        member_decision = cloud_a.post(
            f"/api/v2/organization-access/bots/task-plans/{plan_id}/decide",
            headers=_auth(member_a["accessToken"], "member-plan-decision"),
            json={
                "decision": "approve",
                "decided_by": admin_a["membershipId"],
                "expectedVersion": 1,
            },
        )
        assert member_decision.status_code == 403
        assert member_decision.json()["error"]["code"] == "admin_required"

        approved = cloud_a.post(
            f"/api/v2/organization-access/bots/task-plans/{plan_id}/decide",
            headers=_auth(admin_a["accessToken"], "admin-plan-decision"),
            json={
                "decision": "approve",
                "decided_by": "spoofed-member",
                "expectedVersion": 1,
            },
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["approved_by"] == admin_a["membershipId"]
        assert approved.json()["status"] == "approved"

        progress = cloud_a.get(
            f"/api/v2/organization-access/bots/task-plans/{plan_id}/progress",
            headers=_auth(member_a["accessToken"]),
        )
        assert progress.status_code == 200, progress.text
        assert progress.json()["execution_status"] == "not_started"
        assert progress.json()["version"] == 2

        raw = database_a.read_bytes()
        assert first_token.encode() not in raw
        assert second_token.encode() not in raw
        with runtime_connection(database_a, "cloud") as connection:
            bot_count = connection.execute(
                """
                SELECT COUNT(*)
                FROM organization_bot_profiles
                WHERE organization_id = ?
                """,
                (admin_a["organizationId"],),
            ).fetchone()[0]
            principal_kind = connection.execute(
                """
                SELECT p.principal_kind
                FROM identity_principals AS p
                JOIN organization_bot_profiles AS b
                  ON b.principal_id = p.principal_id
                WHERE b.bot_id = ?
                """,
                (first["id"],),
            ).fetchone()[0]
            leaked = "\n".join(
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT payload_json FROM command_envelopes
                    UNION ALL
                    SELECT summary_json FROM audit_events
                    UNION ALL
                    SELECT payload_json FROM delivery_outbox
                    """
                ).fetchall()
            )
        assert bot_count == 1
        assert principal_kind == "bot"
        assert first_token not in leaked
        assert second_token not in leaked
    finally:
        cloud_a.__exit__(None, None, None)
        cloud_b.__exit__(None, None, None)
