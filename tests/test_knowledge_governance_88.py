from __future__ import annotations

from pathlib import Path

from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository

from cloud_backend.app.repositories.knowledge_governance_88 import (
    KnowledgeGovernance88Repository,
)
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
