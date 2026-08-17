from __future__ import annotations

import mimetypes
import threading
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from strict_common.ids import new_id

from ..project_materials_local import LocalProjectMaterialsRepository
from ..local_asr.models import SENSE_VOICE_MODEL, model_ready
from ..local_asr.subprocess_runner import run_local_asr_subprocess
from ..runtime import LocalRuntimeError
from ..transcript_semantic_correction import correct_project_transcript
from .gc04_tasks import _task_ui
from .project_materials import register_and_process_local_materials
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("gc04_task_attachments", pin_workspace=True)
_MATERIAL_ROOT = "/api/v2/domain/project-materials"
_TASK_ROOT = "/api/v2/domain/tasks"


def _store(compatibility: Any) -> LocalProjectMaterialsRepository:
    return LocalProjectMaterialsRepository(compatibility.runtime)


def _task(compatibility: Any, task_id: str) -> dict[str, Any]:
    result = compatibility.runtime.cloud_query(
        f"{_TASK_ROOT}/{quote(task_id, safe='')}"
    )
    task = result.get("task")
    if not isinstance(task, dict):
        raise LocalRuntimeError(502, "task_receipt_invalid", "组织云任务回执无效")
    return task


def _save_uploaded_file(compatibility: Any, uploaded: Any) -> Path:
    stream = getattr(uploaded, "file", None)
    if stream is None or not callable(getattr(stream, "read", None)):
        raise LocalRuntimeError(422, "attachment_file_required", "请选择要上传的附件")
    name = str(getattr(uploaded, "filename", "") or "attachment.bin")
    raw = stream.read(100 * 1024 * 1024 + 1)
    if not isinstance(raw, bytes):
        raise LocalRuntimeError(422, "attachment_file_unreadable", "任务附件无法读取")
    if len(raw) > 100 * 1024 * 1024:
        raise LocalRuntimeError(413, "attachment_too_large", "单个附件不得超过 100MB")
    root = compatibility.runtime.database_path.parent / "imports" / "task-attachments"
    root.mkdir(parents=True, exist_ok=True)
    suffix = Path(name).suffix[:16]
    temporary = root / f".{new_id()}{suffix}"
    temporary.write_bytes(raw)
    return temporary


def _register(
    compatibility: Any,
    request: UiRequest,
    *,
    task_id: str,
    task: dict[str, Any],
    local: dict[str, Any],
) -> dict[str, Any]:
    client_id = str(task.get("client_id") or task.get("clientId") or "")
    if not client_id:
        raise LocalRuntimeError(
            422,
            "task_attachment_project_required",
            "任务附件必须归入一个项目，避免形成无归属资料",
        )
    store = _store(compatibility)
    store.bind_pending_materials(project_id=client_id, local_materials=[local])
    try:
        registered = compatibility.runtime.cloud_command(
            "POST",
            f"{_MATERIAL_ROOT}/projects/{quote(client_id, safe='')}/materials/register-metadata",
            payload={
                "materials": [
                    {
                        "localSourceId": local["localSourceId"],
                        "fileName": local["fileName"],
                        "contentHash": local["contentHash"],
                        "byteSize": local["byteSize"],
                        "mediaType": local["mediaType"],
                        "relationKind": "task",
                        "relationId": task_id,
                    }
                ]
            },
            idempotency_key=f"{request.idempotency_key}:metadata",
            refresh_business=False,
        )
        document = dict((registered.get("documents") or [])[0])
        if not document.get("documentId"):
            raise LocalRuntimeError(502, "task_attachment_metadata_invalid", "任务附件元数据回执无效")
        store.bind_cloud_documents(
            project_id=client_id,
            local_materials=[local],
            cloud_documents=[document],
        )
        return store.bind_task_attachment(
            project_id=client_id,
            document_id=str(document["documentId"]),
            task_id=task_id,
        )
    except LocalRuntimeError:
        pending_id = f"local-pending:{local['localSourceId']}"
        store.bind_task_attachment(
            project_id=client_id,
            document_id=pending_id,
            task_id=task_id,
        )
        raise


@router.post(r"tasks/(?P<task_id>[^/]+)/attachments")
def upload_task_attachment(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    task = _task(compatibility, task_id)
    client_id = str(task.get("client_id") or "")
    if not client_id:
        raise LocalRuntimeError(422, "task_attachment_project_required", "任务附件必须先关联项目")
    temporary = _save_uploaded_file(compatibility, request.body.get("file"))
    try:
        imported = _store(compatibility).import_paths(
            project_id=client_id,
            mode="file",
            paths=[temporary],
            idempotency_key=f"{request.idempotency_key}:local",
        )
        local = dict((imported.get("materials") or [])[0])
        uploaded = request.body.get("file")
        original_name = str(getattr(uploaded, "filename", "") or local.get("fileName") or "任务附件")
        local["fileName"] = original_name
        local["title"] = original_name
        local["mediaType"] = str(
            getattr(uploaded, "content_type", "")
            or mimetypes.guess_type(original_name)[0]
            or local.get("mediaType")
            or "application/octet-stream"
        )
        _register(
            compatibility, request, task_id=task_id, task=task, local=local
        )
    finally:
        temporary.unlink(missing_ok=True)
    return _task_ui(compatibility, _task(compatibility, task_id))


@router.post(r"tasks/(?P<task_id>[^/]+)/attachments/from-markdown")
def upload_task_markdown(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    task = _task(compatibility, task_id)
    client_id = str(task.get("client_id") or "")
    if not client_id:
        raise LocalRuntimeError(422, "task_attachment_project_required", "任务附件必须先关联项目")
    local = _store(compatibility).import_text(
        project_id=client_id,
        title=str(request.body.get("title") or "任务材料"),
        content=str(request.body.get("markdown") or ""),
        idempotency_key=f"{request.idempotency_key}:local",
    )
    _register(compatibility, request, task_id=task_id, task=task, local=local)
    return _task_ui(compatibility, _task(compatibility, task_id))


@router.post(r"tasks/(?P<task_id>[^/]+)/recordings")
def archive_task_recording(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    task = _task(compatibility, task_id)
    client_id = str(task.get("client_id") or "")
    if not client_id:
        raise LocalRuntimeError(422, "task_attachment_project_required", "任务录音必须先关联项目")
    raw_path = str(request.body.get("audioPath") or "").strip()
    try:
        source = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LocalRuntimeError(404, "recording_file_missing", "本机录音文件不存在") from exc
    managed_root = compatibility.runtime.database_path.parent.resolve()
    if managed_root not in source.parents or not source.is_file():
        raise LocalRuntimeError(403, "recording_path_outside_managed_root", "只能归档严格新版数据目录中的录音")
    imported = _store(compatibility).import_paths(
        project_id=client_id,
        mode="file",
        paths=[source],
        idempotency_key=f"{request.idempotency_key}:local",
    )
    local = dict((imported.get("materials") or [])[0])
    _register(compatibility, request, task_id=task_id, task=task, local=local)
    return _task_ui(compatibility, _task(compatibility, task_id))


@router.delete(r"tasks/(?P<task_id>[^/]+)/attachments/(?P<attachment_id>[^/]+)")
def delete_task_attachment(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    attachment_id = unquote(match.group("attachment_id"))
    task = _task(compatibility, task_id)
    client_id = str(task.get("client_id") or "")
    local = _store(compatibility)
    attachment = next(
        (item for item in local.task_attachments(task_id) if item["id"] == attachment_id),
        None,
    )
    if attachment is None:
        raise LocalRuntimeError(404, "task_attachment_missing", "任务附件不存在")
    local.delete_task_attachment_local(task_id=task_id, attachment_id=attachment_id)
    cloud_deleted = False
    if not attachment_id.startswith("local-pending:"):
        compatibility.runtime.cloud_command(
            "DELETE",
            f"{_MATERIAL_ROOT}/projects/{quote(client_id, safe='')}/documents/"
            f"{quote(attachment_id, safe='')}",
            payload={"expectedVersion": int(attachment.get("version") or 1)},
            idempotency_key=f"{request.idempotency_key}:metadata",
            refresh_business=False,
        )
        cloud_deleted = True
    return {"deleted": True, "knowledgeDeleted": cloud_deleted, "fileDeleted": True}


@router.post(r"tasks/(?P<task_id>[^/]+)/attachments/(?P<attachment_id>[^/]+)/retry-transcription")
def retry_task_transcription(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    attachment_id = unquote(match.group("attachment_id"))
    runtime = compatibility.runtime
    pinned_sandbox = runtime.capture_sandbox_context()
    pinned_context = pinned_sandbox.workspace_context
    if pinned_context is None:
        raise LocalRuntimeError(409, "workspace_not_ready", "当前组织工作空间尚未就绪")
    store = LocalProjectMaterialsRepository(runtime, context_provider=lambda: pinned_context)
    attachment = next(
        (item for item in store.task_attachments(task_id) if item["id"] == attachment_id),
        None,
    )
    if attachment is None or not attachment.get("isAudio"):
        raise LocalRuntimeError(404, "task_audio_attachment_missing", "任务录音不存在")
    if not attachment.get("localAvailable"):
        raise LocalRuntimeError(409, "task_audio_local_file_missing", "当前设备没有该录音原件")
    task = _task(compatibility, task_id)
    project_id = str(task.get("client_id") or "")
    if str(attachment.get("processingStatus") or "") in {"queued", "processing"}:
        return _task_ui(compatibility, _task(compatibility, task_id))
    model_root = runtime.database_path.parent / "models"
    if not model_ready(model_root, SENSE_VOICE_MODEL):
        store.set_task_transcription_state(
            task_id=task_id,
            attachment_id=attachment_id,
            status="blocked",
            error="本机 ASR 模型未就绪；录音已保留，可下载模型后重试",
            progress=0,
            stage="等待安装转写组件",
        )
        raise LocalRuntimeError(424, "local_asr_not_connected", "本机 ASR 模型未就绪；录音已保留，可下载模型后重试")
    store.set_task_transcription_state(
        task_id=task_id,
        attachment_id=attachment_id,
        status="queued",
        progress=2,
        stage="等待本机转写",
    )

    def run_in_background() -> None:
        def report(percent: int, stage: str) -> None:
            store.set_task_transcription_state(
                task_id=task_id,
                attachment_id=attachment_id,
                status="processing",
                progress=percent,
                stage=stage,
            )

        try:
            with runtime.prebound_sandbox_context(pinned_sandbox):
                output = run_local_asr_subprocess(
                    model_root=model_root,
                    audio_path=Path(str(attachment["path"])),
                    language="auto",
                    progress_callback=report,
                )
                text = str(output.get("dialogue_text") or output.get("dialogueText") or output.get("text") or "").strip()
                if not text:
                    raise RuntimeError("TaskTranscriptionEmpty")
                text = correct_project_transcript(
                    runtime,
                    project_id=project_id,
                    title=str(attachment.get("title") or "任务录音"),
                    transcript=text,
                    progress_callback=report,
                )
                transcript = store.save_task_transcript(
                    task_id=task_id,
                    attachment_id=attachment_id,
                    text=text,
                    preserve_original=True,
                )
                operation_key = (
                    f"task-transcript:{task_id}:{attachment_id}:"
                    f"{transcript.get('version') or 1}"
                )
                material = store.import_text(
                    project_id=project_id,
                    title=f"{Path(str(attachment.get('title') or '任务录音')).stem}-录音转写",
                    content=text,
                    idempotency_key=operation_key,
                )
                settled = register_and_process_local_materials(
                    runtime=runtime,
                    store=store,
                    project_id=project_id,
                    local_materials=[material],
                    relation_kind="task",
                    relation_id=task_id,
                    idempotency_key=operation_key,
                )
                store.bind_task_attachment(
                    project_id=project_id,
                    document_id=str(settled["documentIds"][0]),
                    task_id=task_id,
                )
        except Exception as exc:  # noqa: BLE001
            store.set_task_transcription_state(
                task_id=task_id,
                attachment_id=attachment_id,
                status="failed_retryable",
                error=f"{exc.__class__.__name__}：{exc}",
                stage="转写失败，可重试",
            )

    threading.Thread(
        target=run_in_background,
        name=f"task-asr-{attachment_id[-12:]}",
        daemon=True,
    ).start()
    return _task_ui(compatibility, _task(compatibility, task_id))


@router.get(r"tasks/(?P<task_id>[^/]+)/attachments/(?P<attachment_id>[^/]+)/transcript")
def get_task_transcript(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    del request
    return _store(compatibility).task_transcript(
        task_id=unquote(match.group("task_id")),
        attachment_id=unquote(match.group("attachment_id")),
    )


@router.put(r"tasks/(?P<task_id>[^/]+)/attachments/(?P<attachment_id>[^/]+)/transcript")
def update_task_transcript(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    attachment_id = unquote(match.group("attachment_id"))
    current = _store(compatibility).task_transcript(
        task_id=task_id, attachment_id=attachment_id
    )
    return _store(compatibility).save_task_transcript(
        task_id=task_id,
        attachment_id=attachment_id,
        text=str(request.body.get("text") or ""),
        preserve_original=False,
        expected_version=int(request.body.get("expectedVersion") or current["version"]),
    )
