from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
from typing import Any
import io
import sqlite3
import threading
import time

from backend.app.project_materials_local import LocalProjectMaterialsRepository
from backend.app.ui_domains import task_attachments as local_task_attachments
from backend.app.ui_domains.registry import build_default_registry
from backend.app.ui_domains.project_materials import register_and_process_local_materials
from backend.app.ui_domains.routing import UiRequest
from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repositories.gc06_planning import create_meeting
from cloud_backend.app.repositories.project_materials import GC07ProjectMaterialsRepository
from strict_common.schema import runtime_connection, user_tables
from tests.test_gc14_workbench_answer import _repository


def test_task_project_id_accepts_current_cloud_receipt_shape() -> None:
    assert local_task_attachments._task_project_id({"clientId": "client-current"}) == "client-current"
    assert local_task_attachments._task_project_id({"client_id": "client-compat"}) == "client-compat"


def test_recorrect_transcript_route_is_registered_in_strict_runtime() -> None:
    routes = {
        (route.domain, route.method, route.pattern)
        for route in build_default_registry().routes
    }
    assert (
        "gc04_task_attachments",
        "POST",
        r"tasks/(?P<task_id>[^/]+)/attachments/(?P<attachment_id>[^/]+)/recorrect-transcript",
    ) in routes


def test_uploaded_attachment_staging_is_not_a_filtered_dotfile(tmp_path: Path) -> None:
    compatibility = SimpleNamespace(
        runtime=SimpleNamespace(database_path=tmp_path / "strict-local.db")
    )
    upload = SimpleNamespace(
        filename="项目附件.txt",
        file=io.BytesIO("附件正文".encode("utf-8")),
    )

    staged = local_task_attachments._save_uploaded_file(compatibility, upload)

    assert staged.name.startswith("task-attachment-")
    assert not staged.name.startswith(".")
    assert staged.suffix == ".txt"


def test_uploaded_attachment_prefers_real_finder_source_path(tmp_path: Path) -> None:
    source = tmp_path / "星丛测试附件.pages"
    source.write_bytes(b"pages-source")
    upload = SimpleNamespace(
        filename=source.name,
        size=source.stat().st_size,
        file=io.BytesIO(source.read_bytes()),
    )
    request = UiRequest(
        method="POST",
        path="tasks/task-1/attachments",
        query={},
        body={"file": upload, "originalPath": str(source)},
        idempotency_key="task-upload-source-path",
    )

    assert local_task_attachments._uploaded_original_path(request) == source.resolve()


def test_task_recording_human_name_reaches_project_metadata_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "strict-local.db"
    recording = tmp_path / "recordings" / "task-1" / "星丛供应链咨询.wav"
    recording.parent.mkdir(parents=True)
    recording.write_bytes(b"recording")
    metadata_payloads: list[dict[str, Any]] = []

    class Runtime:
        @staticmethod
        def cloud_command(_method, _path, *, payload, **_kwargs):
            metadata_payloads.append(dict(payload))
            return {"documents": [{"documentId": "document-recording-1"}]}

    Runtime.database_path = database_path

    class Store:
        @staticmethod
        def import_paths(*, paths, **_kwargs):
            source = Path(paths[0])
            return {
                "materials": [{
                    "localSourceId": "local-recording-1",
                    "fileName": source.name,
                    "contentHash": "recording-hash",
                    "byteSize": source.stat().st_size,
                    "mediaType": "audio/wav",
                }]
            }

        @staticmethod
        def bind_pending_materials(**_kwargs):
            return []

        @staticmethod
        def bind_cloud_documents(**_kwargs):
            return []

        @staticmethod
        def bind_task_attachment(**payload):
            return payload

    store = Store()
    monkeypatch.setattr(local_task_attachments, "_store", lambda _compatibility: store)
    monkeypatch.setattr(
        local_task_attachments,
        "_task",
        lambda *_args: {"id": "task-1", "clientId": "client-1"},
    )
    monkeypatch.setattr(
        local_task_attachments,
        "_task_ui",
        lambda _compatibility, task: task,
    )

    result = local_task_attachments.archive_task_recording(
        SimpleNamespace(runtime=Runtime()),
        UiRequest(
            method="POST",
            path="tasks/task-1/recordings",
            query={},
            body={"audioPath": str(recording)},
            idempotency_key="archive-human-recording-name",
        ),
        SimpleNamespace(group=lambda _name: "task-1"),
    )

    assert result["id"] == "task-1"
    assert metadata_payloads[0]["materials"][0]["fileName"] == "星丛供应链咨询.wav"


def test_task_attachment_delete_keeps_working_after_processing_kind_refresh(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "strict-local.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE source_assets (id TEXT, scope_id TEXT, client_id TEXT, "
            "source_kind TEXT, source_locator_nonlocal TEXT, lifecycle_state TEXT, "
            "updated_at TEXT)"
        )
        connection.execute(
            "INSERT INTO source_assets VALUES (?,?,?,?,?,?,?)",
            (
                "cloud-document-1",
                "scope-1",
                "client-1",
                "local_original",
                "task:task-1",
                "active",
                "2026-08-24T00:00:00.000Z",
            ),
        )

    class Runtime:
        database_path = db_path

        @staticmethod
        @contextmanager
        def _connection():
            connection = sqlite3.connect(db_path)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()

        @staticmethod
        def _local_object_scope_id(_connection, _sandbox_id):
            return "scope-1"

    store = LocalProjectMaterialsRepository(
        Runtime(),
        context_provider=lambda: SimpleNamespace(sandbox_id="sandbox-1"),
    )
    deleted: list[tuple[str, str]] = []
    store.delete_document_local = (  # type: ignore[method-assign]
        lambda project_id, document_id: (
            deleted.append((project_id, document_id))
            or {"deleted": True}
        )
    )

    result = store.delete_task_attachment_local(
        task_id="task-1",
        attachment_id="cloud-document-1",
    )

    assert result == {"deleted": True}
    assert deleted == [("client-1", "cloud-document-1")]


def test_task_transcription_starts_in_background_without_blocking_task_save(
    tmp_path: Path,
    monkeypatch,
) -> None:
    release_worker = threading.Event()
    completed = threading.Event()

    class Runtime:
        database_path = tmp_path / "strict-local.db"

        @staticmethod
        def capture_sandbox_context():
            return SimpleNamespace(
                sandbox_id="sandbox-test",
                workspace_context=SimpleNamespace(sandbox_id="sandbox-test"),
            )

        @staticmethod
        @contextmanager
        def prebound_sandbox_context(_context):
            yield

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
        "register_and_process_local_materials",
        lambda **_kwargs: {
            "documentIds": ["cloud-transcript-1"],
            "cloudMetadataState": "ready",
            "overallState": "ready",
        },
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

    deleted = materials.delete_local_material_metadata(
        identity,
        project_id=payload["projectId"],
        document_id=attachment["id"],
        expected_version=int(attachment.get("version") or 1),
        idempotency_key="task-attachment-delete",
    )
    assert deleted["deleted"] is True
    assert task_domain.task_detail(identity, task_id=task_id)["task"]["attachments"] == []

    replayed = materials.delete_local_material_metadata(
        identity,
        project_id=payload["projectId"],
        document_id=attachment["id"],
        expected_version=int(attachment.get("version") or 1),
        idempotency_key="task-attachment-delete-retry",
    )
    assert replayed["deleted"] is True
    assert replayed["alreadyDeleted"] is True


def test_task_attachment_delete_accepts_cloud_only_placeholder(
    monkeypatch,
    tmp_path: Path,
) -> None:
    cloud_calls: list[dict[str, Any]] = []

    class Store:
        @staticmethod
        def task_attachments(_task_id: str) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr(
        local_task_attachments,
        "_task",
        lambda _compatibility, _task_id: {
            "id": "task-1",
            "clientId": "client-1",
            "attachments": [{"id": "cloud-document-1", "version": 4}],
        },
    )
    monkeypatch.setattr(local_task_attachments, "_store", lambda _compatibility: Store())
    monkeypatch.setattr(
        local_task_attachments,
        "replayable_generated_value",
        lambda _runtime, **kwargs: kwargs["generate"](),
    )
    monkeypatch.setattr(
        local_task_attachments,
        "replayable_cloud_mutation",
        lambda _runtime, **kwargs: cloud_calls.append(kwargs) or {"deleted": True},
    )
    compatibility = SimpleNamespace(runtime=SimpleNamespace(database_path=tmp_path / "local.db"))
    match = SimpleNamespace(
        group=lambda name: {
            "task_id": "task-1",
            "attachment_id": "cloud-document-1",
        }[name]
    )

    result = local_task_attachments.delete_task_attachment(
        compatibility,
        UiRequest(
            method="DELETE",
            path="tasks/task-1/attachments/cloud-document-1",
            query={},
            body={},
            idempotency_key="delete-cloud-placeholder",
        ),
        match,
    )

    assert result == {"deleted": True, "knowledgeDeleted": True, "fileDeleted": True}
    assert len(cloud_calls) == 1
    assert cloud_calls[0]["cloud_payload_factory"]() == {"expectedVersion": 4}


def test_meeting_attachment_registers_only_safe_project_metadata(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    meeting = create_meeting(
        repository,
        identity,
        payload={
            "title": "核对会议附件",
            "clientId": payload["projectId"],
            "startsAt": "2026-08-13T09:00:00+08:00",
            "endsAt": "2026-08-13T10:00:00+08:00",
        },
        idempotency_key="meeting-attachment-create",
    )["meeting"]
    materials = GC07ProjectMaterialsRepository(repository)
    registered = materials.register_local_material_metadata(
        identity,
        project_id=payload["projectId"],
        materials=[{
            "localSourceId": "local-meeting-file-1",
            "fileName": "会议资料.txt",
            "contentHash": "d" * 64,
            "byteSize": 22,
            "mediaType": "text/plain",
            "relationKind": "meeting",
            "relationId": meeting["id"],
        }],
        idempotency_key="meeting-attachment-metadata",
    )
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT source_kind,source_locator_nonlocal FROM source_assets WHERE id=?",
            (registered["documents"][0]["documentId"],),
        ).fetchone()
        assert tuple(row) == ("meeting_attachment_metadata", f"meeting:{meeting['id']}")
        assert len(user_tables(connection)) == 88
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_transcript_material_registers_metadata_then_settles_local_processing() -> None:
    calls: list[tuple[str, object]] = []

    class Runtime:
        @staticmethod
        def cloud_command(method, path, *, payload, idempotency_key, refresh_business):
            calls.append(("cloud", payload))
            assert method == "POST"
            assert path.endswith("/materials/register-metadata")
            assert idempotency_key == "transcript-once:metadata"
            assert refresh_business is False
            material = payload["materials"][0]
            assert "managedPath" not in material
            assert "originalSourcePath" not in material
            return {
                "documents": [{
                    "documentId": "cloud-transcript-1",
                    "localSourceId": material["localSourceId"],
                    "version": 1,
                }]
            }

    class Store:
        @staticmethod
        def bind_pending_materials(**_kwargs):
            calls.append(("pending", None))

        @staticmethod
        def bind_cloud_documents(**_kwargs):
            calls.append(("bound", _kwargs["cloud_documents"][0]["documentId"]))

        @staticmethod
        def process_pending_documents(**_kwargs):
            calls.append(("processed", tuple(_kwargs["document_ids"])))
            return {
                "items": [{
                    "documentId": "cloud-transcript-1",
                    "parseStatus": "ready",
                    "wikiStatus": "ready",
                }]
            }

        @staticmethod
        def documents(_project_id):
            return []

    result = register_and_process_local_materials(
        runtime=Runtime(),
        store=Store(),
        project_id="client-1",
        local_materials=[{
            "localSourceId": "local-transcript-1",
            "fileName": "访谈-录音转写.txt",
            "contentHash": "a" * 64,
            "byteSize": 18,
            "mediaType": "text/plain",
            "managedPath": "/private/device/transcript.txt",
            "originalSourcePath": "/private/device/source.txt",
        }],
        relation_kind="task",
        relation_id="task-1",
        idempotency_key="transcript-once",
    )
    assert result["documentIds"] == ["cloud-transcript-1"]
    assert result["cloudMetadataState"] == "ready"
    assert result["overallState"] == "ready"
    assert [item[0] for item in calls] == ["pending", "cloud", "bound", "processed"]


def test_task_attachment_cloud_failure_keeps_local_attachment_visible(monkeypatch) -> None:
    calls: list[str] = []

    class Runtime:
        @staticmethod
        def cloud_command(*_args, **_kwargs):
            raise local_task_attachments.LocalRuntimeError(
                503,
                "cloud_temporarily_unavailable",
                "组织云暂时不可用",
            )

    class Store:
        @staticmethod
        def bind_pending_materials(**_kwargs):
            calls.append("pending")

        @staticmethod
        def bind_task_attachment(**payload):
            calls.append("bound")
            return {
                "id": "local-source-1",
                "taskId": payload["task_id"],
                "title": "测试附件.txt",
                "path": "/private/device/测试附件.txt",
                "cloudMetadataState": "pending",
            }

    monkeypatch.setattr(local_task_attachments, "_store", lambda _compatibility: Store())
    result = local_task_attachments._register(
        SimpleNamespace(runtime=Runtime()),
        UiRequest(
            method="POST",
            path="tasks/task-1/attachments",
            query={},
            body={},
            idempotency_key="task-attachment-local-first",
        ),
        task_id="task-1",
        task={"id": "task-1", "client_id": "client-1"},
        local={
            "localSourceId": "local-source-1",
            "fileName": "测试附件.txt",
            "contentHash": "e" * 64,
            "byteSize": 12,
            "mediaType": "text/plain",
        },
    )
    assert calls == ["pending", "bound"]
    assert result["path"] == "/private/device/测试附件.txt"
    assert result["cloudMetadataState"] == "failed_retryable"
    assert result["syncMessage"] == "已保存到任务，项目工作台同步待重试"


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
