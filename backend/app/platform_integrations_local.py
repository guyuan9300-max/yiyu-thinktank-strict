from __future__ import annotations

import json
from typing import Any, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from .runtime import LocalRuntimeError, WorkspaceRuntime


_TERMINAL_STATES = {
    "blocked",
    "cancelled",
    "completed",
    "failed",
    "failed_retryable",
    "succeeded",
}


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class LocalPlatformOperationRepository:
    """Device operation receipts backed only by the strict 88 tables."""

    def __init__(self, runtime: WorkspaceRuntime):
        self.runtime = runtime

    @property
    def _origin_instance_id(self) -> str:
        return str(self.runtime.identity.database_generation_id)

    def _device_context(self) -> dict[str, str | None]:
        generation_id = self._origin_instance_id
        principal_id = f"local_device_{sha256_text(generation_id)[:24]}"
        scope_id = f"local_scope_{sha256_text(generation_id)[:24]}"
        now = utc_now()
        with self.runtime._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO principals (id,status,identity_version,updated_at,"
                    "principal_kind,display_name,version,lifecycle_state,created_at,"
                    "deleted_at,sandbox_id,source_version,projection_state,projected_at,"
                    "stale_at,lease_expires_at) VALUES (?,'active',1,?,'device',"
                    "'本机执行身份',1,'active',?,NULL,NULL,1,'ready',?,NULL,NULL) "
                    "ON CONFLICT(id) DO NOTHING",
                    (principal_id, now, now, now),
                )
                connection.execute(
                    "INSERT INTO authorization_scopes (id,scope_kind,principal_id,"
                    "organization_id,policy_version,created_at,updated_at,status,version,"
                    "lifecycle_state,deleted_at,sandbox_id,source_version,projection_state,"
                    "projected_at,stale_at,lease_expires_at) VALUES (?,'personal',?,NULL,1,"
                    "?,?,'active',1,'active',NULL,NULL,1,'ready',?,NULL,NULL) "
                    "ON CONFLICT(id) DO NOTHING",
                    (scope_id, principal_id, now, now, now),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "sandboxId": f"device:{generation_id}",
            "scopeId": scope_id,
            "cloudInstanceId": None,
            "organizationId": None,
            "principalId": principal_id,
            "membershipId": None,
        }

    def _context_for_sandbox(self, sandbox_id: str) -> dict[str, str | None]:
        if sandbox_id == f"device:{self._origin_instance_id}":
            return self._device_context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT sandbox.id,sandbox.scope_id,sandbox.cloud_instance_id,"
                "sandbox.principal_id,sandbox.membership_id,scope.organization_id "
                "FROM sandboxes AS sandbox JOIN authorization_scopes AS scope "
                "ON scope.id=sandbox.scope_id WHERE sandbox.id=? "
                "AND sandbox.lifecycle_state='active' LIMIT 1",
                (sandbox_id,),
            ).fetchone()
        if row is None:
            raise LocalRuntimeError(
                409,
                "workspace_context_stale",
                "操作所属工作空间已失效，迟到结果已丢弃",
            )
        return {
            "sandboxId": str(row["id"]),
            "scopeId": str(row["scope_id"]),
            "cloudInstanceId": str(row["cloud_instance_id"] or "") or None,
            "organizationId": str(row["organization_id"] or "") or None,
            "principalId": str(row["principal_id"] or "") or None,
            "membershipId": str(row["membership_id"] or "") or None,
        }

    def _context(self) -> dict[str, str | None]:
        captured = self.runtime.capture_sandbox_context()
        if not captured.sandbox_id:
            return self._device_context()
        return self._context_for_sandbox(captured.sandbox_id)

    @staticmethod
    def _receipt(row: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        envelope = _json_object(row["receipt"])
        result = envelope.get("result")
        payload = envelope.get("payload")
        return (
            dict(result) if isinstance(result, Mapping) else {},
            dict(payload) if isinstance(payload, Mapping) else {},
        )

    @staticmethod
    def _command_row(connection: Any, *, scope_id: str, operation_id: str) -> Any:
        return connection.execute(
            "SELECT command.*,manifest.receipt,manifest.id AS manifest_id "
            "FROM commands AS command JOIN object_manifests AS manifest "
            "ON manifest.id=command.payload_object_manifest_id "
            "AND manifest.scope_id=command.scope_id WHERE command.scope_id=? "
            "AND command.operation_id=? LIMIT 1",
            (scope_id, operation_id),
        ).fetchone()

    def _write_receipt(
        self,
        connection: Any,
        *,
        row: Any,
        payload: Mapping[str, Any],
        result: Mapping[str, Any],
        now: str,
    ) -> str:
        receipt = canonical_json({"payload": dict(payload), "result": dict(result)})
        receipt_hash = sha256_text(receipt)
        connection.execute(
            "UPDATE object_manifests SET content_hash=?,receipt=?,byte_size=?,"
            "receipt_hash=?,verified_at=? WHERE id=? AND scope_id=?",
            (
                receipt_hash,
                receipt,
                len(receipt.encode("utf-8")),
                receipt_hash,
                now,
                row["manifest_id"],
                row["scope_id"],
            ),
        )
        connection.execute(
            "UPDATE idempotency_records SET result_hash=?,status=? "
            "WHERE scope_id=? AND idempotency_key=?",
            (
                receipt_hash,
                "settled"
                if str(result.get("state") or "") in _TERMINAL_STATES
                else "pending",
                row["scope_id"],
                row["idempotency_key"],
            ),
        )
        return receipt_hash

    def _audit(
        self,
        connection: Any,
        *,
        row: Any,
        action: str,
        receipt_hash: str,
        now: str,
    ) -> None:
        integrity_hash = sha256_text(
            f"{row['operation_id']}|{action}|{receipt_hash}|{now}"
        )
        connection.execute(
            "INSERT INTO audit_events (id,scope_id,operation_id,actor_id,action,"
            "event_hash,actor_membership_id,target_resource_id,details_object_manifest_id,"
            "occurred_at,origin_instance_id,created_at,integrity_hash,authority_role) "
            "VALUES (?,?,?,?,?,?,?,NULL,?,?,?,?,?,'local')",
            (
                new_id(),
                row["scope_id"],
                row["operation_id"],
                row["actor_principal_id"],
                action,
                receipt_hash,
                row["actor_membership_id"],
                row["manifest_id"],
                now,
                self._origin_instance_id,
                now,
                integrity_hash,
            ),
        )

    def begin(
        self,
        *,
        idempotency_key: str,
        command_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
        initial_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        context = self._context()
        scope_id = str(context["scopeId"])
        normalized_key = idempotency_key or new_id()
        payload_value = dict(payload)
        payload_hash = sha256_text(canonical_json(payload_value))
        now = utc_now()
        with self.runtime._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT command.*,manifest.receipt,manifest.id AS manifest_id "
                    "FROM commands AS command JOIN object_manifests AS manifest "
                    "ON manifest.id=command.payload_object_manifest_id "
                    "AND manifest.scope_id=command.scope_id WHERE command.scope_id=? "
                    "AND command.command_type=? AND command.idempotency_key=? LIMIT 1",
                    (scope_id, command_type, normalized_key),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_hash"] or "") != payload_hash:
                        raise LocalRuntimeError(
                            409,
                            "idempotency_payload_conflict",
                            "同一幂等键不能提交不同内容",
                        )
                    result, _ = self._receipt(existing)
                    result.update(
                        {
                            "commandId": str(existing["id"]),
                            "operationId": str(existing["operation_id"]),
                            "idempotentReplay": True,
                        }
                    )
                    connection.rollback()
                    return result
                occupied = connection.execute(
                    "SELECT payload_hash FROM idempotency_records "
                    "WHERE scope_id=? AND idempotency_key=?",
                    (scope_id, normalized_key),
                ).fetchone()
                if occupied is not None:
                    raise LocalRuntimeError(
                        409,
                        "idempotency_key_conflict",
                        "该操作标识已被其他命令使用",
                    )

                command_id = new_id()
                operation_id = new_id()
                result = {
                    **dict(initial_result),
                    "commandId": command_id,
                    "operationId": operation_id,
                    "state": str(initial_result.get("state") or "processing"),
                    "retryable": bool(initial_result.get("retryable", True)),
                    "pollingEnabled": bool(initial_result.get("pollingEnabled", True)),
                    "sandboxId": context["sandboxId"],
                    "createdAt": now,
                    "updatedAt": now,
                }
                receipt = canonical_json({"payload": payload_value, "result": result})
                receipt_hash = sha256_text(receipt)
                manifest_id = new_id()
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,"
                    "lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,"
                    "byte_size,media_type,availability_state,receipt_hash,created_at,"
                    "verified_at,deleted_at,authority_role,origin_instance_id,"
                    "local_original_path) VALUES (?,?,NULL,?,'active',?,'member_device',?,"
                    "'command_receipt',?,'application/json','ready',?,?,?,NULL,'local',?,NULL)",
                    (
                        manifest_id,
                        scope_id,
                        receipt_hash,
                        receipt,
                        context["sandboxId"],
                        len(receipt.encode("utf-8")),
                        receipt_hash,
                        now,
                        now,
                        self._origin_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO idempotency_records (id,scope_id,idempotency_key,"
                    "payload_hash,result_hash,expires_at,result_object_manifest_id,status,"
                    "created_at,authority_role,origin_instance_id) VALUES (?,?,?,?,?,"
                    "'9999-12-31T23:59:59.999Z',?,'pending',?,'local',?)",
                    (
                        new_id(), scope_id, normalized_key, payload_hash, receipt_hash,
                        manifest_id, now, self._origin_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,"
                    "aggregate_type,aggregate_id,command_type,actor_principal_id,"
                    "expected_aggregate_version,device_command_sequence,status,"
                    "actor_membership_id,payload_object_manifest_id,payload_hash,"
                    "submitted_at,settled_at,authority_role,origin_instance_id) VALUES "
                    "(?,?,?,?,?,?,?,?,NULL,NULL,'sending',?,?,?,?,NULL,'local',?)",
                    (
                        command_id,
                        scope_id,
                        operation_id,
                        normalized_key,
                        aggregate_type,
                        aggregate_id,
                        command_type,
                        context["principalId"],
                        context["membershipId"],
                        manifest_id,
                        payload_hash,
                        now,
                        self._origin_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO operation_attempts (id,scope_id,command_id,attempt_no,"
                    "transport_state,lease_owner,lease_until,permission_revalidated_at,"
                    "receipt_hash,next_retry_at,executor_role,started_at,finished_at,"
                    "authority_role,origin_instance_id) VALUES (?,?,?,1,'processing',"
                    "'local-platform-worker',NULL,?,?,NULL,'member_device',?,NULL,'local',?)",
                    (
                        new_id(), scope_id, command_id, now, receipt_hash, now,
                        self._origin_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,"
                    "event_type,status,aggregate_type,aggregate_id,event_object_manifest_id,"
                    "event_hash,available_at,published_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,1,?,'pending',?,?,?,?,?,NULL,'local',?)",
                    (
                        new_id(), scope_id, operation_id, command_type, aggregate_type,
                        aggregate_id, manifest_id, receipt_hash, now,
                        self._origin_instance_id,
                    ),
                )
                row = self._command_row(
                    connection, scope_id=scope_id, operation_id=operation_id
                )
                self._audit(
                    connection,
                    row=row,
                    action=f"{command_type}.started",
                    receipt_hash=receipt_hash,
                    now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def get(self, operation_id: str) -> dict[str, Any] | None:
        context = self._context()
        with self.runtime._connection() as connection:  # noqa: SLF001
            row = self._command_row(
                connection,
                scope_id=str(context["scopeId"]),
                operation_id=operation_id,
            )
        if row is None:
            return None
        result, payload = self._receipt(row)
        return {
            **result,
            "commandId": str(row["id"]),
            "operationId": str(row["operation_id"]),
            "commandType": str(row["command_type"]),
            "aggregateType": str(row["aggregate_type"]),
            "aggregateId": str(row["aggregate_id"]),
            "commandStatus": str(row["status"]),
            "payload": payload,
        }

    def latest(
        self,
        *,
        command_types: tuple[str, ...],
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if not command_types:
            return []
        context = self._context()
        placeholders = ",".join("?" for _ in command_types)
        with self.runtime._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                f"SELECT command.*,manifest.receipt,manifest.id AS manifest_id "
                "FROM commands AS command JOIN object_manifests AS manifest "
                "ON manifest.id=command.payload_object_manifest_id "
                "AND manifest.scope_id=command.scope_id WHERE command.scope_id=? "
                f"AND command.command_type IN ({placeholders}) "
                "ORDER BY COALESCE(command.settled_at,command.submitted_at) DESC,"
                "command.id DESC LIMIT ?",
                (
                    context["scopeId"],
                    *command_types,
                    max(1, min(limit, 1000)),
                ),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            result, payload = self._receipt(row)
            items.append(
                {
                    **result,
                    "commandId": str(row["id"]),
                    "operationId": str(row["operation_id"]),
                    "commandType": str(row["command_type"]),
                    "aggregateType": str(row["aggregate_type"]),
                    "aggregateId": str(row["aggregate_id"]),
                    "commandStatus": str(row["status"]),
                    "payload": payload,
                    "updatedAt": str(row["settled_at"] or row["submitted_at"] or ""),
                }
            )
        return items

    def update(
        self,
        *,
        operation_id: str,
        state: str,
        result_patch: Mapping[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
        captured_sandbox_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        context = (
            self._context_for_sandbox(captured_sandbox_id)
            if captured_sandbox_id
            else self._context()
        )
        with self.runtime._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._command_row(
                    connection,
                    scope_id=str(context["scopeId"]),
                    operation_id=operation_id,
                )
                if row is None:
                    raise LocalRuntimeError(
                        404, "local_operation_missing", "本机操作不存在"
                    )
                previous, payload = self._receipt(row)
                if str(previous.get("state") or "") in _TERMINAL_STATES:
                    connection.rollback()
                    return previous
                result = {
                    **previous,
                    **dict(result_patch),
                    "state": state,
                    "updatedAt": now,
                    "pollingEnabled": state in {"queued", "processing", "cancelling"},
                    "retryable": state in {"blocked", "failed_retryable"},
                    "errorCode": error_code,
                    "error": error_message,
                }
                receipt_hash = self._write_receipt(
                    connection, row=row, payload=payload, result=result, now=now
                )
                command_status = (
                    "settled"
                    if state in {"cancelled", "completed", "succeeded"}
                    else "sending"
                    if state in {"queued", "processing", "cancelling"}
                    else "failed"
                )
                connection.execute(
                    "UPDATE commands SET status=?,settled_at=? WHERE scope_id=? "
                    "AND operation_id=?",
                    (
                        command_status,
                        now if command_status != "sending" else None,
                        row["scope_id"],
                        operation_id,
                    ),
                )
                connection.execute(
                    "UPDATE operation_attempts SET transport_state=?,lease_until=NULL,"
                    "next_retry_at=NULL,receipt_hash=?,finished_at=? WHERE scope_id=? "
                    "AND command_id=? AND attempt_no=(SELECT MAX(attempt_no) FROM "
                    "operation_attempts WHERE scope_id=? AND command_id=?)",
                    (
                        state,
                        receipt_hash,
                        now if state in _TERMINAL_STATES else None,
                        row["scope_id"],
                        row["id"],
                        row["scope_id"],
                        row["id"],
                    ),
                )
                connection.execute(
                    "UPDATE outbox_events SET status=?,event_hash=?,published_at=? "
                    "WHERE scope_id=? AND operation_id=?",
                    (
                        "published"
                        if command_status == "settled"
                        else "pending"
                        if command_status == "sending"
                        else "failed",
                        receipt_hash,
                        now if command_status == "settled" else None,
                        row["scope_id"],
                        operation_id,
                    ),
                )
                if command_status == "failed":
                    dead = connection.execute(
                        "SELECT id FROM dead_letters WHERE scope_id=? AND operation_id=? "
                        "AND status='open' LIMIT 1",
                        (row["scope_id"], operation_id),
                    ).fetchone()
                    if dead is None:
                        connection.execute(
                            "INSERT INTO dead_letters (id,scope_id,operation_id,aggregate_id,"
                            "error_code,status,aggregate_type,safe_message,retry_after,"
                            "taken_over_by,created_at,resolved_at,version,lifecycle_state,"
                            "updated_at,deleted_at,authority_role,origin_instance_id) VALUES "
                            "(?,?,?,?,?,'open',?,?,NULL,NULL,?,NULL,1,'active',?,NULL,'local',?)",
                            (
                                new_id(), row["scope_id"], operation_id,
                                row["aggregate_id"], error_code or "local_operation_failed",
                                row["aggregate_type"], error_message or "本机操作失败",
                                now, now, self._origin_instance_id,
                            ),
                        )
                self._audit(
                    connection,
                    row=row,
                    action=f"{row['command_type']}.{state}",
                    receipt_hash=receipt_hash,
                    now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def retry(self, *, operation_id: str) -> dict[str, Any]:
        context = self._context()
        now = utc_now()
        with self.runtime._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._command_row(
                    connection,
                    scope_id=str(context["scopeId"]),
                    operation_id=operation_id,
                )
                if row is None:
                    raise LocalRuntimeError(
                        404, "local_operation_missing", "本机操作不存在"
                    )
                previous, payload = self._receipt(row)
                if str(previous.get("state") or "") != "failed_retryable":
                    connection.rollback()
                    return previous
                result = {
                    **previous,
                    "state": "queued",
                    "retryable": True,
                    "pollingEnabled": True,
                    "errorCode": None,
                    "error": None,
                    "retriedAt": now,
                    "updatedAt": now,
                }
                receipt_hash = self._write_receipt(
                    connection, row=row, payload=payload, result=result, now=now
                )
                attempt_no = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(attempt_no),0) FROM operation_attempts "
                        "WHERE scope_id=? AND command_id=?",
                        (row["scope_id"], row["id"]),
                    ).fetchone()[0]
                ) + 1
                connection.execute(
                    "UPDATE commands SET status='sending',settled_at=NULL WHERE scope_id=? "
                    "AND operation_id=?",
                    (row["scope_id"], operation_id),
                )
                connection.execute(
                    "INSERT INTO operation_attempts (id,scope_id,command_id,attempt_no,"
                    "transport_state,lease_owner,lease_until,permission_revalidated_at,"
                    "receipt_hash,next_retry_at,executor_role,started_at,finished_at,"
                    "authority_role,origin_instance_id) VALUES (?,?,?,?,'queued',"
                    "'local-platform-worker',NULL,?,?,NULL,'member_device',?,NULL,'local',?)",
                    (
                        new_id(), row["scope_id"], row["id"], attempt_no, now,
                        receipt_hash, now, self._origin_instance_id,
                    ),
                )
                connection.execute(
                    "UPDATE outbox_events SET status='pending',event_hash=?,published_at=NULL "
                    "WHERE scope_id=? AND operation_id=?",
                    (receipt_hash, row["scope_id"], operation_id),
                )
                connection.execute(
                    "UPDATE dead_letters SET status='resolved',resolved_at=?,updated_at=?,"
                    "version=version+1 WHERE scope_id=? AND operation_id=? AND status='open'",
                    (now, now, row["scope_id"], operation_id),
                )
                self._audit(
                    connection,
                    row=row,
                    action=f"{row['command_type']}.retry_queued",
                    receipt_hash=receipt_hash,
                    now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def record_blocked(
        self,
        *,
        idempotency_key: str,
        command_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
        error_code: str,
        message: str,
        blocker_type: str,
    ) -> dict[str, Any]:
        started = self.begin(
            idempotency_key=idempotency_key,
            command_type=command_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            initial_result={
                "state": "processing",
                "retryable": True,
                "pollingEnabled": False,
            },
        )
        if (
            started.get("idempotentReplay")
            and str(started.get("state") or "") in _TERMINAL_STATES
        ):
            return started
        return self.update(
            operation_id=str(started["operationId"]),
            state="blocked",
            result_patch={"message": message, "blockerType": blocker_type},
            error_code=error_code,
            error_message=message,
        )


__all__ = ["LocalPlatformOperationRepository"]
