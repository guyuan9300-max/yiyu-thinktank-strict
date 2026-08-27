from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import canonical_json, new_id, sha256_text, utc_now

from ..repository import CloudRepository, RepositoryError, SessionIdentity


_URL = re.compile(r"https?://[^\s<>\"'，。；、）】]+", re.IGNORECASE)
_PLATFORMS = {
    "bilibili": ("bilibili.com", "b23.tv"),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com", "xhslink.cn", "xhs.cn"),
    "wechat_article": ("mp.weixin.qq.com",),
}


def _normalize_link(value: str) -> tuple[str, str]:
    raw = str(value or "").strip()
    match = _URL.search(raw)
    url = (match.group(0) if match else raw).rstrip(".,;:!?)]}")
    if re.fullmatch(r"BV[0-9A-Za-z]{8,}", url, re.IGNORECASE):
        url = f"https://www.bilibili.com/video/{url}"
    parsed = urlparse(url)
    host = str(parsed.hostname or "").lower().rstrip(".")
    platform = next(
        (
            name
            for name, suffixes in _PLATFORMS.items()
            if any(host == suffix or host.endswith(f".{suffix}") for suffix in suffixes)
        ),
        "",
    )
    if not platform:
        raise RepositoryError(422, "mobile_link_platform_unsupported", "当前仅支持小红书、B站和微信公众号链接")
    if parsed.scheme == "http":
        url = "https://" + url.split("://", 1)[1]
        parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise RepositoryError(422, "mobile_link_url_invalid", "外部链接格式无效")
    return url, platform


class MobileLinkTransferRepository:
    """Cloud-authoritative intake; device-local extraction remains on desktop."""

    def __init__(self, repository: CloudRepository):
        self.repository = repository

    def _receipt(self, connection: Any, identity: SessionIdentity, run_id: str) -> tuple[Any, dict[str, Any]]:
        row = connection.execute(
            "SELECT run.*,manifest.receipt,manifest.id AS manifest_id FROM execution_runs AS run "
            "JOIN object_manifests AS manifest ON manifest.id=run.result_object_manifest_id "
            "AND manifest.scope_id=run.scope_id WHERE run.id=? AND run.scope_id=? "
            "AND run.run_kind='mobile_link_transfer' AND run.lifecycle_state='active'",
            (run_id, identity.scope_id),
        ).fetchone()
        if row is None:
            raise RepositoryError(404, "mobile_link_transfer_missing", "转存记录不存在")
        try:
            receipt = json.loads(str(row["receipt"] or "{}"))
        except json.JSONDecodeError as exc:
            raise RepositoryError(500, "mobile_link_transfer_receipt_invalid", "转存记录回执损坏") from exc
        if not isinstance(receipt, dict):
            raise RepositoryError(500, "mobile_link_transfer_receipt_invalid", "转存记录回执损坏")
        return row, receipt

    @staticmethod
    def _public(receipt: dict[str, Any], version: int) -> dict[str, Any]:
        return {
            "runId": str(receipt.get("runId") or ""),
            "projectId": str(receipt.get("projectId") or ""),
            "sourceUrl": str(receipt.get("sourceUrl") or ""),
            "sourcePlatform": str(receipt.get("sourcePlatform") or ""),
            "status": str(receipt.get("status") or "queued"),
            "stage": str(receipt.get("stage") or "waiting_for_desktop"),
            "title": str(receipt.get("title") or ""),
            "documentId": receipt.get("documentId"),
            "error": receipt.get("error"),
            "retryable": bool(receipt.get("retryable")),
            "createdAt": receipt.get("createdAt"),
            "updatedAt": receipt.get("updatedAt"),
            "version": max(1, int(version or 1)),
        }

    def _write_receipt(self, connection: Any, *, manifest_id: str, receipt: dict[str, Any], now: str) -> None:
        encoded = canonical_json(receipt)
        digest = sha256_text(encoded)
        connection.execute(
            "UPDATE object_manifests SET content_hash=?,receipt=?,byte_size=?,receipt_hash=?,"
            "availability_state='ready',verified_at=? WHERE id=?",
            (digest, encoded, len(encoded.encode("utf-8")), digest, now, manifest_id),
        )

    def submit(self, identity: SessionIdentity, *, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        project_id = str(payload.get("projectId") or "").strip()
        if not project_id:
            raise RepositoryError(422, "mobile_link_project_required", "请选择项目后再转存")
        source_url, platform = _normalize_link(str(payload.get("url") or ""))
        now = utc_now()
        payload_hash = sha256_text(canonical_json({"projectId": project_id, "sourceUrl": source_url}))
        with self.repository._connection() as connection:  # noqa: SLF001
            self.repository._require_project_access(connection, identity, project_id=project_id)  # noqa: SLF001
            existing = connection.execute(
                "SELECT result_object_manifest_id,payload_hash FROM idempotency_records "
                "WHERE scope_id=? AND idempotency_key=?",
                (identity.scope_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"] or "") != payload_hash:
                    raise RepositoryError(409, "mobile_link_idempotency_conflict", "重复操作的内容不一致")
                manifest = connection.execute(
                    "SELECT receipt FROM object_manifests WHERE id=? AND scope_id=?",
                    (existing["result_object_manifest_id"], identity.scope_id),
                ).fetchone()
                data = json.loads(str(manifest["receipt"] or "{}")) if manifest else {}
                run = connection.execute("SELECT version FROM execution_runs WHERE id=?", (data.get("runId"),)).fetchone()
                return self._public(data, int(run["version"] or 1) if run else 1)
            bot_id = builtin_agent_id(identity.organization_id, "project_workspace")
            if connection.execute(
                "SELECT 1 FROM bot_definitions WHERE id=? AND enabled=1 AND lifecycle_state='active'",
                (bot_id,),
            ).fetchone() is None:
                raise RepositoryError(409, "mobile_link_agent_not_ready", "项目工作台处理能力尚未就绪")
            run_id, manifest_id, operation_id, command_id = new_id(), new_id(), new_id(), new_id()
            receipt = {
                "schema": "yiyu.mobile-link-transfer.v1",
                "runId": run_id,
                "projectId": project_id,
                "sourceUrl": source_url,
                "sourcePlatform": platform,
                "status": "queued",
                "stage": "waiting_for_desktop",
                "title": "",
                "documentId": None,
                "error": None,
                "retryable": True,
                "submittedByMembershipId": identity.membership_id,
                "createdAt": now,
                "updatedAt": now,
            }
            encoded = canonical_json(receipt)
            digest = sha256_text(encoded)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO object_manifests (id,scope_id,storage_key,content_hash,lifecycle_state,receipt,holder_role,holder_instance_id,storage_kind,byte_size,media_type,availability_state,receipt_hash,created_at,verified_at,deleted_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,NULL,?,'active',?,'organization_cloud',?,'mobile_link_transfer_receipt',?,'application/vnd.yiyu.mobile-link-transfer+json','ready',?,?,?,NULL,'cloud',?)",
                    (manifest_id, identity.scope_id, digest, encoded, identity.cloud_instance_id, len(encoded.encode("utf-8")), digest, now, now, identity.cloud_instance_id),
                )
                # Commands reference the idempotency row and execution runs
                # reference the command operation.  Preserve that authority
                # order instead of temporarily disabling foreign keys.
                connection.execute(
                    "INSERT INTO idempotency_records (id,scope_id,idempotency_key,payload_hash,result_hash,expires_at,result_object_manifest_id,status,created_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,?,?,'9999-12-31T23:59:59.999Z',?,'completed',?,'cloud',?)",
                    (new_id(), identity.scope_id, idempotency_key, payload_hash, digest, manifest_id, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "INSERT INTO commands (id,scope_id,operation_id,idempotency_key,aggregate_type,aggregate_id,command_type,actor_principal_id,expected_aggregate_version,device_command_sequence,status,actor_membership_id,payload_object_manifest_id,payload_hash,submitted_at,settled_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,?, 'execution_run',?,'mobile.link_transfer.submitted',?,NULL,NULL,'committed',?,?,?,?,?,'cloud',?)",
                    (command_id, identity.scope_id, operation_id, idempotency_key, run_id, identity.principal_id, identity.membership_id, manifest_id, payload_hash, now, now, identity.cloud_instance_id),
                )
                connection.execute(
                    "INSERT INTO execution_runs (id,scope_id,bot_id,rule_id,task_id,operation_id,status,initiator_membership_id,proposal_id,run_kind,progress_object_manifest_id,result_object_manifest_id,started_at,finished_at,version,lifecycle_state,created_at,updated_at,deleted_at) "
                    "VALUES (?,?,?,NULL,NULL,?,'queued',?,NULL,'mobile_link_transfer',?,?,NULL,NULL,1,'active',?,?,NULL)",
                    (run_id, identity.scope_id, bot_id, operation_id, identity.membership_id, manifest_id, manifest_id, now, now),
                )
                connection.execute(
                    "INSERT INTO outbox_events (id,scope_id,operation_id,aggregate_version,event_type,status,aggregate_type,aggregate_id,event_object_manifest_id,event_hash,available_at,published_at,authority_role,origin_instance_id) "
                    "VALUES (?,?,?,1,'mobile.link_transfer.submitted','pending','execution_run',?,?,?, ?,NULL,'cloud',?)",
                    (new_id(), identity.scope_id, operation_id, run_id, manifest_id, digest, now, identity.cloud_instance_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._public(receipt, 1)

    def list(self, identity: SessionIdentity, *, project_id: str | None = None, pending_only: bool = False) -> dict[str, Any]:
        if project_id:
            with self.repository._connection() as connection:  # noqa: SLF001
                self.repository._require_project_access(connection, identity, project_id=project_id)  # noqa: SLF001
        with self.repository._connection() as connection:  # noqa: SLF001
            rows = connection.execute(
                "SELECT run.version,run.status,run.initiator_membership_id,manifest.receipt FROM execution_runs AS run "
                "JOIN object_manifests AS manifest ON manifest.id=run.result_object_manifest_id AND manifest.scope_id=run.scope_id "
                "WHERE run.scope_id=? AND run.run_kind='mobile_link_transfer' AND run.lifecycle_state='active' "
                "ORDER BY run.updated_at DESC LIMIT 100",
                (identity.scope_id,),
            ).fetchall()
        items = []
        for row in rows:
            try:
                receipt = json.loads(str(row["receipt"] or "{}"))
            except json.JSONDecodeError:
                continue
            if project_id and str(receipt.get("projectId") or "") != project_id:
                continue
            if not pending_only and str(row["initiator_membership_id"] or "") != identity.membership_id:
                continue
            if pending_only and str(row["status"] or "") not in {"queued", "running"}:
                continue
            items.append(self._public(receipt, int(row["version"] or 1)))
        return {"transfers": items}

    def claim(self, identity: SessionIdentity, *, run_id: str) -> dict[str, Any]:
        now = utc_now()
        with self.repository._connection() as connection:  # noqa: SLF001
            row, receipt = self._receipt(connection, identity, run_id)
            self.repository._require_project_access(connection, identity, project_id=str(receipt.get("projectId") or ""))  # noqa: SLF001
            if str(row["status"] or "") in {"completed", "blocked", "failed_retryable", "failed"}:
                return self._public(receipt, int(row["version"] or 1))
            receipt.update({"status": "running", "stage": "desktop_processing", "processorMembershipId": identity.membership_id, "updatedAt": now})
            connection.execute("BEGIN IMMEDIATE")
            self._write_receipt(connection, manifest_id=str(row["manifest_id"]), receipt=receipt, now=now)
            connection.execute("UPDATE execution_runs SET status='running',started_at=COALESCE(started_at,?),updated_at=?,version=version+1 WHERE id=?", (now, now, run_id))
            connection.commit()
            version = int(row["version"] or 1) + 1
        return self._public(receipt, version)

    def settle(self, identity: SessionIdentity, *, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        status = str(payload.get("status") or "").strip()
        if status not in {"completed", "failed_retryable", "blocked"}:
            raise RepositoryError(422, "mobile_link_settlement_invalid", "转存终态无效")
        now = utc_now()
        with self.repository._connection() as connection:  # noqa: SLF001
            row, receipt = self._receipt(connection, identity, run_id)
            self.repository._require_project_access(connection, identity, project_id=str(receipt.get("projectId") or ""))  # noqa: SLF001
            receipt.update({
                "status": status,
                "stage": "completed" if status == "completed" else "failed",
                "title": str(payload.get("title") or "")[:200],
                "documentId": str(payload.get("documentId") or "") or None,
                "error": str(payload.get("error") or "")[:1000] or None,
                "retryable": status == "failed_retryable",
                "updatedAt": now,
            })
            connection.execute("BEGIN IMMEDIATE")
            self._write_receipt(connection, manifest_id=str(row["manifest_id"]), receipt=receipt, now=now)
            connection.execute("UPDATE execution_runs SET status=?,finished_at=?,updated_at=?,version=version+1 WHERE id=?", (status, now, now, run_id))
            connection.commit()
            version = int(row["version"] or 1) + 1
        return self._public(receipt, version)
