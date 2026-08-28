from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.migrate_agent_skill_foundation_v8 import frozen_v8_manifest, migrate
from strict_common.agent_memory import (
    BUILTIN_AGENT_DEFINITIONS,
    builtin_agent_id,
    canonical_organization_scope_id,
)
from strict_common.contracts import CLOUD_CONTRACT, LOCAL_CONTRACT
from strict_common.physical_schema import ddl_from_manifest, user_tables


def _v7_manifest(v8: dict) -> dict:
    raw = copy.deepcopy(v8)
    raw["contractVersion"] = "7"
    raw.pop("commonRules", {}).pop("agentSkillFoundation", None)
    rules = next(
        table for table in raw["allowedTables"]
        if table["name"] == "automation_rules"
    )
    check = next(
        row for row in rules["check_constraints"]
        if row["name"] == "ck_record_kind_domain"
    )
    check["expression"] = (
        "record_kind IN ('template','automation','task_control',"
        "'process_template','source_trust_rule')"
    )
    rules["command_invariants"] = [
        item for item in rules.get("command_invariants", [])
        if "agent_skill" not in item
    ]
    return raw


def _create_v7_source(path: Path, role: str, *, with_organization: bool) -> None:
    contract = LOCAL_CONTRACT if role == "local" else CLOUD_CONTRACT
    raw = _v7_manifest(frozen_v8_manifest(role))  # type: ignore[arg-type]
    manifest_hash = hashlib.sha256(
        json.dumps(
            raw,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    connection = sqlite3.connect(path)
    connection.executescript(ddl_from_manifest(raw))
    connection.execute(
        """
        INSERT INTO schema_versions (
            id, engine, version, checksum, status, database_role,
            schema_family, manifest_hash, migration_set_hash, build_id,
            created_at, activated_at, authority_role, origin_instance_id,
            database_generation_id
        ) VALUES ('schema-v7', 'sqlite', 7, 'ddl-v7', 'active', ?, ?, ?,
                  'ddl-v7', 'schema-v7', '2026-08-05T00:00:00Z',
                  '2026-08-05T00:00:00Z', ?, 'generation-v7', 'generation-v7')
        """,
        (contract.database_role, contract.schema_family, manifest_hash, role),
    )
    if with_organization:
        organization_id = "org-foundation-test"
        scope_id = canonical_organization_scope_id(organization_id)
        connection.execute(
            """
            INSERT INTO organizations (
                id, lifecycle_state, version, updated_at, record_kind, name,
                created_at, deleted_at
            ) VALUES (?, 'active', 1, '2026-08-05T00:00:00Z',
                      'organization', '迁移测试组织', '2026-08-05T00:00:00Z', NULL)
            """,
            (organization_id,),
        )
        connection.execute(
            """
            INSERT INTO authorization_scopes (
                id, scope_kind, organization_id, policy_version, created_at,
                updated_at, status, version, lifecycle_state, deleted_at
            ) VALUES (?, 'organization', ?, 1, '2026-08-05T00:00:00Z',
                      '2026-08-05T00:00:00Z', 'active', 1, 'active', NULL)
            """,
            (scope_id, organization_id),
        )
    connection.execute("PRAGMA user_version=7")
    connection.commit()
    connection.close()


@pytest.mark.parametrize("role", ["local", "cloud"])
def test_offline_v7_to_v8_preserves_88_tables_and_only_extends_skill_enum(
    tmp_path: Path,
    role: str,
) -> None:
    source = tmp_path / f"{role}-v7.db"
    target = tmp_path / f"{role}-v8.db"
    _create_v7_source(source, role, with_organization=role == "cloud")

    report = migrate(source, target, role)  # type: ignore[arg-type]

    assert report["sourceVersion"] == "7"
    assert report["targetVersion"] == "8"
    assert report["tableCount"] == 88
    assert report["quickCheck"] == "ok"
    assert report["foreignKeyViolationCount"] == 0
    assert report["databaseGenerationIdPreserved"] is True
    assert report["updatedBuiltinAgentCount"] == (6 if role == "cloud" else 0)
    connection = sqlite3.connect(target)
    assert len(user_tables(connection)) == 88
    automation_rule_sql = str(
        connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='automation_rules'"
        ).fetchone()[0]
    )
    assert "'agent_skill'" in automation_rule_sql
    if role == "cloud":
        assert connection.execute(
            "SELECT COUNT(*) FROM organizations WHERE id='org-foundation-test'"
        ).fetchone()[0] == 1
    connection.close()


def test_cloud_v8_migration_seeds_stable_organization_agent_identities(
    tmp_path: Path,
) -> None:
    source = tmp_path / "cloud-v7.db"
    target = tmp_path / "cloud-v8.db"
    _create_v7_source(source, "cloud", with_organization=True)
    migrate(source, target, "cloud")

    connection = sqlite3.connect(target)
    rows = connection.execute(
        """
        SELECT id, scope_id, agent_kind, handle, owner_principal_id,
               owner_membership_id, enabled
        FROM bot_definitions WHERE agent_kind IS NOT NULL ORDER BY agent_kind
        """
    ).fetchall()
    assert len(rows) == len(BUILTIN_AGENT_DEFINITIONS) == 6
    by_kind = {
        definition.agent_kind: definition
        for definition in BUILTIN_AGENT_DEFINITIONS
    }
    for (
        bot_id,
        scope_id,
        agent_kind,
        handle,
        owner_principal,
        owner_membership,
        enabled,
    ) in rows:
        assert bot_id == builtin_agent_id("org-foundation-test", agent_kind)
        assert scope_id == canonical_organization_scope_id("org-foundation-test")
        assert handle == by_kind[agent_kind].handle
        assert owner_principal is None
        assert owner_membership is None
        assert enabled == 1
    connection.close()
