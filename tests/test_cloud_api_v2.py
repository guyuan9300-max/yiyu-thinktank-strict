from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from passlib.hash import pbkdf2_sha256

from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from strict_common.schema import runtime_connection
from strict_common.security import LEGACY_PASSWORD_SCHEME, PASSWORD_SCHEME
from tests.strict_cloud_test_factory import (
    provision_test_organization,
    strict_cloud_test_client,
)


def cloud_client(tmp_path: Path) -> tuple[TestClient, Path]:
    client, database, _ = strict_cloud_test_client(
        tmp_path,
        bootstrap_token="bootstrap-test",
        cloud_instance_id="cloud-api-v2-test",
    )
    return client, database


def bootstrap(client: TestClient) -> dict:
    return provision_test_organization(
        client,
        organization_name="严格测试组织",
        display_name="管理员",
        email="admin@example.com",
        phone="13800138000",
        password="12345678",
    )


def test_identity_organization_permissions_and_ai(tmp_path: Path) -> None:
    client, database = cloud_client(tmp_path)
    with client:
        admin = bootstrap(client)
        admin_header = {"Authorization": f"Bearer {admin['accessToken']}"}

        login_by_phone = client.post(
            "/api/v2/auth/login",
            json={"identifier": "13800138000", "password": "12345678"},
        )
        assert login_by_phone.status_code == 200
        assert login_by_phone.json()["principalId"] == admin["principalId"]

        department = client.post(
            "/api/v2/organization/departments",
            headers={**admin_header, "Idempotency-Key": "department-1"},
            json={"name": "企划中心", "expectedOrganizationVersion": 1},
        )
        assert department.status_code == 201, department.text
        repeated = client.post(
            "/api/v2/organization/departments",
            headers={**admin_header, "Idempotency-Key": "department-1"},
            json={"name": "企划中心", "expectedOrganizationVersion": 1},
        )
        assert repeated.status_code == 201
        assert repeated.json() == department.json()

        title = client.post(
            "/api/v2/organization/management-titles",
            headers={**admin_header, "Idempotency-Key": "title-1"},
            json={"name": "顾问", "expectedOrganizationVersion": 2},
        )
        assert title.status_code == 201, title.text

        invite = client.post(
            "/api/v2/organization/invites",
            headers=admin_header,
            json={
                "inviteKind": "department",
                "targetId": department.json()["id"],
            },
        )
        assert invite.status_code == 201, invite.text
        member = client.post(
            "/api/v2/auth/join",
            json={
                "inviteCode": invite.json()["inviteCode"],
                "displayName": "普通成员",
                "email": "member@example.com",
                "password": "abcdefgh",
            },
        )
        assert member.status_code == 201, member.text
        member_header = {"Authorization": f"Bearer {member.json()['accessToken']}"}

        denied = client.post(
            "/api/v2/organization/departments",
            headers={**member_header, "Idempotency-Key": "member-denied"},
            json={"name": "越权部门", "expectedOrganizationVersion": 3},
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "admin_required"

        saved = client.put(
            "/api/v2/settings/org-ai-config",
            headers={**admin_header, "Idempotency-Key": "ai-config-1"},
            json={
                "provider": "doubao",
                "baseUrl": "https://example.invalid/v1",
                "modelName": "strict-model",
                "apiKey": "strict-api-secret",
                "expectedVersion": 0,
            },
        )
        assert saved.status_code == 200, saved.text
        assert "apiKey" not in saved.json()

        runtime = client.get(
            "/api/v2/settings/org-ai-config/runtime-secret",
            headers=member_header,
        )
        assert runtime.status_code == 200, runtime.text
        assert runtime.json()["apiKey"] == "strict-api-secret"
        assert runtime.json()["organizationId"] == admin["organizationId"]

        member_save = client.put(
            "/api/v2/settings/org-ai-config",
            headers={**member_header, "Idempotency-Key": "member-ai-denied"},
            json={
                "provider": "doubao",
                "baseUrl": "https://example.invalid/v1",
                "modelName": "wrong",
                "apiKey": "wrong",
                "expectedVersion": 1,
            },
        )
        assert member_save.status_code == 403

        old_api = client.get("/api/v1/workspaces/current")
        assert old_api.status_code == 404

    persisted = b"".join(
        path.read_bytes()
        for path in tmp_path.iterdir()
        if path.is_file()
    )
    assert b"strict-api-secret" not in persisted
    assert b"12345678" not in persisted
    assert b"abcdefgh" not in persisted
    assert database.exists()


def test_wrong_bootstrap_and_wrong_password_fail_closed(tmp_path: Path) -> None:
    client, _ = cloud_client(tmp_path)
    with client:
        denied = client.post(
            "/api/v2/auth/bootstrap-organization",
            json={
                "organizationName": "组织",
                "displayName": "管理员",
                "email": "admin@example.com",
                "password": "12345678",
                "bootstrapToken": "wrong",
            },
        )
        assert denied.status_code == 403
        bootstrap(client)
        wrong = client.post(
            "/api/v2/auth/login",
            json={"identifier": "admin@example.com", "password": "wrong"},
        )
        assert wrong.status_code == 401


def test_strict_task_create_update_complete_and_restore(tmp_path: Path) -> None:
    client, _ = cloud_client(tmp_path)
    with client:
        admin = bootstrap(client)
        headers = {"Authorization": f"Bearer {admin['accessToken']}"}
        snapshot = client.get("/api/v2/business/snapshot", headers=headers)
        assert snapshot.status_code == 200, snapshot.text
        project_id = snapshot.json()["projects"][0]["projectId"]

        event_line = client.post(
            "/api/v2/event-lines",
            headers={**headers, "Idempotency-Key": "event-line-create-1"},
            json={
                "projectId": project_id,
                "name": "严格事件线",
                "goal": "验证严格新版事件线命令",
            },
        )
        assert event_line.status_code == 201, event_line.text
        assert event_line.json()["eventLine"]["projectId"] == project_id

        created = client.post(
            "/api/v2/tasks",
            headers={**headers, "Idempotency-Key": "task-create-1"},
            json={
                "title": "严格任务",
                "description": "只写严格任务表",
                "projectId": project_id,
                "scheduledStartAt": "2026-07-29T19:00",
                "scheduledEndAt": "2026-07-29T20:00",
            },
        )
        assert created.status_code == 201, created.text
        repeated = client.post(
            "/api/v2/tasks",
            headers={**headers, "Idempotency-Key": "task-create-1"},
            json={
                "title": "严格任务",
                "description": "只写严格任务表",
                "projectId": project_id,
                "scheduledStartAt": "2026-07-29T19:00",
                "scheduledEndAt": "2026-07-29T20:00",
            },
        )
        assert repeated.status_code == 201
        assert repeated.json() == created.json()

        task = created.json()["task"]
        task_id = task["taskId"]
        updated = client.patch(
            f"/api/v2/tasks/{task_id}",
            headers={**headers, "Idempotency-Key": "task-update-1"},
            json={
                "expectedVersion": task["version"],
                "title": "严格任务已更新",
                "scheduledStartAt": "2026-07-30T09:00",
                "scheduledEndAt": "2026-07-30T10:30",
                "durationMinutes": 90,
                "ownerMembershipId": admin["membershipId"],
                "collaboratorMembershipIds": [],
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["task"]["title"] == "严格任务已更新"
        assert updated.json()["task"]["durationMinutes"] == 90
        assert updated.json()["task"]["collaborators"][0]["membershipId"] == (
            admin["membershipId"]
        )
        assert updated.json()["task"]["collaborators"][0]["role"] == "owner"
        assert updated.json()["task"]["collaborators"][0]["inboxState"] == (
            "accepted"
        )

        completed = client.post(
            f"/api/v2/tasks/{task_id}/complete",
            headers={**headers, "Idempotency-Key": "task-complete-1"},
            json={"expectedVersion": updated.json()["task"]["version"]},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["task"]["lifecycleState"] == "completed"

        restored = client.post(
            f"/api/v2/tasks/{task_id}/restore",
            headers={**headers, "Idempotency-Key": "task-restore-1"},
            json={"expectedVersion": completed.json()["task"]["version"]},
        )
        assert restored.status_code == 200, restored.text
        assert restored.json()["task"]["lifecycleState"] == "todo"

        detail = client.get(f"/api/v2/tasks/{task_id}", headers=headers)
        assert detail.status_code == 200
        assert detail.json()["task"]["version"] == restored.json()["task"]["version"]


def test_migrated_legacy_password_upgrades_after_login(tmp_path: Path) -> None:
    client, database = cloud_client(tmp_path)
    with client:
        admin = bootstrap(client)
        principal_id = admin["principalId"]
        legacy_hash = pbkdf2_sha256.hash("12345678")
        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                UPDATE identity_credentials
                SET secret_hash = ?, hash_scheme = ?, version = version + 1
                WHERE principal_id = ?
                """,
                (legacy_hash, LEGACY_PASSWORD_SCHEME, principal_id),
            )
            connection.commit()

        login = client.post(
            "/api/v2/auth/login",
            json={"identifier": "admin@example.com", "password": "12345678"},
        )
        assert login.status_code == 200, login.text

        with runtime_connection(database, "cloud", read_only=True) as connection:
            credential = connection.execute(
                """
                SELECT secret_hash, hash_scheme
                FROM identity_credentials
                WHERE principal_id = ?
                """,
                (principal_id,),
            ).fetchone()
        assert credential is not None
        assert credential["hash_scheme"] == PASSWORD_SCHEME
        assert credential["secret_hash"] != legacy_hash

        phone_login = client.post(
            "/api/v2/auth/login",
            json={"identifier": "13800138000", "password": "12345678"},
        )
        assert phone_login.status_code == 200, phone_login.text


def test_project_knowledge_context_only_returns_published_summaries(
    tmp_path: Path,
) -> None:
    client, database = cloud_client(tmp_path)
    with client:
        admin = bootstrap(client)
        headers = {"Authorization": f"Bearer {admin['accessToken']}"}
        session = client.get("/api/v2/session/current", headers=headers).json()
        snapshot = client.get("/api/v2/business/snapshot", headers=headers).json()
        project = snapshot["projects"][0]
        organization_id = session["organizationId"]
        membership_id = session["membershipId"]
        project_id = project["projectId"]
        now = "2026-07-30T08:00:00Z"

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                INSERT INTO knowledge_documents (
                  document_id, organization_id, project_id,
                  project_assignment_state, source_asset_id,
                  owner_membership_id, department_id, title, document_kind,
                  visibility_scope, parse_state, lifecycle_state,
                  current_version, version, created_at, updated_at
                ) VALUES (?, ?, ?, 'assigned', NULL, ?, NULL, ?, ?,
                          ?, 'ready', 'active', 1, 1, ?, ?)
                """,
                (
                    "doc_shared_summary",
                    organization_id,
                    project_id,
                    membership_id,
                    "已发布项目摘要",
                    "shared_summary",
                    "organization",
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
                ) VALUES (?, ?, ?, 1, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    "docver_shared_summary",
                    organization_id,
                    "doc_shared_summary",
                    "hash-shared",
                    "这是允许进入项目上下文的组织共享摘要。",
                    "RAW_SHARED_BODY_MUST_NOT_LEAVE_CLOUD",
                    "test",
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
                ) VALUES (?, ?, ?, 'assigned', NULL, ?, NULL, ?, ?,
                          ?, 'ready', 'active', 1, 1, ?, ?)
                """,
                (
                    "doc_private_raw",
                    organization_id,
                    project_id,
                    membership_id,
                    "成员私有原始资料",
                    "raw_source",
                    "self",
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
                ) VALUES (?, ?, ?, 1, ?, ?, ?, 1, 1, ?, ?)
                """,
                (
                    "docver_private_raw",
                    organization_id,
                    "doc_private_raw",
                    "hash-private",
                    "PRIVATE_PREVIEW_MUST_NOT_LEAVE_CLOUD",
                    "PRIVATE_BODY_MUST_NOT_LEAVE_CLOUD",
                    "test",
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
                    "narrative_shared",
                    organization_id,
                    project_id,
                    "日慈项目叙事",
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
                ) VALUES (?, ?, ?, 1, ?, ?, '', ?, ?, ?, ?)
                """,
                (
                    "narrativever_shared",
                    organization_id,
                    "narrative_shared",
                    "这是已经人工保存、允许进入项目上下文的组织共享项目叙事。",
                    '{"secret":"NARRATIVE_JSON_MUST_NOT_LEAVE_CLOUD"}',
                    "hash-narrative",
                    "这是允许共享的项目叙事变更摘要。",
                    membership_id,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO source_assets (
                  source_asset_id, organization_id, project_id,
                  storage_object_id, file_name, media_type, byte_size,
                  content_hash, source_kind, source_locator, lifecycle_state,
                  created_by_membership_id, version, created_at, updated_at
                ) VALUES (?, ?, ?, NULL, ?, 'application/pdf', 123, ?,
                          'uploaded_file', ?, 'active', ?, 1, ?, ?)
                """,
                (
                    "source_asset_shared",
                    organization_id,
                    project_id,
                    "组织共享证据.pdf",
                    "hash-source-asset",
                    "/member/private/RAW_SOURCE_LOCATOR_MUST_NOT_LEAVE_CLOUD.pdf",
                    membership_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO evidence_links (
                  evidence_link_id, organization_id, source_type, source_id,
                  target_type, target_id, relation_kind, lifecycle_state,
                  linked_by_membership_id, version, created_at, updated_at
                ) VALUES (?, ?, 'document_version', ?,
                          'narrative_output', ?, 'supports', 'active',
                          ?, 1, ?, ?)
                """,
                (
                    "evidence_shared",
                    organization_id,
                    "docver_shared_summary",
                    "narrative_shared",
                    membership_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO evidence_links (
                  evidence_link_id, organization_id, source_type, source_id,
                  target_type, target_id, relation_kind, lifecycle_state,
                  linked_by_membership_id, version, created_at, updated_at
                ) VALUES (?, ?, 'source_asset', ?,
                          'narrative_output', ?, 'supports', 'active',
                          ?, 1, ?, ?)
                """,
                (
                    "evidence_source_asset",
                    organization_id,
                    "source_asset_shared",
                    "narrative_shared",
                    membership_id,
                    now,
                    now,
                ),
            )
            connection.commit()

        response = client.get(
            f"/api/v2/projects/{project_id}/knowledge-context",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        serialized = json.dumps(payload, ensure_ascii=False)

        assert payload["organizationId"] == organization_id
        assert payload["cloudInstanceId"] == admin["cloudInstanceId"]
        assert payload["project"]["projectId"] == project_id
        assert payload["state"]["organizationShared"] == "ready"
        assert {
            item["sourceType"] for item in payload["organizationSharedKnowledge"]
        } == {
            "knowledge_summary",
            "narrative_summary",
            "evidence_relationship",
        }
        narrative_item = next(
            item
            for item in payload["organizationSharedKnowledge"]
            if item["sourceType"] == "narrative_summary"
        )
        assert narrative_item["summary"] == (
            "这是已经人工保存、允许进入项目上下文的组织共享项目叙事。"
        )
        assert payload["materialBoundary"] == {
            "sourceFileContentIncluded": False,
            "sourceFilePathsIncluded": False,
            "storageLocatorsIncluded": False,
            "unpublishedDocumentContentIncluded": False,
        }
        for forbidden in (
            "RAW_SHARED_BODY_MUST_NOT_LEAVE_CLOUD",
            "PRIVATE_PREVIEW_MUST_NOT_LEAVE_CLOUD",
            "PRIVATE_BODY_MUST_NOT_LEAVE_CLOUD",
            "NARRATIVE_JSON_MUST_NOT_LEAVE_CLOUD",
            "RAW_SOURCE_LOCATOR_MUST_NOT_LEAVE_CLOUD",
            "sourceLocator",
            "storageKey",
        ):
            assert forbidden not in serialized


def test_member_without_department_can_read_visible_project_context(
    tmp_path: Path,
) -> None:
    client, database = cloud_client(tmp_path)
    with client:
        admin = bootstrap(client)
        admin_header = {"Authorization": f"Bearer {admin['accessToken']}"}
        snapshot = client.get(
            "/api/v2/business/snapshot",
            headers=admin_header,
        ).json()
        project_id = snapshot["projects"][0]["projectId"]

        department = client.post(
            "/api/v2/organization/departments",
            headers={**admin_header, "Idempotency-Key": "no-department-create"},
            json={"name": "临时加入部门", "expectedOrganizationVersion": 1},
        )
        assert department.status_code == 201, department.text
        invite = client.post(
            "/api/v2/organization/invites",
            headers=admin_header,
            json={
                "inviteKind": "department",
                "targetId": department.json()["id"],
            },
        )
        assert invite.status_code == 201, invite.text
        member = client.post(
            "/api/v2/auth/join",
            json={
                "inviteCode": invite.json()["inviteCode"],
                "displayName": "无部门成员",
                "email": "member-without-department@example.com",
                "password": "abcdefgh",
            },
        )
        assert member.status_code == 201, member.text
        member_header = {
            "Authorization": f"Bearer {member.json()['accessToken']}"
        }

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                DELETE FROM department_memberships
                WHERE organization_id = ? AND membership_id = ?
                """,
                (admin["organizationId"], member.json()["membershipId"]),
            )
            connection.commit()

        context = client.get(
            f"/api/v2/projects/{project_id}/knowledge-context",
            headers=member_header,
        )
        assert context.status_code == 200, context.text
        assert context.json()["project"]["projectId"] == project_id
