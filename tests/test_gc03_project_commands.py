from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from cloud_backend.app.main import create_app
from strict_common.schema import runtime_connection
from tests.test_gc01_authorization import _auth, _seed_gc01_cloud


def test_project_command_persists_public_website_locator_without_capture(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)
    with TestClient(create_app(config)) as client:
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc03-project-create",
            },
            json={
                "name": "官网登记测试",
                "officialWebsiteUrl": "HTTPS://Example.COM",
            },
        )
        assert created.status_code == 201, created.text
        project = created.json()["project"]
        assert project["officialWebsiteUrl"] == "https://example.com/"
        assert project["documentCount"] == 0

        updated = client.put(
            "/api/v2/domain/project-materials/projects/" + project["projectId"],
            headers={
                **_auth(tokens["admin"]),
                "Idempotency-Key": "gc03-project-update",
            },
            json={
                "expectedVersion": 1,
                "officialWebsiteUrl": "https://example.org/path#ignored",
            },
        )
        assert updated.status_code == 200, updated.text
        assert (
            updated.json()["project"]["officialWebsiteUrl"]
            == "https://example.org/path"
        )

    with runtime_connection(database, "cloud", read_only=True) as connection:
        row = connection.execute(
            """
            SELECT source_locator_nonlocal, availability_state
            FROM source_assets
            WHERE client_id=? AND source_kind='official_website'
              AND lifecycle_state='active'
            """,
            (project["projectId"],),
        ).fetchone()
        assert tuple(row) == ("https://example.org/path", "registered")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
