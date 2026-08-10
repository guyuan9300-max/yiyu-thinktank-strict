from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.ui_domains.workflow import (
    _dispatch_unpinned,
    _event_detail_ui,
    _task_ui,
)
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from strict_common.ids import utc_now
from strict_common.schema import runtime_connection


class _WorkflowRuntime:
    def __init__(self) -> None:
        self.queries: list[tuple[str, Mapping[str, str] | None]] = []

    def cloud_query(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        self.queries.append((path, query))
        if path.endswith("/agent-weekly-plans"):
            return {
                "weeklyPlans": [
                    {
                        "planId": "plan-agent-1",
                        "agentKey": "strategy_design",
                        "agentName": "策略机器人",
                        "departmentName": "咨询策略部",
                        "color": "#123456",
                        "weekLabel": "2026-W31",
                        "summary": "推进日慈项目策略材料",
                        "planItems": [
                            {
                                "id": "plan-item-1",
                                "title": "整理日慈策略",
                                "status": "active",
                            }
                        ],
                        "sourcePolicy": {
                            "authority": "organization_plans"
                        },
                    }
                ]
            }
        if path.endswith("/plan-item-tasks"):
            assert query == {"planItemId": "plan-item-1"}
            return {
                "tasks": [
                    {
                        "taskId": "task-linked",
                        "title": "整理日慈策略",
                        "description": "形成项目背景提纲",
                        "lifecycleState": "todo",
                        "updatedAt": "2026-07-30T08:00:00Z",
                        "attachments": [
                            {"id": "attachment-1"},
                        ],
                        "version": 2,
                    }
                ]
            }
        if path.endswith("/board"):
            return {
                "tasks": [
                    {
                        "taskId": task_id,
                        "title": title,
                        "description": f"{title}说明",
                        "lifecycleState": "todo",
                        "visibilityScope": visibility,
                        "projectId": "project-1",
                        "eventLineId": "event-1",
                        "updatedAt": "2026-07-30T08:00:00Z",
                        "createdAt": "2026-07-29T08:00:00Z",
                        "attachments": [],
                        "tags": [],
                        "version": 1,
                    }
                    for task_id, title, visibility in (
                        ("task-work", "我的工作任务", "participants"),
                        ("task-personal", "我的个人任务", "self"),
                        ("task-department", "同部门任务", "participants"),
                        ("task-other", "其他部门任务", "organization"),
                    )
                ],
                "lists": [],
                "tags": [],
            }
        if path.endswith("/reviews"):
            return {
                "reviews": [
                    {
                        "weeklyReviewId": "review-1",
                        "membershipId": "member-1",
                        "weekLabel": "2026-W31",
                        "version": 2,
                        "updatedAt": "2026-07-30T09:00:00Z",
                        "taskLinks": [
                            {
                                "taskId": "task-work",
                                "contentDomain": "work",
                                "structuredNote": {
                                    "contentDomain": "work"
                                },
                            },
                            {
                                "taskId": "task-personal",
                                "contentDomain": "personal",
                                "structuredNote": {
                                    "contentDomain": "personal"
                                },
                            },
                        ],
                    },
                    {
                        "weeklyReviewId": "review-2",
                        "membershipId": "member-2",
                        "weekLabel": "2026-W31",
                        "version": 7,
                        "updatedAt": "2026-07-30T09:30:00Z",
                        "taskLinks": [
                            {
                                "taskId": "task-department",
                                "contentDomain": "work",
                                "structuredNote": {
                                    "contentDomain": "work",
                                },
                            }
                        ],
                    },
                    {
                        "weeklyReviewId": "review-3",
                        "membershipId": "member-3",
                        "weekLabel": "2026-W31",
                        "version": 9,
                        "updatedAt": "2026-07-30T10:00:00Z",
                        "taskLinks": [
                            {
                                "taskId": "task-other",
                                "contentDomain": "work",
                                "structuredNote": {
                                    "contentDomain": "work",
                                },
                            }
                        ],
                    },
                ]
            }
        if path.endswith("/clients-pulse"):
            return {
                "summaries": [
                    {
                        "clientId": "project-1",
                        "weeklyNewDocumentCount": 1,
                    }
                ],
                "generatedAt": "2026-07-30T10:00:00Z",
            }
        raise AssertionError((path, query))


class _WorkflowCompatibility:
    def __init__(
        self,
        user: Mapping[str, Any] | None = None,
    ) -> None:
        self.runtime = _WorkflowRuntime()
        self.user = dict(
            user
            or {
                "id": "member-1",
                "primaryRole": "employee",
                "visibilityScope": "self",
                "departmentId": "department-a",
                "departmentName": "项目部",
                "isDepartmentLead": False,
            }
        )

    @staticmethod
    def _snapshot() -> dict[str, Any]:
        return {
            "projects": [
                {
                    "projectId": "project-1",
                    "name": "日慈基金会",
                }
            ],
            "eventLines": [
                {
                    "eventLineId": "event-1",
                    "projectId": "project-1",
                    "name": "日慈项目推进",
                    "goal": "完成项目交付",
                    "background": "日慈项目背景",
                    "lifecycleState": "active",
                    "attachmentCount": 2,
                    "departmentId": "department-a",
                }
            ],
            "plans": [{"planId": "formal-plan-1"}],
        }

    @staticmethod
    def _session() -> dict[str, Any]:
        return {
            "departments": [
                {
                    "departmentId": "department-a",
                    "name": "项目部",
                    "members": [
                        {"membershipId": "member-1"},
                        {"membershipId": "member-2"},
                    ],
                },
                {
                    "departmentId": "department-b",
                    "name": "研究部",
                    "members": [{"membershipId": "member-3"}],
                },
            ]
        }

    @staticmethod
    def _task(
        item: Mapping[str, Any],
        _snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "id": item["taskId"],
            "title": item["title"],
            "desc": item.get("description") or "",
            "status": "todo",
            "scopeMode": (
                "PERSONAL_ONLY"
                if item.get("visibilityScope") == "self"
                else "COLLAB_SHARED"
            ),
            "clientId": item.get("projectId"),
            "clientName": "日慈基金会",
            "eventLineId": item.get("eventLineId"),
            "eventLineName": "日慈项目推进",
            "note": "",
            "ownerName": "成员",
            "listName": "全部任务",
            "listColor": "#5B7BFE",
            "tags": [],
            "createdAt": item.get("createdAt"),
            "updatedAt": item["updatedAt"],
        }

    def auth_state(self) -> dict[str, Any]:
        return {"user": dict(self.user)}

    @staticmethod
    def _member_names() -> dict[str, str]:
        return {}


def _dispatch(
    compatibility: _WorkflowCompatibility,
    method: str,
    path: str,
    *,
    query: Mapping[str, str] | None = None,
) -> Any:
    return _dispatch_unpinned(
        compatibility,
        UiRequest(
            method=method,
            path=path,
            query=dict(query or {}),
            body={},
            idempotency_key=f"{method}:{path}",
        ),
        None,  # type: ignore[arg-type]
    )


def test_agent_views_only_project_tasks_linked_to_authoritative_agent_plan() -> None:
    compatibility = _WorkflowCompatibility()

    execution = _dispatch(
        compatibility,
        "GET",
        "tasks/agent-execution",
        query={
            "week": "2026-W31",
            "department": "咨询策略部",
        },
    )
    worklogs = _dispatch(
        compatibility,
        "GET",
        "tasks/agent-worklogs",
        query={"month": "2026-07"},
    )

    assert [task["id"] for task in execution] == ["task-linked"]
    assert execution[0]["evidenceCount"] == 1
    assert worklogs["worklogs"] == [
        {
            "id": "plan-agent-1:plan-item-1:task-linked",
            "agentKey": "strategy_design",
            "agentName": "策略机器人",
            "departmentName": "咨询策略部",
            "color": "#123456",
            "date": "2026-07-30",
            "weekLabel": "2026-W31",
            "title": "整理日慈策略",
            "summary": "形成项目背景提纲",
            "detailLines": [
                "形成项目背景提纲",
                "日慈基金会",
                "日慈项目推进",
            ],
            "sourceType": "workspace_sync",
            "createdAt": "2026-07-30T08:00:00Z",
        }
    ]
    assert worklogs["weeklyDigests"][0]["evidenceCount"] == 1
    assert worklogs["weeklyDigests"][0]["focusItems"] == [
        "整理日慈策略"
    ]
    assert worklogs["weeklyPlans"][0]["planItems"][0]["status"] == (
        "planned"
    )
    assert not any(
        path.endswith("/board")
        for path, _query in compatibility.runtime.queries
    )


def test_weekly_review_projection_preserves_personal_items_and_truthful_status() -> None:
    compatibility = _WorkflowCompatibility()

    dashboard = _dispatch(
        compatibility,
        "GET",
        "reviews",
        query={
            "weekLabel": "2026-W31",
            "perspective": "organization",
        },
    )
    refreshed = _dispatch(
        compatibility,
        "POST",
        "reviews/weekly-overview/refresh",
        query={"weekLabel": "2026-W31"},
    )
    status = _dispatch(
        compatibility,
        "GET",
        "reviews/weekly-overview/status",
        query={"weekLabel": "2026-W31"},
    )
    history = _dispatch(
        compatibility,
        "GET",
        "reviews/history",
    )

    assert dashboard["activePerspective"] == "mine"
    assert dashboard["availablePerspectives"] == [
        {"key": "mine", "label": "我的视角"}
    ]
    assert [item["taskId"] for item in dashboard["workItems"]] == [
        "task-work"
    ]
    assert [item["taskId"] for item in dashboard["personalItems"]] == [
        "task-personal"
    ]
    assert dashboard["currentReview"]["id"] == "review-1"
    assert dashboard["currentReview"]["_strictVersion"] == 2
    assert [
        item["taskId"]
        for item in dashboard["currentReview"]["taskEntries"]
    ] == ["task-work", "task-personal"]
    work_entry = dashboard["workItems"][0]
    assert work_entry["id"] == "review-1:task-work"
    assert work_entry["weekLabel"] == "2026-W31"
    assert work_entry["contentDomain"] == "work"
    assert work_entry["structuredNote"]["completionStatus"] == "in_progress"
    assert work_entry["taskSnapshot"]["title"] == "我的工作任务"
    assert work_entry["taskSnapshot"]["eventLineContext"] == {
        "id": "event-1",
        "name": "日慈项目推进",
        "businessCategory": None,
        "stage": "active",
        "summary": "日慈项目背景",
        "intent": "完成项目交付",
        "currentBlocker": None,
        "recentDecision": None,
        "nextStep": None,
        "evidenceCount": 2,
        "primaryClientId": "project-1",
        "primaryClientName": "日慈基金会",
        "primaryDepartmentId": "department-a",
        "primaryDepartmentName": "项目部",
    }
    assert refreshed["status"] == "succeeded"
    assert refreshed["viewerUserId"] == "member-1"
    assert refreshed["sourceCounts"] == {
        "reviews": 1,
        "workItems": 1,
        "personalItems": 1,
        "plans": 1,
    }
    assert status["status"] == "idle"
    assert status["generatedAt"] is None
    assert history["items"][0]["personalItemCount"] == 1


def test_review_perspectives_filter_reviews_links_and_editable_current() -> None:
    department_lead = _WorkflowCompatibility(
        {
            "id": "member-1",
            "primaryRole": "employee",
            "visibilityScope": "department",
            "departmentId": "department-a",
            "departmentName": "项目部",
            "isDepartmentLead": True,
        }
    )
    team = _dispatch(
        department_lead,
        "GET",
        "reviews",
        query={
            "weekLabel": "2026-W31",
            "perspective": "department",
            "departmentId": "department-b",
        },
    )
    assert team["activeDepartmentId"] == "department-a"
    assert [item["taskId"] for item in team["workItems"]] == [
        "task-work",
        "task-department",
    ]
    assert [item["taskId"] for item in team["personalItems"]] == [
        "task-personal"
    ]
    assert team["currentReview"]["id"] == "review-1"

    admin = _WorkflowCompatibility(
        {
            "id": "member-1",
            "primaryRole": "admin",
            "visibilityScope": "organization",
            "departmentId": "department-a",
            "departmentName": "项目部",
            "isDepartmentLead": False,
        }
    )
    organization = _dispatch(
        admin,
        "GET",
        "reviews",
        query={
            "weekLabel": "2026-W31",
            "perspective": "organization",
        },
    )
    assert [item["taskId"] for item in organization["workItems"]] == [
        "task-work",
        "task-department",
        "task-other",
    ]
    assert [item["taskId"] for item in organization["personalItems"]] == [
        "task-personal"
    ]
    assert organization["currentReview"]["id"] == "review-1"
    assert organization["currentReview"]["_strictVersion"] == 2

    other_department = _dispatch(
        admin,
        "GET",
        "reviews",
        query={
            "weekLabel": "2026-W31",
            "perspective": "department",
            "departmentId": "department-b",
        },
    )
    assert [item["taskId"] for item in other_department["workItems"]] == [
        "task-other"
    ]
    assert other_department["personalItems"] == []
    assert other_department["currentReview"] is None


def test_task_and_event_detail_counts_use_authoritative_children() -> None:
    compatibility = _WorkflowCompatibility()

    task = _task_ui(
        compatibility,
        {
            "taskId": "task-count",
            "title": "带附件任务",
            "updatedAt": "2026-07-30T08:00:00Z",
            "attachments": [{"id": "a"}, {"id": "b"}],
        },
    )
    event = _event_detail_ui(
        compatibility,
        {
            "eventLine": {
                "eventLineId": "event-count",
                "name": "事件线",
            },
            "activities": [{"id": "one"}, {"id": "two"}],
        },
    )

    assert task["evidenceCount"] == 2
    assert event["eventLine"]["activityCount"] == 2


def test_clients_pulse_ui_delegates_to_cloud_authority() -> None:
    compatibility = _WorkflowCompatibility()

    response = _dispatch(
        compatibility,
        "GET",
        "reviews/clients-pulse",
    )

    assert response["summaries"][0]["weeklyNewDocumentCount"] == 1
    assert compatibility.runtime.queries[-1] == (
        "/api/v2/workflow/clients-pulse",
        None,
    )


def test_clients_pulse_uses_visible_v4_authorities(tmp_path: Path) -> None:
    database = tmp_path / "strict-workflow-pulse.db"
    client = TestClient(
        create_app(
            CloudConfig(
                data_dir=tmp_path,
                database_path=database,
                bootstrap_token="pulse-bootstrap",
                master_key=Fernet.generate_key().decode(),
                cloud_instance_id=None,
            )
        )
    )
    with client:
        bootstrapped = client.post(
            "/api/v2/auth/bootstrap-organization",
            json={
                "organizationName": "Pulse 严格测试组织",
                "displayName": "管理员",
                "email": "pulse-admin@example.com",
                "password": "12345678",
                "bootstrapToken": "pulse-bootstrap",
            },
        )
        assert bootstrapped.status_code == 201, bootstrapped.text
        session = bootstrapped.json()
        headers = {
            "Authorization": f"Bearer {session['accessToken']}",
        }
        department = client.post(
            "/api/v2/organization/departments",
            headers={
                **headers,
                "Idempotency-Key": "pulse-department-create",
            },
            json={
                "name": "其他项目部",
                "expectedOrganizationVersion": 1,
            },
        )
        assert department.status_code == 201, department.text
        invite = client.post(
            "/api/v2/organization/invites",
            headers=headers,
            json={
                "inviteKind": "department",
                "targetId": department.json()["id"],
            },
        )
        assert invite.status_code == 201, invite.text
        joined = client.post(
            "/api/v2/auth/join",
            json={
                "inviteCode": invite.json()["inviteCode"],
                "displayName": "其他成员",
                "email": "pulse-member@example.com",
                "password": "member-password",
            },
        )
        assert joined.status_code == 201, joined.text
        other_member_id = joined.json()["membershipId"]
        snapshot = client.get(
            "/api/v2/business/snapshot",
            headers=headers,
        )
        assert snapshot.status_code == 200, snapshot.text
        project_id = snapshot.json()["projects"][0]["projectId"]
        task = client.post(
            "/api/v2/tasks",
            headers={
                **headers,
                "Idempotency-Key": "pulse-task-create",
            },
                json={
                    "title": "日慈项目本周逾期任务",
                    "projectId": project_id,
                    "dueDate": "2020-01-01",
                    "visibilityScope": "organization",
                },
        )
        assert task.status_code == 201, task.text
        task_id = task.json()["task"]["taskId"]
        review = client.post(
            "/api/v2/workflow/reviews/weekly/draft",
            headers={
                **headers,
                "Idempotency-Key": "pulse-review-create",
            },
            json={
                "weekLabel": "2026-W31",
                "taskEntries": [
                    {
                        "taskId": task_id,
                        "contentDomain": "personal",
                        "note": "任务复盘",
                        "structuredNote": {"reflection": "任务复盘"},
                    }
                ],
            },
        )
        assert review.status_code == 200, review.text
        saved_review = client.get(
            "/api/v2/workflow/reviews?weekLabel=2026-W31",
            headers=headers,
        )
        assert saved_review.status_code == 200, saved_review.text
        saved_link = saved_review.json()["reviews"][0]["taskLinks"][0]
        assert saved_link["contentDomain"] == "work"
        assert saved_link["structuredNote"]["contentDomain"] == "work"
        now = utc_now()
        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                INSERT INTO work_projects (
                  project_id, organization_id, name, alias, summary, domain,
                  color, is_default_internal_project, lifecycle_state,
                  created_by_membership_id, version, created_at, updated_at,
                  archived_at
                ) VALUES (
                  'pulse-other-project', ?, '其他项目', '', '', '项目',
                  '#123456', 0, 'active', ?, 1, ?, ?, NULL
                )
                """,
                (
                    session["organizationId"],
                    session["membershipId"],
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE task_records
                SET attributes_json = ?, visibility_scope = 'organization'
                WHERE organization_id = ? AND task_id = ?
                """,
                (
                    '{"currentBlocker":"等待项目确认"}',
                    session["organizationId"],
                    task_id,
                ),
            )
            connection.execute(
                """
                DELETE FROM project_participants
                WHERE organization_id = ? AND project_id = ?
                  AND membership_id = ?
                """,
                (
                    session["organizationId"],
                    project_id,
                    other_member_id,
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
                ) VALUES (
                  'pulse-document', ?, ?, 'assigned', NULL, ?, NULL,
                  '日慈项目背景摘要', 'shared_summary', 'organization',
                  'ready', 'active', 1, 1, ?, ?
                )
                """,
                (
                    session["organizationId"],
                    project_id,
                    session["membershipId"],
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
                ) VALUES (
                  'pulse-version', ?, 'pulse-document', 1,
                  'pulse-hash', '背景摘要', '背景摘要', 1, 1,
                  'workflow-pulse-test', ?
                )
                """,
                (session["organizationId"], now),
            )
            for (
                document_id,
                version_id,
                visibility_scope,
                department_id,
                owner_membership_id,
            ) in (
                (
                    "pulse-department-document",
                    "pulse-department-version",
                    "department",
                    department.json()["id"],
                    other_member_id,
                ),
                (
                    "pulse-participants-document",
                    "pulse-participants-version",
                    "participants",
                    None,
                    session["membershipId"],
                ),
            ):
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                      document_id, organization_id, project_id,
                      project_assignment_state, source_asset_id,
                      owner_membership_id, department_id, title,
                      document_kind, visibility_scope, parse_state,
                      lifecycle_state, current_version, version, created_at,
                      updated_at
                    ) VALUES (
                      ?, ?, ?, 'assigned', NULL, ?, ?, '受限资料',
                      'raw_source', ?, 'ready', 'active', 1, 1, ?, ?
                    )
                    """,
                    (
                        document_id,
                        session["organizationId"],
                        project_id,
                        owner_membership_id,
                        department_id,
                        visibility_scope,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO document_versions (
                      document_version_id, organization_id, document_id,
                      version, content_hash, preview_text, markdown_content,
                      section_count, chunk_count, generator_version,
                      created_at
                    ) VALUES (
                      ?, ?, ?, 1, ?, '受限', '受限', 1, 1,
                      'workflow-pulse-test', ?
                    )
                    """,
                    (
                        version_id,
                        session["organizationId"],
                        document_id,
                        f"{version_id}-hash",
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
                ) VALUES (
                  'pulse-private-document', ?, ?, 'assigned', NULL, ?, NULL,
                  '其他成员私有资料', 'raw_source', 'self',
                  'ready', 'active', 1, 1, ?, ?
                )
                """,
                (
                    session["organizationId"],
                    project_id,
                    other_member_id,
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
                ) VALUES (
                  'pulse-private-version', ?, 'pulse-private-document', 1,
                  'pulse-private-hash', '私有', '私有', 1, 1,
                  'workflow-pulse-test', ?
                )
                """,
                (session["organizationId"], now),
            )
            connection.execute(
                """
                INSERT INTO knowledge_documents (
                  document_id, organization_id, project_id,
                  project_assignment_state, source_asset_id,
                  owner_membership_id, department_id, title, document_kind,
                  visibility_scope, parse_state, lifecycle_state,
                  current_version, version, created_at, updated_at
                ) VALUES (
                  'pulse-cross-document', ?, 'pulse-other-project',
                  'assigned', NULL, ?, NULL, '跨项目资料', 'shared_summary',
                  'organization', 'ready', 'active', 1, 1, ?, ?
                )
                """,
                (
                    session["organizationId"],
                    session["membershipId"],
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
                ) VALUES (
                  'pulse-cross-version', ?, 'pulse-cross-document', 1,
                  'pulse-cross-hash', '跨项目', '跨项目', 1, 1,
                  'workflow-pulse-test', ?
                )
                """,
                (session["organizationId"], now),
            )
            connection.execute(
                """
                INSERT INTO evidence_links (
                  evidence_link_id, organization_id, source_type, source_id,
                  target_type, target_id, relation_kind, lifecycle_state,
                  linked_by_membership_id, version, created_at, updated_at
                ) VALUES (
                  'pulse-evidence', ?, 'document_version', 'pulse-version',
                  'task', ?, 'supports', 'active', ?, 1, ?, ?
                )
                """,
                (
                    session["organizationId"],
                    task_id,
                    session["membershipId"],
                    now,
                    now,
                ),
            )
            for evidence_id, source_id in (
                ("pulse-private-evidence", "pulse-private-version"),
                (
                    "pulse-department-evidence",
                    "pulse-department-version",
                ),
                (
                    "pulse-participants-evidence",
                    "pulse-participants-version",
                ),
                ("pulse-cross-evidence", "pulse-cross-version"),
            ):
                connection.execute(
                    """
                    INSERT INTO evidence_links (
                      evidence_link_id, organization_id, source_type,
                      source_id, target_type, target_id, relation_kind,
                      lifecycle_state, linked_by_membership_id, version,
                      created_at, updated_at
                    ) VALUES (
                      ?, ?, 'document_version', ?, 'task', ?, 'supports',
                      'active', ?, 1, ?, ?
                    )
                    """,
                    (
                        evidence_id,
                        session["organizationId"],
                        source_id,
                        task_id,
                        session["membershipId"],
                        now,
                        now,
                    ),
                )
            connection.commit()

        response = client.get(
            "/api/v2/workflow/clients-pulse",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        item = next(
            entry
            for entry in response.json()["summaries"]
            if entry["clientId"] == project_id
        )
        assert item == {
            "clientId": project_id,
            "clientName": snapshot.json()["projects"][0]["name"],
            "clientStage": "active",
            "weeklyNewDocumentCount": 2,
            "weeklyNewTaskCount": 1,
            "weeklyNewEvidenceCount": 2,
            "currentBlockerCount": 1,
            "overdueTodoCount": 1,
            "hasActivity": True,
            "topSignal": "1 项任务已逾期",
        }
        member_response = client.get(
            "/api/v2/workflow/clients-pulse",
            headers={
                "Authorization": f"Bearer {joined.json()['accessToken']}",
            },
        )
        assert member_response.status_code == 200, member_response.text
        member_item = next(
            entry
            for entry in member_response.json()["summaries"]
            if entry["clientId"] == project_id
        )
        assert member_item["weeklyNewDocumentCount"] == 3
        assert member_item["weeklyNewEvidenceCount"] == 3
