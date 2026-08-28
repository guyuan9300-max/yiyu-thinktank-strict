from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.bounded_dispatch import (
    BoundedDispatch,
    DispatchBusyError,
)
from backend.app.cloud_client import CloudClientPool
from backend.app.config import LocalConfig
from backend.app.main import create_app


def _config(tmp_path: Path) -> LocalConfig:
    data_dir = tmp_path / "bounded-dispatch"
    return LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="bounded-dispatch-token",
        secret_namespace="test.strict.bounded-dispatch",
        test_mode=True,
    )


def test_slow_ui_dispatch_does_not_block_health(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    started = threading.Event()
    release = threading.Event()

    def slow_dispatch(*_args, **_kwargs):
        started.set()
        release.wait(timeout=2)
        return {"status": "ready"}

    app.state.ui_dispatch._dispatch = slow_dispatch
    headers = {"X-Yiyu-Desktop-Token": "bounded-dispatch-token"}
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            client.get,
            "/api/v2/ui/slow-route",
            headers=headers,
        )
        assert started.wait(timeout=1)
        before = time.perf_counter()
        health = client.get("/api/v2/health")
        elapsed = time.perf_counter() - before
        release.set()
        response = pending.result(timeout=2)

    assert health.status_code == 200
    assert elapsed < 0.5
    assert response.status_code == 200


def test_bounded_dispatch_rejects_excess_work() -> None:
    started = threading.Event()
    release = threading.Event()

    def blocked():
        started.set()
        release.wait(timeout=2)
        return "done"

    dispatch = BoundedDispatch(
        blocked,
        max_workers=1,
        max_queued=0,
        deadline_seconds=1,
    )

    async def exercise() -> None:
        first = asyncio.create_task(dispatch.run())
        assert await asyncio.to_thread(started.wait, 1)
        with pytest.raises(DispatchBusyError):
            await dispatch.run()
        release.set()
        assert await first == "done"

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        dispatch.close()


def test_cloud_client_pool_reuses_normalized_cloud() -> None:
    pool = CloudClientPool()
    try:
        first = pool("http://127.0.0.1:47930/")
        second = pool("http://127.0.0.1:47930")
        assert first is second
    finally:
        pool.close()


def test_identical_inflight_reads_are_coalesced() -> None:
    started = threading.Event()
    release = threading.Event()
    calls = {"count": 0}

    def read_once():
        calls["count"] += 1
        started.set()
        release.wait(timeout=2)
        return {"status": "ready"}

    dispatch = BoundedDispatch(read_once, max_workers=2)

    async def exercise() -> None:
        first = asyncio.create_task(
            dispatch.run(coalesce_key=("sandbox-a", "GET", "tasks"))
        )
        assert await asyncio.to_thread(started.wait, 1)
        second = asyncio.create_task(
            dispatch.run(coalesce_key=("sandbox-a", "GET", "tasks"))
        )
        await asyncio.sleep(0.05)
        release.set()
        assert await first == {"status": "ready"}
        assert await second == {"status": "ready"}

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        dispatch.close()
    assert calls["count"] == 1


def test_background_processing_cannot_starve_foreground_ui(
    tmp_path: Path,
) -> None:
    app = create_app(_config(tmp_path))
    background_started = threading.Event()
    background_release = threading.Event()

    def slow_background(*_args, **_kwargs):
        background_started.set()
        background_release.wait(timeout=2)
        return {"status": "completed"}

    app.state.background_ui_dispatch._dispatch = slow_background
    app.state.ui_dispatch._dispatch = lambda *_args, **_kwargs: {
        "status": "ready"
    }
    app.state.ui_compat.capture_dispatch_workspace = (
        lambda *_args, **_kwargs: None
    )
    headers = {"X-Yiyu-Desktop-Token": "bounded-dispatch-token"}
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            client.post,
            "/api/v2/ui/consultation/knowledge-requests/process-pending",
            headers=headers,
            json={},
        )
        assert background_started.wait(timeout=1)
        before = time.perf_counter()
        foreground = client.get(
            "/api/v2/ui/system/health",
            headers=headers,
        )
        elapsed = time.perf_counter() - before
        background_release.set()
        background = pending.result(timeout=2)

    assert foreground.status_code == 200
    assert foreground.json()["status"] == "ready"
    assert elapsed < 0.5
    assert background.status_code == 200


def test_workbench_chat_uses_dedicated_interactive_ai_lane(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    captured: dict[str, object] = {}

    def interactive(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"status": "accepted", "lane": "interactive_ai"}

    app.state.interactive_ai_dispatch._dispatch = interactive
    app.state.ui_dispatch._dispatch = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("工作台问答不得进入普通20秒UI通道")
    )
    app.state.ui_compat.capture_dispatch_workspace = lambda *_args, **_kwargs: None
    headers = {"X-Yiyu-Desktop-Token": "bounded-dispatch-token"}
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/ui/clients/client-a/workspace/chat/start",
            headers=headers,
            json={"prompt": "测试专用交互通道"},
        )

    assert response.status_code == 200
    assert response.json()["lane"] == "interactive_ai"
    assert captured["args"][:2] == (
        "POST",
        "clients/client-a/workspace/chat/start",
    )


def test_document_ai_action_uses_dedicated_interactive_ai_lane(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    captured: dict[str, object] = {}

    def interactive(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"status": "ready", "lane": "interactive_ai"}

    app.state.interactive_ai_dispatch._dispatch = interactive
    app.state.ui_dispatch._dispatch = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("智能编辑不得进入普通20秒UI通道")
    )
    app.state.ui_compat.capture_dispatch_workspace = lambda *_args, **_kwargs: None
    headers = {"X-Yiyu-Desktop-Token": "bounded-dispatch-token"}
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/ui/clients/client-a/documents/ai-action",
            headers=headers,
            json={"content": "待扩写正文", "action": "expand"},
        )

    assert response.status_code == 200
    assert response.json()["lane"] == "interactive_ai"
    assert captured["args"][:2] == (
        "POST",
        "clients/client-a/documents/ai-action",
    )


def test_plan_parse_uses_dedicated_interactive_ai_lane(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    captured: dict[str, object] = {}

    def interactive(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"items": [], "lane": "interactive_ai"}

    app.state.interactive_ai_dispatch._dispatch = interactive
    app.state.ui_dispatch._dispatch = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("计划 AI 拆解不得进入普通20秒UI通道")
    )
    app.state.ui_compat.capture_dispatch_workspace = lambda *_args, **_kwargs: None
    headers = {"X-Yiyu-Desktop-Token": "bounded-dispatch-token"}
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/ui/org-model/plans/parse",
            headers=headers,
            json={"text": "把组织季度重点拆解为多条平级计划"},
        )

    assert response.status_code == 200
    assert response.json()["lane"] == "interactive_ai"
    assert captured["args"][:2] == (
        "POST",
        "org-model/plans/parse",
    )
