from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.app.ui_domains import UiRequest
from backend.app.ui_domains import workbench_outputs
from cloud_backend.app.repositories.task_planning_agent import (
    TaskPlanningAgentRepository,
    _concise_domain_aliases,
    _filter_internal_people_and_organizations,
    _filter_profile_specific_terms,
    _manual_supplements,
    _sanitize_identity_terms,
    _task_matching_keywords,
    _keywords,
)
from cloud_backend.app.repositories.gc12_corrections import (
    create_strategic_profile_clarification,
)
from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import utc_now
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository


def test_task_planning_keyword_profile_uses_versioned_88_table_objects(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    now = utc_now()
    bot_id = builtin_agent_id(identity.organization_id, "task_planning")
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,"
            "resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
            "VALUES (?,?,?,'active',1,?,?,?,NULL,'cloud',?)",
            (
                bot_id,
                identity.scope_id,
                "bot_definition",
                "builtin_function_agent",
                now,
                now,
                identity.cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO bot_definitions (id,scope_id,agent_kind,version,handle,description,"
            "enabled,lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,1,?,?,1,'active',?,?,NULL)",
            (bot_id, identity.scope_id, "task_planning", "task-planning", "任务计划", now, now),
        )
        connection.execute(
            "UPDATE clients SET alias='日慈',domain='公益项目',summary='教师培训与数字化项目' "
            "WHERE id=? AND scope_id=?",
            (payload["projectId"], identity.scope_id),
        )
        connection.commit()

    agent = TaskPlanningAgentRepository(repository)
    before = agent.list_profiles(identity)
    assert before[0]["state"] == "not_built"
    result = agent.refresh_profile(
        identity,
        client_id=payload["projectId"],
        payload={"keywords": ["软件", "官网"]},
        idempotency_key="task-profile-one",
    )
    assert result["state"] == "ready"
    assert result["agentRun"]["agentKind"] == "task_planning"
    assert "日慈" in result["keywords"]
    assert "软件" not in result["keywords"]
    assert "官网" not in result["keywords"]
    assert result["categories"]["identityTerms"] == ["GC14项目", "日慈"]
    assert "taskRoutingTerms" not in result["categories"]
    assert result["sourceSummary"]["verifiedFactCount"] == 0
    assert result["generationState"] == "rules_only"

    after = agent.list_profiles(identity)
    assert after[0]["state"] == "ready"
    assert after[0]["version"] == 1
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM narrative_outputs WHERE artifact_kind='project_keyword_profile'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs WHERE bot_id=? AND run_kind='project_keyword_profile_refresh'",
            (bot_id,),
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0] == 88


def test_task_planning_profile_does_not_turn_summary_or_article_titles_into_keywords() -> None:
    terms = _keywords(
        {
            "name": "益语智库",
            "alias": "益语",
            "domain": "公益咨询",
            "summary": "《老人与海》与单向度的人只是资料标题，不应成为项目识别词",
        },
        ["官网", "战略陪伴"],
    )
    assert terms == ["益语智库", "益语", "公益咨询", "官网", "战略陪伴"]


def test_task_planning_profile_derives_common_terms_from_verified_compound_terms() -> None:
    aliases = _concise_domain_aliases(
        {
            "identityTerms": ["日慈基金会"],
            "peopleAndOrganizations": [],
            "productsAndPrograms": ["心灵魔法学院教师培训试点"],
            "domainTerms": ["儿童青少年心理教育"],
            "asrTerms": [],
        }
    )
    assert aliases["domainTerms"] == ["儿童心理", "青少年心理", "心理教育", "教师培训"]


def test_task_matching_uses_project_facts_not_asr_or_generic_routing_terms() -> None:
    categories = {
        "identityTerms": ["日慈基金会", "日慈"],
        "peopleAndOrganizations": ["张真"],
        "productsAndPrograms": ["心灵魔法学院"],
        "domainTerms": ["儿童心理"],
        "asrTerms": ["飞书表单", "工作", "关怀员"],
    }
    assert _task_matching_keywords(categories, ["关怀员"]) == [
        "日慈基金会",
        "日慈",
        "张真",
        "心灵魔法学院",
        "儿童心理",
        "关怀员",
    ]


def test_project_identity_and_people_exclude_external_partners() -> None:
    client = {"name": "日慈基金会", "alias": "日慈", "domain": "公益项目"}
    facts = [
        {
            "term": "张真",
            "attributeName": "秘书长",
            "statement": "张真是日慈基金会秘书长",
        },
        {
            "term": "顾源源",
            "attributeName": "外部顾问",
            "statement": "顾源源为外部顾问，来自益语智库，曾入选福布斯榜单",
        },
        {
            "term": "张真",
            "attributeName": "从业经历",
            "statement": "张真曾与其他基金会合作并获福布斯报道",
        },
        {
            "term": "行动者宣言",
            "attributeName": "官网页面标题",
            "statement": "官网页面“行动者宣言”介绍项目理念",
        },
    ]
    assert _sanitize_identity_terms(
        client,
        facts,
        ["益语智库", "日慈行动者宣言", "Open Source for Actioners"],
    ) == [
        "日慈基金会",
        "日慈",
    ]
    assert _filter_internal_people_and_organizations(
        ["张真", "顾源源", "益语智库", "福布斯", "其他基金会"], facts
    ) == ["张真"]


def test_six_card_people_supplement_keeps_client_side_and_excludes_service_side() -> None:
    strategic_profile = {
        "dimensions": [
            {
                "dimension": "people",
                "narrative": (
                    "王强：心灵魔法学院项目负责人，现兼顾心盛计划。"
                    "顾源源：益语方人员，参与战略陪伴定向会。"
                ),
            }
        ]
    }
    assert _filter_internal_people_and_organizations(
        ["王强", "顾源源"],
        [],
        strategic_profile,
    ) == ["王强"]


def test_six_card_keywords_exclude_tools_codes_and_generic_delivery_terms() -> None:
    evidence = "心灵魔法学院开展儿童心理教师培训，使用飞书表单，合同编码YY-2025-C0821。"
    assert _filter_profile_specific_terms(
        ["心灵魔法学院", "儿童心理", "教师培训", "飞书表单", "合同编码", "YY-2025-C0821"],
        evidence_text=evidence,
    ) == ["心灵魔法学院", "儿童心理", "教师培训"]


def test_manual_project_keyword_supplements_are_a_separate_category() -> None:
    assert _manual_supplements(
        [
            {"statement": "项目识别关键词补充：关怀员、心盛计划"},
            {"statement": "无关的正式事实"},
        ]
    ) == ["关怀员", "心盛计划"]


def test_keyword_supplement_success_does_not_depend_on_local_dossier_rebuild(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        workbench_outputs,
        "_cloud_command",
        lambda *_args, **_kwargs: {"id": "clarification-1", "status": "answered"},
    )

    class Runtime:
        def workbench_rebuild_strategic_profile(self, **_kwargs):
            raise AssertionError("keyword supplement must not rebuild the dossier inline")

    result = workbench_outputs.router.dispatch(
        SimpleNamespace(runtime=Runtime()),
        UiRequest(
            method="POST",
            path="clients/client-a/narrative/clarifications",
            query={},
            body={
                "dimension": "essence",
                "answer": "项目识别关键词补充：关怀员",
                "feedbackKind": "project_keyword_supplement",
            },
            idempotency_key="keyword-supplement-1",
        ),
    )
    assert result["status"] == "answered"
    assert result["profileUpdate"] == {
        "state": "pending_refresh",
        "retryable": False,
    }


def test_keyword_supplement_cloud_write_does_not_require_strategic_profile(
    tmp_path: Path,
) -> None:
    repository, identity, payload = _repository(tmp_path)
    result = create_strategic_profile_clarification(
        repository,
        identity,
        project_id=payload["projectId"],
        payload={
            "dimension": "essence",
            "answer": "项目识别关键词补充：软件",
            "question": "补充项目关键词",
            "feedbackKind": "project_keyword_supplement",
        },
        idempotency_key="keyword-supplement-without-dossier",
    )

    assert result["status"] == "applied"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM narrative_outputs "
            "WHERE artifact_kind='strategic_profile'"
        ).fetchone()[0] == 0
        source = connection.execute(
            "SELECT source_object_id, source_object_kind FROM source_set_members "
            "WHERE source_set_id=(SELECT source_set_id FROM atomic_facts WHERE id=?)",
            (result["id"],),
        ).fetchone()
        assert tuple(source) == (payload["projectId"], "client")
        evidence = connection.execute(
            "SELECT source_object_id, source_object_kind, locator_kind "
            "FROM evidence_links WHERE fact_id=?",
            (result["id"],),
        ).fetchone()
        assert tuple(evidence) == (
            payload["projectId"],
            "client",
            "project_keyword_supplement",
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_task_planning_parse_returns_draft_without_creating_task(tmp_path: Path, monkeypatch) -> None:
    repository, identity, payload = _repository(tmp_path)
    now = utc_now()
    bot_id = builtin_agent_id(identity.organization_id, "task_planning")
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,"
            "resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
            "VALUES (?,?,?,'active',1,?,?,?,NULL,'cloud',?)",
            (bot_id, identity.scope_id, "bot_definition", "builtin_function_agent", now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO bot_definitions (id,scope_id,agent_kind,version,handle,description,enabled,"
            "lifecycle_state,created_at,updated_at,deleted_at) VALUES (?,?,?,1,?,?,1,'active',?,?,NULL)",
            (bot_id, identity.scope_id, "task_planning", "task-planning", "任务计划", now, now),
        )
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,principal_kind,display_name,"
            "version,lifecycle_state,created_at,deleted_at) VALUES "
            "('principal_helper','active',1,?,'person','乐乐',1,'active',?,NULL)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,version,"
            "record_kind,visibility_scope,lifecycle_state,created_at,updated_at,deleted_at) VALUES "
            "('membership_helper',?,'principal_helper','member','active',1,'membership','organization',"
            "'active',?,?,NULL)",
            (identity.scope_id, now, now),
        )
        connection.commit()
    monkeypatch.setattr(repository, "ai_config", lambda *_args, **_kwargs: {
        "status": "ready", "apiKey": "test", "baseUrl": "http://model.example/v1", "modelName": "test-model"
    })
    class Response:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": (
                '{"recordMode":"task","title":"整理项目资料","description":"形成摘要",'
                '"date":null,"start":null,"end":null,"priority":"normal",'
                f'"clientId":"{payload["projectId"]}","planningCycleId":null,'
                f'"ownerMembershipId":null,"collaboratorMembershipIds":["{identity.membership_id}"],'
                '"reasons":["项目名称明确"]}'
            )}}]}
    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def post(self, *args, **kwargs): return Response()
    monkeypatch.setattr("cloud_backend.app.repositories.task_planning_agent.httpx.Client", Client)
    result = TaskPlanningAgentRepository(repository).parse_draft(
        identity, payload={"text": "由GC14测试成员负责，后续由乐乐协助测试，今天下午整理这个项目的资料", "currentDate": "2026-08-13"}
    )
    assert result["clientId"] == payload["projectId"]
    assert result["priority"] == "normal"
    assert result["date"] == "2026-08-13"
    assert result["start"] == "15:00"
    assert result["ownerMembershipId"] == identity.membership_id
    assert result["collaboratorMembershipIds"] == ["membership_helper"]
    assert result["agentRun"]["state"] == "completed"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM execution_runs WHERE run_kind='task_draft_parse'").fetchone()[0] == 1
