"""Strict persistence for platform integrations.

Provider identities and side effects use the frozen generic ledgers.  Provider
credentials use the v3 scoped-configuration encrypted secret envelope; no
provider-specific credential table or plaintext receipt is introduced here.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlencode, urlparse

import httpx

from strict_common.ids import canonical_json, new_id, sha256_text, utc_now
from strict_common.security import normalize_phone

from ..repository import CloudRepository, RepositoryError, SessionIdentity
from .gc04_tasks import GC04TaskRepository
from .platform_configurations import PlatformConfigurationRepository
from .platform_operations import PlatformOperationRepository
from .platform_runtime_diagnostics import PlatformRuntimeDiagnosticsRepository


FEISHU_TENANT_TOKEN_URL = (
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
)
FEISHU_CONFIGURATION_KIND = "feishu_organization_application"
FEISHU_MEMBER_AUTHORIZATION_KIND = "feishu_member_oauth_authorization"
FEISHU_MEMBER_DELIVERY_PROFILE_KIND = "feishu_member_delivery_profile"
FEISHU_HTTP_CLIENT_FACTORY: Callable[..., Any] = httpx.Client
FEISHU_CALENDAR_API_ROOT = "https://open.feishu.cn/open-apis/calendar/v4/calendars"
FEISHU_PRIMARY_CALENDAR_ID = "primary"
FEISHU_OAUTH_AUTHORIZE_URL = (
    "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
)
FEISHU_OAUTH_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v2/oauth/token"
FEISHU_USER_INFO_URL = "https://open.feishu.cn/open-apis/authen/v1/user_info"
FEISHU_OAUTH_RELAY_DEFAULT_BASE_URL = "https://yiyu.love/oauth"
FEISHU_DOCUMENT_SEARCH_URL = (
    "https://open.feishu.cn/open-apis/search/v2/doc_wiki/search"
)
FEISHU_CONTACT_LOOKUP_URL = (
    "https://open.feishu.cn/open-apis/contact/v3/users/batch_get_id"
)
FEISHU_MESSAGE_CREATE_URL = (
    "https://open.feishu.cn/open-apis/im/v1/messages"
)
FEISHU_API_ROOT = "https://open.feishu.cn/open-apis"
FEISHU_MEMBER_DOCUMENT_SCOPES = (
    "offline_access",
    "docx:document:readonly",
    "docs:doc:readonly",
    "wiki:wiki:readonly",
    "drive:drive:readonly",
    "drive:export:readonly",
)


class _FeishuExecutionError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        receipt: Mapping[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.receipt = dict(receipt or {})


PLATFORM_TOOLS: tuple[dict[str, Any], ...] = (
    {
        "tool_name": "feishu",
        "description": "飞书机器人通知、任务单向投影与文档一次性导入",
        "risk_level": "high",
        "approval_required": True,
        "status": "available_when_configured",
        "external_side_effect": "encrypted_configuration_and_verified_provider",
    },
    {
        "tool_name": "local_asr",
        "description": "本机音频转写",
        "risk_level": "medium",
        "approval_required": False,
        "status": "partial",
        "external_side_effect": "local_device_only",
    },
    {
        "tool_name": "ollama",
        "description": "本机 Ollama 模型探测与模型管理",
        "risk_level": "medium",
        "approval_required": True,
        "status": "partial",
        "external_side_effect": "local_device_only",
    },
    {
        "tool_name": "support_request",
        "description": "组织内支持请求",
        "risk_level": "low",
        "approval_required": False,
        "status": "available",
        "external_side_effect": "durable_outbox",
    },
    {
        "tool_name": "software_feedback",
        "description": "软件反馈可靠投递",
        "risk_level": "low",
        "approval_required": False,
        "status": "available",
        "external_side_effect": "durable_outbox",
    },
)


class PlatformIntegrationsRepository:
    def __init__(
        self,
        repository: CloudRepository,
        *,
        feishu_http_client_factory: Callable[..., Any] | None = None,
    ):
        self.repository = repository
        self.configurations = PlatformConfigurationRepository(repository)
        self.operations = PlatformOperationRepository(repository)
        self.runtime_diagnostics = PlatformRuntimeDiagnosticsRepository(repository)
        self.feishu_http_client_factory = feishu_http_client_factory
        self._feishu_refresh_lock = threading.RLock()
        self._feishu_execution_lock = threading.RLock()

    @staticmethod
    def _feishu_oauth_relay_base_url() -> str:
        return str(
            os.getenv(
                "YIYU_FEISHU_OAUTH_RELAY_BASE_URL",
                FEISHU_OAUTH_RELAY_DEFAULT_BASE_URL,
            )
            or FEISHU_OAUTH_RELAY_DEFAULT_BASE_URL
        ).strip().rstrip("/")

    @classmethod
    def _feishu_oauth_relay_callback_url(cls) -> str:
        return f"{cls._feishu_oauth_relay_base_url()}/feishu/member/callback"

    def _connection(self):
        return self.repository._connection()

    @staticmethod
    def _payload(row: Mapping[str, Any] | None) -> dict[str, Any]:
        if row is None:
            return {}
        try:
            payload = json.loads(str(row["payload_json"]))
        except (KeyError, TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _json_text(value: Any) -> dict[str, Any]:
        try:
            payload = json.loads(str(value or "{}"))
        except (TypeError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    @staticmethod
    def _distribution(values: list[str]) -> dict[str, int]:
        result: dict[str, int] = {}
        for value in values:
            key = value or "unknown"
            result[key] = result.get(key, 0) + 1
        return result

    def _provider_resource(
        self,
        identity: SessionIdentity,
        *,
        provider: str,
        resource_kind: str,
        remote_id: str | None = None,
    ):
        where = [
            "scope_id = ?",
            "organization_id = ?",
            "provider = ?",
            "resource_kind = ?",
        ]
        params: list[Any] = [
            identity.scope_id,
            identity.organization_id,
            provider,
            resource_kind,
        ]
        if remote_id is not None:
            where.append("remote_id = ?")
            params.append(remote_id)
        with self._connection() as connection:
            return connection.execute(
                f"""
                SELECT * FROM external_provider_resources
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()

    def _existing_command_record(
        self,
        identity: SessionIdentity,
        *,
        command_type: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT receipt.receipt
                FROM commands AS c
                JOIN object_manifests AS receipt
                  ON receipt.id=c.payload_object_manifest_id
                 AND receipt.scope_id=c.scope_id
                WHERE c.scope_id = ? AND c.actor_principal_id = ?
                  AND c.command_type = ? AND c.idempotency_key = ?
                LIMIT 1
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
        envelope = self._json_text(row["receipt"])
        result = envelope.get("result")
        payload = envelope.get("payload")
        return (
            dict(result) if isinstance(result, Mapping) else {},
            dict(payload) if isinstance(payload, Mapping) else {},
        )

    def _record_command(
        self,
        identity: SessionIdentity,
        *,
        command_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
        provider: str,
        resource_kind: str,
        remote_id: str,
        outcome: str,
        retention_state: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        processing_kind: str | None = None,
        processing_state: str | None = None,
        result_details: Mapping[str, Any] | None = None,
        owner_kind: str = "organization",
    ) -> dict[str, Any]:
        return self.operations.record(
            identity,
            command_type=command_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload=payload,
            idempotency_key=idempotency_key,
            provider=provider,
            resource_kind=resource_kind,
            remote_id=remote_id,
            outcome=outcome,
            retention_state=retention_state,
            error_code=error_code,
            error_message=error_message,
            processing_kind=processing_kind,
            processing_state=processing_state,
            result_details=result_details,
            owner_kind=owner_kind,
        )

        # Frozen implementation retained below only for diff archaeology.  All
        # active callers return through the strict 88-table adapter above.
        now = utc_now()
        payload_json = canonical_json(dict(payload))
        payload_hash = sha256_text(payload_json)
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT c.command_id, c.operation_id, c.payload_hash, i.result_json
                FROM command_envelopes c
                LEFT JOIN command_idempotency i
                  ON i.scope_id = c.scope_id
                 AND i.actor_principal_id = c.actor_principal_id
                 AND i.command_type = c.command_type
                 AND i.idempotency_key = c.idempotency_key
                WHERE c.scope_id = ? AND c.actor_principal_id = ?
                  AND c.command_type = ? AND c.idempotency_key = ?
                """,
                (
                    identity.scope_id,
                    identity.principal_id,
                    command_type,
                    idempotency_key,
                ),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    raise RepositoryError(
                        409,
                        "idempotency_payload_conflict",
                        "同一幂等键不能提交不同内容",
                    )
                try:
                    result = json.loads(str(existing["result_json"] or "{}"))
                except ValueError:
                    result = {}
                return result if isinstance(result, dict) else {}

            command_id = new_id()
            operation_id = new_id()
            processing_attempt_id = new_id() if processing_kind else None
            provider_resource = connection.execute(
                """
                SELECT provider_resource_id
                FROM external_provider_resources
                WHERE scope_id = ? AND provider = ? AND resource_kind = ?
                  AND remote_id = ?
                """,
                (identity.scope_id, provider, resource_kind, remote_id),
            ).fetchone()
            provider_resource_id = (
                str(provider_resource["provider_resource_id"])
                if provider_resource is not None
                else new_id()
            )
            result = {
                "operationId": operation_id,
                "processingAttemptId": processing_attempt_id,
                "state": outcome,
                "errorCode": error_code,
                "message": error_message or "",
                "retryable": outcome == "failed_retryable",
                **dict(result_details or {}),
            }
            if isinstance(result.get("details"), Mapping):
                result["details"] = {
                    **dict(result["details"]),
                    "processingAttemptId": processing_attempt_id,
                }
            result_json = canonical_json(result)
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO command_envelopes (
                        command_id, scope_id, organization_id, operation_id,
                        idempotency_key, aggregate_type, aggregate_id,
                        command_type, actor_principal_id, expected_version,
                        payload_json, payload_hash, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)
                    """,
                    (
                        command_id,
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                        idempotency_key,
                        aggregate_type,
                        aggregate_id,
                        command_type,
                        identity.principal_id,
                        payload_json,
                        payload_hash,
                        "committed" if outcome in {"queued", "succeeded"} else "failed",
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO external_provider_resources (
                        provider_resource_id, scope_id, organization_id,
                        provider, resource_kind, remote_id, retention_state,
                        version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(scope_id, provider, resource_kind, remote_id)
                    DO UPDATE SET
                        retention_state = excluded.retention_state,
                        version = external_provider_resources.version + 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        provider_resource_id,
                        identity.scope_id,
                        identity.organization_id,
                        provider,
                        resource_kind,
                        remote_id,
                        retention_state
                        or (
                            "active"
                            if outcome in {"queued", "succeeded"}
                            else "blocked"
                        ),
                        now,
                        now,
                    ),
                )
                event_id = new_id()
                connection.execute(
                    """
                    INSERT INTO delivery_outbox (
                        event_id, scope_id, organization_id, operation_id,
                        aggregate_type, aggregate_id, aggregate_version,
                        event_type, payload_json, payload_hash, status,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                        aggregate_type,
                        aggregate_id,
                        command_type,
                        payload_json,
                        payload_hash,
                        (
                            "pending"
                            if outcome == "queued"
                            else (
                                "delivered"
                                if outcome == "succeeded"
                                else "failed"
                            )
                        ),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO operation_attempts (
                        attempt_id, scope_id, command_id, attempt_no,
                        transport_state, lease_owner, lease_until,
                        permission_revalidated_at, next_retry_at,
                        error_code, error_message, created_at
                    ) VALUES (?, ?, ?, 1, ?, NULL, NULL, ?, NULL, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        command_id,
                        outcome,
                        now,
                        error_code,
                        error_message,
                        now,
                    ),
                )
                if processing_kind:
                    resolved_processing_state = processing_state or (
                        "queued"
                        if outcome == "queued"
                        else (
                            "completed"
                            if outcome == "succeeded"
                            else "failed"
                        )
                    )
                    connection.execute(
                        """
                        INSERT INTO processing_attempts (
                            processing_attempt_id, organization_id,
                            source_asset_id, document_id, processing_kind,
                            state, attempt_no, error_code, error_message,
                            started_at, finished_at, created_at
                        ) VALUES (?, ?, NULL, NULL, ?, ?, 1, ?, ?, ?, ?, ?)
                        """,
                        (
                            processing_attempt_id,
                            identity.organization_id,
                            processing_kind,
                            resolved_processing_state,
                            error_code or "",
                            error_message or "",
                            (
                                None
                                if resolved_processing_state == "queued"
                                else now
                            ),
                            (
                                now
                                if resolved_processing_state
                                in {"completed", "partial", "failed", "cancelled"}
                                else None
                            ),
                            now,
                        ),
                    )
                if outcome in {"blocked", "failed_retryable"}:
                    connection.execute(
                        """
                        INSERT INTO operation_dead_letters (
                            dead_letter_id, scope_id, organization_id,
                            operation_id, aggregate_type, aggregate_id,
                            error_code, error_message, status, created_at,
                            resolved_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, NULL)
                        """,
                        (
                            new_id(),
                            identity.scope_id,
                            identity.organization_id,
                            operation_id,
                            aggregate_type,
                            aggregate_id,
                            error_code or "platform_operation_blocked",
                            error_message or "平台操作被阻止",
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO reconciliation_runs (
                            run_id, scope_id, organization_id, operation_id,
                            registry_state_id, mismatch_count, status,
                            report_json, started_at, finished_at
                        ) VALUES (?, ?, ?, ?, ?, 1, 'completed', ?, ?, ?)
                        """,
                        (
                            new_id(),
                            identity.scope_id,
                            identity.organization_id,
                            operation_id,
                            provider_resource_id,
                            canonical_json(
                                {
                                    "provider": provider,
                                    "resourceKind": resource_kind,
                                    "errorCode": (
                                        error_code or "platform_operation_blocked"
                                    ),
                                    "deterministicRepair": False,
                                    "retryRequired": True,
                                }
                            ),
                            now,
                            now,
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO external_side_effects (
                        effect_id, scope_id, organization_id, operation_id,
                        provider_resource_id, effect_kind, outcome,
                        receipt_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                        provider_resource_id,
                        command_type,
                        outcome,
                        sha256_text(result_json),
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO command_idempotency (
                        record_id, scope_id, actor_principal_id, command_type,
                        idempotency_key, payload_hash, result_hash, result_json,
                        expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        "9999-12-31T23:59:59.999Z",
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return result

    def _feishu_configuration(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        return self.configurations.read(
            identity,
            configuration_kind=FEISHU_CONFIGURATION_KIND,
            defaults={
                "appId": "",
                "callbackMode": "cloud_relay",
                "customCallbackUrl": "",
            },
        )

    def _feishu_configuration_for_scope(
        self,
        identity: SessionIdentity,
        *,
        scope_kind: str,
    ) -> dict[str, Any]:
        effective = self._feishu_configuration(identity)
        defaults = {
            "appId": "",
            "callbackMode": "cloud_relay",
            "customCallbackUrl": "",
        }
        exact = self.configurations.read_exact(
            identity,
            configuration_kind=FEISHU_CONFIGURATION_KIND,
            scope_kind=scope_kind,
            defaults=defaults,
        )
        exact["scopeVersions"] = dict(effective.get("scopeVersions") or {})
        return exact

    def _feishu_secret_for_scope(
        self,
        identity: SessionIdentity,
        *,
        scope_kind: str,
    ) -> dict[str, Any] | None:
        return self.configurations.secret_exact(
            identity,
            configuration_kind=FEISHU_CONFIGURATION_KIND,
            scope_kind=scope_kind,
        )

    def _feishu_member_configuration(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        return self.configurations.read(
            identity,
            configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
            defaults={
                "linked": False,
                "authorizationState": "not_connected",
                "appId": "",
            },
            personal_only=True,
        )

    def _feishu_member_secret(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any] | None:
        return self.configurations.secret_exact(
            identity,
            configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
            scope_kind="personal",
        )

    def _feishu_delivery_profile_secret(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any] | None:
        return self.configurations.secret_exact(
            identity,
            configuration_kind=FEISHU_MEMBER_DELIVERY_PROFILE_KIND,
            scope_kind="personal",
        )

    @staticmethod
    def _future_timestamp(seconds: Any, *, fallback: int = 0) -> str | None:
        try:
            normalized = int(seconds or fallback)
        except (TypeError, ValueError):
            normalized = fallback
        if normalized <= 0:
            return None
        return (
            (datetime.now(timezone.utc) + timedelta(seconds=normalized))
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )

    @staticmethod
    def _timestamp_is_fresh(value: Any, *, margin_seconds: int = 60) -> bool:
        normalized = str(value or "").strip()
        if not normalized:
            return False
        try:
            expires_at = datetime.fromisoformat(
                normalized.replace("Z", "+00:00")
            )
        except ValueError:
            return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at > datetime.now(timezone.utc) + timedelta(
            seconds=margin_seconds
        )

    def _feishu_provider_json(
        self,
        method: str,
        url: str,
        *,
        access_token: str | None = None,
        payload: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
        timeout_seconds: float = 18.0,
    ) -> dict[str, Any]:
        factory = self.feishu_http_client_factory or FEISHU_HTTP_CLIENT_FACTORY
        headers = (
            {"Authorization": f"Bearer {access_token}"}
            if access_token
            else {}
        )
        try:
            with factory(
                timeout=httpx.Timeout(timeout_seconds, connect=5.0),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                if method == "GET":
                    response = client.get(
                        url,
                        headers=headers,
                        params=dict(params or {}),
                    )
                elif method == "PATCH":
                    response = client.patch(
                        url,
                        headers=headers,
                        json=dict(payload or {}),
                        params=dict(params or {}),
                    )
                elif method == "DELETE":
                    response = client.request(
                        "DELETE",
                        url,
                        headers=headers,
                        json=dict(payload or {}),
                        params=dict(params or {}),
                    )
                else:
                    response = client.post(
                        url,
                        headers=headers,
                        json=dict(payload or {}),
                        params=dict(params or {}),
                    )
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException as exc:
            raise _FeishuExecutionError(
                "feishu_request_timeout",
                "飞书接口请求超时，请稍后重试",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise _FeishuExecutionError(
                "feishu_request_http_error",
                f"飞书接口返回 HTTP {exc.response.status_code}，请稍后重试",
            ) from exc
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise _FeishuExecutionError(
                "feishu_request_failed",
                "飞书接口请求失败，请稍后重试",
            ) from exc
        except Exception as exc:  # pragma: no cover - injected provider clients
            raise _FeishuExecutionError(
                "feishu_request_failed",
                "飞书接口请求失败，请稍后重试",
            ) from exc
        if not isinstance(result, dict):
            raise _FeishuExecutionError(
                "feishu_response_invalid",
                "飞书接口返回了无效响应",
            )
        try:
            provider_code = int(result.get("code", 0) or 0)
        except (TypeError, ValueError):
            provider_code = -1
        if provider_code != 0 or result.get("error"):
            raise _FeishuExecutionError(
                "feishu_provider_rejected",
                f"飞书拒绝了请求（code={provider_code}），请检查授权与权限后重试",
            )
        return result

    def _feishu_oauth_relay_json(
        self,
        path: str,
        *,
        payload: Mapping[str, Any],
        not_found_is_pending: bool = False,
    ) -> dict[str, Any]:
        factory = self.feishu_http_client_factory or FEISHU_HTTP_CLIENT_FACTORY
        url = f"{self._feishu_oauth_relay_base_url()}/{path.strip('/')}"
        try:
            with factory(
                timeout=httpx.Timeout(8.0, connect=3.0),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.post(url, json=dict(payload))
                if not_found_is_pending and response.status_code == 404:
                    return {"status": "pending"}
                response.raise_for_status()
                result = response.json()
        except httpx.TimeoutException as exc:
            raise _FeishuExecutionError(
                "feishu_oauth_relay_timeout",
                "飞书统一授权服务请求超时，请稍后重试",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise _FeishuExecutionError(
                "feishu_oauth_relay_http_error",
                f"飞书统一授权服务返回 HTTP {exc.response.status_code}，请稍后重试",
            ) from exc
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise _FeishuExecutionError(
                "feishu_oauth_relay_failed",
                "飞书统一授权服务暂时无法连接，请稍后重试",
            ) from exc
        except Exception as exc:  # pragma: no cover - injected clients
            raise _FeishuExecutionError(
                "feishu_oauth_relay_failed",
                "飞书统一授权服务暂时无法连接，请稍后重试",
            ) from exc
        if not isinstance(result, dict):
            raise _FeishuExecutionError(
                "feishu_oauth_relay_invalid",
                "飞书统一授权服务返回了无效响应",
            )
        return result

    def _register_feishu_oauth_relay_session(
        self,
        *,
        state_token: str,
        claim_secret: str,
        expires_at: str,
    ) -> None:
        result = self._feishu_oauth_relay_json(
            "feishu/member/sessions",
            payload={
                "stateHash": sha256_text(state_token),
                "claimSecretHash": sha256_text(claim_secret),
                "expiresAt": expires_at.replace("Z", "+00:00"),
            },
        )
        if str(result.get("status") or "") not in {"registered", "pending"}:
            raise _FeishuExecutionError(
                "feishu_oauth_relay_registration_invalid",
                "飞书统一授权服务没有确认授权会话，请稍后重试",
            )

    def _claim_feishu_oauth_relay_code(
        self,
        *,
        state_token: str,
        claim_secret: str,
    ) -> dict[str, Any]:
        return self._feishu_oauth_relay_json(
            "feishu/member/code/claim",
            payload={"state": state_token, "claimSecret": claim_secret},
            not_found_is_pending=True,
        )

    def _feishu_effective_identity(
        self,
        identity: SessionIdentity,
        configuration: Mapping[str, Any],
    ) -> SessionIdentity:
        if configuration.get("effectiveScopeKind") == "personal":
            return self._personal_identity(identity)
        return identity

    def _latest_feishu_validation(
        self,
        identity: SessionIdentity,
        *,
        app_id: str,
    ) -> Mapping[str, Any] | None:
        if not app_id:
            return None
        with self._connection() as connection:
            return connection.execute(
                """
                SELECT a.transport_state, a.error_code, a.error_message,
                       c.created_at, c.updated_at
                FROM command_envelopes AS c
                JOIN operation_attempts AS a
                  ON a.command_id = c.command_id AND a.scope_id = c.scope_id
                WHERE c.scope_id = ? AND c.organization_id = ?
                  AND c.command_type = 'feishu.validate_and_save'
                  AND c.aggregate_type = 'external_provider'
                  AND c.aggregate_id = ?
                ORDER BY c.created_at DESC, a.attempt_no DESC
                LIMIT 1
                """,
                (identity.scope_id, identity.organization_id, app_id),
            ).fetchone()

    def _verify_feishu_tenant_token(
        self,
        *,
        app_id: str,
        app_secret: str,
    ) -> tuple[bool, str | None, str]:
        factory = self.feishu_http_client_factory or FEISHU_HTTP_CLIENT_FACTORY
        try:
            with factory(
                timeout=httpx.Timeout(10.0, connect=5.0),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    FEISHU_TENANT_TOKEN_URL,
                    json={"app_id": app_id, "app_secret": app_secret},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            return (
                False,
                "feishu_tenant_token_http_error",
                f"飞书凭据验证请求返回 HTTP {exc.response.status_code}，可修改配置后重试",
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return (
                False,
                "feishu_tenant_token_request_failed",
                "飞书凭据验证请求失败，可稍后重试",
            )
        except Exception:  # pragma: no cover - injected clients may raise provider errors
            return (
                False,
                "feishu_tenant_token_request_failed",
                "飞书凭据验证请求失败，可稍后重试",
            )
        if not isinstance(payload, Mapping):
            return (
                False,
                "feishu_tenant_token_response_invalid",
                "飞书凭据验证返回无效响应，可稍后重试",
            )
        try:
            provider_code = int(payload.get("code", -1))
        except (TypeError, ValueError):
            provider_code = -1
        if provider_code != 0:
            return (
                False,
                "feishu_tenant_token_rejected",
                f"飞书拒绝了应用凭据（code={provider_code}），请检查后重试",
            )
        if not str(payload.get("tenant_access_token") or ""):
            return (
                False,
                "feishu_tenant_token_response_invalid",
                "飞书凭据验证未返回有效租户令牌，可稍后重试",
            )
        return True, None, "飞书应用凭据验证成功"

    def _feishu_tenant_access_token(
        self,
        identity: SessionIdentity,
        configuration: Mapping[str, Any],
    ) -> str:
        scope_kind = str(
            configuration.get("effectiveScopeKind") or "organization"
        )
        secret_bundle = self._feishu_secret_for_scope(
            identity,
            scope_kind=scope_kind,
        )
        app_id = str(configuration.get("appId") or "")
        app_secret = str((secret_bundle or {}).get("appSecret") or "")
        if not app_id or not app_secret:
            raise _FeishuExecutionError(
                "feishu_configuration_missing",
                "飞书应用配置缺少有效凭据；未发送同步请求",
            )
        factory = self.feishu_http_client_factory or FEISHU_HTTP_CLIENT_FACTORY
        try:
            with factory(
                timeout=httpx.Timeout(8.0, connect=3.0),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    FEISHU_TENANT_TOKEN_URL,
                    json={"app_id": app_id, "app_secret": app_secret},
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise _FeishuExecutionError(
                "feishu_sync_timeout",
                "飞书同步请求超时，可稍后重试",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise _FeishuExecutionError(
                "feishu_sync_provider_rejected",
                f"飞书同步凭据请求返回 HTTP {exc.response.status_code}，可稍后重试",
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise _FeishuExecutionError(
                "feishu_sync_request_failed",
                "飞书同步凭据请求失败，可稍后重试",
            ) from exc
        except Exception as exc:  # pragma: no cover - injected provider clients
            raise _FeishuExecutionError(
                "feishu_sync_request_failed",
                "飞书同步凭据请求失败，可稍后重试",
            ) from exc
        if not isinstance(payload, Mapping):
            raise _FeishuExecutionError(
                "feishu_sync_response_invalid",
                "飞书同步凭据响应无效，可稍后重试",
            )
        try:
            provider_code = int(payload.get("code", -1))
        except (TypeError, ValueError):
            provider_code = -1
        if provider_code != 0:
            raise _FeishuExecutionError(
                "feishu_sync_provider_rejected",
                f"飞书拒绝了同步凭据（code={provider_code}），请检查配置后重试",
            )
        token = str(payload.get("tenant_access_token") or "")
        if not token:
            raise _FeishuExecutionError(
                "feishu_sync_response_invalid",
                "飞书同步凭据响应缺少租户令牌，可稍后重试",
            )
        return token

    @staticmethod
    def _calendar_date(value: Any) -> date | None:
        text = str(value or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _calendar_datetime(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed

    def _feishu_task_event_payload(
        self,
        task: Mapping[str, Any],
        *,
        notify: bool,
    ) -> dict[str, Any]:
        summary = str(task.get("title") or "").strip()
        if not summary:
            raise RepositoryError(422, "task_title_required", "任务标题为空，无法同步")
        scheduled_start_text = str(
            task.get("scheduledStartAt") or task.get("scheduled_start_at") or ""
        ).strip()
        scheduled_end_text = str(
            task.get("scheduledEndAt") or task.get("scheduled_end_at") or ""
        ).strip()
        if scheduled_start_text:
            scheduled_start = self._calendar_datetime(scheduled_start_text)
            scheduled_end = self._calendar_datetime(scheduled_end_text)
            if scheduled_start is None or (
                scheduled_end_text and scheduled_end is None
            ):
                raise _FeishuExecutionError(
                    "feishu_task_timezone_missing",
                    "任务定时时间缺少明确时区，未向飞书发送；请改为全天日期或带时区时间",
                )
            if scheduled_end is None:
                duration = max(
                    15,
                    int(
                        task.get("durationMinutes")
                        or task.get("duration_minutes")
                        or 60
                    ),
                )
                scheduled_end = scheduled_start + timedelta(minutes=duration)
            if scheduled_end <= scheduled_start:
                raise _FeishuExecutionError(
                    "feishu_task_time_invalid",
                    "任务结束时间不晚于开始时间，未向飞书发送",
                )
            start_time = {"timestamp": str(int(scheduled_start.timestamp()))}
            end_time = {"timestamp": str(int(scheduled_end.timestamp()))}
        else:
            all_day = (
                self._calendar_date(task.get("dueDate") or task.get("due_date"))
                or self._calendar_date(task.get("deadlineAt"))
                or self._calendar_date(task.get("startDate"))
            )
            if all_day is None:
                raise _FeishuExecutionError(
                    "feishu_task_time_missing",
                    "任务没有可同步的日期，未向飞书发送",
                )
            start_time = {"date": all_day.isoformat()}
            end_time = {"date": (all_day + timedelta(days=1)).isoformat()}
        return {
            "summary": summary[:255],
            "description": str(task.get("description") or "")[:2000],
            "start_time": start_time,
            "end_time": end_time,
            "need_notification": bool(notify),
        }

    def _execute_feishu_calendar_event(
        self,
        identity: SessionIdentity,
        *,
        configuration: Mapping[str, Any],
        event_payload: Mapping[str, Any],
        provider_idempotency_key: str,
        remote_id: str | None,
        calendar_id: str | None,
    ) -> dict[str, Any]:
        token = self._feishu_tenant_access_token(identity, configuration)
        factory = self.feishu_http_client_factory or FEISHU_HTTP_CLIENT_FACTORY
        try:
            with factory(
                timeout=httpx.Timeout(8.0, connect=3.0),
                trust_env=False,
                follow_redirects=False,
            ) as client:
                authorization_headers = {"Authorization": f"Bearer {token}"}
                if not calendar_id:
                    calendar_response = client.post(
                        f"{FEISHU_CALENDAR_API_ROOT}/{FEISHU_PRIMARY_CALENDAR_ID}",
                        headers=authorization_headers,
                    )
                    calendar_response.raise_for_status()
                    calendar_payload = calendar_response.json()
                    if not isinstance(calendar_payload, Mapping):
                        raise _FeishuExecutionError(
                            "feishu_sync_response_invalid",
                            "飞书主日历响应无效，可稍后重试",
                        )
                    try:
                        calendar_code = int(calendar_payload.get("code", -1))
                    except (TypeError, ValueError):
                        calendar_code = -1
                    if calendar_code != 0:
                        raise _FeishuExecutionError(
                            "feishu_sync_provider_rejected",
                            (
                                "飞书拒绝了主日历查询"
                                f"（code={calendar_code}），可稍后重试"
                            ),
                        )
                    calendar_data = calendar_payload.get("data")
                    calendars = (
                        calendar_data.get("calendars")
                        if isinstance(calendar_data, Mapping)
                        else None
                    )
                    first_calendar = (
                        calendars[0]
                        if isinstance(calendars, list) and calendars
                        else None
                    )
                    calendar = (
                        first_calendar.get("calendar")
                        if isinstance(first_calendar, Mapping)
                        else None
                    )
                    calendar_id = str(
                        calendar.get("calendar_id")
                        if isinstance(calendar, Mapping)
                        else ""
                    )
                    if not calendar_id:
                        raise _FeishuExecutionError(
                            "feishu_sync_response_invalid",
                            "飞书主日历响应缺少日历标识，可稍后重试",
                        )
                encoded_calendar = quote(calendar_id, safe="")
                if remote_id:
                    encoded_event = quote(remote_id, safe="")
                    url = (
                        f"{FEISHU_CALENDAR_API_ROOT}/{encoded_calendar}"
                        f"/events/{encoded_event}"
                    )
                else:
                    url = (
                        f"{FEISHU_CALENDAR_API_ROOT}/{encoded_calendar}/events"
                    )
                request_args = {
                    "headers": authorization_headers,
                    "json": dict(event_payload),
                }
                if remote_id:
                    response = client.patch(url, **request_args)
                else:
                    response = client.post(
                        url,
                        params={"idempotency_key": provider_idempotency_key},
                        **request_args,
                    )
                response.raise_for_status()
                payload = response.json()
        except _FeishuExecutionError:
            raise
        except httpx.TimeoutException as exc:
            raise _FeishuExecutionError(
                "feishu_sync_timeout",
                "飞书日历请求超时，可稍后重试",
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise _FeishuExecutionError(
                "feishu_sync_provider_rejected",
                f"飞书日历请求返回 HTTP {exc.response.status_code}，可稍后重试",
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise _FeishuExecutionError(
                "feishu_sync_request_failed",
                "飞书日历请求失败，可稍后重试",
            ) from exc
        except Exception as exc:  # pragma: no cover - injected provider clients
            raise _FeishuExecutionError(
                "feishu_sync_request_failed",
                "飞书日历请求失败，可稍后重试",
            ) from exc
        if not isinstance(payload, Mapping):
            raise _FeishuExecutionError(
                "feishu_sync_response_invalid",
                "飞书日历响应无效，可稍后重试",
            )
        try:
            provider_code = int(payload.get("code", -1))
        except (TypeError, ValueError):
            provider_code = -1
        if provider_code != 0:
            raise _FeishuExecutionError(
                "feishu_sync_provider_rejected",
                f"飞书拒绝了日历同步（code={provider_code}），可稍后重试",
            )
        data = payload.get("data")
        event = data.get("event") if isinstance(data, Mapping) else None
        event = event if isinstance(event, Mapping) else {}
        resolved_remote_id = str(event.get("event_id") or remote_id or "")
        if not resolved_remote_id:
            raise _FeishuExecutionError(
                "feishu_sync_response_invalid",
                "飞书日历响应缺少事件标识，可稍后重试",
            )
        remote_url = str(
            event.get("app_link")
            or event.get("html_link")
            or event.get("event_url")
            or ""
        )
        return {
            "calendarId": calendar_id,
            "remoteId": resolved_remote_id,
            "remoteUrl": remote_url or None,
        }

    @staticmethod
    def _feishu_docx_document_id(payload: Mapping[str, Any]) -> str:
        data = payload.get("data")
        if isinstance(data, Mapping):
            document = data.get("document")
            if isinstance(document, Mapping) and document.get("document_id"):
                return str(document["document_id"])
            if data.get("document_id"):
                return str(data["document_id"])
        return str(payload.get("document_id") or "")

    @staticmethod
    def _basic_feishu_docx_blocks(content: str) -> list[dict[str, Any]]:
        lines = str(content or "").replace("\r\n", "\n").splitlines()
        blocks = []
        for line in lines:
            if len(blocks) >= 80:
                break
            normalized = line[:5_000]
            if not normalized and blocks:
                normalized = " "
            blocks.append(
                {
                    "block_type": 2,
                    "text": {
                        "elements": [
                            {"text_run": {"content": normalized or " "}}
                        ]
                    },
                }
            )
        return blocks or [
            {
                "block_type": 2,
                "text": {
                    "elements": [
                        {"text_run": {"content": "（空文档）"}}
                    ]
                },
            }
        ]

    def _execute_feishu_docx_sync(
        self,
        identity: SessionIdentity,
        *,
        configuration: Mapping[str, Any],
        title: str,
        content: str,
        member_open_id: str,
        remote_id: str | None,
        provider_idempotency_key: str,
    ) -> dict[str, Any]:
        tenant_token = self._feishu_tenant_access_token(
            identity,
            configuration,
        )
        document_id = str(remote_id or "")
        action = "update" if document_id else "create"
        try:
            if not document_id:
                created = self._feishu_provider_json(
                    "POST",
                    f"{FEISHU_API_ROOT}/docx/v1/documents",
                    access_token=tenant_token,
                    payload={"title": title[:200] or "益语同步文档"},
                    params={"client_token": provider_idempotency_key},
                )
                document_id = self._feishu_docx_document_id(created)
                if not document_id:
                    raise _FeishuExecutionError(
                        "feishu_docx_receipt_invalid",
                        "飞书已接收创建请求，但没有返回文档标识",
                    )
            else:
                self._feishu_provider_json(
                    "PATCH",
                    (
                        f"{FEISHU_API_ROOT}/docx/v1/documents/"
                        f"{quote(document_id, safe='')}"
                    ),
                    access_token=tenant_token,
                    payload={"title": title[:200] or "益语同步文档"},
                )
                listed = self._feishu_provider_json(
                    "GET",
                    (
                        f"{FEISHU_API_ROOT}/docx/v1/documents/"
                        f"{quote(document_id, safe='')}/blocks"
                    ),
                    access_token=tenant_token,
                    params={"page_size": 500},
                )
                data = listed.get("data")
                items = (
                    data.get("items")
                    if isinstance(data, Mapping)
                    and isinstance(data.get("items"), list)
                    else []
                )
                child_ids: list[str] = []
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    block_id = str(
                        item.get("block_id")
                        or item.get("blockId")
                        or item.get("id")
                        or ""
                    )
                    if block_id != document_id:
                        continue
                    children = item.get("children")
                    if isinstance(children, list):
                        child_ids = [str(value) for value in children if value]
                    break
                if child_ids:
                    self._feishu_provider_json(
                        "DELETE",
                        (
                            f"{FEISHU_API_ROOT}/docx/v1/documents/"
                            f"{quote(document_id, safe='')}/blocks/"
                            f"{quote(document_id, safe='')}/children/batch_delete"
                        ),
                        access_token=tenant_token,
                        params={
                            "document_revision_id": -1,
                            "client_token": provider_idempotency_key,
                        },
                        payload={
                            "start_index": 0,
                            "end_index": len(child_ids),
                        },
                    )

            try:
                converted = self._feishu_provider_json(
                    "POST",
                    f"{FEISHU_API_ROOT}/docx/v1/documents/blocks/convert",
                    access_token=tenant_token,
                    payload={
                        "content_type": "markdown",
                        "content": content,
                    },
                )
                converted_data = converted.get("data")
                blocks = (
                    converted_data.get("blocks")
                    if isinstance(converted_data, Mapping)
                    and isinstance(converted_data.get("blocks"), list)
                    else []
                )
            except _FeishuExecutionError:
                blocks = []
            normalized_blocks = [
                dict(block)
                for block in blocks
                if isinstance(block, Mapping)
            ][:80]
            if not normalized_blocks:
                normalized_blocks = self._basic_feishu_docx_blocks(content)
            self._feishu_provider_json(
                "POST",
                (
                    f"{FEISHU_API_ROOT}/docx/v1/documents/"
                    f"{quote(document_id, safe='')}/blocks/"
                    f"{quote(document_id, safe='')}/children"
                ),
                access_token=tenant_token,
                payload={"children": normalized_blocks},
            )
            public_permission = "synced"
            try:
                self._feishu_provider_json(
                    "PATCH",
                    (
                        f"{FEISHU_API_ROOT}/drive/v1/permissions/"
                        f"{quote(document_id, safe='')}/public"
                    ),
                    access_token=tenant_token,
                    params={"type": "docx"},
                    payload={
                        "link_share_entity": "closed",
                        "external_access_entity": "closed",
                    },
                )
            except _FeishuExecutionError:
                public_permission = "failed"
            self._feishu_provider_json(
                "POST",
                (
                    f"{FEISHU_API_ROOT}/drive/v1/permissions/"
                    f"{quote(document_id, safe='')}/members"
                ),
                access_token=tenant_token,
                params={"type": "docx", "need_notification": "false"},
                payload={
                    "member_type": "openid",
                    "member_id": member_open_id,
                    "perm": "full_access",
                },
            )
            owner_status = "transferred"
            try:
                self._feishu_provider_json(
                    "POST",
                    (
                        f"{FEISHU_API_ROOT}/drive/v1/permissions/"
                        f"{quote(document_id, safe='')}/members/transfer_owner"
                    ),
                    access_token=tenant_token,
                    params={"type": "docx"},
                    payload={
                        "member_type": "openid",
                        "member_id": member_open_id,
                    },
                )
            except _FeishuExecutionError:
                owner_status = "permission_only"
        except _FeishuExecutionError as exc:
            if document_id and not exc.receipt:
                exc.receipt.update(
                    {
                        "remoteId": document_id,
                        "remoteUrl": f"https://feishu.cn/docx/{document_id}",
                        "action": action,
                    }
                )
            raise
        return {
            "remoteId": document_id,
            "remoteUrl": f"https://feishu.cn/docx/{document_id}",
            "action": action,
            "blockCount": len(normalized_blocks),
            "memberPermission": "full_access",
            "organizationPermission": public_permission,
            "ownerStatus": owner_status,
        }

    def _finalize_feishu_docx_sync(
        self,
        identity: SessionIdentity,
        *,
        operation_id: str,
        claimed_remote_id: str,
        outcome: str,
        result: Mapping[str, Any],
        resolved_remote_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        result_json = canonical_json(dict(result))
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT c.command_id, c.scope_id, c.organization_id,
                           c.aggregate_type, c.aggregate_id, c.command_type,
                           c.actor_principal_id, c.idempotency_key,
                           r.provider_resource_id
                    FROM command_envelopes AS c
                    JOIN external_provider_resources AS r
                      ON r.scope_id = c.scope_id
                     AND r.organization_id = c.organization_id
                     AND r.provider = 'feishu'
                     AND r.resource_kind = 'docx_document'
                     AND r.remote_id = ?
                    WHERE c.operation_id = ? AND c.scope_id = ?
                    LIMIT 1
                    """,
                    (
                        claimed_remote_id,
                        operation_id,
                        identity.scope_id,
                    ),
                ).fetchone()
                if row is None:
                    raise RepositoryError(
                        500,
                        "feishu_docx_operation_missing",
                        "飞书文档同步操作回执不存在",
                    )
                current_result_row = connection.execute(
                    """
                    SELECT result_json
                    FROM command_idempotency
                    WHERE scope_id = ? AND actor_principal_id = ?
                      AND command_type = ? AND idempotency_key = ?
                    """,
                    (
                        row["scope_id"],
                        row["actor_principal_id"],
                        row["command_type"],
                        row["idempotency_key"],
                    ),
                ).fetchone()
                current_result = self._json_text(
                    current_result_row["result_json"]
                    if current_result_row is not None
                    else "{}"
                )
                processing_attempt_id = str(
                    current_result.get("processingAttemptId") or ""
                )
                connection.execute(
                    """
                    UPDATE operation_attempts
                    SET transport_state = ?, lease_owner = NULL,
                        lease_until = NULL, error_code = ?,
                        error_message = ?
                    WHERE command_id = ? AND scope_id = ?
                    """,
                    (
                        outcome,
                        error_code,
                        error_message,
                        row["command_id"],
                        row["scope_id"],
                    ),
                )
                if processing_attempt_id:
                    connection.execute(
                        """
                        UPDATE processing_attempts
                        SET state = ?, error_code = ?, error_message = ?,
                            started_at = COALESCE(started_at, ?),
                            finished_at = ?
                        WHERE processing_attempt_id = ?
                          AND organization_id = ?
                        """,
                        (
                            (
                                "completed"
                                if outcome == "succeeded"
                                else "failed"
                            ),
                            error_code or "",
                            error_message or "",
                            now,
                            now,
                            processing_attempt_id,
                            row["organization_id"],
                        ),
                    )
                connection.execute(
                    """
                    UPDATE delivery_outbox
                    SET status = ?, updated_at = ?
                    WHERE scope_id = ? AND operation_id = ?
                    """,
                    (
                        "delivered" if outcome == "succeeded" else "failed",
                        now,
                        row["scope_id"],
                        operation_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE external_provider_resources
                    SET remote_id = ?, retention_state = ?,
                        version = version + 1, updated_at = ?
                    WHERE provider_resource_id = ?
                    """,
                    (
                        resolved_remote_id or claimed_remote_id,
                        (
                            "active"
                            if outcome == "succeeded"
                            else "failed_retryable"
                        ),
                        now,
                        row["provider_resource_id"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO external_side_effects (
                        effect_id, scope_id, organization_id, operation_id,
                        provider_resource_id, effect_kind, outcome,
                        receipt_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?,
                              'feishu.docx_document.sync', ?, ?, ?)
                    """,
                    (
                        new_id(),
                        row["scope_id"],
                        row["organization_id"],
                        operation_id,
                        row["provider_resource_id"],
                        outcome,
                        sha256_text(result_json),
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE command_idempotency
                    SET result_json = ?, result_hash = ?
                    WHERE scope_id = ? AND actor_principal_id = ?
                      AND command_type = ? AND idempotency_key = ?
                    """,
                    (
                        result_json,
                        sha256_text(result_json),
                        row["scope_id"],
                        row["actor_principal_id"],
                        row["command_type"],
                        row["idempotency_key"],
                    ),
                )
                if outcome != "succeeded":
                    connection.execute(
                        """
                        INSERT INTO operation_dead_letters (
                            dead_letter_id, scope_id, organization_id,
                            operation_id, aggregate_type, aggregate_id,
                            error_code, error_message, status,
                            created_at, resolved_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, NULL)
                        """,
                        (
                            new_id(),
                            row["scope_id"],
                            row["organization_id"],
                            operation_id,
                            row["aggregate_type"],
                            row["aggregate_id"],
                            error_code or "feishu_docx_sync_failed",
                            error_message or "飞书文档同步失败",
                            now,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return dict(result)

    def _claim_feishu_docx_sync_lease(
        self,
        identity: SessionIdentity,
        *,
        operation_id: str,
        claim_nonce: str,
        lease_seconds: int = 90,
    ) -> dict[str, Any]:
        now = utc_now()
        lease_until = (
            datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT c.command_id, c.command_type,
                           c.actor_principal_id, c.idempotency_key,
                           c.aggregate_type, c.aggregate_id,
                           i.result_json, o.payload_json,
                           a.attempt_id, a.attempt_no,
                           a.lease_owner, a.lease_until
                    FROM command_envelopes AS c
                    JOIN command_idempotency AS i
                      ON i.scope_id = c.scope_id
                     AND i.actor_principal_id = c.actor_principal_id
                     AND i.command_type = c.command_type
                     AND i.idempotency_key = c.idempotency_key
                    JOIN delivery_outbox AS o
                      ON o.scope_id = c.scope_id
                     AND o.operation_id = c.operation_id
                    LEFT JOIN operation_attempts AS a
                      ON a.scope_id = c.scope_id
                     AND a.command_id = c.command_id
                     AND a.attempt_no = (
                        SELECT MAX(next_attempt.attempt_no)
                        FROM operation_attempts AS next_attempt
                        WHERE next_attempt.scope_id = c.scope_id
                          AND next_attempt.command_id = c.command_id
                     )
                    WHERE c.scope_id = ? AND c.organization_id = ?
                      AND c.operation_id = ?
                      AND c.command_type = 'feishu.sync.docx_document'
                    LIMIT 1
                    """,
                    (
                        identity.scope_id,
                        identity.organization_id,
                        operation_id,
                    ),
                ).fetchone()
                if row is None:
                    raise RepositoryError(
                        500,
                        "feishu_docx_operation_missing",
                        "飞书文档同步操作回执不存在",
                    )
                result = self._json_text(row["result_json"])
                safe_payload = self._json_text(row["payload_json"])
                if str(result.get("status") or "") not in {
                    "queued",
                    "syncing",
                }:
                    connection.rollback()
                    return {
                        "claimed": False,
                        "result": result,
                        "payload": safe_payload,
                        "claimedRemoteId": (
                            str(result.get("remoteId") or "")
                            or (
                                f"pending:{row['aggregate_type']}:"
                                f"{row['aggregate_id']}"
                            )
                        ),
                    }
                current_lease = str(row["lease_until"] or "")
                if (
                    row["lease_owner"]
                    and current_lease
                    and current_lease > now
                ):
                    connection.rollback()
                    return {
                        "claimed": False,
                        "result": result,
                        "payload": safe_payload,
                        "claimedRemoteId": (
                            str(result.get("remoteId") or "")
                            or (
                                f"pending:{row['aggregate_type']}:"
                                f"{row['aggregate_id']}"
                            )
                        ),
                    }
                if row["attempt_id"] is None:
                    next_attempt_no = 1
                    connection.execute(
                        """
                        INSERT INTO operation_attempts (
                            attempt_id, scope_id, command_id, attempt_no,
                            transport_state, lease_owner, lease_until,
                            permission_revalidated_at, next_retry_at,
                            error_code, error_message, created_at
                        ) VALUES (?, ?, ?, ?, 'processing', ?, ?, ?,
                                  NULL, NULL, NULL, ?)
                        """,
                        (
                            new_id(),
                            identity.scope_id,
                            row["command_id"],
                            next_attempt_no,
                            claim_nonce,
                            lease_until,
                            now,
                            now,
                        ),
                    )
                elif not row["lease_owner"]:
                    connection.execute(
                        """
                        UPDATE operation_attempts
                        SET transport_state = 'processing',
                            lease_owner = ?, lease_until = ?,
                            permission_revalidated_at = ?,
                            error_code = NULL, error_message = NULL
                        WHERE attempt_id = ? AND scope_id = ?
                        """,
                        (
                            claim_nonce,
                            lease_until,
                            now,
                            row["attempt_id"],
                            identity.scope_id,
                        ),
                    )
                else:
                    next_attempt_no = int(row["attempt_no"] or 0) + 1
                    connection.execute(
                        """
                        INSERT INTO operation_attempts (
                            attempt_id, scope_id, command_id, attempt_no,
                            transport_state, lease_owner, lease_until,
                            permission_revalidated_at, next_retry_at,
                            error_code, error_message, created_at
                        ) VALUES (?, ?, ?, ?, 'processing', ?, ?, ?,
                                  NULL, NULL, NULL, ?)
                        """,
                        (
                            new_id(),
                            identity.scope_id,
                            row["command_id"],
                            next_attempt_no,
                            claim_nonce,
                            lease_until,
                            now,
                            now,
                        ),
                    )
                result = {
                    **result,
                    "claimNonce": claim_nonce,
                    "state": "processing",
                    "status": "syncing",
                    "updatedAt": now,
                    "details": {
                        **dict(result.get("details") or {}),
                        "state": "processing",
                        "leaseUntil": lease_until,
                    },
                }
                result_json = canonical_json(result)
                connection.execute(
                    """
                    UPDATE command_idempotency
                    SET result_json = ?, result_hash = ?
                    WHERE scope_id = ? AND actor_principal_id = ?
                      AND command_type = ? AND idempotency_key = ?
                    """,
                    (
                        result_json,
                        sha256_text(result_json),
                        identity.scope_id,
                        row["actor_principal_id"],
                        row["command_type"],
                        row["idempotency_key"],
                    ),
                )
                processing_attempt_id = str(
                    result.get("processingAttemptId") or ""
                )
                if processing_attempt_id:
                    connection.execute(
                        """
                        UPDATE processing_attempts
                        SET state = 'processing',
                            started_at = COALESCE(started_at, ?),
                            finished_at = NULL, error_code = '',
                            error_message = ''
                        WHERE processing_attempt_id = ?
                          AND organization_id = ?
                        """,
                        (
                            now,
                            processing_attempt_id,
                            identity.organization_id,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "claimed": True,
            "result": result,
            "payload": safe_payload,
            "claimedRemoteId": (
                str(result.get("remoteId") or "")
                or f"pending:{row['aggregate_type']}:{row['aggregate_id']}"
            ),
        }

    def _latest_feishu_sync_receipt(
        self,
        identity: SessionIdentity,
        *,
        local_type: str,
        local_id: str,
        remote_type: str,
    ) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT i.result_json
                FROM command_envelopes AS c
                JOIN command_idempotency AS i
                  ON i.scope_id = c.scope_id
                 AND i.actor_principal_id = c.actor_principal_id
                 AND i.command_type = c.command_type
                 AND i.idempotency_key = c.idempotency_key
                WHERE c.scope_id = ? AND c.organization_id = ?
                  AND c.aggregate_type = ? AND c.aggregate_id = ?
                  AND c.command_type = ?
                ORDER BY c.created_at DESC, c.command_id DESC
                LIMIT 1
                """,
                (
                    identity.scope_id,
                    identity.organization_id,
                    local_type,
                    local_id,
                    f"feishu.sync.{remote_type}",
                ),
            ).fetchone()
        if row is None and remote_type == "docx_document":
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT i.result_json
                    FROM command_envelopes AS c
                    JOIN command_idempotency AS i
                      ON i.scope_id = c.scope_id
                     AND i.actor_principal_id = c.actor_principal_id
                     AND i.command_type = c.command_type
                     AND i.idempotency_key = c.idempotency_key
                    WHERE c.scope_id = ? AND c.organization_id = ?
                      AND c.aggregate_type = ? AND c.aggregate_id = ?
                      AND c.command_type =
                          'feishu.import.mapping.registered'
                    ORDER BY c.created_at DESC, c.command_id DESC
                    LIMIT 1
                    """,
                    (
                        identity.scope_id,
                        identity.organization_id,
                        local_type,
                        local_id,
                    ),
                ).fetchone()
        if row is None:
            return None
        result = self._json_text(row["result_json"])
        return result or None

    def _feishu_integration_readiness(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        configuration = self._feishu_configuration(identity)
        app_id = str(configuration.get("appId") or "")
        has_credentials = bool(configuration.get("hasCredentials"))
        validation_state = str(
            configuration.get("lastValidationStatus") or ""
        )
        ready = bool(app_id and has_credentials and validation_state == "succeeded")
        if not app_id or not has_credentials:
            state = "not_connected"
            blocked_reason = (
                "feishu_not_configured"
                if not app_id
                else "feishu_credentials_missing"
            )
        elif ready:
            state = "ready"
            blocked_reason = None
        else:
            state = "failed_retryable"
            blocked_reason = (
                str(configuration.get("lastValidationErrorCode") or "")
                or "feishu_validation_required"
            )
        validation_message = (
            str(configuration.get("lastValidationMessage") or "")
            or (
                "尚未配置飞书应用"
                if not app_id
                else (
                    "飞书应用缺少已加密凭据"
                    if not has_credentials
                    else "飞书应用已加密保存，等待凭据验证"
                )
            )
        )
        if ready:
            validation_message = "飞书应用凭据验证成功"
        return {
            "configuration": configuration,
            "appId": app_id,
            "hasCredentials": has_credentials,
            "lastValidationStatus": validation_state or "idle",
            "lastValidationMessage": validation_message,
            "authorizationReady": ready,
            "authorizationBlockedReason": blocked_reason,
            "state": state,
            "retryable": not ready,
        }

    def feishu_integration(self, identity: SessionIdentity) -> dict[str, Any]:
        readiness = self._feishu_integration_readiness(identity)
        configuration = readiness["configuration"]
        app_id = str(readiness["appId"])
        has_credentials = bool(readiness["hasCredentials"])
        validation_state = str(readiness["lastValidationStatus"])
        validation_message = str(readiness["lastValidationMessage"])
        ready = bool(readiness["authorizationReady"])
        state = str(readiness["state"])
        blocked_reason = readiness["authorizationBlockedReason"]
        effective_callback_url = self._feishu_oauth_relay_callback_url()
        with self._connection() as connection:
            organization = connection.execute(
                "SELECT name FROM organizations WHERE id=? AND lifecycle_state!='deleted'",
                (identity.organization_id,),
            ).fetchone()
        return {
            "organizationId": identity.organization_id,
            "organizationName": str(organization["name"] if organization else ""),
            "appId": app_id,
            "callbackMode": str(
                configuration.get("callbackMode") or "cloud_relay"
            ),
            "customCallbackUrl": str(
                configuration.get("customCallbackUrl") or ""
            ),
            "effectiveCallbackUrl": effective_callback_url,
            "enabled": ready,
            "hasAppSecret": has_credentials,
            "configuredBy": configuration.get("effectiveScopeKind"),
            "effectiveScopeKind": configuration.get("effectiveScopeKind"),
            "defaultWriteScope": configuration.get("defaultWriteScope"),
            "scopeVersions": configuration.get("scopeVersions") or {},
            "configuredAt": configuration.get("updatedAt") or None,
            "updatedAt": configuration.get("updatedAt") or utc_now(),
            "version": int(configuration.get("version") or 0),
            "expectedVersion": int(configuration.get("version") or 0),
            "lastValidationStatus": validation_state or "idle",
            "lastValidationMessage": validation_message,
            "authorizationReady": ready,
            "authorizationBlockedReason": blocked_reason,
            "state": state,
            "retryable": not ready,
            "recentAudits": [],
            "botName": str(configuration.get("botName") or "飞书机器人"),
            "tenantName": str(configuration.get("tenantName") or ""),
            "sharedBotLabel": str(configuration.get("sharedBotLabel") or ""),
        }

    def save_feishu(
        self,
        identity: SessionIdentity,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not identity.is_admin:
            raise RepositoryError(403, "admin_required", "只有组织管理员可以配置飞书机器人")
        app_id = str(payload.get("appId") or "").strip()
        if not app_id:
            raise RepositoryError(422, "feishu_app_id_required", "请填写飞书 App ID")
        app_secret = str(payload.get("appSecret") or "")
        effective = self._feishu_configuration(identity)
        scope_kind = "organization"
        current = self._feishu_configuration_for_scope(
            identity,
            scope_kind=scope_kind,
        )
        current_app_id = str(current.get("appId") or "")
        callback_mode = "external_shared_bot"
        custom_callback_url = ""
        if not app_secret and (
            not current.get("hasCredentials") or current_app_id != app_id
        ):
            raise RepositoryError(
                422,
                "feishu_app_secret_required",
                "首次配置或更换飞书 App ID 时必须填写 App Secret",
            )
        secret_fingerprint = (
            sha256_text(canonical_json({"appSecret": app_secret}))[:16]
            if app_secret
            else str(current.get("secretFingerprint") or "")
        )
        safe_validation_payload = {
            "appId": app_id,
            "scopeKind": scope_kind,
            "callbackMode": callback_mode,
            "customCallbackUrl": custom_callback_url,
            "hasCredentials": True,
            "configurationVersion": int(current.get("version") or 0),
            "secretFingerprint": secret_fingerprint,
        }
        configuration_unchanged = bool(
            current_app_id == app_id
            and str(current.get("callbackMode") or "cloud_relay")
            == callback_mode
            and str(current.get("customCallbackUrl") or "")
            == custom_callback_url
            and str(current.get("secretFingerprint") or "")
            == secret_fingerprint
        )
        if configuration_unchanged:
            saved = current
        else:
            if "expectedVersion" in payload:
                expected_version = int(payload.get("expectedVersion") or 0)
            else:
                expected_version = int(
                    (current.get("scopeVersions") or {}).get(scope_kind, 0)
                    or current.get("version")
                    or 0
                )
            saved = self.configurations.upsert(
                identity,
                configuration_kind=FEISHU_CONFIGURATION_KIND,
                scope_kind=scope_kind,
                provider="feishu",
                public_config={
                    "appId": app_id,
                    "callbackMode": callback_mode,
                    "customCallbackUrl": custom_callback_url,
                },
                expected_version=expected_version,
                idempotency_key=f"{idempotency_key}:configuration",
                secret_bundle={"appSecret": app_secret} if app_secret else None,
                secret_action="replace" if app_secret else "preserve",
            )
        final_validation_payload = {
            **safe_validation_payload,
            "hasCredentials": bool(saved.get("hasCredentials")),
            "configurationVersion": int(saved.get("version") or 0),
        }
        validation_key_hash = sha256_text(idempotency_key)
        if (
            str(saved.get("lastValidationIdempotencyHash") or "")
            == validation_key_hash
            and saved.get("lastValidationStatus")
        ):
            return self.feishu_integration(identity)
        secret_bundle = self._feishu_secret_for_scope(
            identity,
            scope_kind=scope_kind,
        )
        stored_secret = str((secret_bundle or {}).get("appSecret") or "")
        if not stored_secret:
            raise RepositoryError(
                500,
                "feishu_encrypted_secret_missing",
                "飞书配置未找到已加密凭据，未执行外部验证",
            )
        verified, error_code, message = self._verify_feishu_tenant_token(
            app_id=app_id,
            app_secret=stored_secret,
        )
        del final_validation_payload
        self.configurations.upsert(
            identity,
            configuration_kind=FEISHU_CONFIGURATION_KIND,
            scope_kind=scope_kind,
            provider="feishu",
            public_config={
                "appId": app_id,
                "callbackMode": callback_mode,
                "customCallbackUrl": custom_callback_url,
                "lastValidationStatus": (
                    "succeeded" if verified else "failed_retryable"
                ),
                "lastValidationMessage": message,
                "lastValidationErrorCode": error_code,
                "lastValidationIdempotencyHash": validation_key_hash,
            },
            expected_version=int(saved.get("version") or 0),
            idempotency_key=f"{idempotency_key}:validation",
            secret_action="preserve",
        )
        return self.feishu_integration(identity)

    def feishu_sync_status(
        self,
        identity: SessionIdentity,
        *,
        local_type: str,
        local_id: str,
        remote_type: str,
    ) -> dict[str, Any]:
        integration = self.feishu_integration(identity)
        record_identity = (
            self._personal_identity(identity)
            if remote_type == "docx_document"
            else identity
        )
        if integration["state"] == "not_connected":
            return {
                "localType": local_type,
                "localId": local_id,
                "remoteType": remote_type,
                "remoteId": None,
                "remoteUrl": None,
                "status": "not_configured",
                "message": integration["lastValidationMessage"],
                "lastSyncedAt": None,
                "updatedAt": integration["updatedAt"],
                "details": {
                    "state": "not_connected",
                    "retryable": True,
                    "pollingEnabled": False,
                    "blockerType": "configuration_missing",
                    "errorCode": integration["authorizationBlockedReason"],
                    "processingAttemptId": None,
                },
            }
        if integration["state"] == "failed_retryable":
            return {
                "localType": local_type,
                "localId": local_id,
                "remoteType": remote_type,
                "remoteId": None,
                "remoteUrl": None,
                "status": "failed_retryable",
                "message": integration["lastValidationMessage"],
                "lastSyncedAt": None,
                "updatedAt": integration["updatedAt"],
                "details": {
                    "state": "failed_retryable",
                    "retryable": True,
                    "pollingEnabled": False,
                    "blockerType": "provider_validation_failed",
                    "errorCode": integration["authorizationBlockedReason"],
                    "processingAttemptId": None,
                },
            }
        command_type = f"feishu.sync.{remote_type}"
        latest = self.operations.latest_result(
            record_identity,
            command_type=command_type,
            aggregate_id=local_id,
        )
        if latest is not None:
            return latest
        return {
            "localType": local_type,
            "localId": local_id,
            "remoteType": remote_type,
            "remoteId": None,
            "remoteUrl": None,
            "status": "not_synced",
            "message": "尚未同步到飞书",
            "lastSyncedAt": None,
            "updatedAt": integration["updatedAt"],
            "details": {
                "state": "ready",
                "retryable": True,
                "pollingEnabled": False,
                "blockerType": None,
                "errorCode": None,
                "processingAttemptId": None,
            },
        }

    def request_feishu_sync(
        self,
        identity: SessionIdentity,
        *,
        local_type: str,
        local_id: str,
        remote_type: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_local_id = str(local_id or "").strip()
        if not normalized_local_id:
            raise RepositoryError(422, "feishu_sync_local_id_required", "缺少待同步对象标识")
        is_document_sync = (
            local_type == "document" and remote_type == "docx_document"
        )
        operation_identity = (
            self._personal_identity(identity) if is_document_sync else identity
        )
        operation_owner_kind = "membership" if is_document_sync else "organization"
        command_type = f"feishu.sync.{remote_type}"
        safe_request: dict[str, Any] = {
            "localType": local_type,
            "localId": normalized_local_id,
            "remoteType": remote_type,
            "notify": bool(payload.get("notify")),
        }
        title = ""
        content = ""
        task: Mapping[str, Any] | None = None
        if is_document_sync:
            title = re.sub(r"\s+", " ", str(payload.get("title") or "")).strip()
            title = title or "益语同步文档"
            content = str(payload.get("content") or "").replace("\r\n", "\n").strip()
            safe_request.update(
                {
                    "titleHash": sha256_text(title),
                    "contentHash": sha256_text(content),
                    "byteSize": len(content.encode("utf-8")),
                    "clientId": str(payload.get("clientId") or "") or None,
                    "triggerSource": str(payload.get("triggerSource") or "document_saved"),
                }
            )
        elif local_type == "task" and remote_type == "calendar_event":
            task = GC04TaskRepository(self.repository).task_detail(
                identity,
                task_id=normalized_local_id,
            )["task"]
            safe_request.update(
                {
                    "taskVersion": int(task.get("version") or 1),
                    "taskTitleHash": sha256_text(str(task.get("title") or "")),
                }
            )
        else:
            raise RepositoryError(
                422,
                "feishu_sync_kind_invalid",
                "该对象类型不支持同步到飞书",
            )

        with self._feishu_execution_lock:
            replay = self.operations.replay(
                operation_identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
                payload=safe_request,
            )
            if replay is not None:
                return replay
            integration = self.feishu_integration(identity)
            if integration["state"] != "ready":
                not_configured = integration["state"] == "not_connected"
                return self._record_command(
                    operation_identity,
                    command_type=command_type,
                    aggregate_type=local_type,
                    aggregate_id=normalized_local_id,
                    payload=safe_request,
                    idempotency_key=idempotency_key,
                    provider="feishu",
                    resource_kind=remote_type,
                    remote_id=f"blocked:{local_type}:{normalized_local_id}",
                    outcome="blocked" if not_configured else "failed_retryable",
                    error_code=(
                        "feishu_configuration_missing"
                        if not_configured
                        else "feishu_provider_validation_failed"
                    ),
                    error_message=str(
                        integration.get("lastValidationMessage")
                        or "组织飞书应用当前不可用"
                    ),
                    result_details={
                        "localType": local_type,
                        "localId": normalized_local_id,
                        "remoteType": remote_type,
                        "status": "blocked" if not_configured else "failed_retryable",
                        "pollingEnabled": False,
                    },
                    owner_kind=operation_owner_kind,
                )
            previous = self.operations.latest_result(
                operation_identity,
                command_type=command_type,
                aggregate_id=normalized_local_id,
            ) or {}
            previous_remote_id = str(previous.get("remoteId") or "") or None
            try:
                configuration = self._feishu_configuration(identity)
                if is_document_sync:
                    member = self._feishu_member_configuration(identity)
                    member_open_id = str(member.get("openId") or "")
                    if not member.get("linked") or not member_open_id:
                        raise _FeishuExecutionError(
                            "feishu_member_authorization_required",
                            "请先完成当前成员的飞书授权",
                        )
                    provider_receipt = self._execute_feishu_docx_sync(
                        identity,
                        configuration=configuration,
                        title=title,
                        content=content,
                        member_open_id=member_open_id,
                        remote_id=previous_remote_id,
                        provider_idempotency_key=sha256_text(idempotency_key)[:32],
                    )
                else:
                    assert task is not None
                    event_payload = self._feishu_task_event_payload(
                        task,
                        notify=bool(payload.get("notify")),
                    )
                    provider_receipt = self._execute_feishu_calendar_event(
                        identity,
                        configuration=configuration,
                        event_payload=event_payload,
                        provider_idempotency_key=sha256_text(idempotency_key)[:32],
                        remote_id=previous_remote_id,
                        calendar_id=str(previous.get("calendarId") or "") or None,
                    )
            except _FeishuExecutionError as exc:
                blocked = exc.code in {
                    "feishu_member_authorization_required",
                    "feishu_configuration_missing",
                }
                return self._record_command(
                    operation_identity,
                    command_type=command_type,
                    aggregate_type=local_type,
                    aggregate_id=normalized_local_id,
                    payload=safe_request,
                    idempotency_key=idempotency_key,
                    provider="feishu",
                    resource_kind=remote_type,
                    remote_id=previous_remote_id or f"failed:{normalized_local_id}",
                    outcome="blocked" if blocked else "failed_retryable",
                    error_code=exc.code,
                    error_message=exc.message,
                    result_details={
                        "localType": local_type,
                        "localId": normalized_local_id,
                        "remoteType": remote_type,
                        "remoteId": previous_remote_id,
                        "status": "blocked" if blocked else "failed_retryable",
                        "pollingEnabled": False,
                    },
                    owner_kind=operation_owner_kind,
                )
            synced_at = utc_now()
            remote_id = str(provider_receipt.get("remoteId") or "")
            result = self._record_command(
                operation_identity,
                command_type=command_type,
                aggregate_type=local_type,
                aggregate_id=normalized_local_id,
                payload=safe_request,
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind=remote_type,
                remote_id=remote_id,
                outcome="succeeded",
                retention_state="active",
                result_details={
                    "localType": local_type,
                    "localId": normalized_local_id,
                    "remoteType": remote_type,
                    "remoteId": remote_id,
                    "remoteUrl": provider_receipt.get("remoteUrl"),
                    "calendarId": provider_receipt.get("calendarId"),
                    "status": "synced",
                    "state": "ready",
                    "message": (
                        "文档已同步到当前成员飞书"
                        if is_document_sync
                        else "任务已同步到飞书日历"
                    ),
                    "lastSyncedAt": synced_at,
                    "updatedAt": synced_at,
                    "retryable": False,
                    "pollingEnabled": False,
                },
                owner_kind=operation_owner_kind,
            )
            self._record_feishu_mapping(
                operation_identity,
                result=result,
                local_type=local_type,
                local_id=normalized_local_id,
                remote_type=remote_type,
                remote_id=remote_id,
                remote_receipt={
                    "remoteUrl": provider_receipt.get("remoteUrl"),
                    "calendarId": provider_receipt.get("calendarId"),
                },
                bound_membership_id=(identity.membership_id if is_document_sync else None),
            )
            return result

    def _record_feishu_mapping(
        self,
        identity: SessionIdentity,
        *,
        result: Mapping[str, Any],
        local_type: str,
        local_id: str,
        remote_type: str,
        remote_id: str,
        remote_receipt: Mapping[str, Any],
        bound_membership_id: str | None,
    ) -> None:
        operation_id = str(result.get("operationId") or "")
        if not operation_id or not remote_id:
            return
        now = utc_now()
        mapping_id = "feishu_map_" + sha256_text(
            f"{identity.scope_id}\x1f{local_type}\x1f{local_id}\x1f{remote_type}"
        )[:24]
        safe_receipt = canonical_json(
            {
                key: value
                for key, value in remote_receipt.items()
                if value not in {None, ""}
            }
        )
        with self._connection() as connection:
            side_effect = connection.execute(
                "SELECT id FROM external_side_effects WHERE scope_id=? AND operation_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (identity.scope_id, operation_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO feishu_mappings (
                    id,scope_id,external_side_effect_id,remote_id,remote_receipt,
                    status,mapping_kind,local_resource_id,bound_membership_id,
                    created_at,revoked_at,version,lifecycle_state,updated_at,deleted_at
                ) VALUES (?,?,?,?,?,'active',?,?,?,?,NULL,1,'active',?,NULL)
                ON CONFLICT(id) DO UPDATE SET
                    external_side_effect_id=excluded.external_side_effect_id,
                    remote_id=excluded.remote_id,remote_receipt=excluded.remote_receipt,
                    status='active',bound_membership_id=excluded.bound_membership_id,
                    revoked_at=NULL,version=feishu_mappings.version+1,
                    lifecycle_state='active',updated_at=excluded.updated_at,deleted_at=NULL
                """,
                (
                    mapping_id,
                    identity.scope_id,
                    str(side_effect["id"]) if side_effect is not None else None,
                    remote_id,
                    safe_receipt,
                    remote_type,
                    local_id,
                    bound_membership_id,
                    now,
                    now,
                ),
            )
            saga_id = "saga_" + sha256_text(
                f"{identity.scope_id}\x1f{operation_id}\x1ffeishu_mapping"
            )[:26]
            connection.execute(
                "INSERT INTO saga_operations "
                "(id,scope_id,operation_id,current_step,outcome,reconciliation_state,"
                "orchestrator_instance_id,compensation_state,started_at,settled_at,version,"
                "lifecycle_state,created_at,updated_at,deleted_at) "
                "VALUES (?,?,?,'mapping_recorded','succeeded','settled',?,'not_required',"
                "?,?,1,'active',?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
                "current_step='mapping_recorded',outcome='succeeded',"
                "reconciliation_state='settled',settled_at=excluded.settled_at,"
                "version=saga_operations.version+1,updated_at=excluded.updated_at",
                (
                    saga_id,
                    identity.scope_id,
                    operation_id,
                    identity.cloud_instance_id,
                    now,
                    now,
                    now,
                    now,
                ),
            )
            connection.commit()
        return

        # Frozen executor retained below for archaeology only.
        is_document_sync = (
            local_type == "document" and remote_type == "docx_document"
        )
        operation_identity = (
            self._personal_identity(identity)
            if is_document_sync
            else identity
        )
        integration = self.feishu_integration(identity)
        not_configured = integration["state"] == "not_connected"
        validation_failed = integration["state"] == "failed_retryable"
        command_type = f"feishu.sync.{remote_type}"
        pending_remote_id = f"pending:{local_type}:{local_id}"
        safe_request = {
            "localType": local_type,
            "localId": local_id,
            "remoteType": remote_type,
            "notify": bool(payload.get("notify")),
        }
        if is_document_sync:
            normalized_title = re.sub(
                r"\s+",
                " ",
                str(payload.get("title") or ""),
            ).strip() or "益语同步文档"
            normalized_content = str(payload.get("content") or "").replace(
                "\r\n",
                "\n",
            ).strip()
            content_bytes = normalized_content.encode("utf-8")
            safe_request = {
                **safe_request,
                "titleHash": sha256_text(normalized_title),
                "contentHash": sha256_text(normalized_content),
                "byteSize": len(content_bytes),
                "clientId": str(payload.get("clientId") or "") or None,
                "triggerSource": str(
                    payload.get("triggerSource") or "document_saved"
                ),
                "notify": bool(payload.get("notifyOnCreate")),
            }

        def record_preflight(
            *,
            outcome: str,
            status: str,
            state: str,
            error_code: str,
            message: str,
            blocker_type: str,
            processing: bool = False,
        ) -> dict[str, Any]:
            now = utc_now()
            return self._record_command(
                operation_identity,
                command_type=command_type,
                aggregate_type=local_type,
                aggregate_id=local_id,
                payload=safe_request,
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind=remote_type,
                remote_id=pending_remote_id,
                outcome=outcome,
                error_code=error_code,
                error_message=message,
                processing_kind=command_type if processing else None,
                processing_state="failed" if processing else None,
                result_details={
                    "localType": local_type,
                    "localId": local_id,
                    "remoteType": remote_type,
                    "remoteId": None,
                    "remoteUrl": None,
                    "status": status,
                    "message": message,
                    "lastSyncedAt": None,
                    "updatedAt": now,
                    "details": {
                        "state": state,
                        "retryable": True,
                        "pollingEnabled": False,
                        "blockerType": blocker_type,
                        "errorCode": error_code,
                    },
                },
            )

        if not_configured:
            return record_preflight(
                outcome="blocked",
                status="not_configured",
                state="not_connected",
                error_code="feishu_configuration_missing",
                message="尚未配置组织飞书应用；未发送外部请求，配置后可重试",
                blocker_type="configuration_missing",
            )
        if validation_failed:
            return record_preflight(
                outcome="failed_retryable",
                status="failed_retryable",
                state="failed_retryable",
                error_code="feishu_provider_validation_failed",
                message="组织飞书应用验证尚未通过；未发送同步请求，修复配置后可重试",
                blocker_type="provider_validation_failed",
            )
        if is_document_sync:
            if not local_id:
                raise RepositoryError(
                    422,
                    "feishu_document_local_id_required",
                    "缺少本机文档标识，无法创建飞书文档",
                )
            if not normalized_content:
                return record_preflight(
                    outcome="blocked",
                    status="skipped",
                    state="blocked",
                    error_code="local_document_empty",
                    message="文档正文为空，未向飞书发送创建请求",
                    blocker_type="local_document_empty",
                )
            if len(content_bytes) > 120 * 1024:
                return record_preflight(
                    outcome="blocked",
                    status="blocked",
                    state="blocked",
                    error_code="feishu_document_too_large",
                    message="文档正文超过 120 KiB，请精简后重试",
                    blocker_type="document_too_large",
                )
            authorization = self.personal_feishu_authorization(identity)
            member_open_id = str(authorization.get("openId") or "")
            if not authorization.get("linked") or not member_open_id:
                return record_preflight(
                    outcome="blocked",
                    status="blocked",
                    state="blocked",
                    error_code="feishu_member_authorization_required",
                    message="请先完成当前成员飞书授权，再创建飞书文档",
                    blocker_type="member_authorization_required",
                )
            existing = self._existing_command_record(
                operation_identity,
                command_type=command_type,
                idempotency_key=idempotency_key,
            )
            claimed: dict[str, Any] | None = None
            claimed_remote_id = ""
            prior: dict[str, Any] | None = None
            if existing is not None:
                if canonical_json(existing[1]) != canonical_json(safe_request):
                    raise RepositoryError(
                        409,
                        "idempotency_payload_conflict",
                        "同一幂等键不能同步不同的文档正文",
                    )
                if str(existing[0].get("status") or "") not in {
                    "queued",
                    "syncing",
                }:
                    return existing[0]
                claim_nonce = secrets.token_urlsafe(12)
                lease = self._claim_feishu_docx_sync_lease(
                    operation_identity,
                    operation_id=str(existing[0].get("operationId") or ""),
                    claim_nonce=claim_nonce,
                )
                if not lease["claimed"]:
                    return dict(lease["result"])
                claimed = dict(lease["result"])
                claimed_remote_id = str(lease["claimedRemoteId"])
                prior = claimed
            else:
                prior = self._latest_feishu_sync_receipt(
                    operation_identity,
                    local_type=local_type,
                    local_id=local_id,
                    remote_type=remote_type,
                )
                if (
                    prior is not None
                    and str(prior.get("status") or "")
                    in {"queued", "syncing"}
                ):
                    recovery_nonce = secrets.token_urlsafe(12)
                    recovery = self._claim_feishu_docx_sync_lease(
                        operation_identity,
                        operation_id=str(prior.get("operationId") or ""),
                        claim_nonce=recovery_nonce,
                    )
                    if not recovery["claimed"]:
                        return dict(recovery["result"])
                    stale = dict(recovery["result"])
                    stale_remote_id = str(
                        stale.get("remoteId") or ""
                    )
                    recovered_at = utc_now()
                    stale_failed = {
                        **stale,
                        "claimNonce": None,
                        "state": "failed_retryable",
                        "status": "failed_retryable",
                        "errorCode": "feishu_docx_lease_expired",
                        "message": (
                            "上一次飞书文档同步租期已过期；"
                            "本次将使用确定性请求标识安全重试"
                        ),
                        "retryable": True,
                        "updatedAt": recovered_at,
                        "details": {
                            **dict(stale.get("details") or {}),
                            "state": "failed_retryable",
                            "retryable": True,
                            "blockerType": "expired_external_attempt",
                            "errorCode": "feishu_docx_lease_expired",
                            "leaseUntil": None,
                        },
                    }
                    prior = self._finalize_feishu_docx_sync(
                        operation_identity,
                        operation_id=str(stale["operationId"]),
                        claimed_remote_id=str(
                            recovery["claimedRemoteId"]
                        ),
                        outcome="failed_retryable",
                        result=stale_failed,
                        resolved_remote_id=stale_remote_id or None,
                        error_code="feishu_docx_lease_expired",
                        error_message=stale_failed["message"],
                    )
            prior_remote_id = (
                str(prior.get("remoteId") or "")
                if prior is not None
                and (
                    claimed is not None
                    or str(prior.get("status") or "")
                    in {"synced", "failed_retryable", "failed"}
                )
                else ""
            )
            if claimed is None:
                claimed_remote_id = prior_remote_id or pending_remote_id
                claim_nonce = secrets.token_urlsafe(12)
                queued = self._record_command(
                    operation_identity,
                    command_type=command_type,
                    aggregate_type=local_type,
                    aggregate_id=local_id,
                    payload=safe_request,
                    idempotency_key=idempotency_key,
                    provider="feishu",
                    resource_kind=remote_type,
                    remote_id=claimed_remote_id,
                    outcome="queued",
                    retention_state="syncing",
                    processing_kind=command_type,
                    processing_state="queued",
                    result_details={
                        "claimNonce": None,
                        "localType": local_type,
                        "localId": local_id,
                        "remoteType": remote_type,
                        "remoteId": prior_remote_id or None,
                        "remoteUrl": (
                            prior.get("remoteUrl")
                            if prior is not None
                            else None
                        ),
                        "status": "syncing",
                        "message": "正在创建或更新飞书文档",
                        "lastSyncedAt": (
                            prior.get("lastSyncedAt")
                            if prior is not None
                            else None
                        ),
                        "updatedAt": utc_now(),
                        "details": {
                            "state": "processing",
                            "retryable": False,
                            "pollingEnabled": False,
                            "blockerType": None,
                            "errorCode": None,
                        },
                    },
                )
                lease = self._claim_feishu_docx_sync_lease(
                    operation_identity,
                    operation_id=str(queued.get("operationId") or ""),
                    claim_nonce=claim_nonce,
                )
                if not lease["claimed"]:
                    return dict(lease["result"])
                claimed = dict(lease["result"])
                claimed_remote_id = str(lease["claimedRemoteId"])
            try:
                provider_receipt = self._execute_feishu_docx_sync(
                    identity,
                    configuration=self._feishu_configuration(identity),
                    title=normalized_title,
                    content=normalized_content,
                    member_open_id=member_open_id,
                    remote_id=prior_remote_id or None,
                    provider_idempotency_key=sha256_text(
                        canonical_json(
                            {
                                "scopeId": operation_identity.scope_id,
                                "localId": local_id,
                                "remoteType": remote_type,
                            }
                        )
                    ),
                )
            except _FeishuExecutionError as exc:
                failed_remote_id = str(exc.receipt.get("remoteId") or "")
                failed_remote_url = str(exc.receipt.get("remoteUrl") or "")
                failed = {
                    "operationId": claimed["operationId"],
                    "processingAttemptId": claimed.get(
                        "processingAttemptId"
                    ),
                    "state": "failed_retryable",
                    "status": "failed_retryable",
                    "errorCode": exc.code,
                    "message": exc.message,
                    "retryable": True,
                    "localType": local_type,
                    "localId": local_id,
                    "remoteType": remote_type,
                    "remoteId": failed_remote_id or prior_remote_id or None,
                    "remoteUrl": (
                        failed_remote_url
                        or (
                            str(prior.get("remoteUrl") or "")
                            if prior is not None
                            else ""
                        )
                        or None
                    ),
                    "lastSyncedAt": (
                        prior.get("lastSyncedAt")
                        if prior is not None
                        else None
                    ),
                    "updatedAt": utc_now(),
                    "details": {
                        "state": "failed_retryable",
                        "retryable": True,
                        "pollingEnabled": False,
                        "blockerType": "provider_execution_failed",
                        "errorCode": exc.code,
                    },
                }
                return self._finalize_feishu_docx_sync(
                    operation_identity,
                    operation_id=str(claimed["operationId"]),
                    claimed_remote_id=claimed_remote_id,
                    outcome="failed_retryable",
                    result=failed,
                    resolved_remote_id=failed_remote_id or prior_remote_id,
                    error_code=exc.code,
                    error_message=exc.message,
                )
            synced_at = utc_now()
            remote_id = str(provider_receipt["remoteId"])
            succeeded = {
                "operationId": claimed["operationId"],
                "processingAttemptId": claimed.get("processingAttemptId"),
                "state": "succeeded",
                "status": "synced",
                "errorCode": None,
                "message": "已同步到飞书文档",
                "retryable": False,
                "localType": local_type,
                "localId": local_id,
                "remoteType": remote_type,
                "remoteId": remote_id,
                "remoteUrl": provider_receipt["remoteUrl"],
                "lastSyncedAt": synced_at,
                "updatedAt": synced_at,
                "details": {
                    "state": "ready",
                    "retryable": False,
                    "pollingEnabled": False,
                    "blockerType": None,
                    "errorCode": None,
                    "action": provider_receipt["action"],
                    "blockCount": provider_receipt["blockCount"],
                    "memberPermission": provider_receipt[
                        "memberPermission"
                    ],
                    "organizationPermission": provider_receipt[
                        "organizationPermission"
                    ],
                    "ownerStatus": provider_receipt["ownerStatus"],
                },
            }
            return self._finalize_feishu_docx_sync(
                operation_identity,
                operation_id=str(claimed["operationId"]),
                claimed_remote_id=claimed_remote_id,
                outcome="succeeded",
                result=succeeded,
                resolved_remote_id=remote_id,
            )
        if local_type != "task" or remote_type != "calendar_event":
            return record_preflight(
                outcome="failed_retryable",
                status="failed_retryable",
                state="failed_retryable",
                error_code="feishu_sync_executor_not_connected",
                message="该飞书对象尚无安全可验证的同步执行器；操作已登记",
                blocker_type="executor_missing",
                processing=True,
            )

        task = GC04TaskRepository(self.repository).task_detail(
            identity,
            task_id=local_id,
        )["task"]
        safe_request = {
            **safe_request,
            "taskVersion": int(task.get("version") or 0),
            "titleHash": sha256_text(str(task.get("title") or "")),
            "descriptionHash": sha256_text(str(task.get("description") or "")),
            "scheduleHash": sha256_text(
                canonical_json(
                    {
                        "startDate": task.get("startDate"),
                        "dueDate": task.get("dueDate"),
                        "scheduledStartAt": task.get("scheduledStartAt"),
                        "scheduledEndAt": task.get("scheduledEndAt"),
                        "deadlineAt": task.get("deadlineAt"),
                        "durationMinutes": task.get("durationMinutes"),
                    }
                )
            ),
        }
        existing = self._existing_command_record(
            identity,
            command_type=command_type,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            if canonical_json(existing[1]) != canonical_json(safe_request):
                raise RepositoryError(
                    409,
                    "idempotency_payload_conflict",
                    "同一幂等键不能同步不同的任务版本",
                )
            return existing[0]

        prior = self._latest_feishu_sync_receipt(
            identity,
            local_type=local_type,
            local_id=local_id,
            remote_type=remote_type,
        )
        prior_remote_id = (
            str(prior.get("remoteId") or "")
            if prior is not None and prior.get("status") == "synced"
            else ""
        )
        prior_details = (
            prior.get("details")
            if prior is not None and isinstance(prior.get("details"), Mapping)
            else {}
        )
        prior_calendar_id = (
            str(prior_details.get("calendarId") or "") if prior_details else ""
        )
        try:
            event_payload = self._feishu_task_event_payload(
                task,
                notify=bool(payload.get("notify")),
            )
            provider_receipt = self._execute_feishu_calendar_event(
                identity,
                configuration=self._feishu_configuration(identity),
                event_payload=event_payload,
                provider_idempotency_key=sha256_text(
                    canonical_json(
                        {
                            "scopeId": identity.scope_id,
                            "localType": local_type,
                            "localId": local_id,
                            "remoteType": remote_type,
                        }
                    )
                ),
                remote_id=prior_remote_id or None,
                calendar_id=prior_calendar_id or None,
            )
        except _FeishuExecutionError as exc:
            if exc.code in {
                "feishu_task_timezone_missing",
                "feishu_task_time_invalid",
                "feishu_task_time_missing",
            }:
                return record_preflight(
                    outcome="blocked",
                    status="time_invalid",
                    state="blocked",
                    error_code=exc.code,
                    message=exc.message,
                    blocker_type="task_time_invalid",
                )
            now = utc_now()
            return self._record_command(
                identity,
                command_type=command_type,
                aggregate_type=local_type,
                aggregate_id=local_id,
                payload=safe_request,
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind=remote_type,
                remote_id=prior_remote_id or pending_remote_id,
                outcome="failed_retryable",
                retention_state=(
                    "active" if prior_remote_id else "failed_retryable"
                ),
                error_code=exc.code,
                error_message=exc.message,
                processing_kind=command_type,
                processing_state="failed",
                result_details={
                    "localType": local_type,
                    "localId": local_id,
                    "remoteType": remote_type,
                    "remoteId": prior_remote_id or None,
                    "remoteUrl": (
                        prior.get("remoteUrl") if prior is not None else None
                    ),
                    "status": "failed_retryable",
                    "message": exc.message,
                    "lastSyncedAt": (
                        prior.get("lastSyncedAt") if prior is not None else None
                    ),
                    "updatedAt": now,
                    "details": {
                        "state": "failed_retryable",
                        "retryable": True,
                        "pollingEnabled": False,
                        "blockerType": "provider_execution_failed",
                        "errorCode": exc.code,
                    },
                },
            )

        synced_at = utc_now()
        remote_id = str(provider_receipt["remoteId"])
        return self._record_command(
            identity,
            command_type=command_type,
            aggregate_type=local_type,
            aggregate_id=local_id,
            payload=safe_request,
            idempotency_key=idempotency_key,
            provider="feishu",
            resource_kind=remote_type,
            remote_id=remote_id,
            outcome="succeeded",
            retention_state="active",
            processing_kind=command_type,
            processing_state="completed",
            result_details={
                "localType": local_type,
                "localId": local_id,
                "remoteType": remote_type,
                "remoteId": remote_id,
                "remoteUrl": provider_receipt.get("remoteUrl"),
                "status": "synced",
                "message": "任务已同步到飞书日历",
                "lastSyncedAt": synced_at,
                "updatedAt": synced_at,
                "details": {
                    "state": "ready",
                    "retryable": False,
                    "pollingEnabled": False,
                    "blockerType": None,
                    "errorCode": None,
                    "calendarId": provider_receipt["calendarId"],
                },
            },
        )

    def _feishu_member_access_token(
        self,
        identity: SessionIdentity,
    ) -> str:
        configuration = self._feishu_member_configuration(identity)
        secret = self._feishu_member_secret(identity)
        access_token = str((secret or {}).get("accessToken") or "")
        if (
            not configuration.get("linked")
            or not configuration.get("openId")
            or not access_token
        ):
            raise _FeishuExecutionError(
                "feishu_member_authorization_required",
                "请先在系统设置完成当前成员的飞书授权",
            )
        expires_at = str(configuration.get("accessExpiresAt") or "")
        if not expires_at or self._timestamp_is_fresh(expires_at):
            return access_token
        with self._feishu_refresh_lock:
            latest_configuration = self._feishu_member_configuration(
                identity
            )
            latest_secret = self._feishu_member_secret(identity)
            latest_access_token = str(
                (latest_secret or {}).get("accessToken") or ""
            )
            latest_expires_at = str(
                latest_configuration.get("accessExpiresAt") or ""
            )
            if (
                latest_access_token
                and latest_expires_at
                and self._timestamp_is_fresh(latest_expires_at)
            ):
                return latest_access_token
            return self._refresh_feishu_member_access_token(
                identity,
                configuration=latest_configuration,
                secret=latest_secret or {},
            )

    def _refresh_feishu_member_access_token(
        self,
        identity: SessionIdentity,
        *,
        configuration: Mapping[str, Any],
        secret: Mapping[str, Any],
    ) -> str:
        expires_at = str(configuration.get("accessExpiresAt") or "")
        refresh_token = str((secret or {}).get("refreshToken") or "")
        if not refresh_token:
            raise _FeishuExecutionError(
                "feishu_member_refresh_token_missing",
                "飞书授权已过期且无法刷新，请重新授权",
            )
        organization_configuration = self._feishu_configuration(identity)
        organization_secret = self._feishu_secret_for_scope(
            identity,
            scope_kind=str(
                organization_configuration.get("effectiveScopeKind")
                or "organization"
            ),
        )
        app_id = str(organization_configuration.get("appId") or "")
        app_secret = str((organization_secret or {}).get("appSecret") or "")
        if not app_id or not app_secret:
            raise _FeishuExecutionError(
                "feishu_configuration_missing",
                "组织飞书应用配置不完整，无法刷新成员授权",
            )
        refreshed = self._feishu_provider_json(
            "POST",
            FEISHU_OAUTH_TOKEN_URL,
            payload={
                "grant_type": "refresh_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "refresh_token": refresh_token,
            },
        )
        next_access_token = str(refreshed.get("access_token") or "")
        if not next_access_token:
            raise _FeishuExecutionError(
                "feishu_user_access_token_missing",
                "飞书没有返回新的用户访问凭据，请重新授权",
            )
        next_refresh_token = str(
            refreshed.get("refresh_token") or refresh_token
        )
        clean_public = {
            key: value
            for key, value in configuration.items()
            if key
            not in {
                "updatedAt",
                "version",
                "expectedVersion",
                "effectiveScopeKind",
                "defaultWriteScope",
                "scopeVersions",
                "hasCredentials",
                "secretFingerprint",
            }
        }
        clean_public.update(
            {
                "authorizationState": "ready",
                "lastVerifiedAt": utc_now(),
                "accessExpiresAt": self._future_timestamp(
                    refreshed.get("expires_in")
                ),
                "refreshExpiresAt": self._future_timestamp(
                    refreshed.get("refresh_token_expires_in")
                )
                or configuration.get("refreshExpiresAt"),
                "grantedScopes": str(
                    refreshed.get("scope")
                    or configuration.get("grantedScopes")
                    or ""
                ),
                "lastError": None,
            }
        )
        try:
            self.configurations.upsert(
                identity,
                configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
                scope_kind="personal",
                provider="feishu",
                public_config=clean_public,
                expected_version=int(configuration.get("version") or 0),
                idempotency_key=(
                    "feishu-oauth-refresh:"
                    + sha256_text(
                        "|".join(
                            (
                                refresh_token,
                                str(configuration.get("version") or 0),
                                expires_at,
                            )
                        )
                    )[:24]
                ),
                secret_bundle={
                    "accessToken": next_access_token,
                    "refreshToken": next_refresh_token,
                },
                secret_action="replace",
            )
        except RepositoryError as exc:
            if exc.code not in {
                "configuration_version_conflict",
                "idempotency_conflict",
            }:
                raise
            latest_configuration = self._feishu_member_configuration(
                identity
            )
            latest = self._feishu_member_secret(identity)
            latest_access_token = str(
                (latest or {}).get("accessToken") or ""
            )
            if (
                latest_configuration.get("linked")
                and latest_access_token
                and self._timestamp_is_fresh(
                    latest_configuration.get("accessExpiresAt")
                )
            ):
                return latest_access_token
            raise _FeishuExecutionError(
                "feishu_token_refresh_conflict",
                "飞书成员授权正在刷新，请稍后重试",
            ) from exc
        return next_access_token

    @staticmethod
    def _parse_feishu_document_link(raw_link: Any) -> dict[str, Any] | None:
        link = str(raw_link or "").strip()
        if not link:
            return None
        parsed = urlparse(link)
        hostname = str(parsed.hostname or "").lower()
        if not (
            hostname == "feishu.cn"
            or hostname.endswith(".feishu.cn")
            or hostname == "larksuite.com"
            or hostname.endswith(".larksuite.com")
        ):
            return None
        match = re.search(
            r"/(docx|docs|wiki)/([A-Za-z0-9_-]+)",
            parsed.path or "",
        )
        if match is None:
            return None
        raw_type = match.group(1)
        return {
            "token": match.group(2),
            "type": (
                "docx"
                if raw_type == "docx"
                else "doc"
                if raw_type == "docs"
                else "wiki"
            ),
            "title": "飞书文档",
            "url": link,
            "ownerName": None,
            "updatedAt": None,
            "source": "link",
        }

    @staticmethod
    def _feishu_document_candidate(
        value: Mapping[str, Any],
        *,
        source: str,
    ) -> dict[str, Any] | None:
        nested = value.get("node")
        raw = nested if isinstance(nested, Mapping) else value
        token = str(
            raw.get("obj_token")
            or raw.get("document_id")
            or raw.get("doc_token")
            or raw.get("token")
            or raw.get("docs_token")
            or ""
        ).strip()
        raw_type = str(
            raw.get("obj_type")
            or raw.get("doc_type")
            or raw.get("type")
            or raw.get("docs_type")
            or ""
        ).lower()
        normalized_type = {
            "docs": "doc",
            "document": "docx",
            "wiki_node": "wiki",
        }.get(raw_type, raw_type)
        if not token or normalized_type not in {"docx", "doc", "wiki"}:
            return None
        title = str(
            raw.get("title")
            or raw.get("name")
            or raw.get("document_title")
            or "飞书文档"
        ).strip()
        return {
            "token": token,
            "type": normalized_type,
            "title": title or "飞书文档",
            "url": str(raw.get("url") or raw.get("link") or ""),
            "ownerName": (
                str(
                    raw.get("owner_name")
                    or raw.get("ownerName")
                    or ""
                )
                or None
            ),
            "updatedAt": (
                str(
                    raw.get("update_time")
                    or raw.get("updated_at")
                    or raw.get("updatedAt")
                    or ""
                )
                or None
            ),
            "source": source,
        }

    def _extract_feishu_document_candidates(
        self,
        payload: Mapping[str, Any],
        *,
        source: str,
    ) -> list[dict[str, Any]]:
        data = payload.get("data")
        containers: list[Any] = []
        if isinstance(data, Mapping):
            for key in (
                "items",
                "docs_entities",
                "docs",
                "nodes",
                "files",
                "list",
            ):
                value = data.get(key)
                if isinstance(value, list):
                    containers.extend(value)
        elif isinstance(data, list):
            containers.extend(data)
        seen: set[tuple[str, str]] = set()
        result: list[dict[str, Any]] = []
        for item in containers:
            if not isinstance(item, Mapping):
                continue
            candidate = self._feishu_document_candidate(
                item,
                source=source,
            )
            if candidate is None:
                continue
            key = (str(candidate["type"]), str(candidate["token"]))
            if key in seen:
                continue
            seen.add(key)
            result.append(candidate)
        return result

    def _resolve_feishu_candidate(
        self,
        access_token: str,
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        token = str(candidate.get("token") or "")
        doc_type = str(candidate.get("type") or "")
        result = dict(candidate)
        if doc_type == "wiki":
            payload = self._feishu_provider_json(
                "GET",
                f"{FEISHU_API_ROOT}/wiki/v2/spaces/get_node",
                access_token=access_token,
                params={"token": token},
            )
            data = payload.get("data")
            node = (
                data.get("node")
                if isinstance(data, Mapping)
                and isinstance(data.get("node"), Mapping)
                else None
            )
            if node is None:
                raise _FeishuExecutionError(
                    "feishu_wiki_node_invalid",
                    "飞书知识库链接没有返回有效文档节点",
                )
            resolved = self._feishu_document_candidate(
                node,
                source=str(candidate.get("source") or "link"),
            )
            if resolved is None:
                raise _FeishuExecutionError(
                    "feishu_wiki_document_unsupported",
                    "该飞书知识库节点不是可导入的文档",
                )
            result.update(resolved)
            result["url"] = str(candidate.get("url") or result.get("url") or "")
        return result

    def _fetch_feishu_document_text(
        self,
        access_token: str,
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        resolved = self._resolve_feishu_candidate(
            access_token,
            candidate,
        )
        if str(resolved.get("type") or "") != "docx":
            raise _FeishuExecutionError(
                "feishu_document_type_unsupported",
                "严格新版当前只支持导入飞书新版文档（Docx）",
            )
        token = str(resolved.get("token") or "")
        payload = self._feishu_provider_json(
            "GET",
            f"{FEISHU_API_ROOT}/docx/v1/documents/{quote(token)}/raw_content",
            access_token=access_token,
        )
        data = payload.get("data")
        content = (
            str(data.get("content") or "")
            if isinstance(data, Mapping)
            else ""
        ).strip()
        if not content:
            raise _FeishuExecutionError(
                "feishu_document_content_empty",
                "飞书文档没有返回可导入的正文",
            )
        if len(content.encode("utf-8")) > 4 * 1024 * 1024:
            raise _FeishuExecutionError(
                "feishu_document_too_large",
                "飞书文档正文超过 4 MiB，本次未导入",
            )
        return {**resolved, "content": content}

    def request_feishu_import(
        self,
        identity: SessionIdentity,
        *,
        action: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if action not in {"search", "resolve_links", "fetch"}:
            raise RepositoryError(
                422,
                "feishu_import_action_invalid",
                "不支持的飞书文档导入动作",
            )
        requested_count = (
            len(payload.get("links") or payload.get("items") or [])
            if action in {"resolve_links", "fetch"}
            else self._safe_int(
                payload.get("pageSize"),
                default=20,
                maximum=50,
            )
        )
        integration = self.feishu_integration(identity)
        personal_identity = self._personal_identity(identity)
        if integration["state"] != "ready":
            not_configured = integration["state"] == "not_connected"
            attempt = self._record_command(
                personal_identity,
                command_type=f"feishu.import.{action}",
                aggregate_type="feishu_document_import",
                aggregate_id=new_id(),
                payload={"requestedCount": requested_count},
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind="document_import",
                remote_id=f"blocked:{new_id()}",
                outcome="blocked" if not_configured else "failed_retryable",
                error_code=(
                    "feishu_configuration_missing"
                    if not_configured
                    else "feishu_provider_validation_failed"
                ),
                error_message=str(
                    integration.get("lastValidationMessage")
                    or "组织飞书应用当前不可用"
                ),
                owner_kind="membership",
            )
            return {
                **attempt,
                "items": [],
                "requestedCount": requested_count,
                "pollingEnabled": False,
                "blockerType": (
                    "configuration_missing"
                    if not_configured
                    else "provider_validation_failed"
                ),
            }
        try:
            access_token = self._feishu_tenant_access_token(
                identity,
                self._feishu_configuration(identity),
            )
            items: list[dict[str, Any]] = []
            failed_items: list[dict[str, Any]] = []
            if action == "search":
                query = str(payload.get("query") or "").strip()
                if not query:
                    raise RepositoryError(
                        422,
                        "feishu_search_query_required",
                        "请输入要搜索的飞书文档关键词",
                    )
                search_payload = self._feishu_provider_json(
                    "POST",
                    FEISHU_DOCUMENT_SEARCH_URL,
                    access_token=access_token,
                    payload={
                        "query": query,
                        "page_size": requested_count,
                    },
                )
                items = self._extract_feishu_document_candidates(
                    search_payload,
                    source="search",
                )[:requested_count]
            elif action == "resolve_links":
                seen: set[tuple[str, str]] = set()
                for raw_link in list(payload.get("links") or [])[:50]:
                    candidate = self._parse_feishu_document_link(raw_link)
                    if candidate is None:
                        continue
                    try:
                        candidate = self._resolve_feishu_candidate(
                            access_token,
                            candidate,
                        )
                    except _FeishuExecutionError:
                        pass
                    key = (
                        str(candidate.get("type") or ""),
                        str(candidate.get("token") or ""),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(candidate)
            else:
                for raw_item in list(payload.get("items") or [])[:20]:
                    if not isinstance(raw_item, Mapping):
                        continue
                    candidate = {
                        "token": str(raw_item.get("token") or "").strip(),
                        "type": str(raw_item.get("type") or "").strip(),
                        "title": str(
                            raw_item.get("title") or "飞书文档"
                        ).strip(),
                        "url": str(raw_item.get("url") or "").strip(),
                        "source": "link",
                    }
                    if not candidate["token"]:
                        continue
                    try:
                        items.append(
                            self._fetch_feishu_document_text(
                                access_token,
                                candidate,
                            )
                        )
                    except _FeishuExecutionError as exc:
                        failed_items.append(
                            {
                                **candidate,
                                "status": "failed",
                                "errorCode": exc.code,
                                "message": exc.message,
                            }
                        )
        except RepositoryError:
            raise
        except _FeishuExecutionError as exc:
            attempt = self._record_command(
                personal_identity,
                command_type=f"feishu.import.{action}",
                aggregate_type="feishu_document_import",
                aggregate_id=new_id(),
                payload={"requestedCount": requested_count},
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind="document_import",
                remote_id=f"failed:{new_id()}",
                outcome=(
                    "blocked"
                    if exc.code
                    in {
                        "feishu_member_authorization_required",
                        "feishu_member_refresh_token_missing",
                    }
                    else "failed_retryable"
                ),
                error_code=exc.code,
                error_message=exc.message,
                owner_kind="membership",
            )
            return {
                **attempt,
                "items": [],
                "requestedCount": requested_count,
                "pollingEnabled": False,
                "blockerType": (
                    "member_authorization_required"
                    if attempt["state"] == "blocked"
                    else "provider_request_failed"
                ),
            }
        safe_item_receipts = [
            {
                "tokenHash": sha256_text(str(item.get("token") or "")),
                "type": str(item.get("type") or ""),
                "contentHash": (
                    sha256_text(str(item.get("content") or ""))
                    if action == "fetch"
                    else None
                ),
            }
            for item in items
        ]
        attempt = self._record_command(
            personal_identity,
            command_type=f"feishu.import.{action}",
            aggregate_type="feishu_document_import",
            aggregate_id=new_id(),
            payload={
                "requestedCount": requested_count,
                "queryHash": (
                    sha256_text(str(payload.get("query") or ""))
                    if action == "search"
                    else None
                ),
                "itemReceipts": safe_item_receipts,
                "failedCount": len(failed_items),
            },
            idempotency_key=idempotency_key,
            provider="feishu",
            resource_kind="document_import",
            remote_id=f"read:{new_id()}",
            outcome="succeeded",
            retention_state="completed",
            result_details={
                "requestedCount": requested_count,
                "resolvedCount": len(items),
                "failedCount": len(failed_items),
            },
            owner_kind="membership",
        )
        message = (
            ""
            if items
            else (
                "没有找到可导入的飞书文档"
                if action != "fetch"
                else "所选飞书文档均未能读取"
            )
        )
        return {
            **attempt,
            "items": items,
            "failedItems": failed_items,
            "message": message,
            "state": "ready",
            "retryable": False,
            "pollingEnabled": False,
            "requestedCount": requested_count,
        }

    def register_feishu_import_mapping(
        self,
        identity: SessionIdentity,
        *,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        document_id = str(payload.get("documentId") or "").strip()
        remote_id = str(payload.get("remoteId") or "").strip()
        provider_remote_type = str(
            payload.get("remoteType") or "docx"
        ).strip()
        remote_type = (
            "docx_document"
            if provider_remote_type in {"docx", "docx_document"}
            else provider_remote_type
        )
        if not document_id or not remote_id:
            raise RepositoryError(
                422,
                "feishu_import_mapping_invalid",
                "飞书导入映射缺少本机文档或远端文档身份",
            )
        personal_identity = self._personal_identity(identity)
        return self._record_command(
            personal_identity,
            command_type="feishu.import.mapping.registered",
            aggregate_type="document",
            aggregate_id=document_id,
            payload={
                "documentId": document_id,
                "remoteIdHash": sha256_text(remote_id),
                "remoteType": remote_type,
                "providerRemoteType": provider_remote_type,
                "direction": "feishu_to_member_local",
            },
            idempotency_key=idempotency_key,
            provider="feishu",
            resource_kind="imported_document",
            remote_id=remote_id,
            outcome="succeeded",
            retention_state="active",
            result_details={
                "localType": "document",
                "localId": document_id,
                "remoteType": remote_type,
                "remoteId": remote_id,
                "remoteUrl": str(payload.get("remoteUrl") or ""),
                "status": "synced",
                "message": "已从飞书导入软件资料库",
                "lastSyncedAt": utc_now(),
            },
            owner_kind="membership",
        )

    def create_support_request(
        self,
        identity: SessionIdentity,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_id = new_id()
        now = utc_now()
        record = {
            "id": request_id,
            "taskId": payload.get("taskId"),
            "eventLineId": payload.get("eventLineId"),
            "requesterUserId": identity.membership_id,
            "targetScope": str(payload.get("targetScope") or "organization"),
            "targetRefId": payload.get("targetRefId"),
            "requestType": str(payload.get("requestType") or "clarification"),
            "urgency": str(payload.get("urgency") or "medium"),
            "summary": str(payload.get("summary") or "").strip(),
            "status": "open",
            "resolutionNote": "",
            "createdAt": now,
            "updatedAt": now,
        }
        if not record["summary"]:
            raise RepositoryError(422, "support_summary_required", "请填写支持请求内容")
        self._record_command(
            identity,
            command_type="support_request.create",
            aggregate_type="support_request",
            aggregate_id=request_id,
            payload=record,
            idempotency_key=idempotency_key,
            provider="yiyu_support",
            resource_kind="support_request",
            remote_id=request_id,
            outcome="queued",
        )
        return record

    def list_support_requests(
        self,
        identity: SessionIdentity,
        *,
        status: str = "",
        task_id: str = "",
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in self.operations.list_records(
            identity,
            aggregate_type="support_request",
            command_types=("support_request.create", "support_request.resolve"),
        ):
            if status and item["status"] != status:
                continue
            if task_id and str(item.get("taskId") or "") != task_id:
                continue
            results.append(item)
        return results

    def resolve_support_request(
        self,
        identity: SessionIdentity,
        request_id: str,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        requested_status = str(payload.get("status") or "resolved")
        if requested_status not in {"accepted", "resolved", "dismissed"}:
            raise RepositoryError(422, "support_status_invalid", "支持请求状态不合法")
        existing = next(
            (
                entry
                for entry in self.list_support_requests(identity)
                if entry.get("id") == request_id
            ),
            None,
        )
        if existing is None:
            raise RepositoryError(404, "support_request_missing", "支持请求不存在")
        updated = {
            **existing,
            "status": requested_status,
            "resolutionNote": str(payload.get("resolutionNote") or ""),
            "updatedAt": utc_now(),
        }
        self._record_command(
            identity,
            command_type="support_request.resolve",
            aggregate_type="support_request",
            aggregate_id=request_id,
            payload=updated,
            idempotency_key=idempotency_key,
            provider="yiyu_support",
            resource_kind="support_request",
            remote_id=request_id,
            outcome="succeeded",
            retention_state=requested_status,
        )
        return updated

    def create_feedback(
        self,
        identity: SessionIdentity,
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        existing = self._existing_command_record(
            identity,
            command_type="software_feedback.submit",
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            receipt, record = existing
            presented = {
                "title": str(payload.get("title") or "").strip(),
                "description": str(payload.get("description") or ""),
                "category": str(payload.get("category") or "bug"),
                "severity": str(payload.get("severity") or "medium"),
                "screenshotObjectId": (
                    str(payload.get("screenshotObjectId") or "") or None
                ),
                "screenshotContentHash": (
                    str(payload.get("screenshotContentHash") or "") or None
                ),
            }
            persisted = {
                key: record.get(key)
                for key in presented
            }
            if presented != persisted:
                raise RepositoryError(
                    409,
                    "idempotency_payload_conflict",
                    "同一幂等键不能提交不同反馈内容",
                )
            return {
                "queued": bool(record.get("queued")),
                "record": record,
                "operationId": receipt.get("operationId"),
                "processingAttemptId": receipt.get("processingAttemptId"),
                "state": record.get("centralStatus") or "not_connected",
                "pollingEnabled": False,
                "retryable": True,
                "idempotentReplay": True,
            }
        feedback_id = new_id()
        now = utc_now()
        screenshot_object_id = (
            str(payload.get("screenshotObjectId") or "").strip() or None
        )
        screenshot_content_hash = (
            str(payload.get("screenshotContentHash") or "").strip() or None
        )
        screenshot_saved = bool(
            payload.get("screenshotRequested")
            and screenshot_object_id
            and screenshot_content_hash
        )
        screenshot_media_type = (
            str(payload.get("screenshotMediaType") or "").strip() or None
        )
        try:
            screenshot_byte_size = int(
                payload.get("screenshotByteSize") or 0
            )
        except (TypeError, ValueError) as exc:
            raise RepositoryError(
                422,
                "feedback_screenshot_metadata_invalid",
                "反馈截图元数据无效",
            ) from exc
        if screenshot_saved and (
            not re.fullmatch(r"[0-9a-f]{64}", screenshot_content_hash or "")
            or screenshot_media_type not in {
                "image/png",
                "image/jpeg",
                "image/webp",
            }
            or not 0 < screenshot_byte_size <= 6 * 1024 * 1024
            or len(screenshot_object_id or "") > 200
        ):
            raise RepositoryError(
                422,
                "feedback_screenshot_metadata_invalid",
                "反馈截图元数据无效",
            )
        record = {
            "id": feedback_id,
            "localFeedbackId": screenshot_object_id or feedback_id,
            "centralFeedbackId": None,
            "queued": False,
            "queueStatus": "blocked",
            "centralStatus": "not_connected",
            "central": False,
            "lastError": "中心反馈平台尚未连接，已进入可靠投递队列",
            "category": str(payload.get("category") or "bug"),
            "severity": str(payload.get("severity") or "medium"),
            "status": "open",
            "title": str(payload.get("title") or "").strip(),
            "description": str(payload.get("description") or ""),
            "appVersion": payload.get("appVersion"),
            "platform": payload.get("platform"),
            "pageRoute": payload.get("pageRoute"),
            "deviceInfo": payload.get("deviceInfo"),
            "logExcerpt": None,
            "screenshotPath": None,
            "screenshotObjectId": screenshot_object_id,
            "screenshotContentHash": screenshot_content_hash,
            "screenshotMediaType": screenshot_media_type,
            "screenshotByteSize": (
                screenshot_byte_size if screenshot_saved else None
            ),
            "screenshotState": (
                "local_saved"
                if screenshot_saved
                else (
                    "blocked"
                    if payload.get("screenshotRequested")
                    else "not_requested"
                )
            ),
            "clientId": payload.get("clientId"),
            "taskId": payload.get("taskId"),
            "resolutionNote": None,
            "createdAt": now,
            "updatedAt": now,
        }
        if not record["title"]:
            raise RepositoryError(422, "feedback_title_required", "请填写反馈标题")
        attempt = self._record_command(
            identity,
            command_type="software_feedback.submit",
            aggregate_type="software_feedback",
            aggregate_id=feedback_id,
            payload=record,
            idempotency_key=idempotency_key,
            provider="yiyu_feedback",
            resource_kind="feedback",
            remote_id=feedback_id,
            outcome="succeeded",
            retention_state="active",
        )
        return {
            "queued": False,
            "record": record,
            "operationId": attempt["operationId"],
            "processingAttemptId": attempt["processingAttemptId"],
            "state": "not_connected",
            "pollingEnabled": False,
            "retryable": True,
        }

    def list_feedback(self, identity: SessionIdentity) -> dict[str, Any]:
        items = self.operations.list_records(
            identity,
            aggregate_type="software_feedback",
            command_types=("software_feedback.submit",),
        )
        return {
            "items": items,
            "queuedCount": sum(1 for item in items if item.get("queued")),
            "centralError": "中心反馈平台尚未连接",
        }

    def operation_logs(
        self,
        identity: SessionIdentity,
        *,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self.runtime_diagnostics.operation_logs(identity, limit=limit)

    def active_background_tasks(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT c.command_type, c.aggregate_id, a.transport_state
                FROM operation_attempts a
                JOIN command_envelopes c ON c.command_id = a.command_id
                WHERE a.scope_id = ? AND c.organization_id = ?
                  AND a.transport_state IN ('queued', 'running', 'retrying')
                ORDER BY a.created_at DESC
                """,
                (identity.scope_id, identity.organization_id),
            ).fetchall()
        tasks = [
            {
                "kind": str(row["command_type"]),
                "label": str(row["aggregate_id"]),
                "status": str(row["transport_state"]),
                "severity": "queued",
            }
            for row in rows
        ]
        return {
            "tasks": tasks,
            "count": len(tasks),
            "pollingEnabled": bool(tasks),
            "state": "active" if tasks else "idle",
        }

    def tool_registry(self) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        for item in PLATFORM_TOOLS:
            state = str(item["status"])
            by_status[state] = by_status.get(state, 0) + 1
        return {
            "version": "strict-platform-v1",
            "total": len(PLATFORM_TOOLS),
            "by_status": by_status,
            "tools": list(PLATFORM_TOOLS),
            "schema_completeness": {
                "external_provider_resources": True,
                "external_side_effects": True,
                "operation_attempts": True,
                "operation_dead_letters": True,
                "reconciliation_runs": True,
                "encrypted_provider_credentials": True,
            },
        }

    def _personal_identity(self, identity: SessionIdentity) -> SessionIdentity:
        # Personal ownership is represented by owner principal/membership on
        # the organization authorization scope.  A synthetic personal scope is
        # not required by the 88-table contract.
        return identity

    @staticmethod
    def _require_scope(
        authorization_scope: str,
        *,
        expected: str,
        resource_path: str,
    ) -> None:
        if authorization_scope not in {"organization", "personal"}:
            raise RepositoryError(
                422,
                "authorization_scope_invalid",
                "authorizationScope 必须是 organization 或 personal",
            )
        if authorization_scope != expected:
            raise RepositoryError(
                409,
                "authorization_scope_mismatch",
                f"{resource_path} 需要 {expected} 授权作用域",
            )

    @staticmethod
    def _personal_feishu_remote_id(identity: SessionIdentity) -> str:
        return f"{identity.organization_id}:{identity.membership_id}"

    @staticmethod
    def _personal_feishu_authorization_resource_id(
        identity: SessionIdentity,
    ) -> str:
        return (
            "feishu-member-auth-"
            + sha256_text(
                f"{identity.organization_id}:{identity.membership_id}"
            )[:32]
        )

    def _write_personal_feishu_authorization_grant(
        self,
        identity: SessionIdentity,
    ) -> None:
        personal_identity = self._personal_identity(identity)
        resource_id = self._personal_feishu_authorization_resource_id(identity)
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                resource = connection.execute(
                    "SELECT version FROM secured_resources WHERE id=? AND scope_id=?",
                    (resource_id, personal_identity.scope_id),
                ).fetchone()
                next_version = (
                    int(resource["version"]) + 1
                    if resource is not None
                    else 1
                )
                if resource is None:
                    connection.execute(
                        """
                        INSERT INTO secured_resources (
                            id,scope_id,resource_kind,lifecycle_state,version,
                            resource_type_key,created_at,updated_at,deleted_at,
                            authority_role,origin_instance_id
                        ) VALUES (?,?,'provider_authorization','active',1,
                                  'feishu_member_authorization',?,?,NULL,'cloud',?)
                        """,
                        (
                            resource_id,
                            personal_identity.scope_id,
                            now,
                            now,
                            identity.cloud_instance_id,
                        ),
                    )
                else:
                    connection.execute(
                        "UPDATE secured_resources SET lifecycle_state='active',"
                        "version=?,updated_at=?,deleted_at=NULL WHERE id=? AND scope_id=?",
                        (
                            next_version,
                            now,
                            resource_id,
                            personal_identity.scope_id,
                        ),
                    )
                policy_version_id = (
                    "policy_feishu_"
                    + sha256_text(f"{resource_id}|{next_version}")[:30]
                )
                capabilities = [
                    "feishu.profile.read",
                    "feishu.document.search",
                    "feishu.document.read",
                ]
                connection.execute(
                    """
                    INSERT INTO policy_versions (
                        id,scope_id,secured_resource_id,policy_scope_kind,version,
                        policy_spec_schema_version,policy_spec,effective_at,created_at,
                        lifecycle_state,updated_at,deleted_at
                    ) VALUES (?, ?, ?, 'member', ?, 'yiyu.feishu-capabilities.v1',
                              ?,?,?, 'active',?,NULL)
                    """,
                    (
                        policy_version_id,
                        personal_identity.scope_id,
                        resource_id,
                        next_version,
                        canonical_json({"capabilities": capabilities}),
                        now,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "UPDATE object_grants SET status='revoked',revoked_at=?,"
                    "updated_at=?,version=version+1 WHERE scope_id=? "
                    "AND secured_resource_id=? AND status='active'",
                    (now, now, personal_identity.scope_id, resource_id),
                )
                connection.execute(
                    """
                    INSERT INTO object_grants (
                        id,scope_id,secured_resource_id,policy_version_id,
                        subject_principal_id,subject_membership_id,
                        capability_set_schema_version,capability_set,grant_generation,
                        status,grant_source_set_id,created_at,updated_at,revoked_at,
                        version,lifecycle_state,deleted_at
                    ) VALUES (?,?,?,?,?,?,'1',?,?,'active',NULL,?,?,NULL,1,'active',NULL)
                    """,
                    (
                        new_id(),
                        personal_identity.scope_id,
                        resource_id,
                        policy_version_id,
                        identity.principal_id,
                        identity.membership_id,
                        canonical_json({"capabilities": capabilities}),
                        next_version,
                        now,
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _revoke_personal_feishu_authorization_grant(
        self,
        identity: SessionIdentity,
    ) -> None:
        personal_identity = self._personal_identity(identity)
        resource_id = self._personal_feishu_authorization_resource_id(identity)
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "UPDATE object_grants SET status='revoked',revoked_at=?,"
                    "updated_at=?,version=version+1 WHERE scope_id=? "
                    "AND secured_resource_id=? AND status='active'",
                    (now, now, personal_identity.scope_id, resource_id),
                )
                connection.execute(
                    "UPDATE secured_resources SET lifecycle_state='archived',"
                    "version=version+1,updated_at=? WHERE id=? AND scope_id=?",
                    (now, resource_id, personal_identity.scope_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _identity_for_feishu_oauth_state(
        self,
        state_token: str,
    ) -> tuple[SessionIdentity, dict[str, Any], int]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT r.public_config, r.version,
                       m.id AS membership_id, m.scope_id,
                       scope.organization_id,
                       m.principal_id, m.role_key, m.visibility_scope,
                       p.display_name
                FROM provider_resources AS r
                JOIN organization_memberships AS m
                  ON m.id = r.owner_membership_id
                 AND m.principal_id = r.owner_principal_id
                 AND m.scope_id = r.scope_id
                JOIN authorization_scopes AS scope ON scope.id=m.scope_id
                JOIN principals AS p ON p.id=m.principal_id
                WHERE r.resource_kind = ?
                  AND r.owner_kind = 'membership'
                  AND r.lifecycle_state = 'active'
                  AND m.status = 'active'
                  AND p.status = 'active'
                  AND json_extract(
                        r.public_config, '$.pendingState'
                      ) = ?
                LIMIT 1
                """,
                (FEISHU_MEMBER_AUTHORIZATION_KIND, state_token),
            ).fetchone()
        if row is None:
            raise RepositoryError(
                404,
                "feishu_oauth_state_missing",
                "这次飞书授权会话不存在或已经失效",
            )
        public_config = self._json_text(row["public_config"])
        expires_at = str(
            public_config.get("pendingStateExpiresAt") or ""
        )
        if not self._timestamp_is_fresh(expires_at, margin_seconds=0):
            raise RepositoryError(
                409,
                "feishu_oauth_state_expired",
                "这次飞书授权会话已经过期，请回到软件重新发起",
            )
        identity = SessionIdentity(
            session_id=f"feishu-oauth:{sha256_text(state_token)[:16]}",
            principal_id=str(row["principal_id"]),
            membership_id=str(row["membership_id"]),
            organization_id=str(row["organization_id"]),
            cloud_instance_id=self.repository.cloud_instance_id,
            scope_id=str(row["scope_id"]),
            system_role=str(row["role_key"]),
            visibility_scope=str(row["visibility_scope"]),
            display_name=str(row["display_name"]),
        )
        return identity, public_config, int(row["version"])

    def complete_personal_feishu_authorization(
        self,
        *,
        state_token: str,
        code: str,
    ) -> dict[str, Any]:
        if not state_token or not code:
            raise RepositoryError(
                422,
                "feishu_oauth_callback_invalid",
                "飞书没有返回完整的授权结果",
            )
        identity, pending, expected_version = (
            self._identity_for_feishu_oauth_state(state_token)
        )
        callback_url = str(pending.get("pendingCallbackUrl") or "")
        claim_nonce = secrets.token_urlsafe(12)
        claimed = self.configurations.upsert(
            identity,
            configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
            scope_kind="personal",
            provider="feishu",
            public_config={
                **{
                    key: value
                    for key, value in pending.items()
                    if key
                    not in {
                        "pendingState",
                        "pendingStateExpiresAt",
                        "pendingCallbackUrl",
                        "updatedAt",
                        "version",
                        "expectedVersion",
                        "effectiveScopeKind",
                        "defaultWriteScope",
                        "scopeVersions",
                        "hasCredentials",
                        "secretFingerprint",
                    }
                },
                "authorizationState": "authorization_exchanging",
                "exchangeClaimNonce": claim_nonce,
                "lastError": None,
            },
            expected_version=expected_version,
            idempotency_key=(
                "feishu-oauth-claim:"
                + sha256_text(state_token)[:24]
            ),
            secret_action="preserve",
        )
        if claimed.get("exchangeClaimNonce") != claim_nonce:
            raise RepositoryError(
                409,
                "feishu_oauth_state_already_claimed",
                "这次飞书授权结果已经处理，请回到软件查看状态",
            )
        expected_version = int(claimed.get("version") or 0)
        integration = self.feishu_integration(identity)
        if integration["state"] != "ready":
            raise RepositoryError(
                409,
                "feishu_application_not_ready",
                "组织飞书应用当前不可用，请回到软件检查配置",
            )
        configuration = self._feishu_configuration(identity)
        secret_bundle = self._feishu_secret_for_scope(
            identity,
            scope_kind=str(
                configuration.get("effectiveScopeKind") or "organization"
            ),
        )
        app_id = str(configuration.get("appId") or "")
        app_secret = str((secret_bundle or {}).get("appSecret") or "")
        if not app_id or not app_secret or not callback_url:
            raise RepositoryError(
                409,
                "feishu_oauth_configuration_incomplete",
                "组织飞书应用或授权回调配置不完整",
            )
        try:
            token_payload = self._feishu_provider_json(
                "POST",
                FEISHU_OAUTH_TOKEN_URL,
                payload={
                    "grant_type": "authorization_code",
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "code": code,
                    "redirect_uri": callback_url,
                },
            )
            access_token = str(token_payload.get("access_token") or "")
            if not access_token:
                raise _FeishuExecutionError(
                    "feishu_user_access_token_missing",
                    "飞书没有返回有效的用户访问凭据",
                )
            user_payload = self._feishu_provider_json(
                "GET",
                FEISHU_USER_INFO_URL,
                access_token=access_token,
            )
        except _FeishuExecutionError as exc:
            raise RepositoryError(502, exc.code, exc.message) from exc
        user_info = user_payload.get("data")
        if not isinstance(user_info, Mapping):
            raise RepositoryError(
                502,
                "feishu_user_info_invalid",
                "飞书没有返回有效的成员身份",
            )
        open_id = str(user_info.get("open_id") or "").strip()
        if not open_id:
            raise RepositoryError(
                502,
                "feishu_open_id_missing",
                "飞书没有返回 open_id，无法完成成员授权",
            )
        now = utc_now()
        access_expires_at = self._future_timestamp(
            token_payload.get("expires_in")
        )
        refresh_expires_at = self._future_timestamp(
            token_payload.get("refresh_token_expires_in")
        )
        public_config = {
            "linked": True,
            "authorizationState": "ready",
            "appId": app_id,
            "openId": open_id,
            "unionId": str(user_info.get("union_id") or "") or None,
            "feishuUserId": str(user_info.get("user_id") or "") or None,
            "name": str(user_info.get("name") or "") or identity.display_name,
            "enName": str(user_info.get("en_name") or "") or None,
            "avatarUrl": str(user_info.get("avatar_url") or "") or None,
            "email": str(user_info.get("email") or "") or None,
            "tenantKey": str(user_info.get("tenant_key") or "") or None,
            "boundAt": now,
            "lastVerifiedAt": now,
            "accessExpiresAt": access_expires_at,
            "refreshExpiresAt": refresh_expires_at,
            "grantedScopes": str(token_payload.get("scope") or ""),
            "lastError": None,
        }
        refresh_token = str(token_payload.get("refresh_token") or "")
        self.configurations.upsert(
            identity,
            configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
            scope_kind="personal",
            provider="feishu",
            public_config=public_config,
            expected_version=expected_version,
            idempotency_key=(
                "feishu-oauth-callback:"
                + sha256_text(state_token)[:24]
            ),
            secret_bundle={
                "accessToken": access_token,
                "refreshToken": refresh_token,
            },
            secret_action="replace",
        )
        self._write_personal_feishu_authorization_grant(identity)
        personal_identity = self._personal_identity(identity)
        self._record_command(
            personal_identity,
            command_type="feishu.personal_authorization.completed",
            aggregate_type="personal_provider_authorization",
            aggregate_id=self._personal_feishu_remote_id(identity),
            payload={
                "organizationId": identity.organization_id,
                "membershipId": identity.membership_id,
                "appId": app_id,
                "openIdHash": sha256_text(open_id),
                "grantedScopeHash": sha256_text(
                    str(token_payload.get("scope") or "")
                ),
            },
            idempotency_key=(
                "feishu-oauth-completed:"
                + sha256_text(state_token)[:24]
            ),
            provider="feishu",
            resource_kind="member_authorization",
            remote_id=self._personal_feishu_remote_id(identity),
            outcome="succeeded",
            retention_state="active",
        )
        return self.personal_feishu_authorization(identity)

    def personal_feishu_authorization(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        # Personal delivery state must read only the organization's base
        # readiness.  The full integration view includes member coverage and
        # therefore calls back into this method for each member.
        integration = self._feishu_integration_readiness(identity)
        configuration = self._feishu_member_configuration(identity)
        authorization_state = str(
            configuration.get("authorizationState") or "not_connected"
        )
        linked = bool(
            configuration.get("linked")
            and configuration.get("hasCredentials")
            and configuration.get("openId")
            and authorization_state == "ready"
        )
        if linked:
            state = "ready"
            blocked_reason = None
        elif authorization_state == "authorization_pending":
            state = "processing"
            blocked_reason = "authorization_pending"
        elif authorization_state == "failed_retryable":
            state = "failed_retryable"
            blocked_reason = str(
                configuration.get("lastError") or "member_authorization_failed"
            )
        elif integration["state"] != "ready":
            state = integration["state"]
            blocked_reason = (
                integration.get("authorizationBlockedReason")
                or "feishu_application_not_registered"
            )
        else:
            state = "not_connected"
            blocked_reason = "member_authorization_required"
        return {
            "linked": linked,
            "readyForAuthorization": integration["state"] == "ready",
            "organizationId": identity.organization_id,
            "organizationName": None,
            "appId": integration["appId"],
            "userId": identity.membership_id,
            "openId": configuration.get("openId"),
            "unionId": configuration.get("unionId"),
            "feishuUserId": configuration.get("feishuUserId"),
            "name": configuration.get("name") or identity.display_name,
            "enName": configuration.get("enName"),
            "avatarUrl": configuration.get("avatarUrl"),
            "email": configuration.get("email"),
            "tenantKey": configuration.get("tenantKey"),
            "boundAt": configuration.get("boundAt"),
            "lastVerifiedAt": configuration.get("lastVerifiedAt"),
            "lastError": configuration.get("lastError"),
            "blockedReason": blocked_reason,
            "state": state,
            "retryable": not linked,
            "authorizationScope": "personal",
        }

    def start_personal_feishu_authorization(
        self,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        personal_identity = self._personal_identity(identity)
        integration = self.feishu_integration(identity)
        if integration["state"] != "ready":
            raise RepositoryError(
                409,
                "feishu_application_not_ready",
                str(
                    integration.get("lastValidationMessage")
                    or "请先完成组织飞书应用配置"
                ),
            )
        normalized_callback = self._feishu_oauth_relay_callback_url()
        parsed_callback = urlparse(normalized_callback)
        if (
            not normalized_callback
            or parsed_callback.scheme != "https"
            or not parsed_callback.netloc
        ):
            raise RepositoryError(
                422,
                "feishu_oauth_callback_invalid",
                "飞书统一授权服务没有生成有效的 HTTPS 回调地址",
            )
        existing = self._existing_command_record(
            personal_identity,
            command_type="feishu.personal_authorization.start",
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return existing[0]
        state_token = secrets.token_urlsafe(32)
        claim_secret = secrets.token_urlsafe(32)
        expires_at = str(self._future_timestamp(600) or "")
        try:
            self._register_feishu_oauth_relay_session(
                state_token=state_token,
                claim_secret=claim_secret,
                expires_at=expires_at,
            )
        except _FeishuExecutionError as exc:
            raise RepositoryError(503, exc.code, exc.message) from exc
        current = self._feishu_member_configuration(identity)
        linked = bool(current.get("linked"))
        current_secret = dict(self._feishu_member_secret(identity) or {})
        current_secret.update(
            {
                "pendingRelayState": state_token,
                "pendingRelayClaimSecret": claim_secret,
            }
        )
        self.configurations.upsert(
            identity,
            configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
            scope_kind="personal",
            provider="feishu",
            public_config={
                **{
                    key: value
                    for key, value in current.items()
                    if key
                    not in {
                        "updatedAt",
                        "version",
                        "expectedVersion",
                        "effectiveScopeKind",
                        "defaultWriteScope",
                        "scopeVersions",
                        "hasCredentials",
                        "secretFingerprint",
                    }
                },
                "linked": linked,
                "authorizationState": "authorization_pending",
                "appId": integration["appId"],
                "pendingState": state_token,
                "pendingStateExpiresAt": expires_at,
                "pendingCallbackUrl": normalized_callback,
                "lastError": None,
            },
            expected_version=int(current.get("version") or 0),
            idempotency_key=f"{idempotency_key}:pending-configuration",
            secret_bundle=current_secret,
            secret_action="replace",
        )
        authorize_url = (
            f"{FEISHU_OAUTH_AUTHORIZE_URL}?"
            + urlencode(
                {
                    "client_id": integration["appId"],
                    "redirect_uri": normalized_callback,
                    "response_type": "code",
                    "state": state_token,
                    "scope": " ".join(FEISHU_MEMBER_DOCUMENT_SCOPES),
                }
            )
        )
        attempt = self._record_command(
            personal_identity,
            command_type="feishu.personal_authorization.start",
            aggregate_type="personal_provider_authorization",
            aggregate_id=self._personal_feishu_remote_id(identity),
            payload={
                "organizationId": identity.organization_id,
                "membershipId": identity.membership_id,
                "applicationRegistered": True,
                "callbackUrlHash": sha256_text(normalized_callback),
                "stateHash": sha256_text(state_token),
                "expiresAt": expires_at,
            },
            idempotency_key=idempotency_key,
            provider="feishu",
            resource_kind="member_authorization",
            remote_id=self._personal_feishu_remote_id(identity),
            outcome="queued",
            retention_state="authorization_pending",
            result_details={
                "authorizeUrl": authorize_url,
                "state": state_token,
                "expiresAt": expires_at,
                "callbackUrl": normalized_callback,
                "qrReady": True,
                "qrBlockedReason": None,
                "retryable": True,
                "authorizationScope": "personal",
            },
        )
        return attempt

    def claim_personal_feishu_authorization(
        self,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        current = self._feishu_member_configuration(identity)
        if str(current.get("authorizationState") or "") != "authorization_pending":
            return self.personal_feishu_authorization(identity)
        state_token = str(current.get("pendingState") or "")
        expires_at = str(current.get("pendingStateExpiresAt") or "")
        current_secret = dict(self._feishu_member_secret(identity) or {})
        claim_secret = str(current_secret.get("pendingRelayClaimSecret") or "")
        if not state_token or not claim_secret:
            raise RepositoryError(
                409,
                "feishu_oauth_relay_session_missing",
                "当前授权会话不是统一官网回调会话，请重新发起飞书授权",
            )
        if not self._timestamp_is_fresh(expires_at, margin_seconds=0):
            relay_result: dict[str, Any] = {
                "status": "expired",
                "errorMessage": "飞书授权会话已过期，请重新发起授权",
            }
        else:
            try:
                relay_result = self._claim_feishu_oauth_relay_code(
                    state_token=state_token,
                    claim_secret=claim_secret,
                )
            except _FeishuExecutionError as exc:
                raise RepositoryError(503, exc.code, exc.message) from exc
        relay_status = str(relay_result.get("status") or "pending")
        if relay_status == "pending":
            return self.personal_feishu_authorization(identity)
        if relay_status == "authorized" and str(relay_result.get("code") or ""):
            return self.complete_personal_feishu_authorization(
                state_token=state_token,
                code=str(relay_result["code"]),
            )

        error_message = str(
            relay_result.get("errorMessage")
            or (
                "飞书授权会话已过期，请重新发起授权"
                if relay_status == "expired"
                else "飞书没有完成本次授权，请重新发起"
            )
        )
        cleaned_secret = {
            key: value
            for key, value in current_secret.items()
            if key not in {"pendingRelayState", "pendingRelayClaimSecret"}
        }
        previous_binding_ready = bool(
            current.get("linked")
            and current.get("openId")
            and cleaned_secret.get("accessToken")
        )
        public_config = {
            key: value
            for key, value in current.items()
            if key
            not in {
                "pendingState",
                "pendingStateExpiresAt",
                "pendingCallbackUrl",
                "updatedAt",
                "version",
                "expectedVersion",
                "effectiveScopeKind",
                "defaultWriteScope",
                "scopeVersions",
                "hasCredentials",
                "secretFingerprint",
            }
        }
        public_config.update(
            {
                "linked": previous_binding_ready,
                "authorizationState": (
                    "ready" if previous_binding_ready else "failed_retryable"
                ),
                "lastError": error_message,
            }
        )
        self.configurations.upsert(
            identity,
            configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
            scope_kind="personal",
            provider="feishu",
            public_config=public_config,
            expected_version=int(current.get("version") or 0),
            idempotency_key=f"{idempotency_key}:relay-terminal",
            secret_bundle=cleaned_secret or None,
            secret_action="replace" if cleaned_secret else "clear",
        )
        return self.personal_feishu_authorization(identity)

    def clear_personal_feishu_authorization(
        self,
        identity: SessionIdentity,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        personal_identity = self._personal_identity(identity)
        current = self._feishu_member_configuration(identity)
        self.configurations.upsert(
            identity,
            configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
            scope_kind="personal",
            provider="feishu",
            public_config={
                "linked": False,
                "authorizationState": "revoked",
                "appId": current.get("appId") or "",
                "lastError": None,
            },
            expected_version=int(current.get("version") or 0),
            idempotency_key=f"{idempotency_key}:configuration",
            secret_action="clear",
        )
        self._revoke_personal_feishu_authorization_grant(identity)
        self._record_command(
            personal_identity,
            command_type="feishu.personal_authorization.clear",
            aggregate_type="personal_provider_authorization",
            aggregate_id=self._personal_feishu_remote_id(identity),
            payload={
                "organizationId": identity.organization_id,
                "membershipId": identity.membership_id,
            },
            idempotency_key=idempotency_key,
            provider="feishu",
            resource_kind="member_authorization",
            remote_id=self._personal_feishu_remote_id(identity),
            outcome="succeeded",
            retention_state="revoked",
        )
        return self.personal_feishu_authorization(identity)

    def personal_feishu_delivery_profile(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        authorization = self.personal_feishu_authorization(identity)
        configured = self.configurations.read(
            identity,
            configuration_kind=FEISHU_MEMBER_DELIVERY_PROFILE_KIND,
            defaults={
                "receiveId": None,
                "receiveIdType": "open_id",
                "deliveryStatus": "integration_pending",
            },
            personal_only=True,
        )
        profile_secret = self._feishu_delivery_profile_secret(identity)
        with self._connection() as connection:
            phone = connection.execute(
                """
                SELECT normalized_contact
                FROM principals
                WHERE parent_principal_id = ? AND principal_kind='contact'
                  AND contact_type = 'phone' AND verification_state='verified'
                  AND lifecycle_state='active'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (identity.principal_id,),
            ).fetchone()
        identity_mobile = (
            str(phone["normalized_contact"]) if phone is not None else ""
        )
        mobile = str((profile_secret or {}).get("mobile") or identity_mobile)
        receive_id = (
            str(authorization.get("openId") or "")
            if authorization.get("linked")
            else str(configured.get("receiveId") or "")
        )
        if receive_id:
            delivery_status = "matched"
            status_label = "当前成员飞书接收身份已验证"
            blocked_reason = None
            state = "ready"
        elif not mobile:
            delivery_status = "missing_mobile"
            status_label = "当前个人身份没有已验证手机号，请先完成飞书授权"
            blocked_reason = "verified_mobile_missing"
            state = "not_connected"
        else:
            delivery_status = str(
                configured.get("deliveryStatus") or "integration_pending"
            )
            status_label = str(
                configured.get("deliveryStatusLabel")
                or "可使用已验证手机号匹配飞书接收身份"
            )
            blocked_reason = str(
                configured.get("blockedReason")
                or "feishu_remote_verification_required"
            )
            state = (
                "failed_retryable"
                if delivery_status == "failed"
                else "not_connected"
            )
        return {
            "userId": identity.membership_id,
            "organizationId": identity.organization_id,
            "organizationName": None,
            "mobile": mobile,
            "normalizedMobile": mobile or None,
            "deliveryStatus": delivery_status,
            "deliveryStatusLabel": status_label,
            "readyForNotifications": bool(receive_id),
            "receiveId": receive_id or None,
            "receiveIdType": "open_id",
            "lastVerifiedAt": (
                authorization.get("lastVerifiedAt")
                or configured.get("lastVerifiedAt")
            ),
            "lastError": configured.get("lastError"),
            "blockedReason": blocked_reason,
            "state": state,
            "retryable": not bool(receive_id),
            "authorizationScope": "personal",
        }

    def save_personal_feishu_delivery_profile(
        self,
        identity: SessionIdentity,
        *,
        mobile: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        personal_identity = self._personal_identity(identity)
        presented_mobile = mobile.strip()
        normalized_mobile = ""
        if presented_mobile:
            try:
                normalized_mobile = normalize_phone(presented_mobile)
            except ValueError as exc:
                raise RepositoryError(
                    422,
                    "feishu_delivery_mobile_invalid",
                    "请输入包含国家或地区代码的有效手机号",
                ) from exc
        current_configuration = self.configurations.read(
            identity,
            configuration_kind=FEISHU_MEMBER_DELIVERY_PROFILE_KIND,
            defaults={},
            personal_only=True,
        )
        current_public = {
            key: value
            for key, value in current_configuration.items()
            if key
            not in {
                "updatedAt",
                "version",
                "expectedVersion",
                "effectiveScopeKind",
                "defaultWriteScope",
                "scopeVersions",
                "hasCredentials",
                "secretFingerprint",
                "receiveId",
                "receiveIdType",
                "lastVerifiedAt",
                "lastError",
                "blockedReason",
            }
        }
        self.configurations.upsert(
            identity,
            configuration_kind=FEISHU_MEMBER_DELIVERY_PROFILE_KIND,
            scope_kind="personal",
            provider="feishu",
            public_config={
                **current_public,
                "customMobileHash": (
                    sha256_text(normalized_mobile)
                    if normalized_mobile
                    else None
                ),
                "hasCustomMobile": bool(normalized_mobile),
                "deliveryStatus": (
                    "integration_pending"
                    if normalized_mobile
                    else "missing_mobile"
                ),
                "deliveryStatusLabel": (
                    "正在使用新手机号校验飞书接收身份"
                    if normalized_mobile
                    else "已清除自定义飞书手机号"
                ),
            },
            expected_version=int(current_configuration.get("version") or 0),
            idempotency_key=f"{idempotency_key}:mobile",
            secret_bundle=(
                {"mobile": normalized_mobile}
                if normalized_mobile
                else None
            ),
            secret_action="replace" if normalized_mobile else "clear",
        )
        profile = self.personal_feishu_delivery_profile(identity)
        if profile["readyForNotifications"] and not normalized_mobile:
            self._record_command(
                personal_identity,
                command_type="feishu.personal_delivery_profile.verify",
                aggregate_type="personal_delivery_profile",
                aggregate_id=self._personal_feishu_remote_id(identity),
                payload={
                    "organizationId": identity.organization_id,
                    "membershipId": identity.membership_id,
                    "identitySource": "member_oauth",
                },
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind="member_delivery_profile",
                remote_id=self._personal_feishu_remote_id(identity),
                outcome="succeeded",
                retention_state="verified",
            )
            return self.personal_feishu_delivery_profile(identity)
        mobile = str(profile.get("normalizedMobile") or "")
        if not mobile:
            self._record_command(
                personal_identity,
                command_type="feishu.personal_delivery_profile.verify",
                aggregate_type="personal_delivery_profile",
                aggregate_id=self._personal_feishu_remote_id(identity),
                payload={
                    "organizationId": identity.organization_id,
                    "membershipId": identity.membership_id,
                    "mobilePresented": bool(normalized_mobile),
                    "verifiedIdentityMobileAvailable": False,
                },
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind="member_delivery_profile",
                remote_id=self._personal_feishu_remote_id(identity),
                outcome="blocked",
                retention_state="verified_contact_missing",
                error_code="verified_mobile_missing",
                error_message="当前个人身份没有已验证手机号，请先完成飞书授权",
            )
            return self.personal_feishu_delivery_profile(identity)
        integration = self.feishu_integration(identity)
        if integration["state"] != "ready":
            self._record_command(
                personal_identity,
                command_type="feishu.personal_delivery_profile.verify",
                aggregate_type="personal_delivery_profile",
                aggregate_id=self._personal_feishu_remote_id(identity),
                payload={
                    "organizationId": identity.organization_id,
                    "membershipId": identity.membership_id,
                    "mobileHash": sha256_text(mobile),
                },
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind="member_delivery_profile",
                remote_id=self._personal_feishu_remote_id(identity),
                outcome="blocked",
                retention_state="application_not_ready",
                error_code="feishu_application_not_ready",
                error_message=str(integration["lastValidationMessage"]),
            )
            return self.personal_feishu_delivery_profile(identity)
        organization_configuration = self._feishu_configuration(identity)
        try:
            tenant_token = self._feishu_tenant_access_token(
                identity,
                organization_configuration,
            )
            matched = self._feishu_provider_json(
                "POST",
                FEISHU_CONTACT_LOOKUP_URL,
                access_token=tenant_token,
                params={"user_id_type": "open_id"},
                payload={
                    "mobiles": [mobile],
                    "include_resigned": False,
                },
            )
            data = matched.get("data")
            user_list = (
                data.get("user_list")
                if isinstance(data, Mapping)
                and isinstance(data.get("user_list"), list)
                else []
            )
            first = (
                user_list[0]
                if user_list and isinstance(user_list[0], Mapping)
                else {}
            )
            receive_id = str(
                first.get("open_id") or first.get("user_id") or ""
            ).strip()
            if not receive_id:
                raise _FeishuExecutionError(
                    "feishu_member_not_matched",
                    "已验证手机号未匹配到可接收消息的飞书成员",
                )
        except _FeishuExecutionError as exc:
            self._record_command(
                personal_identity,
                command_type="feishu.personal_delivery_profile.verify",
                aggregate_type="personal_delivery_profile",
                aggregate_id=self._personal_feishu_remote_id(identity),
                payload={
                    "organizationId": identity.organization_id,
                    "membershipId": identity.membership_id,
                    "mobileHash": sha256_text(mobile),
                },
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind="member_delivery_profile",
                remote_id=self._personal_feishu_remote_id(identity),
                outcome="failed_retryable",
                retention_state="failed_retryable",
                error_code=exc.code,
                error_message=exc.message,
            )
            return {
                **self.personal_feishu_delivery_profile(identity),
                "deliveryStatus": "failed",
                "deliveryStatusLabel": exc.message,
                "lastError": exc.message,
                "blockedReason": exc.code,
                "state": "failed_retryable",
            }
        current = self.configurations.read(
            identity,
            configuration_kind=FEISHU_MEMBER_DELIVERY_PROFILE_KIND,
            defaults={},
            personal_only=True,
        )
        self.configurations.upsert(
            identity,
            configuration_kind=FEISHU_MEMBER_DELIVERY_PROFILE_KIND,
            scope_kind="personal",
            provider="feishu",
            public_config={
                "receiveId": receive_id,
                "receiveIdType": "open_id",
                "deliveryStatus": "matched",
                "deliveryStatusLabel": "已通过账号中验证手机号匹配飞书成员",
                "lastVerifiedAt": utc_now(),
                "lastError": None,
                "blockedReason": None,
                "contactHash": sha256_text(mobile),
            },
            expected_version=int(current.get("version") or 0),
            idempotency_key=f"{idempotency_key}:configuration",
            secret_action="preserve",
        )
        self._record_command(
            personal_identity,
            command_type="feishu.personal_delivery_profile.verify",
            aggregate_type="personal_delivery_profile",
            aggregate_id=self._personal_feishu_remote_id(identity),
            payload={
                "organizationId": identity.organization_id,
                "membershipId": identity.membership_id,
                "mobilePresented": bool(normalized_mobile),
                "mobileHash": sha256_text(mobile),
                "receiveIdHash": sha256_text(receive_id),
            },
            idempotency_key=idempotency_key,
            provider="feishu",
            resource_kind="member_delivery_profile",
            remote_id=self._personal_feishu_remote_id(identity),
            outcome="succeeded",
            retention_state="verified",
        )
        return self.personal_feishu_delivery_profile(identity)

    def _finalize_feishu_message_delivery(
        self,
        identity: SessionIdentity,
        *,
        operation_id: str,
        outcome: str,
        result: Mapping[str, Any],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        result_json = canonical_json(dict(result))
        now = utc_now()
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """
                    SELECT c.command_id, c.scope_id, c.aggregate_type,
                           c.aggregate_id, c.command_type,
                           c.actor_principal_id, c.idempotency_key,
                           r.provider_resource_id
                    FROM command_envelopes AS c
                    JOIN external_provider_resources AS r
                      ON r.scope_id = c.scope_id
                     AND r.organization_id = c.organization_id
                     AND r.provider = 'feishu'
                     AND r.resource_kind = 'member_message_delivery'
                     AND r.remote_id = c.aggregate_id
                    WHERE c.operation_id = ? AND c.organization_id = ?
                    LIMIT 1
                    """,
                    (operation_id, identity.organization_id),
                ).fetchone()
                if row is None:
                    raise RepositoryError(
                        500,
                        "feishu_message_operation_missing",
                        "飞书消息操作回执不存在",
                    )
                connection.execute(
                    """
                    UPDATE operation_attempts
                    SET transport_state = ?, error_code = ?,
                        error_message = ?
                    WHERE command_id = ? AND scope_id = ?
                    """,
                    (
                        outcome,
                        error_code,
                        error_message,
                        row["command_id"],
                        row["scope_id"],
                    ),
                )
                connection.execute(
                    """
                    UPDATE delivery_outbox
                    SET status = ?, updated_at = ?
                    WHERE scope_id = ? AND operation_id = ?
                    """,
                    (
                        "delivered" if outcome == "succeeded" else "failed",
                        now,
                        row["scope_id"],
                        operation_id,
                    ),
                )
                connection.execute(
                    """
                    UPDATE external_provider_resources
                    SET retention_state = ?, version = version + 1,
                        updated_at = ?
                    WHERE provider_resource_id = ?
                    """,
                    (
                        "delivered"
                        if outcome == "succeeded"
                        else "failed_retryable",
                        now,
                        row["provider_resource_id"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO external_side_effects (
                        effect_id, scope_id, organization_id, operation_id,
                        provider_resource_id, effect_kind, outcome,
                        receipt_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?,
                              'feishu.personal_message.send', ?, ?, ?)
                    """,
                    (
                        new_id(),
                        row["scope_id"],
                        identity.organization_id,
                        operation_id,
                        row["provider_resource_id"],
                        outcome,
                        sha256_text(result_json),
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE command_idempotency
                    SET result_json = ?, result_hash = ?
                    WHERE scope_id = ? AND actor_principal_id = ?
                      AND command_type = ? AND idempotency_key = ?
                    """,
                    (
                        result_json,
                        sha256_text(result_json),
                        row["scope_id"],
                        row["actor_principal_id"],
                        row["command_type"],
                        row["idempotency_key"],
                    ),
                )
                if outcome != "succeeded":
                    connection.execute(
                        """
                        INSERT INTO operation_dead_letters (
                            dead_letter_id, scope_id, organization_id,
                            operation_id, aggregate_type, aggregate_id,
                            error_code, error_message, status,
                            created_at, resolved_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, NULL)
                        """,
                        (
                            new_id(),
                            row["scope_id"],
                            identity.organization_id,
                            operation_id,
                            row["aggregate_type"],
                            row["aggregate_id"],
                            error_code or "feishu_message_send_failed",
                            error_message or "飞书消息发送失败",
                            now,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return dict(result)

    def send_personal_feishu_text(
        self,
        identity: SessionIdentity,
        *,
        text: str,
        local_type: str,
        local_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_text = text.strip()
        if not normalized_text:
            raise RepositoryError(
                422,
                "feishu_message_text_required",
                "飞书消息正文不能为空",
            )
        if len(normalized_text.encode("utf-8")) > 120 * 1024:
            raise RepositoryError(
                413,
                "feishu_message_too_large",
                "飞书消息正文过长",
            )
        personal_identity = self._personal_identity(identity)
        profile = self.personal_feishu_delivery_profile(identity)
        safe_payload = {
            "organizationId": identity.organization_id,
            "membershipId": identity.membership_id,
            "localType": local_type,
            "localId": local_id,
            "contentHash": sha256_text(normalized_text),
            "byteSize": len(normalized_text.encode("utf-8")),
        }
        if not profile.get("readyForNotifications"):
            blocked = self._record_command(
                personal_identity,
                command_type="feishu.personal_message.send",
                aggregate_type=local_type,
                aggregate_id=local_id,
                payload=safe_payload,
                idempotency_key=idempotency_key,
                provider="feishu",
                resource_kind="member_message_delivery",
                remote_id=local_id,
                outcome="blocked",
                retention_state="blocked",
                error_code=str(
                    profile.get("blockedReason")
                    or "member_delivery_profile_required"
                ),
                error_message=str(
                    profile.get("deliveryStatusLabel")
                    or "当前成员没有可用的飞书接收身份"
                ),
            )
            return {
                **blocked,
                "status": "skipped",
                "deliveryMode": "none",
                "deliveryTarget": None,
            }
        claim_nonce = secrets.token_urlsafe(12)
        claimed = self._record_command(
            personal_identity,
            command_type="feishu.personal_message.send",
            aggregate_type=local_type,
            aggregate_id=local_id,
            payload=safe_payload,
            idempotency_key=idempotency_key,
            provider="feishu",
            resource_kind="member_message_delivery",
            remote_id=local_id,
            outcome="queued",
            retention_state="sending",
            result_details={
                "claimNonce": claim_nonce,
                "status": "queued",
                "deliveryMode": "member_open_id",
                "deliveryTarget": "current_member",
            },
        )
        if claimed.get("claimNonce") != claim_nonce:
            return claimed
        integration = self.feishu_integration(identity)
        organization_configuration = self._feishu_configuration(identity)
        try:
            if integration["state"] != "ready":
                raise _FeishuExecutionError(
                    "feishu_application_not_ready",
                    str(integration["lastValidationMessage"]),
                )
            tenant_token = self._feishu_tenant_access_token(
                identity,
                organization_configuration,
            )
            provider_result = self._feishu_provider_json(
                "POST",
                FEISHU_MESSAGE_CREATE_URL,
                access_token=tenant_token,
                params={"receive_id_type": "open_id"},
                payload={
                    "receive_id": str(profile["receiveId"]),
                    "msg_type": "text",
                    "content": json.dumps(
                        {"text": normalized_text},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            )
            data = provider_result.get("data")
            message_id = (
                str(data.get("message_id") or "")
                if isinstance(data, Mapping)
                else ""
            )
            if not message_id:
                raise _FeishuExecutionError(
                    "feishu_message_receipt_invalid",
                    "飞书未返回消息回执，请稍后重试",
                )
        except _FeishuExecutionError as exc:
            failed = {
                "operationId": claimed["operationId"],
                "state": "failed_retryable",
                "status": "failed",
                "errorCode": exc.code,
                "message": exc.message,
                "retryable": True,
                "deliveryMode": "member_open_id",
                "deliveryTarget": "current_member",
            }
            return self._finalize_feishu_message_delivery(
                personal_identity,
                operation_id=str(claimed["operationId"]),
                outcome="failed_retryable",
                result=failed,
                error_code=exc.code,
                error_message=exc.message,
            )
        succeeded = {
            "operationId": claimed["operationId"],
            "state": "succeeded",
            "status": "sent",
            "errorCode": None,
            "message": "飞书消息已发送给当前成员",
            "retryable": False,
            "deliveryMode": "member_open_id",
            "deliveryTarget": "current_member",
            "remoteId": message_id,
        }
        return self._finalize_feishu_message_delivery(
            personal_identity,
            operation_id=str(claimed["operationId"]),
            outcome="succeeded",
            result=succeeded,
        )

    def _identity_for_membership(
        self,
        identity: SessionIdentity,
        membership_id: str,
    ) -> SessionIdentity | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT m.id AS membership_id,m.principal_id,m.role_key,
                       m.visibility_scope,p.display_name
                FROM organization_memberships AS m
                JOIN principals AS p ON p.id=m.principal_id
                WHERE m.id=? AND m.scope_id=? AND m.status='active'
                  AND m.lifecycle_state='active' AND p.status='active'
                LIMIT 1
                """,
                (membership_id, identity.scope_id),
            ).fetchone()
        if row is None:
            return None
        return SessionIdentity(
            session_id=identity.session_id,
            principal_id=str(row["principal_id"]),
            membership_id=str(row["membership_id"]),
            organization_id=identity.organization_id,
            cloud_instance_id=identity.cloud_instance_id,
            scope_id=identity.scope_id,
            system_role=str(row["role_key"]),
            visibility_scope=str(row["visibility_scope"]),
            display_name=str(row["display_name"]),
        )

    def deliver_task_notifications(
        self,
        identity: SessionIdentity,
        *,
        result: Mapping[str, Any],
        event: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Deliver task notifications after the task authority commits.

        Provider failures are deliberately converted into delivery terminal
        states and never propagate back into the task transaction.
        """

        task = result.get("task")
        if not isinstance(task, Mapping):
            return {
                "state": "not_requested",
                "requestedRecipients": 0,
                "deliveryCount": 0,
                "partialSuccess": False,
                "message": "本次没有可通知任务",
            }
        task_id = str(task.get("id") or "")
        task_version = int(task.get("version") or 1)
        title = str(task.get("title") or "未命名任务")
        creator_id = str(task.get("creator_membership_id") or "")
        collaborators = task.get("collaborators")
        recipients: set[str] = set()
        if event == "returned":
            if creator_id:
                recipients.add(creator_id)
        elif isinstance(collaborators, list):
            for item in collaborators:
                if not isinstance(item, Mapping):
                    continue
                role_key = str(item.get("role_key") or "")
                assignment_state = str(item.get("assignment_state") or "")
                # A newly invited owner must receive the invitation before accepting.
                # Ordinary collaborators remain hidden until the owner has accepted.
                if role_key == "owner":
                    if assignment_state not in {"assigned", "awaiting_owner"}:
                        continue
                elif assignment_state != "assigned":
                    continue
                membership_id = str(item.get("subject_membership_id") or "")
                if membership_id:
                    recipients.add(membership_id)
        action_label = {
            "created": "新任务",
            "updated": "任务已更新",
            "accepted": "任务已接受",
            "returned": "任务已退回",
            "transferred": "任务负责人已变更",
        }.get(event, "任务有新变化")
        due_at = str(
            task.get("scheduled_start_at")
            or task.get("due_date")
            or ""
        )
        message_text = f"【{action_label}】{title}"
        if due_at:
            message_text += f"\n时间：{due_at}"
        message_text += f"\n来自：{identity.display_name}"
        deliveries: list[dict[str, Any]] = []
        now = utc_now()
        for membership_id in sorted(recipients):
            target = self._identity_for_membership(identity, membership_id)
            if target is None:
                deliveries.append(
                    {"membershipId": membership_id, "status": "blocked", "message": "成员已不可用"}
                )
                continue
            try:
                profile = self.personal_feishu_delivery_profile(target)
                if not profile.get("readyForNotifications"):
                    profile = self.save_personal_feishu_delivery_profile(
                        target,
                        mobile="",
                        idempotency_key=f"{idempotency_key}:resolve:{membership_id}",
                    )
                sent = self.send_personal_feishu_text(
                    target,
                    text=message_text,
                    local_type="task",
                    local_id=task_id,
                    idempotency_key=(
                        f"{idempotency_key}:feishu:{event}:{task_version}:{membership_id}"
                    ),
                )
                status = "sent" if sent.get("state") == "succeeded" else (
                    "failed_retryable" if sent.get("retryable") else "blocked"
                )
                delivery = {
                    "membershipId": membership_id,
                    "displayName": target.display_name,
                    "status": status,
                    "message": str(sent.get("message") or ""),
                    "remoteId": sent.get("remoteId"),
                }
            except Exception as exc:  # provider errors never roll back task facts
                delivery = {
                    "membershipId": membership_id,
                    "displayName": target.display_name,
                    "status": "failed_retryable",
                    "message": str(exc) or "飞书通知发送失败，可重试",
                    "remoteId": None,
                }
            deliveries.append(delivery)
            delivery_id = "notify_" + sha256_text(
                f"{identity.scope_id}\x1f{task_id}\x1f{event}\x1f{task_version}\x1f{membership_id}\x1ffeishu"
            )[:30]
            receipt = canonical_json(
                {
                    "event": event,
                    "taskVersion": task_version,
                    "providerReceiptHash": (
                        sha256_text(str(delivery.get("remoteId")))
                        if delivery.get("remoteId")
                        else None
                    ),
                    "message": delivery.get("message"),
                }
            )
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO notification_deliveries (
                        id,scope_id,external_side_effect_id,channel,remote_receipt,
                        status,recipient_ref_hash,sent_at,delivered_at,next_retry_at,
                        version,lifecycle_state,created_at,updated_at,deleted_at
                    ) VALUES (?,?,NULL,'feishu',?,?,?,?,?,?,1,'active',?,?,NULL)
                    ON CONFLICT(id) DO UPDATE SET
                        remote_receipt=excluded.remote_receipt,
                        status=excluded.status,
                        sent_at=excluded.sent_at,
                        delivered_at=excluded.delivered_at,
                        next_retry_at=excluded.next_retry_at,
                        version=notification_deliveries.version+1,
                        lifecycle_state='active',updated_at=excluded.updated_at,
                        deleted_at=NULL
                    """,
                    (
                        delivery_id,
                        identity.scope_id,
                        receipt,
                        delivery["status"],
                        sha256_text(f"{identity.scope_id}\x1f{membership_id}"),
                        now if delivery["status"] == "sent" else None,
                        now if delivery["status"] == "sent" else None,
                        now if delivery["status"] == "failed_retryable" else None,
                        now,
                        now,
                    ),
                )
                connection.commit()
        sent_count = sum(item["status"] == "sent" for item in deliveries)
        failed_count = sum(item["status"] == "failed_retryable" for item in deliveries)
        if not deliveries:
            state = "not_requested"
        elif sent_count == len(deliveries):
            state = "completed"
        elif sent_count:
            state = "partial"
        elif failed_count:
            state = "failed_retryable"
        else:
            state = "blocked"
        return {
            "state": state,
            "requestedRecipients": len(deliveries),
            "deliveryCount": sent_count,
            "partialSuccess": bool(sent_count and sent_count < len(deliveries)),
            "message": (
                "飞书通知已发送"
                if state == "completed"
                else "任务已生效；部分或全部成员的飞书通知尚未送达"
            ),
            "deliveries": deliveries,
        }

    def query(
        self,
        identity: SessionIdentity,
        *,
        resource_path: str,
        authorization_scope: str,
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = resource_path.strip("/")
        if path in {"agent-run-logs"}:
            raise RepositoryError(
                409,
                "platform_projection_strict_adapter_not_connected",
                "该平台投影尚未迁入88表，已阻止读取冻结旧表",
            )
        if path == "me/feishu-authorization":
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            return self.personal_feishu_authorization(identity)
        if path == "me/feishu-delivery-profile":
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            return self.personal_feishu_delivery_profile(identity)
        if (
            path == "feishu-sync/status"
            and str(query.get("remoteType") or "") == "docx_document"
        ):
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            return self.feishu_sync_status(
                identity,
                local_type=str(query.get("localType") or ""),
                local_id=str(query.get("localId") or ""),
                remote_type="docx_document",
            )
        self._require_scope(
            authorization_scope,
            expected="organization",
            resource_path=path,
        )
        if path == "org-integrations/feishu":
            return {
                **self.feishu_integration(identity),
                "authorizationScope": "organization",
            }
        if path == "feishu-sync/status":
            return self.feishu_sync_status(
                identity,
                local_type=str(query.get("localType") or ""),
                local_id=str(query.get("localId") or ""),
                remote_type=str(query.get("remoteType") or "calendar_event"),
            )
        if path == "feishu-doc-import/status":
            integration = self.feishu_integration(identity)
            ready = bool(integration["state"] == "ready")
            if integration["state"] != "ready":
                reason = integration["lastValidationMessage"]
                blocker_type = (
                    "configuration_missing"
                    if integration["state"] == "not_connected"
                    else "provider_validation_failed"
                )
                state = integration["state"]
            else:
                reason = None
                blocker_type = None
                state = "ready"
            return {
                "ready": ready,
                "linked": ready,
                "reason": reason,
                "organizationId": identity.organization_id,
                "userId": identity.membership_id,
                "boundAt": integration.get("configuredAt"),
                "state": state,
                "retryable": not ready,
                "pollingEnabled": False,
                "blockerType": blocker_type,
                "accessMode": "organization_application_one_time_copy",
            }
        if path == "support-requests":
            return {
                "items": self.list_support_requests(
                    identity,
                    status=str(query.get("status") or ""),
                    task_id=str(query.get("taskId") or ""),
                )
            }
        if path == "software-feedback":
            return self.list_feedback(identity)
        if path == "logs":
            return self._query_logs(identity, query)
        if path == "logs/dates":
            return {
                "dates": sorted(
                    {
                        str(entry.get("timestamp") or "")[:10]
                        for entry in self.operation_logs(identity, limit=500)
                        if entry.get("timestamp")
                    },
                    reverse=True,
                )
            }
        if path == "agent-run-logs":
            limit = self._safe_int(query.get("limit"), default=50, maximum=500)
            client_id = str(query.get("client_id") or "")
            actor_type = str(query.get("actor_type") or "")
            rows = self.operation_logs(identity, limit=limit)
            items = [
                {
                    "id": row["id"],
                    "tool_name": row.get("action"),
                    "actor_type": "user",
                    "actor_id": identity.principal_id,
                    "client_id": row.get("entity_id") if client_id else None,
                    "status": (row.get("detail") or {}).get("state"),
                    "triggered_at": row.get("timestamp"),
                    "error_message": (
                        row.get("message") if row.get("level") == "ERROR" else None
                    ),
                }
                for row in rows
            ]
            return {
                "filter": {
                    "client_id": client_id or None,
                    "actor_type": actor_type or None,
                    "limit": limit,
                },
                "total": len(items),
                "items": items,
            }
        if path == "tool-registry":
            result = self.tool_registry()
            status_filter = str(query.get("status_filter") or "")
            risk_level = str(query.get("risk_level") or "")
            tools = [
                item
                for item in result["tools"]
                if (not status_filter or item.get("status") == status_filter)
                and (not risk_level or item.get("risk_level") == risk_level)
            ]
            return {**result, "total": len(tools), "tools": tools}
        if path == "system/active-background-tasks":
            return self.runtime_diagnostics.active_background_tasks(identity)
        if path == "runtime/generation-state":
            return self.runtime_diagnostics.generation_state(identity, query)
        if path == "runtime/analysis-migration-metrics":
            return self.runtime_diagnostics.analysis_metrics(identity)
        run_match = re.fullmatch(r"runtime/run-log/([^/]+)", path)
        if run_match:
            return self.runtime_diagnostics.run_log(identity, run_match.group(1))
        if path == "runtime/workspace-chat-diagnostics":
            return self.runtime_diagnostics.workspace_chat(identity, query)
        if path == "runtime/workspace-answer-value-diagnostics":
            return self.runtime_diagnostics.workspace_answers(identity, query)
        raise RepositoryError(
            404,
            "platform_resource_unknown",
            f"未知的平台能力资源：{path or '<empty>'}",
        )

    def command(
        self,
        identity: SessionIdentity,
        *,
        resource_path: str,
        authorization_scope: str,
        method: str,
        query: Mapping[str, Any],
        payload: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        path = resource_path.strip("/")
        command_key = idempotency_key or new_id()
        if method not in {"POST", "PUT", "PATCH", "DELETE"}:
            raise RepositoryError(
                405,
                "platform_command_method_invalid",
                f"平台命令不支持 {method}",
            )
        if path == "me/feishu-authorization/start":
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            return self.start_personal_feishu_authorization(
                identity,
                idempotency_key=command_key,
            )
        if path == "me/feishu-authorization/claim":
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            return self.claim_personal_feishu_authorization(
                identity,
                idempotency_key=command_key,
            )
        if path == "me/feishu-authorization" and method == "DELETE":
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            return self.clear_personal_feishu_authorization(
                identity,
                idempotency_key=command_key,
            )
        if path == "me/feishu-delivery-profile":
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            return self.save_personal_feishu_delivery_profile(
                identity,
                mobile=str(payload.get("mobile") or ""),
                idempotency_key=command_key,
            )
        if path == "me/feishu-message/send":
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            profile = self.personal_feishu_delivery_profile(identity)
            if not profile.get("readyForNotifications"):
                profile = self.save_personal_feishu_delivery_profile(
                    identity,
                    mobile="",
                    idempotency_key=f"{command_key}:resolve-recipient",
                )
            return self.send_personal_feishu_text(
                identity,
                text=str(payload.get("text") or ""),
                local_type=str(payload.get("localType") or "notification"),
                local_id=str(payload.get("localId") or new_id()),
                idempotency_key=command_key,
            )
        if path in {
            "feishu-doc-import/search",
            "feishu-doc-import/resolve-links",
            "feishu-doc-import/fetch",
            "feishu-doc-import/register-mapping",
        }:
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            if path == "feishu-doc-import/register-mapping":
                return self.register_feishu_import_mapping(
                    identity,
                    payload=payload,
                    idempotency_key=command_key,
                )
            action = {
                "feishu-doc-import/search": "search",
                "feishu-doc-import/resolve-links": "resolve_links",
                "feishu-doc-import/fetch": "fetch",
            }[path]
            return self.request_feishu_import(
                identity,
                action=action,
                payload=payload,
                idempotency_key=command_key,
            )
        if path == "feishu-sync/documents":
            self._require_scope(
                authorization_scope,
                expected="personal",
                resource_path=path,
            )
            raise RepositoryError(
                410,
                "feishu_document_reverse_projection_retired",
                "本地文件不再创建或持续同步飞书云文档；请使用一次性飞书文档导入",
            )
        self._require_scope(
            authorization_scope,
            expected="organization",
            resource_path=path,
        )
        if path == "org-integrations/feishu/validate-and-save":
            return self.save_feishu(identity, payload, command_key)
        sync_task = re.fullmatch(r"feishu-sync/calendar/tasks/([^/]+)", path)
        if sync_task:
            raise RepositoryError(
                410,
                "feishu_calendar_projection_retired",
                "飞书日历与手机系统日历同步已取消；软件任务仍可发送飞书通知",
            )
        if path == "support-requests":
            return self.create_support_request(identity, payload, command_key)
        resolve_match = re.fullmatch(r"support-requests/([^/]+)/resolve", path)
        if resolve_match:
            return self.resolve_support_request(
                identity,
                resolve_match.group(1),
                payload,
                command_key,
            )
        if path == "software-feedback":
            return self.create_feedback(identity, payload, command_key)
        if path == "runtime/generation-state/reset":
            client_id = str(payload.get("clientId") or "")
            answer_intent = str(payload.get("answerIntent") or "general")
            receipt = self._record_command(
                identity,
                command_type="runtime.generation_state.reset",
                aggregate_type="runtime_generation_projection",
                aggregate_id=f"{client_id}:{answer_intent}",
                payload={
                    "clientId": client_id,
                    "answerIntent": answer_intent,
                },
                idempotency_key=command_key,
                provider="yiyu_runtime",
                resource_kind="generation_projection",
                remote_id=f"{identity.membership_id}:{client_id}:{answer_intent}",
                outcome="succeeded",
            )
            return {
                **self.runtime_diagnostics.generation_state(identity, payload),
                "operationId": receipt["operationId"],
                "stableFallbackReason": None,
                "state": "reset",
            }
        raise RepositoryError(
            404,
            "platform_command_unknown",
            f"未知的平台能力命令：{path or '<empty>'}",
        )

    @staticmethod
    def _safe_int(value: Any, *, default: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, min(parsed, maximum))

    def _query_logs(
        self,
        identity: SessionIdentity,
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        limit = self._safe_int(query.get("limit"), default=200, maximum=500)
        entries = self.operation_logs(identity, limit=limit)
        start_date = str(query.get("startDate") or "")
        end_date = str(query.get("endDate") or "")
        level = str(query.get("level") or "")
        source = str(query.get("source") or "")
        keyword = str(query.get("keyword") or "").strip().lower()
        entries = [
            entry
            for entry in entries
            if (not start_date or str(entry.get("timestamp") or "")[:10] >= start_date)
            and (not end_date or str(entry.get("timestamp") or "")[:10] <= end_date)
            and (not level or str(entry.get("level") or "").lower() == level.lower())
            and (not source or str(entry.get("source") or "") == source)
            and (
                not keyword
                or keyword
                in (
                    f"{entry.get('message') or ''} "
                    f"{entry.get('action') or ''} "
                    f"{entry.get('entity_id') or ''}"
                ).lower()
            )
        ]
        dates = sorted(
            {
                str(entry.get("timestamp") or "")[:10]
                for entry in entries
                if entry.get("timestamp")
            },
            reverse=True,
        )
        return {"entries": entries, "dates": dates, "total": len(entries)}

    def _generation_state(
        self,
        identity: SessionIdentity,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        client_id = str(values.get("clientId") or "")
        answer_intent = str(values.get("answerIntent") or "general")
        with self._connection() as connection:
            reset = connection.execute(
                """
                SELECT created_at
                FROM command_envelopes
                WHERE scope_id = ? AND organization_id = ?
                  AND actor_principal_id = ?
                  AND command_type = 'runtime.generation_state.reset'
                  AND json_extract(payload_json, '$.clientId') = ?
                  AND json_extract(payload_json, '$.answerIntent') = ?
                ORDER BY created_at DESC, command_id DESC
                LIMIT 1
                """,
                (
                    identity.scope_id,
                    identity.organization_id,
                    identity.principal_id,
                    client_id,
                    answer_intent,
                ),
            ).fetchone()
            boundary = str(reset["created_at"]) if reset is not None else ""
            answers = connection.execute(
                """
                SELECT source_manifest_json
                FROM ai_answers
                WHERE organization_id = ? AND membership_id = ?
                  AND lifecycle_state = 'active'
                  AND (? = '' OR project_id = ?)
                  AND (? = '' OR created_at > ?)
                ORDER BY created_at DESC
                LIMIT 200
                """,
                (
                    identity.organization_id,
                    identity.membership_id,
                    client_id,
                    client_id,
                    boundary,
                    boundary,
                ),
            ).fetchall()
            failures = connection.execute(
                """
                SELECT a.error_code
                FROM operation_attempts a
                JOIN command_envelopes c ON c.command_id = a.command_id
                WHERE a.scope_id = ? AND c.organization_id = ?
                  AND c.actor_principal_id = ?
                  AND (
                    c.command_type LIKE 'workbench.%answer%'
                    OR c.command_type LIKE 'runtime.%generation%'
                  )
                  AND c.command_type != 'runtime.generation_state.reset'
                  AND a.error_code IS NOT NULL AND a.error_code != ''
                  AND (? = '' OR a.created_at > ?)
                ORDER BY a.created_at DESC
                LIMIT 200
                """,
                (
                    identity.scope_id,
                    identity.organization_id,
                    identity.principal_id,
                    boundary,
                    boundary,
                ),
            ).fetchall()
            config = connection.execute(
                """
                SELECT provider, model_name, status, updated_at
                FROM organization_ai_configs
                WHERE organization_id = ?
                """,
                (identity.organization_id,),
            ).fetchone()
        manifests = [self._json_text(row["source_manifest_json"]) for row in answers]
        local_fallbacks = sum(
            1
            for manifest in manifests
            if str(manifest.get("answerMode") or "") == "grounded_fallback"
            or bool(manifest.get("localFallback"))
        )
        timeouts = sum(
            1 for row in failures if "timeout" in str(row["error_code"]).lower()
        )
        total = len(answers) + len(failures)
        return {
            "clientId": client_id,
            "answerIntent": answer_intent,
            "provider": (
                str(config["provider"])
                if config is not None
                else values.get("provider") or None
            ),
            "model": (
                str(config["model_name"])
                if config is not None
                else values.get("model") or None
            ),
            "recentTotal": total,
            "recentTimeouts": timeouts,
            "recentLocalFallbacks": local_fallbacks,
            "recentSuccesses": len(answers),
            "stableFallbackActive": False,
            "stableFallbackReason": None,
            "cooldownUntil": None,
            "updatedAt": utc_now(),
            "state": "ready" if total else "ready_empty",
            "projectionSource": [
                "ai_answers",
                "operation_attempts",
                "organization_ai_configs",
            ],
            "resetBoundary": boundary or None,
        }

    def _analysis_migration_metrics(
        self,
        identity: SessionIdentity,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            attempts = connection.execute(
                """
                SELECT state, created_at, finished_at
                FROM processing_attempts
                WHERE organization_id = ?
                  AND created_at >= strftime(
                    '%Y-%m-%dT%H:%M:%fZ', 'now', '-30 days'
                  )
                """,
                (identity.organization_id,),
            ).fetchall()
            documents = connection.execute(
                """
                SELECT parse_state
                FROM knowledge_documents
                WHERE organization_id = ? AND lifecycle_state = 'active'
                """,
                (identity.organization_id,),
            ).fetchall()
            records = connection.execute(
                """
                SELECT status, created_at, updated_at
                FROM intelligence_records
                WHERE organization_id = ? AND status != 'archived'
                """,
                (identity.organization_id,),
            ).fetchall()
        completed = sum(1 for row in attempts if row["state"] == "completed")
        failed = sum(1 for row in attempts if row["state"] == "failed")
        candidates = [row for row in records if row["status"] == "candidate"]
        accepted = [row for row in records if row["status"] == "accepted"]
        warning_count = sum(
            1
            for row in candidates
            if self._age_hours(str(row["created_at"])) >= 24
        )
        overdue_count = sum(
            1
            for row in candidates
            if self._age_hours(str(row["created_at"])) >= 72
        )
        approval_lags = sorted(
            max(
                self._age_hours(
                    str(row["created_at"]),
                    end=str(row["updated_at"]),
                ),
                0.0,
            )
            for row in accepted
        )
        approval_median = (
            approval_lags[len(approval_lags) // 2]
            if approval_lags
            else 0.0
        )
        total_records = len(candidates) + len(accepted)
        has_projection_data = bool(attempts or documents or records)
        return {
            "windowDays": 30,
            "newObjectHitRate": self._ratio(
                sum(1 for row in documents if row["parse_state"] == "ready"),
                len(documents),
            ),
            "fallbackRate": self._ratio(failed, len(attempts)),
            "approvalBacklog": len(candidates),
            "approvalLagHoursMedian": round(approval_median, 2),
            "candidateReviewWarningCount": warning_count,
            "candidateReviewOverdueCount": overdue_count,
            "newCandidateUnreviewed24h": warning_count,
            "candidateToApprovedConversionRate": self._ratio(
                len(accepted),
                total_records,
            ),
            "staleApprovedJudgmentCount": 0,
            "resolverMismatchRate": 0,
            "pageBreakdown": {
                "processingAttempts": {
                    "total": len(attempts),
                    "completed": completed,
                    "failed": failed,
                },
                "knowledgeDocuments": {
                    "total": len(documents),
                    "ready": sum(
                        1 for row in documents if row["parse_state"] == "ready"
                    ),
                },
                "intelligenceRecords": {
                    "candidate": len(candidates),
                    "accepted": len(accepted),
                },
            },
            "state": "ready" if has_projection_data else "ready_empty",
            "message": (
                "指标来自严格处理尝试、文档与情报投影"
                if has_projection_data
                else "当前严格对象中没有可汇总的分析记录"
            ),
            "unavailableMetrics": [
                "staleApprovedJudgmentCount",
                "resolverMismatchRate",
            ],
            "updatedAt": utc_now(),
        }

    @staticmethod
    def _age_hours(start: str, *, end: str | None = None) -> float:
        from datetime import datetime, timezone

        def parse(value: str) -> datetime:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))

        try:
            start_at = parse(start)
            end_at = parse(end) if end else datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return 0.0
        return max((end_at - start_at).total_seconds() / 3600, 0.0)

    def _runtime_run_log(
        self,
        identity: SessionIdentity,
        run_id: str,
    ) -> dict[str, Any]:
        matches = [
            entry
            for entry in self.operation_logs(identity, limit=500)
            if entry["id"] == run_id
            or (entry.get("detail") or {}).get("operationId") == run_id
        ]
        if not matches:
            raise RepositoryError(404, "runtime_run_missing", "未找到该运行记录")
        entry = matches[0]
        return {
            "id": run_id,
            "clientId": str(entry.get("entity_id") or ""),
            "jobId": (entry.get("detail") or {}).get("operationId"),
            "analysisJobId": None,
            "stageRunId": None,
            "contextPackId": None,
            "judgmentVersionId": None,
            "correlationId": (entry.get("detail") or {}).get("operationId"),
            "provider": "platform_integrations",
            "model": None,
            "lane": "cloud_final",
            "cacheHit": False,
            "degraded": entry.get("level") != "INFO",
            "documentCount": 0,
            "evidenceCount": 0,
            "conflictCount": 0,
            "contextTimeRange": None,
            "promptVersion": None,
            "schemaVersion": "strict-v2",
            "summary": str(entry.get("message") or ""),
            "detail": entry.get("detail") or {},
            "createdAt": entry.get("timestamp") or utc_now(),
            "state": "available",
        }

    def _workspace_chat_diagnostics(
        self,
        identity: SessionIdentity,
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        client_id = str(query.get("clientId") or "")
        with self._connection() as connection:
            answers = connection.execute(
                """
                SELECT source_manifest_json
                FROM ai_answers
                WHERE organization_id = ? AND membership_id = ?
                  AND lifecycle_state = 'active'
                  AND (? = '' OR project_id = ?)
                ORDER BY created_at DESC
                LIMIT 200
                """,
                (
                    identity.organization_id,
                    identity.membership_id,
                    client_id,
                    client_id,
                ),
            ).fetchall()
            reviews = connection.execute(
                """
                SELECT source_payload_json
                FROM intelligence_records
                WHERE organization_id = ?
                  AND created_by_membership_id = ?
                  AND record_kind = 'workspace_answer_value_review'
                  AND status != 'archived'
                  AND (? = '' OR project_id = ?)
                ORDER BY created_at DESC
                LIMIT 200
                """,
                (
                    identity.organization_id,
                    identity.membership_id,
                    client_id,
                    client_id,
                ),
            ).fetchall()
            processing = connection.execute(
                """
                SELECT state
                FROM processing_attempts
                WHERE organization_id = ?
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (identity.organization_id,),
            ).fetchall()
            intelligence = connection.execute(
                """
                SELECT status
                FROM intelligence_records
                WHERE organization_id = ? AND status != 'archived'
                  AND (? = '' OR project_id = ?)
                """,
                (identity.organization_id, client_id, client_id),
            ).fetchall()
        answer_manifests = [
            self._json_text(row["source_manifest_json"]) for row in answers
        ]
        review_payloads = [
            self._json_text(row["source_payload_json"]) for row in reviews
        ]
        answer_modes = [
            str(item.get("answerMode") or "unknown")
            for item in review_payloads
        ]
        review_total = len(review_payloads)
        fallback_count = sum(
            1 for mode in answer_modes if mode == "grounded_fallback"
        )
        system_failures = sum(
            1 for mode in answer_modes if mode == "system_failure"
        )
        grounded_answers = sum(
            1
            for manifest in answer_manifests
            if bool(
                manifest.get("documentIds")
                or manifest.get("evidenceLinkIds")
                or manifest.get("sourceIds")
            )
        )
        failed_processing = sum(
            1 for row in processing if row["state"] == "failed"
        )
        has_data = bool(answers or reviews or processing or intelligence)
        root_causes: list[str] = []
        fixes: list[str] = []
        if system_failures:
            root_causes.append("价值评审中存在系统失败回答")
            fixes.append("查看对应回答评审与运行日志后重试")
        if failed_processing:
            root_causes.append("最近处理尝试中存在失败记录")
            fixes.append("从处理尝试或死信入口发起重试")
        return {
            "clientId": client_id,
            "recentMessages": len(answers),
            "groundedFallbackRate": self._ratio(
                fallback_count,
                review_total,
            ),
            "llmTimeoutRate": 0,
            "sourceIntegrityMatch": None,
            "runningBuildVersion": None,
            "expectedBuildVersion": None,
            "dominantLlmErrorKind": None,
            "fallbackTemplateUsedRate": 0,
            "dataCenterPrimaryEnabledRate": self._ratio(
                grounded_answers,
                len(answer_manifests),
            ),
            "partialPreservedRate": 0,
            "systemFailureRate": self._ratio(
                system_failures,
                review_total,
            ),
            "stableFallbackActive": False,
            "stableFallbackReason": None,
            "avgRetrievalMs": 0,
            "avgLlmMs": 0,
            "intentDistribution": (
                {"recorded_answers": len(answers)}
                if answers
                else {}
            ),
            "materialQuality": {
                "pptNoiseRatio": 0,
                "generatedDraftRatio": 0,
                "memoryAnswerRatio": 0,
            },
            "dataCenterQuality": {
                "approvedJudgmentCount": sum(
                    1 for row in intelligence if row["status"] == "accepted"
                ),
                "candidateJudgmentCount": sum(
                    1 for row in intelligence if row["status"] == "candidate"
                ),
                "parseFailedDocuments": failed_processing,
                "contextQuality": (
                    "available" if intelligence or processing else "empty"
                ),
            },
            "breakdown": {
                "strictConnection": {
                    "status": "ready" if has_data else "empty",
                    "details": {
                        "state": "ready" if has_data else "ready_empty",
                        "answers": len(answers),
                        "reviews": len(reviews),
                        "processingAttempts": len(processing),
                    },
                }
            },
            "rootCauseSummary": root_causes,
            "recommendedFixes": fixes,
            "unavailableSignals": [
                "llmLatency",
                "retrievalLatency",
                "sourceBuildComparison",
            ],
            "state": "ready" if has_data else "ready_empty",
        }

    def _workspace_answer_diagnostics(
        self,
        identity: SessionIdentity,
        query: Mapping[str, Any],
    ) -> dict[str, Any]:
        client_id = str(query.get("clientId") or "")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT source_payload_json
                FROM intelligence_records
                WHERE organization_id = ?
                  AND created_by_membership_id = ?
                  AND record_kind = 'workspace_answer_value_review'
                  AND status != 'archived'
                  AND (? = '' OR project_id = ?)
                ORDER BY created_at DESC
                LIMIT 500
                """,
                (
                    identity.organization_id,
                    identity.membership_id,
                    client_id,
                    client_id,
                ),
            ).fetchall()
        items = [self._json_text(row["source_payload_json"]) for row in rows]
        answer_modes = [
            str(item.get("answerMode") or "unknown") for item in items
        ]
        fallback_reasons = [
            str(item.get("fallbackReason") or "")
            for item in items
            if item.get("fallbackReason")
        ]
        presentation_modes = [
            str(item.get("fallbackPresentationMode") or "")
            for item in items
            if item.get("fallbackPresentationMode")
        ]
        retry_count = sum(
            1 for item in items if bool(item.get("shouldShowRetryBanner"))
        )
        low_confidence = sum(
            1 for item in items if item.get("answerMode") == "low_confidence_answer"
        )
        fallback = sum(
            1 for item in items if item.get("answerMode") == "grounded_fallback"
        )
        grounded = sum(
            1 for item in items if item.get("answerMode") == "grounded_answer"
        )
        return {
            "clientId": client_id,
            "recentMessages": len(items),
            "answerModeDistribution": self._distribution(answer_modes),
            "fallbackReasonDistribution": self._distribution(fallback_reasons),
            "fallbackPresentationModeDistribution": self._distribution(
                presentation_modes
            ),
            "retryBannerWouldShowCount": retry_count,
            "retryBannerWouldShowRate": self._ratio(retry_count, len(items)),
            "lowConfidenceCount": low_confidence,
            "groundedFallbackCount": fallback,
            "groundedAnswerCount": grounded,
            "state": "ready" if items else "ready_empty",
            "message": (
                "指标来自已保存的工作台回答价值评审"
                if items
                else "当前没有已保存的工作台回答价值评审"
            ),
            "projectionSource": "intelligence_records.workspace_answer_value_review",
        }
