#!/usr/bin/env python3
"""Offline activation for the GC-03 build-level project scope ADR."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from strict_common.project_scope import seed_project_scope_decision
from strict_common.schema import verify_database


def activate(database: Path, role: str) -> None:
    database = database.resolve()
    verify_database(database, role)  # type: ignore[arg-type]
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT id FROM schema_versions
            WHERE status='active'
            ORDER BY activated_at DESC, created_at DESC LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("database has no active schema build identity")
        seed_project_scope_decision(
            connection,
            schema_version_id=str(row[0]),
        )
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise RuntimeError(
                f"project scope activation created {len(foreign_keys)} foreign key violations"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--role", required=True, choices=("local", "cloud"))
    args = parser.parse_args()
    activate(args.database, args.role)


if __name__ == "__main__":
    main()
