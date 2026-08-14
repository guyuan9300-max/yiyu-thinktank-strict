from __future__ import annotations

import json
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import quote

import httpx

from strict_common.contracts import CLOUD_CONTRACT, capability_registry
from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.schema import initialize_database, runtime_connection
from strict_common.security import decode_secret_bundle, encode_secret_bundle

from .cloud_client import CloudClient, CloudClientError, CloudClientPool, normalize_cloud_url
from .secret_store import SecretStore, SecretStoreError, secret_fingerprint


class LocalRuntimeError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True)
class WorkspaceContext:
    sandbox_id: str
    cloud_instance_id: str
    organization_id: str
    cloud_api_url: str
    principal_id: str
    membership_id: str
    access_token: str
    refresh_token: str
    access_expires_at: str | None
    refresh_expires_at: str | None


@dataclass(frozen=True)
class PinnedSandboxContext:
    sandbox_id: str
    sandbox_kind: str
    cloud_instance_id: str | None
    organization_id: str | None
    scope_id: str | None = None
    request_seq: int = 0
    workspace_context: WorkspaceContext | None = None


CloudFactory = Callable[[str], CloudClient]


class WorkspaceRuntime:
    """Local 88-table foundation runtime.

    It keeps login and workspace switching alive. Business operations are
    intentionally unavailable until their golden chains are reconnected.
    """

    def __init__(
        self,
        database_path: Path,
        secret_store: SecretStore,
        *,
        cloud_factory: CloudFactory | None = None,
    ):
        self.database_path = database_path.resolve()
        self.identity = initialize_database(self.database_path, "local")
        self.secret_store = secret_store
        self.cloud_factory = cloud_factory or CloudClientPool()
        self._workspace_context_local = threading.local()
        self._request_seq_lock = threading.Lock()
        self._request_seq = int(time.time() * 1000)
        self._local_object_locks_guard = threading.Lock()
        self._local_object_locks: dict[str, threading.RLock] = {}

    def _connection(self):
        return runtime_connection(self.database_path, "local")

    def _current_context(self, *, require_ready: bool = True) -> WorkspaceContext:
        pinned = getattr(self._workspace_context_local, "pinned", None)
        if pinned is None:
            pinned = self.capture_sandbox_context()
        context = pinned.workspace_context
        if context is None:
            if require_ready:
                raise LocalRuntimeError(
                    409,
                    "workspace_not_ready",
                    "当前组织工作空间尚未就绪",
                )
            raise LocalRuntimeError(401, "workspace_login_required", "请先登录组织工作空间")
        return context

    @contextmanager
    def local_storage_object_lock(
        self,
        *,
        sandbox_id: str,
        object_id: str,
    ) -> Iterator[None]:
        context = self._current_context(require_ready=True)
        if context.sandbox_id != sandbox_id:
            raise LocalRuntimeError(409, "local_storage_sandbox_changed", "本机工作空间已切换，请重试")
        lock_key = f"{sandbox_id}\x1f{object_id}"
        with self._local_object_locks_guard:
            lock = self._local_object_locks.setdefault(lock_key, threading.RLock())
        with lock:
            yield

    def _local_object_manifest_id(self, sandbox_id: str, object_id: str) -> str:
        return self._stable_id("localobj", sandbox_id, object_id)

    @staticmethod
    def _local_object_receipt(row: Any) -> dict[str, Any]:
        try:
            receipt = json.loads(str(row["receipt"] or ""))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LocalRuntimeError(409, "local_storage_receipt_invalid", "本机对象回执无法校验") from exc
        if not isinstance(receipt, dict) or receipt.get("schema") != "yiyu.local-object-receipt.v1":
            raise LocalRuntimeError(409, "local_storage_receipt_invalid", "本机对象回执无法校验")
        return receipt

    def _local_object_scope_id(self, connection: Any, sandbox_id: str) -> str:
        row = connection.execute(
            """
            SELECT scope_id FROM sandboxes
            WHERE id=? AND record_kind='sandbox' AND lifecycle_state='active'
            """,
            (sandbox_id,),
        ).fetchone()
        if row is None or not str(row["scope_id"] or ""):
            raise LocalRuntimeError(409, "local_storage_scope_missing", "当前工作空间缺少严格作用域")
        return str(row["scope_id"])

    def _local_object_result(self, row: Any) -> dict[str, Any]:
        receipt = self._local_object_receipt(row)
        return {
            **dict(row),
            "object_id": str(receipt.get("objectId") or ""),
            "objectId": str(receipt.get("objectId") or ""),
            "manifest_id": str(row["id"]),
            "manifestId": str(row["id"]),
            "sandbox_id": str(receipt.get("sandboxId") or ""),
            "sandboxId": str(receipt.get("sandboxId") or ""),
            "version": int(receipt.get("version") or 0),
            "updated_at": str(row["verified_at"] or row["created_at"]),
            "updatedAt": str(row["verified_at"] or row["created_at"]),
        }

    def local_storage_object_get(
        self,
        *,
        sandbox_id: str,
        object_id: str,
        storage_key: str | None = None,
    ) -> dict[str, Any] | None:
        context = self._current_context(require_ready=True)
        if context.sandbox_id != sandbox_id:
            raise LocalRuntimeError(409, "local_storage_sandbox_changed", "本机工作空间已切换，请重试")
        manifest_id = self._local_object_manifest_id(sandbox_id, object_id)
        with self._connection() as connection:
            scope_id = self._local_object_scope_id(connection, sandbox_id)
            sql = (
                "SELECT * FROM object_manifests WHERE id=? AND scope_id=? "
                "AND holder_role='sandbox' AND holder_instance_id=?"
            )
            params: list[Any] = [manifest_id, scope_id, sandbox_id]
            if storage_key is not None:
                sql += " AND storage_key=?"
                params.append(storage_key)
            row = connection.execute(sql, tuple(params)).fetchone()
        return self._local_object_result(row) if row is not None else None

    def local_storage_object_put(
        self,
        *,
        sandbox_id: str,
        object_id: str,
        storage_key: str,
        content_hash: str,
        media_type: str,
        byte_size: int,
        expected_version: int,
        original_path: str | None = None,
    ) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        if context.sandbox_id != sandbox_id:
            raise LocalRuntimeError(409, "local_storage_sandbox_changed", "本机工作空间已切换，请重试")
        manifest_id = self._local_object_manifest_id(sandbox_id, object_id)
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            scope_id = self._local_object_scope_id(connection, sandbox_id)
            current = connection.execute(
                "SELECT * FROM object_manifests WHERE id=?",
                (manifest_id,),
            ).fetchone()
            current_version = 0 if current is None else int(self._local_object_receipt(current).get("version") or 0)
            if current_version != expected_version:
                connection.execute("ROLLBACK")
                raise LocalRuntimeError(409, "local_storage_version_conflict", "本机对象版本已变化，请刷新后重试")
            version = current_version + 1
            receipt = canonical_json(
                {
                    "schema": "yiyu.local-object-receipt.v1",
                    "objectId": object_id,
                    "sandboxId": sandbox_id,
                    "storageKey": storage_key,
                    "version": version,
                }
            )
            created_at = str(current["created_at"]) if current is not None else now
            preserved_original = (
                original_path
                if original_path is not None
                else (str(current["local_original_path"] or "") if current is not None else None)
            )
            values = (
                manifest_id, scope_id, storage_key, content_hash, receipt,
                sandbox_id, byte_size, media_type, sha256_text(receipt),
                created_at, now, self.identity.database_generation_id,
                preserved_original or None,
            )
            connection.execute(
                """
                INSERT INTO object_manifests (
                    id, scope_id, storage_key, content_hash, lifecycle_state,
                    receipt, holder_role, holder_instance_id, storage_kind,
                    byte_size, media_type, availability_state, receipt_hash,
                    created_at, verified_at, deleted_at, authority_role,
                    origin_instance_id, local_original_path
                ) VALUES (?, ?, ?, ?, 'active', ?, 'sandbox', ?, 'managed_local',
                          ?, ?, 'ready', ?, ?, ?, NULL, 'local', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    scope_id=excluded.scope_id,
                    storage_key=excluded.storage_key,
                    content_hash=excluded.content_hash,
                    lifecycle_state='active',
                    receipt=excluded.receipt,
                    holder_role='sandbox',
                    holder_instance_id=excluded.holder_instance_id,
                    storage_kind='managed_local',
                    byte_size=excluded.byte_size,
                    media_type=excluded.media_type,
                    availability_state='ready',
                    receipt_hash=excluded.receipt_hash,
                    verified_at=excluded.verified_at,
                    deleted_at=NULL,
                    authority_role='local',
                    origin_instance_id=excluded.origin_instance_id,
                    local_original_path=excluded.local_original_path
                """,
                values,
            )
            row = connection.execute("SELECT * FROM object_manifests WHERE id=?", (manifest_id,)).fetchone()
            connection.execute("COMMIT")
        return self._local_object_result(row)

    def local_storage_objects_by_media_type(self, *, media_type: str) -> list[dict[str, Any]]:
        context = self._current_context(require_ready=True)
        with self._connection() as connection:
            scope_id = self._local_object_scope_id(connection, context.sandbox_id)
            rows = connection.execute(
                """
                SELECT * FROM object_manifests
                WHERE scope_id=? AND holder_role='sandbox' AND holder_instance_id=?
                  AND media_type=? AND lifecycle_state='active'
                ORDER BY verified_at DESC, id
                """,
                (scope_id, context.sandbox_id, media_type),
            ).fetchall()
        return [self._local_object_result(row) for row in rows]

    def local_storage_object_set_lifecycle(
        self,
        *,
        object_id: str,
        lifecycle_state: str,
    ) -> dict[str, Any]:
        if lifecycle_state not in {"active", "deleted"}:
            raise LocalRuntimeError(422, "local_storage_lifecycle_invalid", "本机对象生命周期无效")
        context = self._current_context(require_ready=True)
        manifest_id = self._local_object_manifest_id(context.sandbox_id, object_id)
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM object_manifests WHERE id=?", (manifest_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise LocalRuntimeError(404, "local_storage_object_missing", "本机对象不存在")
            receipt_data = self._local_object_receipt(row)
            version = int(receipt_data.get("version") or 0) + 1
            receipt_data["version"] = version
            receipt = canonical_json(receipt_data)
            connection.execute(
                """
                UPDATE object_manifests
                SET lifecycle_state=?, availability_state=?, receipt=?,
                    receipt_hash=?, verified_at=?, deleted_at=?
                WHERE id=? AND holder_instance_id=? AND authority_role='local'
                """,
                (
                    lifecycle_state,
                    "ready" if lifecycle_state == "active" else "deleted",
                    receipt,
                    sha256_text(receipt),
                    now,
                    now if lifecycle_state == "deleted" else None,
                    manifest_id,
                    context.sandbox_id,
                ),
            )
            connection.execute("COMMIT")
        return {
            "objectId": object_id,
            "sandboxId": context.sandbox_id,
            "lifecycleState": lifecycle_state,
            "version": version,
            "updatedAt": now,
        }

    def close(self) -> None:
        close = getattr(self.cloud_factory, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _connected_cloud_path_allowed(method: str, path: str) -> bool:
        root = "/api/v2/domain/project-materials/projects"
        if method == "GET":
            if path in {
                "/api/v2/system-governance/recovery-sets",
                "/api/v2/system-governance/release-gates",
                "/api/v2/system-governance/git-mappings",
            }:
                return True
            if path == "/api/v2/platform-integrations/query":
                return True
            if path in {
                "/api/v2/data-center-support/team-sync/stats",
                "/api/v2/data-center-support/evidence-quality",
            }:
                return True
            if path in {
                "/api/v2/organization-access/model",
                "/api/v2/organization-access/settings/transcription-preference",
                "/api/v2/organization-access/settings/tasks",
                "/api/v2/organization-access/settings/client-workspace",
                "/api/v2/organization-access/settings/topics",
                "/api/v2/organization-access/settings/analysis-workbench",
                "/api/v2/organization-access/settings/handbook",
                "/api/v2/organization-access/settings/local-input-memory",
                "/api/v2/organization-access/settings/speech-model/effective",
                "/api/v2/organization-access/settings/object-storage/effective",
                "/api/v2/organization-access/settings/main-chain-stability",
                "/api/v2/organization-access/feishu/bot",
                "/api/v2/organization-access/feishu/member-authorization",
                "/api/v2/organization-access/feishu/delivery-profile",
                "/api/v2/organization-access/members",
                "/api/v2/organization-access/member-candidates",
                "/api/v2/organization-access/management-titles",
                "/api/v2/organization-access/bots",
                "/api/v2/organization-access/activity-logs",
                "/api/v2/organization-access/settings/system-admin",
            }:
                return True
            if re.fullmatch(
                r"/api/v2/organization-access/bots/(?:resolve|[^/]+|[^/]+/permissions)",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/organization-access/bots/(?:[^/]+/task-plans|task-plans/[^/]+/progress)",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/gc13/growth(?:/(?:overview|workbench|badges|ledger|experience-wall|weekly-review-candidates))?",
                path,
            ):
                return True
            if path == "/api/v2/ai-proposals" or re.fullmatch(
                r"/api/v2/ai-proposals/[^/]+(?:/execution-preview)?", path
            ):
                return True
            if path == "/api/v2/ai-execution-runs":
                return True
            if path == "/api/v2/domain/project-materials/intelligence" or re.fullmatch(
                r"/api/v2/domain/project-materials/intelligence/(?:"
                r"focus-directives|refresh-cycle-settings|refresh-runs|"
                r"verification-rules|strategy-extract|items/[^/]+)",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/gc06/(?:event-lines(?:/[^/]+)?|planning-cycles|weekly-reviews|decision-actions|meetings|calendar)",
                path,
            ):
                return True
            if path == "/api/v2/domain/tasks" or re.fullmatch(
                r"/api/v2/domain/tasks/[^/]+(?:/context)?", path
            ):
                return True
            if path == "/api/v2/domain/task-agents/coordination":
                return True
            if path == "/api/v2/domain/task-planning/project-keyword-profiles":
                return True
            if path == "/api/v2/workflow/plan-item-tasks" or path == "/api/v2/gc06/plan-item-tasks":
                return True
            if re.fullmatch(r"/api/v2/gc06/tasks/[^/]+/plan-link", path):
                return True
            if path == "/api/v2/agent-skills" or re.fullmatch(
                r"/api/v2/agent-skills/[^/]+", path
            ):
                return True
            if path == "/api/v2/settings/org-ai-config/runtime-secret":
                return True
            if re.fullmatch(r"/api/v2/projects/[^/]+/knowledge-context", path):
                return True
            if re.fullmatch(r"/api/v2/workbench/projects/[^/]+/narrative", path):
                return True
            if re.fullmatch(r"/api/v2/workbench/projects/[^/]+/memory-manifest", path):
                return True
            if re.fullmatch(r"/api/v2/workbench/projects/[^/]+/reports", path):
                return True
            if re.fullmatch(
                r"/api/v2/workbench/reports/[^/]+(?:/versions)?",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/workbench/projects/[^/]+/official-website",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/domain/project-materials/projects/[^/]+/glossary-attributes",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/domain/project-materials/projects/[^/]+/governance-decisions",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/domain/project-materials/projects/[^/]+/"
                r"(?:glossary-drift-alerts|contradictions|duplicate-documents)",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/workbench/libraries/writing_skill(?:/[^/]+)?",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/workbench/projects/[^/]+/narrative-clarifications",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/workbench/projects/[^/]+/(?:workspace|insights)",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/workbench/projects/[^/]+/(?:texts|suggestion-log|meeting-action-items)",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/workbench/analysis-jobs/[^/]+(?:/stages)?",
                path,
            ):
                return True
            if path == "/api/v2/workbench/strategic-thoughts":
                return True
            if re.fullmatch(
                r"/api/v2/workbench/projects/[^/]+/knowledge-status",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/domain/project-materials/projects/[^/]+/(?:fact-bundle|knowledge-status)",
                path,
            ):
                return True
            if re.fullmatch(
                r"/api/v2/domain/project-materials/projects/[^/]+/documents/[^/]+/reading-preview",
                path,
            ):
                return True
            suffix = path.removeprefix(root)
            return path.startswith(root) and (
                suffix == ""
                or (suffix.startswith("/") and suffix.count("/") == 1)
            )
        if method == "POST":
            return (
                path in {
                    "/api/v2/system-governance/recovery-sets",
                    "/api/v2/system-governance/release-gates",
                    "/api/v2/system-governance/git-mappings",
                    "/api/v2/platform-integrations/command",
                    "/api/v2/data-center-support/resolve",
                    "/api/v2/agent-skills",
                    "/api/v2/ai-proposals",
                    "/api/v2/organization-access/settings/transcription-preference",
                    "/api/v2/organization-access/settings/tasks",
                    "/api/v2/organization-access/settings/client-workspace",
                    "/api/v2/organization-access/settings/topics",
                    "/api/v2/organization-access/settings/analysis-workbench",
                    "/api/v2/organization-access/settings/handbook",
                    "/api/v2/organization-access/settings/local-input-memory",
                    "/api/v2/organization-access/settings/speech-model/test",
                    "/api/v2/organization-access/settings/object-storage/test",
                    "/api/v2/organization-access/settings/main-chain-stability",
                    "/api/v2/organization-access/feishu/bot",
                    "/api/v2/organization-access/feishu/member-authorization/start",
                    "/api/v2/organization-access/feishu/member-authorization/claim",
                    "/api/v2/organization-access/feishu/delivery-profile",
                    "/api/v2/organization-access/bots",
                    "/api/v2/organization-access/membership-applications",
                    "/api/v2/organization-access/settings/system-admin",
                    "/api/v2/gc13/growth/evidence",
                    "/api/v2/gc13/growth/rules",
                    "/api/v2/gc13/growth/rebuild",
                    "/api/v2/gc13/growth/companion-summary",
                    "/api/v2/domain/tasks",
                    "/api/v2/domain/tasks/lists",
                    "/api/v2/domain/tasks/tags",
                    "/api/v2/domain/task-bulk/preflight",
                    "/api/v2/workbench/analysis-jobs",
                    "/api/v2/workbench/strategic-thoughts/refresh",
                }
                or bool(
                    re.fullmatch(
                        r"/api/v2/organization-access/members/[^/]+/(?:enable|disable)",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/organization-access/members/[^/]+/(?:approve|reject|reset-password)",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/organization-access/bots/[^/]+/rotate-token",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/organization-access/bots/(?:[^/]+/task-plans|task-plans/[^/]+/decide)",
                        path,
                    )
                )
                or path == "/api/v2/organization-access/admin/transfer"
                or bool(
                    re.fullmatch(
                        r"/api/v2/data-center-support/evidence-quality/[^/]+/label",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/ai-proposals/[^/]+/(?:approve|reject|execute)",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(r"/api/v2/agent-skills/[^/]+/runs", path)
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/gc13/growth/weekly-review-candidates/[^/]+/(?:confirm|ignore)",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/gc13/growth/evidence/[^/]+/(?:revise|exclude)",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/gc13/growth/(?:experience-wall/[^/]+/like|handbook/[^/]+/mark-reused|pending-captures/[^/]+/state|recommendations/[^/]+/(?:accept|dismiss))",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/domain/project-materials/intelligence/[^/]+/attention",
                        path,
                    )
                )
                or path == "/api/v2/domain/project-materials/intelligence/external-capture"
                or bool(
                    re.fullmatch(
                        r"/api/v2/domain/task-planning/project-keyword-profiles/[^/]+/refresh",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/domain/project-materials/intelligence/items/[^/]+/answers",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/domain/tasks/[^/]+/(?:inbox/(?:accept|return)|transfer|agent-proposals)",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/domain/task-bulk/[^/]+/commit",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/gc06/(?:event-lines(?:/[^/]+/(?:activities|tasks/[^/]+|archive|reopen|delete))?|planning-cycles|weekly-reviews/draft|weekly-reviews/[^/]+/(?:submit|return|reopen)|decision-actions(?:/[^/]+/primary-task)?|meetings(?:/[^/]+/collaboration/(?:accept|reject))?)",
                        path,
                    )
                )
                or path == "/api/v2/workbench/answers"
                or path == "/api/v2/workbench/reports"
                or path == "/api/v2/workbench/libraries/writing_skill"
                or bool(
                    re.fullmatch(
                        r"/api/v2/workbench/projects/[^/]+/suggestion-log",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/workbench/strategic-thoughts/[^/]+/(?:state|review)",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/workbench/projects/[^/]+/todos/[^/]+/(?:complete|cancel|promote)",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/workbench/reports/[^/]+/(?:restore|export-grants)",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/workbench/answers/[^/]+/facts/corrections",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/workbench/projects/[^/]+/strategic-profile/rebuild",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/workbench/projects/[^/]+/official-website/captures",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/workbench/projects/[^/]+/official-website/auto-verify",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/domain/project-materials/projects/[^/]+/"
                        r"glossary-attributes/[^/]+/review",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/domain/project-materials/projects/[^/]+/"
                        r"governance-decisions/[^/]+",
                        path,
                    )
                )
                or bool(
                    re.fullmatch(
                        r"/api/v2/workbench/projects/[^/]+/narrative-clarifications",
                        path,
                    )
                )
                or path == root
                or (
                    path.startswith(f"{root}/")
                    and path.endswith("/materials/register-metadata")
                    and path.removeprefix(root).count("/") == 3
                )
            )
        if method == "PUT":
            return bool(
                path == "/api/v2/organization-access/model"
                or path == "/api/v2/organization-access/settings/transcription-preference"
                or path == "/api/v2/organization-access/settings/speech-model/effective"
                or path == "/api/v2/organization-access/settings/object-storage/effective"
                or path in {
                    "/api/v2/domain/project-materials/intelligence/focus-directives",
                    "/api/v2/domain/project-materials/intelligence/refresh-cycle-settings",
                    "/api/v2/domain/project-materials/intelligence/verification-rules",
                }
                or bool(
                    re.fullmatch(
                        r"/api/v2/domain/task-agents/weekly-plans/[^/]+/[^/]+",
                        path,
                    )
                )
                or re.fullmatch(
                    r"/api/v2/domain/project-materials/projects/[^/]+",
                    path,
                )
                or re.fullmatch(
                    r"/api/v2/workbench/projects/[^/]+/memory-manifest",
                    path,
                )
                or re.fullmatch(
                    r"/api/v2/workbench/libraries/writing_skill/[^/]+",
                    path,
                )
                or re.fullmatch(
                    r"/api/v2/workbench/projects/[^/]+/texts/.+",
                    path,
                )
            )
        if method == "PATCH":
            return bool(
                re.fullmatch(r"/api/v2/agent-skills/[^/]+(?:/enabled)?", path)
                or re.fullmatch(r"/api/v2/workbench/reports/[^/]+", path)
                or re.fullmatch(r"/api/v2/domain/tasks/[^/]+", path)
                or re.fullmatch(r"/api/v2/domain/tasks/lists/[^/]+", path)
                or re.fullmatch(r"/api/v2/domain/tasks/tags/[^/]+", path)
                or re.fullmatch(r"/api/v2/gc06/tasks/[^/]+/plan-link", path)
                or re.fullmatch(
                    r"/api/v2/gc06/(?:event-lines|planning-cycles|decision-actions|meetings)/[^/]+",
                    path,
                )
                or re.fullmatch(
                    r"/api/v2/domain/project-materials/projects/[^/]+/documents/[^/]+/local-metadata",
                    path,
                )
                or re.fullmatch(
                    r"/api/v2/organization-access/members/[^/]+/(?:role|department|management-title)",
                    path,
                )
                or re.fullmatch(r"/api/v2/organization-access/bots/[^/]+", path)
            )
        if method == "DELETE":
            return bool(
                path == "/api/v2/organization-access/feishu/member-authorization"
                or
                re.fullmatch(r"/api/v2/gc06/planning-cycles/[^/]+", path)
                or
                re.fullmatch(r"/api/v2/domain/tasks/[^/]+", path)
                or re.fullmatch(r"/api/v2/domain/tasks/lists/[^/]+", path)
                or re.fullmatch(r"/api/v2/domain/tasks/tags/[^/]+", path)
                or re.fullmatch(
                    r"/api/v2/workbench/libraries/writing_skill/[^/]+",
                    path,
                )
                or re.fullmatch(
                    r"/api/v2/workbench/projects/[^/]+/(?:texts/.+|suggestion-log/[^/]+)",
                    path,
                )
                or re.fullmatch(
                    r"/api/v2/domain/project-materials/projects/[^/]+/documents/[^/]+",
                    path,
                )
            )
        return False

    def _connected_cloud_request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        query: Mapping[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        method = method.upper()
        if not self._connected_cloud_path_allowed(method, path):
            raise LocalRuntimeError(
                501,
                "golden_chain_frozen",
                "该组织云操作尚未进入已接通的黄金链",
            )
        context = self._current_context(require_ready=True)
        with self._connection() as connection:
            sandbox = connection.execute(
                "SELECT * FROM sandboxes WHERE id=? AND record_kind='sandbox'",
                (context.sandbox_id,),
            ).fetchone()
        if sandbox is None:
            raise LocalRuntimeError(409, "workspace_context_stale", "工作空间已变化，请重试")
        session, secret_reference, bundle = self._load_session_bundle(sandbox)
        client = self.cloud_factory(context.cloud_api_url)

        def execute(access_token: str) -> Any:
            return client.request_v2(
                method,
                path,
                access_token=access_token,
                query_params=dict(query or {}),
                json_body=dict(payload or {}) if method != "GET" else None,
                idempotency_key=idempotency_key,
                allow_array=method == "GET",
            )

        try:
            return execute(str(bundle["accessToken"]))
        except CloudClientError as exc:
            if exc.status_code != 401 and exc.code not in {
                "authorization_lease_expired",
                "authorization_projection_missing",
                "authorization_projection_stale",
            }:
                raise LocalRuntimeError(exc.status_code, exc.code, exc.message) from exc
        try:
            _, _, refreshed_bundle = self._refresh_local_session(
                sandbox=sandbox,
                session=session,
                secret_reference=secret_reference,
                bundle=bundle,
                client=client,
                # Refresh tokens rotate after every successful refresh.  The
                # idempotency key therefore has to identify the token being
                # consumed, not the long-lived sandbox/session.  Reusing one
                # key for a later token is a different command and correctly
                # conflicts on the cloud.
                idempotency_key=(
                    "gc01-connected-refresh-"
                    + sha256_text(str(bundle.get("refreshToken") or ""))[:32]
                ),
            )
            return execute(str(refreshed_bundle["accessToken"]))
        except CloudClientError as exc:
            raise LocalRuntimeError(exc.status_code, exc.code, exc.message) from exc

    def cloud_query(
        self,
        path: str,
        *,
        query: Mapping[str, str] | None = None,
    ) -> Any:
        return self._connected_cloud_request("GET", path, query=query)

    def project_agent_skill(self, item: Mapping[str, Any]) -> None:
        """Project one cloud-authoritative declarative Skill into the local 88 tables."""
        skill_id = str(item.get("skillId") or "").strip()
        if not skill_id:
            raise LocalRuntimeError(502, "agent_skill_projection_invalid", "组织云 Skill 缺少标识")
        context = self._current_context(require_ready=True)
        sandbox = self.capture_sandbox_context()
        scope_id = str(sandbox.scope_id or "")
        authorization = item.get("authorizationProjection")
        if not isinstance(authorization, Mapping):
            raise LocalRuntimeError(409, "agent_skill_authorization_missing", "组织云 Skill 缺少权限投影")
        policy_version_id = str(authorization.get("policyVersionId") or "").strip()
        viewer_principal_id = str(authorization.get("viewerPrincipalId") or "").strip()
        viewer_membership_id = str(authorization.get("viewerMembershipId") or "").strip()
        viewer_grant_id = str(authorization.get("viewerGrantId") or "").strip()
        if (
            not policy_version_id
            or not viewer_grant_id
            or viewer_principal_id != context.principal_id
            or viewer_membership_id != context.membership_id
            or "use" not in set(authorization.get("viewerCapabilities") or [])
        ):
            raise LocalRuntimeError(409, "agent_skill_authorization_mismatch", "Skill 权限投影与当前身份不一致")
        now = utc_now()
        version = max(1, int(item.get("version") or 1))
        trigger_spec = canonical_json(
            {
                "schema": "yiyu.agent-skill-trigger.v1",
                "agentKinds": list(item.get("agentKinds") or []),
            }
        )
        action_spec = canonical_json(
            {
                "schema": "yiyu.agent-skill-action.v1",
                "shortName": str(item.get("shortName") or ""),
                "description": str(item.get("description") or ""),
                "instructions": list(item.get("instructions") or []),
                "outputTemplate": item.get("outputTemplate"),
                "allowedToolIds": list(item.get("allowedToolIds") or []),
                "visibility": str(item.get("visibility") or "private"),
                "departmentId": item.get("departmentId"),
                "granteeMembershipIds": list(item.get("granteeMembershipIds") or []),
                "publisherPrincipalId": str(item.get("publisherPrincipalId") or ""),
                "publisherMembershipId": str(item.get("publisherMembershipId") or ""),
            }
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO secured_resources (
                    id, scope_id, resource_kind, lifecycle_state, version,
                    resource_type_key, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, 'automation_rule', 'active', ?, 'agent_skill',
                          ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    scope_id=excluded.scope_id, resource_kind='automation_rule',
                    lifecycle_state='active', version=excluded.version,
                    resource_type_key='agent_skill', updated_at=excluded.updated_at,
                    deleted_at=NULL, authority_role='cloud',
                    origin_instance_id=excluded.origin_instance_id
                """,
                (skill_id, scope_id, version, now, now, context.cloud_instance_id),
            )
            connection.execute(
                "UPDATE policy_versions SET lifecycle_state='archived',updated_at=?,"
                "projection_state='stale',stale_at=? WHERE scope_id=? "
                "AND secured_resource_id=? AND id<>? AND lifecycle_state='active'",
                (now, now, scope_id, skill_id, policy_version_id),
            )
            raw_policy_spec = authorization.get("policySpec")
            try:
                policy_spec = canonical_json(
                    json.loads(str(raw_policy_spec or "{}"))
                )
            except (TypeError, ValueError):
                raise LocalRuntimeError(502, "agent_skill_policy_invalid", "组织云 Skill 权限版本无效")
            connection.execute(
                """
                INSERT INTO policy_versions (
                    id,scope_id,secured_resource_id,policy_scope_kind,version,
                    policy_spec_schema_version,policy_spec,effective_at,created_at,
                    lifecycle_state,updated_at,deleted_at,sandbox_id,source_version,
                    projection_state,projected_at,stale_at,lease_expires_at
                ) VALUES (?,?,?,'secured_resource',?,?,?,?,?,'active',?,NULL,?,?,
                          'fresh',?,NULL,?)
                ON CONFLICT(id) DO UPDATE SET
                    scope_id=excluded.scope_id,
                    secured_resource_id=excluded.secured_resource_id,
                    policy_scope_kind='secured_resource',
                    version=excluded.version,
                    policy_spec_schema_version=excluded.policy_spec_schema_version,
                    policy_spec=excluded.policy_spec,
                    effective_at=excluded.effective_at,
                    lifecycle_state='active',updated_at=excluded.updated_at,
                    deleted_at=NULL,sandbox_id=excluded.sandbox_id,
                    source_version=excluded.source_version,
                    projection_state='fresh',projected_at=excluded.projected_at,
                    stale_at=NULL,lease_expires_at=excluded.lease_expires_at
                """,
                (
                    policy_version_id,
                    scope_id,
                    skill_id,
                    max(1, int(authorization.get("policyVersion") or 1)),
                    str(authorization.get("policySpecSchemaVersion") or "yiyu.agent-skill-policy.v1"),
                    policy_spec,
                    str(authorization.get("generatedAt") or now),
                    str(authorization.get("generatedAt") or now),
                    now,
                    sandbox.sandbox_id,
                    max(1, int(authorization.get("policyVersion") or 1)),
                    now,
                    authorization.get("leaseExpiresAt"),
                ),
            )
            connection.execute(
                """
                INSERT INTO automation_rules (
                    id, scope_id, template_key, rule_version, trigger_spec,
                    record_kind, trigger_spec_schema_version,
                    action_spec_schema_version, action_spec,
                    trusted_source_pattern, enabled, effective_at, version,
                    lifecycle_state, created_at, updated_at, deleted_at,
                    sandbox_id, source_version, projection_state, projected_at,
                    stale_at, lease_expires_at
                ) VALUES (?, ?, ?, ?, ?, 'agent_skill',
                          'yiyu.agent-skill-trigger.v1',
                          'yiyu.agent-skill-action.v1', ?, NULL, ?, ?, ?,
                          'active', ?, ?, NULL, ?, ?, 'current', ?, NULL, NULL)
                ON CONFLICT(id) DO UPDATE SET
                    scope_id=excluded.scope_id, template_key=excluded.template_key,
                    rule_version=excluded.rule_version,
                    trigger_spec=excluded.trigger_spec,
                    trigger_spec_schema_version=excluded.trigger_spec_schema_version,
                    action_spec_schema_version=excluded.action_spec_schema_version,
                    action_spec=excluded.action_spec, enabled=excluded.enabled,
                    effective_at=excluded.effective_at, version=excluded.version,
                    lifecycle_state='active', updated_at=excluded.updated_at,
                    deleted_at=NULL, sandbox_id=excluded.sandbox_id,
                    source_version=excluded.source_version,
                    projection_state='current', projected_at=excluded.projected_at,
                    stale_at=NULL, lease_expires_at=NULL
                """,
                (
                    skill_id, scope_id,
                    f"{item.get('publisherPrincipalId') or ''}:{item.get('shortName') or ''}",
                    version, trigger_spec, action_spec,
                    1 if bool(item.get("enabled")) else 0,
                    now, version, now, now, sandbox.sandbox_id, version, now,
                ),
            )
            viewer_projection_id = self._stable_id(
                "viewer_skill",
                sandbox.sandbox_id,
                skill_id,
                viewer_membership_id,
            )
            connection.execute(
                "UPDATE viewer_projections SET invalidated_at=?,projection_state='stale',"
                "stale_at=? WHERE scope_id=? AND secured_resource_id=? "
                "AND viewer_membership_id=? AND sandbox_id=? AND id<>? "
                "AND invalidated_at IS NULL",
                (
                    now,now,scope_id,skill_id,viewer_membership_id,
                    sandbox.sandbox_id,viewer_projection_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO viewer_projections (
                    id,scope_id,secured_resource_id,viewer_principal_id,
                    viewer_membership_id,policy_version_id,viewer_surfaces,
                    viewer_capabilities,viewer_surfaces_schema_version,
                    viewer_capabilities_schema_version,lease_expires_at,
                    generated_at,source_version,invalidated_at,sandbox_id,
                    projection_state,projected_at,stale_at
                ) VALUES (?,?,?,?,?,?,?,?, '1','1',?,?,?,NULL,?,'fresh',?,NULL)
                ON CONFLICT(id) DO UPDATE SET
                    scope_id=excluded.scope_id,
                    secured_resource_id=excluded.secured_resource_id,
                    viewer_principal_id=excluded.viewer_principal_id,
                    viewer_membership_id=excluded.viewer_membership_id,
                    policy_version_id=excluded.policy_version_id,
                    viewer_surfaces=excluded.viewer_surfaces,
                    viewer_capabilities=excluded.viewer_capabilities,
                    lease_expires_at=excluded.lease_expires_at,
                    generated_at=excluded.generated_at,
                    source_version=excluded.source_version,
                    invalidated_at=NULL,sandbox_id=excluded.sandbox_id,
                    projection_state='fresh',projected_at=excluded.projected_at,
                    stale_at=NULL
                """,
                (
                    viewer_projection_id,
                    scope_id,
                    skill_id,
                    viewer_principal_id,
                    viewer_membership_id,
                    policy_version_id,
                    canonical_json(list(authorization.get("viewerSurfaces") or [])),
                    canonical_json(list(authorization.get("viewerCapabilities") or [])),
                    authorization.get("leaseExpiresAt"),
                    str(authorization.get("generatedAt") or now),
                    max(1, int(authorization.get("policyVersion") or 1)),
                    sandbox.sandbox_id,
                    now,
                ),
            )
            connection.execute("COMMIT")

    def reconcile_agent_skill_projections(self, visible_skill_ids: list[str]) -> None:
        """Invalidate local Skill projections omitted by the current cloud decision."""
        context = self._current_context(require_ready=True)
        sandbox = self.capture_sandbox_context()
        scope_id = str(sandbox.scope_id or "")
        visible = {str(item).strip() for item in visible_skill_ids if str(item).strip()}
        now = utc_now()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id FROM automation_rules WHERE scope_id=? AND sandbox_id=? "
                "AND record_kind='agent_skill' AND lifecycle_state='active'",
                (scope_id, context.sandbox_id),
            ).fetchall()
            missing = {str(row["id"]) for row in rows} - visible
            if not missing:
                return
            placeholders = ",".join("?" for _ in missing)
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"UPDATE automation_rules SET enabled=0,projection_state='stale',"
                f"stale_at=?,updated_at=? WHERE id IN ({placeholders}) "
                "AND scope_id=? AND sandbox_id=?",
                (now, now, *sorted(missing), scope_id, context.sandbox_id),
            )
            connection.execute(
                f"UPDATE viewer_projections SET invalidated_at=?,projection_state='stale',"
                f"stale_at=? WHERE secured_resource_id IN ({placeholders}) "
                "AND scope_id=? AND sandbox_id=? AND invalidated_at IS NULL",
                (now, now, *sorted(missing), scope_id, context.sandbox_id),
            )
            connection.commit()

    def project_knowledge_context(self, project_id: str) -> dict[str, Any]:
        result = self._connected_cloud_request(
            "GET",
            f"/api/v2/projects/{project_id}/knowledge-context",
        )
        if not isinstance(result, dict):
            raise LocalRuntimeError(
                502,
                "project_knowledge_context_invalid",
                "组织云返回的项目知识上下文无效",
            )
        return result

    def _invalidate_project_access_projection(
        self,
        project_id: str,
        *,
        reason: str,
    ) -> dict[str, int]:
        """Invalidate the current sandbox's project-derived access state.

        A successful cloud denial or a successful project-list omission is an
        authoritative revocation signal.  Local source files remain on disk,
        but no consumer may continue through their stale project projection.
        """

        context = self._current_context(require_ready=True)
        pinned = self.capture_sandbox_context(
            expected_sandbox_id=context.sandbox_id,
        )
        scope_id = str(pinned.scope_id or "")
        normalized_project_id = str(project_id or "").strip()
        if not scope_id or not normalized_project_id:
            raise LocalRuntimeError(
                409,
                "project_revocation_scope_missing",
                "项目撤权投影缺少稳定作用域",
            )
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                viewer_rows = connection.execute(
                    "SELECT id FROM viewer_projections WHERE scope_id=? "
                    "AND secured_resource_id=? AND viewer_membership_id=? "
                    "AND sandbox_id=?",
                    (
                        scope_id,
                        normalized_project_id,
                        context.membership_id,
                        context.sandbox_id,
                    ),
                ).fetchall()
                viewer_ids = {str(row["id"]) for row in viewer_rows}
                viewer_count = int(
                    connection.execute(
                        "UPDATE viewer_projections SET invalidated_at=?,"
                        "projection_state='stale',stale_at=? WHERE scope_id=? "
                        "AND secured_resource_id=? AND viewer_membership_id=? "
                        "AND sandbox_id=? AND invalidated_at IS NULL",
                        (
                            now,
                            now,
                            scope_id,
                            normalized_project_id,
                            context.membership_id,
                            context.sandbox_id,
                        ),
                    ).rowcount
                    or 0
                )
                client_count = int(
                    connection.execute(
                        "UPDATE clients SET projection_state='stale',stale_at=?,"
                        "updated_at=? WHERE id=? AND scope_id=? AND sandbox_id=? "
                        "AND projection_state!='stale'",
                        (
                            now,
                            now,
                            normalized_project_id,
                            scope_id,
                            context.sandbox_id,
                        ),
                    ).rowcount
                    or 0
                )
                policy_count = int(
                    connection.execute(
                        "UPDATE policy_versions SET lifecycle_state='archived',"
                        "projection_state='stale',stale_at=?,updated_at=? "
                        "WHERE scope_id=? AND secured_resource_id=? AND sandbox_id=? "
                        "AND lifecycle_state='active'",
                        (
                            now,
                            now,
                            scope_id,
                            normalized_project_id,
                            context.sandbox_id,
                        ),
                    ).rowcount
                    or 0
                )
                lineage_ids: set[str] = set()
                if viewer_ids:
                    placeholders = ",".join("?" for _ in viewer_ids)
                    lineage_ids = {
                        str(row["id"])
                        for row in connection.execute(
                            "SELECT id FROM derivation_lineage WHERE scope_id=? "
                            f"AND derivative_object_id IN ({placeholders})",
                            (scope_id, *sorted(viewer_ids)),
                        ).fetchall()
                    }
                lineage_count = 0
                search_count = 0
                vector_count = 0
                context_count = 0
                cache_count = 0
                if lineage_ids:
                    placeholders = ",".join("?" for _ in lineage_ids)
                    lineage_args = (now, scope_id, *sorted(lineage_ids))
                    lineage_count = int(
                        connection.execute(
                            "UPDATE derivation_lineage SET invalidated_at=? "
                            "WHERE scope_id=? "
                            f"AND id IN ({placeholders}) AND invalidated_at IS NULL",
                            lineage_args,
                        ).rowcount
                        or 0
                    )
                    search_count = int(
                        connection.execute(
                            "UPDATE search_index_manifests SET status='invalidated',"
                            "invalidated_at=? WHERE scope_id=? "
                            f"AND lineage_id IN ({placeholders}) "
                            "AND invalidated_at IS NULL",
                            lineage_args,
                        ).rowcount
                        or 0
                    )
                    vector_count = int(
                        connection.execute(
                            "UPDATE vector_index_manifests SET status='invalidated',"
                            "invalidated_at=? WHERE scope_id=? "
                            f"AND lineage_id IN ({placeholders}) "
                            "AND invalidated_at IS NULL",
                            lineage_args,
                        ).rowcount
                        or 0
                    )
                    context_count = int(
                        connection.execute(
                            "UPDATE ai_context_manifests SET status='invalidated',"
                            "invalidated_at=? WHERE scope_id=? "
                            f"AND lineage_id IN ({placeholders}) "
                            "AND invalidated_at IS NULL",
                            lineage_args,
                        ).rowcount
                        or 0
                    )
                    cache_count = int(
                        connection.execute(
                            "UPDATE cache_entries SET invalidated_at=? WHERE scope_id=? "
                            f"AND lineage_id IN ({placeholders}) "
                            "AND invalidated_at IS NULL",
                            lineage_args,
                        ).rowcount
                        or 0
                    )
                export_sql = (
                    "UPDATE export_grants SET status='revoked',revoked_at=?,"
                    "version=version+1,updated_at=?,projection_state='stale',"
                    "stale_at=? WHERE scope_id=? AND sandbox_id=? "
                    "AND grantee_membership_id=? AND status='active' "
                    "AND lifecycle_state='active' AND (source_set_id IN "
                    "(SELECT id FROM source_sets WHERE scope_id=? AND client_id=?)"
                )
                export_args: list[Any] = [
                    now,
                    now,
                    now,
                    scope_id,
                    context.sandbox_id,
                    context.membership_id,
                    scope_id,
                    normalized_project_id,
                ]
                if lineage_ids:
                    placeholders = ",".join("?" for _ in lineage_ids)
                    export_sql += f" OR lineage_id IN ({placeholders})"
                    export_args.extend(sorted(lineage_ids))
                export_sql += ")"
                export_count = int(
                    connection.execute(export_sql, tuple(export_args)).rowcount or 0
                )
                counts = {
                    "clients": client_count,
                    "policies": policy_count,
                    "viewerProjections": viewer_count,
                    "lineages": lineage_count,
                    "searchIndexes": search_count,
                    "vectorIndexes": vector_count,
                    "aiContexts": context_count,
                    "cacheEntries": cache_count,
                    "exportGrants": export_count,
                }
                mismatch_count = sum(counts.values())
                reconciliation_id = self._stable_id(
                    "recon_gc02_revoke",
                    context.sandbox_id,
                    normalized_project_id,
                    context.membership_id,
                )
                connection.execute(
                    "INSERT INTO reconciliation_runs (id,scope_id,operation_id,"
                    "registry_state_id,mismatch_count,status,reconciliation_kind,"
                    "target_instance_id,result_object_manifest_id,started_at,"
                    "completed_at,version,lifecycle_state,created_at,updated_at,"
                    "deleted_at,authority_role,origin_instance_id) VALUES "
                    "(?,?,NULL,NULL,?,'completed',?, ?,NULL,?,?,1,'active',?,?,NULL,"
                    "'local_projection',?) ON CONFLICT(id) DO UPDATE SET "
                    "operation_id=excluded.operation_id,mismatch_count=excluded.mismatch_count,"
                    "status='completed',reconciliation_kind=excluded.reconciliation_kind,"
                    "target_instance_id=excluded.target_instance_id,"
                    "completed_at=excluded.completed_at,updated_at=excluded.updated_at",
                    (
                        reconciliation_id,
                        scope_id,
                        mismatch_count,
                        f"gc02_project_access_revoked:{reason}",
                        context.cloud_instance_id,
                        now,
                        now,
                        now,
                        now,
                        context.cloud_instance_id,
                    ),
                )
                connection.commit()
                return counts
            except Exception:
                connection.rollback()
                raise

    def reconcile_project_projections(self, visible_project_ids: list[str]) -> None:
        """Invalidate local project projections omitted by a successful cloud list."""

        context = self._current_context(require_ready=True)
        pinned = self.capture_sandbox_context(
            expected_sandbox_id=context.sandbox_id,
        )
        scope_id = str(pinned.scope_id or "")
        visible = {
            str(project_id).strip()
            for project_id in visible_project_ids
            if str(project_id or "").strip()
        }
        with self._connection() as connection:
            local_ids = {
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM clients WHERE scope_id=? AND sandbox_id=? "
                    "AND lifecycle_state!='deleted' AND projection_state!='stale'",
                    (scope_id, context.sandbox_id),
                ).fetchall()
            }
        for project_id in sorted(local_ids - visible):
            self._invalidate_project_access_projection(
                project_id,
                reason="cloud_list_omission",
            )

    def require_project_capability(
        self,
        project_id: str,
        capability: str = "read",
    ) -> dict[str, Any]:
        """Resolve every project consumer through the GC-02 authorization gate.

        The cloud project detail is the current decision point because it reads
        ``clients -> secured_resources -> policy_versions -> object_grants`` and
        returns the viewer-specific authorization projection. A cached local
        client row alone is never sufficient evidence of current access.
        """

        normalized_project_id = str(project_id or "").strip()
        normalized_capability = str(capability or "read").strip() or "read"
        if not normalized_project_id:
            raise LocalRuntimeError(422, "project_required", "必须明确选择项目")
        try:
            result = self.cloud_query(
                "/api/v2/domain/project-materials/projects/"
                + quote(normalized_project_id, safe="")
            )
        except LocalRuntimeError as exc:
            if exc.status_code in {403, 404}:
                self._invalidate_project_access_projection(
                    normalized_project_id,
                    reason=exc.code,
                )
            raise
        project = result.get("project") if isinstance(result, Mapping) else None
        if (
            not isinstance(project, Mapping)
            or str(project.get("projectId") or "") != normalized_project_id
        ):
            raise LocalRuntimeError(
                502,
                "project_authorization_response_invalid",
                "组织云返回的项目授权结构无效",
            )
        authorization = project.get("authorizationProjection")
        if not isinstance(authorization, Mapping):
            raise LocalRuntimeError(
                502,
                "project_authorization_projection_missing",
                "组织云未返回项目权限投影",
            )
        context = self._current_context(require_ready=True)
        if (
            str(authorization.get("viewerPrincipalId") or "")
            != context.principal_id
            or str(authorization.get("viewerMembershipId") or "")
            != context.membership_id
        ):
            raise LocalRuntimeError(
                409,
                "project_authorization_identity_mismatch",
                "项目权限投影与当前登录身份不一致",
            )
        # This projection came from the successful online project query
        # immediately above. The cloud membership/grant check is authoritative;
        # the legacy lease timestamp remains diagnostic metadata only.
        capabilities = {
            str(value)
            for value in authorization.get("viewerCapabilities") or []
            if str(value or "").strip()
        }
        if normalized_capability not in capabilities:
            raise LocalRuntimeError(
                403,
                "project_capability_blocked",
                "当前成员没有执行该项目操作的权限",
            )

        # Project only a validated decision. Local file bodies and paths remain
        # in the current sandbox and are never part of this cloud response.
        from .project_materials_local import LocalProjectMaterialsRepository

        LocalProjectMaterialsRepository(self).ensure_project_projection(project)
        return dict(project)

    def cloud_command(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
        refresh_business: bool = True,
    ) -> dict[str, Any]:
        del refresh_business
        result = self._connected_cloud_request(
            method,
            path,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        if not isinstance(result, dict):
            raise LocalRuntimeError(502, "cloud_response_invalid", "组织云返回结构不完整")
        return result

    def _organization_ai_runtime_secret(self) -> dict[str, Any]:
        context = self._current_context(require_ready=True)
        result = self._connected_cloud_request(
            "GET",
            "/api/v2/settings/org-ai-config/runtime-secret",
        )
        if not isinstance(result, dict):
            raise LocalRuntimeError(
                502,
                "organization_ai_config_invalid",
                "组织模型配置响应无效",
            )
        if (
            str(result.get("organizationId") or "") != context.organization_id
            or str(result.get("cloudInstanceId") or "") != context.cloud_instance_id
        ):
            raise LocalRuntimeError(
                409,
                "organization_ai_identity_mismatch",
                "组织模型配置与当前工作空间不一致",
            )
        if (
            str(result.get("status") or "") != "ready"
            or not str(result.get("configId") or "")
            or not str(result.get("baseUrl") or "")
            or not str(result.get("modelName") or "")
            or not str(result.get("apiKey") or "")
        ):
            raise LocalRuntimeError(
                409,
                "organization_ai_not_ready",
                "组织尚未配置可用的大模型",
            )
        return result

    def organization_ai_runtime_status(self) -> dict[str, Any]:
        """Return the active organization's model status without exposing its secret."""
        try:
            provider = self._organization_ai_runtime_secret()
        except LocalRuntimeError as exc:
            return {
                "state": (
                    "failed_retryable"
                    if exc.status_code >= 500
                    else "blocked"
                ),
                "source": "organization_direct",
                "provider": "",
                "providerLabel": "",
                "model": "",
                "configVersion": "",
                "fingerprint": None,
                "syncedAt": None,
                "lastError": exc.message,
                "usingCachedConfig": False,
            }
        provider_key = str(provider.get("provider") or "openai_compatible")
        model_name = str(provider.get("modelName") or "")
        normalized_model = model_name.casefold().replace(".", "-").replace("_", "-")
        if "doubao" in normalized_model and "seed" in normalized_model:
            provider_label = (
                "豆包 Seed 2.1 Pro"
                if "2-1" in normalized_model
                else "豆包 Seed Pro"
            )
        elif provider_key == "doubao":
            provider_label = "豆包"
        else:
            provider_label = str(
                provider.get("providerLabel")
                or model_name
                or provider_key
                or "组织统一模型"
            )
        return {
            "state": "ready_direct",
            "source": "organization_direct",
            "provider": provider_key,
            "providerLabel": provider_label,
            "model": model_name,
            "configVersion": str(provider.get("version") or ""),
            "fingerprint": provider.get("keyFingerprint"),
            "syncedAt": provider.get("updatedAt"),
            "lastError": None,
            "usingCachedConfig": False,
        }

    @staticmethod
    def _invoke_organization_ai(
        provider: Mapping[str, Any],
        *,
        messages: list[dict[str, str]],
        temperature: float,
        read_timeout_seconds: float = 45.0,
    ) -> str:
        base_url = str(provider.get("baseUrl") or "").strip().rstrip("/")
        endpoint = (
            base_url
            if base_url.endswith("/chat/completions")
            else f"{base_url}/chat/completions"
        )
        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=min(100.0, max(5.0, float(read_timeout_seconds))),
                    write=15.0,
                    pool=5.0,
                ),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = client.post(
                    endpoint,
                    headers={
                        "Authorization": f"Bearer {provider['apiKey']}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": str(provider["modelName"]),
                        "messages": messages,
                        "temperature": temperature,
                        # 基础问答走低延迟通道；深度思考仍是明确的待接能力。
                        "thinking": {"type": "disabled"},
                        "max_tokens": 2_048,
                        "stream": False,
                    },
                )
        except httpx.TimeoutException as exc:
            raise LocalRuntimeError(
                504,
                "organization_ai_timeout",
                "组织模型响应超时，可以重试",
            ) from exc
        except httpx.HTTPError as exc:
            raise LocalRuntimeError(
                503,
                "organization_ai_unreachable",
                "组织模型暂时无法连接，可以重试",
            ) from exc
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise LocalRuntimeError(
                502,
                "organization_ai_response_invalid",
                "组织模型返回了无效响应",
            ) from exc
        if response.status_code >= 400:
            retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
            raise LocalRuntimeError(
                503 if retryable else response.status_code,
                "organization_ai_failed_retryable" if retryable else "organization_ai_rejected",
                (
                    "组织模型暂时失败，可以重试"
                    if retryable
                    else f"组织模型拒绝了请求（{response.status_code}）"
                ),
            )
        try:
            content = str(response_payload["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LocalRuntimeError(
                502,
                "organization_ai_response_invalid",
                "组织模型返回了无效回答",
            ) from exc
        if not content:
            raise LocalRuntimeError(502, "organization_ai_response_empty", "组织模型返回了空回答")
        return content

    def organization_ai_completion(
        self,
        *,
        messages: list[dict[str, str]],
        temperature: float,
        read_timeout_seconds: float = 45.0,
    ) -> dict[str, Any]:
        captured = self.capture_sandbox_context()
        provider = self._organization_ai_runtime_secret()
        content = self._invoke_organization_ai(
            provider,
            messages=messages,
            temperature=temperature,
            read_timeout_seconds=read_timeout_seconds,
        )
        current = self.capture_sandbox_context()
        if (
            current.sandbox_id != captured.sandbox_id
            or current.organization_id != captured.organization_id
            or current.request_seq < captured.request_seq
        ):
            raise LocalRuntimeError(
                409,
                "workspace_context_changed",
                "回答生成期间工作空间已切换，本次结果未保存",
            )
        safe_provider = {
            key: provider.get(key)
            for key in (
                "configId",
                "provider",
                "baseUrl",
                "modelName",
                "keyFingerprint",
                "status",
                "version",
                "organizationId",
                "cloudInstanceId",
            )
        }
        return {"content": content, "provider": safe_provider}

    def private_ai_completion(
        self,
        *,
        system_prompt: str,
        prompt: str,
        creativity_mode: str = "balanced",
        capability: str = "deep_analysis",
        read_timeout_seconds: float = 45.0,
    ) -> dict[str, Any]:
        del capability
        normalized = prompt.strip()
        if not normalized:
            raise LocalRuntimeError(422, "ai_prompt_required", "缺少要处理的文字")
        completion = self.organization_ai_completion(
            messages=[
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": normalized},
            ],
            temperature=(
                0.8
                if creativity_mode == "creative"
                else 0.1
                if creativity_mode == "strict"
                else 0.3
            ),
            read_timeout_seconds=read_timeout_seconds,
        )
        provider = dict(completion["provider"])
        return {
            "content": completion["content"],
            "modelName": provider.get("modelName"),
            "routeProfile": "organization_main",
            "routingMode": "main_only",
            "sourceScope": "member_local_private_request",
            "persistedToOrganizationCloud": False,
        }

    def workbench_chat(self, **kwargs: Any) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).run(**kwargs)

    def workbench_answer(self, answer_id: str) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).answer(answer_id)

    def workbench_chat_history(self, client_id: str, thread_id: str) -> list[dict[str, Any]]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).thread(client_id, thread_id)

    def workbench_project_answers(self, client_id: str) -> list[dict[str, Any]]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).project_answers(client_id)

    def workbench_save_answer_memory(self, **kwargs: Any) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).save_answer_memory(**kwargs)

    def workbench_revoke_answer_memory(self, **kwargs: Any) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).revoke_answer_memory(**kwargs)

    def workbench_memory_sync_status(self, **kwargs: Any) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).memory_sync_status(**kwargs)

    def workbench_prepare_memory_sync(self, **kwargs: Any) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).prepare_memory_sync(**kwargs)

    def workbench_correct_answer_fact(self, **kwargs: Any) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).correct_answer_fact(**kwargs)

    def workbench_remember_answer_fact(self, **kwargs: Any) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).remember_answer_fact(**kwargs)

    def workbench_project_strategic_profile(
        self,
        profile: Mapping[str, Any],
    ) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).project_strategic_profile(profile)

    def workbench_rebuild_strategic_profile(self, **kwargs: Any) -> dict[str, Any]:
        from .workbench_chat_local import LocalWorkbenchChatRepository

        return LocalWorkbenchChatRepository(self).rebuild_strategic_profile(**kwargs)

    @staticmethod
    def _stable_id(prefix: str, *parts: str) -> str:
        return f"{prefix}_{sha256_text(chr(31).join(parts))[:30]}"

    def _session_snapshot_for(self, connection, sandbox_id: str):
        return connection.execute(
            """
            SELECT * FROM sandboxes
            WHERE id=? AND record_kind='local_session_snapshot'
              AND lifecycle_state!='deleted'
            """,
            (f"session_snapshot_{sandbox_id}",),
        ).fetchone()

    def _load_session_bundle(self, sandbox) -> tuple[Any, str, dict[str, Any]]:
        with self._connection() as connection:
            session = self._session_snapshot_for(connection, str(sandbox["id"]))
            scope = connection.execute(
                "SELECT organization_id FROM authorization_scopes WHERE id=?",
                (sandbox["scope_id"],),
            ).fetchone()
        if session is None or not session["secret_reference"] or scope is None:
            raise LocalRuntimeError(401, "workspace_secret_missing", "该组织需要重新登录")
        secret_reference = str(session["secret_reference"])
        try:
            encoded = self.secret_store.get(secret_reference)
        except SecretStoreError as exc:
            raise LocalRuntimeError(
                503,
                "local_secret_store_unavailable",
                "本机会话凭据存储暂不可用",
            ) from exc
        if not encoded:
            raise LocalRuntimeError(401, "workspace_secret_missing", "该组织需要重新登录")
        try:
            bundle = decode_secret_bundle(encoded)
        except (TypeError, ValueError) as exc:
            raise LocalRuntimeError(
                401,
                "workspace_secret_invalid",
                "本机会话凭据无法校验，请重新登录",
            ) from exc
        expected = {
            "cloudInstanceId": str(sandbox["cloud_instance_id"] or ""),
            "scopeId": str(sandbox["scope_id"] or ""),
            "organizationId": str(scope["organization_id"] or ""),
            "principalId": str(session["principal_id"] or ""),
            "membershipId": str(session["membership_id"] or ""),
        }
        if any(str(bundle.get(key) or "") != value for key, value in expected.items()):
            raise LocalRuntimeError(
                409,
                "workspace_secret_identity_mismatch",
                "本机会话与组织身份不一致，请重新登录",
            )
        if not all(
            str(bundle.get(key) or "")
            for key in ("cloudApiUrl", "serverSessionId", "accessToken", "refreshToken")
        ):
            raise LocalRuntimeError(
                401,
                "workspace_secret_invalid",
                "本机会话凭据不完整，请重新登录",
            )
        return session, secret_reference, bundle

    def _set_workspace_runtime_status(self, sandbox_id: str, status: str) -> None:
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE sandboxes SET runtime_status=?, updated_at=?, version=version+1 "
                "WHERE id=? AND record_kind='sandbox'",
                (status, now, sandbox_id),
            )
            connection.execute(
                "UPDATE sandboxes SET runtime_status=?, updated_at=?, version=version+1 "
                "WHERE id=? AND record_kind='local_session_snapshot' "
                "AND lifecycle_state='active'",
                (status, now, f"session_snapshot_{sandbox_id}"),
            )
            connection.execute("COMMIT")

    def _record_local_session_operation(
        self,
        connection,
        *,
        sandbox,
        session,
        idempotency_key: str,
        command_type: str,
        event_type: str,
        action: str,
        payload_hash: str,
        now: str,
    ) -> str:
        scope_id = str(sandbox["scope_id"])
        operation_id = self._stable_id("op", command_type, scope_id, idempotency_key)
        result_hash = sha256_text(
            canonical_json(
                {
                    "operationId": operation_id,
                    "eventType": event_type,
                    "sandboxId": str(sandbox["id"]),
                    "sessionVersion": int(session["version"] or 1) + 1,
                }
            )
        )
        connection.execute(
            """
            INSERT INTO idempotency_records (
                id, scope_id, idempotency_key, payload_hash, result_hash,
                expires_at, result_object_manifest_id, status, created_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'settled', ?, 'local', ?)
            """,
            (
                self._stable_id("idem", operation_id, command_type),
                scope_id,
                idempotency_key,
                payload_hash,
                result_hash,
                session["refresh_expires_at"] or sandbox["lease_expires_at"],
                now,
                self.identity.database_generation_id,
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
            ) VALUES (?, ?, ?, ?, 'local_session_snapshot', ?, ?, ?, ?, NULL,
                      'settled', ?, NULL, ?, ?, ?, 'local', ?)
            """,
            (
                self._stable_id("cmd", operation_id, command_type),
                scope_id,
                operation_id,
                idempotency_key,
                str(session["id"]),
                command_type,
                session["principal_id"],
                int(session["version"] or 1),
                session["membership_id"],
                payload_hash,
                now,
                now,
                self.identity.database_generation_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO outbox_events (
                id, scope_id, operation_id, aggregate_version, event_type,
                status, aggregate_type, aggregate_id,
                event_object_manifest_id, event_hash, available_at,
                published_at, authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, 'published', 'local_session_snapshot', ?,
                      NULL, ?, ?, ?, 'local', ?)
            """,
            (
                self._stable_id("evt", operation_id, event_type),
                scope_id,
                operation_id,
                int(session["version"] or 1) + 1,
                event_type,
                str(session["id"]),
                result_hash,
                now,
                now,
                self.identity.database_generation_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, scope_id, operation_id, actor_id, action, event_hash,
                actor_membership_id, target_resource_id,
                details_object_manifest_id, occurred_at, origin_instance_id,
                created_at, integrity_hash, authority_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, 'local')
            """,
            (
                self._stable_id("audit", operation_id, action),
                scope_id,
                operation_id,
                session["principal_id"],
                action,
                result_hash,
                session["membership_id"],
                now,
                self.identity.database_generation_id,
                now,
                sha256_text(f"{operation_id}|{result_hash}|{now}"),
            ),
        )
        return operation_id

    def _active_sandbox(self):
        pinned = getattr(self._workspace_context_local, "pinned", None)
        with self._connection() as connection:
            if pinned is not None:
                if not pinned.sandbox_id:
                    return None
                return connection.execute(
                    """
                    SELECT * FROM sandboxes
                    WHERE id=? AND record_kind='sandbox'
                      AND lifecycle_state!='deleted'
                    """,
                    (pinned.sandbox_id,),
                ).fetchone()
            return connection.execute(
                """
                SELECT * FROM sandboxes
                WHERE record_kind='sandbox' AND lifecycle_state='active'
                ORDER BY updated_at DESC LIMIT 1
                """
            ).fetchone()

    def _binding_for(self, connection, sandbox) -> Any:
        return connection.execute(
            """
            SELECT * FROM sandboxes
            WHERE record_kind='binding' AND scope_id=?
              AND cloud_instance_id=? AND lifecycle_state!='deleted'
            ORDER BY updated_at DESC LIMIT 1
            """,
            (sandbox["scope_id"], sandbox["cloud_instance_id"]),
        ).fetchone()

    def _authorization_snapshot(
        self,
        connection,
        *,
        scope_id: str,
        organization_id: str,
        principal_id: str,
        membership_id: str,
        runtime_status: str,
    ) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT projection.*, policy.version AS policy_version,
                   membership.role_key, membership.visibility_scope
            FROM viewer_projections AS projection
            JOIN policy_versions AS policy
              ON policy.id=projection.policy_version_id
            JOIN organization_memberships AS membership
              ON membership.id=projection.viewer_membership_id
             AND membership.scope_id=projection.scope_id
            JOIN authorization_scopes AS scope ON scope.id=projection.scope_id
            WHERE projection.scope_id=? AND scope.organization_id=?
              AND projection.viewer_principal_id=?
              AND projection.viewer_membership_id=?
              AND projection.secured_resource_id IS NULL
              AND projection.invalidated_at IS NULL
              AND projection.projection_state='fresh'
              AND policy.lifecycle_state='active'
            ORDER BY projection.generated_at DESC, projection.id DESC
            LIMIT 1
            """,
            (scope_id, organization_id, principal_id, membership_id),
        ).fetchone()
        if row is None:
            return None
        try:
            surfaces = json.loads(str(row["viewer_surfaces"] or "[]"))
            capabilities = json.loads(str(row["viewer_capabilities"] or "[]"))
        except (TypeError, ValueError):
            return None
        if not isinstance(surfaces, list) or not isinstance(capabilities, list):
            return None
        lease_expires_at = str(row["lease_expires_at"] or "") or None
        # Local viewer rows only drive presentation. Online cloud requests
        # validate the live session/membership; offline operations fail with a
        # retryable connectivity state instead of trusting this cached row.
        if runtime_status == "sync_degraded":
            state = "ready"
            freshness = "stale"
            reason_code = "cloud_revalidation_pending"
            retryable = True
        else:
            state = "ready"
            freshness = "current"
            reason_code = None
            retryable = False
        return {
            "state": state,
            "freshness": freshness,
            "reasonCode": reason_code,
            "retryable": retryable,
            "principalId": principal_id,
            "membershipId": membership_id,
            "organizationId": organization_id,
            "scopeId": scope_id,
            "systemRole": str(row["role_key"] or "member"),
            "visibilityScope": str(row["visibility_scope"] or "organization"),
            "policyVersion": int(row["policy_version"] or 1),
            "policyVersionId": str(row["policy_version_id"]),
            "projectionId": str(row["id"]),
            "surfaces": surfaces,
            "capabilities": capabilities,
            "generatedAt": row["generated_at"],
            "lastConfirmedAt": row["projected_at"] or row["generated_at"],
            "leaseExpiresAt": lease_expires_at,
            "sourceVersion": int(row["source_version"] or 1),
        }

    def _snapshot(
        self,
        connection,
        scope_id: str,
        principal_id: str | None,
        membership_id: str | None,
        runtime_status: str,
    ) -> dict[str, Any] | None:
        organization = connection.execute(
            """
            SELECT organization.id AS organizationId, organization.name,
                   organization.lifecycle_state AS lifecycleState, organization.version
            FROM authorization_scopes AS scope
            JOIN organizations AS organization ON organization.id=scope.organization_id
            WHERE scope.id=? AND organization.record_kind='organization'
            """,
            (scope_id,),
        ).fetchone()
        if organization is None:
            return None
        current_membership = None
        if membership_id:
            current_membership = connection.execute(
                """
                SELECT membership.id AS membershipId,
                       membership.principal_id AS principalId,
                       principal.display_name AS displayName,
                       membership.role_key AS systemRole,
                       membership.visibility_scope AS visibilityScope,
                       membership.status, membership.version
                FROM organization_memberships AS membership
                JOIN principals AS principal ON principal.id=membership.principal_id
                WHERE membership.id=?
                """,
                (membership_id,),
            ).fetchone()
        if current_membership is None:
            current_membership = connection.execute(
                """
                SELECT membership.id AS membershipId,
                       membership.principal_id AS principalId,
                       principal.display_name AS displayName,
                       membership.role_key AS systemRole,
                       membership.visibility_scope AS visibilityScope,
                       membership.status, membership.version
                FROM organization_memberships AS membership
                JOIN principals AS principal ON principal.id=membership.principal_id
                WHERE membership.scope_id=? AND membership.record_kind='membership'
                  AND membership.status='active'
                  AND membership.lifecycle_state='active'
                  AND principal.status='active'
                  AND principal.lifecycle_state='active'
                ORDER BY membership.updated_at DESC LIMIT 1
                """,
                (scope_id,),
            ).fetchone()
        if current_membership is None:
            return None
        principal_id = str(current_membership["principalId"])
        contacts = [dict(row) for row in connection.execute(
            """
            SELECT contact_type AS type, normalized_contact AS value,
                   verification_state AS verificationState
            FROM principals WHERE parent_principal_id=? AND principal_kind='contact'
            """,
            (principal_id,),
        ).fetchall()]
        members = [dict(row) for row in connection.execute(
            """
            SELECT membership.id AS membershipId,
                   membership.principal_id AS principalId,
                   principal.display_name AS displayName,
                   membership.role_key AS systemRole,
                   membership.visibility_scope AS visibilityScope,
                   membership.status, membership.version
            FROM organization_memberships AS membership
            JOIN principals AS principal ON principal.id=membership.principal_id
            WHERE membership.scope_id=? AND membership.record_kind='membership'
              AND membership.status='active'
              AND membership.lifecycle_state='active'
              AND principal.status='active'
              AND principal.lifecycle_state='active'
            ORDER BY principal.display_name
            """,
            (scope_id,),
        ).fetchall()]
        departments = [dict(row) for row in connection.execute(
            """
            SELECT child.id AS departmentId, child.name, child.color,
                   child.lifecycle_state AS lifecycleState, child.version
            FROM authorization_scopes AS scope
            JOIN organizations AS parent ON parent.id=scope.organization_id
            JOIN organizations AS child ON child.parent_record_id=parent.id
            WHERE scope.id=? AND child.record_kind='department'
            ORDER BY child.name
            """,
            (scope_id,),
        ).fetchall()]
        department_assignments = [dict(row) for row in connection.execute(
            """
            SELECT id AS assignmentId,
                   parent_membership_id AS membershipId,
                   department_id AS departmentId,
                   role_key AS assignmentRole,
                   status, version, lifecycle_state AS lifecycleState
            FROM organization_memberships
            WHERE scope_id=? AND record_kind='department_assignment'
              AND lifecycle_state='active' AND status='active'
            ORDER BY department_id, parent_membership_id, id
            """,
            (scope_id,),
        ).fetchall()]
        assignments_by_department: dict[str, list[dict[str, Any]]] = {}
        for assignment in department_assignments:
            assignments_by_department.setdefault(
                str(assignment.get("departmentId") or ""),
                [],
            ).append(
                {
                    "assignmentId": assignment.get("assignmentId"),
                    "membershipId": assignment.get("membershipId"),
                    "roleKey": assignment.get("assignmentRole") or "member",
                    "isDepartmentLead": (
                        assignment.get("assignmentRole") == "department_lead"
                    ),
                    "status": assignment.get("status") or "active",
                    "version": int(assignment.get("version") or 1),
                }
            )
        for department in departments:
            department["members"] = assignments_by_department.get(
                str(department.get("departmentId") or ""),
                [],
            )
        principal = {
            "principalId": principal_id,
            "displayName": current_membership["displayName"],
            "contacts": contacts,
        }
        membership = dict(current_membership)
        authorization = self._authorization_snapshot(
            connection,
            scope_id=scope_id,
            organization_id=str(organization["organizationId"]),
            principal_id=principal_id,
            membership_id=str(current_membership["membershipId"]),
            runtime_status=runtime_status,
        )
        return {
            "organization": dict(organization),
            "principal": principal,
            "membership": membership,
            "currentPrincipal": principal,
            "currentMembership": membership,
            "members": members,
            "departments": departments,
            "departmentAssignments": department_assignments,
            "managementTitles": [],
            "reportingLines": [],
            "invites": [],
            "authorization": authorization,
        }

    def list_workspaces(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            sandboxes = connection.execute(
                """
                SELECT * FROM sandboxes WHERE record_kind='sandbox'
                  AND lifecycle_state!='deleted' ORDER BY created_at
                """
            ).fetchall()
            # ``lifecycle_state='archived'`` means "not currently selected"
            # for organization sandboxes; it must not remove a valid signed-in
            # organization from the workspace switcher.  Older pre-cutover
            # rows can coexist with the canonical 88-table sandbox, so expose
            # only the best row for each cloud organization.
            selected: dict[tuple[str, str], Any] = {}
            for sandbox in sandboxes:
                scope = connection.execute(
                    "SELECT organization_id FROM authorization_scopes WHERE id=?",
                    (sandbox["scope_id"],),
                ).fetchone()
                organization_id = str(scope["organization_id"] or "") if scope else ""
                key = (str(sandbox["cloud_instance_id"] or ""), organization_id)
                current = selected.get(key)
                score = (
                    2 if str(sandbox["runtime_status"] or "") in {"ready", "sync_degraded"} else 1,
                    1 if str(sandbox["lifecycle_state"] or "") == "active" else 0,
                    str(sandbox["updated_at"] or ""),
                )
                if current is None:
                    selected[key] = sandbox
                    continue
                current_score = (
                    2 if str(current["runtime_status"] or "") in {"ready", "sync_degraded"} else 1,
                    1 if str(current["lifecycle_state"] or "") == "active" else 0,
                    str(current["updated_at"] or ""),
                )
                if score > current_score:
                    selected[key] = sandbox
            result = []
            for sandbox in sorted(
                selected.values(), key=lambda item: str(item["created_at"] or "")
            ):
                binding = self._binding_for(connection, sandbox)
                scope = connection.execute(
                    "SELECT organization_id FROM authorization_scopes WHERE id=?",
                    (sandbox["scope_id"],),
                ).fetchone()
                result.append({
                    "sandboxId": sandbox["id"],
                    "kind": "organization",
                    "runtimeStatus": sandbox["runtime_status"],
                    "displayName": sandbox["display_name"],
                    "isActive": sandbox["lifecycle_state"] == "active",
                    "cloudInstanceId": sandbox["cloud_instance_id"],
                    "organizationId": str(scope["organization_id"] or "") if scope else "",
                    "cloudApiUrl": binding["cloud_api_url"] if binding else sandbox["cloud_api_url"],
                    "identityState": binding["runtime_status"] if binding else sandbox["runtime_status"],
                    "updatedAt": sandbox["updated_at"],
                })
        return result

    def current(self) -> dict[str, Any]:
        row = self._active_sandbox()
        if row is None:
            return {
                "runtimeStatus": "needs_login",
                "requiresLogin": True,
                "identityState": "not_connected",
                "statusMessage": "尚未登录组织",
                "sandbox": None,
                "sessionSnapshot": None,
                "aiRuntime": {"state": "not_ready", "message": "尚未同步组织 AI 配置"},
                "capabilities": capability_registry(cloud_connected=False),
                "databaseIdentity": self._identity_payload(),
            }
        with self._connection() as connection:
            binding = self._binding_for(connection, row)
            session = connection.execute(
                """
                SELECT * FROM sandboxes WHERE record_kind='local_session_snapshot'
                  AND scope_id=? AND cloud_instance_id=? AND lifecycle_state!='deleted'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (row["scope_id"], row["cloud_instance_id"]),
            ).fetchone()
            snapshot = self._snapshot(
                connection,
                str(row["scope_id"]),
                str(session["principal_id"]) if session and session["principal_id"] else None,
                str(session["membership_id"]) if session and session["membership_id"] else None,
                str(row["runtime_status"]),
            )
            organization_id = connection.execute(
                "SELECT organization_id FROM authorization_scopes WHERE id=?",
                (row["scope_id"],),
            ).fetchone()[0]
        connected = row["runtime_status"] in {"ready", "sync_degraded"}
        authorization = (snapshot or {}).get("authorization") or {}
        if authorization.get("state") == "blocked":
            status_message = "权限租约已过期，请重新连接组织云"
        elif row["runtime_status"] == "sync_degraded":
            status_message = "组织云连接失败，当前使用租约内的最后确认权限"
        else:
            status_message = "组织工作空间已准备完成"
        return {
            "runtimeStatus": row["runtime_status"],
            "requiresLogin": row["runtime_status"] == "needs_login",
            "identityState": binding["runtime_status"] if binding else row["runtime_status"],
            "statusMessage": status_message if connected else "需要重新登录该组织",
            "sandbox": {
                "sandboxId": row["id"],
                "kind": "organization",
                "displayName": row["display_name"],
                "cloudInstanceId": row["cloud_instance_id"],
                "organizationId": organization_id,
                "cloudApiUrl": binding["cloud_api_url"] if binding else row["cloud_api_url"],
                "updatedAt": row["updated_at"],
            },
            "sessionSnapshot": snapshot,
            "aiRuntime": {"state": "not_ready", "message": "组织 AI 配置等待黄金链接通"},
            "capabilities": capability_registry(cloud_connected=connected),
            "databaseIdentity": self._identity_payload(),
        }

    def current_authorization(self) -> dict[str, Any]:
        current = self.current()
        snapshot = current.get("sessionSnapshot") or {}
        authorization = snapshot.get("authorization")
        if not isinstance(authorization, dict):
            raise LocalRuntimeError(
                503,
                "authorization_projection_missing",
                "本机权限投影尚未完整落地，请重新登录",
            )
        if authorization.get("state") != "ready":
            state = str(authorization.get("state") or "blocked")
            raise LocalRuntimeError(
                503 if state == "failed_retryable" else 403,
                str(authorization.get("reasonCode") or "authorization_blocked"),
                "当前权限状态尚未就绪",
            )
        return authorization

    def require_surface(self, surface: str) -> dict[str, Any]:
        authorization = self.current_authorization()
        surfaces = authorization.get("surfaces") or []
        if surface not in surfaces:
            raise LocalRuntimeError(
                403,
                "permission_denied",
                "当前组织身份没有使用该界面的权限",
            )
        return authorization

    def require_capability(self, capability: str) -> dict[str, Any]:
        authorization = self.current_authorization()
        capabilities = authorization.get("capabilities") or []
        if capability not in capabilities:
            raise LocalRuntimeError(
                403,
                "permission_denied",
                "当前组织身份没有执行该操作的权限",
            )
        return authorization

    def _identity_payload(self) -> dict[str, str]:
        return {
            "schemaFamily": self.identity.schema_family,
            "contractVersion": self.identity.contract_version,
            "schemaManifestSha256": self.identity.manifest_hash,
            "databaseGenerationId": self.identity.database_generation_id,
            "buildId": self.identity.build_id,
        }

    def _validate_handshake(self, payload: dict[str, Any]) -> None:
        if (
            payload.get("apiVersion") != "v2"
            or payload.get("schemaFamily") != CLOUD_CONTRACT.schema_family
            or str(payload.get("contractVersion")) != CLOUD_CONTRACT.contract_version
            or payload.get("schemaManifestSha256") != CLOUD_CONTRACT.manifest_hash
        ):
            raise LocalRuntimeError(409, "schema_incompatible", "组织云不是当前 88 表合同")

    def _validated_login_projection(
        self,
        handshake: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = payload.get("sessionSnapshot") or {}
        organization = snapshot.get("organization") or {}
        principal = snapshot.get("principal") or snapshot.get("currentPrincipal") or {}
        membership = snapshot.get("membership") or snapshot.get("currentMembership") or {}
        authorization = snapshot.get("authorization") or {}
        organization_id = str(
            payload.get("organizationId") or organization.get("organizationId") or ""
        )
        principal_id = str(payload.get("principalId") or principal.get("principalId") or "")
        membership_id = str(
            payload.get("membershipId") or membership.get("membershipId") or ""
        )
        cloud_instance_id = str(payload.get("cloudInstanceId") or "")
        scope_id = str(authorization.get("scopeId") or "")
        access_token = str(payload.get("accessToken") or "")
        refresh_token = str(payload.get("refreshToken") or "")
        if not all(
            (
                organization_id,
                principal_id,
                membership_id,
                cloud_instance_id,
                scope_id,
                access_token,
                refresh_token,
                payload.get("sessionId"),
                authorization.get("policyVersionId"),
                authorization.get("projectionId"),
                authorization.get("leaseExpiresAt"),
            )
        ):
            raise LocalRuntimeError(502, "session_invalid", "组织云身份快照不完整")
        if (
            cloud_instance_id != str(handshake.get("cloudInstanceId") or "")
            or organization_id != str(authorization.get("organizationId") or "")
            or principal_id != str(authorization.get("principalId") or "")
            or membership_id != str(authorization.get("membershipId") or "")
            or authorization.get("state") != "ready"
        ):
            raise LocalRuntimeError(409, "session_identity_mismatch", "组织云会话身份不一致")
        surfaces = authorization.get("surfaces")
        capabilities = authorization.get("capabilities")
        if not isinstance(surfaces, list) or not isinstance(capabilities, list):
            raise LocalRuntimeError(502, "authorization_invalid", "组织云权限投影不完整")
        members = [
            dict(item)
            for item in snapshot.get("members") or []
            if isinstance(item, dict)
            and item.get("principalId")
            and item.get("membershipId")
        ]
        current_member = next(
            (item for item in members if str(item.get("membershipId")) == membership_id),
            None,
        )
        if current_member is None:
            current_member = dict(membership)
            current_member.update(
                {
                    "membershipId": membership_id,
                    "principalId": principal_id,
                    "displayName": principal.get("displayName"),
                    "systemRole": authorization.get("systemRole"),
                    "visibilityScope": authorization.get("visibilityScope"),
                    "status": "active",
                    "version": authorization.get("sourceVersion") or 1,
                }
            )
            members.append(current_member)
        current_member.update(
            {
                "systemRole": authorization.get("systemRole"),
                "visibilityScope": authorization.get("visibilityScope"),
                "version": authorization.get("sourceVersion") or 1,
            }
        )
        return {
            "snapshot": snapshot,
            "organization": organization,
            "principal": principal,
            "membership": membership,
            "authorization": authorization,
            "members": members,
            "departments": [
                dict(item)
                for item in snapshot.get("departments") or []
                if isinstance(item, dict) and item.get("departmentId")
            ],
            "departmentAssignments": [
                dict(item)
                for item in snapshot.get("departmentAssignments") or []
                if isinstance(item, dict)
                and item.get("assignmentId")
                and item.get("membershipId")
                and item.get("departmentId")
            ],
            "organizationId": organization_id,
            "principalId": principal_id,
            "membershipId": membership_id,
            "cloudInstanceId": cloud_instance_id,
            "scopeId": scope_id,
            "accessToken": access_token,
            "refreshToken": refresh_token,
        }

    def _existing_local_login(
        self,
        connection,
        *,
        scope_id: str,
        idempotency_key: str,
        payload_hash: str,
    ) -> str | None:
        command = connection.execute(
            "SELECT * FROM commands WHERE scope_id=? AND idempotency_key=?",
            (scope_id, idempotency_key),
        ).fetchone()
        if command is None:
            orphan = connection.execute(
                "SELECT 1 FROM idempotency_records WHERE scope_id=? AND idempotency_key=?",
                (scope_id, idempotency_key),
            ).fetchone()
            if orphan is not None:
                raise LocalRuntimeError(
                    409,
                    "local_login_idempotency_incomplete",
                    "本机登录回执不完整，请重新发起登录",
                )
            return None
        if (
            command["command_type"] != "gc01.local.session.login"
            or command["payload_hash"] != payload_hash
        ):
            raise LocalRuntimeError(
                409,
                "local_login_idempotency_conflict",
                "该登录幂等键已用于另一身份",
            )
        sandbox_id = str(command["aggregate_id"])
        session = connection.execute(
            """
            SELECT secret_reference FROM sandboxes
            WHERE scope_id=? AND record_kind='local_session_snapshot'
              AND lifecycle_state='active' AND secret_reference IS NOT NULL
              AND id=?
            """,
            (scope_id, f"session_snapshot_{sandbox_id}"),
        ).fetchone()
        if session is None:
            raise LocalRuntimeError(
                503,
                "local_login_replay_incomplete",
                "本机登录投影尚未完整落地，请重新登录",
            )
        try:
            secret = self.secret_store.get(str(session["secret_reference"]))
        except SecretStoreError as exc:
            raise LocalRuntimeError(
                503,
                "local_secret_store_unavailable",
                "本机会话凭据存储暂不可用",
            ) from exc
        if not secret:
            raise LocalRuntimeError(
                503,
                "local_login_replay_secret_missing",
                "本机会话凭据缺失，请重新登录",
            )
        return sandbox_id

    def _apply_login(
        self,
        cloud_api_url: str,
        handshake: dict[str, Any],
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> str:
        projection = self._validated_login_projection(handshake, payload)
        organization = projection["organization"]
        principal = projection["principal"]
        authorization = projection["authorization"]
        organization_id = projection["organizationId"]
        principal_id = projection["principalId"]
        membership_id = projection["membershipId"]
        cloud_instance_id = projection["cloudInstanceId"]
        scope_id = projection["scopeId"]
        now = utc_now()
        payload_hash = sha256_text(
            canonical_json(
                {
                    "cloudApiUrl": cloud_api_url,
                    "cloudInstanceId": cloud_instance_id,
                    "organizationId": organization_id,
                    "scopeId": scope_id,
                    "principalId": principal_id,
                    "membershipId": membership_id,
                    "serverSessionId": payload.get("sessionId"),
                }
            )
        )
        with self._connection() as connection:
            replayed = self._existing_local_login(
                connection,
                scope_id=scope_id,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )
            if replayed:
                return replayed
            existing = connection.execute(
                """
                SELECT id FROM sandboxes WHERE record_kind='sandbox'
                  AND cloud_instance_id=? AND scope_id=?
                  AND lifecycle_state!='deleted' LIMIT 1
                """,
                (cloud_instance_id, scope_id),
            ).fetchone()
            sandbox_id = (
                str(existing["id"])
                if existing
                else self._stable_id("sandbox", cloud_instance_id, scope_id)
            )
            previous_session = connection.execute(
                """
                SELECT secret_reference FROM sandboxes
                WHERE id=? AND record_kind='local_session_snapshot'
                """,
                (f"session_snapshot_{sandbox_id}",),
            ).fetchone()
            previous_secret_ref = (
                str(previous_session["secret_reference"])
                if previous_session and previous_session["secret_reference"]
                else None
            )

        operation_id = self._stable_id(
            "op", "gc01.local.session.login", scope_id, idempotency_key
        )
        secret_ref = (
            f"workspace-session:{sandbox_id}:{operation_id}:{new_id()}"
        )
        encoded = encode_secret_bundle(
            {
                "cloudApiUrl": cloud_api_url,
                "cloudInstanceId": cloud_instance_id,
                "organizationId": organization_id,
                "scopeId": scope_id,
                "principalId": principal_id,
                "membershipId": membership_id,
                "serverSessionId": payload.get("sessionId"),
                "accessToken": projection["accessToken"],
                "refreshToken": projection["refreshToken"],
                "expiresAt": payload.get("expiresAt"),
                "refreshExpiresAt": payload.get("refreshExpiresAt"),
                "authorizationLeaseExpiresAt": authorization.get("leaseExpiresAt"),
            }
        )
        try:
            self.secret_store.set(secret_ref, encoded)
        except SecretStoreError as exc:
            raise LocalRuntimeError(
                503,
                "local_secret_store_unavailable",
                "本机会话凭据存储暂不可用，尚未切换登录状态",
            ) from exc

        result_hash = sha256_text(
            canonical_json(
                {
                    "operationId": operation_id,
                    "sandboxId": sandbox_id,
                    "scopeId": scope_id,
                    "principalId": principal_id,
                    "membershipId": membership_id,
                    "policyVersionId": authorization["policyVersionId"],
                    "projectionId": authorization["projectionId"],
                }
            )
        )
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                replayed = self._existing_local_login(
                    connection,
                    scope_id=scope_id,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
                if replayed:
                    connection.execute("ROLLBACK")
                    self.secret_store.delete(secret_ref)
                    return replayed

                organization_version = int(organization.get("version") or 1)
                connection.execute(
                    """
                    INSERT INTO organizations (
                        id, lifecycle_state, version, updated_at, record_kind,
                        name, created_at, deleted_at, sandbox_id, source_version,
                        projection_state, projected_at, stale_at, lease_expires_at
                    ) VALUES (?, 'active', ?, ?, 'organization', ?, ?, NULL,
                              NULL, ?, 'fresh', ?, NULL, ?)
                    ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                        lifecycle_state='active', deleted_at=NULL,
                        version=excluded.version, updated_at=excluded.updated_at,
                        source_version=excluded.source_version,
                        projection_state='fresh', projected_at=excluded.projected_at,
                        stale_at=NULL, lease_expires_at=excluded.lease_expires_at
                    """,
                    (
                        organization_id,
                        organization_version,
                        now,
                        organization.get("name"),
                        now,
                        organization_version,
                        now,
                        authorization["leaseExpiresAt"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO authorization_scopes (
                        id, scope_kind, organization_id, policy_version,
                        created_at, updated_at, status, version, lifecycle_state,
                        deleted_at, sandbox_id, source_version,
                        projection_state, projected_at, stale_at, lease_expires_at
                    ) VALUES (?, 'organization', ?, ?, ?, ?, 'active', ?,
                              'active', NULL, NULL, ?, 'fresh', ?, NULL, ?)
                    ON CONFLICT(id) DO UPDATE SET organization_id=excluded.organization_id,
                        policy_version=excluded.policy_version, status='active',
                        lifecycle_state='active', deleted_at=NULL,
                        version=excluded.version, updated_at=excluded.updated_at,
                        source_version=excluded.source_version,
                        projection_state='fresh', projected_at=excluded.projected_at,
                        stale_at=NULL, lease_expires_at=excluded.lease_expires_at
                    """,
                    (
                        scope_id,
                        organization_id,
                        int(authorization.get("policyVersion") or 1),
                        now,
                        now,
                        int(authorization.get("policyVersion") or 1),
                        int(authorization.get("policyVersion") or 1),
                        now,
                        authorization["leaseExpiresAt"],
                    ),
                )

                member_ids: list[str] = []
                principal_ids: list[str] = []
                for item in projection["members"]:
                    member_principal_id = str(item["principalId"])
                    member_id = str(item["membershipId"])
                    member_version = int(item.get("version") or 1)
                    member_ids.append(member_id)
                    principal_ids.append(member_principal_id)
                    connection.execute(
                        """
                        INSERT INTO principals (
                            id, status, identity_version, updated_at,
                            principal_kind, display_name, version,
                            lifecycle_state, created_at, deleted_at, sandbox_id,
                            source_version, projection_state, projected_at,
                            stale_at, lease_expires_at
                        ) VALUES (?, 'active', ?, ?, 'person', ?, ?, 'active',
                                  ?, NULL, NULL, ?, 'fresh', ?, NULL, ?)
                        ON CONFLICT(id) DO UPDATE SET status='active',
                            display_name=excluded.display_name,
                            identity_version=excluded.identity_version,
                            version=excluded.version, lifecycle_state='active',
                            deleted_at=NULL, updated_at=excluded.updated_at,
                            source_version=excluded.source_version,
                            projection_state='fresh', projected_at=excluded.projected_at,
                            stale_at=NULL, lease_expires_at=excluded.lease_expires_at
                        """,
                        (
                            member_principal_id,
                            member_version,
                            now,
                            item.get("displayName"),
                            member_version,
                            now,
                            member_version,
                            now,
                            authorization["leaseExpiresAt"],
                        ),
                    )

                    # A fresh device has no stale membership rows to drive the
                    # cleanup loop below.  Persist every authoritative member
                    # while processing the snapshot so department assignments
                    # can safely reference the membership in the same atomic
                    # transaction.
                    connection.execute(
                        """
                        INSERT INTO organization_memberships (
                            id, scope_id, principal_id, role_key, status,
                            version, record_kind, visibility_scope,
                            lifecycle_state, created_at, updated_at, deleted_at,
                            sandbox_id, source_version, projection_state,
                            projected_at, stale_at, lease_expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'membership', ?, 'active',
                                  ?, ?, NULL, NULL, ?, 'fresh', ?, NULL, ?)
                        ON CONFLICT(id) DO UPDATE SET scope_id=excluded.scope_id,
                            principal_id=excluded.principal_id,
                            role_key=excluded.role_key, status=excluded.status,
                            visibility_scope=excluded.visibility_scope,
                            version=excluded.version, lifecycle_state='active',
                            deleted_at=NULL, updated_at=excluded.updated_at,
                            source_version=excluded.source_version,
                            projection_state='fresh', projected_at=excluded.projected_at,
                            stale_at=NULL, lease_expires_at=excluded.lease_expires_at
                        """,
                        (
                            member_id,
                            scope_id,
                            member_principal_id,
                            item.get("systemRole") or "member",
                            item.get("status") or "active",
                            member_version,
                            item.get("visibilityScope") or "self",
                            now,
                            now,
                            member_version,
                            now,
                            authorization["leaseExpiresAt"],
                        ),
                    )

                # The organization snapshot is a complete active-member projection.
                # Members absent from a fresh snapshot must stop being assignable locally;
                # retaining them as active is what made deleted accounts reappear in editors.
                active_member_ids = set(member_ids)
                for stale_member in connection.execute(
                    "SELECT id FROM organization_memberships "
                    "WHERE scope_id=? AND record_kind='membership' "
                    "AND status='active' AND lifecycle_state='active'",
                    (scope_id,),
                ).fetchall():
                    stale_member_id = str(stale_member["id"])
                    if stale_member_id in active_member_ids:
                        continue
                    connection.execute(
                        "UPDATE organization_memberships SET status='disabled', "
                        "lifecycle_state='deleted', deleted_at=?, updated_at=?, "
                        "projection_state='fresh', projected_at=? WHERE id=?",
                        (now, now, now, stale_member_id),
                    )

                for contact in principal.get("contacts") or []:
                    if not isinstance(contact, dict):
                        continue
                    contact_type = str(contact.get("type") or "").strip()
                    contact_value = str(contact.get("value") or "").strip()
                    if not contact_type or not contact_value:
                        continue
                    # The local 88-table contract makes a normalized contact
                    # globally unique.  The same human may legitimately use
                    # the same email/phone in two independent organization
                    # clouds whose principal IDs differ.  Reuse that one local
                    # contact projection and point it at the identity of the
                    # workspace being activated; creating an instance-scoped
                    # second row violates uq_principals_01 and used to roll the
                    # whole successful cloud login back.
                    existing_contact = connection.execute(
                        "SELECT id FROM principals WHERE normalized_contact=?",
                        (contact_value,),
                    ).fetchone()
                    contact_id = (
                        str(existing_contact["id"])
                        if existing_contact is not None
                        else self._stable_id(
                            "contact", contact_type, contact_value
                        )
                    )
                    connection.execute(
                        """
                        INSERT INTO principals (
                            id, status, identity_version, updated_at,
                            principal_kind, parent_principal_id, contact_type,
                            normalized_contact, verification_state, version,
                            lifecycle_state, created_at, deleted_at, sandbox_id,
                            source_version, projection_state, projected_at,
                            stale_at, lease_expires_at
                        ) VALUES (?, 'active', 1, ?, 'contact', ?, ?, ?, ?, 1,
                                  'active', ?, NULL, NULL, 1, 'fresh', ?, NULL, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            parent_principal_id=excluded.parent_principal_id,
                            verification_state=excluded.verification_state,
                            status='active', lifecycle_state='active',
                            deleted_at=NULL, updated_at=excluded.updated_at,
                            projection_state='fresh', projected_at=excluded.projected_at,
                            stale_at=NULL, lease_expires_at=excluded.lease_expires_at
                        """,
                        (
                            contact_id,
                            now,
                            principal_id,
                            contact_type,
                            contact_value,
                            contact.get("verificationState") or "verified",
                            now,
                            now,
                            authorization["leaseExpiresAt"],
                        ),
                    )
                    principal_ids.append(contact_id)

                department_ids: list[str] = []
                for department in projection["departments"]:
                    department_id = str(department["departmentId"])
                    department_ids.append(department_id)
                    department_version = int(department.get("version") or 1)
                    lifecycle = (
                        "active"
                        if department.get("lifecycleState") == "active"
                        else "archived"
                    )
                    connection.execute(
                        """
                        INSERT INTO organizations (
                            id, lifecycle_state, version, updated_at, record_kind,
                            parent_record_id, name, color, created_at, deleted_at,
                            sandbox_id, source_version, projection_state,
                            projected_at, stale_at, lease_expires_at
                        ) VALUES (?, ?, ?, ?, 'department', ?, ?, ?, ?, NULL,
                                  NULL, ?, 'fresh', ?, NULL, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            parent_record_id=excluded.parent_record_id,
                            name=excluded.name, color=excluded.color,
                            lifecycle_state=excluded.lifecycle_state,
                            deleted_at=NULL, version=excluded.version,
                            updated_at=excluded.updated_at,
                            source_version=excluded.source_version,
                            projection_state='fresh', projected_at=excluded.projected_at,
                            stale_at=NULL, lease_expires_at=excluded.lease_expires_at
                        """,
                        (
                            department_id,
                            lifecycle,
                            department_version,
                            now,
                            organization_id,
                            department.get("name"),
                            department.get("color"),
                            now,
                            department_version,
                            now,
                            authorization["leaseExpiresAt"],
                        ),
                    )

                for assignment in projection["departmentAssignments"]:
                    assignment_id = str(assignment["assignmentId"])
                    assignment_version = int(assignment.get("version") or 1)
                    assignment_lifecycle = (
                        "active"
                        if assignment.get("lifecycleState") == "active"
                        else "archived"
                    )
                    member_ids.append(assignment_id)
                    connection.execute(
                        """
                        INSERT INTO organization_memberships (
                            id, scope_id, principal_id, role_key, status,
                            version, record_kind, parent_membership_id,
                            department_id, lifecycle_state, created_at,
                            updated_at, deleted_at, sandbox_id, source_version,
                            projection_state, projected_at, stale_at,
                            lease_expires_at
                        ) VALUES (?, ?, NULL, ?, ?, ?, 'department_assignment',
                                  ?, ?, ?, ?, ?, NULL, NULL, ?, 'fresh', ?, NULL, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            scope_id=excluded.scope_id,
                            role_key=excluded.role_key,
                            status=excluded.status,
                            version=excluded.version,
                            parent_membership_id=excluded.parent_membership_id,
                            department_id=excluded.department_id,
                            lifecycle_state=excluded.lifecycle_state,
                            deleted_at=NULL,
                            source_version=excluded.source_version,
                            projection_state='fresh',
                            projected_at=excluded.projected_at,
                            stale_at=NULL,
                            lease_expires_at=excluded.lease_expires_at,
                            updated_at=excluded.updated_at
                        """,
                        (
                            assignment_id,
                            scope_id,
                            assignment.get("assignmentRole") or "member",
                            assignment.get("status") or "active",
                            assignment_version,
                            assignment["membershipId"],
                            assignment["departmentId"],
                            assignment_lifecycle,
                            now,
                            now,
                            assignment_version,
                            now,
                            authorization["leaseExpiresAt"],
                        ),
                    )

                connection.execute(
                    "UPDATE sandboxes SET lifecycle_state='archived', updated_at=? "
                    "WHERE record_kind='sandbox' AND lifecycle_state='active' AND id!=?",
                    (now, sandbox_id),
                )
                existing_sandbox = connection.execute(
                    "SELECT version FROM sandboxes WHERE id=?", (sandbox_id,)
                ).fetchone()
                sandbox_version = (
                    int(existing_sandbox["version"] or 1) + 1
                    if existing_sandbox
                    else 1
                )
                connection.execute(
                    """
                    INSERT INTO sandboxes (
                        id, scope_id, principal_id, membership_id, cloud_api_url,
                        record_kind, cloud_instance_id, database_generation_id,
                        sandbox_kind, display_name, runtime_status,
                        contract_version, manifest_hash, lease_expires_at,
                        last_verified_at, version, lifecycle_state, created_at,
                        updated_at, deleted_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 'sandbox', ?, ?, 'organization', ?,
                              'ready', ?, ?, ?, ?, ?, 'active', ?, ?, NULL,
                              'local', ?)
                    ON CONFLICT(id) DO UPDATE SET principal_id=excluded.principal_id,
                        membership_id=excluded.membership_id,
                        cloud_api_url=excluded.cloud_api_url,
                        cloud_instance_id=excluded.cloud_instance_id,
                        database_generation_id=excluded.database_generation_id,
                        display_name=excluded.display_name, runtime_status='ready',
                        contract_version=excluded.contract_version,
                        manifest_hash=excluded.manifest_hash,
                        lease_expires_at=excluded.lease_expires_at,
                        last_verified_at=excluded.last_verified_at,
                        version=excluded.version, lifecycle_state='active',
                        deleted_at=NULL, updated_at=excluded.updated_at
                    """,
                    (
                        sandbox_id,
                        scope_id,
                        principal_id,
                        membership_id,
                        cloud_api_url,
                        cloud_instance_id,
                        handshake["databaseGenerationId"],
                        organization.get("name"),
                        handshake["contractVersion"],
                        handshake["schemaManifestSha256"],
                        authorization["leaseExpiresAt"],
                        now,
                        sandbox_version,
                        now,
                        now,
                        self.identity.database_generation_id,
                    ),
                )
                binding_id = self._stable_id("binding", cloud_instance_id, scope_id)
                connection.execute(
                    """
                    INSERT INTO sandboxes (
                        id, scope_id, principal_id, membership_id, cloud_api_url,
                        record_kind, cloud_instance_id, database_generation_id,
                        sandbox_kind, display_name, runtime_status,
                        contract_version, manifest_hash, lease_expires_at,
                        last_verified_at, version, lifecycle_state, created_at,
                        updated_at, deleted_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 'binding', ?, ?, 'organization', ?,
                              'verified', ?, ?, ?, ?, 1, 'active', ?, ?, NULL,
                              'local', ?)
                    ON CONFLICT(id) DO UPDATE SET principal_id=excluded.principal_id,
                        membership_id=excluded.membership_id,
                        cloud_api_url=excluded.cloud_api_url,
                        database_generation_id=excluded.database_generation_id,
                        runtime_status='verified', manifest_hash=excluded.manifest_hash,
                        contract_version=excluded.contract_version,
                        lease_expires_at=excluded.lease_expires_at,
                        last_verified_at=excluded.last_verified_at,
                        lifecycle_state='active', deleted_at=NULL,
                        version=COALESCE(sandboxes.version, 1)+1,
                        updated_at=excluded.updated_at
                    """,
                    (
                        binding_id,
                        scope_id,
                        principal_id,
                        membership_id,
                        cloud_api_url,
                        cloud_instance_id,
                        handshake["databaseGenerationId"],
                        organization.get("name"),
                        handshake["contractVersion"],
                        handshake["schemaManifestSha256"],
                        authorization["leaseExpiresAt"],
                        now,
                        now,
                        now,
                        self.identity.database_generation_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO sandboxes (
                        id, scope_id, principal_id, membership_id, cloud_api_url,
                        secret_reference, secret_fingerprint, access_expires_at,
                        refresh_expires_at, last_seen_at, record_kind,
                        cloud_instance_id, database_generation_id, sandbox_kind,
                        runtime_status, contract_version, manifest_hash,
                        lease_expires_at, last_verified_at, version,
                        lifecycle_state, created_at, updated_at, deleted_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'local_session_snapshot', ?, ?, 'organization',
                              'active', ?, ?, ?, ?, 1, 'active', ?, ?, NULL,
                              'local', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        principal_id=excluded.principal_id,
                        membership_id=excluded.membership_id,
                        cloud_api_url=excluded.cloud_api_url,
                        secret_reference=excluded.secret_reference,
                        secret_fingerprint=excluded.secret_fingerprint,
                        access_expires_at=excluded.access_expires_at,
                        refresh_expires_at=excluded.refresh_expires_at,
                        last_seen_at=excluded.last_seen_at,
                        database_generation_id=excluded.database_generation_id,
                        runtime_status='active', contract_version=excluded.contract_version,
                        manifest_hash=excluded.manifest_hash,
                        lease_expires_at=excluded.lease_expires_at,
                        last_verified_at=excluded.last_verified_at,
                        version=COALESCE(sandboxes.version, 1)+1,
                        lifecycle_state='active', deleted_at=NULL,
                        updated_at=excluded.updated_at
                    """,
                    (
                        f"session_snapshot_{sandbox_id}",
                        scope_id,
                        principal_id,
                        membership_id,
                        cloud_api_url,
                        secret_ref,
                        secret_fingerprint(encoded),
                        payload.get("expiresAt"),
                        payload.get("refreshExpiresAt"),
                        now,
                        cloud_instance_id,
                        handshake["databaseGenerationId"],
                        handshake["contractVersion"],
                        handshake["schemaManifestSha256"],
                        authorization["leaseExpiresAt"],
                        now,
                        now,
                        now,
                        self.identity.database_generation_id,
                    ),
                )

                connection.execute(
                    "UPDATE organizations SET sandbox_id=? WHERE id=? OR parent_record_id=?",
                    (sandbox_id, organization_id, organization_id),
                )
                connection.execute(
                    "UPDATE authorization_scopes SET sandbox_id=? WHERE id=?",
                    (sandbox_id, scope_id),
                )
                if principal_ids:
                    placeholders = ",".join("?" for _ in principal_ids)
                    connection.execute(
                        f"UPDATE principals SET sandbox_id=? WHERE id IN ({placeholders})",
                        (sandbox_id, *principal_ids),
                    )
                if member_ids:
                    placeholders = ",".join("?" for _ in member_ids)
                    connection.execute(
                        f"UPDATE organization_memberships SET sandbox_id=? "
                        f"WHERE id IN ({placeholders})",
                        (sandbox_id, *member_ids),
                    )

                policy_spec = canonical_json(
                    {
                        "roleKey": authorization.get("systemRole") or "member",
                        "surfaces": authorization["surfaces"],
                        "capabilities": authorization["capabilities"],
                    }
                )
                connection.execute(
                    """
                    INSERT INTO policy_versions (
                        id, scope_id, secured_resource_id, policy_scope_kind,
                        version, policy_spec_schema_version, policy_spec,
                        effective_at, created_at, lifecycle_state, updated_at,
                        deleted_at, sandbox_id, source_version,
                        projection_state, projected_at, stale_at, lease_expires_at
                    ) VALUES (?, ?, NULL, 'organization_role', ?,
                              'gc01.authorization.policy.v1', ?, ?, ?, 'active',
                              ?, NULL, ?, ?, 'fresh', ?, NULL, ?)
                    ON CONFLICT(id) DO UPDATE SET policy_spec=excluded.policy_spec,
                        version=excluded.version,
                        policy_spec_schema_version=excluded.policy_spec_schema_version,
                        effective_at=excluded.effective_at,
                        lifecycle_state='active', deleted_at=NULL,
                        sandbox_id=excluded.sandbox_id,
                        source_version=excluded.source_version,
                        projection_state='fresh', projected_at=excluded.projected_at,
                        stale_at=NULL, lease_expires_at=excluded.lease_expires_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        authorization["policyVersionId"],
                        scope_id,
                        int(authorization.get("policyVersion") or 1),
                        policy_spec,
                        authorization.get("generatedAt") or now,
                        now,
                        now,
                        sandbox_id,
                        int(authorization.get("policyVersion") or 1),
                        now,
                        authorization["leaseExpiresAt"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE viewer_projections
                    SET invalidated_at=?, projection_state='stale', stale_at=?
                    WHERE scope_id=? AND viewer_membership_id=? AND id!=?
                      AND secured_resource_id IS NULL
                      AND invalidated_at IS NULL
                    """,
                    (
                        now,
                        now,
                        scope_id,
                        membership_id,
                        authorization["projectionId"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO viewer_projections (
                        id, scope_id, secured_resource_id, viewer_principal_id,
                        viewer_membership_id, policy_version_id,
                        viewer_surfaces, viewer_capabilities,
                        viewer_surfaces_schema_version,
                        viewer_capabilities_schema_version, lease_expires_at,
                        generated_at, source_version, invalidated_at, sandbox_id,
                        projection_state, projected_at, stale_at
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, '1', '1', ?, ?, ?,
                              NULL, ?, 'fresh', ?, NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        viewer_principal_id=excluded.viewer_principal_id,
                        viewer_membership_id=excluded.viewer_membership_id,
                        policy_version_id=excluded.policy_version_id,
                        viewer_surfaces=excluded.viewer_surfaces,
                        viewer_capabilities=excluded.viewer_capabilities,
                        lease_expires_at=excluded.lease_expires_at,
                        generated_at=excluded.generated_at,
                        source_version=excluded.source_version,
                        invalidated_at=NULL, sandbox_id=excluded.sandbox_id,
                        projection_state='fresh', projected_at=excluded.projected_at,
                        stale_at=NULL
                    """,
                    (
                        authorization["projectionId"],
                        scope_id,
                        principal_id,
                        membership_id,
                        authorization["policyVersionId"],
                        canonical_json(authorization["surfaces"]),
                        canonical_json(authorization["capabilities"]),
                        authorization["leaseExpiresAt"],
                        authorization.get("generatedAt") or now,
                        int(authorization.get("sourceVersion") or 1),
                        sandbox_id,
                        now,
                    ),
                )

                connection.execute(
                    """
                    INSERT INTO idempotency_records (
                        id, scope_id, idempotency_key, payload_hash, result_hash,
                        expires_at, result_object_manifest_id, status,
                        created_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'settled', ?, 'local', ?)
                    """,
                    (
                        self._stable_id("idem", operation_id, "login"),
                        scope_id,
                        idempotency_key,
                        payload_hash,
                        result_hash,
                        payload.get("refreshExpiresAt")
                        or authorization["leaseExpiresAt"],
                        now,
                        self.identity.database_generation_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO commands (
                        id, scope_id, operation_id, idempotency_key,
                        aggregate_type, aggregate_id, command_type,
                        actor_principal_id, expected_aggregate_version,
                        device_command_sequence, status, actor_membership_id,
                        payload_object_manifest_id, payload_hash, submitted_at,
                        settled_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, 'local_sandbox', ?,
                              'gc01.local.session.login', ?, ?, NULL, 'settled',
                              ?, NULL, ?, ?, ?, 'local', ?)
                    """,
                    (
                        self._stable_id("cmd", operation_id, "login"),
                        scope_id,
                        operation_id,
                        idempotency_key,
                        sandbox_id,
                        principal_id,
                        max(0, sandbox_version - 1),
                        membership_id,
                        payload_hash,
                        now,
                        now,
                        self.identity.database_generation_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO audit_events (
                        id, scope_id, operation_id, actor_id, action,
                        event_hash, actor_membership_id, target_resource_id,
                        details_object_manifest_id, occurred_at,
                        origin_instance_id, created_at, integrity_hash,
                        authority_role
                    ) VALUES (?, ?, ?, ?, 'gc01.local.session.login.applied',
                              ?, ?, NULL, NULL, ?, ?, ?, ?, 'local')
                    """,
                    (
                        self._stable_id("audit", operation_id, "login"),
                        scope_id,
                        operation_id,
                        principal_id,
                        result_hash,
                        membership_id,
                        now,
                        self.identity.database_generation_id,
                        now,
                        sha256_text(f"{operation_id}|{result_hash}|{now}"),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO reconciliation_runs (
                        id, scope_id, operation_id, registry_state_id,
                        mismatch_count, status, reconciliation_kind,
                        target_instance_id, result_object_manifest_id,
                        started_at, completed_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, NULL, 0, 'completed',
                              'gc01_local_login_projection_v1', ?, NULL, ?, ?,
                              1, 'active', ?, ?, NULL, 'local', ?)
                    """,
                    (
                        self._stable_id("recon", operation_id, "login"),
                        scope_id,
                        operation_id,
                        cloud_instance_id,
                        now,
                        now,
                        now,
                        now,
                        self.identity.database_generation_id,
                    ),
                )
                connection.execute("COMMIT")
        except Exception as exc:
            try:
                self.secret_store.delete(secret_ref)
            except SecretStoreError as cleanup_error:
                raise LocalRuntimeError(
                    503,
                    "local_login_rollback_incomplete",
                    "本机登录未生效，但临时凭据清理失败，请重试",
                ) from cleanup_error
            if isinstance(exc, LocalRuntimeError):
                raise
            raise LocalRuntimeError(
                500,
                "local_login_apply_failed",
                "组织云登录成功，但本机登录状态未生效；原工作空间保持不变",
            ) from exc

        if previous_secret_ref and previous_secret_ref != secret_ref:
            try:
                self.secret_store.delete(previous_secret_ref)
            except SecretStoreError:
                with self._connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        "UPDATE sandboxes SET runtime_status='sync_degraded', updated_at=? "
                        "WHERE id=? AND record_kind='sandbox'",
                        (utc_now(), sandbox_id),
                    )
                    connection.execute(
                        "UPDATE reconciliation_runs SET mismatch_count=1, "
                        "status='failed_retryable', updated_at=? WHERE operation_id=?",
                        (utc_now(), operation_id),
                    )
                    connection.execute("COMMIT")
        return sandbox_id

    def login(
        self,
        *,
        cloud_api_url: str,
        identifier: str,
        password: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        url = normalize_cloud_url(cloud_api_url)
        operation_key = idempotency_key or new_id()
        client = self.cloud_factory(url)
        handshake = client.handshake()
        self._validate_handshake(handshake)
        payload = client.login(
            identifier=identifier,
            password=password,
            idempotency_key=operation_key,
        )
        self._apply_login(
            url,
            handshake,
            payload,
            idempotency_key=operation_key,
        )
        return self.current()

    @staticmethod
    def _validate_restored_identity(
        sandbox,
        bundle: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        snapshot = payload.get("sessionSnapshot") or {}
        authorization = snapshot.get("authorization") or {}
        expected = {
            "cloudInstanceId": str(bundle.get("cloudInstanceId") or ""),
            "organizationId": str(bundle.get("organizationId") or ""),
            "principalId": str(bundle.get("principalId") or ""),
            "membershipId": str(bundle.get("membershipId") or ""),
        }
        if any(str(payload.get(key) or "") != value for key, value in expected.items()):
            raise LocalRuntimeError(
                409,
                "restored_session_identity_mismatch",
                "组织云会话与当前工作空间身份不一致",
            )
        if (
            str(authorization.get("scopeId") or "") != str(sandbox["scope_id"] or "")
            or str(authorization.get("organizationId") or "")
            != expected["organizationId"]
            or str(authorization.get("principalId") or "")
            != expected["principalId"]
            or str(authorization.get("membershipId") or "")
            != expected["membershipId"]
            or authorization.get("state") != "ready"
        ):
            raise LocalRuntimeError(
                409,
                "restored_authorization_identity_mismatch",
                "组织云权限投影与当前工作空间身份不一致",
            )

    def _apply_restored_authorization(
        self,
        sandbox,
        payload: dict[str, Any],
    ) -> None:
        snapshot = payload.get("sessionSnapshot") or {}
        principal = snapshot.get("principal") or {}
        authorization = snapshot.get("authorization") or {}
        scope_id = str(authorization["scopeId"])
        principal_id = str(authorization["principalId"])
        membership_id = str(authorization["membershipId"])
        policy_version_id = str(authorization["policyVersionId"])
        projection_id = str(authorization["projectionId"])
        lease_expires_at = str(authorization["leaseExpiresAt"])
        policy_version = int(authorization.get("policyVersion") or 1)
        source_version = int(authorization.get("sourceVersion") or 1)
        generated_at = str(authorization.get("generatedAt") or utc_now())
        now = utc_now()
        policy_spec = canonical_json(
            {
                "roleKey": authorization.get("systemRole") or "member",
                "surfaces": authorization["surfaces"],
                "capabilities": authorization["capabilities"],
            }
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT id FROM sandboxes WHERE id=? AND record_kind='sandbox' "
                "AND scope_id=? AND lifecycle_state!='deleted'",
                (sandbox["id"], scope_id),
            ).fetchone()
            if current is None:
                connection.execute("ROLLBACK")
                raise LocalRuntimeError(
                    409,
                    "restored_authorization_workspace_conflict",
                    "权限续租期间工作空间已发生变化",
                )
            connection.execute(
                """
                UPDATE authorization_scopes SET policy_version=?, version=?,
                    source_version=?, projection_state='fresh', projected_at=?,
                    stale_at=NULL, lease_expires_at=?, updated_at=?
                WHERE id=? AND organization_id=?
                """,
                (
                    policy_version,
                    policy_version,
                    policy_version,
                    now,
                    lease_expires_at,
                    now,
                    scope_id,
                    authorization["organizationId"],
                ),
            )
            connection.execute(
                """
                UPDATE organization_memberships SET role_key=?,
                    visibility_scope=?, version=MAX(version, ?),
                    source_version=MAX(COALESCE(source_version, 0), ?),
                    projection_state='fresh', projected_at=?, stale_at=NULL,
                    lease_expires_at=?, updated_at=?
                WHERE id=? AND scope_id=? AND principal_id=?
                  AND record_kind='membership'
                """,
                (
                    authorization.get("systemRole") or "member",
                    authorization.get("visibilityScope") or "self",
                    source_version,
                    source_version,
                    now,
                    lease_expires_at,
                    now,
                    membership_id,
                    scope_id,
                    principal_id,
                ),
            )
            # Contacts are globally unique in the local 88-table contract.
            # Logging into a second organization re-parents the shared local
            # contact projection to that cloud principal.  Switching back must
            # perform the same projection step; otherwise the restored session
            # is valid but its email/phone disappears from the local snapshot.
            for contact in principal.get("contacts") or []:
                if not isinstance(contact, dict):
                    continue
                contact_type = str(contact.get("type") or "").strip()
                contact_value = str(contact.get("value") or "").strip()
                if not contact_type or not contact_value:
                    continue
                existing_contact = connection.execute(
                    "SELECT id FROM principals WHERE normalized_contact=?",
                    (contact_value,),
                ).fetchone()
                contact_id = (
                    str(existing_contact["id"])
                    if existing_contact is not None
                    else self._stable_id("contact", contact_type, contact_value)
                )
                connection.execute(
                    """
                    INSERT INTO principals (
                        id, status, identity_version, updated_at,
                        principal_kind, parent_principal_id, contact_type,
                        normalized_contact, verification_state, version,
                        lifecycle_state, created_at, deleted_at, sandbox_id,
                        source_version, projection_state, projected_at,
                        stale_at, lease_expires_at
                    ) VALUES (?, 'active', 1, ?, 'contact', ?, ?, ?, ?, 1,
                              'active', ?, NULL, NULL, 1, 'fresh', ?, NULL, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        parent_principal_id=excluded.parent_principal_id,
                        contact_type=excluded.contact_type,
                        normalized_contact=excluded.normalized_contact,
                        verification_state=excluded.verification_state,
                        status='active', lifecycle_state='active',
                        deleted_at=NULL, updated_at=excluded.updated_at,
                        projection_state='fresh', projected_at=excluded.projected_at,
                        stale_at=NULL, lease_expires_at=excluded.lease_expires_at
                    """,
                    (
                        contact_id,
                        now,
                        principal_id,
                        contact_type,
                        contact_value,
                        contact.get("verificationState") or "verified",
                        now,
                        now,
                        lease_expires_at,
                    ),
                )
            connection.execute(
                """
                INSERT INTO policy_versions (
                    id, scope_id, secured_resource_id, policy_scope_kind,
                    version, policy_spec_schema_version, policy_spec,
                    effective_at, created_at, lifecycle_state, updated_at,
                    deleted_at, sandbox_id, source_version,
                    projection_state, projected_at, stale_at, lease_expires_at
                ) VALUES (?, ?, NULL, 'organization_role', ?,
                          'gc01.authorization.policy.v1', ?, ?, ?, 'active',
                          ?, NULL, ?, ?, 'fresh', ?, NULL, ?)
                ON CONFLICT(id) DO UPDATE SET policy_spec=excluded.policy_spec,
                    version=excluded.version,
                    policy_spec_schema_version=excluded.policy_spec_schema_version,
                    effective_at=excluded.effective_at,
                    lifecycle_state='active', deleted_at=NULL,
                    sandbox_id=excluded.sandbox_id,
                    source_version=excluded.source_version,
                    projection_state='fresh', projected_at=excluded.projected_at,
                    stale_at=NULL, lease_expires_at=excluded.lease_expires_at,
                    updated_at=excluded.updated_at
                """,
                (
                    policy_version_id,
                    scope_id,
                    policy_version,
                    policy_spec,
                    generated_at,
                    now,
                    now,
                    sandbox["id"],
                    policy_version,
                    now,
                    lease_expires_at,
                ),
            )
            connection.execute(
                """
                UPDATE viewer_projections
                SET invalidated_at=?, projection_state='stale', stale_at=?
                WHERE scope_id=? AND viewer_membership_id=? AND id!=?
                  AND secured_resource_id IS NULL
                  AND invalidated_at IS NULL
                """,
                (now, now, scope_id, membership_id, projection_id),
            )
            connection.execute(
                """
                INSERT INTO viewer_projections (
                    id, scope_id, secured_resource_id, viewer_principal_id,
                    viewer_membership_id, policy_version_id,
                    viewer_surfaces, viewer_capabilities,
                    viewer_surfaces_schema_version,
                    viewer_capabilities_schema_version, lease_expires_at,
                    generated_at, source_version, invalidated_at, sandbox_id,
                    projection_state, projected_at, stale_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, '1', '1', ?, ?, ?,
                          NULL, ?, 'fresh', ?, NULL)
                ON CONFLICT(id) DO UPDATE SET
                    viewer_principal_id=excluded.viewer_principal_id,
                    viewer_membership_id=excluded.viewer_membership_id,
                    policy_version_id=excluded.policy_version_id,
                    viewer_surfaces=excluded.viewer_surfaces,
                    viewer_capabilities=excluded.viewer_capabilities,
                    lease_expires_at=excluded.lease_expires_at,
                    generated_at=excluded.generated_at,
                    source_version=excluded.source_version,
                    invalidated_at=NULL, sandbox_id=excluded.sandbox_id,
                    projection_state='fresh', projected_at=excluded.projected_at,
                    stale_at=NULL
                """,
                (
                    projection_id,
                    scope_id,
                    principal_id,
                    membership_id,
                    policy_version_id,
                    canonical_json(authorization["surfaces"]),
                    canonical_json(authorization["capabilities"]),
                    lease_expires_at,
                    generated_at,
                    source_version,
                    sandbox["id"],
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE sandboxes SET lease_expires_at=?, last_verified_at=?,
                    updated_at=?
                WHERE (id=? AND record_kind='sandbox')
                   OR (record_kind='local_session_snapshot' AND scope_id=?
                       AND principal_id=? AND membership_id=?)
                """,
                (
                    lease_expires_at,
                    now,
                    now,
                    sandbox["id"],
                    scope_id,
                    principal_id,
                    membership_id,
                ),
            )
            connection.execute("COMMIT")

    def _refresh_local_session(
        self,
        *,
        sandbox,
        session,
        secret_reference: str,
        bundle: dict[str, Any],
        client: CloudClient,
        idempotency_key: str,
    ) -> tuple[Any, str, dict[str, Any]]:
        refresh_token = str(bundle.get("refreshToken") or "")
        payload_hash = sha256_text(
            canonical_json(
                {
                    "sandboxId": str(sandbox["id"]),
                    "serverSessionId": str(bundle.get("serverSessionId") or ""),
                    "expectedVersion": int(session["version"] or 1),
                    "refreshTokenFingerprint": sha256_text(refresh_token),
                }
            )
        )
        response = client.refresh(
            refresh_token,
            idempotency_key=idempotency_key,
        )
        if (
            str(response.get("sessionId") or "")
            != str(bundle.get("serverSessionId") or "")
            or not response.get("accessToken")
            or not response.get("refreshToken")
        ):
            raise LocalRuntimeError(
                502,
                "refreshed_session_invalid",
                "组织云返回的续期会话不完整",
            )
        updated_bundle = {
            **bundle,
            "accessToken": response["accessToken"],
            "refreshToken": response["refreshToken"],
            "expiresAt": response.get("expiresAt"),
            "refreshExpiresAt": response.get("refreshExpiresAt"),
        }
        operation_id = self._stable_id(
            "op",
            "gc01.local.session.refresh",
            str(sandbox["scope_id"]),
            idempotency_key,
        )
        replacement_reference = (
            f"workspace-session:{sandbox['id']}:{operation_id}:{new_id()}"
        )
        encoded = encode_secret_bundle(updated_bundle)
        try:
            self.secret_store.set(replacement_reference, encoded)
        except SecretStoreError as exc:
            raise LocalRuntimeError(
                503,
                "local_secret_store_unavailable",
                "组织云已续期，但本机会话凭据尚未落地；可重试恢复",
            ) from exc
        now = utc_now()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current_session = self._session_snapshot_for(
                    connection,
                    str(sandbox["id"]),
                )
                if (
                    current_session is None
                    or str(current_session["secret_reference"] or "")
                    != secret_reference
                    or int(current_session["version"] or 1)
                    != int(session["version"] or 1)
                ):
                    raise LocalRuntimeError(
                        409,
                        "local_session_refresh_conflict",
                        "本机会话已被另一操作更新，请重试",
                    )
                self._record_local_session_operation(
                    connection,
                    sandbox=sandbox,
                    session=session,
                    idempotency_key=idempotency_key,
                    command_type="gc01.local.session.refresh",
                    event_type="gc01.local.session.refreshed",
                    action="session.refresh.applied",
                    payload_hash=payload_hash,
                    now=now,
                )
                connection.execute(
                    """
                    UPDATE sandboxes SET secret_reference=?, secret_fingerprint=?,
                        access_expires_at=?, refresh_expires_at=?, last_seen_at=?,
                        last_verified_at=?, runtime_status='active', version=version+1,
                        updated_at=?
                    WHERE id=? AND record_kind='local_session_snapshot'
                      AND secret_reference=? AND version=?
                    """,
                    (
                        replacement_reference,
                        secret_fingerprint(encoded),
                        response.get("expiresAt"),
                        response.get("refreshExpiresAt"),
                        now,
                        now,
                        now,
                        session["id"],
                        secret_reference,
                        session["version"],
                    ),
                )
                connection.execute(
                    "UPDATE sandboxes SET runtime_status='ready', last_verified_at=?, "
                    "version=version+1, updated_at=? WHERE id=? AND record_kind='sandbox'",
                    (now, now, sandbox["id"]),
                )
                connection.execute("COMMIT")
        except Exception:
            try:
                self.secret_store.delete(replacement_reference)
            except SecretStoreError:
                pass
            raise
        try:
            self.secret_store.delete(secret_reference)
        except SecretStoreError:
            self._set_workspace_runtime_status(str(sandbox["id"]), "sync_degraded")
        with self._connection() as connection:
            refreshed = self._session_snapshot_for(connection, str(sandbox["id"]))
        if refreshed is None:
            raise LocalRuntimeError(500, "local_session_refresh_missing", "续期会话未落地")
        return refreshed, replacement_reference, updated_bundle

    def _restore_active_session(self) -> dict[str, Any]:
        sandbox = self._active_sandbox()
        if sandbox is None or sandbox["runtime_status"] == "needs_login":
            return self.current()
        try:
            session, secret_reference, bundle = self._load_session_bundle(sandbox)
        except LocalRuntimeError as exc:
            status = "sync_degraded" if exc.status_code >= 500 else "needs_login"
            self._set_workspace_runtime_status(str(sandbox["id"]), status)
            return self.current()
        client = self.cloud_factory(str(bundle["cloudApiUrl"]))
        try:
            handshake = client.handshake()
            self._validate_handshake(handshake)
            try:
                payload = client.current_session(str(bundle["accessToken"]))
            except CloudClientError as exc:
                if exc.status_code != 401 and exc.code not in {
                    "authorization_lease_expired",
                    "authorization_projection_missing",
                    "authorization_projection_stale",
                }:
                    raise
                refresh_key = (
                    "gc01-restore-refresh-"
                    + sha256_text(str(bundle["refreshToken"]))[:32]
                )
                session, secret_reference, bundle = self._refresh_local_session(
                    sandbox=sandbox,
                    session=session,
                    secret_reference=secret_reference,
                    bundle=bundle,
                    client=client,
                    idempotency_key=refresh_key,
                )
                payload = client.current_session(str(bundle["accessToken"]))
            self._validate_restored_identity(sandbox, bundle, payload)
            self._apply_restored_authorization(sandbox, payload)
            now = utc_now()
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE sandboxes SET runtime_status='ready', last_verified_at=?, "
                    "updated_at=? WHERE id=? AND record_kind='sandbox'",
                    (now, now, sandbox["id"]),
                )
                connection.execute(
                    "UPDATE sandboxes SET runtime_status='active', last_seen_at=?, "
                    "last_verified_at=?, updated_at=? WHERE id=? "
                    "AND record_kind='local_session_snapshot'",
                    (now, now, now, session["id"]),
                )
                connection.execute("COMMIT")
        except CloudClientError as exc:
            status = "needs_login" if exc.status_code in {401, 403} else "sync_degraded"
            self._set_workspace_runtime_status(str(sandbox["id"]), status)
        except LocalRuntimeError as exc:
            status = "sync_degraded" if exc.status_code >= 500 else "needs_login"
            self._set_workspace_runtime_status(str(sandbox["id"]), status)
        return self.current()

    def _next_request_sequence(self, proposed: int | None = None) -> int:
        with self._request_seq_lock:
            if proposed is not None and proposed > 0:
                self._request_seq = max(self._request_seq, proposed)
                return proposed
            self._request_seq = max(self._request_seq + 1, int(time.time() * 1000))
            return self._request_seq

    def _record_workspace_switch(
        self,
        connection,
        *,
        target,
        session,
        previous_sandbox_id: str | None,
        idempotency_key: str,
        request_seq: int,
        now: str,
    ) -> str:
        scope_id = str(target["scope_id"])
        operation_id = self._stable_id(
            "op",
            "gc01.local.workspace.switch",
            scope_id,
            idempotency_key,
        )
        payload_hash = sha256_text(
            canonical_json(
                {
                    "fromSandboxId": previous_sandbox_id,
                    "toSandboxId": str(target["id"]),
                    "cloudInstanceId": str(target["cloud_instance_id"] or ""),
                    "scopeId": scope_id,
                    "requestSeq": request_seq,
                }
            )
        )
        result_hash = sha256_text(
            canonical_json(
                {
                    "operationId": operation_id,
                    "sandboxId": str(target["id"]),
                    "requestSeq": request_seq,
                    "status": "ready",
                }
            )
        )
        connection.execute(
            """
            INSERT INTO idempotency_records (
                id, scope_id, idempotency_key, payload_hash, result_hash,
                expires_at, result_object_manifest_id, status, created_at,
                authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'settled', ?, 'local', ?)
            """,
            (
                self._stable_id("idem", operation_id, "workspace.switch"),
                scope_id,
                idempotency_key,
                payload_hash,
                result_hash,
                session["refresh_expires_at"] or target["lease_expires_at"],
                now,
                self.identity.database_generation_id,
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
            ) VALUES (?, ?, ?, ?, 'sandbox', ?, 'gc01.local.workspace.switch',
                      ?, ?, ?, 'settled', ?, NULL, ?, ?, ?, 'local', ?)
            """,
            (
                self._stable_id("cmd", operation_id, "workspace.switch"),
                scope_id,
                operation_id,
                idempotency_key,
                str(target["id"]),
                session["principal_id"],
                int(target["version"] or 1),
                request_seq,
                session["membership_id"],
                payload_hash,
                now,
                now,
                self.identity.database_generation_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, scope_id, operation_id, actor_id, action, event_hash,
                actor_membership_id, target_resource_id,
                details_object_manifest_id, occurred_at, origin_instance_id,
                created_at, integrity_hash, authority_role
            ) VALUES (?, ?, ?, ?, 'workspace.switch.applied', ?, ?, NULL, NULL,
                      ?, ?, ?, ?, 'local')
            """,
            (
                self._stable_id("audit", operation_id, "workspace.switch.applied"),
                scope_id,
                operation_id,
                session["principal_id"],
                result_hash,
                session["membership_id"],
                now,
                self.identity.database_generation_id,
                now,
                sha256_text(f"{operation_id}|{result_hash}|{now}"),
            ),
        )
        return operation_id

    def switch(
        self,
        sandbox_id: str,
        *,
        idempotency_key: str | None = None,
        request_seq: int | None = None,
    ) -> dict[str, Any]:
        operation_key = idempotency_key or new_id()
        sequence = self._next_request_sequence(request_seq)
        with self._connection() as connection:
            target = connection.execute(
                "SELECT * FROM sandboxes WHERE id=? AND record_kind='sandbox'",
                (sandbox_id,),
            ).fetchone()
            if target is None or target["lifecycle_state"] == "deleted":
                raise LocalRuntimeError(404, "workspace_missing", "工作空间不存在")
            replay = connection.execute(
                """
                SELECT command_type, aggregate_id FROM commands
                WHERE scope_id=? AND idempotency_key=?
                """,
                (target["scope_id"], operation_key),
            ).fetchone()
            if replay is not None:
                if (
                    replay["command_type"] != "gc01.local.workspace.switch"
                    or replay["aggregate_id"] != sandbox_id
                ):
                    raise LocalRuntimeError(
                        409,
                        "idempotency_key_conflict",
                        "该操作标识已用于另一项工作空间操作",
                    )
                return self.current()
        try:
            session, secret_reference, bundle = self._load_session_bundle(target)
            client = self.cloud_factory(str(bundle["cloudApiUrl"]))
            handshake = client.handshake()
            self._validate_handshake(handshake)
            try:
                payload = client.current_session(str(bundle["accessToken"]))
            except CloudClientError as exc:
                if exc.status_code != 401 and exc.code not in {
                    "authorization_lease_expired",
                    "authorization_projection_missing",
                    "authorization_projection_stale",
                }:
                    raise
                refresh_key = self._stable_id(
                    "switch-refresh",
                    sandbox_id,
                    str(sequence),
                )
                session, secret_reference, bundle = self._refresh_local_session(
                    sandbox=target,
                    session=session,
                    secret_reference=secret_reference,
                    bundle=bundle,
                    client=client,
                    idempotency_key=refresh_key,
                )
                payload = client.current_session(str(bundle["accessToken"]))
            self._validate_restored_identity(target, bundle, payload)
            self._apply_restored_authorization(target, payload)
            target_authorization = (
                (payload.get("sessionSnapshot") or {}).get("authorization") or {}
            )
            if "workspace_switcher" not in (
                target_authorization.get("surfaces") or []
            ):
                raise LocalRuntimeError(
                    403,
                    "permission_denied",
                    "当前组织身份没有切换到该工作空间的权限",
                )
        except CloudClientError as exc:
            if exc.status_code in {401, 403}:
                self._set_workspace_runtime_status(sandbox_id, "needs_login")
                raise LocalRuntimeError(
                    401,
                    "needs_login",
                    "目标组织的本机会话已失效，请重新登录",
                ) from exc
            self._set_workspace_runtime_status(sandbox_id, "sync_degraded")
            raise LocalRuntimeError(
                503,
                "workspace_switch_failed_retryable",
                "暂时无法验证目标组织，原工作空间保持不变",
            ) from exc

        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute(
                "SELECT * FROM sandboxes WHERE id=? AND record_kind='sandbox'",
                (sandbox_id,),
            ).fetchone()
            session = self._session_snapshot_for(connection, sandbox_id)
            if target is None or session is None:
                connection.rollback()
                raise LocalRuntimeError(409, "workspace_changed", "目标工作空间已变化，请重试")
            replay = connection.execute(
                "SELECT command_type, aggregate_id FROM commands "
                "WHERE scope_id=? AND idempotency_key=?",
                (target["scope_id"], operation_key),
            ).fetchone()
            if replay is not None:
                connection.rollback()
                if (
                    replay["command_type"] != "gc01.local.workspace.switch"
                    or replay["aggregate_id"] != sandbox_id
                ):
                    raise LocalRuntimeError(409, "idempotency_key_conflict", "操作标识冲突")
                return self.current()
            last_sequence = connection.execute(
                "SELECT MAX(device_command_sequence) FROM commands "
                "WHERE command_type='gc01.local.workspace.switch'",
            ).fetchone()[0]
            if last_sequence is not None and int(last_sequence) >= sequence:
                connection.rollback()
                raise LocalRuntimeError(
                    409,
                    "workspace_switch_superseded",
                    "该切换请求已被更新的工作空间选择取代",
                )
            active = connection.execute(
                "SELECT id FROM sandboxes WHERE record_kind='sandbox' "
                "AND lifecycle_state='active' ORDER BY updated_at DESC LIMIT 1",
            ).fetchone()
            previous_sandbox_id = str(active["id"]) if active is not None else None
            self._record_workspace_switch(
                connection,
                target=target,
                session=session,
                previous_sandbox_id=previous_sandbox_id,
                idempotency_key=operation_key,
                request_seq=sequence,
                now=now,
            )
            connection.execute(
                "UPDATE sandboxes SET lifecycle_state='archived', "
                "version=version+1, updated_at=? "
                "WHERE record_kind='sandbox' AND lifecycle_state='active' AND id!=?",
                (now, sandbox_id),
            )
            connection.execute(
                "UPDATE sandboxes SET lifecycle_state='active', runtime_status='ready', "
                "last_verified_at=?, version=version+1, updated_at=? "
                "WHERE id=? AND record_kind='sandbox'",
                (now, now, sandbox_id),
            )
            connection.execute(
                "UPDATE sandboxes SET runtime_status='active', last_seen_at=?, "
                "last_verified_at=?, updated_at=? "
                "WHERE id=? AND record_kind='local_session_snapshot'",
                (now, now, now, session["id"]),
            )
            connection.execute("COMMIT")
        return self.current()

    def restore_active(self) -> dict[str, Any]:
        return self._restore_active_session()

    def restore_at_startup(self) -> dict[str, Any]:
        return self._restore_active_session()

    def logout(self, *, idempotency_key: str | None = None) -> dict[str, Any]:
        sandbox = self._active_sandbox()
        if sandbox is None or sandbox["runtime_status"] == "needs_login":
            return self.current()
        session, secret_reference, bundle = self._load_session_bundle(sandbox)
        operation_key = idempotency_key or new_id()
        client = self.cloud_factory(str(bundle["cloudApiUrl"]))
        try:
            client.logout(
                str(bundle["accessToken"]),
                idempotency_key=operation_key,
            )
        except CloudClientError as exc:
            if exc.status_code != 401 or exc.code not in {
                "invalid_session",
                "access_expired",
            }:
                self._set_workspace_runtime_status(
                    str(sandbox["id"]),
                    "sync_degraded",
                )
                raise LocalRuntimeError(
                    503,
                    "logout_failed_retryable",
                    "暂时无法撤销组织云会话；本机凭据已保留，请重试退出",
                ) from exc
        now = utc_now()
        payload_hash = sha256_text(
            canonical_json(
                {
                    "sandboxId": str(sandbox["id"]),
                    "serverSessionId": str(bundle.get("serverSessionId") or ""),
                    "expectedVersion": int(session["version"] or 1),
                }
            )
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._record_local_session_operation(
                connection,
                sandbox=sandbox,
                session=session,
                idempotency_key=operation_key,
                command_type="gc01.local.session.logout",
                event_type="gc01.local.session.revoked",
                action="session.logout.applied",
                payload_hash=payload_hash,
                now=now,
            )
            connection.execute(
                "UPDATE sandboxes SET runtime_status='revoked', "
                "lifecycle_state='archived', version=version+1, updated_at=? "
                "WHERE id=? AND record_kind='local_session_snapshot'",
                (now, session["id"]),
            )
            connection.execute(
                "UPDATE sandboxes SET runtime_status='needs_login', "
                "version=version+1, updated_at=? WHERE id=? AND record_kind='sandbox'",
                (now, sandbox["id"]),
            )
            connection.execute("COMMIT")
        try:
            self.secret_store.delete(secret_reference)
        except SecretStoreError as exc:
            raise LocalRuntimeError(
                503,
                "logout_local_cleanup_failed",
                "组织云会话已撤销，但本机失效凭据清理失败；请重试",
            ) from exc
        with self._connection() as connection:
            connection.execute(
                "UPDATE sandboxes SET secret_reference=NULL, secret_fingerprint=NULL, "
                "access_expires_at=NULL, refresh_expires_at=NULL, updated_at=? "
                "WHERE id=? AND record_kind='local_session_snapshot'",
                (utc_now(), session["id"]),
            )
            connection.commit()
        return self.current()

    def activate_local_draft(self) -> dict[str, Any]:
        raise LocalRuntimeError(501, "personal_space_not_designed", "个人空间不在当前组织蓝图范围内")

    def capture_sandbox_context(
        self,
        *,
        expected_sandbox_id: str | None = None,
        request_seq: int | None = None,
    ) -> PinnedSandboxContext:
        sequence = self._next_request_sequence(request_seq)
        row = self._active_sandbox()
        if row is None:
            if expected_sandbox_id:
                raise LocalRuntimeError(
                    409,
                    "workspace_context_stale",
                    "请求所属工作空间已变化，请在当前空间重试",
                )
            return PinnedSandboxContext("", "organization", None, None, None, sequence)
        if expected_sandbox_id and str(row["id"]) != expected_sandbox_id:
            raise LocalRuntimeError(
                409,
                "workspace_context_stale",
                "旧工作空间请求已被丢弃",
            )
        with self._connection() as connection:
            scope = connection.execute(
                "SELECT organization_id FROM authorization_scopes WHERE id=?",
                (row["scope_id"],),
            ).fetchone()
        organization_id = str(scope["organization_id"]) if scope is not None else None
        workspace_context = None
        if row["runtime_status"] in {"ready", "sync_degraded"}:
            session, _, bundle = self._load_session_bundle(row)
            workspace_context = WorkspaceContext(
                sandbox_id=str(row["id"]),
                cloud_instance_id=str(row["cloud_instance_id"] or ""),
                organization_id=organization_id or "",
                cloud_api_url=str(bundle.get("cloudApiUrl") or ""),
                principal_id=str(session["principal_id"] or ""),
                membership_id=str(session["membership_id"] or ""),
                access_token=str(bundle.get("accessToken") or ""),
                refresh_token=str(bundle.get("refreshToken") or ""),
                access_expires_at=session["access_expires_at"],
                refresh_expires_at=session["refresh_expires_at"],
            )
        return PinnedSandboxContext(
            sandbox_id=str(row["id"]),
            sandbox_kind="organization",
            cloud_instance_id=str(row["cloud_instance_id"] or "") or None,
            organization_id=organization_id,
            scope_id=str(row["scope_id"]),
            request_seq=sequence,
            workspace_context=workspace_context,
        )

    @contextmanager
    def prebound_sandbox_context(self, context: PinnedSandboxContext) -> Iterator[None]:
        previous = getattr(self._workspace_context_local, "pinned", None)
        self._workspace_context_local.pinned = context
        try:
            yield
        finally:
            if previous is None:
                try:
                    del self._workspace_context_local.pinned
                except AttributeError:
                    pass
            else:
                self._workspace_context_local.pinned = previous

    @contextmanager
    def pinned_workspace_context(
        self,
        context: PinnedSandboxContext | None = None,
    ) -> Iterator[WorkspaceContext | PinnedSandboxContext]:
        """Keep one UI operation on the queue-time sandbox identity."""
        resolved = context or getattr(self._workspace_context_local, "pinned", None)
        if resolved is None:
            resolved = self.capture_sandbox_context()
        with self.prebound_sandbox_context(resolved):
            yield resolved.workspace_context or resolved

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        def unavailable(*_: Any, **__: Any) -> Any:
            raise LocalRuntimeError(
                501,
                "golden_chain_frozen",
                f"{name} 已按 88 表底座冻结，等待对应黄金链接通",
            )

        return unavailable
