from pathlib import Path
from contextlib import nullcontext

from cloud_backend.app.repositories.gc14_proposals import (
    create_proposal,
    decide_proposal,
    execute_proposal,
    execution_preview,
    list_proposals,
)
from strict_common.physical_schema import user_tables
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository
from backend.app.ui_domains.gc14_proposals import router as gc14_ui_router
from backend.app.ui_domains.workbench_outputs import router as workbench_router
from backend.app.ui_domains.routing import UiRequest


def test_gc14_answer_to_explicit_proposal_approval_and_controlled_execution(
    tmp_path: Path,
) -> None:
    repository, identity, answer_payload = _repository(tmp_path)
    repository.save_ai_answer(
        identity,
        payload=answer_payload,
        idempotency_key="gc14-proposal-source-answer",
    )
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM ai_approvals").fetchone()[0] == 0

    proposal = create_proposal(
        repository,
        identity,
        payload={
            "clientId": answer_payload["projectId"],
            "answerId": answer_payload["answerId"],
            "kind": "context_refresh",
            "title": "刷新项目问答上下文",
            "summary": "仅登记用户确认的上下文刷新，不直接修改任务或项目事实。",
            "rationale": "当前回答的上下文需要重新构建",
            "riskLevel": "low",
            "sourceRefs": [f"ai_answer:{answer_payload['answerId']}@1"],
            "boundaryNotes": ["不产生隐藏业务写入"],
            "payload": {"requestedAction": "refresh_context"},
        },
        idempotency_key="gc14-proposal-create",
    )
    assert proposal["status"] == "draft"
    assert proposal["answerId"] == answer_payload["answerId"]
    assert len(
        list_proposals(
            repository,
            identity,
            client_id=answer_payload["projectId"],
        )
    ) == 1
    preview = execution_preview(
        repository, identity, proposal_id=proposal["id"]
    )
    assert preview["executionType"] == "recorded_only"
    assert preview["willCreateTask"] is False

    approved = decide_proposal(
        repository,
        identity,
        proposal_id=proposal["id"],
        decision="approved",
        payload={"expectedVersion": 1, "note": "同意仅刷新上下文"},
        idempotency_key="gc14-proposal-approve",
    )
    assert approved["status"] == "approved"
    assert approved["version"] == 2

    executed = execute_proposal(
        repository,
        identity,
        proposal_id=proposal["id"],
        payload={},
        idempotency_key="gc14-proposal-execute",
    )
    replayed = execute_proposal(
        repository,
        identity,
        proposal_id=proposal["id"],
        payload={},
        idempotency_key="gc14-proposal-execute",
    )
    assert executed["status"] == "executed"
    assert executed["executionTicket"]["result"]["resultType"] == "recorded_only"
    assert executed["executionTicket"]["result"]["createdTaskIds"] == []
    assert replayed["id"] == executed["id"]
    assert replayed["idempotentReplay"] is True

    with runtime_connection(repository.database_path, "cloud") as connection:
        assert len(user_tables(connection)) == 88
        assert connection.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ai_approvals").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs WHERE proposal_id=? "
            "AND run_kind='proposal_controlled_execution' AND status='executed'",
            (proposal["id"],),
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc14_ui_proposal_router_uses_dedicated_strict_v2_surface() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, object]] = []

        def pinned_workspace_context(self):
            return nullcontext()

        def cloud_query(self, path: str, query=None):
            self.calls.append(("GET", path, query or {}))
            if path.endswith("/proposal_test"):
                return {"id": "proposal_test", "version": 2}
            return []

        def cloud_command(self, method: str, path: str, *, payload, idempotency_key):
            self.calls.append((method, path, dict(payload)))
            return {"id": "proposal_test", "status": "approved", "version": 3}

    compatibility = type("Compatibility", (), {"runtime": Runtime()})()
    listed = gc14_ui_router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="proposals",
            query={"clientId": "client_test"},
            body={},
            idempotency_key="list",
        ),
    )
    assert listed == []
    approved = gc14_ui_router.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="proposals/proposal_test/approve",
            query={},
            body={"note": "同意"},
            idempotency_key="approve",
        ),
    )
    assert approved["status"] == "approved"
    assert compatibility.runtime.calls == [
        ("GET", "/api/v2/ai-proposals", {"clientId": "client_test"}),
        ("GET", "/api/v2/ai-proposals/proposal_test", {}),
        (
            "POST",
            "/api/v2/ai-proposals/proposal_test/approve",
            {"note": "同意", "expectedVersion": 2},
        ),
    ]


def test_gc14_answer_action_creates_ai_proposal_not_intelligence_draft() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.command: tuple[str, str, dict] | None = None

        def pinned_workspace_context(self):
            return nullcontext()

        def workbench_answer(self, answer_id: str):
            return {
                "answerId": answer_id,
                "projectId": "client_test",
                "question": "下一步怎么做？",
                "answerMarkdown": "建议先核实事实，再形成任务草案。",
                "version": 1,
            }

        def cloud_command(self, method: str, path: str, *, payload, idempotency_key):
            self.command = (method, path, dict(payload))
            return {"id": "proposal_from_answer", "status": "draft"}

    compatibility = type("Compatibility", (), {"runtime": Runtime()})()
    result = workbench_router.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="workspace-answer-action-cards/answer_test/create-proposal",
            query={},
            body={},
            idempotency_key="answer-proposal",
        ),
    )
    assert result["proposalId"] == "proposal_from_answer"
    assert compatibility.runtime.command is not None
    method, path, payload = compatibility.runtime.command
    assert (method, path) == ("POST", "/api/v2/ai-proposals")
    assert payload["answerId"] == "answer_test"
    assert payload["clientId"] == "client_test"
