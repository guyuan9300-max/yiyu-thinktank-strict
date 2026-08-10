from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from strict_common.ids import canonical_json, sha256_text, utc_now

from ..intelligence_capture_local import (
    PublicCaptureError,
    capture_public_web,
)
from ..platform_integrations_local import LocalPlatformOperationRepository
from ..runtime import LocalRuntimeError

from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("intelligence_growth")


_ROUTES: tuple[tuple[str, str], ...] = (
    # Approval and consultation operations.
    ("GET", r"approvals"),
    ("POST", r"approvals/[^/]+/approve"),
    ("POST", r"approvals/[^/]+/reject"),
    ("POST", r"approvals/decide"),
    ("POST", r"consultation/knowledge-requests/process-pending"),
    # Data-center authority and derived operational views.
    ("GET", r"data-center/artifact-status"),
    ("GET", r"data-center/diagnose"),
    ("GET", r"data-center/evidence-quality"),
    ("POST", r"data-center/evidence-quality/[^/]+/label"),
    ("GET", r"data-center/evidence-quality/snapshots"),
    ("POST", r"data-center/evidence-quality/snapshots"),
    ("GET", r"data-center/execution-retry-metrics"),
    ("GET", r"data-center/kernel-primary-rollout"),
    ("POST", r"data-center/kernel-primary-rollout/[^/]+/complete"),
    ("POST", r"data-center/kernel-primary-rollout/[^/]+/rollback"),
    ("POST", r"data-center/kernel-primary-rollout/start"),
    ("GET", r"data-center/operational-status"),
    ("GET", r"data-center/proposal-drafts"),
    ("POST", r"data-center/proposal-drafts/[^/]+/mark-reviewed"),
    ("POST", r"data-center/proposal-drafts/[^/]+/promote"),
    ("POST", r"data-center/proposal-drafts/[^/]+/reject"),
    ("POST", r"data-center/resolve"),
    ("POST", r"data-center/rollback-drill"),
    ("POST", r"data-center/schema/ensure"),
    ("GET", r"data-center/schema/status"),
    ("GET", r"data-center/shadow-runs"),
    ("GET", r"data-center/shadow-summary"),
    ("POST", r"data-center/team-sync/enqueue-all"),
    ("POST", r"data-center/team-sync/run-once"),
    ("GET", r"data-center/team-sync/stats"),
    # Execution tickets and external evidence cards.
    ("GET", r"execution-tickets"),
    ("POST", r"execution-tickets/[^/]+/execute"),
    ("GET", r"execution-tickets/[^/]+/logs"),
    ("POST", r"execution-tickets/[^/]+/retry"),
    ("GET", r"external-evidence-cards"),
    ("POST", r"external-evidence-cards/[^/]+/accept"),
    ("POST", r"external-evidence-cards/[^/]+/create-proposal-draft"),
    ("POST", r"external-evidence-cards/[^/]+/reject"),
    # Growth center.
    ("GET", r"growth/badges"),
    ("GET", r"growth/experience-wall"),
    ("POST", r"growth/experience-wall/[^/]+/like"),
    ("POST", r"growth/handbook/[^/]+/mark-reused"),
    ("GET", r"growth/ledger"),
    ("GET", r"growth/overview"),
    ("POST", r"growth/pending-captures/[^/]+/state"),
    ("POST", r"growth/recommendations/[^/]+/accept"),
    ("POST", r"growth/recommendations/[^/]+/dismiss"),
    ("GET", r"growth/workbench"),
    # Intelligence, profiles, sentiment, themes, and brand strategy.
    ("GET", r"intelligence/brand-mirror/analyze"),
    ("POST", r"intelligence/brand-mirror/analyze"),
    ("GET", r"intelligence/brand-mirror/strategy-extract"),
    ("POST", r"intelligence/brand-mirror/strategy-extract"),
    ("PUT", r"intelligence/brand-mirror/strategy-extract"),
    ("GET", r"intelligence/focus-directives"),
    ("PUT", r"intelligence/focus-directives"),
    ("GET", r"intelligence/items"),
    ("POST", r"intelligence/items/[^/]+/chat"),
    ("POST", r"intelligence/items/[^/]+/dismiss"),
    ("POST", r"intelligence/items/[^/]+/follow"),
    ("POST", r"intelligence/items/[^/]+/task-draft"),
    ("POST", r"intelligence/items/[^/]+/tasks"),
    ("PATCH", r"intelligence/profiles/[^/]+"),
    ("POST", r"intelligence/profiles/[^/]+/refresh"),
    ("POST", r"intelligence/profiles/[^/]+/trial-run"),
    ("POST", r"intelligence/profiles/run-due"),
    ("POST", r"intelligence/refresh"),
    ("GET", r"intelligence/refresh-cycle-settings"),
    ("PUT", r"intelligence/refresh-cycle-settings"),
    ("GET", r"intelligence/refresh-runs"),
    ("GET", r"intelligence/sentiment/audit"),
    ("POST", r"intelligence/sentiment/audit/recompute"),
    ("POST", r"intelligence/sentiment/feedback"),
    ("GET", r"intelligence/sentiment/gap"),
    ("GET", r"intelligence/sentiment/items"),
    ("GET", r"intelligence/sentiment/profile"),
    ("POST", r"intelligence/sentiment/refresh"),
    ("GET", r"intelligence/sentiment/themes"),
    ("GET", r"intelligence/sentiment/themes/[^/]+/items"),
    ("POST", r"intelligence/sentiment/themes/recompute"),
    ("GET", r"intelligence/source-diagnostics"),
    ("POST", r"intelligence/verification-feedback"),
    ("GET", r"intelligence/verification-rules"),
    ("PUT", r"intelligence/verification-rules"),
    ("GET", r"intelligence/work-objects"),
    # Proposals, strategy, and topics.
    ("GET", r"proposals"),
    ("GET", r"proposals/[^/]+"),
    ("POST", r"proposals/[^/]+/approve"),
    ("POST", r"proposals/[^/]+/execute"),
    ("GET", r"proposals/[^/]+/execution-preview"),
    ("POST", r"proposals/[^/]+/execution-ticket"),
    ("POST", r"proposals/[^/]+/reject"),
    ("POST", r"proposals/batch-approve"),
    ("POST", r"proposals/batch-reject"),
    ("GET", r"strategic/thoughts"),
    ("POST", r"strategic/thoughts/[^/]+/review"),
    ("POST", r"strategic/thoughts/[^/]+/state"),
    ("POST", r"strategic/thoughts/refresh"),
    ("POST", r"topic-candidates/[^/]+/external-evidence-card"),
    ("GET", r"topics"),
    ("DELETE", r"topics/candidates/[^/]+"),
    ("POST", r"topics/candidates/[^/]+/chat"),
    ("POST", r"topics/candidates/[^/]+/insights"),
    ("POST", r"topics/candidates/[^/]+/promote-tasks"),
    ("POST", r"topics/candidates/[^/]+/task-plan"),
    ("POST", r"topics/capture"),
    ("POST", r"topics/radars"),
    ("DELETE", r"topics/radars/[^/]+"),
    ("PUT", r"topics/radars/[^/]+"),
    ("POST", r"topics/radars/[^/]+/capture"),
    ("POST", r"topics/radars/assist"),
    ("POST", r"topics/radars/generate-title"),
    ("POST", r"topics/radars/source-label"),
)


_VERSIONED_PATHS: tuple[str, ...] = (
    r"approvals/[^/]+/(?:approve|reject)",
    r"data-center/proposal-drafts/[^/]+/(?:mark-reviewed|promote|reject)",
    r"data-center/evidence-quality/[^/]+/label",
    r"external-evidence-cards/[^/]+/(?:accept|create-proposal-draft|reject)",
    r"growth/pending-captures/[^/]+/state",
    r"growth/recommendations/[^/]+/(?:accept|dismiss)",
    r"intelligence/items/[^/]+/(?:dismiss|follow)",
    r"intelligence/profiles/[^/]+",
    r"intelligence/profiles/[^/]+/(?:refresh|trial-run)",
    r"proposals/[^/]+/(?:approve|execute|execution-ticket|reject)",
    r"strategic/thoughts/[^/]+/(?:review|state)",
    r"topic-candidates/[^/]+/external-evidence-card",
    r"topics/candidates/[^/]+",
    r"topics/radars/[^/]+",
)


def _needs_version(path: str) -> bool:
    if path == "intelligence/profiles/run-due":
        return False
    return any(re.fullmatch(pattern, path) for pattern in _VERSIONED_PATHS)


def _intelligence_item(compatibility: Any, item_id: str) -> dict[str, Any]:
    page = compatibility.runtime.cloud_query(
        "/api/v2/intelligence-growth/query",
        query={
            "resourcePath": "intelligence/items",
            "page": "1",
            "pageSize": "200",
        },
    )
    item = next(
        (
            value
            for value in page.get("items") or []
            if str(value.get("id") or value.get("intelligenceId") or "")
            == item_id
        ),
        None,
    )
    if item is None:
        raise LocalRuntimeError(404, "intelligence_item_missing", "情报条目不存在")
    return dict(item)


def _topic_candidate(compatibility: Any, candidate_id: str) -> dict[str, Any]:
    topics = compatibility.runtime.cloud_query(
        "/api/v2/intelligence-growth/query",
        query={"resourcePath": "topics"},
    )
    candidate = next(
        (
            value
            for value in topics.get("candidates") or []
            if str(value.get("id") or "") == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise LocalRuntimeError(404, "topic_candidate_missing", "议题候选不存在")
    return dict(candidate)


def _private_chat(
    compatibility: Any,
    *,
    object_kind: str,
    object_id: str,
    title: str,
    summary: str,
    request: UiRequest,
) -> dict[str, Any]:
    question = str(request.body.get("question") or "").strip()
    if not question:
        raise LocalRuntimeError(422, "question_required", "请输入问题")
    history = request.body.get("history") or []
    history_text = "\n".join(
        f"{str(item.get('role') or '')}: {str(item.get('content') or '')}"
        for item in history[-10:]
        if isinstance(item, dict)
    )
    prompt = (
        f"对象类型：{object_kind}\n标题：{title}\n摘要：{summary}\n"
        + (f"最近对话：\n{history_text}\n" if history_text else "")
        + f"用户问题：{question}"
    )
    operations = LocalPlatformOperationRepository(compatibility.runtime)
    started = operations.begin(
        idempotency_key=request.idempotency_key,
        command_type=f"intelligence.private_chat.{object_kind}",
        aggregate_type="private_ai_execution",
        aggregate_id=object_id,
        payload={
            "objectKind": object_kind,
            "objectId": object_id,
            "inputHash": sha256_text(prompt),
            "inputChars": len(prompt),
            "historyCount": min(len(history), 10),
        },
        initial_result={
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    if started.get("idempotentReplay"):
        output = started.get("output")
        if isinstance(output, dict):
            return {**dict(output), "question": question}
        state = str(started.get("state") or "")
        if state == "processing":
            raise LocalRuntimeError(
                409,
                "private_ai_operation_in_progress",
                "相同请求仍在处理中，请稍后重试",
            )
        raise LocalRuntimeError(
            409 if state == "blocked" else 503,
            str(started.get("errorCode") or "private_ai_operation_failed"),
            str(started.get("error") or "私有 AI 请求未完成，请重试"),
        )
    try:
        completion = compatibility.runtime.private_ai_completion(
            system_prompt=(
                "你是益语智库的情报分析助手。只能根据提供的权威情报标题、摘要"
                "和用户问题回答；资料不足时明确说明，不虚构外部事实。"
            ),
            prompt=prompt,
            creativity_mode="strict",
        )
        content = str(completion.get("content") or "").strip()
        if not content:
            raise LocalRuntimeError(
                502,
                "private_ai_response_empty",
                "组织模型返回了空结果，请重试",
            )
    except LocalRuntimeError as exc:
        blocked_codes = {
            "needs_login",
            "organization_required",
            "workspace_not_ready",
            "local_ai_profile_not_ready",
            "organization_ai_not_ready",
            "organization_ai_config_incomplete",
            "organization_ai_identity_mismatch",
            "organization_ai_secret_missing",
            "organization_ai_routing_identity_mismatch",
            "organization_ai_routing_mode_invalid",
            "ai_request_rejected",
        }
        operations.update(
            operation_id=str(started["operationId"]),
            state="blocked" if exc.code in blocked_codes else "failed_retryable",
            result_patch={"message": exc.message},
            error_code=exc.code,
            error_message=exc.message,
            captured_sandbox_id=str(started["sandboxId"]),
        )
        raise
    generated_at = utc_now()
    response = {
        f"{object_kind}Id": object_id,
        "question": question,
        "answer": content,
        "generatedAt": generated_at,
        "message": {
            "role": "assistant",
            "content": content,
            "createdAt": generated_at,
        },
        "persistedToOrganizationCloud": False,
    }
    operations.update(
        operation_id=str(started["operationId"]),
        state="completed",
        result_patch={
            "output": {
                key: value
                for key, value in response.items()
                if key != "question"
            },
            "modelUsed": str(completion.get("modelName") or ""),
            "sourceScope": str(completion.get("sourceScope") or ""),
            "persistedToOrganizationCloud": False,
        },
        captured_sandbox_id=str(started["sandboxId"]),
    )
    return response


def _task_draft(item: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    title = str(body.get("title") or item.get("title") or "跟进情报").strip()
    summary = str(item.get("summary") or "").strip()
    return {
        "title": title[:120],
        "desc": str(body.get("desc") or summary)[:4000],
        "priority": str(body.get("priority") or "normal"),
        "listId": body.get("listId"),
        "dueDate": body.get("dueDate"),
        "ddl": str(body.get("ddl") or body.get("dueDate") or ""),
        "ownerId": body.get("ownerId"),
        "ownerName": str(body.get("ownerName") or ""),
        "collaboratorIds": list(body.get("collaboratorIds") or []),
        "tags": list(body.get("tags") or ["情报跟进"]),
        "note": str(body.get("note") or f"来源情报：{item.get('title') or ''}"),
        "ownerRoleHint": body.get("ownerRoleHint"),
        "collaboratorRoleHints": list(
            body.get("collaboratorRoleHints") or []
        ),
    }


def _create_task(
    compatibility: Any,
    request: UiRequest,
    *,
    item: dict[str, Any],
    draft: dict[str, Any],
    suffix: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    payload = {
        "projectId": item.get("clientId") or item.get("projectId"),
        "title": draft["title"],
        "description": draft["desc"],
        "priority": draft["priority"]
        if draft["priority"] in {"low", "normal", "high", "urgent"}
        else "normal",
        "dueDate": draft.get("dueDate") or None,
        "visibilityScope": "participants",
        "collaboratorMembershipIds": draft.get("collaboratorIds") or [],
    }
    if draft.get("ownerId"):
        payload["ownerMembershipId"] = draft["ownerId"]
    return compatibility.runtime.task_command(
        "create",
        task_id=None,
        payload=payload,
        idempotency_key=idempotency_key
        or f"{request.idempotency_key}:{suffix}",
    )


def _work_objects(compatibility: Any) -> list[dict[str, Any]]:
    result = compatibility.runtime.cloud_query(
        "/api/v2/intelligence-growth/query",
        query={"resourcePath": "intelligence/work-objects"},
    )
    return [
        dict(item)
        for item in result or []
        if isinstance(item, Mapping)
    ]


def _capture_specs(
    compatibility: Any,
    request: UiRequest,
) -> list[dict[str, Any]]:
    path = request.path
    if path == "intelligence/refresh":
        content_kind = str(
            request.body.get("contentKind") or "timely_intelligence"
        )
        if content_kind not in {
            "brand_mirror",
            "public_opinion",
            "timely_intelligence",
        }:
            raise LocalRuntimeError(
                422,
                "intelligence_content_kind_invalid",
                "情报刷新类型无效",
            )
        scope_id = str(request.body.get("scopeId") or "")
        objects = _work_objects(compatibility)
        if scope_id:
            objects = [
                item
                for item in objects
                if str(item.get("id") or "") == scope_id
            ]
            if not objects:
                raise LocalRuntimeError(
                    404,
                    "intelligence_work_object_missing",
                    "当前组织中找不到要刷新的项目",
                )
        suffix = {
            "brand_mirror": "品牌 机构介绍 项目 成效",
            "public_opinion": "舆情 评价 反馈",
            "timely_intelligence": "最新 动态 政策 合作 项目",
        }[content_kind]
        return [
            {
                "key": f"work-object:{item.get('id')}",
                "query": f"{item.get('name') or ''} {suffix}".strip(),
                "label": str(item.get("name") or ""),
                "projectId": str(item.get("id") or ""),
                "contentKind": content_kind,
                "recordKind": (
                    "public_opinion_capture"
                    if content_kind == "public_opinion"
                    else "timely_external_capture"
                ),
                "maxResults": 5,
                "preferredSources": [],
            }
            for item in objects[:20]
            if str(item.get("id") or "") and str(item.get("name") or "")
        ]

    if path == "intelligence/sentiment/refresh":
        project_id = str(
            request.body.get("clientId")
            or request.body.get("projectModuleId")
            or ""
        )
        target_name = str(request.body.get("targetName") or "").strip()
        if not target_name and project_id:
            target_name = next(
                (
                    str(item.get("name") or "")
                    for item in _work_objects(compatibility)
                    if str(item.get("id") or "") == project_id
                ),
                "",
            )
        if not target_name:
            raise LocalRuntimeError(
                422,
                "sentiment_target_required",
                "请先选择要抓取舆情的项目",
            )
        max_results = int(request.body.get("maxPerQuery") or 5)
        return [
            {
                "key": f"sentiment:{project_id or 'organization'}",
                "query": (
                    f"{target_name} "
                    f"{request.body.get('businessLine') or ''} 舆情 评价 反馈"
                ).strip(),
                "label": target_name,
                "projectId": project_id,
                "contentKind": "public_opinion",
                "recordKind": "public_opinion_capture",
                "maxResults": min(max(max_results, 1), 10),
                "preferredSources": [],
            }
        ]

    if path in {"topics/capture", "intelligence/profiles/run-due"} or re.fullmatch(
        r"(?:topics/radars|intelligence/profiles)/[^/]+/(?:capture|refresh|trial-run)",
        path,
    ):
        topics = compatibility.runtime.cloud_query(
            "/api/v2/intelligence-growth/query",
            query={"resourcePath": "topics"},
        )
        if path == "topics/capture" or path.startswith("topics/radars/"):
            radars = [
                dict(item)
                for item in topics.get("radars") or []
                if isinstance(item, Mapping)
            ]
            match = re.fullmatch(r"topics/radars/([^/]+)/capture", path)
            if match:
                radar_id = match.group(1)
                radars = [
                    item
                    for item in radars
                    if str(item.get("id") or "") == radar_id
                ]
                if not radars:
                    raise LocalRuntimeError(
                        404,
                        "topic_radar_missing",
                        "情报雷达不存在",
                    )
            return [
                {
                    "key": f"radar:{item.get('id')}",
                    "query": " ".join(
                        value
                        for value in (
                            str(item.get("title") or ""),
                            str(item.get("prompt") or ""),
                        )
                        if value
                    ),
                    "label": str(item.get("title") or "情报雷达"),
                    "projectId": str(item.get("projectId") or ""),
                    "contentKind": "timely_intelligence",
                    "recordKind": "topic_candidate",
                    "radarId": str(item.get("id") or ""),
                    "radarVersion": int(item.get("version") or 0),
                    "maxResults": 5 if match else 3,
                    "preferredSources": list(
                        item.get("preferredSources") or []
                    ),
                }
                for item in radars[:20]
                if str(item.get("id") or "")
            ]

        profiles = [
            dict(item)
            for item in topics.get("intelligenceProfiles") or []
            if isinstance(item, Mapping)
        ]
        match = re.fullmatch(
            r"intelligence/profiles/([^/]+)/(refresh|trial-run)",
            path,
        )
        if match:
            profile_id = match.group(1)
            profiles = [
                item
                for item in profiles
                if str(item.get("id") or "") == profile_id
            ]
            if not profiles:
                raise LocalRuntimeError(
                    404,
                    "intelligence_profile_missing",
                    "情报画像不存在",
                )
        else:
            profiles = [
                item
                for item in profiles
                if bool(
                    item.get("adminProfileRefreshEnabled")
                    or item.get("profileRefreshEnabled")
                )
            ]
        return [
            {
                "key": f"profile:{item.get('id')}",
                "query": " ".join(
                    value
                    for value in [
                        str(
                            item.get("title")
                            or item.get("name")
                            or ""
                        ),
                        str(
                            item.get("effectiveSummary")
                            or item.get("summary")
                            or item.get("description")
                            or ""
                        ),
                        *[
                            str(value)
                            for value in (
                                item.get("adminFocus")
                                or item.get("queries")
                                or []
                            )
                            if str(value)
                        ][:4],
                    ]
                    if value
                ),
                "label": str(
                    item.get("title")
                    or item.get("name")
                    or "情报画像"
                ),
                "projectId": str(
                    item.get("clientId")
                    or item.get("projectId")
                    or ""
                ),
                "contentKind": "timely_intelligence",
                "recordKind": "timely_external_capture",
                "profileId": str(item.get("id") or ""),
                "profileVersion": int(item.get("version") or 0),
                "maxResults": 5,
                "preferredSources": [
                    {"url": str(value), "label": "画像优先来源"}
                    for value in (
                        item.get("adminPriorityUrls")
                        or item.get("sources")
                        or []
                    )
                    if str(value)
                ],
            }
            for item in profiles[:20]
            if str(item.get("id") or "")
        ]
    return []


def _capture_receipt(
    compatibility: Any,
    request: UiRequest,
    specs: list[dict[str, Any]],
) -> dict[str, Any]:
    operations = LocalPlatformOperationRepository(compatibility.runtime)
    started = operations.begin(
        idempotency_key=request.idempotency_key,
        command_type=f"public_intelligence_capture.{request.path}",
        aggregate_type="external_intelligence_capture",
        aggregate_id=sha256_text(request.path)[:24],
        payload={
            "resourcePath": request.path,
            "specs": [
                {
                    "key": spec["key"],
                    "projectId": spec.get("projectId") or None,
                    "contentKind": spec["contentKind"],
                    "queryHash": sha256_text(str(spec["query"])),
                    "radarId": spec.get("radarId") or None,
                    "profileId": spec.get("profileId") or None,
                }
                for spec in specs
            ],
        },
        initial_result={
            "state": "processing",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    if started.get("idempotentReplay"):
        output = started.get("output")
        if isinstance(output, Mapping):
            return dict(output)
        state = str(started.get("state") or "")
        if state == "processing":
            raise LocalRuntimeError(
                409,
                "public_capture_in_progress",
                "相同公开采集请求仍在处理中",
            )
        raise LocalRuntimeError(
            503 if state == "failed_retryable" else 409,
            str(started.get("errorCode") or "public_capture_failed"),
            str(started.get("error") or "公开采集未完成，请重试"),
        )

    captured_sandbox_id = str(started["sandboxId"])
    captured: dict[str, list[Any]] = {}
    failures: list[dict[str, str]] = []

    def run(spec: dict[str, Any]) -> tuple[str, list[Any]]:
        return (
            str(spec["key"]),
            capture_public_web(
                str(spec["query"]),
                max_results=int(spec.get("maxResults") or 5),
                preferred_sources=spec.get("preferredSources") or [],
            ),
        )

    if specs:
        with ThreadPoolExecutor(max_workers=min(4, len(specs))) as pool:
            future_specs = {
                pool.submit(run, spec): spec
                for spec in specs
            }
            for future in as_completed(future_specs):
                spec = future_specs[future]
                try:
                    key, items = future.result()
                    captured[key] = items
                except PublicCaptureError as exc:
                    failures.append(
                        {
                            "key": str(spec["key"]),
                            "code": exc.code,
                            "message": exc.message,
                        }
                    )
                except Exception:
                    failures.append(
                        {
                            "key": str(spec["key"]),
                            "code": "public_search_unexpected_failure",
                            "message": "公开搜索执行异常，可稍后重试",
                        }
                    )
    if failures and len(failures) == len(specs):
        message = failures[0]["message"]
        operations.update(
            operation_id=str(started["operationId"]),
            state="failed_retryable",
            result_patch={"failedSpecCount": len(failures)},
            error_code=failures[0]["code"],
            error_message=message,
            captured_sandbox_id=captured_sandbox_id,
        )
        raise LocalRuntimeError(
            503,
            failures[0]["code"],
            message,
        )

    cloud_items: list[dict[str, Any]] = []
    for spec in specs:
        for index, item in enumerate(captured.get(str(spec["key"]), [])):
            cloud_items.append(
                {
                    **item.as_cloud_payload(),
                    "clientItemKey": f"{spec['key']}:{index}",
                    "projectId": spec.get("projectId") or None,
                    "contentKind": spec["contentKind"],
                    "recordKind": spec["recordKind"],
                    "radarId": spec.get("radarId") or None,
                    "profileId": spec.get("profileId") or None,
                    "queryHash": sha256_text(str(spec["query"])),
                    "tags": ["公开检索"],
                }
            )
    versioned_specs = [
        spec
        for spec in specs
        if spec.get("radarVersion") or spec.get("profileVersion")
    ]
    if versioned_specs:
        latest_topics = compatibility.runtime.cloud_query(
            "/api/v2/intelligence-growth/query",
            query={"resourcePath": "topics"},
        )
        latest_radars = {
            str(item.get("id") or ""): int(item.get("version") or 0)
            for item in latest_topics.get("radars") or []
            if isinstance(item, Mapping)
        }
        latest_profiles = {
            str(item.get("id") or ""): int(item.get("version") or 0)
            for item in latest_topics.get("intelligenceProfiles") or []
            if isinstance(item, Mapping)
        }
        stale = [
            spec
            for spec in versioned_specs
            if (
                spec.get("radarId")
                and latest_radars.get(str(spec["radarId"]))
                != int(spec.get("radarVersion") or 0)
            )
            or (
                spec.get("profileId")
                and latest_profiles.get(str(spec["profileId"]))
                != int(spec.get("profileVersion") or 0)
            )
        ]
        if stale:
            operations.update(
                operation_id=str(started["operationId"]),
                state="blocked",
                result_patch={
                    "fetchedCount": len(cloud_items),
                    "staleSpecCount": len(stale),
                },
                error_code="capture_source_version_conflict",
                error_message="采集期间雷达或画像已更新，请基于最新版本重试",
                captured_sandbox_id=captured_sandbox_id,
            )
            raise LocalRuntimeError(
                409,
                "capture_source_version_conflict",
                "采集期间雷达或画像已更新，请基于最新版本重试",
            )
    try:
        committed = compatibility.runtime.cloud_command(
            "POST",
            "/api/v2/intelligence-growth/command",
            payload={
                "resourcePath": "intelligence/external-capture/commit",
                "method": "POST",
                "query": {},
                "payload": {
                    "captureId": str(started["operationId"]),
                    "items": cloud_items,
                },
            },
            idempotency_key=f"{request.idempotency_key}:authority-commit",
        )
    except LocalRuntimeError as exc:
        state = (
            "failed_retryable"
            if exc.status_code >= 500
            or exc.code in {"needs_login", "failed_retryable"}
            else "blocked"
        )
        operations.update(
            operation_id=str(started["operationId"]),
            state=state,
            result_patch={"fetchedCount": len(cloud_items)},
            error_code=exc.code,
            error_message=exc.message,
            captured_sandbox_id=captured_sandbox_id,
        )
        raise
    item_receipts = [
        {
            "clientItemKey": str(item.get("clientItemKey") or ""),
            "status": str(item.get("status") or ""),
            "intelligenceId": str(item.get("intelligenceId") or ""),
        }
        for item in committed.get("items") or []
        if isinstance(item, Mapping)
    ]
    spec_results = []
    for spec in specs:
        prefix = f"{spec['key']}:"
        receipts = [
            item
            for item in item_receipts
            if item["clientItemKey"].startswith(prefix)
        ]
        spec_results.append(
            {
                "key": spec["key"],
                "fetchedCount": len(
                    captured.get(str(spec["key"]), [])
                ),
                "insertedCount": sum(
                    item["status"] == "inserted" for item in receipts
                ),
                "duplicateCount": sum(
                    item["status"] == "duplicate" for item in receipts
                ),
                "intelligenceIds": [
                    item["intelligenceId"]
                    for item in receipts
                    if item["intelligenceId"]
                ],
            }
        )
    receipt = {
        "captureId": str(started["operationId"]),
        "fetchedCount": len(cloud_items),
        "insertedCount": int(committed.get("insertedCount") or 0),
        "duplicateCount": int(committed.get("duplicateCount") or 0),
        "specResults": spec_results,
        "failures": failures,
        "externalCollectionExecuted": True,
        "modelAnalysisExecuted": False,
        "sourceBodyStored": False,
    }
    operations.update(
        operation_id=str(started["operationId"]),
        state="completed",
        result_patch={"output": receipt},
        captured_sandbox_id=captured_sandbox_id,
    )
    return receipt


def _authority_items(
    compatibility: Any,
    intelligence_ids: list[str],
    *,
    topics: bool = False,
) -> list[dict[str, Any]]:
    if not intelligence_ids:
        return []
    if topics:
        result = compatibility.runtime.cloud_query(
            "/api/v2/intelligence-growth/query",
            query={"resourcePath": "topics"},
        )
        values = result.get("candidates") or []
    else:
        result = compatibility.runtime.cloud_query(
            "/api/v2/intelligence-growth/query",
            query={
                "resourcePath": "intelligence/items",
                "page": "1",
                "pageSize": "200",
            },
        )
        values = result.get("items") or []
    by_id = {
        str(item.get("id") or ""): dict(item)
        for item in values
        if isinstance(item, Mapping)
    }
    return [by_id[item_id] for item_id in intelligence_ids if item_id in by_id]


def _capture_response(
    compatibility: Any,
    request: UiRequest,
    specs: list[dict[str, Any]],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    spec_by_key = {str(spec["key"]): spec for spec in specs}
    result_by_key = {
        str(item.get("key") or ""): item
        for item in receipt.get("specResults") or []
        if isinstance(item, Mapping)
    }
    if request.path == "intelligence/sentiment/refresh":
        spec = specs[0]
        spec_result = result_by_key.get(str(spec["key"]), {})
        # The detailed sentiment labels are returned by the scoped sentiment
        # projection, never reconstructed from local source text.
        sentiment_view = compatibility.runtime.cloud_query(
            "/api/v2/intelligence-growth/query",
            query={
                "resourcePath": "intelligence/sentiment/items",
                "clientId": str(spec.get("projectId") or ""),
            },
        )
        captured_ids = set(spec_result.get("intelligenceIds") or [])
        captured_sentiment = [
            item
            for item in sentiment_view.get("items") or []
            if str(item.get("id") or "") in captured_ids
        ]
        return {
            "targetName": spec["label"],
            "fetchedCount": int(spec_result.get("fetchedCount") or 0),
            "insertedCount": int(spec_result.get("insertedCount") or 0),
            "negativeCount": sum(
                item.get("sentimentLabel") == "negative"
                for item in captured_sentiment
            ),
            "neutralCount": sum(
                item.get("sentimentLabel") == "neutral"
                for item in captured_sentiment
            ),
            "positiveCount": sum(
                item.get("sentimentLabel") == "positive"
                for item in captured_sentiment
            ),
            "externalCollectionExecuted": True,
            "modelAnalysisExecuted": False,
            "sourceBodyStored": False,
        }
    if request.path == "intelligence/refresh":
        results = []
        for spec_result in receipt.get("specResults") or []:
            spec = spec_by_key.get(str(spec_result.get("key") or ""))
            if spec is None:
                continue
            fetched = int(spec_result.get("fetchedCount") or 0)
            inserted = int(spec_result.get("insertedCount") or 0)
            duplicate = int(spec_result.get("duplicateCount") or 0)
            results.append(
                {
                    "scopeType": "client",
                    "scopeId": spec["projectId"],
                    "clientId": spec["projectId"],
                    "projectModuleId": None,
                    "name": spec["label"],
                    "contentKind": spec["contentKind"],
                    "status": "completed" if fetched else "no_results",
                    "intentCount": 1,
                    "diagnosticRunCount": 1,
                    "diagnosticSuccessCount": 1,
                    "fetchJobCount": 1,
                    "candidateCount": fetched,
                    "promotedCount": inserted,
                    "duplicateCount": duplicate,
                    "failedCount": 0,
                    "bodyFetchedCount": 0,
                    "verifiedCount": 0,
                    "summarySuccessCount": fetched,
                    "rejectionCounts": {},
                    "sourceCoverageStatus": "ready",
                    "candidateRefreshStatus": "ready",
                    "lastCandidateFetchAt": utc_now(),
                    "candidateCounts": {
                        "total": fetched,
                        "inserted": inserted,
                        "duplicate": duplicate,
                    },
                    "candidateSamples": [],
                    "queuedJobId": None,
                    "message": (
                        f"已读取 {fetched} 条公开搜索摘要并写入组织云"
                        if fetched
                        else "公开搜索已完成，本轮没有结果"
                    ),
                    "errors": [],
                }
            )
        failures = receipt.get("failures") or []
        totals = {
            "objectCount": len(results),
            "completedCount": sum(
                item["status"] == "completed" for item in results
            ),
            "noResultCount": sum(
                item["status"] == "no_results" for item in results
            ),
            "failedCount": len(failures),
            "intentCount": len(results),
            "fetchJobCount": len(results),
            "candidateCount": int(receipt.get("fetchedCount") or 0),
            "promotedCount": int(receipt.get("insertedCount") or 0),
            "duplicateCount": int(receipt.get("duplicateCount") or 0),
            "bodyFetchedCount": 0,
            "verifiedCount": 0,
            "summarySuccessCount": int(receipt.get("fetchedCount") or 0),
            "rejectionCounts": {},
        }
        status = (
            "partial_failed"
            if failures and results
            else "failed"
            if failures
            else "completed"
            if totals["candidateCount"]
            else "no_results"
        )
        return {
            "status": status,
            "contentKind": str(
                request.body.get("contentKind") or "timely_intelligence"
            ),
            "scopeType": str(request.body.get("scopeType") or "all"),
            "scopeId": request.body.get("scopeId"),
            "results": results,
            "totals": totals,
            "message": (
                "公开搜索摘要已写入组织云"
                if totals["candidateCount"]
                else "公开搜索已完成，本轮没有结果"
            ),
            "generatedAt": utc_now(),
            "externalCollectionExecuted": True,
            "modelAnalysisExecuted": False,
            "sourceBodyStored": False,
        }
    if request.path.startswith("topics/"):
        runs = []
        for spec_result in receipt.get("specResults") or []:
            spec = spec_by_key.get(str(spec_result.get("key") or ""))
            if spec is None:
                continue
            candidates = _authority_items(
                compatibility,
                list(spec_result.get("intelligenceIds") or []),
                topics=True,
            )
            runs.append(
                {
                    "radarId": spec.get("radarId") or "",
                    "radarTitle": spec["label"],
                    "query": spec["query"],
                    "fetchedCount": int(
                        spec_result.get("fetchedCount") or 0
                    ),
                    "createdCount": int(
                        spec_result.get("insertedCount") or 0
                    ),
                    "skippedCount": int(
                        spec_result.get("duplicateCount") or 0
                    ),
                    "candidates": candidates,
                }
            )
        if re.fullmatch(r"topics/radars/[^/]+/capture", request.path):
            return runs[0] if runs else {
                "radarId": request.path.split("/")[-2],
                "radarTitle": "",
                "query": "",
                "fetchedCount": 0,
                "createdCount": 0,
                "skippedCount": 0,
                "candidates": [],
            }
        return {
            "runs": runs,
            "totalCreated": int(receipt.get("insertedCount") or 0),
            "totalSkipped": int(receipt.get("duplicateCount") or 0),
            "externalCollectionExecuted": True,
            "modelAnalysisExecuted": False,
            "sourceBodyStored": False,
        }
    created = int(receipt.get("insertedCount") or 0)
    fetched = int(receipt.get("fetchedCount") or 0)
    if request.path == "intelligence/profiles/run-due":
        return {
            "triggeredCount": len(specs),
            "refreshedCount": len(specs),
            "fetchedCount": fetched,
            "createdCount": created,
            "results": list(receipt.get("specResults") or []),
            "externalCollectionExecuted": True,
            "sourceBodyStored": False,
        }
    return {
        "profileId": specs[0].get("profileId") if specs else None,
        "status": "completed" if fetched else "no_results",
        "state": "ready",
        "fetchedCount": fetched,
        "createdCount": created,
        "duplicateCount": int(receipt.get("duplicateCount") or 0),
        "externalCollectionExecuted": True,
        "modelAnalysisExecuted": False,
        "sourceBodyStored": False,
    }


def _handle_public_capture(
    compatibility: Any,
    request: UiRequest,
) -> dict[str, Any] | None:
    if request.path not in {
        "intelligence/refresh",
        "intelligence/sentiment/refresh",
        "topics/capture",
        "intelligence/profiles/run-due",
    } and not re.fullmatch(
        r"(?:topics/radars|intelligence/profiles)/[^/]+/"
        r"(?:capture|refresh|trial-run)",
        request.path,
    ):
        return None
    specs = _capture_specs(compatibility, request)
    if not specs:
        if request.path == "intelligence/refresh":
            raise LocalRuntimeError(
                409,
                "intelligence_capture_target_not_connected",
                "当前组织没有可采集的项目，请先创建或接入项目后重试",
            )
        empty_receipt = {
            "captureId": "",
            "fetchedCount": 0,
            "insertedCount": 0,
            "duplicateCount": 0,
            "specResults": [],
            "failures": [],
            "externalCollectionExecuted": False,
            "modelAnalysisExecuted": False,
            "sourceBodyStored": False,
        }
        return _capture_response(
            compatibility,
            request,
            specs,
            empty_receipt,
        )
    receipt = _capture_receipt(
        compatibility,
        request,
        specs,
    )
    return _capture_response(
        compatibility,
        request,
        specs,
        receipt,
    )


def _handle_local_intelligence_actions(
    compatibility: Any,
    request: UiRequest,
) -> Any | None:
    public_capture = _handle_public_capture(compatibility, request)
    if public_capture is not None:
        return public_capture
    match = re.fullmatch(
        r"intelligence/items/([^/]+)/(chat|task-draft|tasks)",
        request.path,
    )
    if match:
        item_id, action = match.groups()
        item = _intelligence_item(compatibility, item_id)
        if action == "chat":
            response = _private_chat(
                compatibility,
                object_kind="item",
                object_id=item_id,
                title=str(item.get("title") or ""),
                summary=str(item.get("summary") or ""),
                request=request,
            )
            response["itemId"] = response.pop("itemId")
            return response
        draft = _task_draft(item, dict(request.body))
        if action == "task-draft":
            return {"itemId": item_id, "draft": draft}
        created = _create_task(
            compatibility,
            request,
            item=item,
            draft=draft,
            suffix="task",
        )
        return {"item": item, "task": created.get("task") or {}}

    match = re.fullmatch(
        r"topics/candidates/([^/]+)/(chat|insights|task-plan|promote-tasks)",
        request.path,
    )
    if match:
        candidate_id, action = match.groups()
        candidate = _topic_candidate(compatibility, candidate_id)
        if action == "chat":
            response = _private_chat(
                compatibility,
                object_kind="candidate",
                object_id=candidate_id,
                title=str(candidate.get("title") or ""),
                summary=str(candidate.get("summary") or ""),
                request=request,
            )
            response["candidateId"] = response.pop("candidateId")
            return response
        summary = str(candidate.get("summary") or "").strip()
        points = [
            value.strip()
            for value in re.split(r"[。；;\n]+", summary)
            if value.strip()
        ][:6]
        if action == "insights":
            now = utc_now()
            return {
                "candidateId": candidate_id,
                "overview": summary,
                "keyPoints": points,
                "recommendationReasons": [
                    "该候选已进入严格组织情报权威对象"
                ],
                "practicalUses": ["用于项目研判、任务讨论或后续资料核验"],
                "editorialNote": "以上为现有标题和摘要的确定性提炼，未补造外部事实。",
                "discussionPrompts": [
                    f"这条情报对“{candidate.get('title') or ''}”涉及的项目意味着什么？"
                ],
                "createdAt": now,
                "updatedAt": now,
            }
        if action == "task-plan":
            return {
                "candidateId": candidate_id,
                "candidateTitle": candidate.get("title") or "",
                "candidateSummary": summary,
                "candidateSource": candidate.get("source") or "",
                "candidateSourceUrl": candidate.get("sourceUrl"),
                "overview": "先核验情报，再判断是否进入项目行动。",
                "tasks": [
                    {
                        "title": f"核验：{candidate.get('title') or '议题候选'}",
                        "desc": summary,
                        "dueDate": None,
                        "ddl": "",
                        "note": f"来源议题候选 {candidate_id}",
                        "priority": "normal",
                        "tags": ["议题核验"],
                    }
                ],
            }
        valid_drafts: list[tuple[int, dict[str, Any]]] = []
        warnings = []
        for index, raw in enumerate(request.body.get("tasks") or []):
            if not isinstance(raw, dict):
                warnings.append(f"第 {index + 1} 项任务格式无效")
                continue
            draft = _task_draft(candidate, raw)
            if not draft["title"]:
                warnings.append(f"第 {index + 1} 项任务缺少标题")
                continue
            valid_drafts.append((index, draft))
        if not valid_drafts:
            raise LocalRuntimeError(
                422,
                "task_promotion_items_required",
                "请选择至少一个有效任务草案",
            )
        candidate_version = int(candidate.get("version") or 0)
        prepared_items = [
            {
                "title": draft["title"],
                "draftHash": sha256_text(canonical_json(draft)),
            }
            for _, draft in valid_drafts
        ]
        prepare = compatibility.runtime.cloud_command(
            "POST",
            "/api/v2/intelligence-growth/command",
            payload={
                "resourcePath": request.path,
                "method": "POST",
                "query": {},
                "payload": {
                    "phase": "prepare",
                    "expectedVersion": candidate_version,
                    "tasks": prepared_items,
                },
            },
            idempotency_key=f"{request.idempotency_key}:bulk:prepare",
        )
        tasks: list[dict[str, Any]] = []
        item_results: list[dict[str, Any]] = []
        for item_key, ((source_index, draft), prepared) in enumerate(
            zip(valid_drafts, prepared_items, strict=True)
        ):
            stable_key = (
                f"candidate-promote:{candidate_id}:{candidate_version}:"
                f"{source_index}:{prepared['draftHash']}"
            )
            try:
                created = _create_task(
                    compatibility,
                    request,
                    item=candidate,
                    draft=draft,
                    suffix=f"promote:{source_index}",
                    idempotency_key=stable_key,
                )
                task = dict(created.get("task") or {})
                task_id = str(task.get("taskId") or task.get("id") or "")
                if not task_id:
                    raise LocalRuntimeError(
                        502,
                        "task_creation_receipt_invalid",
                        "任务创建未返回权威任务 ID",
                    )
                tasks.append(task)
                item_results.append(
                    {
                        "itemKey": str(item_key),
                        "status": "committed",
                        "taskId": task_id,
                    }
                )
            except LocalRuntimeError as exc:
                item_results.append(
                    {
                        "itemKey": str(item_key),
                        "status": "failed",
                        "errorCode": exc.code,
                    }
                )
                warnings.append(f"第 {source_index + 1} 项创建失败：{exc.message}")
        finalized = compatibility.runtime.cloud_command(
            "POST",
            "/api/v2/intelligence-growth/command",
            payload={
                "resourcePath": request.path,
                "method": "POST",
                "query": {},
                "payload": {
                    "phase": "finalize",
                    "bulkOperationId": prepare["bulkOperationId"],
                    "expectedVersion": int(prepare.get("version") or 1),
                    "itemResults": item_results,
                },
            },
            idempotency_key=f"{request.idempotency_key}:bulk:finalize",
        )
        return {
            "tasks": tasks,
            "createdCount": len(tasks),
            "flowbackResults": finalized.get("items") or [],
            "bulkOperationId": finalized.get("bulkOperationId"),
            "bulkStatus": finalized.get("status"),
            "warnings": warnings,
        }

    if request.path == "topics/radars/generate-title":
        prompt = str(request.body.get("prompt") or "").strip()
        if not prompt:
            raise LocalRuntimeError(422, "radar_prompt_required", "请输入雷达关注方向")
        return {"title": prompt.splitlines()[0][:30]}
    if request.path == "topics/radars/assist":
        prompt = str(request.body.get("prompt") or "").strip()
        if not prompt:
            raise LocalRuntimeError(422, "radar_prompt_required", "请输入雷达关注方向")
        title = prompt.splitlines()[0][:30]
        queries = [
            value
            for value in dict.fromkeys(
                [title, *re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,20}", prompt)]
            )
            if value
        ][:6]
        return {"title": title, "prompt": prompt, "queries": queries}
    if request.path == "topics/radars/source-label":
        url = str(request.body.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise LocalRuntimeError(422, "source_url_invalid", "请输入有效的网页地址")
        return {"url": url, "label": parsed.hostname.removeprefix("www.")}
    return None


def _forward_unpinned(compatibility: Any, request: UiRequest, _match: Any) -> Any:
    if request.path == "strategic/thoughts":
        return compatibility.runtime.cloud_query(
            "/api/v2/workbench/strategic-thoughts",
            query=dict(request.query),
        )
    if request.path == "strategic/thoughts/refresh":
        return compatibility.runtime.cloud_command(
            "POST",
            "/api/v2/workbench/strategic-thoughts/refresh",
            payload=dict(request.body),
            idempotency_key=request.idempotency_key,
        )
    strategic_action = re.fullmatch(
        r"strategic/thoughts/([^/]+)/(state|review)", request.path
    )
    if strategic_action:
        thought_id, action = strategic_action.groups()
        return compatibility.runtime.cloud_command(
            "POST",
            f"/api/v2/workbench/strategic-thoughts/{thought_id}/{action}",
            payload=dict(request.body),
            idempotency_key=request.idempotency_key,
        )
    if request.method == "GET":
        return compatibility.runtime.cloud_query(
            "/api/v2/intelligence-growth/query",
            query={"resourcePath": request.path, **dict(request.query)},
        )

    local_result = _handle_local_intelligence_actions(compatibility, request)
    if local_result is not None:
        return local_result

    payload = dict(request.body)
    if (
        request.method == "PUT"
        and request.path == "intelligence/brand-mirror/strategy-extract"
        and "expectedVersion" not in payload
    ):
        version_view = compatibility.runtime.cloud_query(
            "/api/v2/intelligence-growth/version",
            query={
                "resourcePath": request.path,
                "clientId": str(payload.get("clientId") or ""),
            },
        )
        payload["expectedVersion"] = int(
            version_view.get("expectedVersion") or 0
        )
    if request.path == "approvals/decide" and "expectedVersion" not in payload:
        approval_id = str(
            payload.get("approvalId")
            or payload.get("proposalId")
            or payload.get("id")
            or ""
        )
        decision = str(payload.get("decision") or payload.get("action") or "").lower()
        suffix = "approve" if decision in {"approve", "approved", "accept"} else "reject"
        if approval_id:
            version_view = compatibility.runtime.cloud_query(
                "/api/v2/intelligence-growth/version",
                query={"resourcePath": f"approvals/{approval_id}/{suffix}"},
            )
            if version_view.get("expectedVersion") is not None:
                payload["expectedVersion"] = version_view["expectedVersion"]
    if request.path in {"proposals/batch-approve", "proposals/batch-reject"}:
        raw_ids = (
            payload.get("proposalIds")
            or payload.get("ids")
            or payload.get("selectedIds")
            or []
        )
        proposal_ids = [
            str(item.get("id") if isinstance(item, dict) else item)
            for item in raw_ids
            if str(item.get("id") if isinstance(item, dict) else item)
        ]
        if proposal_ids and "itemVersions" not in payload:
            snapshot = compatibility._snapshot()
            versions = {
                str(item.get("intelligenceId")): int(item.get("version") or 0)
                for item in snapshot.get("intelligence") or []
                if str(item.get("intelligenceId")) in proposal_ids
            }
            payload["itemVersions"] = versions
    if _needs_version(request.path) and "expectedVersion" not in payload:
        version_view = compatibility.runtime.cloud_query(
            "/api/v2/intelligence-growth/version",
            query={"resourcePath": request.path},
        )
        expected_version = version_view.get("expectedVersion")
        if expected_version is not None:
            payload["expectedVersion"] = expected_version
        item_versions = version_view.get("itemVersions")
        if item_versions and "itemVersions" not in payload:
            payload["itemVersions"] = item_versions

    return compatibility.runtime.cloud_command(
        "POST",
        "/api/v2/intelligence-growth/command",
        payload={
            "resourcePath": request.path,
            "method": request.method,
            "query": dict(request.query),
            "payload": payload,
        },
        idempotency_key=request.idempotency_key,
    )


def _forward(compatibility: Any, request: UiRequest, match: Any) -> Any:
    pin = getattr(compatibility.runtime, "pinned_workspace_context", None)
    if pin is None:
        return _forward_unpinned(compatibility, request, match)
    with pin():
        return _forward_unpinned(compatibility, request, match)


for _method, _pattern in _ROUTES:
    router.route(_method, _pattern)(_forward)
