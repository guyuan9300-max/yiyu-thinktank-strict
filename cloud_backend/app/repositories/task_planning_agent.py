"""Task-planning Agent keyword profiles using only the frozen 88-table graph."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from strict_common.agent_memory import AgentRunReceipt, builtin_agent_id
from strict_common.ids import canonical_json, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .project_materials import GC07ProjectMaterialsRepository


PROFILE_SCHEMA = "yiyu.task-planning.project-keyword-profile.v2"

_PLACEHOLDER_MARKERS = (
    "等待导入",
    "系统将自动",
    "暂无资料",
    "尚未上传",
    "未接通",
)
_NAME_SUFFIXES = (
    "公益基金会",
    "基金会",
    "社会服务中心",
    "研究院",
    "实验室",
    "智库",
    "中心",
    "集团",
    "公司",
)


def _stable_id(prefix: str, *parts: Any) -> str:
    return f"{prefix}_{sha256_text(chr(31).join(str(part) for part in parts))[:30]}"


def _keywords(client: Mapping[str, Any], supplied: list[Any] | None = None) -> list[str]:
    values = [
        str(client.get("name") or ""),
        str(client.get("alias") or ""),
        str(client.get("domain") or ""),
        str(client.get("summary") or ""),
        *[str(item or "") for item in supplied or []],
    ]
    result: list[str] = []

    def add(candidate: str) -> None:
        normalized = " ".join(candidate.split()).strip(" ._-/，。；：、（）()[]【】")
        if (
            len(normalized) < 2
            or len(normalized) > 32
            or any(marker in normalized for marker in _PLACEHOLDER_MARKERS)
            or normalized in result
        ):
            return
        result.append(normalized)

    for value in values:
        normalized = " ".join(value.split())
        if not normalized or any(marker in normalized for marker in _PLACEHOLDER_MARKERS):
            continue
        # Keep meaningful phrases and explicit list items.  Arbitrary Chinese
        # n-grams ("慈基", "金会") are not safe search keywords.
        for phrase in re.split(r"[\n,，。；;：:、|/（）()【】\[\]]+", normalized):
            add(phrase)
        for token in re.findall(r"[A-Za-z][A-Za-z0-9._-]{1,31}", normalized):
            add(token.casefold())
    for raw_name in (str(client.get("name") or ""), str(client.get("alias") or "")):
        name = " ".join(raw_name.split())
        add(name)
        for suffix in _NAME_SUFFIXES:
            if name.endswith(suffix) and len(name) - len(suffix) >= 2:
                add(name[: -len(suffix)])
                break
    return result[:48]


class TaskPlanningAgentRepository:
    def __init__(self, repository: CloudRepository) -> None:
        self.repository = repository

    def list_profiles(self, identity: SessionIdentity) -> list[dict[str, Any]]:
        with self.repository._connection() as connection:  # noqa: SLF001
            clients = connection.execute(
                "SELECT * FROM clients WHERE scope_id=? AND lifecycle_state='active' "
                "ORDER BY name,id",
                (identity.scope_id,),
            ).fetchall()
            result: list[dict[str, Any]] = []
            for client in clients:
                try:
                    self.repository._require_project_access(  # noqa: SLF001
                        connection,
                        identity,
                        project_id=str(client["id"]),
                        capability="project_read",
                    )
                except RepositoryError:
                    continue
                profile = connection.execute(
                    """
                    SELECT narrative.current_version,narrative.version AS aggregate_version,
                           manifest.receipt,narrative.updated_at
                    FROM narrative_outputs AS narrative
                    JOIN artifact_versions AS version
                      ON version.scope_id=narrative.scope_id
                     AND version.artifact_id=narrative.id
                     AND version.version=narrative.current_version
                    JOIN object_manifests AS manifest
                      ON manifest.scope_id=version.scope_id
                     AND manifest.id=version.object_manifest_id
                    WHERE narrative.scope_id=? AND narrative.client_id=?
                      AND narrative.artifact_kind='project_keyword_profile'
                      AND narrative.lifecycle_state='active'
                    """,
                    (identity.scope_id, str(client["id"])),
                ).fetchone()
                payload: dict[str, Any] = {}
                if profile is not None:
                    try:
                        loaded = json.loads(str(profile["receipt"] or "{}"))
                        payload = dict(loaded) if isinstance(loaded, Mapping) else {}
                    except json.JSONDecodeError:
                        payload = {}
                result.append(
                    {
                        "clientId": str(client["id"]),
                        "clientName": str(client["name"] or ""),
                        "keywords": list(payload.get("keywords") or _keywords(dict(client))),
                        "state": "ready" if profile is not None else "not_built",
                        "version": int(profile["current_version"] or 1) if profile else 0,
                        "updatedAt": str(profile["updated_at"] or "") if profile else None,
                    }
                )
            return result

    def refresh_profile(
        self,
        identity: SessionIdentity,
        *,
        client_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.repository._connection() as connection:  # noqa: SLF001
            connection.execute("BEGIN IMMEDIATE")
            try:
                client = connection.execute(
                    "SELECT * FROM clients WHERE scope_id=? AND id=? AND lifecycle_state='active'",
                    (identity.scope_id, client_id),
                ).fetchone()
                if client is None:
                    raise RepositoryError(404, "client_not_found", "项目不存在")
                self.repository._require_project_access(  # noqa: SLF001
                    connection,
                    identity,
                    project_id=client_id,
                    capability="project_write",
                )
                supporting_text = [
                    str(row["title"] or "")
                    for row in connection.execute(
                        "SELECT title FROM knowledge_documents WHERE scope_id=? "
                        "AND client_id=? AND lifecycle_state='active'",
                        (identity.scope_id, client_id),
                    ).fetchall()
                ]
                for row in connection.execute(
                    """
                    SELECT manifest.receipt
                    FROM atomic_facts AS fact
                    JOIN source_sets AS sources
                      ON sources.scope_id=fact.scope_id AND sources.id=fact.source_set_id
                    JOIN object_manifests AS manifest
                      ON manifest.scope_id=fact.scope_id
                     AND manifest.id=fact.fact_object_manifest_id
                    WHERE fact.scope_id=? AND sources.client_id=?
                      AND fact.lifecycle_state='active'
                      AND fact.verification_state IN ('verified','confirmed')
                    """,
                    (identity.scope_id, client_id),
                ).fetchall():
                    try:
                        fact = json.loads(str(row["receipt"] or "{}"))
                    except json.JSONDecodeError:
                        fact = {}
                    if isinstance(fact, Mapping):
                        supporting_text.extend(
                            str(fact.get(key) or "")
                            for key in ("statement", "title", "canonicalValue", "summary")
                        )
                profile_id = _stable_id("project_keyword_profile", identity.scope_id, client_id)
                current = connection.execute(
                    "SELECT * FROM narrative_outputs WHERE scope_id=? AND id=?",
                    (identity.scope_id, profile_id),
                ).fetchone()
                next_content_version = int(current["current_version"] or 0) + 1 if current else 1
                next_aggregate_version = int(current["version"] or 0) + 1 if current else 1
                keywords = _keywords(
                    dict(client),
                    [*supporting_text, *list(payload.get("keywords") or [])],
                )
                receipt = canonical_json(
                    {
                        "schema": PROFILE_SCHEMA,
                        "clientId": client_id,
                        "keywords": keywords,
                        "profileVersion": next_content_version,
                        "sourceFields": ["clients.name", "clients.alias", "clients.domain", "clients.summary"],
                        "updatedAt": now,
                    }
                )
                content_hash = sha256_text(receipt)
                manifest_id = _stable_id("manifest_project_keywords", profile_id, next_content_version)
                version_id = _stable_id("project_keywords_version", profile_id, next_content_version)
                connection.execute(
                    """
                    INSERT INTO object_manifests (
                        id,scope_id,storage_key,content_hash,lifecycle_state,receipt,
                        holder_role,holder_instance_id,storage_kind,byte_size,media_type,
                        availability_state,receipt_hash,created_at,verified_at,deleted_at,
                        authority_role,origin_instance_id
                    ) VALUES (?,?,NULL,?,'active',?,'cloud_task_planning',?,
                              'metadata_receipt',?,'application/vnd.yiyu.project-keywords+json',
                              'ready',?,?,?,NULL,'cloud',?)
                    """,
                    (
                        manifest_id,
                        identity.scope_id,
                        content_hash,
                        receipt,
                        self.repository.cloud_instance_id,
                        len(receipt.encode("utf-8")),
                        content_hash,
                        now,
                        now,
                        self.repository.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO secured_resources (
                        id,scope_id,resource_kind,lifecycle_state,version,
                        resource_type_key,created_at,updated_at,deleted_at,
                        authority_role,origin_instance_id
                    ) VALUES (?,?,'narrative_output','active',?,'project_keyword_profile',
                              ?,?,NULL,'cloud',?)
                    ON CONFLICT(id) DO UPDATE SET version=excluded.version,
                        lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL
                    """,
                    (profile_id, identity.scope_id, next_aggregate_version, now, now, self.repository.cloud_instance_id),
                )
                connection.execute(
                    """
                    INSERT INTO narrative_outputs (
                        id,scope_id,client_id,source_set_id,current_version,lifecycle_state,
                        title,artifact_kind,visibility_scope,publication_state,
                        owner_membership_id,published_at,version,created_at,updated_at,
                        deleted_at,authority_role,origin_instance_id
                    ) VALUES (?,?,?,NULL,?,'active',?,'project_keyword_profile',
                              'organization','published',?,?,?, ?,?,NULL,'cloud',?)
                    ON CONFLICT(id) DO UPDATE SET current_version=excluded.current_version,
                        title=excluded.title,publication_state='published',
                        published_at=excluded.published_at,version=excluded.version,
                        updated_at=excluded.updated_at,lifecycle_state='active',deleted_at=NULL
                    """,
                    (
                        profile_id,
                        identity.scope_id,
                        client_id,
                        next_content_version,
                        f"{str(client['name'] or '项目')}关键词画像",
                        identity.membership_id,
                        now,
                        next_aggregate_version,
                        str(current["created_at"] or now) if current else now,
                        now,
                        self.repository.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO artifact_versions (
                        id,scope_id,artifact_id,version,content_hash,object_manifest_id,
                        source_set_id,publication_state,created_by_membership_id,created_at,
                        origin_instance_id,integrity_hash,authority_role
                    ) VALUES (?,?,?,?,?,?,NULL,'published',?,?,?,?,'cloud')
                    """,
                    (
                        version_id,
                        identity.scope_id,
                        profile_id,
                        next_content_version,
                        content_hash,
                        manifest_id,
                        identity.membership_id,
                        now,
                        self.repository.cloud_instance_id,
                        sha256_text(f"{profile_id}|{next_content_version}|{content_hash}"),
                    ),
                )
                result = {
                    "clientId": client_id,
                    "clientName": str(client["name"] or ""),
                    "keywords": keywords,
                    "state": "ready",
                    "version": next_content_version,
                    "updatedAt": now,
                }
                payload_hash = sha256_text(canonical_json({"clientId": client_id, "keywords": keywords}))
                GC07ProjectMaterialsRepository._record_command(  # noqa: SLF001
                    connection,
                    identity,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                    command_type="task_planning.project_keyword_profile.refreshed",
                    aggregate_type="narrative_output",
                    aggregate_id=profile_id,
                    aggregate_version=next_aggregate_version,
                    expected_aggregate_version=int(current["version"] or 0) if current else None,
                    result=result,
                    target_resource_id=profile_id,
                )
                command = connection.execute(
                    "SELECT operation_id FROM commands WHERE scope_id=? AND idempotency_key=?",
                    (identity.scope_id, idempotency_key),
                ).fetchone()
                operation_id = str(command["operation_id"])
                run_id = self.repository._record_id("run", operation_id, "task-planning")  # noqa: SLF001
                bot_id = builtin_agent_id(identity.organization_id, "task_planning")
                connection.execute(
                    """
                    INSERT INTO execution_runs (
                        id,scope_id,bot_id,rule_id,task_id,operation_id,status,
                        initiator_membership_id,proposal_id,run_kind,
                        progress_object_manifest_id,result_object_manifest_id,
                        started_at,finished_at,version,lifecycle_state,created_at,
                        updated_at,deleted_at
                    ) VALUES (?,?,?,NULL,NULL,?,'completed',?,NULL,
                              'project_keyword_profile_refresh',NULL,?,?,?,1,
                              'active',?,?,NULL)
                    """,
                    (
                        run_id,
                        identity.scope_id,
                        bot_id,
                        operation_id,
                        identity.membership_id,
                        manifest_id,
                        now,
                        now,
                        now,
                        now,
                    ),
                )
                result["agentRun"] = AgentRunReceipt(
                    agent_kind="task_planning",
                    run_id=run_id,
                    state="completed",
                    stage="keyword_profile_ready",
                    message="已更新项目安全关键词画像",
                    result_version=next_content_version,
                ).as_dict()
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise
