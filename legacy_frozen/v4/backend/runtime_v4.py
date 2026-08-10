from __future__ import annotations

import json
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import urlparse

import httpx

from strict_common.contracts import (
    CLOUD_CONTRACT,
    CONNECTED_CAPABILITIES,
    capability_registry,
)
from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.schema import (
    audit_event_hash,
    database_identity,
    initialize_database,
    runtime_connection,
)
from strict_common.security import decode_secret_bundle, encode_secret_bundle

from .cloud_client import (
    CloudClient,
    CloudClientError,
    CloudClientPool,
    normalize_cloud_url,
)
from .project_knowledge import (
    LOCAL_SUMMARY_MEDIA_TYPE,
    managed_source_is_available,
    project_storage_prefix,
    read_summary_document,
)
from .secret_store import SecretStore, SecretStoreError, secret_fingerprint


def _local_ai_url(value: str) -> bool:
    try:
        host = (urlparse(value).hostname or "").casefold()
    except ValueError:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


_AI_ROUTING_PROFILE_KEYS = (
    "online_primary",
    "local_text_deep",
    "local_vision_ocr",
    "local_fast",
)


class LocalRuntimeError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class WorkspaceContext:
    sandbox_id: str
    cloud_instance_id: str
    organization_id: str
    cloud_api_url: str
    principal_id: str
    membership_id: str
    access_token: str
    refresh_token: str
    access_expires_at: str | None
    refresh_expires_at: str | None


@dataclass(frozen=True)
class PinnedSandboxContext:
    """Identity captured before a synchronous UI request enters the worker pool."""

    sandbox_id: str
    sandbox_kind: str
    cloud_instance_id: str | None
    organization_id: str | None
    workspace_context: WorkspaceContext | None = None


CloudFactory = Callable[[str], CloudClient]


class WorkspaceRuntime:
    def __init__(
        self,
        database_path: Path,
        secret_store: SecretStore,
        *,
        cloud_factory: CloudFactory | None = None,
    ):
        self.database_path = database_path.resolve()
        self.identity = initialize_database(self.database_path, "local")
        self.secret_store = secret_store
        self.cloud_factory = cloud_factory or CloudClientPool()
        self._state_lock = threading.RLock()
        self._workspace_context_local = threading.local()
        self._session_locks: dict[str, threading.RLock] = {}
        self._storage_locks: dict[tuple[str, str], threading.RLock] = {}
        self._business_sync_guard = threading.Lock()
        self._business_sync_inflight: dict[str, Future[dict[str, Any]]] = {}
        self._transition_id: str | None = None
        self._ensure_device_and_local_draft()

    def _connection(self):
        return runtime_connection(self.database_path, "local")

    def close(self) -> None:
        close = getattr(self.cloud_factory, "close", None)
        if callable(close):
            close()

    def local_storage_object_put(
        self,
        *,
        sandbox_id: str | None = None,
        object_id: str,
        storage_key: str,
        content_hash: str,
        media_type: str,
        byte_size: int,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        """Register one managed object in an explicitly captured sandbox."""
        if sandbox_id is None:
            sandbox_id = self._current_context(require_ready=True).sandbox_id
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT version
                FROM storage_objects
                WHERE sandbox_id = ? AND object_id = ?
                """,
                (sandbox_id, object_id),
            ).fetchone()
            before_version = int(existing["version"]) if existing is not None else 0
            receipt_version = (
                expected_version
                if expected_version is not None
                else before_version
            )
            idempotency_key = (
                f"storage-put:{object_id}:{receipt_version}:{content_hash}"
            )
            identity = self._local_command_identity(
                connection,
                sandbox_id=sandbox_id,
            )
            replay = connection.execute(
                """
                SELECT result_json
                FROM command_idempotency
                WHERE sandbox_id = ? AND actor_principal_id = ?
                  AND command_type = 'local.storage_object.put'
                  AND idempotency_key = ?
                """,
                (
                    sandbox_id,
                    identity["principalId"],
                    idempotency_key,
                ),
            ).fetchone()
            if replay is not None:
                connection.rollback()
                value = json.loads(str(replay["result_json"]))
                if not isinstance(value, dict):
                    raise LocalRuntimeError(
                        500,
                        "local_storage_receipt_corrupt",
                        "本机对象写入回执损坏",
                    )
                return value
            if existing is None:
                if expected_version not in (None, 0):
                    raise LocalRuntimeError(
                        409,
                        "local_storage_version_conflict",
                        "本机对象版本已变化，请刷新后重试",
                    )
                version = 1
                connection.execute(
                    """
                    INSERT INTO storage_objects (
                        object_id, sandbox_id, storage_key, content_hash,
                        media_type, byte_size, lifecycle_state, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
                    """,
                    (
                        object_id,
                        sandbox_id,
                        storage_key,
                        content_hash,
                        media_type,
                        byte_size,
                        now,
                        now,
                    ),
                )
            else:
                current_version = int(existing["version"])
                if (
                    expected_version is not None
                    and expected_version != current_version
                ):
                    raise LocalRuntimeError(
                        409,
                        "local_storage_version_conflict",
                        "本机对象版本已变化，请刷新后重试",
                    )
                version = current_version + 1
                changed = connection.execute(
                    """
                    UPDATE storage_objects
                    SET storage_key = ?, content_hash = ?, media_type = ?,
                        byte_size = ?, lifecycle_state = 'active',
                        version = ?, updated_at = ?
                    WHERE sandbox_id = ? AND object_id = ? AND version = ?
                    """,
                    (
                        storage_key,
                        content_hash,
                        media_type,
                        byte_size,
                        version,
                        now,
                        sandbox_id,
                        object_id,
                        current_version,
                    ),
                )
                if changed.rowcount != 1:
                    raise LocalRuntimeError(
                        409,
                        "local_storage_version_conflict",
                        "本机对象版本已变化，请刷新后重试",
                    )
            result = {
                "objectId": object_id,
                "sandboxId": sandbox_id,
                "storageKey": storage_key,
                "contentHash": content_hash,
                "mediaType": media_type,
                "byteSize": byte_size,
                "lifecycleState": "active",
                "version": version,
                "updatedAt": now,
            }
            self._record_local_storage_command(
                connection,
                identity=identity,
                sandbox_id=sandbox_id,
                object_id=object_id,
                idempotency_key=idempotency_key,
                before_version=before_version or None,
                after_version=version,
                payload={
                    "contentHash": content_hash,
                    "mediaType": media_type,
                    "byteSize": byte_size,
                    "storageKeyHash": sha256_text(storage_key),
                },
                result=result,
            )
            connection.commit()
        return result

    @staticmethod
    def _local_command_identity(
        connection,
        *,
        sandbox_id: str,
    ) -> dict[str, str | None]:
        row = connection.execute(
            """
            SELECT s.principal_id, b.cloud_instance_id, b.organization_id
            FROM workspace_session_snapshots AS s
            JOIN workspace_bindings AS b ON b.sandbox_id = s.sandbox_id
            WHERE s.sandbox_id = ?
            """,
            (sandbox_id,),
        ).fetchone()
        if row is None:
            return {
                "principalId": "local-device",
                "scopeId": sandbox_id,
                "cloudInstanceId": None,
                "organizationId": None,
            }
        return {
            "principalId": str(row["principal_id"]),
            "scopeId": str(row["organization_id"]),
            "cloudInstanceId": str(row["cloud_instance_id"]),
            "organizationId": str(row["organization_id"]),
        }

    def _record_local_storage_command(
        self,
        connection,
        *,
        identity: Mapping[str, str | None],
        sandbox_id: str,
        object_id: str,
        idempotency_key: str,
        before_version: int | None,
        after_version: int,
        payload: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> None:
        now = utc_now()
        command_id = new_id()
        operation_id = new_id()
        payload_json = canonical_json(dict(payload))
        payload_hash = sha256_text(payload_json)
        result_json = canonical_json(dict(result))
        connection.execute(
            """
            INSERT INTO command_envelopes (
                command_id, sandbox_id, scope_id, cloud_instance_id,
                organization_id, operation_id, idempotency_key,
                aggregate_type, aggregate_id, command_type,
                actor_principal_id, expected_version, payload_json,
                payload_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'storage_object', ?,
                      'local.storage_object.put', ?, ?, ?, ?, 'confirmed', ?, ?)
            """,
            (
                command_id,
                sandbox_id,
                identity["scopeId"],
                identity["cloudInstanceId"],
                identity["organizationId"],
                operation_id,
                idempotency_key,
                object_id,
                identity["principalId"],
                before_version,
                payload_json,
                payload_hash,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO command_idempotency (
                record_id, sandbox_id, actor_principal_id, command_type,
                idempotency_key, payload_hash, result_hash, result_json,
                expires_at, created_at
            ) VALUES (?, ?, ?, 'local.storage_object.put', ?, ?, ?, ?,
                      '9999-12-31T23:59:59.999Z', ?)
            """,
            (
                new_id(),
                sandbox_id,
                identity["principalId"],
                idempotency_key,
                payload_hash,
                sha256_text(result_json),
                result_json,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO delivery_outbox (
                event_id, sandbox_id, operation_id, aggregate_type,
                aggregate_id, aggregate_version, event_type, payload_json,
                payload_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'storage_object', ?, ?,
                      'local.storage_object.updated', ?, ?, 'delivered', ?, ?)
            """,
            (
                new_id(),
                sandbox_id,
                operation_id,
                object_id,
                after_version,
                payload_json,
                payload_hash,
                now,
                now,
            ),
        )
        self._insert_audit(
            connection,
            sandbox_id=sandbox_id,
            action="local.storage_object.updated",
            resource_type="storage_object",
            resource_id=object_id,
            actor_id=str(identity["principalId"]),
            summary={
                "contentHash": payload["contentHash"],
                "mediaType": payload["mediaType"],
                "byteSize": payload["byteSize"],
            },
            operation_id=operation_id,
            before_version=before_version,
            after_version=after_version,
        )

    def local_storage_object_get(
        self,
        *,
        sandbox_id: str | None = None,
        object_id: str,
        storage_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Read local object metadata without crossing sandbox boundaries."""
        if sandbox_id is None:
            sandbox_id = self._current_context(require_ready=True).sandbox_id
        with self._connection() as connection:
            if storage_key is None:
                row = connection.execute(
                    """
                    SELECT object_id, sandbox_id, storage_key, content_hash,
                           media_type, byte_size, lifecycle_state, version,
                           created_at, updated_at
                    FROM storage_objects
                    WHERE sandbox_id = ? AND object_id = ?
                    """,
                    (sandbox_id, object_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT object_id, sandbox_id, storage_key, content_hash,
                           media_type, byte_size, lifecycle_state, version,
                           created_at, updated_at
                    FROM storage_objects
                    WHERE sandbox_id = ? AND object_id = ?
                      AND storage_key = ?
                    """,
                    (sandbox_id, object_id, storage_key),
                ).fetchone()
        return dict(row) if row is not None else None

    @contextmanager
    def local_storage_object_lock(
        self,
        *,
        sandbox_id: str,
        object_id: str,
    ) -> Iterator[None]:
        """Serialize filesystem and metadata mutation for one local object."""
        key = (sandbox_id, object_id)
        with self._state_lock:
            lock = self._storage_locks.setdefault(key, threading.RLock())
        with lock:
            yield

    def local_storage_objects_by_media_type(
        self,
        *,
        media_type: str,
    ) -> list[dict[str, Any]]:
        """List active object metadata for one media type in this sandbox."""
        context = self._current_context(require_ready=True)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT object_id, sandbox_id, storage_key, content_hash,
                       media_type, byte_size, lifecycle_state, version,
                       created_at, updated_at
                FROM storage_objects
                WHERE sandbox_id = ? AND media_type = ?
                  AND lifecycle_state = 'active'
                ORDER BY updated_at DESC, object_id
                """,
                (context.sandbox_id, media_type),
            ).fetchall()
        return [dict(row) for row in rows]

    def local_storage_object_set_lifecycle(
        self,
        *,
        object_id: str,
        lifecycle_state: str,
    ) -> dict[str, Any]:
        """Change lifecycle for one object in the current local sandbox."""
        if lifecycle_state not in {"active", "deleted"}:
            raise LocalRuntimeError(
                422,
                "local_storage_lifecycle_invalid",
                "本机对象生命周期无效",
            )
        context = self._current_context(require_ready=True)
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE storage_objects
                SET lifecycle_state = ?, version = version + 1,
                    updated_at = ?
                WHERE sandbox_id = ? AND object_id = ?
                """,
                (
                    lifecycle_state,
                    now,
                    context.sandbox_id,
                    object_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LocalRuntimeError(
                    404,
                    "local_storage_object_missing",
                    "本机对象不存在",
                )
            row = connection.execute(
                """
                SELECT version
                FROM storage_objects
                WHERE sandbox_id = ? AND object_id = ?
                """,
                (context.sandbox_id, object_id),
            ).fetchone()
            connection.commit()
        return {
            "objectId": object_id,
            "sandboxId": context.sandbox_id,
            "lifecycleState": lifecycle_state,
            "version": int(row["version"]),
            "updatedAt": now,
        }

    @staticmethod
    def _session_ref(sandbox_id: str) -> str:
        return f"workspace-session:{sandbox_id}"

    @staticmethod
    def _ai_ref(sandbox_id: str) -> str:
        return f"organization-ai:{sandbox_id}"

    @staticmethod
    def _ai_profile_ref(sandbox_id: str, profile_key: str) -> str:
        return f"organization-ai-profile:{sandbox_id}:{profile_key}"

    def _ensure_device_and_local_draft(self) -> None:
        now = utc_now()
        with self._connection() as connection:
            device = connection.execute(
                "SELECT device_id FROM device_registry WHERE status = 'active' LIMIT 1"
            ).fetchone()
            if device is None:
                device_id = new_id()
                connection.execute(
                    """
                    INSERT INTO device_registry (
                        device_id, device_epoch, created_at, status, version
                    ) VALUES (?, 1, ?, 'active', 1)
                    """,
                    (device_id, now),
                )
            else:
                device_id = str(device["device_id"])
            local = connection.execute(
                """
                SELECT sandbox_id FROM workspace_sandboxes
                WHERE sandbox_kind = 'local_draft'
                LIMIT 1
                """
            ).fetchone()
            active = connection.execute(
                "SELECT sandbox_id FROM workspace_sandboxes WHERE is_active = 1"
            ).fetchone()
            if local is None:
                sandbox_id = new_id()
                connection.execute(
                    """
                    INSERT INTO workspace_sandboxes (
                        sandbox_id, device_id, sandbox_kind, replica_epoch,
                        runtime_status, display_name, is_active, version,
                        created_at, updated_at
                    ) VALUES (?, ?, 'local_draft', 1, 'local_draft',
                              '本机未登录隔离区', ?, 1, ?, ?)
                    """,
                    (sandbox_id, device_id, 1 if active is None else 0, now, now),
                )
            else:
                connection.execute(
                    """
                    UPDATE workspace_sandboxes
                    SET display_name = '本机未登录隔离区', updated_at = ?
                    WHERE sandbox_id = ? AND display_name = '未连接组织'
                    """,
                    (now, str(local["sandbox_id"])),
                )
            connection.commit()

    def _start_transition(
        self,
        *,
        target_sandbox_id: str | None = None,
        runtime_status: str = "verifying",
    ) -> str:
        transition_id = new_id()
        with self._state_lock:
            self._transition_id = transition_id
            if target_sandbox_id:
                with self._connection() as connection:
                    target = connection.execute(
                        """
                        SELECT sandbox_kind FROM workspace_sandboxes
                        WHERE sandbox_id = ?
                        """,
                        (target_sandbox_id,),
                    ).fetchone()
                    if target is None:
                        raise LocalRuntimeError(
                            404,
                            "workspace_missing",
                            "工作空间不存在",
                        )
                    connection.execute(
                        """
                        UPDATE workspace_sandboxes
                        SET runtime_status = ?, version = version + 1,
                            updated_at = ?
                        WHERE sandbox_id = ?
                        """,
                        (runtime_status, utc_now(), target_sandbox_id),
                    )
                    connection.commit()
        return transition_id

    def _assert_transition(self, transition_id: str) -> None:
        with self._state_lock:
            if self._transition_id != transition_id:
                raise LocalRuntimeError(
                    409,
                    "transition_superseded",
                    "较新的工作空间操作已开始，本次结果已丢弃",
                )

    def _finish_transition(self, transition_id: str) -> None:
        with self._state_lock:
            if self._transition_id == transition_id:
                self._transition_id = None

    def _validate_handshake(self, handshake: dict[str, Any]) -> None:
        required_capabilities = set(CONNECTED_CAPABILITIES)
        actual_capabilities = {
            str(item) for item in handshake.get("capabilities", [])
        }
        mismatches: list[str] = []
        if handshake.get("apiVersion") != "v2":
            mismatches.append("apiVersion")
        if handshake.get("schemaFamily") != CLOUD_CONTRACT.schema_family:
            mismatches.append("schemaFamily")
        if str(handshake.get("contractVersion")) != CLOUD_CONTRACT.contract_version:
            mismatches.append("contractVersion")
        if handshake.get("schemaManifestSha256") != CLOUD_CONTRACT.manifest_hash:
            mismatches.append("schemaManifestSha256")
        if not str(handshake.get("databaseGenerationId") or ""):
            mismatches.append("databaseGenerationId")
        if not str(handshake.get("cloudInstanceId") or ""):
            mismatches.append("cloudInstanceId")
        if not required_capabilities.issubset(actual_capabilities):
            mismatches.append("capabilities")
        if mismatches:
            raise LocalRuntimeError(
                409,
                "schema_incompatible",
                "组织云不是当前严格新版合同：" + "、".join(mismatches),
            )

    def _validate_session_payload(
        self,
        handshake: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        snapshot = payload.get("sessionSnapshot")
        if not isinstance(snapshot, dict):
            raise LocalRuntimeError(502, "session_invalid", "组织云缺少身份快照")
        organization = snapshot.get("organization")
        principal = snapshot.get("principal")
        membership = snapshot.get("membership")
        if not all(isinstance(item, dict) for item in (organization, principal, membership)):
            raise LocalRuntimeError(502, "session_invalid", "组织身份快照不完整")
        checks = {
            "cloudInstanceId": (
                str(payload.get("cloudInstanceId") or ""),
                str(handshake.get("cloudInstanceId") or ""),
            ),
            "organizationId": (
                str(payload.get("organizationId") or ""),
                str(organization.get("organizationId") or ""),
            ),
            "principalId": (
                str(payload.get("principalId") or ""),
                str(principal.get("principalId") or ""),
            ),
            "membershipId": (
                str(payload.get("membershipId") or ""),
                str(membership.get("membershipId") or ""),
            ),
        }
        bad = [name for name, values in checks.items() if not values[0] or values[0] != values[1]]
        if bad:
            raise LocalRuntimeError(
                409,
                "identity_mismatch",
                "组织云身份不一致：" + "、".join(bad),
            )
        for secret_field in ("accessToken", "refreshToken"):
            if not str(payload.get(secret_field) or ""):
                raise LocalRuntimeError(502, "session_invalid", "组织云登录凭据不完整")

    def _current_ai_runtime(
        self,
        connection,
        sandbox_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT session_snapshot_json
            FROM workspace_session_snapshots
            WHERE sandbox_id = ?
            """,
            (sandbox_id,),
        ).fetchone()
        if row is None:
            return {
                "state": "not_ready",
                "provider": "",
                "modelName": "",
                "keyFingerprint": "",
                "syncedAt": None,
                "message": "尚未同步组织 AI 配置",
            }
        try:
            document = json.loads(str(row["session_snapshot_json"]))
        except (TypeError, ValueError):
            return {"state": "error", "message": "本机身份快照损坏"}
        runtime = document.get("aiRuntime")
        return runtime if isinstance(runtime, dict) else {
            "state": "not_ready",
            "provider": "",
            "modelName": "",
            "keyFingerprint": "",
            "syncedAt": None,
            "message": "尚未同步组织 AI 配置",
        }

    def _write_snapshot_document(
        self,
        connection,
        *,
        sandbox_id: str,
        cloud_snapshot: dict[str, Any],
        secret_ref: str,
        credential_fingerprint: str,
        ai_runtime: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        existing_ai = self._current_ai_runtime(connection, sandbox_id)
        document = {
            "cloudSnapshot": cloud_snapshot,
            "aiRuntime": ai_runtime if ai_runtime is not None else existing_ai,
        }
        principal = cloud_snapshot["principal"]
        membership = cloud_snapshot["membership"]
        connection.execute(
            """
            INSERT INTO workspace_session_snapshots (
                session_snapshot_id, sandbox_id, principal_id, membership_id,
                secret_ref, credential_fingerprint, session_snapshot_json,
                verified_at, status, version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?)
            ON CONFLICT(sandbox_id) DO UPDATE SET
                principal_id = excluded.principal_id,
                membership_id = excluded.membership_id,
                secret_ref = excluded.secret_ref,
                credential_fingerprint = excluded.credential_fingerprint,
                session_snapshot_json = excluded.session_snapshot_json,
                verified_at = excluded.verified_at,
                status = 'active',
                version = workspace_session_snapshots.version + 1,
                updated_at = excluded.updated_at
            """,
            (
                new_id(),
                sandbox_id,
                principal["principalId"],
                membership["membershipId"],
                secret_ref,
                credential_fingerprint,
                canonical_json(document),
                now,
                now,
            ),
        )

    def _replace_projections(
        self,
        connection,
        *,
        sandbox_id: str,
        snapshot: dict[str, Any],
    ) -> None:
        now = utc_now()
        organization = snapshot["organization"]
        members = snapshot.get("members") or []
        departments = snapshot.get("departments") or []
        connection.execute(
            "DELETE FROM projection_departments WHERE sandbox_id = ?",
            (sandbox_id,),
        )
        connection.execute(
            "DELETE FROM projection_memberships WHERE sandbox_id = ?",
            (sandbox_id,),
        )
        connection.execute(
            "DELETE FROM projection_principals WHERE sandbox_id = ?",
            (sandbox_id,),
        )
        connection.execute(
            "DELETE FROM projection_organizations WHERE sandbox_id = ?",
            (sandbox_id,),
        )
        connection.execute(
            """
            INSERT INTO projection_organizations (
                sandbox_id, organization_id, name, metadata_json,
                source_version, projection_state, refreshed_at
            ) VALUES (?, ?, ?, ?, ?, 'fresh', ?)
            """,
            (
                sandbox_id,
                organization["organizationId"],
                organization["name"],
                canonical_json(organization),
                int(organization.get("version") or 1),
                now,
            ),
        )
        current_principal = snapshot["principal"]
        principal_rows = {
            str(member["principalId"]): {
                "principalId": member["principalId"],
                "displayName": member["displayName"],
            }
            for member in members
        }
        principal_rows[str(current_principal["principalId"])] = current_principal
        for principal in principal_rows.values():
            connection.execute(
                """
                INSERT INTO projection_principals (
                    sandbox_id, principal_id, display_name, metadata_json,
                    source_version, projection_state, refreshed_at
                ) VALUES (?, ?, ?, ?, 1, 'fresh', ?)
                """,
                (
                    sandbox_id,
                    principal["principalId"],
                    principal["displayName"],
                    canonical_json(principal),
                    now,
                ),
            )
        for membership in members:
            connection.execute(
                """
                INSERT INTO projection_memberships (
                    sandbox_id, membership_id, principal_id, organization_id,
                    status, metadata_json, source_version, projection_state,
                    refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'fresh', ?)
                """,
                (
                    sandbox_id,
                    membership["membershipId"],
                    membership["principalId"],
                    organization["organizationId"],
                    membership["status"],
                    canonical_json(membership),
                    int(membership.get("version") or 1),
                    now,
                ),
            )
        for department in departments:
            connection.execute(
                """
                INSERT INTO projection_departments (
                    sandbox_id, department_id, organization_id, name,
                    metadata_json, source_version, projection_state, refreshed_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'fresh', ?)
                """,
                (
                    sandbox_id,
                    department["departmentId"],
                    organization["organizationId"],
                    department["name"],
                    canonical_json(department),
                    int(department.get("version") or 1),
                    now,
                ),
            )

    def _replace_business_projections(
        self,
        connection,
        *,
        context: WorkspaceContext,
        snapshot: dict[str, Any],
    ) -> None:
        if str(snapshot.get("organizationId") or "") != context.organization_id:
            raise LocalRuntimeError(
                409,
                "business_snapshot_identity_mismatch",
                "业务快照与当前工作空间不属于同一组织",
            )
        now = utc_now()
        mappings = (
            ("projects", "project", "projectId", "lifecycleState"),
            ("tasks", "task", "taskId", "lifecycleState"),
            ("eventLines", "event_line", "eventLineId", "lifecycleState"),
            (
                "documents",
                "knowledge_document",
                "documentId",
                "lifecycleState",
            ),
            (
                "reports",
                "narrative_output",
                "narrativeOutputId",
                "lifecycleState",
            ),
            ("plans", "organization_plan", "planId", "status"),
            (
                "weeklyReviews",
                "weekly_review",
                "weeklyReviewId",
                "lifecycleState",
            ),
            ("intelligence", "intelligence", "intelligenceId", "status"),
            (
                "growthSignals",
                "growth_signal",
                "growthSignalId",
                "lifecycleState",
            ),
            (
                "growthEvidence",
                "growth_evidence",
                "growthEvidenceId",
                "validationState",
            ),
            (
                "experienceQuotes",
                "experience_quote",
                "experienceQuoteId",
                "lifecycleState",
            ),
            ("aiAnswers", "ai_answer", "answerId", "lifecycleState"),
            ("favorites", "favorite", "favoriteId", "lifecycleState"),
        )
        connection.execute(
            "DELETE FROM projection_business_objects WHERE sandbox_id = ?",
            (context.sandbox_id,),
        )
        for source_key, object_kind, id_key, lifecycle_key in mappings:
            for item in snapshot.get(source_key) or []:
                if not isinstance(item, dict) or not str(item.get(id_key) or ""):
                    raise LocalRuntimeError(
                        502,
                        "business_snapshot_invalid",
                        f"组织云业务快照缺少 {source_key} 稳定 ID",
                    )
                connection.execute(
                    """
                    INSERT INTO projection_business_objects (
                        sandbox_id, object_kind, object_id, organization_id,
                        project_id, source_version, lifecycle_state,
                        payload_json, projection_state, refreshed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                    """,
                    (
                        context.sandbox_id,
                        object_kind,
                        str(item[id_key]),
                        context.organization_id,
                        item.get("projectId"),
                        max(1, int(item.get("version") or 1)),
                        str(item.get(lifecycle_key) or "active"),
                        canonical_json(item),
                        now,
                    ),
                )

    def _sync_business_for_context(
        self,
        context: WorkspaceContext,
    ) -> dict[str, Any]:
        with self._business_sync_guard:
            future = self._business_sync_inflight.get(context.sandbox_id)
            owns_sync = future is None
            if future is None:
                future = Future()
                self._business_sync_inflight[context.sandbox_id] = future
        if not owns_sync:
            return future.result()

        try:
            snapshot, current = self._authenticated_cloud_call(
                context,
                lambda client, session: client.business_snapshot(
                    session.access_token
                ),
            )
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._replace_business_projections(
                        connection,
                        context=current,
                        snapshot=snapshot,
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            future.set_result(snapshot)
            return snapshot
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            with self._business_sync_guard:
                if (
                    self._business_sync_inflight.get(context.sandbox_id)
                    is future
                ):
                    del self._business_sync_inflight[context.sandbox_id]

    def _insert_audit(
        self,
        connection,
        *,
        sandbox_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        actor_id: str,
        summary: dict[str, Any],
        operation_id: str | None = None,
        before_version: int | None = None,
        after_version: int | None = None,
    ) -> None:
        now = utc_now()
        operation_id = operation_id or new_id()
        previous = connection.execute(
            """
            SELECT event_hash FROM audit_events
            WHERE sandbox_id = ?
            ORDER BY created_at DESC, audit_id DESC
            LIMIT 1
            """,
            (sandbox_id,),
        ).fetchone()
        previous_hash = str(previous["event_hash"]) if previous else None
        summary_json = canonical_json(summary)
        event_hash = audit_event_hash(
            previous_event_hash=previous_hash,
            operation_id=operation_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            summary_json=summary_json,
            created_at=now,
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                audit_id, sandbox_id, operation_id, actor_id, action,
                resource_type, resource_id, before_version, after_version,
                summary_json, previous_event_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                sandbox_id,
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
                now,
            ),
        )

    def _apply_session(
        self,
        *,
        transition_id: str,
        cloud_api_url: str,
        handshake: dict[str, Any],
        payload: dict[str, Any],
    ) -> str:
        self._assert_transition(transition_id)
        self._validate_handshake(handshake)
        self._validate_session_payload(handshake, payload)
        cloud_instance_id = str(payload["cloudInstanceId"])
        organization_id = str(payload["organizationId"])
        snapshot = payload["sessionSnapshot"]
        display_name = str(snapshot["organization"]["name"])
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT b.sandbox_id
                FROM workspace_bindings AS b
                WHERE b.cloud_instance_id = ? AND b.organization_id = ?
                """,
                (cloud_instance_id, organization_id),
            ).fetchone()
        sandbox_id = str(existing["sandbox_id"]) if existing else new_id()
        secret_ref = self._session_ref(sandbox_id)
        previous_secret = self.secret_store.get(secret_ref)
        previous_document: dict[str, Any] = {}
        if previous_secret:
            try:
                previous_document = decode_secret_bundle(previous_secret)
            except Exception:
                previous_document = {}
        secret_document = {
            "cloudApiUrl": cloud_api_url,
            "cloudInstanceId": cloud_instance_id,
            "organizationId": organization_id,
            "principalId": payload["principalId"],
            "membershipId": payload["membershipId"],
            "accessToken": payload["accessToken"],
            "refreshToken": payload["refreshToken"],
            "expiresAt": payload.get("expiresAt", previous_document.get("expiresAt")),
            "refreshExpiresAt": payload.get(
                "refreshExpiresAt",
                previous_document.get("refreshExpiresAt"),
            ),
        }
        encoded_secret = encode_secret_bundle(secret_document)
        secret_changed = previous_secret != encoded_secret
        if secret_changed:
            self.secret_store.set(secret_ref, encoded_secret)
        credential_hash = secret_fingerprint(encoded_secret)
        now = utc_now()
        try:
            self._assert_transition(transition_id)
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    device = connection.execute(
                        "SELECT device_id FROM device_registry WHERE status = 'active' LIMIT 1"
                    ).fetchone()
                    connection.execute(
                        "UPDATE workspace_sandboxes SET is_active = 0 WHERE is_active = 1"
                    )
                    if existing is None:
                        connection.execute(
                            """
                            INSERT INTO workspace_sandboxes (
                                sandbox_id, device_id, sandbox_kind, replica_epoch,
                                runtime_status, display_name, is_active, version,
                                created_at, updated_at
                            ) VALUES (?, ?, 'organization', 1, 'ready', ?, 1, 1, ?, ?)
                            """,
                            (sandbox_id, device["device_id"], display_name, now, now),
                        )
                        connection.execute(
                            """
                            INSERT INTO workspace_bindings (
                                binding_id, sandbox_id, cloud_instance_id,
                                organization_id, cloud_api_url,
                                database_generation_id, contract_version,
                                cloud_manifest_hash, identity_state, version,
                                verified_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'verified', 1, ?, ?)
                            """,
                            (
                                new_id(),
                                sandbox_id,
                                cloud_instance_id,
                                organization_id,
                                cloud_api_url,
                                handshake["databaseGenerationId"],
                                str(handshake["contractVersion"]),
                                handshake["schemaManifestSha256"],
                                now,
                                now,
                            ),
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE workspace_sandboxes
                            SET runtime_status = 'ready', display_name = ?,
                                is_active = 1, version = version + 1, updated_at = ?
                            WHERE sandbox_id = ?
                            """,
                            (display_name, now, sandbox_id),
                        )
                        connection.execute(
                            """
                            UPDATE workspace_bindings
                            SET cloud_api_url = ?, database_generation_id = ?,
                                contract_version = ?, cloud_manifest_hash = ?,
                                identity_state = 'verified', verified_at = ?,
                                version = version + 1, updated_at = ?
                            WHERE sandbox_id = ?
                            """,
                            (
                                cloud_api_url,
                                handshake["databaseGenerationId"],
                                str(handshake["contractVersion"]),
                                handshake["schemaManifestSha256"],
                                now,
                                now,
                                sandbox_id,
                            ),
                        )
                    connection.execute(
                        """
                        UPDATE workspace_sandboxes
                        SET is_active = CASE WHEN sandbox_id = ? THEN 1 ELSE 0 END,
                            updated_at = ?
                        """,
                        (sandbox_id, now),
                    )
                    self._write_snapshot_document(
                        connection,
                        sandbox_id=sandbox_id,
                        cloud_snapshot=snapshot,
                        secret_ref=secret_ref,
                        credential_fingerprint=credential_hash,
                    )
                    self._replace_projections(
                        connection,
                        sandbox_id=sandbox_id,
                        snapshot=snapshot,
                    )
                    self._insert_audit(
                        connection,
                        sandbox_id=sandbox_id,
                        action="workspace.session_verified",
                        resource_type="workspace",
                        resource_id=sandbox_id,
                        actor_id=str(payload["principalId"]),
                        summary={
                            "cloudInstanceId": cloud_instance_id,
                            "organizationId": organization_id,
                        },
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except Exception:
            if secret_changed:
                if previous_secret is None:
                    self.secret_store.delete(secret_ref)
                else:
                    self.secret_store.set(secret_ref, previous_secret)
            raise
        return sandbox_id

    def _secret_context(self, sandbox_id: str) -> WorkspaceContext:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT b.cloud_instance_id, b.organization_id, b.cloud_api_url,
                       s.principal_id, s.membership_id, s.secret_ref
                FROM workspace_bindings AS b
                JOIN workspace_session_snapshots AS s
                  ON s.sandbox_id = b.sandbox_id
                WHERE b.sandbox_id = ?
                """,
                (sandbox_id,),
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(401, "workspace_session_missing", "该组织需要重新登录")
        encoded = self.secret_store.get(str(row["secret_ref"]))
        if not encoded:
            raise LocalRuntimeError(401, "workspace_secret_missing", "该设备缺少组织登录凭据")
        try:
            secret = decode_secret_bundle(encoded)
        except Exception as exc:
            raise LocalRuntimeError(409, "workspace_secret_invalid", "组织登录凭据损坏") from exc
        fixed = {
            "cloudInstanceId": str(row["cloud_instance_id"]),
            "organizationId": str(row["organization_id"]),
            "principalId": str(row["principal_id"]),
            "membershipId": str(row["membership_id"]),
        }
        bad = [key for key, value in fixed.items() if str(secret.get(key) or "") != value]
        if bad:
            raise LocalRuntimeError(
                409,
                "workspace_secret_identity_mismatch",
                "本机密钥与工作空间身份不一致",
            )
        return WorkspaceContext(
            sandbox_id=sandbox_id,
            cloud_instance_id=fixed["cloudInstanceId"],
            organization_id=fixed["organizationId"],
            cloud_api_url=str(row["cloud_api_url"]),
            principal_id=fixed["principalId"],
            membership_id=fixed["membershipId"],
            access_token=str(secret.get("accessToken") or ""),
            refresh_token=str(secret.get("refreshToken") or ""),
            access_expires_at=(
                str(secret.get("expiresAt")) if secret.get("expiresAt") else None
            ),
            refresh_expires_at=(
                str(secret.get("refreshExpiresAt"))
                if secret.get("refreshExpiresAt")
                else None
            ),
        )

    def _session_lock(self, sandbox_id: str) -> threading.RLock:
        with self._state_lock:
            lock = self._session_locks.get(sandbox_id)
            if lock is None:
                lock = threading.RLock()
                self._session_locks[sandbox_id] = lock
            return lock

    @staticmethod
    def _parse_session_time(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _access_needs_refresh(self, context: WorkspaceContext) -> bool:
        if not context.access_token:
            return True
        expires_at = self._parse_session_time(context.access_expires_at)
        if expires_at is None:
            return False
        return expires_at <= datetime.now(timezone.utc) + timedelta(seconds=30)

    @staticmethod
    def _same_workspace_identity(
        expected: WorkspaceContext,
        actual: WorkspaceContext,
    ) -> bool:
        return (
            expected.sandbox_id == actual.sandbox_id
            and expected.cloud_instance_id == actual.cloud_instance_id
            and expected.organization_id == actual.organization_id
            and expected.cloud_api_url == actual.cloud_api_url
            and expected.principal_id == actual.principal_id
            and expected.membership_id == actual.membership_id
        )

    def _validate_refreshed_session(
        self,
        context: WorkspaceContext,
        refreshed: dict[str, Any],
    ) -> None:
        self._validate_session_payload(
            {"cloudInstanceId": context.cloud_instance_id},
            refreshed,
        )
        expected = {
            "cloudInstanceId": context.cloud_instance_id,
            "organizationId": context.organization_id,
            "principalId": context.principal_id,
            "membershipId": context.membership_id,
        }
        bad = [
            key
            for key, value in expected.items()
            if str(refreshed.get(key) or "") != value
        ]
        if bad:
            self._mark_status(
                context.sandbox_id,
                runtime_status="identity_error",
                identity_state="identity_error",
            )
            raise LocalRuntimeError(
                409,
                "workspace_session_identity_mismatch",
                "刷新后的组织身份与原工作空间不一致：" + "、".join(bad),
            )

    def _persist_refreshed_session(
        self,
        context: WorkspaceContext,
        refreshed: dict[str, Any],
    ) -> WorkspaceContext:
        reference = self._session_ref(context.sandbox_id)
        previous_encoded = self.secret_store.get(reference)
        if not previous_encoded:
            raise LocalRuntimeError(401, "workspace_secret_missing", "该设备缺少组织登录凭据")
        previous_document = decode_secret_bundle(previous_encoded)
        next_document = {
            **previous_document,
            "accessToken": refreshed["accessToken"],
            "refreshToken": refreshed["refreshToken"],
            "expiresAt": refreshed.get("expiresAt"),
            "refreshExpiresAt": refreshed.get("refreshExpiresAt"),
        }
        next_encoded = encode_secret_bundle(next_document)
        self.secret_store.set(reference, next_encoded)
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    binding = connection.execute(
                        """
                        SELECT cloud_instance_id, organization_id
                        FROM workspace_bindings
                        WHERE sandbox_id = ?
                        """,
                        (context.sandbox_id,),
                    ).fetchone()
                    if (
                        binding is None
                        or str(binding["cloud_instance_id"]) != context.cloud_instance_id
                        or str(binding["organization_id"]) != context.organization_id
                    ):
                        raise LocalRuntimeError(
                            409,
                            "workspace_binding_identity_mismatch",
                            "工作空间绑定在会话刷新期间发生变化",
                        )
                    self._write_snapshot_document(
                        connection,
                        sandbox_id=context.sandbox_id,
                        cloud_snapshot=refreshed["sessionSnapshot"],
                        secret_ref=reference,
                        credential_fingerprint=secret_fingerprint(next_encoded),
                    )
                    self._replace_projections(
                        connection,
                        sandbox_id=context.sandbox_id,
                        snapshot=refreshed["sessionSnapshot"],
                    )
                    now = utc_now()
                    connection.execute(
                        """
                        UPDATE workspace_sandboxes
                        SET runtime_status = 'ready', version = version + 1,
                            updated_at = ?
                        WHERE sandbox_id = ?
                        """,
                        (now, context.sandbox_id),
                    )
                    connection.execute(
                        """
                        UPDATE workspace_bindings
                        SET identity_state = 'verified', verified_at = ?,
                            version = version + 1, updated_at = ?
                        WHERE sandbox_id = ?
                        """,
                        (now, now, context.sandbox_id),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
        except Exception:
            self.secret_store.set(reference, previous_encoded)
            raise
        return self._secret_context(context.sandbox_id)

    def _mark_session_ready(self, sandbox_id: str) -> None:
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE workspace_sandboxes
                SET runtime_status = 'ready', version = version + 1,
                    updated_at = ?
                WHERE sandbox_id = ? AND runtime_status != 'ready'
                """,
                (now, sandbox_id),
            )
            connection.execute(
                """
                UPDATE workspace_bindings
                SET identity_state = 'verified', verified_at = ?,
                    version = version + 1, updated_at = ?
                WHERE sandbox_id = ? AND identity_state != 'verified'
                """,
                (now, now, sandbox_id),
            )
            connection.execute(
                """
                UPDATE workspace_session_snapshots
                SET status = 'active', version = version + 1, updated_at = ?
                WHERE sandbox_id = ? AND status != 'active'
                """,
                (now, sandbox_id),
            )
            connection.commit()

    def _raise_cloud_session_failure(
        self,
        context: WorkspaceContext,
        exc: CloudClientError,
    ) -> None:
        if exc.status_code == 401:
            self._mark_status(
                context.sandbox_id,
                runtime_status="needs_login",
                identity_state="needs_login",
                session_status="needs_login",
            )
            raise LocalRuntimeError(
                401,
                "needs_login",
                "组织登录已失效，请重新登录该组织",
            ) from exc
        if exc.status_code >= 500 or exc.code in {
            "cloud_timeout",
            "cloud_unreachable",
            "cloud_response_invalid",
            "session_conflict",
        }:
            self._mark_status(
                context.sandbox_id,
                runtime_status="sync_degraded",
            )
            raise LocalRuntimeError(
                exc.status_code,
                "failed_retryable",
                f"{exc.message}，可以重试",
            ) from exc
        if exc.code in {"cloud_identity_mismatch", "identity_mismatch"}:
            self._mark_status(
                context.sandbox_id,
                runtime_status="identity_error",
                identity_state="identity_error",
            )
            raise LocalRuntimeError(exc.status_code, exc.code, exc.message) from exc
        if exc.code == "schema_incompatible":
            self._mark_status(
                context.sandbox_id,
                runtime_status="schema_incompatible",
                identity_state="identity_error",
            )
            raise LocalRuntimeError(exc.status_code, exc.code, exc.message) from exc
        self._mark_session_ready(context.sandbox_id)
        raise LocalRuntimeError(exc.status_code, exc.code, exc.message) from exc

    def _refresh_session_context(
        self,
        context: WorkspaceContext,
        *,
        force: bool,
    ) -> WorkspaceContext:
        with self._session_lock(context.sandbox_id):
            latest = self._secret_context(context.sandbox_id)
            if not self._same_workspace_identity(context, latest):
                self._mark_status(
                    context.sandbox_id,
                    runtime_status="identity_error",
                    identity_state="identity_error",
                )
                raise LocalRuntimeError(
                    409,
                    "workspace_context_changed",
                    "工作空间身份在请求期间发生变化",
                )
            if force and latest.access_token != context.access_token:
                return latest
            if not force and not self._access_needs_refresh(latest):
                return latest
            if not latest.refresh_token:
                self._mark_status(
                    latest.sandbox_id,
                    runtime_status="needs_login",
                    identity_state="needs_login",
                    session_status="needs_login",
                )
                raise LocalRuntimeError(401, "needs_login", "该组织需要重新登录")
            client = self.cloud_factory(latest.cloud_api_url)
            try:
                refreshed = client.refresh(latest.refresh_token)
            except CloudClientError as exc:
                self._raise_cloud_session_failure(latest, exc)
            self._validate_refreshed_session(latest, refreshed)
            return self._persist_refreshed_session(latest, refreshed)

    def _ensure_session_context(
        self,
        context: WorkspaceContext,
    ) -> WorkspaceContext:
        latest = self._secret_context(context.sandbox_id)
        if not self._same_workspace_identity(context, latest):
            self._mark_status(
                context.sandbox_id,
                runtime_status="identity_error",
                identity_state="identity_error",
            )
            raise LocalRuntimeError(
                409,
                "workspace_context_changed",
                "工作空间身份在请求期间发生变化",
            )
        if not self._access_needs_refresh(latest):
            return latest
        return self._refresh_session_context(latest, force=False)

    def _authenticated_cloud_call(
        self,
        context: WorkspaceContext,
        operation: Callable[[CloudClient, WorkspaceContext], Any],
    ) -> tuple[Any, WorkspaceContext]:
        current = self._ensure_session_context(context)
        client = self.cloud_factory(current.cloud_api_url)
        try:
            result = operation(client, current)
        except CloudClientError as exc:
            if exc.status_code != 401:
                self._raise_cloud_session_failure(current, exc)
            current = self._refresh_session_context(current, force=True)
            client = self.cloud_factory(current.cloud_api_url)
            try:
                result = operation(client, current)
            except CloudClientError as retry_exc:
                self._raise_cloud_session_failure(current, retry_exc)
        self._mark_session_ready(current.sandbox_id)
        return result, current

    def _mark_status(
        self,
        sandbox_id: str,
        *,
        runtime_status: str,
        identity_state: str | None = None,
        session_status: str | None = None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE workspace_sandboxes
                SET runtime_status = ?, version = version + 1, updated_at = ?
                WHERE sandbox_id = ?
                """,
                (runtime_status, utc_now(), sandbox_id),
            )
            if identity_state:
                connection.execute(
                    """
                    UPDATE workspace_bindings
                    SET identity_state = ?, version = version + 1, updated_at = ?
                    WHERE sandbox_id = ?
                    """,
                    (identity_state, utc_now(), sandbox_id),
                )
            if session_status:
                connection.execute(
                    """
                    UPDATE workspace_session_snapshots
                    SET status = ?, version = version + 1, updated_at = ?
                    WHERE sandbox_id = ?
                    """,
                    (session_status, utc_now(), sandbox_id),
                )
            connection.commit()

    def _session_with_refresh(
        self,
        client: CloudClient,
        context: WorkspaceContext,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current = self._ensure_session_context(context)
        client = self.cloud_factory(current.cloud_api_url)
        try:
            session = client.current_session(current.access_token)
        except CloudClientError as exc:
            if exc.status_code != 401:
                raise
            current = self._refresh_session_context(current, force=True)
            client = self.cloud_factory(current.cloud_api_url)
            session = client.current_session(current.access_token)
        return session, {
            "accessToken": current.access_token,
            "refreshToken": current.refresh_token,
            "expiresAt": current.access_expires_at,
            "refreshExpiresAt": current.refresh_expires_at,
        }

    def _write_ai_runtime(
        self,
        sandbox_id: str,
        runtime: dict[str, Any],
    ) -> None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT session_snapshot_json, secret_ref, credential_fingerprint
                FROM workspace_session_snapshots
                WHERE sandbox_id = ?
                """,
                (sandbox_id,),
            ).fetchone()
            if row is None:
                raise LocalRuntimeError(409, "workspace_session_missing", "工作空间会话不存在")
            document = json.loads(str(row["session_snapshot_json"]))
            document["aiRuntime"] = runtime
            connection.execute(
                """
                UPDATE workspace_session_snapshots
                SET session_snapshot_json = ?, version = version + 1,
                    updated_at = ?
                WHERE sandbox_id = ?
                """,
                (canonical_json(document), utc_now(), sandbox_id),
            )
            connection.commit()

    def _sync_ai_for_context(
        self,
        context: WorkspaceContext,
    ) -> dict[str, Any]:
        main_result: dict[str, Any] | None = None
        current = context
        try:
            main_result, current = self._authenticated_cloud_call(
                context,
                lambda client, session: client.ai_runtime_secret(session.access_token),
            )
        except LocalRuntimeError as exc:
            if exc.code != "organization_ai_not_ready":
                raise

        routing_result: dict[str, Any] | None = None
        routing_error: LocalRuntimeError | None = None

        def read_routing_secret(
            client: CloudClient,
            session: WorkspaceContext,
        ) -> dict[str, Any]:
            direct = getattr(client, "ai_routing_runtime_secret", None)
            if callable(direct):
                return direct(session.access_token)
            result = client.request_v2(
                "GET",
                "/api/v2/organization-access/settings/ai-routing/runtime-secret",
                access_token=session.access_token,
            )
            if not isinstance(result, dict):
                raise CloudClientError(
                    502,
                    "cloud_response_invalid",
                    "组织云高级 AI 路由响应结构不正确",
                )
            return result

        try:
            routing_result, current = self._authenticated_cloud_call(
                current,
                read_routing_secret,
            )
        except LocalRuntimeError as exc:
            routing_error = exc

        if main_result is not None:
            if (
                str(main_result.get("cloudInstanceId") or "")
                != current.cloud_instance_id
                or str(main_result.get("organizationId") or "")
                != current.organization_id
            ):
                raise LocalRuntimeError(
                    409,
                    "organization_ai_identity_mismatch",
                    "组织 AI 配置身份与当前工作空间不一致",
                )
            main_api_key = str(main_result.get("apiKey") or "")
            if (
                not main_api_key
                and not _local_ai_url(str(main_result.get("baseUrl") or ""))
            ):
                raise LocalRuntimeError(
                    502,
                    "organization_ai_secret_missing",
                    "远端组织 AI 配置缺少运行密钥",
                )
            self.secret_store.set(
                self._ai_ref(current.sandbox_id),
                main_api_key,
            )
        else:
            main_api_key = ""
            self.secret_store.delete(self._ai_ref(current.sandbox_id))

        routing: dict[str, Any]
        if routing_result is not None:
            if (
                str(routing_result.get("cloudInstanceId") or "")
                != current.cloud_instance_id
                or str(routing_result.get("organizationId") or "")
                != current.organization_id
            ):
                raise LocalRuntimeError(
                    409,
                    "organization_ai_routing_identity_mismatch",
                    "高级 AI 路由身份与当前工作空间不一致",
                )
            for profile_key in _AI_ROUTING_PROFILE_KEYS:
                self.secret_store.delete(
                    self._ai_profile_ref(current.sandbox_id, profile_key)
                )
            profiles: dict[str, dict[str, Any]] = {}
            raw_profiles = routing_result.get("profiles")
            if not isinstance(raw_profiles, dict):
                raw_profiles = {}
            for profile_key, raw_profile in raw_profiles.items():
                if (
                    profile_key not in _AI_ROUTING_PROFILE_KEYS
                    or not isinstance(raw_profile, dict)
                ):
                    continue
                profile_api_key = str(raw_profile.get("apiKey") or "")
                if profile_api_key:
                    self.secret_store.set(
                        self._ai_profile_ref(
                            current.sandbox_id,
                            profile_key,
                        ),
                        profile_api_key,
                    )
                base_url = str(raw_profile.get("baseUrl") or "").strip()
                profiles[profile_key] = {
                    "enabled": bool(raw_profile.get("enabled")),
                    "provider": str(
                        raw_profile.get("provider") or "openai_compatible"
                    ),
                    "providerLabel": str(
                        raw_profile.get("providerLabel") or ""
                    ),
                    "baseUrl": base_url,
                    "model": str(raw_profile.get("model") or "").strip(),
                    "capability": str(
                        raw_profile.get("capability") or profile_key
                    ),
                    "isLocal": _local_ai_url(base_url),
                    "hasApiKey": bool(profile_api_key),
                }
            enabled = bool(
                routing_result.get("advancedAiRoutingEnabled")
            )
            mode = str(routing_result.get("aiModelMode") or "auto")
            if mode not in {
                "auto",
                "online_first",
                "local_first",
                "local_only",
            }:
                raise LocalRuntimeError(
                    502,
                    "organization_ai_routing_mode_invalid",
                    "组织云返回了无效的高级 AI 路由模式",
                )
            routing = {
                "state": "ready" if enabled else "disabled",
                "enabled": enabled,
                "mode": mode,
                "effectiveScopeKind": routing_result.get(
                    "effectiveScopeKind"
                ),
                "precedence": "personal_over_organization",
                "version": int(routing_result.get("version") or 0),
                "profiles": profiles,
                "syncedAt": utc_now(),
                "message": "",
            }
        else:
            with self._connection() as connection:
                existing_routing = self._current_ai_runtime(
                    connection,
                    current.sandbox_id,
                ).get("routing")
            routing = (
                dict(existing_routing)
                if isinstance(existing_routing, dict)
                else {
                    "state": "not_connected",
                    "enabled": False,
                    "mode": "auto",
                    "profiles": {},
                }
            )
            routing.update(
                {
                    "syncState": (
                        "failed_retryable"
                        if routing_error is not None
                        and (
                            routing_error.status_code >= 500
                            or routing_error.status_code in {408, 409, 425, 429}
                        )
                        else "blocked"
                    ),
                    "syncErrorCode": (
                        routing_error.code if routing_error is not None else ""
                    ),
                    "syncedAt": utc_now(),
                    "message": (
                        "高级 AI 路由同步失败，继续使用当前已验证配置"
                        if isinstance(existing_routing, dict)
                        else "高级 AI 路由尚未接通"
                    ),
                }
            )

        first_routed_profile = next(
            (
                routing["profiles"][key]
                for key in (
                    "online_primary",
                    "local_text_deep",
                    "local_fast",
                    "local_vision_ocr",
                )
                if isinstance(routing.get("profiles"), dict)
                and isinstance(routing["profiles"].get(key), dict)
                and bool(routing["profiles"][key].get("enabled"))
                and str(routing["profiles"][key].get("baseUrl") or "")
                and str(routing["profiles"][key].get("model") or "")
                and (
                    bool(routing["profiles"][key].get("isLocal"))
                    or bool(routing["profiles"][key].get("hasApiKey"))
                    or (key == "online_primary" and bool(main_api_key))
                )
            ),
            None,
        )
        ready = main_result is not None or first_routed_profile is not None
        primary = main_result or first_routed_profile or {}
        runtime = {
            "state": "ready_direct" if ready else "not_ready",
            "provider": str(primary.get("provider") or ""),
            "baseUrl": str(primary.get("baseUrl") or ""),
            "modelName": str(
                primary.get("modelName") or primary.get("model") or ""
            ),
            "keyFingerprint": str(
                primary.get("keyFingerprint") or ""
            ),
            "configVersion": int(
                primary.get("configVersion") or 0
            ),
            "syncedAt": utc_now(),
            "message": (
                ""
                if ready
                else "管理员尚未配置可用的组织模型或高级路由"
            ),
            "routing": routing,
        }
        self._write_ai_runtime(current.sandbox_id, runtime)
        return runtime

    def _ai_candidates(
        self,
        context: WorkspaceContext,
        runtime: Mapping[str, Any],
        *,
        capability: str,
    ) -> tuple[list[dict[str, Any]], str]:
        main_api_key = self.secret_store.get(
            self._ai_ref(context.sandbox_id)
        )
        main_base_url = str(runtime.get("baseUrl") or "").strip()
        main_model = str(runtime.get("modelName") or "").strip()
        main_candidate: dict[str, Any] | None = None
        if (
            main_base_url
            and main_model
            and (
                _local_ai_url(main_base_url)
                or bool(main_api_key)
            )
        ):
            main_candidate = {
                "profileKey": "organization_main",
                "provider": str(runtime.get("provider") or ""),
                "baseUrl": main_base_url,
                "modelName": main_model,
                "apiKey": main_api_key or "",
                "isLocal": _local_ai_url(main_base_url),
            }

        routing = runtime.get("routing")
        if not isinstance(routing, dict) or not bool(routing.get("enabled")):
            return ([main_candidate] if main_candidate else []), "main_only"
        mode = str(routing.get("mode") or "auto")
        if mode == "cloud_first":
            mode = "online_first"
        if mode not in {
            "auto",
            "online_first",
            "local_first",
            "local_only",
        }:
            raise LocalRuntimeError(
                409,
                "organization_ai_routing_mode_invalid",
                "高级 AI 路由模式无效，请重新同步组织配置",
            )
        profiles = routing.get("profiles")
        if not isinstance(profiles, dict):
            profiles = {}
        if capability == "vision_ocr":
            local_profile_order = (
                "local_vision_ocr",
                "local_text_deep",
                "local_fast",
            )
        elif capability == "fast_structured":
            local_profile_order = (
                "local_fast",
                "local_text_deep",
            )
        else:
            local_profile_order = (
                "local_text_deep",
                "local_fast",
            )

        def profile_candidate(profile_key: str) -> dict[str, Any] | None:
            profile = profiles.get(profile_key)
            if not isinstance(profile, dict) or not bool(
                profile.get("enabled")
            ):
                return None
            base_url = str(profile.get("baseUrl") or "").strip()
            model_name = str(profile.get("model") or "").strip()
            if not base_url or not model_name:
                return None
            is_local = _local_ai_url(base_url)
            profile_api_key = self.secret_store.get(
                self._ai_profile_ref(
                    context.sandbox_id,
                    profile_key,
                )
            )
            if (
                profile_key == "online_primary"
                and not profile_api_key
            ):
                profile_api_key = main_api_key
            if not is_local and not profile_api_key:
                return None
            return {
                "profileKey": profile_key,
                "provider": str(profile.get("provider") or ""),
                "baseUrl": base_url,
                "modelName": model_name,
                "apiKey": profile_api_key or "",
                "isLocal": is_local,
            }

        capability_candidates = [
            candidate
            for key in local_profile_order
            if (candidate := profile_candidate(key)) is not None
        ]
        local_candidates = [
            candidate
            for candidate in capability_candidates
            if bool(candidate["isLocal"])
        ]
        primary_profile_candidate = profile_candidate("online_primary")
        compatibility_main_candidate = (
            main_candidate
            if primary_profile_candidate is None
            or bool(primary_profile_candidate["isLocal"])
            else None
        )
        online_candidates = [
            candidate
            for candidate in (
                *(
                    item
                    for item in capability_candidates
                    if not bool(item["isLocal"])
                ),
                primary_profile_candidate,
                compatibility_main_candidate,
            )
            if candidate is not None and not bool(candidate["isLocal"])
        ]
        # A profile name describes its intended role; the URL is the final
        # network-boundary authority. A loopback online_primary therefore
        # remains eligible in local_only, while a remote local_text_deep does
        # not.
        other_local_candidates = [
            candidate
            for candidate in (
                primary_profile_candidate,
                compatibility_main_candidate,
            )
            if candidate is not None and bool(candidate["isLocal"])
        ]
        local_candidates.extend(other_local_candidates)

        ordered = (
            local_candidates
            if mode == "local_only"
            else local_candidates + online_candidates
            if mode == "local_first"
            else online_candidates + local_candidates
        )
        deduplicated: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in ordered:
            identity = (
                str(candidate["baseUrl"]).rstrip("/"),
                str(candidate["modelName"]),
            )
            if identity in seen:
                continue
            seen.add(identity)
            deduplicated.append(candidate)
        return deduplicated, mode

    def _ai_runtime_candidates(
        self,
        context: WorkspaceContext,
        *,
        capability: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        with self._connection() as connection:
            runtime = self._current_ai_runtime(
                connection,
                context.sandbox_id,
            )
        should_sync = runtime.get("state") != "ready_direct" or (
            "routing" not in runtime and hasattr(self, "cloud_factory")
        )
        if should_sync:
            runtime = self._sync_ai_for_context(context)
        candidates, mode = self._ai_candidates(
            context,
            runtime,
            capability=capability,
        )
        routing = runtime.get("routing")
        retry_routing_sync = (
            hasattr(self, "cloud_factory")
            and (
                not isinstance(routing, dict)
                or routing.get("syncState") in {
                    "failed_retryable",
                    "not_connected",
                }
            )
        )
        if not candidates and not should_sync and retry_routing_sync:
            runtime = self._sync_ai_for_context(context)
            candidates, mode = self._ai_candidates(
                context,
                runtime,
                capability=capability,
            )
        if not candidates:
            if mode == "local_only":
                raise LocalRuntimeError(
                    409,
                    "local_ai_profile_not_ready",
                    "本地专用模式没有可用的本机模型，请检查本机模型配置",
                )
            raise LocalRuntimeError(
                409,
                "organization_ai_not_ready",
                str(
                    runtime.get("message")
                    or "组织 AI 尚未准备完成"
                ),
            )
        return runtime, candidates, mode

    @staticmethod
    def _invoke_ai_chat(
        candidates: list[dict[str, Any]],
        *,
        messages: list[dict[str, str]],
        temperature: float,
    ) -> tuple[str, dict[str, Any]]:
        failures: list[str] = []
        for candidate in candidates:
            base_url = str(candidate["baseUrl"]).strip().rstrip("/")
            endpoint = (
                base_url
                if base_url.endswith("/chat/completions")
                else f"{base_url}/chat/completions"
            )
            api_key = str(candidate.get("apiKey") or "")
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(
                        connect=5.0,
                        read=45.0,
                        write=15.0,
                        pool=5.0,
                    ),
                    follow_redirects=False,
                    trust_env=False,
                ) as http_client:
                    response = http_client.post(
                        endpoint,
                        headers={
                            **(
                                {"Authorization": f"Bearer {api_key}"}
                                if api_key
                                else {}
                            ),
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": str(candidate["modelName"]),
                            "messages": messages,
                            "temperature": temperature,
                            "stream": False,
                        },
                    )
            except httpx.TimeoutException:
                failures.append(
                    f"{candidate['profileKey']}:timeout"
                )
                continue
            except httpx.HTTPError:
                failures.append(
                    f"{candidate['profileKey']}:unreachable"
                )
                continue
            try:
                response_payload = response.json()
            except ValueError:
                failures.append(
                    f"{candidate['profileKey']}:invalid_response"
                )
                continue
            if response.status_code >= 400:
                if (
                    response.status_code in {408, 425, 429}
                    or response.status_code >= 500
                ):
                    failures.append(
                        f"{candidate['profileKey']}:http_"
                        f"{response.status_code}"
                    )
                    continue
                raise LocalRuntimeError(
                    response.status_code,
                    "ai_request_rejected",
                    f"模型请求被拒绝（{response.status_code}），"
                    "请检查当前路由配置和凭据",
                )
            try:
                content = str(
                    response_payload["choices"][0]["message"]["content"]
                ).strip()
            except (KeyError, IndexError, TypeError):
                failures.append(
                    f"{candidate['profileKey']}:invalid_response"
                )
                continue
            if not content:
                failures.append(
                    f"{candidate['profileKey']}:empty_response"
                )
                continue
            return content, candidate
        raise LocalRuntimeError(
            503,
            "ai_routes_exhausted",
            "可用模型均暂时失败，可重试（"
            + "；".join(failures)
            + "）",
        )

    def _active_context(self, *, require_ready: bool = True) -> WorkspaceContext:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT sandbox_id, sandbox_kind, runtime_status
                FROM workspace_sandboxes
                WHERE is_active = 1
                """
            ).fetchone()
        if row is None or row["sandbox_kind"] != "organization":
            raise LocalRuntimeError(409, "organization_required", "当前尚未连接组织")
        if require_ready and row["runtime_status"] not in {"ready", "sync_degraded"}:
            raise LocalRuntimeError(409, "workspace_not_ready", "当前组织尚未准备完成")
        return self._secret_context(str(row["sandbox_id"]))

    def _current_context(self, *, require_ready: bool = True) -> WorkspaceContext:
        workspace_local = getattr(self, "_workspace_context_local", None)
        if workspace_local is None:
            workspace_local = threading.local()
            self._workspace_context_local = workspace_local
        pinned_sandbox = getattr(workspace_local, "sandbox_context", None)
        if isinstance(pinned_sandbox, PinnedSandboxContext):
            if pinned_sandbox.workspace_context is not None:
                return pinned_sandbox.workspace_context
            raise LocalRuntimeError(
                409,
                "organization_required",
                "当前尚未连接组织",
            )
        pinned = getattr(workspace_local, "workspace_context", None)
        if isinstance(pinned, WorkspaceContext):
            return pinned
        return self._active_context(require_ready=require_ready)

    def capture_workspace_context(self) -> WorkspaceContext:
        return self._active_context(require_ready=True)

    def capture_sandbox_context(self) -> PinnedSandboxContext:
        """Capture the active sandbox, including local-only draft sandboxes."""
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT s.sandbox_id, s.sandbox_kind, s.runtime_status,
                       b.cloud_instance_id, b.organization_id
                FROM workspace_sandboxes AS s
                LEFT JOIN workspace_bindings AS b
                  ON b.sandbox_id = s.sandbox_id
                WHERE s.is_active = 1
                """
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(
                500,
                "active_workspace_missing",
                "没有活动工作空间",
            )
        sandbox_kind = str(row["sandbox_kind"])
        workspace_context = None
        if sandbox_kind == "organization":
            if row["runtime_status"] not in {"ready", "sync_degraded"}:
                raise LocalRuntimeError(
                    409,
                    "workspace_not_ready",
                    "当前组织尚未准备完成",
                )
            workspace_context = self._secret_context(str(row["sandbox_id"]))
        return PinnedSandboxContext(
            sandbox_id=str(row["sandbox_id"]),
            sandbox_kind=sandbox_kind,
            cloud_instance_id=(
                str(row["cloud_instance_id"])
                if row["cloud_instance_id"] is not None
                else None
            ),
            organization_id=(
                str(row["organization_id"])
                if row["organization_id"] is not None
                else None
            ),
            workspace_context=workspace_context,
        )

    @staticmethod
    def _same_sandbox_identity(
        expected: PinnedSandboxContext,
        actual: PinnedSandboxContext,
    ) -> bool:
        return (
            expected.sandbox_id == actual.sandbox_id
            and expected.sandbox_kind == actual.sandbox_kind
            and expected.cloud_instance_id == actual.cloud_instance_id
            and expected.organization_id == actual.organization_id
        )

    def _validate_pinned_sandbox(
        self,
        expected: PinnedSandboxContext,
    ) -> None:
        if expected.workspace_context is not None:
            current_workspace = (
                self._current_context(require_ready=True)
                if "_current_context" in self.__dict__
                or not hasattr(self, "database_path")
                else self._active_context(require_ready=True)
            )
            unchanged = self._same_workspace_identity(
                expected.workspace_context,
                current_workspace,
            )
        else:
            unchanged = self._same_sandbox_identity(
                expected,
                self.capture_sandbox_context(),
            )
        if not unchanged:
            raise LocalRuntimeError(
                409,
                "workspace_context_changed",
                "操作期间工作空间身份发生变化，请在当前工作空间重试",
            )

    @contextmanager
    def prebound_sandbox_context(
        self,
        captured: PinnedSandboxContext,
    ) -> Iterator[PinnedSandboxContext]:
        """Bind a queue-time sandbox identity to the worker thread."""
        workspace_local = getattr(self, "_workspace_context_local", None)
        if workspace_local is None:
            workspace_local = threading.local()
            self._workspace_context_local = workspace_local
        previous = getattr(workspace_local, "sandbox_context", None)
        workspace_local.sandbox_context = captured
        try:
            yield captured
        finally:
            if previous is None:
                del workspace_local.sandbox_context
            else:
                workspace_local.sandbox_context = previous
        self._validate_pinned_sandbox(captured)

    @contextmanager
    def pinned_workspace_context(
        self,
        captured: WorkspaceContext | PinnedSandboxContext | None = None,
    ) -> Iterator[WorkspaceContext | PinnedSandboxContext]:
        """Keep one composite UI operation on its captured sandbox."""
        workspace_local = getattr(self, "_workspace_context_local", None)
        prebound = (
            getattr(workspace_local, "sandbox_context", None)
            if workspace_local is not None
            else None
        )
        if isinstance(captured, WorkspaceContext):
            resolved = PinnedSandboxContext(
                sandbox_id=captured.sandbox_id,
                sandbox_kind="organization",
                cloud_instance_id=captured.cloud_instance_id,
                organization_id=captured.organization_id,
                workspace_context=captured,
            )
        elif isinstance(captured, PinnedSandboxContext):
            resolved = captured
        elif isinstance(prebound, PinnedSandboxContext):
            resolved = prebound
        else:
            try:
                current_workspace = self._current_context(require_ready=True)
            except LocalRuntimeError as exc:
                if exc.code != "organization_required":
                    raise
                resolved = self.capture_sandbox_context()
            else:
                resolved = PinnedSandboxContext(
                    sandbox_id=current_workspace.sandbox_id,
                    sandbox_kind="organization",
                    cloud_instance_id=current_workspace.cloud_instance_id,
                    organization_id=current_workspace.organization_id,
                    workspace_context=current_workspace,
                )
        workspace_local = getattr(self, "_workspace_context_local", None)
        if workspace_local is None:
            workspace_local = threading.local()
            self._workspace_context_local = workspace_local
        previous = getattr(workspace_local, "sandbox_context", None)
        workspace_local.sandbox_context = resolved
        try:
            yield resolved.workspace_context or resolved
        finally:
            if previous is None:
                del workspace_local.sandbox_context
            else:
                workspace_local.sandbox_context = previous
        self._validate_pinned_sandbox(resolved)

    def _authenticate(
        self,
        *,
        mode: str,
        cloud_api_url: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        transition_id = self._start_transition()
        normalized_url = normalize_cloud_url(cloud_api_url)
        client = self.cloud_factory(normalized_url)
        try:
            handshake = client.handshake()
            self._validate_handshake(handshake)
            if mode == "login":
                response = client.login(
                    identifier=str(payload["identifier"]),
                    password=str(payload["password"]),
                )
            elif mode == "join":
                response = client.join(payload)
            elif mode == "create":
                response = client.create_organization(payload)
            else:
                raise AssertionError(f"unsupported auth mode: {mode}")
            sandbox_id = self._apply_session(
                transition_id=transition_id,
                cloud_api_url=normalized_url,
                handshake=handshake,
                payload=response,
            )
            self._sync_business_for_context(
                self._secret_context(sandbox_id)
            )
            return self.current()
        finally:
            self._finish_transition(transition_id)

    def login(
        self,
        *,
        cloud_api_url: str,
        identifier: str,
        password: str,
    ) -> dict[str, Any]:
        return self._authenticate(
            mode="login",
            cloud_api_url=cloud_api_url,
            payload={"identifier": identifier, "password": password},
        )

    def join(
        self,
        *,
        cloud_api_url: str,
        invite_code: str,
        display_name: str,
        email: str | None,
        phone: str | None,
        password: str,
    ) -> dict[str, Any]:
        return self._authenticate(
            mode="join",
            cloud_api_url=cloud_api_url,
            payload={
                "inviteCode": invite_code,
                "displayName": display_name,
                "email": email,
                "phone": phone,
                "password": password,
            },
        )

    def create_organization(
        self,
        *,
        cloud_api_url: str,
        bootstrap_token: str,
        organization_name: str,
        display_name: str,
        email: str | None,
        phone: str | None,
        password: str,
    ) -> dict[str, Any]:
        return self._authenticate(
            mode="create",
            cloud_api_url=cloud_api_url,
            payload={
                "bootstrapToken": bootstrap_token,
                "organizationName": organization_name,
                "displayName": display_name,
                "email": email,
                "phone": phone,
                "password": password,
            },
        )

    def switch(self, sandbox_id: str) -> dict[str, Any]:
        transition_id = self._start_transition(
            target_sandbox_id=sandbox_id,
            runtime_status="switching",
        )
        try:
            context = self._secret_context(sandbox_id)
            client = self.cloud_factory(context.cloud_api_url)
            handshake = client.handshake()
            self._validate_handshake(handshake)
            if handshake["cloudInstanceId"] != context.cloud_instance_id:
                raise LocalRuntimeError(
                    409,
                    "cloud_identity_mismatch",
                    "目标工作空间与组织云身份不一致",
                )
            # A controlled strict-schema migration may replace the database
            # generation while preserving the cloud instance and organization.
            # Rebind only after the existing session is accepted by that cloud;
            # _apply_session then validates the organization/principal/member
            # identity before persisting the new generation.
            session, refreshed = self._session_with_refresh(client, context)
            session_payload = {
                **refreshed,
                "cloudInstanceId": session["cloudInstanceId"],
                "organizationId": session["organizationId"],
                "principalId": session["principalId"],
                "membershipId": session["membershipId"],
                "sessionSnapshot": session["sessionSnapshot"],
            }
            refreshed_sandbox_id = self._apply_session(
                transition_id=transition_id,
                cloud_api_url=context.cloud_api_url,
                handshake=handshake,
                payload=session_payload,
            )
            self._sync_business_for_context(
                self._secret_context(refreshed_sandbox_id)
            )
            return self.current()
        except CloudClientError as exc:
            if exc.status_code == 401:
                self._mark_status(
                    sandbox_id,
                    runtime_status="needs_login",
                    identity_state="needs_login",
                    session_status="needs_login",
                )
            else:
                self._mark_status(sandbox_id, runtime_status="sync_degraded")
            raise LocalRuntimeError(exc.status_code, exc.code, exc.message) from exc
        except (LocalRuntimeError, SecretStoreError) as exc:
            code = getattr(exc, "code", "secret_store_error")
            if code in {"schema_incompatible", "cloud_identity_mismatch"}:
                self._mark_status(
                    sandbox_id,
                    runtime_status="schema_incompatible"
                    if code == "schema_incompatible"
                    else "identity_error",
                    identity_state="identity_error",
                )
            elif code.startswith("workspace_secret"):
                self._mark_status(
                    sandbox_id,
                    runtime_status="needs_login",
                    identity_state="needs_login",
                    session_status="needs_login",
                )
            raise
        finally:
            self._finish_transition(transition_id)

    def _binding_generation(self, sandbox_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT database_generation_id FROM workspace_bindings
                WHERE sandbox_id = ?
                """,
                (sandbox_id,),
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(404, "workspace_binding_missing", "工作空间绑定不存在")
        return str(row["database_generation_id"])

    def restore_active(self) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT sandbox_id, sandbox_kind
                FROM workspace_sandboxes
                WHERE is_active = 1
                """
            ).fetchone()
        if row is None or row["sandbox_kind"] == "local_draft":
            return self.current()
        return self.switch(str(row["sandbox_id"]))

    def restore_at_startup(self) -> dict[str, Any]:
        try:
            return self.restore_active()
        except LocalRuntimeError:
            # switch() has already persisted a precise terminal/retryable state.
            # The local backend must still start so the renderer can show it and
            # offer the correct recovery action.
            return self.current()

    def activate_local_draft(self) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT sandbox_id FROM workspace_sandboxes
                WHERE sandbox_kind = 'local_draft'
                LIMIT 1
                """
            ).fetchone()
            connection.execute(
                """
                UPDATE workspace_sandboxes
                SET is_active = CASE WHEN sandbox_id = ? THEN 1 ELSE 0 END,
                    runtime_status = CASE
                      WHEN sandbox_id = ? THEN 'local_draft'
                      ELSE runtime_status
                    END,
                    updated_at = ?
                """,
                (row["sandbox_id"], row["sandbox_id"], utc_now()),
            )
            connection.commit()
        return self.current()

    def logout(self) -> dict[str, Any]:
        context = self._current_context(require_ready=False)
        client = self.cloud_factory(context.cloud_api_url)
        try:
            client.logout(context.access_token)
        except CloudClientError:
            pass
        self.secret_store.delete(self._session_ref(context.sandbox_id))
        self.secret_store.delete(self._ai_ref(context.sandbox_id))
        for profile_key in _AI_ROUTING_PROFILE_KEYS:
            self.secret_store.delete(
                self._ai_profile_ref(context.sandbox_id, profile_key)
            )
        self._mark_status(
            context.sandbox_id,
            runtime_status="needs_login",
            identity_state="needs_login",
            session_status="revoked",
        )
        return self.current()

    def sync_ai(self) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        return self._sync_ai_for_context(context)

    def save_ai_config(
        self,
        *,
        provider: str,
        base_url: str,
        model_name: str,
        api_key: str,
        expected_version: int | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        result, current = self._authenticated_cloud_call(
            context,
            lambda client, session: client.save_ai_config(
                session.access_token,
                {
                    "provider": provider,
                    "baseUrl": base_url,
                    "modelName": model_name,
                    "apiKey": api_key,
                    "expectedVersion": expected_version,
                },
                idempotency_key=idempotency_key,
            ),
        )
        self.secret_store.set(self._ai_ref(current.sandbox_id), api_key)
        with self._connection() as connection:
            existing_routing = self._current_ai_runtime(
                connection,
                current.sandbox_id,
            ).get("routing")
        runtime = {
            "state": "ready_direct",
            "provider": result["provider"],
            "baseUrl": result["baseUrl"],
            "modelName": result["modelName"],
            "keyFingerprint": result["keyFingerprint"],
            "configVersion": result["configVersion"],
            "syncedAt": utc_now(),
            "message": "",
            **(
                {"routing": existing_routing}
                if isinstance(existing_routing, dict)
                else {}
            ),
        }
        self._write_ai_runtime(current.sandbox_id, runtime)
        return runtime

    def organization_command(
        self,
        command: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        operation_id = new_id()

        def execute(client: CloudClient, session: WorkspaceContext):
            if command == "create_department":
                result = client.create_department(
                    session.access_token,
                    payload,
                    idempotency_key=operation_id,
                )
            elif command == "create_management_title":
                result = client.create_management_title(
                    session.access_token,
                    payload,
                    idempotency_key=operation_id,
                )
            elif command == "create_invite":
                result = client.create_invite(session.access_token, payload)
            else:
                raise LocalRuntimeError(404, "command_unknown", "未知组织命令")
            return result, client.organization_snapshot(session.access_token)

        (result, snapshot), current = self._authenticated_cloud_call(
            context,
            execute,
        )
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT secret_ref, credential_fingerprint
                FROM workspace_session_snapshots
                WHERE sandbox_id = ?
                """,
                (current.sandbox_id,),
            ).fetchone()
            self._write_snapshot_document(
                connection,
                sandbox_id=current.sandbox_id,
                cloud_snapshot=snapshot,
                secret_ref=str(row["secret_ref"]),
                credential_fingerprint=str(row["credential_fingerprint"]),
            )
            self._replace_projections(
                connection,
                sandbox_id=current.sandbox_id,
                snapshot=snapshot,
            )
            connection.commit()
        return {"result": result, "workspace": self.current()}

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT s.sandbox_id, s.sandbox_kind, s.runtime_status,
                       s.display_name, s.is_active, s.updated_at,
                       b.cloud_instance_id, b.organization_id, b.cloud_api_url,
                       b.identity_state
                FROM workspace_sandboxes AS s
                LEFT JOIN workspace_bindings AS b
                  ON b.sandbox_id = s.sandbox_id
                ORDER BY s.sandbox_kind = 'local_draft', s.created_at
                """
            ).fetchall()
        return [
            {
                "sandboxId": row["sandbox_id"],
                "kind": row["sandbox_kind"],
                "runtimeStatus": row["runtime_status"],
                "displayName": row["display_name"],
                "isActive": bool(row["is_active"]),
                "cloudInstanceId": row["cloud_instance_id"],
                "organizationId": row["organization_id"],
                "cloudApiUrl": row["cloud_api_url"],
                "identityState": row["identity_state"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def current(self) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT s.sandbox_id, s.sandbox_kind, s.runtime_status,
                       s.display_name, s.updated_at, b.cloud_instance_id,
                       b.organization_id, b.cloud_api_url, b.identity_state,
                       ss.session_snapshot_json, ss.status AS session_status
                FROM workspace_sandboxes AS s
                LEFT JOIN workspace_bindings AS b
                  ON b.sandbox_id = s.sandbox_id
                LEFT JOIN workspace_session_snapshots AS ss
                  ON ss.sandbox_id = s.sandbox_id
                WHERE s.is_active = 1
                """
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(500, "active_workspace_missing", "没有活动工作空间")
        document: dict[str, Any] = {}
        if row["session_snapshot_json"]:
            try:
                document = json.loads(str(row["session_snapshot_json"]))
            except ValueError:
                document = {}
        is_organization = row["sandbox_kind"] == "organization"
        connected = is_organization and row["runtime_status"] in {"ready", "sync_degraded"}
        status_messages = {
            "local_draft": "尚未登录组织",
            "verifying": "正在验证组织身份",
            "switching": "正在切换工作空间",
            "ready": "组织工作空间已准备完成",
            "needs_login": "需要重新登录该组织",
            "identity_error": "组织身份校验失败",
            "sync_degraded": "组织已连接，部分同步暂时失败",
            "schema_incompatible": "组织云数据库合同不兼容",
        }
        return {
            "runtimeStatus": row["runtime_status"],
            "requiresLogin": row["runtime_status"] == "needs_login",
            "identityState": row["identity_state"]
            or ("unverified" if is_organization else "local_draft"),
            "statusMessage": status_messages.get(
                str(row["runtime_status"]),
                "工作空间状态未知",
            ),
            "sandbox": {
                "sandboxId": row["sandbox_id"],
                "kind": row["sandbox_kind"],
                "displayName": row["display_name"],
                "cloudInstanceId": row["cloud_instance_id"],
                "organizationId": row["organization_id"],
                "cloudApiUrl": row["cloud_api_url"],
                "updatedAt": row["updated_at"],
            },
            "sessionSnapshot": document.get("cloudSnapshot"),
            "aiRuntime": document.get("aiRuntime")
            or {
                "state": "not_ready",
                "message": "尚未同步组织 AI 配置",
            },
            "capabilities": capability_registry(cloud_connected=connected),
            "databaseIdentity": {
                "schemaFamily": self.identity.schema_family,
                "contractVersion": self.identity.contract_version,
                "schemaManifestSha256": self.identity.manifest_hash,
                "databaseGenerationId": self.identity.database_generation_id,
                "buildId": self.identity.build_id,
            },
        }

    def business_snapshot(
        self,
        *,
        refresh: bool = False,
        workspace_context: WorkspaceContext | None = None,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            where_clause = "s.sandbox_id = ?" if workspace_context else "s.is_active = 1"
            parameters = (
                (workspace_context.sandbox_id,)
                if workspace_context
                else ()
            )
            active = connection.execute(
                f"""
                SELECT s.sandbox_id, s.sandbox_kind, s.runtime_status,
                       b.organization_id
                FROM workspace_sandboxes AS s
                LEFT JOIN workspace_bindings AS b
                  ON b.sandbox_id = s.sandbox_id
                WHERE {where_clause}
                """,
                parameters,
            ).fetchone()
        if active is None or active["sandbox_kind"] != "organization":
            raise LocalRuntimeError(409, "organization_required", "当前尚未连接组织")
        sandbox_id = str(active["sandbox_id"])
        organization_id = str(active["organization_id"] or "")
        if not organization_id:
            raise LocalRuntimeError(
                409,
                "workspace_identity_missing",
                "当前工作空间缺少组织身份",
            )
        if refresh:
            context = workspace_context or self._secret_context(sandbox_id)
            self._sync_business_for_context(context)
        items: dict[str, list[dict[str, Any]]] = {
            "project": [],
            "task": [],
            "event_line": [],
            "knowledge_document": [],
            "narrative_output": [],
            "organization_plan": [],
            "weekly_review": [],
            "intelligence": [],
            "growth_signal": [],
            "growth_evidence": [],
            "experience_quote": [],
            "ai_answer": [],
            "favorite": [],
        }
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT object_kind, payload_json
                FROM projection_business_objects
                WHERE sandbox_id = ? AND projection_state = 'active'
                ORDER BY object_kind, refreshed_at DESC, object_id
                """,
                (sandbox_id,),
            ).fetchall()
        for row in rows:
            object_kind = str(row["object_kind"])
            if object_kind not in items:
                continue
            try:
                payload = json.loads(str(row["payload_json"]))
            except ValueError as exc:
                raise LocalRuntimeError(
                    500,
                    "business_projection_invalid",
                    "本机业务投影损坏，请重新同步",
                ) from exc
            items[object_kind].append(payload)
        result = {
            "organizationId": organization_id,
            "sandboxId": sandbox_id,
            "projects": items["project"],
            "tasks": items["task"],
            "eventLines": items["event_line"],
            "documents": items["knowledge_document"],
            "reports": items["narrative_output"],
            "plans": items["organization_plan"],
            "weeklyReviews": items["weekly_review"],
            "intelligence": items["intelligence"],
            "growthSignals": items["growth_signal"],
            "growthEvidence": items["growth_evidence"],
            "experienceQuotes": items["experience_quote"],
            "aiAnswers": items["ai_answer"],
            "favorites": items["favorite"],
        }
        result["counts"] = {
            "projects": len(result["projects"]),
            "tasks": len(result["tasks"]),
            "eventLines": len(result["eventLines"]),
            "documents": len(result["documents"]),
            "reports": len(result["reports"]),
            "plans": len(result["plans"]),
            "weeklyReviews": len(result["weeklyReviews"]),
            "intelligence": len(result["intelligence"]),
            "growthSignals": len(result["growthSignals"]),
            "growthEvidence": len(result["growthEvidence"]),
            "experienceQuotes": len(result["experienceQuotes"]),
        }
        return result

    @staticmethod
    def _project_from_snapshot(
        snapshot: dict[str, Any],
        project_id: str,
    ) -> dict[str, Any]:
        project = next(
            (
                item
                for item in snapshot.get("projects") or []
                if str(item.get("projectId") or "") == project_id
            ),
            None,
        )
        if project is None:
            raise LocalRuntimeError(
                404,
                "project_missing",
                "当前工作空间没有该项目",
            )
        return project

    def _local_project_materials(
        self,
        *,
        sandbox_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        prefix = project_storage_prefix(sandbox_id, project_id)
        with self._connection() as connection:
            summary_rows = connection.execute(
                """
                SELECT object_id, storage_key, content_hash, byte_size,
                       lifecycle_state, version, created_at, updated_at
                FROM storage_objects
                WHERE sandbox_id = ?
                  AND media_type = ?
                  AND lifecycle_state = 'active'
                  AND storage_key LIKE ?
                ORDER BY updated_at DESC, object_id
                """,
                (sandbox_id, LOCAL_SUMMARY_MEDIA_TYPE, f"{prefix}%"),
            ).fetchall()
            source_rows = {
                str(row["object_id"]): row
                for row in connection.execute(
                    """
                    SELECT object_id, storage_key, content_hash, media_type,
                           byte_size, lifecycle_state, version,
                           created_at, updated_at
                    FROM storage_objects
                    WHERE sandbox_id = ?
                      AND media_type != ?
                      AND lifecycle_state = 'active'
                      AND storage_key LIKE ?
                    """,
                    (sandbox_id, LOCAL_SUMMARY_MEDIA_TYPE, f"{prefix}%"),
                ).fetchall()
            }

        items: list[dict[str, Any]] = []
        invalid_count = 0
        for summary_row in summary_rows:
            payload = read_summary_document(
                self.database_path.parent,
                str(summary_row["storage_key"]),
                content_hash=str(summary_row["content_hash"]),
                byte_size=int(summary_row["byte_size"]),
            )
            if payload is None or str(payload.get("projectId") or "") != project_id:
                invalid_count += 1
                continue
            source_id = str(payload.get("sourceId") or "")
            source_row = source_rows.get(source_id)
            if source_row is None:
                invalid_count += 1
                continue
            if not managed_source_is_available(
                self.database_path.parent,
                str(source_row["storage_key"]),
                byte_size=int(source_row["byte_size"]),
            ):
                invalid_count += 1
                continue
            content_hash = str(source_row["content_hash"] or "")
            if content_hash != str(payload.get("contentHash") or ""):
                invalid_count += 1
                continue
            summary = str(payload.get("summary") or "").strip()
            if not summary:
                invalid_count += 1
                continue
            processing_state = str(payload.get("summaryKind") or "metadata_only")
            item = {
                "sourceScope": "local_private",
                "sourceType": "local_material",
                "sourceId": source_id,
                "sourceVersion": int(source_row["version"]),
                "contentHash": content_hash,
                "title": str(payload.get("fileName") or "本机资料"),
                "summary": summary,
                "sourceDescription": str(
                    payload.get("sourceDescription")
                    or "当前设备工作台本机私有资料"
                ),
                "updatedAt": str(
                    payload.get("updatedAt")
                    or source_row["updated_at"]
                ),
                "processingState": processing_state,
            }
            items.append(item)
        return {
            "items": items,
            "invalidCount": invalid_count,
        }

    @staticmethod
    def _validate_cloud_project_knowledge(
        payload: dict[str, Any],
        *,
        cloud_instance_id: str,
        organization_id: str,
        project_id: str,
    ) -> None:
        project = payload.get("project")
        if (
            payload.get("cloudInstanceId") != cloud_instance_id
            or payload.get("organizationId") != organization_id
            or not isinstance(project, dict)
            or project.get("projectId") != project_id
        ):
            raise LocalRuntimeError(
                502,
                "cloud_identity_mismatch",
                "组织云项目知识响应身份不匹配",
            )
        boundary = payload.get("materialBoundary")
        if not isinstance(boundary, dict) or any(
            boundary.get(key) is not False
            for key in (
                "sourceFileContentIncluded",
                "sourceFilePathsIncluded",
                "storageLocatorsIncluded",
                "unpublishedDocumentContentIncluded",
            )
        ):
            raise LocalRuntimeError(
                502,
                "cloud_knowledge_boundary_invalid",
                "组织云项目知识响应未证明材料边界",
            )
        forbidden_keys = {
            "path",
            "sourcePath",
            "sourceLocator",
            "storageKey",
            "markdownContent",
            "contentMarkdown",
            "contentJson",
            "rawContent",
            "fileContent",
        }
        items = payload.get("organizationSharedKnowledge")
        if not isinstance(items, list):
            raise LocalRuntimeError(
                502,
                "cloud_knowledge_invalid",
                "组织云项目知识响应结构不正确",
            )
        organization_state = (
            (payload.get("state") or {}).get("organizationShared")
            if isinstance(payload.get("state"), dict)
            else None
        )
        if organization_state not in {"ready", "empty"}:
            raise LocalRuntimeError(
                502,
                "cloud_knowledge_invalid",
                "组织云项目知识响应状态不正确",
            )
        for item in items:
            if (
                not isinstance(item, dict)
                or item.get("sourceScope") != "organization_shared"
                or not str(item.get("sourceId") or "")
                or not str(item.get("summary") or "")
                or forbidden_keys.intersection(item)
            ):
                raise LocalRuntimeError(
                    502,
                    "cloud_knowledge_invalid",
                    "组织云项目知识包含越界或不完整材料",
                )

    def project_knowledge_context(
        self,
        project_id: str,
        *,
        workspace_context: WorkspaceContext | None = None,
    ) -> dict[str, Any]:
        context = workspace_context or self._current_context(require_ready=True)
        snapshot = self.business_snapshot(
            refresh=False,
            workspace_context=context,
        )
        projected_project = self._project_from_snapshot(snapshot, project_id)
        local_materials = self._local_project_materials(
            sandbox_id=context.sandbox_id,
            project_id=project_id,
        )
        local_items = local_materials["items"]
        local_state = (
            "failed_retryable"
            if local_materials["invalidCount"]
            else "ready"
            if local_items
            else "empty"
        )

        cloud_payload: dict[str, Any] | None = None
        cloud_state = "not_connected"
        cloud_message = ""
        try:
            cloud_payload, current = self._authenticated_cloud_call(
                context,
                lambda client, session: client.project_knowledge_context(
                    session.access_token,
                    project_id,
                ),
            )
            if not self._same_workspace_identity(context, current):
                raise LocalRuntimeError(
                    409,
                    "workspace_context_changed",
                    "项目知识查询期间工作空间身份发生变化",
                )
            self._validate_cloud_project_knowledge(
                cloud_payload,
                cloud_instance_id=context.cloud_instance_id,
                organization_id=context.organization_id,
                project_id=project_id,
            )
            cloud_state = str(
                (cloud_payload.get("state") or {}).get("organizationShared")
                or "empty"
            )
            cloud_message = str(
                (cloud_payload.get("state") or {}).get("message") or ""
            )
        except LocalRuntimeError as exc:
            if exc.code in {"project_missing", "cloud_identity_mismatch"}:
                raise
            if exc.status_code == 404 and exc.code in {
                "cloud_request_failed",
                "capability_not_connected",
            }:
                cloud_state = "not_connected"
                cloud_message = "组织云项目知识查询尚未部署"
            elif exc.code == "failed_retryable" or exc.status_code >= 500:
                cloud_state = "failed_retryable"
                cloud_message = exc.message
            else:
                raise

        organization_items = (
            list(cloud_payload.get("organizationSharedKnowledge") or [])
            if cloud_payload
            else []
        )
        project = (
            dict(cloud_payload["project"])
            if cloud_payload and isinstance(cloud_payload.get("project"), dict)
            else {
                "projectId": projected_project.get("projectId"),
                "name": projected_project.get("name"),
                "summary": projected_project.get("summary") or "",
                "lifecycleState": (
                    projected_project.get("lifecycleState") or "active"
                ),
                "version": int(projected_project.get("version") or 1),
                "updatedAt": projected_project.get("updatedAt"),
            }
        )
        source_states = {cloud_state, local_state}
        if source_states.issubset({"ready", "empty"}):
            overall_state = (
                "ready" if organization_items or local_items else "empty"
            )
        elif "blocked" in source_states:
            overall_state = "blocked"
        elif "failed_retryable" in source_states:
            overall_state = "failed_retryable"
        else:
            overall_state = "not_connected"

        return {
            "sandboxId": context.sandbox_id,
            "cloudInstanceId": context.cloud_instance_id,
            "organizationId": context.organization_id,
            "project": project,
            "organizationSharedKnowledge": organization_items,
            "localPrivateKnowledge": local_items,
            "materialBoundary": {
                "organizationSharedSource": "strict_organization_cloud_v2",
                "localPrivateSource": "current_device_managed_storage",
                "cloudSourceFileContentIncluded": False,
                "cloudSourceFilePathsIncluded": False,
                "localPrivateUploadedToOrganizationCloud": False,
                "localSourcePathsIncludedInContext": False,
            },
            "counts": {
                "organizationShared": len(organization_items),
                "localPrivate": len(local_items),
                "projectMetadata": 1,
                "localRetrievalReady": sum(
                    1
                    for item in local_items
                    if item.get("processingState") != "metadata_only"
                ),
                "localMetadataOnly": sum(
                    1
                    for item in local_items
                    if item.get("processingState") == "metadata_only"
                ),
            },
            "state": {
                "overall": overall_state,
                "organizationShared": cloud_state,
                "localPrivate": local_state,
                "organizationSharedMessage": cloud_message,
                "localPrivateMessage": (
                    f"{local_materials['invalidCount']} 条本机摘要需要重建"
                    if local_materials["invalidCount"]
                    else ""
                ),
            },
        }

    def task_detail(self, task_id: str) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        result, _ = self._authenticated_cloud_call(
            context,
            lambda client, session: client.task_detail(
                session.access_token,
                task_id,
            ),
        )
        return result

    def cloud_query(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
    ) -> Any:
        context = self._current_context(require_ready=True)
        result, _ = self._authenticated_cloud_call(
            context,
            lambda client, session: client.request_v2(
                "GET",
                path,
                access_token=session.access_token,
                query_params=dict(query or {}),
                allow_array=True,
            ),
        )
        return result

    def cloud_command(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
        refresh_business: bool = True,
    ) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        result, captured = self._authenticated_cloud_call(
            context,
            lambda client, session: client.request_v2(
                method,
                path,
                access_token=session.access_token,
                json_body=dict(payload),
                idempotency_key=idempotency_key,
            ),
        )
        if refresh_business:
            self._sync_business_for_context(captured)
        return result

    def create_event_line(
        self,
        *,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        result, current = self._authenticated_cloud_call(
            context,
            lambda client, session: client.create_event_line(
                session.access_token,
                payload,
                idempotency_key=idempotency_key,
            ),
        )
        self._sync_business_for_context(current)
        return result

    def task_command(
        self,
        command: str,
        *,
        task_id: str | None,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        def execute(client: CloudClient, session: WorkspaceContext):
            if command == "create":
                return client.create_task(
                    session.access_token,
                    payload,
                    idempotency_key=idempotency_key,
                )
            if command == "update" and task_id:
                return client.update_task(
                    session.access_token,
                    task_id,
                    payload,
                    idempotency_key=idempotency_key,
                )
            if command in {"complete", "restore"} and task_id:
                return client.transition_task(
                    session.access_token,
                    task_id,
                    command,
                    payload,
                    idempotency_key=idempotency_key,
                )
            if command == "inbox_handle" and task_id:
                return client.handle_task_inbox(
                    session.access_token,
                    task_id,
                    payload,
                    idempotency_key=idempotency_key,
                )
            raise LocalRuntimeError(404, "task_command_unknown", "未知任务命令")

        result, current = self._authenticated_cloud_call(context, execute)
        # The context is captured before the request. A workspace switch while
        # the cloud command is in flight cannot redirect the refreshed mirror.
        self._sync_business_for_context(current)
        return result

    def workbench_chat(
        self,
        *,
        project_id: str | None,
        question: str,
        mode: str,
        private_context_items: list[Mapping[str, Any]] | None = None,
        history_messages: list[Mapping[str, str]] | None = None,
        writing_style: str | None = None,
        deep_thinking: bool = False,
        source_manifest_extra: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        source_manifest_extra = dict(source_manifest_extra or {})
        private_context_items = list(private_context_items or [])
        history_messages = list(history_messages or [])
        operation_key = str(source_manifest_extra.get("operationKey") or "")
        snapshot = self.business_snapshot(
            refresh=False,
            workspace_context=context,
        )
        if project_id and not any(
            str(project.get("projectId") or "") == project_id
            for project in snapshot.get("projects") or []
        ):
            raise LocalRuntimeError(404, "project_missing", "当前工作空间没有该项目")
        project = next(
            (
                item
                for item in snapshot.get("projects") or []
                if str(item.get("projectId") or "") == str(project_id or "")
            ),
            None,
        )
        if operation_key:
            existing = next(
                (
                    item
                    for item in snapshot.get("aiAnswers") or []
                    if str(item.get("projectId") or "") == str(project_id or "")
                    and str(
                        (item.get("sourceManifest") or {}).get("operationKey")
                        or ""
                    )
                    == operation_key
                ),
                None,
            )
            if existing is not None:
                return {"answer": existing, "idempotentReplay": True}
        knowledge_context = (
            self.project_knowledge_context(
                project_id,
                workspace_context=context,
            )
            if project_id
            else None
        )
        _, ai_candidates, routing_mode = self._ai_runtime_candidates(
            context,
            capability="deep_analysis",
        )
        documents = [
            item
            for item in snapshot.get("documents") or []
            if not project_id or item.get("projectId") == project_id
        ][:20]
        context_lines = []
        if project:
            context_lines.append(
                f"当前项目：{project.get('name') or ''}。"
                f"{project.get('summary') or ''}"
            )
        if mode == "balanced" and documents:
            context_lines.append(
                "当前项目资料目录："
                + "；".join(str(item.get("title") or "") for item in documents)
            )
        knowledge_items: list[dict[str, Any]] = []
        if knowledge_context:
            knowledge_items = [
                item
                for key in (
                    "organizationSharedKnowledge",
                    "localPrivateKnowledge",
                )
                for item in knowledge_context.get(key) or []
                if isinstance(item, dict)
                and str(item.get("summary") or "").strip()
            ][:40]
        if knowledge_items:
            context_lines.append(
                "当前项目已提炼知识（只含摘要，不含源文件正文或路径）：\n"
                + "\n".join(
                    f"- {str(item.get('title') or item.get('sourceDescription') or '项目知识').strip()}: "
                    f"{str(item.get('summary') or '').strip()[:2000]}"
                    for item in knowledge_items
                )
            )
        if private_context_items:
            context_lines.append(
                "用户本轮明确选择的本机资料正文：\n"
                + "\n\n".join(
                    f"【{str(item.get('title') or '本机资料')}】\n"
                    f"{str(item.get('content') or '')[:40_000]}"
                    for item in private_context_items[:8]
                )
            )
        if writing_style:
            context_lines.append(
                "本轮写作风格要求：\n" + writing_style[:6000]
            )
        system_prompt = (
            "你是益语智库工作台助手。直接、准确、可执行地回答用户问题。"
            "不要声称看过没有提供正文的资料。"
        )
        if context_lines:
            system_prompt += "\n" + "\n".join(context_lines)
        if deep_thinking:
            system_prompt += (
                "\n请先在内部核对事实边界、冲突和缺口，再给出结构化结论；"
                "不要输出思维过程。"
            )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]
        messages.extend(
            {
                "role": (
                    "assistant"
                    if str(item.get("role") or "") == "assistant"
                    else "user"
                ),
                "content": str(item.get("content") or "")[:12_000],
            }
            for item in history_messages[-8:]
            if str(item.get("content") or "").strip()
        )
        messages.append({"role": "user", "content": question.strip()})
        answer, selected_candidate = self._invoke_ai_chat(
            ai_candidates,
            messages=messages,
            temperature=(
                0.8 if mode == "creative" else 0.1 if mode == "strict" else 0.3
            ),
        )
        model_name = str(selected_candidate["modelName"])
        active_context = self._current_context(require_ready=True)
        if not self._same_workspace_identity(context, active_context):
            raise LocalRuntimeError(
                409,
                "workspace_context_changed",
                "回答生成期间工作空间已切换，本次结果未保存，请在当前工作空间重试",
            )
        source_manifest = {
            "mode": mode,
            "projectId": project_id,
            "documentIds": [item.get("documentId") for item in documents],
            **source_manifest_extra,
            "documentContentIncluded": bool(private_context_items),
            "selectedDocumentContentCount": len(private_context_items),
            "deepThinkingRequested": deep_thinking,
            "projectKnowledgeSummaryCount": len(knowledge_items),
            "projectKnowledgeState": (
                dict(knowledge_context.get("state") or {})
                if knowledge_context
                else {}
            ),
            "projectKnowledgeContentPersisted": False,
            "aiRouteProfile": str(selected_candidate["profileKey"]),
            "aiRoutingMode": routing_mode,
        }
        saved, _ = self._authenticated_cloud_call(
            context,
            lambda client, session: client.save_workbench_answer(
                session.access_token,
                {
                    "projectId": project_id,
                    "question": question.strip(),
                    "answerMarkdown": answer,
                    "sourceManifest": source_manifest,
                    "modelName": model_name,
                },
                idempotency_key=idempotency_key or new_id(),
            ),
        )
        return saved

    def private_ai_completion(
        self,
        *,
        system_prompt: str,
        prompt: str,
        creativity_mode: str = "balanced",
        capability: str = "deep_analysis",
    ) -> dict[str, Any]:
        """Run the configured organization model without persisting private input.

        Document editor selections and member-local source text may be sent to
        the organization's configured model, but they must not be copied into
        the organization cloud business database as an ``ai_answer``.
        """
        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            raise LocalRuntimeError(422, "ai_prompt_required", "缺少要处理的文字")
        context = self._current_context(require_ready=True)
        _, candidates, routing_mode = self._ai_runtime_candidates(
            context,
            capability=capability,
        )
        content, selected_candidate = self._invoke_ai_chat(
            candidates,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt.strip(),
                },
                {"role": "user", "content": normalized_prompt},
            ],
            temperature=(
                0.8 if creativity_mode == "creative" else 0.1
                if creativity_mode == "strict"
                else 0.3
            ),
        )
        return {
            "content": content,
            "modelName": str(selected_candidate["modelName"]),
            "routeProfile": str(selected_candidate["profileKey"]),
            "routingMode": routing_mode,
            "sourceScope": "member_local_private_request",
            "persistedToOrganizationCloud": False,
        }

    def diagnostics(self) -> dict[str, Any]:
        identity = database_identity(self.database_path, "local")
        with self._connection() as connection:
            tables = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                ).fetchall()
            ]
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        return {
            "database": {
                "schemaFamily": identity.schema_family,
                "contractVersion": identity.contract_version,
                "manifestHash": identity.manifest_hash,
                "databaseGenerationId": identity.database_generation_id,
                "buildId": identity.build_id,
                "tableCount": len(tables),
                "tables": tables,
                "quickCheck": quick_check,
            },
            "workspaces": self.list_workspaces(),
        }
