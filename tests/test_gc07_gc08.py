from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from backend.app.gc07_sources import extract_pptx_text, extract_visible_html_text
from backend.app.gc08_meetings import (
    GC08LocalContext,
    GC08LocalMeetingRepository,
)
from backend.app.ui_domains.gc10_consumers import router as gc10_consumers_router
from backend.app.ui_domains.gc12_intelligence import router as gc12_intelligence_router
from backend.app.ui_domains.gc08_meetings import (
    _meeting_fallback_brief,
    _meeting_sources,
)
from backend.app.ui_domains.routing import UiRequest
from backend.app.runtime import LocalRuntimeError, WorkspaceRuntime
from strict_common.agent_memory import builtin_agent_id
from cloud_backend.app.repositories.gc08_meetings import (
    GC08MeetingMinutesRepository,
)
from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repositories.gc15_official_website import (
    capture_official_website,
    official_fact_candidates,
    review_official_fact_candidate,
)
from cloud_backend.app.repositories.gc12_intelligence import (
    GC12IntelligenceRepository,
)
from cloud_backend.app.repositories.gc12_corrections import (
    create_strategic_profile_clarification,
)


def test_gc12_cloud_paths_are_inside_the_connected_golden_chain() -> None:
    allowed = {
        "GET": (
            "/api/v2/domain/project-materials/intelligence",
            "/api/v2/domain/project-materials/intelligence/focus-directives",
            "/api/v2/domain/project-materials/intelligence/refresh-cycle-settings",
            "/api/v2/domain/project-materials/intelligence/refresh-runs",
            "/api/v2/domain/project-materials/intelligence/verification-rules",
            "/api/v2/domain/project-materials/intelligence/strategy-extract",
            "/api/v2/domain/project-materials/intelligence/items/intel_1",
        ),
        "PUT": (
            "/api/v2/domain/project-materials/intelligence/focus-directives",
            "/api/v2/domain/project-materials/intelligence/refresh-cycle-settings",
            "/api/v2/domain/project-materials/intelligence/verification-rules",
        ),
        "POST": (
            "/api/v2/domain/project-materials/intelligence/intel_1/attention",
            "/api/v2/domain/project-materials/intelligence/external-capture",
            "/api/v2/domain/project-materials/intelligence/items/intel_1/answers",
        ),
    }
    for method, paths in allowed.items():
        for path in paths:
            assert WorkspaceRuntime._connected_cloud_path_allowed(method, path)
from cloud_backend.app.repositories.gc14_strategic_profile import (
    rebuild_strategic_profile,
)
from cloud_backend.app.repository import RepositoryError
from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import sha256_text, utc_now
from strict_common.physical_schema import user_tables
from strict_common.schema import initialize_database, runtime_connection
from tests.test_gc14_workbench_answer import _repository as gc14_repository


def test_meeting_context_uses_agenda_relationship_and_narrative_fallback() -> None:
    context = {
        "officialWebsiteFacts": [
            {
                "sourceDescription": "心益计划官网",
                "summary": "心益计划通过培训大学生志愿者开展儿童心理活动课。",
                "sourceKind": "official_website_fact",
            }
        ]
    }
    focused = {"title": "心益计划培训会议", "agenda": "核对志愿者培训安排"}
    generic = {"title": "日慈项目会议", "agenda": "例行沟通", "clientName": "日慈基金会"}
    focused_sources, focused_relationship = _meeting_sources(context, focused)
    generic_sources, generic_relationship = _meeting_sources(context, generic)
    assert focused_relationship is True
    assert generic_relationship is False
    narrative = _meeting_fallback_brief(generic, generic_sources, generic_relationship)
    assert "尚不足以判断" in narrative
    assert "- " not in narrative


def _pptx(path: Path, *, text: str | None) -> None:
    body = (
        f'<a:t>{text}</a:t>' if text is not None else '<p:pic><p:nvPicPr/></p:pic>'
    )
    slide = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        f"<p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>"
    )
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("ppt/slides/slide1.xml", slide)


def test_gc07_pptx_and_web_adapters_never_report_empty_success(tmp_path: Path) -> None:
    text_pptx = tmp_path / "text-layer.pptx"
    _pptx(text_pptx, text="GC07_PPTX_TEXT_SENTINEL")
    assert extract_pptx_text(text_pptx) == "GC07_PPTX_TEXT_SENTINEL"

    image_only = tmp_path / "image-only.pptx"
    _pptx(image_only, text=None)
    with pytest.raises(LocalRuntimeError) as pptx_error:
        extract_pptx_text(image_only)
    assert pptx_error.value.code == "local_document_pptx_ocr_required"

    assert extract_visible_html_text(
        "<html><style>hidden</style><body>GC07_WEB_SENTINEL</body></html>"
    ) == "GC07_WEB_SENTINEL"
    with pytest.raises(LocalRuntimeError) as web_error:
        extract_visible_html_text("<html><script>onlyCode()</script></html>")
    assert web_error.value.code == "local_web_text_missing"


def test_gc10_retired_consultation_poll_is_terminal_and_does_not_call_cloud() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.ready_checks = 0

        def _current_context(self, require_ready: bool = True) -> object:
            assert require_ready is True
            self.ready_checks += 1
            return object()

        def cloud_query(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("retired consultation poll must not call cloud")

        def cloud_command(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("retired consultation poll must not write cloud")

    compatibility = type("Compatibility", (), {"runtime": Runtime()})()
    result = gc10_consumers_router.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="consultation/knowledge-requests/process-pending",
            query={},
            body={},
            idempotency_key="gc10-retired-poll",
        ),
    )
    assert result["state"] == "ready"
    assert result["pollingEnabled"] is False
    assert result["totalPending"] == 0
    assert result["items"] == []
    assert compatibility.runtime.ready_checks == 1


def test_gc12_intelligence_shell_exposes_connected_visible_actions() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.ready_checks = 0
            self.commands: list[tuple[str, str]] = []

        def _current_context(self, require_ready: bool = True) -> object:
            assert require_ready is True
            self.ready_checks += 1
            return type(
                "Context",
                (),
                {"membership_id": "membership_gc12_shell", "cloud_instance_id": "cli_gc12_shell"},
            )()

        def capture_sandbox_context(self) -> object:
            return self._current_context()

        def organization_ai_completion(self, **_kwargs: object) -> dict[str, object]:
            return {
                "content": "GC12 情报回答",
                "provider": {"configId": "provider_gc12", "modelName": "model_gc12"},
            }

        def cloud_query(self, path: str, **_kwargs: object) -> dict[str, object]:
            if path.endswith("/projects"):
                return {
                    "projects": [
                        {
                            "projectId": "client_gc12_shell",
                            "name": "GC12项目",
                            "documentCount": 1,
                            "officialWebsiteUrl": "https://example.org",
                        }
                    ]
                }
            if path.endswith("/intelligence/refresh-runs"):
                return {"runs": [{"id": "run_gc12_shell", "status": "completed"}]}
            if path.endswith("/intelligence/focus-directives"):
                return []
            if path.endswith("/intelligence/refresh-cycle-settings"):
                return {
                    "profileCompletionHours": 72,
                    "timelyIntelligenceHours": 24,
                    "state": "default",
                }
            if path.endswith("/intelligence/verification-rules"):
                return []
            if path.endswith("/intelligence/strategy-extract"):
                return {
                    "extract": {
                        "clientId": "client_gc12_shell",
                        "strategicObjective": "形成稳定项目影响力",
                        "strategicObjectiveSources": ["官网"],
                        "methodology": "以事实和协作推进",
                        "methodologySources": ["客户档案"],
                        "stakeholders": [],
                        "sourceStrategyMdHash": "1" * 64,
                        "sourceMethodologyMdHash": "2" * 64,
                        "llmModel": "model_gc12",
                        "error": None,
                        "extractedAt": "2026-08-07T00:00:00Z",
                        "confirmedBy": None,
                        "confirmedAt": None,
                        "isStale": False,
                    }
                }
            if path.endswith("/narrative"):
                return {"rev": 1}
            if "/intelligence/items/" in path:
                return {
                    "id": "intel_1",
                    "title": "GC12情报",
                    "summary": "GC12真实摘要",
                    "clientId": "client_gc12_shell",
                    "source": "官网",
                    "sourceUrl": "https://example.org/fact",
                    "verificationStatus": "verified",
                    "version": 1,
                }
            if path.endswith("/intelligence"):
                query = dict(_kwargs.get("query") or {})
                if query.get("contentKind") == "public_opinion":
                    return {
                        "items": [
                            {
                                "id": "intel_public_1",
                                "clientId": "client_gc12_shell",
                                "title": "GC12公开评价",
                                "summary": "公开来源肯定项目协作成效。",
                                "source": "example.org",
                                "sourceUrl": "https://example.org/public-opinion",
                                "capturedAt": "2026-08-07T00:00:00Z",
                                "sentimentLabel": "positive",
                                "sentimentReason": "包含明确肯定表达",
                                "tags": [],
                                "userStatus": "active",
                            }
                        ],
                        "total": 1,
                        "candidateSamples": [],
                    }
                return {"items": [], "total": 0, "candidateSamples": []}
            raise AssertionError(path)

        def cloud_command(
            self, method: str, path: str, *, payload: object, **_kwargs: object
        ) -> dict[str, object]:
            self.commands.append((method, path))
            if path.endswith("/intelligence/focus-directives"):
                return {"id": "focus_gc12_shell", **dict(payload)}
            if path.endswith("/intelligence/refresh-cycle-settings"):
                return {"state": "ready", **dict(payload)}
            if path.endswith("/intelligence/verification-rules"):
                return {"id": "verify_gc12_shell", **dict(payload)}
            if path.endswith("/answers"):
                return {"answerId": "answer_gc12_shell", "sourceCount": 1}
            if path == "/api/v2/domain/tasks":
                return {"task": {"taskId": "task_gc12_shell", "title": "跟进情报"}}
            if path.endswith("/attention"):
                return {"id": "intel_1", "userStatus": "following"}
            if path.endswith("/narrative-clarifications"):
                return {"status": "applied"}
            raise AssertionError(path)

    runtime = Runtime()
    compatibility = type("Compatibility", (), {"runtime": runtime})()

    def request(
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, object] | None = None,
    ) -> UiRequest:
        return UiRequest(
            method=method,
            path=path,
            query=query or {},
            body=body or {},
            idempotency_key="gc12-shell",
        )

    objects = gc12_intelligence_router.dispatch(
        compatibility, request("GET", "intelligence/work-objects")
    )
    assert objects[0]["sourceCoverageStatus"] == "ready"
    assert objects[0]["candidateRefreshStatus"] == "missing"
    assert gc12_intelligence_router.dispatch(
        compatibility, request("GET", "intelligence/focus-directives")
    ) == []
    settings = gc12_intelligence_router.dispatch(
        compatibility, request("GET", "intelligence/refresh-cycle-settings")
    )
    assert settings["state"] == "default"
    assert gc12_intelligence_router.dispatch(
        compatibility, request("GET", "intelligence/refresh-runs")
    ) == [{"id": "run_gc12_shell", "status": "completed"}]
    for method, path in (
        ("POST", "intelligence/refresh"),
        ("GET", "intelligence/source-diagnostics"),
    ):
        with pytest.raises(LocalRuntimeError) as error:
            gc12_intelligence_router.dispatch(compatibility, request(method, path))
        assert error.value.status_code == 422
    draft = gc12_intelligence_router.dispatch(
        compatibility,
        request("POST", "intelligence/items/intel_1/task-draft"),
    )
    assert draft["draft"]["title"] == "跟进：GC12情报"
    created = gc12_intelligence_router.dispatch(
        compatibility,
        request(
            "POST",
            "intelligence/items/intel_1/tasks",
            body={"title": "跟进情报", "priority": "normal"},
        ),
    )
    assert created["task"]["taskId"] == "task_gc12_shell"
    chat = gc12_intelligence_router.dispatch(
        compatibility,
        request(
            "POST",
            "intelligence/items/intel_1/chat",
            body={"question": "这条情报说了什么？"},
        ),
    )
    assert chat["answer"] == "GC12 情报回答"
    sentiment = gc12_intelligence_router.dispatch(
        compatibility,
        request(
            "GET",
            "intelligence/sentiment/items",
            query={"clientId": "client_gc12_shell"},
        ),
    )
    assert sentiment["state"] == "ready"
    assert sentiment["items"][0]["sentimentLabel"] == "positive"
    audit = gc12_intelligence_router.dispatch(
        compatibility,
        request(
            "POST",
            "intelligence/sentiment/audit/recompute",
            body={"clientId": "client_gc12_shell", "targetName": "GC12项目"},
        ),
    )
    assert audit["ok"] is True
    assert audit["audit"]["evidenceThemeIds"]
    extract = gc12_intelligence_router.dispatch(
        compatibility,
        request(
            "GET",
            "intelligence/brand-mirror/strategy-extract",
            query={"clientId": "client_gc12_shell"},
        ),
    )
    assert extract["extract"]["strategicObjective"] == "形成稳定项目影响力"
    updated = gc12_intelligence_router.dispatch(
        compatibility,
        request(
            "PUT",
            "intelligence/brand-mirror/strategy-extract",
            body={
                "clientId": "client_gc12_shell",
                "strategicObjective": "形成稳定项目影响力",
                "methodology": "以事实和协作推进",
            },
        ),
    )
    assert updated["extract"]["methodology"] == "以事实和协作推进"
    assert sum(path.endswith("/narrative-clarifications") for _, path in runtime.commands) == 2


def test_gc12_official_fact_review_versions_intelligence_and_propagates(
    tmp_path: Path,
) -> None:
    repository, identity, seed = gc14_repository(tmp_path / "gc12-official")
    now = utc_now()
    bot_id = builtin_agent_id(identity.organization_id, "intelligence_research")
    strategy_bot_id = builtin_agent_id(identity.organization_id, "strategy_companion")
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT OR IGNORE INTO secured_resources (id,scope_id,resource_kind,"
            "lifecycle_state,version,resource_type_key,created_at,updated_at,"
            "deleted_at,authority_role,origin_instance_id) VALUES (?,?,"
            "'bot_definition','active',1,'builtin_agent',?,?,NULL,'cloud',?)",
            (bot_id, identity.scope_id, now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT OR IGNORE INTO bot_definitions (id,scope_id,agent_kind,version,handle,"
            "description,capability_policy_version,enabled,lifecycle_state,"
            "created_at,updated_at,deleted_at) VALUES (?,?,'intelligence_research',"
            "1,'intelligence-research','官网情报研究','builtin-agent-contract-v1',"
            "1,'active',?,?,NULL)",
            (bot_id, identity.scope_id, now, now),
        )
        connection.execute(
            "INSERT OR IGNORE INTO secured_resources (id,scope_id,resource_kind,"
            "lifecycle_state,version,resource_type_key,created_at,updated_at,"
            "deleted_at,authority_role,origin_instance_id) VALUES (?,?,"
            "'bot_definition','active',1,'builtin_agent',?,?,NULL,'cloud',?)",
            (strategy_bot_id, identity.scope_id, now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT OR IGNORE INTO bot_definitions (id,scope_id,agent_kind,version,handle,"
            "description,capability_policy_version,enabled,lifecycle_state,"
            "created_at,updated_at,deleted_at) VALUES (?,?,'strategy_companion',"
            "1,'strategy-companion','战略陪伴','builtin-agent-contract-v1',"
            "1,'active',?,?,NULL)",
            (strategy_bot_id, identity.scope_id, now, now),
        )
        connection.commit()

    rebuild_strategic_profile(
        repository,
        identity,
        project_id=seed["projectId"],
        idempotency_key="gc12-profile-one",
        prepared_profile={
            "schema": "yiyu.strategic-client-profile.v2",
            "generator": "strategy_companion_local_wiki_v1",
            "modelName": seed["modelName"],
            "dimensions": [
                {
                    "dimension": dimension,
                    "narrative": narrative,
                    "references": [
                        {
                            "sourceType": "local_document",
                            "sourceId": "source_gc12_profile",
                            "label": "GC12项目资料",
                        }
                    ],
                }
                for dimension, narrative in (
                    ("essence", "项目定位"),
                    ("business_intro", "项目业务"),
                    ("cooperation", "以伙伴协作推进"),
                    ("people", "项目相关方"),
                    ("timeline", "项目历程"),
                    ("next_steps", "建立长期项目影响力"),
                )
            ],
            "sourceDocuments": [
                {
                    "sourceObjectId": "source_gc12_profile",
                    "sourceVersion": 1,
                    "contentHash": "9" * 64,
                    "title": "GC12项目资料",
                }
            ],
            "coverage": {
                "eligibleDocumentCount": 1,
                "scannedDocumentCount": 1,
                "citedDocumentCount": 1,
            },
        },
    )

    capture_official_website(
        repository,
        identity,
        project_id=seed["projectId"],
        pages=[
            {
                "title": "GC12官网事实",
                "url": "https://example.org/gc12",
                "text": "GC12负责人是林佳维。",
                "contentHash": "7" * 64,
                "capturedAt": now,
                "pageRole": "institutional_profile",
                "captureKind": "static",
            }
        ],
        fact_candidates=[
            {
                "term": "GC12项目",
                "attributeName": "负责人",
                "valueCategory": "person",
                "valueText": "林佳维",
                "evidence": "GC12负责人是林佳维。",
                "sourceUrl": "https://example.org/gc12",
                "sourceTitle": "GC12官网事实",
                "subjectKind": "project",
                "factKind": "person_profile",
                "confidence": 0.97,
            }
        ],
        idempotency_key="gc12-official-capture-once",
    )
    facts = official_fact_candidates(
        repository,
        identity,
        project_id=seed["projectId"],
        status="verified",
    )["attributes"]
    assert len(facts) == 1
    reviewed = review_official_fact_candidate(
        repository,
        identity,
        project_id=seed["projectId"],
        fact_id=facts[0]["id"],
        review_status="verified",
        payload={"valueText": "林佳维（已核实）"},
        idempotency_key="gc12-official-review-once",
    )
    assert reviewed["status"] == "verified"
    assert reviewed["consumerPropagation"]["state"] == "completed"
    intelligence = GC12IntelligenceRepository(repository)
    public_capture = intelligence.commit_external_capture(
        identity,
        project_id=seed["projectId"],
        capture_id="capture_gc12_public_one",
        content_kind="public_opinion",
        capture_kind="manual_intelligence",
        items=[
            {
                "clientItemKey": "sentiment:0",
                "title": "GC12公开评价",
                "summary": "公开来源肯定项目协作成效。",
                "sourceName": "example.org",
                "sourceUrl": "https://example.org/public-opinion",
                "capturedAt": now,
                "sentiment": "positive",
                "sentimentReason": "包含明确肯定表达",
                "contentHash": "8" * 64,
            }
        ],
        idempotency_key="gc12-public-capture-one",
    )
    assert public_capture["insertedCount"] == 1
    assert public_capture["sourceBodyStored"] is False
    empty_capture = intelligence.commit_external_capture(
        identity,
        project_id=seed["projectId"],
        capture_id="capture_gc12_empty_one",
        content_kind="timely_intelligence",
        capture_kind="manual_intelligence",
        items=[],
        idempotency_key="gc12-empty-capture-one",
    )
    assert empty_capture["insertedCount"] == 0
    assert empty_capture["candidateCount"] == 0
    assert empty_capture["externalCollectionExecuted"] is True
    assert empty_capture["items"] == []
    empty_refresh_runs = intelligence.list_refresh_runs(
        identity,
        {"scopeId": seed["projectId"], "contentKind": "timely_intelligence"},
    )["runs"]
    assert len(empty_refresh_runs) == 1
    assert empty_refresh_runs[0]["status"] == "completed"
    assert "未发现" in empty_refresh_runs[0]["message"]
    refresh_runs = intelligence.list_refresh_runs(
        identity,
        {"scopeId": seed["projectId"], "contentKind": "public_opinion"},
    )["runs"]
    assert len(refresh_runs) == 1
    assert refresh_runs[0]["status"] == "completed"
    assert refresh_runs[0]["clientId"] == seed["projectId"]
    assert refresh_runs[0]["triggerSource"] == "manual_intelligence_capture"
    listed = intelligence.list_items(
        identity,
        {"workObjectId": seed["projectId"], "page": 1, "pageSize": 20},
    )
    assert listed["total"] == 1
    assert all(item["id"] != reviewed["intelligenceId"] for item in listed["items"])
    visible_intelligence_id = public_capture["items"][0]["intelligenceId"]
    assert listed["items"][0]["id"] == visible_intelligence_id
    followed = intelligence.set_attention(
        identity,
        intelligence_id=visible_intelligence_id,
        action="follow",
        payload={"followMode": "same_theme"},
        idempotency_key="gc12-follow-one",
    )
    assert followed["userStatus"] == "following"
    refreshed_items = intelligence.list_items(
        identity,
        {"workObjectId": seed["projectId"]},
    )["items"]
    assert next(
        item for item in refreshed_items if item["id"] == visible_intelligence_id
    )["userStatus"] == "following"
    restored = intelligence.set_attention(
        identity,
        intelligence_id=visible_intelligence_id,
        action="restore",
        payload={"sentimentAction": "restore"},
        idempotency_key="gc12-restore-one",
    )
    assert restored["userStatus"] == "active"
    assert next(
        item
        for item in intelligence.list_items(identity, {"workObjectId": seed["projectId"]})["items"]
        if item["id"] == visible_intelligence_id
    )["userStatus"] == "active"
    focus = intelligence.upsert_rule(
        identity,
        rule_kind="focus",
        payload={
            "scopeType": "client",
            "scopeId": seed["projectId"],
            "profileCompletionFocus": ["治理结构"],
            "timelyIntelligenceFocus": ["负责人变化"],
            "exclude": ["无来源传闻"],
        },
        idempotency_key="gc12-focus-one",
    )
    assert focus["timelyIntelligenceFocus"] == ["负责人变化"]
    assert intelligence.list_focus_directives(identity)[0]["scopeId"] == seed["projectId"]
    cycle = intelligence.upsert_rule(
        identity,
        rule_kind="cycle",
        payload={"timelyIntelligenceHours": 12},
        idempotency_key="gc12-cycle-one",
    )
    assert cycle["timelyIntelligenceHours"] == 12
    assert intelligence.refresh_cycle_settings(identity)["state"] == "ready"
    verification = intelligence.upsert_rule(
        identity,
        rule_kind="verification",
        payload={
            "scopeType": "client",
            "scopeId": seed["projectId"],
            "positiveRules": ["优先采用官网原文"],
            "excludeRules": [],
            "identityAnchors": ["林佳维"],
        },
        idempotency_key="gc12-verification-one",
    )
    assert verification["identityAnchors"] == ["林佳维"]
    assert intelligence.list_verification_rules(identity)[0]["positiveRules"] == ["优先采用官网原文"]
    answer_receipt = intelligence.record_item_answer(
        identity,
        intelligence_id=visible_intelligence_id,
        payload={
            "questionHash": "1" * 64,
            "answerHash": "2" * 64,
            "providerResourceId": seed["providerResourceId"],
            "modelName": seed["modelName"],
            "threadId": "intelligence:gc12-test",
            "originInstanceId": "local-gc12-test",
        },
        idempotency_key="gc12-intelligence-answer-one",
    )
    assert answer_receipt["sourceCount"] == 1
    assert answer_receipt["boundaryState"] == "grounded"
    task = GC04TaskRepository(repository).create_task(
        identity,
        payload={
            "title": "跟进 GC12 官网事实",
            "clientId": seed["projectId"],
            "ownerMembershipId": identity.membership_id,
                "priority": "normal",
                "sourceType": "intelligence_record",
                "sourceId": visible_intelligence_id,
        },
        idempotency_key="gc12-intelligence-task-one",
    )["task"]
    assert intelligence.get_item(
        identity, intelligence_id=visible_intelligence_id
    )["convertedTaskId"] == task["id"]
    strategy = intelligence.strategy_extract(identity, project_id=seed["projectId"])["extract"]
    assert strategy["strategicObjective"] == "建立长期项目影响力"
    clarification = create_strategic_profile_clarification(
        repository,
        identity,
        project_id=seed["projectId"],
        payload={
            "dimension": "next_steps",
            "question": "项目当前战略主张是什么？",
            "answer": "以权威事实形成持续影响力",
            "basedOnRev": 1,
        },
        idempotency_key="gc12-strategy-clarification-one",
    )
    assert clarification["status"] == "applied"
    strategy = intelligence.strategy_extract(identity, project_id=seed["projectId"])["extract"]
    assert strategy["strategicObjective"] == "以权威事实形成持续影响力"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM intelligence_revisions "
            "WHERE intelligence_id=? AND reason='official_fact_verified'",
            (reviewed["intelligenceId"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM reconciliation_runs WHERE "
            "reconciliation_kind='project_knowledge_consumer_invalidation_v1' "
            "AND status='completed'",
        ).fetchone()[0] >= 2
        assert connection.execute(
            "SELECT COUNT(*) FROM source_set_members AS member "
            "JOIN source_sets AS sources ON sources.id=member.source_set_id "
            "AND sources.scope_id=member.scope_id "
            "WHERE sources.purpose_kind='intelligence_follow' "
            "AND member.source_object_id=? AND member.lifecycle_state='active'",
            (reviewed["intelligenceId"],),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT source_count FROM source_sets WHERE scope_id=? "
            "AND purpose_kind='intelligence_follow' "
            "AND created_by_principal_id=? AND client_id=?",
            (identity.scope_id, identity.principal_id, seed["projectId"]),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM automation_rules WHERE scope_id=? "
            "AND record_kind='automation' AND template_key LIKE ?",
            (identity.scope_id, f"{identity.principal_id}:intelligence-%"),
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM ai_answers WHERE id=? AND bot_id=? "
            "AND source_count=1 AND boundary_state='grounded'",
            (answer_receipt["answerId"], bot_id),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM intelligence_records AS intelligence "
            "JOIN object_manifests AS manifest ON manifest.id=intelligence.summary_object_manifest_id "
            "AND manifest.scope_id=intelligence.scope_id WHERE intelligence.scope_id=? "
            "AND json_extract(manifest.receipt,'$.contentKind')='public_opinion'",
            (identity.scope_id,),
        ).fetchone()[0] == 1
        assert len(user_tables(connection)) == 88


def test_gc12_same_public_url_can_serve_brand_and_timely_without_cross_stream_loss(
    tmp_path: Path,
) -> None:
    repository, identity, seed = gc14_repository(tmp_path / "gc12-streams")
    now = utc_now()
    bot_id = builtin_agent_id(identity.organization_id, "intelligence_research")
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
            "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES (?,?,'bot_definition','active',1,'builtin_agent',"
            "?,?,NULL,'cloud',?)",
            (bot_id, identity.scope_id, now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO bot_definitions (id,scope_id,agent_kind,version,handle,description,"
            "capability_policy_version,enabled,lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES (?,?,'intelligence_research',1,'intelligence-research','公开情报研究',"
            "'builtin-agent-contract-v1',1,'active',?,?,NULL)",
            (bot_id, identity.scope_id, now, now),
        )
        connection.commit()
    repository_under_test = GC12IntelligenceRepository(repository)
    shared_item = {
        "title": "儿童心理健康项目观察",
        "summary": "公开来源讨论儿童心理健康项目的发展机会。",
        "sourceName": "research.example",
        "sourceUrl": "https://research.example/child-mental-health",
        "capturedAt": now,
        "sentiment": "neutral",
        "sentimentReason": "中性行业信息",
        "contentHash": "a" * 64,
        "relevanceReason": "与项目所处议题相关",
        "impact": "可用于判断资助和合作机会",
        "tags": ["儿童心理", "行业机会"],
    }
    public = repository_under_test.commit_external_capture(
        identity,
        project_id=seed["projectId"],
        capture_id="brand-capture",
        content_kind="public_opinion",
        capture_kind="manual_intelligence",
        items=[{"clientItemKey": "brand:0", **shared_item}],
        research_receipt={"queryCount": 8, "modelAnalysisExecuted": True},
        idempotency_key="brand-capture-once",
    )
    timely = repository_under_test.commit_external_capture(
        identity,
        project_id=seed["projectId"],
        capture_id="timely-capture",
        content_kind="timely_intelligence",
        capture_kind="manual_intelligence",
        items=[{"clientItemKey": "timely:0", **shared_item}],
        research_receipt={
            "queryCount": 10,
            "modelAnalysisExecuted": True,
            "directMentionPolicy": "exclude",
        },
        idempotency_key="timely-capture-once",
    )
    assert public["insertedCount"] == 1
    assert timely["insertedCount"] == 1
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM intelligence_records WHERE scope_id=? AND client_id=?",
            (identity.scope_id, seed["projectId"]),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM source_assets WHERE scope_id=? AND client_id=? "
            "AND source_kind='public_web'",
            (identity.scope_id, seed["projectId"]),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs WHERE scope_id=? "
            "AND run_kind IN ('public_opinion_research','timely_intelligence_research')",
            (identity.scope_id,),
        ).fetchone()[0] == 2
        assert len(user_tables(connection)) == 88


def _seed_local_gc08(database: Path) -> tuple[GC08LocalContext, dict[str, str]]:
    identity = initialize_database(database, "local")
    now = utc_now()
    values = {
        "organizationId": "org_gc08_local",
        "scopeId": "scope_gc08_local",
        "principalId": "principal_gc08_local",
        "membershipId": "membership_gc08_local",
        "sandboxId": "sandbox_gc08_local",
        "clientId": "client_gc08_local",
        "meetingId": "meeting_gc08_local",
    }
    with runtime_connection(database, "local") as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,"
            "record_kind,name,created_at,deleted_at,projection_state,projected_at) "
            "VALUES (?,'active',1,?,'organization','GC08本机组织',?,NULL,'current',?)",
            (values["organizationId"], now, now, now),
        )
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,"
            "principal_kind,display_name,version,lifecycle_state,created_at,deleted_at,"
            "projection_state,projected_at) VALUES (?,'active',1,?,'person',"
            "'GC08成员',1,'active',?,NULL,'current',?)",
            (values["principalId"], now, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,"
            "policy_version,created_at,updated_at,status,version,lifecycle_state,"
            "deleted_at,projection_state,projected_at) VALUES (?,'organization',?,1,"
            "?,?,'active',1,'active',NULL,'current',?)",
            (values["scopeId"], values["organizationId"], now, now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
            "status,version,record_kind,visibility_scope,lifecycle_state,created_at,"
            "updated_at,deleted_at,projection_state,projected_at) VALUES (?,?,?,'admin',"
            "'active',1,'membership','organization','active',?,?,NULL,'current',?)",
            (
                values["membershipId"],
                values["scopeId"],
                values["principalId"],
                now,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO sandboxes (id,scope_id,principal_id,membership_id,record_kind,"
            "cloud_instance_id,database_generation_id,sandbox_kind,display_name,"
            "runtime_status,manifest_hash,version,lifecycle_state,created_at,updated_at,"
            "deleted_at,authority_role,origin_instance_id) VALUES (?,?,?,?,'sandbox',"
            "'cli_gc08_local',?,'organization','GC08工作空间','ready',?,1,'active',"
            "?,?,NULL,'local',?)",
            (
                values["sandboxId"],
                values["scopeId"],
                values["principalId"],
                values["membershipId"],
                identity.database_generation_id,
                identity.manifest_hash,
                now,
                now,
                identity.database_generation_id,
            ),
        )
        for resource_id, kind, type_key in (
            (values["clientId"], "client", "client"),
            (values["meetingId"], "meeting", "meeting"),
        ):
            connection.execute(
                "INSERT INTO secured_resources (id,scope_id,resource_kind,"
                "lifecycle_state,version,resource_type_key,created_at,updated_at,"
                "deleted_at,authority_role,origin_instance_id) VALUES (?,?,?,"
                "'active',1,?,?,?,NULL,'local',?)",
                (
                    resource_id,
                    values["scopeId"],
                    kind,
                    type_key,
                    now,
                    now,
                    identity.database_generation_id,
                ),
            )
        connection.execute(
            "INSERT INTO clients (id,scope_id,owner_membership_id,lifecycle_state,"
            "version,name,created_at,updated_at,deleted_at,sandbox_id,source_version,"
            "projection_state,projected_at) VALUES (?,?,?,'active',1,'GC08项目',"
            "?,?,NULL,?,1,'current',?)",
            (
                values["clientId"],
                values["scopeId"],
                values["membershipId"],
                now,
                now,
                values["sandboxId"],
                now,
            ),
        )
        connection.execute(
            "INSERT INTO meetings (id,scope_id,client_id,event_line_id,"
            "lifecycle_state,title,agenda,starts_at,ends_at,timezone,"
            "organizer_membership_id,visibility_scope,status,version,created_at,"
            "updated_at,deleted_at,sandbox_id,source_version,projection_state,"
            "projected_at) VALUES (?,?,?,NULL,'active','GC08测试会议',NULL,?,?,"
            "'Asia/Shanghai',?,'project','scheduled',1,?,?,NULL,?,1,'current',?)",
            (
                values["meetingId"],
                values["scopeId"],
                values["clientId"],
                "2026-08-07T09:00:00Z",
                "2026-08-07T10:00:00Z",
                values["membershipId"],
                now,
                now,
                values["sandboxId"],
                now,
            ),
        )
        connection.commit()
    context = GC08LocalContext(
        scope_id=values["scopeId"],
        sandbox_id=values["sandboxId"],
        principal_id=values["principalId"],
        membership_id=values["membershipId"],
        origin_instance_id=identity.database_generation_id,
    )
    return context, values


def _seed_cloud_meeting(repository: object, identity: object, values: dict) -> None:
    now = utc_now()
    meeting_id = values["meetingId"]
    project_id = values["projectId"]
    bot_id = builtin_agent_id(identity.organization_id, "meeting_minutes")
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
            "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES (?,?,'bot_definition','active',1,"
            "'builtin_function_agent',?,?,NULL,'cloud',?)",
            (bot_id, identity.scope_id, now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO bot_definitions (id,scope_id,agent_kind,version,handle,"
            "description,enabled,lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,1,'meeting-minutes','会议纪要',1,'active',?,?,NULL)",
            (bot_id, identity.scope_id, "meeting_minutes", now, now),
        )
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,"
            "lifecycle_state,version,resource_type_key,created_at,updated_at,"
            "deleted_at,authority_role,origin_instance_id) VALUES (?,?,"
            "'meeting','active',1,'meeting',?,?,NULL,'cloud',?)",
            (meeting_id, identity.scope_id, now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO meetings (id,scope_id,client_id,event_line_id,"
            "lifecycle_state,title,agenda,starts_at,ends_at,timezone,"
            "organizer_membership_id,visibility_scope,status,version,created_at,"
            "updated_at,deleted_at) VALUES (?,?,?,NULL,'active','GC08云端会议',NULL,"
            "?,?,'Asia/Shanghai',?,'project','scheduled',1,?,?,NULL)",
            (
                meeting_id,
                identity.scope_id,
                project_id,
                "2026-08-07T09:00:00Z",
                "2026-08-07T10:00:00Z",
                identity.membership_id,
                now,
                now,
            ),
        )
        connection.commit()


def test_gc08_local_retry_draft_cloud_publish_and_evidence_boundary(
    tmp_path: Path,
) -> None:
    local_database = tmp_path / "local" / "strict-local.db"
    context, local = _seed_local_gc08(local_database)
    recordings_root = local_database.parent / "recordings"
    recordings_root.mkdir()
    audio = recordings_root / "gc08-session.m4a"
    audio.write_bytes(b"GC08_AUDIO_LOCAL_ONLY")

    blocked_store = GC08LocalMeetingRepository(
        local_database,
        recordings_root,
        lambda: context,
    )
    registered = blocked_store.register_recording(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        audio_path=audio,
        recording_id="recording_gc08_test",
    )
    assert registered["recordingState"] == "captured"
    assert registered["localFiles"]["recordingPath"] == str(audio)
    assert registered["localFiles"]["transcriptionPath"] is None
    renamed_audio = recordings_root / "会议录音已重命名.m4a"
    audio.rename(renamed_audio)
    recovered = blocked_store.recording_detail(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
    )
    assert recovered["localFiles"]["recordingPath"] == str(renamed_audio)
    with runtime_connection(local_database, "local", read_only=True) as connection:
        assert connection.execute(
            "SELECT local_original_path FROM object_manifests WHERE id=("
            "SELECT object_manifest_id FROM recordings WHERE id=?)",
            ("recording_gc08_test",),
        ).fetchone()[0] == str(renamed_audio)
    blocked = blocked_store.transcribe(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
    )
    assert blocked["transcription"]["status"] == "blocked"
    assert blocked["transcription"]["errorCode"] == "local_asr_not_connected"

    empty_store = GC08LocalMeetingRepository(
        local_database,
        recordings_root,
        lambda: context,
        transcription_runner=lambda _path, _language, _progress=None: {"text": ""},
    )
    empty = empty_store.transcribe(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
        force=True,
    )
    assert empty["transcription"]["status"] == "failed_retryable"
    assert empty["transcription"]["errorCode"] == "local_asr_empty_result"

    full_transcript = "FULL_TRANSCRIPT_LOCAL_ONLY：讨论完成，形成可核对结论。"
    ready_store = GC08LocalMeetingRepository(
        local_database,
        recordings_root,
        lambda: context,
        transcription_runner=lambda _path, _language, _progress=None: {
            "text": full_transcript,
            "language": "zh",
            "duration_ms": 42000,
        },
    )
    ready = ready_store.transcribe(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
        force=True,
    )
    assert ready["transcription"]["status"] == "ready"
    assert ready["transcription"]["version"] == 1
    first_transcript = Path(ready["localFiles"]["transcriptionPath"])
    assert first_transcript.is_file()
    assert first_transcript.name == "gc08-session-录音转写.txt"
    first_transcript.unlink()
    missing_transcript = ready_store.recording_detail(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
    )
    assert missing_transcript["localFiles"]["transcriptionPath"] is None
    regenerated = ready_store.transcribe(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
        force=True,
    )
    assert regenerated["transcription"]["version"] == 2
    assert Path(regenerated["localFiles"]["transcriptionPath"]).is_file()

    formal_minutes = "# GC08正式纪要\n\n结论：形成安全业务版本。"
    draft = ready_store.create_minutes_draft(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
        minutes_markdown=formal_minutes,
        citations=[
            {
                "locatorKind": "time_range",
                "locator": "time_ms:0-42000",
            }
        ],
    )
    assert draft["minutes"]["publicationState"] == "draft"
    assert draft["minutes"]["minutesMarkdown"] == formal_minutes
    assert draft["downstreamAdapters"]["taskCommand"]["state"] == (
        "waiting_for_formal_command"
    )

    cloud_payload = ready_store.cloud_publication_payload(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
    )
    serialized_payload = json.dumps(cloud_payload, ensure_ascii=False)
    assert full_transcript not in serialized_payload
    assert str(audio) not in serialized_payload
    assert str(renamed_audio) not in serialized_payload
    assert "audioPath" not in serialized_payload
    assert "localFiles" not in serialized_payload
    assert cloud_payload["minutes"]["minutesMarkdown"] == formal_minutes

    renamed_audio.unlink()
    missing_recording = ready_store.recording_detail(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
    )
    assert missing_recording["localFiles"]["recordingPath"] is None

    cloud_repository, cloud_identity, seed = gc14_repository(tmp_path / "cloud")
    cloud_values = {
        "projectId": seed["projectId"],
        "meetingId": local["meetingId"],
    }
    _seed_cloud_meeting(cloud_repository, cloud_identity, cloud_values)
    published_payload = {
        **cloud_payload,
        "minutes": {
            **cloud_payload["minutes"],
            "documentId": "meeting_minutes_gc08_cloud",
            "actionCandidates": [
                {
                    "title": "确认教师培训下一步",
                    "description": "由正式会议纪要提取，仍待用户确认",
                    "dueDate": "2026-08-14",
                    "ownerHint": "GC08测试成员",
                }
            ],
        },
    }
    published_payload["minutes"]["contentHash"] = sha256_text(formal_minutes)
    cloud_store = GC08MeetingMinutesRepository(cloud_repository)
    published = cloud_store.publish_minutes(
        cloud_identity,
        project_id=seed["projectId"],
        meeting_id=local["meetingId"],
        payload=published_payload,
        idempotency_key="gc08-publish-once",
    )
    replay = cloud_store.publish_minutes(
        cloud_identity,
        project_id=seed["projectId"],
        meeting_id=local["meetingId"],
        payload=published_payload,
        idempotency_key="gc08-publish-once",
    )
    assert published["publicationState"] == "published"
    assert len(published["actionCandidateIds"]) == 1
    assert replay["idempotentReplay"] is True
    with pytest.raises(RepositoryError) as boundary_error:
        cloud_store.publish_minutes(
            cloud_identity,
            project_id=seed["projectId"],
            meeting_id=local["meetingId"],
            payload={**published_payload, "transcript": full_transcript},
            idempotency_key="gc08-forbidden-transcript",
        )
    assert boundary_error.value.code == "gc08_local_material_forbidden"

    with runtime_connection(cloud_repository.database_path, "cloud") as connection:
        assert len(user_tables(connection)) == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM recordings").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM transcription_versions"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_documents WHERE document_kind='meeting_minutes' "
            "AND publication_state='published'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM evidence_links").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM source_sets").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM source_set_members").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM derivation_lineage").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE command_type='meeting_minutes.published'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='meeting_minutes.published'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM event_lines").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM ai_proposals WHERE operation_kind='meeting_action_candidate' "
            "AND status='pending_confirmation'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs WHERE run_kind='formal_meeting_minutes'"
        ).fetchone()[0] == 1
    raw_cloud = cloud_repository.database_path.read_bytes()
    assert b"GC08_AUDIO_LOCAL_ONLY" not in raw_cloud
    assert "FULL_TRANSCRIPT_LOCAL_ONLY".encode() not in raw_cloud
    assert str(audio).encode() not in raw_cloud
    assert "GC08正式纪要".encode() in raw_cloud

    local_published = ready_store.record_cloud_publication(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
        cloud_version=int(published["version"]),
        cloud_instance_id=cloud_identity.cloud_instance_id,
    )
    assert local_published["minutes"]["publicationState"] == "published"
    local_replay = ready_store.record_cloud_publication(
        client_id=local["clientId"],
        meeting_id=local["meetingId"],
        recording_id="recording_gc08_test",
        cloud_version=int(published["version"]),
        cloud_instance_id=cloud_identity.cloud_instance_id,
    )
    assert local_replay["minutes"]["version"] == local_published["minutes"]["version"]
    with runtime_connection(local_database, "local") as connection:
        assert len(user_tables(connection)) == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        attempts = connection.execute(
            "SELECT attempt_no,status,error_code FROM processing_attempts "
            "WHERE recording_id='recording_gc08_test' "
            "AND processor_kind='local_audio_transcription' ORDER BY attempt_no"
        ).fetchall()
        assert [tuple(row) for row in attempts] == [
            (1, "blocked", "local_asr_not_connected"),
            (2, "failed_retryable", "local_asr_empty_result"),
            (3, "ready", None),
            (4, "ready", None),
        ]
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM event_lines").fetchone()[0] == 0
