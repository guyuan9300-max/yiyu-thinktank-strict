from pathlib import Path

from backend.app.runtime import WorkspaceRuntime
from cloud_backend.app.repositories.gc09_reports import (
    create_report,
    issue_export_grant,
    list_reports,
    report_versions,
    update_report,
)
from strict_common.physical_schema import user_tables
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository


def test_gc09_report_uses_only_strict_88_and_restores_version(tmp_path: Path) -> None:
    assert WorkspaceRuntime._connected_cloud_path_allowed(
        "POST",
        "/api/v2/workbench/reports/report_gc09/export-grants",
    )
    repository, identity, seed = _repository(tmp_path)
    report = create_report(
        repository,
        identity,
        payload={
            "reportId": "report_gc09",
            "projectId": seed["projectId"],
            "title": "日慈项目分析报告",
            "outputKind": "strategy_report",
            "contentMarkdown": "# 第一版\n\n依据资料形成。",
            "contentJson": {
                "sourceManifest": [
                    {
                        "sourceId": "local_doc_gc09",
                        "sourceType": "member_local_document",
                        "version": 1,
                        "contentHash": "a" * 64,
                    },
                    {
                        "sourceId": "skill_gc09",
                        "sourceType": "agent_skill",
                        "version": 2,
                        "contentHash": "b" * 64,
                    },
                ],
                "templateManifest": {"templateId": "project_strategy_report_v1"},
                "generatorAgent": "project_workspace",
            },
        },
        idempotency_key="gc09-create",
    )
    assert report["artifact"]["latest_version"] == 1
    assert report["artifact"]["latest"]["source_set_id"]

    updated = update_report(
        repository,
        identity,
        report_id="report_gc09",
        payload={
            "expectedVersion": 1,
            "title": "日慈项目分析报告（修订）",
            "contentMarkdown": "# 第二版\n\n补充新的判断。",
            "changeSummary": "补充判断",
        },
        idempotency_key="gc09-update",
    )
    assert updated["latest_version"] == 2

    restored = update_report(
        repository,
        identity,
        report_id="report_gc09",
        payload={"expectedVersion": 2},
        idempotency_key="gc09-restore",
        restored_from_version=1,
    )
    assert restored["latest_version"] == 3
    assert restored["title"] == "日慈项目分析报告"
    assert restored["latest"]["title"] == "日慈项目分析报告"
    assert restored["latest"]["content_markdown"].startswith("# 第一版")
    assert restored["latest"]["restored_from_version"] == 1
    assert len(report_versions(repository, identity, report_id="report_gc09")) == 3
    assert len(list_reports(repository, identity, project_id=seed["projectId"])) == 1

    grant = issue_export_grant(
        repository,
        identity,
        report_id="report_gc09",
        payload={"exportKind": "docx"},
        idempotency_key="gc09-export",
    )
    replayed_grant = issue_export_grant(
        repository,
        identity,
        report_id="report_gc09",
        payload={"exportKind": "docx"},
        idempotency_key="gc09-export",
    )
    assert grant["status"] == "active"
    assert grant["reportVersion"] == 3
    assert grant["idempotentReplay"] is False
    assert replayed_grant["grantId"] == grant["grantId"]
    assert replayed_grant["idempotentReplay"] is True

    with runtime_connection(repository.database_path, "cloud") as connection:
        assert len(user_tables(connection)) == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM source_set_members WHERE source_set_id=?",
            (report["artifact"]["latest"]["source_set_id"],),
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM export_grants WHERE export_kind='docx' AND status='active'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM publish_records WHERE artifact_kind='report_docx' "
            "AND status='authorized'"
        ).fetchone()[0] == 1
        names = user_tables(connection)
        assert "narrative_output_versions" not in names
