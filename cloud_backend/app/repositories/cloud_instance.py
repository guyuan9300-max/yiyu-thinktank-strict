from __future__ import annotations

import sqlite3

from strict_common.ids import new_id, utc_now


def active_cloud_instance_ids(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT state_id FROM state_registry
        WHERE record_kind='cloud_instance' AND lifecycle_state='active'
        ORDER BY created_at, id
        """
    ).fetchall()
    return [str(row["state_id"]) for row in rows]


def require_cloud_instance(
    connection: sqlite3.Connection,
    *,
    expected_cloud_instance_id: str | None,
) -> str:
    if not expected_cloud_instance_id:
        raise RuntimeError("configured cloud instance id is required")
    active_ids = active_cloud_instance_ids(connection)
    if not active_ids:
        raise RuntimeError("strict cloud has no active cloud_instance state")
    if len(active_ids) != 1:
        raise RuntimeError("strict cloud has multiple active cloud_instance states")
    actual = active_ids[0]
    if expected_cloud_instance_id != actual:
        raise RuntimeError("configured cloud instance id does not match database")
    return actual


def provision_cloud_instance_record(
    connection: sqlite3.Connection,
    *,
    expected_cloud_instance_id: str,
) -> bool:
    rows = connection.execute(
        """
        SELECT state_id, lifecycle_state FROM state_registry
        WHERE record_kind='cloud_instance'
        ORDER BY created_at, id
        """
    ).fetchall()
    active = [row for row in rows if str(row["lifecycle_state"]) == "active"]
    if len(active) > 1:
        raise RuntimeError("strict cloud has multiple active cloud_instance states")
    if active:
        actual = str(active[0]["state_id"])
        if actual != expected_cloud_instance_id:
            raise RuntimeError("configured cloud instance id does not match database")
        return False
    if any(str(row["state_id"]) == expected_cloud_instance_id for row in rows):
        raise RuntimeError("configured cloud instance exists but is not active")

    now = utc_now()
    connection.execute(
        """
        INSERT INTO state_registry (
            id, state_id, target_blueprint_node, target_role, disposition,
            owner, recovery_rule, exit_condition, record_kind,
            recovery_rule_schema_version, observed_at, version,
            lifecycle_state, created_at, updated_at, deleted_at
        ) VALUES (?, ?, 'cloud-instance', 'cloud', 'authoritative',
                  'strict-cloud-provisioner', NULL, NULL, 'cloud_instance',
                  NULL, ?, 1, 'active', ?, ?, NULL)
        """,
        (new_id(), expected_cloud_instance_id, now, now, now),
    )
    return True
