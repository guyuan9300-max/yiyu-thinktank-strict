from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud_backend.app.repositories.gc01_authorization import (
    backfill_authorization_projections,
)
from strict_common.physical_schema import normalized_structure, structure_sha256
from strict_common.schema import initialize_database, runtime_connection


def backfill(database: Path, cloud_instance_id: str | None) -> dict[str, object]:
    identity = initialize_database(database, "cloud")
    with runtime_connection(database, "cloud") as connection:
        before_structure = structure_sha256(normalized_structure(connection))
        resolved_instance_id = cloud_instance_id
        if not resolved_instance_id:
            row = connection.execute(
                """
                SELECT state_id FROM state_registry
                WHERE record_kind='cloud_instance' AND lifecycle_state='active'
                ORDER BY created_at LIMIT 1
                """
            ).fetchone()
            if row is None:
                raise RuntimeError("strict cloud has no active cloud instance")
            resolved_instance_id = str(row["state_id"])
        counts = backfill_authorization_projections(
            connection,
            origin_instance_id=resolved_instance_id,
        )
        after_structure = structure_sha256(normalized_structure(connection))
        quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    if before_structure != after_structure or quick_check != "ok" or foreign_key_errors:
        raise RuntimeError("GC-01 authorization backfill post-check failed")
    return {
        "database": str(database),
        "cloudInstanceId": resolved_instance_id,
        "contractVersion": identity.contract_version,
        "manifestHash": identity.manifest_hash,
        "structureHash": after_structure,
        "counts": counts,
        "quickCheck": quick_check,
        "foreignKeyErrors": foreign_key_errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--cloud-instance-id")
    args = parser.parse_args()
    print(
        json.dumps(
            backfill(args.database.resolve(), args.cloud_instance_id),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
