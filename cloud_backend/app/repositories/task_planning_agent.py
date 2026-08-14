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


PROFILE_SCHEMA = "yiyu.task-planning.project-keyword-profile.v2"

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


def _keywords(client: Mapping[str, Any], supplied: list[Any] | None = None) -> list[str]:
    values = [
        str(client.get("name") or ""),
        str(client.get("alias") or ""),
        str(client.get("domain") or ""),
        str(client.get("summary") or ""),
        *[str(item or "") for item in supplied or []],
    ]
    result: list[str] = []

    def add(candidate: str) -> None:
        normalized = " ".join(candidate.split()).strip(" ._-/，。；：、（）()[]【】")
        if (
            len(normalized) < 2
            or len(normalized) > 32
            or any(marker in normalized for marker in _PLACEHOLDER_MARKERS)
            or normalized in result
        ):
            return
        result.append(normalized)

    for value in values:
        normalized = " ".join(value.split())
        if not normalized or any(marker in normalized for marker in _PLACEHOLDER_MARKERS):
            continue
        # Keep meaningful phrases and explicit list items.  Arbitrary Chinese
        # n-grams ("慈基", "金会") are not safe search keywords.
        for phrase in re.split(r"[\n,，。；;：:、|/（）()【】\[\]]+", normalized):
            add(phrase)
        for token in re.findall(r"[A-Za-z][A-Za-z0-9._-]{1,31}", normalized):
            add(token.casefold())
    for raw_name in (str(client.get("name") or ""), str(client.get("alias") or "")):
        name = " ".join(raw_name.split())
        add(name)
        for suffix in _NAME_SUFFIXES:
            if name.endswith(suffix) and len(name) - len(suffix) >= 2:
                add(name[: -len(suffix)])
                break
    return result[:48]


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
                result.append(
                    {
                        "clientId": str(client["id"]),
                        "clientName": str(client["name"] or ""),
                        "keywords": list(payload.get("keywords") or _keywords(dict(client))),
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
                supporting_text = [
                    str(row["title"] or "")
                    for row in connection.execute(
                        "SELECT title FROM knowledge_documents WHERE scope_id=? "
                        "AND client_id=? AND lifecycle_state='active'",
                        (identity.scope_id, client_id),
                    ).fetchall()
                ]
                for row in connection.execute(
                    """
                    SELECT manifest.receipt
                    FROM atomic_facts AS fact
                    JOIN source_sets AS sources
                      ON sources.scope_id=fact.scope_id AND sources.id=fact.source_set_id
                    JOIN object_manifests AS manifest
                      ON manifest.scope_id=fact.scope_id
                     AND manifest.id=fact.fact_object_manifest_id
                    WHERE fact.scope_id=? AND sources.client_id=?
                      AND fact.lifecycle_state='active'
                      AND fact.verification_state IN ('verified','confirmed')
                    """,
                    (identity.scope_id, client_id),
                ).fetchall():
                    try:
                        fact = json.loads(str(row["receipt"] or "{}"))
                    except json.JSONDecodeError:
                        fact = {}
                    if isinstance(fact, Mapping):
                        supporting_text.extend(
                            str(fact.get(key) or "")
                            for key in ("statement", "title", "canonicalValue", "summary")
                        )
                profile_id = _stable_id("project_keyword_profile", identity.scope_id, client_id)
                current = connection.execute(
                    "SELECT * FROM narrative_outputs WHERE scope_id=? AND id=?",
                    (identity.scope_id, profile_id),
                ).fetchone()
                next_content_version = int(current["current_version"] or 0) + 1 if current else 1
                next_aggregate_version = int(current["version"] or 0) + 1 if current else 1
                keywords = _keywords(
                    dict(client),
                    [*supporting_text, *list(payload.get("keywords") or [])],
                )
                receipt = canonical_json(
                    {
                        "schema": PROFILE_SCHEMA,
                        "clientId": client_id,
                        "keywords": keywords,
                        "profileVersion": next_content_version,
                        "sourceFields": ["clients.name", "clients.alias", "clients.domain", "clients.summary"],
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
                    "state": "ready",
                    "version": next_content_version,
                    "updatedAt": now,
                }
                payload_hash = sha256_text(canonical_json({"clientId": client_id, "keywords": keywords}))
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
