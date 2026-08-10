from __future__ import annotations

import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from strict_common.contracts import CLOUD_CONTRACT, CONNECTED_CAPABILITIES
from strict_common.ids import (
    canonical_json,
    new_id,
    sha256_text,
    utc_now,
)
from strict_common.schema import (
    audit_event_hash,
    database_identity,
    initialize_database,
    runtime_connection,
)
from strict_common.security import (
    LEGACY_PASSWORD_SCHEME,
    PASSWORD_SCHEME,
    SecretCipher,
    hash_password,
    hash_token,
    new_secret_token,
    normalize_email,
    normalize_identifier,
    normalize_phone,
    payload_fingerprint,
    verify_password,
)


ACCESS_TTL = timedelta(hours=2)
REFRESH_TTL = timedelta(days=30)
SHARED_KNOWLEDGE_DOCUMENT_KINDS = frozenset(
    {
        "shared_summary",
        "organization_shared_summary",
        "project_narrative",
        "report_summary",
        "evidence_summary",
    }
)


class RepositoryError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SessionIdentity:
    session_id: str
    principal_id: str
    membership_id: str
    organization_id: str
    cloud_instance_id: str
    scope_id: str
    system_role: str
    visibility_scope: str
    display_name: str

    @property
    def is_admin(self) -> bool:
        return self.system_role == "admin"


def _expires_at(delta: timedelta) -> str:
    return (
        (datetime.now(timezone.utc) + delta)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CloudRepository:
    def __init__(
        self,
        database_path: Path,
        *,
        cloud_instance_id: str | None,
        master_key: str,
    ):
        self.database_path = database_path.resolve()
        self.identity = initialize_database(self.database_path, "cloud")
        self.cipher = SecretCipher(master_key)
        self.cloud_instance_id = self._ensure_cloud_instance(cloud_instance_id)

    def _connection(self):
        return runtime_connection(self.database_path, "cloud")

    def _verify_and_upgrade_password(
        self,
        connection: sqlite3.Connection,
        *,
        principal_id: str,
        password: str,
        secret_hash: str,
        hash_scheme: str,
    ) -> bool:
        if not verify_password(password, secret_hash, scheme=hash_scheme):
            return False
        if hash_scheme == LEGACY_PASSWORD_SCHEME:
            now = utc_now()
            connection.execute(
                """
                UPDATE identity_credentials
                SET secret_hash = ?, hash_scheme = ?, version = version + 1,
                    updated_at = ?
                WHERE principal_id = ? AND credential_type = 'password'
                  AND status = 'active' AND hash_scheme = ?
                """,
                (
                    hash_password(password),
                    PASSWORD_SCHEME,
                    now,
                    principal_id,
                    LEGACY_PASSWORD_SCHEME,
                ),
            )
        return True

    def _ensure_cloud_instance(self, requested_id: str | None) -> str:
        now = utc_now()
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT cloud_instance_id, database_generation_id
                FROM identity_cloud_instances
                LIMIT 1
                """
            ).fetchone()
            if row is not None:
                actual = str(row["cloud_instance_id"])
                if requested_id and not hmac.compare_digest(actual, requested_id):
                    raise RuntimeError(
                        "configured cloud instance id does not match strict database"
                    )
                if str(row["database_generation_id"]) != self.identity.database_generation_id:
                    raise RuntimeError("cloud instance database generation mismatch")
                return actual
            instance_id = requested_id or new_id()
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO identity_cloud_instances (
                        cloud_instance_id, database_generation_id, schema_family,
                        contract_version, status, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)
                    """,
                    (
                        instance_id,
                        self.identity.database_generation_id,
                        CLOUD_CONTRACT.schema_family,
                        CLOUD_CONTRACT.contract_version,
                        now,
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return instance_id

    def handshake(self) -> dict[str, Any]:
        current = database_identity(self.database_path, "cloud")
        with self._connection() as connection:
            org_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM organization_records"
                ).fetchone()[0]
            )
        return {
            "apiVersion": "v2",
            "service": "yiyu-strict-cloud",
            "cloudInstanceId": self.cloud_instance_id,
            "schemaFamily": current.schema_family,
            "contractVersion": current.contract_version,
            "schemaManifestSha256": current.manifest_hash,
            "databaseGenerationId": current.database_generation_id,
            "buildId": current.build_id,
            "organizationCount": org_count,
            "capabilities": sorted(CONNECTED_CAPABILITIES),
        }

    def _insert_personal_identity(
        self,
        connection: sqlite3.Connection,
        *,
        display_name: str,
        email: str | None,
        phone: str | None,
        password: str,
    ) -> tuple[str, str]:
        now = utc_now()
        principal_id = new_id()
        connection.execute(
            """
            INSERT INTO identity_principals (
                principal_id, display_name, status, identity_version,
                created_at, updated_at
            ) VALUES (?, ?, 'active', 1, ?, ?)
            """,
            (principal_id, display_name.strip(), now, now),
        )
        contacts: list[tuple[str, str]] = []
        if email:
            contacts.append(("email", normalize_email(email)))
        if phone:
            contacts.append(("phone", normalize_phone(phone)))
        if not contacts:
            raise RepositoryError(422, "contact_required", "邮箱或手机号至少填写一项")
        for contact_type, normalized in contacts:
            connection.execute(
                """
                INSERT INTO identity_contacts (
                    contact_id, principal_id, contact_type, normalized_value,
                    verification_state, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'verified', 1, ?, ?)
                """,
                (new_id(), principal_id, contact_type, normalized, now, now),
            )
        connection.execute(
            """
            INSERT INTO identity_credentials (
                credential_id, principal_id, credential_type, secret_hash,
                hash_scheme, status, version, created_at, updated_at
            ) VALUES (?, ?, 'password', ?, 'scrypt-v1', 'active', 1, ?, ?)
            """,
            (new_id(), principal_id, hash_password(password), now, now),
        )
        personal_scope_id = new_id()
        connection.execute(
            """
            INSERT INTO authorization_scopes (
                scope_id, scope_kind, principal_id, organization_id,
                policy_version, created_at, updated_at
            ) VALUES (?, 'personal', ?, NULL, 1, ?, ?)
            """,
            (personal_scope_id, principal_id, now, now),
        )
        return principal_id, personal_scope_id

    def _insert_session(
        self,
        connection: sqlite3.Connection,
        *,
        principal_id: str,
        membership_id: str,
        organization_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        access_token = new_secret_token()
        refresh_token = new_secret_token()
        session_id = new_id()
        access_expires_at = _expires_at(ACCESS_TTL)
        refresh_expires_at = _expires_at(REFRESH_TTL)
        connection.execute(
            """
            INSERT INTO authentication_sessions (
                session_id, principal_id, cloud_instance_id, organization_id,
                membership_id, database_generation_id, access_secret_hash,
                refresh_secret_hash, status, issued_at, expires_at,
                refresh_expires_at, version, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, 1, ?)
            """,
            (
                session_id,
                principal_id,
                self.cloud_instance_id,
                organization_id,
                membership_id,
                self.identity.database_generation_id,
                hash_token(access_token),
                hash_token(refresh_token),
                now,
                access_expires_at,
                refresh_expires_at,
                now,
            ),
        )
        return {
            "sessionId": session_id,
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": access_expires_at,
            "refreshExpiresAt": refresh_expires_at,
        }

    def _insert_audit(
        self,
        connection: sqlite3.Connection,
        *,
        scope_id: str,
        organization_id: str,
        operation_id: str,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        before_version: int | None,
        after_version: int | None,
        summary: dict[str, Any],
    ) -> None:
        previous = connection.execute(
            """
            SELECT event_hash
            FROM audit_events
            WHERE scope_id = ?
            ORDER BY created_at DESC, audit_id DESC
            LIMIT 1
            """,
            (scope_id,),
        ).fetchone()
        previous_hash = str(previous[0]) if previous else None
        created_at = utc_now()
        summary_json = canonical_json(summary)
        event_hash = audit_event_hash(
            previous_event_hash=previous_hash,
            operation_id=operation_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            summary_json=summary_json,
            created_at=created_at,
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                audit_id, scope_id, organization_id, operation_id, actor_id,
                action, resource_type, resource_id, before_version,
                after_version, summary_json, previous_event_hash,
                event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                scope_id,
                organization_id,
                operation_id,
                actor_id,
                action,
                resource_type,
                resource_id,
                before_version,
                after_version,
                summary_json,
                previous_hash,
                event_hash,
                created_at,
            ),
        )

    def _insert_outbox(
        self,
        connection: sqlite3.Connection,
        *,
        scope_id: str,
        organization_id: str,
        operation_id: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        now = utc_now()
        payload_json = canonical_json(payload)
        connection.execute(
            """
            INSERT INTO delivery_outbox (
                event_id, scope_id, organization_id, operation_id,
                aggregate_type, aggregate_id, aggregate_version, event_type,
                payload_json, payload_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                new_id(),
                scope_id,
                organization_id,
                operation_id,
                aggregate_type,
                aggregate_id,
                aggregate_version,
                event_type,
                payload_json,
                sha256_text(payload_json),
                now,
                now,
            ),
        )

    def bootstrap_organization(
        self,
        *,
        organization_name: str,
        display_name: str,
        email: str | None,
        phone: str | None,
        password: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    "SELECT 1 FROM organization_records LIMIT 1"
                ).fetchone():
                    raise RepositoryError(
                        409,
                        "organization_exists",
                        "该严格云实例已经创建组织",
                    )
                principal_id, _ = self._insert_personal_identity(
                    connection,
                    display_name=display_name,
                    email=email,
                    phone=phone,
                    password=password,
                )
                now = utc_now()
                organization_id = new_id()
                scope_id = new_id()
                membership_id = new_id()
                operation_id = new_id()
                connection.execute(
                    """
                    INSERT INTO organization_records (
                        organization_id, cloud_instance_id, name,
                        lifecycle_state, version, created_at, updated_at
                    ) VALUES (?, ?, ?, 'active', 1, ?, ?)
                    """,
                    (
                        organization_id,
                        self.cloud_instance_id,
                        organization_name.strip(),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO authorization_scopes (
                        scope_id, scope_kind, principal_id, organization_id,
                        policy_version, created_at, updated_at
                    ) VALUES (?, 'organization', NULL, ?, 1, ?, ?)
                    """,
                    (scope_id, organization_id, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO organization_memberships (
                        membership_id, scope_id, organization_id, principal_id,
                        system_role, visibility_scope, status, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'admin', 'organization', 'active', 1, ?, ?)
                    """,
                    (
                        membership_id,
                        scope_id,
                        organization_id,
                        principal_id,
                        now,
                        now,
                    ),
                )
                resource_id = new_id()
                policy_version_id = new_id()
                connection.execute(
                    """
                    INSERT INTO authorization_resources (
                        resource_id, scope_id, resource_kind, lifecycle_state,
                        version, created_at, updated_at
                    ) VALUES (?, ?, 'organization', 'active', 1, ?, ?)
                    """,
                    (resource_id, scope_id, now, now),
                )
                capabilities = canonical_json(
                    [
                        "organization.read",
                        "organization.manage",
                        "authorization.manage",
                        "organization_ai.manage",
                    ]
                )
                connection.execute(
                    """
                    INSERT INTO authorization_policy_versions (
                        policy_version_id, scope_id, resource_id,
                        policy_scope_kind, version, policy_json, created_at
                    ) VALUES (?, ?, ?, 'organization', 1, ?, ?)
                    """,
                    (
                        policy_version_id,
                        scope_id,
                        resource_id,
                        canonical_json({"admin": json.loads(capabilities)}),
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO authorization_grants (
                        grant_id, scope_id, resource_id, policy_version_id,
                        subject_principal_id, subject_membership_id,
                        capability_set, grant_generation, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'active', ?, ?)
                    """,
                    (
                        new_id(),
                        scope_id,
                        resource_id,
                        policy_version_id,
                        principal_id,
                        membership_id,
                        capabilities,
                        now,
                        now,
                    ),
                )
                default_project_id = new_id()
                connection.execute(
                    """
                    INSERT INTO work_projects (
                        project_id, organization_id, name, alias, summary,
                        domain, color, is_default_internal_project,
                        lifecycle_state, created_by_membership_id, version,
                        created_at, updated_at, archived_at
                    ) VALUES (?, ?, ?, '', '', '内部协作', '#5B7BFE', 1,
                              'active', ?, 1, ?, ?, NULL)
                    """,
                    (
                        default_project_id,
                        organization_id,
                        f"{organization_name.strip()}项目",
                        membership_id,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO project_participants (
                        project_id, organization_id, membership_id,
                        participant_role, status, version, created_at, updated_at
                    ) VALUES (?, ?, ?, 'owner', 'active', 1, ?, ?)
                    """,
                    (
                        default_project_id,
                        organization_id,
                        membership_id,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO task_lists (
                        task_list_id, organization_id, name, color, scope_kind,
                        owner_membership_id, description, sort_order, is_default,
                        lifecycle_state, version, created_at, updated_at, archived_at
                    ) VALUES (?, ?, '默认清单', '#5B7BFE', 'organization',
                              NULL, '', 0, 1, 'active', 1, ?, ?, NULL)
                    """,
                    (new_id(), organization_id, now, now),
                )
                payload = {
                    "organizationName": organization_name.strip(),
                    "displayName": display_name.strip(),
                    "defaultProjectId": default_project_id,
                }
                command_id = new_id()
                connection.execute(
                    """
                    INSERT INTO command_envelopes (
                        command_id, scope_id, organization_id, operation_id,
                        idempotency_key, aggregate_type, aggregate_id,
                        command_type, actor_principal_id, expected_version,
                        payload_json, payload_hash, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'organization', ?,
                              'organization.bootstrap', ?, NULL, ?, ?,
                              'committed', ?, ?)
                    """,
                    (
                        command_id,
                        scope_id,
                        organization_id,
                        operation_id,
                        operation_id,
                        organization_id,
                        principal_id,
                        canonical_json(payload),
                        payload_fingerprint(payload),
                        now,
                        now,
                    ),
                )
                self._insert_audit(
                    connection,
                    scope_id=scope_id,
                    organization_id=organization_id,
                    operation_id=operation_id,
                    actor_id=principal_id,
                    action="organization.bootstrap",
                    resource_type="organization",
                    resource_id=organization_id,
                    before_version=None,
                    after_version=1,
                    summary=payload,
                )
                self._insert_outbox(
                    connection,
                    scope_id=scope_id,
                    organization_id=organization_id,
                    operation_id=operation_id,
                    aggregate_type="organization",
                    aggregate_id=organization_id,
                    aggregate_version=1,
                    event_type="organization.created",
                    payload={"organizationId": organization_id},
                )
                session = self._insert_session(
                    connection,
                    principal_id=principal_id,
                    membership_id=membership_id,
                    organization_id=organization_id,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._session_payload(session)

    def _session_payload(self, session: dict[str, Any]) -> dict[str, Any]:
        identity = self.session_from_access(str(session["accessToken"]))
        snapshot = self.organization_snapshot(identity)
        return {
            **session,
            "cloudInstanceId": self.cloud_instance_id,
            "organizationId": identity.organization_id,
            "principalId": identity.principal_id,
            "membershipId": identity.membership_id,
            "sessionSnapshot": snapshot,
        }

    def login(self, *, identifier: str, password: str) -> dict[str, Any]:
        contact_type, normalized = normalize_identifier(identifier)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT p.principal_id, c.secret_hash, c.hash_scheme,
                       m.membership_id,
                       m.organization_id
                FROM identity_contacts AS i
                JOIN identity_principals AS p
                  ON p.principal_id = i.principal_id
                JOIN identity_credentials AS c
                  ON c.principal_id = p.principal_id
                 AND c.credential_type = 'password'
                 AND c.status = 'active'
                JOIN organization_memberships AS m
                  ON m.principal_id = p.principal_id
                 AND m.status = 'active'
                JOIN organization_records AS o
                  ON o.organization_id = m.organization_id
                 AND o.cloud_instance_id = ?
                 AND o.lifecycle_state = 'active'
                WHERE i.contact_type = ?
                  AND i.normalized_value = ?
                  AND i.verification_state = 'verified'
                  AND p.status = 'active'
                """,
                (self.cloud_instance_id, contact_type, normalized),
            ).fetchone()
            if row is None:
                raise RepositoryError(401, "invalid_credentials", "账号或密码不正确")
            connection.execute("BEGIN IMMEDIATE")
            try:
                if not self._verify_and_upgrade_password(
                    connection,
                    principal_id=str(row["principal_id"]),
                    password=password,
                    secret_hash=str(row["secret_hash"]),
                    hash_scheme=str(row["hash_scheme"]),
                ):
                    raise RepositoryError(
                        401,
                        "invalid_credentials",
                        "账号或密码不正确",
                    )
                session = self._insert_session(
                    connection,
                    principal_id=str(row["principal_id"]),
                    membership_id=str(row["membership_id"]),
                    organization_id=str(row["organization_id"]),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._session_payload(session)

    def session_from_access(self, access_token: str) -> SessionIdentity:
        token_hash = hash_token(access_token)
        now = datetime.now(timezone.utc)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT s.session_id, s.principal_id, s.membership_id,
                       s.organization_id, s.cloud_instance_id, s.expires_at,
                       m.scope_id, m.system_role, m.visibility_scope,
                       m.status AS membership_status, p.display_name,
                       p.status AS principal_status
                FROM authentication_sessions AS s
                JOIN organization_memberships AS m
                  ON m.membership_id = s.membership_id
                JOIN identity_principals AS p
                  ON p.principal_id = s.principal_id
                WHERE s.access_secret_hash = ?
                  AND s.status = 'active'
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                raise RepositoryError(401, "invalid_session", "登录状态已失效")
            if _parse_time(str(row["expires_at"])) <= now:
                raise RepositoryError(401, "access_expired", "登录凭据已过期")
            if row["membership_status"] != "active" or row["principal_status"] != "active":
                raise RepositoryError(403, "account_disabled", "账号当前不可用")
            if str(row["cloud_instance_id"]) != self.cloud_instance_id:
                raise RepositoryError(409, "cloud_identity_mismatch", "云实例身份不一致")
            return SessionIdentity(
                session_id=str(row["session_id"]),
                principal_id=str(row["principal_id"]),
                membership_id=str(row["membership_id"]),
                organization_id=str(row["organization_id"]),
                cloud_instance_id=str(row["cloud_instance_id"]),
                scope_id=str(row["scope_id"]),
                system_role=str(row["system_role"]),
                visibility_scope=str(row["visibility_scope"]),
                display_name=str(row["display_name"]),
            )

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        token_hash = hash_token(refresh_token)
        now_dt = datetime.now(timezone.utc)
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT session_id, principal_id, membership_id,
                           organization_id, refresh_expires_at, version
                    FROM authentication_sessions
                    WHERE refresh_secret_hash = ? AND status = 'active'
                    """,
                    (token_hash,),
                ).fetchone()
                if row is None or _parse_time(str(row["refresh_expires_at"])) <= now_dt:
                    raise RepositoryError(401, "refresh_expired", "需要重新登录")
                access_token = new_secret_token()
                next_refresh = new_secret_token()
                expires_at = _expires_at(ACCESS_TTL)
                refresh_expires_at = _expires_at(REFRESH_TTL)
                cursor = connection.execute(
                    """
                    UPDATE authentication_sessions
                    SET access_secret_hash = ?, refresh_secret_hash = ?,
                        expires_at = ?, refresh_expires_at = ?,
                        version = version + 1, last_seen_at = ?
                    WHERE session_id = ? AND version = ?
                    """,
                    (
                        hash_token(access_token),
                        hash_token(next_refresh),
                        expires_at,
                        refresh_expires_at,
                        now,
                        row["session_id"],
                        row["version"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(409, "session_conflict", "会话已更新，请重试")
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        session = {
            "sessionId": str(row["session_id"]),
            "accessToken": access_token,
            "refreshToken": next_refresh,
            "expiresAt": expires_at,
            "refreshExpiresAt": refresh_expires_at,
        }
        return self._session_payload(session)

    def logout(self, identity: SessionIdentity) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE authentication_sessions
                SET status = 'revoked', version = version + 1, last_seen_at = ?
                WHERE session_id = ? AND status = 'active'
                """,
                (utc_now(), identity.session_id),
            )
            connection.commit()

    def organization_snapshot(self, identity: SessionIdentity) -> dict[str, Any]:
        with self._connection() as connection:
            organization = connection.execute(
                """
                SELECT organization_id, name, lifecycle_state, version
                FROM organization_records
                WHERE organization_id = ? AND cloud_instance_id = ?
                """,
                (identity.organization_id, self.cloud_instance_id),
            ).fetchone()
            if organization is None:
                raise RepositoryError(404, "organization_missing", "组织不存在")
            contacts = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT contact_type AS type, normalized_value AS value,
                           verification_state AS verificationState
                    FROM identity_contacts
                    WHERE principal_id = ?
                    ORDER BY contact_type, normalized_value
                    """,
                    (identity.principal_id,),
                ).fetchall()
            ]
            members = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT m.membership_id AS membershipId,
                           m.principal_id AS principalId,
                           p.display_name AS displayName,
                           m.system_role AS systemRole,
                           m.visibility_scope AS visibilityScope,
                           m.status, m.version
                    FROM organization_memberships AS m
                    JOIN identity_principals AS p
                      ON p.principal_id = m.principal_id
                    WHERE m.organization_id = ?
                    ORDER BY p.display_name, m.membership_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            ]
            departments = [
                {
                    **dict(row),
                    "members": [
                        dict(member)
                        for member in connection.execute(
                            """
                            SELECT dm.membership_id AS membershipId,
                                   p.display_name AS displayName,
                                   dm.is_department_lead AS isDepartmentLead,
                                   dm.status
                            FROM department_memberships AS dm
                            JOIN organization_memberships AS m
                              ON m.membership_id = dm.membership_id
                            JOIN identity_principals AS p
                              ON p.principal_id = m.principal_id
                            WHERE dm.department_id = ?
                            ORDER BY dm.is_department_lead DESC, p.display_name
                            """,
                            (row["departmentId"],),
                        ).fetchall()
                    ],
                }
                for row in connection.execute(
                    """
                    SELECT department_id AS departmentId, name,
                           lifecycle_state AS lifecycleState, version
                    FROM organization_departments
                    WHERE organization_id = ?
                    ORDER BY name
                    """,
                    (identity.organization_id,),
                ).fetchall()
            ]
            titles = [
                {
                    **dict(row),
                    "members": [
                        dict(member)
                        for member in connection.execute(
                            """
                            SELECT mtm.membership_id AS membershipId,
                                   p.display_name AS displayName,
                                   mtm.status
                            FROM management_title_memberships AS mtm
                            JOIN organization_memberships AS m
                              ON m.membership_id = mtm.membership_id
                            JOIN identity_principals AS p
                              ON p.principal_id = m.principal_id
                            WHERE mtm.title_id = ?
                            ORDER BY p.display_name
                            """,
                            (row["titleId"],),
                        ).fetchall()
                    ],
                }
                for row in connection.execute(
                    """
                    SELECT title_id AS titleId, name,
                           lifecycle_state AS lifecycleState, version
                    FROM management_titles
                    WHERE organization_id = ?
                    ORDER BY name
                    """,
                    (identity.organization_id,),
                ).fetchall()
            ]
        current_member = next(
            member for member in members if member["membershipId"] == identity.membership_id
        )
        return {
            "cloudInstanceId": self.cloud_instance_id,
            "organization": {
                "organizationId": organization["organization_id"],
                "name": organization["name"],
                "lifecycleState": organization["lifecycle_state"],
                "version": organization["version"],
            },
            "principal": {
                "principalId": identity.principal_id,
                "displayName": identity.display_name,
                "contacts": contacts,
            },
            "membership": current_member,
            "members": members,
            "departments": departments,
            "managementTitles": titles,
        }

    def business_snapshot(self, identity: SessionIdentity) -> dict[str, Any]:
        with self._connection() as connection:
            department_rows = connection.execute(
                """
                SELECT department_id
                FROM department_memberships
                WHERE organization_id = ? AND membership_id = ?
                  AND status = 'active'
                ORDER BY is_department_lead DESC, department_id
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchall()
            department_ids = {str(row["department_id"]) for row in department_rows}
            lead_department_rows = connection.execute(
                """
                SELECT department_id
                FROM department_memberships
                WHERE organization_id = ? AND membership_id = ?
                  AND status = 'active' AND is_department_lead = 1
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchall()
            lead_department_ids = {
                str(row["department_id"]) for row in lead_department_rows
            }
            is_management = connection.execute(
                """
                SELECT 1
                FROM management_title_memberships mtm
                JOIN management_titles mt ON mt.title_id = mtm.title_id
                WHERE mtm.organization_id = ? AND mtm.membership_id = ?
                  AND mtm.status = 'active' AND mt.lifecycle_state = 'active'
                LIMIT 1
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchone() is not None
            can_read_organization = (
                identity.is_admin
                or identity.visibility_scope == "organization"
                or is_management
            )

            if can_read_organization:
                task_rows = connection.execute(
                    """
                    SELECT * FROM task_records
                    WHERE organization_id = ?
                    ORDER BY updated_at DESC, task_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            else:
                task_rows = connection.execute(
                    """
                    SELECT DISTINCT t.*
                    FROM task_records t
                    LEFT JOIN task_collaborators tc
                      ON tc.task_id = t.task_id
                     AND tc.membership_id = ?
                     AND tc.inbox_state != 'returned'
                    WHERE t.organization_id = ?
                      AND (
                        t.created_by_membership_id = ?
                        OR tc.membership_id IS NOT NULL
                        OR t.visibility_scope = 'organization'
                      )
                    ORDER BY t.updated_at DESC, t.task_id
                    """,
                    (
                        identity.membership_id,
                        identity.organization_id,
                        identity.membership_id,
                    ),
                ).fetchall()
            if can_read_organization:
                event_rows = connection.execute(
                    """
                    SELECT * FROM event_line_records
                    WHERE organization_id = ?
                    ORDER BY updated_at DESC, event_line_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            else:
                department_placeholders = ",".join("?" for _ in department_ids)
                department_clause = (
                    f"OR (e.visibility_scope = 'department' "
                    f"AND e.department_id IN ({department_placeholders}))"
                    if department_ids
                    else ""
                )
                event_rows = connection.execute(
                    f"""
                    SELECT DISTINCT e.*
                    FROM event_line_records e
                    LEFT JOIN event_line_participants ep
                      ON ep.event_line_id = e.event_line_id
                     AND ep.membership_id = ?
                     AND ep.status = 'active'
                    WHERE e.organization_id = ?
                      AND (
                        e.created_by_membership_id = ?
                        OR ep.membership_id IS NOT NULL
                        OR e.visibility_scope = 'organization'
                        {department_clause}
                      )
                    ORDER BY e.updated_at DESC, e.event_line_id
                    """,
                    (
                        identity.membership_id,
                        identity.organization_id,
                        identity.membership_id,
                        *sorted(department_ids),
                    ),
                ).fetchall()
            visible_event_ids = {str(row["event_line_id"]) for row in event_rows}

            if can_read_organization:
                document_rows = connection.execute(
                    """
                    SELECT * FROM knowledge_documents
                    WHERE organization_id = ?
                    ORDER BY updated_at DESC, document_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            else:
                department_placeholders = ",".join("?" for _ in department_ids)
                department_clause = (
                    f"OR (d.visibility_scope = 'department' "
                    f"AND d.department_id IN ({department_placeholders}))"
                    if department_ids
                    else ""
                )
                document_rows = connection.execute(
                    f"""
                    SELECT DISTINCT d.*
                    FROM knowledge_documents d
                    LEFT JOIN project_participants pp
                      ON pp.project_id = d.project_id
                     AND pp.membership_id = ?
                     AND pp.status = 'active'
                    WHERE d.organization_id = ?
                      AND (
                        d.owner_membership_id = ?
                        OR pp.membership_id IS NOT NULL
                        OR d.visibility_scope = 'organization'
                        {department_clause}
                      )
                    ORDER BY d.updated_at DESC, d.document_id
                    """,
                    (
                        identity.membership_id,
                        identity.organization_id,
                        identity.membership_id,
                        *sorted(department_ids),
                    ),
                ).fetchall()

            if can_read_organization:
                project_rows = connection.execute(
                    """
                    SELECT * FROM work_projects
                    WHERE organization_id = ?
                    ORDER BY is_default_internal_project DESC, updated_at DESC, project_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            else:
                referenced_project_ids = {
                    str(row["project_id"])
                    for row in [*task_rows, *event_rows, *document_rows]
                    if row["project_id"]
                }
                project_rows = connection.execute(
                    """
                    SELECT DISTINCT p.*
                    FROM work_projects p
                    LEFT JOIN project_participants pp
                      ON pp.project_id = p.project_id
                     AND pp.membership_id = ?
                     AND pp.status = 'active'
                    WHERE p.organization_id = ?
                      AND (
                        p.is_default_internal_project = 1
                        OR p.created_by_membership_id = ?
                        OR pp.membership_id IS NOT NULL
                      )
                    ORDER BY p.is_default_internal_project DESC,
                             p.updated_at DESC, p.project_id
                    """,
                    (
                        identity.membership_id,
                        identity.organization_id,
                        identity.membership_id,
                    ),
                ).fetchall()
                existing_project_ids = {str(row["project_id"]) for row in project_rows}
                missing_project_ids = referenced_project_ids - existing_project_ids
                if missing_project_ids:
                    placeholders = ",".join("?" for _ in missing_project_ids)
                    project_rows = [
                        *project_rows,
                        *connection.execute(
                            f"""
                            SELECT * FROM work_projects
                            WHERE organization_id = ?
                              AND project_id IN ({placeholders})
                            ORDER BY updated_at DESC, project_id
                            """,
                            (identity.organization_id, *sorted(missing_project_ids)),
                        ).fetchall(),
                    ]

            task_items: list[dict[str, Any]] = []
            for row in task_rows:
                collaborators = [
                    dict(item)
                    for item in connection.execute(
                        """
                        SELECT tc.membership_id AS membershipId,
                               p.display_name AS displayName,
                               tc.collaborator_role AS role,
                               tc.inbox_state AS inboxState,
                               tc.return_reason AS returnReason,
                               tc.handled_at AS handledAt
                        FROM task_collaborators tc
                        JOIN organization_memberships om
                          ON om.membership_id = tc.membership_id
                        JOIN identity_principals p
                          ON p.principal_id = om.principal_id
                        WHERE tc.organization_id = ? AND tc.task_id = ?
                        ORDER BY tc.collaborator_role = 'owner' DESC,
                                 tc.order_index, p.display_name
                        """,
                        (identity.organization_id, row["task_id"]),
                    ).fetchall()
                ]
                task_items.append(
                    {
                        "taskId": row["task_id"],
                        "projectId": row["project_id"],
                        "title": row["title"],
                        "description": row["description"],
                        "createdByMembershipId": row["created_by_membership_id"],
                        "priority": row["priority"],
                        "lifecycleState": row["lifecycle_state"],
                        "visibilityScope": row["visibility_scope"],
                        "startDate": row["start_date"],
                        "dueDate": row["due_date"],
                        "scheduledStartAt": row["scheduled_start_at"],
                        "scheduledEndAt": row["scheduled_end_at"],
                        "deadlineAt": row["deadline_at"],
                        "durationMinutes": row["duration_minutes"],
                        "completionNote": row["completion_note"],
                        "completedAt": row["completed_at"],
                        "version": row["version"],
                        "createdAt": row["created_at"],
                        "updatedAt": row["updated_at"],
                        "collaborators": collaborators,
                    }
                )

            event_items: list[dict[str, Any]] = []
            for row in event_rows:
                counts = connection.execute(
                    """
                    SELECT
                      COUNT(DISTINCT CASE WHEN l.link_state = 'active'
                                          THEN l.task_id END) AS task_count,
                      COUNT(DISTINCT CASE WHEN l.link_state = 'active'
                                               AND l.is_milestone = 1
                                          THEN l.task_id END) AS milestone_count,
                      (SELECT COUNT(*) FROM event_line_attachments a
                       WHERE a.organization_id = ? AND a.event_line_id = ?
                         AND a.lifecycle_state = 'active') AS attachment_count
                    FROM event_line_task_links l
                    WHERE l.organization_id = ? AND l.event_line_id = ?
                    """,
                    (
                        identity.organization_id,
                        row["event_line_id"],
                        identity.organization_id,
                        row["event_line_id"],
                    ),
                ).fetchone()
                event_items.append(
                    {
                        "eventLineId": row["event_line_id"],
                        "projectId": row["project_id"],
                        "projectAssignmentState": row["project_assignment_state"],
                        "createdByMembershipId": row["created_by_membership_id"],
                        "departmentId": row["department_id"],
                        "name": row["name"],
                        "goal": row["goal"],
                        "background": row["background"],
                        "visibilityScope": row["visibility_scope"],
                        "lifecycleState": row["lifecycle_state"],
                        "version": row["version"],
                        "updatedAt": row["updated_at"],
                        "taskCount": int(counts["task_count"] or 0),
                        "milestoneCount": int(counts["milestone_count"] or 0),
                        "attachmentCount": int(counts["attachment_count"] or 0),
                    }
                )

            report_rows: list[sqlite3.Row] = []
            if visible_event_ids:
                placeholders = ",".join("?" for _ in visible_event_ids)
                report_rows = connection.execute(
                    f"""
                    SELECT * FROM narrative_outputs
                    WHERE organization_id = ?
                      AND event_line_id IN ({placeholders})
                    ORDER BY updated_at DESC, narrative_output_id
                    """,
                    (identity.organization_id, *sorted(visible_event_ids)),
                ).fetchall()

            plan_parameters: list[Any] = [identity.organization_id]
            plan_scope = ""
            if not can_read_organization:
                if department_ids:
                    placeholders = ",".join("?" for _ in department_ids)
                    plan_scope = (
                        f"AND (department_id IS NULL OR department_id IN ({placeholders}))"
                    )
                    plan_parameters.extend(sorted(department_ids))
                else:
                    plan_scope = "AND department_id IS NULL"
            if not identity.is_admin:
                plan_scope += (
                    " AND json_type(attributes_json, '$.agentKey') IS NULL"
                )
            plan_rows = connection.execute(
                f"""
                SELECT * FROM organization_plans
                WHERE organization_id = ? {plan_scope}
                ORDER BY updated_at DESC, plan_id
                """,
                tuple(plan_parameters),
            ).fetchall()
            plan_items_by_id: dict[str, list[dict[str, Any]]] = {}
            for row in plan_rows:
                plan_items_by_id[str(row["plan_id"])] = [
                    {
                        "planItemId": item["plan_item_id"],
                        "title": item["title"],
                        "statement": item["statement"],
                        "ownerMembershipId": item["owner_membership_id"],
                        "expectedOutput": item["expected_output"],
                        "status": item["status"],
                        "sortOrder": item["sort_order"],
                        "version": item["version"],
                        "updatedAt": item["updated_at"],
                    }
                    for item in connection.execute(
                        """
                        SELECT * FROM organization_plan_items
                        WHERE organization_id = ? AND plan_id = ?
                        ORDER BY sort_order, updated_at, plan_item_id
                        """,
                        (identity.organization_id, row["plan_id"]),
                    ).fetchall()
                ]

            review_parameters: list[Any] = [identity.organization_id]
            if can_read_organization:
                review_scope = ""
            elif lead_department_ids:
                placeholders = ",".join("?" for _ in lead_department_ids)
                review_scope = f"""
                    AND membership_id IN (
                        SELECT membership_id FROM department_memberships
                        WHERE organization_id = ? AND status = 'active'
                          AND department_id IN ({placeholders})
                    )
                """
                review_parameters.append(identity.organization_id)
                review_parameters.extend(sorted(lead_department_ids))
            else:
                review_scope = "AND membership_id = ?"
                review_parameters.append(identity.membership_id)
            review_rows = connection.execute(
                f"""
                SELECT * FROM weekly_reviews
                WHERE organization_id = ? {review_scope}
                ORDER BY week_label DESC, updated_at DESC, weekly_review_id
                """,
                tuple(review_parameters),
            ).fetchall()

            if can_read_organization:
                intelligence_rows = connection.execute(
                    """
                    SELECT * FROM intelligence_records
                    WHERE organization_id = ?
                    ORDER BY updated_at DESC, intelligence_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            else:
                intelligence_rows = connection.execute(
                    """
                    SELECT * FROM intelligence_records
                    WHERE organization_id = ?
                      AND (
                        visibility_scope = 'organization'
                        OR created_by_membership_id = ?
                        OR project_id IN (
                          SELECT project_id FROM project_participants
                          WHERE organization_id = ? AND membership_id = ?
                            AND status = 'active'
                        )
                      )
                    ORDER BY updated_at DESC, intelligence_id
                    """,
                    (
                        identity.organization_id,
                        identity.membership_id,
                        identity.organization_id,
                        identity.membership_id,
                    ),
                ).fetchall()

            growth_membership_ids = {identity.membership_id}
            if can_read_organization:
                growth_membership_ids = {
                    str(row["membership_id"])
                    for row in connection.execute(
                        """
                        SELECT membership_id FROM organization_memberships
                        WHERE organization_id = ? AND status = 'active'
                        """,
                        (identity.organization_id,),
                    ).fetchall()
                }
            elif lead_department_ids:
                placeholders = ",".join("?" for _ in lead_department_ids)
                growth_membership_ids.update(
                    str(row["membership_id"])
                    for row in connection.execute(
                        f"""
                        SELECT DISTINCT membership_id
                        FROM department_memberships
                        WHERE organization_id = ? AND status = 'active'
                          AND department_id IN ({placeholders})
                        """,
                        (identity.organization_id, *sorted(lead_department_ids)),
                    ).fetchall()
                )
            growth_placeholders = ",".join("?" for _ in growth_membership_ids)
            growth_signal_rows = connection.execute(
                f"""
                SELECT * FROM growth_signals
                WHERE organization_id = ?
                  AND membership_id IN ({growth_placeholders})
                ORDER BY updated_at DESC, growth_signal_id
                """,
                (identity.organization_id, *sorted(growth_membership_ids)),
            ).fetchall()
            growth_evidence_rows = connection.execute(
                f"""
                SELECT * FROM growth_evidence
                WHERE organization_id = ?
                  AND membership_id IN ({growth_placeholders})
                ORDER BY updated_at DESC, growth_evidence_id
                """,
                (identity.organization_id, *sorted(growth_membership_ids)),
            ).fetchall()
            experience_rows = connection.execute(
                """
                SELECT * FROM experience_quotes
                WHERE organization_id = ? AND lifecycle_state = 'active'
                ORDER BY updated_at DESC, experience_quote_id
                """,
                (identity.organization_id,),
            ).fetchall()
            ai_answer_rows = connection.execute(
                """
                SELECT * FROM ai_answers
                WHERE organization_id = ? AND membership_id = ?
                  AND lifecycle_state = 'active'
                ORDER BY updated_at DESC, ai_answer_id
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchall()
            favorite_rows = connection.execute(
                """
                SELECT * FROM workbench_favorites
                WHERE organization_id = ? AND membership_id = ?
                ORDER BY created_at DESC, favorite_id
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchall()

        return {
            "organizationId": identity.organization_id,
            "generatedAt": utc_now(),
            "projects": [
                {
                    "projectId": row["project_id"],
                    "name": row["name"],
                    "alias": row["alias"],
                    "summary": row["summary"],
                    "domain": row["domain"],
                    "color": row["color"],
                    "isDefaultInternalProject": bool(
                        row["is_default_internal_project"]
                    ),
                    "lifecycleState": row["lifecycle_state"],
                    "version": row["version"],
                    "updatedAt": row["updated_at"],
                }
                for row in project_rows
            ],
            "tasks": task_items,
            "eventLines": event_items,
            "documents": [
                {
                    "documentId": row["document_id"],
                    "projectId": row["project_id"],
                    "projectAssignmentState": row["project_assignment_state"],
                    "title": row["title"],
                    "documentKind": row["document_kind"],
                    "visibilityScope": row["visibility_scope"],
                    "parseState": row["parse_state"],
                    "lifecycleState": row["lifecycle_state"],
                    "currentVersion": row["current_version"],
                    "version": row["version"],
                    "updatedAt": row["updated_at"],
                }
                for row in document_rows
            ],
            "reports": [
                {
                    "narrativeOutputId": row["narrative_output_id"],
                    "projectId": row["project_id"],
                    "eventLineId": row["event_line_id"],
                    "outputKind": row["output_kind"],
                    "title": row["title"],
                    "lifecycleState": row["lifecycle_state"],
                    "latestVersion": row["latest_version"],
                    "version": row["version"],
                    "updatedAt": row["updated_at"],
                }
                for row in report_rows
            ],
            "plans": [
                {
                    "planId": row["plan_id"],
                    "departmentId": row["department_id"],
                    "periodLabel": row["period_label"],
                    "ownerMembershipId": row["owner_membership_id"],
                    "summary": row["summary"],
                    "status": row["status"],
                    "version": row["version"],
                    "updatedAt": row["updated_at"],
                    "items": plan_items_by_id.get(str(row["plan_id"]), []),
                }
                for row in plan_rows
            ],
            "weeklyReviews": [
                {
                    "weeklyReviewId": row["weekly_review_id"],
                    "membershipId": row["membership_id"],
                    "weekLabel": row["week_label"],
                    "workProgress": row["work_progress"],
                    "workBlocker": row["work_blocker"],
                    "workDirection": row["work_direction"],
                    "nextWeekFocus": row["next_week_focus"],
                    "supportNeeded": row["support_needed"],
                    "workFreeNote": row["work_free_note"],
                    "personalGrowthNote": (
                        row["personal_growth_note"]
                        if row["membership_id"] == identity.membership_id
                        or row["personal_visibility"] != "self"
                        else ""
                    ),
                    "personalVisibility": row["personal_visibility"],
                    "lifecycleState": row["lifecycle_state"],
                    "version": row["version"],
                    "updatedAt": row["updated_at"],
                }
                for row in review_rows
            ],
            "intelligence": [
                {
                    "intelligenceId": row["intelligence_id"],
                    "projectId": row["project_id"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "sourceUrl": row["source_url"],
                    "recordKind": row["record_kind"],
                    "status": row["status"],
                    "visibilityScope": row["visibility_scope"],
                    "version": row["version"],
                    "updatedAt": row["updated_at"],
                }
                for row in intelligence_rows
            ],
            "growthSignals": [
                {
                    "growthSignalId": row["growth_signal_id"],
                    "membershipId": row["membership_id"],
                    "sourceType": row["source_type"],
                    "sourceId": row["source_id"],
                    "weekLabel": row["week_label"],
                    "rawText": row["raw_text"],
                    "lifecycleState": row["lifecycle_state"],
                    "version": row["version"],
                    "updatedAt": row["updated_at"],
                }
                for row in growth_signal_rows
            ],
            "growthEvidence": [
                {
                    "growthEvidenceId": row["growth_evidence_id"],
                    "growthSignalId": row["growth_signal_id"],
                    "membershipId": row["membership_id"],
                    "abilityKey": row["ability_key"],
                    "evidenceType": row["evidence_type"],
                    "level": row["level"],
                    "confidence": row["confidence"],
                    "reason": row["reason"],
                    "taskId": row["task_id"],
                    "validationState": row["validation_state"],
                    "version": row["version"],
                    "updatedAt": row["updated_at"],
                }
                for row in growth_evidence_rows
            ],
            "experienceQuotes": [
                {
                    "experienceQuoteId": row["experience_quote_id"],
                    "authorMembershipId": row["author_membership_id"],
                    "quoteText": row["quote_text"],
                    "sourceExcerpt": row["source_excerpt"],
                    "sourceType": row["source_type"],
                    "sourceId": row["source_id"],
                    "category": row["category"],
                    "contributionScore": row["contribution_score"],
                    "version": row["version"],
                    "updatedAt": row["updated_at"],
                }
                for row in experience_rows
            ],
            "aiAnswers": [
                {
                    "answerId": row["ai_answer_id"],
                    "projectId": row["project_id"],
                    "question": row["question"],
                    "answerMarkdown": row["answer_markdown"],
                    "sourceManifest": json.loads(row["source_manifest_json"]),
                    "modelName": row["model_name"],
                    "version": row["version"],
                    "createdAt": row["created_at"],
                    "updatedAt": row["updated_at"],
                }
                for row in ai_answer_rows
            ],
            "favorites": [
                {
                    "favoriteId": row["favorite_id"],
                    "targetType": row["target_type"],
                    "targetId": row["target_id"],
                    "title": row["title"],
                    "createdAt": row["created_at"],
                }
                for row in favorite_rows
            ],
            "counts": {
                "projects": len(project_rows),
                "tasks": len(task_items),
                "eventLines": len(event_items),
                "documents": len(document_rows),
                "reports": len(report_rows),
                "plans": len(plan_rows),
                "weeklyReviews": len(review_rows),
                "intelligence": len(intelligence_rows),
                "growthSignals": len(growth_signal_rows),
                "growthEvidence": len(growth_evidence_rows),
                "experienceQuotes": len(experience_rows),
            },
        }

    def _visible_project(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any] | None:
        department_ids = [
            str(row["department_id"])
            for row in connection.execute(
                """
                SELECT department_id
                FROM department_memberships
                WHERE organization_id = ? AND membership_id = ?
                  AND status = 'active'
                ORDER BY department_id
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchall()
        ]
        is_management = connection.execute(
            """
            SELECT 1
            FROM management_title_memberships mtm
            JOIN management_titles mt ON mt.title_id = mtm.title_id
            WHERE mtm.organization_id = ? AND mtm.membership_id = ?
              AND mtm.status = 'active' AND mt.lifecycle_state = 'active'
            LIMIT 1
            """,
            (identity.organization_id, identity.membership_id),
        ).fetchone() is not None
        can_read_organization = (
            identity.is_admin
            or identity.visibility_scope == "organization"
            or is_management
        )
        if can_read_organization:
            row = connection.execute(
                """
                SELECT * FROM work_projects
                WHERE organization_id = ? AND project_id = ?
                """,
                (identity.organization_id, project_id),
            ).fetchone()
        else:
            event_department_clause = ""
            document_department_clause = ""
            parameters: list[Any] = [
                identity.organization_id,
                project_id,
                identity.membership_id,
                identity.membership_id,
                identity.membership_id,
                identity.membership_id,
                identity.membership_id,
                identity.membership_id,
                identity.membership_id,
            ]
            if department_ids:
                placeholders = ",".join("?" for _ in department_ids)
                event_department_clause = (
                    "OR (e.visibility_scope = 'department' "
                    f"AND e.department_id IN ({placeholders}))"
                )
                parameters.extend(department_ids)
                document_department_clause = (
                    "OR (d.visibility_scope = 'department' "
                    f"AND d.department_id IN ({placeholders}))"
                )
                parameters.extend(department_ids)
            row = connection.execute(
                f"""
                SELECT p.*
                FROM work_projects p
                WHERE p.organization_id = ?
                  AND p.project_id = ?
                  AND (
                    p.is_default_internal_project = 1
                    OR p.created_by_membership_id = ?
                    OR EXISTS (
                      SELECT 1
                      FROM project_participants pp
                      WHERE pp.project_id = p.project_id
                        AND pp.membership_id = ?
                        AND pp.status = 'active'
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM task_records t
                      LEFT JOIN task_collaborators tc
                        ON tc.task_id = t.task_id
                       AND tc.membership_id = ?
                       AND tc.inbox_state != 'returned'
                      WHERE t.organization_id = p.organization_id
                        AND t.project_id = p.project_id
                        AND (
                          t.created_by_membership_id = ?
                          OR tc.membership_id IS NOT NULL
                          OR t.visibility_scope = 'organization'
                        )
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM event_line_records e
                      LEFT JOIN event_line_participants ep
                        ON ep.event_line_id = e.event_line_id
                       AND ep.membership_id = ?
                       AND ep.status = 'active'
                      WHERE e.organization_id = p.organization_id
                        AND e.project_id = p.project_id
                        AND (
                          e.created_by_membership_id = ?
                          OR ep.membership_id IS NOT NULL
                          OR e.visibility_scope = 'organization'
                          {event_department_clause}
                        )
                    )
                    OR EXISTS (
                      SELECT 1
                      FROM knowledge_documents d
                      WHERE d.organization_id = p.organization_id
                        AND d.project_id = p.project_id
                        AND (
                          d.owner_membership_id = ?
                          OR d.visibility_scope = 'organization'
                          {document_department_clause}
                        )
                    )
                  )
                """,
                tuple(parameters),
            ).fetchone()
        if row is None:
            return None
        return {
            "projectId": row["project_id"],
            "name": row["name"],
            "summary": row["summary"],
            "lifecycleState": row["lifecycle_state"],
            "version": row["version"],
            "updatedAt": row["updated_at"],
        }

    def project_knowledge_context(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            project = self._visible_project(
                connection,
                identity,
                project_id=project_id,
            )
            if project is None:
                raise RepositoryError(
                    404,
                    "project_missing",
                    "当前成员无法访问该项目",
                )
            document_rows = connection.execute(
                """
                SELECT d.document_id, d.document_kind, d.title,
                       d.current_version, d.version AS aggregate_version,
                       d.updated_at, v.document_version_id,
                       v.version AS content_version, v.content_hash,
                       v.preview_text
                FROM knowledge_documents AS d
                JOIN document_versions AS v
                  ON v.document_id = d.document_id
                 AND v.version = d.current_version
                WHERE d.organization_id = ?
                  AND d.project_id = ?
                  AND d.project_assignment_state = 'assigned'
                  AND d.visibility_scope = 'organization'
                  AND d.parse_state IN ('ready', 'partial_ready')
                  AND d.lifecycle_state = 'active'
                ORDER BY d.updated_at DESC, d.document_id
                """,
                (identity.organization_id, project_id),
            ).fetchall()
            narrative_rows = connection.execute(
                """
                SELECT n.narrative_output_id, n.output_kind, n.title,
                       n.lifecycle_state, n.latest_version,
                       n.version AS aggregate_version, n.updated_at,
                       v.content_hash, v.content_markdown, v.change_summary
                FROM narrative_outputs AS n
                JOIN narrative_output_versions AS v
                  ON v.narrative_output_id = n.narrative_output_id
                 AND v.version = n.latest_version
                WHERE n.organization_id = ?
                  AND n.project_id = ?
                  AND n.lifecycle_state IN ('active', 'stale')
                ORDER BY n.updated_at DESC, n.narrative_output_id
                """,
                (identity.organization_id, project_id),
            ).fetchall()

            narrative_ids = {
                str(row["narrative_output_id"]) for row in narrative_rows
            }
            evidence_rows: list[sqlite3.Row] = []
            if narrative_ids:
                placeholders = ",".join("?" for _ in narrative_ids)
                evidence_rows = connection.execute(
                    f"""
                    SELECT evidence_link_id, source_type, source_id,
                           target_id, relation_kind, version, updated_at
                    FROM evidence_links
                    WHERE organization_id = ?
                      AND target_type = 'narrative_output'
                      AND target_id IN ({placeholders})
                      AND lifecycle_state = 'active'
                    ORDER BY updated_at DESC, evidence_link_id
                    """,
                    (identity.organization_id, *sorted(narrative_ids)),
                ).fetchall()

            document_version_titles = {
                str(row["document_version_id"]): str(row["title"])
                for row in document_rows
            }
            source_asset_ids = {
                str(row["source_id"])
                for row in evidence_rows
                if row["source_type"] == "source_asset"
            }
            source_asset_titles: dict[str, str] = {}
            if source_asset_ids:
                placeholders = ",".join("?" for _ in source_asset_ids)
                source_asset_titles = {
                    str(row["source_asset_id"]): str(row["file_name"])
                    for row in connection.execute(
                        f"""
                        SELECT source_asset_id, file_name
                        FROM source_assets
                        WHERE organization_id = ?
                          AND project_id = ?
                          AND source_asset_id IN ({placeholders})
                          AND lifecycle_state = 'active'
                        """,
                        (
                            identity.organization_id,
                            project_id,
                            *sorted(source_asset_ids),
                        ),
                    ).fetchall()
                }

        items: list[dict[str, Any]] = []
        for row in document_rows:
            document_kind = str(row["document_kind"] or "").strip().lower()
            explicitly_safe = (
                document_kind in SHARED_KNOWLEDGE_DOCUMENT_KINDS
                or document_kind.endswith("_summary")
            )
            summary = str(row["preview_text"] or "").strip()
            if not explicitly_safe or not summary:
                continue
            items.append(
                {
                    "sourceScope": "organization_shared",
                    "sourceType": "knowledge_summary",
                    "sourceId": row["document_id"],
                    "sourceVersion": int(row["content_version"]),
                    "contentHash": row["content_hash"],
                    "title": row["title"],
                    "summary": summary[:2000],
                    "sourceDescription": (
                        f"组织共享知识摘要 · {document_kind}"
                    ),
                    "updatedAt": row["updated_at"],
                }
            )

        narrative_titles = {
            str(row["narrative_output_id"]): str(row["title"])
            for row in narrative_rows
        }
        for row in narrative_rows:
            saved_content = str(row["content_markdown"] or "").strip()
            change_summary = str(row["change_summary"] or "").strip()
            summary = saved_content or change_summary
            if not summary:
                continue
            items.append(
                {
                    "sourceScope": "organization_shared",
                    "sourceType": "narrative_summary",
                    "sourceId": row["narrative_output_id"],
                    "sourceVersion": int(row["latest_version"]),
                    "contentHash": row["content_hash"],
                    "title": row["title"],
                    "summary": summary[:4000],
                    "sourceDescription": (
                        "已保存的组织共享项目叙事或正式报告上下文节选"
                        f" · {row['output_kind']}"
                    ),
                    "updatedAt": row["updated_at"],
                }
            )

        for row in evidence_rows:
            source_type = str(row["source_type"])
            source_id = str(row["source_id"])
            source_title = (
                document_version_titles.get(source_id)
                if source_type == "document_version"
                else source_asset_titles.get(source_id)
                if source_type == "source_asset"
                else None
            )
            target_title = narrative_titles.get(str(row["target_id"]))
            if not source_title or not target_title:
                continue
            relationship = {
                "evidenceLinkId": row["evidence_link_id"],
                "sourceType": source_type,
                "sourceId": source_id,
                "targetId": row["target_id"],
                "relationKind": row["relation_kind"],
                "version": row["version"],
            }
            items.append(
                {
                    "sourceScope": "organization_shared",
                    "sourceType": "evidence_relationship",
                    "sourceId": row["evidence_link_id"],
                    "sourceVersion": int(row["version"]),
                    "contentHash": sha256_text(canonical_json(relationship)),
                    "title": f"{source_title} → {target_title}",
                    "summary": (
                        f"组织云已保存证据关系：资料《{source_title}》"
                        f"以“{row['relation_kind']}”关联到《{target_title}》。"
                    ),
                    "sourceDescription": "组织共享证据关系，不包含来源正文",
                    "updatedAt": row["updated_at"],
                }
            )

        return {
            "cloudInstanceId": identity.cloud_instance_id,
            "organizationId": identity.organization_id,
            "project": {
                "projectId": project["projectId"],
                "name": project["name"],
                "summary": project.get("summary") or "",
                "lifecycleState": project.get("lifecycleState") or "active",
                "version": int(project.get("version") or 1),
                "updatedAt": project.get("updatedAt"),
            },
            "organizationSharedKnowledge": items,
            "materialBoundary": {
                "sourceFileContentIncluded": False,
                "sourceFilePathsIncluded": False,
                "storageLocatorsIncluded": False,
                "unpublishedDocumentContentIncluded": False,
            },
            "state": {
                "organizationShared": "ready" if items else "empty",
                "message": (
                    ""
                    if items
                    else "组织云查询成功，但该项目尚无明确发布的共享知识摘要"
                ),
            },
        }

    def _task_payload(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        collaborators = [
            dict(item)
            for item in connection.execute(
                """
                SELECT tc.membership_id AS membershipId,
                       p.display_name AS displayName,
                       tc.collaborator_role AS role,
                       tc.inbox_state AS inboxState,
                       tc.return_reason AS returnReason,
                       tc.handled_at AS handledAt
                FROM task_collaborators tc
                JOIN organization_memberships om
                  ON om.membership_id = tc.membership_id
                JOIN identity_principals p
                  ON p.principal_id = om.principal_id
                WHERE tc.organization_id = ? AND tc.task_id = ?
                ORDER BY tc.collaborator_role = 'owner' DESC,
                         tc.order_index, p.display_name
                """,
                (row["organization_id"], row["task_id"]),
            ).fetchall()
        ]
        return {
            "taskId": row["task_id"],
            "projectId": row["project_id"],
            "title": row["title"],
            "description": row["description"],
            "createdByMembershipId": row["created_by_membership_id"],
            "priority": row["priority"],
            "lifecycleState": row["lifecycle_state"],
            "visibilityScope": row["visibility_scope"],
            "startDate": row["start_date"],
            "dueDate": row["due_date"],
            "scheduledStartAt": row["scheduled_start_at"],
            "scheduledEndAt": row["scheduled_end_at"],
            "deadlineAt": row["deadline_at"],
            "durationMinutes": row["duration_minutes"],
            "completionNote": row["completion_note"],
            "completedAt": row["completed_at"],
            "version": row["version"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "collaborators": collaborators,
        }

    def _task_row(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_id: str,
        *,
        require_edit: bool,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM task_records
            WHERE organization_id = ? AND task_id = ?
            """,
            (identity.organization_id, task_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "task_missing", "任务不存在")
        participant = connection.execute(
            """
            SELECT 1
            FROM task_collaborators
            WHERE organization_id = ? AND task_id = ? AND membership_id = ?
              AND inbox_state != 'returned'
            """,
            (identity.organization_id, task_id, identity.membership_id),
        ).fetchone()
        is_creator = row["created_by_membership_id"] == identity.membership_id
        if require_edit:
            owner = connection.execute(
                """
                SELECT membership_id
                FROM task_collaborators
                WHERE organization_id = ? AND task_id = ?
                  AND collaborator_role = 'owner'
                  AND inbox_state != 'returned'
                ORDER BY order_index, membership_id
                LIMIT 1
                """,
                (identity.organization_id, task_id),
            ).fetchone()
            owner_id = str(owner["membership_id"]) if owner is not None else ""
            actor_policy = connection.execute(
                """
                SELECT m.task_edit_scope AS membership_scope,
                       COALESCE(t.task_edit_scope, 'self') AS title_scope
                FROM organization_memberships AS m
                LEFT JOIN management_title_memberships AS mtm
                  ON mtm.organization_id = m.organization_id
                 AND mtm.membership_id = m.membership_id
                 AND mtm.status = 'active'
                LEFT JOIN management_titles AS t
                  ON t.organization_id = mtm.organization_id
                 AND t.title_id = mtm.title_id
                 AND t.lifecycle_state = 'active'
                WHERE m.organization_id = ? AND m.membership_id = ?
                  AND m.status = 'active'
                ORDER BY t.sort_order, t.title_id
                LIMIT 1
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchone()
            scopes = {
                str(actor_policy["membership_scope"] or "self"),
                str(actor_policy["title_scope"] or "self"),
            } if actor_policy is not None else {"self"}
            is_direct_manager = bool(
                owner_id
                and connection.execute(
                    """
                    SELECT 1
                    FROM organization_reporting_lines
                    WHERE organization_id = ?
                      AND manager_membership_id = ?
                      AND report_membership_id = ?
                      AND line_type = 'business'
                      AND lifecycle_state = 'active'
                    """,
                    (
                        identity.organization_id,
                        identity.membership_id,
                        owner_id,
                    ),
                ).fetchone()
            )
            same_department = bool(
                owner_id
                and connection.execute(
                    """
                    SELECT 1
                    FROM department_memberships AS actor
                    JOIN department_memberships AS owner
                      ON owner.organization_id = actor.organization_id
                     AND owner.department_id = actor.department_id
                     AND owner.status = 'active'
                    WHERE actor.organization_id = ?
                      AND actor.membership_id = ?
                      AND owner.membership_id = ?
                      AND actor.status = 'active'
                    """,
                    (
                        identity.organization_id,
                        identity.membership_id,
                        owner_id,
                    ),
                ).fetchone()
            )
            permitted = (
                identity.is_admin
                or is_creator
                or participant is not None
                or "organization" in scopes
                or ("manager" in scopes and is_direct_manager)
                or ("department" in scopes and same_department)
            )
        else:
            permitted = (
                identity.is_admin
                or identity.visibility_scope == "organization"
                or is_creator
                or participant is not None
                or row["visibility_scope"] == "organization"
            )
        if not permitted:
            raise RepositoryError(403, "task_forbidden", "无权访问该任务")
        return row

    def _task_control_rule(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        task_row: sqlite3.Row,
        *,
        action: str,
    ) -> dict[str, Any] | None:
        column_by_action = {
            "content": "content_editable_by",
            "deadline": "deadline_editable_by",
            "owner": "owner_editable_by",
            "cancel": "cancellable_by",
            "approve": None,
        }
        if action not in column_by_action:
            raise RuntimeError(f"unknown task control action: {action}")
        column = column_by_action[action]
        required_scope_expression = (
            f"{column} AS required_actor_scope"
            if column is not None
            else "'authorized_approver' AS required_actor_scope"
        )
        owner = connection.execute(
            """
            SELECT membership_id
            FROM task_collaborators
            WHERE organization_id = ? AND task_id = ?
              AND collaborator_role = 'owner'
              AND inbox_state != 'returned'
            ORDER BY order_index, membership_id
            LIMIT 1
            """,
            (identity.organization_id, task_row["task_id"]),
        ).fetchone()
        owner_id = str(owner["membership_id"]) if owner is not None else ""
        owner_scope = connection.execute(
            """
            SELECT dm.department_id, mtm.title_id
            FROM organization_memberships AS m
            LEFT JOIN department_memberships AS dm
              ON dm.organization_id = m.organization_id
             AND dm.membership_id = m.membership_id
             AND dm.status = 'active'
            LEFT JOIN management_title_memberships AS mtm
              ON mtm.organization_id = m.organization_id
             AND mtm.membership_id = m.membership_id
             AND mtm.status = 'active'
            WHERE m.organization_id = ? AND m.membership_id = ?
              AND m.status = 'active'
            ORDER BY dm.updated_at DESC, mtm.updated_at DESC
            LIMIT 1
            """,
            (identity.organization_id, owner_id),
        ).fetchone()
        owner_department_id = (
            str(owner_scope["department_id"])
            if owner_scope is not None and owner_scope["department_id"]
            else None
        )
        owner_title_id = (
            str(owner_scope["title_id"])
            if owner_scope is not None and owner_scope["title_id"]
            else None
        )
        rule = connection.execute(
            f"""
            SELECT *, {required_scope_expression}
            FROM organization_task_control_rules
            WHERE organization_id = ? AND lifecycle_state = 'active'
              AND (department_id IS NULL OR department_id IS ?)
              AND (title_id IS NULL OR title_id IS ?)
            ORDER BY (title_id IS NOT NULL) DESC,
                     (department_id IS NOT NULL) DESC,
                     version DESC, updated_at DESC, task_control_rule_id
            LIMIT 1
            """,
            (
                identity.organization_id,
                owner_department_id,
                owner_title_id,
            ),
        ).fetchone()
        if rule is None:
            return None
        required_scope = str(rule["required_actor_scope"])
        is_creator = (
            str(task_row["created_by_membership_id"]) == identity.membership_id
        )
        is_assignee = bool(owner_id and owner_id == identity.membership_id)
        manager_line = (
            connection.execute(
                """
                SELECT approves_tasks, can_adjust_tasks,
                       can_change_deadline, can_reassign_tasks
                FROM organization_reporting_lines
                WHERE organization_id = ? AND manager_membership_id = ?
                  AND report_membership_id = ? AND line_type = 'business'
                  AND lifecycle_state = 'active'
                """,
                (
                    identity.organization_id,
                    identity.membership_id,
                    owner_id,
                ),
            ).fetchone()
            if owner_id
            else None
        )
        is_manager = manager_line is not None
        actor_policy = connection.execute(
            """
            SELECT m.can_approve_tasks AS membership_can_approve,
                   m.can_reassign_tasks AS membership_can_reassign,
                   m.can_change_deadline AS membership_can_change_deadline,
                   COALESCE(MAX(t.can_approve_tasks), 0) AS title_can_approve,
                   COALESCE(MAX(t.can_reassign_tasks), 0) AS title_can_reassign,
                   COALESCE(MAX(t.can_change_deadline), 0)
                     AS title_can_change_deadline
            FROM organization_memberships AS m
            LEFT JOIN management_title_memberships AS mtm
              ON mtm.organization_id = m.organization_id
             AND mtm.membership_id = m.membership_id
             AND mtm.status = 'active'
            LEFT JOIN management_titles AS t
              ON t.organization_id = mtm.organization_id
             AND t.title_id = mtm.title_id
             AND t.lifecycle_state = 'active'
            WHERE m.organization_id = ? AND m.membership_id = ?
              AND m.status = 'active'
            GROUP BY m.membership_id
            """,
            (identity.organization_id, identity.membership_id),
        ).fetchone()
        actor_can_approve = bool(
            actor_policy
            and (
                actor_policy["membership_can_approve"]
                or actor_policy["title_can_approve"]
            )
        )
        actor_can_reassign = bool(
            actor_policy
            and (
                actor_policy["membership_can_reassign"]
                or actor_policy["title_can_reassign"]
            )
        )
        actor_can_change_deadline = bool(
            actor_policy
            and (
                actor_policy["membership_can_change_deadline"]
                or actor_policy["title_can_change_deadline"]
            )
        )
        is_department_lead = bool(
            owner_department_id
            and connection.execute(
                """
                SELECT 1 FROM department_memberships
                WHERE organization_id = ? AND department_id = ?
                  AND membership_id = ? AND is_department_lead = 1
                  AND status = 'active'
                """,
                (
                    identity.organization_id,
                    owner_department_id,
                    identity.membership_id,
                ),
            ).fetchone()
        )
        is_organization_lead = identity.is_admin or bool(
            connection.execute(
                """
                SELECT 1
                FROM management_title_memberships AS mtm
                JOIN management_titles AS t
                  ON t.organization_id = mtm.organization_id
                 AND t.title_id = mtm.title_id
                WHERE mtm.organization_id = ? AND mtm.membership_id = ?
                  AND mtm.status = 'active'
                  AND t.lifecycle_state = 'active'
                  AND t.level = 'organization_lead'
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchone()
        )
        if action == "approve":
            permitted = bool(
                identity.is_admin
                or str(rule["default_approver_membership_id"] or "")
                == identity.membership_id
                or actor_can_approve
                or (
                    manager_line is not None
                    and bool(manager_line["approves_tasks"])
                )
            )
        else:
            manager_capability = {
                "content": "can_adjust_tasks",
                "cancel": "can_adjust_tasks",
                "deadline": "can_change_deadline",
                "owner": "can_reassign_tasks",
            }[action]
            manager_permitted = bool(
                manager_line is not None and manager_line[manager_capability]
            )
            department_lead_permitted = is_department_lead
            organization_lead_permitted = is_organization_lead
            if action == "deadline":
                department_lead_permitted = (
                    department_lead_permitted and actor_can_change_deadline
                )
                organization_lead_permitted = (
                    organization_lead_permitted
                    and (identity.is_admin or actor_can_change_deadline)
                )
            elif action == "owner":
                department_lead_permitted = (
                    department_lead_permitted and actor_can_reassign
                )
                organization_lead_permitted = (
                    organization_lead_permitted
                    and (identity.is_admin or actor_can_reassign)
                )
            permitted = {
                "assignee": is_assignee,
                "creator": is_creator,
                "manager": manager_permitted,
                "department_lead": department_lead_permitted,
                "organization_lead": organization_lead_permitted,
            }.get(required_scope, False)
        if not permitted:
            raise RepositoryError(
                403,
                "task_control_rule_forbidden",
                "当前组织任务控制规则不允许执行该操作",
            )
        return {
            "ruleId": str(rule["task_control_rule_id"]),
            "ruleVersion": int(rule["version"]),
            "action": action,
            "requiredActorScope": required_scope,
        }

    def _ensure_project(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        project_id: str | None,
    ) -> None:
        if not project_id:
            return
        row = connection.execute(
            """
            SELECT 1 FROM work_projects
            WHERE organization_id = ? AND project_id = ?
              AND lifecycle_state = 'active'
            """,
            (identity.organization_id, project_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "project_missing", "所选项目不存在或已归档")

    def _ensure_memberships(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        membership_ids: set[str],
    ) -> None:
        if not membership_ids:
            return
        placeholders = ",".join("?" for _ in membership_ids)
        rows = connection.execute(
            f"""
            SELECT membership_id
            FROM organization_memberships
            WHERE organization_id = ? AND status = 'active'
              AND membership_id IN ({placeholders})
            """,
            (identity.organization_id, *sorted(membership_ids)),
        ).fetchall()
        found = {str(row["membership_id"]) for row in rows}
        if found != membership_ids:
            raise RepositoryError(422, "task_member_invalid", "负责人或协作者不属于当前组织")

    def _task_receipt(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any] | None:
        receipt = connection.execute(
            """
            SELECT payload_hash, result_json
            FROM command_idempotency
            WHERE scope_id = ? AND actor_principal_id = ?
              AND command_type = ? AND idempotency_key = ?
            """,
            (
                identity.scope_id,
                identity.principal_id,
                command_type,
                idempotency_key,
            ),
        ).fetchone()
        if receipt is None:
            return None
        if str(receipt["payload_hash"]) != payload_hash:
            raise RepositoryError(409, "idempotency_conflict", "操作标识冲突")
        return json.loads(str(receipt["result_json"]))

    def _record_task_command(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        task_id: str,
        expected_version: int | None,
        before_version: int | None,
        after_version: int,
        payload: dict[str, Any],
        result: dict[str, Any],
        policy_evidence: list[dict[str, Any]] | None = None,
    ) -> None:
        now = utc_now()
        operation_id = new_id()
        payload_json = canonical_json(payload)
        payload_hash = payload_fingerprint(payload)
        result_json = canonical_json(result)
        connection.execute(
            """
            INSERT INTO command_envelopes (
                command_id, scope_id, organization_id, operation_id,
                idempotency_key, aggregate_type, aggregate_id,
                command_type, actor_principal_id, expected_version,
                payload_json, payload_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'task', ?, ?, ?, ?, ?, ?,
                      'committed', ?, ?)
            """,
            (
                new_id(),
                identity.scope_id,
                identity.organization_id,
                operation_id,
                idempotency_key,
                task_id,
                command_type,
                identity.principal_id,
                expected_version,
                payload_json,
                payload_hash,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO command_idempotency (
                record_id, scope_id, actor_principal_id, command_type,
                idempotency_key, payload_hash, result_hash, result_json,
                expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                      '9999-12-31T23:59:59.999Z', ?)
            """,
            (
                new_id(),
                identity.scope_id,
                identity.principal_id,
                command_type,
                idempotency_key,
                payload_hash,
                sha256_text(result_json),
                result_json,
                now,
            ),
        )
        self._insert_audit(
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            actor_id=identity.principal_id,
            action=command_type,
            resource_type="task",
            resource_id=task_id,
            before_version=before_version,
            after_version=after_version,
            summary={
                **payload,
                **(
                    {"taskControlRules": policy_evidence}
                    if policy_evidence
                    else {}
                ),
            },
        )
        self._insert_outbox(
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            aggregate_type="task",
            aggregate_id=task_id,
            aggregate_version=after_version,
            event_type=command_type,
            payload={"taskId": task_id, "version": after_version},
        )

    def task_detail(
        self,
        identity: SessionIdentity,
        *,
        task_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            row = self._task_row(connection, identity, task_id, require_edit=False)
            return {"task": self._task_payload(connection, row)}

    def create_event_line(
        self,
        identity: SessionIdentity,
        *,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "event_line.created"
        normalized = {
            "projectId": str(payload.get("projectId") or "").strip(),
            "name": str(payload.get("name") or "").strip(),
            "goal": str(payload.get("goal") or "").strip(),
            "background": str(payload.get("background") or "").strip(),
            "participantMembershipIds": sorted(
                {
                    str(value)
                    for value in payload.get("participantMembershipIds") or []
                    if str(value)
                }
            ),
        }
        if not normalized["projectId"]:
            raise RepositoryError(422, "event_line_project_required", "请选择所属项目")
        if not normalized["name"]:
            raise RepositoryError(422, "event_line_name_required", "请输入事件线名称")
        payload_hash = payload_fingerprint(normalized)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._task_receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self._ensure_project(
                    connection,
                    identity,
                    normalized["projectId"],
                )
                participant_ids = set(normalized["participantMembershipIds"])
                self._ensure_memberships(connection, identity, participant_ids)
                department = connection.execute(
                    """
                    SELECT department_id
                    FROM department_memberships
                    WHERE organization_id = ? AND membership_id = ?
                      AND status = 'active'
                    ORDER BY created_at, department_id
                    LIMIT 1
                    """,
                    (identity.organization_id, identity.membership_id),
                ).fetchone()
                department_id = (
                    str(department["department_id"]) if department is not None else None
                )
                visibility_scope = "department" if department_id else "participants"
                now = utc_now()
                event_line_id = new_id()
                connection.execute(
                    """
                    INSERT INTO event_line_records (
                        event_line_id, organization_id, project_id,
                        project_assignment_state, created_by_membership_id,
                        department_id, name, goal, background, visibility_scope,
                        lifecycle_state, version, created_at, updated_at, archived_at
                    ) VALUES (?, ?, ?, 'assigned', ?, ?, ?, ?, ?, ?,
                              'active', 1, ?, ?, NULL)
                    """,
                    (
                        event_line_id,
                        identity.organization_id,
                        normalized["projectId"],
                        identity.membership_id,
                        department_id,
                        normalized["name"],
                        normalized["goal"],
                        normalized["background"],
                        visibility_scope,
                        now,
                        now,
                    ),
                )
                for membership_id in sorted(participant_ids):
                    connection.execute(
                        """
                        INSERT INTO event_line_participants (
                            event_line_id, organization_id, membership_id,
                            status, version, created_at, updated_at
                        ) VALUES (?, ?, ?, 'active', 1, ?, ?)
                        """,
                        (
                            event_line_id,
                            identity.organization_id,
                            membership_id,
                            now,
                            now,
                        ),
                    )
                event_line = {
                    "eventLineId": event_line_id,
                    "projectId": normalized["projectId"],
                    "projectAssignmentState": "assigned",
                    "createdByMembershipId": identity.membership_id,
                    "departmentId": department_id,
                    "name": normalized["name"],
                    "goal": normalized["goal"],
                    "background": normalized["background"],
                    "visibilityScope": visibility_scope,
                    "lifecycleState": "active",
                    "version": 1,
                    "updatedAt": now,
                    "taskCount": 0,
                    "milestoneCount": 0,
                    "attachmentCount": 0,
                }
                result = {"eventLine": event_line}
                operation_id = new_id()
                payload_json = canonical_json(normalized)
                result_json = canonical_json(result)
                connection.execute(
                    """
                    INSERT INTO command_envelopes (
                        command_id, scope_id, organization_id, operation_id,
                        idempotency_key, aggregate_type, aggregate_id,
                        command_type, actor_principal_id, expected_version,
                        payload_json, payload_hash, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'event_line', ?, ?, ?, NULL,
                              ?, ?, 'committed', ?, ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                        idempotency_key,
                        event_line_id,
                        command_type,
                        identity.principal_id,
                        payload_json,
                        payload_hash,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO command_idempotency (
                        record_id, scope_id, actor_principal_id, command_type,
                        idempotency_key, payload_hash, result_hash, result_json,
                        expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                              '9999-12-31T23:59:59.999Z', ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        identity.principal_id,
                        command_type,
                        idempotency_key,
                        payload_hash,
                        sha256_text(result_json),
                        result_json,
                        now,
                    ),
                )
                self._insert_audit(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    actor_id=identity.principal_id,
                    action=command_type,
                    resource_type="event_line",
                    resource_id=event_line_id,
                    before_version=None,
                    after_version=1,
                    summary=normalized,
                )
                self._insert_outbox(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    aggregate_type="event_line",
                    aggregate_id=event_line_id,
                    aggregate_version=1,
                    event_type=command_type,
                    payload={"eventLineId": event_line_id, "version": 1},
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def handle_task_inbox(
        self,
        identity: SessionIdentity,
        *,
        task_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "task.inbox_handled"
        payload = {"expectedVersion": expected_version}
        payload_hash = payload_fingerprint(payload)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._task_receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                task_row = self._task_row(
                    connection,
                    identity,
                    task_id,
                    require_edit=False,
                )
                collaborator = connection.execute(
                    """
                    SELECT collaborator_role, inbox_state, version
                    FROM task_collaborators
                    WHERE organization_id = ? AND task_id = ? AND membership_id = ?
                    """,
                    (
                        identity.organization_id,
                        task_id,
                        identity.membership_id,
                    ),
                ).fetchone()
                if collaborator is None:
                    raise RepositoryError(403, "task_inbox_forbidden", "当前任务无需你处理")
                if collaborator["inbox_state"] != "pending":
                    result = {
                        "task": self._task_payload(connection, task_row),
                        "handledAs": (
                            "accepted"
                            if collaborator["collaborator_role"] == "owner"
                            else "acknowledged"
                        ),
                    }
                    connection.rollback()
                    return result
                if int(task_row["version"]) != expected_version:
                    raise RepositoryError(409, "task_version_conflict", "任务已更新，请刷新后重试")
                handled_as = (
                    "accepted"
                    if collaborator["collaborator_role"] == "owner"
                    else "acknowledged"
                )
                now = utc_now()
                next_version = expected_version + 1
                connection.execute(
                    """
                    UPDATE task_collaborators
                    SET inbox_state = ?, handled_at = ?, version = version + 1,
                        updated_at = ?
                    WHERE organization_id = ? AND task_id = ? AND membership_id = ?
                      AND inbox_state = 'pending'
                    """,
                    (
                        handled_as,
                        now,
                        now,
                        identity.organization_id,
                        task_id,
                        identity.membership_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE task_records
                    SET version = ?, updated_at = ?
                    WHERE organization_id = ? AND task_id = ? AND version = ?
                    """,
                    (
                        next_version,
                        now,
                        identity.organization_id,
                        task_id,
                        expected_version,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO task_activity_events (
                        task_activity_id, organization_id, task_id,
                        actor_membership_id, event_type, payload_json, happened_at
                    ) VALUES (?, ?, ?, ?, 'task.inbox_handled', ?, ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        task_id,
                        identity.membership_id,
                        canonical_json({"handledAs": handled_as}),
                        now,
                    ),
                )
                updated = connection.execute(
                    "SELECT * FROM task_records WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                result = {
                    "task": self._task_payload(connection, updated),
                    "handledAs": handled_as,
                }
                self._record_task_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    task_id=task_id,
                    expected_version=expected_version,
                    before_version=expected_version,
                    after_version=next_version,
                    payload=payload,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def create_task(
        self,
        identity: SessionIdentity,
        *,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "task.created"
        normalized = {
            **payload,
            "title": str(payload.get("title") or "").strip(),
            "description": str(payload.get("description") or "").strip(),
        }
        if not normalized["title"]:
            raise RepositoryError(422, "task_title_required", "请输入任务标题")
        payload_hash = payload_fingerprint(normalized)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._task_receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                project_id = normalized.get("projectId")
                self._ensure_project(connection, identity, project_id)
                owner_id = str(
                    normalized.get("ownerMembershipId") or identity.membership_id
                )
                collaborator_ids = {
                    str(value)
                    for value in normalized.get("collaboratorMembershipIds") or []
                    if str(value)
                }
                collaborator_ids.discard(owner_id)
                self._ensure_memberships(
                    connection,
                    identity,
                    {owner_id, *collaborator_ids},
                )
                now = utc_now()
                task_id = new_id()
                connection.execute(
                    """
                    INSERT INTO task_records (
                        task_id, organization_id, project_id, title, description,
                        created_by_membership_id, priority, lifecycle_state,
                        task_kind, visibility_scope, start_date, due_date,
                        scheduled_start_at, scheduled_end_at, deadline_at,
                        duration_minutes, completion_note, completed_at,
                        source_type, source_id, attributes_json, version,
                        created_at, updated_at, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'todo', 'task', ?, ?, ?, ?, ?,
                              ?, ?, '', NULL, 'manual', NULL, '{}', 1, ?, ?, NULL)
                    """,
                    (
                        task_id,
                        identity.organization_id,
                        project_id,
                        normalized["title"],
                        normalized["description"],
                        identity.membership_id,
                        normalized.get("priority") or "normal",
                        normalized.get("visibilityScope") or "participants",
                        normalized.get("startDate"),
                        normalized.get("dueDate"),
                        normalized.get("scheduledStartAt"),
                        normalized.get("scheduledEndAt"),
                        normalized.get("deadlineAt"),
                        int(normalized.get("durationMinutes") or 60),
                        now,
                        now,
                    ),
                )
                members = [(owner_id, "owner", 0), *[
                    (membership_id, "collaborator", index + 1)
                    for index, membership_id in enumerate(sorted(collaborator_ids))
                ]]
                for membership_id, role, order_index in members:
                    inbox_state = (
                        "accepted"
                        if role == "owner" and membership_id == identity.membership_id
                        else "acknowledged"
                        if role == "collaborator" and membership_id == identity.membership_id
                        else "pending"
                    )
                    connection.execute(
                        """
                        INSERT INTO task_collaborators (
                            task_id, organization_id, membership_id,
                            collaborator_role, inbox_state, order_index,
                            return_reason, handled_at, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, '', ?, 1, ?, ?)
                        """,
                        (
                            task_id,
                            identity.organization_id,
                            membership_id,
                            role,
                            inbox_state,
                            order_index,
                            now if inbox_state != "pending" else None,
                            now,
                            now,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO task_activity_events (
                        task_activity_id, organization_id, task_id,
                        actor_membership_id, event_type, payload_json, happened_at
                    ) VALUES (?, ?, ?, ?, 'task.created', ?, ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        task_id,
                        identity.membership_id,
                        canonical_json({"title": normalized["title"]}),
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM task_records WHERE task_id = ?",
                    (task_id,),
                ).fetchone()
                result = {"task": self._task_payload(connection, row)}
                self._record_task_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    task_id=task_id,
                    expected_version=None,
                    before_version=None,
                    after_version=1,
                    payload=normalized,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def update_task(
        self,
        identity: SessionIdentity,
        *,
        task_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
        transition: str | None = None,
    ) -> dict[str, Any]:
        expected_version = int(payload.get("expectedVersion") or 0)
        command_type = (
            f"task.{transition}" if transition else "task.updated"
        )
        normalized = dict(payload)
        payload_hash = payload_fingerprint(normalized)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._task_receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self._task_row(connection, identity, task_id, require_edit=True)
                current_version = int(row["version"])
                if expected_version != current_version:
                    raise RepositoryError(
                        409,
                        "task_version_conflict",
                        "任务已被更新，请刷新后重试",
                    )
                control_actions: set[str] = set()
                if transition == "cancelled":
                    control_actions.add("cancel")
                elif transition is not None:
                    control_actions.add("content")
                if any(
                    key in normalized
                    for key in (
                        "title",
                        "description",
                        "projectId",
                        "priority",
                        "visibilityScope",
                        "startDate",
                        "scheduledStartAt",
                        "durationMinutes",
                    )
                ):
                    control_actions.add("content")
                if any(
                    key in normalized
                    for key in ("dueDate", "deadlineAt", "scheduledEndAt")
                ):
                    control_actions.add("deadline")
                if any(
                    key in normalized
                    for key in (
                        "ownerMembershipId",
                        "collaboratorMembershipIds",
                    )
                ):
                    control_actions.add("owner")
                if not control_actions:
                    control_actions.add("content")
                policy_evidence = [
                    evidence
                    for action in sorted(control_actions)
                    if (
                        evidence := self._task_control_rule(
                            connection,
                            identity,
                            row,
                            action=action,
                        )
                    )
                    is not None
                ]
                now = utc_now()
                field_map = {
                    "title": "title",
                    "description": "description",
                    "projectId": "project_id",
                    "priority": "priority",
                    "visibilityScope": "visibility_scope",
                    "startDate": "start_date",
                    "dueDate": "due_date",
                    "scheduledStartAt": "scheduled_start_at",
                    "scheduledEndAt": "scheduled_end_at",
                    "deadlineAt": "deadline_at",
                    "durationMinutes": "duration_minutes",
                }
                updates: dict[str, Any] = {}
                for source, target in field_map.items():
                    if source in normalized:
                        updates[target] = normalized[source]
                if "title" in updates:
                    updates["title"] = str(updates["title"] or "").strip()
                    if not updates["title"]:
                        raise RepositoryError(
                            422,
                            "task_title_required",
                            "请输入任务标题",
                        )
                if "project_id" in updates:
                    self._ensure_project(connection, identity, updates["project_id"])
                if transition == "completed":
                    updates.update(
                        lifecycle_state="completed",
                        completed_at=now,
                        completion_note=str(normalized.get("completionNote") or ""),
                    )
                elif transition == "cancelled":
                    updates.update(
                        lifecycle_state="cancelled",
                        completed_at=None,
                        completion_note=str(normalized.get("completionNote") or ""),
                    )
                elif transition == "restored":
                    updates.update(
                        lifecycle_state="todo",
                        completed_at=None,
                        completion_note="",
                    )
                assignments = [f"{column} = ?" for column in updates]
                values = list(updates.values())
                assignments.extend(["version = version + 1", "updated_at = ?"])
                values.extend([now, identity.organization_id, task_id, current_version])
                changed = connection.execute(
                    f"""
                    UPDATE task_records
                    SET {", ".join(assignments)}
                    WHERE organization_id = ? AND task_id = ? AND version = ?
                    """,
                    values,
                )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "task_version_conflict",
                        "任务已被更新，请刷新后重试",
                    )
                if (
                    "ownerMembershipId" in normalized
                    or "collaboratorMembershipIds" in normalized
                ):
                    existing = connection.execute(
                        """
                        SELECT membership_id, collaborator_role
                        FROM task_collaborators
                        WHERE organization_id = ? AND task_id = ?
                          AND inbox_state != 'returned'
                        """,
                        (identity.organization_id, task_id),
                    ).fetchall()
                    current_owner = next(
                        (
                            str(item["membership_id"])
                            for item in existing
                            if item["collaborator_role"] == "owner"
                        ),
                        identity.membership_id,
                    )
                    current_collaborators = {
                        str(item["membership_id"])
                        for item in existing
                        if item["collaborator_role"] == "collaborator"
                    }
                    owner_id = str(
                        normalized.get("ownerMembershipId") or current_owner
                    )
                    collaborator_ids = (
                        {
                            str(value)
                            for value in normalized.get("collaboratorMembershipIds") or []
                            if str(value)
                        }
                        if "collaboratorMembershipIds" in normalized
                        else current_collaborators
                    )
                    collaborator_ids.discard(owner_id)
                    self._ensure_memberships(
                        connection,
                        identity,
                        {owner_id, *collaborator_ids},
                    )
                    connection.execute(
                        """
                        DELETE FROM task_collaborators
                        WHERE organization_id = ? AND task_id = ?
                        """,
                        (identity.organization_id, task_id),
                    )
                    members = [(owner_id, "owner", 0), *[
                        (membership_id, "collaborator", index + 1)
                        for index, membership_id in enumerate(sorted(collaborator_ids))
                    ]]
                    for membership_id, role, order_index in members:
                        inbox_state = (
                            "accepted"
                            if role == "owner" and membership_id == identity.membership_id
                            else "acknowledged"
                            if role == "collaborator" and membership_id == identity.membership_id
                            else "pending"
                        )
                        connection.execute(
                            """
                            INSERT INTO task_collaborators (
                                task_id, organization_id, membership_id,
                                collaborator_role, inbox_state, order_index,
                                return_reason, handled_at, version, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, '', ?, 1, ?, ?)
                            """,
                            (
                                task_id,
                                identity.organization_id,
                                membership_id,
                                role,
                                inbox_state,
                                order_index,
                                now if inbox_state != "pending" else None,
                                now,
                                now,
                            ),
                        )
                connection.execute(
                    """
                    INSERT INTO task_activity_events (
                        task_activity_id, organization_id, task_id,
                        actor_membership_id, event_type, payload_json, happened_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        task_id,
                        identity.membership_id,
                        command_type,
                        canonical_json(normalized),
                        now,
                    ),
                )
                next_row = connection.execute(
                    """
                    SELECT * FROM task_records
                    WHERE organization_id = ? AND task_id = ?
                    """,
                    (identity.organization_id, task_id),
                ).fetchone()
                result = {"task": self._task_payload(connection, next_row)}
                self._record_task_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    task_id=task_id,
                    expected_version=expected_version,
                    before_version=current_version,
                    after_version=current_version + 1,
                    payload=normalized,
                    result=result,
                    policy_evidence=policy_evidence,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def save_ai_answer(
        self,
        identity: SessionIdentity,
        *,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "workbench.answer.saved"
        normalized = {
            "projectId": payload.get("projectId"),
            "question": str(payload.get("question") or "").strip(),
            "answerMarkdown": str(payload.get("answerMarkdown") or "").strip(),
            "sourceManifest": payload.get("sourceManifest") or {},
            "modelName": str(payload.get("modelName") or "").strip(),
        }
        if not normalized["question"] or not normalized["answerMarkdown"]:
            raise RepositoryError(422, "answer_content_required", "问答内容不完整")
        payload_hash = payload_fingerprint(normalized)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._task_receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self._ensure_project(
                    connection,
                    identity,
                    normalized["projectId"],
                )
                now = utc_now()
                answer_id = new_id()
                connection.execute(
                    """
                    INSERT INTO ai_answers (
                        ai_answer_id, organization_id, project_id, membership_id,
                        question, answer_markdown, source_manifest_json,
                        model_name, lifecycle_state, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
                    """,
                    (
                        answer_id,
                        identity.organization_id,
                        normalized["projectId"],
                        identity.membership_id,
                        normalized["question"],
                        normalized["answerMarkdown"],
                        canonical_json(normalized["sourceManifest"]),
                        normalized["modelName"],
                        now,
                        now,
                    ),
                )
                result = {
                    "answer": {
                        "answerId": answer_id,
                        "projectId": normalized["projectId"],
                        "question": normalized["question"],
                        "answerMarkdown": normalized["answerMarkdown"],
                        "sourceManifest": normalized["sourceManifest"],
                        "modelName": normalized["modelName"],
                        "version": 1,
                        "createdAt": now,
                        "updatedAt": now,
                    }
                }
                operation_id = new_id()
                payload_json = canonical_json(normalized)
                result_json = canonical_json(result)
                connection.execute(
                    """
                    INSERT INTO command_envelopes (
                        command_id, scope_id, organization_id, operation_id,
                        idempotency_key, aggregate_type, aggregate_id,
                        command_type, actor_principal_id, expected_version,
                        payload_json, payload_hash, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'ai_answer', ?, ?, ?, NULL, ?, ?,
                              'committed', ?, ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                        idempotency_key,
                        answer_id,
                        command_type,
                        identity.principal_id,
                        payload_json,
                        payload_hash,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO command_idempotency (
                        record_id, scope_id, actor_principal_id, command_type,
                        idempotency_key, payload_hash, result_hash, result_json,
                        expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                              '9999-12-31T23:59:59.999Z', ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        identity.principal_id,
                        command_type,
                        idempotency_key,
                        payload_hash,
                        sha256_text(result_json),
                        result_json,
                        now,
                    ),
                )
                self._insert_audit(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    actor_id=identity.principal_id,
                    action=command_type,
                    resource_type="ai_answer",
                    resource_id=answer_id,
                    before_version=None,
                    after_version=1,
                    summary={
                        "projectId": normalized["projectId"],
                        "modelName": normalized["modelName"],
                    },
                )
                self._insert_outbox(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    aggregate_type="ai_answer",
                    aggregate_id=answer_id,
                    aggregate_version=1,
                    event_type=command_type,
                    payload={"answerId": answer_id},
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def ai_config(self, identity: SessionIdentity, *, include_secret: bool) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT provider, base_url, model_name, encrypted_api_key,
                       key_fingerprint, config_version, status, updated_at
                FROM organization_ai_configs
                WHERE organization_id = ?
                """,
                (identity.organization_id,),
            ).fetchone()
        if row is None:
            return {
                "status": "not_ready",
                "provider": "",
                "baseUrl": "",
                "modelName": "",
                "keyFingerprint": "",
                "configVersion": 0,
                "updatedAt": None,
            }
        result = {
            "status": row["status"],
            "provider": row["provider"],
            "baseUrl": row["base_url"],
            "modelName": row["model_name"],
            "keyFingerprint": row["key_fingerprint"],
            "configVersion": row["config_version"],
            "updatedAt": row["updated_at"],
        }
        if include_secret:
            result["apiKey"] = self.cipher.decrypt(str(row["encrypted_api_key"]))
        return result

    def save_ai_config(
        self,
        identity: SessionIdentity,
        *,
        provider: str,
        base_url: str,
        model_name: str,
        api_key: str,
        expected_version: int | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not identity.is_admin:
            raise RepositoryError(403, "admin_required", "仅管理员可以修改组织 AI 配置")
        try:
            ai_host = (urlparse(base_url).hostname or "").casefold()
        except ValueError:
            ai_host = ""
        if not api_key and ai_host not in {"localhost", "127.0.0.1", "::1"}:
            raise RepositoryError(
                422,
                "organization_ai_key_required",
                "远端组织 AI 必须配置 API Key；本地 Ollama 可不填",
            )
        encrypted = self.cipher.encrypt(api_key)
        payload = {
            "provider": provider.strip(),
            "baseUrl": base_url.strip(),
            "modelName": model_name.strip(),
            "keyFingerprint": encrypted.fingerprint,
        }
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing_receipt = connection.execute(
                    """
                    SELECT payload_hash, result_json
                    FROM command_idempotency
                    WHERE scope_id = ? AND actor_principal_id = ?
                      AND command_type = 'organization_ai.update'
                      AND idempotency_key = ?
                    """,
                    (identity.scope_id, identity.principal_id, idempotency_key),
                ).fetchone()
                payload_hash = payload_fingerprint(payload)
                if existing_receipt is not None:
                    if existing_receipt["payload_hash"] != payload_hash:
                        raise RepositoryError(
                            409,
                            "idempotency_conflict",
                            "相同操作标识对应了不同配置",
                        )
                    connection.rollback()
                    return json.loads(str(existing_receipt["result_json"]))

                current = connection.execute(
                    """
                    SELECT config_id, config_version
                    FROM organization_ai_configs
                    WHERE organization_id = ?
                    """,
                    (identity.organization_id,),
                ).fetchone()
                current_version = int(current["config_version"]) if current else 0
                if expected_version is not None and expected_version != current_version:
                    raise RepositoryError(
                        409,
                        "version_conflict",
                        "组织 AI 配置已更新，请刷新后重试",
                    )
                next_version = current_version + 1
                now = utc_now()
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO organization_ai_configs (
                            config_id, organization_id, provider, base_url,
                            model_name, encrypted_api_key, key_fingerprint,
                            config_version, status, updated_by_membership_id,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?, ?)
                        """,
                        (
                            new_id(),
                            identity.organization_id,
                            payload["provider"],
                            payload["baseUrl"],
                            payload["modelName"],
                            encrypted.ciphertext,
                            encrypted.fingerprint,
                            next_version,
                            identity.membership_id,
                            now,
                            now,
                        ),
                    )
                else:
                    cursor = connection.execute(
                        """
                        UPDATE organization_ai_configs
                        SET provider = ?, base_url = ?, model_name = ?,
                            encrypted_api_key = ?, key_fingerprint = ?,
                            config_version = ?, status = 'ready',
                            updated_by_membership_id = ?, updated_at = ?
                        WHERE organization_id = ? AND config_version = ?
                        """,
                        (
                            payload["provider"],
                            payload["baseUrl"],
                            payload["modelName"],
                            encrypted.ciphertext,
                            encrypted.fingerprint,
                            next_version,
                            identity.membership_id,
                            now,
                            identity.organization_id,
                            current_version,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise RepositoryError(
                            409, "version_conflict", "组织 AI 配置已更新"
                        )
                operation_id = new_id()
                command_id = new_id()
                connection.execute(
                    """
                    INSERT INTO command_envelopes (
                        command_id, scope_id, organization_id, operation_id,
                        idempotency_key, aggregate_type, aggregate_id,
                        command_type, actor_principal_id, expected_version,
                        payload_json, payload_hash, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'organization_ai_config', ?,
                              'organization_ai.update', ?, ?, ?, ?,
                              'committed', ?, ?)
                    """,
                    (
                        command_id,
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                        idempotency_key,
                        identity.organization_id,
                        identity.principal_id,
                        current_version,
                        canonical_json(payload),
                        payload_hash,
                        now,
                        now,
                    ),
                )
                result = {
                    "status": "ready",
                    **payload,
                    "configVersion": next_version,
                    "updatedAt": now,
                }
                result_json = canonical_json(result)
                connection.execute(
                    """
                    INSERT INTO command_idempotency (
                        record_id, scope_id, actor_principal_id, command_type,
                        idempotency_key, payload_hash, result_hash,
                        result_json, expires_at, created_at
                    ) VALUES (?, ?, ?, 'organization_ai.update', ?, ?, ?, ?,
                              '9999-12-31T23:59:59.999Z', ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        identity.principal_id,
                        idempotency_key,
                        payload_hash,
                        sha256_text(result_json),
                        result_json,
                        now,
                    ),
                )
                self._insert_audit(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    actor_id=identity.principal_id,
                    action="organization_ai.update",
                    resource_type="organization_ai_config",
                    resource_id=identity.organization_id,
                    before_version=current_version or None,
                    after_version=next_version,
                    summary=payload,
                )
                self._insert_outbox(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    aggregate_type="organization_ai_config",
                    aggregate_id=identity.organization_id,
                    aggregate_version=next_version,
                    event_type="organization_ai.updated",
                    payload={
                        "organizationId": identity.organization_id,
                        "configVersion": next_version,
                    },
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def create_department(
        self,
        identity: SessionIdentity,
        *,
        name: str,
        expected_organization_version: int | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._create_named_structure(
            identity,
            kind="department",
            name=name,
            expected_organization_version=expected_organization_version,
            idempotency_key=idempotency_key,
        )

    def create_management_title(
        self,
        identity: SessionIdentity,
        *,
        name: str,
        expected_organization_version: int | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._create_named_structure(
            identity,
            kind="management_title",
            name=name,
            expected_organization_version=expected_organization_version,
            idempotency_key=idempotency_key,
        )

    def _create_named_structure(
        self,
        identity: SessionIdentity,
        *,
        kind: str,
        name: str,
        expected_organization_version: int | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not identity.is_admin:
            raise RepositoryError(403, "admin_required", "仅管理员可以调整组织结构")
        normalized_name = name.strip()
        if not normalized_name:
            raise RepositoryError(422, "name_required", "名称不能为空")
        table = (
            "organization_departments"
            if kind == "department"
            else "management_titles"
        )
        id_column = "department_id" if kind == "department" else "title_id"
        command_type = f"organization.{kind}.create"
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = connection.execute(
                    """
                    SELECT payload_hash, result_json
                    FROM command_idempotency
                    WHERE scope_id = ? AND actor_principal_id = ?
                      AND command_type = ? AND idempotency_key = ?
                    """,
                    (
                        identity.scope_id,
                        identity.principal_id,
                        command_type,
                        idempotency_key,
                    ),
                ).fetchone()
                payload = {"name": normalized_name}
                payload_hash = payload_fingerprint(payload)
                if receipt:
                    if receipt["payload_hash"] != payload_hash:
                        raise RepositoryError(
                            409, "idempotency_conflict", "操作标识冲突"
                        )
                    connection.rollback()
                    return json.loads(str(receipt["result_json"]))
                organization = connection.execute(
                    """
                    SELECT version FROM organization_records
                    WHERE organization_id = ? AND cloud_instance_id = ?
                      AND lifecycle_state = 'active'
                    """,
                    (identity.organization_id, identity.cloud_instance_id),
                ).fetchone()
                if organization is None:
                    raise RepositoryError(404, "organization_missing", "组织不存在")
                current_organization_version = int(organization["version"])
                if (
                    expected_organization_version is not None
                    and expected_organization_version != current_organization_version
                ):
                    raise RepositoryError(
                        409,
                        "organization_version_conflict",
                        "组织结构已更新，请刷新后重试",
                    )
                object_id = new_id()
                now = utc_now()
                connection.execute(
                    f"""
                    INSERT INTO {table} (
                        {id_column}, organization_id, name, lifecycle_state,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, 'active', 1, ?, ?)
                    """,
                    (
                        object_id,
                        identity.organization_id,
                        normalized_name,
                        now,
                        now,
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE organization_records
                    SET version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND version = ?
                    """,
                    (
                        now,
                        identity.organization_id,
                        current_organization_version,
                    ),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "organization_version_conflict",
                        "组织结构已更新，请刷新后重试",
                    )
                operation_id = new_id()
                connection.execute(
                    """
                    INSERT INTO command_envelopes (
                        command_id, scope_id, organization_id, operation_id,
                        idempotency_key, aggregate_type, aggregate_id,
                        command_type, actor_principal_id, expected_version,
                        payload_json, payload_hash, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?,
                              'committed', ?, ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                        idempotency_key,
                        kind,
                        object_id,
                        command_type,
                        identity.principal_id,
                        canonical_json(payload),
                        payload_hash,
                        now,
                        now,
                    ),
                )
                result = {
                    "id": object_id,
                    "name": normalized_name,
                    "version": 1,
                    "organizationVersion": current_organization_version + 1,
                }
                result_json = canonical_json(result)
                connection.execute(
                    """
                    INSERT INTO command_idempotency (
                        record_id, scope_id, actor_principal_id, command_type,
                        idempotency_key, payload_hash, result_hash,
                        result_json, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                              '9999-12-31T23:59:59.999Z', ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        identity.principal_id,
                        command_type,
                        idempotency_key,
                        payload_hash,
                        sha256_text(result_json),
                        result_json,
                        now,
                    ),
                )
                self._insert_audit(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    actor_id=identity.principal_id,
                    action=command_type,
                    resource_type=kind,
                    resource_id=object_id,
                    before_version=None,
                    after_version=1,
                    summary=payload,
                )
                self._insert_outbox(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    aggregate_type=kind,
                    aggregate_id=object_id,
                    aggregate_version=1,
                    event_type=f"{kind}.created",
                    payload={"id": object_id},
                )
                connection.commit()
                return result
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise RepositoryError(409, "name_exists", "名称已经存在") from error
            except Exception:
                connection.rollback()
                raise

    def create_invite(
        self,
        identity: SessionIdentity,
        *,
        invite_kind: str,
        target_id: str | None,
        expires_at: str | None,
    ) -> dict[str, Any]:
        if not identity.is_admin:
            raise RepositoryError(403, "admin_required", "仅管理员可以生成邀请码")
        if invite_kind not in {"organization", "department", "management_title"}:
            raise RepositoryError(422, "invalid_invite_kind", "邀请码类型无效")
        if invite_kind != "organization" and not target_id:
            raise RepositoryError(422, "target_required", "请选择邀请码对应对象")
        raw_code = new_secret_token()[:24]
        now = utc_now()
        if expires_at and _parse_time(expires_at) <= datetime.now(timezone.utc):
            raise RepositoryError(422, "invite_expiry_invalid", "邀请码有效期必须晚于当前时间")
        invite_id = new_id()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if invite_kind == "department":
                    row = connection.execute(
                        """
                        SELECT 1 FROM organization_departments
                        WHERE department_id = ? AND organization_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (target_id, identity.organization_id),
                    ).fetchone()
                elif invite_kind == "management_title":
                    row = connection.execute(
                        """
                        SELECT 1 FROM management_titles
                        WHERE title_id = ? AND organization_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (target_id, identity.organization_id),
                    ).fetchone()
                else:
                    row = (1,)
                if row is None:
                    raise RepositoryError(404, "invite_target_missing", "邀请码对象不存在")
                connection.execute(
                    """
                    INSERT INTO organization_invites (
                        invite_id, organization_id, invite_kind, target_id,
                        code_hash, status, expires_at, version,
                        created_by_membership_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, 1, ?, ?, ?)
                    """,
                    (
                        invite_id,
                        identity.organization_id,
                        invite_kind,
                        target_id,
                        hash_token(raw_code),
                        expires_at,
                        identity.membership_id,
                        now,
                        now,
                    ),
                )
                operation_id = new_id()
                self._insert_audit(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    actor_id=identity.principal_id,
                    action="organization.invite.create",
                    resource_type="organization_invite",
                    resource_id=invite_id,
                    before_version=None,
                    after_version=1,
                    summary={"inviteKind": invite_kind, "targetId": target_id},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "inviteId": invite_id,
            "inviteCode": raw_code,
            "inviteKind": invite_kind,
            "targetId": target_id,
        }

    def join(
        self,
        *,
        invite_code: str,
        display_name: str,
        email: str | None,
        phone: str | None,
        password: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                invite = connection.execute(
                    """
                    SELECT invite_id, organization_id, invite_kind, target_id,
                           expires_at
                    FROM organization_invites
                    WHERE code_hash = ? AND status = 'active'
                    """,
                    (hash_token(invite_code.strip()),),
                ).fetchone()
                if invite is None:
                    raise RepositoryError(404, "invite_invalid", "邀请码无效")
                if invite["expires_at"] and _parse_time(str(invite["expires_at"])) <= datetime.now(timezone.utc):
                    raise RepositoryError(410, "invite_expired", "邀请码已过期")
                contacts: list[tuple[str, str]] = []
                if email:
                    contacts.append(("email", normalize_email(email)))
                if phone:
                    contacts.append(("phone", normalize_phone(phone)))
                if not contacts:
                    raise RepositoryError(422, "contact_required", "邮箱或手机号至少填写一项")
                existing_ids = {
                    str(row[0])
                    for contact_type, value in contacts
                    for row in connection.execute(
                        """
                        SELECT principal_id FROM identity_contacts
                        WHERE contact_type = ? AND normalized_value = ?
                        """,
                        (contact_type, value),
                    ).fetchall()
                }
                if len(existing_ids) > 1:
                    raise RepositoryError(
                        409, "identity_ambiguous", "邮箱与手机号属于不同账号"
                    )
                if existing_ids:
                    principal_id = next(iter(existing_ids))
                    credential = connection.execute(
                        """
                        SELECT secret_hash, hash_scheme
                        FROM identity_credentials
                        WHERE principal_id = ? AND credential_type = 'password'
                          AND status = 'active'
                        """,
                        (principal_id,),
                    ).fetchone()
                    if credential is None or not self._verify_and_upgrade_password(
                        connection,
                        principal_id=principal_id,
                        password=password,
                        secret_hash=str(credential["secret_hash"]),
                        hash_scheme=str(credential["hash_scheme"]),
                    ):
                        raise RepositoryError(401, "invalid_credentials", "账号密码不正确")
                else:
                    principal_id, _ = self._insert_personal_identity(
                        connection,
                        display_name=display_name,
                        email=email,
                        phone=phone,
                        password=password,
                    )
                organization_id = str(invite["organization_id"])
                if connection.execute(
                    """
                    SELECT 1 FROM organization_memberships
                    WHERE organization_id = ? AND principal_id = ?
                    """,
                    (organization_id, principal_id),
                ).fetchone():
                    raise RepositoryError(409, "already_joined", "账号已经加入该组织")
                scope = connection.execute(
                    """
                    SELECT scope_id FROM authorization_scopes
                    WHERE scope_kind = 'organization' AND organization_id = ?
                    """,
                    (organization_id,),
                ).fetchone()
                if scope is None:
                    raise RepositoryError(500, "scope_missing", "组织权限根缺失")
                membership_id = new_id()
                visibility = (
                    "organization"
                    if invite["invite_kind"] == "management_title"
                    else "self"
                )
                connection.execute(
                    """
                    INSERT INTO organization_memberships (
                        membership_id, scope_id, organization_id, principal_id,
                        system_role, visibility_scope, status, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'member', ?, 'active', 1, ?, ?)
                    """,
                    (
                        membership_id,
                        scope["scope_id"],
                        organization_id,
                        principal_id,
                        visibility,
                        now,
                        now,
                    ),
                )
                if invite["invite_kind"] == "department":
                    connection.execute(
                        """
                        INSERT INTO department_memberships (
                            department_membership_id, organization_id,
                            department_id, membership_id, is_department_lead,
                            status, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 0, 'active', 1, ?, ?)
                        """,
                        (
                            new_id(),
                            organization_id,
                            invite["target_id"],
                            membership_id,
                            now,
                            now,
                        ),
                    )
                elif invite["invite_kind"] == "management_title":
                    connection.execute(
                        """
                        INSERT INTO management_title_memberships (
                            assignment_id, organization_id, title_id,
                            membership_id, status, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)
                        """,
                        (
                            new_id(),
                            organization_id,
                            invite["target_id"],
                            membership_id,
                            now,
                            now,
                        ),
                    )
                operation_id = new_id()
                self._insert_audit(
                    connection,
                    scope_id=str(scope["scope_id"]),
                    organization_id=organization_id,
                    operation_id=operation_id,
                    actor_id=principal_id,
                    action="organization.join",
                    resource_type="organization_membership",
                    resource_id=membership_id,
                    before_version=None,
                    after_version=1,
                    summary={
                        "inviteId": invite["invite_id"],
                        "inviteKind": invite["invite_kind"],
                    },
                )
                self._insert_outbox(
                    connection,
                    scope_id=str(scope["scope_id"]),
                    organization_id=organization_id,
                    operation_id=operation_id,
                    aggregate_type="organization_membership",
                    aggregate_id=membership_id,
                    aggregate_version=1,
                    event_type="organization.member_joined",
                    payload={"membershipId": membership_id},
                )
                session = self._insert_session(
                    connection,
                    principal_id=principal_id,
                    membership_id=membership_id,
                    organization_id=organization_id,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._session_payload(session)
