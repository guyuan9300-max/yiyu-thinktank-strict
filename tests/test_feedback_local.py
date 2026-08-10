from __future__ import annotations

from contextlib import nullcontext
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.feedback_local import LocalFeedbackArtifactRepository
from backend.app.runtime import LocalRuntimeError
from backend.app.ui_domains.platform_integrations import (
    create_software_feedback,
)
from backend.app.ui_domains.routing import UiRequest


class _Runtime:
    def __init__(self, database_path: Path, sandbox_id: str = "sandbox-a"):
        self.database_path = database_path
        self.sandbox_id = sandbox_id
        self.rows: dict[str, dict[str, Any]] = {}

    def _current_context(self, *, require_ready: bool) -> Any:
        assert require_ready is True
        return SimpleNamespace(sandbox_id=self.sandbox_id)

    def local_storage_object_lock(self, **_: Any) -> Any:
        return nullcontext()

    def local_storage_object_get(
        self,
        *,
        sandbox_id: str,
        object_id: str,
    ) -> dict[str, Any] | None:
        assert sandbox_id == self.sandbox_id
        return self.rows.get(object_id)

    def local_storage_object_put(self, **values: Any) -> dict[str, Any]:
        assert values["sandbox_id"] == self.sandbox_id
        row = {
            "object_id": values["object_id"],
            "storage_key": values["storage_key"],
            "content_hash": values["content_hash"],
            "media_type": values["media_type"],
            "byte_size": values["byte_size"],
            "version": 1,
        }
        self.rows[values["object_id"]] = row
        return {"version": 1, "updatedAt": "2026-07-31T00:00:00Z"}

    def cloud_command(self, *_: Any, **values: Any) -> dict[str, Any]:
        self.cloud_payload = values["payload"]
        return {
            "result": {
                "queued": False,
                "state": "not_connected",
                "record": {
                    "screenshotState": "local_saved",
                    "centralStatus": "not_connected",
                },
            }
        }


def test_feedback_screenshot_is_local_idempotent_and_sandbox_scoped(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(tmp_path / "strict-local.db")
    repository = LocalFeedbackArtifactRepository(runtime)  # type: ignore[arg-type]
    png = b"\x89PNG\r\n\x1a\n" + b"safe-image"

    first = repository.store_screenshot(
        data=png,
        media_type="image/png",
        idempotency_key="feedback-command",
    )
    replay = repository.store_screenshot(
        data=png,
        media_type="image/png",
        idempotency_key="feedback-command",
    )

    assert first["contentHash"] == replay["contentHash"]
    assert replay["idempotentReplay"] is True
    assert Path(first["path"]).read_bytes() == png
    assert "sandbox-a" not in first["path"]

    other_runtime = _Runtime(
        tmp_path / "other" / "strict-local.db",
        sandbox_id="sandbox-b",
    )
    other = LocalFeedbackArtifactRepository(  # type: ignore[arg-type]
        other_runtime
    ).store_screenshot(
        data=png,
        media_type="image/png",
        idempotency_key="feedback-command",
    )
    assert other["objectId"] != first["objectId"]


def test_feedback_screenshot_rejects_type_size_and_replay_conflicts(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(tmp_path / "strict-local.db")
    repository = LocalFeedbackArtifactRepository(runtime)  # type: ignore[arg-type]

    with pytest.raises(LocalRuntimeError) as invalid:
        repository.store_screenshot(
            data=b"not-an-image",
            media_type="image/png",
            idempotency_key="invalid",
        )
    assert invalid.value.status_code == 415

    with pytest.raises(LocalRuntimeError) as too_large:
        repository.store_screenshot(
            data=b"\x89PNG\r\n\x1a\n"
            + b"x" * LocalFeedbackArtifactRepository.MAX_SCREENSHOT_BYTES,
            media_type="image/png",
            idempotency_key="too-large",
        )
    assert too_large.value.status_code == 413

    repository.store_screenshot(
        data=b"\x89PNG\r\n\x1a\nfirst",
        media_type="image/png",
        idempotency_key="same-command",
    )
    with pytest.raises(LocalRuntimeError) as conflict:
        repository.store_screenshot(
            data=b"\x89PNG\r\n\x1a\nsecond",
            media_type="image/png",
            idempotency_key="same-command",
        )
    assert conflict.value.status_code == 409


def test_feedback_handler_persists_bytes_locally_and_sends_only_metadata(
    tmp_path: Path,
) -> None:
    runtime = _Runtime(tmp_path / "strict-local.db")
    screenshot_bytes = b"\x89PNG\r\n\x1a\n" + b"feedback-image"
    result = create_software_feedback(
        SimpleNamespace(runtime=runtime),
        UiRequest(
            method="POST",
            path="software-feedback",
            query={},
            body={
                "title": "截图反馈",
                "description": "只在本机保存截图正文",
                "screenshot": SimpleNamespace(
                    filename="evidence.png",
                    content_type="image/png",
                    file=BytesIO(screenshot_bytes),
                ),
            },
            idempotency_key="feedback-handler-1",
        ),
        SimpleNamespace(),
    )

    screenshot_path = Path(result["localScreenshotPath"])
    assert screenshot_path.read_bytes() == screenshot_bytes
    forwarded = runtime.cloud_payload["payload"]
    assert forwarded["screenshotObjectId"] == result["localScreenshotObjectId"]
    assert forwarded["screenshotContentHash"]
    assert forwarded["screenshotByteSize"] == len(screenshot_bytes)
    assert "localScreenshotPath" not in forwarded
    assert "screenshot" not in forwarded
    assert str(screenshot_path) not in repr(runtime.cloud_payload)
