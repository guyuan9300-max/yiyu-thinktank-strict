from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.security import payload_fingerprint, redact_payload

from ..repository import CloudRepository, RepositoryError, SessionIdentity


class ScopedConfigurationRepository:
    """Authority for v3 organization defaults and per-membership overrides."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    def _assert_identity(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        admin: bool = False,
    ) -> sqlite3.Row:
        if identity.cloud_instance_id != self.repository.cloud_instance_id:
            raise RepositoryError(
                409,
                "cloud_identity_mismatch",
                "会话云实例与当前严格云不一致",
            )
        row = connection.execute(
            """
            SELECT m.membership_id, m.principal_id, m.organization_id,
                   m.system_role, m.status, m.scope_id
            FROM organization_memberships AS m
            JOIN organization_records AS o
              ON o.organization_id = m.organization_id
            WHERE m.membership_id = ? AND m.principal_id = ?
              AND m.organization_id = ? AND m.scope_id = ?
              AND o.cloud_instance_id = ?
              AND o.lifecycle_state = 'active'
            """,
            (
                identity.membership_id,
                identity.principal_id,
                identity.organization_id,
                identity.scope_id,
                identity.cloud_instance_id,
            ),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise RepositoryError(403, "membership_inactive", "当前组织成员身份不可用")
        if admin and row["system_role"] != "admin":
            raise RepositoryError(403, "admin_required", "仅管理员可以修改组织默认配置")
        return row

    @staticmethod
    def _row_public(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            return {}
        value = json.loads(str(row["public_config_json"]))
        if not isinstance(value, dict):
            raise RepositoryError(
                500,
                "scoped_configuration_corrupt",
                "分域配置公开数据损坏",
            )
        return value

    @staticmethod
    def _public_config(value: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(value)
        if redact_payload(normalized) != normalized:
            raise RepositoryError(
                422,
                "public_configuration_contains_secret",
                "公开配置不得包含凭据、token、密码或 API Key",
            )
        encoded = canonical_json(normalized)
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise RepositoryError(
                413,
                "public_configuration_too_large",
                "公开配置超过 256 KiB 限制",
            )
        return normalized

    @staticmethod
    def _configuration_row(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        scope_kind: str,
    ) -> sqlite3.Row | None:
        if scope_kind == "personal":
            return connection.execute(
                """
                SELECT * FROM scoped_configuration_records
                WHERE organization_id = ? AND membership_id = ?
                  AND principal_id = ? AND scope_kind = 'personal'
                  AND configuration_kind = ?
                  AND lifecycle_state = 'active'
                """,
                (
                    identity.organization_id,
                    identity.membership_id,
                    identity.principal_id,
                    configuration_kind,
                ),
            ).fetchone()
        return connection.execute(
            """
            SELECT * FROM scoped_configuration_records
            WHERE organization_id = ? AND scope_kind = 'organization'
              AND configuration_kind = ?
              AND lifecycle_state = 'active'
            """,
            (identity.organization_id, configuration_kind),
        ).fetchone()

    def _personal_scope(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
    ) -> str:
        row = connection.execute(
            """
            SELECT scope_id
            FROM authorization_scopes
            WHERE scope_kind = 'personal' AND principal_id = ?
              AND organization_id IS NULL
            ORDER BY created_at, scope_id
            LIMIT 1
            """,
            (identity.principal_id,),
        ).fetchone()
        if row is not None:
            return str(row["scope_id"])
        scope_id = new_id()
        now = utc_now()
        connection.execute(
            """
            INSERT INTO authorization_scopes (
                scope_id, scope_kind, principal_id, organization_id,
                policy_version, created_at, updated_at
            ) VALUES (?, 'personal', ?, NULL, 1, ?, ?)
            """,
            (scope_id, identity.principal_id, now, now),
        )
        return scope_id

    @staticmethod
    def _receipt(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
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
        if row is None:
            return None
        if str(row["payload_hash"]) != payload_hash:
            raise RepositoryError(
                409,
                "idempotency_conflict",
                "操作标识已用于不同请求",
            )
        result = json.loads(str(row["result_json"]))
        if not isinstance(result, dict):
            raise RepositoryError(500, "idempotency_receipt_corrupt", "幂等回执损坏")
        return result

    def _record_command(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        payload_hash: str,
        aggregate_id: str,
        before_version: int | None,
        after_version: int,
        result: Mapping[str, Any],
        aggregate_type: str = "scoped_configuration",
        audit_summary: Mapping[str, Any] | None = None,
        outbox_payload: Mapping[str, Any] | None = None,
        operation_id: str | None = None,
    ) -> None:
        now = utc_now()
        operation_id = operation_id or new_id()
        connection.execute(
            """
            INSERT INTO command_envelopes (
                command_id, scope_id, organization_id, operation_id,
                idempotency_key, aggregate_type, aggregate_id,
                command_type, actor_principal_id, expected_version,
                payload_json, payload_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, 'committed', ?, ?)
            """,
            (
                new_id(),
                identity.scope_id,
                identity.organization_id,
                operation_id,
                idempotency_key,
                aggregate_type,
                aggregate_id,
                command_type,
                identity.principal_id,
                before_version,
                canonical_json(dict(payload)),
                payload_hash,
                now,
                now,
            ),
        )
        result_json = canonical_json(dict(result))
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
        self.repository._insert_audit(
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            actor_id=identity.principal_id,
            action=command_type,
            resource_type=aggregate_type,
            resource_id=aggregate_id,
            before_version=before_version,
            after_version=after_version,
            summary=dict(
                audit_summary
                or {
                    "configurationKind": payload["configurationKind"],
                    "scopeKind": payload["scopeKind"],
                    "provider": payload["provider"],
                    "secretAction": payload["secretAction"],
                    "secretFingerprint": payload.get("secretFingerprint"),
                }
            ),
        )
        self.repository._insert_outbox(
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=after_version,
            event_type=f"{command_type}.committed",
            payload=dict(
                outbox_payload
                or {
                    "organizationId": identity.organization_id,
                    "configurationId": aggregate_id,
                    "configurationKind": payload["configurationKind"],
                    "scopeKind": payload["scopeKind"],
                    "version": after_version,
                }
            ),
        )

    def read(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        defaults: Mapping[str, Any],
        personal_only: bool = False,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            member = self._assert_identity(connection, identity)
            personal = self._configuration_row(
                connection,
                identity,
                configuration_kind=configuration_kind,
                scope_kind="personal",
            )
            organization = (
                None
                if personal_only
                else self._configuration_row(
                    connection,
                    identity,
                    configuration_kind=configuration_kind,
                    scope_kind="organization",
                )
            )
        effective = personal or organization
        public = {**dict(defaults), **self._row_public(effective)}
        public.update(
            {
                "updatedAt": (
                    str(effective["updated_at"]) if effective is not None else ""
                ),
                "version": int(effective["version"]) if effective is not None else 0,
                "expectedVersion": (
                    int(effective["version"]) if effective is not None else 0
                ),
                "effectiveScopeKind": (
                    str(effective["scope_kind"]) if effective is not None else None
                ),
                "defaultWriteScope": (
                    "personal"
                    if personal_only or member["system_role"] != "admin"
                    else "organization"
                ),
                "scopeVersions": {
                    "organization": (
                        int(organization["version"]) if organization is not None else 0
                    ),
                    "personal": int(personal["version"]) if personal is not None else 0,
                },
                "hasCredentials": bool(
                    effective is not None
                    and effective["encrypted_secret_bundle"] is not None
                ),
                "secretFingerprint": (
                    str(effective["secret_fingerprint"])
                    if effective is not None
                    and effective["secret_fingerprint"] is not None
                    else None
                ),
            }
        )
        return public

    def upsert(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        scope_kind: str,
        provider: str,
        public_config: Mapping[str, Any],
        expected_version: int,
        idempotency_key: str,
        secret_bundle: Mapping[str, Any] | None = None,
        secret_action: str = "preserve",
    ) -> dict[str, Any]:
        if scope_kind not in {"organization", "personal"}:
            raise RepositoryError(
                422,
                "configuration_scope_invalid",
                "配置作用域必须是 organization 或 personal",
            )
        if personal_only := configuration_kind in {
            "task_settings",
            "client_workspace_settings",
            "topics_settings",
            "analysis_workbench_settings",
            "handbook_settings",
            "transcription_preference",
            "local_input_memory",
        }:
            if scope_kind != "personal":
                raise RepositoryError(
                    403,
                    "personal_configuration_scope_required",
                    "该设置只允许当前成员保存个人偏好",
                )
        del personal_only
        if expected_version < 0:
            raise RepositoryError(422, "expected_version_invalid", "expectedVersion 无效")
        if secret_action not in {"preserve", "replace", "clear"}:
            raise RepositoryError(422, "secret_action_invalid", "凭据更新方式无效")
        if secret_action == "replace" and not secret_bundle:
            raise RepositoryError(422, "secret_bundle_required", "缺少要保存的凭据")
        normalized_public = self._public_config(public_config)
        encrypted_secret: str | None = None
        secret_fingerprint: str | None = None
        if secret_action == "replace":
            encoded_secret = canonical_json(dict(secret_bundle or {}))
            encrypted = self.repository.cipher.encrypt(encoded_secret)
            encrypted_secret = encrypted.ciphertext
            secret_fingerprint = encrypted.fingerprint
        safe_payload: dict[str, Any] = {
            "configurationKind": configuration_kind,
            "scopeKind": scope_kind,
            "provider": provider,
            "publicConfig": normalized_public,
            "expectedVersion": expected_version,
            "secretAction": secret_action,
            "secretFingerprint": secret_fingerprint,
        }
        command_type = f"configuration.{configuration_kind}.saved"
        payload_hash = payload_fingerprint(
            {
                key: value
                for key, value in safe_payload.items()
                if key != "expectedVersion"
            }
        )
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(
                    connection,
                    identity,
                    admin=scope_kind == "organization",
                )
                replay = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                row = self._configuration_row(
                    connection,
                    identity,
                    configuration_kind=configuration_kind,
                    scope_kind=scope_kind,
                )
                now = utc_now()
                if row is None:
                    if expected_version != 0:
                        raise RepositoryError(
                            409,
                            "configuration_version_conflict",
                            "配置尚未创建，请刷新后重试",
                        )
                    configuration_id = new_id()
                    scope_id = (
                        identity.scope_id
                        if scope_kind == "organization"
                        else self._personal_scope(connection, identity)
                    )
                    connection.execute(
                        """
                        INSERT INTO scoped_configuration_records (
                            configuration_id, scope_id, scope_kind,
                            organization_id, principal_id, membership_id,
                            configuration_kind, provider, public_config_json,
                            encrypted_secret_bundle, secret_fingerprint,
                            secret_envelope_version, lifecycle_state, version,
                            updated_by_membership_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1,
                                  'active', 1, ?, ?, ?)
                        """,
                        (
                            configuration_id,
                            scope_id,
                            scope_kind,
                            identity.organization_id,
                            identity.principal_id
                            if scope_kind == "personal"
                            else None,
                            identity.membership_id
                            if scope_kind == "personal"
                            else None,
                            configuration_kind,
                            provider,
                            canonical_json(normalized_public),
                            encrypted_secret,
                            secret_fingerprint,
                            identity.membership_id,
                            now,
                            now,
                        ),
                    )
                    before_version = None
                    after_version = 1
                    final_secret_fingerprint = secret_fingerprint
                    has_credentials = encrypted_secret is not None
                else:
                    current_version = int(row["version"])
                    if expected_version != current_version:
                        raise RepositoryError(
                            409,
                            "configuration_version_conflict",
                            "配置已被更新，请刷新后重试",
                        )
                    configuration_id = str(row["configuration_id"])
                    next_encrypted = row["encrypted_secret_bundle"]
                    next_fingerprint = row["secret_fingerprint"]
                    if secret_action == "replace":
                        next_encrypted = encrypted_secret
                        next_fingerprint = secret_fingerprint
                    elif secret_action == "clear":
                        next_encrypted = None
                        next_fingerprint = None
                    changed = connection.execute(
                        """
                        UPDATE scoped_configuration_records
                        SET provider = ?, public_config_json = ?,
                            encrypted_secret_bundle = ?,
                            secret_fingerprint = ?,
                            secret_envelope_version =
                                CASE WHEN ? = 'replace'
                                     THEN secret_envelope_version + 1
                                     ELSE secret_envelope_version END,
                            lifecycle_state = 'active',
                            version = version + 1,
                            updated_by_membership_id = ?, updated_at = ?
                        WHERE configuration_id = ? AND organization_id = ?
                          AND version = ?
                        """,
                        (
                            provider,
                            canonical_json(normalized_public),
                            next_encrypted,
                            next_fingerprint,
                            secret_action,
                            identity.membership_id,
                            now,
                            configuration_id,
                            identity.organization_id,
                            current_version,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "configuration_version_conflict",
                            "配置已被更新，请刷新后重试",
                        )
                    before_version = current_version
                    after_version = current_version + 1
                    final_secret_fingerprint = (
                        str(next_fingerprint) if next_fingerprint is not None else None
                    )
                    has_credentials = next_encrypted is not None
                result = {
                    **normalized_public,
                    "updatedAt": now,
                    "version": after_version,
                    "expectedVersion": after_version,
                    "effectiveScopeKind": scope_kind,
                    "defaultWriteScope": scope_kind,
                    "hasCredentials": has_credentials,
                    "secretFingerprint": final_secret_fingerprint,
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=safe_payload,
                    payload_hash=payload_hash,
                    aggregate_id=configuration_id,
                    before_version=before_version,
                    after_version=after_version,
                    result=result,
                )
                connection.commit()
                return result
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RepositoryError(
                    409,
                    "configuration_identity_conflict",
                    "该作用域的配置已存在，请刷新后重试",
                ) from exc
            except Exception:
                connection.rollback()
                raise

    def server_secret(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
    ) -> dict[str, Any] | None:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity)
            row = self._configuration_row(
                connection,
                identity,
                configuration_kind=configuration_kind,
                scope_kind="personal",
            )
            if row is None:
                row = self._configuration_row(
                    connection,
                    identity,
                    configuration_kind=configuration_kind,
                    scope_kind="organization",
                )
        if row is None or row["encrypted_secret_bundle"] is None:
            return None
        decoded = json.loads(
            self.repository.cipher.decrypt(str(row["encrypted_secret_bundle"]))
        )
        if not isinstance(decoded, dict):
            raise RepositoryError(
                500,
                "scoped_configuration_secret_corrupt",
                "配置凭据密文损坏",
            )
        return decoded

    def request_probe(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = f"configuration.{configuration_kind}.probe_requested"
        safe_payload = {
            "configurationKind": configuration_kind,
            "organizationId": identity.organization_id,
            "membershipId": identity.membership_id,
        }
        payload_hash = payload_fingerprint(safe_payload)
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(connection, identity)
                replay = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                row = self._configuration_row(
                    connection,
                    identity,
                    configuration_kind=configuration_kind,
                    scope_kind="personal",
                )
                if row is None:
                    row = self._configuration_row(
                        connection,
                        identity,
                        configuration_kind=configuration_kind,
                        scope_kind="organization",
                    )
                provider = str(row["provider"] or "") if row is not None else ""
                has_credentials = bool(
                    row is not None and row["encrypted_secret_bundle"] is not None
                )
                configured = bool(provider and has_credentials)
                remote_id = (
                    str(row["configuration_id"])
                    if row is not None
                    else f"{identity.organization_id}:{identity.membership_id}"
                )
                resource_kind = f"{configuration_kind}_configuration_probe"
                provider_name = provider or "unconfigured"
                resource = connection.execute(
                    """
                    SELECT provider_resource_id, version
                    FROM external_provider_resources
                    WHERE scope_id = ? AND provider = ?
                      AND resource_kind = ? AND remote_id = ?
                    """,
                    (
                        identity.scope_id,
                        provider_name,
                        resource_kind,
                        remote_id,
                    ),
                ).fetchone()
                now = utc_now()
                if resource is None:
                    resource_id = new_id()
                    before_version = None
                    after_version = 1
                    connection.execute(
                        """
                        INSERT INTO external_provider_resources (
                            provider_resource_id, scope_id, organization_id,
                            provider, resource_kind, remote_id,
                            retention_state, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            resource_id,
                            identity.scope_id,
                            identity.organization_id,
                            provider_name,
                            resource_kind,
                            remote_id,
                            "probe_pending"
                            if configured
                            else "configuration_missing",
                            now,
                            now,
                        ),
                    )
                else:
                    resource_id = str(resource["provider_resource_id"])
                    before_version = int(resource["version"])
                    after_version = before_version + 1
                    changed = connection.execute(
                        """
                        UPDATE external_provider_resources
                        SET retention_state = ?, version = version + 1,
                            updated_at = ?
                        WHERE provider_resource_id = ? AND scope_id = ?
                          AND version = ?
                        """,
                        (
                            "probe_pending"
                            if configured
                            else "configuration_missing",
                            now,
                            resource_id,
                            identity.scope_id,
                            before_version,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "configuration_probe_version_conflict",
                            "配置测试请求已更新，请重试",
                        )
                effect_id = new_id()
                operation_id = new_id()
                receipt = {
                    "configurationKind": configuration_kind,
                    "provider": provider,
                    "configurationId": (
                        str(row["configuration_id"]) if row is not None else None
                    ),
                    "hasCredentials": has_credentials,
                    "state": (
                        "registered_not_probed"
                        if configured
                        else "not_configured"
                    ),
                }
                connection.execute(
                    """
                    INSERT INTO external_side_effects (
                        effect_id, scope_id, organization_id, operation_id,
                        provider_resource_id, effect_kind, outcome,
                        receipt_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, 'configuration_probe',
                              ?, ?, ?)
                    """,
                    (
                        effect_id,
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                        resource_id,
                        "pending_worker"
                        if configured
                        else "blocked_missing_configuration",
                        sha256_text(canonical_json(receipt)),
                        now,
                    ),
                )
                result = {
                    "success": False,
                    "message": (
                        "配置已登记；当前严格云未连接服务探测 worker"
                        if configured
                        else "当前有效作用域缺少服务商或已加密凭据"
                    ),
                    "detail": (
                        "provider_probe_worker_not_connected"
                        if configured
                        else "configuration_or_credentials_missing"
                    ),
                    "state": receipt["state"],
                    "effectId": effect_id,
                    "retryable": True,
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=safe_payload,
                    payload_hash=payload_hash,
                    aggregate_id=resource_id,
                    before_version=before_version,
                    after_version=after_version,
                    result=result,
                    aggregate_type="external_provider_resource",
                    audit_summary={
                        "configurationKind": configuration_kind,
                        "provider": provider,
                        "hasCredentials": has_credentials,
                        "outcome": receipt["state"],
                    },
                    outbox_payload={
                        "organizationId": identity.organization_id,
                        "providerResourceId": resource_id,
                        "effectId": effect_id,
                        "configurationKind": configuration_kind,
                        "version": after_version,
                    },
                    operation_id=operation_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
