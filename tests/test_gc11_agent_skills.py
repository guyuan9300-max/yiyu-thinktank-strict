from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.runtime import WorkspaceRuntime
from cloud_backend.app.main import create_app
from strict_common.agent_memory import BUILTIN_AGENT_DEFINITIONS, builtin_agent_id
from strict_common.schema import runtime_connection
from strict_common.ids import utc_now
from tests.test_gc01_authorization import _auth, _seed_gc01_cloud


ANALYSIS_SKILL = {
    "shortName": "证据—判断—边界",
    "description": "用已提供材料区分事实、判断和信息缺口",
    "instructions": [
        "先列出有直接来源支持的事实。",
        "再给出分析判断并说明依据。",
        "最后列出无法确认的边界和需要补证的问题。",
        "不把推断写成项目正式事实。",
    ],
    "outputTemplate": "## 已核实事实\n...\n## 分析判断\n...\n## 信息边界与待补证\n...",
    "allowedToolIds": [],
    "visibility": "private",
    "granteePrincipalIds": [],
    "agentKinds": ["project_workspace"],
}


def test_skill_run_cloud_command_is_on_the_strict_allowlist() -> None:
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST",
        "/api/v2/agent-skills/skill_contract/runs",
    )
    assert not WorkspaceRuntime._connected_cloud_path_allowed(
        "POST",
        "/api/v2/agent-skills/skill_contract/arbitrary-action",
    )


def test_builtin_agent_contracts_are_complete_and_distinct() -> None:
    assert len(BUILTIN_AGENT_DEFINITIONS) == 6
    assert len({item.capability_policy_version for item in BUILTIN_AGENT_DEFINITIONS}) == 6
    for item in BUILTIN_AGENT_DEFINITIONS:
        assert item.service_goal
        assert item.command_boundaries
        assert item.base_mode


def test_private_declarative_analysis_skill_uses_only_strict_88_authority(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    bot_id = builtin_agent_id("org_gc01_test", "project_workspace")
    with runtime_connection(database, "cloud") as connection:
        now = utc_now()
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
            "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES (?,'scope_gc01_test','bot_definition','active',"
            "1,'builtin_function_agent',?,?,NULL,'cloud',?)",
            (bot_id, now, now, config.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO bot_definitions (id,scope_id,agent_kind,version,handle,description,"
            "enabled,lifecycle_state,created_at,updated_at,deleted_at) VALUES "
            "(?,'scope_gc01_test','project_workspace',1,'project-workspace','项目工作台',"
            "1,'active',?,?,NULL)",
            (bot_id, now, now),
        )
        connection.commit()
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v2/agent-skills",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "analysis-skill-1"},
            json=ANALYSIS_SKILL,
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["shortName"] == "证据—判断—边界"
        assert body["agentKinds"] == ["project_workspace"]
        assert body["capabilityBoundary"] == "declarative_only"
        assert body["enabled"] is True

        own = client.get(
            "/api/v2/agent-skills?agentKind=project_workspace",
            headers=_auth(tokens["admin"]),
        )
        assert own.status_code == 200
        assert [item["skillId"] for item in own.json()["items"]] == [body["skillId"]]

        other_member = client.get(
            "/api/v2/agent-skills?agentKind=project_workspace",
            headers=_auth(tokens["member"]),
        )
        assert other_member.status_code == 200
        assert other_member.json()["items"] == []

        replay = client.post(
            "/api/v2/agent-skills",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "analysis-skill-1"},
            json=ANALYSIS_SKILL,
        )
        assert replay.status_code == 200
        assert replay.json()["idempotentReplay"] is True

        rejected = client.post(
            "/api/v2/agent-skills",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "analysis-skill-tool"},
            json={**ANALYSIS_SKILL, "shortName": "危险执行", "allowedToolIds": ["shell"]},
        )
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "agent_skill_tools_not_connected"

        updated = client.patch(
            f"/api/v2/agent-skills/{body['skillId']}",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "analysis-skill-update-1"},
            json={
                **ANALYSIS_SKILL,
                "shortName": "证据判断边界",
                "description": "区分已核实事实、判断和待补证边界",
                "expectedVersion": 1,
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["shortName"] == "证据判断边界"
        assert updated.json()["version"] == 2

        run = client.post(
            f"/api/v2/agent-skills/{body['skillId']}/runs",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "analysis-skill-run-1"},
            json={
                "agentKind": "project_workspace",
                "inputHash": "a" * 64,
                "resultHash": "b" * 64,
                "sourceCount": 1,
            },
        )
        assert run.status_code == 200, run.text
        assert run.json()["shortName"] == "证据判断边界"
        assert run.json()["status"] == "completed"
        run_replay = client.post(
            f"/api/v2/agent-skills/{body['skillId']}/runs",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "analysis-skill-run-1"},
            json={
                "agentKind": "project_workspace",
                "inputHash": "a" * 64,
                "resultHash": "b" * 64,
                "sourceCount": 1,
            },
        )
        assert run_replay.status_code == 200
        assert run_replay.json()["runId"] == run.json()["runId"]
        assert run_replay.json()["idempotentReplay"] is True
        update_replay = client.patch(
            f"/api/v2/agent-skills/{body['skillId']}",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "analysis-skill-update-1"},
            json={
                **ANALYSIS_SKILL,
                "shortName": "证据判断边界",
                "description": "区分已核实事实、判断和待补证边界",
                "expectedVersion": 1,
            },
        )
        assert update_replay.status_code == 200
        assert update_replay.json()["idempotentReplay"] is True

        disabled = client.patch(
            f"/api/v2/agent-skills/{body['skillId']}/enabled",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "analysis-skill-disable-1"},
            json={"enabled": False, "expectedVersion": 2},
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["enabled"] is False
        assert disabled.json()["version"] == 3
        disabled_replay = client.patch(
            f"/api/v2/agent-skills/{body['skillId']}/enabled",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "analysis-skill-disable-1"},
            json={"enabled": False, "expectedVersion": 2},
        )
        assert disabled_replay.status_code == 200
        assert disabled_replay.json()["idempotentReplay"] is True

    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM automation_rules WHERE record_kind='agent_skill'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE aggregate_type='agent_skill'"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs WHERE rule_id IS NOT NULL "
            "AND run_kind='agent_skill_application' AND status='completed'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ai_approvals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_retained_writing_style_ui_uses_typed_skill_authority(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v2/workbench/libraries/writing_skill",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "writing-style-1"},
            json={
                "name": "证据优先",
                "description": "先写证据，再写判断",
                "distilledMd": "先列直接证据，再给结论并标出未知边界。",
            },
        )
        assert created.status_code == 201, created.text
        style = created.json()
        assert style["name"] == "证据优先"
        assert style["distilledMd"].startswith("先列直接证据")

        listed = client.get(
            "/api/v2/workbench/libraries/writing_skill",
            headers=_auth(tokens["member"]),
        )
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [style["id"]]

        normal_skills = client.get(
            "/api/v2/agent-skills?agentKind=project_workspace",
            headers=_auth(tokens["member"]),
        )
        assert normal_skills.status_code == 200
        assert normal_skills.json()["items"] == []

        updated = client.put(
            f"/api/v2/workbench/libraries/writing_skill/{style['id']}",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "writing-style-2"},
            json={"name": "证据与边界"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "证据与边界"
        assert updated.json()["version"] == 2

        deleted = client.request(
            "DELETE",
            f"/api/v2/workbench/libraries/writing_skill/{style['id']}",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "writing-style-3"},
            json={},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"deleted": True, "id": style["id"]}
        assert client.get(
            "/api/v2/workbench/libraries/writing_skill",
            headers=_auth(tokens["member"]),
        ).json() == []

    with runtime_connection(database, "cloud") as connection:
        row = connection.execute(
            "SELECT action_spec, enabled, version FROM automation_rules "
            "WHERE record_kind='agent_skill'"
        ).fetchone()
        assert row is not None
        assert '"skillType":"writing_style"' in row[0]
        assert int(row[1]) == 0
        assert int(row[2]) == 3
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc02_skill_share_scopes_use_current_policy_and_revoke_new_use(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    selected_draft = {
        **ANALYSIS_SKILL,
        "shortName": "成员定向分析",
        "visibility": "selected_members",
        "granteeMembershipIds": ["membership_admin"],
    }
    with TestClient(create_app(config)) as client:
        forbidden_organization = client.post(
            "/api/v2/agent-skills",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "member-org-share"},
            json={**ANALYSIS_SKILL, "shortName": "越权全员", "visibility": "organization"},
        )
        assert forbidden_organization.status_code == 403
        forbidden_department = client.post(
            "/api/v2/agent-skills",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "member-dept-share"},
            json={
                **ANALYSIS_SKILL,
                "shortName": "越权部门",
                "visibility": "department",
                "departmentId": "department_gc01",
            },
        )
        assert forbidden_department.status_code == 403

        selected = client.post(
            "/api/v2/agent-skills",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "member-selected-share"},
            json=selected_draft,
        )
        assert selected.status_code == 200, selected.text
        selected_id = selected.json()["skillId"]
        assert selected.json()["canManage"] is True
        selected_for_admin = client.get(
            f"/api/v2/agent-skills/{selected_id}",
            headers=_auth(tokens["admin"]),
        )
        assert selected_for_admin.status_code == 200
        assert selected_for_admin.json()["canManage"] is False
        assert selected_for_admin.json()["authorizationProjection"]["viewerCapabilities"] == ["read", "use"]

        with runtime_connection(database, "cloud") as connection:
            before = connection.execute(
                "SELECT id,policy_version_id,version FROM object_grants "
                "WHERE secured_resource_id=? AND subject_membership_id='membership_admin' "
                "AND status='active'",
                (selected_id,),
            ).fetchone()
        content_only = client.patch(
            f"/api/v2/agent-skills/{selected_id}",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "member-content-edit"},
            json={**selected_draft, "description": "只修改说明，不重建授权", "expectedVersion": 1},
        )
        assert content_only.status_code == 200, content_only.text
        with runtime_connection(database, "cloud") as connection:
            after = connection.execute(
                "SELECT id,policy_version_id,version FROM object_grants "
                "WHERE secured_resource_id=? AND subject_membership_id='membership_admin' "
                "AND status='active'",
                (selected_id,),
            ).fetchone()
        assert tuple(after) == tuple(before)

        revoked = client.patch(
            f"/api/v2/agent-skills/{selected_id}",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "member-selected-revoke"},
            json={
                **ANALYSIS_SKILL,
                "shortName": "成员定向分析",
                "visibility": "private",
                "expectedVersion": 2,
            },
        )
        assert revoked.status_code == 200, revoked.text
        assert client.get(
            f"/api/v2/agent-skills/{selected_id}",
            headers=_auth(tokens["admin"]),
        ).status_code == 404

        with runtime_connection(database, "cloud") as connection:
            now = utc_now()
            connection.execute(
                "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,"
                "version,record_kind,parent_membership_id,department_id,lifecycle_state,"
                "created_at,updated_at,deleted_at) VALUES "
                "('assignment_member_department','scope_gc01_test',NULL,'department_lead',"
                "'active',1,'department_assignment','membership_member','department_gc01',"
                "'active',?,?,NULL)",
                (now, now),
            )
            connection.commit()
        department = client.post(
            "/api/v2/agent-skills",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "lead-department-share"},
            json={
                **ANALYSIS_SKILL,
                "shortName": "本部门共用分析",
                "visibility": "department",
                "departmentId": "department_gc01",
            },
        )
        assert department.status_code == 200, department.text
        department_id = department.json()["skillId"]
        assert client.get(
            f"/api/v2/agent-skills/{department_id}",
            headers=_auth(tokens["admin"]),
        ).status_code == 200

        organization = client.post(
            "/api/v2/agent-skills",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "admin-organization-share"},
            json={**ANALYSIS_SKILL, "shortName": "全员共用分析", "visibility": "organization"},
        )
        assert organization.status_code == 200, organization.text
        organization_id = organization.json()["skillId"]
        assert client.get(
            f"/api/v2/agent-skills/{organization_id}",
            headers=_auth(tokens["member"]),
        ).status_code == 200
        organization_revoked = client.patch(
            f"/api/v2/agent-skills/{organization_id}",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "admin-organization-revoke"},
            json={
                **ANALYSIS_SKILL,
                "shortName": "全员共用分析",
                "visibility": "private",
                "expectedVersion": 1,
            },
        )
        assert organization_revoked.status_code == 200
        assert client.get(
            f"/api/v2/agent-skills/{organization_id}",
            headers=_auth(tokens["member"]),
        ).status_code == 404

    with runtime_connection(database, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM object_grants AS grant_row "
            "JOIN automation_rules AS skill ON skill.id=grant_row.secured_resource_id "
            "WHERE skill.record_kind='agent_skill' AND grant_row.status='active' "
            "AND grant_row.policy_version_id IS NULL"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM policy_versions WHERE secured_resource_id=? "
            "AND lifecycle_state='archived'",
            (selected_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE target_resource_id=?",
            (selected_id,),
        ).fetchone()[0] == 3
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
