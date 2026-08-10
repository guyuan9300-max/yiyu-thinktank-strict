from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from cloud_backend.app.main import create_app
from strict_common.schema import runtime_connection
from tests.test_gc01_authorization import _auth, _seed_gc01_cloud


SAFE_ENTRY = {
    "memoryId": "memory_gc02_safe_01",
    "memoryKind": "favorite",
    "version": 1,
    "contentHash": "a" * 64,
    "updatedAt": "2026-08-06T16:00:00.000Z",
}


def test_gc02_memory_sync_uploads_only_safe_manifest_and_is_member_scoped(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "memory-project-create"},
            json={
                "name": "记忆安全摘要项目",
                "participantMembershipIds": ["membership_member"],
            },
        )
        assert created.status_code == 201, created.text
        project = created.json()["project"]
        project_id = project["projectId"]
        path = f"/api/v2/workbench/projects/{project_id}/memory-manifest"

        initial = client.get(path, headers=_auth(tokens["admin"]))
        assert initial.status_code == 200
        assert initial.json()["cloudState"] == "not_connected"
        assert initial.json()["entries"] == []

        headers = {**_auth(tokens["admin"]), "Idempotency-Key": "memory-safe-sync-1"}
        synchronized = client.put(
            path,
            headers=headers,
            json={"entries": [SAFE_ENTRY], "expectedVersion": 0},
        )
        assert synchronized.status_code == 200, synchronized.text
        result = synchronized.json()
        assert result["cloudState"] == "ready"
        assert result["manifestVersion"] == 1
        assert result["entries"] == [SAFE_ENTRY]
        assert result["boundary"]["entryFields"] == [
            "memoryId",
            "memoryKind",
            "version",
            "contentHash",
            "updatedAt",
        ]

        replay = client.put(
            path,
            headers=headers,
            json={"entries": [SAFE_ENTRY], "expectedVersion": 0},
        )
        assert replay.status_code == 200
        assert replay.json()["idempotentReplay"] is True

        stale = client.put(
            path,
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "memory-safe-stale"},
            json={"entries": [SAFE_ENTRY], "expectedVersion": 0},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "memory_manifest_version_conflict"

        forbidden_body = client.put(
            path,
            headers={**_auth(tokens["admin"]), "Idempotency-Key": "memory-unsafe-body"},
            json={
                "entries": [SAFE_ENTRY],
                "expectedVersion": 1,
                "answerMarkdown": "不得上传的回答正文",
            },
        )
        assert forbidden_body.status_code == 422
        assert forbidden_body.json()["error"]["code"] == (
            "memory_manifest_payload_boundary_violation"
        )

        member_manifest = client.get(path, headers=_auth(tokens["member"]))
        assert member_manifest.status_code == 200
        assert member_manifest.json()["cloudState"] == "not_connected"
        assert member_manifest.json()["entries"] == []

        revoked = client.put(
            f"/api/v2/domain/project-materials/projects/{project_id}",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "memory-project-revoke-member",
            },
            json={
                "name": project["name"],
                "participantMembershipIds": [],
                "expectedVersion": 1,
            },
        )
        assert revoked.status_code == 200, revoked.text
        denied = client.get(path, headers=_auth(tokens["member"]))
        assert denied.status_code == 404

    with runtime_connection(database, "cloud") as connection:
        aggregate = connection.execute(
            "SELECT storage_key,content_hash,receipt,holder_role,holder_instance_id,"
            "storage_kind,availability_state FROM object_manifests "
            "WHERE storage_kind='member_memory_safe_manifest'"
        ).fetchone()
        assert aggregate is not None
        assert aggregate["storage_key"] is None
        assert aggregate["holder_instance_id"] == "membership_admin"
        assert str(aggregate["holder_role"]).startswith(
            "member_memory_safe_manifest:"
        )
        receipt = json.loads(aggregate["receipt"])
        assert set(receipt) == {"schema", "manifestVersion", "entries"}
        assert set(receipt["entries"][0]) == {
            "memoryId",
            "memoryKind",
            "version",
            "contentHash",
            "updatedAt",
        }
        serialized = json.dumps(receipt, ensure_ascii=False)
        for forbidden in (
            "answerMarkdown",
            "fileBody",
            "localPath",
            "/Users/",
            "apiKey",
            "password",
        ):
            assert forbidden not in serialized
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE command_type='member_memory.safe_manifest.synced'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM inbox_receipts WHERE result_status='completed'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM reconciliation_runs "
            "WHERE reconciliation_kind='member_memory_safe_manifest_v1' AND status='completed'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0] == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
