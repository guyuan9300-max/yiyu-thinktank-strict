"""88-table authority adapter for organization and member platform settings.

Public configuration and ownership live in ``provider_resources``.  Secret
material lives in the server secret store referenced by that row; SQLite keeps
only the reference and fingerprint.  This replaces the frozen
``scoped_configuration_records`` path without adding another table.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.security import payload_fingerprint, redact_payload

from ..repository import CloudRepository, RepositoryError, SessionIdentity


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_" + sha256_text("\x1f".join(parts))[:30]


class PlatformConfigurationRepository:
    """Provider-resource backed replacement for legacy scoped settings."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository
        self.secret_dir = (
            repository.database_path.parent
            / ".runtime-secrets"
            / "provider-resources"
        ).resolve()

    def _assert_identity(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        admin: bool = False,
    ) -> None:
        if identity.cloud_instance_id != self.repository.cloud_instance_id:
            raise RepositoryError(409, "cloud_identity_mismatch", "会话云实例不匹配")
        row = connection.execute(
            """
            SELECT membership.role_key, membership.status
            FROM organization_memberships AS membership
            JOIN authorization_scopes AS scope ON scope.id=membership.scope_id
            JOIN organizations AS organization ON organization.id=scope.organization_id
            WHERE membership.id=? AND membership.principal_id=?
              AND membership.scope_id=? AND scope.organization_id=?
              AND membership.record_kind='membership'
              AND membership.lifecycle_state='active'
              AND organization.record_kind='organization'
              AND organization.lifecycle_state='active'
            """,
            (
                identity.membership_id,
                identity.principal_id,
                identity.scope_id,
                identity.organization_id,
            ),
        ).fetchone()
        if row is None or str(row["status"] or "") != "active":
            raise RepositoryError(403, "membership_inactive", "当前组织成员身份不可用")
        if admin and str(row["role_key"] or "") != "admin":
            raise RepositoryError(403, "admin_required", "仅管理员可以修改组织默认配置")

    @staticmethod
    def _public(value: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(value)
        if redact_payload(normalized) != normalized:
            raise RepositoryError(422, "public_configuration_contains_secret", "公开配置不得包含凭据")
        if len(canonical_json(normalized).encode("utf-8")) > 256 * 1024:
            raise RepositoryError(413, "public_configuration_too_large", "公开配置过大")
        return normalized

    @staticmethod
    def _row_public(row: Any | None) -> dict[str, Any]:
        if row is None or not row["public_config"]:
            return {}
        try:
            parsed = json.loads(str(row["public_config"]))
        except (TypeError, ValueError) as exc:
            raise RepositoryError(500, "provider_configuration_corrupt", "平台配置公开数据损坏") from exc
        if not isinstance(parsed, dict):
            raise RepositoryError(500, "provider_configuration_corrupt", "平台配置公开数据损坏")
        return parsed

    @staticmethod
    def _owner(identity: SessionIdentity, scope_kind: str) -> tuple[str, str | None, str | None]:
        if scope_kind == "personal":
            return "membership", identity.principal_id, identity.membership_id
        return "organization", None, None

    def _resource_id(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        scope_kind: str,
    ) -> str:
        owner_kind, principal_id, membership_id = self._owner(identity, scope_kind)
        return _stable_id(
            "provider_config",
            identity.scope_id,
            owner_kind,
            principal_id or "",
            membership_id or "",
            configuration_kind,
        )

    def _row(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        scope_kind: str,
    ) -> Any | None:
        return connection.execute(
            "SELECT * FROM provider_resources WHERE id=? AND scope_id=? "
            "AND resource_kind=? AND lifecycle_state='active'",
            (
                self._resource_id(
                    identity,
                    configuration_kind=configuration_kind,
                    scope_kind=scope_kind,
                ),
                identity.scope_id,
                configuration_kind,
            ),
        ).fetchone()

    def read_exact(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        scope_kind: str,
        defaults: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            row = self._row(
                connection,
                identity,
                configuration_kind=configuration_kind,
                scope_kind=scope_kind,
            )
        public = {**dict(defaults or {}), **self._row_public(row)}
        return {
            **public,
            "resourceId": str(row["id"]) if row else None,
            "configuredProvider": str(row["provider"] or "") if row else "",
            "updatedAt": str(row["updated_at"] or "") if row else "",
            "version": int(row["version"] or 1) if row else 0,
            "expectedVersion": int(row["version"] or 1) if row else 0,
            "scopeKind": scope_kind,
            "effectiveScopeKind": scope_kind if row else None,
            "hasCredentials": bool(row and row["secret_reference"]),
            "secretFingerprint": str(row["secret_fingerprint"]) if row and row["secret_fingerprint"] else None,
        }

    def read(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        defaults: Mapping[str, Any],
        personal_only: bool = False,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            personal = self._row(
                connection,
                identity,
                configuration_kind=configuration_kind,
                scope_kind="personal",
            )
            organization = None if personal_only else self._row(
                connection,
                identity,
                configuration_kind=configuration_kind,
                scope_kind="organization",
            )
        effective = personal or organization
        public = {**dict(defaults), **self._row_public(effective)}
        effective_scope = "personal" if personal is not None else ("organization" if organization is not None else None)
        return {
            **public,
            "resourceId": str(effective["id"]) if effective else None,
            "configuredProvider": (
                str(effective["provider"] or "") if effective else ""
            ),
            "updatedAt": str(effective["updated_at"] or "") if effective else "",
            "version": int(effective["version"] or 1) if effective else 0,
            "expectedVersion": int(effective["version"] or 1) if effective else 0,
            "effectiveScopeKind": effective_scope,
            "defaultWriteScope": "personal" if personal_only or not identity.is_admin else "organization",
            "scopeVersions": {
                "organization": int(organization["version"] or 1) if organization else 0,
                "personal": int(personal["version"] or 1) if personal else 0,
            },
            "hasCredentials": bool(effective and effective["secret_reference"]),
            "secretFingerprint": (
                str(effective["secret_fingerprint"])
                if effective and effective["secret_fingerprint"]
                else None
            ),
        }

    def _secret_path(self, resource_id: str, version: int) -> Path:
        return (self.secret_dir / f"{resource_id}.v{version}.json").resolve()

    def _write_secret(self, resource_id: str, version: int, value: Mapping[str, Any]) -> tuple[str, str]:
        self.secret_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.secret_dir, 0o700)
        encoded = canonical_json(dict(value))
        encrypted = self.repository.cipher.encrypt(encoded)
        target = self._secret_path(resource_id, version)
        temporary = target.with_suffix(target.suffix + f".{new_id()}.tmp")
        temporary.write_text(
            canonical_json({"schema": "yiyu.provider-secret.v1", "ciphertext": encrypted.ciphertext}),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
        return str(target), encrypted.fingerprint

    def _read_secret_reference(self, reference: Any) -> dict[str, Any] | None:
        raw = str(reference or "").strip()
        if not raw:
            return None
        path = Path(raw).resolve()
        try:
            path.relative_to(self.secret_dir)
        except ValueError as exc:
            raise RepositoryError(500, "provider_secret_reference_invalid", "平台凭据引用越界") from exc
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            decoded = json.loads(self.repository.cipher.decrypt(str(envelope["ciphertext"])))
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RepositoryError(500, "provider_secret_corrupt", "平台凭据密文损坏") from exc
        if not isinstance(decoded, dict):
            raise RepositoryError(500, "provider_secret_corrupt", "平台凭据密文损坏")
        return decoded

    def secret_exact(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        scope_kind: str,
    ) -> dict[str, Any] | None:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            row = self._row(
                connection,
                identity,
                configuration_kind=configuration_kind,
                scope_kind=scope_kind,
            )
        return self._read_secret_reference(row["secret_reference"] if row else None)

    def server_secret(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
    ) -> dict[str, Any] | None:
        personal = self.secret_exact(
            identity,
            configuration_kind=configuration_kind,
            scope_kind="personal",
        )
        return personal or self.secret_exact(
            identity,
            configuration_kind=configuration_kind,
            scope_kind="organization",
        )

    @staticmethod
    def _receipt(
        connection: Any,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT i.payload_hash,i.result_hash,m.receipt FROM idempotency_records i "
            "LEFT JOIN object_manifests m ON m.id=i.result_object_manifest_id "
            "AND m.scope_id=i.scope_id WHERE i.scope_id=? AND i.idempotency_key=?",
            (identity.scope_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"] or "") != payload_hash:
            raise RepositoryError(409, "idempotency_conflict", "操作标识已用于不同内容")
        raw = str(row["receipt"] or "")
        if not raw or sha256_text(raw) != str(row["result_hash"] or ""):
            raise RepositoryError(500, "idempotency_receipt_corrupt", "配置回执损坏")
        result = json.loads(raw)
        return dict(result) if isinstance(result, dict) else {}

    def _record_command(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        resource_id: str,
        configuration_kind: str,
        idempotency_key: str,
        payload_hash: str,
        expected_version: int,
        version: int,
        result: Mapping[str, Any],
        now: str,
        action: str | None = None,
    ) -> None:
        action = action or f"configuration.{configuration_kind}.saved"
        operation_id = _stable_id("op", identity.scope_id, idempotency_key)
        raw = canonical_json(dict(result))
        result_hash = sha256_text(raw)
        manifest_id = _stable_id("manifest", operation_id, result_hash)
        connection.execute(
            "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,lifecycle_state,"
            "receipt,holder_role,holder_instance_id,storage_kind,byte_size,media_type,"
            "availability_state,receipt_hash,created_at,verified_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES (?,?,NULL,?,'active',?,'organization_cloud',?,"
            "'command_receipt',?,'application/json','ready',?,?,?,NULL,'cloud',?)",
            (
                manifest_id, identity.scope_id, result_hash, raw,
                identity.cloud_instance_id, len(raw.encode("utf-8")), result_hash,
                now, now, identity.cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO idempotency_records (id,scope_id,idempotency_key,payload_hash,"
            "result_hash,expires_at,result_object_manifest_id,status,created_at,authority_role,"
            "origin_instance_id) VALUES (?,?,?,?,?,'9999-12-31T23:59:59.999Z',?,"
            "'completed',?,'cloud',?)",
            (
                _stable_id("idem", operation_id), identity.scope_id, idempotency_key,
                payload_hash, result_hash, manifest_id, now, identity.cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,aggregate_type,"
            "aggregate_id,command_type,actor_principal_id,expected_aggregate_version,"
            "device_command_sequence,status,actor_membership_id,payload_object_manifest_id,"
            "payload_hash,submitted_at,settled_at,authority_role,origin_instance_id) "
            "VALUES (?,?,?,?,'provider_resource',?, ?,?,?,NULL,'committed',?,?,?, ?,?,"
            "'cloud',?)",
            (
                _stable_id("command", operation_id), identity.scope_id, operation_id,
                idempotency_key, resource_id, action,
                identity.principal_id, expected_version, identity.membership_id, manifest_id,
                payload_hash, now, now, identity.cloud_instance_id,
            ),
        )
        event_hash = sha256_text(f"{operation_id}|{resource_id}|{version}|{result_hash}")
        connection.execute(
            "INSERT INTO audit_events (id,scope_id,operation_id,actor_id,action,event_hash,"
            "actor_membership_id,target_resource_id,details_object_manifest_id,occurred_at,"
            "origin_instance_id,created_at,integrity_hash,authority_role) VALUES (?,?,?,?,?,"
            "?,?,?,?,?,?,?,?,'cloud')",
            (
                _stable_id("audit", operation_id), identity.scope_id, operation_id,
                identity.principal_id, action,
                event_hash, identity.membership_id, None, manifest_id, now,
                identity.cloud_instance_id, now, event_hash,
            ),
        )
        connection.execute(
            "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,event_type,"
            "status,aggregate_type,aggregate_id,event_object_manifest_id,event_hash,available_at,"
            "published_at,authority_role,origin_instance_id) VALUES (?,?,?,?,?,'pending',"
            "'provider_resource',?,?,?, ?,NULL,'cloud',?)",
            (
                _stable_id("outbox", operation_id), identity.scope_id, operation_id,
                version, action, resource_id,
                manifest_id, event_hash, now, identity.cloud_instance_id,
            ),
        )

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
            raise RepositoryError(422, "configuration_scope_invalid", "配置作用域无效")
        if expected_version < 0:
            raise RepositoryError(422, "expected_version_invalid", "expectedVersion 无效")
        if secret_action not in {"preserve", "replace", "clear"}:
            raise RepositoryError(422, "secret_action_invalid", "凭据更新方式无效")
        if secret_action == "replace" and not secret_bundle:
            raise RepositoryError(422, "secret_bundle_required", "缺少要保存的凭据")
        public = self._public(public_config)
        payload_hash = payload_fingerprint(
            {
                "configurationKind": configuration_kind,
                "scopeKind": scope_kind,
                "provider": provider,
                "publicConfig": public,
                "secretAction": secret_action,
                "secretFingerprint": (
                    sha256_text(canonical_json(dict(secret_bundle or {})))[:16]
                    if secret_action == "replace"
                    else None
                ),
            }
        )
        new_secret_reference: str | None = None
        old_secret_reference: str | None = None
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(connection, identity, admin=scope_kind == "organization")
                replay = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                row = self._row(
                    connection,
                    identity,
                    configuration_kind=configuration_kind,
                    scope_kind=scope_kind,
                )
                current_version = int(row["version"] or 1) if row else 0
                if current_version != expected_version:
                    raise RepositoryError(409, "configuration_version_conflict", "配置已变化，请刷新后重试")
                version = current_version + 1
                resource_id = self._resource_id(
                    identity,
                    configuration_kind=configuration_kind,
                    scope_kind=scope_kind,
                )
                old_secret_reference = str(row["secret_reference"] or "") if row else None
                secret_reference = old_secret_reference
                secret_fingerprint = str(row["secret_fingerprint"] or "") if row else None
                if secret_action == "replace":
                    secret_reference, secret_fingerprint = self._write_secret(
                        resource_id, version, dict(secret_bundle or {})
                    )
                    new_secret_reference = secret_reference
                elif secret_action == "clear":
                    secret_reference = None
                    secret_fingerprint = None
                owner_kind, owner_principal_id, owner_membership_id = self._owner(identity, scope_kind)
                remote_id = str(
                    public.get("appId")
                    or (
                        identity.membership_id
                        if scope_kind == "personal"
                        else identity.organization_id
                    )
                )
                now = utc_now()
                connection.execute(
                    "INSERT INTO provider_resources (id,scope_id,provider,resource_kind,remote_id,"
                    "retention_state,owner_kind,owner_principal_id,owner_membership_id,display_name,"
                    "endpoint,model_name,public_config_schema_version,public_config,secret_reference,"
                    "secret_fingerprint,status,verified_at,version,lifecycle_state,created_at,"
                    "updated_at,deleted_at,authority_role,origin_instance_id) VALUES (?,?,?,?,?,"
                    "'active',?,?,?, ?,NULL,NULL,'yiyu.platform-configuration.v1',?,?,?,"
                    "'configured',NULL,?,'active',?,?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET "
                    "provider=excluded.provider,remote_id=excluded.remote_id,owner_kind=excluded.owner_kind,"
                    "owner_principal_id=excluded.owner_principal_id,owner_membership_id=excluded.owner_membership_id,"
                    "display_name=excluded.display_name,public_config_schema_version=excluded.public_config_schema_version,"
                    "public_config=excluded.public_config,secret_reference=excluded.secret_reference,"
                    "secret_fingerprint=excluded.secret_fingerprint,status='configured',version=excluded.version,"
                    "lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL",
                    (
                        resource_id, identity.scope_id, provider, configuration_kind,
                        remote_id,
                        owner_kind, owner_principal_id, owner_membership_id,
                        configuration_kind, canonical_json(public), secret_reference,
                        secret_fingerprint, version, now, now, identity.cloud_instance_id,
                    ),
                )
                result = {
                    **public,
                    "updatedAt": now,
                    "version": version,
                    "expectedVersion": version,
                    "effectiveScopeKind": scope_kind,
                    "defaultWriteScope": scope_kind,
                    "hasCredentials": bool(secret_reference),
                    "secretFingerprint": secret_fingerprint,
                }
                self._record_command(
                    connection,
                    identity,
                    resource_id=resource_id,
                    configuration_kind=configuration_kind,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    expected_version=expected_version,
                    version=version,
                    result=result,
                    now=now,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                if new_secret_reference:
                    Path(new_secret_reference).unlink(missing_ok=True)
                raise
        if old_secret_reference and old_secret_reference != new_secret_reference and secret_action in {"replace", "clear"}:
            Path(old_secret_reference).unlink(missing_ok=True)
        return result

    def request_probe(
        self,
        identity: SessionIdentity,
        *,
        configuration_kind: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Register a bounded probe request without inventing provider success."""

        current = self.read(
            identity,
            configuration_kind=configuration_kind,
            defaults={},
        )
        provider = str(
            current.get("provider")
            or current.get("configuredProvider")
            or ""
        )
        configured = bool(provider and current.get("hasCredentials"))
        result = {
            "configurationKind": configuration_kind,
            "provider": provider,
            "configurationId": current.get("resourceId"),
            "hasCredentials": bool(current.get("hasCredentials")),
            "state": "registered_not_probed" if configured else "not_configured",
            "retryable": configured,
            "message": (
                "配置已登记；外部连通性探测尚未接通"
                if configured
                else "尚未配置服务商或凭据"
            ),
        }
        payload_hash = payload_fingerprint(
            {
                "configurationKind": configuration_kind,
                "provider": provider,
                "configurationVersion": int(current.get("version") or 0),
            }
        )
        resource_id = str(
            current.get("resourceId")
            or self._resource_id(
                identity,
                configuration_kind=configuration_kind,
                scope_kind="personal",
            )
        )
        now = utc_now()
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(connection, identity)
                replay = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                self._record_command(
                    connection,
                    identity,
                    resource_id=resource_id,
                    configuration_kind=configuration_kind,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    expected_version=int(current.get("version") or 0),
                    version=int(current.get("version") or 0),
                    result=result,
                    now=now,
                    action=f"configuration.{configuration_kind}.probe_requested",
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return result


__all__ = ["PlatformConfigurationRepository"]
