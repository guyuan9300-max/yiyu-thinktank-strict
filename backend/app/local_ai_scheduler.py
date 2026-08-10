from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from strict_common.ids import new_id

from .runtime import LocalRuntimeError


logger = logging.getLogger(__name__)


class LocalAiScheduler:
    """Periodically let the existing local-AI governor run one queued item."""

    def __init__(
        self,
        dispatch: Callable[..., Any],
        *,
        interval_seconds: float = 60.0,
    ):
        self._dispatch = dispatch
        self._interval_seconds = max(1.0, float(interval_seconds))

    async def run_cycle(self) -> dict[str, Any] | None:
        try:
            settings = await asyncio.to_thread(
                self._dispatch,
                "GET",
                "local-ai/settings",
                query={},
                body={},
                idempotency_key=f"local-ai-scheduler-settings:{new_id()}",
            )
            if not isinstance(settings, dict) or not (
                settings.get("enabled") or settings.get("manualActive")
            ):
                return {
                    "status": "disabled",
                    "processed": 0,
                    "failed": 0,
                    "skipped": 1,
                }
            await asyncio.to_thread(
                self._dispatch,
                "POST",
                "local-ai/backfill",
                query={},
                body={},
                idempotency_key=f"local-ai-scheduler-backfill:{new_id()}",
            )
            result = await asyncio.to_thread(
                self._dispatch,
                "POST",
                "local-ai/run-now",
                query={"force": "false"},
                body={},
                idempotency_key=f"local-ai-scheduler:{new_id()}",
            )
        except LocalRuntimeError as exc:
            if exc.code not in {
                "needs_login",
                "organization_required",
                "workspace_missing",
            }:
                logger.warning(
                    "local_ai_scheduler_cycle_failed",
                    extra={"error_code": exc.code},
                )
            return None
        except Exception:
            logger.exception("local_ai_scheduler_cycle_failed")
            return None
        return dict(result) if isinstance(result, dict) else None

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self.run_cycle()
