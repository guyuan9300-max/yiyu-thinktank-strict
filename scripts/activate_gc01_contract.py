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

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.physical_schema import (
    ddl_from_manifest,
    ddl_sha256,
    normalized_structure,
    structure_sha256,
)

CONTRACTS = ROOT / "contracts"
REGISTRY_PATH = CONTRACTS / "gc01-registry.v1.json"


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:30]}"


def _manifest(role: str) -> tuple[dict[str, Any], str]:
    path = CONTRACTS / f"strict-{role}-schema-manifest.v1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()
    frozen = (
        CONTRACTS / f"strict-{role}-schema-manifest.v1.canonical.sha256"
    ).read_text(encoding="utf-8").split()[0]
    if digest != frozen:
        raise RuntimeError(f"{role} manifest hash mismatch")
    if int(raw["contractVersion"]) != 6:
        raise RuntimeError(f"{role} GC-01 activation requires contract version 6")
    return raw, digest


def _registry(allowed_tables: set[str]) -> tuple[dict[str, Any], str]:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if raw.get("goldenChainId") != "GC-01":
        raise RuntimeError("registry is not GC-01")
    controls = raw.get("controls") or []
    queries = raw.get("queries") or []
    if len({item["controlId"] for item in controls}) != len(controls):
        raise RuntimeError("duplicate GC-01 controlId")
    if len({item["queryId"] for item in queries}) != len(queries):
        raise RuntimeError("duplicate GC-01 queryId")
    for item in [*controls, *queries]:
        for key in ("localRead", "localWrite", "cloudRead", "cloudWrite"):
            unknown = set(item.get(key) or []) - allowed_tables
            if unknown:
                raise RuntimeError(f"{item}: unknown tables in {key}: {sorted(unknown)}")
    return raw, hashlib.sha256(canonical_json(raw).encode("utf-8")).hexdigest()


def _expected_structure(manifest: dict[str, Any]) -> str:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(ddl_from_manifest(manifest))
        return structure_sha256(normalized_structure(connection))
    finally:
        connection.close()


def _insert_exact(connection: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    existing = connection.execute(
        f'SELECT * FROM "{table}" WHERE id=?', (row["id"],)
    ).fetchone()
    if existing is not None:
        columns = [item[1] for item in connection.execute(f'PRAGMA table_info("{table}")')]
        current = dict(zip(columns, existing, strict=True))
        if any(current.get(key) != value for key, value in row.items()):
            raise RuntimeError(f"immutable build row drift: {table}/{row['id']}")
        return
    columns = list(row)
    placeholders = ",".join("?" for _ in columns)
    connection.execute(
        f'INSERT INTO "{table}" ({",".join(columns)}) VALUES ({placeholders})',
        tuple(row[column] for column in columns),
    )


def _upsert_build_row(
    connection: sqlite3.Connection,
    table: str,
    row: dict[str, Any],
) -> None:
    existing = connection.execute(
        f'SELECT * FROM "{table}" WHERE id=?',
        (row["id"],),
    ).fetchone()
    if existing is None:
        _insert_exact(connection, table, row)
        return
    table_columns = [
        item[1] for item in connection.execute(f'PRAGMA table_info("{table}")')
    ]
    current = dict(zip(table_columns, existing, strict=True))
    if all(current.get(key) == value for key, value in row.items()):
        return
    columns = [column for column in row if column != "id"]
    connection.execute(
        f'UPDATE "{table}" SET '
        + ",".join(f'"{column}"=?' for column in columns)
        + ' WHERE id=?',
        tuple(row[column] for column in columns) + (row["id"],),
    )


def _activate_schema_identity(
    connection: sqlite3.Connection,
    *,
    role: str,
    manifest: dict[str, Any],
    manifest_hash: str,
    registry_hash: str,
    rollback_ref: str | None,
) -> str:
    active = connection.execute(
        "SELECT * FROM schema_versions WHERE status='active' "
        "ORDER BY activated_at DESC LIMIT 1"
    ).fetchone()
    if active is None:
        raise RuntimeError("active schema identity missing")
    columns = [item[1] for item in connection.execute("PRAGMA table_info(schema_versions)")]
    current = dict(zip(columns, active, strict=True))
    if current["manifest_hash"] == manifest_hash and int(current["version"]) == 6:
        return str(current["id"])
    if int(current["version"]) >= 6:
        raise RuntimeError("unexpected active schema version")

    now = utc_now()
    schema_version_id = new_id()
    migration_id = new_id()
    ddl_hash = ddl_sha256(manifest)
    connection.execute(
        "UPDATE schema_versions SET status='superseded' WHERE id=?",
        (current["id"],),
    )
    connection.execute(
        """
        INSERT INTO schema_versions (
            id, engine, version, checksum, status, database_role,
            schema_family, manifest_hash, migration_set_hash, build_id,
            created_at, activated_at, authority_role, origin_instance_id,
            database_generation_id
        ) VALUES (?, 'sqlite', 6, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            schema_version_id,
            ddl_hash,
            manifest["databaseRole"],
            manifest["schemaFamily"],
            manifest_hash,
            ddl_hash,
            schema_version_id,
            now,
            now,
            role,
            current["origin_instance_id"],
            current["database_generation_id"],
        ),
    )
    connection.execute(
        """
        INSERT INTO migration_ledger (
            id, schema_version_id, step, checksum, status, from_version,
            to_version, code_hash, started_at, completed_at, rollback_ref,
            origin_instance_id, created_at, integrity_hash, authority_role
        ) VALUES (?, ?, 'activate_gc01_contract_v1', ?, 'applied', ?, '6', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            migration_id,
            schema_version_id,
            ddl_hash,
            str(current["version"]),
            registry_hash,
            now,
            now,
            rollback_ref,
            current["origin_instance_id"],
            now,
            sha256_text(
                f"{migration_id}|{schema_version_id}|{manifest_hash}|{registry_hash}|{now}"
            ),
            role,
        ),
    )
    connection.execute("PRAGMA user_version = 6")
    return schema_version_id


def _seed_registry(
    connection: sqlite3.Connection,
    *,
    registry: dict[str, Any],
    manifest_hash: str,
    schema_version_id: str,
    activated_at: str,
) -> tuple[int, int]:
    definition_version = int(registry["definitionVersion"])
    connection.execute(
        """
        UPDATE control_registry SET status='superseded'
        WHERE golden_chain_id=? AND definition_version<? AND status='active'
        """,
        (registry["goldenChainId"], definition_version),
    )
    connection.execute(
        """
        UPDATE query_registry SET status='superseded'
        WHERE evidence_ref=? AND definition_version<? AND status='active'
        """,
        (registry["evidenceRef"], definition_version),
    )
    common = {
        "schema_version_id": schema_version_id,
        "definition_version": definition_version,
        "manifest_hash": manifest_hash,
        "status": str(registry["status"]),
        "activated_at": activated_at,
    }
    control_count = 0
    for control in registry["controls"]:
        control_id = str(control["controlId"])
        _upsert_build_row(
            connection,
            "control_registry",
            {
                "id": _stable_id("cr", "control", control_id),
                "control_id": control_id,
                "surface": control["surface"],
                "intent_kind": control["intentKind"],
                "query_id": control["operationId"],
                "golden_chain_id": registry["goldenChainId"],
                "five_state_contract": registry["stateContract"],
                "completion_state": registry["completionState"],
                "record_kind": "control",
                "target_object_name": None,
                "expected_access_kind": None,
                "evidence_ref": registry["evidenceRef"],
                **common,
            },
        )
        control_count += 1
        for access_key in ("localRead", "localWrite", "cloudRead", "cloudWrite"):
            access_kind = {
                "localRead": "local_read",
                "localWrite": "local_write",
                "cloudRead": "cloud_read",
                "cloudWrite": "cloud_write",
            }[access_key]
            for table_name in control.get(access_key) or []:
                _upsert_build_row(
                    connection,
                    "control_registry",
                    {
                        "id": _stable_id(
                            "cr",
                            "table",
                            control_id,
                            table_name,
                            access_kind,
                        ),
                        "control_id": control_id,
                        "surface": control["surface"],
                        "intent_kind": control["intentKind"],
                        "query_id": control["operationId"],
                        "golden_chain_id": registry["goldenChainId"],
                        "five_state_contract": registry["stateContract"],
                        "completion_state": registry["completionState"],
                        "record_kind": "table_expectation",
                        "target_object_name": table_name,
                        "expected_access_kind": access_kind,
                        "evidence_ref": registry["evidenceRef"],
                        **common,
                    },
                )
                control_count += 1

    query_count = 0
    for query in registry["queries"]:
        query_id = str(query["queryId"])
        _upsert_build_row(
            connection,
            "query_registry",
            {
                "id": _stable_id("qr", "query", query_id),
                "query_id": query_id,
                "authority_kind": query["authorityKind"],
                "projection_kind": query["projectionKind"],
                "policy_version": 1,
                "stale_policy": query["stalePolicy"],
                "record_kind": "query",
                "target_object_name": None,
                "expected_access_kind": None,
                "evidence_ref": registry["evidenceRef"],
                **common,
            },
        )
        query_count += 1
        for access_key in ("localRead", "cloudRead"):
            access_kind = "local_read" if access_key == "localRead" else "cloud_read"
            for table_name in query.get(access_key) or []:
                _upsert_build_row(
                    connection,
                    "query_registry",
                    {
                        "id": _stable_id(
                            "qr",
                            "source",
                            query_id,
                            table_name,
                            access_kind,
                        ),
                        "query_id": query_id,
                        "authority_kind": query["authorityKind"],
                        "projection_kind": query["projectionKind"],
                        "policy_version": 1,
                        "stale_policy": query["stalePolicy"],
                        "record_kind": "source_expectation",
                        "target_object_name": table_name,
                        "expected_access_kind": access_kind,
                        "evidence_ref": registry["evidenceRef"],
                        **common,
                    },
                )
                query_count += 1
    return control_count, query_count


def _record_registry_activation(
    connection: sqlite3.Connection,
    *,
    schema_version_id: str,
    registry: dict[str, Any],
    registry_hash: str,
    rollback_ref: str | None,
) -> str:
    definition_version = int(registry["definitionVersion"])
    migration_id = _stable_id(
        "migration",
        schema_version_id,
        registry["registryId"],
        str(definition_version),
        registry_hash,
    )
    existing = connection.execute(
        "SELECT started_at FROM migration_ledger WHERE id=?",
        (migration_id,),
    ).fetchone()
    if existing is not None:
        return str(existing["started_at"])
    schema = connection.execute(
        "SELECT origin_instance_id FROM schema_versions WHERE id=?",
        (schema_version_id,),
    ).fetchone()
    if schema is None:
        raise RuntimeError("GC-01 schema version missing during registry evidence")
    now = utc_now()
    step = f"activate_gc01_registry_v{definition_version}"
    connection.execute(
        """
        INSERT INTO migration_ledger (
            id, schema_version_id, step, checksum, status, from_version,
            to_version, code_hash, started_at, completed_at, rollback_ref,
            origin_instance_id, created_at, integrity_hash, authority_role
        ) VALUES (?, ?, ?, ?, 'applied', '6', '6', ?, ?, ?, ?, ?, ?, ?, 'build')
        """,
        (
            migration_id,
            schema_version_id,
            step,
            registry_hash,
            registry_hash,
            now,
            now,
            rollback_ref,
            schema["origin_instance_id"],
            now,
            sha256_text(
                f"{migration_id}|{schema_version_id}|{registry_hash}|{now}"
            ),
        ),
    )
    return now


def activate(database: Path, role: str, rollback_ref: str | None) -> dict[str, Any]:
    manifest, manifest_hash = _manifest(role)
    allowed_tables = {item["name"] for item in manifest["allowedTables"]}
    registry, registry_hash = _registry(allowed_tables)
    expected_structure = _expected_structure(manifest)

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        actual_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if actual_tables != allowed_tables:
            raise RuntimeError("activity database is not the exact 88-table manifest")
        before_structure = structure_sha256(normalized_structure(connection))
        if before_structure != expected_structure:
            raise RuntimeError("activity database structure differs from v6 manifest")
        connection.execute("BEGIN IMMEDIATE")
        schema_version_id = _activate_schema_identity(
            connection,
            role=role,
            manifest=manifest,
            manifest_hash=manifest_hash,
            registry_hash=registry_hash,
            rollback_ref=rollback_ref,
        )
        registry_activated_at = _record_registry_activation(
            connection,
            schema_version_id=schema_version_id,
            registry=registry,
            registry_hash=registry_hash,
            rollback_ref=rollback_ref,
        )
        control_rows, query_rows = _seed_registry(
            connection,
            registry=registry,
            manifest_hash=manifest_hash,
            schema_version_id=schema_version_id,
            activated_at=registry_activated_at,
        )
        connection.execute("COMMIT")
        after_structure = structure_sha256(normalized_structure(connection))
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if after_structure != before_structure or quick != "ok" or foreign_key_errors:
            raise RuntimeError("GC-01 activation post-check failed")
        return {
            "database": str(database),
            "role": role,
            "contractVersion": 6,
            "manifestHash": manifest_hash,
            "registryHash": registry_hash,
            "schemaVersionId": schema_version_id,
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
