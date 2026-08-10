from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from strict_common.ids import new_id

from .runtime import LocalRuntimeError, WorkspaceRuntime


class LocalFeedbackArtifactRepository:
    MAX_SCREENSHOT_BYTES = 6 * 1024 * 1024
    _MEDIA_EXTENSIONS = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }

    def __init__(self, runtime: WorkspaceRuntime):
        self.runtime = runtime
        self.data_root = Path(runtime.database_path).resolve().parent

    @staticmethod
    def _stable_segment(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]

    @classmethod
    def _validated_media_type(
        cls,
        data: bytes,
        presented_media_type: str,
    ) -> str:
        detected = (
            "image/png"
            if data.startswith(b"\x89PNG\r\n\x1a\n")
            else "image/jpeg"
            if data.startswith(b"\xff\xd8\xff")
            else "image/webp"
            if len(data) >= 12
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
            else ""
        )
        if not detected or (
            presented_media_type
            and presented_media_type.lower() not in {detected, "image/jpg"}
        ):
            raise LocalRuntimeError(
                415,
                "feedback_screenshot_type_invalid",
                "反馈截图仅支持 PNG、JPEG 或 WebP",
            )
        return detected

    def store_screenshot(
        self,
        *,
        data: bytes,
        media_type: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not data:
            raise LocalRuntimeError(
                422,
                "feedback_screenshot_empty",
                "反馈截图为空",
            )
        if len(data) > self.MAX_SCREENSHOT_BYTES:
            raise LocalRuntimeError(
                413,
                "feedback_screenshot_too_large",
                "反馈截图不能超过 6 MiB",
            )
        detected_media_type = self._validated_media_type(data, media_type)
        context = self.runtime._current_context(require_ready=True)  # noqa: SLF001
        identity = self._stable_segment(
            f"{context.sandbox_id}:{idempotency_key}"
        )
        object_id = f"feedback-screenshot:{identity}"
        storage_key = (
            "software-feedback/screenshots/"
            f"{self._stable_segment(context.sandbox_id)}/{identity}"
            f"{self._MEDIA_EXTENSIONS[detected_media_type]}"
        )
        target = (self.data_root / storage_key).resolve()
        if self.data_root not in target.parents:
            raise LocalRuntimeError(
                422,
                "feedback_screenshot_path_invalid",
                "反馈截图受管路径越界",
            )
        content_hash = hashlib.sha256(data).hexdigest()
        with self.runtime.local_storage_object_lock(
            sandbox_id=context.sandbox_id,
            object_id=object_id,
        ):
            current = self.runtime.local_storage_object_get(
                sandbox_id=context.sandbox_id,
                object_id=object_id,
            )
            if current is not None:
                if (
                    str(current.get("content_hash") or "") != content_hash
                    or str(current.get("media_type") or "")
                    != detected_media_type
                ):
                    raise LocalRuntimeError(
                        409,
                        "feedback_screenshot_idempotency_conflict",
                        "同一反馈操作不能提交不同截图",
                    )
                if (
                    not target.is_file()
                    or hashlib.sha256(target.read_bytes()).hexdigest()
                    != content_hash
                ):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    repair = target.with_name(
                        f".{target.name}.{new_id()}.repair"
                    )
                    try:
                        repair.write_bytes(data)
                        repair.replace(target)
                    finally:
                        repair.unlink(missing_ok=True)
                return {
                    "objectId": object_id,
                    "contentHash": content_hash,
                    "mediaType": detected_media_type,
                    "byteSize": len(data),
                    "path": str(target),
                    "version": int(current.get("version") or 1),
                    "idempotentReplay": True,
                }
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(f".{target.name}.{new_id()}.tmp")
            try:
                temporary.write_bytes(data)
                temporary.replace(target)
                stored = self.runtime.local_storage_object_put(
                    sandbox_id=context.sandbox_id,
                    object_id=object_id,
                    storage_key=storage_key,
                    content_hash=content_hash,
                    media_type=detected_media_type,
                    byte_size=len(data),
                    expected_version=0,
                )
            except Exception:
                temporary.unlink(missing_ok=True)
                target.unlink(missing_ok=True)
                raise
        return {
            "objectId": object_id,
            "contentHash": content_hash,
            "mediaType": detected_media_type,
            "byteSize": len(data),
            "path": str(target),
            "version": int(stored["version"]),
            "idempotentReplay": False,
        }
