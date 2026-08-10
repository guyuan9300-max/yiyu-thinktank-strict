"""Strict device-runtime surface for renderer-visible platform operations.

This is a narrow registry adapter, not another implementation.  Every route
delegates to ``platform_integrations``; persistence therefore stays in the
88-table repositories.  Keeping the allow-list here lets the shared registry
mount the useful local executors without re-enabling unrelated compatibility
routes in bulk.
"""

from __future__ import annotations

from .platform_integrations import (
    _requires_pinned_platform_workspace,
    router as platform_router,
)
from .routing import UiDomainRouter


DEVICE_RUNTIME_ROUTES = frozenset(
    {
        ("GET", r"system/health"),
        ("GET", r"system/source-integrity"),
        ("GET", r"system/active-background-tasks"),
        ("GET", r"audio-transcription-jobs/recent"),
        ("GET", r"local-asr/model/status"),
        ("POST", r"local-asr/model/download"),
        ("POST", r"local-asr/model/cancel"),
        ("POST", r"local-asr/transcribe-test"),
        ("GET", r"local-asr/diarization/status"),
        ("POST", r"local-asr/diarization/download"),
        ("GET", r"ollama/health"),
        ("GET", r"ollama/recommended-models"),
        ("POST", r"ollama/pull"),
        ("GET", r"ollama/pull/status"),
        ("POST", r"ollama/pull/cancel"),
        ("POST", r"ollama/delete"),
        ("GET", r"local-ai/health"),
        ("GET", r"local-ai/queue"),
        ("POST", r"local-ai/run-now"),
        ("GET", r"local-ai/settings"),
        ("PUT", r"local-ai/settings"),
        ("GET", r"local-ai/coverage"),
        ("POST", r"local-ai/backfill"),
        ("POST", r"recordings/transcribe-local-audio"),
        ("POST", r"recordings/summarize-meeting-minutes"),
        ("POST", r"runtime/llm-healthcheck"),
        ("POST", r"runtime/llm-provider-probe"),
        ("POST", r"feishu-sync/calendar/tasks/(?P<task_id>[^/]+)"),
        ("POST", r"feishu-sync/documents"),
        ("GET", r"feishu-doc-import/status"),
        ("POST", r"feishu-doc-import/search"),
        ("POST", r"feishu-doc-import/resolve-links"),
        ("GET", r"tool-registry"),
        ("POST", r"ai-command/parse-steps"),
        ("POST", r"local/tasks/tag-suggestions"),
        ("GET", r"runtime/analysis-migration-metrics"),
        ("GET", r"runtime/generation-state"),
        ("POST", r"runtime/generation-state/reset"),
        ("GET", r"runtime/run-log/(?P<run_id>[^/]+)"),
        ("GET", r"runtime/workspace-chat-diagnostics"),
        ("GET", r"runtime/workspace-answer-value-diagnostics"),
    }
)


router = UiDomainRouter(
    "strict_platform_device_runtime",
    pin_workspace=_requires_pinned_platform_workspace,
)

for _route in platform_router.routes:
    if (_route.method, _route.pattern) in DEVICE_RUNTIME_ROUTES:
        router.route(_route.method, _route.pattern)(_route.handler)

if len(router.routes) != len(DEVICE_RUNTIME_ROUTES):
    raise RuntimeError("strict platform device runtime route registry is incomplete")


__all__ = ["DEVICE_RUNTIME_ROUTES", "router"]
