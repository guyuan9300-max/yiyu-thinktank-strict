from __future__ import annotations

import re
import threading
from collections import defaultdict
from contextlib import nullcontext
from time import perf_counter
from typing import Any, Mapping
from urllib.parse import quote

from strict_common.ids import new_id, sha256_text, utc_now

from ..gc06_planning_local import LocalGC06PlanningProjection
from ..gc07_sources import GC07LocalProjectMaterialsRepository
from ..link_material_fetcher import fetch_link_material
from ..project_materials_local import (
    LocalProjectMaterialsRepository,
    select_relevant_excerpt,
)
from ..runtime import LocalRuntimeError
from ..ui_idempotency import replayable_cloud_mutation, replayable_generated_value
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("project_materials", pin_workspace=True)

_LinkImportThread = threading.Thread

_CLOUD_ROOT = "/api/v2/domain/project-materials"


def _segment(value: str) -> str:
    return quote(str(value), safe="")


def _publish_ready_material_summaries(
    *,
    runtime: Any,
    store: LocalProjectMaterialsRepository,
    project_id: str,
    document_ids: list[str],
    limit: int = 1,
    force: bool = False,
) -> dict[str, Any]:
    """Publish bounded safe summaries for parse-ready local files.

    One document per automatic request keeps a large project from occupying a
    local dispatch worker for minutes.  The renderer refreshes the workspace
    after each settled item and continues with the next missing summary.
    """

    visible = {
        str(item.get("id") or ""): dict(item)
        for item in store.documents(project_id)
    }
    candidates = [
        document_id
        for document_id in document_ids
        if document_id in visible
        and str(visible[document_id].get("parseStatus") or "") == "ready"
        and (
            force
            or str(visible[document_id].get("sharedSummaryState") or "")
            != "ready"
        )
    ][: max(1, int(limit))]
    results: list[dict[str, Any]] = []
    for document_id in candidates:
        try:
            local = store.document_text(document_id)
            content = str(local.get("content") or "").strip()
            if not content:
                raise LocalRuntimeError(422, "local_document_empty", "本机源文件没有可解析正文")
            reading = runtime.cloud_query(
                f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
                f"/documents/{_segment(document_id)}/reading-preview"
            )
            source_hash = str(local.get("contentHash") or "")
            if (
                not force
                and bool(reading.get("publishedSummary"))
                and str(reading.get("publishedSourceContentHash") or "")
                == source_hash
            ):
                store.update_shared_summary_state(
                    document_id,
                    state="ready",
                    source_content_hash=source_hash,
                )
                results.append({"documentId": document_id, "state": "ready", "reused": True})
                continue
            # Very long transcripts and reports used to send their first
            # 120k characters in one blocking request.  A bounded
            # beginning/middle/end evidence pack is both more representative
            # and reliably finishes within the organization-model deadline.
            if len(content) <= 30_000:
                summary_source = content
            else:
                middle = max(0, len(content) // 2 - 5_000)
                summary_source = "\n\n".join(
                    (
                        "[文件开头]\n" + content[:10_000],
                        "[文件中段]\n" + content[middle : middle + 10_000],
                        "[文件结尾]\n" + content[-10_000:],
                    )
                )
            completion = runtime.private_ai_completion(
                system_prompt=(
                    "你是项目资料摘要器。只根据当前成员设备上的文件正文生成"
                    "可供本组织共享的中文项目摘要。覆盖文件的核心主题、主体、"
                    "事实、时间、承诺、风险和待办；忽略排版噪音，不补造信息，"
                    "不输出本机路径。"
                ),
                prompt=summary_source,
                creativity_mode="strict",
                read_timeout_seconds=100.0,
                max_output_tokens=1_200,
            )
            summary = str(completion.get("content") or "").strip()
            if not summary:
                raise LocalRuntimeError(502, "local_ai_summary_empty", "组织模型没有生成可共享摘要")
            model_name = str(completion.get("modelName") or "organization_default")
            store.update_ai_summary(
                document_id,
                summary=summary,
                model_name=model_name,
            )
            published = runtime.cloud_command(
                "POST",
                f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
                f"/documents/{_segment(document_id)}/publish-local-summary",
                payload={
                    "expectedVersion": int(reading.get("aggregateVersion") or 1),
                    "sourceContentHash": source_hash,
                    "summary": summary[:4000],
                    "generatorVersion": model_name,
                },
                idempotency_key=(
                    "project-material-auto-summary:"
                    + sha256_text(f"{project_id}:{document_id}:{source_hash}")[:32]
                ),
                refresh_business=False,
            )
            store.update_shared_summary_state(
                document_id,
                state="ready",
                source_content_hash=source_hash,
            )
            results.append(
                {
                    "documentId": document_id,
                    "state": "ready",
                    "summaryDocumentId": published.get("documentId"),
                    "reused": False,
                }
            )
        except LocalRuntimeError as exc:
            state = "failed_retryable" if exc.status_code >= 500 else "blocked"
            store.update_shared_summary_state(
                document_id,
                state=state,
                message=exc.message,
            )
            results.append(
                {
                    "documentId": document_id,
                    "state": state,
                    "errorCode": exc.code,
                    "message": exc.message,
                }
            )
    return {
        "attempted": len(results),
        "ready": sum(item["state"] == "ready" for item in results),
        "failedRetryable": sum(item["state"] == "failed_retryable" for item in results),
        "blocked": sum(item["state"] == "blocked" for item in results),
        "items": results,
    }


def register_and_process_local_materials(
    *,
    runtime: Any,
    store: LocalProjectMaterialsRepository,
    project_id: str,
    local_materials: list[Mapping[str, Any]],
    relation_kind: str,
    relation_id: str,
    idempotency_key: str,
    force_processing: bool = False,
) -> dict[str, Any]:
    """Finish the same material lifecycle used by direct workbench imports.

    Recording transcription runs in a background thread.  It must not stop at
    a ``local-pending`` workbench card: safe metadata is registered in the
    organization cloud and the local body is parsed/indexed on this device.
    If the cloud is temporarily unavailable, local parsing still settles so
    the transcript remains readable and editable; metadata sync stays
    explicitly retryable instead of masquerading as a 0% running job.
    """

    materials = [dict(item) for item in local_materials]
    store.bind_pending_materials(
        project_id=project_id,
        local_materials=materials,
    )
    document_ids = [
        f"local-pending:{item['localSourceId']}"
        for item in materials
        if str(item.get("localSourceId") or "")
    ]
    cloud_state = "failed_retryable"
    cloud_error: dict[str, Any] | None = None
    try:
        registered = runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
            "/materials/register-metadata",
            payload={
                "materials": [
                    {
                        "localSourceId": item["localSourceId"],
                        "fileName": item["fileName"],
                        "contentHash": item["contentHash"],
                        "byteSize": item["byteSize"],
                        "mediaType": item["mediaType"],
                        "relationKind": relation_kind,
                        "relationId": relation_id,
                    }
                    for item in materials
                ]
            },
            idempotency_key=f"{idempotency_key}:metadata",
            refresh_business=False,
        )
        documents = [
            dict(item)
            for item in registered.get("documents") or []
            if isinstance(item, Mapping) and str(item.get("documentId") or "")
        ]
        if len(documents) != len(materials):
            raise LocalRuntimeError(
                502,
                "project_material_metadata_invalid",
                "组织云资料元数据回执不完整",
            )
        store.bind_cloud_documents(
            project_id=project_id,
            local_materials=materials,
            cloud_documents=documents,
        )
        document_ids = [str(item["documentId"]) for item in documents]
        cloud_state = "ready"
    except LocalRuntimeError as exc:
        cloud_error = {
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.status_code >= 500 or exc.status_code in {408, 409, 429},
        }

    processing = store.process_pending_documents(
        project_id=project_id,
        document_ids=document_ids,
        force=force_processing,
    )
    shared_summaries = _publish_ready_material_summaries(
        runtime=runtime,
        store=store,
        project_id=project_id,
        document_ids=[
            str(item.get("documentId") or "")
            for item in processing.get("items") or []
            if str(item.get("documentId") or "")
        ],
        force=force_processing,
    )
    return {
        "documentIds": document_ids,
        "cloudMetadataState": cloud_state,
        "cloudError": cloud_error,
        "processing": processing,
        "sharedSummaries": shared_summaries,
        "overallState": (
            "ready"
            if cloud_state == "ready"
            and all(
                str(item.get("parseStatus") or "") == "ready"
                for item in processing.get("items") or []
            )
            else "failed_retryable"
        ),
    }


def _client(project: Mapping[str, Any]) -> dict[str, Any]:
    project_id = str(project.get("projectId") or "")
    lifecycle = str(project.get("lifecycleState") or "active")
    authorization_projection = project.get("authorizationProjection")
    if not isinstance(authorization_projection, Mapping):
        authorization_projection = {}
    viewer_capabilities = [
        str(capability)
        for capability in authorization_projection.get("viewerCapabilities") or []
        if str(capability or "").strip()
    ]
    return {
        "id": project_id,
        "name": project.get("name") or "未命名项目",
        "alias": project.get("alias") or project.get("name") or "",
        "domain": project.get("domain") or "",
        "type": "project",
        "intro": project.get("summary") or "",
        "stage": lifecycle,
        "color": project.get("color") or "#5B7BFE",
        "folderCount": 0,
        "documentCount": int(project.get("documentCount") or 0),
        "taskCount": int(project.get("taskCount") or 0),
        "lastActivityAt": project.get("updatedAt"),
        "relatedUserIds": [
            str(membership_id)
            for membership_id in project.get("participantMembershipIds") or []
            if str(membership_id)
            != str(project.get("ownerMembershipId") or "")
        ],
        "relatedUsers": [
            {
                "id": str(member.get("id") or ""),
                "fullName": str(member.get("fullName") or "未命名成员"),
                "email": str(member.get("email") or ""),
                "primaryRole": str(member.get("primaryRole") or "employee"),
                "isSelf": bool(member.get("isSelf")),
            }
            for member in project.get("participantMembers") or []
            if str(member.get("id") or "")
        ],
        "ownerMembershipId": project.get("ownerMembershipId"),
        "managerNames": [
            str(name)
            for name in project.get("managerNames") or []
            if str(name or "").strip()
        ],
        "sharedMemberCount": int(project.get("sharedMemberCount") or 0),
        "isDataCenterIncluded": True,
        "isDefaultInternalProject": bool(
            project.get("isDefaultInternalProject")
        ),
        "isFrozen": lifecycle == "frozen",
        "frozenAt": project.get("updatedAt") if lifecycle == "frozen" else None,
        "syncStatus": "synced",
        "cloudId": project_id,
        "lastSyncError": None,
        "_strictVersion": int(project.get("version") or 1),
        "folderCapabilityState": project.get("folderState") or "not_connected",
        "officialWebsiteUrl": project.get("officialWebsiteUrl"),
        "viewerCapabilities": viewer_capabilities,
    }


def _project_payload(body: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    field_map = {
        "name": "name",
        "alias": "alias",
        "intro": "summary",
        "domain": "domain",
        "color": "color",
        "relatedUserIds": "participantMembershipIds",
        "officialWebsiteUrl": "officialWebsiteUrl",
    }
    for source, target in field_map.items():
        if source in body:
            payload[target] = body.get(source)
    return payload


def _project_detail(compatibility: Any, project_id: str) -> dict[str, Any]:
    return compatibility.runtime.require_project_capability(project_id, "read")


def _require_project_read(compatibility: Any, project_id: str) -> dict[str, Any]:
    return compatibility.runtime.require_project_capability(project_id, "read")


def _local_store(compatibility: Any) -> LocalProjectMaterialsRepository:
    return GC07LocalProjectMaterialsRepository(compatibility.runtime)


def _platform_resource(
    compatibility: Any,
    *,
    resource_path: str,
    authorization_scope: str,
) -> dict[str, Any]:
    result = compatibility.runtime.cloud_query(
        "/api/v2/platform-integrations/query",
        query={
            "resourcePath": resource_path,
            "authorizationScope": authorization_scope,
        },
    )
    resource = result.get("resource")
    if not isinstance(resource, dict):
        raise LocalRuntimeError(
            502,
            "platform_resource_invalid",
            "组织云飞书能力查询返回了无效资源",
        )
    return resource


def _platform_command(
    compatibility: Any,
    *,
    resource_path: str,
    authorization_scope: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    result = compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/platform-integrations/command",
        payload={
            "resourcePath": resource_path,
            "authorizationScope": authorization_scope,
            "method": "POST",
            "query": {},
            "payload": dict(payload),
        },
        idempotency_key=idempotency_key,
        refresh_business=False,
    )
    command_result = result.get("result")
    if not isinstance(command_result, dict):
        raise LocalRuntimeError(
            502,
            "platform_command_result_invalid",
            "组织云飞书能力命令返回了无效结果",
        )
    return command_result


def _client_with_local_folders(
    compatibility: Any,
    project: Mapping[str, Any],
) -> dict[str, Any]:
    client = _client(project)
    if not hasattr(compatibility.runtime, "database_path"):
        return client
    store = _local_store(compatibility)
    _local_call(lambda: store.ensure_project_projection(project))
    folders = store.folders(client["id"])
    return {
        **client,
        "folderCount": len(folders),
        "folderCapabilityState": "ready",
    }


def _local_call(operation: Any) -> Any:
    return operation()


def _transition(
    compatibility: Any,
    request: UiRequest,
    project_id: str,
    target_state: str,
) -> dict[str, Any]:
    project = _project_detail(compatibility, project_id)
    result = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/lifecycle",
        payload={
            "expectedVersion": int(project.get("version") or 1),
            "targetState": target_state,
        },
        idempotency_key=request.idempotency_key,
    )
    return _client_with_local_folders(
        compatibility,
        result.get("project") or {},
    )


@router.get(r"clients")
def list_clients(compatibility: Any, _: UiRequest, __: Any) -> list[dict[str, Any]]:
    result = compatibility.runtime.cloud_query(f"{_CLOUD_ROOT}/projects")
    projects = [
        _client_with_local_folders(compatibility, item)
        for item in result.get("projects") or []
    ]
    reconcile = getattr(compatibility.runtime, "reconcile_project_projections", None)
    if callable(reconcile):
        reconcile([str(item.get("projectId") or "") for item in result.get("projects") or []])
    return projects


@router.post(r"clients")
def create_client(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    result = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects",
        payload=_project_payload(request.body),
        idempotency_key=request.idempotency_key,
    )
    return _client_with_local_folders(
        compatibility,
        result.get("project") or {},
    )


@router.get(r"clients/(?P<project_id>[^/]+)/workspace")
def client_workspace(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    project = _project_detail(compatibility, project_id)
    store = _local_store(compatibility)
    _local_call(lambda: store.ensure_project_projection(project))
    documents = _local_call(lambda: store.documents(project_id))
    folders = _local_call(lambda: store.folders(project_id))
    # Meetings are formal cloud-authoritative GC-06/GC-08 objects.  Never
    # merge the retained local-project-state meeting JSON into this view.
    meeting_projection = LocalGC06PlanningProjection(compatibility.runtime)
    try:
        strict_meeting_rows = compatibility.runtime.cloud_query(
            "/api/v2/gc06/meetings",
            query={"clientId": project_id},
        )
        meeting_projection.apply_meetings(strict_meeting_rows)
    except LocalRuntimeError:
        strict_meeting_rows = meeting_projection.list_meetings(
            client_id=project_id
        )
    meetings = [
        {
            "id": row.get("id"),
            "clientId": project_id,
            "title": row.get("title") or "客户会议",
            "stage": (
                "published" if row.get("status") == "completed" else "prepared"
            ),
            "scheduledAt": row.get("startsAt"),
            "updatedAt": row.get("updatedAt"),
            "sourceScope": "strict_meeting_projection",
            "version": int(row.get("version") or 1),
        }
        for row in strict_meeting_rows
        if isinstance(row, Mapping)
        and str(row.get("clientId") or "") == project_id
        and str(row.get("lifecycleState") or "active") != "deleted"
    ]
    pending_jobs = sum(
        str(item.get("parseStatus") or "") in {"queued", "processing"}
        or (
            str(item.get("parseStatus") or "") == "ready"
            and str(item.get("wikiStatus") or "") in {"queued", "processing"}
        )
        for item in documents
    )
    failed_jobs = sum(
        str(item.get("parseStatus") or "") == "failed_retryable"
        for item in documents
    )
    ready_documents = sum(
        str(item.get("parseStatus") or "") == "ready"
        for item in documents
    )
    local_wiki = _local_call(lambda: store.local_wiki_status(project_id))
    knowledge_presentation = _local_call(
        lambda: store.knowledge_presentation(project_id)
    )
    memory_cards = [
        {
            "id": item.get("id"),
            "clientId": project_id,
            "sourceType": item.get("memoryKind") or "explicit_memory",
            "title": item.get("title") or "已存记忆",
            "folderCategory": (
                "工作台收藏"
                if item.get("memoryKind") == "favorite"
                else "明确记住"
            ),
            "surrogateMdPath": "",
            "overviewSummary": item.get("summary") or "",
            "retrievalSummary": "当前设备 · 当前成员 · 当前项目",
            "documentRole": "本机严格记忆",
            "sourceLinks": [
                {
                    "targetType": "ai_answer",
                    "targetId": item.get("sourceAnswerId"),
                }
            ],
            "createdAt": item.get("updatedAt"),
            "updatedAt": item.get("updatedAt"),
            "chatMessageId": item.get("sourceAnswerId"),
            "storageKind": "local_answer_memory",
            "localFileCreated": False,
            "memoryKind": item.get("memoryKind"),
            "version": item.get("version") or 1,
            "status": "active",
        }
        for item in knowledge_presentation.get("savedMemories") or []
    ]
    answer_loader = getattr(compatibility.runtime, "workbench_project_answers", None)
    answers = answer_loader(project_id) if callable(answer_loader) else []
    from .workbench_outputs import _analysis_run, _chat_messages

    recent_messages = [
        message
        for answer in answers
        for message in _chat_messages(answer)
    ]
    thread_rows: dict[str, list[dict[str, Any]]] = {}
    for answer in answers:
        thread_id = str(answer.get("threadId") or answer.get("answerId") or "")
        if thread_id:
            thread_rows.setdefault(thread_id, []).append(answer)
    threads = [
        {
            "id": thread_id,
            "clientId": project_id,
            "title": rows[0].get("question") or "工作台问答",
            "createdAt": rows[0].get("createdAt"),
            "updatedAt": rows[-1].get("updatedAt") or rows[-1].get("createdAt"),
        }
        for thread_id, rows in thread_rows.items()
    ]
    return {
        "client": {
            **_client(project),
            "folderCount": len(folders),
            "documentCount": len(documents),
            "folderCapabilityState": "ready",
        },
        "folders": folders,
        "documents": documents,
        "documentCards": [],
        "imports": [],
        "knowledgeStatus": {
            "totalDocuments": len(documents),
            "totalChunks": int(local_wiki.get("chunkCount") or 0),
            "vectorizedDocuments": int(
                local_wiki.get("vectorReadyCount") or 0
            ),
            "dedupedDocuments": 0,
            "reviewPendingDocuments": 0,
            "surrogateCount": 0,
            "memoryDocCount": len(memory_cards),
            "masterIndexCount": int(
                local_wiki.get("searchReadyCount") or 0
            ),
            "reclassifiedDocumentCount": 0,
            "qdrantReady": False,
            "lastUpdatedAt": local_wiki.get("updatedAt"),
            "pendingJobs": pending_jobs,
            "runningJobs": 0,
            "lastJobStatus": (
                "failed"
                if failed_jobs
                else "queued"
                if pending_jobs
                else "completed"
                if documents
                else "idle"
            ),
            "lastJobError": (
                "部分本机资料解析失败，可以重试" if failed_jobs else None
            ),
            "lastSuccessfulRunAt": None,
            "embeddingMode": (
                "not_connected"
                if int(local_wiki.get("vectorReadyCount") or 0) == 0
                else "local_sparse_vector"
            ),
            "embeddingModel": (
                LocalProjectMaterialsRepository.WIKI_SPARSE_VECTOR_MODEL
                if int(local_wiki.get("vectorReadyCount") or 0) > 0
                else None
            ),
            "parsedDocuments": ready_documents,
            "blockedDocuments": sum(
                str(item.get("parseStatus") or "") == "blocked"
                for item in documents
            ),
        },
        "knowledgeJobs": [],
        "recentReclassEvents": [],
        "surrogateCount": 0,
        "memoryDocCount": len(memory_cards),
        "memoryCards": memory_cards,
        "threads": threads,
        "recentMessages": recent_messages,
        # Ready answers are already represented by recentMessages.  Returning
        # the same full answer bodies again as completed analysis runs doubled
        # large workspace payloads and was not used for active progress.
        "analysisRuns": [],
        "meetings": meetings,
        "goals": [],
        "dnaModules": [],
        "projectModules": [],
        "projectFlows": [],
        "dnaTerms": [],
        "relatedTasks": [],
        "latestJudgments": [],
        "latestTopics": [],
        "latestConflicts": [],
        "latestOpenQuestions": [],
        "latestRunLogs": [],
        "knowledgeContext": None,
        "strictAuthority": {
            "answers": "ai_answers",
            "memories": "knowledge_documents/document_versions/derivation_lineage",
            "documents": "knowledge_documents/document_versions",
        },
    }


@router.post(r"clients/(?P<project_id>[^/]+)/materials/process-pending")
def process_pending_materials(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    _require_project_read(compatibility, match.group("project_id"))
    store = _local_store(compatibility)
    processing = _local_call(
        lambda: store.process_pending_documents(
            project_id=match.group("project_id"),
            document_ids=request.body.get("documentIds") or [],
            force=bool(request.body.get("force")),
        )
    )
    shared_summaries = _local_call(
        lambda: _publish_ready_material_summaries(
            runtime=compatibility.runtime,
            store=store,
            project_id=match.group("project_id"),
            document_ids=[
                str(item.get("documentId") or "")
                for item in processing.get("items") or []
                if str(item.get("documentId") or "")
            ],
            force=bool(request.body.get("force")),
        )
    )
    return {**processing, "sharedSummaries": shared_summaries}


@router.get(r"clients/(?P<project_id>[^/]+)/knowledge/progress")
def local_knowledge_progress(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    store = _local_store(compatibility)
    documents = _local_call(lambda: store.documents(project_id))
    local_wiki = _local_call(lambda: store.local_wiki_status(project_id))
    pending = [
        item
        for item in documents
        if str(item.get("parseStatus") or "")
        in {"queued", "processing"}
        or (
            str(item.get("parseStatus") or "") == "ready"
            and str(item.get("wikiStatus") or "")
            in {"queued", "processing"}
        )
    ]
    running = [
        item
        for item in documents
        if str(item.get("parseStatus") or "") == "processing"
        or str(item.get("wikiStatus") or "") == "processing"
    ]
    blocked = [
        item
        for item in documents
        if str(item.get("parseStatus") or "") == "blocked"
    ]
    failed = [
        item
        for item in documents
        if str(item.get("parseStatus") or "") == "failed_retryable"
        or str(item.get("wikiStatus") or "") == "failed_retryable"
    ]
    ready = [
        item
        for item in documents
        if str(item.get("parseStatus") or "") == "ready"
        and str(item.get("wikiStatus") or "") == "ready"
    ]
    # Every forced batch is queued with the same authoritative attempt
    # started_at.  Group by that receipt field so the renderer sees one real
    # X/N job instead of N unrelated 0/1 jobs (or a shrinking denominator).
    active_batch_started_at = max(
        (
            str(item.get("processingBatchStartedAt") or "")
            for item in pending
            if item.get("processingBatchStartedAt")
        ),
        default="",
    )
    batch_documents = (
        [
            item
            for item in documents
            if str(item.get("processingBatchStartedAt") or "")
            == active_batch_started_at
        ]
        if active_batch_started_at
        else []
    )
    batch_active = [
        item
        for item in batch_documents
        if str(item.get("parseStatus") or "") in {"queued", "processing"}
        or (
            str(item.get("parseStatus") or "") == "ready"
            and str(item.get("wikiStatus") or "")
            in {"queued", "processing"}
        )
    ]
    batch_running = [
        item
        for item in batch_active
        if str(item.get("parseStatus") or "") == "processing"
        or str(item.get("wikiStatus") or "") == "processing"
    ]
    batch_current = (
        batch_running[0]
        if batch_running
        else batch_active[0]
        if batch_active
        else None
    )
    batch_processed = max(0, len(batch_documents) - len(batch_active))
    batch_is_active = bool(
        active_batch_started_at
        and store.is_processing_batch_active(
            project_id=project_id,
            batch_started_at=active_batch_started_at,
        )
    )
    jobs = (
        [
            {
                "id": f"local-material-batch:{active_batch_started_at}",
                "clientId": project_id,
                "jobType": "local_material_reprocessing",
                "status": (
                    "running"
                    if batch_is_active and batch_running
                    else "queued"
                    if batch_is_active
                    else "interrupted"
                ),
                "totalItems": len(batch_documents),
                "processedItems": batch_processed,
                "lastError": None,
                "currentItemLabel": (batch_current or {}).get("title"),
                "lastEventMessage": (
                    f"正在重新解析 {batch_processed + 1}/{len(batch_documents)}"
                    if batch_current
                    else None
                ),
                "recentEvents": [],
                "queuedItemLabels": [
                    item.get("title") for item in batch_active if item.get("title")
                ],
                "resumeDocumentIds": [
                    str(item.get("id") or "")
                    for item in batch_active
                    if item.get("id")
                ],
                "createdAt": active_batch_started_at,
                "startedAt": active_batch_started_at,
                "finishedAt": None,
                "updatedAt": (batch_current or {}).get("processedAt")
                or active_batch_started_at,
            }
        ]
        if batch_documents and batch_active
        else []
    )
    return {
        "knowledgeStatus": {
            "totalDocuments": len(documents),
            "totalChunks": int(local_wiki.get("chunkCount") or 0),
            "ocrReadyRate": round(len(ready) * 100 / max(1, len(documents)), 1),
            "vectorizedDocuments": int(
                local_wiki.get("vectorReadyCount") or 0
            ),
            "dedupedDocuments": 0,
            "reviewPendingDocuments": len(blocked),
            "surrogateCount": 0,
            "memoryDocCount": 0,
            "masterIndexCount": int(
                local_wiki.get("searchReadyCount") or 0
            ),
            "reclassifiedDocumentCount": 0,
            "qdrantReady": False,
            "lastUpdatedAt": local_wiki.get("updatedAt"),
            "pendingJobs": len(pending),
            "runningJobs": len(running),
            "lastJobStatus": (
                "failed"
                if failed
                else "interrupted"
                if jobs and jobs[0].get("status") == "interrupted"
                else "running"
                if pending
                else "completed"
                if ready
                else "blocked"
                if blocked
                else "idle"
            ),
            "lastJobError": (
                failed[0].get("processingMessage") if failed else None
            ),
            "lastSuccessfulRunAt": local_wiki.get("updatedAt"),
            "embeddingMode": (
                "local_sparse_vector"
                if int(local_wiki.get("vectorReadyCount") or 0) > 0
                else "not_connected"
            ),
            "embeddingModel": (
                LocalProjectMaterialsRepository.WIKI_SPARSE_VECTOR_MODEL
                if int(local_wiki.get("vectorReadyCount") or 0) > 0
                else None
            ),
            "embeddingError": None,
            "embeddingProvider": "current_device",
            "embeddingDimension": (
                LocalProjectMaterialsRepository.WIKI_SPARSE_VECTOR_DIMENSIONS
                if int(local_wiki.get("vectorReadyCount") or 0) > 0
                else None
            ),
            "embeddingSignature": (
                LocalProjectMaterialsRepository.WIKI_RETRIEVAL_GENERATOR_VERSION
                if int(local_wiki.get("vectorReadyCount") or 0) > 0
                else None
            ),
            "activeVectorCollection": "local_private_wiki",
            "vectorIndexStatus": (
                "ready"
                if int(local_wiki.get("vectorReadyCount") or 0) > 0
                else "not_connected"
            ),
            "routerEnabled": int(local_wiki.get("searchReadyCount") or 0) > 0,
            "routerModel": None,
            "parsedDocuments": len(ready),
            "blockedDocuments": len(blocked),
        },
        "knowledgeJobs": jobs,
        "strictState": "ready",
    }


_KNOWLEDGE_SOURCE_GROUPS = (
    ("local_original", "本地原件", "current_device"),
    ("organization_knowledge", "组织知识", "organization_cloud"),
    ("official_website", "官网事实", "organization_cloud"),
    ("explicit_memory", "明确记忆", "organization_cloud"),
    ("favorite", "收藏", "current_member"),
    ("system_inference", "系统推断", "derived_projection"),
)


def _cloud_search_hit(
    item: Mapping[str, Any],
    *,
    source_type: str,
    terms: list[str],
) -> dict[str, Any] | None:
    summary = str(item.get("summary") or "").strip()
    title = str(
        item.get("sourceDescription")
        or item.get("title")
        or item.get("sourceId")
        or "组织知识"
    )
    searchable = f"{title}\n{summary}".casefold()
    matched = [term for term in terms if term in searchable]
    if terms and not matched:
        return None
    base_score = {
        "explicit_memory": 5.2,
        "favorite": 4.8,
        "official_website": 4.2,
        "organization_knowledge": 3.8,
        "system_inference": 1.6,
    }.get(source_type, 2.0)
    return {
        "title": title,
        "excerpt": summary,
        "score": round(base_score + len(matched) / max(1, len(terms)), 6),
        "keywordScore": len(matched) / max(1, len(terms)),
        "semanticScore": 0,
        "stage": "master_index",
        "sourceType": source_type,
        "documentId": item.get("sourceId") or item.get("id"),
        "knowledgeDocumentId": item.get("sourceId") or item.get("id"),
        "documentVersionId": item.get("documentVersionId"),
        "path": None,
        "originalPath": None,
        "sourceAvailability": "machine_readable_only",
        "originalAvailable": False,
        "machineReadableAvailable": bool(summary),
        "openableKind": "system_card",
        "sectionLabel": title,
        "matchedTerms": matched,
        "retrievalMode": "keyword",
        "citationRole": (
            "direct_support"
            if source_type in {"explicit_memory", "official_website"}
            else "background"
        ),
        "citationPriority": {
            "explicit_memory": 95,
            "favorite": 90,
            "official_website": 85,
            "organization_knowledge": 70,
            "system_inference": 30,
        }.get(source_type, 50),
        "citationReason": {
            "explicit_memory": "成员明确确认并立即生效的项目正式知识",
            "favorite": "当前成员在本项目收藏的高权重记忆",
            "official_website": "已登记官网来源形成的可追溯事实",
            "organization_knowledge": "组织已发布的项目知识",
            "system_inference": "可撤回的系统推断，不是正式事实",
        }.get(source_type, "项目知识来源"),
    }


def _source_groups(
    *,
    hits: list[Mapping[str, Any]],
    organization_state: str,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for key, label, authority in _KNOWLEDGE_SOURCE_GROUPS:
        aliases = {
            "local_original": {"local_original", "local_document"},
            "organization_knowledge": {
                "organization_knowledge",
                "organization_summary",
            },
        }.get(key, {key})
        count = sum(
            str(item.get("sourceType") or "") in aliases for item in hits
        )
        if key in {"organization_knowledge", "official_website"}:
            state = organization_state
            message = (
                None
                if state == "ready"
                else "组织云知识暂不可用，本机原件仍可检索"
            )
        elif key == "system_inference" and count == 0:
            state = "not_connected"
            message = "系统推断将在记忆生命周期环节接通"
        else:
            state = "ready"
            message = None
        groups.append(
            {
                "key": key,
                "label": label,
                "authority": authority,
                "state": state,
                "count": count,
                "message": message,
            }
        )
    return groups


def _knowledge_presentation(
    compatibility: Any,
    project_id: str,
) -> dict[str, Any]:
    store = _local_store(compatibility)
    local = _local_call(lambda: store.knowledge_presentation(project_id))
    organization_state = "ready"
    organization_message: str | None = None
    cloud: dict[str, Any] = {}
    try:
        cloud = compatibility.runtime.project_knowledge_context(project_id)
        organization_state = str(cloud.get("state") or "ready")
    except Exception as exc:
        organization_state = (
            "not_connected"
            if isinstance(exc, LocalRuntimeError) and exc.status_code == 501
            else "failed_retryable"
        )
        organization_message = (
            exc.message
            if isinstance(exc, LocalRuntimeError)
            else "组织共享知识暂时无法加载"
        )
    local_memories = list(local.get("savedMemories") or [])
    cloud_memories = [
        {
            "id": str(item.get("sourceId") or ""),
            "documentVersionId": item.get("documentVersionId"),
            "title": item.get("sourceDescription") or "已同步记忆",
            "summary": item.get("summary") or "",
            "memoryKind": item.get("memoryKind") or "explicit_memory",
            "sourceAnswerId": item.get("sourceAnswerId"),
            "contentHash": item.get("contentHash") or "",
            "publicationState": "published",
            "updatedAt": item.get("updatedAt"),
            "authority": item.get("authority") or "organization_cloud",
        }
        for item in (cloud.get("savedMemories") or [])
    ]
    memory_by_id: dict[str, dict[str, Any]] = {}
    for item in [*local_memories, *cloud_memories]:
        memory_id = str(item.get("id") or "")
        if memory_id and memory_id not in memory_by_id:
            memory_by_id[memory_id] = dict(item)
    memories = list(memory_by_id.values())
    source_counts = {
        "local_original": int(local.get("localOriginalCount") or 0),
        "organization_knowledge": len(cloud.get("organizationSharedKnowledge") or []),
        "official_website": len(cloud.get("officialWebsiteFacts") or []),
        "explicit_memory": sum(
            str(item.get("memoryKind") or "") == "explicit_memory"
            for item in memories
        ),
        "favorite": sum(
            str(item.get("memoryKind") or "") == "favorite"
            for item in memories
        ),
        "system_inference": sum(
            str(item.get("memoryKind") or "") == "system_inference"
            for item in memories
        ),
    }
    groups: list[dict[str, Any]] = []
    for key, label, authority in _KNOWLEDGE_SOURCE_GROUPS:
        count = int(source_counts[key])
        if key in {"organization_knowledge", "official_website"}:
            state = organization_state
            message = organization_message
        elif key == "system_inference" and count == 0:
            state = "not_connected"
            message = "系统推断将在记忆生命周期环节接通"
        else:
            state = "ready"
            message = None
        groups.append(
            {
                "key": key,
                "label": label,
                "authority": authority,
                "state": state,
                "count": count,
                "message": message,
            }
        )
    return {
        "clientId": project_id,
        "sourceGroups": groups,
        "savedMemories": memories,
        "relationshipCards": [
            *(local.get("relationshipCards") or []),
            *(cloud.get("relationshipCards") or []),
        ],
        "organizationSharedState": organization_state,
        "organizationSharedMessage": organization_message,
        "updatedAt": utc_now(),
    }


@router.get(r"clients/(?P<project_id>[^/]+)/knowledge/presentation")
def get_knowledge_presentation(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    return _knowledge_presentation(compatibility, project_id)


@router.post(r"clients/(?P<project_id>[^/]+)/knowledge/search")
def search_local_and_shared_knowledge(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    query = str(request.body.get("prompt") or "").strip()
    store = _local_store(compatibility)
    result = _local_call(
        lambda: store.search_local_wiki(
            project_id=project_id,
            query=query,
            limit=int(request.body.get("limit") or 20),
        )
    )
    local_presentation = _local_call(lambda: store.knowledge_presentation(project_id))
    saved_memories = list(local_presentation.get("savedMemories") or [])
    relationship_cards = list(local_presentation.get("relationshipCards") or [])
    try:
        context = compatibility.runtime.project_knowledge_context(project_id)
        organization_materials = list(
            context.get("organizationSharedKnowledge") or []
        )
        website_facts = list(context.get("officialWebsiteFacts") or [])
        cloud_memories = list(context.get("savedMemories") or [])
        terms = [
            item.group(0).casefold()
            for item in re.finditer(
                r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,24}",
                query,
            )
        ]
        shared_hits = [
            hit
            for source_type, items in (
                ("organization_knowledge", organization_materials),
                ("official_website", website_facts),
                (
                    "explicit_memory",
                    [
                        item
                        for item in [*saved_memories, *cloud_memories]
                        if str(item.get("memoryKind") or "") == "explicit_memory"
                    ],
                ),
                (
                    "favorite",
                    [
                        item
                        for item in [*saved_memories, *cloud_memories]
                        if str(item.get("memoryKind") or "") == "favorite"
                    ],
                ),
                (
                    "system_inference",
                    [
                        item
                        for item in [*saved_memories, *cloud_memories]
                        if str(item.get("memoryKind") or "") == "system_inference"
                    ],
                ),
            )
            for item in items
            for hit in [_cloud_search_hit(item, source_type=source_type, terms=terms)]
            if hit is not None
        ]
        result["hits"] = sorted(
            [*(result.get("hits") or []), *shared_hits],
            key=lambda item: -float(item.get("score") or 0),
        )[:50]
        result["masterHitCount"] = len(shared_hits)
        result["organizationSharedState"] = str(
            context.get("state") or "ready"
        )
        result["organizationSharedMessage"] = None
        saved_memories = [*saved_memories, *cloud_memories]
        relationship_cards = [
            *relationship_cards,
            *(context.get("relationshipCards") or []),
        ]
    except Exception as exc:
        result["organizationSharedState"] = (
            "not_connected"
            if isinstance(exc, LocalRuntimeError) and exc.status_code == 501
            else "failed_retryable"
        )
        result["organizationSharedMessage"] = (
            "组织共享知识将在下一环节接入来源分栏；本机资料搜索不受影响"
            if isinstance(exc, LocalRuntimeError) and exc.status_code == 501
            else exc.message
            if isinstance(exc, LocalRuntimeError)
            else "组织共享知识暂时无法加载，本机资料搜索仍可使用"
        )
    result["rawChunkHitCount"] = sum(
        item.get("stage") == "raw_chunk" for item in result.get("hits") or []
    )
    result["previewSummary"] = "；".join(
        str(item.get("excerpt") or "")[:120]
        for item in (result.get("hits") or [])[:3]
    )
    result["sourceGroups"] = _source_groups(
        hits=list(result.get("hits") or []),
        organization_state=str(result.get("organizationSharedState") or "ready"),
    )
    result["savedMemories"] = saved_memories
    result["relationshipCards"] = relationship_cards
    return result


@router.post(
    r"clients/(?P<project_id>[^/]+)/documents/"
    r"(?P<document_id>[^/]+)/retry-processing"
)
def retry_material_processing(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    _require_project_read(compatibility, match.group("project_id"))
    return _local_call(
        lambda: _local_store(compatibility).retry_document_processing(
            project_id=match.group("project_id"),
            document_id=match.group("document_id"),
        )
    )


@router.put(r"clients/(?P<project_id>[^/]+)")
def update_client(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    expected_version = int(request.body.get("expectedVersion") or 0)
    if expected_version < 1:
        raise LocalRuntimeError(
            422,
            "project_version_required",
            "项目版本信息缺失，请刷新后重试",
        )
    result = compatibility.runtime.cloud_command(
        "PUT",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}",
        payload={
            **_project_payload(request.body),
            "expectedVersion": expected_version,
        },
        idempotency_key=request.idempotency_key,
    )
    return _client_with_local_folders(
        compatibility,
        result.get("project") or {},
    )


@router.delete(r"clients/(?P<project_id>[^/]+)")
def archive_client(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    project = _project_detail(compatibility, project_id)
    authorization = project.get("authorizationProjection")
    if not isinstance(authorization, Mapping):
        authorization = {}
    if str(project.get("ownerMembershipId") or "") != str(
        authorization.get("viewerMembershipId") or ""
    ):
        raise LocalRuntimeError(
            403,
            "project_delete_creator_required",
            "只有项目创建者可以删除项目",
        )
    project = _transition(
        compatibility,
        request,
        project_id,
        "deleted",
    )
    return {
        "deleted": True,
        "id": project_id,
        "lifecycleState": project["stage"],
    }


@router.get(r"clients/(?P<project_id>[^/]+)/delete-preview")
def client_delete_preview(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    result = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/delete-preview"
    )
    return {
        "clientId": project_id,
        "name": result.get("name") or "",
        "threadCount": 0,
        "messageCount": 0,
        "documentCount": int(result.get("documentCount") or 0),
        "dnaCount": 0,
        "goalCount": 0,
        "meetingCount": 0,
        "eventLineCount": int(result.get("eventLineCount") or 0),
        "taskCount": int(result.get("taskCount") or 0),
        "isDemoClient": False,
        "narrativeCount": int(result.get("narrativeCount") or 0),
        "unavailableLegacyCounts": list(
            result.get("unavailableLegacyCounts") or []
        ),
        "_strictVersion": int(result.get("version") or 1),
    }


@router.get(r"clients/(?P<project_id>[^/]+)/knowledge-context")
def knowledge_context(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return compatibility.runtime.project_knowledge_context(
        match.group("project_id")
    )


@router.get(r"clients/(?P<project_id>[^/]+)/knowledge-status")
def knowledge_status(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    return compatibility.runtime.cloud_query(
        f"/api/v2/workbench/projects/{_segment(project_id)}/knowledge-status"
    )


@router.get(r"clients/(?P<project_id>[^/]+)/fact-bundle")
def fact_bundle(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    project = _project_detail(compatibility, project_id)
    context = compatibility.runtime.project_knowledge_context(project_id)
    task_result = compatibility.runtime.cloud_query("/api/v2/domain/tasks")
    event_lines = compatibility.runtime.cloud_query(
        "/api/v2/gc06/event-lines", query={"clientId": project_id}
    )
    tasks = [
        item
        for item in task_result.get("tasks") or []
        if str(item.get("client_id") or item.get("clientId") or "") == project_id
    ]
    shared = list(context.get("organizationSharedKnowledge") or [])
    website = list(context.get("officialWebsiteFacts") or [])
    memories = list(context.get("savedMemories") or [])
    lite = str(request.query.get("lite") or "").lower() in {"1", "true", "yes"}
    client = _client(project)
    event_facts = [
        {
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or item.get("title") or ""),
            "kind": str(item.get("lineKind") or item.get("line_kind") or "project"),
            "status": str(item.get("lifecycleState") or item.get("lifecycle_state") or "active"),
            "stage": str(item.get("stage") or "active"),
            "summary": str(item.get("summary") or ""),
            "intent": str(item.get("intent") or ""),
            "current_blocker": str(item.get("currentBlocker") or ""),
            "recent_decision": str(item.get("recentDecision") or ""),
            "next_step": str(item.get("nextStep") or ""),
            "evidence_count": int(item.get("evidenceCount") or 0),
            "owner_id": item.get("ownerMembershipId"),
            "owner_name": item.get("ownerName"),
            "primary_client_id": project_id,
            "primary_client_name": str(project.get("name") or ""),
            "created_at": str(item.get("createdAt") or ""),
            "updated_at": str(item.get("updatedAt") or ""),
        }
        for item in event_lines
    ]
    task_facts = [
        {
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or ""),
            "description_preview": str(item.get("description") or "")[:300],
            "status": "done" if item.get("completed_at") else "todo",
            "priority": str(item.get("priority") or "normal"),
            "progress_status": "completed" if item.get("completed_at") else "active",
            "owner_id": item.get("owner_membership_id"),
            "owner_name": str(item.get("owner_name") or ""),
            "creator_id": str(item.get("created_by_membership_id") or ""),
            "deadline_at": item.get("due_date"),
            "due_date": item.get("due_date"),
            "scheduled_start_at": item.get("scheduled_start_at"),
            "completed_at": item.get("completed_at"),
            "event_line_id": item.get("event_line_id"),
            "business_category": None,
            "current_blocker": "",
            "next_action": "",
            "recent_decision": "",
            "evidence_count": 0,
            "source_type": str(item.get("source_type") or "manual"),
            "source_id": item.get("source_id"),
            "created_at": str(item.get("created_at") or ""),
            "updated_at": str(item.get("updated_at") or ""),
        }
        for item in tasks
    ]
    dna_documents = [
        {
            "module_key": str(item.get("sourceKind") or "organization_knowledge"),
            "title": str(item.get("sourceDescription") or "组织知识"),
            "summary": str(item.get("summary") or ""),
            "file_name": "",
            "source_kind": str(item.get("sourceKind") or "organization_knowledge"),
            "updated_at": str(item.get("updatedAt") or ""),
            "updated_by": "organization_cloud",
            "has_full_content": False,
        }
        for item in [*shared, *memories]
    ]
    atomic_facts = [
        {
            "id": str(item.get("sourceId") or ""),
            "subject_text": str(project.get("name") or "当前项目"),
            "attribute": str(item.get("sourceDescription") or "官网事实"),
            "value_text": str(item.get("summary") or ""),
            "confidence": 1,
            "source_v2_document_id": item.get("documentVersionId"),
            "source_v2_chunk_id": None,
            "evidence_text": str(item.get("summary") or ""),
            "status": str(item.get("verificationState") or "verified"),
            "updated_at": str(item.get("updatedAt") or ""),
        }
        for item in website
    ]
    counts = {
        "event_lines": len(event_facts),
        "tasks": len(task_facts),
        "commitments": 0,
        "dna_documents": len(dna_documents),
        "atomic_facts": len(atomic_facts),
    }
    return {
        "client": client,
        "event_lines": [] if lite else event_facts,
        "tasks": [] if lite else task_facts,
        "commitments": [],
        "dna_documents": [] if lite else dna_documents,
        "atomic_facts": [] if lite else atomic_facts,
        "key_decisions": [],
        "snapshot_at": str(context.get("generatedAt") or utc_now()),
        "sources": {
            "client": "clients",
            "event_lines": "event_lines",
            "tasks": "tasks",
            "knowledge": "knowledge_documents+atomic_facts",
        },
        "counts": counts,
    }


@router.get(r"clients/(?P<project_id>[^/]+)/duplicate-documents")
def duplicate_documents(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> list[dict[str, Any]]:
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    result = LocalProjectMaterialsRepository(
        compatibility.runtime
    ).duplicate_document_groups(project_id)
    return list(result.get("groups") or [])


@router.get(r"clients/(?P<project_id>[^/]+)/entities")
def entities(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    return compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/entities",
        query=dict(request.query),
    )


@router.get(r"clients/(?P<project_id>[^/]+)/entity-merge-candidates")
def entity_merge_candidates(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    return compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
        "/entity-merge-candidates",
        query={"limit": request.query.get("limit") or "50"},
    )

@router.post(r"entities/(?P<entity_id>[^/]+)/verify")
def verify_entity(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/entities/{_segment(match.group('entity_id'))}/verify",
        payload=dict(request.body),
        idempotency_key=request.idempotency_key,
    )


@router.post(r"entities/(?P<merged_id>[^/]+)/merge")
def merge_entity(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/entities/{_segment(match.group('merged_id'))}/merge",
        payload=dict(request.body),
        idempotency_key=request.idempotency_key,
    )


@router.get(r"clients/(?P<project_id>[^/]+)/glossary")
def glossary(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    return compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/glossary",
        query=dict(request.query),
    )

@router.post(r"clients/(?P<project_id>[^/]+)/glossary")
def create_glossary_entry(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    result = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(match.group('project_id'))}"
        "/glossary",
        payload=dict(request.body),
        idempotency_key=request.idempotency_key,
    )
    return dict(result.get("entry") or {})


@router.patch(r"glossary/(?P<entry_id>[^/]+)")
def update_glossary_entry(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    result = compatibility.runtime.cloud_command(
        "PATCH",
        f"{_CLOUD_ROOT}/glossary/{_segment(match.group('entry_id'))}",
        payload=dict(request.body),
        idempotency_key=request.idempotency_key,
    )
    return dict(result.get("entry") or {})


@router.delete(r"glossary/(?P<entry_id>[^/]+)")
def delete_glossary_entry(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "DELETE",
        f"{_CLOUD_ROOT}/glossary/{_segment(match.group('entry_id'))}",
        payload={},
        idempotency_key=request.idempotency_key,
    )


@router.get(r"clients/(?P<project_id>[^/]+)/glossary-attributes")
def glossary_attributes(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    return compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
        "/glossary-attributes",
        query=dict(request.query),
    )

def _review_glossary_attribute(
    compatibility: Any,
    request: UiRequest,
    match: Any,
    review_status: str,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    attribute_id = match.group("attribute_id")
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
        f"/glossary-attributes/{_segment(attribute_id)}/review",
        payload={**dict(request.body), "reviewStatus": review_status},
        idempotency_key=request.idempotency_key,
    )


@router.post(
    r"clients/(?P<project_id>[^/]+)/glossary-attributes/"
    r"(?P<attribute_id>[^/]+)/verify"
)
def verify_glossary_attribute(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _review_glossary_attribute(
        compatibility,
        request,
        match,
        "verified",
    )


@router.post(
    r"clients/(?P<project_id>[^/]+)/glossary-attributes/"
    r"(?P<attribute_id>[^/]+)/reject"
)
def reject_glossary_attribute(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _review_glossary_attribute(
        compatibility,
        request,
        match,
        "rejected",
    )


@router.get(r"clients/(?P<project_id>[^/]+)/glossary-drift-alerts")
def glossary_drift_alerts(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    facts = glossary_attributes(compatibility, request, match).get("attributes") or []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in facts:
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        grouped[(
            str(item.get("term") or "").casefold(),
            str(item.get("attribute_name") or "").casefold(),
            str(item.get("scope") or "").casefold(),
        )].append(item)
    decisions_result = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/governance-decisions",
        query={"decisionKind": "glossary_drift"},
    )
    decisions = {
        str(item.get("id") or ""): dict(item)
        for item in decisions_result.get("decisions") or []
        if isinstance(item, Mapping)
    }
    alerts: list[dict[str, Any]] = []
    requested_status = str(request.query.get("status") or "pending")
    for items in grouped.values():
        verified = next(
            (item for item in items if item.get("verification_status") == "verified"),
            None,
        )
        if verified is None:
            continue
        for candidate in items:
            if candidate is verified or candidate.get("value_text") == verified.get("value_text"):
                continue
            alert_id = "derived_glossary_drift_" + sha256_text(
                f"{verified.get('id')}|{candidate.get('id')}"
            )[:24]
            decision = decisions.get(alert_id)
            review_status = str(decision.get("status") or "pending") if decision else "pending"
            if review_status != requested_status:
                continue
            alerts.append({
                "id": alert_id,
                "client_id": project_id,
                "glossary_attribute_id": verified.get("id"),
                "new_fact_id": candidate.get("id"),
                "verified_value_text": verified.get("value_text"),
                "new_value_text": candidate.get("value_text"),
                "severity": "high",
                "review_status": review_status,
                "review_note": str(decision.get("resolutionNote") or "") if decision else "",
                "detected_at": candidate.get("updated_at"),
                "reviewed_at": decision.get("reviewedAt") if decision else None,
                "reviewed_by": decision.get("reviewedBy") if decision else None,
                "term": verified.get("term"),
                "attribute_name": verified.get("attribute_name"),
                "scope": verified.get("scope"),
                "as_of_date": verified.get("as_of_date"),
            })
    return {"alerts": alerts, "derivation": "verified official facts"}

@router.post(
    r"clients/(?P<project_id>[^/]+)/glossary-drift-alerts/"
    r"(?P<alert_id>[^/]+)/resolve"
)
def resolve_glossary_drift_alert(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    alert_id = match.group("alert_id")
    facts = glossary_attributes(compatibility, request, match).get("attributes") or []
    selected: tuple[dict[str, Any], dict[str, Any]] | None = None
    for left in facts:
        if not isinstance(left, Mapping) or left.get("verification_status") != "verified":
            continue
        for right in facts:
            if not isinstance(right, Mapping) or left is right:
                continue
            derived = "derived_glossary_drift_" + sha256_text(
                f"{left.get('id')}|{right.get('id')}"
            )[:24]
            if derived == alert_id:
                selected = (dict(left), dict(right))
                break
        if selected:
            break
    if selected is None:
        raise LocalRuntimeError(404, "glossary_drift_alert_missing", "漂移候选不存在或已失效")
    action = str(request.body.get("action") or "").strip()
    if action not in {"update_glossary", "dismiss"}:
        raise LocalRuntimeError(422, "glossary_drift_action_invalid", "请选择更新口径或忽略候选")
    verified, candidate = selected
    candidate_id = str(candidate.get("id") or "")
    if action == "update_glossary":
        compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/glossary-attributes/{_segment(candidate_id)}/review",
            payload={"reviewStatus": "verified"},
            idempotency_key=f"{request.idempotency_key}:candidate",
        )
        compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/glossary-attributes/{_segment(str(verified.get('id') or ''))}/review",
            payload={"reviewStatus": "rejected"},
            idempotency_key=f"{request.idempotency_key}:previous",
        )
        review_status = "resolved"
    else:
        compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/glossary-attributes/{_segment(candidate_id)}/review",
            payload={"reviewStatus": "rejected"},
            idempotency_key=f"{request.idempotency_key}:candidate",
        )
        review_status = "dismissed"
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/governance-decisions/{_segment(alert_id)}",
        payload={
            "decisionKind": "glossary_drift",
            "reviewStatus": review_status,
            "acceptedFactId": candidate_id if action == "update_glossary" else None,
            "resolutionNote": str(request.body.get("note") or ""),
        },
        idempotency_key=request.idempotency_key,
    )


@router.get(r"clients/(?P<project_id>[^/]+)/contradictions")
def contradictions(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    facts = glossary_attributes(compatibility, request, match).get("attributes") or []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in facts:
        if isinstance(raw, Mapping):
            item = dict(raw)
            grouped[(
                str(item.get("term") or "").casefold(),
                str(item.get("attribute_name") or "").casefold(),
            )].append(item)
    requested_status = str(request.query.get("status") or "pending")
    decisions_result = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/governance-decisions",
        query={"decisionKind": "fact_contradiction"},
    )
    decisions = {
        str(item.get("id") or ""): dict(item)
        for item in decisions_result.get("decisions") or []
        if isinstance(item, Mapping)
    }
    contradictions_list: list[dict[str, Any]] = []
    for items in grouped.values():
        for index, left in enumerate(items):
            for right in items[index + 1:]:
                if left.get("value_text") == right.get("value_text"):
                    continue
                derived_id = "derived_contradiction_" + sha256_text(
                    f"{left.get('id')}|{right.get('id')}"
                )[:24]
                decision = decisions.get(derived_id)
                review_status = str(decision.get("status") or "pending") if decision else "pending"
                if review_status != requested_status:
                    continue
                contradictions_list.append({
                        "id": derived_id,
                        "clientId": project_id,
                        "subjectText": left.get("term"),
                        "attribute": left.get("attribute_name"),
                        "valueA": left.get("value_text"),
                        "valueB": right.get("value_text"),
                        "evidenceA": left.get("source_evidence"),
                        "evidenceB": right.get("source_evidence"),
                        "factAId": left.get("id"),
                        "factBId": right.get("id"),
                        "contradictionType": "value_diff",
                        "severity": "high",
                        "reviewStatus": review_status,
                        "resolutionNote": decision.get("resolutionNote") if decision else None,
                        "detectedAt": max(
                            str(left.get("updated_at") or ""),
                            str(right.get("updated_at") or ""),
                        ),
                    })
    limit = max(1, min(int(request.query.get("limit") or 100), 1000))
    return {
        "contradictions": contradictions_list[:limit],
        "total": len(contradictions_list),
        "derivation": "current official facts",
    }

@router.post(
    r"clients/(?P<project_id>[^/]+)/contradictions/"
    r"(?P<contradiction_id>[^/]+)/review"
)
def review_contradiction(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    contradiction_id = match.group("contradiction_id")
    facts = glossary_attributes(compatibility, request, match).get("attributes") or []
    fact_by_id = {
        str(item.get("id") or ""): dict(item)
        for item in facts
        if isinstance(item, Mapping)
    }
    pair: tuple[str, str] | None = None
    ids = list(fact_by_id)
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1:]:
            derived = "derived_contradiction_" + sha256_text(f"{left_id}|{right_id}")[:24]
            if derived == contradiction_id:
                pair = (left_id, right_id)
                break
        if pair:
            break
    if pair is None:
        raise LocalRuntimeError(404, "fact_contradiction_missing", "事实矛盾不存在或已失效")
    review_status = str(request.body.get("reviewStatus") or "").strip()
    accepted_id = str(request.body.get("acceptedFactId") or "").strip()
    if review_status not in {"resolved", "dismissed"}:
        raise LocalRuntimeError(422, "fact_contradiction_review_invalid", "矛盾裁决状态无效")
    if accepted_id:
        if accepted_id not in pair:
            raise LocalRuntimeError(422, "accepted_fact_invalid", "采纳事实不属于当前矛盾")
        rejected_id = pair[1] if accepted_id == pair[0] else pair[0]
        compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/glossary-attributes/{_segment(accepted_id)}/review",
            payload={"reviewStatus": "verified"},
            idempotency_key=f"{request.idempotency_key}:accepted",
        )
        compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/glossary-attributes/{_segment(rejected_id)}/review",
            payload={"reviewStatus": "rejected"},
            idempotency_key=f"{request.idempotency_key}:rejected",
        )
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/governance-decisions/{_segment(contradiction_id)}",
        payload={
            "decisionKind": "fact_contradiction",
            "reviewStatus": review_status,
            "acceptedFactId": accepted_id or None,
            "resolutionNote": str(request.body.get("resolutionNote") or ""),
        },
        idempotency_key=request.idempotency_key,
    )


@router.post(r"clients/(?P<project_id>[^/]+)/folders/recommend")
def folder_recommendation(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    result = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
        "/folder-recommendation",
        payload={},
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    if request.body and result.get("folders"):
        return dict(result["folders"][0])
    return result


@router.post(r"clients/(?P<project_id>[^/]+)/documents/auto-repair/preview")
def auto_repair_preview(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
        "/auto-repair-preview",
        payload=dict(request.body),
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )

@router.post(r"clients/(?P<project_id>[^/]+)/documents/auto-repair/apply")
def auto_repair_apply(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    preview = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
        "/auto-repair-preview",
        payload=dict(request.body),
        idempotency_key=f"{request.idempotency_key}:preview",
        refresh_business=False,
    )
    selected = {
        str(value)
        for value in request.body.get("documentIds") or []
        if str(value)
    }
    include_human = bool(request.body.get("includeHumanRequired"))
    candidates = [
        item
        for item in preview.get("items") or []
        if item.get("healthStatus") != "v2_ready"
        and (not selected or str(item.get("documentId") or "") in selected)
    ]
    if not candidates:
        return {
            "jobId": new_id(),
            "status": "completed",
            "queuedCount": 0,
            "skippedCount": 0,
            "humanConfirmationCount": 0,
            "message": "当前没有需要修复的资料",
            "repairedCount": 0,
            "failures": [],
            "pollingEnabled": False,
        }
    store = _local_store(compatibility)
    repaired = 0
    skipped = 0
    failures = []
    for item in candidates:
        document_id = str(item.get("documentId") or "")
        if bool(item.get("requiresHuman")) and not include_human:
            skipped += 1
            continue
        try:
            local = _local_call(lambda: store.document_text(document_id))
            content = str(local.get("content") or "").strip()
            if not content:
                raise LocalRuntimeError(
                    422,
                    "local_document_empty",
                    "本机源文件没有可解析正文",
                )
            reading = compatibility.runtime.cloud_query(
                f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
                f"/documents/{_segment(document_id)}/reading-preview"
            )
            completion = compatibility.runtime.private_ai_completion(
                system_prompt=(
                    "你是项目资料修复器。只根据当前设备正文生成组织共享摘要，"
                    "保留事实、主体、时间、承诺、风险和待办，不补造信息。"
                ),
                prompt=content[:120_000],
                creativity_mode="strict",
            )
            summary = str(completion.get("content") or "").strip()
            if not summary:
                raise LocalRuntimeError(
                    502,
                    "local_ai_summary_empty",
                    "资料修复没有生成可发布摘要",
                )
            _local_call(
                lambda: store.update_ai_summary(
                    document_id,
                    summary=summary,
                    model_name=str(
                        completion.get("modelName") or "organization_default"
                    ),
                )
            )
            compatibility.runtime.cloud_command(
                "POST",
                f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
                f"/documents/{_segment(document_id)}/publish-local-summary",
                payload={
                    "expectedVersion": int(
                        reading.get("aggregateVersion") or 0
                    ),
                    "sourceContentHash": local["contentHash"],
                    "summary": summary[:4000],
                    "generatorVersion": str(
                        completion.get("modelName") or "organization_default"
                    ),
                },
                idempotency_key=(
                    f"{request.idempotency_key}:summary:{document_id}"
                ),
                refresh_business=False,
            )
            repaired += 1
        except LocalRuntimeError as exc:
            failures.append(
                {
                    "documentId": document_id,
                    "errorCode": exc.code,
                    "message": exc.message,
                    "state": (
                        "failed_retryable"
                        if exc.status_code >= 500
                        else "blocked"
                    ),
                }
            )
    return {
        "jobId": new_id(),
        "status": "failed" if failures else "completed",
        "queuedCount": 0,
        "skippedCount": skipped,
        "humanConfirmationCount": sum(
            bool(item.get("requiresHuman")) for item in candidates
        ),
        "message": (
            f"已从当前设备修复 {repaired} 份资料"
            + (f"，{len(failures)} 份失败可重试" if failures else "")
        ),
        "repairedCount": repaired,
        "failures": failures,
        "pollingEnabled": False,
    }


@router.post(r"clients/(?P<project_id>[^/]+)/documents/from-text")
def create_document_from_text(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    local = _local_call(
        lambda: _local_store(compatibility).import_text(
            project_id=project_id,
            title=str(request.body.get("title") or ""),
            content=str(request.body.get("content") or ""),
            idempotency_key=request.idempotency_key,
        )
    )
    store = _local_store(compatibility)
    if hasattr(store, "bind_pending_materials"):
        _local_call(
            lambda: store.bind_pending_materials(
                project_id=project_id,
                local_materials=[local],
            )
        )
    try:
        registered = compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
            "/materials/register-metadata",
            payload={
                "materials": [
                    {
                        "localSourceId": local["localSourceId"],
                        "fileName": local["fileName"],
                        "contentHash": local["contentHash"],
                        "byteSize": local["byteSize"],
                        "mediaType": local["mediaType"],
                        "sourceKind": "local_private_text",
                    }
                ]
            },
            idempotency_key=request.idempotency_key,
        )
        document = dict((registered.get("documents") or [])[0])
        if hasattr(store, "bind_cloud_documents"):
            _local_call(
                lambda: store.bind_cloud_documents(
                    project_id=project_id,
                    local_materials=[local],
                    cloud_documents=[document],
                )
            )
        return {
            "clientId": project_id,
            "documentId": document.get("documentId"),
            "title": local["title"],
            "fileName": local["fileName"],
            "path": local["managedPath"],
            "sourceScope": "local_private",
            "localState": "ready",
            "cloudMetadataState": "ready",
            "overallState": "ready",
            "retryable": False,
            "materialBoundary": registered.get("materialBoundary") or {},
        }
    except LocalRuntimeError as exc:
        duplicate = exc.code == "material_content_duplicate"
        version_conflict = exc.code == "material_metadata_version_conflict"
        return {
            "clientId": project_id,
            "documentId": f"local-pending:{local['localSourceId']}",
            "title": local["title"],
            "fileName": local["fileName"],
            "path": local["managedPath"],
            "sourceScope": "local_private",
            "localState": "ready",
            "cloudMetadataState": (
                "failed_retryable"
                if exc.status_code >= 500
                else "blocked"
            ),
            "overallState": "partial",
            "retryable": exc.status_code >= 500,
            "message": "本机文档已创建；组织云元数据尚待同步",
            "materialBoundary": {},
        }


@router.get(
    r"clients/(?P<project_id>[^/]+)/documents/"
    r"(?P<document_id>[^/]+)/reading-preview"
)
def document_reading_preview(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    document_id = match.group("document_id")
    _require_project_read(compatibility, project_id)
    if hasattr(compatibility.runtime, "database_path"):
        try:
            local = _local_store(compatibility).document_text(document_id)
        except LocalRuntimeError as exc:
            if exc.code not in {
                "local_document_missing",
                "local_document_source_missing",
                "local_document_preview_unsupported",
            }:
                raise
        else:
            if str(local.get("projectId") or "") != project_id:
                raise LocalRuntimeError(
                    409,
                    "local_document_project_mismatch",
                    "本机资料映射与当前项目不一致，请刷新后重试",
                )
            content = str(local.get("content") or "")
            return {
                "documentId": document_id,
                "title": local.get("title") or "未命名资料",
                "parseStatus": "ready",
                "folderLabel": None,
                "sectionCount": len(
                    [
                        line
                        for line in content.splitlines()
                        if line.lstrip().startswith("#")
                    ]
                ),
                "chunkCount": 1 if content else 0,
                "sourceKind": "member_local",
                "readSummary": content[:2000],
                "keyHeadings": [
                    line.lstrip("#").strip()
                    for line in content.splitlines()
                    if line.lstrip().startswith("#")
                ][:20],
                "availableForChat": bool(content),
                "failureReason": None,
                "materialBoundary": {
                    "sourceFileContentIncluded": True,
                    "sourceFilePathsIncluded": False,
                    "storageLocatorsIncluded": False,
                    "unpublishedDocumentContentIncluded": True,
                    "sourceScope": "current_device_managed_storage",
                },
                "_strictVersion": 1,
            }
    result = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
        f"/documents/{_segment(document_id)}/reading-preview"
    )
    published = bool(result.get("publishedSummary"))
    return {
        "documentId": document_id,
        "title": result.get("title") or "未命名资料",
        "parseStatus": result.get("parseState") or "not_requested",
        "folderLabel": None,
        "sectionCount": int(result.get("sectionCount") or 0),
        "chunkCount": int(result.get("chunkCount") or 0),
        "sourceKind": result.get("sourceKind") or "strict_v2",
        "readSummary": result.get("readSummary") or "",
        "keyHeadings": [],
        "availableForChat": published
        and result.get("parseState") in {"ready", "partial_ready"},
        "failureReason": (
            None
            if published
            else "组织云只提供已明确发布的共享摘要，不返回成员源文件正文"
        ),
        "materialBoundary": result.get("materialBoundary") or {},
        "_strictVersion": int(result.get("aggregateVersion") or 1),
    }


@router.delete(
    r"clients/(?P<project_id>[^/]+)/documents/(?P<document_id>[^/]+)"
)
def delete_document(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    document_id = match.group("document_id")
    if not hasattr(compatibility.runtime, "database_path"):
        preview = compatibility.runtime.cloud_query(
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
            f"/documents/{_segment(document_id)}/reading-preview"
        )
        return compatibility.runtime.cloud_command(
            "DELETE",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
            f"/documents/{_segment(document_id)}",
            payload={
                "expectedVersion": int(
                    preview.get("aggregateVersion") or 1
                )
            },
            idempotency_key=request.idempotency_key,
        )
    store = _local_store(compatibility)
    local = replayable_generated_value(
        compatibility.runtime,
        idempotency_key=request.idempotency_key,
        command_type="project_materials.document_local_delete",
        aggregate_type="source_asset",
        aggregate_id=document_id,
        input_payload={"projectId": project_id, "documentId": document_id},
        generate=lambda: _local_call(
            lambda: store.delete_document_local(project_id, document_id)
        ),
    )
    cloud_document_id = str(local.get("cloudDocumentId") or "")
    if not cloud_document_id:
        return {
            **local,
            "overallState": "ready",
            "retryable": False,
            "message": "本机资料已移入回收状态",
        }
    try:
        cloud_path = (
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
            f"/documents/{_segment(cloud_document_id)}"
        )

        def delete_payload() -> dict[str, Any]:
            preview = compatibility.runtime.cloud_query(
                f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
                f"/documents/{_segment(cloud_document_id)}/reading-preview"
            )
            return {
                "expectedVersion": int(preview.get("aggregateVersion") or 1),
            }

        cloud = replayable_cloud_mutation(
            compatibility.runtime,
            idempotency_key=request.idempotency_key,
            command_type="project_materials.document_cloud_delete",
            aggregate_type="knowledge_document",
            aggregate_id=cloud_document_id,
            method="DELETE",
            path=cloud_path,
            request_payload={"projectId": project_id, "documentId": document_id},
            cloud_payload_factory=delete_payload,
        )
        _local_call(
            lambda: store.complete_cloud_delete(
                project_id,
                cloud_document_id,
            )
        )
        return {
            **cloud,
            **local,
            "cloudMetadataState": "ready",
            "overallState": "ready",
            "retryable": False,
            "message": "本机资料已移入回收状态，组织云元数据已删除",
        }
    except LocalRuntimeError as exc:
        return {
            **local,
            "cloudMetadataState": (
                "failed_retryable"
                if exc.status_code >= 500
                else "blocked"
            ),
            "overallState": "partial",
            "retryable": exc.status_code >= 500,
            "errorCode": exc.code,
            "message": "本机资料已删除；组织云元数据尚待同步",
        }


@router.get(r"clients/(?P<project_id>[^/]+)/document-recycle-bin")
def document_recycle_bin(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    items = _local_call(
        lambda: _local_store(compatibility).recycled_documents(project_id)
    )
    return {
        "clientId": project_id,
        "items": items,
        "count": len(items),
        "retentionDays": 30,
        "recoverableOnly": True,
    }


@router.post(
    r"clients/(?P<project_id>[^/]+)/document-recycle-bin/"
    r"(?P<document_id>[^/]+)/restore"
)
def restore_recycled_document(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    document_id = match.group("document_id")
    _require_project_read(compatibility, project_id)
    store = _local_store(compatibility)
    local = _local_call(
        lambda: store.restore_recycled_document(project_id, document_id)
    )
    settled = _local_call(
        lambda: register_and_process_local_materials(
            runtime=compatibility.runtime,
            store=store,
            project_id=project_id,
            local_materials=[local],
            relation_kind="",
            relation_id="",
            idempotency_key=request.idempotency_key,
            force_processing=True,
        )
    )
    restored_ids = [
        str(value)
        for value in settled.get("documentIds") or []
        if str(value)
    ]
    return {
        "restored": True,
        "clientId": project_id,
        "previousDocumentId": document_id,
        "documentId": restored_ids[0] if restored_ids else document_id,
        "fileName": local.get("fileName"),
        "overallState": settled.get("overallState") or "ready",
        "cloudMetadataState": settled.get("cloudMetadataState") or "ready",
        "processing": settled.get("processing") or {},
        "sharedSummaries": settled.get("sharedSummaries") or {},
    }


@router.get(r"documents/(?P<document_id>[^/]+)/text")
def document_text(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    document_id = match.group("document_id")
    if hasattr(compatibility.runtime, "database_path"):
        try:
            store = _local_store(compatibility)
            project_id = store.document_project_id(document_id)
            _require_project_read(compatibility, project_id)
            return _local_call(
                lambda: store.document_text(document_id)
            )
        except LocalRuntimeError as exc:
            if exc.code != "local_document_missing":
                raise
    result = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/documents/{_segment(document_id)}/text"
    )
    return {
        "content": result.get("content") or "",
        "kind": result.get("kind") or "shared_summary",
        "title": result.get("title") or "未命名资料",
        "sourceScope": result.get("sourceScope") or "organization_shared",
    }


@router.get(r"clients/(?P<project_id>[^/]+)/link-materials/import-runs")
def list_link_import_runs(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> list[dict[str, Any]]:
    project_id = match.group("project_id")
    limit = max(1, min(int(request.query.get("limit") or 20), 100))
    local = _local_call(
        lambda: _local_store(compatibility).link_import_runs(
            project_id,
            limit=limit,
        )
    )
    # Link fetching and its progress are member-device work.  The organization
    # cloud receives only the final safe material metadata, never a second copy
    # of the local execution state.  Querying a cloud ``link-import-runs``
    # endpoint here used to turn an otherwise valid empty/local list into a 501
    # whenever that deliberately absent endpoint was polled by the renderer.
    cloud: list[dict[str, Any]] = []
    by_id = {
        str(item.get("runId") or ""): dict(item)
        for item in [*local, *cloud]
        if str(item.get("runId") or "")
    }
    return sorted(
        by_id.values(),
        key=lambda item: (
            str(item.get("updatedAt") or ""),
            str(item.get("runId") or ""),
        ),
        reverse=True,
    )[:limit]


@router.get(
    r"clients/(?P<project_id>[^/]+)/link-materials/import-runs/latest"
)
def latest_link_import_run(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any] | None:
    runs = list_link_import_runs(compatibility, request, match)
    return runs[0] if runs else None


@router.get(
    r"clients/(?P<project_id>[^/]+)/link-materials/import-runs/"
    r"(?P<run_id>[^/]+)"
)
def get_link_import_run(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    run_id = match.group("run_id")
    local = _local_call(
        lambda: _local_store(compatibility).link_import_runs(
            project_id,
            run_id=run_id,
        )
    )
    if local:
        return local[0]
    raise LocalRuntimeError(
        404,
        "link_import_run_missing",
        "当前设备中不存在该链接导入任务",
    )


@router.post(
    r"clients/(?P<project_id>[^/]+)/link-materials/import-runs/"
    r"(?P<run_id>[^/]+)/cancel"
)
def cancel_link_import_run(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    run_id = match.group("run_id")
    local = _local_call(
        lambda: _local_store(compatibility).link_import_runs(
            project_id,
            run_id=run_id,
        )
    )
    if local:
        return _local_call(
            lambda: _local_store(compatibility).cancel_link_import_run(
                project_id,
                run_id,
            )
        )
    raise LocalRuntimeError(
        404,
        "link_import_run_missing",
        "当前设备中不存在该链接导入任务",
    )


def _link_import_run(store: Any, project_id: str, run_id: str) -> dict[str, Any] | None:
    runs = _local_call(lambda: store.link_import_runs(project_id, run_id=run_id))
    return dict(runs[0]) if runs else None


def _save_link_import_run_changes(
    store: Any,
    project_id: str,
    run_id: str,
    **changes: Any,
) -> dict[str, Any] | None:
    current = _link_import_run(store, project_id, run_id)
    if current is None:
        return None
    if str(current.get("status") or "") == "canceled":
        return current
    return _local_call(
        lambda: store.save_link_import_run(
            project_id,
            {**current, **changes, "updatedAt": utc_now()},
        )
    )


def _run_link_import_in_background(
    *,
    compatibility: Any,
    store: Any,
    project_id: str,
    run_id: str,
    url: str,
    pinned_sandbox: Any,
) -> None:
    runtime = compatibility.runtime
    operation_key = f"link-import:{run_id}"
    context = (
        runtime.prebound_sandbox_context(pinned_sandbox)
        if pinned_sandbox is not None
        else nullcontext()
    )
    last_fetch_progress = -1

    def ensure_active() -> dict[str, Any]:
        current = _link_import_run(store, project_id, run_id)
        if current is None:
            raise LocalRuntimeError(404, "link_import_run_missing", "链接转存任务已不存在")
        if str(current.get("status") or "") == "canceled":
            raise LocalRuntimeError(409, "link_import_cancelled", "链接转存已取消")
        return current

    def update(stage: str, progress: int, **changes: Any) -> None:
        ensure_active()
        _save_link_import_run_changes(
            store,
            project_id,
            run_id,
            status="running",
            state="processing",
            stage=stage,
            progress=max(1, min(99, int(progress))),
            retryable=True,
            pollingEnabled=True,
            error=None,
            errorCode=None,
            **changes,
        )

    def report_fetch_progress(percent: int | None) -> None:
        nonlocal last_fetch_progress
        mapped = 12 if percent is None else 10 + int(max(0, min(100, percent)) * 0.42)
        if mapped <= last_fetch_progress:
            return
        last_fetch_progress = mapped
        update("正在获取公开内容", mapped)

    try:
        with context:
            update("正在识别链接", 5)
            database_path = getattr(runtime, "database_path", None)
            fetched = fetch_link_material(
                url,
                data_root=database_path.parent if database_path is not None else None,
                progress=report_fetch_progress,
            )
            fetched_metadata = dict(fetched.get("metadata") or {})
            update(
                "正在生成本机转写文件",
                56,
                sourcePlatform=str(fetched.get("platform") or ""),
                sourceUrl=str(fetched.get("sourceUrl") or url),
                title=str(fetched.get("title") or ""),
                mediaCacheStatus=str(fetched_metadata.get("mediaCacheStatus") or "not_downloaded"),
                metadata=fetched_metadata,
            )
            local = _local_call(
                lambda: store.import_text(
                    project_id=project_id,
                    title=str(fetched["title"]),
                    content=str(fetched["text"]),
                    idempotency_key=f"{operation_key}:local-text",
                )
            )
            if hasattr(store, "bind_pending_materials"):
                _local_call(
                    lambda: store.bind_pending_materials(
                        project_id=project_id,
                        local_materials=[local],
                    )
                )
            local_document_id = "local-pending:" + str(local.get("localSourceId") or "")
            update(
                "本机文件已生成，正在登记组织云",
                68,
                documentId=local_document_id,
                documentPath=local.get("managedPath"),
            )
            registered = runtime.cloud_command(
                "POST",
                f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/materials/register-metadata",
                payload={
                    "materials": [
                        {
                            "localSourceId": local["localSourceId"],
                            "fileName": local["fileName"],
                            "contentHash": local["contentHash"],
                            "byteSize": local["byteSize"],
                            "mediaType": local["mediaType"],
                            "sourceKind": "link_import_local_metadata",
                        }
                    ]
                },
                idempotency_key=f"{operation_key}:metadata",
            )
            documents = list(registered.get("documents") or [])
            if not documents:
                raise LocalRuntimeError(
                    502,
                    "link_import_metadata_result_invalid",
                    "组织云没有返回链接资料元数据",
                )
            _local_call(
                lambda: store.bind_cloud_documents(
                    project_id=project_id,
                    local_materials=[local],
                    cloud_documents=documents,
                )
            )
            document_id = str(documents[0].get("documentId") or "")
            update(
                "正在生成项目共享摘要",
                82,
                documentId=document_id,
                documentPath=local.get("managedPath"),
            )
            shared_state = "not_connected"
            shared_error = None
            published_document_id = None
            try:
                completion = runtime.private_ai_completion(
                    system_prompt=(
                        "你是项目资料摘要器。只根据网页正文生成中文项目背景摘要，"
                        "保留主体、事实、时间、承诺、风险和待办，不补造信息。"
                    ),
                    prompt=str(fetched["text"])[:120_000],
                    creativity_mode="strict",
                )
                summary = str(completion.get("content") or "").strip()
                if not summary:
                    raise LocalRuntimeError(502, "link_import_summary_empty", "组织模型没有生成可发布摘要")
                _local_call(
                    lambda: store.update_ai_summary(
                        document_id,
                        summary=summary,
                        model_name=str(completion.get("modelName") or "organization_default"),
                    )
                )
                published = runtime.cloud_command(
                    "POST",
                    f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
                    f"/documents/{_segment(document_id)}/publish-local-summary",
                    payload={
                        "expectedVersion": int(documents[0].get("version") or 1),
                        "sourceContentHash": local["contentHash"],
                        "summary": summary[:4000],
                        "generatorVersion": str(completion.get("modelName") or "organization_default"),
                    },
                    idempotency_key=f"{operation_key}:summary",
                )
                shared_state = "ready"
                published_document_id = published.get("documentId")
            except LocalRuntimeError as exc:
                shared_state = "failed_retryable" if exc.status_code >= 500 else "blocked"
                shared_error = exc.message
            _save_link_import_run_changes(
                store,
                project_id,
                run_id,
                sourcePlatform=str(fetched.get("platform") or ""),
                sourceUrl=str(fetched.get("sourceUrl") or url),
                title=str(fetched.get("title") or ""),
                status="completed",
                state="ready" if shared_state == "ready" else "completed_with_warning",
                stage=(
                    "转写及共享摘要已完成"
                    if shared_state == "ready"
                    else "转写已完成，共享摘要待重试"
                ),
                progress=100,
                documentId=document_id,
                documentPath=local.get("managedPath"),
                mediaCacheStatus=str(fetched_metadata.get("mediaCacheStatus") or "not_downloaded"),
                metadata=fetched_metadata,
                retryable=shared_state == "failed_retryable",
                pollingEnabled=False,
                materialBoundary=registered.get("materialBoundary") or {},
                sharedKnowledgeState=shared_state,
                sharedKnowledgeError=shared_error,
                publishedSummaryDocumentId=published_document_id,
                error=None,
                errorCode=None,
            )
    except LocalRuntimeError as exc:
        current = _link_import_run(store, project_id, run_id)
        if current is None or str(current.get("status") or "") == "canceled":
            return
        failure_state = str(
            getattr(exc, "state", "failed_retryable" if exc.status_code >= 500 else "blocked")
        )
        _save_link_import_run_changes(
            store,
            project_id,
            run_id,
            status="failed",
            state=failure_state,
            stage=(
                "本机内容已生成，组织云登记待重试"
                if current.get("documentPath")
                else "链接访问受阻" if failure_state in {"blocked", "not_connected"}
                else "处理失败，可重试"
            ),
            progress=100,
            error=exc.message,
            errorCode=exc.code,
            retryable=bool(getattr(exc, "retryable", exc.status_code >= 500)),
            pollingEnabled=False,
            mediaCacheStatus=(
                str(current.get("mediaCacheStatus") or "not_downloaded")
                if current.get("documentPath")
                else "failed"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        current = _link_import_run(store, project_id, run_id)
        if current is None or str(current.get("status") or "") == "canceled":
            return
        _save_link_import_run_changes(
            store,
            project_id,
            run_id,
            status="failed",
            state="failed_retryable",
            stage="本机内容已生成，后续处理待重试" if current.get("documentPath") else "处理意外中断，可重试",
            progress=100,
            error=f"{exc.__class__.__name__}：链接转存意外中断",
            errorCode="link_import_unexpected_failure",
            retryable=True,
            pollingEnabled=False,
        )


def _launch_link_import_run(
    *,
    compatibility: Any,
    store: Any,
    project_id: str,
    run_id: str,
    url: str,
) -> None:
    runtime = compatibility.runtime
    pinned_sandbox = (
        runtime.capture_sandbox_context()
        if callable(getattr(runtime, "capture_sandbox_context", None))
        else None
    )
    _LinkImportThread(
        target=_run_link_import_in_background,
        kwargs={
            "compatibility": compatibility,
            "store": store,
            "project_id": project_id,
            "run_id": run_id,
            "url": url,
            "pinned_sandbox": pinned_sandbox,
        },
        name=f"link-import-{run_id[-10:]}",
        daemon=True,
    ).start()

@router.post(r"clients/(?P<project_id>[^/]+)/link-materials/import/start")
def start_link_import_run(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    url = str(request.body.get("url") or "").strip()
    if not url:
        raise LocalRuntimeError(422, "link_import_url_required", "请填写需要转存的公开链接")
    run_id = (
        "local-link-"
        + sha256_text(f"{project_id}|{request.idempotency_key}")[:32]
    )
    store = _local_store(compatibility)
    existing = _local_call(
        lambda: store.link_import_runs(project_id, run_id=run_id)
    )
    if existing:
        return dict(existing[0])
    else:
        created_at = utc_now()
        initial = {
            "runId": run_id,
            "clientId": project_id,
            "sourcePlatform": "",
            "sourceUrl": url,
            "title": "",
            "status": "queued",
            "state": "processing",
            "stage": "等待本机处理",
            "progress": 1,
            "documentId": None,
            "documentPath": None,
            "mediaCacheStatus": "not_downloaded",
            "metadata": {
                "accessMode": "anonymous",
                "temporaryFilesCleaned": True,
            },
            "error": None,
            "errorCode": None,
            "retryable": True,
            "pollingEnabled": True,
            "createdAt": created_at,
            "updatedAt": created_at,
        }
    saved = _local_call(lambda: store.save_link_import_run(project_id, initial))
    if bool(request.body.get("useBrowserCookies")):
        return _local_call(
            lambda: store.save_link_import_run(
                project_id,
                {
                    **initial,
                    "status": "failed",
                    "state": "blocked",
                    "stage": "浏览器登录态未获授权",
                    "progress": 100,
                    "errorCode": "browser_cookie_authorization_required",
                    "error": (
                        "严格新版不会静默读取浏览器 Cookie；"
                        "请先关闭“使用浏览器登录态”导入公开正文"
                    ),
                    "retryable": False,
                    "pollingEnabled": False,
                    "updatedAt": utc_now(),
                },
            )
        )

    _launch_link_import_run(
        compatibility=compatibility,
        store=store,
        project_id=project_id,
        run_id=run_id,
        url=url,
    )
    return saved


@router.post(
    r"clients/(?P<project_id>[^/]+)/link-materials/import-runs/"
    r"(?P<run_id>[^/]+)/retry"
)
def retry_link_import_run(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    run_id = match.group("run_id")
    _require_project_read(compatibility, project_id)
    store = _local_store(compatibility)
    prior = _link_import_run(store, project_id, run_id)
    if prior is None:
        raise LocalRuntimeError(404, "link_import_run_missing", "当前设备中不存在该链接转存任务")
    if str(prior.get("status") or "") in {"queued", "running"}:
        return prior
    if not bool(prior.get("retryable")):
        raise LocalRuntimeError(409, "link_import_run_not_retryable", "当前链接转存结果不能重试")
    source_url = str(prior.get("sourceUrl") or "").strip()
    if not source_url:
        raise LocalRuntimeError(409, "link_import_source_missing", "原始链接已丢失，不能重试")
    queued = _local_call(
        lambda: store.save_link_import_run(
            project_id,
            {
                **prior,
                "status": "queued",
                "state": "processing",
                "stage": "等待重新处理",
                "progress": 1,
                "error": None,
                "errorCode": None,
                "retryable": True,
                "pollingEnabled": True,
                "updatedAt": utc_now(),
            },
        )
    )
    _launch_link_import_run(
        compatibility=compatibility,
        store=store,
        project_id=project_id,
        run_id=run_id,
        url=source_url,
    )
    return queued


@router.get(r"clients/(?P<project_id>[^/]+)/mobile-link-transfers/pending")
def pending_mobile_link_transfers(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    return compatibility.runtime.cloud_query(
        "/api/v2/mobile-link-transfers/pending",
        query={"projectId": project_id},
    )


@router.post(
    r"clients/(?P<project_id>[^/]+)/mobile-link-transfers/"
    r"(?P<run_id>[^/]+)/claim"
)
def claim_mobile_link_transfer(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    return compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/mobile-link-transfers/"
        f"{_segment(match.group('run_id'))}/claim",
        payload={},
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.post(
    r"clients/(?P<project_id>[^/]+)/mobile-link-transfers/"
    r"(?P<run_id>[^/]+)/settle"
)
def settle_mobile_link_transfer(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    return compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/mobile-link-transfers/"
        f"{_segment(match.group('run_id'))}/settle",
        payload=dict(request.body),
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.post(r"imports")
def import_paths(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> list[dict[str, Any]]:
    project_id = str(request.body.get("clientId") or "").strip()
    _require_project_read(compatibility, project_id)
    local = _local_call(
        lambda: _local_store(compatibility).import_paths(
            project_id=project_id,
            mode=str(request.body.get("mode") or "file"),
            paths=request.body.get("paths") or [],
            idempotency_key=request.idempotency_key,
        )
    )
    store = _local_store(compatibility)
    _local_call(
        lambda: store.bind_pending_materials(
            project_id=project_id,
            local_materials=local["materials"],
        )
    )
    meeting_id = str(request.body.get("meetingId") or "").strip()
    if meeting_id:
        _local_call(
            lambda: store.bind_meeting_materials(
                project_id=project_id,
                meeting_id=meeting_id,
                local_materials=local["materials"],
            )
        )
    try:
        cloud = compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
            "/materials/register-metadata",
            payload={
                "materials": [
                    {
                        "localSourceId": item["localSourceId"],
                        "fileName": item["fileName"],
                        "contentHash": item["contentHash"],
                        "byteSize": item["byteSize"],
                        "mediaType": item["mediaType"],
                        "sourceKind": "local_private_metadata",
                    }
                    for item in local["materials"]
                ]
            },
            idempotency_key=request.idempotency_key,
        )
    except LocalRuntimeError as exc:
        return [
            {
                "id": request.idempotency_key,
                "clientId": project_id,
                "sourcePath": str(
                    next(iter(request.body.get("paths") or []), "")
                ),
                "mode": local["mode"],
                "status": "partial",
                "localState": "ready",
                "cloudMetadataState": (
                    "failed_retryable"
                    if exc.status_code >= 500
                    else "blocked"
                ),
                "overallState": "partial",
                "retryable": exc.status_code >= 500,
                "message": "文件已保存到当前设备；组织云元数据尚待同步",
                "importedCount": len(local["materials"]),
                "skippedCount": int(local.get("skippedCount") or 0),
                "duplicateCount": 0,
                "versionUpgradeCount": 0,
                "unsupportedCount": int(local.get("unsupportedCount") or 0),
                "filteredSystemCount": int(local.get("filteredSystemCount") or 0),
                "filteredVideoCount": int(local.get("filteredVideoCount") or 0),
                "skippedFiles": list(local.get("skipped") or []),
                "createdAt": utc_now(),
                "jobId": None,
                "documents": [
                    {
                        "documentId": (
                            f"local-pending:{item['localSourceId']}"
                        ),
                        "title": item["fileName"],
                        "fileName": item["fileName"],
                        "path": item["managedPath"],
                    }
                    for item in local["materials"]
                ],
                "materialBoundary": {},
            }
        ]
    cloud_by_local = {
        str(item.get("localSourceId") or ""): item
        for item in cloud.get("documents") or []
    }
    _local_call(
        lambda: store.bind_cloud_documents(
            project_id=project_id,
            local_materials=local["materials"],
            cloud_documents=cloud.get("documents") or [],
        )
    )
    documents = [
        {
            "documentId": (
                cloud_by_local.get(item["localSourceId"]) or {}
            ).get("documentId"),
            "title": item["fileName"],
            "fileName": item["fileName"],
            "path": item["managedPath"],
        }
        for item in local["materials"]
    ]
    return [
        {
            "id": cloud.get("importRunId") or request.idempotency_key,
            "clientId": project_id,
            "sourcePath": str(
                next(iter(request.body.get("paths") or []), "")
            ),
            "mode": local["mode"],
            "status": "completed",
            "localState": "ready",
            "cloudMetadataState": "ready",
            "overallState": "ready",
            "retryable": False,
            "importedCount": int(cloud.get("importedCount") or 0),
            "skippedCount": int(local.get("skippedCount") or 0)
            + int(cloud.get("skippedCount") or 0),
            "duplicateCount": 0,
            "versionUpgradeCount": 0,
            "unsupportedCount": int(local.get("unsupportedCount") or 0),
            "filteredSystemCount": int(local.get("filteredSystemCount") or 0),
            "filteredVideoCount": int(local.get("filteredVideoCount") or 0),
            "skippedFiles": list(local.get("skipped") or []),
            "createdAt": cloud.get("createdAt"),
            "jobId": None,
            "documents": documents,
            "materialBoundary": cloud.get("materialBoundary") or {},
        }
    ]


@router.post(r"smart-import/sessions")
def create_smart_import_session(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).create_smart_session(request.body)
    )


@router.get(r"smart-import/sessions/(?P<session_id>[^/]+)")
def get_smart_import_session(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).get_smart_session(
            match.group("session_id")
        )
    )


@router.patch(r"smart-import/sessions/(?P<session_id>[^/]+)")
def update_smart_import_session(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).update_smart_session(
            match.group("session_id"),
            request.body,
        )
    )


@router.delete(r"smart-import/sessions/(?P<session_id>[^/]+)")
def discard_smart_import_session(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).discard_smart_session(
            match.group("session_id")
        )
    )


@router.post(r"smart-import/sessions/(?P<session_id>[^/]+)/files")
def upload_smart_import_file(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    upload = request.body.get("file")
    if isinstance(upload, list):
        upload = upload[0] if upload else None
    if upload is None:
        raise LocalRuntimeError(422, "smart_import_file_required", "请选择上传文件")
    return _local_call(
        lambda: _local_store(compatibility).upload_smart_file(
            match.group("session_id"),
            upload,
        )
    )


@router.delete(r"smart-import/files/(?P<file_id>[^/]+)")
def delete_smart_import_file(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).delete_smart_file(
            match.group("file_id")
        )
    )


@router.patch(r"smart-import/files/(?P<file_id>[^/]+)/assign")
def assign_smart_import_file(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).assign_smart_file(
            match.group("file_id"),
            (
                str(request.body.get("chunkId"))
                if request.body.get("chunkId")
                else None
            ),
        )
    )


@router.post(r"smart-import/sessions/(?P<session_id>[^/]+)/chunks")
def add_smart_import_chunk(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).add_smart_chunk(
            match.group("session_id"),
            request.body,
        )
    )


@router.patch(r"smart-import/chunks/(?P<chunk_id>[^/]+)")
def update_smart_import_chunk(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).update_smart_chunk(
            match.group("chunk_id"),
            request.body,
        )
    )


@router.delete(r"smart-import/chunks/(?P<chunk_id>[^/]+)")
def delete_smart_import_chunk(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).delete_smart_chunk(
            match.group("chunk_id")
        )
    )


@router.post(r"smart-import/chunks/(?P<chunk_id>[^/]+)/parse")
def parse_smart_import_chunk(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).parse_smart_chunk(
            match.group("chunk_id")
        )
    )


@router.patch(r"smart-import/chunks/(?P<chunk_id>[^/]+)/parsed")
def patch_smart_import_chunk(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).patch_smart_chunk(
            match.group("chunk_id"),
            request.body.get("parsed") or {},
        )
    )


@router.get(r"smart-import/sessions/(?P<session_id>[^/]+)/preview")
def smart_import_preview(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).smart_preview(
            match.group("session_id")
        )
    )


@router.post(r"smart-import/sessions/(?P<session_id>[^/]+)/commit")
def commit_smart_import(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    store = _local_store(compatibility)
    session_id = match.group("session_id")
    state = _local_call(lambda: store.get_smart_session(session_id))
    project_id = str(state["session"].get("client_id") or "").strip()
    if not project_id:
        raise LocalRuntimeError(422, "smart_import_project_required", "请选择所属项目")
    preview = _local_call(lambda: store.smart_preview(session_id))
    substantive_fields = (
        "entities",
        "relationships",
        "events",
        "opinions",
        "commitments",
        "risk_signals",
        "open_questions",
    )
    if not any(preview.get(key) for key in substantive_fields):
        raise LocalRuntimeError(
            422,
            "smart_import_publishable_content_required",
            "智能导入尚未形成可发布的结构化内容，请先完成正文解析和审阅",
        )
    staged_files = list(state.get("staged_files") or [])
    document_mapping: dict[str, str] = {}
    documents_created = 0
    if staged_files:
        registered = compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
            "/materials/register-metadata",
            payload={
                "materials": [
                    {
                        "localSourceId": item["id"],
                        "fileName": item["original_filename"],
                        "contentHash": item["_content_hash"],
                        "byteSize": item["size_bytes"],
                        "mediaType": item["mime_type"],
                        "sourceKind": "smart_import_local_metadata",
                    }
                    for item in staged_files
                ]
            },
            idempotency_key=f"{request.idempotency_key}:materials",
        )
        document_mapping = {
            str(item.get("localSourceId") or ""): str(item.get("documentId") or "")
            for item in registered.get("documents") or []
        }
        _local_call(
            lambda: store.bind_cloud_documents(
                project_id=project_id,
                local_materials=[
                    {
                        "localSourceId": item["id"],
                        "localSummaryId": None,
                        "fileName": item["original_filename"],
                        "title": item["original_filename"],
                        "mediaType": item["mime_type"],
                        "contentHash": item["_content_hash"],
                        "byteSize": item["size_bytes"],
                        "updatedAt": item.get("upload_at"),
                    }
                    for item in staged_files
                ],
                cloud_documents=registered.get("documents") or [],
            )
        )
        documents_created = len(document_mapping)
    published = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}/smart-import/publish",
        payload={
            "title": state["session"].get("title") or "智能导入",
            "parsed": {
                key: preview.get(key) or []
                for key in (
                    "entities",
                    "relationships",
                    "events",
                    "opinions",
                    "commitments",
                    "risk_signals",
                    "files_classified",
                    "files_suggested_to_attach",
                    "open_questions",
                )
            },
        },
        idempotency_key=f"{request.idempotency_key}:publish",
    )
    _local_call(
        lambda: store.mark_smart_imported(
            session_id,
            document_ids=document_mapping,
        )
    )
    stats = dict(published.get("stats") or {})
    stats["documents_created"] = documents_created
    return {"ok": True, "stats": stats}


@router.post(
    r"clients/(?P<project_id>[^/]+)/documents/"
    r"(?P<document_id>[^/]+)/move-folder"
)
def move_document_folder(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).move_document(
            match.group("project_id"),
            match.group("document_id"),
            request.body,
        )
    )


@router.post(r"clients/(?P<project_id>[^/]+)/folders")
def create_folder(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).create_folder(
            match.group("project_id"),
            request.body,
        )
    )


@router.patch(r"clients/(?P<project_id>[^/]+)/folders/(?P<folder_id>[^/]+)")
def update_folder(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).update_folder(
            match.group("project_id"),
            match.group("folder_id"),
            request.body,
        )
    )


@router.delete(r"clients/(?P<project_id>[^/]+)/folders/(?P<folder_id>[^/]+)")
def delete_folder(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _local_call(
        lambda: _local_store(compatibility).delete_folder(
            match.group("project_id"),
            match.group("folder_id"),
        )
    )


@router.post(r"clients/(?P<project_id>[^/]+)/folders/apply-recommendation")
def apply_folder_recommendation(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    labels = list(request.body.get("targetFolderLabels") or [])
    if not labels:
        recommendation = compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
            "/folder-recommendation",
            payload={},
            idempotency_key=f"{request.idempotency_key}:recommend",
            refresh_business=False,
        )
        labels = [
            str(item.get("targetFolderLabel") or item.get("label") or "")
            for item in recommendation.get("folders") or []
            if str(item.get("targetFolderLabel") or item.get("label") or "")
        ]
    return _local_call(
        lambda: _local_store(compatibility).apply_folder_labels(
            project_id,
            labels,
        )
    )


@router.post(r"clients/(?P<project_id>[^/]+)/duplicate-documents/resolve")
def resolve_duplicate_documents(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    store = _local_store(compatibility)
    normalized = _local_call(
        lambda: store.preflight_duplicate_resolution(
            project_id,
            request.body,
        )
    )
    if normalized["action"] == "delete_others":
        targets = _local_call(
            lambda: store.duplicate_cloud_delete_targets(
                project_id, normalized["deleteV2DocumentIds"]
            )
        )
        for target in targets:
            cloud_document_id = target["cloudDocumentId"]
            try:
                preview = compatibility.runtime.cloud_query(
                    f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
                    f"/documents/{_segment(cloud_document_id)}/reading-preview"
                )
                compatibility.runtime.cloud_command(
                    "DELETE",
                    f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
                    f"/documents/{_segment(cloud_document_id)}",
                    payload={"expectedVersion": int(preview.get("aggregateVersion") or 1)},
                    idempotency_key=f"{request.idempotency_key}:{target['documentId']}",
                )
            except LocalRuntimeError as exc:
                if exc.status_code != 404:
                    raise
    result = _local_call(lambda: store.resolve_duplicates(project_id, normalized))
    return {
        **result,
        "localState": "ready",
        "cloudMetadataState": "ready",
        "overallState": "ready",
        "retryable": False,
        "message": "重复资料处置已完成",
    }


@router.patch(r"documents/(?P<document_id>[^/]+)/content")
def update_document_content(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    document_id = match.group("document_id")
    store = _local_store(compatibility)
    before = _local_call(lambda: store.document_text(document_id))
    project_id = str(before["projectId"])
    local = _local_call(
        lambda: store.update_document_text(
            document_id,
            title=str(request.body.get("title") or ""),
            content=str(request.body.get("content") or ""),
            expected_version=int(before["storageVersion"]),
            idempotency_key=request.idempotency_key,
        )
    )
    cloud_path = (
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
        f"/documents/{_segment(document_id)}/local-metadata"
    )

    def cloud_payload() -> dict[str, Any]:
        preview = compatibility.runtime.cloud_query(
            f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
            f"/documents/{_segment(document_id)}/reading-preview"
        )
        return {
                "expectedVersion": int(preview.get("aggregateVersion") or 1),
                "title": local["title"],
                "fileName": local["fileName"],
                "contentHash": local["contentHash"],
                "byteSize": local["byteSize"],
                "mediaType": local["mediaType"],
        }

    try:
        cloud = replayable_cloud_mutation(
            compatibility.runtime,
            idempotency_key=request.idempotency_key,
            command_type="project_materials.document_metadata_update",
            aggregate_type="knowledge_document",
            aggregate_id=document_id,
            method="PATCH",
            path=cloud_path,
            request_payload={
                "documentId": document_id,
                "title": str(request.body.get("title") or ""),
                "content": str(request.body.get("content") or ""),
            },
            cloud_payload_factory=cloud_payload,
        )
    except LocalRuntimeError as exc:
        duplicate = exc.code in {
            "duplicate_source_asset",
            "duplicate_document_content",
            "source_asset_content_duplicate",
        }
        version_conflict = exc.status_code == 409 and exc.code in {
            "version_conflict",
            "cas_conflict",
            "aggregate_version_conflict",
        }
        return {
            **local,
            "localState": "ready",
            "overallState": "partial",
            "cloudVersion": None,
            "cloudMetadataState": (
                "failed_retryable"
                if exc.status_code >= 500
                else "blocked"
            ),
            "cloudMetadataErrorCode": exc.code,
            "cloudMetadataMessage": (
                "本机正文已保存；当前项目已存在内容相同的资料，组织云未重复登记"
                if duplicate
                else "本机正文已保存；组织云资料版本已变化，请刷新后再保存一次"
                if version_conflict
                else "本机正文已保存；组织云元数据尚未更新，请重试"
            ),
            "retryable": exc.status_code >= 500 or version_conflict,
            "materialBoundary": {},
        }
    return {
        **local,
        "localState": "ready",
        "overallState": "ready",
        "cloudVersion": cloud.get("aggregateVersion") or cloud.get("version"),
        "cloudMetadataState": "ready",
        "retryable": False,
        "materialBoundary": cloud.get("materialBoundary") or {},
    }


@router.post(r"clients/(?P<project_id>[^/]+)/documents/ai-action")
def document_ai_action(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    _require_project_read(compatibility, project_id)
    content = str(
        request.body.get("selectionText")
        or request.body.get("content")
        or ""
    ).strip()
    if not content:
        raise LocalRuntimeError(422, "document_ai_content_required", "请选择或输入要处理的文字")
    action = str(request.body.get("action") or "rewrite_pro")
    action_labels = {
        "expand": "扩写，补足必要的解释和衔接",
        "rewrite_pro": "改写得专业、清晰、准确",
        "rewrite_short": "压缩为更简洁的表达",
        "summarize": "提炼摘要",
        "extract": "提取事实、行动项和关键结论",
        "translate": "在中文与英文之间翻译",
        "style_distilled": "保持事实不变并按用户要求调整文风",
        "insert_from_materials": "结合已提供的项目上下文补充内容",
        "rewrite_by_strategy": "按项目战略逻辑重写",
        "insert_data_table": "整理为清晰的 Markdown 表格",
    }
    instruction = action_labels.get(action)
    if instruction is None:
        raise LocalRuntimeError(422, "document_ai_action_invalid", "不支持的文档 AI 操作")
    user_request = str(request.body.get("userRequest") or "").strip()
    source_items: list[dict[str, Any]] = []
    source_context: list[str] = []
    store = _local_store(compatibility)
    source_context_budget = 48_000
    for document_id in [
        str(value)
        for value in request.body.get("workingDocumentIds") or []
        if str(value)
    ][:8]:
        local = _local_call(lambda document_id=document_id: store.document_text(document_id))
        if str(local.get("projectId") or "") != project_id:
            raise LocalRuntimeError(
                409,
                "local_document_project_mismatch",
                "引用资料不属于当前项目，请刷新后重试",
            )
        local_content = str(local.get("content") or "").strip()
        if not local_content:
            continue
        if source_context_budget <= 0:
            break
        selected_content = select_relevant_excerpt(
            local_content,
            user_request or content[:2_000],
            max_chars=min(8_000, source_context_budget),
        )
        source_context_budget -= len(selected_content)
        source_context.append(
            f"【{local.get('title') or document_id}】\n"
            f"{selected_content}"
        )
        source_items.append(
            {
                "type": "member_local_document",
                "title": local.get("title") or document_id,
                "snippet": local_content[:240],
                "refId": document_id,
                "extra": {
                    "contentHash": local.get("contentHash"),
                    "sourceScope": "local_private",
                    "fullContentChars": len(local_content),
                    "includedContentChars": len(selected_content),
                },
            }
        )
    active_skill_id = str(request.body.get("activeSkillId") or "").strip()
    writing_style = ""
    if active_skill_id:
        skills = compatibility.runtime.cloud_query(
            "/api/v2/workbench/libraries/writing_skill"
        )
        skill = next(
            (
                item
                for item in skills
                if str(item.get("id") or "") == active_skill_id
            ),
            None,
        )
        if skill is None:
            raise LocalRuntimeError(
                404,
                "writing_skill_missing",
                "选择的写作风格已不存在，请重新选择",
            )
        writing_style = str(skill.get("distilledMd") or "").strip()
        source_items.append(
            {
                "type": "writing_skill",
                "title": skill.get("name") or "写作风格",
                "snippet": writing_style[:240],
                "refId": active_skill_id,
            }
        )
    # GC-11 / GC-14：声明式 Agent Skill 与旧写作风格是两类上下文。
    # Skill 仍由组织云 automation_rules 权威保存；这里仅在当前编辑请求中读取，
    # 不复制正文、不创建第二套 Skill，也不把编辑结果升级为正式项目事实。
    from .workbench_outputs import _selected_style_or_agent_skill

    selected_agent_skill_ids = list(
        dict.fromkeys(
            str(value).strip()
            for value in request.body.get("activeSkillIds") or []
            if str(value).strip()
        )
    )[:5]
    agent_skill_context: list[str] = []
    for selected_id in selected_agent_skill_ids:
        _legacy_style, agent_skill = _selected_style_or_agent_skill(
            compatibility,
            selected_id,
        )
        if agent_skill is None:
            raise LocalRuntimeError(
                409,
                "agent_skill_kind_invalid",
                "智能编辑只能使用已启用的项目工作台 Skill",
            )
        short_name = str(agent_skill.get("shortName") or selected_id)
        rendered = str(agent_skill.get("renderedInstruction") or "").strip()
        if rendered:
            agent_skill_context.append(f"【Skill：{short_name}】\n{rendered}")
        source_items.append(
            {
                "type": "agent_skill",
                "title": short_name,
                "snippet": str(agent_skill.get("description") or rendered)[:240],
                "refId": selected_id,
                "extra": {
                    "version": int(agent_skill.get("version") or 1),
                    "contentHash": agent_skill.get("contentHash"),
                    "capabilityBoundary": "declarative_only",
                },
            }
        )

    thread_id = str(request.body.get("threadId") or "").strip()
    thread_context: list[str] = []
    if thread_id:
        history = compatibility.runtime.workbench_chat_history(project_id, thread_id)
        for answer in history[-3:]:
            question = str(answer.get("question") or "").strip()
            answer_text = str(answer.get("answerMarkdown") or "").strip()
            if question or answer_text:
                thread_context.append(
                    "用户：" + question[:1500] + "\n回答：" + answer_text[:3500]
                )
        if thread_context:
            source_items.append(
                {
                    "type": "workbench_thread_context",
                    "title": "当前工作台对话",
                    "snippet": f"最近 {len(thread_context)} 轮对话",
                    "refId": thread_id,
                    "extra": {"turnCount": len(thread_context)},
                }
            )
    started = perf_counter()
    result = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是益语智库的本机文档编辑助手。只处理用户明确提供的文字；"
            "只在下方明确提供引用资料时使用它们。保持事实边界，直接返回可替换的正文，"
            "不要解释处理过程。"
            + (
                "\n写作风格要求：\n" + writing_style[:6000]
                if writing_style
                else ""
            )
            + (
                "\n本次启用的声明式 Skill（必须按简称对应的规则执行）：\n"
                + "\n\n".join(agent_skill_context)
                if agent_skill_context
                else ""
            )
        ),
        prompt=(
            f"操作：{instruction}\n"
            + (f"补充要求：{user_request}\n" if user_request else "")
            + (
                "当前对话上下文：\n" + "\n\n".join(thread_context) + "\n"
                if thread_context
                else ""
            )
            + (
                "引用资料：\n" + "\n\n".join(source_context) + "\n"
                if source_context
                else ""
            )
            + f"原文：\n{content}"
        ),
        creativity_mode=str(
            request.body.get("creativityMode") or "balanced"
        ),
        # 智能编辑在专属120秒有界交互通道内运行；模型本身最多等待100秒，
        # 留出本机资料读取、上下文整理和响应回传余量，避免假504。
        read_timeout_seconds=100.0,
    )
    return {
        "content": result["content"],
        "action": action,
        "durationMs": int((perf_counter() - started) * 1000),
        "sources": source_items,
        "effectiveCreativity": request.body.get("creativityMode")
        or "balanced",
        "targetScope": (
            "selection" if request.body.get("selectionText") else "cursor_insert"
        ),
        "sourceScope": result["sourceScope"],
        "persistedToOrganizationCloud": False,
        "activeSkillId": active_skill_id or None,
        "activeSkillIds": selected_agent_skill_ids,
        "threadId": thread_id or None,
        "reportArtifactId": request.body.get("reportArtifactId"),
        "reportArtifactVersion": request.body.get("reportArtifactVersion"),
        "reportSourceSetId": request.body.get("reportSourceSetId"),
    }


@router.post(r"clients/(?P<project_id>[^/]+)/documents/fill-template/start")
def start_template_fill(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    project = _project_detail(compatibility, project_id)
    values = {
        "项目名称": project.get("name") or "",
        "项目别名": project.get("alias") or "",
        "项目简介": project.get("summary") or "",
        "项目领域": project.get("domain") or "",
    }
    return _local_call(
        lambda: _local_store(compatibility).start_template_fill(
            project_id,
            template_path=str(request.body.get("templatePath") or ""),
            values=values,
            idempotency_key=request.idempotency_key,
        )
    )


@router.post(r"clients/(?P<project_id>[^/]+)/feishu-doc-import/import")
def import_feishu_documents(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    project_id = match.group("project_id")
    _project_detail(compatibility, project_id)
    items = request.body.get("items")
    if not isinstance(items, list) or not items:
        raise LocalRuntimeError(
            422,
            "feishu_document_import_items_required",
            "请选择要导入的飞书文档",
        )
    organization = _platform_resource(
        compatibility,
        resource_path="org-integrations/feishu",
        authorization_scope="organization",
    )
    if str(organization.get("state") or "") != "ready":
        raise LocalRuntimeError(
            409,
            "feishu_organization_application_required",
            str(
                organization.get("lastValidationMessage")
                or "组织飞书应用尚未配置或验证未通过"
            ),
        )
    member = _platform_resource(
        compatibility,
        resource_path="me/feishu-authorization",
        authorization_scope="personal",
    )
    if not bool(member.get("linked")):
        authorization_message = str(member.get("lastError") or "").strip()
        if not authorization_message:
            authorization_message = (
                "当前成员飞书 OAuth 回调与加密用户令牌权威尚未接通"
                if member.get("blockedReason")
                == "oauth_grant_authority_not_connected"
                else str(
                    member.get("blockedReason")
                    or "当前成员尚未完成飞书授权"
                )
            )
        raise LocalRuntimeError(
            409,
            "feishu_member_authorization_required",
            authorization_message,
        )
    fetched = _platform_command(
        compatibility,
        resource_path="feishu-doc-import/fetch",
        authorization_scope="personal",
        payload={
            "items": [
                {
                    "token": str(item.get("token") or ""),
                    "type": str(item.get("type") or ""),
                    "title": str(item.get("title") or "飞书文档"),
                    "url": str(item.get("url") or ""),
                }
                for item in items
                if isinstance(item, Mapping)
            ]
        },
        idempotency_key=f"{request.idempotency_key}:fetch",
    )
    fetched_items = [
        item
        for item in fetched.get("items") or []
        if isinstance(item, Mapping)
        and str(item.get("content") or "").strip()
    ]
    failures = [
        {
            "token": str(item.get("token") or ""),
            "title": str(item.get("title") or "飞书文档"),
            "status": "failed",
            "documentId": None,
            "fileName": None,
            "path": None,
            "remoteUrl": str(item.get("url") or ""),
            "message": str(item.get("message") or "飞书文档读取失败"),
        }
        for item in fetched.get("failedItems") or []
        if isinstance(item, Mapping)
    ]
    if not fetched_items:
        return {
            "clientId": project_id,
            "importedCount": 0,
            "failedCount": len(failures),
            "items": failures,
            "state": str(fetched.get("state") or "failed_retryable"),
            "message": str(
                fetched.get("message") or "所选飞书文档均未能读取"
            ),
        }
    store = _local_store(compatibility)
    local_materials: list[dict[str, Any]] = []
    fetched_by_local_source: dict[str, Mapping[str, Any]] = {}
    for item in fetched_items:
        try:
            local = _local_call(
                lambda item=item: store.import_text(
                    project_id=project_id,
                    title=str(item.get("title") or "飞书文档"),
                    content=str(item.get("content") or ""),
                    idempotency_key=(
                        f"{request.idempotency_key}:local:"
                        + sha256_text(
                            f"{item.get('type')}:{item.get('token')}"
                        )[:24]
                    ),
                )
            )
        except LocalRuntimeError as exc:
            failures.append(
                {
                    "token": str(item.get("token") or ""),
                    "title": str(item.get("title") or "飞书文档"),
                    "status": "failed",
                    "documentId": None,
                    "fileName": None,
                    "path": None,
                    "remoteUrl": str(item.get("url") or ""),
                    "message": exc.message,
                }
            )
            continue
        local_materials.append(local)
        fetched_by_local_source[str(local["localSourceId"])] = item
    if not local_materials:
        return {
            "clientId": project_id,
            "importedCount": 0,
            "failedCount": len(failures),
            "items": failures,
            "state": "blocked",
            "message": "飞书正文已读取，但未能保存到当前设备",
        }
    registered = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
        "/materials/register-metadata",
        payload={
            "materials": [
                {
                    "localSourceId": local["localSourceId"],
                    "fileName": local["fileName"],
                    "contentHash": local["contentHash"],
                    "byteSize": local["byteSize"],
                    "mediaType": local["mediaType"],
                    "sourceKind": "feishu_member_local_metadata",
                }
                for local in local_materials
            ]
        },
        idempotency_key=f"{request.idempotency_key}:metadata",
    )
    cloud_documents = list(registered.get("documents") or [])
    if len(cloud_documents) != len(local_materials):
        raise LocalRuntimeError(
            502,
            "feishu_import_metadata_result_invalid",
            "组织云没有完整返回飞书资料元数据",
        )
    _local_call(
        lambda: store.bind_cloud_documents(
            project_id=project_id,
            local_materials=local_materials,
            cloud_documents=cloud_documents,
        )
    )
    cloud_by_local = {
        str(document.get("localSourceId") or ""): document
        for document in cloud_documents
    }
    imported_items: list[dict[str, Any]] = []
    for local in local_materials:
        source_id = str(local["localSourceId"])
        remote = fetched_by_local_source[source_id]
        document = cloud_by_local.get(source_id) or {}
        document_id = str(document.get("documentId") or "")
        summary_state = "not_connected"
        summary_message = ""
        if document_id:
            try:
                completion = compatibility.runtime.private_ai_completion(
                    system_prompt=(
                        "你是项目资料摘要器。只根据当前成员设备上的飞书文档"
                        "正文生成可供本组织共享的中文项目背景摘要；保留主体、"
                        "事实、时间、承诺、风险和待办，不补造信息。"
                    ),
                    prompt=str(remote.get("content") or "")[:120_000],
                    creativity_mode="strict",
                )
                summary = str(completion.get("content") or "").strip()
                if not summary:
                    raise LocalRuntimeError(
                        502,
                        "feishu_import_summary_empty",
                        "组织模型没有生成可发布摘要",
                    )
                _local_call(
                    lambda: store.update_ai_summary(
                        document_id,
                        summary=summary,
                        model_name=str(
                            completion.get("modelName")
                            or "organization_default"
                        ),
                    )
                )
                compatibility.runtime.cloud_command(
                    "POST",
                    f"{_CLOUD_ROOT}/projects/{_segment(project_id)}"
                    f"/documents/{_segment(document_id)}"
                    "/publish-local-summary",
                    payload={
                        "expectedVersion": int(document.get("version") or 1),
                        "sourceContentHash": local["contentHash"],
                        "summary": summary[:4000],
                        "generatorVersion": str(
                            completion.get("modelName")
                            or "organization_default"
                        ),
                    },
                    idempotency_key=(
                        f"{request.idempotency_key}:summary:{document_id}"
                    ),
                    refresh_business=False,
                )
                summary_state = "ready"
            except LocalRuntimeError as exc:
                summary_state = (
                    "failed_retryable"
                    if exc.status_code >= 500
                    else "blocked"
                )
                summary_message = (
                    "正文已保存在当前设备；组织共享摘要未生成："
                    + exc.message
                )
            _platform_command(
                compatibility,
                resource_path="feishu-doc-import/register-mapping",
                authorization_scope="personal",
                payload={
                    "documentId": document_id,
                    "remoteId": str(remote.get("token") or ""),
                    "remoteType": str(remote.get("type") or "docx"),
                    "remoteUrl": str(remote.get("url") or ""),
                },
                idempotency_key=(
                    f"{request.idempotency_key}:mapping:{document_id}"
                ),
            )
        imported_items.append(
            {
                "token": str(remote.get("token") or ""),
                "title": str(remote.get("title") or local["title"]),
                "status": "imported",
                "documentId": document_id or None,
                "fileName": local["fileName"],
                "path": local["managedPath"],
                "remoteUrl": str(remote.get("url") or ""),
                "message": summary_message,
                "sharedKnowledgeState": summary_state,
            }
        )
    return {
        "clientId": project_id,
        "importedCount": len(imported_items),
        "failedCount": len(failures),
        "items": [*imported_items, *failures],
        "state": "ready" if not failures else "partial",
        "message": (
            f"已在当前设备保存 {len(imported_items)} 份飞书文档"
            + (f"，{len(failures)} 份失败" if failures else "")
        ),
        "materialBoundary": registered.get("materialBoundary") or {},
    }


_UNSUPPORTED_ROUTE_SPECS: tuple[tuple[str, str, str], ...] = ()


def _unsupported_handler(reason: str):
    def handler(
        compatibility: Any,
        request: UiRequest,
        _: Any,
    ) -> Any:
        compatibility._not_connected(f"{request.path}（{reason}）")

    return handler


for _method, _pattern, _reason in _UNSUPPORTED_ROUTE_SPECS:
    router.route(_method, _pattern)(_unsupported_handler(_reason))
