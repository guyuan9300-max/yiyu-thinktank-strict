from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from cloud_backend.app.repositories.intelligence_growth import (
    IntelligenceGrowthRepository,
)
from strict_common.schema import runtime_connection
from tests.strict_cloud_test_factory import (
    provision_test_organization,
    strict_cloud_test_client,
)


def _cloud(tmp_path: Path) -> tuple[TestClient, Path]:
    client, database, _ = strict_cloud_test_client(
        tmp_path,
        bootstrap_token="bootstrap-test",
        cloud_instance_id="cloud-consultation-test",
    )
    return client, database


def _bootstrap(client: TestClient) -> dict[str, Any]:
    return provision_test_organization(
        client,
        organization_name="咨询知识测试组织",
        display_name="管理员",
        email="consultation@example.com",
        password="12345678",
    )


def _headers(
    session: dict[str, Any],
    idempotency_key: str | None = None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {session['accessToken']}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def _command(
    client: TestClient,
    session: dict[str, Any],
    *,
    path: str,
    payload: dict[str, Any],
    key: str,
) -> Any:
    return client.post(
        "/api/v2/intelligence-growth/command",
        headers=_headers(session, key),
        json={
            "resourcePath": path,
            "method": "POST",
            "payload": payload,
        },
    )


def _default_project_id(client: TestClient, session: dict[str, Any]) -> str:
    response = client.get(
        "/api/v2/domain/project-materials/projects",
        headers=_headers(session),
    )
    assert response.status_code == 200, response.text
    return str(
        next(
            item
            for item in response.json()["projects"]
            if item["isDefaultInternalProject"]
        )["projectId"]
    )


def _request_payload(
    session: dict[str, Any],
    project_id: str,
) -> dict[str, Any]:
    return {
        "organizationId": session["organizationId"],
        "projectId": project_id,
        "requestedByMembershipId": session["membershipId"],
        "answerId": "answer-source-1",
        "sourceRequestId": "consultation-source-request-1",
        "target": "document_archive",
        "question": "PRIVATE_QUESTION_MUST_NOT_PERSIST",
        "answer": "这是成员明确确认可共享的咨询摘要。",
        "shareableFacts": ["事实一", "事实二"],
        "shareConfirmed": True,
    }


def test_consultation_request_publishes_confirmed_project_knowledge_without_raw_question(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        project_id = _default_project_id(client, session)
        payload = _request_payload(session, project_id)

        unconfirmed = _command(
            client,
            session,
            path="consultation/knowledge-requests",
            payload={**payload, "shareConfirmed": False},
            key="consultation-unconfirmed",
        )
        assert unconfirmed.status_code == 409, unconfirmed.text
        assert unconfirmed.json()["error"]["code"] == (
            "consultation_share_confirmation_required"
        )

        created = _command(
            client,
            session,
            path="consultation/knowledge-requests",
            payload=payload,
            key="consultation-create-1",
        )
        assert created.status_code == 200, created.text
        request = created.json()
        assert request["state"] == "pending"
        assert request["projectId"] == project_id
        assert request["requestedByMembershipId"] == session["membershipId"]
        assert request["question"] == ""
        assert request["answer"] == payload["answer"]

        replay = _command(
            client,
            session,
            path="consultation/knowledge-requests",
            payload=payload,
            key="consultation-create-1",
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == request["id"]

        listed = client.get(
            "/api/v2/intelligence-growth/query",
            headers=_headers(session),
            params={"resourcePath": "consultation/knowledge-requests"},
        )
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [request["id"]]

        processed = _command(
            client,
            session,
            path="consultation/knowledge-requests/process-pending",
            payload={"batchSize": 10},
            key="consultation-process-1",
        )
        assert processed.status_code == 200, processed.text
        summary = processed.json()
        assert summary["totalPending"] == 1
        assert summary["processedCount"] == 1
        assert summary["completedCount"] == 1
        assert summary["failedCount"] == 0
        completed = summary["items"][0]
        assert completed["state"] == "completed"
        assert completed["knowledgeDocumentId"]
        assert completed["documentVersionId"]

        context = client.get(
            f"/api/v2/projects/{project_id}/knowledge-context",
            headers=_headers(session),
        )
        assert context.status_code == 200, context.text
        context_item = next(
            item
            for item in context.json()["organizationSharedKnowledge"]
            if item["sourceId"] == completed["knowledgeDocumentId"]
        )
        assert context_item["sourceType"] == "knowledge_summary"
        assert "consultation_summary" in context_item["sourceDescription"]
        assert payload["answer"] in context_item["summary"]
        assert payload["question"] not in json.dumps(
            context.json(),
            ensure_ascii=False,
        )

    with runtime_connection(database, "cloud") as connection:
        request_row = connection.execute(
            """
            SELECT status, visibility_scope, source_payload_json, version
            FROM intelligence_records
            WHERE intelligence_id = ?
            """,
            (request["id"],),
        ).fetchone()
        assert request_row is not None
        assert request_row["status"] == "accepted"
        assert request_row["visibility_scope"] == "self"
        assert int(request_row["version"]) == 2
        source = json.loads(str(request_row["source_payload_json"]))
        assert source["requestState"] == "completed"
        assert source["organizationId"] == session["organizationId"]
        assert source["projectId"] == project_id
        assert source["requestedByMembershipId"] == session["membershipId"]

        document = connection.execute(
            """
            SELECT document_kind, visibility_scope, parse_state,
                   project_id, current_version, version
            FROM knowledge_documents
            WHERE document_id = ?
            """,
            (completed["knowledgeDocumentId"],),
        ).fetchone()
        assert document is not None
        assert tuple(document) == (
            "consultation_summary",
            "organization",
            "ready",
            project_id,
            1,
            1,
        )

        serialized_records = "\n".join(
            str(row[0])
            for table, column in (
                ("command_envelopes", "payload_json"),
                ("audit_events", "summary_json"),
                ("delivery_outbox", "payload_json"),
            )
            for row in connection.execute(
                f"SELECT {column} FROM {table}"
            ).fetchall()
        )
        assert payload["question"] not in serialized_records
        assert payload["answer"] not in serialized_records
        assert "answer-source-1" in serialized_records

        assert int(
            connection.execute(
                """
                SELECT COUNT(*) FROM knowledge_documents
                WHERE document_id = ?
                """,
                (completed["knowledgeDocumentId"],),
            ).fetchone()[0]
        ) == 1


def test_consultation_scope_cas_blocked_retry_and_process_are_explicit(
    tmp_path: Path,
) -> None:
    client, database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        project_id = _default_project_id(client, session)
        payload = _request_payload(session, project_id)

        mismatch = _command(
            client,
            session,
            path="consultation/knowledge-requests",
            payload={**payload, "organizationId": "org-not-current"},
            key="consultation-scope-mismatch",
        )
        assert mismatch.status_code == 403, mismatch.text
        assert mismatch.json()["error"]["code"] == "consultation_scope_mismatch"

        created = _command(
            client,
            session,
            path="consultation/knowledge-requests",
            payload=payload,
            key="consultation-create-blocked",
        )
        assert created.status_code == 200, created.text
        request_id = created.json()["id"]

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                UPDATE work_projects
                SET lifecycle_state = 'archived', version = version + 1
                WHERE project_id = ?
                """,
                (project_id,),
            )
            connection.commit()

        blocked = _command(
            client,
            session,
            path="consultation/knowledge-requests/process-pending",
            payload={"batchSize": 1},
            key="consultation-process-blocked",
        )
        assert blocked.status_code == 200, blocked.text
        blocked_item = blocked.json()["items"][0]
        assert blocked_item["state"] == "blocked"
        assert blocked_item["errorCode"] == "consultation_project_missing"
        assert blocked_item["retryable"] is False

        retry_conflict = _command(
            client,
            session,
            path=f"consultation/knowledge-requests/{request_id}/retry",
            payload={"expectedVersion": 1},
            key="consultation-retry-conflict",
        )
        assert retry_conflict.status_code == 409, retry_conflict.text
        assert retry_conflict.json()["error"]["code"] == "version_conflict"

        with runtime_connection(database, "cloud") as connection:
            connection.execute(
                """
                UPDATE work_projects
                SET lifecycle_state = 'active', version = version + 1
                WHERE project_id = ?
                """,
                (project_id,),
            )
            connection.commit()

        version = client.get(
            "/api/v2/intelligence-growth/version",
            headers=_headers(session),
            params={
                "resourcePath": (
                    f"consultation/knowledge-requests/{request_id}/retry"
                )
            },
        )
        assert version.status_code == 200, version.text
        retried = _command(
            client,
            session,
            path=f"consultation/knowledge-requests/{request_id}/retry",
            payload={"expectedVersion": version.json()["expectedVersion"]},
            key="consultation-retry-valid",
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["state"] == "pending"

        completed = _command(
            client,
            session,
            path="consultation/knowledge-requests/process-pending",
            payload={"batchSize": 1},
            key="consultation-process-after-retry",
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["completedCount"] == 1
        assert completed.json()["items"][0]["state"] == "completed"


def test_consultation_transient_processing_failure_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _database = _cloud(tmp_path)
    with client:
        session = _bootstrap(client)
        project_id = _default_project_id(client, session)
        created = _command(
            client,
            session,
            path="consultation/knowledge-requests",
            payload=_request_payload(session, project_id),
            key="consultation-create-transient",
        )
        assert created.status_code == 200, created.text

        original_publish = (
            IntelligenceGrowthRepository._publish_consultation_request
        )

        def fail_once(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("TRANSIENT_PRIVATE_DETAIL")

        monkeypatch.setattr(
            IntelligenceGrowthRepository,
            "_publish_consultation_request",
            fail_once,
        )
        failed = _command(
            client,
            session,
            path="consultation/knowledge-requests/process-pending",
            payload={"batchSize": 1},
            key="consultation-process-transient",
        )
        assert failed.status_code == 200, failed.text
        failed_item = failed.json()["items"][0]
        assert failed_item["state"] == "failed_retryable"
        assert failed_item["retryable"] is True
        assert failed_item["errorCode"] == "consultation_processing_failed"
        assert "TRANSIENT_PRIVATE_DETAIL" not in json.dumps(
            failed.json(),
            ensure_ascii=False,
        )

        monkeypatch.setattr(
            IntelligenceGrowthRepository,
            "_publish_consultation_request",
            original_publish,
        )
        completed = _command(
            client,
            session,
            path="consultation/knowledge-requests/process-pending",
            payload={"batchSize": 1},
            key="consultation-process-transient-retry",
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["completedCount"] == 1
        assert completed.json()["items"][0]["state"] == "completed"
