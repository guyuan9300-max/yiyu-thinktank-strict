#!/usr/bin/env python3
"""Activate the GC-02 build registry in an existing exact 88-table database.

This script performs no DDL and does not change the schema identity.  It only
records the frozen control/query contract in the existing build-authority
tables and adds an immutable migration-ledger receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.activate_gc01_contract import _seed_registry, _stable_id
from strict_common.ids import canonical_json, sha256_text, utc_now
from strict_common.physical_schema import (
    ddl_from_manifest,
    normalized_structure,
    structure_sha256,
)

CONTRACTS = ROOT / "contracts"
REGISTRY_PATH = CONTRACTS / "gc02-registry.v1.json"


def _manifest(role: str) -> tuple[dict[str, Any], str]:
    path = CONTRACTS / f"strict-{role}-schema-manifest.v1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()
    frozen = (
        CONTRACTS / f"strict-{role}-schema-manifest.v1.canonical.sha256"
    ).read_text(encoding="utf-8").split()[0]
    if digest != frozen:
        raise RuntimeError(f"{role} manifest hash mismatch")
    if len(raw.get("allowedTables") or []) != 88:
        raise RuntimeError(f"{role} manifest is not the exact 88-table contract")
    return raw, digest


def _registry(allowed_tables: set[str]) -> tuple[dict[str, Any], str]:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if raw.get("goldenChainId") != "GC-02":
        raise RuntimeError("registry is not GC-02")
    controls = raw.get("controls") or []
    queries = raw.get("queries") or []
    if len({item["controlId"] for item in controls}) != len(controls):
        raise RuntimeError("duplicate GC-02 controlId")
    if len({item["queryId"] for item in queries}) != len(queries):
        raise RuntimeError("duplicate GC-02 queryId")
    forbidden = {"work_projects", "project_participants", "projection_business_objects"}
    for item in [*controls, *queries]:
        for key in ("localRead", "localWrite", "cloudRead", "cloudWrite"):
            tables = set(item.get(key) or [])
            unknown = tables - allowed_tables
            if unknown:
                raise RuntimeError(
                    f"{item}: unknown tables in {key}: {sorted(unknown)}"
                )
            if tables & forbidden:
                raise RuntimeError(
                    f"{item}: frozen tables in {key}: {sorted(tables & forbidden)}"
                )
    return raw, hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()


def _expected_structure(manifest: dict[str, Any]) -> str:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(ddl_from_manifest(manifest))
        return structure_sha256(normalized_structure(connection))
    finally:
        connection.close()


def _record_activation(
    connection: sqlite3.Connection,
    *,
    schema: sqlite3.Row,
    registry: dict[str, Any],
    registry_hash: str,
    rollback_ref: str | None,
) -> str:
    definition_version = int(registry["definitionVersion"])
    migration_id = _stable_id(
        "migration",
        str(schema["id"]),
        str(registry["registryId"]),
        str(definition_version),
        registry_hash,
    )
    existing = connection.execute(
        "SELECT started_at FROM migration_ledger WHERE id=?",
        (migration_id,),
    ).fetchone()
    if existing is not None:
        return str(existing["started_at"])
    now = utc_now()
    version = str(schema["version"])
    step = f"activate_gc02_registry_v{definition_version}"
    connection.execute(
        """
        INSERT INTO migration_ledger (
            id, schema_version_id, step, checksum, status, from_version,
            to_version, code_hash, started_at, completed_at, rollback_ref,
            origin_instance_id, created_at, integrity_hash, authority_role
        ) VALUES (?, ?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'build')
        """,
        (
            migration_id,
            schema["id"],
            step,
            registry_hash,
            version,
            version,
            registry_hash,
            now,
            now,
            rollback_ref,
            schema["origin_instance_id"],
            now,
            sha256_text(
                f"{migration_id}|{schema['id']}|{registry_hash}|{now}"
            ),
        ),
    )
    return now


def activate(database: Path, role: str, rollback_ref: str | None) -> dict[str, Any]:
    manifest, manifest_hash = _manifest(role)
    allowed_tables = {str(item["name"]) for item in manifest["allowedTables"]}
    registry, registry_hash = _registry(allowed_tables)
    expected_structure = _expected_structure(manifest)

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        actual_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        if actual_tables != allowed_tables:
            raise RuntimeError("activity database is not the exact 88-table manifest")
        before_structure = structure_sha256(normalized_structure(connection))
        if before_structure != expected_structure:
            raise RuntimeError("activity database structure differs from frozen manifest")
        schema = connection.execute(
            "SELECT * FROM schema_versions WHERE status='active' "
            "ORDER BY activated_at DESC LIMIT 1"
        ).fetchone()
        if schema is None or str(schema["manifest_hash"]) != manifest_hash:
            raise RuntimeError("active schema identity does not match frozen manifest")

        connection.execute("BEGIN IMMEDIATE")
        activated_at = _record_activation(
            connection,
            schema=schema,
            registry=registry,
            registry_hash=registry_hash,
            rollback_ref=rollback_ref,
        )
        control_rows, query_rows = _seed_registry(
            connection,
            registry=registry,
            manifest_hash=manifest_hash,
            schema_version_id=str(schema["id"]),
            activated_at=activated_at,
        )
        connection.execute("COMMIT")

        after_structure = structure_sha256(normalized_structure(connection))
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if after_structure != before_structure or quick != "ok" or foreign_key_errors:
            raise RuntimeError("GC-02 registry activation post-check failed")
        return {
            "database": str(database),
            "role": role,
            "contractVersion": int(manifest["contractVersion"]),
            "manifestHash": manifest_hash,
            "registryHash": registry_hash,
            "schemaVersionId": str(schema["id"]),
            "tables": len(actual_tables),
            "structureHash": after_structure,
            "controlRegistryRows": control_rows,
            "queryRegistryRows": query_rows,
            "quickCheck": quick,
            "foreignKeyErrors": foreign_key_errors,
        }
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--role", required=True, choices=("local", "cloud"))
    parser.add_argument("--rollback-ref")
    args = parser.parse_args()
    print(
        json.dumps(
            activate(args.database.resolve(), args.role, args.rollback_ref),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
