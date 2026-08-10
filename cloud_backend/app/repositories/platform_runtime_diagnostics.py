"""Read-only runtime diagnostics over the strict 88-table authority."""

from __future__ import annotations

import json
from typing import Any, Mapping

from strict_common.ids import utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity


class PlatformRuntimeDiagnosticsRepository:
    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _json(value: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    def active_background_tasks(self, identity: SessionIdentity) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT c.operation_id,c.command_type,c.aggregate_type,c.aggregate_id,"
                "c.submitted_at,a.transport_state,a.started_at FROM commands c "
                "LEFT JOIN operation_attempts a ON a.scope_id=c.scope_id "
                "AND a.command_id=c.id AND a.attempt_no=(SELECT MAX(x.attempt_no) "
                "FROM operation_attempts x WHERE x.scope_id=c.scope_id AND x.command_id=c.id) "
                "WHERE c.scope_id=? AND c.status='sending' "
                "ORDER BY c.submitted_at DESC LIMIT 100",
                (identity.scope_id,),
            ).fetchall()
        tasks = [
            {
                "operationId": str(row["operation_id"]),
                "commandType": str(row["command_type"]),
                "aggregateType": str(row["aggregate_type"]),
                "aggregateId": str(row["aggregate_id"]),
                "state": str(row["transport_state"] or "queued"),
                "createdAt": str(row["started_at"] or row["submitted_at"] or ""),
            }
            for row in rows
        ]
        return {
            "tasks": tasks,
            "count": len(tasks),
            "state": "ready",
            "message": "",
            "pollingEnabled": bool(tasks),
        }

    def operation_logs(
        self,
        identity: SessionIdentity,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Return sanitized logs from strict command/attempt/dead-letter facts."""
        safe_limit = max(1, min(int(limit), 500))
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                """
                SELECT c.id AS command_id,c.operation_id,c.command_type,
                       c.aggregate_type,c.aggregate_id,c.status,c.submitted_at,
                       c.settled_at,a.id AS attempt_id,a.transport_state,
                       a.started_at,a.finished_at,d.error_code,d.safe_message
                FROM commands AS c
                LEFT JOIN operation_attempts AS a
                  ON a.scope_id=c.scope_id AND a.command_id=c.id
                 AND a.attempt_no=(
                    SELECT MAX(x.attempt_no) FROM operation_attempts AS x
                    WHERE x.scope_id=c.scope_id AND x.command_id=c.id
                 )
                LEFT JOIN dead_letters AS d
                  ON d.scope_id=c.scope_id AND d.operation_id=c.operation_id
                 AND d.lifecycle_state='active'
                WHERE c.scope_id=?
                ORDER BY COALESCE(a.started_at,c.submitted_at) DESC,c.id DESC
                LIMIT ?
                """,
                (identity.scope_id, safe_limit),
            ).fetchall()
        values = []
        for row in rows:
            state = str(row["transport_state"] or row["status"] or "unknown")
            error_code = str(row["error_code"] or "")
            values.append(
                {
                    "id": str(row["attempt_id"] or row["command_id"]),
                    "level": (
                        "ERROR"
                        if error_code or state in {"failed", "failed_retryable"}
                        else ("WARNING" if state == "blocked" else "INFO")
                    ),
                    "source": "strict_commands",
                    "message": str(row["safe_message"] or row["command_type"]),
                    "timestamp": str(row["started_at"] or row["submitted_at"] or ""),
                    "action": str(row["command_type"]),
                    "entity_type": str(row["aggregate_type"]),
                    "entity_id": str(row["aggregate_id"]),
                    "detail": {
                        "operationId": str(row["operation_id"]),
                        "state": state,
                        "errorCode": error_code or None,
                    },
                }
            )
        return values

    def generation_state(
        self,
        identity: SessionIdentity,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        client_id = str(values.get("clientId") or "")
        answer_intent = str(values.get("answerIntent") or "general")
        with self.repository._connection() as connection:  # noqa: SLF001
            reset = connection.execute(
                "SELECT c.submitted_at,m.receipt FROM commands c JOIN object_manifests m "
                "ON m.id=c.payload_object_manifest_id AND m.scope_id=c.scope_id "
                "WHERE c.scope_id=? AND c.actor_principal_id=? "
                "AND c.command_type='runtime.generation_state.reset' "
                "ORDER BY c.submitted_at DESC,c.id DESC LIMIT 100",
                (identity.scope_id, identity.principal_id),
            ).fetchall()
            boundary = ""
            for row in reset:
                payload = self._json(row["receipt"]).get("payload")
                if not isinstance(payload, Mapping):
                    continue
                if str(payload.get("clientId") or "") == client_id and str(
                    payload.get("answerIntent") or "general"
                ) == answer_intent:
                    boundary = str(row["submitted_at"] or "")
                    break
            answers = connection.execute(
                "SELECT status,boundary_state,model_name,provider_resource_id,created_at "
                "FROM ai_answers WHERE scope_id=? AND lifecycle_state='active' "
                "AND (?='' OR client_id=?) AND (?='' OR created_at>?) "
                "ORDER BY created_at DESC LIMIT 200",
                (identity.scope_id, client_id, client_id, boundary, boundary),
            ).fetchall()
            failures = connection.execute(
                "SELECT a.transport_state FROM operation_attempts a JOIN commands c "
                "ON c.id=a.command_id AND c.scope_id=a.scope_id WHERE c.scope_id=? "
                "AND c.actor_principal_id=? AND (c.command_type LIKE 'workbench.%answer%' "
                "OR c.command_type LIKE 'runtime.%generation%') "
                "AND c.command_type!='runtime.generation_state.reset' "
                "AND a.transport_state IN ('failed','failed_retryable') "
                "AND (?='' OR a.started_at>?) ORDER BY a.started_at DESC LIMIT 200",
                (identity.scope_id, identity.principal_id, boundary, boundary),
            ).fetchall()
        provider = values.get("provider") or None
        model = values.get("model") or None
        if answers:
            model = str(answers[0]["model_name"] or "") or model
        fallback_count = sum(
            1
            for row in answers
            if str(row["boundary_state"] or "") not in {"", "grounded", "ready"}
        )
        timeout_count = sum(
            1 for row in failures if "timeout" in str(row["transport_state"]).lower()
        )
        total = len(answers) + len(failures)
        return {
            "clientId": client_id,
            "answerIntent": answer_intent,
            "provider": provider,
            "model": model,
            "recentTotal": total,
            "recentTimeouts": timeout_count,
            "recentLocalFallbacks": fallback_count,
            "recentSuccesses": len(answers),
            "stableFallbackActive": False,
            "stableFallbackReason": None,
            "cooldownUntil": None,
            "updatedAt": utc_now(),
            "state": "ready" if total else "ready_empty",
            "projectionSource": ["ai_answers", "operation_attempts", "commands"],
            "resetBoundary": boundary or None,
        }

    def analysis_metrics(self, identity: SessionIdentity) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            attempts = connection.execute(
                "SELECT transport_state FROM operation_attempts WHERE scope_id=? "
                "AND started_at>=strftime('%Y-%m-%dT%H:%M:%fZ','now','-30 days')",
                (identity.scope_id,),
            ).fetchall()
            documents = connection.execute(
                "SELECT parse_state FROM knowledge_documents WHERE scope_id=? "
                "AND lifecycle_state='active'",
                (identity.scope_id,),
            ).fetchall()
            records = connection.execute(
                "SELECT verification_state FROM intelligence_records WHERE scope_id=? "
                "AND lifecycle_state='active'",
                (identity.scope_id,),
            ).fetchall()
        completed = sum(
            1 for row in attempts if row["transport_state"] in {"completed", "succeeded"}
        )
        failed = sum(
            1 for row in attempts if row["transport_state"] in {"failed", "failed_retryable"}
        )
        candidates = sum(1 for row in records if row["verification_state"] == "candidate")
        accepted = sum(
            1 for row in records if row["verification_state"] in {"accepted", "confirmed"}
        )
        has_data = bool(attempts or documents or records)
        return {
            "windowDays": 30,
            "newObjectHitRate": self._ratio(
                sum(1 for row in documents if row["parse_state"] == "ready"),
                len(documents),
            ),
            "fallbackRate": self._ratio(failed, len(attempts)),
            "approvalBacklog": candidates,
            "approvalLagHoursMedian": 0,
            "candidateReviewWarningCount": 0,
            "candidateReviewOverdueCount": 0,
            "newCandidateUnreviewed24h": 0,
            "candidateToApprovedConversionRate": self._ratio(accepted, candidates + accepted),
            "staleApprovedJudgmentCount": 0,
            "resolverMismatchRate": 0,
            "pageBreakdown": {
                "operationAttempts": {
                    "total": len(attempts),
                    "completed": completed,
                    "failed": failed,
                },
                "knowledgeDocuments": {
                    "total": len(documents),
                    "ready": sum(1 for row in documents if row["parse_state"] == "ready"),
                },
                "intelligenceRecords": {"candidate": candidates, "accepted": accepted},
            },
            "state": "ready" if has_data else "ready_empty",
            "message": (
                "指标来自严格操作尝试、文档与情报权威"
                if has_data
                else "当前严格对象中没有可汇总的分析记录"
            ),
            "unavailableMetrics": ["approvalLagHoursMedian"],
            "updatedAt": utc_now(),
        }

    def run_log(self, identity: SessionIdentity, run_id: str) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            row = connection.execute(
                "SELECT c.*,a.transport_state,a.started_at,a.finished_at,m.receipt "
                "FROM commands c LEFT JOIN operation_attempts a ON a.scope_id=c.scope_id "
                "AND a.command_id=c.id LEFT JOIN object_manifests m "
                "ON m.id=c.payload_object_manifest_id AND m.scope_id=c.scope_id "
                "WHERE c.scope_id=? AND (c.operation_id=? OR c.id=?) "
                "ORDER BY COALESCE(a.attempt_no,0) DESC LIMIT 1",
                (identity.scope_id, run_id, run_id),
            ).fetchone()
        if row is None:
            raise RepositoryError(404, "runtime_run_missing", "未找到该运行记录")
        result = self._json(row["receipt"]).get("result")
        result = dict(result) if isinstance(result, Mapping) else {}
        return {
            "id": run_id,
            "clientId": "",
            "jobId": str(row["operation_id"]),
            "correlationId": str(row["operation_id"]),
            "provider": "strict_commands",
            "model": result.get("modelName") or result.get("modelUsed"),
            "lane": "cloud_final",
            "cacheHit": False,
            "degraded": str(row["transport_state"] or "") in {"failed", "failed_retryable"},
            "summary": str(result.get("message") or row["command_type"]),
            "detail": result,
            "createdAt": str(row["started_at"] or row["submitted_at"] or ""),
            "state": "available",
        }

    def workspace_chat(
        self,
        identity: SessionIdentity,
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        client_id = str(query.get("clientId") or "")
        with self.repository._connection() as connection:  # noqa: SLF001
            answers = connection.execute(
                "SELECT status,source_count,material_access_mode,boundary_state "
                "FROM ai_answers WHERE scope_id=? AND lifecycle_state='active' "
                "AND (?='' OR client_id=?) ORDER BY created_at DESC LIMIT 200",
                (identity.scope_id, client_id, client_id),
            ).fetchall()
            failed = connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE scope_id=? "
                "AND transport_state IN ('failed','failed_retryable')",
                (identity.scope_id,),
            ).fetchone()[0]
            intelligence = connection.execute(
                "SELECT verification_state FROM intelligence_records WHERE scope_id=? "
                "AND lifecycle_state='active' AND (?='' OR client_id=?)",
                (identity.scope_id, client_id, client_id),
            ).fetchall()
        grounded = sum(1 for row in answers if int(row["source_count"] or 0) > 0)
        has_data = bool(answers or failed or intelligence)
        return {
            "clientId": client_id,
            "recentMessages": len(answers),
            "groundedFallbackRate": self._ratio(
                sum(1 for row in answers if row["boundary_state"] == "grounded_fallback"),
                len(answers),
            ),
            "llmTimeoutRate": 0,
            "dataCenterPrimaryEnabledRate": self._ratio(grounded, len(answers)),
            "systemFailureRate": self._ratio(
                sum(1 for row in answers if row["status"] in {"failed", "failed_retryable"}),
                len(answers),
            ),
            "stableFallbackActive": False,
            "stableFallbackReason": None,
            "dataCenterQuality": {
                "approvedJudgmentCount": sum(
                    1 for row in intelligence if row["verification_state"] in {"accepted", "confirmed"}
                ),
                "candidateJudgmentCount": sum(
                    1 for row in intelligence if row["verification_state"] == "candidate"
                ),
                "parseFailedDocuments": int(failed),
                "contextQuality": "available" if intelligence else "empty",
            },
            "rootCauseSummary": (["最近执行尝试中存在失败记录"] if failed else []),
            "recommendedFixes": (["从死信或原操作入口重试"] if failed else []),
            "state": "ready" if has_data else "ready_empty",
        }

    def workspace_answers(
        self,
        identity: SessionIdentity,
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        client_id = str(query.get("clientId") or "")
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT status,boundary_state,material_access_mode,source_count "
                "FROM ai_answers WHERE scope_id=? AND lifecycle_state='active' "
                "AND (?='' OR client_id=?) ORDER BY created_at DESC LIMIT 500",
                (identity.scope_id, client_id, client_id),
            ).fetchall()
        distribution: dict[str, int] = {}
        for row in rows:
            mode = str(row["boundary_state"] or row["status"] or "unknown")
            distribution[mode] = distribution.get(mode, 0) + 1
        grounded = sum(1 for row in rows if int(row["source_count"] or 0) > 0)
        fallback = sum(1 for row in rows if row["boundary_state"] == "grounded_fallback")
        return {
            "clientId": client_id,
            "recentMessages": len(rows),
            "answerModeDistribution": distribution,
            "fallbackReasonDistribution": {},
            "fallbackPresentationModeDistribution": {},
            "retryBannerWouldShowCount": sum(
                1 for row in rows if row["status"] in {"failed", "failed_retryable"}
            ),
            "retryBannerWouldShowRate": self._ratio(
                sum(1 for row in rows if row["status"] in {"failed", "failed_retryable"}),
                len(rows),
            ),
            "lowConfidenceCount": sum(1 for row in rows if row["boundary_state"] == "low_confidence"),
            "groundedFallbackCount": fallback,
            "groundedAnswerCount": grounded,
            "state": "ready" if rows else "ready_empty",
            "message": "指标来自严格 ai_answers" if rows else "当前没有已保存的工作台回答",
            "projectionSource": "ai_answers",
        }


__all__ = ["PlatformRuntimeDiagnosticsRepository"]
