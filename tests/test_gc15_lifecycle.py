from __future__ import annotations

from pathlib import Path

from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repositories.gc15_lifecycle import GC15LifecycleRepository
from strict_common.ids import utc_now
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository


def _seed_task_derivatives(
    database: Path,
    *,
    scope_id: str,
    principal_id: str,
    cloud_instance_id: str,
    task_id: str,
) -> dict[str, str]:
    now = utc_now()
    values = {
        "sourceSetId": "sources_gc15_task",
        "sourceMemberId": "source_member_gc15_task",
        "lineageId": "lineage_gc15_task",
        "searchId": "search_gc15_task",
        "vectorId": "vector_gc15_task",
        "cacheId": "cache_gc15_task",
        "contextId": "context_gc15_task",
        "exportId": "export_gc15_task",
    }
    with runtime_connection(database, "cloud") as connection:
        connection.execute(
            "INSERT INTO source_sets (id,scope_id,client_id,security_label_set_version,"
            "source_count,version,purpose_kind,publication_state,created_by_principal_id,"
            "created_at,expires_at,lifecycle_state,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES (?,?,NULL,1,1,1,'gc15_lifecycle_test',"
            "'published',?,?,NULL,'active',?,NULL,'cloud',?)",
            (
                values["sourceSetId"], scope_id, principal_id, now, now,
                cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO source_set_members (id,scope_id,source_set_id,source_object_id,"
            "source_version,policy_version,source_object_kind,ordinal,added_at,removed_at,"
            "version,lifecycle_state,created_at,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES (?,?,?,?,1,1,'task',0,?,NULL,1,'active',"
            "?,?,NULL,'cloud',?)",
            (
                values["sourceMemberId"], scope_id, values["sourceSetId"],
                task_id, now, now, now, cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO derivation_lineage (id,scope_id,source_set_id,policy_version_id,"
            "grant_generation,derivative_kind,derivative_object_id,generator_version,"
            "generated_at,invalidated_at,source_version,authority_role,origin_instance_id) "
            "VALUES (?,?,?,NULL,1,'task_context',?,'gc15-test-v1',?,NULL,1,'cloud',?)",
            (
                values["lineageId"], scope_id, values["sourceSetId"], task_id,
                now, cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO search_index_manifests (id,scope_id,lineage_id,index_version,status,"
            "reconciled_at,index_kind,index_artifact_ref,generator_version,invalidated_at,"
            "source_version,generated_at,authority_role,origin_instance_id) VALUES "
            "(?,?,?,1,'ready',?,'keyword','gc15-search','gc15-test-v1',NULL,1,?,"
            "'cloud',?)",
            (
                values["searchId"], scope_id, values["lineageId"], now, now,
                cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO vector_index_manifests (id,scope_id,lineage_id,provider_resource_id,"
            "policy_version,status,embedding_model,embedding_dimensions,index_artifact_ref,"
            "generator_version,reconciled_at,invalidated_at,source_version,generated_at,"
            "authority_role,origin_instance_id) VALUES (?,?,?,NULL,1,'ready','test',3,"
            "'gc15-vector','gc15-test-v1',?,NULL,1,?,'cloud',?)",
            (
                values["vectorId"], scope_id, values["lineageId"], now, now,
                cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO cache_entries (id,scope_id,lineage_id,subject_hash,policy_version,"
            "expires_at,cache_kind,object_manifest_id,source_version,generated_at,"
            "invalidated_at,authority_role,origin_instance_id) VALUES (?,?,?,NULL,1,NULL,"
            "'task_context',NULL,1,?,NULL,'cloud',?)",
            (
                values["cacheId"], scope_id, values["lineageId"], now,
                cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO ai_context_manifests (id,scope_id,lineage_id,provider_resource_id,"
            "policy_version,status,source_set_id,question_hash,retrieval_policy_version,"
            "selected_source_count,context_object_manifest_id,generated_at,invalidated_at,"
            "source_version,authority_role,origin_instance_id) VALUES (?,?,?,NULL,1,'ready',"
            "?,NULL,'gc15-test-v1',1,NULL,?,NULL,1,'cloud',?)",
            (
                values["contextId"], scope_id, values["lineageId"],
                values["sourceSetId"], now, cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO export_grants (id,scope_id,source_set_id,lineage_id,grant_generation,"
            "expires_at,status,grantee_principal_id,grantee_membership_id,export_kind,"
            "revoked_at,version,lifecycle_state,created_at,updated_at,deleted_at) VALUES "
            "(?,?,?,?,1,NULL,'active',?,NULL,'task_context',NULL,1,'active',?,?,NULL)",
            (
                values["exportId"], scope_id, values["sourceSetId"],
                values["lineageId"], principal_id, now, now,
            ),
        )
        connection.commit()
    return values


def test_gc15_task_tombstone_hold_retry_purge_and_derivative_invalidation(
    tmp_path: Path,
) -> None:
    repository, identity, _ = _repository(tmp_path)
    task_domain = GC04TaskRepository(repository)
    lifecycle = GC15LifecycleRepository(repository)
    with runtime_connection(repository.database_path, "cloud") as connection:
        structure_before = dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )

    created = task_domain.create_task(
        identity,
        payload={"title": "GC15唯一真实记录敏感标题", "description": "待清除正文"},
        idempotency_key="gc15-task-create",
    )
    task_id = created["task"]["id"]
    rows = _seed_task_derivatives(
        repository.database_path,
        scope_id=identity.scope_id,
        principal_id=identity.principal_id,
        cloud_instance_id=identity.cloud_instance_id,
        task_id=task_id,
    )
    hold = lifecycle.place_legal_hold(
        identity,
        resource_id=task_id,
        reason="GC15临时验收保留",
        idempotency_key="gc15-hold-place",
    )
    replay = lifecycle.place_legal_hold(
        identity,
        resource_id=task_id,
        reason="GC15临时验收保留",
        idempotency_key="gc15-hold-place",
    )
    assert replay["idempotentReplay"] is True
    assert hold["holdState"] == "active"

    task_domain.delete_task(
        identity,
        task_id=task_id,
        expected_version=1,
        idempotency_key="gc15-task-delete",
    )
    blocked = lifecycle.settle_purge(
        identity,
        resource_id=task_id,
        idempotency_key="gc15-purge-blocked",
    )
    assert blocked["state"] == "blocked"
    assert blocked["retryable"] is True
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT title FROM tasks WHERE id=?", (task_id,)
        ).fetchone()[0] == "GC15唯一真实记录敏感标题"

    released = lifecycle.release_legal_hold(
        identity,
        hold_id=hold["holdId"],
        expected_version=1,
        idempotency_key="gc15-hold-release",
    )
    assert released["holdState"] == "released"
    completed = lifecycle.settle_purge(
        identity,
        resource_id=task_id,
        idempotency_key="gc15-purge-complete",
    )
    completed_replay = lifecycle.settle_purge(
        identity,
        resource_id=task_id,
        idempotency_key="gc15-purge-complete",
    )
    assert completed["state"] == "completed"
    assert completed["tombstoneRetained"] is True
    assert completed_replay["idempotentReplay"] is True

    with runtime_connection(repository.database_path, "cloud") as connection:
        task = connection.execute(
            "SELECT title,description,lifecycle_state FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        assert tuple(task) == ("[已清除]", None, "deleted")
        assert connection.execute(
            "SELECT lifecycle_state FROM secured_resources WHERE id=?", (task_id,)
        ).fetchone()[0] == "deleted"
        assert connection.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE secured_resource_id=?",
            (task_id,),
        ).fetchone()[0] == 2
        assert [
            row[0]
            for row in connection.execute(
                "SELECT status FROM purge_ledger WHERE secured_resource_id=? "
                "ORDER BY purge_generation",
                (task_id,),
            ).fetchall()
        ] == ["blocked_legal_hold", "completed"]
        assert connection.execute(
            "SELECT invalidated_at IS NOT NULL FROM derivation_lineage WHERE id=?",
            (rows["lineageId"],),
        ).fetchone()[0] == 1
        for table, row_id in (
            ("search_index_manifests", rows["searchId"]),
            ("vector_index_manifests", rows["vectorId"]),
            ("ai_context_manifests", rows["contextId"]),
        ):
            assert tuple(
                connection.execute(
                    f"SELECT status,invalidated_at IS NOT NULL FROM {table} WHERE id=?",
                    (row_id,),
                ).fetchone()
            ) == ("invalidated", 1)
        assert connection.execute(
            "SELECT invalidated_at IS NOT NULL FROM cache_entries WHERE id=?",
            (rows["cacheId"],),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM export_grants WHERE id=?", (rows["exportId"],)
        ).fetchone()[0] == "revoked"
        assert connection.execute(
            "SELECT lifecycle_state FROM source_set_members WHERE id=?",
            (rows["sourceMemberId"],),
        ).fetchone()[0] == "archived"
        assert connection.execute(
            "SELECT COUNT(*) FROM reconciliation_runs WHERE reconciliation_kind='gc15_purge'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM purge_layer_receipts WHERE purge_id=? "
            "AND status='completed'",
            (completed["purgeLedgerId"],),
        ).fetchone()[0] == len(completed["invalidated"])
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        structure_after = dict(
            connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
        )
    assert structure_after == structure_before
    assert task_domain.board(identity)["tasks"] == []
