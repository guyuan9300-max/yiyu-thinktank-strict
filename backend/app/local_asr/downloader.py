from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .models import MODEL_SPECS, ModelSpec, model_dir, model_ready


@dataclass
class DownloadProgress:
    in_progress: bool = False
    bytes_downloaded: int = 0
    bytes_total: int = 0
    current_file: str = ""
    current_model: str = ""
    pending_models: list[str] = field(default_factory=list)
    completed_models: list[str] = field(default_factory=list)
    error_message: str | None = None
    completed: bool = False
    started_at: float = 0.0
    elapsed_seconds: float = 0.0


class ModelDownloadManager:
    """One cancellable model download per strict data directory."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._lock = threading.Lock()
        self._progress = DownloadProgress()
        self._cancel = threading.Event()

    def status(self) -> DownloadProgress:
        with self._lock:
            value = self._progress
            return DownloadProgress(
                in_progress=value.in_progress,
                bytes_downloaded=value.bytes_downloaded,
                bytes_total=value.bytes_total,
                current_file=value.current_file,
                current_model=value.current_model,
                pending_models=list(value.pending_models),
                completed_models=list(value.completed_models),
                error_message=value.error_message,
                completed=value.completed,
                started_at=value.started_at,
                elapsed_seconds=(
                    time.time() - value.started_at
                    if value.in_progress and value.started_at
                    else value.elapsed_seconds
                ),
            )

    def start(
        self,
        model_names: str | list[str],
        *,
        prefer_mirror: bool,
    ) -> tuple[bool, str]:
        names = [model_names] if isinstance(model_names, str) else list(model_names)
        if not names:
            return False, "未指定要下载的模型"
        specs: list[ModelSpec] = []
        for name in names:
            spec = MODEL_SPECS.get(name)
            if spec is None:
                return False, f"未注册的模型：{name}"
            specs.append(spec)
        pending = [spec for spec in specs if not model_ready(self.root, spec.name)]
        completed = [spec.name for spec in specs if model_ready(self.root, spec.name)]
        if not pending:
            return False, "模型已就绪"
        with self._lock:
            if self._progress.in_progress:
                return False, "已有下载任务在进行中"
            self._cancel.clear()
            self._progress = DownloadProgress(
                in_progress=True,
                pending_models=[spec.name for spec in pending],
                completed_models=completed,
                started_at=time.time(),
            )
        thread = threading.Thread(
            target=self._download_batch,
            args=(pending, prefer_mirror),
            name=f"strict-asr-download-{len(pending)}",
            daemon=True,
        )
        thread.start()
        return True, f"已开始下载 {len(pending)} 个模型"

    def cancel(self) -> bool:
        with self._lock:
            if not self._progress.in_progress:
                return False
            self._cancel.set()
            return True

    def _fail(self, message: str) -> None:
        with self._lock:
            self._progress.in_progress = False
            self._progress.completed = False
            self._progress.error_message = message
            if self._progress.started_at:
                self._progress.elapsed_seconds = time.time() - self._progress.started_at

    def _finish(self) -> None:
        with self._lock:
            self._progress.in_progress = False
            self._progress.completed = True
            self._progress.current_model = ""
            self._progress.current_file = ""
            self._progress.pending_models = []
            self._progress.elapsed_seconds = time.time() - self._progress.started_at

    def _download_batch(self, specs: list[ModelSpec], prefer_mirror: bool) -> None:
        try:
            for spec in specs:
                if self._cancel.is_set():
                    self._fail("用户已取消下载")
                    return
                with self._lock:
                    self._progress.current_model = spec.name
                    if spec.name in self._progress.pending_models:
                        self._progress.pending_models.remove(spec.name)
                if not self._download_model(spec, prefer_mirror):
                    return
                with self._lock:
                    self._progress.completed_models.append(spec.name)
            self._finish()
        except Exception as exc:  # noqa: BLE001
            self._fail(f"下载器内部错误：{exc.__class__.__name__}: {exc}")

    def _download_model(self, spec: ModelSpec, prefer_mirror: bool) -> bool:
        selected: list[tuple[str, str, int]] = []
        for item in spec.files:
            candidates = (
                (item.mirror_url, item.primary_url)
                if prefer_mirror
                else (item.primary_url, item.mirror_url)
            )
            choice: tuple[str, int] | None = None
            last_error = ""
            for url in dict.fromkeys(candidates):
                try:
                    with httpx.Client(follow_redirects=True, timeout=20.0) as client:
                        response = client.head(url)
                        response.raise_for_status()
                        size = int(response.headers.get("content-length") or 0)
                    choice = (url, size)
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
            if choice is None:
                self._fail(f"{spec.name}/{item.name} 没有可用下载源：{last_error}")
                return False
            selected.append((item.name, choice[0], choice[1]))
        with self._lock:
            self._progress.bytes_total = sum(item[2] for item in selected)
            self._progress.bytes_downloaded = 0

        directory = model_dir(self.root, spec.name)
        directory.mkdir(parents=True, exist_ok=True)
        for file_name, url, _ in selected:
            if self._cancel.is_set():
                self._fail("用户已取消下载")
                return False
            with self._lock:
                self._progress.current_file = file_name
            target = directory / file_name
            temporary = directory / f"{file_name}.part"
            try:
                with httpx.Client(
                    follow_redirects=True,
                    timeout=httpx.Timeout(30.0, read=120.0),
                ) as client:
                    with client.stream("GET", url) as response:
                        response.raise_for_status()
                        with temporary.open("wb") as output:
                            for chunk in response.iter_bytes(256 * 1024):
                                if self._cancel.is_set():
                                    temporary.unlink(missing_ok=True)
                                    self._fail("用户已取消下载")
                                    return False
                                output.write(chunk)
                                with self._lock:
                                    self._progress.bytes_downloaded += len(chunk)
                temporary.replace(target)
            except Exception as exc:  # noqa: BLE001
                temporary.unlink(missing_ok=True)
                self._fail(f"下载 {spec.name}/{file_name} 失败：{exc}")
                return False
        if not model_ready(self.root, spec.name):
            self._fail(f"下载完成但模型 {spec.name} 不完整")
            return False
        return True


_MANAGERS: dict[str, ModelDownloadManager] = {}
_MANAGERS_LOCK = threading.Lock()


def get_download_manager(root: Path) -> ModelDownloadManager:
    key = str(root.resolve())
    with _MANAGERS_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager = ModelDownloadManager(root)
            _MANAGERS[key] = manager
        return manager
