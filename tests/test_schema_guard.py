from __future__ import annotations

import json
from concurrent.futures import ProcessPoolExecutor
import sqlite3
from pathlib import Path

import pytest

from strict_common.contracts import CLOUD_CONTRACT, LOCAL_CONTRACT
from strict_common.schema import (
    initialize_database,
    runtime_connection,
    verify_database,
)


def _initialize_in_process(arguments: tuple[str, str]) -> str:
    database_path, role = arguments
    return initialize_database(Path(database_path), role).manifest_hash


@pytest.mark.parametrize(
    ("role", "contract"),
    [("local", LOCAL_CONTRACT), ("cloud", CLOUD_CONTRACT)],
)
def test_genesis_matches_frozen_manifest(tmp_path: Path, role, contract) -> None:
    database = tmp_path / f"{role}.db"
    first = initialize_database(database, role)
    second = initialize_database(database, role)
    assert first == second
    assert first.manifest_hash == contract.manifest_hash
    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        assert tables == set(contract.allowed_tables)
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_genesis_recovers_a_valid_empty_sqlite_file(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE residue (id TEXT)")
        connection.execute("DROP TABLE residue")
        connection.execute("VACUUM")
    assert database.stat().st_size > 0

    identity = initialize_database(database, "cloud")

    assert identity.manifest_hash == CLOUD_CONTRACT.manifest_hash
    assert verify_database(database, "cloud") == identity


def test_genesis_is_safe_across_concurrent_processes(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    arguments = [(str(database), "cloud")] * 4

    with ProcessPoolExecutor(max_workers=4) as pool:
        hashes = list(pool.map(_initialize_in_process, arguments))

    assert hashes == [CLOUD_CONTRACT.manifest_hash] * 4
    assert database.exists()
    assert verify_database(database, "cloud").manifest_hash == CLOUD_CONTRACT.manifest_hash


def test_failed_genesis_preserves_sqlite_for_forensics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import strict_common.schema as schema_module

    database = tmp_path / "strict-cloud.db"

    def reject_layout(*_args, **_kwargs) -> None:
        raise RuntimeError("forced verification failure")

    monkeypatch.setattr(schema_module, "_verify_layout", reject_layout)
    with pytest.raises(RuntimeError, match="forced verification failure"):
        initialize_database(database, "cloud")

    assert database.exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_runtime_rejects_ddl_attach_and_unknown_schema(tmp_path: Path) -> None:
    database = tmp_path / "strict-local.db"
    initialize_database(database, "local")
    with runtime_connection(database, "local") as connection:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("CREATE TABLE surprise (id TEXT)")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("ATTACH DATABASE ':memory:' AS legacy")

    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE legacy_tasks (id TEXT)")
        connection.commit()
    with pytest.raises(RuntimeError, match="table mismatch"):
        verify_database(database, "local")


def test_runtime_never_initializes_missing_database(tmp_path: Path) -> None:
    database = tmp_path / "missing.db"
    with pytest.raises(RuntimeError, match="does not exist"):
        with runtime_connection(database, "local"):
            pass
    assert not database.exists()


def test_runtime_sql_audit_is_opt_in_and_redacts_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "strict-local.db"
    audit_file = tmp_path / "sql-audit.jsonl"
    initialize_database(database, "local")
    monkeypatch.setenv("YIYU_STRICT_SQL_AUDIT_FILE", str(audit_file))

    sentinel = "secret-value-must-not-appear"
    with runtime_connection(database, "local") as connection:
        connection.execute(
            "SELECT id FROM backup_catalog WHERE backup_ref = ?",
            (sentinel,),
        ).fetchall()

    records = [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
    ]
    assert records
    assert any(record["statementKind"] == "SELECT" for record in records)
    assert all(record["ddl"] is False for record in records)
    assert all(record["databasePath"] == str(database) for record in records)
    assert sentinel not in audit_file.read_text(encoding="utf-8")


def test_required_manifest_columns_are_enforced(tmp_path: Path) -> None:
    database = tmp_path / "strict-cloud.db"
    initialize_database(database, "cloud")
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE backup_catalog DROP COLUMN authority_role")
        connection.commit()
    with pytest.raises(RuntimeError, match="required columns missing"):
        verify_database(database, "cloud")
