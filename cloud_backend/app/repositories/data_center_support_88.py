from __future__ import annotations

import json
from collections import Counter
from typing import Any, Mapping

from strict_common.ids import canonical_json, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .gc14_proposals import _manifest, _record_command


LABELS = frozenset({"useful", "noise", "needs_review"})


def _id(prefix: str, *parts: str) -> str:
    return prefix + "_" + sha256_text("\x1f".join(parts))[:28]


class DataCenterSupportRepository:
    """Renderer-visible data-center support paths over the frozen 88 tables."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _assert_identity(connection: Any, identity: SessionIdentity) -> None:
        row = connection.execute(
            "SELECT 1 FROM organization_memberships WHERE id=? AND scope_id=? "
            "AND principal_id=? AND record_kind='membership' AND status='active' "
            "AND lifecycle_state='active'",
            (identity.membership_id, identity.scope_id, identity.principal_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(403, "membership_inactive", "当前组织成员身份不可用")

    def resolve(self, identity: SessionIdentity, payload: Mapping[str, Any]) -> dict[str, Any]:
        scope = payload.get("scope") if isinstance(payload.get("scope"), Mapping) else {}
        client_id = str(scope.get("clientId") or "").strip() or None
        task_id = str(scope.get("taskId") or "").strip() or None
        meeting_id = str(scope.get("meetingId") or "").strip() or None
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            if client_id:
                self.repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=client_id, capability="read"
                )
            task = None
            if task_id:
                task = connection.execute(
                    "SELECT id,title,client_id,status,version FROM tasks WHERE scope_id=? "
                    "AND id=? AND lifecycle_state='active'",
                    (identity.scope_id, task_id),
                ).fetchone()
            meeting = None
            if meeting_id:
                meeting = connection.execute(
                    "SELECT id,title,client_id,status,version FROM meetings WHERE scope_id=? "
                    "AND id=? AND lifecycle_state='active'",
                    (identity.scope_id, meeting_id),
                ).fetchone()
            facts = connection.execute(
                "SELECT COUNT(*) FROM atomic_facts f JOIN content_chunks c ON c.id=f.chunk_id "
                "AND c.scope_id=f.scope_id JOIN document_versions v ON v.id=c.document_version_id "
                "AND v.scope_id=c.scope_id JOIN knowledge_documents d ON d.id=v.document_id "
                "AND d.scope_id=v.scope_id WHERE f.scope_id=? AND f.lifecycle_state='active' "
                "AND (? IS NULL OR d.client_id=?)",
                (identity.scope_id, client_id, client_id),
            ).fetchone()[0]
            knowledge = connection.execute(
                "SELECT COUNT(*) FROM knowledge_documents WHERE scope_id=? "
                "AND lifecycle_state='active' AND publication_state='published' "
                "AND (? IS NULL OR client_id=?)",
                (identity.scope_id, client_id, client_id),
            ).fetchone()[0]
        return {
            "scope": dict(scope),
            "pageContext": {
                "scope": dict(scope),
                "authorityState": "ready",
                "sourceSummary": {
                    "publishedKnowledgeCount": int(knowledge),
                    "verifiedFactCount": int(facts),
                    "taskFound": task is not None,
                    "meetingFound": meeting is not None,
                },
                "generatedAt": utc_now(),
            },
            "routeDecision": {
                "mode": str(payload.get("mode") or "page_context"),
                "source": "strict_88_authority",
            },
            "retrievalTrace": None,
            "answerPlan": None,
            "answerMaterial": None,
            "searchResult": None,
            "prepResult": None,
            "proposalDrafts": [],
            "persistedProposalDraftIds": [],
            "dedupedDraftIds": [],
            "actionSuggestions": [],
            "quality": None,
            "debug": {
                "shadow": bool(payload.get("shadow")),
                "mutationExecuted": False,
                "tablesRead": ["tasks", "meetings", "knowledge_documents", "atomic_facts"],
            },
        }

    def team_sync_stats(self, identity: SessionIdentity) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            rows = connection.execute(
                "SELECT id,event_type,status,aggregate_type,aggregate_id,available_at,published_at "
                "FROM outbox_events WHERE scope_id=? AND (event_type LIKE '%member%' "
                "OR event_type LIKE '%collaborat%' OR event_type LIKE '%grant%' "
                "OR event_type LIKE '%share%' OR aggregate_type IN "
                "('organization_membership','task_collaborator','object_grant')) "
                "ORDER BY available_at DESC,id DESC LIMIT 200",
                (identity.scope_id,),
            ).fetchall()
        items = [dict(row) for row in rows]
        counts = Counter(str(item.get("status") or "pending") for item in items)
        return {"total": len(items), "statusCounts": dict(counts), "events": items}

    def evidence_labels(
        self,
        identity: SessionIdentity,
        *,
        label: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        args: list[Any] = [identity.scope_id, identity.principal_id]
        filter_sql = ""
        if label:
            filter_sql = " AND s.purpose_kind=?"
            args.append("evidence_quality_" + label)
        args.append(limit)
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            rows = connection.execute(
                "SELECT m.source_object_id,m.added_at,m.updated_at,s.purpose_kind "
                "FROM source_set_members m JOIN source_sets s ON s.id=m.source_set_id "
                "AND s.scope_id=m.scope_id WHERE m.scope_id=? AND s.created_by_principal_id=? "
                "AND s.purpose_kind LIKE 'evidence_quality_%' AND s.lifecycle_state='active' "
                "AND m.lifecycle_state='active'" + filter_sql +
                " ORDER BY m.updated_at DESC,m.id DESC LIMIT ?",
                tuple(args),
            ).fetchall()
        return [self._annotation(row) for row in rows]

    @staticmethod
    def _annotation(row: Mapping[str, Any]) -> dict[str, Any]:
        annotation_id = str(row["source_object_id"])
        label = str(row["purpose_kind"]).removeprefix("evidence_quality_")
        return {
            "id": annotation_id,
            "sourceType": "search_hit",
            "sourceId": annotation_id,
            "documentId": None,
            "path": None,
            "excerptHash": sha256_text(annotation_id),
            "sourceKind": "local_or_published_source",
            "qualityScore": 1 if label == "useful" else 0,
            "demotionScore": 1 if label == "noise" else 0,
            "noiseReasons": [],
            "authorityHint": "human_feedback",
            "humanLabel": label,
            "humanNote": "",
            "createdAt": str(row["added_at"]),
            "updatedAt": str(row["updated_at"]),
        }

    def label_evidence(
        self,
        identity: SessionIdentity,
        *,
        annotation_id: str,
        label: str,
        note: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if label not in LABELS:
            raise RepositoryError(422, "evidence_label_invalid", "证据标签无效")
        normalized = {"annotationId": annotation_id, "label": label, "noteHash": sha256_text(note)}
        payload_hash = sha256_text(canonical_json(normalized))
        command_type = "data_center.evidence_quality.labeled"
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(connection, identity)
                existing = self.repository._existing_command(  # noqa: SLF001
                    connection,
                    scope_id=identity.scope_id,
                    idempotency_key=idempotency_key,
                    command_type=command_type,
                    payload_hash=payload_hash,
                )
                if existing is not None:
                    row = connection.execute(
                        "SELECT m.source_object_id,m.added_at,m.updated_at,s.purpose_kind "
                        "FROM source_set_members m JOIN source_sets s ON s.id=m.source_set_id "
                        "WHERE m.scope_id=? AND m.source_object_id=? AND m.lifecycle_state='active' "
                        "AND s.created_by_principal_id=? AND s.purpose_kind LIKE 'evidence_quality_%'",
                        (identity.scope_id, annotation_id, identity.principal_id),
                    ).fetchone()
                    connection.commit()
                    if row is None:
                        raise RepositoryError(409, "evidence_label_replay_missing", "证据标签回执已失效")
                    return self._annotation(row)
                now = utc_now()
                set_id = _id("source_set", identity.scope_id, identity.principal_id, label)
                connection.execute(
                    "INSERT INTO source_sets (id,scope_id,client_id,security_label_set_version,"
                    "source_count,version,purpose_kind,publication_state,created_by_principal_id,"
                    "created_at,expires_at,lifecycle_state,updated_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?,NULL,1,0,1,?,'published',?,?,NULL,'active',?,"
                    "NULL,'cloud',?) ON CONFLICT(id) DO NOTHING",
                    (set_id, identity.scope_id, "evidence_quality_" + label,
                     identity.principal_id, now, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "UPDATE source_set_members SET lifecycle_state='deleted',removed_at=?,deleted_at=?,"
                    "updated_at=?,version=version+1 WHERE scope_id=? AND source_object_id=? "
                    "AND lifecycle_state='active' AND source_set_id IN (SELECT id FROM source_sets "
                    "WHERE scope_id=? AND created_by_principal_id=? "
                    "AND purpose_kind LIKE 'evidence_quality_%')",
                    (now, now, now, identity.scope_id, annotation_id,
                     identity.scope_id, identity.principal_id),
                )
                member_id = _id("source_member", set_id, annotation_id)
                connection.execute(
                    "INSERT INTO source_set_members (id,scope_id,source_set_id,source_object_id,"
                    "source_version,policy_version,source_object_kind,ordinal,added_at,removed_at,"
                    "version,lifecycle_state,created_at,updated_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?,?,?,1,1,'evidence_annotation',0,?,NULL,1,"
                    "'active',?,?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET removed_at=NULL,"
                    "lifecycle_state='active',deleted_at=NULL,version=source_set_members.version+1,"
                    "updated_at=excluded.updated_at",
                    (member_id, identity.scope_id, set_id, annotation_id,
                     now, now, now, identity.cloud_instance_id),
                )
                count = connection.execute(
                    "SELECT COUNT(*) FROM source_set_members WHERE source_set_id=? "
                    "AND lifecycle_state='active'", (set_id,)
                ).fetchone()[0]
                connection.execute(
                    "UPDATE source_sets SET source_count=?,version=version+1,updated_at=? WHERE id=?",
                    (int(count), now, set_id),
                )
                result = self._annotation({
                    "source_object_id": annotation_id,
                    "purpose_kind": "evidence_quality_" + label,
                    "added_at": now,
                    "updated_at": now,
                })
                manifest_id = _id("manifest", identity.scope_id, command_type, idempotency_key)
                result_hash = _manifest(
                    connection, self.repository, identity, manifest_id=manifest_id,
                    receipt={"result": result, "noteHash": sha256_text(note)},
                    media_type="application/vnd.yiyu.evidence-quality+json", now=now,
                )
                _record_command(
                    connection, self.repository, identity, command_type=command_type,
                    idempotency_key=idempotency_key, aggregate_type="source_set_member",
                    aggregate_id=member_id, expected_version=None, aggregate_version=1,
                    payload_hash=payload_hash, result_hash=result_hash,
                    result_manifest_id=manifest_id, target_resource_id=None, now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
