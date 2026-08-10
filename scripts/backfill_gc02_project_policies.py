#!/usr/bin/env python3
"""Bind legacy active client grants to an explicit GC-02 policy version.

This is a DML-only, idempotent v8 repair.  It never creates tables and never
changes who can access a client; it only makes the existing permission source
explicit as required by GC-02.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud_backend.app.repositories.project_materials import (
    create_project_policy_version,
)
from scripts.activate_gc01_contract import _stable_id
from strict_common.ids import sha256_text, utc_now


BACKFILL_ID = "gc02-project-policy-backfill-v1"


def backfill(
    database: Path,
    *,
    rollback_ref: str | None = None,
) -> dict[str, Any]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        table_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        if table_count != 88:
            raise RuntimeError("GC-02 policy backfill requires the exact 88-table database")
        schema = connection.execute(
            "SELECT * FROM schema_versions WHERE status='active' "
            "ORDER BY activated_at DESC LIMIT 1"
        ).fetchone()
        if schema is None or str(schema["authority_role"]) != "cloud":
            raise RuntimeError("GC-02 project policies can only be backfilled on cloud authority")

        now = utc_now()
        policies_created = 0
        grants_bound = 0
        connection.execute("BEGIN IMMEDIATE")
        projects = connection.execute(
            """
            SELECT client.id, client.scope_id
            FROM clients AS client
            JOIN secured_resources AS resource
              ON resource.id=client.id AND resource.scope_id=client.scope_id
            WHERE client.lifecycle_state!='deleted'
              AND resource.lifecycle_state='active'
            ORDER BY client.id
            """
        ).fetchall()
        for project in projects:
            policy = connection.execute(
                """
                SELECT id
                FROM policy_versions
                WHERE scope_id=? AND secured_resource_id=?
                  AND lifecycle_state='active'
                ORDER BY version DESC, created_at DESC, id DESC
                LIMIT 1
                """,
                (project["scope_id"], project["id"]),
            ).fetchone()
            if policy is None:
                policy_id = create_project_policy_version(
                    connection,
                    scope_id=str(project["scope_id"]),
                    project_id=str(project["id"]),
                    now=now,
                )
                policies_created += 1
            else:
                policy_id = str(policy["id"])
            updated = connection.execute(
                """
                UPDATE object_grants
                SET policy_version_id=?, version=version+1, updated_at=?
                WHERE scope_id=? AND secured_resource_id=?
                  AND status='active' AND lifecycle_state='active'
                  AND policy_version_id IS NULL
                """,
                (
                    policy_id,
                    now,
                    project["scope_id"],
                    project["id"],
                ),
            )
            grants_bound += int(updated.rowcount)

        if policies_created or grants_bound:
            checksum = sha256_text(BACKFILL_ID)
            migration_id = _stable_id(
                "migration",
                str(schema["id"]),
                BACKFILL_ID,
            )
            existing = connection.execute(
                "SELECT id FROM migration_ledger WHERE id=?",
                (migration_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO migration_ledger (
                        id, schema_version_id, step, checksum, status,
                        from_version, to_version, code_hash, started_at,
                        completed_at, rollback_ref, origin_instance_id,
                        created_at, integrity_hash, authority_role
                    ) VALUES (?, ?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'build')
                    """,
                    (
                        migration_id,
                        schema["id"],
                        BACKFILL_ID,
                        checksum,
                        str(schema["version"]),
                        str(schema["version"]),
                        checksum,
                        now,
                        now,
                        rollback_ref,
                        schema["origin_instance_id"],
                        now,
                        sha256_text(
                            f"{migration_id}|{policies_created}|{grants_bound}|{now}"
                        ),
                    ),
                )
        connection.execute("COMMIT")

        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        remaining = int(
            connection.execute(
                "SELECT COUNT(*) FROM object_grants AS grant_row "
                "JOIN clients AS client "
                "ON client.id=grant_row.secured_resource_id "
                "AND client.scope_id=grant_row.scope_id "
                "WHERE grant_row.status='active' "
                "AND grant_row.lifecycle_state='active' "
                "AND grant_row.policy_version_id IS NULL"
            ).fetchone()[0]
        )
        if quick != "ok" or foreign_key_errors or remaining:
            raise RuntimeError("GC-02 project policy backfill post-check failed")
        return {
            "tables": table_count,
            "projectsScanned": len(projects),
            "policiesCreated": policies_created,
            "grantsBound": grants_bound,
            "remainingActiveGrantsWithoutPolicy": remaining,
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
    parser.add_argument("--rollback-ref")
    args = parser.parse_args()
    print(
        json.dumps(
            backfill(args.database.resolve(), rollback_ref=args.rollback_ref),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
