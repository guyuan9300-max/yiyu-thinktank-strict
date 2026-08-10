from __future__ import annotations

from pathlib import Path

from cloud_backend.app.repositories.task_planning_agent import TaskPlanningAgentRepository
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
    assert {"日慈", "软件", "官网"}.issubset(set(result["keywords"]))

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
