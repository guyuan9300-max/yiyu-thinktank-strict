from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cloud_backend.app.repository import CloudRepository, SessionIdentity
from strict_common.ids import canonical_json, sha256_text
from strict_common.schema import initialize_database


SCOPE_ID = "scope-story-simulation"
PROJECT_ID = "project-xingcong"


def _identity() -> SessionIdentity:
    return SessionIdentity(
        session_id="session-story-simulation",
        principal_id="principal-story-simulation",
        membership_id="membership-story-simulation",
        organization_id="organization-story-simulation",
        cloud_instance_id="cloud-story-simulation",
        scope_id=SCOPE_ID,
        system_role="admin",
        visibility_scope="organization",
        display_name="Story simulation",
    )


def _database(tmp_path: Path) -> sqlite3.Connection:
    database_path = tmp_path / "project-story-authority.db"
    initialize_database(database_path, "cloud")
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _insert_story(
    connection: sqlite3.Connection,
    *,
    suffix: str,
    project_id: str = PROJECT_ID,
    publication_state: str = "published",
    source_project_id: str | None = None,
    receipt: dict[str, object] | None = None,
    content_markdown: str | None = None,
) -> None:
    story_id = f"story-{suffix}"
    source_set_id = f"source-set-{suffix}"
    fact_source_set_id = f"fact-source-set-{suffix}"
    manifest_id = f"manifest-{suffix}"
    fact_manifest_id = f"fact-manifest-{suffix}"
    source_manifest_id = f"source-manifest-{suffix}"
    source_asset_id = f"source-asset-{suffix}"
    document_id = f"document-{suffix}"
    document_version_id = f"document-version-{suffix}"
    chunk_id = f"chunk-{suffix}"
    fact_id = f"fact-{suffix}"
    timestamp = f"2026-08-27T00:00:0{len(suffix) % 10}Z"
    evidence_timestamp = f"2026-08-26T10:00:0{len(suffix) % 10}Z"
    fact_timestamp = f"2026-08-26T12:00:0{len(suffix) % 10}Z"
    fact_text = f"星丛已核验项目事实 {suffix}"
    chunk_hash = sha256_text(f"source-chunk-{suffix}")
    source_receipt_payload = {
        "schema": "yiyu.project-story-source-snapshot.v1",
        "projectId": project_id,
        "sourceAssetId": source_asset_id,
        "documentId": document_id,
        "contentHash": chunk_hash,
        "capturedAt": evidence_timestamp,
    }
    source_receipt = canonical_json(source_receipt_payload)
    fact_receipt_payload = {
        "schema": "yiyu.verified-project-fact.v1",
        "factText": fact_text,
        "sourceChunkHash": chunk_hash,
        "confirmedByMembershipId": "membership-story-simulation",
        "verifiedAt": fact_timestamp,
    }
    fact_receipt = canonical_json(fact_receipt_payload)
    fact_hash = sha256_text(fact_text)
    facts_digest = sha256_text(
        canonical_json(
            [
                {
                    "ordinal": 0,
                    "factId": fact_id,
                    "version": 1,
                    "factHash": fact_hash,
                }
            ]
        )
    )
    receipt_payload = receipt if receipt is not None else {
        "schema": "yiyu.authoritative-project-story.v1",
        "projectId": project_id,
        "storyId": story_id,
        "sourceSetId": source_set_id,
        "sourceSetVersion": 1,
        "lineageId": f"lineage-{suffix}",
        "version": 1,
        "contentMarkdown": content_markdown or (
            "星丛正在形成以 AI 原生运营为核心、"
            "以真实验收为边界的工作体系。"
        ),
        "contentHash": "__filled_after_content__",
        "factsDigest": facts_digest,
        "knowledgeCutoff": "2026-08-26T23:59:59Z",
        "generatorVersion": "project-story-simulation-v1",
        "publishedAt": timestamp,
    }
    content_value = receipt_payload.get("contentMarkdown")
    content = content_value if isinstance(content_value, str) else ""
    content_hash = sha256_text(content)
    if receipt is None:
        receipt_payload["contentHash"] = content_hash
    serialized_receipt = canonical_json(receipt_payload)
    version_integrity_hash = sha256_text(
        canonical_json(
            {
                "schema": "yiyu.authoritative-project-story-version.v1",
                "projectId": project_id,
                "storyId": story_id,
                "sourceSetId": source_set_id,
                "sourceSetVersion": 1,
                "lineageId": f"lineage-{suffix}",
                "version": 1,
                "contentHash": content_hash,
                "factsDigest": str(receipt_payload.get("factsDigest") or ""),
                "receiptHash": sha256_text(serialized_receipt),
            }
        )
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO organization_memberships (
          id, scope_id, principal_id, role_key, status, version, record_kind,
          visibility_scope, lifecycle_state, created_at, updated_at
        ) VALUES (?, ?, ?, 'admin', 'active', 1, 'membership', 'organization',
                  'active', ?, ?)
        """,
        (
            "membership-story-simulation",
            SCOPE_ID,
            "principal-story-simulation",
            evidence_timestamp,
            evidence_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO object_manifests (
          id, scope_id, content_hash, lifecycle_state, receipt, storage_kind,
          media_type, availability_state, receipt_hash, created_at, verified_at,
          authority_role
        ) VALUES (?, ?, ?, 'active', ?, 'verified_project_source_snapshot',
                  'application/vnd.yiyu.project-story-source+json', 'ready',
                  ?, ?, ?, 'cloud')
        """,
        (
            source_manifest_id,
            SCOPE_ID,
            chunk_hash,
            source_receipt,
            sha256_text(source_receipt),
            evidence_timestamp,
            evidence_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO secured_resources (
          id, scope_id, resource_kind, lifecycle_state, version,
          resource_type_key, created_at, updated_at, authority_role
        ) VALUES (?, ?, 'source_asset', 'active', 1,
                  'project_story_evidence_source', ?, ?, 'cloud')
        """,
        (source_asset_id, SCOPE_ID, evidence_timestamp, evidence_timestamp),
    )
    connection.execute(
        """
        INSERT INTO source_assets (
          id, scope_id, client_id, object_manifest_id, content_hash,
          record_kind, source_kind, display_name, media_type, byte_size,
          created_by_membership_id, availability_state, version,
          lifecycle_state, created_at, updated_at, authority_role
        ) VALUES (?, ?, ?, ?, ?, 'asset', 'project_story_evidence',
                  'Story 核验来源', 'application/json', 1, ?, 'ready', 1,
                  'active', ?, ?, 'cloud')
        """,
        (
            source_asset_id,
            SCOPE_ID,
            project_id,
            source_manifest_id,
            chunk_hash,
            "membership-story-simulation",
            evidence_timestamp,
            evidence_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO secured_resources (
          id, scope_id, resource_kind, lifecycle_state, version,
          resource_type_key, created_at, updated_at, authority_role
        ) VALUES (?, ?, 'knowledge_document', 'active', 1,
                  'project_story_evidence_document', ?, ?, 'cloud')
        """,
        (document_id, SCOPE_ID, evidence_timestamp, evidence_timestamp),
    )
    connection.execute(
        """
        INSERT INTO knowledge_documents (
          id, scope_id, source_asset_id, client_id, current_version,
          owner_membership_id, title, document_kind, visibility_scope,
          parse_state, publication_state, published_at, version,
          lifecycle_state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, ?, 'Story 核验来源', 'project_evidence',
                  'organization', 'ready', 'published', ?, 1, 'active', ?, ?)
        """,
        (
            document_id,
            SCOPE_ID,
            source_asset_id,
            project_id,
            "membership-story-simulation",
            evidence_timestamp,
            evidence_timestamp,
            evidence_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO document_versions (
          id, scope_id, document_id, version, content_hash, created_at,
          object_manifest_id, source_asset_version, publication_state,
          created_by_membership_id, integrity_hash
        ) VALUES (?, ?, ?, 1, ?, ?, ?, 1, 'published', ?, ?)
        """,
        (
            document_version_id,
            SCOPE_ID,
            document_id,
            chunk_hash,
            evidence_timestamp,
            source_manifest_id,
            "membership-story-simulation",
            sha256_text(f"{document_id}|1|{chunk_hash}"),
        ),
    )
    connection.execute(
        """
        INSERT INTO content_chunks (
          id, scope_id, document_version_id, ordinal, policy_version,
          chunk_hash, object_manifest_id, embedding_eligibility, created_at, version,
          lifecycle_state, updated_at, authority_role
        ) VALUES (?, ?, ?, 0, 1, ?, ?, 'eligible', ?, 1, 'active', ?, 'cloud')
        """,
        (
            chunk_id,
            SCOPE_ID,
            document_version_id,
            chunk_hash,
            source_manifest_id,
            evidence_timestamp,
            evidence_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO source_sets (
          id, scope_id, client_id, source_count, version, purpose_kind,
          publication_state, created_by_principal_id, created_at,
          lifecycle_state, updated_at, authority_role
        ) VALUES (?, ?, ?, 1, 1, 'verified_project_fact_evidence', 'published',
                  ?, ?, 'active', ?, 'cloud')
        """,
        (
            fact_source_set_id,
            SCOPE_ID,
            project_id,
            "principal-story-simulation",
            fact_timestamp,
            fact_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO source_set_members (
          id, scope_id, source_set_id, source_object_id, source_version,
          policy_version, source_object_kind, ordinal, added_at, version,
          lifecycle_state, created_at, updated_at, authority_role
        ) VALUES (?, ?, ?, ?, 1, 1, 'knowledge_document', 0, ?, 1,
                  'active', ?, ?, 'cloud')
        """,
        (
            f"fact-source-member-{suffix}",
            SCOPE_ID,
            fact_source_set_id,
            document_id,
            fact_timestamp,
            fact_timestamp,
            fact_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO object_manifests (
          id, scope_id, content_hash, lifecycle_state, receipt, storage_kind,
          media_type, availability_state, receipt_hash, created_at, verified_at,
          authority_role
        ) VALUES (?, ?, ?, 'active', ?, 'verified_fact_receipt',
                  'application/vnd.yiyu.verified-project-fact+json', 'ready',
                  ?, ?, ?, 'cloud')
        """,
        (
            fact_manifest_id,
            SCOPE_ID,
            fact_hash,
            fact_receipt,
            sha256_text(fact_receipt),
            fact_timestamp,
            fact_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO source_sets (
          id, scope_id, client_id, source_count, version, purpose_kind,
          publication_state, created_by_principal_id, created_at,
          lifecycle_state, updated_at, authority_role
        ) VALUES (?, ?, ?, 1, 1, 'project_story_evidence', 'published', ?, ?, 'active', ?, 'cloud')
        """,
        (
            source_set_id,
            SCOPE_ID,
            source_project_id or project_id,
            "principal-story-simulation",
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO object_manifests (
          id, scope_id, content_hash, lifecycle_state, receipt, storage_kind,
          media_type, availability_state, receipt_hash, created_at, verified_at,
          authority_role
        ) VALUES (?, ?, ?, 'active', ?, 'project_story_snapshot',
                  'application/vnd.yiyu.project-story+json', 'ready', ?, ?, ?, 'cloud')
        """,
        (
            manifest_id,
            SCOPE_ID,
            content_hash,
            serialized_receipt,
            sha256_text(serialized_receipt),
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO secured_resources (
          id, scope_id, resource_kind, lifecycle_state, version,
          resource_type_key, created_at, updated_at, authority_role
        ) VALUES (?, ?, 'narrative_output', 'active', 1, 'project_story', ?, ?, 'cloud')
        """,
        (story_id, SCOPE_ID, timestamp, timestamp),
    )
    connection.execute(
        """
        INSERT INTO narrative_outputs (
          id, scope_id, client_id, source_set_id, current_version,
          lifecycle_state, title, artifact_kind, visibility_scope,
          publication_state, published_at, version, created_at, updated_at,
          authority_role
        ) VALUES (?, ?, ?, ?, 1, 'active', '星丛项目 Story', 'project_story',
                  'organization', ?, ?, 1, ?, ?, 'cloud')
        """,
        (
            story_id,
            SCOPE_ID,
            project_id,
            source_set_id,
            publication_state,
            timestamp if publication_state == "published" else None,
            timestamp,
            timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO artifact_versions (
          id, scope_id, artifact_id, version, content_hash, object_manifest_id,
          source_set_id, publication_state, created_at, integrity_hash, authority_role
        ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'cloud')
        """,
        (
            f"version-{suffix}",
            SCOPE_ID,
            story_id,
            content_hash,
            manifest_id,
            source_set_id,
            publication_state,
            timestamp,
            version_integrity_hash,
        ),
    )
    connection.execute(
        """
        INSERT INTO secured_resources (
          id, scope_id, resource_kind, lifecycle_state, version,
          resource_type_key, created_at, updated_at, authority_role
        ) VALUES (?, ?, 'atomic_fact', 'active', 1, 'verified_story_fact', ?, ?, 'cloud')
        """,
        (fact_id, SCOPE_ID, fact_timestamp, fact_timestamp),
    )
    connection.execute(
        """
        INSERT INTO atomic_facts (
          id, scope_id, chunk_id, fact_hash, confidence, version, source_set_id,
          fact_object_manifest_id, verification_state,
          confirmed_by_membership_id, confirmed_at, lifecycle_state,
          created_at, updated_at, authority_role
        ) VALUES (?, ?, ?, ?, 1.0, 1, ?, ?, 'verified', ?, ?, 'active', ?, ?, 'cloud')
        """,
        (
            fact_id,
            SCOPE_ID,
            chunk_id,
            fact_hash,
            fact_source_set_id,
            fact_manifest_id,
            "membership-story-simulation",
            fact_timestamp,
            fact_timestamp,
            fact_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO source_set_members (
          id, scope_id, source_set_id, source_object_id, source_version,
          policy_version, source_object_kind, ordinal, added_at, version,
          lifecycle_state, created_at, updated_at, authority_role
        ) VALUES (?, ?, ?, ?, 1, 1, 'atomic_fact', 0, ?, 1, 'active', ?, ?, 'cloud')
        """,
        (
            f"source-member-{suffix}",
            SCOPE_ID,
            source_set_id,
            fact_id,
            timestamp,
            timestamp,
            timestamp,
        ),
    )
    locator = canonical_json(
        {
            "schema": "yiyu.project-story-fact-evidence.v1",
            "chunkId": chunk_id,
        }
    )
    connection.execute(
        """
        INSERT INTO evidence_links (
          id, scope_id, fact_id, source_object_id, source_version, locator,
          source_object_kind, locator_kind, locator_hash, created_at
        ) VALUES (?, ?, ?, ?, 1, ?, 'knowledge_document', 'content_chunk', ?, ?)
        """,
        (
            f"evidence-{suffix}",
            SCOPE_ID,
            fact_id,
            document_id,
            locator,
            sha256_text(locator),
            fact_timestamp,
        ),
    )
    connection.execute(
        """
        INSERT INTO derivation_lineage (
          id, scope_id, source_set_id, derivative_kind, derivative_object_id,
          generator_version, generated_at, source_version, authority_role
        ) VALUES (?, ?, ?, 'project_story', ?, 'project-story-simulation-v1', ?, 1, 'cloud')
        """,
        (
            f"lineage-{suffix}",
            SCOPE_ID,
            source_set_id,
            story_id,
            timestamp,
        ),
    )
    connection.commit()


def test_authority_projection_returns_only_one_complete_published_story(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="valid")

    result = CloudRepository._project_story_context(
        connection,
        _identity(),
        project_id=PROJECT_ID,
    )

    assert result["state"] == "ready"
    assert result["projectId"] == PROJECT_ID
    assert result["storyId"] == "story-valid"
    assert result["version"] == 1
    assert result["sourceSetId"] == "source-set-valid"
    assert result["contentHash"] == sha256_text(result["content"])
    assert "AI 原生运营" in result["content"]


def test_authority_projection_ignores_draft_and_wrong_project_story(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="draft", publication_state="draft")
    _insert_story(connection, suffix="other", project_id="project-other")

    result = CloudRepository._project_story_context(
        connection,
        _identity(),
        project_id=PROJECT_ID,
    )

    assert result == {"state": "not_available", "projectId": PROJECT_ID}


def test_authority_projection_rejects_source_set_for_another_project(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(
        connection,
        suffix="source-mismatch",
        source_project_id="project-other",
    )

    result = CloudRepository._project_story_context(
        connection,
        _identity(),
        project_id=PROJECT_ID,
    )

    assert result == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_fails_closed_when_two_published_stories_exist(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="first")
    _insert_story(connection, suffix="second")

    result = CloudRepository._project_story_context(
        connection,
        _identity(),
        project_id=PROJECT_ID,
    )

    assert result == {
        "state": "authority_conflict",
        "projectId": PROJECT_ID,
        "candidateCount": 2,
    }


def test_authority_projection_rejects_story_without_authoritative_content(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(
        connection,
        suffix="empty",
        receipt={"summary": "普通摘要不能充当 Story"},
    )

    result = CloudRepository._project_story_context(
        connection,
        _identity(),
        project_id=PROJECT_ID,
    )

    assert result == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_tampered_receipt_with_stale_hashes(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="tampered")
    tampered = canonical_json(
        {
            "contentMarkdown": "港货北上研究被伪装成正式 Story。",
            "knowledgeCutoff": "2026-08-27T23:59:59Z",
            "generatorVersion": "project-story-simulation-v1",
        }
    )
    connection.execute(
        "UPDATE object_manifests SET receipt=? WHERE id='manifest-tampered'",
        (tampered,),
    )
    connection.commit()

    result = CloudRepository._project_story_context(
        connection,
        _identity(),
        project_id=PROJECT_ID,
    )

    assert result == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_tampered_content_with_new_receipt_hash(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="tampered-content")
    tampered = canonical_json(
        {
            "contentMarkdown": "港货北上研究被伪装成正式 Story。",
            "knowledgeCutoff": "2026-08-27T23:59:59Z",
            "generatorVersion": "project-story-simulation-v1",
        }
    )
    connection.execute(
        """
        UPDATE object_manifests
           SET receipt=?, receipt_hash=?
         WHERE id='manifest-tampered-content'
        """,
        (tampered, sha256_text(tampered)),
    )
    connection.commit()

    result = CloudRepository._project_story_context(
        connection,
        _identity(),
        project_id=PROJECT_ID,
    )

    assert result == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_requires_a_real_verified_atomic_fact(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="missing-fact")
    connection.execute("DELETE FROM atomic_facts WHERE id='fact-missing-fact'")
    connection.commit()

    result = CloudRepository._project_story_context(
        connection,
        _identity(),
        project_id=PROJECT_ID,
    )

    assert result == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_duplicate_current_version_rows(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="duplicate-version")
    row = connection.execute(
        "SELECT * FROM artifact_versions WHERE id='version-duplicate-version'"
    ).fetchone()
    connection.execute(
        """
        INSERT INTO artifact_versions (
          id, scope_id, artifact_id, version, content_hash, object_manifest_id,
          source_set_id, publication_state, created_at, authority_role
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "version-duplicate-version-copy",
            row["scope_id"],
            row["artifact_id"],
            row["version"],
            row["content_hash"],
            row["object_manifest_id"],
            row["source_set_id"],
            row["publication_state"],
            row["created_at"],
            row["authority_role"],
        ),
    )
    connection.commit()

    result = CloudRepository._project_story_context(
        connection,
        _identity(),
        project_id=PROJECT_ID,
    )

    assert result == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_ghost_fact_without_evidence_chain(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="ghost-fact")
    connection.execute(
        """
        UPDATE atomic_facts
           SET chunk_id=NULL, source_set_id=NULL,
               fact_object_manifest_id=NULL, confirmed_by_membership_id=NULL
         WHERE id='fact-ghost-fact'
        """
    )
    connection.commit()

    result = CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    )

    assert result == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_unparseable_or_naive_story_times(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="bad-time")
    connection.execute(
        "UPDATE object_manifests SET verified_at='not-a-time' WHERE id='manifest-bad-time'"
    )
    connection.commit()
    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}

    connection = _database(tmp_path / "naive")
    _insert_story(connection, suffix="naive-time")
    receipt = dict(
        json.loads(
            connection.execute(
                "SELECT receipt FROM object_manifests WHERE id='manifest-naive-time'"
            ).fetchone()[0]
        )
    )
    receipt["publishedAt"] = "2026-08-27T00:00:00"
    serialized = canonical_json(receipt)
    connection.execute(
        "UPDATE narrative_outputs SET published_at='2026-08-27T00:00:00' WHERE id='story-naive-time'"
    )
    connection.execute(
        "UPDATE object_manifests SET receipt=?, receipt_hash=? WHERE id='manifest-naive-time'",
        (serialized, sha256_text(serialized)),
    )
    connection.commit()
    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_future_story_timestamps(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="future")
    receipt = dict(
        json.loads(
            connection.execute(
                "SELECT receipt FROM object_manifests WHERE id='manifest-future'"
            ).fetchone()[0]
        )
    )
    receipt.update(
        {
            "knowledgeCutoff": "2098-12-31T23:59:59Z",
            "publishedAt": "2099-01-02T00:00:00Z",
        }
    )
    serialized = canonical_json(receipt)
    connection.execute(
        "UPDATE narrative_outputs SET published_at='2099-01-02T00:00:00Z' WHERE id='story-future'"
    )
    connection.execute(
        "UPDATE derivation_lineage SET generated_at='2099-01-01T00:00:00Z' WHERE id='lineage-future'"
    )
    connection.execute(
        "UPDATE object_manifests SET receipt=?, receipt_hash=?, verified_at='2099-01-01T12:00:00Z' WHERE id='manifest-future'",
        (serialized, sha256_text(serialized)),
    )
    connection.commit()

    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_binds_resource_versions_and_fact_type(tmp_path: Path) -> None:
    for suffix, resource_id, update in (
        ("story-version", "story-story-version", "version=999"),
        ("fact-version", "fact-fact-version", "version=999"),
        (
            "fact-type",
            "fact-fact-type",
            "resource_type_key='ordinary_shared_summary'",
        ),
    ):
        connection = _database(tmp_path / suffix)
        _insert_story(connection, suffix=suffix)
        connection.execute(
            f"UPDATE secured_resources SET {update} WHERE id=?",  # noqa: S608
            (resource_id,),
        )
        connection.commit()

        assert CloudRepository._project_story_context(
            connection, _identity(), project_id=PROJECT_ID
        ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_binds_lineage_generator_to_receipt(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="generator-mismatch")
    connection.execute(
        "UPDATE derivation_lineage SET generator_version='other-generator' "
        "WHERE id='lineage-generator-mismatch'"
    )
    connection.commit()

    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_tampered_fact_receipt(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="fact-tamper")
    receipt = canonical_json(
        {
            "schema": "yiyu.verified-project-fact.v1",
            "factText": "未经核验的任意事实",
            "sourceChunkHash": sha256_text("source-chunk-fact-tamper"),
            "confirmedByMembershipId": "membership-story-simulation",
            "verifiedAt": "2026-08-26T12:00:01Z",
        }
    )
    connection.execute(
        "UPDATE object_manifests SET receipt=?, receipt_hash=? "
        "WHERE id='fact-manifest-fact-tamper'",
        (receipt, sha256_text(receipt)),
    )
    connection.commit()

    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_requires_materialized_source_chain(tmp_path: Path) -> None:
    mutations = (
        (
            "missing-source-link",
            "UPDATE knowledge_documents SET source_asset_id=NULL "
            "WHERE id='document-missing-source-link'",
        ),
        (
            "missing-document-manifest",
            "UPDATE document_versions SET object_manifest_id=NULL "
            "WHERE id='document-version-missing-document-manifest'",
        ),
        (
            "missing-chunk-manifest",
            "UPDATE content_chunks SET object_manifest_id=NULL "
            "WHERE id='chunk-missing-chunk-manifest'",
        ),
        (
            "missing-document-resource",
            "DELETE FROM secured_resources "
            "WHERE id='document-missing-document-resource'",
        ),
        (
            "missing-source-resource",
            "DELETE FROM secured_resources "
            "WHERE id='source-asset-missing-source-resource'",
        ),
    )
    for suffix, mutation in mutations:
        connection = _database(tmp_path / suffix)
        _insert_story(connection, suffix=suffix)
        connection.execute(mutation)
        connection.commit()

        assert CloudRepository._project_story_context(
            connection, _identity(), project_id=PROJECT_ID
        ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_evidence_created_after_cutoff(
    tmp_path: Path,
) -> None:
    mutations = (
        (
            "future-document",
            "UPDATE knowledge_documents SET published_at='2099-01-01T00:00:00Z' "
            "WHERE id='document-future-document'",
        ),
        (
            "future-document-version",
            "UPDATE document_versions SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='document-version-future-document-version'",
        ),
        (
            "future-chunk",
            "UPDATE content_chunks SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='chunk-future-chunk'",
        ),
        (
            "future-fact",
            "UPDATE atomic_facts SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-future-fact'",
        ),
        (
            "future-source-manifest",
            "UPDATE object_manifests SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='source-manifest-future-source-manifest'",
        ),
        (
            "future-fact-manifest",
            "UPDATE object_manifests SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-manifest-future-fact-manifest'",
        ),
    )
    for suffix, mutation in mutations:
        connection = _database(tmp_path / suffix)
        _insert_story(connection, suffix=suffix)
        connection.execute(mutation)
        connection.commit()

        assert CloudRepository._project_story_context(
            connection, _identity(), project_id=PROJECT_ID
        ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_future_authority_graph_nodes_and_edges(
    tmp_path: Path,
) -> None:
    mutations = (
        (
            "future-story-source-set",
            "UPDATE source_sets SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='source-set-future-story-source-set'",
        ),
        (
            "future-story-member-added",
            "UPDATE source_set_members SET added_at='2099-01-01T00:00:00Z' "
            "WHERE id='source-member-future-story-member-added'",
        ),
        (
            "future-story-member-created",
            "UPDATE source_set_members SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='source-member-future-story-member-created'",
        ),
        (
            "future-fact-source-set",
            "UPDATE source_sets SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-source-set-future-fact-source-set'",
        ),
        (
            "future-fact-member-added",
            "UPDATE source_set_members SET added_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-source-member-future-fact-member-added'",
        ),
        (
            "future-fact-member-created",
            "UPDATE source_set_members SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-source-member-future-fact-member-created'",
        ),
        (
            "future-evidence-link",
            "UPDATE evidence_links SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='evidence-future-evidence-link'",
        ),
        (
            "future-source-resource",
            "UPDATE secured_resources SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='source-asset-future-source-resource'",
        ),
        (
            "future-document-resource",
            "UPDATE secured_resources SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='document-future-document-resource'",
        ),
        (
            "future-fact-resource",
            "UPDATE secured_resources SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-future-fact-resource'",
        ),
        (
            "future-story-resource",
            "UPDATE secured_resources SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='story-future-story-resource'",
        ),
        (
            "future-story-record",
            "UPDATE narrative_outputs SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='story-future-story-record'",
        ),
        (
            "future-confirmer",
            "UPDATE organization_memberships SET created_at='2099-01-01T00:00:00Z' "
            "WHERE id='membership-story-simulation'",
        ),
    )
    for suffix, mutation in mutations:
        connection = _database(tmp_path / suffix)
        _insert_story(connection, suffix=suffix)
        connection.execute(mutation)
        connection.commit()

        assert CloudRepository._project_story_context(
            connection, _identity(), project_id=PROJECT_ID
        ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_tampered_document_integrity_hash(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="document-integrity")
    connection.execute(
        "UPDATE document_versions SET integrity_hash='tampered' "
        "WHERE id='document-version-document-integrity'"
    )
    connection.commit()

    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_post_cutoff_mutation_of_authority_graph(
    tmp_path: Path,
) -> None:
    mutations = (
        (
            "updated-story-record",
            "UPDATE narrative_outputs SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='story-updated-story-record'",
        ),
        (
            "updated-story-resource",
            "UPDATE secured_resources SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='story-updated-story-resource'",
        ),
        (
            "updated-story-source-set",
            "UPDATE source_sets SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='source-set-updated-story-source-set'",
        ),
        (
            "updated-story-member",
            "UPDATE source_set_members SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='source-member-updated-story-member'",
        ),
        (
            "updated-fact",
            "UPDATE atomic_facts SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-updated-fact'",
        ),
        (
            "updated-fact-resource",
            "UPDATE secured_resources SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-updated-fact-resource'",
        ),
        (
            "updated-confirmer",
            "UPDATE organization_memberships SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='membership-story-simulation'",
        ),
        (
            "updated-fact-source-set",
            "UPDATE source_sets SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-source-set-updated-fact-source-set'",
        ),
        (
            "updated-fact-member",
            "UPDATE source_set_members SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='fact-source-member-updated-fact-member'",
        ),
        (
            "updated-source",
            "UPDATE source_assets SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='source-asset-updated-source'",
        ),
        (
            "updated-source-resource",
            "UPDATE secured_resources SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='source-asset-updated-source-resource'",
        ),
        (
            "updated-document",
            "UPDATE knowledge_documents SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='document-updated-document'",
        ),
        (
            "updated-document-resource",
            "UPDATE secured_resources SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='document-updated-document-resource'",
        ),
        (
            "updated-chunk",
            "UPDATE content_chunks SET updated_at='2099-01-01T00:00:00Z' "
            "WHERE id='chunk-updated-chunk'",
        ),
    )
    for suffix, mutation in mutations:
        connection = _database(tmp_path / suffix)
        _insert_story(connection, suffix=suffix)
        connection.execute(mutation)
        connection.commit()

        assert CloudRepository._project_story_context(
            connection, _identity(), project_id=PROJECT_ID
        ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_soft_deleted_or_expired_authority(
    tmp_path: Path,
) -> None:
    mutations = (
        (
            "deleted-story-resource",
            "UPDATE secured_resources SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='story-deleted-story-resource'",
        ),
        (
            "deleted-story-source-set",
            "UPDATE source_sets SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='source-set-deleted-story-source-set'",
        ),
        (
            "deleted-story-member",
            "UPDATE source_set_members SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='source-member-deleted-story-member'",
        ),
        (
            "deleted-story-manifest",
            "UPDATE object_manifests SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='manifest-deleted-story-manifest'",
        ),
        (
            "deleted-fact",
            "UPDATE atomic_facts SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='fact-deleted-fact'",
        ),
        (
            "deleted-fact-resource",
            "UPDATE secured_resources SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='fact-deleted-fact-resource'",
        ),
        (
            "deleted-fact-source-set",
            "UPDATE source_sets SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='fact-source-set-deleted-fact-source-set'",
        ),
        (
            "deleted-fact-member",
            "UPDATE source_set_members SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='fact-source-member-deleted-fact-member'",
        ),
        (
            "deleted-fact-manifest",
            "UPDATE object_manifests SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='fact-manifest-deleted-fact-manifest'",
        ),
        (
            "deleted-source-manifest",
            "UPDATE object_manifests SET lifecycle_state='deleted', "
            "deleted_at='2026-08-27T00:00:00Z' "
            "WHERE id='source-manifest-deleted-source-manifest'",
        ),
        (
            "expired-confirmer",
            "UPDATE organization_memberships SET expires_at='2026-08-26T11:00:00Z' "
            "WHERE id='membership-story-simulation'",
        ),
    )
    for suffix, mutation in mutations:
        connection = _database(tmp_path / suffix)
        _insert_story(connection, suffix=suffix)
        connection.execute(mutation)
        connection.commit()

        assert CloudRepository._project_story_context(
            connection, _identity(), project_id=PROJECT_ID
        ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_ignores_soft_deleted_story(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="deleted-story")
    connection.execute(
        "UPDATE narrative_outputs SET lifecycle_state='deleted', "
        "deleted_at='2026-08-27T00:00:00Z' "
        "WHERE id='story-deleted-story'"
    )
    connection.commit()

    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "not_available", "projectId": PROJECT_ID}


def test_authority_projection_rejects_tampered_source_receipt(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="source-tamper")
    receipt = canonical_json(
        {
            "schema": "yiyu.project-story-source-snapshot.v1",
            "projectId": PROJECT_ID,
            "sourceAssetId": "source-asset-source-tamper",
            "documentId": "document-source-tamper",
            "contentHash": sha256_text("unrelated-content"),
            "capturedAt": "2026-08-26T10:00:03Z",
        }
    )
    connection.execute(
        "UPDATE object_manifests SET receipt=?, receipt_hash=? "
        "WHERE id='source-manifest-source-tamper'",
        (receipt, sha256_text(receipt)),
    )
    connection.commit()

    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_cross_project_story_manifest_swap(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    _insert_story(
        connection,
        suffix="foreign",
        project_id="project-foreign",
        content_markdown="港货北上研究：香港商品北上渠道、选品及零售运营。",
    )
    _insert_story(connection, suffix="current")
    foreign = connection.execute(
        "SELECT object_manifest_id, content_hash FROM artifact_versions "
        "WHERE artifact_id='story-foreign'"
    ).fetchone()
    connection.execute(
        "UPDATE artifact_versions SET object_manifest_id=?, content_hash=? "
        "WHERE artifact_id='story-current'",
        (foreign["object_manifest_id"], foreign["content_hash"]),
    )
    connection.commit()

    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_compares_confirmer_expiry_as_real_time(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="offset-confirmer")
    connection.execute(
        "UPDATE organization_memberships "
        "SET expires_at='2026-08-26T13:00:00+14:00' "
        "WHERE id='membership-story-simulation'"
    )
    connection.commit()

    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_compares_source_set_expiry_as_real_time(
    tmp_path: Path,
) -> None:
    for suffix, source_set_id in (
        ("offset-story-source", "source-set-offset-story-source"),
        ("offset-fact-source", "fact-source-set-offset-fact-source"),
    ):
        connection = _database(tmp_path / suffix)
        _insert_story(connection, suffix=suffix)
        expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).astimezone(
            timezone(timedelta(hours=14))
        )
        connection.execute(
            "UPDATE source_sets SET expires_at=? WHERE id=?",
            (expired.isoformat(), source_set_id),
        )
        connection.commit()

        assert CloudRepository._project_story_context(
            connection, _identity(), project_id=PROJECT_ID
        ) == {"state": "invalid_authority", "projectId": PROJECT_ID}


def test_authority_projection_rejects_malformed_expiry_timestamp(
    tmp_path: Path,
) -> None:
    connection = _database(tmp_path)
    _insert_story(connection, suffix="malformed-expiry")
    connection.execute(
        "UPDATE source_sets SET expires_at='tomorrow-ish' "
        "WHERE id='source-set-malformed-expiry'"
    )
    connection.commit()

    assert CloudRepository._project_story_context(
        connection, _identity(), project_id=PROJECT_ID
    ) == {"state": "invalid_authority", "projectId": PROJECT_ID}
