from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from strict_common.contracts import CONNECTED_CAPABILITIES
from strict_common.agent_memory import AgentRunReceipt, builtin_agent_id
from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.schema import database_identity, initialize_database, runtime_connection
from strict_common.security import (
    SecretCipher,
    hash_token,
    new_secret_token,
    normalize_identifier,
    verify_password,
)

from .repositories.gc01_authorization import (
    AuthorizationProjectionError,
    read_authorization_projection,
    renew_authorization_projection_for_session,
)


ACCESS_TTL = timedelta(hours=2)
REFRESH_TTL = timedelta(days=30)


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
    """88-table foundation repository.

    Only identity, organization snapshot, session and organization-AI reads are
    active during the foundation cut-over. Every golden-chain method remains an
    explicit 501 until it is reconnected against the approved 88 tables.
    """

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
        self.session_secret_dir = (
            self.database_path.parent / ".runtime-secrets" / "server-sessions"
        ).resolve()
        self.cloud_instance_id = self._cloud_instance_id(cloud_instance_id)

    def _connection(self):
        return runtime_connection(self.database_path, "cloud")

    def _require_project_access(
        self,
        connection,
        identity: SessionIdentity,
        *,
        project_id: str,
        capability: str = "read",
    ):
        """Resolve the single GC-01 authorization path for a client/project.

        Project metadata and all project knowledge consumers must use the same
        ``clients -> secured_resources -> object_grants`` decision.  An active
        grant may explicitly carry ``contributeKnowledge``; older strict-v2
        grants with ``write`` remain compatible during the rolling upgrade.
        """

        project = connection.execute(
            "SELECT * FROM clients WHERE id=? AND scope_id=? "
            "AND lifecycle_state!='deleted'",
            (project_id, identity.scope_id),
        ).fetchone()
        if project is None:
            raise RepositoryError(404, "project_missing", "当前成员无法访问该项目")
        if capability != "read" and str(project["lifecycle_state"] or "") != "active":
            raise RepositoryError(409, "project_inactive", "当前项目不是可写状态")
        policy = connection.execute(
            "SELECT id FROM policy_versions WHERE scope_id=? "
            "AND secured_resource_id=? AND lifecycle_state='active' "
            "ORDER BY version DESC, created_at DESC, id DESC LIMIT 1",
            (identity.scope_id, project_id),
        ).fetchone()
        if policy is None:
            raise RepositoryError(
                409,
                "project_policy_projection_missing",
                "项目权限版本尚未形成，请稍后重试",
            )
        if identity.is_admin or str(project["owner_membership_id"] or "") == identity.membership_id:
            return project
        grants = connection.execute(
            "SELECT capability_set FROM object_grants "
            "WHERE scope_id=? AND secured_resource_id=? "
            "AND subject_membership_id=? AND status='active' "
            "AND lifecycle_state='active' AND policy_version_id=?",
            (
                identity.scope_id,
                project_id,
                identity.membership_id,
                str(policy["id"]),
            ),
        ).fetchall()
        for grant in grants:
            try:
                capabilities = json.loads(str(grant["capability_set"] or "{}"))
            except json.JSONDecodeError:
                capabilities = {}
            allowed = {
                "read": bool(
                    capabilities.get("read")
                    or capabilities.get("write")
                    or capabilities.get("contributeKnowledge")
                ),
                "knowledge_read": bool(
                    capabilities.get("read")
                    or capabilities.get("write")
                    or capabilities.get("contributeKnowledge")
                ),
                "project_write": bool(capabilities.get("write")),
                "knowledge_write": bool(
                    capabilities.get("contributeKnowledge")
                    or capabilities.get("write")
                ),
                "manage_sharing": bool(capabilities.get("manageSharing")),
            }.get(capability, False)
            if allowed:
                return project
        code = "project_forbidden" if capability != "read" else "project_missing"
        status = 403 if capability != "read" else 404
        message = "当前成员无权执行该项目操作" if capability != "read" else "当前成员无法访问该项目"
        raise RepositoryError(status, code, message)

    def _cloud_instance_id(self, configured: str | None) -> str:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT state_id FROM state_registry
                WHERE record_kind='cloud_instance' AND lifecycle_state='active'
                ORDER BY created_at LIMIT 1
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("strict cloud has no active cloud_instance state")
        actual = str(row["state_id"])
        if configured and configured != actual:
            raise RuntimeError("configured cloud instance id does not match database")
        return actual

    def handshake(self) -> dict[str, Any]:
        current = database_identity(self.database_path, "cloud")
        with self._connection() as connection:
            organization_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM organizations "
                    "WHERE record_kind='organization' AND lifecycle_state='active'"
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
            "organizationCount": organization_count,
            "capabilities": sorted(CONNECTED_CAPABILITIES),
        }

    def _credential(self, reference: str) -> tuple[str, str]:
        path = Path(reference)
        if not path.is_file():
            raise RepositoryError(503, "credential_unavailable", "登录凭据存储暂不可用")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return str(payload["hashScheme"]), str(payload["secretHash"])

    def _session_payload(self, session: dict[str, Any]) -> dict[str, Any]:
        identity = self.session_from_access(str(session["accessToken"]))
        return {
            **session,
            "cloudInstanceId": self.cloud_instance_id,
            "organizationId": identity.organization_id,
            "principalId": identity.principal_id,
            "membershipId": identity.membership_id,
            "sessionSnapshot": self.organization_snapshot(identity),
        }

    @staticmethod
    def _operation_id(scope_id: str, command_type: str, idempotency_key: str) -> str:
        return "op_" + sha256_text(
            f"gc01\x1f{scope_id}\x1f{command_type}\x1f{idempotency_key}"
        )[:30]

    @staticmethod
    def _record_id(prefix: str, operation_id: str, kind: str) -> str:
        return f"{prefix}_" + sha256_text(f"{operation_id}\x1f{kind}")[:30]

    def _session_secret_path(self, session_id: str, version: int) -> Path:
        return self.session_secret_dir / f"{session_id}.v{version}.json"

    def _store_session_secret(
        self,
        *,
        session_id: str,
        version: int,
        payload: dict[str, Any],
    ) -> tuple[str, str]:
        self.session_secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.session_secret_dir, 0o700)
        plaintext = canonical_json(payload)
        encrypted = self.cipher.encrypt(plaintext)
        target = self._session_secret_path(session_id, version)
        temporary = target.with_suffix(f".tmp-{new_id()}")
        try:
            temporary.write_text(
                canonical_json(
                    {
                        "encryptedSessionBundle": encrypted.ciphertext,
                        "fingerprint": encrypted.fingerprint,
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return str(target), encrypted.fingerprint

    def _validated_session_secret_path(self, reference: str) -> Path:
        path = Path(reference).resolve()
        if path.parent != self.session_secret_dir or not path.is_file():
            raise RepositoryError(
                503,
                "session_secret_unavailable",
                "会话凭据存储暂不可用",
            )
        return path

    def _load_session_secret(self, reference: str) -> dict[str, Any]:
        path = self._validated_session_secret_path(reference)
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            plaintext = self.cipher.decrypt(str(stored["encryptedSessionBundle"]))
            payload = json.loads(plaintext)
        except (KeyError, TypeError, ValueError) as exc:
            raise RepositoryError(
                503,
                "session_secret_unavailable",
                "会话凭据存储暂不可用",
            ) from exc
        if not isinstance(payload, dict):
            raise RepositoryError(503, "session_secret_unavailable", "会话凭据存储暂不可用")
        return payload

    def _delete_session_secret(self, reference: str | None) -> None:
        if not reference:
            return
        path = Path(reference).resolve()
        if path.parent == self.session_secret_dir:
            path.unlink(missing_ok=True)

    def _existing_command(
        self,
        connection,
        *,
        scope_id: str,
        idempotency_key: str,
        command_type: str,
        payload_hash: str,
    ):
        row = connection.execute(
            "SELECT * FROM commands WHERE scope_id=? AND idempotency_key=?",
            (scope_id, idempotency_key),
        ).fetchone()
        if row is not None and (
            row["command_type"] != command_type
            or row["payload_hash"] != payload_hash
        ):
            raise RepositoryError(
                409,
                "idempotency_conflict",
                "该幂等键已用于不同的会话操作",
            )
        return row

    def _record_session_operation(
        self,
        connection,
        *,
        scope_id: str,
        idempotency_key: str,
        command_type: str,
        event_type: str,
        action: str,
        session_id: str,
        session_version: int,
        principal_id: str,
        membership_id: str,
        payload_hash: str,
        now: str,
    ) -> str:
        operation_id = self._operation_id(scope_id, command_type, idempotency_key)
        command_id = self._record_id("cmd", operation_id, command_type)
        event_id = self._record_id("evt", operation_id, event_type)
        audit_id = self._record_id("audit", operation_id, action)
        event_hash = sha256_text(
            canonical_json(
                {
                    "operationId": operation_id,
                    "eventType": event_type,
                    "sessionId": session_id,
                    "sessionVersion": session_version,
                }
            )
        )
        connection.execute(
            """
            INSERT INTO idempotency_records (
                id, scope_id, idempotency_key, payload_hash, result_hash,
                expires_at, result_object_manifest_id, status, created_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'settled', ?, 'cloud', ?)
            """,
            (
                self._record_id("idem", operation_id, command_type),
                scope_id,
                idempotency_key,
                payload_hash,
                event_hash,
                _expires_at(REFRESH_TTL),
                now,
                self.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO commands (
                id, scope_id, operation_id, idempotency_key, aggregate_type,
                aggregate_id, command_type, actor_principal_id,
                expected_aggregate_version, device_command_sequence, status,
                actor_membership_id, payload_object_manifest_id, payload_hash,
                submitted_at, settled_at, authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, 'server_session', ?, ?, ?, ?, NULL,
                      'settled', ?, NULL, ?, ?, ?, 'cloud', ?)
            """,
            (
                command_id,
                scope_id,
                operation_id,
                idempotency_key,
                session_id,
                command_type,
                principal_id,
                max(0, session_version - 1),
                membership_id,
                payload_hash,
                now,
                now,
                self.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO outbox_events (
                id, scope_id, operation_id, aggregate_version, event_type,
                status, aggregate_type, aggregate_id,
                event_object_manifest_id, event_hash, available_at,
                published_at, authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, 'pending', 'server_session', ?, NULL, ?,
                      ?, NULL, 'cloud', ?)
            """,
            (
                event_id,
                scope_id,
                operation_id,
                session_version,
                event_type,
                session_id,
                event_hash,
                now,
                self.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, scope_id, operation_id, actor_id, action, event_hash,
                actor_membership_id, target_resource_id,
                details_object_manifest_id, occurred_at, origin_instance_id,
                created_at, integrity_hash, authority_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, 'cloud')
            """,
            (
                audit_id,
                scope_id,
                operation_id,
                principal_id,
                action,
                event_hash,
                membership_id,
                now,
                self.cloud_instance_id,
                now,
                sha256_text(f"{audit_id}|{event_hash}|{now}|{self.cloud_instance_id}"),
            ),
        )
        return operation_id

    def _insert_session(
        self,
        connection,
        *,
        principal_id: str,
        membership_id: str,
        scope_id: str,
    ) -> dict[str, Any]:
        now = utc_now()
        session_id = new_id()
        access_token = new_secret_token()
        refresh_token = new_secret_token()
        expires_at = _expires_at(ACCESS_TTL)
        refresh_expires_at = _expires_at(REFRESH_TTL)
        response = {
            "sessionId": session_id,
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at,
            "refreshExpiresAt": refresh_expires_at,
        }
        secret_reference, secret_fingerprint = self._store_session_secret(
            session_id=session_id,
            version=1,
            payload=response,
        )
        try:
            connection.execute(
                """
                INSERT INTO sandboxes (
                id, scope_id, principal_id, membership_id, secret_reference,
                secret_fingerprint, access_secret_hash,
                refresh_secret_hash, access_expires_at, refresh_expires_at,
                last_seen_at, record_kind, cloud_instance_id,
                database_generation_id, sandbox_kind, runtime_status,
                contract_version, manifest_hash, lease_expires_at,
                last_verified_at, version, lifecycle_state, created_at,
                updated_at, deleted_at, authority_role, origin_instance_id,
                source_version, projection_state, projected_at, stale_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'server_session', ?, ?,
                'organization', 'active', ?, ?, ?, ?, 1, 'active', ?, ?, NULL,
                'cloud', ?, 1, 'authoritative', ?, NULL
            )
                """,
                (
                    session_id,
                    scope_id,
                    principal_id,
                    membership_id,
                    secret_reference,
                    secret_fingerprint,
                    hash_token(access_token),
                    hash_token(refresh_token),
                    expires_at,
                    refresh_expires_at,
                    now,
                    self.cloud_instance_id,
                    self.identity.database_generation_id,
                    self.identity.contract_version,
                    self.identity.manifest_hash,
                    refresh_expires_at,
                    now,
                    now,
                    now,
                    self.cloud_instance_id,
                    now,
                ),
            )
        except Exception:
            self._delete_session_secret(secret_reference)
            raise
        return {**response, "_secretReference": secret_reference}

    def login(
        self,
        *,
        identifier: str,
        password: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        contact_type, normalized = normalize_identifier(identifier)
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT person.id AS principal_id,
                       credential.secret_reference,
                       membership.id AS membership_id,
                       membership.scope_id,
                       scope.organization_id
                FROM principals AS contact
                JOIN principals AS person ON person.id=contact.parent_principal_id
                JOIN principals AS credential
                  ON credential.parent_principal_id=person.id
                 AND credential.principal_kind='credential'
                 AND credential.credential_type='password'
                 AND credential.credential_state='active'
                JOIN organization_memberships AS membership
                  ON membership.principal_id=person.id
                 AND membership.record_kind='membership'
                 AND membership.status='active'
                JOIN authorization_scopes AS scope ON scope.id=membership.scope_id
                JOIN organizations AS organization
                  ON organization.id=scope.organization_id
                 AND organization.record_kind='organization'
                 AND organization.lifecycle_state='active'
                WHERE contact.principal_kind='contact'
                  AND contact.contact_type=?
                  AND contact.normalized_contact=?
                  AND contact.verification_state='verified'
                  AND person.status='active'
                LIMIT 1
                """,
                (contact_type, normalized),
            ).fetchone()
            if row is None or not row["secret_reference"]:
                raise RepositoryError(401, "invalid_credentials", "账号或密码不正确")
            scheme, secret_hash = self._credential(str(row["secret_reference"]))
            if not verify_password(password, secret_hash, scheme=scheme):
                raise RepositoryError(401, "invalid_credentials", "账号或密码不正确")
            principal_id = str(row["principal_id"])
            membership_id = str(row["membership_id"])
            scope_id = str(row["scope_id"])
            payload_hash = sha256_text(
                canonical_json(
                    {
                        "contactType": contact_type,
                        "normalizedIdentifierHash": sha256_text(normalized),
                    }
                )
            )
            secret_to_remove: str | None = None
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = self._existing_command(
                    connection,
                    scope_id=scope_id,
                    idempotency_key=idempotency_key,
                    command_type="gc01.session.login",
                    payload_hash=payload_hash,
                )
                if existing is not None:
                    replay = connection.execute(
                        """
                        SELECT secret_reference FROM sandboxes
                        WHERE id=? AND record_kind='server_session'
                          AND lifecycle_state='active' AND runtime_status='active'
                        """,
                        (existing["aggregate_id"],),
                    ).fetchone()
                    if replay is None or not replay["secret_reference"]:
                        raise RepositoryError(
                            409,
                            "session_replay_unavailable",
                            "该登录操作对应的会话已失效，请使用新的幂等键重新登录",
                        )
                    try:
                        renew_authorization_projection_for_session(
                            connection,
                            scope_id=scope_id,
                            membership_id=membership_id,
                            now=utc_now(),
                        )
                    except AuthorizationProjectionError as exc:
                        raise RepositoryError(
                            exc.status_code,
                            exc.code,
                            exc.message,
                        ) from exc
                    connection.commit()
                    session = self._load_session_secret(str(replay["secret_reference"]))
                else:
                    session = self._insert_session(
                        connection,
                        principal_id=principal_id,
                        membership_id=membership_id,
                        scope_id=scope_id,
                    )
                    secret_to_remove = str(session["_secretReference"])
                    try:
                        renew_authorization_projection_for_session(
                            connection,
                            scope_id=scope_id,
                            membership_id=membership_id,
                            now=utc_now(),
                        )
                    except AuthorizationProjectionError as exc:
                        raise RepositoryError(
                            exc.status_code,
                            exc.code,
                            exc.message,
                        ) from exc
                    self._record_session_operation(
                        connection,
                        scope_id=scope_id,
                        idempotency_key=idempotency_key,
                        command_type="gc01.session.login",
                        event_type="gc01.session.started",
                        action="session.login",
                        session_id=str(session["sessionId"]),
                        session_version=1,
                        principal_id=principal_id,
                        membership_id=membership_id,
                        payload_hash=payload_hash,
                        now=utc_now(),
                    )
                    connection.commit()
                    secret_to_remove = None
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                self._delete_session_secret(secret_to_remove)
                raise
        session.pop("_secretReference", None)
        return self._session_payload(session)

    def session_from_access(self, access_token: str) -> SessionIdentity:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT session.id AS session_id, session.principal_id,
                       session.membership_id, session.cloud_instance_id,
                       session.access_expires_at, membership.scope_id,
                       membership.role_key, membership.visibility_scope,
                       membership.status AS membership_status,
                       principal.display_name, principal.status AS principal_status,
                       scope.organization_id
                FROM sandboxes AS session
                JOIN organization_memberships AS membership
                  ON membership.id=session.membership_id
                JOIN principals AS principal ON principal.id=session.principal_id
                JOIN authorization_scopes AS scope ON scope.id=membership.scope_id
                WHERE session.record_kind='server_session'
                  AND session.access_secret_hash=?
                  AND session.lifecycle_state='active'
                  AND session.runtime_status='active'
                LIMIT 1
                """,
                (hash_token(access_token),),
            ).fetchone()
        if row is None:
            raise RepositoryError(401, "invalid_session", "登录状态已失效")
        if not row["access_expires_at"] or _parse_time(str(row["access_expires_at"])) <= datetime.now(timezone.utc):
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
            cloud_instance_id=self.cloud_instance_id,
            scope_id=str(row["scope_id"]),
            system_role=str(row["role_key"] or "member"),
            visibility_scope=str(row["visibility_scope"] or "organization"),
            display_name=str(row["display_name"] or ""),
        )

    def refresh(self, refresh_token: str, *, idempotency_key: str) -> dict[str, Any]:
        now = utc_now()
        request_hash = sha256_text(f"gc01.session.refresh|{hash_token(refresh_token)}")
        new_secret_reference: str | None = None
        old_secret_reference: str | None = None
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                replay_rows = connection.execute(
                    """
                    SELECT command.*, session.secret_reference
                    FROM commands AS command
                    JOIN sandboxes AS session ON session.id=command.aggregate_id
                    WHERE command.idempotency_key=?
                      AND command.command_type='gc01.session.refresh'
                      AND command.payload_hash=?
                    """,
                    (idempotency_key, request_hash),
                ).fetchall()
                if len(replay_rows) > 1:
                    raise RepositoryError(
                        409,
                        "idempotency_conflict",
                        "刷新幂等键在多个授权范围内冲突",
                    )
                if replay_rows:
                    replay = replay_rows[0]
                    if not replay["secret_reference"]:
                        raise RepositoryError(
                            409,
                            "session_replay_unavailable",
                            "刷新操作的会话凭据已失效",
                        )
                    connection.commit()
                    response = self._load_session_secret(str(replay["secret_reference"]))
                    return self._session_payload(response)
                row = connection.execute(
                    """
                    SELECT id, principal_id, membership_id, scope_id,
                           refresh_expires_at, version, secret_reference
                    FROM sandboxes
                    WHERE record_kind='server_session' AND refresh_secret_hash=?
                      AND lifecycle_state='active' AND runtime_status='active'
                    LIMIT 1
                    """,
                    (hash_token(refresh_token),),
                ).fetchone()
                if (
                    row is None
                    or not row["refresh_expires_at"]
                    or _parse_time(str(row["refresh_expires_at"]))
                    <= datetime.now(timezone.utc)
                ):
                    raise RepositoryError(401, "refresh_expired", "需要重新登录")
                # A key already used with another rotated refresh token is an
                # explicit idempotency conflict.  Detect it before rotating
                # secrets so clients receive a stable 409 instead of a late
                # SQLite UNIQUE violation/500.
                self._existing_command(
                    connection,
                    scope_id=str(row["scope_id"]),
                    idempotency_key=idempotency_key,
                    command_type="gc01.session.refresh",
                    payload_hash=request_hash,
                )
                next_version = int(row["version"] or 0) + 1
                response = {
                    "sessionId": str(row["id"]),
                    "accessToken": new_secret_token(),
                    "refreshToken": new_secret_token(),
                    "expiresAt": _expires_at(ACCESS_TTL),
                    "refreshExpiresAt": _expires_at(REFRESH_TTL),
                }
                new_secret_reference, new_fingerprint = self._store_session_secret(
                    session_id=str(row["id"]),
                    version=next_version,
                    payload=response,
                )
                old_secret_reference = (
                    str(row["secret_reference"]) if row["secret_reference"] else None
                )
                cursor = connection.execute(
                    """
                    UPDATE sandboxes SET secret_reference=?, secret_fingerprint=?,
                        access_secret_hash=?, refresh_secret_hash=?,
                        access_expires_at=?, refresh_expires_at=?, last_seen_at=?,
                        last_verified_at=?, projected_at=?, version=version+1
                    WHERE id=? AND version=?
                    """,
                    (
                        new_secret_reference,
                        new_fingerprint,
                        hash_token(str(response["accessToken"])),
                        hash_token(str(response["refreshToken"])),
                        response["expiresAt"],
                        response["refreshExpiresAt"],
                        now,
                        now,
                        now,
                        row["id"],
                        row["version"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(409, "session_conflict", "会话已更新，请重试")
                renew_authorization_projection_for_session(
                    connection,
                    scope_id=str(row["scope_id"]),
                    membership_id=str(row["membership_id"]),
                    now=now,
                )
                self._record_session_operation(
                    connection,
                    scope_id=str(row["scope_id"]),
                    idempotency_key=idempotency_key,
                    command_type="gc01.session.refresh",
                    event_type="gc01.session.refreshed",
                    action="session.refresh",
                    session_id=str(row["id"]),
                    session_version=next_version,
                    principal_id=str(row["principal_id"]),
                    membership_id=str(row["membership_id"]),
                    payload_hash=request_hash,
                    now=now,
                )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                self._delete_session_secret(new_secret_reference)
                raise
        self._delete_session_secret(old_secret_reference)
        return self._session_payload(response)

    def logout(
        self,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
    ) -> None:
        now = utc_now()
        secret_reference: str | None = None
        with self._connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT version, secret_reference FROM sandboxes
                    WHERE id=? AND record_kind='server_session'
                      AND lifecycle_state='active' AND runtime_status='active'
                    """,
                    (identity.session_id,),
                ).fetchone()
                if row is None:
                    raise RepositoryError(401, "invalid_session", "登录状态已失效")
                payload_hash = sha256_text(
                    canonical_json(
                        {
                            "sessionId": identity.session_id,
                            "expectedVersion": int(row["version"] or 1),
                        }
                    )
                )
                existing = self._existing_command(
                    connection,
                    scope_id=identity.scope_id,
                    idempotency_key=idempotency_key,
                    command_type="gc01.session.logout",
                    payload_hash=payload_hash,
                )
                if existing is not None:
                    connection.commit()
                    return
                next_version = int(row["version"] or 0) + 1
                cursor = connection.execute(
                    """
                    UPDATE sandboxes SET runtime_status='revoked',
                        lifecycle_state='archived', version=version+1,
                        updated_at=?, projected_at=?
                    WHERE id=? AND version=? AND lifecycle_state='active'
                      AND runtime_status='active'
                    """,
                    (now, now, identity.session_id, row["version"]),
                )
                if cursor.rowcount != 1:
                    raise RepositoryError(409, "session_conflict", "会话已更新，请重试")
                self._record_session_operation(
                    connection,
                    scope_id=identity.scope_id,
                    idempotency_key=idempotency_key,
                    command_type="gc01.session.logout",
                    event_type="gc01.session.revoked",
                    action="session.logout",
                    session_id=identity.session_id,
                    session_version=next_version,
                    principal_id=identity.principal_id,
                    membership_id=identity.membership_id,
                    payload_hash=payload_hash,
                    now=now,
                )
                secret_reference = (
                    str(row["secret_reference"]) if row["secret_reference"] else None
                )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
        self._delete_session_secret(secret_reference)

    def organization_snapshot(self, identity: SessionIdentity) -> dict[str, Any]:
        with self._connection() as connection:
            organization = connection.execute(
                """
                SELECT id AS organizationId, name, lifecycle_state AS lifecycleState,
                       version FROM organizations WHERE id=? AND record_kind='organization'
                """,
                (identity.organization_id,),
            ).fetchone()
            if organization is None:
                raise RepositoryError(404, "organization_missing", "组织不存在")
            members = [dict(row) for row in connection.execute(
                """
                SELECT membership.id AS membershipId,
                       membership.principal_id AS principalId,
                       principal.display_name AS displayName,
                       membership.role_key AS systemRole,
                       membership.visibility_scope AS visibilityScope,
                       membership.status, membership.version
                FROM organization_memberships AS membership
                JOIN principals AS principal ON principal.id=membership.principal_id
                WHERE membership.scope_id=? AND membership.record_kind='membership'
                  AND membership.status='active'
                  AND membership.lifecycle_state='active'
                  AND principal.status='active'
                  AND principal.lifecycle_state='active'
                ORDER BY principal.display_name, membership.id
                """,
                (identity.scope_id,),
            ).fetchall()]
            departments = [dict(row) for row in connection.execute(
                """
                SELECT id AS departmentId, name, color, lifecycle_state AS lifecycleState,
                       version FROM organizations
                WHERE parent_record_id=? AND record_kind='department'
                ORDER BY name, id
                """,
                (identity.organization_id,),
            ).fetchall()]
            department_assignments = [dict(row) for row in connection.execute(
                """
                SELECT assignment.id AS assignmentId,
                       assignment.parent_membership_id AS membershipId,
                       assignment.department_id AS departmentId,
                       assignment.role_key AS assignmentRole,
                       assignment.status, assignment.version,
                       assignment.lifecycle_state AS lifecycleState
                FROM organization_memberships AS assignment
                JOIN organization_memberships AS membership
                  ON membership.id=assignment.parent_membership_id
                 AND membership.scope_id=assignment.scope_id
                 AND membership.record_kind='membership'
                 AND membership.status='active'
                 AND membership.lifecycle_state='active'
                JOIN principals AS principal
                  ON principal.id=membership.principal_id
                 AND principal.status='active'
                 AND principal.lifecycle_state='active'
                JOIN organizations AS department
                  ON department.id=assignment.department_id
                 AND department.record_kind='department'
                 AND department.lifecycle_state='active'
                WHERE assignment.scope_id=?
                  AND assignment.record_kind='department_assignment'
                  AND assignment.status='active'
                  AND assignment.lifecycle_state='active'
                ORDER BY assignment.department_id,
                         assignment.parent_membership_id, assignment.id
                """,
                (identity.scope_id,),
            ).fetchall()]
            contacts = [dict(row) for row in connection.execute(
                """
                SELECT contact_type AS type, normalized_contact AS value,
                       verification_state AS verificationState
                FROM principals WHERE parent_principal_id=? AND principal_kind='contact'
                ORDER BY contact_type, normalized_contact
                """,
                (identity.principal_id,),
            ).fetchall()]
        principal = {
            "principalId": identity.principal_id,
            "displayName": identity.display_name,
            "contacts": contacts,
        }
        membership = {
            "membershipId": identity.membership_id,
            "principalId": identity.principal_id,
            "systemRole": identity.system_role,
            "visibilityScope": identity.visibility_scope,
            "status": "active",
        }
        authorization = self.current_authorization(identity)
        assignments_by_department: dict[str, list[dict[str, Any]]] = {}
        for assignment in department_assignments:
            department_id = str(assignment.get("departmentId") or "")
            if not department_id:
                continue
            assignments_by_department.setdefault(department_id, []).append(
                {
                    "assignmentId": assignment.get("assignmentId"),
                    "membershipId": assignment.get("membershipId"),
                    "roleKey": assignment.get("assignmentRole") or "member",
                    "isDepartmentLead": (
                        assignment.get("assignmentRole") == "department_lead"
                    ),
                    "status": assignment.get("status") or "active",
                    "version": int(assignment.get("version") or 1),
                }
            )
        for department in departments:
            department["members"] = assignments_by_department.get(
                str(department.get("departmentId") or ""),
                [],
            )
        return {
            "organization": dict(organization),
            "principal": principal,
            "membership": membership,
            "currentPrincipal": principal,
            "currentMembership": membership,
            "members": members,
            "departments": departments,
            "departmentAssignments": department_assignments,
            "managementTitles": [],
            "reportingLines": [],
            "invites": [],
            "authorization": authorization,
        }

    def current_authorization(self, identity: SessionIdentity) -> dict[str, Any]:
        try:
            with self._connection() as connection:
                return read_authorization_projection(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    principal_id=identity.principal_id,
                    membership_id=identity.membership_id,
                )
        except AuthorizationProjectionError as exc:
            raise RepositoryError(exc.status_code, exc.code, exc.message) from exc

    def ai_config(self, identity: SessionIdentity, *, include_secret: bool) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT resource.* FROM provider_resources AS resource
                JOIN authorization_scopes AS scope ON scope.id=resource.scope_id
                WHERE scope.organization_id=?
                  AND resource.resource_kind='organization_ai_configuration'
                  AND resource.lifecycle_state='active'
                ORDER BY resource.updated_at DESC LIMIT 1
                """,
                (identity.organization_id,),
            ).fetchone()
        if row is None:
            return {"status": "not_configured", "version": 0}
        result: dict[str, Any] = {
            "configId": str(row["id"]),
            "provider": row["provider"],
            "baseUrl": row["endpoint"],
            "modelName": row["model_name"],
            "keyFingerprint": row["secret_fingerprint"],
            "status": row["status"],
            "version": int(row["version"] or 1),
        }
        if include_secret and row["secret_reference"]:
            payload = json.loads(Path(str(row["secret_reference"])).read_text(encoding="utf-8"))
            encrypted = payload.get("encryptedApiKey")
            if encrypted:
                result["apiKey"] = self.cipher.decrypt(str(encrypted))
        return result

    @staticmethod
    def _knowledge_manifest_summary(receipt: Any) -> str:
        raw = str(receipt or "").strip()
        if not raw:
            return ""
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return raw[:2_000]
        if isinstance(payload, str):
            return payload.strip()[:2_000]
        if not isinstance(payload, dict):
            return ""
        for key in (
            "summary",
            "content",
            "text",
            "markdown",
            "previewSummary",
            "answer",
            "statement",
            "excerpt",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:2_000]
        return ""

    def project_knowledge_context(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        """Return only published/safe project knowledge, never member file data."""
        memory_kinds = {
            "favorite_memory": "favorite",
            "answer_favorite": "favorite",
            "system_inference": "system_inference",
            "inferred_memory": "system_inference",
        }
        website_kinds = {
            "official_website_fact",
            "website_fact",
            "official_fact",
            "website_summary",
        }
        with self._connection() as connection:
            project = self._require_project_access(
                connection,
                identity,
                project_id=project_id,
            )
            rows = connection.execute(
                """
                SELECT d.id, d.title, d.document_kind, d.visibility_scope,
                       d.publication_state, d.owner_membership_id,
                       d.current_version, d.updated_at, d.source_asset_id,
                       v.id AS document_version_id, v.content_hash,
                       m.receipt, m.availability_state,
                       a.source_kind
                FROM knowledge_documents AS d
                JOIN document_versions AS v
                  ON v.scope_id=d.scope_id AND v.document_id=d.id
                 AND v.version=d.current_version
                 AND v.publication_state='published'
                LEFT JOIN object_manifests AS m
                  ON m.scope_id=d.scope_id AND m.id=v.object_manifest_id
                 AND m.lifecycle_state='active'
                LEFT JOIN source_assets AS a
                  ON a.scope_id=d.scope_id AND a.id=d.source_asset_id
                 AND a.lifecycle_state='active'
                WHERE d.scope_id=? AND d.client_id=?
                  AND d.lifecycle_state='active'
                  AND d.publication_state='published'
                  AND (
                    d.visibility_scope='organization'
                    OR d.owner_membership_id=?
                  )
                ORDER BY d.updated_at DESC, d.id
                """,
                (identity.scope_id, project_id, identity.membership_id),
            ).fetchall()
            relationship_rows = connection.execute(
                """
                SELECT r.id, r.predicate, r.verification_state, r.confidence,
                       r.version, sd.title AS subject_title,
                       od.title AS object_title
                FROM relationship_triples AS r
                JOIN atomic_facts AS sf
                  ON sf.scope_id=r.scope_id AND sf.id=r.subject_fact_id
                 AND sf.lifecycle_state='active'
                JOIN content_chunks AS sc
                  ON sc.scope_id=sf.scope_id AND sc.id=sf.chunk_id
                 AND sc.lifecycle_state='active'
                JOIN document_versions AS sv
                  ON sv.scope_id=sc.scope_id AND sv.id=sc.document_version_id
                JOIN knowledge_documents AS sd
                  ON sd.scope_id=sv.scope_id AND sd.id=sv.document_id
                 AND sd.client_id=? AND sd.lifecycle_state='active'
                JOIN atomic_facts AS ofact
                  ON ofact.scope_id=r.scope_id AND ofact.id=r.object_fact_id
                 AND ofact.lifecycle_state='active'
                JOIN content_chunks AS oc
                  ON oc.scope_id=ofact.scope_id AND oc.id=ofact.chunk_id
                 AND oc.lifecycle_state='active'
                JOIN document_versions AS ov
                  ON ov.scope_id=oc.scope_id AND ov.id=oc.document_version_id
                JOIN knowledge_documents AS od
                  ON od.scope_id=ov.scope_id AND od.id=ov.document_id
                 AND od.client_id=? AND od.lifecycle_state='active'
                WHERE r.scope_id=? AND r.lifecycle_state='active'
                ORDER BY CASE r.verification_state WHEN 'verified' THEN 0 ELSE 1 END,
                         r.updated_at DESC, r.id
                LIMIT 24
                """,
                (project_id, project_id, identity.scope_id),
            ).fetchall()
            correction_rows = connection.execute(
                """
                SELECT fact.id, fact.version, fact.updated_at,
                       fact.fact_hash, manifest.receipt,
                       manifest.content_hash,
                       fact.confirmed_by_membership_id, sources.purpose_kind,
                       (
                         SELECT member.source_object_id
                         FROM source_set_members AS member
                         WHERE member.scope_id=fact.scope_id
                           AND member.source_set_id=fact.source_set_id
                           AND member.source_object_kind='ai_answer'
                           AND member.lifecycle_state='active'
                         ORDER BY member.ordinal, member.id
                         LIMIT 1
                       ) AS source_answer_id
                FROM atomic_facts AS fact
                JOIN source_sets AS sources
                  ON sources.scope_id=fact.scope_id
                 AND sources.id=fact.source_set_id
                 AND sources.client_id=?
                 AND sources.purpose_kind IN (
                   'answer_correction', 'answer_remember',
                   'strategic_profile_clarification'
                 )
                 AND sources.lifecycle_state='active'
                JOIN object_manifests AS manifest
                  ON manifest.scope_id=fact.scope_id
                 AND manifest.id=fact.fact_object_manifest_id
                 AND manifest.lifecycle_state='active'
                WHERE fact.scope_id=? AND fact.lifecycle_state='active'
                  AND fact.verification_state='verified'
                ORDER BY fact.updated_at DESC, fact.id
                """,
                (project_id, identity.scope_id),
            ).fetchall()
            favorite_rows = connection.execute(
                """
                SELECT sets.id, sets.version, sets.updated_at,
                       manifest.receipt, manifest.content_hash,
                       (
                         SELECT member.source_object_id
                           FROM source_set_members AS member
                          WHERE member.scope_id=sets.scope_id
                            AND member.source_set_id=sets.id
                            AND member.source_object_kind='ai_answer'
                            AND member.lifecycle_state='active'
                          ORDER BY member.ordinal, member.id
                          LIMIT 1
                       ) AS source_answer_id
                  FROM source_sets AS sets
                  JOIN source_set_members AS excerpt
                    ON excerpt.scope_id=sets.scope_id
                   AND excerpt.source_set_id=sets.id
                   AND excerpt.source_object_kind='favorite_excerpt'
                   AND excerpt.lifecycle_state='active'
                  JOIN object_manifests AS manifest
                    ON manifest.scope_id=excerpt.scope_id
                   AND manifest.id=excerpt.source_object_id
                   AND manifest.lifecycle_state='active'
                 WHERE sets.scope_id=? AND sets.client_id=?
                   AND sets.created_by_principal_id=?
                   AND sets.purpose_kind='answer_favorite'
                   AND sets.lifecycle_state='active'
                 ORDER BY sets.updated_at DESC, sets.id
                """,
                (identity.scope_id, project_id, identity.principal_id),
            ).fetchall()
            official_semantic_rows = connection.execute(
                """
                SELECT fact.id, fact.version, fact.updated_at, fact.fact_hash,
                       manifest.receipt, manifest.content_hash
                FROM atomic_facts AS fact
                JOIN source_sets AS sources
                  ON sources.scope_id=fact.scope_id
                 AND sources.id=fact.source_set_id
                 AND sources.client_id=?
                 AND sources.purpose_kind='official_website_capture'
                 AND sources.lifecycle_state='active'
                JOIN object_manifests AS manifest
                  ON manifest.scope_id=fact.scope_id
                 AND manifest.id=fact.fact_object_manifest_id
                 AND manifest.storage_kind='official_fact_candidate'
                 AND manifest.lifecycle_state='active'
                WHERE fact.scope_id=? AND fact.lifecycle_state='active'
                  AND fact.verification_state='verified'
                ORDER BY fact.updated_at DESC, fact.id
                LIMIT 160
                """,
                (project_id, identity.scope_id),
            ).fetchall()

        organization_shared: list[dict[str, Any]] = []
        # Semantic official facts come first so bounded AI context does not fill
        # up with whole-page summaries before precise person/project facts.
        website_facts: list[dict[str, Any]] = []
        for row in official_semantic_rows:
            summary = self._knowledge_manifest_summary(row["receipt"])
            if not summary:
                continue
            try:
                receipt_payload = json.loads(str(row["receipt"] or "{}"))
            except json.JSONDecodeError:
                receipt_payload = {}
            website_facts.append(
                {
                    "sourceId": str(row["id"]),
                    "sourceDescription": str(
                        receipt_payload.get("sourceTitle")
                        if isinstance(receipt_payload, dict)
                        else "官网事实"
                    )
                    or "官网事实",
                    "summary": summary,
                    "contentHash": str(row["content_hash"] or row["fact_hash"] or ""),
                    "sourceKind": "official_website_semantic_fact",
                    "sourceUrl": str(
                        receipt_payload.get("sourcePublicUrl")
                        if isinstance(receipt_payload, dict)
                        else ""
                    ),
                    "updatedAt": row["updated_at"],
                    "availabilityState": "ready",
                    "version": int(row["version"] or 1),
                    "verificationState": "verified",
                }
            )
        saved_memories: list[dict[str, Any]] = []
        for row in rows:
            kind = str(row["document_kind"] or "").strip().lower()
            if kind in {"explicit_memory", "answer_memory"}:
                # Frozen pre-contract rows were member-private drafts.  They are
                # not formal project knowledge and must not re-enter retrieval.
                continue
            summary = self._knowledge_manifest_summary(row["receipt"])
            item = {
                "sourceId": str(row["id"]),
                "documentVersionId": str(row["document_version_id"]),
                "sourceDescription": str(row["title"] or "组织知识"),
                "summary": summary,
                "contentHash": str(row["content_hash"] or ""),
                "sourceKind": kind,
                "updatedAt": row["updated_at"],
                "availabilityState": str(row["availability_state"] or "ready"),
                "version": int(row["current_version"] or 1),
            }
            try:
                receipt_payload = json.loads(str(row["receipt"] or "{}"))
            except json.JSONDecodeError:
                receipt_payload = {}
            if isinstance(receipt_payload, dict):
                source_url = str(receipt_payload.get("canonicalPublicUrl") or "").strip()
                if source_url:
                    item["sourceUrl"] = source_url
            memory_kind = memory_kinds.get(kind)
            if memory_kind:
                if str(row["owner_membership_id"] or "") == identity.membership_id:
                    saved_memories.append({**item, "memoryKind": memory_kind})
                continue
            source_kind = str(row["source_kind"] or "").strip().lower()
            if kind in website_kinds or source_kind in {
                "official_website",
                "official_website_fact",
                "website_official",
            }:
                website_facts.append(item)
            else:
                organization_shared.append(item)

        for row in correction_rows:
            summary = self._knowledge_manifest_summary(row["receipt"])
            if not summary:
                continue
            try:
                correction_receipt = json.loads(str(row["receipt"] or "{}"))
            except json.JSONDecodeError:
                correction_receipt = {}
            purpose_kind = str(row["purpose_kind"] or "")
            is_remember = purpose_kind == "answer_remember"
            is_profile_clarification = purpose_kind == "strategic_profile_clarification"
            saved_memories.append(
                {
                    "sourceId": str(row["id"]),
                    "sourceAnswerId": str(row["source_answer_id"] or "") or None,
                    "sourceDescription": (
                        "成员明确记住"
                        if is_remember
                        else "客户档案成员补充"
                        if is_profile_clarification
                        else "成员纠错/补充"
                    ),
                    "summary": summary,
                    "contentHash": str(row["content_hash"] or row["fact_hash"] or ""),
                    "sourceKind": purpose_kind or "answer_correction",
                    "memoryKind": "explicit_memory" if is_remember else "correction",
                    "selectedTextHash": str(
                        correction_receipt.get("selectedTextHash")
                        if isinstance(correction_receipt, dict)
                        else ""
                    ) or None,
                    "version": int(row["version"] or 1),
                    "updatedAt": row["updated_at"],
                    "availabilityState": "ready",
                    "authority": "organization_cloud",
                }
            )

        for row in favorite_rows:
            summary = self._knowledge_manifest_summary(row["receipt"])
            if not summary:
                continue
            saved_memories.append(
                {
                    "sourceId": str(row["id"]),
                    "sourceAnswerId": str(row["source_answer_id"] or "") or None,
                    "sourceDescription": "本人项目收藏",
                    "summary": summary,
                    "contentHash": str(row["content_hash"] or ""),
                    "sourceKind": "answer_favorite",
                    "memoryKind": "favorite",
                    "version": int(row["version"] or 1),
                    "updatedAt": row["updated_at"],
                    "availabilityState": "ready",
                    "authority": "organization_cloud",
                }
            )

        relationship_cards = [
            {
                "id": str(row["id"]),
                "subject": str(row["subject_title"] or "未命名事实"),
                "predicate": str(row["predicate"] or "相关"),
                "object": str(row["object_title"] or "未命名事实"),
                "verificationState": str(row["verification_state"] or "candidate"),
                "confidence": row["confidence"],
                "version": int(row["version"] or 1),
                "authority": "organization_cloud",
            }
            for row in relationship_rows
        ]
        return {
            "clientId": project_id,
            "state": "ready",
            "organizationSharedKnowledge": organization_shared,
            "officialWebsiteFacts": website_facts,
            "savedMemories": saved_memories,
            "relationshipCards": relationship_cards,
            "materialBoundary": {
                "sourceFileContentReturned": False,
                "sourceFilePathReturned": False,
                "localStorageLocatorReturned": False,
            },
            "generatedAt": utc_now(),
        }

    def _ai_answer_receipt(
        self,
        connection,
        identity: SessionIdentity,
        answer_id: str,
        *,
        idempotent_replay: bool,
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT answer.*, context.question_hash, context.status AS context_status
            FROM ai_answers AS answer
            JOIN ai_context_manifests AS context
              ON context.id=answer.ai_context_manifest_id
            WHERE answer.id=? AND answer.scope_id=?
              AND answer.lifecycle_state='active'
            """,
            (answer_id, identity.scope_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(
                503,
                "ai_answer_receipt_incomplete",
                "回答回执尚未完整落地，可以重试",
            )
        run = connection.execute(
            "SELECT run.id,run.status,run.version FROM execution_runs AS run "
            "JOIN commands AS command ON command.scope_id=run.scope_id "
            "AND command.operation_id=run.operation_id "
            "WHERE command.scope_id=? AND command.aggregate_type='ai_answer' "
            "AND command.aggregate_id=? AND run.run_kind='workbench_answer_generation' "
            "ORDER BY run.created_at DESC,run.id DESC LIMIT 1",
            (identity.scope_id, answer_id),
        ).fetchone()
        result = {
            "answer": {
                "answerId": str(row["id"]),
                "projectId": str(row["client_id"]),
                "threadId": str(row["thread_id"]),
                "questionHash": str(row["question_hash"]),
                "answerHash": str(row["answer_hash"]),
                "sourceSetId": str(row["source_set_id"]),
                "contextManifestId": str(row["ai_context_manifest_id"]),
                "botId": str(row["bot_id"]),
                "providerResourceId": str(row["provider_resource_id"]),
                "modelName": str(row["model_name"]),
                "sourceCount": int(row["source_count"] or 0),
                "materialAccessMode": str(row["material_access_mode"] or "none"),
                "boundaryState": str(row["boundary_state"] or "no_material_context"),
                "status": str(row["status"]),
                "version": int(row["version"] or 1),
                "createdAt": str(row["created_at"]),
                "updatedAt": str(row["updated_at"]),
            },
            "idempotentReplay": idempotent_replay,
        }
        if run is not None:
            result["agentRun"] = AgentRunReceipt(
                agent_kind="project_workspace",
                run_id=str(run["id"]),
                state=str(run["status"] or "completed"),
                stage="answer_ready",
                message="已基于本次真实来源完成回答",
                result_version=int(row["version"] or 1),
            ).as_dict()
        return result

    def save_ai_answer(
        self,
        identity: SessionIdentity,
        *,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Persist only the GC-14 safe answer identity/model receipt.

        Question text, answer text, local paths and excerpts are deliberately
        absent from this command.  The cloud stores hashes, source identities,
        the organization Agent/model receipt and an auditable external effect.
        """

        command_type = "gc14.workbench.answer.recorded"
        normalized = {
            "answerId": str(payload.get("answerId") or "").strip(),
            "projectId": str(payload.get("projectId") or "").strip(),
            "threadId": str(payload.get("threadId") or "").strip(),
            "questionHash": str(payload.get("questionHash") or "").strip().lower(),
            "answerHash": str(payload.get("answerHash") or "").strip().lower(),
            "sourceSetId": str(payload.get("sourceSetId") or "").strip(),
            "contextManifestId": str(payload.get("contextManifestId") or "").strip(),
            "lineageId": str(payload.get("lineageId") or "").strip(),
            "botId": str(payload.get("botId") or "").strip(),
            "providerResourceId": str(payload.get("providerResourceId") or "").strip(),
            "modelName": str(payload.get("modelName") or "").strip(),
            "sourceCount": int(payload.get("sourceCount") or 0),
            "materialAccessMode": str(payload.get("materialAccessMode") or "none"),
            "boundaryState": str(payload.get("boundaryState") or "no_material_context"),
            "selectedSources": [
                {
                    "sourceObjectId": str(item.get("sourceObjectId") or "").strip(),
                    "sourceObjectKind": str(item.get("sourceObjectKind") or "").strip(),
                    "sourceVersion": max(1, int(item.get("sourceVersion") or 1)),
                    "contentHash": str(item.get("contentHash") or "").strip().lower(),
                }
                for item in payload.get("selectedSources") or []
                if isinstance(item, dict)
            ],
            "originInstanceId": str(payload.get("originInstanceId") or "").strip(),
        }
        required = (
            "answerId",
            "projectId",
            "threadId",
            "questionHash",
            "answerHash",
            "sourceSetId",
            "contextManifestId",
            "lineageId",
            "botId",
            "providerResourceId",
            "modelName",
            "originInstanceId",
        )
        if any(not normalized[key] for key in required):
            raise RepositoryError(422, "ai_answer_receipt_incomplete", "回答安全回执不完整")
        if any(
            len(normalized[key]) != 64
            or any(character not in "0123456789abcdef" for character in normalized[key])
            for key in ("questionHash", "answerHash")
        ):
            raise RepositoryError(422, "ai_answer_hash_invalid", "回答哈希无效")
        if normalized["sourceCount"] != len(normalized["selectedSources"]):
            raise RepositoryError(422, "ai_answer_source_count_mismatch", "回答来源数量不一致")
        if any(
            not item["sourceObjectId"]
            or item["sourceObjectKind"]
            not in {
                "local_document",
                "organization_knowledge",
                "official_website_fact",
                "explicit_memory",
                "favorite",
                "correction",
                "agent_skill",
            }
            or (
                item["contentHash"]
                and (
                    len(item["contentHash"]) != 64
                    or any(
                        character not in "0123456789abcdef"
                        for character in item["contentHash"]
                    )
                )
            )
            for item in normalized["selectedSources"]
        ):
            raise RepositoryError(422, "ai_answer_source_invalid", "回答来源回执无效")
        expected_bot_id = builtin_agent_id(identity.organization_id, "project_workspace")
        if normalized["botId"] != expected_bot_id:
            raise RepositoryError(409, "ai_answer_agent_mismatch", "回答未绑定当前组织的工作台 Agent")
        payload_hash = sha256_text(canonical_json(normalized))
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                project = self._require_project_access(
                    connection,
                    identity,
                    project_id=normalized["projectId"],
                )
                existing = self._existing_command(
                    connection,
                    scope_id=identity.scope_id,
                    idempotency_key=idempotency_key,
                    command_type=command_type,
                    payload_hash=payload_hash,
                )
                if existing is not None:
                    receipt = self._ai_answer_receipt(
                        connection,
                        identity,
                        str(existing["aggregate_id"]),
                        idempotent_replay=True,
                    )
                    connection.commit()
                    return receipt
                bot = connection.execute(
                    """
                    SELECT bot.id FROM bot_definitions AS bot
                    JOIN authorization_scopes AS scope ON scope.id=bot.scope_id
                    WHERE bot.id=? AND scope.organization_id=?
                      AND bot.agent_kind='project_workspace'
                      AND bot.enabled=1 AND bot.lifecycle_state='active'
                    """,
                    (normalized["botId"], identity.organization_id),
                ).fetchone()
                if bot is None:
                    raise RepositoryError(409, "workspace_agent_not_ready", "项目问答能力尚未就绪")
                provider = connection.execute(
                    """
                    SELECT resource.id, resource.model_name, resource.status
                    FROM provider_resources AS resource
                    JOIN authorization_scopes AS scope ON scope.id=resource.scope_id
                    WHERE resource.id=? AND scope.organization_id=?
                      AND resource.resource_kind='organization_ai_configuration'
                      AND resource.lifecycle_state='active'
                    """,
                    (normalized["providerResourceId"], identity.organization_id),
                ).fetchone()
                if provider is None or str(provider["status"] or "") != "ready":
                    raise RepositoryError(409, "organization_ai_not_ready", "组织大模型尚未就绪")
                if str(provider["model_name"] or "") != normalized["modelName"]:
                    raise RepositoryError(409, "organization_ai_model_mismatch", "回答模型与组织配置不一致")
                collision = connection.execute(
                    "SELECT answer_hash FROM ai_answers WHERE id=?",
                    (normalized["answerId"],),
                ).fetchone()
                if collision is not None:
                    raise RepositoryError(409, "ai_answer_identity_conflict", "回答标识已被其他操作使用")

                now = utc_now()
                safe_context = {
                    "schema": "yiyu.cloud-ai-context-receipt.v1",
                    "answerId": normalized["answerId"],
                    "clientId": normalized["projectId"],
                    "threadId": normalized["threadId"],
                    "questionHash": normalized["questionHash"],
                    "sourceSetId": normalized["sourceSetId"],
                    "selectedSources": normalized["selectedSources"],
                    "sourceCount": normalized["sourceCount"],
                    "materialAccessMode": normalized["materialAccessMode"],
                    "boundaryState": normalized["boundaryState"],
                    "generatorVersion": "yiyu-gc14-workbench-p07-v1",
                }
                safe_context_json = canonical_json(safe_context)
                safe_context_hash = sha256_text(safe_context_json)
                context_object_manifest_id = self._record_id(
                    "manifest",
                    normalized["contextManifestId"],
                    "safe-context",
                )
                connection.execute(
                    """
                    INSERT INTO object_manifests (
                        id, scope_id, storage_key, content_hash, lifecycle_state,
                        receipt, holder_role, holder_instance_id, storage_kind,
                        byte_size, media_type, availability_state, receipt_hash,
                        created_at, verified_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud_metadata_receipt',
                              ?, 'metadata_receipt', ?,
                              'application/vnd.yiyu.ai-context-receipt+json',
                              'ready', ?, ?, ?, NULL, 'local', ?)
                    """,
                    (
                        context_object_manifest_id,
                        identity.scope_id,
                        safe_context_hash,
                        safe_context_json,
                        self.cloud_instance_id,
                        len(safe_context_json.encode("utf-8")),
                        safe_context_hash,
                        now,
                        now,
                        normalized["originInstanceId"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO source_sets (
                        id, scope_id, client_id, security_label_set_version,
                        source_count, version, purpose_kind, publication_state,
                        created_by_principal_id, created_at, expires_at,
                        lifecycle_state, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, 'policy-v1', ?, 1, 'ai_answer_context',
                              'draft', ?, ?, NULL, 'active', ?, NULL, 'local', ?)
                    """,
                    (
                        normalized["sourceSetId"],
                        identity.scope_id,
                        normalized["projectId"],
                        normalized["sourceCount"],
                        identity.principal_id,
                        now,
                        now,
                        normalized["originInstanceId"],
                    ),
                )
                for ordinal, source in enumerate(normalized["selectedSources"]):
                    member_id = self._record_id(
                        "source_member",
                        normalized["sourceSetId"],
                        f"{source['sourceObjectKind']}:{source['sourceObjectId']}",
                    )
                    connection.execute(
                        """
                        INSERT INTO source_set_members (
                            id, scope_id, source_set_id, source_object_id,
                            source_version, policy_version, source_object_kind,
                            ordinal, added_at, removed_at, version,
                            lifecycle_state, created_at, updated_at, deleted_at,
                            authority_role, origin_instance_id
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, 1,
                                  'active', ?, ?, NULL, 'local', ?)
                        """,
                        (
                            member_id,
                            identity.scope_id,
                            normalized["sourceSetId"],
                            source["sourceObjectId"],
                            source["sourceVersion"],
                            source["sourceObjectKind"],
                            ordinal,
                            now,
                            now,
                            now,
                            normalized["originInstanceId"],
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO derivation_lineage (
                        id, scope_id, source_set_id, policy_version_id,
                        grant_generation, derivative_kind, derivative_object_id,
                        generator_version, generated_at, invalidated_at,
                        source_version, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, NULL, 1, 'ai_context_manifest', ?,
                              'yiyu-gc14-workbench-p07-v1', ?, NULL, 1,
                              'local', ?)
                    """,
                    (
                        normalized["lineageId"],
                        identity.scope_id,
                        normalized["sourceSetId"],
                        normalized["contextManifestId"],
                        now,
                        normalized["originInstanceId"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO ai_context_manifests (
                        id, scope_id, lineage_id, provider_resource_id,
                        policy_version, status, source_set_id, question_hash,
                        retrieval_policy_version, selected_source_count,
                        context_object_manifest_id, generated_at, invalidated_at,
                        source_version, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, 1, 'ready', ?, ?,
                              'yiyu-gc14-workbench-p07-v1', ?, ?, ?, NULL, 1,
                              'local', ?)
                    """,
                    (
                        normalized["contextManifestId"],
                        identity.scope_id,
                        normalized["lineageId"],
                        normalized["providerResourceId"],
                        normalized["sourceSetId"],
                        normalized["questionHash"],
                        normalized["sourceCount"],
                        context_object_manifest_id,
                        now,
                        normalized["originInstanceId"],
                    ),
                )
                # The context receipt is a rebuildable retrieval cache, not a
                # second knowledge authority.  Registering it here makes the
                # cache lifecycle explicit: project-material changes and
                # GC-15 invalidation already clear cache_entries by lineage.
                connection.execute(
                    """
                    INSERT INTO cache_entries (
                        id, scope_id, lineage_id, subject_hash, policy_version,
                        expires_at, cache_kind, object_manifest_id,
                        source_version, generated_at, invalidated_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, 1, ?, 'ai_context_receipt', ?, 1, ?,
                              NULL, 'local', ?)
                    """,
                    (
                        self._record_id("cache", normalized["contextManifestId"], "receipt"),
                        identity.scope_id,
                        normalized["lineageId"],
                        normalized["questionHash"],
                        _expires_at(REFRESH_TTL),
                        context_object_manifest_id,
                        now,
                        normalized["originInstanceId"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO ai_answers (
                        id, scope_id, client_id, bot_id, source_set_id, status,
                        created_at, thread_id, ai_context_manifest_id,
                        provider_resource_id, model_name,
                        answer_object_manifest_id, answer_hash, source_count,
                        material_access_mode, boundary_state, version,
                        lifecycle_state, updated_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, 'ready', ?, ?, ?, ?, ?, NULL, ?,
                              ?, ?, ?, 1, 'active', ?, NULL)
                    """,
                    (
                        normalized["answerId"],
                        identity.scope_id,
                        normalized["projectId"],
                        normalized["botId"],
                        normalized["sourceSetId"],
                        now,
                        normalized["threadId"],
                        normalized["contextManifestId"],
                        normalized["providerResourceId"],
                        normalized["modelName"],
                        normalized["answerHash"],
                        normalized["sourceCount"],
                        normalized["materialAccessMode"],
                        normalized["boundaryState"],
                        now,
                    ),
                )

                operation_id = "op_" + sha256_text(
                    f"gc14\x1f{identity.scope_id}\x1f{command_type}\x1f{idempotency_key}"
                )[:30]
                result_hash = sha256_text(
                    canonical_json(
                        {
                            "answerId": normalized["answerId"],
                            "answerHash": normalized["answerHash"],
                            "sourceCount": normalized["sourceCount"],
                            "modelName": normalized["modelName"],
                        }
                    )
                )
                connection.execute(
                    """
                    INSERT INTO idempotency_records (
                        id, scope_id, idempotency_key, payload_hash,
                        result_hash, expires_at, result_object_manifest_id,
                        status, created_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'settled', ?, 'cloud', ?)
                    """,
                    (
                        self._record_id("idem", operation_id, command_type),
                        identity.scope_id,
                        idempotency_key,
                        payload_hash,
                        result_hash,
                        _expires_at(REFRESH_TTL),
                        now,
                        self.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO commands (
                        id, scope_id, operation_id, idempotency_key,
                        aggregate_type, aggregate_id, command_type,
                        actor_principal_id, expected_aggregate_version,
                        device_command_sequence, status, actor_membership_id,
                        payload_object_manifest_id, payload_hash, submitted_at,
                        settled_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, 'ai_answer', ?, ?, ?, NULL, NULL,
                              'settled', ?, ?, ?, ?, ?, 'cloud', ?)
                    """,
                    (
                        self._record_id("cmd", operation_id, command_type),
                        identity.scope_id,
                        operation_id,
                        idempotency_key,
                        normalized["answerId"],
                        command_type,
                        identity.principal_id,
                        identity.membership_id,
                        context_object_manifest_id,
                        payload_hash,
                        now,
                        now,
                        self.cloud_instance_id,
                    ),
                )
                run_id = self._record_id("run", operation_id, "project-workspace")
                connection.execute(
                    """
                    INSERT INTO execution_runs (
                        id, scope_id, bot_id, rule_id, task_id, operation_id,
                        status, initiator_membership_id, proposal_id, run_kind,
                        progress_object_manifest_id, result_object_manifest_id,
                        started_at, finished_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, NULL, NULL, ?, 'completed', ?, NULL,
                              'workbench_answer_generation', NULL, NULL, ?, ?, 1,
                              'active', ?, ?, NULL)
                    """,
                    (
                        run_id,
                        identity.scope_id,
                        normalized["botId"],
                        operation_id,
                        identity.membership_id,
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                effect_id = self._record_id("effect", operation_id, "organization-ai")
                connection.execute(
                    """
                    INSERT INTO external_side_effects (
                        id, scope_id, operation_id, provider_resource_id,
                        effect_kind, outcome, request_hash,
                        remote_receipt_hash, executed_by_instance_id,
                        attempted_at, settled_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, ?, 'organization_ai_completion',
                              'succeeded', ?, ?, ?, ?, ?, 1, 'active', ?, ?, NULL)
                    """,
                    (
                        effect_id,
                        identity.scope_id,
                        operation_id,
                        normalized["providerResourceId"],
                        normalized["questionHash"],
                        normalized["answerHash"],
                        normalized["originInstanceId"],
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                event_hash = sha256_text(
                    f"{operation_id}|{normalized['answerId']}|{result_hash}"
                )
                connection.execute(
                    """
                    INSERT INTO outbox_events (
                        id, scope_id, operation_id, aggregate_version,
                        event_type, status, aggregate_type, aggregate_id,
                        event_object_manifest_id, event_hash, available_at,
                        published_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, 1, 'gc14.workbench.answer.recorded',
                              'pending', 'ai_answer', ?, ?, ?, ?, NULL,
                              'cloud', ?)
                    """,
                    (
                        self._record_id("evt", operation_id, command_type),
                        identity.scope_id,
                        operation_id,
                        normalized["answerId"],
                        context_object_manifest_id,
                        event_hash,
                        now,
                        self.cloud_instance_id,
                    ),
                )
                audit_id = self._record_id("audit", operation_id, "answer.recorded")
                connection.execute(
                    """
                    INSERT INTO audit_events (
                        id, scope_id, operation_id, actor_id, action,
                        event_hash, actor_membership_id, target_resource_id,
                        details_object_manifest_id, occurred_at,
                        origin_instance_id, created_at, integrity_hash,
                        authority_role
                    ) VALUES (?, ?, ?, ?, 'workbench.answer.recorded', ?, ?,
                              NULL, ?, ?, ?, ?, ?, 'cloud')
                    """,
                    (
                        audit_id,
                        identity.scope_id,
                        operation_id,
                        identity.principal_id,
                        event_hash,
                        identity.membership_id,
                        context_object_manifest_id,
                        now,
                        self.cloud_instance_id,
                        now,
                        sha256_text(f"{audit_id}|{event_hash}|{now}|{self.cloud_instance_id}"),
                    ),
                )
                connection.commit()
                return self._ai_answer_receipt(
                    connection,
                    identity,
                    normalized["answerId"],
                    idempotent_replay=False,
                )
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

    def correct_ai_answer_fact(
        self,
        identity: SessionIdentity,
        *,
        answer_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        from .repositories.gc12_corrections import correct_answer_fact

        return correct_answer_fact(
            self,
            identity,
            answer_id=answer_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    def project_narrative(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        """Read the GC-14 strategic profile from the strict 88-table repository."""

        from .repositories.workbench_outputs import project_narrative

        return project_narrative(self, identity, project_id=project_id)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)

        def unavailable(*_: Any, **__: Any) -> Any:
            raise RepositoryError(
                501,
                "golden_chain_frozen",
                f"{name} 已按 88 表底座冻结，等待对应黄金链接通",
            )

        return unavailable
