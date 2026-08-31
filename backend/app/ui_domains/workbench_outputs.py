from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from strict_common.ids import canonical_json, new_id, sha256_text

from ..gc06_planning_local import LocalGC06PlanningProjection
from ..intelligence_capture_local import (
    PublicCaptureError,
    capture_official_website,
    capture_public_web,
    merge_rendered_official_pages,
)
from ..platform_integrations_local import LocalPlatformOperationRepository
from ..project_materials_local import (
    LocalProjectMaterialsRepository,
    select_relevant_excerpt,
)
from ..runtime import LocalRuntimeError
from ..ui_idempotency import replayable_cloud_mutation
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("workbench_outputs", pin_workspace=True)

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_ui_id(prefix: str, *parts: Any) -> str:
    material = "|".join(str(part) for part in parts)
    return f"{prefix}_{sha256_text(material)[:32]}"


def _string(value: Any) -> str:
    return str(value or "").strip()


def _fallback_public_analysis_plan(
    prompt: str,
    *,
    selected_titles: Sequence[str] = (),
    mode: str = "balanced",
) -> dict[str, Any]:
    question = _string(prompt)
    focus = question[:90] + ("…" if len(question) > 90 else "")
    directions = [
        f"先明确“{focus}”中需要确认的对象、边界和期望结论",
        "再把可核实事实、基于事实的判断和仍需补证的部分分开",
    ]
    if any(token in question for token in ("为什么", "原因", "根因")):
        directions.append("区分直接原因、结构性原因和表面现象，并反查相互矛盾的解释")
    elif any(token in question for token in ("怎么", "如何", "方案", "建议")):
        directions.append("比较可行路径、使用条件和风险，再给出可执行次序")
    elif any(token in question for token in ("比较", "区别", "优劣", "是否")):
        directions.append("按同一组判断标准比较候选结论，避免只罗列各自特点")
    elif any(token in question for token in ("关系", "联系", "关联", "作用", "影响")):
        directions.append("分别界定两边各自承担的作用，再核对它们之间的支撑、反馈、因果与边界")
    else:
        directions.append("围绕问题核心归纳证据，并检查结论是否真正回答了用户所问")
    planned_sources = [f"用户本轮指定的《{title}》" for title in selected_titles[:4]]
    if not planned_sources:
        planned_sources = [
            "当前项目的本机可读资料与相关原文片段",
            "组织共享摘要、客户档案、官网事实及人工纠错/补充",
            "当前对话中与本题直接相关的上下文",
        ]
    cautions = [
        "不把历史回答或一般常识冒充当前项目事实",
        (
            "资料不足时明确缺口，同时把可行创意标成假设"
            if mode == "creative"
            else "资料不足时明确缺口，不用无证据内容补齐"
        ),
    ]
    narrative = (
        f"我理解你不是要我分别介绍相关概念，而是要围绕“{focus}”找出真正的连接方式与判断依据。"
        f"我会先{directions[0]}，再{directions[1]}，并继续{directions[2]}。\n\n"
        f"接下来优先核对{'、'.join(planned_sources[:3])}，看现有材料能否支持这些关系，"
        "尤其区分已经明确写出的事实、从事实推导出的判断，以及目前仍缺证据的部分。\n\n"
        f"组织答案时会特别注意{cautions[0]}；{cautions[1]}。"
    )
    return {
        "narrative": narrative,
        "intent": f"用户希望围绕“{focus}”得到一份直接、可核实且能用于下一步判断的回答。",
        "directions": directions[:4],
        "plannedSources": planned_sources[:5],
        "cautions": cautions,
    }


def _normalize_public_analysis_plan(
    raw_content: Any,
    *,
    prompt: str,
    selected_titles: Sequence[str],
    mode: str,
) -> dict[str, Any]:
    fallback = _fallback_public_analysis_plan(
        prompt,
        selected_titles=selected_titles,
        mode=mode,
    )
    text = _string(raw_content)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        match = re.search(r"\{[\s\S]*\}", text)
        try:
            parsed = json.loads(match.group(0)) if match else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = {}
    if not isinstance(parsed, Mapping):
        return fallback

    def _items(key: str, limit: int) -> list[str]:
        values = parsed.get(key)
        if not isinstance(values, list):
            return list(fallback[key])
        result = [_string(item)[:180] for item in values if _string(item)]
        return result[:limit] or list(fallback[key])

    return {
        "narrative": _string(parsed.get("narrative"))[:1_800] or fallback["narrative"],
        "intent": _string(parsed.get("intent"))[:260] or fallback["intent"],
        "directions": _items("directions", 4),
        "plannedSources": _items("plannedSources", 5),
        "cautions": _items("cautions", 3),
    }


def _sanitize_answer_export_title(value: Any) -> str:
    raw = _string(value)
    if not raw:
        return ""
    raw = re.sub(r"```(?:text|markdown)?", "", raw, flags=re.I)
    raw = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    raw = re.sub(r"^(?:标题|文档标题|建议标题)\s*[：:]\s*", "", raw)
    raw = raw.strip("#*_`《》“”\"' ")
    raw = re.sub(r"[\\/:*?\"<>|]+", "-", raw)
    raw = re.sub(r"\s+", "", raw).strip("-_.，。！？；：")
    raw = re.sub(r"\.(?:md|txt|docx?)$", "", raw, flags=re.I)
    if raw in {"工作台回答", "工作台回答导出", "回答导出"}:
        return ""
    return raw[:28]


def _fallback_answer_export_title(answers: Sequence[Mapping[str, Any]]) -> str:
    first = answers[0] if answers else {}
    question = _string(first.get("question"))
    question = re.sub(
        r"^(?:请帮我|请为我|麻烦你?|请你?|帮我)\s*",
        "",
        question,
    )
    question = re.sub(r"[？?。！!，,：:；;]+$", "", question)
    title = _sanitize_answer_export_title(question)
    if not title:
        answer = _string(first.get("answerMarkdown"))
        heading = re.search(r"^#{1,3}\s+(.+)$", answer, flags=re.M)
        title = _sanitize_answer_export_title(
            heading.group(1) if heading else answer[:80]
        )
    if not title:
        title = "项目问答摘要"
    if len(answers) > 1:
        title = _sanitize_answer_export_title(f"{title}等{len(answers)}项") or title
    return title


def _answer_export_title(
    compatibility: Any,
    answers: Sequence[Mapping[str, Any]],
) -> str:
    fallback = _fallback_answer_export_title(answers)
    samples = []
    remaining = 4_500
    for item in answers[:5]:
        question = _string(item.get("question"))
        answer = _string(item.get("answerMarkdown"))
        excerpt = answer[: min(1_200, remaining)]
        remaining -= len(excerpt)
        samples.append(f"问题：{question}\n回答：{excerpt}")
        if remaining <= 0:
            break
    try:
        completion = compatibility.runtime.private_ai_completion(
            system_prompt=(
                "你是中文文档标题编辑。根据问题与回答提炼一个准确、具体、简短的标题；"
                "优先写明主题，不写‘工作台回答’‘回答导出’等来源词。只输出标题本身，"
                "不要引号、书名号、Markdown、解释或文件扩展名，建议8至20个汉字。"
            ),
            prompt="\n\n".join(samples),
            creativity_mode="strict",
            read_timeout_seconds=8.0,
            max_output_tokens=48,
        )
        return _sanitize_answer_export_title(completion.get("content")) or fallback
    except Exception:
        # 导出不能因标题提炼服务短暂不可用而失败，问题文本仍能形成准确标题。
        return fallback


_OFFICIAL_RESEARCH_TARGETS: tuple[dict[str, Any], ...] = (
    {
        "id": "organization_profile",
        "label": "机构定位与简介",
        "keywords": ("关于", "简介", "使命", "愿景", "宗旨", "基金会", "机构"),
        "attributes": "机构定位、使命、愿景、成立背景",
    },
    {
        "id": "projects_and_services",
        "label": "项目与服务",
        "keywords": ("项目", "计划", "服务", "行动", "课程", "学院"),
        "attributes": "项目名称、服务对象、项目内容、实施方式",
    },
    {
        "id": "people_and_roles",
        "label": "关键人物与角色",
        "keywords": ("团队", "理事", "秘书长", "负责人", "发起人", "专家", "成员"),
        "attributes": (
            "人物姓名、当前职务、职责与项目角色、创办或从业经历、专业领域、"
            "代表性服务经验、公开荣誉资质、代表性成果或著作；每项分别成事实"
        ),
    },
    {
        "id": "history_and_milestones",
        "label": "发展历程与时间",
        "keywords": ("历程", "大事记", "成立", "启动", "发布", "年度", "年"),
        "attributes": "成立日期、项目启动时间、重要里程碑",
    },
    {
        "id": "reach_and_scale",
        "label": "覆盖范围与规模",
        "keywords": ("覆盖", "受益", "人数", "学校", "教师", "儿童", "地区", "省", "市"),
        "attributes": "覆盖地区、服务人数、学校或教师数量",
    },
    {
        "id": "methods_and_strategy",
        "label": "方法与战略",
        "keywords": ("方法", "模式", "战略", "理念", "心智", "素养", "培训"),
        "attributes": "工作方法、理论框架、战略重点",
    },
    {
        "id": "partners_and_governance",
        "label": "合作与治理",
        "keywords": ("合作", "伙伴", "支持", "治理", "理事会", "捐赠", "资助"),
        "attributes": "合作伙伴、治理结构、资助或支持关系",
    },
)


def _plan_official_research(pages: Sequence[Any]) -> list[dict[str, Any]]:
    """Derive a content-sized research plan from pages actually visible on the site."""

    eligible_pages = [
        page
        for page in pages
        if str(getattr(page, "page_role", "unknown"))
        not in {"resource", "product_demo", "transition"}
    ]
    planned: list[dict[str, Any]] = []
    for target in _OFFICIAL_RESEARCH_TARGETS:
        title_matches = [
            page
            for page in eligible_pages
            if any(keyword in str(page.title) for keyword in target["keywords"])
        ]
        body_matches = [
            page
            for page in eligible_pages
            if page not in title_matches
            and any(keyword in str(page.text) for keyword in target["keywords"])
        ]
        relevant = title_matches + body_matches
        if target["id"] == "organization_profile" and not relevant:
            relevant = list(eligible_pages[:2])
        if not relevant:
            continue
        planned.append(
            {
                "id": target["id"],
                "label": target["label"],
                "attributes": target["attributes"],
                "pages": relevant,
                "minimumPages": min(3, max(1, len(title_matches) or 1)),
                # 内容越多，目标要求越高；页面穷尽而无可靠事实也算已结算，
                # 但不会伪造事实凑数。
                "minimumFacts": min(4, max(1, (len(relevant) + 1) // 2)),
            }
        )
    # A content-rich site must create more work than a brochure site. Dedicated
    # same-origin pages become their own goals instead of letting a navigation-
    # heavy homepage satisfy every broad category.
    generic_titles = {"首页", "关于我们", "联系我们", "日慈公益基金会", "日慈基金会"}
    for page in eligible_pages[1:]:
        title = _string(page.title)
        if not title or title in generic_titles or len(_string(page.text)) < 160:
            continue
        short_title = title.split("|")[0].strip() or title
        planned.append(
            {
                "id": f"page_{sha256_text(str(page.url))[:12]}",
                "label": f"专题页面：{short_title[:50]}",
                "attributes": (
                    "仅提取以当前机构、项目、正式服务、真实成员或自身成效为主体的正式事实"
                ),
                "pages": [page],
                "minimumPages": 1,
                "minimumFacts": 2,
            }
        )
        if len(planned) >= 19:
            break
    return planned


def _parse_official_fact_response(
    raw_content: str,
    *,
    pages: Sequence[Any],
) -> list[dict[str, Any]]:
    page_by_url = {str(page.url): page for page in pages}
    raw = str(raw_content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        raw = raw[start : end + 1] if start >= 0 and end > start else "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LocalRuntimeError(502, "official_fact_extraction_invalid", "官网事实提炼结果无效，可以重试") from exc
    facts = parsed.get("facts") if isinstance(parsed, Mapping) else None
    if not isinstance(facts, list):
        raise LocalRuntimeError(502, "official_fact_extraction_invalid", "官网事实提炼结果无效，可以重试")
    allowed_categories = {"person", "date", "location", "count", "amount", "text"}
    allowed_subjects = {"client", "project", "service", "person", "team", "governance"}
    allowed_fact_kinds = {
        "organization_profile",
        "mission_vision",
        "service_offering",
        "project_definition",
        "methodology",
        "governance",
        "partnership",
        "person_profile",
        "milestone",
        "impact_metric",
        "business_term",
    }
    excluded_date_terms = ("版权", "更新", "发布日期", "发布时间", "抓取", "网页")
    excluded_metric_terms = (
        "文章", "报告", "图书", "文件", "页面", "附件", "任务", "资源库", "登录", "演示"
    )
    excluded_resource_fact_terms = (
        "文章标题", "报告标题", "报告名称", "图书名称", "资源数量", "内容数量", "发布日期"
    )
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in facts[:20]:
        if not isinstance(item, Mapping):
            continue
        source_url = _string(item.get("sourceUrl"))
        page = page_by_url.get(source_url)
        term = _string(item.get("term"))[:120]
        attribute = _string(item.get("attributeName"))[:120]
        value_text = _string(item.get("valueText"))[:1_000]
        evidence = _string(item.get("evidence"))[:1_500]
        if page is None or not term or not attribute or not value_text or not evidence:
            continue
        if "".join(evidence.split()) not in "".join(str(page.text).split()):
            continue
        category = _string(item.get("valueCategory"))
        if category not in allowed_categories:
            category = "text"
        subject_kind = _string(item.get("subjectKind"))
        fact_kind = _string(item.get("factKind"))
        relevance = item.get("businessRelevance") is True
        if subject_kind not in allowed_subjects or fact_kind not in allowed_fact_kinds or not relevance:
            continue
        page_role = _string(getattr(page, "page_role", "unknown")) or "unknown"
        if page_role in {"resource", "product_demo", "transition"}:
            continue
        semantic_text = f"{attribute}{value_text}{evidence}"
        if category == "date" and any(term in semantic_text for term in excluded_date_terms):
            continue
        if category in {"count", "amount"} and any(
            term in semantic_text for term in excluded_metric_terms
        ):
            continue
        if any(term in semantic_text for term in excluded_resource_fact_terms):
            continue
        try:
            confidence = min(0.95, max(0.5, float(item.get("confidence") or 0.75)))
        except (TypeError, ValueError):
            confidence = 0.75
        identity = (term, attribute, value_text)
        if identity in seen:
            continue
        seen.add(identity)
        results.append(
            {
                "term": term,
                "attributeName": attribute,
                "valueCategory": category,
                "valueText": value_text,
                "evidence": evidence,
                "sourceUrl": source_url,
                "sourcePublicUrl": _string(getattr(page, "canonical_public_url", "")),
                "sourceTitle": str(page.title)[:300],
                "pageRole": page_role,
                "captureKind": _string(getattr(page, "capture_kind", "static")) or "static",
                "subjectKind": subject_kind,
                "factKind": fact_kind,
                "scope": _string(item.get("scope"))[:200],
                "valueUnit": _string(item.get("valueUnit"))[:80],
                "asOfDate": _string(item.get("asOfDate"))[:80],
                "confidence": confidence,
            }
        )
    return results


def _official_fact_candidates(
    compatibility: Any,
    *,
    project_name: str,
    pages: Sequence[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plan = _plan_official_research(pages)
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    target_receipts: list[dict[str, Any]] = []
    retryable_failures = 0
    for target in plan:
        target_results: list[dict[str, Any]] = []
        target_had_retryable_failure = False
        page_groups = [target["pages"][index : index + 2] for index in range(0, len(target["pages"]), 2)]
        attempted_pages = 0
        for group in page_groups:
            if (
                len(target_results) >= int(target["minimumFacts"])
                and attempted_pages >= int(target.get("minimumPages") or 1)
            ):
                break
            source_text = "\n\n".join(
                f"[PAGE {index + 1}]\n标题：{page.title}\n网址：{page.url}"
                f"\n页面角色：{getattr(page, 'page_role', 'unknown')}"
                f"\n读取方式：{getattr(page, 'capture_kind', 'static')}"
                f"\n正文：{page.text[:6_000]}"
                for index, page in enumerate(group)
            )
            batch: list[dict[str, Any]] | None = None
            for attempt in range(2):
                try:
                    completion = compatibility.runtime.organization_ai_completion(
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "你是益语智库情报研究 Agent。当前研究目标是："
                                    f"{target['label']}（{target['attributes']}）。"
                                    "你的任务是提取关于当前客户或项目的正式事实，不是总结网页内容。"
                                    "先判断句子主体和页面用途。只有主体为当前机构、项目、正式服务、"
                                    "真实成员、团队治理、历史里程碑或自身规模成效时才能输出。"
                                    "文章、报告、图书及其标题、发布日期、资源数量，网页更新日期、"
                                    "版权年份、界面演示数据、登录用户、任务计数和第三方报告数据一律排除，"
                                    "这些资源只作为可搜索知识。人物应分别提取职务、职责、经历、专业领域、"
                                    "代表性服务、荣誉与成果；数量必须说明主体、指标、单位、范围和统计时间。"
                                    "同一语义只输出一次，不得把60+、60余家、服务规模60+拆成重复事实。"
                                    "只从提供的官网原文逐字有据地抽取，不得使用常识、推断或补全。"
                                    "每条 evidence 必须是对应页面正文中的连续原句；sourceUrl 必须使用提供的网址。"
                                    "返回纯 JSON："
                                    '{"facts":[{"term":"实体","attributeName":"属性",'
                                    '"valueCategory":"person|date|location|count|amount|text",'
                                    '"subjectKind":"client|project|service|person|team|governance",'
                                    '"factKind":"organization_profile|mission_vision|service_offering|project_definition|methodology|governance|partnership|person_profile|milestone|impact_metric|business_term",'
                                    '"businessRelevance":true,"scope":"适用范围","valueUnit":"单位",'
                                    '"asOfDate":"截至日期或空字符串",'
                                    '"valueText":"属性值","evidence":"官网原句",'
                                    '"sourceUrl":"页面网址","confidence":0.0}]}。'
                                    "最多12条；没有可靠正式事实就返回空数组。"
                                ),
                            },
                            {"role": "user", "content": f"项目：{project_name}\n\n{source_text}"},
                        ],
                        temperature=0.0,
                        read_timeout_seconds=45.0,
                    )
                    batch = _parse_official_fact_response(
                        str(completion.get("content") or ""),
                        pages=group,
                    )
                    break
                except LocalRuntimeError as exc:
                    if exc.status_code < 500 or attempt == 1:
                        retryable_failures += 1
                        target_had_retryable_failure = exc.status_code >= 500
                        batch = None
                        break
            attempted_pages += len(group)
            if batch is None:
                continue
            for item in batch:
                identity = (item["term"], item["attributeName"], item["valueText"])
                if identity in seen:
                    continue
                seen.add(identity)
                target_results.append(item)
                results.append(item)
        target_receipts.append(
            {
                "targetId": target["id"],
                "label": target["label"],
                "pageCount": len(target["pages"]),
                "attemptedPageCount": attempted_pages,
                "minimumFacts": target["minimumFacts"],
                "factCount": len(target_results),
                "status": (
                    "completed"
                    if len(target_results) >= int(target["minimumFacts"])
                    else "failed_retryable"
                    if target_had_retryable_failure
                    else "settled_no_reliable_fact"
                    if attempted_pages >= len(target["pages"])
                    else "failed_retryable"
                ),
            }
        )
    return results, {
        "state": (
            "completed"
            if all(item["status"] in {"completed", "settled_no_reliable_fact"} for item in target_receipts)
            else "partial"
        ),
        "pageCount": len(pages),
        "targetCount": len(target_receipts),
        "completedTargetCount": sum(
            item["status"] in {"completed", "settled_no_reliable_fact"}
            for item in target_receipts
        ),
        "factCount": len(results),
        "retryableFailureCount": retryable_failures,
        "targets": target_receipts,
    }


def _explicit_project_memory_statement(prompt: str) -> str | None:
    """Extract only an explicit, non-negated user instruction to remember a fact."""

    text = " ".join(str(prompt or "").strip().split())
    if not text:
        return None
    patterns = (
        re.compile(
            r"(?:^|[。！？!?；;，,]\s*)"
            r"(?:(?:请|麻烦|务必|一定要|必须)?(?:你)?(?:帮我)?|(?:我)?希望你|你(?:要|得|需要))"
            r"(?:明确)?记住(?:一下|这一点|这点)?[：:，,\s]*(?P<fact>.+)"
        ),
        re.compile(
            r"(?:^|[。！？!?；;，,]\s*)(?:请|麻烦|帮我)?"
            r"把(?P<fact>.+?)(?:记住|记下来)(?:[。！!]|$)"
        ),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        prefix = text[max(0, match.start() - 8) : match.start()]
        if re.search(r"(?:不要|不用|别|不必|无需|不能|不应)$", prefix):
            continue
        fact = str(match.group("fact") or "").strip(" ：:，,。.!！?？")
        if fact in {"", "这件事", "这点", "这一点", "这个", "这些", "上述内容", "它"}:
            continue
        if len(fact) < 4:
            continue
        return fact[:20_000]
    return None


def _search_terms(value: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(
        r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,32}",
        value,
    ):
        lowered = token.lower()
        terms.append(lowered)
        if re.fullmatch(r"[\u4e00-\u9fff]{4,}", token):
            terms.extend(
                token[index : index + 2]
                for index in range(len(token) - 1)
            )
    return list(dict.fromkeys(term for term in terms if term))


def _cloud_query(
    compatibility: Any,
    path: str,
    *,
    query: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(path, query=query)


def _cloud_command(
    compatibility: Any,
    request: UiRequest,
    method: str,
    path: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        method,
        path,
        payload=payload,
        idempotency_key=request.idempotency_key,
    )


def _replayable_workbench_mutation(
    compatibility: Any,
    request: UiRequest,
    method: str,
    path: str,
    *,
    aggregate_type: str,
    aggregate_id: str,
    payload_factory: Any,
) -> dict[str, Any]:
    return replayable_cloud_mutation(
        compatibility.runtime,
        idempotency_key=request.idempotency_key,
        command_type="workbench.ui.cas_mutation",
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        method=method,
        path=path,
        request_payload=request.body,
        cloud_payload_factory=payload_factory,
    )


def _require_project_read(compatibility: Any, project_id: str) -> dict[str, Any]:
    return compatibility.runtime.require_project_capability(project_id, "read")


def _selected_style_or_agent_skill(
    compatibility: Any,
    selected_id: str,
) -> tuple[str, dict[str, Any] | None]:
    """Resolve the new declarative Skill first, then the legacy writing style."""
    # v8 Skill ids are deliberately namespaced with ``skill_``.  Existing
    # writing-style ids keep their original lookup path during phase 16.
    if selected_id.startswith("skill_"):
        try:
            item = _cloud_query(compatibility, f"/api/v2/agent-skills/{selected_id}")
        except LocalRuntimeError as exc:
            if exc.status_code != 404:
                raise
        else:
            compatibility.runtime.project_agent_skill(item)
            if not bool(item.get("enabled")) or "project_workspace" not in (
                item.get("agentKinds") or []
            ):
                raise LocalRuntimeError(
                    409,
                    "agent_skill_not_applicable",
                    "所选 Skill 未启用或不适用于项目工作台",
                )
            instructions = [
                _string(value) for value in item.get("instructions") or [] if _string(value)
            ]
            rendered = "\n".join(f"{index + 1}. {value}" for index, value in enumerate(instructions))
            template = _string(item.get("outputTemplate"))
            if template:
                rendered += (
                    "\n风格代表性样本（只模仿表达方式，不照抄其中事实）：\n"
                    + template
                )
            return "", {**item, "renderedInstruction": rendered}
    skills = _cloud_query(
        compatibility,
        "/api/v2/workbench/libraries/writing_skill",
    )
    skill = next(
        (item for item in skills if _string(item.get("id")) == selected_id),
        None,
    )
    if skill is None:
        raise LocalRuntimeError(
            404,
            "selected_skill_missing",
            "选择的 Skill 或写作风格已不存在，请重新选择",
        )
    return _string(skill.get("distilledMd")), None


def _project_summary(project: Mapping[str, Any], workspace: Mapping[str, Any]) -> dict[str, Any]:
    project_id = _string(project.get("projectId"))
    documents = workspace.get("documents") or []
    tasks = workspace.get("tasks") or []
    return {
        "id": project_id,
        "name": project.get("name") or "未命名项目",
        "alias": project.get("alias") or "",
        "domain": project.get("domain") or "项目",
        "type": "project",
        "intro": project.get("summary") or "",
        "stage": project.get("lifecycleState") or "active",
        "color": project.get("color") or "#5B7BFE",
        "folderCount": 0,
        "documentCount": len(documents),
        "taskCount": len(tasks),
        "lastActivityAt": project.get("updatedAt"),
        "relatedUserIds": [],
        "isDataCenterIncluded": True,
        "isDefaultInternalProject": bool(project.get("isDefaultInternalProject")),
        "isFrozen": project.get("lifecycleState") != "active",
        "syncStatus": "synced",
        "cloudId": project_id,
    }


def _chat_messages(
    answer: Mapping[str, Any],
    runtime: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    answer_id = _string(answer.get("answerId"))
    source_manifest = answer.get("sourceManifest") or {}
    thread_id = _string(source_manifest.get("threadId")) or answer_id
    created_at = answer.get("createdAt") or _now()
    selected_documents = [
        dict(item)
        for item in source_manifest.get("selectedDocuments") or []
        if isinstance(item, Mapping)
    ]
    retrieved_documents = [
        dict(item)
        for item in source_manifest.get("retrievedDocuments") or []
        if isinstance(item, Mapping)
    ]
    organization_sources = [
        dict(item)
        for item in source_manifest.get("organizationSources") or []
        if isinstance(item, Mapping)
    ]
    local_memory_sources = [
        dict(item)
        for item in source_manifest.get("localMemorySources") or []
        if isinstance(item, Mapping)
    ]
    selected_content_count = int(
        source_manifest.get("selectedDocumentContentCount") or 0
    )
    knowledge_summary_count = int(
        source_manifest.get("projectKnowledgeSummaryCount") or 0
    )
    local_memory_count = int(source_manifest.get("localMemoryCount") or 0)
    material_access_mode = _string(source_manifest.get("materialAccessMode")) or (
        "lightweight_direct"
        if selected_content_count > 0
        else "knowledge_summary"
        if knowledge_summary_count > 0
        else "none"
    )
    evidence = [
        {
            "id": item.get("documentId"),
            "title": item.get("title") or item.get("documentId"),
            "sourceType": "local_document",
            "sourceId": item.get("documentId"),
            "contentHash": item.get("contentHash"),
        }
        for item in selected_documents
        if item.get("documentId")
    ]
    evidence.extend(
        {
            "id": item.get("documentId"),
            "title": item.get("title") or item.get("documentId"),
            "sourceType": "local_document",
            "sourceId": item.get("documentId"),
            "contentHash": item.get("contentHash"),
            "retrievalMode": item.get("retrievalMode"),
            "score": item.get("score"),
            "chunkIds": item.get("chunkIds") or [],
            "factIds": item.get("factIds") or [],
            "evidenceIds": item.get("evidenceIds") or [],
        }
        for item in retrieved_documents
        if item.get("documentId")
        and not any(
            selected.get("documentId") == item.get("documentId")
            for selected in selected_documents
        )
    )
    evidence.extend(
        {
            "id": item.get("sourceId"),
            "title": item.get("title") or item.get("sourceId"),
            "sourceType": item.get("sourceKind") or "organization_knowledge",
            "sourceId": item.get("sourceId"),
            "contentHash": item.get("contentHash"),
        }
        for item in organization_sources
        if item.get("sourceId")
    )
    evidence.extend(
        {
            "id": item.get("sourceId"),
            "title": item.get("title") or item.get("sourceId"),
            "sourceType": item.get("sourceKind") or "explicit_memory",
            "sourceId": item.get("sourceId"),
            "contentHash": item.get("contentHash"),
        }
        for item in local_memory_sources
        if item.get("sourceId")
    )
    user = {
        "id": f"{answer_id}:question",
        "threadId": thread_id,
        "role": "user",
        "content": answer.get("question") or "",
        "createdAt": created_at,
        "status": "success",
        "evidence": [],
        "activeSkillIds": list(source_manifest.get("activeSkillIds") or []),
    }
    local_chat_images = [
        dict(item)
        for item in source_manifest.get("localChatImageInputs") or []
        if isinstance(item, Mapping)
    ]
    if (
        local_chat_images
        and runtime is not None
        and hasattr(runtime, "resolve_workbench_chat_images")
    ):
        try:
            user["imageAttachments"] = runtime.resolve_workbench_chat_images(
                local_chat_images
            )
        except LocalRuntimeError:
            # Missing local binary must not hide the verified text history.
            user["imageAttachments"] = []
    assistant = {
        "id": answer_id,
        "threadId": thread_id,
        "role": "assistant",
        "content": answer.get("answerMarkdown") or "",
        "createdAt": created_at,
        "status": "success",
        "modelRoute": answer.get("modelName"),
        "llmInvoked": True,
        "providerUsed": "organization_direct",
        "answerMode": "general_answer",
        "evidenceStatus": (
            "sufficient"
            if selected_content_count > 0 or local_memory_count > 0
            else "partial"
            if knowledge_summary_count > 0
            else "none"
        ),
        "evidence": evidence,
        "creativityMode": (answer.get("sourceManifest") or {}).get("mode")
        or "balanced",
        "deepThinkingRequested": bool(
            source_manifest.get("deepThinkingRequested")
        ),
        "timing": dict(source_manifest.get("timing") or {}),
        "activeSkillId": source_manifest.get("activeSkillId"),
        "activeSkillIds": list(source_manifest.get("activeSkillIds") or []),
        "retrievalSummary": {
            "workspaceWorkflow": "project_chat",
            "materialAccessMode": material_access_mode,
            "memoryState": source_manifest.get("memoryState") or "ready",
            "memoryMessage": source_manifest.get("memoryMessage"),
            "linkedEvidenceCount": len(evidence),
            "selectedDocumentContentCount": selected_content_count,
            "userSelectedDocumentCount": int(
                source_manifest.get("userSelectedDocumentCount") or 0
            ),
            "localRetrievedDocumentCount": int(
                source_manifest.get("localRetrievedDocumentCount") or 0
            ),
            "localRetrievalState": source_manifest.get("localRetrievalState") or "ready",
            "localRetrievalMessage": source_manifest.get("localRetrievalMessage"),
            "projectKnowledgeSummaryCount": knowledge_summary_count,
            "localMemoryCount": local_memory_count,
            "sourceCount": int(source_manifest.get("sourceCount") or len(evidence)),
            "sourceSetId": source_manifest.get("sourceSetId"),
            "aiContextManifestId": source_manifest.get("aiContextManifestId"),
            "botId": source_manifest.get("botId"),
            "agentKind": source_manifest.get("agentKind"),
            "providerResourceId": source_manifest.get("providerResourceId"),
            "modelName": source_manifest.get("modelName") or answer.get("modelName"),
            "multipassUsed": bool(source_manifest.get("multipassUsed")),
            "retrievalPassCount": int(source_manifest.get("retrievalPassCount") or 1),
            "analysisTrace": [
                _string(item)
                for item in source_manifest.get("analysisTrace") or []
                if _string(item)
            ],
            "publicAnalysisPlan": (
                dict(source_manifest.get("publicAnalysisPlan") or {})
                if isinstance(source_manifest.get("publicAnalysisPlan"), Mapping)
                else None
            ),
            "providerReasoningContent": _string(
                source_manifest.get("providerReasoningContent")
            ),
            "providerFinishReason": _string(
                source_manifest.get("providerFinishReason")
            ),
            "answerVersion": int(answer.get("version") or 1),
            "selectedHits": evidence,
            "primarySources": [
                str(item.get("title") or item.get("documentId"))
                for item in selected_documents + retrieved_documents
                if item.get("documentId")
            ],
            "boundaryNotes": (
                []
                if selected_content_count > 0
                else ["本轮没有读取用户选中的本机资料正文"]
            ),
            "sourceManifest": {
                "documentContentIncluded": bool(
                    source_manifest.get("documentContentIncluded")
                ),
                "selectedDocuments": selected_documents,
                "retrievedDocuments": retrieved_documents,
                "organizationSources": organization_sources,
                "localMemorySources": local_memory_sources,
                "memoryState": source_manifest.get("memoryState") or "ready",
                "memoryMessage": source_manifest.get("memoryMessage"),
                "deepThinkingRequested": bool(
                    source_manifest.get("deepThinkingRequested")
                ),
                "publicAnalysisPlan": (
                    dict(source_manifest.get("publicAnalysisPlan") or {})
                    if isinstance(source_manifest.get("publicAnalysisPlan"), Mapping)
                    else None
                ),
                "providerReasoningContent": _string(
                    source_manifest.get("providerReasoningContent")
                ),
            },
        },
    }
    return user, assistant


def _analysis_run(
    project_id: str,
    answer: Mapping[str, Any],
    *,
    state: str = "completed",
) -> dict[str, Any]:
    user, assistant = _chat_messages(answer)
    completed = state == "completed"
    failed = state == "failed"
    return {
        "id": answer.get("answerId"),
        "clientId": project_id,
        "threadId": (
            (answer.get("sourceManifest") or {}).get("threadId")
            or answer.get("answerId")
        ),
        "userMessageId": user["id"],
        "assistantMessageId": assistant["id"],
        "question": answer.get("question") or "",
        "status": "failed" if failed else "completed" if completed else "running",
        "phase": "failed" if failed else "completed" if completed else "retrieving",
        "progress": 100 if completed else 0 if failed else 50,
        "progressFloor": 100 if completed else 0,
        "progressCeiling": 100,
        "stageLabel": None,
        "elapsedMs": int((assistant.get("timing") or {}).get("totalMs") or 0),
        "evidenceSummary": {
            "summaryText": "",
            "masterHitCount": 0,
            "surrogateHitCount": 0,
            "rawChunkHitCount": 0,
            "drillthroughUsed": False,
            "coveredCategories": [],
            "missingCategories": [],
            "evidenceList": [],
        },
        "longAnswerStatus": "ready" if completed else "failed" if failed else "pending",
        "summaryStatus": "ready" if completed else "failed" if failed else "pending",
        "longAnswer": assistant["content"] if completed else None,
        "answerMode": "general_answer",
        "llmInvoked": completed,
        "providerUsed": "organization_direct" if completed else None,
        "failureReason": None,
        "timing": dict(assistant.get("timing") or {}),
        "assistantMessage": assistant if completed else None,
        "createdAt": answer.get("createdAt") or _now(),
        "updatedAt": answer.get("updatedAt") or answer.get("createdAt") or _now(),
    }


def _knowledge_status(status: Mapping[str, Any]) -> dict[str, Any]:
    counts = status.get("counts") or {}
    documents = [
        {
            **dict(item),
            "parseStatus": (
                item.get("parseStatus")
                or item.get("parseState")
                or "not_requested"
            ),
        }
        for item in status.get("documents") or []
    ]
    attempts = status.get("processingAttempts") or []
    completed_attempts = [
        item for item in attempts if item.get("state") in {"completed", "partial"}
    ]
    failed_attempts = [item for item in attempts if item.get("state") == "failed"]
    pending_attempts = [
        item for item in attempts if item.get("state") in {"queued", "processing"}
    ]
    return {
        "totalDocuments": int(counts.get("total") or 0),
        "totalChunks": int(counts.get("totalChunks") or 0),
        "ocrReadyRate": (
            round(
                (
                    int(counts.get("ready") or 0)
                    + 0.7 * int(counts.get("partial") or 0)
                )
                * 100
                / max(1, int(counts.get("total") or 0)),
                1,
            )
        ),
        "vectorizedDocuments": 0,
        "dedupedDocuments": 0,
        "reviewPendingDocuments": int(counts.get("failed") or 0),
        "surrogateCount": 0,
        "memoryDocCount": 0,
        "masterIndexCount": int(counts.get("ready") or 0),
        "reclassifiedDocumentCount": 0,
        "qdrantReady": False,
        "lastUpdatedAt": max(
            (
                _string(item.get("createdAt"))
                for item in attempts
                if item.get("createdAt")
            ),
            default=None,
        ),
        "pendingJobs": len(pending_attempts),
        "runningJobs": sum(1 for item in attempts if item.get("state") == "processing"),
        "lastJobStatus": (
            "failed"
            if failed_attempts
            else "running"
            if pending_attempts
            else "completed"
            if completed_attempts
            else "idle"
        ),
        "lastJobError": (
            failed_attempts[0].get("errorMessage") if failed_attempts else None
        ),
        "lastSuccessfulRunAt": (
            completed_attempts[0].get("finishedAt") if completed_attempts else None
        ),
        "embeddingMode": "strict_document_versions",
        "embeddingModel": None,
        "embeddingError": "严格新版未建立向量索引权威对象",
        "embeddingProvider": None,
        "embeddingDimension": None,
        "embeddingSignature": None,
        "activeVectorCollection": None,
        "vectorIndexStatus": "stale",
        "routerEnabled": False,
        "routerModel": None,
        "rerankEnabled": False,
    }


def _knowledge_jobs(status: Mapping[str, Any]) -> list[dict[str, Any]]:
    project_id = _string(status.get("projectId"))
    return [
        {
            "id": item.get("processingAttemptId"),
            "clientId": project_id,
            "jobType": item.get("processingKind"),
            "status": (
                "running" if item.get("state") == "processing" else item.get("state")
            ),
            "totalItems": 1,
            "processedItems": 1
            if item.get("state") in {"completed", "partial", "failed", "cancelled"}
            else 0,
            "lastError": item.get("errorMessage") or None,
            "currentItemLabel": item.get("documentId"),
            "lastEventMessage": item.get("errorMessage") or item.get("state"),
            "recentEvents": [],
            "queuedItemLabels": [],
            "createdAt": item.get("createdAt"),
            "startedAt": item.get("startedAt"),
            "finishedAt": item.get("finishedAt"),
            "updatedAt": item.get("finishedAt")
            or item.get("startedAt")
            or item.get("createdAt"),
        }
        for item in status.get("processingAttempts") or []
    ]


def _workspace(compatibility: Any, project_id: str) -> dict[str, Any]:
    rich_domain_endpoints = True
    try:
        data = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/workspace",
        )
        status = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/knowledge-status",
        )
        dna = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/dna",
        )
        project_texts = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/texts",
        )
        project_structure = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/structure",
        )
        insights = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/insights",
        )
        goals = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/goals",
        )
    except AttributeError as exc:
        if "request_v2" not in str(exc):
            raise
        rich_domain_endpoints = False
        snapshot = compatibility.runtime.business_snapshot(refresh=False)
        project = next(
            (
                item
                for item in snapshot.get("projects") or []
                if _string(item.get("projectId")) == project_id
            ),
            None,
        )
        if project is None:
            raise LocalRuntimeError(404, "project_missing", "当前工作空间没有该项目")
        project_documents = [
            item
            for item in snapshot.get("documents") or []
            if _string(item.get("projectId")) == project_id
        ]
        data = {
            "project": project,
            "documents": project_documents,
            "answers": [
                item
                for item in snapshot.get("aiAnswers") or []
                if _string(item.get("projectId")) == project_id
            ],
            "favorites": snapshot.get("favorites") or [],
            "reports": [
                item
                for item in snapshot.get("reports") or []
                if _string(item.get("projectId")) == project_id
            ],
            "tasks": [
                item
                for item in snapshot.get("tasks") or []
                if _string(item.get("projectId")) == project_id
            ],
            "eventLines": [
                item
                for item in snapshot.get("eventLines") or []
                if _string(item.get("projectId")) == project_id
            ],
            "processingAttempts": [],
        }
        status = {
            "projectId": project_id,
            "state": "ready" if project_documents else "empty",
            "documents": project_documents,
            "processingAttempts": [],
            "counts": {
                "total": len(project_documents),
                "ready": sum(
                    1 for item in project_documents if item.get("parseState") == "ready"
                ),
                "partial": sum(
                    1
                    for item in project_documents
                    if item.get("parseState") == "partial_ready"
                ),
                "failed": sum(
                    1
                    for item in project_documents
                    if item.get("parseState") in {"failed", "missing_source"}
                ),
                "pending": sum(
                    1
                    for item in project_documents
                    if item.get("parseState")
                    in {"not_requested", "queued", "processing"}
                ),
            },
            "generatedAt": snapshot.get("generatedAt"),
        }
        dna = {"modules": []}
        project_texts = {}
        project_structure = {"modules": [], "flows": []}
        insights = {
            "judgments": [],
            "topics": [],
            "conflicts": [],
            "openQuestions": [],
        }
        goals = []
    data["documents"] = status.get("documents") or []
    local_materials = (
        LocalProjectMaterialsRepository(compatibility.runtime)
        if hasattr(compatibility.runtime, "database_path")
        else None
    )
    local_folders: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    try:
        if local_materials is None:
            raise LocalRuntimeError(
                404,
                "local_project_state_unavailable",
                "测试运行时没有本机受管存储",
            )
        local_folders = local_materials.folders(project_id)
        documents = local_materials.documents(project_id)
    except LocalRuntimeError:
        local_folders = []
        documents = []
    answers = data.get("answers") or []
    # GC-08 meetings are cloud-authoritative business objects.  The retained
    # local-project-state JSON meeting shell is deliberately not consulted:
    # it used to surface test drafts that did not exist in the strict 88-table
    # graph.  Refresh the verified local projection and use it as the
    # last-confirmed offline view.
    meeting_projection = LocalGC06PlanningProjection(compatibility.runtime)
    try:
        strict_meetings = compatibility.runtime.cloud_query(
            "/api/v2/gc06/meetings",
            query={"clientId": project_id},
        )
        meeting_projection.apply_meetings(strict_meetings)
    except LocalRuntimeError:
        strict_meetings = meeting_projection.list_meetings(client_id=project_id)
    meeting_records = [
        {
            "id": item.get("id"),
            "clientId": project_id,
            "title": item.get("title") or "客户会议",
            "stage": "published" if item.get("status") == "completed" else "prepared",
            "scheduledAt": item.get("startsAt"),
            "updatedAt": item.get("updatedAt"),
            "transcriptText": "",
            "notes": item.get("agenda") or "",
            "agendaItems": [],
            "decisions": [],
            "actionItems": [],
            "risks": [],
            "ambiguities": [],
            "sourceScope": "strict_meeting_projection",
            "_strictVersion": int(item.get("version") or 1),
        }
        for item in strict_meetings
        if isinstance(item, Mapping)
        and _string(item.get("clientId")) == project_id
        and _string(item.get("lifecycleState") or "active") != "deleted"
    ]
    messages = [
        message
        for answer in answers
        for message in _chat_messages(answer, compatibility.runtime)
    ]
    thread_by_id: dict[str, dict[str, Any]] = {}
    for answer in answers:
        thread_id = (
            _string((answer.get("sourceManifest") or {}).get("threadId"))
            or _string(answer.get("answerId"))
        )
        current = thread_by_id.get(thread_id)
        if current is None:
            thread_by_id[thread_id] = {
                "id": thread_id,
                "clientId": project_id,
                "title": answer.get("question") or "工作台问答",
                "createdAt": answer.get("createdAt"),
                "updatedAt": answer.get("updatedAt"),
            }
        elif _string(answer.get("updatedAt")) > _string(
            current.get("updatedAt")
        ):
            current["updatedAt"] = answer.get("updatedAt")
    threads = list(thread_by_id.values())
    knowledge_context = compatibility.runtime.project_knowledge_context(project_id)
    local_knowledge = (
        local_materials.knowledge_presentation(project_id)
        if local_materials is not None
        and hasattr(local_materials, "knowledge_presentation")
        else {"savedMemories": []}
    )
    merged_saved_memories: dict[str, dict[str, Any]] = {}
    for item in [
        *(local_knowledge.get("savedMemories") or []),
        *(knowledge_context.get("savedMemories") or []),
    ]:
        memory_id = _string(item.get("id") or item.get("sourceId"))
        if not memory_id:
            continue
        merged_saved_memories[memory_id] = {
            **item,
            "id": memory_id,
            "title": item.get("title") or item.get("sourceDescription") or "已存记忆",
            "summary": item.get("summary") or item.get("statement") or "",
            "authority": item.get("authority") or (
                "organization_cloud" if item.get("sourceId") else "current_device"
            ),
        }
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
            "retrievalSummary": (
                "当前成员 · 当前项目"
                if item.get("memoryKind") == "favorite"
                else "组织正式项目知识"
            ),
            "documentRole": (
                "本人项目收藏"
                if item.get("memoryKind") == "favorite"
                else "项目正式知识"
            ),
            "sourceLinks": [
                {
                    "targetType": "ai_answer",
                    "targetId": item.get("sourceAnswerId"),
                }
            ],
            "createdAt": item.get("updatedAt"),
            "updatedAt": item.get("updatedAt"),
            "chatMessageId": item.get("sourceAnswerId"),
            "storageKind": (
                (
                    "organization_member_favorite"
                    if item.get("authority") == "organization_cloud"
                    else "local_answer_memory"
                )
                if item.get("memoryKind") == "favorite"
                else "cloud_formal_fact"
            ),
            "authority": item.get("authority") or "current_device",
            "localFileCreated": False,
            "memoryKind": item.get("memoryKind"),
            "version": item.get("version") or 1,
            "status": "active",
        }
        for item in merged_saved_memories.values()
    ]
    resource_states = {
        "documents": "ready" if documents else "empty",
        "answers": "ready" if answers else "empty",
        "reports": "ready" if data.get("reports") else "empty",
        "favorites": "ready" if memory_cards else "empty",
        "folders": "ready" if local_folders else "empty",
        "meetings": "ready" if meeting_records else "empty",
        "goals": "ready" if goals else "empty",
        "projectModules": "ready"
        if project_structure.get("modules")
        else "empty",
        "projectFlows": "ready"
        if project_structure.get("flows")
        else "empty",
        "dna": (
            "ready"
            if dna.get("modules")
            else "empty"
            if rich_domain_endpoints
            else "not_connected"
        ),
    }
    dna_terms = []
    for key, item in project_texts.items():
        if not key.startswith("dna_term:"):
            continue
        try:
            term = json.loads(_string(item.get("markdownContent")))
        except (TypeError, ValueError):
            term = {}
        if not isinstance(term, Mapping):
            term = {}
        dna_terms.append(
            {
                "id": item.get("documentId"),
                "clientId": project_id,
                "category": term.get("category") or "",
                "canonicalName": term.get("canonicalName") or item.get("title"),
                "aliases": list(term.get("aliases") or []),
                "description": term.get("description") or "",
                "sourceLevel": "client",
                "version": item.get("version"),
            }
        )
    return {
        "client": _project_summary(data["project"], data),
        "folders": local_folders,
        "documents": documents,
        "documentCards": [],
        "imports": [],
        "knowledgeStatus": _knowledge_status(status),
        "knowledgeJobs": _knowledge_jobs(status),
        "recentReclassEvents": [],
        "surrogateCount": 0,
        "memoryDocCount": len(memory_cards),
        "memoryCards": memory_cards,
        "threads": threads,
        "recentMessages": messages,
        "analysisRuns": [
            _analysis_run(project_id, answer) for answer in answers
        ],
        "meetings": meeting_records,
        "goals": list(goals),
        "dnaModules": dna.get("modules") or [],
        "projectModules": project_structure.get("modules") or [],
        "projectFlows": project_structure.get("flows") or [],
        "dnaTerms": dna_terms,
        "relatedTasks": data.get("tasks") or [],
        "latestJudgments": insights.get("judgments") or [],
        "latestTopics": insights.get("topics") or [],
        "latestConflicts": insights.get("conflicts") or [],
        "latestOpenQuestions": insights.get("openQuestions") or [],
        "latestRunLogs": data.get("processingAttempts") or [],
        "knowledgeContext": knowledge_context,
        "strictResourceStates": resource_states,
        "strictAuthority": {
            "answers": "ai_answers",
            "memories": "knowledge_documents/document_versions/derivation_lineage",
            "documents": "knowledge_documents/document_versions",
            "reports": "narrative_outputs/narrative_output_versions",
            "processing": "processing_attempts",
        },
    }


@router.get(r"brain/dashboard")
def brain_dashboard(compatibility: Any, _: UiRequest, __: re.Match[str]) -> Any:
    return _cloud_query(compatibility, "/api/v2/workbench/dashboard")


@router.get(r"digital-assets/dashboard")
def digital_asset_dashboard(
    compatibility: Any,
    _: UiRequest,
    __: re.Match[str],
) -> Any:
    return _cloud_query(compatibility, "/api/v2/workbench/digital-assets")


@router.get(r"digital-assets/organization-dna")
def organization_dna(
    compatibility: Any,
    _: UiRequest,
    __: re.Match[str],
) -> Any:
    return _cloud_query(compatibility, "/api/v2/workbench/organization-dna")


@router.get(r"clients/([^/]+)/digital-assets")
def project_digital_assets(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/digital-assets",
    )


@router.get(r"clients/([^/]+)/workspace")
def project_workspace(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _workspace(compatibility, match.group(1))


@router.post(r"clients/([^/]+)/sync")
def sync_project(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    if hasattr(compatibility.runtime, "database_path"):
        store = LocalProjectMaterialsRepository(compatibility.runtime)
        pending_materials = store.pending_cloud_materials(project_id)
        metadata_updates = [
            item
            for item in pending_materials
            if _string(item.get("cloudMetadataOperation")) == "update"
            and _string(item.get("cloudDocumentId") or item.get("documentId"))
        ]
        for item in metadata_updates:
            document_id = _string(
                item.get("cloudDocumentId") or item.get("documentId")
            )
            preview = compatibility.runtime.cloud_query(
                "/api/v2/domain/project-materials/projects/"
                f"{project_id}/documents/{document_id}/reading-preview"
            )
            updated = compatibility.runtime.cloud_command(
                "PATCH",
                "/api/v2/domain/project-materials/projects/"
                f"{project_id}/documents/{document_id}/local-metadata",
                payload={
                    "expectedVersion": int(preview.get("aggregateVersion") or 1),
                    "title": item.get("title") or item.get("fileName"),
                    "fileName": item.get("fileName"),
                    "contentHash": item.get("contentHash"),
                    "byteSize": int(item.get("byteSize") or 0),
                    "mediaType": item.get("mediaType"),
                },
                idempotency_key=(
                    f"{request.idempotency_key}:rename:{document_id}:"
                    f"{sha256_text(_string(item.get('fileName')))[:12]}"
                ),
            )
            store.complete_cloud_metadata_update(
                project_id=project_id,
                document_id=document_id,
                version=int(updated.get("version") or 1),
            )
        pending_materials = [
            item for item in pending_materials if item not in metadata_updates
        ]
        if pending_materials:
            material_signature = sha256_text(
                canonical_json(
                    [
                        {
                            "localSourceId": item.get("localSourceId"),
                            "contentHash": item.get("contentHash"),
                        }
                        for item in pending_materials
                    ]
                )
            )
            registered = compatibility.runtime.cloud_command(
                "POST",
                f"/api/v2/domain/project-materials/projects/{project_id}"
                "/materials/register-metadata",
                payload={
                    "materials": [
                        {
                            "localSourceId": item.get("localSourceId"),
                            "fileName": item.get("fileName"),
                            "contentHash": item.get("contentHash"),
                            "byteSize": int(item.get("byteSize") or 0),
                            "mediaType": item.get("mediaType"),
                            **(
                                {
                                    "relationKind": "task",
                                    "relationId": item.get("taskId"),
                                }
                                if str(item.get("taskId") or "")
                                else {
                                    "sourceKind": "local_private_metadata",
                                }
                            ),
                        }
                        for item in pending_materials
                    ]
                },
                idempotency_key=(
                    f"{request.idempotency_key}:pending:{material_signature}"
                ),
            )
            store.bind_cloud_documents(
                project_id=project_id,
                local_materials=pending_materials,
                cloud_documents=registered.get("documents") or [],
            )
        for pending_delete in store.pending_cloud_deletes(project_id):
            document_id = _string(pending_delete.get("documentId"))
            if not document_id:
                continue
            try:
                preview = compatibility.runtime.cloud_query(
                    "/api/v2/domain/project-materials/projects/"
                    f"{project_id}/documents/{document_id}/reading-preview"
                )
                compatibility.runtime.cloud_command(
                    "DELETE",
                    "/api/v2/domain/project-materials/projects/"
                    f"{project_id}/documents/{document_id}",
                    payload={
                        "expectedVersion": int(
                            preview.get("aggregateVersion") or 1
                        )
                    },
                    idempotency_key=(
                        f"{request.idempotency_key}:delete:{document_id}"
                    ),
                )
            except LocalRuntimeError as exc:
                if exc.status_code != 404:
                    raise
            store.complete_cloud_delete(project_id, document_id)
    # GC-07 synchronization has already settled its strict metadata commands.
    # Do not invoke the frozen pre-blueprint all-business snapshot here; the
    # current project response below is the authoritative v2 read model.
    workspace = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/workspace",
    )
    return _project_summary(workspace["project"], workspace)


@router.post(r"clients/([^/]+)/workspace/chat/plan")
def plan_chat(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    """Create a question-specific, user-visible analysis outline.

    This route intentionally asks the model for a concise public plan rather
    than returning provider reasoning tokens.  It is read-only and stores no
    second business fact.
    """

    started_at = time.monotonic()
    project_id = match.group(1)
    _require_project_read(compatibility, project_id)
    prompt = _string(request.body.get("prompt"))
    if not prompt:
        raise LocalRuntimeError(422, "prompt_required", "请输入问题")
    mode = _string(request.body.get("creativityMode")) or "balanced"
    if mode not in {"creative", "balanced", "strict"}:
        mode = "balanced"
    selected_document_ids = [
        _string(value)
        for value in request.body.get("workingDocumentIds") or []
        if _string(value)
    ][:8]
    selected_titles: list[str] = []
    if selected_document_ids and hasattr(compatibility.runtime, "database_path"):
        store = LocalProjectMaterialsRepository(compatibility.runtime)
        for document_id in selected_document_ids:
            try:
                document = store.document_text(document_id)
            except LocalRuntimeError:
                continue
            if _string(document.get("projectId")) != project_id:
                continue
            title = _string(document.get("title"))
            if title:
                selected_titles.append(title)

    mode_instruction = {
        "strict": "资料优先：事实、推断、未知必须严格分开。",
        "balanced": "兼顾资料：以项目事实为底色，再作必要分析。",
        "creative": "创意优先：项目事实仍是边界，可提出多种假设方向。",
    }[mode]
    source_hint = (
        "用户已指定资料：" + "、".join(f"《{title}》" for title in selected_titles)
        if selected_titles
        else (
            "可按问题需要查找：当前项目本机资料、组织共享摘要、客户档案、"
            "官网事实、正式会议纪要、人工纠错/补充和当前对话。"
        )
    )
    planning_prompt = (
        "你正在为用户生成一份可公开展示的本题分析思路。"
        "它不是最终答案，也不是隐藏思维链逐字稿；必须具体对应用户的问题，"
        "让用户看懂你如何理解意图、准备从哪些方向分析、将查找哪些资料、"
        "以及会防止哪些误判。不要使用‘深入分析’‘综合考虑’等空话。\n"
        f"回答模式：{mode_instruction}\n{source_hint}\n"
        "只输出一个 JSON 对象，不要 Markdown。narrative 要写成连贯的第一人称分析叙述，"
        "不是字段说明或机械步骤；用三至六个短段落，说清楚我怎样理解问题、准备怎样判断、"
        "会核对哪些资料以及证据不足时怎样收束，不要提前给最终答案："
        '{"narrative":"针对本题的连续分析叙述",'
        '"intent":"一句话说明对本题意图的具体理解",'
        '"directions":["具体分析方向1","具体分析方向2"],'
        '"plannedSources":["具体资料或知识类别1","具体资料或知识类别2"],'
        '"cautions":["本题需要防止的误判1"]}\n'
        f"用户问题：{prompt[:4_000]}"
    )
    plan = _fallback_public_analysis_plan(
        prompt,
        selected_titles=selected_titles,
        mode=mode,
    )
    try:
        completion = compatibility.runtime.organization_ai_completion(
            messages=[
                {
                    "role": "system",
                    "content": "你只负责生成面向用户的公开分析计划，不回答最终问题。",
                },
                {"role": "user", "content": planning_prompt},
            ],
            temperature=0.15,
            # The public analysis should become visible while the final
            # answer is still running.  Keep its own provider wait shorter
            # than the interactive dispatch deadline; a question-specific
            # fallback is preferable to an empty progress card.
            read_timeout_seconds=25.0,
            max_output_tokens=1_000,
            thinking_enabled=False,
        )
        plan = _normalize_public_analysis_plan(
            completion.get("content"),
            prompt=prompt,
            selected_titles=selected_titles,
            mode=mode,
        )
    except Exception:
        # The final answer remains available even if this optional public-plan
        # pass fails.  The fallback is question-specific and does not claim a
        # provider call succeeded.
        pass
    return {
        "plan": plan,
        "elapsedMs": max(1, int((time.monotonic() - started_at) * 1000)),
    }


@router.post(r"clients/([^/]+)/workspace/chat/start")
def start_chat(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    _require_project_read(compatibility, project_id)
    prompt = _string(request.body.get("prompt"))
    raw_image_inputs = request.body.get("imageInputs") or []
    if not isinstance(raw_image_inputs, list):
        raise LocalRuntimeError(422, "chat_images_invalid", "图片输入格式无效")
    if len(raw_image_inputs) > 4:
        raise LocalRuntimeError(422, "chat_images_too_many", "一次最多附加4张图片")
    image_context_items: list[dict[str, Any]] = []
    image_source_receipts: list[dict[str, Any]] = []
    local_image_objects: list[dict[str, Any]] = []
    total_image_bytes = 0
    supported_image_types = {"image/png", "image/jpeg", "image/webp"}
    for index, raw_image in enumerate(raw_image_inputs):
        if not isinstance(raw_image, Mapping):
            raise LocalRuntimeError(422, "chat_image_invalid", "图片输入格式无效")
        mime_type = _string(raw_image.get("mimeType")).lower()
        data_url = _string(raw_image.get("dataUrl"))
        name = _string(raw_image.get("name")) or f"图片{index + 1}"
        prefix = f"data:{mime_type};base64,"
        if mime_type not in supported_image_types or not data_url.startswith(prefix):
            raise LocalRuntimeError(422, "chat_image_type_unsupported", "仅支持 PNG、JPG 和 WebP 图片")
        try:
            image_bytes = base64.b64decode(data_url[len(prefix):], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise LocalRuntimeError(422, "chat_image_invalid", "图片内容无效") from exc
        if not image_bytes or len(image_bytes) > 8 * 1024 * 1024:
            raise LocalRuntimeError(422, "chat_image_too_large", "单张图片不能超过8MB")
        total_image_bytes += len(image_bytes)
        if total_image_bytes > 20 * 1024 * 1024:
            raise LocalRuntimeError(422, "chat_images_too_large", "本轮图片总大小不能超过20MB")
        content_hash = hashlib.sha256(image_bytes).hexdigest()
        image_context_items.append({"name": name[:120], "mimeType": mime_type, "dataUrl": data_url})
        local_image_objects.append(
            {
                "name": name[:120],
                "mimeType": mime_type,
                "bytes": image_bytes,
                "contentHash": content_hash,
            }
        )
        image_source_receipts.append(
            {"name": name[:120], "mimeType": mime_type, "size": len(image_bytes), "contentHash": content_hash}
        )
    if not prompt and image_context_items:
        prompt = "请理解这些图片，并结合当前项目上下文回答。"
    if not prompt:
        raise LocalRuntimeError(422, "prompt_required", "请输入问题")
    mode = _string(request.body.get("creativityMode")) or "balanced"
    if mode not in {"creative", "balanced", "strict"}:
        mode = "balanced"
    deep_thinking_requested = bool(request.body.get("deepThinking"))
    stream_event_callback = request.body.get("_streamEventCallback")
    if not callable(stream_event_callback):
        stream_event_callback = None
    public_analysis_plan = request.body.get("publicAnalysisPlan")
    if not isinstance(public_analysis_plan, Mapping):
        public_analysis_plan = None
    requested_thread_id = _string(request.body.get("threadId"))
    thread_id = requested_thread_id or _stable_ui_id(
        "chat_thread",
        project_id,
        request.idempotency_key,
    )
    local_image_receipts: list[dict[str, Any]] = []
    if local_image_objects:
        if not hasattr(compatibility.runtime, "persist_workbench_chat_images"):
            raise LocalRuntimeError(
                503,
                "chat_image_storage_unavailable",
                "本机图片保存能力暂不可用，请稍后重试",
            )
        local_image_receipts = compatibility.runtime.persist_workbench_chat_images(
            project_id=project_id,
            thread_id=thread_id,
            images=local_image_objects,
        )
    selected_document_ids = [
        _string(value)
        for value in request.body.get("workingDocumentIds") or []
        if _string(value)
    ][:8]
    per_document_context_limit = min(
        6_000,
        max(1_000, 8_000 // max(1, len(selected_document_ids))),
    )
    private_context_items: list[dict[str, Any]] = []
    selected_sources: list[dict[str, Any]] = []
    retrieved_sources: list[dict[str, Any]] = []
    retrieval_pass_count = 1
    local_retrieval_state = "selected_documents" if selected_document_ids else "ready"
    local_retrieval_message: str | None = None
    store: LocalProjectMaterialsRepository | None = None
    if selected_document_ids and hasattr(compatibility.runtime, "database_path"):
        store = LocalProjectMaterialsRepository(compatibility.runtime)
        for document_id in selected_document_ids:
            local = store.document_text(document_id)
            if _string(local.get("projectId")) != project_id:
                raise LocalRuntimeError(
                    409,
                    "local_document_project_mismatch",
                    "引用资料不属于当前项目，请刷新后重试",
                )
            content = _string(local.get("content"))
            if not content:
                continue
            selected_content = select_relevant_excerpt(
                content,
                prompt,
                max_chars=per_document_context_limit,
            )
            private_context_items.append(
                {
                    "documentId": document_id,
                    "title": local.get("title") or document_id,
                    "content": selected_content,
                }
            )
            selected_sources.append(
                {
                    "documentId": document_id,
                    "contentHash": local.get("contentHash"),
                    "title": local.get("title") or document_id,
                    "fullContentChars": len(content),
                    "includedContentChars": len(selected_content),
                    "retrievalMode": "user_selected_excerpt",
                }
            )
    elif hasattr(compatibility.runtime, "database_path"):
        # A normal workbench question must be able to use the current
        # project's already-built local Wiki without forcing the user to know
        # a filename first.  The file-card arrow remains an explicit priority
        # scope; this branch is the automatic project-Wiki recall path.
        store = LocalProjectMaterialsRepository(compatibility.runtime)
        try:
            retrieval_queries = [prompt]
            if deep_thinking_requested:
                # 深度思考会把复合问题拆成少量检索面，避免只用整句查询
                # 命中一个表面相似片段。拆解只用于检索，不被当作事实。
                for clause in re.split(r"[。！？!?；;\n]+", prompt):
                    normalized_clause = _string(clause)
                    if 4 <= len(normalized_clause) <= 120 and normalized_clause not in retrieval_queries:
                        retrieval_queries.append(normalized_clause)
                    if len(retrieval_queries) >= 4:
                        break
            retrieval_pass_count = len(retrieval_queries)
            hits_by_document: dict[str, list[dict[str, Any]]] = {}
            seen_hits: set[tuple[str, str]] = set()
            for retrieval_query in retrieval_queries:
                retrieval = store.search_local_wiki(
                    project_id=project_id,
                    query=retrieval_query,
                    limit=12 if deep_thinking_requested else 12,
                )
                for raw_hit in retrieval.get("hits") or []:
                    if not isinstance(raw_hit, Mapping):
                        continue
                    document_id = _string(raw_hit.get("documentId"))
                    excerpt = _string(raw_hit.get("excerpt"))
                    hit_key = (document_id, _string(raw_hit.get("chunkId")) or excerpt[:120])
                    if not document_id or not excerpt or hit_key in seen_hits:
                        continue
                    seen_hits.add(hit_key)
                    hits_by_document.setdefault(document_id, []).append(dict(raw_hit))
            for document_id, hits in list(hits_by_document.items())[:4]:
                local = store.document_text(document_id)
                if _string(local.get("projectId")) != project_id:
                    continue
                excerpts = [
                    _string(item.get("excerpt"))
                    for item in hits[:3]
                    if _string(item.get("excerpt"))
                ]
                content = "\n\n".join(excerpts)[:6_000]
                if not content:
                    continue
                title = _string(local.get("title")) or _string(hits[0].get("title")) or document_id
                private_context_items.append(
                    {
                        "documentId": document_id,
                        "title": title,
                        "content": content,
                    }
                )
                retrieved_sources.append(
                    {
                        "documentId": document_id,
                        "contentHash": local.get("contentHash"),
                        "title": title,
                        "fullContentChars": len(_string(local.get("content"))),
                        "includedContentChars": len(content),
                        "retrievalMode": _string(hits[0].get("retrievalMode")) or "local_hybrid",
                        "score": hits[0].get("score"),
                        "chunkIds": [
                            _string(item.get("chunkId"))
                            for item in hits[:3]
                            if _string(item.get("chunkId"))
                        ],
                        "factIds": [
                            _string(item.get("factId"))
                            for item in hits[:3]
                            if _string(item.get("factId"))
                        ],
                        "evidenceIds": [
                            _string(item.get("evidenceId"))
                            for item in hits[:3]
                            if _string(item.get("evidenceId"))
                        ],
                    }
                )
        except LocalRuntimeError as exc:
            # Local Wiki recall is an optional evidence lane.  A missing or
            # rebuilding index must not turn the main question into a fake
            # success or a full-screen failure.
            local_retrieval_state = (
                "failed_retryable" if exc.status_code >= 500 else "blocked"
            )
            local_retrieval_message = exc.message
    active_skill_id = _string(request.body.get("activeSkillId"))
    requested_agent_skill_ids = [
        _string(value)
        for value in request.body.get("activeSkillIds") or []
        if _string(value)
    ]
    if active_skill_id.startswith("skill_"):
        requested_agent_skill_ids.insert(0, active_skill_id)
        active_skill_id = ""
    requested_agent_skill_ids = list(dict.fromkeys(requested_agent_skill_ids))
    if len(requested_agent_skill_ids) > 5:
        raise LocalRuntimeError(
            422,
            "agent_skill_selection_too_large",
            "一次最多组合5个写作模板",
        )
    writing_style = ""
    agent_skills: list[dict[str, Any]] = []
    if active_skill_id:
        writing_style, legacy_agent_skill = _selected_style_or_agent_skill(
            compatibility,
            active_skill_id,
        )
        if legacy_agent_skill:
            agent_skills.append(legacy_agent_skill)
    for selected_agent_skill_id in requested_agent_skill_ids:
        _, agent_skill = _selected_style_or_agent_skill(
            compatibility,
            selected_agent_skill_id,
        )
        if agent_skill is None:
            raise LocalRuntimeError(
                422,
                "agent_skill_id_invalid",
                "写作模板标识无效",
            )
        agent_skills.append(agent_skill)
    history_messages: list[dict[str, str]] = []
    if requested_thread_id:
        for answer in compatibility.runtime.workbench_chat_history(
            project_id,
            requested_thread_id,
        ):
            history_messages.extend(
                [
                    {
                        "role": "user",
                        "content": _string(answer.get("question")),
                    },
                    {
                        "role": "assistant",
                        "content": _string(answer.get("answerMarkdown")),
                    },
                ]
            )
    saved = compatibility.runtime.workbench_chat(
        project_id=project_id,
        question=prompt,
        mode=mode,
        private_context_items=private_context_items,
        history_messages=history_messages,
        writing_style=writing_style,
        agent_skills=agent_skills,
        image_context_items=image_context_items,
        deep_thinking=deep_thinking_requested,
        stream_event_callback=stream_event_callback,
        source_manifest_extra={
            "operationKey": f"{request.idempotency_key}:chat-answer",
            "workbenchKind": "project_chat",
            "threadId": thread_id,
            "activeSkillId": active_skill_id or None,
            "activeSkillIds": [item.get("skillId") for item in agent_skills],
            "activeAgentSkillVersions": [
                {
                    "skillId": item.get("skillId"),
                    "version": int(item.get("version") or 1),
                    "contentHash": item.get("contentHash"),
                }
                for item in agent_skills
            ],
            "selectedDocuments": selected_sources,
            "retrievedDocuments": retrieved_sources,
            "localRetrievalState": local_retrieval_state,
            "localRetrievalMessage": local_retrieval_message,
            "retrievalPassCount": retrieval_pass_count,
            "publicAnalysisPlan": dict(public_analysis_plan or {}),
            "transientImageInputs": image_source_receipts,
            "localChatImageInputs": local_image_receipts,
        },
        idempotency_key=f"{request.idempotency_key}:chat-answer",
    )
    answer = saved.get("answer") or {}
    user, assistant = _chat_messages(answer, compatibility.runtime)
    skill_runs: list[dict[str, Any]] = []
    for item in agent_skills:
        skill_id = _string(item.get("skillId"))
        if not skill_id:
            continue
        try:
            skill_runs.append(
                compatibility.runtime.cloud_command(
                    "POST",
                    f"/api/v2/agent-skills/{skill_id}/runs",
                    payload={
                        "agentKind": "project_workspace",
                        "inputHash": sha256_text(prompt),
                        "resultHash": sha256_text(
                            _string(answer.get("answerMarkdown"))
                        ),
                        "sourceCount": int(
                            (answer.get("sourceManifest") or {}).get("sourceCount")
                            or len(selected_sources)
                            + len(retrieved_sources)
                        ),
                    },
                    idempotency_key=(
                        f"{request.idempotency_key}:skill-run:{skill_id}"
                    ),
                )
            )
        except LocalRuntimeError as exc:
            skill_runs.append(
                {
                    "skillId": skill_id,
                    "shortName": item.get("shortName"),
                    "status": (
                        "failed_retryable" if exc.status_code >= 500 else "blocked"
                    ),
                    "message": exc.message,
                    "retryable": exc.status_code >= 500,
                }
            )
    memory_update: dict[str, Any] | None = None
    memory_statement = _explicit_project_memory_statement(prompt)
    if memory_statement:
        try:
            formal_memory = compatibility.runtime.workbench_remember_answer_fact(
                project_id=project_id,
                answer_id=_string(answer.get("answerId")),
                statement=memory_statement,
                idempotency_key=f"{request.idempotency_key}:explicit-memory",
            )
            propagation = formal_memory.get("consumerPropagation")
            propagation_state = (
                _string(propagation.get("state"))
                if isinstance(propagation, Mapping)
                else "failed_retryable"
            )
            memory_update = {
                "state": "ready" if propagation_state == "completed" else "failed_retryable",
                "message": (
                    "相关记忆已更新，相关页面正在整理"
                    if propagation_state == "completed"
                    else "相关记忆已保存，部分页面更新失败，可以重试"
                ),
                "memoryId": formal_memory.get("factId"),
                "memoryKind": "explicit_memory",
            }
        except LocalRuntimeError as exc:
            memory_update = {
                "state": "failed_retryable" if exc.status_code >= 500 else "blocked",
                "message": exc.message,
                "memoryId": None,
                "memoryKind": "explicit_memory",
            }
        except Exception:
            memory_update = {
                "state": "failed_retryable",
                "message": "项目记忆写入异常，可以重试",
                "memoryId": None,
                "memoryKind": "explicit_memory",
            }
    return {
        "threadId": thread_id,
        "userMessage": user,
        "assistantMessage": assistant,
        "analysisRun": _analysis_run(project_id, answer),
        "skillRuns": skill_runs,
        "memoryUpdate": memory_update,
    }


def _answer_for_message(
    compatibility: Any,
    message_id: str,
    *,
    expected_project_id: str | None = None,
) -> dict[str, Any]:
    if expected_project_id is not None:
        _require_project_read(compatibility, expected_project_id)
    answer_id = message_id.removesuffix(":question")
    answer = compatibility.runtime.workbench_answer(answer_id)
    if (
        expected_project_id is not None
        and _string(answer.get("projectId")) != expected_project_id
    ):
        raise LocalRuntimeError(
            404,
            "answer_project_mismatch",
            "当前项目没有该工作台回答",
        )
    return answer


def _favorite_excerpt(answer: Mapping[str, Any]) -> str:
    """Return the canonical visible answer body for a member favorite."""
    return _string(
        answer.get("answerMarkdown")
        or answer.get("answer")
        or answer.get("content")
    )


@router.get(r"clients/([^/]+)/workspace/chat/messages/([^/]+)")
def get_chat_message(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    message_id = match.group(2)
    answer = _answer_for_message(
        compatibility,
        message_id,
        expected_project_id=project_id,
    )
    user, assistant = _chat_messages(answer, compatibility.runtime)
    return user if message_id.endswith(":question") else assistant


@router.get(r"clients/([^/]+)/workspace/chat/threads/([^/]+)")
def get_chat_thread(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, thread_id = match.group(1), match.group(2)
    _require_project_read(compatibility, project_id)
    answers = compatibility.runtime.workbench_chat_history(project_id, thread_id)
    if not answers:
        answers = [
            _answer_for_message(
                compatibility,
                thread_id,
                expected_project_id=project_id,
            )
        ]
    answers.sort(key=lambda item: _string(item.get("createdAt")))
    messages = [
        message
        for answer in answers
        for message in _chat_messages(answer, compatibility.runtime)
    ]
    return {
        "thread": {
            "id": thread_id,
            "clientId": project_id,
            "title": answers[0].get("question") or "工作台问答",
            "createdAt": answers[0].get("createdAt"),
            "updatedAt": answers[-1].get("updatedAt"),
        },
        "messages": messages,
    }


@router.delete(r"clients/([^/]+)/workspace/chat/messages/([^/]+)")
def delete_chat_pair(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, message_id = match.group(1), match.group(2)
    answer_id = (
        message_id[: -len(":question")]
        if message_id.endswith(":question")
        else message_id
    )

    def delete_payload() -> dict[str, Any]:
        answer = _answer_for_message(
            compatibility,
            message_id,
            expected_project_id=project_id,
        )
        return {"expectedVersion": answer.get("version")}

    replayable_cloud_mutation(
        compatibility.runtime,
        idempotency_key=request.idempotency_key,
        command_type="workbench.chat_pair_delete",
        aggregate_type="ai_answer",
        aggregate_id=answer_id,
        method="DELETE",
        path=f"/api/v2/workbench/answers/{answer_id}",
        request_payload={"projectId": project_id, "messageId": message_id},
        cloud_payload_factory=delete_payload,
    )
    return {
        "clientId": project_id,
        "threadId": answer_id,
        "deletedIds": [f"{answer_id}:question", answer_id],
        "threadDeleted": True,
        "alreadyDeleted": False,
    }


@router.post(r"clients/([^/]+)/knowledge/vectorize-answer")
def favorite_answer(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    message_id = _string(request.body.get("messageId"))
    answer = _answer_for_message(
        compatibility,
        message_id,
        expected_project_id=project_id,
    )
    result = _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/mobile-consult/answers/{_string(answer.get('answerId'))}/favorite",
        {"projectId": project_id, "excerpt": _favorite_excerpt(answer)},
    )
    return {
        **result,
        "documentId": result.get("favoriteId"),
        "storageKind": "organization_member_favorite",
        "message": "已收藏，并自动同步到本人其他设备",
    }


@router.delete(r"clients/([^/]+)/knowledge/memory-cards/by-message/([^/]+)")
def unfavorite_answer(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, message_id = match.group(1), match.group(2)
    answer = _answer_for_message(
        compatibility,
        message_id,
        expected_project_id=project_id,
    )
    favorites = _cloud_query(compatibility, f"/api/v2/mobile-consult/projects/{project_id}/favorites")
    favorite = next((item for item in favorites.get("favorites", []) if _string(item.get("answerId")) == _string(answer.get("answerId"))), None)
    if not favorite:
        return {"removed": True, "alreadyRemoved": True}
    return _cloud_command(compatibility, request, "DELETE", f"/api/v2/mobile-consult/favorites/{_string(favorite.get('favoriteId'))}", {})


@router.post(r"clients/([^/]+)/workspace/chat/messages/([^/]+)/facts/corrections")
def correct_answer_fact(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, message_id = match.group(1), match.group(2)
    answer = _answer_for_message(
        compatibility,
        message_id,
        expected_project_id=project_id,
    )
    return compatibility.runtime.workbench_correct_answer_fact(
        project_id=project_id,
        answer_id=_string(answer.get("answerId")),
        selected_text=_string(request.body.get("selectedText")),
        correction_kind=_string(request.body.get("correctionKind")),
        statement=_string(request.body.get("statement")),
        idempotency_key=request.idempotency_key,
    )


@router.get(r"clients/([^/]+)/knowledge/memory-sync")
def memory_sync_status(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    _require_project_read(compatibility, match.group(1))
    return compatibility.runtime.workbench_memory_sync_status(
        project_id=match.group(1),
    )


@router.post(r"clients/([^/]+)/knowledge/memory-sync")
def prepare_memory_sync(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    _require_project_read(compatibility, match.group(1))
    return compatibility.runtime.workbench_prepare_memory_sync(
        project_id=match.group(1),
        idempotency_key=request.idempotency_key,
    )


@router.get(r"clients/([^/]+)/dna-documents")
def dna_modules(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/dna",
    )


@router.get(r"clients/([^/]+)/dna-documents/([^/]+)")
def dna_module(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    result = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/dna",
    )
    module = next(
        (
            item
            for item in result.get("modules") or []
            if item.get("moduleKey") == match.group(2)
        ),
        None,
    )
    if module is None:
        raise LocalRuntimeError(404, "dna_module_missing", "DNA 模块不存在")
    return module


def _dna_body(body: Mapping[str, Any]) -> dict[str, Any]:
    markdown = _string(body.get("markdownContent"))
    file_name = _string(body.get("fileName"))
    file_path = _string(body.get("filePath"))
    if not markdown and file_path:
        source = Path(file_path).expanduser().resolve()
        try:
            if source.stat().st_size > 2_000_000:
                raise LocalRuntimeError(413, "dna_file_too_large", "DNA 文档不得超过 2MB")
            markdown = source.read_text(encoding="utf-8")
            file_name = file_name or source.name
        except UnicodeDecodeError as exc:
            raise LocalRuntimeError(
                422,
                "dna_file_not_text",
                "当前 DNA 上传仅支持 UTF-8 文本或 Markdown",
            ) from exc
        except OSError as exc:
            raise LocalRuntimeError(422, "dna_file_unreadable", "无法读取所选 DNA 文档") from exc
    return {
        "markdownContent": markdown,
        "fileName": file_name,
        "expectedVersion": int(body.get("expectedVersion") or 0),
    }


@router.post(r"clients/([^/]+)/dna-documents/([^/]+)")
def save_dna(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "PUT",
        f"/api/v2/workbench/projects/{match.group(1)}/dna/{match.group(2)}",
        _dna_body(request.body),
    )


@router.get(r"clients/([^/]+)/knowledge/progress")
def knowledge_progress(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    status = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/knowledge-status",
    )
    return {
        "knowledgeStatus": _knowledge_status(status),
        "knowledgeJobs": _knowledge_jobs(status),
        "strictState": status.get("state"),
    }


@router.get(r"clients/([^/]+)/knowledge/parse-failures")
def knowledge_failures(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    status = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/knowledge-status",
    )
    return [
        {
            "documentId": item.get("documentId"),
            "title": item.get("title"),
            "path": "",
            "kind": item.get("documentKind"),
            "parseStatus": item.get("parseState"),
            "error": (
                (item.get("latestProcessingAttempt") or {}).get("errorMessage") or ""
            ),
            "failureType": (
                (item.get("latestProcessingAttempt") or {}).get("errorCode")
                or item.get("parseState")
            ),
            "recoverable": item.get("parseState") != "missing_source",
            "lastRetryAt": (
                (item.get("latestProcessingAttempt") or {}).get("createdAt")
            ),
            "recommendedAction": (
                "重新绑定原始文件"
                if item.get("parseState") == "missing_source"
                else "重试严格新版处理任务"
            ),
        }
        for item in status.get("documents") or []
        if item.get("parseState") in {"failed", "missing_source"}
    ]


@router.get(r"clients/([^/]+)/knowledge/vector-index/status")
def vector_status(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    context = compatibility.runtime.project_knowledge_context(project_id)
    state = str((context.get("state") or {}).get("overall") or "empty")
    counts = context.get("counts") or {}
    ready = state in {"ready", "partial_ready"} or bool(
        context.get("items") or context.get("summaryExcerpts")
    )
    return {
        "clientId": project_id,
        "embeddingSignature": "not-applicable:strict-project-context-v2",
        "activeCollection": "strict_project_knowledge_context",
        "status": "ready" if ready else "empty",
        "masterIndexed": int(
            counts.get("organizationShared")
            or counts.get("ready")
            or len(context.get("items") or [])
        ),
        "chunkIndexed": len(context.get("summaryExcerpts") or []),
        "error": None,
        "updatedAt": context.get("generatedAt") or _now(),
        "strictDocumentCount": int(
            counts.get("total") or len(context.get("items") or [])
        ),
        "fallbackUsed": True,
        "retrievalMode": "strict_relational_context",
    }


@router.get(r"clients/([^/]+)/analysis-runs/([^/]+)")
def analysis_run(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, run_id = match.group(1), match.group(2)
    try:
        answer = _answer_for_message(
            compatibility,
            run_id,
            expected_project_id=project_id,
        )
        return _analysis_run(project_id, answer)
    except LocalRuntimeError as exc:
        if exc.code != "answer_missing":
            raise
    status = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/analysis-status",
    )
    attempt = next(
        (
            item
            for item in status.get("attempts") or []
            if item.get("processingAttemptId") == run_id
        ),
        None,
    )
    if attempt is None:
        raise LocalRuntimeError(404, "analysis_run_missing", "分析记录不存在")
    answer = {
        "answerId": run_id,
        "question": attempt.get("processingKind") or "资料分析",
        "answerMarkdown": "",
        "createdAt": attempt.get("createdAt"),
        "updatedAt": attempt.get("finishedAt")
        or attempt.get("startedAt")
        or attempt.get("createdAt"),
    }
    return _analysis_run(project_id, answer, state=_string(attempt.get("state")))


def _narrative_with_profile_updates(
    compatibility: Any,
    project_id: str,
) -> dict[str, Any]:
    """Overlay member-private memories without publishing them to cloud."""

    result = dict(
        _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/narrative",
        )
    )
    try:
        result["localProjection"] = compatibility.runtime.workbench_project_strategic_profile(
            result
        )
    except LocalRuntimeError as exc:
        result["localProjection"] = {
            "state": "failed_retryable" if exc.status_code >= 500 else "blocked",
            "projected": False,
            "message": exc.message,
        }
    updates_by_id = {
        _string(item.get("id")): dict(item)
        for item in result.get("profileUpdates") or []
        if isinstance(item, Mapping) and _string(item.get("id"))
    }
    try:
        local_knowledge = LocalProjectMaterialsRepository(
            compatibility.runtime
        ).knowledge_presentation(project_id)
    except LocalRuntimeError:
        local_knowledge = {"savedMemories": []}
    for item in local_knowledge.get("savedMemories") or []:
        if not isinstance(item, Mapping):
            continue
        memory_kind = _string(item.get("memoryKind"))
        if memory_kind not in {"explicit_memory", "correction"}:
            continue
        memory_id = _string(item.get("id"))
        statement = _string(item.get("summary"))
        if not memory_id or not statement:
            continue
        correction_kind = _string(item.get("correctionKind"))
        if correction_kind not in {"correction", "supplement"}:
            correction_kind = "correction"
        is_correction = memory_kind == "correction"
        updates_by_id[memory_id] = {
            "id": memory_id,
            "updateKind": correction_kind if is_correction else "explicit_memory",
            "title": (
                "人工纠错" if correction_kind == "correction" else "人工补充"
            ) if is_correction else "明确记住",
            "statement": statement,
            "authority": "organization_cloud",
            "visibility": "organization",
            "incorporationState": "formal_fact_ready",
            "sourceAnswerId": item.get("sourceAnswerId"),
            "version": max(1, int(item.get("version") or 1)),
            "updatedAt": item.get("updatedAt") or _now(),
        }
    profile_updates = sorted(
        updates_by_id.values(),
        key=lambda item: (_string(item.get("updatedAt")), _string(item.get("id"))),
        reverse=True,
    )[:20]
    narrative_generated_at = _string(result.get("generatedAt"))
    result["profileUpdates"] = profile_updates
    result["narrativeNeedsRefresh"] = bool(
        result.get("narrativeNeedsRefresh")
        or any(
            _string(item.get("updatedAt")) > narrative_generated_at
            for item in profile_updates
            if _string(item.get("updateKind")) in {"explicit_memory", "remember", "correction", "supplement"}
        )
    )
    result["memberProfileUpdatedAt"] = next(
        (
            item.get("updatedAt")
            for item in profile_updates
            if _string(item.get("updateKind")) == "explicit_memory"
        ),
        None,
    )
    return result


@router.get(r"clients/([^/]+)/narrative")
def narrative(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _narrative_with_profile_updates(compatibility, match.group(1))


@router.get(r"clients/([^/]+)/narrative/stale-status")
def narrative_stale_status(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    result = _narrative_with_profile_updates(compatibility, match.group(1))
    stale = (
        result.get("lifecycleState") == "stale"
        or bool(result.get("narrativeNeedsRefresh"))
    )
    latest_organization_update = next(
        (
            item
            for item in result.get("profileUpdates") or []
            if _string(item.get("visibility")) == "organization"
        ),
        None,
    )
    return {
        "isStale": stale,
        "markedAt": (
            (latest_organization_update or {}).get("updatedAt")
            or result.get("updatedAt")
            if stale
            else ""
        ),
        "narrativeGeneratedAt": result.get("generatedAt") or "",
        "lastDocTitle": (
            (latest_organization_update or {}).get("title") or ""
        ),
        "reason": (
            "工作台纠错/补充已进入客户档案，组织叙事可重新生成"
            if latest_organization_update
            else "权威输入已有更新"
            if stale
            else ""
        ),
    }


@router.post(r"clients/([^/]+)/narrative/stale-clear")
def clear_narrative_stale(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)

    def payload_factory() -> dict[str, Any]:
        narrative_data = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/narrative",
        )
        report = _cloud_query(
            compatibility,
            f"/api/v2/workbench/reports/{narrative_data['id']}",
        )
        latest = report.get("latest") or {}
        return {
            "expectedVersion": report.get("aggregateVersion"),
            "title": report.get("title"),
            "contentMarkdown": latest.get("content_markdown"),
            "contentJson": latest.get("content_payload") or {},
            "changeSummary": "确认当前叙事版本仍然有效",
        }

    narrative_data = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/narrative",
    )
    _replayable_workbench_mutation(
        compatibility,
        request,
        "PATCH",
        f"/api/v2/workbench/reports/{narrative_data['id']}",
        aggregate_type="narrative_output",
        aggregate_id=_string(narrative_data.get("id")),
        payload_factory=payload_factory,
    )
    return {"ok": True}


@router.get(r"clients/([^/]+)/report-artifacts")
def project_reports(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return compatibility.runtime.cloud_query(
        f"/api/v2/workbench/projects/{match.group(1)}/reports"
    )


@router.get(r"report-artifacts/([^/]+)")
def report_detail(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/reports/{match.group(1)}",
    )


@router.get(r"report-artifacts/([^/]+)/versions")
def report_versions(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return compatibility.runtime.cloud_query(
        f"/api/v2/workbench/reports/{match.group(1)}/versions"
    )


@router.patch(r"report-artifacts/([^/]+)")
def update_report(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "PATCH",
        f"/api/v2/workbench/reports/{match.group(1)}",
        {
            "expectedVersion": request.body.get(
                "expectedVersion", request.body.get("expected_version")
            ),
            "title": request.body.get("title"),
            "contentMarkdown": request.body.get(
                "contentMarkdown", request.body.get("content_markdown")
            ),
            "contentJson": request.body.get(
                "contentJson", request.body.get("content_payload")
            )
            or {},
            "changeSummary": request.body.get(
                "changeSummary", request.body.get("change_summary")
            ),
        },
    )


@router.post(r"report-artifacts/([^/]+)/restore")
def restore_report(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/reports/{match.group(1)}/restore",
        {
            "expectedVersion": request.body.get(
                "expectedVersion", request.body.get("expected_version")
            ),
            "restoreVersion": request.body.get(
                "restoreVersion", request.body.get("restore_version")
            ),
            "changeSummary": request.body.get(
                "changeSummary", request.body.get("change_summary")
            ),
        },
    )


@router.get(r"retrieval/health")
def retrieval_health(
    compatibility: Any,
    _: UiRequest,
    __: re.Match[str],
) -> Any:
    snapshot = compatibility.runtime.business_snapshot(refresh=False)
    documents = snapshot.get("documents") or []
    ready_documents = [
        item for item in documents if item.get("parseState") in {"ready", "partial_ready"}
    ]
    return {
        "embedding": {
            "provider": "strict_document_versions",
            "model": "",
            "dimension": None,
            "signature": None,
            "ready": False,
            "error": (
                f"{len(ready_documents)} 份资料可供直接上下文使用；"
                "严格新版 schema 尚无向量索引权威对象"
            ),
        },
        "router": {
            "provider": "strict_project_knowledge_context",
            "model": "",
            "ready": bool(ready_documents),
            "error": None if ready_documents else "当前没有解析完成的知识文档",
        },
        "rerank": {"enabled": False, "provider": "none"},
        "shadowMode": False,
    }


@router.post(r"clients/([^/]+)/knowledge/search")
def search_knowledge(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    query = _string(request.body.get("prompt"))
    context = compatibility.runtime.project_knowledge_context(project_id)
    materials = [
        *(context.get("organizationSharedKnowledge") or []),
        *(context.get("localPrivateKnowledge") or []),
    ]
    terms = _search_terms(query)
    hits: list[dict[str, Any]] = []
    for item in materials:
        summary = _string(item.get("summary"))
        lowered = summary.lower()
        matched = [term for term in terms if term in lowered]
        if terms and not matched:
            continue
        hits.append(
            {
                "title": item.get("sourceDescription") or item.get("sourceId"),
                "excerpt": summary,
                "score": len(matched) / max(1, len(terms)),
                "stage": (
                    "master_index"
                    if item.get("sourceScope") == "organization_shared"
                    else "surrogate"
                ),
                "path": None,
                "sectionLabel": item.get("sourceDescription"),
                "matchedTerms": matched,
                "sourceType": "organization_summary",
            }
        )
    local_document_count = 0
    if hasattr(compatibility.runtime, "database_path"):
        store = LocalProjectMaterialsRepository(compatibility.runtime)
        local_documents = store.documents(project_id)
        local_document_count = len(local_documents)
        for document in local_documents:
            document_id = _string(document.get("id"))
            try:
                local = store.document_text(document_id)
            except LocalRuntimeError:
                continue
            content = _string(local.get("content"))
            lowered = content.lower()
            matched = [term for term in terms if term in lowered]
            if terms and not matched:
                continue
            first_position = min(
                (
                    lowered.find(term)
                    for term in matched
                    if lowered.find(term) >= 0
                ),
                default=0,
            )
            excerpt_start = max(0, first_position - 180)
            hits.append(
                {
                    "title": local.get("title") or document.get("title"),
                    "excerpt": content[
                        excerpt_start : excerpt_start + 720
                    ].strip(),
                    "score": len(matched) / max(1, len(terms)),
                    "stage": "raw_chunk",
                    "path": local.get("path"),
                    "sectionLabel": "当前设备本地正文",
                    "matchedTerms": matched,
                    "sourceType": "local_document",
                }
            )
    hits.sort(
        key=lambda item: (
            0 if item.get("sourceType") == "local_document" else 1,
            -float(item.get("score") or 0),
        )
    )
    return {
        "searchId": new_id(),
        "clientId": project_id,
        "query": query,
        "coverage": round(
            len(hits) / max(1, len(materials) + local_document_count),
            3,
        ),
        "matchedTerms": sorted(
            {term for item in hits for term in item.get("matchedTerms") or []}
        ),
        "masterHitCount": sum(1 for item in hits if item["stage"] == "master_index"),
        "surrogateHitCount": sum(1 for item in hits if item["stage"] == "surrogate"),
        "rawChunkHitCount": sum(
            1 for item in hits if item["stage"] == "raw_chunk"
        ),
        "drillthroughUsed": any(
            item["stage"] == "raw_chunk" for item in hits
        ),
        "phase": "completed",
        "progress": 100,
        "progressFloor": 100,
        "progressCeiling": 100,
        "lastUpdatedAt": _now(),
        "hits": hits,
        "previewSummary": "；".join(item["excerpt"][:120] for item in hits[:3]),
        "strictState": context.get("state"),
    }


@router.get(r"clients/([^/]+)/page-context")
def page_context(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    context = compatibility.runtime.project_knowledge_context(project_id)
    task_payload = compatibility.runtime.cloud_query("/api/v2/domain/tasks")
    related_tasks = [
        item
        for item in task_payload.get("tasks") or []
        if str(item.get("client_id") or item.get("clientId") or "") == project_id
    ]
    meetings = (
        LocalProjectMaterialsRepository(compatibility.runtime).meetings(project_id)
        if hasattr(compatibility.runtime, "database_path")
        else []
    )
    materials = [
        *(context.get("organizationSharedKnowledge") or []),
        *(context.get("officialWebsiteFacts") or []),
        *(context.get("localPrivateKnowledge") or []),
    ]
    related_documents = [
        {
            "id": item.get("sourceId"),
            "title": item.get("sourceDescription"),
            "summary": item.get("summary"),
            "sourceScope": item.get("sourceScope"),
            "sourceVersion": item.get("sourceVersion"),
            "contentHash": item.get("contentHash"),
        }
        for item in materials
    ]
    state_payload = context.get("state")
    state = (
        state_payload.get("overall")
        if isinstance(state_payload, Mapping)
        else str(state_payload or "ready")
    )
    official_judgments = [
        {
            "id": item.get("sourceId"),
            "title": item.get("sourceDescription") or "官网事实",
            "summary": item.get("summary") or "",
            "status": item.get("verificationState") or "verified",
            "authorityLevel": "confirmed",
            "sourceUrl": item.get("sourceUrl"),
            "updatedAt": item.get("updatedAt"),
        }
        for item in context.get("officialWebsiteFacts") or []
    ]
    return {
        "page": request.query.get("page") or "client_workspace",
        "scopeType": "client",
        "scopeId": project_id,
        "clientId": project_id,
        "intent": "overview",
        "officialJudgments": official_judgments,
        "candidateJudgments": [],
        "overlayJudgments": [],
        "evidenceCards": [],
        "rawEvidence": [],
        "openQuestions": [],
        "conflicts": [],
        "themeClusters": [],
        "relatedTasks": related_tasks,
        "relatedMeetings": meetings,
        "relatedDocuments": related_documents,
        "memoryFacts": [
            _string(item.get("summary"))[:400]
            for item in context.get("savedMemories") or []
        ],
        "missingContext": (
            [] if state in {"ready", "empty"} else ["部分项目知识源当前不可用"]
        ),
        "boundaryNotes": [
            "仅使用严格新版权威摘要；未读取源文件路径或未保存正文。",
            "判断与主题来自已保存叙事/判断权威；会议原文仍仅在当前成员本机。",
        ],
        "sourceSummary": {
            "documents": len(related_documents),
            "tasks": len(related_tasks),
            "answers": len(context.get("savedMemories") or []),
            "reports": 0,
        },
        "answerPolicy": {
            "canAnswer": state in {"ready", "empty"},
            "answerLevel": "evidence",
            "mustDiscloseCandidateBoundary": True,
            "mustUseRawEvidence": False,
            "shouldCreateProposal": False,
            "fallbackToLegacyRetrieval": False,
            "reason": "严格新版项目知识上下文",
        },
        "retrievalPlan": {"source": "ProjectKnowledgeContext", "rawEvidence": False},
        "quality": {
            "stateObjectCount": len(related_documents),
            "approvedJudgmentCount": len(official_judgments),
            "candidateJudgmentCount": 0,
            "evidenceCardCount": 0,
            "rawEvidenceCount": 0,
            "openQuestionCount": 0,
            "taskCount": len(related_tasks),
            "meetingCount": len(meetings),
            "contextQuality": "medium" if related_documents else "low",
            "canUseAnalysisFirst": bool(related_documents),
            "mustFallbackToLegacy": False,
        },
        "strictKnowledgeContext": context,
    }


@router.get(r"clients/([^/]+)/agent-state")
def agent_state(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    workspace = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/workspace",
    )
    status = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/analysis-status",
    )
    insights = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/insights",
    )
    return {
        "client_id": project_id,
        "client_profile": workspace.get("project"),
        "active_projects": [workspace.get("project")],
        "latest_events": workspace.get("eventLines") or [],
        "file_identities": workspace.get("documents") or [],
        "contract_structures": workspace.get("reports") or [],
        "historical_reference_links": [],
        "commitments": workspace.get("tasks") or [],
        "risk_signals": [
            item
            for item in status.get("attempts") or []
            if item.get("state") == "failed"
        ],
        "clarifications": insights.get("openQuestions") or [],
        "approval_queue": insights.get("judgments") or [],
        "data_gaps": insights.get("conflicts") or [],
        "agent_run_logs": status.get("attempts") or [],
        "recommended_next_actions": [
            item
            for item in workspace.get("tasks") or []
            if item.get("lifecycleState") in {"todo", "in_progress"}
        ][:10],
        "evidence_summary": status.get("counts") or {},
        "used_tables": [
            "clients",
            "knowledge_documents",
            "processing_attempts",
            "narrative_outputs",
            "ai_answers",
        ],
    }


@router.get(r"clients/([^/]+)/data-gaps")
def data_gaps(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    status = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/knowledge-status",
    )
    items = []
    if not (status.get("documents") or []):
        items.append(
            {
                "gap_id": f"{project_id}:no-material",
                "gap_type": "missing_project_material",
                "subject": "项目资料",
                "description": "当前项目尚无严格新版知识文档",
                "missing_evidence": ["knowledge_document"],
                "suggested_tools": ["项目资料导入"],
                "priority": "high",
                "severity": "high",
                "status": "open",
                "approval_required": False,
            }
        )
    for document in status.get("documents") or []:
        if document.get("parseState") not in {"failed", "missing_source"}:
            continue
        items.append(
            {
                "gap_id": f"{project_id}:{document.get('documentId')}",
                "gap_type": document.get("parseState"),
                "subject": document.get("title"),
                "description": "资料尚未形成可用文档版本",
                "missing_evidence": [document.get("documentId")],
                "suggested_tools": ["资料处理重试"],
                "priority": "high",
                "severity": "high",
                "status": "open",
                "approval_required": False,
            }
        )
    return {
        "client_id": project_id,
        "total": len(items),
        "items": items,
        "schema_version": "strict-workbench-v1",
    }


@router.get(r"clients/([^/]+)/clarification-context")
def clarification_context(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    workspace = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/workspace",
    )
    project = workspace.get("project") or {}
    documents = workspace.get("documents") or []
    failed_documents = [
        item
        for item in documents
        if item.get("parseState") in {"failed", "missing_source"}
    ]
    event_lines = list(workspace.get("eventLines") or [])
    tasks = list(workspace.get("tasks") or [])
    timeline = [
        {
            "id": f"event-line:{item.get('eventLineId')}",
            "eventLineId": item.get("eventLineId") or "",
            "eventLineName": item.get("name") or "",
            "happenedAt": item.get("updatedAt") or item.get("createdAt") or "",
            "sourceType": "event_line_record",
            "actorName": "",
            "title": item.get("name") or "",
            "summary": item.get("background") or item.get("goal") or "",
            "isKey": item.get("lifecycleState") in {"completed", "paused"},
        }
        for item in event_lines
    ]
    people: dict[str, dict[str, Any]] = {}
    for task in tasks:
        for collaborator in task.get("collaborators") or []:
            name = _string(collaborator.get("displayName"))
            if not name:
                continue
            person = people.setdefault(
                name,
                {"name": name, "mentionCount": 0, "sources": []},
            )
            person["mentionCount"] += 1
            source = f"task:{task.get('taskId')}"
            if source not in person["sources"]:
                person["sources"].append(source)
    commitments = [
        {
            "id": item.get("taskId") or "",
            "title": item.get("title") or "",
            "ownerName": next(
                (
                    collaborator.get("displayName") or ""
                    for collaborator in item.get("collaborators") or []
                    if collaborator.get("role") == "owner"
                ),
                "",
            ),
            "dueDate": item.get("dueDate") or item.get("deadlineAt") or "",
            "confidence": 1,
            "publishStatus": item.get("lifecycleState") or "todo",
            "meetingId": "",
            "meetingTitle": "",
            "meetingScheduledAt": "",
            "createdAt": item.get("createdAt") or "",
        }
        for item in tasks
        if item.get("lifecycleState") not in {"archived"}
    ]
    return {
        "clientId": project_id,
        "profile": {
            "name": project.get("name") or "",
            "alias": project.get("alias") or "",
            "domain": project.get("domain") or "",
            "type": "project",
            "intro": project.get("summary") or "",
            "stage": project.get("lifecycleState") or "",
            "color": project.get("color") or "",
            "industry": "",
            "scale": "",
            "influence": "",
            "currentNeeds": "",
            "painPoints": "",
            "strategicValueToYiyu": "",
            "decisionChain": "",
            "cooperationType": "",
            "relationshipHealth": "",
            "milestones": "",
            "cooperationStartedAt": project.get("createdAt") or "",
        },
        "eventLines": [
            {
                "id": item.get("eventLineId"),
                "name": item.get("name") or "",
                "kind": "event_line",
                "status": item.get("lifecycleState") or "",
                "stage": item.get("lifecycleState") or "",
                "summary": item.get("background") or "",
                "intent": item.get("goal") or "",
                "nextStep": "",
                "currentBlocker": (
                    item.get("background") or ""
                    if item.get("lifecycleState") == "paused"
                    else ""
                ),
                "recentDecision": "",
                "businessCategory": project.get("domain") or "",
                "ownerId": item.get("createdByMembershipId") or "",
                "ownerName": "",
                "evidenceCount": int(item.get("attachmentCount") or 0),
                "createdAt": item.get("createdAt") or "",
                "updatedAt": item.get("updatedAt") or "",
                "closedAt": (
                    item.get("updatedAt") or ""
                    if item.get("lifecycleState") in {"completed", "archived"}
                    else ""
                ),
                "isDirtyName": False,
            }
            for item in event_lines
        ],
        "timeline": timeline,
        "peopleCandidates": list(people.values()),
        "commitments": commitments,
        "clarificationNeeds": [
            {
                "eventLineId": "",
                "eventLineName": project.get("name") or "",
                "missingFields": [
                    f"资料《{item.get('title') or item.get('documentId')}》尚不可用"
                    for item in failed_documents
                ],
                "predictionReadiness": max(
                    0,
                    100 - len(failed_documents) * 20,
                ),
                "confidence": 0.5 if failed_documents else 1,
                "updatedAt": project.get("updatedAt") or _now(),
            }
        ]
        if failed_documents
        else [],
        "generatedAt": _now(),
        "strictResourceStates": {
            "profile": "ready",
            "eventLines": (
                "ready" if event_lines else "empty"
            ),
            "documents": "ready" if documents else "empty",
            "timeline": "ready" if timeline else "empty",
            "peopleCandidates": "ready" if people else "empty",
            "commitments": "ready" if commitments else "empty",
        },
    }


@router.get(r"clients/([^/]+)/runtime-run-logs")
def runtime_run_logs(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    status = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/analysis-status",
    )
    return [
        {
            "id": item.get("processingAttemptId"),
            "clientId": project_id,
            "jobId": item.get("processingAttemptId"),
            "analysisJobId": item.get("processingAttemptId"),
            "stageRunId": None,
            "contextPackId": None,
            "judgmentVersionId": None,
            "correlationId": None,
            "provider": "strict_processing_attempt",
            "model": None,
            "lane": "local_deep",
            "cacheHit": False,
            "degraded": item.get("state") in {"partial", "failed"},
            "documentCount": 1 if item.get("documentId") else 0,
            "evidenceCount": int((status.get("counts") or {}).get("evidenceLinks") or 0),
            "conflictCount": 0,
            "contextTimeRange": None,
            "promptVersion": None,
            "schemaVersion": "strict-cloud-v1",
            "summary": item.get("processingKind") or "严格新版处理尝试",
            "detail": {
                "state": item.get("state"),
                "attemptNo": item.get("attemptNo"),
                "errorCode": item.get("errorCode"),
                "errorMessage": item.get("errorMessage"),
            },
            "createdAt": item.get("createdAt"),
        }
        for item in status.get("attempts") or []
    ]


@router.get(r"clients/([^/]+)/data-center/mobile-snapshot")
def mobile_data_center_snapshot(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    workspace = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/workspace",
    )
    status = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/knowledge-status",
    )
    insights = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/insights",
    )
    proposals = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/proposal-drafts",
    )
    meetings = (
        LocalProjectMaterialsRepository(compatibility.runtime).meetings(project_id)
        if hasattr(compatibility.runtime, "database_path")
        else []
    )
    ready_documents = int((status.get("counts") or {}).get("ready") or 0)
    return {
        "clientId": project_id,
        "latestContextPack": {
            "project": workspace.get("project"),
            "documents": status.get("documents") or [],
            "reports": workspace.get("reports") or [],
            "answers": workspace.get("answers") or [],
        },
        "latestJudgments": insights.get("judgments") or [],
        "openQuestions": insights.get("openQuestions") or [],
        "conflicts": insights.get("conflicts") or [],
        "relatedTasks": workspace.get("tasks") or [],
        "recentMeetings": meetings,
        "stateProjection": {
            "processingAttempts": status.get("processingAttempts") or [],
            "eventLines": workspace.get("eventLines") or [],
        },
        "proposalDraftSummary": {
            "total": len(proposals),
            "items": proposals,
        },
        "openProposalSummary": {
            "total": sum(
                1
                for item in proposals
                if item.get("status") in {"draft", "reviewed"}
            ),
            "items": [
                item
                for item in proposals
                if item.get("status") in {"draft", "reviewed"}
            ],
        },
        "latestExecutionTickets": [],
        "evidenceQualitySummary": {
            "reports": len(workspace.get("reports") or []),
            "answers": len(workspace.get("answers") or []),
        },
        "kernelReadiness": (
            "ready"
            if ready_documents and workspace.get("reports")
            else "partial"
            if ready_documents
            else "weak"
        ),
        "generatedAt": status.get("generatedAt") or _now(),
        "strictResourceStates": {
            "projectContext": "ready",
            "tasks": "ready" if workspace.get("tasks") else "empty",
            "meetings": "ready" if meetings else "empty",
            "judgments": (
                "ready" if insights.get("judgments") else "empty"
            ),
            "openQuestions": (
                "ready" if insights.get("openQuestions") else "empty"
            ),
            "conflicts": (
                "ready" if insights.get("conflicts") else "empty"
            ),
            "proposals": "ready" if proposals else "empty",
        },
    }


@router.get(r"clients/([^/]+)/next-steps")
def next_steps(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    workspace = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/workspace",
    )
    meeting_actions = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/meeting-action-items",
    )
    items = [
        {
            "fingerprint": task.get("taskId"),
            "kind": "task",
            "actor": task.get("ownerMembershipId") or "",
            "text": task.get("title") or "",
            "dueDate": task.get("dueDate") or task.get("deadlineAt") or "",
            "severity": task.get("priority") or "medium",
            "rawId": task.get("taskId"),
            "ownerSide": "us",
            "actionDirection": "do",
            "mergedCount": 1,
            "matchedTaskTitle": task.get("title"),
        }
        for task in workspace.get("tasks") or []
        if task.get("lifecycleState") not in {"completed", "cancelled", "archived"}
    ]
    for candidate in [
        *(meeting_actions.get("high") or []),
        *(meeting_actions.get("medium") or []),
    ]:
        items.append(
            {
                "fingerprint": candidate.get("fingerprint") or candidate.get("proposalId"),
                "kind": "meeting_action",
                "actor": candidate.get("actor") or "",
                "text": candidate.get("text") or "",
                "dueDate": candidate.get("dueDate") or "",
                "severity": "medium",
                "rawId": candidate.get("proposalId") or candidate.get("fingerprint"),
                "ownerSide": "us",
                "actionDirection": "confirm",
                "mergedCount": 1,
                "matchedTaskTitle": None,
                "description": candidate.get("description") or "",
                "confirmationState": "pending_confirmation",
            }
        )
    return {
        "clientId": project_id,
        "items": items,
        "total": len(items),
        "consumedCount": 0,
        "possibleDuplicates": [],
        "needsReview": [],
        "matchedExistingCount": len(items),
        "invalidFilteredCount": 0,
        "debugSummary": {"strictTaskRecords": len(items)},
    }


@router.post(r"clients/([^/]+)/next-steps-background")
def next_step_background(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    workspace = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/workspace",
    )
    fingerprint = _string(request.body.get("fingerprint"))
    task = next(
        (
            item
            for item in workspace.get("tasks") or []
            if item.get("taskId") == fingerprint
        ),
        None,
    )
    if task is None:
        return {
            "background": "",
            "sourceLabel": "严格新版任务",
            "hasSource": False,
            "fromCache": False,
            "strictState": "empty",
        }
    return {
        "background": task.get("description") or "",
        "sourceLabel": task.get("title") or "严格新版任务",
        "hasSource": bool(task.get("description")),
        "fromCache": False,
        "strictState": "ready" if task.get("description") else "empty",
    }


@router.get(r"clients/([^/]+)/todos/unified")
def unified_todos(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    workspace = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/workspace",
    )
    todos = [
        {
            "id": task.get("taskId"),
            "source": "task",
            "title": task.get("title") or "",
            "owner": task.get("ownerMembershipId") or "",
            "due_date": task.get("dueDate") or task.get("deadlineAt") or "",
            "status": task.get("lifecycleState"),
            "direction": "do",
            "related_to": project_id,
            "raw_id": task.get("taskId"),
            "severity": task.get("priority") or "medium",
            "description": task.get("description") or "",
        }
        for task in workspace.get("tasks") or []
        if task.get("lifecycleState") not in {"completed", "cancelled", "archived"}
    ]
    return {
        "todos": todos,
        "total": len(todos),
        "by_source": {"task": len(todos), "meeting_action": 0, "commitment": 0},
        "by_severity": {
            level: sum(1 for item in todos if item["severity"] == level)
            for level in ("high", "medium", "low")
        },
    }


@router.get(r"clients/([^/]+)/strategic-pulse")
def strategic_pulse(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    workspace = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/workspace",
    )
    return {
        "clientId": project_id,
        "weekStart": "",
        "weekEnd": "",
        "weeklyEvents": [
            {
                "title": item.get("name"),
                "occurredAt": item.get("updatedAt"),
                "impact": (
                    "block"
                    if item.get("lifecycleState") == "paused"
                    else "advance"
                    if item.get("lifecycleState") == "completed"
                    else "neutral"
                ),
                "sourceType": "event_line",
                "sourceId": item.get("eventLineId"),
                "sourceLabel": item.get("name"),
            }
            for item in workspace.get("eventLines") or []
        ],
        "upcomingTodos": [
            {
                "title": item.get("title"),
                "dueDate": item.get("dueDate") or item.get("deadlineAt"),
                "daysUntilDue": None,
                "urgency": "later",
                "sourceTaskId": item.get("taskId"),
                "eventLineId": item.get("eventLineId"),
                "eventLineName": "",
            }
            for item in workspace.get("tasks") or []
            if item.get("lifecycleState") not in {"completed", "cancelled", "archived"}
        ],
        "currentBlockers": [
            {
                "title": item.get("name"),
                "reason": item.get("background") or "事件线已暂停",
                "stuckDays": 0,
                "eventLineId": item.get("eventLineId"),
                "suggestedAction": "确认阻塞原因与下一步",
            }
            for item in workspace.get("eventLines") or []
            if item.get("lifecycleState") == "paused"
        ],
        "generatedAt": _now(),
    }


@router.get(r"clients/([^/]+)/workspace/context-refresh-events")
def context_refresh_events(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    workspace = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/workspace",
    )
    limit = max(1, min(200, int(request.query.get("limit") or 60)))
    active_only = request.query.get("activeOnly") in {"1", "true"}
    items = [
        {
            "id": item.get("processingAttemptId"),
            "clientId": match.group(1),
            "sourceType": item.get("processingKind"),
            "sourceId": item.get("documentId") or item.get("sourceAssetId"),
            "reason": item.get("processingKind"),
            "scopeType": "client",
            "scopeId": match.group(1),
            "priority": "normal",
            "status": item.get("state"),
            "createdAt": item.get("createdAt"),
            "updatedAt": item.get("finishedAt")
            or item.get("startedAt")
            or item.get("createdAt"),
        }
        for item in workspace.get("processingAttempts") or []
        if "context" in _string(item.get("processingKind")).lower()
        and (
            not active_only
            or item.get("state") in {"queued", "processing"}
        )
    ]
    return items[:limit]


@router.get(r"clients/([^/]+)/workspace/data-center-readiness")
def data_center_readiness(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    status = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/knowledge-status",
    )
    counts = status.get("counts") or {}
    return {
        "clientId": match.group(1),
        "state": status.get("state"),
        "ready": int(counts.get("ready") or 0) > 0,
        "summary": counts,
        "documents": status.get("documents") or [],
        "processingAttempts": status.get("processingAttempts") or [],
        "blockedReasons": (
            ["尚无解析完成的严格新版资料"]
            if int(counts.get("ready") or 0) == 0
            else []
        ),
        "generatedAt": status.get("generatedAt"),
    }


def _library_upsert(
    compatibility: Any,
    request: UiRequest,
    *,
    kind: str,
    item_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {**request.body, **(extra or {})}
    if item_id:
        path = f"/api/v2/workbench/libraries/{kind}/{item_id}"

        def payload_factory() -> dict[str, Any]:
            current = _cloud_query(compatibility, path)
            return {**payload, "expectedVersion": current.get("version")}

        return _replayable_workbench_mutation(
            compatibility,
            request,
            "PUT",
            path,
            aggregate_type="automation_rule",
            aggregate_id=item_id,
            payload_factory=payload_factory,
        )
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/libraries/{kind}",
        payload,
    )


def _delete_library(
    compatibility: Any,
    request: UiRequest,
    *,
    kind: str,
    item_id: str,
) -> dict[str, Any]:
    path = f"/api/v2/workbench/libraries/{kind}/{item_id}"

    def payload_factory() -> dict[str, Any]:
        try:
            current = _cloud_query(compatibility, path)
        except LocalRuntimeError as exc:
            if exc.status_code != 404:
                raise
            current = {"version": 1}
        return {"expectedVersion": current.get("version")}

    return _replayable_workbench_mutation(
        compatibility,
        request,
        "DELETE",
        path,
        aggregate_type="automation_rule",
        aggregate_id=item_id,
        payload_factory=payload_factory,
    )


def _project_text_save(
    compatibility: Any,
    request: UiRequest,
    *,
    project_id: str,
    key: str,
    title: str,
    markdown: str,
) -> dict[str, Any]:
    path = f"/api/v2/workbench/projects/{project_id}/texts/{key}"

    def payload_factory() -> dict[str, Any]:
        items = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/texts",
        )
        current = items.get(key) or {}
        return {
            "title": title,
            "markdownContent": markdown,
            "expectedVersion": int(current.get("version") or 0),
        }

    return _replayable_workbench_mutation(
        compatibility,
        request,
        "PUT",
        path,
        aggregate_type="project_text",
        aggregate_id=f"{project_id}:{key}",
        payload_factory=payload_factory,
    )


@router.get(r"analysis/jobs/([^/]+)")
def get_analysis_job(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/analysis-jobs/{match.group(1)}",
    )


@router.get(r"analysis/jobs/([^/]+)/stages")
def get_analysis_job_stages(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/analysis-jobs/{match.group(1)}/stages",
    )


@router.post(r"analysis/jobs")
def create_analysis_job(
    compatibility: Any,
    request: UiRequest,
    _: re.Match[str],
) -> Any:
    project_id = _string(request.body.get("clientId"))
    question = _string(request.body.get("question"))
    if not project_id:
        raise LocalRuntimeError(422, "analysis_project_required", "分析任务缺少固定项目 WorkspaceContext")
    if not question:
        question = (
            f"请基于当前项目严格权威资料执行"
            f"{_string(request.body.get('jobType')) or '项目分析'}，"
            "给出结论、证据边界与下一步。"
        )
    saved = compatibility.runtime.workbench_chat(
        project_id=project_id,
        question=question,
        mode="balanced",
        source_manifest_extra={
            "operationKey": f"{request.idempotency_key}:analysis-answer",
            "workbenchKind": "analysis_job",
        },
        idempotency_key=f"{request.idempotency_key}:analysis-answer",
    )
    answer = saved.get("answer") or {}
    return compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/workbench/analysis-jobs",
        payload={
            "answerId": answer.get("answerId"),
            "projectId": project_id,
            "jobType": request.body.get("jobType") or "evidence_extract",
        },
        idempotency_key=f"{request.idempotency_key}:analysis-job",
    )


@router.get(r"clients/([^/]+)/(judgments|topics|conflicts|open-questions)")
def project_insight_collection(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    key = {
        "judgments": "judgments",
        "topics": "topics",
        "conflicts": "conflicts",
        "open-questions": "openQuestions",
    }[match.group(2)]
    result = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/insights",
    )
    return result[key]


@router.get(r"clients/([^/]+)/project-structure")
def get_project_structure(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/structure",
    )


@router.get(r"clients/([^/]+)/project-modules/([^/]+)")
def get_project_module_detail(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    structure = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/structure",
    )
    module = next(
        (
            item
            for item in structure.get("modules") or []
            if item.get("id") == match.group(2)
        ),
        None,
    )
    if module is None:
        raise LocalRuntimeError(404, "project_module_missing", "项目模块不存在")
    flows = [
        item
        for item in structure.get("flows") or []
        if item.get("moduleId") == module.get("id")
    ]
    return {
        **module,
        "relatedTaskIds": [item.get("id") for item in flows],
        "relatedTaskTitles": [item.get("name") for item in flows],
        "flowIds": [item.get("id") for item in flows],
        "flowNames": [item.get("name") for item in flows],
        "contextSummary": module.get("description") or module.get("goal") or "",
    }


@router.get(r"clients/([^/]+)/project-flows/([^/]+)")
def get_project_flow_detail(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    structure = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/structure",
    )
    flow = next(
        (
            item
            for item in structure.get("flows") or []
            if item.get("id") == match.group(2)
        ),
        None,
    )
    if flow is None:
        raise LocalRuntimeError(404, "project_flow_missing", "项目流程不存在")
    return {
        **flow,
        "relatedTaskIds": [flow.get("id")],
        "relatedTaskTitles": [flow.get("name")],
        "contextSummary": flow.get("description") or "",
    }


@router.post(r"clients/([^/]+)/goals")
def create_project_goal(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    title = _string(request.body.get("title"))
    if not title:
        raise LocalRuntimeError(422, "goal_title_required", "请输入目标标题")
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/goals",
        request.body,
    )


@router.post(r"clients/([^/]+)/dna")
def upsert_dna_term(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    canonical_name = _string(request.body.get("canonicalName"))
    if not canonical_name:
        raise LocalRuntimeError(422, "dna_name_required", "请输入 DNA 术语")
    project_id = match.group(1)
    items = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/texts",
    )
    existing_key = next(
        (
            key
            for key, item in items.items()
            if key.startswith("dna_term:")
            and _string(item.get("title")) == canonical_name
        ),
        None,
    )
    key = existing_key or f"dna_term:{new_id()}"
    term_payload = {
        "category": _string(request.body.get("category")),
        "canonicalName": canonical_name,
        "aliases": list(request.body.get("aliases") or []),
        "description": _string(request.body.get("description")),
    }
    saved = _project_text_save(
        compatibility,
        request,
        project_id=project_id,
        key=key,
        title=canonical_name,
        markdown=json.dumps(term_payload, ensure_ascii=False, sort_keys=True),
    )
    return {
        "id": saved.get("documentId"),
        "clientId": project_id,
        **term_payload,
        "sourceLevel": "client",
        "version": saved.get("version"),
    }


@router.get(r"clients/([^/]+)/brand-proposition")
def get_brand_proposition(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    items = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/texts",
    )
    item = items.get("brand_proposition")
    if item is None:
        raise LocalRuntimeError(404, "brand_proposition_missing", "当前项目尚未建立品牌主张")
    return {
        "clientId": match.group(1),
        "brandProposition": item.get("markdownContent") or "",
        "updatedAt": item.get("updatedAt"),
        "version": item.get("version"),
    }


@router.patch(r"clients/([^/]+)/brand-proposition")
def update_brand_proposition(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    saved = _project_text_save(
        compatibility,
        request,
        project_id=match.group(1),
        key="brand_proposition",
        title="品牌主张",
        markdown=_string(request.body.get("brandProposition")),
    )
    return {
        "clientId": match.group(1),
        "brandProposition": saved.get("markdownContent"),
        "updatedAt": saved.get("updatedAt"),
        "version": saved.get("version"),
    }


@router.get(r"clients/([^/]+)/strategic-docs")
def get_strategic_docs(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    items = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/texts",
    )

    def entry(key: str) -> dict[str, Any] | None:
        item = items.get(key)
        if item is None:
            return None
        return {
            "fileName": item.get("title"),
            "mdContent": item.get("markdownContent"),
            "uploadedAt": item.get("updatedAt"),
            "uploadedBy": "strict_organization_member",
            "version": item.get("version"),
        }

    strategy = entry("strategic_doc:strategy")
    methodology = entry("strategic_doc:methodology")
    return {
        "clientId": match.group(1),
        "strategy": strategy,
        "methodology": methodology,
        "hasStrategy": strategy is not None,
        "hasMethodology": methodology is not None,
    }


@router.post(r"clients/([^/]+)/strategic-docs")
def save_strategic_doc(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    doc_type = _string(request.body.get("docType"))
    if doc_type not in {"strategy", "methodology"}:
        raise LocalRuntimeError(422, "strategic_doc_type_invalid", "战略文档类型无效")
    saved = _project_text_save(
        compatibility,
        request,
        project_id=match.group(1),
        key=f"strategic_doc:{doc_type}",
        title=_string(request.body.get("fileName")) or "战略文档",
        markdown=_string(request.body.get("mdContent")),
    )
    return {
        "ok": True,
        "docType": doc_type,
        "fileName": saved.get("title"),
        "version": saved.get("version"),
    }


@router.delete(r"clients/([^/]+)/strategic-docs/([^/]+)")
def delete_strategic_doc(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    doc_type = match.group(2)
    if doc_type not in {"strategy", "methodology"}:
        raise LocalRuntimeError(422, "strategic_doc_type_invalid", "战略文档类型无效")
    project_id = match.group(1)
    path = f"/api/v2/workbench/projects/{project_id}/texts/strategic_doc:{doc_type}"

    def payload_factory() -> dict[str, Any]:
        items = _cloud_query(
            compatibility,
            f"/api/v2/workbench/projects/{project_id}/texts",
        )
        item = items.get(f"strategic_doc:{doc_type}")
        return {"expectedVersion": int((item or {}).get("version") or 1)}

    return _replayable_workbench_mutation(
        compatibility,
        request,
        "DELETE",
        path,
        aggregate_type="project_text",
        aggregate_id=f"{project_id}:strategic_doc:{doc_type}",
        payload_factory=payload_factory,
    )


@router.get(r"analysis-tools")
def analysis_tools(
    compatibility: Any,
    _: UiRequest,
    __: re.Match[str],
) -> Any:
    templates = _cloud_query(
        compatibility,
        "/api/v2/workbench/libraries/analysis_template",
    )
    snapshot = compatibility.runtime.business_snapshot(refresh=False)
    runs = [
        {
            "id": answer.get("answerId"),
            "templateId": (answer.get("sourceManifest") or {}).get("templateId") or "",
            "title": answer.get("question") or "组织 AI 分析",
            "inputText": answer.get("question") or "",
            "output": {
                "content": answer.get("answerMarkdown") or "",
                "judgment": _string(answer.get("answerMarkdown")).splitlines()[0]
                if _string(answer.get("answerMarkdown"))
                else "",
                "analysis": answer.get("answerMarkdown") or "",
                "actions": "",
                "timeline": "",
            },
            "parentRunId": None,
            "createdAt": answer.get("createdAt"),
            "status": "success",
        }
        for answer in snapshot.get("aiAnswers") or []
        if answer.get("lifecycleState") == "active"
    ]
    return {"templates": templates, "runs": runs}


_LIBRARY_COLLECTIONS = {
    "analysis-tools/fundraising/dna": "fundraising_dna",
    "analysis-tools/fundraising/cases": "fundraising_case",
    "analysis-tools/fundraising/reminders": "fundraising_reminder",
    "analysis-tools/fundraising/norms": "fundraising_norm",
}


def _lines(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_string(item) for item in value if _string(item)]
    return [
        line.strip(" -•\t")
        for line in _string(value).splitlines()
        if line.strip(" -•\t")
    ]


def _deep_dna_dto(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "id": item.get("id"),
        "groupKey": item.get("groupKey") or "platform_fundraising",
        "label": item.get("label") or item.get("title") or "募资对象",
        "status": item.get("status") or "published",
        "sourceKind": item.get("sourceKind") or "manual",
        "identitySummary": item.get("identitySummary") or "",
        "corePreferences": _lines(
            item.get("corePreferences") or item.get("corePreferencesText")
        ),
        "supportTriggers": _lines(
            item.get("supportTriggers") or item.get("supportTriggersText")
        ),
        "redFlags": _lines(item.get("redFlags") or item.get("redFlagsText")),
        "evidencePreferences": _lines(
            item.get("evidencePreferences") or item.get("evidencePreferencesText")
        ),
        "voiceStyle": _lines(item.get("voiceStyle") or item.get("voiceStyleText")),
        "commonQuestions": _lines(
            item.get("commonQuestions") or item.get("commonQuestionsText")
        ),
        "sources": list(item.get("sources") or []),
        "confidenceScore": float(item.get("confidenceScore") or 0),
        "confidenceLevel": item.get("confidenceLevel") or "low",
        "authorizationStatus": item.get("authorizationStatus") or "restricted",
        "rawContent": item.get("rawContent") or "",
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
    }


@router.get(
    r"(analysis-tools/fundraising/(?:dna|cases|reminders|norms))"
)
def fundraising_library(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    items = _cloud_query(
        compatibility,
        f"/api/v2/workbench/libraries/{_LIBRARY_COLLECTIONS[match.group(1)]}",
    )
    if match.group(1).endswith("/dna"):
        return [_deep_dna_dto(item) for item in items]
    return items


@router.post(
    r"(analysis-tools/fundraising/(?:dna|cases|reminders|norms))"
)
def save_fundraising_library(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    item_id = _string(request.body.get("id")) or None
    saved = _library_upsert(
        compatibility,
        request,
        kind=_LIBRARY_COLLECTIONS[match.group(1)],
        item_id=item_id,
    )
    return (
        _deep_dna_dto(saved)
        if match.group(1).endswith("/dna")
        else saved
    )


@router.post(r"analysis-tools/fundraising/dna/(manual|import)")
def create_fundraising_dna_source(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    saved = _library_upsert(
        compatibility,
        request,
        kind="fundraising_dna",
        extra={
            "sourceKind": match.group(1),
            "status": "published",
            "rawContent": _string(request.body.get("content")),
        },
    )
    return _deep_dna_dto(saved)


@router.post(r"analysis-tools/fundraising/dna/([^/]+)/publish")
def publish_fundraising_dna(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    current = _cloud_query(
        compatibility,
        f"/api/v2/workbench/libraries/fundraising_dna/{match.group(1)}",
    )
    saved = _library_upsert(
        compatibility,
        request,
        kind="fundraising_dna",
        item_id=match.group(1),
        extra={**current, "status": "published"},
    )
    return _deep_dna_dto(saved)


def _handbook_dto(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "id": item.get("id"),
        "title": item.get("title") or "",
        "summary": item.get("summary") or "",
        "tags": list(item.get("tags") or []),
        "sourceType": item.get("sourceType") or "organization_knowledge_document",
        "abilityKeys": list(item.get("abilityKeys") or []),
        "evidenceRefs": list(item.get("evidenceRefs") or []),
        "contextSummary": item.get("contextSummary") or "",
        "reuseCount": int(item.get("reuseCount") or 0),
        "linkedContexts": list(item.get("linkedContexts") or []),
        "createdAt": item.get("createdAt"),
    }


@router.get(r"handbook")
def handbook(
    compatibility: Any,
    _: UiRequest,
    __: re.Match[str],
) -> Any:
    items = _cloud_query(
        compatibility,
        "/api/v2/workbench/libraries/handbook",
    )
    return {"entries": [_handbook_dto(item) for item in items]}


@router.get(r"handbook/([^/]+)")
def handbook_entry(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    item = _cloud_query(
        compatibility,
        f"/api/v2/workbench/libraries/handbook/{match.group(1)}",
    )
    return {
        **_handbook_dto(item),
        "relatedLedgerEntries": [],
        "originContexts": item.get("linkedContexts") or [],
        "reuseHistory": [],
    }


@router.post(r"handbook")
def create_handbook_entry(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    saved = _library_upsert(
        compatibility,
        request,
        kind="handbook",
    )
    return _handbook_dto(saved)


def _writing_skill_dto(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "id": item.get("id"),
        "name": item.get("name") or item.get("title") or "",
        "description": item.get("description") or "",
        "distilledMd": item.get("distilledMd") or "",
        "isBuiltin": False,
        "sortOrder": int(item.get("sortOrder") or 0),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
    }


@router.get(r"writing-skills")
def writing_skills(
    compatibility: Any,
    _: UiRequest,
    __: re.Match[str],
) -> Any:
    items = _cloud_query(
        compatibility,
        "/api/v2/workbench/libraries/writing_skill",
    )
    return [_writing_skill_dto(item) for item in items]


@router.post(r"writing-skills")
def create_writing_skill(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    saved = _library_upsert(
        compatibility,
        request,
        kind="writing_skill",
    )
    return _writing_skill_dto(saved)


@router.get(r"agent-skills")
def agent_skills(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    query = {
        "agentKind": _string(request.query.get("agentKind")) or "project_workspace",
        "enabledOnly": _string(request.query.get("enabledOnly")) or "true",
    }
    result = _cloud_query(compatibility, "/api/v2/agent-skills", query=query)
    items = result.get("items") or []
    for item in items:
        compatibility.runtime.project_agent_skill(item)
    if query["enabledOnly"].lower() == "false":
        compatibility.runtime.reconcile_agent_skill_projections(
            [str(item.get("skillId") or "") for item in items]
        )
    return items


@router.post(r"agent-skills")
def create_agent_skill(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    saved = _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/agent-skills",
        request.body,
    )
    compatibility.runtime.project_agent_skill(saved)
    return saved


@router.patch(r"agent-skills/([^/]+)")
def update_agent_skill(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    saved = _cloud_command(
        compatibility,
        request,
        "PATCH",
        f"/api/v2/agent-skills/{match.group(1)}",
        request.body,
    )
    compatibility.runtime.project_agent_skill(saved)
    return saved


@router.patch(r"agent-skills/([^/]+)/enabled")
def set_agent_skill_enabled(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    saved = _cloud_command(
        compatibility,
        request,
        "PATCH",
        f"/api/v2/agent-skills/{match.group(1)}/enabled",
        request.body,
    )
    compatibility.runtime.project_agent_skill(saved)
    return saved


@router.delete(r"agent-skills/([^/]+)")
def delete_agent_skill(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    saved = _cloud_command(
        compatibility,
        request,
        "DELETE",
        f"/api/v2/agent-skills/{match.group(1)}",
        request.body,
    )
    current = _cloud_query(
        compatibility,
        "/api/v2/agent-skills",
        query={"agentKind": "project_workspace", "enabledOnly": "false"},
    )
    compatibility.runtime.reconcile_agent_skill_projections(
        [str(item.get("skillId") or "") for item in current.get("items") or []]
    )
    return saved


@router.post(r"agent-skills/([^/]+)/delete")
def delete_agent_skill_command(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    saved = _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/agent-skills/{match.group(1)}/delete",
        request.body,
    )
    current = _cloud_query(
        compatibility,
        "/api/v2/agent-skills",
        query={"agentKind": "project_workspace", "enabledOnly": "false"},
    )
    compatibility.runtime.reconcile_agent_skill_projections(
        [str(item.get("skillId") or "") for item in current.get("items") or []]
    )
    return saved


@router.put(r"writing-skills/([^/]+)")
def update_writing_skill(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    saved = _library_upsert(
        compatibility,
        request,
        kind="writing_skill",
        item_id=match.group(1),
    )
    return _writing_skill_dto(saved)


@router.delete(r"writing-skills/([^/]+)")
def delete_writing_skill(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _delete_library(
        compatibility,
        request,
        kind="writing_skill",
        item_id=match.group(1),
    )


@router.get(r"reports/([^/]+)")
def get_report_run(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    if hasattr(compatibility.runtime, "database_path"):
        try:
            draft = LocalProjectMaterialsRepository(
                compatibility.runtime
            ).report_draft(match.group(1))
            _require_project_read(compatibility, _string(draft.get("client_id")))
            return draft
        except LocalRuntimeError as exc:
            if exc.code != "report_draft_missing":
                raise
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/report-runs/{match.group(1)}",
    )


@router.patch(r"reports/([^/]+)/blueprint")
def update_report_blueprint(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    store = LocalProjectMaterialsRepository(compatibility.runtime)
    draft = store.report_draft(match.group(1))
    _require_project_read(compatibility, _string(draft.get("client_id")))
    blueprint = request.body.get("blueprint") or {}
    if not isinstance(blueprint, Mapping):
        raise LocalRuntimeError(422, "report_blueprint_invalid", "报告蓝图格式无效")
    sections = list(blueprint.get("sections") or [])
    return store.save_report_draft(
        _string(draft.get("client_id")),
        {
            **draft,
            "blueprint": dict(blueprint),
            "status": "blueprint_confirmed",
            "sections": [None for _ in sections],
            "sections_status": ["pending" for _ in sections],
            "body_markdown": "",
        },
    )


@router.post(r"reports/([^/]+)/save")
def save_report_run(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    store = LocalProjectMaterialsRepository(compatibility.runtime)
    draft = store.report_draft(match.group(1))
    _require_project_read(compatibility, _string(draft.get("client_id")))
    if draft.get("status") == "saved":
        return draft
    if draft.get("status") != "body_ready":
        raise LocalRuntimeError(
            409,
            "report_body_not_ready",
            "报告正文尚未完整生成，不能保存为组织共享版本",
        )
    blueprint = draft.get("blueprint") or {}
    saved = _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/workbench/reports",
        {
            "reportId": draft["id"],
            "projectId": draft["client_id"],
            "eventLineId": draft.get("event_line_id"),
            "title": request.body.get("title")
            or blueprint.get("title")
            or "项目报告",
            "outputKind": blueprint.get("report_kind") or "strategy_report",
            "contentMarkdown": draft.get("body_markdown") or "# 报告",
            "contentJson": {
                "blueprint": blueprint,
                "sections": draft.get("sections") or [],
                "period_start": draft.get("period_start"),
                "period_end": draft.get("period_end"),
                "intent_hint": draft.get("intent_hint"),
                "eventLineId": draft.get("event_line_id"),
                "sourceManifest": draft.get("source_manifest") or [],
                "agentManifest": draft.get("agent_manifest") or [],
                "templateManifest": draft.get("template_manifest") or {},
                "generatorAgent": "project_workspace",
            },
        },
    )
    store.save_report_draft(
        _string(draft.get("client_id")),
        {
            **draft,
            **saved,
            "status": "saved",
            "artifact": saved.get("artifact"),
            "saved_at": saved.get("saved_at") or _now(),
        },
    )
    return saved


@router.get(r"retrieval/shadow-runs")
def get_retrieval_shadow_runs(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    query = {
        "limit": request.query.get("limit") or "60",
    }
    if request.query.get("clientId"):
        query["projectId"] = request.query["clientId"]
    return _cloud_query(
        compatibility,
        "/api/v2/workbench/retrieval-shadow-runs",
        query=query,
    )


@router.get(r"retrieval/shadow-summary")
def get_retrieval_shadow_summary(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    query = (
        {"projectId": request.query["clientId"]}
        if request.query.get("clientId")
        else None
    )
    return _cloud_query(
        compatibility,
        "/api/v2/workbench/retrieval-shadow-summary",
        query=query,
    )


@router.post(r"workspace-answer-action-cards/([^/]+)/(create-task|request-evidence)")
def answer_task_action(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    action = (
        "create_task"
        if match.group(2) == "create-task"
        else "request_evidence"
    )
    answer = compatibility.runtime.workbench_answer(match.group(1))
    project_id = _string(answer.get("projectId"))
    if not project_id:
        raise LocalRuntimeError(409, "answer_workspace_missing", "回答没有固定项目归属")
    question = _string(answer.get("question"))
    answer_markdown = _string(answer.get("answerMarkdown"))
    title = (
        f"跟进：{question}" if action == "create_task" else f"补充证据：{question}"
    )[:160]
    description = (
        answer_markdown
        if action == "create_task"
        else f"请补充支持以下工作台回答的权威资料：\n\n{answer_markdown}"
    )
    return {
        "messageId": match.group(1),
        "actionType": action,
        "status": "created",
        "summary": "已生成任务草稿，请确认后保存",
        "taskId": None,
        "autoApproved": False,
        "autoExecuted": False,
        "taskDraft": {
            "clientId": project_id,
            "title": title,
            "description": description,
            "priority": "normal",
            "sourceType": "ai_answer",
            "sourceId": match.group(1),
        },
    }


@router.post(r"clients/([^/]+)/todos/([^/]+)/promote-to-task")
def promote_todo_to_task(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/todos/{match.group(2)}/promote",
        request.body,
    )


@router.post(r"clients/([^/]+)/todos/([^/]+)/dismiss")
def dismiss_todo(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    action = _string(request.body.get("action")) or "cancel"
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/todos/{match.group(2)}/{action}",
        request.body,
    )


@router.post(r"clients/([^/]+)/narrative/regenerate")
@router.post(r"clients/([^/]+)/digital-assets/narrative/refresh")
def regenerate_narrative(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    _require_project_read(compatibility, project_id)
    requested_dimensions = request.body.get("dimensions")
    dimensions = (
        [str(value) for value in requested_dimensions]
        if isinstance(requested_dimensions, list)
        else None
    )
    return compatibility.runtime.workbench_rebuild_strategic_profile(
        project_id=project_id,
        idempotency_key=f"{request.idempotency_key}:strategic-profile",
        dimensions=dimensions,
    )


@router.get(r"clients/([^/]+)/official-website")
def get_official_website(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/official-website",
    )


@router.post(r"clients/([^/]+)/official-website/refresh")
def refresh_official_website(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    raw_url = _string(request.body.get("url"))
    if not raw_url:
        raise LocalRuntimeError(422, "official_website_url_required", "请先填写项目官网地址")
    try:
        pages = capture_official_website(raw_url, max_pages=36)
    except PublicCaptureError as exc:
        raise LocalRuntimeError(
            503 if exc.retryable else 422,
            exc.code,
            exc.message,
        ) from exc
    rendered_pages = [
        dict(item)
        for item in request.body.get("renderedPages") or []
        if isinstance(item, Mapping)
    ]
    if rendered_pages:
        try:
            pages = merge_rendered_official_pages(
                raw_url,
                pages,
                rendered_pages,
                max_pages=36,
            )
        except PublicCaptureError as exc:
            raise LocalRuntimeError(
                503 if exc.retryable else 422,
                exc.code,
                exc.message,
            ) from exc
    project = _cloud_query(
        compatibility,
        f"/api/v2/domain/project-materials/projects/{match.group(1)}",
    ).get("project") or {}
    # 先登记盘点结果，模型某一批超时也不会丢掉已经读取的官网页面。
    compatibility.runtime.cloud_command(
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/official-website/captures",
        payload={"pages": [page.as_cloud_payload() for page in pages], "factCandidates": []},
        idempotency_key=f"{request.idempotency_key}:site-scan",
    )
    fact_candidates, research_progress = _official_fact_candidates(
        compatibility,
        project_name=_string(project.get("name")) or "当前项目",
        pages=pages,
    )
    committed = compatibility.runtime.cloud_command(
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/official-website/captures",
        payload={
            "pages": [page.as_cloud_payload() for page in pages],
            "factCandidates": fact_candidates,
            "researchProgress": research_progress,
        },
        idempotency_key=f"{request.idempotency_key}:targeted-facts",
    )
    compatibility.runtime.cloud_command(
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/official-website/auto-verify",
        payload={},
        idempotency_key=f"{request.idempotency_key}:auto-verify-existing",
    )
    # Status queries count all pending candidates, including earlier target batches.
    latest = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/official-website",
    )
    return {**committed, **latest, "researchProgress": research_progress}


@router.get(r"retrieval/settings")
def get_retrieval_settings(
    compatibility: Any,
    _: UiRequest,
    __: re.Match[str],
) -> Any:
    return _cloud_query(compatibility, "/api/v2/workbench/retrieval-settings")


@router.post(r"retrieval/settings")
def save_retrieval_settings(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    path = "/api/v2/workbench/retrieval-settings"

    def payload_factory() -> dict[str, Any]:
        payload = dict(request.body)
        try:
            current = _cloud_query(compatibility, path)
        except LocalRuntimeError as exc:
            if exc.status_code != 404:
                raise
        else:
            payload["expectedVersion"] = current.get("version")
        return payload

    return _replayable_workbench_mutation(
        compatibility,
        request,
        "POST",
        path,
        aggregate_type="retrieval_settings",
        aggregate_id="organization-retrieval-settings",
        payload_factory=payload_factory,
    )


@router.post(r"clients/([^/]+)/workspace/proposal-drafts")
def create_workspace_proposal_draft(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/proposal-drafts",
        request.body,
    )


@router.post(r"clients/([^/]+)/meetings/([^/]+)/proposals/(follow-up|prepare)")
def create_meeting_proposal_draft(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        (
            f"/api/v2/workbench/projects/{match.group(1)}/meetings/"
            f"{match.group(2)}/proposal-drafts/{match.group(3)}"
        ),
        {},
    )


@router.get(r"clients/([^/]+)/meeting-action-items")
def get_meeting_action_items(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/meeting-action-items",
    )


@router.post(r"workspace-answer-action-cards/([^/]+)/create-proposal")
def create_answer_action_proposal(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    answer = _answer_for_message(compatibility, match.group(1))
    project_id = _string(answer.get("projectId"))
    if not project_id:
        raise LocalRuntimeError(
            422,
            "answer_project_context_required",
            "回答没有固定项目 WorkspaceContext，无法创建提案",
        )
    proposal = _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/ai-proposals",
        {
            "clientId": project_id,
            "answerId": answer.get("answerId"),
            "kind": "context_refresh",
            "title": f"由工作台回答生成上下文更新提案：{_string(answer.get('question'))[:48]}",
            "summary": _string(answer.get("answerMarkdown"))[:1000],
            "rationale": "由当前成员的严格 ai_answers 记录显式触发",
            "sourceType": "manual",
            "sourceRefs": [
                f"ai_answer:{answer.get('answerId')}@{answer.get('version')}"
            ],
            "payload": {"sourceAnswerId": answer.get("answerId")},
        },
    )
    return {
        "messageId": match.group(1),
        "actionType": "create-proposal",
        "status": "created",
        "summary": "已创建需审批的严格提案草稿",
        "draftId": proposal.get("id"),
        "proposalId": proposal.get("id"),
        "autoApproved": False,
        "autoExecuted": False,
    }


@router.get(r"workspace-answer-value-reviews")
def list_workspace_answer_value_reviews(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    query = {"limit": request.query.get("limit") or "120"}
    if request.query.get("clientId"):
        query["projectId"] = request.query["clientId"]
    return _cloud_query(
        compatibility,
        "/api/v2/workbench/answer-value-reviews",
        query=query,
    )


@router.post(r"workspace-answer-value-reviews")
def create_workspace_answer_value_review(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/workbench/answer-value-reviews",
        request.body,
    )


@router.get(r"workspace-answer-value-summary")
def get_workspace_answer_value_summary(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    project_id = _string(request.query.get("clientId"))
    if not project_id:
        raise LocalRuntimeError(
            422,
            "answer_value_project_required",
            "clientId 不能为空",
        )
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/answer-value-summary",
    )


@router.post(r"workspace-answer/([^/]+)/promote-to-judgment")
def promote_workspace_answer_to_judgment(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    judgment = _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/answers/{match.group(1)}/judgment",
        request.body,
    )
    return {
        "messageId": match.group(1),
        "actionType": "promote-to-judgment",
        "status": "created",
        "summary": "已沉淀为待复核的严格判断版本",
        "draftId": judgment.get("id"),
        "autoApproved": False,
        "autoExecuted": False,
    }


@router.post(r"memory/judgments/confirm")
def confirm_workspace_judgment(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    judgment_id = _string(request.body.get("judgmentId"))
    if not judgment_id:
        raise LocalRuntimeError(422, "judgment_id_required", "judgmentId 不能为空")
    path = f"/api/v2/workbench/judgments/{judgment_id}/confirm"

    def payload_factory() -> dict[str, Any]:
        current = _cloud_query(
            compatibility,
            f"/api/v2/workbench/judgments/{judgment_id}",
        )
        return {**request.body, "expectedVersion": current.get("aggregateVersion")}

    return _replayable_workbench_mutation(
        compatibility,
        request,
        "POST",
        path,
        aggregate_type="ai_proposal",
        aggregate_id=judgment_id,
        payload_factory=payload_factory,
    )


@router.get(r"workspace-answer-quality-failures")
def list_workspace_answer_quality_failures(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    query = {"limit": request.query.get("limit") or "80"}
    if request.query.get("clientId"):
        query["projectId"] = request.query["clientId"]
    return _cloud_query(
        compatibility,
        "/api/v2/workbench/answer-quality-failures",
        query=query,
    )


@router.post(r"workspace-answer-quality-failures/([^/]+)/resolve")
def resolve_workspace_answer_quality_failure(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    failure_id = match.group(1)
    path = f"/api/v2/workbench/answer-quality-failures/{failure_id}/resolve"

    def payload_factory() -> dict[str, Any]:
        current = _cloud_query(
            compatibility,
            f"/api/v2/workbench/answer-quality-failures/{failure_id}",
        )
        return {**request.body, "expectedVersion": current.get("version")}

    return _replayable_workbench_mutation(
        compatibility,
        request,
        "POST",
        path,
        aggregate_type="answer_quality_failure",
        aggregate_id=failure_id,
        payload_factory=payload_factory,
    )


@router.post(r"memory/dna/delta")
def create_workspace_dna_delta(
    compatibility: Any,
    request: UiRequest,
    __: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/workbench/dna-deltas",
        request.body,
    )


def _project_structure_item_version(
    compatibility: Any,
    project_id: str,
    collection: str,
    item_id: str,
) -> int:
    structure = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/structure",
    )
    item = next(
        (
            value
            for value in structure.get(collection) or []
            if str(value.get("id") or "") == item_id
        ),
        None,
    )
    if item is None:
        raise LocalRuntimeError(404, "project_structure_item_missing", "项目结构记录不存在")
    return int(item.get("version") or 0)


@router.post(r"clients/([^/]+)/project-modules")
def create_project_module(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/structure/project_module",
        request.body,
    )


@router.patch(r"clients/([^/]+)/project-modules/([^/]+)")
def update_project_module(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, module_id = match.groups()
    return _cloud_command(
        compatibility,
        request,
        "PATCH",
        (
            f"/api/v2/workbench/projects/{project_id}/structure/"
            f"project_module/{module_id}"
        ),
        {
            **request.body,
            "expectedVersion": _project_structure_item_version(
                compatibility,
                project_id,
                "modules",
                module_id,
            ),
        },
    )


@router.delete(r"clients/([^/]+)/project-modules/([^/]+)")
def delete_project_module(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, module_id = match.groups()
    return _cloud_command(
        compatibility,
        request,
        "DELETE",
        (
            f"/api/v2/workbench/projects/{project_id}/structure/"
            f"project_module/{module_id}"
        ),
        {
            "expectedVersion": _project_structure_item_version(
                compatibility,
                project_id,
                "modules",
                module_id,
            )
        },
    )


@router.post(r"clients/([^/]+)/project-flows")
def create_project_flow(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/structure/project_flow",
        request.body,
    )


@router.patch(r"clients/([^/]+)/project-flows/([^/]+)")
def update_project_flow(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, flow_id = match.groups()
    return _cloud_command(
        compatibility,
        request,
        "PATCH",
        (
            f"/api/v2/workbench/projects/{project_id}/structure/"
            f"project_flow/{flow_id}"
        ),
        {
            **request.body,
            "expectedVersion": _project_structure_item_version(
                compatibility,
                project_id,
                "flows",
                flow_id,
            ),
        },
    )


@router.delete(r"clients/([^/]+)/project-flows/([^/]+)")
def delete_project_flow(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, flow_id = match.groups()
    return _cloud_command(
        compatibility,
        request,
        "DELETE",
        (
            f"/api/v2/workbench/projects/{project_id}/structure/"
            f"project_flow/{flow_id}"
        ),
        {
            "expectedVersion": _project_structure_item_version(
                compatibility,
                project_id,
                "flows",
                flow_id,
            )
        },
    )


@router.get(r"clients/([^/]+)/narrative/clarifications")
def list_narrative_clarifications(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        (
            f"/api/v2/workbench/projects/{match.group(1)}/"
            "narrative-clarifications"
        ),
    )


@router.post(r"clients/([^/]+)/narrative/clarifications")
def submit_narrative_clarification(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id = match.group(1)
    clarification = _cloud_command(
        compatibility,
        request,
        "POST",
        (
            f"/api/v2/workbench/projects/{project_id}/"
            "narrative-clarifications"
        ),
        request.body,
    )
    if str(request.body.get("feedbackKind") or "") == "project_keyword_supplement":
        # Keyword supplements are already formal verified project facts in the
        # cloud.  They refresh the versioned recognition profile directly and
        # must not be reported as failed merely because this device cannot
        # rebuild the larger strategic dossier at the same moment.
        return {
            **dict(clarification),
            "profileUpdate": {
                "state": "pending_refresh",
                "retryable": False,
            },
        }
    # This input belongs to the client profile itself.  It is already a
    # verified project fact on the cloud, so the device holding the local Wiki
    # immediately rebuilds the safe client-profile version; no second click or
    # approval side path is required.
    narrative = compatibility.runtime.workbench_rebuild_strategic_profile(
        project_id=project_id,
        idempotency_key=f"{request.idempotency_key}:profile-clarification-rebuild",
        dimensions=[str(request.body.get("dimension") or "")],
    )
    return {**dict(clarification), "narrative": narrative}


@router.get(r"clients/([^/]+)/suggestions/log")
def get_suggestion_log(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{match.group(1)}/suggestion-log",
    )


@router.post(r"clients/([^/]+)/suggestions/log")
def save_suggestion_log(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/projects/{match.group(1)}/suggestion-log",
        request.body,
    )


@router.delete(r"clients/([^/]+)/suggestions/log/([^/]+)")
def delete_suggestion_log(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "DELETE",
        (
            f"/api/v2/workbench/projects/{match.group(1)}/"
            f"suggestion-log/{match.group(2)}"
        ),
        {},
    )


@router.get(r"workspace-value-validation-sessions")
def list_value_validation_sessions(
    compatibility: Any,
    request: UiRequest,
    _: re.Match[str],
) -> Any:
    query = {"limit": request.query.get("limit") or "20"}
    if request.query.get("clientId"):
        query["project_id"] = request.query["clientId"]
    return _cloud_query(
        compatibility,
        "/api/v2/workbench/value-validation-sessions",
        query=query,
    )


@router.post(r"workspace-value-validation-sessions")
def create_value_validation_session(
    compatibility: Any,
    request: UiRequest,
    _: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        "/api/v2/workbench/value-validation-sessions",
        {"projectId": request.body.get("clientId")},
    )


@router.get(r"workspace-value-validation-sessions/([^/]+)")
def get_value_validation_session(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_query(
        compatibility,
        f"/api/v2/workbench/value-validation-sessions/{match.group(1)}",
    )


@router.post(
    r"workspace-value-validation-sessions/([^/]+)/(complete-question|finish)"
)
def update_value_validation_session(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    return _cloud_command(
        compatibility,
        request,
        "POST",
        (
            f"/api/v2/workbench/value-validation-sessions/"
            f"{match.group(1)}/{match.group(2)}"
        ),
        request.body,
    )


def _meeting_from_text(
    project_id: str,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        payload = json.loads(_string(item.get("markdownContent")))
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    return {
        "id": payload.get("id") or item.get("documentId"),
        "clientId": project_id,
        "title": payload.get("title") or item.get("title") or "项目会议",
        "stage": payload.get("stage") or "prepared",
        "scheduledAt": payload.get("scheduledAt"),
        "updatedAt": item.get("updatedAt") or payload.get("updatedAt"),
        "summary": payload.get("summary") or "",
        "transcriptText": "",
        "notes": "",
        "agendaItems": list(payload.get("agendaItems") or []),
        "decisions": list(payload.get("decisions") or []),
        "actionItems": list(payload.get("actionItems") or []),
        "risks": list(payload.get("risks") or []),
        "ambiguities": [],
        "sourceScope": "organization_shared_summary",
        "_strictVersion": int(item.get("version") or 1),
    }


def _meeting_records(
    project_id: str,
    texts: Mapping[str, Any],
) -> list[dict[str, Any]]:
    return [
        _meeting_from_text(project_id, item)
        for key, item in texts.items()
        if str(key).startswith("meeting-summary:")
        and isinstance(item, Mapping)
        and item.get("lifecycleState") != "archived"
    ]


def _meeting(
    compatibility: Any,
    project_id: str,
    meeting_id: str,
) -> dict[str, Any]:
    if hasattr(compatibility.runtime, "database_path"):
        try:
            return LocalProjectMaterialsRepository(
                compatibility.runtime
            ).meeting(project_id, meeting_id)
        except LocalRuntimeError as exc:
            if exc.code != "meeting_missing":
                raise
    texts = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/texts",
    )
    key = f"meeting-summary:{meeting_id}"
    item = texts.get(key)
    if not isinstance(item, Mapping):
        raise LocalRuntimeError(404, "meeting_missing", "会议不存在")
    return _meeting_from_text(project_id, item)


def _save_meeting(
    compatibility: Any,
    request: UiRequest,
    *,
    project_id: str,
    meeting: Mapping[str, Any],
) -> dict[str, Any]:
    meeting_id = _string(meeting.get("id")) or _stable_ui_id(
        "meeting", project_id, request.idempotency_key
    )
    payload = {
        **dict(meeting),
        "id": meeting_id,
        "clientId": project_id,
        "updatedAt": _now(),
    }
    del request
    if not hasattr(compatibility.runtime, "database_path"):
        raise LocalRuntimeError(
            409,
            "local_meeting_storage_unavailable",
            "会议源资料只能保存在当前成员本机严格存储",
        )
    return LocalProjectMaterialsRepository(
        compatibility.runtime
    ).save_meeting(project_id, payload)


def _published_meeting_payload(meeting: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "yiyu.project-meeting-summary.v1",
        "id": meeting.get("id"),
        "title": meeting.get("title"),
        "stage": "published",
        "scheduledAt": meeting.get("scheduledAt"),
        "summary": meeting.get("summary") or "",
        "agendaItems": [
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "description": item.get("description") or "",
            }
            for item in meeting.get("agendaItems") or []
            if isinstance(item, Mapping)
        ],
        "decisions": [
            {
                "id": item.get("id"),
                "summary": item.get("summary") or item.get("title") or "",
            }
            for item in meeting.get("decisions") or []
            if isinstance(item, Mapping)
        ],
        "actionItems": [
            {
                "id": item.get("id"),
                "summary": item.get("summary") or item.get("title") or "",
                "ownerMembershipId": item.get("ownerMembershipId"),
                "dueDate": item.get("dueDate"),
            }
            for item in meeting.get("actionItems") or []
            if isinstance(item, Mapping)
        ],
        "risks": [
            {
                "id": item.get("id"),
                "summary": item.get("summary") or item.get("title") or "",
                "severity": item.get("severity") or "normal",
            }
            for item in meeting.get("risks") or []
            if isinstance(item, Mapping)
        ],
        "materialBoundary": {
            "sourceFileUploaded": False,
            "rawTranscriptUploaded": False,
            "rawNotesUploaded": False,
            "unresolvedAmbiguitiesUploaded": False,
        },
        "updatedAt": _now(),
    }


def _publish_meeting(
    compatibility: Any,
    request: UiRequest,
    *,
    project_id: str,
    meeting: Mapping[str, Any],
) -> dict[str, Any]:
    meeting_id = _string(meeting.get("id"))
    published = _published_meeting_payload(meeting)
    _project_text_save(
        compatibility,
        request,
        project_id=project_id,
        key=f"meeting-summary:{meeting_id}",
        title=_string(meeting.get("title")) or "项目会议摘要",
        markdown=json.dumps(published, ensure_ascii=False, sort_keys=True),
    )
    return _save_meeting(
        compatibility,
        request,
        project_id=project_id,
        meeting={**dict(meeting), "stage": "published"},
    )


@router.post(r"clients/([^/]+)/meetings")
def create_meeting(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    title = _string(request.body.get("title"))
    if not title:
        raise LocalRuntimeError(422, "meeting_title_required", "请输入会议标题")
    meeting = _save_meeting(
        compatibility,
        request,
        project_id=match.group(1),
        meeting={
            "id": _stable_ui_id(
                "meeting", match.group(1), request.idempotency_key
            ),
            "title": title,
            "stage": "prepared",
            "scheduledAt": request.body.get("scheduledAt"),
            "transcriptText": "",
            "notes": "",
            "agendaItems": [],
            "decisions": [],
            "actionItems": [],
            "risks": [],
            "ambiguities": [],
        },
    )
    return {
        "meeting": meeting,
        "message": "会议草稿已保存到当前成员本机；发布后才共享摘要",
    }


@router.post(r"clients/([^/]+)/meetings/([^/]+)/(extract|ingest|publish|resolve)")
def update_meeting_pipeline(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    project_id, meeting_id, action = match.groups()
    meeting = _meeting(compatibility, project_id, meeting_id)
    if action == "ingest":
        meeting["transcriptText"] = _string(
            request.body.get("transcriptText")
        )
        meeting["notes"] = _string(request.body.get("notes"))
        meeting["stage"] = "ingested"
    elif action == "extract":
        source = (
            _string(meeting.get("transcriptText"))
            + "\n"
            + _string(meeting.get("notes"))
        )
        lines = [
            line.strip(" -•\t")
            for line in source.splitlines()
            if line.strip(" -•\t")
        ]
        meeting["decisions"] = [
            {
                "id": _stable_ui_id("decision", meeting_id, index, line),
                "summary": line[:300],
            }
            for index, line in enumerate(lines)
            if any(token in line for token in ("决定", "确认", "同意"))
        ]
        meeting["risks"] = [
            {
                "id": _stable_ui_id("risk", meeting_id, index, line),
                "summary": line[:300],
                "severity": "normal",
            }
            for index, line in enumerate(lines)
            if any(token in line for token in ("风险", "困难", "阻碍"))
        ]
        meeting["ambiguities"] = [
            {
                "id": _stable_ui_id("ambiguity", meeting_id, index, line),
                "rawText": line[:300],
                "candidates": [],
                "status": "pending",
            }
            for index, line in enumerate(lines)
            if any(token in line for token in ("待确认", "不确定", "？", "?"))
        ]
        meeting["stage"] = "extracted"
    elif action == "resolve":
        resolutions = request.body.get("resolutions") or {}
        for item in meeting.get("ambiguities") or []:
            if str(item.get("id")) in resolutions:
                item["status"] = "resolved"
                item["resolution"] = resolutions[str(item["id"])]
        meeting["stage"] = "resolved"
    else:
        meeting["summary"] = _string(
            request.body.get("summary")
        ) or _string(meeting.get("summary"))
        saved = _publish_meeting(
            compatibility,
            request,
            project_id=project_id,
            meeting=meeting,
        )
        return {
            "meeting": saved,
            "message": "仅会议摘要、决定、行动项和风险已共享到组织云",
        }
    saved = _save_meeting(
        compatibility,
        request,
        project_id=project_id,
        meeting=meeting,
    )
    return {"meeting": saved, "message": f"会议流程已进入 {saved['stage']}"}


@router.post(r"clients/([^/]+)/meetings/launch-feishu")
def launch_feishu_meeting(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    project_id = match.group(1)
    title = _string(request.body.get("title"))
    if not title:
        raise LocalRuntimeError(422, "meeting_title_required", "请输入会议标题")
    meeting_id = _stable_ui_id(
        "meeting", project_id, request.idempotency_key, "feishu"
    )
    meeting = _save_meeting(
        compatibility,
        request,
        project_id=project_id,
        meeting={
            "id": meeting_id,
            "title": title,
            "stage": "prepared",
            "scheduledAt": request.body.get("scheduledAt"),
            "sourceTaskId": request.body.get("sourceTaskId"),
            "transcriptText": "",
            "notes": "",
            "agendaItems": [],
            "decisions": [],
            "actionItems": [],
            "risks": [],
            "ambiguities": [],
        },
    )
    command_hint = (
        f"纪要回写 {meeting_id}\\n请把会议纪要正文粘贴在第二行开始。"
    )
    notice_text = (
        f"【会议草稿】{title}\n"
        f"计划时间：{request.body.get('scheduledAt') or '待补充'}\n"
        f"会议编号：{meeting_id}\n\n"
        f"纪要回写格式：\n{command_hint}"
    )
    try:
        delivered = compatibility.runtime.cloud_command(
            "POST",
            "/api/v2/platform-integrations/command",
            payload={
                "resourcePath": "me/feishu-message/send",
                "authorizationScope": "personal",
                "method": "POST",
                "query": {},
                "payload": {
                    "text": notice_text,
                    "localType": "meeting",
                    "localId": meeting_id,
                },
            },
            idempotency_key=(
                f"{request.idempotency_key}:feishu-meeting-notice"
            ),
            refresh_business=False,
        )
        result = delivered.get("result")
        if not isinstance(result, Mapping):
            raise LocalRuntimeError(
                502,
                "feishu_message_result_invalid",
                "组织云没有返回有效的飞书投递结果",
            )
        status = _string(result.get("status")) or "failed"
        message = _string(result.get("message"))
        return {
            "meeting": meeting,
            "deliveryStatus": status,
            "deliveryMessage": (
                "会议草稿已保存到当前成员本机；"
                + (
                    "飞书提醒已发送"
                    if status == "sent"
                    else f"飞书提醒未发送：{message or '当前投递条件不满足'}"
                )
            ),
            "commandHint": command_hint,
            "noticeText": notice_text,
            "deliveryMode": result.get("deliveryMode") or "none",
            "deliveryTarget": result.get("deliveryTarget"),
            "operationId": result.get("operationId"),
            "retryable": bool(result.get("retryable")),
            "state": result.get("state") or (
                "ready" if status == "sent" else "blocked"
            ),
        }
    except (LocalRuntimeError, AttributeError, TypeError) as exc:
        message = (
            exc.message
            if isinstance(exc, LocalRuntimeError)
            else "组织飞书投递状态查询失败，可稍后重试"
        )
        failed_retryable = (
            isinstance(exc, LocalRuntimeError)
            and exc.status_code >= 500
        )
    return {
        "meeting": meeting,
        "deliveryStatus": "failed" if failed_retryable else "skipped",
        "deliveryMessage": f"会议草稿已保存到当前成员本机；未发送飞书：{message}",
        "commandHint": command_hint,
        "noticeText": notice_text,
        "deliveryMode": "none",
        "deliveryTarget": None,
        "retryable": True,
        "state": "failed_retryable" if failed_retryable else "blocked",
    }


def _strategic_cockpit(
    compatibility: Any,
    project_id: str,
) -> dict[str, Any]:
    workspace = _workspace(compatibility, project_id)
    texts = _cloud_query(
        compatibility,
        f"/api/v2/workbench/projects/{project_id}/texts",
    )
    confirmation = texts.get("strategic_cockpit_confirmation") or {}
    try:
        official = json.loads(
            _string(confirmation.get("markdownContent"))
        )
    except (TypeError, ValueError):
        official = {}
    if not isinstance(official, Mapping):
        official = {}
    client = workspace["client"]
    tasks = list(workspace.get("relatedTasks") or [])
    documents = list(workspace.get("documents") or [])
    score = min(100, len(tasks) * 10 + len(documents) * 5)
    ready = score >= 30
    focus_items = list(official.get("focusItems") or [])
    return {
        "clientId": project_id,
        "clientName": client.get("name") or "",
        "clientTagline": client.get("intro") or "",
        "stageLabel": client.get("stage") or "active",
        "permission": {
            "canEdit": True,
            "isCeo": False,
            "leaderUserId": None,
            "notice": None,
        },
        "readiness": {
            "status": "ready" if ready else "insufficient",
            "score": score,
            "summary": "已有可研判的项目事实" if ready else "项目事实仍不足",
            "gaps": [] if ready else ["补充项目资料、任务或已确认判断"],
        },
        "headline": {
            "weekSummary": {
                "value": official.get("weekSummary") or "",
                "status": "confirmed" if official else "system_draft",
                "sources": [],
            },
            "mainContradiction": {
                "value": official.get("mainContradiction") or "",
                "status": "confirmed" if official else "system_draft",
                "sources": [],
            },
            "coreBreakthrough": {
                "value": official.get("coreBreakthrough") or "",
                "status": "confirmed" if official else "system_draft",
                "sources": [],
            },
            "focusItems": focus_items,
            "focusStatus": "confirmed" if official else "system_draft",
            "freshness": confirmation.get("updatedAt") or _now(),
        },
        "health": [
            {
                "key": "knowledge",
                "title": "项目知识",
                "status": "healthy" if documents else "uncalibrated",
                "trend": "stable",
                "summary": f"{len(documents)} 份严格项目资料",
                "evidence": [str(item.get("id")) for item in documents[:10]],
            },
            {
                "key": "execution",
                "title": "任务执行",
                "status": "healthy" if tasks else "uncalibrated",
                "trend": "stable",
                "summary": f"{len(tasks)} 项严格任务",
                "evidence": [
                    str(item.get("taskId") or item.get("id")) for item in tasks[:10]
                ],
            },
        ],
        "strategicLines": [],
        "twoWeekChanges": [],
        "pendingDecisions": [],
        "pendingMaterials": [],
        "meetingPackDraft": {
            "title": f"{client.get('name') or '项目'}战略碰头会",
            "agenda": [
                value
                for value in (
                    official.get("mainContradiction"),
                    official.get("coreBreakthrough"),
                    *focus_items,
                )
                if value
            ],
            "groups": [],
        },
        "evidencePreview": {
            "summary": f"基于 {len(documents)} 份资料和 {len(tasks)} 项任务",
            "cards": [],
            "boundaries": [
                "成员本机源文件正文未写入组织云驾驶舱",
                "没有权威事实时保持未校准，不生成伪判断",
            ],
            "keyFacts": [],
            "keyWarnings": [],
        },
        "assetCandidates": [],
        "officialLayer": dict(official),
        "radarLayer": {},
        "officialLayerStatus": "ready" if official else "empty",
        "officialEmptyReason": None if official else "尚未确认本周战略判断",
        "resolutionTrace": {
            "authority": (
                "project_text/knowledge_documents/task_records"
            )
        },
        "notebookSummary": workspace.get("notebookSummary"),
        "memoryStatus": workspace.get("memoryStatus"),
        "linkedEventLineMemories": [],
    }


@router.get(r"clients/([^/]+)/strategic-cockpit")
def strategic_cockpit(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    return _strategic_cockpit(compatibility, match.group(1))


@router.post(r"clients/([^/]+)/strategic-cockpit/confirm")
def confirm_strategic_cockpit(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    project_id = match.group(1)
    required = ("weekSummary", "mainContradiction", "coreBreakthrough")
    if any(not _string(request.body.get(key)) for key in required):
        raise LocalRuntimeError(
            422,
            "strategic_confirmation_incomplete",
            "请完整填写周摘要、主要矛盾和核心突破口",
        )
    _project_text_save(
        compatibility,
        request,
        project_id=project_id,
        key="strategic_cockpit_confirmation",
        title="战略驾驶舱确认",
        markdown=json.dumps(
            {
                key: request.body.get(key)
                for key in (*required, "focusItems")
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    return _strategic_cockpit(compatibility, project_id)


@router.post(r"clients/([^/]+)/strategic-cockpit/meeting-pack")
def create_strategic_meeting_pack(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    project_id = match.group(1)
    cockpit = _strategic_cockpit(compatibility, project_id)
    draft = cockpit["meetingPackDraft"]
    meeting = _save_meeting(
        compatibility,
        request,
        project_id=project_id,
        meeting={
            "id": new_id(),
            "title": draft["title"],
            "stage": "prepared",
            "scheduledAt": None,
            "transcriptText": "",
            "notes": cockpit["evidencePreview"]["summary"],
            "agendaItems": [
                {"id": new_id(), "title": item, "description": ""}
                for item in draft["agenda"]
            ],
            "decisions": [],
            "actionItems": [],
            "risks": [],
            "ambiguities": [],
            "sourceType": "strategic_cockpit",
        },
    )
    return {"meeting": meeting, "message": "战略会议包已保存为严格项目会议"}


@router.post(r"clients/([^/]+)/strategic-cockpit/meeting-pack/([^/]+)/apply")
def apply_strategic_meeting_pack(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    project_id, meeting_id = match.groups()
    meeting = _meeting(compatibility, project_id, meeting_id)
    _publish_meeting(
        compatibility,
        request,
        project_id=project_id,
        meeting=meeting,
    )
    return _strategic_cockpit(compatibility, project_id)


@router.post(r"analysis/backfill-main-chain")
def backfill_analysis_main_chain(
    compatibility: Any,
    request: UiRequest,
    _: re.Match[str],
) -> dict[str, Any]:
    snapshot = compatibility.runtime.business_snapshot(refresh=False)
    requested = {
        _string(value) for value in request.body.get("clientIds") or []
    }
    projects = [
        item
        for item in snapshot.get("projects") or []
        if not requested or _string(item.get("projectId")) in requested
    ]
    max_jobs = max(0, int(request.body.get("maxJobs") or len(projects)))
    candidates = [
        {
            "clientId": item.get("projectId"),
            "scopeType": "client",
            "scopeId": item.get("projectId"),
            "jobType": "evidence_extract",
            "triggerType": "strict_backfill",
            "intentProfile": "client_overview",
        }
        for item in projects[:max_jobs]
    ]
    dry_run = bool(request.body.get("dryRun"))
    queued = 0
    if not dry_run and not request.body.get("pauseRequested"):
        for item in candidates:
            project_id = _string(item["clientId"])
            operation_key = (
                f"{request.idempotency_key}:project:{project_id}"
            )
            compatibility.runtime.workbench_chat(
                project_id=project_id,
                question="请基于当前严格项目知识生成项目主链摘要、证据边界和下一步。",
                mode="balanced",
                source_manifest_extra={
                    "jobType": "evidence_extract",
                    "triggerType": "strict_backfill",
                    "backfillProjectId": project_id,
                    "operationKey": operation_key,
                },
                idempotency_key=operation_key,
            )
            queued += 1
    return {
        "dryRun": dry_run,
        "pauseRequested": bool(request.body.get("pauseRequested")),
        "paused": bool(request.body.get("pauseRequested")),
        "scannedClients": len(projects),
        "queuedJobs": queued,
        "skippedJobs": len(candidates) - queued,
        "candidates": candidates,
    }


@router.post(r"clients/([^/]+)/analysis-runs/([^/]+)/cancel")
def cancel_analysis_run(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    project_id, run_id = match.group(1), match.group(2)
    return _cloud_command(
        compatibility,
        request,
        "POST",
        (
            f"/api/v2/workbench/projects/{project_id}/"
            f"analysis-runs/{run_id}/cancel"
        ),
        {},
    )


@router.post(r"clients/([^/]+)/knowledge/export-answer")
def export_answer(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    project_id = match.group(1)
    raw_ids = request.body.get("messageIds") or [
        request.body.get("messageId")
    ]
    ids = {_string(value) for value in raw_ids if _string(value)}
    answers = []
    for answer_id in sorted(ids):
        try:
            item = compatibility.runtime.workbench_answer(answer_id)
        except LocalRuntimeError as exc:
            if exc.status_code == 404:
                continue
            raise
        if _string(item.get("projectId")) == project_id:
            answers.append(item)
    if not answers:
        raise LocalRuntimeError(404, "answer_missing", "未找到要导出的项目回答")
    content = "\n\n".join(
        f"# {item.get('question') or '工作台回答'}\n\n"
        f"{item.get('answerMarkdown') or ''}"
        for item in answers
    )
    export_title = _answer_export_title(compatibility, answers)
    store = LocalProjectMaterialsRepository(compatibility.runtime)
    local = store.import_text(
        project_id=project_id,
        title=export_title,
        content=content,
        # The UI request may be replayed after the cloud registration has
        # committed but before its response reaches Electron.  Bind the local
        # file preparation to the same user intent so a replay reuses the
        # original localSourceId/path instead of producing a different cloud
        # metadata payload under the same idempotency key.
        idempotency_key=f"{request.idempotency_key}:local-answer-export",
    )
    registered = _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/domain/project-materials/projects/{project_id}"
        "/materials/register-metadata",
        {
            "materials": [
                {
                    "localSourceId": local["localSourceId"],
                    "fileName": local["fileName"],
                    "contentHash": local["contentHash"],
                    "byteSize": local["byteSize"],
                    "mediaType": local["mediaType"],
                    "sourceKind": "local_private_answer_export",
                }
            ]
        },
    )
    document = (registered.get("documents") or [])[0]
    store.bind_cloud_documents(
        project_id=project_id,
        local_materials=[local],
        cloud_documents=[document],
    )
    return {
        "clientId": project_id,
        "documentId": document.get("documentId"),
        "title": local["title"],
        "fileName": local["fileName"],
        "path": local["managedPath"],
    }


@router.post(
    r"clients/([^/]+)/knowledge/(parse-failures/retry|rebuild|reindex-vector)"
)
def project_knowledge_action(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    project_id, action = match.groups()
    if action == "rebuild":
        context = compatibility.runtime.project_knowledge_context(project_id)
        return {
            "clientId": project_id,
            "status": "completed",
            "state": (context.get("state") or {}).get("overall") or "empty",
            "counts": context.get("counts") or {},
            "message": "已从严格云摘要与当前设备本机摘要重建项目上下文",
        }
    if action == "reindex-vector":
        context = compatibility.runtime.project_knowledge_context(project_id)
        counts = context.get("counts") or {}
        return {
            "clientId": project_id,
            "embeddingSignature": "not-applicable:strict-project-context-v2",
            "masterIndexed": int(
                counts.get("organizationShared")
                or counts.get("ready")
                or len(context.get("items") or [])
            ),
            "chunkIndexed": len(context.get("summaryExcerpts") or []),
            "fallbackUsed": True,
            "status": "completed",
            "retrievalMode": "strict_relational_context",
        }

    failures = knowledge_failures(compatibility, request, match)
    selected = {
        str(value)
        for value in request.body.get("documentIds") or []
        if str(value)
    }
    if selected:
        failures = [
            item for item in failures if str(item["documentId"]) in selected
        ]
    store = LocalProjectMaterialsRepository(compatibility.runtime)
    items = []
    buckets: dict[str, int] = {}
    for failure in failures:
        document_id = str(failure["documentId"])
        title = str(failure.get("title") or document_id)
        try:
            preview = _cloud_query(
                compatibility,
                f"/api/v2/domain/project-materials/projects/{project_id}"
                f"/documents/{document_id}/reading-preview",
            )
            local = store.document_text(document_id)
            content = str(local.get("content") or "").strip()
            if not content:
                raise LocalRuntimeError(
                    422,
                    "local_document_empty",
                    "本机源文件没有可解析正文",
                )
            completion = compatibility.runtime.private_ai_completion(
                system_prompt=(
                    "你是项目资料修复器。只根据当前设备读取到的正文生成中文摘要，"
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
                    "资料处理没有生成可发布摘要",
                )
            store.update_ai_summary(
                document_id,
                summary=summary,
                model_name=str(
                    completion.get("modelName") or "organization_default"
                ),
            )
            _cloud_command(
                compatibility,
                UiRequest(
                    method=request.method,
                    path=request.path,
                    query=request.query,
                    body=request.body,
                    idempotency_key=(
                        f"{request.idempotency_key}:{document_id}"
                    ),
                ),
                "POST",
                f"/api/v2/domain/project-materials/projects/{project_id}"
                f"/documents/{document_id}/publish-local-summary",
                {
                    "expectedVersion": int(
                        preview.get("aggregateVersion") or 0
                    ),
                    "sourceContentHash": local["contentHash"],
                    "summary": summary[:4000],
                    "generatorVersion": str(
                        completion.get("modelName") or "organization_default"
                    ),
                },
            )
        except LocalRuntimeError as exc:
            failure_type = {
                "local_document_missing": "managed_path_missing",
                "local_document_preview_unsupported": "unsupported_format",
                "local_document_encoding_unsupported": "unsupported_format",
                "local_document_empty": "empty_text",
                "source_content_not_shared": "permission_denied",
            }.get(exc.code, "parser_exception")
            buckets[failure_type] = buckets.get(failure_type, 0) + 1
            items.append(
                {
                    "documentId": document_id,
                    "title": title,
                    "status": "failed",
                    "failureType": failure_type,
                    "message": exc.message,
                }
            )
            continue
        items.append(
            {
                "documentId": document_id,
                "title": title,
                "status": "succeeded",
                "failureType": None,
                "message": "已从当前设备源文件生成并发布组织共享摘要",
            }
        )
    succeeded = sum(item["status"] == "succeeded" for item in items)
    failed = sum(item["status"] == "failed" for item in items)
    return {
        "batchId": _stable_ui_id(
            "knowledge_batch",
            project_id,
            request.idempotency_key,
        ),
        "attempted": len(items),
        "succeeded": succeeded,
        "failed": failed,
        "skipped": 0,
        "failureBuckets": buckets,
        "items": items,
    }


@router.post(r"clients/([^/]+)/workspace/backfill-imports")
def backfill_workspace_imports(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    project_id = match.group(1)
    store = LocalProjectMaterialsRepository(compatibility.runtime)
    state = store._load_project_state(project_id)  # noqa: SLF001
    count = len(state.get("documents") or {})
    return {
        "importId": f"local-reconcile:{project_id}",
        "jobId": f"local-reconcile:{project_id}",
        "sourceRoot": "current_device_managed_storage",
        "discovered": count,
        "imported": 0,
        "skipped": count,
        "state": "verified",
        "mutationExecuted": False,
        "message": "已核对当前设备受管资料；没有发现需要补写的导入记录",
    }


@router.post(r"clients/([^/]+)/workspace/context-refresh-events")
def refresh_workspace_context(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    project_id = match.group(1)
    context = compatibility.runtime.project_knowledge_context(project_id)
    state = (context.get("state") or {}).get("overall") or "empty"
    counts = context.get("counts") or {}
    return _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/projects/{project_id}/context-refresh-events",
        {
            "state": state,
            "counts": counts,
            "materialPackHash": sha256_text(
                canonical_json(
                    {
                        "projectId": project_id,
                        "state": state,
                        "counts": counts,
                    }
                )
            ),
        },
    )


@router.post(r"clients/([^/]+)/workspace/data-center-readiness/actions")
def workspace_readiness_action(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    action = _string(
        request.body.get("actionType")
        or request.body.get("action")
        or request.body.get("type")
    )
    project_id = match.group(1)
    delegated_action = (
        "reindex-vector"
        if action in {"reindex", "reindex_vector"}
        else (
            "parse-failures/retry"
            if action in {"retry_parse", "parse_retry"}
            else ""
        )
    )
    if delegated_action:
        delegated_match = re.fullmatch(
            r"clients/([^/]+)/knowledge/"
            r"(parse-failures/retry|rebuild|reindex-vector)",
            f"clients/{project_id}/knowledge/{delegated_action}",
        )
        if delegated_match is None:  # pragma: no cover - static pattern guard
            raise LocalRuntimeError(
                500,
                "readiness_action_dispatch_invalid",
                "数据中心就绪动作无法分派",
            )
        result = project_knowledge_action(
            compatibility,
            request,
            delegated_match,
        )
        return {
            "clientId": project_id,
            "action": action,
            "actionType": action,
            "affectedCount": int(
                result.get("succeeded")
                or result.get("repairedCount")
                or result.get("masterIndexed")
                or 0
            ),
            "message": result.get("message") or "数据中心动作已完成",
            "errors": result.get("failures") or [],
            **result,
        }
    if action not in {"", "refresh", "refresh_context", "verify"}:
        raise LocalRuntimeError(
            422,
            "readiness_action_unknown",
            "未知的数据中心就绪动作",
        )
    context = compatibility.runtime.project_knowledge_context(project_id)
    return {
        "clientId": project_id,
        "action": action or "refresh_context",
        "actionType": action or "refresh_context",
        "status": "completed",
        "affectedCount": 0,
        "message": "数据中心状态已刷新",
        "errors": [],
        "state": (context.get("state") or {}).get("overall") or "empty",
        "counts": context.get("counts") or {},
    }


@router.post(r"digital-assets/organization-dna/refresh")
def refresh_organization_dna(
    compatibility: Any,
    request: UiRequest,
    _: re.Match[str],
) -> dict[str, Any]:
    compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/intelligence-growth/command",
        payload={
            "resourcePath": "intelligence/refresh",
            "method": "POST",
            "payload": {},
        },
        idempotency_key=request.idempotency_key,
    )
    return {
        "status": "completed",
        "dna": _cloud_query(
            compatibility,
            "/api/v2/workbench/organization-dna",
        ),
    }


@router.post(r"report-artifacts/([^/]+)/render")
def render_report(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    report_id = match.group(1)
    report = _cloud_query(
        compatibility,
        f"/api/v2/workbench/reports/{report_id}",
    )
    latest = report.get("latest") or {}
    output_format = _string(request.query.get("format")) or "docx"
    export_grant = _cloud_command(
        compatibility,
        request,
        "POST",
        f"/api/v2/workbench/reports/{report_id}/export-grants",
        {"exportKind": output_format},
    )
    rendered = LocalProjectMaterialsRepository(
        compatibility.runtime
    ).render_report(
        report_id=report_id,
        report_version=int(latest.get("version") or 1),
        title=_string(report.get("title")) or "报告",
        markdown=_string(latest.get("content_markdown")),
        output_format=output_format,
    )
    return {**rendered, "exportGrant": export_grant}


@router.post(r"reports/draft-blueprint")
def draft_report_blueprint(
    compatibility: Any,
    request: UiRequest,
    _: re.Match[str],
) -> dict[str, Any]:
    project_id = _string(
        request.body.get("client_id") or request.body.get("clientId")
    )
    event_line_id = _string(request.body.get("event_line_id"))
    event_line: Mapping[str, Any] = {}
    if event_line_id:
        event_line_result = compatibility.runtime.cloud_query(
            f"/api/v2/gc06/event-lines/{quote(event_line_id, safe='')}"
        )
        event_line = (
            event_line_result.get("eventLine")
            if isinstance(event_line_result, Mapping)
            else None
        )
        event_line_project_id = _string(
            (event_line or {}).get("clientId")
            or (event_line or {}).get("primaryClientId")
        )
        if not event_line_project_id:
            raise LocalRuntimeError(
                409,
                "report_event_line_project_missing",
                "当前事件线没有可用的所属项目，请刷新事件线后重试",
            )
        if project_id and project_id != event_line_project_id:
            raise LocalRuntimeError(
                409,
                "report_event_line_project_mismatch",
                "事件线所属项目已经变化，请刷新事件线后重试",
            )
        project_id = event_line_project_id
    if not project_id:
        raise LocalRuntimeError(
            422,
            "report_project_required",
            "报告蓝图需要明确选择项目",
        )
    project_result = compatibility.runtime.cloud_query(
        f"/api/v2/domain/project-materials/projects/{project_id}"
    )
    project = project_result.get("project") if isinstance(project_result, Mapping) else None
    if project is None:
        raise LocalRuntimeError(404, "project_missing", "当前工作空间没有该项目")
    if not event_line_id:
        raise LocalRuntimeError(
            422,
            "report_event_line_required",
            "请从事件线生成报告骨架",
        )
    narrative = LocalGC06PlanningProjection(
        compatibility.runtime
    ).load_event_line_narrative(event_line_id)
    if (
        not isinstance(narrative, Mapping)
        or narrative.get("outputKind") != "formal_mainline"
        or narrative.get("availabilityStatus") == "blocked"
        or bool(narrative.get("isStale"))
    ):
        raise LocalRuntimeError(
            409,
            "report_formal_mainline_required",
            "请先生成有效的正式主线，再生成报告骨架",
        )
    narrative_event_line_version = int(narrative.get("eventLineVersion") or 0)
    current_event_line_version = int(event_line.get("version") or 0)
    if (
        narrative_event_line_version
        and current_event_line_version
        and narrative_event_line_version != current_event_line_version
    ):
        raise LocalRuntimeError(
            409,
            "report_formal_mainline_stale",
            "事件线内容已经变化，请先重新生成正式主线",
        )
    intent_hint = _string(request.body.get("intent_hint"))
    audience_hint = _string(request.body.get("audience_hint")) or "项目团队"
    tone_hint = _string(request.body.get("tone_hint")) or "专业、准确"
    period_start = _string(request.body.get("period_start"))
    period_end = _string(request.body.get("period_end"))
    try:
        project_knowledge = compatibility.runtime.project_knowledge_context(project_id)
    except (AttributeError, LocalRuntimeError):
        project_knowledge = {}
    completion = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是益语智库的项目报告骨架 Agent。正式主线是报告骨架唯一的叙事骨架："
            "章节必须沿主线中的阶段、转折和当前所处位置展开，不能另起一套项目叙事。"
            "事件线目标、背景与项目知识只用于帮助理解主线和补足章节关注点，不得覆盖、"
            "改写或绕开主线；不得补造事实。用户填写的‘这份报告需要回答什么’是报告意图，"
            "不是报告标题。标题必须是自然、正式、简明的报告名称，不得照抄问题，不得写成"
            "问句或操作指令。根据报告意图、目标读者和基调，生成2至6个有真实逻辑顺序的"
            "章节；每章说明该章要基于主线回答什么，不预写正文。只输出JSON："
            '{"title":"报告标题","subtitle":"可为空","inferredTheme":"核心主题",'
            '"sections":[{"title":"章节标题","goal":"本章要回答的问题"}],'
            '"openQuestions":[]}。不要输出代码围栏或解释。'
        ),
        prompt=json.dumps(
            {
                "project": {
                    "id": project_id,
                    "name": project.get("name"),
                },
                "eventLine": {
                    "id": event_line_id,
                    "name": event_line.get("name"),
                    "goal": event_line.get("goal"),
                    "background": event_line.get("background"),
                },
                "formalMainline": {
                    "headline": narrative.get("headline"),
                    "opening": narrative.get("opening"),
                    "nodes": narrative.get("nodes") or [],
                    "closing": narrative.get("closing"),
                },
                "reportRequest": {
                    "periodStart": period_start,
                    "periodEnd": period_end,
                    "questionToAnswer": intent_hint,
                    "audience": audience_hint,
                    "tone": tone_hint,
                },
                "projectKnowledgeSupplement": project_knowledge,
            },
            ensure_ascii=False,
        )[:48_000],
        creativity_mode="strict",
        capability="event_line_report_blueprint",
        read_timeout_seconds=120.0,
        max_output_tokens=2_500,
    )
    raw_blueprint = _string(completion.get("content"))
    if raw_blueprint.startswith("```"):
        raw_blueprint = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", raw_blueprint, flags=re.I | re.S
        )
    try:
        parsed_blueprint = json.loads(raw_blueprint)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        match = re.search(r"\{[\s\S]*\}", raw_blueprint)
        try:
            parsed_blueprint = json.loads(match.group(0)) if match else None
        except (TypeError, ValueError, json.JSONDecodeError) as nested_exc:
            raise LocalRuntimeError(
                502,
                "report_blueprint_agent_invalid",
                "模型没有返回可用的报告骨架，请重试",
            ) from nested_exc
        if not isinstance(parsed_blueprint, Mapping):
            raise LocalRuntimeError(
                502,
                "report_blueprint_agent_invalid",
                "模型没有返回可用的报告骨架，请重试",
            ) from exc
    if not isinstance(parsed_blueprint, Mapping):
        raise LocalRuntimeError(
            502,
            "report_blueprint_agent_invalid",
            "模型没有返回可用的报告骨架，请重试",
        )
    title = _string(parsed_blueprint.get("title"))[:80]
    if not title or (intent_hint and title == intent_hint):
        raise LocalRuntimeError(
            502,
            "report_blueprint_title_invalid",
            "模型没有形成合适的报告标题，请重试",
        )
    sections: list[dict[str, Any]] = []
    for raw_section in list(parsed_blueprint.get("sections") or [])[:6]:
        if not isinstance(raw_section, Mapping):
            continue
        section_title = _string(raw_section.get("title"))[:80]
        section_goal = _string(raw_section.get("goal"))[:240]
        if not section_title or not section_goal:
            continue
        sections.append(
            {
                "level": 1,
                "title": section_title,
                "goal": section_goal,
                "data_sources": [
                    "formal_mainline",
                    "event_line_evidence",
                    "project_knowledge_context",
                ],
                "chart_hints": [],
                "citation_budget": 5,
                "estimated_words": 800,
            }
        )
    if len(sections) < 2:
        raise LocalRuntimeError(
            502,
            "report_blueprint_sections_invalid",
            "模型没有形成完整的报告骨架，请重试",
        )
    now = _now()
    blueprint = {
        "title": title,
        "subtitle": _string(parsed_blueprint.get("subtitle"))[:120] or None,
        "report_kind": "strategy_report",
        "audience": audience_hint,
        "tone": tone_hint,
        "period_start": period_start,
        "period_end": period_end,
        "sections": sections,
        "inferred_theme": _string(parsed_blueprint.get("inferredTheme"))[:120]
        or title,
        "confidence": 0.8,
        "open_questions_for_human": [
            _string(value)[:160]
            for value in list(parsed_blueprint.get("openQuestions") or [])[:3]
            if _string(value)
        ],
        "event_line_id": event_line_id or None,
        "client_id": project_id,
        "generated_at": now,
    }
    report_id = new_id()
    working_document_ids = list(
        dict.fromkeys(
            _string(value)
            for value in request.body.get("workingDocumentIds") or []
            if _string(value)
        )
    )[:8]
    active_skill_ids = list(
        dict.fromkeys(
            _string(value)
            for value in request.body.get("activeSkillIds") or []
            if _string(value)
        )
    )[:5]
    draft = {
        "id": report_id,
        "client_id": project_id,
        "event_line_id": event_line_id or None,
        "period_start": blueprint["period_start"] or None,
        "period_end": blueprint["period_end"] or None,
        "intent_hint": request.body.get("intent_hint"),
        "status": "blueprint_pending",
        "blueprint": blueprint,
        "sections_status": ["pending" for _ in blueprint["sections"]],
        "sections": [None for _ in blueprint["sections"]],
        "body_markdown": "",
        "warnings": [],
        "source_set_id": _string(narrative.get("sourceSetId")),
        "narrative_id": report_id,
        "narrative_rev": int(narrative.get("rev") or 0),
        "event_line_version": current_event_line_version,
        "input_fingerprint": "",
        "artifact": None,
        "saved_at": None,
        "error_message": None,
        "output_files": {},
        "total_llm_tokens": 0,
        "working_document_ids": working_document_ids,
        "active_skill_ids": active_skill_ids,
        "template_manifest": {
            "templateId": "event_line_mainline_report_v2",
            "templateVersion": 2,
            "templateKind": "report_blueprint",
        },
        "source_manifest": [],
        "agent_manifest": [],
        "created_at": now,
        "updated_at": now,
    }
    return LocalProjectMaterialsRepository(
        compatibility.runtime
    ).save_report_draft(project_id, draft)


@router.post(r"reports/([^/]+)/draft-sections")
def draft_report_sections(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    report_id = match.group(1)
    store = LocalProjectMaterialsRepository(compatibility.runtime)
    draft = store.report_draft(report_id)
    blueprint = draft.get("blueprint") or {}
    project_id = _string(draft.get("client_id"))
    _require_project_read(compatibility, project_id)
    context = compatibility.runtime.project_knowledge_context(project_id)
    event_line_id = _string(draft.get("event_line_id"))
    formal_mainline = (
        LocalGC06PlanningProjection(compatibility.runtime).load_event_line_narrative(
            event_line_id
        )
        if event_line_id
        else None
    )
    if not isinstance(formal_mainline, Mapping) or formal_mainline.get("outputKind") != "formal_mainline":
        raise LocalRuntimeError(
            409,
            "report_formal_mainline_required",
            "正式主线已不可用，请先重新生成报告骨架",
        )
    if (
        _string(draft.get("source_set_id"))
        and _string(formal_mainline.get("sourceSetId")) != _string(draft.get("source_set_id"))
    ):
        raise LocalRuntimeError(
            409,
            "report_blueprint_mainline_changed",
            "正式主线已经变化，请先重新生成报告骨架",
        )
    working_document_ids = [
        _string(value)
        for value in draft.get("working_document_ids") or []
        if _string(value)
    ][:8]
    source_manifest: list[dict[str, Any]] = []
    source_manifest.append(
        {
            "sourceId": _string(formal_mainline.get("sourceSetId")) or event_line_id,
            "sourceType": "formal_mainline",
            "title": _string(formal_mainline.get("headline")) or "正式主线",
            "version": int(formal_mainline.get("rev") or 1),
            "contentHash": _string(formal_mainline.get("sourceSetId")) or None,
        }
    )
    local_documents: list[dict[str, Any]] = []
    for document_id in working_document_ids:
        local = store.document_text(document_id)
        if _string(local.get("projectId")) != project_id:
            raise LocalRuntimeError(
                409,
                "report_document_project_mismatch",
                "报告引用资料不属于当前项目，请刷新后重试",
            )
        content = _string(local.get("content"))
        if not content:
            continue
        local_documents.append({**local, "content": content})
        source_manifest.append(
            {
                "sourceId": document_id,
                "sourceType": "member_local_document",
                "title": _string(local.get("title")) or document_id,
                "version": 1,
                "contentHash": local.get("contentHash"),
                "materialBoundary": "local_body_not_uploaded",
            }
        )
    agent_skills: list[dict[str, Any]] = []
    for skill_id in [
        _string(value)
        for value in draft.get("active_skill_ids") or []
        if _string(value)
    ][:5]:
        _legacy_style, agent_skill = _selected_style_or_agent_skill(
            compatibility,
            skill_id,
        )
        if agent_skill is None:
            raise LocalRuntimeError(
                409,
                "report_agent_skill_invalid",
                "项目报告只能使用已启用的项目工作台 Skill",
            )
        agent_skills.append(agent_skill)
    agent_manifest = [
        {
            "sourceId": _string(item.get("skillId")),
            "sourceType": "agent_skill",
            "title": _string(item.get("shortName")),
            "version": int(item.get("version") or 1),
            "contentHash": item.get("contentHash"),
        }
        for item in agent_skills
    ]
    plans = list(blueprint.get("sections") or [])
    sections = list(draft.get("sections") or [])
    statuses = list(draft.get("sections_status") or [])
    sections.extend([None] * max(0, len(plans) - len(sections)))
    statuses.extend(["pending"] * max(0, len(plans) - len(statuses)))
    raw_indices = request.body.get("section_indices")
    indices = (
        [int(value) for value in raw_indices]
        if isinstance(raw_indices, list)
        else list(range(len(plans)))
    )
    if any(index < 0 or index >= len(plans) for index in indices):
        raise LocalRuntimeError(
            422,
            "report_section_index_invalid",
            "报告章节序号无效",
        )
    overall_feedback = _string(request.body.get("overall_feedback"))
    section_feedback = request.body.get("section_feedback") or {}
    if not isinstance(section_feedback, Mapping):
        section_feedback = {}
    for index in indices:
        plan = plans[index]
        statuses[index] = "drafting"
        store.save_report_draft(
            project_id,
            {
                **draft,
                "status": "drafting",
                "sections": sections,
                "sections_status": statuses,
            },
        )
        feedback = _string(
            section_feedback.get(index)
            or section_feedback.get(str(index))
        )
        try:
            query = " ".join(
                value
                for value in (
                    _string(blueprint.get("title")),
                    _string(blueprint.get("inferred_theme")),
                    _string(plan.get("title")),
                    _string(plan.get("goal")),
                    overall_feedback,
                    feedback,
                )
                if value
            )
            local_context_parts: list[str] = []
            remaining = 36_000
            for local in local_documents:
                if remaining <= 0:
                    break
                excerpt = store.select_relevant_excerpt(
                    _string(local.get("content")),
                    query,
                    max_chars=min(8_000, remaining),
                )
                if not excerpt:
                    continue
                remaining -= len(excerpt)
                local_context_parts.append(
                    f"【本机原件：{_string(local.get('title'))}】\n{excerpt}"
                )
            skill_context = "\n\n".join(
                f"【写作模板：{_string(item.get('shortName'))}】\n"
                f"{_string(item.get('renderedInstruction'))}"
                for item in agent_skills
                if _string(item.get("renderedInstruction"))
            )
            result = compatibility.runtime.private_ai_completion(
                system_prompt=(
                    "你是益语智库的项目工作台 Agent。正式主线是整份报告唯一的叙事骨架，"
                    "正文必须沿主线的阶段、转折和当前所处位置展开，不能另起炉灶。只写指定章节；依据下方明确"
                    "提供的本机原件片段、组织共享知识与客户档案，明确区分事实、"
                    "判断和建议，不声称读取未提供的源文件。"
                    + ("\n本次启用写作模板：\n" + skill_context if skill_context else "")
                ),
                prompt=(
                    "整份报告蓝图：\n"
                    + json.dumps(blueprint, ensure_ascii=False)
                    + "\n本章计划：\n"
                    + json.dumps(plan, ensure_ascii=False)
                    + "\n正式主线：\n"
                    + json.dumps(formal_mainline, ensure_ascii=False)
                    + "\n整稿修改意见：\n"
                    + overall_feedback
                    + "\n本章修改意见：\n"
                    + feedback
                    + "\n项目知识上下文：\n"
                    + json.dumps(context, ensure_ascii=False)
                    + (
                        "\n本机原件相关片段（正文只在本机进入模型，不上传组织云）：\n"
                        + "\n\n".join(local_context_parts)
                        if local_context_parts
                        else "\n本次未选择本机原件，只能使用组织共享知识。"
                    )
                    + "\n请只输出本章 Markdown 正文，不重复章节标题。"
                ),
                creativity_mode="strict",
                read_timeout_seconds=100.0,
            )
            markdown = _string(result.get("content"))
            if not markdown:
                raise LocalRuntimeError(
                    502,
                    "report_section_empty",
                    f"第 {index + 1} 章未生成正文",
                )
            sections[index] = {
                "plan": plan,
                "markdown": markdown,
                "citations": [],
                "charts": [],
                "data_source_annotation": "严格项目知识上下文",
                "confidence": 0.6,
                "warnings": [],
            }
            statuses[index] = "done"
        except Exception:
            statuses[index] = "failed"
            store.save_report_draft(
                project_id,
                {
                    **draft,
                    "status": "failed",
                    "sections": sections,
                    "sections_status": statuses,
                    "error_message": f"第 {index + 1} 章生成失败",
                },
            )
            raise
    body_markdown = "\n\n".join(
        (
            f"## {_string(item.get('plan', {}).get('title'))}\n\n"
            f"{_string(item.get('markdown'))}"
        )
        for item in sections
        if isinstance(item, Mapping)
    )
    status = (
        "body_ready"
        if statuses and all(value == "done" for value in statuses)
        else "drafting"
    )
    return store.save_report_draft(
        project_id,
        {
            **draft,
            "status": status,
            "sections": sections,
            "sections_status": statuses,
            "body_markdown": body_markdown,
            "error_message": None,
            "source_manifest": source_manifest + agent_manifest,
            "agent_manifest": agent_manifest,
        },
    )


@router.post(r"analysis-tools/runs")
def run_analysis_tool(
    compatibility: Any,
    request: UiRequest,
    _: re.Match[str],
) -> dict[str, Any]:
    title = _string(request.body.get("title")) or "组织 AI 分析"
    input_text = _string(request.body.get("inputText"))
    if not input_text:
        raise LocalRuntimeError(422, "analysis_input_required", "请输入分析内容")
    saved = compatibility.runtime.workbench_chat(
        project_id=None,
        question=f"{title}\n\n{input_text}",
        mode="balanced",
        source_manifest_extra={
            "operationKey": f"{request.idempotency_key}:tool-answer",
            "templateId": request.body.get("templateId"),
            "parentRunId": request.body.get("parentRunId"),
            "analysisTitle": title,
        },
        idempotency_key=f"{request.idempotency_key}:tool-answer",
    )
    answer = saved.get("answer") or {}
    return {
        "id": answer.get("answerId"),
        "templateId": request.body.get("templateId") or "",
        "title": title,
        "inputText": input_text,
        "output": {
            "content": answer.get("answerMarkdown") or "",
            "judgment": _string(answer.get("answerMarkdown")).splitlines()[0]
            if _string(answer.get("answerMarkdown"))
            else "",
            "analysis": answer.get("answerMarkdown") or "",
            "actions": "",
            "timeline": "",
        },
        "parentRunId": request.body.get("parentRunId"),
        "createdAt": answer.get("createdAt") or _now(),
        "status": "success",
    }


@router.get(r"analysis-tools/fundraising/runs/([^/]+)/comparison")
def fundraising_run_comparison(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    run_id = match.group(1)
    snapshot = compatibility.runtime.business_snapshot(refresh=False)
    current = next(
        (
            item
            for item in snapshot.get("aiAnswers") or []
            if _string(item.get("answerId")) == run_id
        ),
        None,
    )
    if current is None:
        raise LocalRuntimeError(404, "analysis_run_missing", "分析记录不存在")
    parent_id = _string(
        (current.get("sourceManifest") or {}).get("parentRunId")
    )
    parent = next(
        (
            item
            for item in snapshot.get("aiAnswers") or []
            if _string(item.get("answerId")) == parent_id
        ),
        None,
    )
    if parent is None:
        return {
            "currentRunId": run_id,
            "previousRunId": None,
            "resultChanges": [],
            "learningChanges": [],
            "resolvedIssues": [],
            "newIssues": [],
            "repeatedIssues": [],
        }
    current_lines = set(_lines(current.get("answerMarkdown")))
    parent_lines = set(_lines(parent.get("answerMarkdown")))
    return {
        "currentRunId": run_id,
        "previousRunId": parent_id,
        "resultChanges": sorted(current_lines - parent_lines),
        "learningChanges": [],
        "resolvedIssues": sorted(parent_lines - current_lines),
        "newIssues": sorted(current_lines - parent_lines),
        "repeatedIssues": sorted(current_lines & parent_lines),
    }


@router.post(r"analysis-tools/fundraising/dna/web-drafts")
def fundraising_web_draft(
    compatibility: Any,
    request: UiRequest,
    _: re.Match[str],
) -> dict[str, Any]:
    group_key = _string(request.body.get("groupKey"))
    if group_key not in {
        "platform_fundraising",
        "monthly_donor",
        "key_person",
    }:
        raise LocalRuntimeError(
            422,
            "fundraising_dna_group_invalid",
            "募资 DNA 对象类型无效",
        )
    label = _string(request.body.get("label"))
    query = _string(request.body.get("searchQuery"))
    if not label or not query:
        raise LocalRuntimeError(
            422,
            "fundraising_web_query_required",
            "请填写调研对象和公开搜索词",
        )
    operations = LocalPlatformOperationRepository(compatibility.runtime)
    started = operations.begin(
        idempotency_key=request.idempotency_key,
        command_type="fundraising_dna.public_web_draft",
        aggregate_type="fundraising_dna_draft",
        aggregate_id=sha256_text(f"{group_key}:{label}")[:24],
        payload={
            "groupKey": group_key,
            "labelHash": sha256_text(label),
            "queryHash": sha256_text(query),
        },
        initial_result={
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    if started.get("idempotentReplay"):
        output = started.get("output")
        if isinstance(output, Mapping) and output.get("draftId"):
            current = _cloud_query(
                compatibility,
                (
                    "/api/v2/workbench/libraries/fundraising_dna/"
                    f"{output['draftId']}"
                ),
            )
            record = _deep_dna_dto(current)
            return {
                "id": record["id"],
                "groupKey": record["groupKey"],
                "label": record["label"],
                "searchQuery": record.get("searchQuery") or query,
                "draftRecord": record,
                "previewSources": record["sources"],
                "createdAt": record["createdAt"],
                "updatedAt": record["updatedAt"],
            }
        if isinstance(output, Mapping) and not int(
            output.get("sourceCount") or 0
        ):
            raise LocalRuntimeError(
                404,
                "fundraising_web_results_empty",
                "公开搜索已完成，本轮没有找到可引用资料",
            )
        raise LocalRuntimeError(
            409,
            "fundraising_web_draft_replay_incomplete",
            "相同网页调研请求尚未完成，请重试",
        )
    sandbox_id = str(started["sandboxId"])
    try:
        items = capture_public_web(query, max_results=8)
    except PublicCaptureError as exc:
        operations.update(
            operation_id=str(started["operationId"]),
            state="failed_retryable" if exc.retryable else "blocked",
            result_patch={"sourceCount": 0},
            error_code=exc.code,
            error_message=exc.message,
            captured_sandbox_id=sandbox_id,
        )
        raise LocalRuntimeError(
            503 if exc.retryable else 422,
            exc.code,
            exc.message,
        ) from exc
    if not items:
        operations.update(
            operation_id=str(started["operationId"]),
            state="completed",
            result_patch={
                "output": {
                    "draftId": None,
                    "sourceCount": 0,
                    "sourceHashes": [],
                }
            },
            captured_sandbox_id=sandbox_id,
        )
        raise LocalRuntimeError(
            404,
            "fundraising_web_results_empty",
            "公开搜索已完成，本轮没有找到可引用资料",
        )
    sources = [
        {
            "id": f"web_{item.content_hash[:16]}",
            "kind": "web",
            "title": item.title,
            "excerpt": item.summary,
            "sourceUrl": item.source_url,
            "fileName": None,
            "filePath": None,
            "createdAt": item.captured_at,
        }
        for item in items
    ]
    positive_titles = [
        item.title for item in items if item.sentiment == "positive"
    ]
    negative_titles = [
        item.title for item in items if item.sentiment == "negative"
    ]
    raw_content = "\n\n".join(
        (
            f"来源：{item.title}\n"
            f"摘要：{item.summary}\n"
            f"链接：{item.source_url}"
        )
        for item in items
    )
    try:
        saved = _library_upsert(
            compatibility,
            request,
            kind="fundraising_dna",
            extra={
                "groupKey": group_key,
                "label": label,
                "title": label,
                "status": "draft",
                "sourceKind": "web",
                "identitySummary": "；".join(
                    item.summary[:180] for item in items[:3]
                ),
                "corePreferences": [item.title for item in items[:5]],
                "supportTriggers": positive_titles[:5],
                "redFlags": negative_titles[:5],
                "evidencePreferences": list(
                    dict.fromkeys(item.source_name for item in items)
                )[:8],
                "voiceStyle": [],
                "commonQuestions": [
                    "哪些公开事实仍需要一手材料核验？",
                    "哪些判断可以转化为募资沟通假设？",
                ],
                "sources": sources,
                "confidenceScore": min(0.45 + len(items) * 0.05, 0.85),
                "confidenceLevel": (
                    "high"
                    if len(items) >= 7
                    else "medium"
                    if len(items) >= 3
                    else "low"
                ),
                "authorizationStatus": "public",
                "rawContent": raw_content,
                "searchQuery": query,
                "externalCollectionExecuted": True,
                "modelAnalysisExecuted": False,
                "sourceBodyStored": False,
            },
        )
    except LocalRuntimeError as exc:
        operations.update(
            operation_id=str(started["operationId"]),
            state=(
                "failed_retryable"
                if exc.status_code >= 500
                else "blocked"
            ),
            result_patch={"sourceCount": len(sources)},
            error_code=exc.code,
            error_message=exc.message,
            captured_sandbox_id=sandbox_id,
        )
        raise
    record = _deep_dna_dto(saved)
    operations.update(
        operation_id=str(started["operationId"]),
        state="completed",
        result_patch={
            "output": {
                "draftId": record["id"],
                "sourceCount": len(sources),
                "sourceHashes": [
                    item.content_hash for item in items
                ],
            }
        },
        captured_sandbox_id=sandbox_id,
    )
    return {
        "id": record["id"],
        "groupKey": record["groupKey"],
        "label": record["label"],
        "searchQuery": query,
        "draftRecord": record,
        "previewSources": sources,
        "createdAt": record["createdAt"],
        "updatedAt": record["updatedAt"],
    }


@router.post(r"writing-skills/distill")
def distill_writing_skill(
    compatibility: Any,
    request: UiRequest,
    _: re.Match[str],
) -> dict[str, Any]:
    samples = [
        _string(value)
        for value in request.body.get("samples") or []
        if _string(value)
    ]
    if not samples:
        raise LocalRuntimeError(422, "writing_samples_required", "请提供写作样本")
    result = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是写作风格提炼器。只总结提供样本中的可观察写作特征，"
            "输出 Markdown 规则，不补造作者身份或背景。"
        ),
        prompt="\n\n--- 样本 ---\n\n".join(samples),
        creativity_mode="strict",
    )
    return {
        "distilledMd": result["content"],
        "samplesProcessed": len(samples),
        "suggestedName": _string(request.body.get("skillName")) or "提炼写作风格",
    }


@router.get(r"clients/([^/]+)/template-fill-runs/([^/]+)")
def get_template_fill_run(
    compatibility: Any,
    _: UiRequest,
    match: re.Match[str],
) -> dict[str, Any]:
    return LocalProjectMaterialsRepository(
        compatibility.runtime
    ).template_fill_run(match.group(1), match.group(2))


_FROZEN_CAPABILITIES: tuple[tuple[str, str, str, str], ...] = (
)


def _register_frozen_capability(
    method: str,
    pattern: str,
    capability: str,
    evidence: str,
) -> None:
    def frozen_capability(
        _: Any,
        request: UiRequest,
        __: re.Match[str],
    ) -> Any:
        raise LocalRuntimeError(
            501,
            f"{capability}_not_connected",
            f"{evidence}；路径={request.path}",
        )

    frozen_capability.__name__ = f"frozen_{capability}"
    router.route(method, pattern)(frozen_capability)


for (
    _frozen_method,
    _frozen_pattern,
    _frozen_capability,
    _frozen_evidence,
) in _FROZEN_CAPABILITIES:
    _register_frozen_capability(
        _frozen_method,
        _frozen_pattern,
        _frozen_capability,
        _frozen_evidence,
    )
