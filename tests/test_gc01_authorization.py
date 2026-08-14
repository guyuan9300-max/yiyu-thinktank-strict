from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from cloud_backend.app.config import CloudConfig
from cloud_backend.app.repositories.gc01_authorization import (
    backfill_authorization_projections,
)
from cloud_backend.app.main import create_app
from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import canonical_json, sha256_text, utc_now
from strict_common.physical_schema import normalized_structure, structure_sha256
from strict_common.schema import initialize_database, runtime_connection
from strict_common.security import PASSWORD_SCHEME, hash_password, hash_token


def _future(hours: int) -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(hours=hours))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _seed_gc01_cloud(database: Path) -> tuple[CloudConfig, dict[str, str]]:
    identity = initialize_database(database, "cloud")
    now = utc_now()
    instance_id = "cli_gc01_test"
    organization_id = "org_gc01_test"
    scope_id = "scope_gc01_test"
    tokens = {"admin": "gc01-admin-token", "member": "gc01-member-token"}
    config = CloudConfig(
        data_dir=database.parent,
        database_path=database,
        bootstrap_token="unused-in-gc01",
        master_key=Fernet.generate_key().decode(),
        cloud_instance_id=instance_id,
    )
    with runtime_connection(database, "cloud") as connection:
        connection.execute(
            """
            INSERT INTO state_registry (
                id, state_id, target_blueprint_node, version, record_kind,
                lifecycle_state, created_at, updated_at, deleted_at
            ) VALUES (?, ?, 'cloud_instance', 1, 'cloud_instance', 'active',
                      ?, ?, NULL)
            """,
            (instance_id, instance_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO organizations (
                id, lifecycle_state, version, updated_at, record_kind, name,
                created_at, deleted_at
            ) VALUES (?, 'active', 1, ?, 'organization', 'GC01测试组织', ?, NULL)
            """,
            (organization_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO organizations (
                id, lifecycle_state, version, updated_at, record_kind,
                parent_record_id, name, created_at, deleted_at
            ) VALUES ('department_gc01', 'active', 1, ?, 'department', ?,
                      '技术测试部', ?, NULL)
            """,
            (now, organization_id, now),
        )
        connection.execute(
            """
            INSERT INTO authorization_scopes (
                id, scope_kind, organization_id, policy_version, created_at,
                updated_at, status, version, lifecycle_state, deleted_at
            ) VALUES (?, 'organization', ?, 1, ?, ?, 'active', 1, 'active', NULL)
            """,
            (scope_id, organization_id, now, now),
        )
        for role_key in ("admin", "member"):
            principal_id = f"principal_{role_key}"
            membership_id = f"membership_{role_key}"
            connection.execute(
                """
                INSERT INTO principals (
                    id, status, identity_version, updated_at, principal_kind,
                    display_name, version, lifecycle_state, created_at,
                    deleted_at
                ) VALUES (?, 'active', 1, ?, 'person', ?, 1, 'active', ?, NULL)
                """,
                (principal_id, now, role_key, now),
            )
            connection.execute(
                """
                INSERT INTO organization_memberships (
                    id, scope_id, principal_id, role_key, status, version,
                    record_kind, visibility_scope, lifecycle_state, created_at,
                    updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, 'active', 1, 'membership', ?, 'active',
                          ?, ?, NULL)
                """,
                (
                    membership_id,
                    scope_id,
                    principal_id,
                    role_key,
                    "organization" if role_key == "admin" else "self",
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO sandboxes (
                    id, scope_id, principal_id, membership_id,
                    access_secret_hash, access_expires_at, last_seen_at,
                    record_kind, cloud_instance_id, database_generation_id,
                    sandbox_kind, runtime_status, contract_version,
                    manifest_hash, lease_expires_at, last_verified_at, version,
                    lifecycle_state, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id, source_version,
                    projection_state, projected_at, stale_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'server_session', ?, ?,
                          'organization', 'active', ?, ?, ?, ?, 1, 'active',
                          ?, ?, NULL, 'cloud', ?, 1, 'authoritative', ?, NULL)
                """,
                (
                    f"session_{role_key}",
                    scope_id,
                    principal_id,
                    membership_id,
                    hash_token(tokens[role_key]),
                    _future(2),
                    now,
                    instance_id,
                    identity.database_generation_id,
                    identity.contract_version,
                    identity.manifest_hash,
                    _future(24),
                    now,
                    now,
                    now,
                    instance_id,
                    now,
                ),
            )
        connection.execute(
            """
            INSERT INTO organization_memberships (
                id, scope_id, principal_id, role_key, status, version,
                record_kind, parent_membership_id, department_id,
                lifecycle_state, created_at, updated_at, deleted_at
            ) VALUES ('assignment_admin_department', ?, NULL,
                      'department_lead', 'active', 1,
                      'department_assignment', 'membership_admin',
                      'department_gc01', 'active', ?, ?, NULL)
            """,
            (scope_id, now, now),
        )
        password_file = database.parent / "admin-password.json"
        password_file.write_text(
            json.dumps(
                {
                    "hashScheme": PASSWORD_SCHEME,
                    "secretHash": hash_password("gc01-admin-password"),
                }
            ),
            encoding="utf-8",
        )
        connection.execute(
            """
            INSERT INTO principals (
                id, status, identity_version, updated_at, principal_kind,
                parent_principal_id, contact_type, normalized_contact,
                verification_state, version, lifecycle_state, created_at,
                deleted_at
            ) VALUES ('contact_admin', 'active', 1, ?, 'contact',
                      'principal_admin', 'email', 'gc01-admin@example.com',
                      'verified', 1, 'active', ?, NULL)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO principals (
                id, status, identity_version, updated_at, principal_kind,
                parent_principal_id, contact_type, normalized_contact,
                verification_state, version, lifecycle_state, created_at,
                deleted_at
            ) VALUES ('contact_admin_phone', 'active', 1, ?, 'contact',
                      'principal_admin', 'phone', '+8613812345678',
                      'verified', 1, 'active', ?, NULL)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO principals (
                id, status, identity_version, updated_at, principal_kind,
                parent_principal_id, credential_type, secret_reference,
                credential_state, version, lifecycle_state, created_at,
                deleted_at
            ) VALUES ('credential_admin', 'active', 1, ?, 'credential',
                      'principal_admin', 'password', ?, 'active', 1,
                      'active', ?, NULL)
            """,
            (now, str(password_file), now),
        )
        connection.commit()
    return config, tokens


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in tables
    }


def test_gc01_policy_and_viewer_projection_are_explicit_and_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    with runtime_connection(database, "cloud") as connection:
        structure_before = structure_sha256(normalized_structure(connection))
        counts_before = _table_counts(connection)

    with TestClient(create_app(config)) as client:
        missing = client.get(
            "/api/v2/authorization/current",
            headers=_auth(tokens["admin"]),
        )
        assert missing.status_code == 503
        assert missing.json()["error"]["code"] == "authorization_projection_missing"

        with runtime_connection(database, "cloud") as connection:
            first = backfill_authorization_projections(
                connection,
                origin_instance_id=config.cloud_instance_id or "",
            )
            second = backfill_authorization_projections(
                connection,
                origin_instance_id=config.cloud_instance_id or "",
            )
        assert first == {
            "scopes": 1,
            "activeMemberships": 2,
            "policiesCreated": 2,
            "projectionsCreated": 2,
            "projectionsRenewed": 0,
            "projectionsInvalidated": 0,
            "reconciliationRunsCreated": 1,
        }
        assert second == {
            "scopes": 1,
            "activeMemberships": 2,
            "policiesCreated": 0,
            "projectionsCreated": 0,
            "projectionsRenewed": 0,
            "projectionsInvalidated": 0,
            "reconciliationRunsCreated": 0,
        }

        admin = client.get(
            "/api/v2/authorization/current",
            headers=_auth(tokens["admin"]),
        )
        member = client.get(
            "/api/v2/authorization/current",
            headers=_auth(tokens["member"]),
        )
        assert admin.status_code == 200, admin.text
        assert member.status_code == 200, member.text
        assert admin.json()["state"] == "ready"
        assert admin.json()["freshness"] == "current"
        assert admin.json()["capabilities"] == [
            "organization.read",
            "organization.manage",
            "authorization.manage",
            "organization_ai.manage",
        ]
        assert member.json()["capabilities"] == ["organization.read"]
        assert "organization_administration" in admin.json()["surfaces"]
        assert "organization_administration" not in member.json()["surfaces"]

    with runtime_connection(database, "cloud") as connection:
        structure_after = structure_sha256(normalized_structure(connection))
        counts_after = _table_counts(connection)
        changed_tables = {
            table
            for table in counts_before
            if counts_before[table] != counts_after[table]
        }
        assert structure_after == structure_before
        assert changed_tables == {
            "policy_versions",
            "viewer_projections",
            "reconciliation_runs",
        }
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc01_role_change_invalidates_old_projection(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    with runtime_connection(database, "cloud") as connection:
        backfill_authorization_projections(
            connection,
            origin_instance_id=config.cloud_instance_id or "",
        )
        member_projection_id = str(
            connection.execute(
                "SELECT id FROM viewer_projections "
                "WHERE viewer_membership_id='membership_member' "
                "AND invalidated_at IS NULL"
            ).fetchone()[0]
        )
        connection.execute(
            "UPDATE organization_memberships SET role_key='admin', version=2, "
            "updated_at=? WHERE id='membership_member' AND version=1",
            (utc_now(),),
        )
        connection.commit()
        result = backfill_authorization_projections(
            connection,
            origin_instance_id=config.cloud_instance_id or "",
        )
        old_projection = connection.execute(
            "SELECT invalidated_at FROM viewer_projections WHERE id=?",
            (member_projection_id,),
        ).fetchone()
    assert result["policiesCreated"] == 0
    assert result["projectionsCreated"] == 1
    assert result["projectionsInvalidated"] == 1
    assert old_projection is not None and old_projection["invalidated_at"] is not None

    with TestClient(create_app(config)) as client:
        updated = client.get(
            "/api/v2/authorization/current",
            headers=_auth(tokens["member"]),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["systemRole"] == "admin"
        assert updated.json()["sourceVersion"] == 2
        assert "authorization.manage" in updated.json()["capabilities"]


def test_project_share_is_the_single_gate_for_metadata_and_shared_knowledge(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "project-share-create",
            },
            json={
                "name": "共享知识项目",
                "participantMembershipIds": [],
            },
        )
        assert created.status_code == 201, created.text
        project = created.json()["project"]
        project_id = project["projectId"]

        hidden = client.get(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers=_auth(tokens["member"]),
        )
        assert hidden.status_code == 404
        hidden_context = client.get(
            f"/api/v2/projects/{project_id}/knowledge-context",
            headers=_auth(tokens["member"]),
        )
        assert hidden_context.status_code == 404

        shared = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "project-share-member",
            },
            json={
                "participantMembershipIds": ["membership_member"],
                "expectedVersion": project["version"],
            },
        )
        assert shared.status_code == 200, shared.text
        assert "membership_member" in shared.json()["project"]["participantMembershipIds"]

        # Seed only safe organization knowledge plus the two builtin Agent
        # identities needed to exercise the recipient's real read/write path.
        # The local-document source below is deliberately a receipt ID/hash;
        # no file body, path or storage locator enters the cloud database.
        workspace_bot_id = builtin_agent_id("org_gc01_test", "project_workspace")
        strategy_bot_id = builtin_agent_id("org_gc01_test", "strategy_companion")
        intelligence_bot_id = builtin_agent_id("org_gc01_test", "intelligence_research")
        provider_id = "provider_gc02_shared"
        shared_document_id = "knowledge_gc02_shared"
        shared_manifest_id = "manifest_gc02_shared"
        shared_version_id = "version_gc02_shared"
        shared_summary = "组织共享知识哨兵：该项目服务儿童社会情感学习。"
        shared_receipt = canonical_json(
            {
                "schema": "yiyu.project-knowledge-summary.v1",
                "summary": shared_summary,
            }
        )
        shared_hash = sha256_text(shared_summary)
        with runtime_connection(database, "cloud") as connection:
            now = utc_now()
            for bot_id, agent_kind, handle in (
                (workspace_bot_id, "project_workspace", "project-workspace"),
                (strategy_bot_id, "strategy_companion", "strategy-companion"),
                (intelligence_bot_id, "intelligence_research", "intelligence-research"),
            ):
                connection.execute(
                    "INSERT INTO secured_resources (id, scope_id, resource_kind, "
                    "lifecycle_state, version, resource_type_key, created_at, "
                    "updated_at, deleted_at, authority_role, origin_instance_id) "
                    "VALUES (?, 'scope_gc01_test', 'bot_definition', 'active', 1, "
                    "'builtin_function_agent', ?, ?, NULL, 'cloud', ?)",
                    (bot_id, now, now, config.cloud_instance_id),
                )
                connection.execute(
                    "INSERT INTO bot_definitions (id, scope_id, agent_kind, version, "
                    "handle, description, capability_policy_version, enabled, "
                    "lifecycle_state, created_at, updated_at, deleted_at) "
                    "VALUES (?, 'scope_gc01_test', ?, 1, ?, 'GC02共享协作测试', "
                    "'builtin-agent-contract-v1', 1, 'active', ?, ?, NULL)",
                    (bot_id, agent_kind, handle, now, now),
                )
            connection.execute(
                "INSERT INTO provider_resources (id, scope_id, provider, resource_kind, "
                "remote_id, retention_state, owner_kind, display_name, endpoint, "
                "model_name, secret_fingerprint, status, verified_at, version, "
                "lifecycle_state, created_at, updated_at, deleted_at, authority_role, "
                "origin_instance_id) VALUES (?, 'scope_gc01_test', 'doubao', "
                "'organization_ai_configuration', ?, 'organization_managed', "
                "'organization', '组织大模型', 'https://example.invalid/api/v3', "
                "'model-gc02-shared', 'fingerprint', 'ready', ?, 1, 'active', ?, ?, "
                "NULL, 'cloud', ?)",
                (provider_id, provider_id, now, now, now, config.cloud_instance_id),
            )
            connection.execute(
                "INSERT INTO secured_resources (id, scope_id, resource_kind, "
                "lifecycle_state, version, resource_type_key, created_at, updated_at, "
                "deleted_at, authority_role, origin_instance_id) VALUES "
                "(?, 'scope_gc01_test', 'knowledge_document', 'active', 1, "
                "'organization_knowledge', ?, ?, NULL, 'cloud', ?)",
                (shared_document_id, now, now, config.cloud_instance_id),
            )
            connection.execute(
                "INSERT INTO object_manifests (id, scope_id, storage_key, content_hash, "
                "lifecycle_state, receipt, holder_role, holder_instance_id, storage_kind, "
                "byte_size, media_type, availability_state, receipt_hash, created_at, "
                "verified_at, deleted_at, authority_role, origin_instance_id) VALUES "
                "(?, 'scope_gc01_test', NULL, ?, 'active', ?, 'organization_cloud', ?, "
                "'metadata_receipt', ?, 'application/json', 'ready', ?, ?, ?, NULL, "
                "'cloud', ?)",
                (
                    shared_manifest_id,
                    shared_hash,
                    shared_receipt,
                    config.cloud_instance_id,
                    len(shared_receipt.encode("utf-8")),
                    sha256_text(shared_receipt),
                    now,
                    now,
                    config.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO knowledge_documents (id, scope_id, source_asset_id, "
                "client_id, current_version, owner_membership_id, title, document_kind, "
                "visibility_scope, parse_state, publication_state, published_at, version, "
                "lifecycle_state, created_at, updated_at, deleted_at) VALUES "
                "(?, 'scope_gc01_test', NULL, ?, 1, 'membership_admin', "
                "'组织共享项目摘要', 'intelligence_summary', 'organization', 'ready', "
                "'published', ?, 1, 'active', ?, ?, NULL)",
                (shared_document_id, project_id, now, now, now),
            )
            connection.execute(
                "INSERT INTO document_versions (id, scope_id, document_id, version, "
                "content_hash, created_at, object_manifest_id, source_asset_version, "
                "publication_state, created_by_membership_id, origin_instance_id, "
                "integrity_hash) VALUES (?, 'scope_gc01_test', ?, 1, ?, ?, ?, NULL, "
                "'published', 'membership_admin', ?, ?)",
                (
                    shared_version_id,
                    shared_document_id,
                    shared_hash,
                    now,
                    shared_manifest_id,
                    config.cloud_instance_id,
                    sha256_text(f"{shared_document_id}|1|{shared_hash}"),
                ),
            )
            connection.commit()

        captured = client.post(
            f"/api/v2/workbench/projects/{project_id}/official-website/captures",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "project-share-official-capture",
            },
            json={
                "pages": [
                    {
                        "title": "共享项目官网",
                        "url": "https://official.example/shared-project",
                        "text": "官网事实哨兵：金老师担任项目理事。",
                        "contentHash": "1" * 64,
                        "capturedAt": "2026-08-06T08:00:00.000Z",
                    }
                ],
                "factCandidates": [
                    {
                        "term": "金老师",
                        "attributeName": "身份",
                        "valueCategory": "text",
                        "valueText": "项目理事",
                        "evidence": "金老师担任项目理事。",
                        "sourceUrl": "https://official.example/shared-project",
                        "sourceTitle": "共享项目官网",
                        "confidence": 0.96,
                    }
                ],
            },
        )
        assert captured.status_code == 200, captured.text
        profile = client.post(
            f"/api/v2/workbench/projects/{project_id}/strategic-profile/rebuild",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "project-share-profile",
            },
            json={
                "profile": {
                    "schema": "yiyu.strategic-client-profile.v2",
                    "generator": "strategy_companion_local_wiki_v1",
                    "modelName": "model-gc02-shared",
                    "dimensions": [
                        {
                            "dimension": dimension,
                            "narrative": f"{label}共享档案哨兵",
                            "references": [
                                {
                                    "sourceType": "local_document",
                                    "sourceId": "owner-local-source-receipt",
                                    "label": "负责人本机资料安全回执",
                                }
                            ],
                        }
                        for dimension, label in (
                            ("essence", "本质"),
                            ("business_intro", "业务"),
                            ("cooperation", "合作"),
                            ("people", "人物"),
                            ("timeline", "时间线"),
                            ("next_steps", "下一步"),
                        )
                    ],
                    "sourceDocuments": [
                        {
                            "sourceObjectId": "owner-local-source-receipt",
                            "sourceObjectKind": "source_asset",
                            "sourceVersion": 1,
                            "contentHash": "2" * 64,
                            "title": "负责人本机资料安全回执",
                        }
                    ],
                }
            },
        )
        assert profile.status_code == 200, profile.text

        member_projects = client.get(
            "/api/v2/domain/project-materials/projects",
            headers=_auth(tokens["member"]),
        )
        assert member_projects.status_code == 200, member_projects.text
        assert project_id in {
            item["projectId"] for item in member_projects.json()["projects"]
        }
        member_detail = client.get(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers=_auth(tokens["member"]),
        )
        owner_narrative = client.get(
            f"/api/v2/workbench/projects/{project_id}/narrative",
            headers=_auth(tokens["admin"]),
        )
        member_narrative = client.get(
            f"/api/v2/workbench/projects/{project_id}/narrative",
            headers=_auth(tokens["member"]),
        )
        member_context = client.get(
            f"/api/v2/projects/{project_id}/knowledge-context",
            headers=_auth(tokens["member"]),
        )
        member_official = client.get(
            f"/api/v2/workbench/projects/{project_id}/official-website",
            headers=_auth(tokens["member"]),
        )
        assert member_detail.status_code == 200, member_detail.text
        assert member_detail.json()["project"]["authorizationProjection"][
            "viewerCapabilities"
        ] == ["read", "contributeKnowledge"]
        assert owner_narrative.status_code == member_narrative.status_code == 200
        assert owner_narrative.json()["dimensions"] == member_narrative.json()["dimensions"]
        assert {item["dimension"] for item in member_narrative.json()["dimensions"]} == {
            "essence",
            "business_intro",
            "cooperation",
            "people",
            "timeline",
            "next_steps",
        }
        assert member_context.status_code == 200, member_context.text
        assert member_official.status_code == 200, member_official.text
        assert member_official.json()["registeredUrl"] == (
            "https://official.example/shared-project"
        )
        assert any(
            item["summary"] == shared_summary
            for item in member_context.json()["organizationSharedKnowledge"]
        )
        assert any(
            "金老师" in item["summary"]
            for item in member_context.json()["officialWebsiteFacts"]
        )
        assert member_context.json()["materialBoundary"]["sourceFilePathReturned"] is False
        assert member_context.json()["materialBoundary"]["sourceFileContentReturned"] is False
        safe_shared_payload = json.dumps(
            {
                "context": member_context.json(),
                "narrative": member_narrative.json(),
                "official": member_official.json(),
            },
            ensure_ascii=False,
        )
        assert "/Users/" not in safe_shared_payload
        assert "managed/private" not in safe_shared_payload
        assert "storageKey" not in safe_shared_payload
        assert "originalSourcePath" not in safe_shared_payload

        # The recipient can ask with organization-shared knowledge even when
        # this device owns no project file.  The cloud records hashes/source
        # IDs only, then accepts the recipient's formal correction/supplement.
        member_answer_id = "answer_gc02_shared_member"
        member_answer_payload = {
            "answerId": member_answer_id,
            "projectId": project_id,
            "threadId": "thread_gc02_shared_member",
            "questionHash": "3" * 64,
            "answerHash": "4" * 64,
            "sourceSetId": "source_set_gc02_shared_member",
            "contextManifestId": "context_gc02_shared_member",
            "lineageId": "lineage_gc02_shared_member",
            "botId": workspace_bot_id,
            "providerResourceId": provider_id,
            "modelName": "model-gc02-shared",
            "sourceCount": 1,
            "materialAccessMode": "organization_knowledge",
            "boundaryState": "organization_published_context",
            "selectedSources": [
                {
                    "sourceObjectId": shared_document_id,
                    "sourceObjectKind": "organization_knowledge",
                    "sourceVersion": 1,
                    "contentHash": shared_hash,
                }
            ],
            "originInstanceId": "member-device-gc02",
        }
        member_answer = client.post(
            "/api/v2/workbench/answers",
            headers={
                **_auth(tokens["member"]),
                "Idempotency-Key": "project-share-member-answer",
            },
            json=member_answer_payload,
        )
        assert member_answer.status_code == 201, member_answer.text
        assert member_answer.json()["answer"]["sourceCount"] == 1
        correction_statement = "成员协作补充哨兵：项目联系人为王老师。"
        corrected = client.post(
            f"/api/v2/workbench/answers/{member_answer_id}/facts/corrections",
            headers={
                **_auth(tokens["member"]),
                "Idempotency-Key": "project-share-member-answer-correction",
            },
            json={
                "projectId": project_id,
                "correctionKind": "correction",
                "selectedTextHash": sha256_text("待纠错表述"),
                "statement": correction_statement,
                "statementHash": sha256_text(correction_statement),
                "expectedVersion": 0,
                "originInstanceId": "member-device-gc02",
            },
        )
        assert corrected.status_code == 201, corrected.text
        owner_context_after_correction = client.get(
            f"/api/v2/projects/{project_id}/knowledge-context",
            headers=_auth(tokens["admin"]),
        )
        assert correction_statement in json.dumps(
            owner_context_after_correction.json(), ensure_ascii=False
        )

        # A shared participant is a knowledge collaborator, not a project
        # metadata editor.  Seed the existing strategic-profile authority row
        # and prove the member can add a formal clarification consumed by the
        # same project knowledge context.
        with runtime_connection(database, "cloud") as connection:
            now = utc_now()
            connection.execute(
                "INSERT INTO secured_resources (id, scope_id, resource_kind, "
                "lifecycle_state, version, resource_type_key, created_at, "
                "updated_at, deleted_at, authority_role, origin_instance_id) "
                "VALUES ('profile_shared_test', 'scope_gc01_test', 'narrative_output', "
                "'active', 1, 'narrative_output', ?, ?, NULL, 'cloud', ?)",
                (now, now, config.cloud_instance_id),
            )
            connection.execute(
                "INSERT INTO narrative_outputs (id, scope_id, client_id, "
                "current_version, lifecycle_state, title, artifact_kind, "
                "visibility_scope, publication_state, owner_membership_id, "
                "published_at, version, created_at, updated_at, deleted_at, "
                "authority_role, origin_instance_id) VALUES "
                "('profile_shared_test', 'scope_gc01_test', ?, 1, 'active', "
                "'共享客户档案', 'strategic_profile', 'organization', "
                "'published', 'membership_admin', ?, 1, ?, ?, NULL, 'cloud', ?)",
                (project_id, now, now, now, config.cloud_instance_id),
            )
            connection.commit()
        clarified = client.post(
            f"/api/v2/workbench/projects/{project_id}/narrative-clarifications",
            headers={
                **_auth(tokens["member"]),
                "Idempotency-Key": "shared-member-clarification",
            },
            json={
                "dimension": "people",
                "question": "补充关键人物",
                "answer": "共享协作事实哨兵：项目联系人为王老师。",
                "basedOnRev": 1,
            },
        )
        assert clarified.status_code == 200, clarified.text
        shared_context_after_write = client.get(
            f"/api/v2/projects/{project_id}/knowledge-context",
            headers=_auth(tokens["admin"]),
        )
        assert shared_context_after_write.status_code == 200
        assert "共享协作事实哨兵" in json.dumps(
            shared_context_after_write.json(), ensure_ascii=False
        )

        forbidden_metadata_edit = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["member"]),
                "Idempotency-Key": "project-member-metadata-edit",
            },
            json={
                "name": "成员不得改项目元数据",
                "expectedVersion": shared.json()["project"]["version"],
            },
        )
        assert forbidden_metadata_edit.status_code == 403
        forbidden_reshare = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["member"]),
                "Idempotency-Key": "project-member-reshare",
            },
            json={
                "participantMembershipIds": ["membership_admin"],
                "expectedVersion": shared.json()["project"]["version"],
            },
        )
        assert forbidden_reshare.status_code == 403

        revoked = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "project-share-revoke",
            },
            json={
                "participantMembershipIds": [],
                "expectedVersion": shared.json()["project"]["version"],
            },
        )
        assert revoked.status_code == 200, revoked.text
        after_revoke = client.get(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers=_auth(tokens["member"]),
        )
        assert after_revoke.status_code == 404
        for consumer_path in (
            f"/api/v2/projects/{project_id}/knowledge-context",
            f"/api/v2/workbench/projects/{project_id}/narrative",
            f"/api/v2/workbench/projects/{project_id}/official-website",
            f"/api/v2/workbench/projects/{project_id}/reports",
        ):
            blocked_consumer = client.get(
                consumer_path,
                headers=_auth(tokens["member"]),
            )
            assert blocked_consumer.status_code == 404, consumer_path
        replay_after_revoke = client.post(
            "/api/v2/workbench/answers",
            headers={
                **_auth(tokens["member"]),
                "Idempotency-Key": "project-share-member-answer",
            },
            json=member_answer_payload,
        )
        assert replay_after_revoke.status_code == 404

    with runtime_connection(database, "cloud") as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert int(
            connection.execute(
                "SELECT COUNT(*) FROM object_grants WHERE secured_resource_id=? "
                "AND subject_membership_id='membership_member' AND status='active'",
                (project_id,),
            ).fetchone()[0]
        ) == 0


def test_gc01_session_login_refresh_and_logout_are_replayable_and_audited(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, _ = _seed_gc01_cloud(database)
    with runtime_connection(database, "cloud") as connection:
        backfill_authorization_projections(
            connection,
            origin_instance_id=config.cloud_instance_id or "",
        )
        structure_before = structure_sha256(normalized_structure(connection))
        counts_before = _table_counts(connection)

    with TestClient(create_app(config)) as client:
        login_headers = {"Idempotency-Key": "gc01-login-operation"}
        credentials = {
            "identifier": "gc01-admin@example.com",
            "password": "gc01-admin-password",
        }
        first = client.post(
            "/api/v2/auth/login",
            headers=login_headers,
            json=credentials,
        )
        replay = client.post(
            "/api/v2/auth/login",
            headers=login_headers,
            json=credentials,
        )
        assert first.status_code == 200, first.text
        assert replay.status_code == 200, replay.text
        assert replay.json()["sessionId"] == first.json()["sessionId"]
        assert replay.json()["accessToken"] == first.json()["accessToken"]
        assert replay.json()["refreshToken"] == first.json()["refreshToken"]
        assert first.json()["sessionSnapshot"]["authorization"]["state"] == "ready"
        department = first.json()["sessionSnapshot"]["departments"][0]
        assert department["departmentId"] == "department_gc01"
        assert department["members"] == [
            {
                "assignmentId": "assignment_admin_department",
                "membershipId": "membership_admin",
                "roleKey": "department_lead",
                "isDepartmentLead": True,
                "status": "active",
                "version": 1,
            }
        ]

        current = client.get(
            "/api/v2/session/current",
            headers=_auth(first.json()["accessToken"]),
        )
        assert current.status_code == 200, current.text
        assert current.json()["membershipId"] == "membership_admin"

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                "UPDATE viewer_projections SET lease_expires_at=? "
                "WHERE viewer_membership_id='membership_admin' "
                "AND invalidated_at IS NULL",
                (_future(-1),),
            )
            connection.commit()

        still_current = client.get(
            "/api/v2/authorization/current",
            headers=_auth(first.json()["accessToken"]),
        )
        assert still_current.status_code == 200, still_current.text
        assert still_current.json()["state"] == "ready"

        reconnected = client.post(
            "/api/v2/auth/login",
            headers={"Idempotency-Key": "gc01-login-after-expired-lease"},
            json=credentials,
        )
        assert reconnected.status_code == 200, reconnected.text
        renewed_authorization = reconnected.json()["sessionSnapshot"]["authorization"]
        assert renewed_authorization["state"] == "ready"
        assert renewed_authorization["leaseExpiresAt"] > _future(-1)

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                "UPDATE viewer_projections SET lease_expires_at=? "
                "WHERE viewer_membership_id='membership_admin' "
                "AND invalidated_at IS NULL",
                (_future(-1),),
            )
            connection.commit()

        refresh_headers = {"Idempotency-Key": "gc01-refresh-operation"}
        refreshed = client.post(
            "/api/v2/auth/refresh",
            headers=refresh_headers,
            json={"refreshToken": first.json()["refreshToken"]},
        )
        refresh_replay = client.post(
            "/api/v2/auth/refresh",
            headers=refresh_headers,
            json={"refreshToken": first.json()["refreshToken"]},
        )
        assert refreshed.status_code == 200, refreshed.text
        assert refresh_replay.status_code == 200, refresh_replay.text
        assert refresh_replay.json()["accessToken"] == refreshed.json()["accessToken"]
        assert refresh_replay.json()["refreshToken"] == refreshed.json()["refreshToken"]
        assert refreshed.json()["accessToken"] != first.json()["accessToken"]
        conflicting_rotation = client.post(
            "/api/v2/auth/refresh",
            headers=refresh_headers,
            json={"refreshToken": refreshed.json()["refreshToken"]},
        )
        assert conflicting_rotation.status_code == 409, conflicting_rotation.text
        assert conflicting_rotation.json()["error"]["code"] == "idempotency_conflict"
        renewed = client.get(
            "/api/v2/session/current",
            headers=_auth(refreshed.json()["accessToken"]),
        )
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["sessionSnapshot"]["authorization"]["state"] == "ready"
        expired_access = client.get(
            "/api/v2/session/current",
            headers=_auth(first.json()["accessToken"]),
        )
        assert expired_access.status_code == 401

        logout = client.post(
            "/api/v2/auth/logout",
            headers={
                **_auth(refreshed.json()["accessToken"]),
                "Idempotency-Key": "gc01-logout-operation",
            },
        )
        assert logout.status_code == 204, logout.text
        revoked = client.get(
            "/api/v2/session/current",
            headers=_auth(refreshed.json()["accessToken"]),
        )
        assert revoked.status_code == 401

    with runtime_connection(database, "cloud") as connection:
        structure_after = structure_sha256(normalized_structure(connection))
        counts_after = _table_counts(connection)
        changed_tables = {
            table
            for table in counts_before
            if counts_before[table] != counts_after[table]
        }
        assert structure_after == structure_before
        assert changed_tables == {
            "sandboxes",
            "idempotency_records",
            "commands",
            "outbox_events",
            "audit_events",
        }
        assert counts_after["commands"] - counts_before["commands"] == 4
        assert (
            counts_after["idempotency_records"]
            - counts_before["idempotency_records"]
            == 4
        )
        assert counts_after["outbox_events"] - counts_before["outbox_events"] == 4
        assert counts_after["audit_events"] - counts_before["audit_events"] == 4
        session = connection.execute(
            "SELECT runtime_status, lifecycle_state, version, secret_reference "
            "FROM sandboxes WHERE id=?",
            (first.json()["sessionId"],),
        ).fetchone()
        assert session is not None
        assert tuple(session[:3]) == ("revoked", "archived", 3)
        assert session["secret_reference"]
        assert not Path(str(session["secret_reference"])).exists()
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    database_bytes = database.read_bytes()
    for secret in (
        "gc01-admin-password",
        str(first.json()["accessToken"]),
        str(first.json()["refreshToken"]),
        str(reconnected.json()["accessToken"]),
        str(reconnected.json()["refreshToken"]),
        str(refreshed.json()["accessToken"]),
        str(refreshed.json()["refreshToken"]),
    ):
        assert secret.encode() not in database_bytes


def test_gc01_phone_login_accepts_common_mainland_formats_as_one_identifier(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, _ = _seed_gc01_cloud(database)
    with runtime_connection(database, "cloud") as connection:
        backfill_authorization_projections(
            connection,
            origin_instance_id=config.cloud_instance_id or "",
        )
        structure_before = structure_sha256(normalized_structure(connection))

    with TestClient(create_app(config)) as client:
        headers = {"Idempotency-Key": "gc01-phone-login-operation"}
        local_format = client.post(
            "/api/v2/auth/login",
            headers=headers,
            json={
                "identifier": "138 1234 5678",
                "password": "gc01-admin-password",
            },
        )
        country_format = client.post(
            "/api/v2/auth/login",
            headers=headers,
            json={
                "identifier": "+86-138-1234-5678",
                "password": "gc01-admin-password",
            },
        )

        assert local_format.status_code == 200, local_format.text
        assert country_format.status_code == 200, country_format.text
        assert country_format.json()["sessionId"] == local_format.json()["sessionId"]
        assert country_format.json()["accessToken"] == local_format.json()["accessToken"]

    with runtime_connection(database, "cloud") as connection:
        assert structure_sha256(normalized_structure(connection)) == structure_before
        stored = connection.execute(
            "SELECT normalized_contact FROM principals "
            "WHERE id='contact_admin_phone'"
        ).fetchone()
        assert stored is not None and stored[0] == "+8613812345678"
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
