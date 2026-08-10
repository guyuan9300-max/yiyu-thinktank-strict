#!/usr/bin/env python3
"""Register GC-03..GC-15 control/query contracts without changing the 88-table schema."""

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

from scripts.activate_gc01_contract import _seed_registry, _stable_id
from strict_common.ids import canonical_json, sha256_text, utc_now
from strict_common.physical_schema import ddl_from_manifest, normalized_structure, structure_sha256


CHAINS: dict[str, dict[str, Any]] = {
    "GC-03": {"surface": "event_line_workspace", "objects": ["clients", "event_lines", "tasks", "meetings", "narrative_outputs", "artifact_versions"]},
    "GC-04": {"surface": "task_and_schedule", "objects": ["tasks", "task_collaborators", "task_lists", "task_views", "calendar_entries", "notification_deliveries"]},
    "GC-05": {"surface": "task_bulk_toolbar", "objects": ["tasks", "commands", "idempotency_records", "operation_attempts", "outbox_events", "audit_events"]},
    "GC-06": {"surface": "organization_planning", "objects": ["organizations", "organization_memberships", "planning_cycles", "weekly_reviews", "decision_actions", "tasks", "calendar_entries"]},
    "GC-07": {"surface": "project_materials", "objects": ["source_assets", "storage_objects", "object_manifests", "processing_attempts", "knowledge_documents", "document_versions", "content_chunks"]},
    "GC-08": {"surface": "customer_meeting", "objects": ["meetings", "source_assets", "storage_objects", "transcription_versions", "knowledge_documents", "document_versions", "ai_proposals"]},
    "GC-09": {"surface": "report_workspace", "objects": ["narrative_outputs", "artifact_versions", "object_manifests", "export_grants", "derivation_lineage"]},
    "GC-10": {"surface": "project_knowledge", "objects": ["knowledge_documents", "document_versions", "content_chunks", "atomic_facts", "evidence_links", "relationship_triples", "search_index_manifests", "vector_index_manifests"]},
    "GC-11": {"surface": "agent_skill_picker", "objects": ["automation_rules", "bot_definitions", "execution_runs", "ai_proposals", "ai_approvals", "provider_resources"]},
    "GC-12": {"surface": "intelligence_and_fact_governance", "objects": ["intelligence_records", "intelligence_revisions", "source_assets", "atomic_facts", "evidence_links", "relationship_triples", "ai_proposals", "ai_approvals"]},
    "GC-13": {"surface": "growth_center", "objects": ["growth_evidence", "growth_read_models", "tasks", "meetings", "weekly_reviews", "ai_proposals", "ai_approvals"]},
    "GC-14": {"surface": "workbench_ai", "objects": ["source_sets", "source_set_members", "derivation_lineage", "ai_context_manifests", "ai_answers", "cache_entries", "ai_proposals", "ai_approvals"]},
    "GC-15": {"surface": "lifecycle_and_recovery", "objects": ["secured_resources", "lifecycle_events", "purge_ledger", "derivation_lineage", "search_index_manifests", "vector_index_manifests", "cache_entries", "reconciliation_runs", "export_grants"]},
}

LOCAL_COMMAND_SUPPORT = ["commands", "idempotency_records", "operation_attempts", "outbox_events", "audit_events", "reconciliation_runs"]
CLOUD_COMMAND_SUPPORT = ["commands", "idempotency_records", "operation_attempts", "outbox_events", "audit_events"]


def _registry(chain_id: str, spec: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    objects = [item for item in spec["objects"] if item in allowed]
    cloud_objects = [item for item in objects if item != "storage_objects"]
    local_support = [item for item in LOCAL_COMMAND_SUPPORT if item in allowed]
    cloud_support = [item for item in CLOUD_COMMAND_SUPPORT if item in allowed]
    slug = chain_id.lower().replace("-", "")
    return {
        "formatVersion": 1,
        "registryId": f"yiyu.{slug}.golden-chain.v1",
        "goldenChainId": chain_id,
        "definitionVersion": 1,
        "stateContract": "strict-six-state-v1",
        "status": "active",
        "completionState": "implementation_partial",
        "evidenceRef": "contracts/STRICT_BUSINESS_DATA_CONTRACT_V2.md",
        "controls": [
            {
                "controlId": f"{slug}.control.primary.query",
                "surface": spec["surface"],
                "intentKind": "query",
                "operationId": f"{slug}.query.primary",
                "localRead": objects,
                "localWrite": [],
                "cloudRead": cloud_objects,
                "cloudWrite": [],
            },
            {
                "controlId": f"{slug}.control.primary.command",
                "surface": spec["surface"],
                "intentKind": "command",
                "operationId": f"{slug}.command.primary",
                "localRead": list(dict.fromkeys([*objects, *local_support])),
                "localWrite": list(dict.fromkeys([*objects, *local_support])),
                "cloudRead": list(dict.fromkeys([*cloud_objects, *cloud_support])),
                "cloudWrite": list(dict.fromkeys([*cloud_objects, *cloud_support])),
            },
        ],
        "queries": [
            {
                "queryId": f"{slug}.query.primary",
                "authorityKind": "row_authority_with_local_projection",
                "projectionKind": f"{slug}_authorized_projection",
                "stalePolicy": "show_last_confirmed_with_sync_state_and_fail_closed_on_expired_grant",
                "localRead": objects,
                "cloudRead": cloud_objects,
            }
        ],
    }


def activate(database: Path, role: str, rollback_ref: str | None = None) -> dict[str, Any]:
    manifest_path = ROOT / "contracts" / f"strict-{role}-schema-manifest.v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_hash = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    frozen_hash = (ROOT / "contracts" / f"strict-{role}-schema-manifest.v1.canonical.sha256").read_text(encoding="utf-8").split()[0]
    if manifest_hash != frozen_hash or len(manifest.get("allowedTables") or []) != 88:
        raise RuntimeError("frozen 88-table manifest identity mismatch")
    allowed = {str(item["name"]) for item in manifest["allowedTables"]}
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript(ddl_from_manifest(manifest))
        expected_hash = structure_sha256(normalized_structure(expected))
    finally:
        expected.close()

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        actual = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if actual != allowed or structure_sha256(normalized_structure(connection)) != expected_hash:
            raise RuntimeError("activity database is not the exact frozen 88-table structure")
        schema = connection.execute("SELECT * FROM schema_versions WHERE status='active' ORDER BY activated_at DESC LIMIT 1").fetchone()
        if schema is None or str(schema["manifest_hash"]) != manifest_hash:
            raise RuntimeError("active schema identity does not match manifest")
        connection.execute("BEGIN IMMEDIATE")
        controls = queries = 0
        for chain_id, spec in CHAINS.items():
            registry = _registry(chain_id, spec, allowed)
            registry_hash = sha256_text(canonical_json(registry))
            migration_id = _stable_id("migration", str(schema["id"]), registry["registryId"], registry_hash)
            existing = connection.execute("SELECT started_at FROM migration_ledger WHERE id=?", (migration_id,)).fetchone()
            activated_at = str(existing["started_at"]) if existing else utc_now()
            if existing is None:
                connection.execute(
                    "INSERT INTO migration_ledger (id,schema_version_id,step,checksum,status,from_version,to_version,code_hash,started_at,completed_at,rollback_ref,origin_instance_id,created_at,integrity_hash,authority_role) VALUES (?,?,? ,?,'applied',?,?,?, ?,?,?,?,?,?,'build')",
                    (migration_id, schema["id"], f"activate_{chain_id.lower().replace('-', '')}_registry_v1", registry_hash, str(schema["version"]), str(schema["version"]), registry_hash, activated_at, activated_at, rollback_ref, schema["origin_instance_id"], activated_at, sha256_text(f"{migration_id}|{registry_hash}|{activated_at}")),
                )
            added_controls, added_queries = _seed_registry(
                connection,
                registry=registry,
                manifest_hash=manifest_hash,
                schema_version_id=str(schema["id"]),
                activated_at=activated_at,
            )
            controls += added_controls
            queries += added_queries
        connection.execute("COMMIT")
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        fk = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if quick != "ok" or fk or structure_sha256(normalized_structure(connection)) != expected_hash:
            raise RuntimeError("registry activation post-check failed")
        return {"database": str(database), "role": role, "tables": 88, "controls": controls, "queries": queries, "quickCheck": quick, "foreignKeyErrors": fk, "structureHash": expected_hash}
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
    print(json.dumps(activate(args.database, args.role, args.rollback_ref), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
