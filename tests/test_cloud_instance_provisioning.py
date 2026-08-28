from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from cloud_backend.app.provisioning import provision_cloud_instance
from scripts.backfill_gc01_authorization import backfill
from strict_common.schema import runtime_connection


def _config(tmp_path: Path, database: Path, cloud_instance_id: str | None) -> CloudConfig:
    return CloudConfig(
        data_dir=tmp_path,
        database_path=database,
        bootstrap_token="bootstrap-test",
        master_key=Fernet.generate_key().decode(),
        cloud_instance_id=cloud_instance_id,
    )


def test_fresh_provision_and_retry_are_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"

    first = provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")
    second = provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")

    assert first.cloud_instance_id == "cloud-a"
    assert first.created is True
    assert second.cloud_instance_id == "cloud-a"
    assert second.created is False


def test_provision_rejects_wrong_or_archived_identity(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")

    with pytest.raises(RuntimeError, match="does not match"):
        provision_cloud_instance(database, expected_cloud_instance_id="cloud-b")

    with runtime_connection(database, "cloud") as connection:
        connection.execute(
            "UPDATE state_registry SET lifecycle_state='archived' "
            "WHERE record_kind='cloud_instance'"
        )
        connection.commit()
    with pytest.raises(RuntimeError, match="exists but is not active"):
        provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")


def test_provision_rejects_multiple_active_identities(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")
    with runtime_connection(database, "cloud") as connection:
        original = dict(
            connection.execute(
                "SELECT * FROM state_registry WHERE record_kind='cloud_instance'"
            ).fetchone()
        )
        original["id"] = "second-cloud-instance-row"
        original["state_id"] = "cloud-b"
        columns = list(original)
        connection.execute(
            f"INSERT INTO state_registry ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            [original[column] for column in columns],
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="multiple active"):
        provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")


def test_concurrent_provision_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: provision_cloud_instance(
                    database,
                    expected_cloud_instance_id="cloud-a",
                ),
                range(2),
            )
        )

    assert sorted(result.created for result in results) == [False, True]


def test_runtime_requires_one_explicit_matching_identity(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")

    with pytest.raises(RuntimeError, match="configured cloud instance id is required"):
        create_app(_config(tmp_path, database, None))
    with pytest.raises(RuntimeError, match="does not match"):
        create_app(_config(tmp_path, database, "cloud-b"))

    app = create_app(_config(tmp_path, database, "cloud-a"))
    assert app.state.repository.cloud_instance_id == "cloud-a"


def test_authorization_backfill_rejects_mismatched_identity(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")

    with pytest.raises(RuntimeError, match="does not match"):
        backfill(database, "cloud-b")


def test_environment_config_requires_cloud_instance_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("YIYU_STRICT_CLOUD_BOOTSTRAP_TOKEN", "bootstrap-test")
    monkeypatch.setenv("YIYU_STRICT_CLOUD_MASTER_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("YIYU_STRICT_CLOUD_INSTANCE_ID", raising=False)

    with pytest.raises(RuntimeError, match="YIYU_STRICT_CLOUD_INSTANCE_ID is required"):
        CloudConfig.load(tmp_path)

    monkeypatch.setenv("YIYU_STRICT_CLOUD_INSTANCE_ID", "cloud-a")
    assert CloudConfig.load(tmp_path).cloud_instance_id == "cloud-a"


def test_organization_bootstrap_reports_not_connected_without_writing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    provision_cloud_instance(database, expected_cloud_instance_id="cloud-a")
    app = create_app(_config(tmp_path, database, "cloud-a"))

    with TestClient(app) as client:
        denied = client.post(
            "/api/v2/auth/bootstrap-organization",
            json={
                "organizationName": "测试组织",
                "displayName": "管理员",
                "email": "admin@example.com",
                "password": "12345678",
                "bootstrapToken": "wrong",
            },
        )
        assert denied.status_code == 403
        response = client.post(
            "/api/v2/auth/bootstrap-organization",
            json={
                "organizationName": "测试组织",
                "displayName": "管理员",
                "email": "admin@example.com",
                "password": "12345678",
                "bootstrapToken": "bootstrap-test",
            },
        )

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "organization_bootstrap_not_connected"
    with runtime_connection(database, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0] == 0
