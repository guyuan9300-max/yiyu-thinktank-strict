from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from strict_common.agent_memory import AgentRunReceipt, builtin_agent_id
from strict_common.ids import canonical_json, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity


EVIDENCE_SCHEMA = "yiyu.gc13.growth-evidence.v1"
RULE_SCHEMA = "yiyu.gc13.metric-rule.v1"
READ_MODEL_SCHEMA = "yiyu.gc13.growth-read-model.v1"
PREFERENCE_SCHEMA = "yiyu.member-stable-preference.v1"
GENERATOR_VERSION = "gc13-growth-rebuild-v1"
RECONCILIATION_KIND = "gc13_growth_read_model"

BUILTIN_GROWTH_RULES: tuple[dict[str, Any], ...] = (
    {
        "metricKey": "execution_delivery",
        "label": "执行与交付",
        "abilityKey": "execution",
        "abilityLabel": "执行与交付",
        "evidenceCategories": ["execution", "risk"],
    },
    {
        "metricKey": "collaboration_practice",
        "label": "协作实践",
        "abilityKey": "collaboration",
        "abilityLabel": "协作能力",
        "evidenceCategories": ["collaboration"],
    },
    {
        "metricKey": "analysis_insight",
        "label": "分析与洞察",
        "abilityKey": "analysis",
        "abilityLabel": "分析与洞察",
        "evidenceCategories": ["analysis", "insight"],
    },
    {
        "metricKey": "knowledge_reflection",
        "label": "知识沉淀与复盘",
        "abilityKey": "reflection",
        "abilityLabel": "知识沉淀与复盘",
        "evidenceCategories": ["writing", "learning", "reflection"],
    },
)

ALLOWED_CATEGORIES = frozenset(
    {
        "execution",
        "collaboration",
        "analysis",
        "insight",
        "risk",
        "writing",
        "learning",
        "reflection",
    }
)
ALLOWED_SOURCE_TYPES = frozenset(
    {
        "manual_reflection",
        "weekly_review_candidate",
        "learning_practice",
        "confirmed_outcome",
        "formal_task",
        "formal_meeting",
        "weekly_review",
    }
)
FORBIDDEN_SOURCE_TYPES = frozenset(
    {
        "project_memory",
        "project_collaboration_memory",
        "atomic_fact",
        "agent_skill",
        "skill",
    }
)


ProjectionEvaluator = Callable[
    [Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]],
    list[dict[str, Any]],
]


def _json(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return parsed


def _identifier(value: Any, *, field: str, maximum: int = 120) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum:
        raise RepositoryError(422, f"gc13_{field}_invalid", f"{field} 无效")
    return normalized


def _sha256(value: Any, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise RepositoryError(422, f"gc13_{field}_invalid", f"{field} 必须是 SHA-256")
    return normalized


def _result_manifest(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    operation_id: str,
    kind: str,
    result: Mapping[str, Any],
    now: str,
) -> tuple[str, str]:
    receipt = canonical_json(
        {
            "schema": "yiyu.gc13.command-receipt.v1",
            "kind": kind,
            "result": dict(result),
        }
    )
    receipt_hash = sha256_text(receipt)
    manifest_id = repository._record_id("manifest", operation_id, kind)  # noqa: SLF001
    connection.execute(
        """
        INSERT INTO object_manifests (
            id, scope_id, storage_key, content_hash, lifecycle_state, receipt,
            holder_role, holder_instance_id, storage_kind, byte_size,
            media_type, availability_state, receipt_hash, created_at,
            verified_at, deleted_at, authority_role, origin_instance_id
        ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud', ?, ?, ?,
                  'application/json', 'verified', ?, ?, ?, NULL, 'cloud', ?)
        """,
        (
            manifest_id,
            identity.scope_id,
            receipt_hash,
            receipt,
            repository.cloud_instance_id,
            kind,
            len(receipt.encode("utf-8")),
            receipt_hash,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    return manifest_id, receipt_hash


def _replay(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    idempotency_key: str,
    payload_hash: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT idem.payload_hash, manifest.receipt
        FROM idempotency_records AS idem
        LEFT JOIN object_manifests AS manifest
          ON manifest.scope_id=idem.scope_id
         AND manifest.id=idem.result_object_manifest_id
        WHERE idem.scope_id=? AND idem.idempotency_key=?
        """,
        (identity.scope_id, idempotency_key),
    ).fetchone()
    if row is None:
        return None
    if str(row["payload_hash"] or "") != payload_hash:
        raise RepositoryError(
            409,
            "gc13_idempotency_payload_conflict",
            "同一幂等键不能提交不同的成长命令",
        )
    receipt = _json(row["receipt"], {})
    result = receipt.get("result") if isinstance(receipt, Mapping) else None
    if not isinstance(result, Mapping):
        raise RepositoryError(409, "gc13_idempotency_receipt_missing", "成长命令回执不完整")
    return {**dict(result), "idempotentReplay": True}


def _trace_command(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    operation_id: str,
    idempotency_key: str,
    payload_hash: str,
    result_manifest_id: str,
    result_hash: str,
    command_type: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    target_resource_id: str | None,
    command_status: str,
    event_type: str,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO idempotency_records (
            id, scope_id, idempotency_key, payload_hash, result_hash,
            expires_at, result_object_manifest_id, status, created_at,
            authority_role, origin_instance_id
        ) VALUES (?, ?, ?, ?, ?, '9999-12-31T23:59:59.999Z', ?, ?, ?,
                  'cloud', ?)
        """,
        (
            repository._record_id("idem", operation_id, command_type),  # noqa: SLF001
            identity.scope_id,
            idempotency_key,
            payload_hash,
            result_hash,
            result_manifest_id,
            command_status,
            now,
            repository.cloud_instance_id,
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?,
                  'cloud', ?)
        """,
        (
            repository._record_id("cmd", operation_id, command_type),  # noqa: SLF001
            identity.scope_id,
            operation_id,
            idempotency_key,
            aggregate_type,
            aggregate_id,
            command_type,
            identity.principal_id,
            command_status,
            identity.membership_id,
            result_manifest_id,
            payload_hash,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )
    event_hash = sha256_text(
        f"{event_type}|{aggregate_type}|{aggregate_id}|{aggregate_version}|{result_hash}"
    )
    connection.execute(
        """
        INSERT INTO audit_events (
            id, scope_id, operation_id, actor_id, action, event_hash,
            actor_membership_id, target_resource_id, details_object_manifest_id,
            occurred_at, origin_instance_id, created_at, integrity_hash,
            authority_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'cloud')
        """,
        (
            repository._record_id("audit", operation_id, command_type),  # noqa: SLF001
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
        """
        INSERT INTO outbox_events (
            id, scope_id, operation_id, aggregate_version, event_type, status,
            aggregate_type, aggregate_id, event_object_manifest_id, event_hash,
            available_at, published_at, authority_role, origin_instance_id
        ) VALUES (?, ?, ?, ?, ?, 'published', ?, ?, ?, ?, ?, ?, 'cloud', ?)
        """,
        (
            repository._record_id("evt", operation_id, event_type),  # noqa: SLF001
            identity.scope_id,
            operation_id,
            aggregate_version,
            event_type,
            aggregate_type,
            aggregate_id,
            result_manifest_id,
            event_hash,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )


def _pending_reconciliation(
    repository: CloudRepository,
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    operation_id: str,
    reason: str,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO reconciliation_runs (
            id, scope_id, operation_id, registry_state_id, mismatch_count,
            status, reconciliation_kind, target_instance_id,
            result_object_manifest_id, started_at, completed_at, version,
            lifecycle_state, created_at, updated_at, deleted_at,
            authority_role, origin_instance_id
        ) VALUES (?, ?, ?, NULL, 1, 'pending', ?, ?, NULL, ?, NULL, 1,
                  'active', ?, ?, NULL, 'cloud', ?)
        """,
        (
            repository._record_id("reconcile", operation_id, reason),  # noqa: SLF001
            identity.scope_id,
            operation_id,
            RECONCILIATION_KIND,
            repository.cloud_instance_id,
            now,
            now,
            now,
            repository.cloud_instance_id,
        ),
    )


def _validate_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    summary = str(payload.get("summary") or "").strip()
    if not 1 <= len(summary) <= 2_000:
        raise RepositoryError(422, "gc13_evidence_summary_invalid", "成长证据摘要需为1至2000个字符")
    category = str(payload.get("category") or "").strip()
    if category not in ALLOWED_CATEGORIES:
        raise RepositoryError(422, "gc13_evidence_category_invalid", "成长证据分类无效")
    source_type = str(payload.get("sourceType") or "manual_reflection").strip()
    if source_type in FORBIDDEN_SOURCE_TYPES:
        raise RepositoryError(
            422,
            "gc13_evidence_source_forbidden",
            "项目协作记忆与 Skill 不能成为个人成长事实",
        )
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise RepositoryError(422, "gc13_evidence_source_invalid", "成长证据来源类型无效")
    source_id = str(payload.get("sourceId") or "").strip()
    if not source_id and source_type == "manual_reflection":
        source_id = "manual_" + sha256_text(summary)[:24]
    source_id = _identifier(source_id, field="source_id", maximum=160)
    try:
        source_version = int(payload.get("sourceVersion") or 1)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(422, "gc13_source_version_invalid", "成长来源版本无效") from exc
    if source_version < 1:
        raise RepositoryError(422, "gc13_source_version_invalid", "成长来源版本无效")
    source_hash_value = payload.get("sourceHash")
    source_hash = (
        sha256_text(summary)
        if source_hash_value in {None, ""} and source_type == "manual_reflection"
        else _sha256(source_hash_value, field="source_hash")
    )
    try:
        contribution_score = float(payload.get("contributionScore") or 1.0)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(422, "gc13_contribution_score_invalid", "成长贡献系数无效") from exc
    if not 0.1 <= contribution_score <= 5.0:
        raise RepositoryError(422, "gc13_contribution_score_invalid", "成长贡献系数需在0.1至5之间")
    source_metadata = payload.get("sourceMetadata")
    if source_metadata is None:
        source_metadata = {}
    if not isinstance(source_metadata, Mapping):
        raise RepositoryError(422, "gc13_source_metadata_invalid", "成长来源元数据无效")
    allowed_metadata = {
        "candidateId",
        "reviewId",
        "reviewVersionId",
        "adapterVersion",
        "practiceId",
        "outcomeId",
        "taskId",
        "meetingId",
    }
    if any(str(key) not in allowed_metadata for key in source_metadata):
        raise RepositoryError(422, "gc13_source_metadata_invalid", "成长来源只接受已冻结的引用字段")
    normalized_metadata = {
        str(key): str(value)[:160]
        for key, value in source_metadata.items()
        if str(value or "").strip()
    }
    return {
        "summary": summary,
        "category": category,
        "sourceType": source_type,
        "sourceId": source_id,
        "sourceVersion": source_version,
        "sourceHash": source_hash,
        "contributionScore": contribution_score,
        "sourceMetadata": normalized_metadata,
    }


def _evidence_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    receipt = _json(row["receipt"], {})
    source = receipt.get("source") if isinstance(receipt, Mapping) else {}
    source = source if isinstance(source, Mapping) else {}
    return {
        "evidenceId": str(row["id"]),
        "summary": str(receipt.get("summary") or ""),
        "category": str(row["category"] or ""),
        "validationState": str(row["validation_state"] or "validated"),
        "sourceType": str(row["source_type"] or ""),
        "sourceId": str(row["source_id"] or ""),
        "sourceVersion": int(source.get("version") or 1),
        "contentHash": str(row["content_hash"] or ""),
        "contributionScore": float(row["contribution_score"] or 1.0),
        "version": int(row["version"] or 1),
        "createdAt": str(row["created_at"] or ""),
    }


def _evidence_rows(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT evidence.*, manifest.receipt
        FROM growth_evidence AS evidence
        JOIN object_manifests AS manifest
          ON manifest.scope_id=evidence.scope_id
         AND manifest.id=evidence.content_object_manifest_id
         AND manifest.lifecycle_state='active'
        WHERE evidence.scope_id=?
          AND evidence.subject_principal_id=?
          AND evidence.subject_membership_id=?
          AND evidence.record_kind='evidence'
          AND evidence.validation_state='validated'
          AND evidence.lifecycle_state='active'
        ORDER BY evidence.created_at DESC, evidence.id DESC
        """,
        (identity.scope_id, identity.principal_id, identity.membership_id),
    ).fetchall()


def _weekly_formal_evidence_candidates(
    repository: CloudRepository,
    identity: SessionIdentity,
) -> list[dict[str, Any]]:
    """Read only this member's completed formal work for the current ISO week."""

    today = datetime.now(timezone.utc)
    week_start = (today - timedelta(days=today.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    week_end = week_start + timedelta(days=7)
    start = week_start.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    end = week_end.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    candidates: list[dict[str, Any]] = []
    with repository._connection() as connection:  # noqa: SLF001
        tasks = connection.execute(
            """
            SELECT DISTINCT task.id,task.title,task.version,task.completed_at
            FROM tasks AS task
            JOIN task_collaborators AS collaborator
              ON collaborator.scope_id=task.scope_id AND collaborator.task_id=task.id
            WHERE task.scope_id=? AND collaborator.subject_membership_id=?
              AND collaborator.role_key='owner' AND collaborator.inbox_status='accepted'
              AND collaborator.lifecycle_state='active'
              AND task.lifecycle_state='active' AND task.completed_at>=? AND task.completed_at<?
            ORDER BY task.completed_at,task.id
            """,
            (identity.scope_id, identity.membership_id, start, end),
        ).fetchall()
        meetings = connection.execute(
            """
            SELECT id,title,version,ends_at FROM meetings
            WHERE scope_id=? AND organizer_membership_id=? AND lifecycle_state='active'
              AND status NOT IN ('cancelled','archived') AND ends_at>=? AND ends_at<?
            ORDER BY ends_at,id
            """,
            (identity.scope_id, identity.membership_id, start, end),
        ).fetchall()
        reviews = connection.execute(
            """
            SELECT review.id,version.id AS version_id,version.version,version.review_note,
                   version.content_hash,version.submitted_at
            FROM weekly_reviews AS review
            JOIN weekly_review_versions AS version
              ON version.scope_id=review.scope_id
             AND version.id=review.current_submitted_version_id
            WHERE review.scope_id=? AND review.membership_id=?
              AND review.lifecycle_state='active' AND version.business_state='submitted'
              AND version.submitted_at>=? AND version.submitted_at<?
            ORDER BY version.submitted_at,review.id
            """,
            (identity.scope_id, identity.membership_id, start, end),
        ).fetchall()
    for row in tasks:
        candidates.append(
            {
                "summary": f"本周完成任务「{str(row['title'] or '未命名任务')}」",
                "category": "execution",
                "sourceType": "formal_task",
                "sourceId": str(row["id"]),
                "sourceVersion": int(row["version"] or 1),
                "sourceHash": sha256_text(
                    f"{row['id']}|{row['version']}|{row['title']}|{row['completed_at']}"
                ),
                "sourceMetadata": {"taskId": str(row["id"])},
            }
        )
    for row in meetings:
        candidates.append(
            {
                "summary": f"本周完成客户会议「{str(row['title'] or '未命名会议')}」",
                "category": "collaboration",
                "sourceType": "formal_meeting",
                "sourceId": str(row["id"]),
                "sourceVersion": int(row["version"] or 1),
                "sourceHash": sha256_text(
                    f"{row['id']}|{row['version']}|{row['title']}|{row['ends_at']}"
                ),
                "sourceMetadata": {"meetingId": str(row["id"])},
            }
        )
    for row in reviews:
        candidates.append(
            {
                "summary": str(row["review_note"] or "本周已提交正式周复盘")[:2000],
                "category": "reflection",
                "sourceType": "weekly_review",
                "sourceId": str(row["id"]),
                "sourceVersion": int(row["version"] or 1),
                "sourceHash": str(row["content_hash"] or sha256_text(str(row["version_id"]))),
                "sourceMetadata": {
                    "reviewId": str(row["id"]),
                    "reviewVersionId": str(row["version_id"]),
                },
            }
        )
    return candidates


def _sync_weekly_formal_evidence(
    repository: CloudRepository,
    identity: SessionIdentity,
) -> int:
    created = 0
    for candidate in _weekly_formal_evidence_candidates(repository, identity):
        stable_key = "growth-auto:" + sha256_text(
            f"{identity.membership_id}|{candidate['sourceType']}|"
            f"{candidate['sourceId']}|{candidate['sourceVersion']}"
        )[:48]
        try:
            result = confirm_growth_evidence(
                repository,
                identity,
                payload={**candidate, "contributionScore": 1.0},
                idempotency_key=stable_key,
            )
            if not result.get("idempotentReplay"):
                created += 1
        except RepositoryError as exc:
            if exc.code != "gc13_evidence_already_confirmed":
                raise
    return created


def confirm_growth_evidence(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    normalized = _validate_evidence(payload)
    idempotency_key = _identifier(idempotency_key, field="idempotency_key", maximum=200)
    command_type = "gc13.growth_evidence.confirmed"
    payload_hash = sha256_text(canonical_json(normalized))
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id, command_type, idempotency_key
    )
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        replay = _replay(
            connection,
            identity,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            connection.rollback()
            return replay
        existing = connection.execute(
            """
            SELECT evidence.*, manifest.receipt
            FROM growth_evidence AS evidence
            JOIN object_manifests AS manifest
              ON manifest.scope_id=evidence.scope_id
             AND manifest.id=evidence.content_object_manifest_id
            WHERE evidence.scope_id=? AND evidence.subject_principal_id=?
              AND evidence.subject_membership_id=?
              AND evidence.source_type=? AND evidence.source_id=?
              AND evidence.lifecycle_state='active'
              AND EXISTS (
                  SELECT 1 FROM source_set_members AS member
                  WHERE member.scope_id=evidence.scope_id
                    AND member.source_set_id=evidence.source_set_id
                    AND member.source_object_kind=evidence.source_type
                    AND member.source_object_id=evidence.source_id
                    AND member.source_version=?
                    AND member.lifecycle_state='active'
              )
            LIMIT 1
            """,
            (
                identity.scope_id,
                identity.principal_id,
                identity.membership_id,
                normalized["sourceType"],
                normalized["sourceId"],
                normalized["sourceVersion"],
            ),
        ).fetchone()
        if existing is not None:
            connection.rollback()
            raise RepositoryError(409, "gc13_evidence_already_confirmed", "该成长来源已经确认")

        evidence_id = repository._record_id("growth_evidence", operation_id, "authority")  # noqa: SLF001
        source_set_id = repository._record_id("source_set", operation_id, "growth")  # noqa: SLF001
        content_manifest_id = repository._record_id("manifest", operation_id, "evidence")  # noqa: SLF001
        agent_derived_source = normalized["sourceType"] in {
            "formal_task",
            "formal_meeting",
            "weekly_review",
        }
        evidence_receipt = canonical_json(
            {
                "schema": EVIDENCE_SCHEMA,
                "summary": normalized["summary"],
                "category": normalized["category"],
                "source": {
                    "type": normalized["sourceType"],
                    "id": normalized["sourceId"],
                    "version": normalized["sourceVersion"],
                    "contentHash": normalized["sourceHash"],
                    **normalized["sourceMetadata"],
                },
                **(
                    {
                        "derivedByAgentKind": "growth_companion",
                        "derivedAt": now,
                    }
                    if agent_derived_source
                    else {
                        "confirmedByMembershipId": identity.membership_id,
                        "confirmedAt": now,
                    }
                ),
            }
        )
        content_hash = sha256_text(evidence_receipt)
        connection.execute(
            """
            INSERT INTO secured_resources (
                id, scope_id, resource_kind, lifecycle_state, version,
                resource_type_key, created_at, updated_at, deleted_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, 'growth_evidence', 'active', 1,
                      'member_growth_evidence', ?, ?, NULL, 'cloud', ?)
            """,
            (evidence_id, identity.scope_id, now, now, repository.cloud_instance_id),
        )
        connection.execute(
            """
            INSERT INTO object_manifests (
                id, scope_id, storage_key, content_hash, lifecycle_state, receipt,
                holder_role, holder_instance_id, storage_kind, byte_size,
                media_type, availability_state, receipt_hash, created_at,
                verified_at, deleted_at, authority_role, origin_instance_id
            ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud', ?,
                      'growth_evidence_receipt', ?, 'application/json', 'verified',
                      ?, ?, ?, NULL, 'cloud', ?)
            """,
            (
                content_manifest_id,
                identity.scope_id,
                content_hash,
                evidence_receipt,
                repository.cloud_instance_id,
                len(evidence_receipt.encode("utf-8")),
                content_hash,
                now,
                now,
                repository.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO source_sets (
                id, scope_id, client_id, security_label_set_version,
                source_count, version, purpose_kind, publication_state,
                created_by_principal_id, created_at, expires_at,
                lifecycle_state, updated_at, deleted_at, authority_role,
                origin_instance_id
            ) VALUES (?, ?, NULL, 'gc13-member-private-v1', 1, 1,
                      'growth_evidence_confirmation', 'published', ?, ?, NULL,
                      'active', ?, NULL, 'cloud', ?)
            """,
            (
                source_set_id,
                identity.scope_id,
                identity.principal_id,
                now,
                now,
                repository.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO source_set_members (
                id, scope_id, source_set_id, source_object_id, source_version,
                policy_version, source_object_kind, ordinal, added_at, removed_at,
                version, lifecycle_state, created_at, updated_at, deleted_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, 1, ?, 0, ?, NULL, 1, 'active', ?, ?, NULL,
                      'cloud', ?)
            """,
            (
                repository._record_id("source_member", operation_id, "origin"),  # noqa: SLF001
                identity.scope_id,
                source_set_id,
                normalized["sourceId"],
                normalized["sourceVersion"],
                normalized["sourceType"],
                now,
                now,
                now,
                repository.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO growth_evidence (
                id, scope_id, subject_principal_id, source_set_id,
                validation_state, version, record_kind, subject_membership_id,
                source_type, source_id, content_object_manifest_id, content_hash,
                category, contribution_score, reaction_type, parent_evidence_id,
                lifecycle_state, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, 'validated', 1, 'evidence', ?, ?, ?, ?, ?, ?, ?,
                      NULL, NULL, 'active', ?, ?, NULL)
            """,
            (
                evidence_id,
                identity.scope_id,
                identity.principal_id,
                source_set_id,
                identity.membership_id,
                normalized["sourceType"],
                normalized["sourceId"],
                content_manifest_id,
                content_hash,
                normalized["category"],
                normalized["contributionScore"],
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE chart_read_models SET invalidated_at=? WHERE scope_id=? "
            "AND invalidated_at IS NULL AND lineage_id IN "
            "(SELECT lineage_id FROM growth_read_models WHERE scope_id=? "
            "AND membership_id=? AND invalidated_at IS NULL)",
            (now, identity.scope_id, identity.scope_id, identity.membership_id),
        )
        connection.execute(
            "UPDATE growth_read_models SET invalidated_at=? "
            "WHERE scope_id=? AND membership_id=? AND invalidated_at IS NULL",
            (now, identity.scope_id, identity.membership_id),
        )
        row = connection.execute(
            "SELECT evidence.*, manifest.receipt FROM growth_evidence AS evidence "
            "JOIN object_manifests AS manifest ON manifest.id=evidence.content_object_manifest_id "
            "AND manifest.scope_id=evidence.scope_id WHERE evidence.id=?",
            (evidence_id,),
        ).fetchone()
        result = {
            "schema": "yiyu.gc13.evidence-command.v1",
            "evidence": _evidence_dto(row),
            "readModelState": "updating",
            "skillCreated": False,
            "projectMemoryConsumed": False,
            "idempotentReplay": False,
        }
        result_manifest_id, result_hash = _result_manifest(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            kind="growth_evidence_confirmed",
            result=result,
            now=now,
        )
        _trace_command(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            result_manifest_id=result_manifest_id,
            result_hash=result_hash,
            command_type=command_type,
            aggregate_type="growth_evidence",
            aggregate_id=evidence_id,
            aggregate_version=1,
            target_resource_id=evidence_id,
            command_status="settled",
            event_type="gc13.growth_evidence.confirmed",
            now=now,
        )
        _pending_reconciliation(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            reason="evidence_changed",
            now=now,
        )
        connection.commit()
        return result


def update_growth_evidence(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    evidence_id: str,
    action: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if action not in {"revise", "exclude"}:
        raise RepositoryError(422, "gc13_evidence_action_invalid", "成长证据操作无效")
    expected = int(payload.get("expectedVersion") or 0)
    if expected < 1:
        raise RepositoryError(422, "gc13_evidence_expected_version_required", "请刷新成长证据后重试")
    summary = str(payload.get("summary") or "").strip()
    category = str(payload.get("category") or "").strip()
    if action == "revise":
        if not 1 <= len(summary) <= 2_000 or category not in ALLOWED_CATEGORIES:
            raise RepositoryError(422, "gc13_evidence_revision_invalid", "请填写有效的纠正内容和分类")
    normalized = {
        "evidenceId": evidence_id,
        "action": action,
        "expectedVersion": expected,
        "summary": summary,
        "category": category,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    command_type = f"gc13.growth_evidence.{action}d"
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id, command_type, idempotency_key
    )
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        replay = _replay(
            connection,
            identity,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            connection.rollback()
            return replay
        row = connection.execute(
            "SELECT evidence.*,manifest.receipt FROM growth_evidence AS evidence "
            "JOIN object_manifests AS manifest ON manifest.scope_id=evidence.scope_id "
            "AND manifest.id=evidence.content_object_manifest_id "
            "WHERE evidence.scope_id=? AND evidence.id=? AND evidence.subject_principal_id=? "
            "AND evidence.subject_membership_id=? AND evidence.lifecycle_state='active'",
            (identity.scope_id, evidence_id, identity.principal_id, identity.membership_id),
        ).fetchone()
        if row is None:
            connection.rollback()
            raise RepositoryError(404, "gc13_evidence_missing", "成长证据不存在")
        if int(row["version"] or 1) != expected:
            connection.rollback()
            raise RepositoryError(409, "gc13_evidence_version_conflict", "成长证据已更新，请刷新后重试")
        next_version = expected + 1
        if action == "revise":
            prior = _json(row["receipt"], {})
            receipt = canonical_json(
                {
                    **dict(prior),
                    "summary": summary,
                    "category": category,
                    "revisedByMembershipId": identity.membership_id,
                    "revisedAt": now,
                    "previousContentHash": str(row["content_hash"] or ""),
                }
            )
            content_hash = sha256_text(receipt)
            manifest_id = repository._record_id("manifest", operation_id, "evidence-revision")  # noqa: SLF001
            connection.execute(
                "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,lifecycle_state,"
                "receipt,holder_role,holder_instance_id,storage_kind,byte_size,media_type,"
                "availability_state,receipt_hash,created_at,verified_at,deleted_at,authority_role,"
                "origin_instance_id) VALUES (?,?,NULL,?,'active',?,'cloud',?,"
                "'growth_evidence_receipt',?,'application/json','verified',?,?,?,NULL,'cloud',?)",
                (
                    manifest_id,
                    identity.scope_id,
                    content_hash,
                    receipt,
                    repository.cloud_instance_id,
                    len(receipt.encode("utf-8")),
                    content_hash,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                "UPDATE growth_evidence SET content_object_manifest_id=?,content_hash=?,category=?,"
                "version=?,updated_at=? WHERE scope_id=? AND id=?",
                (manifest_id, content_hash, category, next_version, now, identity.scope_id, evidence_id),
            )
            connection.execute(
                "UPDATE secured_resources SET version=?,updated_at=? WHERE scope_id=? AND id=?",
                (next_version, now, identity.scope_id, evidence_id),
            )
        else:
            connection.execute(
                "UPDATE growth_evidence SET lifecycle_state='deleted',deleted_at=?,updated_at=?,"
                "version=? WHERE scope_id=? AND id=?",
                (now, now, next_version, identity.scope_id, evidence_id),
            )
            connection.execute(
                "UPDATE secured_resources SET lifecycle_state='deleted',deleted_at=?,updated_at=?,"
                "version=? WHERE scope_id=? AND id=?",
                (now, now, next_version, identity.scope_id, evidence_id),
            )
        connection.execute(
            "UPDATE growth_read_models SET invalidated_at=? WHERE scope_id=? AND membership_id=? "
            "AND invalidated_at IS NULL",
            (now, identity.scope_id, identity.membership_id),
        )
        result = {
            "schema": "yiyu.gc13.evidence-command.v1",
            "evidenceId": evidence_id,
            "state": "excluded" if action == "exclude" else "revised",
            "version": next_version,
            "readModelState": "updating",
            "idempotentReplay": False,
        }
        result_manifest_id, result_hash = _result_manifest(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            kind=f"growth_evidence_{action}d",
            result=result,
            now=now,
        )
        _trace_command(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            result_manifest_id=result_manifest_id,
            result_hash=result_hash,
            command_type=command_type,
            aggregate_type="growth_evidence",
            aggregate_id=evidence_id,
            aggregate_version=next_version,
            target_resource_id=evidence_id,
            command_status="settled",
            event_type=command_type,
            now=now,
        )
        if action == "exclude":
            lifecycle_id = repository._record_id("lifecycle", operation_id, evidence_id)  # noqa: SLF001
            connection.execute(
                "INSERT INTO lifecycle_events (id,scope_id,operation_id,secured_resource_id,"
                "from_state,to_state,tombstone_version,actor_id,reason_code,occurred_at,"
                "origin_instance_id,created_at,integrity_hash) VALUES "
                "(?,?,?,?,'active','deleted',?,?,'member_excluded_growth_evidence',?,?,?,?)",
                (
                    lifecycle_id,
                    identity.scope_id,
                    operation_id,
                    evidence_id,
                    next_version,
                    identity.principal_id,
                    now,
                    repository.cloud_instance_id,
                    now,
                    sha256_text(f"{lifecycle_id}|{evidence_id}|{next_version}|{now}"),
                ),
            )
        connection.commit()
        return result


def _validate_rule(payload: Mapping[str, Any]) -> dict[str, Any]:
    metric_key = str(payload.get("metricKey") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", metric_key):
        raise RepositoryError(422, "gc13_metric_key_invalid", "成长指标键无效")
    label = str(payload.get("label") or "").strip()
    if not 1 <= len(label) <= 80:
        raise RepositoryError(422, "gc13_metric_label_invalid", "成长指标名称无效")
    ability_key = str(payload.get("abilityKey") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", ability_key):
        raise RepositoryError(422, "gc13_ability_key_invalid", "成长能力键无效")
    ability_label = str(payload.get("abilityLabel") or label).strip()
    if not 1 <= len(ability_label) <= 80:
        raise RepositoryError(422, "gc13_ability_label_invalid", "成长能力名称无效")
    categories = sorted({str(item).strip() for item in payload.get("evidenceCategories") or []})
    if not categories or any(item not in ALLOWED_CATEGORIES for item in categories):
        raise RepositoryError(422, "gc13_rule_categories_invalid", "成长规则证据分类无效")
    try:
        points_per_evidence = float(payload.get("pointsPerEvidence") or 10.0)
        max_score = float(payload.get("maxScore") or 100.0)
        expected_rule_version = int(payload.get("expectedRuleVersion") or 0)
    except (TypeError, ValueError) as exc:
        raise RepositoryError(422, "gc13_rule_number_invalid", "成长规则数值无效") from exc
    if not 0.1 <= points_per_evidence <= 100 or not 1 <= max_score <= 10_000:
        raise RepositoryError(422, "gc13_rule_number_invalid", "成长规则数值超出范围")
    thresholds: list[dict[str, Any]] = []
    seen_badges: set[str] = set()
    for raw in payload.get("badgeThresholds") or []:
        if not isinstance(raw, Mapping):
            raise RepositoryError(422, "gc13_badge_threshold_invalid", "成长徽章阈值无效")
        badge_key = str(raw.get("badgeKey") or "").strip()
        badge_label = str(raw.get("label") or "").strip()
        try:
            minimum = float(raw.get("minimum"))
        except (TypeError, ValueError) as exc:
            raise RepositoryError(422, "gc13_badge_threshold_invalid", "成长徽章阈值无效") from exc
        if (
            not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", badge_key)
            or badge_key in seen_badges
            or not 1 <= len(badge_label) <= 80
            or not 0 <= minimum <= max_score
        ):
            raise RepositoryError(422, "gc13_badge_threshold_invalid", "成长徽章阈值无效")
        seen_badges.add(badge_key)
        thresholds.append({"badgeKey": badge_key, "label": badge_label, "minimum": minimum})
    thresholds.sort(key=lambda item: (item["minimum"], item["badgeKey"]))
    return {
        "schema": RULE_SCHEMA,
        "metricKey": metric_key,
        "label": label,
        "abilityKey": ability_key,
        "abilityLabel": ability_label,
        "evidenceCategories": categories,
        "pointsPerEvidence": points_per_evidence,
        "maxScore": max_score,
        "badgeThresholds": thresholds,
        "expectedRuleVersion": expected_rule_version,
    }


def _rule_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    rule = _json(row["rule_spec"], {})
    return {
        "ruleVersionId": str(row["id"]),
        "metricKey": str(row["metric_key"] or ""),
        "ruleVersion": int(row["rule_version"] or 1),
        "effectiveAt": str(row["effective_at"] or ""),
        "lifecycleState": str(row["lifecycle_state"] or "active"),
        "spec": rule,
    }


def _active_rule_rows(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT * FROM growth_rule_versions
        WHERE scope_id=? AND lifecycle_state='active' AND retired_at IS NULL
        ORDER BY metric_key, rule_version DESC, id DESC
        """,
        (identity.scope_id,),
    ).fetchall()


def _ensure_builtin_growth_rules(
    repository: CloudRepository,
    identity: SessionIdentity,
) -> int:
    """Install fixed product rules when an organization has never published any.

    These are part of the built-in Growth Companion contract, not member-created
    settings.  An administrator can later publish version 2 through the formal
    rule command; runtime never creates a parallel rules authority.
    """

    now = utc_now()
    created = 0
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        any_existing = connection.execute(
            "SELECT 1 FROM growth_rule_versions WHERE scope_id=? LIMIT 1",
            (identity.scope_id,),
        ).fetchone()
        if any_existing is not None:
            connection.rollback()
            return 0
        for base in BUILTIN_GROWTH_RULES:
            rule_spec = {
                "schema": RULE_SCHEMA,
                **base,
                "pointsPerEvidence": 10.0,
                "maxScore": 100.0,
                "badgeThresholds": [],
            }
            rule_id = "growth_rule_" + sha256_text(
                f"{identity.scope_id}|{base['metricKey']}|1"
            )[:28]
            connection.execute(
                """
                INSERT INTO growth_rule_versions (
                    id, scope_id, metric_key, rule_version, effective_at,
                    rule_spec_schema_version, rule_spec, retired_at, version,
                    lifecycle_state, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, 1, ?, ?, ?, NULL, 1, 'active', ?, ?, NULL)
                """,
                (
                    rule_id,
                    identity.scope_id,
                    base["metricKey"],
                    now,
                    RULE_SCHEMA,
                    canonical_json(rule_spec),
                    now,
                    now,
                ),
            )
            created += 1
        connection.commit()
    return created


def publish_growth_rule(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if not identity.is_admin:
        raise RepositoryError(403, "gc13_growth_rule_admin_required", "仅组织管理员可发布成长规则")
    normalized = _validate_rule(payload)
    idempotency_key = _identifier(idempotency_key, field="idempotency_key", maximum=200)
    command_type = "gc13.growth_rule.published"
    payload_hash = sha256_text(canonical_json(normalized))
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id, command_type, idempotency_key
    )
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        replay = _replay(
            connection,
            identity,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            connection.rollback()
            return replay
        current = connection.execute(
            "SELECT * FROM growth_rule_versions WHERE scope_id=? AND metric_key=? "
            "ORDER BY rule_version DESC, created_at DESC, id DESC LIMIT 1",
            (identity.scope_id, normalized["metricKey"]),
        ).fetchone()
        current_version = int(current["rule_version"] or 0) if current else 0
        if current_version != normalized["expectedRuleVersion"]:
            connection.rollback()
            raise RepositoryError(409, "gc13_growth_rule_version_conflict", "成长规则版本已变化，请刷新后重试")
        next_version = current_version + 1
        if current is not None and str(current["lifecycle_state"] or "") == "active":
            connection.execute(
                "UPDATE growth_rule_versions SET lifecycle_state='archived', "
                "retired_at=?, updated_at=? WHERE id=? AND scope_id=?",
                (now, now, str(current["id"]), identity.scope_id),
            )
        rule_id = "growth_rule_" + sha256_text(
            f"{identity.scope_id}|{normalized['metricKey']}|{next_version}"
        )[:28]
        rule_spec = canonical_json(
            {key: value for key, value in normalized.items() if key != "expectedRuleVersion"}
        )
        connection.execute(
            """
            INSERT INTO growth_rule_versions (
                id, scope_id, metric_key, rule_version, effective_at,
                rule_spec_schema_version, rule_spec, retired_at, version,
                lifecycle_state, created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'active', ?, ?, NULL)
            """,
            (
                rule_id,
                identity.scope_id,
                normalized["metricKey"],
                next_version,
                now,
                RULE_SCHEMA,
                rule_spec,
                next_version,
                now,
                now,
            ),
        )
        connection.execute(
            "UPDATE growth_read_models SET invalidated_at=? "
            "WHERE scope_id=? AND invalidated_at IS NULL",
            (now, identity.scope_id),
        )
        row = connection.execute(
            "SELECT * FROM growth_rule_versions WHERE id=?",
            (rule_id,),
        ).fetchone()
        result = {
            "schema": "yiyu.gc13.rule-command.v1",
            "rule": _rule_dto(row),
            "readModelState": "updating",
            "idempotentReplay": False,
        }
        result_manifest_id, result_hash = _result_manifest(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            kind="growth_rule_published",
            result=result,
            now=now,
        )
        _trace_command(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            result_manifest_id=result_manifest_id,
            result_hash=result_hash,
            command_type=command_type,
            aggregate_type="growth_rule",
            aggregate_id=rule_id,
            aggregate_version=next_version,
            target_resource_id=None,
            command_status="settled",
            event_type="gc13.growth_rule.published",
            now=now,
        )
        _pending_reconciliation(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            reason="rule_changed",
            now=now,
        )
        connection.commit()
        return result


def build_growth_projections(
    evidence_rows: Sequence[Mapping[str, Any]],
    rule_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    metric_summaries: list[dict[str, Any]] = []
    for row in rule_rows:
        spec = _json(row["rule_spec"], {})
        if spec.get("schema") != RULE_SCHEMA:
            raise RepositoryError(409, "gc13_growth_rule_schema_mismatch", "成长规则结构版本不受支持")
        categories = set(spec.get("evidenceCategories") or [])
        matching = [item for item in evidence_rows if str(item["category"] or "") in categories]
        weighted_count = sum(float(item["contribution_score"] or 1.0) for item in matching)
        maximum = float(spec.get("maxScore") or 100.0)
        score = min(maximum, round(weighted_count * float(spec.get("pointsPerEvidence") or 10.0), 2))
        metric = {
            "schema": READ_MODEL_SCHEMA,
            "modelKind": "metric",
            "metricKey": str(row["metric_key"]),
            "label": str(spec.get("label") or row["metric_key"]),
            "score": score,
            "maxScore": maximum,
            "evidenceCount": len(matching),
            "ruleVersion": int(row["rule_version"] or 1),
            "ruleVersionId": str(row["id"]),
        }
        projections.append(metric)
        metric_summaries.append(metric)
        projections.append(
            {
                "schema": READ_MODEL_SCHEMA,
                "modelKind": "ability",
                "abilityKey": str(spec.get("abilityKey") or row["metric_key"]),
                "label": str(spec.get("abilityLabel") or spec.get("label") or row["metric_key"]),
                "score": score,
                "maxScore": maximum,
                "evidenceCount": len(matching),
                "metricKey": str(row["metric_key"]),
                "ruleVersion": int(row["rule_version"] or 1),
                "ruleVersionId": str(row["id"]),
            }
        )
        for badge in spec.get("badgeThresholds") or []:
            minimum = float(badge.get("minimum") or 0)
            projections.append(
                {
                    "schema": READ_MODEL_SCHEMA,
                    "modelKind": "badge",
                    "badgeKey": str(badge.get("badgeKey") or ""),
                    "label": str(badge.get("label") or ""),
                    "state": "earned" if score >= minimum else "locked",
                    "minimum": minimum,
                    "score": score,
                    "progressPercent": 100 if minimum <= 0 else min(100, round(score / minimum * 100)),
                    "metricKey": str(row["metric_key"]),
                    "ruleVersion": int(row["rule_version"] or 1),
                    "ruleVersionId": str(row["id"]),
                }
            )
    projections.append(
        {
            "schema": READ_MODEL_SCHEMA,
            "modelKind": "overview",
            "evidenceCount": len(evidence_rows),
            "metricCount": len(metric_summaries),
            "totalScore": round(sum(float(item["score"]) for item in metric_summaries), 2),
            "ruleVersions": [
                {"metricKey": item["metricKey"], "ruleVersion": item["ruleVersion"]}
                for item in metric_summaries
            ],
        }
    )
    return projections


def _growth_input_fingerprint(
    evidence_rows: Sequence[Mapping[str, Any]],
    rule_rows: Sequence[Mapping[str, Any]],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "evidence": [
                    {
                        "id": str(row["id"]),
                        "version": int(row["version"] or 1),
                        "contentHash": str(row["content_hash"] or ""),
                    }
                    for row in evidence_rows
                ],
                "rules": [
                    {
                        "id": str(row["id"]),
                        "version": int(row["rule_version"] or 1),
                    }
                    for row in rule_rows
                ],
                "generatorVersion": GENERATOR_VERSION,
            }
        )
    )


def _active_models_for_input(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    input_fingerprint: str,
) -> list[dict[str, Any]]:
    models = _read_models(connection, identity)
    overview = next(
        (item for item in models if item.get("modelKind") == "overview"),
        None,
    )
    if not isinstance(overview, Mapping):
        return []
    return models if overview.get("inputFingerprint") == input_fingerprint else []


def _failed_rebuild(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    operation_id: str,
    idempotency_key: str,
    payload_hash: str,
    error_code: str,
) -> dict[str, Any]:
    now = utc_now()
    result = {
        "schema": "yiyu.gc13.rebuild-command.v1",
        "state": "failed_retryable",
        "retryable": True,
        "errorCode": error_code,
        "message": "成长指标重算暂时失败；成长证据已保留，可以重试",
        "evidencePreserved": True,
        "idempotentReplay": False,
    }
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        result_manifest_id, result_hash = _result_manifest(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            kind="growth_rebuild_failed",
            result=result,
            now=now,
        )
        _trace_command(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            result_manifest_id=result_manifest_id,
            result_hash=result_hash,
            command_type="gc13.growth_read_model.rebuilt",
            aggregate_type="growth_read_model",
            aggregate_id=identity.membership_id,
            aggregate_version=1,
            target_resource_id=None,
            command_status="failed",
            event_type="gc13.growth_read_model.rebuild_failed",
            now=now,
        )
        connection.execute(
            """
            INSERT INTO reconciliation_runs (
                id, scope_id, operation_id, registry_state_id, mismatch_count,
                status, reconciliation_kind, target_instance_id,
                result_object_manifest_id, started_at, completed_at, version,
                lifecycle_state, created_at, updated_at, deleted_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, NULL, 1, 'failed', ?, ?, ?, ?, ?, 1,
                      'active', ?, ?, NULL, 'cloud', ?)
            """,
            (
                repository._record_id("reconcile", operation_id, "failed"),  # noqa: SLF001
                identity.scope_id,
                operation_id,
                RECONCILIATION_KIND,
                repository.cloud_instance_id,
                result_manifest_id,
                now,
                now,
                now,
                now,
                repository.cloud_instance_id,
            ),
        )
        connection.commit()
    return result


def rebuild_growth_read_models(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    idempotency_key: str,
    evaluator: ProjectionEvaluator = build_growth_projections,
) -> dict[str, Any]:
    builtin_rules_created = _ensure_builtin_growth_rules(repository, identity)
    auto_evidence_created = _sync_weekly_formal_evidence(repository, identity)
    idempotency_key = _identifier(idempotency_key, field="idempotency_key", maximum=200)
    command_type = "gc13.growth_read_model.rebuilt"
    payload_hash = sha256_text(
        canonical_json(
            {
                "schema": "yiyu.gc13.rebuild-request.v1",
                "membershipId": identity.membership_id,
                "generatorVersion": GENERATOR_VERSION,
            }
        )
    )
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id, command_type, idempotency_key
    )
    with repository._connection() as connection:  # noqa: SLF001
        replay = _replay(
            connection,
            identity,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            return replay
        evidence_rows = _evidence_rows(connection, identity)
        rule_rows = _active_rule_rows(connection, identity)
    if not rule_rows:
        raise RepositoryError(409, "gc13_growth_rules_not_connected", "成长规则尚未发布")
    input_fingerprint = _growth_input_fingerprint(evidence_rows, rule_rows)
    with repository._connection() as connection:  # noqa: SLF001
        current_models = _active_models_for_input(
            connection,
            identity,
            input_fingerprint=input_fingerprint,
        )
    if current_models:
        return {
            "schema": "yiyu.gc13.rebuild-command.v1",
            "state": "ready",
            "retryable": False,
            "evidenceCount": len(evidence_rows),
            "modelCount": len(current_models),
            "models": current_models,
            "evidencePreserved": True,
            "idempotentReplay": True,
            "unchanged": True,
            "autoEvidenceCreated": auto_evidence_created,
            "builtinRulesCreated": builtin_rules_created,
            "sourceFingerprint": input_fingerprint,
        }
    try:
        projections = evaluator(evidence_rows, rule_rows)
    except RepositoryError:
        raise
    except Exception as exc:  # a failed generator must become a durable retry state
        return _failed_rebuild(
            repository,
            identity,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            error_code=type(exc).__name__,
        )

    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        replay = _replay(
            connection,
            identity,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            connection.rollback()
            return replay
        current_evidence = _evidence_rows(connection, identity)
        current_rules = _active_rule_rows(connection, identity)
        if (
            [(row["id"], row["version"], row["content_hash"]) for row in current_evidence]
            != [(row["id"], row["version"], row["content_hash"]) for row in evidence_rows]
            or [(row["id"], row["rule_version"]) for row in current_rules]
            != [(row["id"], row["rule_version"]) for row in rule_rows]
        ):
            connection.rollback()
            raise RepositoryError(409, "gc13_rebuild_input_changed", "成长重算期间输入已变化，请重试")

        source_set_id = repository._record_id("source_set", operation_id, "rebuild")  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO source_sets (
                id, scope_id, client_id, security_label_set_version,
                source_count, version, purpose_kind, publication_state,
                created_by_principal_id, created_at, expires_at, lifecycle_state,
                updated_at, deleted_at, authority_role, origin_instance_id
            ) VALUES (?, ?, NULL, 'gc13-member-private-v1', ?, 1,
                      'growth_read_model_rebuild', 'published', ?, ?, NULL,
                      'active', ?, NULL, 'cloud', ?)
            """,
            (
                source_set_id,
                identity.scope_id,
                len(current_evidence),
                identity.principal_id,
                now,
                now,
                repository.cloud_instance_id,
            ),
        )
        for ordinal, evidence in enumerate(current_evidence):
            connection.execute(
                """
                INSERT INTO source_set_members (
                    id, scope_id, source_set_id, source_object_id, source_version,
                    policy_version, source_object_kind, ordinal, added_at,
                    removed_at, version, lifecycle_state, created_at, updated_at,
                    deleted_at, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, 1, 'growth_evidence', ?, ?, NULL, 1,
                          'active', ?, ?, NULL, 'cloud', ?)
                """,
                (
                    repository._record_id("source_member", operation_id, str(evidence["id"])),  # noqa: SLF001
                    identity.scope_id,
                    source_set_id,
                    str(evidence["id"]),
                    int(evidence["version"] or 1),
                    ordinal,
                    now,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
        connection.execute(
            "UPDATE growth_read_models SET invalidated_at=? "
            "WHERE scope_id=? AND membership_id=? AND invalidated_at IS NULL",
            (now, identity.scope_id, identity.membership_id),
        )
        created_models: list[dict[str, Any]] = []
        rule_by_id = {str(row["id"]): row for row in current_rules}
        for index, projection in enumerate(projections):
            model_kind = str(projection.get("modelKind") or "")
            if model_kind not in {"metric", "badge", "ability", "overview"}:
                connection.rollback()
                raise RepositoryError(409, "gc13_projection_kind_invalid", "成长读模型类型无效")
            stable_key = str(
                projection.get("metricKey")
                or projection.get("badgeKey")
                or projection.get("abilityKey")
                or "overview"
            )
            rule_version_id = str(projection.get("ruleVersionId") or "") or None
            if rule_version_id is not None and rule_version_id not in rule_by_id:
                connection.rollback()
                raise RepositoryError(409, "gc13_projection_rule_mismatch", "成长读模型规则引用无效")
            model_id = "growth_model_" + sha256_text(
                f"{operation_id}|{model_kind}|{stable_key}|{index}"
            )[:26]
            lineage_id = repository._record_id("lineage", operation_id, model_id)  # noqa: SLF001
            manifest_id = repository._record_id("manifest", operation_id, model_id)  # noqa: SLF001
            normalized_projection = {
                **dict(projection),
                "schema": READ_MODEL_SCHEMA,
                "memberId": identity.membership_id,
                "inputFingerprint": input_fingerprint,
                "generatedAt": now,
                "generatorVersion": GENERATOR_VERSION,
            }
            receipt = canonical_json(normalized_projection)
            receipt_hash = sha256_text(receipt)
            connection.execute(
                """
                INSERT INTO derivation_lineage (
                    id, scope_id, source_set_id, policy_version_id,
                    grant_generation, derivative_kind, derivative_object_id,
                    generator_version, generated_at, invalidated_at,
                    source_version, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, NULL, NULL, 'growth_read_model', ?, ?, ?, NULL,
                          1, 'cloud', ?)
                """,
                (
                    lineage_id,
                    identity.scope_id,
                    source_set_id,
                    model_id,
                    GENERATOR_VERSION,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO object_manifests (
                    id, scope_id, storage_key, content_hash, lifecycle_state,
                    receipt, holder_role, holder_instance_id, storage_kind,
                    byte_size, media_type, availability_state, receipt_hash,
                    created_at, verified_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, NULL, ?, 'active', ?, 'cloud', ?,
                          'growth_read_model', ?, 'application/json', 'verified',
                          ?, ?, ?, NULL, 'cloud', ?)
                """,
                (
                    manifest_id,
                    identity.scope_id,
                    receipt_hash,
                    receipt,
                    repository.cloud_instance_id,
                    len(receipt.encode("utf-8")),
                    receipt_hash,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO growth_read_models (
                    id, scope_id, lineage_id, rule_version_id,
                    metric_badge_ability, refreshed_at, membership_id,
                    model_kind, metric_key, badge_key, ability_key,
                    model_object_manifest_id, generator_version, invalidated_at,
                    source_version, generated_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?,
                          'cloud', ?)
                """,
                (
                    model_id,
                    identity.scope_id,
                    lineage_id,
                    rule_version_id,
                    receipt,
                    now,
                    identity.membership_id,
                    model_kind,
                    projection.get("metricKey"),
                    projection.get("badgeKey"),
                    projection.get("abilityKey"),
                    manifest_id,
                    GENERATOR_VERSION,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO chart_read_models "
                "(id,scope_id,lineage_id,chart_kind,source_version,refreshed_at,"
                "model_object_manifest_id,generator_version,invalidated_at,generated_at,"
                "authority_role,origin_instance_id) VALUES (?,?,?,?,1,?,?,?,?,?,'cloud',?)",
                (
                    "chart_model_" + sha256_text(model_id)[:26],
                    identity.scope_id,
                    lineage_id,
                    f"growth_{model_kind}",
                    now,
                    manifest_id,
                    GENERATOR_VERSION,
                    None,
                    now,
                    repository.cloud_instance_id,
                ),
            )
            created_models.append(normalized_projection)
        result = {
            "schema": "yiyu.gc13.rebuild-command.v1",
            "state": "ready",
            "retryable": False,
            "evidenceCount": len(current_evidence),
            "modelCount": len(created_models),
            "models": created_models,
            "evidencePreserved": True,
            "idempotentReplay": False,
            "autoEvidenceCreated": auto_evidence_created,
            "builtinRulesCreated": builtin_rules_created,
            "sourceFingerprint": input_fingerprint,
        }
        result_manifest_id, result_hash = _result_manifest(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            kind="growth_rebuild_completed",
            result=result,
            now=now,
        )
        _trace_command(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            result_manifest_id=result_manifest_id,
            result_hash=result_hash,
            command_type=command_type,
            aggregate_type="growth_read_model",
            aggregate_id=identity.membership_id,
            aggregate_version=max((int(row["rule_version"] or 1) for row in current_rules), default=1),
            target_resource_id=None,
            command_status="settled",
            event_type="gc13.growth_read_model.rebuilt",
            now=now,
        )
        connection.execute(
            """
            INSERT INTO reconciliation_runs (
                id, scope_id, operation_id, registry_state_id, mismatch_count,
                status, reconciliation_kind, target_instance_id,
                result_object_manifest_id, started_at, completed_at, version,
                lifecycle_state, created_at, updated_at, deleted_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, NULL, 0, 'completed', ?, ?, ?, ?, ?, 1,
                      'active', ?, ?, NULL, 'cloud', ?)
            """,
            (
                repository._record_id("reconcile", operation_id, "completed"),  # noqa: SLF001
                identity.scope_id,
                operation_id,
                RECONCILIATION_KIND,
                repository.cloud_instance_id,
                result_manifest_id,
                now,
                now,
                now,
                now,
                repository.cloud_instance_id,
            ),
        )
        bot_id = builtin_agent_id(identity.organization_id, "growth_companion")
        run_id = repository._record_id("run", operation_id, "growth-companion")  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO execution_runs (
                id, scope_id, bot_id, rule_id, task_id, operation_id, status,
                initiator_membership_id, proposal_id, run_kind,
                progress_object_manifest_id, result_object_manifest_id,
                started_at, finished_at, version, lifecycle_state,
                created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, NULL, NULL, ?, 'completed', ?, NULL,
                      'growth_read_model_rebuild', NULL, ?, ?, ?, 1, 'active', ?, ?, NULL)
            """,
            (
                run_id,
                identity.scope_id,
                bot_id,
                operation_id,
                identity.membership_id,
                result_manifest_id,
                now,
                now,
                now,
                now,
            ),
        )
        result["agentRun"] = AgentRunReceipt(
            agent_kind="growth_companion",
            run_id=run_id,
            state="completed",
            stage="read_models_ready",
            message="成长指标已按本人的正式证据重算",
            result_version=max(
                (int(row["rule_version"] or 1) for row in current_rules),
                default=1,
            ),
        ).as_dict()
        connection.commit()
        return result


def record_growth_companion_summary(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    source_fingerprint = _sha256(
        payload.get("sourceFingerprint"),
        field="source_fingerprint",
    )
    weekly_summary = str(payload.get("weeklySummary") or "").strip()
    if not 1 <= len(weekly_summary) <= 500:
        raise RepositoryError(422, "gc13_weekly_summary_invalid", "成长周总结需为1至500个字符")

    def short_list(key: str, maximum_items: int = 4) -> list[str]:
        raw = payload.get(key) or []
        if not isinstance(raw, list):
            raise RepositoryError(422, f"gc13_{key}_invalid", f"{key} 无效")
        values = [str(item or "").strip()[:500] for item in raw]
        return [item for item in values if item][:maximum_items]

    patterns = short_list("patterns")
    blind_spots = short_list("blindSpots")
    suggestions = short_list("suggestions")
    ability_keys = {"exec", "collab", "analyze", "insight", "risk", "write"}
    trend_values = {"up", "steady", "forming"}
    raw_highlights = payload.get("growthHighlights") or []
    if not isinstance(raw_highlights, list):
        raise RepositoryError(422, "gc13_growth_highlights_invalid", "growthHighlights 无效")
    growth_highlights: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_highlights[:3]):
        if not isinstance(raw, Mapping):
            continue
        ability_key = str(raw.get("abilityKey") or "").strip()
        if ability_key not in ability_keys:
            continue
        try:
            level = max(1, min(5, int(raw.get("level") or 1)))
        except (TypeError, ValueError):
            level = 1
        trend = str(raw.get("trend") or "forming").strip()
        if trend not in trend_values:
            trend = "forming"
        title = str(raw.get("title") or raw.get("abilityLabel") or "").strip()[:80]
        summary = str(raw.get("summary") or "").strip()[:240]
        if not title or not summary:
            continue
        signal_id = "growth_signal_" + sha256_text(
            f"{identity.scope_id}|{identity.membership_id}|{source_fingerprint}|{ability_key}|{index}"
        )[:28]
        growth_highlights.append(
            {
                "id": signal_id,
                "abilityKey": ability_key,
                "abilityLabel": str(raw.get("abilityLabel") or title).strip()[:80],
                "title": title,
                "summary": summary,
                "trend": trend,
                "level": level,
            }
        )

    raw_experiences = payload.get("experienceEntries") or []
    if not isinstance(raw_experiences, list):
        raise RepositoryError(422, "gc13_experience_entries_invalid", "experienceEntries 无效")
    experience_entries: list[dict[str, Any]] = []
    for raw in raw_experiences[:3]:
        if not isinstance(raw, Mapping):
            continue
        text = str(raw.get("text") or "").strip()[:240]
        if not text:
            continue
        kind = str(raw.get("kind") or "distilled").strip()
        if kind not in {"quote", "distilled"}:
            kind = "distilled"
        source_id = str(raw.get("sourceId") or "").strip()[:160]
        entry_id = "experience_" + sha256_text(
            f"{identity.scope_id}|{identity.membership_id}|{kind}|{source_id}|{text}"
        )[:28]
        experience_entries.append(
            {
                "id": entry_id,
                "kind": kind,
                "text": text,
                "category": str(raw.get("category") or "成长经验").strip()[:80],
                "sourceType": str(raw.get("sourceType") or "weekly_review").strip()[:80],
                "sourceId": source_id,
                "sourceTitle": str(raw.get("sourceTitle") or "成长复盘").strip()[:160],
            }
        )
    model_name = str(payload.get("modelName") or "").strip()[:160]
    provider_resource_id = str(payload.get("providerResourceId") or "").strip()[:160] or None
    idempotency_key = _identifier(idempotency_key, field="idempotency_key", maximum=200)
    normalized = {
        "sourceFingerprint": source_fingerprint,
        "weeklySummary": weekly_summary,
        "patterns": patterns,
        "blindSpots": blind_spots,
        "suggestions": suggestions,
        "growthHighlights": growth_highlights,
        "experienceEntries": experience_entries,
        "modelName": model_name or None,
        "providerResourceId": provider_resource_id,
    }
    payload_hash = sha256_text(canonical_json(normalized))
    command_type = "gc13.growth_companion.summarized"
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id,
        command_type,
        idempotency_key,
    )
    now = utc_now()
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        replay = _replay(
            connection,
            identity,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            connection.rollback()
            return replay
        evidence_rows = _evidence_rows(connection, identity)
        rule_rows = _active_rule_rows(connection, identity)
        analysis_context = _growth_analysis_context(connection, identity)
        current_fingerprint = _growth_companion_source_fingerprint(
            evidence_rows,
            rule_rows,
            analysis_context,
        )
        if current_fingerprint != source_fingerprint:
            connection.rollback()
            raise RepositoryError(409, "gc13_growth_summary_source_changed", "成长证据已变化，请重新生成总结")
        if not evidence_rows:
            connection.rollback()
            raise RepositoryError(422, "gc13_growth_summary_evidence_required", "尚无正式成长证据")

        for entry in experience_entries:
            connection.execute(
                """
                INSERT OR IGNORE INTO secured_resources (
                    id, scope_id, resource_kind, lifecycle_state, version,
                    resource_type_key, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, 'growth_experience_quote', 'active', 1,
                          'organization_growth_experience', ?, ?, NULL, 'cloud', ?)
                """,
                (
                    entry["id"],
                    identity.scope_id,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )

        source_set_id = repository._record_id("source_set", operation_id, "companion-summary")  # noqa: SLF001
        connection.execute(
            """
            INSERT INTO source_sets (
                id, scope_id, client_id, security_label_set_version,
                source_count, version, purpose_kind, publication_state,
                created_by_principal_id, created_at, expires_at, lifecycle_state,
                updated_at, deleted_at, authority_role, origin_instance_id
            ) VALUES (?, ?, NULL, 'gc13-member-private-v1', ?, 1,
                      'growth_companion_summary', 'published', ?, ?, NULL,
                      'active', ?, NULL, 'cloud', ?)
            """,
            (
                source_set_id,
                identity.scope_id,
                len(evidence_rows),
                identity.principal_id,
                now,
                now,
                repository.cloud_instance_id,
            ),
        )
        for ordinal, evidence in enumerate(evidence_rows):
            connection.execute(
                """
                INSERT INTO source_set_members (
                    id, scope_id, source_set_id, source_object_id, source_version,
                    policy_version, source_object_kind, ordinal, added_at,
                    removed_at, version, lifecycle_state, created_at, updated_at,
                    deleted_at, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, ?, 1, 'growth_evidence', ?, ?, NULL, 1,
                          'active', ?, ?, NULL, 'cloud', ?)
                """,
                (
                    repository._record_id("source_member", operation_id, str(evidence["id"])),  # noqa: SLF001
                    identity.scope_id,
                    source_set_id,
                    str(evidence["id"]),
                    int(evidence["version"] or 1),
                    ordinal,
                    now,
                    now,
                    now,
                    repository.cloud_instance_id,
                ),
            )
        bot_id = builtin_agent_id(identity.organization_id, "growth_companion")
        run_id = repository._record_id("run", operation_id, "growth-companion-summary")  # noqa: SLF001
        result = {
            "schema": "yiyu.gc13.growth-companion-summary.v1",
            **normalized,
            "sourceCount": len(evidence_rows),
            "generatedAt": now,
            "state": "ready",
            "agentRun": AgentRunReceipt(
                agent_kind="growth_companion",
                run_id=run_id,
                state="completed",
                stage="weekly_summary_ready",
                message="成长陪伴已按本人的正式工作证据生成周总结",
                result_version=1,
            ).as_dict(),
            "idempotentReplay": False,
        }
        result_manifest_id, result_hash = _result_manifest(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            kind="growth_companion_summary",
            result=result,
            now=now,
        )
        _trace_command(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            result_manifest_id=result_manifest_id,
            result_hash=result_hash,
            command_type=command_type,
            aggregate_type="execution_run",
            aggregate_id=run_id,
            aggregate_version=1,
            target_resource_id=None,
            command_status="settled",
            event_type="gc13.growth_companion.summarized",
            now=now,
        )
        connection.execute(
            """
            INSERT INTO execution_runs (
                id, scope_id, bot_id, rule_id, task_id, operation_id, status,
                initiator_membership_id, proposal_id, run_kind,
                progress_object_manifest_id, result_object_manifest_id,
                started_at, finished_at, version, lifecycle_state,
                created_at, updated_at, deleted_at
            ) VALUES (?, ?, ?, NULL, NULL, ?, 'completed', ?, NULL,
                      'weekly_growth_summary', NULL, ?, ?, ?, 1, 'active', ?, ?, NULL)
            """,
            (
                run_id,
                identity.scope_id,
                bot_id,
                operation_id,
                identity.membership_id,
                result_manifest_id,
                now,
                now,
                now,
                now,
            ),
        )
        connection.commit()
        return result


def _allowed_preferences(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT id, target_id, capability_set
        FROM organization_memberships
        WHERE scope_id=? AND record_kind='preference'
          AND parent_membership_id=? AND target_type='stable_preference'
          AND status='active' AND lifecycle_state='active'
        ORDER BY target_id, id
        """,
        (identity.scope_id, identity.membership_id),
    ).fetchall()
    preferences: list[dict[str, Any]] = []
    for row in rows:
        spec = _json(row["capability_set"], {})
        consumers = {str(item) for item in spec.get("allowConsumers") or []}
        if (
            spec.get("schema") != PREFERENCE_SCHEMA
            or spec.get("memberAllowed") is not True
            or "growth_companion" not in consumers
        ):
            continue
        value = str(spec.get("value") or "").strip()
        if not value:
            continue
        preferences.append(
            {
                "preferenceId": str(row["id"]),
                "key": str(row["target_id"] or ""),
                "label": str(spec.get("label") or row["target_id"] or "个人偏好")[:80],
                "value": value[:1_000],
                "origin": str(spec.get("origin") or "explicit"),
                "memberAllowed": True,
                "consumer": "growth_companion",
            }
        )
    return preferences


def _read_models(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT model.*, manifest.receipt
        FROM growth_read_models AS model
        JOIN object_manifests AS manifest
          ON manifest.scope_id=model.scope_id
         AND manifest.id=model.model_object_manifest_id
         AND manifest.lifecycle_state='active'
        WHERE model.scope_id=? AND model.membership_id=?
          AND model.invalidated_at IS NULL
        ORDER BY model.model_kind, model.metric_key, model.badge_key,
                 model.ability_key, model.generated_at, model.id
        """,
        (identity.scope_id, identity.membership_id),
    ).fetchall()
    models: list[dict[str, Any]] = []
    for row in rows:
        payload = _json(row["receipt"], {})
        if payload.get("schema") == READ_MODEL_SCHEMA:
            models.append(dict(payload))
    return models


def _latest_rebuild(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
) -> dict[str, Any] | None:
    rows = connection.execute(
        """
        SELECT run.id, run.status, run.started_at, run.completed_at,
               manifest.receipt, command.actor_membership_id
        FROM reconciliation_runs AS run
        JOIN commands AS command
          ON command.scope_id=run.scope_id
         AND command.operation_id=run.operation_id
        LEFT JOIN object_manifests AS manifest
          ON manifest.scope_id=run.scope_id
         AND manifest.id=run.result_object_manifest_id
        WHERE run.scope_id=? AND run.reconciliation_kind=?
          AND command.actor_membership_id=?
        ORDER BY COALESCE(run.completed_at, run.started_at) DESC, run.id DESC
        LIMIT 1
        """,
        (identity.scope_id, RECONCILIATION_KIND, identity.membership_id),
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    receipt = _json(row["receipt"], {})
    result = receipt.get("result") if isinstance(receipt, Mapping) else {}
    return {
        "runId": str(row["id"]),
        "status": str(row["status"]),
        "startedAt": str(row["started_at"] or ""),
        "completedAt": str(row["completed_at"] or ""),
        "result": dict(result) if isinstance(result, Mapping) else {},
    }


def _latest_companion_summary(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    source_fingerprint: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT manifest.receipt
        FROM execution_runs AS run
        JOIN object_manifests AS manifest
          ON manifest.scope_id=run.scope_id
         AND manifest.id=run.result_object_manifest_id
         AND manifest.lifecycle_state='active'
        WHERE run.scope_id=? AND run.initiator_membership_id=?
          AND run.run_kind='weekly_growth_summary'
          AND run.status='completed' AND run.lifecycle_state='active'
        ORDER BY run.finished_at DESC, run.id DESC
        LIMIT 1
        """,
        (identity.scope_id, identity.membership_id),
    ).fetchone()
    if row is None:
        return None
    receipt = _json(row["receipt"], {})
    result = receipt.get("result") if isinstance(receipt, Mapping) else None
    if not isinstance(result, Mapping):
        return None
    if result.get("schema") != "yiyu.gc13.growth-companion-summary.v1":
        return None
    if result.get("sourceFingerprint") != source_fingerprint:
        return None
    return dict(result)


def _growth_analysis_context(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
) -> dict[str, Any]:
    """Return bounded, member-owned work context for semantic growth analysis.

    The context deliberately excludes ``personalPrivateNote``.  It keeps the
    submitted review text together with the task, event-line and planning
    context that explains why a reflection matters.
    """

    rows = connection.execute(
        """
        SELECT review.id AS review_id, version.id AS review_version_id,
               version.version, version.submitted_at, version.content_hash,
               manifest.receipt
        FROM weekly_reviews AS review
        JOIN weekly_review_versions AS version
          ON version.scope_id=review.scope_id
         AND version.id=review.current_submitted_version_id
        JOIN object_manifests AS manifest
          ON manifest.scope_id=version.scope_id
         AND manifest.id=version.content_object_manifest_id
         AND manifest.lifecycle_state='active'
        WHERE review.scope_id=? AND review.membership_id=?
          AND review.lifecycle_state='active'
          AND version.business_state='submitted'
        ORDER BY version.submitted_at DESC, version.id DESC
        LIMIT 8
        """,
        (identity.scope_id, identity.membership_id),
    ).fetchall()
    reviews: list[dict[str, Any]] = []
    task_ids: set[str] = set()
    for row in rows:
        receipt = _json(row["receipt"], {})
        content = receipt.get("content") if isinstance(receipt, Mapping) else {}
        content = content if isinstance(content, Mapping) else {}
        entries: list[dict[str, Any]] = []
        for raw_entry in content.get("taskEntries") or []:
            if not isinstance(raw_entry, Mapping):
                continue
            task_id = str(raw_entry.get("taskId") or "").strip()
            if task_id:
                task_ids.add(task_id)
            structured = raw_entry.get("structuredNote")
            structured = structured if isinstance(structured, Mapping) else {}
            entries.append(
                {
                    "taskId": task_id,
                    "note": str(raw_entry.get("note") or "")[:2_000],
                    "reflection": str(structured.get("reflection") or "")[:2_000],
                    "successExperience": str(structured.get("successExperience") or "")[:1_000],
                    "failureInsight": str(structured.get("failureInsight") or "")[:1_000],
                    "blockerReason": str(structured.get("blockerReason") or "")[:800],
                    "supportNeeded": str(structured.get("supportNeeded") or "")[:800],
                    "nextAction": str(structured.get("nextAction") or "")[:800],
                    "completionStatus": str(structured.get("completionStatus") or ""),
                }
            )
        reviews.append(
            {
                "reviewId": str(row["review_id"]),
                "reviewVersionId": str(row["review_version_id"]),
                "version": int(row["version"] or 1),
                "submittedAt": str(row["submitted_at"] or ""),
                "contentHash": str(row["content_hash"] or ""),
                "weekLabel": str(content.get("weekLabel") or ""),
                "summary": str(content.get("summary") or "")[:2_000],
                "workFreeNote": str(content.get("workFreeNote") or "")[:2_000],
                "personalGrowthNote": str(content.get("personalGrowthNote") or "")[:2_000],
                "taskEntries": entries,
            }
        )

    tasks: list[dict[str, Any]] = []
    if task_ids:
        placeholders = ",".join("?" for _ in task_ids)
        task_rows = connection.execute(
            f"""
            SELECT task.id, task.title, task.description, task.completion_note,
                   task.priority, task.completed_at, task.client_id,
                   event.id AS event_line_id,
                   COALESCE(event.name,event.title) AS event_line_name,
                   event.goal AS event_line_goal, event.background AS event_line_background,
                   cycle.id AS planning_cycle_id, cycle.title AS planning_cycle_title,
                   cycle.summary AS planning_cycle_summary
            FROM tasks AS task
            LEFT JOIN event_lines AS event
              ON event.scope_id=task.scope_id AND event.id=task.event_line_id
             AND event.lifecycle_state='active'
            LEFT JOIN planning_cycles AS cycle
              ON cycle.scope_id=task.scope_id AND cycle.id=task.planning_cycle_id
             AND cycle.lifecycle_state='active'
            WHERE task.scope_id=? AND task.lifecycle_state='active'
              AND task.id IN ({placeholders})
            ORDER BY task.completed_at DESC, task.updated_at DESC
            """,
            (identity.scope_id, *sorted(task_ids)),
        ).fetchall()
        for row in task_rows:
            tasks.append(
                {
                    "taskId": str(row["id"]),
                    "title": str(row["title"] or "")[:300],
                    "description": str(row["description"] or "")[:1_500],
                    "completionNote": str(row["completion_note"] or "")[:1_200],
                    "priority": str(row["priority"] or ""),
                    "completedAt": str(row["completed_at"] or ""),
                    "clientId": str(row["client_id"] or ""),
                    "eventLine": {
                        "id": str(row["event_line_id"] or ""),
                        "name": str(row["event_line_name"] or "")[:300],
                        "goal": str(row["event_line_goal"] or "")[:800],
                        "background": str(row["event_line_background"] or "")[:1_000],
                    },
                    "planningCycle": {
                        "id": str(row["planning_cycle_id"] or ""),
                        "title": str(row["planning_cycle_title"] or "")[:300],
                        "summary": str(row["planning_cycle_summary"] or "")[:1_000],
                    },
                }
            )

    previous: list[dict[str, Any]] = []
    previous_rows = connection.execute(
        """
        SELECT manifest.receipt
        FROM execution_runs AS run
        JOIN object_manifests AS manifest
          ON manifest.scope_id=run.scope_id
         AND manifest.id=run.result_object_manifest_id
         AND manifest.lifecycle_state='active'
        WHERE run.scope_id=? AND run.initiator_membership_id=?
          AND run.run_kind='weekly_growth_summary'
          AND run.status='completed' AND run.lifecycle_state='active'
        ORDER BY run.finished_at DESC, run.id DESC
        LIMIT 4
        """,
        (identity.scope_id, identity.membership_id),
    ).fetchall()
    for row in previous_rows:
        receipt = _json(row["receipt"], {})
        result = receipt.get("result") if isinstance(receipt, Mapping) else None
        if not isinstance(result, Mapping):
            continue
        previous.append(
            {
                "weeklySummary": str(result.get("weeklySummary") or "")[:500],
                "growthHighlights": list(result.get("growthHighlights") or [])[:3],
                "generatedAt": str(result.get("generatedAt") or ""),
            }
        )
    return {"reviews": reviews, "tasks": tasks, "previousGrowth": previous}


def _growth_companion_source_fingerprint(
    evidence_rows: Sequence[Mapping[str, Any]],
    rule_rows: Sequence[Mapping[str, Any]],
    analysis_context: Mapping[str, Any],
) -> str:
    return sha256_text(
        canonical_json(
            {
                "growthInput": _growth_input_fingerprint(evidence_rows, rule_rows),
                # Previous AI summaries help the next interpretation compare
                # periods, but must not invalidate the source immediately after
                # a new summary is recorded.
                "analysisContext": {
                    "reviews": list(analysis_context.get("reviews") or []),
                    "tasks": list(analysis_context.get("tasks") or []),
                },
            }
        )
    )


def _experience_wall_items(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT run.initiator_membership_id, run.finished_at, manifest.receipt,
               principal.display_name
        FROM execution_runs AS run
        JOIN object_manifests AS manifest
          ON manifest.scope_id=run.scope_id
         AND manifest.id=run.result_object_manifest_id
         AND manifest.lifecycle_state='active'
        LEFT JOIN organization_memberships AS membership
          ON membership.scope_id=run.scope_id
         AND membership.id=run.initiator_membership_id
        LEFT JOIN principals AS principal
          ON principal.id=membership.principal_id
        WHERE run.scope_id=? AND run.run_kind='weekly_growth_summary'
          AND run.status='completed' AND run.lifecycle_state='active'
        ORDER BY run.finished_at DESC, run.id DESC
        LIMIT 200
        """,
        (identity.scope_id,),
    ).fetchall()
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        receipt = _json(row["receipt"], {})
        result = receipt.get("result") if isinstance(receipt, Mapping) else None
        if not isinstance(result, Mapping):
            continue
        for raw in result.get("experienceEntries") or []:
            if not isinstance(raw, Mapping):
                continue
            entry_id = str(raw.get("id") or "").strip()
            text = str(raw.get("text") or "").strip()
            if not entry_id or not text or entry_id in by_id:
                continue
            kind = str(raw.get("kind") or "distilled")
            by_id[entry_id] = {
                "id": entry_id,
                "source": "exp_wall",
                "text": text,
                "summary": text,
                "authorUserId": str(row["initiator_membership_id"] or ""),
                "authorUserName": str(row["display_name"] or "团队成员"),
                "clientId": None,
                "clientName": None,
                "sourceType": (
                    "review_quote" if kind == "quote" else "review_insight"
                ),
                "sourceObjectId": str(raw.get("sourceId") or ""),
                "sourceTitle": str(raw.get("sourceTitle") or "成长复盘"),
                "category": str(raw.get("category") or "成长经验"),
                "reuseCount": 0,
                "likeCount": 0,
                "saveCount": 0,
                "currentUserLiked": False,
                "linkedContexts": [],
                "createdAt": str(result.get("generatedAt") or row["finished_at"] or ""),
            }
    if not by_id:
        return []
    placeholders = ",".join("?" for _ in by_id)
    reaction_rows = connection.execute(
        f"""
        SELECT target_resource_id,
               COUNT(DISTINCT actor_membership_id) AS like_count,
               MAX(CASE WHEN actor_membership_id=? THEN 1 ELSE 0 END) AS current_liked
        FROM audit_events
        WHERE scope_id=? AND action='gc13.experience_quote.liked'
          AND target_resource_id IN ({placeholders})
        GROUP BY target_resource_id
        """,
        (identity.membership_id, identity.scope_id, *by_id.keys()),
    ).fetchall()
    for row in reaction_rows:
        item = by_id.get(str(row["target_resource_id"] or ""))
        if item is None:
            continue
        item["likeCount"] = int(row["like_count"] or 0)
        item["currentUserLiked"] = bool(row["current_liked"])
    return sorted(
        by_id.values(),
        key=lambda item: (str(item.get("createdAt") or ""), str(item.get("id") or "")),
        reverse=True,
    )


def like_growth_experience_quote(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    quote_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    quote_id = _identifier(quote_id, field="experience_quote_id", maximum=160)
    idempotency_key = _identifier(idempotency_key, field="idempotency_key", maximum=200)
    now = utc_now()
    command_type = "gc13.experience_quote.liked"
    payload_hash = sha256_text(canonical_json({"quoteId": quote_id, "reaction": "like"}))
    operation_id = repository._operation_id(  # noqa: SLF001
        identity.scope_id,
        command_type,
        idempotency_key,
    )
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        replay = _replay(
            connection,
            identity,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
        )
        if replay is not None:
            connection.rollback()
            return replay
        items = _experience_wall_items(connection, identity)
        item = next((value for value in items if value["id"] == quote_id), None)
        if item is None:
            connection.rollback()
            raise RepositoryError(404, "gc13_experience_quote_not_found", "成长金句不存在")
        already_liked = connection.execute(
            """
            SELECT 1 FROM audit_events
            WHERE scope_id=? AND action=? AND target_resource_id=?
              AND actor_membership_id=?
            LIMIT 1
            """,
            (identity.scope_id, command_type, quote_id, identity.membership_id),
        ).fetchone()
        result = {
            **item,
            "likeCount": int(item.get("likeCount") or 0) + (0 if already_liked else 1),
            "currentUserLiked": True,
            "idempotentReplay": bool(already_liked),
        }
        result_manifest_id, result_hash = _result_manifest(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            kind="growth_experience_quote_like",
            result=result,
            now=now,
        )
        _trace_command(
            repository,
            connection,
            identity,
            operation_id=operation_id,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            result_manifest_id=result_manifest_id,
            result_hash=result_hash,
            command_type=command_type,
            aggregate_type="growth_experience_quote",
            aggregate_id=quote_id,
            aggregate_version=1,
            target_resource_id=quote_id,
            command_status="settled",
            event_type=command_type,
            now=now,
        )
        connection.commit()
        return result


def growth_snapshot(
    repository: CloudRepository,
    identity: SessionIdentity,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        evidence_rows = _evidence_rows(connection, identity)
        rule_rows = _active_rule_rows(connection, identity)
        analysis_context = _growth_analysis_context(connection, identity)
        source_fingerprint = _growth_companion_source_fingerprint(
            evidence_rows,
            rule_rows,
            analysis_context,
        )
        evidence = [_evidence_dto(row) for row in evidence_rows]
        rules = [_rule_dto(row) for row in rule_rows]
        models = _read_models(connection, identity)
        latest_rebuild = _latest_rebuild(connection, identity)
        companion_summary = _latest_companion_summary(
            connection,
            identity,
            source_fingerprint=source_fingerprint,
        )
        bot_id = builtin_agent_id(identity.organization_id, "growth_companion")
        bot = connection.execute(
            """
            SELECT bot.id, bot.enabled, bot.capability_policy_version
            FROM bot_definitions AS bot
            JOIN authorization_scopes AS agent_scope ON agent_scope.id=bot.scope_id
            WHERE bot.id=? AND bot.agent_kind='growth_companion'
              AND bot.lifecycle_state='active'
              AND agent_scope.organization_id=?
              AND agent_scope.status='active'
              AND agent_scope.lifecycle_state='active'
            """,
            (bot_id, identity.organization_id),
        ).fetchone()
        preferences = _allowed_preferences(connection, identity) if bot and bool(bot["enabled"]) else []

    if not rules:
        rebuild_state = "not_connected"
        rebuild_message = "成长规则尚未发布；成长证据仍可独立记录"
    elif models:
        rebuild_state = "ready"
        rebuild_message = "成长指标、徽章与能力已按当前规则生成"
    elif latest_rebuild and latest_rebuild["status"] == "failed":
        rebuild_state = "failed_retryable"
        rebuild_message = "成长指标重算暂时失败；成长证据已保留，可以重试"
    else:
        rebuild_state = "updating"
        rebuild_message = "成长证据已记录，指标与徽章待重算"

    agent_ready = bool(bot and bot["enabled"])
    companion_state = rebuild_state if agent_ready else "base_mode"
    return {
        "schema": "yiyu.gc13.growth-snapshot.v1",
        "memberId": identity.membership_id,
        "evidence": evidence,
        "rules": rules,
        "readModel": {
            "state": rebuild_state,
            "models": models,
            "metrics": [item for item in models if item.get("modelKind") == "metric"],
            "badges": [item for item in models if item.get("modelKind") == "badge"],
            "abilities": [item for item in models if item.get("modelKind") == "ability"],
            "overview": next((item for item in models if item.get("modelKind") == "overview"), None),
        },
        "rebuild": {
            "state": rebuild_state,
            "retryable": rebuild_state == "failed_retryable",
            "message": rebuild_message,
            "latestRun": latest_rebuild,
        },
        "companion": {
            "agentId": bot_id,
            "agentKind": "growth_companion",
            "mode": "growth_companion" if agent_ready else "base_mode",
            "state": companion_state,
            "baseMode": "专门能力不可用时保留确定性成长记录并准确说明基础模式",
            "allowedPreferences": preferences,
            "sourceLabels": ["成长证据", "成长规则"]
            + (["成员本人允许的通用偏好"] if preferences else []),
            "boundaries": [
                "不读取项目协作记忆",
                "不把成长数据转换成 Skill",
                "不读取未获成员本人允许的偏好",
            ],
            "sourceFingerprint": source_fingerprint,
            "summary": companion_summary,
            "analysisContext": analysis_context,
        },
        "weeklyReviewAdapter": {
            "contract": "yiyu.gc13.formal-work-evidence-port.v2",
            "status": "connected",
            "acceptedSourceType": "weekly_review",
            "readsWeeklyReviewTables": True,
            "writesCandidateBeforeMemberConfirmation": False,
            "requiresMemberApproval": False,
        },
        "weeklyReviewCandidates": [],
        "skillBoundary": {
            "autoCreatesSkill": False,
            "readsAutomationRules": False,
        },
        "projectMemoryBoundary": {
            "consumed": False,
            "copiedIntoGrowthEvidence": False,
        },
    }


def growth_compatibility_view(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    view: str,
) -> dict[str, Any]:
    """Present the frozen GC-13 authority through the existing Growth Center UI.

    This adapter deliberately reads only the GC-13 snapshot.  It never calls
    the frozen all-domain business snapshot and therefore cannot reintroduce a
    generic projection authority.
    """

    snapshot = growth_snapshot(repository, identity)
    evidence = list(snapshot["evidence"])
    read_model = dict(snapshot["readModel"])
    companion_summary = (snapshot.get("companion") or {}).get("summary")
    companion_summary = companion_summary if isinstance(companion_summary, Mapping) else {}
    ai_highlights = [
        item
        for item in companion_summary.get("growthHighlights") or []
        if isinstance(item, Mapping)
    ]
    category_points: dict[str, float] = {}
    for rule in snapshot.get("rules") or []:
        spec = rule.get("spec") if isinstance(rule, Mapping) else None
        if not isinstance(spec, Mapping):
            continue
        points = float(spec.get("pointsPerEvidence") or 10.0)
        for category in spec.get("evidenceCategories") or []:
            category_points[str(category)] = points

    def evidence_xp(item: Mapping[str, Any]) -> float:
        return round(
            float(item.get("contributionScore") or 1.0)
            * category_points.get(str(item.get("category") or ""), 1.0),
            2,
        )

    generated_at = max(
        (str(item.get("generatedAt") or "") for item in read_model["models"]),
        default="",
    ) or max(
        (str(item.get("createdAt") or "") for item in evidence),
        default=utc_now(),
    )
    ledger = [
        {
            "id": item["evidenceId"],
            "userId": identity.membership_id,
            "userName": identity.display_name,
            "abilityKey": item["category"],
            "abilityLabel": item["category"],
            "evidenceId": item["evidenceId"],
            "xpType": item["sourceType"] or "growth_evidence",
            "delta": evidence_xp(item),
            "baseXp": evidence_xp(item),
            "premiumRate": 0,
            "premiumXp": 0,
            "totalXp": evidence_xp(item),
            "reason": item["summary"],
            "sourceType": item["sourceType"],
            "sourceId": item["sourceId"],
            "sourceTitle": item["summary"],
            "handbookEntryId": None,
            "taskId": None,
            "meetingId": None,
            "reviewId": None,
            "clientId": None,
            "clientName": None,
            "eventLineId": None,
            "eventLineName": None,
            "sourceRoute": [value for value in (item["sourceType"], item["sourceId"]) if value],
            "evidenceRefs": [item["evidenceId"]],
            "contextSummary": item["summary"],
            "linkedContexts": [],
            "contributionTags": [item["category"]],
            "validationState": item["validationState"],
            "orgContributionScore": item["contributionScore"],
            "weekLabel": "",
            "createdAt": item["createdAt"],
            "reversedAt": None,
        }
        for item in evidence
    ]
    overview_model = read_model.get("overview")
    total_xp = float(overview_model.get("totalScore") or 0) if isinstance(overview_model, Mapping) else sum(
        float(item["totalXp"]) for item in ledger
    )
    current_week_start = (
        datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday())
    ).replace(hour=0, minute=0, second=0, microsecond=0)
    weekly_xp = 0.0
    for item in ledger:
        try:
            created_at = datetime.fromisoformat(str(item.get("createdAt") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if created_at >= current_week_start:
            weekly_xp += float(item.get("totalXp") or 0)

    evidence_with_dates: list[tuple[Mapping[str, Any], datetime]] = []
    for item in evidence:
        try:
            evidence_with_dates.append(
                (
                    item,
                    datetime.fromisoformat(
                        str(item.get("createdAt") or "").replace("Z", "+00:00")
                    ),
                )
            )
        except ValueError:
            continue
    today = datetime.now(timezone.utc).date()
    activity_counts: dict[str, int] = {}
    for _, created_at in evidence_with_dates:
        key = created_at.date().isoformat()
        activity_counts[key] = activity_counts.get(key, 0) + 1
    activity_days = []
    for offset in range(83, -1, -1):
        day = today - timedelta(days=offset)
        count = activity_counts.get(day.isoformat(), 0)
        activity_days.append(
            {
                "date": day.isoformat(),
                "count": count,
                "level": min(4, count),
            }
        )
    active_dates = sorted(
        datetime.fromisoformat(value).date() for value, count in activity_counts.items() if count > 0
    )
    longest_activity_streak = 0
    current_activity_streak = 0
    previous_date = None
    for day in active_dates:
        current_activity_streak = (
            current_activity_streak + 1
            if previous_date is not None and day == previous_date + timedelta(days=1)
            else 1
        )
        longest_activity_streak = max(longest_activity_streak, current_activity_streak)
        previous_date = day

    work_type_labels = {
        "formal_task": "任务推进",
        "formal_meeting": "会议协作",
        "weekly_review": "复盘沉淀",
        "handbook_reuse": "方法复用",
    }
    category_work_type_labels = {
        "analysis": "分析与洞察",
        "collaboration": "协作推进",
        "execution": "任务推进",
        "reflection": "复盘沉淀",
    }
    work_type_counts: dict[str, int] = {}
    for item in evidence:
        label = work_type_labels.get(str(item.get("sourceType") or "")) or category_work_type_labels.get(
            str(item.get("category") or ""),
            "其他正式证据",
        )
        work_type_counts[label] = work_type_counts.get(label, 0) + 1

    review_dates = sorted(
        created_at.date()
        for item, created_at in evidence_with_dates
        if item.get("sourceType") == "weekly_review"
    )
    review_weeks = sorted({day - timedelta(days=day.weekday()) for day in review_dates})
    review_streak = 0
    max_review_streak = 0
    previous_week = None
    for week in review_weeks:
        review_streak = (
            review_streak + 1
            if previous_week is not None and week == previous_week + timedelta(days=7)
            else 1
        )
        max_review_streak = max(max_review_streak, review_streak)
        previous_week = week
    if review_weeks:
        this_week = today - timedelta(days=today.weekday())
        if review_weeks[-1] not in {this_week, this_week - timedelta(days=7)}:
            review_streak = 0

    task_dates = [
        created_at.date()
        for item, created_at in evidence_with_dates
        if item.get("sourceType") == "formal_task"
    ]
    task_streak = 0
    longest_task_streak = 0
    previous_task_date = None
    for day in sorted(set(task_dates)):
        task_streak = (
            task_streak + 1
            if previous_task_date is not None and day == previous_task_date + timedelta(days=1)
            else 1
        )
        longest_task_streak = max(longest_task_streak, task_streak)
        previous_task_date = day
    if previous_task_date not in {today, today - timedelta(days=1)}:
        task_streak = 0
    month_start = today.replace(day=1)
    previous_month_end = month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)
    monthly_tasks = sum(day >= month_start for day in task_dates)
    previous_month_tasks = sum(previous_month_start <= day <= previous_month_end for day in task_dates)
    ability_key_aliases = {
        "execution": "exec",
        "collaboration": "collab",
        "analysis": "analyze",
        "reflection": "write",
    }
    abilities = [
        {
            "abilityKey": ability_key_aliases.get(
                str(item.get("abilityKey") or ""),
                str(item.get("abilityKey") or item.get("metricKey") or "growth"),
            ),
            "label": str(item.get("label") or item.get("abilityKey") or "成长能力"),
            "currentScore": float(item.get("score") or 0),
            "previousScore": 0,
            "totalXp": float(item.get("score") or 0),
            "weeklyXp": 0,
            "stage": "已有证据" if float(item.get("score") or 0) > 0 else "待积累",
            "nextStage": "持续验证",
            "evidence": f"{int(item.get('evidenceCount') or 0)} 条权威成长证据",
        }
        for item in read_model["abilities"]
    ]
    ability_by_key = {str(item["abilityKey"]): item for item in abilities}
    for signal in ai_highlights:
        ability_key = str(signal.get("abilityKey") or "")
        if not ability_key:
            continue
        try:
            ai_score = max(20, min(100, int(signal.get("level") or 1) * 20))
        except (TypeError, ValueError):
            ai_score = 20
        trend = str(signal.get("trend") or "forming")
        weekly_delta = 10 if trend == "up" else 5 if trend == "forming" else 0
        existing = ability_by_key.get(ability_key)
        if existing is None:
            existing = {
                "abilityKey": ability_key,
                "label": str(signal.get("abilityLabel") or signal.get("title") or ability_key),
                "currentScore": ai_score,
                "previousScore": max(0, ai_score - weekly_delta),
                "totalXp": ai_score,
                "weeklyXp": weekly_delta,
                "stage": str(signal.get("title") or "正在形成"),
                "nextStage": "持续在真实工作中验证",
                "evidence": str(signal.get("summary") or ""),
            }
            abilities.append(existing)
            ability_by_key[ability_key] = existing
        else:
            existing["currentScore"] = max(float(existing.get("currentScore") or 0), ai_score)
            existing["previousScore"] = max(
                0,
                float(existing["currentScore"]) - weekly_delta,
            )
            existing["weeklyXp"] = weekly_delta
            existing["stage"] = str(signal.get("title") or existing.get("stage") or "正在形成")
            existing["evidence"] = str(signal.get("summary") or existing.get("evidence") or "")
    if view == "overview":
        return {
            "userId": identity.membership_id,
            "userName": identity.display_name,
            "totalXp": total_xp,
            "weeklyXp": weekly_xp,
            "weeklyBaseXp": weekly_xp,
            "weeklyPremiumXp": 0,
            "level": int(total_xp),
            "stageLabel": "已形成成长证据" if evidence else "成长证据整理中",
            "xpToNext": 1,
            "rank": {
                "key": "evidence",
                "name": "成长证据",
                "division": None,
                "fullLabel": f"{len(evidence)} 条工作证据",
                "progress": min(100, total_xp),
                "nextName": "100 XP 成长里程碑",
                "xpToNext": max(0, 100 - total_xp),
            },
            "abilities": abilities,
            "recentEntries": ledger[:20],
            "recommendations": [],
            "companionSummary": companion_summary,
            "evidenceItems": evidence,
            "sourceCoverage": {
                "taskSignals": sum(item["sourceType"] == "formal_task" for item in evidence),
                "meetingSignals": sum(item["sourceType"] == "formal_meeting" for item in evidence),
                "strategicSignals": 0,
                "reviewSignals": sum(item["sourceType"] == "weekly_review" for item in evidence),
                "handbookSignals": sum(item["sourceType"] == "handbook_reuse" for item in evidence),
                "expWallSignals": 0,
                "memorySignals": 0,
                "documentSignals": 0,
                "clientCount": 0,
            },
            "dailyActivity": {
                "days": activity_days,
                "totalDays": len(activity_days),
                "activeDays": sum(item["count"] > 0 for item in activity_days),
                "maxStreak": longest_activity_streak,
            },
            "commitmentSummary": {
                "totalCount": len(task_dates),
                "fulfilledCount": len(task_dates),
                "pendingCount": 0,
                "overdueCount": 0,
                "rate": 100 if task_dates else 0,
                "trend": [],
                "upcomingPending": [],
                "currentStreakDays": task_streak,
                "longestStreakDays": longest_task_streak,
                "monthlyFulfilledCount": monthly_tasks,
                "lastMonthFulfilledCount": previous_month_tasks,
                "growthPercent": 0,
                "cumulativeCurve": [],
            },
            "reviewStreak": {
                "currentStreakWeeks": review_streak,
                "maxStreakWeeks": max_review_streak,
                "totalReviewWeeks": len(review_weeks),
                "lastReviewedWeekLabel": review_weeks[-1].isoformat() if review_weeks else "",
                "monthlyEntryCount": sum(day >= month_start for day in review_dates),
                "lastMonthEntryCount": sum(
                    previous_month_start <= day <= previous_month_end for day in review_dates
                ),
                "weeklyTrend": [
                    {
                        "weekLabel": week.isoformat(),
                        "entryCount": sum(
                            week <= day < week + timedelta(days=7) for day in review_dates
                        ),
                        "charCount": 0,
                    }
                    for week in review_weeks[-8:]
                ],
                "dailyTrend": [],
            },
            "workTypeDistribution": {
                "slices": [
                    {"label": label, "count": count}
                    for label, count in sorted(
                        work_type_counts.items(), key=lambda pair: (-pair[1], pair[0])
                    )
                ],
                "totalTasks": len(evidence),
                "unlabeledTasks": 0,
            },
            "projectGrowthHighlights": [],
            "eventLineGrowthHighlights": [],
            "strategicAlignmentHighlights": [],
            "pendingCaptures": [],
            "currentFocusActions": [],
            "abilityGaps": [],
            "updatedAt": generated_at,
            "derivation": "GC-13 confirmed growth_evidence and growth_read_models",
        }
    if view == "ledger":
        return {"entries": ledger}
    if view == "badges":
        badge_models = list(read_model["badges"])
        if not badge_models:
            # Product-default baseline badges are a rebuildable presentation of
            # the existing ability models. They do not add a second badge
            # authority or require a new table.
            badge_models = [
                {
                    "badgeKey": f"{str(item.get('abilityKey') or 'growth')}_starter",
                    "label": f"{str(item.get('label') or '成长能力')}起步",
                    "state": "earned" if int(item.get("evidenceCount") or 0) > 0 else "locked",
                    "score": float(item.get("score") or 0),
                    "minimum": 10.0,
                    "progressPercent": min(100, round(float(item.get("score") or 0) / 10 * 100)),
                    "abilityKey": str(item.get("abilityKey") or "growth"),
                    "evidenceCount": int(item.get("evidenceCount") or 0),
                }
                for item in read_model["abilities"]
            ]
        categories = []
        for item in badge_models:
            badge_key = str(item.get("badgeKey") or "growth")
            earned = str(item.get("state") or "locked") == "earned"
            categories.append(
                {
                    "id": f"category:{badge_key}",
                    "label": str(item.get("label") or badge_key),
                    "abilityKey": str(item.get("abilityKey") or badge_key),
                    "abilityLabel": str(item.get("label") or badge_key),
                    "litCount": int(earned),
                    "totalCount": 1,
                    "badges": [
                        {
                            "id": badge_key,
                            "code": badge_key,
                            "name": str(item.get("label") or badge_key),
                            "categoryId": f"category:{badge_key}",
                            "categoryLabel": str(item.get("label") or badge_key),
                            "abilityKey": str(item.get("abilityKey") or badge_key),
                            "abilityLabel": str(item.get("label") or badge_key),
                            "roles": [],
                            "xp": int(float(item.get("score") or 0)),
                            "iconMotif": "evidence",
                            "description": "仅由已确认成长证据点亮",
                            "whyItMatters": "能力展示可从权威成长证据重建",
                            "systemHowText": "growth_evidence confirmed count",
                            "state": "lit" if earned else "locked",
                            "progressValue": int(item.get("evidenceCount") or 0),
                            "progressTarget": 1,
                            "progressPercent": int(item.get("progressPercent") or (100 if earned else 0)),
                            "progressText": f"{int(item.get('evidenceCount') or 0)}/1",
                            "nextActionText": "继续积累并确认成长证据",
                            "actionLinks": [],
                            "evidence": [],
                            "linkedContexts": [],
                            "missingSignals": [] if earned else ["confirmed_evidence"],
                            "unlockedAt": generated_at if earned else None,
                            "masteryLevel": int(item.get("evidenceCount") or 0),
                            "historical": False,
                        }
                    ],
                }
            )
        existing_badge_ids = {
            str(badge.get("id") or "")
            for category in categories
            for badge in category.get("badges") or []
        }
        for signal in ai_highlights:
            signal_id = str(signal.get("id") or "")
            if not signal_id or signal_id in existing_badge_ids:
                continue
            label = str(signal.get("title") or signal.get("abilityLabel") or "成长印记")
            ability_key = str(signal.get("abilityKey") or "growth")
            categories.append(
                {
                    "id": f"category:{signal_id}",
                    "label": str(signal.get("abilityLabel") or label),
                    "abilityKey": ability_key,
                    "abilityLabel": str(signal.get("abilityLabel") or label),
                    "litCount": 1,
                    "totalCount": 1,
                    "badges": [
                        {
                            "id": signal_id,
                            "code": signal_id,
                            "name": label,
                            "categoryId": f"category:{signal_id}",
                            "categoryLabel": str(signal.get("abilityLabel") or label),
                            "abilityKey": ability_key,
                            "abilityLabel": str(signal.get("abilityLabel") or label),
                            "roles": [],
                            "xp": int(signal.get("level") or 1) * 10,
                            "iconMotif": "growth_signal",
                            "description": str(signal.get("summary") or ""),
                            "whyItMatters": "由成长陪伴结合复盘原文和关联工作理解",
                            "systemHowText": "AI semantic growth interpretation",
                            "state": "lit",
                            "progressValue": 1,
                            "progressTarget": 1,
                            "progressPercent": 100,
                            "progressText": "本期形成",
                            "nextActionText": "继续在真实工作中验证",
                            "actionLinks": [],
                            "evidence": [],
                            "linkedContexts": [],
                            "missingSignals": [],
                            "unlockedAt": str(companion_summary.get("generatedAt") or generated_at),
                            "masteryLevel": int(signal.get("level") or 1),
                            "historical": False,
                        }
                    ],
                }
            )
        lit = sum(item["litCount"] for item in categories)
        return {
            "overview": {
                "totalBadges": len(categories),
                "litBadges": lit,
                "readyBadges": lit,
                "inProgressBadges": len(categories) - lit,
                "monthlyNewBadges": 0,
                "totalXp": total_xp,
                "upcomingBadgeIds": [item["id"] for item in categories if not item["litCount"]],
            },
            "categories": categories,
            "updatedAt": generated_at,
        }
    if view == "experience-wall":
        with repository._connection() as connection:  # noqa: SLF001
            items = _experience_wall_items(connection, identity)
        return {
            "items": items,
            "refreshedFromCloud": True,
            "cloudSyncError": None,
            "authorityState": "ready" if items else "ready_empty",
        }
    if view == "workbench":
        return {
            "tasks": [],
            "activeTaskId": None,
            "learningSummary": {
                "headline": f"{len(evidence)} 条成长证据可复盘",
                "whyItMatters": "仅基于本人确认的成长事实",
                "immediateMove": "确认下一条成长证据",
                "generator": "rules",
                "confidence": "high" if evidence else "low",
            },
            "genericLessons": [],
            "projectGuidance": [],
            "reasoningTrace": {
                "mode": "rules_only",
                "usedInputs": [item["evidenceId"] for item in evidence],
                "evidenceRefs": [item["evidenceId"] for item in evidence],
                "missingContext": [],
                "aiContribution": [],
                "modelLabel": None,
                "confidence": "high" if evidence else "low",
            },
            "robotAssist": {
                "ready": False,
                "canDelegate": [],
                "mustStayHuman": ["成长确认与任务执行"],
                "why": ["成长陪伴当前只生成可追源的个人读模型"],
            },
            "afterActionCapture": {
                "title": "行动后确认成长事实",
                "summary": "保存前核对来源、能力与证据状态",
                "experienceType": "growth_evidence",
                "recommendedWriteback": "growth_evidence",
            },
            "processSteps": [],
            "activeProcessId": None,
            "actionsBefore": [],
            "actionsDuring": [],
            "actionsAfter": [],
            "supportMaterials": [],
            "checklistItems": [],
            "supportCopy": {
                "title": "成长工作台",
                "intro": "以下内容由严格权威成长事实现场计算",
                "bullets": [],
            },
            "robotPlan": [],
            "sourceMode": "growth_seed" if evidence else "empty",
            "scopeMode": "global",
            "scopeClientId": None,
            "scopeClientName": None,
            "updatedAt": generated_at,
        }
    raise RepositoryError(404, "gc13_view_not_found", "成长中心视图不存在")
