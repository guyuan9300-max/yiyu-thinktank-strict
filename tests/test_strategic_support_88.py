from __future__ import annotations

from pathlib import Path

from cloud_backend.app.repositories.gc04_tasks import GC04TaskRepository
from cloud_backend.app.repositories.gc06_planning import create_meeting
from cloud_backend.app.repositories.strategic_support import (
    analysis_job_detail,
    analysis_job_stages,
    archive_project_text,
    meeting_action_items,
    project_text_items,
    register_analysis_job,
    save_project_text,
    suggestion_log,
    write_suggestion_log,
)
from cloud_backend.app.repositories.workbench_outputs import project_workspace
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository


def _assert_strict_database(repository) -> None:
    with runtime_connection(repository.database_path, "cloud", read_only=True) as connection:
        table_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        assert table_count == 88
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_strategic_workspace_reads_standard_client_task_and_event_tables(
    tmp_path: Path,
) -> None:
    repository, identity, payload = _repository(tmp_path)
    task = GC04TaskRepository(repository).create_task(
        identity,
        payload={
            "clientId": payload["projectId"],
            "title": "核实战略陪伴下一步",
            "description": "从客户档案进入正式任务权威",
            "priority": "high",
        },
        idempotency_key="strategic-workspace-task",
    )["task"]
    workspace = project_workspace(
        repository,
        identity,
        project_id=payload["projectId"],
    )
    assert workspace["project"]["projectId"] == payload["projectId"]
    assert workspace["tasks"][0]["taskId"] == task["id"]
    assert workspace["tasks"][0]["description"] == "从客户档案进入正式任务权威"
    assert workspace["favorites"] == []
    _assert_strict_database(repository)


def test_strategic_documents_and_suggestion_history_are_cas_idempotent(
    tmp_path: Path,
) -> None:
    repository, identity, payload = _repository(tmp_path)
    project_id = payload["projectId"]
    created = save_project_text(
        repository,
        identity,
        project_id=project_id,
        key="strategic_doc:strategy",
        payload={"markdownContent": "坚持儿童成长与社会情感学习。", "expectedVersion": 0},
        idempotency_key="strategic-document-create",
    )
    replay = save_project_text(
        repository,
        identity,
        project_id=project_id,
        key="strategic_doc:strategy",
        payload={"markdownContent": "坚持儿童成长与社会情感学习。", "expectedVersion": 0},
        idempotency_key="strategic-document-create",
    )
    assert replay == created
    assert project_text_items(repository, identity, project_id=project_id)[
        "strategic_doc:strategy"
    ]["markdownContent"] == "坚持儿童成长与社会情感学习。"

    saved_log = write_suggestion_log(
        repository,
        identity,
        project_id=project_id,
        payload={
            "fingerprint": "next-step-1",
            "action": "promoted",
            "actor": "项目负责人",
            "suggestionText": "确认下一阶段目标",
        },
        idempotency_key="strategic-suggestion-save",
    )
    assert saved_log["ok"] is True
    assert suggestion_log(repository, identity, project_id=project_id)["promoted"][0][
        "suggestionText"
    ] == "确认下一阶段目标"
    removed = write_suggestion_log(
        repository,
        identity,
        project_id=project_id,
        fingerprint="next-step-1",
        payload={},
        idempotency_key="strategic-suggestion-remove",
        archive=True,
    )
    assert removed["ok"] is True
    assert suggestion_log(repository, identity, project_id=project_id)["promoted"] == []

    archived = archive_project_text(
        repository,
        identity,
        project_id=project_id,
        key="strategic_doc:strategy",
        payload={"expectedVersion": created["version"]},
        idempotency_key="strategic-document-delete",
    )
    assert archived["ok"] is True
    assert project_text_items(repository, identity, project_id=project_id) == {}
    _assert_strict_database(repository)


def test_analysis_job_uses_source_assets_and_processing_attempts(
    tmp_path: Path,
) -> None:
    repository, identity, answer = _repository(tmp_path)
    repository.save_ai_answer(
        identity,
        payload=answer,
        idempotency_key="strategic-source-answer",
    )
    created = register_analysis_job(
        repository,
        identity,
        payload={
            "answerId": answer["answerId"],
            "projectId": answer["projectId"],
            "jobType": "workbench_analysis",
        },
        idempotency_key="strategic-analysis-job",
    )
    replay = register_analysis_job(
        repository,
        identity,
        payload={
            "answerId": answer["answerId"],
            "projectId": answer["projectId"],
            "jobType": "workbench_analysis",
        },
        idempotency_key="strategic-analysis-job",
    )
    assert replay == created
    assert analysis_job_detail(
        repository,
        identity,
        job_id=answer["answerId"],
    )["authorityType"] == "processing_attempts"
    assert analysis_job_stages(
        repository,
        identity,
        job_id=answer["answerId"],
    )[0]["status"] == "completed"
    with runtime_connection(repository.database_path, "cloud", read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM processing_attempts").fetchone()[0] == 1
        assert connection.execute(
            "SELECT source_kind FROM source_assets WHERE source_kind='workbench_analysis_job'"
        ).fetchone()[0] == "workbench_analysis_job"
    _assert_strict_database(repository)


def test_meeting_action_items_read_tasks_and_meetings(tmp_path: Path) -> None:
    repository, identity, payload = _repository(tmp_path)
    project_id = payload["projectId"]
    meeting = create_meeting(
        repository,
        identity,
        payload={
            "clientId": project_id,
            "title": "项目复盘会",
            "startsAt": "2026-08-08T09:00:00Z",
            "endsAt": "2026-08-08T10:00:00Z",
        },
        idempotency_key="strategic-meeting",
    )["meeting"]
    task = GC04TaskRepository(repository).create_task(
        identity,
        payload={
            "clientId": project_id,
            "title": "整理会议行动项",
            "sourceType": "meeting",
            "sourceId": meeting["id"],
        },
        idempotency_key="strategic-meeting-task",
    )["task"]
    result = meeting_action_items(repository, identity, project_id=project_id)
    assert result["totalHigh"] == 1
    assert result["high"][0]["taskId"] == task["id"]
    assert result["high"][0]["sourceDocTitle"] == "项目复盘会"
    _assert_strict_database(repository)
