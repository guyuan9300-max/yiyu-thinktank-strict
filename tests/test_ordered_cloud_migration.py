from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from strict_common.contracts import CLOUD_CONTRACT

migrations = pytest.importorskip(
    "strict_common.migrations",
    reason="pre-88-table ordered migrations are frozen under legacy_frozen/v4",
)
CLOUD_V2_TO_V3 = migrations.CLOUD_V2_TO_V3
CLOUD_V3_TO_V4 = migrations.CLOUD_V3_TO_V4
from strict_common.schema import initialize_database, migrate_database


def _downgrade_fixture_to_v3_layout(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in (
            "organization_membership_applications",
            "organization_role_process_templates",
            "organization_task_control_rules",
            "organization_reporting_lines",
        ):
            connection.execute(f'DROP TABLE "{table}"')
        for table, columns in (
            (
                "organization_records",
                (
                    "leader_name_override",
                    "leader_membership_id",
                    "quarterly_focus_json",
                    "annual_strategy",
                    "annual_strategy_year",
                    "annual_goal",
                ),
            ),
            (
                "organization_memberships",
                (
                    "can_change_deadline",
                    "can_reassign_tasks",
                    "can_approve_tasks",
                    "task_edit_scope",
                    "current_focus",
                    "project_role_labels_json",
                ),
            ),
            (
                "organization_departments",
                (
                    "collaboration_department_ids_json",
                    "quarterly_focus_json",
                    "team_context",
                    "business_context",
                    "mission",
                    "leader_name_override",
                    "parent_department_id",
                    "color",
                ),
            ),
            (
                "management_titles",
                (
                    "sort_order",
                    "can_change_deadline",
                    "can_reassign_tasks",
                    "can_approve_tasks",
                    "task_edit_scope",
                    "collaboration_title_ids_json",
                    "should_avoid_json",
                    "responsibilities_json",
                    "goal",
                    "is_manager",
                    "manager_title_id",
                    "visibility_scope",
                    "level",
                    "department_id",
                ),
            ),
        ):
            for column in columns:
                connection.execute(
                    f'ALTER TABLE "{table}" DROP COLUMN "{column}"'
                )
        connection.execute(
            """
            UPDATE meta_schema_builds
            SET manifest_hash = ?, contract_version = '3',
                migration_set_hash = 'fixture-v3-ddl'
            WHERE status = 'active'
            """,
            (CLOUD_V3_TO_V4.from_manifest_hash,),
        )
        connection.execute(
            """
            UPDATE meta_migration_steps
            SET to_manifest_hash = ?, step_hash = 'fixture-v3-ddl'
            WHERE from_manifest_hash IS NULL
            """,
            (CLOUD_V3_TO_V4.from_manifest_hash,),
        )
        connection.execute("PRAGMA user_version = 3")
        connection.commit()


def _downgrade_fixture_to_v2_layout(database: Path) -> None:
    _downgrade_fixture_to_v3_layout(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in (
            "bot_task_plans",
            "organization_bot_profiles",
            "scoped_configuration_records",
        ):
            connection.execute(f'DROP TABLE "{table}"')
        connection.execute(
            "ALTER TABLE identity_principals DROP COLUMN principal_kind"
        )
        connection.execute(
            """
            UPDATE meta_schema_builds
            SET manifest_hash = ?, contract_version = '2',
                migration_set_hash = 'fixture-v2-ddl'
            WHERE status = 'active'
            """,
            (CLOUD_V2_TO_V3.from_manifest_hash,),
        )
        connection.execute(
            """
            UPDATE meta_migration_steps
            SET to_manifest_hash = ?, step_hash = 'fixture-v2-ddl'
            WHERE from_manifest_hash IS NULL
            """,
            (CLOUD_V2_TO_V3.from_manifest_hash,),
        )
        connection.execute("PRAGMA user_version = 2")
        connection.commit()


def test_registered_cloud_v2_to_v4_migration_chain_is_ordered_and_exact(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cloud-v2.db"
    initialize_database(database, "cloud")
    _downgrade_fixture_to_v2_layout(database)

    identity = migrate_database(
        database,
        "cloud",
        expected_from_manifest_hash=CLOUD_V2_TO_V3.from_manifest_hash,
    )

    assert identity.manifest_hash == CLOUD_CONTRACT.manifest_hash
    assert identity.contract_version == "4"
    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        assert tables == set(CLOUD_CONTRACT.allowed_tables)
        principal_columns = {
            str(row[1])
            for row in connection.execute(
                'PRAGMA table_info("identity_principals")'
            )
        }
        assert "principal_kind" in principal_columns
        step = connection.execute(
            """
            SELECT status
            FROM meta_migration_steps
            WHERE from_manifest_hash = ? AND to_manifest_hash = ?
            """,
            (
                CLOUD_V2_TO_V3.from_manifest_hash,
                CLOUD_V2_TO_V3.to_manifest_hash,
            ),
        ).fetchone()
        assert step == ("applied",)
        v4_step = connection.execute(
            """
            SELECT status
            FROM meta_migration_steps
            WHERE from_manifest_hash = ? AND to_manifest_hash = ?
            """,
            (
                CLOUD_V3_TO_V4.from_manifest_hash,
                CLOUD_V3_TO_V4.to_manifest_hash,
            ),
        ).fetchone()
        assert v4_step == ("applied",)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_registered_cloud_v3_to_v4_migration_is_exact(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cloud-v3.db"
    fresh_v4 = tmp_path / "cloud-fresh-v4.db"
    initialize_database(database, "cloud")
    initialize_database(fresh_v4, "cloud")
    _downgrade_fixture_to_v3_layout(database)

    identity = migrate_database(
        database,
        "cloud",
        expected_from_manifest_hash=CLOUD_V3_TO_V4.from_manifest_hash,
    )

    assert identity.manifest_hash == CLOUD_CONTRACT.manifest_hash
    assert identity.contract_version == "4"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert {
            str(row[1])
            for row in connection.execute(
                'PRAGMA table_info("organization_records")'
            )
        }.issuperset(
            {
                "annual_goal",
                "annual_strategy",
                "quarterly_focus_json",
                "leader_membership_id",
                "leader_name_override",
            }
        )
    changed_tables = (
        "organization_records",
        "organization_memberships",
        "organization_departments",
        "management_titles",
        "organization_reporting_lines",
        "organization_task_control_rules",
        "organization_role_process_templates",
        "organization_membership_applications",
    )
    with sqlite3.connect(database) as migrated, sqlite3.connect(
        fresh_v4
    ) as fresh:
        for table in changed_tables:
            migrated_columns = {
                str(row[1]): tuple(row[2:])
                for row in migrated.execute(
                    f'PRAGMA table_info("{table}")'
                ).fetchall()
            }
            fresh_columns = {
                str(row[1]): tuple(row[2:])
                for row in fresh.execute(
                    f'PRAGMA table_info("{table}")'
                ).fetchall()
            }
            assert migrated_columns == fresh_columns
            assert migrated.execute(
                f'PRAGMA foreign_key_list("{table}")'
            ).fetchall() == fresh.execute(
                f'PRAGMA foreign_key_list("{table}")'
            ).fetchall()
            assert migrated.execute(
                f'PRAGMA index_list("{table}")'
            ).fetchall() == fresh.execute(
                f'PRAGMA index_list("{table}")'
            ).fetchall()


def test_migration_rejects_unregistered_source_manifest(tmp_path: Path) -> None:
    database = tmp_path / "cloud-v2.db"
    initialize_database(database, "cloud")
    _downgrade_fixture_to_v2_layout(database)

    try:
        migrate_database(
            database,
            "cloud",
            expected_from_manifest_hash="not-the-installed-v2-manifest",
        )
    except RuntimeError as error:
        assert "no ordered strict migration registered" in str(error)
    else:
        raise AssertionError("unregistered migration source was accepted")


def test_migration_rejects_spoofed_source_contract_version(
    tmp_path: Path,
) -> None:
    database = tmp_path / "cloud-spoofed-v3.db"
    initialize_database(database, "cloud")
    _downgrade_fixture_to_v3_layout(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE meta_schema_builds
            SET contract_version = '2'
            WHERE status = 'active'
            """
        )
        connection.commit()

    try:
        migrate_database(
            database,
            "cloud",
            expected_from_manifest_hash=CLOUD_V3_TO_V4.from_manifest_hash,
        )
    except RuntimeError as error:
        assert "source identity mismatch" in str(error)
        assert "contract_version" in str(error)
    else:
        raise AssertionError("spoofed source contract version was accepted")


def test_multi_step_migration_rolls_back_every_step_on_late_ddl_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "cloud-v2-late-failure.db"
    initialize_database(database, "cloud")
    _downgrade_fixture_to_v2_layout(database)
    broken_sql = tmp_path / "broken-v3-to-v4.sql"
    broken_sql.write_text(
        """
        CREATE TABLE migration_must_rollback (id TEXT PRIMARY KEY) STRICT;
        SELECT * FROM table_that_does_not_exist;
        """,
        encoding="utf-8",
    )
    broken_v4 = replace(CLOUD_V3_TO_V4, sql_path=broken_sql)
    monkeypatch.setattr(
        "strict_common.schema.migration_path",
        lambda *_: (CLOUD_V2_TO_V3, broken_v4),
    )

    with pytest.raises(sqlite3.OperationalError):
        migrate_database(
            database,
            "cloud",
            expected_from_manifest_hash=CLOUD_V2_TO_V3.from_manifest_hash,
        )

    with sqlite3.connect(database) as connection:
        active = connection.execute(
            """
            SELECT manifest_hash, contract_version
            FROM meta_schema_builds
            WHERE status = 'active'
            """
        ).fetchall()
        tables = {
            str(row[0])
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            )
        }
        migrated_steps = connection.execute(
            """
            SELECT COUNT(*)
            FROM meta_migration_steps
            WHERE from_manifest_hash IS NOT NULL
            """
        ).fetchone()[0]
    assert active == [(CLOUD_V2_TO_V3.from_manifest_hash, "2")]
    assert "scoped_configuration_records" not in tables
    assert "migration_must_rollback" not in tables
    assert migrated_steps == 0
