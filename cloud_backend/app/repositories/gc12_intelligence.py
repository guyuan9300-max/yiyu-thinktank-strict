"""GC-12 intelligence list and member attention signals on the frozen 88 tables."""

from __future__ import annotations

import json
from typing import Any, Mapping

from strict_common.ids import canonical_json, sha256_text, utc_now
from strict_common.agent_memory import AgentRunReceipt, builtin_agent_id
from strict_common.security import payload_fingerprint

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .project_materials import GC07ProjectMaterialsRepository


def _bounded_int(value: Any, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    return max(minimum, min(maximum, parsed))


class GC12IntelligenceRepository:
    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _receipt(raw: Any) -> dict[str, Any]:
        try:
            value = json.loads(str(raw or "{}"))
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    @staticmethod
    def _view(row: Mapping[str, Any], receipt: Mapping[str, Any], status: str) -> dict[str, Any]:
        source_url = str(receipt.get("sourceUrl") or "").strip() or None
        statement = str(
            receipt.get("statement")
            or receipt.get("summary")
            or receipt.get("valueText")
            or row["title"]
            or ""
        ).strip()
        content_kind = str(receipt.get("contentKind") or "timely_intelligence")
        if content_kind not in {"brand_mirror", "timely_intelligence", "public_opinion"}:
            content_kind = "timely_intelligence"
        return {
            "id": str(row["id"]),
            "contentKind": content_kind,
            "scopeType": "client",
            "scopeId": str(row["client_id"] or ""),
            "clientId": str(row["client_id"] or "") or None,
            "projectModuleId": None,
            "title": str(row["title"] or statement or "未命名情报"),
            "summary": statement,
            "keyPoints": [],
            "analysis": str(receipt.get("relevanceReason") or ""),
            "impact": str(receipt.get("impact") or ""),
            "intelligenceType": str(receipt.get("sourceType") or "project_intelligence"),
            "timelinessLabel": None,
            "relevanceReason": str(
                receipt.get("relevanceReason")
                or "与当前项目权威知识或已登记公开来源相关"
            ),
            "suggestedAction": "核对来源后决定是否用于业务行动",
            "followupQuestions": [],
            "tags": list(receipt.get("tags") or []),
            "source": str(receipt.get("sourceTitle") or "项目情报"),
            "sourceUrl": source_url,
            "publishedAt": receipt.get("publishedAt") or row["confirmed_at"],
            "capturedAt": str(row["created_at"]),
            "verifiedAt": row["confirmed_at"],
            "dataCenterIngestEventId": None,
            "externalEvidenceCardId": None,
            "topicCandidateId": None,
            "convertedTaskId": row["converted_task_id"] if "converted_task_id" in row.keys() else None,
            "verificationStatus": str(row["verification_state"] or "candidate"),
            "verificationReason": str(receipt.get("verificationBasis") or ""),
            "sentimentLabel": str(receipt.get("sentimentLabel") or "unclassified"),
            "sentimentReason": str(receipt.get("sentimentReason") or "尚未完成情感分类"),
            "userStatus": status,
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
            "version": int(row["version"] or 1),
        }

    def list_items(self, identity: SessionIdentity, query: Mapping[str, Any]) -> dict[str, Any]:
        projects = GC07ProjectMaterialsRepository(self.repository).list_projects(identity)["projects"]
        visible_ids = [str(item["projectId"]) for item in projects]
        requested = str(query.get("workObjectId") or query.get("clientId") or "").strip()
        if requested:
            if requested not in visible_ids:
                raise RepositoryError(404, "intelligence_project_missing", "当前成员无法访问该项目情报")
            visible_ids = [requested]
        try:
            page = max(1, int(query.get("page") or 1))
            page_size = max(1, min(100, int(query.get("pageSize") or 20)))
        except (TypeError, ValueError):
            raise RepositoryError(422, "intelligence_page_invalid", "情报分页参数无效")
        if not visible_ids:
            return {"items": [], "candidateSamples": [], "total": 0, "page": page, "pageSize": page_size}
        placeholders = ",".join("?" for _ in visible_ids)
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                f"""
                SELECT i.*, m.receipt,
                       (SELECT task.id FROM tasks AS task
                        WHERE task.scope_id=i.scope_id
                          AND task.source_type='intelligence_record'
                          AND task.source_id=i.id
                          AND task.lifecycle_state!='deleted'
                        ORDER BY task.created_at DESC,task.id LIMIT 1)
                        AS converted_task_id
                FROM intelligence_records AS i
                JOIN source_sets AS capture_sources
                  ON capture_sources.scope_id=i.scope_id
                 AND capture_sources.id=i.source_set_id
                 AND capture_sources.purpose_kind IN (
                    'manual_intelligence_capture','public_opinion_capture'
                 )
                 AND capture_sources.lifecycle_state='active'
                LEFT JOIN object_manifests AS m
                  ON m.scope_id=i.scope_id AND m.id=i.summary_object_manifest_id
                 AND m.lifecycle_state='active'
                WHERE i.scope_id=? AND i.client_id IN ({placeholders})
                  AND i.lifecycle_state='active'
                ORDER BY i.updated_at DESC, i.id
                """,
                (identity.scope_id, *visible_ids),
            ).fetchall()
            attention_rows = connection.execute(
                """
                SELECT member.source_object_id, sources.purpose_kind
                FROM source_sets AS sources
                JOIN source_set_members AS member
                  ON member.scope_id=sources.scope_id
                 AND member.source_set_id=sources.id
                 AND member.lifecycle_state='active'
                WHERE sources.scope_id=? AND sources.created_by_principal_id=?
                  AND sources.purpose_kind IN ('intelligence_follow','intelligence_dismiss')
                  AND sources.lifecycle_state='active'
                  AND member.source_object_kind='intelligence_record'
                """,
                (identity.scope_id, identity.principal_id),
            ).fetchall()
        attention = {
            str(row["source_object_id"]): (
                "following" if row["purpose_kind"] == "intelligence_follow" else "dismissed"
            )
            for row in attention_rows
        }
        content_kind = str(query.get("contentKind") or "").strip()
        values = [
            self._view(row, self._receipt(row["receipt"]), attention.get(str(row["id"]), "active"))
            for row in rows
        ]
        if content_kind:
            values = [item for item in values if item["contentKind"] == content_kind]
        include_dismissed = str(query.get("includeDismissed") or "").casefold() in {
            "1",
            "true",
            "yes",
        }
        if not include_dismissed:
            values = [item for item in values if item["userStatus"] != "dismissed"]
        total = len(values)
        start = (page - 1) * page_size
        return {
            "items": values[start : start + page_size],
            "candidateSamples": [],
            "total": total,
            "page": page,
            "pageSize": page_size,
        }

    def strategy_extract(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        """Derive the visible brand strategy card from the formal profile/facts.

        This is intentionally a read model over the frozen narrative, version,
        manifest and fact objects.  It does not revive the pre-blueprint strategy
        tables or create another authority for the same project knowledge.
        """

        with self.repository._connection() as connection:  # noqa: SLF001
            self.repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=project_id,
            )
            profile = connection.execute(
                """
                SELECT n.id, n.current_version, n.updated_at,
                       manifest.receipt, manifest.content_hash
                FROM narrative_outputs AS n
                JOIN artifact_versions AS version
                  ON version.scope_id=n.scope_id AND version.artifact_id=n.id
                 AND version.version=n.current_version
                LEFT JOIN object_manifests AS manifest
                  ON manifest.scope_id=version.scope_id
                 AND manifest.id=version.object_manifest_id
                 AND manifest.lifecycle_state='active'
                WHERE n.scope_id=? AND n.client_id=?
                  AND n.artifact_kind='strategic_profile'
                  AND n.lifecycle_state='active'
                ORDER BY n.updated_at DESC, n.id
                LIMIT 1
                """,
                (identity.scope_id, project_id),
            ).fetchone()
            if profile is None:
                return {"extract": None}
            clarification_rows = connection.execute(
                """
                SELECT fact.id, fact.updated_at, manifest.receipt
                FROM atomic_facts AS fact
                JOIN source_sets AS sources
                  ON sources.scope_id=fact.scope_id
                 AND sources.id=fact.source_set_id
                 AND sources.client_id=?
                 AND sources.purpose_kind='strategic_profile_clarification'
                 AND sources.lifecycle_state='active'
                JOIN object_manifests AS manifest
                  ON manifest.scope_id=fact.scope_id
                 AND manifest.id=fact.fact_object_manifest_id
                 AND manifest.lifecycle_state='active'
                WHERE fact.scope_id=? AND fact.verification_state='verified'
                  AND fact.lifecycle_state='active'
                ORDER BY fact.updated_at DESC, fact.id DESC
                """,
                (project_id, identity.scope_id),
            ).fetchall()

        receipt = self._receipt(profile["receipt"])
        dimensions = {
            str(item.get("dimension") or ""): dict(item)
            for item in receipt.get("dimensions") or []
            if isinstance(item, Mapping) and str(item.get("dimension") or "")
        }
        clarifications: dict[str, dict[str, Any]] = {}
        for row in clarification_rows:
            item = self._receipt(row["receipt"])
            dimension = str(item.get("dimension") or "")
            if dimension and dimension not in clarifications:
                clarifications[dimension] = {**item, "updatedAt": row["updated_at"]}

        def value_for(primary: str, fallback: str) -> tuple[str, dict[str, Any]]:
            correction = clarifications.get(primary)
            if correction and str(correction.get("statement") or "").strip():
                return str(correction["statement"]).strip(), correction
            item = dimensions.get(primary) or dimensions.get(fallback) or {}
            return str(item.get("narrative") or "").strip(), item

        objective, objective_source = value_for("next_steps", "essence")
        methodology, methodology_source = value_for("cooperation", "business_intro")
        if not objective and not methodology:
            return {"extract": None}

        def source_labels(item: Mapping[str, Any]) -> list[str]:
            labels: list[str] = []
            for reference in item.get("references") or []:
                if not isinstance(reference, Mapping):
                    continue
                label = str(
                    reference.get("sourceUrl")
                    or reference.get("label")
                    or reference.get("sourceId")
                    or ""
                ).strip()
                if label and label not in labels:
                    labels.append(label)
            return labels

        latest_clarification = next(iter(clarifications.values()), {})
        source_hash = str(profile["content_hash"] or "")
        if len(source_hash) != 64:
            source_hash = sha256_text(str(profile["receipt"] or ""))
        return {
            "extract": {
                "clientId": project_id,
                "strategicObjective": objective,
                "strategicObjectiveSources": source_labels(objective_source),
                "methodology": methodology,
                "methodologySources": source_labels(methodology_source),
                "stakeholders": [],
                "sourceStrategyMdHash": sha256_text(objective) if objective else source_hash,
                "sourceMethodologyMdHash": sha256_text(methodology) if methodology else source_hash,
                "llmModel": str(receipt.get("modelName") or "strict-strategic-profile"),
                "error": None,
                "extractedAt": str(receipt.get("generatedAt") or profile["updated_at"] or ""),
                "confirmedBy": str(latest_clarification.get("confirmedByMembershipId") or "") or None,
                "confirmedAt": str(latest_clarification.get("updatedAt") or "") or None,
                "isStale": False,
            }
        }

    def get_item(
        self,
        identity: SessionIdentity,
        *,
        intelligence_id: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            row = connection.execute(
                """
                SELECT i.*, manifest.receipt,
                       (SELECT task.id FROM tasks AS task
                        WHERE task.scope_id=i.scope_id
                          AND task.source_type='intelligence_record'
                          AND task.source_id=i.id
                          AND task.lifecycle_state!='deleted'
                        ORDER BY task.created_at DESC,task.id LIMIT 1)
                        AS converted_task_id
                FROM intelligence_records AS i
                JOIN source_sets AS capture_sources
                  ON capture_sources.scope_id=i.scope_id
                 AND capture_sources.id=i.source_set_id
                 AND capture_sources.purpose_kind IN (
                    'manual_intelligence_capture','public_opinion_capture'
                 )
                 AND capture_sources.lifecycle_state='active'
                LEFT JOIN object_manifests AS manifest
                  ON manifest.scope_id=i.scope_id
                 AND manifest.id=i.summary_object_manifest_id
                 AND manifest.lifecycle_state='active'
                WHERE i.id=? AND i.scope_id=? AND i.lifecycle_state='active'
                """,
                (intelligence_id, identity.scope_id),
            ).fetchone()
            if row is None or not row["client_id"]:
                raise RepositoryError(404, "intelligence_item_missing", "情报条目不存在")
            self.repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=str(row["client_id"]),
            )
            attention = connection.execute(
                """
                SELECT sources.purpose_kind
                FROM source_sets AS sources
                JOIN source_set_members AS member
                  ON member.scope_id=sources.scope_id
                 AND member.source_set_id=sources.id
                 AND member.lifecycle_state='active'
                WHERE sources.scope_id=? AND sources.created_by_principal_id=?
                  AND sources.purpose_kind IN ('intelligence_follow','intelligence_dismiss')
                  AND sources.lifecycle_state='active'
                  AND member.source_object_kind='intelligence_record'
                  AND member.source_object_id=?
                ORDER BY member.updated_at DESC LIMIT 1
                """,
                (identity.scope_id, identity.principal_id, intelligence_id),
            ).fetchone()
        status = (
            "following"
            if attention is not None and attention["purpose_kind"] == "intelligence_follow"
            else "dismissed"
            if attention is not None
            else "active"
        )
        return self._view(row, self._receipt(row["receipt"]), status)

    def list_refresh_runs(
        self,
        identity: SessionIdentity,
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        projects = GC07ProjectMaterialsRepository(self.repository).list_projects(identity)["projects"]
        visible_ids = {str(item["projectId"]) for item in projects}
        requested_scope = str(query.get("scopeId") or "").strip()
        if requested_scope:
            if requested_scope not in visible_ids:
                raise RepositoryError(404, "intelligence_project_missing", "当前成员无法访问该项目情报")
            visible_ids = {requested_scope}
        try:
            limit = max(1, min(100, int(query.get("limit") or 20)))
        except (TypeError, ValueError):
            raise RepositoryError(422, "intelligence_run_limit_invalid", "情报运行记录数量无效")
        if not visible_ids:
            return {"runs": []}
        content_kind = str(query.get("contentKind") or "").strip()
        active_only = str(query.get("activeOnly") or "").strip().lower() == "true"
        if active_only:
            return {"runs": []}
        placeholders = ",".join("?" for _ in visible_ids)
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                f"""
                SELECT command.id, command.aggregate_id AS client_id,
                       command.command_type, command.status,
                       command.submitted_at AS created_at,
                       command.settled_at AS finished_at,
                       result.receipt AS result_receipt
                FROM commands AS command
                LEFT JOIN idempotency_records AS idem
                  ON idem.scope_id=command.scope_id
                 AND idem.idempotency_key=command.idempotency_key
                 AND idem.status='completed'
                LEFT JOIN object_manifests AS result
                  ON result.scope_id=command.scope_id
                 AND result.id=idem.result_object_manifest_id
                 AND result.lifecycle_state='active'
                WHERE command.scope_id=? AND command.status='committed'
                  AND command.aggregate_type='client'
                  AND command.command_type IN (
                    'gc12.intelligence.captured','gc12.public_opinion.captured'
                  )
                  AND command.aggregate_id IN ({placeholders})
                ORDER BY command.submitted_at DESC, command.id DESC
                LIMIT ?
                """,
                (identity.scope_id, *sorted(visible_ids), limit),
            ).fetchall()
        values: list[dict[str, Any]] = []
        for row in rows:
            receipt = self._receipt(row["result_receipt"])
            rejection_counts = receipt.get("rejectionCounts")
            run_content_kind = str(
                receipt.get("contentKind")
                or (
                    "public_opinion"
                    if row["command_type"] == "gc12.public_opinion.captured"
                    else "timely_intelligence"
                )
            )
            if content_kind and run_content_kind != content_kind:
                continue
            status = "completed"
            result = {
                key: value
                for key, value in receipt.items()
                if key
                in {
                    "pageCount",
                    "changedCount",
                    "verifiedCount",
                    "candidateCount",
                    "errors",
                    "researchProgress",
                    "researchReceipt",
                }
            }
            values.append(
                {
                    "id": str(row["id"]),
                    "scopeType": "client",
                    "scopeId": str(row["client_id"]),
                    "clientId": str(row["client_id"]),
                    "projectModuleId": None,
                    "contentKind": run_content_kind,
                    "triggerSource": "manual_intelligence_capture",
                    "status": status,
                    "stage": "completed" if status == "completed" else status,
                    "message": str(
                        receipt.get("message")
                        or (
                            "手动情报抓取完成"
                        )
                    ),
                    "result": result,
                    "rejectionSummary": (
                        dict(rejection_counts)
                        if isinstance(rejection_counts, Mapping)
                        else {}
                    ),
                    "createdAt": str(row["created_at"]),
                    "updatedAt": str(row["finished_at"] or row["created_at"]),
                    "startedAt": row["created_at"],
                    "finishedAt": row["finished_at"],
                }
            )
        return {"runs": values}

    @staticmethod
    def _rule_action(row: Mapping[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(str(row["action_spec"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            return {}
        return dict(value) if isinstance(value, Mapping) else {}

    def list_focus_directives(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        prefix = f"{identity.principal_id}:intelligence-focus:"
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT * FROM automation_rules WHERE scope_id=? "
                "AND record_kind='automation' AND template_key LIKE ? "
                "AND lifecycle_state='active' ORDER BY updated_at,id",
                (identity.scope_id, f"{prefix}%"),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "scopeType": str(self._rule_action(row).get("scopeType") or "global"),
                "scopeId": self._rule_action(row).get("scopeId"),
                "profileCompletionFocus": list(
                    self._rule_action(row).get("profileCompletionFocus") or []
                ),
                "timelyIntelligenceFocus": list(
                    self._rule_action(row).get("timelyIntelligenceFocus") or []
                ),
                "exclude": list(self._rule_action(row).get("exclude") or []),
                "createdAt": str(row["created_at"]),
                "updatedAt": str(row["updated_at"]),
            }
            for row in rows
        ]

    def refresh_cycle_settings(self, identity: SessionIdentity) -> dict[str, Any]:
        template_key = f"{identity.principal_id}:intelligence-refresh-cycle"
        with self.repository._connection() as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT * FROM automation_rules WHERE scope_id=? AND template_key=? "
                "AND record_kind='automation' AND lifecycle_state='active'",
                (identity.scope_id, template_key),
            ).fetchone()
        action = self._rule_action(row) if row is not None else {}
        return {
            "profileCompletionHours": int(action.get("profileCompletionHours") or 72),
            "timelyIntelligenceHours": int(action.get("timelyIntelligenceHours") or 24),
            "state": "ready" if row is not None else "default",
            "message": (
                "已读取当前成员的情报刷新周期"
                if row is not None
                else "当前使用产品默认刷新周期"
            ),
        }

    def list_verification_rules(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        prefix = f"{identity.principal_id}:intelligence-verification:"
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT * FROM automation_rules WHERE scope_id=? "
                "AND record_kind='automation' AND template_key LIKE ? "
                "AND lifecycle_state='active' ORDER BY updated_at,id",
                (identity.scope_id, f"{prefix}%"),
            ).fetchall()
        result = []
        for row in rows:
            action = self._rule_action(row)
            result.append(
                {
                    "id": str(row["id"]),
                    "scopeType": str(action.get("scopeType") or "global"),
                    "scopeId": action.get("scopeId"),
                    "positiveRules": list(action.get("positiveRules") or []),
                    "excludeRules": list(action.get("excludeRules") or []),
                    "identityAnchors": list(action.get("identityAnchors") or []),
                    "clarificationExamples": list(
                        action.get("clarificationExamples") or []
                    ),
                    "createdAt": str(row["created_at"]),
                    "updatedAt": str(row["updated_at"]),
                }
            )
        return result

    def upsert_rule(
        self,
        identity: SessionIdentity,
        *,
        rule_kind: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if rule_kind not in {"focus", "cycle", "verification"}:
            raise RepositoryError(422, "intelligence_rule_kind_invalid", "情报规则类型无效")
        if rule_kind == "cycle":
            action = {
                "profileCompletionHours": max(
                    1, min(8760, int(payload.get("profileCompletionHours") or 72))
                ),
                "timelyIntelligenceHours": max(
                    1, min(8760, int(payload.get("timelyIntelligenceHours") or 24))
                ),
            }
            scope_type = "member"
            scope_id = identity.principal_id
            template_key = f"{identity.principal_id}:intelligence-refresh-cycle"
        else:
            scope_type = str(payload.get("scopeType") or "global").strip()
            scope_id = str(payload.get("scopeId") or "").strip() or None
            if scope_type not in {"global", "client"}:
                raise RepositoryError(422, "intelligence_rule_scope_invalid", "情报规则作用域无效")
            if scope_type == "client":
                if not scope_id:
                    raise RepositoryError(422, "intelligence_rule_scope_required", "请选择规则所属项目")
                with self.repository._connection() as connection:  # noqa: SLF001
                    self.repository._require_project_access(  # noqa: SLF001
                        connection, identity, project_id=scope_id
                    )
            suffix = scope_id or "global"
            template_key = f"{identity.principal_id}:intelligence-{rule_kind}:{scope_type}:{suffix}"
            allowed = (
                ("profileCompletionFocus", "timelyIntelligenceFocus", "exclude")
                if rule_kind == "focus"
                else ("positiveRules", "excludeRules", "identityAnchors", "clarificationExamples")
            )
            action = {"scopeType": scope_type, "scopeId": scope_id}
            for key in allowed:
                action[key] = [
                    str(value).strip()
                    for value in payload.get(key) or []
                    if str(value or "").strip()
                ]
        normalized = {"ruleKind": rule_kind, "templateKey": template_key, "action": action}
        payload_hash = payload_fingerprint(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = GC07ProjectMaterialsRepository._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                existing = connection.execute(
                    "SELECT * FROM automation_rules WHERE scope_id=? AND template_key=? "
                    "AND record_kind='automation' AND lifecycle_state='active'",
                    (identity.scope_id, template_key),
                ).fetchone()
                now = utc_now()
                rule_id = (
                    str(existing["id"])
                    if existing is not None
                    else self.repository._record_id(
                        "automation-rule", identity.principal_id, template_key
                    )
                )
                next_version = int(existing["version"] or 0) + 1 if existing else 1
                action_spec = canonical_json({"schema": "yiyu.intelligence-rule-action.v1", **action})
                trigger_spec = canonical_json(
                    {
                        "schema": "yiyu.intelligence-rule-trigger.v1",
                        "ownerPrincipalId": identity.principal_id,
                        "ruleKind": rule_kind,
                        "scopeType": scope_type,
                        "scopeId": scope_id,
                    }
                )
                connection.execute(
                    "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,"
                    "resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,'automation_rule','active',?,'intelligence_rule',?,?,NULL,'cloud',?) "
                    "ON CONFLICT(id) DO UPDATE SET version=excluded.version,updated_at=excluded.updated_at,"
                    "lifecycle_state='active',deleted_at=NULL",
                    (rule_id, identity.scope_id, next_version, now, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "INSERT INTO automation_rules (id,scope_id,template_key,rule_version,trigger_spec,"
                    "record_kind,trigger_spec_schema_version,action_spec_schema_version,action_spec,"
                    "trusted_source_pattern,enabled,effective_at,version,lifecycle_state,created_at,updated_at,deleted_at) "
                    "VALUES (?,?,?,1,?,'automation','yiyu.intelligence-rule-trigger.v1',"
                    "'yiyu.intelligence-rule-action.v1',?,NULL,1,?,?,'active',?,?,NULL) "
                    "ON CONFLICT(id) DO UPDATE SET trigger_spec=excluded.trigger_spec,"
                    "action_spec=excluded.action_spec,enabled=1,effective_at=excluded.effective_at,"
                    "version=excluded.version,lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL",
                    (rule_id, identity.scope_id, template_key, trigger_spec, action_spec, now, next_version, now, now),
                )
                if rule_kind == "cycle":
                    result = {**action, "state": "ready", "message": "情报刷新周期已保存"}
                else:
                    result = {
                        "id": rule_id,
                        **action,
                        "createdAt": str(existing["created_at"] if existing else now),
                        "updatedAt": now,
                    }
                GC07ProjectMaterialsRepository._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type=f"gc12.intelligence.{rule_kind}_rule_saved",
                    aggregate_type="automation_rule",
                    aggregate_id=rule_id,
                    aggregate_version=next_version,
                    expected_aggregate_version=(int(existing["version"]) if existing else None),
                    result=result,
                    target_resource_id=rule_id,
                )
                connection.commit()
                return result
            except (TypeError, ValueError) as exc:
                connection.rollback()
                raise RepositoryError(422, "intelligence_rule_value_invalid", "情报规则参数无效") from exc
            except Exception:
                connection.rollback()
                raise

    def record_item_answer(
        self,
        identity: SessionIdentity,
        *,
        intelligence_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = {
            "intelligenceId": intelligence_id,
            "questionHash": str(payload.get("questionHash") or "").strip(),
            "answerHash": str(payload.get("answerHash") or "").strip(),
            "providerResourceId": str(payload.get("providerResourceId") or "").strip(),
            "modelName": str(payload.get("modelName") or "").strip(),
            "threadId": str(payload.get("threadId") or f"intelligence:{intelligence_id}").strip(),
            "originInstanceId": str(payload.get("originInstanceId") or identity.cloud_instance_id).strip(),
        }
        for key in ("questionHash", "answerHash"):
            value = normalized[key]
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise RepositoryError(422, "intelligence_answer_hash_invalid", "情报回答哈希无效")
        if not normalized["providerResourceId"] or not normalized["modelName"]:
            raise RepositoryError(422, "intelligence_answer_provider_required", "情报回答缺少模型回执")
        payload_hash = payload_fingerprint(normalized)
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = GC07ProjectMaterialsRepository._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                item = connection.execute(
                    "SELECT * FROM intelligence_records WHERE id=? AND scope_id=? "
                    "AND lifecycle_state='active'",
                    (intelligence_id, identity.scope_id),
                ).fetchone()
                if item is None or not item["client_id"]:
                    raise RepositoryError(404, "intelligence_item_missing", "情报条目不存在")
                project_id = str(item["client_id"])
                self.repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=project_id
                )
                bot_id = builtin_agent_id(identity.organization_id, "intelligence_research")
                bot = connection.execute(
                    "SELECT bot.id FROM bot_definitions AS bot "
                    "JOIN authorization_scopes AS scope ON scope.id=bot.scope_id "
                    "WHERE bot.id=? AND bot.agent_kind='intelligence_research' "
                    "AND bot.enabled=1 AND bot.lifecycle_state='active' "
                    "AND scope.organization_id=? AND scope.status='active'",
                    (bot_id, identity.organization_id),
                ).fetchone()
                provider = connection.execute(
                    "SELECT id,model_name,status FROM provider_resources WHERE id=? "
                    "AND scope_id=? AND resource_kind='organization_ai_configuration' "
                    "AND lifecycle_state='active'",
                    (normalized["providerResourceId"], identity.scope_id),
                ).fetchone()
                if bot is None or provider is None or str(provider["status"] or "") != "ready":
                    raise RepositoryError(409, "intelligence_answer_runtime_not_ready", "情报问答或组织模型尚未就绪")
                if str(provider["model_name"] or "") != normalized["modelName"]:
                    raise RepositoryError(409, "intelligence_answer_model_mismatch", "情报回答模型回执不一致")
                now = utc_now()
                answer_id = self.repository._record_id("answer", idempotency_key, intelligence_id)  # noqa: SLF001
                source_set_id = self.repository._record_id("source-set", answer_id, "intelligence")  # noqa: SLF001
                source_member_id = self.repository._record_id("source-member", source_set_id, intelligence_id)  # noqa: SLF001
                lineage_id = self.repository._record_id("lineage", answer_id, "intelligence")  # noqa: SLF001
                context_id = self.repository._record_id("context", answer_id, "intelligence")  # noqa: SLF001
                context_manifest_id = self.repository._record_id("manifest", context_id, "safe-context")  # noqa: SLF001
                context_receipt = canonical_json(
                    {
                        "schema": "yiyu.intelligence-answer-context.v1",
                        "clientId": project_id,
                        "intelligenceId": intelligence_id,
                        "intelligenceVersion": int(item["version"] or 1),
                        "questionHash": normalized["questionHash"],
                        "answerHash": normalized["answerHash"],
                        "sourceCount": 1,
                        "contentBoundary": "answer_body_local_only",
                    }
                )
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,lifecycle_state,"
                    "receipt,holder_role,holder_instance_id,storage_kind,byte_size,media_type,"
                    "availability_state,receipt_hash,created_at,verified_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,NULL,?,'active',?,'cloud_metadata_receipt',?,'metadata_receipt',?,"
                    "'application/vnd.yiyu.intelligence-answer-context+json','ready',?,?,?,NULL,'cloud',?)",
                    (
                        context_manifest_id,
                        identity.scope_id,
                        sha256_text(context_receipt),
                        context_receipt,
                        identity.cloud_instance_id,
                        len(context_receipt.encode("utf-8")),
                        sha256_text(context_receipt),
                        now,
                        now,
                        identity.cloud_instance_id,
                    ),
                )
                connection.execute(
                    "INSERT INTO source_sets (id,scope_id,client_id,security_label_set_version,source_count,"
                    "version,purpose_kind,publication_state,created_by_principal_id,created_at,expires_at,"
                    "lifecycle_state,updated_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,'organization-v1',1,1,'intelligence_answer_context','draft',?,?,NULL,"
                    "'active',?,NULL,'cloud',?)",
                    (source_set_id, identity.scope_id, project_id, identity.principal_id, now, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "INSERT INTO source_set_members (id,scope_id,source_set_id,source_object_id,source_version,"
                    "policy_version,source_object_kind,ordinal,added_at,removed_at,version,lifecycle_state,"
                    "created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,?,?,1,'intelligence_record',0,?,NULL,1,'active',?,?,NULL,'cloud',?)",
                    (source_member_id, identity.scope_id, source_set_id, intelligence_id, int(item["version"] or 1), now, now, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "INSERT INTO derivation_lineage (id,scope_id,source_set_id,policy_version_id,grant_generation,"
                    "derivative_kind,derivative_object_id,generator_version,generated_at,invalidated_at,"
                    "source_version,authority_role,origin_instance_id) VALUES (?,?,?,NULL,1,'ai_context_manifest',?,"
                    "'gc12-intelligence-answer-v1',?,NULL,1,'cloud',?)",
                    (lineage_id, identity.scope_id, source_set_id, context_id, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "INSERT INTO ai_context_manifests (id,scope_id,lineage_id,provider_resource_id,policy_version,"
                    "status,source_set_id,question_hash,retrieval_policy_version,selected_source_count,"
                    "context_object_manifest_id,generated_at,invalidated_at,source_version,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,?,1,'ready',?,?,'gc12-intelligence-answer-v1',1,?,?,NULL,1,'cloud',?)",
                    (context_id, identity.scope_id, lineage_id, normalized["providerResourceId"], source_set_id, normalized["questionHash"], context_manifest_id, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "INSERT INTO ai_answers (id,scope_id,client_id,bot_id,source_set_id,status,created_at,thread_id,"
                    "ai_context_manifest_id,provider_resource_id,model_name,answer_object_manifest_id,answer_hash,"
                    "source_count,material_access_mode,boundary_state,version,lifecycle_state,updated_at,deleted_at) "
                    "VALUES (?,?,?,?,?,'ready',?,?,?,?,?,NULL,?,1,'organization_knowledge_only','grounded',1,'active',?,NULL)",
                    (answer_id, identity.scope_id, project_id, bot_id, source_set_id, now, normalized["threadId"], context_id, normalized["providerResourceId"], normalized["modelName"], normalized["answerHash"], now),
                )
                result = {"answerId": answer_id, "threadId": normalized["threadId"], "sourceCount": 1, "boundaryState": "grounded"}
                GC07ProjectMaterialsRepository._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="gc12.intelligence.answer_recorded",
                    aggregate_type="ai_answer",
                    aggregate_id=answer_id,
                    aggregate_version=1,
                    expected_aggregate_version=None,
                    result=result,
                    target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _record_research_run(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
        content_kind: str,
        project_version: int,
        now: str,
    ) -> dict[str, Any]:
        bot_id = builtin_agent_id(identity.organization_id, "intelligence_research")
        bot = connection.execute(
            "SELECT bot.id FROM bot_definitions AS bot "
            "JOIN authorization_scopes AS scope ON scope.id=bot.scope_id "
            "WHERE bot.id=? AND bot.agent_kind='intelligence_research' "
            "AND bot.enabled=1 AND bot.lifecycle_state='active' "
            "AND scope.organization_id=? AND scope.status='active'",
            (bot_id, identity.organization_id),
        ).fetchone()
        if bot is None:
            raise RepositoryError(503, "intelligence_research_unavailable", "公开情报研究能力尚未就绪")
        command = connection.execute(
            "SELECT operation_id FROM commands WHERE scope_id=? AND idempotency_key=?",
            (identity.scope_id, idempotency_key),
        ).fetchone()
        receipt = connection.execute(
            "SELECT result_object_manifest_id FROM idempotency_records "
            "WHERE scope_id=? AND idempotency_key=? AND status='completed'",
            (identity.scope_id, idempotency_key),
        ).fetchone()
        if command is None or receipt is None or not receipt["result_object_manifest_id"]:
            raise RepositoryError(500, "intelligence_research_receipt_missing", "情报运行回执未形成")
        operation_id = str(command["operation_id"])
        run_id = self.repository._record_id("run", operation_id, bot_id)  # noqa: SLF001
        run_kind = (
            "public_opinion_research"
            if content_kind == "public_opinion"
            else "timely_intelligence_research"
        )
        connection.execute(
            """
            INSERT INTO execution_runs (
                id,scope_id,bot_id,rule_id,task_id,operation_id,status,
                initiator_membership_id,proposal_id,run_kind,
                progress_object_manifest_id,result_object_manifest_id,
                started_at,finished_at,version,lifecycle_state,
                created_at,updated_at,deleted_at
            ) VALUES (?,?,?,NULL,NULL,?,'completed',?,NULL,?,NULL,?,?,?,1,'active',?,?,NULL)
            ON CONFLICT(id) DO UPDATE SET status='completed',
                result_object_manifest_id=excluded.result_object_manifest_id,
                finished_at=excluded.finished_at,version=execution_runs.version+1,
                lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL
            """,
            (
                run_id,
                identity.scope_id,
                bot_id,
                operation_id,
                identity.membership_id,
                run_kind,
                str(receipt["result_object_manifest_id"]),
                now,
                now,
                now,
                now,
            ),
        )
        return AgentRunReceipt(
            agent_kind="intelligence_research",
            run_id=run_id,
            state="completed",
            stage="public_evidence_ready",
            message="公开网页检索、证据筛选与情报梳理已完成",
            result_version=project_version,
        ).as_dict()

    def commit_external_capture(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        capture_id: str,
        content_kind: str,
        capture_kind: str,
        items: list[Mapping[str, Any]],
        research_receipt: Mapping[str, Any] | None = None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if len(items) > 20:
            raise RepositoryError(422, "external_capture_items_invalid", "公开采集结果数量无效")
        if content_kind not in {"brand_mirror", "timely_intelligence", "public_opinion"}:
            raise RepositoryError(422, "intelligence_content_kind_invalid", "情报内容类型无效")
        if capture_kind != "manual_intelligence":
            raise RepositoryError(422, "intelligence_capture_kind_invalid", "情报抓取必须由用户明确触发")
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(items):
            title = str(raw.get("title") or "").strip()[:240]
            summary = str(raw.get("summary") or "").strip()[:4_000]
            source_url = str(raw.get("sourceUrl") or "").strip()[:2_048]
            if not title or not summary or not source_url.startswith(("https://", "http://")):
                raise RepositoryError(422, "external_capture_item_invalid", f"第{index + 1}条公开线索无效")
            sentiment = str(raw.get("sentiment") or "neutral").lower()
            if sentiment not in {"negative", "neutral", "positive"}:
                sentiment = "neutral"
            normalized.append(
                {
                    "clientItemKey": str(raw.get("clientItemKey") or f"item:{index}")[:160],
                    "title": title,
                    "summary": summary,
                    "sourceUrl": source_url,
                    "sourceName": str(raw.get("sourceName") or "公开来源")[:160],
                    "capturedAt": str(raw.get("capturedAt") or utc_now()),
                    "contentHash": str(raw.get("contentHash") or sha256_text(f"{title}\n{summary}\n{source_url}")),
                    "sentiment": sentiment,
                    "sentimentReason": str(raw.get("sentimentReason") or "")[:500],
                    "publishedAt": str(raw.get("publishedAt") or "")[:64] or None,
                    "relevanceReason": str(raw.get("relevanceReason") or "")[:800],
                    "impact": str(raw.get("impact") or "")[:1_200],
                    "tags": [
                        str(value).strip()[:80]
                        for value in raw.get("tags") or []
                        if str(value or "").strip()
                    ][:8],
                    "directProjectMention": bool(raw.get("directProjectMention")),
                    "bodyFetched": bool(raw.get("bodyFetched")),
                }
            )
        research = {
            "planningMode": str((research_receipt or {}).get("planningMode") or "unknown")[:80],
            "queryCount": _bounded_int((research_receipt or {}).get("queryCount"), minimum=0, maximum=24),
            "coverageTarget": _bounded_int((research_receipt or {}).get("coverageTarget"), minimum=0, maximum=50),
            "directMentionPolicy": str((research_receipt or {}).get("directMentionPolicy") or "")[:40],
            "rejectionCounts": dict((research_receipt or {}).get("rejectionCounts") or {}),
            "bodyFetchedCount": _bounded_int((research_receipt or {}).get("bodyFetchedCount"), minimum=0, maximum=100),
            "modelAnalysisExecuted": bool((research_receipt or {}).get("modelAnalysisExecuted")),
        }
        payload_hash = payload_fingerprint(
            {
                "projectId": project_id,
                "captureId": capture_id,
                "contentKind": content_kind,
                "captureKind": capture_kind,
                "items": normalized,
                "researchReceipt": research,
            }
        )
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                project = self.repository._require_project_access(  # noqa: SLF001
                    connection, identity, project_id=project_id, capability="knowledge_write"
                )
                replay = GC07ProjectMaterialsRepository._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                now = utc_now()
                if not normalized:
                    result = {
                        "captureId": capture_id,
                        "contentKind": content_kind,
                        "captureKind": capture_kind,
                        "fetchedCount": 0,
                        "insertedCount": 0,
                        "duplicateCount": 0,
                        "candidateCount": 0,
                        "items": [],
                        "externalCollectionExecuted": True,
                        "sourceBodyStored": False,
                        "researchReceipt": research,
                        "message": "手动情报抓取完成，未发现可进入判断流程的公开资料",
                    }
                    GC07ProjectMaterialsRepository._record_command(
                        connection,
                        identity,
                        idempotency_key=idempotency_key,
                        payload_hash=payload_hash,
                        command_type="gc12.intelligence.captured",
                        aggregate_type="client",
                        aggregate_id=project_id,
                        aggregate_version=int(project["version"] or 1),
                        expected_aggregate_version=int(project["version"] or 1),
                        result=result,
                        target_resource_id=project_id,
                    )
                    result["agentRun"] = self._record_research_run(
                        connection,
                        identity,
                        idempotency_key=idempotency_key,
                        content_kind=content_kind,
                        project_version=int(project["version"] or 1),
                        now=now,
                    )
                    connection.commit()
                    return result
                source_set_id = self.repository._record_id("source-set", project_id, capture_id)  # noqa: SLF001
                source_set_kind = (
                    "public_opinion_capture"
                    if content_kind == "public_opinion"
                    else "manual_intelligence_capture"
                )
                connection.execute(
                    "INSERT INTO source_sets (id,scope_id,client_id,security_label_set_version,source_count,"
                    "version,purpose_kind,publication_state,created_by_principal_id,created_at,expires_at,"
                    "lifecycle_state,updated_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,'organization-public-v1',?,1,?,'published',?,?,NULL,"
                    "'active',?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET source_count=excluded.source_count,"
                    "version=source_sets.version+1,lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL",
                    (source_set_id, identity.scope_id, project_id, len(normalized), source_set_kind, identity.principal_id, now, now, identity.cloud_instance_id),
                )
                results: list[dict[str, Any]] = []
                inserted = 0
                updated = 0
                for ordinal, item in enumerate(normalized):
                    source_id = self.repository._record_id("source-asset", project_id, item["sourceUrl"].lower())  # noqa: SLF001
                    intelligence_id = self.repository._record_id(
                        "intelligence",
                        project_id,
                        f"{content_kind}|{item['sourceUrl'].lower()}",
                    )  # noqa: SLF001
                    receipt = canonical_json(
                        {
                            "schema": "yiyu.public-intelligence-capture.v1",
                            "sourceType": "public_search_summary",
                            "sourceTitle": item["sourceName"],
                            "sourceUrl": item["sourceUrl"],
                            "title": item["title"],
                            "summary": item["summary"],
                            "statement": item["summary"],
                            "contentKind": content_kind,
                            "sentimentLabel": item["sentiment"],
                            "sentimentReason": item["sentimentReason"],
                            "publishedAt": item["publishedAt"],
                            "relevanceReason": item["relevanceReason"],
                            "impact": item["impact"],
                            "tags": item["tags"],
                            "directProjectMention": item["directProjectMention"],
                            "sourceBodyRead": item["bodyFetched"],
                            "capturedAt": item["capturedAt"],
                            "sourceBodyStored": False,
                        }
                    )
                    receipt_hash = sha256_text(receipt)
                    existing = connection.execute(
                        "SELECT intelligence.version,manifest.id AS manifest_id,manifest.content_hash,"
                        "manifest.receipt_hash FROM intelligence_records AS intelligence "
                        "LEFT JOIN object_manifests AS manifest ON manifest.scope_id=intelligence.scope_id "
                        "AND manifest.id=intelligence.summary_object_manifest_id "
                        "WHERE intelligence.id=? AND intelligence.scope_id=? AND intelligence.lifecycle_state='active'",
                        (intelligence_id, identity.scope_id),
                    ).fetchone()
                    if existing is not None and str(existing["content_hash"] or "") == item["contentHash"]:
                        if (
                            existing["manifest_id"]
                            and str(existing["receipt_hash"] or "") != receipt_hash
                        ):
                            connection.execute(
                                "UPDATE object_manifests SET receipt=?,receipt_hash=?,verified_at=? "
                                "WHERE id=? AND scope_id=?",
                                (
                                    receipt,
                                    receipt_hash,
                                    now,
                                    str(existing["manifest_id"]),
                                    identity.scope_id,
                                ),
                            )
                            connection.execute(
                                "UPDATE intelligence_records SET version=version+1,updated_at=? "
                                "WHERE id=? AND scope_id=?",
                                (now, intelligence_id, identity.scope_id),
                            )
                            updated += 1
                            status = "updated_metadata"
                        else:
                            status = "duplicate"
                        results.append({"clientItemKey": item["clientItemKey"], "status": status, "intelligenceId": intelligence_id})
                        continue
                    manifest_id = self.repository._record_id("manifest", source_id, item["contentHash"])  # noqa: SLF001
                    for resource_id, resource_kind, type_key in (
                        (source_id, "source_asset", "public_search_summary"),
                        (intelligence_id, "intelligence_record", source_set_kind),
                    ):
                        connection.execute(
                            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,"
                            "resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                            "VALUES (?,?,?,'active',1,?,?,?,NULL,'cloud',?) ON CONFLICT(id) DO UPDATE SET "
                            "lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL",
                            (resource_id, identity.scope_id, resource_kind, type_key, now, now, identity.cloud_instance_id),
                        )
                    connection.execute(
                        "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,lifecycle_state,receipt,"
                        "holder_role,holder_instance_id,storage_kind,byte_size,media_type,availability_state,receipt_hash,"
                        "created_at,verified_at,deleted_at,authority_role,origin_instance_id) VALUES (?,?,NULL,?,'active',?,"
                        "'organization_cloud',?,'metadata_receipt',?,'application/json','ready',?,?,?,NULL,'cloud',?) "
                        "ON CONFLICT(id) DO UPDATE SET receipt=excluded.receipt,receipt_hash=excluded.receipt_hash,"
                        "verified_at=excluded.verified_at,lifecycle_state='active',deleted_at=NULL",
                        (manifest_id, identity.scope_id, item["contentHash"], receipt, identity.cloud_instance_id, len(receipt.encode("utf-8")), receipt_hash, now, now, identity.cloud_instance_id),
                    )
                    connection.execute(
                        "INSERT INTO source_assets (id,scope_id,client_id,object_manifest_id,content_hash,record_kind,"
                        "source_kind,display_name,media_type,byte_size,source_locator_nonlocal,parent_folder_id,asset_id,"
                        "folder_id,created_by_membership_id,availability_state,archived_at,version,lifecycle_state,created_at,"
                        "updated_at,deleted_at,authority_role,origin_instance_id) VALUES (?,?,?,?,?,'asset','public_web',?,"
                        "'application/json',?,?,NULL,NULL,NULL,?,'ready',NULL,1,'active',?,?,NULL,'cloud',?) "
                        "ON CONFLICT(id) DO UPDATE SET object_manifest_id=excluded.object_manifest_id,content_hash=excluded.content_hash,"
                        "display_name=excluded.display_name,byte_size=excluded.byte_size,source_locator_nonlocal=excluded.source_locator_nonlocal,"
                        "availability_state='ready',version=source_assets.version+1,lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL",
                        (source_id, identity.scope_id, project_id, manifest_id, item["contentHash"], item["title"], len(receipt.encode("utf-8")), item["sourceUrl"], identity.membership_id, now, now, identity.cloud_instance_id),
                    )
                    member_id = self.repository._record_id("source-member", source_set_id, source_id)  # noqa: SLF001
                    connection.execute(
                        "INSERT INTO source_set_members (id,scope_id,source_set_id,source_object_id,source_version,policy_version,"
                        "source_object_kind,ordinal,added_at,removed_at,version,lifecycle_state,created_at,updated_at,deleted_at,"
                        "authority_role,origin_instance_id) VALUES (?,?,?,?,1,1,'source_asset',?,?,NULL,1,'active',?,?,NULL,'cloud',?) "
                        "ON CONFLICT(id) DO UPDATE SET ordinal=excluded.ordinal,removed_at=NULL,lifecycle_state='active',"
                        "updated_at=excluded.updated_at,deleted_at=NULL",
                        (member_id, identity.scope_id, source_set_id, source_id, ordinal, now, now, now, identity.cloud_instance_id),
                    )
                    connection.execute(
                        "INSERT INTO intelligence_records (id,scope_id,client_id,event_line_id,verification_state,version,"
                        "source_set_id,title,summary_object_manifest_id,trust_rule_id,confirmed_by_membership_id,confirmed_at,"
                        "published_document_id,lifecycle_state,created_at,updated_at,deleted_at) VALUES (?,?,?,NULL,'candidate',"
                        "1,?,?,?,NULL,NULL,NULL,NULL,'active',?,?,NULL) "
                        "ON CONFLICT(id) DO UPDATE SET source_set_id=excluded.source_set_id,title=excluded.title,"
                        "summary_object_manifest_id=excluded.summary_object_manifest_id,version=intelligence_records.version+1,"
                        "lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL",
                        (intelligence_id, identity.scope_id, project_id, source_set_id, item["title"], manifest_id, now, now),
                    )
                    status = "updated" if existing is not None else "inserted"
                    if existing is not None:
                        updated += 1
                    else:
                        inserted += 1
                    results.append({"clientItemKey": item["clientItemKey"], "status": status, "intelligenceId": intelligence_id})
                connection.execute(
                    "UPDATE source_sets SET source_count=?,updated_at=? WHERE id=? AND scope_id=?",
                    (inserted + updated, now, source_set_id, identity.scope_id),
                )
                result = {
                    "captureId": capture_id,
                    "contentKind": content_kind,
                    "captureKind": capture_kind,
                    "fetchedCount": len(normalized),
                    "insertedCount": inserted,
                    "updatedCount": updated,
                    "duplicateCount": len(normalized) - inserted - updated,
                    "items": results,
                    "externalCollectionExecuted": True,
                    "sourceBodyStored": False,
                    "researchReceipt": research,
                    "message": "手动情报抓取完成",
                }
                GC07ProjectMaterialsRepository._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="gc12.intelligence.captured",
                    aggregate_type="client",
                    aggregate_id=project_id,
                    aggregate_version=int(project["version"] or 1),
                    expected_aggregate_version=int(project["version"] or 1),
                    result=result,
                    target_resource_id=project_id,
                )
                result["agentRun"] = self._record_research_run(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    content_kind=content_kind,
                    project_version=int(project["version"] or 1),
                    now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def set_attention(
        self,
        identity: SessionIdentity,
        *,
        intelligence_id: str,
        action: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if action not in {"follow", "dismiss", "restore"}:
            raise RepositoryError(422, "intelligence_attention_invalid", "情报关注动作无效")
        payload_hash = payload_fingerprint(
            {"intelligenceId": intelligence_id, "action": action, "payload": dict(payload)}
        )
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = GC07ProjectMaterialsRepository._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                row = connection.execute(
                    "SELECT * FROM intelligence_records WHERE id=? AND scope_id=? "
                    "AND lifecycle_state='active'",
                    (intelligence_id, identity.scope_id),
                ).fetchone()
                if row is None or not row["client_id"]:
                    raise RepositoryError(404, "intelligence_item_missing", "情报条目不存在")
                project_id = str(row["client_id"])
                project = self.repository._require_project_access(  # noqa: SLF001
                    connection,
                    identity,
                    project_id=project_id,
                )
                purpose = f"intelligence_{action}" if action != "restore" else ""
                opposite = "intelligence_dismiss" if action == "follow" else "intelligence_follow"
                set_id = self.repository._record_id(  # noqa: SLF001
                    "source-set",
                    f"{identity.principal_id}|{project_id}|{purpose}",
                    purpose,
                )
                member_id = self.repository._record_id(  # noqa: SLF001
                    "source-set-member",
                    f"{set_id}|{intelligence_id}",
                    intelligence_id,
                )
                now = utc_now()
                connection.execute(
                    """
                    UPDATE source_set_members SET lifecycle_state='archived',
                        removed_at=?, updated_at=?, version=version+1
                    WHERE scope_id=? AND source_object_id=?
                      AND source_object_kind='intelligence_record'
                      AND source_set_id IN (
                        SELECT id FROM source_sets WHERE scope_id=?
                          AND created_by_principal_id=?
                          AND purpose_kind IN (?,?)
                      ) AND lifecycle_state='active'
                    """,
                    (
                        now,
                        now,
                        identity.scope_id,
                        intelligence_id,
                        identity.scope_id,
                        identity.principal_id,
                        "intelligence_follow" if action == "restore" else opposite,
                        "intelligence_dismiss" if action == "restore" else opposite,
                    ),
                )
                if action != "restore":
                    connection.execute(
                    """
                    INSERT INTO source_sets (
                        id,scope_id,client_id,security_label_set_version,source_count,
                        version,purpose_kind,publication_state,created_by_principal_id,
                        created_at,expires_at,lifecycle_state,updated_at,deleted_at,
                        authority_role,origin_instance_id
                    ) VALUES (?,?,?,'member-private-v1',1,1,?,'published',?,?,NULL,
                              'active',?,NULL,'cloud',?)
                    ON CONFLICT(id) DO UPDATE SET source_count=1,
                        version=source_sets.version+1,lifecycle_state='active',
                        updated_at=excluded.updated_at,deleted_at=NULL
                    """,
                    (set_id, identity.scope_id, project_id, purpose, identity.principal_id, now, now, self.repository.cloud_instance_id),
                    )
                    connection.execute(
                    """
                    INSERT INTO source_set_members (
                        id,scope_id,source_set_id,source_object_id,source_version,
                        policy_version,source_object_kind,ordinal,added_at,removed_at,
                        version,lifecycle_state,created_at,updated_at,deleted_at,
                        authority_role,origin_instance_id
                    ) VALUES (?,?,?,?,?,1,'intelligence_record',1,?,NULL,1,
                              'active',?,?,NULL,'cloud',?)
                    ON CONFLICT(id) DO UPDATE SET source_version=excluded.source_version,
                        removed_at=NULL,version=source_set_members.version+1,
                        lifecycle_state='active',updated_at=excluded.updated_at,
                        deleted_at=NULL
                    """,
                    (member_id, identity.scope_id, set_id, intelligence_id, int(row["version"] or 1), now, now, now, self.repository.cloud_instance_id),
                    )
                connection.execute(
                    """
                    UPDATE source_sets
                    SET source_count=(
                            SELECT COUNT(*) FROM source_set_members AS members
                            WHERE members.scope_id=source_sets.scope_id
                              AND members.source_set_id=source_sets.id
                              AND members.lifecycle_state='active'
                        ),
                        updated_at=?
                    WHERE scope_id=? AND client_id=?
                      AND created_by_principal_id=?
                      AND purpose_kind IN (?,?)
                    """,
                    (
                        now,
                        identity.scope_id,
                        project_id,
                        identity.principal_id,
                        "intelligence_follow" if action == "restore" else purpose,
                        "intelligence_dismiss" if action == "restore" else opposite,
                    ),
                )
                receipt = self._receipt(
                    connection.execute(
                        "SELECT receipt FROM object_manifests WHERE id=? AND scope_id=?",
                        (row["summary_object_manifest_id"], identity.scope_id),
                    ).fetchone()["receipt"]
                    if row["summary_object_manifest_id"]
                    else None
                )
                result = self._view(
                    row,
                    receipt,
                    "active" if action == "restore" else ("following" if action == "follow" else "dismissed"),
                )
                GC07ProjectMaterialsRepository._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type=f"gc12.intelligence.{action}",
                    aggregate_type="intelligence_record",
                    aggregate_id=intelligence_id,
                    aggregate_version=int(row["version"] or 1),
                    expected_aggregate_version=int(row["version"] or 1),
                    result=result,
                    target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
