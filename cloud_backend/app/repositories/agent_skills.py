from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from strict_common.agent_memory import BUILTIN_AGENT_KINDS, builtin_agent_id
from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.security import payload_fingerprint

from ..repository import CloudRepository, RepositoryError, SessionIdentity


TRIGGER_SCHEMA = "yiyu.agent-skill-trigger.v1"
ACTION_SCHEMA = "yiyu.agent-skill-action.v1"
CAPABILITY_SCHEMA = "yiyu.agent-skill-grant.v1"
POLICY_SCHEMA = "yiyu.agent-skill-policy.v1"
_VISIBILITIES = frozenset({"private", "organization", "department", "selected_members"})


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, ValueError):
        return fallback


def _operation_id(scope_id: str, command_type: str, idempotency_key: str) -> str:
    return "op_" + sha256_text(f"agent-skill\x1f{scope_id}\x1f{command_type}\x1f{idempotency_key}")[:30]


def _record_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_" + sha256_text("\x1f".join(parts))[:30]


def _lease_expires_at() -> str:
    return (
        (datetime.now(timezone.utc) + timedelta(hours=24))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _validate_draft(payload: Mapping[str, Any]) -> dict[str, Any]:
    skill_type = str(payload.get("skillType") or "agent_skill").strip()
    if skill_type not in {"agent_skill", "writing_style"}:
        raise RepositoryError(422, "agent_skill_type_invalid", "Skill 类型无效")
    short_name = str(payload.get("shortName") or "").strip()
    description = str(payload.get("description") or "").strip()
    instructions = [str(item).strip() for item in payload.get("instructions") or [] if str(item).strip()]
    output_template = str(payload.get("outputTemplate") or "").strip() or None
    allowed_tool_ids = [str(item).strip() for item in payload.get("allowedToolIds") or [] if str(item).strip()]
    visibility = str(payload.get("visibility") or "private").strip()
    grantees = sorted({str(item).strip() for item in payload.get("granteeMembershipIds") or [] if str(item).strip()})
    legacy_grantees = [
        str(item).strip()
        for item in payload.get("granteePrincipalIds") or []
        if str(item).strip()
    ]
    department_id = str(payload.get("departmentId") or "").strip() or None
    agent_kinds = sorted({str(item).strip() for item in payload.get("agentKinds") or ["project_workspace"] if str(item).strip()})
    if not 1 <= len(short_name) <= 24:
        raise RepositoryError(422, "agent_skill_name_invalid", "Skill 简称需为1至24个字符")
    if len(description) > 240:
        raise RepositoryError(422, "agent_skill_description_invalid", "Skill 说明不能超过240个字符")
    if not instructions or len(instructions) > 12 or any(len(item) > 500 for item in instructions):
        raise RepositoryError(422, "agent_skill_instructions_invalid", "Skill 需包含1至12条简短声明式指令")
    if sum(len(item) for item in instructions) > 4_000:
        raise RepositoryError(422, "agent_skill_instructions_too_long", "Skill 指令总长度不能超过4000个字符")
    if output_template and len(output_template) > 3_000:
        raise RepositoryError(422, "agent_skill_template_too_long", "Skill 输出模板不能超过3000个字符")
    if visibility not in _VISIBILITIES:
        raise RepositoryError(422, "agent_skill_visibility_invalid", "Skill 可见范围无效")
    if legacy_grantees:
        raise RepositoryError(
            422,
            "agent_skill_membership_id_required",
            "Skill 分享只接受稳定成员身份标识",
        )
    if visibility == "selected_members" and not grantees:
        raise RepositoryError(422, "agent_skill_grantees_required", "定向共享 Skill 必须选择成员")
    if visibility not in {"selected_members"} and grantees:
        raise RepositoryError(422, "agent_skill_grantees_unexpected", "当前可见范围不接受指定成员")
    if visibility == "department" and not department_id:
        raise RepositoryError(422, "agent_skill_department_required", "部门共享 Skill 必须选择部门")
    if visibility != "department" and department_id:
        raise RepositoryError(422, "agent_skill_department_unexpected", "当前可见范围不接受部门")
    if not agent_kinds or any(kind not in BUILTIN_AGENT_KINDS for kind in agent_kinds):
        raise RepositoryError(422, "agent_skill_agent_kind_invalid", "Skill 适用 Agent 无效")
    # Phase 16 only admits declarative analysis/generation. Tool execution is
    # enabled later through the registered-tool contract, never arbitrary code.
    if allowed_tool_ids:
        raise RepositoryError(409, "agent_skill_tools_not_connected", "已登记工具 Skill 尚未接通；当前仅支持纯分析与生成")
    forbidden = ("忽略核心指令", "覆盖岗位", "system prompt", "shell", "任意脚本")
    joined = "\n".join(instructions).lower()
    if any(token.lower() in joined for token in forbidden):
        raise RepositoryError(422, "agent_skill_boundary_override", "Skill 不得覆盖内置 Agent 岗位或执行任意脚本")
    return {
        "skillType": skill_type,
        "shortName": short_name,
        "description": description,
        "instructions": instructions,
        "outputTemplate": output_template,
        "allowedToolIds": [],
        "visibility": visibility,
        "departmentId": department_id,
        "granteeMembershipIds": grantees,
        "agentKinds": agent_kinds,
    }


def _dto(
    row: sqlite3.Row,
    *,
    authorization: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    trigger = _json(row["trigger_spec"], {})
    action = _json(row["action_spec"], {})
    canonical_content = canonical_json({"trigger": trigger, "action": action})
    return {
        "skillId": str(row["id"]),
        "skillType": str(action.get("skillType") or "agent_skill"),
        "shortName": str(action.get("shortName") or row["template_key"] or ""),
        "description": str(action.get("description") or ""),
        "instructions": list(action.get("instructions") or []),
        "outputTemplate": action.get("outputTemplate"),
        "allowedToolIds": list(action.get("allowedToolIds") or []),
        "visibility": str(action.get("visibility") or "private"),
        "departmentId": action.get("departmentId"),
        "granteeMembershipIds": list(action.get("granteeMembershipIds") or []),
        # Kept empty for one compatibility cycle.  Principal IDs are never an
        # accepted sharing input or authorization source.
        "granteePrincipalIds": [],
        "agentKinds": list(trigger.get("agentKinds") or []),
        "version": int(row["version"] or 1),
        "enabled": bool(row["enabled"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "publisherPrincipalId": str(action.get("publisherPrincipalId") or ""),
        "publisherMembershipId": str(action.get("publisherMembershipId") or ""),
        "contentHash": sha256_text(canonical_content),
        "capabilityBoundary": "declarative_only",
        "canManage": bool((authorization or {}).get("canManage")),
        "authorizationProjection": dict(authorization or {}),
    }


def _lead_department_ids(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
) -> set[str]:
    return {
        str(row["department_id"])
        for row in connection.execute(
            "SELECT department_id FROM organization_memberships "
            "WHERE scope_id=? AND record_kind='department_assignment' "
            "AND parent_membership_id=? AND role_key='department_lead' "
            "AND status='active' AND lifecycle_state='active'",
            (identity.scope_id, identity.membership_id),
        ).fetchall()
        if row["department_id"]
    }


def _member_department_ids(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
) -> set[str]:
    return {
        str(row["department_id"])
        for row in connection.execute(
            "SELECT department_id FROM organization_memberships "
            "WHERE scope_id=? AND record_kind='department_assignment' "
            "AND parent_membership_id=? AND status='active' "
            "AND lifecycle_state='active'",
            (identity.scope_id, identity.membership_id),
        ).fetchall()
        if row["department_id"]
    }


def _validate_share_authority(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    draft: Mapping[str, Any],
) -> None:
    visibility = str(draft.get("visibility") or "private")
    if visibility == "organization" and not identity.is_admin:
        raise RepositoryError(403, "agent_skill_organization_share_forbidden", "仅组织管理员可共享给全员")
    if visibility == "department":
        department_id = str(draft.get("departmentId") or "")
        if department_id not in _lead_department_ids(connection, identity):
            raise RepositoryError(403, "agent_skill_department_share_forbidden", "仅部门负责人可共享给所负责部门")
    membership_ids = set(draft.get("granteeMembershipIds") or [])
    if membership_ids:
        placeholders = ",".join("?" for _ in membership_ids)
        rows = connection.execute(
            f"SELECT id FROM organization_memberships WHERE scope_id=? "
            f"AND id IN ({placeholders}) AND record_kind='membership' "
            "AND status='active' AND lifecycle_state='active'",
            (identity.scope_id, *sorted(membership_ids)),
        ).fetchall()
        if {str(row["id"]) for row in rows} != membership_ids:
            raise RepositoryError(422, "agent_skill_member_invalid", "指定成员不属于当前组织")


def _active_policy(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    skill_id: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM policy_versions WHERE scope_id=? AND secured_resource_id=? "
        "AND policy_spec_schema_version=? AND lifecycle_state='active' "
        "ORDER BY version DESC,created_at DESC,id DESC LIMIT 1",
        (scope_id, skill_id, POLICY_SCHEMA),
    ).fetchone()


def _authorization_for(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    identity: SessionIdentity,
) -> dict[str, Any] | None:
    policy = _active_policy(
        connection,
        scope_id=identity.scope_id,
        skill_id=str(row["id"]),
    )
    if policy is None:
        return None
    action = _json(row["action_spec"], {})
    policy_spec = _json(policy["policy_spec"], {})
    visibility = str(action.get("visibility") or "private")
    if (
        str(policy_spec.get("visibility") or "private") != visibility
        or (policy_spec.get("departmentId") or None) != (action.get("departmentId") or None)
        or sorted(policy_spec.get("granteeMembershipIds") or [])
        != sorted(action.get("granteeMembershipIds") or [])
    ):
        raise RepositoryError(409, "agent_skill_policy_drift", "Skill 权限版本与定义不一致")
    grants = connection.execute(
        "SELECT * FROM object_grants WHERE scope_id=? AND secured_resource_id=? "
        "AND policy_version_id=? AND status='active' AND lifecycle_state='active' "
        "ORDER BY grant_generation DESC,updated_at DESC,id DESC",
        (identity.scope_id, str(row["id"]), str(policy["id"])),
    ).fetchall()
    grants = sorted(
        grants,
        key=lambda grant: 0
        if (
            str(grant["subject_membership_id"] or "") == identity.membership_id
            or str(grant["subject_principal_id"] or "") == identity.principal_id
        )
        else 1,
    )
    member_departments: set[str] | None = None
    for grant in grants:
        capabilities = _json(grant["capability_set"], {})
        if not bool(capabilities.get("read")) or not bool(capabilities.get("use")):
            continue
        direct = (
            str(grant["subject_membership_id"] or "") == identity.membership_id
            or str(grant["subject_principal_id"] or "") == identity.principal_id
        )
        if direct and bool(capabilities.get("manage")):
            direct = (
                str(action.get("publisherPrincipalId") or "") == identity.principal_id
                and str(action.get("publisherMembershipId") or "") == identity.membership_id
            )
        elif direct:
            direct = (
                visibility == "selected_members"
                and identity.membership_id
                in set(policy_spec.get("granteeMembershipIds") or [])
            )
        scope_kind = str(capabilities.get("subjectScopeKind") or "")
        scope_allowed = False
        if not grant["subject_membership_id"] and not grant["subject_principal_id"]:
            if visibility == "organization" and scope_kind == "organization":
                scope_allowed = True
            elif visibility == "department" and scope_kind == "department":
                member_departments = member_departments or _member_department_ids(connection, identity)
                scope_allowed = str(capabilities.get("departmentId") or "") in member_departments
        if not direct and not scope_allowed:
            continue
        can_manage = bool(capabilities.get("manage")) and direct
        return {
            "state": "ready",
            "viewerPrincipalId": identity.principal_id,
            "viewerMembershipId": identity.membership_id,
            "policyVersionId": str(policy["id"]),
            "policyVersion": int(policy["version"] or 1),
            "policySpecSchemaVersion": str(policy["policy_spec_schema_version"] or ""),
            "policySpec": str(policy["policy_spec"] or "{}"),
            "viewerGrantId": str(grant["id"]),
            "viewerGrantVersion": int(grant["version"] or 1),
            "viewerGrantGeneration": int(grant["grant_generation"] or 1),
            "viewerCapabilities": [
                key for key in ("read", "use", "manage") if bool(capabilities.get(key))
            ],
            "viewerSurfaces": ["project_workspace", "smart_editor", "project_reports"],
            "capabilitySetSchemaVersion": str(grant["capability_set_schema_version"] or ""),
            "capabilitySet": str(grant["capability_set"] or "{}"),
            "subjectPrincipalId": grant["subject_principal_id"],
            "subjectMembershipId": grant["subject_membership_id"],
            "canManage": can_manage,
            "generatedAt": utc_now(),
            "leaseExpiresAt": _lease_expires_at(),
        }
    return None


def list_agent_skills(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    agent_kind: str | None = None,
    enabled_only: bool = True,
    skill_type: str = "agent_skill",
) -> dict[str, Any]:
    if agent_kind and agent_kind not in BUILTIN_AGENT_KINDS:
        raise RepositoryError(422, "agent_skill_agent_kind_invalid", "Skill 适用 Agent 无效")
    with repository._connection() as connection:  # noqa: SLF001
        rows = connection.execute(
            "SELECT * FROM automation_rules WHERE scope_id=? AND record_kind='agent_skill' "
            "AND lifecycle_state='active' ORDER BY updated_at DESC,id",
            (identity.scope_id,),
        ).fetchall()
    items = []
    with repository._connection() as connection:  # noqa: SLF001
        for row in rows:
            if enabled_only and not bool(row["enabled"]):
                continue
            authorization = _authorization_for(connection, row, identity)
            if authorization is None:
                continue
            dto = _dto(row, authorization=authorization)
            if dto["skillType"] != skill_type:
                continue
            if agent_kind and agent_kind not in dto["agentKinds"]:
                continue
            items.append(dto)
    return {"state": "ready", "items": items}


def get_agent_skill(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    skill_id: str,
) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        row = connection.execute(
            "SELECT * FROM automation_rules WHERE id=? AND scope_id=? "
            "AND record_kind='agent_skill' AND lifecycle_state='active'",
            (skill_id, identity.scope_id),
        ).fetchone()
        authorization = _authorization_for(connection, row, identity) if row is not None else None
    if row is None or authorization is None:
        raise RepositoryError(404, "agent_skill_missing", "Skill 不存在或当前成员不可见")
    return _dto(row, authorization=authorization)


def _policy_spec(draft: Mapping[str, Any]) -> str:
    return canonical_json(
        {
            "policyKind": "agent_skill_access",
            "defaultDecision": "deny",
            "grantAuthority": "object_grants",
            "visibility": str(draft.get("visibility") or "private"),
            "departmentId": draft.get("departmentId"),
            "granteeMembershipIds": sorted(draft.get("granteeMembershipIds") or []),
        }
    )


def _grant_key(grant: sqlite3.Row) -> str:
    capabilities = _json(grant["capability_set"], {})
    if bool(capabilities.get("manage")):
        return "owner"
    membership_id = str(grant["subject_membership_id"] or "")
    if membership_id:
        return f"member:{membership_id}"
    scope_kind = str(capabilities.get("subjectScopeKind") or "")
    if scope_kind == "organization":
        return "scope:organization"
    if scope_kind == "department":
        return f"scope:department:{capabilities.get('departmentId') or ''}"
    return f"unknown:{grant['id']}"


def _desired_recipient_grants(draft: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    visibility = str(draft.get("visibility") or "private")
    if visibility == "organization":
        return {
            "scope:organization": {
                "subjectPrincipalId": None,
                "subjectMembershipId": None,
                "capabilities": {
                    "read": True,
                    "use": True,
                    "manage": False,
                    "subjectScopeKind": "organization",
                },
            }
        }
    if visibility == "department":
        department_id = str(draft.get("departmentId") or "")
        return {
            f"scope:department:{department_id}": {
                "subjectPrincipalId": None,
                "subjectMembershipId": None,
                "capabilities": {
                    "read": True,
                    "use": True,
                    "manage": False,
                    "subjectScopeKind": "department",
                    "departmentId": department_id,
                },
            }
        }
    if visibility == "selected_members":
        return {
            f"member:{membership_id}": {
                "subjectPrincipalId": None,
                "subjectMembershipId": membership_id,
                "capabilities": {"read": True, "use": True, "manage": False},
            }
            for membership_id in sorted(set(draft.get("granteeMembershipIds") or []))
        }
    return {}


def _next_grant_generation(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    skill_id: str,
    key: str,
) -> int:
    rows = connection.execute(
        "SELECT * FROM object_grants WHERE scope_id=? AND secured_resource_id=?",
        (scope_id, skill_id),
    ).fetchall()
    return max(
        [int(row["grant_generation"] or 0) for row in rows if _grant_key(row) == key],
        default=0,
    ) + 1


def _install_skill_policy(
    connection: sqlite3.Connection,
    identity: SessionIdentity,
    *,
    skill_id: str,
    draft: Mapping[str, Any],
    now: str,
) -> sqlite3.Row:
    desired_spec = _policy_spec(draft)
    current_policy = _active_policy(
        connection,
        scope_id=identity.scope_id,
        skill_id=skill_id,
    )
    if current_policy is not None and str(current_policy["policy_spec"] or "") == desired_spec:
        return current_policy
    policy_version = int(current_policy["version"] or 0) + 1 if current_policy else 1
    if current_policy is not None:
        connection.execute(
            "UPDATE policy_versions SET lifecycle_state='archived',updated_at=? WHERE id=?",
            (now, str(current_policy["id"])),
        )
    policy_id = _record_id(
        "policy",
        skill_id,
        str(policy_version),
        sha256_text(desired_spec),
    )
    connection.execute(
        "INSERT INTO policy_versions (id,scope_id,secured_resource_id,policy_scope_kind,"
        "version,policy_spec_schema_version,policy_spec,effective_at,created_at,"
        "lifecycle_state,updated_at,deleted_at) "
        "VALUES (?,?,?,'secured_resource',?,?,?,?,?,'active',?,NULL)",
        (
            policy_id,
            identity.scope_id,
            skill_id,
            policy_version,
            POLICY_SCHEMA,
            desired_spec,
            now,
            now,
            now,
        ),
    )
    active_grants = connection.execute(
        "SELECT * FROM object_grants WHERE scope_id=? AND secured_resource_id=? "
        "AND status='active' AND lifecycle_state='active' ORDER BY id",
        (identity.scope_id, skill_id),
    ).fetchall()
    owner_grant = next((grant for grant in active_grants if _grant_key(grant) == "owner"), None)
    owner_capabilities = canonical_json({"read": True, "use": True, "manage": True})
    if owner_grant is None:
        connection.execute(
            "INSERT INTO object_grants (id,scope_id,secured_resource_id,policy_version_id,"
            "subject_principal_id,subject_membership_id,capability_set_schema_version,"
            "capability_set,grant_generation,status,created_at,updated_at,revoked_at,"
            "version,lifecycle_state,deleted_at) VALUES (?,?,?,?,?,?,?,?,1,'active',?,?,NULL,1,'active',NULL)",
            (
                _record_id("grant", skill_id, "owner"),
                identity.scope_id,
                skill_id,
                policy_id,
                identity.principal_id,
                identity.membership_id,
                CAPABILITY_SCHEMA,
                owner_capabilities,
                now,
                now,
            ),
        )
    else:
        connection.execute(
            "UPDATE object_grants SET policy_version_id=?,subject_principal_id=?,"
            "subject_membership_id=?,capability_set_schema_version=?,capability_set=?,"
            "updated_at=?,version=version+1 WHERE id=?",
            (
                policy_id,
                identity.principal_id,
                identity.membership_id,
                CAPABILITY_SCHEMA,
                owner_capabilities,
                now,
                str(owner_grant["id"]),
            ),
        )
    active_recipient_by_key = {
        _grant_key(grant): grant
        for grant in active_grants
        if _grant_key(grant) != "owner"
    }
    desired_by_key = _desired_recipient_grants(draft)
    removed = set(active_recipient_by_key) - set(desired_by_key)
    unchanged = set(active_recipient_by_key) & set(desired_by_key)
    added = set(desired_by_key) - set(active_recipient_by_key)
    for key in sorted(removed):
        connection.execute(
            "UPDATE object_grants SET status='revoked',revoked_at=?,updated_at=?,"
            "version=version+1 WHERE id=?",
            (now, now, str(active_recipient_by_key[key]["id"])),
        )
    for key in sorted(unchanged):
        desired = desired_by_key[key]
        connection.execute(
            "UPDATE object_grants SET policy_version_id=?,subject_principal_id=?,"
            "subject_membership_id=?,capability_set_schema_version=?,capability_set=?,"
            "updated_at=?,version=version+1 WHERE id=?",
            (
                policy_id,
                desired["subjectPrincipalId"],
                desired["subjectMembershipId"],
                CAPABILITY_SCHEMA,
                canonical_json(desired["capabilities"]),
                now,
                str(active_recipient_by_key[key]["id"]),
            ),
        )
    for key in sorted(added):
        desired = desired_by_key[key]
        generation = _next_grant_generation(
            connection,
            scope_id=identity.scope_id,
            skill_id=skill_id,
            key=key,
        )
        connection.execute(
            "INSERT INTO object_grants (id,scope_id,secured_resource_id,policy_version_id,"
            "subject_principal_id,subject_membership_id,capability_set_schema_version,"
            "capability_set,grant_generation,status,created_at,updated_at,revoked_at,"
            "version,lifecycle_state,deleted_at) VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,NULL,1,'active',NULL)",
            (
                _record_id("grant", skill_id, key, str(generation)),
                identity.scope_id,
                skill_id,
                policy_id,
                desired["subjectPrincipalId"],
                desired["subjectMembershipId"],
                CAPABILITY_SCHEMA,
                canonical_json(desired["capabilities"]),
                generation,
                now,
                now,
            ),
        )
    return connection.execute("SELECT * FROM policy_versions WHERE id=?", (policy_id,)).fetchone()


def publish_agent_skill(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    draft = _validate_draft(payload)
    normalized = {
        **draft,
        "publisherPrincipalId": identity.principal_id,
        "publisherMembershipId": identity.membership_id,
    }
    payload_hash = payload_fingerprint(normalized)
    command_type = "agent_skill.publish"
    operation_id = _operation_id(identity.scope_id, command_type, idempotency_key)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT aggregate_id,payload_hash FROM commands WHERE scope_id=? "
                "AND idempotency_key=? AND command_type=?",
                (identity.scope_id, idempotency_key, command_type),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"] or "") != payload_hash:
                    raise RepositoryError(409, "idempotency_payload_conflict", "相同操作键对应了不同 Skill 内容")
                row = connection.execute(
                    "SELECT * FROM automation_rules WHERE id=?",
                    (str(existing["aggregate_id"]),),
                ).fetchone()
                if row is None:
                    raise RepositoryError(409, "agent_skill_receipt_missing", "Skill 操作回执不完整")
                authorization = _authorization_for(connection, row, identity)
                if authorization is None:
                    raise RepositoryError(404, "agent_skill_missing", "Skill 不存在或当前成员不可见")
                connection.commit()
                return {**_dto(row, authorization=authorization), "idempotentReplay": True}
            _validate_share_authority(connection, identity, draft)
            duplicate = connection.execute(
                "SELECT id FROM automation_rules WHERE scope_id=? AND record_kind='agent_skill' "
                "AND template_key=? AND lifecycle_state='active'",
                (identity.scope_id, f"{identity.principal_id}:{draft['shortName']}"),
            ).fetchone()
            if duplicate is not None:
                raise RepositoryError(409, "agent_skill_name_exists", "你已创建同名 Skill")
            now = utc_now()
            skill_id = "skill_" + sha256_text(f"{identity.scope_id}\x1f{idempotency_key}")[:30]
            trigger = canonical_json({"schema": TRIGGER_SCHEMA, "agentKinds": draft["agentKinds"]})
            action = canonical_json({"schema": ACTION_SCHEMA, **normalized})
            connection.execute(
                "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,"
                "resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
                "VALUES (?,?,'automation_rule','active',1,'agent_skill',?,?,NULL,'cloud',?)",
                (skill_id, identity.scope_id, now, now, identity.cloud_instance_id),
            )
            connection.execute(
                "INSERT INTO automation_rules (id,scope_id,template_key,rule_version,trigger_spec,record_kind,"
                "trigger_spec_schema_version,action_spec_schema_version,action_spec,trusted_source_pattern,"
                "enabled,effective_at,version,lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,1,?,'agent_skill',?,?,?,NULL,1,?,1,'active',?,?,NULL)",
                (
                    skill_id, identity.scope_id,
                    f"{identity.principal_id}:{draft['shortName']}", trigger,
                    TRIGGER_SCHEMA, ACTION_SCHEMA, action, now, now, now,
                ),
            )
            _install_skill_policy(
                connection,
                identity,
                skill_id=skill_id,
                draft=draft,
                now=now,
            )
            connection.execute(
                "INSERT INTO idempotency_records (id,scope_id,idempotency_key,payload_hash,result_hash,"
                "expires_at,result_object_manifest_id,status,created_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?,NULL,NULL,NULL,'completed',?,'cloud',?)",
                (
                    _record_id("idempotency", operation_id), identity.scope_id,
                    idempotency_key, payload_hash, now, identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,aggregate_type,aggregate_id,"
                "command_type,actor_principal_id,expected_aggregate_version,status,actor_membership_id,"
                "payload_hash,submitted_at,settled_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?, 'agent_skill',?,?,?,NULL,'completed',?,?,?,?,'cloud',?)",
                (
                    _record_id("cmd", operation_id), identity.scope_id, operation_id,
                    idempotency_key, skill_id, command_type, identity.principal_id,
                    identity.membership_id, payload_hash, now, now, identity.cloud_instance_id,
                ),
            )
            event_hash = sha256_text(f"{skill_id}|1|published|{payload_hash}")
            connection.execute(
                "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,event_type,status,"
                "aggregate_type,aggregate_id,event_hash,available_at,published_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,1,'agent_skill.published','pending','agent_skill',?,?,?,NULL,'cloud',?)",
                (
                    _record_id("outbox", operation_id, "published"), identity.scope_id,
                    operation_id, skill_id, event_hash, now, identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (id,scope_id,operation_id,actor_id,action,event_hash,"
                "actor_membership_id,target_resource_id,occurred_at,origin_instance_id,created_at,"
                "integrity_hash,authority_role) VALUES (?,?,?,?, 'agent_skill.publish',?,?,?,?,?,?,?,'cloud')",
                (
                    _record_id("audit", operation_id), identity.scope_id, operation_id,
                    identity.principal_id, event_hash, identity.membership_id, skill_id,
                    now, identity.cloud_instance_id, now,
                    sha256_text(f"{operation_id}|{event_hash}|{now}"),
                ),
            )
            row = connection.execute("SELECT * FROM automation_rules WHERE id=?", (skill_id,)).fetchone()
            authorization = _authorization_for(connection, row, identity)
            if authorization is None:
                raise RepositoryError(409, "agent_skill_owner_grant_missing", "Skill 管理授权未形成")
            connection.commit()
            return {**_dto(row, authorization=authorization), "idempotentReplay": False}
        except Exception:
            connection.rollback()
            raise


def update_agent_skill(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    skill_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    expected_version = int(payload.get("expectedVersion") or 0)
    draft = _validate_draft(payload)
    normalized = {
        **draft,
        "skillId": skill_id,
        "expectedVersion": expected_version,
        "publisherPrincipalId": identity.principal_id,
        "publisherMembershipId": identity.membership_id,
    }
    payload_hash = payload_fingerprint(normalized)
    command_type = "agent_skill.update"
    operation_id = _operation_id(identity.scope_id, command_type, idempotency_key)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT aggregate_id,payload_hash FROM commands WHERE scope_id=? "
                "AND idempotency_key=? AND command_type=?",
                (identity.scope_id, idempotency_key, command_type),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"] or "") != payload_hash:
                    raise RepositoryError(409, "idempotency_payload_conflict", "相同操作键对应了不同 Skill 内容")
                replay = connection.execute(
                    "SELECT * FROM automation_rules WHERE id=?",
                    (str(existing["aggregate_id"]),),
                ).fetchone()
                if replay is None:
                    raise RepositoryError(409, "agent_skill_receipt_missing", "Skill 操作回执不完整")
                authorization = _authorization_for(connection, replay, identity)
                if authorization is None or not authorization.get("canManage"):
                    raise RepositoryError(404, "agent_skill_missing", "Skill 不存在或当前成员不可管理")
                connection.commit()
                return {**_dto(replay, authorization=authorization), "idempotentReplay": True}
            row = connection.execute(
                "SELECT * FROM automation_rules WHERE id=? AND scope_id=? "
                "AND record_kind='agent_skill' AND lifecycle_state='active'",
                (skill_id, identity.scope_id),
            ).fetchone()
            authorization = _authorization_for(connection, row, identity) if row is not None else None
            if row is None or authorization is None or not authorization.get("canManage"):
                raise RepositoryError(404, "agent_skill_missing", "Skill 不存在或当前成员不可管理")
            if int(row["version"] or 1) != expected_version:
                raise RepositoryError(409, "agent_skill_version_conflict", "Skill 已变化，请刷新后重试")
            _validate_share_authority(connection, identity, draft)
            duplicate = connection.execute(
                "SELECT id FROM automation_rules WHERE scope_id=? AND record_kind='agent_skill' "
                "AND template_key=? AND lifecycle_state='active' AND id<>?",
                (identity.scope_id, f"{identity.principal_id}:{draft['shortName']}", skill_id),
            ).fetchone()
            if duplicate is not None:
                raise RepositoryError(409, "agent_skill_name_exists", "你已创建同名 Skill")
            now = utc_now()
            next_version = expected_version + 1
            trigger = canonical_json({"schema": TRIGGER_SCHEMA, "agentKinds": draft["agentKinds"]})
            action = canonical_json({
                "schema": ACTION_SCHEMA,
                **draft,
                "publisherPrincipalId": identity.principal_id,
                "publisherMembershipId": identity.membership_id,
            })
            connection.execute(
                "UPDATE automation_rules SET template_key=?,rule_version=?,trigger_spec=?,"
                "action_spec=?,version=?,updated_at=? WHERE id=?",
                (
                    f"{identity.principal_id}:{draft['shortName']}",
                    next_version,
                    trigger,
                    action,
                    next_version,
                    now,
                    skill_id,
                ),
            )
            connection.execute(
                "UPDATE secured_resources SET version=?,updated_at=? WHERE id=?",
                (next_version, now, skill_id),
            )
            _install_skill_policy(
                connection,
                identity,
                skill_id=skill_id,
                draft=draft,
                now=now,
            )
            connection.execute(
                "INSERT INTO idempotency_records (id,scope_id,idempotency_key,payload_hash,result_hash,"
                "expires_at,result_object_manifest_id,status,created_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?,NULL,NULL,NULL,'completed',?,'cloud',?)",
                (
                    _record_id("idempotency", operation_id), identity.scope_id,
                    idempotency_key, payload_hash, now, identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,aggregate_type,aggregate_id,"
                "command_type,actor_principal_id,expected_aggregate_version,status,actor_membership_id,"
                "payload_hash,submitted_at,settled_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?, 'agent_skill',?,?,?,?, 'completed',?,?,?,?, 'cloud',?)",
                (
                    _record_id("cmd", operation_id), identity.scope_id, operation_id,
                    idempotency_key, skill_id, command_type, identity.principal_id,
                    expected_version, identity.membership_id, payload_hash, now, now,
                    identity.cloud_instance_id,
                ),
            )
            event_hash = sha256_text(f"{skill_id}|{next_version}|updated|{payload_hash}")
            connection.execute(
                "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,event_type,status,"
                "aggregate_type,aggregate_id,event_hash,available_at,published_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?,'agent_skill.updated','pending','agent_skill',?,?,?,NULL,'cloud',?)",
                (
                    _record_id("outbox", operation_id, "updated"), identity.scope_id,
                    operation_id, next_version, skill_id, event_hash, now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (id,scope_id,operation_id,actor_id,action,event_hash,"
                "actor_membership_id,target_resource_id,occurred_at,origin_instance_id,created_at,"
                "integrity_hash,authority_role) VALUES (?,?,?,?, 'agent_skill.update',?,?,?,?,?,?,?,'cloud')",
                (
                    _record_id("audit", operation_id), identity.scope_id, operation_id,
                    identity.principal_id, event_hash, identity.membership_id, skill_id,
                    now, identity.cloud_instance_id, now,
                    sha256_text(f"{operation_id}|{event_hash}|{now}"),
                ),
            )
            updated = connection.execute(
                "SELECT * FROM automation_rules WHERE id=?", (skill_id,)
            ).fetchone()
            updated_authorization = _authorization_for(connection, updated, identity)
            if updated_authorization is None or not updated_authorization.get("canManage"):
                raise RepositoryError(409, "agent_skill_owner_grant_missing", "Skill 管理授权未形成")
            connection.commit()
            return {**_dto(updated, authorization=updated_authorization), "idempotentReplay": False}
        except Exception:
            connection.rollback()
            raise


def set_agent_skill_enabled(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    skill_id: str,
    enabled: bool,
    expected_version: int,
    idempotency_key: str,
) -> dict[str, Any]:
    command_type = "agent_skill.set_enabled"
    normalized = {
        "skillId": skill_id,
        "enabled": bool(enabled),
        "expectedVersion": int(expected_version),
    }
    payload_hash = payload_fingerprint(normalized)
    operation_id = _operation_id(identity.scope_id, command_type, idempotency_key)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT aggregate_id,payload_hash FROM commands WHERE scope_id=? "
                "AND idempotency_key=? AND command_type=?",
                (identity.scope_id, idempotency_key, command_type),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"] or "") != payload_hash:
                    raise RepositoryError(409, "idempotency_payload_conflict", "相同操作键对应了不同 Skill 状态")
                replay = connection.execute(
                    "SELECT * FROM automation_rules WHERE id=?",
                    (str(existing["aggregate_id"]),),
                ).fetchone()
                if replay is None:
                    raise RepositoryError(409, "agent_skill_receipt_missing", "Skill 操作回执不完整")
                authorization = _authorization_for(connection, replay, identity)
                if authorization is None or not authorization.get("canManage"):
                    raise RepositoryError(404, "agent_skill_missing", "Skill 不存在或当前成员不可管理")
                connection.commit()
                return {**_dto(replay, authorization=authorization), "idempotentReplay": True}
            row = connection.execute(
                "SELECT * FROM automation_rules WHERE id=? AND scope_id=? "
                "AND record_kind='agent_skill' AND lifecycle_state='active'",
                (skill_id, identity.scope_id),
            ).fetchone()
            authorization = _authorization_for(connection, row, identity) if row is not None else None
            if row is None or authorization is None or not authorization.get("canManage"):
                raise RepositoryError(404, "agent_skill_missing", "Skill 不存在或当前成员不可管理")
            if int(row["version"] or 1) != expected_version:
                raise RepositoryError(409, "agent_skill_version_conflict", "Skill 已变化，请刷新后重试")
            if bool(row["enabled"]) == enabled:
                connection.commit()
                return {**_dto(row, authorization=authorization), "idempotentReplay": True}
            now = utc_now()
            next_version = expected_version + 1
            connection.execute(
                "UPDATE automation_rules SET enabled=?,version=?,rule_version=?,updated_at=? WHERE id=?",
                (1 if enabled else 0, next_version, next_version, now, skill_id),
            )
            connection.execute(
                "UPDATE secured_resources SET version=?,updated_at=? WHERE id=?",
                (next_version, now, skill_id),
            )
            connection.execute(
                "INSERT INTO idempotency_records (id,scope_id,idempotency_key,payload_hash,result_hash,"
                "expires_at,result_object_manifest_id,status,created_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?,NULL,NULL,NULL,'completed',?,'cloud',?)",
                (
                    _record_id("idempotency", operation_id), identity.scope_id,
                    idempotency_key, payload_hash, now, identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,aggregate_type,aggregate_id,"
                "command_type,actor_principal_id,expected_aggregate_version,status,actor_membership_id,"
                "payload_hash,submitted_at,settled_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?, 'agent_skill',?,?,?,?, 'completed',?,?,?,?, 'cloud',?)",
                (
                    _record_id("cmd", operation_id), identity.scope_id, operation_id,
                    idempotency_key, skill_id, command_type, identity.principal_id,
                    expected_version, identity.membership_id, payload_hash, now, now,
                    identity.cloud_instance_id,
                ),
            )
            event_hash = sha256_text(
                f"{skill_id}|{next_version}|enabled={int(enabled)}|{payload_hash}"
            )
            connection.execute(
                "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,event_type,status,"
                "aggregate_type,aggregate_id,event_hash,available_at,published_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?,'agent_skill.enabled_changed','pending','agent_skill',?,?,?,NULL,'cloud',?)",
                (
                    _record_id("outbox", operation_id, "enabled"), identity.scope_id,
                    operation_id, next_version, skill_id, event_hash, now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events (id,scope_id,operation_id,actor_id,action,event_hash,"
                "actor_membership_id,target_resource_id,occurred_at,origin_instance_id,created_at,"
                "integrity_hash,authority_role) VALUES (?,?,?,?, 'agent_skill.set_enabled',?,?,?,?,?,?,?,'cloud')",
                (
                    _record_id("audit", operation_id), identity.scope_id, operation_id,
                    identity.principal_id, event_hash, identity.membership_id, skill_id,
                    now, identity.cloud_instance_id, now,
                    sha256_text(f"{operation_id}|{event_hash}|{now}"),
                ),
            )
            updated = connection.execute("SELECT * FROM automation_rules WHERE id=?", (skill_id,)).fetchone()
            updated_authorization = _authorization_for(connection, updated, identity)
            if updated_authorization is None or not updated_authorization.get("canManage"):
                raise RepositoryError(409, "agent_skill_owner_grant_missing", "Skill 管理授权未形成")
            connection.commit()
            return {**_dto(updated, authorization=updated_authorization), "idempotentReplay": False}
        except Exception:
            connection.rollback()
            raise


def record_agent_skill_run(
    repository: CloudRepository,
    identity: SessionIdentity,
    *,
    skill_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Record one real declarative Skill execution without storing prompts/content."""

    agent_kind = str(payload.get("agentKind") or "project_workspace").strip()
    if agent_kind not in BUILTIN_AGENT_KINDS:
        raise RepositoryError(422, "agent_skill_agent_kind_invalid", "Skill 适用 Agent 无效")
    input_hash = str(payload.get("inputHash") or "").strip().lower()
    result_hash = str(payload.get("resultHash") or "").strip().lower()
    if any(
        len(value) != 64 or any(character not in "0123456789abcdef" for character in value)
        for value in (input_hash, result_hash)
    ):
        raise RepositoryError(422, "agent_skill_run_hash_invalid", "Skill 运行哈希无效")
    try:
        source_count = max(0, int(payload.get("sourceCount") or 0))
    except (TypeError, ValueError):
        raise RepositoryError(422, "agent_skill_source_count_invalid", "Skill 来源数量无效")
    command_type = "agent_skill.run.recorded"
    normalized = {
        "skillId": skill_id,
        "agentKind": agent_kind,
        "inputHash": input_hash,
        "resultHash": result_hash,
        "sourceCount": source_count,
    }
    payload_hash = payload_fingerprint(normalized)
    operation_id = _operation_id(identity.scope_id, command_type, idempotency_key)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            existing = connection.execute(
                "SELECT aggregate_id,payload_hash FROM commands WHERE scope_id=? "
                "AND idempotency_key=? AND command_type=?",
                (identity.scope_id, idempotency_key, command_type),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"] or "") != payload_hash:
                    raise RepositoryError(
                        409,
                        "idempotency_payload_conflict",
                        "相同操作键对应了不同 Skill 运行",
                    )
                run = connection.execute(
                    "SELECT * FROM execution_runs WHERE id=? AND scope_id=?",
                    (str(existing["aggregate_id"]), identity.scope_id),
                ).fetchone()
                if run is None:
                    raise RepositoryError(409, "agent_skill_run_receipt_missing", "Skill 运行回执不完整")
                connection.commit()
                return {
                    "runId": str(run["id"]),
                    "skillId": str(run["rule_id"]),
                    "botId": str(run["bot_id"]),
                    "agentKind": agent_kind,
                    "status": str(run["status"]),
                    "sourceCount": source_count,
                    "startedAt": str(run["started_at"] or ""),
                    "finishedAt": str(run["finished_at"] or ""),
                    "idempotentReplay": True,
                }
            skill = connection.execute(
                "SELECT * FROM automation_rules WHERE id=? AND scope_id=? "
                "AND record_kind='agent_skill' AND lifecycle_state='active'",
                (skill_id, identity.scope_id),
            ).fetchone()
            authorization = (
                _authorization_for(connection, skill, identity)
                if skill is not None
                else None
            )
            if skill is None or authorization is None:
                raise RepositoryError(404, "agent_skill_missing", "Skill 不存在或当前成员不可使用")
            dto = _dto(skill, authorization=authorization)
            if not bool(skill["enabled"]):
                raise RepositoryError(409, "agent_skill_disabled", "Skill 已停用")
            if agent_kind not in dto["agentKinds"]:
                raise RepositoryError(409, "agent_skill_not_applicable", "Skill 不适用于当前 Agent")
            expected_bot_id = builtin_agent_id(identity.organization_id, agent_kind)
            bot = connection.execute(
                "SELECT bot.id FROM bot_definitions AS bot "
                "JOIN authorization_scopes AS agent_scope ON agent_scope.id=bot.scope_id "
                "WHERE bot.id=? AND bot.agent_kind=? AND bot.enabled=1 "
                "AND bot.lifecycle_state='active' AND agent_scope.organization_id=? "
                "AND agent_scope.status='active' AND agent_scope.lifecycle_state='active'",
                (expected_bot_id, agent_kind, identity.organization_id),
            ).fetchone()
            if bot is None:
                raise RepositoryError(409, "agent_kind_not_connected", "对应内置 Agent 尚未接通")
            now = utc_now()
            receipt = canonical_json(
                {
                    "skillId": skill_id,
                    "skillVersion": int(skill["version"] or 1),
                    "shortName": dto["shortName"],
                    "agentKind": agent_kind,
                    "inputHash": input_hash,
                    "resultHash": result_hash,
                    "sourceCount": source_count,
                    "materialBoundary": {
                        "promptStored": False,
                        "resultContentStored": False,
                        "localPathStored": False,
                    },
                }
            )
            manifest_id = _record_id("manifest", operation_id, "skill-run")
            connection.execute(
                "INSERT INTO object_manifests "
                "(id,scope_id,storage_key,content_hash,lifecycle_state,receipt,holder_role,"
                "holder_instance_id,storage_kind,byte_size,media_type,availability_state,"
                "receipt_hash,created_at,verified_at,deleted_at,authority_role,origin_instance_id) "
                "VALUES (?,?,NULL,?,'active',?,'cloud_skill_run',?,'metadata_receipt',?,"
                "'application/vnd.yiyu.agent-skill-run+json','ready',?,?,?,NULL,'cloud',?)",
                (
                    manifest_id,
                    identity.scope_id,
                    result_hash,
                    receipt,
                    identity.cloud_instance_id,
                    len(receipt.encode("utf-8")),
                    sha256_text(receipt),
                    now,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            run_id = _record_id("run", operation_id, skill_id)
            connection.execute(
                "INSERT INTO idempotency_records "
                "(id,scope_id,idempotency_key,payload_hash,result_hash,expires_at,"
                "result_object_manifest_id,status,created_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?,?,NULL,?,'settled',?,'cloud',?)",
                (
                    _record_id("idem", operation_id, command_type),
                    identity.scope_id,
                    idempotency_key,
                    payload_hash,
                    result_hash,
                    manifest_id,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO commands "
                "(id,scope_id,operation_id,idempotency_key,aggregate_type,aggregate_id,"
                "command_type,actor_principal_id,expected_aggregate_version,device_command_sequence,"
                "status,actor_membership_id,payload_object_manifest_id,payload_hash,submitted_at,"
                "settled_at,authority_role,origin_instance_id) "
                "VALUES (?,?,?,?, 'execution_run',?,?,?,NULL,NULL,'settled',?,?,?,?,?,'cloud',?)",
                (
                    _record_id("cmd", operation_id, command_type),
                    identity.scope_id,
                    operation_id,
                    idempotency_key,
                    run_id,
                    command_type,
                    identity.principal_id,
                    identity.membership_id,
                    manifest_id,
                    payload_hash,
                    now,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO execution_runs "
                "(id,scope_id,bot_id,rule_id,task_id,operation_id,status,"
                "initiator_membership_id,proposal_id,run_kind,progress_object_manifest_id,"
                "result_object_manifest_id,started_at,finished_at,version,lifecycle_state,"
                "created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,?,NULL,?,'completed',?,NULL,'agent_skill_application',NULL,?,"
                "?,?,1,'active',?,?,NULL)",
                (
                    run_id,
                    identity.scope_id,
                    str(bot["id"]),
                    skill_id,
                    operation_id,
                    identity.membership_id,
                    manifest_id,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            event_hash = sha256_text(f"{run_id}|{skill_id}|{result_hash}")
            connection.execute(
                "INSERT INTO outbox_events "
                "(id,scope_id,operation_id,aggregate_version,event_type,status,aggregate_type,"
                "aggregate_id,event_object_manifest_id,event_hash,available_at,published_at,"
                "authority_role,origin_instance_id) "
                "VALUES (?,?,?,1,'agent_skill.run.completed','published','execution_run',?,?,?,?,?,"
                "'cloud',?)",
                (
                    _record_id("outbox", operation_id, "completed"),
                    identity.scope_id,
                    operation_id,
                    run_id,
                    manifest_id,
                    event_hash,
                    now,
                    now,
                    identity.cloud_instance_id,
                ),
            )
            connection.execute(
                "INSERT INTO audit_events "
                "(id,scope_id,operation_id,actor_id,action,event_hash,actor_membership_id,"
                "target_resource_id,details_object_manifest_id,occurred_at,origin_instance_id,"
                "created_at,integrity_hash,authority_role) "
                "VALUES (?,?,?,?, 'agent_skill.run',?,?,?,?,?,?,?,?,'cloud')",
                (
                    _record_id("audit", operation_id, "run"),
                    identity.scope_id,
                    operation_id,
                    identity.principal_id,
                    event_hash,
                    identity.membership_id,
                    skill_id,
                    manifest_id,
                    now,
                    identity.cloud_instance_id,
                    now,
                    event_hash,
                ),
            )
            connection.commit()
            return {
                "runId": run_id,
                "skillId": skill_id,
                "shortName": dto["shortName"],
                "botId": str(bot["id"]),
                "agentKind": agent_kind,
                "status": "completed",
                "sourceCount": source_count,
                "startedAt": now,
                "finishedAt": now,
                "idempotentReplay": False,
            }
        except Exception:
            connection.rollback()
            raise
