from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Mapping

from strict_common.ids import canonical_json, sha256_text, utc_now
from strict_common.security import payload_fingerprint

from ..repository import CloudRepository, RepositoryError, SessionIdentity


SCHEMA = "yiyu.member-memory-safe-manifest.v1"
MEDIA_TYPE = "application/vnd.yiyu.member-memory-safe-manifest+json"
STORAGE_KIND = "member_memory_safe_manifest"
ALLOWED_MEMORY_KINDS = frozenset({"explicit_memory", "favorite", "correction"})
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _record_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_" + sha256_text("\x1f".join(parts))[:30]


def _manifest_id(identity: SessionIdentity, project_id: str) -> str:
    return _record_id(
        "memory_manifest",
        identity.scope_id,
        identity.membership_id,
        project_id,
    )


def _reconciliation_id(identity: SessionIdentity, project_id: str) -> str:
    return _record_id(
        "recon",
        "member_memory_safe_manifest",
        identity.scope_id,
        identity.membership_id,
        project_id,
    )


def _receipt(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _validate_timestamp(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 40:
        raise RepositoryError(422, "memory_manifest_updated_at_invalid", "记忆更新时间无效")
    try:
        datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RepositoryError(422, "memory_manifest_updated_at_invalid", "记忆更新时间无效") from exc
    return normalized


def _validate_entries(raw_entries: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_entries, list) or len(raw_entries) > 5_000:
        raise RepositoryError(422, "memory_manifest_entries_invalid", "记忆安全摘要清单无效")
    entries: dict[str, dict[str, Any]] = {}
    allowed_fields = {"memoryId", "memoryKind", "version", "contentHash", "updatedAt"}
    for raw in raw_entries:
        if not isinstance(raw, Mapping) or set(raw) != allowed_fields:
            raise RepositoryError(
                422,
                "memory_manifest_entry_boundary_violation",
                "记忆同步只接受ID、类型、版本、哈希和更新时间",
            )
        memory_id = str(raw.get("memoryId") or "").strip()
        memory_kind = str(raw.get("memoryKind") or "").strip()
        content_hash = str(raw.get("contentHash") or "").strip().lower()
        try:
            version = int(raw.get("version"))
        except (TypeError, ValueError) as exc:
            raise RepositoryError(422, "memory_manifest_version_invalid", "记忆版本无效") from exc
        if not memory_id or len(memory_id) > 180 or any(ch.isspace() for ch in memory_id):
            raise RepositoryError(422, "memory_manifest_id_invalid", "记忆标识无效")
        if memory_kind not in ALLOWED_MEMORY_KINDS:
            raise RepositoryError(422, "memory_manifest_kind_invalid", "记忆类型无效")
        if version < 1:
            raise RepositoryError(422, "memory_manifest_version_invalid", "记忆版本无效")
        if not _HASH_RE.fullmatch(content_hash):
            raise RepositoryError(422, "memory_manifest_hash_invalid", "记忆哈希无效")
        entry = {
            "memoryId": memory_id,
            "memoryKind": memory_kind,
            "version": version,
            "contentHash": content_hash,
            "updatedAt": _validate_timestamp(raw.get("updatedAt")),
        }
        previous = entries.get(memory_id)
        if previous is not None and previous != entry:
            raise RepositoryError(409, "memory_manifest_duplicate_conflict", "同一记忆标识出现冲突版本")
        entries[memory_id] = entry
    return [entries[key] for key in sorted(entries)]


def _counts(entries: list[Mapping[str, Any]]) -> dict[str, int]:
    result = {"explicitMemory": 0, "favorite": 0, "correction": 0}
    for entry in entries:
        key = {
            "explicit_memory": "explicitMemory",
            "favorite": "favorite",
            "correction": "correction",
        }.get(str(entry.get("memoryKind") or ""))
        if key:
            result[key] += 1
    return result


def _dto(
    identity: SessionIdentity,
    project_id: str,
    *,
    entries: list[dict[str, Any]],
    version: int,
    updated_at: str | None,
    state: str,
    idempotent_replay: bool = False,
) -> dict[str, Any]:
    digest = sha256_text(canonical_json(entries))
    return {
        "clientId": project_id,
        "cloudState": state,
        "manifestVersion": max(0, int(version)),
        "memoryCount": len(entries),
        "counts": _counts(entries),
        "memoryDigest": digest,
        "entries": entries,
        "updatedAt": updated_at,
        "idempotentReplay": idempotent_replay,
        "boundary": {
            "l0ConversationIncluded": False,
            "answerBodyIncluded": False,
            "fileBodyIncluded": False,
            "localPathIncluded": False,
            "secretIncluded": False,
            "entryFields": [
                "memoryId",
                "memoryKind",
                "version",
                "contentHash",
                "updatedAt",
            ],
        },
    }


def get_memory_manifest(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        repository._require_project_access(connection, identity, project_id=project_id, capability="read")  # noqa: SLF001
        row = connection.execute(
            "SELECT receipt,verified_at FROM object_manifests WHERE id=? AND scope_id=? "
            "AND storage_kind=? AND holder_instance_id=? AND lifecycle_state='active'",
            (
                _manifest_id(identity, project_id),
                identity.scope_id,
                STORAGE_KIND,
                identity.membership_id,
            ),
        ).fetchone()
    if row is None:
        return _dto(
            identity,
            project_id,
            entries=[],
            version=0,
            updated_at=None,
            state="not_connected",
        )
    receipt = _receipt(row["receipt"])
    entries = _validate_entries(receipt.get("entries") or [])
    return _dto(
        identity,
        project_id,
        entries=entries,
        version=int(receipt.get("manifestVersion") or 0),
        updated_at=str(row["verified_at"] or "") or None,
        state="ready",
    )


def put_memory_manifest(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    project_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if set(payload) - {"entries", "expectedVersion"}:
        raise RepositoryError(
            422,
            "memory_manifest_payload_boundary_violation",
            "记忆同步请求包含不允许上传的字段",
        )
    entries = _validate_entries(payload.get("entries"))
    try:
        expected_version = int(payload.get("expectedVersion") or 0)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(422, "memory_manifest_expected_version_invalid", "记忆清单版本无效") from exc
    if expected_version < 0:
        raise RepositoryError(422, "memory_manifest_expected_version_invalid", "记忆清单版本无效")
    normalized_payload = {"entries": entries, "expectedVersion": expected_version}
    payload_hash = payload_fingerprint(normalized_payload)
    command_type = "member_memory.safe_manifest.synced"
    operation_id = _record_id(
        "op",
        "member_memory_safe_manifest",
        identity.scope_id,
        idempotency_key,
    )
    manifest_id = _manifest_id(identity, project_id)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            repository._require_project_access(connection, identity, project_id=project_id, capability="read")  # noqa: SLF001
            replay = connection.execute(
                "SELECT payload_hash,result_object_manifest_id FROM idempotency_records "
                "WHERE scope_id=? AND idempotency_key=?",
                (identity.scope_id, idempotency_key),
            ).fetchone()
            if replay is not None:
                if str(replay["payload_hash"] or "") != payload_hash:
                    raise RepositoryError(409, "memory_manifest_idempotency_conflict", "相同操作标识对应了不同记忆清单")
                result_row = connection.execute(
                    "SELECT receipt FROM object_manifests WHERE id=? AND scope_id=?",
                    (str(replay["result_object_manifest_id"] or ""), identity.scope_id),
                ).fetchone()
                result = _receipt(result_row["receipt"] if result_row else None)
                if not result:
                    raise RepositoryError(409, "memory_manifest_receipt_missing", "记忆同步回执不完整")
                result["idempotentReplay"] = True
                connection.commit()
                return result

            current = connection.execute(
                "SELECT receipt FROM object_manifests WHERE id=? AND scope_id=? "
                "AND storage_kind=? AND holder_instance_id=? AND lifecycle_state='active'",
                (manifest_id, identity.scope_id, STORAGE_KIND, identity.membership_id),
            ).fetchone()
            current_receipt = _receipt(current["receipt"] if current else None)
            current_version = int(current_receipt.get("manifestVersion") or 0)
            if current_version != expected_version:
                raise RepositoryError(409, "memory_manifest_version_conflict", "云端记忆摘要已变化，请刷新后重试")
            next_version = current_version + 1
            now = utc_now()
            receipt_payload = {
                "schema": SCHEMA,
                "manifestVersion": next_version,
                "entries": entries,
            }
            receipt_json = canonical_json(receipt_payload)
            digest = sha256_text(canonical_json(entries))
            holder_role = f"member_memory_safe_manifest:{project_id}"
            if current is None:
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,lifecycle_state,"
                    "receipt,holder_role,holder_instance_id,storage_kind,byte_size,media_type,"
                    "availability_state,receipt_hash,created_at,verified_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?,NULL,?,'active',?,?,?,?,?,?, 'ready',?,?,?,NULL,'cloud',?)",
                    (
                        manifest_id,
                        identity.scope_id,
                        digest,
                        receipt_json,
                        holder_role,
                        identity.membership_id,
                        STORAGE_KIND,
                        len(receipt_json.encode("utf-8")),
                        MEDIA_TYPE,
                        sha256_text(receipt_json),
                        now,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE object_manifests SET content_hash=?,receipt=?,holder_role=?,byte_size=?,"
                    "availability_state='ready',receipt_hash=?,verified_at=?,authority_role='cloud',"
                    "origin_instance_id=? WHERE id=? AND scope_id=?",
                    (
                        digest,
                        receipt_json,
                        holder_role,
                        len(receipt_json.encode("utf-8")),
                        sha256_text(receipt_json),
                        now,
                        identity.cloud_instance_id,
                        manifest_id,
                        identity.scope_id,
                    ),
                )
            result = _dto(
                identity,
                project_id,
                entries=entries,
                version=next_version,
                updated_at=now,
                state="ready",
            )
            result_json = canonical_json(result)
            result_manifest_id = _record_id("memory_sync_receipt", operation_id)
            connection.execute(
                "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,lifecycle_state,"
                "receipt,holder_role,holder_instance_id,storage_kind,byte_size,media_type,"
                "availability_state,receipt_hash,created_at,verified_at,deleted_at,authority_role,"
                "origin_instance_id) VALUES (?,?,NULL,?,'active',?,'command_receipt',?,"
                "'command_receipt',?,'application/vnd.yiyu.command-receipt+json','ready',?,?,?,NULL,'cloud',?)",
                (
                    result_manifest_id,
                    identity.scope_id,
                    sha256_text(result_json),
                    result_json,
                    identity.membership_id,
                    len(result_json.encode("utf-8")),
                    sha256_text(result_json),
                    now,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            result_hash = sha256_text(result_json)
            connection.execute(
                "INSERT INTO idempotency_records (id,scope_id,idempotency_key,payload_hash,result_hash,"
                "expires_at,result_object_manifest_id,status,created_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?,?,NULL,?,'completed',?,'cloud',?)",
                (
                    _record_id("idempotency", operation_id),
                    identity.scope_id,
                    idempotency_key,
                    payload_hash,
                    result_hash,
                    result_manifest_id,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,aggregate_type,aggregate_id,"
                "command_type,actor_principal_id,expected_aggregate_version,status,actor_membership_id,"
                "payload_object_manifest_id,payload_hash,submitted_at,settled_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?, 'object_manifest',?,?,?,?, 'completed',?,?,?,?,?,'cloud',?)",
                (
                    _record_id("cmd", operation_id),
                    identity.scope_id,
                    operation_id,
                    idempotency_key,
                    manifest_id,
                    command_type,
                    identity.principal_id,
                    expected_version,
                    identity.membership_id,
                    result_manifest_id,
                    payload_hash,
                    now,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO inbox_receipts (id,scope_id,operation_id,payload_hash,result_status,"
                "processed_at,result_hash,source_instance_id,origin_instance_id,created_at,integrity_hash,"
                "authority_role) VALUES (?,?,?,?, 'completed',?,?,?,?,?,?, 'cloud')",
                (
                    _record_id("inbox", operation_id),
                    identity.scope_id,
                    operation_id,
                    payload_hash,
                    now,
                    result_hash,
                    identity.membership_id,
                    identity.cloud_instance_id,
                    now,
                    sha256_text(f"{operation_id}|{payload_hash}|{result_hash}|{now}"),
                ),
            )
            reconciliation_id = _reconciliation_id(identity, project_id)
            connection.execute(
                "INSERT INTO reconciliation_runs (id,scope_id,operation_id,registry_state_id,mismatch_count,"
                "status,reconciliation_kind,target_instance_id,result_object_manifest_id,started_at,"
                "completed_at,version,lifecycle_state,created_at,updated_at,deleted_at,authority_role,"
                "origin_instance_id) VALUES (?,?,?,NULL,0,'completed','member_memory_safe_manifest_v1',"
                "?,?,?, ?,?,'active',?,?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET operation_id=excluded.operation_id,"
                "mismatch_count=0,status='completed',target_instance_id=excluded.target_instance_id,"
                "result_object_manifest_id=excluded.result_object_manifest_id,started_at=excluded.started_at,"
                "completed_at=excluded.completed_at,version=excluded.version,lifecycle_state='active',"
                "updated_at=excluded.updated_at,deleted_at=NULL,authority_role='cloud',"
                "origin_instance_id=excluded.origin_instance_id",
                (
                    reconciliation_id,
                    identity.scope_id,
                    operation_id,
                    identity.membership_id,
                    manifest_id,
                    now,
                    now,
                    next_version,
                    now,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            event_hash = sha256_text(f"{manifest_id}|{next_version}|{digest}")
            connection.execute(
                "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,event_type,status,"
                "aggregate_type,aggregate_id,event_object_manifest_id,event_hash,available_at,published_at,"
                "authority_role,origin_instance_id) VALUES (?,?,?,?,'member_memory.safe_manifest.synced',"
                "'pending','object_manifest',?,?,?,?,NULL,'cloud',?)",
                (
                    _record_id("outbox", operation_id),
                    identity.scope_id,
                    operation_id,
                    next_version,
                    manifest_id,
                    result_manifest_id,
                    event_hash,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (id,scope_id,operation_id,actor_id,action,event_hash,"
                "actor_membership_id,target_resource_id,details_object_manifest_id,occurred_at,"
                "origin_instance_id,created_at,integrity_hash,authority_role) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'cloud')",
                (
                    _record_id("audit", operation_id),
                    identity.scope_id,
                    operation_id,
                    identity.principal_id,
                    command_type,
                    event_hash,
                    identity.membership_id,
                    project_id,
                    result_manifest_id,
                    now,
                    identity.cloud_instance_id,
                    now,
                    sha256_text(f"{operation_id}|{event_hash}|{now}"),
                ),
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
