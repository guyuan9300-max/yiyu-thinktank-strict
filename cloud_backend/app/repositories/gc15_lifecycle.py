"""GC-15 legal-hold and purge settlement on the frozen 88-table schema.

This module does not create another delete API.  Domain commands first write
their normal CAS tombstone (for example ``GC04TaskRepository.delete_task``),
then use this settlement layer to prove that protected payload was cleared.
The immutable resource identity and lifecycle evidence remain as tombstones so
late projections cannot resurrect the business object.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_" + sha256_text("\x1f".join(parts))[:30]


class GC15LifecycleRepository:
    """Horizontal lifecycle settlement without owning domain tombstones."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _require_admin(identity: SessionIdentity) -> None:
        if not identity.is_admin:
            raise RepositoryError(403, "gc15_admin_required", "该生命周期操作仅限组织管理员")

    @staticmethod
    def _payload_hash(payload: Mapping[str, Any]) -> str:
        return sha256_text(canonical_json(dict(payload)))

    @staticmethod
    def _receipt(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT i.payload_hash, i.result_hash, m.receipt
            FROM idempotency_records AS i
            LEFT JOIN object_manifests AS m
              ON m.scope_id=i.scope_id AND m.id=i.result_object_manifest_id
            WHERE i.scope_id=? AND i.idempotency_key=?
            """,
            (identity.scope_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"] or "") != payload_hash:
            raise RepositoryError(409, "gc15_idempotency_conflict", "操作标识已用于不同内容")
        raw = str(row["receipt"] or "")
        if not raw or sha256_text(raw) != str(row["result_hash"] or ""):
            raise RepositoryError(500, "gc15_receipt_invalid", "生命周期操作回执校验失败")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RepositoryError(500, "gc15_receipt_invalid", "生命周期操作回执结构无效")
        parsed["idempotentReplay"] = True
        return parsed

    @staticmethod
    def _store_receipt(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        result: Mapping[str, Any],
        now: str,
    ) -> tuple[str, str]:
        manifest_id = new_id()
        raw = canonical_json(dict(result))
        digest = sha256_text(raw)
        connection.execute(
            """
            INSERT INTO object_manifests (
                id, scope_id, storage_key, content_hash, lifecycle_state,
                receipt, holder_role, holder_instance_id, storage_kind,
                byte_size, media_type, availability_state, receipt_hash,
                created_at, verified_at, deleted_at, authority_role,
                origin_instance_id
            ) VALUES (?, ?, NULL, ?, 'active', ?, 'organization_cloud', ?,
                      'command_receipt', ?, 'application/json', 'ready', ?,
                      ?, ?, NULL, 'cloud', ?)
            """,
            (
                manifest_id,
                identity.scope_id,
                digest,
                raw,
                identity.cloud_instance_id,
                len(raw.encode("utf-8")),
                digest,
                now,
                now,
                identity.cloud_instance_id,
            ),
        )
        return manifest_id, digest

    def _record_command(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        operation_id: str,
        idempotency_key: str,
        payload_hash: str,
        command_type: str,
        resource_id: str,
        aggregate_version: int,
        command_status: str,
        result: Mapping[str, Any],
        now: str,
    ) -> str:
        manifest_id, result_hash = self._store_receipt(
            connection,
            identity,
            result=result,
            now=now,
        )
        connection.execute(
            """
            INSERT INTO idempotency_records (
                id, scope_id, idempotency_key, payload_hash, result_hash,
                expires_at, result_object_manifest_id, status, created_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, '9999-12-31T23:59:59.999Z', ?,
                      'completed', ?, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, idempotency_key, payload_hash,
                result_hash, manifest_id, now, identity.cloud_instance_id,
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
            ) VALUES (?, ?, ?, ?, 'secured_resource', ?, ?, ?, ?, NULL, ?, ?,
                      ?, ?, ?, ?, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, operation_id, idempotency_key,
                resource_id, command_type, identity.principal_id,
                aggregate_version, command_status, identity.membership_id,
                manifest_id, payload_hash, now, now, identity.cloud_instance_id,
            ),
        )
        event_hash = sha256_text(
            canonical_json(
                {
                    "operationId": operation_id,
                    "commandType": command_type,
                    "resourceId": resource_id,
                    "version": aggregate_version,
                    "resultHash": result_hash,
                }
            )
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, scope_id, operation_id, actor_id, action, event_hash,
                actor_membership_id, target_resource_id,
                details_object_manifest_id, occurred_at, origin_instance_id,
                created_at, integrity_hash, authority_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cloud')
            """,
            (
                new_id(), identity.scope_id, operation_id, identity.principal_id,
                command_type, event_hash, identity.membership_id, resource_id,
                manifest_id, now, identity.cloud_instance_id, now, event_hash,
            ),
        )
        connection.execute(
            """
            INSERT INTO outbox_events (
                id, scope_id, operation_id, aggregate_version, event_type,
                status, aggregate_type, aggregate_id, event_object_manifest_id,
                event_hash, available_at, published_at, authority_role,
                origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, 'pending', 'secured_resource', ?, ?, ?, ?,
                      NULL, 'cloud', ?)
            """,
            (
                new_id(), identity.scope_id, operation_id, aggregate_version,
                command_type, resource_id, manifest_id, event_hash, now,
                identity.cloud_instance_id,
            ),
        )
        return manifest_id

    @staticmethod
    def _resource(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        resource_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM secured_resources WHERE id=? AND scope_id=?",
            (resource_id, identity.scope_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "gc15_resource_missing", "待处理对象不存在")
        return row

    def place_legal_hold(
        self,
        identity: SessionIdentity,
        *,
        resource_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        normalized_reason = " ".join(str(reason or "").split())[:500]
        if not normalized_reason:
            raise RepositoryError(422, "gc15_hold_reason_required", "请填写法律保留原因")
        normalized = {"resourceId": resource_id, "reason": normalized_reason}
        payload_hash = self._payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                resource = self._resource(connection, identity, resource_id)
                active = connection.execute(
                    "SELECT * FROM legal_holds WHERE scope_id=? AND secured_resource_id=? "
                    "AND hold_state='active' AND lifecycle_state='active' "
                    "ORDER BY created_at DESC,id DESC LIMIT 1",
                    (identity.scope_id, resource_id),
                ).fetchone()
                now = utc_now()
                operation_id = _stable_id("op", identity.scope_id, idempotency_key)
                hold_id = str(active["id"]) if active is not None else new_id()
                hold_version = int(active["version"] or 1) if active is not None else 1
                result = {
                    "state": "blocked",
                    "resourceId": resource_id,
                    "holdId": hold_id,
                    "holdVersion": hold_version,
                    "holdState": "active",
                    "message": "对象已进入法律保留，彻底清除将被阻止",
                    "idempotentReplay": False,
                }
                self._record_command(
                    connection,
                    identity,
                    operation_id=operation_id,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="gc15.legal_hold.placed",
                    resource_id=resource_id,
                    aggregate_version=int(resource["version"] or 1),
                    command_status="committed",
                    result=result,
                    now=now,
                )
                if active is None:
                    connection.execute(
                        """
                        INSERT INTO legal_holds (
                            id, scope_id, secured_resource_id, operation_id,
                            hold_state, reason, placed_by_membership_id, placed_at,
                            released_at, version, lifecycle_state, created_at,
                            updated_at, deleted_at
                        ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, NULL, 1,
                                  'active', ?, ?, NULL)
                        """,
                        (
                            hold_id, identity.scope_id, resource_id, operation_id,
                            normalized_reason, identity.membership_id, now, now, now,
                        ),
                    )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def release_legal_hold(
        self,
        identity: SessionIdentity,
        *,
        hold_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        normalized = {"holdId": hold_id, "expectedVersion": expected_version}
        payload_hash = self._payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                hold = connection.execute(
                    "SELECT * FROM legal_holds WHERE id=? AND scope_id=?",
                    (hold_id, identity.scope_id),
                ).fetchone()
                if hold is None:
                    raise RepositoryError(404, "gc15_hold_missing", "法律保留记录不存在")
                if str(hold["hold_state"] or "") != "active":
                    raise RepositoryError(409, "gc15_hold_not_active", "法律保留已解除")
                if int(hold["version"] or 1) != int(expected_version):
                    raise RepositoryError(409, "gc15_hold_version_conflict", "法律保留已变化，请刷新后重试")
                now = utc_now()
                next_version = int(expected_version) + 1
                connection.execute(
                    "UPDATE legal_holds SET hold_state='released',released_at=?,"
                    "version=?,lifecycle_state='archived',updated_at=? WHERE id=? "
                    "AND scope_id=? AND version=? AND hold_state='active'",
                    (
                        now, next_version, now, hold_id, identity.scope_id,
                        expected_version,
                    ),
                )
                resource_id = str(hold["secured_resource_id"])
                resource = self._resource(connection, identity, resource_id)
                operation_id = _stable_id("op", identity.scope_id, idempotency_key)
                result = {
                    "state": "ready",
                    "resourceId": resource_id,
                    "holdId": hold_id,
                    "holdVersion": next_version,
                    "holdState": "released",
                    "message": "法律保留已解除，可重新发起清除",
                    "idempotentReplay": False,
                }
                self._record_command(
                    connection,
                    identity,
                    operation_id=operation_id,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="gc15.legal_hold.released",
                    resource_id=resource_id,
                    aggregate_version=int(resource["version"] or 1),
                    command_status="committed",
                    result=result,
                    now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _lineage_ids(
        connection: sqlite3.Connection,
        *,
        scope_id: str,
        resource_id: str,
    ) -> list[str]:
        rows = connection.execute(
            """
            SELECT id FROM derivation_lineage
            WHERE scope_id=? AND invalidated_at IS NULL AND (
                derivative_object_id=? OR source_set_id IN (
                    SELECT source_set_id FROM source_set_members
                    WHERE scope_id=? AND source_object_id=?
                      AND lifecycle_state='active'
                )
            )
            ORDER BY id
            """,
            (scope_id, resource_id, scope_id, resource_id),
        ).fetchall()
        return [str(row["id"]) for row in rows]

    @staticmethod
    def _invalidate_derivatives(
        connection: sqlite3.Connection,
        *,
        scope_id: str,
        resource_id: str,
        now: str,
    ) -> dict[str, int]:
        lineage_ids = GC15LifecycleRepository._lineage_ids(
            connection,
            scope_id=scope_id,
            resource_id=resource_id,
        )
        if not lineage_ids:
            return {"lineages": 0, "indexes": 0, "contexts": 0, "exports": 0}
        placeholders = ",".join("?" for _ in lineage_ids)
        lineages = connection.execute(
            f"UPDATE derivation_lineage SET invalidated_at=? WHERE scope_id=? "
            f"AND id IN ({placeholders}) AND invalidated_at IS NULL",
            (now, scope_id, *lineage_ids),
        ).rowcount
        indexes = 0
        for table in ("search_index_manifests", "vector_index_manifests"):
            indexes += connection.execute(
                f"UPDATE {table} SET status='invalidated',invalidated_at=? "
                f"WHERE scope_id=? AND lineage_id IN ({placeholders}) "
                "AND invalidated_at IS NULL",
                (now, scope_id, *lineage_ids),
            ).rowcount
        connection.execute(
            f"UPDATE cache_entries SET invalidated_at=? WHERE scope_id=? "
            f"AND lineage_id IN ({placeholders}) AND invalidated_at IS NULL",
            (now, scope_id, *lineage_ids),
        )
        contexts = connection.execute(
            f"UPDATE ai_context_manifests SET status='invalidated',invalidated_at=? "
            f"WHERE scope_id=? AND lineage_id IN ({placeholders}) "
            "AND invalidated_at IS NULL",
            (now, scope_id, *lineage_ids),
        ).rowcount
        exports = connection.execute(
            f"UPDATE export_grants SET status='revoked',revoked_at=?,version=version+1,"
            f"updated_at=? WHERE scope_id=? AND lineage_id IN ({placeholders}) "
            "AND status='active'",
            (now, now, scope_id, *lineage_ids),
        ).rowcount
        return {
            "lineages": int(lineages),
            "indexes": int(indexes),
            "contexts": int(contexts),
            "exports": int(exports),
        }

    @staticmethod
    def _scrub_task_payload(
        connection: sqlite3.Connection,
        *,
        scope_id: str,
        resource_id: str,
        now: str,
    ) -> bool:
        row = connection.execute(
            "SELECT lifecycle_state FROM tasks WHERE id=? AND scope_id=?",
            (resource_id, scope_id),
        ).fetchone()
        if row is None:
            return False
        if str(row["lifecycle_state"] or "") != "deleted":
            raise RepositoryError(409, "gc15_tombstone_required", "对象尚未完成业务删除，不能彻底清除")
        connection.execute(
            "UPDATE tasks SET title='[已清除]',description=NULL,completion_note=NULL,"
            "source_type=NULL,source_id=NULL,updated_at=? WHERE id=? AND scope_id=? "
            "AND lifecycle_state='deleted'",
            (now, resource_id, scope_id),
        )
        return True

    def settle_purge(
        self,
        identity: SessionIdentity,
        *,
        resource_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Settle one purge attempt; tombstone identity is intentionally retained."""

        self._require_admin(identity)
        normalized = {"resourceId": resource_id, "mode": "gc15-tombstone-retained-v1"}
        payload_hash = self._payload_hash(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                resource = self._resource(connection, identity, resource_id)
                resource_state = str(resource["lifecycle_state"] or "")
                if resource_state not in {"deleted", "archived", "revoked"}:
                    raise RepositoryError(409, "gc15_tombstone_required", "对象尚未完成业务删除，不能彻底清除")
                now = utc_now()
                operation_id = _stable_id("op", identity.scope_id, idempotency_key)
                generation = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(purge_generation),0)+1 AS generation "
                        "FROM purge_ledger WHERE scope_id=? AND secured_resource_id=?",
                        (identity.scope_id, resource_id),
                    ).fetchone()["generation"]
                )
                ledger_id = _stable_id(
                    "purge", identity.scope_id, resource_id, str(generation)
                )
                hold = connection.execute(
                    "SELECT id FROM legal_holds WHERE scope_id=? AND secured_resource_id=? "
                    "AND hold_state='active' AND lifecycle_state='active' LIMIT 1",
                    (identity.scope_id, resource_id),
                ).fetchone()
                if hold is not None:
                    result = {
                        "state": "blocked",
                        "retryable": True,
                        "retryCondition": "legal_hold_released",
                        "resourceId": resource_id,
                        "purgeLedgerId": ledger_id,
                        "purgeGeneration": generation,
                        "holdId": str(hold["id"]),
                        "message": "对象处于法律保留状态；解除后可重新发起清除",
                        "idempotentReplay": False,
                    }
                    manifest_id = self._record_command(
                        connection,
                        identity,
                        operation_id=operation_id,
                        idempotency_key=idempotency_key,
                        payload_hash=payload_hash,
                        command_type="gc15.purge.blocked",
                        resource_id=resource_id,
                        aggregate_version=int(resource["version"] or 1),
                        command_status="blocked",
                        result=result,
                        now=now,
                    )
                    impact_hash = sha256_text(canonical_json({"holdId": str(hold["id"])}))
                    connection.execute(
                        "INSERT INTO purge_ledger (id,scope_id,operation_id,secured_resource_id,"
                        "purge_generation,status,proof_hash,impact_snapshot_hash,requested_at,"
                        "completed_at,version,lifecycle_state,created_at,updated_at,deleted_at) "
                        "VALUES (?,?,?,?,?,'blocked_legal_hold',NULL,?,?,NULL,1,'active',?,?,NULL)",
                        (
                            ledger_id, identity.scope_id, operation_id, resource_id,
                            generation, impact_hash, now, now, now,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO reconciliation_runs (id,scope_id,operation_id,registry_state_id,"
                        "mismatch_count,status,reconciliation_kind,target_instance_id,"
                        "result_object_manifest_id,started_at,completed_at,version,lifecycle_state,"
                        "created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                        "VALUES (?,?,?,NULL,1,'blocked','gc15_purge',?,?,?,NULL,1,'active',"
                        "?,?,NULL,'cloud',?)",
                        (
                            _stable_id("recon", ledger_id), identity.scope_id,
                            operation_id, identity.cloud_instance_id, manifest_id,
                            now, now, now, identity.cloud_instance_id,
                        ),
                    )
                    connection.commit()
                    return result

                resource_kind = str(resource["resource_kind"] or "")
                scrubbed = False
                if resource_kind == "task":
                    scrubbed = self._scrub_task_payload(
                        connection,
                        scope_id=identity.scope_id,
                        resource_id=resource_id,
                        now=now,
                    )
                if not scrubbed:
                    result = {
                        "state": "failed_retryable",
                        "retryable": True,
                        "resourceId": resource_id,
                        "purgeLedgerId": ledger_id,
                        "purgeGeneration": generation,
                        "message": "该对象的受控清除执行器尚未接通，墓碑保持有效",
                        "idempotentReplay": False,
                    }
                    manifest_id = self._record_command(
                        connection,
                        identity,
                        operation_id=operation_id,
                        idempotency_key=idempotency_key,
                        payload_hash=payload_hash,
                        command_type="gc15.purge.failed_retryable",
                        resource_id=resource_id,
                        aggregate_version=int(resource["version"] or 1),
                        command_status="failed_retryable",
                        result=result,
                        now=now,
                    )
                    connection.execute(
                        "INSERT INTO purge_ledger (id,scope_id,operation_id,secured_resource_id,"
                        "purge_generation,status,proof_hash,impact_snapshot_hash,requested_at,"
                        "completed_at,version,lifecycle_state,created_at,updated_at,deleted_at) "
                        "VALUES (?,?,?,?,?,'failed_retryable',NULL,NULL,?,NULL,1,'active',?,?,NULL)",
                        (
                            ledger_id, identity.scope_id, operation_id, resource_id,
                            generation, now, now, now,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO reconciliation_runs (id,scope_id,operation_id,registry_state_id,"
                        "mismatch_count,status,reconciliation_kind,target_instance_id,"
                        "result_object_manifest_id,started_at,completed_at,version,lifecycle_state,"
                        "created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                        "VALUES (?,?,?,NULL,1,'failed_retryable','gc15_purge',?,?,?,NULL,1,"
                        "'active',?,?,NULL,'cloud',?)",
                        (
                            _stable_id("recon", ledger_id), identity.scope_id,
                            operation_id, identity.cloud_instance_id, manifest_id,
                            now, now, now, identity.cloud_instance_id,
                        ),
                    )
                    connection.commit()
                    return result

                impact = self._invalidate_derivatives(
                    connection,
                    scope_id=identity.scope_id,
                    resource_id=resource_id,
                    now=now,
                )
                connection.execute(
                    "UPDATE source_set_members SET lifecycle_state='archived',removed_at=?,"
                    "version=version+1,updated_at=? WHERE scope_id=? AND source_object_id=? "
                    "AND lifecycle_state='active'",
                    (now, now, identity.scope_id, resource_id),
                )
                impact_snapshot = {
                    "resourceKind": resource_kind,
                    "tombstoneRetained": True,
                    "protectedPayloadCleared": True,
                    "invalidated": impact,
                }
                impact_hash = sha256_text(canonical_json(impact_snapshot))
                proof_hash = sha256_text(
                    canonical_json(
                        {
                            "resourceId": resource_id,
                            "generation": generation,
                            "impactHash": impact_hash,
                            "completedAt": now,
                        }
                    )
                )
                result = {
                    "state": "completed",
                    "retryable": False,
                    "resourceId": resource_id,
                    "purgeLedgerId": ledger_id,
                    "purgeGeneration": generation,
                    "tombstoneRetained": True,
                    "protectedPayloadCleared": True,
                    "invalidated": impact,
                    "proofHash": proof_hash,
                    "message": "受保护正文已清除，防复活墓碑和审计证据已保留",
                    "idempotentReplay": False,
                }
                manifest_id = self._record_command(
                    connection,
                    identity,
                    operation_id=operation_id,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="gc15.purge.completed",
                    resource_id=resource_id,
                    aggregate_version=int(resource["version"] or 1),
                    command_status="committed",
                    result=result,
                    now=now,
                )
                connection.execute(
                    "INSERT INTO purge_ledger (id,scope_id,operation_id,secured_resource_id,"
                    "purge_generation,status,proof_hash,impact_snapshot_hash,requested_at,"
                    "completed_at,version,lifecycle_state,created_at,updated_at,deleted_at) "
                    "VALUES (?,?,?,?,?,'completed',?,?,?, ?,1,'active',?,?,NULL)",
                    (
                        ledger_id, identity.scope_id, operation_id, resource_id,
                        generation, proof_hash, impact_hash, now, now, now, now,
                    ),
                )
                for layer_kind, affected_count in sorted(impact.items()):
                    layer_proof = sha256_text(
                        canonical_json(
                            {
                                "purgeId": ledger_id,
                                "layerKind": layer_kind,
                                "affectedCount": affected_count,
                                "processedAt": now,
                            }
                        )
                    )
                    connection.execute(
                        "INSERT INTO purge_layer_receipts "
                        "(id,scope_id,purge_id,layer_kind,status,proof_hash,"
                        "executor_instance_id,processed_at,origin_instance_id,created_at,"
                        "integrity_hash,authority_role) VALUES (?,?,?,?, 'completed',?,?,?,?,?,?,?)",
                        (
                            _stable_id("purge_layer", ledger_id, layer_kind),
                            identity.scope_id,
                            ledger_id,
                            layer_kind,
                            layer_proof,
                            identity.cloud_instance_id,
                            now,
                            identity.cloud_instance_id,
                            now,
                            layer_proof,
                            "cloud",
                        ),
                    )
                connection.execute(
                    "INSERT INTO reconciliation_runs (id,scope_id,operation_id,registry_state_id,"
                    "mismatch_count,status,reconciliation_kind,target_instance_id,"
                    "result_object_manifest_id,started_at,completed_at,version,lifecycle_state,"
                    "created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,NULL,0,'completed','gc15_purge',?,?,?,?,1,'active',"
                    "?,?,NULL,'cloud',?)",
                    (
                        _stable_id("recon", ledger_id), identity.scope_id,
                        operation_id, identity.cloud_instance_id, manifest_id,
                        now, now, now, now, identity.cloud_instance_id,
                    ),
                )
                event_hash = sha256_text(
                    f"{operation_id}|{resource_id}|{resource_state}|purged|{generation}"
                )
                connection.execute(
                    "INSERT INTO lifecycle_events (id,scope_id,operation_id,secured_resource_id,"
                    "from_state,to_state,tombstone_version,actor_id,reason_code,occurred_at,"
                    "origin_instance_id,created_at,integrity_hash) VALUES (?,?,?,?,?,'purged',"
                    "?,?, 'retention_purge',?,?,?,?)",
                    (
                        new_id(), identity.scope_id, operation_id, resource_id,
                        resource_state, int(resource["version"] or 1),
                        identity.principal_id, now, identity.cloud_instance_id,
                        now, event_hash,
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
