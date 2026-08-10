from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strict_common.schema import migrate_database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Back up and apply one registered strict-schema migration. "
            "This command is intentionally offline-only."
        )
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--role", required=True, choices=("local", "cloud"))
    parser.add_argument("--from-manifest-hash", required=True)
    parser.add_argument(
        "--confirm-service-stopped",
        action="store_true",
        help="Confirm the database writer/service has been stopped.",
    )
    return parser.parse_args()


def create_backup(source_path: Path, backup_path: Path) -> None:
    source_path = source_path.resolve()
    backup_path = backup_path.resolve()
    if source_path == backup_path:
        raise RuntimeError("backup path must differ from database path")
    if not source_path.is_file():
        raise RuntimeError(f"database does not exist: {source_path}")
    if backup_path.exists():
        raise RuntimeError(f"refusing to overwrite backup: {backup_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("backup quick_check failed")
    except Exception:
        target.close()
        source.close()
        backup_path.unlink(missing_ok=True)
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
    os.chmod(backup_path, 0o600)


def main() -> int:
    args = parse_args()
    if not args.confirm_service_stopped:
        raise RuntimeError(
            "refusing online migration: pass --confirm-service-stopped "
            "only after the database writer/service is stopped"
        )

    database_path = args.database.resolve()
    backup_path = args.backup.resolve()
    create_backup(database_path, backup_path)
    identity = migrate_database(
        database_path,
        args.role,
        expected_from_manifest_hash=args.from_manifest_hash,
    )
    print(
        json.dumps(
            {
                "status": "migrated",
                "role": args.role,
                "database": str(database_path),
                "backup": str(backup_path),
                "manifestHash": identity.manifest_hash,
                "contractVersion": identity.contract_version,
                "databaseGenerationId": identity.database_generation_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
