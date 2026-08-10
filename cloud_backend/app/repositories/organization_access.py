from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.security import (
    hash_password,
    hash_token,
    new_secret_token,
    normalize_email,
    normalize_phone,
    payload_fingerprint,
    redact_payload,
    verify_password,
)

from ..repository import CloudRepository, RepositoryError, SessionIdentity


Mutation = Callable[[sqlite3.Connection], tuple[dict[str, Any], dict[str, Any]]]

BOT_CAPABILITIES = (
    "workspace_file_write.request",
    "data_center_parse.request",
    "external_material_draft.create",
    "external_send.request",
    "clarification_resolution.propose",
    "inline_approval.allow_from_supervisor",
)


class OrganizationAccessRepository:
    """Strict authority operations for organization identity and access."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    def _assert_identity(
        self,
        connection: sqlite3.Connection,
        identity: SessionIdentity,
        *,
        admin: bool = False,
    ) -> sqlite3.Row:
        if identity.cloud_instance_id != self.repository.cloud_instance_id:
            raise RepositoryError(
                409,
                "cloud_identity_mismatch",
                "会话云实例与当前严格云不一致",
            )
        row = connection.execute(
            """
            SELECT m.membership_id, m.principal_id, m.organization_id,
                   m.system_role, m.visibility_scope, m.status, m.version,
                   m.scope_id
            FROM organization_memberships AS m
            JOIN organization_records AS o
              ON o.organization_id = m.organization_id
            WHERE m.membership_id = ? AND m.principal_id = ?
              AND m.organization_id = ? AND o.cloud_instance_id = ?
              AND o.lifecycle_state = 'active'
            """,
            (
                identity.membership_id,
                identity.principal_id,
                identity.organization_id,
                identity.cloud_instance_id,
            ),
        ).fetchone()
        if row is None or row["status"] != "active":
            raise RepositoryError(403, "membership_inactive", "当前组织成员身份不可用")
        if admin and row["system_role"] != "admin":
            raise RepositoryError(403, "admin_required", "仅管理员可以执行该操作")
        return row

    def _idempotent_mutation(
        self,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        mutation: Mutation,
    ) -> dict[str, Any]:
        safe_payload = dict(payload)
        payload_hash = payload_fingerprint(safe_payload)
        with self.repository._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._assert_identity(connection, identity)
                receipt = connection.execute(
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
                if receipt is not None:
                    if receipt["payload_hash"] != payload_hash:
                        raise RepositoryError(
                            409,
                            "idempotency_conflict",
                            "操作标识已用于不同请求",
                        )
                    connection.rollback()
                    return json.loads(str(receipt["result_json"]))

                result, metadata = mutation(connection)
                now = utc_now()
                operation_id = str(metadata.get("operation_id") or new_id())
                aggregate_type = str(metadata["aggregate_type"])
                aggregate_id = str(metadata["aggregate_id"])
                before_version = metadata.get("before_version")
                after_version = int(metadata["after_version"])
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
                        before_version,
                        canonical_json(safe_payload),
                        payload_hash,
                        now,
                        now,
                    ),
                )
                result_json = canonical_json(result)
                connection.execute(
                    """
                    INSERT INTO command_idempotency (
                        record_id, scope_id, actor_principal_id, command_type,
                        idempotency_key, payload_hash, result_hash,
                        result_json, expires_at, created_at
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
                    summary=dict(metadata.get("audit_summary") or safe_payload),
                )
                self.repository._insert_outbox(
                    connection,
                    scope_id=identity.scope_id,
                    organization_id=identity.organization_id,
                    operation_id=operation_id,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    aggregate_version=after_version,
                    event_type=f"{command_type}.committed",
                    payload={
                        "organizationId": identity.organization_id,
                        "resourceId": aggregate_id,
                        "version": after_version,
                    },
                )
                connection.commit()
                return result
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RepositoryError(
                    409,
                    "identity_value_conflict",
                    "联系方式或组织关系已被占用",
                ) from exc
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _member_record(
        connection: sqlite3.Connection,
        *,
        organization_id: str,
        membership_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT m.membership_id, m.principal_id, m.system_role,
                   m.visibility_scope, m.status, m.version, m.created_at,
                   p.display_name,
                   (SELECT normalized_value FROM identity_contacts
                    WHERE principal_id = p.principal_id
                      AND contact_type = 'email'
                      AND verification_state != 'revoked'
                    ORDER BY verification_state = 'verified' DESC, created_at
                    LIMIT 1) AS email,
                   (SELECT normalized_value FROM identity_contacts
                    WHERE principal_id = p.principal_id
                      AND contact_type = 'phone'
                      AND verification_state != 'revoked'
                    ORDER BY verification_state = 'verified' DESC, created_at
                    LIMIT 1) AS phone,
                   (SELECT dm.department_id
                    FROM department_memberships AS dm
                    WHERE dm.organization_id = m.organization_id
                      AND dm.membership_id = m.membership_id
                      AND dm.status = 'active'
                    ORDER BY dm.updated_at DESC LIMIT 1) AS department_id,
                   (SELECT d.name
                    FROM department_memberships AS dm
                    JOIN organization_departments AS d
                      ON d.department_id = dm.department_id
                    WHERE dm.organization_id = m.organization_id
                      AND dm.membership_id = m.membership_id
                      AND dm.status = 'active'
                    ORDER BY dm.updated_at DESC LIMIT 1) AS department_name,
                   COALESCE((SELECT dm.is_department_lead
                    FROM department_memberships AS dm
                    WHERE dm.organization_id = m.organization_id
                      AND dm.membership_id = m.membership_id
                      AND dm.status = 'active'
                    ORDER BY dm.updated_at DESC LIMIT 1), 0) AS is_department_lead,
                   (SELECT mtm.title_id
                    FROM management_title_memberships AS mtm
                    WHERE mtm.organization_id = m.organization_id
                      AND mtm.membership_id = m.membership_id
                      AND mtm.status = 'active'
                    ORDER BY mtm.updated_at DESC LIMIT 1) AS title_id,
                   (SELECT t.name
                    FROM management_title_memberships AS mtm
                    JOIN management_titles AS t ON t.title_id = mtm.title_id
                    WHERE mtm.organization_id = m.organization_id
                      AND mtm.membership_id = m.membership_id
                      AND mtm.status = 'active'
                    ORDER BY mtm.updated_at DESC LIMIT 1) AS title_name,
                   (SELECT MAX(s.last_seen_at)
                    FROM authentication_sessions AS s
                    WHERE s.organization_id = m.organization_id
                      AND s.membership_id = m.membership_id) AS last_login_at
            FROM organization_memberships AS m
            JOIN identity_principals AS p ON p.principal_id = m.principal_id
            WHERE m.organization_id = ? AND m.membership_id = ?
            """,
            (organization_id, membership_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "membership_missing", "组织成员不存在")
        status = str(row["status"])
        application = connection.execute(
            """
            SELECT a.membership_application_id, a.application_state,
                   a.rejection_reason, a.submitted_at, a.version,
                   a.requested_department_id,
                   d.name AS requested_department_name,
                   a.requested_management_title_id,
                   t.name AS requested_management_title_name,
                   a.requested_job_title, a.requested_manager_name,
                   a.requested_current_focus
            FROM organization_membership_applications AS a
            LEFT JOIN organization_departments AS d
              ON d.department_id = a.requested_department_id
             AND d.organization_id = a.organization_id
            LEFT JOIN management_titles AS t
              ON t.title_id = a.requested_management_title_id
             AND t.organization_id = a.organization_id
            WHERE a.organization_id = ? AND a.membership_id = ?
            ORDER BY a.application_state = 'pending' DESC,
                     a.submitted_at DESC, a.membership_application_id DESC
            LIMIT 1
            """,
            (organization_id, membership_id),
        ).fetchone()
        application_state = (
            str(application["application_state"])
            if application is not None
            else None
        )
        return {
            "id": row["membership_id"],
            "principalId": row["principal_id"],
            "email": row["email"] or "",
            "phone": row["phone"],
            "fullName": row["display_name"],
            "primaryRole": "admin" if row["system_role"] == "admin" else "employee",
            "accountStatus": "active" if status == "active" else "disabled",
            "membershipStatus": (
                "approved"
                if status == "active"
                else "disabled"
            ),
            "membershipApplicationState": application_state,
            "membershipApplicationId": (
                application["membership_application_id"]
                if application is not None
                else None
            ),
            "membershipApplicationVersion": (
                int(application["version"])
                if application is not None
                else None
            ),
            "membershipApplicationRequestedDepartmentId": (
                application["requested_department_id"]
                if application is not None
                else None
            ),
            "membershipApplicationRequestedDepartmentName": (
                application["requested_department_name"]
                if application is not None
                else None
            ),
            "membershipApplicationRequestedManagementTitleId": (
                application["requested_management_title_id"]
                if application is not None
                else None
            ),
            "membershipApplicationRequestedManagementTitleName": (
                application["requested_management_title_name"]
                if application is not None
                else None
            ),
            "membershipApplicationRequestedJobTitle": (
                application["requested_job_title"]
                if application is not None
                else None
            ),
            "membershipApplicationRequestedManagerName": (
                application["requested_manager_name"]
                if application is not None
                else None
            ),
            "membershipApplicationRequestedCurrentFocus": (
                application["requested_current_focus"]
                if application is not None
                else None
            ),
            "membershipSubmittedAt": (
                application["submitted_at"]
                if application is not None
                else None
            ),
            "membershipRejectedReason": (
                application["rejection_reason"]
                if application_state == "rejected"
                else None
            ),
            "departmentId": row["department_id"],
            "departmentName": row["department_name"],
            "isDepartmentLead": bool(row["is_department_lead"]),
            "visibilityScope": row["visibility_scope"],
            "managementTitleId": row["title_id"],
            "managementTitleName": row["title_name"],
            "createdAt": row["created_at"],
            "lastLoginAt": row["last_login_at"],
            "version": row["version"],
        }

    def members(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity)
            ids = [
                str(row["membership_id"])
                for row in connection.execute(
                    """
                    SELECT membership_id
                    FROM organization_memberships
                    WHERE organization_id = ? AND status != 'left'
                    ORDER BY created_at, membership_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            ]
            return [
                self._member_record(
                    connection,
                    organization_id=identity.organization_id,
                    membership_id=membership_id,
                )
                for membership_id in ids
            ]

    @staticmethod
    def _membership_application_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "applicationId": str(row["membership_application_id"]),
            "organizationId": str(row["organization_id"]),
            "membershipId": str(row["membership_id"]),
            "inviteId": row["invite_id"],
            "requestedDepartmentId": row["requested_department_id"],
            "requestedManagementTitleId": row[
                "requested_management_title_id"
            ],
            "requestedJobTitle": str(row["requested_job_title"] or ""),
            "requestedManagerName": str(row["requested_manager_name"] or ""),
            "requestedCurrentFocus": str(
                row["requested_current_focus"] or ""
            ),
            "applicationState": str(row["application_state"]),
            "rejectionReason": str(row["rejection_reason"] or ""),
            "reviewedByMembershipId": row["reviewed_by_membership_id"],
            "submittedAt": str(row["submitted_at"]),
            "reviewedAt": row["reviewed_at"],
            "version": int(row["version"]),
            "updatedAt": str(row["updated_at"]),
        }

    def membership_application(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str | None = None,
    ) -> dict[str, Any] | None:
        target_membership_id = membership_id or identity.membership_id
        with self.repository._connection() as connection:
            self._assert_identity(
                connection,
                identity,
                admin=target_membership_id != identity.membership_id,
            )
            row = connection.execute(
                """
                SELECT *
                FROM organization_membership_applications
                WHERE organization_id = ? AND membership_id = ?
                ORDER BY application_state = 'pending' DESC,
                         submitted_at DESC, membership_application_id DESC
                LIMIT 1
                """,
                (identity.organization_id, target_membership_id),
            ).fetchone()
        return (
            self._membership_application_record(row)
            if row is not None
            else None
        )

    def submit_membership_application(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        invite_code = str(payload.get("inviteCode") or "").strip()
        safe_payload = {
            "inviteCodeHash": hash_token(invite_code) if invite_code else "",
            "departmentId": str(payload.get("departmentId") or "").strip(),
            "managementTitleId": str(
                payload.get("managementTitleId") or ""
            ).strip(),
            "jobTitle": str(payload.get("jobTitle") or "").strip(),
            "managerName": str(payload.get("managerName") or "").strip(),
            "currentFocus": str(payload.get("currentFocus") or "").strip(),
        }

        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity)
            if connection.execute(
                """
                SELECT 1
                FROM organization_membership_applications
                WHERE organization_id = ? AND membership_id = ?
                  AND application_state = 'pending'
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchone():
                raise RepositoryError(
                    409,
                    "membership_application_pending",
                    "已有待审批的组织身份调整申请",
                )
            invite_id: str | None = None
            requested_department_id = safe_payload["departmentId"] or None
            requested_title_id: str | None = (
                safe_payload["managementTitleId"] or None
            )
            if invite_code:
                invite = connection.execute(
                    """
                    SELECT invite_id, organization_id, invite_kind, target_id,
                           expires_at
                    FROM organization_invites
                    WHERE code_hash = ? AND status = 'active'
                    """,
                    (hash_token(invite_code),),
                ).fetchone()
                if invite is None:
                    raise RepositoryError(404, "invite_invalid", "邀请码无效")
                if str(invite["organization_id"]) != identity.organization_id:
                    raise RepositoryError(
                        409,
                        "invite_organization_mismatch",
                        "邀请码不属于当前组织",
                    )
                expires_at = str(invite["expires_at"] or "")
                if expires_at and datetime.fromisoformat(
                    expires_at.replace("Z", "+00:00")
                ) <= datetime.now(timezone.utc):
                    raise RepositoryError(410, "invite_expired", "邀请码已过期")
                invite_id = str(invite["invite_id"])
                if invite["invite_kind"] == "department":
                    invite_department_id = str(invite["target_id"] or "")
                    if (
                        requested_department_id
                        and requested_department_id != invite_department_id
                    ):
                        raise RepositoryError(
                            409,
                            "invite_department_mismatch",
                            "邀请码与申请部门不一致",
                        )
                    requested_department_id = invite_department_id
                elif invite["invite_kind"] == "management_title":
                    invite_title_id = str(invite["target_id"] or "")
                    if (
                        requested_title_id
                        and requested_title_id != invite_title_id
                    ):
                        raise RepositoryError(
                            409,
                            "invite_management_title_mismatch",
                            "邀请码与申请岗位不一致",
                        )
                    requested_title_id = invite_title_id
            requested_job_title = safe_payload["jobTitle"]
            if requested_job_title:
                matching_titles = connection.execute(
                    """
                    SELECT title_id, department_id
                    FROM management_titles
                    WHERE organization_id = ? AND name = ?
                      AND lifecycle_state = 'active'
                    ORDER BY title_id
                    """,
                    (identity.organization_id, requested_job_title),
                ).fetchall()
                if len(matching_titles) != 1:
                    raise RepositoryError(
                        422,
                        "membership_application_job_title_unresolved",
                        "职位必须与组织搭建中心的一个现有岗位完全一致",
                    )
                matched_title_id = str(matching_titles[0]["title_id"])
                if (
                    requested_title_id
                    and requested_title_id != matched_title_id
                ):
                    raise RepositoryError(
                        409,
                        "membership_application_job_title_mismatch",
                        "填写的职位与所选岗位不一致",
                    )
                requested_title_id = matched_title_id
            if requested_department_id:
                department = connection.execute(
                    """
                    SELECT 1 FROM organization_departments
                    WHERE organization_id = ? AND department_id = ?
                      AND lifecycle_state = 'active'
                    """,
                    (identity.organization_id, requested_department_id),
                ).fetchone()
                if department is None:
                    raise RepositoryError(
                        404,
                        "membership_application_department_missing",
                        "申请部门不存在或已归档",
                    )
            if requested_title_id:
                title = connection.execute(
                    """
                    SELECT department_id FROM management_titles
                    WHERE organization_id = ? AND title_id = ?
                      AND lifecycle_state = 'active'
                    """,
                    (identity.organization_id, requested_title_id),
                ).fetchone()
                if title is None:
                    raise RepositoryError(
                        404,
                        "membership_application_title_missing",
                        "申请岗位不存在或已归档",
                    )
                title_department_id = str(title["department_id"] or "")
                if (
                    requested_department_id
                    and title_department_id
                    and title_department_id != requested_department_id
                ):
                    raise RepositoryError(
                        409,
                        "membership_application_title_department_mismatch",
                        "申请岗位不属于所选部门",
                    )
                requested_department_id = (
                    requested_department_id or title_department_id or None
                )
            requested_manager_name = safe_payload["managerName"]
            if requested_manager_name:
                matching_managers = connection.execute(
                    """
                    SELECT m.membership_id
                    FROM organization_memberships AS m
                    JOIN identity_principals AS p
                      ON p.principal_id = m.principal_id
                    WHERE m.organization_id = ? AND m.status = 'active'
                      AND p.principal_kind = 'human'
                      AND p.display_name = ?
                    ORDER BY m.membership_id
                    """,
                    (identity.organization_id, requested_manager_name),
                ).fetchall()
                if len(matching_managers) != 1:
                    raise RepositoryError(
                        422,
                        "membership_application_manager_unresolved",
                        "直属负责人必须与一个在职成员姓名完全一致且不能重名",
                    )
                if (
                    str(matching_managers[0]["membership_id"])
                    == identity.membership_id
                ):
                    raise RepositoryError(
                        422,
                        "membership_application_manager_self",
                        "不能申请由自己担任直属负责人",
                    )
            if not any(
                (
                    invite_id,
                    requested_department_id,
                    requested_title_id,
                    requested_job_title,
                    requested_manager_name,
                    safe_payload["currentFocus"],
                )
            ):
                raise RepositoryError(
                    422,
                    "membership_application_empty",
                    "请至少填写一项组织身份调整内容",
                )
            application_id = new_id()
            now = utc_now()
            connection.execute(
                """
                INSERT INTO organization_membership_applications (
                    membership_application_id, organization_id, membership_id,
                    invite_id, requested_department_id,
                    requested_management_title_id, requested_job_title,
                    requested_manager_name, requested_current_focus,
                    application_state, rejection_reason,
                    reviewed_by_membership_id, submitted_at, reviewed_at,
                    version, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', NULL,
                          ?, NULL, 1, ?)
                """,
                (
                    application_id,
                    identity.organization_id,
                    identity.membership_id,
                    invite_id,
                    requested_department_id,
                    requested_title_id,
                    safe_payload["jobTitle"],
                    safe_payload["managerName"],
                    safe_payload["currentFocus"],
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM organization_membership_applications
                WHERE membership_application_id = ?
                """,
                (application_id,),
            ).fetchone()
            return (
                self._membership_application_record(row),
                {
                    "aggregate_type": "organization_membership_application",
                    "aggregate_id": application_id,
                    "before_version": None,
                    "after_version": 1,
                    "audit_summary": {
                        "membershipId": identity.membership_id,
                        "requestedDepartmentId": requested_department_id,
                        "requestedManagementTitleId": requested_title_id,
                        "hasInvite": bool(invite_id),
                    },
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="organization.membership_application.submit",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            mutation=mutate,
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
        if decision not in {"approve", "reject"}:
            raise RepositoryError(
                422,
                "membership_application_decision_invalid",
                "审批决定必须是 approve 或 reject",
            )
        safe_reason = rejection_reason.strip()
        if decision == "reject" and not safe_reason:
            raise RepositoryError(
                422,
                "membership_application_rejection_reason_required",
                "拒绝申请时必须填写原因",
            )
        safe_payload = {
            "applicationId": application_id,
            "decision": decision,
            "rejectionReasonHash": (
                sha256_text(safe_reason) if safe_reason else None
            ),
            "expectedVersion": expected_version,
        }

        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity, admin=True)
            row = connection.execute(
                """
                SELECT *
                FROM organization_membership_applications
                WHERE organization_id = ? AND membership_application_id = ?
                """,
                (identity.organization_id, application_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(
                    404,
                    "membership_application_missing",
                    "组织身份申请不存在",
                )
            current_version = int(row["version"])
            if expected_version is not None and expected_version != current_version:
                raise RepositoryError(
                    409,
                    "membership_application_version_conflict",
                    "组织身份申请已更新，请刷新后重试",
                )
            if row["application_state"] != "pending":
                raise RepositoryError(
                    409,
                    "membership_application_already_decided",
                    "组织身份申请已经处理",
                )
            now = utc_now()
            membership_id = str(row["membership_id"])
            approved_manager_id: str | None = None
            if decision == "approve":
                department_id = str(row["requested_department_id"] or "")
                if department_id:
                    department = connection.execute(
                        """
                        SELECT 1 FROM organization_departments
                        WHERE organization_id = ? AND department_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, department_id),
                    ).fetchone()
                    if department is None:
                        raise RepositoryError(
                            409,
                            "membership_application_department_unavailable",
                            "申请部门已不存在或已归档，请拒绝后重新申请",
                        )
                    connection.execute(
                        """
                        UPDATE department_memberships
                        SET status = 'revoked', version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND membership_id = ?
                          AND status = 'active' AND department_id != ?
                        """,
                        (
                            now,
                            identity.organization_id,
                            membership_id,
                            department_id,
                        ),
                    )
                    existing_department = connection.execute(
                        """
                        SELECT department_membership_id, version
                        FROM department_memberships
                        WHERE organization_id = ? AND department_id = ?
                          AND membership_id = ?
                        """,
                        (
                            identity.organization_id,
                            department_id,
                            membership_id,
                        ),
                    ).fetchone()
                    if existing_department is None:
                        connection.execute(
                            """
                            INSERT INTO department_memberships (
                                department_membership_id, organization_id,
                                department_id, membership_id,
                                is_department_lead, status, version,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 0, 'active', 1, ?, ?)
                            """,
                            (
                                new_id(),
                                identity.organization_id,
                                department_id,
                                membership_id,
                                now,
                                now,
                            ),
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE department_memberships
                            SET status = 'active', is_department_lead = 0,
                                version = version + 1, updated_at = ?
                            WHERE department_membership_id = ?
                            """,
                            (
                                now,
                                existing_department["department_membership_id"],
                            ),
                        )
                title_id = str(
                    row["requested_management_title_id"] or ""
                )
                if title_id:
                    title = connection.execute(
                        """
                        SELECT department_id FROM management_titles
                        WHERE organization_id = ? AND title_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, title_id),
                    ).fetchone()
                    if title is None:
                        raise RepositoryError(
                            409,
                            "membership_application_title_unavailable",
                            "申请岗位已不存在或已归档，请拒绝后重新申请",
                        )
                    title_department_id = str(title["department_id"] or "")
                    if (
                        department_id
                        and title_department_id
                        and department_id != title_department_id
                    ):
                        raise RepositoryError(
                            409,
                            "membership_application_title_department_changed",
                            "申请岗位所属部门已变化，请拒绝后重新申请",
                        )
                    connection.execute(
                        """
                        UPDATE management_title_memberships
                        SET status = 'revoked', version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND status = 'active'
                          AND (
                            (membership_id = ? AND title_id != ?)
                            OR (title_id = ? AND membership_id != ?)
                          )
                        """,
                        (
                            now,
                            identity.organization_id,
                            membership_id,
                            title_id,
                            title_id,
                            membership_id,
                        ),
                    )
                    assignment = connection.execute(
                        """
                        SELECT assignment_id
                        FROM management_title_memberships
                        WHERE organization_id = ? AND title_id = ?
                          AND membership_id = ?
                        """,
                        (identity.organization_id, title_id, membership_id),
                    ).fetchone()
                    if assignment is None:
                        connection.execute(
                            """
                            INSERT INTO management_title_memberships (
                                assignment_id, organization_id, title_id,
                                membership_id, status, version,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)
                            """,
                            (
                                new_id(),
                                identity.organization_id,
                                title_id,
                                membership_id,
                                now,
                                now,
                            ),
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE management_title_memberships
                            SET status = 'active', version = version + 1,
                                updated_at = ?
                            WHERE assignment_id = ?
                            """,
                            (now, assignment["assignment_id"]),
                        )
                current_focus = str(row["requested_current_focus"] or "")
                if current_focus:
                    connection.execute(
                        """
                        UPDATE organization_memberships
                        SET current_focus = ?, version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND membership_id = ?
                        """,
                        (
                            current_focus,
                            now,
                            identity.organization_id,
                            membership_id,
                        ),
                    )
                manager_name = str(row["requested_manager_name"] or "")
                if manager_name:
                    matching_managers = connection.execute(
                        """
                        SELECT m.membership_id
                        FROM organization_memberships AS m
                        JOIN identity_principals AS p
                          ON p.principal_id = m.principal_id
                        WHERE m.organization_id = ? AND m.status = 'active'
                          AND p.principal_kind = 'human'
                          AND p.display_name = ?
                        ORDER BY m.membership_id
                        """,
                        (identity.organization_id, manager_name),
                    ).fetchall()
                    if len(matching_managers) != 1:
                        raise RepositoryError(
                            409,
                            "membership_application_manager_unavailable",
                            "申请的直属负责人已不可唯一识别，请拒绝后重新申请",
                        )
                    approved_manager_id = str(
                        matching_managers[0]["membership_id"]
                    )
                    if approved_manager_id == membership_id:
                        raise RepositoryError(
                            409,
                            "membership_application_manager_self",
                            "不能批准成员向自己汇报",
                        )
                    parent_rows = connection.execute(
                        """
                        SELECT report_membership_id, manager_membership_id
                        FROM organization_reporting_lines
                        WHERE organization_id = ? AND line_type = 'business'
                          AND lifecycle_state = 'active'
                          AND report_membership_id != ?
                        """,
                        (identity.organization_id, membership_id),
                    ).fetchall()
                    parents = {
                        str(item["report_membership_id"]): str(
                            item["manager_membership_id"]
                        )
                        for item in parent_rows
                    }
                    current_parent: str | None = approved_manager_id
                    seen: set[str] = set()
                    while current_parent is not None:
                        if current_parent == membership_id:
                            raise RepositoryError(
                                409,
                                "membership_application_reporting_cycle",
                                "批准该直属负责人会形成循环汇报关系",
                            )
                        if current_parent in seen:
                            raise RepositoryError(
                                409,
                                "organization_reporting_cycle",
                                "当前组织汇报线已存在循环，请先修复组织结构",
                            )
                        seen.add(current_parent)
                        current_parent = parents.get(current_parent)
                    connection.execute(
                        """
                        UPDATE organization_reporting_lines
                        SET lifecycle_state = 'archived',
                            version = version + 1, updated_at = ?
                        WHERE organization_id = ?
                          AND report_membership_id = ?
                          AND line_type = 'business'
                          AND lifecycle_state = 'active'
                          AND manager_membership_id != ?
                        """,
                        (
                            now,
                            identity.organization_id,
                            membership_id,
                            approved_manager_id,
                        ),
                    )
                    existing_line = connection.execute(
                        """
                        SELECT reporting_line_id
                        FROM organization_reporting_lines
                        WHERE organization_id = ?
                          AND manager_membership_id = ?
                          AND report_membership_id = ?
                          AND line_type = 'business'
                        """,
                        (
                            identity.organization_id,
                            approved_manager_id,
                            membership_id,
                        ),
                    ).fetchone()
                    if existing_line is None:
                        connection.execute(
                            """
                            INSERT INTO organization_reporting_lines (
                                reporting_line_id, organization_id,
                                manager_membership_id, report_membership_id,
                                line_type, approves_tasks, can_adjust_tasks,
                                can_change_deadline, can_reassign_tasks,
                                is_cross_department_approver,
                                lifecycle_state, version, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, 'business', 0, 0, 0, 0, 0,
                                      'active', 1, ?, ?)
                            """,
                            (
                                new_id(),
                                identity.organization_id,
                                approved_manager_id,
                                membership_id,
                                now,
                                now,
                            ),
                        )
                    else:
                        connection.execute(
                            """
                            UPDATE organization_reporting_lines
                            SET lifecycle_state = 'active',
                                version = version + 1, updated_at = ?
                            WHERE organization_id = ?
                              AND reporting_line_id = ?
                            """,
                            (
                                now,
                                identity.organization_id,
                                existing_line["reporting_line_id"],
                            ),
                        )
            next_state = "approved" if decision == "approve" else "rejected"
            changed = connection.execute(
                """
                UPDATE organization_membership_applications
                SET application_state = ?, rejection_reason = ?,
                    reviewed_by_membership_id = ?, reviewed_at = ?,
                    version = version + 1, updated_at = ?
                WHERE organization_id = ? AND membership_application_id = ?
                  AND application_state = 'pending' AND version = ?
                """,
                (
                    next_state,
                    safe_reason if decision == "reject" else "",
                    identity.membership_id,
                    now,
                    now,
                    identity.organization_id,
                    application_id,
                    current_version,
                ),
            )
            if changed.rowcount != 1:
                raise RepositoryError(
                    409,
                    "membership_application_version_conflict",
                    "组织身份申请已更新，请刷新后重试",
                )
            next_row = connection.execute(
                """
                SELECT * FROM organization_membership_applications
                WHERE membership_application_id = ?
                """,
                (application_id,),
            ).fetchone()
            return (
                self._membership_application_record(next_row),
                {
                    "aggregate_type": "organization_membership_application",
                    "aggregate_id": application_id,
                    "before_version": current_version,
                    "after_version": current_version + 1,
                    "audit_summary": {
                        "membershipId": membership_id,
                        "decision": decision,
                        "rejectionReasonHash": (
                            sha256_text(safe_reason) if safe_reason else None
                        ),
                        "approvedManagerMembershipId": approved_manager_id,
                    },
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type=f"organization.membership_application.{decision}",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            mutation=mutate,
        )

    def departments(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity)
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT department_id AS id, name, lifecycle_state AS state,
                           version, created_at AS createdAt, updated_at AS updatedAt
                    FROM organization_departments
                    WHERE organization_id = ? AND lifecycle_state = 'active'
                    ORDER BY name, department_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            ]

    def management_titles(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity)
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT title_id AS id, name, lifecycle_state AS state,
                           version, created_at AS createdAt, updated_at AS updatedAt
                    FROM management_titles
                    WHERE organization_id = ? AND lifecycle_state = 'active'
                    ORDER BY name, title_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            ]

    def resolve_invite(self, invite_code: str) -> dict[str, Any]:
        code = invite_code.strip()
        if not code:
            raise RepositoryError(422, "invite_code_required", "请输入邀请码")
        with self.repository._connection() as connection:
            invite = connection.execute(
                """
                SELECT i.organization_id, i.invite_kind, i.target_id,
                       i.expires_at, o.name AS organization_name,
                       d.name AS department_name, t.name AS title_name
                FROM organization_invites AS i
                JOIN organization_records AS o
                  ON o.organization_id = i.organization_id
                 AND o.cloud_instance_id = ?
                LEFT JOIN organization_departments AS d
                  ON i.invite_kind = 'department'
                 AND d.department_id = i.target_id
                 AND d.organization_id = i.organization_id
                LEFT JOIN management_titles AS t
                  ON i.invite_kind = 'management_title'
                 AND t.title_id = i.target_id
                 AND t.organization_id = i.organization_id
                WHERE i.code_hash = ? AND i.status = 'active'
                  AND o.lifecycle_state = 'active'
                """,
                (self.repository.cloud_instance_id, hash_token(code)),
            ).fetchone()
        if invite is None:
            return {"valid": False, "message": "邀请码无效"}
        if invite["expires_at"]:
            expires = datetime.fromisoformat(
                str(invite["expires_at"]).replace("Z", "+00:00")
            )
            if expires <= datetime.now(timezone.utc):
                return {"valid": False, "message": "邀请码已过期"}
        kind = str(invite["invite_kind"])
        return {
            "valid": True,
            "organizationId": invite["organization_id"],
            "organizationName": invite["organization_name"],
            "targetType": (
                "department"
                if kind == "department"
                else "management_role" if kind == "management_title" else None
            ),
            "departmentId": invite["target_id"] if kind == "department" else None,
            "departmentName": invite["department_name"],
            "managementTitleId": (
                invite["target_id"] if kind == "management_title" else None
            ),
            "managementTitleName": invite["title_name"],
            "roleKey": invite["target_id"] if kind == "management_title" else None,
            "roleName": invite["title_name"],
            "message": "",
        }

    def invite_departments(self, invite_code: str) -> list[dict[str, Any]]:
        resolved = self.resolve_invite(invite_code)
        if not resolved.get("valid"):
            raise RepositoryError(404, "invite_invalid", str(resolved.get("message")))
        organization_id = str(resolved["organizationId"])
        with self.repository._connection() as connection:
            rows = connection.execute(
                """
                SELECT department_id AS id, name
                FROM organization_departments
                WHERE organization_id = ? AND lifecycle_state = 'active'
                ORDER BY name
                """,
                (organization_id,),
            ).fetchall()
        return [{"id": row["id"], "name": row["name"], "color": "#5B7CFA"} for row in rows]

    def change_password(
        self,
        identity: SessionIdentity,
        *,
        current_password: str,
        new_password: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if len(new_password) < 8:
            raise RepositoryError(422, "password_too_short", "新密码至少需要 8 位")

        def mutate(connection: sqlite3.Connection):
            credential = connection.execute(
                """
                SELECT credential_id, secret_hash, hash_scheme, version
                FROM identity_credentials
                WHERE principal_id = ? AND credential_type = 'password'
                  AND status = 'active'
                """,
                (identity.principal_id,),
            ).fetchone()
            if credential is None or not verify_password(
                current_password,
                str(credential["secret_hash"]),
                scheme=str(credential["hash_scheme"]),
            ):
                raise RepositoryError(403, "current_password_invalid", "当前密码不正确")
            before = int(credential["version"])
            now = utc_now()
            connection.execute(
                """
                UPDATE identity_credentials
                SET secret_hash = ?, hash_scheme = 'scrypt-v1',
                    version = version + 1, updated_at = ?
                WHERE credential_id = ? AND version = ?
                """,
                (
                    hash_password(new_password),
                    now,
                    credential["credential_id"],
                    before,
                ),
            )
            connection.execute(
                """
                UPDATE authentication_sessions
                SET status = 'revoked', version = version + 1,
                    last_seen_at = ?
                WHERE principal_id = ? AND session_id != ? AND status = 'active'
                """,
                (now, identity.principal_id, identity.session_id),
            )
            return (
                {"message": "密码已更新，其他会话已退出"},
                {
                    "aggregate_type": "identity_credential",
                    "aggregate_id": credential["credential_id"],
                    "before_version": before,
                    "after_version": before + 1,
                    "audit_summary": {"principalId": identity.principal_id},
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="identity.password.change",
            idempotency_key=idempotency_key,
            payload={
                "principalId": identity.principal_id,
                "credentialMutation": "password_change",
            },
            mutation=mutate,
        )

    def update_profile(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        allowed = {key: payload[key] for key in ("fullName", "email", "phone") if key in payload}
        if not allowed:
            raise RepositoryError(422, "profile_update_empty", "没有可更新的个人资料")

        def mutate(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT identity_version FROM identity_principals
                WHERE principal_id = ? AND status = 'active'
                """,
                (identity.principal_id,),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "principal_missing", "账号不存在")
            before = int(row["identity_version"])
            now = utc_now()
            if "fullName" in allowed:
                name = str(allowed["fullName"] or "").strip()
                if not name:
                    raise RepositoryError(422, "display_name_required", "姓名不能为空")
                connection.execute(
                    """
                    UPDATE identity_principals
                    SET display_name = ?, identity_version = identity_version + 1,
                        updated_at = ?
                    WHERE principal_id = ? AND identity_version = ?
                    """,
                    (name, now, identity.principal_id, before),
                )
            else:
                connection.execute(
                    """
                    UPDATE identity_principals
                    SET identity_version = identity_version + 1, updated_at = ?
                    WHERE principal_id = ? AND identity_version = ?
                    """,
                    (now, identity.principal_id, before),
                )
            for contact_type in ("email", "phone"):
                if contact_type not in allowed:
                    continue
                raw_value = str(allowed[contact_type] or "").strip()
                existing = connection.execute(
                    """
                    SELECT contact_id, version FROM identity_contacts
                    WHERE principal_id = ? AND contact_type = ?
                    """,
                    (identity.principal_id, contact_type),
                ).fetchone()
                if not raw_value:
                    if contact_type == "phone" and existing:
                        connection.execute(
                            """
                            UPDATE identity_contacts
                            SET verification_state = 'revoked', version = version + 1,
                                updated_at = ?
                            WHERE contact_id = ?
                            """,
                            (now, existing["contact_id"]),
                        )
                    elif contact_type == "email":
                        raise RepositoryError(422, "email_required", "邮箱不能为空")
                    continue
                normalized = (
                    normalize_email(raw_value)
                    if contact_type == "email"
                    else normalize_phone(raw_value)
                )
                if existing:
                    connection.execute(
                        """
                        UPDATE identity_contacts
                        SET normalized_value = ?, verification_state = 'verified',
                            version = version + 1, updated_at = ?
                        WHERE contact_id = ?
                        """,
                        (normalized, now, existing["contact_id"]),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO identity_contacts (
                            contact_id, principal_id, contact_type,
                            normalized_value, verification_state, version,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 'verified', 1, ?, ?)
                        """,
                        (
                            new_id(),
                            identity.principal_id,
                            contact_type,
                            normalized,
                            now,
                            now,
                        ),
                    )
            return (
                self._member_record(
                    connection,
                    organization_id=identity.organization_id,
                    membership_id=identity.membership_id,
                ),
                {
                    "aggregate_type": "identity_principal",
                    "aggregate_id": identity.principal_id,
                    "before_version": before,
                    "after_version": before + 1,
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="identity.profile.update",
            idempotency_key=idempotency_key,
            payload=allowed,
            mutation=mutate,
        )

    def set_member_status(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str,
        enabled: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity, admin=True)
            target = connection.execute(
                """
                SELECT version, status, system_role
                FROM organization_memberships
                WHERE organization_id = ? AND membership_id = ?
                """,
                (identity.organization_id, membership_id),
            ).fetchone()
            if target is None:
                raise RepositoryError(404, "membership_missing", "组织成员不存在")
            if membership_id == identity.membership_id and not enabled:
                raise RepositoryError(409, "cannot_disable_self", "管理员不能停用自己的当前会话")
            if target["system_role"] == "admin" and not enabled:
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM organization_memberships
                    WHERE organization_id = ? AND system_role = 'admin'
                      AND status = 'active'
                    """,
                    (identity.organization_id,),
                ).fetchone()[0]
                if int(count) <= 1:
                    raise RepositoryError(409, "last_admin_required", "组织必须保留一名管理员")
            before = int(target["version"])
            connection.execute(
                """
                UPDATE organization_memberships
                SET status = ?, version = version + 1, updated_at = ?
                WHERE organization_id = ? AND membership_id = ? AND version = ?
                """,
                (
                    "active" if enabled else "disabled",
                    utc_now(),
                    identity.organization_id,
                    membership_id,
                    before,
                ),
            )
            if not enabled:
                connection.execute(
                    """
                    UPDATE authentication_sessions
                    SET status = 'revoked', version = version + 1
                    WHERE organization_id = ? AND membership_id = ?
                      AND status = 'active'
                    """,
                    (identity.organization_id, membership_id),
                )
            return (
                self._member_record(
                    connection,
                    organization_id=identity.organization_id,
                    membership_id=membership_id,
                ),
                {
                    "aggregate_type": "organization_membership",
                    "aggregate_id": membership_id,
                    "before_version": before,
                    "after_version": before + 1,
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type=(
                "organization.membership.enable"
                if enabled
                else "organization.membership.disable"
            ),
            idempotency_key=idempotency_key,
            payload={"membershipId": membership_id},
            mutation=mutate,
        )

    def set_member_role(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str,
        role: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_role = "admin" if role == "admin" else "member"

        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity, admin=True)
            target = connection.execute(
                """
                SELECT version, system_role, status
                FROM organization_memberships
                WHERE organization_id = ? AND membership_id = ?
                """,
                (identity.organization_id, membership_id),
            ).fetchone()
            if target is None:
                raise RepositoryError(404, "membership_missing", "组织成员不存在")
            if target["status"] != "active":
                raise RepositoryError(409, "membership_inactive", "停用成员不能调整角色")
            if target["system_role"] == "admin" and normalized_role != "admin":
                count = connection.execute(
                    """
                    SELECT COUNT(*) FROM organization_memberships
                    WHERE organization_id = ? AND system_role = 'admin'
                      AND status = 'active'
                    """,
                    (identity.organization_id,),
                ).fetchone()[0]
                if int(count) <= 1:
                    raise RepositoryError(409, "last_admin_required", "组织必须保留一名管理员")
            before = int(target["version"])
            connection.execute(
                """
                UPDATE organization_memberships
                SET system_role = ?, visibility_scope = CASE
                      WHEN ? = 'admin' THEN 'organization'
                      WHEN visibility_scope = 'organization' THEN 'self'
                      ELSE visibility_scope END,
                    version = version + 1, updated_at = ?
                WHERE organization_id = ? AND membership_id = ? AND version = ?
                """,
                (
                    normalized_role,
                    normalized_role,
                    utc_now(),
                    identity.organization_id,
                    membership_id,
                    before,
                ),
            )
            return (
                self._member_record(
                    connection,
                    organization_id=identity.organization_id,
                    membership_id=membership_id,
                ),
                {
                    "aggregate_type": "organization_membership",
                    "aggregate_id": membership_id,
                    "before_version": before,
                    "after_version": before + 1,
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="organization.membership.role.update",
            idempotency_key=idempotency_key,
            payload={"membershipId": membership_id, "role": normalized_role},
            mutation=mutate,
        )

    def set_member_department(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str,
        department_id: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity, admin=True)
            target = connection.execute(
                """
                SELECT version FROM organization_memberships
                WHERE organization_id = ? AND membership_id = ?
                  AND status = 'active'
                """,
                (identity.organization_id, membership_id),
            ).fetchone()
            if target is None:
                raise RepositoryError(404, "membership_missing", "组织成员不存在或已停用")
            if department_id:
                department = connection.execute(
                    """
                    SELECT 1 FROM organization_departments
                    WHERE organization_id = ? AND department_id = ?
                      AND lifecycle_state = 'active'
                    """,
                    (identity.organization_id, department_id),
                ).fetchone()
                if department is None:
                    raise RepositoryError(404, "department_missing", "部门不存在")
            now = utc_now()
            connection.execute(
                """
                UPDATE department_memberships
                SET status = 'revoked', version = version + 1, updated_at = ?
                WHERE organization_id = ? AND membership_id = ?
                  AND status = 'active'
                """,
                (now, identity.organization_id, membership_id),
            )
            if department_id:
                connection.execute(
                    """
                    INSERT INTO department_memberships (
                        department_membership_id, organization_id, department_id,
                        membership_id, is_department_lead, status, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, 'active', 1, ?, ?)
                    ON CONFLICT(department_id, membership_id) DO UPDATE SET
                        status = 'active', is_department_lead = 0,
                        version = department_memberships.version + 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        department_id,
                        membership_id,
                        now,
                        now,
                    ),
                )
            before = int(target["version"])
            connection.execute(
                """
                UPDATE organization_memberships
                SET version = version + 1, updated_at = ?
                WHERE organization_id = ? AND membership_id = ? AND version = ?
                """,
                (now, identity.organization_id, membership_id, before),
            )
            return (
                self._member_record(
                    connection,
                    organization_id=identity.organization_id,
                    membership_id=membership_id,
                ),
                {
                    "aggregate_type": "organization_membership",
                    "aggregate_id": membership_id,
                    "before_version": before,
                    "after_version": before + 1,
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="organization.membership.department.update",
            idempotency_key=idempotency_key,
            payload={
                "membershipId": membership_id,
                "departmentId": department_id,
            },
            mutation=mutate,
        )

    def transfer_admin(
        self,
        identity: SessionIdentity,
        *,
        target_membership_id: str,
        current_admin_action: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if target_membership_id == identity.membership_id:
            raise RepositoryError(422, "admin_target_invalid", "目标成员不能是当前管理员")

        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity, admin=True)
            target = connection.execute(
                """
                SELECT version, status FROM organization_memberships
                WHERE organization_id = ? AND membership_id = ?
                """,
                (identity.organization_id, target_membership_id),
            ).fetchone()
            current = connection.execute(
                """
                SELECT version FROM organization_memberships
                WHERE organization_id = ? AND membership_id = ?
                """,
                (identity.organization_id, identity.membership_id),
            ).fetchone()
            if target is None or target["status"] != "active":
                raise RepositoryError(404, "membership_missing", "目标成员不存在或已停用")
            target_before = int(target["version"])
            current_before = int(current["version"])
            now = utc_now()
            connection.execute(
                """
                UPDATE organization_memberships
                SET system_role = 'admin', visibility_scope = 'organization',
                    version = version + 1, updated_at = ?
                WHERE organization_id = ? AND membership_id = ? AND version = ?
                """,
                (
                    now,
                    identity.organization_id,
                    target_membership_id,
                    target_before,
                ),
            )
            if current_admin_action in {"demote_to_member", "disable_self"}:
                connection.execute(
                    """
                    UPDATE organization_memberships
                    SET system_role = 'member',
                        visibility_scope = CASE
                          WHEN visibility_scope = 'organization' THEN 'self'
                          ELSE visibility_scope END,
                        status = ?, version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND membership_id = ? AND version = ?
                    """,
                    (
                        "disabled" if current_admin_action == "disable_self" else "active",
                        now,
                        identity.organization_id,
                        identity.membership_id,
                        current_before,
                    ),
                )
                if current_admin_action == "disable_self":
                    connection.execute(
                        """
                        UPDATE authentication_sessions
                        SET status = 'revoked', version = version + 1,
                            last_seen_at = ?
                        WHERE organization_id = ? AND membership_id = ?
                          AND status = 'active'
                        """,
                        (now, identity.organization_id, identity.membership_id),
                    )
            return (
                {"message": "管理员已移交", "targetUserId": target_membership_id},
                {
                    "aggregate_type": "organization_membership",
                    "aggregate_id": target_membership_id,
                    "before_version": target_before,
                    "after_version": target_before + 1,
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="organization.admin.transfer",
            idempotency_key=idempotency_key,
            payload={
                "targetMembershipId": target_membership_id,
                "currentAdminAction": current_admin_action,
            },
            mutation=mutate,
        )

    def reset_password(
        self,
        identity: SessionIdentity,
        *,
        membership_id: str,
        new_password: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if len(new_password) < 8:
            raise RepositoryError(422, "password_too_short", "新密码至少需要 8 位")

        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity, admin=True)
            target = connection.execute(
                """
                SELECT m.principal_id, c.credential_id, c.version
                FROM organization_memberships AS m
                JOIN identity_credentials AS c
                  ON c.principal_id = m.principal_id
                 AND c.credential_type = 'password' AND c.status = 'active'
                WHERE m.organization_id = ? AND m.membership_id = ?
                """,
                (identity.organization_id, membership_id),
            ).fetchone()
            if target is None:
                raise RepositoryError(404, "membership_missing", "组织成员不存在")
            before = int(target["version"])
            now = utc_now()
            connection.execute(
                """
                UPDATE identity_credentials
                SET secret_hash = ?, hash_scheme = 'scrypt-v1',
                    version = version + 1, updated_at = ?
                WHERE credential_id = ? AND version = ?
                """,
                (
                    hash_password(new_password),
                    now,
                    target["credential_id"],
                    before,
                ),
            )
            connection.execute(
                """
                UPDATE authentication_sessions
                SET status = 'revoked', version = version + 1,
                    last_seen_at = ?
                WHERE organization_id = ? AND membership_id = ?
                  AND status = 'active'
                """,
                (now, identity.organization_id, membership_id),
            )
            return (
                {"message": "密码已重置，目标成员需重新登录"},
                {
                    "aggregate_type": "identity_credential",
                    "aggregate_id": target["credential_id"],
                    "before_version": before,
                    "after_version": before + 1,
                    "audit_summary": {"membershipId": membership_id},
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="identity.password.admin_reset",
            idempotency_key=idempotency_key,
            payload={
                "membershipId": membership_id,
                "credentialMutation": "admin_password_reset",
            },
            mutation=mutate,
        )

    def activity_logs(
        self,
        identity: SessionIdentity,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity, admin=True)
            rows = connection.execute(
                """
                SELECT a.audit_id, a.actor_id, a.action, a.resource_type,
                       a.resource_id, a.summary_json, a.created_at,
                       p.display_name AS actor_name
                FROM audit_events AS a
                LEFT JOIN identity_principals AS p
                  ON p.principal_id = a.actor_id
                WHERE a.scope_id = ? AND a.organization_id = ?
                ORDER BY a.created_at DESC, a.audit_id DESC
                LIMIT ?
                """,
                (identity.scope_id, identity.organization_id, max(1, min(limit, 500))),
            ).fetchall()
        return [
            {
                "id": row["audit_id"],
                "actorName": row["actor_name"] or "系统",
                "action": row["action"],
                "entityType": row["resource_type"],
                "entityId": row["resource_id"],
                "detail": json.loads(str(row["summary_json"])),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def _command_receipt(
        self,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any],
        admin: bool = False,
    ) -> dict[str, Any] | None:
        payload_hash = payload_fingerprint(dict(payload))
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity, admin=admin)
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
            return None
        if str(row["payload_hash"]) != payload_hash:
            raise RepositoryError(
                409,
                "idempotency_conflict",
                "操作标识已用于不同请求",
            )
        return json.loads(str(row["result_json"]))

    def list_recovery_sets(
        self,
        identity: SessionIdentity,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity, admin=True)
            rows = connection.execute(
                """
                SELECT r.recovery_set_id, r.status AS recovery_status,
                       r.created_at, r.verified_at,
                       b.backup_id, b.component_kind, b.backup_kind,
                       b.byte_size, b.retention_until, b.verified,
                       b.status AS backup_status
                FROM recovery_sets AS r
                JOIN backup_catalog AS b
                  ON b.recovery_set_id = r.recovery_set_id
                JOIN command_envelopes AS c
                  ON c.scope_id = ?
                 AND c.organization_id = ?
                 AND c.aggregate_type = 'recovery_set'
                 AND c.aggregate_id = r.recovery_set_id
                 AND c.command_type = 'recovery.database_backup_registered'
                 AND c.status = 'committed'
                WHERE b.component_kind = 'cloud_database'
                ORDER BY r.created_at DESC, r.recovery_set_id DESC
                LIMIT ?
                """,
                (
                    identity.scope_id,
                    identity.organization_id,
                    max(1, min(limit, 100)),
                ),
            ).fetchall()
        return [
            {
                "recoverySetId": row["recovery_set_id"],
                "backupId": row["backup_id"],
                "backupPath": (
                    f"strict-recovery://{row['recovery_set_id']}/{row['backup_id']}"
                ),
                "componentKind": row["component_kind"],
                "backupKind": row["backup_kind"],
                "byteSize": int(row["byte_size"]),
                "retentionUntil": row["retention_until"],
                "databaseVerified": bool(row["verified"]),
                "databaseStatus": row["backup_status"],
                "recoveryStatus": row["recovery_status"],
                "wholeSystemVerified": row["recovery_status"] == "verified",
                "coverage": "cloud_database_only",
                "createdAt": row["created_at"],
                "verifiedAt": row["verified_at"],
            }
            for row in rows
        ]

    def create_database_backup(
        self,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
        retention_days: int = 30,
    ) -> dict[str, Any]:
        retention_days = max(1, min(int(retention_days), 3650))
        safe_payload = {
            "retentionDays": retention_days,
            "coverage": "cloud_database_only",
        }
        receipt = self._command_receipt(
            identity,
            command_type="recovery.database_backup_registered",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            admin=True,
        )
        if receipt is not None:
            return receipt

        recovery_set_id = new_id()
        backup_id = new_id()
        backup_directory = (
            self.repository.database_path.parent
            / f"{self.repository.database_path.stem}.strict-recovery"
        )
        backup_path = backup_directory / f"{recovery_set_id}.sqlite3"
        temporary_path = backup_directory / f".{recovery_set_id}.{new_id()}.tmp"
        backup_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination: sqlite3.Connection | None = None
        try:
            with self.repository._connection() as source:
                self._assert_identity(source, identity, admin=True)
                destination = sqlite3.connect(temporary_path)
                source.backup(destination)
                destination.close()
                destination = None
            os.chmod(temporary_path, 0o600)
            with sqlite3.connect(
                f"file:{temporary_path}?mode=ro",
                uri=True,
            ) as verification:
                build = verification.execute(
                    """
                    SELECT build_id, database_generation_id, manifest_hash
                    FROM meta_schema_builds
                    WHERE status = 'active'
                    ORDER BY activated_at DESC
                    LIMIT 1
                    """
                ).fetchone()
            if build is None:
                raise RepositoryError(
                    500,
                    "backup_verification_failed",
                    "备份缺少严格 schema 身份",
                )
            if (
                str(build[0]) != self.repository.identity.build_id
                or str(build[1])
                != self.repository.identity.database_generation_id
                or str(build[2]) != self.repository.identity.manifest_hash
            ):
                raise RepositoryError(
                    500,
                    "backup_identity_mismatch",
                    "备份身份与当前严格云不一致",
                )
            with temporary_path.open("rb") as backup_stream:
                database_hash = hashlib.file_digest(
                    backup_stream,
                    "sha256",
                ).hexdigest()
            byte_size = temporary_path.stat().st_size
            os.replace(temporary_path, backup_path)
            os.chmod(backup_path, 0o600)
        except Exception:
            if destination is not None:
                destination.close()
            temporary_path.unlink(missing_ok=True)
            raise

        now = utc_now()
        retention_until = (
            datetime.now(timezone.utc) + timedelta(days=retention_days)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        object_manifest_hash = sha256_text(
            canonical_json(
                {
                    "coverage": "catalog_metadata_in_database_backup",
                    "objectPayloadsIncluded": False,
                }
            )
        )
        deployment_manifest_hash = sha256_text(
            canonical_json(
                {
                    "coverage": "strict_database_identity_only",
                    "cloudInstanceId": identity.cloud_instance_id,
                    "databaseGenerationId": (
                        self.repository.identity.database_generation_id
                    ),
                }
            )
        )
        component_manifest_hash = sha256_text(
            canonical_json(
                {
                    "database": database_hash,
                    "objects": object_manifest_hash,
                    "deployment": deployment_manifest_hash,
                    "wholeSystemVerified": False,
                }
            )
        )

        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity, admin=True)
            connection.execute(
                """
                INSERT INTO recovery_sets (
                    recovery_set_id, candidate_version, schema_build_id,
                    database_generation_id, schema_manifest_hash,
                    component_manifest_hash, database_hash,
                    object_manifest_hash, deployment_manifest_hash,
                    status, created_at, verified_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, NULL)
                """,
                (
                    recovery_set_id,
                    self.repository.identity.contract_version,
                    self.repository.identity.build_id,
                    self.repository.identity.database_generation_id,
                    self.repository.identity.manifest_hash,
                    component_manifest_hash,
                    database_hash,
                    object_manifest_hash,
                    deployment_manifest_hash,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO backup_catalog (
                    backup_id, recovery_set_id, component_kind, backup_kind,
                    storage_location, checksum, content_hash, byte_size,
                    retention_until, verified, status, created_at, verified_at
                ) VALUES (?, ?, 'cloud_database', 'sqlite_online_backup',
                          ?, ?, ?, ?, ?, 1, 'available', ?, ?)
                """,
                (
                    backup_id,
                    recovery_set_id,
                    str(backup_path),
                    f"sha256:{database_hash}",
                    database_hash,
                    byte_size,
                    retention_until,
                    now,
                    now,
                ),
            )
            result = {
                "recoverySetId": recovery_set_id,
                "backupId": backup_id,
                "backupPath": (
                    f"strict-recovery://{recovery_set_id}/{backup_id}"
                ),
                "createdAt": now,
                "verifiedAt": now,
                "databaseVerified": True,
                "wholeSystemVerified": False,
                "coverage": "cloud_database_only",
                "status": "created",
                "byteSize": byte_size,
                "retentionUntil": retention_until,
            }
            return (
                result,
                {
                    "aggregate_type": "recovery_set",
                    "aggregate_id": recovery_set_id,
                    "before_version": None,
                    "after_version": 1,
                    "audit_summary": {
                        "coverage": "cloud_database_only",
                        "databaseVerified": True,
                        "wholeSystemVerified": False,
                        "byteSize": byte_size,
                    },
                },
            )

        try:
            result = self._idempotent_mutation(
                identity,
                command_type="recovery.database_backup_registered",
                idempotency_key=idempotency_key,
                payload=safe_payload,
                mutation=mutate,
            )
        except Exception:
            backup_path.unlink(missing_ok=True)
            raise
        if result.get("recoverySetId") != recovery_set_id:
            backup_path.unlink(missing_ok=True)
        return result

    def organization_model(self, identity: SessionIdentity) -> dict[str, Any]:
        snapshot = self.repository.organization_snapshot(identity)
        organization = snapshot["organization"]
        members = self.members(identity)
        now = utc_now()
        member_by_id = {str(member["id"]): member for member in members}
        admin_ids = [
            member["id"] for member in members if member["primaryRole"] == "admin"
        ]

        def parse_json(value: Any, fallback: Any) -> Any:
            if value in {None, ""}:
                return fallback
            return json.loads(str(value))

        with self.repository._connection() as connection:
            self._assert_identity(connection, identity)
            organization_row = connection.execute(
                """
                SELECT *
                FROM organization_records
                WHERE organization_id = ? AND cloud_instance_id = ?
                """,
                (identity.organization_id, identity.cloud_instance_id),
            ).fetchone()
            if organization_row is None:
                raise RepositoryError(404, "organization_missing", "组织不存在")
            department_rows = connection.execute(
                """
                SELECT *
                FROM organization_departments
                WHERE organization_id = ?
                ORDER BY name, department_id
                """,
                (identity.organization_id,),
            ).fetchall()
            title_rows = connection.execute(
                """
                SELECT *
                FROM management_titles
                WHERE organization_id = ?
                ORDER BY sort_order, name, title_id
                """,
                (identity.organization_id,),
            ).fetchall()
            membership_rows = {
                str(row["membership_id"]): row
                for row in connection.execute(
                    """
                    SELECT *
                    FROM organization_memberships
                    WHERE organization_id = ? AND status != 'left'
                    ORDER BY created_at, membership_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            }
            bot_title_map = {
                str(row["title_id"]): str(row["bot_id"])
                for row in connection.execute(
                    """
                    SELECT mtm.title_id, b.bot_id
                    FROM management_title_memberships AS mtm
                    JOIN organization_bot_profiles AS b
                      ON b.organization_id = mtm.organization_id
                     AND b.membership_id = mtm.membership_id
                     AND b.lifecycle_state = 'active'
                    WHERE mtm.organization_id = ? AND mtm.status = 'active'
                    ORDER BY mtm.updated_at DESC, mtm.assignment_id
                    """,
                    (identity.organization_id,),
                ).fetchall()
            }
            reporting_rows = connection.execute(
                """
                SELECT *
                FROM organization_reporting_lines
                WHERE organization_id = ? AND lifecycle_state != 'archived'
                ORDER BY updated_at, reporting_line_id
                """,
                (identity.organization_id,),
            ).fetchall()
            rule_rows = connection.execute(
                """
                SELECT *
                FROM organization_task_control_rules
                WHERE organization_id = ? AND lifecycle_state != 'archived'
                ORDER BY updated_at, task_control_rule_id
                """,
                (identity.organization_id,),
            ).fetchall()
            process_rows = connection.execute(
                """
                SELECT *
                FROM organization_role_process_templates
                WHERE organization_id = ? AND lifecycle_state != 'archived'
                ORDER BY updated_at, role_process_template_id
                """,
                (identity.organization_id,),
            ).fetchall()
            plan_rows = connection.execute(
                """
                SELECT *
                FROM organization_plans
                WHERE organization_id = ? AND status != 'archived'
                ORDER BY updated_at, plan_id
                """,
                (identity.organization_id,),
            ).fetchall()
            plan_items = {
                str(plan["plan_id"]): connection.execute(
                    """
                    SELECT *
                    FROM organization_plan_items
                    WHERE organization_id = ? AND plan_id = ?
                      AND status != 'archived'
                    ORDER BY sort_order, plan_item_id
                    """,
                    (identity.organization_id, plan["plan_id"]),
                ).fetchall()
                for plan in plan_rows
            }

            def load_intro(
                *,
                document_kind: str,
                department_id: str | None,
            ) -> dict[str, Any] | None:
                row = connection.execute(
                    """
                    SELECT d.document_id, d.title, d.owner_membership_id,
                           d.version, d.updated_at, v.content_hash,
                           v.preview_text, v.markdown_content
                    FROM knowledge_documents AS d
                    JOIN document_versions AS v
                      ON v.document_id = d.document_id
                     AND v.version = d.current_version
                    WHERE d.organization_id = ?
                      AND d.project_assignment_state = 'unassigned'
                      AND d.document_kind = ?
                      AND d.department_id IS ?
                      AND d.visibility_scope = 'organization'
                      AND d.lifecycle_state = 'active'
                    ORDER BY d.updated_at DESC, d.document_id
                    LIMIT 1
                    """,
                    (
                        identity.organization_id,
                        document_kind,
                        department_id,
                    ),
                ).fetchone()
                if row is None:
                    return None
                markdown = str(row["markdown_content"])
                normalized = " ".join(markdown.split())
                return {
                    "fileName": str(row["title"]),
                    "fileType": (
                        Path(str(row["title"])).suffix.casefold().lstrip(".")
                        or "md"
                    ),
                    "markdownContent": markdown,
                    "normalizedText": normalized,
                    "summary": str(row["preview_text"] or normalized[:500])[:500],
                    "contentHash": str(row["content_hash"]),
                    "uploadedBy": str(row["owner_membership_id"] or ""),
                    "uploadedAt": str(row["updated_at"]),
                    "documentId": str(row["document_id"]),
                    "version": int(row["version"]),
                    "expectedVersion": int(row["version"]),
                }

            organization_intro = load_intro(
                document_kind="organization_intro_document",
                department_id=None,
            )
            department_intro_by_id = {
                str(row["department_id"]): load_intro(
                    document_kind="department_intro_document",
                    department_id=str(row["department_id"]),
                )
                for row in department_rows
            }

        organization_quarter_plans: list[dict[str, Any]] = []
        department_quarter_plans: dict[str, dict[str, Any]] = {}
        for plan in plan_rows:
            attributes = parse_json(plan["attributes_json"], {})
            kind = str(attributes.get("orgModelKind") or "")
            if kind == "organization_quarter_plan":
                quarter = dict(attributes.get("quarterPlan") or {})
                organization_quarter_plans.append(
                    {
                        "id": plan["plan_id"],
                        "year": str(quarter.get("year") or ""),
                        "quarter": str(quarter.get("quarter") or "Q1"),
                        "theme": str(quarter.get("theme") or ""),
                        "objective": str(
                            quarter.get("objective") or plan["summary"] or ""
                        ),
                        "keyResults": list(quarter.get("keyResults") or []),
                        "keyActions": list(quarter.get("keyActions") or []),
                        "majorRisks": list(quarter.get("majorRisks") or []),
                        "version": int(plan["version"]),
                        "updatedAt": plan["updated_at"],
                    }
                )
            elif kind == "department_quarter_plan" and plan["department_id"]:
                quarter = dict(attributes.get("quarterPlan") or {})
                department_quarter_plans[str(plan["department_id"])] = {
                    "planId": plan["plan_id"],
                    "planVersion": int(plan["version"]),
                    "year": str(quarter.get("year") or ""),
                    "quarter": str(quarter.get("quarter") or "Q1"),
                    "objective": str(
                        quarter.get("objective") or plan["summary"] or ""
                    ),
                    "deliverables": list(quarter.get("deliverables") or []),
                    "successMetrics": list(
                        quarter.get("successMetrics") or []
                    ),
                    "majorRisks": list(quarter.get("majorRisks") or []),
                    "updatedAt": plan["updated_at"],
                }

        department_records = [
            {
                "id": row["department_id"],
                "version": int(row["version"]),
                "name": row["name"],
                "color": row["color"],
                "leaderUserId": next(
                    (
                        member["id"]
                        for member in members
                        if member.get("departmentId") == row["department_id"]
                        and bool(member.get("isDepartmentLead"))
                    ),
                    None,
                ),
                "leaderName": next(
                    (
                        member["fullName"]
                        for member in members
                        if member.get("departmentId") == row["department_id"]
                        and bool(member.get("isDepartmentLead"))
                    ),
                    str(row["leader_name_override"] or ""),
                ),
                "introDocument": department_intro_by_id.get(
                    str(row["department_id"])
                ),
                "parentDepartmentId": row["parent_department_id"],
                "mission": row["mission"],
                "businessContext": row["business_context"],
                "teamContext": row["team_context"],
                "quarterPlan": {
                    "year": "",
                    "quarter": "Q1",
                    "objective": "",
                    "deliverables": [],
                    "successMetrics": [],
                    "majorRisks": [],
                    "updatedAt": row["updated_at"],
                    **department_quarter_plans.get(
                        str(row["department_id"]), {}
                    ),
                },
                "quarterlyFocus": parse_json(
                    row["quarterly_focus_json"], []
                ),
                "collaborationDepartmentIds": parse_json(
                    row["collaboration_department_ids_json"], []
                ),
                "active": row["lifecycle_state"] == "active",
                "updatedAt": row["updated_at"],
                "authorityCoverage": "organization_department",
            }
            for row in department_rows
        ]
        role_records = [
            {
                "id": row["title_id"],
                "version": int(row["version"]),
                "departmentId": row["department_id"],
                "name": row["name"],
                "level": row["level"],
                "visibilityScope": row["visibility_scope"],
                "managerRoleId": row["manager_title_id"],
                "isManager": bool(row["is_manager"]),
                "goal": row["goal"],
                "responsibilities": parse_json(
                    row["responsibilities_json"], []
                ),
                "shouldAvoid": parse_json(row["should_avoid_json"], []),
                "collaborationRoleIds": parse_json(
                    row["collaboration_title_ids_json"], []
                ),
                "taskEditScope": row["task_edit_scope"],
                "canApproveTasks": bool(row["can_approve_tasks"]),
                "canReassignTasks": bool(row["can_reassign_tasks"]),
                "canChangeDeadline": bool(row["can_change_deadline"]),
                "sortOrder": int(row["sort_order"]),
                "active": row["lifecycle_state"] == "active",
                "holderBotId": bot_title_map.get(str(row["title_id"])),
                "updatedAt": row["updated_at"],
                "authorityCoverage": "management_title",
            }
            for row in title_rows
        ]
        bindings = [
            {
                "userId": member["id"],
                "version": int(membership_rows[str(member["id"])]["version"]),
                "departmentId": member.get("departmentId"),
                "primaryRoleId": member.get("managementTitleId"),
                "managerUserId": next(
                    (
                        row["manager_membership_id"]
                        for row in reporting_rows
                        if row["report_membership_id"] == member["id"]
                        and row["line_type"] == "business"
                    ),
                    None,
                ),
                "isManager": bool(member.get("isDepartmentLead")),
                "visibilityScope": membership_rows[str(member["id"])][
                    "visibility_scope"
                ],
                "projectRoleLabels": parse_json(
                    membership_rows[str(member["id"])][
                        "project_role_labels_json"
                    ],
                    [],
                ),
                "currentFocus": membership_rows[str(member["id"])][
                    "current_focus"
                ],
                "taskEditScope": membership_rows[str(member["id"])][
                    "task_edit_scope"
                ],
                "canApproveTasks": bool(
                    membership_rows[str(member["id"])]["can_approve_tasks"]
                ),
                "canReassignTasks": bool(
                    membership_rows[str(member["id"])]["can_reassign_tasks"]
                ),
                "canChangeDeadline": bool(
                    membership_rows[str(member["id"])]["can_change_deadline"]
                ),
                "updatedAt": membership_rows[str(member["id"])]["updated_at"],
                "authorityCoverage": "organization_membership",
            }
            for member in members
        ]
        focus_items: list[dict[str, Any]] = []
        department_plans: list[dict[str, Any]] = []
        for plan in plan_rows:
            attributes = json.loads(str(plan["attributes_json"] or "{}"))
            kind = str(attributes.get("orgModelKind") or "")
            items = plan_items[str(plan["plan_id"])]
            if kind == "focus_item" and items:
                item = items[0]
                focus_items.append(
                    {
                        "id": item["plan_item_id"],
                        "planId": plan["plan_id"],
                        "planVersion": int(plan["version"]),
                        "version": int(item["version"]),
                        "periodKey": plan["period_label"],
                        "title": item["title"],
                        "statement": item["statement"],
                        "ownerUserId": item["owner_membership_id"],
                        "priority": attributes.get("priority") or "medium",
                        "status": attributes.get("uiStatus") or "active",
                        "evidenceKeywords": list(
                            attributes.get("evidenceKeywords") or []
                        ),
                        "updatedAt": item["updated_at"],
                        "authorityCoverage": "organization_plan",
                    }
                )
            elif kind == "department_plan":
                item_links = attributes.get("itemFocusLinks") or {}
                department_plans.append(
                    {
                        "id": plan["plan_id"],
                        "version": int(plan["version"]),
                        "departmentId": plan["department_id"],
                        "weekLabel": plan["period_label"],
                        "ownerUserId": plan["owner_membership_id"],
                        "summary": plan["summary"],
                        "majorRisks": list(attributes.get("majorRisks") or []),
                        "dependencies": list(attributes.get("dependencies") or []),
                        "status": attributes.get("uiStatus") or "active",
                        "items": [
                            {
                                "id": item["plan_item_id"],
                                "version": int(item["version"]),
                                "focusItemId": item_links.get(
                                    str(item["plan_item_id"])
                                ),
                                "title": item["title"],
                                "statement": item["statement"],
                                "ownerUserId": item["owner_membership_id"],
                                "status": {
                                    "completed": "done",
                                    "cancelled": "dropped",
                                }.get(str(item["status"]), "active"),
                                "expectedOutput": item["expected_output"],
                                "sortOrder": int(item["sort_order"]),
                                "updatedAt": item["updated_at"],
                            }
                            for item in items
                        ],
                        "updatedAt": plan["updated_at"],
                        "authorityCoverage": "organization_plan",
                    }
                )
        return {
            "organization": {
                "organizationId": organization["organizationId"],
                "version": int(organization["version"]),
                "name": organization["name"],
                "annualGoal": organization_row["annual_goal"],
                "annualStrategyYear": organization_row["annual_strategy_year"],
                "annualStrategy": organization_row["annual_strategy"],
                "quarterPlans": organization_quarter_plans,
                "quarterlyFocus": parse_json(
                    organization_row["quarterly_focus_json"], []
                ),
                "leaderUserId": (
                    organization_row["leader_membership_id"]
                    or (admin_ids[0] if admin_ids else None)
                ),
                "leaderName": next(
                    (
                        member["fullName"]
                        for member in members
                        if member["id"]
                        == (
                            organization_row["leader_membership_id"]
                            or (admin_ids[0] if admin_ids else None)
                        )
                    ),
                    str(organization_row["leader_name_override"] or ""),
                ),
                "introDocument": organization_intro,
                "managementUserIds": admin_ids,
                "updatedAt": organization_row["updated_at"],
                "authorityCoverage": "organization_authority",
            },
            "departments": department_records,
            "roles": role_records,
            "bindings": bindings,
            "reportingLines": [
                {
                    "id": row["reporting_line_id"],
                    "version": int(row["version"]),
                    "managerUserId": row["manager_membership_id"],
                    "reportUserId": row["report_membership_id"],
                    "lineType": row["line_type"],
                    "approvesTasks": bool(row["approves_tasks"]),
                    "canAdjustTasks": bool(row["can_adjust_tasks"]),
                    "canChangeDeadline": bool(row["can_change_deadline"]),
                    "canReassignTasks": bool(row["can_reassign_tasks"]),
                    "isCrossDepartmentApprover": bool(
                        row["is_cross_department_approver"]
                    ),
                    "active": row["lifecycle_state"] == "active",
                    "updatedAt": row["updated_at"],
                }
                for row in reporting_rows
            ],
            "taskControlRules": [
                {
                    "id": row["task_control_rule_id"],
                    "version": int(row["version"]),
                    "name": row["name"],
                    "controlLevel": row["control_level"],
                    "departmentId": row["department_id"],
                    "roleTemplateId": row["title_id"],
                    "contentEditableBy": row["content_editable_by"],
                    "deadlineEditableBy": row["deadline_editable_by"],
                    "ownerEditableBy": row["owner_editable_by"],
                    "cancellableBy": row["cancellable_by"],
                    "requireCollabConfirmation": bool(
                        row["require_collab_confirmation"]
                    ),
                    "defaultApproverUserId": row[
                        "default_approver_membership_id"
                    ],
                    "active": row["lifecycle_state"] == "active",
                    "updatedAt": row["updated_at"],
                }
                for row in rule_rows
            ],
            "roleProcessTemplates": [
                {
                    "id": row["role_process_template_id"],
                    "version": int(row["version"]),
                    "roleTemplateId": row["title_id"],
                    "name": row["name"],
                    "triggerType": row["trigger_type"],
                    "triggerCondition": row["trigger_condition"],
                    "keySteps": parse_json(row["key_steps_json"], []),
                    "collaborationStep": row["collaboration_step"],
                    "approvalStep": row["approval_step"],
                    "outputArtifact": row["output_artifact"],
                    "commonBlockers": parse_json(
                        row["common_blockers_json"], []
                    ),
                    "active": row["lifecycle_state"] == "active",
                    "updatedAt": row["updated_at"],
                }
                for row in process_rows
            ],
            "focusItems": focus_items,
            "departmentPlans": department_plans,
            "updatedAt": now,
            "authorityStates": {
                "identityStructure": {
                    "state": "ready",
                    "authority": [
                        "organization_records",
                        "organization_departments",
                        "department_memberships",
                        "management_titles",
                        "management_title_memberships",
                        "organization_memberships",
                    ],
                },
                "organizationPlans": {
                    "state": "ready",
                    "authority": [
                        "organization_plans",
                        "organization_plan_items",
                    ],
                },
                "unfrozenSemanticFields": {
                    "state": "ready",
                    "authority": [
                        "organization_records",
                        "organization_departments",
                        "management_titles",
                        "organization_memberships",
                        "organization_reporting_lines",
                        "organization_task_control_rules",
                        "organization_role_process_templates",
                        "knowledge_documents",
                        "document_versions",
                    ],
                },
                "roleProcessAutomation": {
                    "state": "blocked",
                    "reasonCode": "role_process_executor_not_connected",
                    "message": (
                        "角色流程模板已可配置并由组织云保存；"
                        "自动触发执行器尚未接通"
                    ),
                    "configurationAuthority": (
                        "organization_role_process_templates"
                    ),
                    "executionAttemptCreated": False,
                },
            },
            "authorityEvidence": {
                "authoritativeObjects": [
                    "organization_records",
                    "organization_departments",
                    "department_memberships",
                    "management_titles",
                    "management_title_memberships",
                    "organization_memberships",
                    "organization_plans",
                    "organization_plan_items",
                    "organization_reporting_lines",
                    "organization_task_control_rules",
                    "organization_role_process_templates",
                    "knowledge_documents",
                    "document_versions",
                ],
                "unsupportedFields": [],
            },
        }

    def update_organization_model(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        organization_payload = payload.get("organization")
        if not isinstance(organization_payload, Mapping):
            raise RepositoryError(
                422,
                "organization_model_identity_required",
                "组织模型缺少 organization",
            )

        collection_labels = {
            "departments": "部门",
            "roles": "管理职务",
            "bindings": "成员绑定",
            "reportingLines": "汇报线",
            "taskControlRules": "任务控制规则",
            "roleProcessTemplates": "角色流程模板",
            "focusItems": "组织重点",
            "departmentPlans": "部门计划",
        }
        for collection_key, label in collection_labels.items():
            raw_collection = payload.get(collection_key) or []
            if not isinstance(raw_collection, list) or any(
                not isinstance(item, Mapping) for item in raw_collection
            ):
                raise RepositoryError(
                    422,
                    f"organization_{collection_key}_invalid",
                    f"{label}记录格式无效",
                )

        def expected_version(
            item: Mapping[str, Any],
            *,
            code: str,
        ) -> int:
            value = item.get("version")
            if isinstance(value, bool):
                value = None
            try:
                version = int(value)
            except (TypeError, ValueError) as exc:
                raise RepositoryError(
                    409,
                    code,
                    "记录缺少严格版本，请刷新后重试",
                ) from exc
            if version < 1:
                raise RepositoryError(409, code, "记录版本无效，请刷新后重试")
            return version

        normalized_payload = {
            "organization": dict(organization_payload),
            "departments": [
                dict(item) for item in payload.get("departments") or []
            ],
            "roles": [dict(item) for item in payload.get("roles") or []],
            "bindings": [dict(item) for item in payload.get("bindings") or []],
            "reportingLines": [
                dict(item) for item in payload.get("reportingLines") or []
            ],
            "taskControlRules": [
                dict(item) for item in payload.get("taskControlRules") or []
            ],
            "roleProcessTemplates": [
                dict(item)
                for item in payload.get("roleProcessTemplates") or []
            ],
            "focusItems": [
                dict(item) for item in payload.get("focusItems") or []
            ],
            "departmentPlans": [
                dict(item) for item in payload.get("departmentPlans") or []
            ],
        }

        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity, admin=True)
            organization = connection.execute(
                """
                SELECT *
                FROM organization_records
                WHERE organization_id = ? AND cloud_instance_id = ?
                """,
                (identity.organization_id, identity.cloud_instance_id),
            ).fetchone()
            if organization is None:
                raise RepositoryError(404, "organization_missing", "组织不存在")
            organization_version = expected_version(
                organization_payload,
                code="organization_version_required",
            )
            if int(organization["version"]) != organization_version:
                raise RepositoryError(
                    409,
                    "organization_version_conflict",
                    "组织模型已更新，请刷新后重试",
                )
            organization_name = str(
                organization_payload.get("name") or ""
            ).strip()
            if not organization_name:
                raise RepositoryError(
                    422,
                    "organization_name_required",
                    "组织名称不能为空",
                )
            now = utc_now()

            def string_list(value: Any, *, code: str, label: str) -> list[str]:
                if value is None:
                    return []
                if not isinstance(value, list):
                    raise RepositoryError(422, code, f"{label}格式无效")
                return [
                    str(item).strip()
                    for item in value
                    if str(item).strip()
                ]

            def enum_value(
                value: Any,
                *,
                allowed: set[str],
                default: str,
                code: str,
                label: str,
            ) -> str:
                normalized = str(value or default)
                if normalized not in allowed:
                    raise RepositoryError(422, code, f"{label}无效")
                return normalized

            def active_membership(
                membership_id: Any,
                *,
                required: bool = False,
                code: str = "membership_missing",
                label: str = "组织成员",
            ) -> str | None:
                normalized = str(membership_id or "").strip()
                if not normalized:
                    if required:
                        raise RepositoryError(422, code, f"{label}不能为空")
                    return None
                row = connection.execute(
                    """
                    SELECT 1
                    FROM organization_memberships
                    WHERE organization_id = ? AND membership_id = ?
                      AND status = 'active'
                    """,
                    (identity.organization_id, normalized),
                ).fetchone()
                if row is None:
                    raise RepositoryError(
                        422, code, f"{label}不属于当前组织或不可用"
                    )
                return normalized

            leader_membership_id = active_membership(
                organization_payload.get("leaderUserId"),
                code="organization_leader_missing",
                label="组织负责人",
            )
            leader_name_override = (
                ""
                if leader_membership_id
                else str(organization_payload.get("leaderName") or "").strip()
            )
            quarterly_focus = string_list(
                organization_payload.get("quarterlyFocus"),
                code="organization_quarterly_focus_invalid",
                label="组织季度重点",
            )
            updated = connection.execute(
                """
                UPDATE organization_records
                SET name = ?, annual_goal = ?, annual_strategy_year = ?,
                    annual_strategy = ?, quarterly_focus_json = ?,
                    leader_membership_id = ?, leader_name_override = ?,
                    version = version + 1, updated_at = ?
                WHERE organization_id = ? AND cloud_instance_id = ?
                  AND version = ?
                """,
                (
                    organization_name,
                    str(organization_payload.get("annualGoal") or ""),
                    str(organization_payload.get("annualStrategyYear") or ""),
                    str(organization_payload.get("annualStrategy") or ""),
                    canonical_json(quarterly_focus),
                    leader_membership_id,
                    leader_name_override,
                    now,
                    identity.organization_id,
                    identity.cloud_instance_id,
                    organization_version,
                ),
            )
            if updated.rowcount != 1:
                raise RepositoryError(
                    409,
                    "organization_version_conflict",
                    "组织模型已更新，请刷新后重试",
                )

            def save_intro_document(
                raw_document: Any,
                *,
                field_present: bool,
                document_kind: str,
                department_id: str | None,
                default_name: str,
            ) -> None:
                if not field_present:
                    return
                existing = connection.execute(
                    """
                    SELECT *
                    FROM knowledge_documents
                    WHERE organization_id = ?
                      AND project_assignment_state = 'unassigned'
                      AND document_kind = ?
                      AND department_id IS ?
                      AND visibility_scope = 'organization'
                      AND lifecycle_state = 'active'
                    ORDER BY updated_at DESC, document_id
                    LIMIT 1
                    """,
                    (
                        identity.organization_id,
                        document_kind,
                        department_id,
                    ),
                ).fetchone()
                if raw_document is None:
                    if existing is not None:
                        connection.execute(
                            """
                            UPDATE knowledge_documents
                            SET lifecycle_state = 'archived',
                                version = version + 1, updated_at = ?
                            WHERE organization_id = ? AND document_id = ?
                            """,
                            (
                                now,
                                identity.organization_id,
                                existing["document_id"],
                            ),
                        )
                    return
                if not isinstance(raw_document, Mapping):
                    raise RepositoryError(
                        422,
                        "organization_intro_document_invalid",
                        "组织介绍文档格式无效",
                    )
                markdown = str(
                    raw_document.get("markdownContent")
                    or raw_document.get("normalizedText")
                    or ""
                )
                if not markdown.strip():
                    raise RepositoryError(
                        422,
                        "organization_intro_document_content_required",
                        "组织介绍文档正文不能为空",
                    )
                byte_size = len(markdown.encode("utf-8"))
                if byte_size > 2 * 1024 * 1024:
                    raise RepositoryError(
                        413,
                        "organization_intro_document_too_large",
                        "组织介绍文档正文超过 2 MiB 限制",
                    )
                content_hash = sha256_text(markdown)
                supplied_hash = str(
                    raw_document.get("contentHash") or ""
                ).strip()
                if supplied_hash and supplied_hash != content_hash:
                    raise RepositoryError(
                        422,
                        "organization_intro_document_hash_mismatch",
                        "组织介绍文档内容哈希不匹配",
                    )
                file_name = str(
                    raw_document.get("fileName") or default_name
                ).strip() or default_name
                normalized = " ".join(markdown.split())
                preview = str(
                    raw_document.get("summary") or normalized[:500]
                )[:1200]
                if existing is None:
                    document_id = new_id()
                    document_version = 1
                    connection.execute(
                        """
                        INSERT INTO knowledge_documents (
                            document_id, organization_id, project_id,
                            project_assignment_state, source_asset_id,
                            owner_membership_id, department_id, title,
                            document_kind, visibility_scope, parse_state,
                            lifecycle_state, current_version, version,
                            created_at, updated_at
                        ) VALUES (?, ?, NULL, 'unassigned', NULL, ?, ?, ?, ?,
                                  'organization', 'ready', 'active', 1, 1,
                                  ?, ?)
                        """,
                        (
                            document_id,
                            identity.organization_id,
                            identity.membership_id,
                            department_id,
                            file_name,
                            document_kind,
                            now,
                            now,
                        ),
                    )
                else:
                    document_id = str(existing["document_id"])
                    current_hash_row = connection.execute(
                        """
                        SELECT content_hash
                        FROM document_versions
                        WHERE organization_id = ? AND document_id = ?
                          AND version = ?
                        """,
                        (
                            identity.organization_id,
                            document_id,
                            existing["current_version"],
                        ),
                    ).fetchone()
                    if (
                        current_hash_row is not None
                        and str(current_hash_row["content_hash"]) == content_hash
                        and str(existing["title"]) == file_name
                    ):
                        return
                    document_version = int(existing["current_version"]) + 1
                    connection.execute(
                        """
                        UPDATE knowledge_documents
                        SET title = ?, owner_membership_id = ?,
                            current_version = ?, parse_state = 'ready',
                            version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND document_id = ?
                        """,
                        (
                            file_name,
                            identity.membership_id,
                            document_version,
                            now,
                            identity.organization_id,
                            document_id,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO document_versions (
                        document_version_id, organization_id, document_id,
                        version, content_hash, preview_text, markdown_content,
                        section_count, chunk_count, generator_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0,
                              'organization-model-v4', ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        document_id,
                        document_version,
                        content_hash,
                        preview,
                        markdown,
                        now,
                    ),
                )

            save_intro_document(
                organization_payload.get("introDocument"),
                field_present="introDocument" in organization_payload,
                document_kind="organization_intro_document",
                department_id=None,
                default_name="组织介绍.md",
            )

            for item in normalized_payload["departments"]:
                department_id = str(item.get("id") or new_id()).strip()
                item["id"] = department_id
                name = str(item.get("name") or "").strip()
                if not name:
                    raise RepositoryError(
                        422,
                        "department_name_required",
                        "部门名称不能为空",
                    )
                existing = connection.execute(
                    """
                    SELECT version
                    FROM organization_departments
                    WHERE organization_id = ? AND department_id = ?
                    """,
                    (identity.organization_id, department_id),
                ).fetchone()
                lifecycle = "active" if item.get("active", True) else "archived"
                color = str(item.get("color") or "#5B7CFA").strip()
                leader_name_override = (
                    ""
                    if str(item.get("leaderUserId") or "").strip()
                    else str(item.get("leaderName") or "").strip()
                )
                quarterly_focus = string_list(
                    item.get("quarterlyFocus"),
                    code="department_quarterly_focus_invalid",
                    label="部门季度重点",
                )
                collaboration_ids = string_list(
                    item.get("collaborationDepartmentIds"),
                    code="department_collaboration_invalid",
                    label="协作部门",
                )
                if existing is None:
                    collision = connection.execute(
                        """
                        SELECT 1 FROM organization_departments
                        WHERE department_id = ?
                        """,
                        (department_id,),
                    ).fetchone()
                    if collision is not None:
                        raise RepositoryError(
                            409,
                            "department_identity_conflict",
                            "部门标识已被其他组织使用",
                        )
                    connection.execute(
                        """
                        INSERT INTO organization_departments (
                            department_id, organization_id, name, color,
                            parent_department_id, leader_name_override,
                            mission, business_context, team_context,
                            quarterly_focus_json,
                            collaboration_department_ids_json,
                            lifecycle_state, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?,
                                  1, ?, ?)
                        """,
                        (
                            department_id,
                            identity.organization_id,
                            name,
                            color,
                            leader_name_override,
                            str(item.get("mission") or ""),
                            str(item.get("businessContext") or ""),
                            str(item.get("teamContext") or ""),
                            canonical_json(quarterly_focus),
                            canonical_json(collaboration_ids),
                            lifecycle,
                            now,
                            now,
                        ),
                    )
                else:
                    version = expected_version(
                        item,
                        code="department_version_required",
                    )
                    changed = connection.execute(
                        """
                        UPDATE organization_departments
                        SET name = ?, color = ?, leader_name_override = ?,
                            mission = ?, business_context = ?,
                            team_context = ?, quarterly_focus_json = ?,
                            collaboration_department_ids_json = ?,
                            lifecycle_state = ?,
                            version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND department_id = ?
                          AND version = ?
                        """,
                        (
                            name,
                            color,
                            leader_name_override,
                            str(item.get("mission") or ""),
                            str(item.get("businessContext") or ""),
                            str(item.get("teamContext") or ""),
                            canonical_json(quarterly_focus),
                            canonical_json(collaboration_ids),
                            lifecycle,
                            now,
                            identity.organization_id,
                            department_id,
                            version,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "department_version_conflict",
                            "部门已更新，请刷新后重试",
                        )

            for item in normalized_payload["departments"]:
                department_id = str(item["id"])
                parent_id = str(item.get("parentDepartmentId") or "").strip()
                if parent_id == department_id:
                    raise RepositoryError(
                        422,
                        "department_parent_cycle",
                        "部门不能以自身作为上级部门",
                    )
                referenced_ids = [
                    value
                    for value in (
                        [parent_id] if parent_id else []
                    )
                    + string_list(
                        item.get("collaborationDepartmentIds"),
                        code="department_collaboration_invalid",
                        label="协作部门",
                    )
                    if value
                ]
                for referenced_id in referenced_ids:
                    referenced = connection.execute(
                        """
                        SELECT 1
                        FROM organization_departments
                        WHERE organization_id = ? AND department_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, referenced_id),
                    ).fetchone()
                    if referenced is None:
                        raise RepositoryError(
                            422,
                            "department_reference_missing",
                            "部门上级或协作部门不属于当前组织",
                        )
                connection.execute(
                    """
                    UPDATE organization_departments
                    SET parent_department_id = ?
                    WHERE organization_id = ? AND department_id = ?
                    """,
                    (
                        parent_id or None,
                        identity.organization_id,
                        department_id,
                    ),
                )
                save_intro_document(
                    item.get("introDocument"),
                    field_present="introDocument" in item,
                    document_kind="department_intro_document",
                    department_id=department_id,
                    default_name=f"{str(item.get('name') or '部门')}介绍.md",
                )

            for item in normalized_payload["roles"]:
                title_id = str(item.get("id") or new_id()).strip()
                item["id"] = title_id
                name = str(item.get("name") or "").strip()
                if not name:
                    raise RepositoryError(
                        422,
                        "management_title_name_required",
                        "管理职务名称不能为空",
                    )
                existing = connection.execute(
                    """
                    SELECT version
                    FROM management_titles
                    WHERE organization_id = ? AND title_id = ?
                    """,
                    (identity.organization_id, title_id),
                ).fetchone()
                lifecycle = "active" if item.get("active", True) else "archived"
                department_id = str(item.get("departmentId") or "").strip()
                if department_id:
                    department = connection.execute(
                        """
                        SELECT 1
                        FROM organization_departments
                        WHERE organization_id = ? AND department_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, department_id),
                    ).fetchone()
                    if department is None:
                        raise RepositoryError(
                            422,
                            "management_title_department_missing",
                            "管理职务所属部门不属于当前组织",
                        )
                level = enum_value(
                    item.get("level"),
                    allowed={
                        "employee",
                        "supervisor",
                        "department_lead",
                        "organization_lead",
                    },
                    default="employee",
                    code="management_title_level_invalid",
                    label="管理职务层级",
                )
                visibility = enum_value(
                    item.get("visibilityScope"),
                    allowed={"organization", "department", "self"},
                    default="self",
                    code="management_title_visibility_invalid",
                    label="管理职务可见范围",
                )
                task_edit_scope = enum_value(
                    item.get("taskEditScope"),
                    allowed={"self", "manager", "department", "organization"},
                    default="self",
                    code="management_title_task_scope_invalid",
                    label="管理职务任务编辑范围",
                )
                responsibilities = string_list(
                    item.get("responsibilities"),
                    code="management_title_responsibilities_invalid",
                    label="岗位职责",
                )
                should_avoid = string_list(
                    item.get("shouldAvoid"),
                    code="management_title_avoid_invalid",
                    label="岗位避免事项",
                )
                collaboration_ids = string_list(
                    item.get("collaborationRoleIds"),
                    code="management_title_collaboration_invalid",
                    label="协作岗位",
                )
                try:
                    sort_order = int(item.get("sortOrder") or 0)
                except (TypeError, ValueError) as exc:
                    raise RepositoryError(
                        422,
                        "management_title_sort_invalid",
                        "管理职务排序无效",
                    ) from exc
                if existing is None:
                    collision = connection.execute(
                        "SELECT 1 FROM management_titles WHERE title_id = ?",
                        (title_id,),
                    ).fetchone()
                    if collision is not None:
                        raise RepositoryError(
                            409,
                            "management_title_identity_conflict",
                            "管理职务标识已被其他组织使用",
                        )
                    connection.execute(
                        """
                        INSERT INTO management_titles (
                            title_id, organization_id, name, department_id,
                            level, visibility_scope, manager_title_id,
                            is_manager, goal, responsibilities_json,
                            should_avoid_json, collaboration_title_ids_json,
                            task_edit_scope, can_approve_tasks,
                            can_reassign_tasks, can_change_deadline, sort_order,
                            lifecycle_state, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?,
                                  ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            title_id,
                            identity.organization_id,
                            name,
                            department_id or None,
                            level,
                            visibility,
                            1 if item.get("isManager") else 0,
                            str(item.get("goal") or ""),
                            canonical_json(responsibilities),
                            canonical_json(should_avoid),
                            canonical_json(collaboration_ids),
                            task_edit_scope,
                            1 if item.get("canApproveTasks") else 0,
                            1 if item.get("canReassignTasks") else 0,
                            1 if item.get("canChangeDeadline") else 0,
                            sort_order,
                            lifecycle,
                            now,
                            now,
                        ),
                    )
                else:
                    version = expected_version(
                        item,
                        code="management_title_version_required",
                    )
                    changed = connection.execute(
                        """
                        UPDATE management_titles
                        SET name = ?, department_id = ?, level = ?,
                            visibility_scope = ?, is_manager = ?, goal = ?,
                            responsibilities_json = ?, should_avoid_json = ?,
                            collaboration_title_ids_json = ?,
                            task_edit_scope = ?, can_approve_tasks = ?,
                            can_reassign_tasks = ?, can_change_deadline = ?,
                            sort_order = ?, lifecycle_state = ?,
                            version = version + 1, updated_at = ?
                        WHERE organization_id = ? AND title_id = ?
                          AND version = ?
                        """,
                        (
                            name,
                            department_id or None,
                            level,
                            visibility,
                            1 if item.get("isManager") else 0,
                            str(item.get("goal") or ""),
                            canonical_json(responsibilities),
                            canonical_json(should_avoid),
                            canonical_json(collaboration_ids),
                            task_edit_scope,
                            1 if item.get("canApproveTasks") else 0,
                            1 if item.get("canReassignTasks") else 0,
                            1 if item.get("canChangeDeadline") else 0,
                            sort_order,
                            lifecycle,
                            now,
                            identity.organization_id,
                            title_id,
                            version,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "management_title_version_conflict",
                            "管理职务已更新，请刷新后重试",
                        )

            for item in normalized_payload["roles"]:
                title_id = str(item["id"])
                manager_title_id = str(
                    item.get("managerRoleId") or ""
                ).strip()
                if manager_title_id == title_id:
                    raise RepositoryError(
                        422,
                        "management_title_manager_cycle",
                        "管理职务不能以自身作为上级职务",
                    )
                referenced_ids = [
                    value
                    for value in (
                        [manager_title_id] if manager_title_id else []
                    )
                    + string_list(
                        item.get("collaborationRoleIds"),
                        code="management_title_collaboration_invalid",
                        label="协作岗位",
                    )
                    if value
                ]
                for referenced_id in referenced_ids:
                    referenced = connection.execute(
                        """
                        SELECT 1
                        FROM management_titles
                        WHERE organization_id = ? AND title_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, referenced_id),
                    ).fetchone()
                    if referenced is None:
                        raise RepositoryError(
                            422,
                            "management_title_reference_missing",
                            "上级或协作管理职务不属于当前组织",
                        )
                connection.execute(
                    """
                    UPDATE management_titles
                    SET manager_title_id = ?
                    WHERE organization_id = ? AND title_id = ?
                    """,
                    (
                        manager_title_id or None,
                        identity.organization_id,
                        title_id,
                    ),
                )

            for item in normalized_payload["bindings"]:
                membership_id = str(item.get("userId") or "").strip()
                member = connection.execute(
                    """
                    SELECT version
                    FROM organization_memberships
                    WHERE organization_id = ? AND membership_id = ?
                      AND status != 'left'
                    """,
                    (identity.organization_id, membership_id),
                ).fetchone()
                if member is None:
                    raise RepositoryError(
                        404,
                        "membership_missing",
                        "成员绑定目标不存在",
                    )
                version = expected_version(
                    item,
                    code="membership_version_required",
                )
                visibility = str(item.get("visibilityScope") or "self")
                if visibility not in {"organization", "department", "self"}:
                    raise RepositoryError(
                        422,
                        "visibility_scope_invalid",
                        "成员可见范围无效",
                    )
                task_edit_scope = enum_value(
                    item.get("taskEditScope"),
                    allowed={"self", "manager", "department", "organization"},
                    default="self",
                    code="membership_task_scope_invalid",
                    label="成员任务编辑范围",
                )
                project_role_labels = string_list(
                    item.get("projectRoleLabels"),
                    code="membership_project_roles_invalid",
                    label="成员项目角色",
                )
                changed = connection.execute(
                    """
                    UPDATE organization_memberships
                    SET visibility_scope = ?, project_role_labels_json = ?,
                        current_focus = ?, task_edit_scope = ?,
                        can_approve_tasks = ?, can_reassign_tasks = ?,
                        can_change_deadline = ?, version = version + 1,
                        updated_at = ?
                    WHERE organization_id = ? AND membership_id = ?
                      AND version = ?
                    """,
                    (
                        visibility,
                        canonical_json(project_role_labels),
                        str(item.get("currentFocus") or ""),
                        task_edit_scope,
                        1 if item.get("canApproveTasks") else 0,
                        1 if item.get("canReassignTasks") else 0,
                        1 if item.get("canChangeDeadline") else 0,
                        now,
                        identity.organization_id,
                        membership_id,
                        version,
                    ),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "membership_version_conflict",
                        "成员绑定已更新，请刷新后重试",
                    )

                department_id = str(item.get("departmentId") or "").strip()
                if department_id:
                    department = connection.execute(
                        """
                        SELECT 1 FROM organization_departments
                        WHERE organization_id = ? AND department_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, department_id),
                    ).fetchone()
                    if department is None:
                        raise RepositoryError(
                            422,
                            "department_missing",
                            "成员部门不存在或已归档",
                        )
                connection.execute(
                    """
                    UPDATE department_memberships
                    SET status = 'revoked', version = version + 1,
                        updated_at = ?
                    WHERE organization_id = ? AND membership_id = ?
                      AND status = 'active'
                    """,
                    (now, identity.organization_id, membership_id),
                )
                if department_id:
                    connection.execute(
                        """
                        INSERT INTO department_memberships (
                            department_membership_id, organization_id,
                            department_id, membership_id, is_department_lead,
                            status, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'active', 1, ?, ?)
                        ON CONFLICT(department_id, membership_id)
                        DO UPDATE SET
                            is_department_lead = excluded.is_department_lead,
                            status = 'active', version = version + 1,
                            updated_at = excluded.updated_at
                        """,
                        (
                            new_id(),
                            identity.organization_id,
                            department_id,
                            membership_id,
                            1 if item.get("isManager") else 0,
                            now,
                            now,
                        ),
                    )

                title_id = str(item.get("primaryRoleId") or "").strip()
                if title_id:
                    title = connection.execute(
                        """
                        SELECT 1 FROM management_titles
                        WHERE organization_id = ? AND title_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, title_id),
                    ).fetchone()
                    if title is None:
                        raise RepositoryError(
                            422,
                            "management_title_missing",
                            "成员管理职务不存在或已归档",
                        )
                connection.execute(
                    """
                    UPDATE management_title_memberships
                    SET status = 'revoked', version = version + 1,
                        updated_at = ?
                    WHERE organization_id = ? AND membership_id = ?
                      AND status = 'active'
                    """,
                    (now, identity.organization_id, membership_id),
                )
                if title_id:
                    connection.execute(
                        """
                        INSERT INTO management_title_memberships (
                            assignment_id, organization_id, title_id,
                            membership_id, status, version, created_at,
                            updated_at
                        ) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)
                        ON CONFLICT(title_id, membership_id)
                        DO UPDATE SET status = 'active',
                            version = version + 1,
                            updated_at = excluded.updated_at
                        """,
                        (
                            new_id(),
                            identity.organization_id,
                            title_id,
                            membership_id,
                            now,
                            now,
                        ),
                    )

            def assert_parent_graph_acyclic(
                *,
                table: str,
                id_column: str,
                parent_column: str,
                code: str,
                label: str,
            ) -> None:
                rows = connection.execute(
                    f"""
                    SELECT {id_column}, {parent_column}
                    FROM {table}
                    WHERE organization_id = ? AND lifecycle_state = 'active'
                    """,
                    (identity.organization_id,),
                ).fetchall()
                parents = {
                    str(row[id_column]): (
                        str(row[parent_column])
                        if row[parent_column] is not None
                        else None
                    )
                    for row in rows
                }
                for start in parents:
                    seen: set[str] = set()
                    current: str | None = start
                    while current is not None:
                        if current in seen:
                            raise RepositoryError(422, code, f"{label}存在循环")
                        seen.add(current)
                        current = parents.get(current)

            assert_parent_graph_acyclic(
                table="organization_departments",
                id_column="department_id",
                parent_column="parent_department_id",
                code="department_parent_cycle",
                label="部门层级",
            )
            assert_parent_graph_acyclic(
                table="management_titles",
                id_column="title_id",
                parent_column="manager_title_id",
                code="management_title_manager_cycle",
                label="管理职务层级",
            )

            for item in normalized_payload["roles"]:
                title_id = str(item["id"])
                holder_bot_id = str(item.get("holderBotId") or "").strip()
                if holder_bot_id:
                    bot = connection.execute(
                        """
                        SELECT membership_id
                        FROM organization_bot_profiles
                        WHERE organization_id = ? AND bot_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, holder_bot_id),
                    ).fetchone()
                    if bot is None:
                        raise RepositoryError(
                            422,
                            "management_title_bot_missing",
                            "机器人持岗人不属于当前组织或不可用",
                        )
                    bot_membership_id = str(bot["membership_id"])
                    connection.execute(
                        """
                        UPDATE management_title_memberships
                        SET status = 'revoked', version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND title_id = ?
                          AND membership_id != ? AND status = 'active'
                        """,
                        (
                            now,
                            identity.organization_id,
                            title_id,
                            bot_membership_id,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO management_title_memberships (
                            assignment_id, organization_id, title_id,
                            membership_id, status, version, created_at,
                            updated_at
                        ) VALUES (?, ?, ?, ?, 'active', 1, ?, ?)
                        ON CONFLICT(title_id, membership_id)
                        DO UPDATE SET status = 'active',
                            version = version + 1,
                            updated_at = excluded.updated_at
                        """,
                        (
                            new_id(),
                            identity.organization_id,
                            title_id,
                            bot_membership_id,
                            now,
                            now,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE management_title_memberships
                        SET status = 'revoked', version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND title_id = ?
                          AND status = 'active'
                          AND membership_id IN (
                            SELECT membership_id
                            FROM organization_bot_profiles
                            WHERE organization_id = ?
                          )
                        """,
                        (
                            now,
                            identity.organization_id,
                            title_id,
                            identity.organization_id,
                        ),
                    )

            reporting_records = list(normalized_payload["reportingLines"])
            direct_reports = {
                (
                    str(item.get("userId") or "").strip(),
                    str(item.get("managerUserId") or "").strip(),
                )
                for item in normalized_payload["bindings"]
                if str(item.get("userId") or "").strip()
                and str(item.get("managerUserId") or "").strip()
            }
            explicit_business_reports = {
                str(item.get("reportUserId") or "").strip()
                for item in reporting_records
                if str(item.get("lineType") or "business") == "business"
            }
            for report_id, manager_id in sorted(direct_reports):
                if report_id not in explicit_business_reports:
                    reporting_records.append(
                        {
                            "managerUserId": manager_id,
                            "reportUserId": report_id,
                            "lineType": "business",
                            "approvesTasks": False,
                            "canAdjustTasks": False,
                            "canChangeDeadline": False,
                            "canReassignTasks": False,
                            "isCrossDepartmentApprover": False,
                            "active": True,
                        }
                    )

            retained_reporting_ids: list[str] = []
            reporting_keys: set[tuple[str, str]] = set()
            for item in reporting_records:
                manager_id = active_membership(
                    item.get("managerUserId"),
                    required=True,
                    code="reporting_manager_missing",
                    label="汇报线负责人",
                )
                report_id = active_membership(
                    item.get("reportUserId"),
                    required=True,
                    code="reporting_member_missing",
                    label="汇报成员",
                )
                if manager_id == report_id:
                    raise RepositoryError(
                        422,
                        "reporting_line_self_reference",
                        "成员不能向自己汇报",
                    )
                line_type = enum_value(
                    item.get("lineType"),
                    allowed={"business", "administrative"},
                    default="business",
                    code="reporting_line_type_invalid",
                    label="汇报线类型",
                )
                key = (str(report_id), line_type)
                if key in reporting_keys:
                    raise RepositoryError(
                        422,
                        "reporting_line_duplicate_manager",
                        "同一成员的同类汇报线只能有一个直属负责人",
                    )
                reporting_keys.add(key)
                line_id = str(item.get("id") or "").strip()
                if not line_id:
                    matching = connection.execute(
                        """
                        SELECT reporting_line_id
                        FROM organization_reporting_lines
                        WHERE organization_id = ?
                          AND report_membership_id = ? AND line_type = ?
                        ORDER BY lifecycle_state = 'active' DESC, updated_at DESC
                        LIMIT 1
                        """,
                        (identity.organization_id, report_id, line_type),
                    ).fetchone()
                    line_id = (
                        str(matching["reporting_line_id"])
                        if matching is not None
                        else new_id()
                    )
                item["id"] = line_id
                existing = connection.execute(
                    """
                    SELECT organization_id
                    FROM organization_reporting_lines
                    WHERE reporting_line_id = ?
                    """,
                    (line_id,),
                ).fetchone()
                values = (
                    manager_id,
                    report_id,
                    line_type,
                    1 if item.get("approvesTasks") else 0,
                    1 if item.get("canAdjustTasks") else 0,
                    1 if item.get("canChangeDeadline") else 0,
                    1 if item.get("canReassignTasks") else 0,
                    1 if item.get("isCrossDepartmentApprover") else 0,
                    "active" if item.get("active", True) else "archived",
                )
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO organization_reporting_lines (
                            reporting_line_id, organization_id,
                            manager_membership_id, report_membership_id,
                            line_type, approves_tasks, can_adjust_tasks,
                            can_change_deadline, can_reassign_tasks,
                            is_cross_department_approver, lifecycle_state,
                            version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            line_id,
                            identity.organization_id,
                            *values,
                            now,
                            now,
                        ),
                    )
                elif str(existing["organization_id"]) != identity.organization_id:
                    raise RepositoryError(
                        409,
                        "reporting_line_identity_conflict",
                        "汇报线标识已被其他组织使用",
                    )
                else:
                    connection.execute(
                        """
                        UPDATE organization_reporting_lines
                        SET manager_membership_id = ?,
                            report_membership_id = ?, line_type = ?,
                            approves_tasks = ?, can_adjust_tasks = ?,
                            can_change_deadline = ?, can_reassign_tasks = ?,
                            is_cross_department_approver = ?,
                            lifecycle_state = ?, version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND reporting_line_id = ?
                        """,
                        (
                            *values,
                            now,
                            identity.organization_id,
                            line_id,
                        ),
                    )
                retained_reporting_ids.append(line_id)
            if retained_reporting_ids:
                placeholders = ",".join("?" for _ in retained_reporting_ids)
                connection.execute(
                    f"""
                    UPDATE organization_reporting_lines
                    SET lifecycle_state = 'archived',
                        version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND lifecycle_state = 'active'
                      AND reporting_line_id NOT IN ({placeholders})
                    """,
                    (
                        now,
                        identity.organization_id,
                        *retained_reporting_ids,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE organization_reporting_lines
                    SET lifecycle_state = 'archived',
                        version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND lifecycle_state = 'active'
                    """,
                    (now, identity.organization_id),
                )

            business_lines = connection.execute(
                """
                SELECT report_membership_id, manager_membership_id
                FROM organization_reporting_lines
                WHERE organization_id = ? AND line_type = 'business'
                  AND lifecycle_state = 'active'
                """,
                (identity.organization_id,),
            ).fetchall()
            reporting_parents = {
                str(row["report_membership_id"]): str(
                    row["manager_membership_id"]
                )
                for row in business_lines
            }
            for start in reporting_parents:
                seen: set[str] = set()
                current: str | None = start
                while current is not None:
                    if current in seen:
                        raise RepositoryError(
                            422,
                            "reporting_line_cycle",
                            "业务汇报线存在循环",
                        )
                    seen.add(current)
                    current = reporting_parents.get(current)

            retained_rule_ids: list[str] = []
            for item in normalized_payload["taskControlRules"]:
                rule_id = str(item.get("id") or new_id()).strip()
                item["id"] = rule_id
                name = str(item.get("name") or "").strip()
                if not name:
                    raise RepositoryError(
                        422,
                        "task_control_rule_name_required",
                        "任务控制规则名称不能为空",
                    )
                control_level = enum_value(
                    item.get("controlLevel"),
                    allowed={
                        "normal",
                        "leader_control",
                        "department_control",
                        "organization_control",
                    },
                    default="normal",
                    code="task_control_level_invalid",
                    label="任务控制等级",
                )
                actor_values = {}
                for key, column_label in (
                    ("contentEditableBy", "内容编辑主体"),
                    ("deadlineEditableBy", "截止时间编辑主体"),
                    ("ownerEditableBy", "负责人编辑主体"),
                    ("cancellableBy", "取消任务主体"),
                ):
                    actor_values[key] = enum_value(
                        item.get(key),
                        allowed={
                            "assignee",
                            "manager",
                            "department_lead",
                            "organization_lead",
                            "creator",
                        },
                        default="assignee",
                        code="task_control_actor_scope_invalid",
                        label=column_label,
                    )
                department_id = str(item.get("departmentId") or "").strip()
                if department_id:
                    department = connection.execute(
                        """
                        SELECT 1 FROM organization_departments
                        WHERE organization_id = ? AND department_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, department_id),
                    ).fetchone()
                    if department is None:
                        raise RepositoryError(
                            422,
                            "task_control_department_missing",
                            "任务控制规则部门不属于当前组织",
                        )
                title_id = str(item.get("roleTemplateId") or "").strip()
                if title_id:
                    title = connection.execute(
                        """
                        SELECT 1 FROM management_titles
                        WHERE organization_id = ? AND title_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, title_id),
                    ).fetchone()
                    if title is None:
                        raise RepositoryError(
                            422,
                            "task_control_title_missing",
                            "任务控制规则职务不属于当前组织",
                        )
                approver_id = active_membership(
                    item.get("defaultApproverUserId"),
                    code="task_control_approver_missing",
                    label="默认审批人",
                )
                existing = connection.execute(
                    """
                    SELECT organization_id
                    FROM organization_task_control_rules
                    WHERE task_control_rule_id = ?
                    """,
                    (rule_id,),
                ).fetchone()
                values = (
                    name,
                    control_level,
                    department_id or None,
                    title_id or None,
                    actor_values["contentEditableBy"],
                    actor_values["deadlineEditableBy"],
                    actor_values["ownerEditableBy"],
                    actor_values["cancellableBy"],
                    1 if item.get("requireCollabConfirmation") else 0,
                    approver_id,
                    "active" if item.get("active", True) else "archived",
                )
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO organization_task_control_rules (
                            task_control_rule_id, organization_id, name,
                            control_level, department_id, title_id,
                            content_editable_by, deadline_editable_by,
                            owner_editable_by, cancellable_by,
                            require_collab_confirmation,
                            default_approver_membership_id, lifecycle_state,
                            version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  1, ?, ?)
                        """,
                        (
                            rule_id,
                            identity.organization_id,
                            *values,
                            now,
                            now,
                        ),
                    )
                elif str(existing["organization_id"]) != identity.organization_id:
                    raise RepositoryError(
                        409,
                        "task_control_rule_identity_conflict",
                        "任务控制规则标识已被其他组织使用",
                    )
                else:
                    connection.execute(
                        """
                        UPDATE organization_task_control_rules
                        SET name = ?, control_level = ?, department_id = ?,
                            title_id = ?, content_editable_by = ?,
                            deadline_editable_by = ?, owner_editable_by = ?,
                            cancellable_by = ?,
                            require_collab_confirmation = ?,
                            default_approver_membership_id = ?,
                            lifecycle_state = ?, version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ?
                          AND task_control_rule_id = ?
                        """,
                        (
                            *values,
                            now,
                            identity.organization_id,
                            rule_id,
                        ),
                    )
                retained_rule_ids.append(rule_id)
            if retained_rule_ids:
                placeholders = ",".join("?" for _ in retained_rule_ids)
                connection.execute(
                    f"""
                    UPDATE organization_task_control_rules
                    SET lifecycle_state = 'archived',
                        version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND lifecycle_state = 'active'
                      AND task_control_rule_id NOT IN ({placeholders})
                    """,
                    (now, identity.organization_id, *retained_rule_ids),
                )
            else:
                connection.execute(
                    """
                    UPDATE organization_task_control_rules
                    SET lifecycle_state = 'archived',
                        version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND lifecycle_state = 'active'
                    """,
                    (now, identity.organization_id),
                )

            retained_process_ids: list[str] = []
            for item in normalized_payload["roleProcessTemplates"]:
                process_id = str(item.get("id") or new_id()).strip()
                item["id"] = process_id
                name = str(item.get("name") or "").strip()
                if not name:
                    raise RepositoryError(
                        422,
                        "role_process_name_required",
                        "角色流程模板名称不能为空",
                    )
                title_id = str(item.get("roleTemplateId") or "").strip()
                if title_id:
                    title = connection.execute(
                        """
                        SELECT 1 FROM management_titles
                        WHERE organization_id = ? AND title_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, title_id),
                    ).fetchone()
                    if title is None:
                        raise RepositoryError(
                            422,
                            "role_process_title_missing",
                            "角色流程模板职务不属于当前组织",
                        )
                trigger_type = enum_value(
                    item.get("triggerType"),
                    allowed={
                        "weekly_followup",
                        "task_created",
                        "meeting_closed",
                        "client_update",
                        "manual",
                    },
                    default="manual",
                    code="role_process_trigger_invalid",
                    label="角色流程触发类型",
                )
                key_steps = string_list(
                    item.get("keySteps"),
                    code="role_process_steps_invalid",
                    label="角色流程关键步骤",
                )
                common_blockers = string_list(
                    item.get("commonBlockers"),
                    code="role_process_blockers_invalid",
                    label="角色流程常见阻塞",
                )
                existing = connection.execute(
                    """
                    SELECT organization_id
                    FROM organization_role_process_templates
                    WHERE role_process_template_id = ?
                    """,
                    (process_id,),
                ).fetchone()
                values = (
                    title_id or None,
                    name,
                    trigger_type,
                    str(item.get("triggerCondition") or ""),
                    canonical_json(key_steps),
                    str(item.get("collaborationStep") or ""),
                    str(item.get("approvalStep") or ""),
                    str(item.get("outputArtifact") or ""),
                    canonical_json(common_blockers),
                    "active" if item.get("active", True) else "archived",
                )
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO organization_role_process_templates (
                            role_process_template_id, organization_id,
                            title_id, name, trigger_type, trigger_condition,
                            key_steps_json, collaboration_step, approval_step,
                            output_artifact, common_blockers_json,
                            lifecycle_state, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            process_id,
                            identity.organization_id,
                            *values,
                            now,
                            now,
                        ),
                    )
                elif str(existing["organization_id"]) != identity.organization_id:
                    raise RepositoryError(
                        409,
                        "role_process_identity_conflict",
                        "角色流程模板标识已被其他组织使用",
                    )
                else:
                    connection.execute(
                        """
                        UPDATE organization_role_process_templates
                        SET title_id = ?, name = ?, trigger_type = ?,
                            trigger_condition = ?, key_steps_json = ?,
                            collaboration_step = ?, approval_step = ?,
                            output_artifact = ?, common_blockers_json = ?,
                            lifecycle_state = ?, version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ?
                          AND role_process_template_id = ?
                        """,
                        (
                            *values,
                            now,
                            identity.organization_id,
                            process_id,
                        ),
                    )
                retained_process_ids.append(process_id)
            if retained_process_ids:
                placeholders = ",".join("?" for _ in retained_process_ids)
                connection.execute(
                    f"""
                    UPDATE organization_role_process_templates
                    SET lifecycle_state = 'archived',
                        version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND lifecycle_state = 'active'
                      AND role_process_template_id NOT IN ({placeholders})
                    """,
                    (now, identity.organization_id, *retained_process_ids),
                )
            else:
                connection.execute(
                    """
                    UPDATE organization_role_process_templates
                    SET lifecycle_state = 'archived',
                        version = version + 1, updated_at = ?
                    WHERE organization_id = ? AND lifecycle_state = 'active'
                    """,
                    (now, identity.organization_id),
                )

            def assert_owner(owner_id: str | None) -> str | None:
                normalized = str(owner_id or "").strip()
                if not normalized:
                    return None
                owner = connection.execute(
                    """
                    SELECT 1 FROM organization_memberships
                    WHERE organization_id = ? AND membership_id = ?
                      AND status = 'active'
                    """,
                    (identity.organization_id, normalized),
                ).fetchone()
                if owner is None:
                    raise RepositoryError(
                        422,
                        "plan_owner_missing",
                        "计划负责人不是当前组织 active membership",
                    )
                return normalized

            def save_plan(
                record: Mapping[str, Any],
                *,
                plan_id: str,
                department_id: str | None,
                period_label: str,
                owner_id: str | None,
                summary: str,
                plan_status: str,
                attributes: Mapping[str, Any],
                items: list[dict[str, Any]],
                plan_version_key: str = "version",
            ) -> None:
                existing_plan = connection.execute(
                    """
                    SELECT version
                    FROM organization_plans
                    WHERE organization_id = ? AND plan_id = ?
                    """,
                    (identity.organization_id, plan_id),
                ).fetchone()
                if department_id:
                    department = connection.execute(
                        """
                        SELECT 1 FROM organization_departments
                        WHERE organization_id = ? AND department_id = ?
                          AND lifecycle_state = 'active'
                        """,
                        (identity.organization_id, department_id),
                    ).fetchone()
                    if department is None:
                        raise RepositoryError(
                            422,
                            "plan_department_missing",
                            "计划部门不存在或已归档",
                        )
                if existing_plan is None:
                    collision = connection.execute(
                        "SELECT 1 FROM organization_plans WHERE plan_id = ?",
                        (plan_id,),
                    ).fetchone()
                    if collision is not None:
                        raise RepositoryError(
                            409,
                            "organization_plan_identity_conflict",
                            "计划标识已被其他组织使用",
                        )
                    connection.execute(
                        """
                        INSERT INTO organization_plans (
                            plan_id, organization_id, department_id,
                            period_label, owner_membership_id, summary, status,
                            attributes_json, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            plan_id,
                            identity.organization_id,
                            department_id,
                            period_label,
                            assert_owner(owner_id),
                            summary,
                            plan_status,
                            canonical_json(dict(attributes)),
                            now,
                            now,
                        ),
                    )
                else:
                    version_value = record.get(plan_version_key)
                    try:
                        version = int(version_value)
                    except (TypeError, ValueError) as exc:
                        raise RepositoryError(
                            409,
                            "organization_plan_version_required",
                            "计划缺少严格版本，请刷新后重试",
                        ) from exc
                    changed = connection.execute(
                        """
                        UPDATE organization_plans
                        SET department_id = ?, period_label = ?,
                            owner_membership_id = ?, summary = ?, status = ?,
                            attributes_json = ?, version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND plan_id = ?
                          AND version = ?
                        """,
                        (
                            department_id,
                            period_label,
                            assert_owner(owner_id),
                            summary,
                            plan_status,
                            canonical_json(dict(attributes)),
                            now,
                            identity.organization_id,
                            plan_id,
                            version,
                        ),
                    )
                    if changed.rowcount != 1:
                        raise RepositoryError(
                            409,
                            "organization_plan_version_conflict",
                            "计划已更新，请刷新后重试",
                        )

                retained_item_ids: list[str] = []
                for order, item in enumerate(items):
                    item_id = str(item.get("id") or new_id()).strip()
                    title = str(item.get("title") or "").strip()
                    if not title:
                        raise RepositoryError(
                            422,
                            "organization_plan_item_title_required",
                            "计划项标题不能为空",
                        )
                    retained_item_ids.append(item_id)
                    existing_item = connection.execute(
                        """
                        SELECT version
                        FROM organization_plan_items
                        WHERE organization_id = ? AND plan_id = ?
                          AND plan_item_id = ?
                        """,
                        (identity.organization_id, plan_id, item_id),
                    ).fetchone()
                    item_status = str(item.get("databaseStatus") or "active")
                    if item_status not in {"active", "completed", "cancelled"}:
                        item_status = "active"
                    values = (
                        title,
                        str(item.get("statement") or ""),
                        assert_owner(item.get("ownerUserId")),
                        str(item.get("expectedOutput") or ""),
                        item_status,
                        int(item.get("sortOrder", order)),
                    )
                    if existing_item is None:
                        collision = connection.execute(
                            """
                            SELECT 1 FROM organization_plan_items
                            WHERE plan_item_id = ?
                            """,
                            (item_id,),
                        ).fetchone()
                        if collision is not None:
                            raise RepositoryError(
                                409,
                                "organization_plan_item_identity_conflict",
                                "计划项标识已被其他计划使用",
                            )
                        connection.execute(
                            """
                            INSERT INTO organization_plan_items (
                                plan_item_id, organization_id, plan_id, title,
                                statement, owner_membership_id,
                                expected_output, status, sort_order, version,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                            """,
                            (
                                item_id,
                                identity.organization_id,
                                plan_id,
                                *values,
                                now,
                                now,
                            ),
                        )
                    else:
                        version = expected_version(
                            item,
                            code="organization_plan_item_version_required",
                        )
                        changed = connection.execute(
                            """
                            UPDATE organization_plan_items
                            SET title = ?, statement = ?,
                                owner_membership_id = ?, expected_output = ?,
                                status = ?, sort_order = ?,
                                version = version + 1, updated_at = ?
                            WHERE organization_id = ? AND plan_id = ?
                              AND plan_item_id = ? AND version = ?
                            """,
                            (
                                *values,
                                now,
                                identity.organization_id,
                                plan_id,
                                item_id,
                                version,
                            ),
                        )
                        if changed.rowcount != 1:
                            raise RepositoryError(
                                409,
                                "organization_plan_item_version_conflict",
                                "计划项已更新，请刷新后重试",
                            )
                if retained_item_ids:
                    placeholders = ",".join("?" for _ in retained_item_ids)
                    connection.execute(
                        f"""
                        UPDATE organization_plan_items
                        SET status = 'archived', version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND plan_id = ?
                          AND status != 'archived'
                          AND plan_item_id NOT IN ({placeholders})
                        """,
                        (
                            now,
                            identity.organization_id,
                            plan_id,
                            *retained_item_ids,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE organization_plan_items
                        SET status = 'archived', version = version + 1,
                            updated_at = ?
                        WHERE organization_id = ? AND plan_id = ?
                          AND status != 'archived'
                        """,
                        (now, identity.organization_id, plan_id),
                    )

            def archive_plan_kind(
                kind: str,
                retained_ids: list[str],
            ) -> None:
                parameters: list[Any] = [now, identity.organization_id, kind]
                suffix = ""
                if retained_ids:
                    placeholders = ",".join("?" for _ in retained_ids)
                    suffix = f" AND plan_id NOT IN ({placeholders})"
                    parameters.extend(retained_ids)
                connection.execute(
                    f"""
                    UPDATE organization_plans
                    SET status = 'archived', version = version + 1,
                        updated_at = ?
                    WHERE organization_id = ? AND status != 'archived'
                      AND json_extract(attributes_json, '$.orgModelKind') = ?
                      {suffix}
                    """,
                    tuple(parameters),
                )

            retained_quarter_plan_ids: list[str] = []
            for quarter_plan in organization_payload.get("quarterPlans") or []:
                if not isinstance(quarter_plan, Mapping):
                    raise RepositoryError(
                        422,
                        "organization_quarter_plan_invalid",
                        "组织季度计划格式无效",
                    )
                quarter = enum_value(
                    quarter_plan.get("quarter"),
                    allowed={"Q1", "Q2", "Q3", "Q4"},
                    default="Q1",
                    code="organization_quarter_invalid",
                    label="组织计划季度",
                )
                plan_id = str(quarter_plan.get("id") or new_id()).strip()
                retained_quarter_plan_ids.append(plan_id)
                save_plan(
                    quarter_plan,
                    plan_id=plan_id,
                    department_id=None,
                    period_label=(
                        f"{str(quarter_plan.get('year') or '').strip()}-{quarter}"
                    ).strip("-"),
                    owner_id=None,
                    summary=str(quarter_plan.get("objective") or ""),
                    plan_status="active",
                    attributes={
                        "orgModelKind": "organization_quarter_plan",
                        "quarterPlan": {
                            "year": str(quarter_plan.get("year") or ""),
                            "quarter": quarter,
                            "theme": str(quarter_plan.get("theme") or ""),
                            "objective": str(
                                quarter_plan.get("objective") or ""
                            ),
                            "keyResults": string_list(
                                quarter_plan.get("keyResults"),
                                code="organization_quarter_results_invalid",
                                label="组织季度关键结果",
                            ),
                            "keyActions": string_list(
                                quarter_plan.get("keyActions"),
                                code="organization_quarter_actions_invalid",
                                label="组织季度关键行动",
                            ),
                            "majorRisks": string_list(
                                quarter_plan.get("majorRisks"),
                                code="organization_quarter_risks_invalid",
                                label="组织季度主要风险",
                            ),
                        },
                    },
                    items=[],
                )
            archive_plan_kind(
                "organization_quarter_plan", retained_quarter_plan_ids
            )

            retained_department_quarter_ids: list[str] = []
            for department in normalized_payload["departments"]:
                raw_quarter_plan = department.get("quarterPlan")
                if raw_quarter_plan is None:
                    continue
                if not isinstance(raw_quarter_plan, Mapping):
                    raise RepositoryError(
                        422,
                        "department_quarter_plan_invalid",
                        "部门季度计划格式无效",
                    )
                meaningful = any(
                    raw_quarter_plan.get(key)
                    for key in (
                        "year",
                        "objective",
                        "deliverables",
                        "successMetrics",
                        "majorRisks",
                    )
                )
                if not meaningful:
                    continue
                quarter = enum_value(
                    raw_quarter_plan.get("quarter"),
                    allowed={"Q1", "Q2", "Q3", "Q4"},
                    default="Q1",
                    code="department_quarter_invalid",
                    label="部门计划季度",
                )
                plan_id = str(
                    raw_quarter_plan.get("planId") or new_id()
                ).strip()
                retained_department_quarter_ids.append(plan_id)
                save_plan(
                    raw_quarter_plan,
                    plan_id=plan_id,
                    department_id=str(department["id"]),
                    period_label=(
                        f"{str(raw_quarter_plan.get('year') or '').strip()}"
                        f"-{quarter}"
                    ).strip("-"),
                    owner_id=department.get("leaderUserId"),
                    summary=str(raw_quarter_plan.get("objective") or ""),
                    plan_status="active",
                    attributes={
                        "orgModelKind": "department_quarter_plan",
                        "quarterPlan": {
                            "year": str(raw_quarter_plan.get("year") or ""),
                            "quarter": quarter,
                            "objective": str(
                                raw_quarter_plan.get("objective") or ""
                            ),
                            "deliverables": string_list(
                                raw_quarter_plan.get("deliverables"),
                                code="department_quarter_deliverables_invalid",
                                label="部门季度交付物",
                            ),
                            "successMetrics": string_list(
                                raw_quarter_plan.get("successMetrics"),
                                code="department_quarter_metrics_invalid",
                                label="部门季度成功指标",
                            ),
                            "majorRisks": string_list(
                                raw_quarter_plan.get("majorRisks"),
                                code="department_quarter_risks_invalid",
                                label="部门季度主要风险",
                            ),
                        },
                    },
                    items=[],
                    plan_version_key="planVersion",
                )
            archive_plan_kind(
                "department_quarter_plan", retained_department_quarter_ids
            )

            for focus in normalized_payload["focusItems"]:
                focus_id = str(focus.get("id") or new_id()).strip()
                plan_id = str(focus.get("planId") or new_id()).strip()
                ui_status = str(focus.get("status") or "active")
                if ui_status not in {"draft", "active", "paused", "done"}:
                    raise RepositoryError(
                        422,
                        "focus_status_invalid",
                        "组织重点状态无效",
                    )
                priority = str(focus.get("priority") or "medium")
                if priority not in {"high", "medium", "low"}:
                    raise RepositoryError(
                        422,
                        "focus_priority_invalid",
                        "组织重点优先级无效",
                    )
                plan_status = {
                    "draft": "draft",
                    "active": "active",
                    "paused": "draft",
                    "done": "completed",
                }.get(ui_status, "active")
                save_plan(
                    focus,
                    plan_id=plan_id,
                    department_id=None,
                    period_label=str(focus.get("periodKey") or "").strip()
                    or "unscheduled",
                    owner_id=focus.get("ownerUserId"),
                    summary=str(focus.get("statement") or ""),
                    plan_status=plan_status,
                    attributes={
                        "orgModelKind": "focus_item",
                        "priority": priority,
                        "uiStatus": ui_status,
                        "evidenceKeywords": [
                            str(value)
                            for value in focus.get("evidenceKeywords") or []
                        ],
                    },
                    items=[
                        {
                            "id": focus_id,
                            "version": focus.get("version"),
                            "title": focus.get("title"),
                            "statement": focus.get("statement"),
                            "ownerUserId": focus.get("ownerUserId"),
                            "expectedOutput": "",
                            "databaseStatus": {
                                "done": "completed",
                            }.get(ui_status, "active"),
                            "sortOrder": 0,
                        }
                    ],
                    plan_version_key="planVersion",
                )

            for plan in normalized_payload["departmentPlans"]:
                plan_id = str(plan.get("id") or new_id()).strip()
                ui_status = str(plan.get("status") or "active")
                if ui_status not in {"draft", "active", "closed"}:
                    raise RepositoryError(
                        422,
                        "department_plan_status_invalid",
                        "部门计划状态无效",
                    )
                plan_status = {
                    "draft": "draft",
                    "active": "active",
                    "closed": "completed",
                }.get(ui_status, "active")
                raw_items = plan.get("items") or []
                if not isinstance(raw_items, list):
                    raise RepositoryError(
                        422,
                        "organization_plan_items_invalid",
                        "部门计划项格式无效",
                    )
                item_focus_links = {
                    str(item.get("id")): item.get("focusItemId")
                    for item in raw_items
                    if isinstance(item, Mapping)
                    and item.get("id")
                    and item.get("focusItemId")
                }
                normalized_items: list[dict[str, Any]] = []
                for order, item in enumerate(raw_items):
                    if not isinstance(item, Mapping):
                        raise RepositoryError(
                            422,
                            "organization_plan_item_invalid",
                            "部门计划项格式无效",
                        )
                    item_status = str(item.get("status") or "active")
                    if item_status not in {
                        "active",
                        "paused",
                        "done",
                        "dropped",
                    }:
                        raise RepositoryError(
                            422,
                            "department_plan_item_status_invalid",
                            "部门计划项状态无效",
                        )
                    try:
                        sort_order = int(item.get("sortOrder", order))
                    except (TypeError, ValueError) as exc:
                        raise RepositoryError(
                            422,
                            "department_plan_item_sort_invalid",
                            "部门计划项排序无效",
                        ) from exc
                    normalized_items.append(
                        {
                            **dict(item),
                            "databaseStatus": {
                                "done": "completed",
                                "dropped": "cancelled",
                            }.get(item_status, "active"),
                            "sortOrder": sort_order,
                        }
                    )
                save_plan(
                    plan,
                    plan_id=plan_id,
                    department_id=str(plan.get("departmentId") or "").strip()
                    or None,
                    period_label=str(plan.get("weekLabel") or "").strip()
                    or "unscheduled",
                    owner_id=plan.get("ownerUserId"),
                    summary=str(plan.get("summary") or ""),
                    plan_status=plan_status,
                    attributes={
                        "orgModelKind": "department_plan",
                        "uiStatus": ui_status,
                        "majorRisks": [
                            str(value) for value in plan.get("majorRisks") or []
                        ],
                        "dependencies": [
                            str(value) for value in plan.get("dependencies") or []
                        ],
                        "itemFocusLinks": item_focus_links,
                    },
                    items=normalized_items,
                )

            return (
                {
                    "organizationId": identity.organization_id,
                    "version": organization_version + 1,
                    "saved": True,
                },
                {
                    "aggregate_type": "organization_model",
                    "aggregate_id": identity.organization_id,
                    "before_version": organization_version,
                    "after_version": organization_version + 1,
                    "audit_summary": {
                        "departments": len(normalized_payload["departments"]),
                        "managementTitles": len(normalized_payload["roles"]),
                        "memberBindings": len(normalized_payload["bindings"]),
                        "reportingLines": len(retained_reporting_ids),
                        "taskControlRules": len(retained_rule_ids),
                        "roleProcessTemplates": len(retained_process_ids),
                        "organizationQuarterPlans": len(
                            retained_quarter_plan_ids
                        ),
                        "departmentQuarterPlans": len(
                            retained_department_quarter_ids
                        ),
                        "focusItems": len(normalized_payload["focusItems"]),
                        "departmentPlans": len(
                            normalized_payload["departmentPlans"]
                        ),
                    },
                },
            )

        self._idempotent_mutation(
            identity,
            command_type="organization.model.updated",
            idempotency_key=idempotency_key,
            payload=normalized_payload,
            mutation=mutate,
        )
        return self.organization_model(identity)

    def reconcile_task_authority_links(
        self,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Verify the direct strict task authorities that replaced legacy links."""

        def mutate(connection: sqlite3.Connection):
            self._assert_identity(connection, identity, admin=True)
            rows = connection.execute(
                """
                SELECT t.task_id,
                       CASE
                         WHEN t.project_id IS NOT NULL AND NOT EXISTS (
                           SELECT 1 FROM clients p
                           WHERE p.scope_id = ?
                             AND p.id = t.project_id
                             AND p.lifecycle_state != 'deleted'
                         ) THEN 'project_missing'
                         WHEN NOT EXISTS (
                           SELECT 1 FROM organization_memberships creator
                           WHERE creator.organization_id = t.organization_id
                             AND creator.membership_id = t.created_by_membership_id
                             AND creator.status != 'left'
                         ) THEN 'creator_missing'
                         ELSE ''
                       END AS mismatch_code
                FROM task_records t
                WHERE t.organization_id = ? AND t.lifecycle_state != 'archived'
                ORDER BY t.task_id
                """,
                (identity.scope_id, identity.organization_id),
            ).fetchall()
            mismatches = [
                {
                    "taskId": str(row["task_id"]),
                    "code": str(row["mismatch_code"]),
                }
                for row in rows
                if str(row["mismatch_code"])
            ]
            now = utc_now()
            run_id = new_id()
            operation_id = new_id()
            total = len(rows)
            report = {
                "organizationId": identity.organization_id,
                "totalTasks": total,
                "linkedTasks": total - len(mismatches),
                "createdLinks": 0,
                "updatedLinks": 0,
                "updatedAt": now,
                "state": "completed" if not mismatches else "blocked",
                "authorityModel": "task_records_direct_foreign_keys",
                "mismatchCount": len(mismatches),
                "mismatches": mismatches[:100],
                "legacyLinkTableRequired": False,
            }
            connection.execute(
                """
                INSERT INTO reconciliation_runs (
                    run_id, scope_id, organization_id, operation_id,
                    registry_state_id, mismatch_count, status, report_json,
                    started_at, finished_at
                ) VALUES (?, ?, ?, ?, 'task_direct_authority', ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    identity.scope_id,
                    identity.organization_id,
                    operation_id,
                    len(mismatches),
                    "completed" if not mismatches else "blocked",
                    canonical_json(report),
                    now,
                    now,
                ),
            )
            return (
                report,
                {
                    "operation_id": operation_id,
                    "aggregate_type": "reconciliation_run",
                    "aggregate_id": run_id,
                    "before_version": None,
                    "after_version": 1,
                    "audit_summary": {
                        "totalTasks": total,
                        "linkedTasks": total - len(mismatches),
                        "mismatchCount": len(mismatches),
                        "authorityModel": "task_records_direct_foreign_keys",
                    },
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="organization.task_authority.reconciled",
            idempotency_key=idempotency_key,
            payload={"authorityModel": "task_records_direct_foreign_keys"},
            mutation=mutate,
        )

    @staticmethod
    def _bot_capability_policy(
        enabled_capabilities: Any,
    ) -> list[dict[str, Any]]:
        if not isinstance(enabled_capabilities, list):
            raise RepositoryError(
                422,
                "bot_capabilities_invalid",
                "机器人能力必须是数组",
            )
        enabled = {str(value) for value in enabled_capabilities}
        unknown = enabled.difference(BOT_CAPABILITIES)
        if unknown:
            raise RepositoryError(
                422,
                "bot_capability_unknown",
                "机器人能力不在冻结能力集合内",
            )
        return [
            {
                "capability_key": key,
                "enabled": key in enabled,
                "approval_required": key
                not in {
                    "clarification_resolution.propose",
                    "inline_approval.allow_from_supervisor",
                },
                "approval_policy": (
                    "organization_admin"
                    if key
                    not in {
                        "clarification_resolution.propose",
                        "inline_approval.allow_from_supervisor",
                    }
                    else "active_membership"
                ),
            }
            for key in BOT_CAPABILITIES
        ]

    @staticmethod
    def _bot_reporting(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "report_to_creator": bool(payload.get("report_to_creator", True)),
            "report_to_department_lead": bool(
                payload.get("report_to_department_lead", False)
            ),
            "report_to_ceo": bool(payload.get("report_to_ceo", False)),
            "department_leader_user_ids": [],
            "ceo_user_ids": [],
            "approval_mode": "capability_policy",
        }

    @staticmethod
    def _resolved_bot_reporting(
        connection: sqlite3.Connection,
        *,
        organization_id: str,
        department_id: str | None,
        creator_membership_id: str,
        requested: Mapping[str, Any],
    ) -> dict[str, Any]:
        reporting = OrganizationAccessRepository._bot_reporting(requested)
        if reporting["report_to_creator"]:
            reporting["creator_user_ids"] = [creator_membership_id]
        else:
            reporting["creator_user_ids"] = []
        if reporting["report_to_department_lead"] and department_id:
            reporting["department_leader_user_ids"] = [
                str(row["membership_id"])
                for row in connection.execute(
                    """
                    SELECT dm.membership_id
                    FROM department_memberships AS dm
                    JOIN organization_memberships AS m
                      ON m.membership_id = dm.membership_id
                    WHERE dm.organization_id = ? AND dm.department_id = ?
                      AND dm.is_department_lead = 1 AND dm.status = 'active'
                      AND m.status = 'active'
                    ORDER BY dm.membership_id
                    """,
                    (organization_id, department_id),
                ).fetchall()
            ]
        if reporting["report_to_ceo"]:
            reporting["ceo_user_ids"] = [
                str(row["membership_id"])
                for row in connection.execute(
                    """
                    SELECT membership_id
                    FROM organization_memberships
                    WHERE organization_id = ? AND system_role = 'admin'
                      AND status = 'active'
                    ORDER BY membership_id
                    """,
                    (organization_id,),
                ).fetchall()
            ]
        return reporting

    @staticmethod
    def _validate_bot_handle(value: str) -> str:
        handle = value.strip().casefold().lstrip("@")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", handle):
            raise RepositoryError(
                422,
                "bot_handle_invalid",
                "机器人 handle 需为 3-64 位小写字母、数字、下划线或短横线",
            )
        return handle

    @staticmethod
    def _bot_row(
        connection: sqlite3.Connection,
        *,
        organization_id: str,
        bot_id: str | None = None,
        handle: str | None = None,
    ) -> sqlite3.Row | None:
        predicate = "b.bot_id = ?" if bot_id is not None else "b.handle = ?"
        value = bot_id if bot_id is not None else handle
        return connection.execute(
            f"""
            SELECT b.*, p.display_name, p.status AS principal_status,
                   m.status AS membership_status, d.name AS department_name
            FROM organization_bot_profiles AS b
            JOIN identity_principals AS p ON p.principal_id = b.principal_id
            JOIN organization_memberships AS m
              ON m.membership_id = b.membership_id
            LEFT JOIN organization_departments AS d
              ON d.department_id = b.department_id
             AND d.organization_id = b.organization_id
            WHERE b.organization_id = ? AND {predicate}
              AND p.principal_kind = 'bot'
            """,
            (organization_id, value),
        ).fetchone()

    @staticmethod
    def _bot_record(row: sqlite3.Row) -> dict[str, Any]:
        capabilities = json.loads(str(row["capability_policy_json"]))
        reporting = json.loads(str(row["reporting_policy_json"]))
        return {
            "id": str(row["bot_id"]),
            "bot_member_id": str(row["bot_id"]),
            "display_name": str(row["display_name"]),
            "handle": str(row["handle"]),
            "actor_id": str(row["principal_id"]),
            "actor_type": "organization_bot",
            "department_id": row["department_id"],
            "department_name": str(row["department_name"] or ""),
            "description": str(row["description"]),
            "status": (
                "active"
                if row["lifecycle_state"] == "active"
                and row["principal_status"] == "active"
                and row["membership_status"] == "active"
                else "disabled"
            ),
            "reporting": reporting if isinstance(reporting, dict) else {},
            "capabilities": capabilities if isinstance(capabilities, list) else [],
            "token_prefix": str(row["token_prefix"]),
            "token_rotated_at": str(row["token_rotated_at"]),
            "has_token": True,
            "version": int(row["version"]),
            "expectedVersion": int(row["version"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def bots(
        self,
        identity: SessionIdentity,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status not in {None, "", "active", "disabled"}:
            raise RepositoryError(422, "bot_status_invalid", "机器人状态筛选无效")
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity)
            rows = connection.execute(
                """
                SELECT b.*, p.display_name, p.status AS principal_status,
                       m.status AS membership_status,
                       d.name AS department_name
                FROM organization_bot_profiles AS b
                JOIN identity_principals AS p
                  ON p.principal_id = b.principal_id
                 AND p.principal_kind = 'bot'
                JOIN organization_memberships AS m
                  ON m.membership_id = b.membership_id
                 AND m.organization_id = b.organization_id
                LEFT JOIN organization_departments AS d
                  ON d.department_id = b.department_id
                 AND d.organization_id = b.organization_id
                WHERE b.organization_id = ?
                  AND b.lifecycle_state != 'archived'
                ORDER BY b.created_at, b.bot_id
                """,
                (identity.organization_id,),
            ).fetchall()
        records = [self._bot_record(row) for row in rows]
        return [
            record
            for record in records
            if not status or record["status"] == status
        ]

    def bot(
        self,
        identity: SessionIdentity,
        *,
        bot_id: str | None = None,
        handle: str | None = None,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity)
            row = self._bot_row(
                connection,
                organization_id=identity.organization_id,
                bot_id=bot_id,
                handle=handle,
            )
        if row is None or row["lifecycle_state"] == "archived":
            raise RepositoryError(404, "bot_missing", "机器人不存在")
        return self._bot_record(row)

    @staticmethod
    def _validate_bot_department(
        connection: sqlite3.Connection,
        *,
        organization_id: str,
        department_id: str | None,
    ) -> None:
        if department_id is None:
            return
        row = connection.execute(
            """
            SELECT department_id
            FROM organization_departments
            WHERE organization_id = ? AND department_id = ?
              AND lifecycle_state = 'active'
            """,
            (organization_id, department_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(
                404,
                "bot_department_missing",
                "机器人所属部门不存在或已归档",
            )

    @staticmethod
    def _write_bot_authorization(
        connection: sqlite3.Connection,
        *,
        identity: SessionIdentity,
        bot_id: str,
        bot_principal_id: str,
        bot_membership_id: str,
        capabilities: list[dict[str, Any]],
        now: str,
        policy_version: int,
    ) -> None:
        resource = connection.execute(
            """
            SELECT resource_id, version
            FROM authorization_resources
            WHERE resource_id = ? AND scope_id = ?
              AND resource_kind = 'organization_bot'
            """,
            (bot_id, identity.scope_id),
        ).fetchone()
        if resource is None:
            connection.execute(
                """
                INSERT INTO authorization_resources (
                    resource_id, scope_id, resource_kind, lifecycle_state,
                    version, created_at, updated_at
                ) VALUES (?, ?, 'organization_bot', 'active', 1, ?, ?)
                """,
                (bot_id, identity.scope_id, now, now),
            )
        else:
            connection.execute(
                """
                UPDATE authorization_resources
                SET lifecycle_state = 'active', version = version + 1,
                    updated_at = ?
                WHERE resource_id = ? AND scope_id = ? AND version = ?
                """,
                (
                    now,
                    bot_id,
                    identity.scope_id,
                    int(resource["version"]),
                ),
            )
        policy_id = new_id()
        connection.execute(
            """
            INSERT INTO authorization_policy_versions (
                policy_version_id, scope_id, resource_id,
                policy_scope_kind, version, policy_json, created_at
            ) VALUES (?, ?, ?, 'organization', ?, ?, ?)
            """,
            (
                policy_id,
                identity.scope_id,
                bot_id,
                policy_version,
                canonical_json({"capabilities": capabilities}),
                now,
            ),
        )
        connection.execute(
            """
            UPDATE authorization_grants
            SET status = 'revoked', updated_at = ?
            WHERE scope_id = ? AND resource_id = ? AND status = 'active'
            """,
            (now, identity.scope_id, bot_id),
        )
        enabled = [
            item["capability_key"] for item in capabilities if item["enabled"]
        ]
        connection.execute(
            """
            INSERT INTO authorization_grants (
                grant_id, scope_id, resource_id, policy_version_id,
                subject_principal_id, subject_membership_id,
                capability_set, grant_generation, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                new_id(),
                identity.scope_id,
                bot_id,
                policy_id,
                bot_principal_id,
                bot_membership_id,
                canonical_json(enabled),
                policy_version,
                now,
                now,
            ),
        )

    def create_bot(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        display_name = str(payload.get("display_name") or "").strip()
        if not display_name:
            raise RepositoryError(422, "bot_display_name_required", "机器人名称不能为空")
        if len(display_name) > 120:
            raise RepositoryError(422, "bot_display_name_too_long", "机器人名称过长")
        raw_handle = str(payload.get("handle") or "").strip()
        requested_handle = (
            self._validate_bot_handle(raw_handle) if raw_handle else None
        )
        department_id = (
            str(payload.get("department_id") or "").strip() or None
        )
        description = str(payload.get("description") or "").strip()
        capabilities = self._bot_capability_policy(
            payload.get("enabled_capabilities") or []
        )
        requested_reporting = self._bot_reporting(payload)
        token_plain: str | None = None
        committed = False
        safe_payload = {
            "displayName": display_name,
            "requestedHandle": requested_handle,
            "departmentId": department_id,
            "description": description,
            "reporting": requested_reporting,
            "capabilities": capabilities,
        }

        def mutate(
            connection: sqlite3.Connection,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            nonlocal committed
            nonlocal token_plain
            self._assert_identity(connection, identity, admin=True)
            self._validate_bot_department(
                connection,
                organization_id=identity.organization_id,
                department_id=department_id,
            )
            now = utc_now()
            bot_id = new_id()
            handle = requested_handle or self._validate_bot_handle(
                f"bot_{bot_id.replace('-', '')[-12:]}"
            )
            token_plain = new_secret_token()
            token_fingerprint = hash_token(token_plain)
            principal_id = new_id()
            membership_id = new_id()
            reporting = self._resolved_bot_reporting(
                connection,
                organization_id=identity.organization_id,
                department_id=department_id,
                creator_membership_id=identity.membership_id,
                requested=payload,
            )
            connection.execute(
                """
                INSERT INTO identity_principals (
                    principal_id, principal_kind, display_name, status,
                    identity_version, created_at, updated_at
                ) VALUES (?, 'bot', ?, 'active', 1, ?, ?)
                """,
                (principal_id, display_name, now, now),
            )
            connection.execute(
                """
                INSERT INTO organization_memberships (
                    membership_id, scope_id, organization_id, principal_id,
                    system_role, visibility_scope, status, version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'member', 'organization', 'active',
                          1, ?, ?)
                """,
                (
                    membership_id,
                    identity.scope_id,
                    identity.organization_id,
                    principal_id,
                    now,
                    now,
                ),
            )
            if department_id:
                connection.execute(
                    """
                    INSERT INTO department_memberships (
                        department_membership_id, organization_id,
                        department_id, membership_id, is_department_lead,
                        status, version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 0, 'active', 1, ?, ?)
                    """,
                    (
                        new_id(),
                        identity.organization_id,
                        department_id,
                        membership_id,
                        now,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO organization_bot_profiles (
                    bot_id, organization_id, principal_id, membership_id,
                    handle, description, department_id,
                    reporting_policy_json, capability_policy_json,
                    token_hash, token_prefix, token_rotated_at,
                    lifecycle_state, version, created_by_membership_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'active', 1, ?, ?, ?)
                """,
                (
                    bot_id,
                    identity.organization_id,
                    principal_id,
                    membership_id,
                    handle,
                    description,
                    department_id,
                    canonical_json(reporting),
                    canonical_json(capabilities),
                    token_fingerprint,
                    token_plain[:12],
                    now,
                    identity.membership_id,
                    now,
                    now,
                ),
            )
            self._write_bot_authorization(
                connection,
                identity=identity,
                bot_id=bot_id,
                bot_principal_id=principal_id,
                bot_membership_id=membership_id,
                capabilities=capabilities,
                now=now,
                policy_version=1,
            )
            row = self._bot_row(
                connection,
                organization_id=identity.organization_id,
                bot_id=bot_id,
            )
            if row is None:
                raise RepositoryError(500, "bot_create_failed", "机器人创建失败")
            committed = True
            return (
                {**self._bot_record(row), "tokenAlreadyIssued": True},
                {
                    "aggregate_type": "organization_bot",
                    "aggregate_id": bot_id,
                    "before_version": None,
                    "after_version": 1,
                    "audit_summary": {
                        "handle": handle,
                        "departmentId": department_id,
                        "enabledCapabilities": [
                            item["capability_key"]
                            for item in capabilities
                            if item["enabled"]
                        ],
                        "tokenFingerprint": token_fingerprint,
                    },
                },
            )

        result = self._idempotent_mutation(
            identity,
            command_type="organization.bot.created",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            mutation=mutate,
        )
        if committed and token_plain is not None:
            return {
                **result,
                "token_plain": token_plain,
                "tokenAlreadyIssued": False,
            }
        return result

    def update_bot(
        self,
        identity: SessionIdentity,
        *,
        bot_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        try:
            expected = int(
                payload.get("expectedVersion", payload.get("expected_version"))
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                428,
                "bot_expected_version_required",
                "机器人修改必须携带 expectedVersion",
            ) from exc
        if expected < 1:
            raise RepositoryError(
                422,
                "bot_expected_version_invalid",
                "机器人 expectedVersion 无效",
            )
        safe_payload = {
            key: value
            for key, value in dict(payload).items()
            if key
            not in {
                "created_by_user_id",
                "department_leader_user_ids",
                "ceo_user_ids",
            }
        }
        safe_payload["botId"] = bot_id
        safe_payload["expectedVersion"] = expected

        def mutate(
            connection: sqlite3.Connection,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            self._assert_identity(connection, identity, admin=True)
            row = self._bot_row(
                connection,
                organization_id=identity.organization_id,
                bot_id=bot_id,
            )
            if row is None or row["lifecycle_state"] == "archived":
                raise RepositoryError(404, "bot_missing", "机器人不存在")
            if int(row["version"]) != expected:
                raise RepositoryError(
                    409,
                    "bot_version_conflict",
                    "机器人已更新，请刷新后重试",
                )
            display_name = str(
                payload.get("display_name", row["display_name"])
            ).strip()
            if not display_name:
                raise RepositoryError(
                    422,
                    "bot_display_name_required",
                    "机器人名称不能为空",
                )
            handle = (
                self._validate_bot_handle(str(payload["handle"]))
                if payload.get("handle") is not None
                else str(row["handle"])
            )
            department_id = (
                str(payload.get("department_id") or "").strip() or None
                if "department_id" in payload
                else row["department_id"]
            )
            self._validate_bot_department(
                connection,
                organization_id=identity.organization_id,
                department_id=department_id,
            )
            description = str(
                payload.get("description", row["description"])
            ).strip()
            current_capabilities = json.loads(
                str(row["capability_policy_json"])
            )
            capabilities = (
                self._bot_capability_policy(payload["enabled_capabilities"])
                if "enabled_capabilities" in payload
                else current_capabilities
            )
            if not isinstance(capabilities, list):
                raise RepositoryError(
                    500,
                    "bot_capability_policy_corrupt",
                    "机器人能力策略损坏",
                )
            current_reporting = json.loads(str(row["reporting_policy_json"]))
            reporting_input = {
                **(
                    current_reporting
                    if isinstance(current_reporting, dict)
                    else {}
                ),
                **dict(payload),
            }
            reporting = self._resolved_bot_reporting(
                connection,
                organization_id=identity.organization_id,
                department_id=department_id,
                creator_membership_id=str(row["created_by_membership_id"]),
                requested=reporting_input,
            )
            status = str(payload.get("status") or "").strip() or (
                "active" if row["lifecycle_state"] == "active" else "disabled"
            )
            if status not in {"active", "disabled"}:
                raise RepositoryError(422, "bot_status_invalid", "机器人状态无效")
            now = utc_now()
            changed = connection.execute(
                """
                UPDATE organization_bot_profiles
                SET handle = ?, description = ?, department_id = ?,
                    reporting_policy_json = ?, capability_policy_json = ?,
                    lifecycle_state = ?, version = version + 1,
                    updated_at = ?
                WHERE organization_id = ? AND bot_id = ? AND version = ?
                """,
                (
                    handle,
                    description,
                    department_id,
                    canonical_json(reporting),
                    canonical_json(capabilities),
                    status,
                    now,
                    identity.organization_id,
                    bot_id,
                    expected,
                ),
            )
            if changed.rowcount != 1:
                raise RepositoryError(
                    409,
                    "bot_version_conflict",
                    "机器人已更新，请刷新后重试",
                )
            connection.execute(
                """
                UPDATE identity_principals
                SET display_name = ?, status = ?,
                    identity_version = identity_version + 1, updated_at = ?
                WHERE principal_id = ? AND principal_kind = 'bot'
                """,
                (
                    display_name,
                    status,
                    now,
                    row["principal_id"],
                ),
            )
            connection.execute(
                """
                UPDATE organization_memberships
                SET status = ?, version = version + 1, updated_at = ?
                WHERE organization_id = ? AND membership_id = ?
                """,
                (
                    status,
                    now,
                    identity.organization_id,
                    row["membership_id"],
                ),
            )
            connection.execute(
                """
                UPDATE department_memberships
                SET status = 'revoked', version = version + 1, updated_at = ?
                WHERE organization_id = ? AND membership_id = ?
                  AND status = 'active'
                """,
                (now, identity.organization_id, row["membership_id"]),
            )
            if department_id and status == "active":
                current_department = connection.execute(
                    """
                    SELECT department_membership_id, version
                    FROM department_memberships
                    WHERE organization_id = ? AND department_id = ?
                      AND membership_id = ?
                    """,
                    (
                        identity.organization_id,
                        department_id,
                        row["membership_id"],
                    ),
                ).fetchone()
                if current_department is None:
                    connection.execute(
                        """
                        INSERT INTO department_memberships (
                            department_membership_id, organization_id,
                            department_id, membership_id, is_department_lead,
                            status, version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, 0, 'active', 1, ?, ?)
                        """,
                        (
                            new_id(),
                            identity.organization_id,
                            department_id,
                            row["membership_id"],
                            now,
                            now,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE department_memberships
                        SET status = 'active', version = version + 1,
                            updated_at = ?
                        WHERE department_membership_id = ? AND version = ?
                        """,
                        (
                            now,
                            current_department["department_membership_id"],
                            int(current_department["version"]),
                        ),
                    )
            self._write_bot_authorization(
                connection,
                identity=identity,
                bot_id=bot_id,
                bot_principal_id=str(row["principal_id"]),
                bot_membership_id=str(row["membership_id"]),
                capabilities=capabilities,
                now=now,
                policy_version=expected + 1,
            )
            updated = self._bot_row(
                connection,
                organization_id=identity.organization_id,
                bot_id=bot_id,
            )
            if updated is None:
                raise RepositoryError(500, "bot_update_failed", "机器人更新失败")
            return (
                self._bot_record(updated),
                {
                    "aggregate_type": "organization_bot",
                    "aggregate_id": bot_id,
                    "before_version": expected,
                    "after_version": expected + 1,
                    "audit_summary": {
                        "status": status,
                        "departmentId": department_id,
                        "enabledCapabilities": [
                            item["capability_key"]
                            for item in capabilities
                            if item["enabled"]
                        ],
                    },
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="organization.bot.updated",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            mutation=mutate,
        )

    def rotate_bot_token(
        self,
        identity: SessionIdentity,
        *,
        bot_id: str,
        expected_version: int,
        idempotency_key: str,
        presented_token: str | None = None,
    ) -> dict[str, Any]:
        if expected_version < 1:
            raise RepositoryError(
                428,
                "bot_expected_version_required",
                "轮换机器人 token 必须携带 expectedVersion",
            )
        if presented_token is not None and len(presented_token) < 32:
            raise RepositoryError(
                422,
                "bot_token_too_short",
                "机器人 token 至少需要 32 个字符",
            )
        token_plain: str | None = None
        committed = False
        safe_payload = {
            "botId": bot_id,
            "expectedVersion": expected_version,
            "presentedTokenFingerprint": (
                hash_token(presented_token) if presented_token is not None else None
            ),
        }

        def mutate(
            connection: sqlite3.Connection,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            nonlocal committed
            nonlocal token_plain
            self._assert_identity(connection, identity, admin=True)
            row = self._bot_row(
                connection,
                organization_id=identity.organization_id,
                bot_id=bot_id,
            )
            if row is None or row["lifecycle_state"] == "archived":
                raise RepositoryError(404, "bot_missing", "机器人不存在")
            if int(row["version"]) != expected_version:
                raise RepositoryError(
                    409,
                    "bot_version_conflict",
                    "机器人已更新，请刷新后重试",
                )
            token_plain = presented_token or new_secret_token()
            token_fingerprint = hash_token(token_plain)
            now = utc_now()
            changed = connection.execute(
                """
                UPDATE organization_bot_profiles
                SET token_hash = ?, token_prefix = ?, token_rotated_at = ?,
                    version = version + 1, updated_at = ?
                WHERE organization_id = ? AND bot_id = ? AND version = ?
                """,
                (
                    token_fingerprint,
                    token_plain[:12],
                    now,
                    now,
                    identity.organization_id,
                    bot_id,
                    expected_version,
                ),
            )
            if changed.rowcount != 1:
                raise RepositoryError(
                    409,
                    "bot_version_conflict",
                    "机器人已更新，请刷新后重试",
                )
            updated = self._bot_row(
                connection,
                organization_id=identity.organization_id,
                bot_id=bot_id,
            )
            if updated is None:
                raise RepositoryError(500, "bot_rotation_failed", "机器人 token 轮换失败")
            committed = True
            return (
                {**self._bot_record(updated), "tokenAlreadyIssued": True},
                {
                    "aggregate_type": "organization_bot",
                    "aggregate_id": bot_id,
                    "before_version": expected_version,
                    "after_version": expected_version + 1,
                    "audit_summary": {
                        "tokenFingerprint": token_fingerprint,
                        "rotated": True,
                    },
                },
            )

        result = self._idempotent_mutation(
            identity,
            command_type="organization.bot.token_rotated",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            mutation=mutate,
        )
        if committed and token_plain is not None:
            return {
                **result,
                "token_plain": token_plain,
                "tokenAlreadyIssued": False,
            }
        return result

    def bot_permissions(
        self,
        identity: SessionIdentity,
        *,
        bot_id: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity)
            row = self._bot_row(
                connection,
                organization_id=identity.organization_id,
                bot_id=bot_id,
            )
            if row is None or row["lifecycle_state"] == "archived":
                raise RepositoryError(404, "bot_missing", "机器人不存在")
            grant = connection.execute(
                """
                SELECT capability_set
                FROM authorization_grants
                WHERE scope_id = ? AND resource_id = ?
                  AND subject_principal_id = ?
                  AND subject_membership_id = ? AND status = 'active'
                ORDER BY grant_generation DESC, created_at DESC
                LIMIT 1
                """,
                (
                    identity.scope_id,
                    bot_id,
                    row["principal_id"],
                    row["membership_id"],
                ),
            ).fetchone()
        enabled = (
            json.loads(str(grant["capability_set"])) if grant is not None else []
        )
        if not isinstance(enabled, list):
            raise RepositoryError(500, "bot_grant_corrupt", "机器人授权记录损坏")
        profile = json.loads(str(row["capability_policy_json"]))
        if not isinstance(profile, list):
            raise RepositoryError(500, "bot_capability_policy_corrupt", "机器人能力策略损坏")
        capabilities = [
            {**item, "enabled": item.get("capability_key") in enabled}
            for item in profile
            if isinstance(item, dict)
        ]
        return {
            "bot_member_id": bot_id,
            "actor_id": str(row["principal_id"]),
            "capabilities": capabilities,
            "hard_denies": [
                key for key in BOT_CAPABILITIES if key not in enabled
            ],
            "inline_approval_blocked_actions": [
                item["capability_key"]
                for item in capabilities
                if item.get("enabled") and item.get("approval_required")
            ],
            "version": int(row["version"]),
        }

    @staticmethod
    def _bot_plan_status(row: sqlite3.Row) -> str:
        if row["execution_state"] in {"queued", "running"}:
            return "executing"
        if row["execution_state"] == "completed":
            return "completed"
        return {
            "draft": "needs_revision",
            "pending": "pending_approval",
            "approved": "approved",
            "rejected": "rejected",
        }[str(row["approval_state"])]

    @classmethod
    def _bot_plan_record(cls, row: sqlite3.Row) -> dict[str, Any]:
        plan = json.loads(str(row["plan_json"]))
        if not isinstance(plan, dict):
            raise RepositoryError(500, "bot_plan_corrupt", "机器人任务计划损坏")
        return {
            "id": str(row["plan_id"]),
            "ai_task_plan_id": str(row["plan_id"]),
            "bot_member_id": str(row["bot_id"]),
            "client_id": row["project_id"],
            "project_id": row["project_id"],
            "event_line_id": row["event_line_id"],
            "task_id": row["task_id"],
            "plan_title": str(plan.get("plan_title") or ""),
            "plan_text": str(plan.get("plan_text") or ""),
            "required_modules_json": canonical_json(
                plan.get("required_modules") or []
            ),
            "steps_json": canonical_json(plan.get("steps") or []),
            "expected_outputs_json": canonical_json(
                plan.get("expected_outputs") or []
            ),
            "approval_required": bool(plan.get("approval_required")),
            "approval_id": (
                str(row["plan_id"])
                if row["approval_state"] in {"pending", "approved", "rejected"}
                else None
            ),
            "approval_source": str(
                plan.get("approval_source") or "bot_capability_policy"
            ),
            "approval_status": str(row["approval_state"]),
            "status": cls._bot_plan_status(row),
            "human_initiator_id": str(row["initiator_membership_id"]),
            "approved_by": row["approved_by_membership_id"],
            "approved_at": plan.get("approved_at"),
            "supervisor_feedback": plan.get("supervisor_feedback"),
            "plan_version": int(row["version"]),
            "version": int(row["version"]),
            "expectedVersion": int(row["version"]),
            "prev_plan_json": plan.get("previous_plan_json"),
            "execution_state": str(row["execution_state"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    @staticmethod
    def _bot_plan_link(
        connection: sqlite3.Connection,
        *,
        table: str,
        id_column: str,
        organization_id: str,
        scope_column: str = "organization_id",
        value: str | None,
        code: str,
        message: str,
    ) -> str | None:
        if not value:
            return None
        row = connection.execute(
            f"""
            SELECT {id_column}
            FROM {table}
            WHERE {scope_column} = ? AND {id_column} = ?
            """,
            (organization_id, value),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, code, message)
        return value

    @staticmethod
    def _required_bot_capabilities(payload: Mapping[str, Any]) -> set[str]:
        explicit = str(payload.get("action_capability") or "").strip()
        if explicit:
            if explicit not in BOT_CAPABILITIES:
                raise RepositoryError(
                    422,
                    "bot_action_capability_invalid",
                    "任务计划要求的机器人能力无效",
                )
            return {explicit}
        module_capabilities = {
            "smart_import": "data_center_parse.request",
            "web_search": "data_center_parse.request",
            "docs.create": "external_material_draft.create",
            "tasks.create": "workspace_file_write.request",
            "calendar.create": "workspace_file_write.request",
            "email.send": "external_send.request",
        }
        modules = payload.get("required_modules") or []
        if not isinstance(modules, list):
            raise RepositoryError(
                422,
                "bot_plan_modules_invalid",
                "任务计划模块必须是数组",
            )
        return {
            module_capabilities.get(
                str(module),
                "clarification_resolution.propose",
            )
            for module in modules
        } or {"clarification_resolution.propose"}

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
            raise RepositoryError(422, "bot_plan_title_required", "任务计划标题不能为空")
        required = self._required_bot_capabilities(payload)
        safe_plan = redact_payload(dict(payload))
        if safe_plan != dict(payload):
            raise RepositoryError(
                422,
                "bot_plan_contains_secret",
                "机器人任务计划不得包含 token、密码或 API Key",
            )
        encoded = canonical_json(safe_plan)
        if len(encoded.encode("utf-8")) > 512 * 1024:
            raise RepositoryError(413, "bot_plan_too_large", "机器人任务计划过大")
        safe_payload = {
            "botId": bot_id,
            "plan": safe_plan,
            "requiredCapabilities": sorted(required),
        }

        def mutate(
            connection: sqlite3.Connection,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            member = self._assert_identity(connection, identity)
            plan_id = new_id()
            bot = self._bot_row(
                connection,
                organization_id=identity.organization_id,
                bot_id=bot_id,
            )
            if (
                bot is None
                or bot["lifecycle_state"] != "active"
                or bot["membership_status"] != "active"
                or bot["principal_status"] != "active"
            ):
                raise RepositoryError(
                    404,
                    "active_bot_missing",
                    "机器人不存在或已停用",
                )
            grant = connection.execute(
                """
                SELECT capability_set
                FROM authorization_grants
                WHERE scope_id = ? AND resource_id = ?
                  AND subject_principal_id = ?
                  AND subject_membership_id = ? AND status = 'active'
                ORDER BY grant_generation DESC
                LIMIT 1
                """,
                (
                    identity.scope_id,
                    bot_id,
                    bot["principal_id"],
                    bot["membership_id"],
                ),
            ).fetchone()
            enabled = (
                set(json.loads(str(grant["capability_set"])))
                if grant is not None
                else set()
            )
            missing = required.difference(enabled)
            if missing:
                raise RepositoryError(
                    403,
                    "bot_capability_not_granted",
                    "机器人未获该任务计划所需能力",
                )
            policy = json.loads(str(bot["capability_policy_json"]))
            policy_by_key = {
                str(item.get("capability_key")): item
                for item in policy
                if isinstance(item, dict)
            }
            approval_required = bool(payload.get("approval_required")) or any(
                bool(policy_by_key.get(key, {}).get("approval_required"))
                for key in required
            )
            project_value = str(
                payload.get("project_id") or payload.get("client_id") or ""
            ).strip() or None
            project_id = self._bot_plan_link(
                connection,
                table="clients",
                id_column="id",
                organization_id=identity.scope_id,
                scope_column="scope_id",
                value=project_value,
                code="bot_plan_project_missing",
                message="任务计划关联的严格项目不存在",
            )
            event_line_id = self._bot_plan_link(
                connection,
                table="event_line_records",
                id_column="event_line_id",
                organization_id=identity.organization_id,
                value=str(payload.get("event_line_id") or "").strip() or None,
                code="bot_plan_event_line_missing",
                message="任务计划关联的事件线不存在",
            )
            task_id = self._bot_plan_link(
                connection,
                table="task_records",
                id_column="task_id",
                organization_id=identity.organization_id,
                value=str(payload.get("task_id") or "").strip() or None,
                code="bot_plan_task_missing",
                message="任务计划关联的任务不存在",
            )
            if event_line_id and task_id:
                linked = connection.execute(
                    """
                    SELECT 1
                    FROM event_line_task_links
                    WHERE organization_id = ? AND event_line_id = ?
                      AND task_id = ? AND link_state = 'active'
                    """,
                    (identity.organization_id, event_line_id, task_id),
                ).fetchone()
                if linked is None:
                    raise RepositoryError(
                        409,
                        "bot_plan_task_event_line_conflict",
                        "任务与事件线没有 active 权威关系",
                    )
            now = utc_now()
            approval_state = "pending" if approval_required else "approved"
            approved_by = (
                None if approval_required else str(member["membership_id"])
            )
            normalized_plan = {
                **safe_plan,
                "plan_title": title,
                "required_capabilities": sorted(required),
                "approval_required": approval_required,
                "approval_source": "bot_capability_policy",
                "approved_at": None if approval_required else now,
            }
            connection.execute(
                """
                INSERT INTO bot_task_plans (
                    plan_id, organization_id, bot_id,
                    initiator_membership_id, project_id, event_line_id,
                    task_id, plan_json, approval_state, execution_state,
                    progress_json, approved_by_membership_id,
                    lifecycle_state, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'not_started', '{}',
                          ?, 'active', 1, ?, ?)
                """,
                (
                    plan_id,
                    identity.organization_id,
                    bot_id,
                    identity.membership_id,
                    project_id,
                    event_line_id,
                    task_id,
                    canonical_json(normalized_plan),
                    approval_state,
                    approved_by,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM bot_task_plans
                WHERE organization_id = ? AND plan_id = ?
                """,
                (identity.organization_id, plan_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(500, "bot_plan_create_failed", "任务计划创建失败")
            record = self._bot_plan_record(row)
            result = {
                "ai_task_plan_id": plan_id,
                "task_id": task_id,
                "approval_id": record["approval_id"],
                "approval_status": approval_state,
                "approval_source": "bot_capability_policy",
                "approved_by": approved_by,
                "status": record["status"],
                "pending_reason": (
                    "organization_admin_approval_required"
                    if approval_required
                    else None
                ),
                "version": 1,
            }
            return (
                result,
                {
                    "aggregate_type": "bot_task_plan",
                    "aggregate_id": plan_id,
                    "before_version": None,
                    "after_version": 1,
                    "audit_summary": {
                        "botId": bot_id,
                        "requiredCapabilities": sorted(required),
                        "approvalState": approval_state,
                    },
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="organization.bot_plan.created",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            mutation=mutate,
        )

    def bot_plans(
        self,
        identity: SessionIdentity,
        *,
        bot_id: str,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:
            member = self._assert_identity(connection, identity)
            bot = self._bot_row(
                connection,
                organization_id=identity.organization_id,
                bot_id=bot_id,
            )
            if bot is None or bot["lifecycle_state"] == "archived":
                raise RepositoryError(404, "bot_missing", "机器人不存在")
            rows = connection.execute(
                """
                SELECT *
                FROM bot_task_plans
                WHERE organization_id = ? AND bot_id = ?
                  AND lifecycle_state = 'active'
                  AND (? = 'admin' OR initiator_membership_id = ?)
                ORDER BY created_at DESC, plan_id DESC
                LIMIT ?
                """,
                (
                    identity.organization_id,
                    bot_id,
                    member["system_role"],
                    identity.membership_id,
                    max(1, min(limit, 200)),
                ),
            ).fetchall()
        records = [self._bot_plan_record(row) for row in rows]
        return [
            record
            for record in records
            if not status or record["status"] == status
        ]

    def decide_bot_plan(
        self,
        identity: SessionIdentity,
        *,
        plan_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        decision = str(payload.get("decision") or "").strip()
        if decision not in {"approve", "reject", "revise"}:
            raise RepositoryError(422, "bot_plan_decision_invalid", "审批决定无效")
        try:
            expected = int(
                payload.get("expectedVersion", payload.get("expected_version"))
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                428,
                "bot_plan_expected_version_required",
                "审批任务计划必须携带 expectedVersion",
            ) from exc
        safe_payload = {
            "planId": plan_id,
            "decision": decision,
            "feedback": str(payload.get("feedback") or "").strip(),
            "modifiedPlan": payload.get("modified_plan"),
            "expectedVersion": expected,
        }
        if redact_payload(safe_payload) != safe_payload:
            raise RepositoryError(
                422,
                "bot_plan_decision_contains_secret",
                "审批内容不得包含 token、密码或 API Key",
            )

        def mutate(
            connection: sqlite3.Connection,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            self._assert_identity(connection, identity, admin=True)
            row = connection.execute(
                """
                SELECT *
                FROM bot_task_plans
                WHERE organization_id = ? AND plan_id = ?
                  AND lifecycle_state = 'active'
                """,
                (identity.organization_id, plan_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "bot_plan_missing", "机器人任务计划不存在")
            if int(row["version"]) != expected:
                raise RepositoryError(
                    409,
                    "bot_plan_version_conflict",
                    "机器人任务计划已更新，请刷新后重试",
                )
            if row["approval_state"] not in {"draft", "pending"}:
                raise RepositoryError(
                    409,
                    "bot_plan_decision_state_conflict",
                    "当前任务计划状态不允许再次审批",
                )
            current_plan = json.loads(str(row["plan_json"]))
            if not isinstance(current_plan, dict):
                raise RepositoryError(500, "bot_plan_corrupt", "机器人任务计划损坏")
            modified = payload.get("modified_plan")
            if modified is not None and not isinstance(modified, Mapping):
                raise RepositoryError(
                    422,
                    "bot_plan_modification_invalid",
                    "修改后的任务计划必须是对象",
                )
            next_plan = {
                **current_plan,
                **dict(modified or {}),
                "previous_plan_json": current_plan,
                "supervisor_feedback": str(payload.get("feedback") or "").strip(),
                "approved_at": utc_now() if decision == "approve" else None,
            }
            approval_state = {
                "approve": "approved",
                "reject": "rejected",
                "revise": "draft",
            }[decision]
            execution_state = (
                "cancelled"
                if decision == "reject"
                else str(row["execution_state"])
            )
            now = utc_now()
            changed = connection.execute(
                """
                UPDATE bot_task_plans
                SET plan_json = ?, approval_state = ?, execution_state = ?,
                    approved_by_membership_id = ?, version = version + 1,
                    updated_at = ?
                WHERE organization_id = ? AND plan_id = ? AND version = ?
                """,
                (
                    canonical_json(next_plan),
                    approval_state,
                    execution_state,
                    identity.membership_id,
                    now,
                    identity.organization_id,
                    plan_id,
                    expected,
                ),
            )
            if changed.rowcount != 1:
                raise RepositoryError(
                    409,
                    "bot_plan_version_conflict",
                    "机器人任务计划已更新，请刷新后重试",
                )
            updated = connection.execute(
                """
                SELECT * FROM bot_task_plans
                WHERE organization_id = ? AND plan_id = ?
                """,
                (identity.organization_id, plan_id),
            ).fetchone()
            if updated is None:
                raise RepositoryError(500, "bot_plan_update_failed", "任务计划审批失败")
            return (
                self._bot_plan_record(updated),
                {
                    "aggregate_type": "bot_task_plan",
                    "aggregate_id": plan_id,
                    "before_version": expected,
                    "after_version": expected + 1,
                    "audit_summary": {
                        "decision": decision,
                        "approvalState": approval_state,
                        "decidedByMembershipId": identity.membership_id,
                    },
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="organization.bot_plan.decided",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            mutation=mutate,
        )

    def bot_plan_progress(
        self,
        identity: SessionIdentity,
        *,
        plan_id: str,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            member = self._assert_identity(connection, identity)
            row = connection.execute(
                """
                SELECT *
                FROM bot_task_plans
                WHERE organization_id = ? AND plan_id = ?
                  AND lifecycle_state = 'active'
                """,
                (identity.organization_id, plan_id),
            ).fetchone()
        if row is None:
            raise RepositoryError(404, "bot_plan_missing", "机器人任务计划不存在")
        if (
            member["system_role"] != "admin"
            and row["initiator_membership_id"] != identity.membership_id
        ):
            raise RepositoryError(
                403,
                "bot_plan_read_forbidden",
                "只能读取本人发起的机器人任务计划",
            )
        plan = json.loads(str(row["plan_json"]))
        progress = json.loads(str(row["progress_json"]))
        steps = plan.get("steps") or [] if isinstance(plan, dict) else []
        if not isinstance(progress, dict):
            progress = {}
        total = len(steps) if isinstance(steps, list) else 0
        return {
            "plan_id": plan_id,
            "plan_status": self._bot_plan_status(row),
            "execution_status": {
                "not_started": "not_started",
                "queued": "pending_execute",
                "running": "running",
                "completed": "success",
                "failed": "failed",
                "cancelled": "failed",
            }[str(row["execution_state"])],
            "started_at": progress.get("started_at"),
            "completed_at": progress.get("completed_at"),
            "progress": {
                "total": int(progress.get("total", total)),
                "completed": int(progress.get("completed", 0)),
                "current": str(progress.get("current") or ""),
                "percent": int(progress.get("percent", 0)),
                "errors": progress.get("errors") or [],
            },
            "subtasks": progress.get("subtasks") or [],
            "errors": progress.get("errors") or [],
            "version": int(row["version"]),
            "expectedVersion": int(row["version"]),
            "updatedAt": str(row["updated_at"]),
        }

    @staticmethod
    def _system_admin_defaults() -> dict[str, bool]:
        return {
            "allowBusinessSettingsForEmployees": True,
            "allowOrgDnaForEmployees": True,
            "protectEmployeeAdmin": True,
            "protectAiAndCloud": True,
            "protectCloudSecurity": True,
        }

    def system_admin_settings(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity, admin=True)
            row = connection.execute(
                """
                SELECT r.resource_id, r.version, r.updated_at, p.policy_json
                FROM authorization_resources AS r
                JOIN authorization_policy_versions AS p
                  ON p.resource_id = r.resource_id AND p.scope_id = r.scope_id
                WHERE r.scope_id = ? AND r.resource_kind = 'system_admin_settings'
                  AND r.lifecycle_state = 'active'
                ORDER BY p.version DESC, p.created_at DESC
                LIMIT 1
                """,
                (identity.scope_id,),
            ).fetchone()
        if row is None:
            return {
                **self._system_admin_defaults(),
                "updatedAt": "",
                "version": 0,
                "expectedVersion": 0,
            }
        policy = json.loads(str(row["policy_json"]))
        settings = (
            policy.get("settings") if isinstance(policy, dict) else None
        )
        if not isinstance(settings, dict):
            raise RepositoryError(
                500,
                "system_admin_policy_corrupt",
                "系统管理授权策略损坏",
            )
        return {
            **self._system_admin_defaults(),
            **settings,
            "updatedAt": str(row["updated_at"]),
            "version": int(row["version"]),
            "expectedVersion": int(row["version"]),
        }

    def update_system_admin_settings(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        try:
            expected = int(
                payload.get("expectedVersion", payload.get("expected_version"))
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                428,
                "system_admin_expected_version_required",
                "系统管理策略写入必须携带 expectedVersion",
            ) from exc
        defaults = self._system_admin_defaults()
        normalized = {
            key: bool(payload.get(key, value))
            for key, value in defaults.items()
        }
        safe_payload = {**normalized, "expectedVersion": expected}

        def mutate(
            connection: sqlite3.Connection,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            self._assert_identity(connection, identity, admin=True)
            resource = connection.execute(
                """
                SELECT resource_id, version, created_at
                FROM authorization_resources
                WHERE scope_id = ? AND resource_kind = 'system_admin_settings'
                  AND lifecycle_state = 'active'
                ORDER BY created_at, resource_id
                LIMIT 1
                """,
                (identity.scope_id,),
            ).fetchone()
            now = utc_now()
            if resource is None:
                if expected != 0:
                    raise RepositoryError(
                        409,
                        "system_admin_version_conflict",
                        "系统管理策略尚未创建，请刷新后重试",
                    )
                resource_id = new_id()
                before_version = None
                after_version = 1
                connection.execute(
                    """
                    INSERT INTO authorization_resources (
                        resource_id, scope_id, resource_kind,
                        lifecycle_state, version, created_at, updated_at
                    ) VALUES (?, ?, 'system_admin_settings', 'active', 1, ?, ?)
                    """,
                    (resource_id, identity.scope_id, now, now),
                )
            else:
                current_version = int(resource["version"])
                if expected != current_version:
                    raise RepositoryError(
                        409,
                        "system_admin_version_conflict",
                        "系统管理策略已更新，请刷新后重试",
                    )
                resource_id = str(resource["resource_id"])
                changed = connection.execute(
                    """
                    UPDATE authorization_resources
                    SET version = version + 1, updated_at = ?
                    WHERE resource_id = ? AND scope_id = ? AND version = ?
                    """,
                    (now, resource_id, identity.scope_id, current_version),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "system_admin_version_conflict",
                        "系统管理策略已更新，请刷新后重试",
                    )
                before_version = current_version
                after_version = current_version + 1
            policy_version_id = new_id()
            connection.execute(
                """
                INSERT INTO authorization_policy_versions (
                    policy_version_id, scope_id, resource_id,
                    policy_scope_kind, version, policy_json, created_at
                ) VALUES (?, ?, ?, 'organization', ?, ?, ?)
                """,
                (
                    policy_version_id,
                    identity.scope_id,
                    resource_id,
                    after_version,
                    canonical_json(
                        {
                            "settings": normalized,
                            "capability": "system_admin_settings.manage",
                        }
                    ),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE authorization_grants
                SET status = 'revoked', updated_at = ?
                WHERE scope_id = ? AND resource_id = ? AND status = 'active'
                """,
                (now, identity.scope_id, resource_id),
            )
            admins = connection.execute(
                """
                SELECT membership_id, principal_id
                FROM organization_memberships
                WHERE organization_id = ? AND system_role = 'admin'
                  AND status = 'active'
                ORDER BY membership_id
                """,
                (identity.organization_id,),
            ).fetchall()
            for admin in admins:
                connection.execute(
                    """
                    INSERT INTO authorization_grants (
                        grant_id, scope_id, resource_id, policy_version_id,
                        subject_principal_id, subject_membership_id,
                        capability_set, grant_generation, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?,
                              '["system_admin_settings.manage"]', ?,
                              'active', ?, ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        resource_id,
                        policy_version_id,
                        admin["principal_id"],
                        admin["membership_id"],
                        after_version,
                        now,
                        now,
                    ),
                )
            result = {
                **normalized,
                "updatedAt": now,
                "version": after_version,
                "expectedVersion": after_version,
            }
            return (
                result,
                {
                    "aggregate_type": "authorization_policy",
                    "aggregate_id": resource_id,
                    "before_version": before_version,
                    "after_version": after_version,
                    "audit_summary": {
                        "policyKind": "system_admin_settings",
                        "settings": normalized,
                        "grantedAdminCount": len(admins),
                    },
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="authorization.system_admin_settings.updated",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            mutation=mutate,
        )

    def organization_intro_document(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        with self.repository._connection() as connection:
            self._assert_identity(connection, identity, admin=True)
            row = connection.execute(
                """
                SELECT d.*, v.content_hash, v.markdown_content
                FROM knowledge_documents AS d
                JOIN document_versions AS v
                  ON v.document_id = d.document_id
                 AND v.version = d.current_version
                WHERE d.organization_id = ?
                  AND d.project_assignment_state = 'unassigned'
                  AND d.document_kind = 'organization_intro_document'
                  AND d.visibility_scope = 'organization'
                  AND d.lifecycle_state = 'active'
                ORDER BY d.updated_at DESC, d.document_id
                LIMIT 1
                """,
                (identity.organization_id,),
            ).fetchone()
        if row is None:
            return {
                "fileName": "",
                "fileType": "",
                "markdownContent": "",
                "normalizedText": "",
                "summary": "",
                "contentHash": "",
                "uploadedBy": "",
                "uploadedAt": "",
                "version": 0,
                "expectedVersion": 0,
            }
        markdown = str(row["markdown_content"])
        normalized = " ".join(markdown.split())
        return {
            "fileName": str(row["title"]),
            "fileType": (
                Path(str(row["title"])).suffix.casefold().lstrip(".") or "md"
            ),
            "markdownContent": markdown,
            "normalizedText": normalized,
            "summary": normalized[:500],
            "contentHash": str(row["content_hash"]),
            "uploadedBy": str(row["owner_membership_id"] or ""),
            "uploadedAt": str(row["updated_at"]),
            "documentId": str(row["document_id"]),
            "version": int(row["version"]),
            "expectedVersion": int(row["version"]),
        }

    def save_organization_intro_document(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        markdown = str(payload.get("markdownContent") or "")
        if not markdown.strip():
            if str(payload.get("filePath") or "").strip():
                raise RepositoryError(
                    422,
                    "intro_document_local_path_rejected",
                    "云端不读取本机路径；请由本机受管导入提取正文后再提交",
                )
            raise RepositoryError(
                422,
                "intro_document_content_required",
                "组织介绍文档正文不能为空",
            )
        if len(markdown.encode("utf-8")) > 2 * 1024 * 1024:
            raise RepositoryError(
                413,
                "intro_document_too_large",
                "组织介绍文档正文超过 2 MiB 限制",
            )
        try:
            expected = int(
                payload.get("expectedVersion", payload.get("expected_version"))
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                428,
                "intro_document_expected_version_required",
                "组织介绍文档写入必须携带 expectedVersion",
            ) from exc
        file_name = str(
            payload.get("fileName") or payload.get("title") or "组织介绍.md"
        ).strip()
        if not file_name:
            file_name = "组织介绍.md"
        content_hash = sha256_text(markdown)
        safe_payload = {
            "fileName": file_name,
            "contentHash": content_hash,
            "expectedVersion": expected,
            "byteSize": len(markdown.encode("utf-8")),
        }

        def mutate(
            connection: sqlite3.Connection,
        ) -> tuple[dict[str, Any], dict[str, Any]]:
            self._assert_identity(connection, identity, admin=True)
            row = connection.execute(
                """
                SELECT *
                FROM knowledge_documents
                WHERE organization_id = ?
                  AND project_assignment_state = 'unassigned'
                  AND document_kind = 'organization_intro_document'
                  AND visibility_scope = 'organization'
                  AND lifecycle_state = 'active'
                ORDER BY updated_at DESC, document_id
                LIMIT 1
                """,
                (identity.organization_id,),
            ).fetchone()
            now = utc_now()
            if row is None:
                if expected != 0:
                    raise RepositoryError(
                        409,
                        "intro_document_version_conflict",
                        "组织介绍文档尚未创建，请刷新后重试",
                    )
                document_id = new_id()
                document_version = 1
                aggregate_version = 1
                before_version = None
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        document_id, organization_id, project_id,
                        project_assignment_state, source_asset_id,
                        owner_membership_id, department_id, title,
                        document_kind, visibility_scope, parse_state,
                        lifecycle_state, current_version, version,
                        created_at, updated_at
                    ) VALUES (?, ?, NULL, 'unassigned', NULL, ?, NULL, ?,
                              'organization_intro_document', 'organization',
                              'ready', 'active', 1, 1, ?, ?)
                    """,
                    (
                        document_id,
                        identity.organization_id,
                        identity.membership_id,
                        file_name,
                        now,
                        now,
                    ),
                )
            else:
                current_version = int(row["version"])
                if expected != current_version:
                    raise RepositoryError(
                        409,
                        "intro_document_version_conflict",
                        "组织介绍文档已更新，请刷新后重试",
                    )
                document_id = str(row["document_id"])
                document_version = int(row["current_version"]) + 1
                aggregate_version = current_version + 1
                before_version = current_version
                changed = connection.execute(
                    """
                    UPDATE knowledge_documents
                    SET title = ?, owner_membership_id = ?,
                        current_version = ?, version = version + 1,
                        parse_state = 'ready', updated_at = ?
                    WHERE organization_id = ? AND document_id = ?
                      AND version = ?
                    """,
                    (
                        file_name,
                        identity.membership_id,
                        document_version,
                        now,
                        identity.organization_id,
                        document_id,
                        current_version,
                    ),
                )
                if changed.rowcount != 1:
                    raise RepositoryError(
                        409,
                        "intro_document_version_conflict",
                        "组织介绍文档已更新，请刷新后重试",
                    )
            normalized = " ".join(markdown.split())
            connection.execute(
                """
                INSERT INTO document_versions (
                    document_version_id, organization_id, document_id,
                    version, content_hash, preview_text, markdown_content,
                    section_count, chunk_count, generator_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0,
                          'organization-intro-v1', ?)
                """,
                (
                    new_id(),
                    identity.organization_id,
                    document_id,
                    document_version,
                    content_hash,
                    normalized[:1200],
                    markdown,
                    now,
                ),
            )
            result = {
                "fileName": file_name,
                "fileType": Path(file_name).suffix.casefold().lstrip(".") or "md",
                "markdownContent": markdown,
                "normalizedText": normalized,
                "summary": normalized[:500],
                "contentHash": content_hash,
                "uploadedBy": identity.membership_id,
                "uploadedAt": now,
                "documentId": document_id,
                "version": aggregate_version,
                "expectedVersion": aggregate_version,
            }
            return (
                result,
                {
                    "aggregate_type": "knowledge_document",
                    "aggregate_id": document_id,
                    "before_version": before_version,
                    "after_version": aggregate_version,
                    "audit_summary": {
                        "documentKind": "organization_intro_document",
                        "fileName": file_name,
                        "contentHash": content_hash,
                        "byteSize": len(markdown.encode("utf-8")),
                    },
                },
            )

        return self._idempotent_mutation(
            identity,
            command_type="organization.intro_document.saved",
            idempotency_key=idempotency_key,
            payload=safe_payload,
            mutation=mutate,
        )
