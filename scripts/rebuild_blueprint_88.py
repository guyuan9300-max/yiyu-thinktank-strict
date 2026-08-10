#!/usr/bin/env python3
"""Offline strict-v4 -> blueprint-88 rebuild.

Only identity, organization structure, sessions and provider configuration cross
the cut-over. Business rows remain exclusively in the archived source database.
The script never attaches the source database to the target database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from strict_common.contracts import CLOUD_CONTRACT, LOCAL_CONTRACT
from strict_common.ids import new_id, utc_now
from strict_common.physical_schema import normalized_structure, structure_sha256, user_tables
from strict_common.schema import database_identity, initialize_database


BUSINESS_TABLE_MARKERS = (
    "task",
    "project",
    "event_line",
    "knowledge",
    "document",
    "intelligence",
    "meeting",
    "planning",
    "weekly",
    "growth",
    "proposal",
    "approval",
)


def _rows(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    if table not in user_tables(connection):
        return []
    return list(connection.execute(f'SELECT * FROM "{table}"'))


def _value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row.keys() and row[key] is not None else default


def _json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError):
        return default
    return parsed


def _insert(connection: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    table_columns = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
    values = dict(values)
    if "projected_at" in table_columns and "projected_at" not in values:
        values["projected_at"] = values.get("updated_at") or values.get("created_at") or utc_now()
    if "projection_state" in table_columns and "projection_state" not in values:
        values["projection_state"] = "fresh"
    if "source_version" in table_columns and "source_version" not in values:
        values["source_version"] = max(1, int(values.get("version") or 1))
    values = {key: value for key, value in values.items() if key in table_columns}
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    quoted = ", ".join(f'"{column}"' for column in columns)
    connection.execute(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
        tuple(values[column] for column in columns),
    )


def _upsert(connection: sqlite3.Connection, table: str, values: dict[str, Any]) -> None:
    table_columns = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
    values = dict(values)
    if "projected_at" in table_columns and "projected_at" not in values:
        values["projected_at"] = values.get("updated_at") or values.get("created_at") or utc_now()
    if "projection_state" in table_columns and "projection_state" not in values:
        values["projection_state"] = "fresh"
    if "source_version" in table_columns and "source_version" not in values:
        values["source_version"] = max(1, int(values.get("version") or 1))
    values = {key: value for key, value in values.items() if key in table_columns}
    columns = list(values)
    placeholders = ", ".join("?" for _ in columns)
    quoted = ", ".join(f'"{column}"' for column in columns)
    assignments = ", ".join(
        f'"{column}"=excluded."{column}"' for column in columns if column != "id"
    )
    connection.execute(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders}) '
        f'ON CONFLICT("id") DO UPDATE SET {assignments}',
        tuple(values[column] for column in columns),
    )


def _base_lifecycle(row: sqlite3.Row, *, created: str, updated: str) -> dict[str, Any]:
    state = str(_value(row, "lifecycle_state", "active"))
    if state not in {"active", "archived", "deleted"}:
        state = "active" if str(_value(row, "status", "active")) == "active" else "archived"
    return {
        "version": max(1, int(_value(row, "version", 1))),
        "lifecycle_state": state,
        "created_at": str(_value(row, "created_at", created)),
        "updated_at": str(_value(row, "updated_at", updated)),
        "deleted_at": updated if state == "deleted" else None,
    }


def _write_secret(
    secret_dir: Path,
    reference_root: str,
    identifier: str,
    payload: dict[str, Any],
) -> tuple[str, str]:
    secret_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(secret_dir, 0o700)
    safe_name = hashlib.sha256(identifier.encode("utf-8")).hexdigest() + ".json"
    destination = secret_dir / safe_name
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    reference = reference_root.rstrip("/") + "/" + safe_name
    return reference, hashlib.sha256(encoded).hexdigest()[:16]


def _organization_scope_id(organization_id: str) -> str:
    return f"scope_organization_{organization_id}"


def _target_identity(connection: sqlite3.Connection) -> dict[str, str]:
    row = connection.execute(
        """
        SELECT CAST(version AS TEXT), manifest_hash, database_generation_id
        FROM schema_versions WHERE status='active'
        ORDER BY activated_at DESC LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("target has no active schema identity")
    return {
        "contract_version": str(row[0]),
        "manifest_hash": str(row[1]),
        "database_generation_id": str(row[2]),
    }


def _insert_scope(
    target: sqlite3.Connection,
    *,
    scope_id: str,
    organization_id: str | None,
    principal_id: str | None,
    now: str,
) -> None:
    _upsert(
        target,
        "authorization_scopes",
        {
            "id": scope_id,
            "scope_kind": "organization" if organization_id else "principal",
            "principal_id": principal_id,
            "organization_id": organization_id,
            "policy_version": 1,
            "created_at": now,
            "updated_at": now,
            "status": "active",
            "version": 1,
            "lifecycle_state": "active",
            "deleted_at": None,
        },
    )


def _migrate_cloud(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    secret_dir: Path,
    secret_reference_root: str,
    now: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    principals: set[str] = set()

    for row in _rows(source, "identity_principals"):
        principal_id = str(row["principal_id"])
        principals.add(principal_id)
        life = _base_lifecycle(row, created=now, updated=now)
        _insert(
            target,
            "principals",
            {
                "id": principal_id,
                "status": str(_value(row, "status", "active")),
                "identity_version": max(1, int(_value(row, "identity_version", 1))),
                "updated_at": life["updated_at"],
                "principal_kind": str(_value(row, "principal_kind", "person")),
                "parent_principal_id": None,
                "display_name": _value(row, "display_name"),
                "contact_type": None,
                "normalized_contact": None,
                "verification_state": None,
                "credential_type": None,
                "secret_reference": None,
                "secret_fingerprint": None,
                "credential_state": None,
                **life,
            },
        )
    counts["principals"] = len(principals)

    for row in _rows(source, "identity_contacts"):
        life = _base_lifecycle(row, created=now, updated=now)
        _insert(
            target,
            "principals",
            {
                "id": str(row["contact_id"]),
                "status": "active",
                "identity_version": max(1, int(_value(row, "version", 1))),
                "updated_at": life["updated_at"],
                "principal_kind": "contact",
                "parent_principal_id": str(row["principal_id"]),
                "display_name": None,
                "contact_type": _value(row, "contact_type"),
                "normalized_contact": _value(row, "normalized_value"),
                "verification_state": _value(row, "verification_state"),
                "credential_type": None,
                "secret_reference": None,
                "secret_fingerprint": None,
                "credential_state": None,
                **life,
            },
        )
        counts["contacts"] = counts.get("contacts", 0) + 1

    for row in _rows(source, "identity_credentials"):
        reference, fingerprint = _write_secret(
            secret_dir,
            secret_reference_root,
            str(row["credential_id"]),
            {
                "kind": "password_credential",
                "hashScheme": str(_value(row, "hash_scheme", "scrypt-v1")),
                "secretHash": str(row["secret_hash"]),
            },
        )
        life = _base_lifecycle(row, created=now, updated=now)
        _insert(
            target,
            "principals",
            {
                "id": str(row["credential_id"]),
                "status": str(_value(row, "status", "active")),
                "identity_version": max(1, int(_value(row, "version", 1))),
                "updated_at": life["updated_at"],
                "principal_kind": "credential",
                "parent_principal_id": str(row["principal_id"]),
                "display_name": None,
                "contact_type": None,
                "normalized_contact": None,
                "verification_state": None,
                "credential_type": _value(row, "credential_type"),
                "secret_reference": reference,
                "secret_fingerprint": fingerprint,
                "credential_state": str(_value(row, "status", "active")),
                **life,
            },
        )
        counts["credentials"] = counts.get("credentials", 0) + 1

    organization_ids: set[str] = set()
    for row in _rows(source, "organization_records"):
        organization_id = str(row["organization_id"])
        organization_ids.add(organization_id)
        life = _base_lifecycle(row, created=now, updated=now)
        strategy = _value(row, "annual_strategy")
        _insert(
            target,
            "organizations",
            {
                "id": organization_id,
                "record_kind": "organization",
                "parent_record_id": None,
                "name": _value(row, "name"),
                "color": None,
                "mission": None,
                "business_context": None,
                "team_context": None,
                "level": None,
                "manager_record_id": None,
                "annual_goal": _value(row, "annual_goal"),
                "strategy_spec_schema_version": "v1" if strategy else None,
                "strategy_spec": json.dumps({"text": strategy}, ensure_ascii=False) if strategy else None,
                **life,
            },
        )
        _insert_scope(
            target,
            scope_id=_organization_scope_id(organization_id),
            organization_id=organization_id,
            principal_id=None,
            now=now,
        )
    counts["organizations"] = len(organization_ids)

    for row in _rows(source, "organization_departments"):
        life = _base_lifecycle(row, created=now, updated=now)
        _insert(
            target,
            "organizations",
            {
                "id": str(row["department_id"]),
                "record_kind": "department",
                "parent_record_id": _value(row, "parent_department_id") or str(row["organization_id"]),
                "name": _value(row, "name"),
                "color": _value(row, "color"),
                "mission": _value(row, "mission"),
                "business_context": _value(row, "business_context"),
                "team_context": _value(row, "team_context"),
                "level": None,
                "manager_record_id": None,
                "annual_goal": None,
                "strategy_spec_schema_version": None,
                "strategy_spec": None,
                **life,
            },
        )
        counts["departments"] = counts.get("departments", 0) + 1

    for row in _rows(source, "management_titles"):
        life = _base_lifecycle(row, created=now, updated=now)
        _insert(
            target,
            "organizations",
            {
                "id": str(row["title_id"]),
                "record_kind": "management_title",
                "parent_record_id": str(row["organization_id"]),
                "name": _value(row, "name"),
                "color": None,
                "mission": None,
                "business_context": None,
                "team_context": None,
                "level": _value(row, "level"),
                "manager_record_id": None,
                "annual_goal": None,
                "strategy_spec_schema_version": None,
                "strategy_spec": None,
                **life,
            },
        )
        counts["management_titles"] = counts.get("management_titles", 0) + 1

    memberships: dict[str, str] = {}
    for row in _rows(source, "organization_memberships"):
        organization_id = str(row["organization_id"])
        scope_id = str(_value(row, "scope_id", _organization_scope_id(organization_id)))
        _insert_scope(target, scope_id=scope_id, organization_id=organization_id, principal_id=None, now=now)
        membership_id = str(row["membership_id"])
        memberships[membership_id] = scope_id
        life = _base_lifecycle(row, created=now, updated=now)
        _insert(
            target,
            "organization_memberships",
            {
                "id": membership_id,
                "scope_id": scope_id,
                "principal_id": str(row["principal_id"]),
                "role_key": _value(row, "system_role"),
                "status": str(_value(row, "status", "active")),
                "record_kind": "membership",
                "parent_membership_id": None,
                "department_id": None,
                "title_id": None,
                "manager_membership_id": None,
                "visibility_scope": _value(row, "visibility_scope"),
                "capability_set_schema_version": None,
                "capability_set": None,
                "target_type": None,
                "target_id": None,
                "expires_at": None,
                **life,
            },
        )
    counts["memberships"] = len(memberships)

    for row in _rows(source, "department_memberships"):
        membership_id = str(row["membership_id"])
        if membership_id not in memberships:
            continue
        life = _base_lifecycle(row, created=now, updated=now)
        _insert(
            target,
            "organization_memberships",
            {
                "id": str(row["department_membership_id"]),
                "scope_id": memberships[membership_id],
                "principal_id": None,
                "role_key": "department_lead" if int(_value(row, "is_department_lead", 0)) else "member",
                "status": str(_value(row, "status", "active")),
                "record_kind": "department_assignment",
                "parent_membership_id": membership_id,
                "department_id": str(row["department_id"]),
                "title_id": None,
                "manager_membership_id": None,
                "visibility_scope": None,
                "capability_set_schema_version": None,
                "capability_set": None,
                "target_type": None,
                "target_id": None,
                "expires_at": None,
                **life,
            },
        )
        counts["department_assignments"] = counts.get("department_assignments", 0) + 1

    for row in _rows(source, "organization_reporting_lines"):
        report_id = str(row["report_membership_id"])
        manager_id = str(row["manager_membership_id"])
        if report_id not in memberships or manager_id not in memberships:
            continue
        life = _base_lifecycle(row, created=now, updated=now)
        _insert(
            target,
            "organization_memberships",
            {
                "id": str(row["reporting_line_id"]),
                "scope_id": memberships[report_id],
                "principal_id": None,
                "role_key": _value(row, "line_type"),
                "status": "active" if life["lifecycle_state"] == "active" else "inactive",
                "record_kind": "reporting_line",
                "parent_membership_id": report_id,
                "department_id": None,
                "title_id": None,
                "manager_membership_id": manager_id,
                "visibility_scope": None,
                "capability_set_schema_version": None,
                "capability_set": None,
                "target_type": None,
                "target_id": None,
                "expires_at": None,
                **life,
            },
        )
        counts["reporting_lines"] = counts.get("reporting_lines", 0) + 1

    instance_rows = _rows(source, "identity_cloud_instances")
    if instance_rows:
        row = instance_rows[0]
        _insert(
            target,
            "state_registry",
            {
                "id": str(row["cloud_instance_id"]),
                "state_id": str(row["cloud_instance_id"]),
                "target_blueprint_node": "cloud_instance",
                "target_role": "cloud",
                "disposition": str(_value(row, "status", "active")),
                "owner": None,
                "recovery_rule": None,
                "exit_condition": None,
                "record_kind": "cloud_instance",
                "recovery_rule_schema_version": None,
                "observed_at": str(_value(row, "updated_at", now)),
                "version": max(1, int(_value(row, "version", 1))),
                "lifecycle_state": "active",
                "created_at": str(_value(row, "created_at", now)),
                "updated_at": str(_value(row, "updated_at", now)),
                "deleted_at": None,
            },
        )
        counts["cloud_instances"] = 1

    cloud_instance_id = str(instance_rows[0]["cloud_instance_id"]) if instance_rows else ""
    target_identity = _target_identity(target)
    for row in _rows(source, "authentication_sessions"):
        membership_id = str(row["membership_id"])
        if membership_id not in memberships:
            continue
        status = str(_value(row, "status", "active"))
        life = "active" if status == "active" else "archived"
        _insert(
            target,
            "sandboxes",
            {
                "id": str(row["session_id"]),
                "scope_id": memberships[membership_id],
                "principal_id": str(row["principal_id"]),
                "membership_id": membership_id,
                "cloud_api_url": None,
                "secret_reference": None,
                "secret_fingerprint": None,
                "access_secret_hash": _value(row, "access_secret_hash"),
                "refresh_secret_hash": _value(row, "refresh_secret_hash"),
                "access_expires_at": _value(row, "expires_at"),
                "refresh_expires_at": _value(row, "refresh_expires_at"),
                "last_seen_at": _value(row, "last_seen_at"),
                "device_id": None,
                "replica_epoch": None,
                "sync_cursor": None,
                "record_kind": "server_session",
                "cloud_instance_id": cloud_instance_id or _value(row, "cloud_instance_id"),
                "database_generation_id": target_identity["database_generation_id"],
                "sandbox_kind": "organization",
                "display_name": None,
                "runtime_status": status,
                "contract_version": target_identity["contract_version"],
                "manifest_hash": target_identity["manifest_hash"],
                "lease_expires_at": _value(row, "refresh_expires_at"),
                "last_verified_at": _value(row, "last_seen_at", now),
                "version": max(1, int(_value(row, "version", 1))),
                "lifecycle_state": life,
                "created_at": str(_value(row, "issued_at", now)),
                "updated_at": str(_value(row, "last_seen_at", now)),
                "deleted_at": None,
                "authority_role": "cloud",
                "origin_instance_id": cloud_instance_id,
                "source_version": max(1, int(_value(row, "version", 1))),
                "projection_state": "authoritative",
                "projected_at": str(_value(row, "last_seen_at", now)),
                "stale_at": None,
            },
        )
        counts["sessions"] = counts.get("sessions", 0) + 1

    def add_provider(
        *,
        identifier: str,
        scope_id: str,
        provider: str,
        resource_kind: str,
        remote_id: str | None,
        display_name: str | None,
        endpoint: str | None,
        model_name: str | None,
        public_config: Any,
        secret_payload: dict[str, Any] | None,
        status: str,
        owner_membership_id: str | None,
        version: int,
        created_at: str,
        updated_at: str,
    ) -> None:
        reference = None
        fingerprint = None
        if secret_payload:
            reference, fingerprint = _write_secret(
                secret_dir, secret_reference_root, identifier, secret_payload
            )
        public_value = json.dumps(public_config, ensure_ascii=False, sort_keys=True) if public_config else None
        _upsert(
            target,
            "provider_resources",
            {
                "id": identifier,
                "scope_id": scope_id,
                "provider": provider,
                "resource_kind": resource_kind,
                "remote_id": remote_id,
                "retention_state": "retained",
                "owner_kind": "membership" if owner_membership_id else "organization",
                "owner_principal_id": None,
                "owner_membership_id": owner_membership_id,
                "display_name": display_name,
                "endpoint": endpoint,
                "model_name": model_name,
                "public_config_schema_version": "v1" if public_value else None,
                "public_config": public_value,
                "secret_reference": reference,
                "secret_fingerprint": fingerprint,
                "status": status,
                "verified_at": updated_at,
                "version": max(1, version),
                "lifecycle_state": "active",
                "created_at": created_at,
                "updated_at": updated_at,
                "deleted_at": None,
                "authority_role": "cloud",
                "origin_instance_id": cloud_instance_id,
            },
        )
        counts["provider_resources"] = counts.get("provider_resources", 0) + 1

    for row in _rows(source, "organization_ai_configs"):
        organization_id = str(row["organization_id"])
        add_provider(
            identifier=str(row["config_id"]),
            scope_id=_organization_scope_id(organization_id),
            provider=str(_value(row, "provider", "unknown")),
            resource_kind="organization_ai_configuration",
            remote_id=None,
            display_name="组织大模型",
            endpoint=_value(row, "base_url"),
            model_name=_value(row, "model_name"),
            public_config=None,
            secret_payload={"encryptedApiKey": str(row["encrypted_api_key"])} if _value(row, "encrypted_api_key") else None,
            status=str(_value(row, "status", "ready")),
            owner_membership_id=None,
            version=int(_value(row, "config_version", 1)),
            created_at=str(_value(row, "created_at", now)),
            updated_at=str(_value(row, "updated_at", now)),
        )

    for row in _rows(source, "scoped_configuration_records"):
        organization_id = str(row["organization_id"])
        scope_id = str(_value(row, "scope_id", _organization_scope_id(organization_id)))
        _insert_scope(target, scope_id=scope_id, organization_id=organization_id, principal_id=None, now=now)
        add_provider(
            identifier=str(row["configuration_id"]),
            scope_id=scope_id,
            provider=str(_value(row, "provider", "internal")),
            resource_kind=str(row["configuration_kind"]),
            remote_id=None,
            display_name=str(row["configuration_kind"]),
            endpoint=None,
            model_name=None,
            public_config=_json(_value(row, "public_config_json"), {}),
            secret_payload={"encryptedSecretBundle": str(row["encrypted_secret_bundle"]), "envelopeVersion": int(_value(row, "secret_envelope_version", 1))} if _value(row, "encrypted_secret_bundle") else None,
            status="active" if str(_value(row, "lifecycle_state", "active")) == "active" else "archived",
            owner_membership_id=_value(row, "membership_id"),
            version=int(_value(row, "version", 1)),
            created_at=str(_value(row, "created_at", now)),
            updated_at=str(_value(row, "updated_at", now)),
        )

    for row in _rows(source, "external_provider_resources"):
        organization_id = str(row["organization_id"])
        scope_id = str(_value(row, "scope_id", _organization_scope_id(organization_id)))
        _insert_scope(target, scope_id=scope_id, organization_id=organization_id, principal_id=None, now=now)
        add_provider(
            identifier=str(row["provider_resource_id"]),
            scope_id=scope_id,
            provider=str(_value(row, "provider", "external")),
            resource_kind=str(_value(row, "resource_kind", "external_resource")),
            remote_id=_value(row, "remote_id"),
            display_name=None,
            endpoint=None,
            model_name=None,
            public_config=None,
            secret_payload=None,
            status="active",
            owner_membership_id=None,
            version=int(_value(row, "version", 1)),
            created_at=str(_value(row, "created_at", now)),
            updated_at=str(_value(row, "updated_at", now)),
        )
    return counts


def _migrate_local(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    *,
    now: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    organizations: dict[str, str] = {}
    for row in _rows(source, "projection_organizations"):
        organization_id = str(row["organization_id"])
        sandbox_id = str(row["sandbox_id"])
        organizations[organization_id] = sandbox_id
        metadata = _json(_value(row, "metadata_json"), {})
        _insert(
            target,
            "organizations",
            {
                "id": organization_id,
                "record_kind": "organization",
                "parent_record_id": None,
                "name": _value(row, "name"),
                "color": None,
                "mission": None,
                "business_context": None,
                "team_context": None,
                "level": None,
                "manager_record_id": None,
                "annual_goal": None,
                "strategy_spec_schema_version": None,
                "strategy_spec": None,
                "version": max(1, int(_value(row, "source_version", metadata.get("version", 1)))),
                "lifecycle_state": "active",
                "created_at": str(_value(row, "refreshed_at", now)),
                "updated_at": str(_value(row, "refreshed_at", now)),
                "deleted_at": None,
            },
        )
        _insert_scope(target, scope_id=_organization_scope_id(organization_id), organization_id=organization_id, principal_id=None, now=now)
    counts["organizations"] = len(organizations)

    principals: set[str] = set()
    for row in _rows(source, "projection_principals"):
        principal_id = str(row["principal_id"])
        if principal_id in principals:
            continue
        principals.add(principal_id)
        _insert(
            target,
            "principals",
            {
                "id": principal_id,
                "status": "active",
                "identity_version": max(1, int(_value(row, "source_version", 1))),
                "updated_at": str(_value(row, "refreshed_at", now)),
                "principal_kind": "person",
                "parent_principal_id": None,
                "display_name": _value(row, "display_name"),
                "contact_type": None,
                "normalized_contact": None,
                "verification_state": None,
                "credential_type": None,
                "secret_reference": None,
                "secret_fingerprint": None,
                "credential_state": None,
                "version": max(1, int(_value(row, "source_version", 1))),
                "lifecycle_state": "active",
                "created_at": str(_value(row, "refreshed_at", now)),
                "deleted_at": None,
            },
        )
    counts["principals"] = len(principals)

    for row in _rows(source, "projection_departments"):
        organization_id = str(row["organization_id"])
        if organization_id not in organizations:
            continue
        metadata = _json(_value(row, "metadata_json"), {})
        _insert(
            target,
            "organizations",
            {
                "id": str(row["department_id"]),
                "record_kind": "department",
                "parent_record_id": organization_id,
                "name": _value(row, "name"),
                "color": metadata.get("color"),
                "mission": metadata.get("mission"),
                "business_context": metadata.get("businessContext"),
                "team_context": metadata.get("teamContext"),
                "level": None,
                "manager_record_id": None,
                "annual_goal": None,
                "strategy_spec_schema_version": None,
                "strategy_spec": None,
                "version": max(1, int(_value(row, "source_version", 1))),
                "lifecycle_state": "active",
                "created_at": str(_value(row, "refreshed_at", now)),
                "updated_at": str(_value(row, "refreshed_at", now)),
                "deleted_at": None,
            },
        )
        counts["departments"] = counts.get("departments", 0) + 1

    membership_scope: dict[str, str] = {}
    membership_sandbox: dict[str, str] = {}
    for row in _rows(source, "projection_memberships"):
        organization_id = str(row["organization_id"])
        principal_id = str(row["principal_id"])
        if organization_id not in organizations or principal_id not in principals:
            continue
        metadata = _json(_value(row, "metadata_json"), {})
        scope_id = _organization_scope_id(organization_id)
        membership_id = str(row["membership_id"])
        membership_scope[membership_id] = scope_id
        membership_sandbox[membership_id] = str(row["sandbox_id"])
        _insert(
            target,
            "organization_memberships",
            {
                "id": membership_id,
                "scope_id": scope_id,
                "principal_id": principal_id,
                "role_key": metadata.get("systemRole"),
                "status": str(_value(row, "status", "active")),
                "record_kind": "membership",
                "parent_membership_id": None,
                "department_id": None,
                "title_id": None,
                "manager_membership_id": None,
                "visibility_scope": metadata.get("visibilityScope"),
                "capability_set_schema_version": None,
                "capability_set": None,
                "target_type": None,
                "target_id": None,
                "expires_at": None,
                "version": max(1, int(_value(row, "source_version", 1))),
                "lifecycle_state": "active",
                "created_at": str(_value(row, "refreshed_at", now)),
                "updated_at": str(_value(row, "refreshed_at", now)),
                "deleted_at": None,
            },
        )
    counts["memberships"] = len(membership_scope)

    bindings = {str(row["sandbox_id"]): row for row in _rows(source, "workspace_bindings")}
    target_identity = _target_identity(target)
    sandbox_rows = {
        str(row["sandbox_id"]): row
        for row in _rows(source, "workspace_sandboxes")
        if str(_value(row, "sandbox_kind", "")) != "local_draft"
    }
    for sandbox_id, row in sandbox_rows.items():
        binding = bindings.get(sandbox_id)
        if binding is None:
            continue
        organization_id = str(binding["organization_id"])
        if organization_id not in organizations:
            continue
        _insert(
            target,
            "sandboxes",
            {
                "id": sandbox_id,
                "scope_id": _organization_scope_id(organization_id),
                "principal_id": None,
                "membership_id": None,
                "cloud_api_url": _value(binding, "cloud_api_url"),
                "secret_reference": None,
                "secret_fingerprint": None,
                "access_secret_hash": None,
                "refresh_secret_hash": None,
                "access_expires_at": None,
                "refresh_expires_at": None,
                "last_seen_at": _value(binding, "verified_at"),
                "device_id": _value(row, "device_id"),
                "replica_epoch": int(_value(row, "replica_epoch", 1)),
                "sync_cursor": None,
                "record_kind": "sandbox",
                "cloud_instance_id": _value(binding, "cloud_instance_id"),
                "database_generation_id": _value(binding, "database_generation_id"),
                "sandbox_kind": "organization",
                "display_name": _value(row, "display_name"),
                "runtime_status": _value(row, "runtime_status"),
                "contract_version": target_identity["contract_version"],
                "manifest_hash": target_identity["manifest_hash"],
                "lease_expires_at": None,
                "last_verified_at": _value(binding, "verified_at"),
                "version": max(1, int(_value(row, "version", 1))),
                "lifecycle_state": "active" if int(_value(row, "is_active", 0)) else "archived",
                "created_at": str(_value(row, "created_at", now)),
                "updated_at": str(_value(row, "updated_at", now)),
                "deleted_at": None,
                "authority_role": "local",
                "origin_instance_id": _value(row, "device_id"),
                "source_version": max(1, int(_value(row, "version", 1))),
                "projection_state": "authoritative",
                "projected_at": str(_value(row, "updated_at", now)),
                "stale_at": None,
            },
        )
        counts["sandboxes"] = counts.get("sandboxes", 0) + 1
        _insert(
            target,
            "sandboxes",
            {
                "id": str(binding["binding_id"]),
                "scope_id": _organization_scope_id(organization_id),
                "principal_id": None,
                "membership_id": None,
                "cloud_api_url": _value(binding, "cloud_api_url"),
                "secret_reference": None,
                "secret_fingerprint": None,
                "access_secret_hash": None,
                "refresh_secret_hash": None,
                "access_expires_at": None,
                "refresh_expires_at": None,
                "last_seen_at": _value(binding, "verified_at"),
                "device_id": _value(row, "device_id"),
                "replica_epoch": int(_value(row, "replica_epoch", 1)),
                "sync_cursor": None,
                "record_kind": "binding",
                "cloud_instance_id": _value(binding, "cloud_instance_id"),
                "database_generation_id": _value(binding, "database_generation_id"),
                "sandbox_kind": "organization",
                "display_name": _value(row, "display_name"),
                "runtime_status": _value(binding, "identity_state"),
                "contract_version": target_identity["contract_version"],
                "manifest_hash": target_identity["manifest_hash"],
                "lease_expires_at": None,
                "last_verified_at": _value(binding, "verified_at"),
                "version": max(1, int(_value(binding, "version", 1))),
                "lifecycle_state": "active",
                "created_at": str(_value(row, "created_at", now)),
                "updated_at": str(_value(binding, "updated_at", now)),
                "deleted_at": None,
                "authority_role": "local",
                "origin_instance_id": _value(row, "device_id"),
                "source_version": max(1, int(_value(binding, "version", 1))),
                "projection_state": "authoritative",
                "projected_at": str(_value(binding, "updated_at", now)),
                "stale_at": None,
            },
        )
        counts["bindings"] = counts.get("bindings", 0) + 1

    for row in _rows(source, "workspace_session_snapshots"):
        sandbox_id = str(row["sandbox_id"])
        binding = bindings.get(sandbox_id)
        membership_id = str(row["membership_id"])
        if binding is None or membership_id not in membership_scope:
            continue
        _insert(
            target,
            "sandboxes",
            {
                "id": str(row["session_snapshot_id"]),
                "scope_id": membership_scope[membership_id],
                "principal_id": str(row["principal_id"]),
                "membership_id": membership_id,
                "cloud_api_url": _value(binding, "cloud_api_url"),
                "secret_reference": _value(row, "secret_ref"),
                "secret_fingerprint": _value(row, "credential_fingerprint"),
                "access_secret_hash": None,
                "refresh_secret_hash": None,
                "access_expires_at": None,
                "refresh_expires_at": None,
                "last_seen_at": _value(row, "verified_at"),
                "device_id": None,
                "replica_epoch": None,
                "sync_cursor": None,
                "record_kind": "local_session_snapshot",
                "cloud_instance_id": _value(binding, "cloud_instance_id"),
                "database_generation_id": _value(binding, "database_generation_id"),
                "sandbox_kind": "organization",
                "display_name": None,
                "runtime_status": str(_value(row, "status", "active")),
                "contract_version": target_identity["contract_version"],
                "manifest_hash": target_identity["manifest_hash"],
                "lease_expires_at": None,
                "last_verified_at": _value(row, "verified_at"),
                "version": max(1, int(_value(row, "version", 1))),
                "lifecycle_state": "active",
                "created_at": str(_value(row, "updated_at", now)),
                "updated_at": str(_value(row, "updated_at", now)),
                "deleted_at": None,
                "authority_role": "local",
                "origin_instance_id": _value(binding, "cloud_instance_id"),
                "source_version": max(1, int(_value(row, "version", 1))),
                "projection_state": "authoritative",
                "projected_at": str(_value(row, "verified_at", now)),
                "stale_at": None,
            },
        )
        counts["session_snapshots"] = counts.get("session_snapshots", 0) + 1
    counts["discarded_local_draft_sandboxes"] = sum(
        1 for row in _rows(source, "workspace_sandboxes")
        if str(_value(row, "sandbox_kind", "")) == "local_draft"
    )
    return counts


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _business_counts(source: sqlite3.Connection) -> dict[str, int]:
    result: dict[str, int] = {}
    for table in sorted(user_tables(source)):
        if any(marker in table for marker in BUSINESS_TABLE_MARKERS):
            count = int(source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            if count:
                result[table] = count
    return result


def rebuild(
    *,
    source_path: Path,
    target_path: Path,
    role: str,
    report_path: Path,
    secret_dir: Path | None,
    secret_reference_root: str | None,
) -> dict[str, Any]:
    if target_path.exists():
        raise RuntimeError(f"target already exists: {target_path}")
    source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("PRAGMA query_only=ON")
    if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise RuntimeError("source quick_check failed")
    now = utc_now()
    initialize_database(target_path, role)  # type: ignore[arg-type]
    target = sqlite3.connect(target_path)
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys=ON")
    target.execute("BEGIN IMMEDIATE")
    try:
        if role == "cloud":
            if secret_dir is None or not secret_reference_root:
                raise RuntimeError("cloud rebuild requires secret directory and reference root")
            preserved = _migrate_cloud(
                source,
                target,
                secret_dir=secret_dir,
                secret_reference_root=secret_reference_root,
                now=now,
            )
        else:
            preserved = _migrate_local(source, target, now=now)
        violations = target.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"target has {len(violations)} foreign-key violations")
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()

    verification = sqlite3.connect(f"file:{target_path.resolve()}?mode=ro", uri=True)
    verification.execute("PRAGMA query_only=ON")
    structure = normalized_structure(verification)
    identity = database_identity(target_path, role)  # type: ignore[arg-type]
    archived_source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    archived_business_counts = _business_counts(archived_source)
    archived_source.close()
    report = {
        "role": role,
        "sourcePath": str(source_path.resolve()),
        "sourceSha256": _sha256(source_path),
        "targetPath": str(target_path.resolve()),
        "targetSha256": _sha256(target_path),
        "tableCount": structure["table_count"],
        "structureSha256": structure_sha256(structure),
        "quickCheck": verification.execute("PRAGMA quick_check").fetchone()[0],
        "foreignKeyViolationCount": len(verification.execute("PRAGMA foreign_key_check").fetchall()),
        "schemaFamily": identity.schema_family,
        "contractVersion": identity.contract_version,
        "manifestHash": identity.manifest_hash,
        "databaseGenerationId": identity.database_generation_id,
        "preservedCounts": preserved,
        "archivedBusinessRowCounts": archived_business_counts,
        "createdAt": now,
    }
    verification.close()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--role", choices=("local", "cloud"), required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--secret-dir", type=Path)
    parser.add_argument("--secret-reference-root")
    args = parser.parse_args()
    report = rebuild(
        source_path=args.source,
        target_path=args.target,
        role=args.role,
        report_path=args.report,
        secret_dir=args.secret_dir,
        secret_reference_root=args.secret_reference_root,
    )
    print(json.dumps({
        "role": report["role"],
        "tableCount": report["tableCount"],
        "quickCheck": report["quickCheck"],
        "foreignKeyViolationCount": report["foreignKeyViolationCount"],
        "manifestHash": report["manifestHash"],
        "structureSha256": report["structureSha256"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
