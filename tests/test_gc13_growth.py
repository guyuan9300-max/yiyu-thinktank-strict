from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.app.ui_domains.gc13_growth import (
    register_gc13_growth_ui_domain,
    router as local_router,
)
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.domain_routes.gc13_growth import register_gc13_growth_routes
from cloud_backend.app.repositories import gc13_growth
from cloud_backend.app.repositories.gc13_growth import (
    PREFERENCE_SCHEMA,
    confirm_growth_evidence,
    growth_snapshot,
    like_growth_experience_quote,
    publish_growth_rule,
    record_growth_companion_summary,
    rebuild_growth_read_models,
    update_growth_evidence,
)
from cloud_backend.app.repositories.gc13_weekly_review_adapter import (
    WeeklyReviewGrowthCandidate,
    confirm_weekly_review_candidate,
)
from cloud_backend.app.repository import CloudRepository, RepositoryError, SessionIdentity
from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import canonical_json, utc_now
from strict_common.physical_schema import user_tables
from strict_common.schema import initialize_database, runtime_connection


def _seed(tmp_path: Path) -> tuple[CloudRepository, SessionIdentity, SessionIdentity]:
    database = tmp_path / "strict-cloud.db"
    initialize_database(database, "cloud")
    now = utc_now()
    cloud_instance_id = "cloud_gc13_test"
    organization_id = "organization_gc13_test"
    scope_id = "scope_gc13_test"
    growth_bot_id = builtin_agent_id(organization_id, "growth_companion")
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
            "VALUES (?,'active',1,?,'organization','GC13测试组织',?,NULL)",
            (organization_id, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,"
            "policy_version,created_at,updated_at,status,version,lifecycle_state,"
            "deleted_at) VALUES (?,'organization',?,1,?,?,'active',1,'active',NULL)",
            (scope_id, organization_id, now, now),
        )
        for principal_id, display_name in (
            ("principal_gc13_owner", "成长成员"),
            ("principal_gc13_other", "其他成员"),
        ):
            connection.execute(
                "INSERT INTO principals (id,status,identity_version,updated_at,"
                "principal_kind,display_name,version,lifecycle_state,created_at,deleted_at) "
                "VALUES (?,'active',1,?,'person',?,1,'active',?,NULL)",
                (principal_id, now, display_name, now),
            )
        for membership_id, principal_id, role in (
            ("membership_gc13_owner", "principal_gc13_owner", "admin"),
            ("membership_gc13_other", "principal_gc13_other", "member"),
        ):
            connection.execute(
                "INSERT INTO organization_memberships (id,scope_id,principal_id,"
                "role_key,status,version,record_kind,visibility_scope,lifecycle_state,"
                "created_at,updated_at,deleted_at) VALUES (?,?,?,?,'active',1,"
                "'membership','organization','active',?,?,NULL)",
                (membership_id, scope_id, principal_id, role, now, now),
            )

        def preference(
            preference_id: str,
            membership_id: str,
            key: str,
            value: str,
            *,
            allowed: bool,
        ) -> None:
            spec = canonical_json(
                {
                    "schema": PREFERENCE_SCHEMA,
                    "label": key,
                    "value": value,
                    "origin": "explicit",
                    "memberAllowed": allowed,
                    "allowConsumers": ["growth_companion"],
                }
            )
            connection.execute(
                "INSERT INTO organization_memberships (id,scope_id,principal_id,"
                "role_key,status,version,record_kind,parent_membership_id,"
                "visibility_scope,capability_set_schema_version,capability_set,"
                "target_type,target_id,lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES (?,?,NULL,NULL,'active',1,'preference',?,'self',?,?,"
                "'stable_preference',?,'active',?,?,NULL)",
                (
                    preference_id,
                    scope_id,
                    membership_id,
                    PREFERENCE_SCHEMA,
                    spec,
                    key,
                    now,
                    now,
                ),
            )

        preference(
            "preference_gc13_owner_allowed",
            "membership_gc13_owner",
            "学习节奏",
            "先给一个可实践的小步骤",
            allowed=True,
        )
        preference(
            "preference_gc13_owner_blocked",
            "membership_gc13_owner",
            "未授权偏好",
            "不得被成长陪伴读取",
            allowed=False,
        )
        preference(
            "preference_gc13_other_allowed",
            "membership_gc13_other",
            "他人偏好",
            "不得泄露给当前成员",
            allowed=True,
        )
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
            "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES (?,?,'bot_definition','active',1,"
            "'builtin_function_agent',?,?,NULL,'cloud',?)",
            (growth_bot_id, scope_id, now, now, cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO bot_definitions (id,scope_id,agent_kind,version,handle,"
            "description,capability_policy_version,enabled,lifecycle_state,created_at,"
            "updated_at,deleted_at) VALUES (?,?,'growth_companion',1,'growth-companion',"
            "'成长陪伴','yiyu.growth-companion.v1',1,'active',?,?,NULL)",
            (growth_bot_id, scope_id, now, now),
        )
        connection.commit()
    repository = CloudRepository(
        database,
        cloud_instance_id=cloud_instance_id,
        master_key=Fernet.generate_key().decode(),
    )
    owner = SessionIdentity(
        session_id="session_gc13_owner",
        principal_id="principal_gc13_owner",
        membership_id="membership_gc13_owner",
        organization_id=organization_id,
        cloud_instance_id=cloud_instance_id,
        scope_id=scope_id,
        system_role="admin",
        visibility_scope="organization",
        display_name="成长成员",
    )
    other = SessionIdentity(
        session_id="session_gc13_other",
        principal_id="principal_gc13_other",
        membership_id="membership_gc13_other",
        organization_id=organization_id,
        cloud_instance_id=cloud_instance_id,
        scope_id=scope_id,
        system_role="member",
        visibility_scope="organization",
        display_name="其他成员",
    )
    return repository, owner, other


def _rule(*, expected_version: int = 0, points: int = 20) -> dict:
    return {
        "metricKey": "reflective_practice",
        "label": "复盘实践",
        "abilityKey": "reflection",
        "abilityLabel": "复盘能力",
        "evidenceCategories": ["reflection"],
        "pointsPerEvidence": points,
        "maxScore": 100,
        "badgeThresholds": [
            {"badgeKey": "reflection_starter", "label": "复盘起步", "minimum": 20},
            {"badgeKey": "reflection_builder", "label": "复盘进阶", "minimum": 60},
        ],
        "expectedRuleVersion": expected_version,
    }


def _evidence(summary: str = "我已经把一次复杂协作整理成可复用的复盘方法。") -> dict:
    return {
        "summary": summary,
        "category": "reflection",
        "sourceType": "manual_reflection",
        "contributionScore": 1,
    }


def test_gc13_authority_rebuild_and_rule_version_keep_evidence_immutable(
    tmp_path: Path,
) -> None:
    repository, identity, other = _seed(tmp_path)
    rule = publish_growth_rule(
        repository,
        identity,
        payload=_rule(),
        idempotency_key="gc13-rule-v1",
    )
    assert rule["rule"]["ruleVersion"] == 1
    rule_replay = publish_growth_rule(
        repository,
        identity,
        payload=_rule(),
        idempotency_key="gc13-rule-v1",
    )
    assert rule_replay["idempotentReplay"] is True
    confirmed = confirm_growth_evidence(
        repository,
        identity,
        payload=_evidence(),
        idempotency_key="gc13-evidence-1",
    )
    assert confirmed["skillCreated"] is False
    assert confirmed["projectMemoryConsumed"] is False
    evidence_replay = confirm_growth_evidence(
        repository,
        identity,
        payload=_evidence(),
        idempotency_key="gc13-evidence-1",
    )
    assert evidence_replay["idempotentReplay"] is True
    before_rebuild = growth_snapshot(repository, identity)
    assert before_rebuild["readModel"]["state"] == "updating"
    assert [item["label"] for item in before_rebuild["companion"]["allowedPreferences"]] == ["学习节奏"]
    assert "不得被成长陪伴读取" not in json.dumps(before_rebuild, ensure_ascii=False)
    assert "不得泄露给当前成员" not in json.dumps(before_rebuild, ensure_ascii=False)

    with runtime_connection(repository.database_path, "cloud") as connection:
        evidence_before = tuple(
            connection.execute(
                "SELECT version,validation_state,content_hash,updated_at "
                "FROM growth_evidence WHERE id=?",
                (confirmed["evidence"]["evidenceId"],),
            ).fetchone()
        )
    rebuilt = rebuild_growth_read_models(
        repository,
        identity,
        idempotency_key="gc13-rebuild-v1",
    )
    assert rebuilt["state"] == "ready"
    assert rebuilt["evidenceCount"] == 1
    rebuild_replay = rebuild_growth_read_models(
        repository,
        identity,
        idempotency_key="gc13-rebuild-v1",
    )
    assert rebuild_replay["idempotentReplay"] is True
    snapshot = growth_snapshot(repository, identity)
    assert snapshot["readModel"]["state"] == "ready"
    assert snapshot["readModel"]["metrics"][0]["score"] == 20
    badge_states = {
        item["badgeKey"]: item["state"] for item in snapshot["readModel"]["badges"]
    }
    assert badge_states == {
        "reflection_starter": "earned",
        "reflection_builder": "locked",
    }
    assert snapshot["readModel"]["abilities"][0]["label"] == "复盘能力"

    updated_rule = publish_growth_rule(
        repository,
        identity,
        payload=_rule(expected_version=1, points=30),
        idempotency_key="gc13-rule-v2",
    )
    assert updated_rule["rule"]["ruleVersion"] == 2
    assert growth_snapshot(repository, identity)["readModel"]["state"] == "updating"
    rebuild_growth_read_models(
        repository,
        identity,
        idempotency_key="gc13-rebuild-v2",
    )
    assert growth_snapshot(repository, identity)["readModel"]["metrics"][0]["score"] == 30

    other_snapshot = growth_snapshot(repository, other)
    assert other_snapshot["evidence"] == []
    assert [item["label"] for item in other_snapshot["companion"]["allowedPreferences"]] == ["他人偏好"]
    assert "先给一个可实践的小步骤" not in json.dumps(other_snapshot, ensure_ascii=False)

    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "UPDATE bot_definitions SET enabled=0 WHERE agent_kind='growth_companion'"
        )
        connection.commit()
    base_mode = growth_snapshot(repository, identity)
    assert base_mode["companion"]["mode"] == "base_mode"
    assert base_mode["companion"]["allowedPreferences"] == []
    assert base_mode["evidence"][0]["evidenceId"] == confirmed["evidence"]["evidenceId"]

    with runtime_connection(repository.database_path, "cloud") as connection:
        evidence_after = tuple(
            connection.execute(
                "SELECT version,validation_state,content_hash,updated_at "
                "FROM growth_evidence WHERE id=?",
                (confirmed["evidence"]["evidenceId"],),
            ).fetchone()
        )
        assert evidence_after == evidence_before
        assert len(user_tables(connection)) == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM automation_rules").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM growth_evidence").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM growth_rule_versions"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM growth_read_models WHERE invalidated_at IS NULL"
        ).fetchone()[0] == 5


def test_gc13_failure_is_retryable_and_never_rolls_back_evidence(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _seed(tmp_path)
    publish_growth_rule(
        repository,
        identity,
        payload=_rule(),
        idempotency_key="gc13-failure-rule",
    )
    confirmed = confirm_growth_evidence(
        repository,
        identity,
        payload=_evidence("我确认本周形成了更稳定的复盘节奏。"),
        idempotency_key="gc13-failure-evidence",
    )

    def fail_evaluator(*_: object) -> list[dict]:
        raise RuntimeError("synthetic projection failure")

    failed = rebuild_growth_read_models(
        repository,
        identity,
        idempotency_key="gc13-rebuild-failed",
        evaluator=fail_evaluator,
    )
    assert failed["state"] == "failed_retryable"
    assert failed["evidencePreserved"] is True
    failed_snapshot = growth_snapshot(repository, identity)
    assert failed_snapshot["rebuild"]["state"] == "failed_retryable"
    assert failed_snapshot["evidence"][0]["evidenceId"] == confirmed["evidence"]["evidenceId"]

    retry = rebuild_growth_read_models(
        repository,
        identity,
        idempotency_key="gc13-rebuild-retry",
    )
    assert retry["state"] == "ready"
    assert growth_snapshot(repository, identity)["readModel"]["state"] == "ready"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM reconciliation_runs WHERE status='failed'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM growth_evidence").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc13_rejects_project_memory_and_skill_sources(tmp_path: Path) -> None:
    repository, identity, _ = _seed(tmp_path)
    for source_type in ("project_memory", "project_collaboration_memory", "agent_skill"):
        with pytest.raises(RepositoryError) as error:
            confirm_growth_evidence(
                repository,
                identity,
                payload={
                    **_evidence(),
                    "sourceType": source_type,
                    "sourceId": f"source-{source_type}",
                    "sourceHash": "a" * 64,
                },
                idempotency_key=f"gc13-forbidden-{source_type}",
            )
        assert error.value.code == "gc13_evidence_source_forbidden"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM growth_evidence").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM automation_rules").fetchone()[0] == 0


def test_gc13_weekly_review_port_only_persists_after_explicit_confirmation(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _seed(tmp_path)
    candidate = WeeklyReviewGrowthCandidate(
        candidate_id="candidate_gc06_gc13_1",
        review_id="review_gc06_1",
        review_version_id="review_version_gc06_2",
        source_version=2,
        source_hash="b" * 64,
        summary="我确认本周已经把零散经验整理成可复用的复盘步骤。",
        category="reflection",
    )
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM growth_evidence").fetchone()[0] == 0
    confirmed = confirm_weekly_review_candidate(
        repository,
        identity,
        candidate=candidate,
        idempotency_key="gc13-weekly-adapter-confirm",
    )
    assert confirmed["evidence"]["sourceType"] == "weekly_review_candidate"
    assert confirmed["evidence"]["sourceId"] == "candidate_gc06_gc13_1"
    module_source = inspect.getsource(gc13_growth)
    # The Growth Companion may read this member's submitted formal review on
    # rebuild.  The separate GC06 candidate port still requires confirmation.
    assert "FROM weekly_reviews" in module_source
    assert "JOIN weekly_review_versions" in module_source
    assert "weekly_review_candidate" in module_source


def test_gc13_isolated_cloud_registrar_and_local_router_are_mountable(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _seed(tmp_path)
    app = FastAPI()

    def current_identity() -> SessionIdentity:
        return identity

    register_gc13_growth_routes(app, repository, current_identity)
    with TestClient(app) as client:
        response = client.get("/api/v2/gc13/growth")
        assert response.status_code == 200, response.text
        assert response.json()["schema"] == "yiyu.gc13.growth-snapshot.v1"
        for path in ("overview", "workbench", "badges", "ledger", "experience-wall"):
            compatibility_response = client.get(f"/api/v2/gc13/growth/{path}")
            assert compatibility_response.status_code == 200, compatibility_response.text

    routers = []
    register_gc13_growth_ui_domain(routers)
    register_gc13_growth_ui_domain(routers)
    assert routers == [local_router]
    route_pairs = {(route.method, route.pattern) for route in local_router.routes}
    assert ("GET", r"growth/overview") in route_pairs
    assert ("GET", r"growth/experience-wall") in route_pairs
    assert (
        "POST",
        r"growth/recommendations/(?P<recommendation_id>[^/]+)/(?P<action>accept|dismiss)",
    ) in route_pairs

    calls: list[tuple] = []

    class Runtime:
        def cloud_query(self, path: str) -> dict:
            calls.append(("GET", path))
            return {"schema": "yiyu.gc13.growth-snapshot.v1"}

        def cloud_command(
            self,
            method: str,
            path: str,
            *,
            payload: dict,
            idempotency_key: str,
            refresh_business: bool,
        ) -> dict:
            calls.append((method, path, payload, idempotency_key, refresh_business))
            return {"state": "accepted"}

    compatibility = SimpleNamespace(runtime=Runtime())
    result = local_router.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="gc13/growth/rebuild",
            query={},
            body={},
            idempotency_key="gc13-local-rebuild",
        ),
    )
    assert result == {"state": "accepted"}
    assert calls == [
        (
            "POST",
            "/api/v2/gc13/growth/rebuild",
            {},
            "gc13-local-rebuild:models",
            False,
        ),
        ("GET", "/api/v2/gc13/growth"),
    ]


def test_growth_companion_derives_current_week_task_and_supports_member_exclusion(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _seed(tmp_path)
    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,"
            "resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
            "VALUES ('task_growth_auto',?,'task','active',1,'task',?,?,NULL,'cloud',?)",
            (identity.scope_id, now, now, identity.cloud_instance_id),
        )
        connection.execute(
            "INSERT INTO tasks (id,scope_id,creator_membership_id,lifecycle_state,version,title,"
            "completed_at,created_at,updated_at,deleted_at) VALUES "
            "('task_growth_auto',?,?,'active',1,'完成本周自动化梳理',?,?,?,NULL)",
            (identity.scope_id, identity.membership_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO task_collaborators (id,scope_id,task_id,subject_membership_id,role_key,"
            "assignment_state,inbox_status,assigned_at,responded_at,version,lifecycle_state,"
            "created_at,updated_at,deleted_at) VALUES ('task_member_growth_auto',?,"
            "'task_growth_auto',?,'owner','assigned','accepted',?,?,1,'active',?,?,NULL)",
            (identity.scope_id, identity.membership_id, now, now, now, now),
        )
        connection.commit()
    rebuilt = rebuild_growth_read_models(
        repository,
        identity,
        idempotency_key="gc13-auto-rebuild",
    )
    assert rebuilt["autoEvidenceCreated"] == 1
    snapshot = growth_snapshot(repository, identity)
    derived = next(item for item in snapshot["evidence"] if item["sourceType"] == "formal_task")
    overview = gc13_growth.growth_compatibility_view(
        repository,
        identity,
        view="overview",
    )
    assert overview["totalXp"] == 10
    assert overview["weeklyXp"] == 10
    assert overview["sourceCoverage"]["taskSignals"] == 1
    assert overview["dailyActivity"]["activeDays"] == 1
    assert overview["commitmentSummary"]["fulfilledCount"] == 1
    assert overview["workTypeDistribution"] == {
        "slices": [{"label": "任务推进", "count": 1}],
        "totalTasks": 1,
        "unlabeledTasks": 0,
    }
    badges = gc13_growth.growth_compatibility_view(
        repository,
        identity,
        view="badges",
    )
    assert badges["overview"]["totalBadges"] == 4
    assert badges["overview"]["litBadges"] == 1
    assert [
        badge["state"]
        for category in badges["categories"]
        for badge in category["badges"]
        if category["litCount"]
    ] == ["lit"]
    excluded = update_growth_evidence(
        repository,
        identity,
        evidence_id=derived["evidenceId"],
        action="exclude",
        payload={"expectedVersion": derived["version"]},
        idempotency_key="gc13-auto-exclude",
    )
    assert excluded["state"] == "excluded"
    assert not any(
        item["evidenceId"] == derived["evidenceId"]
        for item in growth_snapshot(repository, identity)["evidence"]
    )
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert len(user_tables(connection)) == 88
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_growth_companion_summary_is_traced_and_unchanged_inputs_do_not_duplicate_models(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _seed(tmp_path)
    confirmed = confirm_growth_evidence(
        repository,
        identity,
        payload=_evidence(),
        idempotency_key="gc13-summary-evidence",
    )
    first = rebuild_growth_read_models(
        repository,
        identity,
        idempotency_key="gc13-summary-rebuild-1",
    )
    assert first["state"] == "ready"
    with runtime_connection(repository.database_path, "cloud") as connection:
        model_count = connection.execute("SELECT COUNT(*) FROM growth_read_models").fetchone()[0]
        assert connection.execute(
            "SELECT COUNT(*) FROM chart_read_models WHERE invalidated_at IS NULL"
        ).fetchone()[0] == model_count
    unchanged = rebuild_growth_read_models(
        repository,
        identity,
        idempotency_key="gc13-summary-rebuild-2",
    )
    assert unchanged["unchanged"] is True
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM growth_read_models").fetchone()[0] == model_count
    snapshot = growth_snapshot(repository, identity)
    summary = record_growth_companion_summary(
        repository,
        identity,
        payload={
            "sourceFingerprint": snapshot["companion"]["sourceFingerprint"],
            "weeklySummary": "本周完成一次可追源的复盘沉淀。",
            "patterns": ["能够把实践整理为正式证据"],
            "blindSpots": ["协作类证据仍不足"],
            "suggestions": ["下周补充一条协作实践证据"],
            "growthHighlights": [
                {
                    "abilityKey": "insight",
                    "abilityLabel": "用户洞察",
                    "title": "用户视角正在形成",
                    "summary": "开始从用户交互成本判断功能取舍。",
                    "trend": "up",
                    "level": 2,
                }
            ],
            "experienceEntries": [
                {
                    "kind": "distilled",
                    "text": "功能取舍要同时考虑架构边界和用户交互成本。",
                    "category": "用户洞察",
                    "sourceType": "weekly_review",
                    "sourceId": "review-growth-1",
                    "sourceTitle": "本周复盘",
                }
            ],
            "modelName": "test-model",
        },
        idempotency_key="gc13-summary-record",
    )
    assert summary["agentRun"]["state"] == "completed"
    refreshed = growth_snapshot(repository, identity)
    assert refreshed["companion"]["summary"]["weeklySummary"] == "本周完成一次可追源的复盘沉淀。"
    assert refreshed["companion"]["summary"]["sourceCount"] == 1
    overview = gc13_growth.growth_compatibility_view(
        repository,
        identity,
        view="overview",
    )
    assert any(item["abilityKey"] == "insight" for item in overview["abilities"])
    wall = gc13_growth.growth_compatibility_view(
        repository,
        identity,
        view="experience-wall",
    )
    assert wall["authorityState"] == "ready"
    assert wall["items"][0]["text"] == "功能取舍要同时考虑架构边界和用户交互成本。"
    liked = like_growth_experience_quote(
        repository,
        identity,
        quote_id=wall["items"][0]["id"],
        idempotency_key="gc13-like-growth-experience",
    )
    assert liked["currentUserLiked"] is True
    assert liked["likeCount"] == 1
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs WHERE run_kind='weekly_growth_summary'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM automation_rules"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM tasks"
        ).fetchone()[0] == 0
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert confirmed["evidence"]["evidenceId"] in {
        item["evidenceId"] for item in refreshed["evidence"]
    }
