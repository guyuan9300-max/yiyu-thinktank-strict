from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet
import pytest

from cloud_backend.app.models import AiAnswerSaveRequest
from cloud_backend.app.repositories.gc14_strategic_profile import (
    _normalize_prepared_profile,
    rebuild_strategic_profile,
)
from cloud_backend.app.repositories.gc12_corrections import (
    create_strategic_profile_clarification,
    list_strategic_profile_clarifications,
)
from cloud_backend.app.repositories.workbench_outputs import answer_task_action
from cloud_backend.app.repository import CloudRepository, SessionIdentity
from backend.app.runtime import LocalRuntimeError
from backend.app.workbench_chat_local import LocalWorkbenchChatRepository
from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import utc_now
from strict_common.ids import sha256_text
from strict_common.schema import initialize_database, runtime_connection


def test_gc15_memory_only_answer_receipt_contract() -> None:
    payload = {
        "answerId": "answer_gc15_memory",
        "projectId": "client_gc15_memory",
        "threadId": "thread_gc15_memory",
        "questionHash": "a" * 64,
        "answerHash": "b" * 64,
        "sourceSetId": "source_set_gc15_memory",
        "contextManifestId": "context_gc15_memory",
        "lineageId": "lineage_gc15_memory",
        "botId": "bot_gc15_memory",
        "providerResourceId": "provider_gc15_memory",
        "modelName": "model-gc15-memory",
        "sourceCount": 1,
        "materialAccessMode": "memory_context",
        "boundaryState": "member_local_memory_context",
        "selectedSources": [
            {
                "sourceObjectId": "memory_gc15",
                "sourceObjectKind": "explicit_memory",
                "sourceVersion": 1,
                "contentHash": "c" * 64,
            }
        ],
        "originInstanceId": "local-generation-gc15",
    }
    parsed = AiAnswerSaveRequest.model_validate(payload)
    assert parsed.material_access_mode == "memory_context"
    assert parsed.boundary_state == "member_local_memory_context"


def test_strategic_profile_single_dimension_accepts_one_tag() -> None:
    parsed = LocalWorkbenchChatRepository._json_object_from_model(
        "<people>王强现兼顾心盛计划相关工作。",
        expected_dimensions=("people",),
    )
    assert parsed["dimensions"]["people"]["narrative"] == "王强现兼顾心盛计划相关工作。"

    plain = LocalWorkbenchChatRepository._json_object_from_model(
        "关键人物：王强现兼顾心盛计划相关工作。",
        expected_dimensions=("people",),
    )
    assert plain["dimensions"]["people"]["narrative"] == "王强现兼顾心盛计划相关工作。"


def test_strategic_profile_full_refresh_rejects_truncated_tags() -> None:
    with pytest.raises(LocalRuntimeError, match="未返回有效档案结构"):
        LocalWorkbenchChatRepository._json_object_from_model(
            "<people>王强现兼顾心盛计划相关工作。</people>"
        )


def _prepared_profile() -> dict:
    narratives = {
        "essence": "日慈公益基金会持续关注儿童成长与社会情感学习。",
        "business_intro": "机构通过心盛计划等项目为流动儿童提供社会情感学习支持。",
        "cooperation": "",
        "people": "机构现由张真担任秘书长。",
        "timeline": "",
        "next_steps": "",
    }
    return {
        "schema": "yiyu.strategic-client-profile.v2",
        "generator": "strategy_companion_local_wiki_v1",
        "modelName": "doubao-test",
        "dimensions": [
            {
                "dimension": dimension,
                "narrative": narrative,
                "references": ([{"sourceType": "local_document", "sourceId": "source-asset-1", "label": "项目资料"}] if narrative else []),
            }
            for dimension, narrative in narratives.items()
        ],
        "sourceDocuments": [
            {
                "sourceObjectId": "source-asset-1",
                "sourceObjectKind": "source_asset",
                "sourceVersion": 1,
                "contentHash": "d" * 64,
                "knowledgeDocumentId": "knowledge-local-1",
                "documentVersionId": "document-version-local-1",
                "title": "项目资料",
            }
        ],
    }


def test_gc14_strategic_profile_accepts_local_wiki_synthesis_not_raw_correction() -> None:
    facts = [
        {
            "id": "fact-person",
            "version": 1,
            "fact_hash": "a" * 64,
            "statement": "张真现任日慈公益基金会秘书长。",
        },
        {
            "id": "fact-program",
            "version": 1,
            "fact_hash": "b" * 64,
            "statement": "心盛计划服务流动儿童的社会情感学习需求。",
        },
    ]
    content = _normalize_prepared_profile(
        _prepared_profile(),
        project_name="日慈基金会",
        bot_id="bot-strategy",
        generated_at="2026-08-06T12:00:00Z",
        facts=facts,
    )
    by_dimension = {item["dimension"]: item for item in content["dimensions"]}
    assert by_dimension["people"]["narrative"] == "机构现由张真担任秘书长。"
    assert content["sourceFacts"][0]["factId"] == "fact-person"
    assert content["sourceDocuments"][0]["sourceObjectId"] == "source-asset-1"
    assert set(by_dimension) == {
        "essence",
        "business_intro",
        "cooperation",
        "people",
        "timeline",
        "next_steps",
    }


def test_profile_own_clarification_is_formal_fact_and_requests_local_wiki_rebuild(
    tmp_path: Path,
) -> None:
    repository, identity, payload = _repository(tmp_path)
    rebuild_strategic_profile(
        repository,
        identity,
        project_id=payload["projectId"],
        idempotency_key="gc14-profile-before-clarification",
        prepared_profile=_prepared_profile(),
    )
    statement = "心盛计划现由王强兼顾，数字化试点转向教师培训项目。"
    result = create_strategic_profile_clarification(
        repository,
        identity,
        project_id=payload["projectId"],
        payload={
            "dimension": "people",
            "answer": statement,
            "question": "这里 AI 理解得对不对",
            "basedOnRev": 1,
        },
        idempotency_key="gc12-profile-clarification",
    )
    assert result["status"] == "applied"
    assert result["consumerPropagation"]["state"] == "completed"
    assert result["consumerPropagation"]["strategicProfile"]["state"] == "pending_local_wiki"
    content_replay = create_strategic_profile_clarification(
        repository,
        identity,
        project_id=payload["projectId"],
        payload={
            "dimension": "people",
            "answer": statement,
            "question": "这里 AI 理解得对不对",
            "basedOnRev": 1,
        },
        idempotency_key="gc12-profile-clarification-after-client-timeout",
    )
    assert content_replay["idempotentReplay"] is True
    assert content_replay["consumerPropagation"]["state"] == "completed"
    listed = list_strategic_profile_clarifications(
        repository,
        identity,
        project_id=payload["projectId"],
    )
    assert listed["clarifications"][0]["answer"] == statement
    assert listed["clarifications"][0]["dimension"] == "people"
    context = repository.project_knowledge_context(
        identity,
        project_id=payload["projectId"],
    )
    assert any(item["summary"] == statement for item in context["savedMemories"])
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT sources.purpose_kind, fact.verification_state "
            "FROM atomic_facts AS fact JOIN source_sets AS sources "
            "ON sources.id=fact.source_set_id AND sources.scope_id=fact.scope_id "
            "WHERE fact.id=?",
            (result["id"],),
        ).fetchone()
        assert tuple(row) == ("strategic_profile_clarification", "verified")
        assert connection.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ai_approvals").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def _repository(tmp_path: Path) -> tuple[CloudRepository, SessionIdentity, dict]:
    database = tmp_path / "strict-cloud.db"
    initialize_database(database, "cloud")
    now = utc_now()
    organization_id = "org_gc14_test"
    scope_id = "scope_gc14_test"
    principal_id = "principal_gc14_test"
    membership_id = "membership_gc14_test"
    client_id = "client_gc14_test"
    cloud_instance_id = "cli_gc14_test"
    provider_id = "provider_gc14_test"
    bot_id = builtin_agent_id(organization_id, "project_workspace")
    strategy_bot_id = builtin_agent_id(organization_id, "strategy_companion")
    with runtime_connection(database, "cloud") as connection:
        connection.execute(
            "INSERT INTO state_registry (id,state_id,target_blueprint_node,version,"
            "record_kind,lifecycle_state,created_at,updated_at,deleted_at) "
            "VALUES (?,?,'cloud_instance',1,'cloud_instance','active',?,?,NULL)",
            (cloud_instance_id, cloud_instance_id, now, now),
        )
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,"
            "record_kind,name,created_at,deleted_at) "
            "VALUES (?,'active',1,?,'organization','GC14测试组织',?,NULL)",
            (organization_id, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,"
            "policy_version,created_at,updated_at,status,version,lifecycle_state,"
            "deleted_at) VALUES (?,'organization',?,1,?,?,'active',1,'active',NULL)",
            (scope_id, organization_id, now, now),
        )
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,"
            "principal_kind,display_name,version,lifecycle_state,created_at,deleted_at) "
            "VALUES (?,'active',1,?,'person','GC14测试成员',1,'active',?,NULL)",
            (principal_id, now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
            "status,version,record_kind,visibility_scope,lifecycle_state,created_at,"
            "updated_at,deleted_at) VALUES (?,?,?,'admin','active',1,'membership',"
            "'organization','active',?,?,NULL)",
            (membership_id, scope_id, principal_id, now, now),
        )
        for resource_id, resource_kind, resource_type in (
            (client_id, "client", "client"),
            (bot_id, "bot_definition", "builtin_function_agent"),
            (strategy_bot_id, "bot_definition", "builtin_function_agent"),
        ):
            connection.execute(
                "INSERT INTO secured_resources (id,scope_id,resource_kind,"
                "lifecycle_state,version,resource_type_key,created_at,updated_at,"
                "deleted_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,'active',1,?,?,?,NULL,'cloud',?)",
                (
                    resource_id,
                    scope_id,
                    resource_kind,
                    resource_type,
                    now,
                    now,
                    cloud_instance_id,
                ),
            )
        connection.execute(
            "INSERT INTO clients (id,scope_id,owner_membership_id,lifecycle_state,"
            "version,name,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,'active',1,'GC14项目',?,?,NULL)",
            (client_id, scope_id, membership_id, now, now),
        )
        connection.execute(
            "INSERT INTO policy_versions (id,scope_id,secured_resource_id,"
            "policy_scope_kind,version,policy_spec_schema_version,policy_spec,"
            "effective_at,created_at,lifecycle_state,updated_at,deleted_at) "
            "VALUES ('policy_gc14_project',?,?,'secured_resource',1,"
            "'gc02.client-access.v1','{\"defaultDecision\":\"deny\"}',"
            "?,?,'active',?,NULL)",
            (scope_id, client_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO object_grants (id,scope_id,secured_resource_id,"
            "policy_version_id,subject_principal_id,subject_membership_id,"
            "capability_set_schema_version,capability_set,grant_generation,status,"
            "grant_source_set_id,created_at,updated_at,revoked_at,version,"
            "lifecycle_state,deleted_at) VALUES ('grant_gc14_project',?,?,"
            "'policy_gc14_project',NULL,?,'1',"
            "'{\"read\":true,\"write\":true,\"contributeKnowledge\":true,"
            "\"manageSharing\":true}',1,'active',NULL,?,?,NULL,1,'active',NULL)",
            (scope_id, client_id, membership_id, now, now),
        )
        for resource_id, agent_kind, handle, description in (
            (bot_id, "project_workspace", "project-workspace", "项目工作台"),
            (strategy_bot_id, "strategy_companion", "strategy-companion", "战略陪伴"),
        ):
            connection.execute(
                "INSERT INTO bot_definitions (id,scope_id,agent_kind,version,handle,"
                "description,enabled,lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,1,?,?,1,'active',?,?,NULL)",
                (resource_id, scope_id, agent_kind, handle, description, now, now),
            )
        connection.execute(
            "INSERT INTO provider_resources (id,scope_id,provider,resource_kind,"
            "remote_id,retention_state,owner_kind,display_name,endpoint,model_name,"
            "secret_fingerprint,status,verified_at,version,lifecycle_state,created_at,"
            "updated_at,deleted_at,authority_role,origin_instance_id) VALUES "
            "(?,?,'doubao','organization_ai_configuration',?,'organization_managed',"
            "'organization','组织大模型','https://example.invalid/api/v3','model-test',"
            "'fingerprint','ready',?,1,'active',?,?,NULL,'cloud',?)",
            (provider_id, scope_id, provider_id, now, now, now, cloud_instance_id),
        )
        connection.commit()
    repository = CloudRepository(
        database,
        cloud_instance_id=cloud_instance_id,
        master_key=Fernet.generate_key().decode(),
    )
    identity = SessionIdentity(
        session_id="session_gc14_test",
        principal_id=principal_id,
        membership_id=membership_id,
        organization_id=organization_id,
        cloud_instance_id=cloud_instance_id,
        scope_id=scope_id,
        system_role="admin",
        visibility_scope="organization",
        display_name="GC14测试成员",
    )
    payload = {
        "answerId": "answer_gc14_test",
        "projectId": client_id,
        "threadId": "thread_gc14_test",
        "questionHash": "a" * 64,
        "answerHash": "b" * 64,
        "sourceSetId": "source_set_gc14_test",
        "contextManifestId": "context_gc14_test",
        "lineageId": "lineage_gc14_test",
        "botId": bot_id,
        "providerResourceId": provider_id,
        "modelName": "model-test",
        "sourceCount": 1,
        "materialAccessMode": "local_original",
        "boundaryState": "local_private_context",
        "selectedSources": [
            {
                "sourceObjectId": "document_gc14_test",
                "sourceObjectKind": "local_document",
                "sourceVersion": 1,
                "contentHash": "c" * 64,
            }
        ],
        "originInstanceId": "local-generation-gc14-test",
    }
    return repository, identity, payload


def test_gc14_safe_answer_receipt_is_idempotent_and_has_exact_trace(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)

    first = repository.save_ai_answer(
        identity,
        payload=payload,
        idempotency_key="gc14-answer-test",
    )
    replay = repository.save_ai_answer(
        identity,
        payload=payload,
        idempotency_key="gc14-answer-test",
    )

    assert first["answer"]["answerId"] == payload["answerId"]
    assert first["answer"]["threadId"] == payload["threadId"]
    assert first["answer"]["sourceCount"] == 1
    assert first["answer"]["answerHash"] == payload["answerHash"]
    assert first["idempotentReplay"] is False
    assert replay["idempotentReplay"] is True
    with runtime_connection(repository.database_path, "cloud") as connection:
        for table in (
            "source_sets",
            "source_set_members",
            "derivation_lineage",
            "ai_context_manifests",
            "cache_entries",
            "ai_answers",
            "execution_runs",
            "external_side_effects",
            "commands",
            "audit_events",
            "outbox_events",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table}"  # noqa: S608 - frozen table list
            ).fetchone()[0] == 1
        for forbidden_table in (
            "ai_proposals",
            "ai_approvals",
            "tasks",
            "atomic_facts",
            "automation_rules",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {forbidden_table}"  # noqa: S608 - frozen table list
            ).fetchone()[0] == 0
        cloud_manifest = connection.execute(
            "SELECT receipt FROM object_manifests "
            "WHERE id=(SELECT context_object_manifest_id FROM ai_context_manifests)"
        ).fetchone()
        receipt = str(cloud_manifest[0])
        assert "本机" not in receipt
        assert "answerMarkdown" not in receipt
        assert "question" not in receipt.lower().replace("questionhash", "")
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_workbench_answer_action_enters_gc04_task_authority(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    repository.save_ai_answer(
        identity,
        payload=payload,
        idempotency_key="gc14-answer-for-task",
    )
    created = answer_task_action(
        repository,
        identity,
        answer_id=payload["answerId"],
        action_type="create_task",
        idempotency_key="gc14-answer-task",
    )
    replay = answer_task_action(
        repository,
        identity,
        answer_id=payload["answerId"],
        action_type="create_task",
        idempotency_key="gc14-answer-task",
    )
    assert created["taskId"] == replay["taskId"]
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT client_id, source_type, source_id FROM tasks WHERE id=?",
            (created["taskId"],),
        ).fetchone()
        assert tuple(row) == (payload["projectId"], "ai_answer", payload["answerId"])
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc12_answer_correction_is_cas_versioned_and_invalidates_old_context(
    tmp_path: Path,
) -> None:
    repository, identity, answer_payload = _repository(tmp_path)
    repository.save_ai_answer(
        identity,
        payload=answer_payload,
        idempotency_key="gc12-source-answer",
    )
    selected_hash = sha256_text("张真是益语智库联合创始人")

    def correction(statement: str, expected_version: int) -> dict:
        return {
            "projectId": answer_payload["projectId"],
            "correctionKind": "correction",
            "selectedTextHash": selected_hash,
            "statement": statement,
            "statementHash": sha256_text(statement),
            "expectedVersion": expected_version,
            "originInstanceId": "local-gc12-test",
        }

    first = repository.correct_ai_answer_fact(
        identity,
        answer_id=answer_payload["answerId"],
        payload=correction("张真是日慈公益基金会秘书长。", 0),
        idempotency_key="gc12-correction-v1",
    )
    replay = repository.correct_ai_answer_fact(
        identity,
        answer_id=answer_payload["answerId"],
        payload=correction("张真是日慈公益基金会秘书长。", 0),
        idempotency_key="gc12-correction-v1",
    )
    second = repository.correct_ai_answer_fact(
        identity,
        answer_id=answer_payload["answerId"],
        payload=correction("张真现任日慈公益基金会秘书长。", 1),
        idempotency_key="gc12-correction-v2",
    )
    assert first["version"] == 1
    assert first["consumerPropagation"]["state"] == "completed"
    assert first["consumerPropagation"]["retryable"] is False
    assert replay["idempotentReplay"] is True
    assert replay["consumerPropagation"]["state"] == "completed"
    assert second["version"] == 2
    rebuild_strategic_profile(
        repository,
        identity,
        project_id=answer_payload["projectId"],
        idempotency_key="gc14-local-wiki-profile-v1",
        prepared_profile=_prepared_profile(),
    )
    narrative = repository.project_narrative(
        identity,
        project_id=answer_payload["projectId"],
    )
    assert narrative["generator"] == "strategy_companion_local_wiki_v1"
    assert narrative["narrativeNeedsRefresh"] is False
    people = next(
        item for item in narrative["dimensions"] if item["dimension"] == "people"
    )
    assert "机构现由张真担任秘书长。" in people["narrative"]
    assert "益语智库联合创始人" not in people["narrative"]
    with pytest.raises(Exception, match="该事实已被更新"):
        repository.correct_ai_answer_fact(
            identity,
            answer_id=answer_payload["answerId"],
            payload=correction("过期客户端覆盖", 0),
            idempotency_key="gc12-correction-stale",
        )
    with runtime_connection(repository.database_path, "cloud") as connection:
        fact = connection.execute(
            "SELECT version, verification_state, fact_hash FROM atomic_facts"
        ).fetchone()
        assert tuple(fact) == (2, "verified", sha256_text("张真现任日慈公益基金会秘书长。"))
        assert connection.execute(
            "SELECT COUNT(*) FROM object_manifests "
            "WHERE media_type='application/vnd.yiyu.project-answer-knowledge+json'"
        ).fetchone()[0] == 2
        context = connection.execute(
            "SELECT status, invalidated_at FROM ai_context_manifests"
        ).fetchone()
        assert context["status"] == "invalidated"
        assert context["invalidated_at"]
        assert connection.execute(
            "SELECT COUNT(*) FROM reconciliation_runs "
            "WHERE reconciliation_kind='project_knowledge_consumer_invalidation_v1' "
            "AND status='completed'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_events "
            "WHERE event_type='gc13.project_knowledge.consumers_invalidated' "
            "AND status='published'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_events "
            "WHERE event_type='gc13.project_knowledge.project_reports_requested' "
            "AND status='pending'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_events "
            "WHERE event_type='gc13.project_knowledge.strategic_profile_requested' "
            "AND status='published'"
        ).fetchone()[0] == 2
        profile = connection.execute(
            "SELECT current_version, artifact_kind FROM narrative_outputs "
            "WHERE client_id=? AND artifact_kind='strategic_profile'",
            (answer_payload["projectId"],),
        ).fetchone()
        assert tuple(profile) == (1, "strategic_profile")
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs "
            "WHERE run_kind='strategic_profile_rebuild' AND status='completed'"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc12_explicit_remember_is_formal_project_knowledge_without_approval(
    tmp_path: Path,
) -> None:
    repository, identity, answer_payload = _repository(tmp_path)
    repository.save_ai_answer(
        identity,
        payload=answer_payload,
        idempotency_key="gc12-remember-source-answer",
    )
    statement = "心盛计划是日慈基金会面向流动儿童的社会情感学习项目。"
    statement_hash = sha256_text(statement)
    result = repository.correct_ai_answer_fact(
        identity,
        answer_id=answer_payload["answerId"],
        payload={
            "projectId": answer_payload["projectId"],
            "correctionKind": "remember",
            "selectedTextHash": statement_hash,
            "statement": statement,
            "statementHash": statement_hash,
            "expectedVersion": 0,
            "originInstanceId": "local-gc12-remember-test",
        },
        idempotency_key="gc12-remember-v1",
    )
    assert result["correctionKind"] == "remember"
    assert result["verificationState"] == "verified"
    assert result["consumerPropagation"]["state"] == "completed"
    context = repository.project_knowledge_context(
        identity,
        project_id=answer_payload["projectId"],
    )
    remembered = [
        item
        for item in context["savedMemories"]
        if item.get("memoryKind") == "explicit_memory"
    ]
    assert len(remembered) == 1
    assert remembered[0]["summary"] == statement
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT purpose_kind FROM source_sets WHERE id=?",
            (result["sourceSetId"],),
        ).fetchone()[0] == "answer_remember"
        assert connection.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ai_approvals").fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc13_consumer_failure_does_not_roll_back_formal_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cloud_backend.app.repositories import gc12_corrections

    repository, identity, answer_payload = _repository(tmp_path)
    repository.save_ai_answer(
        identity,
        payload=answer_payload,
        idempotency_key="gc13-failure-source-answer",
    )
    monkeypatch.setattr(
        gc12_corrections,
        "_propagate_project_knowledge_consumers",
        lambda *_args, **_kwargs: {
            "state": "failed_retryable",
            "retryable": True,
            "message": "项目知识已保存，相关页面更新失败，可以重试",
            "directConsumers": [],
            "pendingConsumers": [],
        },
    )
    statement = "消费者失败时仍须保留这条已确认的项目事实。"
    result = repository.correct_ai_answer_fact(
        identity,
        answer_id=answer_payload["answerId"],
        payload={
            "projectId": answer_payload["projectId"],
            "correctionKind": "supplement",
            "selectedTextHash": sha256_text("待补充文本"),
            "statement": statement,
            "statementHash": sha256_text(statement),
            "expectedVersion": 0,
            "originInstanceId": "local-gc13-failure-test",
        },
        idempotency_key="gc13-consumer-failure",
    )
    assert result["consumerPropagation"]["state"] == "failed_retryable"
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT verification_state, fact_hash FROM atomic_facts WHERE id=?",
            (result["factId"],),
        ).fetchone()
        assert tuple(row) == ("verified", sha256_text(statement))
