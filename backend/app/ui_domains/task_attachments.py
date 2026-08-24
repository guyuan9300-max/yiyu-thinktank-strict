from __future__ import annotations

import mimetypes
import threading
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from strict_common.ids import new_id

from ..project_materials_local import LocalProjectMaterialsRepository
from ..local_asr.models import SENSE_VOICE_MODEL, model_ready
from ..local_asr.subprocess_runner import run_local_asr_subprocess
from ..runtime import LocalRuntimeError
from ..transcript_semantic_correction import correct_project_transcript
from ..ui_idempotency import replayable_cloud_mutation, replayable_generated_value
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


def _task_project_id(task: dict[str, Any]) -> str:
    """Read the canonical project relation from either cloud receipt shape."""
    return str(task.get("clientId") or task.get("client_id") or "").strip()


def _task_project_name(task: dict[str, Any]) -> str:
    for key in ("clientName", "client_name", "projectName", "project_name"):
        value = str(task.get(key) or "").strip()
        if value:
            return value
    client = task.get("client")
    if isinstance(client, dict):
        return str(client.get("name") or client.get("title") or "").strip()
    return ""


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
    # The shared project importer deliberately rejects dotfiles such as
    # .DS_Store.  A real user attachment must not become a hidden file merely
    # because this bridge stages it before importing.
    temporary = root / f"task-attachment-{new_id()}{suffix}"
    temporary.write_bytes(raw)
    return temporary


def _uploaded_original_path(request: UiRequest) -> Path | None:
    """Return the real Finder source when the desktop bridge supplied it.

    Browser uploads do not expose a local path and continue through the
    bounded staging fallback.  Electron uploads do expose the path through
    ``webUtils.getPathForFile``; keeping that path is what lets “打开位置”
    reveal the user's original file instead of an internal managed copy.
    """

    raw_path = str(request.body.get("originalPath") or "").strip()
    uploaded = request.body.get("file")
    original_name = str(getattr(uploaded, "filename", "") or "").strip()
    if not raw_path or not original_name:
        return None
    try:
        source = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not source.is_file():
        return None
    normalized_source_name = unicodedata.normalize("NFC", source.name)
    normalized_upload_name = unicodedata.normalize("NFC", Path(original_name).name)
    if normalized_source_name != normalized_upload_name:
        return None
    upload_size = getattr(uploaded, "size", None)
    if isinstance(upload_size, int) and upload_size >= 0:
        try:
            if source.stat().st_size != upload_size:
                return None
        except OSError:
            return None
    return source


def _register(
    compatibility: Any,
    request: UiRequest,
    *,
    task_id: str,
    task: dict[str, Any],
    local: dict[str, Any],
) -> dict[str, Any]:
    client_id = _task_project_id(task)
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
    except LocalRuntimeError as exc:
        pending_id = f"local-pending:{local['localSourceId']}"
        pending = store.bind_task_attachment(
            project_id=client_id,
            document_id=pending_id,
            task_id=task_id,
        )
        # The task attachment is a device-local fact first; organization-cloud
        # metadata is an asynchronous consumer.  A consumer outage must not
        # make the editor pretend the attachment was lost.
        return {
            **pending,
            "cloudMetadataState": "failed_retryable",
            "syncMessage": "已保存到任务，项目工作台同步待重试",
            "syncError": {"code": exc.code, "message": exc.message},
        }


@router.post(r"tasks/(?P<task_id>[^/]+)/attachments")
def upload_task_attachment(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    task = _task(compatibility, task_id)
    client_id = _task_project_id(task)
    if not client_id:
        raise LocalRuntimeError(422, "task_attachment_project_required", "任务附件必须先关联项目")
    original_source = _uploaded_original_path(request)
    temporary = (
        None
        if original_source is not None
        else _save_uploaded_file(compatibility, request.body.get("file"))
    )
    import_source = original_source or temporary
    if import_source is None:  # pragma: no cover - guarded by helpers above
        raise LocalRuntimeError(422, "attachment_file_required", "请选择要上传的附件")
    try:
        imported = _store(compatibility).import_paths(
            project_id=client_id,
            mode="file",
            paths=[import_source],
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
        # A staging path is an implementation detail, never the user's
        # original file.  Do not persist a soon-to-be-deleted path as the
        # source shown by “打开位置”.
        if original_source is None:
            local["originalSourcePath"] = None
        _register(
            compatibility, request, task_id=task_id, task=task, local=local
        )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return _task_ui(compatibility, _task(compatibility, task_id))


@router.post(r"tasks/(?P<task_id>[^/]+)/attachments/from-markdown")
def upload_task_markdown(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    task = _task(compatibility, task_id)
    client_id = _task_project_id(task)
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
    client_id = _task_project_id(task)
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
    client_id = _task_project_id(task)
    local = _store(compatibility)

    def delete_local() -> dict[str, Any]:
        local_attachment = next(
            (
                item
                for item in local.task_attachments(task_id)
                if item["id"] == attachment_id
            ),
            None,
        )
        cloud_attachment = next(
            (
                dict(item)
                for item in task.get("attachments") or []
                if isinstance(item, dict)
                and str(item.get("id") or "") == attachment_id
            ),
            None,
        )
        attachment = dict(local_attachment or cloud_attachment or {
            "id": attachment_id,
            "version": 1,
        })
        if local_attachment is None:
            deleted = {"deleted": True, "alreadyMissing": True}
        else:
            deleted = local.delete_task_attachment_local(
                task_id=task_id,
                attachment_id=attachment_id,
            )
        return {
            "attachment": attachment,
            "cloudPresent": cloud_attachment is not None,
            "localDelete": dict(deleted),
        }

    local_receipt = replayable_generated_value(
        compatibility.runtime,
        idempotency_key=request.idempotency_key,
        command_type="task_attachment.local_delete",
        aggregate_type="source_asset",
        aggregate_id=attachment_id,
        input_payload={"taskId": task_id, "attachmentId": attachment_id},
        generate=delete_local,
    )
    attachment = dict(local_receipt.get("attachment") or {})
    cloud_deleted = False
    if (
        not attachment_id.startswith("local-pending:")
        and bool(local_receipt.get("cloudPresent"))
    ):
        cloud_path = (
            f"{_MATERIAL_ROOT}/projects/{quote(client_id, safe='')}/documents/"
            f"{quote(attachment_id, safe='')}"
        )
        replayable_cloud_mutation(
            compatibility.runtime,
            idempotency_key=request.idempotency_key,
            command_type="task_attachment.cloud_delete",
            aggregate_type="knowledge_document",
            aggregate_id=attachment_id,
            method="DELETE",
            path=cloud_path,
            request_payload={"taskId": task_id, "attachmentId": attachment_id},
            cloud_payload_factory=lambda: {
                "expectedVersion": int(attachment.get("version") or 1)
            },
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
    project_id = _task_project_id(task)
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
                    project_name=_task_project_name(task),
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
                    # The cloud document id may be replaced while metadata is
                    # settling; localSourceId is already stable and is now an
                    # accepted identity for the same source.
                    document_id=str(material["localSourceId"]),
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


@router.post(r"tasks/(?P<task_id>[^/]+)/attachments/(?P<attachment_id>[^/]+)/recorrect-transcript")
def recorrect_task_transcript(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
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
    task = _task(compatibility, task_id)
    project_id = _task_project_id(task)
    if not project_id:
        raise LocalRuntimeError(422, "task_attachment_project_required", "任务录音必须先关联项目")
    current = store.task_transcript(task_id=task_id, attachment_id=attachment_id)

    def report(percent: int, stage: str) -> None:
        store.set_task_transcription_state(
            task_id=task_id,
            attachment_id=attachment_id,
            status="processing",
            progress=percent,
            stage=stage,
        )

    with runtime.prebound_sandbox_context(pinned_sandbox):
        report(82, "重新检查项目专名与语境")
        corrected = correct_project_transcript(
            runtime,
            project_id=project_id,
            project_name=_task_project_name(task),
            title=str(attachment.get("title") or "任务录音"),
            transcript=str(current["currentText"]),
            progress_callback=report,
        ).strip()
    if corrected == str(current["currentText"]).strip():
        store.set_task_transcription_state(
            task_id=task_id,
            attachment_id=attachment_id,
            status="ready",
            progress=100,
            stage="重新校正完成，未发现确定改项",
        )
        return {**current, "changed": False}

    transcript = store.save_task_transcript(
        task_id=task_id,
        attachment_id=attachment_id,
        text=corrected,
        preserve_original=False,
        expected_version=int(current["version"]),
    )
    operation_key = (
        f"task-transcript-recorrect:{task_id}:{attachment_id}:"
        f"{transcript.get('version') or 1}"
    )
    material = store.import_text(
        project_id=project_id,
        title=f"{Path(str(attachment.get('title') or '任务录音')).stem}-录音转写",
        content=corrected,
        idempotency_key=operation_key,
    )
    register_and_process_local_materials(
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
        document_id=str(material["localSourceId"]),
        task_id=task_id,
    )
    return {**transcript, "changed": True}
