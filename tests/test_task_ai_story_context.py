from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from backend.app.runtime import LocalRuntimeError
from backend.app.ui_domains.workflow import _dispatch_unpinned
from backend.app.ui_domains.routing import UiRequest


class _StoryRuntime:
    def __init__(
        self,
        *,
        parsed_client_name: str | None,
        knowledge: dict[str, Any],
        fail_context: bool = False,
        fail_narrative: bool = False,
    ) -> None:
        self.parsed_client_name = parsed_client_name
        self.knowledge = knowledge
        self.fail_context = fail_context
        self.fail_narrative = fail_narrative
        self.story_project_ids: list[str] = []
        self.completion_calls: list[dict[str, Any]] = []
        self.narrative_calls: list[dict[str, Any]] = []

    @staticmethod
    def _projects() -> list[dict[str, Any]]:
        return [
            {
                "projectId": "project-xingcong",
                "name": "星丛",
                "alias": "新从",
                "lifecycleState": "active",
                "isDefaultInternalProject": True,
            },
            {
                "projectId": "project-rici",
                "name": "日慈基金会",
                "alias": "日慈",
                "lifecycleState": "active",
                "isDefaultInternalProject": False,
            },
        ]

    def cloud_query(self, path: str) -> dict[str, Any]:
        assert path == "/api/v2/domain/project-materials/projects"
        return {"projects": self._projects()}

    def private_ai_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.completion_calls.append(kwargs)
        return {
            "content": json.dumps(
                {
                    "title": "完成 AI 运营功能",
                    "desc": "完成前后台功能并形成可验收结果",
                    "dueDate": None,
                    "dueTime": None,
                    "priority": "high",
                    "clientName": self.parsed_client_name,
                },
                ensure_ascii=False,
            )
        }

    def project_knowledge_context(self, project_id: str) -> dict[str, Any]:
        self.story_project_ids.append(project_id)
        if self.fail_context:
            raise LocalRuntimeError(503, "project_story_unavailable", "项目 Story 暂时不可用")
        return self.knowledge

    def organization_ai_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.narrative_calls.append(kwargs)
        if self.fail_narrative:
            raise LocalRuntimeError(503, "organization_model_unavailable", "组织模型暂时不可用")
        return {
            "content": (
                "星丛正在建设 AI 原生运营能力，这项任务直接服务于该阶段目标。"
                "当前应以可运行的前后台和真实验收证据为交付边界。"
            ),
            "provider": {"modelName": "organization-model-test"},
        }


class _Compatibility:
    def __init__(self, runtime: _StoryRuntime) -> None:
        self.runtime = runtime


def _parse(runtime: _StoryRuntime) -> dict[str, Any]:
    return _dispatch_unpinned(
        _Compatibility(runtime),
        UiRequest(
            method="POST",
            path="tasks/ai-parse",
            query={},
            body={"text": "把 AI 运营功能做好", "currentDate": "2026-08-26"},
            idempotency_key="task-ai-story-context",
        ),
        None,  # type: ignore[arg-type]
    )


def _story(
    *,
    story_id: str = "story-authoritative",
    project_id: str = "project-xingcong",
    title: str = "星丛发展 Story",
    content: str = "当前阶段聚焦 AI 原生运营能力和可验收的交付链路。",
    version: int = 3,
    publication_state: str = "published",
) -> dict[str, Any]:
    return {
        "state": "ready",
        "projectId": project_id,
        "storyId": story_id,
        "title": title,
        "version": version,
        "content": content,
        "contentHash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "sourceSetId": f"sources-{story_id}",
        "publicationState": publication_state,
        "lifecycleState": "active",
        "availabilityState": "ready",
        "knowledgeCutoff": "2026-08-26T00:00:00Z",
        "publishedAt": "2026-08-26T01:00:00Z",
        "generatorVersion": "project-story-simulation-v1",
    }


def _knowledge(
    *shared_items: dict[str, Any],
    story: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "clientId": "project-xingcong",
        "state": "ready",
        "projectStory": story or {"state": "not_available", "projectId": "project-xingcong"},
        "organizationSharedKnowledge": list(shared_items),
        "officialWebsiteFacts": [],
        "savedMemories": [],
        "materialBoundary": {
            "sourceFileContentReturned": False,
            "sourceFilePathReturned": False,
            "localStorageLocatorReturned": False,
        },
    }


def test_task_ai_parse_uses_explicit_project_story_to_explain_description() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(story=_story(story_id="story-1")),
    )

    result = _parse(runtime)

    assert result["clientId"] == "project-xingcong"
    assert runtime.story_project_ids == ["project-xingcong"]
    assert result["desc"].startswith("完成前后台功能并形成可验收结果\n\n任务背景：")
    assert "AI 原生运营能力" in result["desc"]
    assert result["storyContext"] == {
        "state": "applied_authoritative_story",
        "projectId": "project-xingcong",
        "projectName": "星丛",
        "projectSelectionSource": "explicit_match",
        "relationshipMode": "task_specific",
        "usedSignals": ["星丛发展 Story"],
        "materialBoundary": {
            "sourceFileContentReturned": False,
            "sourceFilePathReturned": False,
            "localStorageLocatorReturned": False,
        },
        "generationModel": "deterministic-authority-brief-v2",
        "storyId": "story-1",
        "storyVersion": 3,
        "storyContentHash": hashlib.sha256(
            "当前阶段聚焦 AI 原生运营能力和可验收的交付链路。".encode("utf-8")
        ).hexdigest(),
        "sourceSetId": "sources-story-1",
        "knowledgeCutoff": "2026-08-26T00:00:00Z",
        "message": "已依据唯一、已发布的正式 Story 补充任务背景",
    }
    assert runtime.narrative_calls == []


def test_task_ai_parse_uses_organization_default_story_when_text_has_no_project() -> None:
    runtime = _StoryRuntime(
        parsed_client_name=None,
        knowledge=_knowledge(
            story=_story(
                story_id="story-default",
                title="组织大 Story",
                content="星丛的组织项目承载公司级共同背景。",
                version=1,
            )
        ),
    )

    result = _parse(runtime)

    assert result["clientId"] == "project-xingcong"
    assert result["clientName"] == "星丛"
    assert result["storyContext"]["state"] == "applied_authoritative_story"
    assert result["storyContext"]["projectSelectionSource"] == "organization_default"
    assert runtime.story_project_ids == ["project-xingcong"]


def test_task_ai_parse_title_prompt_preserves_explicit_meeting_intent_in_one_parse_call() -> None:
    runtime = _StoryRuntime(parsed_client_name="星丛", knowledge=_knowledge())

    _dispatch_unpinned(
        _Compatibility(runtime),
        UiRequest(
            method="POST",
            path="tasks/ai-parse",
            query={},
            body={
                "text": (
                    "与樂樂、硕硕开会推进三项重点工作\n"
                    "会议重点内容：完成AI运营后台交付，并规划前台智能搜索。"
                ),
                "currentDate": "2026-08-26",
            },
            idempotency_key="task-ai-title-meeting-contract",
        ),
        None,  # type: ignore[arg-type]
    )

    assert len(runtime.completion_calls) == 1
    system_prompt = runtime.completion_calls[0]["system_prompt"]
    assert "先识别原文的核心任务类型" in system_prompt
    assert "开会、沟通、汇报、评审、拜访" in system_prompt
    assert "必须保留该动作" in system_prompt
    assert "不得把会议议题改写成已经承诺完成的执行任务" in system_prompt
    assert "参与人+会议动作+核心议题" in system_prompt
    assert "通常不超过32个汉字" in system_prompt
    assert "相关工作" in system_prompt
    assert "保留议题中的具体业务对象和阶段或结果" in system_prompt


def test_task_ai_parse_story_enrichment_does_not_add_a_second_model_wait() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(story=_story(story_id="story-fast")),
    )

    result = _parse(runtime)

    assert len(runtime.completion_calls) == 1
    assert runtime.narrative_calls == []
    assert result["storyContext"]["generationModel"] == "deterministic-authority-brief-v2"


def test_task_ai_parse_keeps_original_description_when_project_has_no_story() -> None:
    runtime = _StoryRuntime(parsed_client_name="星丛", knowledge=_knowledge())

    result = _parse(runtime)

    assert result["desc"] == "完成前后台功能并形成可验收结果"
    assert result["storyContext"]["state"] == "not_available"
    assert result["storyContext"]["usedSignals"] == []
    assert result["storyContext"]["message"] == "当前项目暂无唯一、已发布的正式 Story"
    assert runtime.narrative_calls == []


def test_task_ai_parse_excludes_private_favorite_memory_from_shared_task_story() -> None:
    knowledge = _knowledge()
    knowledge["savedMemories"] = [
        {
            "sourceId": "favorite-private",
            "sourceDescription": "本人项目收藏",
            "summary": "这是当前用户的私人收藏，不得写入共享任务说明。",
            "sourceKind": "answer_favorite",
            "availabilityState": "ready",
        },
        {
            "sourceId": "member-private-memory",
            "sourceDescription": "当前成员明确记住",
            "summary": "这是当前成员的私有记忆，不得写入共享任务说明。",
            "sourceKind": "answer_remember",
            "availabilityState": "ready",
        },
    ]
    runtime = _StoryRuntime(parsed_client_name="星丛", knowledge=knowledge)

    result = _parse(runtime)

    assert result["storyContext"]["state"] == "not_available"
    assert "私人收藏" not in result["desc"]
    assert "私有记忆" not in result["desc"]
    assert runtime.narrative_calls == []


def test_task_ai_parse_ignores_unrelated_shared_summary_without_authoritative_story() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(
            {
                "sourceId": "shared-hk-retail",
                "sourceDescription": "港货北上研究",
                "summary": "香港零售商品北上渠道与选品研究。",
                "sourceKind": "shared_summary",
                "availabilityState": "ready",
            }
        ),
    )

    result = _parse(runtime)

    assert result["desc"] == "完成前后台功能并形成可验收结果"
    assert result["storyContext"]["state"] == "not_available"
    assert "港货北上" not in result["desc"]


def test_task_ai_parse_uses_only_authoritative_story_when_unrelated_summaries_exist() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(
            {
                "sourceId": "shared-hk-retail",
                "sourceDescription": "港货北上研究",
                "summary": "香港零售商品北上渠道与选品研究。",
                "sourceKind": "shared_summary",
                "availabilityState": "ready",
            },
            story=_story(story_id="story-only"),
        ),
    )

    result = _parse(runtime)

    assert result["storyContext"]["state"] == "applied_authoritative_story"
    assert result["storyContext"]["storyId"] == "story-only"
    assert "AI 原生运营能力" in result["desc"]
    assert "港货北上" not in result["desc"]


def test_task_ai_parse_rejects_story_for_another_project() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(story=_story(project_id="project-rici")),
    )

    result = _parse(runtime)

    assert result["desc"] == "完成前后台功能并形成可验收结果"
    assert result["storyContext"]["state"] == "project_mismatch"


def test_task_ai_parse_rejects_unpublished_story() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(story=_story(publication_state="draft")),
    )

    result = _parse(runtime)

    assert result["desc"] == "完成前后台功能并形成可验收结果"
    assert result["storyContext"]["state"] == "unpublished"


def test_task_ai_parse_rejects_multiple_published_story_authorities() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(
            story={
                "state": "authority_conflict",
                "projectId": "project-xingcong",
                "candidateCount": 2,
            }
        ),
    )

    result = _parse(runtime)

    assert result["desc"] == "完成前后台功能并形成可验收结果"
    assert result["storyContext"]["state"] == "authority_conflict"
    assert "停止自动引用" in result["storyContext"]["message"]


def test_task_ai_parse_rejects_story_with_a_stale_content_hash() -> None:
    story = _story(story_id="story-stale-hash")
    story["content"] = "港货北上研究被替换进正式 Story。"
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(story=story),
    )

    result = _parse(runtime)

    assert result["desc"] == "完成前后台功能并形成可验收结果"
    assert result["storyContext"]["state"] == "invalid_authority"
    assert "港货北上" not in result["desc"]


def test_task_ai_parse_rejects_future_story_projection() -> None:
    story = _story(story_id="story-future")
    story["knowledgeCutoff"] = "2098-12-31T23:59:59Z"
    story["publishedAt"] = "2099-01-01T00:00:00Z"
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(story=story),
    )

    result = _parse(runtime)

    assert result["desc"] == "完成前后台功能并形成可验收结果"
    assert result["storyContext"]["state"] == "invalid_authority"


def test_task_ai_parse_rejects_ready_story_inside_empty_projection() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge={
            **_knowledge(story=_story(story_id="story-inconsistent")),
            "state": "empty",
        },
    )

    result = _parse(runtime)

    assert result["desc"] == "完成前后台功能并形成可验收结果"
    assert result["storyContext"]["state"] == "invalid_authority"


def test_task_ai_parse_uses_visible_deterministic_story_without_model_roundtrip() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        fail_narrative=True,
        knowledge=_knowledge(story=_story(story_id="story-fallback")),
    )

    result = _parse(runtime)

    assert result["storyContext"]["state"] == "applied_authoritative_story"
    assert result["storyContext"]["generationModel"] == "deterministic-authority-brief-v2"
    assert result["storyContext"]["message"] == "已依据唯一、已发布的正式 Story 补充任务背景"
    assert "当前可确认的背景" in result["desc"]
    assert runtime.narrative_calls == []


def test_task_ai_parse_does_not_silently_ignore_story_highway_failure() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge=_knowledge(),
        fail_context=True,
    )

    with pytest.raises(LocalRuntimeError) as error:
        _parse(runtime)

    assert error.value.code == "project_story_unavailable"


def test_task_ai_parse_does_not_treat_failed_story_state_as_empty() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="星丛",
        knowledge={**_knowledge(), "state": "failed_retryable"},
    )

    with pytest.raises(LocalRuntimeError) as error:
        _parse(runtime)

    assert error.value.code == "task_ai_story_context_unavailable"


def test_task_ai_parse_rejects_missing_top_level_knowledge_state() -> None:
    knowledge = _knowledge(story=_story(story_id="story-without-envelope-state"))
    knowledge.pop("state")
    runtime = _StoryRuntime(parsed_client_name="星丛", knowledge=knowledge)

    with pytest.raises(LocalRuntimeError) as error:
        _parse(runtime)

    assert error.value.code == "task_ai_story_context_unavailable"


def test_task_ai_parse_does_not_default_when_model_names_unknown_project() -> None:
    runtime = _StoryRuntime(
        parsed_client_name="模型编造的项目",
        knowledge=_knowledge(
            story=_story(
                story_id="story-must-not-leak",
                content="这是星丛的项目背景，不应误套到未知项目。",
            )
        ),
    )

    result = _parse(runtime)

    assert result["clientId"] is None
    assert result["storyContext"]["state"] == "project_unresolved"
    assert runtime.story_project_ids == []
    assert "星丛的项目背景" not in result["desc"]
