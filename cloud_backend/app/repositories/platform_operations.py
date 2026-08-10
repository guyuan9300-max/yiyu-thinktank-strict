"""88-table receipts for bounded platform operations."""

from __future__ import annotations

from typing import Any, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity


class PlatformOperationRepository:
    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _receipt(value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        import json

        try:
            envelope = json.loads(str(value or "{}"))
        except (TypeError, ValueError):
            return {}, {}
        if not isinstance(envelope, Mapping):
            return {}, {}
        result = envelope.get("result")
        payload = envelope.get("payload")
        return (
            dict(result) if isinstance(result, Mapping) else {},
            dict(payload) if isinstance(payload, Mapping) else {},
        )

    def replay(
        self,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        payload_hash = sha256_text(canonical_json(dict(payload)))
        with self.repository._connection() as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT c.payload_hash,m.receipt FROM commands c "
                "JOIN object_manifests m ON m.id=c.payload_object_manifest_id "
                "AND m.scope_id=c.scope_id WHERE c.scope_id=? "
                "AND c.actor_principal_id=? AND c.command_type=? "
                "AND c.idempotency_key=? LIMIT 1",
                (
                    identity.scope_id,
                    identity.principal_id,
                    command_type,
                    idempotency_key,
                ),
            ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"] or "") != payload_hash:
            raise RepositoryError(
                409,
                "idempotency_payload_conflict",
                "同一幂等键不能提交不同内容",
            )
        result, _ = self._receipt(row["receipt"])
        return {**result, "idempotentReplay": True}

    def latest_result(
        self,
        identity: SessionIdentity,
        *,
        command_type: str,
        aggregate_id: str,
    ) -> dict[str, Any] | None:
        with self.repository._connection() as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT m.receipt FROM commands c JOIN object_manifests m "
                "ON m.id=c.payload_object_manifest_id AND m.scope_id=c.scope_id "
                "WHERE c.scope_id=? AND c.command_type=? AND c.aggregate_id=? "
                "ORDER BY COALESCE(c.settled_at,c.submitted_at) DESC,c.id DESC LIMIT 1",
                (identity.scope_id, command_type, aggregate_id),
            ).fetchone()
        if row is None:
            return None
        result, _ = self._receipt(row["receipt"])
        return result or None

    def list_records(
        self,
        identity: SessionIdentity,
        *,
        aggregate_type: str,
        command_types: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        if not command_types:
            return []
        placeholders = ",".join("?" for _ in command_types)
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                f"SELECT c.aggregate_id,c.command_type,c.submitted_at,m.receipt "
                f"FROM commands c JOIN object_manifests m ON m.id=c.payload_object_manifest_id "
                f"AND m.scope_id=c.scope_id WHERE c.scope_id=? AND c.aggregate_type=? "
                f"AND c.command_type IN ({placeholders}) "
                f"ORDER BY COALESCE(c.settled_at,c.submitted_at) DESC,c.id DESC",
                (identity.scope_id, aggregate_type, *command_types),
            ).fetchall()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            aggregate_id = str(row["aggregate_id"] or "")
            if not aggregate_id or aggregate_id in latest:
                continue
            _, payload = self._receipt(row["receipt"])
            if not payload:
                continue
            latest[aggregate_id] = {
                **payload,
                "id": str(payload.get("id") or aggregate_id),
                "updatedAt": str(payload.get("updatedAt") or row["submitted_at"] or ""),
            }
        return list(latest.values())

    @staticmethod
    def _resource_id(
        identity: SessionIdentity,
        provider: str,
        resource_kind: str,
        remote_id: str,
    ) -> str:
        return "provider_operation_" + sha256_text(
            f"{identity.scope_id}|{provider}|{resource_kind}|{remote_id}"
        )[:30]

    def record(
        self,
        identity: SessionIdentity,
        *,
        command_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        provider: str,
        resource_kind: str,
        remote_id: str,
        outcome: str,
        retention_state: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        processing_kind: str | None = None,
        processing_state: str | None = None,
        result_details: Mapping[str, Any] | None = None,
        owner_kind: str = "organization",
    ) -> dict[str, Any]:
        del processing_kind, processing_state
        payload_value = dict(payload)
        payload_hash = sha256_text(canonical_json(payload_value))
        now = utc_now()
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT c.payload_hash,m.receipt FROM commands c "
                    "JOIN object_manifests m ON m.id=c.payload_object_manifest_id "
                    "AND m.scope_id=c.scope_id WHERE c.scope_id=? "
                    "AND c.actor_principal_id=? AND c.command_type=? "
                    "AND c.idempotency_key=? LIMIT 1",
                    (
                        identity.scope_id,
                        identity.principal_id,
                        command_type,
                        idempotency_key,
                    ),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_hash"] or "") != payload_hash:
                        raise RepositoryError(
                            409,
                            "idempotency_payload_conflict",
                            "同一幂等键不能提交不同内容",
                        )
                    import json

                    envelope = json.loads(str(existing["receipt"] or "{}"))
                    result = envelope.get("result") if isinstance(envelope, dict) else None
                    connection.rollback()
                    return dict(result) if isinstance(result, dict) else {}

                command_id = new_id()
                operation_id = new_id()
                result = {
                    "operationId": operation_id,
                    "processingAttemptId": None,
                    "state": outcome,
                    "errorCode": error_code,
                    "message": error_message or "",
                    "retryable": outcome == "failed_retryable",
                    **dict(result_details or {}),
                }
                receipt = canonical_json({"payload": payload_value, "result": result})
                receipt_hash = sha256_text(receipt)
                manifest_id = new_id()
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,"
                    "lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,"
                    "byte_size,media_type,availability_state,receipt_hash,created_at,"
                    "verified_at,deleted_at,authority_role,origin_instance_id) VALUES "
                    "(?,?,NULL,?,'active',?,'organization_cloud',?,'command_receipt',?,"
                    "'application/json','ready',?,?,?,NULL,'cloud',?)",
                    (
                        manifest_id,
                        identity.scope_id,
                        receipt_hash,
                        receipt,
                        identity.cloud_instance_id,
                        len(receipt.encode("utf-8")),
                        receipt_hash,
                        now,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO idempotency_records (id,scope_id,idempotency_key,"
                    "payload_hash,result_hash,expires_at,result_object_manifest_id,status,"
                    "created_at,authority_role,origin_instance_id) VALUES (?,?,?,?,?,"
                    "'9999-12-31T23:59:59.999Z',?,'completed',?,'cloud',?)",
                    (
                        new_id(),
                        identity.scope_id,
                        idempotency_key,
                        payload_hash,
                        receipt_hash,
                        manifest_id,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,"
                    "aggregate_type,aggregate_id,command_type,actor_principal_id,"
                    "expected_aggregate_version,device_command_sequence,status,"
                    "actor_membership_id,payload_object_manifest_id,payload_hash,"
                    "submitted_at,settled_at,authority_role,origin_instance_id) VALUES "
                    "(?,?,?,?,?,?,?,?,NULL,NULL,?,?,?,?,?,?,'cloud',?)",
                    (
                        command_id,
                        identity.scope_id,
                        operation_id,
                        idempotency_key,
                        aggregate_type,
                        aggregate_id,
                        command_type,
                        identity.principal_id,
                        "committed" if outcome in {"queued", "succeeded"} else "failed",
                        identity.membership_id,
                        manifest_id,
                        payload_hash,
                        now,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
                provider_resource_id = self._resource_id(
                    identity, provider, resource_kind, remote_id
                )
                row = connection.execute(
                    "SELECT version FROM provider_resources WHERE id=? AND scope_id=?",
                    (provider_resource_id, identity.scope_id),
                ).fetchone()
                version = int(row["version"] or 1) + 1 if row else 1
                connection.execute(
                    "INSERT INTO provider_resources (id,scope_id,provider,resource_kind,"
                    "remote_id,retention_state,owner_kind,owner_principal_id,"
                    "owner_membership_id,display_name,endpoint,model_name,"
                    "public_config_schema_version,public_config,secret_reference,"
                    "secret_fingerprint,status,verified_at,version,lifecycle_state,"
                    "created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?, ?,NULL,NULL,NULL,NULL,NULL,"
                    "NULL,?,?,?,'active',?,?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET "
                    "retention_state=excluded.retention_state,status=excluded.status,"
                    "verified_at=excluded.verified_at,version=excluded.version,"
                    "updated_at=excluded.updated_at,lifecycle_state='active',deleted_at=NULL",
                    (
                        provider_resource_id,
                        identity.scope_id,
                        provider,
                        resource_kind,
                        remote_id,
                        retention_state or outcome,
                        owner_kind,
                        identity.principal_id if owner_kind == "principal" else None,
                        identity.membership_id if owner_kind == "membership" else None,
                        resource_kind,
                        outcome,
                        now if outcome == "succeeded" else None,
                        version,
                        now,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO operation_attempts (id,scope_id,command_id,attempt_no,"
                    "transport_state,lease_owner,lease_until,permission_revalidated_at,"
                    "receipt_hash,next_retry_at,executor_role,started_at,finished_at,"
                    "authority_role,origin_instance_id) VALUES (?,?,?,1,?,NULL,NULL,?,?,"
                    "NULL,'organization_cloud',?,?,'cloud',?)",
                    (
                        new_id(), identity.scope_id, command_id, outcome, now,
                        receipt_hash, now, now, identity.cloud_instance_id,
                    ),
                )
                if outcome in {"blocked", "failed_retryable"}:
                    connection.execute(
                        "INSERT INTO dead_letters (id,scope_id,operation_id,aggregate_id,"
                        "error_code,status,aggregate_type,safe_message,retry_after,"
                        "taken_over_by,created_at,resolved_at,version,lifecycle_state,"
                        "updated_at,deleted_at,authority_role,origin_instance_id) VALUES "
                        "(?,?,?,?,?,'open',?,?,NULL,NULL,?,NULL,1,'active',?,NULL,'cloud',?)",
                        (
                            new_id(), identity.scope_id, operation_id, aggregate_id,
                            error_code or "platform_operation_blocked", aggregate_type,
                            error_message or "平台操作被阻止", now, now,
                            identity.cloud_instance_id,
                        ),
                    )
                connection.execute(
                    "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,"
                    "event_type,status,aggregate_type,aggregate_id,event_object_manifest_id,"
                    "event_hash,available_at,published_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,?,?,'pending',?,?,?,?,?,NULL,'cloud',?)",
                    (
                        new_id(), identity.scope_id, operation_id, version, command_type,
                        aggregate_type, aggregate_id, manifest_id, receipt_hash, now,
                        identity.cloud_instance_id,
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise


__all__ = ["PlatformOperationRepository"]
