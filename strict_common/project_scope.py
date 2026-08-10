from __future__ import annotations

import sqlite3
from typing import Any

from .ids import canonical_json, sha256_text


PROJECT_SCOPE_DECISION: dict[str, Any] = {
    "id": "adr_gc03_client_is_project_v1",
    "decision_key": "client_is_project",
    "decision_version": 1,
    "decision_status": "approved",
    "approved_option": "client_is_project",
    "rationale": (
        "益语智库中的客户与项目使用同一个 clients 聚合；任务可以不属于项目，"
        "事件线和会议必须通过 client_id 属于一个项目。"
    ),
    "approved_by": "yiyu_product_decision_20260803",
    "approved_at": "2026-08-03T00:00:00Z",
    "effective_at": "2026-08-04T00:00:00Z",
}


def project_scope_evidence_hash() -> str:
    evidence = {
        key: value
        for key, value in PROJECT_SCOPE_DECISION.items()
        if key not in {"id", "evidence_hash"}
    }
    return sha256_text(canonical_json(evidence))


def seed_project_scope_decision(
    connection: sqlite3.Connection,
    *,
    schema_version_id: str,
) -> None:
    """Seed the signed build ADR without exposing a runtime business write path."""

    expected = {
        **PROJECT_SCOPE_DECISION,
        "schema_version_id": schema_version_id,
        "evidence_hash": project_scope_evidence_hash(),
    }
    existing = connection.execute(
        """
        SELECT id, decision_key, decision_version, decision_status,
               approved_option, rationale, approved_by, approved_at,
               effective_at, schema_version_id, evidence_hash
        FROM project_scope_decisions
        WHERE decision_key=? AND decision_version=?
        """,
        (expected["decision_key"], expected["decision_version"]),
    ).fetchone()
    columns = tuple(expected)
    if existing is not None:
        actual = dict(zip(columns, existing, strict=True))
        if actual != expected:
            raise RuntimeError(
                "project scope ADR differs from the signed build decision"
            )
        return
    connection.execute(
        """
        INSERT INTO project_scope_decisions (
            id, decision_key, decision_version, decision_status,
            approved_option, rationale, approved_by, approved_at,
            effective_at, schema_version_id, evidence_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(expected[column] for column in columns),
    )
