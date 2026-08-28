#!/usr/bin/env python3
"""Offline v7 -> v8 rebuild for the declarative Agent Skill foundation."""

from __future__ import annotations

import argparse
import copy
import hashlib
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

FROZEN_V8_MANIFEST_SHA256 = {
    "local": "19971bd3a3e1cf9beecdb5893b2b15fd6bc02c8951795fc828105ab481f20432",
    "cloud": "b1d65aa9e406398dd9692387406a3d8a27beb66c584bd6953c815eb413909a10",
}


def _table(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        table for table in manifest["allowedTables"]
        if table["name"] == name
    )


def frozen_v8_manifest(role: Role) -> dict[str, Any]:
    """Reconstruct and hash-check the immutable v8 migration artifact.

    The active repository manifest has advanced to v10. Historical migration
    code must not silently read that moving target, so the small reviewed v9/v10
    schema delta is explicitly removed and the resulting canonical hash is
    pinned to the original v8 release artifact.
    """

    current = LOCAL_CONTRACT if role == "local" else CLOUD_CONTRACT
    raw = copy.deepcopy(current.raw)
    raw["contractDate"] = "2026-08-06"
    raw["contractVersion"] = "8"
    raw["commonRules"].pop("flatPlanning", None)
    raw["commonRules"].pop("classifiedMobileRecording", None)

    for table_name in ("tasks", "meetings"):
        table = _table(raw, table_name)
        table["fields"] = [
            field for field in table["fields"]
            if field["name"] != "planning_cycle_id"
        ]

    planning = _table(raw, "planning_cycles")
    client_index = next(
        index for index, field in enumerate(planning["fields"])
        if field["name"] == "client_id"
    )
    planning["fields"].insert(client_index + 1, {
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
            "source": "终审补充",
        },
    })
    planning["command_invariants"] = []

    decisions = _table(raw, "decision_actions")
    source_index = next(
        index for index, field in enumerate(decisions["fields"])
        if field["name"] == "source_set_id"
    )
    decisions["fields"].insert(source_index + 1, {
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
            "source": "蓝图保留",
        },
    })
    decisions["unique_constraints"] = [{
        "name": "uq_decision_actions_01",
        "fields": ["task_id"],
        "where": "task_id IS NOT NULL",
    }]
    tombstone_index = next(
        index for index, check in enumerate(decisions["check_constraints"])
        if check["name"] == "ck_tombstone_time"
    )
    decisions["check_constraints"].insert(tombstone_index, {
        "name": "ck_primary_task_unique_role",
        "expression": "task_id IS NULL OR record_kind IN ('decision','plan_action')",
    })
    decisions["command_invariants"] = []

    recordings = _table(raw, "recordings")
    recordings["fields"] = [
        field for field in recordings["fields"]
        if field["name"] not in {
            "binding_kind", "task_id", "client_id", "event_line_id"
        }
    ]
    meeting_field = next(
        field for field in recordings["fields"]
        if field["name"] == "meeting_id"
    )
    meeting_field["nullable"] = False
    recordings["check_constraints"] = [
        check for check in recordings["check_constraints"]
        if check["name"] not in {
            "ck_recording_binding_kind", "ck_recording_binding_shape"
        }
    ]
    recordings["command_invariants"] = []

    digest = hashlib.sha256(
        json.dumps(
            raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    if digest != FROZEN_V8_MANIFEST_SHA256[role]:
        raise RuntimeError("frozen v8 manifest reconstruction drifted")
    return raw


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
    current_contract = LOCAL_CONTRACT if role == "local" else CLOUD_CONTRACT
    manifest = frozen_v8_manifest(role)
    manifest_hash = FROZEN_V8_MANIFEST_SHA256[role]
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
        target.executescript(ddl_from_manifest(manifest))
        target.execute("BEGIN IMMEDIATE")
        source_counts = _copy_tables(source, target, manifest)
        now = utc_now()
        build_id = new_id()
        migration_id = new_id()
        generation_id = str(source_identity["database_generation_id"])
        ddl_hash = ddl_sha256(manifest)
        target.execute("UPDATE schema_versions SET status='superseded' WHERE status='active'")
        target.execute(
            "INSERT INTO schema_versions (id,engine,version,checksum,status,database_role,schema_family,"
            "manifest_hash,migration_set_hash,build_id,created_at,activated_at,authority_role,"
            "origin_instance_id,database_generation_id) VALUES (?,'sqlite',8,?,'active',?,?,?,?,?,?,?,?,?,?)",
            (
                build_id, ddl_hash, current_contract.database_role,
                current_contract.schema_family, manifest_hash, ddl_hash,
                build_id, now, now, role,
                generation_id, generation_id,
            ),
        )
        target.execute(
            "INSERT INTO migration_ledger (id,schema_version_id,step,checksum,status,from_version,to_version,"
            "code_hash,started_at,completed_at,rollback_ref,origin_instance_id,created_at,integrity_hash,"
            "authority_role) VALUES (?,?,'agent_skill_foundation_v8',?,'applied','7','8',?,?,?,?,?,?,?,?)",
            (
                migration_id, build_id, ddl_hash, manifest_hash, now, now,
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
        if user_tables(target) != {
            str(table["name"]) for table in manifest["allowedTables"]
        }:
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
