"""Task-planning Agent keyword profiles using only the frozen 88-table graph."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

import httpx

from strict_common.agent_memory import AgentRunReceipt, builtin_agent_id
from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .project_materials import GC07ProjectMaterialsRepository
from . import gc06_planning


PROFILE_SCHEMA = "yiyu.project-recognition-profile.v5"

PROFILE_CATEGORY_ORDER = (
    "identityTerms",
    "peopleAndOrganizations",
    "productsAndPrograms",
    "domainTerms",
    "asrTerms",
)

_PLACEHOLDER_MARKERS = (
    "等待导入",
    "系统将自动",
    "暂无资料",
    "尚未上传",
    "未接通",
)
_NAME_SUFFIXES = (
    "公益基金会",
    "基金会",
    "社会服务中心",
    "研究院",
    "实验室",
    "智库",
    "中心",
    "集团",
    "公司",
)


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{sha256_text(chr(31).join(str(part) for part in parts))[:30]}"


def _normalize_term(value: Any) -> str:
    term = " ".join(str(value or "").split()).strip(" ._-/，。；：、（）()[]【】《》\"'")
    if (
        len(term) < 2
        or len(term) > 32
        or any(marker in term for marker in _PLACEHOLDER_MARKERS)
    ):
        return ""
    return term


def _empty_categories() -> dict[str, list[str]]:
    return {key: [] for key in PROFILE_CATEGORY_ORDER}


def _add_term(categories: dict[str, list[str]], category: str, value: Any) -> None:
    if category not in categories:
        return
    term = _normalize_term(value)
    if not term:
        return
    lowered = term.casefold()
    if any(existing.casefold() == lowered for existing in categories[category]):
        return
    categories[category].append(term)


def _base_categories(client: Mapping[str, Any]) -> dict[str, list[str]]:
    categories = _empty_categories()
    for raw_name in (client.get("name"), client.get("alias")):
        name = _normalize_term(raw_name)
        if not name:
            continue
        _add_term(categories, "identityTerms", name)
        for suffix in _NAME_SUFFIXES:
            if name.endswith(suffix) and len(name) - len(suffix) >= 2:
                _add_term(categories, "identityTerms", name[: -len(suffix)])
                break
    domain = _normalize_term(client.get("domain"))
    if domain and domain not in {"项目", "客户", "组织", "公益项目"}:
        _add_term(categories, "domainTerms", domain)
    return categories


def _normalized_categories(raw: Any) -> dict[str, list[str]]:
    categories = _empty_categories()
    if not isinstance(raw, Mapping):
        return categories
    for key in PROFILE_CATEGORY_ORDER:
        values = raw.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            _add_term(categories, key, value)
    return categories


def _merge_categories(*items: Mapping[str, list[str]]) -> dict[str, list[str]]:
    merged = _empty_categories()
    limits = {
        "identityTerms": 10,
        "peopleAndOrganizations": 16,
        "productsAndPrograms": 16,
        "domainTerms": 14,
        "asrTerms": 24,
    }
    for item in items:
        for key in PROFILE_CATEGORY_ORDER:
            for value in item.get(key) or []:
                _add_term(merged, key, value)
    for key, limit in limits.items():
        merged[key] = merged[key][:limit]
    return merged


def _flatten_categories(categories: Mapping[str, list[str]]) -> list[str]:
    result: list[str] = []
    for key in PROFILE_CATEGORY_ORDER:
        for term in categories.get(key) or []:
            if term.casefold() not in {item.casefold() for item in result}:
                result.append(term)
    return result


def _task_matching_keywords(
    categories: Mapping[str, list[str]],
    supplements: list[str] | None = None,
) -> list[str]:
    """Return only real project concepts used by the task editor.

    ASR pronunciation hints are deliberately excluded: they may contain
    low-discrimination terms useful for transcription, but are not project
    facts and must never become a hidden task-routing projection.
    """

    result: list[str] = []
    for key in (
        "identityTerms",
        "peopleAndOrganizations",
        "productsAndPrograms",
        "domainTerms",
    ):
        for term in categories.get(key) or []:
            if term.casefold() not in {item.casefold() for item in result}:
                result.append(term)
    for term in supplements or []:
        normalized = _normalize_term(term)
        if normalized and normalized.casefold() not in {item.casefold() for item in result}:
            result.append(normalized)
    return result


def _concise_domain_aliases(categories: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """Derive common user-language concepts from verified compound terms.

    Official sites often use precise long phrases (for example
    ``儿童青少年心理教育``), while tasks, search and speech use shorter stable
    concepts (``儿童心理``).  These aliases are a projection of an existing
    verified term, never an independent fact source.
    """

    corpus = "\n".join(
        str(term)
        for key in PROFILE_CATEGORY_ORDER
        for term in categories.get(key) or []
    )
    aliases = _empty_categories()
    rules = (
        (r"儿童[^，。；\n]{0,12}心理|心理[^，。；\n]{0,12}儿童", "儿童心理"),
        (r"青少年[^，。；\n]{0,12}心理|心理[^，。；\n]{0,12}青少年", "青少年心理"),
        (r"心理教育", "心理教育"),
        (r"教师[^，。；\n]{0,8}(?:培训|赋能)|(?:培训|赋能)[^，。；\n]{0,8}教师", "教师培训"),
    )
    for pattern, alias in rules:
        if not re.search(pattern, corpus):
            continue
        _add_term(aliases, "domainTerms", alias)
    return aliases


def _manual_supplements(facts: list[dict[str, Any]]) -> list[str]:
    result: list[str] = []
    for fact in facts:
        statement = str(fact.get("statement") or "").strip()
        match = re.match(r"^项目识别(?:画像|关键词)补充(?:或纠正)?[：:]\s*(.+)$", statement)
        if not match:
            continue
        for raw in re.split(r"[，,、；;\n]+", match.group(1)):
            term = _normalize_term(raw)
            if term and term.casefold() not in {item.casefold() for item in result}:
                result.append(term)
    return result[:20]


def _identity_evidence_terms(client: Mapping[str, Any], facts: list[dict[str, Any]]) -> list[str]:
    result = list(_base_categories(client)["identityTerms"])
    identity_markers = ("全称", "简称", "英文名", "外文名", "机构名称", "组织名称", "官方名称")
    for fact in facts:
        attribute = str(fact.get("attributeName") or "")
        if any(marker in attribute for marker in identity_markers):
            for value in (fact.get("term"), fact.get("valueText")):
                term = _normalize_term(value)
                if term and term.casefold() not in {item.casefold() for item in result}:
                    result.append(term)
    return result


def _sanitize_identity_terms(
    client: Mapping[str, Any],
    facts: list[dict[str, Any]],
    generated: list[str],
) -> list[str]:
    evidence = _identity_evidence_terms(client, facts)
    result: list[str] = []
    evidence_keys = {item.casefold() for item in evidence}
    # Identity is intentionally deterministic.  A model may classify explicit
    # identity evidence, but may not promote page titles, slogans or partner
    # names into the project's own aliases merely because they contain the
    # project name.
    for term in evidence:
        normalized = _normalize_term(term)
        if not normalized:
            continue
        lowered = normalized.casefold()
        if lowered in evidence_keys and lowered not in {item.casefold() for item in result}:
            result.append(normalized)
    return result[:10]


def _filter_internal_people_and_organizations(
    candidates: list[str],
    facts: list[dict[str, Any]],
    strategic_profile: Mapping[str, Any] | None = None,
) -> list[str]:
    internal_markers = (
        "创办人", "创始人", "联合创始人", "发起人", "理事", "秘书长", "负责人",
        "主任", "主管", "员工", "团队", "部门", "下属", "内部", "项目经理",
        "项目负责人", "管理层", "管理者", "核心决策者", "首席", "CEO", "成员",
    )
    external_markers = (
        "合作方", "合作伙伴", "外部合作", "技术支持", "咨询方", "外部顾问", "供应商",
        "资助方", "捐赠方", "服务机构", "陪伴机构", "益语方", "客户方之外",
    )
    profile_people: dict[str, list[str]] = {}
    if isinstance(strategic_profile, Mapping):
        for item in strategic_profile.get("dimensions") or []:
            if not isinstance(item, Mapping):
                continue
            dimension = str(item.get("dimension") or "").casefold()
            if dimension not in {"people", "key_people", "关键人物", "关键人物网"}:
                continue
            narrative = str(item.get("narrative") or "")
            for match in re.finditer(
                r"(?:^|[。；;\n])\s*([^：:，,。；;\n]{2,20})\s*[：:]\s*([^。；;\n]+)",
                narrative,
            ):
                name = _normalize_term(match.group(1))
                if name:
                    profile_people.setdefault(name, []).append(match.group(2).strip())
    result: list[str] = []
    for candidate in candidates:
        term = _normalize_term(candidate)
        if not term:
            continue
        supported = False
        for fact in facts:
            # A name mentioned in somebody else's biography (Forbes, a former
            # employer, a partner foundation, etc.) is context, not an internal
            # person or organization.  Only the fact's own normalized subject
            # term may qualify for this category.
            if _normalize_term(fact.get("term")) != term:
                continue
            statement = " ".join(
                str(fact.get(key) or "")
                for key in ("term", "attributeName", "valueText", "statement", "summary")
            )
            if any(marker in statement for marker in external_markers):
                continue
            if any(marker in statement for marker in internal_markers):
                supported = True
                break
        if not supported:
            for description in profile_people.get(term, []):
                if any(marker in description for marker in external_markers):
                    continue
                if any(marker in description for marker in internal_markers):
                    supported = True
                    break
        if supported and term.casefold() not in {item.casefold() for item in result}:
            result.append(term)
    return result[:16]


_GENERIC_PROFILE_TERMS = {
    "项目", "工作", "发展", "服务", "活动", "会议", "资料", "文件", "报告", "文章",
    "表单", "飞书表单", "飞书看板", "飞书工作流", "看板", "工作流", "数字化工具",
    "合同", "协议", "合同编码", "项目编码", "编号", "代码", "ai", "人工智能",
}


def _filter_profile_specific_terms(
    values: list[str],
    *,
    evidence_text: str,
) -> list[str]:
    """Keep six-card additions only when they distinguish the current project."""

    compact_evidence = "".join(str(evidence_text or "").split()).casefold()
    result: list[str] = []
    for value in values:
        term = _normalize_term(value)
        if not term:
            continue
        compact = "".join(term.split()).casefold()
        if compact in _GENERIC_PROFILE_TERMS:
            continue
        if any(tool in compact for tool in ("飞书", "腾讯会议", "企业微信", "钉钉")):
            continue
        if re.search(r"[a-z]{2,}[-_]?\d{3,}|\d{4,}[-_][a-z0-9_-]+", compact, re.I):
            continue
        if compact not in compact_evidence:
            continue
        if compact not in {item.casefold() for item in result}:
            result.append(term)
    return result


def _keywords(client: Mapping[str, Any], supplied: list[Any] | None = None) -> list[str]:
    """Compatibility flat view consumed by task matching.

    New profiles are structured.  The flat list remains a projection for
    existing task editors and must not become a second editable authority.
    """

    categories = _base_categories(client)
    return [*_flatten_categories(categories), *[
        term for term in (_normalize_term(value) for value in supplied or []) if term
    ]]


class TaskPlanningAgentRepository:
    def __init__(self, repository: CloudRepository) -> None:
        self.repository = repository

    def list_profiles(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:  # noqa: SLF001
            clients = connection.execute(
                "SELECT * FROM clients WHERE scope_id=? AND lifecycle_state='active' "
                "ORDER BY name,id",
                (identity.scope_id,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for client in clients:
                try:
                    self.repository._require_project_access(  # noqa: SLF001
                        connection,
                        identity,
                        project_id=str(client["id"]),
                        capability="project_read",
                    )
                except RepositoryError:
                    continue
                profile = connection.execute(
                    """
                    SELECT narrative.current_version,narrative.version AS aggregate_version,
                           manifest.receipt,narrative.updated_at
                    FROM narrative_outputs AS narrative
                    JOIN artifact_versions AS version
                      ON version.scope_id=narrative.scope_id
                     AND version.artifact_id=narrative.id
                     AND version.version=narrative.current_version
                    JOIN object_manifests AS manifest
                      ON manifest.scope_id=version.scope_id
                     AND manifest.id=version.object_manifest_id
                    WHERE narrative.scope_id=? AND narrative.client_id=?
                      AND narrative.artifact_kind='project_keyword_profile'
                      AND narrative.lifecycle_state='active'
                    """,
                    (identity.scope_id, str(client["id"])),
                ).fetchone()
                payload: dict[str, Any] = {}
                if profile is not None:
                    try:
                        loaded = json.loads(str(profile["receipt"] or "{}"))
                        payload = dict(loaded) if isinstance(loaded, Mapping) else {}
                    except json.JSONDecodeError:
                        payload = {}
                categories = _normalized_categories(payload.get("categories"))
                if not any(categories.values()):
                    categories = _base_categories(dict(client))
                supplements = [
                    term for term in payload.get("supplements") or []
                    if _normalize_term(term)
                ][:20]
                result.append(
                    {
                        "clientId": str(client["id"]),
                        "clientName": str(client["name"] or ""),
                        "keywords": _task_matching_keywords(categories, supplements),
                        "categories": categories,
                        "supplements": supplements,
                        "sourceSummary": dict(payload.get("sourceSummary") or {}),
                        "generationState": str(payload.get("generationState") or "rules_only"),
                        "state": "ready" if profile is not None else "not_built",
                        "version": int(profile["current_version"] or 1) if profile else 0,
                        "updatedAt": str(profile["updated_at"] or "") if profile else None,
                    }
                )
            return result

    @staticmethod
    def _json_object(value: str) -> dict[str, Any]:
        raw = value.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RepositoryError(502, "task_draft_parse_invalid", "组织模型没有返回有效的任务草稿") from exc
        if not isinstance(parsed, dict):
            raise RepositoryError(502, "task_draft_parse_invalid", "组织模型没有返回有效的任务草稿")
        return parsed

    def _recognition_inputs(
        self,
        identity: SessionIdentity,
        *,
        client_id: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, int]]:
        """Read only evidence that may safely participate in a shared profile."""

        with self.repository._connection() as connection:  # noqa: SLF001
            client = connection.execute(
                "SELECT * FROM clients WHERE scope_id=? AND id=? AND lifecycle_state='active'",
                (identity.scope_id, client_id),
            ).fetchone()
            if client is None:
                raise RepositoryError(404, "client_not_found", "项目不存在")
            self.repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=client_id,
                capability="project_write",
            )
            fact_rows = connection.execute(
                """
                SELECT manifest.receipt
                FROM atomic_facts AS fact
                JOIN source_sets AS sources
                  ON sources.scope_id=fact.scope_id AND sources.id=fact.source_set_id
                 AND sources.client_id=? AND sources.lifecycle_state='active'
                JOIN object_manifests AS manifest
                  ON manifest.scope_id=fact.scope_id
                 AND manifest.id=fact.fact_object_manifest_id
                 AND manifest.lifecycle_state='active'
                WHERE fact.scope_id=? AND fact.lifecycle_state='active'
                  AND fact.verification_state IN ('verified','confirmed')
                ORDER BY fact.updated_at DESC, fact.id
                """,
                (client_id, identity.scope_id),
            ).fetchall()
            facts: list[dict[str, Any]] = []
            for row in fact_rows:
                try:
                    loaded = json.loads(str(row["receipt"] or "{}"))
                except json.JSONDecodeError:
                    continue
                if isinstance(loaded, Mapping):
                    facts.append(dict(loaded))
            strategic_row = connection.execute(
                """
                SELECT manifest.receipt
                FROM narrative_outputs AS narrative
                JOIN artifact_versions AS version
                  ON version.scope_id=narrative.scope_id
                 AND version.artifact_id=narrative.id
                 AND version.version=narrative.current_version
                JOIN object_manifests AS manifest
                  ON manifest.scope_id=version.scope_id
                 AND manifest.id=version.object_manifest_id
                WHERE narrative.scope_id=? AND narrative.client_id=?
                  AND narrative.artifact_kind='strategic_profile'
                  AND narrative.lifecycle_state='active'
                """,
                (identity.scope_id, client_id),
            ).fetchone()
            strategic_profile: dict[str, Any] = {}
            if strategic_row is not None:
                try:
                    loaded = json.loads(str(strategic_row["receipt"] or "{}"))
                    if isinstance(loaded, Mapping):
                        strategic_profile = dict(loaded)
                except json.JSONDecodeError:
                    strategic_profile = {}
            shared_knowledge_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_documents WHERE scope_id=? AND client_id=? "
                    "AND visibility_scope='organization' AND publication_state='published' "
                    "AND lifecycle_state='active'",
                    (identity.scope_id, client_id),
                ).fetchone()[0]
            )
        coverage = strategic_profile.get("coverage") if isinstance(strategic_profile.get("coverage"), Mapping) else {}
        source_summary = {
            "verifiedFactCount": len(facts),
            "sharedKnowledgeCount": shared_knowledge_count,
            "localDocumentCount": max(0, int(coverage.get("scannedDocumentCount") or 0)),
        }
        return dict(client), facts, strategic_profile, source_summary

    def _synthesize_recognition_categories(
        self,
        identity: SessionIdentity,
        *,
        client: Mapping[str, Any],
        facts: list[dict[str, Any]],
        strategic_profile: Mapping[str, Any],
        supplied: list[Any] | None,
    ) -> tuple[dict[str, list[str]], str, list[str]]:
        base = _base_categories(client)
        evidence_categories = _empty_categories()
        evidence_rows: list[dict[str, str]] = []
        for fact in facts[:180]:
            term = _normalize_term(fact.get("term"))
            subject_kind = str(fact.get("subjectKind") or "").casefold()
            fact_kind = str(fact.get("factKind") or "").casefold()
            statement = " ".join(
                str(fact.get(key) or "").strip()
                for key in ("term", "attributeName", "valueText", "statement", "summary")
                if str(fact.get(key) or "").strip()
            )[:800]
            if statement:
                evidence_rows.append(
                    {
                        "subjectKind": subject_kind,
                        "factKind": fact_kind,
                        "statement": statement,
                    }
                )
            if not term:
                continue
            if subject_kind in {"person", "team", "governance"}:
                _add_term(evidence_categories, "peopleAndOrganizations", term)
                _add_term(evidence_categories, "asrTerms", term)
            elif subject_kind in {"service", "project"}:
                _add_term(evidence_categories, "productsAndPrograms", term)
                _add_term(evidence_categories, "asrTerms", term)
            elif fact_kind in {"identity", "organization_name", "alias"}:
                _add_term(evidence_categories, "identityTerms", term)
            else:
                _add_term(evidence_categories, "domainTerms", term)

        supplements = _manual_supplements(facts)
        legacy_manual = _empty_categories()
        for value in supplied or []:
            _add_term(legacy_manual, "asrTerms", value)
        for value in supplements:
            _add_term(legacy_manual, "asrTerms", value)

        dimensions = [
            {
                "dimension": str(item.get("dimension") or ""),
                "narrative": str(item.get("narrative") or "")[:2_000],
            }
            for item in strategic_profile.get("dimensions") or []
            if isinstance(item, Mapping) and str(item.get("narrative") or "").strip()
        ]
        dossier_evidence_text = "\n".join(
            str(item.get("narrative") or "")
            for item in dimensions
        )
        fact_evidence_text = "\n".join(row["statement"] for row in evidence_rows)
        combined_evidence_text = f"{dossier_evidence_text}\n{fact_evidence_text}"
        prompt = {
            "project": {
                "name": str(client.get("name") or ""),
                "alias": str(client.get("alias") or ""),
                "domain": str(client.get("domain") or ""),
                "summary": str(client.get("summary") or "")[:1_500],
            },
            "verifiedFacts": evidence_rows,
            "clientProfile": dimensions,
        }
        generated = _empty_categories()
        generation_state = "rules_only"
        try:
            provider = self.repository.ai_config(identity, include_secret=True)
            if provider.get("status") == "ready" and provider.get("apiKey"):
                system = (
                    "你负责从项目官网权威事实、正式共享知识和客户档案中提炼结构化项目识别画像。"
                    "只返回JSON对象，字段必须是identityTerms、peopleAndOrganizations、productsAndPrograms、"
                    "domainTerms、asrTerms，值均为简短字符串数组。"
                    "识别稳定名称、简称、人名、机构、产品、服务、计划、专业术语和任务中常出现的辨识词。"
                    "服务领域除官网精确术语外，还必须提炼3至8个用户在任务、搜索和口语中会使用的简短上位概念；"
                    "例如‘儿童青少年心理教育’应同时提炼‘儿童心理’、‘青少年心理’和‘心理教育’。"
                    "禁止收录文章或报告标题、完整句子、发布日期、版权年份、导航词，以及‘项目、工作、发展、AI’"
                    "等脱离上下文无区分度的泛词。"
                    "转写术语优先人名、机构名、计划名和容易听错的专有名词。证据不足就返回空数组，不得编造。"
                    "名称与别称只能包含本项目自身的正式名称、法定全称、简称和外文名，严禁放入合作方。"
                    "关键人物与组织只包含本机构内部管理者、员工、项目负责人和内部部门，"
                    "不得包含外部顾问、咨询机构、技术支持方、合作伙伴、资助方或捐赠方。"
                    "客户档案六个栏目可以补充官网没有覆盖但已由项目资料支持的人物、服务、计划和领域；"
                    "但不得把飞书表单、看板、工作流等交付工具，合同号、编码、文档标题或通用流程词当成项目关键词。"
                )
                base_url = str(provider.get("baseUrl") or "").rstrip("/")
                endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
                with httpx.Client(
                    timeout=httpx.Timeout(connect=5, read=75, write=15, pool=5),
                    trust_env=False,
                ) as model_client:
                    response = model_client.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {provider['apiKey']}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": provider["modelName"],
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": canonical_json(prompt)},
                            ],
                            "temperature": 0.1,
                            "thinking": {"type": "disabled"},
                            "max_tokens": 1800,
                            "stream": False,
                        },
                    )
                if response.status_code < 400:
                    content = str(response.json()["choices"][0]["message"]["content"])
                    generated = _normalized_categories(self._json_object(content))
                    generation_state = "model_enriched"
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, RepositoryError):
            # A recognition profile remains usable from verified deterministic
            # sources when the configured model is temporarily unavailable.
            generation_state = "rules_only"

        generated["identityTerms"] = _sanitize_identity_terms(
            client, facts, generated["identityTerms"]
        )
        combined_people = [
            *generated["peopleAndOrganizations"],
            *evidence_categories["peopleAndOrganizations"],
        ]
        internal_people = _filter_internal_people_and_organizations(
            combined_people,
            facts,
            strategic_profile,
        )
        identity_keys = {
            term.casefold()
            for term in generated["identityTerms"]
        } | {
            term.casefold()
            for term in base["identityTerms"]
        }
        internal_people = [
            term for term in internal_people if term.casefold() not in identity_keys
        ]
        generated["peopleAndOrganizations"] = internal_people
        generated["productsAndPrograms"] = _filter_profile_specific_terms(
            generated["productsAndPrograms"],
            evidence_text=combined_evidence_text,
        )
        generated["domainTerms"] = _filter_profile_specific_terms(
            generated["domainTerms"],
            evidence_text=combined_evidence_text,
        )
        evidence_categories["peopleAndOrganizations"] = []
        evidence_categories["productsAndPrograms"] = _filter_profile_specific_terms(
            evidence_categories["productsAndPrograms"],
            evidence_text=combined_evidence_text,
        )
        evidence_categories["domainTerms"] = _filter_profile_specific_terms(
            evidence_categories["domainTerms"],
            evidence_text=combined_evidence_text,
        )
        provisional = _merge_categories(base, generated, evidence_categories, legacy_manual)
        concise_aliases = _concise_domain_aliases(provisional)
        categories = _merge_categories(base, concise_aliases, generated, evidence_categories, legacy_manual)
        for term in categories["identityTerms"]:
            _add_term(categories, "asrTerms", term)
        # 人工补充始终只显示在“补充”类别；它仍进入任务匹配，但不冒充模型从
        # 六卡或官网自动识别出的项目、人物或领域。
        supplement_keys = {term.casefold() for term in supplements}
        for key in PROFILE_CATEGORY_ORDER:
            categories[key] = [
                term for term in categories[key] if term.casefold() not in supplement_keys
            ]
        return _merge_categories(categories), generation_state, supplements

    def parse_draft(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Parse, but never save, one task/meeting draft for desktop or mobile."""
        text = str(payload.get("text") or "").strip()
        if not text:
            raise RepositoryError(422, "task_draft_text_required", "请输入或说出要记录的事项")
        current_date = str(payload.get("currentDate") or utc_now()[:10]).strip()
        profiles = self.list_profiles(identity)
        plans = gc06_planning.list_planning_cycles(
            self.repository, identity, include_archived=False
        )
        event_lines = gc06_planning.list_event_lines(
            self.repository, identity, include_archived=False
        )
        project_options = [
            {
                "clientId": row["clientId"],
                "name": row["clientName"],
                "keywords": list(row.get("keywords") or []),
            }
            for row in profiles
        ]
        plan_options = [
            {
                "planningCycleId": str(row.get("id") or row.get("planningCycleId") or ""),
                "title": str(row.get("title") or ""),
                "periodStart": row.get("periodStart"),
                "periodEnd": row.get("periodEnd"),
                "summary": str(row.get("summary") or "")[:500],
            }
            for row in plans
            if str(row.get("id") or row.get("planningCycleId") or "")
        ]
        with self.repository._connection() as connection:  # noqa: SLF001
            member_options = [
                {"membershipId": str(row["membership_id"]), "displayName": str(row["display_name"] or "")}
                for row in connection.execute(
                    "SELECT membership.id AS membership_id,principal.display_name "
                    "FROM organization_memberships AS membership "
                    "JOIN principals AS principal ON principal.id=membership.principal_id "
                    "WHERE membership.scope_id=? AND membership.record_kind='membership' "
                    "AND membership.status='active' AND membership.lifecycle_state='active' "
                    "AND principal.status='active' AND principal.lifecycle_state='active' "
                    "ORDER BY principal.display_name,membership.id",
                    (identity.scope_id,),
                ).fetchall()
            ]
        event_line_options = [
            {
                "eventLineId": str(row.get("id") or row.get("eventLineId") or ""),
                "clientId": str(row.get("clientId") or row.get("client_id") or row.get("primaryClientId") or ""),
                "name": str(row.get("name") or row.get("title") or ""),
            }
            for row in event_lines
            if str(row.get("id") or row.get("eventLineId") or "")
        ]
        provider = self.repository.ai_config(identity, include_secret=True)
        if provider.get("status") != "ready" or not provider.get("apiKey"):
            raise RepositoryError(409, "organization_ai_not_ready", "组织大模型尚未就绪")
        system = (
            "你是任务计划岗位的草稿解析器。只返回JSON对象，不保存或执行任何业务动作。"
            "字段：recordMode(task|customer_meeting|personal_schedule)、title、description、date(YYYY-MM-DD或null)、"
            "start(HH:MM或null)、end(HH:MM或null)、priority(low|normal|high)、clientId、eventLineId、planningCycleId、"
            "ownerMembershipId、collaboratorMembershipIds、reasons。项目、事件线、计划和成员只能从候选ID原样选择；事件线必须属于已选项目；"
            "任务中的‘负责/主责/牵头/交给’对应ownerMembershipId，‘协助/配合/参与’对应协作者；"
            "会议中的‘组织/主持/召集’对应ownerMembershipId（组织者），‘参会/列席/参与’对应协作者。负责人不得同时出现在协作者数组。"
            "今天/明天及上午/下午/晚上必须结合currentDate换算，下午未给时刻默认15:00。"
            "无把握时必须为null或空数组。普通任务默认normal；只有明确紧急、严重阻塞、"
            "法定或当天硬截止才high，明确可延后且影响小才low。会议须有明确会面/会议意图；个人日程只用于纯个人安排。"
            "不得编造日期时间，不得自动保存。reasons为简短中文数组，说明项目、计划、优先级判断依据。"
        )
        prompt = canonical_json(
            {
                "currentDate": current_date,
                "availableProjects": project_options,
                "availablePlans": plan_options,
                "availableMembers": member_options,
                "availableEventLines": event_line_options,
                "input": text,
            }
        )
        base = str(provider.get("baseUrl") or "").rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(connect=5, read=75, write=15, pool=5),
                trust_env=False,
            ) as client:
                response = client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {provider['apiKey']}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": provider["modelName"],
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "thinking": {"type": "disabled"},
                        "max_tokens": 1200,
                        "stream": False,
                    },
                )
        except httpx.HTTPError as exc:
            raise RepositoryError(503, "task_draft_parse_failed_retryable", "任务草稿解析暂时失败，可以重试") from exc
        if response.status_code >= 400:
            raise RepositoryError(
                503 if response.status_code >= 500 or response.status_code in {408, 425, 429} else 502,
                "task_draft_parse_failed_retryable",
                "任务草稿解析暂时失败，可以重试",
            )
        try:
            content = str(response.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise RepositoryError(502, "task_draft_parse_invalid", "组织模型没有返回有效的任务草稿") from exc
        parsed = self._json_object(content)
        allowed_projects = {row["clientId"] for row in project_options}
        allowed_plans = {row["planningCycleId"] for row in plan_options}
        allowed_members = {row["membershipId"] for row in member_options}
        owner_id = str(parsed.get("ownerMembershipId") or "") or None
        if owner_id not in allowed_members:
            owner_id = None
        event_line_by_id = {row["eventLineId"]: row for row in event_line_options}
        client_id = str(parsed.get("clientId") or "") or None
        plan_id = str(parsed.get("planningCycleId") or "") or None
        if client_id not in allowed_projects:
            client_id = None
        if plan_id not in allowed_plans:
            plan_id = None
        event_line_id = str(parsed.get("eventLineId") or "") or None
        if event_line_id not in event_line_by_id or not client_id or event_line_by_id[event_line_id]["clientId"] != client_id:
            event_line_id = None
        collaborator_ids = [
            str(value) for value in list(parsed.get("collaboratorMembershipIds") or [])
            if str(value) in allowed_members and str(value) != identity.membership_id
        ]
        mode = str(parsed.get("recordMode") or "task")
        if mode not in {"task", "customer_meeting", "personal_schedule"}:
            mode = "task"
        # The model is asked to classify member roles, but explicit Chinese
        # role phrases are deterministic enough to correct before returning a
        # draft.  This never saves the business object.
        for member in sorted(member_options, key=lambda item: len(item["displayName"]), reverse=True):
            member_id, name = member["membershipId"], re.escape(member["displayName"])
            owner_pattern = (
                rf"(?:由\s*)?{name}\s*(?:组织|主持|召集)"
                if mode == "customer_meeting"
                else rf"(?:由\s*)?{name}\s*(?:负责|主责|牵头|执行)|(?:负责人(?:是|为)?|交给)\s*{name}"
            )
            collaborator_pattern = (
                rf"{name}\s*(?:参会|列席|参与)"
                if mode == "customer_meeting"
                else rf"{name}\s*(?:协助|配合|参与|协作|测试)"
            )
            if re.search(owner_pattern, text):
                owner_id = member_id
            elif re.search(collaborator_pattern, text) and member_id != identity.membership_id:
                collaborator_ids.append(member_id)
        collaborator_ids = sorted(set(collaborator_ids) - ({owner_id} if owner_id else set()))
        priority = str(parsed.get("priority") or "normal")
        if priority not in {"low", "normal", "high"}:
            priority = "normal"
        parsed_date = str(parsed.get("date") or "") or None
        if not parsed_date and "今天" in text:
            parsed_date = current_date
        parsed_start = str(parsed.get("start") or "") or None
        if "下午" in text and (not parsed_start or parsed_start < "12:00"):
            parsed_start = "15:00"
        parsed_end = str(parsed.get("end") or "") or None
        if parsed_start and not parsed_end:
            hour, minute = (int(value) for value in parsed_start.split(":", 1))
            parsed_end = f"{min(23, hour + 1):02d}:{minute:02d}"
        result = {
            "recordMode": mode,
            "title": str(parsed.get("title") or "").strip()[:300] or text[:300],
            "description": str(parsed.get("description") or "").strip() or text,
            "date": parsed_date,
            "start": parsed_start,
            "end": parsed_end,
            "priority": priority,
            "clientId": client_id,
            "planningCycleId": plan_id,
            "eventLineId": event_line_id,
            "ownerMembershipId": owner_id,
            "collaboratorMembershipIds": collaborator_ids,
            "reasons": [str(item)[:200] for item in list(parsed.get("reasons") or [])[:5]],
            "sourceText": text,
        }
        now, run_id = utc_now(), new_id()
        bot_id = builtin_agent_id(identity.organization_id, "task_planning")
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute(
                "INSERT INTO execution_runs (id,scope_id,bot_id,rule_id,task_id,operation_id,status,"
                "initiator_membership_id,proposal_id,run_kind,progress_object_manifest_id,"
                "result_object_manifest_id,started_at,finished_at,version,lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,NULL,NULL,NULL,'completed',?,NULL,'task_draft_parse',NULL,NULL,?,?,1,'active',?,?,NULL)",
                (run_id, identity.scope_id, bot_id, identity.membership_id, now, now, now, now),
            )
            connection.commit()
        result["agentRun"] = AgentRunReceipt(
            agent_kind="task_planning",
            run_id=run_id,
            state="completed",
            stage="draft_ready",
            message="草稿已解析，等待人工确认保存",
            result_version=1,
        ).as_dict()
        return result

    def refresh_profile(
        self,
        identity: SessionIdentity,
        *,
        client_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        client_input, facts, strategic_profile, source_summary = self._recognition_inputs(
            identity,
            client_id=client_id,
        )
        categories, generation_state, supplements = self._synthesize_recognition_categories(
            identity,
            client=client_input,
            facts=facts,
            strategic_profile=strategic_profile,
            supplied=list(payload.get("keywords") or []),
        )
        keywords = _task_matching_keywords(categories, supplements)
        now = utc_now()
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                client = connection.execute(
                    "SELECT * FROM clients WHERE scope_id=? AND id=? AND lifecycle_state='active'",
                    (identity.scope_id, client_id),
                ).fetchone()
                if client is None:
                    raise RepositoryError(404, "client_not_found", "项目不存在")
                self.repository._require_project_access(  # noqa: SLF001
                    connection,
                    identity,
                    project_id=client_id,
                    capability="project_write",
                )
                profile_id = _stable_id("project_keyword_profile", identity.scope_id, client_id)
                current = connection.execute(
                    "SELECT * FROM narrative_outputs WHERE scope_id=? AND id=?",
                    (identity.scope_id, profile_id),
                ).fetchone()
                next_content_version = int(current["current_version"] or 0) + 1 if current else 1
                next_aggregate_version = int(current["version"] or 0) + 1 if current else 1
                receipt = canonical_json(
                    {
                        "schema": PROFILE_SCHEMA,
                        "clientId": client_id,
                        "keywords": keywords,
                        "categories": categories,
                        "supplements": supplements,
                        "sourceSummary": source_summary,
                        "generationState": generation_state,
                        "profileVersion": next_content_version,
                        "sourceFields": [
                            "clients.name",
                            "clients.alias",
                            "verified atomic_facts",
                            "strategic_profile",
                        ],
                        "updatedAt": now,
                    }
                )
                content_hash = sha256_text(receipt)
                manifest_id = _stable_id("manifest_project_keywords", profile_id, next_content_version)
                version_id = _stable_id("project_keywords_version", profile_id, next_content_version)
                connection.execute(
                    """
                    INSERT INTO object_manifests (
                        id,scope_id,storage_key,content_hash,lifecycle_state,receipt,
                        holder_role,holder_instance_id,storage_kind,byte_size,media_type,
                        availability_state,receipt_hash,created_at,verified_at,deleted_at,
                        authority_role,origin_instance_id
                    ) VALUES (?,?,NULL,?,'active',?,'cloud_task_planning',?,
                              'metadata_receipt',?,'application/vnd.yiyu.project-keywords+json',
                              'ready',?,?,?,NULL,'cloud',?)
                    """,
                    (
                        manifest_id,
                        identity.scope_id,
                        content_hash,
                        receipt,
                        self.repository.cloud_instance_id,
                        len(receipt.encode("utf-8")),
                        content_hash,
                        now,
                        now,
                        self.repository.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO secured_resources (
                        id,scope_id,resource_kind,lifecycle_state,version,
                        resource_type_key,created_at,updated_at,deleted_at,
                        authority_role,origin_instance_id
                    ) VALUES (?,?,'narrative_output','active',?,'project_keyword_profile',
                              ?,?,NULL,'cloud',?)
                    ON CONFLICT(id) DO UPDATE SET version=excluded.version,
                        lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL
                    """,
                    (profile_id, identity.scope_id, next_aggregate_version, now, now, self.repository.cloud_instance_id),
                )
                connection.execute(
                    """
                    INSERT INTO narrative_outputs (
                        id,scope_id,client_id,source_set_id,current_version,lifecycle_state,
                        title,artifact_kind,visibility_scope,publication_state,
                        owner_membership_id,published_at,version,created_at,updated_at,
                        deleted_at,authority_role,origin_instance_id
                    ) VALUES (?,?,?,NULL,?,'active',?,'project_keyword_profile',
                              'organization','published',?,?,?, ?,?,NULL,'cloud',?)
                    ON CONFLICT(id) DO UPDATE SET current_version=excluded.current_version,
                        title=excluded.title,publication_state='published',
                        published_at=excluded.published_at,version=excluded.version,
                        updated_at=excluded.updated_at,lifecycle_state='active',deleted_at=NULL
                    """,
                    (
                        profile_id,
                        identity.scope_id,
                        client_id,
                        next_content_version,
                        f"{str(client['name'] or '项目')}关键词画像",
                        identity.membership_id,
                        now,
                        next_aggregate_version,
                        str(current["created_at"] or now) if current else now,
                        now,
                        self.repository.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO artifact_versions (
                        id,scope_id,artifact_id,version,content_hash,object_manifest_id,
                        source_set_id,publication_state,created_by_membership_id,created_at,
                        origin_instance_id,integrity_hash,authority_role
                    ) VALUES (?,?,?,?,?,?,NULL,'published',?,?,?,?,'cloud')
                    """,
                    (
                        version_id,
                        identity.scope_id,
                        profile_id,
                        next_content_version,
                        content_hash,
                        manifest_id,
                        identity.membership_id,
                        now,
                        self.repository.cloud_instance_id,
                        sha256_text(f"{profile_id}|{next_content_version}|{content_hash}"),
                    ),
                )
                result = {
                    "clientId": client_id,
                    "clientName": str(client["name"] or ""),
                    "keywords": keywords,
                    "categories": categories,
                    "supplements": supplements,
                    "sourceSummary": source_summary,
                    "generationState": generation_state,
                    "state": "ready",
                    "version": next_content_version,
                    "updatedAt": now,
                }
                payload_hash = sha256_text(
                    canonical_json(
                        {
                            "clientId": client_id,
                            "categories": categories,
                            "supplements": supplements,
                            "sourceSummary": source_summary,
                        }
                    )
                )
                GC07ProjectMaterialsRepository._record_command(  # noqa: SLF001
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="task_planning.project_keyword_profile.refreshed",
                    aggregate_type="narrative_output",
                    aggregate_id=profile_id,
                    aggregate_version=next_aggregate_version,
                    expected_aggregate_version=int(current["version"] or 0) if current else None,
                    result=result,
                    target_resource_id=profile_id,
                )
                command = connection.execute(
                    "SELECT operation_id FROM commands WHERE scope_id=? AND idempotency_key=?",
                    (identity.scope_id, idempotency_key),
                ).fetchone()
                operation_id = str(command["operation_id"])
                run_id = self.repository._record_id("run", operation_id, "task-planning")  # noqa: SLF001
                bot_id = builtin_agent_id(identity.organization_id, "task_planning")
                connection.execute(
                    """
                    INSERT INTO execution_runs (
                        id,scope_id,bot_id,rule_id,task_id,operation_id,status,
                        initiator_membership_id,proposal_id,run_kind,
                        progress_object_manifest_id,result_object_manifest_id,
                        started_at,finished_at,version,lifecycle_state,created_at,
                        updated_at,deleted_at
                    ) VALUES (?,?,?,NULL,NULL,?,'completed',?,NULL,
                              'project_keyword_profile_refresh',NULL,?,?,?,1,
                              'active',?,?,NULL)
                    """,
                    (
                        run_id,
                        identity.scope_id,
                        bot_id,
                        operation_id,
                        identity.membership_id,
                        manifest_id,
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                result["agentRun"] = AgentRunReceipt(
                    agent_kind="task_planning",
                    run_id=run_id,
                    state="completed",
                    stage="keyword_profile_ready",
                    message="已更新项目安全关键词画像",
                    result_version=next_content_version,
                ).as_dict()
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
