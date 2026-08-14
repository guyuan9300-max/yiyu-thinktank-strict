"""GC-07 format adapters layered on the established local material repository.

The mainline repository already owns import, object manifests, processing
attempts, retry, and local Wiki construction.  This module only fills format
gaps that the mainline currently reports as the generic
``local_document_preview_unsupported`` state.
"""

from __future__ import annotations

import re
import threading
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from strict_common.ids import utc_now

from .local_asr.models import SENSE_VOICE_MODEL, model_ready
from .local_asr.subprocess_runner import run_local_asr_subprocess
from .project_materials_local import LocalProjectMaterialsRepository
from .runtime import LocalRuntimeError


PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument."
    "presentationml.presentation"
)
_PPTX_TEXT_TAG = (
    "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
)
_AUDIO_SUFFIXES = frozenset(
    {".aac", ".flac", ".m4a", ".mp3", ".mp4", ".ogg", ".wav", ".webm"}
)


class _VisibleHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._suppressed_depth = 0
        self.fragments: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag.casefold() in {"script", "style", "template", "noscript"}:
            self._suppressed_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if (
            tag.casefold() in {"script", "style", "template", "noscript"}
            and self._suppressed_depth
        ):
            self._suppressed_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._suppressed_depth:
            normalized = " ".join(data.split())
            if normalized:
                self.fragments.append(normalized)


def extract_visible_html_text(value: str) -> str:
    """Return visible webpage text, never markup-only pseudo content."""
    parser = _VisibleHtmlParser()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, TypeError) as exc:
        raise LocalRuntimeError(
            415,
            "local_web_document_invalid",
            "网页快照结构异常，无法读取正文",
        ) from exc
    content = "\n".join(parser.fragments).strip()
    if not content:
        raise LocalRuntimeError(
            415,
            "local_web_text_missing",
            "网页快照未检测到可读取正文；动态网页抓取尚未接通",
        )
    return content


def extract_pptx_text(path: Path) -> str:
    """Extract the real PPTX text layer with stdlib OOXML support."""
    try:
        with zipfile.ZipFile(path) as package:
            slide_names = sorted(
                (
                    name
                    for name in package.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                ),
                key=lambda name: int(re.search(r"(\d+)", name).group(1)),  # type: ignore[union-attr]
            )
            if not slide_names:
                raise LocalRuntimeError(
                    415,
                    "local_document_pptx_invalid",
                    "PPTX 未包含可识别的幻灯片结构",
                )
            slides: list[str] = []
            for name in slide_names:
                root = ElementTree.fromstring(package.read(name))
                fragments = [
                    " ".join(str(node.text or "").split())
                    for node in root.iter(_PPTX_TEXT_TAG)
                    if str(node.text or "").strip()
                ]
                if fragments:
                    slides.append("\n".join(fragments))
    except LocalRuntimeError:
        raise
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise LocalRuntimeError(
            415,
            "local_document_pptx_invalid",
            "PPTX 文件结构异常，无法读取正文",
        ) from exc
    content = "\n\n".join(slides).strip()
    if not content:
        raise LocalRuntimeError(
            415,
            "local_document_pptx_ocr_required",
            "PPTX 未检测到文字层；图片文字 OCR 尚未接通",
        )
    return content


class GC07LocalProjectMaterialsRepository(LocalProjectMaterialsRepository):
    """Drop-in local store for the GC-07 registry integration point."""

    @staticmethod
    def _processing_error_state(exc: LocalRuntimeError) -> tuple[str, bool]:
        if exc.code in {
            "local_audio_asr_not_connected",
            "local_document_pptx_invalid",
            "local_document_pptx_ocr_required",
            "local_web_capture_not_connected",
            "local_web_document_invalid",
            "local_web_text_missing",
        }:
            return "blocked", False
        return LocalProjectMaterialsRepository._processing_error_state(exc)

    def document_text(self, document_id: str) -> dict[str, Any]:
        try:
            result = super().document_text(document_id)
        except LocalRuntimeError as exc:
            if exc.code != "local_document_preview_unsupported":
                raise
            project_id, state, entry = self._document_entry(document_id)
            context = self._context()
            if str(state.get("_localSandboxId") or "") != context.sandbox_id:
                raise LocalRuntimeError(
                    409,
                    "local_storage_sandbox_changed",
                    "本机工作空间已切换，请重试",
                )
            path, row = self._source_path(
                entry,
                sandbox_id=context.sandbox_id,
            )
            media_type = str(entry.get("mediaType") or row["media_type"] or "")
            suffix = path.suffix.casefold()
            if suffix == ".pptx" or media_type == PPTX_MEDIA_TYPE:
                return {
                    "documentId": document_id,
                    "projectId": project_id,
                    "content": extract_pptx_text(path),
                    "kind": "pptx",
                    "title": entry.get("title") or entry.get("fileName") or path.name,
                }
            if suffix in _AUDIO_SUFFIXES or media_type.startswith("audio/"):
                raise LocalRuntimeError(
                    424,
                    "local_audio_asr_not_connected",
                    "音频原件已保留；当前未接通可用的本机 ASR",
                ) from exc
            if suffix == ".url":
                raise LocalRuntimeError(
                    424,
                    "local_web_capture_not_connected",
                    "网页地址已登记；动态网页抓取尚未接通",
                ) from exc
            raise

        if str(result.get("kind") or "") in {"html", "htm"}:
            content = extract_visible_html_text(str(result.get("content") or ""))
            return {**result, "content": content, "kind": "web_snapshot"}
        return result

    @staticmethod
    def _is_audio(entry: dict[str, Any], path: Path) -> bool:
        media_type = str(entry.get("mediaType") or "").casefold()
        return media_type.startswith("audio/") or path.suffix.casefold() in _AUDIO_SUFFIXES

    def processing_state(self, entry: dict[str, Any]) -> dict[str, Any]:
        try:
            _project_id, _state, current = self._document_entry(
                str(entry.get("documentId") or entry.get("cloudDocumentId") or entry.get("localSourceId") or "")
            )
        except LocalRuntimeError:
            current = entry
        try:
            path, _row = self._source_path(current, sandbox_id=self._context().sandbox_id)
        except LocalRuntimeError:
            return super().processing_state(entry)
        if not self._is_audio(dict(current), path):
            return super().processing_state(entry)
        source_ids = list(dict.fromkeys(
            str(value).strip()
            for value in (
                current.get("cloudDocumentId"),
                current.get("documentId"),
                current.get("localSourceId"),
            )
            if str(value or "").strip() and not str(value).startswith("local-pending:")
        ))
        attempt = None
        for source_id in source_ids:
            attempt = self._latest_processing_attempt(
                source_id,
                processor_kind="local_audio_transcription",
            )
            if attempt is not None:
                break
        if attempt is None:
            return {
                "parseStatus": "not_requested",
                "wikiStatus": "not_requested",
                "processingErrorCode": None,
                "processingMessage": "等待本机录音转写",
                "processingRetryable": True,
            }
        status = str(attempt.get("status") or "not_requested")
        return {
            "parseStatus": status,
            # The audio card remains the local original.  The separately
            # created transcript document owns Wiki construction, so the
            # original must never masquerade as a text document ready for
            # chunking.
            "wikiStatus": "not_requested",
            "processingAttemptId": str(attempt.get("id") or ""),
            "processingAttemptNo": int(attempt.get("attempt_no") or 0),
            "processingStage": "audio_transcription",
            "processingErrorCode": attempt.get("error_code") or None,
            "processingMessage": attempt.get("error_message_safe") or None,
            "processingRetryable": status in {"not_requested", "failed_retryable", "blocked"},
            "processedAt": attempt.get("finished_at") or attempt.get("started_at"),
            "transcriptDocumentId": current.get("transcriptDocumentId"),
            "originalAudioAvailable": path.is_file(),
        }

    def process_document(
        self,
        *,
        project_id: str,
        document_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        _found_project_id, state, entry = self._document_entry(document_id)
        path, _row = self._source_path(entry, sandbox_id=self._context().sandbox_id)
        if not self._is_audio(dict(entry), path):
            return super().process_document(
                project_id=project_id,
                document_id=document_id,
                force=force,
            )
        if str(state.get("projectId") or "") != project_id:
            raise LocalRuntimeError(409, "local_document_project_mismatch", "本机资料不属于当前项目")
        context = self._context()
        source_id = self._ensure_local_source_asset(project_id=project_id, entry=entry)
        current = self._latest_processing_attempt(
            source_id,
            processor_kind="local_audio_transcription",
        )
        if current is not None and not force and str(current.get("status") or "") in {
            "queued", "processing", "ready", "blocked"
        }:
            return {"documentId": document_id, **self.processing_state(entry)}
        model_root = self.runtime.database_path.parent / "models"
        attempt_no = int((current or {}).get("attempt_no") or 0) + 1
        initial_status = "queued" if model_ready(model_root, SENSE_VOICE_MODEL) else "blocked"
        initial_code = None if initial_status == "queued" else "local_audio_asr_not_ready"
        initial_message = (
            "等待本机转写"
            if initial_status == "queued"
            else "本机 ASR 模型未就绪；录音原件已保留，可在系统设置安装后重试"
        )
        attempt_id = self.create_local_processing_attempt(
            source_asset_id=source_id,
            processor_kind="local_audio_transcription",
            status=initial_status,
            error_code=initial_code,
            error_message=initial_message,
            attempt_no=attempt_no,
        )
        if initial_status == "blocked":
            return {"documentId": document_id, **self.processing_state(entry)}

        pinned_sandbox = self.runtime.capture_sandbox_context()

        def run_in_background() -> None:
            pinned_store = GC07LocalProjectMaterialsRepository(
                self.runtime,
                context_provider=lambda: context,
            )

            def update(status: str, message: str, *, code: str | None = None, finished: bool = False) -> None:
                pinned_store.update_local_processing_attempt(
                    attempt_id=attempt_id,
                    status=status,
                    error_code=code,
                    message=message,
                    finished=finished,
                )

            def report(percent: int, stage: str) -> None:
                update("processing", f"{max(0, min(100, int(percent)))}% · {stage}")

            try:
                with self.runtime.prebound_sandbox_context(pinned_sandbox):
                    output = run_local_asr_subprocess(
                        model_root=model_root,
                        audio_path=path,
                        language="auto",
                        progress_callback=report,
                    )
                    text = str(
                        output.get("dialogue_text")
                        or output.get("dialogueText")
                        or output.get("text")
                        or ""
                    ).strip()
                    if not text:
                        raise RuntimeError("AudioTranscriptionEmpty")
                    visible_source_name = str(
                        entry.get("fileName")
                        or entry.get("title")
                        or path.name
                    )
                    title = f"{Path(visible_source_name).stem}-录音转写"
                    material = pinned_store.import_text(
                        project_id=project_id,
                        title=title,
                        content=text,
                        idempotency_key=f"workbench-audio:{source_id}:{attempt_no}",
                    )
                    from .ui_domains.project_materials import register_and_process_local_materials
                    settled = register_and_process_local_materials(
                        runtime=self.runtime,
                        store=pinned_store,
                        project_id=project_id,
                        local_materials=[material],
                        relation_kind="",
                        relation_id="",
                        idempotency_key=f"workbench-audio:{source_id}:{attempt_no}",
                    )
                    _pid, latest_state, latest_entry = pinned_store._document_entry(document_id)
                    latest_entry["transcriptDocumentId"] = str((settled.get("documentIds") or [""])[0])
                    latest_entry["transcriptionStatus"] = "ready"
                    latest_entry["transcriptionProgress"] = 100
                    latest_entry["transcriptionStage"] = "转写完成"
                    latest_entry["updatedAt"] = utc_now()
                    pinned_store._write_project_state(project_id, latest_state)
                    update("ready", "100% · 转写完成", finished=True)
            except Exception:  # noqa: BLE001
                update(
                    "failed_retryable",
                    "本机录音转写暂时失败，可以重试；原件仍保留在本机",
                    code="local_audio_transcription_failed",
                    finished=True,
                )

        threading.Thread(
            target=run_in_background,
            name=f"workbench-audio-{source_id[-10:]}",
            daemon=True,
        ).start()
        return {"documentId": document_id, **self.processing_state(entry)}
