from __future__ import annotations

import hashlib
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from strict_common.contracts import LOCAL_CONTRACT
from strict_common.offline_upgrade import ensure_local_database_current
from strict_common.physical_schema import ddl_from_manifest, ddl_sha256
from strict_common.schema import initialize_database, verify_database


LEGACY_IDENTITIES = {
    8: (
        "19971bd3a3e1cf9beecdb5893b2b15fd6bc02c8951795fc828105ab481f20432",
        "34a81aecbaad520dd451eb474ac617171634db0f32994ed3abcbe64cd6753d75",
    ),
    9: (
        "3b55180712dac2fac2e4257937aecc3afc583398fc61a8953ed390d82cf21d39",
        "24239764b640dd5e16bb9cfa5fe693858df6015a8831c1446d64f534008dee16",
    ),
}


def _table(raw: dict, name: str) -> dict:
    return next(item for item in raw["allowedTables"] if item["name"] == name)


def _remove_fields(table: dict, names: set[str]) -> None:
    table["fields"] = [field for field in table["fields"] if field["name"] not in names]


def _insert_after(table: dict, after: str, field: dict) -> None:
    index = next(index for index, item in enumerate(table["fields"]) if item["name"] == after)
    table["fields"].insert(index + 1, field)


def _legacy_manifest(version: int) -> dict:
    raw = deepcopy(LOCAL_CONTRACT.raw)
    recordings = _table(raw, "recordings")
    _remove_fields(recordings, {"binding_kind", "task_id", "client_id", "event_line_id"})
    next(field for field in recordings["fields"] if field["name"] == "meeting_id")["nullable"] = False
    recordings["check_constraints"] = [
        check
        for check in recordings["check_constraints"]
        if not check["name"].startswith("ck_recording_binding")
    ]
    if version == 9:
        assert ddl_sha256(raw) == LEGACY_IDENTITIES[9][1]
        return raw

    decisions = _table(raw, "decision_actions")
    _insert_after(
        decisions,
        "source_set_id",
        {
            "name": "task_id",
            "type": "TEXT",
            "nullable": True,
            "default": None,
            "primary_key": False,
            "reference": {
                "kind": "foreign_key",
                "target_table": "tasks",
                "target_field": "id",
                "on_delete": "RESTRICT",
                "source": "published-v8",
            },
        },
    )
    decisions["unique_constraints"].append(
        {
            "name": "uq_decision_actions_01",
            "fields": ["task_id"],
            "where": "task_id IS NOT NULL",
        }
    )
    decisions["check_constraints"].insert(
        -1,
        {
            "name": "ck_primary_task_unique_role",
            "expression": "task_id IS NULL OR record_kind IN ('decision','plan_action')",
        },
    )
    _remove_fields(_table(raw, "meetings"), {"planning_cycle_id"})
    _remove_fields(_table(raw, "tasks"), {"planning_cycle_id"})
    _insert_after(
        _table(raw, "planning_cycles"),
        "client_id",
        {
            "name": "parent_plan_id",
            "type": "TEXT",
            "nullable": True,
            "default": None,
            "primary_key": False,
            "reference": {
                "kind": "foreign_key",
                "target_table": "planning_cycles",
                "target_field": "id",
                "on_delete": "RESTRICT",
                "source": "published-v8",
            },
        },
    )
    assert ddl_sha256(raw) == LEGACY_IDENTITIES[8][1]
    return raw


def _create_legacy_database(path: Path, version: int) -> None:
    manifest_hash, migration_hash = LEGACY_IDENTITIES[version]
    connection = sqlite3.connect(path)
    try:
        connection.executescript(ddl_from_manifest(_legacy_manifest(version)))
        connection.execute(
            """
            INSERT INTO schema_versions (
                id, engine, version, checksum, status, database_role,
                schema_family, manifest_hash, migration_set_hash, build_id,
                created_at, activated_at, authority_role, origin_instance_id,
                database_generation_id
            ) VALUES (
                'legacy-build', 'sqlite', ?, ?, 'active',
                'local_blueprint_88_authority_and_projection', 'yiyu-blueprint-88-v1',
                ?, ?, 'legacy-build', '2026-08-01T00:00:00Z',
                '2026-08-01T00:00:00Z', 'local', 'generation-preserved',
                'generation-preserved'
            )
            """,
            (version, migration_hash, manifest_hash, migration_hash),
        )
        connection.execute(
            """
            INSERT INTO state_registry (
                id, record_kind, lifecycle_state, created_at, updated_at
            ) VALUES ('sentinel-row', 'runtime_state', 'active',
                      '2026-08-01T00:00:00Z', '2026-08-01T00:00:00Z')
            """
        )
        connection.execute(f"PRAGMA user_version={version}")
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize("version", [8, 9])
def test_known_legacy_local_database_is_rebuilt_and_preserved(
    tmp_path: Path,
    version: int,
) -> None:
    database = tmp_path / "strict-local.db"
    _create_legacy_database(database, version)

    result = ensure_local_database_current(database)

    assert result.upgraded is True
    assert result.from_version == version
    assert result.to_version == int(LOCAL_CONTRACT.contract_version)
    assert result.backup_path is not None and result.backup_path.is_file()
    assert result.backup_path.parent == tmp_path / "migration-backups"
    identity = verify_database(database, "local")
    assert identity.database_generation_id == "generation-preserved"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM state_registry WHERE id='sentinel-row'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT status FROM schema_versions WHERE id='legacy-build'"
        ).fetchone()[0] == "superseded"
        assert connection.execute(
            "SELECT COUNT(*) FROM migration_ledger WHERE from_version=? AND to_version='10'",
            (str(version),),
        ).fetchone()[0] == 1


def test_unknown_legacy_identity_is_rejected_without_touching_source(tmp_path: Path) -> None:
    database = tmp_path / "strict-local.db"
    _create_legacy_database(database, 9)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_versions SET manifest_hash='unknown' WHERE status='active'"
        )
        connection.commit()
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="unsupported strict local database identity"):
        ensure_local_database_current(database)

    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert not (tmp_path / "migration-backups").exists()


def test_current_database_is_noop_and_creates_no_backup(tmp_path: Path) -> None:
    database = tmp_path / "strict-local.db"
    initialize_database(database, "local")
    before = hashlib.sha256(database.read_bytes()).hexdigest()

    result = ensure_local_database_current(database)

    assert result.upgraded is False
    assert result.from_version == 10
    assert result.to_version == 10
    assert result.backup_path is None
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert not (tmp_path / "migration-backups").exists()
