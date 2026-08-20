from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from strict_common.agent_memory import BUILTIN_AGENT_DEFINITIONS, builtin_agent_id
from strict_common.ids import canonical_json, sha256_text, utc_now

from .runtime import LocalRuntimeError, WorkspaceRuntime


def _normalize_answer_selection_text(value: str) -> str:
    """Match browser-rendered answer text against its Markdown source."""

    text = str(value or "")
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*(?:[-+*]|\d+[.)])\s+", "", text)
    text = re.sub(r"[*_~`]", "", text)
    text = re.sub(r"[>|]", " ", text)
    return " ".join(text.split())


def _load_optional_project_knowledge(
    runtime: WorkspaceRuntime,
    project_id: str,
) -> tuple[dict[str, Any], str, str | None]:
    """Load optional cloud knowledge without blocking a local-material answer."""

    try:
        knowledge = runtime.project_knowledge_context(project_id)
    except LocalRuntimeError as exc:
        if exc.status_code == 501:
            return {}, "not_connected", "组织知识链路尚未接通"
        if exc.status_code in {401, 403, 404}:
            return {}, "blocked", "组织知识暂不可用，请检查登录、权限或项目同步状态"
        return {}, "failed_retryable", "组织知识读取失败，可稍后重试"
    state_value = knowledge.get("state")
    if isinstance(state_value, Mapping):
        state_value = state_value.get("overall")
    normalized_state = str(state_value or "ready").strip()
    if normalized_state not in {"ready", "not_connected", "blocked", "failed_retryable"}:
        normalized_state = "ready"
    message = str(knowledge.get("message") or "").strip() or None
    return dict(knowledge), normalized_state, message


class LocalWorkbenchChatRepository:
    """GC-14 local-private answer body and strict-88 projections.

    The organization cloud owns answer identity/status and the safe model
    receipt.  Question/answer text and member-local source titles remain in a
    managed file referenced by ``object_manifests`` on this device.
    """

    ANSWER_MEDIA_TYPE = "application/vnd.yiyu.local-ai-answer+json"
    CONTEXT_MEDIA_TYPE = "application/vnd.yiyu.local-ai-context+json"
    MEMORY_MEDIA_TYPE = "application/vnd.yiyu.local-answer-memory+json"
    MEMORY_SYNC_MEDIA_TYPE = (
        "application/vnd.yiyu.member-memory-safe-summary+json"
    )
    CHAT_IMAGE_MEDIA_TYPES = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/webp": "webp",
    }
    GENERATOR_VERSION = "yiyu-gc14-workbench-p07-v1"
    MEMORY_GENERATOR_VERSION = "yiyu-gc15-answer-memory-p09-v1"
    MEMORY_SYNC_GENERATOR_VERSION = "yiyu-gc15-memory-sync-p10-v1"
    STRATEGIC_PROFILE_GENERATOR_VERSION = "strategy_companion_local_wiki_v1"

    def __init__(self, runtime: WorkspaceRuntime):
        self.runtime = runtime
        self.data_root = Path(runtime.database_path).resolve().parent

    @staticmethod
    def _stable_id(prefix: str, *parts: str) -> str:
        return f"{prefix}_{sha256_text(chr(31).join(parts))[:30]}"

    def _context(self):
        return self.runtime._current_context(require_ready=True)

    @staticmethod
    def _json_object_from_model(
        raw: str,
        *,
        expected_dimensions: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        text = str(raw or "").strip()
        expected = tuple(
            expected_dimensions
            or (
                "essence",
                "business_intro",
                "cooperation",
                "people",
                "timeline",
                "next_steps",
            )
        )
        tagged: dict[str, dict[str, Any]] = {}
        for dimension in expected:
            match = re.search(
                rf"<{dimension}>\s*(.*?)\s*</{dimension}>",
                text,
                re.DOTALL | re.IGNORECASE,
            )
            if match:
                value = match.group(1).strip()
                tagged[dimension] = {
                    "narrative": "" if value in {"无", "资料不足", "暂无"} else value,
                    "sourceIds": [],
                }
        if len(tagged) == len(expected):
            return {"dimensions": tagged}
        # 单栏目刷新时，模型偶尔会漏掉闭合标签。只在请求恰好一个栏目时
        # 接受到文本末尾，避免把六栏目截断响应误当成完整档案。
        if len(expected) == 1:
            dimension = expected[0]
            loose = re.search(
                rf"<{dimension}>\s*(.*?)(?:</{dimension}>|$)",
                text,
                re.DOTALL | re.IGNORECASE,
            )
            if loose:
                value = loose.group(1).strip()
                return {
                    "dimensions": {
                        dimension: {
                            "narrative": "" if value in {"无", "资料不足", "暂无"} else value,
                            "sourceIds": [],
                        }
                    }
                }
        labels = {
            "essence": "组织介绍",
            "business_intro": "业务介绍",
            "cooperation": "合作关系",
            "people": "关键人物",
            "timeline": "时间线",
            "next_steps": "本阶段战略思路",
        }
        heading_pattern = "|".join(re.escape(label) for label in labels.values())
        heading_matches = list(
            re.finditer(
                rf"(?m)^\s*(?:[#*]+\s*)?(?:[一二三四五六\d]+[.、]\s*)?(?P<label>{heading_pattern})(?:[*#]+)?\s*[:：]?\s*$",
                text,
            )
        )
        minimum_headings = 1 if len(expected) == 1 else len(expected)
        if len(heading_matches) >= minimum_headings:
            reverse_labels = {label: key for key, label in labels.items()}
            sections: dict[str, dict[str, Any]] = {
                key: {"narrative": "", "sourceIds": []} for key in labels
            }
            for index, match in enumerate(heading_matches):
                end = (
                    heading_matches[index + 1].start()
                    if index + 1 < len(heading_matches)
                    else len(text)
                )
                narrative = text[match.end() : end].strip()
                sections[reverse_labels[match.group("label")]] = {
                    "narrative": "" if narrative in {"无", "资料不足", "暂无"} else narrative,
                    "sourceIds": [],
                }
            selected_sections = {
                key: value for key, value in sections.items() if key in expected
            }
            if len(selected_sections) == len(expected):
                return {"dimensions": selected_sections}
        if len(expected) == 1 and text:
            # 单栏目请求不存在栏目错配风险。部分模型会无视标签要求，直接返回
            # 一段已经整理好的正文；这时接收正文比把一次可用结果升级成 502 更准确。
            dimension = expected[0]
            label = labels[dimension]
            plain = re.sub(r"^```(?:text|markdown)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
            plain = re.sub(
                rf"^\s*(?:[#*]+\s*)?(?:{re.escape(label)}|{re.escape(dimension)})\s*[:：]?\s*",
                "",
                plain,
                flags=re.IGNORECASE,
            ).strip()
            if plain:
                return {
                    "dimensions": {
                        dimension: {
                            "narrative": "" if plain in {"无", "资料不足", "暂无"} else plain,
                            "sourceIds": [],
                        }
                    }
                }
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise LocalRuntimeError(502, "strategic_profile_response_invalid", "战略陪伴模型未返回有效档案结构")
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LocalRuntimeError(502, "strategic_profile_response_invalid", "战略陪伴模型返回的档案结构无法解析") from exc
        if not isinstance(parsed, dict):
            raise LocalRuntimeError(502, "strategic_profile_response_invalid", "战略陪伴模型返回的档案结构无效")
        return parsed

    def _managed_path(self, storage_key: str) -> Path:
        candidate = (self.data_root / storage_key).resolve()
        if self.data_root not in candidate.parents:
            raise LocalRuntimeError(
                422,
                "local_storage_path_invalid",
                "本机受管路径越界",
            )
        return candidate

    def _write_managed_object(
        self,
        *,
        object_id: str,
        storage_key: str,
        media_type: str,
        payload: Mapping[str, Any],
    ) -> str:
        context = self._context()
        data = canonical_json(dict(payload)).encode("utf-8")
        content_hash = hashlib.sha256(data).hexdigest()
        current = self.runtime.local_storage_object_get(
            sandbox_id=context.sandbox_id,
            object_id=object_id,
        )
        if (
            current is not None
            and str(current.get("content_hash") or "") == content_hash
            and str(current.get("storage_key") or "") == storage_key
            and str(current.get("lifecycle_state") or "") == "active"
        ):
            return str(current["manifest_id"])
        path = self._managed_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
        stored = self.runtime.local_storage_object_put(
            sandbox_id=context.sandbox_id,
            object_id=object_id,
            storage_key=storage_key,
            content_hash=content_hash,
            media_type=media_type,
            byte_size=len(data),
            expected_version=int((current or {}).get("version") or 0),
        )
        return str(stored["manifest_id"])

    def _read_managed_payload(
        self,
        object_id: str,
        *,
        manifest: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self._context()
        if manifest is None:
            manifest = self.runtime.local_storage_object_get(
                sandbox_id=context.sandbox_id,
                object_id=object_id,
            )
        if manifest is None or str(manifest.get("lifecycle_state") or "") != "active":
            raise LocalRuntimeError(404, "workbench_answer_missing", "工作台回答不存在")
        path = self._managed_path(str(manifest.get("storage_key") or ""))
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise LocalRuntimeError(
                409,
                "workbench_answer_body_missing",
                "回答记录存在，但本机正文不可读取",
            ) from exc
        if (
            len(data) != int(manifest.get("byte_size") or 0)
            or hashlib.sha256(data).hexdigest()
            != str(manifest.get("content_hash") or "")
        ):
            raise LocalRuntimeError(
                409,
                "workbench_answer_body_corrupt",
                "本机回答正文校验失败",
            )
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LocalRuntimeError(
                409,
                "workbench_answer_body_invalid",
                "本机回答正文格式无效",
            ) from exc
        if not isinstance(payload, dict):
            raise LocalRuntimeError(
                409,
                "workbench_answer_body_invalid",
                "本机回答正文格式无效",
            )
        return payload

    def persist_chat_images(
        self,
        *,
        project_id: str,
        thread_id: str,
        images: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Store chat-only images locally without creating project files."""

        context = self._context()
        receipts: list[dict[str, Any]] = []
        for index, image in enumerate(images):
            mime_type = str(image.get("mimeType") or "").strip().lower()
            extension = self.CHAT_IMAGE_MEDIA_TYPES.get(mime_type)
            raw = image.get("bytes")
            if extension is None or not isinstance(raw, bytes) or not raw:
                raise LocalRuntimeError(422, "chat_image_invalid", "图片内容无效")
            content_hash = hashlib.sha256(raw).hexdigest()
            supplied_hash = str(image.get("contentHash") or "").strip()
            if supplied_hash and supplied_hash != content_hash:
                raise LocalRuntimeError(409, "chat_image_hash_mismatch", "图片内容校验失败")
            object_id = self._stable_id(
                "chat-image",
                project_id,
                thread_id,
                str(index),
                content_hash,
            )
            storage_key = (
                f"managed/private/workbench/{context.sandbox_id}/"
                f"chat-images/{object_id}.{extension}"
            )
            with self.runtime.local_storage_object_lock(
                sandbox_id=context.sandbox_id,
                object_id=object_id,
            ):
                current = self.runtime.local_storage_object_get(
                    sandbox_id=context.sandbox_id,
                    object_id=object_id,
                )
                path = self._managed_path(storage_key)
                existing_valid = False
                if (
                    current is not None
                    and str(current.get("content_hash") or "") == content_hash
                    and str(current.get("storage_key") or "") == storage_key
                    and str(current.get("lifecycle_state") or "") == "active"
                ):
                    try:
                        existing = path.read_bytes()
                        existing_valid = (
                            len(existing) == len(raw)
                            and hashlib.sha256(existing).hexdigest() == content_hash
                        )
                    except OSError:
                        existing_valid = False
                if not existing_valid:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    descriptor, temporary_name = tempfile.mkstemp(
                        prefix=f".{path.name}.",
                        suffix=".tmp",
                        dir=path.parent,
                    )
                    try:
                        with os.fdopen(descriptor, "wb") as temporary:
                            temporary.write(raw)
                            temporary.flush()
                            os.fsync(temporary.fileno())
                        os.replace(temporary_name, path)
                    finally:
                        try:
                            os.unlink(temporary_name)
                        except FileNotFoundError:
                            pass
                    if current is None or (
                        str(current.get("content_hash") or "") != content_hash
                        or str(current.get("storage_key") or "") != storage_key
                        or str(current.get("lifecycle_state") or "") != "active"
                    ):
                        self.runtime.local_storage_object_put(
                            sandbox_id=context.sandbox_id,
                            object_id=object_id,
                            storage_key=storage_key,
                            content_hash=content_hash,
                            media_type=mime_type,
                            byte_size=len(raw),
                            expected_version=int((current or {}).get("version") or 0),
                        )
            receipts.append(
                {
                    "objectId": object_id,
                    "name": str(image.get("name") or f"图片{index + 1}")[:120],
                    "mimeType": mime_type,
                    "size": len(raw),
                    "contentHash": content_hash,
                }
            )
        return receipts

    def resolve_chat_images(
        self,
        receipts: Iterable[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Resolve verified local image references for renderer history."""

        context = self._context()
        normalized = [dict(item) for item in receipts if isinstance(item, Mapping)]
        object_ids = [str(item.get("objectId") or "").strip() for item in normalized]
        manifests = self.runtime.local_storage_objects_get(
            sandbox_id=context.sandbox_id,
            object_ids=object_ids,
        )
        resolved: list[dict[str, Any]] = []
        for item in normalized:
            object_id = str(item.get("objectId") or "").strip()
            manifest = manifests.get(object_id)
            if not object_id or manifest is None:
                continue
            mime_type = str(manifest.get("media_type") or "").strip().lower()
            if (
                mime_type not in self.CHAT_IMAGE_MEDIA_TYPES
                or str(manifest.get("lifecycle_state") or "") != "active"
                or str(item.get("contentHash") or "")
                != str(manifest.get("content_hash") or "")
            ):
                continue
            try:
                data = self._managed_path(str(manifest.get("storage_key") or "")).read_bytes()
            except OSError:
                continue
            if (
                len(data) != int(manifest.get("byte_size") or 0)
                or hashlib.sha256(data).hexdigest()
                != str(manifest.get("content_hash") or "")
            ):
                continue
            resolved.append(
                {
                    "id": object_id,
                    "name": str(item.get("name") or "图片")[:120],
                    "mimeType": mime_type,
                    "dataUrl": f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}",
                }
            )
        return resolved

    @staticmethod
    def _temperature(mode: str) -> float:
        return 0.8 if mode == "creative" else 0.1 if mode == "strict" else 0.3

    @staticmethod
    def _safe_cloud_sources(sources: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "sourceObjectId": str(item.get("sourceObjectId") or ""),
                "sourceObjectKind": str(item.get("sourceObjectKind") or ""),
                "sourceVersion": max(1, int(item.get("sourceVersion") or 1)),
                "contentHash": str(item.get("contentHash") or ""),
            }
            for item in sources
            if str(item.get("sourceObjectId") or "")
            and str(item.get("sourceObjectKind") or "")
        ]

    def _project_agent_and_provider(
        self,
        *,
        provider: Mapping[str, Any],
        bot_id: str,
    ) -> None:
        context = self._context()
        now = utc_now()
        definition = next(
            item
            for item in BUILTIN_AGENT_DEFINITIONS
            if item.agent_kind == "project_workspace"
        )
        provider_id = str(provider.get("configId") or "")
        if not provider_id:
            raise LocalRuntimeError(
                502,
                "organization_ai_config_invalid",
                "组织模型缺少资源标识",
            )
        with self.runtime._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO secured_resources (
                    id, scope_id, resource_kind, lifecycle_state, version,
                    resource_type_key, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, 'bot_definition', 'active', 1,
                          'builtin_function_agent', ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    scope_id=excluded.scope_id, lifecycle_state='active',
                    resource_kind='bot_definition',
                    resource_type_key='builtin_function_agent',
                    updated_at=excluded.updated_at, deleted_at=NULL,
                    authority_role='cloud',
                    origin_instance_id=excluded.origin_instance_id
                """,
                (
                    bot_id,
                    str(context.organization_id and self.runtime.capture_sandbox_context().scope_id or ""),
                    now,
                    now,
                    context.cloud_instance_id,
                ),
            )
            scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
            connection.execute(
                """
                INSERT INTO bot_definitions (
                    id, scope_id, agent_kind, owner_principal_id,
                    owner_membership_id, permission_policy_id, version,
                    handle, description, department_id,
                    capability_policy_version, secret_reference,
                    secret_fingerprint, enabled, lifecycle_state, created_at,
                    updated_at, deleted_at, sandbox_id, source_version,
                    projection_state, projected_at, stale_at, lease_expires_at
                ) VALUES (?, ?, 'project_workspace', NULL, NULL, NULL, 1,
                          ?, ?, NULL, ?, NULL, NULL, 1, 'active', ?, ?, NULL,
                          ?, 1, 'current', ?, NULL, ?)
                ON CONFLICT(id) DO UPDATE SET
                    scope_id=excluded.scope_id, handle=excluded.handle,
                    description=excluded.description, enabled=1,
                    capability_policy_version=excluded.capability_policy_version,
                    lifecycle_state='active', updated_at=excluded.updated_at,
                    deleted_at=NULL, sandbox_id=excluded.sandbox_id,
                    source_version=excluded.source_version,
                    projection_state='current', projected_at=excluded.projected_at,
                    stale_at=NULL, lease_expires_at=excluded.lease_expires_at
                """,
                (
                    bot_id,
                    scope_id,
                    definition.handle,
                    definition.description,
                    definition.capability_policy_version,
                    now,
                    now,
                    context.sandbox_id,
                    now,
                    (datetime.now(timezone.utc) + timedelta(hours=24))
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                ),
            )
            connection.execute(
                """
                INSERT INTO provider_resources (
                    id, scope_id, provider, resource_kind, remote_id,
                    retention_state, owner_kind, owner_principal_id,
                    owner_membership_id, display_name, endpoint, model_name,
                    public_config_schema_version, public_config,
                    secret_reference, secret_fingerprint, status, verified_at,
                    version, lifecycle_state, created_at, updated_at, deleted_at,
                    authority_role, origin_instance_id
                ) VALUES (?, ?, ?, 'organization_ai_configuration', ?,
                          'organization_managed', 'organization', NULL, NULL,
                          '组织大模型', ?, ?, NULL, NULL, NULL, ?, ?, ?, ?,
                          'active', ?, ?, NULL, 'cloud', ?)
                ON CONFLICT(id) DO UPDATE SET
                    scope_id=excluded.scope_id, provider=excluded.provider,
                    remote_id=excluded.remote_id, endpoint=excluded.endpoint,
                    model_name=excluded.model_name,
                    secret_reference=NULL,
                    secret_fingerprint=excluded.secret_fingerprint,
                    status=excluded.status, verified_at=excluded.verified_at,
                    version=excluded.version, lifecycle_state='active',
                    updated_at=excluded.updated_at, deleted_at=NULL,
                    authority_role='cloud',
                    origin_instance_id=excluded.origin_instance_id
                """,
                (
                    provider_id,
                    scope_id,
                    str(provider.get("provider") or "openai_compatible"),
                    provider_id,
                    str(provider.get("baseUrl") or ""),
                    str(provider.get("modelName") or ""),
                    str(provider.get("keyFingerprint") or "") or None,
                    str(provider.get("status") or "ready"),
                    now,
                    max(1, int(provider.get("version") or 1)),
                    now,
                    now,
                    context.cloud_instance_id,
                ),
            )
            connection.execute("COMMIT")

    def _persist_pending(
        self,
        *,
        answer_id: str,
        client_id: str,
        thread_id: str,
        question: str,
        answer_markdown: str,
        source_manifest: Mapping[str, Any],
        source_set_id: str,
        context_manifest_id: str,
        lineage_id: str,
        provider_id: str,
        bot_id: str,
        model_name: str,
        sources: list[dict[str, Any]],
        material_access_mode: str,
        boundary_state: str,
        created_at: str,
    ) -> dict[str, Any]:
        context = self._context()
        question_hash = sha256_text(question)
        answer_hash = sha256_text(answer_markdown)
        context_object_id = f"ai-context:{context_manifest_id}"
        answer_object_id = f"ai-answer:{answer_id}"
        context_payload = {
            "schema": "yiyu.local-ai-context.v1",
            "clientId": client_id,
            "threadId": thread_id,
            "questionHash": question_hash,
            "sourceSetId": source_set_id,
            "selectedSources": sources,
            "sourceCount": len(sources),
            "materialAccessMode": material_access_mode,
            "boundaryState": boundary_state,
            "memoryState": source_manifest.get("memoryState") or "ready",
            "memoryMessage": source_manifest.get("memoryMessage"),
            "generatorVersion": self.GENERATOR_VERSION,
            "createdAt": created_at,
        }
        context_object_manifest_id = self._write_managed_object(
            object_id=context_object_id,
            storage_key=(
                f"managed/private/workbench/{context.sandbox_id}/contexts/"
                f"{context_manifest_id}.json"
            ),
            media_type=self.CONTEXT_MEDIA_TYPE,
            payload=context_payload,
        )
        local_answer = {
            "schema": "yiyu.local-ai-answer.v1",
            "answerId": answer_id,
            "projectId": client_id,
            "threadId": thread_id,
            "question": question,
            "answerMarkdown": answer_markdown,
            "sourceManifest": dict(source_manifest),
            "modelName": model_name,
            "providerResourceId": provider_id,
            "botId": bot_id,
            "sourceSetId": source_set_id,
            "aiContextManifestId": context_manifest_id,
            "questionHash": question_hash,
            "answerHash": answer_hash,
            "sourceCount": len(sources),
            "materialAccessMode": material_access_mode,
            "boundaryState": boundary_state,
            "version": 1,
            "createdAt": created_at,
            "updatedAt": created_at,
        }
        answer_object_manifest_id = self._write_managed_object(
            object_id=answer_object_id,
            storage_key=(
                f"managed/private/workbench/{context.sandbox_id}/answers/"
                f"{answer_id}.json"
            ),
            media_type=self.ANSWER_MEDIA_TYPE,
            payload=local_answer,
        )
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        with self.runtime._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            project = connection.execute(
                """
                SELECT id FROM clients
                WHERE id=? AND scope_id=? AND sandbox_id=?
                  AND lifecycle_state='active'
                """,
                (client_id, scope_id, context.sandbox_id),
            ).fetchone()
            if project is None:
                connection.execute("ROLLBACK")
                raise LocalRuntimeError(404, "project_missing", "当前工作空间没有该项目")
            connection.execute(
                """
                INSERT INTO source_sets (
                    id, scope_id, client_id, security_label_set_version,
                    source_count, version, purpose_kind, publication_state,
                    created_by_principal_id, created_at, expires_at,
                    lifecycle_state, updated_at, deleted_at, authority_role,
                    origin_instance_id
                ) VALUES (?, ?, ?, 'policy-v1', ?, 1, 'ai_answer_context',
                          'draft', ?, ?, NULL, 'active', ?, NULL, 'local', ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_count=excluded.source_count,
                    updated_at=excluded.updated_at, lifecycle_state='active',
                    deleted_at=NULL
                """,
                (
                    source_set_id,
                    scope_id,
                    client_id,
                    len(sources),
                    context.principal_id,
                    created_at,
                    created_at,
                    self.runtime.identity.database_generation_id,
                ),
            )
            for ordinal, source in enumerate(sources):
                connection.execute(
                    """
                    INSERT INTO source_set_members (
                        id, scope_id, source_set_id, source_object_id,
                        source_version, policy_version, source_object_kind,
                        ordinal, added_at, removed_at, version,
                        lifecycle_state, created_at, updated_at, deleted_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, NULL, 1, 'active',
                              ?, ?, NULL, 'local', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        ordinal=excluded.ordinal, removed_at=NULL,
                        lifecycle_state='active', updated_at=excluded.updated_at
                    """,
                    (
                        self._stable_id(
                            "source_member",
                            source_set_id,
                            f"{source['sourceObjectKind']}:{source['sourceObjectId']}",
                        ),
                        scope_id,
                        source_set_id,
                        source["sourceObjectId"],
                        source["sourceVersion"],
                        source["sourceObjectKind"],
                        ordinal,
                        created_at,
                        created_at,
                        created_at,
                        self.runtime.identity.database_generation_id,
                    ),
                )
            connection.execute(
                """
                INSERT INTO derivation_lineage (
                    id, scope_id, source_set_id, policy_version_id,
                    grant_generation, derivative_kind, derivative_object_id,
                    generator_version, generated_at, invalidated_at,
                    source_version, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, NULL, 1, 'ai_context_manifest', ?, ?, ?,
                          NULL, 1, 'local', ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_set_id=excluded.source_set_id,
                    derivative_object_id=excluded.derivative_object_id,
                    generated_at=excluded.generated_at, invalidated_at=NULL
                """,
                (
                    lineage_id,
                    scope_id,
                    source_set_id,
                    context_manifest_id,
                    self.GENERATOR_VERSION,
                    created_at,
                    self.runtime.identity.database_generation_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO ai_context_manifests (
                    id, scope_id, lineage_id, provider_resource_id,
                    policy_version, status, source_set_id, question_hash,
                    retrieval_policy_version, selected_source_count,
                    context_object_manifest_id, generated_at, invalidated_at,
                    source_version, authority_role, origin_instance_id
                ) VALUES (?, ?, ?, ?, 1, 'ready', ?, ?, ?, ?, ?, ?, NULL,
                          1, 'local', ?)
                ON CONFLICT(id) DO UPDATE SET
                    status='ready', selected_source_count=excluded.selected_source_count,
                    context_object_manifest_id=excluded.context_object_manifest_id,
                    invalidated_at=NULL
                """,
                (
                    context_manifest_id,
                    scope_id,
                    lineage_id,
                    provider_id,
                    source_set_id,
                    question_hash,
                    self.GENERATOR_VERSION,
                    len(sources),
                    context_object_manifest_id,
                    created_at,
                    self.runtime.identity.database_generation_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO ai_answers (
                    id, scope_id, client_id, bot_id, source_set_id, status,
                    created_at, thread_id, ai_context_manifest_id,
                    provider_resource_id, model_name,
                    answer_object_manifest_id, answer_hash, source_count,
                    material_access_mode, boundary_state, version,
                    lifecycle_state, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, 'pending_sync', ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, 1, 'active', ?, NULL)
                ON CONFLICT(id) DO UPDATE SET
                    status=CASE WHEN ai_answers.status='ready' THEN 'ready'
                                ELSE 'pending_sync' END,
                    updated_at=excluded.updated_at
                """,
                (
                    answer_id,
                    scope_id,
                    client_id,
                    bot_id,
                    source_set_id,
                    created_at,
                    thread_id,
                    context_manifest_id,
                    provider_id,
                    model_name,
                    answer_object_manifest_id,
                    answer_hash,
                    len(sources),
                    material_access_mode,
                    boundary_state,
                    created_at,
                ),
            )
            connection.execute("COMMIT")
        return local_answer

    def _mark_ready(self, answer_id: str, *, updated_at: str) -> None:
        with self.runtime._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE ai_answers SET status='ready', updated_at=?
                WHERE id=? AND lifecycle_state='active'
                """,
                (updated_at, answer_id),
            )
            connection.execute("COMMIT")

    def _existing(self, answer_id: str) -> tuple[dict[str, Any], str] | None:
        context = self._context()
        with self.runtime._connection() as connection:
            row = connection.execute(
                """
                SELECT status FROM ai_answers
                WHERE id=? AND scope_id=? AND lifecycle_state='active'
                """,
                (
                    answer_id,
                    str(self.runtime.capture_sandbox_context().scope_id or ""),
                ),
            ).fetchone()
        if row is None:
            return None
        payload = self._read_managed_payload(f"ai-answer:{answer_id}")
        if str(payload.get("projectId") or "") == "":
            raise LocalRuntimeError(409, "workbench_answer_body_invalid", "回答缺少项目归属")
        return payload, str(row["status"] or "")

    def answer(self, answer_id: str) -> dict[str, Any]:
        existing = self._existing(answer_id)
        if existing is None:
            raise LocalRuntimeError(404, "workbench_answer_missing", "工作台回答不存在")
        return existing[0]

    def thread(self, client_id: str, thread_id: str) -> list[dict[str, Any]]:
        context = self._context()
        with self.runtime._connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM ai_answers
                WHERE scope_id=? AND client_id=? AND thread_id=?
                  AND lifecycle_state='active'
                ORDER BY created_at, id
                """,
                (
                    str(self.runtime.capture_sandbox_context().scope_id or ""),
                    client_id,
                    thread_id,
                ),
            ).fetchall()
        object_ids = [f"ai-answer:{row['id']}" for row in rows]
        manifests = self.runtime.local_storage_objects_get(
            sandbox_id=context.sandbox_id,
            object_ids=object_ids,
        )
        answers = [
            self._read_managed_payload(object_id, manifest=manifests.get(object_id))
            for object_id in object_ids
        ]
        return [
            answer
            for answer in answers
            if str(answer.get("projectId") or "") == client_id
            and str(answer.get("threadId") or "") == thread_id
        ]

    def project_answers(self, client_id: str) -> list[dict[str, Any]]:
        context = self._context()
        with self.runtime._connection() as connection:
            rows = connection.execute(
                """
                SELECT id FROM ai_answers
                WHERE scope_id=? AND client_id=? AND status='ready'
                  AND lifecycle_state='active'
                ORDER BY created_at, id
                """,
                (
                    str(self.runtime.capture_sandbox_context().scope_id or ""),
                    client_id,
                ),
            ).fetchall()
        object_ids = [f"ai-answer:{row['id']}" for row in rows]
        manifests = self.runtime.local_storage_objects_get(
            sandbox_id=context.sandbox_id,
            object_ids=object_ids,
        )
        answers = [
            self._read_managed_payload(object_id, manifest=manifests.get(object_id))
            for object_id in object_ids
        ]
        return [
            answer
            for answer in answers
            if str(answer.get("projectId") or "") == client_id
        ]

    @staticmethod
    def _memory_definition(memory_kind: str) -> tuple[str, str, str]:
        normalized = str(memory_kind or "").strip().lower()
        if normalized in {"favorite", "favorite_memory"}:
            return "favorite_memory", "收藏", "favorite"
        raise LocalRuntimeError(422, "memory_kind_invalid", "不支持的记忆类型")

    def _record_memory_operation(
        self,
        connection: Any,
        *,
        operation_id: str,
        idempotency_key: str,
        command_type: str,
        event_type: str,
        action: str,
        aggregate_id: str,
        aggregate_version: int,
        payload_hash: str,
        result_hash: str,
        now: str,
    ) -> None:
        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        expires_at = (
            context.refresh_expires_at
            or (datetime.now(timezone.utc) + timedelta(days=365))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
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
                expires_at,
                now,
                self.runtime.identity.database_generation_id,
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
            ) VALUES (?, ?, ?, ?, 'knowledge_document', ?, ?, ?, ?, NULL,
                      'settled', ?, NULL, ?, ?, ?, 'local', ?)
            """,
            (
                self._stable_id("cmd", operation_id, command_type),
                scope_id,
                operation_id,
                idempotency_key,
                aggregate_id,
                command_type,
                context.principal_id,
                aggregate_version,
                context.membership_id,
                payload_hash,
                now,
                now,
                self.runtime.identity.database_generation_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO outbox_events (
                id, scope_id, operation_id, aggregate_version, event_type,
                status, aggregate_type, aggregate_id,
                event_object_manifest_id, event_hash, available_at,
                published_at, authority_role, origin_instance_id
            ) VALUES (?, ?, ?, ?, ?, 'published', 'knowledge_document', ?,
                      NULL, ?, ?, ?, 'local', ?)
            """,
            (
                self._stable_id("evt", operation_id, event_type),
                scope_id,
                operation_id,
                aggregate_version,
                event_type,
                aggregate_id,
                result_hash,
                now,
                now,
                self.runtime.identity.database_generation_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events (
                id, scope_id, operation_id, actor_id, action, event_hash,
                actor_membership_id, target_resource_id,
                details_object_manifest_id, occurred_at, origin_instance_id,
                created_at, integrity_hash, authority_role
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, 'local')
            """,
            (
                self._stable_id("audit", operation_id, action),
                scope_id,
                operation_id,
                context.principal_id,
                action,
                result_hash,
                context.membership_id,
                aggregate_id,
                now,
                self.runtime.identity.database_generation_id,
                now,
                sha256_text(f"{operation_id}|{result_hash}|{now}"),
            ),
        )

    def save_answer_memory(
        self,
        *,
        project_id: str,
        answer_id: str,
        memory_kind: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        document_kind, label, source_kind = self._memory_definition(memory_kind)
        operation_key = str(idempotency_key or "").strip()
        if not operation_key:
            raise LocalRuntimeError(422, "idempotency_required", "保存记忆缺少幂等键")
        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        answer = self.answer(answer_id)
        if str(answer.get("projectId") or "") != project_id:
            raise LocalRuntimeError(409, "memory_project_mismatch", "回答不属于当前项目")
        document_id = self._stable_id(
            "memory", context.sandbox_id, project_id, answer_id, source_kind
        )
        source_set_id = self._stable_id("source_set", document_id)
        lineage_id = self._stable_id("lineage", document_id)
        payload_hash = sha256_text(
            canonical_json(
                {
                    "projectId": project_id,
                    "answerId": answer_id,
                    "memoryKind": source_kind,
                }
            )
        )
        with self.runtime._connection() as connection:
            replay = connection.execute(
                """
                SELECT payload_hash FROM idempotency_records
                WHERE scope_id=? AND idempotency_key=?
                """,
                (scope_id, operation_key),
            ).fetchone()
            if replay is not None:
                if str(replay["payload_hash"] or "") != payload_hash:
                    raise LocalRuntimeError(
                        409,
                        "memory_idempotency_conflict",
                        "该幂等键已用于另一项记忆操作",
                    )
                row = connection.execute(
                    "SELECT version, lifecycle_state, updated_at FROM knowledge_documents WHERE id=? AND scope_id=?",
                    (document_id, scope_id),
                ).fetchone()
                if row is None:
                    raise LocalRuntimeError(409, "memory_replay_incomplete", "记忆操作回执不完整")
                return {
                    "clientId": project_id,
                    "memoryId": document_id,
                    "answerId": answer_id,
                    "memoryKind": source_kind,
                    "version": int(row["version"] or 1),
                    "status": str(row["lifecycle_state"] or "active"),
                    "updatedAt": row["updated_at"],
                    "idempotentReplay": True,
                }
            existing = connection.execute(
                "SELECT version, current_version, lifecycle_state, created_at FROM knowledge_documents WHERE id=? AND scope_id=?",
                (document_id, scope_id),
            ).fetchone()
            if existing is not None and str(existing["lifecycle_state"] or "") == "active":
                return {
                    "clientId": project_id,
                    "memoryId": document_id,
                    "answerId": answer_id,
                    "memoryKind": source_kind,
                    "version": int(existing["version"] or 1),
                    "status": "active",
                    "updatedAt": answer.get("updatedAt") or utc_now(),
                    "alreadySaved": True,
                }

        now = utc_now()
        memory_payload = {
            "schema": "yiyu.local-answer-memory.v1",
            "clientId": project_id,
            "sourceAnswerId": answer_id,
            "memoryKind": source_kind,
            "title": str(answer.get("question") or "工作台回答")[:120],
            "content": str(answer.get("answerMarkdown") or ""),
            "answerHash": str(answer.get("answerHash") or ""),
            "createdAt": now,
        }
        object_manifest_id = self._write_managed_object(
            object_id=f"answer-memory:{document_id}",
            storage_key=(
                f"managed/private/workbench/{context.sandbox_id}/memories/"
                f"{document_id}.json"
            ),
            media_type=self.MEMORY_MEDIA_TYPE,
            payload=memory_payload,
        )
        content_hash = sha256_text(str(memory_payload["content"]))
        result_hash = sha256_text(
            canonical_json(
                {
                    "memoryId": document_id,
                    "memoryKind": source_kind,
                    "contentHash": content_hash,
                    "status": "active",
                }
            )
        )
        operation_id = self._stable_id(
            "op", "gc15.memory.save", scope_id, operation_key
        )
        with self.runtime._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                project = connection.execute(
                    """
                    SELECT id FROM clients
                    WHERE id=? AND scope_id=? AND sandbox_id=?
                      AND lifecycle_state='active'
                    """,
                    (project_id, scope_id, context.sandbox_id),
                ).fetchone()
                answer_row = connection.execute(
                    """
                    SELECT id FROM ai_answers
                    WHERE id=? AND scope_id=? AND client_id=?
                      AND lifecycle_state='active'
                    """,
                    (answer_id, scope_id, project_id),
                ).fetchone()
                if project is None or answer_row is None:
                    raise LocalRuntimeError(409, "memory_scope_changed", "项目或回答作用域已变化")
                existing = connection.execute(
                    "SELECT version, current_version, created_at FROM knowledge_documents WHERE id=?",
                    (document_id,),
                ).fetchone()
                next_version = int(existing["version"] or 0) + 1 if existing else 1
                current_version = int(existing["current_version"] or 0) + 1 if existing else 1
                created_at = str(existing["created_at"] or now) if existing else now
                connection.execute(
                    """
                    INSERT INTO secured_resources (
                        id, scope_id, resource_kind, lifecycle_state, version,
                        resource_type_key, created_at, updated_at, deleted_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, 'knowledge_document', 'active', ?, ?, ?, ?, NULL,
                              'local', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        lifecycle_state='active', version=excluded.version,
                        updated_at=excluded.updated_at, deleted_at=NULL,
                        authority_role='local', origin_instance_id=excluded.origin_instance_id
                    """,
                    (
                        document_id,
                        scope_id,
                        next_version,
                        document_kind,
                        created_at,
                        now,
                        self.runtime.identity.database_generation_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO knowledge_documents (
                        id, scope_id, source_asset_id, client_id, current_version,
                        owner_membership_id, title, document_kind, visibility_scope,
                        parse_state, publication_state, published_at, version,
                        lifecycle_state, created_at, updated_at, deleted_at,
                        sandbox_id, source_version, projection_state, projected_at,
                        stale_at, lease_expires_at
                    ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'self', 'ready', 'draft',
                              NULL, ?, 'active', ?, ?, NULL, ?, ?, 'current', ?, NULL, NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        current_version=excluded.current_version,
                        owner_membership_id=excluded.owner_membership_id,
                        title=excluded.title, document_kind=excluded.document_kind,
                        visibility_scope='self', parse_state='ready',
                        publication_state='draft', published_at=NULL,
                        version=excluded.version, lifecycle_state='active',
                        updated_at=excluded.updated_at, deleted_at=NULL,
                        sandbox_id=excluded.sandbox_id,
                        source_version=excluded.source_version,
                        projection_state='current', projected_at=excluded.projected_at,
                        stale_at=NULL, lease_expires_at=NULL
                    """,
                    (
                        document_id,
                        scope_id,
                        project_id,
                        current_version,
                        context.membership_id,
                        f"{label}：{str(answer.get('question') or '工作台回答')[:100]}",
                        document_kind,
                        next_version,
                        created_at,
                        now,
                        context.sandbox_id,
                        next_version,
                        now,
                    ),
                )
                document_version_id = self._stable_id(
                    "document_version", document_id, str(current_version)
                )
                connection.execute(
                    """
                    INSERT INTO document_versions (
                        id, scope_id, document_id, version, content_hash,
                        created_at, object_manifest_id, source_asset_version,
                        publication_state, created_by_membership_id,
                        origin_instance_id, integrity_hash, sandbox_id,
                        source_version, projection_state, projected_at,
                        stale_at, lease_expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'draft', ?, ?, ?, ?, ?,
                              'current', ?, NULL, NULL)
                    """,
                    (
                        document_version_id,
                        scope_id,
                        document_id,
                        current_version,
                        content_hash,
                        now,
                        object_manifest_id,
                        context.membership_id,
                        self.runtime.identity.database_generation_id,
                        sha256_text(f"{document_id}|{current_version}|{content_hash}"),
                        context.sandbox_id,
                        next_version,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO source_sets (
                        id, scope_id, client_id, security_label_set_version,
                        source_count, version, purpose_kind, publication_state,
                        created_by_principal_id, created_at, expires_at,
                        lifecycle_state, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, 'self-v1', 1, ?, 'answer_memory', 'draft',
                              ?, ?, NULL, 'active', ?, NULL, 'local', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        version=excluded.version, publication_state='draft',
                        lifecycle_state='active', updated_at=excluded.updated_at,
                        deleted_at=NULL
                    """,
                    (
                        source_set_id,
                        scope_id,
                        project_id,
                        next_version,
                        context.principal_id,
                        created_at,
                        now,
                        self.runtime.identity.database_generation_id,
                    ),
                )
                source_member_id = self._stable_id("source_member", source_set_id, answer_id)
                connection.execute(
                    """
                    INSERT INTO source_set_members (
                        id, scope_id, source_set_id, source_object_id,
                        source_version, policy_version, source_object_kind,
                        ordinal, added_at, removed_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 1, 'ai_answer', 0, ?, NULL, ?,
                              'active', ?, ?, NULL, 'local', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_version=excluded.source_version, removed_at=NULL,
                        version=excluded.version, lifecycle_state='active',
                        updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (
                        source_member_id,
                        scope_id,
                        source_set_id,
                        answer_id,
                        int(answer.get("version") or 1),
                        created_at,
                        next_version,
                        created_at,
                        now,
                        self.runtime.identity.database_generation_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO derivation_lineage (
                        id, scope_id, source_set_id, policy_version_id,
                        grant_generation, derivative_kind, derivative_object_id,
                        generator_version, generated_at, invalidated_at,
                        source_version, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, NULL, 1, ?, ?, ?, ?, NULL, ?, 'local', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_set_id=excluded.source_set_id,
                        derivative_kind=excluded.derivative_kind,
                        derivative_object_id=excluded.derivative_object_id,
                        generator_version=excluded.generator_version,
                        generated_at=excluded.generated_at, invalidated_at=NULL,
                        source_version=excluded.source_version
                    """,
                    (
                        lineage_id,
                        scope_id,
                        source_set_id,
                        source_kind,
                        document_id,
                        self.MEMORY_GENERATOR_VERSION,
                        now,
                        next_version,
                        self.runtime.identity.database_generation_id,
                    ),
                )
                self._record_memory_operation(
                    connection,
                    operation_id=operation_id,
                    idempotency_key=operation_key,
                    command_type="gc15.answer_memory.save",
                    event_type="answer_memory.saved",
                    action="answer_memory.saved",
                    aggregate_id=document_id,
                    aggregate_version=next_version,
                    payload_hash=payload_hash,
                    result_hash=result_hash,
                    now=now,
                )
                connection.execute(
                    """
                    INSERT INTO lifecycle_events (
                        id, scope_id, operation_id, secured_resource_id,
                        from_state, to_state, tombstone_version, actor_id,
                        reason_code, occurred_at, origin_instance_id, created_at,
                        integrity_hash
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, 'user_saved', ?, ?, ?, ?)
                    """,
                    (
                        self._stable_id("lifecycle", operation_id, "active"),
                        scope_id,
                        operation_id,
                        document_id,
                        "archived" if existing else None,
                        next_version,
                        context.principal_id,
                        now,
                        self.runtime.identity.database_generation_id,
                        now,
                        sha256_text(f"{operation_id}|active|{next_version}"),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return {
            "clientId": project_id,
            "memoryId": document_id,
            "answerId": answer_id,
            "memoryKind": source_kind,
            "version": next_version,
            "status": "active",
            "updatedAt": now,
            "idempotentReplay": False,
        }

    def correct_answer_fact(
        self,
        *,
        project_id: str,
        answer_id: str,
        selected_text: str,
        correction_kind: str,
        statement: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Promote an explicit member statement to current project knowledge."""

        operation_key = str(idempotency_key or "").strip()
        if not operation_key:
            raise LocalRuntimeError(422, "idempotency_required", "纠错提交缺少幂等键")
        normalized_kind = str(correction_kind or "").strip().lower()
        if normalized_kind not in {"correction", "supplement", "remember"}:
            raise LocalRuntimeError(422, "correction_kind_invalid", "请选择记住、纠错或补充")
        normalized_selection = str(selected_text or "").strip()
        normalized_statement = str(statement or "").strip()
        selection_limit = 20_000 if normalized_kind == "remember" else 2_000
        statement_limit = 20_000 if normalized_kind == "remember" else 4_000
        if not normalized_selection or len(normalized_selection) > selection_limit:
            raise LocalRuntimeError(
                422,
                "answer_selection_invalid",
                f"请选择不超过 {selection_limit} 字的回答文本",
            )
        if not normalized_statement or len(normalized_statement) > statement_limit:
            raise LocalRuntimeError(
                422,
                "correction_statement_invalid",
                f"请输入不超过 {statement_limit} 字的项目知识",
            )

        answer = self.answer(answer_id)
        if str(answer.get("projectId") or "") != project_id:
            raise LocalRuntimeError(409, "answer_project_mismatch", "回答不属于当前项目")
        answer_text = str(answer.get("answerMarkdown") or "")
        collapsed_answer = _normalize_answer_selection_text(answer_text)
        collapsed_selection = _normalize_answer_selection_text(normalized_selection)
        compact_answer = re.sub(r"\s+", "", collapsed_answer)
        compact_selection = re.sub(r"\s+", "", collapsed_selection)
        selection_matches = bool(collapsed_selection) and (
            collapsed_selection in collapsed_answer
            or compact_selection in compact_answer
        )
        if normalized_kind != "remember" and not selection_matches:
            raise LocalRuntimeError(
                409,
                "answer_selection_stale",
                "所选文字已不属于当前回答，请重新选择",
            )

        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        selected_text_hash = sha256_text(normalized_selection)
        statement_hash = sha256_text(normalized_statement)
        action_key = "answer-remember" if normalized_kind == "remember" else "answer-correction"
        source_purpose = "answer_remember" if normalized_kind == "remember" else "answer_correction"
        resource_type = (
            "verified_project_memory"
            if normalized_kind == "remember"
            else "verified_member_correction"
        )
        expected_fact_id = "fact_" + sha256_text(
            f"{action_key}\x1f{scope_id}\x1f{project_id}\x1f"
            f"{answer_id}\x1f{selected_text_hash}"
        )[:30]
        with self.runtime._connection() as connection:
            current = connection.execute(
                "SELECT version, fact_hash, updated_at FROM atomic_facts "
                "WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                (expected_fact_id, scope_id),
            ).fetchone()
        if current is not None and str(current["fact_hash"] or "") == statement_hash:
            propagation = self._propagate_project_knowledge_consumers(
                project_id=project_id,
                fact_id=expected_fact_id,
                fact_version=int(current["version"] or 1),
            )
            propagation_ready = propagation["state"] == "completed"
            return {
                "clientId": project_id,
                "answerId": answer_id,
                "factId": expected_fact_id,
                "correctionKind": normalized_kind,
                "version": int(current["version"] or 1),
                "localState": "ready",
                "cloudState": "ready",
                "overallState": "ready" if propagation_ready else "partial_ready",
                "contextInvalidated": True,
                "canReanswer": True,
                "originalQuestion": str(answer.get("question") or ""),
                "updatedAt": current["updated_at"],
                "idempotentReplay": True,
                "retryable": not propagation_ready,
                "consumerPropagation": {
                    "state": propagation["state"],
                    "retryable": propagation["retryable"],
                    "message": propagation["message"],
                    "directConsumers": propagation["directConsumers"],
                    "pendingConsumers": propagation["pendingConsumers"],
                    "local": propagation,
                },
            }
        expected_version = int(current["version"] or 0) if current else 0
        cloud = self.runtime.cloud_command(
            "POST",
            f"/api/v2/workbench/answers/{answer_id}/facts/corrections",
            payload={
                "projectId": project_id,
                "correctionKind": normalized_kind,
                "selectedTextHash": selected_text_hash,
                "statement": normalized_statement,
                "statementHash": statement_hash,
                "expectedVersion": expected_version,
                "originInstanceId": self.runtime.identity.database_generation_id,
            },
            idempotency_key=operation_key,
        )
        fact_id = str(cloud.get("factId") or "")
        source_set_id = str(cloud.get("sourceSetId") or "")
        cloud_version = int(cloud.get("version") or 0)
        if (
            fact_id != expected_fact_id
            or not source_set_id
            or cloud_version != expected_version + 1
            or str(cloud.get("verificationState") or "") != "verified"
            or not bool(cloud.get("contextInvalidated"))
        ):
            raise LocalRuntimeError(
                502,
                "answer_fact_cloud_receipt_invalid",
                "组织云项目知识回执不完整，可以重试",
            )

        now = str(cloud.get("updatedAt") or utc_now())
        local_payload = {
            "schema": "yiyu.local-project-answer-knowledge.v1",
            "clientId": project_id,
            "answerId": answer_id,
            "factId": fact_id,
            "factVersion": cloud_version,
            "correctionKind": normalized_kind,
            "selectedText": normalized_selection,
            "selectedTextHash": selected_text_hash,
            "content": normalized_statement,
            "statementHash": statement_hash,
            "verificationState": resource_type,
            "confirmedByMembershipId": context.membership_id,
            "cloudFactObjectManifestId": cloud.get("factObjectManifestId"),
            "createdAt": now,
        }
        object_manifest_id = self._write_managed_object(
            object_id=f"answer-knowledge:{fact_id}:v{cloud_version}",
            storage_key=(
                f"managed/private/workbench/{context.sandbox_id}/project-knowledge/"
                f"{fact_id}/v{cloud_version}.json"
            ),
            media_type="application/vnd.yiyu.local-project-answer-knowledge+json",
            payload=local_payload,
        )
        source_member_id = "source_member_" + sha256_text(
            f"{source_set_id}\x1f{answer_id}"
        )[:30]
        evidence_id = "evidence_" + sha256_text(
            f"{fact_id}\x1fversion-{cloud_version}"
        )[:30]
        locator = canonical_json(
            {
                "schema": "yiyu.answer-selection-hash.v1",
                "selectedTextHash": selected_text_hash,
                "correctionKind": normalized_kind,
                "factVersion": cloud_version,
            }
        )
        audit_id = self._stable_id(
            "audit",
            "gc12.answer_fact.projected",
            scope_id,
            operation_key,
        )
        operation_id = self._stable_id(
            "op",
            "gc12.answer_fact.projected",
            scope_id,
            operation_key,
        )
        with self.runtime._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                project = connection.execute(
                    "SELECT id FROM clients WHERE id=? AND scope_id=? "
                    "AND sandbox_id=? AND lifecycle_state='active'",
                    (project_id, scope_id, context.sandbox_id),
                ).fetchone()
                answer_row = connection.execute(
                    "SELECT id, version, ai_context_manifest_id FROM ai_answers "
                    "WHERE id=? AND scope_id=? AND client_id=? "
                    "AND lifecycle_state='active'",
                    (answer_id, scope_id, project_id),
                ).fetchone()
                if project is None or answer_row is None:
                    raise LocalRuntimeError(
                        409,
                        "answer_fact_scope_changed",
                        "项目或回答作用域已变化",
                    )
                latest = connection.execute(
                    "SELECT version FROM atomic_facts WHERE id=? AND scope_id=?",
                    (fact_id, scope_id),
                ).fetchone()
                latest_version = int(latest["version"] or 0) if latest else 0
                if latest_version not in {expected_version, cloud_version}:
                    raise LocalRuntimeError(
                        409,
                        "answer_fact_local_version_conflict",
                        "本机事实投影已变化，请刷新后重试",
                    )
                connection.execute(
                    """
                    INSERT INTO source_sets (
                        id, scope_id, client_id, security_label_set_version,
                        source_count, version, purpose_kind, publication_state,
                        created_by_principal_id, created_at, expires_at,
                        lifecycle_state, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, 'organization-v1', 1, ?,
                              ?, 'published', ?, ?, NULL,
                              'active', ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        version=excluded.version, publication_state='published',
                        lifecycle_state='active', updated_at=excluded.updated_at,
                        deleted_at=NULL, authority_role='cloud',
                        origin_instance_id=excluded.origin_instance_id
                    """,
                    (
                        source_set_id,
                        scope_id,
                        project_id,
                        cloud_version,
                        source_purpose,
                        context.principal_id,
                        now,
                        now,
                        context.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO source_set_members (
                        id, scope_id, source_set_id, source_object_id,
                        source_version, policy_version, source_object_kind,
                        ordinal, added_at, removed_at, version, lifecycle_state,
                        created_at, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 1, 'ai_answer', 0, ?, NULL, ?,
                              'active', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_version=excluded.source_version,
                        removed_at=NULL, version=excluded.version,
                        lifecycle_state='active', updated_at=excluded.updated_at,
                        deleted_at=NULL, authority_role='cloud',
                        origin_instance_id=excluded.origin_instance_id
                    """,
                    (
                        source_member_id,
                        scope_id,
                        source_set_id,
                        answer_id,
                        int(answer_row["version"] or 1),
                        now,
                        cloud_version,
                        now,
                        now,
                        context.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO secured_resources (
                        id, scope_id, resource_kind, lifecycle_state, version,
                        resource_type_key, created_at, updated_at, deleted_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, 'atomic_fact', 'active', ?,
                              ?, ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        lifecycle_state='active', version=excluded.version,
                        resource_type_key=excluded.resource_type_key,
                        updated_at=excluded.updated_at, deleted_at=NULL,
                        authority_role='cloud',
                        origin_instance_id=excluded.origin_instance_id
                    """,
                    (
                        fact_id,
                        scope_id,
                        cloud_version,
                        resource_type,
                        now,
                        now,
                        context.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO atomic_facts (
                        id, scope_id, chunk_id, fact_hash, confidence, version,
                        source_set_id, fact_object_manifest_id,
                        verification_state, confirmed_by_membership_id,
                        confirmed_at, lifecycle_state, created_at, updated_at,
                        deleted_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, NULL, ?, NULL, ?, ?, ?, 'verified', ?, ?,
                              'active', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        fact_hash=excluded.fact_hash, version=excluded.version,
                        source_set_id=excluded.source_set_id,
                        fact_object_manifest_id=excluded.fact_object_manifest_id,
                        verification_state='verified',
                        confirmed_by_membership_id=excluded.confirmed_by_membership_id,
                        confirmed_at=excluded.confirmed_at,
                        lifecycle_state='active', updated_at=excluded.updated_at,
                        deleted_at=NULL, authority_role='cloud',
                        origin_instance_id=excluded.origin_instance_id
                    """,
                    (
                        fact_id,
                        scope_id,
                        statement_hash,
                        cloud_version,
                        source_set_id,
                        object_manifest_id,
                        context.membership_id,
                        now,
                        now,
                        now,
                        context.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO evidence_links (
                        id, scope_id, fact_id, source_object_id,
                        source_version, locator, source_object_kind,
                        locator_kind, page_no, paragraph_no, locator_hash,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'ai_answer',
                              'answer_selection_hash', NULL, NULL, ?, ?)
                    """,
                    (
                        evidence_id,
                        scope_id,
                        fact_id,
                        answer_id,
                        int(answer_row["version"] or 1),
                        locator,
                        sha256_text(locator),
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO audit_events (
                        id, scope_id, operation_id, actor_id, action,
                        event_hash, actor_membership_id, target_resource_id,
                        details_object_manifest_id, occurred_at,
                        origin_instance_id, created_at, integrity_hash,
                        authority_role
                    ) VALUES (?, ?, ?, ?, 'workbench.answer_fact.projected', ?, ?,
                              ?, ?, ?, ?, ?, ?, 'local')
                    """,
                    (
                        audit_id,
                        scope_id,
                        None,
                        context.principal_id,
                        sha256_text(
                            f"{fact_id}|{cloud_version}|{statement_hash}"
                        ),
                        context.membership_id,
                        fact_id,
                        object_manifest_id,
                        now,
                        self.runtime.identity.database_generation_id,
                        now,
                        sha256_text(
                            f"{audit_id}|{fact_id}|{cloud_version}|{statement_hash}"
                        ),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        local_propagation = self._propagate_project_knowledge_consumers(
            project_id=project_id,
            fact_id=fact_id,
            fact_version=cloud_version,
        )
        cloud_propagation = cloud.get("consumerPropagation")
        cloud_propagation_state = (
            str(cloud_propagation.get("state") or "failed_retryable")
            if isinstance(cloud_propagation, Mapping)
            else "failed_retryable"
        )
        local_profile_projection: dict[str, Any]
        try:
            rebuilt_profile = self.rebuild_strategic_profile(
                project_id=project_id,
                idempotency_key=f"{idempotency_key}:strategic-profile",
            )
            local_profile_projection = dict(
                rebuilt_profile.get("localProjection") or {}
            )
            local_profile_projection["consumerState"] = "completed"
        except LocalRuntimeError as exc:
            local_profile_projection = {
                "state": "failed_retryable" if exc.status_code >= 500 else "blocked",
                "projected": False,
                "consumerState": "failed_retryable",
                "message": exc.message,
            }
        propagation_ready = (
            local_propagation["state"] == "completed"
            and cloud_propagation_state == "completed"
            and local_profile_projection.get("consumerState") == "completed"
        )
        return {
            "clientId": project_id,
            "answerId": answer_id,
            "factId": fact_id,
            "correctionKind": normalized_kind,
            "version": cloud_version,
            "verificationState": "verified",
            "localState": "ready",
            "cloudState": "ready",
            "overallState": "ready" if propagation_ready else "partial_ready",
            "contextInvalidated": True,
            "canReanswer": True,
            "originalQuestion": str(answer.get("question") or ""),
            "updatedAt": now,
            "idempotentReplay": bool(cloud.get("idempotentReplay")),
            "retryable": not propagation_ready,
            "consumerPropagation": {
                "state": "completed" if propagation_ready else "failed_retryable",
                "retryable": not propagation_ready,
                "message": (
                    "相关页面正在整理"
                    if propagation_ready
                    else "项目知识已保存，部分相关页面更新失败，可以重试"
                ),
                "directConsumers": local_propagation["directConsumers"],
                "pendingConsumers": local_propagation["pendingConsumers"],
                "local": local_propagation,
                "cloud": cloud_propagation,
                "localStrategicProfile": local_profile_projection,
            },
        }

    def _propagate_project_knowledge_consumers(
        self,
        *,
        project_id: str,
        fact_id: str,
        fact_version: int,
    ) -> dict[str, Any]:
        """Invalidate this project's local derived reads after fact commit."""

        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        now = utc_now()
        reconciliation_id = self._stable_id(
            "recon",
            "project_knowledge_consumer_invalidation_v1",
            scope_id,
            project_id,
            fact_id,
            str(fact_version),
        )
        direct_consumers = [
            "project_knowledge_context",
            "workbench_next_answer",
            "task_project_background",
        ]
        pending_consumers = ["strategic_client_profile", "project_reports"]
        try:
            with self.runtime._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                project_context_ids = [
                    str(row["ai_context_manifest_id"])
                    for row in connection.execute(
                        "SELECT DISTINCT ai_context_manifest_id FROM ai_answers "
                        "WHERE scope_id=? AND client_id=? AND lifecycle_state='active' "
                        "AND ai_context_manifest_id IS NOT NULL",
                        (scope_id, project_id),
                    ).fetchall()
                ]
                context_count = 0
                if project_context_ids:
                    placeholders = ",".join("?" for _ in project_context_ids)
                    context_count = int(
                        connection.execute(
                            f"UPDATE ai_context_manifests SET status='invalidated', invalidated_at=? "
                            f"WHERE scope_id=? AND id IN ({placeholders}) AND invalidated_at IS NULL",
                            (now, scope_id, *project_context_ids),
                        ).rowcount
                        or 0
                    )
                narrative_ids = [
                    str(row["id"])
                    for row in connection.execute(
                        "SELECT id FROM narrative_outputs WHERE scope_id=? AND client_id=? "
                        "AND lifecycle_state='active'",
                        (scope_id, project_id),
                    ).fetchall()
                ]
                derivative_ids = project_context_ids + narrative_ids
                lineage_ids: list[str] = []
                if derivative_ids:
                    placeholders = ",".join("?" for _ in derivative_ids)
                    lineage_ids = [
                        str(row["id"])
                        for row in connection.execute(
                            f"SELECT id FROM derivation_lineage WHERE scope_id=? "
                            f"AND derivative_object_id IN ({placeholders}) "
                            "AND invalidated_at IS NULL",
                            (scope_id, *derivative_ids),
                        ).fetchall()
                    ]
                lineage_count = 0
                cache_count = 0
                if lineage_ids:
                    placeholders = ",".join("?" for _ in lineage_ids)
                    lineage_count = int(
                        connection.execute(
                            f"UPDATE derivation_lineage SET invalidated_at=? WHERE scope_id=? "
                            f"AND id IN ({placeholders}) AND invalidated_at IS NULL",
                            (now, scope_id, *lineage_ids),
                        ).rowcount
                        or 0
                    )
                    cache_count = int(
                        connection.execute(
                            f"UPDATE cache_entries SET invalidated_at=? WHERE scope_id=? "
                            f"AND lineage_id IN ({placeholders}) AND invalidated_at IS NULL",
                            (now, scope_id, *lineage_ids),
                        ).rowcount
                        or 0
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
                    ) VALUES (?, ?, NULL, NULL, 0, 'completed',
                              'project_knowledge_consumer_invalidation_v1', ?, NULL,
                              ?, ?, ?, 'active', ?, ?, NULL, 'local', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        mismatch_count=0, status='completed', completed_at=excluded.completed_at,
                        version=excluded.version, lifecycle_state='active',
                        updated_at=excluded.updated_at, deleted_at=NULL
                    """,
                    (
                        reconciliation_id,
                        scope_id,
                        context.cloud_instance_id,
                        now,
                        now,
                        fact_version,
                        now,
                        now,
                        self.runtime.identity.database_generation_id,
                    ),
                )
                connection.execute("COMMIT")
            return {
                "state": "completed",
                "retryable": False,
                "message": "本机相关页面已标记待更新",
                "directConsumers": direct_consumers,
                "pendingConsumers": pending_consumers,
                "invalidatedAiContextCount": context_count,
                "invalidatedLineageCount": lineage_count,
                "invalidatedCacheCount": cache_count,
            }
        except Exception:
            return {
                "state": "failed_retryable",
                "retryable": True,
                "message": "项目知识已保存，本机相关页面更新失败，可以重试",
                "directConsumers": direct_consumers,
                "pendingConsumers": pending_consumers,
                "invalidatedAiContextCount": 0,
                "invalidatedLineageCount": 0,
                "invalidatedCacheCount": 0,
            }

    def rebuild_strategic_profile(
        self,
        *,
        project_id: str,
        idempotency_key: str,
        dimensions: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Build a client profile from this device's Wiki, then publish its safe result."""

        from .project_materials_local import LocalProjectMaterialsRepository

        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        with self.runtime._connection() as connection:
            project = connection.execute(
                "SELECT name, summary FROM clients WHERE id=? AND scope_id=? "
                "AND sandbox_id=? AND lifecycle_state='active'",
                (project_id, scope_id, context.sandbox_id),
            ).fetchone()
        if project is None:
            raise LocalRuntimeError(404, "project_missing", "当前工作空间没有该项目")

        store = LocalProjectMaterialsRepository(self.runtime)
        all_dimension_queries = {
            "essence": "组织 基金会 成立 宗旨 使命 定位 服务对象",
            "business_intro": "项目 计划 课程 服务 活动 业务 模式",
            "cooperation": "合作 伙伴 资助 支持 共创 交付 关系",
            "people": "负责人 秘书长 理事长 创始人 团队 人员",
            "timeline": "成立 启动 时间 阶段 历程 里程碑 年",
            "next_steps": "战略 目标 计划 下一步 挑战 风险 发展",
        }
        requested_dimensions = tuple(
            dict.fromkeys(str(value).strip() for value in (dimensions or []) if str(value).strip())
        )
        invalid_dimensions = [
            value for value in requested_dimensions if value not in all_dimension_queries
        ]
        if invalid_dimensions:
            raise LocalRuntimeError(422, "strategic_profile_dimension_invalid", "客户档案栏目无效")
        partial_refresh = bool(requested_dimensions)
        if not requested_dimensions:
            requested_dimensions = tuple(all_dimension_queries)
        dimension_queries = {
            key: all_dimension_queries[key] for key in requested_dimensions
        }
        current_profile: dict[str, Any] = {}
        if partial_refresh:
            current = self.runtime.cloud_query(
                f"/api/v2/workbench/projects/{project_id}/narrative"
            )
            if not isinstance(current, Mapping):
                raise LocalRuntimeError(502, "strategic_profile_current_invalid", "当前客户档案结构无效")
            current_profile = dict(current)
            current_keys = {
                str(item.get("dimension") or "")
                for item in current_profile.get("dimensions") or []
                if isinstance(item, Mapping)
            }
            if not set(all_dimension_queries).issubset(current_keys):
                raise LocalRuntimeError(409, "strategic_profile_current_incomplete", "当前客户档案尚未完整生成，请先重新整理全部档案")
        evidence_by_dimension: dict[str, list[dict[str, Any]]] = {}
        safe_documents: dict[str, dict[str, Any]] = {}
        corpus = store.strategic_profile_corpus(project_id)
        corpus_evidence: list[dict[str, Any]] = []
        for raw_document in corpus.get("documents") or []:
            if not isinstance(raw_document, Mapping):
                continue
            document_id = str(raw_document.get("sourceObjectId") or "").strip()
            excerpt = str(raw_document.get("excerpt") or "").strip()
            if not document_id or not excerpt:
                continue
            safe_documents[document_id] = {
                key: raw_document.get(key)
                for key in (
                    "sourceObjectId",
                    "sourceObjectKind",
                    "sourceVersion",
                    "contentHash",
                    "knowledgeDocumentId",
                    "documentVersionId",
                    "title",
                )
            }
            corpus_evidence.append(
                {
                    "sourceId": f"doc:{document_id}",
                    "title": str(raw_document.get("title") or "本机资料")[:300],
                    "excerpt": excerpt[:3_000],
                }
            )
        for dimension, query in dimension_queries.items():
            retrieval = store.search_local_wiki(
                project_id=project_id,
                query=query,
                limit=None,
            )
            items: list[dict[str, Any]] = []
            seen_dimension_documents: set[str] = set()
            for hit in retrieval.get("hits") or []:
                if not isinstance(hit, Mapping):
                    continue
                document_id = str(hit.get("documentId") or "").strip()
                excerpt = str(hit.get("excerpt") or "").strip()
                if not document_id or not excerpt:
                    continue
                # 引用按文件去重，每份相关文件保留其最高排序命中。相关性检索
                # 仍负责过滤，但不再用固定的 3/10 条上限隐藏已命中文件。
                if document_id in seen_dimension_documents:
                    continue
                seen_dimension_documents.add(document_id)
                source_id = f"doc:{document_id}"
                items.append(
                    {
                        "sourceId": source_id,
                        "title": str(hit.get("title") or "本机资料")[:300],
                        "excerpt": excerpt[:2_000],
                    }
                )
                if document_id not in safe_documents:
                    local = store.document_text(document_id)
                    safe_documents[document_id] = {
                        "sourceObjectId": document_id,
                        "sourceObjectKind": "source_asset",
                        "sourceVersion": 1,
                        "contentHash": str(local.get("contentHash") or ""),
                        "knowledgeDocumentId": str(hit.get("knowledgeDocumentId") or ""),
                        "documentVersionId": str(hit.get("documentVersionId") or ""),
                        "title": str(hit.get("title") or local.get("title") or "本机资料")[:300],
                    }
            evidence_by_dimension[dimension] = items
        if not safe_documents:
            raise LocalRuntimeError(
                409,
                "strategic_profile_local_evidence_missing",
                "当前项目没有可供客户档案提炼的本机资料，请先完成资料解析",
            )

        presentation = store.knowledge_presentation(project_id)
        cloud_memories: list[Any] = []
        cloud_website_facts: list[Any] = []
        try:
            cloud_context = self.runtime.project_knowledge_context(project_id)
            cloud_memories = list(cloud_context.get("savedMemories") or [])
            cloud_website_facts = list(cloud_context.get("officialWebsiteFacts") or [])
        except Exception:
            # The local Wiki can still rebuild from its last confirmed projection.
            # A disconnected cloud must not make local source files unreadable.
            cloud_memories = []
        formal_facts: list[dict[str, Any]] = []
        seen_fact_ids: set[str] = set()
        for item in list(presentation.get("savedMemories") or []) + cloud_memories:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("authority") or "") not in {"organization_cloud", "cloud"}:
                continue
            if str(item.get("memoryKind") or "") not in {"correction", "explicit_memory"}:
                continue
            statement = str(item.get("summary") or "").strip()
            fact_id = str(item.get("id") or item.get("sourceId") or "").strip()
            if not statement or not fact_id or fact_id in seen_fact_ids:
                continue
            seen_fact_ids.add(fact_id)
            formal_facts.append(
                {
                    "sourceId": f"fact:{fact_id}",
                    "factId": fact_id,
                    "statement": statement[:4_000],
                    "version": max(1, int(item.get("version") or 1)),
                    "contentHash": str(item.get("contentHash") or sha256_text(statement)),
                    "sourceType": "verified_project_fact",
                    "sourceDescription": "成员已核实项目事实",
                    "sourceUrl": "",
                }
            )
        for item in cloud_website_facts:
            if not isinstance(item, Mapping):
                continue
            statement = str(item.get("summary") or "").strip()
            fact_id = str(item.get("sourceId") or "").strip()
            source_url = str(item.get("sourceUrl") or "").strip()
            if not statement or not fact_id or fact_id in seen_fact_ids:
                continue
            seen_fact_ids.add(fact_id)
            formal_facts.append(
                {
                    "sourceId": f"fact:{fact_id}",
                    "factId": fact_id,
                    "statement": statement[:4_000],
                    "version": max(1, int(item.get("version") or 1)),
                    "contentHash": str(item.get("contentHash") or sha256_text(statement)),
                    "sourceType": "official_website",
                    "sourceDescription": str(item.get("sourceDescription") or "官网权威信息")[:300],
                    "sourceUrl": source_url,
                }
            )

        evidence_text = "\n\n".join(
            f"## {dimension}\n"
            + "\n".join(
                f"[{item['sourceId']}] {item['title']}：{item['excerpt']}"
                for item in evidence_by_dimension[dimension]
            )
            for dimension in dimension_queries
        )
        corpus_text = "\n".join(
            f"[{item['sourceId']}] {item['title']}：{item['excerpt']}"
            for item in corpus_evidence
        )
        facts_text = "\n".join(
            f"[{item['sourceId']}] "
            f"{'成员已确认' if item['sourceType'] == 'verified_project_fact' else '官网权威页面'}："
            f"{item['statement']}"
            for item in formal_facts
        ) or "（暂无人工确认或官网权威事实）"
        requested_tags = "".join(
            f"<{dimension}>{dimension}栏目综合文字</{dimension}>"
            for dimension in requested_dimensions
        )
        system_prompt = (
            "你是益语智库战略陪伴 Agent。请根据本机项目资料证据，综合形成客户档案，"
            "而不是复制某一条材料或人工纠错原文。人工确认事实是校正层：若与材料冲突，"
            "必须采用人工确认事实；但仍要把它自然融入相关档案栏目，不能单独暴露为纠错记录。"
            "不得补造人物身份、机构关系、数字或时间。证据不足的栏目返回空字符串。"
            f"只按下面{len(requested_dimensions)}个标签返回，不要JSON、标题、解释或Markdown围栏；"
            f"资料不足就在标签内写“资料不足”：{requested_tags}"
        )
        completion = self.runtime.organization_ai_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"客户名称：{str(project['name'] or '')}\n"
                        f"项目元数据摘要：{str(project['summary'] or '')[:2_000]}\n\n"
                        f"项目正式事实（成员明确事实优先；官网事实用于补充，冲突时不得覆盖成员事实）：\n{facts_text}\n\n"
                        f"本机Wiki逐文件覆盖（已扫描 {len(corpus_evidence)} 份）：\n{corpus_text}\n\n"
                        f"各栏目高相关检索证据：\n{evidence_text}"
                    ),
                },
            ],
            temperature=0.1,
        )
        parsed = self._json_object_from_model(
            str(completion.get("content") or ""),
            expected_dimensions=requested_dimensions,
        )
        raw_dimensions = parsed.get("dimensions")
        if not isinstance(raw_dimensions, Mapping):
            raise LocalRuntimeError(502, "strategic_profile_response_invalid", "战略陪伴模型缺少客户档案栏目")
        document_sources = {f"doc:{key}": value for key, value in safe_documents.items()}
        fact_sources = {str(item["sourceId"]): item for item in formal_facts}
        allowed_sources = set(document_sources) | set(fact_sources)
        generated_dimensions: dict[str, dict[str, Any]] = {}
        for dimension in requested_dimensions:
            raw = raw_dimensions.get(dimension)
            if not isinstance(raw, Mapping):
                raw = {}
            narrative = str(raw.get("narrative") or "").strip()[:8_000]
            selected_ids = [
                str(value)
                for value in raw.get("sourceIds") or []
                if str(value) in allowed_sources
            ]
            if narrative and not selected_ids:
                selected_ids = [
                    str(item["sourceId"])
                    for item in evidence_by_dimension[dimension]
                    if str(item["sourceId"]) in allowed_sources
                ]
            references = []
            for source_id in dict.fromkeys(selected_ids):
                if source_id in document_sources:
                    item = document_sources[source_id]
                    references.append(
                        {
                            "sourceType": "local_document",
                            "sourceId": item["sourceObjectId"],
                            "label": item["title"],
                            "confidence": "high",
                        }
                    )
                elif source_id in fact_sources:
                    fact_source = fact_sources[source_id]
                    references.append(
                        {
                            "sourceType": fact_source["sourceType"],
                            "sourceId": fact_source["factId"],
                            "label": fact_source["sourceDescription"],
                            "confidence": "high",
                            "sourceUrl": fact_source.get("sourceUrl") or "",
                        }
                    )
            generated_dimensions[dimension] = {
                    "dimension": dimension,
                    "narrative": narrative,
                    "confidence": "high" if references else "low",
                    "confidenceReason": (
                        "由本机项目资料提炼，并以人工确认事实校正"
                        if references
                        else "当前资料不足"
                    ),
                    "references": references,
                    "dataLayerGap": "" if narrative else "当前资料不足，尚未形成可靠结论",
                    "openClarifications": [],
                }
        if partial_refresh:
            current_dimensions = {
                str(item.get("dimension") or ""): dict(item)
                for item in current_profile.get("dimensions") or []
                if isinstance(item, Mapping)
            }
            current_dimensions.update(generated_dimensions)
            profile_dimensions = [
                current_dimensions[key] for key in all_dimension_queries
            ]
        else:
            profile_dimensions = [
                generated_dimensions[key] for key in all_dimension_queries
            ]
        safe_source_documents = list(safe_documents.values())
        input_fingerprint = sha256_text(
            canonical_json(
                {
                    "clientId": project_id,
                    "documents": safe_source_documents,
                    "facts": [
                        {key: item[key] for key in ("factId", "version", "contentHash")}
                        for item in formal_facts
                    ],
                }
            )
        )
        profile = {
            "schema": "yiyu.strategic-client-profile.v2",
            "generator": self.STRATEGIC_PROFILE_GENERATOR_VERSION,
            "processingAgentKind": "strategy_companion",
            "modelName": str(dict(completion.get("provider") or {}).get("modelName") or ""),
            "inputFingerprint": input_fingerprint,
            "dimensions": profile_dimensions,
            "overallConfidence": round(
                sum(1 for item in profile_dimensions if item["narrative"])
                / len(profile_dimensions),
                3,
            ),
            "openClarificationsCount": 0,
            "dataLayerGaps": [
                item["dataLayerGap"]
                for item in profile_dimensions
                if item["dataLayerGap"]
            ],
            "contributors": [],
            "sourceDocuments": safe_source_documents,
            "sourceFacts": [
                {
                    "factId": item["factId"],
                    "version": item["version"],
                    "factHash": item["contentHash"],
                }
                for item in formal_facts
            ],
            "coverage": {
                "eligibleDocumentCount": int(corpus.get("eligibleDocumentCount") or 0),
                "scannedDocumentCount": int(corpus.get("scannedDocumentCount") or 0),
                "citedDocumentCount": len(
                    {
                        reference["sourceId"]
                        for item in profile_dimensions
                        for reference in item["references"]
                        if reference["sourceType"] == "local_document"
                    }
                ),
            },
        }
        cloud_profile = self.runtime.cloud_command(
            "POST",
            f"/api/v2/workbench/projects/{project_id}/strategic-profile/rebuild",
            payload={"profile": profile},
            idempotency_key=idempotency_key,
        )
        projection = self.project_strategic_profile(cloud_profile)
        return {**dict(cloud_profile), "localProjection": projection}

    def project_strategic_profile(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the cloud-authoritative strategic profile as a local projection."""

        project_id = str(profile.get("clientId") or "").strip()
        profile_id = str(profile.get("id") or "").strip()
        source_set_id = str(profile.get("sourceSetId") or "").strip()
        generator = str(profile.get("generator") or "").strip()
        content_version = int(profile.get("rev") or 0)
        if (
            not project_id
            or not profile_id
            or not source_set_id
            or content_version < 1
            or generator
            not in {
                "strategy_companion_verified_fact_router_v1",
                self.STRATEGIC_PROFILE_GENERATOR_VERSION,
            }
        ):
            return {"state": "not_connected", "projected": False}
        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        payload = {
            key: profile.get(key)
            for key in (
                "id",
                "clientId",
                "clientName",
                "rev",
                "generator",
                "generatedAt",
                "modelName",
                "dimensions",
                "overallConfidence",
                "openClarificationsCount",
                "dataLayerGaps",
                "contributors",
                "updatedAt",
                "aggregateVersion",
                "lifecycleState",
                "sourceSetId",
                "sourceDocuments",
                "sourceFacts",
                "coverage",
            )
        }
        manifest_id = self._write_managed_object(
            object_id=f"strategic-profile:{profile_id}:v{content_version}",
            storage_key=(
                f"managed/private/workbench/{context.sandbox_id}/strategic-profile/"
                f"{project_id}/{profile_id}/v{content_version}.json"
            ),
            media_type="application/vnd.yiyu.local-strategic-client-profile+json",
            payload=payload,
        )
        now = str(profile.get("updatedAt") or utc_now())
        aggregate_version = max(1, int(profile.get("aggregateVersion") or content_version))
        source_facts = [
            dict(item)
            for item in profile.get("sourceFacts") or []
            if isinstance(item, Mapping) and str(item.get("factId") or "").strip()
        ]
        source_documents = [
            dict(item)
            for item in profile.get("sourceDocuments") or []
            if isinstance(item, Mapping)
            and str(item.get("sourceObjectId") or "").strip()
        ]
        content_hash = sha256_text(canonical_json(payload))
        with self.runtime._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                if connection.execute(
                    "SELECT 1 FROM clients WHERE id=? AND scope_id=? AND sandbox_id=? "
                    "AND lifecycle_state='active'",
                    (project_id, scope_id, context.sandbox_id),
                ).fetchone() is None:
                    raise LocalRuntimeError(409, "strategic_profile_scope_changed", "客户档案项目作用域已变化")
                connection.execute(
                    """
                    INSERT INTO source_sets (
                        id, scope_id, client_id, security_label_set_version,
                        source_count, version, purpose_kind, publication_state,
                        created_by_principal_id, created_at, expires_at,
                        lifecycle_state, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, 'organization-v1', ?, ?,
                              'strategic_profile_generation', 'published', NULL, ?, NULL,
                              'active', ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_count=excluded.source_count, version=excluded.version,
                        lifecycle_state='active', updated_at=excluded.updated_at,
                        deleted_at=NULL, authority_role='cloud'
                    """,
                    (
                        source_set_id,
                        scope_id,
                        project_id,
                        len(source_facts) + len(source_documents),
                        content_version,
                        now,
                        now,
                        context.cloud_instance_id,
                    ),
                )
                for ordinal, item in enumerate(source_facts):
                    fact_id = str(item.get("factId") or "")
                    fact_version = max(1, int(item.get("version") or 1))
                    member_id = self._stable_id("source_member", source_set_id, fact_id)
                    connection.execute(
                        """
                        INSERT INTO source_set_members (
                            id, scope_id, source_set_id, source_object_id,
                            source_version, policy_version, source_object_kind,
                            ordinal, added_at, removed_at, version, lifecycle_state,
                            created_at, updated_at, deleted_at, authority_role,
                            origin_instance_id
                        ) VALUES (?, ?, ?, ?, ?, 1, 'atomic_fact', ?, ?, NULL, ?,
                                  'active', ?, ?, NULL, 'cloud', ?)
                        ON CONFLICT(id) DO UPDATE SET
                            source_version=excluded.source_version,
                            ordinal=excluded.ordinal, removed_at=NULL,
                            version=excluded.version, lifecycle_state='active',
                            updated_at=excluded.updated_at, deleted_at=NULL
                        """,
                        (
                            member_id,
                            scope_id,
                            source_set_id,
                            fact_id,
                            fact_version,
                            ordinal,
                            now,
                            content_version,
                            now,
                            now,
                            context.cloud_instance_id,
                        ),
                    )
                for ordinal, item in enumerate(source_documents, start=len(source_facts)):
                    source_object_id = str(item.get("sourceObjectId") or "")
                    source_version = max(1, int(item.get("sourceVersion") or 1))
                    member_id = self._stable_id(
                        "source_member", source_set_id, source_object_id
                    )
                    connection.execute(
                        """
                        INSERT INTO source_set_members (
                            id, scope_id, source_set_id, source_object_id,
                            source_version, policy_version, source_object_kind,
                            ordinal, added_at, removed_at, version, lifecycle_state,
                            created_at, updated_at, deleted_at, authority_role,
                            origin_instance_id
                        ) VALUES (?, ?, ?, ?, ?, 1, 'source_asset', ?, ?, NULL, ?,
                                  'active', ?, ?, NULL, 'cloud', ?)
                        ON CONFLICT(id) DO UPDATE SET
                            source_version=excluded.source_version,
                            ordinal=excluded.ordinal, removed_at=NULL,
                            version=excluded.version, lifecycle_state='active',
                            updated_at=excluded.updated_at, deleted_at=NULL
                        """,
                        (
                            member_id,
                            scope_id,
                            source_set_id,
                            source_object_id,
                            source_version,
                            ordinal,
                            now,
                            content_version,
                            now,
                            now,
                            context.cloud_instance_id,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO secured_resources (
                        id, scope_id, resource_kind, lifecycle_state, version,
                        resource_type_key, created_at, updated_at, deleted_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, 'narrative_output', 'active', ?,
                              'strategic_client_profile', ?, ?, NULL, 'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        lifecycle_state='active', version=excluded.version,
                        updated_at=excluded.updated_at, deleted_at=NULL,
                        authority_role='cloud'
                    """,
                    (
                        profile_id,
                        scope_id,
                        aggregate_version,
                        now,
                        now,
                        context.cloud_instance_id,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO narrative_outputs (
                        id, scope_id, client_id, source_set_id, current_version,
                        lifecycle_state, title, artifact_kind, visibility_scope,
                        publication_state, owner_membership_id, published_at,
                        version, created_at, updated_at, deleted_at, authority_role,
                        origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, 'active', ?, 'strategic_profile',
                              'organization', 'published', ?, ?, ?, ?, ?, NULL,
                              'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_set_id=excluded.source_set_id,
                        current_version=excluded.current_version,
                        lifecycle_state='active', title=excluded.title,
                        publication_state='published', published_at=excluded.published_at,
                        version=excluded.version, updated_at=excluded.updated_at,
                        deleted_at=NULL, authority_role='cloud'
                    """,
                    (
                        profile_id,
                        scope_id,
                        project_id,
                        source_set_id,
                        content_version,
                        f"{str(profile.get('clientName') or '项目')}客户档案",
                        context.membership_id,
                        str(profile.get("generatedAt") or now),
                        aggregate_version,
                        now,
                        now,
                        context.cloud_instance_id,
                    ),
                )
                artifact_version_id = self._stable_id(
                    "artifact_version", profile_id, str(content_version)
                )
                connection.execute(
                    """
                    INSERT INTO artifact_versions (
                        id, scope_id, artifact_id, version, content_hash,
                        object_manifest_id, source_set_id, publication_state,
                        created_by_membership_id, created_at, origin_instance_id,
                        integrity_hash, authority_role
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'published', ?, ?, ?, ?, 'cloud')
                    ON CONFLICT(id) DO UPDATE SET
                        content_hash=excluded.content_hash,
                        object_manifest_id=excluded.object_manifest_id,
                        source_set_id=excluded.source_set_id,
                        publication_state='published'
                    """,
                    (
                        artifact_version_id,
                        scope_id,
                        profile_id,
                        content_version,
                        content_hash,
                        manifest_id,
                        source_set_id,
                        context.membership_id,
                        now,
                        context.cloud_instance_id,
                        sha256_text(
                            f"{profile_id}|{content_version}|{content_hash}|{source_set_id}"
                        ),
                    ),
                )
                lineage_id = self._stable_id(
                    "lineage", profile_id, str(content_version)
                )
                connection.execute(
                    """
                    INSERT INTO derivation_lineage (
                        id, scope_id, source_set_id, policy_version_id,
                        grant_generation, derivative_kind, derivative_object_id,
                        generator_version, generated_at, invalidated_at,
                        source_version, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, NULL, 1, 'narrative_output', ?,
                              'strategy_companion_verified_fact_router_v1', ?, NULL, ?,
                              'cloud', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_set_id=excluded.source_set_id,
                        generated_at=excluded.generated_at, invalidated_at=NULL,
                        source_version=excluded.source_version
                    """,
                    (
                        lineage_id,
                        scope_id,
                        source_set_id,
                        profile_id,
                        now,
                        content_version,
                        context.cloud_instance_id,
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return {
            "state": "ready",
            "projected": True,
            "profileId": profile_id,
            "version": content_version,
        }

    def remember_answer_fact(
        self,
        *,
        project_id: str,
        answer_id: str,
        statement: str | None = None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        answer = self.answer(answer_id)
        remembered_statement = str(statement or answer.get("answerMarkdown") or "").strip()
        if not remembered_statement:
            raise LocalRuntimeError(409, "answer_content_missing", "当前回答没有可记住的内容")
        return self.correct_answer_fact(
            project_id=project_id,
            answer_id=answer_id,
            selected_text=remembered_statement,
            correction_kind="remember",
            statement=remembered_statement,
            idempotency_key=idempotency_key,
        )

    def revoke_answer_memory(
        self,
        *,
        project_id: str,
        answer_id: str,
        memory_kind: str,
        expected_version: int | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        _, _, source_kind = self._memory_definition(memory_kind)
        operation_key = str(idempotency_key or "").strip()
        if not operation_key:
            raise LocalRuntimeError(422, "idempotency_required", "撤回记忆缺少幂等键")
        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        document_id = self._stable_id(
            "memory", context.sandbox_id, project_id, answer_id, source_kind
        )
        payload_hash = sha256_text(
            canonical_json(
                {
                    "projectId": project_id,
                    "answerId": answer_id,
                    "memoryKind": source_kind,
                    "expectedVersion": expected_version,
                }
            )
        )
        operation_id = self._stable_id(
            "op", "gc15.memory.revoke", scope_id, operation_key
        )
        now = utc_now()
        with self.runtime._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                replay = connection.execute(
                    "SELECT payload_hash FROM idempotency_records WHERE scope_id=? AND idempotency_key=?",
                    (scope_id, operation_key),
                ).fetchone()
                row = connection.execute(
                    """
                    SELECT version, lifecycle_state FROM knowledge_documents
                    WHERE id=? AND scope_id=? AND client_id=? AND sandbox_id=?
                    """,
                    (document_id, scope_id, project_id, context.sandbox_id),
                ).fetchone()
                if replay is not None:
                    if str(replay["payload_hash"] or "") != payload_hash:
                        raise LocalRuntimeError(409, "memory_idempotency_conflict", "该幂等键已用于另一项记忆操作")
                    connection.execute("COMMIT")
                    return {
                        "ok": True,
                        "clientId": project_id,
                        "memoryId": document_id,
                        "answerId": answer_id,
                        "memoryKind": source_kind,
                        "status": "archived",
                        "version": int((row or {"version": expected_version or 1})["version"] or 1),
                        "idempotentReplay": True,
                    }
                if row is None or str(row["lifecycle_state"] or "") != "active":
                    raise LocalRuntimeError(404, "memory_missing", "该回答没有可撤回的记忆")
                current_version = int(row["version"] or 1)
                if expected_version is not None and int(expected_version) != current_version:
                    raise LocalRuntimeError(409, "memory_version_conflict", "记忆已更新，请刷新后重试")
                next_version = current_version + 1
                result_hash = sha256_text(
                    canonical_json(
                        {
                            "memoryId": document_id,
                            "memoryKind": source_kind,
                            "status": "archived",
                            "version": next_version,
                        }
                    )
                )
                self._record_memory_operation(
                    connection,
                    operation_id=operation_id,
                    idempotency_key=operation_key,
                    command_type="gc15.answer_memory.revoke",
                    event_type="answer_memory.revoked",
                    action="answer_memory.revoked",
                    aggregate_id=document_id,
                    aggregate_version=next_version,
                    payload_hash=payload_hash,
                    result_hash=result_hash,
                    now=now,
                )
                dependent_lineages = [
                    str(item["lineage_id"])
                    for item in connection.execute(
                        """
                        SELECT DISTINCT ac.lineage_id
                        FROM ai_context_manifests AS ac
                        JOIN source_set_members AS sm
                          ON sm.scope_id=ac.scope_id
                         AND sm.source_set_id=ac.source_set_id
                        WHERE ac.scope_id=? AND sm.source_object_id=?
                          AND sm.lifecycle_state='active'
                        """,
                        (scope_id, document_id),
                    ).fetchall()
                    if item["lineage_id"]
                ]
                own_lineage = self._stable_id("lineage", document_id)
                all_lineages = list(dict.fromkeys([own_lineage, *dependent_lineages]))
                placeholders = ",".join("?" for _ in all_lineages)
                connection.execute(
                    "UPDATE knowledge_documents SET publication_state='revoked', lifecycle_state='archived', version=?, updated_at=? WHERE id=?",
                    (next_version, now, document_id),
                )
                connection.execute(
                    "UPDATE document_versions SET publication_state='revoked' WHERE document_id=? AND scope_id=?",
                    (document_id, scope_id),
                )
                connection.execute(
                    "UPDATE secured_resources SET lifecycle_state='archived', version=?, updated_at=? WHERE id=?",
                    (next_version, now, document_id),
                )
                connection.execute(
                    "UPDATE source_sets SET publication_state='revoked', lifecycle_state='archived', version=?, updated_at=? WHERE id=? AND scope_id=?",
                    (next_version, now, self._stable_id("source_set", document_id), scope_id),
                )
                connection.execute(
                    "UPDATE source_set_members SET lifecycle_state='archived', removed_at=?, version=?, updated_at=? WHERE source_set_id=? AND scope_id=?",
                    (now, next_version, now, self._stable_id("source_set", document_id), scope_id),
                )
                connection.execute(
                    f"UPDATE derivation_lineage SET invalidated_at=? WHERE scope_id=? AND id IN ({placeholders})",
                    (now, scope_id, *all_lineages),
                )
                for table in (
                    "search_index_manifests",
                    "vector_index_manifests",
                    "cache_entries",
                ):
                    connection.execute(
                        f"UPDATE {table} SET invalidated_at=? WHERE scope_id=? AND lineage_id IN ({placeholders})",
                        (now, scope_id, *all_lineages),
                    )
                connection.execute(
                    """
                    UPDATE ai_context_manifests
                    SET status='revoked', invalidated_at=?
                    WHERE scope_id=? AND source_set_id IN (
                        SELECT source_set_id FROM source_set_members
                        WHERE scope_id=? AND source_object_id=?
                    )
                    """,
                    (now, scope_id, scope_id, document_id),
                )
                connection.execute(
                    """
                    INSERT INTO lifecycle_events (
                        id, scope_id, operation_id, secured_resource_id,
                        from_state, to_state, tombstone_version, actor_id,
                        reason_code, occurred_at, origin_instance_id, created_at,
                        integrity_hash
                    ) VALUES (?, ?, ?, ?, 'active', 'archived', ?, ?,
                              'user_revoked', ?, ?, ?, ?)
                    """,
                    (
                        self._stable_id("lifecycle", operation_id, "archived"),
                        scope_id,
                        operation_id,
                        document_id,
                        next_version,
                        context.principal_id,
                        now,
                        self.runtime.identity.database_generation_id,
                        now,
                        sha256_text(f"{operation_id}|archived|{next_version}"),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return {
            "ok": True,
            "clientId": project_id,
            "memoryId": document_id,
            "answerId": answer_id,
            "memoryKind": source_kind,
            "status": "archived",
            "version": next_version,
            "updatedAt": now,
            "idempotentReplay": False,
        }

    @staticmethod
    def _controlled_memory_kind(document_kind: str) -> str | None:
        normalized = str(document_kind or "").strip().lower()
        if normalized in {"favorite_memory", "answer_favorite"}:
            return "favorite"
        if normalized in {
            "correction_memory",
            "answer_correction",
            "user_correction",
            "user_supplement",
        }:
            return "correction"
        return None

    def _memory_sync_identity(self, project_id: str) -> tuple[str, str, str]:
        context = self._context()
        object_id = self._stable_id(
            "memory_sync",
            context.sandbox_id,
            context.principal_id,
            project_id,
        )
        reconciliation_id = self._stable_id(
            "recon",
            "member_memory_safe_summary",
            context.sandbox_id,
            context.principal_id,
            project_id,
        )
        storage_key = (
            f"managed/private/workbench/{context.sandbox_id}/memory-sync/"
            f"{project_id}.json"
        )
        return object_id, reconciliation_id, storage_key

    def _controlled_memory_entries(
        self,
        project_id: str,
    ) -> list[dict[str, Any]]:
        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        with self.runtime._connection() as connection:
            project = connection.execute(
                """
                SELECT id FROM clients
                WHERE id=? AND scope_id=? AND sandbox_id=?
                  AND lifecycle_state='active'
                """,
                (project_id, scope_id, context.sandbox_id),
            ).fetchone()
            if project is None:
                raise LocalRuntimeError(
                    404,
                    "memory_sync_project_missing",
                    "当前项目不存在或已切换",
                )
            rows = connection.execute(
                """
                SELECT d.id, d.document_kind, d.version, d.updated_at,
                       v.content_hash
                FROM knowledge_documents AS d
                JOIN document_versions AS v
                  ON v.scope_id=d.scope_id AND v.document_id=d.id
                 AND v.version=d.current_version
                WHERE d.scope_id=? AND d.client_id=? AND d.sandbox_id=?
                  AND d.owner_membership_id=?
                  AND d.lifecycle_state='active'
                ORDER BY d.id
                """,
                (
                    scope_id,
                    project_id,
                    context.sandbox_id,
                    context.membership_id,
                ),
            ).fetchall()
            correction_rows = connection.execute(
                """
                SELECT fact.id, fact.version, fact.fact_hash, fact.updated_at,
                       sources.purpose_kind
                FROM atomic_facts AS fact
                JOIN source_sets AS sources
                  ON sources.scope_id=fact.scope_id
                 AND sources.id=fact.source_set_id
                 AND sources.client_id=?
                 AND sources.purpose_kind IN ('answer_correction', 'answer_remember')
                 AND sources.lifecycle_state='active'
                WHERE fact.scope_id=? AND fact.lifecycle_state='active'
                  AND fact.verification_state='verified'
                  AND fact.confirmed_by_membership_id=?
                ORDER BY fact.id
                """,
                (project_id, scope_id, context.membership_id),
            ).fetchall()
        entries: list[dict[str, Any]] = []
        for row in rows:
            memory_kind = self._controlled_memory_kind(row["document_kind"])
            if memory_kind is None:
                continue
            entries.append(
                {
                    "memoryId": str(row["id"]),
                    "memoryKind": memory_kind,
                    "version": max(1, int(row["version"] or 1)),
                    "contentHash": str(row["content_hash"] or ""),
                    "updatedAt": str(row["updated_at"] or ""),
                }
            )
        entries.extend(
            {
                "memoryId": str(row["id"]),
                "memoryKind": (
                    "explicit_memory"
                    if str(row["purpose_kind"] or "") == "answer_remember"
                    else "correction"
                ),
                "version": max(1, int(row["version"] or 1)),
                "contentHash": str(row["fact_hash"] or ""),
                "updatedAt": str(row["updated_at"] or ""),
            }
            for row in correction_rows
        )
        return entries

    @staticmethod
    def _memory_counts(entries: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        counts = {"explicitMemory": 0, "favorite": 0, "correction": 0}
        for item in entries:
            key = {
                "explicit_memory": "explicitMemory",
                "favorite": "favorite",
                "correction": "correction",
            }.get(str(item.get("memoryKind") or ""))
            if key:
                counts[key] += 1
        return counts

    def memory_sync_status(self, *, project_id: str) -> dict[str, Any]:
        self.runtime.require_project_capability(project_id, "read")
        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        entries = self._controlled_memory_entries(project_id)
        memory_digest = sha256_text(canonical_json(entries))
        object_id, reconciliation_id, _ = self._memory_sync_identity(project_id)
        snapshot: dict[str, Any] | None = None
        manifest = self.runtime.local_storage_object_get(
            sandbox_id=context.sandbox_id,
            object_id=object_id,
        )
        if manifest is not None and str(manifest.get("lifecycle_state") or "") == "active":
            try:
                snapshot = self._read_managed_payload(object_id)
            except LocalRuntimeError:
                snapshot = None
        with self.runtime._connection() as connection:
            reconciliation = connection.execute(
                """
                SELECT status, mismatch_count, version, updated_at
                FROM reconciliation_runs
                WHERE id=? AND scope_id=? AND lifecycle_state='active'
                """,
                (reconciliation_id, scope_id),
            ).fetchone()
        snapshot_digest = str((snapshot or {}).get("memoryDigest") or "")
        if snapshot is None:
            local_state = "not_connected"
        elif snapshot_digest != memory_digest:
            local_state = "stale"
        else:
            local_state = "ready"
        counts = self._memory_counts(entries)
        last_synced_at = (
            str((snapshot or {}).get("generatedAt") or "")
            or (str(reconciliation["updated_at"] or "") if reconciliation else "")
            or None
        )
        cloud_state = "not_connected"
        cloud_entries: list[dict[str, Any]] = []
        cloud_version = 0
        cloud_updated_at: str | None = None
        cloud_error: str | None = None
        try:
            cloud = self.runtime.cloud_query(
                f"/api/v2/workbench/projects/{quote(project_id, safe='')}/memory-manifest"
            )
            if not isinstance(cloud, Mapping) or str(cloud.get("clientId") or "") != project_id:
                raise LocalRuntimeError(
                    502,
                    "memory_manifest_response_invalid",
                    "组织云返回的记忆安全摘要结构无效",
                )
            cloud_state = str(cloud.get("cloudState") or "not_connected")
            cloud_version = max(0, int(cloud.get("manifestVersion") or 0))
            cloud_updated_at = str(cloud.get("updatedAt") or "") or None
            raw_cloud_entries = cloud.get("entries") or []
            if not isinstance(raw_cloud_entries, list):
                raise LocalRuntimeError(
                    502,
                    "memory_manifest_response_invalid",
                    "组织云返回的记忆安全摘要结构无效",
                )
            allowed = {"memoryId", "memoryKind", "version", "contentHash", "updatedAt"}
            for raw in raw_cloud_entries:
                if not isinstance(raw, Mapping) or set(raw) != allowed:
                    raise LocalRuntimeError(
                        502,
                        "memory_manifest_boundary_violation",
                        "组织云记忆摘要包含越界字段",
                    )
                cloud_entries.append({key: raw[key] for key in sorted(allowed)})
            cloud_entries.sort(key=lambda item: str(item.get("memoryId") or ""))
        except LocalRuntimeError as exc:
            if exc.status_code < 500 and exc.code not in {
                "cloud_unreachable",
                "cloud_timeout",
                "organization_not_connected",
            }:
                raise
            cloud_state = "failed_retryable"
            cloud_error = exc.message
        cloud_digest = sha256_text(canonical_json(cloud_entries))
        if cloud_state == "ready":
            conflict_state = "none" if cloud_digest == memory_digest else "different"
            overall_state = "ready" if conflict_state == "none" else "partial_ready"
        elif cloud_state == "failed_retryable":
            conflict_state = "not_checked"
            overall_state = "failed_retryable"
        else:
            conflict_state = "not_checked"
            overall_state = "not_connected"
        return {
            "clientId": project_id,
            "organizationId": context.organization_id,
            "cloudInstanceId": context.cloud_instance_id,
            "localState": local_state,
            "cloudState": cloud_state,
            "overallState": overall_state,
            "conflictState": conflict_state,
            "retryable": overall_state != "ready",
            "lastSyncedAt": cloud_updated_at or last_synced_at,
            "localSummary": {
                "memoryCount": len(entries),
                "counts": counts,
                "memoryDigest": memory_digest,
                "snapshotVersion": int(reconciliation["version"] or 0)
                if reconciliation
                else 0,
            },
            "cloudSummary": {
                "memoryCount": len(cloud_entries),
                "counts": self._memory_counts(cloud_entries),
                "memoryDigest": cloud_digest,
                "manifestVersion": cloud_version,
                "state": cloud_state,
            },
            "message": (
                "本机与组织云安全摘要一致"
                if overall_state == "ready"
                else "本机与组织云安全摘要不同，可同步当前设备清单"
                if cloud_state == "ready"
                else cloud_error
                if cloud_state == "failed_retryable"
                else "组织云尚无本账号在该项目的安全摘要，可一键同步"
            ),
            "boundary": {
                "l0ConversationIncluded": False,
                "answerBodyIncluded": False,
                "fileBodyIncluded": False,
                "localPathIncluded": False,
                "secretIncluded": False,
                "sourceHashesIncluded": True,
            },
            "generatorVersion": self.MEMORY_SYNC_GENERATOR_VERSION,
        }

    def prepare_memory_sync(
        self,
        *,
        project_id: str,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        operation_key = str(idempotency_key or "").strip()
        if not operation_key:
            raise LocalRuntimeError(
                422,
                "idempotency_required",
                "记忆同步缺少幂等键",
            )
        self.runtime.require_project_capability(project_id, "read")
        context = self._context()
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        entries = self._controlled_memory_entries(project_id)
        memory_digest = sha256_text(canonical_json(entries))
        payload_hash = sha256_text(
            canonical_json(
                {
                    "projectId": project_id,
                    "memoryDigest": memory_digest,
                    "generatorVersion": self.MEMORY_SYNC_GENERATOR_VERSION,
                }
            )
        )
        with self.runtime._connection() as connection:
            replay = connection.execute(
                "SELECT payload_hash FROM idempotency_records WHERE scope_id=? AND idempotency_key=?",
                (scope_id, operation_key),
            ).fetchone()
        if replay is not None:
            if str(replay["payload_hash"] or "") != payload_hash:
                raise LocalRuntimeError(
                    409,
                    "memory_sync_idempotency_conflict",
                    "该幂等键已用于另一份记忆同步清单",
                )
            return {**self.memory_sync_status(project_id=project_id), "idempotentReplay": True}

        cloud_before = self.runtime.cloud_query(
            f"/api/v2/workbench/projects/{quote(project_id, safe='')}/memory-manifest"
        )
        if not isinstance(cloud_before, Mapping) or str(cloud_before.get("clientId") or "") != project_id:
            raise LocalRuntimeError(
                502,
                "memory_manifest_response_invalid",
                "组织云返回的记忆安全摘要结构无效",
            )
        cloud_expected_version = max(0, int(cloud_before.get("manifestVersion") or 0))
        now = utc_now()
        counts = self._memory_counts(entries)
        safe_payload = {
            "schema": "yiyu.member-memory-safe-summary.v1",
            "clientId": project_id,
            "organizationId": context.organization_id,
            "principalId": context.principal_id,
            "membershipId": context.membership_id,
            "memoryDigest": memory_digest,
            "memoryCount": len(entries),
            "counts": counts,
            "entries": entries,
            "generatorVersion": self.MEMORY_SYNC_GENERATOR_VERSION,
            "generatedAt": now,
            "boundary": {
                "l0ConversationIncluded": False,
                "answerBodyIncluded": False,
                "fileBodyIncluded": False,
                "localPathIncluded": False,
                "secretIncluded": False,
            },
        }
        object_id, reconciliation_id, storage_key = self._memory_sync_identity(project_id)
        manifest_id = self._write_managed_object(
            object_id=object_id,
            storage_key=storage_key,
            media_type=self.MEMORY_SYNC_MEDIA_TYPE,
            payload=safe_payload,
        )
        operation_id = self._stable_id(
            "op",
            "gc15.memory_sync.prepare",
            scope_id,
            operation_key,
        )
        result_hash = sha256_text(
            canonical_json(
                {
                    "reconciliationId": reconciliation_id,
                    "manifestId": manifest_id,
                    "memoryDigest": memory_digest,
                    "cloudState": "not_connected",
                }
            )
        )
        expires_at = (
            context.refresh_expires_at
            or (datetime.now(timezone.utc) + timedelta(days=365))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        with self.runtime._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                previous = connection.execute(
                    "SELECT version FROM reconciliation_runs WHERE id=? AND scope_id=?",
                    (reconciliation_id, scope_id),
                ).fetchone()
                version = int(previous["version"] or 0) + 1 if previous else 1
                connection.execute(
                    """
                    INSERT INTO idempotency_records (
                        id, scope_id, idempotency_key, payload_hash, result_hash,
                        expires_at, result_object_manifest_id, status, created_at,
                        authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'settled', ?, 'local', ?)
                    """,
                    (
                        self._stable_id("idem", operation_id, "memory_sync"),
                        scope_id,
                        operation_key,
                        payload_hash,
                        result_hash,
                        expires_at,
                        manifest_id,
                        now,
                        self.runtime.identity.database_generation_id,
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
                    ) VALUES (?, ?, ?, ?, 'reconciliation_run', ?,
                              'gc15.member_memory.safe_summary.prepare', ?, ?,
                              NULL, 'settled', ?, NULL, ?, ?, ?, 'local', ?)
                    """,
                    (
                        self._stable_id("cmd", operation_id, "memory_sync"),
                        scope_id,
                        operation_id,
                        operation_key,
                        reconciliation_id,
                        context.principal_id,
                        max(0, version - 1),
                        context.membership_id,
                        payload_hash,
                        now,
                        now,
                        self.runtime.identity.database_generation_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE outbox_events
                    SET status='superseded'
                    WHERE scope_id=? AND aggregate_type='reconciliation_run'
                      AND aggregate_id=? AND status='pending'
                    """,
                    (scope_id, reconciliation_id),
                )
                connection.execute(
                    """
                    INSERT INTO outbox_events (
                        id, scope_id, operation_id, aggregate_version,
                        event_type, status, aggregate_type, aggregate_id,
                        event_object_manifest_id, event_hash, available_at,
                        published_at, authority_role, origin_instance_id
                    ) VALUES (?, ?, ?, ?, 'member_memory.safe_summary.prepared',
                              'pending', 'reconciliation_run', ?, ?, ?, ?, NULL,
                              'local', ?)
                    """,
                    (
                        self._stable_id("evt", operation_id, "memory_sync"),
                        scope_id,
                        operation_id,
                        version,
                        reconciliation_id,
                        manifest_id,
                        result_hash,
                        now,
                        self.runtime.identity.database_generation_id,
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
                    ) VALUES (?, ?, ?, NULL, ?, 'not_connected',
                              'member_memory_safe_summary_single_device_v1', ?, ?,
                              ?, NULL, ?, 'active', ?, ?, NULL, 'local', ?)
                    ON CONFLICT(id) DO UPDATE SET
                        operation_id=excluded.operation_id,
                        mismatch_count=excluded.mismatch_count,
                        status='not_connected',
                        target_instance_id=excluded.target_instance_id,
                        result_object_manifest_id=excluded.result_object_manifest_id,
                        started_at=excluded.started_at, completed_at=NULL,
                        version=excluded.version, lifecycle_state='active',
                        updated_at=excluded.updated_at, deleted_at=NULL,
                        authority_role='local',
                        origin_instance_id=excluded.origin_instance_id
                    """,
                    (
                        reconciliation_id,
                        scope_id,
                        operation_id,
                        len(entries),
                        context.cloud_instance_id,
                        manifest_id,
                        now,
                        version,
                        now,
                        now,
                        self.runtime.identity.database_generation_id,
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
                    ) VALUES (?, ?, ?, ?, 'member_memory.safe_summary.prepared',
                              ?, ?, ?, ?, ?, ?, ?, ?, 'local')
                    """,
                    (
                        self._stable_id("audit", operation_id, "memory_sync"),
                        scope_id,
                        operation_id,
                        context.principal_id,
                        result_hash,
                        context.membership_id,
                        project_id,
                        manifest_id,
                        now,
                        self.runtime.identity.database_generation_id,
                        now,
                        sha256_text(f"{operation_id}|{result_hash}|{now}"),
                    ),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        try:
            cloud_result = self.runtime.cloud_command(
                "PUT",
                f"/api/v2/workbench/projects/{quote(project_id, safe='')}/memory-manifest",
                payload={
                    "entries": entries,
                    "expectedVersion": cloud_expected_version,
                },
                idempotency_key=f"{operation_key}:cloud",
            )
            if (
                str(cloud_result.get("clientId") or "") != project_id
                or str(cloud_result.get("cloudState") or "") != "ready"
            ):
                raise LocalRuntimeError(
                    502,
                    "memory_manifest_response_invalid",
                    "组织云未确认记忆安全摘要",
                )
            cloud_result_hash = sha256_text(canonical_json(dict(cloud_result)))
            completed_at = utc_now()
            with self.runtime._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE idempotency_records SET status='completed',result_hash=? "
                    "WHERE scope_id=? AND idempotency_key=?",
                    (cloud_result_hash, scope_id, operation_key),
                )
                connection.execute(
                    "UPDATE commands SET status='completed',settled_at=? "
                    "WHERE scope_id=? AND operation_id=?",
                    (completed_at, scope_id, operation_id),
                )
                connection.execute(
                    "UPDATE outbox_events SET status='published',published_at=? "
                    "WHERE scope_id=? AND operation_id=? AND status='pending'",
                    (completed_at, scope_id, operation_id),
                )
                connection.execute(
                    "UPDATE reconciliation_runs SET status='completed',mismatch_count=0,"
                    "completed_at=?,updated_at=? WHERE id=? AND scope_id=?",
                    (completed_at, completed_at, reconciliation_id, scope_id),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO inbox_receipts (id,scope_id,operation_id,payload_hash,"
                    "result_status,processed_at,result_hash,source_instance_id,origin_instance_id,"
                    "created_at,integrity_hash,authority_role) VALUES (?,?,?,?,'completed',?,?,?,?,?,?, 'local')",
                    (
                        self._stable_id("inbox", operation_id, "memory_sync"),
                        scope_id,
                        operation_id,
                        payload_hash,
                        completed_at,
                        cloud_result_hash,
                        context.cloud_instance_id,
                        self.runtime.identity.database_generation_id,
                        completed_at,
                        sha256_text(
                            f"{operation_id}|{payload_hash}|{cloud_result_hash}|{completed_at}"
                        ),
                    ),
                )
                connection.execute("COMMIT")
        except LocalRuntimeError:
            failed_at = utc_now()
            with self.runtime._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE idempotency_records SET status='failed_retryable' "
                    "WHERE scope_id=? AND idempotency_key=?",
                    (scope_id, operation_key),
                )
                connection.execute(
                    "UPDATE commands SET status='failed_retryable',settled_at=? "
                    "WHERE scope_id=? AND operation_id=?",
                    (failed_at, scope_id, operation_id),
                )
                connection.execute(
                    "UPDATE reconciliation_runs SET status='failed_retryable',updated_at=? "
                    "WHERE id=? AND scope_id=?",
                    (failed_at, reconciliation_id, scope_id),
                )
                connection.execute("COMMIT")
            raise
        return {**self.memory_sync_status(project_id=project_id), "idempotentReplay": False}

    def run(
        self,
        *,
        project_id: str | None,
        question: str,
        mode: str,
        private_context_items: list[Mapping[str, Any]] | None = None,
        history_messages: list[Mapping[str, str]] | None = None,
        writing_style: str | None = None,
        agent_skills: list[Mapping[str, Any]] | None = None,
        image_context_items: list[Mapping[str, Any]] | None = None,
        deep_thinking: bool = False,
        stream_event_callback: Any | None = None,
        memory_policy: str = "member_private",
        source_manifest_extra: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        run_started_at = time.monotonic()
        if not project_id:
            raise LocalRuntimeError(422, "project_required", "工作台问答必须选择项目")
        normalized_question = str(question or "").strip()
        if not normalized_question:
            raise LocalRuntimeError(422, "ai_prompt_required", "请输入问题")
        if memory_policy not in {"member_private", "organization_publishable"}:
            raise LocalRuntimeError(422, "memory_policy_invalid", "记忆使用边界无效")
        context = self._context()
        operation_key = str(idempotency_key or "").strip()
        if not operation_key:
            raise LocalRuntimeError(422, "idempotency_required", "工作台问答缺少幂等键")
        # WorkspaceContext has no scope_id; the active sandbox is the fixed
        # source of the local scope for this pinned request.
        scope_id = str(self.runtime.capture_sandbox_context().scope_id or "")
        answer_id = self._stable_id("answer", scope_id, project_id, operation_key)
        existing = self._existing(answer_id)
        if existing is not None and (
            str(existing[0].get("projectId") or "") != project_id
            or str(existing[0].get("questionHash") or "")
            != sha256_text(normalized_question)
        ):
            raise LocalRuntimeError(
                409,
                "workbench_answer_idempotency_conflict",
                "该问答幂等键已用于另一个问题",
            )
        if existing is not None and existing[1] == "ready":
            return {"answer": existing[0], "idempotentReplay": True}

        source_manifest_extra = dict(source_manifest_extra or {})
        thread_id = str(source_manifest_extra.get("threadId") or answer_id)
        source_set_id = self._stable_id("source_set", answer_id)
        context_manifest_id = self._stable_id("ai_context", answer_id)
        lineage_id = self._stable_id("lineage", context_manifest_id)
        private_items = [dict(item) for item in private_context_items or []]
        local_sources = [
            {
                "sourceObjectId": str(item.get("documentId") or ""),
                "sourceObjectKind": "local_document",
                "sourceVersion": 1,
                "contentHash": str(
                    next(
                        (
                            source.get("contentHash")
                            for source in (
                                list(source_manifest_extra.get("selectedDocuments") or [])
                                + list(source_manifest_extra.get("retrievedDocuments") or [])
                            )
                            if isinstance(source, Mapping)
                            and str(source.get("documentId") or "")
                            == str(item.get("documentId") or "")
                        ),
                        "",
                    )
                    or ""
                ),
                "title": str(item.get("title") or item.get("documentId") or "本机资料"),
            }
            for item in private_items
            if str(item.get("documentId") or "") and str(item.get("content") or "").strip()
        ]
        knowledge, memory_state, memory_message = _load_optional_project_knowledge(
            self.runtime,
            project_id,
        )
        organization_items = [
            dict(item)
            for key, kind in (
                ("organizationSharedKnowledge", "organization_knowledge"),
                ("officialWebsiteFacts", "official_website_fact"),
                ("savedMemories", "explicit_memory"),
            )
            for item in knowledge.get(key) or []
            if isinstance(item, Mapping)
            and str(item.get("sourceId") or "")
            and str(item.get("summary") or "").strip()
            for item in (
                {
                    **item,
                    "_sourceObjectKind": (
                        "correction"
                        if str(item.get("memoryKind") or "") == "correction"
                        else kind
                    ),
                },
            )
        ]
        query_terms = list(
            dict.fromkeys(
                token.lower()
                for token in re.findall(
                    r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,24}",
                    question,
                )
                if token.strip()
            )
        )
        expanded_terms = list(query_terms)
        for token in query_terms:
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", token):
                expanded_terms.extend(
                    token[index : index + 2]
                    for index in range(len(token) - 1)
                )

        def organization_rank(item: Mapping[str, Any]) -> tuple[int, int, str]:
            haystack = (
                f"{item.get('sourceDescription') or ''} "
                f"{item.get('summary') or ''}"
            ).lower()
            kind = str(item.get("_sourceObjectKind") or item.get("sourceKind") or "")
            authority_priority = {
                "correction": 300,
                "explicit_memory": 250,
                "official_website_semantic_fact": 120,
                "official_website_fact": 60,
                "organization_knowledge": 40,
            }.get(kind, 20)
            lexical = sum(
                (40 if len(term) >= 3 else 8) * haystack.count(term)
                for term in dict.fromkeys(expanded_terms)
                if term in haystack
            )
            return (authority_priority + lexical, lexical, str(item.get("updatedAt") or ""))

        organization_items.sort(key=organization_rank, reverse=True)
        organization_items = organization_items[:20]
        from .project_materials_local import LocalProjectMaterialsRepository

        presented_memories = [
            dict(item)
            for item in LocalProjectMaterialsRepository(self.runtime)
            .knowledge_presentation(project_id)
            .get("savedMemories")
            or []
            if isinstance(item, Mapping)
            and str(item.get("id") or "")
            and str(item.get("summary") or "").strip()
        ]
        organization_source_ids = {
            str(item.get("sourceId") or "") for item in organization_items
        }
        organization_by_id = {
            str(item.get("sourceId") or ""): item for item in organization_items
        }
        for item in presented_memories:
            if str(item.get("authority") or "") != "organization_cloud":
                continue
            source_id = str(item.get("id") or "")
            if not source_id:
                continue
            if source_id in organization_source_ids:
                organization_by_id[source_id]["_supersededText"] = item.get(
                    "supersededText"
                )
                continue
            organization_items.append(
                {
                    "sourceId": source_id,
                    "sourceDescription": item.get("title") or "项目正式知识",
                    "summary": item.get("summary") or "",
                    "contentHash": item.get("contentHash") or "",
                    "_supersededText": item.get("supersededText"),
                    "_sourceObjectKind": (
                        "correction"
                        if str(item.get("memoryKind") or "") == "correction"
                        else "explicit_memory"
                    ),
                }
            )
            organization_source_ids.add(source_id)
            organization_by_id[source_id] = organization_items[-1]
        organization_sources = [
            {
                "sourceObjectId": str(item.get("sourceId")),
                "sourceObjectKind": str(item.get("_sourceObjectKind")),
                "sourceVersion": 1,
                "contentHash": str(item.get("contentHash") or ""),
                "title": str(item.get("sourceDescription") or "组织知识"),
            }
            for item in organization_items
        ]
        local_memory_items = [
            dict(item)
            for item in presented_memories
            if isinstance(item, Mapping)
            and str(item.get("id") or "")
            and str(item.get("summary") or "").strip()
            and str(item.get("authority") or "current_device") == "current_device"
        ]
        if memory_policy == "organization_publishable":
            # Organization narrative/report writes consume formal facts from
            # the cloud lane above, never member-private favorites.
            local_memory_items = []
        local_memory_items.sort(
            key=lambda item: (
                {
                    "correction": 0,
                    "explicit_memory": 1,
                    "favorite": 2,
                    "system_inference": 3,
                }.get(str(item.get("memoryKind") or ""), 3),
                -int(item.get("version") or 1),
                str(item.get("updatedAt") or ""),
            )
        )
        local_memory_items = local_memory_items[:20]
        local_memory_sources = [
            {
                "sourceObjectId": str(item.get("id") or ""),
                "sourceObjectKind": (
                    str(item.get("memoryKind") or "explicit_memory")
                ),
                "sourceVersion": max(1, int(item.get("version") or 1)),
                "contentHash": str(item.get("contentHash") or ""),
                "title": str(item.get("title") or "已存记忆"),
            }
            for item in local_memory_items
            if str(item.get("memoryKind") or "")
            in {"explicit_memory", "favorite", "correction"}
        ]
        skill_source = [
                {
                    "sourceObjectId": str(agent_skill.get("skillId") or ""),
                    "sourceObjectKind": "agent_skill",
                    "sourceVersion": max(1, int(agent_skill.get("version") or 1)),
                    "contentHash": str(agent_skill.get("contentHash") or ""),
                    "title": str(agent_skill.get("shortName") or "Skill"),
                }
                for agent_skill in (agent_skills or [])
                if str(agent_skill.get("skillId") or "")
            ]
        sources = local_sources + organization_sources + local_memory_sources + skill_source
        material_groups = sum(
            bool(group)
            for group in (local_sources, organization_sources, local_memory_sources)
        )
        if material_groups > 1:
            material_access_mode = "mixed"
            boundary_state = "mixed_boundary"
        elif local_sources:
            material_access_mode = "local_original"
            boundary_state = "local_private_context"
        elif organization_sources:
            material_access_mode = "organization_knowledge"
            boundary_state = "organization_published_context"
        elif local_memory_sources:
            material_access_mode = "memory_context"
            boundary_state = "member_local_memory_context"
        else:
            material_access_mode = "none"
            boundary_state = "no_material_context"

        if existing is None:
            project_name = ""
            with self.runtime._connection() as connection:
                row = connection.execute(
                    "SELECT name FROM clients WHERE id=? AND scope_id=? AND lifecycle_state='active'",
                    (project_id, scope_id),
                ).fetchone()
            if row is None:
                raise LocalRuntimeError(404, "project_missing", "当前工作空间没有该项目")
            project_name = str(row["name"] or "")
            context_lines = [f"当前项目：{project_name}。"]
            # The complete evidence inventory remains in the source manifest.
            # Deep mode only sends compact, ranked excerpts to the provider so
            # prompt prefill does not consume most of the interactive wait.
            private_item_limit = 6_000
            organization_item_limit = 2_000
            memory_item_limit = 4_000
            history_count_limit = 8
            history_item_limit = 12_000
            if deep_thinking:
                private_count = max(1, min(len(private_items), 8))
                private_item_limit = max(2_500, 24_000 // private_count)
                organization_item_limit = 800
                memory_item_limit = 900
                history_count_limit = 4
                history_item_limit = 3_000
            if private_items:
                context_lines.append(
                    "用户本轮明确选择的本机资料正文：\n"
                    + "\n\n".join(
                        f"【{str(item.get('title') or '本机资料')}】\n"
                        f"{str(item.get('content') or '')[:private_item_limit]}"
                        for item in private_items[:8]
                        if str(item.get("content") or "").strip()
                    )
                )
            if organization_items:
                organization_lines: list[str] = []
                for item in organization_items:
                    description = str(item.get("sourceDescription") or "组织知识")
                    summary = str(item.get("summary") or "")[:organization_item_limit]
                    superseded = (
                        ""
                        if memory_policy == "organization_publishable"
                        else str(item.get("_supersededText") or "").strip()
                    )
                    if str(item.get("_sourceObjectKind") or "") == "correction" and superseded:
                        organization_lines.append(
                            f"- {description}: 最新正式事实：{summary}\n"
                            f"  已被否定的旧表述：{superseded}\n"
                            "  回答时只陈述最新事实；除非用户明确询问纠错历史，"
                            "不得复述、括注、比较或暴露上述旧表述。"
                        )
                    else:
                        organization_lines.append(f"- {description}: {summary}")
                context_lines.append(
                    "组织云已发布的项目知识摘要：\n"
                    + "\n".join(organization_lines)
                )
            if local_memory_items:
                memory_lines: list[str] = []
                for item in local_memory_items:
                    title = str(item.get("title") or "已存记忆")
                    summary = str(item.get("summary") or "")[:memory_item_limit]
                    superseded = str(item.get("supersededText") or "").strip()
                    if (
                        str(item.get("memoryKind") or "") == "correction"
                        and superseded
                    ):
                        memory_lines.append(
                            f"【{title}】\n最新正式事实：{summary}\n"
                            f"已被否定的旧表述：{superseded}\n"
                            "回答时只陈述最新事实；除非用户明确询问纠错历史，"
                            "不得复述、括注、比较或暴露上述旧表述。"
                        )
                    else:
                        memory_lines.append(f"【{title}】\n{summary}")
                context_lines.append(
                    "当前项目已保存的记忆与正式人工纠正（仅限当前项目；"
                    "人工纠错/补充是最新正式事实，优先于旧回答、收藏和自动推断；"
                    "纠错后的正常回答只说新事实，不主动披露已否定内容）：\n"
                    + "\n\n".join(memory_lines)
                )
            if writing_style:
                context_lines.append("本轮写作风格要求：\n" + str(writing_style)[:6_000])
            if agent_skills:
                context_lines.append(
                    "本轮启用的声明式 Skill 组合（只能在下方岗位边界内调整分析方法和输出结构，"
                    "不得覆盖项目工作台 Agent 的核心规则，也不得直接产生业务写入）：\n"
                    + "\n\n".join(
                        f"【{str(item.get('shortName') or 'Skill')}】\n"
                        + str(item.get("renderedInstruction") or "")[:6_000]
                        for item in agent_skills
                    )
                )
            mode_contract = {
                "strict": (
                    "本轮采用“资料优先”模式。优先回答资料能够直接支持的事实；"
                    "把资料事实、基于资料的推断、尚待核实的建议明确分开。"
                    "资料不足时直接说明不能确认，不得用常识或旧回答补成项目事实；"
                    "仍可给出建议，但必须显式标注为建议或待核实。"
                ),
                "balanced": (
                    "本轮采用“兼顾资料”模式。先以项目资料确定事实底色，再做合理分析和表达；"
                    "明确区分已知事实与分析推断，不把推断写成已经发生的项目事实。"
                ),
                "creative": (
                    "本轮采用“创意优先”模式。项目资料仍是事实边界和约束，不得忽略或篡改；"
                    "可以跨领域联想、提出多种创意方向，并把已知事实、创意假设、建议和待核实项分开。"
                ),
            }[mode]
            system_prompt = (
                "你是益语智库项目工作台 Agent。只把明确提供的本机资料正文、"
                "组织已发布知识和当前对话当作项目事实；不得补造人物身份、机构关系或数据。"
                "历史助手回答只用于理解对话指代，不能作为事实证据；用户明确补充的事实仍需与本轮资料边界区分。"
                "材料没有覆盖时要明确说明缺口。回答直接、准确、可执行。\n"
                "使用规范 Markdown 输出：标题必须使用 # 到 ###### 的显式标题标记；"
                "普通正文和有序列表项不得冒充标题；有序列表保持连续编号，子项用缩进后的项目符号；"
                "表格使用标准 GFM 表格；不要用 Markdown 代码围栏包住整篇回答。\n"
                + mode_contract
                + "\n"
                + "\n".join(context_lines)
            )
            if deep_thinking:
                system_prompt += (
                    "\n本轮已开启供应商原生深度思考。请对本题进行必要而不冗长的严谨推理，"
                    "结合实际资料理解用户意图并形成判断；判断形成后立即输出正式答案，"
                    "不要为了展示思考而穷举步骤、重复材料或延长推理。"
                )
            messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
            messages.extend(
                {
                    "role": "assistant" if str(item.get("role") or "") == "assistant" else "user",
                    "content": str(item.get("content") or "")[:history_item_limit],
                }
                for item in (history_messages or [])[-history_count_limit:]
                if str(item.get("content") or "").strip()
            )
            image_items = [dict(item) for item in image_context_items or []]
            if image_items:
                user_content: list[dict[str, Any]] = [{"type": "text", "text": normalized_question}]
                user_content.extend(
                    {
                        "type": "image_url",
                        "image_url": {"url": str(item.get("dataUrl") or "")},
                    }
                    for item in image_items
                )
                messages.append({"role": "user", "content": user_content})
            else:
                messages.append({"role": "user", "content": normalized_question})
            if callable(stream_event_callback):
                completion = self.runtime.organization_ai_stream_completion(
                    messages=messages,
                    temperature=self._temperature(mode),
                    on_event=stream_event_callback,
                    read_timeout_seconds=60.0,
                    # Reasoning and the answer share this provider budget.
                    # 4K leaves room for a useful answer without encouraging
                    # the model to spend minutes on an exhaustive trace.
                    max_output_tokens=4_096 if deep_thinking else 2_048,
                    thinking_enabled=deep_thinking,
                )
            else:
                completion = self.runtime.organization_ai_completion(
                    messages=messages,
                    temperature=self._temperature(mode),
                    read_timeout_seconds=60.0 if deep_thinking else 45.0,
                    max_output_tokens=4_096 if deep_thinking else 2_048,
                    thinking_enabled=deep_thinking,
                )
            answer_markdown = str(completion.get("content") or "").strip()
            provider = dict(completion.get("provider") or {})
            provider_id = str(provider.get("configId") or "")
            model_name = str(provider.get("modelName") or "")
            bot_id = builtin_agent_id(context.organization_id, "project_workspace")
            self._project_agent_and_provider(provider=provider, bot_id=bot_id)
            public_analysis_plan = source_manifest_extra.get("publicAnalysisPlan")
            if deep_thinking:
                # Never manufacture a generic three-step trace for deep mode.
                # The UI must show only reasoning text actually returned by
                # the provider in this same completion request.
                analysis_trace = []
            else:
                analysis_trace = [
                    "理解问题",
                    "检索相关资料",
                    "生成并核对回答",
                ]
            source_manifest = {
                **source_manifest_extra,
                "operationKey": operation_key,
                "threadId": thread_id,
                "mode": mode,
                "deepThinkingRequested": deep_thinking,
                "analysisTrace": analysis_trace,
                "publicAnalysisPlan": (
                    dict(public_analysis_plan)
                    if isinstance(public_analysis_plan, Mapping)
                    else None
                ),
                "providerReasoningContent": (
                    str(completion.get("reasoningContent") or "").strip()
                    if deep_thinking
                    else ""
                ),
                "providerFinishReason": str(completion.get("finishReason") or ""),
                "providerUsage": dict(completion.get("usage") or {}),
                "multipassUsed": bool(
                    deep_thinking
                    and int(source_manifest_extra.get("retrievalPassCount") or 1) > 1
                ),
                "retrievalPassCount": int(
                    source_manifest_extra.get("retrievalPassCount") or 1
                ),
                "memoryPolicy": memory_policy,
                "selectedDocuments": source_manifest_extra.get("selectedDocuments") or [],
                "retrievedDocuments": source_manifest_extra.get("retrievedDocuments") or [],
                "localRetrievalState": source_manifest_extra.get("localRetrievalState") or "ready",
                "localRetrievalMessage": source_manifest_extra.get("localRetrievalMessage"),
                "organizationSources": [
                    {
                        "sourceId": source["sourceObjectId"],
                        "sourceKind": source["sourceObjectKind"],
                        "contentHash": source["contentHash"],
                        "title": source["title"],
                    }
                    for source in organization_sources
                ],
                "localMemorySources": [
                    {
                        "sourceId": source["sourceObjectId"],
                        "sourceKind": source["sourceObjectKind"],
                        "contentHash": source["contentHash"],
                        "title": source["title"],
                    }
                    for source in local_memory_sources
                ],
                "documentContentIncluded": bool(local_sources),
                "selectedDocumentContentCount": len(local_sources),
                "userSelectedDocumentCount": len(
                    source_manifest_extra.get("selectedDocuments") or []
                ),
                "localRetrievedDocumentCount": len(
                    source_manifest_extra.get("retrievedDocuments") or []
                ),
                "projectKnowledgeSummaryCount": len(organization_sources),
                "localMemoryCount": len(local_memory_sources),
                "activeAgentSkills": [
                    {
                        "skillId": source["sourceObjectId"],
                        "version": source["sourceVersion"],
                        "contentHash": source["contentHash"],
                        "shortName": source["title"],
                    }
                    for source in skill_source
                ],
                "activeAgentSkill": (
                    {
                        "skillId": skill_source[0]["sourceObjectId"],
                        "version": skill_source[0]["sourceVersion"],
                        "contentHash": skill_source[0]["contentHash"],
                        "shortName": skill_source[0]["title"],
                    }
                    if len(skill_source) == 1
                    else None
                ),
                "sourceCount": len(sources),
                "materialAccessMode": material_access_mode,
                "boundaryState": boundary_state,
                "memoryState": memory_state,
                "memoryMessage": memory_message,
                "sourceSetId": source_set_id,
                "aiContextManifestId": context_manifest_id,
                "botId": bot_id,
                "agentKind": "project_workspace",
                "providerResourceId": provider_id,
                "modelName": model_name,
                "timing": {
                    **dict(completion.get("timing") or {}),
                    "totalMs": max(1, int((time.monotonic() - run_started_at) * 1000)),
                },
            }
            created_at = utc_now()
            local_answer = self._persist_pending(
                answer_id=answer_id,
                client_id=project_id,
                thread_id=thread_id,
                question=normalized_question,
                answer_markdown=answer_markdown,
                source_manifest=source_manifest,
                source_set_id=source_set_id,
                context_manifest_id=context_manifest_id,
                lineage_id=lineage_id,
                provider_id=provider_id,
                bot_id=bot_id,
                model_name=model_name,
                sources=sources,
                material_access_mode=material_access_mode,
                boundary_state=boundary_state,
                created_at=created_at,
            )
        else:
            local_answer = existing[0]
            thread_id = str(local_answer.get("threadId") or thread_id)
            provider_id = str(local_answer.get("providerResourceId") or "")
            bot_id = str(local_answer.get("botId") or "")
            model_name = str(local_answer.get("modelName") or "")
            sources = [
                dict(item)
                for item in self._read_managed_payload(
                    f"ai-context:{context_manifest_id}"
                ).get("selectedSources")
                or []
                if isinstance(item, Mapping)
            ]
            material_access_mode = str(local_answer.get("materialAccessMode") or "none")
            boundary_state = str(local_answer.get("boundaryState") or "no_material_context")

        cloud_receipt = self.runtime.cloud_command(
            "POST",
            "/api/v2/workbench/answers",
            payload={
                "answerId": answer_id,
                "projectId": project_id,
                "threadId": thread_id,
                "questionHash": str(local_answer.get("questionHash") or sha256_text(normalized_question)),
                "answerHash": str(local_answer.get("answerHash") or sha256_text(str(local_answer.get("answerMarkdown") or ""))),
                "sourceSetId": source_set_id,
                "contextManifestId": context_manifest_id,
                "lineageId": lineage_id,
                "botId": bot_id,
                "providerResourceId": provider_id,
                "modelName": model_name,
                "sourceCount": len(sources),
                "materialAccessMode": material_access_mode,
                "boundaryState": boundary_state,
                "selectedSources": self._safe_cloud_sources(sources),
                "originInstanceId": self.runtime.identity.database_generation_id,
            },
            idempotency_key=operation_key,
        )
        receipt = dict(cloud_receipt.get("answer") or {})
        if (
            str(receipt.get("answerId") or "") != answer_id
            or str(receipt.get("threadId") or "") != thread_id
            or int(receipt.get("sourceCount") or 0) != len(sources)
            or str(receipt.get("answerHash") or "")
            != str(local_answer.get("answerHash") or "")
        ):
            raise LocalRuntimeError(
                502,
                "workbench_answer_receipt_mismatch",
                "组织云回答回执与本机回答不一致",
            )
        updated_at = str(receipt.get("updatedAt") or utc_now())
        self._mark_ready(answer_id, updated_at=updated_at)
        local_answer["updatedAt"] = updated_at
        local_answer["version"] = int(receipt.get("version") or 1)
        return {
            "answer": local_answer,
            "idempotentReplay": bool(cloud_receipt.get("idempotentReplay")),
        }
