from __future__ import annotations

from pathlib import Path

from strict_common.ids import utc_now
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository

from cloud_backend.app.repositories.knowledge_governance_88 import (
    KnowledgeGovernance88Repository,
)
from cloud_backend.app.repository import SessionIdentity
from backend.app.ui_domains.project_materials import router
from backend.app.ui_domains.routing import UiRequest


def test_governance_decision_is_versioned_and_idempotent_on_88_tables(
    tmp_path: Path,
) -> None:
    repository, identity, payload = _repository(tmp_path)
    domain = KnowledgeGovernance88Repository(repository)
    body = {
        "decisionKind": "fact_contradiction",
        "reviewStatus": "resolved",
        "acceptedFactId": "fact-accepted",
        "resolutionNote": "采用人工核实的当前口径",
    }
    first = domain.record_decision(
        identity,
        project_id=payload["projectId"],
        derived_id="derived_contradiction_test",
        payload=body,
        idempotency_key="governance-test-1",
    )
    replay = domain.record_decision(
        identity,
        project_id=payload["projectId"],
        derived_id="derived_contradiction_test",
        payload=body,
        idempotency_key="governance-test-1",
    )
    assert replay == first
    listed = domain.list_decisions(
        identity,
        project_id=payload["projectId"],
        decision_kind="fact_contradiction",
    )
    assert listed["count"] == 1
    assert listed["decisions"][0]["acceptedFactId"] == "fact-accepted"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert len(connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()) == 88
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM ai_proposals WHERE operation_kind='fact_contradiction_review'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ai_approvals").fetchone()[0] == 1


def test_shared_project_member_can_read_governance_decisions(tmp_path: Path) -> None:
    repository, owner, payload = _repository(tmp_path)
    now = utc_now()
    with runtime_connection(repository.database_path, "cloud") as connection:
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,"
            "principal_kind,display_name,version,lifecycle_state,created_at,deleted_at) "
            "VALUES ('principal_shared','active',1,?,'person','共享成员',1,'active',?,NULL)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
            "status,version,record_kind,visibility_scope,lifecycle_state,created_at,"
            "updated_at,deleted_at) VALUES ('membership_shared',?,'principal_shared',"
            "'member','active',1,'membership','organization','active',?,?,NULL)",
            (owner.scope_id, now, now),
        )
        connection.execute(
            "INSERT INTO object_grants (id,scope_id,secured_resource_id,policy_version_id,"
            "subject_principal_id,subject_membership_id,capability_set_schema_version,"
            "capability_set,grant_generation,status,grant_source_set_id,created_at,"
            "updated_at,revoked_at,version,lifecycle_state,deleted_at) VALUES "
            "('grant_shared',?,?,'policy_gc14_project',NULL,'membership_shared','1',"
            "'{\"read\":true,\"write\":false,\"contributeKnowledge\":true,"
            "\"manageSharing\":false}',1,'active',NULL,?,?,NULL,1,'active',NULL)",
            (owner.scope_id, payload["projectId"], now, now),
        )
        connection.commit()
    shared = SessionIdentity(
        session_id="session_shared",
        principal_id="principal_shared",
        membership_id="membership_shared",
        organization_id=owner.organization_id,
        cloud_instance_id=owner.cloud_instance_id,
        scope_id=owner.scope_id,
        system_role="member",
        visibility_scope="organization",
        display_name="共享成员",
    )
    listed = KnowledgeGovernance88Repository(repository).list_decisions(
        shared,
        project_id=payload["projectId"],
        decision_kind="glossary_drift",
    )
    assert listed == {"decisions": [], "count": 0}


def test_ui_contradiction_review_keeps_project_scope_and_reviews_real_facts() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.commands: list[tuple[str, str, dict]] = []

        @staticmethod
        def cloud_query(path: str, *, query: dict | None = None) -> dict:
            del query
            if path.endswith("/glossary-attributes"):
                return {
                    "attributes": [
                        {"id": "fact-a", "term": "人数", "attribute_name": "目标", "value_text": "10", "verification_status": "verified"},
                        {"id": "fact-b", "term": "人数", "attribute_name": "目标", "value_text": "20", "verification_status": "pending"},
                    ]
                }
            if path.endswith("/governance-decisions"):
                return {"decisions": []}
            raise AssertionError(path)

        def cloud_command(self, method: str, path: str, *, payload: dict, idempotency_key: str, **_: object) -> dict:
            self.commands.append((method, path, dict(payload)))
            return {"ok": True, "status": payload.get("reviewStatus")}

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

    compatibility = Compatibility()
    listed = router.dispatch(
        compatibility,
        UiRequest("GET", "clients/client-a/contradictions", {"status": "pending"}, {}, "list"),
    )
    contradiction = listed["contradictions"][0]
    result = router.dispatch(
        compatibility,
        UiRequest(
            "POST",
            f"clients/client-a/contradictions/{contradiction['id']}/review",
            {},
            {"reviewStatus": "resolved", "acceptedFactId": "fact-b"},
            "review-1",
        ),
    )
    assert result["ok"] is True
    assert all("/projects/client-a/" in path for _method, path, _payload in compatibility.runtime.commands)
    assert compatibility.runtime.commands[-1][1].endswith(
        f"/governance-decisions/{contradiction['id']}"
    )
