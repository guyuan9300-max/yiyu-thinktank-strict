from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from strict_common.ids import canonical_json, sha256_text, utc_now


ROOT = Path(__file__).resolve().parents[3]
POLICY_CONTRACT_PATH = ROOT / "contracts" / "gc01-authorization-policy.v1.json"


class AuthorizationProjectionError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _load_policy_contract() -> dict[str, Any]:
    raw = json.loads(POLICY_CONTRACT_PATH.read_text(encoding="utf-8"))
    profiles = raw.get("profiles")
    baseline = raw.get("baselineProfile")
    if (
        raw.get("status") != "active"
        or not isinstance(profiles, dict)
        or baseline not in profiles
        or raw.get("authorizationMode") != "online_revalidation"
        or raw.get("leaseEnforcement") != "diagnostic_only"
        or int(raw.get("leaseHours") or 0) != 24
    ):
        raise RuntimeError("GC-01 authorization policy contract is invalid")
    for profile in profiles.values():
        if not isinstance(profile, dict):
            raise RuntimeError("GC-01 authorization profile is invalid")
        for key in ("surfaces", "capabilities"):
            values = profile.get(key)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) or not value for value in values)
                or len(values) != len(set(values))
            ):
                raise RuntimeError(f"GC-01 authorization {key} is invalid")
    return raw


POLICY_CONTRACT = _load_policy_contract()
AUTHORIZATION_LEASE = timedelta(hours=int(POLICY_CONTRACT["leaseHours"]))


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256_text("\x1f".join(parts))
    return f"{prefix}_{digest[:30]}"


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _expires_at(delta: timedelta) -> str:
    return (
        (datetime.now(timezone.utc) + delta)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _profile(role_key: str) -> tuple[str, dict[str, Any]]:
    profiles = POLICY_CONTRACT["profiles"]
    profile_key = role_key if role_key in profiles else POLICY_CONTRACT["baselineProfile"]
    return str(profile_key), dict(profiles[profile_key])


def _policy_spec(role_key: str) -> tuple[str, list[str], list[str]]:
    profile_key, profile = _profile(role_key)
    surfaces = list(profile["surfaces"])
    capabilities = list(profile["capabilities"])
    return (
        canonical_json(
            {
                "roleKey": role_key,
                "profileKey": profile_key,
                "surfaces": surfaces,
                "capabilities": capabilities,
            }
        ),
        surfaces,
        capabilities,
    )


def _ensure_policy(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    role_key: str,
    policy_version: int,
    now: str,
) -> tuple[str, list[str], list[str], bool]:
    policy_id = _stable_id(
        "policy",
        POLICY_CONTRACT["policyId"],
        scope_id,
        role_key,
        str(policy_version),
    )
    spec, surfaces, capabilities = _policy_spec(role_key)
    existing = connection.execute(
        "SELECT * FROM policy_versions WHERE id=?",
        (policy_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO policy_versions (
                id, scope_id, secured_resource_id, policy_scope_kind, version,
                policy_spec_schema_version, policy_spec, effective_at,
                created_at, lifecycle_state, updated_at, deleted_at
            ) VALUES (?, ?, NULL, 'organization_role', ?, ?, ?, ?, ?,
                      'active', ?, NULL)
            """,
            (
                policy_id,
                scope_id,
                policy_version,
                POLICY_CONTRACT["policySpecSchemaVersion"],
                spec,
                now,
                now,
                now,
            ),
        )
        return policy_id, surfaces, capabilities, True
    expected = {
        "scope_id": scope_id,
        "secured_resource_id": None,
        "policy_scope_kind": "organization_role",
        "version": policy_version,
        "policy_spec_schema_version": POLICY_CONTRACT["policySpecSchemaVersion"],
        "policy_spec": spec,
        "lifecycle_state": "active",
        "deleted_at": None,
    }
    if any(existing[key] != value for key, value in expected.items()):
        raise AuthorizationProjectionError(
            409,
            "authorization_policy_version_drift",
            "权限版本内容与冻结合同不一致，请先提升权限版本",
        )
    return policy_id, surfaces, capabilities, False


def _upsert_viewer_projection(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    principal_id: str,
    membership_id: str,
    membership_version: int,
    policy_id: str,
    surfaces: list[str],
    capabilities: list[str],
    now: str,
    force_renew_lease: bool = False,
) -> tuple[str, str]:
    projection_id = _stable_id("viewer", scope_id, membership_id, policy_id)
    surfaces_json = canonical_json(surfaces)
    capabilities_json = canonical_json(capabilities)
    lease_expires_at = _expires_at(AUTHORIZATION_LEASE)
    existing = connection.execute(
        "SELECT * FROM viewer_projections WHERE id=?",
        (projection_id,),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO viewer_projections (
                id, scope_id, secured_resource_id, viewer_principal_id,
                viewer_membership_id, policy_version_id, viewer_surfaces,
                viewer_capabilities, viewer_surfaces_schema_version,
                viewer_capabilities_schema_version, lease_expires_at,
                generated_at, source_version, invalidated_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                projection_id,
                scope_id,
                principal_id,
                membership_id,
                policy_id,
                surfaces_json,
                capabilities_json,
                POLICY_CONTRACT["viewerSurfacesSchemaVersion"],
                POLICY_CONTRACT["viewerCapabilitiesSchemaVersion"],
                lease_expires_at,
                now,
                membership_version,
            ),
        )
        state = "created"
    else:
        unchanged = (
            not force_renew_lease
            and existing["scope_id"] == scope_id
            and existing["secured_resource_id"] is None
            and existing["viewer_principal_id"] == principal_id
            and existing["viewer_membership_id"] == membership_id
            and existing["policy_version_id"] == policy_id
            and existing["viewer_surfaces"] == surfaces_json
            and existing["viewer_capabilities"] == capabilities_json
            and existing["viewer_surfaces_schema_version"]
            == POLICY_CONTRACT["viewerSurfacesSchemaVersion"]
            and existing["viewer_capabilities_schema_version"]
            == POLICY_CONTRACT["viewerCapabilitiesSchemaVersion"]
            and int(existing["source_version"] or 0) == membership_version
            and existing["invalidated_at"] is None
            and bool(existing["lease_expires_at"])
            and _parse_time(str(existing["lease_expires_at"]))
            > datetime.now(timezone.utc)
        )
        if unchanged:
            state = "unchanged"
        else:
            cursor = connection.execute(
                """
                UPDATE viewer_projections SET viewer_principal_id=?,
                    policy_version_id=?, viewer_surfaces=?, viewer_capabilities=?,
                    viewer_surfaces_schema_version=?,
                    viewer_capabilities_schema_version=?, lease_expires_at=?,
                    generated_at=?, source_version=?, invalidated_at=NULL
                WHERE id=? AND source_version IS ? AND generated_at IS ?
                """,
                (
                    principal_id,
                    policy_id,
                    surfaces_json,
                    capabilities_json,
                    POLICY_CONTRACT["viewerSurfacesSchemaVersion"],
                    POLICY_CONTRACT["viewerCapabilitiesSchemaVersion"],
                    lease_expires_at,
                    now,
                    membership_version,
                    projection_id,
                    existing["source_version"],
                    existing["generated_at"],
                ),
            )
            if cursor.rowcount != 1:
                raise AuthorizationProjectionError(
                    409,
                    "authorization_projection_conflict",
                    "权限投影已被更新，请重试",
                )
            state = "renewed"
    invalidated = connection.execute(
        """
        UPDATE viewer_projections SET invalidated_at=?
        WHERE scope_id=? AND viewer_membership_id=? AND id!=?
          AND invalidated_at IS NULL
        """,
        (now, scope_id, membership_id, projection_id),
    ).rowcount
    return projection_id, f"{state}:{invalidated}"


def renew_authorization_projection_for_session(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    membership_id: str,
    now: str,
) -> dict[str, Any]:
    """Renew one session viewer projection inside the caller's transaction."""
    source = connection.execute(
        """
        SELECT membership.principal_id, membership.role_key,
               membership.status, membership.version,
               membership.lifecycle_state, scope.policy_version,
               scope.status AS scope_status,
               scope.lifecycle_state AS scope_lifecycle_state
        FROM organization_memberships AS membership
        JOIN authorization_scopes AS scope ON scope.id=membership.scope_id
        WHERE membership.id=? AND membership.scope_id=?
          AND membership.record_kind='membership'
        """,
        (membership_id, scope_id),
    ).fetchone()
    if source is None:
        raise AuthorizationProjectionError(
            403,
            "authorization_scope_mismatch",
            "当前会话不属于该组织授权范围",
        )
    if (
        source["scope_status"] != "active"
        or source["scope_lifecycle_state"] != "active"
        or source["status"] != "active"
        or source["lifecycle_state"] != "active"
        or not source["principal_id"]
    ):
        raise AuthorizationProjectionError(
            403,
            "authorization_blocked",
            "当前组织身份已被停用",
        )
    role_key = str(source["role_key"] or "member")
    policy_version = int(source["policy_version"] or 0)
    if policy_version < 1:
        raise AuthorizationProjectionError(
            409,
            "authorization_policy_version_missing",
            "授权根缺少有效权限版本",
        )
    policy_id, surfaces, capabilities, _ = _ensure_policy(
        connection,
        scope_id=scope_id,
        role_key=role_key,
        policy_version=policy_version,
        now=now,
    )
    projection_id, state = _upsert_viewer_projection(
        connection,
        scope_id=scope_id,
        principal_id=str(source["principal_id"]),
        membership_id=membership_id,
        membership_version=int(source["version"] or 1),
        policy_id=policy_id,
        surfaces=surfaces,
        capabilities=capabilities,
        now=now,
        force_renew_lease=True,
    )
    row = connection.execute(
        "SELECT lease_expires_at FROM viewer_projections WHERE id=?",
        (projection_id,),
    ).fetchone()
    return {
        "projectionId": projection_id,
        "state": state.split(":", 1)[0],
        "leaseExpiresAt": row["lease_expires_at"] if row else None,
    }


def backfill_authorization_projections(
    connection: sqlite3.Connection,
    *,
    origin_instance_id: str,
) -> dict[str, int]:
    now = utc_now()
    counts = {
        "scopes": 0,
        "activeMemberships": 0,
        "policiesCreated": 0,
        "projectionsCreated": 0,
        "projectionsRenewed": 0,
        "projectionsInvalidated": 0,
        "reconciliationRunsCreated": 0,
    }
    connection.execute("BEGIN IMMEDIATE")
    try:
        scopes = connection.execute(
            """
            SELECT scope.id, scope.policy_version
            FROM authorization_scopes AS scope
            JOIN organizations AS organization
              ON organization.id=scope.organization_id
            WHERE scope.scope_kind='organization' AND scope.status='active'
              AND scope.lifecycle_state='active'
              AND organization.record_kind='organization'
              AND organization.lifecycle_state='active'
            ORDER BY scope.id
            """
        ).fetchall()
        for scope in scopes:
            scope_id = str(scope["id"])
            policy_version = int(scope["policy_version"] or 0)
            if policy_version < 1:
                raise AuthorizationProjectionError(
                    409,
                    "authorization_policy_version_missing",
                    "授权根缺少有效权限版本",
                )
            counts["scopes"] += 1
            memberships = connection.execute(
                """
                SELECT id, principal_id, role_key, status, version,
                       lifecycle_state
                FROM organization_memberships
                WHERE scope_id=? AND record_kind='membership'
                ORDER BY id
                """,
                (scope_id,),
            ).fetchall()
            source_fingerprint = sha256_text(
                canonical_json(
                    {
                        "scopeId": scope_id,
                        "policyVersion": policy_version,
                        "memberships": [dict(row) for row in memberships],
                    }
                )
            )
            scope_changes = 0
            for membership in memberships:
                membership_id = str(membership["id"])
                active = (
                    membership["status"] == "active"
                    and membership["lifecycle_state"] == "active"
                    and bool(membership["principal_id"])
                )
                if not active:
                    invalidated = connection.execute(
                        """
                        UPDATE viewer_projections SET invalidated_at=?
                        WHERE scope_id=? AND viewer_membership_id=?
                          AND invalidated_at IS NULL
                        """,
                        (now, scope_id, membership_id),
                    ).rowcount
                    counts["projectionsInvalidated"] += invalidated
                    scope_changes += invalidated
                    continue
                counts["activeMemberships"] += 1
                role_key = str(membership["role_key"] or "member")
                policy_id, surfaces, capabilities, policy_created = _ensure_policy(
                    connection,
                    scope_id=scope_id,
                    role_key=role_key,
                    policy_version=policy_version,
                    now=now,
                )
                if policy_created:
                    counts["policiesCreated"] += 1
                    scope_changes += 1
                _, state = _upsert_viewer_projection(
                    connection,
                    scope_id=scope_id,
                    principal_id=str(membership["principal_id"]),
                    membership_id=membership_id,
                    membership_version=int(membership["version"] or 1),
                    policy_id=policy_id,
                    surfaces=surfaces,
                    capabilities=capabilities,
                    now=now,
                )
                projection_state, invalidated_text = state.split(":", 1)
                invalidated = int(invalidated_text)
                counts["projectionsInvalidated"] += invalidated
                scope_changes += invalidated
                if projection_state == "created":
                    counts["projectionsCreated"] += 1
                    scope_changes += 1
                elif projection_state == "renewed":
                    counts["projectionsRenewed"] += 1
                    scope_changes += 1
            run_id = _stable_id(
                "recon",
                "gc01.authorization_projection.backfill.v1",
                scope_id,
                source_fingerprint,
            )
            if connection.execute(
                "SELECT 1 FROM reconciliation_runs WHERE id=?",
                (run_id,),
            ).fetchone() is None:
                connection.execute(
                    """
                    INSERT INTO reconciliation_runs (
                        id, scope_id, operation_id, registry_state_id,
                        mismatch_count, status, reconciliation_kind,
                        target_instance_id, result_object_manifest_id,
                        started_at, completed_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, NULL, NULL, ?, 'completed',
                              'gc01_authorization_projection_backfill_v1', ?,
                              NULL, ?, ?, 1, 'active', ?, ?, NULL, 'cloud', ?)
                    """,
                    (
                        run_id,
                        scope_id,
                        scope_changes,
                        origin_instance_id,
                        now,
                        now,
                        now,
                        now,
                        origin_instance_id,
                    ),
                )
                counts["reconciliationRunsCreated"] += 1
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    return counts


def read_authorization_projection(
    connection: sqlite3.Connection,
    *,
    scope_id: str,
    organization_id: str,
    principal_id: str,
    membership_id: str,
) -> dict[str, Any]:
    source = connection.execute(
        """
        SELECT scope.policy_version, membership.role_key,
               membership.visibility_scope, membership.version AS membership_version,
               membership.status AS membership_status,
               membership.lifecycle_state AS membership_lifecycle_state,
               scope.status AS scope_status,
               scope.lifecycle_state AS scope_lifecycle_state
        FROM authorization_scopes AS scope
        JOIN organization_memberships AS membership
          ON membership.scope_id=scope.id
        WHERE scope.id=? AND scope.organization_id=?
          AND membership.id=? AND membership.principal_id=?
          AND membership.record_kind='membership'
        """,
        (scope_id, organization_id, membership_id, principal_id),
    ).fetchone()
    if source is None:
        raise AuthorizationProjectionError(
            403,
            "authorization_scope_mismatch",
            "当前身份不属于该组织授权范围",
        )
    if (
        source["scope_status"] != "active"
        or source["scope_lifecycle_state"] != "active"
        or source["membership_status"] != "active"
        or source["membership_lifecycle_state"] != "active"
    ):
        raise AuthorizationProjectionError(
            403,
            "authorization_blocked",
            "当前组织身份已被停用",
        )
    rows = connection.execute(
        """
        SELECT projection.*, policy.version AS policy_version,
               policy.policy_spec
        FROM viewer_projections AS projection
        JOIN policy_versions AS policy ON policy.id=projection.policy_version_id
        WHERE projection.scope_id=?
          AND projection.viewer_principal_id=?
          AND projection.viewer_membership_id=?
          AND projection.invalidated_at IS NULL
          AND policy.scope_id=? AND policy.lifecycle_state='active'
          AND policy.version=?
        ORDER BY projection.generated_at DESC, projection.id DESC
        """,
        (
            scope_id,
            principal_id,
            membership_id,
            scope_id,
            int(source["policy_version"] or 0),
        ),
    ).fetchall()
    if len(rows) != 1:
        raise AuthorizationProjectionError(
            503,
            "authorization_projection_missing",
            "权限投影尚未生成，请联系管理员重建",
        )
    row = rows[0]
    try:
        surfaces = json.loads(str(row["viewer_surfaces"]))
        capabilities = json.loads(str(row["viewer_capabilities"]))
        policy_spec = json.loads(str(row["policy_spec"]))
    except (TypeError, ValueError) as exc:
        raise AuthorizationProjectionError(
            500,
            "authorization_projection_invalid",
            "权限投影内容损坏",
        ) from exc
    role_key = str(source["role_key"] or "member")
    if (
        not isinstance(surfaces, list)
        or not isinstance(capabilities, list)
        or not isinstance(policy_spec, dict)
        or policy_spec.get("roleKey") != role_key
        or int(row["source_version"] or 0)
        != int(source["membership_version"] or 0)
    ):
        raise AuthorizationProjectionError(
            409,
            "authorization_projection_stale",
            "权限投影已过期，请重新同步",
        )
    # The cloud request itself has already authenticated the live server
    # session and joined the current principal/membership/scope rows above.
    # `lease_expires_at` is retained for old-client compatibility and audit
    # freshness only; it must not become a second, time-based authorization
    # authority that can block an otherwise active organization member.
    lease_expires_at = str(row["lease_expires_at"] or "") or None
    return {
        "state": "ready",
        "freshness": "current",
        "reasonCode": None,
        "retryable": False,
        "principalId": principal_id,
        "membershipId": membership_id,
        "organizationId": organization_id,
        "scopeId": scope_id,
        "systemRole": role_key,
        "visibilityScope": str(source["visibility_scope"] or "organization"),
        "policyVersion": int(row["policy_version"]),
        "policyVersionId": str(row["policy_version_id"]),
        "projectionId": str(row["id"]),
        "surfaces": surfaces,
        "capabilities": capabilities,
        "generatedAt": row["generated_at"],
        "leaseExpiresAt": lease_expires_at,
        "sourceVersion": int(row["source_version"]),
    }
