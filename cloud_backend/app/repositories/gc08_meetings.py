"""GC-08 cloud authority for safe recording projections and formal minutes."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping, Sequence

from strict_common.agent_memory import AgentRunReceipt, builtin_agent_id
from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .gc03_scope import validate_meeting_client_binding
from .project_materials import GC07ProjectMaterialsRepository


_FORBIDDEN_CLOUD_KEYS = frozenset(
    {
        "audioPath",
        "dialogueText",
        "fullTranscript",
        "localOriginalPath",
        "localPath",
        "segments",
        "sourcePath",
        "storageKey",
        "transcript",
        "transcriptText",
    }
)


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{sha256_text(material)[:30]}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _reject_local_material(value: Any, *, key: str | None = None) -> None:
    if key in _FORBIDDEN_CLOUD_KEYS:
        raise RepositoryError(
            422,
            "gc08_local_material_forbidden",
            "组织云请求不得包含录音、完整转写或本机路径",
        )
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _reject_local_material(child, key=str(child_key))
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            _reject_local_material(child)
        return
    if isinstance(value, str):
        normalized = value.strip().replace("\\", "/")
        if (
            normalized.startswith("file://")
            or normalized.startswith("/Users/")
            or normalized.startswith("/home/")
            or (len(normalized) >= 3 and normalized[1:3] == ":/")
        ):
            raise RepositoryError(
                422,
                "gc08_local_path_forbidden",
                "组织云请求不得包含本机路径",
            )


def _safe_citations(
    value: Any,
    *,
    transcription_id: str,
    transcription_version: int,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise RepositoryError(
            422,
            "meeting_minutes_evidence_required",
            "正式纪要必须保留至少一条转写证据定位",
        )
    citations: list[dict[str, Any]] = []
    for item in value:
        source = _mapping(item)
        locator = str(source.get("locator") or "").strip()
        locator_kind = str(source.get("locatorKind") or "").strip()
        locator_hash = str(source.get("locatorHash") or "").strip()
        if (
            not locator
            or locator_kind
            not in {"char_range", "paragraph", "segment", "time_range"}
            or locator_hash != sha256_text(locator)
        ):
            raise RepositoryError(
                422,
                "meeting_minutes_evidence_invalid",
                "纪要证据定位或校验值无效",
            )
        citations.append(
            {
                "sourceObjectId": transcription_id,
                "sourceObjectKind": "transcription_version",
                "sourceVersion": transcription_version,
                "locator": locator,
                "locatorKind": locator_kind,
                "pageNo": source.get("pageNo"),
                "paragraphNo": source.get("paragraphNo"),
                "locatorHash": locator_hash,
            }
        )
    return citations


class GC08MeetingMinutesRepository:
    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _safe_projection(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        project_id: str,
        meeting_id: str,
        recording: Mapping[str, Any],
        transcription: Mapping[str, Any],
        now: str,
    ) -> tuple[str, str, int]:
        recording_id = str(recording.get("recordingId") or "").strip()
        transcription_id = str(transcription.get("transcriptionId") or "").strip()
        try:
            transcription_version = max(1, int(transcription.get("version") or 1))
            recording_source_version = max(
                1,
                int(recording.get("sourceVersion") or 1),
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                422,
                "gc08_safe_projection_invalid",
                "录音安全元数据版本无效",
            ) from exc
        integrity_hash = str(transcription.get("integrityHash") or "").strip()
        if (
            not recording_id
            or not transcription_id
            or len(integrity_hash) != 64
            or str(transcription.get("status") or "") != "ready"
        ):
            raise RepositoryError(
                422,
                "gc08_safe_projection_invalid",
                "录音或转写安全元数据不完整",
            )
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,"
            "lifecycle_state,version,resource_type_key,created_at,updated_at,"
            "deleted_at,authority_role,origin_instance_id) VALUES (?,?,"
            "'recording','active',1,'meeting_recording',?,?,NULL,'cloud',?) "
            "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at",
            (
                recording_id,
                identity.scope_id,
                now,
                now,
                identity.cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO recordings (id,scope_id,binding_kind,meeting_id,object_manifest_id,"
            "lifecycle_state,current_transcription_version_id,recording_state,"
            "duration_ms,captured_at,device_id,version,created_at,updated_at,"
            "deleted_at,source_version,projection_state,projected_at,stale_at) "
            "VALUES (?,?,'meeting',?,NULL,'active',NULL,'captured',?,?,?,1,?,?,NULL,?,"
            "'current',?,NULL) ON CONFLICT(id) DO UPDATE SET "
            "meeting_id=excluded.meeting_id,"
            "duration_ms=excluded.duration_ms,captured_at=excluded.captured_at,"
            "device_id=excluded.device_id,version=recordings.version+1,"
            "updated_at=excluded.updated_at,source_version=excluded.source_version,"
            "projection_state='current',projected_at=excluded.projected_at,stale_at=NULL",
            (
                recording_id,
                identity.scope_id,
                meeting_id,
                max(0, int(recording.get("durationMs") or 0)) or None,
                str(recording.get("capturedAt") or now),
                str(recording.get("deviceIdHash") or "") or None,
                now,
                now,
                recording_source_version,
                now,
            ),
        )
        existing = connection.execute(
            "SELECT * FROM transcription_versions WHERE scope_id=? AND id=?",
            (identity.scope_id, transcription_id),
        ).fetchone()
        if existing is not None and (
            str(existing["recording_id"]) != recording_id
            or int(existing["version"] or 0) != transcription_version
            or str(existing["integrity_hash"] or "") != integrity_hash
        ):
            raise RepositoryError(
                409,
                "transcription_projection_conflict",
                "转写版本安全投影与既有回执冲突",
            )
        if existing is None:
            connection.execute(
                "INSERT INTO transcription_versions (id,scope_id,recording_id,"
                "document_id,version,status,object_manifest_id,provider_resource_id,"
                "language,created_at,supersedes_version_id,origin_instance_id,"
                "integrity_hash,source_version,projection_state,projected_at,stale_at) "
                "VALUES (?,?,?,NULL,?,'ready',NULL,NULL,?,?,NULL,?,?,?,'current',?,NULL)",
                (
                    transcription_id,
                    identity.scope_id,
                    recording_id,
                    transcription_version,
                    str(transcription.get("language") or "auto"),
                    now,
                    identity.cloud_instance_id,
                    integrity_hash,
                    transcription_version,
                    now,
                ),
            )
        connection.execute(
            "UPDATE recordings SET current_transcription_version_id=?,"
            "recording_state='transcribed',updated_at=? WHERE scope_id=? AND id=?",
            (
                transcription_id,
                now,
                identity.scope_id,
                recording_id,
            ),
        )
        return recording_id, transcription_id, transcription_version

    def publish_minutes(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        meeting_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        _reject_local_material(payload)
        recording = _mapping(payload.get("recording"))
        transcription = _mapping(payload.get("transcription"))
        minutes = _mapping(payload.get("minutes"))
        document_id = str(minutes.get("documentId") or "").strip()
        title = str(minutes.get("title") or "").strip()
        markdown = str(minutes.get("minutesMarkdown") or "").strip()
        content_hash = str(minutes.get("contentHash") or "").strip()
        try:
            expected_version = max(0, int(minutes.get("expectedVersion") or 0))
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                422,
                "meeting_minutes_expected_version_invalid",
                "正式纪要版本信息无效",
            ) from exc
        if (
            not document_id
            or not title
            or not markdown
            or content_hash != sha256_text(markdown)
        ):
            raise RepositoryError(
                422,
                "meeting_minutes_payload_invalid",
                "正式纪要正文、标题或校验值无效",
            )
        payload_hash = GC07ProjectMaterialsRepository._payload_hash(payload)  # noqa: SLF001
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = GC07ProjectMaterialsRepository._receipt(  # noqa: SLF001
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return {**receipt, "idempotentReplay": True}
                project = self.repository._require_project_access(  # noqa: SLF001
                    connection,
                    identity,
                    project_id=project_id,
                )
                meeting = connection.execute(
                    "SELECT * FROM meetings WHERE id=? AND scope_id=? "
                    "AND lifecycle_state='active'",
                    (meeting_id, identity.scope_id),
                ).fetchone()
                if meeting is None:
                    raise RepositoryError(404, "meeting_missing", "会议不存在或已不可用")
                binding = validate_meeting_client_binding(
                    connection,
                    scope_id=identity.scope_id,
                    client_id=meeting["client_id"],
                    event_line_id=meeting["event_line_id"],
                )
                if binding.client_id != project_id:
                    raise RepositoryError(
                        409,
                        "meeting_client_mismatch",
                        "会议与当前项目不一致",
                    )
                now = utc_now()
                recording_id, transcription_id, transcription_version = (
                    self._safe_projection(
                        connection,
                        identity,
                        project_id=project_id,
                        meeting_id=meeting_id,
                        recording=recording,
                        transcription=transcription,
                        now=now,
                    )
                )
                citations = _safe_citations(
                    minutes.get("evidence"),
                    transcription_id=transcription_id,
                    transcription_version=transcription_version,
                )
                current = connection.execute(
                    "SELECT * FROM knowledge_documents WHERE scope_id=? AND id=?",
                    (identity.scope_id, document_id),
                ).fetchone()
                if current is None:
                    if expected_version != 0:
                        raise RepositoryError(
                            409,
                            "meeting_minutes_version_conflict",
                            "正式纪要尚未创建，请刷新后重试",
                        )
                    document_version = 1
                    aggregate_version = 1
                    connection.execute(
                        "INSERT INTO secured_resources (id,scope_id,resource_kind,"
                        "lifecycle_state,version,resource_type_key,created_at,updated_at,"
                        "deleted_at,authority_role,origin_instance_id) VALUES (?,?,"
                        "'knowledge_document','active',1,'meeting_minutes',?,?,NULL,"
                        "'cloud',?)",
                        (
                            document_id,
                            identity.scope_id,
                            now,
                            now,
                            identity.cloud_instance_id,
                        ),
                    )
                else:
                    if int(current["version"] or 0) != expected_version:
                        raise RepositoryError(
                            409,
                            "meeting_minutes_version_conflict",
                            "正式纪要已更新，请刷新后重试",
                        )
                    document_version = int(current["current_version"] or 0) + 1
                    aggregate_version = int(current["version"] or 0) + 1
                document_version_id = _stable_id(
                    "meeting_minutes_version",
                    identity.scope_id,
                    document_id,
                    document_version,
                )
                body_manifest_id = _stable_id(
                    "manifest_meeting_minutes",
                    document_version_id,
                )
                safe_body = canonical_json(
                    {
                        "schema": "yiyu.gc08.formal-meeting-minutes.v1",
                        "title": title,
                        "minutesMarkdown": markdown,
                        "evidence": citations,
                        "recordingId": recording_id,
                        "transcriptionId": transcription_id,
                        "transcriptUploaded": False,
                        "localPathUploaded": False,
                        "actionCandidateCount": len(minutes.get("actionCandidates") or []),
                    }
                )
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,"
                    "content_hash,lifecycle_state,receipt,holder_role,"
                    "holder_instance_id,storage_kind,byte_size,media_type,"
                    "availability_state,receipt_hash,created_at,verified_at,"
                    "deleted_at,authority_role,origin_instance_id) VALUES "
                    "(?,?,NULL,?,'active',?,'organization_cloud',?,"
                    "'formal_meeting_minutes',?,'text/markdown','ready',?,?,?,NULL,"
                    "'cloud',?)",
                    (
                        body_manifest_id,
                        identity.scope_id,
                        content_hash,
                        safe_body,
                        identity.cloud_instance_id,
                        len(markdown.encode("utf-8")),
                        sha256_text(safe_body),
                        now,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
                action_candidate_ids: list[str] = []
                raw_action_candidates = minutes.get("actionCandidates")
                if isinstance(raw_action_candidates, list):
                    for index, raw_candidate in enumerate(raw_action_candidates[:20]):
                        candidate = _mapping(raw_candidate)
                        candidate_title = str(candidate.get("title") or "").strip()
                        if not candidate_title:
                            continue
                        candidate_receipt = canonical_json(
                            {
                                "schema": "yiyu.gc08.meeting-action-candidate.v1",
                                "clientId": project_id,
                                "meetingId": meeting_id,
                                "minutesDocumentId": document_id,
                                "minutesDocumentVersionId": document_version_id,
                                "title": candidate_title[:240],
                                "description": str(candidate.get("description") or "").strip()[:2000],
                                "dueDate": str(candidate.get("dueDate") or "").strip()[:10],
                                "ownerHint": str(candidate.get("ownerHint") or "").strip()[:120],
                                "taskWritePerformed": False,
                                "createdAt": now,
                            }
                        )
                        candidate_hash = sha256_text(candidate_receipt)
                        candidate_id = _stable_id(
                            "meeting_action_candidate",
                            identity.scope_id,
                            document_version_id,
                            index,
                            candidate_hash,
                        )
                        candidate_manifest_id = _stable_id("manifest", candidate_id)
                        connection.execute(
                            "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,"
                            "lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,"
                            "byte_size,media_type,availability_state,receipt_hash,created_at,"
                            "verified_at,deleted_at,authority_role,origin_instance_id) VALUES "
                            "(?,?,NULL,?,'active',?,'organization_cloud',?,"
                            "'meeting_action_candidate',?,'application/vnd.yiyu.ai-proposal+json',"
                            "'ready',?,?,?,NULL,'cloud',?)",
                            (
                                candidate_manifest_id,
                                identity.scope_id,
                                candidate_hash,
                                candidate_receipt,
                                identity.cloud_instance_id,
                                len(candidate_receipt.encode("utf-8")),
                                candidate_hash,
                                now,
                                now,
                                identity.cloud_instance_id,
                            ),
                        )
                        connection.execute(
                            "INSERT INTO ai_proposals (id,scope_id,answer_id,operation_kind,"
                            "payload_hash,status,payload_object_manifest_id,risk_level,expires_at,"
                            "version,lifecycle_state,created_at,updated_at,deleted_at) VALUES "
                            "(?,?,NULL,'meeting_action_candidate',?,'pending_confirmation',?,"
                            "'business_write',NULL,1,'active',?,?,NULL)",
                            (
                                candidate_id,
                                identity.scope_id,
                                candidate_hash,
                                candidate_manifest_id,
                                now,
                                now,
                            ),
                        )
                        action_candidate_ids.append(candidate_id)
                if current is None:
                    connection.execute(
                        "INSERT INTO knowledge_documents (id,scope_id,source_asset_id,"
                        "client_id,current_version,owner_membership_id,title,document_kind,"
                        "visibility_scope,parse_state,publication_state,published_at,"
                        "version,lifecycle_state,created_at,updated_at,deleted_at) "
                        "VALUES (?,?,NULL,?,?,?,?,'meeting_minutes','organization',"
                        "'ready','published',?,1,'active',?,?,NULL)",
                        (
                            document_id,
                            identity.scope_id,
                            project_id,
                            document_version,
                            identity.membership_id,
                            title,
                            now,
                            now,
                            now,
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE knowledge_documents SET current_version=?,title=?,"
                        "parse_state='ready',publication_state='published',published_at=?,"
                        "version=?,lifecycle_state='active',updated_at=?,deleted_at=NULL "
                        "WHERE scope_id=? AND id=?",
                        (
                            document_version,
                            title,
                            now,
                            aggregate_version,
                            now,
                            identity.scope_id,
                            document_id,
                        ),
                    )
                connection.execute(
                    "INSERT INTO document_versions (id,scope_id,document_id,version,"
                    "content_hash,created_at,object_manifest_id,source_asset_version,"
                    "publication_state,created_by_membership_id,origin_instance_id,"
                    "integrity_hash) VALUES (?,?,?,?,?,?,?,NULL,'published',?,?,?)",
                    (
                        document_version_id,
                        identity.scope_id,
                        document_id,
                        document_version,
                        content_hash,
                        now,
                        body_manifest_id,
                        identity.membership_id,
                        identity.cloud_instance_id,
                        content_hash,
                    ),
                )
                evidence_ids: list[str] = []
                for citation in citations:
                    evidence_id = _stable_id(
                        "meeting_evidence",
                        identity.scope_id,
                        document_version_id,
                        citation["locatorKind"],
                        citation["locator"],
                    )
                    evidence_ids.append(evidence_id)
                    connection.execute(
                        "INSERT INTO evidence_links (id,scope_id,fact_id,"
                        "source_object_id,source_version,locator,source_object_kind,"
                        "locator_kind,page_no,paragraph_no,locator_hash,created_at) "
                        "VALUES (?,?,NULL,?,?,?,?,?,?,?,?,?)",
                        (
                            evidence_id,
                            identity.scope_id,
                            transcription_id,
                            transcription_version,
                            citation["locator"],
                            "transcription_version",
                            citation["locatorKind"],
                            citation["pageNo"],
                            citation["paragraphNo"],
                            citation["locatorHash"],
                            now,
                        ),
                    )
                source_set_id = _stable_id(
                    "meeting_minutes_sources",
                    identity.scope_id,
                    document_version_id,
                )
                source_member_id = _stable_id(
                    "meeting_minutes_source_member",
                    source_set_id,
                    transcription_id,
                )
                lineage_id = _stable_id(
                    "meeting_minutes_lineage",
                    identity.scope_id,
                    document_version_id,
                )
                connection.execute(
                    "INSERT INTO source_sets (id,scope_id,client_id,"
                    "security_label_set_version,source_count,version,purpose_kind,"
                    "publication_state,created_by_principal_id,created_at,expires_at,"
                    "lifecycle_state,updated_at,deleted_at,authority_role,"
                    "origin_instance_id) VALUES (?,?,?,'gc08-formal-v1',1,1,"
                    "'meeting_minutes_evidence','published',?,?,NULL,'active',?,NULL,"
                    "'cloud',?)",
                    (
                        source_set_id,
                        identity.scope_id,
                        project_id,
                        identity.principal_id,
                        now,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO source_set_members (id,scope_id,source_set_id,"
                    "source_object_id,source_version,policy_version,source_object_kind,"
                    "ordinal,added_at,removed_at,version,lifecycle_state,created_at,"
                    "updated_at,deleted_at,authority_role,origin_instance_id) VALUES "
                    "(?,?,?,?,?,1,'transcription_version',0,?,NULL,1,'active',?,?,NULL,"
                    "'cloud',?)",
                    (
                        source_member_id,
                        identity.scope_id,
                        source_set_id,
                        transcription_id,
                        transcription_version,
                        now,
                        now,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO derivation_lineage (id,scope_id,source_set_id,"
                    "policy_version_id,grant_generation,derivative_kind,"
                    "derivative_object_id,generator_version,generated_at,"
                    "invalidated_at,source_version,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,NULL,1,'formal_meeting_minutes',?,"
                    "'meeting_minutes_agent_v1',?,NULL,?,'cloud',?)",
                    (
                        lineage_id,
                        identity.scope_id,
                        source_set_id,
                        document_version_id,
                        now,
                        transcription_version,
                        identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "UPDATE recordings SET recording_state='minutes_published',"
                    "version=version+1,updated_at=? WHERE scope_id=? AND id=?",
                    (now, identity.scope_id, recording_id),
                )
                result = {
                    "projectId": project_id,
                    "meetingId": meeting_id,
                    "recordingId": recording_id,
                    "transcriptionId": transcription_id,
                    "documentId": document_id,
                    "documentVersionId": document_version_id,
                    "version": aggregate_version,
                    "contentVersion": document_version,
                    "contentHash": content_hash,
                    "publicationState": "published",
                    "evidenceLinkIds": evidence_ids,
                    "actionCandidateIds": action_candidate_ids,
                    "publishedAt": now,
                    "safeProjection": {
                        "recordingBodyUploaded": False,
                        "fullTranscriptUploaded": False,
                        "localPathUploaded": False,
                    },
                    "idempotentReplay": False,
                }
                GC07ProjectMaterialsRepository._record_command(  # noqa: SLF001
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="meeting_minutes.published",
                    aggregate_type="knowledge_document",
                    aggregate_id=document_id,
                    aggregate_version=aggregate_version,
                    expected_aggregate_version=(expected_version or None),
                    result=result,
                    target_resource_id=document_id,
                )
                command_row = connection.execute(
                    "SELECT operation_id FROM commands WHERE scope_id=? AND idempotency_key=?",
                    (identity.scope_id, idempotency_key),
                ).fetchone()
                receipt_row = connection.execute(
                    "SELECT result_object_manifest_id FROM idempotency_records "
                    "WHERE scope_id=? AND idempotency_key=?",
                    (identity.scope_id, idempotency_key),
                ).fetchone()
                if command_row is None:
                    raise RepositoryError(
                        409,
                        "meeting_minutes_command_missing",
                        "会议纪要运行回执缺失",
                    )
                operation_id = str(command_row["operation_id"])
                bot_id = builtin_agent_id(identity.organization_id, "meeting_minutes")
                run_id = self.repository._record_id(  # noqa: SLF001
                    "run", operation_id, "meeting-minutes"
                )
                result_manifest_id = (
                    str(receipt_row["result_object_manifest_id"] or "") or None
                    if receipt_row is not None
                    else None
                )
                connection.execute(
                    """
                    INSERT INTO execution_runs (
                        id, scope_id, bot_id, rule_id, task_id, operation_id,
                        status, initiator_membership_id, proposal_id, run_kind,
                        progress_object_manifest_id, result_object_manifest_id,
                        started_at, finished_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, NULL, NULL, ?, 'completed', ?, NULL,
                              'formal_meeting_minutes', NULL, ?, ?, ?, 1,
                              'active', ?, ?, NULL)
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
                    agent_kind="meeting_minutes",
                    run_id=run_id,
                    state="completed",
                    stage="published",
                    message="会议纪要已生成正式版本并发布到项目共享知识",
                    result_version=aggregate_version,
                ).as_dict()
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
