from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlparse

from strict_common.agent_memory import AgentRunReceipt, builtin_agent_id
from strict_common.ids import canonical_json, sha256_text, utc_now
from strict_common.security import payload_fingerprint

from ..repository import RepositoryError
from .project_materials import GC07ProjectMaterialsRepository


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def _public_url(value: Any) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RepositoryError(422, "official_website_url_invalid", "官网地址无效")
    return raw


def _manifest_receipt(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(row["receipt"] or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _page_priority(item: Mapping[str, Any]) -> tuple[int, int, int, str]:
    url = str(item.get("url") or "")
    title = str(item.get("title") or "")
    parsed = urlparse(url)
    path = parsed.path.strip("/").casefold()
    route = " ".join(parse_qs(parsed.query).get("page", []))
    haystack = f"{route} {path} {title}".casefold()
    groups = (
        (0, ("about", "about-us", "关于", "机构介绍", "机构简介")),
        (1, ("team", "people", "团队", "成员", "理事", "治理", "governance")),
        (2, ("project", "program", "service", "项目", "业务", "服务", "计划")),
        (3, ("mission", "vision", "history", "使命", "愿景", "历程")),
        (4, ("contact", "联系", "workbench", "产品")),
        (8, ("report", "article", "news", "报告", "文章", "资讯", "洞察")),
    )
    is_root = not path and not parsed.query
    category = -1 if is_root else 12
    for rank, terms in groups:
        if any(term.casefold() in haystack for term in terms):
            category = rank
            break
    crawl_only = -1 if is_root else 2 if path.startswith("share/") else 1 if path.startswith("seo/") else 0
    return crawl_only, category, len([part for part in path.split("/") if part]), url.casefold()


def official_website_status(repository: Any, identity: Any, *, project_id: str) -> dict[str, Any]:
    with repository._connection() as connection:  # noqa: SLF001
        project = repository._require_project_access(  # noqa: SLF001
            connection,
            identity,
            project_id=project_id,
        )
        rows = connection.execute(
            """
            SELECT d.id, d.title, d.current_version, d.updated_at,
                   a.id AS source_asset_id, a.source_locator_nonlocal,
                   a.availability_state, v.content_hash, m.receipt
            FROM source_sets AS capture
            JOIN source_set_members AS member
              ON member.source_set_id=capture.id AND member.scope_id=capture.scope_id
             AND member.lifecycle_state='active' AND member.source_object_kind='source_asset'
            JOIN source_assets AS a
              ON a.id=member.source_object_id AND a.scope_id=capture.scope_id
             AND a.lifecycle_state='active' AND a.source_kind='official_website'
            JOIN knowledge_documents AS d
              ON d.source_asset_id=a.id AND d.scope_id=a.scope_id
            JOIN document_versions AS v
              ON v.document_id=d.id AND v.scope_id=d.scope_id
             AND v.version=d.current_version
            JOIN object_manifests AS m
              ON m.id=v.object_manifest_id AND m.scope_id=d.scope_id
             AND m.lifecycle_state='active'
            WHERE d.scope_id=? AND d.client_id=?
              AND capture.id=(
                SELECT latest.id FROM source_sets AS latest
                WHERE latest.scope_id=d.scope_id AND latest.client_id=d.client_id
                  AND latest.purpose_kind='official_website_capture'
                  AND latest.lifecycle_state='active'
                ORDER BY latest.updated_at DESC, latest.created_at DESC, latest.id DESC
                LIMIT 1
              )
              AND capture.purpose_kind='official_website_capture'
              AND capture.lifecycle_state='active'
              AND d.document_kind='official_website_fact'
              AND d.lifecycle_state='active' AND d.publication_state='published'
            ORDER BY LENGTH(a.source_locator_nonlocal), d.title, d.id
            """,
            (identity.scope_id, project_id),
        ).fetchall()
        candidate_count = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM intelligence_records "
                "WHERE scope_id=? AND client_id=? AND lifecycle_state='active' "
                "AND verification_state='candidate'",
                (identity.scope_id, project_id),
            ).fetchone()["count"]
        )
        latest_run = connection.execute(
            """
            SELECT run.status, run.started_at, run.finished_at, result.receipt
            FROM execution_runs AS run
            JOIN bot_definitions AS bot ON bot.id=run.bot_id
            JOIN commands AS command
              ON command.scope_id=run.scope_id AND command.operation_id=run.operation_id
            LEFT JOIN object_manifests AS result
              ON result.id=run.result_object_manifest_id AND result.scope_id=run.scope_id
            WHERE run.scope_id=? AND bot.agent_kind='intelligence_research'
              AND run.run_kind='official_website_capture'
              AND command.aggregate_type='client' AND command.aggregate_id=?
              AND run.lifecycle_state='active'
            ORDER BY run.created_at DESC, run.id DESC LIMIT 1
            """,
            (identity.scope_id, project_id),
        ).fetchone()
    latest_receipt = _manifest_receipt(latest_run) if latest_run is not None else {}
    pages = []
    for row in rows:
        receipt = _manifest_receipt(row)
        pages.append(
            {
                "sourceAssetId": str(row["source_asset_id"]),
                "knowledgeDocumentId": str(row["id"]),
                "title": str(row["title"] or "官网页面"),
                "url": str(
                    receipt.get("canonicalPublicUrl")
                    or receipt.get("sourceUrl")
                    or row["source_locator_nonlocal"]
                    or ""
                ),
                "captureUrl": str(receipt.get("sourceUrl") or row["source_locator_nonlocal"] or ""),
                "canonicalPublicUrl": str(receipt.get("canonicalPublicUrl") or ""),
                "pageRole": str(receipt.get("pageRole") or "unknown"),
                "captureKind": str(receipt.get("captureKind") or "static"),
                "summary": _text(receipt.get("summary") or receipt.get("statement"), 2_000),
                "contentHash": str(row["content_hash"] or ""),
                "version": int(row["current_version"] or 1),
                "availabilityState": str(row["availability_state"] or "ready"),
                "updatedAt": str(row["updated_at"] or ""),
            }
        )
    pages.sort(key=_page_priority)
    root_page = min(
        pages,
        key=lambda item: (
            len(urlparse(str(item["url"])).path.strip("/")),
            bool(urlparse(str(item["url"])).query),
            len(str(item["url"])),
        ),
        default=None,
    )
    return {
        "projectId": project_id,
        "state": "ready" if pages else "not_connected",
        "registeredUrl": root_page["url"] if root_page else None,
        "pages": pages,
        "pageCount": len(pages),
        "candidateCount": candidate_count,
        "latestRun": (
            {
                "status": str(latest_run["status"]),
                "startedAt": latest_run["started_at"],
                "finishedAt": latest_run["finished_at"],
            }
            if latest_run is not None
            else None
        ),
        "researchProgress": latest_receipt.get("researchProgress"),
        "processingAgentKind": "intelligence_research",
        "updatedAt": max((item["updatedAt"] for item in pages), default=None),
    }


def official_fact_candidates(
    repository: Any,
    identity: Any,
    *,
    project_id: str,
    status: str | None = None,
) -> dict[str, Any]:
    state = {"pending": "candidate", "verified": "verified", "rejected": "rejected"}.get(
        str(status or "pending"),
        "candidate",
    )
    with repository._connection() as connection:  # noqa: SLF001
        repository._require_project_access(  # noqa: SLF001
            connection,
            identity,
            project_id=project_id,
        )
        rows = connection.execute(
            """
            SELECT f.id, f.confidence, f.verification_state, f.confirmed_at,
                   f.created_at, f.updated_at, m.receipt, e.source_object_id,
                   a.display_name, a.source_locator_nonlocal
            FROM atomic_facts AS f
            JOIN source_sets AS s ON s.id=f.source_set_id AND s.scope_id=f.scope_id
            JOIN object_manifests AS m
              ON m.id=f.fact_object_manifest_id AND m.scope_id=f.scope_id
            LEFT JOIN evidence_links AS e ON e.fact_id=f.id AND e.scope_id=f.scope_id
            LEFT JOIN source_assets AS a
              ON a.id=e.source_object_id AND a.scope_id=f.scope_id
            WHERE f.scope_id=? AND s.client_id=?
              AND s.purpose_kind='official_website_capture'
              AND f.verification_state=? AND f.lifecycle_state='active'
              AND m.storage_kind='official_fact_candidate'
            ORDER BY f.confidence DESC, f.created_at, f.id
            """,
            (identity.scope_id, project_id, state),
        ).fetchall()
    attributes = []
    for row in rows:
        receipt = _manifest_receipt(row)
        if receipt.get("schema") != "yiyu.official-website-semantic-candidate.v1":
            continue
        attributes.append(
            {
                "id": str(row["id"]),
                "term_id": str(receipt.get("term") or ""),
                "term": str(receipt.get("term") or "官网事实"),
                "attribute_name": str(receipt.get("attributeName") or "事实"),
                "value_category": str(receipt.get("valueCategory") or "text"),
                "value_text": str(receipt.get("valueText") or ""),
                "value_normalized": None,
                "value_unit": str(receipt.get("valueUnit") or ""),
                "scope": str(receipt.get("scope") or "官网权威信息"),
                "as_of_date": str(receipt.get("asOfDate") or "") or None,
                "fact_kind": str(receipt.get("factKind") or "business_term"),
                "subject_kind": str(receipt.get("subjectKind") or "client"),
                "source_type": "official_website",
                "source_evidence": str(receipt.get("evidence") or ""),
                "source_doc_id": row["source_object_id"],
                "source_doc_title": str(receipt.get("sourceTitle") or row["display_name"] or "官网页面"),
                "source_doc_path": str(receipt.get("sourcePublicUrl") or "") or None,
                "source_capture_url": str(receipt.get("sourceUrl") or row["source_locator_nonlocal"] or ""),
                "source_reference_mode": (
                    "public_page" if str(receipt.get("sourcePublicUrl") or "") else "evidence_snapshot"
                ),
                "confidence": float(row["confidence"] or 0.0),
                "verification_status": (
                    "pending" if str(row["verification_state"]) == "candidate" else str(row["verification_state"])
                ),
                "verified_by": identity.membership_id if row["confirmed_at"] else None,
                "verified_at": row["confirmed_at"],
                "rejection_note": "",
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
        )
    return {"attributes": attributes}


def auto_verify_official_facts(
    repository: Any,
    identity: Any,
    *,
    project_id: str,
    idempotency_key: str,
) -> dict[str, Any]:
    """Promote exact-evidence facts from a registered official site.

    This is both the compatibility bridge for earlier pending rows and the
    explicit command receipt proving that no human approval gate was required.
    """

    payload_hash = payload_fingerprint({"projectId": project_id, "policy": "official-exact-evidence-v1"})
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = GC07ProjectMaterialsRepository._receipt(
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )
            if replay is not None:
                connection.rollback()
                return replay
            project = repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=project_id,
                capability="knowledge_write",
            )
            rows = connection.execute(
                """
                SELECT fact.id, fact.version, fact.fact_object_manifest_id,
                       manifest.receipt
                FROM atomic_facts AS fact
                JOIN source_sets AS sources
                  ON sources.id=fact.source_set_id AND sources.scope_id=fact.scope_id
                 AND sources.client_id=? AND sources.purpose_kind='official_website_capture'
                 AND sources.lifecycle_state='active'
                JOIN object_manifests AS manifest
                  ON manifest.id=fact.fact_object_manifest_id AND manifest.scope_id=fact.scope_id
                 AND manifest.storage_kind='official_fact_candidate'
                 AND manifest.lifecycle_state='active'
                WHERE fact.scope_id=? AND fact.verification_state='candidate'
                  AND fact.lifecycle_state='active'
                ORDER BY fact.id
                """,
                (project_id, identity.scope_id),
            ).fetchall()
            now = utc_now()
            promoted = 0
            for row in rows:
                receipt = _manifest_receipt(row)
                if (
                    receipt.get("schema") != "yiyu.official-website-semantic-candidate.v1"
                    or not str(receipt.get("evidence") or "").strip()
                    or not str(receipt.get("sourceUrl") or "").strip()
                ):
                    continue
                receipt["sourceType"] = "official_website_semantic_fact"
                receipt["verificationState"] = "verified"
                receipt["verificationBasis"] = "registered_official_website_exact_evidence"
                receipt["autoVerifiedAt"] = now
                receipt_json = canonical_json(receipt)
                fact_hash = sha256_text(receipt_json)
                manifest_id = repository._record_id("manifest", str(row["id"]), fact_hash)  # noqa: SLF001
                connection.execute(
                    """
                    INSERT INTO object_manifests (
                        id, scope_id, storage_key, content_hash, lifecycle_state,
                        receipt, holder_role, holder_instance_id, storage_kind,
                        byte_size, media_type, availability_state, receipt_hash,
                        created_at, verified_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, NULL, ?, 'active', ?, 'organization_cloud', ?,
                              'official_fact_candidate', ?, 'application/json', 'ready',
                              ?, ?, ?, NULL, 'cloud', ?)
                    """,
                    (manifest_id, identity.scope_id, fact_hash, receipt_json, repository.cloud_instance_id, len(receipt_json.encode("utf-8")), sha256_text(receipt_json), now, now, repository.cloud_instance_id),
                )
                connection.execute(
                    """
                    UPDATE atomic_facts
                    SET fact_hash=?, fact_object_manifest_id=?, verification_state='verified',
                        confirmed_by_membership_id=NULL, confirmed_at=?,
                        version=version+1, updated_at=?
                    WHERE id=? AND scope_id=? AND verification_state='candidate'
                    """,
                    (fact_hash, manifest_id, now, now, row["id"], identity.scope_id),
                )
                intelligence_id = str(receipt.get("intelligenceRecordId") or "")
                if intelligence_id:
                    connection.execute(
                        """
                        UPDATE intelligence_records
                        SET verification_state='verified', confirmed_by_membership_id=NULL,
                            confirmed_at=?, version=version+1, updated_at=?
                        WHERE id=? AND scope_id=? AND client_id=?
                          AND verification_state='candidate'
                        """,
                        (now, now, intelligence_id, identity.scope_id, project_id),
                    )
                promoted += 1
            result = {
                "projectId": project_id,
                "status": "completed",
                "promotedCount": promoted,
                "policy": "registered_official_website_exact_evidence",
                "updatedAt": now,
            }
            GC07ProjectMaterialsRepository._record_command(
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                command_type="gc15.official_facts.auto_verified",
                aggregate_type="client",
                aggregate_id=project_id,
                aggregate_version=int(project["version"] or 1),
                expected_aggregate_version=int(project["version"] or 1),
                result=result,
                target_resource_id=project_id,
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def review_official_fact_candidate(
    repository: Any,
    identity: Any,
    *,
    project_id: str,
    fact_id: str,
    review_status: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    if review_status not in {"verified", "rejected"}:
        raise RepositoryError(422, "official_fact_review_invalid", "官网事实审核状态无效")
    normalized = {
        "projectId": project_id,
        "factId": fact_id,
        "reviewStatus": review_status,
        "attributeName": _text(payload.get("attributeName"), 120),
        "valueText": _text(payload.get("valueText"), 1_000),
        "valueUnit": _text(payload.get("valueUnit"), 80),
        "scope": _text(payload.get("scope"), 200),
        "asOfDate": _text(payload.get("asOfDate"), 80),
    }
    payload_hash = payload_fingerprint(normalized)
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            project = repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=project_id,
                capability="knowledge_write",
            )
            replay = GC07ProjectMaterialsRepository._receipt(
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )
            if replay is not None:
                connection.rollback()
                return replay
            row = connection.execute(
                """
                SELECT f.*, m.receipt, s.client_id
                FROM atomic_facts AS f
                JOIN source_sets AS s ON s.id=f.source_set_id AND s.scope_id=f.scope_id
                JOIN object_manifests AS m
                  ON m.id=f.fact_object_manifest_id AND m.scope_id=f.scope_id
                WHERE f.id=? AND f.scope_id=? AND s.client_id=?
                  AND s.purpose_kind='official_website_capture'
                  AND f.lifecycle_state='active'
                  AND m.storage_kind='official_fact_candidate'
                """,
                (fact_id, identity.scope_id, project_id),
            ).fetchone()
            if row is None:
                raise RepositoryError(404, "official_fact_missing", "官网事实候选不存在")
            receipt = _manifest_receipt(row)
            if receipt.get("schema") != "yiyu.official-website-semantic-candidate.v1":
                raise RepositoryError(409, "official_fact_receipt_invalid", "官网事实候选回执无效")
            if normalized["attributeName"]:
                receipt["attributeName"] = normalized["attributeName"]
            if normalized["valueText"]:
                receipt["valueText"] = normalized["valueText"]
            receipt["statement"] = (
                f"{receipt.get('term') or '官网事实'}·"
                f"{receipt.get('attributeName') or '事实'}：{receipt.get('valueText') or ''}"
            )
            receipt["verificationState"] = review_status
            receipt["reviewedAt"] = utc_now()
            receipt_json = canonical_json(receipt)
            fact_hash = sha256_text(receipt_json)
            now = utc_now()
            manifest_id = repository._record_id("manifest", fact_id, fact_hash)  # noqa: SLF001
            connection.execute(
                """
                INSERT INTO object_manifests (
                    id, scope_id, storage_key, content_hash, lifecycle_state,
                    receipt, holder_role, holder_instance_id, storage_kind,
                    byte_size, media_type, availability_state, receipt_hash,
                    created_at, verified_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, NULL, ?, 'active', ?, 'organization_cloud', ?,
                          'official_fact_candidate', ?, 'application/json', 'ready',
                          ?, ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET receipt=excluded.receipt,
                    receipt_hash=excluded.receipt_hash, verified_at=excluded.verified_at,
                    lifecycle_state='active', deleted_at=NULL
                """,
                (manifest_id, identity.scope_id, fact_hash, receipt_json, repository.cloud_instance_id, len(receipt_json.encode("utf-8")), sha256_text(receipt_json), now, now, repository.cloud_instance_id),
            )
            connection.execute(
                """
                UPDATE atomic_facts
                SET fact_hash=?, fact_object_manifest_id=?, verification_state=?,
                    confirmed_by_membership_id=?, confirmed_at=?, version=version+1,
                    updated_at=?
                WHERE id=? AND scope_id=?
                """,
                (fact_hash, manifest_id, review_status, identity.membership_id if review_status == "verified" else None, now if review_status == "verified" else None, now, fact_id, identity.scope_id),
            )
            intelligence_id = str(receipt.get("intelligenceRecordId") or "")
            if intelligence_id:
                connection.execute(
                    """
                    UPDATE intelligence_records
                    SET verification_state=?, confirmed_by_membership_id=?,
                        confirmed_at=?, version=version+1, updated_at=?
                    WHERE id=? AND scope_id=? AND client_id=?
                    """,
                    (review_status, identity.membership_id if review_status == "verified" else None, now if review_status == "verified" else None, now, intelligence_id, identity.scope_id, project_id),
                )
                intelligence = connection.execute(
                    "SELECT version FROM intelligence_records "
                    "WHERE id=? AND scope_id=? AND client_id=?",
                    (intelligence_id, identity.scope_id, project_id),
                ).fetchone()
                if intelligence is not None:
                    revision_version = int(intelligence["version"] or 1)
                    revision_id = repository._record_id(  # noqa: SLF001
                        "intelligence-revision",
                        intelligence_id,
                        revision_version,
                    )
                    integrity_hash = sha256_text(
                        canonical_json(
                            {
                                "intelligenceId": intelligence_id,
                                "version": revision_version,
                                "contentHash": fact_hash,
                                "reviewStatus": review_status,
                                "editorMembershipId": identity.membership_id,
                            }
                        )
                    )
                    connection.execute(
                        """
                        INSERT INTO intelligence_revisions (
                            id, scope_id, intelligence_id, editor_principal_id,
                            version, editor_membership_id,
                            content_object_manifest_id, content_hash, reason,
                            created_at, origin_instance_id, integrity_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO NOTHING
                        """,
                        (
                            revision_id,
                            identity.scope_id,
                            intelligence_id,
                            identity.principal_id,
                            revision_version,
                            identity.membership_id,
                            manifest_id,
                            fact_hash,
                            f"official_fact_{review_status}",
                            now,
                            repository.cloud_instance_id,
                            integrity_hash,
                        ),
                    )
            result = {
                "ok": True,
                "id": fact_id,
                "status": review_status,
                "intelligenceId": intelligence_id or None,
            }
            operation_id, _ = GC07ProjectMaterialsRepository._record_command(
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                command_type="gc15.official_fact.reviewed",
                aggregate_type="client",
                aggregate_id=project_id,
                aggregate_version=int(project["version"] or 1),
                expected_aggregate_version=int(project["version"] or 1),
                result=result,
                target_resource_id=project_id,
            )
            connection.commit()
            from .gc12_corrections import _propagate_project_knowledge_consumers

            result["consumerPropagation"] = _propagate_project_knowledge_consumers(
                repository,
                identity,
                project_id=project_id,
                fact_id=fact_id,
                fact_version=int(row["version"] or 0) + 1,
                operation_id=operation_id,
                source_event_type="gc15.official_fact.reviewed",
            )
            return result
        except Exception:
            connection.rollback()
            raise


def capture_official_website(
    repository: Any,
    identity: Any,
    *,
    project_id: str,
    pages: Sequence[Mapping[str, Any]],
    fact_candidates: Sequence[Mapping[str, Any]] = (),
    research_progress: Mapping[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    normalized = []
    # The crawler stops naturally when the site has no more same-origin text
    # pages. 36 is a safety ceiling, not the research goal; small sites remain
    # small while content-rich sites are no longer silently truncated at 12.
    for raw in pages[:36]:
        url = _public_url(raw.get("url"))
        title = _text(raw.get("title"), 300) or url
        text = _text(raw.get("text"), 12_000)
        content_hash = str(raw.get("contentHash") or "").strip().lower()
        if not text or len(content_hash) != 64 or any(c not in "0123456789abcdef" for c in content_hash):
            raise RepositoryError(422, "official_website_page_invalid", "官网页面回执无效")
        normalized.append(
            {
                "url": url,
                "title": title,
                "text": text,
                "contentHash": content_hash,
                "capturedAt": str(raw.get("capturedAt") or utc_now()),
                "discoveredUrl": _text(raw.get("discoveredUrl"), 2_000) or url,
                "canonicalPublicUrl": _text(raw.get("canonicalPublicUrl"), 2_000),
                "pageRole": _text(raw.get("pageRole"), 60) or "unknown",
                "captureKind": _text(raw.get("captureKind"), 60) or "static",
            }
        )
    if not normalized:
        raise RepositoryError(422, "official_website_pages_required", "官网没有可登记页面")
    root_host = str(urlparse(normalized[0]["url"]).hostname or "").removeprefix("www.").lower()
    if any(str(urlparse(item["url"]).hostname or "").removeprefix("www.").lower() != root_host for item in normalized):
        raise RepositoryError(422, "official_website_origin_mismatch", "官网页面必须属于同一域名")
    allowed_page_roles = {
        "institutional_profile", "project_service", "impact", "resource",
        "product_demo", "transition", "unknown",
    }
    allowed_capture_kinds = {"static", "rendered", "seo_mirror"}
    for item in normalized:
        if item["pageRole"] not in allowed_page_roles:
            item["pageRole"] = "unknown"
        if item["captureKind"] not in allowed_capture_kinds:
            item["captureKind"] = "static"
        try:
            discovered_url = _public_url(item["discoveredUrl"])
        except RepositoryError:
            discovered_url = item["url"]
        discovered_host = str(urlparse(discovered_url).hostname or "").removeprefix("www.").lower()
        item["discoveredUrl"] = discovered_url if discovered_host == root_host else item["url"]
        public_url = item["canonicalPublicUrl"]
        if public_url:
            try:
                public_url = _public_url(public_url)
            except RepositoryError:
                public_url = ""
        public_host = str(urlparse(public_url).hostname or "").removeprefix("www.").lower()
        public_path = str(urlparse(public_url).path or "").strip("/").casefold()
        if public_host != root_host or public_path.startswith("seo/") or "sitemap" in public_path:
            public_url = ""
        item["canonicalPublicUrl"] = public_url

    page_urls = {item["url"] for item in normalized}
    pages_by_url = {item["url"]: item for item in normalized}
    allowed_subjects = {"client", "project", "service", "person", "team", "governance"}
    allowed_fact_kinds = {
        "organization_profile", "mission_vision", "service_offering",
        "project_definition", "methodology", "governance", "partnership",
        "person_profile", "milestone", "impact_metric", "business_term",
    }
    normalized_candidates: list[dict[str, Any]] = []
    for raw in fact_candidates[:160]:
        source_url = _public_url(raw.get("sourceUrl"))
        term = _text(raw.get("term"), 120)
        attribute_name = _text(raw.get("attributeName"), 120)
        value_text = _text(raw.get("valueText"), 1_000)
        evidence = _text(raw.get("evidence"), 1_500)
        if source_url not in page_urls or not term or not attribute_name or not value_text or not evidence:
            raise RepositoryError(422, "official_fact_candidate_invalid", "官网事实候选缺少可核验证据")
        source_page = pages_by_url[source_url]
        subject_kind = _text(raw.get("subjectKind"), 40)
        fact_kind = _text(raw.get("factKind"), 60)
        if (
            subject_kind not in allowed_subjects
            or fact_kind not in allowed_fact_kinds
            or source_page["pageRole"] in {"resource", "product_demo", "transition"}
        ):
            raise RepositoryError(422, "official_fact_policy_rejected", "官网内容不属于可自动生效的客户事实")
        value_category = _text(raw.get("valueCategory"), 40)
        if value_category not in {"person", "date", "location", "count", "amount", "text"}:
            value_category = "text"
        try:
            confidence = min(0.95, max(0.5, float(raw.get("confidence") or 0.75)))
        except (TypeError, ValueError):
            confidence = 0.75
        normalized_candidates.append(
            {
                "term": term,
                "attributeName": attribute_name,
                "valueCategory": value_category,
                "valueText": value_text,
                "evidence": evidence,
                "sourceUrl": source_url,
                "sourcePublicUrl": source_page["canonicalPublicUrl"],
                "sourceTitle": _text(raw.get("sourceTitle"), 300) or source_url,
                "pageRole": source_page["pageRole"],
                "captureKind": source_page["captureKind"],
                "subjectKind": subject_kind,
                "factKind": fact_kind,
                "scope": _text(raw.get("scope"), 200),
                "valueUnit": _text(raw.get("valueUnit"), 80),
                "asOfDate": _text(raw.get("asOfDate"), 80),
                "confidence": confidence,
            }
        )

    safe_progress = {
        "state": _text((research_progress or {}).get("state"), 40),
        "pageCount": max(0, int((research_progress or {}).get("pageCount") or len(normalized))),
        "targetCount": max(0, int((research_progress or {}).get("targetCount") or 0)),
        "completedTargetCount": max(0, int((research_progress or {}).get("completedTargetCount") or 0)),
        "factCount": max(0, int((research_progress or {}).get("factCount") or len(normalized_candidates))),
        "retryableFailureCount": max(0, int((research_progress or {}).get("retryableFailureCount") or 0)),
        "targets": [
            {
                "targetId": _text(item.get("targetId"), 80),
                "label": _text(item.get("label"), 120),
                "pageCount": max(0, int(item.get("pageCount") or 0)),
                "attemptedPageCount": max(0, int(item.get("attemptedPageCount") or 0)),
                "minimumFacts": max(0, int(item.get("minimumFacts") or 0)),
                "factCount": max(0, int(item.get("factCount") or 0)),
                "status": _text(item.get("status"), 40),
            }
            for item in ((research_progress or {}).get("targets") or [])[:20]
            if isinstance(item, Mapping)
        ],
    }
    payload_hash = payload_fingerprint(
        {
            "projectId": project_id,
            "pages": normalized,
            "factCandidates": normalized_candidates,
            "researchProgress": safe_progress,
        }
    )
    bot_id = builtin_agent_id(identity.organization_id, "intelligence_research")
    with repository._connection() as connection:  # noqa: SLF001
        connection.execute("BEGIN IMMEDIATE")
        try:
            replay = GC07ProjectMaterialsRepository._receipt(
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )
            if replay is not None:
                connection.rollback()
                return {**replay, "idempotentReplay": True}
            project = repository._require_project_access(  # noqa: SLF001
                connection,
                identity,
                project_id=project_id,
                capability="project_write",
            )
            if connection.execute(
                "SELECT 1 FROM bot_definitions AS bot "
                "JOIN authorization_scopes AS s ON s.id=bot.scope_id "
                "WHERE bot.id=? AND bot.agent_kind='intelligence_research' "
                "AND bot.enabled=1 AND bot.lifecycle_state='active' "
                "AND s.organization_id=? AND s.status='active'",
                (bot_id, identity.organization_id),
            ).fetchone() is None:
                raise RepositoryError(503, "intelligence_research_unavailable", "官网信息抓取与核实暂未就绪")

            now = utc_now()
            changed_count = 0
            verified_count = 0
            candidate_count = 0
            page_results: list[dict[str, Any]] = []
            page_context: dict[str, dict[str, Any]] = {}
            capture_set_id = repository._record_id("source_set", project_id, payload_hash)  # noqa: SLF001
            connection.execute(
                """
                INSERT INTO source_sets (
                    id, scope_id, client_id, security_label_set_version,
                    source_count, version, purpose_kind, publication_state,
                    created_by_principal_id, created_at, expires_at,
                    lifecycle_state, updated_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, ?, 'organization-public-v1', ?, 1,
                          'official_website_capture', 'published', ?, ?, NULL,
                          'active', ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET source_count=excluded.source_count,
                    updated_at=excluded.updated_at, lifecycle_state='active', deleted_at=NULL
                """,
                (capture_set_id, identity.scope_id, project_id, len(normalized), identity.principal_id, now, now, repository.cloud_instance_id),
            )

            home_fact_id: str | None = None
            for ordinal, item in enumerate(normalized):
                source_id = repository._record_id("source_asset", project_id, item["url"].lower())  # noqa: SLF001
                document_id = repository._record_id("knowledge_document", source_id, "official-page")  # noqa: SLF001
                intelligence_id = repository._record_id("intelligence", source_id, item["contentHash"])  # noqa: SLF001
                existing = connection.execute(
                    "SELECT content_hash, version, created_at FROM source_assets WHERE id=? AND scope_id=?",
                    (source_id, identity.scope_id),
                ).fetchone()
                changed = existing is None or str(existing["content_hash"] or "") != item["contentHash"]
                source_version = (int(existing["version"] or 1) + 1) if existing is not None and changed else int(existing["version"] or 1) if existing else 1
                if changed:
                    changed_count += 1
                # This record preserves the official page's own text and URL; it
                # is not an inferred claim. A changed page is therefore a new
                # verified source version. Semantic inferences built from it may
                # still be candidate/conflict records in later processing.
                page_state = "verified"
                verified_count += page_state == "verified"
                candidate_count += page_state == "candidate"
                summary = item["text"][:4_000]
                receipt = {
                    "schema": "yiyu.official-website-page.v1",
                    "sourceType": "official_website_fact",
                    "sourceUrl": item["url"],
                    "discoveredUrl": item["discoveredUrl"],
                    "canonicalPublicUrl": item["canonicalPublicUrl"],
                    "pageRole": item["pageRole"],
                    "captureKind": item["captureKind"],
                    "title": item["title"],
                    "summary": summary,
                    "statement": f"{str(project['name'] or '该项目')}官网页面“{item['title']}”：{summary}",
                    "contentHash": item["contentHash"],
                    "capturedAt": item["capturedAt"],
                    "verificationState": page_state,
                }
                receipt_json = canonical_json(receipt)
                receipt_hash = sha256_text(receipt_json)
                manifest_id = repository._record_id("manifest", source_id, item["contentHash"])  # noqa: SLF001
                connection.execute(
                    """
                    INSERT INTO object_manifests (
                        id, scope_id, storage_key, content_hash, lifecycle_state,
                        receipt, holder_role, holder_instance_id, storage_kind,
                        byte_size, media_type, availability_state, receipt_hash,
                        created_at, verified_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, NULL, ?, 'active', ?, 'organization_cloud', ?,
                              'public_web_snapshot', ?, 'text/html', 'ready', ?, ?, ?,
                              NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET receipt=excluded.receipt,
                        receipt_hash=excluded.receipt_hash, verified_at=excluded.verified_at,
                        lifecycle_state='active', deleted_at=NULL
                    """,
                    (manifest_id, identity.scope_id, item["contentHash"], receipt_json, repository.cloud_instance_id, len(receipt_json.encode("utf-8")), receipt_hash, now, now, repository.cloud_instance_id),
                )
                for resource_id, kind, key in (
                    (source_id, "source_asset", "official_website_page"),
                    (document_id, "knowledge_document", "official_website_fact"),
                    (intelligence_id, "intelligence_record", "official_website_snapshot"),
                ):
                    connection.execute(
                        """
                        INSERT INTO secured_resources (
                            id, scope_id, resource_kind, lifecycle_state, version,
                            resource_type_key, created_at, updated_at, deleted_at,
                            authority_role, origin_instance_id
                        ) VALUES (?, ?, ?, 'active', 1, ?, ?, ?, NULL, 'cloud', ?)
                        ON CONFLICT(id) DO UPDATE SET lifecycle_state='active',
                            updated_at=excluded.updated_at, deleted_at=NULL
                        """,
                        (resource_id, identity.scope_id, kind, key, now, now, repository.cloud_instance_id),
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
                    ) VALUES (?, ?, ?, ?, ?, 'asset', 'official_website', ?, 'text/html', ?,
                              ?, NULL, NULL, NULL, ?, 'ready', NULL, ?, 'active', ?, ?,
                              NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET object_manifest_id=excluded.object_manifest_id,
                        content_hash=excluded.content_hash, display_name=excluded.display_name,
                        byte_size=excluded.byte_size, source_locator_nonlocal=excluded.source_locator_nonlocal,
                        availability_state='ready', version=excluded.version,
                        lifecycle_state='active', updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (source_id, identity.scope_id, project_id, manifest_id, item["contentHash"], item["title"], len(item["text"].encode("utf-8")), item["url"], identity.membership_id, source_version, str(existing["created_at"] or now) if existing else now, now, repository.cloud_instance_id),
                )
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        id, scope_id, source_asset_id, client_id, current_version,
                        owner_membership_id, title, document_kind, visibility_scope,
                        parse_state, publication_state, published_at, version,
                        lifecycle_state, created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?, 'official_website_fact',
                              'organization', 'ready', 'published', ?, ?, 'active', ?, ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET current_version=excluded.current_version,
                        title=excluded.title, parse_state='ready', publication_state='published',
                        published_at=excluded.published_at, version=excluded.version,
                        lifecycle_state='active', updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (document_id, identity.scope_id, source_id, project_id, source_version, item["title"], now, source_version, now, now),
                )
                document_version_id = repository._record_id("document_version", document_id, str(source_version))  # noqa: SLF001
                connection.execute(
                    """
                    INSERT OR IGNORE INTO document_versions (
                        id, scope_id, document_id, version, content_hash, created_at,
                        object_manifest_id, source_asset_version, publication_state,
                        created_by_membership_id, origin_instance_id, integrity_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'published', ?, ?, ?)
                    """,
                    (document_version_id, identity.scope_id, document_id, source_version, item["contentHash"], now, manifest_id, source_version, identity.membership_id, repository.cloud_instance_id, sha256_text(f"{document_id}|{source_version}|{item['contentHash']}")),
                )
                chunk_id = repository._record_id("chunk", document_version_id, "0")  # noqa: SLF001
                connection.execute(
                    """
                    INSERT INTO content_chunks (
                        id, scope_id, document_version_id, ordinal, policy_version,
                        chunk_hash, object_manifest_id, start_locator, end_locator,
                        embedding_eligibility, created_at, version, lifecycle_state,
                        updated_at, deleted_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, 0, 1, ?, ?, 'web:start', 'web:end', 'eligible',
                              ?, 1, 'active', ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET chunk_hash=excluded.chunk_hash,
                        object_manifest_id=excluded.object_manifest_id,
                        lifecycle_state='active', updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (chunk_id, identity.scope_id, document_version_id, item["contentHash"], manifest_id, now, now, repository.cloud_instance_id),
                )
                member_id = repository._record_id("source_member", capture_set_id, source_id)  # noqa: SLF001
                connection.execute(
                    """
                    INSERT INTO source_set_members (
                        id, scope_id, source_set_id, source_object_id, source_version,
                        policy_version, source_object_kind, ordinal, added_at, removed_at,
                        version, lifecycle_state, created_at, updated_at, deleted_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 1, 'source_asset', ?, ?, NULL, 1,
                              'active', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET source_version=excluded.source_version,
                        ordinal=excluded.ordinal, removed_at=NULL, lifecycle_state='active',
                        updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (member_id, identity.scope_id, capture_set_id, source_id, source_version, ordinal, now, now, now, repository.cloud_instance_id),
                )
                connection.execute(
                    """
                    INSERT INTO intelligence_records (
                        id, scope_id, client_id, event_line_id, verification_state,
                        version, source_set_id, title, summary_object_manifest_id,
                        trust_rule_id, confirmed_by_membership_id, confirmed_at,
                        published_document_id, lifecycle_state, created_at, updated_at, deleted_at
                    ) VALUES (?, ?, ?, NULL, ?, 1, ?, ?, ?, NULL, ?, ?, ?, 'active', ?, ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET verification_state=excluded.verification_state,
                        version=intelligence_records.version+1, title=excluded.title,
                        summary_object_manifest_id=excluded.summary_object_manifest_id,
                        confirmed_by_membership_id=excluded.confirmed_by_membership_id,
                        confirmed_at=excluded.confirmed_at, published_document_id=excluded.published_document_id,
                        lifecycle_state='active', updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (intelligence_id, identity.scope_id, project_id, page_state, capture_set_id, item["title"], manifest_id, identity.membership_id if page_state == "verified" else None, now if page_state == "verified" else None, document_id if page_state == "verified" else None, now, now),
                )
                fact_id = repository._record_id("fact", source_id, "official-page")  # noqa: SLF001
                fact_manifest_id = repository._record_id("manifest", fact_id, item["contentHash"])  # noqa: SLF001
                connection.execute(
                    """
                    INSERT OR IGNORE INTO object_manifests (
                        id, scope_id, storage_key, content_hash, lifecycle_state,
                        receipt, holder_role, holder_instance_id, storage_kind,
                        byte_size, media_type, availability_state, receipt_hash,
                        created_at, verified_at, deleted_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, NULL, ?, 'active', ?, 'organization_cloud', ?,
                              'verified_public_fact', ?, 'application/json', 'ready', ?, ?, ?,
                              NULL, 'cloud', ?)
                    """,
                    (fact_manifest_id, identity.scope_id, item["contentHash"], receipt_json, repository.cloud_instance_id, len(receipt_json.encode("utf-8")), receipt_hash, now, now, repository.cloud_instance_id),
                )
                connection.execute(
                    """
                    INSERT INTO secured_resources (
                        id, scope_id, resource_kind, lifecycle_state, version,
                        resource_type_key, created_at, updated_at, deleted_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, 'atomic_fact', 'active', ?, 'official_website_fact', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET version=excluded.version,
                        lifecycle_state='active', updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (fact_id, identity.scope_id, source_version, now, now, repository.cloud_instance_id),
                )
                connection.execute(
                    """
                    INSERT INTO atomic_facts (
                        id, scope_id, chunk_id, fact_hash, confidence, version,
                        source_set_id, fact_object_manifest_id, verification_state,
                        confirmed_by_membership_id, confirmed_at, lifecycle_state,
                        created_at, updated_at, deleted_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, 1.0, ?, ?, ?, ?, ?, ?, 'active', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET chunk_id=excluded.chunk_id,
                        fact_hash=excluded.fact_hash, version=excluded.version,
                        source_set_id=excluded.source_set_id,
                        fact_object_manifest_id=excluded.fact_object_manifest_id,
                        verification_state=excluded.verification_state,
                        confirmed_by_membership_id=excluded.confirmed_by_membership_id,
                        confirmed_at=excluded.confirmed_at, lifecycle_state='active',
                        updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (fact_id, identity.scope_id, chunk_id, item["contentHash"], source_version, capture_set_id, fact_manifest_id, page_state, identity.membership_id if page_state == "verified" else None, now if page_state == "verified" else None, now, now, repository.cloud_instance_id),
                )
                evidence_id = repository._record_id("evidence", fact_id, source_id)  # noqa: SLF001
                connection.execute(
                    """
                    INSERT INTO evidence_links (
                        id, scope_id, fact_id, source_object_id, source_version,
                        locator, source_object_kind, locator_kind, page_no,
                        paragraph_no, locator_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'source_asset', 'web_url', NULL, NULL, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET source_version=excluded.source_version,
                        locator=excluded.locator, locator_hash=excluded.locator_hash
                    """,
                    (evidence_id, identity.scope_id, fact_id, source_id, source_version, item["url"], sha256_text(item["url"]), now),
                )
                if ordinal == 0:
                    home_fact_id = fact_id
                elif home_fact_id:
                    relation_id = repository._record_id("relationship", home_fact_id, fact_id)  # noqa: SLF001
                    connection.execute(
                        """
                        INSERT INTO relationship_triples (
                            id, scope_id, subject_fact_id, object_fact_id, predicate,
                            version, confidence, verification_state,
                            confirmed_by_membership_id, created_at, lifecycle_state,
                            updated_at, deleted_at, authority_role, origin_instance_id
                        ) VALUES (?, ?, ?, ?, '官网介绍', 1, 1.0, ?, ?, ?, 'active', ?, NULL, 'cloud', ?)
                        ON CONFLICT(id) DO UPDATE SET verification_state=excluded.verification_state,
                            confirmed_by_membership_id=excluded.confirmed_by_membership_id,
                            lifecycle_state='active', updated_at=excluded.updated_at, deleted_at=NULL
                        """,
                        (relation_id, identity.scope_id, home_fact_id, fact_id, page_state, identity.membership_id if page_state == "verified" else None, now, now, repository.cloud_instance_id),
                    )
                page_results.append(
                    {
                        "title": item["title"],
                        "url": item["canonicalPublicUrl"] or item["url"],
                        "captureUrl": item["url"],
                        "canonicalPublicUrl": item["canonicalPublicUrl"],
                        "pageRole": item["pageRole"],
                        "captureKind": item["captureKind"],
                        "version": source_version,
                        "verificationState": page_state,
                        "contentHash": item["contentHash"],
                    }
                )
                page_context[item["url"]] = {
                    "sourceId": source_id,
                    "sourceVersion": source_version,
                    "chunkId": chunk_id,
                    "documentId": document_id,
                }

            # A completed research run replaces only earlier machine-produced
            # semantic facts from this official-site lane. Human corrections
            # remain authoritative and historical manifests remain auditable.
            if safe_progress["state"] == "completed" and safe_progress["retryableFailureCount"] == 0:
                current_fact_ids = {
                    repository._record_id(  # noqa: SLF001
                        "fact",
                        project_id,
                        sha256_text(
                            canonical_json(
                                {
                                    "term": candidate["term"],
                                    "attributeName": candidate["attributeName"],
                                    "valueText": candidate["valueText"],
                                    "sourceUrl": candidate["sourceUrl"],
                                }
                            )
                        ),
                    )
                    for candidate in normalized_candidates
                }
                previous = connection.execute(
                    """
                    SELECT f.id, f.confirmed_by_membership_id, m.receipt
                    FROM atomic_facts AS f
                    JOIN source_sets AS s ON s.id=f.source_set_id AND s.scope_id=f.scope_id
                    JOIN object_manifests AS m
                      ON m.id=f.fact_object_manifest_id AND m.scope_id=f.scope_id
                    WHERE f.scope_id=? AND s.client_id=?
                      AND s.purpose_kind='official_website_capture'
                      AND f.lifecycle_state='active'
                      AND m.storage_kind='official_fact_candidate'
                    """,
                    (identity.scope_id, project_id),
                ).fetchall()
                for prior in previous:
                    prior_id = str(prior["id"])
                    if prior_id in current_fact_ids or prior["confirmed_by_membership_id"] is not None:
                        continue
                    prior_receipt = _manifest_receipt(prior)
                    if prior_receipt.get("schema") != "yiyu.official-website-semantic-candidate.v1":
                        continue
                    connection.execute(
                        "UPDATE atomic_facts SET lifecycle_state='deleted', deleted_at=?, "
                        "updated_at=?, version=version+1 WHERE id=? AND scope_id=?",
                        (now, now, prior_id, identity.scope_id),
                    )
                    intelligence_id = str(prior_receipt.get("intelligenceRecordId") or "")
                    if intelligence_id:
                        connection.execute(
                            "UPDATE intelligence_records SET lifecycle_state='deleted', "
                            "deleted_at=?, updated_at=?, version=version+1 "
                            "WHERE id=? AND scope_id=? AND client_id=?",
                            (now, now, intelligence_id, identity.scope_id, project_id),
                        )

            for item in normalized_candidates:
                source = page_context[item["sourceUrl"]]
                fact_hash = sha256_text(
                    canonical_json(
                        {
                            "term": item["term"],
                            "attributeName": item["attributeName"],
                            "valueText": item["valueText"],
                            "sourceUrl": item["sourceUrl"],
                        }
                    )
                )
                fact_id = repository._record_id("fact", project_id, fact_hash)  # noqa: SLF001
                intelligence_id = repository._record_id("intelligence", fact_id, "candidate")  # noqa: SLF001
                fact_manifest_id = repository._record_id("manifest", fact_id, fact_hash)  # noqa: SLF001
                receipt = {
                    "schema": "yiyu.official-website-semantic-candidate.v1",
                    "sourceType": "official_website_semantic_fact",
                    "sourceUrl": item["sourceUrl"],
                    "sourcePublicUrl": item["sourcePublicUrl"],
                    "sourceTitle": item["sourceTitle"],
                    "pageRole": item["pageRole"],
                    "captureKind": item["captureKind"],
                    "subjectKind": item["subjectKind"],
                    "factKind": item["factKind"],
                    "term": item["term"],
                    "attributeName": item["attributeName"],
                    "valueCategory": item["valueCategory"],
                    "valueText": item["valueText"],
                    "scope": item["scope"],
                    "valueUnit": item["valueUnit"],
                    "asOfDate": item["asOfDate"],
                    "statement": f"{item['term']}·{item['attributeName']}：{item['valueText']}",
                    "evidence": item["evidence"],
                    "confidence": item["confidence"],
                    "verificationState": "verified",
                    "verificationBasis": "registered_official_website_policy_and_exact_evidence",
                    "intelligenceRecordId": intelligence_id,
                }
                receipt_json = canonical_json(receipt)
                receipt_hash = sha256_text(receipt_json)
                for resource_id, resource_kind, type_key in (
                    (fact_id, "atomic_fact", "official_website_semantic_candidate"),
                    (intelligence_id, "intelligence_record", "official_website_semantic_candidate"),
                ):
                    connection.execute(
                        """
                        INSERT INTO secured_resources (
                            id, scope_id, resource_kind, lifecycle_state, version,
                            resource_type_key, created_at, updated_at, deleted_at,
                            authority_role, origin_instance_id
                        ) VALUES (?, ?, ?, 'active', 1, ?, ?, ?, NULL, 'cloud', ?)
                        ON CONFLICT(id) DO UPDATE SET lifecycle_state='active',
                            updated_at=excluded.updated_at, deleted_at=NULL
                        """,
                        (resource_id, identity.scope_id, resource_kind, type_key, now, now, repository.cloud_instance_id),
                    )
                connection.execute(
                    """
                    INSERT INTO object_manifests (
                        id, scope_id, storage_key, content_hash, lifecycle_state,
                        receipt, holder_role, holder_instance_id, storage_kind,
                        byte_size, media_type, availability_state, receipt_hash,
                        created_at, verified_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, NULL, ?, 'active', ?, 'organization_cloud', ?,
                              'official_fact_candidate', ?, 'application/json',
                              'ready', ?, ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET receipt=excluded.receipt,
                        receipt_hash=excluded.receipt_hash, verified_at=excluded.verified_at,
                        lifecycle_state='active', deleted_at=NULL
                    """,
                    (fact_manifest_id, identity.scope_id, fact_hash, receipt_json, repository.cloud_instance_id, len(receipt_json.encode("utf-8")), receipt_hash, now, now, repository.cloud_instance_id),
                )
                connection.execute(
                    """
                    INSERT INTO intelligence_records (
                        id, scope_id, client_id, event_line_id, verification_state,
                        version, source_set_id, title, summary_object_manifest_id,
                        trust_rule_id, confirmed_by_membership_id, confirmed_at,
                        published_document_id, lifecycle_state, created_at, updated_at,
                        deleted_at
                    ) VALUES (?, ?, ?, NULL, 'verified', 1, ?, ?, ?, NULL, NULL,
                              ?, NULL, 'active', ?, ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        verification_state=CASE
                            WHEN intelligence_records.verification_state='rejected' THEN 'rejected'
                            ELSE 'verified' END,
                        version=intelligence_records.version+1, title=excluded.title,
                        summary_object_manifest_id=CASE
                            WHEN intelligence_records.verification_state='rejected'
                              THEN intelligence_records.summary_object_manifest_id
                            ELSE excluded.summary_object_manifest_id END,
                        confirmed_at=CASE
                            WHEN intelligence_records.verification_state='rejected'
                              THEN intelligence_records.confirmed_at
                            ELSE excluded.confirmed_at END,
                        lifecycle_state='active', updated_at=excluded.updated_at,
                        deleted_at=NULL
                    """,
                    (intelligence_id, identity.scope_id, project_id, capture_set_id, receipt["statement"], fact_manifest_id, now, now, now),
                )
                connection.execute(
                    """
                    INSERT INTO atomic_facts (
                        id, scope_id, chunk_id, fact_hash, confidence, version,
                        source_set_id, fact_object_manifest_id, verification_state,
                        confirmed_by_membership_id, confirmed_at, lifecycle_state,
                        created_at, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'verified', NULL, ?,
                              'active', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET chunk_id=excluded.chunk_id,
                        fact_hash=CASE
                            WHEN atomic_facts.verification_state='rejected'
                              OR atomic_facts.confirmed_by_membership_id IS NOT NULL
                              THEN atomic_facts.fact_hash ELSE excluded.fact_hash END,
                        confidence=excluded.confidence,
                        version=atomic_facts.version+1, source_set_id=excluded.source_set_id,
                        fact_object_manifest_id=CASE
                            WHEN atomic_facts.verification_state='rejected'
                              OR atomic_facts.confirmed_by_membership_id IS NOT NULL
                              THEN atomic_facts.fact_object_manifest_id
                            ELSE excluded.fact_object_manifest_id END,
                        verification_state=CASE
                            WHEN atomic_facts.verification_state='rejected' THEN 'rejected'
                            ELSE 'verified' END,
                        confirmed_at=CASE
                            WHEN atomic_facts.verification_state='rejected'
                              OR atomic_facts.confirmed_by_membership_id IS NOT NULL
                              THEN atomic_facts.confirmed_at ELSE excluded.confirmed_at END,
                        lifecycle_state='active', updated_at=excluded.updated_at,
                        deleted_at=NULL
                    """,
                    (fact_id, identity.scope_id, source["chunkId"], fact_hash, item["confidence"], capture_set_id, fact_manifest_id, now, now, now, repository.cloud_instance_id),
                )
                evidence_id = repository._record_id("evidence", fact_id, source["sourceId"])  # noqa: SLF001
                connection.execute(
                    """
                    INSERT INTO evidence_links (
                        id, scope_id, fact_id, source_object_id, source_version,
                        locator, source_object_kind, locator_kind, page_no,
                        paragraph_no, locator_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'source_asset', 'web_url', NULL,
                              NULL, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET source_version=excluded.source_version,
                        locator=excluded.locator, locator_hash=excluded.locator_hash
                    """,
                    (evidence_id, identity.scope_id, fact_id, source["sourceId"], source["sourceVersion"], item["sourceUrl"], sha256_text(item["sourceUrl"]), now),
                )
                verified_count += 1

            result = {
                "projectId": project_id,
                "state": "ready",
                "registeredUrl": normalized[0]["url"],
                "pageCount": len(page_results),
                "changedCount": changed_count,
                "verifiedCount": verified_count,
                "candidateCount": candidate_count,
                "pages": page_results,
                "processingAgentKind": "intelligence_research",
                "materialBoundary": {"memberFileContentUploaded": False, "localPathUploaded": False},
                "researchProgress": safe_progress,
                "updatedAt": now,
            }
            GC07ProjectMaterialsRepository._record_command(
                connection,
                identity,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
                command_type="gc15.official_website.captured",
                aggregate_type="client",
                aggregate_id=project_id,
                aggregate_version=int(project["version"] or 1),
                expected_aggregate_version=int(project["version"] or 1),
                result=result,
                target_resource_id=project_id,
            )
            command = connection.execute(
                "SELECT operation_id FROM commands WHERE scope_id=? AND idempotency_key=?",
                (identity.scope_id, idempotency_key),
            ).fetchone()
            receipt_row = connection.execute(
                "SELECT result_object_manifest_id FROM idempotency_records WHERE scope_id=? AND idempotency_key=?",
                (identity.scope_id, idempotency_key),
            ).fetchone()
            operation_id = str(command["operation_id"])
            result_manifest_id = str(receipt_row["result_object_manifest_id"])
            connection.execute(
                """
                INSERT INTO execution_runs (
                    id, scope_id, bot_id, rule_id, task_id, operation_id, status,
                    initiator_membership_id, proposal_id, run_kind,
                    progress_object_manifest_id, result_object_manifest_id,
                    started_at, finished_at, version, lifecycle_state,
                    created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, NULL, NULL, ?, 'completed', ?, NULL,
                          'official_website_capture', NULL, ?, ?, ?, 1, 'active', ?, ?, NULL)
                """,
                (repository._record_id("run", operation_id, bot_id), identity.scope_id, bot_id, operation_id, identity.membership_id, result_manifest_id, now, now, now, now),  # noqa: SLF001
            )
            result["agentRun"] = AgentRunReceipt(
                agent_kind="intelligence_research",
                run_id=repository._record_id("run", operation_id, bot_id),  # noqa: SLF001
                state="completed",
                stage="official_facts_ready",
                message="已完成官网范围抓取、核实与事实更新",
                result_version=int(project["version"] or 1),
            ).as_dict()
            for event_type in (
                "gc15.official_website.facts_updated",
                "gc13.project_knowledge.strategic_profile_requested",
            ):
                event_hash = sha256_text(f"{operation_id}|{event_type}|{project_id}|{payload_hash}")
                connection.execute(
                    """
                    INSERT INTO outbox_events (
                        id, scope_id, operation_id, aggregate_version, event_type,
                        status, aggregate_type, aggregate_id, event_object_manifest_id,
                        event_hash, available_at, published_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 'client', ?, ?, ?, ?, NULL, 'cloud', ?)
                    """,
                    (repository._record_id("evt", operation_id, event_type), identity.scope_id, operation_id, int(project["version"] or 1), event_type, project_id, result_manifest_id, event_hash, now, repository.cloud_instance_id),  # noqa: SLF001
                )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
