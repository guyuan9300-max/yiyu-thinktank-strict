from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.config import LocalConfig
from backend.app.cloud_client import CloudClientError
from backend.app.main import create_app as create_local_app
from backend.app.runtime import LocalRuntimeError, WorkspaceRuntime
from backend.app.secret_store import MemorySecretStore
from strict_common.contracts import CLOUD_CONTRACT
from strict_common.physical_schema import normalized_structure, structure_sha256
from strict_common.schema import runtime_connection
from strict_common.security import decode_secret_bundle


class LoginCloud:
    def __init__(
        self,
        *,
        instance: str,
        organization: str,
        scope: str,
        email: str,
        system_role: str = "member",
    ):
        self.instance = instance
        self.organization = organization
        self.scope = scope
        self.email = email
        self.system_role = system_role
        self.login_keys: list[str | None] = []
        self.refresh_keys: list[str | None] = []
        self.logout_keys: list[str | None] = []
        self.current_session_count = 0
        self.expire_access = False
        self.handshake_error: CloudClientError | None = None
        self.logout_error: CloudClientError | None = None
        self._payload: dict[str, Any] | None = None

    def handshake(self) -> dict[str, Any]:
        if self.handshake_error is not None:
            raise self.handshake_error
        return {
            "apiVersion": "v2",
            "cloudInstanceId": self.instance,
            "schemaFamily": CLOUD_CONTRACT.schema_family,
            "contractVersion": CLOUD_CONTRACT.contract_version,
            "schemaManifestSha256": CLOUD_CONTRACT.manifest_hash,
            "databaseGenerationId": f"generation_{self.instance}",
        }

    def login(
        self,
        *,
        identifier: str,
        password: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert identifier == self.email
        assert password == "test-password"
        self.login_keys.append(idempotency_key)
        principal_id = f"principal_{self.instance}"
        membership_id = f"membership_{self.instance}"
        policy_id = f"policy_{self.instance}"
        projection_id = f"viewer_{self.instance}"
        payload = {
            "sessionId": f"server_session_{self.instance}",
            "accessToken": f"access-secret-{self.instance}",
            "refreshToken": f"refresh-secret-{self.instance}",
            "expiresAt": "2099-01-01T00:00:00.000Z",
            "refreshExpiresAt": "2099-02-01T00:00:00.000Z",
            "cloudInstanceId": self.instance,
            "organizationId": self.organization,
            "principalId": principal_id,
            "membershipId": membership_id,
            "sessionSnapshot": {
                "organization": {
                    "organizationId": self.organization,
                    "name": f"组织 {self.instance}",
                    "lifecycleState": "active",
                    "version": 3,
                },
                "principal": {
                    "principalId": principal_id,
                    "displayName": "测试成员",
                    "contacts": [
                        {
                            "type": "email",
                            "value": self.email,
                            "verificationState": "verified",
                        }
                    ],
                },
                "membership": {
                    "membershipId": membership_id,
                    "principalId": principal_id,
                    "systemRole": self.system_role,
                    "visibilityScope": (
                        "organization" if self.system_role == "admin" else "self"
                    ),
                    "status": "active",
                },
                "members": [
                    {
                        "membershipId": membership_id,
                        "principalId": principal_id,
                        "displayName": "测试成员",
                        "systemRole": self.system_role,
                        "visibilityScope": (
                            "organization" if self.system_role == "admin" else "self"
                        ),
                        "status": "active",
                        "version": 4,
                    }
                ],
                "departments": [
                    {
                        "departmentId": f"department_{self.instance}",
                        "name": "测试部门",
                        "color": "#336699",
                        "lifecycleState": "active",
                        "version": 2,
                        "members": [
                            {
                                "assignmentId": f"assignment_{self.instance}",
                                "membershipId": membership_id,
                                "roleKey": "department_lead",
                                "isDepartmentLead": True,
                                "status": "active",
                                "version": 2,
                            }
                        ],
                    }
                ],
                "departmentAssignments": [
                    {
                        "assignmentId": f"assignment_{self.instance}",
                        "membershipId": membership_id,
                        "departmentId": f"department_{self.instance}",
                        "assignmentRole": "department_lead",
                        "status": "active",
                        "version": 2,
                        "lifecycleState": "active",
                    }
                ],
                "authorization": {
                    "state": "ready",
                    "freshness": "current",
                    "reasonCode": None,
                    "retryable": False,
                    "principalId": principal_id,
                    "membershipId": membership_id,
                    "organizationId": self.organization,
                    "scopeId": self.scope,
                    "systemRole": self.system_role,
                    "visibilityScope": (
                        "organization" if self.system_role == "admin" else "self"
                    ),
                    "policyVersion": 2,
                    "policyVersionId": policy_id,
                    "projectionId": projection_id,
                    "surfaces": [
                        "application_shell",
                        "workspace_switcher",
                        "account_identity_card",
                        *(
                            ["organization_administration"]
                            if self.system_role == "admin"
                            else []
                        ),
                    ],
                    "capabilities": [
                        "organization.read",
                        *(
                            [
                                "organization.manage",
                                "authorization.manage",
                                "organization_ai.manage",
                            ]
                            if self.system_role == "admin"
                            else []
                        ),
                    ],
                    "generatedAt": "2098-12-31T00:00:00.000Z",
                    "leaseExpiresAt": "2099-01-02T00:00:00.000Z",
                    "sourceVersion": 4,
                },
            },
        }
        self._payload = payload
        return payload

    def current_session(self, access_token: str) -> dict[str, Any]:
        self.current_session_count += 1
        if self.expire_access:
            raise CloudClientError(401, "access_expired", "登录凭据已过期")
        assert self._payload is not None
        assert access_token == self._payload["accessToken"]
        return {
            "cloudInstanceId": self.instance,
            "organizationId": self.organization,
            "principalId": self._payload["principalId"],
            "membershipId": self._payload["membershipId"],
            "sessionSnapshot": self._payload["sessionSnapshot"],
        }

    def refresh(
        self,
        refresh_token: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        assert self._payload is not None
        assert refresh_token == self._payload["refreshToken"]
        self.refresh_keys.append(idempotency_key)
        self.expire_access = False
        self._payload = {
            **self._payload,
            "accessToken": f"refreshed-access-{self.instance}",
            "refreshToken": f"refreshed-refresh-{self.instance}",
        }
        return {
            key: self._payload[key]
            for key in (
                "sessionId",
                "accessToken",
                "refreshToken",
                "expiresAt",
                "refreshExpiresAt",
            )
        }

    def logout(
        self,
        access_token: str,
        *,
        idempotency_key: str | None = None,
    ) -> None:
        if self.logout_error is not None:
            raise self.logout_error
        assert self._payload is not None
        assert access_token == self._payload["accessToken"]
        self.logout_keys.append(idempotency_key)


def _counts(database: Path) -> dict[str, int]:
    with runtime_connection(database, "local") as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            table: int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in tables
        }


def test_gc01_local_login_is_atomic_replayable_and_uses_cloud_scope(tmp_path: Path) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    cloud = LoginCloud(
        instance="cli_gc01_local",
        organization="org_gc01_local",
        scope="scope_from_cloud_not_derived",
        email="gc01-local@example.com",
    )
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    before_counts = _counts(database)
    with runtime_connection(database, "local") as connection:
        before_structure = structure_sha256(normalized_structure(connection))

    first = runtime.login(
        cloud_api_url="http://gc01.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="renderer-login-once",
    )
    assert first["runtimeStatus"] == "ready"
    assert first["sandbox"]["organizationId"] == cloud.organization
    assert first["sessionSnapshot"]["authorization"]["state"] == "ready"
    assert first["sessionSnapshot"]["authorization"]["scopeId"] == cloud.scope
    sandbox_id = first["sandbox"]["sandboxId"]

    after_counts = _counts(database)
    changed_tables = {
        table for table in before_counts if before_counts[table] != after_counts[table]
    }
    assert changed_tables == {
        "principals",
        "organizations",
        "authorization_scopes",
        "organization_memberships",
        "sandboxes",
        "policy_versions",
        "viewer_projections",
        "idempotency_records",
        "commands",
        "audit_events",
        "reconciliation_runs",
    }
    with runtime_connection(database, "local") as connection:
        assert structure_sha256(normalized_structure(connection)) == before_structure
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        session = connection.execute(
            "SELECT secret_reference FROM sandboxes "
            "WHERE id=? AND record_kind='local_session_snapshot'",
            (f"session_snapshot_{sandbox_id}",),
        ).fetchone()
        assert session is not None
        secret_reference = str(session["secret_reference"])
        assert connection.execute(
            "SELECT COUNT(*) FROM authorization_scopes WHERE id=?",
            (cloud.scope,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM authorization_scopes "
            "WHERE id='scope_organization_org_gc01_local'",
        ).fetchone()[0] == 0
    bundle = decode_secret_bundle(secrets.get(secret_reference) or "")
    assert bundle["scopeId"] == cloud.scope
    assert bundle["accessToken"] == "access-secret-cli_gc01_local"
    raw_database = database.read_bytes()
    assert b"test-password" not in raw_database
    assert b"access-secret-cli_gc01_local" not in raw_database
    assert b"refresh-secret-cli_gc01_local" not in raw_database

    replay = runtime.login(
        cloud_api_url="http://gc01.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="renderer-login-once",
    )
    assert replay["sandbox"]["sandboxId"] == sandbox_id
    assert _counts(database) == after_counts
    assert list(secrets.values) == [secret_reference]
    assert cloud.login_keys == ["renderer-login-once", "renderer-login-once"]


def test_gc01_local_login_failure_preserves_previous_workspace_and_secret(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    first_cloud = LoginCloud(
        instance="cli_first",
        organization="org_first",
        scope="scope_first",
        email="shared-contact@example.com",
    )
    second_cloud = LoginCloud(
        instance="cli_second",
        organization="org_second",
        scope="scope_second",
        email="shared-contact@example.com",
    )
    clouds = {
        "http://first.local": first_cloud,
        "http://second.local": second_cloud,
    }
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=clouds.__getitem__)
    first = runtime.login(
        cloud_api_url="http://first.local",
        identifier=first_cloud.email,
        password="test-password",
        idempotency_key="first-login",
    )
    first_sandbox = first["sandbox"]["sandboxId"]
    counts_before_failure = _counts(database)
    secrets_before_failure = dict(secrets.values)

    with pytest.raises(LocalRuntimeError) as failure:
        runtime.login(
            cloud_api_url="http://second.local",
            identifier=second_cloud.email,
            password="test-password",
            idempotency_key="second-login",
        )
    assert failure.value.code == "local_login_apply_failed"
    current = runtime.current()
    assert current["sandbox"]["sandboxId"] == first_sandbox
    assert current["runtimeStatus"] == "ready"
    assert _counts(database) == counts_before_failure
    assert secrets.values == secrets_before_failure
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc01_renderer_login_entry_forwards_one_idempotency_key(tmp_path: Path) -> None:
    data_dir = tmp_path / "local-api"
    config = LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="desktop-gc01-token",
        secret_namespace="test.gc01.local",
        test_mode=True,
    )
    cloud = LoginCloud(
        instance="cli_renderer",
        organization="org_renderer",
        scope="scope_renderer",
        email="renderer@example.com",
    )
    app = create_local_app(config)
    app.state.runtime.cloud_factory = lambda _: cloud
    headers = {
        "X-Yiyu-Desktop-Token": config.desktop_token,
        "Idempotency-Key": "renderer-stable-login-key",
    }
    body = {
        "cloudApiUrl": "http://renderer.local",
        "identifier": cloud.email,
        "password": "test-password",
    }
    with TestClient(app) as client:
        first = client.post("/api/v2/ui/auth/login", headers=headers, json=body)
        assert first.status_code == 200, first.text
        assert first.json()["authenticated"] is True
        assert first.json()["user"]["organizationId"] == cloud.organization
        assert first.json()["user"]["departmentName"] == "测试部门"
        assert first.json()["user"]["isDepartmentLead"] is True
        assert first.json()["authorization"]["state"] == "ready"
        assert first.json()["authorization"]["surfaces"] == [
            "application_shell",
            "workspace_switcher",
            "account_identity_card",
        ]
        assert first.json()["authorization"]["capabilities"] == [
            "organization.read"
        ]
        authorization = client.get(
            "/api/v2/authorization/current",
            headers={"X-Yiyu-Desktop-Token": config.desktop_token},
        )
        assert authorization.status_code == 200, authorization.text
        assert authorization.json()["scopeId"] == cloud.scope
        membership = client.get(
            "/api/v2/ui/me/org-membership",
            headers={"X-Yiyu-Desktop-Token": config.desktop_token},
        )
        assert membership.status_code == 200, membership.text
        assert membership.json()["membershipStatus"] == "approved"
        assert membership.json()["applicationState"] == "none"
        departments = client.get(
            f"/api/v2/ui/auth/department-options?organizationId={cloud.organization}",
            headers={"X-Yiyu-Desktop-Token": config.desktop_token},
        )
        assert departments.status_code == 200, departments.text
        assert departments.json() == [
            {
                "id": f"department_{cloud.instance}",
                "name": "测试部门",
                "color": "#336699",
            }
        ]
        before_replay = _counts(config.database_path)
        replay = client.post("/api/v2/ui/auth/login", headers=headers, json=body)
        assert replay.status_code == 200, replay.text
        assert _counts(config.database_path) == before_replay
        logout = client.post(
            "/api/v2/ui/auth/logout",
            headers={
                "X-Yiyu-Desktop-Token": config.desktop_token,
                "Idempotency-Key": "renderer-stable-logout-key",
            },
        )
        assert logout.status_code == 200, logout.text
        assert logout.json()["authenticated"] is False
    assert cloud.login_keys == [
        "renderer-stable-login-key",
        "renderer-stable-login-key",
    ]
    assert cloud.logout_keys == ["renderer-stable-logout-key"]


def test_gc01_member_is_denied_but_authorized_admin_reaches_frozen_chain(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "permission-api"
    config = LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="desktop-permission-token",
        secret_namespace="test.gc01.permission",
        test_mode=True,
    )
    member_cloud = LoginCloud(
        instance="cli_permission_member",
        organization="org_permission_member",
        scope="scope_permission_member",
        email="permission-member@example.com",
    )
    admin_cloud = LoginCloud(
        instance="cli_permission_admin",
        organization="org_permission_admin",
        scope="scope_permission_admin",
        email="permission-admin@example.com",
        system_role="admin",
    )
    clouds = {
        "http://permission-member.local": member_cloud,
        "http://permission-admin.local": admin_cloud,
    }
    app = create_local_app(config)
    app.state.runtime.cloud_factory = clouds.__getitem__
    headers = {"X-Yiyu-Desktop-Token": config.desktop_token}
    create_body = {"name": "不可落地的测试部门"}
    with TestClient(app) as client:
        member_login = client.post(
            "/api/v2/ui/auth/login",
            headers={**headers, "Idempotency-Key": "permission-member-login"},
            json={
                "cloudApiUrl": "http://permission-member.local",
                "identifier": member_cloud.email,
                "password": "test-password",
            },
        )
        assert member_login.status_code == 200, member_login.text
        denied = client.post(
            "/api/v2/organization/departments",
            headers=headers,
            json=create_body,
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["error"]["code"] == "permission_denied"

        admin_login = client.post(
            "/api/v2/ui/auth/login",
            headers={**headers, "Idempotency-Key": "permission-admin-login"},
            json={
                "cloudApiUrl": "http://permission-admin.local",
                "identifier": admin_cloud.email,
                "password": "test-password",
            },
        )
        assert admin_login.status_code == 200, admin_login.text
        not_connected = client.post(
            "/api/v2/organization/departments",
            headers=headers,
            json=create_body,
        )
        assert not_connected.status_code == 501, not_connected.text
        assert not_connected.json()["error"]["code"] == "golden_chain_frozen"


def test_gc01_restart_relogin_and_logout_use_the_recorded_secret_reference(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    cloud = LoginCloud(
        instance="cli_lifecycle",
        organization="org_lifecycle",
        scope="scope_lifecycle",
        email="lifecycle@example.com",
    )
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    with runtime_connection(database, "local") as connection:
        structure_before = structure_sha256(normalized_structure(connection))

    first = runtime.login(
        cloud_api_url="http://lifecycle.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="lifecycle-first-login",
    )
    sandbox_id = first["sandbox"]["sandboxId"]
    with runtime_connection(database, "local") as connection:
        first_session = connection.execute(
            "SELECT secret_reference FROM sandboxes WHERE id=?",
            (f"session_snapshot_{sandbox_id}",),
        ).fetchone()
    assert first_session is not None
    first_reference = str(first_session["secret_reference"])
    assert first_reference in secrets.values

    restarted = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    restored = restarted.restore_at_startup()
    assert restored["runtimeStatus"] == "ready"
    assert cloud.current_session_count == 1

    relogged = restarted.login(
        cloud_api_url="http://lifecycle.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="lifecycle-second-login",
    )
    assert relogged["sandbox"]["sandboxId"] == sandbox_id
    with runtime_connection(database, "local") as connection:
        second_session = connection.execute(
            "SELECT secret_reference FROM sandboxes WHERE id=?",
            (f"session_snapshot_{sandbox_id}",),
        ).fetchone()
    assert second_session is not None
    second_reference = str(second_session["secret_reference"])
    assert second_reference != first_reference
    assert first_reference not in secrets.values
    assert second_reference in secrets.values

    logged_out = restarted.logout(idempotency_key="lifecycle-logout")
    assert logged_out["runtimeStatus"] == "needs_login"
    assert cloud.logout_keys == ["lifecycle-logout"]
    assert secrets.values == {}
    with runtime_connection(database, "local") as connection:
        sandbox_row = connection.execute(
            "SELECT runtime_status FROM sandboxes WHERE id=?",
            (sandbox_id,),
        ).fetchone()
        session_row = connection.execute(
            "SELECT runtime_status, lifecycle_state, secret_reference "
            "FROM sandboxes WHERE id=?",
            (f"session_snapshot_{sandbox_id}",),
        ).fetchone()
        command_types = {
            str(row[0])
            for row in connection.execute(
                "SELECT command_type FROM commands WHERE scope_id=?",
                (cloud.scope,),
            )
        }
        assert structure_sha256(normalized_structure(connection)) == structure_before
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert sandbox_row is not None and sandbox_row["runtime_status"] == "needs_login"
    assert session_row is not None
    assert tuple(session_row) == ("revoked", "archived", None)
    assert command_types == {
        "gc01.local.session.login",
        "gc01.local.session.logout",
    }


def test_gc01_archived_sandboxes_are_not_switchable_workspaces(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    cloud = LoginCloud(
        instance="cli_archived",
        organization="org_archived",
        scope="scope_archived",
        email="archived@example.com",
    )
    runtime = WorkspaceRuntime(
        database,
        MemorySecretStore(),
        cloud_factory=lambda _: cloud,
    )
    logged_in = runtime.login(
        cloud_api_url="http://archived.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="archived-login",
    )
    sandbox_id = logged_in["sandbox"]["sandboxId"]
    assert [item["sandboxId"] for item in runtime.list_workspaces()] == [
        sandbox_id
    ]

    with runtime_connection(database, "local") as connection:
        connection.execute(
            "UPDATE sandboxes SET lifecycle_state='archived' WHERE id=?",
            (sandbox_id,),
        )
        connection.commit()
    assert runtime.list_workspaces() == []


def test_gc01_restart_refreshes_expired_access_without_fixed_secret_name(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    cloud = LoginCloud(
        instance="cli_refresh",
        organization="org_refresh",
        scope="scope_refresh",
        email="refresh@example.com",
    )
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    logged_in = runtime.login(
        cloud_api_url="http://refresh.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="refresh-login",
    )
    sandbox_id = logged_in["sandbox"]["sandboxId"]
    old_reference = next(iter(secrets.values))
    cloud.expire_access = True

    restarted = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    restored = restarted.restore_at_startup()
    assert restored["runtimeStatus"] == "ready"
    assert len(cloud.refresh_keys) == 1
    assert cloud.current_session_count == 2
    with runtime_connection(database, "local") as connection:
        session = connection.execute(
            "SELECT secret_reference FROM sandboxes WHERE id=?",
            (f"session_snapshot_{sandbox_id}",),
        ).fetchone()
        refresh_commands = connection.execute(
            "SELECT COUNT(*) FROM commands "
            "WHERE command_type='gc01.local.session.refresh'"
        ).fetchone()[0]
        refresh_events = connection.execute(
            "SELECT COUNT(*) FROM outbox_events "
            "WHERE event_type='gc01.local.session.refreshed'"
        ).fetchone()[0]
    assert session is not None
    assert str(session["secret_reference"]) != old_reference
    assert old_reference not in secrets.values
    assert refresh_commands == 1
    assert refresh_events == 1


def test_gc01_logout_cloud_failure_keeps_the_session_for_retry(tmp_path: Path) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    cloud = LoginCloud(
        instance="cli_logout_retry",
        organization="org_logout_retry",
        scope="scope_logout_retry",
        email="logout-retry@example.com",
    )
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    runtime.login(
        cloud_api_url="http://logout-retry.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="logout-retry-login",
    )
    before = dict(secrets.values)
    cloud.logout_error = CloudClientError(503, "cloud_unreachable", "暂时不可用")

    with pytest.raises(LocalRuntimeError) as failure:
        runtime.logout(idempotency_key="logout-retry-operation")
    assert failure.value.code == "logout_failed_retryable"
    assert secrets.values == before
    assert runtime.current()["runtimeStatus"] == "sync_degraded"
    with runtime_connection(database, "local") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM commands "
            "WHERE command_type='gc01.local.session.logout'"
        ).fetchone()[0] == 0


def test_gc01_offline_uses_stale_authorization_only_inside_the_lease(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    cloud = LoginCloud(
        instance="cli_offline_lease",
        organization="org_offline_lease",
        scope="scope_offline_lease",
        email="offline-lease@example.com",
    )
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    runtime.login(
        cloud_api_url="http://offline-lease.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="offline-lease-login",
    )
    cloud.handshake_error = CloudClientError(
        503,
        "cloud_unreachable",
        "组织云暂时不可用",
    )

    offline = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    degraded = offline.restore_at_startup()
    authorization = degraded["sessionSnapshot"]["authorization"]
    assert degraded["runtimeStatus"] == "sync_degraded"
    assert authorization["state"] == "ready"
    assert authorization["freshness"] == "stale"
    assert authorization["reasonCode"] == "cloud_revalidation_pending"
    assert authorization["lastConfirmedAt"]
    assert offline.require_surface("application_shell")["state"] == "ready"


def test_gc01_expired_offline_lease_fails_closed_then_recovers_from_cloud(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    cloud = LoginCloud(
        instance="cli_expired_lease",
        organization="org_expired_lease",
        scope="scope_expired_lease",
        email="expired-lease@example.com",
    )
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    runtime.login(
        cloud_api_url="http://expired-lease.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="expired-lease-login",
    )
    with runtime_connection(database, "local") as connection:
        connection.execute(
            "UPDATE viewer_projections SET generated_at=?, lease_expires_at=? "
            "WHERE viewer_membership_id=? AND invalidated_at IS NULL",
            (
                "2026-08-01T00:00:00.000Z",
                "2026-08-02T00:00:00.000Z",
                f"membership_{cloud.instance}",
            ),
        )
        connection.commit()
    cloud.handshake_error = CloudClientError(
        503,
        "cloud_unreachable",
        "组织云暂时不可用",
    )

    offline = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    expired = offline.restore_at_startup()
    authorization = expired["sessionSnapshot"]["authorization"]
    assert expired["runtimeStatus"] == "sync_degraded"
    assert authorization["state"] == "blocked"
    assert authorization["freshness"] == "expired"
    assert authorization["reasonCode"] == "authorization_lease_expired"
    with pytest.raises(LocalRuntimeError) as denied:
        offline.require_surface("application_shell")
    assert denied.value.status_code == 403
    assert denied.value.code == "authorization_lease_expired"

    cloud.handshake_error = None
    recovered = offline.restore_at_startup()
    recovered_authorization = recovered["sessionSnapshot"]["authorization"]
    assert recovered["runtimeStatus"] == "ready"
    assert recovered_authorization["state"] == "ready"
    assert recovered_authorization["freshness"] == "current"
    assert recovered_authorization["leaseExpiresAt"] != "2026-08-02T00:00:00.000Z"


def test_gc01_workspace_switch_validates_then_pins_late_request_to_original_sandbox(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    first_cloud = LoginCloud(
        instance="cli_switch_first",
        organization="org_switch_first",
        scope="scope_switch_first",
        email="first-switch@example.com",
    )
    second_cloud = LoginCloud(
        instance="cli_switch_second",
        organization="org_switch_second",
        scope="scope_switch_second",
        email="second-switch@example.com",
    )
    clouds = {
        "http://first-switch.local": first_cloud,
        "http://second-switch.local": second_cloud,
    }
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=clouds.__getitem__)
    first = runtime.login(
        cloud_api_url="http://first-switch.local",
        identifier=first_cloud.email,
        password="test-password",
        idempotency_key="switch-first-login",
    )
    first_sandbox = first["sandbox"]["sandboxId"]
    second = runtime.login(
        cloud_api_url="http://second-switch.local",
        identifier=second_cloud.email,
        password="test-password",
        idempotency_key="switch-second-login",
    )
    second_sandbox = second["sandbox"]["sandboxId"]
    late_second_context = runtime.capture_sandbox_context(
        expected_sandbox_id=second_sandbox,
        request_seq=1_800_000_000_001,
    )

    switched = runtime.switch(
        first_sandbox,
        idempotency_key="switch-to-first",
        request_seq=1_800_000_000_002,
    )
    assert switched["sandbox"]["sandboxId"] == first_sandbox
    assert first_cloud.current_session_count == 1
    with runtime.prebound_sandbox_context(late_second_context):
        assert runtime.current()["sandbox"]["sandboxId"] == second_sandbox
    assert runtime.current()["sandbox"]["sandboxId"] == first_sandbox

    with runtime_connection(database, "local") as connection:
        switch_commands = connection.execute(
            "SELECT aggregate_id, device_command_sequence FROM commands "
            "WHERE command_type='gc01.local.workspace.switch'"
        ).fetchall()
        switch_outbox = connection.execute(
            "SELECT COUNT(*) FROM outbox_events "
            "WHERE event_type='gc01.local.workspace.switched'"
        ).fetchone()[0]
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert [tuple(row) for row in switch_commands] == [
        (first_sandbox, 1_800_000_000_002)
    ]
    assert switch_outbox == 0

    second_session_reference = next(
        reference
        for reference in list(secrets.values)
        if second_sandbox in reference
    )
    secrets.delete(second_session_reference)
    with pytest.raises(LocalRuntimeError) as failure:
        runtime.switch(
            second_sandbox,
            idempotency_key="switch-missing-secret",
            request_seq=1_800_000_000_003,
        )
    assert failure.value.code == "workspace_secret_missing"
    assert runtime.current()["sandbox"]["sandboxId"] == first_sandbox


def test_gc01_renderer_rejects_stale_workspace_header_after_switch(tmp_path: Path) -> None:
    data_dir = tmp_path / "switch-api"
    config = LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="desktop-switch-token",
        secret_namespace="test.gc01.switch",
        test_mode=True,
    )
    first_cloud = LoginCloud(
        instance="cli_api_first",
        organization="org_api_first",
        scope="scope_api_first",
        email="first-api@example.com",
    )
    second_cloud = LoginCloud(
        instance="cli_api_second",
        organization="org_api_second",
        scope="scope_api_second",
        email="second-api@example.com",
    )
    clouds = {
        "http://first-api.local": first_cloud,
        "http://second-api.local": second_cloud,
    }
    app = create_local_app(config)
    app.state.runtime.cloud_factory = clouds.__getitem__
    base_headers = {"X-Yiyu-Desktop-Token": config.desktop_token}
    with TestClient(app) as client:
        first = client.post(
            "/api/v2/ui/auth/login",
            headers={**base_headers, "Idempotency-Key": "api-first-login"},
            json={
                "cloudApiUrl": "http://first-api.local",
                "identifier": first_cloud.email,
                "password": "test-password",
            },
        )
        assert first.status_code == 200, first.text
        first_sandbox = client.get(
            "/api/v2/ui/workspaces", headers=base_headers
        ).json()["activeSandboxId"]
        second = client.post(
            "/api/v2/ui/auth/login",
            headers={**base_headers, "Idempotency-Key": "api-second-login"},
            json={
                "cloudApiUrl": "http://second-api.local",
                "identifier": second_cloud.email,
                "password": "test-password",
            },
        )
        assert second.status_code == 200, second.text
        second_sandbox = client.get(
            "/api/v2/ui/workspaces", headers=base_headers
        ).json()["activeSandboxId"]

        switched = client.post(
            f"/api/v2/ui/workspaces/{first_sandbox}/activate?restoreSession=true",
            headers={
                **base_headers,
                "Idempotency-Key": "api-switch-first",
                "X-Yiyu-Sandbox-Id": first_sandbox,
                "X-Yiyu-Request-Seq": "1800000000010",
            },
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["activeSandboxId"] == first_sandbox
        assert switched.headers["X-Yiyu-Request-Seq"] == "1800000000010"

        stale = client.get(
            "/api/v2/ui/auth/me",
            headers={
                **base_headers,
                "X-Yiyu-Sandbox-Id": second_sandbox,
                "X-Yiyu-Request-Seq": "1800000000009",
            },
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error"]["code"] == "workspace_context_stale"
        current = client.get(
            "/api/v2/ui/auth/me",
            headers={
                **base_headers,
                "X-Yiyu-Sandbox-Id": first_sandbox,
                "X-Yiyu-Request-Seq": "1800000000010",
            },
        )
        assert current.status_code == 200, current.text
        assert current.json()["user"]["organizationId"] == first_cloud.organization
