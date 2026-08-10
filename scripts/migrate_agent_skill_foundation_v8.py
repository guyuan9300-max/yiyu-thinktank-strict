#!/usr/bin/env python3
"""Offline v7 -> v8 rebuild for the declarative Agent Skill foundation."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from strict_common.agent_memory import (
    BUILTIN_AGENT_DEFINITIONS,
    builtin_agent_id,
    canonical_organization_scope_id,
)
from strict_common.contracts import CLOUD_CONTRACT, LOCAL_CONTRACT
from strict_common.ids import new_id, sha256_text, utc_now
from strict_common.physical_schema import ddl_from_manifest, ddl_sha256, user_tables


Role = Literal["local", "cloud"]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]


def _active_identity(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM schema_versions WHERE status='active' "
        "ORDER BY activated_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("source database has no active schema identity")
    return row


def _validate_source(connection: sqlite3.Connection, role: Role) -> sqlite3.Row:
    if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
        raise RuntimeError("source database quick_check failed")
    identity = _active_identity(connection)
    expected = LOCAL_CONTRACT if role == "local" else CLOUD_CONTRACT
    if str(identity["version"]) != "7":
        raise RuntimeError(f"source must be contract v7, got {identity['version']}")
    if str(identity["schema_family"]) != expected.schema_family:
        raise RuntimeError("source schema family mismatch")
    if str(identity["database_role"]) != expected.database_role:
        raise RuntimeError("source database role mismatch")
    if len(user_tables(connection)) != 88:
        raise RuntimeError("source database must contain exactly 88 tables")
    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"source database has {len(violations)} foreign key violations")
    return identity


def _copy_tables(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    manifest: dict[str, Any],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_spec in manifest["allowedTables"]:
        table = str(table_spec["name"])
        source_columns = set(_columns(source, table))
        common = [column for column in _columns(target, table) if column in source_columns]
        names = ", ".join(_quote(column) for column in common)
        rows = source.execute(f"SELECT {names} FROM {_quote(table)}").fetchall()
        if rows:
            placeholders = ", ".join("?" for _ in common)
            target.executemany(
                f"INSERT INTO {_quote(table)} ({names}) VALUES ({placeholders})",
                [tuple(row[column] for column in common) for row in rows],
            )
        counts[table] = len(rows)
    return counts


def _sync_builtin_agents(
    target: sqlite3.Connection,
    *,
    role: Role,
    generation_id: str,
    now: str,
) -> int:
    changed = 0
    organizations = target.execute(
        "SELECT id FROM organizations WHERE record_kind='organization' "
        "AND lifecycle_state='active' ORDER BY id"
    ).fetchall()
    for organization in organizations:
        organization_id = str(organization[0])
        scope_id = canonical_organization_scope_id(organization_id)
        if target.execute(
            "SELECT 1 FROM authorization_scopes WHERE id=? AND organization_id=? "
            "AND scope_kind='organization' AND status='active' AND lifecycle_state='active'",
            (scope_id, organization_id),
        ).fetchone() is None:
            continue
        for definition in BUILTIN_AGENT_DEFINITIONS:
            bot_id = builtin_agent_id(organization_id, definition.agent_kind)
            row = target.execute(
                "SELECT description,capability_policy_version,version FROM bot_definitions WHERE id=?",
                (bot_id,),
            ).fetchone()
            if row is None:
                if role != "cloud":
                    continue
                target.execute(
                    "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,"
                    "resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,'bot_definition','active',1,'builtin_function_agent',?,?,NULL,'cloud',?)",
                    (bot_id, scope_id, now, now, generation_id),
                )
                target.execute(
                    "INSERT INTO bot_definitions (id,scope_id,agent_kind,owner_principal_id,"
                    "owner_membership_id,permission_policy_id,version,handle,description,department_id,"
                    "capability_policy_version,secret_reference,secret_fingerprint,enabled,lifecycle_state,"
                    "created_at,updated_at,deleted_at) VALUES (?,?,?,NULL,NULL,NULL,1,?,?,NULL,?,NULL,NULL,1,"
                    "'active',?,?,NULL)",
                    (
                        bot_id, scope_id, definition.agent_kind, definition.handle,
                        definition.description, definition.capability_policy_version, now, now,
                    ),
                )
                changed += 1
                continue
            if (
                str(row["description"] or "") != definition.description
                or str(row["capability_policy_version"] or "")
                != definition.capability_policy_version
            ):
                target.execute(
                    "UPDATE bot_definitions SET description=?,capability_policy_version=?,"
                    "version=version+1,updated_at=? WHERE id=?",
                    (definition.description, definition.capability_policy_version, now, bot_id),
                )
                target.execute(
                    "UPDATE secured_resources SET version=version+1,updated_at=? WHERE id=?",
                    (now, bot_id),
                )
                changed += 1
    return changed


def migrate(source_path: Path, target_path: Path, role: Role) -> dict[str, Any]:
    source_path = source_path.resolve()
    target_path = target_path.resolve()
    if target_path.exists():
        raise RuntimeError(f"target already exists: {target_path}")
    contract = LOCAL_CONTRACT if role == "local" else CLOUD_CONTRACT
    if contract.contract_version != "8":
        raise RuntimeError("active repository contract is not v8")
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    target = sqlite3.connect(target_path)
    target.row_factory = sqlite3.Row
    try:
        source_identity = _validate_source(source, role)
        target.execute("PRAGMA foreign_keys=OFF")
        target.execute("PRAGMA journal_mode=WAL")
        target.execute("PRAGMA synchronous=FULL")
        target.executescript(ddl_from_manifest(contract.raw))
        target.execute("BEGIN IMMEDIATE")
        source_counts = _copy_tables(source, target, contract.raw)
        now = utc_now()
        build_id = new_id()
        migration_id = new_id()
        generation_id = str(source_identity["database_generation_id"])
        ddl_hash = ddl_sha256(contract.raw)
        target.execute("UPDATE schema_versions SET status='superseded' WHERE status='active'")
        target.execute(
            "INSERT INTO schema_versions (id,engine,version,checksum,status,database_role,schema_family,"
            "manifest_hash,migration_set_hash,build_id,created_at,activated_at,authority_role,"
            "origin_instance_id,database_generation_id) VALUES (?,'sqlite',8,?,'active',?,?,?,?,?,?,?,?,?,?)",
            (
                build_id, ddl_hash, contract.database_role, contract.schema_family,
                contract.manifest_hash, ddl_hash, build_id, now, now, role,
                generation_id, generation_id,
            ),
        )
        target.execute(
            "INSERT INTO migration_ledger (id,schema_version_id,step,checksum,status,from_version,to_version,"
            "code_hash,started_at,completed_at,rollback_ref,origin_instance_id,created_at,integrity_hash,"
            "authority_role) VALUES (?,?,'agent_skill_foundation_v8',?,'applied','7','8',?,?,?,?,?,?,?,?)",
            (
                migration_id, build_id, ddl_hash, contract.manifest_hash, now, now,
                "restore reviewed v7 database backup", generation_id, now,
                sha256_text(f"{migration_id}|7|8|{ddl_hash}|{now}"), role,
            ),
        )
        changed = _sync_builtin_agents(
            target, role=role, generation_id=generation_id, now=now
        )
        target.execute("PRAGMA user_version=8")
        target.commit()
        target.execute("PRAGMA foreign_keys=ON")
        quick_check = str(target.execute("PRAGMA quick_check").fetchone()[0])
        violations = target.execute("PRAGMA foreign_key_check").fetchall()
        if quick_check != "ok" or violations:
            raise RuntimeError(
                f"target integrity failed quick_check={quick_check} foreign_keys={len(violations)}"
            )
        if user_tables(target) != set(contract.allowed_tables):
            raise RuntimeError("target table inventory differs from the v8 manifest")
        return {
            "role": role,
            "sourceVersion": str(source_identity["version"]),
            "targetVersion": "8",
            "tableCount": 88,
            "sourceRowCount": sum(source_counts.values()),
            "updatedBuiltinAgentCount": changed,
            "quickCheck": quick_check,
            "foreignKeyViolationCount": len(violations),
            "databaseGenerationIdPreserved": generation_id
            == str(_active_identity(target)["database_generation_id"]),
        }
    except Exception:
        target.rollback()
        target.close()
        target_path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(target_path) + suffix).unlink(missing_ok=True)
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
