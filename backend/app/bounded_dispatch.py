from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Hashable


class DispatchBusyError(RuntimeError):
    pass


class DispatchTimeoutError(RuntimeError):
    pass


class BoundedDispatch:
    """Run synchronous UI compatibility work without blocking the event loop."""

    def __init__(
        self,
        dispatch: Callable[..., Any],
        *,
        max_workers: int = 4,
        max_queued: int = 8,
        deadline_seconds: float = 20.0,
    ):
        self._dispatch = dispatch
        self._deadline_seconds = max(0.1, float(deadline_seconds))
        self._max_workers = max(1, int(max_workers))
        self._max_queued = max(0, int(max_queued))
        self._slots = threading.BoundedSemaphore(
            self._max_workers + self._max_queued
        )
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="yiyu-ui-dispatch",
        )
        self._closed = False
        self._state_lock = threading.Lock()
        self._inflight: dict[Hashable, Any] = {}

    async def run(
        self,
        *args: Any,
        coalesce_key: Hashable | None = None,
        **kwargs: Any,
    ) -> Any:
        with self._state_lock:
            if self._closed:
                raise DispatchBusyError("本地后端正在停止")
            existing = (
                self._inflight.get(coalesce_key)
                if coalesce_key is not None
                else None
            )
            if existing is not None:
                future = existing
                accepted = True
                owns_work = False
            else:
                slots = self._slots
                accepted = slots.acquire(blocking=False)
                owns_work = accepted
                if accepted:
                    future = self._executor.submit(
                        self._dispatch,
                        *args,
                        **kwargs,
                    )
                    if coalesce_key is not None:
                        self._inflight[coalesce_key] = future
        if not accepted:
            raise DispatchBusyError("本地后端繁忙，请稍后重试")

        if owns_work:
            def completed(_: Any) -> None:
                slots.release()
                if coalesce_key is not None:
                    with self._state_lock:
                        if self._inflight.get(coalesce_key) is future:
                            del self._inflight[coalesce_key]

            future.add_done_callback(completed)
        try:
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=self._deadline_seconds,
            )
        except TimeoutError as exc:
            # A running thread cannot be killed safely. It keeps its capacity
            # slot until the synchronous operation actually returns.
            raise DispatchTimeoutError(
                "本地后端等待组织云超时，请稍后重试"
            ) from exc

    def start(self) -> None:
        """Recreate the bounded pool when an app lifespan is started again."""
        with self._state_lock:
            if not self._closed:
                return
            self._slots = threading.BoundedSemaphore(
                self._max_workers + self._max_queued
            )
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers,
                thread_name_prefix="yiyu-ui-dispatch",
            )
            self._inflight = {}
            self._closed = False

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=False)
