from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.app.config import LocalConfig
from backend.app.main import create_app
from backend.app.ui_idempotency import (
    replayable_cloud_mutation,
    replayable_generated_value,
)
from strict_common.schema import runtime_connection


def _runtime(tmp_path: Path) -> Any:
    data_dir = tmp_path / "strict-local"
    app = create_app(
        LocalConfig(
            data_dir=data_dir,
            database_path=data_dir / "strict-local.db",
            host="127.0.0.1",
            port=47931,
            desktop_token="ui-idempotency-test",
            secret_namespace="test.strict.ui-idempotency",
            test_mode=True,
        )
    )
    return app.state.runtime


def test_cloud_response_loss_reuses_first_cas_payload_and_receipt(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    generated_versions: list[int] = []
    cloud_calls: list[dict[str, Any]] = []
    current_version = 7

    def payload_factory() -> dict[str, Any]:
        generated_versions.append(current_version)
        return {"expectedVersion": current_version, "title": "固定内容"}

    def cloud_command(
        method: str,
        path: str,
        *,
        payload: dict[str, Any],
        idempotency_key: str,
        refresh_business: bool = True,
    ) -> dict[str, Any]:
        del refresh_business
        cloud_calls.append(
            {
                "method": method,
                "path": path,
                "payload": dict(payload),
                "key": idempotency_key,
            }
        )
        if len(cloud_calls) == 1:
            # 模拟组织云已收到同一命令，但 Electron 没拿到回包。
            raise RuntimeError("response lost after commit")
        return {"ok": True, "aggregateVersion": 8}

    runtime.cloud_command = cloud_command
    kwargs = {
        "runtime": runtime,
        "idempotency_key": "save-document-once",
        "command_type": "test.cas_save",
        "aggregate_type": "knowledge_document",
        "aggregate_id": "document-1",
        "method": "PATCH",
        "path": "/api/v2/test/documents/document-1",
        "request_payload": {"title": "固定内容"},
        "cloud_payload_factory": payload_factory,
    }

    with pytest.raises(RuntimeError, match="response lost"):
        replayable_cloud_mutation(**kwargs)

    current_version = 99
    replayed = replayable_cloud_mutation(**kwargs)
    completed = replayable_cloud_mutation(**kwargs)

    assert replayed == completed == {"ok": True, "aggregateVersion": 8}
    assert generated_versions == [7]
    assert len(cloud_calls) == 2
    assert cloud_calls[0]["payload"] == cloud_calls[1]["payload"] == {
        "expectedVersion": 7,
        "title": "固定内容",
    }
    assert cloud_calls[0]["key"] == cloud_calls[1]["key"]
    with runtime_connection(runtime.database_path, "local", read_only=True) as connection:
        tables = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
    assert tables == 88


def test_generated_value_is_created_once_for_local_first_delete(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    calls = 0

    def generate() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"cloudDocumentId": "document-1", "deleted": True}

    kwargs = {
        "runtime": runtime,
        "idempotency_key": "delete-document-once",
        "command_type": "test.local_delete",
        "aggregate_type": "source_asset",
        "aggregate_id": "document-1",
        "input_payload": {"documentId": "document-1"},
        "generate": generate,
    }
    assert replayable_generated_value(**kwargs) == replayable_generated_value(**kwargs)
    assert calls == 1

