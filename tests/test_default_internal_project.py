from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType

from fastapi.testclient import TestClient

# The current checkout already references an untracked mobile-sync route module.
# Keep this focused contract test independent from that pre-existing import gap.
for module_name, registrar_name in (
    ("mobile_sync", "register_mobile_sync_routes"),
    ("mobile_consult", "register_mobile_consult_routes"),
    ("mobile_devices", "register_mobile_device_routes"),
    ("mobile_link_transfers", "register_mobile_link_transfer_routes"),
):
    module = ModuleType(f"cloud_backend.app.domain_routes.{module_name}")
    setattr(module, registrar_name, lambda *_args, **_kwargs: None)
    sys.modules.setdefault(f"cloud_backend.app.domain_routes.{module_name}", module)

from cloud_backend.app.main import create_app
from strict_common.schema import runtime_connection
from tests.test_gc01_authorization import _auth, _seed_gc01_cloud


def test_admin_can_set_one_idempotent_default_internal_project_without_new_tables(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)

    with TestClient(create_app(config)) as client:
        headers = _auth(tokens["admin"])
        first = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={**headers, "Idempotency-Key": "create-company-project"},
            json={"name": "星丛"},
        )
        second = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={**headers, "Idempotency-Key": "create-other-project"},
            json={"name": "其他项目"},
        )
        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        first_project = first.json()["project"]
        second_project = second.json()["project"]

        selected = client.put(
            f"/api/v2/domain/project-materials/projects/{first_project['projectId']}/default-internal",
            headers={**headers, "Idempotency-Key": "set-company-default"},
            json={"expectedVersion": first_project["version"]},
        )
        assert selected.status_code == 200, selected.text
        assert selected.json()["project"]["isDefaultInternalProject"] is True

        replay = client.put(
            f"/api/v2/domain/project-materials/projects/{first_project['projectId']}/default-internal",
            headers={**headers, "Idempotency-Key": "set-company-default"},
            json={"expectedVersion": first_project["version"]},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json() == selected.json()

        switched = client.put(
            f"/api/v2/domain/project-materials/projects/{second_project['projectId']}/default-internal",
            headers={**headers, "Idempotency-Key": "set-other-default"},
            json={"expectedVersion": second_project["version"]},
        )
        assert switched.status_code == 200, switched.text

        projects = client.get(
            "/api/v2/domain/project-materials/projects",
            headers=headers,
        ).json()["projects"]
        defaults = [item for item in projects if item["isDefaultInternalProject"]]
        assert [item["projectId"] for item in defaults] == [second_project["projectId"]]

    with runtime_connection(database, "cloud", read_only=True) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0] == 88
        assert connection.execute(
            "SELECT COUNT(*) FROM commands WHERE command_type='client.default_internal_project_set'"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM outbox_events WHERE event_type='client.default_internal_project_set'"
        ).fetchone()[0] == 2


def test_non_admin_cannot_change_the_organization_default_project(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    config, tokens = _seed_gc01_cloud(database)

    with TestClient(create_app(config)) as client:
        admin_headers = _auth(tokens["admin"])
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={**admin_headers, "Idempotency-Key": "create-admin-project"},
            json={"name": "组织项目"},
        )
        project = created.json()["project"]
        response = client.put(
            f"/api/v2/domain/project-materials/projects/{project['projectId']}/default-internal",
            headers={**_auth(tokens["member"]), "Idempotency-Key": "member-default-denied"},
            json={"expectedVersion": project["version"]},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == "organization_admin_required"
