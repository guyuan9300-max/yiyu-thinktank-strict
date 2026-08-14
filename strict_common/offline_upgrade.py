from __future__ import annotations

import os
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from .contracts import LOCAL_CONTRACT
from .ids import new_id, sha256_text, utc_now
from .physical_schema import ddl_from_manifest, ddl_sha256, user_tables
from .schema import verify_database


_LEGACY_IDENTITIES = {
    8: (
        "19971bd3a3e1cf9beecdb5893b2b15fd6bc02c8951795fc828105ab481f20432",
        "34a81aecbaad520dd451eb474ac617171634db0f32994ed3abcbe64cd6753d75",
    ),
    9: (
        "3b55180712dac2fac2e4257937aecc3afc583398fc61a8953ed390d82cf21d39",
        "24239764b640dd5e16bb9cfa5fe693858df6015a8831c1446d64f534008dee16",
    ),
}
_LOCAL_ROLE = "local_blueprint_88_authority_and_projection"
_SCHEMA_FAMILY = "yiyu-blueprint-88-v1"


@dataclass(frozen=True)
class OfflineUpgradeResult:
    upgraded: bool
    from_version: int | None
    to_version: int
    backup_path: Path | None
    rows_copied: int = 0


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({_quote(table)})")
    ]


def _active_identity(connection: sqlite3.Connection) -> sqlite3.Row:
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """
        SELECT * FROM schema_versions
        WHERE status='active'
        ORDER BY activated_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("strict local database has no active identity")
    return row


def _inspect_source(path: Path) -> tuple[int, sqlite3.Row]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise RuntimeError("strict local database quick_check failed")
        if len(user_tables(connection)) != 88:
            raise RuntimeError("strict local database must contain exactly 88 tables")
        identity = _active_identity(connection)
        version = int(identity["version"])
        expected = _LEGACY_IDENTITIES.get(version)
        if expected is None:
            raise RuntimeError(
                "unsupported strict local database identity: "
                f"version={version}"
            )
        actual = (str(identity["manifest_hash"]), str(identity["migration_set_hash"]))
        if (
            str(identity["database_role"]) != _LOCAL_ROLE
            or str(identity["schema_family"]) != _SCHEMA_FAMILY
            or actual != expected
        ):
            raise RuntimeError(
                "unsupported strict local database identity: "
                f"version={version} manifest={actual[0]} migration={actual[1]}"
            )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"strict local database has {len(violations)} foreign key violations"
            )
        return version, identity
    finally:
        connection.close()


def _backup_database(source_path: Path, version: int) -> Path:
    backup_dir = source_path.parent / "migration-backups"
    backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"{source_path.stem}-v{version}-pre-v10-{stamp}.db"
    source = sqlite3.connect(source_path)
    target = sqlite3.connect(backup_path)
    try:
        source.execute("PRAGMA busy_timeout=10000")
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    backup_path.chmod(0o600)
    with sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True) as check:
        if str(check.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise RuntimeError("strict local migration backup quick_check failed")
    return backup_path


def _copy_table(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    table: str,
) -> int:
    source_columns = set(_columns(source, table))
    common = [name for name in _columns(target, table) if name in source_columns]
    if table == "recordings" and "binding_kind" not in source_columns:
        select_names = ",".join(_quote(name) for name in common)
        rows = source.execute(
            f"SELECT {select_names} FROM {_quote(table)}"
        ).fetchall()
        insert_names = [*common, "binding_kind"]
        placeholders = ",".join("?" for _ in insert_names)
        if rows:
            target.executemany(
                f"INSERT INTO {_quote(table)} "
                f"({','.join(_quote(name) for name in insert_names)}) "
                f"VALUES ({placeholders})",
                [tuple(row[name] for name in common) + ("meeting",) for row in rows],
            )
        return len(rows)

    names = ",".join(_quote(name) for name in common)
    rows = source.execute(f"SELECT {names} FROM {_quote(table)}").fetchall()
    if rows:
        target.executemany(
            f"INSERT INTO {_quote(table)} ({names}) "
            f"VALUES ({','.join('?' for _ in common)})",
            [tuple(row[name] for name in common) for row in rows],
        )
    return len(rows)


def _apply_v8_semantics(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    nested_plans = int(
        source.execute(
            "SELECT COUNT(*) FROM planning_cycles WHERE parent_plan_id IS NOT NULL"
        ).fetchone()[0]
    )
    if nested_plans:
        raise RuntimeError(
            "v8 local database contains parent plan relationships with no v10 "
            "lossless representation"
        )

    for row in source.execute(
        "SELECT task_id,planning_cycle_id FROM decision_actions "
        "WHERE task_id IS NOT NULL AND planning_cycle_id IS NOT NULL"
    ):
        updated = target.execute(
            "UPDATE tasks SET planning_cycle_id=? "
            "WHERE id=? AND planning_cycle_id IS NULL",
            (row["planning_cycle_id"], row["task_id"]),
        ).rowcount
        if updated != 1:
            raise RuntimeError(
                f"cannot preserve v8 task-plan relationship task={row['task_id']}"
            )

    meeting_links: dict[str, str] = {}
    for row in source.execute(
        """
        SELECT dl.derivative_object_id AS meeting_id,
               sm.source_object_id AS planning_cycle_id
        FROM derivation_lineage dl
        JOIN source_set_members sm
          ON sm.source_set_id=dl.source_set_id AND sm.scope_id=dl.scope_id
        WHERE dl.derivative_kind='meeting_plan_link'
          AND sm.source_object_kind='planning_cycle'
        """
    ):
        meeting_id = str(row["meeting_id"])
        planning_cycle_id = str(row["planning_cycle_id"])
        previous = meeting_links.setdefault(meeting_id, planning_cycle_id)
        if previous != planning_cycle_id:
            raise RuntimeError(
                f"conflicting v8 meeting-plan relationship meeting={meeting_id}"
            )
    for meeting_id, planning_cycle_id in meeting_links.items():
        updated = target.execute(
            "UPDATE meetings SET planning_cycle_id=? WHERE id=?",
            (planning_cycle_id, meeting_id),
        ).rowcount
        if updated != 1:
            raise RuntimeError(
                f"cannot preserve v8 meeting-plan relationship meeting={meeting_id}"
            )


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0])
        for table in sorted(LOCAL_CONTRACT.allowed_tables)
    }


def _build_v10(
    source_path: Path,
    target_path: Path,
    *,
    version: int,
    backup_path: Path,
) -> int:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(target_path)
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row
    try:
        source_counts = _row_counts(source)
        identity = _active_identity(source)
        target.execute("PRAGMA foreign_keys=OFF")
        target.execute("PRAGMA journal_mode=DELETE")
        target.executescript(ddl_from_manifest(LOCAL_CONTRACT.raw))
        target.execute("BEGIN IMMEDIATE")
        copied = 0
        for spec in LOCAL_CONTRACT.raw["allowedTables"]:
            copied += _copy_table(source, target, str(spec["name"]))
        if version == 8:
            _apply_v8_semantics(source, target)

        now = utc_now()
        build_id = new_id()
        migration_id = new_id()
        generation_id = str(identity["database_generation_id"])
        ddl_hash = ddl_sha256(LOCAL_CONTRACT.raw)
        target.execute(
            "UPDATE schema_versions SET status='superseded' WHERE status='active'"
        )
        target.execute(
            """
            INSERT INTO schema_versions (
                id,engine,version,checksum,status,database_role,schema_family,
                manifest_hash,migration_set_hash,build_id,created_at,activated_at,
                authority_role,origin_instance_id,database_generation_id
            ) VALUES (?, 'sqlite', 10, ?, 'active', ?, ?, ?, ?, ?, ?, ?,
                      'local', ?, ?)
            """,
            (
                build_id,
                ddl_hash,
                LOCAL_CONTRACT.database_role,
                LOCAL_CONTRACT.schema_family,
                LOCAL_CONTRACT.manifest_hash,
                ddl_hash,
                build_id,
                now,
                now,
                generation_id,
                generation_id,
            ),
        )
        integrity_hash = sha256_text(
            f"{migration_id}|{version}|10|{ddl_hash}|{now}"
        )
        target.execute(
            """
            INSERT INTO migration_ledger (
                id,schema_version_id,step,checksum,status,from_version,to_version,
                code_hash,started_at,completed_at,rollback_ref,origin_instance_id,
                created_at,integrity_hash,authority_role
            ) VALUES (?,?,'local_offline_rebuild_v10',?,'applied',?,'10',
                      ?,?,?,?,?,?,?,'local')
            """,
            (
                migration_id,
                build_id,
                ddl_hash,
                str(version),
                sha256_text("strict-local-v8-v9-to-v10-offline-rebuild-v1"),
                now,
                now,
                str(backup_path),
                generation_id,
                now,
                integrity_hash,
            ),
        )
        target.execute("PRAGMA user_version=10")
        target.commit()

        target_counts = _row_counts(target)
        for table, source_count in source_counts.items():
            expected = source_count
            if table in {"schema_versions", "migration_ledger"}:
                expected += 1
            if target_counts[table] != expected:
                raise RuntimeError(
                    f"strict local migration row-count mismatch table={table} "
                    f"source={source_count} target={target_counts[table]}"
                )
        target.execute("PRAGMA foreign_keys=ON")
        violations = target.execute("PRAGMA foreign_key_check").fetchall()
        quick = str(target.execute("PRAGMA quick_check").fetchone()[0])
        if quick != "ok" or violations or len(user_tables(target)) != 88:
            raise RuntimeError(
                f"strict local v10 stage invalid quick={quick} fk={len(violations)}"
            )
        return copied
    except Exception:
        target.rollback()
        raise
    finally:
        target.close()
        source.close()


def _fsync(path: Path) -> None:
    if os.name == "nt" and path.is_dir():
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _exclusive_upgrade_lock(path: Path):
    """Hold one cross-process migration lock on macOS, Linux, and Windows."""

    with path.open("a+b") as lock:
        if os.name == "nt":
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _replace_database(path: Path, stage: Path, backup_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            os.replace(sidecar, backup_path.with_name(backup_path.name + suffix))
    os.replace(stage, path)
    path.chmod(0o600)
    _fsync(path)
    _fsync(path.parent)


def _restore_backup(path: Path, backup_path: Path) -> None:
    failed = backup_path.with_name(backup_path.name + ".failed-v10")
    if path.exists():
        os.replace(path, failed)
    restore_stage = path.with_name(f".{path.name}.restore-{new_id()}")
    shutil.copy2(backup_path, restore_stage)
    _fsync(restore_stage)
    os.replace(restore_stage, path)
    _fsync(path.parent)


def ensure_local_database_current(path: Path) -> OfflineUpgradeResult:
    """Upgrade only exact published v8/v9 local identities before runtime opens."""

    path = path.resolve()
    current_version = int(LOCAL_CONTRACT.contract_version)
    if not path.exists() or path.stat().st_size == 0:
        return OfflineUpgradeResult(False, None, current_version, None)

    lock_path = path.with_name(path.name + ".upgrade.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_upgrade_lock(lock_path):
        try:
            current = verify_database(path, "local")
        except RuntimeError:
            current = None
        if current is not None:
            return OfflineUpgradeResult(
                False, int(current.contract_version), current_version, None
            )

        version, _ = _inspect_source(path)
        backup_path = _backup_database(path, version)
        stage = path.with_name(f".{path.name}.v10-stage-{new_id()}")
        try:
            copied = _build_v10(
                backup_path,
                stage,
                version=version,
                backup_path=backup_path,
            )
            staged_identity = verify_database(stage, "local")
            _fsync(stage)
            _replace_database(path, stage, backup_path)
            try:
                live_identity = verify_database(path, "local")
            except Exception:
                _restore_backup(path, backup_path)
                raise
            if (
                live_identity.database_generation_id
                != staged_identity.database_generation_id
            ):
                _restore_backup(path, backup_path)
                raise RuntimeError("strict local migration generation changed after swap")
            return OfflineUpgradeResult(
                True, version, current_version, backup_path, copied
            )
        finally:
            if stage.exists():
                stage.unlink()
