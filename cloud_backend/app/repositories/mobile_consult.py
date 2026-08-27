"""Mobile consultation orchestration using strict knowledge and safe receipts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
from typing import Any

import httpx

from strict_common.agent_memory import AgentRunReceipt, builtin_agent_id
from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mobile-consult")
_RESULTS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()


def _source_text(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    direct = str(
        item.get("statement")
        or item.get("summary")
        or item.get("title")
        or item.get("name")
        or ""
    ).strip()
    if direct:
        return direct
    if item.get("subject") and item.get("object"):
        return " ".join(
            str(value).strip()
            for value in (
                item.get("subject"),
                item.get("predicate") or "相关",
                item.get("object"),
            )
            if str(value or "").strip()
        )
    return ""


def _source_title(item: dict[str, Any], fallback: str) -> str:
    return str(
        item.get("sourceDescription")
        or item.get("title")
        or item.get("name")
        or fallback
    ).strip()[:120]


def _classify_intent(question: str) -> str:
    normalized = re.sub(r"\s+", "", question.casefold())
    if any(
        token in normalized
        for token in (
            "什么模型", "哪个模型", "模型名称", "模型版本", "你是谁",
            "当前模型", "大模型配置", "连接状态", "当前组织", "工作空间",
        )
    ):
        return "live_system"
    creative = any(
        token in normalized
        for token in (
            "创意", "策划", "起名", "文案", "头脑风暴", "设想", "设计几个",
            "帮我写", "提供建议", "给些建议", "怎么办", "如何改进", "方案",
        )
    )
    factual = any(
        token in normalized
        for token in (
            "是谁", "是什么", "有哪些", "多少", "何时", "什么时候", "哪里",
            "是否", "有没有", "现任", "负责", "介绍", "事实", "依据", "来源",
        )
    )
    if creative and factual:
        return "hybrid"
    if creative:
        return "creative"
    if factual:
        return "project_fact"
    return "hybrid"


def _query_terms(value: str) -> set[str]:
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.casefold())
    terms = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,8}", value.casefold()))
    terms.update(normalized[index : index + 2] for index in range(max(0, len(normalized) - 1)))
    return {item for item in terms if item}


def _rank_sources(
    question: str,
    sources: list[dict[str, Any]],
    *,
    intent: str,
) -> list[dict[str, Any]]:
    question_terms = _query_terms(question)
    compact_question = re.sub(r"\s+", "", question.casefold())
    asks_people = any(
        token in compact_question
        for token in (
            "有哪些人", "什么人", "谁", "成员", "人员", "团队", "负责人",
            "创始人", "管理者", "顾问", "董事", "理事", "秘书长",
        )
    )
    priority = {
        "纠错/补充": 120,
        "事实澄清": 122,
        "明确记忆": 112,
        "收藏": 106,
        "官网事实": 100,
        "客户档案": 96,
        "项目画像": 92,
        "组织知识": 78,
        "关系知识": 58,
    }
    ranked: list[tuple[int, int, dict[str, str]]] = []
    for ordinal, item in enumerate(sources):
        haystack = f"{item.get('title', '')} {item.get('text', '')}".casefold()
        matches = sum(1 for term in question_terms if term in haystack)
        score = priority.get(item.get("kind", ""), 40) + matches * 24
        if asks_people and item.get("kind") in {"客户档案", "项目画像", "事实澄清"}:
            if "关键人物" in haystack or "人物与组织" in haystack:
                score += 260
            else:
                score += 120
        if intent == "project_fact" and item.get("kind") in {"纠错/补充", "官网事实"}:
            score += 18
        ranked.append((score, -ordinal, item))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    if asks_people:
        people_profiles = [
            row
            for row in ranked
            if row[2].get("kind") in {"客户档案", "项目画像", "事实澄清"}
            and ("关键人物" in str(row[2].get("text") or "")
                 or "人物与组织" in str(row[2].get("text") or ""))
        ]
        # A direct people-list question should consume the curated people facet,
        # not dozens of loosely matching page facts that merely repeat the client name.
        if people_profiles:
            ranked = people_profiles
    max_sources = 12 if intent == "creative" else 18 if intent == "project_fact" else 24
    max_characters = 14_000 if intent == "creative" else 22_000 if intent == "project_fact" else 34_000
    selected: list[dict[str, Any]] = []
    consumed = 0
    for _, _, item in ranked:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        remaining = max_characters - consumed
        if remaining <= 0 or len(selected) >= max_sources:
            break
        clipped = text[: min(2_000, remaining)]
        selected.append({**item, "text": clipped})
        consumed += len(clipped)
    return selected


def _source_object_kind(kind: str) -> str:
    return {
        "官网事实": "official_website_fact",
        "纠错/补充": "correction",
        "明确记忆": "explicit_memory",
        "收藏": "favorite",
        "事实澄清": "correction",
        # Client-profile and keyword-profile narratives are published project
        # knowledge.  The GC-14 answer contract intentionally has no separate
        # narrative source kind, so keep them on its existing auditable kind.
        "客户档案": "organization_knowledge",
        "项目画像": "organization_knowledge",
    }.get(kind, "organization_knowledge")


class MobileConsultRepository:
    def __init__(self, repository: CloudRepository):
        self.repository = repository

    def start(self, identity: SessionIdentity, *, payload: dict[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question") or "").strip()
        if not question:
            raise RepositoryError(422, "mobile_consult_question_required", "请输入问题")
        project_id = str(payload.get("projectId") or "").strip()
        if not project_id:
            raise RepositoryError(422, "mobile_consult_project_required", "请先选择项目")
        thread_id = str(payload.get("threadId") or "").strip() or new_id()
        safe_thread_summary = str(payload.get("safeThreadSummary") or "").strip()[-6_000:]
        agent_kind = "project_workspace"
        bot_id = builtin_agent_id(identity.organization_id, agent_kind)
        run_id, now = new_id(), utc_now()
        self._expire_delivery_results(identity, now=now)
        with self.repository._connection() as connection:  # noqa: SLF001
            self.repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=project_id,
            )
            bot = connection.execute(
                "SELECT id FROM bot_definitions WHERE id=? AND agent_kind=? AND enabled=1 AND lifecycle_state='active'",
                (bot_id, agent_kind),
            ).fetchone()
            if bot is None:
                raise RepositoryError(409, "mobile_consult_agent_not_ready", "对应的组织咨询能力尚未就绪")
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO execution_runs (id,scope_id,bot_id,rule_id,task_id,operation_id,status,initiator_membership_id,proposal_id,run_kind,progress_object_manifest_id,result_object_manifest_id,started_at,finished_at,version,lifecycle_state,created_at,updated_at,deleted_at) VALUES (?,?,?,NULL,NULL,NULL,'queued',?,NULL,?,NULL,NULL,NULL,NULL,1,'active',?,?,NULL)",
                    (run_id, identity.scope_id, bot_id, identity.membership_id, "mobile_project_consult", now, now),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        frozen = SessionIdentity(**identity.__dict__)
        _POOL.submit(
            self._execute,
            frozen,
            run_id,
            thread_id,
            question,
            project_id,
            safe_thread_summary,
        )
        return {"runId": run_id, "threadId": thread_id, "status": "queued", "agentRun": AgentRunReceipt(agent_kind=agent_kind, run_id=run_id, state="queued", stage="context_pending", message="正在整理已授权上下文").as_dict()}

    def _expire_delivery_results(self, identity: SessionIdentity, *, now: str) -> None:
        """Remove old encrypted answer bodies while retaining the audited run receipt."""
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute(
                "UPDATE object_manifests SET receipt=NULL,receipt_hash=NULL,byte_size=0,"
                "availability_state='expired',verified_at=? "
                "WHERE scope_id=? AND storage_kind='encrypted_delivery_receipt' "
                "AND availability_state='ready' AND datetime(created_at) < datetime(?, '-1 day')",
                (now, identity.scope_id, now),
            )
            connection.commit()

    def _persist_result(
        self,
        identity: SessionIdentity,
        *,
        run_id: str,
        result: dict[str, Any],
        status: str,
        finished_at: str,
    ) -> None:
        plaintext = canonical_json(result)
        encrypted = self.repository.cipher.encrypt(plaintext)
        manifest_id = self.repository._record_id(  # noqa: SLF001
            "manifest",
            run_id,
            "mobile-consult-delivery",
        )
        receipt = canonical_json(
            {
                "schema": "yiyu.mobile-consult-delivery.v1",
                "ciphertext": encrypted.ciphertext,
                "contentFingerprint": encrypted.fingerprint,
                "runId": run_id,
                "temporaryDelivery": True,
            }
        )
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,byte_size,media_type,availability_state,receipt_hash,created_at,verified_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,NULL,?,'active',?,'organization_cloud_ephemeral',?,'encrypted_delivery_receipt',?,'application/vnd.yiyu.mobile-consult-delivery+json','ready',?,?,?,NULL,'cloud',?) "
                    "ON CONFLICT(id) DO UPDATE SET content_hash=excluded.content_hash,receipt=excluded.receipt,byte_size=excluded.byte_size,receipt_hash=excluded.receipt_hash,verified_at=excluded.verified_at,availability_state='ready',lifecycle_state='active',deleted_at=NULL",
                    (
                        manifest_id,
                        identity.scope_id,
                        sha256_text(plaintext),
                        receipt,
                        identity.cloud_instance_id,
                        len(receipt.encode("utf-8")),
                        sha256_text(receipt),
                        finished_at,
                        finished_at,
                        identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "UPDATE execution_runs SET status=?,result_object_manifest_id=?,finished_at=?,updated_at=?,version=version+1 WHERE id=? AND scope_id=?",
                    (status, manifest_id, finished_at, finished_at, run_id, identity.scope_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        with _LOCK:
            _RESULTS[run_id] = result

    def _persisted_result(
        self,
        identity: SessionIdentity,
        *,
        manifest_id: str | None,
    ) -> dict[str, Any] | None:
        if not manifest_id:
            return None
        with self.repository._connection() as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT receipt FROM object_manifests WHERE id=? AND scope_id=? AND lifecycle_state='active' "
                "AND storage_kind='encrypted_delivery_receipt' AND availability_state='ready'",
                (manifest_id, identity.scope_id),
            ).fetchone()
        if row is None:
            return None
        try:
            receipt = json.loads(str(row["receipt"] or "{}"))
            plaintext = self.repository.cipher.decrypt(str(receipt["ciphertext"]))
            result = json.loads(plaintext)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return result if isinstance(result, dict) else None

    def _execute(
        self,
        identity: SessionIdentity,
        run_id: str,
        thread_id: str,
        question: str,
        project_id: str,
        safe_thread_summary: str,
    ) -> None:
        started = utc_now()
        try:
            with self.repository._connection() as connection:  # noqa: SLF001
                connection.execute("UPDATE execution_runs SET status='running',started_at=?,updated_at=?,version=version+1 WHERE id=?", (started, started, run_id))
                connection.commit()
            intent = _classify_intent(question)
            context = self.repository.project_knowledge_context(identity, project_id=project_id)
            sources: list[dict[str, Any]] = []
            groups = (
                ("纠错/补充", [item for item in context.get("savedMemories", []) if str(item.get("memoryKind") or "") == "correction"]),
                ("明确记忆", [item for item in context.get("savedMemories", []) if str(item.get("memoryKind") or "") == "explicit_memory"]),
                ("收藏", [item for item in context.get("savedMemories", []) if str(item.get("memoryKind") or "") == "favorite"]),
                ("官网事实", context.get("officialWebsiteFacts", [])),
                ("组织知识", context.get("organizationSharedKnowledge", [])),
                ("关系知识", context.get("relationshipCards", [])),
            )
            for label, rows in groups:
                for item in rows:
                    text = _source_text(item)
                    if text:
                        sources.append(
                            {
                                "kind": label,
                                "id": str(item.get("id") or item.get("sourceId") or item.get("sourceObjectId") or sha256_text(text)[:24]),
                                "title": _source_title(item, label),
                                "text": text,
                            }
                        )
            profile_source = self._strategic_profile_source(
                identity,
                project_id=project_id,
            )
            if profile_source is not None:
                sources.append(profile_source)
            keyword_profile_source = self._project_keyword_profile_source(
                identity,
                project_id=project_id,
            )
            if keyword_profile_source is not None:
                sources.append(keyword_profile_source)
            clarification_source = self._strategic_profile_clarification_source(
                identity,
                project_id=project_id,
            )
            if clarification_source is not None:
                sources.append(clarification_source)
            selected = _rank_sources(question, sources, intent=intent)
            provider = self.repository.ai_config(identity, include_secret=True)
            if provider.get("status") != "ready" or not provider.get("apiKey"):
                raise RepositoryError(409, "organization_ai_not_ready", "组织大模型尚未就绪")
            if intent == "live_system" and any(token in question for token in ("模型", "你是谁")):
                answer = (
                    "我是益语智库AI手机版的项目咨询助手。"
                    f"当前组织实际配置的生成模型是 {provider['modelName']}。"
                )
                selected = []
            else:
                evidence = "\n".join(
                    f"[{item['kind']}｜{item['title']}] {item['text']}"
                    for item in selected
                )
                if intent == "project_fact" and not evidence:
                    answer = "当前项目尚无足够的已确认知识支持这个事实判断。"
                else:
                    intent_rule = {
                        "project_fact": "这是项目事实问题。只陈述来源支持的事实，证据不足就明确指出。",
                        "creative": "这是创意或建议问题。可以使用通用能力，但必须结合项目背景，并把建议与既有事实分开。",
                        "hybrid": "这是综合问题。先给已核实事实，再给明确标注的分析或建议。",
                        "live_system": "这是实时系统问题。只根据实时配置回答，不引用项目旧知识猜测。",
                    }[intent]
                    prompt = (
                        "你是益语智库AI手机版的项目咨询助手。回答要适合出差或现场快速查看："
                        "结论优先，通常控制在300至800字；不要为了显得完整而编造。"
                        f"{intent_rule}\n"
                    )
                    if any(token in re.sub(r"\s+", "", question) for token in ("有哪些人", "什么人", "成员", "人员", "团队")):
                        prompt += (
                            "这是名单问题：直接给人数和姓名；来源明确时可附一行职务。"
                            "不要扩展机构特色、咨询方法、服务领域或其他背景。\n"
                        )
                    if safe_thread_summary:
                        prompt += f"近期对话（只用于理解指代和连续追问）：\n{safe_thread_summary}\n\n"
                    prompt += f"已授权项目上下文：\n{evidence or '当前没有可用项目资料'}\n\n用户问题：{question}"
                    base = str(provider.get("baseUrl") or "").rstrip("/")
                    endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
                    with httpx.Client(timeout=httpx.Timeout(connect=5, read=100, write=15, pool=5), trust_env=False) as client:
                        response = client.post(endpoint, headers={"Authorization": f"Bearer {provider['apiKey']}", "Content-Type": "application/json"}, json={"model": provider["modelName"], "messages": [{"role":"user","content":prompt}], "temperature": 0.35 if intent in {"creative", "hybrid"} else 0.15, "thinking":{"type":"disabled"}, "max_tokens":1400, "stream":False})
                    if response.status_code >= 400:
                        raise RepositoryError(503 if response.status_code >= 500 or response.status_code in {408, 425, 429} else 502, "organization_ai_failed_retryable", "组织模型暂时失败，可以重试")
                    answer = str(response.json()["choices"][0]["message"]["content"]).strip()
            if not answer:
                raise RepositoryError(502, "organization_ai_response_empty", "组织模型返回了空回答")
            selected_sources = [
                    {
                        "sourceObjectId": item["id"],
                        "sourceObjectKind": _source_object_kind(item["kind"]),
                        "sourceVersion": max(1, int(item.get("version") or 1)),
                        "contentHash": sha256_text(item["text"]),
                    }
                    for item in selected
                ]
            answer_id, source_set_id, context_id, lineage_id = new_id(), new_id(), new_id(), new_id()
            receipt = self.repository.save_ai_answer(
                identity,
                payload={
                        "answerId": answer_id,
                        "projectId": project_id,
                        "threadId": thread_id,
                        "questionHash": sha256_text(question),
                        "answerHash": sha256_text(answer),
                        "sourceSetId": source_set_id,
                        "contextManifestId": context_id,
                        "lineageId": lineage_id,
                        "botId": builtin_agent_id(identity.organization_id, "project_workspace"),
                        "providerResourceId": provider["configId"],
                        "modelName": provider["modelName"],
                        "sourceCount": len(selected_sources),
                        "materialAccessMode": "live_system_configuration" if intent == "live_system" else "organization_shared_only",
                        "boundaryState": "live_configuration" if intent == "live_system" else "organization_safe_sources",
                        "selectedSources": selected_sources,
                        "originInstanceId": identity.cloud_instance_id,
                },
                idempotency_key=f"mobile-consult-answer:{run_id}",
            )
            answer_version = int(receipt["answer"]["version"])
            finished = utc_now()
            result = {
                "answer": answer,
                "answerId": answer_id,
                "answerVersion": answer_version,
                "intent": intent,
                "sources": [
                    {"kind": item["kind"], "title": item["title"], "sourceObjectId": item["id"]}
                    for item in selected
                ],
                "answerHash": sha256_text(answer),
                "questionHash": sha256_text(question),
                "threadId": thread_id,
                "projectId": project_id,
                "modelName": provider.get("modelName"),
                "expiresAfterRead": True,
            }
            self._persist_result(
                identity,
                run_id=run_id,
                result=result,
                status="completed",
                finished_at=finished,
            )
        except Exception as exc:  # settle every run; raw exception never leaves the server
            retryable = isinstance(exc, (httpx.HTTPError, httpx.TimeoutException)) or isinstance(exc, RepositoryError) and exc.status_code >= 500
            state = "failed_retryable" if retryable else "blocked"
            message = exc.message if isinstance(exc, RepositoryError) else "移动咨询执行失败，可以重试"
            finished = utc_now()
            try:
                self._persist_result(
                    identity,
                    run_id=run_id,
                    result={"error": message, "retryable": retryable},
                    status=state,
                    finished_at=finished,
                )
            except Exception:
                with self.repository._connection() as connection:  # noqa: SLF001
                    connection.execute("UPDATE execution_runs SET status=?,finished_at=?,updated_at=?,version=version+1 WHERE id=?", (state, finished, finished, run_id))
                    connection.commit()
                with _LOCK:
                    _RESULTS[run_id] = {"error": message, "retryable": retryable}

    def _strategic_profile_source(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any] | None:
        """Expose the published client profile as one bounded retrieval source."""
        with self.repository._connection() as connection:  # noqa: SLF001
            row = connection.execute(
                """
                SELECT narrative.id, narrative.title, narrative.current_version,
                       manifest.receipt
                  FROM narrative_outputs AS narrative
                  JOIN artifact_versions AS version
                    ON version.scope_id=narrative.scope_id
                   AND version.artifact_id=narrative.id
                   AND version.version=narrative.current_version
                  JOIN object_manifests AS manifest
                    ON manifest.scope_id=version.scope_id
                   AND manifest.id=version.object_manifest_id
                   AND manifest.lifecycle_state='active'
                 WHERE narrative.scope_id=? AND narrative.client_id=?
                   AND narrative.artifact_kind='strategic_profile'
                   AND narrative.publication_state='published'
                   AND narrative.lifecycle_state='active'
                 ORDER BY narrative.updated_at DESC, narrative.id
                 LIMIT 1
                """,
                (identity.scope_id, project_id),
            ).fetchone()
        if row is None:
            return None
        try:
            receipt = json.loads(str(row["receipt"] or "{}"))
        except json.JSONDecodeError:
            return None
        if not isinstance(receipt, dict):
            return None
        dimension_titles = {
            "essence": "项目定位",
            "business_intro": "业务与项目",
            "cooperation": "合作关系",
            "people": "关键人物",
            "timeline": "重要进展",
            "next_steps": "下一步",
        }
        sections: list[str] = []
        for item in receipt.get("dimensions") or []:
            if not isinstance(item, dict):
                continue
            narrative = str(item.get("narrative") or "").strip()
            if not narrative:
                continue
            dimension = str(item.get("dimension") or "").strip()
            sections.append(f"{dimension_titles.get(dimension, dimension or '客户档案')}：{narrative}")
        if not sections:
            return None
        client_name = str(receipt.get("clientName") or "").strip()
        return {
            "kind": "客户档案",
            "id": str(row["id"]),
            "title": str(row["title"] or (f"{client_name}客户档案" if client_name else "客户档案")),
            "text": "\n".join(sections)[:12_000],
            "version": max(1, int(row["current_version"] or 1)),
        }

    def _project_keyword_profile_source(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any] | None:
        """Expose curated project identity/people terms, excluding broad ASR terms."""
        with self.repository._connection() as connection:  # noqa: SLF001
            row = connection.execute(
                """
                SELECT narrative.id, narrative.title, narrative.current_version,
                       manifest.receipt
                  FROM narrative_outputs AS narrative
                  JOIN artifact_versions AS version
                    ON version.scope_id=narrative.scope_id
                   AND version.artifact_id=narrative.id
                   AND version.version=narrative.current_version
                  JOIN object_manifests AS manifest
                    ON manifest.scope_id=version.scope_id
                   AND manifest.id=version.object_manifest_id
                   AND manifest.lifecycle_state='active'
                 WHERE narrative.scope_id=? AND narrative.client_id=?
                   AND narrative.artifact_kind='project_keyword_profile'
                   AND narrative.lifecycle_state='active'
                 ORDER BY narrative.updated_at DESC, narrative.id
                 LIMIT 1
                """,
                (identity.scope_id, project_id),
            ).fetchone()
        if row is None:
            return None
        try:
            receipt = json.loads(str(row["receipt"] or "{}"))
        except json.JSONDecodeError:
            return None
        categories = receipt.get("categories") if isinstance(receipt, dict) else None
        if not isinstance(categories, dict):
            return None
        category_titles = (
            ("identityTerms", "名称与别称"),
            ("peopleAndOrganizations", "关键人物与组织"),
            ("domainTerms", "服务领域"),
            ("productsAndPrograms", "重点项目与产品"),
        )
        sections: list[str] = []
        for key, title in category_titles:
            values = [
                str(value).strip()
                for value in categories.get(key) or []
                if str(value or "").strip()
            ]
            if values:
                sections.append(f"{title}：{'、'.join(values[:40])}")
        supplements = [
            str(value).strip()
            for value in receipt.get("supplements") or []
            if str(value or "").strip()
        ]
        if supplements:
            sections.append(f"成员补充：{'、'.join(supplements[:40])}")
        if not sections:
            return None
        return {
            # This object is a retrieval index derived from the client dossier
            # and verified clarifications.  Do not expose the index itself as
            # an authority label to users.
            "kind": "客户档案",
            "id": str(row["id"]),
            "title": "客户档案·项目人物与业务索引",
            "text": "\n".join(sections)[:8_000],
            "version": max(1, int(row["current_version"] or 1)),
        }

    def _strategic_profile_clarification_source(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any] | None:
        """Expose verified dossier clarifications as their own authority source."""
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                """
                SELECT fact.id, fact.version, fact.updated_at, manifest.receipt
                  FROM atomic_facts AS fact
                  JOIN source_sets AS sources
                    ON sources.id=fact.source_set_id
                   AND sources.scope_id=fact.scope_id
                   AND sources.client_id=?
                   AND sources.purpose_kind='strategic_profile_clarification'
                   AND sources.publication_state='published'
                   AND sources.lifecycle_state='active'
                  JOIN object_manifests AS manifest
                    ON manifest.id=fact.fact_object_manifest_id
                   AND manifest.scope_id=fact.scope_id
                   AND manifest.lifecycle_state='active'
                 WHERE fact.scope_id=? AND fact.verification_state='verified'
                   AND fact.lifecycle_state='active'
                 ORDER BY fact.updated_at DESC, fact.id
                 LIMIT 40
                """,
                (project_id, identity.scope_id),
            ).fetchall()
        sections: list[str] = []
        ids: list[str] = []
        version = 1
        for row in rows:
            try:
                receipt = json.loads(str(row["receipt"] or "{}"))
            except json.JSONDecodeError:
                continue
            if not isinstance(receipt, dict):
                continue
            statement = str(receipt.get("statement") or "").strip()
            if not statement:
                continue
            dimension = str(receipt.get("dimension") or "事实").strip()
            sections.append(f"{dimension}：{statement}")
            ids.append(str(row["id"]))
            version = max(version, int(row["version"] or 1))
        if not sections:
            return None
        return {
            "kind": "事实澄清",
            "id": self.repository._record_id(  # noqa: SLF001
                "mobile_consult_clarifications", identity.scope_id, project_id
            ),
            "title": "客户档案·事实澄清",
            "text": "\n".join(sections)[:8_000],
            "version": version,
        }

    def get(self, identity: SessionIdentity, *, run_id: str) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            row = connection.execute("SELECT run.id,run.status,run.run_kind,run.started_at,run.finished_at,run.version,run.result_object_manifest_id,bot.agent_kind FROM execution_runs AS run JOIN bot_definitions AS bot ON bot.id=run.bot_id WHERE run.id=? AND run.scope_id=? AND run.initiator_membership_id=? AND run.lifecycle_state='active'", (run_id, identity.scope_id, identity.membership_id)).fetchone()
        if row is None:
            raise RepositoryError(404, "mobile_consult_run_missing", "咨询运行不存在")
        with _LOCK:
            result = _RESULTS.get(run_id)
        if result is None:
            result = self._persisted_result(
                identity,
                manifest_id=str(row["result_object_manifest_id"] or "") or None,
            )
        status = str(row["status"])
        if status in {"completed", "blocked", "failed_retryable", "failed"} and result is None:
            status, result = "failed_retryable", {"error": "运行结果已随服务重启失效，请重新提问", "retryable": True}
        return {"runId": run_id, "status": status, "startedAt": row["started_at"], "finishedAt": row["finished_at"], "agentRun": AgentRunReceipt(agent_kind=str(row["agent_kind"]), run_id=run_id, state=status, stage="answer_ready" if status=="completed" else "generating" if status in {"queued","running"} else "settled", message="回答已完成" if status=="completed" else "正在基于已确认上下文回答" if status in {"queued","running"} else str((result or {}).get("error") or "运行结束"), retryable=bool((result or {}).get("retryable")), result_version=int(row["version"] or 1)).as_dict(), "result": result if status not in {"queued", "running"} else None}

    def list_favorites(self, identity: SessionIdentity, *, project_id: str) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self.repository._require_project_access(connection, identity, project_id=project_id)  # noqa: SLF001
            rows = connection.execute(
                "SELECT sets.id,sets.version,sets.updated_at,manifest.receipt FROM source_sets AS sets "
                "LEFT JOIN source_set_members AS excerpt ON excerpt.scope_id=sets.scope_id AND excerpt.source_set_id=sets.id "
                "AND excerpt.source_object_kind='favorite_excerpt' AND excerpt.lifecycle_state='active' "
                "LEFT JOIN object_manifests AS manifest ON manifest.scope_id=sets.scope_id AND manifest.id=excerpt.source_object_id "
                "WHERE sets.scope_id=? AND sets.client_id=? AND sets.created_by_principal_id=? "
                "AND sets.purpose_kind='answer_favorite' AND sets.lifecycle_state='active' ORDER BY sets.updated_at DESC",
                (identity.scope_id, project_id, identity.principal_id),
            ).fetchall()
        result=[]
        for row in rows:
            try: receipt=json.loads(str(row["receipt"] or "{}"))
            except json.JSONDecodeError: receipt={}
            result.append({"favoriteId":str(row["id"]),"answerId":receipt.get("answerId"),"text":receipt.get("excerpt") or "","version":int(row["version"] or 1),"updatedAt":row["updated_at"]})
        return {"projectId":project_id,"favorites":result}

    def favorite(self, identity: SessionIdentity, *, answer_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        project_id=str(payload.get("projectId") or "").strip(); excerpt=str(payload.get("excerpt") or "").strip()[:4000]
        if not project_id or not excerpt: raise RepositoryError(422,"favorite_payload_invalid","收藏内容不完整")
        now=utc_now()
        with self.repository._connection() as connection:  # noqa: SLF001
            self.repository._require_project_access(connection,identity,project_id=project_id)  # noqa: SLF001
            answer=connection.execute("SELECT id,version FROM ai_answers WHERE id=? AND scope_id=? AND client_id=? AND lifecycle_state='active'",(answer_id,identity.scope_id,project_id)).fetchone()
            if answer is None: raise RepositoryError(404,"favorite_answer_missing","回答不存在或已失效")
            set_id=self.repository._record_id("source_set",sha256_text(f"{identity.scope_id}|{identity.principal_id}|{project_id}|{answer_id}"),"answer_favorite")  # noqa: SLF001
            manifest_id=self.repository._record_id("manifest",set_id,"excerpt")  # noqa: SLF001
            receipt=canonical_json({"schema":"yiyu.answer-favorite.v1","answerId":answer_id,"excerpt":excerpt,"excerptHash":sha256_text(excerpt)})
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,byte_size,media_type,availability_state,receipt_hash,created_at,verified_at,deleted_at,authority_role,origin_instance_id) VALUES (?,?,NULL,?,'active',?,'member_cloud',?,'metadata_receipt',?,'application/vnd.yiyu.answer-favorite+json','ready',?,?,?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET content_hash=excluded.content_hash,receipt=excluded.receipt,byte_size=excluded.byte_size,receipt_hash=excluded.receipt_hash,verified_at=excluded.verified_at,lifecycle_state='active',deleted_at=NULL",(manifest_id,identity.scope_id,sha256_text(excerpt),receipt,identity.principal_id,len(receipt.encode()),sha256_text(receipt),now,now,identity.cloud_instance_id))
                connection.execute("INSERT INTO source_sets (id,scope_id,client_id,security_label_set_version,source_count,version,purpose_kind,publication_state,created_by_principal_id,created_at,expires_at,lifecycle_state,updated_at,deleted_at,authority_role,origin_instance_id) VALUES (?,?,?,'member-private-v1',2,1,'answer_favorite','published',?,?,NULL,'active',?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET source_count=2,version=source_sets.version+1,lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL",(set_id,identity.scope_id,project_id,identity.principal_id,now,now,identity.cloud_instance_id))
                for ordinal,(kind,obj,version) in enumerate((("ai_answer",answer_id,int(answer["version"] or 1)),("favorite_excerpt",manifest_id,1))):
                    member_id=self.repository._record_id("source_member",set_id,f"{kind}:{obj}")  # noqa: SLF001
                    connection.execute("INSERT INTO source_set_members (id,scope_id,source_set_id,source_object_id,source_version,policy_version,source_object_kind,ordinal,added_at,removed_at,version,lifecycle_state,created_at,updated_at,deleted_at,authority_role,origin_instance_id) VALUES (?,?,?,?,?,1,?,?,?,NULL,1,'active',?,?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET removed_at=NULL,lifecycle_state='active',deleted_at=NULL,updated_at=excluded.updated_at,version=source_set_members.version+1",(member_id,identity.scope_id,set_id,obj,version,kind,ordinal,now,now,now,identity.cloud_instance_id))
                connection.commit()
            except Exception: connection.rollback(); raise
        return {"favoriteId":set_id,"answerId":answer_id,"text":excerpt,"version":1,"updatedAt":now}

    def unfavorite(self, identity: SessionIdentity, *, favorite_id: str) -> dict[str, Any]:
        now=utc_now()
        with self.repository._connection() as connection:  # noqa: SLF001
            row=connection.execute("SELECT id FROM source_sets WHERE id=? AND scope_id=? AND created_by_principal_id=? AND purpose_kind='answer_favorite' AND lifecycle_state='active'",(favorite_id,identity.scope_id,identity.principal_id)).fetchone()
            if row is None: raise RepositoryError(404,"favorite_missing","收藏不存在或已取消")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE source_sets SET lifecycle_state='archived',publication_state='revoked',updated_at=?,version=version+1 WHERE id=?",(now,favorite_id))
            connection.execute("UPDATE source_set_members SET lifecycle_state='archived',removed_at=?,updated_at=?,version=version+1 WHERE source_set_id=? AND lifecycle_state='active'",(now,now,favorite_id))
            connection.commit()
        return {"favoriteId":favorite_id,"removed":True}
