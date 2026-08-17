"""Conservative, project-aware cleanup for local ASR transcripts.

The raw audio remains the authority for what was said.  This module only
repairs high-confidence transcription spelling mistakes before the local
transcript version is written.  It never writes project keywords or facts and
falls back to the ASR text when the organization model is unavailable or the
model rewrites too much.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Callable, Mapping
from urllib.parse import quote


ProgressCallback = Callable[[int, str], None]


def _strings(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = " ".join(str(item or "").split()).strip()
        if not text or text.casefold() in {existing.casefold() for existing in result}:
            continue
        result.append(text[:80])
        if len(result) >= limit:
            break
    return result


def _project_context(runtime: Any, project_id: str) -> dict[str, Any]:
    profiles = runtime.cloud_query(
        "/api/v2/domain/task-planning/project-keyword-profiles"
    )
    profile = next(
        (
            dict(item)
            for item in profiles or []
            if isinstance(item, Mapping)
            and str(item.get("clientId") or "") == project_id
        ),
        {},
    )
    categories = (
        dict(profile.get("categories") or {})
        if isinstance(profile.get("categories"), Mapping)
        else {}
    )
    try:
        narrative = runtime.cloud_query(
            f"/api/v2/workbench/projects/{quote(project_id, safe='')}/narrative"
        )
    except Exception:  # noqa: BLE001 - keywords alone remain useful
        narrative = {}

    highlights: list[str] = []
    for item in narrative.get("dimensions") or []:
        if not isinstance(item, Mapping):
            continue
        label = " ".join(str(item.get("label") or item.get("dimension") or "").split())
        body = " ".join(str(item.get("narrative") or "").split())
        if not body:
            continue
        highlights.append(f"{label}：{body[:320]}" if label else body[:320])
        if len(highlights) >= 6:
            break

    return {
        "projectName": str(profile.get("clientName") or "").strip(),
        # These are the only terms strong enough to support direct spelling
        # repair.  Supplements are explicit member input.
        "canonicalTerms": _strings(categories.get("identityTerms"), limit=10)
        + _strings(profile.get("supplements"), limit=20),
        # The remaining layers explain the setting but are never a replacement
        # dictionary.  This prevents a nearby person or funder being forced into
        # an uncertain utterance merely because it exists in the dossier.
        "domainHints": _strings(categories.get("domainTerms"), limit=12)
        + _strings(categories.get("productsAndPrograms"), limit=12),
        "nameHints": _strings(categories.get("peopleAndOrganizations"), limit=16),
        "dossierHighlights": highlights,
    }


def _chunks(text: str, *, target_chars: int = 1800) -> list[str]:
    lines = text.splitlines()
    if not lines:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        added = len(line) + (1 if current else 0)
        if current and size + added > target_chars:
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(line)
        size += added
    if current:
        chunks.append("\n".join(current))
    return chunks


def _strip_fence(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^```(?:text|markdown)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _safe_revision(original: str, candidate: str) -> str:
    revised = _strip_fence(candidate)
    original_lines = original.splitlines()
    revised_lines = revised.splitlines()
    if len(original_lines) != len(revised_lines):
        return original
    if not original:
        return revised
    length_ratio = len(revised) / max(1, len(original))
    if not 0.86 <= length_ratio <= 1.14:
        return original
    if SequenceMatcher(None, original, revised, autojunk=False).ratio() < 0.78:
        return original
    speaker = re.compile(r"^\s*((?:说话人|Speaker)\s*[A-Za-z0-9一-龥]+\s*[：:])")
    for before, after in zip(original_lines, revised_lines, strict=True):
        before_prefix = speaker.match(before)
        after_prefix = speaker.match(after)
        if bool(before_prefix) != bool(after_prefix):
            return original
        if before_prefix and before_prefix.group(1) != after_prefix.group(1):
            return original
    return revised


def correct_project_transcript(
    runtime: Any,
    *,
    project_id: str,
    title: str,
    transcript: str,
    progress_callback: ProgressCallback | None = None,
) -> str:
    """Return a conservative local correction, or the original on any failure."""

    if not project_id or len(transcript.strip()) < 8:
        return transcript
    try:
        context = _project_context(runtime, project_id)
        if not context["canonicalTerms"] and not context["domainHints"]:
            return transcript
        parts = _chunks(transcript)
        corrected: list[str] = []
        for index, part in enumerate(parts):
            if progress_callback is not None:
                progress_callback(
                    82 + round(13 * index / max(1, len(parts))),
                    "结合项目上下文校正专名",
                )
            prompt = (
                f"录音标题：{title}\n"
                f"项目：{context['projectName']}\n"
                f"A级规范词（仅语音高度相似且句意吻合时可直接修正）："
                f"{'、'.join(context['canonicalTerms']) or '无'}\n"
                f"B级领域与项目提示（只帮助理解，不是替换词典）："
                f"{'、'.join(context['domainHints']) or '无'}\n"
                f"C级人物与机构提示（弱提示；原文没有充分语音和角色证据时不得替换）："
                f"{'、'.join(context['nameHints']) or '无'}\n"
                "客户档案摘录（仅用于判断语境，不得补写事实）：\n- "
                + "\n- ".join(context["dossierHighlights"] or ["无"])
                + "\n\n待校正片段：\n"
                + part
            )
            result = runtime.private_ai_completion(
                system_prompt=(
                    "你是保守的中文录音转写校对器。只修正由同一句语法语义、相邻句和项目上下文"
                    "共同强证实的错别字、同音专名和明显断句；不得润色、概括、补充事实或把不确定"
                    "词强行绑定到某个人物/机构。无法确定就原样保留。尤其不能仅因候选姓名存在，"
                    "就把含混语音替换为该姓名。逐行输出，行数、顺序和说话人前缀必须完全不变；"
                    "只输出校正后的正文。"
                ),
                prompt=prompt,
                creativity_mode="strict",
                read_timeout_seconds=100.0,
                max_output_tokens=4_096,
            )
            corrected.append(_safe_revision(part, str(result.get("content") or "")))
        return "\n".join(corrected)
    except Exception:  # noqa: BLE001 - semantic cleanup must never block ASR
        return transcript
