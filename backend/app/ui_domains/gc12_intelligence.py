"""GC-12 renderer adapter backed by the strict intelligence objects."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlparse

from strict_common.ids import sha256_text, utc_now

from ..intelligence_capture_local import (
    PublicCaptureError,
    PublicCaptureItem,
    capture_public_web,
    enrich_public_capture_item,
)
from ..runtime import LocalRuntimeError
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("gc12_intelligence", pin_workspace=True)
_ROOT = "/api/v2/domain/project-materials"


def _segment(value: str) -> str:
    return quote(value, safe="")


def _require_workspace(compatibility: Any) -> None:
    compatibility.runtime._current_context(require_ready=True)  # noqa: SLF001


_PROJECT_PLACEHOLDER_MARKERS = (
    "等待导入",
    "系统将自动",
    "暂无资料",
    "尚未上传",
    "未接通",
)
_DIRECT_NEWS_REQUEST_MARKERS = (
    "本项目动态",
    "本机构动态",
    "直接提到",
    "相关新闻",
    "本项目新闻",
    "本机构新闻",
)


@dataclass(frozen=True)
class _ResearchPlan:
    mode: str
    queries: tuple[str, ...]
    include_concepts: tuple[str, ...]
    exclude_concepts: tuple[str, ...]
    direct_mention_policy: str
    coverage_target: int
    planning_mode: str


def _json_object(raw: Any) -> dict[str, Any] | None:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def _unique_text(values: Iterable[Any], *, limit: int = 20) -> list[str]:
    result: list[str] = []
    for raw in values:
        value = " ".join(str(raw or "").split()).strip(" ，。；、")
        if not value or value in result:
            continue
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _project_names(project: Mapping[str, Any]) -> list[str]:
    values = [project.get("name"), project.get("alias")]
    name = str(project.get("name") or "").strip()
    for suffix in ("公益基金会", "基金会", "研究院", "实验室", "智库", "中心", "集团", "公司"):
        if name.endswith(suffix) and len(name) - len(suffix) >= 2:
            values.append(name[: -len(suffix)])
            break
    return _unique_text(values, limit=6)


def _clean_profile_keywords(
    values: Iterable[Any], *, project_names: Iterable[str]
) -> list[str]:
    names = list(project_names)
    result: list[str] = []
    for raw in values:
        value = " ".join(str(raw or "").split()).strip(" ，。；、")
        if (
            len(value) < 2
            or len(value) > 32
            or any(marker in value for marker in _PROJECT_PLACEHOLDER_MARKERS)
            or value in result
        ):
            continue
        # Remove historical arbitrary n-grams cut from the organization name.
        if any(value in name and value != name and value not in names for name in names):
            continue
        result.append(value)
        if len(result) >= 24:
            break
    return result


def _research_context(
    compatibility: Any,
    *,
    project_id: str,
    project: Mapping[str, Any],
) -> dict[str, Any]:
    names = _project_names(project)
    keywords: list[str] = []
    try:
        profiles = compatibility.runtime.cloud_query(
            "/api/v2/domain/task-planning/project-keyword-profiles"
        )
        if isinstance(profiles, list):
            profile = next(
                (
                    item
                    for item in profiles
                    if isinstance(item, Mapping)
                    and str(item.get("clientId") or "") == project_id
                ),
                None,
            )
            if isinstance(profile, Mapping):
                keywords = _clean_profile_keywords(
                    profile.get("keywords") or [], project_names=names
                )
    except Exception:
        keywords = []
    strategy: dict[str, Any] = {}
    try:
        raw = compatibility.runtime.cloud_query(
            f"{_ROOT}/intelligence/strategy-extract",
            query={"clientId": project_id},
        )
        if isinstance(raw.get("extract"), Mapping):
            strategy = dict(raw["extract"])
    except Exception:
        strategy = {}
    return {
        "projectId": project_id,
        "name": str(project.get("name") or "当前项目"),
        "names": names,
        "domain": str(project.get("domain") or ""),
        "summary": str(project.get("summary") or ""),
        "keywords": keywords,
        "strategicObjective": str(strategy.get("strategicObjective") or ""),
        "methodology": str(strategy.get("methodology") or ""),
    }


def _focus_atoms(values: Iterable[str]) -> list[str]:
    atoms: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        value = re.sub(r"^(?:优先|重点)?(?:关注|看)?\s*", "", value)
        value = re.sub(r"^和(.+?)有关的", r"\1 ", value)
        for item in re.split(r"[，,、；;。\n]+", value):
            item = item.strip(" ‘“’”")
            if len(item) >= 2 and item not in atoms:
                atoms.append(item)
    return atoms[:12]


_TOPIC_CATEGORY_WORDS = re.compile(
    r"(?:政策|监管|资助项目|资助|申报|行业研究|研究报告|研究|实践案例|案例|机会|项目)"
)
_GENERIC_TOPICS = frozenset(
    {"儿童", "公益", "基金会", "新闻", "项目", "政策", "资助", "研究", "行业"}
)
_LOW_VALUE_HOSTS = (
    "baike.baidu.com",
    "baike.sogou.com",
    "zhidao.baidu.com",
    "v.qq.com",
    "iqiyi.com",
    "youku.com",
    "book118.com",
    "doc88.com",
    "renrendoc.com",
    "docin.com",
    "wenku.so.com",
)
_LOW_VALUE_TITLE_MARKERS = (
    "的意思",
    "的解释",
    "的拼音",
    "的部首",
    "的笔顺",
    "怎么读",
    "几岁到几岁",
    "百度百科",
    "_百科",
    "少儿频道",
    "儿童频道",
    "动画片",
    "在线观看",
    "原创力文档",
    "人人文库",
)
_INTELLIGENCE_SIGNALS = (
    "政策",
    "监管",
    "条例",
    "规划",
    "指南",
    "标准",
    "资助",
    "基金",
    "申报",
    "招募",
    "研究",
    "报告",
    "调查",
    "实践",
    "案例",
    "服务体系",
    "试点",
)


def _topic_phrases(context: Mapping[str, Any], focus: Iterable[str]) -> list[str]:
    topics: list[str] = []
    for atom in _focus_atoms(focus):
        value = _TOPIC_CATEGORY_WORDS.sub(" ", atom)
        value = "".join(value.split()).strip("，。；、")
        if len(value) >= 4 and value not in _GENERIC_TOPICS and value not in topics:
            topics.append(value)
    names = _unique_text(context.get("names") or [], limit=6)
    for value in _clean_profile_keywords(
        context.get("keywords") or [], project_names=names
    ):
        compact = "".join(value.split()).strip("，。；、")
        if (
            len(compact) >= 4
            and len(compact) <= 18
            and compact not in _GENERIC_TOPICS
            and compact not in topics
        ):
            topics.append(compact)
    if not topics:
        topics.extend(("公益组织发展", "公益项目资助"))
    return topics[:6]


def _contains_anchor(text: str, anchors: Iterable[str]) -> bool:
    compact = re.sub(r"\s+", "", text).casefold()
    return any(
        len(anchor.strip()) >= 2
        and re.sub(r"\s+", "", anchor).casefold() in compact
        for anchor in anchors
    )


def _is_low_value_result(item: PublicCaptureItem) -> bool:
    host = (urlparse(item.source_url).hostname or "").casefold().removeprefix("www.")
    title = item.title.casefold()
    return any(host == value or host.endswith(f".{value}") for value in _LOW_VALUE_HOSTS) or any(
        marker.casefold() in title for marker in _LOW_VALUE_TITLE_MARKERS
    )


def _fallback_queries(
    *,
    mode: str,
    context: Mapping[str, Any],
    focus: list[str],
) -> list[str]:
    names = _unique_text(context.get("names") or [], limit=5)
    keywords = _clean_profile_keywords(
        context.get("keywords") or [], project_names=names
    )
    if mode == "brand":
        anchors = [value for value in names if len(value.strip()) >= 2]
        target = anchors[0] if anchors else str(context.get("name") or "当前项目")
        queries = [
            f'"{target}" 评价 报道',
            f'"{target}" 合作 成效',
            f'"{target}" 投诉 质疑 争议',
        ]
        for anchor in [*anchors[1:], *(value for value in keywords[:4] if len(value) >= 4)]:
            queries.append(f'"{anchor}" 评价 报道')
        return _unique_text(queries, limit=10)
    topics = _topic_phrases(context, focus)
    queries: list[str] = []
    for topic in topics[:4]:
        queries.extend(
            (
                f'"{topic}" 政策 监管',
                f'"{topic}" 资助 申报',
                f'"{topic}" 研究 实践案例',
            )
        )
    return _unique_text(queries, limit=12)


def _research_plan(
    compatibility: Any,
    *,
    mode: str,
    context: Mapping[str, Any],
    focus: list[str],
    excluded: list[str],
    use_model: bool = True,
) -> _ResearchPlan:
    default_policy = "require" if mode == "brand" else "exclude"
    explicit_direct_news = any(
        marker in item
        for marker in _DIRECT_NEWS_REQUEST_MARKERS
        for item in focus
    )
    if mode == "timely" and explicit_direct_news:
        default_policy = "allow"
    fallback = _fallback_queries(mode=mode, context=context, focus=focus)
    payload = {
        "mode": mode,
        "project": {
            key: context.get(key)
            for key in (
                "name",
                "names",
                "domain",
                "summary",
                "keywords",
                "strategicObjective",
                "methodology",
            )
        },
        "focus": focus,
        "exclude": excluded,
        "directMentionPolicy": default_policy,
    }
    system = (
        "你负责为公益项目制定公开网页研究检索方案。只能输出JSON，不得编造事实。"
        "品牌监测要寻找外界对主体、简称、核心项目和关键人物的真实提及，排除主体官网自述；"
        "时效情报用于拓展项目视野和机会，默认不得搜索或保留直接提到本项目的新闻，而应围绕"
        "项目议题寻找政策、资助申报、研究、同类实践与环境变化。用户自然语言中的少看、不看、"
        "排除必须转成语义规则，不得仅作字面字符串过滤。生成6至12条互补而不重复的中文搜索词。"
        "返回：queries字符串数组、includeConcepts、excludeConcepts、coverageTarget。"
    )
    parsed: dict[str, Any] | None = None
    if use_model:
        try:
            completion = compatibility.runtime.organization_ai_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.1,
                # 研究规划运行在独立的长任务通道内。这里保留足够时间让
                # 模型理解自然语言关注点；超时后仍会回退到确定性计划。
                read_timeout_seconds=35.0,
            )
            parsed = _json_object(completion.get("content"))
        except Exception:
            parsed = None
    planned_queries = _unique_text(
        parsed.get("queries") if parsed else [], limit=12
    )
    queries = planned_queries or fallback
    names = _unique_text(context.get("names") or [], limit=6)
    if mode == "timely" and default_policy == "exclude":
        # The product boundary is enforced after model planning as well: a
        # planner cannot silently re-anchor discovery on the client name.
        queries = [
            query
            for query in queries
            if not any(name and name.casefold() in query.casefold() for name in names)
        ] or fallback
    try:
        requested_target = int((parsed or {}).get("coverageTarget") or 0)
    except (TypeError, ValueError):
        requested_target = 0
    baseline_includes = (
        _unique_text(context.get("names") or [], limit=6)
        if mode == "brand"
        else _topic_phrases(context, focus)
    )
    return _ResearchPlan(
        mode=mode,
        queries=tuple(queries[:12]),
        include_concepts=tuple(
            _unique_text(
                [*((parsed or {}).get("includeConcepts") or []), *baseline_includes],
                limit=16,
            )
        ),
        exclude_concepts=tuple(
            _unique_text(
                [*((parsed or {}).get("excludeConcepts") or []), *excluded],
                limit=20,
            )
        ),
        direct_mention_policy=default_policy,
        coverage_target=max(8, min(24, requested_target or (12 if mode == "brand" else 10))),
        planning_mode="model" if planned_queries else "deterministic_fallback",
    )


def _capture_queries(plan: _ResearchPlan) -> list[PublicCaptureItem]:
    results: list[PublicCaptureItem] = []
    seen: set[str] = set()

    def run(query: str) -> list[PublicCaptureItem]:
        return capture_public_web(query, max_results=8)

    failures: list[PublicCaptureError] = []
    with ThreadPoolExecutor(max_workers=min(4, len(plan.queries) or 1)) as pool:
        futures = {pool.submit(run, query): query for query in plan.queries}
        for future in as_completed(futures):
            try:
                items = future.result()
            except PublicCaptureError as exc:
                failures.append(exc)
                continue
            for item in items:
                key = item.source_url.casefold().rstrip("/")
                if key in seen:
                    continue
                seen.add(key)
                results.append(item)
    if not results and failures:
        raise failures[0]
    return results[:48]


def _enrich_candidates(items: list[PublicCaptureItem]) -> list[PublicCaptureItem]:
    if not items:
        return []
    enriched: dict[int, PublicCaptureItem] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(items))) as pool:
        futures = {
            pool.submit(enrich_public_capture_item, item): index
            for index, item in enumerate(items[:30])
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                enriched[index] = future.result()
            except Exception:
                enriched[index] = items[index]
    return [enriched.get(index, item) for index, item in enumerate(items)]


def _mentions_project(item: PublicCaptureItem, names: Iterable[str]) -> bool:
    text = f"{item.title}\n{item.summary}\n{item.body_excerpt}".casefold()
    return any(name and name.casefold() in text for name in names)


def _judge_candidates(
    compatibility: Any,
    *,
    plan: _ResearchPlan,
    context: Mapping[str, Any],
    items: list[PublicCaptureItem],
    official_host: str,
    use_model: bool = True,
) -> tuple[list[dict[str, Any]], bool, dict[str, int]]:
    names = _unique_text(context.get("names") or [], limit=6)
    hard_filtered: list[PublicCaptureItem] = []
    rejected = {
        "direct_project_news": 0,
        "official_self_published": 0,
        "user_excluded": 0,
        "source_duplicate": 0,
        "low_relevance": 0,
        "low_value_source": 0,
        "topic_mismatch": 0,
    }
    secondary_matches: list[PublicCaptureItem] = []
    for item in items:
        host = (urlparse(item.source_url).hostname or "").casefold().removeprefix("www.")
        if official_host and host == official_host:
            rejected["official_self_published"] += 1
            continue
        if (
            plan.mode == "timely"
            and plan.direct_mention_policy == "exclude"
            and _mentions_project(item, names)
        ):
            rejected["direct_project_news"] += 1
            continue
        corpus = f"{item.title}\n{item.summary}\n{item.body_excerpt}".casefold()
        if any(
            concept and concept.casefold() in corpus
            for concept in plan.exclude_concepts
            if len(concept.strip()) >= 2
        ):
            rejected["user_excluded"] += 1
            continue
        if _is_low_value_result(item):
            rejected["low_value_source"] += 1
            continue
        required_anchors = (
            names if plan.mode == "brand" else plan.include_concepts
        )
        if not _contains_anchor(corpus, required_anchors):
            rejected["topic_mismatch"] += 1
            continue
        if plan.mode == "timely" and not any(
            signal in corpus for signal in _INTELLIGENCE_SIGNALS
        ):
            # Keep a topic-grounded secondary pool so the fast path can still
            # return useful material when a result describes the issue without
            # using a policy/report keyword. It is considered only after the
            # stronger intelligence-shaped results.
            secondary_matches.append(item)
            continue
        hard_filtered.append(item)

    if plan.mode == "timely":
        hard_filtered.extend(secondary_matches)

    evidence = [
        {
            "index": index,
            "title": item.title,
            "summary": item.summary,
            "bodyExcerpt": item.body_excerpt[:1_800],
            "source": item.source_name,
            "publishedAt": item.published_at,
        }
        for index, item in enumerate(hard_filtered[:12])
    ]
    system = (
        "你是联网研究结果编辑。只能依据候选标题和摘要判断，不得补造正文或事实。"
        "先理解研究意图，再判断来源和值不值得展示：政府、资助方、研究机构、主流媒体和可核实项目页面优先；"
        "词典百科、文库下载、概念释义、视频频道、聚合搬运和只有标题没有信息量的页面必须淘汰。"
        "品牌监测只保留确实涉及主体、简称、核心项目或关键人物的外部评价、报道、合作、成果或风险；"
        "时效情报只保留与项目议题有关但不是直接报道本项目的政策、资助机会、研究、同类实践或环境变化。"
        "严格执行include/exclude语义，并把保留结果整理成前端卡片字段。"
        "只返回JSON：items数组，每项含index、keep、relevanceReason、impact、"
        "sentiment(positive|neutral|negative)、sentimentReason、tags；理由必须具体，不得写泛泛的‘相关’。"
    )
    parsed: dict[str, Any] | None = None
    if use_model:
        try:
            completion = compatibility.runtime.organization_ai_completion(
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "mode": plan.mode,
                                "project": context.get("name"),
                                "include": plan.include_concepts,
                                "exclude": plan.exclude_concepts,
                                "directMentionPolicy": plan.direct_mention_policy,
                                "candidates": evidence,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                temperature=0.1,
                # 资讯研究与官网研究使用同一独立有界通道，不再为了迁就
                # 普通 UI 的 20 秒截止时间而压缩来源甄别与结构化整理。
                read_timeout_seconds=75.0,
            )
            parsed = _json_object(completion.get("content"))
        except Exception:
            parsed = None
    decisions = {
        int(item.get("index")): item
        for item in (parsed or {}).get("items") or []
        if isinstance(item, Mapping) and str(item.get("index", "")).isdigit()
    }
    host_counts: dict[str, int] = {}
    accepted: list[dict[str, Any]] = []
    for index, item in enumerate(hard_filtered[:12]):
        decision = decisions.get(index)
        if decision is not None and not bool(decision.get("keep")):
            rejected["low_relevance"] += 1
            continue
        host = (urlparse(item.source_url).hostname or item.source_name).casefold().removeprefix("www.")
        if host_counts.get(host, 0) >= 3:
            rejected["source_duplicate"] += 1
            continue
        host_counts[host] = host_counts.get(host, 0) + 1
        accepted.append(
            {
                **item.as_cloud_payload(),
                "sentiment": str((decision or {}).get("sentiment") or item.sentiment),
                "sentimentReason": str((decision or {}).get("sentimentReason") or item.sentiment_reason),
                "relevanceReason": str((decision or {}).get("relevanceReason") or "公开来源与当前研究目标相关"),
                "impact": str((decision or {}).get("impact") or ""),
                "tags": _unique_text((decision or {}).get("tags") or [], limit=8),
                "directProjectMention": _mentions_project(item, names),
            }
        )
        if len(accepted) >= plan.coverage_target:
            break
    return accepted, parsed is not None, rejected


def _execute_research(
    compatibility: Any,
    *,
    mode: str,
    project_id: str,
    project: Mapping[str, Any],
    focus: list[str],
    excluded: list[str],
) -> dict[str, Any]:
    context = _research_context(
        compatibility, project_id=project_id, project=project
    )
    plan = _research_plan(
        compatibility,
        mode=mode,
        context=context,
        focus=focus,
        excluded=excluded,
        use_model=True,
    )
    # 研究请求由专属有界通道承载。最多执行六组互补检索并读取优先候选
    # 的公开正文，再由模型按研究意图甄别；这与通用联网研究的流程一致，
    # 但最终结果仍落入既有 intelligence_records，不增加第二权威。
    plan = _ResearchPlan(
        mode=plan.mode,
        queries=plan.queries[:6],
        include_concepts=plan.include_concepts,
        exclude_concepts=plan.exclude_concepts,
        direct_mention_policy=plan.direct_mention_policy,
        coverage_target=min(plan.coverage_target, 12),
        planning_mode=plan.planning_mode,
    )
    captured = _capture_queries(plan)
    enriched = _enrich_candidates(captured[:20]) + captured[20:]
    official_host = (
        urlparse(str(project.get("officialWebsiteUrl") or "")).hostname or ""
    ).casefold().removeprefix("www.")
    accepted, model_executed, rejected = _judge_candidates(
        compatibility,
        plan=plan,
        context=context,
        items=enriched,
        official_host=official_host,
        use_model=True,
    )
    return {
        "plan": plan,
        "items": accepted,
        "fetchedCount": len(captured),
        "bodyFetchedCount": sum(1 for item in enriched if item.body_fetched),
        "modelAnalysisExecuted": model_executed,
        "rejectionCounts": rejected,
    }


@router.get(r"intelligence/work-objects")
def work_objects(compatibility: Any, _: UiRequest, __: Any) -> list[dict[str, Any]]:
    result = compatibility.runtime.cloud_query(f"{_ROOT}/projects")
    directives = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/focus-directives"
    )
    runs = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/refresh-runs",
        query={"limit": "100"},
    )
    directives_by_project = {
        str(item.get("scopeId") or ""): item
        for item in directives or []
        if isinstance(item, dict) and str(item.get("scopeId") or "")
    }
    latest_run_by_project: dict[str, dict[str, Any]] = {}
    for run in runs.get("runs") or []:
        project_id = str(run.get("clientId") or run.get("scopeId") or "")
        if project_id and project_id not in latest_run_by_project:
            latest_run_by_project[project_id] = run
    return [
        {
            "type": "client",
            "id": str(item.get("projectId") or ""),
            "clientId": str(item.get("projectId") or ""),
            "projectModuleId": None,
            "name": str(item.get("name") or "未命名项目"),
            "subtitle": str(item.get("summary") or "项目情报"),
            "color": str(item.get("color") or "#5B7BFE"),
            "updatedAt": item.get("updatedAt"),
            "searchIntentStatus": (
                "ready"
                if str(item.get("projectId") or "") in directives_by_project
                else "missing"
            ),
            "searchIntentHint": (
                "已保存项目情报关注范围"
                if str(item.get("projectId") or "") in directives_by_project
                else "尚未设置项目情报关注范围"
            ),
            "sourceCoverageStatus": (
                "ready"
                if item.get("officialWebsiteUrl") or int(item.get("documentCount") or 0) > 0
                else "missing"
            ),
            "candidateRefreshStatus": (
                str(latest_run_by_project[str(item.get("projectId") or "")].get("status") or "missing")
                if str(item.get("projectId") or "") in latest_run_by_project
                else "missing"
            ),
            "candidateRefreshHint": (
                "最近一次公开来源刷新已有正式回执"
                if str(item.get("projectId") or "") in latest_run_by_project
                else "尚未执行公开来源刷新"
            ),
            "lastCandidateFetchAt": (
                latest_run_by_project[str(item.get("projectId") or "")].get("finishedAt")
                if str(item.get("projectId") or "") in latest_run_by_project
                else None
            ),
            "candidateCounts": {},
        }
        for item in result.get("projects") or []
        if str(item.get("projectId") or "")
    ]


@router.get(r"intelligence/items")
def list_items(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence",
        query={key: str(value) for key, value in request.query.items()},
    )


@router.get(r"intelligence/focus-directives")
def focus_directives(
    compatibility: Any, _: UiRequest, __: Any
) -> list[dict[str, Any]]:
    return compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/focus-directives"
    )


@router.put(r"intelligence/focus-directives")
def save_focus_directive(
    compatibility: Any, request: UiRequest, __: Any
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "PUT",
        f"{_ROOT}/intelligence/focus-directives",
        payload=dict(request.body),
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.get(r"intelligence/refresh-cycle-settings")
def refresh_cycle_settings(
    compatibility: Any, _: UiRequest, __: Any
) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/refresh-cycle-settings"
    )


@router.put(r"intelligence/refresh-cycle-settings")
def update_refresh_cycle_settings(
    compatibility: Any, request: UiRequest, __: Any
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "PUT",
        f"{_ROOT}/intelligence/refresh-cycle-settings",
        payload=dict(request.body),
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.get(r"intelligence/refresh-runs")
def refresh_runs(compatibility: Any, request: UiRequest, _: Any) -> list[dict[str, Any]]:
    return compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/refresh-runs",
        query={key: str(value) for key, value in request.query.items()},
    ).get("runs", [])


@router.post(r"intelligence/refresh")
def refresh_supply(
    compatibility: Any, request: UiRequest, __: Any
) -> dict[str, Any]:
    content_kind = str(request.body.get("contentKind") or "timely_intelligence")
    project_id = str(request.body.get("scopeId") or "").strip()
    scope_type = str(request.body.get("scopeType") or "all")
    if scope_type != "client" or not project_id:
        raise LocalRuntimeError(422, "intelligence_refresh_project_required", "请先选择一个项目再刷新情报")
    project = compatibility.runtime.cloud_query(
        f"{_ROOT}/projects/{_segment(project_id)}"
    ).get("project") or {}
    directives = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/focus-directives"
    )
    applicable = [
        item
        for item in directives or []
        if str(item.get("scopeType") or "global") == "global"
        or (
            str(item.get("scopeType") or "") == "client"
            and str(item.get("scopeId") or "") == project_id
        )
    ]
    focus_terms = list(
        dict.fromkeys(
            str(value).strip()
            for item in applicable
            for value in item.get("timelyIntelligenceFocus") or []
            if str(value or "").strip()
        )
    )[:6]
    excluded_terms = list(
        dict.fromkeys(
            str(value).strip().lower()
            for item in applicable
            for value in item.get("exclude") or []
            if str(value or "").strip()
        )
    )[:20]
    try:
        compatibility.runtime.cloud_command(
            "POST",
            f"/api/v2/domain/task-planning/project-keyword-profiles/{_segment(project_id)}/refresh",
            payload={"keywords": focus_terms},
            idempotency_key=f"{request.idempotency_key}:keyword-profile",
            refresh_business=False,
        )
    except Exception:
        # A stale keyword profile must not block an explicit research run; the
        # planner still receives current project facts and the member directive.
        pass
    try:
        research = _execute_research(
            compatibility,
            mode="timely",
            project_id=project_id,
            project=project,
            focus=focus_terms,
            excluded=excluded_terms,
        )
    except PublicCaptureError as exc:
        raise LocalRuntimeError(503 if exc.retryable else 422, exc.code, exc.message) from exc
    visible_items = list(research["items"])
    plan: _ResearchPlan = research["plan"]
    committed = compatibility.runtime.cloud_command(
        "POST",
        f"{_ROOT}/intelligence/external-capture",
        payload={
            "projectId": project_id,
            "captureId": sha256_text(f"{request.idempotency_key}|{project_id}")[:32],
            "contentKind": content_kind,
            "captureKind": "manual_intelligence",
            "items": [
                {**item, "clientItemKey": f"timely:{index}"}
                for index, item in enumerate(visible_items)
            ],
            "researchReceipt": {
                "planningMode": plan.planning_mode,
                "queryCount": len(plan.queries),
                "coverageTarget": plan.coverage_target,
                "directMentionPolicy": plan.direct_mention_policy,
                "rejectionCounts": research["rejectionCounts"],
                "bodyFetchedCount": research["bodyFetchedCount"],
                "modelAnalysisExecuted": research["modelAnalysisExecuted"],
            },
        },
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    candidate_count = len(visible_items)
    inserted_count = int(committed.get("insertedCount") or 0)
    status = "completed" if candidate_count else "no_results"
    object_result = {
        "scopeType": "client",
        "scopeId": project_id,
        "clientId": project_id,
        "projectModuleId": None,
        "name": str(project.get("name") or "当前项目"),
        "contentKind": content_kind,
        "status": status,
        "intentCount": len(plan.queries),
        "diagnosticRunCount": len(plan.queries),
        "diagnosticSuccessCount": len(plan.queries),
        "fetchJobCount": len(plan.queries),
        "candidateCount": candidate_count,
        "promotedCount": inserted_count,
        "duplicateCount": int(committed.get("duplicateCount") or 0),
        "failedCount": 0,
        "bodyFetchedCount": int(research["bodyFetchedCount"]),
        "verifiedCount": 0,
        "summarySuccessCount": candidate_count,
        "rejectionCounts": dict(research["rejectionCounts"]),
        "sourceCoverageStatus": "ready" if candidate_count else "missing",
        "candidateRefreshStatus": "ready" if status == "completed" else "missing",
        "lastCandidateFetchAt": utc_now(),
        "candidateCounts": {content_kind: candidate_count},
        "candidateSamples": [],
        "message": (
            f"已完成 {len(plan.queries)} 组公开检索和证据筛选，形成 {candidate_count} 条可见情报"
        ),
        "errors": [],
    }
    return {
        "status": status,
        "contentKind": content_kind,
        "scopeType": "client",
        "scopeId": project_id,
        "results": [object_result],
        "totals": {
            "objectCount": 1,
            "completedCount": 1 if status == "completed" else 0,
            "noResultCount": 1 if status == "no_results" else 0,
            "failedCount": 0,
            "intentCount": len(plan.queries),
            "fetchJobCount": len(plan.queries),
            "candidateCount": candidate_count,
            "promotedCount": inserted_count,
            "duplicateCount": int(committed.get("duplicateCount") or 0),
            "bodyFetchedCount": int(research["bodyFetchedCount"]),
            "verifiedCount": 0,
            "summarySuccessCount": candidate_count,
            "rejectionCounts": dict(research["rejectionCounts"]),
        },
        "message": object_result["message"],
        "generatedAt": utc_now(),
    }


@router.get(r"intelligence/source-diagnostics")
def source_diagnostics(
    compatibility: Any, request: UiRequest, __: Any
) -> dict[str, Any]:
    scope_type = str(request.query.get("scopeType") or "")
    scope_id = str(request.query.get("scopeId") or "").strip()
    content_kind = str(request.query.get("contentKind") or "").strip() or None
    if scope_type != "client" or not scope_id:
        raise LocalRuntimeError(422, "intelligence_diagnostics_scope_invalid", "请选择项目查看来源诊断")
    project = compatibility.runtime.cloud_query(
        f"{_ROOT}/projects/{_segment(scope_id)}"
    ).get("project") or {}
    item_result = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence",
        query={
            "workObjectId": scope_id,
            "contentKind": content_kind or "",
            "page": "1",
            "pageSize": "100",
        },
    )
    items = list(item_result.get("items") or [])
    runs = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/refresh-runs",
        query={"scopeId": scope_id, "limit": "20"},
    ).get("runs", [])
    source_rows: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in items:
        url = str(item.get("sourceUrl") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        source_rows.append(
            {
                "id": f"source-{sha256_text(url)[:20]}",
                "sourceType": "official_website" if project.get("officialWebsiteUrl") and url.startswith(str(project.get("officialWebsiteUrl")).rstrip("/")) else "public_web",
                "sourceName": str(item.get("source") or url),
                "sourceUrlTemplate": url,
                "contentKinds": [str(item.get("contentKind") or "timely_intelligence")],
                "region": "public_web",
                "reliabilityTier": "verified" if item.get("verificationStatus") == "verified" else "candidate",
                "priority": 100 if item.get("verificationStatus") == "verified" else 50,
                "enabled": True,
                "discoverySource": "intelligence_record",
                "discoveryReason": str(item.get("relevanceReason") or "已有项目情报来源"),
                "discoverySamples": [],
                "healthScore": 100 if item.get("verificationStatus") == "verified" else 60,
                "successCount": 1,
                "failureCount": 0,
                "candidateCount": 1,
                "promotedCount": 1 if item.get("verificationStatus") == "verified" else 0,
                "duplicateCount": 0,
                "lastStatus": "ready",
                "lastCheckedAt": item.get("updatedAt"),
                "lastSuccessAt": item.get("updatedAt"),
                "lastFailureAt": None,
                "nextDueAt": None,
            }
        )
    return {
        "scopeType": "client",
        "scopeId": scope_id,
        "contentKind": content_kind,
        "sourceCoverageStatus": "ready" if source_rows else "missing",
        "candidateRefreshStatus": "ready" if runs else "missing",
        "candidateRefreshHint": None if runs else "尚无情报刷新运行记录",
        "lastCandidateFetchAt": runs[0].get("finishedAt") if runs else None,
        "candidateCounts": {content_kind or "all": len(items)},
        "officialSiteDiscoveredCount": sum(1 for row in source_rows if row["sourceType"] == "official_website"),
        "coverageGaps": [] if source_rows else ["当前项目尚无可核实的情报来源"],
        "sources": source_rows,
        "recentFetchJobs": [
            {
                "id": run.get("id"),
                "contentKind": run.get("contentKind") or "brand_mirror",
                "provider": "official_website",
                "sourceConfigId": None,
                "query": str(project.get("officialWebsiteUrl") or ""),
                "status": run.get("status"),
                "rawCount": int((run.get("result") or {}).get("pageCount") or 0),
                "dedupedCount": int((run.get("result") or {}).get("pageCount") or 0),
                "candidateCount": int((run.get("result") or {}).get("candidateCount") or 0),
                "sampleHits": [],
                "failureReason": "" if run.get("status") != "failed" else str(run.get("message") or "抓取失败"),
                "durationMs": 0,
                "createdAt": run.get("createdAt"),
            }
            for run in runs
        ],
        "officialSiteDiscoverySamples": [],
    }


@router.get(r"intelligence/verification-rules")
def verification_rules(
    compatibility: Any, _: UiRequest, __: Any
) -> list[dict[str, Any]]:
    return compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/verification-rules"
    )


@router.put(r"intelligence/verification-rules")
def save_verification_rule(
    compatibility: Any, request: UiRequest, __: Any
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        "PUT",
        f"{_ROOT}/intelligence/verification-rules",
        payload=dict(request.body),
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.post(r"intelligence/verification-feedback")
def save_verification_feedback(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    target_type = str(request.body.get("targetType") or "")
    target_id = str(request.body.get("targetId") or "").strip()
    note = str(request.body.get("note") or "").strip()
    if target_type != "item" or not target_id or not note:
        raise LocalRuntimeError(
            422,
            "intelligence_verification_feedback_invalid",
            "请选择情报条目并填写核实或补充说明",
        )
    scope_type = str(request.body.get("scopeType") or "global")
    scope_id = request.body.get("scopeId")
    existing_rules = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/verification-rules"
    )
    existing = next(
        (
            item
            for item in existing_rules
            if str(item.get("scopeType") or "global") == scope_type
            and (item.get("scopeId") or None) == (scope_id or None)
        ),
        {},
    )
    rule = compatibility.runtime.cloud_command(
        "PUT",
        f"{_ROOT}/intelligence/verification-rules",
        payload={
            "scopeType": scope_type,
            "scopeId": scope_id,
            "positiveRules": list(existing.get("positiveRules") or []),
            "excludeRules": list(existing.get("excludeRules") or []),
            "identityAnchors": list(existing.get("identityAnchors") or []),
            "clarificationExamples": [
                *list(existing.get("clarificationExamples") or []),
                note,
            ],
        },
        idempotency_key=f"{request.idempotency_key}:rule",
        refresh_business=False,
    )
    compatibility.runtime.cloud_command(
        "POST",
        f"{_ROOT}/intelligence/{_segment(target_id)}/attention",
        payload={"action": "dismiss", "reasonCode": "inaccurate", "note": note},
        idempotency_key=f"{request.idempotency_key}:dismiss",
        refresh_business=False,
    )
    return rule


@router.get(r"topics")
def topics_compatibility_view(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    """Present strict intelligence records through the retained topics page.

    Radars and profiles are not synthesized: until their dedicated 88-table
    command contracts are connected the page shows the real intelligence
    records only, rather than examples or a frozen generic resource snapshot.
    """
    result = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence",
        query={"page": "1", "pageSize": str(request.query.get("limit") or 100)},
    )
    candidates = [
        {
            "id": str(item.get("id") or ""),
            "radarId": "",
            "title": str(item.get("title") or "未命名情报"),
            "summary": str(item.get("summary") or ""),
            "source": str(item.get("source") or "项目情报"),
            "sourceUrl": item.get("sourceUrl"),
            "publishedAt": item.get("publishedAt"),
            "captureMethod": "strict_intelligence_record",
            "capturedBy": None,
            "status": (
                "archived"
                if str(item.get("userStatus") or "") == "dismissed"
                else "tracking"
                if str(item.get("userStatus") or "") == "following"
                else "candidate"
            ),
            "evidenceStatus": str(item.get("verificationStatus") or "candidate"),
            "primaryBadge": None,
            "insightStatus": "ready" if item.get("summary") else "pending",
            "contentKind": item.get("contentKind"),
            "whyRecommended": item.get("relevanceReason"),
            "relevanceReason": item.get("relevanceReason"),
            "suggestedAction": item.get("suggestedAction"),
            "recommendationBasis": [],
            "groundingFactRefs": [],
            "scopeType": item.get("scopeType"),
            "scopeId": item.get("scopeId"),
            "clientId": item.get("clientId"),
            "projectModuleId": None,
            "createdAt": str(item.get("createdAt") or item.get("updatedAt") or ""),
            "version": int(item.get("version") or 1),
        }
        for item in result.get("items") or []
    ]
    return {
        "radars": [],
        "candidates": candidates,
        "intelligenceProfiles": [],
        "state": "ready" if candidates else "ready_empty",
        "message": (
            "已读取严格新版项目情报；情报雷达和画像的专属命令仍待单独接通"
            if candidates
            else "当前没有项目情报记录"
        ),
    }


def _set_attention(
    compatibility: Any,
    request: UiRequest,
    match: Any,
    *,
    action: str,
) -> dict[str, Any]:
    intelligence_id = str(match.group("intelligence_id"))
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_ROOT}/intelligence/{_segment(intelligence_id)}/attention",
        payload={"action": action, **dict(request.body)},
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.post(r"intelligence/items/(?P<intelligence_id>[^/]+)/follow")
def follow(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    return _set_attention(compatibility, request, match, action="follow")


@router.post(r"intelligence/items/(?P<intelligence_id>[^/]+)/dismiss")
def dismiss(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    return _set_attention(compatibility, request, match, action="dismiss")


@router.post(r"intelligence/items/(?P<intelligence_id>[^/]+)/task-draft")
def intelligence_task_draft(
    compatibility: Any, _: UiRequest, match: Any
) -> dict[str, Any]:
    item_id = str(match.group("intelligence_id"))
    item = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/items/{_segment(item_id)}"
    )
    context = compatibility.runtime._current_context(require_ready=True)  # noqa: SLF001
    return {
        "itemId": item_id,
        "draft": {
            "title": f"跟进：{str(item.get('title') or '项目情报')}",
            "desc": str(item.get("summary") or ""),
            "priority": "normal",
            "listId": None,
            "dueDate": None,
            "ddl": "本周",
            "ownerId": context.membership_id,
            "ownerName": "当前成员",
            "collaboratorIds": [],
            "tags": ["情报跟进"],
            "note": f"来源：{str(item.get('source') or '项目情报')}",
            "ownerRoleHint": "当前成员",
            "collaboratorRoleHints": [],
        },
    }


@router.post(r"intelligence/items/(?P<intelligence_id>[^/]+)/tasks")
def create_intelligence_task(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    item_id = str(match.group("intelligence_id"))
    item = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/items/{_segment(item_id)}"
    )
    context = compatibility.runtime._current_context(require_ready=True)  # noqa: SLF001
    payload = dict(request.body)
    task_result = compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/domain/tasks",
        payload={
            "title": str(payload.get("title") or "").strip(),
            "description": str(payload.get("desc") or "").strip(),
            "priority": str(payload.get("priority") or "normal"),
            "taskListId": payload.get("listId"),
            "clientId": item.get("clientId"),
            "dueDate": payload.get("dueDate"),
            "ownerMembershipId": context.membership_id,
            "collaboratorMembershipIds": list(payload.get("collaboratorIds") or []),
            "sourceType": "intelligence_record",
            "sourceId": item_id,
            "visibilityScope": "participants",
        },
        idempotency_key=request.idempotency_key,
        refresh_business=True,
    )
    task = dict(task_result.get("task") or {})
    return {
        "item": {**item, "convertedTaskId": task.get("taskId") or task.get("id")},
        "task": task,
    }


@router.post(r"intelligence/items/(?P<intelligence_id>[^/]+)/chat")
def intelligence_item_chat(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    item_id = str(match.group("intelligence_id"))
    question = str(request.body.get("question") or "").strip()
    if not question:
        raise LocalRuntimeError(422, "intelligence_question_required", "请输入要追问的问题")
    item = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/items/{_segment(item_id)}"
    )
    completion = compatibility.runtime.organization_ai_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "你是益语智库情报研究 Agent。只能依据给定情报及其已核实来源回答；"
                    "证据不足时明确说明，不得补造项目事实。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"情报标题：{item.get('title') or ''}\n"
                    f"情报摘要：{item.get('summary') or ''}\n"
                    f"核验状态：{item.get('verificationStatus') or 'candidate'}\n"
                    f"来源：{item.get('sourceUrl') or item.get('source') or ''}\n\n"
                    f"问题：{question}"
                ),
            },
        ],
        temperature=0.1,
        read_timeout_seconds=45.0,
    )
    answer = str(completion.get("content") or "").strip()
    provider = dict(completion.get("provider") or {})
    context = compatibility.runtime.capture_sandbox_context()
    recorded = compatibility.runtime.cloud_command(
        "POST",
        f"{_ROOT}/intelligence/items/{_segment(item_id)}/answers",
        payload={
            "questionHash": sha256_text(question),
            "answerHash": sha256_text(answer),
            "providerResourceId": provider.get("configId"),
            "modelName": provider.get("modelName"),
            "threadId": f"intelligence:{item_id}",
            "originInstanceId": context.cloud_instance_id,
        },
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    generated_at = utc_now()
    return {
        "itemId": item_id,
        "question": question,
        "answer": answer,
        "generatedAt": generated_at,
        "message": {"role": "assistant", "content": answer, "createdAt": generated_at},
        "sourceManifest": {
            "sourceObjectKind": "intelligence_record",
            "sourceObjectId": item_id,
            "sourceVersion": int(item.get("version") or 1),
            "sourceUrl": item.get("sourceUrl"),
        },
        "answerReceipt": recorded,
    }


def _sentiment_items(compatibility: Any, request: UiRequest) -> list[dict[str, Any]]:
    project_id = str(request.query.get("clientId") or request.body.get("clientId") or "").strip()
    if not project_id:
        raise LocalRuntimeError(422, "sentiment_project_required", "请选择舆情所属项目")
    result = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence",
        query={
            "workObjectId": project_id,
            "contentKind": "public_opinion",
            "page": "1",
            "pageSize": str(request.query.get("limit") or 100),
        },
    )
    values = []
    for item in result.get("items") or []:
        raw_label = str(item.get("sentimentLabel") or "unclassified")
        label = raw_label if raw_label in {"negative", "neutral", "positive"} else "neutral"
        reason = str(item.get("sentimentReason") or "")
        if raw_label == "unclassified":
            reason = "当前来源尚未完成情感分类，暂列为未分类中性项"
        values.append(
            {
                "id": item.get("id"),
                "clientId": item.get("clientId"),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "source": item.get("source"),
                "sourceUrl": item.get("sourceUrl") or "",
                "capturedAt": item.get("capturedAt"),
                "sentimentLabel": label,
                "sentimentReason": reason,
                "tags": list(item.get("tags") or []),
                "relevanceReason": str(item.get("relevanceReason") or ""),
                "impact": str(item.get("impact") or ""),
                "userStatus": item.get("userStatus") or "active",
            }
        )
    return values


@router.get(r"intelligence/sentiment/items")
def sentiment_items(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    items = _sentiment_items(compatibility, request)
    return {"items": items, "total": len(items), "state": "ready" if items else "ready_empty"}


@router.get(r"intelligence/sentiment/profile")
def sentiment_profile(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    items = _sentiment_items(compatibility, request)
    counts = {
        label: sum(1 for item in items if item["sentimentLabel"] == label)
        for label in ("negative", "neutral", "positive")
    }
    sources: dict[str, int] = {}
    for item in items:
        source = str(item.get("source") or "未知来源")
        sources[source] = sources.get(source, 0) + 1
    total = len(items)
    return {
        "withinDays": int(request.query.get("withinDays") or 30),
        "totalMentions": total,
        "sentimentScore": round((counts["positive"] - counts["negative"]) * 100 / total) if total else 0,
        "negativeCount": counts["negative"],
        "neutralCount": counts["neutral"],
        "positiveCount": counts["positive"],
        "topNegativeSources": [
            {"source": source, "count": sum(1 for item in items if item["source"] == source and item["sentimentLabel"] == "negative")}
            for source in sources
            if any(item["source"] == source and item["sentimentLabel"] == "negative" for item in items)
        ],
        "topSources": [
            {"source": source, "count": count}
            for source, count in sorted(sources.items(), key=lambda entry: (-entry[1], entry[0]))[:8]
        ],
        "state": "ready" if items else "ready_empty",
    }


@router.get(r"intelligence/sentiment/audit")
def sentiment_audit(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    items = _sentiment_items(compatibility, request)
    audit = _sentiment_audit_view(
        items,
        target_name=str(request.query.get("targetName") or "当前项目").strip() or "当前项目",
    )
    return {
        "audit": audit,
        "recomputeNote": None if audit else "当前没有可用于品牌印象分析的舆情记录",
    }


@router.post(r"intelligence/sentiment/refresh")
def refresh_sentiment(
    compatibility: Any, request: UiRequest, __: Any
) -> dict[str, Any]:
    _require_workspace(compatibility)
    project_id = str(
        request.body.get("clientId") or request.body.get("projectModuleId") or ""
    ).strip()
    target_name = str(request.body.get("targetName") or "").strip()
    if not project_id or not target_name:
        raise LocalRuntimeError(422, "sentiment_target_required", "请选择要抓取舆情的项目")
    project = compatibility.runtime.cloud_query(
        f"{_ROOT}/projects/{_segment(project_id)}"
    ).get("project") or {"name": target_name}
    try:
        compatibility.runtime.cloud_command(
            "POST",
            f"/api/v2/domain/task-planning/project-keyword-profiles/{_segment(project_id)}/refresh",
            payload={"keywords": []},
            idempotency_key=f"{request.idempotency_key}:keyword-profile",
            refresh_business=False,
        )
    except Exception:
        pass
    try:
        research = _execute_research(
            compatibility,
            mode="brand",
            project_id=project_id,
            project={**dict(project), "name": target_name},
            focus=[],
            excluded=[],
        )
    except PublicCaptureError as exc:
        raise LocalRuntimeError(503 if exc.retryable else 422, exc.code, exc.message) from exc
    captured = list(research["items"])
    plan: _ResearchPlan = research["plan"]
    if not captured:
        return {
            "targetName": target_name,
            "fetchedCount": 0,
            "insertedCount": 0,
            "negativeCount": 0,
            "neutralCount": 0,
            "positiveCount": 0,
            "externalCollectionExecuted": True,
            "modelAnalysisExecuted": bool(research["modelAnalysisExecuted"]),
            "sourceBodyStored": False,
            "sourceBodyReadCount": int(research["bodyFetchedCount"]),
            "queryCount": len(plan.queries),
            "rejectionCounts": dict(research["rejectionCounts"]),
            "state": "ready_empty",
        }
    committed = compatibility.runtime.cloud_command(
        "POST",
        f"{_ROOT}/intelligence/external-capture",
        payload={
            "projectId": project_id,
            "captureId": sha256_text(f"{request.idempotency_key}|{project_id}")[:32],
            "contentKind": "public_opinion",
            "captureKind": "manual_intelligence",
            "items": [
                {
                    **item,
                    "clientItemKey": f"sentiment:{index}",
                }
                for index, item in enumerate(captured)
            ],
            "researchReceipt": {
                "planningMode": plan.planning_mode,
                "queryCount": len(plan.queries),
                "coverageTarget": plan.coverage_target,
                "directMentionPolicy": plan.direct_mention_policy,
                "rejectionCounts": research["rejectionCounts"],
                "bodyFetchedCount": research["bodyFetchedCount"],
                "modelAnalysisExecuted": research["modelAnalysisExecuted"],
            },
        },
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    counts = {
        sentiment: sum(1 for item in captured if item.get("sentiment") == sentiment)
        for sentiment in ("negative", "neutral", "positive")
    }
    return {
        "targetName": target_name,
        "fetchedCount": len(captured),
        "insertedCount": int(committed.get("insertedCount") or 0),
        "negativeCount": counts["negative"],
        "neutralCount": counts["neutral"],
        "positiveCount": counts["positive"],
        "externalCollectionExecuted": True,
        "modelAnalysisExecuted": bool(research["modelAnalysisExecuted"]),
        "sourceBodyStored": False,
        "sourceBodyReadCount": int(research["bodyFetchedCount"]),
        "queryCount": len(plan.queries),
        "rejectionCounts": dict(research["rejectionCounts"]),
        "state": "ready",
    }


@router.post(r"intelligence/sentiment/feedback")
def sentiment_feedback(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    item_id = str(request.body.get("itemId") or "").strip()
    action = str(request.body.get("action") or "").strip()
    if not item_id or action not in {"confirm_negative", "mark_misclassified", "mark_resolved", "restore"}:
        raise LocalRuntimeError(422, "sentiment_feedback_invalid", "舆情反馈动作无效")
    attention = (
        "restore"
        if action == "restore"
        else ("follow" if action == "confirm_negative" else "dismiss")
    )
    item = compatibility.runtime.cloud_command(
        "POST",
        f"{_ROOT}/intelligence/{_segment(item_id)}/attention",
        payload={"action": attention, "sentimentAction": action},
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    return {
        "itemId": item_id,
        "action": action,
        "userStatus": item.get("userStatus"),
        "updatedAt": item.get("updatedAt") or utc_now(),
    }


def _sentiment_themes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = utc_now()
    themes: list[dict[str, Any]] = []
    tag_members: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        for tag in item.get("tags") or []:
            label = str(tag or "").strip()
            if label:
                tag_members.setdefault(label, []).append(item)
    ranked_tags = sorted(
        tag_members,
        key=lambda label: (-len(tag_members[label]), label),
    )[:6]
    groups: list[tuple[str, list[dict[str, Any]]]] = [
        (label, tag_members[label]) for label in ranked_tags
    ]
    if not groups:
        labels = {"negative": "负面评价", "neutral": "中性评价", "positive": "积极评价"}
        groups = [
            (labels[sentiment], [item for item in items if item.get("sentimentLabel") == sentiment])
            for sentiment in ("negative", "neutral", "positive")
        ]
    for label, members in groups:
        if not members:
            continue
        representative = members[0]
        sentiment_counts = {
            value: sum(1 for item in members if item.get("sentimentLabel") == value)
            for value in ("negative", "neutral", "positive")
        }
        sentiment = max(sentiment_counts, key=sentiment_counts.get)
        theme_id = "theme_" + sha256_text(
            f"{label}|{'|'.join(str(item.get('id') or '') for item in members)}"
        )[:24]
        themes.append(
            {
                "id": theme_id,
                "themeLabel": label,
                "themeSummary": str(
                    representative.get("relevanceReason")
                    or f"共 {len(members)} 条外部来源将项目与“{label}”联系起来。"
                ),
                "sentimentTone": sentiment,
                "itemCount": len(members),
                "representativeQuote": str(representative.get("summary") or "")[:300],
                "representativeItemId": representative.get("id"),
                "itemIds": [str(item.get("id") or "") for item in members],
                "computedAt": now,
                "expiresAt": now,
            }
        )
    return themes


def _sentiment_audit_view(
    items: list[dict[str, Any]], *, target_name: str
) -> dict[str, Any] | None:
    if not items:
        return None
    themes = _sentiment_themes(items)
    counts = {
        label: sum(1 for item in items if item.get("sentimentLabel") == label)
        for label in ("negative", "neutral", "positive")
    }
    dominant = max(counts, key=counts.get)
    label = {"negative": "负面", "neutral": "中性", "positive": "积极"}[dominant]
    theme_labels = [str(theme.get("themeLabel") or "") for theme in themes[:4]]
    now = utc_now()
    negative = [item for item in items if item.get("sentimentLabel") == "negative"]
    positive = [item for item in items if item.get("sentimentLabel") == "positive"]
    return {
        "id": "audit_" + sha256_text(
            f"{target_name}|{'|'.join(str(item.get('id') or '') for item in items)}"
        )[:24],
        "scopeType": "client",
        "scopeId": str(items[0].get("clientId") or ""),
        "headline": (
            f"外部来源主要将{target_name}与{'、'.join(theme_labels[:3])}联系起来"
            if theme_labels
            else f"{target_name}当前公开评价以{label}信息为主"
        ),
        "narrativeMd": (
            f"当前共纳入 {len(items)} 条有来源记录：积极 {counts['positive']} 条，"
            f"中性 {counts['neutral']} 条，负面 {counts['negative']} 条。"
            + (
                f"较集中的外部印象包括：{'、'.join(theme_labels)}。"
                if theme_labels
                else ""
            )
        ),
        "tensions": [
            str(item.get("relevanceReason") or item.get("summary") or "")[:180]
            for item in negative[:3]
        ],
        "recommendations": (
            [
                {
                    "action": "逐条核实负面来源并形成回应依据",
                    "rationale": f"当前有 {len(negative)} 条负面来源记录",
                    "priority": "high",
                }
            ]
            if negative
            else []
        ),
        "contentAngles": {
            "amplify": [str(item.get("summary") or "")[:120] for item in positive[:3]],
            "new": [str(item.get("summary") or "")[:120] for item in negative[:3]],
        },
        "evidenceThemeIds": [str(theme["id"]) for theme in themes],
        "computedAt": now,
        "expiresAt": now,
    }


@router.post(r"intelligence/sentiment/themes/recompute")
def recompute_sentiment_themes(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    items = _sentiment_items(compatibility, request)
    if not items:
        return {"ok": False, "reason": "too_few_items", "themes": [], "audit": None}
    return {"ok": True, "themes": _sentiment_themes(items)}


@router.post(r"intelligence/sentiment/audit/recompute")
def recompute_sentiment_audit(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    items = _sentiment_items(compatibility, request)
    if not items:
        return {"ok": False, "reason": "too_few_items", "themes": [], "audit": None}
    audit = _sentiment_audit_view(
        items,
        target_name=str(request.body.get("targetName") or "当前项目").strip() or "当前项目",
    )
    return {
        "ok": True,
        "audit": audit,
    }


def _strategy_extract(compatibility: Any, project_id: str) -> dict[str, Any] | None:
    result = compatibility.runtime.cloud_query(
        f"{_ROOT}/intelligence/strategy-extract",
        query={"clientId": project_id},
    )
    extract = result.get("extract")
    return dict(extract) if isinstance(extract, dict) else None


@router.get(r"intelligence/brand-mirror/strategy-extract")
def brand_strategy_extract(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    project_id = str(request.query.get("clientId") or "").strip()
    if not project_id:
        raise LocalRuntimeError(422, "strategy_extract_project_required", "请选择项目")
    return {"extract": _strategy_extract(compatibility, project_id)}


@router.post(r"intelligence/brand-mirror/strategy-extract")
def refresh_brand_strategy_extract(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    project_id = str(request.body.get("clientId") or "").strip()
    if not project_id:
        raise LocalRuntimeError(422, "strategy_extract_project_required", "请选择项目")
    extract = _strategy_extract(compatibility, project_id)
    if extract is None:
        raise LocalRuntimeError(
            409,
            "strategy_extract_profile_required",
            "请先在战略陪伴中根据项目资料生成客户档案",
        )
    return extract


@router.put(r"intelligence/brand-mirror/strategy-extract")
def update_brand_strategy_extract(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    project_id = str(request.body.get("clientId") or "").strip()
    objective = str(request.body.get("strategicObjective") or "").strip()
    methodology = str(request.body.get("methodology") or "").strip()
    if not project_id or not objective or not methodology:
        raise LocalRuntimeError(422, "strategy_extract_invalid", "战略主张和方法学不能为空")
    if len(objective) + len(methodology) > 200:
        raise LocalRuntimeError(422, "strategy_extract_too_long", "战略主张和方法学合计不能超过200字")
    profile = compatibility.runtime.cloud_query(
        f"/api/v2/workbench/projects/{_segment(project_id)}/narrative"
    )
    based_on_rev = int(profile.get("rev") or 0)
    if based_on_rev <= 0:
        raise LocalRuntimeError(
            409,
            "strategy_extract_profile_required",
            "请先在战略陪伴中根据项目资料生成客户档案",
        )
    base_key = request.idempotency_key or sha256_text(
        f"strategy-extract|{project_id}|{objective}|{methodology}"
    )
    for dimension, question, statement in (
        ("next_steps", "项目当前战略主张是什么？", objective),
        ("cooperation", "项目采用什么方法推进战略？", methodology),
    ):
        compatibility.runtime.cloud_command(
            "POST",
            f"/api/v2/workbench/projects/{_segment(project_id)}/narrative-clarifications",
            payload={
                "dimension": dimension,
                "question": question,
                "answer": statement,
                "basedOnRev": based_on_rev,
            },
            idempotency_key=sha256_text(f"{base_key}|{dimension}"),
            refresh_business=False,
        )
    extract = _strategy_extract(compatibility, project_id)
    if extract is None:
        raise LocalRuntimeError(503, "strategy_extract_refresh_pending", "战略提炼已保存，读取最新版本失败，可以重试")
    return {"extract": extract}
