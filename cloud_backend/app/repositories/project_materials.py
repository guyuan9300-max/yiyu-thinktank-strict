"""Project, material, import, and shared-knowledge authority operations.

This module is deliberately limited to the frozen strict-v2 authority objects.
It never reads a member source file and never returns ``source_locator`` or
``markdown_content``.  Renderer DTO adaptation belongs to the local UI domain.
"""

from __future__ import annotations

import sqlite3
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.security import payload_fingerprint

from ..repository import CloudRepository, RepositoryError, SessionIdentity


# Project-material knowledge kinds belong to this repository.  Keeping this
# domain list here prevents the 88-table runtime repository from depending on
# the frozen pre-blueprint repository module.
SHARED_KNOWLEDGE_DOCUMENT_KINDS = frozenset(
    {
        "shared_summary",
        "organization_shared_summary",
        "project_narrative",
        "report_summary",
        "evidence_summary",
    }
)

PROJECT_POLICY_SPEC_SCHEMA_VERSION = "gc02.client-access.v1"


def _bounded_project_lease(value: Any) -> str:
    """Project authorization projections may never outlive the 24h lease."""
    now = datetime.now(timezone.utc)
    maximum = now + timedelta(hours=24)
    raw = str(value or "").strip()
    try:
        candidate = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=timezone.utc)
        candidate = candidate.astimezone(timezone.utc)
    except ValueError:
        candidate = maximum
    return min(candidate, maximum).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def create_project_policy_version(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    project_id: str,
    now: str,
    version: int = 1,
) -> str:
    """Create one immutable client-access policy without copying members."""
    policy_id = new_id()
    policy_spec = canonical_json(
        {
            "allowedCapabilities": [
                "contributeKnowledge",
                "manageSharing",
                "read",
                "write",
            ],
            "defaultDecision": "deny",
            "grantAuthority": "object_grants",
            "policyKind": "client_access",
        }
    )
    connection.execute(
        """
        INSERT INTO policy_versions (
            id, scope_id, secured_resource_id, policy_scope_kind, version,
            policy_spec_schema_version, policy_spec, effective_at,
            created_at, lifecycle_state, updated_at, deleted_at
        ) VALUES (?, ?, ?, 'secured_resource', ?, ?, ?, ?, ?, 'active', ?, NULL)
        """,
        (
            policy_id,
            scope_id,
            project_id,
            version,
            PROJECT_POLICY_SPEC_SCHEMA_VERSION,
            policy_spec,
            now,
            now,
            now,
        ),
    )
    return policy_id


def _safe_summary_kind(value: Any) -> bool:
    kind = str(value or "").strip().lower()
    return kind in SHARED_KNOWLEDGE_DOCUMENT_KINDS or kind.endswith("_summary")


def _iso_week_ago() -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=7))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _official_website_url(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise RepositoryError(
            422,
            "project_official_website_invalid",
            "项目官网必须是完整的 http 或 https 地址",
        )
    normalized_path = parsed.path or "/"
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=normalized_path,
        fragment="",
    ).geturl()


class GC07ProjectMaterialsRepository:
    """The narrow GC-07 project/material authority over the 88-table schema."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _payload_hash(payload: Mapping[str, Any]) -> str:
        return payload_fingerprint(dict(payload))

    def _set_official_website_locator(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        project_id: str,
        official_website_url: str,
        now: str,
    ) -> None:
        source_id = self.repository._record_id(  # noqa: SLF001
            "source_asset",
            project_id,
            "official-website-root",
        )
        manifest_id = self.repository._record_id(  # noqa: SLF001
            "manifest",
            source_id,
            "official-website-locator",
        )
        existing = connection.execute(
            "SELECT source_locator_nonlocal, version, lifecycle_state "
            "FROM source_assets WHERE id=? AND scope_id=?",
            (source_id, identity.scope_id),
        ).fetchone()
        if not official_website_url:
            if existing is None or str(existing["lifecycle_state"]) != "active":
                return
            connection.execute(
                "UPDATE source_assets SET lifecycle_state='archived', archived_at=?, "
                "version=version+1, updated_at=? WHERE id=? AND scope_id=?",
                (now, now, source_id, identity.scope_id),
            )
            connection.execute(
                "UPDATE secured_resources SET lifecycle_state='archived', "
                "version=version+1, updated_at=? WHERE id=? AND scope_id=?",
                (now, source_id, identity.scope_id),
            )
            connection.execute(
                "UPDATE object_manifests SET lifecycle_state='archived' "
                "WHERE id=? AND scope_id=?",
                (manifest_id, identity.scope_id),
            )
            return
        if (
            existing is not None
            and str(existing["source_locator_nonlocal"] or "") == official_website_url
            and str(existing["lifecycle_state"]) == "active"
        ):
            return
        next_version = int(existing["version"] or 0) + 1 if existing else 1
        receipt = canonical_json(
            {
                "schema": "yiyu.project-official-website-locator.v1",
                "clientId": project_id,
                "url": official_website_url,
                "contentBoundary": "public_locator_only",
            }
        )
        receipt_hash = sha256_text(receipt)
        connection.execute(
            """
            INSERT INTO object_manifests (
                id, scope_id, storage_key, content_hash, lifecycle_state,
                receipt, holder_role, holder_instance_id, storage_kind,
                byte_size, media_type, availability_state, receipt_hash,
                created_at, verified_at, deleted_at, authority_role,
                origin_instance_id
            ) VALUES (?, ?, NULL, NULL, 'active', ?, 'organization_cloud', ?,
                      'public_web_locator', ?, 'application/json', 'registered', ?,
                      ?, ?, NULL, 'cloud', ?)
            ON CONFLICT(id) DO UPDATE SET receipt=excluded.receipt,
                lifecycle_state='active', receipt_hash=excluded.receipt_hash,
                byte_size=excluded.byte_size, availability_state='registered',
                verified_at=excluded.verified_at, deleted_at=NULL
            """,
            (
                manifest_id,
                identity.scope_id,
                receipt,
                self.repository.cloud_instance_id,
                len(receipt.encode("utf-8")),
                receipt_hash,
                now,
                now,
                self.repository.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO secured_resources (
                id, scope_id, resource_kind, lifecycle_state, version,
                resource_type_key, created_at, updated_at, deleted_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, 'source_asset', 'active', ?,
                      'official_website_locator', ?, ?, NULL, 'cloud', ?)
            ON CONFLICT(id) DO UPDATE SET lifecycle_state='active',
                version=excluded.version, updated_at=excluded.updated_at,
                deleted_at=NULL
            """,
            (
                source_id,
                identity.scope_id,
                next_version,
                now,
                now,
                self.repository.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO source_assets (
                id, scope_id, client_id, object_manifest_id, content_hash,
                record_kind, source_kind, display_name, media_type, byte_size,
                source_locator_nonlocal, parent_folder_id, asset_id, folder_id,
                created_by_membership_id, availability_state, archived_at,
                version, lifecycle_state, created_at, updated_at, deleted_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, NULL, 'asset', 'official_website',
                      '项目官网', 'text/html', NULL, ?, NULL, NULL, NULL, ?,
                      'registered', NULL, ?, 'active', ?, ?, NULL, 'cloud', ?)
            ON CONFLICT(id) DO UPDATE SET object_manifest_id=excluded.object_manifest_id,
                source_locator_nonlocal=excluded.source_locator_nonlocal,
                availability_state='registered', archived_at=NULL,
                version=excluded.version, lifecycle_state='active',
                updated_at=excluded.updated_at, deleted_at=NULL
            """,
            (
                source_id,
                identity.scope_id,
                project_id,
                manifest_id,
                official_website_url,
                identity.membership_id,
                next_version,
                now,
                now,
                self.repository.cloud_instance_id,
            ),
        )

    @staticmethod
    def _receipt(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT i.payload_hash, i.result_hash, m.receipt
            FROM idempotency_records AS i
            LEFT JOIN object_manifests AS m
              ON m.id = i.result_object_manifest_id
            WHERE i.scope_id=? AND i.idempotency_key=?
            """,
            (identity.scope_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"] or "") != payload_hash:
            raise RepositoryError(409, "idempotency_conflict", "操作标识冲突")
        receipt = str(row["receipt"] or "")
        if not receipt or sha256_text(receipt) != str(row["result_hash"] or ""):
            raise RepositoryError(500, "idempotency_receipt_invalid", "操作回执无法校验")
        value = json.loads(receipt)
        if not isinstance(value, dict):
            raise RepositoryError(500, "idempotency_receipt_invalid", "操作回执结构无效")
        return value

    @staticmethod
    def _record_command(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
        payload_hash: str,
        command_type: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        expected_aggregate_version: int | None,
        result: Mapping[str, Any],
        target_resource_id: str,
    ) -> tuple[str, str]:
        now = utc_now()
        operation_id = new_id()
        result_json = canonical_json(dict(result))
        result_hash = sha256_text(result_json)
        result_manifest_id = new_id()
        connection.execute(
            """
            INSERT INTO object_manifests (
                id, scope_id, storage_key, content_hash, lifecycle_state,
                receipt, holder_role, holder_instance_id, storage_kind,
                byte_size, media_type, availability_state, receipt_hash,
                created_at, verified_at, deleted_at, authority_role,
                origin_instance_id
            ) VALUES (?, ?, NULL, ?, 'active', ?, 'organization_cloud', ?,
                      'command_receipt', ?, 'application/json', 'ready', ?,
                      ?, ?, NULL, 'cloud', ?)
            """,
            (
                result_manifest_id,
                identity.scope_id,
                result_hash,
                result_json,
                identity.cloud_instance_id,
                len(result_json.encode("utf-8")),
                result_hash,
                now,
                now,
                identity.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO idempotency_records (
                id, scope_id, idempotency_key, payload_hash, result_hash,
                expires_at, result_object_manifest_id, status, created_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, '9999-12-31T23:59:59.999Z', ?,
                      'completed', ?, 'cloud', ?)
            """,
            (
                new_id(),
                identity.scope_id,
                idempotency_key,
                payload_hash,
                result_hash,
                result_manifest_id,
                now,
                identity.cloud_instance_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO commands (
                id, scope_id, operation_id, idempotency_key, aggregate_type,
                aggregate_id, command_type, actor_principal_id,
                expected_aggregate_version, device_command_sequence, status,
                actor_membership_id, payload_object_manifest_id, payload_hash,
                submitted_at, settled_at, authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'committed', ?,
                      NULL, ?, ?, ?, 'cloud', ?)
            """,
            (
                new_id(),
                identity.scope_id,
                operation_id,
                idempotency_key,
                aggregate_type,
                aggregate_id,
                command_type,
                identity.principal_id,
                expected_aggregate_version,
                identity.membership_id,
                payload_hash,
                now,
                now,
                identity.cloud_instance_id,
            ),
        )
        event_hash = sha256_text(
            canonical_json(
                {
                    "operationId": operation_id,
                    "commandType": command_type,
                    "aggregateId": aggregate_id,
                    "aggregateVersion": aggregate_version,
                }
            )
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, scope_id, operation_id, actor_id, action, event_hash,
                actor_membership_id, target_resource_id,
                details_object_manifest_id, occurred_at, origin_instance_id,
                created_at, integrity_hash, authority_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 'cloud')
            """,
            (
                new_id(),
                identity.scope_id,
                operation_id,
                identity.principal_id,
                command_type,
                event_hash,
                identity.membership_id,
                target_resource_id,
                now,
                identity.cloud_instance_id,
                now,
                event_hash,
            ),
        )
        connection.execute(
            """
            INSERT INTO outbox_events (
                id, scope_id, operation_id, aggregate_version, event_type,
                status, aggregate_type, aggregate_id,
                event_object_manifest_id, event_hash, available_at,
                published_at, authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, NULL, ?, ?, NULL,
                      'cloud', ?)
            """,
            (
                new_id(),
                identity.scope_id,
                operation_id,
                aggregate_version,
                command_type,
                aggregate_type,
                aggregate_id,
                event_hash,
                now,
                identity.cloud_instance_id,
            ),
        )
        return operation_id, result_manifest_id

    @staticmethod
    def _invalidate_revoked_member_derivatives(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        project_id: str,
        project_version: int,
        policy_version_id: str,
        removed_grants: list[sqlite3.Row],
        now: str,
    ) -> dict[str, int]:
        """Invalidate only derivatives traceable to the revoked memberships."""

        if not removed_grants:
            return {
                "viewerProjections": 0,
                "lineages": 0,
                "searchIndexes": 0,
                "vectorIndexes": 0,
                "aiContexts": 0,
                "cacheEntries": 0,
                "exportGrants": 0,
            }
        membership_ids = sorted(
            {str(row["subject_membership_id"]) for row in removed_grants}
        )
        placeholders = ",".join("?" for _ in membership_ids)
        viewer_rows = connection.execute(
            "SELECT id FROM viewer_projections WHERE scope_id=? "
            "AND secured_resource_id=? "
            f"AND viewer_membership_id IN ({placeholders})",
            (identity.scope_id, project_id, *membership_ids),
        ).fetchall()
        viewer_ids = {str(row["id"]) for row in viewer_rows}
        viewer_count = int(
            connection.execute(
                "UPDATE viewer_projections SET invalidated_at=? WHERE scope_id=? "
                "AND secured_resource_id=? "
                f"AND viewer_membership_id IN ({placeholders}) "
                "AND invalidated_at IS NULL",
                (now, identity.scope_id, project_id, *membership_ids),
            ).rowcount
            or 0
        )
        revoked_grant_ids: set[str] = set()
        for grant in removed_grants:
            grant_id = str(grant["id"])
            revoked_grant_ids.add(grant_id)
            lineage_id = "lineage_" + sha256_text(
                f"gc02.project_access_revoked\x1f{identity.scope_id}\x1f{grant_id}"
            )[:30]
            connection.execute(
                "INSERT OR IGNORE INTO derivation_lineage (id,scope_id,source_set_id,"
                "policy_version_id,grant_generation,derivative_kind,derivative_object_id,"
                "generator_version,generated_at,invalidated_at,source_version,authority_role,"
                "origin_instance_id) VALUES (?,?,NULL,?,?, 'project_access_grant',?,"
                "'gc02-project-revoke-v1',?,?,?,'cloud',?)",
                (
                    lineage_id,
                    identity.scope_id,
                    policy_version_id,
                    int(grant["grant_generation"] or 1),
                    grant_id,
                    now,
                    now,
                    project_version,
                    identity.cloud_instance_id,
                ),
            )
        derivative_ids = viewer_ids | revoked_grant_ids
        lineage_ids: set[str] = set()
        if derivative_ids:
            derivative_placeholders = ",".join("?" for _ in derivative_ids)
            lineage_ids = {
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM derivation_lineage WHERE scope_id=? "
                    f"AND derivative_object_id IN ({derivative_placeholders})",
                    (identity.scope_id, *sorted(derivative_ids)),
                ).fetchall()
            }
        lineage_count = 0
        search_count = 0
        vector_count = 0
        context_count = 0
        cache_count = 0
        if lineage_ids:
            lineage_placeholders = ",".join("?" for _ in lineage_ids)
            lineage_count = int(
                connection.execute(
                    "UPDATE derivation_lineage SET invalidated_at=? WHERE scope_id=? "
                    f"AND id IN ({lineage_placeholders}) AND invalidated_at IS NULL",
                    (now, identity.scope_id, *sorted(lineage_ids)),
                ).rowcount
                or 0
            )
            search_count = int(
                connection.execute(
                    "UPDATE search_index_manifests SET status='invalidated',invalidated_at=? "
                    "WHERE scope_id=? "
                    f"AND lineage_id IN ({lineage_placeholders}) AND invalidated_at IS NULL",
                    (now, identity.scope_id, *sorted(lineage_ids)),
                ).rowcount
                or 0
            )
            vector_count = int(
                connection.execute(
                    "UPDATE vector_index_manifests SET status='invalidated',invalidated_at=? "
                    "WHERE scope_id=? "
                    f"AND lineage_id IN ({lineage_placeholders}) AND invalidated_at IS NULL",
                    (now, identity.scope_id, *sorted(lineage_ids)),
                ).rowcount
                or 0
            )
            context_count = int(
                connection.execute(
                    "UPDATE ai_context_manifests SET status='invalidated',invalidated_at=? "
                    "WHERE scope_id=? "
                    f"AND lineage_id IN ({lineage_placeholders}) AND invalidated_at IS NULL",
                    (now, identity.scope_id, *sorted(lineage_ids)),
                ).rowcount
                or 0
            )
            cache_count = int(
                connection.execute(
                    "UPDATE cache_entries SET invalidated_at=? WHERE scope_id=? "
                    f"AND lineage_id IN ({lineage_placeholders}) AND invalidated_at IS NULL",
                    (now, identity.scope_id, *sorted(lineage_ids)),
                ).rowcount
                or 0
            )
        export_count = int(
            connection.execute(
                "UPDATE export_grants SET status='revoked',revoked_at=?,version=version+1,"
                "updated_at=? WHERE scope_id=? "
                f"AND grantee_membership_id IN ({placeholders}) AND status='active' "
                "AND lifecycle_state='active' AND ("
                "source_set_id IN (SELECT id FROM source_sets WHERE scope_id=? AND client_id=?)"
                + (
                    f" OR lineage_id IN ({','.join('?' for _ in lineage_ids)})"
                    if lineage_ids
                    else ""
                )
                + ")",
                (
                    now,
                    now,
                    identity.scope_id,
                    *membership_ids,
                    identity.scope_id,
                    project_id,
                    *sorted(lineage_ids),
                ),
            ).rowcount
            or 0
        )
        return {
            "viewerProjections": viewer_count,
            "lineages": lineage_count,
            "searchIndexes": search_count,
            "vectorIndexes": vector_count,
            "aiContexts": context_count,
            "cacheEntries": cache_count,
            "exportGrants": export_count,
        }
    @staticmethod
    def _project_authorization_projection(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        project: sqlite3.Row,
    ) -> dict[str, Any]:
        project_id = str(project["id"])
        policy = connection.execute(
            """
            SELECT *
            FROM policy_versions
            WHERE scope_id=? AND secured_resource_id=?
              AND lifecycle_state='active'
            ORDER BY version DESC, created_at DESC, id DESC
            LIMIT 1
            """,
            (identity.scope_id, project_id),
        ).fetchone()
        if policy is None:
            raise RepositoryError(
                409,
                "project_policy_projection_missing",
                "项目权限版本尚未形成，请稍后重试",
            )
        grant = connection.execute(
            """
            SELECT *
            FROM object_grants
            WHERE scope_id=? AND secured_resource_id=?
              AND subject_membership_id=? AND status='active'
              AND lifecycle_state='active' AND policy_version_id=?
            ORDER BY grant_generation DESC, updated_at DESC, id DESC
            LIMIT 1
            """,
            (
                identity.scope_id,
                project_id,
                identity.membership_id,
                policy["id"],
            ),
        ).fetchone()
        is_manager = identity.is_admin or str(
            project["owner_membership_id"] or ""
        ) == identity.membership_id
        try:
            grant_capabilities = (
                json.loads(str(grant["capability_set"] or "{}"))
                if grant is not None
                else {}
            )
        except json.JSONDecodeError:
            grant_capabilities = {}
        capability_map = {
            "read": bool(is_manager or grant_capabilities.get("read")),
            "write": bool(is_manager or grant_capabilities.get("write")),
            "contributeKnowledge": bool(
                is_manager
                or grant_capabilities.get("contributeKnowledge")
                or grant_capabilities.get("write")
            ),
            "manageSharing": bool(
                is_manager or grant_capabilities.get("manageSharing")
            ),
        }
        if not capability_map["read"]:
            raise RepositoryError(404, "project_missing", "当前成员无法访问该项目")
        session = connection.execute(
            "SELECT lease_expires_at FROM sandboxes WHERE id=? "
            "AND lifecycle_state='active'",
            (identity.session_id,),
        ).fetchone()
        lease_expires_at = _bounded_project_lease(
            session["lease_expires_at"] if session is not None else None
        )
        try:
            policy_spec = json.loads(str(policy["policy_spec"] or "{}"))
        except json.JSONDecodeError:
            policy_spec = {}
        generated_at = utc_now()
        return {
            "viewerPrincipalId": identity.principal_id,
            "viewerMembershipId": identity.membership_id,
            "policyVersionId": str(policy["id"]),
            "policyVersion": int(policy["version"] or 1),
            "policySpecSchemaVersion": str(
                policy["policy_spec_schema_version"]
                or PROJECT_POLICY_SPEC_SCHEMA_VERSION
            ),
            "policySpec": policy_spec,
            "viewerSurfaces": [
                "project_workspace",
                "strategic_accompaniment",
                "task_project_context",
            ],
            "viewerCapabilities": [
                name for name, enabled in capability_map.items() if enabled
            ],
            "leaseExpiresAt": lease_expires_at,
            "generatedAt": generated_at,
            "sourceVersion": max(
                int(project["version"] or 1),
                int(policy["version"] or 1),
                int(grant["grant_generation"] or 1) if grant is not None else 1,
            ),
        }

    @staticmethod
    def _project_payload(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        project_id = str(row["id"])
        participants = {
            str(item["subject_membership_id"])
            for item in connection.execute(
                """
                SELECT subject_membership_id
                FROM object_grants
                WHERE scope_id=? AND secured_resource_id=?
                  AND status='active' AND lifecycle_state='active'
                  AND subject_membership_id IS NOT NULL
                """,
                (identity.scope_id, project_id),
            ).fetchall()
        }
        if row["owner_membership_id"]:
            participants.add(str(row["owner_membership_id"]))
        document_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM source_assets
            WHERE scope_id=? AND client_id=? AND record_kind='asset'
              AND lifecycle_state='active'
              AND NOT (
                  source_kind='official_website'
                  AND availability_state='registered'
              )
            """,
            (identity.scope_id, project_id),
        ).fetchone()["count"]
        task_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM tasks
            WHERE scope_id=? AND client_id=? AND lifecycle_state!='deleted'
            """,
            (identity.scope_id, project_id),
        ).fetchone()["count"]
        official_source = connection.execute(
            """
            SELECT source_assets.source_locator_nonlocal
            FROM source_assets
            JOIN object_manifests
              ON object_manifests.id=source_assets.object_manifest_id
             AND object_manifests.scope_id=source_assets.scope_id
            WHERE source_assets.scope_id=? AND source_assets.client_id=?
              AND source_assets.record_kind='asset'
              AND source_assets.source_kind='official_website'
              AND source_assets.lifecycle_state='active'
            ORDER BY CASE WHEN object_manifests.storage_kind='public_web_locator'
                          THEN 0 ELSE 1 END,
                     source_assets.updated_at DESC, source_assets.id
            LIMIT 1
            """,
            (identity.scope_id, project_id),
        ).fetchone()
        return {
            "projectId": project_id,
            "ownerMembershipId": (
                str(row["owner_membership_id"])
                if row["owner_membership_id"]
                else None
            ),
            "name": str(row["name"] or ""),
            "alias": str(row["alias"] or ""),
            "summary": str(row["summary"] or ""),
            "domain": str(row["domain"] or "项目"),
            "color": str(row["color"] or "#5B7BFE"),
            "isDefaultInternalProject": bool(row["is_default_internal"]),
            "lifecycleState": str(row["lifecycle_state"]),
            "version": int(row["version"] or 1),
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
            "participantMembershipIds": sorted(participants),
            "documentCount": int(document_count),
            "taskCount": int(task_count),
            "folderState": "local_only",
            "officialWebsiteUrl": (
                str(official_source["source_locator_nonlocal"])
                if official_source is not None
                else None
            ),
            "authorizationProjection": (
                GC07ProjectMaterialsRepository._project_authorization_projection(
                    connection,
                    identity,
                    project=row,
                )
            ),
        }

    def list_projects(self, identity: SessionIdentity) -> dict[str, Any]:
        with self.repository._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM clients
                WHERE scope_id=? AND lifecycle_state!='deleted'
                ORDER BY updated_at DESC, id
                """,
                (identity.scope_id,),
            ).fetchall()
            projects = []
            for row in rows:
                try:
                    self.repository._require_project_access(
                        connection,
                        identity,
                        project_id=str(row["id"]),
                    )
                except RepositoryError as exc:
                    if exc.status_code == 404:
                        continue
                    raise
                projects.append(self._project_payload(connection, identity, row))
        return {"projects": projects, "generatedAt": utc_now()}

    def project_detail(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            row = self.repository._require_project_access(
                connection,
                identity,
                project_id=project_id,
            )
            project = self._project_payload(connection, identity, row)
        return {"project": project}

    @staticmethod
    def _glossary_entity_payload(row: sqlite3.Row) -> dict[str, Any] | None:
        """Adapt a confirmed glossary row without inventing entity semantics."""
        receipt: dict[str, Any] = {}
        try:
            parsed = json.loads(str(row["definition_receipt"] or "{}"))
            if isinstance(parsed, dict):
                receipt = parsed
        except (TypeError, ValueError):
            pass
        entity_type = str(
            receipt.get("entityType") or receipt.get("entity_type") or ""
        ).strip()
        allowed_types = {
            "person",
            "company",
            "project",
            "product",
            "competitor",
            "amount",
            "date",
        }
        # glossary_entities also stores ordinary terminology.  Only rows that
        # explicitly declare an entity type belong on the entity panel.
        if entity_type not in allowed_types:
            return None
        aliases: list[str] = []
        try:
            parsed_aliases = json.loads(str(row["aliases"] or "[]"))
            if isinstance(parsed_aliases, list):
                aliases = [
                    str(value).strip()
                    for value in parsed_aliases
                    if str(value or "").strip()
                ]
        except (TypeError, ValueError):
            pass
        attributes = receipt.get("attributes")
        if not isinstance(attributes, dict):
            attributes = {}
        preferred = str(row["preferred_term"] or "").strip()
        if not preferred:
            return None
        return {
            "id": str(row["id"]),
            "clientId": str(row["client_id"]),
            "entityType": entity_type,
            "normalizedName": preferred.casefold(),
            "displayName": preferred,
            "aliases": aliases,
            "attributes": {
                str(key): str(value)
                for key, value in attributes.items()
                if value is not None
            },
            "mentionCount": max(0, int(receipt.get("mentionCount") or 0)),
            "confidence": max(0.0, min(1.0, float(receipt.get("confidence") or 0))),
            "firstSeenAt": str(receipt.get("firstSeenAt") or row["created_at"]),
            "lastSeenAt": str(receipt.get("lastSeenAt") or row["updated_at"]),
            "status": "active",
        }

    def list_entities(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        entity_type: str = "",
        query: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
        with self.repository._connection() as connection:
            self.repository._require_project_access(
                connection,
                identity,
                project_id=project_id,
            )
            rows = connection.execute(
                """
                SELECT g.*,s.client_id,m.receipt AS definition_receipt
                FROM glossary_entities AS g
                JOIN source_sets AS s
                  ON s.scope_id=g.scope_id AND s.id=g.source_set_id
                LEFT JOIN object_manifests AS m
                  ON m.scope_id=g.scope_id AND m.id=g.definition_object_manifest_id
                WHERE g.scope_id=? AND s.client_id=?
                  AND g.lifecycle_state='active'
                  AND s.lifecycle_state='active'
                  AND g.verification_state IN ('confirmed','accepted','canonical')
                ORDER BY g.updated_at DESC,g.id
                """,
                (identity.scope_id, project_id),
            ).fetchall()
        values = [
            value
            for row in rows
            if (value := self._glossary_entity_payload(row)) is not None
        ]
        normalized_query = query.strip().casefold()
        if entity_type:
            values = [item for item in values if item["entityType"] == entity_type]
        if normalized_query:
            values = [
                item
                for item in values
                if normalized_query in str(item["displayName"]).casefold()
                or any(
                    normalized_query in str(alias).casefold()
                    for alias in item["aliases"]
                )
            ]
        total = len(values)
        return {"entities": values[safe_offset : safe_offset + safe_limit], "total": total}

    def entity_merge_candidates(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        entities = self.list_entities(
            identity,
            project_id=project_id,
            limit=500,
        )["entities"]
        candidates: list[dict[str, Any]] = []
        for index, left in enumerate(entities):
            for right in entities[index + 1 :]:
                if left["entityType"] != right["entityType"]:
                    continue
                similarity = SequenceMatcher(
                    None,
                    str(left["normalizedName"]),
                    str(right["normalizedName"]),
                ).ratio()
                alias_overlap = bool(
                    {str(value).casefold() for value in left["aliases"]}
                    & {str(value).casefold() for value in right["aliases"]}
                )
                if similarity < 0.82 and not alias_overlap:
                    continue
                candidates.append(
                    {
                        "entityAId": left["id"],
                        "entityBId": right["id"],
                        "entityType": left["entityType"],
                        "nameA": left["displayName"],
                        "nameB": right["displayName"],
                        "mentionCountA": left["mentionCount"],
                        "mentionCountB": right["mentionCount"],
                        "similarity": round(similarity, 4),
                        "reason": "名称相似" if not alias_overlap else "别名重合",
                    }
                )
        candidates.sort(key=lambda item: (-item["similarity"], item["entityAId"]))
        return {"candidates": candidates[: max(1, min(int(limit), 200))]}

    @staticmethod
    def _glossary_payload(row: sqlite3.Row) -> dict[str, Any]:
        details: dict[str, Any] = {}
        try:
            parsed = json.loads(str(row["definition_receipt"] or "{}"))
            if isinstance(parsed, dict):
                details = parsed
        except (TypeError, ValueError):
            pass
        try:
            parsed_aliases = json.loads(str(row["aliases"] or "[]"))
            aliases = parsed_aliases if isinstance(parsed_aliases, list) else []
        except (TypeError, ValueError):
            aliases = []
        term = str(row["preferred_term"] or "")
        return {
            "id": str(row["id"]),
            "clientId": str(row["client_id"]),
            "term": term,
            "normalizedTerm": term.casefold(),
            "definition": str(details.get("definition") or ""),
            "aliases": [str(value) for value in aliases],
            "category": str(details.get("category") or "project_term"),
            "createdAt": str(row["created_at"]),
            "updatedAt": str(row["updated_at"]),
            "version": int(row["version"] or 1),
        }

    def _project_glossary_source_set(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        project_id: str,
        now: str,
    ) -> str:
        source_set_id = self.repository._record_id(  # noqa: SLF001
            "source_set", project_id, "project-glossary"
        )
        connection.execute(
            """
            INSERT INTO source_sets (
                id,scope_id,client_id,security_label_set_version,source_count,
                version,purpose_kind,publication_state,created_by_principal_id,
                created_at,expires_at,lifecycle_state,updated_at,deleted_at,
                authority_role,origin_instance_id
            ) VALUES (?,?,?,'gc02.client-access.v1',0,1,'project_glossary',
                      'published',?,?,NULL,'active',?,NULL,'cloud',?)
            ON CONFLICT(id) DO UPDATE SET lifecycle_state='active',
                updated_at=excluded.updated_at,deleted_at=NULL
            """,
            (
                source_set_id,
                identity.scope_id,
                project_id,
                identity.principal_id,
                now,
                now,
                identity.cloud_instance_id,
            ),
        )
        return source_set_id

    def _glossary_definition_manifest(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        entry_id: str,
        version: int,
        definition: str,
        category: str,
        now: str,
    ) -> str:
        manifest_id = self.repository._record_id(  # noqa: SLF001
            "manifest", entry_id, f"definition-v{version}"
        )
        receipt = canonical_json(
            {
                "schema": "yiyu.project-glossary-definition.v1",
                "definition": definition,
                "category": category,
            }
        )
        receipt_hash = sha256_text(receipt)
        connection.execute(
            """
            INSERT INTO object_manifests (
                id,scope_id,storage_key,content_hash,lifecycle_state,receipt,
                holder_role,holder_instance_id,storage_kind,byte_size,media_type,
                availability_state,receipt_hash,created_at,verified_at,deleted_at,
                authority_role,origin_instance_id
            ) VALUES (?,?,NULL,?,'active',?,'organization_cloud',?,
                      'project_glossary_definition',?,'application/json','ready',
                      ?,?,?,NULL,'cloud',?)
            """,
            (
                manifest_id,
                identity.scope_id,
                receipt_hash,
                receipt,
                identity.cloud_instance_id,
                len(receipt.encode("utf-8")),
                receipt_hash,
                now,
                now,
                identity.cloud_instance_id,
            ),
        )
        return manifest_id

    def list_glossary(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        query: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            self.repository._require_project_access(connection, identity, project_id=project_id)
            rows = connection.execute(
                """
                SELECT g.*,s.client_id,m.receipt AS definition_receipt
                FROM glossary_entities AS g
                JOIN source_sets AS s ON s.scope_id=g.scope_id AND s.id=g.source_set_id
                LEFT JOIN object_manifests AS m
                  ON m.scope_id=g.scope_id AND m.id=g.definition_object_manifest_id
                WHERE g.scope_id=? AND s.client_id=?
                  AND g.lifecycle_state='active' AND s.lifecycle_state='active'
                ORDER BY g.updated_at DESC,g.id
                """,
                (identity.scope_id, project_id),
            ).fetchall()
        entries = [self._glossary_payload(row) for row in rows]
        normalized = query.strip().casefold()
        if normalized:
            entries = [
                entry
                for entry in entries
                if normalized in entry["term"].casefold()
                or normalized in entry["definition"].casefold()
                or any(normalized in alias.casefold() for alias in entry["aliases"])
            ]
        total = len(entries)
        start = max(0, int(offset))
        return {"entries": entries[start : start + max(1, min(int(limit), 500))], "total": total}

    def create_glossary_entry(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        term = str(payload.get("term") or "").strip()
        if not term:
            raise RepositoryError(422, "glossary_term_required", "请输入术语")
        aliases = sorted({str(v).strip() for v in payload.get("aliases") or [] if str(v or "").strip()})
        normalized = {
            "projectId": project_id,
            "term": term,
            "definition": str(payload.get("definition") or "").strip(),
            "aliases": aliases,
            "category": str(payload.get("category") or "project_term").strip() or "project_term",
        }
        payload_hash = self._payload_hash(normalized)
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self.repository._require_project_access(connection, identity, project_id=project_id, capability="knowledge_write")
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                source_set_id = self._project_glossary_source_set(connection, identity, project_id=project_id, now=utc_now())
                duplicate = connection.execute(
                    "SELECT id FROM glossary_entities WHERE scope_id=? AND source_set_id=? "
                    "AND lower(preferred_term)=lower(?) AND lifecycle_state='active'",
                    (identity.scope_id, source_set_id, term),
                ).fetchone()
                if duplicate is not None:
                    raise RepositoryError(409, "glossary_term_exists", "该项目已有同名术语")
                entry_id, now = new_id(), utc_now()
                manifest_id = self._glossary_definition_manifest(
                    connection, identity, entry_id=entry_id, version=1,
                    definition=normalized["definition"], category=normalized["category"], now=now,
                )
                connection.execute(
                    """
                    INSERT INTO glossary_entities (
                        id,scope_id,source_set_id,preferred_term,aliases,version,
                        aliases_schema_version,definition_object_manifest_id,
                        verification_state,confirmed_by_membership_id,created_at,
                        lifecycle_state,updated_at,deleted_at,authority_role,origin_instance_id
                    ) VALUES (?,?,?,?,?,1,'yiyu.aliases.v1',?,'confirmed',?,?,
                              'active',?,NULL,'cloud',?)
                    """,
                    (entry_id, identity.scope_id, source_set_id, term,
                     canonical_json(aliases), manifest_id, identity.membership_id,
                     now, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "UPDATE source_sets SET source_count=source_count+1,version=version+1,updated_at=? WHERE id=? AND scope_id=?",
                    (now, source_set_id, identity.scope_id),
                )
                row = connection.execute(
                    "SELECT g.*,s.client_id,m.receipt AS definition_receipt FROM glossary_entities g "
                    "JOIN source_sets s ON s.scope_id=g.scope_id AND s.id=g.source_set_id "
                    "LEFT JOIN object_manifests m ON m.scope_id=g.scope_id AND m.id=g.definition_object_manifest_id "
                    "WHERE g.id=? AND g.scope_id=?",
                    (entry_id, identity.scope_id),
                ).fetchone()
                result = {"entry": self._glossary_payload(row)}
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="project_glossary.created",
                    aggregate_type="glossary_entity", aggregate_id=entry_id,
                    aggregate_version=1, expected_aggregate_version=None,
                    result=result, target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def update_glossary_entry(
        self,
        identity: SessionIdentity,
        *,
        entry_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_request = {
            key: payload.get(key)
            for key in ("term", "definition", "aliases", "category", "expectedVersion")
            if key in payload
        }
        payload_hash = self._payload_hash({"entryId": entry_id, **normalized_request})
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT g.*,s.client_id,m.receipt AS definition_receipt FROM glossary_entities g "
                    "JOIN source_sets s ON s.scope_id=g.scope_id AND s.id=g.source_set_id "
                    "LEFT JOIN object_manifests m ON m.scope_id=g.scope_id AND m.id=g.definition_object_manifest_id "
                    "WHERE g.id=? AND g.scope_id=? AND g.lifecycle_state='active'",
                    (entry_id, identity.scope_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "glossary_entry_missing", "术语不存在")
                project_id = str(row["client_id"])
                self.repository._require_project_access(connection, identity, project_id=project_id, capability="knowledge_write")
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                current = self._glossary_payload(row)
                expected = int(payload.get("expectedVersion") or current["version"])
                if expected != current["version"]:
                    raise RepositoryError(409, "glossary_version_conflict", "术语已更新，请刷新后重试")
                term = str(payload.get("term") if "term" in payload else current["term"]).strip()
                if not term:
                    raise RepositoryError(422, "glossary_term_required", "请输入术语")
                aliases = current["aliases"] if "aliases" not in payload else sorted(
                    {str(v).strip() for v in payload.get("aliases") or [] if str(v or "").strip()}
                )
                definition = str(payload.get("definition") if "definition" in payload else current["definition"]).strip()
                category = str(payload.get("category") if "category" in payload else current["category"]).strip() or "project_term"
                version, now = current["version"] + 1, utc_now()
                manifest_id = self._glossary_definition_manifest(
                    connection, identity, entry_id=entry_id, version=version,
                    definition=definition, category=category, now=now,
                )
                connection.execute(
                    "UPDATE glossary_entities SET preferred_term=?,aliases=?,version=?,"
                    "definition_object_manifest_id=?,verification_state='confirmed',"
                    "confirmed_by_membership_id=?,updated_at=? WHERE id=? AND scope_id=? AND version=?",
                    (term, canonical_json(aliases), version, manifest_id,
                     identity.membership_id, now, entry_id, identity.scope_id, current["version"]),
                )
                updated = connection.execute(
                    "SELECT g.*,s.client_id,m.receipt AS definition_receipt FROM glossary_entities g "
                    "JOIN source_sets s ON s.scope_id=g.scope_id AND s.id=g.source_set_id "
                    "LEFT JOIN object_manifests m ON m.scope_id=g.scope_id AND m.id=g.definition_object_manifest_id "
                    "WHERE g.id=? AND g.scope_id=?",
                    (entry_id, identity.scope_id),
                ).fetchone()
                result = {"entry": self._glossary_payload(updated)}
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="project_glossary.updated",
                    aggregate_type="glossary_entity", aggregate_id=entry_id,
                    aggregate_version=version, expected_aggregate_version=expected,
                    result=result, target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def delete_glossary_entry(
        self,
        identity: SessionIdentity,
        *,
        entry_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload_hash = self._payload_hash({"entryId": entry_id})
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT g.*,s.client_id FROM glossary_entities g JOIN source_sets s "
                    "ON s.scope_id=g.scope_id AND s.id=g.source_set_id WHERE g.id=? AND g.scope_id=?",
                    (entry_id, identity.scope_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "glossary_entry_missing", "术语不存在")
                project_id = str(row["client_id"])
                self.repository._require_project_access(connection, identity, project_id=project_id, capability="knowledge_write")
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                version = int(row["version"] or 1) + 1
                now = utc_now()
                if str(row["lifecycle_state"]) != "deleted":
                    connection.execute(
                        "UPDATE glossary_entities SET lifecycle_state='deleted',deleted_at=?,"
                        "version=?,updated_at=? WHERE id=? AND scope_id=?",
                        (now, version, now, entry_id, identity.scope_id),
                    )
                    connection.execute(
                        "UPDATE source_sets SET source_count=MAX(0,source_count-1),version=version+1,updated_at=? "
                        "WHERE id=? AND scope_id=?",
                        (now, row["source_set_id"], identity.scope_id),
                    )
                result = {"status": "deleted", "id": entry_id, "version": version}
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="project_glossary.deleted",
                    aggregate_type="glossary_entity", aggregate_id=entry_id,
                    aggregate_version=version, expected_aggregate_version=version - 1,
                    result=result, target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def verify_entity(
        self,
        identity: SessionIdentity,
        *,
        entity_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        status = str(payload.get("status") or "").strip()
        if status == "alias_of":
            target = str(payload.get("alias_target_id") or "").strip()
            if not target:
                raise RepositoryError(422, "entity_alias_target_required", "请选择要合并到的实体")
            merged = self.merge_entity(
                identity,
                merged_id=entity_id,
                surviving_id=target,
                reason=str(payload.get("reason") or ""),
                idempotency_key=idempotency_key,
            )
            return {
                "entityId": entity_id,
                "verifiedStatus": status,
                "verifiedAt": merged["updatedAt"],
                "mergedInto": target,
                **{key: merged[key] for key in ("mentionsMoved", "factsMoved")},
            }
        if status not in {"canonical", "noise"}:
            raise RepositoryError(422, "entity_verification_invalid", "实体核实状态无效")
        payload_hash = self._payload_hash({"entityId": entity_id, "status": status})
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT g.*,s.client_id FROM glossary_entities g JOIN source_sets s "
                    "ON s.scope_id=g.scope_id AND s.id=g.source_set_id "
                    "WHERE g.id=? AND g.scope_id=? AND g.lifecycle_state='active'",
                    (entity_id, identity.scope_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "entity_missing", "实体不存在")
                project_id = str(row["client_id"])
                self.repository._require_project_access(connection, identity, project_id=project_id, capability="knowledge_write")
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                version, now = int(row["version"] or 1) + 1, utc_now()
                if status == "noise":
                    connection.execute(
                        "UPDATE glossary_entities SET lifecycle_state='archived',verification_state='rejected',"
                        "confirmed_by_membership_id=?,version=?,updated_at=? WHERE id=? AND scope_id=?",
                        (identity.membership_id, version, now, entity_id, identity.scope_id),
                    )
                else:
                    connection.execute(
                        "UPDATE glossary_entities SET verification_state='canonical',confirmed_by_membership_id=?,"
                        "version=?,updated_at=? WHERE id=? AND scope_id=?",
                        (identity.membership_id, version, now, entity_id, identity.scope_id),
                    )
                result = {"entityId": entity_id, "verifiedStatus": status, "verifiedAt": now}
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="project_entity.verified",
                    aggregate_type="glossary_entity", aggregate_id=entity_id,
                    aggregate_version=version, expected_aggregate_version=version - 1,
                    result=result, target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def merge_entity(
        self,
        identity: SessionIdentity,
        *,
        merged_id: str,
        surviving_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not surviving_id or surviving_id == merged_id:
            raise RepositoryError(422, "entity_merge_target_invalid", "请选择另一个保留实体")
        payload_hash = self._payload_hash(
            {"mergedId": merged_id, "survivingId": surviving_id, "reason": reason}
        )
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    "SELECT g.*,s.client_id FROM glossary_entities g JOIN source_sets s "
                    "ON s.scope_id=g.scope_id AND s.id=g.source_set_id "
                    "WHERE g.scope_id=? AND g.id IN (?,?) AND g.lifecycle_state='active'",
                    (identity.scope_id, merged_id, surviving_id),
                ).fetchall()
                by_id = {str(row["id"]): row for row in rows}
                if set(by_id) != {merged_id, surviving_id}:
                    raise RepositoryError(404, "entity_missing", "要合并的实体不存在")
                if str(by_id[merged_id]["client_id"]) != str(by_id[surviving_id]["client_id"]):
                    raise RepositoryError(409, "entity_scope_mismatch", "不能跨项目合并实体")
                project_id = str(by_id[merged_id]["client_id"])
                self.repository._require_project_access(connection, identity, project_id=project_id, capability="knowledge_write")
                replay = self._receipt(connection, identity, idempotency_key=idempotency_key, payload_hash=payload_hash)
                if replay is not None:
                    connection.rollback()
                    return replay
                def aliases_of(row: sqlite3.Row) -> set[str]:
                    try:
                        value = json.loads(str(row["aliases"] or "[]"))
                        return {str(v).strip() for v in value if str(v or "").strip()} if isinstance(value, list) else set()
                    except (TypeError, ValueError):
                        return set()
                source, target = by_id[merged_id], by_id[surviving_id]
                aliases = aliases_of(target) | aliases_of(source) | {str(source["preferred_term"])}
                now = utc_now()
                target_version = int(target["version"] or 1) + 1
                connection.execute(
                    "UPDATE glossary_entities SET aliases=?,version=?,updated_at=? WHERE id=? AND scope_id=?",
                    (canonical_json(sorted(aliases)), target_version, now, surviving_id, identity.scope_id),
                )
                connection.execute(
                    "UPDATE glossary_entities SET lifecycle_state='archived',verification_state='alias_of',"
                    "version=version+1,updated_at=? WHERE id=? AND scope_id=?",
                    (now, merged_id, identity.scope_id),
                )
                result = {
                    "mentionsMoved": 0,
                    "triplesMoved": 0,
                    "factsMoved": 0,
                    "mergedId": merged_id,
                    "survivingEntityId": surviving_id,
                    "updatedAt": now,
                }
                self._record_command(
                    connection, identity, idempotency_key=idempotency_key,
                    payload_hash=payload_hash, command_type="project_entity.merged",
                    aggregate_type="glossary_entity", aggregate_id=surviving_id,
                    aggregate_version=target_version,
                    expected_aggregate_version=int(target["version"] or 1),
                    result=result, target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def create_project(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = {
            "name": str(payload.get("name") or "").strip(),
            "alias": str(payload.get("alias") or "").strip(),
            "summary": str(payload.get("summary") or "").strip(),
            "domain": str(payload.get("domain") or "项目").strip() or "项目",
            "color": str(payload.get("color") or "#5B7BFE").strip() or "#5B7BFE",
            "participantMembershipIds": sorted(
                {
                    str(value).strip()
                    for value in payload.get("participantMembershipIds") or []
                    if str(value or "").strip()
                }
            ),
            "officialWebsiteUrl": _official_website_url(
                payload.get("officialWebsiteUrl")
            ),
        }
        if not normalized["name"]:
            raise RepositoryError(422, "project_name_required", "请输入项目名称")
        payload_hash = self._payload_hash(normalized)
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                requested = set(normalized["participantMembershipIds"])
                if requested:
                    placeholders = ",".join("?" for _ in requested)
                    rows = connection.execute(
                        f"SELECT id FROM organization_memberships WHERE scope_id=? "
                        f"AND id IN ({placeholders}) AND status='active' "
                        "AND lifecycle_state='active'",
                        (identity.scope_id, *sorted(requested)),
                    ).fetchall()
                    if {str(row["id"]) for row in rows} != requested:
                        raise RepositoryError(422, "project_member_invalid", "相关成员不属于当前组织")
                project_id = new_id()
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO secured_resources (
                        id, scope_id, resource_kind, lifecycle_state, version,
                        resource_type_key, created_at, updated_at, deleted_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, 'client', 'active', 1, 'client', ?, ?, NULL,
                              'cloud', ?)
                    """,
                    (project_id, identity.scope_id, now, now, identity.cloud_instance_id),
                )
                policy_version_id = create_project_policy_version(
                    connection,
                    scope_id=identity.scope_id,
                    project_id=project_id,
                    now=now,
                )
                connection.execute(
                    """
                    INSERT INTO clients (
                        id, scope_id, owner_principal_id, owner_membership_id,
                        lifecycle_state, version, name, alias, summary, domain,
                        color, visibility_scope, is_default_internal,
                        archived_at, created_at, updated_at, deleted_at
                    ) VALUES (?, ?, NULL, ?, 'active', 1, ?, ?, ?, ?, ?,
                              'organization', 0, NULL, ?, ?, NULL)
                    """,
                    (
                        project_id,
                        identity.scope_id,
                        identity.membership_id,
                        normalized["name"],
                        normalized["alias"],
                        normalized["summary"],
                        normalized["domain"],
                        normalized["color"],
                        now,
                        now,
                    ),
                )
                self._set_official_website_locator(
                    connection,
                    identity,
                    project_id=project_id,
                    official_website_url=normalized["officialWebsiteUrl"],
                    now=now,
                )
                members = requested | {identity.membership_id}
                for membership_id in sorted(members):
                    capability_set = canonical_json(
                        {
                            "read": True,
                            "write": membership_id == identity.membership_id,
                            "contributeKnowledge": True,
                            "manageSharing": membership_id == identity.membership_id,
                        }
                    )
                    connection.execute(
                        """
                        INSERT INTO object_grants (
                            id, scope_id, secured_resource_id,
                            policy_version_id, subject_principal_id,
                            subject_membership_id,
                            capability_set_schema_version, capability_set,
                            grant_generation, status, grant_source_set_id,
                            created_at, updated_at, revoked_at, version,
                            lifecycle_state, deleted_at
                        ) VALUES (?, ?, ?, ?, NULL, ?, '1', ?, 1, 'active',
                                  NULL, ?, ?, NULL, 1, 'active', NULL)
                        """,
                        (
                            new_id(),
                            identity.scope_id,
                            project_id,
                            policy_version_id,
                            membership_id,
                            capability_set,
                            now,
                            now,
                        ),
                    )
                row = connection.execute(
                    "SELECT * FROM clients WHERE id=?", (project_id,)
                ).fetchone()
                result = {"project": self._project_payload(connection, identity, row)}
                self._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="client.created",
                    aggregate_type="client",
                    aggregate_id=project_id,
                    aggregate_version=1,
                    expected_aggregate_version=None,
                    result=result,
                    target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def update_project(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        expected_version = int(payload.get("expectedVersion") or 0)
        allowed_fields = {
            "name": "name",
            "alias": "alias",
            "summary": "summary",
            "domain": "domain",
            "color": "color",
        }
        normalized: dict[str, Any] = {"expectedVersion": expected_version}
        for source in allowed_fields:
            if source in payload:
                normalized[source] = str(payload.get(source) or "").strip()
        if "name" in normalized and not normalized["name"]:
            raise RepositoryError(422, "project_name_required", "请输入项目名称")
        if "participantMembershipIds" in payload:
            normalized["participantMembershipIds"] = sorted(
                {
                    str(value).strip()
                    for value in payload.get("participantMembershipIds") or []
                    if str(value or "").strip()
                }
            )
        if "officialWebsiteUrl" in payload:
            normalized["officialWebsiteUrl"] = _official_website_url(
                payload.get("officialWebsiteUrl")
            )
        payload_hash = self._payload_hash(normalized)
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self.repository._require_project_access(
                    connection,
                    identity,
                    project_id=project_id,
                )
                if not (
                    identity.is_admin
                    or str(row["owner_membership_id"] or "") == identity.membership_id
                ):
                    raise RepositoryError(403, "project_forbidden", "只有项目负责人可以修改项目元数据")
                current_version = int(row["version"] or 1)
                if expected_version != current_version:
                    raise RepositoryError(
                        409,
                        "project_version_conflict",
                        "项目已更新，请刷新后重试",
                    )
                requested = set(normalized.get("participantMembershipIds") or [])
                if requested:
                    placeholders = ",".join("?" for _ in requested)
                    member_rows = connection.execute(
                        f"SELECT id FROM organization_memberships WHERE scope_id=? "
                        f"AND id IN ({placeholders}) AND status='active' "
                        "AND lifecycle_state='active'",
                        (identity.scope_id, *sorted(requested)),
                    ).fetchall()
                    if {str(item["id"]) for item in member_rows} != requested:
                        raise RepositoryError(422, "project_member_invalid", "相关成员不属于当前组织")
                assignments: list[str] = []
                values: list[Any] = []
                for source, column in allowed_fields.items():
                    if source in normalized:
                        assignments.append(f"{column}=?")
                        values.append(normalized[source])
                now = utc_now()
                assignments.extend(["version=version+1", "updated_at=?"])
                values.extend([now, project_id, identity.scope_id, current_version])
                updated = connection.execute(
                    f"UPDATE clients SET {', '.join(assignments)} "
                    "WHERE id=? AND scope_id=? AND version=?",
                    tuple(values),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(409, "project_version_conflict", "项目已更新，请刷新后重试")
                if "officialWebsiteUrl" in normalized:
                    self._set_official_website_locator(
                        connection,
                        identity,
                        project_id=project_id,
                        official_website_url=normalized["officialWebsiteUrl"],
                        now=now,
                    )
                revocation_propagation: dict[str, int] | None = None
                revocation_policy_version_id: str | None = None
                if "participantMembershipIds" in normalized:
                    owner_id = str(row["owner_membership_id"] or "")
                    desired = requested | ({owner_id} if owner_id else set())
                    policy = connection.execute(
                        "SELECT * FROM policy_versions WHERE scope_id=? "
                        "AND secured_resource_id=? AND lifecycle_state='active' "
                        "ORDER BY version DESC, created_at DESC, id DESC LIMIT 1",
                        (identity.scope_id, project_id),
                    ).fetchone()
                    if policy is None:
                        raise RepositoryError(
                            409,
                            "project_policy_projection_missing",
                            "项目权限版本尚未形成，请稍后重试",
                        )
                    active_grants = connection.execute(
                        "SELECT id, subject_membership_id, policy_version_id, "
                        "grant_generation, version "
                        "FROM object_grants WHERE scope_id=? "
                        "AND secured_resource_id=? AND status='active' "
                        "AND lifecycle_state='active' "
                        "AND subject_membership_id IS NOT NULL",
                        (identity.scope_id, project_id),
                    ).fetchall()
                    active_by_membership = {
                        str(item["subject_membership_id"]): item
                        for item in active_grants
                    }
                    current = set(active_by_membership)
                    removed = current - desired
                    added = desired - current
                    unchanged = desired & current
                    removed_grants = [active_by_membership[value] for value in sorted(removed)]
                    policy_version_id = str(policy["id"])
                    if removed:
                        connection.execute(
                            "UPDATE policy_versions SET lifecycle_state='archived',updated_at=? "
                            "WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                            (now, policy_version_id, identity.scope_id),
                        )
                        revocation_policy_version_id = create_project_policy_version(
                            connection,
                            scope_id=identity.scope_id,
                            project_id=project_id,
                            now=now,
                            version=int(policy["version"] or 1) + 1,
                        )
                        policy_version_id = revocation_policy_version_id
                    if removed:
                        placeholders = ",".join("?" for _ in removed)
                        connection.execute(
                            "UPDATE object_grants SET status='revoked', revoked_at=?, "
                            "version=version+1, updated_at=? "
                            "WHERE scope_id=? AND secured_resource_id=? "
                            f"AND subject_membership_id IN ({placeholders}) "
                            "AND status='active' AND lifecycle_state='active'",
                            (
                                now,
                                now,
                                identity.scope_id,
                                project_id,
                                *sorted(removed),
                            ),
                        )
                    for membership_id in sorted(unchanged):
                        existing = active_by_membership[membership_id]
                        if str(existing["policy_version_id"] or "") != policy_version_id:
                            connection.execute(
                                "UPDATE object_grants SET policy_version_id=?, "
                                "version=version+1, updated_at=? WHERE id=?",
                                (policy_version_id, now, existing["id"]),
                            )
                    for membership_id in sorted(added):
                        is_owner = membership_id == owner_id
                        capability_set = canonical_json(
                            {
                                "read": True,
                                "write": is_owner,
                                "contributeKnowledge": True,
                                "manageSharing": is_owner,
                            }
                        )
                        generation_row = connection.execute(
                            "SELECT COALESCE(MAX(grant_generation), 0) AS value "
                            "FROM object_grants WHERE scope_id=? AND secured_resource_id=? "
                            "AND subject_membership_id=?",
                            (identity.scope_id, project_id, membership_id),
                        ).fetchone()
                        generation = int(generation_row["value"] or 0) + 1
                        connection.execute(
                            "INSERT INTO object_grants ("
                            "id, scope_id, secured_resource_id, policy_version_id, "
                            "subject_principal_id, subject_membership_id, "
                            "capability_set_schema_version, capability_set, "
                            "grant_generation, status, grant_source_set_id, created_at, "
                            "updated_at, revoked_at, version, lifecycle_state, deleted_at"
                            ") VALUES (?, ?, ?, ?, NULL, ?, '1', ?, ?, 'active', "
                            "NULL, ?, ?, NULL, 1, 'active', NULL)",
                            (
                                new_id(),
                                identity.scope_id,
                                project_id,
                                policy_version_id,
                                membership_id,
                                capability_set,
                                generation,
                                now,
                                now,
                            ),
                        )
                    if removed_grants:
                        revocation_propagation = self._invalidate_revoked_member_derivatives(
                            connection,
                            identity,
                            project_id=project_id,
                            project_version=current_version + 1,
                            policy_version_id=str(policy["id"]),
                            removed_grants=removed_grants,
                            now=now,
                        )
                updated_row = connection.execute(
                    "SELECT * FROM clients WHERE id=? AND scope_id=?",
                    (project_id, identity.scope_id),
                ).fetchone()
                result = {"project": self._project_payload(connection, identity, updated_row)}
                if revocation_propagation is not None:
                    result["revocationPropagation"] = {
                        "state": "completed",
                        "policyVersionId": revocation_policy_version_id,
                        **revocation_propagation,
                    }
                operation_id, result_manifest_id = self._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="client.updated",
                    aggregate_type="client",
                    aggregate_id=project_id,
                    aggregate_version=current_version + 1,
                    expected_aggregate_version=expected_version,
                    result=result,
                    target_resource_id=project_id,
                )
                if revocation_propagation is not None:
                    invalidated_count = sum(revocation_propagation.values())
                    connection.execute(
                        "INSERT INTO reconciliation_runs (id,scope_id,operation_id,registry_state_id,"
                        "mismatch_count,status,reconciliation_kind,target_instance_id,"
                        "result_object_manifest_id,started_at,completed_at,version,lifecycle_state,"
                        "created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                        "VALUES (?,?,?,NULL,?,'completed','gc02_project_access_revocation_v1',"
                        "?,?,?, ?,?,'active',?,?,NULL,'cloud',?)",
                        (
                            "recon_" + sha256_text(
                                f"gc02_project_access_revocation_v1\x1f{identity.scope_id}\x1f{operation_id}"
                            )[:30],
                            identity.scope_id,
                            operation_id,
                            invalidated_count,
                            project_id,
                            result_manifest_id,
                            now,
                            now,
                            current_version + 1,
                            now,
                            now,
                            identity.cloud_instance_id,
                        ),
                    )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def register_local_material_metadata(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        materials: Iterable[Mapping[str, Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = []
        for material in materials:
            file_name = str(material.get("fileName") or "").strip()
            content_hash = str(material.get("contentHash") or "").strip().lower()
            try:
                byte_size = int(material.get("byteSize") or 0)
            except (TypeError, ValueError) as exc:
                raise RepositoryError(422, "material_metadata_invalid", "资料大小无效") from exc
            if (
                not file_name
                or len(content_hash) != 64
                or any(value not in "0123456789abcdef" for value in content_hash)
                or byte_size < 0
            ):
                raise RepositoryError(422, "material_metadata_invalid", "资料文件名、内容哈希或大小无效")
            normalized.append(
                {
                    "localSourceId": str(material.get("localSourceId") or "").strip(),
                    "fileName": file_name,
                    "contentHash": content_hash,
                    "byteSize": byte_size,
                    "mediaType": str(material.get("mediaType") or "application/octet-stream"),
                    "sourceKind": (
                        "task_attachment_metadata"
                        if str(material.get("relationKind") or "") == "task"
                        else "local_private_metadata"
                    ),
                    "relationKind": str(material.get("relationKind") or "").strip(),
                    "relationId": str(material.get("relationId") or "").strip(),
                }
            )
        if not normalized:
            raise RepositoryError(422, "materials_required", "请选择要导入的资料")
        payload = {"projectId": project_id, "materials": normalized}
        payload_hash = self._payload_hash(payload)
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                project = self.repository._require_project_access(
                    connection,
                    identity,
                    project_id=project_id,
                )
                for material in normalized:
                    if material["relationKind"] not in {"", "task"}:
                        raise RepositoryError(422, "material_relation_invalid", "资料关联对象无效")
                    if material["relationKind"] == "task":
                        task = connection.execute(
                            "SELECT client_id FROM tasks WHERE id=? AND scope_id=? "
                            "AND lifecycle_state!='deleted'",
                            (material["relationId"], identity.scope_id),
                        ).fetchone()
                        if task is None or str(task["client_id"] or "") != project_id:
                            raise RepositoryError(409, "task_material_project_mismatch", "任务附件与项目归属不一致")
                now = utc_now()
                documents: list[dict[str, Any]] = []
                skipped = 0
                imported = 0
                seen_hashes: set[str] = set()
                for material in normalized:
                    content_hash = material["contentHash"]
                    if content_hash in seen_hashes:
                        skipped += 1
                        continue
                    seen_hashes.add(content_hash)
                    existing = connection.execute(
                        """
                        SELECT * FROM source_assets
                        WHERE scope_id=? AND client_id=? AND record_kind='asset'
                          AND content_hash=? AND source_kind=?
                          AND COALESCE(source_locator_nonlocal,'')=?
                          AND lifecycle_state='active'
                        """,
                        (
                            identity.scope_id,
                            project_id,
                            content_hash,
                            material["sourceKind"],
                            (
                                f"task:{material['relationId']}"
                                if material["relationKind"] == "task"
                                else ""
                            ),
                        ),
                    ).fetchone()
                    if existing is not None:
                        skipped += 1
                        asset_id = str(existing["id"])
                        version = int(existing["version"] or 1)
                        updated_at = str(existing["updated_at"])
                    else:
                        asset_id = new_id()
                        manifest_id = new_id()
                        version = 1
                        updated_at = now
                        boundary_receipt = canonical_json(
                            {
                                "boundary": "local_private_metadata_only",
                                "sourceFileContentUploaded": False,
                                "sourceFilePathUploaded": False,
                            }
                        )
                        connection.execute(
                            """
                            INSERT INTO object_manifests (
                                id, scope_id, storage_key, content_hash,
                                lifecycle_state, receipt, holder_role,
                                holder_instance_id, storage_kind, byte_size,
                                media_type, availability_state, receipt_hash,
                                created_at, verified_at, deleted_at,
                                authority_role, origin_instance_id
                            ) VALUES (?, ?, NULL, ?, 'active', ?, 'member_device',
                                      ?, 'local_private_reference', ?, ?,
                                      'local_only', ?, ?, ?, NULL, 'local', ?)
                            """,
                            (
                                manifest_id,
                                identity.scope_id,
                                content_hash,
                                boundary_receipt,
                                identity.principal_id,
                                material["byteSize"],
                                material["mediaType"],
                                sha256_text(boundary_receipt),
                                now,
                                now,
                                identity.principal_id,
                            ),
                        )
                        imported += 1
                        connection.execute(
                            """
                            INSERT INTO secured_resources (
                                id, scope_id, resource_kind, lifecycle_state,
                                version, resource_type_key, created_at,
                                updated_at, deleted_at, authority_role,
                                origin_instance_id
                            ) VALUES (?, ?, 'source_asset', 'active', 1,
                                      'local_private_metadata', ?, ?, NULL,
                                      'cloud', ?)
                            """,
                            (asset_id, identity.scope_id, now, now, identity.cloud_instance_id),
                        )
                        connection.execute(
                            """
                            INSERT INTO source_assets (
                                id, scope_id, client_id, object_manifest_id,
                                content_hash, record_kind, source_kind,
                                display_name, media_type, byte_size,
                                source_locator_nonlocal, parent_folder_id,
                                asset_id, folder_id, created_by_membership_id,
                                availability_state, archived_at, version,
                                lifecycle_state, created_at, updated_at,
                                deleted_at, authority_role, origin_instance_id
                            ) VALUES (?, ?, ?, ?, ?, 'asset', ?, ?, ?, ?,
                                      ?, NULL, NULL, NULL, ?, 'local_only',
                                      NULL, 1, 'active', ?, ?, NULL, 'cloud', ?)
                            """,
                            (
                                asset_id,
                                identity.scope_id,
                                project_id,
                                manifest_id,
                                content_hash,
                                material["sourceKind"],
                                material["fileName"],
                                material["mediaType"],
                                material["byteSize"],
                                (
                                    f"task:{material['relationId']}"
                                    if material["relationKind"] == "task"
                                    else None
                                ),
                                identity.membership_id,
                                now,
                                now,
                                identity.cloud_instance_id,
                            ),
                        )
                    documents.append(
                        {
                            "localSourceId": material["localSourceId"],
                            "sourceAssetId": asset_id,
                            "documentId": asset_id,
                            "title": material["fileName"],
                            "fileName": material["fileName"],
                            "contentHash": content_hash,
                            "lifecycleState": "active",
                            "parseState": "local_only",
                            "version": version,
                            "updatedAt": updated_at,
                        }
                    )
                result = {
                    "importRunId": new_id(),
                    "projectId": project_id,
                    "documents": documents,
                    "importedCount": imported,
                    "skippedCount": skipped,
                    "createdAt": now,
                    "materialBoundary": {
                        "sourceFileContentUploaded": False,
                        "sourceFilePathUploaded": False,
                        "localSummaryUploaded": False,
                    },
                }
                self._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="source_asset.metadata_registered",
                    aggregate_type="client",
                    aggregate_id=project_id,
                    aggregate_version=int(project["version"] or 1),
                    expected_aggregate_version=None,
                    result=result,
                    target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def local_material_metadata(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        """Return safe cloud metadata for a member-owned local source.

        The organization cloud never returns the member file body or its path.
        This read exists only to support CAS updates/deletion of the registered
        metadata row.
        """
        with self.repository._connection() as connection:
            self.repository._require_project_access(
                connection,
                identity,
                project_id=project_id,
            )
            row = connection.execute(
                "SELECT * FROM source_assets WHERE id=? AND scope_id=? "
                "AND client_id=? AND record_kind='asset' "
                "AND source_kind='local_private_metadata' "
                "AND lifecycle_state='active'",
                (document_id, identity.scope_id, project_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "material_metadata_missing", "资料元数据不存在或已删除")
            if str(row["created_by_membership_id"] or "") != identity.membership_id:
                raise RepositoryError(403, "material_metadata_owner_required", "只能管理本人设备登记的资料元数据")
            return {
                "documentId": str(row["id"]),
                "sourceAssetId": str(row["id"]),
                "projectId": project_id,
                "title": str(row["display_name"] or "未命名资料"),
                "fileName": str(row["display_name"] or "未命名资料"),
                "contentHash": str(row["content_hash"] or ""),
                "byteSize": int(row["byte_size"] or 0),
                "mediaType": str(row["media_type"] or "application/octet-stream"),
                "parseState": "local_only",
                "aggregateVersion": int(row["version"] or 1),
                "lifecycleState": "active",
                "materialBoundary": {
                    "sourceFileContentUploaded": False,
                    "sourceFilePathUploaded": False,
                    "localSummaryUploaded": False,
                },
            }

    def update_local_material_metadata(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        document_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        expected_version = int(payload.get("expectedVersion") or 0)
        title = str(payload.get("title") or payload.get("fileName") or "").strip()
        content_hash = str(payload.get("contentHash") or "").strip().lower()
        media_type = str(payload.get("mediaType") or "application/octet-stream").strip()
        try:
            byte_size = int(payload.get("byteSize") or 0)
        except (TypeError, ValueError) as exc:
            raise RepositoryError(422, "material_metadata_invalid", "资料大小无效") from exc
        if (
            not title
            or len(content_hash) != 64
            or any(value not in "0123456789abcdef" for value in content_hash)
            or byte_size < 0
        ):
            raise RepositoryError(422, "material_metadata_invalid", "资料元数据无效")
        normalized = {
            "projectId": project_id,
            "documentId": document_id,
            "expectedVersion": expected_version,
            "title": title,
            "contentHash": content_hash,
            "byteSize": byte_size,
            "mediaType": media_type,
        }
        payload_hash = self._payload_hash(normalized)
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self.repository._require_project_access(
                    connection,
                    identity,
                    project_id=project_id,
                )
                row = connection.execute(
                    "SELECT * FROM source_assets WHERE id=? AND scope_id=? "
                    "AND client_id=? AND record_kind='asset' "
                    "AND source_kind='local_private_metadata' "
                    "AND lifecycle_state='active'",
                    (document_id, identity.scope_id, project_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "material_metadata_missing", "资料元数据不存在或已删除")
                if str(row["created_by_membership_id"] or "") != identity.membership_id:
                    raise RepositoryError(403, "material_metadata_owner_required", "只能管理本人设备登记的资料元数据")
                current_version = int(row["version"] or 1)
                if expected_version != current_version:
                    raise RepositoryError(409, "material_metadata_version_conflict", "资料元数据已更新，请刷新后重试")
                now = utc_now()
                updated = connection.execute(
                    "UPDATE source_assets SET display_name=?, content_hash=?, "
                    "byte_size=?, media_type=?, version=version+1, updated_at=? "
                    "WHERE id=? AND scope_id=? AND version=? "
                    "AND lifecycle_state='active'",
                    (
                        title,
                        content_hash,
                        byte_size,
                        media_type,
                        now,
                        document_id,
                        identity.scope_id,
                        current_version,
                    ),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(409, "material_metadata_version_conflict", "资料元数据已更新，请刷新后重试")
                manifest_id = str(row["object_manifest_id"] or "")
                if manifest_id:
                    boundary_receipt = canonical_json(
                        {
                            "boundary": "local_private_metadata_only",
                            "sourceFileContentUploaded": False,
                            "sourceFilePathUploaded": False,
                        }
                    )
                    connection.execute(
                        "UPDATE object_manifests SET content_hash=?, byte_size=?, "
                        "media_type=?, receipt=?, receipt_hash=?, verified_at=? "
                        "WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                        (
                            content_hash,
                            byte_size,
                            media_type,
                            boundary_receipt,
                            sha256_text(boundary_receipt),
                            now,
                            manifest_id,
                            identity.scope_id,
                        ),
                    )
                result = {
                    "documentId": document_id,
                    "projectId": project_id,
                    "title": title,
                    "fileName": title,
                    "contentHash": content_hash,
                    "byteSize": byte_size,
                    "mediaType": media_type,
                    "aggregateVersion": current_version + 1,
                    "lifecycleState": "active",
                    "materialBoundary": {
                        "sourceFileContentUploaded": False,
                        "sourceFilePathUploaded": False,
                        "localSummaryUploaded": False,
                    },
                }
                self._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="source_asset.metadata_updated",
                    aggregate_type="source_asset",
                    aggregate_id=document_id,
                    aggregate_version=current_version + 1,
                    expected_aggregate_version=expected_version,
                    result=result,
                    target_resource_id=document_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def delete_local_material_metadata(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        document_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized = {
            "projectId": project_id,
            "documentId": document_id,
            "expectedVersion": int(expected_version),
        }
        payload_hash = self._payload_hash(normalized)
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt = self._receipt(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self.repository._require_project_access(
                    connection,
                    identity,
                    project_id=project_id,
                )
                row = connection.execute(
                    "SELECT * FROM source_assets WHERE id=? AND scope_id=? "
                    "AND client_id=? AND record_kind='asset' "
                    "AND source_kind='local_private_metadata' "
                    "AND lifecycle_state='active'",
                    (document_id, identity.scope_id, project_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "material_metadata_missing", "资料元数据不存在或已删除")
                if str(row["created_by_membership_id"] or "") != identity.membership_id:
                    raise RepositoryError(403, "material_metadata_owner_required", "只能管理本人设备登记的资料元数据")
                current_version = int(row["version"] or 1)
                if int(expected_version) != current_version:
                    raise RepositoryError(409, "material_metadata_version_conflict", "资料元数据已更新，请刷新后重试")
                now = utc_now()
                connection.execute(
                    "UPDATE source_assets SET lifecycle_state='deleted', "
                    "availability_state='deleted', deleted_at=?, updated_at=?, "
                    "version=version+1 WHERE id=? AND scope_id=? AND version=?",
                    (now, now, document_id, identity.scope_id, current_version),
                )
                connection.execute(
                    "UPDATE secured_resources SET lifecycle_state='deleted', "
                    "deleted_at=?, updated_at=?, version=version+1 "
                    "WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                    (now, now, document_id, identity.scope_id),
                )
                manifest_id = str(row["object_manifest_id"] or "")
                if manifest_id:
                    connection.execute(
                        "UPDATE object_manifests SET lifecycle_state='deleted', "
                        "availability_state='deleted', deleted_at=? "
                        "WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                        (now, manifest_id, identity.scope_id),
                    )
                lifecycle_id = new_id()
                lifecycle_hash = sha256_text(
                    canonical_json(
                        {
                            "id": lifecycle_id,
                            "scopeId": identity.scope_id,
                            "resourceId": document_id,
                            "from": "active",
                            "to": "deleted",
                            "reason": "member_local_metadata_deleted",
                            "occurredAt": now,
                        }
                    )
                )
                connection.execute(
                    "INSERT INTO lifecycle_events ("
                    "id,scope_id,operation_id,secured_resource_id,from_state,"
                    "to_state,tombstone_version,actor_id,reason_code,occurred_at,"
                    "origin_instance_id,created_at,integrity_hash"
                    ") VALUES (?,?,NULL,?,'active','deleted',?,?,?,?,?,?,?)",
                    (
                        lifecycle_id,
                        identity.scope_id,
                        document_id,
                        current_version + 1,
                        identity.principal_id,
                        "member_local_metadata_deleted",
                        now,
                        identity.cloud_instance_id,
                        now,
                        lifecycle_hash,
                    ),
                )
                result = {
                    "documentId": document_id,
                    "projectId": project_id,
                    "deleted": True,
                    "aggregateVersion": current_version + 1,
                    "lifecycleState": "deleted",
                }
                self._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="source_asset.metadata_deleted",
                    aggregate_type="source_asset",
                    aggregate_id=document_id,
                    aggregate_version=current_version + 1,
                    expected_aggregate_version=int(expected_version),
                    result=result,
                    target_resource_id=document_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise



# The pre-blueprint ProjectMaterialsRepository was frozen under
# legacy_frozen/v8_pre_gc02.  Only GC07ProjectMaterialsRepository is runtime.
