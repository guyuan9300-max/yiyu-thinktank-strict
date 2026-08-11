from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.runtime import LocalRuntimeError
from backend.app.runtime import WorkspaceRuntime
from backend.app.secret_store import MemorySecretStore
from backend.app.ui_domains.project_materials import router as project_materials_router
from backend.app.ui_domains.organization_access import router as organization_access_router
from backend.app.ui_domains.workbench_outputs import router as workbench_outputs_router
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.main import create_app
from scripts.activate_gc02_contract import activate
from scripts.backfill_gc02_project_policies import backfill as backfill_project_policies
from strict_common.physical_schema import normalized_structure, structure_sha256
from strict_common.schema import initialize_database, runtime_connection
from tests.test_gc01_authorization import _auth, _seed_gc01_cloud
from tests.test_gc01_local_login import LoginCloud


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


def test_gc02_registry_uses_only_blueprint_88_tables() -> None:
    registry = json.loads(
        (CONTRACTS / "gc02-registry.v1.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (CONTRACTS / "strict-local-schema-manifest.v1.json").read_text(
            encoding="utf-8"
        )
    )
    allowed = {str(item["name"]) for item in manifest["allowedTables"]}
    assert len(allowed) == 88
    forbidden = {"work_projects", "project_participants", "projection_business_objects"}
    assert registry["goldenChainId"] == "GC-02"
    assert registry["completionState"] == "runtime_verified"
    assert registry["evidenceRef"] == "contracts/GC02_CLIENT_SHARING_CONTRACT_V1.md"
    assert registry["runtimeEvidenceRef"] == "output/gc02-phase11/GC02_RUNTIME_VERIFICATION_20260807.md"
    assert len({item["controlId"] for item in registry["controls"]}) == 9
    assert len({item["queryId"] for item in registry["queries"]}) == 6
    for item in [*registry["controls"], *registry["queries"]]:
        for key in ("localRead", "localWrite", "cloudRead", "cloudWrite"):
            tables = set(item.get(key) or [])
            assert tables <= allowed
            assert not (tables & forbidden)


def test_active_runtime_has_no_pre_blueprint_project_tables() -> None:
    for relative in ("backend", "cloud_backend", "src"):
        for path in (ROOT / relative).rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".ts", ".tsx", ".js"}:
                continue
            source = path.read_text(encoding="utf-8")
            assert "work_projects" not in source, path
            assert "project_participants" not in source, path


def test_gc02_registry_activation_is_idempotent_and_structure_neutral(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    initialize_database(database, "local")
    with sqlite3.connect(database) as connection:
        before = structure_sha256(normalized_structure(connection))
        before_tables = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )

    first = activate(database, "local", "test://gc02-before")
    second = activate(database, "local", "test://gc02-before")
    assert first["tables"] == second["tables"] == before_tables == 88
    assert first["structureHash"] == second["structureHash"] == before
    assert first["quickCheck"] == second["quickCheck"] == "ok"
    assert first["foreignKeyErrors"] == second["foreignKeyErrors"] == 0

    with sqlite3.connect(database) as connection:
        controls = connection.execute(
            "SELECT COUNT(DISTINCT control_id) FROM control_registry "
            "WHERE golden_chain_id='GC-02' AND status='active'"
        ).fetchone()[0]
        queries = connection.execute(
            "SELECT COUNT(DISTINCT query_id) FROM query_registry "
            "WHERE evidence_ref='contracts/GC02_CLIENT_SHARING_CONTRACT_V1.md' "
            "AND status='active'"
        ).fetchone()[0]
        duplicate_ids = connection.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT id FROM control_registry GROUP BY id HAVING COUNT(*) > 1"
            ")"
        ).fetchone()[0]
        assert int(controls) == 9
        assert int(queries) == 6
        assert int(duplicate_ids) == 0


def test_gc02_project_create_edit_cas_and_replay_are_one_authority_transaction(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    tracked = (
        "clients",
        "secured_resources",
        "policy_versions",
        "object_grants",
        "commands",
        "idempotency_records",
        "object_manifests",
        "outbox_events",
        "audit_events",
    )

    def counts() -> dict[str, int]:
        with runtime_connection(database, "cloud") as connection:
            return {
                table: int(
                    connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                )
                for table in tracked
            }

    before = counts()
    with TestClient(create_app(config)) as client:
        create_headers = {
            **_auth(tokens["admin"]),
            "Idempotency-Key": "gc02-project-create",
        }
        create_payload = {
            "name": "GC02项目创建",
            "alias": "GC02",
            "summary": "创建、编辑、CAS和幂等验收",
            "participantMembershipIds": ["membership_member"],
        }
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers=create_headers,
            json=create_payload,
        )
        replayed_create = client.post(
            "/api/v2/domain/project-materials/projects",
            headers=create_headers,
            json=create_payload,
        )
        assert created.status_code == replayed_create.status_code == 201
        assert replayed_create.json() == created.json()
        conflicting_replay = client.post(
            "/api/v2/domain/project-materials/projects",
            headers=create_headers,
            json={**create_payload, "name": "不得复用同一操作标识"},
        )
        assert conflicting_replay.status_code == 409
        assert conflicting_replay.json()["error"]["code"] == "idempotency_conflict"
        project = created.json()["project"]
        project_id = project["projectId"]
        assert project["version"] == 1
        assert project["participantMembershipIds"] == [
            "membership_admin",
            "membership_member",
        ]
        assert project["managerMembershipIds"] == ["membership_admin"]
        assert project["managerNames"] == ["admin"]
        assert project["sharedMemberCount"] == 1
        assert project["authorizationProjection"]["viewerMembershipId"] == (
            "membership_admin"
        )
        assert project["authorizationProjection"]["viewerCapabilities"] == [
            "read",
            "write",
            "contributeKnowledge",
            "manageSharing",
        ]

        after_create = counts()
        assert after_create == {
            **before,
            "clients": before["clients"] + 1,
            "secured_resources": before["secured_resources"] + 1,
            "policy_versions": before["policy_versions"] + 1,
            "object_grants": before["object_grants"] + 2,
            "commands": before["commands"] + 1,
            "idempotency_records": before["idempotency_records"] + 1,
            "object_manifests": before["object_manifests"] + 1,
            "outbox_events": before["outbox_events"] + 1,
            "audit_events": before["audit_events"] + 1,
        }

        with runtime_connection(database, "cloud") as connection:
            policy = connection.execute(
                "SELECT * FROM policy_versions WHERE secured_resource_id=?",
                (project_id,),
            ).fetchone()
            assert policy is not None
            assert policy["policy_scope_kind"] == "secured_resource"
            assert int(policy["version"]) == 1
            assert policy["lifecycle_state"] == "active"
            assert json.loads(policy["policy_spec"])["defaultDecision"] == "deny"
            assert project["authorizationProjection"]["policyVersionId"] == policy["id"]
            grants = connection.execute(
                "SELECT policy_version_id, grant_generation, status "
                "FROM object_grants WHERE secured_resource_id=? ORDER BY id",
                (project_id,),
            ).fetchall()
            assert len(grants) == 2
            assert {row["policy_version_id"] for row in grants} == {policy["id"]}
            assert {int(row["grant_generation"]) for row in grants} == {1}
            assert {row["status"] for row in grants} == {"active"}
            create_command = connection.execute(
                "SELECT expected_aggregate_version, status FROM commands "
                "WHERE aggregate_id=? AND command_type='client.created'",
                (project_id,),
            ).fetchone()
            assert create_command["expected_aggregate_version"] is None
            assert create_command["status"] == "committed"

        update_headers = {
            **_auth(tokens["admin"]),
            "Idempotency-Key": "gc02-project-edit",
        }
        update_payload = {
            "name": "GC02项目编辑完成",
            "summary": "CAS版本2",
            "expectedVersion": 1,
        }
        updated = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers=update_headers,
            json=update_payload,
        )
        replayed_update = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers=update_headers,
            json=update_payload,
        )
        assert updated.status_code == replayed_update.status_code == 200
        assert replayed_update.json() == updated.json()
        assert updated.json()["project"]["version"] == 2
        assert updated.json()["project"]["name"] == "GC02项目编辑完成"

        after_update = counts()
        assert after_update == {
            **after_create,
            "commands": after_create["commands"] + 1,
            "idempotency_records": after_create["idempotency_records"] + 1,
            "object_manifests": after_create["object_manifests"] + 1,
            "outbox_events": after_create["outbox_events"] + 1,
            "audit_events": after_create["audit_events"] + 1,
        }
        with runtime_connection(database, "cloud") as connection:
            edit_command = connection.execute(
                "SELECT expected_aggregate_version, status FROM commands "
                "WHERE aggregate_id=? AND command_type='client.updated'",
                (project_id,),
            ).fetchone()
            assert int(edit_command["expected_aggregate_version"]) == 1
            assert edit_command["status"] == "committed"
            assert int(
                connection.execute(
                    "SELECT COUNT(*) FROM policy_versions "
                    "WHERE secured_resource_id=? AND lifecycle_state='active'",
                    (project_id,),
                ).fetchone()[0]
            ) == 1
            assert int(
                connection.execute(
                    "SELECT COUNT(*) FROM object_grants "
                    "WHERE secured_resource_id=? AND status='active'",
                    (project_id,),
                ).fetchone()[0]
            ) == 2

        stale = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-project-edit-stale",
            },
            json={"name": "不得覆盖", "expectedVersion": 1},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "project_version_conflict"
        assert counts() == after_update

    with runtime_connection(database, "cloud") as connection:
        assert int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        ) == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc02_project_sharing_mutates_only_membership_delta(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)

    def grant_snapshot(project_id: str) -> list[tuple[object, ...]]:
        with runtime_connection(database, "cloud") as connection:
            return [
                tuple(row)
                for row in connection.execute(
                    "SELECT id, subject_membership_id, policy_version_id, "
                    "grant_generation, status, version, capability_set "
                    "FROM object_grants WHERE secured_resource_id=? "
                    "ORDER BY subject_membership_id, grant_generation, id",
                    (project_id,),
                ).fetchall()
            ]

    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-delta-create",
            },
            json={"name": "差量分享项目"},
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["project"]["projectId"]
        owner_before = grant_snapshot(project_id)
        assert len(owner_before) == 1
        assert owner_before[0][1] == "membership_admin"
        assert owner_before[0][2]

        added = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-delta-add",
            },
            json={
                "participantMembershipIds": ["membership_member"],
                "expectedVersion": 1,
            },
        )
        assert added.status_code == 200, added.text
        assert added.json()["project"]["participantMembershipIds"] == [
            "membership_admin",
            "membership_member",
        ]
        after_add = grant_snapshot(project_id)
        assert len(after_add) == 2
        owner_after_add = next(
            row for row in after_add if row[1] == "membership_admin"
        )
        member_after_add = next(
            row for row in after_add if row[1] == "membership_member"
        )
        assert owner_after_add == owner_before[0]
        assert member_after_add[2] == owner_before[0][2]
        assert member_after_add[3:6] == (1, "active", 1)
        assert json.loads(str(member_after_add[6])) == {
            "contributeKnowledge": True,
            "manageSharing": False,
            "read": True,
            "write": False,
        }

        unchanged = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-delta-unchanged",
            },
            json={
                "participantMembershipIds": ["membership_member"],
                "expectedVersion": 2,
            },
        )
        assert unchanged.status_code == 200, unchanged.text
        assert grant_snapshot(project_id) == after_add

        removed = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-delta-remove",
            },
            json={"participantMembershipIds": [], "expectedVersion": 3},
        )
        assert removed.status_code == 200, removed.text
        assert removed.json()["project"]["participantMembershipIds"] == [
            "membership_admin"
        ]
        after_remove = grant_snapshot(project_id)
        owner_after_remove = next(
            row for row in after_remove if row[1] == "membership_admin"
        )
        member_after_remove = next(
            row for row in after_remove if row[1] == "membership_member"
        )
        # 撤权形成新的正式 policy 版本；未变化的负责人 grant 不重建，
        # 只保留原 id/代际/能力并改绑新 policy。
        assert owner_after_remove[0] == owner_before[0][0]
        assert owner_after_remove[1] == owner_before[0][1]
        assert owner_after_remove[2] != owner_before[0][2]
        assert owner_after_remove[3:5] == (1, "active")
        assert owner_after_remove[5] == 2
        assert owner_after_remove[6] == owner_before[0][6]
        assert member_after_remove[3:6] == (1, "revoked", 2)

        replayed_remove = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-delta-remove",
            },
            json={"participantMembershipIds": [], "expectedVersion": 3},
        )
        assert replayed_remove.status_code == 200
        assert replayed_remove.json() == removed.json()
        assert grant_snapshot(project_id) == after_remove

        readded = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-delta-readd",
            },
            json={
                "participantMembershipIds": ["membership_member"],
                "expectedVersion": 4,
            },
        )
        assert readded.status_code == 200, readded.text
        final_grants = grant_snapshot(project_id)
        owner_final = [
            row for row in final_grants if row[1] == "membership_admin"
        ]
        member_final = [
            row for row in final_grants if row[1] == "membership_member"
        ]
        assert owner_final == [owner_after_remove]
        assert [(row[3], row[4]) for row in member_final] == [
            (1, "revoked"),
            (2, "active"),
        ]
        assert member_final[0][2] == owner_before[0][2]
        assert member_final[1][2] == owner_after_remove[2]

    with runtime_connection(database, "cloud") as connection:
        assert int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        ) == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc02_revocation_rotates_policy_and_invalidates_member_derivatives(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-revoke-create",
            },
            json={
                "name": "撤权传播项目",
                "participantMembershipIds": ["membership_member"],
            },
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["project"]["projectId"]

        with runtime_connection(database, "cloud") as connection:
            old_policy = connection.execute(
                "SELECT id FROM policy_versions WHERE secured_resource_id=? "
                "AND lifecycle_state='active'",
                (project_id,),
            ).fetchone()
            member_grant = connection.execute(
                "SELECT id,grant_generation FROM object_grants "
                "WHERE secured_resource_id=? AND subject_membership_id='membership_member' "
                "AND status='active'",
                (project_id,),
            ).fetchone()
            assert old_policy is not None and member_grant is not None
            connection.execute(
                "INSERT INTO viewer_projections (id,scope_id,secured_resource_id,"
                "viewer_principal_id,viewer_membership_id,policy_version_id,"
                "viewer_surfaces,viewer_capabilities,viewer_surfaces_schema_version,"
                "viewer_capabilities_schema_version,lease_expires_at,generated_at,"
                "source_version,invalidated_at) VALUES "
                "('viewer_member_project','scope_gc01_test',?,'principal_member',"
                "'membership_member',?,'[\"project_workspace\"]','[\"read\"]',"
                "'1','1','9999-12-31T23:59:59.999Z',?,1,NULL)",
                (project_id, old_policy["id"], now),
            )
            connection.execute(
                "INSERT INTO source_sets (id,scope_id,client_id,source_count,version,"
                "purpose_kind,publication_state,created_by_principal_id,created_at,"
                "lifecycle_state,updated_at,authority_role,origin_instance_id) VALUES "
                "('source_set_member_project','scope_gc01_test',?,1,1,'project_knowledge',"
                "'published','principal_member',?,'active',?,'cloud','instance_gc01_test')",
                (project_id, now, now),
            )
            connection.execute(
                "INSERT INTO derivation_lineage (id,scope_id,source_set_id,"
                "policy_version_id,grant_generation,derivative_kind,"
                "derivative_object_id,generator_version,generated_at,invalidated_at,"
                "source_version,authority_role,origin_instance_id) VALUES "
                "('lineage_member_project','scope_gc01_test','source_set_member_project',"
                "?,?,'viewer_projection','viewer_member_project','gc02-test',?,NULL,1,"
                "'cloud','instance_gc01_test')",
                (old_policy["id"], int(member_grant["grant_generation"]), now),
            )
            connection.execute(
                "INSERT INTO search_index_manifests (id,scope_id,lineage_id,index_version,"
                "status,index_kind,generator_version,generated_at,authority_role,"
                "origin_instance_id) VALUES ('search_member_project','scope_gc01_test',"
                "'lineage_member_project',1,'ready','project_knowledge','gc02-test',?,"
                "'cloud','instance_gc01_test')",
                (now,),
            )
            connection.execute(
                "INSERT INTO vector_index_manifests (id,scope_id,lineage_id,policy_version,"
                "status,embedding_model,generator_version,generated_at,authority_role,"
                "origin_instance_id) VALUES ('vector_member_project','scope_gc01_test',"
                "'lineage_member_project',1,'ready','gc02-test-model','gc02-test',?,"
                "'cloud','instance_gc01_test')",
                (now,),
            )
            connection.execute(
                "INSERT INTO ai_context_manifests (id,scope_id,lineage_id,policy_version,"
                "status,source_set_id,question_hash,retrieval_policy_version,"
                "selected_source_count,generated_at,authority_role,origin_instance_id) "
                "VALUES ('context_member_project','scope_gc01_test',"
                "'lineage_member_project',1,'ready','source_set_member_project',"
                "'question-hash','gc02-test',1,?,'cloud','instance_gc01_test')",
                (now,),
            )
            connection.execute(
                "INSERT INTO cache_entries (id,scope_id,lineage_id,subject_hash,"
                "policy_version,expires_at,cache_kind,source_version,generated_at,"
                "authority_role,origin_instance_id) VALUES ('cache_member_project',"
                "'scope_gc01_test','lineage_member_project','membership_member',1,"
                "'9999-12-31T23:59:59.999Z','project_context',1,?,'cloud',"
                "'instance_gc01_test')",
                (now,),
            )
            connection.execute(
                "INSERT INTO export_grants (id,scope_id,source_set_id,lineage_id,"
                "grant_generation,status,grantee_principal_id,grantee_membership_id,"
                "export_kind,version,lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES ('export_member_project','scope_gc01_test',"
                "'source_set_member_project','lineage_member_project',1,'active',"
                "'principal_member','membership_member','project_report',1,'active',"
                "?,?,NULL)",
                (now, now),
            )
            connection.commit()

        removed = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-revoke-member",
            },
            json={"participantMembershipIds": [], "expectedVersion": 1},
        )
        assert removed.status_code == 200, removed.text
        propagation = removed.json()["revocationPropagation"]
        assert propagation["state"] == "completed"
        assert propagation["viewerProjections"] == 1
        assert propagation["lineages"] == 1
        assert propagation["searchIndexes"] == 1
        assert propagation["vectorIndexes"] == 1
        assert propagation["aiContexts"] == 1
        assert propagation["cacheEntries"] == 1
        assert propagation["exportGrants"] == 1

        denied = client.get(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers=_auth(tokens["member"]),
        )
        assert denied.status_code == 404
        manifest_denied = client.get(
            f"/api/v2/workbench/projects/{project_id}/memory-manifest",
            headers=_auth(tokens["member"]),
        )
        assert manifest_denied.status_code == 404

    with runtime_connection(database, "cloud") as connection:
        policies = connection.execute(
            "SELECT id,version,lifecycle_state FROM policy_versions "
            "WHERE secured_resource_id=? ORDER BY version",
            (project_id,),
        ).fetchall()
        assert [(int(row["version"]), row["lifecycle_state"]) for row in policies] == [
            (1, "archived"),
            (2, "active"),
        ]
        assert policies[0]["id"] != policies[1]["id"]
        owner_grant = connection.execute(
            "SELECT policy_version_id,status,grant_generation FROM object_grants "
            "WHERE secured_resource_id=? AND subject_membership_id='membership_admin'",
            (project_id,),
        ).fetchone()
        assert owner_grant["policy_version_id"] == policies[1]["id"]
        assert owner_grant["status"] == "active"
        assert int(owner_grant["grant_generation"]) == 1
        assert connection.execute(
            "SELECT invalidated_at FROM viewer_projections "
            "WHERE id='viewer_member_project'"
        ).fetchone()["invalidated_at"]
        assert connection.execute(
            "SELECT invalidated_at FROM derivation_lineage "
            "WHERE id='lineage_member_project'"
        ).fetchone()["invalidated_at"]
        for table, row_id in (
            ("search_index_manifests", "search_member_project"),
            ("vector_index_manifests", "vector_member_project"),
            ("ai_context_manifests", "context_member_project"),
        ):
            row = connection.execute(
                f"SELECT status,invalidated_at FROM {table} WHERE id=?", (row_id,)
            ).fetchone()
            assert row["status"] == "invalidated"
            assert row["invalidated_at"]
        assert connection.execute(
            "SELECT invalidated_at FROM cache_entries WHERE id='cache_member_project'"
        ).fetchone()["invalidated_at"]
        export = connection.execute(
            "SELECT status,revoked_at,version FROM export_grants "
            "WHERE id='export_member_project'"
        ).fetchone()
        assert (export["status"], int(export["version"])) == ("revoked", 2)
        assert export["revoked_at"]
        reconciliation = connection.execute(
            "SELECT status,mismatch_count FROM reconciliation_runs "
            "WHERE reconciliation_kind='gc02_project_access_revocation_v1'"
        ).fetchone()
        assert reconciliation["status"] == "completed"
        assert int(reconciliation["mismatch_count"]) == 7
        assert int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        ) == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc02_share_picker_reads_stable_local_membership_projection() -> None:
    class Runtime:
        @staticmethod
        def current() -> dict[str, object]:
            return {
                "sessionSnapshot": {
                    "membership": {"membershipId": "membership_self"},
                    "members": [
                        {
                            "membershipId": "membership_self",
                            "displayName": "当前成员",
                            "systemRole": "member",
                            "status": "active",
                        },
                        {
                            "membershipId": "membership_colleague",
                            "displayName": "协作同事",
                            "systemRole": "member",
                            "status": "active",
                        },
                        {
                            "membershipId": "membership_disabled",
                            "displayName": "停用成员",
                            "systemRole": "member",
                            "status": "disabled",
                        },
                    ],
                }
            }

    candidates = organization_access_router.dispatch(
        SimpleNamespace(runtime=Runtime()),
        UiRequest(
            method="GET",
            path="employees/mention-candidates",
            query={"q": ""},
            body={},
            idempotency_key="gc02-share-picker",
        ),
    )
    assert candidates == [
        {
            "id": "membership_self",
            "fullName": "当前成员",
            "email": "",
            "primaryRole": "employee",
            "isSelf": True,
        },
        {
            "id": "membership_colleague",
            "fullName": "协作同事",
            "email": "",
            "primaryRole": "employee",
            "isSelf": False,
        },
    ]


def test_gc02_local_project_editor_forwards_the_version_captured_when_opened() -> None:
    calls: list[dict[str, object]] = []

    class Runtime:
        def cloud_query(self, *_: object, **__: object) -> object:
            raise AssertionError("编辑保存前不得重新读取最新版本来掩盖并发冲突")

        def cloud_command(
            self,
            method: str,
            path: str,
            *,
            payload: dict[str, object],
            idempotency_key: str,
        ) -> dict[str, object]:
            calls.append(
                {
                    "method": method,
                    "path": path,
                    "payload": payload,
                    "idempotencyKey": idempotency_key,
                }
            )
            return {
                "project": {
                    "projectId": "client_gc02",
                    "name": payload["name"],
                    "version": 8,
                    "lifecycleState": "active",
                }
            }

    compatibility = SimpleNamespace(runtime=Runtime())
    request = UiRequest(
        method="PUT",
        path="clients/client_gc02",
        query={},
        body={"name": "保留打开时版本", "expectedVersion": 7},
        idempotency_key="gc02-ui-edit",
    )
    result = project_materials_router.dispatch(compatibility, request)
    assert result["id"] == "client_gc02"
    assert calls == [
        {
            "method": "PUT",
            "path": "/api/v2/domain/project-materials/projects/client_gc02",
            "payload": {"name": "保留打开时版本", "expectedVersion": 7},
            "idempotencyKey": "gc02-ui-edit",
        }
    ]

    with pytest.raises(LocalRuntimeError) as missing:
        project_materials_router.dispatch(
            compatibility,
            UiRequest(
                method="PUT",
                path="clients/client_gc02",
                query={},
                body={"name": "缺版本不得保存"},
                idempotency_key="gc02-ui-edit-missing-version",
            ),
        )
    assert missing.value.status_code == 422
    assert missing.value.code == "project_version_required"


def test_gc02_policy_backfill_repairs_existing_grants_without_changing_access(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-legacy-project-create",
            },
            json={
                "name": "既有项目授权补正",
                "participantMembershipIds": ["membership_member"],
            },
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["project"]["projectId"]

    with runtime_connection(database, "cloud") as connection:
        policy_id = str(
            connection.execute(
                "SELECT id FROM policy_versions WHERE secured_resource_id=?",
                (project_id,),
            ).fetchone()[0]
        )
        connection.execute(
            "UPDATE object_grants SET policy_version_id=NULL "
            "WHERE secured_resource_id=?",
            (project_id,),
        )
        connection.execute("DELETE FROM policy_versions WHERE id=?", (policy_id,))
        connection.commit()

    first = backfill_project_policies(database, rollback_ref="test://gc02-backfill")
    second = backfill_project_policies(database, rollback_ref="test://gc02-backfill")
    assert first == {
        "tables": 88,
        "projectsScanned": 1,
        "policiesCreated": 1,
        "grantsBound": 2,
        "remainingActiveGrantsWithoutPolicy": 0,
        "quickCheck": "ok",
        "foreignKeyErrors": 0,
    }
    assert second == {
        **first,
        "policiesCreated": 0,
        "grantsBound": 0,
    }
    with runtime_connection(database, "cloud") as connection:
        policies = connection.execute(
            "SELECT id FROM policy_versions WHERE secured_resource_id=? "
            "AND lifecycle_state='active'",
            (project_id,),
        ).fetchall()
        grants = connection.execute(
            "SELECT policy_version_id, version FROM object_grants "
            "WHERE secured_resource_id=? AND status='active'",
            (project_id,),
        ).fetchall()
        assert len(policies) == 1
        assert len(grants) == 2
        assert {row["policy_version_id"] for row in grants} == {policies[0]["id"]}
        assert {int(row["version"]) for row in grants} == {2}


def test_gc02_local_client_and_viewer_projection_survive_refresh_and_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    cloud = LoginCloud(
        instance="cli_gc02_local",
        organization="org_gc02_local",
        scope="scope_gc02_local",
        email="gc02-local@example.com",
        system_role="admin",
    )
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    login = runtime.login(
        cloud_api_url="http://gc02.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="gc02-local-login",
    )
    sandbox_id = login["sandbox"]["sandboxId"]
    principal_id = login["sessionSnapshot"]["principal"]["principalId"]
    membership_id = login["sessionSnapshot"]["membership"]["membershipId"]
    project = {
        "projectId": "client_gc02_projection",
        "ownerMembershipId": membership_id,
        "name": "同一项目投影",
        "alias": "投影项目",
        "summary": "工作台、任务和战略陪伴使用同一client ID",
        "domain": "项目",
        "color": "#336699",
        "lifecycleState": "active",
        "version": 1,
        "createdAt": "2026-08-06T00:00:00.000Z",
        "updatedAt": "2026-08-06T00:00:00.000Z",
        "participantMembershipIds": [membership_id],
        "documentCount": 0,
        "taskCount": 0,
        "folderState": "local_only",
        "authorizationProjection": {
            "viewerPrincipalId": principal_id,
            "viewerMembershipId": membership_id,
            "policyVersionId": "policy_gc02_project_projection",
            "policyVersion": 1,
            "policySpecSchemaVersion": "gc02.client-access.v1",
            "policySpec": {
                "policyKind": "client_access",
                "defaultDecision": "deny",
                "grantAuthority": "object_grants",
                "allowedCapabilities": [
                    "read",
                    "write",
                    "contributeKnowledge",
                    "manageSharing",
                ],
            },
            "viewerSurfaces": [
                "project_workspace",
                "strategic_accompaniment",
                "task_project_context",
            ],
            "viewerCapabilities": [
                "read",
                "write",
                "contributeKnowledge",
                "manageSharing",
            ],
            "leaseExpiresAt": "2099-01-02T00:00:00.000Z",
            "generatedAt": "2098-12-31T00:00:00.000Z",
            "sourceVersion": 1,
        },
    }

    def cloud_query(path: str, query: object = None) -> dict[str, object]:
        del query
        if path.endswith("/projects"):
            return {"projects": [dict(project)]}
        if path.endswith("/projects/client_gc02_projection"):
            return {"project": dict(project)}
        raise AssertionError(path)

    runtime.cloud_query = cloud_query  # type: ignore[method-assign]
    compatibility = SimpleNamespace(runtime=runtime)
    listed = project_materials_router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="clients",
            query={},
            body={},
            idempotency_key="gc02-local-list",
        ),
    )
    assert [(item["id"], item["name"]) for item in listed] == [
        ("client_gc02_projection", "同一项目投影")
    ]
    assert listed[0]["relatedUserIds"] == []
    assert listed[0]["viewerCapabilities"] == [
        "read",
        "write",
        "contributeKnowledge",
        "manageSharing",
    ]
    workspace = project_materials_router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="clients/client_gc02_projection/workspace",
            query={},
            body={},
            idempotency_key="gc02-local-workspace",
        ),
    )
    assert workspace["client"]["id"] == "client_gc02_projection"

    project["name"] = "同一项目投影已更新"
    project["version"] = 2
    project["authorizationProjection"]["sourceVersion"] = 2
    refreshed = project_materials_router.dispatch(
        compatibility,
        UiRequest(
            method="GET",
            path="clients",
            query={},
            body={},
            idempotency_key="gc02-local-list-refresh",
        ),
    )
    assert refreshed[0]["name"] == "同一项目投影已更新"
    assert refreshed[0]["_strictVersion"] == 2

    reopened = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    reopened_current = reopened.current()
    assert reopened_current["sandbox"]["sandboxId"] == sandbox_id
    assert "application_shell" in (
        reopened_current["sessionSnapshot"]["authorization"]["surfaces"]
    )
    restored = reopened.restore_at_startup()
    assert "application_shell" in (
        restored["sessionSnapshot"]["authorization"]["surfaces"]
    )
    with runtime_connection(database, "local") as connection:
        client = connection.execute(
            "SELECT * FROM clients WHERE id='client_gc02_projection'"
        ).fetchone()
        policy = connection.execute(
            "SELECT * FROM policy_versions "
            "WHERE id='policy_gc02_project_projection'"
        ).fetchone()
        viewers = connection.execute(
            "SELECT * FROM viewer_projections "
            "WHERE secured_resource_id='client_gc02_projection' "
            "AND sandbox_id=?",
            (sandbox_id,),
        ).fetchall()
        assert client is not None
        assert client["name"] == "同一项目投影已更新"
        assert int(client["version"]) == 2
        assert client["sandbox_id"] == sandbox_id
        assert client["projection_state"] == "current"
        assert policy is not None
        assert policy["secured_resource_id"] == client["id"]
        assert policy["projection_state"] == "fresh"
        assert len(viewers) == 1
        assert viewers[0]["policy_version_id"] == policy["id"]
        assert viewers[0]["viewer_membership_id"] == membership_id
        assert viewers[0]["projection_state"] == "fresh"
        lease = datetime.fromisoformat(
            str(viewers[0]["lease_expires_at"]).replace("Z", "+00:00")
        )
        assert lease <= datetime.now(timezone.utc) + timedelta(hours=24)
        assert json.loads(viewers[0]["viewer_capabilities"]) == [
            "contributeKnowledge",
            "manageSharing",
            "read",
            "write",
        ]
        assert int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        ) == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_gc02_project_capability_gate_uses_current_cloud_decision(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-local.db"
    secrets = MemorySecretStore()
    cloud = LoginCloud(
        instance="cli_gc02_gate",
        organization="org_gc02_gate",
        scope="scope_gc02_gate",
        email="gc02-gate@example.com",
    )
    runtime = WorkspaceRuntime(database, secrets, cloud_factory=lambda _: cloud)
    login = runtime.login(
        cloud_api_url="http://gc02-gate.local",
        identifier=cloud.email,
        password="test-password",
        idempotency_key="gc02-gate-login",
    )
    principal_id = login["sessionSnapshot"]["principal"]["principalId"]
    membership_id = login["sessionSnapshot"]["membership"]["membershipId"]
    sandbox_id = login["sandbox"]["sandboxId"]
    project = {
        "projectId": "client_gc02_gate",
        "ownerMembershipId": membership_id,
        "name": "统一授权门项目",
        "lifecycleState": "active",
        "version": 1,
        "createdAt": "2026-08-06T00:00:00.000Z",
        "updatedAt": "2026-08-06T00:00:00.000Z",
        "authorizationProjection": {
            "viewerPrincipalId": principal_id,
            "viewerMembershipId": membership_id,
            "policyVersionId": "policy_gc02_gate",
            "policyVersion": 1,
            "policySpecSchemaVersion": "gc02.client-access.v1",
            "policySpec": {"defaultDecision": "deny"},
            "viewerSurfaces": ["project_workspace"],
            "viewerCapabilities": ["read"],
            "leaseExpiresAt": "2099-01-02T00:00:00.000Z",
            "generatedAt": "2098-12-31T00:00:00.000Z",
            "sourceVersion": 1,
        },
    }
    runtime.cloud_query = lambda path, query=None: {"project": dict(project)}  # type: ignore[method-assign]
    allowed = runtime.require_project_capability("client_gc02_gate", "read")
    assert allowed["projectId"] == "client_gc02_gate"

    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    with runtime_connection(database, "local") as connection:
        scope_id = connection.execute(
            "SELECT scope_id FROM clients WHERE id='client_gc02_gate'"
        ).fetchone()["scope_id"]
        viewer = connection.execute(
            "SELECT id,policy_version_id FROM viewer_projections "
            "WHERE secured_resource_id='client_gc02_gate' AND sandbox_id=? "
            "AND invalidated_at IS NULL",
            (sandbox_id,),
        ).fetchone()
        connection.execute(
            "INSERT INTO source_sets (id,scope_id,client_id,source_count,version,"
            "purpose_kind,publication_state,created_by_principal_id,created_at,"
            "lifecycle_state,updated_at,authority_role,origin_instance_id) VALUES "
            "('source_set_gc02_local',?,'client_gc02_gate',1,1,'project_knowledge',"
            "'published',?,?,'active',?,'local','cli_gc02_gate')",
            (scope_id, principal_id, now, now),
        )
        connection.execute(
            "INSERT INTO derivation_lineage (id,scope_id,source_set_id,policy_version_id,"
            "grant_generation,derivative_kind,derivative_object_id,generator_version,"
            "generated_at,source_version,authority_role,origin_instance_id) VALUES "
            "('lineage_gc02_local',?,'source_set_gc02_local',?,1,'viewer_projection',?,"
            "'gc02-test',?,1,'local','cli_gc02_gate')",
            (scope_id, viewer["policy_version_id"], viewer["id"], now),
        )
        connection.execute(
            "INSERT INTO search_index_manifests (id,scope_id,lineage_id,index_version,"
            "status,index_kind,generated_at,authority_role,origin_instance_id) VALUES "
            "('search_gc02_local',?,'lineage_gc02_local',1,'ready','project_knowledge',?,"
            "'local','cli_gc02_gate')",
            (scope_id, now),
        )
        connection.execute(
            "INSERT INTO vector_index_manifests (id,scope_id,lineage_id,policy_version,"
            "status,embedding_model,generated_at,authority_role,origin_instance_id) VALUES "
            "('vector_gc02_local',?,'lineage_gc02_local',1,'ready','gc02-test',?,"
            "'local','cli_gc02_gate')",
            (scope_id, now),
        )
        connection.execute(
            "INSERT INTO ai_context_manifests (id,scope_id,lineage_id,policy_version,"
            "status,source_set_id,question_hash,selected_source_count,generated_at,"
            "authority_role,origin_instance_id) VALUES ('context_gc02_local',?,"
            "'lineage_gc02_local',1,'ready','source_set_gc02_local','question',1,?,"
            "'local','cli_gc02_gate')",
            (scope_id, now),
        )
        connection.execute(
            "INSERT INTO cache_entries (id,scope_id,lineage_id,subject_hash,policy_version,"
            "cache_kind,source_version,generated_at,authority_role,origin_instance_id) "
            "VALUES ('cache_gc02_local',?,'lineage_gc02_local',?,1,'project_context',1,?,"
            "'local','cli_gc02_gate')",
            (scope_id, membership_id, now),
        )
        connection.execute(
            "INSERT INTO export_grants (id,scope_id,source_set_id,lineage_id,"
            "grant_generation,status,grantee_principal_id,grantee_membership_id,"
            "export_kind,version,lifecycle_state,created_at,updated_at,deleted_at,"
            "sandbox_id,source_version,projection_state,projected_at,stale_at) VALUES "
            "('export_gc02_local',?,'source_set_gc02_local','lineage_gc02_local',1,"
            "'active',?,?,'project_report',1,'active',?,?,NULL,?,1,'current',?,NULL)",
            (scope_id, principal_id, membership_id, now, now, sandbox_id, now),
        )
        connection.commit()

    def revoked(path: str, query: object = None) -> dict[str, object]:
        del path, query
        raise LocalRuntimeError(404, "project_missing", "当前成员无法访问该项目")

    runtime.cloud_query = revoked  # type: ignore[method-assign]
    with pytest.raises(LocalRuntimeError) as blocked:
        runtime.require_project_capability("client_gc02_gate", "read")
    assert blocked.value.status_code == 404
    assert blocked.value.code == "project_missing"
    with runtime_connection(database, "local") as connection:
        # A stale local row may remain as a rebuildable projection, but it can
        # no longer authorize a new consumer request by itself.
        client_projection = connection.execute(
            "SELECT projection_state,stale_at FROM clients "
            "WHERE id='client_gc02_gate'"
        ).fetchone()
        assert client_projection["projection_state"] == "stale"
        assert client_projection["stale_at"]
        assert connection.execute(
            "SELECT invalidated_at FROM viewer_projections "
            "WHERE secured_resource_id='client_gc02_gate' AND sandbox_id=?",
            (sandbox_id,),
        ).fetchone()["invalidated_at"]
        assert connection.execute(
            "SELECT invalidated_at FROM derivation_lineage "
            "WHERE id='lineage_gc02_local'"
        ).fetchone()["invalidated_at"]
        for table, row_id in (
            ("search_index_manifests", "search_gc02_local"),
            ("vector_index_manifests", "vector_gc02_local"),
            ("ai_context_manifests", "context_gc02_local"),
        ):
            row = connection.execute(
                f"SELECT status,invalidated_at FROM {table} WHERE id=?", (row_id,)
            ).fetchone()
            assert row["status"] == "invalidated"
            assert row["invalidated_at"]
        assert connection.execute(
            "SELECT invalidated_at FROM cache_entries WHERE id='cache_gc02_local'"
        ).fetchone()["invalidated_at"]
        export = connection.execute(
            "SELECT status,revoked_at,projection_state FROM export_grants "
            "WHERE id='export_gc02_local'"
        ).fetchone()
        assert export["status"] == "revoked"
        assert export["revoked_at"]
        assert export["projection_state"] == "stale"
        reconciliation = connection.execute(
            "SELECT status,reconciliation_kind FROM reconciliation_runs "
            "WHERE reconciliation_kind='gc02_project_access_revoked:project_missing'"
        ).fetchone()
        assert reconciliation["status"] == "completed"
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0] == 88

    # A successful cloud list omission is the same authoritative revocation
    # signal and must not depend on a later direct open attempt.
    runtime.cloud_query = lambda path, query=None: {"project": dict(project)}  # type: ignore[method-assign]
    runtime.require_project_capability("client_gc02_gate", "read")
    runtime.reconcile_project_projections([])
    with runtime_connection(database, "local") as connection:
        assert connection.execute(
            "SELECT projection_state FROM clients WHERE id='client_gc02_gate'"
        ).fetchone()["projection_state"] == "stale"
        assert connection.execute(
            "SELECT reconciliation_kind FROM reconciliation_runs "
            "WHERE id LIKE 'recon_gc02_revoke_%'"
        ).fetchone()["reconciliation_kind"] == (
            "gc02_project_access_revoked:cloud_list_omission"
        )

    # A transient network failure is retryable and must not be misread as a
    # revocation; the last projection stays visible but cannot authorize the
    # failed request by itself.
    runtime.cloud_query = lambda path, query=None: {"project": dict(project)}  # type: ignore[method-assign]
    runtime.require_project_capability("client_gc02_gate", "read")

    def unreachable(path: str, query: object = None) -> dict[str, object]:
        del path, query
        raise LocalRuntimeError(503, "cloud_unreachable", "暂时无法连接组织云")

    runtime.cloud_query = unreachable  # type: ignore[method-assign]
    with pytest.raises(LocalRuntimeError) as retryable:
        runtime.require_project_capability("client_gc02_gate", "read")
    assert retryable.value.status_code == 503
    assert retryable.value.code == "cloud_unreachable"
    with runtime_connection(database, "local") as connection:
        assert connection.execute(
            "SELECT projection_state FROM clients WHERE id='client_gc02_gate'"
        ).fetchone()["projection_state"] == "current"

    # Even a syntactically valid cloud response cannot authorize past the
    # bounded 24-hour project lease.
    expired_project = json.loads(json.dumps(project))
    expired_project["authorizationProjection"]["generatedAt"] = (
        datetime.now(timezone.utc) - timedelta(hours=26)
    ).isoformat(timespec="milliseconds")
    expired_project["authorizationProjection"]["leaseExpiresAt"] = (
        datetime.now(timezone.utc) + timedelta(hours=2)
    ).isoformat(timespec="milliseconds")
    runtime.cloud_query = lambda path, query=None: {"project": expired_project}  # type: ignore[method-assign]
    with pytest.raises(LocalRuntimeError) as expired:
        runtime.require_project_capability("client_gc02_gate", "read")
    assert expired.value.status_code == 403
    assert expired.value.code == "project_authorization_lease_expired"
    with runtime_connection(database, "local") as connection:
        assert connection.execute(
            "SELECT projection_state FROM clients WHERE id='client_gc02_gate'"
        ).fetchone()["projection_state"] == "stale"


def test_gc02_workbench_answer_consumer_fails_closed_before_local_body_read() -> None:
    calls: list[str] = []

    class Runtime:
        allowed = True

        def require_project_capability(
            self, project_id: str, capability: str = "read"
        ) -> dict[str, object]:
            calls.append(f"authorize:{project_id}:{capability}")
            if not self.allowed:
                raise LocalRuntimeError(
                    404, "project_missing", "当前成员无法访问该项目"
                )
            return {"projectId": project_id}

        def workbench_answer(self, answer_id: str) -> dict[str, object]:
            calls.append(f"answer:{answer_id}")
            return {
                "answerId": answer_id,
                "projectId": "client_gc02_consumer",
                "question": "问题",
                "answerMarkdown": "回答正文",
                "createdAt": "2026-08-06T00:00:00.000Z",
                "updatedAt": "2026-08-06T00:00:00.000Z",
            }

    runtime = Runtime()
    compatibility = SimpleNamespace(runtime=runtime)
    request = UiRequest(
        method="GET",
        path="clients/client_gc02_consumer/workspace/chat/messages/answer_1",
        query={},
        body={},
        idempotency_key="gc02-consumer-read",
    )
    response = workbench_outputs_router.dispatch(compatibility, request)
    assert response["content"] == "回答正文"
    assert calls == [
        "authorize:client_gc02_consumer:read",
        "answer:answer_1",
    ]

    calls.clear()
    runtime.allowed = False
    with pytest.raises(LocalRuntimeError) as blocked:
        workbench_outputs_router.dispatch(compatibility, request)
    assert blocked.value.code == "project_missing"
    assert calls == ["authorize:client_gc02_consumer:read"]


def test_gc02_cloud_consumers_reject_grant_from_superseded_policy(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc02-policy-gate-create",
            },
            json={
                "name": "权限版本门项目",
                "participantMembershipIds": ["membership_member"],
            },
        )
        assert created.status_code == 201, created.text
        project_id = created.json()["project"]["projectId"]
        with runtime_connection(database, "cloud") as connection:
            current = connection.execute(
                "SELECT * FROM policy_versions WHERE secured_resource_id=? "
                "ORDER BY version DESC LIMIT 1",
                (project_id,),
            ).fetchone()
            assert current is not None
            connection.execute(
                "INSERT INTO policy_versions ("
                "id, scope_id, secured_resource_id, policy_scope_kind, version, "
                "policy_spec_schema_version, policy_spec, effective_at, created_at, "
                "lifecycle_state, updated_at, deleted_at"
                ") VALUES (?, ?, ?, ?, 2, ?, ?, ?, ?, 'active', ?, NULL)",
                (
                    "policy_gc02_current_v2",
                    current["scope_id"],
                    project_id,
                    current["policy_scope_kind"],
                    current["policy_spec_schema_version"],
                    current["policy_spec"],
                    "2026-08-06T01:00:00.000Z",
                    "2026-08-06T01:00:00.000Z",
                    "2026-08-06T01:00:00.000Z",
                ),
            )
            connection.commit()

        stale_grant = client.get(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers=_auth(tokens["member"]),
        )
        assert stale_grant.status_code == 404
        assert stale_grant.json()["error"]["code"] == "project_missing"

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                "UPDATE object_grants SET policy_version_id=?, version=version+1 "
                "WHERE secured_resource_id=? AND subject_membership_id=? "
                "AND status='active'",
                ("policy_gc02_current_v2", project_id, "membership_member"),
            )
            connection.commit()
        current_grant = client.get(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers=_auth(tokens["member"]),
        )
        assert current_grant.status_code == 200, current_grant.text


@pytest.mark.parametrize(
    ("router", "ui_request"),
    [
        (
            project_materials_router,
            UiRequest(
                method="POST",
                path="clients/client_gc02_consumer/knowledge/search",
                query={},
                body={"prompt": "关键词"},
                idempotency_key="gc02-search-blocked",
            ),
        ),
        (
            workbench_outputs_router,
            UiRequest(
                method="POST",
                path="clients/client_gc02_consumer/workspace/chat/start",
                query={},
                body={"prompt": "问题"},
                idempotency_key="gc02-chat-blocked",
            ),
        ),
        (
            workbench_outputs_router,
            UiRequest(
                method="POST",
                path="clients/client_gc02_consumer/narrative/regenerate",
                query={},
                body={},
                idempotency_key="gc02-profile-blocked",
            ),
        ),
    ],
)
def test_gc02_local_consumers_authorize_before_search_model_or_projection(
    router: object,
    ui_request: UiRequest,
) -> None:
    class Runtime:
        pinned_workspace_context = None

        def require_project_capability(
            self, project_id: str, capability: str = "read"
        ) -> dict[str, object]:
            raise LocalRuntimeError(
                404, "project_missing", "当前成员无法访问该项目"
            )

        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"授权失败后不应调用{name}")

    with pytest.raises(LocalRuntimeError) as blocked:
        router.dispatch(SimpleNamespace(runtime=Runtime()), ui_request)
    assert blocked.value.code == "project_missing"
