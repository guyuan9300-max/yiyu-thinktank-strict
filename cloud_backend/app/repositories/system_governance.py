from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from strict_common.ids import canonical_json, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity


def _id(prefix: str, *parts: str) -> str:
    return prefix + "_" + sha256_text("\x1f".join(parts))[:28]


class SystemGovernanceRepository:
    """Strict recovery/release evidence; never reads a frozen business table."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _require_admin(identity: SessionIdentity) -> None:
        if identity.system_role != "admin":
            raise RepositoryError(403, "admin_required", "该操作仅组织管理员可执行")

    def _backup_payload(
        self, connection: sqlite3.Connection, recovery_set_id: str
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT r.*,b.id AS backup_id,b.component_kind,b.checksum,b.retention_until,"
            "b.verified,b.backup_ref,b.created_at AS backup_created_at,m.status AS manifest_status,"
            "m.result_hash FROM recovery_sets r JOIN backup_catalog b "
            "ON b.recovery_set_id=r.id LEFT JOIN recovery_manifests m "
            "ON m.recovery_set_id=r.id WHERE r.id=? AND r.lifecycle_state='active'",
            (recovery_set_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "recoverySetId": str(row["id"]),
            "backupId": str(row["backup_id"]),
            "backupPath": f"strict-recovery://{row['id']}/{row['backup_id']}",
            "componentKind": str(row["component_kind"] or "cloud_database"),
            "checksum": str(row["checksum"] or ""),
            "retentionUntil": row["retention_until"],
            "databaseVerified": bool(row["verified"]),
            "wholeSystemVerified": str(row["manifest_status"] or "") == "verified",
            "status": str(row["status"] or "created"),
            "createdAt": str(row["created_at"] or row["backup_created_at"] or ""),
            "verifiedAt": row["verified_at"],
            "resultHash": row["result_hash"],
            "retryable": False,
        }

    def list_recovery_sets(
        self, identity: SessionIdentity, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        self._require_admin(identity)
        with self.repository._connection() as connection:  # noqa: SLF001
            ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM recovery_sets WHERE lifecycle_state='active' "
                    "ORDER BY created_at DESC,id DESC LIMIT ?",
                    (max(1, min(int(limit), 100)),),
                ).fetchall()
            ]
            return [
                item
                for recovery_id in ids
                if (item := self._backup_payload(connection, recovery_id)) is not None
            ]

    def _record_command(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        operation_id: str,
        idempotency_key: str,
        payload_hash: str,
        aggregate_type: str,
        aggregate_id: str,
        command_type: str,
        aggregate_version: int,
        result: Mapping[str, Any],
        now: str,
    ) -> None:
        receipt = canonical_json(dict(result))
        result_hash = sha256_text(receipt)
        manifest_id = _id("manifest", operation_id, "recovery")
        connection.execute(
            "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,"
            "lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,byte_size,"
            "media_type,availability_state,receipt_hash,created_at,verified_at,deleted_at,"
            "authority_role,origin_instance_id) VALUES (?,?,NULL,?,'active',?,'cloud',?,"
            "'recovery_receipt',?,'application/json','verified',?,?,?,NULL,'cloud',?)",
            (
                manifest_id,
                identity.scope_id,
                result_hash,
                receipt,
                identity.cloud_instance_id,
                len(receipt.encode("utf-8")),
                result_hash,
                now,
                now,
                identity.cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO idempotency_records (id,scope_id,idempotency_key,payload_hash,"
            "result_hash,expires_at,result_object_manifest_id,status,created_at,authority_role,"
            "origin_instance_id) VALUES (?,?,?,?,?,'9999-12-31T23:59:59.999Z',?,'settled',?,"
            "'cloud',?)",
            (
                _id("idem", operation_id), identity.scope_id, idempotency_key,
                payload_hash, result_hash, manifest_id, now, identity.cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,aggregate_type,"
            "aggregate_id,command_type,actor_principal_id,expected_aggregate_version,"
            "device_command_sequence,status,actor_membership_id,payload_object_manifest_id,"
            "payload_hash,submitted_at,settled_at,authority_role,origin_instance_id) VALUES "
            "(?,?,?,?, ?,?,?,?,NULL,NULL,'settled',"
            "?,?,?,?,?,'cloud',?)",
            (
                _id("cmd", operation_id), identity.scope_id, operation_id,
                idempotency_key, aggregate_type, aggregate_id, command_type,
                identity.principal_id,
                identity.membership_id, manifest_id, payload_hash, now, now,
                identity.cloud_instance_id,
            ),
        )
        event_hash = sha256_text(f"{operation_id}|{aggregate_id}|{result_hash}")
        connection.execute(
            "INSERT INTO audit_events (id,scope_id,operation_id,actor_id,action,event_hash,"
            "actor_membership_id,target_resource_id,details_object_manifest_id,occurred_at,"
            "origin_instance_id,created_at,integrity_hash,authority_role) VALUES "
            "(?,?,?,?, ?,?,?,?,?,?,?,?,?,'cloud')",
            (
                _id("audit", operation_id), identity.scope_id, operation_id,
                identity.principal_id, command_type, event_hash, identity.membership_id,
                None, manifest_id, now, identity.cloud_instance_id, now,
                event_hash,
            ),
        )
        connection.execute(
            "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,event_type,"
            "status,aggregate_type,aggregate_id,event_object_manifest_id,event_hash,available_at,"
            "published_at,authority_role,origin_instance_id) VALUES "
            "(?,?,?,?,?,'published',?,?,?,?,?,?,"
            "'cloud',?)",
            (
                _id("evt", operation_id), identity.scope_id, operation_id,
                aggregate_version, command_type, aggregate_type, aggregate_id,
                manifest_id, event_hash, now, now,
                identity.cloud_instance_id,
            ),
        )

    def create_database_backup(
        self,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
        retention_days: int = 30,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        retention_days = max(1, min(int(retention_days), 3650))
        payload_hash = sha256_text(canonical_json({"retentionDays": retention_days}))
        operation_id = _id("op", identity.scope_id, idempotency_key, "recovery")
        recovery_set_id = _id("recovery", operation_id)
        with self.repository._connection() as connection:  # noqa: SLF001
            existing = self._backup_payload(connection, recovery_set_id)
            if existing is not None:
                return {**existing, "idempotentReplay": True}

        backup_directory = self.repository.database_path.parent / "strict-recovery"
        backup_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup_path = backup_directory / f"{recovery_set_id}.sqlite3"
        temporary_path = backup_directory / f".{recovery_set_id}.tmp"
        started = datetime.now(timezone.utc)
        destination: sqlite3.Connection | None = None
        try:
            with self.repository._connection() as source:  # noqa: SLF001
                destination = sqlite3.connect(temporary_path)
                source.backup(destination)
                destination.close()
                destination = None
            os.chmod(temporary_path, 0o600)
            with sqlite3.connect(f"file:{temporary_path}?mode=ro", uri=True) as check:
                if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RepositoryError(500, "backup_quick_check_failed", "备份完整性检查失败")
                if check.execute("PRAGMA foreign_key_check").fetchall():
                    raise RepositoryError(500, "backup_foreign_key_failed", "备份外键检查失败")
            checksum = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
            os.replace(temporary_path, backup_path)
            os.chmod(backup_path, 0o600)
        finally:
            if destination is not None:
                destination.close()
            temporary_path.unlink(missing_ok=True)

        now = utc_now()
        retention_until = (
            datetime.now(timezone.utc) + timedelta(days=retention_days)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        backup_id = _id("backup", recovery_set_id, checksum)
        recovery_manifest_id = _id("recovery_manifest", recovery_set_id)
        elapsed = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                schema = connection.execute(
                    "SELECT id FROM schema_versions WHERE status='active' "
                    "ORDER BY activated_at DESC,id DESC LIMIT 1"
                ).fetchone()
                schema_version_id = str(schema["id"]) if schema is not None else None
                result = {
                    "recoverySetId": recovery_set_id,
                    "backupId": backup_id,
                    "backupPath": f"strict-recovery://{recovery_set_id}/{backup_id}",
                    "componentKind": "cloud_database",
                    "checksum": f"sha256:{checksum}",
                    "retentionUntil": retention_until,
                    "databaseVerified": True,
                    "wholeSystemVerified": True,
                    "status": "verified",
                    "createdAt": now,
                    "verifiedAt": now,
                    "resultHash": checksum,
                    "retryable": False,
                    "idempotentReplay": False,
                }
                self._record_command(
                    connection, identity, operation_id=operation_id,
                    idempotency_key=idempotency_key, payload_hash=payload_hash,
                    aggregate_type="recovery_set", aggregate_id=recovery_set_id,
                    command_type="system.recovery_set.create", aggregate_version=1,
                    result=result, now=now,
                )
                steps_manifest_id = _id("manifest", operation_id, "recovery_steps")
                steps = canonical_json(
                    {"steps": ["sqlite_backup", "quick_check", "foreign_key_check", "sha256"],
                     "databasePathExposed": False}
                )
                steps_hash = sha256_text(steps)
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,"
                    "lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,byte_size,"
                    "media_type,availability_state,receipt_hash,created_at,verified_at,deleted_at,"
                    "authority_role,origin_instance_id) VALUES (?,?,NULL,?,'active',?,'cloud',?,"
                    "'recovery_steps',?,'application/json','verified',?,?,?,NULL,'cloud',?)",
                    (
                        steps_manifest_id, identity.scope_id, steps_hash, steps,
                        identity.cloud_instance_id, len(steps.encode("utf-8")), steps_hash,
                        now, now, identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO recovery_sets (id,candidate_version,schema_version_id,"
                    "component_manifest_hash,status,created_at,verified_at,version,lifecycle_state,"
                    "updated_at,deleted_at,authority_role,origin_instance_id) VALUES "
                    "(?,?,?,?, 'verified',?,?,1,'active',?,NULL,'cloud',?)",
                    (
                        recovery_set_id, self.repository.identity.contract_version,
                        schema_version_id, checksum, now, now, now,
                        identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO backup_catalog (id,recovery_set_id,component_kind,checksum,"
                    "retention_until,verified,storage_holder_role,backup_ref,created_at,verified_at,"
                    "version,lifecycle_state,updated_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?, 'cloud_database',?,?,1,'cloud',?,?,?,1,"
                    "'active',?,NULL,'cloud',?)",
                    (
                        backup_id, recovery_set_id, f"sha256:{checksum}", retention_until,
                        str(backup_path), now, now, now, identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO recovery_manifests (id,recovery_set_id,rpo_actual,rto_actual,"
                    "verified_at,status,steps_object_manifest_id,result_hash,version,"
                    "lifecycle_state,created_at,updated_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?,0,?,?,'verified',?,?,1,'active',?,?,NULL,"
                    "'cloud',?)",
                    (
                        recovery_manifest_id, recovery_set_id, elapsed, now,
                        steps_manifest_id, checksum, now, now, identity.cloud_instance_id,
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                backup_path.unlink(missing_ok=True)
                raise

    def list_release_gates(
        self, identity: SessionIdentity, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        self._require_admin(identity)
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT id,candidate_version,recovery_set_id,evidence_version,decision,"
                "owner,decided_at,blocking_reason,evidence_hash,version,created_at,updated_at "
                "FROM release_gates WHERE lifecycle_state='active' "
                "ORDER BY created_at DESC,id DESC LIMIT ?",
                (max(1, min(int(limit), 100)),),
            ).fetchall()
        return [
            {
                "releaseGateId": str(row["id"]),
                "candidateVersion": row["candidate_version"],
                "recoverySetId": row["recovery_set_id"],
                "evidenceVersion": row["evidence_version"],
                "decision": row["decision"],
                "ownerMembershipId": row["owner"],
                "decidedAt": row["decided_at"],
                "blockingReason": row["blocking_reason"],
                "evidenceHash": row["evidence_hash"],
                "version": int(row["version"] or 1),
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def decide_release_gate(
        self,
        identity: SessionIdentity,
        *,
        candidate_version: str,
        recovery_set_id: str,
        evidence_version: str,
        evidence_hash: str,
        decision: str,
        blocking_reason: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        candidate_version = candidate_version.strip()
        recovery_set_id = recovery_set_id.strip()
        evidence_version = evidence_version.strip()
        evidence_hash = evidence_hash.removeprefix("sha256:").strip().lower()
        decision = decision.strip().lower()
        if not candidate_version or not recovery_set_id or not evidence_version:
            raise RepositoryError(422, "release_gate_incomplete", "发布门禁证据不完整")
        if decision not in {"passed", "blocked"}:
            raise RepositoryError(422, "release_gate_decision_invalid", "发布门禁决定无效")
        if len(evidence_hash) != 64 or any(c not in "0123456789abcdef" for c in evidence_hash):
            raise RepositoryError(422, "release_gate_evidence_hash_invalid", "发布证据哈希无效")
        normalized = {
            "candidateVersion": candidate_version,
            "recoverySetId": recovery_set_id,
            "evidenceVersion": evidence_version,
            "evidenceHash": evidence_hash,
            "decision": decision,
            "blockingReason": (blocking_reason or "").strip() or None,
        }
        payload_hash = sha256_text(canonical_json(normalized))
        operation_id = _id("op", identity.scope_id, idempotency_key, "release-gate")
        gate_id = _id("release_gate", operation_id)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT payload_hash FROM idempotency_records WHERE scope_id=? "
                    "AND idempotency_key=?",
                    (identity.scope_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay["payload_hash"]) != payload_hash:
                        raise RepositoryError(409, "idempotency_conflict", "同一幂等键的发布证据不同")
                    row = connection.execute(
                        "SELECT * FROM release_gates WHERE id=?", (gate_id,)
                    ).fetchone()
                    if row is None:
                        raise RepositoryError(503, "release_gate_receipt_incomplete", "发布门禁回执尚未完整落地")
                    connection.commit()
                    return {
                        "releaseGateId": gate_id,
                        **normalized,
                        "ownerMembershipId": row["owner"],
                        "decidedAt": row["decided_at"],
                        "version": int(row["version"] or 1),
                        "idempotentReplay": True,
                    }
                recovery = connection.execute(
                    "SELECT id FROM recovery_sets WHERE id=? AND status='verified' "
                    "AND lifecycle_state='active'",
                    (recovery_set_id,),
                ).fetchone()
                if recovery is None:
                    raise RepositoryError(409, "verified_recovery_required", "发布前必须存在已验证恢复集")
                now = utc_now()
                result = {
                    "releaseGateId": gate_id,
                    **normalized,
                    "ownerMembershipId": identity.membership_id,
                    "decidedAt": now,
                    "version": 1,
                    "idempotentReplay": False,
                }
                self._record_command(
                    connection, identity, operation_id=operation_id,
                    idempotency_key=idempotency_key, payload_hash=payload_hash,
                    aggregate_type="release_gate", aggregate_id=gate_id,
                    command_type="system.release_gate.decided", aggregate_version=1,
                    result=result, now=now,
                )
                connection.execute(
                    "INSERT INTO release_gates (id,candidate_version,recovery_set_id,"
                    "evidence_version,decision,owner,decided_at,blocking_reason,evidence_hash,"
                    "version,lifecycle_state,created_at,updated_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?,?,?,?,?,?,?,?,1,'active',?,?,NULL,'cloud',?)",
                    (
                        gate_id, candidate_version, recovery_set_id, evidence_version,
                        decision, identity.membership_id, now, normalized["blockingReason"],
                        evidence_hash, now, now, identity.cloud_instance_id,
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def list_git_mappings(
        self, identity: SessionIdentity, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        self._require_admin(identity)
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT id,repository_ref,remote_receipt,status,commit_ref,"
                "executed_by_instance_id,created_at,version,updated_at FROM git_mappings "
                "WHERE scope_id=? AND lifecycle_state='active' "
                "ORDER BY created_at DESC,id DESC LIMIT ?",
                (identity.scope_id, max(1, min(int(limit), 100))),
            ).fetchall()
        return [
            {
                "gitMappingId": str(row["id"]),
                "repositoryRef": row["repository_ref"],
                "remoteReceipt": row["remote_receipt"],
                "status": row["status"],
                "commitRef": row["commit_ref"],
                "executedByInstanceId": row["executed_by_instance_id"],
                "createdAt": row["created_at"],
                "version": int(row["version"] or 1),
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def record_git_mapping(
        self,
        identity: SessionIdentity,
        *,
        repository_ref: str,
        commit_ref: str,
        remote_receipt: str,
        status: str,
        executed_by_instance_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        repository_ref = repository_ref.strip()
        commit_ref = commit_ref.strip().lower()
        remote_receipt = remote_receipt.strip()
        status = status.strip().lower()
        executed_by_instance_id = executed_by_instance_id.strip()
        if not repository_ref or repository_ref.startswith(("/", "~")):
            raise RepositoryError(422, "git_repository_ref_invalid", "Git仓库只允许登记无本机路径的稳定引用")
        if len(commit_ref) != 40 or any(c not in "0123456789abcdef" for c in commit_ref):
            raise RepositoryError(422, "git_commit_ref_invalid", "Git提交标识无效")
        if status not in {"succeeded", "blocked", "failed_retryable", "failed"}:
            raise RepositoryError(422, "git_mapping_status_invalid", "Git执行状态无效")
        if not executed_by_instance_id:
            raise RepositoryError(422, "git_executor_required", "缺少Git执行设备标识")
        normalized = {
            "repositoryRef": repository_ref,
            "commitRef": commit_ref,
            "remoteReceipt": remote_receipt,
            "status": status,
            "executedByInstanceId": executed_by_instance_id,
        }
        payload_hash = sha256_text(canonical_json(normalized))
        operation_id = _id("op", identity.scope_id, idempotency_key, "git-mapping")
        mapping_id = _id("git_mapping", operation_id)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT payload_hash FROM idempotency_records WHERE scope_id=? "
                    "AND idempotency_key=?",
                    (identity.scope_id, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay["payload_hash"]) != payload_hash:
                        raise RepositoryError(409, "idempotency_conflict", "同一幂等键的Git回执不同")
                    connection.commit()
                    return {"gitMappingId": mapping_id, **normalized, "version": 1, "idempotentReplay": True}
                now = utc_now()
                result = {"gitMappingId": mapping_id, **normalized, "version": 1, "idempotentReplay": False}
                self._record_command(
                    connection, identity, operation_id=operation_id,
                    idempotency_key=idempotency_key, payload_hash=payload_hash,
                    aggregate_type="git_mapping", aggregate_id=mapping_id,
                    command_type="system.git_mapping.recorded", aggregate_version=1,
                    result=result, now=now,
                )
                connection.execute(
                    "INSERT INTO git_mappings (id,scope_id,external_side_effect_id,"
                    "repository_ref,remote_receipt,status,commit_ref,executed_by_instance_id,"
                    "created_at,version,lifecycle_state,updated_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?,NULL,?,?,?,?,?,?,1,'active',?,NULL,'local',?)",
                    (
                        mapping_id, identity.scope_id, repository_ref, remote_receipt,
                        status, commit_ref, executed_by_instance_id, now, now,
                        identity.cloud_instance_id,
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
