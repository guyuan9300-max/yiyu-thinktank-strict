from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

from strict_common.ids import canonical_json, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity


@dataclass(frozen=True)
class WeeklyReviewGrowthCandidate:
    """Typed hand-off owned by thread B.

    GC-13 never queries weekly_reviews or weekly_review_versions.  Thread B
    creates this candidate from its own authoritative review flow.  Calling
    ``confirm_weekly_review_candidate`` means the member has explicitly
    confirmed the candidate as a new growth fact; it is not an automatic copy
    of the weekly-review fact.
    """

    candidate_id: str
    review_id: str
    review_version_id: str
    source_version: int
    source_hash: str
    summary: str
    category: str
    contribution_score: float = 1.0
    adapter_version: str = "gc06-to-gc13.v1"


def confirm_weekly_review_candidate(
    repository: object,
    identity: object,
    *,
    candidate: WeeklyReviewGrowthCandidate,
    idempotency_key: str,
) -> dict:
    from .gc13_growth import confirm_growth_evidence

    return confirm_growth_evidence(
        repository,  # type: ignore[arg-type]
        identity,  # type: ignore[arg-type]
        payload={
            "summary": candidate.summary,
            "category": candidate.category,
            "sourceType": "weekly_review_candidate",
            "sourceId": candidate.candidate_id,
            "sourceVersion": candidate.source_version,
            "sourceHash": candidate.source_hash,
            "contributionScore": candidate.contribution_score,
            "sourceMetadata": {
                "candidateId": candidate.candidate_id,
                "reviewId": candidate.review_id,
                "reviewVersionId": candidate.review_version_id,
                "adapterVersion": candidate.adapter_version,
            },
        },
        idempotency_key=idempotency_key,
    )


CANDIDATE_KIND = "weekly_review_growth_candidate"


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _candidate_id(scope_id: str, review_version_id: str) -> str:
    return "proposal_" + sha256_text(
        f"gc06-weekly-review-growth\x1f{scope_id}\x1f{review_version_id}"
    )[:30]


def _candidate_summary(content: Mapping[str, Any], cycle_title: str) -> str:
    preferred = (
        "summary",
        "reflection",
        "achievements",
        "learning",
        "learnings",
        "nextSteps",
        "content",
    )
    parts: list[str] = []
    for key in preferred:
        value = content.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, list):
            parts.extend(str(item).strip() for item in value if str(item).strip())
        if sum(len(item) for item in parts) >= 1_800:
            break
    summary = "；".join(parts).strip()
    return (summary or f"已完成“{cycle_title or '本周期'}”周复盘，待本人确认成长收获")[:2_000]


def create_weekly_review_growth_candidate(
    connection: sqlite3.Connection,
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    review: sqlite3.Row,
    review_version: sqlite3.Row,
    client_id: str | None,
    cycle_title: str,
    now: str,
) -> dict[str, Any]:
    """Create the member-private proposal in the same GC-06 submit transaction."""

    manifest = connection.execute(
        "SELECT receipt FROM object_manifests WHERE scope_id=? AND id=?",
        (identity.scope_id, review_version["content_object_manifest_id"]),
    ).fetchone()
    review_receipt = _json(manifest["receipt"], {}) if manifest is not None else {}
    content = review_receipt.get("content") if isinstance(review_receipt, Mapping) else {}
    content = content if isinstance(content, Mapping) else {}
    proposal_id = _candidate_id(identity.scope_id, str(review_version["id"]))
    existing = connection.execute(
        "SELECT status,version FROM ai_proposals WHERE scope_id=? AND id=?",
        (identity.scope_id, proposal_id),
    ).fetchone()
    if existing is not None:
        return {
            "candidateId": proposal_id,
            "status": str(existing["status"]),
            "version": int(existing["version"] or 1),
        }
    summary = _candidate_summary(content, cycle_title)
    payload = {
        "candidateId": proposal_id,
        "reviewId": str(review["id"]),
        "reviewVersionId": str(review_version["id"]),
        "sourceVersion": int(review_version["version"] or 1),
        "sourceHash": str(review_version["content_hash"]),
        "summary": summary,
        "category": "reflection",
        "membershipId": str(review["membership_id"]),
    }
    receipt = {
        "schema": "yiyu.gc06.weekly-review-growth-candidate.v1",
        "clientId": str(client_id or ""),
        "kind": CANDIDATE_KIND,
        "title": "周复盘成长候选",
        "summary": summary,
        "rationale": "周复盘已正式提交；只有成员本人确认后才成为成长证据。",
        "riskLevel": "low",
        "targetRefs": [{"kind": "membership", "id": str(review["membership_id"])}],
        "sourceRefs": [{
            "kind": "weekly_review_version",
            "id": str(review_version["id"]),
            "version": int(review_version["version"] or 1),
        }],
        "boundaryNotes": ["未确认前不写 growth_evidence", "不复制项目协作记忆"],
        "payload": payload,
        "createdBy": identity.principal_id,
        "materialBoundary": {"localPathStored": False, "sourceFileContentStored": False},
    }
    serialized = canonical_json(receipt)
    receipt_hash = sha256_text(serialized)
    manifest_id = "manifest_" + sha256_text(f"{proposal_id}|payload")[:30]
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
            "application/vnd.yiyu.weekly-review-growth-candidate+json",
            receipt_hash,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    connection.execute(
        "INSERT INTO ai_proposals "
        "(id,scope_id,answer_id,operation_kind,payload_hash,status,"
        "payload_object_manifest_id,risk_level,expires_at,version,lifecycle_state,"
        "created_at,updated_at,deleted_at) VALUES (?,?,NULL,?,?,'draft',?,'low',NULL,1,"
        "'active',?,?,NULL)",
        (
            proposal_id,
            identity.scope_id,
            CANDIDATE_KIND,
            receipt_hash,
            manifest_id,
            now,
            now,
        ),
    )
    return {"candidateId": proposal_id, "status": "draft", "version": 1}


def _candidate_payload(row: sqlite3.Row) -> dict[str, Any]:
    receipt = _json(row["receipt"], {})
    receipt = receipt if isinstance(receipt, Mapping) else {}
    payload = receipt.get("payload") if isinstance(receipt.get("payload"), Mapping) else {}
    status = str(row["status"] or "draft")
    return {
        "candidateId": str(row["id"]),
        "status": {
            "draft": "pending_confirmation",
            "approved": "confirming",
            "executed": "confirmed",
            "rejected": "ignored",
        }.get(status, status),
        "summary": str(payload.get("summary") or receipt.get("summary") or ""),
        "category": str(payload.get("category") or "reflection"),
        "reviewId": str(payload.get("reviewId") or ""),
        "reviewVersionId": str(payload.get("reviewVersionId") or ""),
        "sourceVersion": int(payload.get("sourceVersion") or 1),
        "sourceHash": str(payload.get("sourceHash") or ""),
        "version": int(row["version"] or 1),
        "createdAt": str(row["created_at"] or ""),
    }


def list_weekly_review_growth_candidates(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    include_settled: bool = False,
) -> list[dict[str, Any]]:
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT p.*,m.receipt FROM ai_proposals p JOIN object_manifests m "
            "ON m.scope_id=p.scope_id AND m.id=p.payload_object_manifest_id "
            "WHERE p.scope_id=? AND p.operation_kind=? AND p.lifecycle_state='active' "
            "ORDER BY p.created_at DESC,p.id DESC",
            (identity.scope_id, CANDIDATE_KIND),
        ).fetchall()
    result = []
    for row in rows:
        receipt = _json(row["receipt"], {})
        payload = receipt.get("payload") if isinstance(receipt, Mapping) else {}
        if not isinstance(payload, Mapping) or str(payload.get("membershipId") or "") != identity.membership_id:
            continue
        if not include_settled and str(row["status"] or "") in {"executed", "rejected"}:
            continue
        result.append(_candidate_payload(row))
    return result


def _candidate_row(
    repository: CloudRepository,
    identity: SessionIdentity,
    candidate_id: str,
) -> sqlite3.Row:
    with repository._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            "SELECT p.*,m.receipt FROM ai_proposals p JOIN object_manifests m "
            "ON m.scope_id=p.scope_id AND m.id=p.payload_object_manifest_id "
            "WHERE p.scope_id=? AND p.id=? AND p.operation_kind=? AND p.lifecycle_state='active'",
            (identity.scope_id, candidate_id, CANDIDATE_KIND),
        ).fetchone()
    if row is None:
        raise RepositoryError(404, "weekly_review_growth_candidate_missing", "周复盘成长候选不存在")
    receipt = _json(row["receipt"], {})
    payload = receipt.get("payload") if isinstance(receipt, Mapping) else {}
    if not isinstance(payload, Mapping) or str(payload.get("membershipId") or "") != identity.membership_id:
        raise RepositoryError(403, "weekly_review_growth_candidate_forbidden", "只能处理本人的成长候选")
    return row


def _decide_candidate(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    candidate_id: str,
    decision: str,
    idempotency_key: str,
) -> sqlite3.Row:
    from .gc14_proposals import _operation_id, _record_command, _record_id

    command_type = f"gc13.weekly_review_growth_candidate.{decision}"
    payload_hash = sha256_text(canonical_json({
        "candidateId": candidate_id,
        "decision": decision,
        "membershipId": identity.membership_id,
    }))
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT p.*,m.receipt FROM ai_proposals p JOIN object_manifests m "
            "ON m.scope_id=p.scope_id AND m.id=p.payload_object_manifest_id "
            "WHERE p.scope_id=? AND p.id=? AND p.operation_kind=? AND p.lifecycle_state='active'",
            (identity.scope_id, candidate_id, CANDIDATE_KIND),
        ).fetchone()
        if row is None:
            connection.rollback()
            raise RepositoryError(404, "weekly_review_growth_candidate_missing", "周复盘成长候选不存在")
        receipt = _json(row["receipt"], {})
        payload = receipt.get("payload") if isinstance(receipt, Mapping) else {}
        if not isinstance(payload, Mapping) or str(payload.get("membershipId") or "") != identity.membership_id:
            connection.rollback()
            raise RepositoryError(403, "weekly_review_growth_candidate_forbidden", "只能处理本人的成长候选")
        existing = repository._existing_command(  # noqa: SLF001
            connection,
            scope_id=identity.scope_id,
            idempotency_key=idempotency_key,
            command_type=command_type,
            payload_hash=payload_hash,
        )
        target_status = "approved" if decision == "approved" else "rejected"
        if existing is not None:
            connection.commit()
            return _candidate_row(repository, identity, candidate_id)
        if str(row["status"] or "") != "draft":
            connection.rollback()
            raise RepositoryError(409, "weekly_review_growth_candidate_already_decided", "该成长候选已经处理")
        now = utc_now()
        current_version = int(row["version"] or 1)
        next_version = current_version + 1
        operation_id = _operation_id(identity.scope_id, command_type, idempotency_key)
        connection.execute(
            "INSERT INTO ai_approvals "
            "(id,scope_id,proposal_id,approver_principal_id,decision,decided_at,"
            "approver_membership_id,decision_note,approved_rule_id,version,lifecycle_state,"
            "created_at,updated_at,deleted_at) VALUES (?,?,?,?,?,?,?,?,NULL,1,'active',?,?,NULL)",
            (
                _record_id("approval", operation_id, candidate_id),
                identity.scope_id,
                candidate_id,
                identity.principal_id,
                target_status,
                now,
                identity.membership_id,
                "本人确认" if decision == "approved" else "本人忽略",
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE ai_proposals SET status=?,version=?,updated_at=? WHERE scope_id=? AND id=?",
            (target_status, next_version, now, identity.scope_id, candidate_id),
        )
        result_hash = sha256_text(f"{candidate_id}|{target_status}|{next_version}")
        _record_command(
            connection,
            repository,
            identity,
            command_type=command_type,
            idempotency_key=idempotency_key,
            aggregate_type="ai_proposal",
            aggregate_id=candidate_id,
            expected_version=current_version,
            aggregate_version=next_version,
            payload_hash=payload_hash,
            result_hash=result_hash,
            result_manifest_id=str(row["payload_object_manifest_id"]),
            target_resource_id=str(receipt.get("clientId") or "") or None,
            now=now,
        )
        connection.commit()
    return _candidate_row(repository, identity, candidate_id)


def confirm_candidate_proposal(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    candidate_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    row = _candidate_row(repository, identity, candidate_id)
    if str(row["status"] or "") == "executed":
        return {"candidate": _candidate_payload(row), "idempotentReplay": True}
    if str(row["status"] or "") == "rejected":
        raise RepositoryError(409, "weekly_review_growth_candidate_ignored", "该成长候选已忽略")
    if str(row["status"] or "") == "draft":
        row = _decide_candidate(
            repository,
            identity,
            candidate_id=candidate_id,
            decision="approved",
            idempotency_key=idempotency_key + ":approve",
        )
    receipt = _json(row["receipt"], {})
    payload = receipt.get("payload") if isinstance(receipt, Mapping) else {}
    candidate = WeeklyReviewGrowthCandidate(
        candidate_id=candidate_id,
        review_id=str(payload.get("reviewId") or ""),
        review_version_id=str(payload.get("reviewVersionId") or ""),
        source_version=int(payload.get("sourceVersion") or 1),
        source_hash=str(payload.get("sourceHash") or ""),
        summary=str(payload.get("summary") or ""),
        category=str(payload.get("category") or "reflection"),
    )
    evidence = confirm_weekly_review_candidate(
        repository,
        identity,
        candidate=candidate,
        idempotency_key=f"weekly-review-growth:{candidate_id}:confirm",
    )
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE ai_proposals SET status='executed',version=version+1,updated_at=? "
            "WHERE scope_id=? AND id=? AND status='approved'",
            (now, identity.scope_id, candidate_id),
        )
        connection.commit()
    return {
        "candidate": _candidate_payload(_candidate_row(repository, identity, candidate_id)),
        "evidence": evidence["evidence"],
        "idempotentReplay": bool(evidence.get("idempotentReplay")),
    }


def ignore_candidate_proposal(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    candidate_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    row = _candidate_row(repository, identity, candidate_id)
    if str(row["status"] or "") == "rejected":
        return {"candidate": _candidate_payload(row), "idempotentReplay": True}
    if str(row["status"] or "") != "draft":
        raise RepositoryError(409, "weekly_review_growth_candidate_already_confirming", "该成长候选已进入确认流程")
    _decide_candidate(
        repository,
        identity,
        candidate_id=candidate_id,
        decision="rejected",
        idempotency_key=idempotency_key,
    )
    return {
        "candidate": _candidate_payload(_candidate_row(repository, identity, candidate_id)),
        "idempotentReplay": False,
    }
