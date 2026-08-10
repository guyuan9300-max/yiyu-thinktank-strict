from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .ids import canonical_json


DatabaseRole = Literal["local", "cloud"]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = PROJECT_ROOT / "contracts"


@dataclass(frozen=True)
class SchemaContract:
    role: DatabaseRole
    manifest_id: str
    schema_family: str
    database_role: str
    contract_version: str
    manifest_hash: str
    allowed_tables: frozenset[str]
    required_keys: dict[str, frozenset[str]]
    required_pragmas: dict[str, Any]
    raw: dict[str, Any]


def _manifest_path(role: DatabaseRole) -> Path:
    return CONTRACTS_DIR / f"strict-{role}-schema-manifest.v1.json"


def _hash_path(role: DatabaseRole) -> Path:
    return CONTRACTS_DIR / f"strict-{role}-schema-manifest.v1.canonical.sha256"


def load_schema_contract(role: DatabaseRole) -> SchemaContract:
    manifest_path = _manifest_path(role)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    canonical_hash = hashlib.sha256(
        canonical_json(raw).encode("utf-8")
    ).hexdigest()
    frozen_hash = _hash_path(role).read_text(encoding="utf-8").split()[0]
    if canonical_hash != frozen_hash:
        raise RuntimeError(
            f"{role} schema manifest hash mismatch: "
            f"expected={frozen_hash} actual={canonical_hash}"
        )
    if raw.get("status") != "FROZEN_FOR_IMPLEMENTATION":
        raise RuntimeError(f"{role} schema manifest is not frozen")
    table_specs = raw["allowedTables"]
    tables = frozenset(str(item["name"]) for item in table_specs)
    if len(tables) != len(table_specs):
        raise RuntimeError(f"{role} schema manifest contains duplicate table names")
    if len(tables) != 88:
        raise RuntimeError(f"{role} schema manifest must contain exactly 88 tables")
    required_keys: dict[str, frozenset[str]] = {}
    for item in table_specs:
        fields = item.get("fields")
        if not isinstance(fields, list) or not fields:
            raise RuntimeError(
                f"{role} physical schema table has no field definitions: {item['name']}"
            )
        field_names = [str(field["name"]) for field in fields]
        if len(field_names) != len(set(field_names)):
            raise RuntimeError(
                f"{role} physical schema table has duplicate fields: {item['name']}"
            )
        required_keys[str(item["name"])] = frozenset(
            str(field["name"])
            for field in fields
            if bool(field.get("primary_key")) or not bool(field.get("nullable", True))
        )
    return SchemaContract(
        role=role,
        manifest_id=str(raw["manifestId"]),
        schema_family=str(raw["schemaFamily"]),
        database_role=str(raw["databaseRole"]),
        contract_version=str(raw.get("contractVersion") or "1"),
        manifest_hash=canonical_hash,
        allowed_tables=tables,
        required_keys=required_keys,
        required_pragmas=dict(raw["requiredPragmas"]),
        raw=raw,
    )


LOCAL_CONTRACT = load_schema_contract("local")
CLOUD_CONTRACT = load_schema_contract("cloud")


CONNECTED_CAPABILITIES = frozenset(
    {
        "strict.handshake",
        "identity.account",
        "organization.workspace",
        "organization.members",
        "organization.structure",
        "authorization.current",
        "session.runtime",
        "organization_ai.configuration",
        "organization_ai.runtime_secret",
        "workbench.chat",
    }
)

BUSINESS_CAPABILITIES = frozenset(
    {
        "tasks.inbox",
        "tasks.list",
        "tasks.calendar",
        "organization.plans",
        "event_lines.workspace",
        "weekly_review.workspace",
        "tasks.create",
        "tasks.ai_decompose",
        "workbench.projects",
        "workbench.chat",
        "workbench.editor",
        "workbench.files",
        "workbench.favorites",
        "workbench.import_tools",
        "strategy.workspace",
        "intelligence.workspace",
        "growth.workspace",
        "feishu.integration",
        "audio.transcription",
        "documents.deep_read",
        "object_storage.files",
        "data_center.runtime",
        "feedback.submit",
        "software.update",
    }
)


def capability_registry(*, cloud_connected: bool) -> list[dict[str, str]]:
    connected_state = "connected" if cloud_connected else "blocked"
    connected_reason = "" if cloud_connected else "尚未连接严格新版组织云"
    rows = [
        {
            "id": capability,
            "state": connected_state,
            "reason": connected_reason,
        }
        for capability in sorted(CONNECTED_CAPABILITIES)
    ]
    rows.extend(
        {
            "id": capability,
            "state": "not_connected",
            "reason": "该功能尚未接入严格新版数据合同",
        }
        for capability in sorted(BUSINESS_CAPABILITIES - CONNECTED_CAPABILITIES)
    )
    return rows
