from __future__ import annotations

from typing import Any

from backend.app.ui_domains import build_default_registry
from backend.app.ui_domains.routing import UiRequest


def test_plan_parse_uses_organization_ai_and_returns_structured_draft() -> None:
    calls: list[dict[str, Any]] = []

    class Runtime:
        def organization_ai_completion(self, **kwargs: Any) -> dict[str, Any]:
            calls.append(kwargs)
            return {
                "content": (
                    '{"summary":"本月完成新版链路", "confidence":"high", "items":['
                    '{"title":"接通任务链","statement":"完成任务创建与协作",'
                    '"expectedOutput":"可人工验收的任务闭环"}]}'
                ),
                "provider": {"modelName": "Doubao-Seed-2.1-pro"},
            }

    result = build_default_registry().dispatch(
        type("Compatibility", (), {"runtime": Runtime()})(),
        UiRequest(
            method="POST",
            path="org-model/plans/parse",
            query={},
            body={
                "text": "目标：完成任务创建与协作，产出可人工验收的任务闭环。",
                "organizationName": "益语智库",
                "scopeKind": "department",
                "scopeName": "技术创新部",
                "periodKey": "2026-W32",
                "cycleType": "week",
            },
            idempotency_key="plan-parse-agent-1",
        ),
    )

    assert result["items"] == [
        {
            "title": "接通任务链",
            "statement": "完成任务创建与协作",
            "expectedOutput": "可人工验收的任务闭环",
        }
    ]
    assert result["confidence"] == "high"
    assert result["modelName"] == "Doubao-Seed-2.1-pro"
    assert result["agentRun"]["agentKind"] == "task_planning"
    assert calls[0]["temperature"] == 0.1
    assert calls[0]["read_timeout_seconds"] == 60.0
