from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Mapping

import httpx
from fastapi.responses import PlainTextResponse

from strict_common.ids import sha256_text, utc_now

from ..feedback_local import LocalFeedbackArtifactRepository
from ..local_ai_governor import (
    collect_machine_health,
    decide_machine_run,
)
from ..local_asr.downloader import get_download_manager
from ..local_asr.engine import (
    transcribe_audio,
    transcribe_recording as run_recording_transcription,
)
from ..local_asr.models import (
    EMBEDDING_MODEL,
    SEGMENTATION_MODEL,
    SENSE_VOICE_MODEL,
    diarization_ready,
    model_dir,
    model_ready,
    model_size,
)
from ..platform_integrations_local import LocalPlatformOperationRepository
from ..project_materials_local import LocalProjectMaterialsRepository
from ..runtime import LocalRuntimeError
from .routing import UiDomainRouter, UiRequest


_PlatformThread = threading.Thread


def _requires_pinned_platform_workspace(request: UiRequest) -> bool:
    path = request.path
    method = request.method
    return (
        path in {
            "feishu-sync/documents",
            "software-feedback",
            "local-asr/transcribe-test",
            "recordings/transcribe-local-audio",
            "recordings/summarize-meeting-minutes",
            "ai-command/parse-steps",
            "local/tasks/tag-suggestions",
            "runtime/llm-healthcheck",
            "runtime/llm-provider-probe",
            "ollama/pull/status",
        }
        or (method != "GET" and path.startswith("local-ai/"))
        or (method != "GET" and path.startswith("local-asr/"))
        or (method != "GET" and path.startswith("ollama/"))
    )


router = UiDomainRouter(
    "platform_integrations",
    pin_workspace=_requires_pinned_platform_workspace,
)


OLLAMA_BASE_URL = "http://127.0.0.1:11434"
_OLLAMA_LOCK = threading.RLock()
_OLLAMA_CANCEL = threading.Event()
_LOCAL_AI_EXECUTION_LOCK = threading.Lock()
_OLLAMA_PULL: dict[str, Any] = {
    "inProgress": False,
    "modelName": "",
    "status": "idle",
    "bytesDownloaded": 0,
    "bytesTotal": 0,
    "elapsedSeconds": 0,
    "completed": False,
    "error": None,
    "operationId": None,
}
_LOCAL_AI_TASK_TYPE = "local_ai.document_card_generation"
_LOCAL_AI_SETTINGS_COMMAND = "local_ai.settings.update"
_LOCAL_AI_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "paused": True,
    "manualActive": False,
    "parseModelMode": "online",
    "priorityClientId": None,
    "dailyWindows": [],
    "autoEnqueueDocumentCards": False,
    "requireACPower": True,
    "minIdleSeconds": 300,
}


def _blocked(message: str, *, state: str = "not_connected") -> dict[str, Any]:
    return {
        "state": state,
        "message": message,
        "retryable": True,
        "pollingEnabled": False,
    }


def _cloud_query(
    compatibility: Any,
    resource_path: str,
    query: Mapping[str, str] | None = None,
    *,
    authorization_scope: str = "organization",
) -> dict[str, Any]:
    result = compatibility.runtime.cloud_query(
        "/api/v2/platform-integrations/query",
        query={
            "resourcePath": resource_path.strip("/"),
            "authorizationScope": authorization_scope,
            **dict(query or {}),
        },
    )
    resource = result.get("resource")
    if not isinstance(resource, dict):
        raise LocalRuntimeError(
            502,
            "platform_resource_invalid",
            "组织云平台能力查询返回了无效资源",
        )
    return resource


def _cloud_command(
    compatibility: Any,
    request: UiRequest,
    resource_path: str,
    *,
    authorization_scope: str = "organization",
) -> dict[str, Any]:
    result = compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/platform-integrations/command",
        payload={
            "resourcePath": resource_path.strip("/"),
            "authorizationScope": authorization_scope,
            "method": request.method,
            "query": dict(request.query),
            "payload": dict(request.body),
        },
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    command_result = result.get("result")
    if not isinstance(command_result, dict):
        raise LocalRuntimeError(
            502,
            "platform_command_result_invalid",
            "组织云平台能力命令返回了无效结果",
        )
    return command_result


def _organization_query(
    compatibility: Any,
    resource_path: str,
    query: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(
        f"/api/v2/organization-access/{resource_path.strip('/')}",
        query=dict(query or {}),
    )


def _week_window(value: str) -> tuple[str, str]:
    try:
        if "-W" in value:
            year, week = value.split("-W", 1)
            start = datetime.fromisocalendar(int(year), int(week), 1)
        elif value:
            start = datetime.fromisoformat(value[:10])
        else:
            now = datetime.now()
            start = now - timedelta(days=now.weekday())
    except (TypeError, ValueError):
        now = datetime.now()
        start = now - timedelta(days=now.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=7)
    return start.isoformat(), end.isoformat()


def _within_window(value: Any, start: str, end: str) -> bool:
    created = str(value or "")
    return bool(created and start <= created < end)


def _model_root(compatibility: Any) -> Path:
    return compatibility.runtime.database_path.parent / "models"


def _local_operations(compatibility: Any) -> LocalPlatformOperationRepository:
    return LocalPlatformOperationRepository(compatibility.runtime)


def _model_json(value: str) -> Any:
    text = value.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(text)
    except ValueError:
        starts = [
            index
            for index in (text.find("{"), text.find("["))
            if index >= 0
        ]
        if not starts:
            raise LocalRuntimeError(
                502,
                "ai_response_invalid",
                "组织模型未返回可识别的结构化结果",
            )
        start = min(starts)
        end = max(text.rfind("}"), text.rfind("]"))
        if end <= start:
            raise LocalRuntimeError(
                502,
                "ai_response_invalid",
                "组织模型未返回完整的结构化结果",
            )
        try:
            return json.loads(text[start : end + 1])
        except ValueError as exc:
            raise LocalRuntimeError(
                502,
                "ai_response_invalid",
                "组织模型返回的结构化结果无效",
            ) from exc


def _ai_failure_state(exc: LocalRuntimeError) -> tuple[str, str]:
    if exc.code in {
        "needs_login",
        "organization_required",
        "organization_ai_not_ready",
        "organization_ai_config_incomplete",
    }:
        return "blocked", "configuration_missing"
    return "failed_retryable", "provider_execution_failed"


def _run_private_ai_operation(
    compatibility: Any,
    request: UiRequest,
    *,
    command_type: str,
    aggregate_id: str,
    safe_payload: Mapping[str, Any],
    system_prompt: str,
    prompt: str,
    parser: Callable[[str, str], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    operations = _local_operations(compatibility)
    started = operations.begin(
        idempotency_key=request.idempotency_key,
        command_type=command_type,
        aggregate_type="private_ai_execution",
        aggregate_id=aggregate_id,
        payload={
            **dict(safe_payload),
            "inputHash": sha256_text(prompt),
            "inputChars": len(prompt),
        },
        initial_result={
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    if started.get("idempotentReplay"):
        output = started.get("output")
        return started, dict(output) if isinstance(output, dict) else None
    began = perf_counter()
    try:
        completion = compatibility.runtime.private_ai_completion(
            system_prompt=system_prompt,
            prompt=prompt,
            creativity_mode="strict",
        )
        content = str(completion.get("content") or "").strip()
        output = parser(content, str(completion.get("modelName") or ""))
    except LocalRuntimeError as exc:
        state, blocker_type = _ai_failure_state(exc)
        receipt = operations.update(
            operation_id=str(started["operationId"]),
            state=state,
            result_patch={
                "message": exc.message,
                "blockerType": blocker_type,
                "latencyMs": int((perf_counter() - began) * 1000),
            },
            error_code=exc.code,
            error_message=exc.message,
        )
        return receipt, None
    receipt = operations.update(
        operation_id=str(started["operationId"]),
        state="completed",
        result_patch={
            "output": output,
            "outputHash": sha256_text(json.dumps(output, ensure_ascii=False, sort_keys=True)),
            "modelUsed": str(completion.get("modelName") or ""),
            "sourceScope": str(completion.get("sourceScope") or ""),
            "persistedToOrganizationCloud": False,
            "latencyMs": int((perf_counter() - began) * 1000),
        },
    )
    return receipt, output


def _parse_healthcheck_output(content: str, model_name: str) -> dict[str, Any]:
    if not content:
        raise LocalRuntimeError(502, "ai_response_empty", "组织模型返回了空结果")
    return {
        "acknowledged": True,
        "model": model_name,
    }


def _parse_meeting_minutes_output(content: str, model_name: str) -> dict[str, Any]:
    normalized = content.strip()
    if not normalized:
        raise LocalRuntimeError(502, "ai_response_empty", "组织模型返回了空结果")

    # Meeting minutes are read-only prose that will still be reviewed in the
    # task editor.  Do not make a slightly imperfect JSON wrapper a reason to
    # discard otherwise usable minutes.  Other business-write parsers remain
    # strict; this tolerance is deliberately local to the minutes operation.
    try:
        parsed = _model_json(normalized)
    except LocalRuntimeError:
        parsed = None

    title = ""
    minutes = ""
    if isinstance(parsed, dict):
        candidate = parsed
        for key in ("data", "result", "output"):
            nested = candidate.get(key)
            if isinstance(nested, dict):
                candidate = nested
                break
        title = str(candidate.get("title") or candidate.get("subject") or "").strip()
        minutes = str(
            candidate.get("minutesMd")
            or candidate.get("minutes_md")
            or candidate.get("minutes")
            or candidate.get("markdown")
            or candidate.get("content")
            or candidate.get("text")
            or ""
        ).strip()
    elif isinstance(parsed, str):
        minutes = parsed.strip()

    if not minutes:
        minutes = normalized
        if minutes.startswith("```") and minutes.endswith("```"):
            lines = minutes.splitlines()
            if len(lines) >= 3:
                minutes = "\n".join(lines[1:-1]).strip()
    if not minutes:
        raise LocalRuntimeError(502, "meeting_minutes_incomplete", "组织模型未生成有效纪要")
    if not title:
        first_heading = next(
            (
                re.sub(r"^#{1,6}\s*", "", line).strip()
                for line in minutes.splitlines()
                if re.match(r"^#{1,6}\s+\S", line.strip())
            ),
            "",
        )
        title = first_heading or "录音纪要"
    return {
        "title": title[:200],
        "minutesMd": minutes,
        "model": model_name,
    }


def _parse_command_steps_output(content: str, model_name: str) -> dict[str, Any]:
    parsed = _model_json(content)
    raw_steps = parsed.get("steps") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_steps, list) or not raw_steps:
        raise LocalRuntimeError(502, "ai_command_steps_invalid", "组织模型未返回有效步骤")
    steps: list[dict[str, Any]] = []
    for raw in raw_steps[:20]:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "").strip()
        if not action:
            continue
        steps.append(
            {
                "index": len(steps) + 1,
                "action": action[:500],
                "basis": str(raw.get("basis") or "").strip()[:1000],
                "deliverable": str(raw.get("deliverable") or "").strip()[:500],
            }
        )
    if not steps:
        raise LocalRuntimeError(502, "ai_command_steps_invalid", "组织模型未返回有效步骤")
    return {"steps": steps, "model": model_name}


def _parse_tag_suggestions_output(content: str, model_name: str) -> dict[str, Any]:
    parsed = _model_json(content)
    raw_tags = parsed.get("suggestedTags") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_tags, list):
        raise LocalRuntimeError(502, "task_tag_suggestions_invalid", "组织模型未返回标签数组")
    tags: list[str] = []
    for raw in raw_tags:
        tag = str(raw or "").strip()
        if tag and tag not in tags:
            tags.append(tag[:32])
        if len(tags) >= 5:
            break
    return {"suggestedTags": tags, "model": model_name}


def _transcription_response(result: Any) -> dict[str, Any]:
    return {
        "success": True,
        "text": str(result.text or ""),
        "durationMs": int(result.duration_ms),
        "elapsedMs": int(result.elapsed_ms),
        "language": str(result.language or "auto"),
        "segments": [
            {
                "startMs": int(item.start_ms),
                "endMs": int(item.end_ms),
                "text": str(item.text or ""),
                "speakerId": item.speaker_id,
                "emotion": item.emotion,
                "event": item.event,
            }
            for item in result.segments
        ],
        "errorMessage": None,
        "retryable": False,
        "pollingEnabled": False,
    }


def _feishu_runtime_status(compatibility: Any) -> dict[str, Any]:
    try:
        return _cloud_query(compatibility, "org-integrations/feishu")
    except LocalRuntimeError as exc:
        if exc.code not in {
            "needs_login",
            "organization_required",
            "workspace_not_ready",
            "workspace_secret_missing",
            "workspace_session_invalid",
            "failed_retryable",
        }:
            raise
        try:
            local = compatibility.feishu_integration()
        except (AttributeError, LocalRuntimeError):
            local = {
                "enabled": False,
                "appId": None,
                "lastValidationStatus": "not_configured",
            }
        return {
            **local,
            "state": "not_connected",
            "retryable": True,
            "lastValidationMessage": exc.message,
            "authorizationBlockedReason": exc.code,
        }


def _sensevoice_status(compatibility: Any) -> dict[str, Any]:
    root = _model_root(compatibility)
    directory = model_dir(root, SENSE_VOICE_MODEL)
    installed = model_ready(root, SENSE_VOICE_MODEL)
    progress = get_download_manager(root).status()
    if installed:
        state = "ready"
    elif progress.in_progress:
        state = "processing"
    elif progress.error_message:
        state = "failed_retryable"
    else:
        state = "not_connected"
    return {
        "modelName": SENSE_VOICE_MODEL,
        "installed": installed,
        "modelDir": str(directory),
        "sizeBytes": model_size(root, SENSE_VOICE_MODEL),
        "downloadInProgress": progress.in_progress,
        "downloadBytesDownloaded": progress.bytes_downloaded,
        "downloadBytesTotal": progress.bytes_total,
        "downloadCurrentFile": progress.current_file,
        "downloadCompleted": progress.completed or installed,
        "downloadError": progress.error_message,
        "downloadElapsedSeconds": progress.elapsed_seconds,
        "state": state,
        "retryable": not installed,
        "pollingEnabled": progress.in_progress,
    }


def _diarization_status(compatibility: Any) -> dict[str, Any]:
    root = _model_root(compatibility)
    segmentation_installed = model_ready(root, SEGMENTATION_MODEL)
    embedding_installed = model_ready(root, EMBEDDING_MODEL)
    both = diarization_ready(root)
    progress = get_download_manager(root).status()
    if both:
        state = "ready"
    elif progress.in_progress:
        state = "processing"
    elif progress.error_message:
        state = "failed_retryable"
    else:
        state = "not_connected"
    return {
        "segmentationModelName": SEGMENTATION_MODEL,
        "embeddingModelName": EMBEDDING_MODEL,
        "segmentationInstalled": segmentation_installed,
        "embeddingInstalled": embedding_installed,
        "bothInstalled": both,
        "sizeBytes": model_size(root, SEGMENTATION_MODEL, EMBEDDING_MODEL),
        "downloadInProgress": progress.in_progress,
        "downloadBytesDownloaded": progress.bytes_downloaded,
        "downloadBytesTotal": progress.bytes_total,
        "downloadCurrentFile": progress.current_file,
        "downloadCurrentModel": progress.current_model,
        "downloadPendingModels": progress.pending_models,
        "downloadCompletedModels": progress.completed_models,
        "downloadCompleted": progress.completed or both,
        "downloadError": progress.error_message,
        "downloadElapsedSeconds": progress.elapsed_seconds,
        "state": state,
        "retryable": not both,
        "pollingEnabled": progress.in_progress,
    }


def _ollama_health() -> dict[str, Any]:
    try:
        with httpx.Client(
            base_url=OLLAMA_BASE_URL,
            timeout=httpx.Timeout(2.0),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            tags_response = client.get("/api/tags")
            tags_response.raise_for_status()
            tags_payload = tags_response.json()
            try:
                version_response = client.get("/api/version")
                version = str(version_response.json().get("version") or "")
            except (httpx.HTTPError, ValueError, AttributeError):
                version = ""
    except (httpx.HTTPError, ValueError):
        return {
            "running": False,
            "baseUrl": OLLAMA_BASE_URL,
            "installedModels": [],
            "error": "本机 Ollama 未运行",
            "version": None,
            "state": "not_connected",
            "retryable": True,
            "pollingEnabled": False,
        }
    models = tags_payload.get("models") if isinstance(tags_payload, dict) else []
    installed = [
        {
            "name": str(item.get("name") or item.get("model") or ""),
            "sizeBytes": int(item.get("size") or 0),
            "digest": str(item.get("digest") or ""),
            "modifiedAt": str(item.get("modified_at") or ""),
        }
        for item in (models or [])
        if isinstance(item, dict)
    ]
    return {
        "running": True,
        "baseUrl": OLLAMA_BASE_URL,
        "installedModels": installed,
        "error": None,
        "version": version or None,
        "state": "ready",
        "retryable": False,
        "pollingEnabled": True,
    }


def _run_ollama_pull(
    operations: LocalPlatformOperationRepository,
    model_name: str,
    operation_id: str,
    sandbox_id: str,
) -> None:
    started = time.monotonic()
    try:
        with httpx.Client(
            base_url=OLLAMA_BASE_URL,
            timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            with client.stream(
                "POST",
                "/api/pull",
                json={"model": model_name, "stream": True},
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if _OLLAMA_CANCEL.is_set():
                        raise RuntimeError("模型下载已取消")
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except ValueError:
                        continue
                    with _OLLAMA_LOCK:
                        _OLLAMA_PULL.update(
                            {
                                "status": str(item.get("status") or "downloading"),
                                "bytesDownloaded": int(item.get("completed") or 0),
                                "bytesTotal": int(item.get("total") or 0),
                                "elapsedSeconds": int(time.monotonic() - started),
                            }
                        )
        with _OLLAMA_LOCK:
            _OLLAMA_PULL.update(
                {
                    "inProgress": False,
                    "completed": True,
                    "status": "completed",
                    "elapsedSeconds": int(time.monotonic() - started),
                    "error": None,
                }
            )
        operations.update(
            operation_id=operation_id,
            state="completed",
            result_patch={
                "started": True,
                "modelName": model_name,
                "message": f"{model_name} 下载完成",
                "completed": True,
            },
            captured_sandbox_id=sandbox_id,
        )
    except Exception as exc:
        cancelled = _OLLAMA_CANCEL.is_set()
        with _OLLAMA_LOCK:
            _OLLAMA_PULL.update(
                {
                    "inProgress": False,
                    "completed": False,
                    "status": "cancelled" if cancelled else "failed",
                    "elapsedSeconds": int(time.monotonic() - started),
                    "error": str(exc),
                }
            )
        operations.update(
            operation_id=operation_id,
            state="cancelled" if cancelled else "failed_retryable",
            result_patch={
                "started": True,
                "modelName": model_name,
                "message": (
                    f"{model_name} 下载已取消"
                    if cancelled
                    else f"{model_name} 下载失败，可重试"
                ),
                "completed": False,
            },
            error_code=None if cancelled else "ollama_pull_failed",
            error_message=None if cancelled else str(exc),
            captured_sandbox_id=sandbox_id,
        )


def _local_ai_settings(compatibility: Any) -> dict[str, Any]:
    rows = _local_operations(compatibility).latest(
        command_types=(_LOCAL_AI_SETTINGS_COMMAND,),
        limit=1,
    )
    stored = rows[0].get("settings") if rows else None
    settings = {
        **_LOCAL_AI_DEFAULTS,
        **(dict(stored) if isinstance(stored, Mapping) else {}),
    }
    settings["enabled"] = bool(settings["enabled"])
    settings["paused"] = bool(settings["paused"])
    settings["manualActive"] = bool(settings["manualActive"])
    settings["parseModelMode"] = (
        "local" if settings.get("parseModelMode") == "local" else "online"
    )
    settings["priorityClientId"] = (
        str(settings["priorityClientId"])
        if settings.get("priorityClientId")
        else None
    )
    windows = []
    for raw in settings.get("dailyWindows") or []:
        if not isinstance(raw, Mapping):
            continue
        start = str(raw.get("start") or "")
        end = str(raw.get("end") or "")
        if re.fullmatch(r"\d{2}:\d{2}", start) and re.fullmatch(
            r"\d{2}:\d{2}",
            end,
        ):
            windows.append({"start": start, "end": end})
    settings["dailyWindows"] = windows
    settings["requireACPower"] = bool(settings["requireACPower"])
    settings["minIdleSeconds"] = max(
        0,
        min(int(settings.get("minIdleSeconds") or 0), 86_400),
    )
    return settings


def _local_ai_queue_rows(compatibility: Any) -> list[dict[str, Any]]:
    return _local_operations(compatibility).latest(
        command_types=(_LOCAL_AI_TASK_TYPE,),
        limit=1000,
    )


def _local_ai_state(value: Any) -> str:
    state = str(value or "")
    if state in {"queued"}:
        return "queued"
    if state in {"processing", "sending"}:
        return "running"
    if state in {"completed", "succeeded"}:
        return "completed"
    return "failed"


def _local_ai_projects(compatibility: Any) -> list[str]:
    snapshot = compatibility._snapshot()  # noqa: SLF001
    return [
        str(item.get("id") or item.get("projectId") or "")
        for item in snapshot.get("projects") or []
        if str(item.get("id") or item.get("projectId") or "")
    ]


def _in_daily_window(settings: Mapping[str, Any]) -> bool:
    windows = settings.get("dailyWindows") or []
    if not windows:
        return True
    now = datetime.now().strftime("%H:%M")
    for window in windows:
        if not isinstance(window, Mapping):
            continue
        start = str(window.get("start") or "")
        end = str(window.get("end") or "")
        if not start or not end:
            continue
        if start <= end and start <= now <= end:
            return True
        if start > end and (now >= start or now <= end):
            return True
    return False


def _ollama_summarize(text: str) -> tuple[str, str]:
    health = _ollama_health()
    installed = [
        str(item.get("name") or "")
        for item in health.get("installedModels") or []
        if isinstance(item, Mapping)
    ]
    model = next(
        (name for name in installed if name.startswith("qwen3-vl:32b")),
        "",
    )
    if not model:
        raise LocalRuntimeError(
            409,
            "local_parse_model_missing",
            "本机未安装 qwen3-vl:32b 深度解析模型",
        )
    try:
        with httpx.Client(
            base_url=OLLAMA_BASE_URL,
            timeout=httpx.Timeout(10.0, read=300.0),
            trust_env=False,
        ) as client:
            response = client.post(
                "/api/generate",
                json={
                    "model": model,
                    "stream": False,
                    "prompt": (
                        "请只根据以下资料生成一份可供项目协作使用的中文摘要。"
                        "保留事实、主体、时间、承诺、风险和待办；不补造信息。\n\n"
                        f"{text[:120_000]}"
                    ),
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LocalRuntimeError(
            503,
            "local_ollama_execution_failed",
            "本机 Ollama 深度解析失败，可重试",
        ) from exc
    summary = str(payload.get("response") or "").strip()
    if not summary:
        raise LocalRuntimeError(502, "local_ai_summary_empty", "本机模型返回了空摘要")
    return summary, model


def _local_ai_health(compatibility: Any) -> dict[str, Any]:
    ollama = _ollama_health()
    settings = _local_ai_settings(compatibility)
    queue = _local_ai_queue_rows(compatibility)
    machine = collect_machine_health()
    machine_decision = decide_machine_run(
        machine,
        require_ac_power=bool(settings["requireACPower"]),
        min_idle_seconds=float(settings["minIdleSeconds"]),
    )
    queued = sum(_local_ai_state(item.get("state")) == "queued" for item in queue)
    in_window = _in_daily_window(settings)
    local_mode = settings["parseModelMode"] == "local"
    local_model_ready = bool(ollama["running"]) and any(
        str(item.get("name") or "").startswith("qwen3-vl:32b")
        for item in ollama.get("installedModels") or []
        if isinstance(item, Mapping)
    )
    if not settings["enabled"] and not settings["manualActive"]:
        verdict = "skip"
        reason = "本机深度解析尚未启用"
    elif settings["paused"]:
        verdict = "wait"
        reason = "本机深度解析已暂停"
    elif not in_window and not settings["manualActive"]:
        verdict = "wait"
        reason = "当前不在自动解析时间窗口"
    elif local_mode and not local_model_ready:
        verdict = "wait"
        reason = "已选择本地解析，但 qwen3-vl:32b 或 Ollama 尚未就绪"
    elif machine_decision.verdict != "go":
        verdict = machine_decision.verdict
        reason = machine_decision.reason
    else:
        verdict = "go"
        reason = "本机深度解析执行器已就绪"
    return {
        "verdict": verdict,
        "reason": reason,
        "retry_after_seconds": (
            machine_decision.retry_after_seconds
            if verdict == machine_decision.verdict == "wait"
            and reason == machine_decision.reason
            else 0
        ),
        "summary": f"{reason}；等待处理 {queued} 项",
        "thermal_state": machine.thermal_state,
        "cpu_speed_limit": machine.cpu_speed_limit,
        "memory_pressure": machine.memory_pressure,
        "battery_percent": machine.battery_percent,
        "on_ac_power": machine.on_ac_power,
        "user_idle_seconds": machine.user_idle_seconds,
        "ollama_reachable": bool(ollama["running"]),
        "in_run_window": in_window,
        "enabled": bool(settings["enabled"] or settings["manualActive"]),
        "paused": bool(settings["paused"]),
        "state": "ready",
        "retryable": verdict != "go",
        "pollingEnabled": bool(queued or settings["enabled"]),
    }


@router.get(r"system/health")
def system_health(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del request, match
    health = compatibility.health()
    feishu = _feishu_runtime_status(compatibility)
    ollama = _ollama_health()
    local_ai = _local_ai_health(compatibility)
    health["pollingEnabled"] = True
    health["platformCapabilities"] = {
        "backgroundTasks": {"state": "idle", "pollingEnabled": False},
        "audioTranscription": {
            "state": _sensevoice_status(compatibility)["state"],
            "pollingEnabled": False,
        },
        "localAi": {
            "state": str(local_ai.get("state") or "blocked"),
            "pollingEnabled": bool(local_ai.get("pollingEnabled")),
        },
        "ollama": {
            "state": str(ollama.get("state") or "not_connected"),
            "pollingEnabled": bool(ollama.get("pollingEnabled")),
        },
        "feishu": {
            "state": str(feishu.get("state") or "not_connected"),
            "pollingEnabled": False,
        },
    }
    return health


@router.get(r"system/source-integrity")
def source_integrity(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    diagnostics = compatibility.runtime.diagnostics()
    database = diagnostics.get("database") or {}
    return {
        "runningBackendRoot": str(compatibility.runtime.database_path.parent),
        "workspaceBackendRoot": None,
        "runningHash": str(database.get("manifestHash") or ""),
        "workspaceHash": None,
        "match": None,
        "warning": (
            "严格安装版只报告当前运行数据库与构建身份；未猜测源码工作区"
        ),
        "buildVersion": str(database.get("buildId") or ""),
        "gitCommit": None,
        "runtimeMode": "packaged",
        "frontendBuildVersion": request.query.get("frontendBuildVersion"),
        "frontendGitCommit": request.query.get("frontendGitCommit"),
        "workspaceBuildVersion": None,
        "workspaceGitCommit": None,
    }


@router.get(r"system/active-background-tasks")
def active_background_tasks(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    try:
        return _cloud_query(
            compatibility,
            "system/active-background-tasks",
        )
    except LocalRuntimeError as exc:
        if exc.code not in {
            "needs_login",
            "organization_required",
            "failed_retryable",
        }:
            raise
        return {
            "tasks": [],
            "count": 0,
            "pollingEnabled": False,
            "state": "blocked",
            "message": exc.message,
        }


@router.get(r"audio-transcription-jobs/recent")
def recent_audio_jobs(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    status = _sensevoice_status(compatibility)
    try:
        attempts = _local_operations(compatibility).latest(
            command_types=(
                "local_asr.transcribe_test",
                "recordings.transcribe_local_audio",
            ),
            limit=20,
        )
    except LocalRuntimeError as exc:
        if exc.code != "local_platform_receipt_adapter_not_connected":
            raise
        return {
            "jobs": [],
            "state": "not_connected",
            "message": exc.message,
            "pollingEnabled": False,
            "retryable": False,
        }
    jobs = [
        {
            "id": item["operationId"],
            "operationId": item["operationId"],
            "status": item.get("state") or "unknown",
            "state": item.get("state") or "unknown",
            "errorCode": item.get("errorCode"),
            "errorMessage": item.get("error") or item.get("message") or "",
            "createdAt": item.get("createdAt") or item.get("updatedAt"),
            "updatedAt": item.get("updatedAt"),
            "retryable": bool(item.get("retryable")),
            "pollingEnabled": bool(item.get("pollingEnabled")),
        }
        for item in attempts
    ]
    return {
        "jobs": jobs,
        "state": "ready" if jobs else status["state"],
        "message": (
            ""
            if jobs
            else status.get("downloadError") or ""
        ),
        "pollingEnabled": False,
        "retryable": True,
    }


@router.get(r"local-asr/model/status")
def local_asr_status(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del request, match
    return _sensevoice_status(compatibility)


@router.post(r"local-asr/model/download")
def local_asr_download(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    prefer_mirror = bool(request.body.get("preferMirror", True))
    started, message = get_download_manager(_model_root(compatibility)).start(
        SENSE_VOICE_MODEL,
        prefer_mirror=prefer_mirror,
    )
    operations = _local_operations(compatibility)
    result = operations.begin(
        idempotency_key=request.idempotency_key,
        command_type="local_asr.model.download",
        aggregate_type="local_model",
        aggregate_id=SENSE_VOICE_MODEL,
        payload={
            "modelName": SENSE_VOICE_MODEL,
            "preferMirror": prefer_mirror,
        },
        initial_result={
            "state": "processing" if started else "completed",
            "started": started,
            "message": message,
            "retryable": True,
            "pollingEnabled": started,
        },
    )
    return {
        "started": bool(result.get("started", started)),
        "message": str(result.get("message") or message),
        **result,
        "retryable": True,
        "pollingEnabled": bool(result.get("pollingEnabled", started)),
    }


@router.post(r"local-asr/model/cancel")
def local_asr_cancel(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del request, match
    cancelled = get_download_manager(_model_root(compatibility)).cancel()
    return {
        "cancelled": cancelled,
        "state": "cancelled" if cancelled else "idle",
        "pollingEnabled": cancelled,
    }


@router.post(r"local-asr/transcribe-test")
def local_asr_test(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    audio_path = str(request.body.get("audioPath") or "").strip()
    if not audio_path:
        raise LocalRuntimeError(422, "audio_path_required", "请选择要测试的音频文件")
    language = str(request.body.get("language") or "auto")
    operations = _local_operations(compatibility)
    receipt = operations.begin(
        idempotency_key=request.idempotency_key,
        command_type="local_asr.transcribe_test",
        aggregate_type="local_audio_transcription",
        aggregate_id=sha256_text(audio_path),
        payload={
            "audioPathHash": sha256_text(audio_path),
            "language": language,
        },
        initial_result={
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    replay_output = receipt.get("output")
    if receipt.get("idempotentReplay") and isinstance(replay_output, dict):
        return {**replay_output, **receipt}
    try:
        result = transcribe_audio(
            _model_root(compatibility),
            audio_path,
            language=language,
        )
        output = _transcription_response(result)
    except Exception as exc:  # noqa: BLE001
        message = f"{exc.__class__.__name__}：{exc}"
        failed = operations.update(
            operation_id=str(receipt["operationId"]),
            state="failed_retryable",
            result_patch={
                "success": False,
                "output": {
                    "success": False,
                    "text": "",
                    "durationMs": 0,
                    "elapsedMs": 0,
                    "language": language,
                    "segments": [],
                    "errorMessage": message,
                    "retryable": True,
                    "pollingEnabled": False,
                },
            },
            error_code="local_asr_execution_failed",
            error_message=message,
        )
        return {**failed["output"], **failed}
    completed = operations.update(
        operation_id=str(receipt["operationId"]),
        state="completed",
        result_patch={"success": True, "output": output},
    )
    return {**output, **completed}


@router.get(r"local-asr/diarization/status")
def diarization_status(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del request, match
    return _diarization_status(compatibility)


@router.post(r"local-asr/diarization/download")
def diarization_download(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    prefer_mirror = bool(request.body.get("preferMirror", True))
    started, message = get_download_manager(_model_root(compatibility)).start(
        [SEGMENTATION_MODEL, EMBEDDING_MODEL],
        prefer_mirror=prefer_mirror,
    )
    result = _local_operations(compatibility).begin(
        idempotency_key=request.idempotency_key,
        command_type="local_asr.diarization.download",
        aggregate_type="local_model",
        aggregate_id="speaker-diarization",
        payload={
            "models": [SEGMENTATION_MODEL, EMBEDDING_MODEL],
            "preferMirror": prefer_mirror,
        },
        initial_result={
            "state": "processing" if started else "completed",
            "started": started,
            "message": message,
            "retryable": True,
            "pollingEnabled": started,
        },
    )
    return {
        "started": bool(result.get("started", started)),
        "message": str(result.get("message") or message),
        **result,
        "retryable": True,
        "pollingEnabled": bool(result.get("pollingEnabled", started)),
    }


@router.get(r"ollama/health")
def ollama_health(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del compatibility, request, match
    return _ollama_health()


@router.get(r"ollama/recommended-models")
def ollama_recommended(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del compatibility, match
    capability = str(request.query.get("capability") or "general")
    models = {
        "embedding": [
            {
                "name": "bge-m3",
                "sizeGb": 1.2,
                "description": "中英文向量模型",
                "default": True,
            }
        ],
        "parse": [
            {
                "name": "qwen2.5:14b",
                "sizeGb": 9.0,
                "description": "本地深读与结构化解析",
                "default": True,
            }
        ],
    }.get(
        capability,
        [
            {
                "name": "qwen2.5:7b",
                "sizeGb": 4.7,
                "description": "通用本地模型",
                "default": True,
            }
        ],
    )
    return {"capability": capability, "models": models}


@router.post(r"ollama/pull")
def ollama_pull(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    model_name = str(request.body.get("modelName") or "").strip()
    if not model_name:
        raise LocalRuntimeError(422, "ollama_model_required", "请选择要下载的模型")
    if not _ollama_health()["running"]:
        result = _local_operations(compatibility).record_blocked(
            idempotency_key=request.idempotency_key,
            command_type="ollama.pull",
            aggregate_type="local_model",
            aggregate_id=model_name,
            payload={"modelName": model_name},
            error_code="ollama_not_running",
            message="本机 Ollama 未运行；操作已登记，启动服务后可重试",
            blocker_type="executor_unavailable",
        )
        return {
            "started": False,
            **result,
            "retryable": True,
            "pollingEnabled": False,
        }
    with _OLLAMA_LOCK:
        if _OLLAMA_PULL["inProgress"]:
            return {
                "started": False,
                "message": f"正在下载 {_OLLAMA_PULL['modelName']}",
                "state": "blocked",
                "retryable": True,
                "pollingEnabled": True,
            }
        operations = _local_operations(compatibility)
        started = operations.begin(
            idempotency_key=request.idempotency_key,
            command_type="ollama.pull",
            aggregate_type="local_model",
            aggregate_id=model_name,
            payload={"modelName": model_name},
            initial_result={
                "started": True,
                "modelName": model_name,
                "message": f"已开始下载 {model_name}",
                "state": "processing",
                "retryable": True,
                "pollingEnabled": True,
            },
        )
        if started.get("idempotentReplay"):
            return started
        _OLLAMA_CANCEL.clear()
        _OLLAMA_PULL.update(
            {
                "inProgress": True,
                "modelName": model_name,
                "status": "starting",
                "bytesDownloaded": 0,
                "bytesTotal": 0,
                "elapsedSeconds": 0,
                "completed": False,
                "error": None,
                "operationId": started["operationId"],
            }
        )
    _PlatformThread(
        target=_run_ollama_pull,
        args=(
            operations,
            model_name,
            str(started["operationId"]),
            str(started["sandboxId"]),
        ),
        daemon=True,
        name="strict-ollama-pull",
    ).start()
    return started


@router.get(r"ollama/pull/status")
def ollama_pull_status(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    with _OLLAMA_LOCK:
        active = dict(_OLLAMA_PULL)
    if active["inProgress"]:
        return {**active, "pollingEnabled": True}
    latest = _local_operations(compatibility).latest(
        command_types=("ollama.pull",),
        limit=1,
    )
    if not latest:
        return {**active, "pollingEnabled": False}
    operation = latest[0]
    if operation.get("state") in {"queued", "processing", "cancelling"}:
        installed_names = {
            str(item.get("name") or "")
            for item in _ollama_health().get("installedModels") or []
        }
        model_name = str(operation.get("modelName") or operation["aggregateId"])
        if model_name in installed_names:
            operation = _local_operations(compatibility).update(
                operation_id=str(operation["operationId"]),
                state="completed",
                result_patch={
                    "started": True,
                    "modelName": model_name,
                    "message": f"{model_name} 下载已由本机状态确认完成",
                    "completed": True,
                    "reconciledAfterRestart": True,
                },
            )
        else:
            operation = _local_operations(compatibility).update(
                operation_id=str(operation["operationId"]),
                state="failed_retryable",
                result_patch={
                    "started": True,
                    "modelName": model_name,
                    "message": "下载工作线程已中断；未猜测完成状态，请重试",
                    "completed": False,
                    "reconciledAfterRestart": True,
                    "blockerType": "worker_interrupted",
                },
                error_code="ollama_pull_worker_interrupted",
                error_message="下载工作线程已中断",
            )
    return {
        "inProgress": False,
        "modelName": str(operation.get("modelName") or operation["aggregateId"]),
        "status": str(operation.get("state") or "unknown"),
        "bytesDownloaded": 0,
        "bytesTotal": 0,
        "elapsedSeconds": 0,
        "completed": operation.get("state") == "completed",
        "error": operation.get("error"),
        **operation,
        "pollingEnabled": False,
    }


@router.post(r"ollama/pull/cancel")
def ollama_pull_cancel(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    with _OLLAMA_LOCK:
        active = bool(_OLLAMA_PULL["inProgress"])
        operation_id = str(_OLLAMA_PULL.get("operationId") or "")
        model_name = str(_OLLAMA_PULL.get("modelName") or "")
    if active:
        _OLLAMA_CANCEL.set()
        cancel = _local_operations(compatibility).begin(
            idempotency_key=request.idempotency_key,
            command_type="ollama.pull.cancel",
            aggregate_type="local_model",
            aggregate_id=model_name,
            payload={"pullOperationId": operation_id, "modelName": model_name},
            initial_result={
                "cancelled": True,
                "state": "processing",
                "message": f"已请求取消 {model_name} 下载",
                "retryable": False,
                "pollingEnabled": False,
            },
        )
        if not cancel.get("idempotentReplay"):
            cancel = _local_operations(compatibility).update(
                operation_id=str(cancel["operationId"]),
                state="completed",
                result_patch={
                    "cancelled": True,
                    "message": f"已请求取消 {model_name} 下载",
                },
            )
        return cancel
    return {
        "cancelled": False,
        "state": "idle",
        "message": "当前没有正在下载的 Ollama 模型",
        "retryable": False,
        "pollingEnabled": False,
    }


@router.post(r"ollama/delete")
def ollama_delete(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    model_name = str(request.body.get("modelName") or "").strip()
    if not model_name:
        raise LocalRuntimeError(422, "ollama_model_required", "请选择要删除的模型")
    operations = _local_operations(compatibility)
    started = operations.begin(
        idempotency_key=request.idempotency_key,
        command_type="ollama.delete",
        aggregate_type="local_model",
        aggregate_id=model_name,
        payload={"modelName": model_name},
        initial_result={
            "success": False,
            "modelName": model_name,
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    if started.get("idempotentReplay"):
        return started
    try:
        with httpx.Client(
            base_url=OLLAMA_BASE_URL,
            timeout=httpx.Timeout(10.0),
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = client.request(
                "DELETE",
                "/api/delete",
                json={"model": model_name},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        return operations.update(
            operation_id=str(started["operationId"]),
            state="failed_retryable",
            result_patch={
                "success": False,
                "message": "Ollama 模型删除失败，可重试",
                "modelName": model_name,
            },
            error_code="ollama_delete_failed",
            error_message=str(exc),
        )
    return operations.update(
        operation_id=str(started["operationId"]),
        state="completed",
        result_patch={
            "success": True,
            "message": f"已删除 {model_name}",
            "modelName": model_name,
        },
    )


@router.get(r"local-ai/health")
def local_ai_health(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del request, match
    return _local_ai_health(compatibility)


@router.get(r"local-ai/queue")
def local_ai_queue(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    requested_status = str(request.query.get("status") or "")
    requested_type = str(request.query.get("task_type") or "")
    limit = max(1, min(int(request.query.get("limit") or 50), 500))
    rows = _local_ai_queue_rows(compatibility)
    tasks = []
    for row in rows:
        status = _local_ai_state(row.get("state"))
        payload = row.get("payload") or {}
        task_type = "document_card_generation"
        if requested_status and status != requested_status:
            continue
        if requested_type and task_type != requested_type:
            continue
        tasks.append(
            {
                "id": row["operationId"],
                "task_type": task_type,
                "status": status,
                "priority": 100,
                "client_id": payload.get("projectId"),
                "knowledge_document_id": payload.get("documentId"),
                "model_profile_id": str(
                    row.get("modelName")
                    or payload.get("modelMode")
                    or "organization_default"
                ),
                "attempts": 1,
                "max_attempts": 3,
                "last_error": row.get("error"),
                "locked_by": (
                    "strict-local-runtime" if status == "running" else None
                ),
                "started_at": (
                    row.get("updatedAt") if status == "running" else None
                ),
                "completed_at": (
                    row.get("updatedAt") if status == "completed" else None
                ),
                "created_at": row.get("createdAt") or row.get("updatedAt"),
                "updated_at": row.get("updatedAt"),
                "payload_preview": json.dumps(
                    {
                        "projectId": payload.get("projectId"),
                        "documentId": payload.get("documentId"),
                        "contentHash": payload.get("contentHash"),
                    },
                    ensure_ascii=False,
                ),
                "result_preview": json.dumps(
                    {
                        "summaryHash": row.get("summaryHash"),
                        "modelName": row.get("modelName"),
                    },
                    ensure_ascii=False,
                ),
            }
        )
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0}
    for row in rows:
        counts[_local_ai_state(row.get("state"))] += 1
    return {
        "tasks": tasks[:limit],
        "totalByStatus": counts,
        "filter": {
            "status": requested_status or None,
            "task_type": requested_type or None,
            "limit": limit,
        },
        "state": "ready",
        "message": "",
        "pollingEnabled": bool(counts["queued"] or counts["running"]),
        "retryable": bool(counts["failed"]),
    }


def _local_ai_run_now_pinned(
    compatibility: Any,
    request: UiRequest,
) -> dict[str, Any]:
    force = str(request.query.get("force") or "").lower() == "true"
    settings = _local_ai_settings(compatibility)
    health = _local_ai_health(compatibility)
    if not force and health["verdict"] != "go":
        return {
            "processed": 0,
            "failed": 0,
            "skipped": 1,
            "status": "skipped",
            "governor_reason": health["reason"],
            "governor_retry_after": health["retry_after_seconds"],
        }
    if not _LOCAL_AI_EXECUTION_LOCK.acquire(blocking=False):
        return {
            "processed": 0,
            "failed": 0,
            "skipped": 1,
            "status": "processing",
            "governor_reason": "已有一项本机深度解析正在执行",
            "governor_retry_after": 5,
        }
    try:
        rows = [
            item
            for item in reversed(_local_ai_queue_rows(compatibility))
            if _local_ai_state(item.get("state")) == "queued"
        ]
        priority_project = str(settings.get("priorityClientId") or "")
        if priority_project:
            rows.sort(
                key=lambda item: (
                    str((item.get("payload") or {}).get("projectId") or "")
                    != priority_project
                )
            )
        if not rows:
            return {
                "processed": 0,
                "failed": 0,
                "skipped": 1,
                "status": "idle",
                "governor_reason": "没有等待处理的本机资料",
                "governor_retry_after": 0,
            }
        row = rows[0]
        operations = _local_operations(compatibility)
        operation_id = str(row["operationId"])
        operations.update(
            operation_id=operation_id,
            state="processing",
            result_patch={"startedAt": utc_now()},
        )
        document_id = str((row.get("payload") or {}).get("documentId") or "")
        materials = LocalProjectMaterialsRepository(compatibility.runtime)
        try:
            document = materials.document_text(document_id)
            text = str(document.get("content") or "").strip()
            if not text:
                raise LocalRuntimeError(
                    422,
                    "local_document_empty",
                    "本机资料没有可解析正文",
                )
            if settings["parseModelMode"] == "local":
                summary, model_name = _ollama_summarize(text)
            else:
                completion = compatibility.runtime.private_ai_completion(
                    system_prompt=(
                        "你是项目资料深度解析器。仅根据用户给出的资料生成中文摘要，"
                        "保留事实、主体、时间、承诺、风险和待办，不补造信息。"
                    ),
                    prompt=text[:120_000],
                    creativity_mode="strict",
                )
                summary = str(completion.get("content") or "").strip()
                model_name = str(
                    completion.get("modelName") or "organization_default"
                )
            saved = materials.update_ai_summary(
                document_id,
                summary=summary,
                model_name=model_name,
            )
        except (LocalRuntimeError, OSError, RuntimeError, ValueError) as exc:
            message = (
                exc.message if isinstance(exc, LocalRuntimeError) else str(exc)
            )
            operations.update(
                operation_id=operation_id,
                state="failed_retryable",
                result_patch={"failedAt": utc_now()},
                error_code=(
                    exc.code
                    if isinstance(exc, LocalRuntimeError)
                    else "local_ai_execution_failed"
                ),
                error_message=message,
            )
            return {
                "processed": 0,
                "failed": 1,
                "skipped": 0,
                "status": "failed_retryable",
                "governor_reason": message,
                "governor_retry_after": 0,
            }
        operations.update(
            operation_id=operation_id,
            state="completed",
            result_patch={
                "completedAt": utc_now(),
                "summaryHash": saved["summaryHash"],
                "modelName": model_name,
            },
        )
        return {
            "processed": 1,
            "failed": 0,
            "skipped": 0,
            "status": "completed",
            "governor_reason": "",
            "governor_retry_after": 0,
        }
    finally:
        _LOCAL_AI_EXECUTION_LOCK.release()


@router.post(r"local-ai/run-now")
def local_ai_run_now(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    with compatibility.runtime.pinned_workspace_context():
        return _local_ai_run_now_pinned(compatibility, request)


@router.get(r"local-ai/settings")
def local_ai_settings(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del request, match
    return {
        **_local_ai_settings(compatibility),
        "state": "ready",
        "message": "",
        "pollingEnabled": bool(_local_ai_queue_rows(compatibility)),
    }


@router.put(r"local-ai/settings")
def update_local_ai_settings(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    allowed = set(_LOCAL_AI_DEFAULTS)
    unknown = sorted(set(request.body) - allowed)
    if unknown:
        raise LocalRuntimeError(
            422,
            "local_ai_setting_unknown",
            f"不支持的本机解析设置：{', '.join(unknown)}",
        )
    current = _local_ai_settings(compatibility)
    merged = {
        key: value
        for key, value in {**current, **dict(request.body)}.items()
        if key in allowed
    }
    priority = str(merged.get("priorityClientId") or "")
    if priority and priority not in _local_ai_projects(compatibility):
        raise LocalRuntimeError(
            404,
            "project_missing",
            "优先解析项目不存在或当前成员不可见",
        )
    operations = _local_operations(compatibility)
    started = operations.begin(
        idempotency_key=request.idempotency_key,
        command_type=_LOCAL_AI_SETTINGS_COMMAND,
        aggregate_type="device_personal_preference",
        aggregate_id="local-ai-settings",
        payload={
            "changedFields": sorted(request.body),
            "settingsHash": sha256_text(
                json.dumps(merged, ensure_ascii=False, sort_keys=True)
            ),
        },
        initial_result={
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    if not started.get("idempotentReplay"):
        operations.update(
            operation_id=str(started["operationId"]),
            state="completed",
            result_patch={"settings": merged},
        )
    return local_ai_settings(compatibility, request, None)


@router.get(r"local-ai/coverage")
def local_ai_coverage(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    requested = str(request.query.get("client_id") or "")
    project_ids = (
        [requested] if requested else _local_ai_projects(compatibility)
    )
    candidates = LocalProjectMaterialsRepository(
        compatibility.runtime
    ).optimization_candidates(project_ids)
    per_project = []
    for project_id in project_ids:
        rows = [
            item for item in candidates if item["projectId"] == project_id
        ]
        deep_read = sum(bool(item["deepRead"]) for item in rows)
        per_project.append(
            {
                "clientId": project_id,
                "documents": len(rows),
                "deepRead": deep_read,
                "coverage": (
                    round(deep_read / len(rows), 3) if rows else 0
                ),
            }
        )
    total = len(candidates)
    complete = sum(bool(item["deepRead"]) for item in candidates)
    return {
        "perClient": per_project,
        "totalDocuments": total,
        "totalDeepRead": complete,
        "overallCoverage": round(complete / total, 3) if total else 0,
        "state": "ready",
        "message": "",
        "retryable": False,
    }


@router.post(r"local-ai/backfill")
def local_ai_backfill(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    requested = str(request.query.get("client_id") or "")
    project_ids = (
        [requested] if requested else _local_ai_projects(compatibility)
    )
    candidates = LocalProjectMaterialsRepository(
        compatibility.runtime
    ).optimization_candidates(project_ids)
    operations = _local_operations(compatibility)
    created = 0
    retried = 0
    attempted = 0
    for candidate in candidates:
        if candidate["deepRead"]:
            continue
        attempted += 1
        result = operations.begin(
            idempotency_key=(
                "local-ai-document:"
                f"{candidate['documentId']}:{candidate['contentHash']}"
            ),
            command_type=_LOCAL_AI_TASK_TYPE,
            aggregate_type="local_knowledge_document",
            aggregate_id=str(candidate["documentId"]),
            payload={
                "projectId": candidate["projectId"],
                "documentId": candidate["documentId"],
                "contentHash": candidate["contentHash"],
                "modelMode": _local_ai_settings(compatibility)["parseModelMode"],
            },
            initial_result={
                "state": "queued",
                "retryable": True,
                "pollingEnabled": True,
            },
        )
        if not result.get("idempotentReplay"):
            created += 1
        elif str(result.get("state") or "") == "failed_retryable":
            operations.retry(operation_id=str(result["operationId"]))
            retried += 1
    return {
        "scope": requested or "all",
        "created": created,
        "retried": retried,
        "attempted": attempted,
        "documents": len(candidates),
        "taskTypes": ["document_card_generation"],
        "state": "ready",
        "message": "",
        "retryable": False,
    }


@router.get(r"org-integrations/feishu")
def get_feishu_integration(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    return _feishu_runtime_status(compatibility)


@router.post(r"org-integrations/feishu/validate-and-save")
def save_feishu_integration(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    return _cloud_command(
        compatibility,
        request,
        "org-integrations/feishu/validate-and-save",
    )


@router.get(r"feishu-sync/status")
def feishu_sync_status(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    authorization_scope = (
        "personal"
        if str(request.query.get("remoteType") or "") == "docx_document"
        else "organization"
    )
    return _cloud_query(
        compatibility,
        "feishu-sync/status",
        request.query,
        authorization_scope=authorization_scope,
    )


@router.post(r"feishu-sync/calendar/tasks/(?P<task_id>[^/]+)")
def feishu_sync_task(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        f"feishu-sync/calendar/tasks/{match.group('task_id')}",
    )


@router.post(r"feishu-sync/documents")
def feishu_sync_document(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    document_id = str(request.body.get("localId") or "").strip()
    if not document_id:
        raise LocalRuntimeError(
            422,
            "feishu_document_local_id_required",
            "缺少本机文档标识，无法创建飞书文档",
        )
    local_document = LocalProjectMaterialsRepository(
        compatibility.runtime
    ).document_text(document_id)
    body = {
        **dict(request.body),
        "localType": "document",
        "localId": document_id,
        "title": str(
            local_document.get("title")
            or request.body.get("title")
            or "益语同步文档"
        ),
        "content": str(local_document.get("content") or ""),
    }
    return _cloud_command(
        compatibility,
        UiRequest(
            method=request.method,
            path=request.path,
            query=request.query,
            body=body,
            idempotency_key=request.idempotency_key,
        ),
        "feishu-sync/documents",
        authorization_scope="personal",
    )


@router.get(r"feishu-doc-import/status")
def feishu_import_status(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    return _cloud_query(
        compatibility,
        "feishu-doc-import/status",
    )


@router.post(r"feishu-doc-import/search")
def feishu_import_search(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    return _cloud_command(
        compatibility,
        request,
        "feishu-doc-import/search",
        authorization_scope="personal",
    )


@router.post(r"feishu-doc-import/resolve-links")
def feishu_import_resolve(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    return _cloud_command(
        compatibility,
        request,
        "feishu-doc-import/resolve-links",
        authorization_scope="personal",
    )


@router.get(r"logs")
def system_logs(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    try:
        return _cloud_query(compatibility, "logs", request.query)
    except LocalRuntimeError as exc:
        if exc.code not in {
            "needs_login",
            "organization_required",
            "failed_retryable",
        }:
            raise
        return {
            "entries": [],
            "dates": [],
            "total": 0,
            "state": "blocked",
            "message": f"{exc.message}；本机结构化系统日志投影尚未接通",
            "retryable": True,
        }


@router.get(r"logs/dates")
def system_log_dates(compatibility: Any, request: UiRequest, match: Any) -> list[str]:
    del request, match
    result = _cloud_query(compatibility, "logs/dates")
    if isinstance(result, list):
        return result
    return list(result.get("dates") or [])


@router.get(r"logs/export")
def export_system_logs(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> PlainTextResponse:
    del match
    result = _cloud_query(compatibility, "logs", request.query)
    lines = [
        canonical_line
        for canonical_line in (
            json.dumps(entry, ensure_ascii=False, sort_keys=True)
            for entry in result.get("entries") or []
        )
    ]
    return PlainTextResponse(
        "\n".join(lines) + ("\n" if lines else ""),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="yiyu-system-logs.ndjson"'},
    )


@router.get(r"agent-run-logs")
def agent_run_logs(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    try:
        return _cloud_query(
            compatibility,
            "agent-run-logs",
            request.query,
        )
    except LocalRuntimeError as exc:
        if exc.code not in {
            "needs_login",
            "organization_required",
            "failed_retryable",
        }:
            raise
        return {
            "filter": {
                "client_id": request.query.get("client_id"),
                "actor_type": request.query.get("actor_type"),
                "limit": int(request.query.get("limit") or 50),
            },
            "total": 0,
            "items": [],
            "state": "blocked",
            "message": exc.message,
            "retryable": True,
        }


@router.get(r"tool-registry")
def tool_registry(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del match
    asr = _sensevoice_status(compatibility)
    ollama = _ollama_health()
    local_ai = _local_ai_health(compatibility)
    feishu = _feishu_runtime_status(compatibility)
    tools = [
        {
            "tool_name": "local_asr",
            "description": "本机音频转写",
            "risk_level": "medium",
            "approval_required": False,
            "status": "available" if asr["state"] == "ready" else "missing",
            "external_side_effect": "local_device_only",
            "audit_note": asr.get("downloadError") or "",
        },
        {
            "tool_name": "ollama",
            "description": "本机 Ollama 模型",
            "risk_level": "medium",
            "approval_required": True,
            "status": "available" if ollama["running"] else "missing",
            "external_side_effect": "local_device_only",
            "audit_note": ollama.get("error") or "",
        },
        {
            "tool_name": "local_ai",
            "description": "本地深读队列",
            "risk_level": "medium",
            "approval_required": False,
            "status": (
                "available"
                if local_ai.get("state") == "ready"
                else "partial"
            ),
            "external_side_effect": "local_device_only",
            "audit_note": str(local_ai.get("reason") or ""),
        },
        {
            "tool_name": "feishu",
            "description": "飞书日历、文档与资料导入",
            "risk_level": "high",
            "approval_required": True,
            "status": "available" if feishu.get("enabled") else "partial",
            "external_side_effect": "strict_cloud_ledger",
            "audit_note": feishu.get("lastValidationMessage") or "",
        },
    ]
    status_filter = str(request.query.get("status_filter") or "")
    risk_level = str(request.query.get("risk_level") or "")
    tools = [
        item
        for item in tools
        if (not status_filter or item["status"] == status_filter)
        and (not risk_level or item["risk_level"] == risk_level)
    ]
    by_status: dict[str, int] = {}
    for item in tools:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    return {
        "version": "strict-platform-local-v1",
        "total": len(tools),
        "by_status": by_status,
        "tools": tools,
        "schema_completeness": {
            "localAsrRuntime": False,
            "localAsrModelFiles": bool(asr["installed"]),
            "ollamaRuntime": bool(ollama["running"]),
            "localAiQueueAuthority": local_ai.get("state") == "ready",
            "feishuCredentialVault": bool(feishu.get("appId")),
        },
    }


@router.post(r"support-requests")
def create_support_request(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    return _cloud_command(
        compatibility,
        request,
        "support-requests",
    )


@router.get(r"support-requests")
def list_support_requests(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> Any:
    del match
    result = _cloud_query(
        compatibility,
        "support-requests",
        request.query,
    )
    return list(result.get("items") or [])


@router.post(r"support-requests/(?P<request_id>[^/]+)/resolve")
def resolve_support_request(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _cloud_command(
        compatibility,
        request,
        f"support-requests/{match.group('request_id')}/resolve",
    )


@router.get(r"software-feedback")
def list_software_feedback(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    return _cloud_query(compatibility, "software-feedback")


@router.post(r"software-feedback")
def create_software_feedback(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    screenshot = request.body.get("screenshot")
    screenshot_name = str(getattr(screenshot, "filename", "") or "").strip()
    local_screenshot: dict[str, Any] | None = None
    if screenshot_name:
        screenshot_file = getattr(screenshot, "file", None)
        if screenshot_file is None or not callable(
            getattr(screenshot_file, "read", None)
        ):
            raise LocalRuntimeError(
                422,
                "feedback_screenshot_unreadable",
                "反馈截图无法读取，请重新选择后提交",
            )
        screenshot_bytes = screenshot_file.read()
        if not isinstance(screenshot_bytes, bytes):
            raise LocalRuntimeError(
                422,
                "feedback_screenshot_unreadable",
                "反馈截图无法读取，请重新选择后提交",
            )
        local_screenshot = LocalFeedbackArtifactRepository(
            compatibility.runtime
        ).store_screenshot(
            data=screenshot_bytes,
            media_type=str(getattr(screenshot, "content_type", "") or ""),
            idempotency_key=request.idempotency_key,
        )
    payload = {
        key: value
        for key, value in request.body.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }
    if local_screenshot is not None:
        payload.update(
            {
                "screenshotRequested": True,
                "screenshotName": screenshot_name,
                "screenshotObjectId": local_screenshot["objectId"],
                "screenshotContentHash": local_screenshot["contentHash"],
                "screenshotMediaType": local_screenshot["mediaType"],
                "screenshotByteSize": local_screenshot["byteSize"],
            }
        )
    result = compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/platform-integrations/command",
        payload={
            "resourcePath": "software-feedback",
            "authorizationScope": "organization",
            "method": request.method,
            "query": dict(request.query),
            "payload": payload,
        },
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    command_result = result.get("result")
    if not isinstance(command_result, dict):
        raise LocalRuntimeError(
            502,
            "platform_command_result_invalid",
            "组织云软件反馈命令返回了无效结果",
        )
    if local_screenshot is None:
        return command_result
    return {
        **command_result,
        "localScreenshotPath": local_screenshot["path"],
        "localScreenshotObjectId": local_screenshot["objectId"],
    }


@router.post(r"recordings/transcribe-local-audio")
def transcribe_recording(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    audio_path = str(request.body.get("audioPath") or "").strip()
    if not audio_path:
        raise LocalRuntimeError(422, "audio_path_required", "录音文件路径不能为空")
    source = Path(audio_path).expanduser().resolve()
    strict_data_root = compatibility.runtime.database_path.parent.resolve()
    if source != strict_data_root and strict_data_root not in source.parents:
        raise LocalRuntimeError(
            403,
            "recording_path_outside_strict_data",
            "只允许转写当前严格新版数据目录中的录音",
        )
    language = str(request.body.get("language") or "auto")
    operations = _local_operations(compatibility)
    receipt = operations.begin(
        idempotency_key=request.idempotency_key,
        command_type="recordings.transcribe_local_audio",
        aggregate_type="local_audio_transcription",
        aggregate_id=sha256_text(str(source)),
        payload={
            "audioPathHash": sha256_text(str(source)),
            "language": language,
            "diarizationRequested": bool(request.body.get("diarization", True)),
        },
        initial_result={
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    replay_output = receipt.get("output")
    if receipt.get("idempotentReplay") and isinstance(replay_output, dict):
        return {**replay_output, **receipt}
    try:
        outcome = run_recording_transcription(
            _model_root(compatibility),
            str(source),
            language=language,
        )
        output = {
            **_transcription_response(outcome.result),
            "sourceFormat": outcome.source_format,
            "transcodedToWav": outcome.transcoded_to_wav,
            "dialogueText": outcome.dialogue_text,
            "numSpeakers": outcome.num_speakers,
            "diarizationUsed": outcome.diarization_used,
            "diarizationError": outcome.diarization_error,
        }
    except Exception as exc:  # noqa: BLE001
        message = f"{exc.__class__.__name__}：{exc}"
        output = {
            "success": False,
            "text": "",
            "durationMs": 0,
            "elapsedMs": 0,
            "language": language,
            "segments": [],
            "errorMessage": message,
            "retryable": True,
            "pollingEnabled": False,
            "sourceFormat": source.suffix.lstrip(".").lower(),
            "transcodedToWav": False,
            "dialogueText": "",
            "numSpeakers": 0,
            "diarizationUsed": False,
            "diarizationError": None,
        }
        failed = operations.update(
            operation_id=str(receipt["operationId"]),
            state="failed_retryable",
            result_patch={"success": False, "output": output},
            error_code="local_asr_execution_failed",
            error_message=message,
        )
        return {**output, **failed}
    completed = operations.update(
        operation_id=str(receipt["operationId"]),
        state="completed",
        result_patch={"success": True, "output": output},
    )
    return {**output, **completed}


@router.post(r"recordings/summarize-meeting-minutes")
def summarize_recording(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    transcript = str(request.body.get("transcript") or "").strip()
    dialogue = str(request.body.get("dialogueText") or "").strip()
    source = dialogue or transcript
    if not source:
        raise LocalRuntimeError(422, "meeting_transcript_required", "请先完成录音转写")
    if len(source) > 120_000:
        raise LocalRuntimeError(413, "meeting_transcript_too_large", "会议转写内容过长，请分段处理")
    title_hint = str(request.body.get("taskTitleHint") or "").strip()
    language_hint = str(request.body.get("languageHint") or "").strip()
    receipt, output = _run_private_ai_operation(
        compatibility,
        request,
        command_type="recordings.summarize_meeting_minutes",
        aggregate_id="meeting-minutes",
        safe_payload={
            "transcriptChars": len(transcript),
            "dialogueChars": len(dialogue),
            "hasTitleHint": bool(title_hint),
            "languageHint": language_hint[:32],
            "numSpeakers": int(request.body.get("numSpeakers") or 0),
        },
        system_prompt=(
            "你是益语智库的会议纪要助手，当前处理任务录音。只能依据用户提供的转写内容，"
            "不得补写未出现的事实。直接返回 Markdown 纪要，不要返回 JSON，"
            "不要附加说明或代码围栏。"
            "纪要将直接写入任务详情，应紧扣当前任务，压缩口语、重复和无关寒暄，"
            "优先提炼讨论要点、明确结论、行动项和待确认事项；没有的类别不要硬凑。"
            "正文控制在 1200 个中文字符以内，不要复述整篇转写。"
        ),
        prompt=(
            (f"任务标题提示：{title_hint}\n" if title_hint else "")
            + (f"语言提示：{language_hint}\n" if language_hint else "")
            + f"转写内容：\n{source}"
        ),
        parser=_parse_meeting_minutes_output,
    )
    if output is not None:
        return {
            "success": True,
            "title": output["title"],
            "minutesMd": output["minutesMd"],
            "errorMessage": None,
            **receipt,
        }
    return {
        "success": False,
        "title": "",
        "minutesMd": "",
        "errorMessage": receipt.get("message") or receipt.get("error"),
        **receipt,
    }


@router.post(r"ai-command/parse-steps")
def parse_ai_command(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    text = str(request.body.get("text") or "").strip()
    if not text:
        raise LocalRuntimeError(422, "ai_command_text_required", "请输入要解析的指令")
    if len(text) > 6_000:
        raise LocalRuntimeError(413, "ai_command_text_too_large", "指令过长，请精简后重试")
    receipt, output = _run_private_ai_operation(
        compatibility,
        request,
        command_type="ai_command.parse_steps",
        aggregate_id="natural-language-command",
        safe_payload={"textChars": len(text)},
        system_prompt=(
            "你是益语智库的任务规划助手。把自然语言指令拆为可执行步骤，"
            "不执行任何步骤，不虚构已完成状态。只返回严格 JSON："
            '{"steps":[{"action":"动作","basis":"依据",'
            '"deliverable":"交付物"}]}。'
        ),
        prompt=f"当前日期：{utc_now()[:10]}\n用户指令：\n{text}",
        parser=_parse_command_steps_output,
    )
    if output is not None:
        return {
            "steps": output["steps"],
            "model_used": output["model"],
            "fallback_reason": None,
            **receipt,
        }
    return {
        "steps": [],
        "model_used": None,
        "fallback_reason": receipt.get("message") or receipt.get("error"),
        **receipt,
    }


@router.post(r"local/tasks/tag-suggestions")
def task_tag_suggestions(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    title = str(request.body.get("title") or "").strip()
    description = str(request.body.get("desc") or "").strip()
    module = str(request.body.get("module") or "").strip()
    collaborators = [
        str(item).strip()
        for item in request.body.get("collaboratorNames") or []
        if str(item).strip()
    ][:20]
    if not title and not description:
        raise LocalRuntimeError(422, "task_tag_source_required", "请先填写任务标题或说明")
    prompt = (
        f"标题：{title}\n说明：{description}\n模块：{module}\n"
        f"协作者：{'、'.join(collaborators)}\n"
        f"截止日期：{str(request.body.get('dueDate') or '')}"
    )
    receipt, output = _run_private_ai_operation(
        compatibility,
        request,
        command_type="tasks.suggest_tags",
        aggregate_id="task-draft",
        safe_payload={
            "titleChars": len(title),
            "descriptionChars": len(description),
            "moduleProvided": bool(module),
            "collaboratorCount": len(collaborators),
            "dueDateProvided": bool(request.body.get("dueDate")),
        },
        system_prompt=(
            "你是益语智库的任务标签助手。根据任务草稿建议 0 到 5 个简短中文标签；"
            "信息不足可以返回空数组，不得虚构项目或人员。"
            '只返回严格 JSON：{"suggestedTags":["标签"]}。'
        ),
        prompt=prompt,
        parser=_parse_tag_suggestions_output,
    )
    if output is not None:
        return {
            "suggestedTags": output["suggestedTags"],
            **receipt,
        }
    return {
        "suggestedTags": [],
        **receipt,
    }


@router.get(r"local/bot-members/(?P<bot_id>[^/]+)/weekly-summary")
def bot_weekly_summary(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    week = str(request.query.get("week") or "")
    week_start, week_end = _week_window(week)
    bot_id = match.group("bot_id")
    try:
        bot = _organization_query(compatibility, f"bots/{bot_id}")
        plan_result = _organization_query(
            compatibility,
            f"bots/{bot_id}/task-plans",
            {"limit": "200"},
        )
        plans = [
            item
            for item in plan_result.get("items") or []
            if isinstance(item, dict)
            and _within_window(item.get("created_at"), week_start, week_end)
        ]
        received: list[dict[str, Any]] = []
        actions_summary: dict[str, int] = {}
        success_count = 0
        failed_count = 0
        total_duration_ms = 0
        total_actions = 0
        for plan in plans:
            progress = _organization_query(
                compatibility,
                f"bots/task-plans/{plan['id']}/progress",
            )
            subtasks = [
                item
                for item in progress.get("subtasks") or []
                if isinstance(item, dict)
            ]
            plan_success = sum(
                1
                for item in subtasks
                if str(item.get("status") or "") in {"success", "completed"}
            )
            plan_failed = sum(
                1
                for item in subtasks
                if str(item.get("status") or "") in {"failed", "cancelled"}
            )
            success_count += plan_success
            failed_count += plan_failed
            total_actions += len(subtasks)
            for item in subtasks:
                action = str(
                    item.get("tool")
                    or item.get("module")
                    or item.get("action")
                    or "unknown"
                )
                actions_summary[action] = actions_summary.get(action, 0) + 1
                total_duration_ms += int(
                    item.get("durationMs") or item.get("duration_ms") or 0
                )
            received.append(
                {
                    "plan_id": str(plan.get("id") or ""),
                    "plan_title": str(plan.get("plan_title") or ""),
                    "human_initiator": str(
                        plan.get("human_initiator_id") or ""
                    ),
                    "status": str(plan.get("status") or ""),
                    "execution_status": str(
                        progress.get("execution_status")
                        or plan.get("execution_state")
                        or "not_started"
                    ),
                    "client_id": str(plan.get("client_id") or ""),
                    "created_at": str(plan.get("created_at") or ""),
                    "subtask_count": len(subtasks),
                    "success_count": plan_success,
                }
            )
    except LocalRuntimeError as exc:
        return {
            "bot": {
                "id": bot_id,
                "actor_id": "",
                "display_name": "",
                "department_id": "",
                "department_name": "",
            },
            "week_start": week_start,
            "week_end": week_end,
            "plans_received": [],
            "actions_summary": {},
            "total_actions": 0,
            "success_count": 0,
            "failed_count": 0,
            "success_rate": 0,
            "avg_duration_ms": 0,
            "state": "blocked",
            "message": exc.message,
            "errorCode": exc.code,
            "retryable": True,
        }
    return {
        "bot": {
            "id": str(bot.get("id") or bot_id),
            "actor_id": str(bot.get("actor_id") or ""),
            "display_name": str(bot.get("display_name") or ""),
            "department_id": str(bot.get("department_id") or ""),
            "department_name": str(bot.get("department_name") or ""),
        },
        "week_start": week_start,
        "week_end": week_end,
        "plans_received": received,
        "actions_summary": actions_summary,
        "total_actions": total_actions,
        "success_count": success_count,
        "failed_count": failed_count,
        "success_rate": (
            round(success_count / total_actions, 3) if total_actions else 0
        ),
        "avg_duration_ms": (
            total_duration_ms // total_actions if total_actions else 0
        ),
        "state": "ready",
        "message": "",
        "retryable": False,
    }


@router.get(r"local/users/(?P<user_id>[^/]+)/ai-delegations")
def user_ai_delegations(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    week = str(request.query.get("week") or "")
    week_start, week_end = _week_window(week)
    user_id = match.group("user_id")
    try:
        authorization = compatibility.runtime.cloud_query(
            "/api/v2/authorization/current",
        )
        current_membership_id = str(
            authorization.get("membershipId") or ""
        )
        if (
            user_id != current_membership_id
            and str(authorization.get("systemRole") or "") != "admin"
        ):
            raise LocalRuntimeError(
                403,
                "ai_delegation_read_forbidden",
                "普通成员只能查看本人的 AI 委派复盘",
            )
        bots_result = _organization_query(
            compatibility,
            "bots",
            {"status": "active"},
        )
        plans: list[dict[str, Any]] = []
        for bot in bots_result.get("items") or []:
            if not isinstance(bot, dict):
                continue
            plan_result = _organization_query(
                compatibility,
                f"bots/{bot['id']}/task-plans",
                {"limit": "200"},
            )
            for plan in plan_result.get("items") or []:
                if (
                    not isinstance(plan, dict)
                    or str(plan.get("human_initiator_id") or "") != user_id
                    or not _within_window(
                        plan.get("created_at"),
                        week_start,
                        week_end,
                    )
                ):
                    continue
                progress = _organization_query(
                    compatibility,
                    f"bots/task-plans/{plan['id']}/progress",
                )
                subtasks = [
                    item
                    for item in progress.get("subtasks") or []
                    if isinstance(item, dict)
                ]
                success_count = sum(
                    1
                    for item in subtasks
                    if str(item.get("status") or "")
                    in {"success", "completed"}
                )
                failed_count = sum(
                    1
                    for item in subtasks
                    if str(item.get("status") or "")
                    in {"failed", "cancelled"}
                )
                plans.append(
                    {
                        "plan_id": str(plan.get("id") or ""),
                        "plan_title": str(plan.get("plan_title") or ""),
                        "bot_id": str(bot.get("id") or ""),
                        "bot_name": str(bot.get("display_name") or ""),
                        "client_id": str(plan.get("client_id") or ""),
                        "status": str(plan.get("status") or ""),
                        "execution_status": str(
                            progress.get("execution_status")
                            or plan.get("execution_state")
                            or "not_started"
                        ),
                        "created_at": str(plan.get("created_at") or ""),
                        "subtask_count": len(subtasks),
                        "success_count": success_count,
                        "failed_count": failed_count,
                        "summary": subtasks[:3],
                    }
                )
        snapshot = compatibility.runtime.business_snapshot(refresh=False)
        manual_tasks = sum(
            1
            for item in snapshot.get("tasks") or []
            if isinstance(item, dict)
            and str(item.get("createdByMembershipId") or "") == user_id
            and _within_window(item.get("createdAt"), week_start, week_end)
        )
    except LocalRuntimeError as exc:
        return {
            "user_id": user_id,
            "week_start": week_start,
            "week_end": week_end,
            "plans": [],
            "summary": {
                "total_plans": 0,
                "approved": 0,
                "executing": 0,
                "completed": 0,
                "failed": 0,
            },
            "ai_collaboration_score": 0,
            "user_manual_tasks": 0,
            "state": "blocked",
            "message": exc.message,
            "errorCode": exc.code,
            "retryable": True,
        }
    approved = sum(1 for item in plans if item["status"] == "approved")
    executing = sum(
        1
        for item in plans
        if item["execution_status"] in {"pending_execute", "running"}
    )
    completed = sum(
        1 for item in plans if item["execution_status"] == "success"
    )
    failed = sum(
        1 for item in plans if item["execution_status"] == "failed"
    )
    return {
        "user_id": user_id,
        "week_start": week_start,
        "week_end": week_end,
        "plans": plans,
        "summary": {
            "total_plans": len(plans),
            "approved": approved,
            "executing": executing,
            "completed": completed,
            "failed": failed,
        },
        "ai_collaboration_score": (
            round(completed / (completed + manual_tasks), 3)
            if completed + manual_tasks
            else 0
        ),
        "user_manual_tasks": manual_tasks,
        "state": "ready",
        "message": "",
        "retryable": False,
    }


@router.get(r"runtime/analysis-migration-metrics")
def analysis_migration_metrics(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request, match
    return _cloud_query(
        compatibility,
        "runtime/analysis-migration-metrics",
    )


@router.get(r"runtime/generation-state")
def generation_state(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    return _cloud_query(
        compatibility,
        "runtime/generation-state",
        request.query,
    )


@router.post(r"runtime/generation-state/reset")
def reset_generation_state(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    return _cloud_command(
        compatibility,
        request,
        "runtime/generation-state/reset",
    )


@router.get(r"runtime/run-log/(?P<run_id>[^/]+)")
def runtime_run_log(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del request
    return _cloud_query(
        compatibility,
        f"runtime/run-log/{match.group('run_id')}",
    )


@router.get(r"runtime/workspace-chat-diagnostics")
def workspace_chat_diagnostics(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    return _cloud_query(
        compatibility,
        "runtime/workspace-chat-diagnostics",
        request.query,
    )


@router.get(r"runtime/workspace-answer-value-diagnostics")
def workspace_answer_diagnostics(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    return _cloud_query(
        compatibility,
        "runtime/workspace-answer-value-diagnostics",
        request.query,
    )


@router.post(r"runtime/llm-healthcheck")
def llm_healthcheck(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    ai = compatibility.ai_runtime()
    prompt = str(request.body.get("prompt") or "只回复 OK").strip()[:500]
    receipt, output = _run_private_ai_operation(
        compatibility,
        request,
        command_type="runtime.llm_healthcheck",
        aggregate_id=str(ai.get("organizationId") or "organization-ai"),
        safe_payload={
            "requestedProvider": str(request.body.get("provider") or "")[:100],
            "requestedModel": str(request.body.get("model") or "")[:200],
            "probePromptHash": sha256_text(prompt),
        },
        system_prompt=(
            "这是组织统一模型连通性探测。不要复述用户输入或任何凭据，"
            "只回复 OK。"
        ),
        prompt=prompt,
        parser=_parse_healthcheck_output,
    )
    success = output is not None
    error_code = str(receipt.get("errorCode") or "")
    error_kind = (
        "connect_timeout"
        if error_code == "ai_unreachable"
        else "read_timeout"
        if error_code == "ai_timeout"
        else "auth_error"
        if error_code == "ai_request_failed"
        else "unknown"
        if not success
        else None
    )
    return {
        "provider": str(ai.get("provider") or ""),
        "model": str(
            ai.get("model")
            or (output.get("model") if output is not None else "")
            or receipt.get("modelUsed")
            or ""
        ),
        "success": success,
        "latencyMs": int(receipt.get("latencyMs") or 0),
        "error": None if success else receipt.get("message") or receipt.get("error"),
        "errorKind": error_kind,
        "probeExecuted": bool(success or receipt.get("state") == "failed_retryable"),
        **receipt,
    }


@router.post(r"runtime/llm-provider-probe")
def llm_provider_probe(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    del match
    health = llm_healthcheck(compatibility, request, None)
    return {
        "clientId": request.body.get("clientId"),
        "prompt": str(request.body.get("prompt") or ""),
        "generatedAt": utc_now(),
        "results": [health],
        "retryable": health["retryable"],
        "probeExecuted": health["probeExecuted"],
    }
