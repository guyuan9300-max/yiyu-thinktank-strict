from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


MigrationRole = Literal["local", "cloud"]
MIGRATIONS_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class MigrationSpec:
    role: MigrationRole
    from_manifest_hash: str
    to_manifest_hash: str
    source_contract_version: str
    sql_path: Path
    added_tables: frozenset[str]
    required_source_columns: dict[str, frozenset[str]] = field(
        default_factory=dict
    )
    forbidden_source_columns: dict[str, frozenset[str]] = field(
        default_factory=dict
    )


CLOUD_V2_TO_V3 = MigrationSpec(
    role="cloud",
    from_manifest_hash=(
        "6cbe155474234f90ca59307b46efa078fc39c3dedea4ff65eca8242248d665b0"
    ),
    to_manifest_hash=(
        "0dd63b8635cab79e0f908d3243c44f4668d319c2c29d1359bf602478394b2bca"
    ),
    source_contract_version="2",
    sql_path=(
        MIGRATIONS_ROOT
        / "cloud"
        / (
            "6cbe155474234f90ca59307b46efa078fc39c3dedea4ff65eca8242248d665b0"
            "__0dd63b8635cab79e0f908d3243c44f4668d319c2c29d1359bf602478394b2bca.sql"
        )
    ),
    added_tables=frozenset(
        {
            "scoped_configuration_records",
            "organization_bot_profiles",
            "bot_task_plans",
        }
    ),
    forbidden_source_columns={
        "identity_principals": frozenset({"principal_kind"}),
    },
)

CLOUD_V3_TO_V4 = MigrationSpec(
    role="cloud",
    from_manifest_hash=(
        "0dd63b8635cab79e0f908d3243c44f4668d319c2c29d1359bf602478394b2bca"
    ),
    to_manifest_hash=(
        "61fdbf96dcdf35848e5f59bf5e7a612fdbd3762503d037a9fba9eee817f8c115"
    ),
    source_contract_version="3",
    sql_path=(
        MIGRATIONS_ROOT
        / "cloud"
        / (
            "0dd63b8635cab79e0f908d3243c44f4668d319c2c29d1359bf602478394b2bca"
            "__61fdbf96dcdf35848e5f59bf5e7a612fdbd3762503d037a9fba9eee817f8c115.sql"
        )
    ),
    added_tables=frozenset(
        {
            "organization_reporting_lines",
            "organization_task_control_rules",
            "organization_role_process_templates",
            "organization_membership_applications",
        }
    ),
    required_source_columns={
        "identity_principals": frozenset({"principal_kind"}),
    },
    forbidden_source_columns={
        "organization_records": frozenset(
            {
                "annual_goal",
                "annual_strategy_year",
                "annual_strategy",
                "quarterly_focus_json",
                "leader_membership_id",
                "leader_name_override",
            }
        ),
        "organization_memberships": frozenset(
            {
                "project_role_labels_json",
                "current_focus",
                "task_edit_scope",
                "can_approve_tasks",
                "can_reassign_tasks",
                "can_change_deadline",
            }
        ),
        "organization_departments": frozenset(
            {
                "color",
                "parent_department_id",
                "leader_name_override",
                "mission",
                "business_context",
                "team_context",
                "quarterly_focus_json",
                "collaboration_department_ids_json",
            }
        ),
        "management_titles": frozenset(
            {
                "department_id",
                "level",
                "visibility_scope",
                "manager_title_id",
                "is_manager",
                "goal",
                "responsibilities_json",
                "should_avoid_json",
                "collaboration_title_ids_json",
                "task_edit_scope",
                "can_approve_tasks",
                "can_reassign_tasks",
                "can_change_deadline",
                "sort_order",
            }
        ),
    },
)

ORDERED_MIGRATIONS = (CLOUD_V2_TO_V3, CLOUD_V3_TO_V4)


def find_migration(
    role: MigrationRole,
    from_manifest_hash: str,
    to_manifest_hash: str,
) -> MigrationSpec | None:
    return next(
        (
            migration
            for migration in ORDERED_MIGRATIONS
            if migration.role == role
            and migration.from_manifest_hash == from_manifest_hash
            and migration.to_manifest_hash == to_manifest_hash
        ),
        None,
    )


def migration_path(
    role: MigrationRole,
    from_manifest_hash: str,
    to_manifest_hash: str,
) -> tuple[MigrationSpec, ...]:
    if from_manifest_hash == to_manifest_hash:
        return ()
    path: list[MigrationSpec] = []
    visited = {from_manifest_hash}
    current = from_manifest_hash
    while current != to_manifest_hash:
        next_steps = [
            migration
            for migration in ORDERED_MIGRATIONS
            if migration.role == role
            and migration.from_manifest_hash == current
        ]
        if len(next_steps) != 1:
            return ()
        step = next_steps[0]
        if step.to_manifest_hash in visited:
            return ()
        path.append(step)
        current = step.to_manifest_hash
        visited.add(current)
    return tuple(path)
