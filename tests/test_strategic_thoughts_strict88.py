from contextlib import nullcontext
from pathlib import Path

from backend.app.ui_domains.intelligence_growth import router as intelligence_router
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.repositories.gc14_strategic_profile import rebuild_strategic_profile
from cloud_backend.app.repositories.strategic_thoughts import (
    list_thoughts,
    refresh_thoughts,
    review_thought,
    update_thought_state,
)
from strict_common.physical_schema import user_tables
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _prepared_profile, _repository


def test_strategic_thought_real_profile_to_review_lifecycle_stays_in_88_tables(
    tmp_path: Path,
) -> None:
    repository, identity, answer = _repository(tmp_path)
    client_id = answer["projectId"]
    rebuild_strategic_profile(
        repository,
        identity,
        project_id=client_id,
        idempotency_key="strict-thought-profile",
        prepared_profile=_prepared_profile(),
    )

    derived = list_thoughts(repository, identity, client_id=client_id, limit=1)
    assert derived["items"][0]["version"] == 0
    assert derived["items"][0]["sources"]

    refreshed = refresh_thoughts(
        repository,
        identity,
        client_id=client_id,
        limit=1,
        idempotency_key="strict-thought-refresh",
    )
    thought = refreshed["items"][0]
    assert thought["version"] == 1
    favorite = update_thought_state(
        repository,
        identity,
        thought_id=thought["id"],
        client_id=client_id,
        action="favorite",
        idempotency_key="strict-thought-favorite",
    )
    assert favorite["isFavorite"] is True
    confirmed = review_thought(
        repository,
        identity,
        thought_id=thought["id"],
        client_id=client_id,
        action="confirm",
        note="确认该判断，后续继续核验",
        task_id=None,
        idempotency_key="strict-thought-review",
    )
    assert confirmed["status"] == "confirmed"
    deleted = update_thought_state(
        repository,
        identity,
        thought_id=thought["id"],
        client_id=client_id,
        action="delete",
        idempotency_key="strict-thought-delete",
    )
    assert deleted["isDeleted"] is True

    with runtime_connection(repository.database_path, "cloud") as connection:
        assert len(user_tables(connection)) == 88
        assert connection.execute("SELECT COUNT(*) FROM ai_proposals").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ai_approvals").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_runs "
            "WHERE run_kind='strategic_thought_authority_projection' AND status='completed'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM source_sets WHERE purpose_kind='strategic_thought_favorite'"
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_strategic_thought_ui_routes_use_dedicated_workbench_surface() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, object]] = []

        def pinned_workspace_context(self):
            return nullcontext()

        def cloud_query(self, path: str, query=None):
            self.calls.append(("GET", path, dict(query or {})))
            return {"items": [], "total": 0}

        def cloud_command(self, method: str, path: str, *, payload, idempotency_key):
            self.calls.append((method, path, dict(payload)))
            return {"items": [], "total": 0}

    compatibility = type("Compatibility", (), {"runtime": Runtime()})()
    intelligence_router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="strategic/thoughts",
            query={"clientId": "client_strict"},
            body={},
            idempotency_key="list",
        ),
    )
    intelligence_router.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="strategic/thoughts/refresh",
            query={},
            body={"clientId": "client_strict", "limit": 1},
            idempotency_key="refresh",
        ),
    )
    assert compatibility.runtime.calls == [
        (
            "GET",
            "/api/v2/workbench/strategic-thoughts",
            {"clientId": "client_strict"},
        ),
        (
            "POST",
            "/api/v2/workbench/strategic-thoughts/refresh",
            {"clientId": "client_strict", "limit": 1},
        ),
    ]
