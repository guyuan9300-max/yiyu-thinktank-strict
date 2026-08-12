#!/usr/bin/env python3
"""Offline v8 -> v9 rebuild: plans become directly selectable, many tasks per plan."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strict_common.contracts import CLOUD_CONTRACT, LOCAL_CONTRACT
from strict_common.ids import new_id, sha256_text, utc_now
from strict_common.physical_schema import ddl_from_manifest, ddl_sha256, user_tables

Role = Literal["local", "cloud"]


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in db.execute(f"PRAGMA table_info({q(table)})")]


def copy_common(source: sqlite3.Connection, target: sqlite3.Connection, raw: dict[str, Any]) -> int:
    total = 0
    for spec in raw["allowedTables"]:
        table = str(spec["name"])
        common = [name for name in columns(target, table) if name in set(columns(source, table))]
        names = ",".join(q(name) for name in common)
        rows = source.execute(f"SELECT {names} FROM {q(table)}").fetchall()
        if rows:
            target.executemany(
                f"INSERT INTO {q(table)} ({names}) VALUES ({','.join('?' for _ in common)})",
                [tuple(row[name] for name in common) for row in rows],
            )
        total += len(rows)
    return total


def migrate(source_path: Path, target_path: Path, role: Role) -> dict[str, Any]:
    contract = LOCAL_CONTRACT if role == "local" else CLOUD_CONTRACT
    if contract.contract_version != "9":
        raise RuntimeError("active repository contract is not v9")
    if target_path.exists():
        raise RuntimeError(f"target already exists: {target_path}")
    source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = sqlite3.connect(target_path)
    target.row_factory = sqlite3.Row
    try:
        if str(source.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise RuntimeError("source quick_check failed")
        identity = source.execute(
            "SELECT * FROM schema_versions WHERE status='active' ORDER BY activated_at DESC LIMIT 1"
        ).fetchone()
        if identity is None or str(identity["version"]) != "8":
            raise RuntimeError("source must be active v8")
        if len(user_tables(source)) != 88:
            raise RuntimeError("source must contain exactly 88 tables")
        target.execute("PRAGMA foreign_keys=OFF")
        target.executescript(ddl_from_manifest(contract.raw))
        target.execute("BEGIN IMMEDIATE")
        copied = copy_common(source, target, contract.raw)
        # Preserve old task->plan facts, but remove the one-task-per-action bottleneck.
        for row in source.execute(
            "SELECT task_id,planning_cycle_id FROM decision_actions "
            "WHERE task_id IS NOT NULL AND planning_cycle_id IS NOT NULL"
        ):
            target.execute(
                "UPDATE tasks SET planning_cycle_id=? WHERE id=? AND planning_cycle_id IS NULL",
                (row["planning_cycle_id"], row["task_id"]),
            )
        # Preserve the former meeting source-set relation as a direct FK.
        meeting_links = source.execute(
            "SELECT dl.derivative_object_id AS meeting_id,sm.source_object_id AS planning_cycle_id "
            "FROM derivation_lineage dl JOIN source_set_members sm "
            "ON sm.source_set_id=dl.source_set_id AND sm.scope_id=dl.scope_id "
            "WHERE dl.derivative_kind='meeting_plan_link' "
            "AND sm.source_object_kind='planning_cycle'"
        ).fetchall()
        for row in meeting_links:
            target.execute(
                "UPDATE meetings SET planning_cycle_id=? WHERE id=?",
                (row["planning_cycle_id"], row["meeting_id"]),
            )
        now = utc_now()
        build_id = new_id()
        migration_id = new_id()
        generation_id = str(identity["database_generation_id"])
        ddl_hash = ddl_sha256(contract.raw)
        target.execute("UPDATE schema_versions SET status='superseded' WHERE status='active'")
        target.execute(
            "INSERT INTO schema_versions (id,engine,version,checksum,status,database_role,schema_family,"
            "manifest_hash,migration_set_hash,build_id,created_at,activated_at,authority_role,"
            "origin_instance_id,database_generation_id) VALUES (?,'sqlite',9,?,'active',?,?,?,?,?,?,?,?,?,?)",
            (build_id, ddl_hash, contract.database_role, contract.schema_family, contract.manifest_hash,
             ddl_hash, build_id, now, now, role, generation_id, generation_id),
        )
        target.execute(
            "INSERT INTO migration_ledger (id,schema_version_id,step,checksum,status,from_version,to_version,"
            "code_hash,started_at,completed_at,rollback_ref,origin_instance_id,created_at,integrity_hash,authority_role) "
            "VALUES (?,?,'flat_planning_v9',?,'applied','8','9',?,?,?,?,?,?,?,?)",
            (migration_id, build_id, ddl_hash, contract.manifest_hash, now, now,
             "restore reviewed v8 backup", generation_id, now,
             sha256_text(f"{migration_id}|8|9|{ddl_hash}|{now}"), role),
        )
        target.execute("PRAGMA user_version=9")
        target.commit()
        target.execute("PRAGMA foreign_keys=ON")
        fk = target.execute("PRAGMA foreign_key_check").fetchall()
        quick = str(target.execute("PRAGMA quick_check").fetchone()[0])
        if fk or quick != "ok" or user_tables(target) != set(contract.allowed_tables):
            raise RuntimeError(f"target invalid quick={quick} fk={len(fk)}")
        return {"role": role, "rows": copied, "tables": 88, "quickCheck": quick, "foreignKeys": 0}
    except Exception:
        target.rollback()
        target.close()
        target_path.unlink(missing_ok=True)
        raise
    finally:
        source.close()
        try:
            target.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=("local", "cloud"), required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    print(migrate(args.source, args.target, args.role))


if __name__ == "__main__":
    main()
