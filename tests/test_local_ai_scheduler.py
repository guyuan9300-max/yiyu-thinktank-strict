from __future__ import annotations

import asyncio
from typing import Any

from backend.app.local_ai_scheduler import LocalAiScheduler
from backend.app.runtime import LocalRuntimeError


def test_local_ai_scheduler_dispatches_real_governed_route() -> None:
    calls: list[dict[str, Any]] = []

    def dispatch(
        method: str,
        path: str,
        *,
        query: dict[str, str],
        body: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        calls.append(
            {
                "method": method,
                "path": path,
                "query": query,
                "body": body,
                "idempotencyKey": idempotency_key,
            }
        )
        if path == "local-ai/settings":
            return {"enabled": True, "manualActive": False}
        if path == "local-ai/backfill":
            return {"created": 1}
        return {"status": "idle", "processed": 0}

    scheduler = LocalAiScheduler(dispatch)
    result = asyncio.run(scheduler.run_cycle())

    assert result == {"status": "idle", "processed": 0}
    assert [call["path"] for call in calls] == [
        "local-ai/settings",
        "local-ai/backfill",
        "local-ai/run-now",
    ]
    assert calls[2]["method"] == "POST"
    assert calls[2]["query"] == {"force": "false"}
    assert calls[2]["idempotencyKey"].startswith("local-ai-scheduler:")


def test_local_ai_scheduler_does_not_enqueue_when_disabled() -> None:
    calls: list[str] = []

    def dispatch(
        method: str,
        path: str,
        **_: Any,
    ) -> dict[str, Any]:
        del method
        calls.append(path)
        return {"enabled": False, "manualActive": False}

    scheduler = LocalAiScheduler(dispatch)
    result = asyncio.run(scheduler.run_cycle())

    assert result == {
        "status": "disabled",
        "processed": 0,
        "failed": 0,
        "skipped": 1,
    }
    assert calls == ["local-ai/settings"]


def test_local_ai_scheduler_treats_logged_out_workspace_as_idle() -> None:
    def dispatch(*_: Any, **__: Any) -> None:
        raise LocalRuntimeError(409, "needs_login", "请先登录")

    scheduler = LocalAiScheduler(dispatch)

    assert asyncio.run(scheduler.run_cycle()) is None
