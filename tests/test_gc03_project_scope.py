from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.activate_gc03_scope_decision import activate
from strict_common.project_scope import PROJECT_SCOPE_DECISION
from strict_common.schema import initialize_database


@pytest.mark.parametrize("role", ["local", "cloud"])
def test_genesis_seeds_only_the_build_level_project_scope_adr(
    tmp_path: Path,
    role: str,
) -> None:
    database = tmp_path / f"strict-{role}.db"
    initialize_database(database, role)  # type: ignore[arg-type]
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT * FROM project_scope_decisions"
        ).fetchall()
        columns = {
            str(row[1])
            for row in connection.execute(
                "PRAGMA table_info(project_scope_decisions)"
            )
        }
    assert len(rows) == 1
    assert rows[0]["decision_key"] == PROJECT_SCOPE_DECISION["decision_key"]
    assert rows[0]["approved_option"] == "client_is_project"
    assert "scope_id" not in columns
    assert "client_id" not in columns


def test_offline_activation_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    initialize_database(database, "cloud")
    activate(database, "cloud")
    activate(database, "cloud")
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM project_scope_decisions"
        ).fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    assert count == 1
    assert foreign_keys == []
