from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import canonical_json, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity


PROPOSAL_KINDS = frozenset(
    {
        "task_prep",
        "meeting_prep",
        "meeting_followup",
        "evidence_request",
        "judgment_review",
        "context_refresh",
    }
)
RISK_LEVELS = frozenset({"low", "medium", "high"})


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _operation_id(scope_id: str, command_type: str, idempotency_key: str) -> str:
    return "op_" + sha256_text(
        f"gc14-proposal\x1f{scope_id}\x1f{command_type}\x1f{idempotency_key}"
    )[:30]


def _record_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_" + sha256_text("\x1f".join(parts))[:30]


def _manifest(
    connection: sqlite3.Connection,
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    manifest_id: str,
    receipt: Mapping[str, Any],
    media_type: str,
    now: str,
) -> str:
    serialized = canonical_json(dict(receipt))
    receipt_hash = sha256_text(serialized)
    connection.execute(
        "INSERT INTO object_manifests "
        "(id,scope_id,storage_key,content_hash,lifecycle_state,receipt,holder_role,"
        "holder_instance_id,storage_kind,byte_size,media_type,availability_state,receipt_hash,"
        "created_at,verified_at,deleted_at,authority_role,origin_instance_id) "
        "VALUES (?,?,NULL,?,'active',?,'cloud_ai_proposal',?,'metadata_receipt',?,?,"
        "'ready',?,?,?,NULL,'cloud',?)",
        (
            manifest_id,
            identity.scope_id,
            receipt_hash,
            serialized,
            repository.cloud_instance_id,
            len(serialized.encode("utf-8")),
            media_type,
            receipt_hash,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    return receipt_hash


def _proposal_row(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    proposal_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT p.*,m.receipt FROM ai_proposals p "
        "JOIN object_manifests m ON m.scope_id=p.scope_id AND m.id=p.payload_object_manifest_id "
        "WHERE p.scope_id=? AND p.id=? AND p.lifecycle_state='active'",
        (identity.scope_id, proposal_id),
    ).fetchone()


def _latest_approval(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    proposal_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM ai_approvals WHERE scope_id=? AND proposal_id=? "
        "AND lifecycle_state='active' ORDER BY created_at DESC,id DESC LIMIT 1",
        (identity.scope_id, proposal_id),
    ).fetchone()


def _execution_row(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    proposal_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT r.*,m.receipt AS result_receipt FROM execution_runs r "
        "LEFT JOIN object_manifests m ON m.scope_id=r.scope_id "
        "AND m.id=r.result_object_manifest_id WHERE r.scope_id=? AND r.proposal_id=? "
        "AND r.lifecycle_state='active' ORDER BY r.created_at DESC,id DESC LIMIT 1",
        (identity.scope_id, proposal_id),
    ).fetchone()


def _proposal_payload(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    row: sqlite3.Row,
) -> dict[str, Any]:
    receipt = _json(row["receipt"], {})
    if not isinstance(receipt, Mapping):
        receipt = {}
    approval = _latest_approval(connection, identity, str(row["id"]))
    execution = _execution_row(connection, identity, str(row["id"]))
    payload = receipt.get("payload") if isinstance(receipt.get("payload"), Mapping) else {}
    status = str(row["status"] or "draft")
    execution_ticket = _execution_payload(execution, receipt) if execution is not None else None
    return {
        "id": str(row["id"]),
        "clientId": str(receipt.get("clientId") or ""),
        "kind": str(receipt.get("kind") or row["operation_kind"] or "context_refresh"),
        "status": status,
        "riskLevel": str(row["risk_level"] or receipt.get("riskLevel") or "medium"),
        "title": str(receipt.get("title") or "AI行动提案"),
        "summary": str(receipt.get("summary") or ""),
        "rationale": str(receipt.get("rationale") or ""),
        "targetRefs": list(receipt.get("targetRefs") or []),
        "sourceRefs": list(receipt.get("sourceRefs") or []),
        "boundaryNotes": list(receipt.get("boundaryNotes") or []),
        "payload": dict(payload),
        "createdBy": str(receipt.get("createdBy") or ""),
        "decidedBy": str(approval["approver_principal_id"] or "") if approval else None,
        "decidedAt": str(approval["decided_at"] or "") if approval else None,
        "rejectedReason": (
            str(approval["decision_note"] or "")
            if approval is not None and str(approval["decision"] or "") == "rejected"
            else None
        ),
        "executionTicketId": str(execution["id"]) if execution is not None else None,
        "executionTicket": execution_ticket,
        "answerId": str(row["answer_id"] or "") or None,
        "version": int(row["version"] or 1),
        "createdAt": str(row["created_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
    }


def _execution_payload(row: sqlite3.Row, proposal_receipt: Mapping[str, Any]) -> dict[str, Any]:
    result = _json(row["result_receipt"], {})
    if not isinstance(result, Mapping):
        result = {}
    return {
        "id": str(row["id"]),
        "proposalId": str(row["proposal_id"] or ""),
        "clientId": str(proposal_receipt.get("clientId") or ""),
        "executionType": str(row["run_kind"] or "proposal_controlled_execution"),
        "status": str(row["status"] or "pending"),
        "payload": {},
        "result": {
            "resultType": str(result.get("resultType") or "recorded_only"),
            "summary": str(result.get("summary") or ""),
            "createdTaskIds": list(result.get("createdTaskIds") or []),
            "artifactRefs": list(result.get("artifactRefs") or []),
        },
        "retryCount": 0,
        "maxRetries": 0,
        "lastError": None,
        "lastAttemptAt": str(row["finished_at"] or ""),
        "errorMessage": None,
        "executedAt": str(row["finished_at"] or "") or None,
        "createdAt": str(row["created_at"] or ""),
        "updatedAt": str(row["updated_at"] or ""),
    }


def _record_command(
    connection: sqlite3.Connection,
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    command_type: str,
    idempotency_key: str,
    aggregate_type: str,
    aggregate_id: str,
    expected_version: int | None,
    aggregate_version: int,
    payload_hash: str,
    result_hash: str,
    result_manifest_id: str | None,
    target_resource_id: str | None,
    now: str,
) -> str:
    operation_id = _operation_id(identity.scope_id, command_type, idempotency_key)
    connection.execute(
        "INSERT INTO idempotency_records "
        "(id,scope_id,idempotency_key,payload_hash,result_hash,expires_at,"
        "result_object_manifest_id,status,created_at,authority_role,origin_instance_id) "
        "VALUES (?,?,?,?,?,NULL,?,'settled',?,'cloud',?)",
        (
            _record_id("idem", operation_id, command_type),
            identity.scope_id,
            idempotency_key,
            payload_hash,
            result_hash,
            result_manifest_id,
            now,
            repository.cloud_instance_id,
        ),
    )
    connection.execute(
        "INSERT INTO commands "
        "(id,scope_id,operation_id,idempotency_key,aggregate_type,aggregate_id,command_type,"
        "actor_principal_id,expected_aggregate_version,device_command_sequence,status,"
        "actor_membership_id,payload_object_manifest_id,payload_hash,submitted_at,settled_at,"
        "authority_role,origin_instance_id) VALUES (?,?,?,?,?,?,?,?,?,NULL,'settled',?,?,?,?,?,"
        "'cloud',?)",
        (
            _record_id("cmd", operation_id, command_type),
            identity.scope_id,
            operation_id,
            idempotency_key,
            aggregate_type,
            aggregate_id,
            command_type,
            identity.principal_id,
            expected_version,
            identity.membership_id,
            result_manifest_id,
            payload_hash,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    event_hash = sha256_text(
        f"{command_type}|{aggregate_id}|{aggregate_version}|{result_hash}"
    )
    connection.execute(
        "INSERT INTO audit_events "
        "(id,scope_id,operation_id,actor_id,action,event_hash,actor_membership_id,"
        "target_resource_id,details_object_manifest_id,occurred_at,origin_instance_id,"
        "created_at,integrity_hash,authority_role) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'cloud')",
        (
            _record_id("audit", operation_id, command_type),
            identity.scope_id,
            operation_id,
            identity.principal_id,
            command_type,
            event_hash,
            identity.membership_id,
            target_resource_id,
            result_manifest_id,
            now,
            repository.cloud_instance_id,
            now,
            event_hash,
        ),
    )
    connection.execute(
        "INSERT INTO outbox_events "
        "(id,scope_id,operation_id,aggregate_version,event_type,status,aggregate_type,"
        "aggregate_id,event_object_manifest_id,event_hash,available_at,published_at,authority_role,"
        "origin_instance_id) VALUES (?,?,?,?,?,'published',?,?,?,?,?,?,'cloud',?)",
        (
            _record_id("evt", operation_id, command_type),
            identity.scope_id,
            operation_id,
            aggregate_version,
            command_type,
            aggregate_type,
            aggregate_id,
            result_manifest_id,
            event_hash,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    return operation_id


def create_proposal(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    client_id = str(payload.get("clientId") or payload.get("projectId") or "").strip()
    kind = str(payload.get("kind") or "context_refresh").strip()
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    answer_id = str(payload.get("answerId") or payload.get("sourceAnswerId") or "").strip() or None
    risk_level = str(payload.get("riskLevel") or "medium").strip()
    if not client_id or not title or not summary:
        raise RepositoryError(422, "ai_proposal_required_fields", "提案缺少项目、标题或摘要")
    if kind not in PROPOSAL_KINDS:
        raise RepositoryError(422, "ai_proposal_kind_invalid", "提案类型无效")
    if risk_level not in RISK_LEVELS:
        raise RepositoryError(422, "ai_proposal_risk_invalid", "提案风险等级无效")
    receipt = {
        "clientId": client_id,
        "kind": kind,
        "title": title,
        "summary": summary,
        "rationale": str(payload.get("rationale") or summary),
        "riskLevel": risk_level,
        "targetRefs": list(payload.get("targetRefs") or []),
        "sourceRefs": list(payload.get("sourceRefs") or []),
        "boundaryNotes": list(payload.get("boundaryNotes") or []),
        "payload": dict(payload.get("payload") or {}) if isinstance(payload.get("payload"), Mapping) else {},
        "createdBy": identity.principal_id,
        "materialBoundary": {"localPathStored": False, "sourceFileContentStored": False},
    }
    normalized = {**receipt, "answerId": answer_id}
    payload_hash = sha256_text(canonical_json(normalized))
    command_type = "gc14.ai_proposal.created"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            repository._require_project_access(  # noqa: SLF001
                connection, identity, project_id=client_id, capability="project_write"
            )
            existing = repository._existing_command(  # noqa: SLF001
                connection,
                scope_id=identity.scope_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                payload_hash=payload_hash,
            )
            if existing is not None:
                row = _proposal_row(connection, identity, str(existing["aggregate_id"]))
                if row is None:
                    raise RepositoryError(409, "ai_proposal_replay_missing", "提案回执已失效")
                result = _proposal_payload(connection, identity, row)
                result["idempotentReplay"] = True
                connection.commit()
                return result
            if answer_id:
                answer = connection.execute(
                    "SELECT client_id FROM ai_answers WHERE scope_id=? AND id=? "
                    "AND lifecycle_state='active'",
                    (identity.scope_id, answer_id),
                ).fetchone()
                if answer is None or str(answer["client_id"] or "") != client_id:
                    raise RepositoryError(409, "ai_proposal_answer_scope_mismatch", "回答不属于当前项目")
            now = utc_now()
            operation_id = _operation_id(identity.scope_id, command_type, idempotency_key)
            proposal_id = _record_id("proposal", operation_id, client_id)
            manifest_id = _record_id("manifest", operation_id, "proposal")
            result_hash = _manifest(
                connection,
                repository,
                identity,
                manifest_id=manifest_id,
                receipt=receipt,
                media_type="application/vnd.yiyu.ai-proposal+json",
                now=now,
            )
            connection.execute(
                "INSERT INTO ai_proposals "
                "(id,scope_id,answer_id,operation_kind,payload_hash,status,"
                "payload_object_manifest_id,risk_level,expires_at,version,lifecycle_state,"
                "created_at,updated_at,deleted_at) VALUES (?,?,?,?,?,'draft',?,?,NULL,1,"
                "'active',?,?,NULL)",
                (
                    proposal_id,
                    identity.scope_id,
                    answer_id,
                    kind,
                    payload_hash,
                    manifest_id,
                    risk_level,
                    now,
                    now,
                ),
            )
            _record_command(
                connection,
                repository,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="ai_proposal",
                aggregate_id=proposal_id,
                expected_version=None,
                aggregate_version=1,
                payload_hash=payload_hash,
                result_hash=result_hash,
                result_manifest_id=manifest_id,
                target_resource_id=client_id,
                now=now,
            )
            row = _proposal_row(connection, identity, proposal_id)
            if row is None:
                raise RepositoryError(500, "ai_proposal_write_lost", "提案创建后无法读取")
            result = _proposal_payload(connection, identity, row)
            result["idempotentReplay"] = False
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def list_proposals(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    client_id: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT p.*,m.receipt FROM ai_proposals p JOIN object_manifests m "
            "ON m.scope_id=p.scope_id AND m.id=p.payload_object_manifest_id "
            "WHERE p.scope_id=? AND p.lifecycle_state='active' ORDER BY p.updated_at DESC,p.id LIMIT ?",
            (identity.scope_id, max(1, min(limit, 200))),
        ).fetchall()
        result = []
        for row in rows:
            receipt = _json(row["receipt"], {})
            project_id = str(receipt.get("clientId") or "") if isinstance(receipt, Mapping) else ""
            if not project_id or (client_id and project_id != client_id):
                continue
            try:
                repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=project_id
                )
            except RepositoryError:
                continue
            if status and str(row["status"] or "") != status:
                continue
            if kind and str(row["operation_kind"] or "") != kind:
                continue
            result.append(_proposal_payload(connection, identity, row))
        return result


def get_proposal(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    proposal_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = _proposal_row(connection, identity, proposal_id)
        if row is None:
            raise RepositoryError(404, "ai_proposal_missing", "提案不存在")
        receipt = _json(row["receipt"], {})
        client_id = str(receipt.get("clientId") or "") if isinstance(receipt, Mapping) else ""
        repository._require_project_access(  # noqa: SLF001
            connection, identity, project_id=client_id
        )
        return _proposal_payload(connection, identity, row)


def decide_proposal(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    proposal_id: str,
    decision: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise RepositoryError(422, "ai_proposal_decision_invalid", "提案决定无效")
    command_type = f"gc14.ai_proposal.{decision}"
    note = str(payload.get("note") or payload.get("comment") or "").strip()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = _proposal_row(connection, identity, proposal_id)
            if row is None:
                raise RepositoryError(404, "ai_proposal_missing", "提案不存在")
            receipt = _json(row["receipt"], {})
            client_id = str(receipt.get("clientId") or "") if isinstance(receipt, Mapping) else ""
            repository._require_project_access(  # noqa: SLF001
                connection, identity, project_id=client_id, capability="project_write"
            )
            current_version = int(row["version"] or 1)
            expected_version = int(payload.get("expectedVersion") or current_version)
            normalized = {
                "proposalId": proposal_id,
                "decision": decision,
                "noteHash": sha256_text(note),
                "expectedVersion": expected_version,
            }
            payload_hash = sha256_text(canonical_json(normalized))
            existing = repository._existing_command(  # noqa: SLF001
                connection,
                scope_id=identity.scope_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                payload_hash=payload_hash,
            )
            if existing is not None:
                replay = _proposal_row(connection, identity, proposal_id)
                if replay is None:
                    raise RepositoryError(409, "ai_proposal_replay_missing", "提案回执已失效")
                result = _proposal_payload(connection, identity, replay)
                result["idempotentReplay"] = True
                connection.commit()
                return result
            if current_version != expected_version:
                raise RepositoryError(409, "ai_proposal_version_conflict", "提案已变化，请刷新后重试")
            if str(row["status"] or "") != "draft":
                raise RepositoryError(409, "ai_proposal_already_decided", "提案已经处理")
            now = utc_now()
            next_version = current_version + 1
            operation_id = _operation_id(identity.scope_id, command_type, idempotency_key)
            approval_id = _record_id("approval", operation_id, proposal_id)
            connection.execute(
                "INSERT INTO ai_approvals "
                "(id,scope_id,proposal_id,approver_principal_id,decision,decided_at,"
                "approver_membership_id,decision_note,approved_rule_id,version,lifecycle_state,"
                "created_at,updated_at,deleted_at) VALUES (?,?,?,?,?,?,?,?,NULL,1,'active',?,?,NULL)",
                (
                    approval_id,
                    identity.scope_id,
                    proposal_id,
                    identity.principal_id,
                    decision,
                    now,
                    identity.membership_id,
                    note,
                    now,
                    now,
                ),
            )
            cursor = connection.execute(
                "UPDATE ai_proposals SET status=?,version=?,updated_at=? WHERE scope_id=? "
                "AND id=? AND version=? AND status='draft'",
                (
                    decision,
                    next_version,
                    now,
                    identity.scope_id,
                    proposal_id,
                    current_version,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryError(409, "ai_proposal_version_conflict", "提案已变化，请刷新后重试")
            result_hash = sha256_text(f"{proposal_id}|{decision}|{next_version}|{note}")
            _record_command(
                connection,
                repository,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="ai_proposal",
                aggregate_id=proposal_id,
                expected_version=current_version,
                aggregate_version=next_version,
                payload_hash=payload_hash,
                result_hash=result_hash,
                result_manifest_id=str(row["payload_object_manifest_id"]),
                target_resource_id=client_id,
                now=now,
            )
            updated = _proposal_row(connection, identity, proposal_id)
            result = _proposal_payload(connection, identity, updated)
            result["idempotentReplay"] = False
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def execution_preview(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    proposal_id: str,
) -> dict[str, Any]:
    proposal = get_proposal(repository, identity, proposal_id=proposal_id)
    kind = str(proposal["kind"])
    controlled_only = kind in {"context_refresh", "judgment_review", "evidence_request"}
    return {
        "proposalId": proposal_id,
        "executionType": "recorded_only" if controlled_only else "formal_command_required",
        "riskLevel": proposal["riskLevel"],
        "willCreateTask": False,
        "willCreatePrepArtifact": False,
        "willCreateEvidenceRequest": False,
        "willUpdateEventLine": False,
        "summary": (
            "本次只登记已确认的分析动作，不修改业务事实"
            if controlled_only
            else "该建议必须转入对应正式业务命令，本入口不会暗中写入"
        ),
        "warnings": [] if controlled_only else ["正式业务命令尚未由本提案执行器接管"],
    }


def execute_proposal(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    proposal_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    command_type = "gc14.ai_proposal.executed"
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = _proposal_row(connection, identity, proposal_id)
            if row is None:
                raise RepositoryError(404, "ai_proposal_missing", "提案不存在")
            receipt = _json(row["receipt"], {})
            client_id = str(receipt.get("clientId") or "") if isinstance(receipt, Mapping) else ""
            repository._require_project_access(  # noqa: SLF001
                connection, identity, project_id=client_id, capability="project_write"
            )
            kind = str(row["operation_kind"] or "")
            if kind not in {"context_refresh", "judgment_review", "evidence_request"}:
                raise RepositoryError(
                    409,
                    "ai_proposal_formal_command_required",
                    "该提案必须进入对应正式业务命令，不能从通用执行器直接写入",
                )
            current_version = int(row["version"] or 1)
            normalized = {
                "proposalId": proposal_id,
                "dryRun": bool(payload.get("dryRun")),
            }
            payload_hash = sha256_text(canonical_json(normalized))
            existing = repository._existing_command(  # noqa: SLF001
                connection,
                scope_id=identity.scope_id,
                idempotency_key=idempotency_key,
                command_type=command_type,
                payload_hash=payload_hash,
            )
            if existing is not None:
                replay = _proposal_row(connection, identity, proposal_id)
                result = _proposal_payload(connection, identity, replay)
                result["idempotentReplay"] = True
                connection.commit()
                return result
            if str(row["status"] or "") != "approved":
                raise RepositoryError(409, "ai_proposal_not_approved", "提案批准后才能执行")
            expected_bot_id = builtin_agent_id(identity.organization_id, "project_workspace")
            bot = connection.execute(
                "SELECT bot.id FROM bot_definitions AS bot "
                "JOIN authorization_scopes AS agent_scope ON agent_scope.id=bot.scope_id "
                "WHERE bot.id=? AND bot.agent_kind='project_workspace' "
                "AND bot.enabled=1 AND bot.lifecycle_state='active' "
                "AND agent_scope.organization_id=? AND agent_scope.status='active' "
                "AND agent_scope.lifecycle_state='active'",
                (expected_bot_id, identity.organization_id),
            ).fetchone()
            if bot is None:
                raise RepositoryError(409, "project_workspace_agent_not_connected", "项目工作台建议能力尚未接通")
            now = utc_now()
            next_version = current_version + 1
            operation_id = _operation_id(identity.scope_id, command_type, idempotency_key)
            run_id = _record_id("run", operation_id, proposal_id)
            result_receipt = {
                "resultType": "recorded_only",
                "summary": "提案已按用户批准登记完成；未产生隐藏业务写入",
                "createdTaskIds": [],
                "artifactRefs": [],
                "proposalId": proposal_id,
                "clientId": client_id,
            }
            manifest_id = _record_id("manifest", operation_id, "execution")
            result_hash = _manifest(
                connection,
                repository,
                identity,
                manifest_id=manifest_id,
                receipt=result_receipt,
                media_type="application/vnd.yiyu.ai-proposal-execution+json",
                now=now,
            )
            _record_command(
                connection,
                repository,
                identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                aggregate_type="execution_run",
                aggregate_id=run_id,
                expected_version=current_version,
                aggregate_version=1,
                payload_hash=payload_hash,
                result_hash=result_hash,
                result_manifest_id=manifest_id,
                target_resource_id=client_id,
                now=now,
            )
            connection.execute(
                "INSERT INTO execution_runs "
                "(id,scope_id,bot_id,rule_id,task_id,operation_id,status,initiator_membership_id,"
                "proposal_id,run_kind,progress_object_manifest_id,result_object_manifest_id,"
                "started_at,finished_at,version,lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,NULL,NULL,?,'executed',?,?,'proposal_controlled_execution',NULL,?,"
                "?,?,1,'active',?,?,NULL)",
                (
                    run_id,
                    identity.scope_id,
                    str(bot["id"]),
                    operation_id,
                    identity.membership_id,
                    proposal_id,
                    manifest_id,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            cursor = connection.execute(
                "UPDATE ai_proposals SET status='executed',version=?,updated_at=? WHERE scope_id=? "
                "AND id=? AND version=? AND status='approved'",
                (next_version, now, identity.scope_id, proposal_id, current_version),
            )
            if cursor.rowcount != 1:
                raise RepositoryError(409, "ai_proposal_version_conflict", "提案已变化，请刷新后重试")
            updated = _proposal_row(connection, identity, proposal_id)
            result = _proposal_payload(connection, identity, updated)
            result["idempotentReplay"] = False
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def list_execution_runs(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    client_id: str | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    proposals = list_proposals(
        repository, identity, client_id=client_id, limit=max(1, min(limit, 200))
    )
    return [
        dict(item["executionTicket"])
        for item in proposals
        if isinstance(item.get("executionTicket"), Mapping)
    ]
