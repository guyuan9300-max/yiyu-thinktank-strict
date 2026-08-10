from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.runtime import LocalRuntimeError
from backend.app.ui_domains import workflow as local_workflow
from backend.app.ui_domains.workflow import (
    _dispatch_unpinned,
    _legacy_report_run,
    _recording_upload_payload,
    _task_context_brief,
    _task_context_with_local_knowledge,
    router,
)
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from strict_common.schema import runtime_connection


def _client(tmp_path: Path) -> tuple[TestClient, Path]:
    database = tmp_path / "strict-workflow-cloud.db"
    config = CloudConfig(
        data_dir=tmp_path,
        database_path=database,
        bootstrap_token="workflow-bootstrap",
        master_key=Fernet.generate_key().decode(),
        cloud_instance_id=None,
    )
    return TestClient(create_app(config)), database


def _bootstrap(client: TestClient) -> tuple[dict, dict[str, str]]:
    response = client.post(
        "/api/v2/auth/bootstrap-organization",
        json={
            "organizationName": "Workflow 严格测试组织",
            "displayName": "管理员",
            "email": "workflow-admin@example.com",
            "password": "12345678",
            "bootstrapToken": "workflow-bootstrap",
        },
    )
    assert response.status_code == 201, response.text
    session = response.json()
    return session, {"Authorization": f"Bearer {session['accessToken']}"}


def _project_id(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/api/v2/business/snapshot", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["projects"][0]["projectId"]


def test_weekly_writes_preserve_explicit_expected_version() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.commands: list[dict[str, Any]] = []

        def cloud_query(
            self,
            path: str,
            *,
            query: Mapping[str, str] | None = None,
        ) -> dict[str, Any]:
            if path.endswith("/agent-weekly-plans"):
                return {"weeklyPlans": [{"version": 11}]}
            if path.endswith("/reviews"):
                return {
                    "reviews": [
                        {
                            "weeklyReviewId": "review-1",
                            "weekLabel": "2026-W31",
                            "version": 13,
                        }
                    ]
                }
            raise AssertionError((path, query))

        def cloud_command(
            self,
            method: str,
            path: str,
            *,
            payload: Mapping[str, Any],
            idempotency_key: str,
        ) -> dict[str, Any]:
            self.commands.append(dict(payload))
            if path.endswith("/agent-weekly-plans/2026-W31/research"):
                return {"weeklyPlan": dict(payload)}
            return {}

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

        @staticmethod
        def _snapshot() -> dict[str, Any]:
            return {"plans": []}

    compatibility = Compatibility()
    _dispatch_unpinned(
        compatibility,
        UiRequest(
            method="PUT",
            path="tasks/agent-weekly-plans/2026-W31/research",
            query={},
            body={"summary": "并发基线", "expectedVersion": 7},
            idempotency_key="agent-plan-cas",
        ),
        None,  # type: ignore[arg-type]
    )
    assert compatibility.runtime.commands[-1]["expectedVersion"] == 7

    _dispatch_unpinned(
        compatibility,
        UiRequest(
            method="POST",
            path="reviews/weekly/draft",
            query={},
            body={"weekLabel": "2026-W31", "expectedVersion": 5},
            idempotency_key="weekly-review-cas",
        ),
        None,  # type: ignore[arg-type]
    )
    assert compatibility.runtime.commands[-1]["expectedVersion"] == 5


def test_completed_task_toggle_maps_renderer_doing_to_strict_restore() -> None:
    class Runtime:
        def __init__(self) -> None:
            self.commands: list[tuple[str, str, dict[str, Any]]] = []

        def cloud_command(
            self,
            method: str,
            path: str,
            *,
            payload: Mapping[str, Any],
            idempotency_key: str,
        ) -> dict[str, Any]:
            del idempotency_key
            self.commands.append((method, path, dict(payload)))
            return {
                "task": {
                    "taskId": "task-restore",
                    "title": "恢复任务",
                    "lifecycleState": "todo",
                    "version": 8,
                }
            }

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

        @staticmethod
        def _snapshot() -> dict[str, Any]:
            return {}

        @staticmethod
        def _task(item: Mapping[str, Any], _: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "id": item["taskId"],
                "title": item["title"],
                "status": "todo",
            }

    compatibility = Compatibility()
    restored = _dispatch_unpinned(
        compatibility,
        UiRequest(
            method="PATCH",
            path="tasks/task-restore",
            query={},
            body={
                "status": "doing",
                "progressStatus": "doing",
                "expectedVersion": 7,
            },
            idempotency_key="task-restore-from-renderer",
        ),
        None,  # type: ignore[arg-type]
    )

    assert restored["status"] == "todo"
    assert compatibility.runtime.commands == [
        (
            "POST",
            "/api/v2/tasks/task-restore/restore",
            {"expectedVersion": 7, "completionNote": ""},
        )
    ]


def test_task_ai_parse_uses_runtime_and_exact_visible_project_match() -> None:
    class Runtime:
        completion_calls: list[dict[str, Any]] = []

        def private_ai_completion(self, **kwargs: Any) -> dict[str, Any]:
            self.completion_calls.append(kwargs)
            return {
                "content": json.dumps(
                    {
                        "title": "准备日慈项目复盘",
                        "desc": "整理项目背景并形成复盘提纲",
                        "dueDate": "2026-08-03",
                        "dueTime": "14:30",
                        "priority": "high",
                        "clientName": "日慈",
                    },
                    ensure_ascii=False,
                )
            }

    class Compatibility:
        runtime = Runtime()

        @staticmethod
        def _snapshot() -> dict[str, Any]:
            return {
                "projects": [
                    {
                        "projectId": "project-rci",
                        "name": "日慈基金会",
                        "alias": "日慈",
                        "lifecycleState": "active",
                    },
                    {
                        "projectId": "project-archived",
                        "name": "历史项目",
                        "alias": "",
                        "lifecycleState": "archived",
                    },
                ]
            }

    result = _dispatch_unpinned(
        Compatibility(),
        UiRequest(
            method="POST",
            path="tasks/ai-parse",
            query={},
            body={"text": "下周一下午两点半准备日慈复盘", "currentDate": "2026-07-31"},
            idempotency_key="task-ai-parse-a",
        ),
        None,  # type: ignore[arg-type]
    )

    assert result == {
        "title": "准备日慈项目复盘",
        "desc": "整理项目背景并形成复盘提纲",
        "dueDate": "2026-08-03",
        "dueTime": "14:30",
        "priority": "high",
        "clientId": "project-rci",
        "clientName": "日慈基金会",
        "clientCandidates": [
            {"id": "project-rci", "name": "日慈基金会", "score": 1.0}
        ],
        "rawLlmGuessClientName": "日慈",
    }
    call = Compatibility.runtime.completion_calls[-1]
    assert call["capability"] == "fast_structured"
    assert call["creativity_mode"] == "strict"
    prompt = json.loads(call["prompt"])
    assert prompt["currentDate"] == "2026-07-31"
    assert prompt["availableProjects"] == [{"name": "日慈基金会", "alias": "日慈"}]


def test_task_ai_parse_does_not_turn_model_project_guess_into_authority() -> None:
    class Runtime:
        @staticmethod
        def private_ai_completion(**_: Any) -> dict[str, Any]:
            return {
                "content": json.dumps(
                    {
                        "title": "准备复盘",
                        "desc": "",
                        "dueDate": None,
                        "dueTime": None,
                        "priority": "normal",
                        "clientName": "模型编造的项目",
                    },
                    ensure_ascii=False,
                )
            }

    class Compatibility:
        runtime = Runtime()

        @staticmethod
        def _snapshot() -> dict[str, Any]:
            return {
                "projects": [
                    {
                        "projectId": "project-rci",
                        "name": "日慈基金会",
                        "alias": "日慈",
                        "lifecycleState": "active",
                    }
                ]
            }

    result = _dispatch_unpinned(
        Compatibility(),
        UiRequest(
            method="POST",
            path="tasks/ai-parse",
            query={},
            body={"text": "准备复盘", "currentDate": "2026-07-31"},
            idempotency_key="task-ai-parse-b",
        ),
        None,  # type: ignore[arg-type]
    )

    assert result["clientId"] is None
    assert result["clientName"] is None
    assert result["clientCandidates"] == []
    assert result["rawLlmGuessClientName"] == "模型编造的项目"
    assert result["desc"] == "准备复盘"


def test_task_ai_parse_rejects_malformed_model_result_instead_of_faking_success() -> None:
    class Runtime:
        @staticmethod
        def private_ai_completion(**_: Any) -> dict[str, Any]:
            return {"content": "这是一段无法解析的说明"}

    class Compatibility:
        runtime = Runtime()

        @staticmethod
        def _snapshot() -> dict[str, Any]:
            return {"projects": []}

    with pytest.raises(LocalRuntimeError) as error:
        _dispatch_unpinned(
            Compatibility(),
            UiRequest(
                method="POST",
                path="tasks/ai-parse",
                query={},
                body={"text": "准备复盘", "currentDate": "2026-07-31"},
                idempotency_key="task-ai-parse-c",
            ),
            None,  # type: ignore[arg-type]
        )
    assert error.value.status_code == 502
    assert error.value.code == "task_ai_parse_response_invalid"


def test_task_attachment_retry_runs_device_asr_and_commits_cloud_transcript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = b"fake-audio-for-local-asr"

    class Runtime:
        database_path = tmp_path / "strict-local.db"

        def __init__(self) -> None:
            self.commands: list[tuple[str, str, dict[str, Any]]] = []

        def cloud_query(
            self,
            path: str,
            *,
            query: Mapping[str, str] | None = None,
        ) -> dict[str, Any]:
            del query
            if path.endswith("/attachments/attachment-1/content"):
                return {
                    "fileName": "recording.m4a",
                    "mediaType": "audio/mp4",
                    "byteSize": len(raw),
                    "contentHash": hashlib.sha256(raw).hexdigest(),
                    "contentBase64": base64.b64encode(raw).decode(),
                    "version": 1,
                }
            if path.endswith("/tasks/task-1"):
                return {
                    "task": {
                        "taskId": "task-1",
                        "title": "录音任务",
                        "version": 1,
                        "attachments": [
                            {
                                "id": "attachment-1",
                                "version": 1,
                                "processingStatus": (
                                    "ready" if self.commands else "not_requested"
                                ),
                            }
                        ],
                    }
                }
            raise AssertionError(path)

        def cloud_command(
            self,
            method: str,
            path: str,
            *,
            payload: Mapping[str, Any],
            idempotency_key: str,
        ) -> dict[str, Any]:
            del idempotency_key
            self.commands.append((method, path, dict(payload)))
            return {"status": "completed", "state": "ready"}

    class Compatibility:
        def __init__(self) -> None:
            self.runtime = Runtime()

        @staticmethod
        def _snapshot() -> dict[str, Any]:
            return {"tasks": []}

        @staticmethod
        def _task(item: Mapping[str, Any], _: Mapping[str, Any]) -> dict[str, Any]:
            return dict(item)

    outcome = SimpleNamespace(
        dialogue_text="说话人A：严格转写正文",
        result=SimpleNamespace(
            text="严格转写正文",
            model_name="sense-voice-test",
            language="zh",
            segments=[SimpleNamespace(text="严格转写正文")],
        ),
    )
    monkeypatch.setattr(local_workflow, "model_ready", lambda *_: True)
    monkeypatch.setattr(
        local_workflow,
        "run_recording_transcription",
        lambda *_args, **_kwargs: outcome,
    )
    compatibility = Compatibility()
    result = _dispatch_unpinned(
        compatibility,
        UiRequest(
            method="POST",
            path="tasks/task-1/attachments/attachment-1/retry-transcription",
            query={},
            body={"language": "zh"},
            idempotency_key="local-asr-task-1",
        ),
        None,  # type: ignore[arg-type]
    )
    assert result["attachments"][0]["processingStatus"] == "ready"
    assert len(compatibility.runtime.commands) == 1
    method, path, payload = compatibility.runtime.commands[0]
    assert method == "POST"
    assert path.endswith(
        "/tasks/task-1/attachments/attachment-1/transcription-complete"
    )
    assert payload["text"] == "说话人A：严格转写正文"
    assert payload["expectedVersion"] == 1
    temporary_root = tmp_path / "recordings" / "workflow-transcription"
    assert not list(temporary_root.iterdir())


def _seed_project_knowledge(
    database: Path,
    *,
    organization_id: str,
    membership_id: str,
    project_id: str,
    prefix: str,
) -> dict[str, str]:
    now = "2026-07-30T08:00:00Z"
    values = {
        "shared": f"{prefix} 组织共享的已发布项目摘要。",
        "narrative": f"{prefix} 已保存的组织共享项目叙事。",
        "smart": f"{prefix} 已接受智能导入的受限结构化摘要。",
        "rawBody": f"{prefix}_RAW_SHARED_BODY_MUST_NOT_ENTER",
        "rawPreview": f"{prefix}_UNPUBLISHED_PREVIEW_MUST_NOT_ENTER",
        "candidate": f"{prefix}_CANDIDATE_SUMMARY_MUST_NOT_ENTER",
        "payload": f"{prefix}_SOURCE_PAYLOAD_MUST_NOT_ENTER",
        "narrativeJson": f"{prefix}_NARRATIVE_JSON_MUST_NOT_ENTER",
    }
    with runtime_connection(database, "cloud") as connection:
        connection.execute(
            """
            INSERT INTO knowledge_documents (
              document_id, organization_id, project_id,
              project_assignment_state, source_asset_id,
              owner_membership_id, department_id, title, document_kind,
              visibility_scope, parse_state, lifecycle_state,
              current_version, version, created_at, updated_at
            ) VALUES (?, ?, ?, 'assigned', NULL, ?, NULL, ?,
                      'shared_summary', 'organization', 'ready', 'active',
                      1, 1, ?, ?)
            """,
            (
                f"{prefix}_shared_doc",
                organization_id,
                project_id,
                membership_id,
                f"{prefix} 已发布摘要",
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO document_versions (
              document_version_id, organization_id, document_id, version,
              content_hash, preview_text, markdown_content, section_count,
              chunk_count, generator_version, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, 1, 1, 'workflow-test', ?)
            """,
            (
                f"{prefix}_shared_version",
                organization_id,
                f"{prefix}_shared_doc",
                f"{prefix}-shared-hash",
                values["shared"],
                values["rawBody"],
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO knowledge_documents (
              document_id, organization_id, project_id,
              project_assignment_state, source_asset_id,
              owner_membership_id, department_id, title, document_kind,
              visibility_scope, parse_state, lifecycle_state,
              current_version, version, created_at, updated_at
            ) VALUES (?, ?, ?, 'assigned', NULL, ?, NULL, ?, 'raw_source',
                      'organization', 'ready', 'active', 1, 1, ?, ?)
            """,
            (
                f"{prefix}_raw_doc",
                organization_id,
                project_id,
                membership_id,
                f"{prefix} 未发布原始资料",
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO document_versions (
              document_version_id, organization_id, document_id, version,
              content_hash, preview_text, markdown_content, section_count,
              chunk_count, generator_version, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, 1, 1, 'workflow-test', ?)
            """,
            (
                f"{prefix}_raw_version",
                organization_id,
                f"{prefix}_raw_doc",
                f"{prefix}-raw-hash",
                values["rawPreview"],
                values["rawBody"],
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO narrative_outputs (
              narrative_output_id, organization_id, project_id,
              event_line_id, output_kind, title, lifecycle_state,
              latest_version, created_by_membership_id, version,
              created_at, updated_at, archived_at
            ) VALUES (?, ?, ?, NULL, 'strategy_report', ?, 'active',
                      1, ?, 1, ?, ?, NULL)
            """,
            (
                f"{prefix}_narrative",
                organization_id,
                project_id,
                f"{prefix} 项目叙事",
                membership_id,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO narrative_output_versions (
              narrative_output_version_id, organization_id,
              narrative_output_id, version, content_markdown, content_json,
              input_fingerprint, content_hash, change_summary,
              created_by_membership_id, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, '', ?, '', ?, ?)
            """,
            (
                f"{prefix}_narrative_version",
                organization_id,
                f"{prefix}_narrative",
                values["narrative"],
                json.dumps({"raw": values["narrativeJson"]}),
                f"{prefix}-narrative-hash",
                membership_id,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO intelligence_records (
              intelligence_id, organization_id, project_id, title, summary,
              source_url, record_kind, status, visibility_scope,
              created_by_membership_id, source_payload_json, version,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '', 'smart_import_reviewed', 'accepted',
                      'organization', ?, ?, 1, ?, ?)
            """,
            (
                f"{prefix}_smart_accepted",
                organization_id,
                project_id,
                f"{prefix} 智能导入",
                values["smart"],
                membership_id,
                json.dumps({"raw": values["payload"]}),
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO intelligence_records (
              intelligence_id, organization_id, project_id, title, summary,
              source_url, record_kind, status, visibility_scope,
              created_by_membership_id, source_payload_json, version,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '', 'smart_import_reviewed', 'candidate',
                      'organization', ?, '{}', 1, ?, ?)
            """,
            (
                f"{prefix}_smart_candidate",
                organization_id,
                project_id,
                f"{prefix} 未接受智能导入",
                values["candidate"],
                membership_id,
                now,
                now,
            ),
        )
        connection.commit()
    return values


def test_workflow_route_denominator_includes_page_contexts() -> None:
    assert len(router.routes) == 84
    samples = {(route.method, route.pattern) for route in router.routes}
    assert ("GET", r"tasks/([^/]+)/page-context") in samples
    assert ("GET", r"meetings/([^/]+)/page-context") in samples


def test_task_classification_event_link_and_context_are_authoritative(
    tmp_path: Path,
) -> None:
    client, database = _client(tmp_path)
    with client:
        session, headers = _bootstrap(client)
        project_id = _project_id(client, headers)

        task_list = client.post(
            "/api/v2/workflow/lists",
            headers={**headers, "Idempotency-Key": "list-create"},
            json={
                "name": "项目推进",
                "color": "#3366FF",
                "scope": "org",
                "sortOrder": 2,
            },
        )
        assert task_list.status_code == 200, task_list.text
        task_list_id = task_list.json()["list"]["taskListId"]

        repeated_list = client.post(
            "/api/v2/workflow/lists",
            headers={**headers, "Idempotency-Key": "list-create"},
            json={
                "name": "项目推进",
                "color": "#3366FF",
                "scope": "org",
                "sortOrder": 2,
            },
        )
        assert repeated_list.status_code == 200
        assert repeated_list.json() == task_list.json()

        idempotency_conflict = client.post(
            "/api/v2/workflow/lists",
            headers={**headers, "Idempotency-Key": "list-create"},
            json={"name": "不同载荷", "scope": "org"},
        )
        assert idempotency_conflict.status_code == 409
        assert idempotency_conflict.json()["error"]["code"] == "idempotency_conflict"

        tag = client.post(
            "/api/v2/workflow/tags",
            headers={**headers, "Idempotency-Key": "tag-create"},
            json={"name": "重点", "color": "#EE5533", "scope": "self"},
        )
        assert tag.status_code == 200, tag.text
        tag_id = tag.json()["tag"]["taskTagId"]

        event_line = client.post(
            "/api/v2/event-lines",
            headers={**headers, "Idempotency-Key": "event-create"},
            json={
                "projectId": project_id,
                "name": "年度伙伴推进",
                "goal": "完成关键里程碑",
                "background": "严格新版真实背景",
            },
        )
        assert event_line.status_code == 201, event_line.text
        event_line_id = event_line.json()["eventLine"]["eventLineId"]

        task = client.post(
            "/api/v2/tasks",
            headers={**headers, "Idempotency-Key": "task-create"},
            json={
                "title": "准备伙伴沟通材料",
                "description": "围绕年度目标准备材料",
                "projectId": project_id,
                "dueDate": "2026-08-07",
            },
        )
        assert task.status_code == 201, task.text
        task_id = task.json()["task"]["taskId"]

        classified = client.patch(
            f"/api/v2/workflow/tasks/{task_id}/classification",
            headers={**headers, "Idempotency-Key": "task-classify"},
            json={
                "expectedVersion": task.json()["task"]["version"],
                "taskListIds": [task_list_id],
                "taskTagIds": [tag_id],
            },
        )
        assert classified.status_code == 200, classified.text
        classified_task = classified.json()["task"]
        assert classified_task["listMemberships"] == [
            {"taskListId": task_list_id, "orderIndex": 0}
        ]
        assert [item["taskTagId"] for item in classified_task["tags"]] == [tag_id]

        linked = client.post(
            f"/api/v2/workflow/event-lines/{event_line_id}/tasks/{task_id}",
            headers={**headers, "Idempotency-Key": "event-task-link"},
            json={"expectedVersion": 1, "allowReassign": False},
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["task"]["eventLineId"] == event_line_id

        milestone = client.patch(
            f"/api/v2/workflow/event-lines/{event_line_id}/tasks/{task_id}",
            headers={**headers, "Idempotency-Key": "event-task-milestone"},
            json={"expectedVersion": 2, "isMilestone": True},
        )
        assert milestone.status_code == 200, milestone.text
        assert milestone.json()["task"]["eventLineMilestone"] is True

        board = client.get("/api/v2/workflow/board", headers=headers)
        assert board.status_code == 200, board.text
        board_task = next(
            item for item in board.json()["tasks"] if item["taskId"] == task_id
        )
        assert board_task["eventLineId"] == event_line_id
        assert board_task["listMemberships"][0]["taskListId"] == task_list_id

        context = client.get(
            f"/api/v2/workflow/tasks/{task_id}/context",
            headers=headers,
        )
        assert context.status_code == 200, context.text
        assert context.json()["task"]["taskId"] == task_id
        assert context.json()["project"]["project_id"] == project_id
        assert context.json()["eventLine"]["event_line_id"] == event_line_id
        assert "年度伙伴推进" in context.json()["brief"]
        assert context.json()["projectKnowledge"]["state"] == "empty"
        assert context.json()["projectKnowledge"]["items"] == []
        assert context.json()["summaryExcerpts"] == []

        with runtime_connection(database, "cloud", read_only=True) as connection:
            command_count = connection.execute(
                """
                SELECT COUNT(*) FROM command_envelopes
                WHERE organization_id = ?
                  AND command_type IN (
                    'task_list.create',
                    'task_tag.create',
                    'task.classification_updated',
                    'event_line.task_linked',
                    'event_line.task_milestone_changed'
                  )
                """,
                (session["organizationId"],),
            ).fetchone()[0]
            outbox_count = connection.execute(
                """
                SELECT COUNT(*) FROM delivery_outbox
                WHERE organization_id = ?
                  AND event_type IN (
                    'task_list.create',
                    'task_tag.create',
                    'task.classification_updated',
                    'event_line.task_linked',
                    'event_line.task_milestone_changed'
                  )
                """,
                (session["organizationId"],),
            ).fetchone()[0]
            audit_count = connection.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE organization_id = ?
                  AND action IN (
                    'task_list.create',
                    'task_tag.create',
                    'task.classification_updated',
                    'event_line.task_linked',
                    'event_line.task_milestone_changed'
                  )
                """,
                (session["organizationId"],),
            ).fetchone()[0]
        assert command_count == 5
        assert outbox_count == 5
        assert audit_count == 5


def test_workflow_cas_review_plan_link_and_meeting_context(tmp_path: Path) -> None:
    client, database = _client(tmp_path)
    with client:
        session, headers = _bootstrap(client)
        project_id = _project_id(client, headers)
        organization_id = session["organizationId"]
        membership_id = session["membershipId"]
        now = "2026-07-30T08:00:00Z"

        task = client.post(
            "/api/v2/tasks",
            headers={**headers, "Idempotency-Key": "review-task-create"},
            json={
                "title": "形成周复盘材料",
                "description": "由会议行动进入任务与复盘",
                "projectId": project_id,
            },
        )
        assert task.status_code == 201, task.text
        task_id = task.json()["task"]["taskId"]

        task_list = client.post(
            "/api/v2/workflow/lists",
            headers={**headers, "Idempotency-Key": "cas-list-create"},
            json={"name": "CAS 清单", "scope": "org"},
        )
        assert task_list.status_code == 200, task_list.text
        list_id = task_list.json()["list"]["taskListId"]
        updated = client.patch(
            f"/api/v2/workflow/lists/{list_id}",
            headers={**headers, "Idempotency-Key": "cas-list-update"},
            json={"expectedVersion": 1, "name": "CAS 清单已更新"},
        )
        assert updated.status_code == 200, updated.text
        stale = client.patch(
            f"/api/v2/workflow/lists/{list_id}",
            headers={**headers, "Idempotency-Key": "cas-list-stale"},
            json={"expectedVersion": 1, "name": "不应覆盖"},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "task_list_version_conflict"

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                UPDATE task_records
                SET source_type = 'meeting', source_id = ?
                WHERE organization_id = ? AND task_id = ?
                """,
                ("meeting-strict-1", organization_id, task_id),
            )
            plan_id = "plan-strict-1"
            plan_item_id = "plan-item-strict-1"
            connection.execute(
                """
                INSERT INTO organization_plans (
                    plan_id, organization_id, department_id, period_label,
                    owner_membership_id, summary, status, attributes_json,
                    version, created_at, updated_at
                ) VALUES (?, ?, NULL, '2026-W31', ?, '本周计划', 'active', '{}',
                          1, ?, ?)
                """,
                (plan_id, organization_id, membership_id, now, now),
            )
            connection.execute(
                """
                INSERT INTO organization_plan_items (
                    plan_item_id, organization_id, plan_id, title, statement,
                    owner_membership_id, expected_output, status, sort_order,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, '完成伙伴材料', '形成可沟通版本', ?,
                          '沟通材料', 'active', 0, 1, ?, ?)
                """,
                (
                    plan_item_id,
                    organization_id,
                    plan_id,
                    membership_id,
                    now,
                    now,
                ),
            )
            connection.commit()

        plan_link = client.patch(
            f"/api/v2/workflow/tasks/{task_id}/plan-link",
            headers={**headers, "Idempotency-Key": "task-plan-link"},
            json={
                "expectedVersion": 1,
                "departmentPlanItemId": plan_item_id,
            },
        )
        assert plan_link.status_code == 200, plan_link.text
        assert plan_link.json()["planLink"]["departmentPlanItemId"] == plan_item_id

        meeting_context = client.get(
            "/api/v2/workflow/meetings/meeting-strict-1/context",
            headers=headers,
        )
        assert meeting_context.status_code == 200, meeting_context.text
        assert meeting_context.json()["meetingId"] == "meeting-strict-1"
        assert meeting_context.json()["tasks"][0]["taskId"] == task_id

        submitted = client.post(
            "/api/v2/workflow/reviews/weekly",
            headers={**headers, "Idempotency-Key": "weekly-review-submit"},
            json={
                "weekLabel": "2026-W31",
                "taskEntries": [
                    {
                        "taskId": task_id,
                        "contentDomain": "work",
                        "note": "已形成第一版材料",
                        "structuredNote": {"contentDomain": "work"},
                    }
                ],
                "workProgress": "完成伙伴材料初稿",
                "workBlocker": "等待伙伴反馈",
                "workDirection": "根据反馈迭代",
                "nextWeekFocus": "确认最终版本",
                "supportNeeded": "需要同事校对",
                "personalGrowthNote": "提升结构化表达",
            },
        )
        assert submitted.status_code == 200, submitted.text
        review = submitted.json()["review"]
        assert review["lifecycleState"] == "submitted"

        repeated = client.post(
            "/api/v2/workflow/reviews/weekly",
            headers={**headers, "Idempotency-Key": "weekly-review-submit"},
            json={
                "weekLabel": "2026-W31",
                "taskEntries": [
                    {
                        "taskId": task_id,
                        "contentDomain": "work",
                        "note": "已形成第一版材料",
                        "structuredNote": {"contentDomain": "work"},
                    }
                ],
                "workProgress": "完成伙伴材料初稿",
                "workBlocker": "等待伙伴反馈",
                "workDirection": "根据反馈迭代",
                "nextWeekFocus": "确认最终版本",
                "supportNeeded": "需要同事校对",
                "personalGrowthNote": "提升结构化表达",
            },
        )
        assert repeated.status_code == 200
        assert repeated.json() == submitted.json()

        dashboard = client.get(
            "/api/v2/workflow/reviews?weekLabel=2026-W31",
            headers=headers,
        )
        assert dashboard.status_code == 200, dashboard.text
        saved = dashboard.json()["reviews"][0]
        assert saved["taskLinks"][0]["taskId"] == task_id
        assert any(
            section["sectionType"] == "work_progress"
            for section in saved["sections"]
        )

        stale_review = client.post(
            "/api/v2/workflow/reviews/weekly",
            headers={**headers, "Idempotency-Key": "weekly-review-stale"},
            json={
                "weekLabel": "2026-W31",
                "expectedVersion": 999,
                "taskEntries": [],
                "workProgress": "不应覆盖",
            },
        )
        assert stale_review.status_code == 409
        assert stale_review.json()["error"]["code"] == "weekly_review_version_conflict"


def test_event_attachment_parse_creates_authoritative_document(
    tmp_path: Path,
) -> None:
    client, database = _client(tmp_path)
    with client:
        session, headers = _bootstrap(client)
        project_id = _project_id(client, headers)
        event_line = client.post(
            "/api/v2/event-lines",
            headers={**headers, "Idempotency-Key": "parse-event-line"},
            json={"projectId": project_id, "name": "附件解析事件线"},
        )
        assert event_line.status_code == 201, event_line.text
        event_line_id = event_line.json()["eventLine"]["eventLineId"]
        raw = "项目背景\n\n这是需要进入事件线知识上下文的正文。".encode()
        uploaded = client.post(
            f"/api/v2/workflow/event-lines/{event_line_id}/attachments",
            headers={**headers, "Idempotency-Key": "parse-attachment-upload"},
            json={
                "fileName": "background.md",
                "mediaType": "text/markdown",
                "byteSize": len(raw),
                "contentHash": hashlib.sha256(raw).hexdigest(),
                "contentBase64": base64.b64encode(raw).decode(),
                "title": "项目背景",
                "sourceKind": "event_line_attachment",
                "expectedVersion": 1,
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        attachment = uploaded.json()["attachment"]
        assert attachment["parseStatus"] == "uploaded"
        before_parse = client.get(
            f"/api/v2/workflow/event-lines/{event_line_id}",
            headers=headers,
        )
        assert before_parse.status_code == 200
        assert before_parse.json()["attachments"][0]["parseStatus"] == "uploaded"
        parsed = client.post(
            (
                f"/api/v2/workflow/event-lines/{event_line_id}/attachments/"
                f"{attachment['id']}/retry-parse"
            ),
            headers={**headers, "Idempotency-Key": "parse-attachment-run"},
            json={"expectedVersion": 1},
        )
        assert parsed.status_code == 200, parsed.text
        assert parsed.json()["status"] == "completed"
        assert parsed.json()["state"] == "ready"
        assert parsed.json()["documentId"]
        replay = client.post(
            (
                f"/api/v2/workflow/event-lines/{event_line_id}/attachments/"
                f"{attachment['id']}/retry-parse"
            ),
            headers={**headers, "Idempotency-Key": "parse-attachment-run"},
            json={"expectedVersion": 1},
        )
        assert replay.status_code == 200
        assert replay.json() == parsed.json()
        detail = client.get(
            f"/api/v2/workflow/event-lines/{event_line_id}",
            headers=headers,
        )
        assert detail.status_code == 200, detail.text
        projected = detail.json()["attachments"][0]
        assert projected["parseStatus"] == "ready"
        assert projected["documentId"] == parsed.json()["documentId"]
        assert "需要进入事件线知识上下文" in projected["parsedPreview"]

    with runtime_connection(database, "cloud", read_only=True) as connection:
        document = connection.execute(
            """
            SELECT kd.parse_state, dv.markdown_content
            FROM knowledge_documents kd
            JOIN document_versions dv
              ON dv.document_id = kd.document_id
             AND dv.version = kd.current_version
            WHERE kd.document_id = ?
            """,
            (parsed.json()["documentId"],),
        ).fetchone()
        assert document["parse_state"] == "ready"
        assert "需要进入事件线知识上下文" in document["markdown_content"]
        assert connection.execute(
            """
            SELECT COUNT(*) FROM audit_events
            WHERE resource_id = ?
              AND action = 'knowledge_document.attachment_parsed'
            """,
            (parsed.json()["documentId"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            """
            SELECT COUNT(*) FROM delivery_outbox
            WHERE aggregate_id = ?
              AND event_type = 'knowledge_document.attachment_parsed'
            """,
            (parsed.json()["documentId"],),
        ).fetchone()[0] == 1


def test_attachment_storage_merge_and_agent_plan_are_durable(tmp_path: Path) -> None:
    client, database = _client(tmp_path)
    with client:
        session, headers = _bootstrap(client)
        project_id = _project_id(client, headers)
        task = client.post(
            "/api/v2/tasks",
            headers={**headers, "Idempotency-Key": "attachment-task"},
            json={"title": "附件任务", "projectId": project_id},
        )
        assert task.status_code == 201, task.text
        task_id = task.json()["task"]["taskId"]
        raw = b"strict-workflow-attachment\x00payload"
        upload_payload = {
            "fileName": "proof.bin",
            "mediaType": "application/octet-stream",
            "byteSize": len(raw),
            "contentHash": hashlib.sha256(raw).hexdigest(),
            "contentBase64": base64.b64encode(raw).decode("ascii"),
            "title": "证据附件",
            "sourceKind": "task_attachment",
            "expectedVersion": 1,
        }
        uploaded = client.post(
            f"/api/v2/workflow/tasks/{task_id}/attachments",
            headers={**headers, "Idempotency-Key": "attachment-upload"},
            json=upload_payload,
        )
        assert uploaded.status_code == 200, uploaded.text
        attachment = uploaded.json()["attachment"]
        assert uploaded.json()["task"]["attachments"][0]["id"] == attachment["sourceAssetId"]

        with runtime_connection(database, "cloud", read_only=True) as connection:
            storage = connection.execute(
                "SELECT * FROM storage_objects WHERE object_id = ?",
                (attachment["storageObjectId"],),
            ).fetchone()
            command = connection.execute(
                """
                SELECT payload_json FROM command_envelopes
                WHERE command_type = 'task.attachment_added'
                  AND idempotency_key = 'attachment-upload'
                """
            ).fetchone()
        assert storage is not None
        managed = tmp_path / storage["storage_key"]
        assert managed.read_bytes() == raw
        assert "contentBase64" not in command["payload_json"]
        assert upload_payload["contentBase64"] not in database.read_text(
            encoding="latin1",
            errors="ignore",
        )
        downloaded = client.get(
            (
                f"/api/v2/workflow/tasks/{task_id}/attachments/"
                f"{attachment['sourceAssetId']}/content"
            ),
            headers=headers,
        )
        assert downloaded.status_code == 200, downloaded.text
        assert base64.b64decode(downloaded.json()["contentBase64"]) == raw
        transcript_text = "本机识别出的任务录音文本，只进入权威文档版本。"
        transcribed = client.post(
            (
                f"/api/v2/workflow/tasks/{task_id}/attachments/"
                f"{attachment['sourceAssetId']}/transcription-complete"
            ),
            headers={**headers, "Idempotency-Key": "attachment-transcribed"},
            json={
                "expectedVersion": 1,
                "text": transcript_text,
                "modelName": "sense-voice-test",
                "language": "zh",
                "segmentCount": 1,
            },
        )
        assert transcribed.status_code == 200, transcribed.text
        assert transcribed.json()["status"] == "completed"
        task_after_transcription = client.get(
            f"/api/v2/workflow/tasks/{task_id}",
            headers=headers,
        )
        assert task_after_transcription.status_code == 200
        projected_attachment = task_after_transcription.json()["task"][
            "attachments"
        ][0]
        assert projected_attachment["processingStatus"] == "ready"
        assert projected_attachment["transcriptAttachmentId"] == (
            attachment["sourceAssetId"]
        )
        transcript = client.get(
            (
                f"/api/v2/workflow/tasks/{task_id}/attachments/"
                f"{attachment['sourceAssetId']}/transcript"
            ),
            headers=headers,
        )
        assert transcript.status_code == 200, transcript.text
        assert transcript.json()["transcript"]["currentText"] == transcript_text
        edited_transcript_text = "成员校订后的任务录音文本。"
        edited = client.put(
            (
                f"/api/v2/workflow/tasks/{task_id}/attachments/"
                f"{attachment['sourceAssetId']}/transcript"
            ),
            headers={**headers, "Idempotency-Key": "attachment-transcript-edit"},
            json={
                "expectedVersion": 2,
                "expectedTranscriptVersion": 1,
                "text": edited_transcript_text,
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["transcript"]["currentText"] == edited_transcript_text
        assert edited.json()["transcript"]["version"] == 2
        replayed_edit = client.put(
            (
                f"/api/v2/workflow/tasks/{task_id}/attachments/"
                f"{attachment['sourceAssetId']}/transcript"
            ),
            headers={**headers, "Idempotency-Key": "attachment-transcript-edit"},
            json={
                "expectedVersion": 2,
                "expectedTranscriptVersion": 1,
                "text": edited_transcript_text,
            },
        )
        assert replayed_edit.status_code == 200
        assert replayed_edit.json() == edited.json()
        stale_edit = client.put(
            (
                f"/api/v2/workflow/tasks/{task_id}/attachments/"
                f"{attachment['sourceAssetId']}/transcript"
            ),
            headers={**headers, "Idempotency-Key": "attachment-transcript-stale"},
            json={
                "expectedVersion": 2,
                "expectedTranscriptVersion": 1,
                "text": "不应覆盖的新文本",
            },
        )
        assert stale_edit.status_code == 409
        persisted_transcript = client.get(
            (
                f"/api/v2/workflow/tasks/{task_id}/attachments/"
                f"{attachment['sourceAssetId']}/transcript"
            ),
            headers=headers,
        )
        assert persisted_transcript.status_code == 200
        assert (
            persisted_transcript.json()["transcript"]["currentText"]
            == edited_transcript_text
        )
        with runtime_connection(database, "cloud", read_only=True) as connection:
            transcript_command = connection.execute(
                """
                SELECT payload_json FROM command_envelopes
                WHERE command_type = 'task.attachment_transcription_completed'
                  AND idempotency_key = 'attachment-transcribed'
                """
            ).fetchone()
        assert transcript_command is not None
        assert transcript_text not in transcript_command["payload_json"]

        archived = client.request(
            "DELETE",
            f"/api/v2/workflow/tasks/{task_id}/attachments/{attachment['sourceAssetId']}",
            headers={**headers, "Idempotency-Key": "attachment-archive"},
            json={"expectedVersion": 3, "syncKnowledge": True},
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["lifecycleState"] == "archived"
        assert managed.exists()

        target = client.post(
            "/api/v2/event-lines",
            headers={**headers, "Idempotency-Key": "merge-target"},
            json={"projectId": project_id, "name": "合并目标"},
        )
        source = client.post(
            "/api/v2/event-lines",
            headers={**headers, "Idempotency-Key": "merge-source"},
            json={"projectId": project_id, "name": "合并来源"},
        )
        assert target.status_code == 201 and source.status_code == 201
        target_id = target.json()["eventLine"]["eventLineId"]
        source_id = source.json()["eventLine"]["eventLineId"]
        preview = client.post(
            f"/api/v2/workflow/event-lines/{target_id}/merge-preview",
            headers={**headers, "Idempotency-Key": "merge-preview"},
            json={"sourceIds": [source_id]},
        )
        assert preview.status_code == 200, preview.text
        merged = client.post(
            f"/api/v2/workflow/event-lines/{target_id}/merge",
            headers={**headers, "Idempotency-Key": "merge-commit"},
            json={
                "expectedVersion": preview.json()["targetVersion"],
                "sourceIds": preview.json()["sourceIds"],
                "sourceExpectedVersions": preview.json()["sourceExpectedVersions"],
            },
        )
        assert merged.status_code == 200, merged.text
        assert merged.json()["mergedSourceIds"] == [source_id]
        with runtime_connection(database, "cloud", read_only=True) as connection:
            source_row = connection.execute(
                "SELECT lifecycle_state FROM event_line_records WHERE event_line_id = ?",
                (source_id,),
            ).fetchone()
            source_audit = connection.execute(
                """
                SELECT COUNT(*) FROM audit_events
                WHERE resource_id = ?
                  AND action = 'event_line.merged_source_archived'
                """,
                (source_id,),
            ).fetchone()[0]
        assert source_row["lifecycle_state"] == "archived"
        assert source_audit == 1

        plan = client.put(
            "/api/v2/workflow/agent-weekly-plans/2026-W31/research",
            headers={**headers, "Idempotency-Key": "agent-plan-create"},
            json={
                "weekLabel": "2026-W31",
                "agentKey": "research",
                "summary": "本周调研重点",
                "planItems": [
                    {
                        "title": "完成资料梳理",
                        "rationale": "支撑伙伴沟通",
                        "scheduleHint": "周四前",
                        "status": "active",
                    }
                ],
            },
        )
        assert plan.status_code == 200, plan.text
        assert plan.json()["weeklyPlan"]["planItems"][0]["title"] == "完成资料梳理"
        persisted = client.get(
            "/api/v2/workflow/agent-weekly-plans"
            "?weekLabel=2026-W31&agentKey=research",
            headers=headers,
        )
        assert persisted.status_code == 200, persisted.text
        assert persisted.json()["weeklyPlans"][0]["summary"] == "本周调研重点"


def test_task_context_uses_only_published_project_knowledge_and_isolates_orgs(
    tmp_path: Path,
) -> None:
    contexts: dict[str, dict] = {}
    seeded: dict[str, dict[str, str]] = {}
    for prefix in ("ORG_A", "ORG_B"):
        data_dir = tmp_path / prefix.lower()
        data_dir.mkdir()
        client, database = _client(data_dir)
        with client:
            session, headers = _bootstrap(client)
            current = client.get(
                "/api/v2/session/current",
                headers=headers,
            ).json()
            project_id = _project_id(client, headers)
            seeded[prefix] = _seed_project_knowledge(
                database,
                organization_id=current["organizationId"],
                membership_id=current["membershipId"],
                project_id=project_id,
                prefix=prefix,
            )
            task = client.post(
                "/api/v2/tasks",
                headers={
                    **headers,
                    "Idempotency-Key": f"{prefix}-task-create",
                },
                json={
                    "title": f"{prefix} 项目任务",
                    "description": f"{prefix} 任务描述",
                    "projectId": project_id,
                },
            )
            assert task.status_code == 201, task.text
            task_id = task.json()["task"]["taskId"]
            response = client.get(
                f"/api/v2/workflow/tasks/{task_id}/context",
                headers=headers,
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["cloudInstanceId"] == session["cloudInstanceId"]
            assert payload["organizationId"] == current["organizationId"]
            contexts[prefix] = payload

    for prefix, other_prefix in (("ORG_A", "ORG_B"), ("ORG_B", "ORG_A")):
        payload = contexts[prefix]
        serialized = json.dumps(payload, ensure_ascii=False)
        project_knowledge = payload["projectKnowledge"]
        assert project_knowledge["state"] == "ready"
        assert {
            item["sourceType"] for item in project_knowledge["items"]
        } == {
            "knowledge_summary",
            "narrative_summary",
            "smart_import_summary",
        }
        assert seeded[prefix]["shared"] in payload["brief"]
        assert seeded[prefix]["narrative"] in payload["brief"]
        assert seeded[prefix]["smart"] in payload["brief"]
        brief = _task_context_brief(payload)
        assert seeded[prefix]["shared"] in brief["brief"]
        assert seeded[prefix]["smart"] in {
            item["summary"] for item in brief["summaryExcerpts"]
        }
        for forbidden in (
            seeded[prefix]["rawBody"],
            seeded[prefix]["rawPreview"],
            seeded[prefix]["candidate"],
            seeded[prefix]["payload"],
            seeded[prefix]["narrativeJson"],
            seeded[other_prefix]["shared"],
            seeded[other_prefix]["narrative"],
            seeded[other_prefix]["smart"],
        ):
            assert forbidden not in serialized


class _LocalKnowledgeRuntime:
    def __init__(self, *, switch_during_local_query: bool = False):
        self.fixed = SimpleNamespace(
            sandbox_id="sandbox-a",
            cloud_instance_id="cloud-a",
            organization_id="organization-a",
            cloud_api_url="https://cloud-a.invalid",
            principal_id="principal-a",
            membership_id="membership-a",
        )
        self.other = SimpleNamespace(
            sandbox_id="sandbox-b",
            cloud_instance_id="cloud-b",
            organization_id="organization-b",
            cloud_api_url="https://cloud-b.invalid",
            principal_id="principal-b",
            membership_id="membership-b",
        )
        self.active = self.fixed
        self.switch_during_local_query = switch_during_local_query

    def _current_context(self, *, require_ready: bool) -> SimpleNamespace:
        assert require_ready is True
        return self.active

    @staticmethod
    def _same_workspace_identity(expected: object, actual: object) -> bool:
        return expected == actual

    def cloud_query(
        self,
        path: str,
        *,
        query: dict[str, str] | None = None,
    ) -> dict:
        assert path == "/api/v2/workflow/tasks/task-a/context"
        assert query is None
        return {
            "cloudInstanceId": "cloud-a",
            "organizationId": "organization-a",
            "task": {"taskId": "task-a", "projectId": "project-a"},
            "projectKnowledge": {
                "projectId": "project-a",
                "organizationId": "organization-a",
                "state": "empty",
                "items": [],
                "summaryExcerpts": [],
                "materialBoundary": {
                    "sourceFileContentIncluded": False,
                    "sourceFilePathsIncluded": False,
                },
            },
            "summaryExcerpts": [],
            "sources": [],
            "brief": "",
        }

    def project_knowledge_context(self, project_id: str) -> dict:
        assert project_id == "project-a"
        result = {
            "sandboxId": "sandbox-a",
            "cloudInstanceId": "cloud-a",
            "organizationId": "organization-a",
            "project": {"projectId": project_id},
            "localPrivateKnowledge": [
                {
                    "sourceScope": "local_private",
                    "sourceType": "local_material",
                    "sourceId": "local-source-a",
                    "sourceVersion": 1,
                    "contentHash": "local-hash-a",
                    "title": "本机访谈摘要",
                    "summary": "仅属于当前 sandbox 的本机私有项目背景。",
                    "sourceDescription": "当前设备工作台本机私有资料",
                    "updatedAt": "2026-07-30T08:00:00Z",
                    "processingState": "ready",
                    "sourcePath": "/must/not/leave",
                }
            ],
            "state": {"localPrivate": "ready"},
        }
        if self.switch_during_local_query:
            self.active = self.other
        return result


def test_task_context_merges_current_sandbox_private_summary_and_rejects_switch() -> None:
    runtime = _LocalKnowledgeRuntime()
    compatibility = SimpleNamespace(runtime=runtime)
    context = _task_context_with_local_knowledge(compatibility, "task-a")
    serialized = json.dumps(context, ensure_ascii=False)
    assert context["projectKnowledge"]["localPrivateState"] == "ready"
    assert context["projectKnowledge"]["items"][0]["sourceScope"] == "local_private"
    assert "仅属于当前 sandbox 的本机私有项目背景。" in context["brief"]
    assert "/must/not/leave" not in serialized
    assert context["projectKnowledge"]["materialBoundary"][
        "localPrivateUploadedToOrganizationCloud"
    ] is False

    switched = _LocalKnowledgeRuntime(switch_during_local_query=True)
    with pytest.raises(LocalRuntimeError) as error:
        _task_context_with_local_knowledge(
            SimpleNamespace(runtime=switched),
            "task-a",
        )
    assert error.value.status_code == 409
    assert error.value.code == "workspace_context_changed"


def test_legacy_report_run_is_a_strict_report_artifact_projection() -> None:
    artifact = {
        "id": "report-a",
        "client_id": "project-a",
        "event_line_id": "event-a",
        "availability_status": "ready",
        "stale_reasons": [],
        "updated_at": "2026-07-30T09:00:00Z",
        "latest": {
            "content_markdown": "# 严格报告",
            "content_payload": {
                "sections": [{"title": "摘要"}],
                "sectionsStatus": ["done"],
                "totalLlmTokens": 17,
            },
            "narrative_id": "report-a",
            "narrative_rev": 2,
            "event_line_version": 3,
            "input_fingerprint": "strict-fingerprint",
            "created_at": "2026-07-30T08:00:00Z",
        },
    }
    run = _legacy_report_run(artifact)
    assert run["id"] == "report-a"
    assert run["status"] == "saved"
    assert run["body_markdown"] == "# 严格报告"
    assert run["artifact"] == artifact
    assert run["total_llm_tokens"] == 17


def test_recording_archive_reads_only_current_managed_recordings(tmp_path: Path) -> None:
    data_dir = tmp_path / "strict-data"
    recording_dir = data_dir / "recordings"
    recording_dir.mkdir(parents=True)
    recording = recording_dir / "session-a.webm"
    recording.write_bytes(b"strict-recording")
    compatibility = SimpleNamespace(
        runtime=SimpleNamespace(database_path=data_dir / "strict-local.db")
    )
    payload = _recording_upload_payload(
        compatibility,
        {
            "audioPath": str(recording),
            "sessionId": "session-a",
            "taskTitle": "访谈录音",
        },
        expected_version=4,
    )
    assert payload["fileName"] == "session-a.webm"
    assert payload["sourceKind"] == "task_recording"
    assert payload["expectedVersion"] == 4
    assert base64.b64decode(payload["contentBase64"]) == b"strict-recording"

    outside = tmp_path / "outside.webm"
    outside.write_bytes(b"must-not-read")
    with pytest.raises(LocalRuntimeError) as error:
        _recording_upload_payload(
            compatibility,
            {"audioPath": str(outside)},
            expected_version=4,
        )
    assert error.value.code == "recording_path_outside_managed_root"


class _WorkflowCompatibility:
    def __init__(self, runtime: Any):
        self.runtime = runtime

    @staticmethod
    def _snapshot() -> dict:
        return {"projects": []}

    @staticmethod
    def _task(item: Mapping[str, Any], _: Mapping[str, Any]) -> dict:
        return {
            "id": item.get("taskId"),
            "title": item.get("title"),
            "clientId": item.get("projectId"),
        }


class _WorkflowUiRuntime:
    def __init__(self, data_dir: Path):
        self.database_path = data_dir / "strict-local.db"
        self.fixed = SimpleNamespace(
            sandbox_id="sandbox-a",
            cloud_instance_id="cloud-a",
            organization_id="organization-a",
            principal_id="principal-a",
            membership_id="membership-a",
        )
        self.commands: list[tuple[str, str, dict[str, Any]]] = []

    def _current_context(self, *, require_ready: bool) -> SimpleNamespace:
        assert require_ready is True
        return self.fixed

    @staticmethod
    def _same_workspace_identity(expected: object, actual: object) -> bool:
        return expected == actual

    def project_knowledge_context(self, project_id: str) -> dict:
        return {
            "sandboxId": "sandbox-a",
            "cloudInstanceId": "cloud-a",
            "organizationId": "organization-a",
            "project": {"projectId": project_id},
            "localPrivateKnowledge": [],
            "state": {"localPrivate": "empty"},
        }

    def cloud_query(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
    ) -> dict:
        if path == "/api/v2/workflow/tasks/task-a":
            return {
                "task": {
                    "taskId": "task-a",
                    "projectId": "project-a",
                    "title": "准备伙伴沟通",
                    "version": 2,
                    "attachments": [],
                }
            }
        if path == "/api/v2/workflow/tasks/task-a/context":
            return {
                "cloudInstanceId": "cloud-a",
                "organizationId": "organization-a",
                "task": {
                    "taskId": "task-a",
                    "projectId": "project-a",
                    "title": "准备伙伴沟通",
                },
                "brief": "组织共享项目摘要",
                "sources": [
                    {
                        "type": "knowledge_summary",
                        "id": "knowledge-a",
                        "version": 2,
                        "title": "项目背景",
                    }
                ],
                "projectKnowledge": {
                    "items": [],
                    "summaryExcerpts": [],
                    "materialBoundary": {},
                },
                "summaryExcerpts": [],
            }
        if path == "/api/v2/workflow/event-lines/event-a/report-artifacts":
            return {"artifacts": [{"id": "report-a"}]}
        if path == "/api/v2/workbench/reports/report-a":
            return {
                "id": "report-a",
                "client_id": "project-a",
                "event_line_id": "event-a",
                "availability_status": "ready",
                "latest": {
                    "content_markdown": "# 报告",
                    "content_payload": {},
                    "created_at": "2026-07-30T08:00:00Z",
                },
                "updated_at": "2026-07-30T08:00:00Z",
            }
        if path == "/api/v2/intelligence-growth/query":
            assert query == {"resourcePath": "proposals/proposal-a"}
            return {
                "id": "proposal-a",
                "clientId": "project-a",
                "kind": "task_prep",
                "status": "draft",
            }
        raise AssertionError((path, query))

    def cloud_command(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
        **_: Any,
    ) -> dict:
        self.commands.append((method, path, dict(payload)))
        if path == "/api/v2/workflow/tasks/task-a/attachments":
            return {
                "task": {
                    "taskId": "task-a",
                    "projectId": "project-a",
                    "title": "准备伙伴沟通",
                    "version": 3,
                    "attachments": [{"id": "attachment-a"}],
                }
            }
        if path == "/api/v2/workbench/projects/project-a/proposal-drafts":
            return {"proposalId": "proposal-a"}
        raise AssertionError((method, path, idempotency_key))


def test_workflow_ui_connects_reports_recording_and_prep_proposal(
    tmp_path: Path,
) -> None:
    runtime = _WorkflowUiRuntime(tmp_path)
    compatibility = _WorkflowCompatibility(runtime)
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    recording = recordings / "session-a.webm"
    recording.write_bytes(b"strict-recording")

    report_runs = router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="event-lines/event-a/legacy-report-runs",
            query={},
            body={},
            idempotency_key="",
        ),
    )
    assert report_runs[0]["artifact"]["id"] == "report-a"

    archived = router.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="tasks/task-a/recordings",
            query={},
            body={
                "audioPath": str(recording),
                "sessionId": "session-a",
                "taskTitle": "准备伙伴沟通",
            },
            idempotency_key="recording-a",
        ),
    )
    assert archived["attachments"] == [{"id": "attachment-a"}]
    assert runtime.commands[-1][2]["sourceKind"] == "task_recording"

    proposal = router.dispatch(
        compatibility,
        UiRequest(
            method="POST",
            path="tasks/task-a/prep-pack/proposals",
            query={},
            body={},
            idempotency_key="proposal-a",
        ),
    )
    assert proposal == {
        "id": "proposal-a",
        "clientId": "project-a",
        "kind": "task_prep",
        "status": "draft",
    }
    proposal_payload = runtime.commands[-1][2]
    assert proposal_payload["payload"]["taskId"] == "task-a"
    assert proposal_payload["sourceRefs"] == [
        "knowledge_summary:knowledge-a@2"
    ]


def test_event_line_report_contract_is_complete_and_renderer_safe() -> None:
    class Runtime:
        @staticmethod
        def cloud_query(
            path: str,
            *,
            query: Mapping[str, str] | None = None,
        ) -> dict[str, Any]:
            assert query is None
            assert path == "/api/v2/workflow/event-lines/event-safe"
            return {
                "eventLine": {
                    "eventLineId": "event-safe",
                    "projectId": "project-a",
                    "name": "日慈推进线",
                    "background": "项目推进背景",
                    "goal": "完成伙伴沟通",
                    "lifecycleState": "active",
                    "version": 3,
                    "createdByMembershipId": "member-a",
                    "participantMembershipIds": ["member-a"],
                    "taskCount": 1,
                    "attachmentCount": 0,
                },
                "tasks": [],
                "activities": [
                    {
                        "id": "activity-a",
                        "title": "完成访谈",
                        "summary": "形成访谈纪要",
                        "actorName": "林佳维",
                        "createdAt": "2026-07-31T00:00:00Z",
                    }
                ],
                "attachments": [],
            }

    compatibility = SimpleNamespace(
        runtime=Runtime(),
        _snapshot=lambda: {
            "projects": [{"projectId": "project-a", "name": "日慈基金会"}]
        },
        _member_names=lambda: {"member-a": "林佳维"},
        auth_state=lambda: {
            "user": {"id": "member-a", "primaryRole": "member"}
        },
    )
    snapshot = router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="event-lines/event-safe/report-snapshot",
            query={},
            body={},
            idempotency_key="",
        ),
    )
    assert snapshot["participantNames"] == ["林佳维"]
    assert snapshot["snapshotAt"]
    assert snapshot["timelineNodes"] == []
    assert snapshot["sourceState"] == "cloud_ready"
    assert snapshot["canEdit"] is True

    narrative = router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="event-lines/event-safe/timeline-narrative",
            query={},
            body={},
            idempotency_key="",
        ),
    )
    assert narrative["rev"] == 3
    assert narrative["headline"] == "日慈推进线"
    assert narrative["nodes"][0]["linkedActivityIds"] == ["activity-a"]
    assert narrative["updatedAt"]
