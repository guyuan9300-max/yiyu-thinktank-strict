from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.config import LocalConfig
from backend.app.main import create_app
from backend.app.platform_integrations_local import LocalPlatformOperationRepository
from backend.app.ui_domains import platform_device_runtime, platform_integrations
from backend.app.ui_domains.routing import UiRequest
from strict_common.schema import runtime_connection


def _request(
    method: str,
    path: str,
    *,
    body: dict | None = None,
    key: str = "device-runtime-test",
) -> UiRequest:
    return UiRequest(
        method=method,
        path=path,
        query={},
        body=body or {},
        idempotency_key=key,
    )


def _local(tmp_path: Path) -> TestClient:
    data_dir = tmp_path / "strict-local"
    app = create_app(
        LocalConfig(
            data_dir=data_dir,
            database_path=data_dir / "strict-local.db",
            host="127.0.0.1",
            port=47929,
            desktop_token="device-runtime-token",
            secret_namespace="test.strict.device-runtime",
            test_mode=True,
        )
    )
    app.state.runtime.pinned_workspace_context = lambda: nullcontext()
    return TestClient(app)


def test_device_runtime_router_is_the_exact_existing_clickable_surface() -> None:
    actual = {(item.method, item.pattern) for item in platform_device_runtime.router.routes}
    assert actual == platform_device_runtime.DEVICE_RUNTIME_ROUTES
    assert len(actual) == 41
    platform_source = Path(platform_device_runtime.__file__).read_text(encoding="utf-8")
    local_source = (
        Path(__file__).resolve().parents[1]
        / "backend/app/platform_integrations_local.py"
    ).read_text(encoding="utf-8")
    for frozen in (
        "command_envelopes",
        "workspace_sandboxes",
        "command_idempotency",
        "delivery_outbox",
        "operation_dead_letters",
    ):
        assert frozen not in platform_source
        assert frozen not in local_source


def test_device_model_audio_and_local_ai_routes_use_the_88_table_ledger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _local(tmp_path)
    database = tmp_path / "strict-local" / "strict-local.db"
    monkeypatch.setattr(
        platform_integrations,
        "_ollama_health",
        lambda: {
            "running": False,
            "baseUrl": "http://127.0.0.1:11434",
            "installedModels": [],
            "error": "not running",
            "version": None,
            "state": "not_connected",
            "retryable": True,
            "pollingEnabled": False,
        },
    )

    with client:
        compatibility = client.app.state.ui_compat
        operations = LocalPlatformOperationRepository(client.app.state.runtime)
        audio = operations.begin(
            idempotency_key="audio-job-one",
            command_type="local_asr.transcribe_test",
            aggregate_type="local_audio_transcription",
            aggregate_id="audio-one",
            payload={"audioPathHash": "hash-only"},
            initial_result={
                "state": "processing",
                "pollingEnabled": True,
                "retryable": True,
            },
        )
        operations.update(
            operation_id=str(audio["operationId"]),
            state="completed",
            result_patch={"textHash": "transcript-hash"},
        )

        recent = platform_device_runtime.router.dispatch(
            compatibility,
            _request("GET", "audio-transcription-jobs/recent"),
        )
        blocked_pull = platform_device_runtime.router.dispatch(
            compatibility,
            _request(
                "POST",
                "ollama/pull",
                body={"modelName": "qwen-test:latest"},
                key="ollama-missing-executor",
            ),
        )
        updated_settings = platform_device_runtime.router.dispatch(
            compatibility,
            _request(
                "PUT",
                "local-ai/settings",
                body={"enabled": True, "paused": False, "manualActive": True},
                key="local-ai-settings-real",
            ),
        )
        loaded_settings = platform_device_runtime.router.dispatch(
            compatibility,
            _request("GET", "local-ai/settings"),
        )

    assert recent["state"] == "ready"
    assert len(recent["jobs"]) == 1
    assert recent["jobs"][0]["operationId"] == audio["operationId"]
    assert blocked_pull["state"] == "blocked"
    assert blocked_pull["errorCode"] == "ollama_not_running"
    assert blocked_pull["retryable"] is True
    assert updated_settings["state"] == "ready"
    assert loaded_settings["enabled"] is True

    with runtime_connection(database, "local") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE command_type IN "
            "('local_asr.transcribe_test','ollama.pull','local_ai.settings.update')"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM dead_letters WHERE error_code='ollama_not_running'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM object_manifests WHERE storage_kind='command_receipt'"
        ).fetchone()[0] == 3
