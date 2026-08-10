from __future__ import annotations

from pathlib import Path

import pytest

from cloud_backend.app.repositories.gc03_scope import (
    validate_meeting_client_binding,
    validate_task_client_binding,
)
from cloud_backend.app.repository import RepositoryError
from strict_common.ids import utc_now
from strict_common.schema import runtime_connection
from tests.test_gc01_authorization import _seed_gc01_cloud


def _seed_clients_and_line(database: Path) -> None:
    now = utc_now()
    with runtime_connection(database, "cloud") as connection:
        for client_id in ("client_a", "client_b"):
            connection.execute(
                """
                INSERT INTO secured_resources (
                    id, scope_id, resource_kind, lifecycle_state, version,
                    resource_type_key, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, 'scope_gc01_test', 'client', 'active', 1,
                          'client', ?, ?, NULL, 'cloud', 'cli_gc01_test')
                """,
                (client_id, now, now),
            )
            connection.execute(
                """
                INSERT INTO clients (
                    id, scope_id, owner_membership_id,
                    lifecycle_state, version, name,
                    created_at, updated_at, deleted_at
                ) VALUES (?, 'scope_gc01_test', 'membership_admin',
                          'active', 1, ?, ?, ?, NULL)
                """,
                (client_id, client_id, now, now),
            )
        connection.execute(
            """
            INSERT INTO secured_resources (
                id, scope_id, resource_kind, lifecycle_state, version,
                resource_type_key, created_at, updated_at, deleted_at,
                authority_role, origin_instance_id
            ) VALUES ('line_a', 'scope_gc01_test', 'event_line', 'active', 1,
                      'event_line', ?, ?, NULL, 'cloud', 'cli_gc01_test')
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO event_lines (
                id, scope_id, client_id, lifecycle_state, version,
                record_kind, name, created_at, updated_at, deleted_at
            ) VALUES ('line_a', 'scope_gc01_test', 'client_a', 'active', 1,
                      'line', 'A事件线', ?, ?, NULL)
            """,
            (now, now),
        )
        connection.commit()


def test_task_binding_allows_unscoped_organization_task_and_matching_line(
    tmp_path: Path,
) -> None:
    database = tmp_path / "strict-cloud.db"
    _seed_gc01_cloud(database)
    _seed_clients_and_line(database)
    with runtime_connection(database, "cloud") as connection:
        assert validate_task_client_binding(
            connection,
            scope_id="scope_gc01_test",
            client_id=None,
            event_line_id=None,
        ).client_id is None
        binding = validate_task_client_binding(
            connection,
            scope_id="scope_gc01_test",
            client_id="client_a",
            event_line_id="line_a",
        )
    assert binding.client_id == "client_a"
    assert binding.event_line_id == "line_a"


def test_task_and_meeting_reject_cross_project_event_line(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    _seed_gc01_cloud(database)
    _seed_clients_and_line(database)
    with runtime_connection(database, "cloud") as connection:
        with pytest.raises(RepositoryError, match="任务与事件线不属于同一项目"):
            validate_task_client_binding(
                connection,
                scope_id="scope_gc01_test",
                client_id="client_b",
                event_line_id="line_a",
            )
        with pytest.raises(RepositoryError, match="会议与事件线不属于同一项目"):
            validate_meeting_client_binding(
                connection,
                scope_id="scope_gc01_test",
                client_id="client_b",
                event_line_id="line_a",
            )


def test_meeting_requires_a_real_client(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    _seed_gc01_cloud(database)
    _seed_clients_and_line(database)
    with runtime_connection(database, "cloud") as connection:
        with pytest.raises(RepositoryError, match="请选择项目"):
            validate_meeting_client_binding(
                connection,
                scope_id="scope_gc01_test",
                client_id=None,
                event_line_id=None,
            )
