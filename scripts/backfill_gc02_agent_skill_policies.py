#!/usr/bin/env python3
"""Attach existing Agent Skills to explicit GC-02 policy/grant authority.

This repair is DML-only and idempotent.  It preserves the existing Skill
definition and sharing decision, replaces legacy principal lists with stable
organization membership IDs, and never creates a table.
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

from cloud_backend.app.repositories.agent_skills import _install_skill_policy
from cloud_backend.app.repository import SessionIdentity
from scripts.activate_gc01_contract import _stable_id
from strict_common.ids import canonical_json, sha256_text, utc_now


BACKFILL_ID = "gc02-agent-skill-policy-backfill-v1"


def _json(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def backfill(database: Path, *, rollback_ref: str | None = None) -> dict[str, Any]:
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
            raise RuntimeError("Agent Skill policy backfill requires the exact 88-table database")
        schema = connection.execute(
            "SELECT * FROM schema_versions WHERE status='active' "
            "ORDER BY activated_at DESC LIMIT 1"
        ).fetchone()
        if schema is None or str(schema["authority_role"]) != "cloud":
            raise RuntimeError("Agent Skill policies can only be backfilled on cloud authority")
        now = utc_now()
        policies_created = 0
        grants_bound = 0
        actions_normalized = 0
        connection.execute("BEGIN IMMEDIATE")
        skills = connection.execute(
            "SELECT * FROM automation_rules WHERE record_kind='agent_skill' "
            "AND lifecycle_state='active' ORDER BY id"
        ).fetchall()
        for skill in skills:
            action = _json(skill["action_spec"])
            publisher_principal_id = str(action.get("publisherPrincipalId") or "")
            membership = connection.execute(
                "SELECT membership.*,scope.organization_id FROM organization_memberships AS membership "
                "JOIN authorization_scopes AS scope ON scope.id=membership.scope_id "
                "WHERE membership.scope_id=? AND membership.principal_id=? "
                "AND membership.record_kind='membership' AND membership.status='active' "
                "AND membership.lifecycle_state='active' LIMIT 1",
                (skill["scope_id"], publisher_principal_id),
            ).fetchone()
            if membership is None:
                raise RuntimeError(f"Skill {skill['id']} publisher membership is missing")
            legacy_principals = {
                str(item) for item in action.get("granteePrincipalIds") or [] if str(item)
            }
            membership_ids = {
                str(item) for item in action.get("granteeMembershipIds") or [] if str(item)
            }
            if legacy_principals:
                placeholders = ",".join("?" for _ in legacy_principals)
                rows = connection.execute(
                    f"SELECT id,principal_id FROM organization_memberships WHERE scope_id=? "
                    f"AND principal_id IN ({placeholders}) AND record_kind='membership' "
                    "AND status='active' AND lifecycle_state='active'",
                    (skill["scope_id"], *sorted(legacy_principals)),
                ).fetchall()
                if {str(row["principal_id"]) for row in rows} != legacy_principals:
                    raise RuntimeError(f"Skill {skill['id']} has unmapped legacy principals")
                membership_ids.update(str(row["id"]) for row in rows)
            visibility = str(action.get("visibility") or "private")
            department_id = str(action.get("departmentId") or "").strip() or None
            if visibility == "department" and not department_id:
                departments = connection.execute(
                    "SELECT department_id FROM organization_memberships WHERE scope_id=? "
                    "AND parent_membership_id=? AND record_kind='department_assignment' "
                    "AND role_key='department_lead' AND status='active' "
                    "AND lifecycle_state='active'",
                    (skill["scope_id"], membership["id"]),
                ).fetchall()
                if len(departments) != 1:
                    raise RuntimeError(f"Skill {skill['id']} department scope is ambiguous")
                department_id = str(departments[0]["department_id"])
            normalized_action = {
                **action,
                "departmentId": department_id if visibility == "department" else None,
                "granteeMembershipIds": sorted(membership_ids) if visibility == "selected_members" else [],
                "granteePrincipalIds": [],
                "publisherMembershipId": str(membership["id"]),
            }
            draft = {
                "visibility": visibility,
                "departmentId": normalized_action["departmentId"],
                "granteeMembershipIds": normalized_action["granteeMembershipIds"],
            }
            before_policy = connection.execute(
                "SELECT COUNT(*) FROM policy_versions WHERE scope_id=? "
                "AND secured_resource_id=? AND lifecycle_state='active'",
                (skill["scope_id"], skill["id"]),
            ).fetchone()[0]
            before_bound = connection.execute(
                "SELECT COUNT(*) FROM object_grants WHERE scope_id=? "
                "AND secured_resource_id=? AND status='active' "
                "AND lifecycle_state='active' AND policy_version_id IS NOT NULL",
                (skill["scope_id"], skill["id"]),
            ).fetchone()[0]
            identity = SessionIdentity(
                session_id="gc02-agent-skill-backfill",
                principal_id=publisher_principal_id,
                membership_id=str(membership["id"]),
                organization_id=str(membership["organization_id"]),
                cloud_instance_id=str(schema["origin_instance_id"] or ""),
                scope_id=str(skill["scope_id"]),
                system_role=str(membership["role_key"] or "member"),
                visibility_scope=str(membership["visibility_scope"] or "self"),
                display_name="",
            )
            _install_skill_policy(
                connection,
                identity,
                skill_id=str(skill["id"]),
                draft=draft,
                now=now,
            )
            connection.execute(
                "UPDATE automation_rules SET action_spec=?,updated_at=? WHERE id=?",
                (canonical_json(normalized_action), now, skill["id"]),
            )
            actions_normalized += int(canonical_json(action) != canonical_json(normalized_action))
            after_policy = connection.execute(
                "SELECT COUNT(*) FROM policy_versions WHERE scope_id=? "
                "AND secured_resource_id=? AND lifecycle_state='active'",
                (skill["scope_id"], skill["id"]),
            ).fetchone()[0]
            after_bound = connection.execute(
                "SELECT COUNT(*) FROM object_grants WHERE scope_id=? "
                "AND secured_resource_id=? AND status='active' "
                "AND lifecycle_state='active' AND policy_version_id IS NOT NULL",
                (skill["scope_id"], skill["id"]),
            ).fetchone()[0]
            policies_created += max(0, int(after_policy) - int(before_policy))
            grants_bound += max(0, int(after_bound) - int(before_bound))

        checksum = sha256_text(BACKFILL_ID)
        migration_id = _stable_id("migration", str(schema["id"]), BACKFILL_ID)
        if skills and connection.execute(
            "SELECT id FROM migration_ledger WHERE id=?", (migration_id,)
        ).fetchone() is None:
            connection.execute(
                "INSERT INTO migration_ledger (id,schema_version_id,step,checksum,status,"
                "from_version,to_version,code_hash,started_at,completed_at,rollback_ref,"
                "origin_instance_id,created_at,integrity_hash,authority_role) "
                "VALUES (?,?,?,?,'applied',?,?,?,?,?,?,?,?,?,'build')",
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
                    sha256_text(f"{migration_id}|{len(skills)}|{now}"),
                ),
            )
        connection.execute("COMMIT")
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        missing = int(
            connection.execute(
                "SELECT COUNT(*) FROM automation_rules AS skill WHERE skill.record_kind='agent_skill' "
                "AND skill.lifecycle_state='active' AND NOT EXISTS (SELECT 1 FROM policy_versions AS policy "
                "WHERE policy.secured_resource_id=skill.id AND policy.scope_id=skill.scope_id "
                "AND policy.lifecycle_state='active')"
            ).fetchone()[0]
        )
        unbound = int(
            connection.execute(
                "SELECT COUNT(*) FROM object_grants AS grant_row JOIN automation_rules AS skill "
                "ON skill.id=grant_row.secured_resource_id WHERE skill.record_kind='agent_skill' "
                "AND grant_row.status='active' AND grant_row.lifecycle_state='active' "
                "AND grant_row.policy_version_id IS NULL"
            ).fetchone()[0]
        )
        if quick != "ok" or foreign_key_errors or missing or unbound:
            raise RuntimeError("Agent Skill policy backfill post-check failed")
        return {
            "tables": table_count,
            "skillsScanned": len(skills),
            "policiesCreated": policies_created,
            "grantsBound": grants_bound,
            "actionsNormalized": actions_normalized,
            "skillsMissingPolicy": missing,
            "activeGrantsWithoutPolicy": unbound,
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
    print(json.dumps(backfill(args.database.resolve(), rollback_ref=args.rollback_ref), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
