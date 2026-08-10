from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


class CloudClientError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def normalize_cloud_url(value: str) -> str:
    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CloudClientError(422, "cloud_url_invalid", "组织云地址格式不正确")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CloudClientError(422, "cloud_url_invalid", "组织云地址不能包含凭据或参数")
    return candidate


@dataclass(frozen=True)
class CloudResponse:
    payload: dict[str, Any]
    status_code: int


class CloudClient:
    def __init__(self, base_url: str, *, timeout_seconds: float = 15.0):
        self.base_url = normalize_cloud_url(base_url)
        self.timeout = httpx.Timeout(
            connect=min(timeout_seconds, 5.0),
            read=timeout_seconds,
            write=timeout_seconds,
            pool=5.0,
        )
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        json_body: dict[str, Any] | None = None,
        query_params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        allow_array: bool = False,
    ) -> dict[str, Any] | list[Any]:
        headers = {"Accept": "application/json", "X-Yiyu-Client": "strict-desktop-v1"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = self._client.request(
                method,
                path,
                headers=headers,
                json=json_body,
                params=query_params,
            )
        except httpx.TimeoutException as exc:
            raise CloudClientError(504, "cloud_timeout", "组织云响应超时") from exc
        except httpx.HTTPError as exc:
            raise CloudClientError(503, "cloud_unreachable", "暂时无法连接组织云") from exc
        if response.is_redirect:
            raise CloudClientError(
                409,
                "cloud_redirect_rejected",
                "组织云返回了未授权的地址跳转",
            )
        try:
            payload = response.json() if response.content else {}
        except ValueError as exc:
            raise CloudClientError(
                502,
                "cloud_response_invalid",
                "组织云返回了无法识别的数据",
            ) from exc
        if response.status_code >= 400:
            error = payload.get("error") if isinstance(payload, dict) else None
            code = (
                str(error.get("code"))
                if isinstance(error, dict) and error.get("code")
                else "cloud_request_failed"
            )
            message = (
                str(error.get("message"))
                if isinstance(error, dict) and error.get("message")
                else f"组织云请求失败（{response.status_code}）"
            )
            raise CloudClientError(response.status_code, code, message)
        if not isinstance(payload, dict) and not (
            allow_array and isinstance(payload, list)
        ):
            raise CloudClientError(502, "cloud_response_invalid", "组织云响应结构不正确")
        return payload

    def request_v2(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        json_body: dict[str, Any] | None = None,
        query_params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        allow_array: bool = False,
    ) -> dict[str, Any] | list[Any]:
        normalized_method = method.strip().upper()
        normalized_path = "/" + path.strip().lstrip("/")
        if normalized_method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise CloudClientError(405, "cloud_method_invalid", "组织云请求方法不受支持")
        if not normalized_path.startswith("/api/v2/"):
            raise CloudClientError(
                422,
                "strict_v2_path_required",
                "组织云业务请求只允许严格 /api/v2 路径",
            )
        return self._request(
            normalized_method,
            normalized_path,
            access_token=access_token,
            json_body=json_body,
            query_params=query_params,
            idempotency_key=idempotency_key,
            allow_array=allow_array,
        )

    def handshake(self) -> dict[str, Any]:
        return self._request("GET", "/api/v2/handshake")

    def login(
        self,
        *,
        identifier: str,
        password: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/auth/login",
            json_body={"identifier": identifier, "password": password},
            idempotency_key=idempotency_key,
        )

    def join(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v2/auth/join", json_body=payload)

    def create_organization(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/auth/bootstrap-organization",
            json_body=payload,
        )

    def refresh(
        self,
        refresh_token: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/auth/refresh",
            json_body={"refreshToken": refresh_token},
            idempotency_key=idempotency_key,
        )

    def current_session(self, access_token: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/session/current",
            access_token=access_token,
        )

    def logout(
        self,
        access_token: str,
        *,
        idempotency_key: str | None = None,
    ) -> None:
        self._request(
            "POST",
            "/api/v2/auth/logout",
            access_token=access_token,
            idempotency_key=idempotency_key,
        )

    def organization_snapshot(self, access_token: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/organization/snapshot",
            access_token=access_token,
        )

    def business_snapshot(self, access_token: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/business/snapshot",
            access_token=access_token,
        )

    def project_knowledge_context(
        self,
        access_token: str,
        project_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v2/projects/{project_id}/knowledge-context",
            access_token=access_token,
        )

    def create_event_line(
        self,
        access_token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/event-lines",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def task_detail(self, access_token: str, task_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v2/tasks/{task_id}",
            access_token=access_token,
        )

    def create_task(
        self,
        access_token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/tasks",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def update_task(
        self,
        access_token: str,
        task_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/api/v2/tasks/{task_id}",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def transition_task(
        self,
        access_token: str,
        task_id: str,
        transition: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v2/tasks/{task_id}/{transition}",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def handle_task_inbox(
        self,
        access_token: str,
        task_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v2/tasks/{task_id}/inbox/handle",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def save_workbench_answer(
        self,
        access_token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/workbench/answers",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def ai_config(self, access_token: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/settings/org-ai-config",
            access_token=access_token,
        )

    def ai_runtime_secret(self, access_token: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/settings/org-ai-config/runtime-secret",
            access_token=access_token,
        )

    def ai_routing_runtime_secret(self, access_token: str) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v2/organization-access/settings/ai-routing/runtime-secret",
            access_token=access_token,
        )

    def save_ai_config(
        self,
        access_token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "PUT",
            "/api/v2/settings/org-ai-config",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def create_department(
        self,
        access_token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/organization/departments",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def create_management_title(
        self,
        access_token: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/organization/management-titles",
            access_token=access_token,
            json_body=payload,
            idempotency_key=idempotency_key,
        )

    def create_invite(
        self,
        access_token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v2/organization/invites",
            access_token=access_token,
            json_body=payload,
        )


class CloudClientPool:
    """Reuse one thread-safe HTTP client per normalized organization cloud."""

    def __init__(self, *, timeout_seconds: float = 15.0):
        self._timeout_seconds = timeout_seconds
        self._clients: dict[str, CloudClient] = {}
        self._lock = threading.Lock()
        self._closed = False

    def __call__(self, base_url: str) -> CloudClient:
        normalized = normalize_cloud_url(base_url)
        with self._lock:
            if self._closed:
                raise CloudClientError(
                    503,
                    "cloud_client_closed",
                    "组织云连接正在关闭",
                )
            client = self._clients.get(normalized)
            if client is None:
                client = CloudClient(
                    normalized,
                    timeout_seconds=self._timeout_seconds,
                )
                self._clients[normalized] = client
            return client

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()
