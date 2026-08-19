from __future__ import annotations

import hashlib
import json
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from backend.app.intelligence_capture_local import PublicCaptureItem
from backend.app.project_materials_local import LocalProjectMaterialsRepository
from backend.app.runtime import LocalRuntimeError, WorkspaceRuntime
from backend.app.secret_store import MemorySecretStore
from backend.app.ui_domains import UiRequest, build_default_registry
from backend.app.ui_domains import project_materials as project_materials_ui
from backend.app.ui_domains import workbench_outputs
from backend.app.ui_domains.workbench_outputs import (
    _answer_export_title,
    _explicit_project_memory_statement,
    _published_meeting_payload,
)
from backend.app.workbench_chat_local import (
    LocalWorkbenchChatRepository,
    _load_optional_project_knowledge,
)
from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from cloud_backend.app.repositories.workbench_outputs import (
    _profile_updates_from_correction_rows,
)
from cloud_backend.app.repositories.project_materials import (
    GC07ProjectMaterialsRepository,
)
from cloud_backend.app.repositories.gc15_official_website import (
    capture_official_website as commit_official_website,
    official_fact_candidates,
    official_website_status,
    review_official_fact_candidate,
)
from cloud_backend.app.repository import SessionIdentity
from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import new_id, utc_now
from strict_common.schema import initialize_database, runtime_connection


ROOT = Path(__file__).resolve().parents[1]


def test_answer_export_title_uses_fast_ai_and_has_question_fallback() -> None:
    class Runtime:
        def private_ai_completion(self, **_: object) -> dict[str, str]:
            return {"content": "《日慈基金会定位与发展》"}

    answers = [
        {
            "question": "请为我提炼这份文件的核心内容？",
            "answerMarkdown": "# 日慈是谁\n\n日慈基金会关注儿童心理健康。",
        }
    ]
    assert _answer_export_title(
        SimpleNamespace(runtime=Runtime()), answers
    ) == "日慈基金会定位与发展"

    class FailedRuntime:
        def private_ai_completion(self, **_: object) -> dict[str, str]:
            raise RuntimeError("model unavailable")

    assert _answer_export_title(
        SimpleNamespace(runtime=FailedRuntime()), answers
    ) == "提炼这份文件的核心内容"


def _grant_project_access(connection, identity: SessionIdentity, project_id: str, now: str) -> None:
    policy_id = f"policy_{project_id}"
    grant_id = f"grant_{project_id}_{identity.membership_id}"
    connection.execute(
        "INSERT INTO policy_versions (id,scope_id,secured_resource_id,policy_scope_kind,"
        "version,policy_spec_schema_version,policy_spec,effective_at,created_at,"
        "lifecycle_state,updated_at,deleted_at) VALUES (?,?,?,'secured_resource',1,"
        "'gc02.client-access.v1','{\"defaultDecision\":\"deny\"}',?,?,'active',?,NULL)",
        (policy_id, identity.scope_id, project_id, now, now, now),
    )
    connection.execute(
        "INSERT INTO object_grants (id,scope_id,secured_resource_id,policy_version_id,"
        "subject_principal_id,subject_membership_id,capability_set_schema_version,"
        "capability_set,grant_generation,status,grant_source_set_id,created_at,updated_at,"
        "revoked_at,version,lifecycle_state,deleted_at) VALUES (?,?,?, ?,NULL,?,'1',"
        "'{\"read\":true,\"write\":true,\"contributeKnowledge\":true}',1,'active',NULL,"
        "?,?,NULL,1,'active',NULL)",
        (grant_id, identity.scope_id, project_id, policy_id, identity.membership_id, now, now),
    )


def test_explicit_project_memory_requires_an_imperative_with_a_concrete_fact() -> None:
    assert _explicit_project_memory_statement(
        "请记住：张真是日慈公益基金会秘书长。"
    ) == "张真是日慈公益基金会秘书长"
    assert _explicit_project_memory_statement(
        "还有一点，帮我记住心盛计划服务流动儿童。"
    ) == "心盛计划服务流动儿童"
    assert _explicit_project_memory_statement(
        "请你记住：该项目不上传成员文件正文。"
    ) == "该项目不上传成员文件正文"
    assert _explicit_project_memory_statement("把官网作为优先事实来源记下来。") == "官网作为优先事实来源"
    assert _explicit_project_memory_statement("我发现你记住的身份不对") is None
    assert _explicit_project_memory_statement("不要记住这段测试文字") is None
    assert _explicit_project_memory_statement("你记住了吗？") is None


def test_official_fact_policy_keeps_person_profile_and_rejects_web_noise() -> None:
    page = SimpleNamespace(
        title="关于我们",
        url="https://example.org/about",
        text=(
            "顾源源是首席战略专家，颗粒公益传播发展中心创始人，"
            "长期深耕公益品牌建设。网站最后更新时间为2026年，版权年份为2026。"
        ),
        page_role="institutional_profile",
        capture_kind="rendered",
        canonical_public_url="https://example.org/about",
    )
    parsed = workbench_outputs._parse_official_fact_response(
        json.dumps(
            {
                "facts": [
                    {
                        "term": "顾源源",
                        "attributeName": "从业经历",
                        "valueCategory": "person",
                        "valueText": "颗粒公益传播发展中心创始人",
                        "evidence": "顾源源是首席战略专家，颗粒公益传播发展中心创始人，长期深耕公益品牌建设。",
                        "sourceUrl": "https://example.org/about",
                        "subjectKind": "person",
                        "factKind": "person_profile",
                        "businessRelevance": True,
                        "confidence": 0.9,
                    },
                    {
                        "term": "官网",
                        "attributeName": "版权年份",
                        "valueCategory": "date",
                        "valueText": "2026",
                        "evidence": "版权年份为2026。",
                        "sourceUrl": "https://example.org/about",
                        "subjectKind": "client",
                        "factKind": "milestone",
                        "businessRelevance": True,
                        "confidence": 0.9,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        pages=[page],
    )
    assert len(parsed) == 1
    assert parsed[0]["factKind"] == "person_profile"
    assert parsed[0]["sourcePublicUrl"] == "https://example.org/about"


def test_optional_project_knowledge_failure_is_explicit_and_non_blocking() -> None:
    class FailingRuntime:
        @staticmethod
        def project_knowledge_context(_project_id: str) -> dict:
            raise LocalRuntimeError(502, "cloud_response_invalid", "上游响应异常")

    knowledge, state, message = _load_optional_project_knowledge(
        FailingRuntime(),  # type: ignore[arg-type]
        "client-a",
    )
    assert knowledge == {}
    assert state == "failed_retryable"
    assert message == "组织知识读取失败，可稍后重试"


def _cloud(tmp_path: Path) -> tuple[TestClient, Path]:
    database = tmp_path / "strict-cloud.db"
    initialize_database(database, "cloud")
    now = utc_now()
    with runtime_connection(database, "cloud") as connection:
        connection.execute(
            "INSERT INTO state_registry "
            "(id,state_id,target_blueprint_node,version,record_kind,"
            "lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES ('cli_workbench_test','cli_workbench_test','cloud_instance',"
            "1,'cloud_instance','active',?,?,NULL)",
            (now, now),
        )
        connection.commit()
    config = CloudConfig(
        data_dir=tmp_path,
        database_path=database,
        bootstrap_token="bootstrap-test",
        master_key=Fernet.generate_key().decode(),
        cloud_instance_id="cli_workbench_test",
    )
    return TestClient(create_app(config)), database


def _bootstrap(client: TestClient) -> tuple[dict, dict[str, str]]:
    response = client.post(
        "/api/v2/auth/bootstrap-organization",
        json={
            "organizationName": "工作台严格测试组织",
            "displayName": "工作台管理员",
            "email": "workbench-admin@example.com",
            "password": "12345678",
            "bootstrapToken": "bootstrap-test",
        },
    )
    assert response.status_code == 201, response.text
    identity = response.json()
    return identity, {"Authorization": f"Bearer {identity['accessToken']}"}


def _project_id(client: TestClient, headers: dict[str, str]) -> str:
    snapshot = client.get("/api/v2/business/snapshot", headers=headers)
    assert snapshot.status_code == 200, snapshot.text
    return str(snapshot.json()["projects"][0]["projectId"])


def test_gc12_strategic_profile_route_is_registered(tmp_path: Path) -> None:
    client, _ = _cloud(tmp_path)
    registered = {
        (method, route.path)
        for route in client.app.routes
        for method in getattr(route, "methods", set())
    }
    assert (
        "GET",
        "/api/v2/workbench/projects/{project_id}/narrative",
    ) in registered


def test_gc07_project_update_and_gc15_official_paths_are_connected(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    repository = client.app.state.repository
    identity = SessionIdentity(
        session_id="session_gc07_update",
        principal_id="principal_gc07_update",
        membership_id="membership_gc07_update",
        organization_id="org_gc07_update",
        cloud_instance_id="cli_workbench_test",
        scope_id="scope_gc07_update",
        system_role="admin",
        visibility_scope="organization",
        display_name="项目测试管理员",
    )
    project_id = "client_gc07_update"
    now = utc_now()
    with runtime_connection(database, "cloud") as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,record_kind,name,created_at,deleted_at) "
            "VALUES (?,'active',1,?,'organization','项目测试组织',?,NULL)",
            (identity.organization_id, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,policy_version,created_at,updated_at,status,version,lifecycle_state,deleted_at) "
            "VALUES (?,'organization',?,1,?,?,'active',1,'active',NULL)",
            (identity.scope_id, identity.organization_id, now, now),
        )
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,principal_kind,display_name,version,lifecycle_state,created_at,deleted_at) "
            "VALUES (?,'active',1,?,'person','项目测试管理员',1,'active',?,NULL)",
            (identity.principal_id, now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,version,record_kind,visibility_scope,lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,'admin','active',1,'membership','organization','active',?,?,NULL)",
            (identity.membership_id, identity.scope_id, identity.principal_id, now, now),
        )
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
            "VALUES (?,?,'client','active',1,'client',?,?,NULL,'cloud',?)",
            (project_id, identity.scope_id, now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO clients (id,scope_id,owner_membership_id,lifecycle_state,version,name,alias,summary,domain,color,visibility_scope,is_default_internal,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,'active',1,'日慈基金会','','','项目','#5B7BFE','organization',0,?,?,NULL)",
            (project_id, identity.scope_id, identity.membership_id, now, now),
        )
        _grant_project_access(connection, identity, project_id, now)
        connection.commit()
    updated = GC07ProjectMaterialsRepository(repository).update_project(
        identity,
        project_id=project_id,
        payload={"alias": "日慈", "expectedVersion": 1},
        idempotency_key="gc15-project-update",
    )
    assert updated["project"]["alias"] == "日慈"
    assert updated["project"]["version"] == 2
    registered = {
        (method, route.path)
        for route in client.app.routes
        for method in getattr(route, "methods", set())
    }
    assert (
        "PUT",
        "/api/v2/domain/project-materials/projects/{project_id}",
    ) in registered

    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "PUT",
        f"/api/v2/domain/project-materials/projects/{project_id}",
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "GET",
        f"/api/v2/workbench/projects/{project_id}/official-website",
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST",
        f"/api/v2/workbench/projects/{project_id}/official-website/captures",
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "GET",
        f"/api/v2/workbench/projects/{project_id}/reports",
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST",
        "/api/v2/workbench/reports",
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "GET",
        "/api/v2/workbench/reports/report_gc09/versions",
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "PATCH",
        "/api/v2/workbench/reports/report_gc09",
    )
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST",
        "/api/v2/workbench/reports/report_gc09/restore",
    )


def test_gc15_official_website_builds_strict_knowledge_and_agent_receipt(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    repository = client.app.state.repository
    now = utc_now()
    project_id = "client_gc15_official"
    identity = SessionIdentity(
        session_id="session_gc15",
        principal_id="principal_gc15",
        membership_id="membership_gc15",
        organization_id="org_gc15",
        cloud_instance_id="cli_workbench_test",
        scope_id="scope_gc15",
        system_role="admin",
        visibility_scope="organization",
        display_name="官网测试管理员",
    )
    bot_id = builtin_agent_id(identity.organization_id, "intelligence_research")
    with runtime_connection(database, "cloud") as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,record_kind,name,created_at,deleted_at) "
            "VALUES (?,'active',1,?,'organization','官网测试组织',?,NULL)",
            (identity.organization_id, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,policy_version,created_at,updated_at,status,version,lifecycle_state,deleted_at) "
            "VALUES (?,'organization',?,1,?,?,'active',1,'active',NULL)",
            (identity.scope_id, identity.organization_id, now, now),
        )
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,principal_kind,display_name,version,lifecycle_state,created_at,deleted_at) "
            "VALUES (?,'active',1,?,'person','官网测试管理员',1,'active',?,NULL)",
            (identity.principal_id, now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,version,record_kind,visibility_scope,lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,'admin','active',1,'membership','organization','active',?,?,NULL)",
            (identity.membership_id, identity.scope_id, identity.principal_id, now, now),
        )
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
            "VALUES (?,?,'client','active',1,'project',?,?,NULL,'cloud',?)",
            (project_id, identity.scope_id, now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO clients (id,scope_id,owner_membership_id,lifecycle_state,version,name,alias,summary,domain,color,visibility_scope,is_default_internal,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,'active',1,'日慈基金会','日慈','测试项目','项目','#5B7BFE','organization',0,?,?,NULL)",
            (project_id, identity.scope_id, identity.membership_id, now, now),
        )
        _grant_project_access(connection, identity, project_id, now)
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
            "VALUES (?,?,'bot_definition','active',1,'builtin_agent',?,?,NULL,'cloud',?)",
            (bot_id, identity.scope_id, now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO bot_definitions (id,scope_id,agent_kind,version,handle,description,capability_policy_version,enabled,lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES (?,?,'intelligence_research',1,'intelligence-research','官网情报研究','builtin-agent-contract-v1',1,'active',?,?,NULL)",
            (bot_id, identity.scope_id, now, now),
        )
        connection.commit()
    pages = [
            {
                "title": "日慈公益基金会",
                "url": "https://www.ricifoundation.com/",
                "text": "日慈公益基金会致力于儿童心智素养发展。",
                "contentHash": "1" * 64,
                "capturedAt": "2026-08-06T08:00:00.000Z",
                "canonicalPublicUrl": "https://www.ricifoundation.com/",
                "pageRole": "institutional_profile",
                "captureKind": "rendered",
            },
            {
                "title": "心灵魔法学院",
                "url": "https://www.ricifoundation.com/home/project/detail/project_id/1.html",
                "text": "心灵魔法学院面向儿童提供社会情感学习支持。",
                "contentHash": "2" * 64,
                "capturedAt": "2026-08-06T08:00:00.000Z",
                "canonicalPublicUrl": "https://www.ricifoundation.com/home/project/detail/project_id/1.html",
                "pageRole": "project_service",
                "captureKind": "rendered",
            },
        ]
    captured = commit_official_website(
            repository,
            identity,
            project_id=project_id,
            pages=pages,
            fact_candidates=[
                {
                    "term": "日慈公益基金会",
                    "attributeName": "关注领域",
                    "valueCategory": "text",
                    "valueText": "儿童心智素养发展",
                    "evidence": "日慈公益基金会致力于儿童心智素养发展。",
                    "sourceUrl": "https://www.ricifoundation.com/",
                    "sourceTitle": "日慈公益基金会",
                    "subjectKind": "client",
                    "factKind": "organization_profile",
                    "confidence": 0.92,
                }
            ],
            idempotency_key="gc15-official-site-1",
        )
    assert captured["pageCount"] == 2
    assert captured["processingAgentKind"] == "intelligence_research"
    assert captured["candidateCount"] == 0

    assert official_fact_candidates(
        repository,
        identity,
        project_id=project_id,
        status="pending",
    )["attributes"] == []
    verified_facts = official_fact_candidates(
        repository,
        identity,
        project_id=project_id,
        status="verified",
    )["attributes"]
    assert len(verified_facts) == 1
    assert verified_facts[0]["source_doc_path"] == "https://www.ricifoundation.com/"
    reviewed = review_official_fact_candidate(
        repository,
        identity,
        project_id=project_id,
        fact_id=verified_facts[0]["id"],
        review_status="verified",
        payload={},
        idempotency_key="gc15-official-fact-review-1",
    )
    assert reviewed["status"] == "verified"
    assert len(official_fact_candidates(
        repository,
        identity,
        project_id=project_id,
        status="verified",
    )["attributes"]) == 1

    status = official_website_status(
            repository,
            identity,
            project_id=project_id,
        )
    assert status["registeredUrl"] == "https://www.ricifoundation.com/"
    assert status["pageCount"] == 2

    context = repository.project_knowledge_context(
            identity,
            project_id=project_id,
        )
    website_facts = context["officialWebsiteFacts"]
    assert len(website_facts) == 3
    assert {item["sourceUrl"] for item in website_facts} == {
        item["url"] for item in pages
    }

    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM source_assets WHERE client_id=? "
            "AND source_kind='official_website'",
            (project_id,),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs AS run "
            "JOIN bot_definitions AS bot ON bot.id=run.bot_id "
            "WHERE bot.agent_kind='intelligence_research' "
            "AND run.run_kind='official_website_capture' AND run.status='completed'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM relationship_triples WHERE predicate='官网介绍'"
        ).fetchone()[0] == 1


def _seed_report_and_processing(
    database: Path,
    *,
    organization_id: str,
    membership_id: str,
    project_id: str,
    document_id: str,
) -> tuple[str, str]:
    report_id = new_id()
    report_version_id = new_id()
    attempt_id = new_id()
    now = utc_now()
    with runtime_connection(database, "cloud") as connection:
        document_version = connection.execute(
            """
            SELECT document_version_id
            FROM document_versions
            WHERE organization_id = ? AND document_id = ? AND version = 1
            """,
            (organization_id, document_id),
        ).fetchone()
        assert document_version is not None
        connection.execute(
            """
            INSERT INTO narrative_outputs (
                narrative_output_id, organization_id, project_id,
                event_line_id, output_kind, title, lifecycle_state,
                latest_version, created_by_membership_id, version,
                created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, NULL, 'strategy_report', ?, 'active',
                      1, ?, 1, ?, ?, NULL)
            """,
            (
                report_id,
                organization_id,
                project_id,
                "严格测试报告",
                membership_id,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO narrative_output_versions (
                narrative_output_version_id, organization_id,
                narrative_output_id, version, content_markdown,
                content_json, input_fingerprint, content_hash,
                change_summary, created_by_membership_id, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, 'fixture-v1', ?, '初版', ?, ?)
            """,
            (
                report_version_id,
                organization_id,
                report_id,
                "# 严格报告 v1",
                json.dumps(
                    {
                        "generator": "strict-test",
                        "overallConfidence": 0.88,
                        "dimensions": [
                            {
                                "dimension": "essence",
                                "narrative": "严格报告 v1",
                                "confidence": "high",
                                "confidenceReason": "测试权威版本",
                                "references": [],
                                "dataLayerGap": "",
                                "openClarifications": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                "report-content-hash-v1",
                membership_id,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO processing_attempts (
                processing_attempt_id, organization_id, source_asset_id,
                document_id, processing_kind, state, attempt_no,
                error_code, error_message, started_at, finished_at, created_at
            ) VALUES (?, ?, NULL, ?, 'analysis_summary', 'completed', 1,
                      '', '', ?, ?, ?)
            """,
            (attempt_id, organization_id, document_id, now, now, now),
        )
        connection.execute(
            """
            INSERT INTO evidence_links (
                evidence_link_id, organization_id, source_type, source_id,
                target_type, target_id, relation_kind, lifecycle_state,
                linked_by_membership_id, version, created_at, updated_at
            ) VALUES (?, ?, 'document_version', ?, 'narrative_output', ?,
                      'supports', 'active', ?, 1, ?, ?)
            """,
            (
                new_id(),
                organization_id,
                document_version["document_version_id"],
                report_id,
                membership_id,
                now,
                now,
            ),
        )
        connection.commit()
    return report_id, attempt_id


def test_workbench_authorities_cas_idempotency_and_derived_views(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        identity, headers = _bootstrap(client)
        snapshot = client.get("/api/v2/business/snapshot", headers=headers)
        assert snapshot.status_code == 200, snapshot.text
        project_id = snapshot.json()["projects"][0]["projectId"]

        answer = client.post(
            "/api/v2/workbench/answers",
            headers={**headers, "Idempotency-Key": "answer-1"},
            json={
                "projectId": project_id,
                "question": "这个项目现在最重要的事情是什么？",
                "answerMarkdown": "先完成严格新版资料核验。",
                "sourceManifest": {
                    "projectId": project_id,
                    "documentIds": [],
                    "documentContentIncluded": False,
                },
                "modelName": "strict-test-model",
            },
        )
        assert answer.status_code == 201, answer.text
        answer_id = answer.json()["answer"]["answerId"]

        favorite_payload = {
            "targetType": "ai_answer",
            "targetId": answer_id,
            "title": "关键回答",
            "expectedVersion": 1,
        }
        favorite = client.post(
            "/api/v2/workbench/favorites",
            headers={**headers, "Idempotency-Key": "favorite-1"},
            json=favorite_payload,
        )
        repeated_favorite = client.post(
            "/api/v2/workbench/favorites",
            headers={**headers, "Idempotency-Key": "favorite-1"},
            json=favorite_payload,
        )
        assert favorite.status_code == 201, favorite.text
        assert repeated_favorite.status_code == 201, repeated_favorite.text
        assert repeated_favorite.json() == favorite.json()

        dna = client.put(
            f"/api/v2/workbench/projects/{project_id}/dna/organization_intro",
            headers={**headers, "Idempotency-Key": "dna-create-1"},
            json={
                "markdownContent": "# 组织介绍\n使命是推动公益组织数字化。",
                "fileName": "organization.md",
                "expectedVersion": 0,
            },
        )
        assert dna.status_code == 200, dna.text
        assert dna.json()["version"] == 1
        document_id = dna.json()["documentId"]
        repeated_dna = client.put(
            f"/api/v2/workbench/projects/{project_id}/dna/organization_intro",
            headers={**headers, "Idempotency-Key": "dna-create-1"},
            json={
                "markdownContent": "# 组织介绍\n使命是推动公益组织数字化。",
                "fileName": "organization.md",
                "expectedVersion": 0,
            },
        )
        assert repeated_dna.json() == dna.json()

        updated_dna = client.put(
            f"/api/v2/workbench/projects/{project_id}/dna/organization_intro",
            headers={**headers, "Idempotency-Key": "dna-update-2"},
            json={
                "markdownContent": "# 组织介绍\n使命是推动公益组织数字化与知识沉淀。",
                "expectedVersion": 1,
            },
        )
        assert updated_dna.status_code == 200, updated_dna.text
        assert updated_dna.json()["version"] == 2
        conflict = client.put(
            f"/api/v2/workbench/projects/{project_id}/dna/organization_intro",
            headers={**headers, "Idempotency-Key": "dna-stale-3"},
            json={"markdownContent": "过期覆盖", "expectedVersion": 1},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "dna_version_conflict"

        report_id, attempt_id = _seed_report_and_processing(
            database,
            organization_id=identity["organizationId"],
            membership_id=identity["membershipId"],
            project_id=project_id,
            document_id=document_id,
        )
        reports = client.get(
            f"/api/v2/workbench/projects/{project_id}/reports",
            headers=headers,
        )
        assert reports.status_code == 200, reports.text
        assert reports.json()[0]["id"] == report_id
        assert reports.json()[0]["latest"]["content_markdown"] == "# 严格报告 v1"

        report_update = client.patch(
            f"/api/v2/workbench/reports/{report_id}",
            headers={**headers, "Idempotency-Key": "report-update-1"},
            json={
                "expectedVersion": 1,
                "title": "严格测试报告（二版）",
                "contentMarkdown": "# 严格报告 v2",
                "contentJson": {"eventLineVersion": 0},
                "changeSummary": "补充结论",
            },
        )
        assert report_update.status_code == 200, report_update.text
        assert report_update.json()["aggregateVersion"] == 2
        assert report_update.json()["latest_version"] == 2
        assert report_update.json()["latest"]["content_markdown"] == "# 严格报告 v2"
        repeated_report = client.patch(
            f"/api/v2/workbench/reports/{report_id}",
            headers={**headers, "Idempotency-Key": "report-update-1"},
            json={
                "expectedVersion": 1,
                "title": "严格测试报告（二版）",
                "contentMarkdown": "# 严格报告 v2",
                "contentJson": {"eventLineVersion": 0},
                "changeSummary": "补充结论",
            },
        )
        assert repeated_report.json() == report_update.json()

        stale_report = client.patch(
            f"/api/v2/workbench/reports/{report_id}",
            headers={**headers, "Idempotency-Key": "report-stale-2"},
            json={
                "expectedVersion": 1,
                "title": "错误覆盖",
                "contentMarkdown": "错误覆盖",
            },
        )
        assert stale_report.status_code == 409
        assert stale_report.json()["error"]["code"] == "report_version_conflict"

        restored = client.post(
            f"/api/v2/workbench/reports/{report_id}/restore",
            headers={**headers, "Idempotency-Key": "report-restore-1"},
            json={"expectedVersion": 2, "restoreVersion": 1},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["aggregateVersion"] == 3
        assert restored.json()["latest"]["content_markdown"] == "# 严格报告 v1"
        assert restored.json()["latest"]["restored_from_version"] == 1

        versions = client.get(
            f"/api/v2/workbench/reports/{report_id}/versions",
            headers=headers,
        )
        assert versions.status_code == 200, versions.text
        assert [item["version"] for item in versions.json()] == [3, 2, 1]

        workspace = client.get(
            f"/api/v2/workbench/projects/{project_id}/workspace",
            headers=headers,
        )
        assert workspace.status_code == 200, workspace.text
        assert workspace.json()["answers"][0]["answerId"] == answer_id
        assert workspace.json()["favorites"][0]["targetId"] == answer_id
        assert workspace.json()["processingAttempts"][0]["processingAttemptId"] == attempt_id

        analysis = client.get(
            f"/api/v2/workbench/projects/{project_id}/analysis-status",
            headers=headers,
        )
        assert analysis.status_code == 200, analysis.text
        assert analysis.json()["state"] == "ready"
        assert analysis.json()["counts"]["attempts"] == 1
        assert analysis.json()["counts"]["evidenceLinks"] == 1

        narrative = client.get(
            f"/api/v2/workbench/projects/{project_id}/narrative",
            headers=headers,
        )
        assert narrative.status_code == 200, narrative.text
        assert narrative.json()["id"] == report_id
        assert narrative.json()["rev"] == 3

        favorite_id = favorite.json()["favorite"]["favoriteId"]
        removed_favorite = client.request(
            "DELETE",
            f"/api/v2/workbench/favorites/{favorite_id}",
            headers={**headers, "Idempotency-Key": "favorite-remove-1"},
            json={"expectedVersion": 1},
        )
        repeated_remove = client.request(
            "DELETE",
            f"/api/v2/workbench/favorites/{favorite_id}",
            headers={**headers, "Idempotency-Key": "favorite-remove-1"},
            json={"expectedVersion": 1},
        )
        assert removed_favorite.status_code == 200, removed_favorite.text
        assert repeated_remove.json() == removed_favorite.json()

        archived_answer = client.request(
            "DELETE",
            f"/api/v2/workbench/answers/{answer_id}",
            headers={**headers, "Idempotency-Key": "answer-archive-1"},
            json={"expectedVersion": 1},
        )
        repeated_archive = client.request(
            "DELETE",
            f"/api/v2/workbench/answers/{answer_id}",
            headers={**headers, "Idempotency-Key": "answer-archive-1"},
            json={"expectedVersion": 1},
        )
        assert archived_answer.status_code == 200, archived_answer.text
        assert repeated_archive.json() == archived_answer.json()

    with runtime_connection(database, "cloud") as connection:
        command_types = {
            str(row["command_type"])
            for row in connection.execute(
                """
                SELECT command_type FROM command_envelopes
                WHERE organization_id = ?
                """,
                (identity["organizationId"],),
            ).fetchall()
        }
        assert "workbench.favorite.created" in command_types
        assert "workbench.favorite.removed" in command_types
        assert "workbench.answer.archived" in command_types
        assert "workbench.dna.saved" in command_types
        assert "workbench.report.updated" in command_types
        assert "workbench.report.restored" in command_types
        assert connection.execute(
            """
            SELECT COUNT(*) FROM delivery_outbox
            WHERE organization_id = ?
              AND aggregate_type IN (
                'workbench_favorite', 'knowledge_document', 'narrative_output'
              )
            """,
            (identity["organizationId"],),
        ).fetchone()[0] >= 4
        assert connection.execute(
            """
            SELECT COUNT(*) FROM audit_events
            WHERE organization_id = ?
              AND resource_type IN (
                'workbench_favorite', 'knowledge_document', 'narrative_output'
              )
            """,
            (identity["organizationId"],),
        ).fetchone()[0] >= 4


def test_workbench_libraries_project_text_jobs_and_task_actions(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        identity, headers = _bootstrap(client)
        snapshot = client.get("/api/v2/business/snapshot", headers=headers).json()
        project_id = snapshot["projects"][0]["projectId"]

        skill_payload = {
            "name": "严格写作风格",
            "description": "只陈述可追溯事实",
            "distilledMd": "结论后附证据边界。",
            "sortOrder": 1,
        }
        skill = client.post(
            "/api/v2/workbench/libraries/writing_skill",
            headers={**headers, "Idempotency-Key": "skill-create-1"},
            json=skill_payload,
        )
        repeated_skill = client.post(
            "/api/v2/workbench/libraries/writing_skill",
            headers={**headers, "Idempotency-Key": "skill-create-1"},
            json=skill_payload,
        )
        assert skill.status_code == 201, skill.text
        assert repeated_skill.json() == skill.json()
        skill_id = skill.json()["id"]
        assert skill.json()["version"] == 1
        listed = client.get(
            "/api/v2/workbench/libraries/writing_skill",
            headers=headers,
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["id"] == skill_id

        skill_update_payload = {
            **skill_payload,
            "description": "只陈述可追溯事实并标明不确定性",
            "expectedVersion": 1,
        }
        updated_skill = client.put(
            f"/api/v2/workbench/libraries/writing_skill/{skill_id}",
            headers={**headers, "Idempotency-Key": "skill-update-1"},
            json=skill_update_payload,
        )
        repeated_update = client.put(
            f"/api/v2/workbench/libraries/writing_skill/{skill_id}",
            headers={**headers, "Idempotency-Key": "skill-update-1"},
            json={**skill_update_payload, "expectedVersion": 2},
        )
        assert updated_skill.status_code == 200, updated_skill.text
        assert updated_skill.json()["version"] == 2
        assert repeated_update.json() == updated_skill.json()
        stale_skill = client.put(
            f"/api/v2/workbench/libraries/writing_skill/{skill_id}",
            headers={**headers, "Idempotency-Key": "skill-stale-1"},
            json={**skill_payload, "expectedVersion": 1},
        )
        assert stale_skill.status_code == 409
        assert stale_skill.json()["error"]["code"] == "workbench_library_version_conflict"

        brand = client.put(
            f"/api/v2/workbench/projects/{project_id}/texts/brand_proposition",
            headers={**headers, "Idempotency-Key": "brand-create-1"},
            json={
                "title": "品牌主张",
                "markdownContent": "让公益组织拥有可积累的数字能力。",
                "expectedVersion": 0,
            },
        )
        repeated_brand = client.put(
            f"/api/v2/workbench/projects/{project_id}/texts/brand_proposition",
            headers={**headers, "Idempotency-Key": "brand-create-1"},
            json={
                "title": "品牌主张",
                "markdownContent": "让公益组织拥有可积累的数字能力。",
                "expectedVersion": 1,
            },
        )
        assert brand.status_code == 200, brand.text
        assert repeated_brand.json() == brand.json()
        assert brand.json()["version"] == 1
        text_items = client.get(
            f"/api/v2/workbench/projects/{project_id}/texts",
            headers=headers,
        )
        assert text_items.status_code == 200, text_items.text
        assert (
            text_items.json()["brand_proposition"]["markdownContent"]
            == "让公益组织拥有可积累的数字能力。"
        )

        answer = client.post(
            "/api/v2/workbench/answers",
            headers={**headers, "Idempotency-Key": "action-answer-1"},
            json={
                "projectId": project_id,
                "question": "下一步需要补什么证据？",
                "answerMarkdown": "补充本季度服务对象反馈。",
                "sourceManifest": {"projectId": project_id, "documentIds": []},
                "modelName": "strict-test-model",
            },
        )
        assert answer.status_code == 201, answer.text
        answer_id = answer.json()["answer"]["answerId"]
        action = client.post(
            f"/api/v2/workbench/answers/{answer_id}/actions/request_evidence",
            headers={**headers, "Idempotency-Key": "answer-evidence-task-1"},
        )
        repeated_action = client.post(
            f"/api/v2/workbench/answers/{answer_id}/actions/request_evidence",
            headers={**headers, "Idempotency-Key": "answer-evidence-task-1"},
        )
        assert action.status_code == 200, action.text
        assert repeated_action.json() == action.json()
        task_id = action.json()["taskId"]

        promoted = client.post(
            f"/api/v2/workbench/projects/{project_id}/todos/{task_id}/promote",
            headers={**headers, "Idempotency-Key": "todo-promote-1"},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["newTaskId"] == task_id
        assert promoted.json()["status"] == "reused"

        completed = client.post(
            f"/api/v2/workbench/projects/{project_id}/todos/{task_id}/complete",
            headers={**headers, "Idempotency-Key": "todo-complete-1"},
        )
        repeated_completed = client.post(
            f"/api/v2/workbench/projects/{project_id}/todos/{task_id}/complete",
            headers={**headers, "Idempotency-Key": "todo-complete-1"},
        )
        assert completed.status_code == 200, completed.text
        assert repeated_completed.json() == completed.json()
        assert completed.json()["task"]["lifecycleState"] == "completed"

        dna = client.put(
            f"/api/v2/workbench/projects/{project_id}/dna/organization_intro",
            headers={**headers, "Idempotency-Key": "job-dna-1"},
            json={
                "markdownContent": "严格分析任务资料",
                "expectedVersion": 0,
            },
        )
        assert dna.status_code == 200, dna.text
        report_id, attempt_id = _seed_report_and_processing(
            database,
            organization_id=identity["organizationId"],
            membership_id=identity["membershipId"],
            project_id=project_id,
            document_id=dna.json()["documentId"],
        )
        job = client.get(
            f"/api/v2/workbench/analysis-jobs/{attempt_id}",
            headers=headers,
        )
        stages = client.get(
            f"/api/v2/workbench/analysis-jobs/{attempt_id}/stages",
            headers=headers,
        )
        report_run = client.get(
            f"/api/v2/workbench/report-runs/{report_id}",
            headers=headers,
        )
        assert job.status_code == 200, job.text
        assert job.json()["clientId"] == project_id
        assert job.json()["status"] == "completed"
        assert stages.status_code == 200, stages.text
        assert stages.json()[0]["jobId"] == attempt_id
        assert report_run.status_code == 200, report_run.text
        assert report_run.json()["artifact"]["id"] == report_id

        deleted_skill = client.request(
            "DELETE",
            f"/api/v2/workbench/libraries/writing_skill/{skill_id}",
            headers={**headers, "Idempotency-Key": "skill-delete-1"},
            json={"expectedVersion": 2},
        )
        repeated_delete = client.request(
            "DELETE",
            f"/api/v2/workbench/libraries/writing_skill/{skill_id}",
            headers={**headers, "Idempotency-Key": "skill-delete-1"},
            json={"expectedVersion": 1},
        )
        assert deleted_skill.status_code == 200, deleted_skill.text
        assert repeated_delete.json() == deleted_skill.json()

    with runtime_connection(database, "cloud") as connection:
        command_types = {
            str(row["command_type"])
            for row in connection.execute(
                """
                SELECT command_type
                FROM command_envelopes
                WHERE organization_id = ?
                """,
                (identity["organizationId"],),
            ).fetchall()
        }
        assert "workbench.library.writing_skill.saved" in command_types
        assert "workbench.library.writing_skill.archived" in command_types
        assert "workbench.project_text.brand_proposition.saved" in command_types
        assert "task.created" in command_types
        assert "task.completed" in command_types


def test_workbench_route_inventory_is_fully_owned() -> None:
    result = subprocess.run(
        ["node", "scripts/ui_route_inventory.mjs", "--domain", "workbench_outputs", "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    operations = json.loads(result.stdout)
    assert len(operations) == 148
    registry = build_default_registry()
    domain_routes = [
        route for route in registry.routes if route.domain == "workbench_outputs"
    ]
    missing = []
    for operation in operations:
        sample = re.sub(r":[^/]+", "sample", operation["path"])
        if not any(
            route.method == operation["method"]
            and route.regex.fullmatch(sample)
            and route.handler.__name__ != "_gap"
            for route in domain_routes
        ):
            missing.append(f"{operation['method']} {operation['path']}")
    assert missing == []
    frozen_operations = []
    for operation in operations:
        sample = re.sub(r":[^/]+", "sample", operation["path"])
        if any(
            route.method == operation["method"]
            and route.regex.fullmatch(sample)
            and route.handler.__name__.startswith("frozen_")
            for route in domain_routes
        ):
            frozen_operations.append(f"{operation['method']} {operation['path']}")
    assert frozen_operations == []
    assert not any(route.handler.__name__ == "_gap" for route in domain_routes)


def test_project_structure_crud_reuses_versioned_organization_plans(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        identity, headers = _bootstrap(client)
        project_id = _project_id(client, headers)
        module = client.post(
            f"/api/v2/workbench/projects/{project_id}/structure/project_module",
            headers={**headers, "Idempotency-Key": "module-create"},
            json={
                "name": "伙伴协作",
                "goal": "形成稳定协作机制",
                "deliverables": ["伙伴地图"],
            },
        )
        assert module.status_code == 200, module.text
        module_id = module.json()["id"]
        assert module.json()["authorityType"] == "organization_plans"

        flow = client.post(
            f"/api/v2/workbench/projects/{project_id}/structure/project_flow",
            headers={**headers, "Idempotency-Key": "flow-create"},
            json={
                "moduleId": module_id,
                "name": "首次沟通",
                "steps": ["准备背景", "完成访谈"],
            },
        )
        assert flow.status_code == 200, flow.text
        flow_id = flow.json()["id"]

        structure = client.get(
            f"/api/v2/workbench/projects/{project_id}/structure",
            headers=headers,
        )
        assert any(item["id"] == module_id for item in structure.json()["modules"])
        assert next(
            item for item in structure.json()["flows"] if item["id"] == flow_id
        )["moduleName"] == "伙伴协作"

        updated = client.patch(
            (
                f"/api/v2/workbench/projects/{project_id}/structure/"
                f"project_flow/{flow_id}"
            ),
            headers={**headers, "Idempotency-Key": "flow-update"},
            json={
                "expectedVersion": 1,
                "moduleId": module_id,
                "name": "首次沟通（二版）",
                "steps": ["准备背景", "完成访谈", "确认行动"],
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["version"] == 2

        archived = client.request(
            "DELETE",
            (
                f"/api/v2/workbench/projects/{project_id}/structure/"
                f"project_flow/{flow_id}"
            ),
            headers={**headers, "Idempotency-Key": "flow-archive"},
            json={"expectedVersion": 2},
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["status"] == "archived"

        with runtime_connection(database, "cloud", read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT status, json_extract(attributes_json, '$.orgModelKind') AS kind
                FROM organization_plans
                WHERE organization_id = ? AND period_label = 'project_structure'
                ORDER BY created_at
                """,
                (identity["organizationId"],),
            ).fetchall()
            command_count = connection.execute(
                """
                SELECT COUNT(*) FROM command_envelopes
                WHERE organization_id = ?
                  AND command_type LIKE 'workbench.project_%'
                """,
                (identity["organizationId"],),
            ).fetchone()[0]
        assert [(row["status"], row["kind"]) for row in rows] == [
            ("active", "project_module"),
            ("archived", "project_flow"),
        ]
        assert command_count == 4


def test_clarification_suggestion_and_value_validation_reuse_intelligence(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        identity, headers = _bootstrap(client)
        project_id = _project_id(client, headers)

        clarification = client.post(
            (
                f"/api/v2/workbench/projects/{project_id}/"
                "narrative-clarifications"
            ),
            headers={**headers, "Idempotency-Key": "clarification-create"},
            json={
                "dimension": "organization_intro",
                "question": "服务对象是谁？",
                "answer": "以县域学校的一线教师和学生为主要服务对象。",
            },
        )
        assert clarification.status_code == 200, clarification.text
        clarification_list = client.get(
            (
                f"/api/v2/workbench/projects/{project_id}/"
                "narrative-clarifications"
            ),
            headers=headers,
        )
        assert clarification_list.json()["clarifications"][0]["answer"].startswith(
            "以县域学校"
        )

        suggestion_payload = {
            "fingerprint": "suggestion-a",
            "action": "completed",
            "actor": "工作台管理员",
            "suggestionText": "补充伙伴访谈",
            "sourceDocTitle": "伙伴纪要",
            "sourceDocId": "doc-a",
        }
        suggestion = client.post(
            f"/api/v2/workbench/projects/{project_id}/suggestion-log",
            headers={**headers, "Idempotency-Key": "suggestion-save"},
            json=suggestion_payload,
        )
        assert suggestion.status_code == 200, suggestion.text
        logged = client.get(
            f"/api/v2/workbench/projects/{project_id}/suggestion-log",
            headers=headers,
        )
        assert logged.json()["completed"][0]["fingerprint"] == "suggestion-a"
        removed = client.request(
            "DELETE",
            (
                f"/api/v2/workbench/projects/{project_id}/"
                "suggestion-log/suggestion-a"
            ),
            headers={**headers, "Idempotency-Key": "suggestion-delete"},
            json={},
        )
        assert removed.json() == {"ok": True}

        answer = client.post(
            "/api/v2/workbench/answers",
            headers={**headers, "Idempotency-Key": "validation-answer"},
            json={
                "projectId": project_id,
                "question": "这个客户是谁？",
                "answerMarkdown": "这是当前严格项目背景回答。",
                "sourceManifest": {"projectId": project_id},
                "modelName": "strict-test-model",
            },
        )
        assert answer.status_code == 201, answer.text
        answer_id = answer.json()["answer"]["answerId"]
        review = client.post(
            "/api/v2/workbench/answer-value-reviews",
            headers={**headers, "Idempotency-Key": "validation-review"},
            json={
                "clientId": project_id,
                "messageId": answer_id,
                "prompt": "这个客户是谁？",
                "answerMode": "general",
                "userVisibleQualityStatus": "ready",
                "shouldShowRetryBanner": False,
                "usableAnswer": True,
                "reviewerNote": "可使用",
                "manualBaselineMinutes": 20,
                "dataCenterReviewMinutes": 5,
            },
        )
        assert review.status_code == 201, review.text

        session = client.post(
            "/api/v2/workbench/value-validation-sessions",
            headers={**headers, "Idempotency-Key": "validation-session"},
            json={"projectId": project_id},
        )
        assert session.status_code == 200, session.text
        session_id = session.json()["id"]
        completed = client.post(
            (
                f"/api/v2/workbench/value-validation-sessions/{session_id}/"
                "complete-question"
            ),
            headers={**headers, "Idempotency-Key": "validation-question"},
            json={
                "questionId": "wvq_01",
                "reviewId": review.json()["id"],
                "messageId": answer_id,
                "proposalCreated": False,
                "executionTicketCreated": False,
            },
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["completedQuestionIds"] == ["wvq_01"]
        finished = client.post(
            f"/api/v2/workbench/value-validation-sessions/{session_id}/finish",
            headers={**headers, "Idempotency-Key": "validation-finish"},
            json={},
        )
        assert finished.status_code == 200, finished.text
        assert finished.json()["status"] == "completed"

        with runtime_connection(database, "cloud", read_only=True) as connection:
            kinds = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT record_kind FROM intelligence_records
                    WHERE organization_id = ?
                    """,
                    (identity["organizationId"],),
                ).fetchall()
            }
        assert {
            "narrative_clarification",
            "suggestion_action",
            "value_validation_session",
        } <= kinds


def test_new_workbench_ui_handlers_forward_cas_and_workspace_context() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.commands: list[tuple[str, str, dict, str]] = []

        def cloud_query(self, path: str, *, query: dict | None = None) -> dict:
            if path == "/api/v2/workbench/retrieval-settings":
                return {"version": 4}
            if path == "/api/v2/workbench/judgments/judgment-1":
                return {"aggregateVersion": 3}
            if path == "/api/v2/workbench/answer-quality-failures/failure-1":
                return {"version": 2}
            raise AssertionError((path, query))

        def cloud_command(
            self,
            method: str,
            path: str,
            *,
            payload: dict,
            idempotency_key: str,
            refresh_business: bool = True,
        ) -> dict:
            self.commands.append((method, path, payload, idempotency_key))
            return {
                "id": "strict-result",
                "proposalId": "strict-result",
                "status": "resolved",
            }

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

    compatibility = Compatibility()
    registry = build_default_registry()
    settings = registry.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="retrieval/settings",
            query={},
            body={"shadowMode": False},
            idempotency_key="ui-settings-1",
        ),
    )
    assert settings["id"] == "strict-result"
    registry.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="memory/judgments/confirm",
            query={},
            body={"judgmentId": "judgment-1", "action": "approved"},
            idempotency_key="ui-judgment-1",
        ),
    )
    registry.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="workspace-answer-quality-failures/failure-1/resolve",
            query={},
            body={"note": "已修复"},
            idempotency_key="ui-quality-1",
        ),
    )
    registry.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="clients/project-1/meetings/meeting-1/proposals/prepare",
            query={},
            body={},
            idempotency_key="ui-meeting-proposal-1",
        ),
    )
    assert compatibility.runtime.commands == [
        (
            "POST",
            "/api/v2/workbench/retrieval-settings",
            {"shadowMode": False, "expectedVersion": 4},
            "ui-settings-1",
        ),
        (
            "POST",
            "/api/v2/workbench/judgments/judgment-1/confirm",
            {
                "judgmentId": "judgment-1",
                "action": "approved",
                "expectedVersion": 3,
            },
            "ui-judgment-1",
        ),
        (
            "POST",
            "/api/v2/workbench/answer-quality-failures/failure-1/resolve",
            {"note": "已修复", "expectedVersion": 2},
            "ui-quality-1",
        ),
        (
            "POST",
            (
                "/api/v2/workbench/projects/project-1/meetings/"
                "meeting-1/proposal-drafts/prepare"
            ),
            {},
            "ui-meeting-proposal-1",
        ),
    ]


def test_workbench_configuration_proposals_meeting_and_value_review_authorities(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        identity, headers = _bootstrap(client)
        snapshot = client.get("/api/v2/business/snapshot", headers=headers)
        assert snapshot.status_code == 200, snapshot.text
        project_id = snapshot.json()["projects"][0]["projectId"]

        missing_settings = client.get(
            "/api/v2/workbench/retrieval-settings",
            headers=headers,
        )
        assert missing_settings.status_code == 404
        settings_payload = {
            "embeddingProvider": "local",
            "embeddingModel": "bge-small",
            "embeddingDimension": 384,
            "embeddingMode": "local",
            "routerEnabled": True,
            "routerProvider": "rules",
            "routerModel": "strict-rules-v1",
            "rerankEnabled": False,
            "rerankProvider": "rules",
            "shadowMode": True,
        }
        settings = client.post(
            "/api/v2/workbench/retrieval-settings",
            headers={**headers, "Idempotency-Key": "retrieval-settings-1"},
            json=settings_payload,
        )
        repeated_settings = client.post(
            "/api/v2/workbench/retrieval-settings",
            headers={**headers, "Idempotency-Key": "retrieval-settings-1"},
            json=settings_payload,
        )
        assert settings.status_code == 200, settings.text
        assert settings.json()["version"] == 1
        assert repeated_settings.json() == settings.json()
        stale_settings = client.post(
            "/api/v2/workbench/retrieval-settings",
            headers={**headers, "Idempotency-Key": "retrieval-settings-stale"},
            json={**settings_payload, "shadowMode": False, "expectedVersion": 2},
        )
        assert stale_settings.status_code == 409
        assert (
            stale_settings.json()["error"]["code"]
            == "retrieval_settings_version_conflict"
        )

        proposal_payload = {
            "kind": "task_prep",
            "title": "验证严格提案权威",
            "summary": "提案统一复用 intelligence_records。",
            "rationale": "先审批再执行",
            "riskLevel": "low",
            "sourceRefs": ["manual:test"],
            "payload": {"taskDrafts": [{"title": "核验提案"}]},
        }
        proposal = client.post(
            f"/api/v2/workbench/projects/{project_id}/proposal-drafts",
            headers={**headers, "Idempotency-Key": "proposal-1"},
            json=proposal_payload,
        )
        repeated_proposal = client.post(
            f"/api/v2/workbench/projects/{project_id}/proposal-drafts",
            headers={**headers, "Idempotency-Key": "proposal-1"},
            json=proposal_payload,
        )
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["kind"] == "task_prep"
        assert proposal.json()["requiresApproval"] is True
        assert repeated_proposal.json() == proposal.json()

        answer = client.post(
            "/api/v2/workbench/answers",
            headers={**headers, "Idempotency-Key": "review-answer-1"},
            json={
                "projectId": project_id,
                "question": "价值评审测试问题",
                "answerMarkdown": "这是可复核的严格回答。",
                "sourceManifest": {"projectId": project_id},
                "modelName": "strict-test-model",
            },
        )
        assert answer.status_code == 201, answer.text
        answer_id = answer.json()["answer"]["answerId"]
        judgment = client.post(
            f"/api/v2/workbench/answers/{answer_id}/judgment",
            headers={**headers, "Idempotency-Key": "answer-judgment-1"},
            json={"note": "先作为候选判断复核"},
        )
        repeated_judgment = client.post(
            f"/api/v2/workbench/answers/{answer_id}/judgment",
            headers={**headers, "Idempotency-Key": "answer-judgment-1"},
            json={"note": "先作为候选判断复核"},
        )
        assert judgment.status_code == 200, judgment.text
        assert judgment.json()["status"] == "awaiting_review"
        assert repeated_judgment.json() == judgment.json()
        judgment_id = judgment.json()["id"]
        confirmed_judgment = client.post(
            f"/api/v2/workbench/judgments/{judgment_id}/confirm",
            headers={**headers, "Idempotency-Key": "judgment-confirm-1"},
            json={
                "action": "approved",
                "note": "证据充分",
                "expectedVersion": 1,
            },
        )
        assert confirmed_judgment.status_code == 200, confirmed_judgment.text
        assert confirmed_judgment.json()["status"] == "approved"
        assert confirmed_judgment.json()["version"] == 2
        assert confirmed_judgment.json()["aggregateVersion"] == 2
        repeated_confirmed_judgment = client.post(
            f"/api/v2/workbench/judgments/{judgment_id}/confirm",
            headers={**headers, "Idempotency-Key": "judgment-confirm-1"},
            json={
                "action": "approved",
                "note": "证据充分",
                "expectedVersion": 2,
            },
        )
        assert repeated_confirmed_judgment.json() == confirmed_judgment.json()
        stale_judgment = client.post(
            f"/api/v2/workbench/judgments/{judgment_id}/confirm",
            headers={**headers, "Idempotency-Key": "judgment-confirm-stale"},
            json={
                "action": "rejected",
                "note": "过期审批",
                "expectedVersion": 1,
            },
        )
        assert stale_judgment.status_code == 409
        reports_without_judgment = client.get(
            f"/api/v2/workbench/projects/{project_id}/reports",
            headers=headers,
        )
        insights_with_judgment = client.get(
            f"/api/v2/workbench/projects/{project_id}/insights",
            headers=headers,
        )
        assert reports_without_judgment.status_code == 200
        assert reports_without_judgment.json() == []
        assert insights_with_judgment.status_code == 200
        assert insights_with_judgment.json()["judgments"][0]["id"] == judgment_id

        review_payload = {
            "clientId": project_id,
            "messageId": answer_id,
            "prompt": "价值评审测试问题",
            "answerMode": "general",
            "userVisibleQualityStatus": "ready",
            "shouldShowRetryBanner": False,
            "usableAnswer": True,
            "reviewerNote": "可以直接使用",
            "manualBaselineMinutes": 30,
            "dataCenterReviewMinutes": 10,
        }
        review = client.post(
            "/api/v2/workbench/answer-value-reviews",
            headers={**headers, "Idempotency-Key": "answer-review-1"},
            json=review_payload,
        )
        repeated_review = client.post(
            "/api/v2/workbench/answer-value-reviews",
            headers={**headers, "Idempotency-Key": "answer-review-1"},
            json=review_payload,
        )
        assert review.status_code == 201, review.text
        assert review.json()["savedMinutes"] == 20
        assert repeated_review.json() == review.json()
        reviews = client.get(
            "/api/v2/workbench/answer-value-reviews",
            headers=headers,
            params={"projectId": project_id},
        )
        summary = client.get(
            f"/api/v2/workbench/projects/{project_id}/answer-value-summary",
            headers=headers,
        )
        assert reviews.status_code == 200, reviews.text
        assert [item["id"] for item in reviews.json()] == [review.json()["id"]]
        assert summary.status_code == 200, summary.text
        assert summary.json()["reviewCount"] == 1
        assert summary.json()["usableAnswerRate"] == 1
        assert summary.json()["estimatedTimeSavedRate"] == 2 / 3
        failed_review = client.post(
            "/api/v2/workbench/answer-value-reviews",
            headers={**headers, "Idempotency-Key": "answer-review-failed-1"},
            json={
                **review_payload,
                "userVisibleQualityStatus": "needs_retry",
                "shouldShowRetryBanner": True,
                "usableAnswer": False,
                "reviewerNote": "需要补充直接结论",
            },
        )
        assert failed_review.status_code == 201, failed_review.text
        failures = client.get(
            "/api/v2/workbench/answer-quality-failures",
            headers=headers,
            params={"projectId": project_id},
        )
        assert failures.status_code == 200, failures.text
        assert failures.json()[0]["id"] == failed_review.json()["id"]
        assert failures.json()[0]["status"] == "open"
        resolved_failure = client.post(
            (
                "/api/v2/workbench/answer-quality-failures/"
                f"{failed_review.json()['id']}/resolve"
            ),
            headers={**headers, "Idempotency-Key": "answer-failure-resolve-1"},
            json={"note": "已重试并补充结论", "expectedVersion": 1},
        )
        assert resolved_failure.status_code == 200, resolved_failure.text
        assert resolved_failure.json()["status"] == "resolved"
        assert resolved_failure.json()["version"] == 2
        repeated_resolved_failure = client.post(
            (
                "/api/v2/workbench/answer-quality-failures/"
                f"{failed_review.json()['id']}/resolve"
            ),
            headers={**headers, "Idempotency-Key": "answer-failure-resolve-1"},
            json={"note": "已重试并补充结论", "expectedVersion": 2},
        )
        assert repeated_resolved_failure.json() == resolved_failure.json()
        dna_delta = client.post(
            "/api/v2/workbench/dna-deltas",
            headers={**headers, "Idempotency-Key": "dna-delta-1"},
            json={
                "clientId": project_id,
                "dimension": "organization_intro",
                "proposedChange": "补充数字化知识沉淀能力",
                "summary": "组织能力画像发生变化",
                "confidence": "high",
                "evidenceIds": [],
            },
        )
        repeated_dna_delta = client.post(
            "/api/v2/workbench/dna-deltas",
            headers={**headers, "Idempotency-Key": "dna-delta-1"},
            json={
                "clientId": project_id,
                "dimension": "organization_intro",
                "proposedChange": "补充数字化知识沉淀能力",
                "summary": "组织能力画像发生变化",
                "confidence": "high",
                "evidenceIds": [],
            },
        )
        assert dna_delta.status_code == 201, dna_delta.text
        assert dna_delta.json()["status"] == "awaiting_review"
        assert repeated_dna_delta.json() == dna_delta.json()
        next_dna_delta = client.post(
            "/api/v2/workbench/dna-deltas",
            headers={**headers, "Idempotency-Key": "dna-delta-2"},
            json={
                "clientId": project_id,
                "dimension": "organization_intro",
                "proposedChange": "进一步明确知识运营机制",
                "confidence": "medium",
            },
        )
        assert next_dna_delta.status_code == 201, next_dna_delta.text
        assert next_dna_delta.json()["supersedesId"] == dna_delta.json()["id"]

        meeting_id = "meeting-strict-001"
        task_id = new_id()
        now = utc_now()
        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                INSERT INTO task_records (
                    task_id, organization_id, project_id, title, description,
                    created_by_membership_id, priority, lifecycle_state,
                    task_kind, visibility_scope, duration_minutes,
                    completion_note, source_type, source_id, attributes_json,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, '整理会议行动项', '', ?, 'high', 'todo',
                          'task', 'participants', 60, '', 'meeting', ?, '{}',
                          1, ?, ?)
                """,
                (
                    task_id,
                    identity["organizationId"],
                    project_id,
                    identity["membershipId"],
                    meeting_id,
                    now,
                    now,
                ),
            )
            connection.commit()
        action_items = client.get(
            f"/api/v2/workbench/projects/{project_id}/meeting-action-items",
            headers=headers,
        )
        assert action_items.status_code == 200, action_items.text
        assert action_items.json()["medium"][0]["taskId"] == task_id
        assert action_items.json()["medium"][0]["meetingId"] == meeting_id

        meeting_proposal = client.post(
            (
                f"/api/v2/workbench/projects/{project_id}/meetings/"
                f"{meeting_id}/proposal-drafts/prepare"
            ),
            headers={**headers, "Idempotency-Key": "meeting-proposal-1"},
        )
        assert meeting_proposal.status_code == 200, meeting_proposal.text
        assert meeting_proposal.json()["kind"] == "meeting_prep"
        assert meeting_proposal.json()["payload"]["meetingContext"]["taskIds"] == [
            task_id
        ]
        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                UPDATE task_records
                SET version = 2, updated_at = ?
                WHERE task_id = ?
                """,
                (utc_now(), task_id),
            )
            connection.commit()
        repeated_meeting_proposal = client.post(
            (
                f"/api/v2/workbench/projects/{project_id}/meetings/"
                f"{meeting_id}/proposal-drafts/prepare"
            ),
            headers={**headers, "Idempotency-Key": "meeting-proposal-1"},
        )
        assert repeated_meeting_proposal.json() == meeting_proposal.json()

    with runtime_connection(database, "cloud") as connection:
        config_count = connection.execute(
            """
            SELECT COUNT(*)
            FROM scoped_configuration_records
            WHERE organization_id = ? AND configuration_kind = ?
            """,
            (identity["organizationId"], "workbench_retrieval_model"),
        ).fetchone()[0]
        intelligence_kinds = {
            row["record_kind"]
            for row in connection.execute(
                """
                SELECT record_kind
                FROM intelligence_records
                WHERE organization_id = ?
                """,
                (identity["organizationId"],),
            ).fetchall()
        }
        command_types = {
            row["command_type"]
            for row in connection.execute(
                """
                SELECT command_type
                FROM command_envelopes
                WHERE organization_id = ?
                """,
                (identity["organizationId"],),
            ).fetchall()
        }
        assert config_count == 1
        assert {"proposal_draft", "workspace_answer_value_review"} <= intelligence_kinds
        assert {
            "workbench.retrieval_settings.saved",
            "workbench.proposal_draft.created",
            "workbench.answer_value_review.created",
            "workbench.answer_quality_failure.resolved",
            "workbench.answer.promoted_to_judgment",
            "workbench.judgment.confirmed",
            "workbench.dna_delta.created",
        } <= command_types


def test_fundraising_web_draft_uses_public_metadata_and_cloud_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[dict] = []

    class Operations:
        def __init__(self, _runtime) -> None:
            pass

        def begin(self, **kwargs):
            assert "queryHash" in kwargs["payload"]
            assert "日慈公开资料" not in json.dumps(
                kwargs["payload"],
                ensure_ascii=False,
            )
            return {
                **kwargs["initial_result"],
                "operationId": "web-draft-operation",
                "sandboxId": "sandbox-a",
            }

        def update(self, **kwargs):
            updates.append(dict(kwargs))
            return kwargs["result_patch"]

    class Runtime:
        def __init__(self) -> None:
            self.saved_payload = None

        @contextmanager
        def pinned_workspace_context(self):
            yield

        def cloud_command(
            self,
            _method,
            _path,
            *,
            payload,
            idempotency_key,
        ):
            assert idempotency_key == "web-draft-1"
            self.saved_payload = dict(payload)
            return {
                **payload,
                "id": "dna-web-1",
                "version": 1,
                "createdAt": "2026-07-31T00:00:00Z",
                "updatedAt": "2026-07-31T00:00:00Z",
            }

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

    monkeypatch.setattr(
        workbench_outputs,
        "LocalPlatformOperationRepository",
        Operations,
    )
    monkeypatch.setattr(
        workbench_outputs,
        "capture_public_web",
        lambda *_args, **_kwargs: [
            PublicCaptureItem(
                title="日慈项目公开进展",
                summary="公开页面摘要显示项目已启动。",
                source_name="example.org",
                source_url="https://example.org/rici",
                captured_at="2026-07-31T00:00:00Z",
                published_at=None,
                sentiment="positive",
                sentiment_reason="命中启动",
                content_hash="b" * 64,
            )
        ],
    )

    registry = build_default_registry()
    compatibility = Compatibility()
    request = UiRequest(
        method="POST",
        path="analysis-tools/fundraising/dna/web-drafts",
        query={},
        body={
            "groupKey": "platform_fundraising",
            "label": "日慈募资画像",
            "searchQuery": "日慈公开资料",
        },
        idempotency_key="web-draft-1",
    )
    result = registry.dispatch(compatibility, request)

    assert result["id"] == "dna-web-1"
    assert result["draftRecord"]["status"] == "draft"
    assert result["draftRecord"]["sourceKind"] == "web"
    assert result["previewSources"][0]["sourceUrl"] == (
        "https://example.org/rici"
    )
    assert compatibility.runtime.saved_payload["sourceBodyStored"] is False
    assert "公开页面摘要" in compatibility.runtime.saved_payload["rawContent"]
    assert updates[-1]["state"] == "completed"
    receipt = updates[-1]["result_patch"]["output"]
    assert "公开页面摘要" not in json.dumps(receipt, ensure_ascii=False)


def test_analysis_refresh_goals_and_proposals_are_durable_and_project_scoped(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        identity, headers = _bootstrap(client)
        project_a = _project_id(client, headers)
        created_project = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={**headers, "Idempotency-Key": "workbench-project-b"},
            json={
                "name": "第二项目",
                "summary": "用于验证项目隔离",
                "participantMembershipIds": [],
            },
        )
        assert created_project.status_code == 201, created_project.text
        project_b = created_project.json()["project"]["projectId"]

        answer = client.post(
            "/api/v2/workbench/answers",
            headers={**headers, "Idempotency-Key": "analysis-answer-a"},
            json={
                "projectId": project_a,
                "question": "日慈项目的背景是什么？",
                "answerMarkdown": "严格新版项目背景回答。",
                "sourceManifest": {"projectId": project_a},
                "modelName": "strict-test-model",
            },
        )
        assert answer.status_code == 201, answer.text
        answer_id = answer.json()["answer"]["answerId"]
        analysis_payload = {
            "answerId": answer_id,
            "projectId": project_a,
            "jobType": "project_background",
        }
        analysis_headers = {
            **headers,
            "Idempotency-Key": "analysis-job-a",
        }
        analysis = client.post(
            "/api/v2/workbench/analysis-jobs",
            headers=analysis_headers,
            json=analysis_payload,
        )
        repeated_analysis = client.post(
            "/api/v2/workbench/analysis-jobs",
            headers=analysis_headers,
            json=analysis_payload,
        )
        assert analysis.status_code == 200, analysis.text
        assert repeated_analysis.json() == analysis.json()
        assert analysis.json()["id"] == answer_id
        assert analysis.json()["clientId"] == project_a
        assert analysis.json()["status"] == "completed"
        assert len(analysis.json()["sourceSnapshotHash"]) == 64
        detail = client.get(
            f"/api/v2/workbench/analysis-jobs/{answer_id}",
            headers=headers,
        )
        stages = client.get(
            f"/api/v2/workbench/analysis-jobs/{answer_id}/stages",
            headers=headers,
        )
        assert detail.json() == analysis.json()
        assert stages.status_code == 200, stages.text
        assert stages.json()[0]["jobId"] == answer_id
        assert stages.json()[0]["status"] == "completed"

        cross_project_analysis = client.post(
            "/api/v2/workbench/analysis-jobs",
            headers={**headers, "Idempotency-Key": "analysis-job-cross"},
            json={**analysis_payload, "projectId": project_b},
        )
        assert cross_project_analysis.status_code == 404
        assert cross_project_analysis.json()["error"]["code"] == (
            "analysis_answer_missing"
        )

        refresh_payload = {
            "state": "ready",
            "counts": {
                "organizationShared": 2,
                "localPrivate": 1,
            },
            "materialPackHash": "renderer-observed-hash",
        }
        refresh_headers = {
            **headers,
            "Idempotency-Key": "context-refresh-a",
        }
        refresh = client.post(
            f"/api/v2/workbench/projects/{project_a}/context-refresh-events",
            headers=refresh_headers,
            json=refresh_payload,
        )
        repeated_refresh = client.post(
            f"/api/v2/workbench/projects/{project_a}/context-refresh-events",
            headers=refresh_headers,
            json=refresh_payload,
        )
        assert refresh.status_code == 200, refresh.text
        assert repeated_refresh.json() == refresh.json()
        assert refresh.json()["status"] == "completed"
        assert refresh.json()["materialPackHash"] == "renderer-observed-hash"
        assert len(refresh.json()["receiptHash"]) == 64

        goal_payload = {
            "title": "补齐项目背景",
            "quarter": "2026Q3",
            "progress": 25,
            "ownerName": "工作台管理员",
        }
        goal_headers = {**headers, "Idempotency-Key": "goal-a"}
        goal = client.post(
            f"/api/v2/workbench/projects/{project_a}/goals",
            headers=goal_headers,
            json=goal_payload,
        )
        repeated_goal = client.post(
            f"/api/v2/workbench/projects/{project_a}/goals",
            headers=goal_headers,
            json=goal_payload,
        )
        assert goal.status_code == 200, goal.text
        assert repeated_goal.json() == goal.json()
        assert goal.json()["authorityType"] == "task_records(task_kind=goal)"
        assert client.get(
            f"/api/v2/workbench/projects/{project_a}/goals",
            headers=headers,
        ).json() == [goal.json()]
        assert client.get(
            f"/api/v2/workbench/projects/{project_b}/goals",
            headers=headers,
        ).json() == []

        proposal = client.post(
            f"/api/v2/workbench/projects/{project_a}/proposal-drafts",
            headers={**headers, "Idempotency-Key": "proposal-a"},
            json={
                "kind": "context_refresh",
                "title": "补充背景提案",
                "summary": "建议补充组织共享摘要。",
                "payload": {"source": "strict_test"},
            },
        )
        assert proposal.status_code == 200, proposal.text
        assert client.get(
            f"/api/v2/workbench/projects/{project_a}/proposal-drafts",
            headers=headers,
        ).json() == [proposal.json()]
        assert client.get(
            f"/api/v2/workbench/projects/{project_b}/proposal-drafts",
            headers=headers,
        ).json() == []

        workspace_a = client.get(
            f"/api/v2/workbench/projects/{project_a}/workspace",
            headers=headers,
        ).json()
        workspace_b = client.get(
            f"/api/v2/workbench/projects/{project_b}/workspace",
            headers=headers,
        ).json()
        assert refresh.json()["id"] in {
            item["processingAttemptId"]
            for item in workspace_a["processingAttempts"]
        }
        assert answer_id in {
            item["processingAttemptId"]
            for item in workspace_a["processingAttempts"]
        }
        assert refresh.json()["id"] not in {
            item["processingAttemptId"]
            for item in workspace_b["processingAttempts"]
        }

    with client:
        assert client.get(
            f"/api/v2/workbench/analysis-jobs/{answer_id}",
            headers=headers,
        ).json() == analysis.json()
        assert client.get(
            f"/api/v2/workbench/projects/{project_a}/goals",
            headers=headers,
        ).json() == [goal.json()]
        assert client.get(
            f"/api/v2/workbench/projects/{project_a}/proposal-drafts",
            headers=headers,
        ).json() == [proposal.json()]

    with runtime_connection(database, "cloud", read_only=True) as connection:
        command_counts = {
            str(row["command_type"]): int(row["count"])
            for row in connection.execute(
                """
                SELECT command_type, COUNT(*) AS count
                FROM command_envelopes
                WHERE organization_id = ?
                  AND command_type IN (
                    'workbench.analysis_job.completed',
                    'workbench.context_refreshed',
                    'workbench.project_goal.created'
                  )
                GROUP BY command_type
                """,
                (identity["organizationId"],),
            ).fetchall()
        }
    assert command_counts == {
        "workbench.analysis_job.completed": 1,
        "workbench.context_refreshed": 1,
        "workbench.project_goal.created": 1,
    }


def test_analysis_backfill_uses_stable_per_project_operation_keys() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def business_snapshot(self, *, refresh: bool = False) -> dict:
            assert refresh is False
            return {
                "projects": [
                    {"projectId": "project-a"},
                    {"projectId": "project-b"},
                ]
            }

        def workbench_chat(self, **kwargs: object) -> dict:
            self.calls.append(dict(kwargs))
            return {"answer": {"answerId": "answer"}}

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

    compatibility = Compatibility()
    result = build_default_registry().dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="analysis/backfill-main-chain",
            query={},
            body={},
            idempotency_key="backfill-operation",
        ),
    )
    assert result["queuedJobs"] == 2
    assert [
        call["idempotency_key"] for call in compatibility.runtime.calls
    ] == [
        "backfill-operation:project:project-a",
        "backfill-operation:project:project-b",
    ]
    assert [
        call["source_manifest_extra"]["operationKey"]
        for call in compatibility.runtime.calls
    ] == [
        "backfill-operation:project:project-a",
        "backfill-operation:project:project-b",
    ]


def test_workspace_chat_consumes_thread_attachment_deep_mode_and_style(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        database_path = Path("/tmp/strict-local.db")

        def __init__(self) -> None:
            self.chat_call: dict[str, object] = {}

        @staticmethod
        def require_project_capability(project_id: str, capability: str) -> dict:
            assert project_id == "project-a"
            assert capability == "read"
            return {"allowed": True}

        def cloud_query(self, path: str, *, query: dict | None = None) -> object:
            assert query is None
            assert path == "/api/v2/workbench/libraries/writing_skill"
            return [
                {
                    "id": "skill-1",
                    "name": "简洁风格",
                    "distilledMd": "短句，先给结论。",
                }
            ]

        def workbench_chat_history(self, project_id: str, thread_id: str) -> list[dict]:
            assert project_id == "project-a"
            assert thread_id == "thread-a"
            return [
                {
                    "answerId": "answer-old",
                    "projectId": "project-a",
                    "question": "上一问",
                    "answerMarkdown": "上一答",
                    "sourceManifest": {"threadId": "thread-a"},
                }
            ]

        def workbench_chat(self, **kwargs: object) -> dict:
            self.chat_call = dict(kwargs)
            return {
                "answer": {
                    "answerId": "answer-new",
                    "projectId": "project-a",
                    "question": "继续回答",
                    "answerMarkdown": "已使用哨兵正文",
                    "sourceManifest": {
                        **dict(kwargs["source_manifest_extra"]),
                        "mode": kwargs["mode"],
                        "deepThinkingRequested": kwargs["deep_thinking"],
                        "documentContentIncluded": True,
                    },
                    "createdAt": utc_now(),
                }
            }

    runtime = Runtime()
    compatibility = SimpleNamespace(runtime=runtime)
    monkeypatch.setattr(
        workbench_outputs,
        "LocalProjectMaterialsRepository",
        lambda _runtime: SimpleNamespace(
            document_text=lambda _document_id: {
                "projectId": "project-a",
                "title": "哨兵资料",
                "content": "ATTACHMENT_SENTINEL_BODY",
                "contentHash": "sentinel-hash",
            }
        ),
    )
    result = build_default_registry().dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="clients/project-a/workspace/chat/start",
            query={},
            body={
                "prompt": "继续回答",
                "threadId": "thread-a",
                "workingDocumentIds": ["document-a"],
                "deepThinking": True,
                "activeSkillId": "skill-1",
                "creativityMode": "strict",
            },
            idempotency_key="chat-context-sentinel",
        ),
    )
    assert result["threadId"] == "thread-a"
    assert runtime.chat_call["deep_thinking"] is True
    assert runtime.chat_call["mode"] == "strict"
    assert runtime.chat_call["writing_style"] == "短句，先给结论。"
    assert runtime.chat_call["private_context_items"][0]["content"] == (
        "ATTACHMENT_SENTINEL_BODY"
    )
    assert runtime.chat_call["history_messages"] == [
        {"role": "user", "content": "上一问"},
        {"role": "assistant", "content": "上一答"},
    ]
    manifest = runtime.chat_call["source_manifest_extra"]
    assert manifest["threadId"] == "thread-a"
    assert manifest["selectedDocuments"][0]["contentHash"] == "sentinel-hash"
    assert "ATTACHMENT_SENTINEL_BODY" not in json.dumps(manifest)


def test_workspace_chat_persists_local_image_reference_without_archiving_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        database_path = Path("/tmp/strict-local.db")

        def __init__(self) -> None:
            self.chat_call: dict[str, object] = {}

        @staticmethod
        def require_project_capability(project_id: str, capability: str) -> dict:
            assert (project_id, capability) == ("project-a", "read")
            return {"allowed": True}

        def workbench_chat(self, **kwargs: object) -> dict:
            self.chat_call = dict(kwargs)
            return {
                "answer": {
                    "answerId": "answer-image",
                    "projectId": "project-a",
                    "question": str(kwargs["question"]),
                    "answerMarkdown": "图片已理解",
                    "sourceManifest": dict(kwargs["source_manifest_extra"]),
                    "createdAt": utc_now(),
                }
            }

        @staticmethod
        def persist_workbench_chat_images(**kwargs: object) -> list[dict]:
            images = list(kwargs["images"])
            assert kwargs["project_id"] == "project-a"
            assert kwargs["thread_id"]
            return [
                {
                    "objectId": "chat-image-local-1",
                    "name": images[0]["name"],
                    "mimeType": images[0]["mimeType"],
                    "size": len(images[0]["bytes"]),
                    "contentHash": images[0]["contentHash"],
                }
            ]

        @staticmethod
        def resolve_workbench_chat_images(receipts: object) -> list[dict]:
            receipt = list(receipts)[0]
            return [
                {
                    "id": receipt["objectId"],
                    "name": receipt["name"],
                    "mimeType": receipt["mimeType"],
                    "dataUrl": "data:image/png;base64,iVBORw0KGgo=",
                }
            ]

    runtime = Runtime()
    monkeypatch.setattr(
        workbench_outputs,
        "LocalProjectMaterialsRepository",
        lambda _runtime: SimpleNamespace(
            search_local_wiki=lambda **_kwargs: {"hits": []},
        ),
    )
    data_url = "data:image/png;base64,iVBORw0KGgo="
    result = build_default_registry().dispatch(
        SimpleNamespace(runtime=runtime),
        UiRequest(
            method="POST",
            path="clients/project-a/workspace/chat/start",
            query={},
            body={
                "prompt": "请看图说明问题",
                "imageInputs": [
                    {
                        "name": "截图.png",
                        "mimeType": "image/png",
                        "dataUrl": data_url,
                    }
                ],
            },
            idempotency_key="chat-image-transient",
        ),
    )
    assert result["assistantMessage"]["content"] == "图片已理解"
    assert runtime.chat_call["image_context_items"] == [
        {"name": "截图.png", "mimeType": "image/png", "dataUrl": data_url}
    ]
    manifest = runtime.chat_call["source_manifest_extra"]
    assert manifest["transientImageInputs"][0]["name"] == "截图.png"
    assert manifest["transientImageInputs"][0]["size"] > 0
    assert manifest["localChatImageInputs"] == [
        {
            "objectId": "chat-image-local-1",
            "name": "截图.png",
            "mimeType": "image/png",
            "size": 8,
            "contentHash": manifest["transientImageInputs"][0]["contentHash"],
        }
    ]
    assert result["userMessage"]["imageAttachments"][0]["id"] == "chat-image-local-1"
    assert data_url not in json.dumps(manifest)


def test_local_chat_image_survives_runtime_restart_without_source_asset(
    tmp_path: Path,
) -> None:
    database = tmp_path / "local" / "strict-local.db"
    runtime = WorkspaceRuntime(database, MemorySecretStore())
    sandbox_id = "sandbox-chat-image"
    scope_id = "scope-chat-image"
    principal_id = "principal-chat-image"
    membership_id = "membership-chat-image"
    organization_id = "organization-chat-image"
    now = utc_now()
    with runtime._connection() as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,record_kind,name,created_at,deleted_at,projection_state,projected_at) "
            "VALUES (?,'active',1,?,'organization','图片测试组织',?,NULL,'current',?)",
            (organization_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,principal_kind,display_name,version,lifecycle_state,created_at,deleted_at,projection_state,projected_at) "
            "VALUES (?,'active',1,?,'person','图片测试成员',1,'active',?,NULL,'current',?)",
            (principal_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,policy_version,created_at,updated_at,status,version,lifecycle_state,deleted_at,projection_state,projected_at) "
            "VALUES (?,'organization',?,1,?,?,'active',1,'active',NULL,'current',?)",
            (scope_id, organization_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,version,record_kind,visibility_scope,lifecycle_state,created_at,updated_at,deleted_at,projection_state,projected_at) "
            "VALUES (?,?,?,'member','active',1,'membership','organization','active',?,?,NULL,'current',?)",
            (membership_id, scope_id, principal_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO sandboxes (id,scope_id,principal_id,membership_id,record_kind,cloud_instance_id,database_generation_id,sandbox_kind,display_name,runtime_status,manifest_hash,version,lifecycle_state,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
            "VALUES (?,?,?,?,'sandbox','cloud-chat-image',?,'organization','图片工作空间','ready',?,1,'active',?,?,NULL,'local',?)",
            (
                sandbox_id,
                scope_id,
                principal_id,
                membership_id,
                runtime.identity.database_generation_id,
                runtime.identity.manifest_hash,
                now,
                now,
                runtime.identity.database_generation_id,
            ),
        )
        connection.commit()
    context = SimpleNamespace(sandbox_id=sandbox_id)
    runtime._current_context = lambda require_ready=True: context  # type: ignore[method-assign]
    raw = b"\x89PNG\r\n\x1a\nchat-image-sentinel"
    receipt = LocalWorkbenchChatRepository(runtime).persist_chat_images(
        project_id="project-a",
        thread_id="thread-a",
        images=[
            {
                "name": "现场截图.png",
                "mimeType": "image/png",
                "bytes": raw,
                "contentHash": hashlib.sha256(raw).hexdigest(),
            }
        ],
    )[0]

    restarted = WorkspaceRuntime(database, MemorySecretStore())
    restarted._current_context = lambda require_ready=True: context  # type: ignore[method-assign]
    restored = LocalWorkbenchChatRepository(restarted).resolve_chat_images([receipt])
    assert restored[0]["name"] == "现场截图.png"
    assert restored[0]["dataUrl"].startswith("data:image/png;base64,")
    with runtime._connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM object_manifests").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM source_assets").fetchone()[0] == 0


def test_workspace_chat_automatically_recalls_current_project_local_wiki(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        database_path = Path("/tmp/strict-local.db")

        def __init__(self) -> None:
            self.chat_call: dict[str, object] = {}

        @staticmethod
        def require_project_capability(project_id: str, capability: str) -> dict:
            assert project_id == "project-a"
            assert capability == "read"
            return {"allowed": True}

        def workbench_chat(self, **kwargs: object) -> dict:
            self.chat_call = dict(kwargs)
            return {
                "answer": {
                    "answerId": "answer-retrieved",
                    "projectId": "project-a",
                    "question": "没有照抄原文的发散问题",
                    "answerMarkdown": "已根据本机Wiki命中片段回答",
                    "sourceManifest": {
                        **dict(kwargs["source_manifest_extra"]),
                        "selectedDocumentContentCount": 1,
                        "localRetrievedDocumentCount": 1,
                        "documentContentIncluded": True,
                    },
                    "createdAt": utc_now(),
                }
            }

    runtime = Runtime()
    store = SimpleNamespace(
        search_local_wiki=lambda **kwargs: {
            "hits": [
                {
                    "documentId": "document-wiki",
                    "title": "日慈会议资料",
                    "excerpt": "会议讨论了匿名参与和现场反馈。",
                    "retrievalMode": "local_sparse_vector",
                    "score": 0.91,
                    "chunkId": "chunk-wiki",
                    "factId": "fact-wiki",
                    "evidenceId": "evidence-wiki",
                }
            ]
        },
        document_text=lambda _document_id: {
            "projectId": "project-a",
            "title": "日慈会议资料",
            "content": "完整正文不应进入安全来源清单。",
            "contentHash": "wiki-content-hash",
        },
    )
    monkeypatch.setattr(
        workbench_outputs,
        "LocalProjectMaterialsRepository",
        lambda _runtime: store,
    )
    result = build_default_registry().dispatch(
        SimpleNamespace(runtime=runtime),
        UiRequest(
            method="POST",
            path="clients/project-a/workspace/chat/start",
            query={},
            body={"prompt": "没有照抄原文的发散问题"},
            idempotency_key="chat-auto-wiki-recall",
        ),
    )
    assert result["assistantMessage"]["evidence"][0]["sourceId"] == "document-wiki"
    assert runtime.chat_call["private_context_items"][0]["content"] == (
        "会议讨论了匿名参与和现场反馈。"
    )
    manifest = runtime.chat_call["source_manifest_extra"]
    assert manifest["selectedDocuments"] == []
    assert manifest["retrievedDocuments"][0]["retrievalMode"] == "local_sparse_vector"
    assert manifest["retrievedDocuments"][0]["evidenceIds"] == ["evidence-wiki"]
    assert "完整正文不应进入安全来源清单" not in json.dumps(manifest)


def test_mobile_proposals_and_context_refresh_use_real_v2_projections() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.commands: list[tuple[str, str, dict, str]] = []

        def cloud_query(self, path: str, *, query: dict | None = None) -> object:
            assert query is None
            responses: dict[str, object] = {
                "/api/v2/workbench/projects/project-a/workspace": {
                    "project": {
                        "projectId": "project-a",
                        "name": "日慈基金会",
                        "summary": "儿童社会情感能力项目",
                        "lifecycleState": "active",
                    },
                    "documents": [
                        {
                            "documentId": "document-a",
                            "title": "项目背景",
                            "parseState": "ready",
                        }
                    ],
                    "reports": [],
                    "answers": [],
                    "tasks": [
                        {
                            "taskId": "task-a",
                            "title": "准备项目沟通",
                            "lifecycleState": "todo",
                            "dueDate": "2026-08-01",
                            "collaborators": [
                                {
                                    "role": "owner",
                                    "displayName": "林佳维",
                                }
                            ],
                        }
                    ],
                    "eventLines": [
                        {
                            "eventLineId": "event-line-a",
                            "name": "项目推进",
                            "background": "准备项目沟通",
                            "goal": "确认下一步",
                            "lifecycleState": "active",
                            "attachmentCount": 1,
                        }
                    ],
                },
                "/api/v2/workbench/projects/project-a/knowledge-status": {
                    "counts": {"ready": 1},
                    "documents": [{"documentId": "document-a"}],
                    "processingAttempts": [],
                },
                "/api/v2/workbench/projects/project-a/insights": {
                    "judgments": [],
                    "openQuestions": [],
                    "conflicts": [],
                },
                "/api/v2/workbench/projects/project-a/proposal-drafts": [
                    {
                        "id": "proposal-a",
                        "status": "draft",
                        "title": "补充项目背景",
                    }
                ],
            }
            if path not in responses:
                raise AssertionError(path)
            return responses[path]

        def project_knowledge_context(self, project_id: str) -> dict:
            assert project_id == "project-a"
            return {
                "state": {"overall": "ready"},
                "counts": {
                    "organizationShared": 1,
                    "localPrivate": 1,
                },
            }

        def cloud_command(
            self,
            method: str,
            path: str,
            *,
            payload: dict,
            idempotency_key: str,
            refresh_business: bool = True,
        ) -> dict:
            assert refresh_business is True
            self.commands.append((method, path, payload, idempotency_key))
            return {
                "id": "refresh-a",
                "status": "completed",
                **payload,
            }

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

    compatibility = Compatibility()
    registry = build_default_registry()
    mobile = registry.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="clients/project-a/data-center/mobile-snapshot",
            query={},
            body={},
            idempotency_key="mobile-projection",
        ),
    )
    assert mobile["proposalDraftSummary"]["total"] == 1
    assert mobile["openProposalSummary"]["items"][0]["id"] == "proposal-a"

    clarification = registry.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="clients/project-a/clarification-context",
            query={},
            body={},
            idempotency_key="clarification-context-ui",
        ),
    )
    assert clarification["profile"]["name"] == "日慈基金会"
    assert clarification["strictResourceStates"] == {
        "profile": "ready",
        "eventLines": "ready",
        "documents": "ready",
        "timeline": "ready",
        "peopleCandidates": "ready",
        "commitments": "ready",
    }
    assert clarification["peopleCandidates"][0]["name"] == "林佳维"
    assert clarification["commitments"][0]["id"] == "task-a"

    readiness = registry.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="clients/project-a/workspace/data-center-readiness/actions",
            query={},
            body={"action": "reindex"},
            idempotency_key="readiness-reindex-ui",
        ),
    )
    assert readiness["status"] == "completed"
    assert readiness["retrievalMode"] == "strict_relational_context"
    assert readiness["masterIndexed"] == 1

    refreshed = registry.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="clients/project-a/workspace/context-refresh-events",
            query={},
            body={},
            idempotency_key="context-refresh-ui",
        ),
    )
    assert refreshed["status"] == "completed"
    assert compatibility.runtime.commands == [
        (
            "POST",
            "/api/v2/workbench/projects/project-a/context-refresh-events",
            {
                "state": "ready",
                "counts": {
                    "organizationShared": 1,
                    "localPrivate": 1,
                },
                "materialPackHash": refreshed["materialPackHash"],
            },
            "context-refresh-ui",
        )
    ]
    assert len(refreshed["materialPackHash"]) == 64


def test_published_meeting_excludes_member_local_raw_content() -> None:
    published = _published_meeting_payload(
        {
            "id": "meeting-1",
            "title": "项目复盘",
            "summary": "可共享摘要",
            "transcriptText": "不得上传的逐字稿",
            "notes": "不得上传的私人笔记",
            "ambiguities": [{"summary": "尚未确认"}],
            "decisions": [{"id": "d1", "summary": "已确认决定"}],
        }
    )
    serialized = json.dumps(published, ensure_ascii=False)
    assert "不得上传的逐字稿" not in serialized
    assert "不得上传的私人笔记" not in serialized
    assert "尚未确认" not in serialized
    assert published["decisions"] == [{"id": "d1", "summary": "已确认决定"}]
    assert published["materialBoundary"] == {
        "sourceFileUploaded": False,
        "rawTranscriptUploaded": False,
        "rawNotesUploaded": False,
        "unresolvedAmbiguitiesUploaded": False,
    }


def test_launch_feishu_meeting_saves_local_draft_and_reports_real_blocker(
    tmp_path: Path,
) -> None:
    runtime = WorkspaceRuntime(
        tmp_path / "local" / "strict-local.db",
        MemorySecretStore(),
    )
    sandbox_id = runtime.current()["sandbox"]["sandboxId"]
    runtime._current_context = lambda require_ready=True: SimpleNamespace(  # type: ignore[method-assign]
        sandbox_id=sandbox_id,
        cloud_instance_id="cloud-workbench-test",
        organization_id="organization-workbench-test",
        cloud_api_url="https://workbench-test.invalid",
        principal_id="principal-workbench-test",
        membership_id="membership-workbench-test",
    )
    cloud_calls: list[tuple[str, dict[str, Any]]] = []

    def cloud_command(
        method: str,
        path: str,
        *,
        payload: dict[str, Any],
        idempotency_key: str,
        refresh_business: bool = True,
    ) -> dict:
        del method, idempotency_key, refresh_business
        cloud_calls.append((path, payload))
        if payload.get("resourcePath") == "me/feishu-message/send":
            return {
                "result": {
                    "state": "blocked",
                    "status": "skipped",
                    "message": "当前成员尚未完成飞书授权",
                    "retryable": True,
                    "deliveryMode": "none",
                    "deliveryTarget": None,
                }
            }
        raise AssertionError((path, payload))

    runtime.cloud_command = cloud_command  # type: ignore[method-assign]
    compatibility = SimpleNamespace(runtime=runtime)
    launched = build_default_registry().dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="clients/project-1/meetings/launch-feishu",
            query={},
            body={
                "title": "日慈项目周会",
                "scheduledAt": "2026-08-05T10:00",
                "sourceTaskId": "task-1",
            },
            idempotency_key="launch-feishu-meeting-1",
        ),
    )

    assert launched["deliveryStatus"] == "skipped"
    assert launched["deliveryMode"] == "none"
    assert "当前成员尚未完成飞书授权" in launched["deliveryMessage"]
    assert launched["meeting"]["sourceScope"] == "local_private"
    stored = LocalProjectMaterialsRepository(runtime).meeting(
        "project-1",
        launched["meeting"]["id"],
    )
    assert stored["title"] == "日慈项目周会"
    assert stored["sourceTaskId"] == "task-1"
    assert cloud_calls == [
        (
            "/api/v2/platform-integrations/command",
            {
                "resourcePath": "me/feishu-message/send",
                "authorizationScope": "personal",
                "method": "POST",
                "query": {},
                "payload": {
                    "text": launched["noticeText"],
                    "localType": "meeting",
                    "localId": launched["meeting"]["id"],
                },
            },
        ),
    ]


def test_launch_feishu_meeting_transport_failure_uses_valid_retryable_dto(
    tmp_path: Path,
) -> None:
    runtime = WorkspaceRuntime(
        tmp_path / "local" / "strict-local.db",
        MemorySecretStore(),
    )
    sandbox_id = runtime.current()["sandbox"]["sandboxId"]
    runtime._current_context = lambda require_ready=True: SimpleNamespace(  # type: ignore[method-assign]
        sandbox_id=sandbox_id,
        cloud_instance_id="cloud-workbench-test",
        organization_id="organization-workbench-test",
        cloud_api_url="https://workbench-test.invalid",
        principal_id="principal-workbench-test",
        membership_id="membership-workbench-test",
    )

    def unavailable(*_args: Any, **_kwargs: Any) -> dict:
        raise LocalRuntimeError(
            503,
            "organization_cloud_unavailable",
            "组织云暂时无响应",
        )

    runtime.cloud_command = unavailable  # type: ignore[method-assign]
    launched = build_default_registry().dispatch(
        SimpleNamespace(runtime=runtime),
        UiRequest(
            method="POST",
            path="clients/project-1/meetings/launch-feishu",
            query={},
            body={"title": "日慈项目故障保留草稿"},
            idempotency_key="launch-feishu-meeting-failed-1",
        ),
    )

    assert launched["deliveryStatus"] == "failed"
    assert launched["state"] == "failed_retryable"
    assert launched["retryable"] is True
    assert LocalProjectMaterialsRepository(runtime).meeting(
        "project-1",
        launched["meeting"]["id"],
    )["title"] == "日慈项目故障保留草稿"


def test_explicit_report_save_keeps_local_paths_out_of_cloud(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        identity, headers = _bootstrap(client)
        project_id = _project_id(client, headers)
        report_id = new_id()
        response = client.post(
            "/api/v2/workbench/reports",
            headers={**headers, "Idempotency-Key": "report-explicit-save"},
            json={
                "reportId": report_id,
                "projectId": project_id,
                "title": "明确保存的报告",
                "contentMarkdown": "# 共享正文",
                "contentJson": {
                    "sections": [{"markdown": "共享章节"}],
                    "nested": {
                        "managedPath": "/Users/member/private/source.docx",
                        "sourceLocator": "strict-local-storage:secret",
                        "allowed": "共享摘要",
                    },
                },
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["id"] == report_id
    with runtime_connection(database, "cloud", read_only=True) as connection:
        content_json = connection.execute(
            """
            SELECT content_json
            FROM narrative_output_versions
            WHERE organization_id = ? AND narrative_output_id = ?
            """,
            (identity["organizationId"], report_id),
        ).fetchone()["content_json"]
        command_payload = connection.execute(
            """
            SELECT payload_json
            FROM command_envelopes
            WHERE organization_id = ? AND aggregate_id = ?
            """,
            (identity["organizationId"], report_id),
        ).fetchone()["payload_json"]
    assert "/Users/member/private/source.docx" not in content_json
    assert "strict-local-storage:secret" not in content_json
    assert "/Users/member/private/source.docx" not in command_payload
    assert "strict-local-storage:secret" not in command_payload
    assert "共享摘要" in content_json


def test_project_narrative_without_saved_report_is_truthful_projection(
    tmp_path: Path,
) -> None:
    client, _ = _cloud(tmp_path)
    with client:
        _, headers = _bootstrap(client)
        project_id = _project_id(client, headers)
        response = client.get(
            f"/api/v2/workbench/projects/{project_id}/narrative",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["generator"] == "strict_project_metadata_projection"
        assert payload["lifecycleState"] == "not_connected"
        assert payload["aggregateVersion"] == 0
        assert payload["dataLayerGaps"] == ["尚无已保存的严格新版叙事产物"]


def test_verified_answer_correction_maps_to_project_narrative_profile() -> None:
    statement = "客户负责人应以成员本次确认的最新信息为准。"
    updates = _profile_updates_from_correction_rows(
        [
            {
                "id": "fact-profile-correction",
                "version": 2,
                "updated_at": "2026-08-06T12:00:00Z",
                "source_answer_id": "answer-profile-correction",
                "receipt": json.dumps(
                    {
                        "correctionKind": "correction",
                        "statement": statement,
                    },
                    ensure_ascii=False,
                ),
            }
        ]
    )
    assert updates == [
        {
            "id": "fact-profile-correction",
            "updateKind": "correction",
            "title": "人工纠错",
            "statement": statement,
            "authority": "organization_cloud",
            "visibility": "organization",
            "incorporationState": "formal_fact_ready",
            "sourceAnswerId": "answer-profile-correction",
            "version": 2,
            "updatedAt": "2026-08-06T12:00:00Z",
        }
    ]


def test_unified_todo_cancel_is_a_real_cas_transition(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        identity, headers = _bootstrap(client)
        project_id = _project_id(client, headers)
        created = client.post(
            "/api/v2/tasks",
            headers={**headers, "Idempotency-Key": "todo-cancel-create"},
            json={"title": "待取消任务", "projectId": project_id},
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["task"]["taskId"]
        cancelled = client.post(
            f"/api/v2/workbench/projects/{project_id}/todos/{task_id}/cancel",
            headers={**headers, "Idempotency-Key": "todo-cancel"},
        )
        replay = client.post(
            f"/api/v2/workbench/projects/{project_id}/todos/{task_id}/cancel",
            headers={**headers, "Idempotency-Key": "todo-cancel"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert replay.json() == cancelled.json()
        assert cancelled.json()["task"]["lifecycleState"] == "cancelled"
        detail = client.get(f"/api/v2/tasks/{task_id}", headers=headers)
        assert detail.json()["task"]["lifecycleState"] == "cancelled"
    with runtime_connection(database, "cloud", read_only=True) as connection:
        envelope = connection.execute(
            """
            SELECT expected_version, status
            FROM command_envelopes
            WHERE organization_id = ? AND idempotency_key = 'todo-cancel'
            """,
            (identity["organizationId"],),
        ).fetchone()
    assert (envelope["expected_version"], envelope["status"]) == (1, "committed")


def test_analysis_run_cancel_uses_processing_attempt_state_cas(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        identity, headers = _bootstrap(client)
        project_id = _project_id(client, headers)
        document = client.put(
            f"/api/v2/workbench/projects/{project_id}/dna/organization_intro",
            headers={**headers, "Idempotency-Key": "analysis-source"},
            json={"markdownContent": "分析来源", "expectedVersion": 0},
        )
        assert document.status_code == 200, document.text
        run_id = new_id()
        now = utc_now()
        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                INSERT INTO processing_attempts (
                    processing_attempt_id, organization_id, source_asset_id,
                    document_id, processing_kind, state, attempt_no,
                    error_code, error_message, started_at, finished_at,
                    created_at
                ) VALUES (?, ?, NULL, ?, 'analysis_summary', 'processing', 1,
                          '', '', ?, NULL, ?)
                """,
                (
                    run_id,
                    identity["organizationId"],
                    document.json()["documentId"],
                    now,
                    now,
                ),
            )
            connection.commit()
        cancelled = client.post(
            (
                f"/api/v2/workbench/projects/{project_id}/"
                f"analysis-runs/{run_id}/cancel"
            ),
            headers={**headers, "Idempotency-Key": "analysis-cancel"},
        )
        replay = client.post(
            (
                f"/api/v2/workbench/projects/{project_id}/"
                f"analysis-runs/{run_id}/cancel"
            ),
            headers={**headers, "Idempotency-Key": "analysis-cancel"},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert replay.json() == cancelled.json()
        assert cancelled.json()["state"] == "cancelled"
    with runtime_connection(database, "cloud", read_only=True) as connection:
        row = connection.execute(
            """
            SELECT state, finished_at FROM processing_attempts
            WHERE processing_attempt_id = ?
            """,
            (run_id,),
        ).fetchone()
        commands = connection.execute(
            """
            SELECT COUNT(*) FROM command_envelopes
            WHERE organization_id = ? AND idempotency_key = 'analysis-cancel'
            """,
            (identity["organizationId"],),
        ).fetchone()[0]
    assert row["state"] == "cancelled"
    assert row["finished_at"]
    assert commands == 1


def test_chat_message_maps_actual_local_sources_without_false_warning() -> None:
    _, assistant = workbench_outputs._chat_messages(
        {
            "answerId": "answer-source",
            "question": "哨兵问题",
            "answerMarkdown": "哨兵回答",
            "sourceManifest": {
                "threadId": "thread-source",
                "documentContentIncluded": True,
                "selectedDocumentContentCount": 1,
                "projectKnowledgeSummaryCount": 2,
                "materialAccessMode": "mixed",
                "memoryState": "failed_retryable",
                "memoryMessage": "组织知识读取失败，可稍后重试",
                "selectedDocuments": [
                    {
                        "documentId": "document-source",
                        "title": "日慈项目资料",
                        "contentHash": "sentinel-content-hash",
                    }
                ],
            },
        }
    )
    assert assistant["evidenceStatus"] == "sufficient"
    assert assistant["retrievalSummary"]["materialAccessMode"] == "mixed"
    assert assistant["retrievalSummary"]["memoryState"] == "failed_retryable"
    assert assistant["retrievalSummary"]["memoryMessage"] == (
        "组织知识读取失败，可稍后重试"
    )
    assert assistant["retrievalSummary"]["linkedEvidenceCount"] == 1
    assert assistant["evidence"][0]["sourceId"] == "document-source"


def test_data_center_readiness_uses_cloud_documents_without_name_error() -> None:
    runtime = SimpleNamespace(
        cloud_query=lambda path, query=None: {
            "state": "ready",
            "counts": {"ready": 1, "total": 1},
            "documents": [
                {"documentId": "document-ready", "parseStatus": "ready"}
            ],
            "processingAttempts": [],
            "generatedAt": "2026-07-31T00:00:00Z",
        }
    )
    result = build_default_registry().dispatch(
        SimpleNamespace(runtime=runtime),
        UiRequest(
            method="GET",
            path="clients/project-a/workspace/data-center-readiness",
            query={},
            body={},
            idempotency_key="",
        ),
    )
    assert result["ready"] is True
    assert result["documents"][0]["documentId"] == "document-ready"


def test_knowledge_search_reads_current_project_local_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        database_path = Path("/tmp/strict-local-search.db")

        @staticmethod
        def require_project_capability(project_id: str, capability: str) -> dict:
            assert project_id == "project-a"
            assert capability == "read"
            return {"allowed": True}

        @staticmethod
        def project_knowledge_context(project_id: str) -> dict:
            assert project_id == "project-a"
            return {
                "organizationSharedKnowledge": [],
                "localPrivateKnowledge": [],
                "state": {"overall": "ready"},
            }

    store = SimpleNamespace(
        search_local_wiki=lambda **kwargs: {
            "projectId": kwargs["project_id"],
            "drillthroughUsed": True,
            "hits": [
                {
                    "documentId": "document-local",
                    "title": "日慈项目背景",
                    "path": "/tmp/local-only.docx",
                    "sourceType": "local_document",
                    "stage": "raw_chunk",
                    "excerpt": "本机正文包含唯一哨兵词 LOCAL_SEARCH_SENTINEL。",
                    "score": 1.0,
                }
            ],
        },
        knowledge_presentation=lambda _project_id: {
            "savedMemories": [],
            "relationshipCards": [],
        },
    )
    monkeypatch.setattr(
        project_materials_ui,
        "GC07LocalProjectMaterialsRepository",
        lambda _runtime: store,
    )
    result = build_default_registry().dispatch(
        SimpleNamespace(runtime=Runtime()),
        UiRequest(
            method="POST",
            path="clients/project-a/knowledge/search",
            query={},
            body={"prompt": "LOCAL_SEARCH_SENTINEL"},
            idempotency_key="local-search",
        ),
    )
    assert result["rawChunkHitCount"] == 1
    assert result["drillthroughUsed"] is True
    assert result["hits"][0]["sourceType"] == "local_document"
    assert result["hits"][0]["path"] == "/tmp/local-only.docx"


def test_workspace_file_list_excludes_cloud_metadata_only_documents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        database_path = Path("/tmp/strict-local-workspace.db")

        @staticmethod
        def cloud_query(path: str, *, query: dict | None = None) -> object:
            if path == "/api/v2/gc06/meetings":
                assert query == {"clientId": "project-a"}
                return [
                    {
                        "id": "strict-meeting-a",
                        "clientId": "project-a",
                        "title": "正式客户会议",
                        "startsAt": "2026-08-12T09:00:00+08:00",
                        "status": "scheduled",
                        "version": 2,
                        "lifecycleState": "active",
                        "updatedAt": "2026-08-08T12:00:00Z",
                    }
                ]
            assert query is None
            suffix = path.rsplit("/", 1)[-1]
            if suffix == "workspace":
                return {
                    "project": {
                        "projectId": "project-a",
                        "name": "日慈基金会",
                        "lifecycleState": "active",
                    },
                    "documents": [],
                    "answers": [],
                    "favorites": [],
                    "reports": [],
                    "tasks": [],
                    "eventLines": [],
                    "processingAttempts": [],
                }
            if suffix == "knowledge-status":
                return {
                    "projectId": "project-a",
                    "state": "ready",
                    "documents": [
                        {
                            "documentId": f"cloud-only-{index}",
                            "title": f"云摘要 {index}",
                            "parseState": "ready",
                        }
                        for index in range(249)
                    ],
                    "counts": {"total": 249, "ready": 249},
                    "processingAttempts": [],
                }
            if suffix == "dna":
                return {"modules": []}
            if suffix == "texts":
                return {}
            if suffix == "structure":
                return {"modules": [], "flows": []}
            if suffix == "insights":
                return {
                    "judgments": [],
                    "topics": [],
                    "conflicts": [],
                    "openQuestions": [],
                }
            if suffix == "goals":
                return []
            raise AssertionError(path)

        @staticmethod
        def project_knowledge_context(project_id: str) -> dict:
            return {
                "project": {"projectId": project_id},
                "organizationSharedKnowledge": [{"sourceId": "summary-a"}],
                "localPrivateKnowledge": [{"sourceId": "local-a"}],
                "counts": {"organizationShared": 1, "localPrivate": 1},
                "state": {
                    "overall": "ready",
                    "organizationShared": "ready",
                    "localPrivate": "ready",
                },
            }

    local_store = SimpleNamespace(
        folders=lambda project_id: [],
        documents=lambda project_id: [
            {
                "id": "local-openable",
                "clientId": project_id,
                "title": "当前设备资料.docx",
                "path": "/tmp/current-device.docx",
                "source": "member_local",
            }
        ],
        meetings=lambda project_id: [],
    )
    monkeypatch.setattr(
        workbench_outputs,
        "LocalProjectMaterialsRepository",
        lambda _runtime: local_store,
    )
    projected: list[dict] = []

    class PlanningProjection:
        def __init__(self, _runtime: object) -> None:
            pass

        @staticmethod
        def apply_meetings(rows: list[dict]) -> None:
            projected.extend(rows)

        @staticmethod
        def list_meetings(*, client_id: str | None = None) -> list[dict]:
            assert client_id == "project-a"
            return projected

    monkeypatch.setattr(
        workbench_outputs,
        "LocalGC06PlanningProjection",
        PlanningProjection,
    )
    workspace = workbench_outputs._workspace(
        SimpleNamespace(runtime=Runtime()),
        "project-a",
    )
    assert [item["id"] for item in workspace["documents"]] == [
        "local-openable"
    ]
    assert [item["id"] for item in workspace["meetings"]] == [
        "strict-meeting-a"
    ]
    assert workspace["meetings"][0]["sourceScope"] == "strict_meeting_projection"
    assert workspace["knowledgeStatus"]["totalDocuments"] == 249
