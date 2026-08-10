from pathlib import Path
from types import SimpleNamespace
import threading
import time

from backend.app.ui_domains import task_attachments as local_task_attachments
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repositories.project_materials import GC07ProjectMaterialsRepository
from strict_common.schema import runtime_connection, user_tables
from tests.test_gc14_workbench_answer import _repository


def test_task_transcription_starts_in_background_without_blocking_task_save(
    tmp_path: Path,
    monkeypatch,
) -> None:
    release_worker = threading.Event()
    completed = threading.Event()

    class Runtime:
        database_path = tmp_path / "strict-local.db"

        @staticmethod
        def _current_context(*, require_ready: bool):
            assert require_ready
            return SimpleNamespace(sandbox_id="sandbox-test")

    class Store:
        states: list[str] = []
        imported_title = ""

        def __init__(self, *_args, **_kwargs):
            pass

        @staticmethod
        def task_attachments(_task_id: str):
            return [{
                "id": "audio-1",
                "title": "访谈.m4a",
                "path": str(tmp_path / "访谈.m4a"),
                "isAudio": True,
                "localAvailable": True,
                "processingStatus": "not_requested",
                "clientId": "client-1",
            }]

        def set_task_transcription_state(self, **payload):
            self.states.append(str(payload["status"]))
            return payload

        def save_task_transcript(self, **_payload):
            self.states.append("ready")
            return {"version": 1, "path": str(tmp_path / "访谈_转写.txt")}

        @classmethod
        def import_text(cls, **_payload):
            cls.imported_title = str(_payload.get("title") or "")
            return {"localSourceId": "transcript-source-1"}

        @staticmethod
        def bind_pending_materials(**_payload):
            return []

        @staticmethod
        def bind_task_attachment(**_payload):
            completed.set()

    (tmp_path / "访谈.m4a").write_bytes(b"audio")
    monkeypatch.setattr(local_task_attachments, "LocalProjectMaterialsRepository", Store)
    monkeypatch.setattr(local_task_attachments, "model_ready", lambda *_args: True)
    monkeypatch.setattr(
        local_task_attachments,
        "run_local_asr_subprocess",
        lambda **_kwargs: (
            release_worker.wait(2),
            {"text": "转写正文"},
        )[1],
    )
    monkeypatch.setattr(
        local_task_attachments,
        "_task",
        lambda *_args: {"id": "task-1", "client_id": "client-1"},
    )
    monkeypatch.setattr(
        local_task_attachments,
        "_task_ui",
        lambda _compatibility, row: {"id": row["id"]},
    )
    compatibility = SimpleNamespace(runtime=Runtime())
    match = SimpleNamespace(group=lambda name: {"task_id": "task-1", "attachment_id": "audio-1"}[name])
    started_at = time.monotonic()
    result = local_task_attachments.retry_task_transcription(
        compatibility,
        UiRequest(
            method="POST",
            path="tasks/task-1/attachments/audio-1/retry-transcription",
            query={},
            body={},
            idempotency_key="task-asr-test",
        ),
        match,
    )
    assert time.monotonic() - started_at < 0.25
    assert result == {"id": "task-1"}
    assert Store.states[0] == "queued"
    release_worker.set()
    assert completed.wait(1)
    assert "ready" in Store.states
    assert Store.imported_title == "访谈-录音转写"


def test_task_attachment_registers_only_safe_project_metadata(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    task_domain = GC04TaskRepository(repository)
    created = task_domain.create_task(
        identity,
        payload={"title": "核对项目附件", "clientId": payload["projectId"]},
        idempotency_key="task-attachment-create",
    )
    task_id = created["task"]["id"]
    materials = GC07ProjectMaterialsRepository(repository)
    registered = materials.register_local_material_metadata(
        identity,
        project_id=payload["projectId"],
        materials=[
            {
                "localSourceId": "local-task-file-1",
                "fileName": "核对资料.txt",
                "contentHash": "c" * 64,
                "byteSize": 18,
                "mediaType": "text/plain",
                "relationKind": "task",
                "relationId": task_id,
            }
        ],
        idempotency_key="task-attachment-metadata",
    )
    detail = task_domain.task_detail(identity, task_id=task_id)
    attachment = detail["task"]["attachments"][0]
    assert attachment["id"] == registered["documents"][0]["documentId"]
    assert attachment["localAvailable"] is False
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert len(user_tables(connection)) == 88
        row = connection.execute(
            "SELECT source_kind,source_locator_nonlocal FROM source_assets "
            "WHERE id=?",
            (attachment["id"],),
        ).fetchone()
        assert tuple(row) == ("task_attachment_metadata", f"task:{task_id}")
        receipt = connection.execute(
            "SELECT receipt FROM object_manifests WHERE id=(SELECT object_manifest_id "
            "FROM source_assets WHERE id=?)",
            (attachment["id"],),
        ).fetchone()[0]
        assert "/" not in str(receipt).replace("local_private_metadata_only", "")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_task_tags_use_task_views_without_parallel_tag_tables(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    domain = GC04TaskRepository(repository)
    tag = domain.create_tag(
        identity,
        payload={"name": "需核实", "color": "#E59B2F", "scope": "org"},
        idempotency_key="task-tag-create",
    )["taskTag"]
    created = domain.create_task(
        identity,
        payload={
            "title": "核实项目事实",
            "clientId": payload["projectId"],
            "tagIds": [tag["id"]],
        },
        idempotency_key="tagged-task-create",
    )
    task = created["task"]
    assert task["tags"][0]["taskTagId"] == tag["id"]
    updated = domain.update_task(
        identity,
        task_id=task["id"],
        payload={"expectedVersion": task["version"], "tagIds": []},
        idempotency_key="tagged-task-clear",
    )
    assert updated["task"]["tags"] == []
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM task_views WHERE record_kind='tag'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM task_views WHERE record_kind='tag_assignment' "
            "AND lifecycle_state='deleted'"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
