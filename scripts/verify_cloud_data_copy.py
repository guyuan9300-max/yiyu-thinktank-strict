from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud_backend.app.repositories import workbench_outputs  # noqa: E402
from cloud_backend.app.repositories.project_materials import (  # noqa: E402
    ProjectMaterialsRepository,
)
from cloud_backend.app.repositories.workflow import WorkflowRepository  # noqa: E402
from cloud_backend.app.repository import (  # noqa: E402
    CloudRepository,
    SessionIdentity,
)
from strict_common.schema import database_identity, runtime_connection  # noqa: E402
from strict_common.security import SecretCipher  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ReadOnlyCloudRepository(CloudRepository):
    """Run production-shaped repository reads without permitting writes."""

    def __init__(self, database_path: Path, cloud_instance_id: str):
        self.database_path = database_path.resolve()
        self.identity = database_identity(self.database_path, "cloud")
        self.cipher = SecretCipher(Fernet.generate_key().decode())
        self.cloud_instance_id = cloud_instance_id

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with runtime_connection(
            self.database_path,
            "cloud",
            read_only=True,
        ) as connection:
            yield connection


def _database_facts(
    database_path: Path,
) -> tuple[str, str, list[SessionIdentity], dict[str, Any]]:
    with runtime_connection(database_path, "cloud", read_only=True) as connection:
        quick_check = [
            str(row[0])
            for row in connection.execute("PRAGMA quick_check").fetchall()
        ]
        foreign_key_violations = len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        )
        organizations = [
            str(row["organization_id"])
            for row in connection.execute(
                "SELECT organization_id FROM organization_records"
            ).fetchall()
        ]
        cloud_instances = [
            str(row["cloud_instance_id"])
            for row in connection.execute(
                """
                SELECT cloud_instance_id
                FROM identity_cloud_instances
                WHERE status = 'active'
                """
            ).fetchall()
        ]
        if len(organizations) != 1:
            raise RuntimeError(
                f"expected one organization, found {len(organizations)}"
            )
        if len(cloud_instances) != 1:
            raise RuntimeError(
                f"expected one active cloud instance, found {len(cloud_instances)}"
            )
        organization_id = organizations[0]
        cloud_instance_id = cloud_instances[0]
        identities = [
            SessionIdentity(
                session_id=f"offline-copy:{row['membership_id']}",
                principal_id=str(row["principal_id"]),
                membership_id=str(row["membership_id"]),
                organization_id=str(row["organization_id"]),
                cloud_instance_id=cloud_instance_id,
                scope_id=str(row["scope_id"]),
                system_role=str(row["system_role"]),
                visibility_scope=str(row["visibility_scope"]),
                display_name=str(row["display_name"]),
            )
            for row in connection.execute(
                """
                SELECT m.*, p.display_name
                FROM organization_memberships AS m
                JOIN identity_principals AS p
                  ON p.principal_id = m.principal_id
                WHERE m.organization_id = ?
                  AND m.status = 'active'
                  AND p.status = 'active'
                ORDER BY m.system_role, m.membership_id
                """,
                (organization_id,),
            ).fetchall()
        ]
        table_names = [
            str(row["name"])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_schema
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            ).fetchall()
        ]
        foreign_organization_rows: dict[str, int] = {}
        for table_name in table_names:
            columns = {
                str(row["name"])
                for row in connection.execute(
                    f'PRAGMA table_info("{table_name}")'
                ).fetchall()
            }
            if "organization_id" not in columns:
                continue
            count = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*) AS count
                    FROM "{table_name}"
                    WHERE organization_id != ?
                    """,
                    (organization_id,),
                ).fetchone()["count"]
            )
            if count:
                foreign_organization_rows[table_name] = count
        facts = {
            "quickCheck": quick_check,
            "foreignKeyViolations": foreign_key_violations,
            "tableCount": len(table_names),
            "activeMembershipCount": len(identities),
            "foreignOrganizationRows": foreign_organization_rows,
        }
        return organization_id, cloud_instance_id, identities, facts


def verify(database_path: Path, label: str) -> dict[str, Any]:
    database_path = database_path.resolve()
    before_hash = _sha256(database_path)
    identity = database_identity(database_path, "cloud")
    (
        organization_id,
        cloud_instance_id,
        identities,
        database_facts,
    ) = _database_facts(database_path)
    repository = ReadOnlyCloudRepository(database_path, cloud_instance_id)
    project_materials = ProjectMaterialsRepository(repository)
    workflow = WorkflowRepository(repository)
    member_results: list[dict[str, Any]] = []

    for member in identities:
        snapshot = repository.business_snapshot(member)
        projects = list(snapshot.get("projects") or [])
        tasks = list(snapshot.get("tasks") or [])
        project_surface_count = 0
        for project in projects:
            project_id = str(project.get("projectId") or "")
            if not project_id:
                raise RuntimeError("visible project omitted projectId")
            surfaces = (
                repository.project_knowledge_context(
                    member,
                    project_id=project_id,
                ),
                project_materials.project_detail(
                    member,
                    project_id=project_id,
                ),
                project_materials.knowledge_status(
                    member,
                    project_id=project_id,
                ),
                project_materials.fact_bundle(
                    member,
                    project_id=project_id,
                    lite=True,
                ),
                workbench_outputs.project_workspace(
                    repository,
                    member,
                    project_id=project_id,
                ),
                workbench_outputs.knowledge_status(
                    repository,
                    member,
                    project_id=project_id,
                ),
                workbench_outputs.analysis_status(
                    repository,
                    member,
                    project_id=project_id,
                ),
            )
            if len(surfaces) != 7:
                raise AssertionError("project surface denominator changed")
            project_surface_count += len(surfaces)

        task_context_count = 0
        for task in tasks:
            task_id = str(task.get("taskId") or "")
            if not task_id:
                raise RuntimeError("visible task omitted taskId")
            context = workflow.task_context(member, task_id)
            if str((context.get("task") or {}).get("taskId") or "") != task_id:
                raise RuntimeError(f"task context mismatch for {task_id}")
            task_context_count += 1

        member_results.append(
            {
                "systemRole": member.system_role,
                "visibilityScope": member.visibility_scope,
                "visibleProjects": len(projects),
                "visibleTasks": len(tasks),
                "projectSurfaceReads": project_surface_count,
                "taskContextReads": task_context_count,
            }
        )

    after_hash = _sha256(database_path)
    if after_hash != before_hash:
        raise RuntimeError("read-only verifier changed the database copy")
    if database_facts["quickCheck"] != ["ok"]:
        raise RuntimeError("database quick_check failed")
    if database_facts["foreignKeyViolations"]:
        raise RuntimeError("database foreign_key_check failed")
    if database_facts["foreignOrganizationRows"]:
        raise RuntimeError("database contains rows from another organization")

    return {
        "label": label,
        "databaseSha256": before_hash,
        "schema": {
            "family": identity.schema_family,
            "contractVersion": identity.contract_version,
            "manifestHash": identity.manifest_hash,
            "migrationSetHash": identity.migration_set_hash,
        },
        "organizationId": organization_id,
        "cloudInstanceId": cloud_instance_id,
        **database_facts,
        "members": member_results,
        "totals": {
            "projectSurfaceReads": sum(
                item["projectSurfaceReads"] for item in member_results
            ),
            "taskContextReads": sum(
                item["taskContextReads"] for item in member_results
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.database, args.label)
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
