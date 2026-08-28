from __future__ import annotations

import json
from typing import Any

from ..runtime import LocalRuntimeError
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("gc13_growth", pin_workspace=True)
_CLOUD_ROOT = "/api/v2/gc13/growth"


def register_gc13_growth_ui_domain(
    routers: list[UiDomainRouter],
) -> None:
    """Append the isolated router to the shared registry during integration."""
    if any(current.domain == router.domain for current in routers):
        return
    routers.append(router)


@router.get(r"gc13/growth")
def read_growth(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(_CLOUD_ROOT)


@router.post(r"gc13/growth/evidence")
def confirm_evidence(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/evidence",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.post(r"gc13/growth/rules")
def publish_rule(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/rules",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.post(r"gc13/growth/rebuild")
def rebuild_growth(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    rebuilt = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/rebuild",
        payload={},
        idempotency_key=f"{request.idempotency_key}:models",
        refresh_business=False,
    )
    snapshot = compatibility.runtime.cloud_query(_CLOUD_ROOT)
    evidence = [
        item
        for item in snapshot.get("evidence") or []
        if isinstance(item, dict) and str(item.get("summary") or "").strip()
    ]
    companion = dict(snapshot.get("companion") or {})
    analysis_context = companion.get("analysisContext")
    analysis_context = analysis_context if isinstance(analysis_context, dict) else {}
    source_fingerprint = str(companion.get("sourceFingerprint") or "").strip()
    current_summary = companion.get("summary")
    force_summary = bool(request.body.get("forceCompanionSummary"))
    if not evidence:
        return rebuilt
    if isinstance(current_summary, dict) and not force_summary:
        return {**rebuilt, "companionSummary": current_summary}

    completion = compatibility.runtime.organization_ai_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "你是益语智库成长陪伴 Agent。请结合成员提交的复盘原文、任务详情、"
                    "关联计划与事件背景，以及此前成长总结，理解用户这段时间真正形成了什么"
                    "能力变化。不能按关键词分类，也不能把完成任务本身等同于成长。每个判断"
                    "都必须能从输入事实中得到支持；没有明显成长可以少输出或不输出。"
                    "返回纯 JSON，结构为："
                    '{"weeklySummary":"不超过180字、最多三句话的总结",'
                    '"growthHighlights":[{"abilityKey":"exec|collab|analyze|insight|risk|write",'
                    '"abilityLabel":"中文能力名","title":"简短进步标题",'
                    '"summary":"不超过80字的具体变化","trend":"up|steady|forming","level":1}],'
                    '"experienceEntries":[{"kind":"quote|distilled","text":"金句或经验",'
                    '"category":"能力或经验分类","sourceType":"weekly_review|task|meeting",'
                    '"sourceId":"输入中的真实来源ID","sourceTitle":"简短来源标题"}]}。'
                    "growthHighlights最多3条，level为1到5；experienceEntries最多3条。"
                    "quote必须逐字来自输入原文，概括内容必须标记distilled。不要输出盲点、"
                    "建议、证据清单或任何JSON之外的文字。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "memberName": getattr(
                            compatibility.runtime.capture_sandbox_context(),
                            "user_display_name",
                            None,
                        ),
                        "evidence": [
                            {
                                "evidenceId": item.get("evidenceId"),
                                "category": item.get("category"),
                                "sourceType": item.get("sourceType"),
                                "summary": item.get("summary"),
                                "createdAt": item.get("createdAt"),
                            }
                            for item in evidence[:20]
                        ],
                        "analysisContext": analysis_context,
                        "abilityModels": list(
                            (snapshot.get("readModel") or {}).get("abilities") or []
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        temperature=0.2,
        read_timeout_seconds=60.0,
    )
    raw = str(completion.get("content") or "").strip()
    if raw.startswith("```"):
        raw = raw.removeprefix("```json").removeprefix("```").strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    try:
        generated = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LocalRuntimeError(502, "gc13_growth_summary_invalid", "成长陪伴返回的周总结格式无效，可以重试") from exc
    if not isinstance(generated, dict):
        raise LocalRuntimeError(502, "gc13_growth_summary_invalid", "成长陪伴返回的周总结格式无效，可以重试")
    provider = dict(completion.get("provider") or {})
    raw_highlights = generated.get("growthHighlights") or []
    growth_highlights = [
        dict(item)
        for item in raw_highlights[:3]
        if isinstance(item, dict)
    ] if isinstance(raw_highlights, list) else []
    raw_experiences = generated.get("experienceEntries") or []
    context_text = json.dumps(analysis_context, ensure_ascii=False)
    experience_entries: list[dict[str, Any]] = []
    if isinstance(raw_experiences, list):
        for item in raw_experiences[:3]:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            text_value = str(normalized.get("text") or "").strip()
            if not text_value:
                continue
            if str(normalized.get("kind") or "") == "quote" and text_value not in context_text:
                normalized["kind"] = "distilled"
            experience_entries.append(normalized)
    saved = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/companion-summary",
        payload={
            "sourceFingerprint": source_fingerprint,
            "weeklySummary": generated.get("weeklySummary"),
            "patterns": [],
            "blindSpots": [],
            "suggestions": [],
            "growthHighlights": growth_highlights,
            "experienceEntries": experience_entries,
            "providerResourceId": provider.get("configId"),
            "modelName": provider.get("modelName"),
        },
        idempotency_key=f"{request.idempotency_key}:companion",
        refresh_business=False,
    )
    return {**rebuilt, "companionSummary": saved}


@router.post(r"gc13/growth/evidence/(?P<evidence_id>[^/]+)/(?P<action>revise|exclude)")
def update_evidence(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/evidence/{match.group('evidence_id')}/{match.group('action')}",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.get(r"gc13/growth/weekly-review-candidates")
def read_weekly_review_candidates(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> Any:
    return compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/weekly-review-candidates"
    )


@router.post(
    r"gc13/growth/weekly-review-candidates/(?P<candidate_id>[^/]+)/(?P<action>confirm|ignore)"
)
def decide_weekly_review_candidate(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> Any:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/weekly-review-candidates/{match.group('candidate_id')}/"
        f"{match.group('action')}",
        payload={},
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


def _compat_view(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> Any:
    view = request.path.removeprefix("growth/")
    return compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/{view}",
        query=dict(request.query),
    )


for _view in ("overview", "workbench", "badges", "ledger", "experience-wall"):
    router.get(rf"growth/{_view}")(_compat_view)


@router.get(r"handbook")
def handbook_compatibility_view(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    """Expose only real handbook-reuse evidence through the retained counter.

    The full Growth Center uses the GC-13 views directly.  This compatibility
    read prevents the legacy bootstrap counter from consulting a second
    handbook authority or reporting a frozen-route error.
    """
    ledger = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/ledger", query=dict(request.query)
    )
    entries = [
        {
            "id": str(item.get("id") or ""),
            "title": str(item.get("sourceTitle") or "经验复用"),
            "summary": str(item.get("reason") or ""),
            "tags": ["经验复用"],
            "sourceType": "handbook_reuse",
            "clientName": item.get("clientName"),
            "clientId": item.get("clientId"),
            "authorUserId": item.get("userId"),
            "authorUserName": item.get("userName"),
            "sourceObjectType": item.get("sourceType"),
            "sourceObjectId": item.get("sourceId"),
            "sourceTitle": item.get("sourceTitle"),
            "abilityKeys": [item.get("abilityKey")] if item.get("abilityKey") else [],
            "evidenceRefs": list(item.get("evidenceRefs") or []),
            "contextSummary": str(item.get("reason") or ""),
            "reuseCount": 1,
            "lastReusedAt": item.get("createdAt"),
            "linkedContexts": [],
            "createdAt": str(item.get("createdAt") or ""),
        }
        for item in ledger.get("entries") or []
        if str(item.get("sourceType") or item.get("xpType") or "")
        == "handbook_reuse"
    ]
    return {"entries": entries, "state": "ready" if entries else "ready_empty"}


@router.post(r"growth/experience-wall/(?P<quote_id>[^/]+)/like")
def like_experience_wall_quote(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> Any:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/experience-wall/{match.group('quote_id')}/like",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.post(r"growth/handbook/(?P<entry_id>[^/]+)/mark-reused")
def mark_handbook_reused(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> Any:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/handbook/{match.group('entry_id')}/mark-reused",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.post(r"growth/pending-captures/(?P<capture_id>[^/]+)/state")
def update_pending_capture(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> Any:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/pending-captures/{match.group('capture_id')}/state",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.post(
    r"growth/recommendations/(?P<recommendation_id>[^/]+)/(?P<action>accept|dismiss)"
)
def decide_recommendation(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> Any:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/recommendations/"
        f"{match.group('recommendation_id')}/{match.group('action')}",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
