from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.app.gc07_sources import GC07LocalProjectMaterialsRepository
from backend.app.runtime import WorkspaceRuntime
from backend.app.secret_store import MemorySecretStore
from backend.app.ui_domains.project_materials import _knowledge_progress_view
from strict_common.ids import utc_now


def _seed_runtime_scope(runtime: WorkspaceRuntime) -> str:
    now = utc_now()
    sandbox_id = "sandbox-audio"
    with runtime._connection() as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,"
            "record_kind,name,created_at,deleted_at,projection_state,projected_at) "
            "VALUES ('organization-audio','active',1,?,'organization',"
            "'音频测试组织',?,NULL,'current',?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,"
            "principal_kind,display_name,version,lifecycle_state,created_at,deleted_at,"
            "projection_state,projected_at) VALUES ('principal-audio','active',1,?,"
            "'person','音频测试成员',1,'active',?,NULL,'current',?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,"
            "policy_version,created_at,updated_at,status,version,lifecycle_state,"
            "deleted_at,projection_state,projected_at) VALUES ('scope-audio',"
            "'organization','organization-audio',1,?,?,'active',1,'active',NULL,"
            "'current',?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
            "status,version,record_kind,visibility_scope,lifecycle_state,created_at,"
            "updated_at,deleted_at,projection_state,projected_at) VALUES ("
            "'membership-audio','scope-audio','principal-audio','admin','active',1,"
            "'membership','organization','active',?,?,NULL,'current',?)",
            (now, now, now),
        )
        connection.execute(
            "INSERT INTO sandboxes (id,scope_id,principal_id,membership_id,record_kind,"
            "cloud_instance_id,database_generation_id,sandbox_kind,display_name,"
            "runtime_status,manifest_hash,version,lifecycle_state,created_at,updated_at,"
            "deleted_at,authority_role,origin_instance_id) VALUES ("
            "?,'scope-audio','principal-audio','membership-audio','sandbox',"
            "'cloud-audio',?,'organization','音频测试工作空间','ready',?,1,"
            "'active',?,?,NULL,'local',?)",
            (
                sandbox_id,
                runtime.identity.database_generation_id,
                runtime.identity.manifest_hash,
                now,
                now,
                runtime.identity.database_generation_id,
            ),
        )
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
            "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
            "origin_instance_id) VALUES ('project-audio','scope-audio','client',"
            "'active',1,'client',?,?,NULL,'local',?)",
            (now, now, runtime.identity.database_generation_id),
        )
        connection.execute(
            "INSERT INTO clients (id,scope_id,owner_membership_id,lifecycle_state,"
            "version,name,created_at,updated_at,deleted_at,sandbox_id,source_version,"
            "projection_state,projected_at) VALUES ('project-audio','scope-audio',"
            "'membership-audio','active',1,'音频测试项目',?,?,NULL,?,1,"
            "'current',?)",
            (now, now, sandbox_id, now),
        )
        connection.commit()
    return sandbox_id


def test_audio_processing_uses_one_authoritative_pipeline_and_terminal_state(
    tmp_path: Path,
) -> None:
    runtime = WorkspaceRuntime(
        tmp_path / "local-audio" / "strict-local.db",
        MemorySecretStore(),
    )
    sandbox_id = _seed_runtime_scope(runtime)
    runtime._current_context = lambda require_ready=True: SimpleNamespace(  # type: ignore[method-assign]
        sandbox_id=sandbox_id,
        cloud_instance_id="cloud-audio",
        organization_id="organization-audio",
        cloud_api_url="https://audio.invalid",
        principal_id="principal-audio",
        membership_id="membership-audio",
    )
    store = GC07LocalProjectMaterialsRepository(runtime)
    recording = tmp_path / "meeting.webm"
    recording.write_bytes(b"webm-test-original")
    imported = store.import_paths(
        project_id="project-audio",
        mode="file",
        paths=[str(recording)],
    )
    material = imported["materials"][0]
    store.bind_cloud_documents(
        project_id="project-audio",
        local_materials=[material],
        cloud_documents=[
            {
                "localSourceId": material["localSourceId"],
                "documentId": "audio-document",
            }
        ],
    )

    result = store.process_pending_documents(
        project_id="project-audio",
        document_ids=["audio-document"],
    )

    assert result["items"][0]["parseStatus"] == "blocked"
    assert result["items"][0]["processingErrorCode"] == "local_audio_asr_not_ready"
    [document] = store.documents("project-audio")
    assert document["parseStatus"] == "blocked"
    assert document["processingStage"] == "audio_transcription"
    assert document["processingBatchStartedAt"]
    assert Path(document["managedPath"]).read_bytes() == b"webm-test-original"
    with runtime._connection() as connection:
        attempts = connection.execute(
            "SELECT source_asset_id,processor_kind,status FROM processing_attempts "
            "ORDER BY started_at,id"
        ).fetchall()
    assert [(row["processor_kind"], row["status"]) for row in attempts] == [
        ("local_audio_transcription", "blocked")
    ]
    source_asset_id = str(attempts[0]["source_asset_id"])

    # Historical builds could leave a generic text-extraction attempt beside
    # the real audio attempt. Such an orphan must never regain authority over
    # the file card or progress projection after an upgrade.
    store.create_local_processing_attempt(
        source_asset_id=source_asset_id,
        processor_kind="local_text_extraction",
        status="queued",
        error_code=None,
        error_message=None,
        attempt_no=99,
    )
    [document_with_historical_orphan] = store.documents("project-audio")
    assert document_with_historical_orphan["parseStatus"] == "blocked"
    assert (
        document_with_historical_orphan["processingErrorCode"]
        == "local_audio_asr_not_ready"
    )


def test_durable_running_attempt_does_not_become_interrupted_when_memory_is_lost() -> None:
    class Store:
        @staticmethod
        def is_processing_batch_active(*, project_id: str, batch_started_at: str) -> bool:
            del project_id, batch_started_at
            return False

    view = _knowledge_progress_view(
        store=Store(),
        project_id="project-audio",
        documents=[
            {
                "id": "audio-document",
                "title": "meeting.webm",
                "parseStatus": "processing",
                "wikiStatus": "not_requested",
                "processingBatchStartedAt": "2026-08-21T08:00:00Z",
            }
        ],
    )

    assert view["jobs"][0]["status"] == "running"
    assert len(view["running"]) == 1
