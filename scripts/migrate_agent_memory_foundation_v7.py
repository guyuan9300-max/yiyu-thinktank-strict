#!/usr/bin/env python3
"""Offline v6 -> v7 rebuild for the Agent Memory foundation.

The source is opened read-only. The target must not exist. No ATTACH or runtime DDL is used.
"""

from __future__ import annotations

import argparse
import json
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
        """
        SELECT * FROM schema_versions
        WHERE status='active'
        ORDER BY activated_at DESC LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("source database has no active schema identity")
    return row


def _validate_source(connection: sqlite3.Connection, role: Role) -> sqlite3.Row:
    if str(connection.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
        raise RuntimeError("source database quick_check failed")
    identity = _active_identity(connection)
    expected = LOCAL_CONTRACT if role == "local" else CLOUD_CONTRACT
    if str(identity["version"]) != "6":
        raise RuntimeError(f"source must be contract v6, got {identity['version']}")
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


def _copy_tables(source: sqlite3.Connection, target: sqlite3.Connection, manifest: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_spec in manifest["allowedTables"]:
        table = str(table_spec["name"])
        source_columns = set(_columns(source, table))
        target_columns = _columns(target, table)
        common = [column for column in target_columns if column in source_columns]
        if not common:
            raise RuntimeError(f"no common columns while migrating {table}")
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


def _seed_cloud_builtin_agents(target: sqlite3.Connection, generation_id: str, now: str) -> int:
    organizations = target.execute(
        """
        SELECT id FROM organizations
        WHERE record_kind='organization' AND lifecycle_state='active'
        ORDER BY id
        """
    ).fetchall()
    inserted = 0
    for row in organizations:
        organization_id = str(row[0])
        scope_id = canonical_organization_scope_id(organization_id)
        scope = target.execute(
            """
            SELECT id FROM authorization_scopes
            WHERE id=? AND organization_id=? AND scope_kind='organization'
              AND status='active' AND lifecycle_state='active'
            """,
            (scope_id, organization_id),
        ).fetchone()
        if scope is None:
            raise RuntimeError(
                f"organization {organization_id} has no canonical active authorization scope {scope_id}"
            )
        for definition in BUILTIN_AGENT_DEFINITIONS:
            bot_id = builtin_agent_id(organization_id, definition.agent_kind)
            target.execute(
                """
                INSERT INTO secured_resources (
                    id, scope_id, resource_kind, lifecycle_state, version,
                    resource_type_key, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, 'bot_definition', 'active', 1,
                          'builtin_function_agent', ?, ?, NULL, 'cloud', ?)
                """,
                (bot_id, scope_id, now, now, generation_id),
            )
            target.execute(
                """
                INSERT INTO bot_definitions (
                    id, scope_id, agent_kind, owner_principal_id,
                    owner_membership_id, permission_policy_id, version, handle,
                    description, department_id, capability_policy_version,
                    secret_reference, secret_fingerprint, enabled,
                    lifecycle_state, created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, NULL, NULL, NULL, 1, ?, ?, NULL, NULL,
                          NULL, NULL, 1, 'active', ?, ?, NULL)
                """,
                (
                    bot_id,
                    scope_id,
                    definition.agent_kind,
                    definition.handle,
                    definition.label,
                    now,
                    now,
                ),
            )
            inserted += 1
    return inserted


def migrate(source_path: Path, target_path: Path, role: Role) -> dict[str, Any]:
    source_path = source_path.resolve()
    target_path = target_path.resolve()
    if target_path.exists():
        raise RuntimeError(f"target already exists: {target_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    contract = LOCAL_CONTRACT if role == "local" else CLOUD_CONTRACT
    if contract.contract_version != "7":
        raise RuntimeError("active repository contract is not v7")

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    source.execute("PRAGMA foreign_keys=ON")
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
            """
            INSERT INTO schema_versions (
                id, engine, version, checksum, status, database_role,
                schema_family, manifest_hash, migration_set_hash, build_id,
                created_at, activated_at, authority_role, origin_instance_id,
                database_generation_id
            ) VALUES (?, 'sqlite', 7, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_id,
                ddl_hash,
                contract.database_role,
                contract.schema_family,
                contract.manifest_hash,
                ddl_hash,
                build_id,
                now,
                now,
                role,
                generation_id,
                generation_id,
            ),
        )
        target.execute(
            """
            INSERT INTO migration_ledger (
                id, schema_version_id, step, checksum, status, from_version,
                to_version, code_hash, started_at, completed_at, rollback_ref,
                origin_instance_id, created_at, integrity_hash, authority_role
            ) VALUES (?, ?, 'agent_memory_foundation_v7', ?, 'applied', '6',
                      '7', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                migration_id,
                build_id,
                ddl_hash,
                contract.manifest_hash,
                now,
                now,
                "restore reviewed v6 database backup",
                generation_id,
                now,
                sha256_text(f"{migration_id}|6|7|{ddl_hash}|{now}"),
                role,
            ),
        )
        seeded = _seed_cloud_builtin_agents(target, generation_id, now) if role == "cloud" else 0
        target.execute("PRAGMA user_version=7")
        target.commit()
        target.execute("PRAGMA foreign_keys=ON")
        quick_check = str(target.execute("PRAGMA quick_check").fetchone()[0])
        violations = target.execute("PRAGMA foreign_key_check").fetchall()
        if quick_check != "ok" or violations:
            raise RuntimeError(
                f"target integrity failed quick_check={quick_check} foreign_keys={len(violations)}"
            )
        target_tables = user_tables(target)
        if target_tables != set(contract.allowed_tables):
            raise RuntimeError("target table inventory differs from the v7 manifest")
        active = _active_identity(target)
        if str(active["manifest_hash"]) != contract.manifest_hash:
            raise RuntimeError("target manifest identity mismatch")
        return {
            "role": role,
            "source": str(source_path),
            "target": str(target_path),
            "sourceVersion": str(source_identity["version"]),
            "targetVersion": str(active["version"]),
            "tableCount": len(target_tables),
            "sourceRowCount": sum(source_counts.values()),
            "seededBuiltinAgentCount": seeded,
            "quickCheck": quick_check,
            "foreignKeyViolationCount": len(violations),
            "manifestHash": contract.manifest_hash,
            "databaseGenerationIdPreserved": str(active["database_generation_id"]) == generation_id,
        }
    except Exception:
        target.close()
        source.close()
        target_path.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(target_path) + suffix).unlink(missing_ok=True)
        raise
    finally:
        try:
            target.close()
        except Exception:
            pass
        try:
            source.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--role", choices=("local", "cloud"), required=True)
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.target, args.role), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
