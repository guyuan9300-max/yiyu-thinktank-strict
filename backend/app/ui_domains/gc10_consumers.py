"""GC-10 compatibility surface for the retired consultation queue poll.

Project knowledge corrections and explicit memories are now propagated by the
formal GC-10/GC-12 command transaction.  The old renderer still performs a
minute-level ``process-pending`` poll.  Returning a bounded, explicit terminal
summary prevents that obsolete poll from falling through to a 501 or creating
a second knowledge queue.
"""

from __future__ import annotations

from typing import Any

from strict_common.ids import utc_now

from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("gc10_consumer_compat", pin_workspace=True)


@router.post(r"consultation/knowledge-requests/process-pending")
def process_retired_consultation_queue(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    # Still require a ready authenticated workspace.  This is not an anonymous
    # fake-success route; it only acknowledges that the removed legacy queue
    # has no work because current knowledge writes propagate transactionally.
    compatibility.runtime._current_context(require_ready=True)  # noqa: SLF001
    return {
        "totalPending": 0,
        "processedCount": 0,
        "completedCount": 0,
        "failedCount": 0,
        "skippedCount": 0,
        "updatedAt": utc_now(),
        "items": [],
        "state": "ready",
        "retryable": False,
        "pollingEnabled": False,
        "message": (
            "旧版咨询知识批处理队列已停用；当前项目知识由正式写入命令"
            "直接触发消费者传播"
        ),
    }
