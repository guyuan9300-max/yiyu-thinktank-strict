from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from cloud_backend.app.repositories.cloud_instance import (
    provision_cloud_instance_record,
)
from strict_common.schema import initialize_database, runtime_connection


@dataclass(frozen=True)
class ProvisioningResult:
    cloud_instance_id: str
    created: bool


def provision_cloud_instance(
    database_path: Path,
    *,
    expected_cloud_instance_id: str,
) -> ProvisioningResult:
    """Provision the one authoritative cloud identity outside app startup.

    This is an explicit deployment operation, not runtime schema repair.  It
    only writes an existing ``state_registry`` record kind and is safe to retry
    with the same stable identity.
    """

    expected = expected_cloud_instance_id.strip()
    if not expected:
        raise RuntimeError("expected cloud instance id is required")
    initialize_database(database_path, "cloud")
    with runtime_connection(database_path, "cloud") as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            created = provision_cloud_instance_record(
                connection,
                expected_cloud_instance_id=expected,
            )
            connection.execute("COMMIT")
            return ProvisioningResult(expected, created)
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision one stable strict-cloud instance identity."
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--cloud-instance-id", required=True)
    args = parser.parse_args()
    result = provision_cloud_instance(
        args.database,
        expected_cloud_instance_id=args.cloud_instance_id,
    )
    print(
        json.dumps(
            {
                "cloudInstanceId": result.cloud_instance_id,
                "created": result.created,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
