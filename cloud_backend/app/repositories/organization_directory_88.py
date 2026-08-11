from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.security import new_secret_token
from strict_common.security import PASSWORD_SCHEME, hash_password

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .gc14_proposals import _manifest, _record_command


BOT_CAPABILITIES = (
    "workspace_file_write.request",
    "data_center_parse.request",
    "external_material_draft.create",
    "external_send.request",
    "clarification_resolution.propose",
    "inline_approval.allow_from_supervisor",
)


def _id(prefix: str, *parts: str) -> str:
    return prefix + "_" + sha256_text("\x1f".join(parts))[:28]


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


class StrictOrganizationDirectoryRepository:
    """Narrow 88-table member and organization-bot authority.

    This deliberately replaces only the renderer-visible directory and bot
    routes.  It never reaches the frozen organization_* or bot_* tables.
    """

    def __init__(self, repository: CloudRepository):
        self.repository = repository
        self.bot_secret_dir = (
            repository.database_path.parent / ".runtime-secrets" / "organization-bots"
        ).resolve()

    @staticmethod
    def _require_admin(identity: SessionIdentity) -> None:
        if not identity.is_admin:
            raise RepositoryError(403, "admin_required", "仅组织管理员可以执行该操作")

    def _assert_identity(self, connection: Any, identity: SessionIdentity) -> None:
        row = connection.execute(
            "SELECT 1 FROM organization_memberships m "
            "JOIN authorization_scopes s ON s.id=m.scope_id "
            "WHERE m.id=? AND m.principal_id=? AND m.scope_id=? "
            "AND s.organization_id=? AND m.record_kind='membership' "
            "AND m.status='active' AND m.lifecycle_state='active'",
            (
                identity.membership_id,
                identity.principal_id,
                identity.scope_id,
                identity.organization_id,
            ),
        ).fetchone()
        if row is None:
            raise RepositoryError(403, "membership_inactive", "当前组织成员身份不可用")

    def _replay(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            "SELECT c.payload_hash,m.receipt FROM commands c "
            "JOIN object_manifests m ON m.id=c.payload_object_manifest_id "
            "AND m.scope_id=c.scope_id WHERE c.scope_id=? "
            "AND c.actor_principal_id=? AND c.command_type=? "
            "AND c.idempotency_key=? LIMIT 1",
            (
                identity.scope_id,
                identity.principal_id,
                command_type,
                idempotency_key,
            ),
        ).fetchone()
        if row is None:
            return None
        if str(row["payload_hash"] or "") != payload_hash:
            raise RepositoryError(409, "idempotency_payload_conflict", "操作标识已用于不同内容")
        receipt = _json(row["receipt"], {})
        result = receipt.get("result") if isinstance(receipt, Mapping) else None
        return dict(result) if isinstance(result, Mapping) else {}

    def _record(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload_hash: str,
        aggregate_type: str,
        aggregate_id: str,
        aggregate_version: int,
        result: Mapping[str, Any],
        now: str,
    ) -> None:
        operation_id = _id("op", identity.scope_id, command_type, idempotency_key)
        manifest_id = _id("manifest", operation_id, "receipt")
        receipt = canonical_json({"result": dict(result)})
        receipt_hash = sha256_text(receipt)
        connection.execute(
            "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,"
            "lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,byte_size,"
            "media_type,availability_state,receipt_hash,created_at,verified_at,deleted_at,"
            "authority_role,origin_instance_id) VALUES (?,?,NULL,?,'active',?,'cloud',?,"
            "'command_receipt',?,'application/json','ready',?,?,?,NULL,'cloud',?)",
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
        connection.execute(
            "INSERT INTO idempotency_records (id,scope_id,idempotency_key,payload_hash,"
            "result_hash,expires_at,result_object_manifest_id,status,created_at,authority_role,"
            "origin_instance_id) VALUES (?,?,?,?,?,'9999-12-31T23:59:59.999Z',?,"
            "'completed',?,'cloud',?)",
            (
                _id("idem", operation_id), identity.scope_id, idempotency_key,
                payload_hash, receipt_hash, manifest_id, now,
                identity.cloud_instance_id,
            ),
        )
        connection.execute(
            "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,aggregate_type,"
            "aggregate_id,command_type,actor_principal_id,expected_aggregate_version,"
            "device_command_sequence,status,actor_membership_id,payload_object_manifest_id,"
            "payload_hash,submitted_at,settled_at,authority_role,origin_instance_id) VALUES "
            "(?,?,?,?,?,?,?,?,NULL,NULL,'committed',?,?,?,?,?,'cloud',?)",
            (
                _id("cmd", operation_id), identity.scope_id, operation_id,
                idempotency_key, aggregate_type, aggregate_id, command_type,
                identity.principal_id, identity.membership_id, manifest_id,
                payload_hash, now, now, identity.cloud_instance_id,
            ),
        )
        event_hash = sha256_text(
            canonical_json(
                {
                    "operationId": operation_id,
                    "aggregateType": aggregate_type,
                    "aggregateId": aggregate_id,
                    "aggregateVersion": aggregate_version,
                    "resultHash": receipt_hash,
                }
            )
        )
        connection.execute(
            "INSERT INTO audit_events (id,scope_id,operation_id,actor_id,action,event_hash,"
            "actor_membership_id,target_resource_id,details_object_manifest_id,occurred_at,"
            "origin_instance_id,created_at,integrity_hash,authority_role) VALUES "
            "(?,?,?,?,?,?,?,NULL,?,?,?,?,?,'cloud')",
            (
                _id("audit", operation_id), identity.scope_id, operation_id,
                identity.principal_id, command_type, event_hash,
                identity.membership_id, manifest_id, now,
                identity.cloud_instance_id, now, event_hash,
            ),
        )
        connection.execute(
            "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,event_type,"
            "status,aggregate_type,aggregate_id,event_object_manifest_id,event_hash,available_at,"
            "published_at,authority_role,origin_instance_id) VALUES "
            "(?,?,?,?,?,'pending',?,?,?,?,?,NULL,'cloud',?)",
            (
                _id("event", operation_id), identity.scope_id, operation_id,
                aggregate_version, command_type, aggregate_type, aggregate_id,
                manifest_id, event_hash, now, identity.cloud_instance_id,
            ),
        )

    def _mutate(
        self,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        aggregate_type: str,
        aggregate_id: str,
        mutation: Callable[[Any, str], tuple[dict[str, Any], int]],
    ) -> dict[str, Any]:
        payload_hash = sha256_text(canonical_json(dict(payload)))
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(connection, identity)
                replay = self._replay(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replay is not None:
                    connection.rollback()
                    return replay
                now = utc_now()
                result, version = mutation(connection, now)
                self._record(
                    connection,
                    identity,
                    command_type=command_type,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    aggregate_version=version,
                    result=result,
                    now=now,
                )
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _department_assignment(connection: Any, scope_id: str, membership_id: str) -> Any | None:
        return connection.execute(
            "SELECT a.*,d.name AS department_name "
            "FROM organization_memberships a "
            "LEFT JOIN organizations d ON d.id=a.department_id "
            "AND d.record_kind='department' WHERE a.scope_id=? "
            "AND a.record_kind='department_assignment' "
            "AND a.parent_membership_id=? AND a.lifecycle_state='active' "
            "ORDER BY a.updated_at DESC,a.id DESC LIMIT 1",
            (scope_id, membership_id),
        ).fetchone()

    @staticmethod
    def _title_assignment(connection: Any, scope_id: str, membership_id: str) -> Any | None:
        return connection.execute(
            "SELECT a.*,t.name AS title_name FROM organization_memberships a "
            "JOIN organizations t ON t.id=a.title_id "
            "AND t.record_kind='management_title' WHERE a.scope_id=? "
            "AND a.record_kind='title_assignment' "
            "AND a.parent_membership_id=? AND a.lifecycle_state='active' "
            "ORDER BY a.updated_at DESC,a.id DESC LIMIT 1",
            (scope_id, membership_id),
        ).fetchone()

    def _member(self, connection: Any, identity: SessionIdentity, membership_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT m.*,p.display_name,p.status AS principal_status,p.principal_kind,p.contact_type,"
            "p.normalized_contact FROM organization_memberships m "
            "JOIN principals p ON p.id=m.principal_id WHERE m.scope_id=? AND m.id=? "
            "AND m.record_kind='membership' AND m.lifecycle_state!='deleted'",
            (identity.scope_id, membership_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "membership_missing", "组织成员不存在")
        assignment = self._department_assignment(connection, identity.scope_id, membership_id)
        title_assignment = self._title_assignment(connection, identity.scope_id, membership_id)
        application = connection.execute(
            "SELECT * FROM organization_memberships WHERE scope_id=? AND principal_id=? "
            "AND record_kind='application' AND lifecycle_state='active' "
            "ORDER BY updated_at DESC,id DESC LIMIT 1",
            (identity.scope_id, str(row["principal_id"])),
        ).fetchone()
        application_payload = _json(application["capability_set"], {}) if application else {}
        contact_type = str(row["contact_type"] or "")
        contact = str(row["normalized_contact"] or "")
        return {
            "id": str(row["id"]),
            "email": contact if contact_type == "email" else "",
            "phone": contact if contact_type == "phone" else None,
            "fullName": str(row["display_name"] or "未命名成员"),
            "primaryRole": "admin" if str(row["role_key"] or "") == "admin" else "employee",
            "accountStatus": "disabled" if str(row["status"] or "") != "active" else "approved",
            "membershipStatus": str(row["status"] or "active"),
            "departmentId": str(assignment["department_id"] or "") if assignment else None,
            "departmentName": str(assignment["department_name"] or "") if assignment else None,
            "jobTitle": None,
            "managerName": None,
            "currentFocus": None,
            "isBot": str(row["principal_kind"] or "human") == "bot" if "principal_kind" in row.keys() else False,
            "isDepartmentLead": bool(assignment and str(assignment["role_key"] or "") == "department_lead"),
            "visibilityScope": str(row["visibility_scope"] or "self"),
            "managementTitleId": (
                str(title_assignment["title_id"] or "")
                if title_assignment and title_assignment["title_id"] else None
            ),
            "managementTitleName": (
                str(title_assignment["title_name"] or "")
                if title_assignment and title_assignment["title_name"] else None
            ),
            "approvedAt": str(row["updated_at"] or ""),
            "rejectedReason": None,
            "disabledAt": str(row["updated_at"] or "") if str(row["status"] or "") != "active" else None,
            "lastLoginAt": None,
            "createdAt": str(row["created_at"] or ""),
            "version": int(row["version"] or 1),
            "membershipApplicationId": str(application["id"]) if application else None,
            "membershipApplicationState": str(application["status"] or "none") if application else "none",
            "membershipApplicationVersion": int(application["version"] or 1) if application else None,
            "membershipApplicationSubmittedAt": str(application["created_at"] or "") if application else None,
            "membershipApplicationRejectedReason": str(application_payload.get("rejectionReason") or "") or None,
        }

    def members(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        self._require_admin(identity)
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM organization_memberships WHERE scope_id=? "
                    "AND record_kind='membership' AND lifecycle_state!='deleted' "
                    "ORDER BY created_at,id",
                    (identity.scope_id,),
                ).fetchall()
            ]
            return [self._member(connection, identity, item) for item in ids]

    def departments(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            return [
                {
                    "id": str(row["id"]), "name": str(row["name"] or "未命名部门"),
                    "state": str(row["lifecycle_state"]), "version": int(row["version"] or 1),
                    "createdAt": str(row["created_at"]), "updatedAt": str(row["updated_at"]),
                }
                for row in connection.execute(
                    "SELECT id,name,lifecycle_state,version,created_at,updated_at FROM organizations "
                    "WHERE record_kind='department' AND parent_record_id=? "
                    "AND lifecycle_state='active' ORDER BY name,id",
                    (identity.organization_id,),
                ).fetchall()
            ]

    def management_titles(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            return [
                {
                    "id": str(row["id"]), "name": str(row["name"] or "未命名岗位"),
                    "state": str(row["lifecycle_state"]), "version": int(row["version"] or 1),
                    "createdAt": str(row["created_at"]), "updatedAt": str(row["updated_at"]),
                }
                for row in connection.execute(
                    "SELECT id,name,lifecycle_state,version,created_at,updated_at FROM organizations "
                    "WHERE record_kind='management_title' AND parent_record_id=? "
                    "AND lifecycle_state='active' ORDER BY name,id",
                    (identity.organization_id,),
                ).fetchall()
            ]

    def activity_logs(self, identity: SessionIdentity, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            predicate = "" if identity.is_admin else " AND a.actor_id=?"
            args: list[Any] = [identity.scope_id]
            if not identity.is_admin:
                args.append(identity.principal_id)
            args.append(max(1, min(limit, 500)))
            rows = connection.execute(
                "SELECT a.id,a.operation_id,a.actor_id,a.action,a.target_resource_id,"
                "a.integrity_hash,a.created_at,p.display_name FROM audit_events a "
                "LEFT JOIN principals p ON p.id=a.actor_id WHERE a.scope_id=?" + predicate +
                " ORDER BY a.created_at DESC,a.id DESC LIMIT ?",
                tuple(args),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "actorName": str(row["display_name"] or "系统"),
                "action": str(row["action"] or ""),
                "entityType": "strict_authority_object",
                "entityId": str(row["target_resource_id"] or ""),
                "detail": {
                    "operationId": str(row["operation_id"] or ""),
                    "integrityHash": str(row["integrity_hash"] or ""),
                },
                "createdAt": str(row["created_at"] or ""),
            }
            for row in rows
        ]

    @staticmethod
    def _system_admin_defaults() -> dict[str, bool]:
        return {
            "allowBusinessSettingsForEmployees": True,
            "allowOrgDnaForEmployees": True,
            "protectEmployeeAdmin": True,
            "protectAiAndCloud": True,
            "protectCloudSecurity": True,
        }

    def system_admin_settings(self, identity: SessionIdentity) -> dict[str, Any]:
        self._require_admin(identity)
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            row = connection.execute(
                "SELECT r.version AS resource_version,r.updated_at,p.policy_spec FROM secured_resources r "
                "LEFT JOIN policy_versions p ON p.secured_resource_id=r.id AND p.scope_id=r.scope_id "
                "AND p.lifecycle_state='active' WHERE r.scope_id=? "
                "AND r.resource_kind='system_admin_settings' AND r.lifecycle_state='active' "
                "ORDER BY p.version DESC LIMIT 1",
                (identity.scope_id,),
            ).fetchone()
        if row is None:
            return {**self._system_admin_defaults(), "updatedAt": "", "version": 0, "expectedVersion": 0}
        spec = _json(row["policy_spec"], {})
        settings = spec.get("settings") if isinstance(spec, Mapping) else {}
        return {
            **self._system_admin_defaults(),
            **(dict(settings) if isinstance(settings, Mapping) else {}),
            "updatedAt": str(row["updated_at"] or ""),
            "version": int(row["resource_version"] or 1),
            "expectedVersion": int(row["resource_version"] or 1),
        }

    def update_system_admin_settings(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        try:
            expected = int(payload.get("expectedVersion", payload.get("expected_version")))
        except (TypeError, ValueError) as exc:
            raise RepositoryError(428, "system_admin_expected_version_required", "系统管理策略写入必须携带 expectedVersion") from exc
        settings = {
            key: bool(payload.get(key, default))
            for key, default in self._system_admin_defaults().items()
        }
        resource_id = _id("secured", identity.scope_id, "system_admin_settings")

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            current = connection.execute(
                "SELECT version,created_at FROM secured_resources WHERE scope_id=? AND id=? "
                "AND lifecycle_state='active'",
                (identity.scope_id, resource_id),
            ).fetchone()
            current_version = int(current["version"] or 1) if current else 0
            if current_version != expected:
                raise RepositoryError(409, "system_admin_version_conflict", "系统管理设置已更新，请刷新后重试")
            next_version = current_version + 1
            if current is None:
                connection.execute(
                    "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,"
                    "resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,'system_admin_settings','active',1,'system_admin_settings',?,?,NULL,'cloud',?)",
                    (resource_id, identity.scope_id, now, now, identity.cloud_instance_id),
                )
            else:
                connection.execute(
                    "UPDATE secured_resources SET version=?,updated_at=? WHERE id=? AND scope_id=?",
                    (next_version, now, resource_id, identity.scope_id),
                )
            policy_id = _id("policy", resource_id, str(next_version))
            connection.execute(
                "INSERT INTO policy_versions (id,scope_id,secured_resource_id,policy_scope_kind,"
                "version,policy_spec_schema_version,policy_spec,effective_at,created_at,lifecycle_state,"
                "updated_at,deleted_at) VALUES (?,?,?,'organization',?,'yiyu.system-admin-settings.v1',"
                "?,?,?,'active',?,NULL)",
                (policy_id, identity.scope_id, resource_id, next_version,
                 canonical_json({"settings": settings}), now, now, now),
            )
            return {**settings, "updatedAt": now, "version": next_version, "expectedVersion": next_version}, next_version

        return self._mutate(
            identity,
            command_type="authorization.system_admin_settings.updated",
            idempotency_key=idempotency_key,
            payload={**settings, "expectedVersion": expected},
            aggregate_type="secured_resource",
            aggregate_id=resource_id,
            mutation=mutation,
        )

    def member(self, identity: SessionIdentity, membership_id: str) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            return self._member(connection, identity, membership_id)

    def set_member_status(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str,
        enabled: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            row = connection.execute(
                "SELECT version,principal_id,role_key FROM organization_memberships "
                "WHERE scope_id=? AND id=? AND record_kind='membership'",
                (identity.scope_id, membership_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "membership_missing", "组织成员不存在")
            if not enabled and str(row["role_key"] or "") == "admin":
                count = connection.execute(
                    "SELECT COUNT(*) FROM organization_memberships WHERE scope_id=? "
                    "AND record_kind='membership' AND role_key='admin' AND status='active'",
                    (identity.scope_id,),
                ).fetchone()[0]
                if int(count) <= 1:
                    raise RepositoryError(409, "last_admin_required", "不能停用组织唯一管理员")
            version = int(row["version"] or 1) + 1
            status = "active" if enabled else "disabled"
            connection.execute(
                "UPDATE organization_memberships SET status=?,version=?,updated_at=? "
                "WHERE scope_id=? AND id=?",
                (status, version, now, identity.scope_id, membership_id),
            )
            connection.execute(
                "UPDATE principals SET status=?,version=version+1,identity_version=identity_version+1,"
                "updated_at=? WHERE id=?",
                (status, now, str(row["principal_id"])),
            )
            return self._member(connection, identity, membership_id), version

        return self._mutate(
            identity,
            command_type="organization.member.enabled" if enabled else "organization.member.disabled",
            idempotency_key=idempotency_key,
            payload={"membershipId": membership_id, "enabled": enabled},
            aggregate_type="organization_membership",
            aggregate_id=membership_id,
            mutation=mutation,
        )

    def set_member_role(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str,
        role: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        role_key = "admin" if role == "admin" else "member"

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            row = connection.execute(
                "SELECT version,role_key FROM organization_memberships WHERE scope_id=? "
                "AND id=? AND record_kind='membership'",
                (identity.scope_id, membership_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "membership_missing", "组织成员不存在")
            if str(row["role_key"] or "") == "admin" and role_key != "admin":
                count = connection.execute(
                    "SELECT COUNT(*) FROM organization_memberships WHERE scope_id=? "
                    "AND record_kind='membership' AND role_key='admin' AND status='active'",
                    (identity.scope_id,),
                ).fetchone()[0]
                if int(count) <= 1:
                    raise RepositoryError(409, "last_admin_required", "组织必须保留至少一名管理员")
            version = int(row["version"] or 1) + 1
            connection.execute(
                "UPDATE organization_memberships SET role_key=?,version=?,updated_at=? "
                "WHERE scope_id=? AND id=?",
                (role_key, version, now, identity.scope_id, membership_id),
            )
            return self._member(connection, identity, membership_id), version

        return self._mutate(
            identity,
            command_type="organization.member.role_updated",
            idempotency_key=idempotency_key,
            payload={"membershipId": membership_id, "role": role_key},
            aggregate_type="organization_membership",
            aggregate_id=membership_id,
            mutation=mutation,
        )

    def set_member_department(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str,
        department_id: str | None,
        department_lead: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            self._member(connection, identity, membership_id)
            if department_id:
                department = connection.execute(
                    "SELECT id FROM organizations WHERE id=? AND record_kind='department' "
                    "AND lifecycle_state='active'",
                    (department_id,),
                ).fetchone()
                if department is None:
                    raise RepositoryError(404, "department_missing", "所选部门不存在")
            current = self._department_assignment(connection, identity.scope_id, membership_id)
            assignment_id = (
                str(current["id"])
                if current is not None
                else _id("department_assignment", identity.scope_id, membership_id)
            )
            version = int(current["version"] or 1) + 1 if current else 1
            if not department_id:
                if current is not None:
                    connection.execute(
                        "UPDATE organization_memberships SET lifecycle_state='deleted',"
                        "deleted_at=?,updated_at=?,version=? WHERE id=?",
                        (now, now, version, assignment_id),
                    )
            elif current is None:
                connection.execute(
                    "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
                    "status,version,record_kind,parent_membership_id,department_id,title_id,"
                    "manager_membership_id,visibility_scope,capability_set_schema_version,"
                    "capability_set,target_type,target_id,expires_at,lifecycle_state,created_at,"
                    "updated_at,deleted_at) "
                    "VALUES (?,?,NULL,?,'active',1,'department_assignment',?,?,NULL,NULL,NULL,"
                    "NULL,NULL,NULL,NULL,NULL,'active',?,?,NULL)",
                    (
                        assignment_id, identity.scope_id,
                        "department_lead" if department_lead else "member",
                        membership_id, department_id, now, now,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE organization_memberships SET role_key=?,department_id=?,status='active',"
                    "lifecycle_state='active',deleted_at=NULL,version=?,updated_at=? WHERE id=?",
                    (
                        "department_lead" if department_lead else "member",
                        department_id, version, now, assignment_id,
                    ),
                )
            return self._member(connection, identity, membership_id), version

        return self._mutate(
            identity,
            command_type="organization.member.department_updated",
            idempotency_key=idempotency_key,
            payload={
                "membershipId": membership_id,
                "departmentId": department_id,
                "departmentLead": department_lead,
            },
            aggregate_type="organization_membership",
            aggregate_id=membership_id,
            mutation=mutation,
        )

    def set_member_management_title(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str,
        title_id: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            self._member(connection, identity, membership_id)
            if title_id:
                title = connection.execute(
                    "SELECT id FROM organizations WHERE id=? "
                    "AND record_kind='management_title' "
                    "AND parent_record_id=? AND lifecycle_state='active'",
                    (title_id, identity.organization_id),
                ).fetchone()
                if title is None:
                    raise RepositoryError(404, "management_title_missing", "所选管理层头衔不存在")
            current = self._title_assignment(connection, identity.scope_id, membership_id)
            assignment_id = (
                str(current["id"])
                if current is not None
                else _id("title_assignment", identity.scope_id, membership_id)
            )
            version = int(current["version"] or 1) + 1 if current else 1
            if current is None and title_id:
                connection.execute(
                    "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
                    "status,version,record_kind,parent_membership_id,department_id,title_id,"
                    "manager_membership_id,visibility_scope,capability_set_schema_version,"
                    "capability_set,target_type,target_id,expires_at,lifecycle_state,created_at,"
                    "updated_at,deleted_at) VALUES "
                    "(?,?,NULL,'member','active',1,'title_assignment',?,NULL,?,NULL,"
                    "NULL,NULL,NULL,NULL,NULL,NULL,'active',?,?,NULL)",
                    (assignment_id, identity.scope_id, membership_id, title_id, now, now),
                )
            elif current is not None:
                if not title_id:
                    connection.execute(
                        "UPDATE organization_memberships SET title_id=NULL,lifecycle_state='deleted',"
                        "deleted_at=?,updated_at=?,version=? WHERE id=?",
                        (now, now, version, assignment_id),
                    )
                else:
                    connection.execute(
                        "UPDATE organization_memberships SET title_id=?,status='active',"
                        "lifecycle_state='active',deleted_at=NULL,version=?,updated_at=? WHERE id=?",
                        (title_id, version, now, assignment_id),
                    )
            return self._member(connection, identity, membership_id), version

        return self._mutate(
            identity,
            command_type="organization.member.management_title_updated",
            idempotency_key=idempotency_key,
            payload={"membershipId": membership_id, "managementTitleId": title_id},
            aggregate_type="organization_membership",
            aggregate_id=membership_id,
            mutation=mutation,
        )

    def transfer_admin(
        self,
        identity: SessionIdentity,
        *,
        target_membership_id: str,
        current_admin_action: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        if target_membership_id == identity.membership_id:
            raise RepositoryError(422, "admin_target_invalid", "目标成员不能是当前管理员")

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            target = connection.execute(
                "SELECT version,status FROM organization_memberships WHERE scope_id=? "
                "AND id=? AND record_kind='membership' AND lifecycle_state='active'",
                (identity.scope_id, target_membership_id),
            ).fetchone()
            current = connection.execute(
                "SELECT version FROM organization_memberships WHERE scope_id=? AND id=? "
                "AND record_kind='membership' AND lifecycle_state='active'",
                (identity.scope_id, identity.membership_id),
            ).fetchone()
            if target is None or str(target["status"] or "") != "active":
                raise RepositoryError(404, "membership_missing", "目标成员不存在或已停用")
            if current is None:
                raise RepositoryError(403, "membership_inactive", "当前管理员身份不可用")
            target_version = int(target["version"] or 1) + 1
            connection.execute(
                "UPDATE organization_memberships SET role_key='admin',visibility_scope='organization',"
                "version=?,updated_at=? WHERE scope_id=? AND id=?",
                (target_version, now, identity.scope_id, target_membership_id),
            )
            if current_admin_action in {"demote_to_member", "disable_self"}:
                status = "disabled" if current_admin_action == "disable_self" else "active"
                connection.execute(
                    "UPDATE organization_memberships SET role_key='member',status=?,"
                    "visibility_scope=CASE WHEN visibility_scope='organization' THEN 'self' "
                    "ELSE visibility_scope END,version=version+1,updated_at=? "
                    "WHERE scope_id=? AND id=?",
                    (status, now, identity.scope_id, identity.membership_id),
                )
            return {
                "message": "管理员已移交",
                "targetUserId": target_membership_id,
                "currentAdminAction": current_admin_action,
            }, target_version

        return self._mutate(
            identity,
            command_type="organization.admin.transferred",
            idempotency_key=idempotency_key,
            payload={
                "targetMembershipId": target_membership_id,
                "currentAdminAction": current_admin_action,
            },
            aggregate_type="organization_membership",
            aggregate_id=target_membership_id,
            mutation=mutation,
        )

    def _store_password_credential(
        self,
        credential_id: str,
        version: int,
        password: str,
    ) -> tuple[str, str]:
        target_dir = (self.repository.database_path.parent / ".runtime-secrets" / "credentials").resolve()
        target_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(target_dir, 0o700)
        serialized = canonical_json({"hashScheme": PASSWORD_SCHEME, "secretHash": hash_password(password)})
        target = target_dir / f"{credential_id}.v{version}.json"
        temporary = target.with_suffix(".tmp-" + new_id())
        try:
            temporary.write_text(serialized, encoding="utf-8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return str(target), sha256_text(serialized)[:16]

    def reset_password(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str,
        new_password: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        if len(new_password) < 8:
            raise RepositoryError(422, "password_too_short", "新密码至少需要 8 位")

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            target = connection.execute(
                "SELECT m.principal_id,c.id AS credential_id,c.version FROM organization_memberships m "
                "JOIN principals c ON c.parent_principal_id=m.principal_id "
                "AND c.principal_kind='credential' AND c.credential_type='password' "
                "AND c.credential_state='active' AND c.lifecycle_state='active' "
                "WHERE m.scope_id=? AND m.id=? AND m.record_kind='membership' "
                "AND m.lifecycle_state='active'",
                (identity.scope_id, membership_id),
            ).fetchone()
            if target is None:
                raise RepositoryError(404, "membership_credential_missing", "组织成员或登录凭据不存在")
            version = int(target["version"] or 1) + 1
            reference, fingerprint = self._store_password_credential(
                str(target["credential_id"]), version, new_password
            )
            connection.execute(
                "UPDATE principals SET secret_reference=?,secret_fingerprint=?,identity_version="
                "identity_version+1,version=?,updated_at=? WHERE id=?",
                (reference, fingerprint, version, now, str(target["credential_id"])),
            )
            connection.execute(
                "UPDATE sandboxes SET runtime_status='revoked',version=version+1,updated_at=?,"
                "last_seen_at=? WHERE scope_id=? AND principal_id=? AND record_kind='server_session' "
                "AND lifecycle_state='active' AND runtime_status='active'",
                (now, now, identity.scope_id, str(target["principal_id"])),
            )
            return {"message": "密码已重置，原登录会话已失效", "membershipId": membership_id}, version

        return self._mutate(
            identity,
            command_type="organization.member.password_reset",
            idempotency_key=idempotency_key,
            payload={"membershipId": membership_id, "passwordFingerprint": sha256_text(new_password)},
            aggregate_type="principal_credential",
            aggregate_id=membership_id,
            mutation=mutation,
        )

    @staticmethod
    def _application_result(row: Mapping[str, Any]) -> dict[str, Any]:
        payload = _json(row["capability_set"], {})
        return {
            "applicationId": str(row["id"]),
            "membershipId": payload.get("membershipId"),
            "applicationState": str(row["status"] or "pending"),
            "departmentId": row["department_id"],
            "managementTitleId": row["title_id"],
            "jobTitle": str(payload.get("jobTitle") or ""),
            "managerName": str(payload.get("managerName") or ""),
            "currentFocus": str(payload.get("currentFocus") or ""),
            "rejectionReason": str(payload.get("rejectionReason") or "") or None,
            "submittedAt": str(row["created_at"] or ""),
            "decidedAt": str(row["updated_at"] or "") if str(row["status"] or "") != "pending" else None,
            "version": int(row["version"] or 1),
        }

    def membership_application(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            target_membership_id = membership_id or identity.membership_id
            membership = connection.execute(
                "SELECT principal_id FROM organization_memberships WHERE scope_id=? AND id=? "
                "AND record_kind='membership' AND lifecycle_state='active'",
                (identity.scope_id, target_membership_id),
            ).fetchone()
            if membership is None:
                return None
            row = connection.execute(
                "SELECT * FROM organization_memberships WHERE scope_id=? AND principal_id=? "
                "AND record_kind='application' AND lifecycle_state='active' "
                "ORDER BY updated_at DESC,id DESC LIMIT 1",
                (identity.scope_id, str(membership["principal_id"])),
            ).fetchone()
            return self._application_result(row) if row is not None else None

    def submit_membership_application(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        department_id = str(payload.get("departmentId") or "").strip() or None
        title_id = str(payload.get("managementTitleId") or "").strip() or None
        normalized = {
            "membershipId": identity.membership_id,
            "departmentId": department_id,
            "managementTitleId": title_id,
            "jobTitle": str(payload.get("jobTitle") or "").strip(),
            "managerName": str(payload.get("managerName") or "").strip(),
            "currentFocus": str(payload.get("currentFocus") or "").strip(),
            "rejectionReason": "",
        }
        if not any(normalized.get(key) for key in ("departmentId", "managementTitleId", "jobTitle", "managerName", "currentFocus")):
            raise RepositoryError(422, "membership_application_empty", "请至少填写一项组织身份调整内容")
        application_id = _id("membership_application", identity.scope_id, identity.membership_id, idempotency_key)

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            if department_id and connection.execute(
                "SELECT 1 FROM organizations WHERE id=? AND record_kind='department' "
                "AND lifecycle_state='active'", (department_id,)
            ).fetchone() is None:
                raise RepositoryError(404, "department_missing", "申请部门不存在")
            if title_id and connection.execute(
                "SELECT 1 FROM organizations WHERE id=? AND record_kind='management_title' "
                "AND lifecycle_state='active'", (title_id,)
            ).fetchone() is None:
                raise RepositoryError(404, "management_title_missing", "申请岗位不存在")
            pending = connection.execute(
                "SELECT id FROM organization_memberships WHERE scope_id=? AND principal_id=? "
                "AND record_kind='application' AND status='pending' AND lifecycle_state='active'",
                (identity.scope_id, identity.principal_id),
            ).fetchone()
            if pending is not None:
                raise RepositoryError(409, "membership_application_pending", "已有待处理的组织身份调整申请")
            connection.execute(
                "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,"
                "version,record_kind,parent_membership_id,department_id,title_id,manager_membership_id,"
                "visibility_scope,capability_set_schema_version,capability_set,target_type,target_id,"
                "expires_at,lifecycle_state,created_at,updated_at,deleted_at) VALUES "
                "(?,?,?,'member','pending',1,'application',NULL,?,?,NULL,NULL,"
                "'yiyu.membership-application.v1',?,NULL,NULL,NULL,'active',?,?,NULL)",
                (application_id, identity.scope_id, identity.principal_id,
                 department_id, title_id, canonical_json(normalized), now, now),
            )
            row = connection.execute(
                "SELECT * FROM organization_memberships WHERE id=?", (application_id,)
            ).fetchone()
            return self._application_result(row), 1

        return self._mutate(
            identity,
            command_type="organization.membership_application.submitted",
            idempotency_key=idempotency_key,
            payload=normalized,
            aggregate_type="organization_membership",
            aggregate_id=application_id,
            mutation=mutation,
        )

    def decide_membership_application(
        self,
        identity: SessionIdentity,
        *,
        application_id: str,
        decision: str,
        rejection_reason: str,
        expected_version: int | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self._require_admin(identity)
        target = "approved" if decision in {"approve", "approved"} else "rejected" if decision in {"reject", "rejected"} else None
        if target is None:
            raise RepositoryError(422, "membership_application_decision_invalid", "申请决定无效")
        if target == "rejected" and not rejection_reason.strip():
            raise RepositoryError(422, "membership_application_reason_required", "请填写拒绝原因")

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            row = connection.execute(
                "SELECT * FROM organization_memberships WHERE scope_id=? AND id=? "
                "AND record_kind='application' AND lifecycle_state='active'",
                (identity.scope_id, application_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "membership_application_missing", "组织身份调整申请不存在")
            current_version = int(row["version"] or 1)
            if expected_version is not None and expected_version != current_version:
                raise RepositoryError(409, "membership_application_version_conflict", "申请已更新，请刷新后重试")
            if str(row["status"] or "") != "pending":
                raise RepositoryError(409, "membership_application_already_decided", "申请已经处理")
            application_payload = _json(row["capability_set"], {})
            application_payload["rejectionReason"] = rejection_reason.strip() if target == "rejected" else ""
            version = current_version + 1
            connection.execute(
                "UPDATE organization_memberships SET status=?,capability_set=?,version=?,updated_at=? "
                "WHERE id=? AND scope_id=?",
                (target, canonical_json(application_payload), version, now, application_id, identity.scope_id),
            )
            if target == "approved":
                membership_id = str(application_payload.get("membershipId") or "")
                membership = connection.execute(
                    "SELECT id FROM organization_memberships WHERE scope_id=? AND id=? "
                    "AND principal_id=? AND record_kind='membership' AND lifecycle_state='active'",
                    (identity.scope_id, membership_id, str(row["principal_id"])),
                ).fetchone()
                if membership is None:
                    raise RepositoryError(409, "membership_application_target_missing", "申请对应的正式成员不存在")
                department_id = str(row["department_id"] or "").strip() or None
                if department_id:
                    current = self._department_assignment(connection, identity.scope_id, membership_id)
                    if current is None:
                        connection.execute(
                            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
                            "status,version,record_kind,parent_membership_id,department_id,title_id,"
                            "manager_membership_id,visibility_scope,capability_set_schema_version,"
                            "capability_set,target_type,target_id,expires_at,lifecycle_state,created_at,"
                            "updated_at,deleted_at) VALUES (?,?,NULL,'member','active',1,"
                            "'department_assignment',?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,"
                            "'active',?,?,NULL)",
                            (_id("department_assignment", identity.scope_id, membership_id),
                             identity.scope_id, membership_id, department_id, now, now),
                        )
                    else:
                        connection.execute(
                            "UPDATE organization_memberships SET department_id=?,status='active',"
                            "lifecycle_state='active',deleted_at=NULL,version=version+1,updated_at=? "
                            "WHERE id=?",
                            (department_id, now, str(current["id"])),
                        )
            updated = connection.execute(
                "SELECT * FROM organization_memberships WHERE id=?", (application_id,)
            ).fetchone()
            return self._application_result(updated), version

        return self._mutate(
            identity,
            command_type="organization.membership_application." + target,
            idempotency_key=idempotency_key,
            payload={"applicationId": application_id, "decision": target, "reason": rejection_reason},
            aggregate_type="organization_membership",
            aggregate_id=application_id,
            mutation=mutation,
        )

    @staticmethod
    def _bot_handle(value: str) -> str:
        handle = value.strip().casefold().lstrip("@")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", handle):
            raise RepositoryError(422, "bot_handle_invalid", "机器人简称需为3至64位字母、数字、下划线或短横线")
        return handle

    def _store_bot_token(self, bot_id: str, version: int, token: str) -> tuple[str, str]:
        self.bot_secret_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.bot_secret_dir, 0o700)
        encrypted = self.repository.cipher.encrypt(token)
        target = self.bot_secret_dir / f"{bot_id}.v{version}.json"
        temporary = target.with_suffix(".tmp-" + new_id())
        try:
            temporary.write_text(
                canonical_json(
                    {
                        "encryptedBotToken": encrypted.ciphertext,
                        "fingerprint": encrypted.fingerprint,
                    }
                ),
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return str(target), encrypted.fingerprint

    @staticmethod
    def _capabilities(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        enabled = {str(value) for value in payload.get("enabled_capabilities") or []}
        invalid = enabled - set(BOT_CAPABILITIES)
        if invalid:
            raise RepositoryError(422, "bot_capability_invalid", "包含未登记的机器人能力")
        return [
            {"capability_key": key, "enabled": key in enabled}
            for key in BOT_CAPABILITIES
        ]

    def _bot(self, connection: Any, identity: SessionIdentity, *, bot_id: str | None = None, handle: str | None = None) -> dict[str, Any]:
        predicate = "b.id=?" if bot_id else "b.handle=?"
        value = bot_id or handle
        row = connection.execute(
            "SELECT b.*,p.display_name,m.id AS bot_membership_id,m.status AS membership_status,"
            "d.name AS department_name FROM bot_definitions b "
            "JOIN principals p ON p.id=b.id AND p.principal_kind='bot' "
            "LEFT JOIN organization_memberships m ON m.scope_id=b.scope_id "
            "AND m.principal_id=b.id AND m.record_kind='membership' "
            "LEFT JOIN organizations d ON d.id=b.department_id "
            f"WHERE b.scope_id=? AND {predicate} AND b.agent_kind IS NULL "
            "AND b.lifecycle_state!='deleted'",
            (identity.scope_id, value),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "bot_missing", "机器人同事不存在")
        policy = _json(row["capability_set"] if "capability_set" in row.keys() else None, [])
        if not isinstance(policy, list):
            policy = []
        reporting_rows = connection.execute(
            "SELECT manager_membership_id FROM organization_memberships WHERE scope_id=? "
            "AND record_kind='reporting_line' AND parent_membership_id=? "
            "AND lifecycle_state='active'",
            (identity.scope_id, str(row["bot_membership_id"] or "")),
        ).fetchall()
        manager_ids = [str(item["manager_membership_id"]) for item in reporting_rows]
        creator_ids = [str(row["owner_membership_id"])] if row["owner_membership_id"] in manager_ids else []
        department_leader_ids: list[str] = []
        ceo_ids: list[str] = []
        if manager_ids:
            placeholders = ",".join("?" for _ in manager_ids)
            manager_rows = connection.execute(
                "SELECT m.id,m.role_key,a.role_key AS department_role FROM organization_memberships m "
                "LEFT JOIN organization_memberships a ON a.scope_id=m.scope_id "
                "AND a.record_kind='department_assignment' AND a.parent_membership_id=m.id "
                "AND a.lifecycle_state='active' WHERE m.scope_id=? AND m.id IN (" + placeholders + ")",
                (identity.scope_id, *manager_ids),
            ).fetchall()
            department_leader_ids = [
                str(item["id"]) for item in manager_rows
                if str(item["department_role"] or "") == "department_lead"
            ]
            ceo_ids = [
                str(item["id"]) for item in manager_rows
                if str(item["role_key"] or "") == "admin"
            ]
        reporting = {
            "report_to_creator": bool(creator_ids),
            "report_to_department_lead": bool(department_leader_ids),
            "report_to_ceo": bool(ceo_ids),
            "creator_user_ids": creator_ids,
            "department_leader_user_ids": department_leader_ids,
            "ceo_user_ids": ceo_ids,
        }
        enabled = bool(row["enabled"]) and str(row["membership_status"] or "") == "active"
        return {
            "id": str(row["id"]),
            "bot_member_id": str(row["id"]),
            "display_name": str(row["display_name"] or row["handle"] or "机器人同事"),
            "handle": str(row["handle"] or ""),
            "actor_id": str(row["id"]),
            "actor_type": "organization_bot",
            "department_id": row["department_id"],
            "department_name": str(row["department_name"] or ""),
            "description": str(row["description"] or ""),
            "status": "active" if enabled else "disabled",
            "reporting": reporting,
            "capabilities": policy,
            "token_prefix": str(row["secret_fingerprint"] or "")[:8],
            "token_rotated_at": str(row["updated_at"] or ""),
            "has_token": bool(row["secret_reference"]),
            "version": int(row["version"] or 1),
            "expectedVersion": int(row["version"] or 1),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def bots(self, identity: SessionIdentity, *, status: str | None = None) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM bot_definitions WHERE scope_id=? "
                    "AND agent_kind IS NULL AND lifecycle_state!='deleted' "
                    "ORDER BY created_at,id",
                    (identity.scope_id,),
                ).fetchall()
            ]
            values = [self._bot(connection, identity, bot_id=item) for item in ids]
        return [item for item in values if not status or item["status"] == status]

    def bot(self, identity: SessionIdentity, *, bot_id: str | None = None, handle: str | None = None) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            return self._bot(connection, identity, bot_id=bot_id, handle=handle)

    def _replace_reporting(
        self,
        connection: Any,
        identity: SessionIdentity,
        *,
        bot_membership_id: str,
        department_id: str | None,
        payload: Mapping[str, Any],
        now: str,
    ) -> None:
        connection.execute(
            "UPDATE organization_memberships SET lifecycle_state='deleted',deleted_at=?,"
            "updated_at=?,version=version+1 WHERE scope_id=? "
            "AND record_kind='reporting_line' AND parent_membership_id=? "
            "AND lifecycle_state='active'",
            (now, now, identity.scope_id, bot_membership_id),
        )
        targets: list[str] = []
        if payload.get("report_to_creator", True):
            targets.append(identity.membership_id)
        if payload.get("report_to_department_lead") and department_id:
            rows = connection.execute(
                "SELECT parent_membership_id FROM organization_memberships WHERE scope_id=? "
                "AND record_kind='department_assignment' AND department_id=? "
                "AND role_key='department_lead' AND status='active' AND lifecycle_state='active'",
                (identity.scope_id, department_id),
            ).fetchall()
            targets.extend(str(row["parent_membership_id"]) for row in rows)
        if payload.get("report_to_ceo"):
            rows = connection.execute(
                "SELECT id FROM organization_memberships WHERE scope_id=? "
                "AND record_kind='membership' AND role_key='admin' "
                "AND status='active' AND lifecycle_state='active'",
                (identity.scope_id,),
            ).fetchall()
            targets.extend(str(row["id"]) for row in rows)
        if not targets:
            raise RepositoryError(422, "bot_reporting_required", "机器人至少需要一名汇报对象")
        for target_id in sorted(set(targets)):
            connection.execute(
                "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,"
                "status,version,record_kind,parent_membership_id,department_id,title_id,"
                "manager_membership_id,visibility_scope,capability_set_schema_version,"
                "capability_set,target_type,target_id,expires_at,lifecycle_state,created_at,"
                "updated_at,deleted_at) VALUES "
                "(?,?,NULL,'member','active',1,'reporting_line',?,NULL,NULL,?,"
                "NULL,NULL,NULL,NULL,NULL,NULL,'active',?,?,NULL)",
                (
                    _id("bot_report", identity.scope_id, bot_membership_id, target_id),
                    identity.scope_id, bot_membership_id, target_id, now, now,
                ),
            )

    def create_bot(self, identity: SessionIdentity, *, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        self._require_admin(identity)
        display_name = str(payload.get("display_name") or "").strip()
        if not display_name:
            raise RepositoryError(422, "bot_display_name_required", "机器人名称不能为空")
        department_id = str(payload.get("department_id") or "").strip() or None
        capabilities = self._capabilities(payload)
        requested_handle = str(payload.get("handle") or "").strip()
        token_plain = str(payload.get("new_token") or "").strip() or new_secret_token()
        bot_id = new_id()

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            if department_id and connection.execute(
                "SELECT 1 FROM organizations WHERE id=? AND record_kind='department' "
                "AND lifecycle_state='active'", (department_id,)
            ).fetchone() is None:
                raise RepositoryError(404, "bot_department_missing", "机器人所属部门不存在")
            handle = self._bot_handle(requested_handle or f"bot-{bot_id[-12:]}")
            if connection.execute(
                "SELECT 1 FROM bot_definitions WHERE scope_id=? AND handle=? "
                "AND lifecycle_state!='deleted'", (identity.scope_id, handle)
            ).fetchone() is not None:
                raise RepositoryError(409, "bot_handle_exists", "机器人简称已存在")
            secret_reference, secret_fingerprint = self._store_bot_token(bot_id, 1, token_plain)
            bot_membership_id = _id("bot_membership", identity.scope_id, bot_id)
            connection.execute(
                "INSERT INTO principals (id,status,identity_version,updated_at,principal_kind,"
                "parent_principal_id,display_name,contact_type,normalized_contact,verification_state,"
                "credential_type,secret_reference,secret_fingerprint,credential_state,version,"
                "lifecycle_state,created_at,deleted_at) VALUES "
                "(?,'active',1,?,'bot',NULL,?,NULL,NULL,'verified','bot_token',?,?,"
                "'active',1,'active',?,NULL)",
                (bot_id, now, display_name, secret_reference, secret_fingerprint, now),
            )
            connection.execute(
                "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,"
                "version,record_kind,parent_membership_id,department_id,title_id,manager_membership_id,"
                "visibility_scope,capability_set_schema_version,capability_set,target_type,target_id,"
                "expires_at,lifecycle_state,created_at,updated_at,deleted_at"
                ") VALUES (?,?,?,'member','active',1,'membership',NULL,?,NULL,?,"
                "'organization','yiyu.bot-capabilities.v1',?,NULL,NULL,NULL,'active',?,?,NULL)",
                (
                    bot_membership_id, identity.scope_id, bot_id, department_id,
                    identity.membership_id, canonical_json(capabilities), now, now,
                ),
            )
            connection.execute(
                "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,"
                "version,resource_type_key,created_at,updated_at,deleted_at,authority_role,"
                "origin_instance_id) VALUES (?,?,'bot_definition','active',1,?, ?,?,NULL,"
                "'cloud',?)",
                (
                    bot_id,
                    identity.scope_id,
                    "organization_colleague",
                    now,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO bot_definitions (id,scope_id,agent_kind,owner_principal_id,"
                "owner_membership_id,permission_policy_id,version,handle,description,department_id,"
                "capability_policy_version,secret_reference,secret_fingerprint,enabled,"
                "lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES (?,?,NULL,NULL,?,NULL,1,?,?,?,'yiyu.bot-capabilities.v1',"
                "?,?,1,'active',?,?,NULL)",
                (
                    bot_id, identity.scope_id, identity.membership_id,
                    handle, str(payload.get("description") or "").strip(),
                    department_id, secret_reference, secret_fingerprint, now, now,
                ),
            )
            self._replace_reporting(
                connection, identity, bot_membership_id=bot_membership_id,
                department_id=department_id, payload=payload, now=now,
            )
            result = self._bot(connection, identity, bot_id=bot_id)
            result["token_plain"] = token_plain
            return result, 1

        return self._mutate(
            identity,
            command_type="organization.bot.created",
            idempotency_key=idempotency_key,
            payload={
                "displayName": display_name,
                "departmentId": department_id,
                "description": str(payload.get("description") or ""),
                "capabilities": capabilities,
            },
            aggregate_type="bot_definition",
            aggregate_id=bot_id,
            mutation=mutation,
        )

    def update_bot(self, identity: SessionIdentity, *, bot_id: str, payload: Mapping[str, Any], idempotency_key: str) -> dict[str, Any]:
        self._require_admin(identity)

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            current = self._bot(connection, identity, bot_id=bot_id)
            expected = int(payload.get("expectedVersion") or current["version"])
            if expected != int(current["version"]):
                raise RepositoryError(409, "bot_version_conflict", "机器人资料已更新，请刷新后重试")
            display_name = str(payload.get("display_name") or current["display_name"]).strip()
            department_id = str(payload.get("department_id") or current.get("department_id") or "").strip() or None
            capabilities = self._capabilities(payload) if "enabled_capabilities" in payload else list(current["capabilities"])
            enabled = (str(payload.get("status") or current["status"]) == "active")
            version = int(current["version"]) + 1
            connection.execute(
                "UPDATE bot_definitions SET description=?,department_id=?,enabled=?,version=?,"
                "capability_policy_version='yiyu.bot-capabilities.v1',updated_at=? "
                "WHERE id=? AND scope_id=?",
                (str(payload.get("description") or current["description"]), department_id, int(enabled), version, now, bot_id, identity.scope_id),
            )
            connection.execute(
                "UPDATE principals SET display_name=?,status=?,version=version+1,updated_at=? WHERE id=?",
                (display_name, "active" if enabled else "disabled", now, bot_id),
            )
            membership = connection.execute(
                "SELECT id FROM organization_memberships WHERE scope_id=? AND principal_id=? "
                "AND record_kind='membership'", (identity.scope_id, bot_id)
            ).fetchone()
            if membership is None:
                raise RepositoryError(409, "bot_membership_missing", "机器人组织身份缺失")
            connection.execute(
                "UPDATE organization_memberships SET status=?,department_id=?,capability_set=?,"
                "version=version+1,updated_at=? WHERE id=?",
                ("active" if enabled else "disabled", department_id, canonical_json(capabilities), now, str(membership["id"])),
            )
            self._replace_reporting(
                connection, identity, bot_membership_id=str(membership["id"]),
                department_id=department_id, payload=payload, now=now,
            )
            return self._bot(connection, identity, bot_id=bot_id), version

        return self._mutate(
            identity,
            command_type="organization.bot.updated",
            idempotency_key=idempotency_key,
            payload={key: value for key, value in payload.items() if key != "new_token"},
            aggregate_type="bot_definition",
            aggregate_id=bot_id,
            mutation=mutation,
        )

    def rotate_bot_token(self, identity: SessionIdentity, *, bot_id: str, expected_version: int, presented_token: str | None, idempotency_key: str) -> dict[str, Any]:
        self._require_admin(identity)
        token_plain = presented_token or new_secret_token()

        def mutation(connection: Any, now: str) -> tuple[dict[str, Any], int]:
            current = self._bot(connection, identity, bot_id=bot_id)
            if expected_version != int(current["version"]):
                raise RepositoryError(409, "bot_version_conflict", "机器人资料已更新，请刷新后重试")
            version = int(current["version"]) + 1
            reference, fingerprint = self._store_bot_token(bot_id, version, token_plain)
            connection.execute(
                "UPDATE bot_definitions SET secret_reference=?,secret_fingerprint=?,version=?,"
                "updated_at=? WHERE scope_id=? AND id=?",
                (reference, fingerprint, version, now, identity.scope_id, bot_id),
            )
            connection.execute(
                "UPDATE principals SET secret_reference=?,secret_fingerprint=?,identity_version="
                "identity_version+1,version=version+1,updated_at=? WHERE id=?",
                (reference, fingerprint, now, bot_id),
            )
            result = self._bot(connection, identity, bot_id=bot_id)
            result["token_plain"] = token_plain
            return result, version

        return self._mutate(
            identity,
            command_type="organization.bot.token_rotated",
            idempotency_key=idempotency_key,
            payload={"botId": bot_id, "expectedVersion": expected_version, "tokenFingerprint": sha256_text(token_plain)},
            aggregate_type="bot_definition",
            aggregate_id=bot_id,
            mutation=mutation,
        )

    def bot_permissions(self, identity: SessionIdentity, *, bot_id: str) -> dict[str, Any]:
        record = self.bot(identity, bot_id=bot_id)
        return {
            "bot_member_id": bot_id,
            "capabilities": record["capabilities"],
            "reporting": record["reporting"],
            "version": record["version"],
        }

    @staticmethod
    def _plan_record(row: Mapping[str, Any], receipt: Mapping[str, Any]) -> dict[str, Any]:
        plan = receipt.get("plan") if isinstance(receipt.get("plan"), Mapping) else {}
        status_map = {
            "draft": "pending_approval",
            "approved": "approved",
            "rejected": "rejected",
            "changes_requested": "needs_revision",
        }
        return {
            "id": str(row["id"]),
            "bot_member_id": str(plan.get("botId") or ""),
            "client_id": plan.get("clientId"),
            "plan_title": str(plan.get("planTitle") or "未命名方案"),
            "plan_text": str(plan.get("planText") or ""),
            "required_modules_json": canonical_json(list(plan.get("requiredModules") or [])),
            "steps_json": canonical_json(list(plan.get("steps") or [])),
            "expected_outputs_json": canonical_json(list(plan.get("expectedOutputs") or [])),
            "approval_required": True,
            "approval_id": row["approval_id"] if "approval_id" in row.keys() else None,
            "approval_source": "human_explicit",
            "status": status_map.get(str(row["status"] or "draft"), str(row["status"] or "draft")),
            "human_initiator_id": plan.get("initiatorMembershipId"),
            "approved_by": row["approved_by"] if "approved_by" in row.keys() else None,
            "approved_at": row["approved_at"] if "approved_at" in row.keys() else None,
            "supervisor_feedback": row["decision_note"] if "decision_note" in row.keys() else None,
            "plan_version": int(row["version"] or 1),
            "prev_plan_json": None,
            "created_at": str(row["created_at"] or ""),
        }

    def _plan_row(self, connection: Any, identity: SessionIdentity, plan_id: str) -> tuple[Any, dict[str, Any]]:
        row = connection.execute(
            "SELECT p.*,m.receipt,a.id AS approval_id,a.approver_membership_id AS approved_by,"
            "a.decided_at AS approved_at,a.decision_note FROM ai_proposals p "
            "JOIN object_manifests m ON m.id=p.payload_object_manifest_id AND m.scope_id=p.scope_id "
            "LEFT JOIN ai_approvals a ON a.proposal_id=p.id AND a.scope_id=p.scope_id "
            "AND a.lifecycle_state='active' WHERE p.scope_id=? AND p.id=? "
            "AND p.operation_kind='task_prep' AND p.lifecycle_state='active' "
            "ORDER BY a.decided_at DESC LIMIT 1",
            (identity.scope_id, plan_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "bot_task_plan_missing", "机器人任务方案不存在")
        receipt = _json(row["receipt"], {})
        if not isinstance(receipt, Mapping) or not isinstance(receipt.get("plan"), Mapping):
            raise RepositoryError(409, "bot_task_plan_receipt_invalid", "机器人任务方案回执不可用")
        return row, dict(receipt)

    def bot_plans(
        self,
        identity: SessionIdentity,
        *,
        bot_id: str,
        status: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            self._bot(connection, identity, bot_id=bot_id)
            rows = connection.execute(
                "SELECT p.id FROM ai_proposals p JOIN object_manifests m "
                "ON m.id=p.payload_object_manifest_id AND m.scope_id=p.scope_id "
                "WHERE p.scope_id=? AND p.operation_kind='task_prep' "
                "AND p.lifecycle_state='active' AND json_extract(m.receipt,'$.plan.botId')=? "
                "ORDER BY p.updated_at DESC,p.id DESC LIMIT ?",
                (identity.scope_id, bot_id, limit),
            ).fetchall()
            records = []
            for item in rows:
                row, receipt = self._plan_row(connection, identity, str(item["id"]))
                record = self._plan_record(row, receipt)
                if not status or record["status"] == status:
                    records.append(record)
            return records

    def create_bot_plan(
        self,
        identity: SessionIdentity,
        *,
        bot_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        title = str(payload.get("plan_title") or "").strip()
        if not title:
            raise RepositoryError(422, "bot_task_plan_title_required", "请填写任务方案标题")
        client_id = str(payload.get("client_id") or "").strip() or None
        plan = {
            "botId": bot_id,
            "clientId": client_id,
            "planTitle": title,
            "planText": str(payload.get("plan_text") or ""),
            "requiredModules": list(payload.get("required_modules") or []),
            "steps": list(payload.get("steps") or []),
            "expectedOutputs": list(payload.get("expected_outputs") or []),
            "writeActions": list(payload.get("write_actions") or []),
            "actionCapability": payload.get("action_capability"),
            "initiatorMembershipId": identity.membership_id,
        }
        payload_hash = sha256_text(canonical_json(plan))
        command_type = "organization.bot.task_plan.created"
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(connection, identity)
                self._bot(connection, identity, bot_id=bot_id)
                if client_id:
                    self.repository._require_project_access(  # noqa: SLF001
                        connection, identity, project_id=client_id, capability="project_write"
                    )
                existing = self.repository._existing_command(  # noqa: SLF001
                    connection, scope_id=identity.scope_id, idempotency_key=idempotency_key,
                    command_type=command_type, payload_hash=payload_hash,
                )
                if existing is not None:
                    row, receipt = self._plan_row(connection, identity, str(existing["aggregate_id"]))
                    record = self._plan_record(row, receipt)
                    connection.commit()
                    return {
                        "ai_task_plan_id": record["id"], "task_id": None,
                        "approval_id": record["approval_id"], "approval_status": record["status"],
                        "approval_source": "human_explicit", "status": record["status"],
                        "pending_reason": "awaiting_human_approval",
                    }
                now = utc_now()
                operation_id = _id("op", identity.scope_id, command_type, idempotency_key)
                proposal_id = _id("proposal", operation_id, bot_id)
                manifest_id = _id("manifest", operation_id, "plan")
                result_hash = _manifest(
                    connection, self.repository, identity, manifest_id=manifest_id,
                    receipt={"plan": plan},
                    media_type="application/vnd.yiyu.bot-task-plan+json", now=now,
                )
                connection.execute(
                    "INSERT INTO ai_proposals (id,scope_id,answer_id,operation_kind,payload_hash,"
                    "status,payload_object_manifest_id,risk_level,expires_at,version,lifecycle_state,"
                    "created_at,updated_at,deleted_at) VALUES (?,?,NULL,'task_prep',?,'draft',?,"
                    "'medium',NULL,1,'active',?,?,NULL)",
                    (proposal_id, identity.scope_id, payload_hash, manifest_id, now, now),
                )
                _record_command(
                    connection, self.repository, identity, command_type=command_type,
                    idempotency_key=idempotency_key, aggregate_type="ai_proposal",
                    aggregate_id=proposal_id, expected_version=None, aggregate_version=1,
                    payload_hash=payload_hash, result_hash=result_hash,
                    result_manifest_id=manifest_id, target_resource_id=bot_id, now=now,
                )
                connection.commit()
                return {
                    "ai_task_plan_id": proposal_id, "task_id": None, "approval_id": None,
                    "approval_status": "pending_approval", "approval_source": "human_explicit",
                    "status": "pending_approval", "pending_reason": "awaiting_human_approval",
                }
            except Exception:
                connection.rollback()
                raise

    def decide_bot_plan(
        self,
        identity: SessionIdentity,
        *,
        plan_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        decision = str(payload.get("decision") or "").strip().lower()
        target = {"approve": "approved", "reject": "rejected", "revise": "changes_requested"}.get(decision)
        if target is None:
            raise RepositoryError(422, "bot_task_plan_decision_invalid", "任务方案决定无效")
        command_type = "organization.bot.task_plan." + target
        payload_hash = sha256_text(canonical_json({"planId": plan_id, "decision": target, "feedback": str(payload.get("feedback") or "")}))
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(connection, identity)
                row, receipt = self._plan_row(connection, identity, plan_id)
                existing = self.repository._existing_command(  # noqa: SLF001
                    connection, scope_id=identity.scope_id, idempotency_key=idempotency_key,
                    command_type=command_type, payload_hash=payload_hash,
                )
                if existing is not None:
                    replay, replay_receipt = self._plan_row(connection, identity, plan_id)
                    connection.commit()
                    return self._plan_record(replay, replay_receipt)
                if str(row["status"] or "") != "draft":
                    raise RepositoryError(409, "bot_task_plan_already_decided", "任务方案已经处理")
                now = utc_now()
                version = int(row["version"] or 1) + 1
                approval_id = _id("approval", identity.scope_id, plan_id, idempotency_key)
                connection.execute(
                    "INSERT INTO ai_approvals (id,scope_id,proposal_id,approver_principal_id,"
                    "decision,decided_at,approver_membership_id,decision_note,approved_rule_id,"
                    "version,lifecycle_state,created_at,updated_at,deleted_at) VALUES "
                    "(?,?,?,?,?,?,?,?,NULL,1,'active',?,?,NULL)",
                    (approval_id, identity.scope_id, plan_id, identity.principal_id, target,
                     now, identity.membership_id, str(payload.get("feedback") or ""), now, now),
                )
                connection.execute(
                    "UPDATE ai_proposals SET status=?,version=?,updated_at=? WHERE id=? AND scope_id=?",
                    (target, version, now, plan_id, identity.scope_id),
                )
                if target == "approved":
                    progress_manifest_id = _id("manifest", identity.scope_id, plan_id, "executor-blocked")
                    _manifest(
                        connection, self.repository, identity, manifest_id=progress_manifest_id,
                        receipt={"status": "blocked", "reason": "organization_bot_executor_not_connected"},
                        media_type="application/vnd.yiyu.execution-progress+json", now=now,
                    )
                    connection.execute(
                        "INSERT INTO execution_runs (id,scope_id,bot_id,rule_id,task_id,operation_id,"
                        "status,initiator_membership_id,proposal_id,run_kind,progress_object_manifest_id,"
                        "result_object_manifest_id,started_at,finished_at,version,lifecycle_state,"
                        "created_at,updated_at,deleted_at) VALUES (?,?,?,NULL,NULL,NULL,'blocked',?,?,"
                        "'organization_bot_task_plan',?,NULL,?, ?,1,'active',?,?,NULL)",
                        (_id("run", identity.scope_id, plan_id), identity.scope_id,
                         str(receipt["plan"]["botId"]), identity.membership_id, plan_id,
                         progress_manifest_id, now, now, now, now),
                    )
                result_manifest_id = _id("manifest", identity.scope_id, command_type, idempotency_key)
                result_hash = _manifest(
                    connection, self.repository, identity, manifest_id=result_manifest_id,
                    receipt={"planId": plan_id, "status": target, "version": version},
                    media_type="application/vnd.yiyu.bot-task-plan-decision+json", now=now,
                )
                _record_command(
                    connection, self.repository, identity, command_type=command_type,
                    idempotency_key=idempotency_key, aggregate_type="ai_proposal",
                    aggregate_id=plan_id, expected_version=int(row["version"] or 1),
                    aggregate_version=version, payload_hash=payload_hash, result_hash=result_hash,
                    result_manifest_id=result_manifest_id, target_resource_id=str(receipt["plan"]["botId"]),
                    now=now,
                )
                updated, updated_receipt = self._plan_row(connection, identity, plan_id)
                result = self._plan_record(updated, updated_receipt)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def bot_plan_progress(self, identity: SessionIdentity, *, plan_id: str) -> dict[str, Any]:
        with self.repository._connection() as connection:  # noqa: SLF001
            self._assert_identity(connection, identity)
            proposal, _ = self._plan_row(connection, identity, plan_id)
            run = connection.execute(
                "SELECT * FROM execution_runs WHERE scope_id=? AND proposal_id=? "
                "AND lifecycle_state='active' ORDER BY updated_at DESC LIMIT 1",
                (identity.scope_id, plan_id),
            ).fetchone()
        status = str(proposal["status"] or "draft")
        execution_status = "not_started"
        errors: list[dict[str, Any]] = []
        if run is not None and str(run["status"] or "") == "blocked":
            execution_status = "failed"
            errors = [{"index": 0, "tool": "organization_bot_executor", "error": "专用执行器尚未接通"}]
        return {
            "plan_id": plan_id,
            "plan_status": {"draft": "pending_approval"}.get(status, status),
            "execution_status": execution_status,
            "started_at": str(run["started_at"]) if run is not None else None,
            "completed_at": str(run["finished_at"]) if run is not None else None,
            "progress": {"total": 0, "completed": 0, "current": "", "percent": 0, "errors": errors},
            "subtasks": [],
            "errors": errors,
            "expectedVersion": int(proposal["version"] or 1),
        }
