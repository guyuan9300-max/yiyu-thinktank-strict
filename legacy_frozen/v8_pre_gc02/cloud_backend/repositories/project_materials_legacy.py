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


def _safe_summary_kind(value: Any) -> bool:
    kind = str(value or "").strip().lower()
    return kind in SHARED_KNOWLEDGE_DOCUMENT_KINDS or kind.endswith("_summary")


def _iso_week_ago() -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=7))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class GC07ProjectMaterialsRepository:
    """The narrow GC-07 project/material authority over the 88-table schema."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _payload_hash(payload: Mapping[str, Any]) -> str:
        return payload_fingerprint(dict(payload))

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
        result: Mapping[str, Any],
        target_resource_id: str,
    ) -> None:
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'committed', ?,
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
            SELECT source_locator_nonlocal
            FROM source_assets
            WHERE scope_id=? AND client_id=? AND record_kind='asset'
              AND source_kind='official_website' AND lifecycle_state='active'
            ORDER BY created_at, id LIMIT 1
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
                    str(value)
                    for value in payload.get("participantMembershipIds") or []
                    if str(value or "").strip()
                }
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
                        ) VALUES (?, ?, ?, NULL, NULL, ?, '1', ?, 1, 'active',
                                  NULL, ?, ?, NULL, 1, 'active', NULL)
                        """,
                        (
                            new_id(),
                            identity.scope_id,
                            project_id,
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
                if "participantMembershipIds" in normalized:
                    owner_id = str(row["owner_membership_id"] or "")
                    desired = requested | ({owner_id} if owner_id else set())
                    connection.execute(
                        "UPDATE object_grants SET status='revoked', revoked_at=?, "
                        "version=version+1, updated_at=? "
                        "WHERE scope_id=? AND secured_resource_id=? "
                        "AND status='active' AND lifecycle_state='active' "
                        "AND subject_membership_id IS NOT NULL",
                        (now, now, identity.scope_id, project_id),
                    )
                    for membership_id in sorted(desired):
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
                            ") VALUES (?, ?, ?, NULL, NULL, ?, '1', ?, ?, 'active', "
                            "NULL, ?, ?, NULL, 1, 'active', NULL)",
                            (
                                new_id(),
                                identity.scope_id,
                                project_id,
                                membership_id,
                                capability_set,
                                generation,
                                now,
                                now,
                            ),
                        )
                updated_row = connection.execute(
                    "SELECT * FROM clients WHERE id=? AND scope_id=?",
                    (project_id, identity.scope_id),
                ).fetchone()
                result = {"project": self._project_payload(connection, identity, updated_row)}
                self._record_command(
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="client.updated",
                    aggregate_type="client",
                    aggregate_id=project_id,
                    aggregate_version=current_version + 1,
                    result=result,
                    target_resource_id=project_id,
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
                    "sourceKind": "local_private_metadata",
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
                          AND content_hash=? AND lifecycle_state='active'
                        """,
                        (identity.scope_id, project_id, content_hash),
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
                                      NULL, NULL, NULL, NULL, ?, 'local_only',
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
                    result=result,
                    target_resource_id=project_id,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise


class ProjectMaterialsRepository:
    def __init__(self, repository: CloudRepository):
        self.repository = repository

    @staticmethod
    def _receipt(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        payload_hash = payload_fingerprint(dict(payload))
        row = connection.execute(
            """
            SELECT payload_hash, result_json
            FROM command_idempotency
            WHERE scope_id = ? AND actor_principal_id = ?
              AND command_type = ? AND idempotency_key = ?
            """,
            (
                identity.scope_id,
                identity.principal_id,
                command_type,
                idempotency_key,
            ),
        ).fetchone()
        if row is None:
            return None, payload_hash
        if str(row["payload_hash"]) != payload_hash:
            raise RepositoryError(409, "idempotency_conflict", "操作标识冲突")
        return json.loads(str(row["result_json"])), payload_hash

    def _record_command(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        aggregate_type: str,
        aggregate_id: str,
        expected_version: int | None,
        before_version: int | None,
        after_version: int,
        payload: Mapping[str, Any],
        payload_hash: str,
        result: Mapping[str, Any],
        audit_summary: Mapping[str, Any] | None = None,
        outbox_payload: Mapping[str, Any] | None = None,
        additional_outbox_events: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        now = utc_now()
        operation_id = new_id()
        payload_json = canonical_json(dict(payload))
        result_json = canonical_json(dict(result))
        connection.execute(
            """
            INSERT INTO command_envelopes (
                command_id, scope_id, organization_id, operation_id,
                idempotency_key, aggregate_type, aggregate_id,
                command_type, actor_principal_id, expected_version,
                payload_json, payload_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'committed', ?, ?)
            """,
            (
                new_id(),
                identity.scope_id,
                identity.organization_id,
                operation_id,
                idempotency_key,
                aggregate_type,
                aggregate_id,
                command_type,
                identity.principal_id,
                expected_version,
                payload_json,
                payload_hash,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO command_idempotency (
                record_id, scope_id, actor_principal_id, command_type,
                idempotency_key, payload_hash, result_hash, result_json,
                expires_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                      '9999-12-31T23:59:59.999Z', ?)
            """,
            (
                new_id(),
                identity.scope_id,
                identity.principal_id,
                command_type,
                idempotency_key,
                payload_hash,
                sha256_text(result_json),
                result_json,
                now,
            ),
        )
        self.repository._insert_audit(
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            actor_id=identity.principal_id,
            action=command_type,
            resource_type=aggregate_type,
            resource_id=aggregate_id,
            before_version=before_version,
            after_version=after_version,
            summary=dict(audit_summary or payload),
        )
        self.repository._insert_outbox(
            connection,
            scope_id=identity.scope_id,
            organization_id=identity.organization_id,
            operation_id=operation_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=after_version,
            event_type=command_type,
            payload=dict(
                outbox_payload
                or {
                    f"{aggregate_type}Id": aggregate_id,
                    "version": after_version,
                }
            ),
        )
        for event in additional_outbox_events:
            self.repository._insert_outbox(
                connection,
                scope_id=identity.scope_id,
                organization_id=identity.organization_id,
                operation_id=operation_id,
                aggregate_type=str(event["aggregateType"]),
                aggregate_id=str(event["aggregateId"]),
                aggregate_version=int(event.get("aggregateVersion") or 1),
                event_type=str(event["eventType"]),
                payload=dict(event.get("payload") or {}),
            )

    def _project_row(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        project_id: str,
        *,
        require_edit: bool,
    ) -> sqlite3.Row:
        if self.repository._visible_project(
            connection,
            identity,
            project_id=project_id,
        ) is None:
            raise RepositoryError(404, "project_missing", "当前成员无法访问该项目")
        row = connection.execute(
            """
            SELECT *
            FROM work_projects
            WHERE organization_id = ? AND project_id = ?
            """,
            (identity.organization_id, project_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "project_missing", "项目不存在")
        if require_edit and not identity.is_admin:
            participant = connection.execute(
                """
                SELECT participant_role
                FROM project_participants
                WHERE organization_id = ? AND project_id = ?
                  AND membership_id = ? AND status = 'active'
                """,
                (
                    identity.organization_id,
                    project_id,
                    identity.membership_id,
                ),
            ).fetchone()
            can_edit = row["created_by_membership_id"] == identity.membership_id or (
                participant is not None
                and str(participant["participant_role"]) in {"owner", "editor"}
            )
            if not can_edit:
                raise RepositoryError(403, "project_forbidden", "无权修改该项目")
        return row

    @staticmethod
    def _active_memberships(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        values: Iterable[Any],
    ) -> set[str]:
        requested = {str(value) for value in values if str(value or "").strip()}
        if not requested:
            return set()
        placeholders = ",".join("?" for _ in requested)
        rows = connection.execute(
            f"""
            SELECT membership_id
            FROM organization_memberships
            WHERE organization_id = ? AND status = 'active'
              AND membership_id IN ({placeholders})
            """,
            (identity.organization_id, *sorted(requested)),
        ).fetchall()
        found = {str(row["membership_id"]) for row in rows}
        if found != requested:
            raise RepositoryError(422, "project_member_invalid", "相关成员不属于当前组织")
        return found

    def list_projects(self, identity: SessionIdentity) -> dict[str, Any]:
        snapshot = self.repository.business_snapshot(identity)
        projects = list(snapshot.get("projects") or [])
        project_ids = {str(item["projectId"]) for item in projects}
        participants: dict[str, list[str]] = defaultdict(list)
        if project_ids:
            placeholders = ",".join("?" for _ in project_ids)
            with self.repository._connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT project_id, membership_id
                    FROM project_participants
                    WHERE organization_id = ?
                      AND project_id IN ({placeholders})
                      AND status = 'active'
                    ORDER BY project_id, membership_id
                    """,
                    (identity.organization_id, *sorted(project_ids)),
                ).fetchall()
            for row in rows:
                participants[str(row["project_id"])].append(
                    str(row["membership_id"])
                )
        document_counts: dict[str, int] = defaultdict(int)
        task_counts: dict[str, int] = defaultdict(int)
        for item in snapshot.get("documents") or []:
            if (
                item.get("projectId")
                and item.get("lifecycleState") == "active"
            ):
                document_counts[str(item["projectId"])] += 1
        for item in snapshot.get("tasks") or []:
            if (
                item.get("projectId")
                and item.get("lifecycleState") != "archived"
            ):
                task_counts[str(item["projectId"])] += 1
        return {
            "projects": [
                {
                    **project,
                    "participantMembershipIds": participants.get(
                        str(project["projectId"]), []
                    ),
                    "documentCount": document_counts.get(
                        str(project["projectId"]), 0
                    ),
                    "taskCount": task_counts.get(str(project["projectId"]), 0),
                    "folderState": "not_connected",
                }
                for project in projects
            ],
            "generatedAt": snapshot.get("generatedAt") or utc_now(),
        }

    def project_detail(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        project = next(
            (
                item
                for item in self.list_projects(identity)["projects"]
                if str(item["projectId"]) == project_id
            ),
            None,
        )
        if project is None:
            raise RepositoryError(404, "project_missing", "当前成员无法访问该项目")
        return {"project": project}

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
            "color": str(payload.get("color") or "#5B7BFE").strip()
            or "#5B7BFE",
            "participantMembershipIds": sorted(
                {
                    str(value)
                    for value in payload.get("participantMembershipIds") or []
                    if str(value or "").strip()
                }
            ),
        }
        if not normalized["name"]:
            raise RepositoryError(422, "project_name_required", "请输入项目名称")
        command_type = "project.created"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                related = self._active_memberships(
                    connection,
                    identity,
                    normalized["participantMembershipIds"],
                )
                project_id = new_id()
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO work_projects (
                        project_id, organization_id, name, alias, summary,
                        domain, color, is_default_internal_project,
                        lifecycle_state, created_by_membership_id, version,
                        created_at, updated_at, archived_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'active', ?, 1, ?, ?, NULL)
                    """,
                    (
                        project_id,
                        identity.organization_id,
                        normalized["name"],
                        normalized["alias"],
                        normalized["summary"],
                        normalized["domain"],
                        normalized["color"],
                        identity.membership_id,
                        now,
                        now,
                    ),
                )
                roles = {membership_id: "editor" for membership_id in related}
                roles[identity.membership_id] = "owner"
                for membership_id, role in sorted(roles.items()):
                    connection.execute(
                        """
                        INSERT INTO project_participants (
                            project_id, organization_id, membership_id,
                            participant_role, status, version, created_at,
                            updated_at
                        ) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)
                        """,
                        (
                            project_id,
                            identity.organization_id,
                            membership_id,
                            role,
                            now,
                            now,
                        ),
                    )
                project = {
                    "projectId": project_id,
                    "name": normalized["name"],
                    "alias": normalized["alias"],
                    "summary": normalized["summary"],
                    "domain": normalized["domain"],
                    "color": normalized["color"],
                    "isDefaultInternalProject": False,
                    "lifecycleState": "active",
                    "version": 1,
                    "updatedAt": now,
                    "participantMembershipIds": sorted(roles),
                    "documentCount": 0,
                    "taskCount": 0,
                    "folderState": "not_connected",
                }
                result = {"project": project}
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="project",
                    aggregate_id=project_id,
                    expected_version=None,
                    before_version=None,
                    after_version=1,
                    payload=normalized,
                    payload_hash=payload_hash,
                    result=result,
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
                    str(value)
                    for value in payload.get("participantMembershipIds") or []
                    if str(value or "").strip()
                }
            )
        command_type = "project.updated"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self._project_row(
                    connection,
                    identity,
                    project_id,
                    require_edit=True,
                )
                current_version = int(row["version"])
                if expected_version != current_version:
                    raise RepositoryError(
                        409,
                        "project_version_conflict",
                        "项目已更新，请刷新后重试",
                    )
                assignments: list[str] = []
                values: list[Any] = []
                for source, column in allowed_fields.items():
                    if source in normalized:
                        assignments.append(f"{column} = ?")
                        values.append(normalized[source])
                now = utc_now()
                assignments.extend(["version = version + 1", "updated_at = ?"])
                values.extend(
                    [
                        now,
                        identity.organization_id,
                        project_id,
                        current_version,
                    ]
                )
                updated = connection.execute(
                    f"""
                    UPDATE work_projects
                    SET {", ".join(assignments)}
                    WHERE organization_id = ? AND project_id = ? AND version = ?
                    """,
                    tuple(values),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "project_version_conflict",
                        "项目已更新，请刷新后重试",
                    )
                if "participantMembershipIds" in normalized:
                    related = self._active_memberships(
                        connection,
                        identity,
                        normalized["participantMembershipIds"],
                    )
                    desired = {
                        membership_id: "editor" for membership_id in related
                    }
                    creator_membership_id = str(
                        row["created_by_membership_id"] or ""
                    )
                    if creator_membership_id:
                        desired[creator_membership_id] = "owner"
                    connection.execute(
                        """
                        UPDATE project_participants
                        SET status = 'revoked', version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND project_id = ?
                          AND membership_id NOT IN (
                              SELECT created_by_membership_id
                              FROM work_projects WHERE project_id = ?
                          )
                        """,
                        (
                            now,
                            identity.organization_id,
                            project_id,
                            project_id,
                        ),
                    )
                    for membership_id, role in sorted(desired.items()):
                        connection.execute(
                            """
                            INSERT INTO project_participants (
                                project_id, organization_id, membership_id,
                                participant_role, status, version, created_at,
                                updated_at
                            ) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)
                            ON CONFLICT(project_id, membership_id) DO UPDATE SET
                                participant_role = excluded.participant_role,
                                status = 'active',
                                version = project_participants.version + 1,
                                updated_at = excluded.updated_at
                            """,
                            (
                                project_id,
                                identity.organization_id,
                                membership_id,
                                role,
                                now,
                                now,
                            ),
                        )
                participant_rows = connection.execute(
                    """
                    SELECT membership_id
                    FROM project_participants
                    WHERE organization_id = ? AND project_id = ?
                      AND status = 'active'
                    ORDER BY membership_id
                    """,
                    (identity.organization_id, project_id),
                ).fetchall()
                result = {
                    "project": {
                        "projectId": project_id,
                        "name": normalized.get("name", row["name"]),
                        "alias": normalized.get("alias", row["alias"]),
                        "summary": normalized.get("summary", row["summary"]),
                        "domain": normalized.get("domain", row["domain"]),
                        "color": normalized.get("color", row["color"]),
                        "isDefaultInternalProject": bool(
                            row["is_default_internal_project"]
                        ),
                        "lifecycleState": row["lifecycle_state"],
                        "version": current_version + 1,
                        "updatedAt": now,
                        "participantMembershipIds": [
                            str(item["membership_id"])
                            for item in participant_rows
                        ],
                        "documentCount": int(
                            connection.execute(
                                """
                                SELECT COUNT(*) FROM knowledge_documents
                                WHERE organization_id = ? AND project_id = ?
                                  AND lifecycle_state = 'active'
                                """,
                                (identity.organization_id, project_id),
                            ).fetchone()[0]
                        ),
                        "taskCount": int(
                            connection.execute(
                                """
                                SELECT COUNT(*) FROM task_records
                                WHERE organization_id = ? AND project_id = ?
                                  AND lifecycle_state != 'archived'
                                """,
                                (identity.organization_id, project_id),
                            ).fetchone()[0]
                        ),
                        "folderState": "not_connected",
                    }
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="project",
                    aggregate_id=project_id,
                    expected_version=current_version,
                    before_version=current_version,
                    after_version=current_version + 1,
                    payload=normalized,
                    payload_hash=payload_hash,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def transition_project(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        target_state: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if target_state not in {"active", "frozen", "archived"}:
            raise RepositoryError(422, "project_state_invalid", "项目状态无效")
        command_type = f"project.{target_state}"
        payload = {
            "expectedVersion": expected_version,
            "targetState": target_state,
        }
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self._project_row(
                    connection,
                    identity,
                    project_id,
                    require_edit=True,
                )
                current_version = int(row["version"])
                if current_version != expected_version:
                    raise RepositoryError(
                        409,
                        "project_version_conflict",
                        "项目已更新，请刷新后重试",
                    )
                if bool(row["is_default_internal_project"]) and target_state == "archived":
                    raise RepositoryError(
                        409,
                        "default_project_protected",
                        "组织默认内部项目不能归档",
                    )
                now = utc_now()
                updated = connection.execute(
                    """
                    UPDATE work_projects
                    SET lifecycle_state = ?, version = version + 1,
                        updated_at = ?,
                        archived_at = CASE WHEN ? = 'archived' THEN ? ELSE NULL END
                    WHERE organization_id = ? AND project_id = ? AND version = ?
                    """,
                    (
                        target_state,
                        now,
                        target_state,
                        now,
                        identity.organization_id,
                        project_id,
                        current_version,
                    ),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "project_version_conflict",
                        "项目已更新，请刷新后重试",
                    )
                result = {
                    "project": {
                        "projectId": project_id,
                        "name": row["name"],
                        "alias": row["alias"],
                        "summary": row["summary"],
                        "domain": row["domain"],
                        "color": row["color"],
                        "isDefaultInternalProject": bool(
                            row["is_default_internal_project"]
                        ),
                        "lifecycleState": target_state,
                        "version": current_version + 1,
                        "updatedAt": now,
                    }
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="project",
                    aggregate_id=project_id,
                    expected_version=current_version,
                    before_version=current_version,
                    after_version=current_version + 1,
                    payload=payload,
                    payload_hash=payload_hash,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def delete_preview(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            project = self._project_row(
                connection,
                identity,
                project_id,
                require_edit=True,
            )
            counts = {
                "documentCount": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM knowledge_documents
                        WHERE organization_id = ? AND project_id = ?
                          AND lifecycle_state != 'deleted'
                        """,
                        (identity.organization_id, project_id),
                    ).fetchone()[0]
                ),
                "taskCount": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM task_records
                        WHERE organization_id = ? AND project_id = ?
                          AND lifecycle_state != 'archived'
                        """,
                        (identity.organization_id, project_id),
                    ).fetchone()[0]
                ),
                "eventLineCount": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM event_line_records
                        WHERE organization_id = ? AND project_id = ?
                          AND lifecycle_state != 'archived'
                        """,
                        (identity.organization_id, project_id),
                    ).fetchone()[0]
                ),
                "narrativeCount": int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM narrative_outputs
                        WHERE organization_id = ? AND project_id = ?
                          AND lifecycle_state != 'archived'
                        """,
                        (identity.organization_id, project_id),
                    ).fetchone()[0]
                ),
            }
        return {
            "projectId": project_id,
            "name": project["name"],
            "version": int(project["version"]),
            "isDefaultInternalProject": bool(
                project["is_default_internal_project"]
            ),
            **counts,
            "unavailableLegacyCounts": [
                "threads",
                "messages",
                "folders",
                "dna",
                "goals",
                "meetings",
            ],
        }

    def _visible_document_ids(
        self,
        identity: SessionIdentity,
        *,
        project_id: str | None = None,
    ) -> set[str]:
        snapshot = self.repository.business_snapshot(identity)
        return {
            str(item["documentId"])
            for item in snapshot.get("documents") or []
            if not project_id or str(item.get("projectId") or "") == project_id
        }

    def _document_row(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        document_id: str,
        project_id: str | None = None,
        require_edit: bool = False,
    ) -> sqlite3.Row:
        if document_id not in self._visible_document_ids(
            identity,
            project_id=project_id,
        ):
            raise RepositoryError(404, "document_missing", "当前成员无法访问该资料")
        row = connection.execute(
            """
            SELECT d.*, a.file_name, a.media_type, a.byte_size,
                   a.content_hash AS source_content_hash,
                   a.source_kind, a.lifecycle_state AS source_lifecycle_state
            FROM knowledge_documents d
            LEFT JOIN source_assets a
              ON a.source_asset_id = d.source_asset_id
             AND a.organization_id = d.organization_id
            WHERE d.organization_id = ? AND d.document_id = ?
            """,
            (identity.organization_id, document_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "document_missing", "资料不存在")
        if project_id and str(row["project_id"] or "") != project_id:
            raise RepositoryError(404, "document_missing", "资料不属于该项目")
        if require_edit and not identity.is_admin:
            can_edit = row["owner_membership_id"] == identity.membership_id
            if not can_edit and row["project_id"]:
                participant = connection.execute(
                    """
                    SELECT participant_role
                    FROM project_participants
                    WHERE organization_id = ? AND project_id = ?
                      AND membership_id = ? AND status = 'active'
                    """,
                    (
                        identity.organization_id,
                        row["project_id"],
                        identity.membership_id,
                    ),
                ).fetchone()
                can_edit = participant is not None and str(
                    participant["participant_role"]
                ) in {"owner", "editor"}
            if not can_edit:
                raise RepositoryError(403, "document_forbidden", "无权修改该资料")
        return row

    def document_reading_preview(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        document_id: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            row = self._document_row(
                connection,
                identity,
                document_id=document_id,
                project_id=project_id,
            )
            version = connection.execute(
                """
                SELECT document_version_id, version, content_hash,
                       preview_text, section_count, chunk_count, created_at
                FROM document_versions
                WHERE organization_id = ? AND document_id = ?
                  AND version = ?
                """,
                (
                    identity.organization_id,
                    document_id,
                    row["current_version"],
                ),
            ).fetchone()
        published_summary = (
            row["visibility_scope"] == "organization"
            and _safe_summary_kind(row["document_kind"])
        )
        read_summary = (
            str(version["preview_text"] or "")[:2000]
            if version is not None and published_summary
            else ""
        )
        return {
            "documentId": document_id,
            "projectId": row["project_id"],
            "title": row["title"],
            "documentKind": row["document_kind"],
            "visibilityScope": row["visibility_scope"],
            "parseState": row["parse_state"],
            "lifecycleState": row["lifecycle_state"],
            "aggregateVersion": int(row["version"]),
            "contentVersion": int(version["version"]) if version else 0,
            "contentHash": version["content_hash"] if version else None,
            "sectionCount": int(version["section_count"]) if version else 0,
            "chunkCount": int(version["chunk_count"]) if version else 0,
            "sourceKind": row["source_kind"] or row["document_kind"],
            "fileName": row["file_name"] or row["title"],
            "mediaType": row["media_type"] or "",
            "byteSize": int(row["byte_size"] or 0),
            "readSummary": read_summary,
            "publishedSummary": published_summary,
            "updatedAt": row["updated_at"],
            "materialBoundary": {
                "sourceFileContentIncluded": False,
                "sourceFilePathsIncluded": False,
                "storageLocatorsIncluded": False,
                "unpublishedDocumentContentIncluded": False,
            },
        }

    def document_text(
        self,
        identity: SessionIdentity,
        *,
        document_id: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            row = self._document_row(
                connection,
                identity,
                document_id=document_id,
            )
            if (
                row["visibility_scope"] != "organization"
                or not _safe_summary_kind(row["document_kind"])
            ):
                raise RepositoryError(
                    403,
                    "source_content_not_shared",
                    "组织云只提供已发布摘要，不返回成员源文件正文",
                )
            version = connection.execute(
                """
                SELECT version, preview_text
                FROM document_versions
                WHERE organization_id = ? AND document_id = ?
                  AND version = ?
                """,
                (
                    identity.organization_id,
                    document_id,
                    row["current_version"],
                ),
            ).fetchone()
        return {
            "documentId": document_id,
            "title": row["title"],
            "kind": row["document_kind"],
            "content": str(version["preview_text"] or "")[:2000] if version else "",
            "contentVersion": int(version["version"]) if version else 0,
            "sourceScope": "organization_shared",
        }

    def archive_document(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        document_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = {
            "projectId": project_id,
            "documentId": document_id,
            "expectedVersion": expected_version,
            "targetState": "archived",
        }
        command_type = "knowledge_document.archived"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self._document_row(
                    connection,
                    identity,
                    document_id=document_id,
                    project_id=project_id,
                    require_edit=True,
                )
                current_version = int(row["version"])
                if expected_version != current_version:
                    raise RepositoryError(
                        409,
                        "document_version_conflict",
                        "资料已更新，请刷新后重试",
                    )
                now = utc_now()
                updated = connection.execute(
                    """
                    UPDATE knowledge_documents
                    SET lifecycle_state = 'archived', version = version + 1,
                        updated_at = ?
                    WHERE organization_id = ? AND document_id = ?
                      AND version = ?
                    """,
                    (
                        now,
                        identity.organization_id,
                        document_id,
                        current_version,
                    ),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "document_version_conflict",
                        "资料已更新，请刷新后重试",
                    )
                result = {
                    "deleted": True,
                    "documentId": document_id,
                    "fileName": row["file_name"] or row["title"],
                    "recycledPath": "",
                    "lifecycleState": "archived",
                    "version": current_version + 1,
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="knowledge_document",
                    aggregate_id=document_id,
                    expected_version=current_version,
                    before_version=current_version,
                    after_version=current_version + 1,
                    payload=payload,
                    payload_hash=payload_hash,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _duplicate_group_document_ids(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        project_id: str,
        group_key: str,
    ) -> set[str]:
        rows = connection.execute(
            """
            SELECT d.document_id, v.content_hash,
                   COALESCE(a.file_name, d.title) AS file_name
            FROM knowledge_documents d
            LEFT JOIN document_versions v
              ON v.organization_id = d.organization_id
             AND v.document_id = d.document_id
             AND v.version = d.current_version
            LEFT JOIN source_assets a
              ON a.organization_id = d.organization_id
             AND a.source_asset_id = d.source_asset_id
            WHERE d.organization_id = ? AND d.project_id = ?
              AND d.lifecycle_state = 'active'
            """,
            (identity.organization_id, project_id),
        ).fetchall()
        if group_key.startswith("hash:"):
            content_hash = group_key.removeprefix("hash:")
            matches = {
                str(row["document_id"])
                for row in rows
                if str(row["content_hash"] or "").strip() == content_hash
            }
        elif group_key.startswith("name:"):
            name_hash = group_key.removeprefix("name:")
            matches = {
                str(row["document_id"])
                for row in rows
                if not str(row["content_hash"] or "").strip()
                and sha256_text(
                    str(row["file_name"] or "").strip().lower()
                )
                == name_hash
            }
        else:
            raise RepositoryError(
                422,
                "duplicate_group_invalid",
                "重复资料分组标识无效",
            )
        if len(matches) < 2:
            raise RepositoryError(
                409,
                "duplicate_group_changed",
                "重复资料分组已变化，请刷新后重试",
            )
        return matches

    def resolve_duplicate_documents(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        group_key = str(payload.get("groupKey") or "").strip()
        action = str(payload.get("action") or "").strip()
        keep_ids = [
            str(value)
            for value in payload.get("keepV2DocumentIds") or []
            if str(value)
        ]
        delete_ids = [
            str(value)
            for value in payload.get("deleteV2DocumentIds") or []
            if str(value)
        ]
        documents = [
            {
                "documentId": str(item.get("documentId") or "").strip(),
                "expectedVersion": int(item.get("expectedVersion") or 0),
            }
            for item in payload.get("documents") or []
            if isinstance(item, Mapping)
        ]
        if not group_key or action not in {"delete_others", "keep_all"}:
            raise RepositoryError(
                422,
                "duplicate_resolution_invalid",
                "重复资料处置请求无效",
            )
        if (
            len(set(keep_ids)) != len(keep_ids)
            or len(set(delete_ids)) != len(delete_ids)
            or set(keep_ids) & set(delete_ids)
            or any(not item["documentId"] for item in documents)
            or len({item["documentId"] for item in documents}) != len(documents)
        ):
            raise RepositoryError(
                422,
                "duplicate_resolution_documents_invalid",
                "重复资料处置清单存在重复或冲突",
            )
        if action == "delete_others" and (
            not delete_ids
            or {item["documentId"] for item in documents} != set(delete_ids)
        ):
            raise RepositoryError(
                422,
                "duplicate_resolution_versions_required",
                "待移除资料必须提供完整版本",
            )
        if action == "keep_all" and (delete_ids or documents):
            raise RepositoryError(
                422,
                "duplicate_resolution_keep_all_invalid",
                "保留全部资料时不能包含待移除资料",
            )
        normalized_payload = {
            "projectId": project_id,
            "groupKey": group_key,
            "action": action,
            "keepV2DocumentIds": keep_ids,
            "deleteV2DocumentIds": delete_ids,
            "documents": documents,
            "migrateReferences": bool(payload.get("migrateReferences")),
            "note": str(payload.get("note") or ""),
        }
        command_type = "knowledge_documents.duplicates_resolved"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized_payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self._project_row(
                    connection,
                    identity,
                    project_id,
                    require_edit=True,
                )
                group_ids = self._duplicate_group_document_ids(
                    connection,
                    identity,
                    project_id=project_id,
                    group_key=group_key,
                )
                selected_ids = set(keep_ids) | set(delete_ids)
                if not selected_ids or not selected_ids.issubset(group_ids):
                    raise RepositoryError(
                        409,
                        "duplicate_group_changed",
                        "重复资料分组已变化，请刷新后重试",
                    )
                rows: list[tuple[sqlite3.Row, int]] = []
                expected_by_id = {
                    item["documentId"]: int(item["expectedVersion"])
                    for item in documents
                }
                for document_id in delete_ids:
                    row = self._document_row(
                        connection,
                        identity,
                        document_id=document_id,
                        project_id=project_id,
                        require_edit=True,
                    )
                    expected_version = expected_by_id[document_id]
                    if (
                        str(row["lifecycle_state"]) != "active"
                        or expected_version <= 0
                        or int(row["version"]) != expected_version
                    ):
                        raise RepositoryError(
                            409,
                            "document_version_conflict",
                            "资料已更新，请刷新后重试",
                        )
                    rows.append((row, expected_version))
                now = utc_now()
                archived = []
                for row, expected_version in rows:
                    document_id = str(row["document_id"])
                    changed = connection.execute(
                        """
                        UPDATE knowledge_documents
                        SET lifecycle_state = 'archived',
                            version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND document_id = ?
                          AND project_id = ? AND lifecycle_state = 'active'
                          AND version = ?
                        """,
                        (
                            now,
                            identity.organization_id,
                            document_id,
                            project_id,
                            expected_version,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "document_version_conflict",
                            "资料已更新，请刷新后重试",
                        )
                    archived.append(
                        {
                            "documentId": document_id,
                            "version": expected_version + 1,
                        }
                    )
                result = {
                    "action": action,
                    "groupKey": group_key,
                    "deletedCount": len(archived),
                    "archivedDocuments": archived,
                    "keptDocumentIds": keep_ids,
                    "migratedTaskAttachments": 0,
                    "migratedEvidenceRefs": 0,
                    "migratedAtomicFacts": 0,
                }
                aggregate_id = (
                    "duplicate-resolution:"
                    f"{sha256_text(f'{project_id}:{group_key}')}"
                )
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="duplicate_resolution",
                    aggregate_id=aggregate_id,
                    expected_version=None,
                    before_version=None,
                    after_version=1,
                    payload=normalized_payload,
                    payload_hash=payload_hash,
                    result=result,
                    audit_summary={
                        "projectId": project_id,
                        "groupKey": group_key,
                        "action": action,
                        "documentIds": delete_ids,
                        "expectedVersions": expected_by_id,
                    },
                    outbox_payload={
                        "projectId": project_id,
                        "groupKey": group_key,
                        "action": action,
                        "archivedDocuments": archived,
                    },
                    additional_outbox_events=[
                        {
                            "aggregateType": "knowledge_document",
                            "aggregateId": item["documentId"],
                            "aggregateVersion": item["version"],
                            "eventType": "knowledge_document.archived",
                            "payload": {
                                "projectId": project_id,
                                **item,
                            },
                        }
                        for item in archived
                    ],
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def duplicate_documents(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        visible_ids = self._visible_document_ids(identity, project_id=project_id)
        if not visible_ids:
            return {"groups": [], "generatedAt": utc_now()}
        placeholders = ",".join("?" for _ in visible_ids)
        with self.repository._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT d.document_id, d.title, d.document_kind, d.parse_state,
                       d.updated_at, d.source_asset_id, v.document_version_id,
                       v.content_hash, v.section_count, v.chunk_count,
                       COALESCE(a.file_name, d.title) AS file_name,
                       COALESCE(a.byte_size, 0) AS byte_size
                FROM knowledge_documents d
                LEFT JOIN document_versions v
                  ON v.document_id = d.document_id
                 AND v.version = d.current_version
                LEFT JOIN source_assets a
                  ON a.source_asset_id = d.source_asset_id
                WHERE d.organization_id = ?
                  AND d.project_id = ?
                  AND d.lifecycle_state = 'active'
                  AND d.document_id IN ({placeholders})
                ORDER BY d.updated_at DESC, d.document_id
                """,
                (
                    identity.organization_id,
                    project_id,
                    *sorted(visible_ids),
                ),
            ).fetchall()
            version_ids = {
                str(row["document_version_id"])
                for row in rows
                if row["document_version_id"]
            }
            source_ids = {
                str(row["source_asset_id"])
                for row in rows
                if row["source_asset_id"]
            }
            reference_counts: dict[str, int] = defaultdict(int)
            all_source_ids = version_ids | source_ids
            if all_source_ids:
                source_placeholders = ",".join("?" for _ in all_source_ids)
                for ref in connection.execute(
                    f"""
                    SELECT source_id, COUNT(*) AS count
                    FROM evidence_links
                    WHERE organization_id = ?
                      AND lifecycle_state = 'active'
                      AND source_id IN ({source_placeholders})
                    GROUP BY source_id
                    """,
                    (identity.organization_id, *sorted(all_source_ids)),
                ).fetchall():
                    reference_counts[str(ref["source_id"])] = int(ref["count"])

        by_hash: dict[str, list[sqlite3.Row]] = defaultdict(list)
        no_hash_by_name: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            content_hash = str(row["content_hash"] or "").strip()
            if content_hash:
                by_hash[content_hash].append(row)
            else:
                no_hash_by_name[str(row["file_name"] or "").strip().lower()].append(
                    row
                )

        def item(row: sqlite3.Row) -> dict[str, Any]:
            ref_count = reference_counts.get(
                str(row["document_version_id"] or ""), 0
            ) + reference_counts.get(str(row["source_asset_id"] or ""), 0)
            return {
                "id": row["document_id"],
                "documentId": row["document_id"],
                "fileName": row["file_name"],
                "kind": row["document_kind"],
                "managedPath": "",
                "originalPath": "",
                "contentHash": row["content_hash"] or "",
                "parseStatus": row["parse_state"],
                "sectionCount": int(row["section_count"] or 0),
                "chunkCount": int(row["chunk_count"] or 0),
                "importedAt": row["updated_at"],
                "fileSizeBytes": int(row["byte_size"] or 0),
                "refTaskAttachmentCount": 0,
                "refEvidenceCardCount": ref_count,
                "refAtomicFactCount": 0,
            }

        groups: list[dict[str, Any]] = []
        for content_hash, group_rows in sorted(by_hash.items()):
            if len(group_rows) < 2:
                continue
            documents = [item(row) for row in group_rows]
            groups.append(
                {
                    "groupKey": f"hash:{content_hash}",
                    "groupType": "same_content_hash",
                    "fileName": documents[0]["fileName"],
                    "contentHash": content_hash,
                    "count": len(documents),
                    "documents": documents,
                }
            )
        for file_name, group_rows in sorted(no_hash_by_name.items()):
            if not file_name or len(group_rows) < 2:
                continue
            documents = [item(row) for row in group_rows]
            groups.append(
                {
                    "groupKey": f"name:{sha256_text(file_name)}",
                    "groupType": "same_filename",
                    "fileName": documents[0]["fileName"],
                    "contentHash": "",
                    "count": len(documents),
                    "documents": documents,
                }
            )
        return {"groups": groups, "generatedAt": utc_now()}

    def knowledge_status(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        self.project_detail(identity, project_id=project_id)
        visible_ids = self._visible_document_ids(identity, project_id=project_id)
        if not visible_ids:
            return {
                "clientId": project_id,
                "confirmedFacts": 0,
                "pendingThoughts": 0,
                "activeContradictions": 0,
                "knowledgeGaps": 0,
                "weeklyDelta": {
                    "confirmedFacts": 0,
                    "activeContradictions": 0,
                    "newThoughts": 0,
                    "confirmedJudgments": 0,
                },
                "pendingJudgmentReevaluation": 0,
                "pendingProfileReview": 0,
                "pendingThoughtRefresh": 0,
                "recentFanoutCount": 0,
                "pendingActions": [],
                "generatedAt": utc_now(),
                "derivation": "strict_v2_authority",
            }
        placeholders = ",".join("?" for _ in visible_ids)
        week_ago = _iso_week_ago()
        with self.repository._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT d.document_id, d.title, d.document_kind,
                       d.project_assignment_state, d.parse_state,
                       d.visibility_scope, d.created_at, d.updated_at,
                       v.document_version_id, v.content_hash,
                       d.source_asset_id
                FROM knowledge_documents d
                LEFT JOIN document_versions v
                  ON v.document_id = d.document_id
                 AND v.version = d.current_version
                WHERE d.organization_id = ? AND d.project_id = ?
                  AND d.lifecycle_state = 'active'
                  AND d.document_id IN ({placeholders})
                """,
                (
                    identity.organization_id,
                    project_id,
                    *sorted(visible_ids),
                ),
            ).fetchall()
            target_ids = {
                str(row[0])
                for query in (
                    """
                    SELECT task_id FROM task_records
                    WHERE organization_id = ? AND project_id = ?
                    """,
                    """
                    SELECT event_line_id FROM event_line_records
                    WHERE organization_id = ? AND project_id = ?
                    """,
                    """
                    SELECT narrative_output_id FROM narrative_outputs
                    WHERE organization_id = ? AND project_id = ?
                    """,
                )
                for row in connection.execute(
                    query, (identity.organization_id, project_id)
                ).fetchall()
            }
            source_ids = {
                str(value)
                for row in rows
                for value in (row["document_version_id"], row["source_asset_id"])
                if value
            }
            evidence_count = 0
            weekly_evidence_count = 0
            if source_ids or target_ids:
                conditions: list[str] = []
                params: list[Any] = [identity.organization_id]
                if source_ids:
                    source_placeholders = ",".join("?" for _ in source_ids)
                    conditions.append(f"source_id IN ({source_placeholders})")
                    params.extend(sorted(source_ids))
                if target_ids:
                    target_placeholders = ",".join("?" for _ in target_ids)
                    conditions.append(f"target_id IN ({target_placeholders})")
                    params.extend(sorted(target_ids))
                evidence_rows = connection.execute(
                    f"""
                    SELECT created_at
                    FROM evidence_links
                    WHERE organization_id = ?
                      AND lifecycle_state = 'active'
                      AND ({" OR ".join(conditions)})
                    """,
                    tuple(params),
                ).fetchall()
                evidence_count = len(evidence_rows)
                weekly_evidence_count = sum(
                    1 for row in evidence_rows if str(row["created_at"]) >= week_ago
                )
            attempt_rows = connection.execute(
                f"""
                SELECT pa.processing_attempt_id, pa.document_id, pa.state,
                       pa.error_message, pa.created_at
                FROM processing_attempts pa
                WHERE pa.organization_id = ?
                  AND pa.document_id IN ({placeholders})
                """,
                (identity.organization_id, *sorted(visible_ids)),
            ).fetchall()

        hash_by_title: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            content_hash = str(row["content_hash"] or "").strip()
            if content_hash:
                hash_by_title[str(row["title"]).strip().lower()].add(content_hash)
        contradiction_count = sum(
            1 for hashes in hash_by_title.values() if len(hashes) > 1
        )
        pending_count = sum(
            1
            for row in rows
            if row["parse_state"] in {"queued", "processing", "partial_ready"}
        )
        gaps = [
            row
            for row in rows
            if row["project_assignment_state"] == "unassigned"
            or row["parse_state"] in {
                "not_requested",
                "failed",
                "missing_source",
            }
        ]
        failed_attempts = [
            row for row in attempt_rows if row["state"] == "failed"
        ]
        pending_actions = [
            {
                "actionType": "material_needs_retry",
                "entityId": row["document_id"] or row["processing_attempt_id"],
                "entityLabel": "资料处理失败",
                "reason": row["error_message"] or "资料处理失败，请重试",
                "triggeredAt": row["created_at"],
            }
            for row in failed_attempts[:20]
        ]
        confirmed_summaries = sum(
            1
            for row in rows
            if row["visibility_scope"] == "organization"
            and row["parse_state"] in {"ready", "partial_ready"}
            and _safe_summary_kind(row["document_kind"])
        )
        return {
            "clientId": project_id,
            "confirmedFacts": confirmed_summaries + evidence_count,
            "pendingThoughts": pending_count,
            "activeContradictions": contradiction_count,
            "knowledgeGaps": len(gaps),
            "weeklyDelta": {
                "confirmedFacts": sum(
                    1
                    for row in rows
                    if str(row["created_at"]) >= week_ago
                    and row["visibility_scope"] == "organization"
                    and _safe_summary_kind(row["document_kind"])
                )
                + weekly_evidence_count,
                "activeContradictions": sum(
                    1
                    for title, hashes in hash_by_title.items()
                    if title and len(hashes) > 1
                ),
                "newThoughts": sum(
                    1
                    for row in attempt_rows
                    if str(row["created_at"]) >= week_ago
                ),
                "confirmedJudgments": weekly_evidence_count,
            },
            "pendingJudgmentReevaluation": len(failed_attempts),
            "pendingProfileReview": len(gaps),
            "pendingThoughtRefresh": pending_count,
            "recentFanoutCount": len(pending_actions),
            "pendingActions": pending_actions,
            "generatedAt": utc_now(),
            "derivation": (
                "strict_v2: published summaries + evidence links + "
                "document parse states + processing attempts"
            ),
        }

    def fact_bundle(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        include_archived: bool = False,
        lite: bool = False,
    ) -> dict[str, Any]:
        project = self.project_detail(identity, project_id=project_id)["project"]
        if (
            project.get("lifecycleState") == "archived"
            and not include_archived
        ):
            raise RepositoryError(404, "project_missing", "项目已归档")
        snapshot = self.repository.business_snapshot(identity)
        event_lines = [
            item
            for item in snapshot.get("eventLines") or []
            if str(item.get("projectId") or "") == project_id
        ]
        tasks = [
            item
            for item in snapshot.get("tasks") or []
            if str(item.get("projectId") or "") == project_id
        ]
        membership_names = {
            str(item.get("membershipId")): str(item.get("displayName") or "")
            for item in self.repository.organization_snapshot(identity).get(
                "members", []
            )
        }
        task_ids = {str(item["taskId"]) for item in tasks}
        event_ids = {str(item["eventLineId"]) for item in event_lines}
        evidence_counts: dict[str, int] = defaultdict(int)
        commitment_rows: list[sqlite3.Row] = []
        document_facts: list[dict[str, Any]] = []
        key_decisions: list[dict[str, Any]] = []
        with self.repository._connection() as connection:
            target_ids = task_ids | event_ids
            if target_ids:
                placeholders = ",".join("?" for _ in target_ids)
                for row in connection.execute(
                    f"""
                    SELECT target_id, COUNT(*) AS count
                    FROM evidence_links
                    WHERE organization_id = ?
                      AND lifecycle_state = 'active'
                      AND target_id IN ({placeholders})
                    GROUP BY target_id
                    """,
                    (identity.organization_id, *sorted(target_ids)),
                ).fetchall():
                    evidence_counts[str(row["target_id"])] = int(row["count"])
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                commitment_rows = connection.execute(
                    f"""
                    SELECT *
                    FROM task_records
                    WHERE organization_id = ?
                      AND task_id IN ({placeholders})
                      AND task_kind = 'commitment'
                    ORDER BY updated_at DESC, task_id
                    """,
                    (identity.organization_id, *sorted(task_ids)),
                ).fetchall()
            visible_document_ids = self._visible_document_ids(
                identity,
                project_id=project_id,
            )
            if visible_document_ids:
                placeholders = ",".join("?" for _ in visible_document_ids)
                rows = connection.execute(
                    f"""
                    SELECT d.document_id, d.title, d.document_kind,
                           d.visibility_scope, d.parse_state, d.updated_at,
                           v.document_version_id, v.preview_text,
                           v.content_hash
                    FROM knowledge_documents d
                    JOIN document_versions v
                      ON v.document_id = d.document_id
                     AND v.version = d.current_version
                    WHERE d.organization_id = ?
                      AND d.project_id = ?
                      AND d.lifecycle_state = 'active'
                      AND d.document_id IN ({placeholders})
                    ORDER BY d.updated_at DESC, d.document_id
                    """,
                    (
                        identity.organization_id,
                        project_id,
                        *sorted(visible_document_ids),
                    ),
                ).fetchall()
                document_facts = [
                    {
                        "id": row["document_version_id"],
                        "subject_text": row["title"],
                        "attribute": row["document_kind"],
                        "value_text": str(row["preview_text"] or "")[:500],
                        "confidence": 1.0,
                        "source_v2_document_id": row["document_id"],
                        "source_v2_chunk_id": None,
                        "evidence_text": None,
                        "status": "confirmed",
                        "updated_at": row["updated_at"],
                    }
                    for row in rows
                    if row["visibility_scope"] == "organization"
                    and row["parse_state"] in {"ready", "partial_ready"}
                    and _safe_summary_kind(row["document_kind"])
                    and str(row["preview_text"] or "").strip()
                ]
            key_decisions = [
                {
                    "id": row["narrative_output_id"],
                    "title": row["title"],
                    "kind": row["output_kind"],
                    "status": row["lifecycle_state"],
                    "version": int(row["latest_version"]),
                    "updatedAt": row["updated_at"],
                }
                for row in connection.execute(
                    """
                    SELECT narrative_output_id, title, output_kind,
                           lifecycle_state, latest_version, updated_at
                    FROM narrative_outputs
                    WHERE organization_id = ? AND project_id = ?
                      AND lifecycle_state IN ('active', 'stale')
                    ORDER BY updated_at DESC, narrative_output_id
                    """,
                    (identity.organization_id, project_id),
                ).fetchall()
            ]

        mapped_events = [
            {
                "id": item["eventLineId"],
                "name": item.get("name") or "",
                "kind": "event_line",
                "status": item.get("lifecycleState") or "active",
                "stage": item.get("lifecycleState") or "active",
                "summary": item.get("background") or "",
                "intent": item.get("goal") or "",
                "current_blocker": "",
                "recent_decision": "",
                "next_step": "",
                "evidence_count": evidence_counts.get(
                    str(item["eventLineId"]), 0
                ),
                "owner_id": item.get("createdByMembershipId"),
                "owner_name": membership_names.get(
                    str(item.get("createdByMembershipId") or "")
                ),
                "primary_client_id": project_id,
                "primary_client_name": project.get("name") or "",
                "created_at": item.get("createdAt")
                or item.get("updatedAt")
                or utc_now(),
                "updated_at": item.get("updatedAt") or utc_now(),
            }
            for item in event_lines
        ]
        mapped_tasks = []
        for item in tasks:
            collaborators = list(item.get("collaborators") or [])
            owner = next(
                (
                    collaborator
                    for collaborator in collaborators
                    if collaborator.get("role") == "owner"
                ),
                None,
            )
            mapped_tasks.append(
                {
                    "id": item["taskId"],
                    "title": item.get("title") or "",
                    "description_preview": str(
                        item.get("description") or ""
                    )[:300],
                    "status": item.get("lifecycleState") or "todo",
                    "priority": item.get("priority") or "normal",
                    "progress_status": item.get("lifecycleState") or "todo",
                    "owner_id": (owner or {}).get("membershipId"),
                    "owner_name": (owner or {}).get("displayName") or "",
                    "creator_id": item.get("createdByMembershipId") or "",
                    "deadline_at": item.get("deadlineAt"),
                    "due_date": item.get("dueDate"),
                    "scheduled_start_at": item.get("scheduledStartAt"),
                    "completed_at": item.get("completedAt"),
                    "event_line_id": item.get("eventLineId"),
                    "business_category": None,
                    "current_blocker": "",
                    "next_action": "",
                    "recent_decision": "",
                    "evidence_count": evidence_counts.get(
                        str(item["taskId"]), 0
                    ),
                    "source_type": "strict_v2",
                    "source_id": None,
                    "created_at": item.get("createdAt")
                    or item.get("updatedAt")
                    or utc_now(),
                    "updated_at": item.get("updatedAt") or utc_now(),
                }
            )
        commitments = [
            {
                "id": row["task_id"],
                "committer": membership_names.get(
                    str(row["created_by_membership_id"] or ""), ""
                ),
                "recipient": "",
                "commitment_type": "task_commitment",
                "content": row["description"] or row["title"],
                "deadline": row["deadline_at"] or row["due_date"],
                "status": row["lifecycle_state"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in commitment_rows
        ]
        counts = {
            "event_lines": len(mapped_events),
            "tasks": len(mapped_tasks),
            "commitments": len(commitments),
            "dna_documents": len(document_facts),
            "atomic_facts": len(document_facts),
            "key_decisions": len(key_decisions),
        }
        return {
            "client": {
                "id": project_id,
                "name": project.get("name") or "",
                "alias": project.get("alias") or "",
                "domain": project.get("domain") or "",
                "type": "project",
                "intro": project.get("summary") or "",
                "stage": project.get("lifecycleState") or "active",
                "color": project.get("color") or "#5B7BFE",
                "created_at": project.get("updatedAt") or utc_now(),
                "updated_at": project.get("updatedAt") or utc_now(),
            },
            "event_lines": [] if lite else mapped_events,
            "tasks": [] if lite else mapped_tasks,
            "commitments": [] if lite else commitments,
            "dna_documents": (
                []
                if lite
                else [
                    {
                        "module_key": fact["id"],
                        "title": fact["subject_text"],
                        "summary": fact["value_text"],
                        "file_name": fact["subject_text"],
                        "source_kind": "organization_shared_summary",
                        "updated_at": fact["updated_at"],
                        "updated_by": "",
                        "has_full_content": False,
                    }
                    for fact in document_facts
                ]
            ),
            "atomic_facts": [] if lite else document_facts,
            "key_decisions": [] if lite else key_decisions,
            "snapshot_at": utc_now(),
            "sources": {
                "client": "work_projects",
                "event_lines": "event_line_records+evidence_links",
                "tasks": "task_records+task_collaborators+evidence_links",
                "commitments": "task_records(task_kind=commitment)",
                "dna_documents": "knowledge_documents+document_versions",
                "atomic_facts": "published summary projections",
                "key_decisions": "narrative_outputs",
            },
            "counts": counts,
        }

    def _published_intelligence(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> list[dict[str, Any]]:
        self.project_detail(identity, project_id=project_id)
        snapshot = self.repository.business_snapshot(identity)
        visible_ids = {
            str(item["intelligenceId"])
            for item in snapshot.get("intelligence") or []
            if str(item.get("projectId") or "") == project_id
        }
        if not visible_ids:
            return []
        placeholders = ",".join("?" for _ in visible_ids)
        with self.repository._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT intelligence_id, title, summary, record_kind,
                       source_payload_json, created_at, updated_at
                FROM intelligence_records
                WHERE organization_id = ? AND project_id = ?
                  AND intelligence_id IN ({placeholders})
                  AND visibility_scope = 'organization'
                  AND status = 'accepted'
                ORDER BY updated_at DESC, intelligence_id
                """,
                (
                    identity.organization_id,
                    project_id,
                    *sorted(visible_ids),
                ),
            ).fetchall()
        result = []
        for row in rows:
            try:
                payload = json.loads(str(row["source_payload_json"] or "{}"))
            except ValueError:
                payload = {}
            result.append(
                {
                    "id": str(row["intelligence_id"]),
                    "title": str(row["title"]),
                    "summary": str(row["summary"] or ""),
                    "kind": str(row["record_kind"]),
                    "payload": payload if isinstance(payload, dict) else {},
                    "createdAt": str(row["created_at"]),
                    "updatedAt": str(row["updated_at"]),
                }
            )
        return result

    @staticmethod
    def _payload_items(
        record: Mapping[str, Any],
        *keys: str,
    ) -> list[Any]:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            return []
        result: list[Any] = []
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                result.extend(value)
        return result

    @staticmethod
    def _knowledge_decisions(
        records: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        decisions = []
        for record in reversed(list(records)):
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            raw_decisions = payload.get("knowledgeDecisions")
            if not isinstance(raw_decisions, list):
                continue
            for raw in raw_decisions:
                if not isinstance(raw, dict):
                    continue
                decision = dict(raw)
                decision["_recordCreatedAt"] = record.get("createdAt")
                decision["_recordUpdatedAt"] = record.get("updatedAt")
                decisions.append(decision)
        return decisions

    def derived_entities(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        entity_type: str | None = None,
        query: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        records = self._published_intelligence(identity, project_id=project_id)
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for record in records:
            for raw in self._payload_items(
                record, "entities", "namedEntities", "named_entities"
            ):
                if isinstance(raw, str):
                    item = {"name": raw, "type": "project"}
                elif isinstance(raw, dict):
                    item = raw
                else:
                    continue
                name = str(
                    item.get("displayName")
                    or item.get("name")
                    or item.get("normalizedName")
                    or ""
                ).strip()
                kind = str(
                    item.get("entityType") or item.get("type") or "project"
                ).strip()
                if kind not in {
                    "person",
                    "company",
                    "project",
                    "product",
                    "competitor",
                    "amount",
                    "date",
                }:
                    kind = "project"
                if not name:
                    continue
                normalized = name.casefold()
                key = (kind, normalized)
                aliases = item.get("aliases")
                alias_values = (
                    {str(value) for value in aliases if str(value).strip()}
                    if isinstance(aliases, list)
                    else set()
                )
                attributes = item.get("attributes")
                attribute_values = (
                    {
                        str(key): str(value)
                        for key, value in attributes.items()
                        if isinstance(key, str)
                    }
                    if isinstance(attributes, dict)
                    else {}
                )
                existing = merged.get(key)
                if existing is None:
                    merged[key] = {
                        "id": f"derived_entity_{sha256_text(project_id + '|' + kind + '|' + normalized)[:24]}",
                        "clientId": project_id,
                        "entityType": kind,
                        "normalizedName": normalized,
                        "displayName": name,
                        "aliases": alias_values,
                        "attributes": attribute_values,
                        "mentionCount": 1,
                        "confidence": float(item.get("confidence") or 1.0),
                        "firstSeenAt": record["createdAt"],
                        "lastSeenAt": record["updatedAt"],
                        "status": "active",
                    }
                else:
                    existing["aliases"].update(alias_values)
                    existing["attributes"].update(attribute_values)
                    existing["mentionCount"] += 1
                    existing["confidence"] = max(
                        float(existing["confidence"]),
                        float(item.get("confidence") or 1.0),
                    )
                    existing["lastSeenAt"] = max(
                        str(existing["lastSeenAt"]),
                        str(record["updatedAt"]),
                    )
        items_by_id = {
            str(item["id"]): item for item in merged.values()
        }
        for decision in self._knowledge_decisions(records):
            kind = str(decision.get("decisionKind") or "")
            target_id = str(decision.get("targetId") or "")
            data = decision.get("data")
            data = data if isinstance(data, dict) else {}
            if kind == "entity_verify":
                item = items_by_id.get(target_id)
                if item is None:
                    continue
                verified_status = str(data.get("status") or "")
                if verified_status == "noise":
                    items_by_id.pop(target_id, None)
                elif verified_status == "alias_of":
                    survivor_id = str(data.get("aliasTargetId") or "")
                    survivor = items_by_id.get(survivor_id)
                    if survivor is not None and survivor_id != target_id:
                        survivor["aliases"].add(str(item["displayName"]))
                        survivor["aliases"].update(item["aliases"])
                        survivor["mentionCount"] += int(item["mentionCount"])
                        items_by_id.pop(target_id, None)
                elif verified_status == "canonical":
                    item["status"] = "canonical"
                    item["verifiedAt"] = decision.get("decidedAt")
            elif kind == "entity_merge":
                survivor_id = str(data.get("survivingEntityId") or "")
                merged_id = str(data.get("mergedEntityId") or target_id)
                survivor = items_by_id.get(survivor_id)
                merged_item = items_by_id.get(merged_id)
                if (
                    survivor is not None
                    and merged_item is not None
                    and survivor_id != merged_id
                ):
                    survivor["aliases"].add(
                        str(merged_item["displayName"])
                    )
                    survivor["aliases"].update(merged_item["aliases"])
                    survivor["attributes"].update(merged_item["attributes"])
                    survivor["mentionCount"] += int(
                        merged_item["mentionCount"]
                    )
                    items_by_id.pop(merged_id, None)
        items = []
        lowered_query = query.strip().casefold()
        for item in items_by_id.values():
            if entity_type and item["entityType"] != entity_type:
                continue
            if lowered_query and lowered_query not in item["displayName"].casefold():
                continue
            item["aliases"] = sorted(item["aliases"])
            items.append(item)
        items.sort(key=lambda item: (-int(item["mentionCount"]), item["displayName"]))
        return {
            "entities": items[max(0, offset) : max(0, offset) + max(1, limit)],
            "total": len(items),
            "derivation": "accepted organization intelligence source_payload_json",
        }

    def entity_merge_candidates(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        entities = self.derived_entities(
            identity,
            project_id=project_id,
            limit=1000,
        )["entities"]
        candidates = []
        for index, left in enumerate(entities):
            for right in entities[index + 1 :]:
                if left["entityType"] != right["entityType"]:
                    continue
                similarity = SequenceMatcher(
                    None,
                    str(left["normalizedName"]),
                    str(right["normalizedName"]),
                ).ratio()
                aliases = {
                    value.casefold()
                    for value in [
                        *left.get("aliases", []),
                        *right.get("aliases", []),
                    ]
                }
                if similarity < 0.72 and not (
                    str(left["normalizedName"]) in aliases
                    or str(right["normalizedName"]) in aliases
                ):
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
                        "reason": "同类实体名称或已发布别名高度相似",
                    }
                )
        candidates.sort(key=lambda item: -float(item["similarity"]))
        return {"candidates": candidates[: max(1, min(limit, 100))]}

    def derived_glossary(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        query: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        records = self._published_intelligence(identity, project_id=project_id)
        entries: dict[str, dict[str, Any]] = {}
        for record in reversed(records):
            for raw in self._payload_items(
                record, "glossary", "terms", "glossaryEntries"
            ):
                if isinstance(raw, str):
                    item = {"term": raw}
                elif isinstance(raw, dict):
                    item = raw
                else:
                    continue
                term = str(item.get("term") or item.get("name") or "").strip()
                if not term:
                    continue
                normalized = term.casefold()
                aliases = item.get("aliases")
                entries[normalized] = {
                    "id": f"derived_glossary_{sha256_text(project_id + '|' + normalized)[:24]}",
                    "clientId": project_id,
                    "term": term,
                    "normalizedTerm": normalized,
                    "definition": str(
                        item.get("definition") or record["summary"] or ""
                    )[:1000],
                    "aliases": (
                        sorted(
                            {
                                str(value)
                                for value in aliases
                                if str(value).strip()
                            }
                        )
                        if isinstance(aliases, list)
                        else []
                    ),
                    "category": str(
                        item.get("category") or record["kind"] or "项目知识"
                    ),
                    "createdAt": record["createdAt"],
                    "updatedAt": record["updatedAt"],
                }
        for decision in self._knowledge_decisions(records):
            kind = str(decision.get("decisionKind") or "")
            target_id = str(decision.get("targetId") or "")
            if kind == "glossary_delete":
                entries = {
                    key: value
                    for key, value in entries.items()
                    if str(value.get("id") or "") != target_id
                }
                continue
            if kind != "glossary_upsert":
                continue
            data = decision.get("data")
            if not isinstance(data, dict):
                continue
            term = str(data.get("term") or "").strip()
            if not term:
                continue
            entries = {
                key: value
                for key, value in entries.items()
                if str(value.get("id") or "") != target_id
            }
            normalized = term.casefold()
            entries[normalized] = {
                "id": str(data.get("id") or target_id),
                "clientId": project_id,
                "term": term,
                "normalizedTerm": normalized,
                "definition": str(data.get("definition") or "")[:1000],
                "aliases": sorted(
                    {
                        str(value)
                        for value in data.get("aliases") or []
                        if str(value).strip()
                    }
                ),
                "category": str(data.get("category") or "项目知识"),
                "createdAt": str(
                    data.get("createdAt")
                    or decision.get("_recordCreatedAt")
                    or ""
                ),
                "updatedAt": str(
                    data.get("updatedAt")
                    or decision.get("_recordUpdatedAt")
                    or ""
                ),
            }
        lowered_query = query.strip().casefold()
        values = [
            entry
            for entry in entries.values()
            if not lowered_query
            or lowered_query in entry["normalizedTerm"]
            or lowered_query in entry["definition"].casefold()
        ]
        values.sort(key=lambda entry: entry["normalizedTerm"])
        return {
            "entries": values[max(0, offset) : max(0, offset) + max(1, limit)],
            "total": len(values),
            "derivation": "accepted organization intelligence glossary payloads",
        }

    def glossary_attributes(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        status: str | None = None,
    ) -> dict[str, Any]:
        records = self._published_intelligence(identity, project_id=project_id)
        attributes = []
        for record in records:
            for index, raw in enumerate(
                self._payload_items(
                    record,
                    "glossaryAttributes",
                    "glossary_attributes",
                )
            ):
                if not isinstance(raw, dict):
                    continue
                verification = str(
                    raw.get("verification_status")
                    or raw.get("verificationStatus")
                    or "pending"
                )
                if verification not in {"pending", "verified", "rejected"}:
                    verification = "pending"
                term = str(raw.get("term") or "").strip()
                attribute_name = str(
                    raw.get("attribute_name")
                    or raw.get("attributeName")
                    or ""
                ).strip()
                if not term or not attribute_name:
                    continue
                value_text = str(
                    raw.get("value_text") or raw.get("valueText") or ""
                )
                attributes.append(
                    {
                        "id": (
                            "derived_glossary_attr_"
                            + sha256_text(
                                f"{record['id']}|{index}|{term}|{attribute_name}"
                            )[:24]
                        ),
                        "term_id": f"term_{sha256_text(project_id + '|' + term.casefold())[:24]}",
                        "term": term,
                        "attribute_name": attribute_name,
                        "value_category": str(
                            raw.get("value_category")
                            or raw.get("valueCategory")
                            or "text"
                        ),
                        "value_text": value_text,
                        "value_normalized": raw.get("value_normalized")
                        or raw.get("valueNormalized"),
                        "value_unit": str(
                            raw.get("value_unit") or raw.get("valueUnit") or ""
                        ),
                        "scope": str(raw.get("scope") or ""),
                        "as_of_date": raw.get("as_of_date")
                        or raw.get("asOfDate"),
                        "source_type": "intelligence_record",
                        "source_evidence": record["summary"][:500],
                        "source_doc_id": None,
                        "source_doc_title": record["title"],
                        "source_doc_path": None,
                        "confidence": float(raw.get("confidence") or 1.0),
                        "verification_status": verification,
                        "verified_by": raw.get("verified_by")
                        or raw.get("verifiedBy"),
                        "verified_at": raw.get("verified_at")
                        or raw.get("verifiedAt"),
                        "rejection_note": str(
                            raw.get("rejection_note")
                            or raw.get("rejectionNote")
                            or ""
                        ),
                        "created_at": record["createdAt"],
                        "updated_at": record["updatedAt"],
                    }
                )
        by_id = {str(item["id"]): item for item in attributes}
        for decision in self._knowledge_decisions(records):
            kind = str(decision.get("decisionKind") or "")
            data = decision.get("data")
            data = data if isinstance(data, dict) else {}
            if kind == "glossary_attribute_review":
                target = by_id.get(str(decision.get("targetId") or ""))
                if target is None:
                    continue
                target["verification_status"] = str(
                    data.get("status") or target["verification_status"]
                )
                field_map = {
                    "termId": "term_id",
                    "attributeName": "attribute_name",
                    "valueText": "value_text",
                    "valueUnit": "value_unit",
                    "scope": "scope",
                    "asOfDate": "as_of_date",
                }
                for source, target_key in field_map.items():
                    if source in data:
                        target[target_key] = data[source]
                target["verified_by"] = data.get("verifiedBy")
                target["verified_at"] = decision.get("decidedAt")
                target["rejection_note"] = str(data.get("note") or "")
                target["updated_at"] = str(
                    decision.get("_recordUpdatedAt") or target["updated_at"]
                )
        attributes = list(by_id.values())
        if status:
            attributes = [
                item
                for item in attributes
                if item["verification_status"] == status
            ]
        return {
            "attributes": attributes,
            "derivation": "accepted organization intelligence attribute payloads",
        }

    def glossary_drift_alerts(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        status: str = "pending",
    ) -> dict[str, Any]:
        attributes = self.glossary_attributes(
            identity,
            project_id=project_id,
        )["attributes"]
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(
            list
        )
        for item in attributes:
            grouped[
                (
                    str(item["term"]).casefold(),
                    str(item["attribute_name"]).casefold(),
                    str(item.get("scope") or "").casefold(),
                )
            ].append(item)
        alerts = []
        for items in grouped.values():
            verified = next(
                (
                    item
                    for item in items
                    if item["verification_status"] == "verified"
                ),
                None,
            )
            if verified is None:
                continue
            for candidate in items:
                if (
                    candidate["id"] == verified["id"]
                    or candidate["value_text"] == verified["value_text"]
                ):
                    continue
                alert_id = (
                    "derived_glossary_drift_"
                    + sha256_text(verified["id"] + "|" + candidate["id"])[:24]
                )
                alert_status = "pending"
                alerts.append(
                    {
                        "id": alert_id,
                        "client_id": project_id,
                        "glossary_attribute_id": verified["id"],
                        "new_fact_id": candidate["id"],
                        "verified_value_text": verified["value_text"],
                        "new_value_text": candidate["value_text"],
                        "severity": "high",
                        "review_status": alert_status,
                        "review_note": "",
                        "detected_at": candidate["updated_at"],
                        "reviewed_at": None,
                        "reviewed_by": None,
                        "term": verified["term"],
                        "attribute_name": verified["attribute_name"],
                        "scope": verified.get("scope"),
                        "as_of_date": verified.get("as_of_date"),
                    }
                )
        review_by_id = {
            str(decision.get("targetId") or ""): decision
            for decision in self._knowledge_decisions(
                self._published_intelligence(
                    identity,
                    project_id=project_id,
                )
            )
            if str(decision.get("decisionKind") or "")
            == "glossary_drift_review"
        }
        reviewed_alerts = []
        for alert in alerts:
            decision = review_by_id.get(str(alert["id"]))
            if decision is not None:
                data = decision.get("data")
                data = data if isinstance(data, dict) else {}
                alert["review_status"] = (
                    "resolved"
                    if str(data.get("action") or "") == "update_glossary"
                    else "dismissed"
                )
                alert["review_note"] = str(data.get("note") or "")
                alert["reviewed_at"] = decision.get("decidedAt")
                alert["reviewed_by"] = data.get("reviewedBy")
            if alert["review_status"] == status:
                reviewed_alerts.append(alert)
        return {
            "alerts": reviewed_alerts,
            "derivation": "verified attribute versus later published attribute",
        }

    def derived_contradictions(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        status: str = "pending",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        attributes = self.glossary_attributes(
            identity,
            project_id=project_id,
        )["attributes"]
        facts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for item in attributes:
            facts[
                (
                    str(item["term"]).casefold(),
                    str(item["attribute_name"]).casefold(),
                )
            ].append(item)
        contradictions = []
        for items in facts.values():
            for index, left in enumerate(items):
                for right in items[index + 1 :]:
                    if left["value_text"] == right["value_text"]:
                        continue
                    contradiction_id = (
                        "derived_contradiction_"
                        + sha256_text(left["id"] + "|" + right["id"])[:24]
                    )
                    review_status = "pending"
                    contradictions.append(
                        {
                            "id": contradiction_id,
                            "clientId": project_id,
                            "subjectText": left["term"],
                            "attribute": left["attribute_name"],
                            "valueA": left["value_text"],
                            "valueB": right["value_text"],
                            "evidenceA": left["source_evidence"],
                            "evidenceB": right["source_evidence"],
                            "factAId": left["id"],
                            "factBId": right["id"],
                            "factAAt": left["updated_at"],
                            "factBAt": right["updated_at"],
                            "docAFileName": left["source_doc_title"],
                            "docAImportedAt": left["updated_at"],
                            "docAOriginalPath": None,
                            "docBFileName": right["source_doc_title"],
                            "docBImportedAt": right["updated_at"],
                            "docBOriginalPath": None,
                            "contradictionType": "value_diff",
                            "severity": "high",
                            "reviewStatus": review_status,
                            "resolutionNote": None,
                            "detectedAt": max(
                                left["updated_at"], right["updated_at"]
                            ),
                        }
                    )
        review_by_id = {
            str(decision.get("targetId") or ""): decision
            for decision in self._knowledge_decisions(
                self._published_intelligence(
                    identity,
                    project_id=project_id,
                )
            )
            if str(decision.get("decisionKind") or "")
            == "contradiction_review"
        }
        filtered = []
        for contradiction in contradictions:
            decision = review_by_id.get(str(contradiction["id"]))
            if decision is not None:
                data = decision.get("data")
                data = data if isinstance(data, dict) else {}
                contradiction["reviewStatus"] = str(
                    data.get("reviewStatus") or "resolved"
                )
                contradiction["resolutionNote"] = str(
                    data.get("resolutionNote") or ""
                )
                contradiction["acceptedFactId"] = data.get(
                    "acceptedFactId"
                )
                contradiction["reviewedAt"] = decision.get("decidedAt")
            if contradiction["reviewStatus"] == status:
                filtered.append(contradiction)
        contradictions = filtered
        contradictions.sort(key=lambda item: item["detectedAt"], reverse=True)
        return {
            "contradictions": contradictions[
                max(0, offset) : max(0, offset) + max(1, limit)
            ],
            "total": len(contradictions),
            "derivation": "published glossary attribute value differences",
        }

    def _publish_knowledge_decision(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        decision_kind: str,
        target_id: str,
        data: Mapping[str, Any],
        title: str,
        summary: str,
        result_payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        stable_data = {
            key: value
            for key, value in dict(data).items()
            if key
            not in {
                "createdAt",
                "updatedAt",
                "verifiedAt",
                "reviewedAt",
            }
        }
        normalized = {
            "projectId": project_id,
            "decisionKind": decision_kind,
            "targetId": target_id,
            "data": stable_data,
        }
        command_type = f"project_knowledge.{decision_kind}"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self._project_row(
                    connection,
                    identity,
                    project_id,
                    require_edit=True,
                )
                now = utc_now()
                intelligence_id = new_id()
                decision = {
                    "decisionId": intelligence_id,
                    "decisionKind": decision_kind,
                    "targetId": target_id,
                    "data": dict(data),
                    "decidedBy": identity.membership_id,
                    "decidedAt": now,
                }
                connection.execute(
                    """
                    INSERT INTO intelligence_records (
                        intelligence_id, organization_id, project_id, title,
                        summary, source_url, record_kind, status,
                        visibility_scope, created_by_membership_id,
                        source_payload_json, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '',
                              'project_knowledge_decision', 'accepted',
                              'organization', ?, ?, 1, ?, ?)
                    """,
                    (
                        intelligence_id,
                        identity.organization_id,
                        project_id,
                        title[:300],
                        summary[:2000],
                        identity.membership_id,
                        canonical_json(
                            {"knowledgeDecisions": [decision]}
                        ),
                        now,
                        now,
                    ),
                )
                published = self._insert_shared_summary(
                    connection,
                    identity,
                    project_id=project_id,
                    title=f"{title} · 裁决摘要",
                    summary=summary,
                    generator_version="project-knowledge-decision-summary-v1",
                    now=now,
                )
                result = {
                    **dict(result_payload),
                    "decisionId": intelligence_id,
                    "decidedAt": now,
                    "knowledgeDocumentId": published["documentId"],
                    "knowledgeDocumentVersion": published["version"],
                    "documentVersionId": published["documentVersionId"],
                    "publishedKnowledge": published,
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="intelligence",
                    aggregate_id=intelligence_id,
                    expected_version=None,
                    before_version=None,
                    after_version=1,
                    payload=normalized,
                    payload_hash=payload_hash,
                    result=result,
                    audit_summary={
                        "projectId": project_id,
                        "decisionKind": decision_kind,
                        "targetId": target_id,
                        "knowledgeDocumentId": published["documentId"],
                        "knowledgeDocumentVersion": published["version"],
                        "contentHash": published["contentHash"],
                    },
                    outbox_payload={
                        "intelligenceId": intelligence_id,
                        "version": 1,
                        "projectId": project_id,
                        "decisionKind": decision_kind,
                        "targetId": target_id,
                        "knowledgeDocumentId": published["documentId"],
                        "knowledgeDocumentVersion": published["version"],
                        "documentVersionId": published[
                            "documentVersionId"
                        ],
                        "contentHash": published["contentHash"],
                    },
                    additional_outbox_events=(
                        {
                            "aggregateType": "knowledge_document",
                            "aggregateId": published["documentId"],
                            "aggregateVersion": published["version"],
                            "eventType": (
                                "project_knowledge.decision_summary_published"
                            ),
                            "payload": {
                                "projectId": project_id,
                                "intelligenceId": intelligence_id,
                                "decisionKind": decision_kind,
                                "targetId": target_id,
                                "knowledgeDocumentId": published[
                                    "documentId"
                                ],
                                "knowledgeDocumentVersion": published[
                                    "version"
                                ],
                                "documentVersionId": published[
                                    "documentVersionId"
                                ],
                                "contentHash": published["contentHash"],
                                "sourceType": published["sourceType"],
                            },
                        },
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def _visible_project_ids(
        self,
        identity: SessionIdentity,
    ) -> list[str]:
        return [
            str(project["projectId"])
            for project in self.list_projects(identity)["projects"]
            if str(project.get("lifecycleState") or "")
            in {"active", "frozen"}
        ]

    def _decision_target_project(
        self,
        identity: SessionIdentity,
        *,
        target_id: str,
    ) -> str | None:
        for project_id in self._visible_project_ids(identity):
            records = self._published_intelligence(
                identity,
                project_id=project_id,
            )
            if any(
                str(decision.get("targetId") or "") == target_id
                for decision in self._knowledge_decisions(records)
            ):
                return project_id
        return None

    def _find_glossary_entry(
        self,
        identity: SessionIdentity,
        *,
        entry_id: str,
    ) -> tuple[str, dict[str, Any]]:
        for project_id in self._visible_project_ids(identity):
            entries = self.derived_glossary(
                identity,
                project_id=project_id,
                limit=1000,
            )["entries"]
            for entry in entries:
                if str(entry["id"]) == entry_id:
                    return project_id, dict(entry)
        raise RepositoryError(404, "glossary_entry_missing", "字典词条不存在")

    def create_glossary_entry(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.project_detail(identity, project_id=project_id)
        term = str(payload.get("term") or "").strip()
        if not term:
            raise RepositoryError(422, "glossary_term_required", "请输入词条")
        now = utc_now()
        entry_id = (
            "derived_glossary_"
            + sha256_text(project_id + "|" + term.casefold())[:24]
        )
        entry = {
            "id": entry_id,
            "clientId": project_id,
            "term": term,
            "normalizedTerm": term.casefold(),
            "definition": str(payload.get("definition") or "")[:1000],
            "aliases": sorted(
                {
                    str(value)
                    for value in payload.get("aliases") or []
                    if str(value).strip()
                }
            ),
            "category": str(payload.get("category") or "项目知识")[:100],
            "createdAt": now,
            "updatedAt": now,
        }
        return self._publish_knowledge_decision(
            identity,
            project_id=project_id,
            decision_kind="glossary_upsert",
            target_id=entry_id,
            data=entry,
            title=f"字典词条：{term}",
            summary=(
                f"项目字典新增词条“{term}”。"
                f"定义：{entry['definition'] or '未填写'}"
            ),
            result_payload={"entry": entry},
            idempotency_key=idempotency_key,
        )

    def update_glossary_entry(
        self,
        identity: SessionIdentity,
        *,
        entry_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        project_id, current = self._find_glossary_entry(
            identity,
            entry_id=entry_id,
        )
        updated = dict(current)
        for key in ("term", "definition", "category"):
            if key in payload:
                updated[key] = str(payload.get(key) or "").strip()
        if not updated["term"]:
            raise RepositoryError(422, "glossary_term_required", "请输入词条")
        if "aliases" in payload:
            updated["aliases"] = sorted(
                {
                    str(value)
                    for value in payload.get("aliases") or []
                    if str(value).strip()
                }
            )
        updated["normalizedTerm"] = str(updated["term"]).casefold()
        updated["updatedAt"] = utc_now()
        return self._publish_knowledge_decision(
            identity,
            project_id=project_id,
            decision_kind="glossary_upsert",
            target_id=entry_id,
            data=updated,
            title=f"字典词条更新：{updated['term']}",
            summary=(
                f"项目字典词条“{updated['term']}”已更新。"
                f"定义：{updated['definition'] or '未填写'}"
            ),
            result_payload={"entry": updated},
            idempotency_key=idempotency_key,
        )

    def delete_glossary_entry(
        self,
        identity: SessionIdentity,
        *,
        entry_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        try:
            project_id, current = self._find_glossary_entry(
                identity,
                entry_id=entry_id,
            )
        except RepositoryError:
            project_id = self._decision_target_project(
                identity,
                target_id=entry_id,
            )
            if project_id is None:
                raise
            current = {"term": entry_id}
        return self._publish_knowledge_decision(
            identity,
            project_id=project_id,
            decision_kind="glossary_delete",
            target_id=entry_id,
            data={"term": current["term"]},
            title=f"字典词条删除：{current['term']}",
            summary=f"项目字典词条“{current['term']}”已删除。",
            result_payload={"status": "deleted", "entryId": entry_id},
            idempotency_key=idempotency_key,
        )

    def _find_entity(
        self,
        identity: SessionIdentity,
        *,
        entity_id: str,
    ) -> tuple[str, dict[str, Any]]:
        for project_id in self._visible_project_ids(identity):
            entities = self.derived_entities(
                identity,
                project_id=project_id,
                limit=1000,
            )["entities"]
            for entity in entities:
                if str(entity["id"]) == entity_id:
                    return project_id, dict(entity)
        raise RepositoryError(404, "entity_missing", "实体不存在")

    def verify_entity(
        self,
        identity: SessionIdentity,
        *,
        entity_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        try:
            project_id, entity = self._find_entity(
                identity,
                entity_id=entity_id,
            )
        except RepositoryError:
            project_id = self._decision_target_project(
                identity,
                target_id=entity_id,
            )
            if project_id is None:
                raise
            entity = {
                "displayName": entity_id,
                "mentionCount": 0,
            }
        verified_status = str(payload.get("status") or "")
        if verified_status not in {"canonical", "noise", "alias_of"}:
            raise RepositoryError(422, "entity_status_invalid", "实体裁决状态无效")
        alias_target_id = str(payload.get("alias_target_id") or "")
        if verified_status == "alias_of":
            target_project, _ = self._find_entity(
                identity,
                entity_id=alias_target_id,
            )
            if target_project != project_id or alias_target_id == entity_id:
                raise RepositoryError(
                    422,
                    "entity_alias_target_invalid",
                    "实体别名目标无效",
                )
        data = {
            "status": verified_status,
            "aliasTargetId": alias_target_id or None,
            "reason": str(payload.get("reason") or "")[:1000],
        }
        result = {
            "entityId": entity_id,
            "verifiedStatus": verified_status,
            "verifiedAt": utc_now(),
            "mergedInto": alias_target_id or None,
            "mentionsMoved": (
                int(entity.get("mentionCount") or 0)
                if verified_status == "alias_of"
                else 0
            ),
            "factsMoved": 0,
        }
        return self._publish_knowledge_decision(
            identity,
            project_id=project_id,
            decision_kind="entity_verify",
            target_id=entity_id,
            data=data,
            title=f"实体裁决：{entity['displayName']}",
            summary=(
                f"实体“{entity['displayName']}”已人工裁决为"
                f" {verified_status}。"
            ),
            result_payload=result,
            idempotency_key=idempotency_key,
        )

    def merge_entity(
        self,
        identity: SessionIdentity,
        *,
        merged_id: str,
        surviving_entity_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        try:
            project_id, merged = self._find_entity(
                identity,
                entity_id=merged_id,
            )
        except RepositoryError:
            project_id = self._decision_target_project(
                identity,
                target_id=merged_id,
            )
            if project_id is None:
                raise
            merged = {
                "displayName": merged_id,
                "entityType": "",
                "mentionCount": 0,
            }
        survivor_project, survivor = self._find_entity(
            identity,
            entity_id=surviving_entity_id,
        )
        if (
            survivor_project != project_id
            or surviving_entity_id == merged_id
            or (
                merged["entityType"]
                and survivor["entityType"] != merged["entityType"]
            )
        ):
            raise RepositoryError(
                422,
                "entity_merge_target_invalid",
                "实体合并目标无效",
            )
        data = {
            "mergedEntityId": merged_id,
            "survivingEntityId": surviving_entity_id,
            "reason": reason[:1000],
        }
        result = {
            "mentionsMoved": int(merged.get("mentionCount") or 0),
            "triplesMoved": 0,
            "factsMoved": 0,
        }
        return self._publish_knowledge_decision(
            identity,
            project_id=project_id,
            decision_kind="entity_merge",
            target_id=merged_id,
            data=data,
            title=f"实体合并：{merged['displayName']}",
            summary=(
                f"实体“{merged['displayName']}”已合并到"
                f"“{survivor['displayName']}”。"
            ),
            result_payload=result,
            idempotency_key=idempotency_key,
        )

    def review_glossary_attribute(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        attribute_id: str,
        review_status: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        attributes = self.glossary_attributes(
            identity,
            project_id=project_id,
        )["attributes"]
        attribute = next(
            (
                item
                for item in attributes
                if str(item["id"]) == attribute_id
            ),
            None,
        )
        if attribute is None:
            raise RepositoryError(
                404,
                "glossary_attribute_missing",
                "字典属性不存在",
            )
        if review_status not in {"verified", "rejected"}:
            raise RepositoryError(
                422,
                "glossary_attribute_status_invalid",
                "字典属性裁决状态无效",
            )
        data = {
            "status": review_status,
            "verifiedBy": str(payload.get("verifiedBy") or identity.membership_id),
            "note": str(payload.get("note") or "")[:1000],
        }
        for key in (
            "termId",
            "attributeName",
            "valueText",
            "valueUnit",
            "scope",
            "asOfDate",
        ):
            if key in payload:
                data[key] = payload.get(key)
        return self._publish_knowledge_decision(
            identity,
            project_id=project_id,
            decision_kind="glossary_attribute_review",
            target_id=attribute_id,
            data=data,
            title=f"字典属性裁决：{attribute['term']}",
            summary=(
                f"字典属性“{attribute['term']} / "
                f"{attribute['attribute_name']}”已裁决为 {review_status}。"
            ),
            result_payload={
                "ok": True,
                "id": attribute_id,
                "status": review_status,
            },
            idempotency_key=idempotency_key,
        )

    def review_glossary_drift(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        alert_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        action = str(payload.get("action") or "")
        if action not in {"update_glossary", "dismiss"}:
            raise RepositoryError(
                422,
                "glossary_drift_action_invalid",
                "字典漂移处理动作无效",
            )
        alert = next(
            (
                item
                for review_status in ("pending", "resolved", "dismissed")
                for item in self.glossary_drift_alerts(
                    identity,
                    project_id=project_id,
                    status=review_status,
                )["alerts"]
                if str(item["id"]) == alert_id
            ),
            None,
        )
        if alert is None:
            raise RepositoryError(
                404,
                "glossary_drift_alert_missing",
                "字典漂移告警不存在",
            )
        data = {
            "action": action,
            "note": str(payload.get("note") or "")[:1000],
            "reviewedBy": identity.membership_id,
            "glossaryAttributeId": alert["glossary_attribute_id"],
            "newFactId": alert["new_fact_id"],
        }
        return self._publish_knowledge_decision(
            identity,
            project_id=project_id,
            decision_kind="glossary_drift_review",
            target_id=alert_id,
            data=data,
            title=f"字典漂移裁决：{alert['term']}",
            summary=(
                f"字典漂移“{alert['term']} / "
                f"{alert['attribute_name']}”已执行 {action}。"
            ),
            result_payload={"ok": True, "id": alert_id, "action": action},
            idempotency_key=idempotency_key,
        )

    def review_contradiction(
        self,
        identity: SessionIdentity,
        *,
        contradiction_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        review_status = str(payload.get("reviewStatus") or "")
        if review_status not in {"dismissed", "resolved"}:
            raise RepositoryError(
                422,
                "contradiction_review_status_invalid",
                "矛盾裁决状态无效",
            )
        found: tuple[str, dict[str, Any]] | None = None
        for project_id in self._visible_project_ids(identity):
            for candidate_status in ("pending", "resolved", "dismissed"):
                items = self.derived_contradictions(
                    identity,
                    project_id=project_id,
                    status=candidate_status,
                    limit=1000,
                )["contradictions"]
                contradiction = next(
                    (
                        item
                        for item in items
                        if str(item["id"]) == contradiction_id
                    ),
                    None,
                )
                if contradiction is not None:
                    found = project_id, contradiction
                    break
            if found is not None:
                break
        if found is None:
            raise RepositoryError(
                404,
                "contradiction_missing",
                "资料矛盾不存在",
            )
        project_id, contradiction = found
        data = {
            "reviewStatus": review_status,
            "acceptedFactId": payload.get("acceptedFactId"),
            "resolutionNote": str(
                payload.get("resolutionNote") or ""
            )[:1000],
        }
        return self._publish_knowledge_decision(
            identity,
            project_id=project_id,
            decision_kind="contradiction_review",
            target_id=contradiction_id,
            data=data,
            title=f"资料矛盾裁决：{contradiction['subjectText']}",
            summary=(
                f"资料矛盾“{contradiction['subjectText']} / "
                f"{contradiction['attribute']}”已裁决为 {review_status}。"
            ),
            result_payload={"status": review_status},
            idempotency_key=idempotency_key,
        )

    def folder_recommendation_plan(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
    ) -> dict[str, Any]:
        visible_ids = self._visible_document_ids(identity, project_id=project_id)
        if not visible_ids:
            return {
                "clientId": project_id,
                "generatedAt": utc_now(),
                "visibleFolderLimit": 8,
                "visibleFolderBudget": 8,
                "recommendedVisibleFolders": [],
                "hiddenLegacyFolders": [],
                "pendingReasonCounts": {},
                "folders": [],
                "totalDocumentCount": 0,
                "pendingDocumentCount": 0,
                "lowConfidenceDocumentCount": 0,
                "derivation": "knowledge_documents.document_kind",
            }
        placeholders = ",".join("?" for _ in visible_ids)
        with self.repository._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT document_kind, title, parse_state
                FROM knowledge_documents
                WHERE organization_id = ? AND project_id = ?
                  AND lifecycle_state = 'active'
                  AND document_id IN ({placeholders})
                ORDER BY updated_at DESC, document_id
                """,
                (
                    identity.organization_id,
                    project_id,
                    *sorted(visible_ids),
                ),
            ).fetchall()
        groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in rows:
            label = str(row["document_kind"] or "未分类").strip() or "未分类"
            groups[label].append(row)
        folders = [
            {
                "targetFolderLabel": label,
                "confidence": 1.0,
                "reason": "按严格知识文档 document_kind 实时分组",
                "suggestedTags": [label],
                "needsReview": any(
                    row["parse_state"] in {"failed", "missing_source"}
                    for row in group
                ),
                "documentCount": len(group),
                "exampleDocuments": [
                    str(row["title"]) for row in group[:3]
                ],
            }
            for label, group in sorted(
                groups.items(), key=lambda item: (-len(item[1]), item[0])
            )
        ]
        pending = sum(
            1
            for row in rows
            if row["parse_state"]
            in {"not_requested", "queued", "processing", "failed", "missing_source"}
        )
        return {
            "clientId": project_id,
            "generatedAt": utc_now(),
            "visibleFolderLimit": 8,
            "visibleFolderBudget": 8,
            "recommendedVisibleFolders": [
                item["targetFolderLabel"] for item in folders[:8]
            ],
            "hiddenLegacyFolders": [],
            "pendingReasonCounts": {
                state: sum(1 for row in rows if row["parse_state"] == state)
                for state in {
                    str(row["parse_state"])
                    for row in rows
                    if row["parse_state"] != "ready"
                }
            },
            "folders": folders,
            "totalDocumentCount": len(rows),
            "pendingDocumentCount": pending,
            "lowConfidenceDocumentCount": 0,
            "derivation": "knowledge_documents.document_kind + parse_state",
        }

    def auto_repair_preview(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        document_ids: Iterable[Any] = (),
        limit: int = 100,
    ) -> dict[str, Any]:
        visible_ids = self._visible_document_ids(identity, project_id=project_id)
        requested_ids = {
            str(value) for value in document_ids if str(value or "").strip()
        }
        if requested_ids:
            visible_ids &= requested_ids
        if not visible_ids:
            rows: list[sqlite3.Row] = []
        else:
            placeholders = ",".join("?" for _ in visible_ids)
            with self.repository._connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT d.document_id, d.title, d.document_kind,
                           d.parse_state, d.project_assignment_state,
                           d.source_asset_id, a.lifecycle_state AS source_state
                    FROM knowledge_documents d
                    LEFT JOIN source_assets a
                      ON a.source_asset_id = d.source_asset_id
                    WHERE d.organization_id = ? AND d.project_id = ?
                      AND d.lifecycle_state = 'active'
                      AND d.document_id IN ({placeholders})
                    ORDER BY d.updated_at DESC, d.document_id
                    LIMIT ?
                    """,
                    (
                        identity.organization_id,
                        project_id,
                        *sorted(visible_ids),
                        max(1, min(limit, 500)),
                    ),
                ).fetchall()
        plan = self.folder_recommendation_plan(identity, project_id=project_id)
        default_folder = (
            plan["recommendedVisibleFolders"][0]
            if plan["recommendedVisibleFolders"]
            else "未分类"
        )
        items = []
        for row in rows:
            parse_state = str(row["parse_state"])
            if row["source_state"] == "missing" or parse_state == "missing_source":
                health = "missing_original"
                stage = "minimal_human_check"
                requires_human = True
            elif parse_state == "failed":
                health = "parse_failed"
                stage = "repair_ingest"
                requires_human = False
            elif parse_state in {"ready", "partial_ready"}:
                health = "v2_ready"
                stage = "ready_classify"
                requires_human = False
            else:
                health = "unknown"
                stage = "repair_ingest"
                requires_human = False
            items.append(
                {
                    "documentId": row["document_id"],
                    "v2DocumentId": row["document_id"],
                    "title": row["title"],
                    "kind": row["document_kind"],
                    "healthStatus": health,
                    "stage": stage,
                    "nextSystemAction": (
                        "等待用户重新选择本机源文件"
                        if requires_human
                        else "依据 processing_attempts 显式重试"
                        if parse_state == "failed"
                        else "按 document_kind 展示派生分组"
                    ),
                    "targetFolder": str(row["document_kind"] or default_folder),
                    "tags": [str(row["document_kind"] or default_folder)],
                    "searchPolicy": (
                        "include"
                        if parse_state in {"ready", "partial_ready"}
                        else "exclude_until_repaired"
                    ),
                    "requiresHuman": requires_human,
                    "humanQuestion": (
                        "请重新选择当前设备上的源文件"
                        if requires_human
                        else None
                    ),
                    "confidence": 1.0,
                    "reason": f"严格权威 parse_state={parse_state}",
                    "sourcePath": None,
                    "duplicateOfDocumentId": None,
                }
            )
        summary: dict[str, int] = defaultdict(int)
        for item in items:
            summary[str(item["healthStatus"])] += 1
        return {
            "previewId": (
                "derived_repair_"
                + sha256_text(
                    project_id + "|" + "|".join(sorted(visible_ids))
                )[:24]
            ),
            "clientId": project_id,
            "generatedAt": utc_now(),
            "visibleFolderBudget": int(plan["visibleFolderBudget"]),
            "recommendedVisibleFolders": plan["recommendedVisibleFolders"],
            "pendingReasonCounts": plan["pendingReasonCounts"],
            "summary": dict(summary),
            "items": items,
            "derivation": "knowledge_documents + source_assets + processing state",
        }

    def queue_auto_repair(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        document_ids: Iterable[Any],
        include_human_required: bool,
        limit: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        preview = self.auto_repair_preview(
            identity,
            project_id=project_id,
            document_ids=document_ids,
            limit=limit,
        )
        actionable_ids = [
            str(item["documentId"])
            for item in preview["items"]
            if item["healthStatus"] != "v2_ready"
            and (
                not bool(item["requiresHuman"])
                or include_human_required
            )
        ]
        human_confirmation_count = sum(
            1 for item in preview["items"] if bool(item["requiresHuman"])
        )
        normalized = {
            "projectId": project_id,
            "documentIds": actionable_ids,
            "includeHumanRequired": bool(include_human_required),
            "previewId": preview["previewId"],
        }
        command_type = "project_material.auto_repair_queued"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self._project_row(
                    connection,
                    identity,
                    project_id,
                    require_edit=True,
                )
                if actionable_ids:
                    raise RepositoryError(
                        409,
                        "local_material_auto_repair_executor_not_connected",
                        "自动修复需要当前成员设备读取本机源文件；组织云不能代替本机执行",
                    )
                batch_id = new_id()
                result = {
                    "jobId": batch_id,
                    "status": "completed",
                    "queuedCount": 0,
                    "skippedCount": len(preview["items"]),
                    "humanConfirmationCount": human_confirmation_count,
                    "message": "没有需要修复的资料",
                    "processingAttemptIds": [],
                    "materialBoundary": {
                        "sourceFileContentUploaded": False,
                        "sourceFilePathUploaded": False,
                    },
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="processing_batch",
                    aggregate_id=batch_id,
                    expected_version=None,
                    before_version=None,
                    after_version=1,
                    payload=normalized,
                    payload_hash=payload_hash,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _attempt_payload(row: sqlite3.Row) -> dict[str, Any]:
        status_map = {
            "queued": ("queued", "queued", 0),
            "processing": ("running", "processing", 50),
            "completed": ("completed", "completed", 100),
            "partial": ("completed", "partial", 100),
            "failed": ("failed", "failed", 100),
            "cancelled": ("canceled", "canceled", 100),
        }
        status, stage, progress = status_map[str(row["state"])]
        source_kind = str(row["source_kind"] or "").lower()
        platform = (
            "bilibili"
            if "bilibili" in source_kind
            else "xiaohongshu"
            if "xiaohongshu" in source_kind
            else "wechat_article"
        )
        return {
            "runId": row["processing_attempt_id"],
            "clientId": row["project_id"],
            "sourcePlatform": platform,
            "sourceUrl": "",
            "title": row["file_name"] or None,
            "status": status,
            "stage": stage,
            "progress": progress,
            "documentId": row["document_id"],
            "documentPath": None,
            "mediaCacheStatus": "not_downloaded",
            "error": row["error_message"] or None,
            "metadata": {
                "processingKind": row["processing_kind"],
                "attemptNo": int(row["attempt_no"]),
                "sourceAssetId": row["source_asset_id"],
                "errorCode": row["error_code"] or None,
                "sourceLocatorIncluded": False,
            },
            "createdAt": row["created_at"],
            "updatedAt": row["finished_at"]
            or row["started_at"]
            or row["created_at"],
        }

    def start_link_import(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        url: str,
        use_browser_cookies: bool,
        cookie_browser: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_url = url.strip()
        parsed_url = urlparse(normalized_url)
        hostname = (parsed_url.hostname or "").lower()
        if parsed_url.scheme not in {"http", "https"} or not hostname:
            raise RepositoryError(422, "link_import_url_invalid", "资料链接无效")
        platform = (
            "bilibili"
            if "bilibili.com" in hostname or "b23.tv" in hostname
            else "xiaohongshu"
            if "xiaohongshu.com" in hostname or "xhslink.com" in hostname
            else "wechat_article"
            if hostname in {"mp.weixin.qq.com", "weixin.qq.com"}
            else ""
        )
        if not platform:
            raise RepositoryError(
                422,
                "link_import_platform_unsupported",
                "当前仅支持哔哩哔哩、小红书和微信公众号资料链接",
            )
        with self.repository._connection() as connection:
            self._project_row(
                connection,
                identity,
                project_id,
                require_edit=True,
            )
        raise RepositoryError(
            409,
            "link_import_executor_not_connected",
            "链接资料需要当前成员设备执行网页读取；组织云未配置可消费该任务的执行器",
        )

    def link_import_runs(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        limit: int = 20,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        self.project_detail(identity, project_id=project_id)
        with self.repository._connection() as connection:
            params: list[Any] = [identity.organization_id, project_id]
            run_clause = ""
            if run_id:
                run_clause = "AND pa.processing_attempt_id = ?"
                params.append(run_id)
            params.append(max(1, min(int(limit), 100)))
            rows = connection.execute(
                f"""
                SELECT pa.*, a.project_id, a.file_name, a.source_kind
                FROM processing_attempts pa
                JOIN source_assets a
                  ON a.source_asset_id = pa.source_asset_id
                 AND a.organization_id = pa.organization_id
                WHERE pa.organization_id = ?
                  AND a.project_id = ?
                  AND pa.processing_kind = 'link_import'
                  {run_clause}
                ORDER BY pa.created_at DESC, pa.processing_attempt_id
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        if run_id and not rows:
            raise RepositoryError(404, "import_run_missing", "导入任务不存在")
        return {"runs": [self._attempt_payload(row) for row in rows]}

    def cancel_link_import(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        run_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        command_type = "processing_attempt.cancelled"
        payload = {"projectId": project_id, "runId": run_id}
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self._project_row(
                    connection,
                    identity,
                    project_id,
                    require_edit=True,
                )
                row = connection.execute(
                    """
                    SELECT pa.*, a.project_id, a.file_name, a.source_kind
                    FROM processing_attempts pa
                    JOIN source_assets a
                      ON a.source_asset_id = pa.source_asset_id
                     AND a.organization_id = pa.organization_id
                    WHERE pa.organization_id = ?
                      AND pa.processing_attempt_id = ?
                      AND pa.processing_kind = 'link_import'
                      AND a.project_id = ?
                    """,
                    (identity.organization_id, run_id, project_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(404, "import_run_missing", "导入任务不存在")
                if row["state"] not in {"queued", "processing"}:
                    result = {"run": self._attempt_payload(row)}
                    connection.rollback()
                    return result
                now = utc_now()
                connection.execute(
                    """
                    UPDATE processing_attempts
                    SET state = 'cancelled', finished_at = ?
                    WHERE organization_id = ? AND processing_attempt_id = ?
                      AND state IN ('queued', 'processing')
                    """,
                    (now, identity.organization_id, run_id),
                )
                updated = dict(row)
                updated["state"] = "cancelled"
                updated["finished_at"] = now
                result = {"run": self._attempt_payload(updated)}
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="processing_attempt",
                    aggregate_id=run_id,
                    expected_version=None,
                    before_version=1,
                    after_version=2,
                    payload=payload,
                    payload_hash=payload_hash,
                    result=result,
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
        normalized_materials = []
        for material in materials:
            file_name = str(material.get("fileName") or "").strip()
            content_hash = str(material.get("contentHash") or "").strip()
            byte_size = int(material.get("byteSize") or 0)
            if not file_name or not content_hash or byte_size < 0:
                raise RepositoryError(
                    422,
                    "material_metadata_invalid",
                    "资料文件名、内容哈希或大小无效",
                )
            normalized_materials.append(
                {
                    "localSourceId": str(
                        material.get("localSourceId") or ""
                    ).strip(),
                    "fileName": file_name,
                    "contentHash": content_hash,
                    "byteSize": byte_size,
                    "mediaType": str(material.get("mediaType") or ""),
                    "sourceKind": str(
                        material.get("sourceKind")
                        or "local_private_metadata"
                    ),
                }
            )
        if not normalized_materials:
            raise RepositoryError(422, "materials_required", "请选择要导入的资料")
        payload = {
            "projectId": project_id,
            "materials": normalized_materials,
        }
        command_type = "project_material.metadata_registered"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=payload,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self._project_row(
                    connection,
                    identity,
                    project_id,
                    # Registering a member-owned, self-visible local material
                    # does not mutate the project itself. A member who can see
                    # the project may contribute their own local metadata
                    # without gaining permission to edit the project.
                    require_edit=False,
                )
                now = utc_now()
                documents = []
                for material in normalized_materials:
                    source_asset_id = new_id()
                    document_id = new_id()
                    attempt_id = new_id()
                    connection.execute(
                        """
                        INSERT INTO source_assets (
                            source_asset_id, organization_id, project_id,
                            storage_object_id, file_name, media_type, byte_size,
                            content_hash, source_kind, source_locator,
                            lifecycle_state, created_by_membership_id, version,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, '',
                                  'active', ?, 1, ?, ?)
                        """,
                        (
                            source_asset_id,
                            identity.organization_id,
                            project_id,
                            material["fileName"],
                            material["mediaType"],
                            material["byteSize"],
                            material["contentHash"],
                            material["sourceKind"],
                            identity.membership_id,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO knowledge_documents (
                            document_id, organization_id, project_id,
                            project_assignment_state, source_asset_id,
                            owner_membership_id, department_id, title,
                            document_kind, visibility_scope, parse_state,
                            lifecycle_state, current_version, version,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, 'assigned', ?, ?, NULL, ?,
                                  'local_private_metadata', 'self',
                                  'not_requested', 'active', 0, 1, ?, ?)
                        """,
                        (
                            document_id,
                            identity.organization_id,
                            project_id,
                            source_asset_id,
                            identity.membership_id,
                            material["fileName"],
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO processing_attempts (
                            processing_attempt_id, organization_id,
                            source_asset_id, document_id, processing_kind,
                            state, attempt_no, error_code, error_message,
                            started_at, finished_at, created_at
                        ) VALUES (?, ?, ?, ?,
                                  'local_material_metadata_registration',
                                  'completed', 1, '', '', ?, ?, ?)
                        """,
                        (
                            attempt_id,
                            identity.organization_id,
                            source_asset_id,
                            document_id,
                            now,
                            now,
                            now,
                        ),
                    )
                    documents.append(
                        {
                            "localSourceId": material["localSourceId"],
                            "sourceAssetId": source_asset_id,
                            "documentId": document_id,
                            "processingAttemptId": attempt_id,
                            "title": material["fileName"],
                            "fileName": material["fileName"],
                            "lifecycleState": "active",
                            "parseState": "not_requested",
                            "version": 1,
                            "updatedAt": now,
                        }
                    )
                import_run_id = new_id()
                result = {
                    "importRunId": import_run_id,
                    "projectId": project_id,
                    "documents": documents,
                    "importedCount": len(documents),
                    "skippedCount": 0,
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
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="material_import",
                    aggregate_id=import_run_id,
                    expected_version=None,
                    before_version=None,
                    after_version=1,
                    payload=payload,
                    payload_hash=payload_hash,
                    result=result,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def update_local_material_metadata(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        document_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        try:
            expected = int(payload.get("expectedVersion"))
            byte_size = int(payload.get("byteSize"))
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                409,
                "material_metadata_version_required",
                "更新本机资料元数据需要有效的版本和文件大小",
            ) from exc
        content_hash = str(payload.get("contentHash") or "").strip()
        file_name = str(payload.get("fileName") or "").strip()
        title = str(payload.get("title") or file_name).strip()
        media_type = str(payload.get("mediaType") or "").strip()
        if (
            len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
            or not file_name
            or not title
            or byte_size < 0
        ):
            raise RepositoryError(
                422,
                "material_metadata_invalid",
                "资料标题、文件名、内容哈希或大小无效",
            )
        normalized = {
            "projectId": project_id,
            "documentId": document_id,
            "expectedVersion": expected,
            "title": title,
            "fileName": file_name,
            "contentHash": content_hash,
            "byteSize": byte_size,
            "mediaType": media_type,
        }
        command_type = "project_material.local_metadata_updated"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self._document_row(
                    connection,
                    identity,
                    document_id=document_id,
                    project_id=project_id,
                    require_edit=True,
                )
                before = int(row["version"])
                if before != expected:
                    raise RepositoryError(
                        409,
                        "document_version_conflict",
                        f"资料版本已变化，当前版本为 {before}",
                    )
                if str(row["document_kind"]) != "local_private_metadata":
                    raise RepositoryError(
                        409,
                        "document_not_member_local",
                        "只有成员本机资料可以更新本机元数据",
                    )
                if str(row["owner_membership_id"] or "") != identity.membership_id:
                    raise RepositoryError(
                        403,
                        "local_material_owner_required",
                        "只有持有本机源文件的成员可以更新该资料元数据",
                    )
                source_asset_id = str(row["source_asset_id"] or "")
                if not source_asset_id:
                    raise RepositoryError(
                        409,
                        "source_asset_missing",
                        "资料缺少严格来源对象",
                    )
                now = utc_now()
                after = before + 1
                updated = connection.execute(
                    """
                    UPDATE knowledge_documents
                    SET title = ?, version = ?, updated_at = ?
                    WHERE organization_id = ? AND document_id = ?
                      AND project_id = ? AND version = ?
                    """,
                    (
                        title,
                        after,
                        now,
                        identity.organization_id,
                        document_id,
                        project_id,
                        before,
                    ),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "document_version_conflict",
                        "资料版本已变化，请刷新后重试",
                    )
                connection.execute(
                    """
                    UPDATE source_assets
                    SET file_name = ?, media_type = ?, byte_size = ?,
                        content_hash = ?, version = version + 1,
                        updated_at = ?
                    WHERE organization_id = ? AND source_asset_id = ?
                    """,
                    (
                        file_name,
                        media_type,
                        byte_size,
                        content_hash,
                        now,
                        identity.organization_id,
                        source_asset_id,
                    ),
                )
                result = {
                    "projectId": project_id,
                    "documentId": document_id,
                    "title": title,
                    "fileName": file_name,
                    "contentHash": content_hash,
                    "byteSize": byte_size,
                    "mediaType": media_type,
                    "version": after,
                    "updatedAt": now,
                    "materialBoundary": {
                        "sourceFileContentUploaded": False,
                        "sourceFilePathUploaded": False,
                        "storageLocatorUploaded": False,
                    },
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="knowledge_document",
                    aggregate_id=document_id,
                    expected_version=expected,
                    before_version=before,
                    after_version=after,
                    payload=normalized,
                    payload_hash=payload_hash,
                    result=result,
                    audit_summary={
                        "projectId": project_id,
                        "contentHash": content_hash,
                        "byteSize": byte_size,
                    },
                    outbox_payload={
                        "projectId": project_id,
                        "documentId": document_id,
                        "contentHash": content_hash,
                        "version": after,
                    },
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def publish_local_material_summary(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        document_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        summary = str(payload.get("summary") or "").strip()
        source_content_hash = str(
            payload.get("sourceContentHash") or ""
        ).strip()
        generator_version = str(
            payload.get("generatorVersion") or "strict-local-summary-v1"
        ).strip()[:120]
        try:
            expected = int(payload.get("expectedVersion"))
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                409,
                "material_summary_version_required",
                "发布本机资料摘要需要有效的资料版本",
            ) from exc
        if not summary:
            raise RepositoryError(
                422,
                "material_summary_required",
                "发布摘要不能为空",
            )
        if len(summary) > 4000:
            raise RepositoryError(
                422,
                "material_summary_too_long",
                "组织共享摘要不能超过 4000 字",
            )
        if (
            len(source_content_hash) != 64
            or any(
                character not in "0123456789abcdef"
                for character in source_content_hash
            )
        ):
            raise RepositoryError(
                422,
                "material_source_hash_invalid",
                "本机资料内容哈希无效",
            )
        summary_hash = sha256_text(summary)
        normalized = {
            "projectId": project_id,
            "documentId": document_id,
            "expectedVersion": expected,
            "sourceContentHash": source_content_hash,
            "summaryHash": summary_hash,
            "generatorVersion": generator_version,
        }
        command_type = "project_material.local_summary_published"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                row = self._document_row(
                    connection,
                    identity,
                    document_id=document_id,
                    project_id=project_id,
                    require_edit=True,
                )
                before = int(row["version"])
                if before != expected:
                    raise RepositoryError(
                        409,
                        "document_version_conflict",
                        f"资料版本已变化，当前版本为 {before}",
                    )
                if str(row["owner_membership_id"] or "") != identity.membership_id:
                    raise RepositoryError(
                        403,
                        "local_material_owner_required",
                        "只有持有本机源文件的成员可以发布该资料摘要",
                    )
                if str(row["source_content_hash"] or "") != source_content_hash:
                    raise RepositoryError(
                        409,
                        "local_material_content_changed",
                        "本机资料内容已变化，请先更新元数据后重试",
                    )
                source_asset_id = str(row["source_asset_id"] or "")
                if not source_asset_id:
                    raise RepositoryError(
                        409,
                        "source_asset_missing",
                        "资料缺少严格来源对象",
                    )
                now = utc_now()
                content_version = int(row["current_version"] or 0) + 1
                aggregate_version = before + 1
                document_version_id = new_id()
                attempt_id = new_id()
                connection.execute(
                    """
                    INSERT INTO document_versions (
                        document_version_id, organization_id, document_id,
                        version, content_hash, preview_text, markdown_content,
                        section_count, chunk_count, generator_version,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                    """,
                    (
                        document_version_id,
                        identity.organization_id,
                        document_id,
                        content_version,
                        summary_hash,
                        summary[:2000],
                        summary,
                        generator_version,
                        now,
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE knowledge_documents
                    SET document_kind = 'project_material_summary',
                        visibility_scope = 'organization',
                        parse_state = 'ready',
                        current_version = ?, version = ?, updated_at = ?
                    WHERE organization_id = ? AND document_id = ?
                      AND project_id = ? AND version = ?
                    """,
                    (
                        content_version,
                        aggregate_version,
                        now,
                        identity.organization_id,
                        document_id,
                        project_id,
                        before,
                    ),
                )
                if updated.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "document_version_conflict",
                        "资料已更新，请刷新后重试",
                    )
                attempt_no = int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(attempt_no), 0) + 1
                        FROM processing_attempts
                        WHERE organization_id = ? AND source_asset_id = ?
                          AND processing_kind = 'local_summary_publish'
                        """,
                        (identity.organization_id, source_asset_id),
                    ).fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO processing_attempts (
                        processing_attempt_id, organization_id,
                        source_asset_id, document_id, processing_kind,
                        state, attempt_no, error_code, error_message,
                        started_at, finished_at, created_at
                    ) VALUES (?, ?, ?, ?, 'local_summary_publish',
                              'completed', ?, '', '', ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        identity.organization_id,
                        source_asset_id,
                        document_id,
                        attempt_no,
                        now,
                        now,
                        now,
                    ),
                )
                result = {
                    "projectId": project_id,
                    "documentId": document_id,
                    "documentVersionId": document_version_id,
                    "processingAttemptId": attempt_id,
                    "parseState": "ready",
                    "visibilityScope": "organization",
                    "documentKind": "project_material_summary",
                    "contentVersion": content_version,
                    "version": aggregate_version,
                    "contentHash": summary_hash,
                    "updatedAt": now,
                    "materialBoundary": {
                        "sourceFileContentUploaded": False,
                        "sourceFilePathUploaded": False,
                        "storageLocatorUploaded": False,
                        "organizationSummaryUploaded": True,
                    },
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="knowledge_document",
                    aggregate_id=document_id,
                    expected_version=expected,
                    before_version=before,
                    after_version=aggregate_version,
                    payload=normalized,
                    payload_hash=payload_hash,
                    result=result,
                    audit_summary={
                        "projectId": project_id,
                        "documentId": document_id,
                        "documentVersionId": document_version_id,
                        "sourceContentHash": source_content_hash,
                        "summaryHash": summary_hash,
                    },
                    outbox_payload={
                        "projectId": project_id,
                        "documentId": document_id,
                        "documentVersionId": document_version_id,
                        "contentHash": summary_hash,
                        "version": aggregate_version,
                    },
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _safe_published_structure(
        parsed: Mapping[str, Any],
    ) -> dict[str, list[Any]]:
        allowed_fields = {
            "entities": {
                "name",
                "displayName",
                "normalizedName",
                "type",
                "kind",
                "entityType",
                "aliases",
                "attributes",
                "confidence",
            },
            "relationships": {
                "source",
                "target",
                "from",
                "to",
                "type",
                "relation",
                "label",
                "confidence",
            },
            "events": {"summary", "title", "date", "occurredAt", "kind"},
            "opinions": {"summary", "content", "speaker", "stance"},
            "commitments": {
                "content",
                "title",
                "status",
                "owner",
                "dueDate",
            },
            "risk_signals": {
                "title",
                "severity",
                "description",
                "signal_kind",
            },
            "files_classified": {
                "original_filename",
                "role",
                "confidence",
            },
            "files_suggested_to_attach": {
                "original_filename",
                "role",
                "reason",
            },
            "open_questions": {"question", "title", "status"},
        }

        def safe_value(value: Any) -> Any:
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            if isinstance(value, list):
                return [
                    item
                    for item in value[:100]
                    if item is None or isinstance(item, (str, int, float, bool))
                ]
            if isinstance(value, dict):
                return {
                    str(key)[:100]: item
                    for key, item in list(value.items())[:100]
                    if isinstance(key, str)
                    and (
                        item is None
                        or isinstance(item, (str, int, float, bool))
                    )
                }
            return None

        result: dict[str, list[Any]] = {}
        for key, fields in allowed_fields.items():
            raw_items = parsed.get(key)
            if not isinstance(raw_items, list):
                continue
            items: list[Any] = []
            for raw in raw_items[:500]:
                if isinstance(raw, str):
                    items.append(raw[:2000])
                    continue
                if not isinstance(raw, dict):
                    continue
                item = {
                    field: safe_value(raw[field])
                    for field in fields
                    if field in raw and safe_value(raw[field]) is not None
                }
                if item:
                    items.append(item)
            result[key] = items
        return result

    @staticmethod
    def _published_structure_summary(
        title: str,
        payload: Mapping[str, list[Any]],
    ) -> str:
        labels = {
            "entities": "实体",
            "relationships": "关系",
            "events": "事件",
            "opinions": "观点",
            "commitments": "承诺",
            "risk_signals": "风险",
            "files_classified": "资料分类",
            "files_suggested_to_attach": "建议关联资料",
            "open_questions": "待确认问题",
        }
        preferred_fields = (
            "name",
            "displayName",
            "summary",
            "content",
            "title",
            "question",
            "description",
            "label",
            "original_filename",
        )
        lines = [f"《{title}》已审阅结构化知识摘要"]
        for key, label in labels.items():
            values = []
            for raw in payload.get(key) or []:
                if isinstance(raw, str):
                    value = raw
                else:
                    value = next(
                        (
                            str(raw[field])
                            for field in preferred_fields
                            if raw.get(field) not in {None, ""}
                        ),
                        "",
                    )
                if value:
                    values.append(value.replace("\n", " ")[:240])
                if len(values) >= 12:
                    break
            if values:
                lines.append(f"{label}：{'；'.join(values)}")
        if len(lines) == 1:
            lines.append("本次审阅未形成可发布的结构化条目。")
        return "\n".join(lines)[:4000]

    @staticmethod
    def _insert_shared_summary(
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        project_id: str,
        title: str,
        summary: str,
        generator_version: str,
        now: str,
    ) -> dict[str, Any]:
        normalized_summary = summary.strip()[:4000]
        if not normalized_summary:
            raise RepositoryError(
                422,
                "published_summary_required",
                "发布摘要不能为空",
            )
        document_id = new_id()
        document_version_id = new_id()
        content_hash = sha256_text(normalized_summary)
        connection.execute(
            """
            INSERT INTO knowledge_documents (
                document_id, organization_id, project_id,
                project_assignment_state, source_asset_id,
                owner_membership_id, department_id, title,
                document_kind, visibility_scope, parse_state,
                lifecycle_state, current_version, version,
                created_at, updated_at
            ) VALUES (?, ?, ?, 'assigned', NULL, ?, NULL, ?,
                      'intelligence_summary', 'organization', 'ready',
                      'active', 1, 1, ?, ?)
            """,
            (
                document_id,
                identity.organization_id,
                project_id,
                identity.membership_id,
                title[:300],
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO document_versions (
                document_version_id, organization_id, document_id,
                version, content_hash, preview_text, markdown_content,
                section_count, chunk_count, generator_version, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, ?, 1, 1, ?, ?)
            """,
            (
                document_version_id,
                identity.organization_id,
                document_id,
                content_hash,
                normalized_summary[:2000],
                normalized_summary,
                generator_version,
                now,
            ),
        )
        return {
            "documentId": document_id,
            "documentVersionId": document_version_id,
            "version": 1,
            "sourceType": "structured_intelligence_summary",
            "contentHash": content_hash,
            "publishedSummary": normalized_summary[:2000],
            "materialBoundary": {
                "sourceFileContentIncluded": False,
                "sourceFilePathsIncluded": False,
                "rawImportTextIncluded": False,
                "storageLocatorsIncluded": False,
            },
        }

    def publish_smart_import(
        self,
        identity: SessionIdentity,
        *,
        project_id: str,
        title: str,
        parsed: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        safe_payload = self._safe_published_structure(parsed)
        if not any(
            safe_payload.get(key)
            for key in (
                "entities",
                "relationships",
                "events",
                "opinions",
                "commitments",
                "risk_signals",
                "open_questions",
            )
        ):
            raise RepositoryError(
                422,
                "published_summary_content_required",
                "智能导入没有形成可发布的结构化内容",
            )
        normalized = {
            "projectId": project_id,
            "title": title.strip() or "智能导入",
            "parsed": safe_payload,
        }
        command_type = "smart_import.published"
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                receipt, payload_hash = self._receipt(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload=normalized,
                )
                if receipt is not None:
                    connection.rollback()
                    return receipt
                self._project_row(
                    connection,
                    identity,
                    project_id,
                    require_edit=True,
                )
                now = utc_now()
                intelligence_id = new_id()
                counts = {
                    "entities_created": len(safe_payload.get("entities") or []),
                    "atomic_facts_created": 0,
                    "commitments_created": len(
                        safe_payload.get("commitments") or []
                    ),
                    "risk_signals_created": len(
                        safe_payload.get("risk_signals") or []
                    ),
                    "events_created": len(safe_payload.get("events") or []),
                    "documents_created": 1,
                    "errors": [],
                }
                summary = self._published_structure_summary(
                    normalized["title"],
                    safe_payload,
                )
                connection.execute(
                    """
                    INSERT INTO intelligence_records (
                        intelligence_id, organization_id, project_id, title,
                        summary, source_url, record_kind, status,
                        visibility_scope, created_by_membership_id,
                        source_payload_json, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '', 'smart_import_reviewed',
                              'accepted', 'organization', ?, ?, 1, ?, ?)
                    """,
                    (
                        intelligence_id,
                        identity.organization_id,
                        project_id,
                        normalized["title"],
                        (
                            "用户已审阅并提交的智能导入结构化结果："
                            f"{sum(counts[key] for key in counts if key.endswith('_created'))}"
                            " 项"
                        ),
                        identity.membership_id,
                        canonical_json(safe_payload),
                        now,
                        now,
                    ),
                )
                published_knowledge = self._insert_shared_summary(
                    connection,
                    identity,
                    project_id=project_id,
                    title=f"{normalized['title']} · 已发布结构化摘要",
                    summary=summary,
                    generator_version="smart-import-reviewed-summary-v1",
                    now=now,
                )
                result = {
                    "intelligenceId": intelligence_id,
                    "knowledgeDocumentId": published_knowledge["documentId"],
                    "knowledgeDocumentVersion": published_knowledge["version"],
                    "documentVersionId": published_knowledge[
                        "documentVersionId"
                    ],
                    "publishedKnowledge": published_knowledge,
                    "stats": counts,
                    "publishedAt": now,
                    "rawTextUploaded": False,
                    "sourceFilesUploaded": False,
                }
                self._record_command(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    aggregate_type="intelligence",
                    aggregate_id=intelligence_id,
                    expected_version=None,
                    before_version=None,
                    after_version=1,
                    payload=normalized,
                    payload_hash=payload_hash,
                    result=result,
                    audit_summary={
                        "projectId": project_id,
                        "counts": {
                            key: value
                            for key, value in counts.items()
                            if key.endswith("_created")
                        },
                        "knowledgeDocumentId": published_knowledge[
                            "documentId"
                        ],
                        "knowledgeDocumentVersion": published_knowledge[
                            "version"
                        ],
                        "contentHash": published_knowledge["contentHash"],
                    },
                    outbox_payload={
                        "intelligenceId": intelligence_id,
                        "version": 1,
                        "projectId": project_id,
                        "knowledgeDocumentId": published_knowledge[
                            "documentId"
                        ],
                        "knowledgeDocumentVersion": published_knowledge[
                            "version"
                        ],
                        "documentVersionId": published_knowledge[
                            "documentVersionId"
                        ],
                        "contentHash": published_knowledge["contentHash"],
                    },
                    additional_outbox_events=(
                        {
                            "aggregateType": "knowledge_document",
                            "aggregateId": published_knowledge["documentId"],
                            "aggregateVersion": published_knowledge["version"],
                            "eventType": "project_knowledge.summary_published",
                            "payload": {
                                "projectId": project_id,
                                "intelligenceId": intelligence_id,
                                "knowledgeDocumentId": published_knowledge[
                                    "documentId"
                                ],
                                "knowledgeDocumentVersion": (
                                    published_knowledge["version"]
                                ),
                                "documentVersionId": published_knowledge[
                                    "documentVersionId"
                                ],
                                "contentHash": published_knowledge[
                                    "contentHash"
                                ],
                                "sourceType": published_knowledge[
                                    "sourceType"
                                ],
                            },
                        },
                    ),
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
