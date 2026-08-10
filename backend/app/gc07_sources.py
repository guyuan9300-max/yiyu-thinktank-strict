"""GC-07 format adapters layered on the established local material repository.

The mainline repository already owns import, object manifests, processing
attempts, retry, and local Wiki construction.  This module only fills format
gaps that the mainline currently reports as the generic
``local_document_preview_unsupported`` state.
"""

from __future__ import annotations

import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

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
