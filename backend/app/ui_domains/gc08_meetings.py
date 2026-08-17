"""Detached GC-08 UI router; the integration thread owns registry wiring."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Mapping

from strict_common.ids import sha256_text, utc_now

from ..gc08_meetings import (
    GC08BlockedError,
    GC08DomainError,
    GC08LocalContext,
    GC08LocalMeetingRepository,
    GC08RetryableError,
)
from ..project_materials_local import LocalProjectMaterialsRepository
from ..local_asr.models import SENSE_VOICE_MODEL, model_ready
from ..local_asr.subprocess_runner import run_local_asr_subprocess
from ..runtime import LocalRuntimeError
from ..transcript_semantic_correction import correct_project_transcript
from .project_materials import register_and_process_local_materials
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("gc08_meeting_media", pin_workspace=True)

_TRANSCRIPTION_JOBS_LOCK = threading.Lock()
_TRANSCRIPTION_JOBS: dict[str, dict[str, Any]] = {}


def _transcription_job_key(
    runtime: Any,
    sandbox_id: str,
    client_id: str,
    meeting_id: str,
    recording_id: str,
) -> str:
    return "|".join(
        (
            str(Path(runtime.database_path).resolve()),
            sandbox_id,
            client_id,
            meeting_id,
            recording_id,
        )
    )


def _set_transcription_job(key: str, **changes: Any) -> dict[str, Any]:
    with _TRANSCRIPTION_JOBS_LOCK:
        current = dict(_TRANSCRIPTION_JOBS.get(key) or {})
        current.update(changes)
        current["updatedAt"] = utc_now()
        _TRANSCRIPTION_JOBS[key] = current
        return dict(current)


def _transcription_job(key: str) -> dict[str, Any] | None:
    with _TRANSCRIPTION_JOBS_LOCK:
        value = _TRANSCRIPTION_JOBS.get(key)
        return dict(value) if value else None


def _with_transcription_progress(
    detail: Mapping[str, Any],
    key: str,
) -> dict[str, Any]:
    result = dict(detail)
    job = _transcription_job(key)
    if job is None:
        transcription = dict(result.get("transcription") or {})
        if str(transcription.get("status") or "") == "processing":
            result["transcription"] = {
                **transcription,
                "status": "failed_retryable",
                "errorCode": "local_asr_process_interrupted",
                "message": "上次转写因应用重启而中断，可以重新转写。",
                "retryable": True,
            }
        return result
    result["transcriptionProgress"] = job
    if str(job.get("status") or "") in {"queued", "processing"}:
        result["transcription"] = {
            **dict(result.get("transcription") or {}),
            "status": "processing",
            "message": str(job.get("stage") or "正在转写"),
            "retryable": False,
        }
    return result


def _meeting_record(compatibility: Any, meeting_id: str) -> Mapping[str, Any]:
    meetings = compatibility.runtime.cloud_query("/api/v2/gc06/meetings")
    if not isinstance(meetings, list):
        meetings = meetings.get("meetings") or []
    meeting = next(
        (item for item in meetings if str(item.get("id") or "") == meeting_id),
        None,
    )
    if not isinstance(meeting, Mapping):
        raise LocalRuntimeError(404, "meeting_missing", "会议不存在或当前成员无权查看")
    return meeting


_MEETING_GENERIC_TERMS = {
    "会议", "客户", "项目", "讨论", "沟通", "协作", "推进", "测试", "验收",
    "基金会", "当前", "相关", "安排", "日慈",
}


def _meeting_terms(meeting: Mapping[str, Any]) -> list[str]:
    text = f"{meeting.get('title') or ''}\n{meeting.get('agenda') or ''}".casefold()
    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,24}", text):
        if token.isdigit() or token in _MEETING_GENERIC_TERMS:
            continue
        terms.add(token)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            for size in (2, 3, 4):
                for index in range(max(0, len(token) - size + 1)):
                    part = token[index : index + size]
                    if part not in _MEETING_GENERIC_TERMS:
                        terms.add(part)
    return sorted(terms, key=lambda value: (-len(value), value))[:64]


def _meeting_sources(context: Mapping[str, Any], meeting: Mapping[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    terms = _meeting_terms(meeting)
    rows = [
        *list(context.get("savedMemories") or []),
        *list(context.get("officialWebsiteFacts") or []),
        *list(context.get("organizationSharedKnowledge") or []),
    ]
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            continue
        summary = re.sub(
            r"\s+",
            " ",
            str(raw.get("summary") or raw.get("statement") or raw.get("content") or ""),
        ).strip()[:420]
        if not summary or summary.casefold() in seen:
            continue
        seen.add(summary.casefold())
        title = str(
            raw.get("sourceDescription")
            or raw.get("title")
            or raw.get("sourceType")
            or "项目知识"
        ).strip()[:180]
        corpus = f"{title}\n{summary}".casefold()
        kind = str(raw.get("sourceKind") or raw.get("sourceType") or "").casefold()
        score = 50 if kind in {"answer_correction", "answer_remember", "strategic_profile_clarification"} else 30 if "official" in kind or "website" in kind else 20
        matches = [term for term in terms if term in corpus]
        score += sum(min(18, 3 + len(term) * 2) for term in matches)
        ranked.append((score, index, {"title": title, "summary": summary, "matches": matches}))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected: list[dict[str, Any]] = []
    title_counts: dict[str, int] = {}
    for _, _, item in ranked:
        key = item["title"].casefold()
        if title_counts.get(key, 0) >= 2:
            continue
        selected.append(item)
        title_counts[key] = title_counts.get(key, 0) + 1
        if len(selected) >= 8:
            break
    relationship_clear = bool(terms) and any(item["matches"] for item in selected)
    return selected, relationship_clear


def _meeting_fallback_brief(
    meeting: Mapping[str, Any],
    sources: list[Mapping[str, Any]],
    relationship_clear: bool,
) -> str:
    project = str(meeting.get("clientName") or "当前项目").strip()
    facts = [re.sub(r"\s+", " ", str(item.get("summary") or "")).strip(" 。；")[:180] for item in sources[:3]]
    facts = [item for item in facts if item]
    if not facts:
        return "当前会议尚无足够的正式项目知识可用于梳理前情。"
    joined = "；".join(facts)
    if relationship_clear:
        return f"结合会议主题和议程，当前最相关的项目背景是：{joined}。会议讨论应以这些已核实信息为边界。"
    return f"会议主题和议程尚不足以判断具体业务关系，先提供{project}的通用背景：{joined}。补充议题后，前情会进一步聚焦。"


@router.get(r"meetings/(?P<meeting_id>[^/]+)/context-brief")
def meeting_context_brief(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    meeting_id = match.group("meeting_id")
    meeting = _meeting_record(compatibility, meeting_id)
    client_id = str(meeting.get("clientId") or "")
    _require_project(compatibility, client_id)
    context = compatibility.runtime.project_knowledge_context(client_id)
    sources, relationship_clear = _meeting_sources(context, meeting)
    quality_flags: list[str] = []
    if len(sources) < 2:
        quality_flags.append("thin_context")
    if sources and not relationship_clear:
        quality_flags.append("general_context")
    brief = _meeting_fallback_brief(meeting, sources, relationship_clear)
    generation_model = "deterministic-authority-brief-v2"
    if sources:
        payload = {
            "title": str(meeting.get("title") or ""),
            "agenda": str(meeting.get("agenda") or ""),
            "project": str(meeting.get("clientName") or ""),
            "relationshipMode": "meeting_specific" if relationship_clear else "general_project_context",
            "evidence": [
                {"source": item["title"], "content": item["summary"]}
                for item in sources
            ],
        }
        try:
            completion = compatibility.runtime.organization_ai_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是益语智库的会议纪要Agent。请只依据提供的项目证据，结合会议标题和议程，"
                            "梳理一段便于参会者进入状态的前情提要。不要逐条罗列事实，要说明已有脉络、"
                            "本次会议可能承接的背景和事实边界。若relationshipMode为general_project_context，"
                            "明确会议信息不足以判断具体关系，再概括通用项目背景；不得虚构议题。"
                            "输出2至3个短段落、约120至260个中文字符，不使用标题或项目符号，"
                            "不引入证据之外的人名、数字或结论。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)},
                ],
                temperature=0.1,
                read_timeout_seconds=30.0,
            )
            generated = str(completion.get("content") or "").strip()
            generated = re.sub(r"^```(?:markdown|text)?\s*|\s*```$", "", generated, flags=re.IGNORECASE)
            generated = re.sub(r"(?m)^\s*[-*•]\s+", "", generated).strip()[:1200]
            if generated:
                brief = generated
                generation_model = str(dict(completion.get("provider") or {}).get("modelName") or "organization-model")
        except LocalRuntimeError as exc:
            quality_flags.append("model_failed_retryable" if exc.status_code >= 500 else "model_blocked")
    return {
        "id": f"meeting-brief-{meeting_id}",
        "meetingId": meeting_id,
        "clientId": client_id,
        "eventLineId": meeting.get("eventLineId"),
        "brief": brief,
        "shouldDisplay": bool(brief),
        "materialPackHash": sha256_text(json.dumps({"meeting": dict(meeting), "sources": sources}, ensure_ascii=False, sort_keys=True, default=str)),
        "usedProjectSignals": list(dict.fromkeys(str(item["title"]) for item in sources)),
        "materialBoundary": {
            "sourceFileContentIncluded": False,
            "sourceFilePathsIncluded": False,
            "storageLocatorsIncluded": False,
            "unpublishedDocumentContentIncluded": False,
        },
        "qualityFlags": quality_flags,
        "generationModel": generation_model,
        "generationPromptVersion": "gc08-meeting-context-v1",
        "updatedAt": utc_now(),
    }


@router.get(r"meetings/(?P<meeting_id>[^/]+)/page-context")
def meeting_page_context(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    meeting_id = match.group("meeting_id")
    meeting = _meeting_record(compatibility, meeting_id)
    client_id = str(meeting.get("clientId") or "")
    _require_project(compatibility, client_id)
    context = compatibility.runtime.project_knowledge_context(client_id)
    task_board = compatibility.runtime.cloud_query("/api/v2/domain/tasks")
    tasks = [
        dict(item)
        for item in task_board.get("tasks") or []
        if str(item.get("client_id") or item.get("clientId") or "") == client_id
    ]
    shared = list(context.get("organizationSharedKnowledge") or [])
    website = list(context.get("officialWebsiteFacts") or [])
    sources = [*shared, *website]
    evidence = [
        {
            "sourceType": str(item.get("sourceType") or item.get("type") or "project_knowledge"),
            "sourceId": str(item.get("id") or item.get("documentId") or ""),
            "title": str(item.get("title") or item.get("statement") or "项目知识"),
            "summary": str(item.get("summary") or item.get("statement") or ""),
            "authorityLevel": "strict_v2",
        }
        for item in sources
        if isinstance(item, Mapping)
    ]
    missing = [] if evidence else ["project_knowledge"]
    return {
        "page": "meeting",
        "scopeType": "meeting",
        "scopeId": meeting_id,
        "clientId": client_id,
        "intent": "status_brief",
        "officialJudgments": [],
        "candidateJudgments": [],
        "overlayJudgments": [],
        "evidenceCards": evidence,
        "rawEvidence": [],
        "openQuestions": [],
        "conflicts": [],
        "themeClusters": [],
        "relatedTasks": tasks,
        "relatedMeetings": [dict(meeting)],
        "relatedDocuments": shared,
        "notebookSummary": None,
        "memoryFacts": [],
        "contextPack": {"meeting": dict(meeting), "projectKnowledge": context},
        "judgmentBundle": None,
        "resolutionTrace": {"authority": "strict_v2", "legacyRead": False, "fallback": False},
        "stateProjection": None,
        "missingContext": missing,
        "boundaryNotes": ["会议上下文来自组织云正式会议与当前项目正式知识。"],
        "sourceSummary": {"projectKnowledge": len(evidence)},
        "answerPolicy": {
            "canAnswer": bool(evidence),
            "answerLevel": "evidence_grounded" if evidence else "insufficient_evidence",
            "mustDiscloseCandidateBoundary": True,
            "mustUseRawEvidence": False,
            "shouldCreateProposal": False,
            "fallbackToLegacyRetrieval": False,
            "reason": "strict_meeting_context",
        },
        "retrievalPlan": {"mode": "strict_project_knowledge", "sourceCount": len(evidence)},
        "quality": {"score": min(1, len(evidence) / 3), "complete": bool(evidence), "missingCount": len(missing)},
        "routeDecision": None,
        "retrievalTrace": None,
    }


def _context_provider(runtime: Any):
    def provide() -> GC08LocalContext:
        current = runtime._current_context(require_ready=True)  # noqa: SLF001
        with runtime._connection() as connection:  # noqa: SLF001
            scope_id = runtime._local_object_scope_id(  # noqa: SLF001
                connection,
                current.sandbox_id,
            )
        return GC08LocalContext(
            scope_id=scope_id,
            sandbox_id=current.sandbox_id,
            principal_id=current.principal_id,
            membership_id=current.membership_id,
            origin_instance_id=runtime.identity.database_generation_id,
        )

    return provide


def _transcription_runner(runtime: Any, *, project_id: str = ""):
    model_root = runtime.database_path.parent / "models"

    def run(path: Path, language: str, progress_callback: Any = None) -> Any:
        if not model_ready(model_root, SENSE_VOICE_MODEL):
            raise GC08BlockedError(
                424,
                "local_asr_not_connected",
                "本机 ASR 模型未就绪；录音已保留，可下载模型后重试",
            )
        try:
            output = run_local_asr_subprocess(
                model_root=model_root,
                audio_path=path,
                language=language,
                progress_callback=progress_callback,
            )
            if project_id:
                raw_text = str(
                    output.get("dialogue_text")
                    or output.get("dialogueText")
                    or output.get("text")
                    or ""
                ).strip()
                if raw_text:
                    corrected = correct_project_transcript(
                        runtime,
                        project_id=project_id,
                        title=path.name,
                        transcript=raw_text,
                        progress_callback=progress_callback,
                    )
                    output = dict(output)
                    output["dialogue_text"] = corrected
                    output["dialogueText"] = corrected
                    output["text"] = corrected
            return output
        except GC08DomainError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise GC08RetryableError(
                503,
                "local_asr_execution_failed",
                f"本机 ASR 暂时失败：{exc.__class__.__name__}",
            ) from exc

    return run


def _minutes_runner(runtime: Any):
    def run(transcript: str, title: str) -> Any:
        try:
            return runtime.private_ai_completion(
                system_prompt=(
                    "你是益语智库会议纪要助手。只能依据本轮完整转写，"
                    "不得补写未出现的事实。返回严格 JSON："
                    '{"title":"会议标题","minutesMarkdown":"Markdown纪要",'
                    '"citations":[{"locatorKind":"char_range",'
                    '"locator":"char:起点-终点"}],'
                    '"actionCandidates":[{"title":"待办标题","description":"依据",'
                    '"dueDate":"YYYY-MM-DD或空字符串","ownerHint":"姓名或空字符串"}]}。'
                    "行动、承诺和日期只能列为待确认候选，不得宣称已经创建任务。"
                ),
                prompt=f"会议标题：{title}\n完整转写：\n{transcript}",
                read_timeout_seconds=120.0,
            )
        except LocalRuntimeError as exc:
            error_type = GC08RetryableError if exc.status_code >= 500 else GC08BlockedError
            raise error_type(exc.status_code, exc.code, exc.message) from exc

    return run


def _repository(compatibility: Any) -> GC08LocalMeetingRepository:
    runtime = compatibility.runtime
    return GC08LocalMeetingRepository(
        runtime.database_path,
        runtime.database_path.parent / "recordings",
        _context_provider(runtime),
        transcription_runner=_transcription_runner(runtime),
        minutes_runner=_minutes_runner(runtime),
    )


_RECORDING_SUFFIXES = {".m4a", ".mp3", ".wav", ".aac", ".flac", ".ogg", ".webm", ".mp4", ".mov"}


def _import_selected_recording(runtime: Any, value: str) -> Path:
    """Copy an explicitly selected recording into the strict managed root."""
    source = Path(value).expanduser().resolve()
    if not source.is_file():
        raise LocalRuntimeError(404, "recording_file_missing", "选择的本机录音文件不存在")
    if source.suffix.lower() not in _RECORDING_SUFFIXES:
        raise LocalRuntimeError(422, "recording_format_unsupported", "该录音格式暂不支持")
    recordings_root = runtime.database_path.parent / "recordings"
    resolved_root = recordings_root.resolve()
    if resolved_root in source.parents:
        return source
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    safe_name = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+", "_", source.stem).strip("._")
    target = resolved_root / "imports" / f"{digest.hexdigest()[:24]}-{safe_name or 'recording'}{source.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    return target


def _call(operation: Any) -> Any:
    try:
        return operation()
    except GC08DomainError as exc:
        raise LocalRuntimeError(exc.status_code, exc.code, exc.message) from exc


def _require_project(compatibility: Any, client_id: str) -> None:
    compatibility.runtime.require_project_capability(client_id, "read")


def _publish_current_minutes(
    compatibility: Any,
    request: UiRequest,
    *,
    repository: GC08LocalMeetingRepository,
    client_id: str,
    meeting_id: str,
    recording_id: str,
    expected_version: int,
) -> dict[str, Any]:
    payload = _call(
        lambda: repository.cloud_publication_payload(
            client_id=client_id,
            meeting_id=meeting_id,
            recording_id=recording_id,
            expected_version=expected_version,
        )
    )
    result = compatibility.runtime.cloud_command(
        "POST",
        f"/api/v2/domain/gc08/projects/{client_id}/meetings/{meeting_id}/minutes",
        payload=payload,
        idempotency_key=request.idempotency_key,
    )
    local = _call(
        lambda: repository.record_cloud_publication(
            client_id=client_id,
            meeting_id=meeting_id,
            recording_id=recording_id,
            cloud_version=int(result.get("version") or 1),
            cloud_instance_id=str(
                compatibility.runtime._current_context(  # noqa: SLF001
                    require_ready=True
                ).cloud_instance_id
            ),
        )
    )
    return {"state": "published", "cloud": result, "local": local, "agentRun": result.get("agentRun")}


@router.post(
    r"clients/(?P<client_id>[^/]+)/meetings/(?P<meeting_id>[^/]+)/recordings"
)
def register_recording(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    client_id = match.group("client_id")
    _require_project(compatibility, client_id)
    selected_audio_path = str(request.body.get("audioPath") or "").strip()
    if not selected_audio_path:
        raise LocalRuntimeError(422, "audio_path_required", "录音文件路径不能为空")
    audio_path = _import_selected_recording(
        compatibility.runtime,
        selected_audio_path,
    )
    return _call(
        lambda: _repository(compatibility).register_recording(
            client_id=client_id,
            meeting_id=match.group("meeting_id"),
            audio_path=audio_path,
            original_file_name=Path(selected_audio_path).name,
            recording_id=str(request.body.get("recordingId") or "").strip() or None,
            duration_ms=int(request.body.get("durationMs") or 0) or None,
            captured_at=str(request.body.get("capturedAt") or "").strip() or None,
            device_id=str(request.body.get("deviceId") or "").strip() or None,
        )
    )


@router.get(
    r"clients/(?P<client_id>[^/]+)/meetings/(?P<meeting_id>[^/]+)/recordings"
)
def latest_recording(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any] | None:
    client_id = match.group("client_id")
    _require_project(compatibility, client_id)
    detail = _call(
        lambda: _repository(compatibility).latest_recording_detail(
            client_id=client_id,
            meeting_id=match.group("meeting_id"),
        )
    )
    if detail is None:
        return None
    current = compatibility.runtime._current_context(require_ready=True)  # noqa: SLF001
    key = _transcription_job_key(
        compatibility.runtime,
        current.sandbox_id,
        client_id,
        match.group("meeting_id"),
        str(detail.get("recordingId") or ""),
    )
    return _with_transcription_progress(detail, key)


@router.get(
    r"clients/(?P<client_id>[^/]+)/meetings/(?P<meeting_id>[^/]+)/materials"
)
def meeting_materials(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> list[dict[str, Any]]:
    client_id = match.group("client_id")
    _require_project(compatibility, client_id)
    return _call(
        lambda: LocalProjectMaterialsRepository(
            compatibility.runtime
        ).meeting_materials(
            project_id=client_id,
            meeting_id=match.group("meeting_id"),
        )
    )


@router.post(
    r"clients/(?P<client_id>[^/]+)/meetings/(?P<meeting_id>[^/]+)/recordings/"
    r"(?P<recording_id>[^/]+)/transcriptions"
)
def transcribe(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    client_id = match.group("client_id")
    meeting_id = match.group("meeting_id")
    recording_id = match.group("recording_id")
    _require_project(compatibility, client_id)
    # 转写只产生当前设备上的正式转写版本。纪要生成与组织云发布是
    # 后续明确动作，不能因后续云端闸门失败而把已成功的本机转写
    # 伪装成失败，也不能在用户只点击“开始转写”时静默发布纪要。
    runtime = compatibility.runtime
    pinned_sandbox = runtime.capture_sandbox_context()
    pinned_workspace_context = pinned_sandbox.workspace_context
    if pinned_workspace_context is None:
        raise LocalRuntimeError(409, "workspace_not_ready", "当前组织工作空间尚未就绪")
    pinned_context = _context_provider(runtime)()
    requested_language = str(request.body.get("language") or "auto")
    requested_force = bool(request.body.get("force"))
    key = _transcription_job_key(
        runtime,
        pinned_context.sandbox_id,
        client_id,
        meeting_id,
        recording_id,
    )
    current_job = _transcription_job(key)
    repository = GC08LocalMeetingRepository(
        runtime.database_path,
        runtime.database_path.parent / "recordings",
        lambda: pinned_context,
        transcription_runner=_transcription_runner(runtime, project_id=client_id),
        minutes_runner=_minutes_runner(runtime),
    )
    if not current_job or str(current_job.get("status") or "") not in {
        "queued",
        "processing",
    }:
        _set_transcription_job(
            key,
            percent=2,
            stage="等待本机转写",
            status="queued",
            retryable=False,
        )

        def run_in_background() -> None:
            def report(percent: int, stage: str) -> None:
                _set_transcription_job(
                    key,
                    percent=percent,
                    stage=stage,
                    status="processing",
                    retryable=False,
                )

            try:
                with runtime.prebound_sandbox_context(pinned_sandbox):
                    result = repository.transcribe(
                        client_id=client_id,
                        meeting_id=meeting_id,
                        recording_id=recording_id,
                        language=requested_language,
                        force=requested_force,
                        progress_callback=report,
                    )
                transcript_path = str(
                    (result.get("localFiles") or {}).get("transcriptionPath") or ""
                ).strip()
                archive_status = "not_applicable"
                if transcript_path and str(
                    (result.get("transcription") or {}).get("status") or ""
                ) == "ready":
                    try:
                        report(99, "加入项目工作台")
                        material_store = LocalProjectMaterialsRepository(
                            runtime,
                            context_provider=lambda: pinned_workspace_context,
                        )
                        imported = material_store.import_paths(
                            project_id=client_id,
                            mode="file",
                            paths=[transcript_path],
                            idempotency_key=(
                                f"meeting-transcript:{recording_id}:"
                                f"{(result.get('transcription') or {}).get('version') or 1}"
                            ),
                        )
                        operation_key = (
                            f"meeting-transcript:{recording_id}:"
                            f"{(result.get('transcription') or {}).get('version') or 1}"
                        )
                        with runtime.prebound_sandbox_context(pinned_sandbox):
                            settled = register_and_process_local_materials(
                                runtime=runtime,
                                store=material_store,
                                project_id=client_id,
                                local_materials=imported["materials"],
                                relation_kind="meeting",
                                relation_id=meeting_id,
                                idempotency_key=operation_key,
                            )
                            material_store.bind_meeting_materials(
                                project_id=client_id,
                                meeting_id=meeting_id,
                                local_materials=imported["materials"],
                            )
                        archive_status = str(settled.get("overallState") or "failed_retryable")
                    except Exception:  # noqa: BLE001
                        archive_status = "failed_retryable"
                terminal = str(
                    (result.get("transcription") or {}).get("status") or "failed_retryable"
                )
                _set_transcription_job(
                    key,
                    percent=100 if terminal == "ready" else int(
                        (_transcription_job(key) or {}).get("percent") or 0
                    ),
                    stage=(
                        "转写完成"
                        if terminal == "ready"
                        else str((result.get("transcription") or {}).get("message") or "转写未完成")
                    ),
                    status="completed" if terminal == "ready" else terminal,
                    retryable=terminal == "failed_retryable",
                    workbenchArchiveState=archive_status,
                )
            except Exception as exc:  # noqa: BLE001
                _set_transcription_job(
                    key,
                    stage=f"转写失败：{exc.__class__.__name__}",
                    status="failed_retryable",
                    retryable=True,
                )

        threading.Thread(
            target=run_in_background,
            name=f"gc08-asr-{recording_id[-12:]}",
            daemon=True,
        ).start()
    detail = _call(
        lambda: repository.recording_detail(
            client_id=client_id,
            meeting_id=meeting_id,
            recording_id=recording_id,
        )
    )
    return _with_transcription_progress(detail, key)


@router.post(
    r"clients/(?P<client_id>[^/]+)/meetings/(?P<meeting_id>[^/]+)/recordings/"
    r"(?P<recording_id>[^/]+)/minutes/draft"
)
def create_minutes_draft(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    client_id = match.group("client_id")
    _require_project(compatibility, client_id)
    raw_citations = request.body.get("citations")
    citations = (
        [item for item in raw_citations if isinstance(item, Mapping)]
        if isinstance(raw_citations, list)
        else []
    )
    return _call(
        lambda: _repository(compatibility).create_minutes_draft(
            client_id=client_id,
            meeting_id=match.group("meeting_id"),
            recording_id=match.group("recording_id"),
            title=str(request.body.get("title") or "").strip() or None,
            minutes_markdown=(
                str(request.body.get("minutesMarkdown") or "").strip() or None
            ),
            citations=citations,
            force=bool(request.body.get("force")),
        )
    )


@router.post(
    r"clients/(?P<client_id>[^/]+)/meetings/(?P<meeting_id>[^/]+)/recordings/"
    r"(?P<recording_id>[^/]+)/minutes/publish"
)
def publish_minutes(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    client_id = match.group("client_id")
    meeting_id = match.group("meeting_id")
    recording_id = match.group("recording_id")
    _require_project(compatibility, client_id)
    repository = _repository(compatibility)
    return _publish_current_minutes(
        compatibility,
        request,
        repository=repository,
        client_id=client_id,
        meeting_id=meeting_id,
        recording_id=recording_id,
        expected_version=int(request.body.get("expectedVersion") or 0),
    )


@router.get(
    r"clients/(?P<client_id>[^/]+)/meetings/(?P<meeting_id>[^/]+)/recordings/"
    r"(?P<recording_id>[^/]+)"
)
def recording_detail(
    compatibility: Any,
    _: UiRequest,
    match: Any,
) -> dict[str, Any]:
    client_id = match.group("client_id")
    _require_project(compatibility, client_id)
    detail = _call(
        lambda: _repository(compatibility).recording_detail(
            client_id=client_id,
            meeting_id=match.group("meeting_id"),
            recording_id=match.group("recording_id"),
        )
    )
    current = compatibility.runtime._current_context(require_ready=True)  # noqa: SLF001
    key = _transcription_job_key(
        compatibility.runtime,
        current.sandbox_id,
        client_id,
        match.group("meeting_id"),
        match.group("recording_id"),
    )
    return _with_transcription_progress(detail, key)
