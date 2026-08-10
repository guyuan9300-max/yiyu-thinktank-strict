from __future__ import annotations

import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app.config import LocalConfig
from backend.app.cloud_client import CloudClient, CloudClientError, normalize_cloud_url
from backend.app.main import create_app as create_local_app
from backend.app.project_knowledge import (
    LOCAL_SUMMARY_MEDIA_TYPE,
    project_storage_prefix,
)
from backend.app.project_materials_local import LocalProjectMaterialsRepository
from backend.app.runtime import (
    LocalRuntimeError,
    PinnedSandboxContext,
    WorkspaceContext,
    WorkspaceRuntime,
)
from backend.app.secret_store import MemorySecretStore
from backend.app.ui_compat import StrictUiCompatibility
from backend.app.workbench_chat_local import LocalWorkbenchChatRepository
from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from strict_common.agent_memory import builtin_agent_id
from strict_common.ids import canonical_json, sha256_text, utc_now
from strict_common.schema import runtime_connection
from strict_common.security import decode_secret_bundle, encode_secret_bundle


def test_cloud_url_accepts_http_and_https() -> None:
    assert normalize_cloud_url("http://101.126.34.232/") == "http://101.126.34.232"
    assert normalize_cloud_url("https://example.invalid") == "https://example.invalid"


def test_cloud_client_v2_normalizes_one_leading_slash() -> None:
    client = CloudClient("https://example.invalid")
    paths: list[str] = []

    def capture(
        _method: str,
        path: str,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        paths.append(path)
        return {"ok": True}

    client._request = capture  # type: ignore[method-assign]
    assert client.request_v2(
        "GET",
        "/api/v2/business/snapshot",
        access_token="secret",
    ) == {"ok": True}
    assert client.request_v2(
        "GET",
        "api/v2/business/snapshot",
        access_token="secret",
    ) == {"ok": True}
    assert paths == [
        "/api/v2/business/snapshot",
        "/api/v2/business/snapshot",
    ]


def test_cloud_client_allows_arrays_only_for_explicit_v2_queries(
    monkeypatch: Any,
) -> None:
    class FakeHttpClient:
        def __init__(self, **_: Any):
            pass

        def __enter__(self) -> "FakeHttpClient":
            return self

        def __exit__(self, *_: Any) -> None:
            return None

        @staticmethod
        def request(*_: Any, **__: Any) -> Any:
            class Response:
                is_redirect = False
                status_code = 200
                content = b"[]"

                @staticmethod
                def json() -> list[dict[str, str]]:
                    return [{"id": "proposal-a"}]

            return Response()

    monkeypatch.setattr(
        "backend.app.cloud_client.httpx.Client",
        FakeHttpClient,
    )
    client = CloudClient("https://example.invalid")
    result = client.request_v2(
        "GET",
        "/api/v2/intelligence-growth/query",
        access_token="secret",
        allow_array=True,
    )
    assert result == [{"id": "proposal-a"}]
    with pytest.raises(CloudClientError) as error:
        client.request_v2(
            "GET",
            "/api/v2/intelligence-growth/query",
            access_token="secret",
        )
    assert error.value.code == "cloud_response_invalid"


class AsgiCloudClient:
    def __init__(self, client: TestClient):
        self.client = client
        self.refresh_count = 0
        self.business_snapshot_count = 0
        self.refresh_error: CloudClientError | None = None
        self._refresh_lock = threading.Lock()

    @staticmethod
    def _headers(
        access_token: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        if access_token:
            result["Authorization"] = f"Bearer {access_token}"
        if idempotency_key:
            result["Idempotency-Key"] = idempotency_key
        return result

    def _call(
        self,
        method: str,
        path: str,
        *,
        access_token: str | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = self.client.request(
            method,
            path,
            headers=self._headers(access_token, idempotency_key),
            json=json_body,
        )
        if response.status_code >= 400:
            from backend.app.cloud_client import CloudClientError

            payload = response.json()
            raise CloudClientError(
                response.status_code,
                payload["error"]["code"],
                payload["error"]["message"],
            )
        return response.json() if response.content else {}

    def handshake(self):
        return self._call("GET", "/api/v2/handshake")

    def login(
        self,
        *,
        identifier: str,
        password: str,
        idempotency_key: str | None = None,
    ):
        return self._call(
            "POST",
            "/api/v2/auth/login",
            json_body={"identifier": identifier, "password": password},
            idempotency_key=idempotency_key,
        )

    def join(self, payload):
        return self._call("POST", "/api/v2/auth/join", json_body=payload)

    def create_organization(self, payload):
        return self._call(
            "POST",
            "/api/v2/auth/bootstrap-organization",
            json_body=payload,
        )

    def refresh(
        self,
        refresh_token: str,
        *,
        idempotency_key: str | None = None,
    ):
        with self._refresh_lock:
            self.refresh_count += 1
            refresh_error = self.refresh_error
        if refresh_error is not None:
            raise refresh_error
        return self._call(
            "POST",
            "/api/v2/auth/refresh",
            json_body={"refreshToken": refresh_token},
            idempotency_key=idempotency_key,
        )

    def current_session(self, access_token: str):
        return self._call(
            "GET",
            "/api/v2/session/current",
            access_token=access_token,
        )

    def logout(
        self,
        access_token: str,
        *,
        idempotency_key: str | None = None,
    ):
        return self._call(
            "POST",
            "/api/v2/auth/logout",
            access_token=access_token,
            idempotency_key=idempotency_key,
        )

    def organization_snapshot(self, access_token: str):
        return self._call(
            "GET",
            "/api/v2/organization/snapshot",
            access_token=access_token,
        )

    def business_snapshot(self, access_token: str):
        with self._refresh_lock:
            self.business_snapshot_count += 1
        return self._call(
            "GET",
            "/api/v2/business/snapshot",
            access_token=access_token,
        )

    def project_knowledge_context(self, access_token: str, project_id: str):
        return self._call(
            "GET",
            f"/api/v2/projects/{project_id}/knowledge-context",
            access_token=access_token,
        )

    def task_detail(self, access_token: str, task_id: str):
        return self._call(
            "GET",
            f"/api/v2/tasks/{task_id}",
            access_token=access_token,
        )

    def create_task(
        self,
        access_token: str,
        payload,
        *,
        idempotency_key: str,
    ):
        return self._call(
            "POST",
            "/api/v2/tasks",
            access_token=access_token,
            idempotency_key=idempotency_key,
            json_body=payload,
        )

    def ai_runtime_secret(self, access_token: str):
        return self._call(
            "GET",
            "/api/v2/settings/org-ai-config/runtime-secret",
            access_token=access_token,
        )

    def save_ai_config(
        self,
        access_token: str,
        payload,
        *,
        idempotency_key: str,
    ):
        return self._call(
            "PUT",
            "/api/v2/settings/org-ai-config",
            access_token=access_token,
            idempotency_key=idempotency_key,
            json_body=payload,
        )

    def create_department(
        self,
        access_token: str,
        payload,
        *,
        idempotency_key: str,
    ):
        return self._call(
            "POST",
            "/api/v2/organization/departments",
            access_token=access_token,
            idempotency_key=idempotency_key,
            json_body=payload,
        )

    def create_management_title(
        self,
        access_token: str,
        payload,
        *,
        idempotency_key: str,
    ):
        return self._call(
            "POST",
            "/api/v2/organization/management-titles",
            access_token=access_token,
            idempotency_key=idempotency_key,
            json_body=payload,
        )

    def create_invite(self, access_token: str, payload):
        return self._call(
            "POST",
            "/api/v2/organization/invites",
            access_token=access_token,
            json_body=payload,
        )


def make_cloud(tmp_path: Path, name: str) -> tuple[TestClient, str]:
    cloud_dir = tmp_path / name
    config = CloudConfig(
        data_dir=cloud_dir,
        database_path=cloud_dir / "strict-cloud.db",
        bootstrap_token=f"{name}-bootstrap",
        master_key=Fernet.generate_key().decode(),
        cloud_instance_id=None,
    )
    client = TestClient(create_app(config))
    client.__enter__()
    return client, f"http://127.0.0.1:{51000 + len(name)}"


def session_reference(store: MemorySecretStore, sandbox_id: str) -> str:
    prefix = f"workspace-session:{sandbox_id}:"
    matches = [reference for reference in store.values if reference.startswith(prefix)]
    assert len(matches) == 1
    return matches[0]


def session_secret(store: MemorySecretStore, sandbox_id: str) -> dict[str, Any]:
    encoded = store.get(session_reference(store, sandbox_id))
    assert encoded
    return decode_secret_bundle(encoded)


def expire_local_access(store: MemorySecretStore, sandbox_id: str) -> None:
    reference = session_reference(store, sandbox_id)
    encoded = store.get(reference)
    assert encoded
    document = decode_secret_bundle(encoded)
    document["expiresAt"] = "2000-01-01T00:00:00Z"
    store.set(reference, encode_secret_bundle(document))


def expire_cloud_access(cloud_database: Path) -> None:
    with sqlite3.connect(cloud_database) as connection:
        connection.execute(
            "UPDATE authentication_sessions SET expires_at = ? WHERE status = 'active'",
            ("2000-01-01T00:00:00Z",),
        )
        connection.commit()


def expire_cloud_refresh(cloud_database: Path) -> None:
    with sqlite3.connect(cloud_database) as connection:
        connection.execute(
            """
            UPDATE authentication_sessions
            SET expires_at = ?, refresh_expires_at = ?
            WHERE status = 'active'
            """,
            ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z"),
        )
        connection.commit()


def make_runtime_with_organization(
    tmp_path: Path,
    name: str,
) -> tuple[
    TestClient,
    AsgiCloudClient,
    MemorySecretStore,
    WorkspaceRuntime,
    str,
    Path,
]:
    cloud, url = make_cloud(tmp_path, name)
    adapter = AsgiCloudClient(cloud)
    store = MemorySecretStore()
    runtime = WorkspaceRuntime(
        tmp_path / f"local-{name}" / "strict-local.db",
        store,
        cloud_factory=lambda _: adapter,
    )
    created = runtime.create_organization(
        cloud_api_url=url,
        bootstrap_token=f"{name}-bootstrap",
        organization_name=f"组织 {name}",
        display_name=f"管理员 {name}",
        email=f"{name}@example.com",
        phone=None,
        password="12345678",
    )
    return (
        cloud,
        adapter,
        store,
        runtime,
        created["sandbox"]["sandboxId"],
        tmp_path / name / "strict-cloud.db",
    )


def test_authentication_populates_business_projection_before_ui_load(
    tmp_path: Path,
) -> None:
    cloud, _, _, runtime, _, _ = make_runtime_with_organization(
        tmp_path,
        "auth-business-projection",
    )
    try:
        snapshot = runtime.business_snapshot(refresh=False)
        assert len(snapshot["projects"]) == 1
        assert snapshot["projects"][0]["name"] == (
            "组织 auth-business-projection项目"
        )
        assert snapshot["counts"]["projects"] == 1
    finally:
        cloud.__exit__(None, None, None)


def test_two_organizations_switch_without_secret_or_identity_leak(tmp_path: Path) -> None:
    cloud_a, url_a = make_cloud(tmp_path, "a")
    cloud_b, url_b = make_cloud(tmp_path, "bb")
    adapters = {
        url_a: AsgiCloudClient(cloud_a),
        url_b: AsgiCloudClient(cloud_b),
    }
    store = MemorySecretStore()
    database = tmp_path / "local" / "strict-local.db"
    runtime = WorkspaceRuntime(
        database,
        store,
        cloud_factory=lambda url: adapters[url],
    )
    try:
        a = runtime.create_organization(
            cloud_api_url=url_a,
            bootstrap_token="a-bootstrap",
            organization_name="组织 A",
            display_name="管理员 A",
            email="a@example.com",
            phone=None,
            password="12345678",
        )
        a_sandbox = a["sandbox"]["sandboxId"]
        runtime.save_ai_config(
            provider="doubao",
            base_url="https://a.invalid/v1",
            model_name="model-a",
            api_key="secret-a",
            expected_version=0,
            idempotency_key="ai-a",
        )
        b = runtime.create_organization(
            cloud_api_url=url_b,
            bootstrap_token="bb-bootstrap",
            organization_name="组织 B",
            display_name="管理员 B",
            email="b@example.com",
            phone=None,
            password="abcdefgh",
        )
        b_sandbox = b["sandbox"]["sandboxId"]
        runtime.save_ai_config(
            provider="doubao",
            base_url="https://b.invalid/v1",
            model_name="model-b",
            api_key="secret-b",
            expected_version=0,
            idempotency_key="ai-b",
        )

        back_to_a = runtime.switch(a_sandbox)
        assert back_to_a["sessionSnapshot"]["organization"]["name"] == "组织 A"
        assert back_to_a["aiRuntime"]["modelName"] == "model-a"
        back_to_b = runtime.switch(b_sandbox)
        assert back_to_b["sessionSnapshot"]["organization"]["name"] == "组织 B"
        assert back_to_b["aiRuntime"]["modelName"] == "model-b"

        restored_process = WorkspaceRuntime(
            database,
            store,
            cloud_factory=lambda url: adapters[url],
        )
        restored = restored_process.restore_active()
        assert restored["sandbox"]["sandboxId"] == b_sandbox
        assert restored["sessionSnapshot"]["organization"]["name"] == "组织 B"

        raw_database = b"".join(
            path.read_bytes()
            for path in database.parent.iterdir()
            if path.is_file()
        )
        for forbidden in (
            b"secret-a",
            b"secret-b",
            b"12345678",
            b"abcdefgh",
        ):
            assert forbidden not in raw_database
    finally:
        cloud_a.__exit__(None, None, None)
        cloud_b.__exit__(None, None, None)


def test_startup_restore_refreshes_expired_access_token(tmp_path: Path) -> None:
    cloud, adapter, store, runtime, sandbox_id, cloud_database = (
        make_runtime_with_organization(tmp_path, "startup")
    )
    try:
        before = session_secret(store, sandbox_id)
        expire_local_access(store, sandbox_id)
        expire_cloud_access(cloud_database)

        restarted = WorkspaceRuntime(
            runtime.database_path,
            store,
            cloud_factory=lambda _: adapter,
        )
        restored = restarted.restore_at_startup()
        after = session_secret(store, sandbox_id)

        assert adapter.refresh_count == 1
        assert after["accessToken"] != before["accessToken"]
        assert after["refreshToken"] != before["refreshToken"]
        assert restored["runtimeStatus"] == "ready"
        assert restored["sandbox"]["sandboxId"] == sandbox_id
        assert restored["sessionSnapshot"]["organization"]["name"] == "组织 startup"

        snapshot = restarted.business_snapshot(refresh=True)
        assert snapshot["sandboxId"] == sandbox_id
        assert adapter.refresh_count == 1

        expire_local_access(store, sandbox_id)
        expire_cloud_access(cloud_database)
        created = restarted.task_command(
            "create",
            task_id=None,
            payload={
                "title": "续期后的任务",
                "description": "",
                "projectId": None,
                "ownerMembershipId": None,
                "collaboratorMembershipIds": [],
                "priority": "normal",
                "visibilityScope": "participants",
                "startDate": None,
                "dueDate": None,
                "scheduledStartAt": None,
                "scheduledEndAt": None,
                "deadlineAt": None,
                "durationMinutes": 60,
            },
            idempotency_key="task-after-refresh",
        )
        task_id = created["task"]["taskId"]
        assert restarted.task_detail(task_id)["task"]["title"] == "续期后的任务"
        assert adapter.refresh_count == 2
    finally:
        cloud.__exit__(None, None, None)


def test_local_backend_lifespan_runs_startup_restore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original = WorkspaceRuntime.restore_at_startup

    def tracking_restore(runtime: WorkspaceRuntime):
        calls.append(str(runtime.database_path))
        return original(runtime)

    monkeypatch.setattr(WorkspaceRuntime, "restore_at_startup", tracking_restore)
    data_dir = tmp_path / "lifespan-local"
    config = LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="desktop-test-token",
        secret_namespace="test.strict.lifespan",
        test_mode=True,
    )
    with TestClient(create_local_app(config)):
        pass
    assert calls == [str(config.database_path.resolve())]


def test_local_http_exposes_project_knowledge_context_in_workspace(
    tmp_path: Path,
) -> None:
    cloud, url = make_cloud(tmp_path, "knowledge-http")
    adapter = AsgiCloudClient(cloud)
    data_dir = tmp_path / "knowledge-http-local"
    config = LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="knowledge-http-token",
        secret_namespace="test.strict.knowledge-http",
        test_mode=True,
    )
    app = create_local_app(config)
    app.state.runtime.cloud_factory = lambda _: adapter
    headers = {"X-Yiyu-Desktop-Token": config.desktop_token}
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v2/workspaces/create-organization",
                headers=headers,
                json={
                    "cloudApiUrl": url,
                    "bootstrapToken": "knowledge-http-bootstrap",
                    "organizationName": "知识接口组织",
                    "displayName": "管理员",
                    "email": "knowledge-http@example.com",
                    "phone": None,
                    "password": "12345678",
                },
            )
            assert created.status_code == 200, created.text
            snapshot = client.get(
                "/api/v2/business/snapshot?refresh=true",
                headers=headers,
            )
            assert snapshot.status_code == 200, snapshot.text
            project_id = snapshot.json()["projects"][0]["projectId"]

            direct = client.get(
                f"/api/v2/projects/{project_id}/knowledge-context",
                headers=headers,
            )
            assert direct.status_code == 200, direct.text
            assert direct.json()["project"]["projectId"] == project_id
            assert direct.json()["counts"]["organizationShared"] == 0
            assert direct.json()["counts"]["localPrivate"] == 0

            workspace = client.get(
                f"/api/v2/ui/clients/{project_id}/workspace",
                headers=headers,
            )
            assert workspace.status_code == 200, workspace.text
            assert workspace.json()["knowledgeContext"] == direct.json()
    finally:
        cloud.__exit__(None, None, None)


def test_concurrent_requests_singleflight_session_refresh(tmp_path: Path) -> None:
    cloud, adapter, store, runtime, sandbox_id, cloud_database = (
        make_runtime_with_organization(tmp_path, "parallel")
    )
    try:
        expire_local_access(store, sandbox_id)
        expire_cloud_access(cloud_database)
        snapshot_count_before = adapter.business_snapshot_count
        original_snapshot = adapter.business_snapshot
        snapshot_started = threading.Event()
        snapshot_release = threading.Event()

        def delayed_snapshot(access_token: str):
            snapshot_started.set()
            snapshot_release.wait(timeout=2)
            return original_snapshot(access_token)

        adapter.business_snapshot = delayed_snapshot  # type: ignore[method-assign]

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(runtime.business_snapshot, refresh=True)
            assert snapshot_started.wait(timeout=1)
            second_started = threading.Event()

            def second_sync():
                second_started.set()
                return runtime.business_snapshot(refresh=True)

            second = executor.submit(second_sync)
            assert second_started.wait(timeout=1)
            time.sleep(0.05)
            snapshot_release.set()
            results = [
                first.result(timeout=2),
                second.result(timeout=2),
            ]

        assert adapter.refresh_count == 1
        assert adapter.business_snapshot_count - snapshot_count_before == 1
        assert {item["sandboxId"] for item in results} == {sandbox_id}
        assert runtime.current()["runtimeStatus"] == "ready"
    finally:
        cloud.__exit__(None, None, None)


def test_transient_refresh_failure_is_retryable_and_recovers(tmp_path: Path) -> None:
    cloud, adapter, store, runtime, sandbox_id, _ = make_runtime_with_organization(
        tmp_path,
        "retryable",
    )
    try:
        expire_local_access(store, sandbox_id)
        adapter.refresh_error = CloudClientError(
            503,
            "cloud_unreachable",
            "暂时无法连接组织云",
        )
        with pytest.raises(LocalRuntimeError) as failure:
            runtime.business_snapshot(refresh=True)
        assert failure.value.code == "failed_retryable"
        assert runtime.current()["runtimeStatus"] == "sync_degraded"
        workspace = StrictUiCompatibility(runtime).current_workspace()
        assert workspace["cloudConnectionStatus"] == "failed_retryable"
        assert workspace["requiresLogin"] is False

        adapter.refresh_error = None
        recovered = runtime.business_snapshot(refresh=True)
        assert recovered["sandboxId"] == sandbox_id
        assert runtime.current()["runtimeStatus"] == "ready"
    finally:
        cloud.__exit__(None, None, None)


def test_expired_refresh_token_requires_login(tmp_path: Path) -> None:
    cloud, _, store, runtime, sandbox_id, cloud_database = (
        make_runtime_with_organization(tmp_path, "relogin")
    )
    try:
        expire_local_access(store, sandbox_id)
        expire_cloud_refresh(cloud_database)

        with pytest.raises(LocalRuntimeError) as failure:
            runtime.business_snapshot(refresh=True)

        assert failure.value.code == "needs_login"
        assert runtime.current()["runtimeStatus"] == "needs_login"
        workspace = StrictUiCompatibility(runtime).current_workspace()
        assert workspace["cloudConnectionStatus"] == "needs_login"
        assert workspace["requiresLogin"] is True
    finally:
        cloud.__exit__(None, None, None)


def test_switch_with_missing_sandbox_secret_marks_only_target_for_login(
    tmp_path: Path,
) -> None:
    cloud_a, url_a = make_cloud(tmp_path, "missing-secret-a")
    cloud_b, url_b = make_cloud(tmp_path, "missing-secret-bb")
    adapters = {
        url_a: AsgiCloudClient(cloud_a),
        url_b: AsgiCloudClient(cloud_b),
    }
    store = MemorySecretStore()
    runtime = WorkspaceRuntime(
        tmp_path / "missing-secret-local" / "strict-local.db",
        store,
        cloud_factory=lambda url: adapters[url],
    )
    try:
        a = runtime.create_organization(
            cloud_api_url=url_a,
            bootstrap_token="missing-secret-a-bootstrap",
            organization_name="组织 A",
            display_name="管理员 A",
            email="missing-secret-a@example.com",
            phone=None,
            password="12345678",
        )
        a_sandbox = a["sandbox"]["sandboxId"]
        b = runtime.create_organization(
            cloud_api_url=url_b,
            bootstrap_token="missing-secret-bb-bootstrap",
            organization_name="组织 B",
            display_name="管理员 B",
            email="missing-secret-b@example.com",
            phone=None,
            password="12345678",
        )
        b_sandbox = b["sandbox"]["sandboxId"]
        runtime.switch(a_sandbox)
        store.delete(session_reference(store, b_sandbox))

        with pytest.raises(LocalRuntimeError) as failure:
            runtime.switch(b_sandbox)

        assert failure.value.code == "workspace_secret_missing"
        assert runtime.current()["sandbox"]["sandboxId"] == a_sandbox
        workspaces = StrictUiCompatibility(runtime).workspaces()["workspaces"]
        by_id = {workspace["id"]: workspace for workspace in workspaces}
        assert by_id[b_sandbox]["cloudConnectionStatus"] == "needs_login"
        assert by_id[b_sandbox]["requiresLogin"] is True
        assert by_id[a_sandbox]["cloudConnectionStatus"] == "connected"
        assert by_id[a_sandbox]["requiresLogin"] is False
    finally:
        cloud_b.__exit__(None, None, None)
        cloud_a.__exit__(None, None, None)


def test_switch_rebinds_migrated_database_generation_after_session_validation(
    tmp_path: Path,
) -> None:
    cloud, adapter, _, runtime, sandbox_id, _ = make_runtime_with_organization(
        tmp_path,
        "generation-rebind",
    )
    try:
        with runtime_connection(runtime.database_path, "local") as connection:
            connection.execute(
                """
                UPDATE workspace_bindings
                SET database_generation_id = ?,
                    identity_state = 'identity_error',
                    version = version + 1
                WHERE sandbox_id = ?
                """,
                ("generation-before-strict-migration", sandbox_id),
            )
            connection.execute(
                """
                UPDATE workspace_sandboxes
                SET runtime_status = 'identity_error',
                    version = version + 1
                WHERE sandbox_id = ?
                """,
                (sandbox_id,),
            )
            connection.commit()

        switched = runtime.switch(sandbox_id)
        handshake = adapter.handshake()
        assert switched["runtimeStatus"] == "ready"
        assert switched["sandbox"]["cloudInstanceId"] == (
            handshake["cloudInstanceId"]
        )
        with runtime_connection(
            runtime.database_path,
            "local",
            read_only=True,
        ) as connection:
            binding = connection.execute(
                """
                SELECT database_generation_id, identity_state
                FROM workspace_bindings
                WHERE sandbox_id = ?
                """,
                (sandbox_id,),
            ).fetchone()
        assert binding["database_generation_id"] == (
            handshake["databaseGenerationId"]
        )
        assert binding["identity_state"] == "verified"
    finally:
        cloud.__exit__(None, None, None)


def test_delayed_refresh_stays_with_originating_sandbox(tmp_path: Path) -> None:
    cloud_a, url_a = make_cloud(tmp_path, "late-a")
    cloud_b, url_b = make_cloud(tmp_path, "late-bb")
    adapter_a = AsgiCloudClient(cloud_a)
    adapter_b = AsgiCloudClient(cloud_b)
    adapters = {url_a: adapter_a, url_b: adapter_b}
    store = MemorySecretStore()
    runtime = WorkspaceRuntime(
        tmp_path / "late-local" / "strict-local.db",
        store,
        cloud_factory=lambda url: adapters[url],
    )
    try:
        a = runtime.create_organization(
            cloud_api_url=url_a,
            bootstrap_token="late-a-bootstrap",
            organization_name="组织 A",
            display_name="管理员 A",
            email="late-a@example.com",
            phone=None,
            password="12345678",
        )
        a_sandbox = a["sandbox"]["sandboxId"]
        expire_local_access(store, a_sandbox)
        expire_cloud_access(tmp_path / "late-a" / "strict-cloud.db")
        delayed_context = runtime._secret_context(a_sandbox)

        b = runtime.create_organization(
            cloud_api_url=url_b,
            bootstrap_token="late-bb-bootstrap",
            organization_name="组织 B",
            display_name="管理员 B",
            email="late-b@example.com",
            phone=None,
            password="12345678",
        )
        b_sandbox = b["sandbox"]["sandboxId"]
        b_secret_before = session_secret(store, b_sandbox)

        runtime._sync_business_for_context(delayed_context)

        current = runtime.current()
        assert current["sandbox"]["sandboxId"] == b_sandbox
        assert current["sessionSnapshot"]["organization"]["name"] == "组织 B"
        assert session_secret(store, b_sandbox) == b_secret_before
        assert adapter_a.refresh_count == 1
        assert adapter_b.refresh_count == 0
        with sqlite3.connect(runtime.database_path) as connection:
            a_projects = connection.execute(
                """
                SELECT COUNT(*) FROM projection_business_objects
                WHERE sandbox_id = ? AND organization_id = ?
                """,
                (a_sandbox, a["sandbox"]["organizationId"]),
            ).fetchone()[0]
            wrong_b_projects = connection.execute(
                """
                SELECT COUNT(*) FROM projection_business_objects
                WHERE sandbox_id = ? AND organization_id = ?
                """,
                (b_sandbox, a["sandbox"]["organizationId"]),
            ).fetchone()[0]
        assert a_projects >= 1
        assert wrong_b_projects == 0
    finally:
        cloud_a.__exit__(None, None, None)
        cloud_b.__exit__(None, None, None)


def test_project_knowledge_context_keeps_local_private_summary_in_its_sandbox(
    tmp_path: Path,
) -> None:
    cloud_a, url_a = make_cloud(tmp_path, "knowledge-a")
    cloud_b, url_b = make_cloud(tmp_path, "knowledge-bb")
    adapters = {
        url_a: AsgiCloudClient(cloud_a),
        url_b: AsgiCloudClient(cloud_b),
    }
    store = MemorySecretStore()
    runtime = WorkspaceRuntime(
        tmp_path / "knowledge-local" / "strict-local.db",
        store,
        cloud_factory=lambda url: adapters[url],
    )
    try:
        a = runtime.create_organization(
            cloud_api_url=url_a,
            bootstrap_token="knowledge-a-bootstrap",
            organization_name="知识组织 A",
            display_name="管理员 A",
            email="knowledge-a@example.com",
            phone=None,
            password="12345678",
        )
        a_sandbox = a["sandbox"]["sandboxId"]
        a_project_id = runtime.business_snapshot(
            refresh=True
        )["projects"][0]["projectId"]
        prefix = project_storage_prefix(a_sandbox, a_project_id)
        source_key = f"{prefix}source/source.txt"
        summary_key = f"{prefix}summary/source.json"
        source_path = runtime.database_path.parent / source_key
        summary_path = runtime.database_path.parent / summary_key
        source_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        source_bytes = "仅属于 A 的本机源文件正文".encode()
        source_hash = sha256_text(source_bytes.decode())
        source_path.write_bytes(source_bytes)
        now = utc_now()
        sidecar = {
            "schema": "yiyu.project-local-private-knowledge.v1",
            "sourceScope": "local_private",
            "projectId": a_project_id,
            "sourceId": "local_source_a",
            "contentHash": source_hash,
            "summary": "A 项目的本机私有资料摘要。",
            "summaryKind": "extracted_text",
            "sourceDescription": "当前设备工作台本机私有资料",
            "updatedAt": now,
            "fileName": "source.txt",
        }
        sidecar_text = json.dumps(sidecar, ensure_ascii=False)
        summary_path.write_text(sidecar_text, encoding="utf-8")

        with runtime_connection(runtime.database_path, "local") as connection:
            connection.execute(
                """
                INSERT INTO storage_objects (
                  object_id, sandbox_id, storage_key, content_hash, media_type,
                  byte_size, lifecycle_state, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
                """,
                (
                    "local_source_a",
                    a_sandbox,
                    source_key,
                    source_hash,
                    "text/plain",
                    len(source_bytes),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO storage_objects (
                  object_id, sandbox_id, storage_key, content_hash, media_type,
                  byte_size, lifecycle_state, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', 1, ?, ?)
                """,
                (
                    "local_summary_a",
                    a_sandbox,
                    summary_key,
                    sha256_text(sidecar_text),
                    LOCAL_SUMMARY_MEDIA_TYPE,
                    len(sidecar_text.encode()),
                    now,
                    now,
                ),
            )
            connection.commit()

        b = runtime.create_organization(
            cloud_api_url=url_b,
            bootstrap_token="knowledge-bb-bootstrap",
            organization_name="知识组织 B",
            display_name="管理员 B",
            email="knowledge-b@example.com",
            phone=None,
            password="12345678",
        )
        b_project_id = runtime.business_snapshot(
            refresh=True
        )["projects"][0]["projectId"]
        b_context = runtime.project_knowledge_context(b_project_id)
        assert b_context["sandboxId"] == b["sandbox"]["sandboxId"]
        assert b_context["counts"]["localPrivate"] == 0
        assert b_context["state"]["localPrivate"] == "empty"

        runtime.switch(a_sandbox)
        a_context = runtime.project_knowledge_context(a_project_id)
        serialized = json.dumps(a_context, ensure_ascii=False)
        assert a_context["sandboxId"] == a_sandbox
        assert a_context["cloudInstanceId"] == a["sandbox"]["cloudInstanceId"]
        assert a_context["organizationId"] == a["sandbox"]["organizationId"]
        assert a_context["counts"] == {
            "organizationShared": 0,
            "localPrivate": 1,
            "projectMetadata": 1,
            "localRetrievalReady": 1,
            "localMetadataOnly": 0,
        }
        assert a_context["state"] == {
            "overall": "ready",
            "organizationShared": "empty",
            "localPrivate": "ready",
            "organizationSharedMessage": (
                "组织云查询成功，但该项目尚无明确发布的共享知识摘要"
            ),
            "localPrivateMessage": "",
        }
        assert a_context["localPrivateKnowledge"][0]["summary"] == (
            "A 项目的本机私有资料摘要。"
        )
        assert str(tmp_path) not in serialized
        assert source_key not in serialized
        assert "仅属于 A 的本机源文件正文" not in serialized
        assert a_context["materialBoundary"][
            "localPrivateUploadedToOrganizationCloud"
        ] is False

        summary_path.write_text(
            sidecar_text.replace("本机私有资料摘要", "被外部篡改的摘要"),
            encoding="utf-8",
        )
        invalid_context = runtime.project_knowledge_context(a_project_id)
        assert invalid_context["counts"]["localPrivate"] == 0
        assert invalid_context["state"]["localPrivate"] == "failed_retryable"
        assert invalid_context["state"]["overall"] == "failed_retryable"
        assert invalid_context["state"]["localPrivateMessage"] == (
            "1 条本机摘要需要重建"
        )

        with runtime_connection(
            tmp_path / "knowledge-a" / "strict-cloud.db",
            "cloud",
            read_only=True,
        ) as connection:
            cloud_storage_count = connection.execute(
                "SELECT COUNT(*) FROM storage_objects"
            ).fetchone()[0]
        assert cloud_storage_count == 0
    finally:
        cloud_b.__exit__(None, None, None)
        cloud_a.__exit__(None, None, None)


def test_project_knowledge_context_reports_cloud_connection_state(
    tmp_path: Path,
) -> None:
    cloud, adapter, _, runtime, _, _ = make_runtime_with_organization(
        tmp_path,
        "knowledge-state",
    )
    try:
        project_id = runtime.business_snapshot(
            refresh=True
        )["projects"][0]["projectId"]

        def missing_endpoint(_: str, __: str) -> dict[str, Any]:
            raise CloudClientError(
                404,
                "cloud_request_failed",
                "组织云请求失败（404）",
            )

        adapter.project_knowledge_context = missing_endpoint  # type: ignore[method-assign]
        missing = runtime.project_knowledge_context(project_id)
        assert missing["counts"]["organizationShared"] == 0
        assert missing["state"]["overall"] == "not_connected"
        assert missing["state"]["organizationShared"] == "not_connected"
        assert missing["state"]["organizationSharedMessage"] == (
            "组织云项目知识查询尚未部署"
        )

        def retryable_failure(_: str, __: str) -> dict[str, Any]:
            raise CloudClientError(503, "cloud_unreachable", "暂时无法连接组织云")

        adapter.project_knowledge_context = retryable_failure  # type: ignore[method-assign]
        failed = runtime.project_knowledge_context(project_id)
        assert failed["counts"]["organizationShared"] == 0
        assert failed["state"]["overall"] == "failed_retryable"
        assert failed["state"]["organizationShared"] == "failed_retryable"
        assert "可以重试" in failed["state"]["organizationSharedMessage"]
    finally:
        cloud.__exit__(None, None, None)


def test_old_database_path_is_poison_not_fallback(tmp_path: Path) -> None:
    data_dir = tmp_path / "strict"
    data_dir.mkdir()
    poisoned_old_database = data_dir / "app.db"
    poisoned_old_database.write_bytes(b"OLD-DATABASE-MUST-NEVER-BE-READ")
    before = poisoned_old_database.read_bytes()
    runtime = WorkspaceRuntime(
        data_dir / "strict-local.db",
        MemorySecretStore(),
        cloud_factory=lambda _: None,  # type: ignore[arg-type]
    )
    assert runtime.current()["runtimeStatus"] == "local_draft"
    assert poisoned_old_database.read_bytes() == before
    assert (data_dir / "strict-local.db").exists()


def test_workbench_operation_replay_does_not_call_model_runtime() -> None:
    runtime = object.__new__(WorkspaceRuntime)
    runtime.__dict__["_current_context"] = lambda **_: object()
    snapshot_call: dict[str, Any] = {}

    def local_snapshot(**kwargs: Any) -> dict[str, Any]:
        snapshot_call.update(kwargs)
        return {
            "projects": [{"projectId": "project-1"}],
            "aiAnswers": [
                {
                    "answerId": "answer-1",
                    "projectId": "project-1",
                    "answerMarkdown": "已完成的稳定答案",
                    "sourceManifest": {"operationKey": "operation-1"},
                }
            ],
        }

    runtime.__dict__["business_snapshot"] = local_snapshot
    result = WorkspaceRuntime.workbench_chat(
        runtime,
        project_id="project-1",
        question="不会再次调用模型",
        mode="balanced",
        source_manifest_extra={"operationKey": "operation-1"},
        idempotency_key="operation-1",
    )
    assert snapshot_call["refresh"] is False
    assert result["idempotentReplay"] is True
    assert result["answer"]["answerId"] == "answer-1"


def test_workbench_chat_uses_summary_only_project_knowledge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = WorkspaceContext(
        sandbox_id="sandbox-a",
        cloud_instance_id="cloud-a",
        organization_id="org-a",
        cloud_api_url="https://cloud-a.invalid",
        principal_id="principal-a",
        membership_id="membership-a",
        access_token="access-a",
        refresh_token="refresh-a",
        access_expires_at=None,
        refresh_expires_at=None,
    )
    runtime = object.__new__(WorkspaceRuntime)
    runtime.__dict__["_current_context"] = lambda **_: context
    runtime.__dict__["business_snapshot"] = lambda **_: {
        "projects": [
            {
                "projectId": "project-a",
                "name": "日慈基金会",
                "summary": "项目元数据摘要",
            }
        ],
        "documents": [
            {
                "documentId": "document-a",
                "projectId": "project-a",
                "title": "资料目录标题",
            }
        ],
        "aiAnswers": [],
    }
    runtime.__dict__["project_knowledge_context"] = lambda *_args, **_kwargs: {
        "organizationSharedKnowledge": [
            {
                "sourceId": "knowledge-a",
                "title": "组织共享摘要",
                "summary": "组织可共享的日慈项目背景。",
                "markdownContent": "CLOUD_RAW_BODY_MUST_NOT_ENTER_PROMPT",
            }
        ],
        "localPrivateKnowledge": [
            {
                "sourceId": "local-a",
                "title": "本机私有摘要",
                "summary": "本机提炼但不上传源文件的背景。",
                "sourcePath": "/private/LOCAL_PATH_MUST_NOT_ENTER_PROMPT.pdf",
            }
        ],
        "state": {
            "overall": "ready",
            "organizationShared": "ready",
            "localPrivate": "ready",
        },
    }
    runtime.__dict__["_connection"] = lambda: nullcontext(object())
    runtime.__dict__["_current_ai_runtime"] = lambda *_: {
        "state": "ready_direct",
        "baseUrl": "https://model.invalid/v1",
        "modelName": "strict-model",
    }
    secret_store = MemorySecretStore()
    secret_store.set(runtime._ai_ref(context.sandbox_id), "model-secret")
    runtime.__dict__["secret_store"] = secret_store

    model_request: dict[str, Any] = {}
    saved_request: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "choices": [
                    {"message": {"content": "这是只基于摘要生成的回答。"}}
                ]
            }

    class FakeHttpClient:
        def __init__(self, **_kwargs: Any):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def post(self, _url: str, **kwargs: Any) -> FakeResponse:
            model_request.update(kwargs["json"])
            return FakeResponse()

    class FakeCloudClient:
        def save_workbench_answer(
            self,
            _access_token: str,
            payload: dict[str, Any],
            *,
            idempotency_key: str,
        ) -> dict[str, Any]:
            saved_request.update(payload)
            saved_request["idempotencyKey"] = idempotency_key
            return {"answer": {"answerId": "answer-a", **payload}}

    runtime.__dict__["_authenticated_cloud_call"] = (
        lambda captured, execute: (
            execute(FakeCloudClient(), captured),
            captured,
        )
    )
    monkeypatch.setattr("backend.app.runtime.httpx.Client", FakeHttpClient)

    result = WorkspaceRuntime.workbench_chat(
        runtime,
        project_id="project-a",
        question=" 日慈项目的背景是什么？ ",
        mode="balanced",
        source_manifest_extra={"operationKey": "workbench-chat-a"},
        idempotency_key="workbench-chat-a",
    )

    system_prompt = model_request["messages"][0]["content"]
    assert "组织可共享的日慈项目背景" in system_prompt
    assert "本机提炼但不上传源文件的背景" in system_prompt
    assert "CLOUD_RAW_BODY_MUST_NOT_ENTER_PROMPT" not in system_prompt
    assert "LOCAL_PATH_MUST_NOT_ENTER_PROMPT" not in system_prompt
    assert saved_request["question"] == "日慈项目的背景是什么？"
    assert "组织可共享的日慈项目背景" not in json.dumps(
        saved_request["sourceManifest"],
        ensure_ascii=False,
    )
    assert saved_request["sourceManifest"]["projectKnowledgeSummaryCount"] == 2
    assert result["answer"]["answerId"] == "answer-a"


def test_workbench_chat_does_not_save_after_workspace_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = WorkspaceContext(
        sandbox_id="sandbox-a",
        cloud_instance_id="cloud-a",
        organization_id="org-a",
        cloud_api_url="https://cloud-a.invalid",
        principal_id="principal-a",
        membership_id="membership-a",
        access_token="access-a",
        refresh_token="refresh-a",
        access_expires_at=None,
        refresh_expires_at=None,
    )
    second = WorkspaceContext(
        sandbox_id="sandbox-b",
        cloud_instance_id="cloud-b",
        organization_id="org-b",
        cloud_api_url="https://cloud-b.invalid",
        principal_id="principal-b",
        membership_id="membership-b",
        access_token="access-b",
        refresh_token="refresh-b",
        access_expires_at=None,
        refresh_expires_at=None,
    )
    current = {"context": first}
    runtime = object.__new__(WorkspaceRuntime)
    runtime.__dict__["_current_context"] = (
        lambda **_: current["context"]
    )
    runtime.__dict__["business_snapshot"] = lambda **_: {
        "projects": [{"projectId": "project-a", "name": "项目 A"}],
        "documents": [],
        "aiAnswers": [],
    }
    runtime.__dict__["project_knowledge_context"] = lambda *_args, **_kwargs: {
        "organizationSharedKnowledge": [],
        "localPrivateKnowledge": [],
        "state": {"overall": "empty"},
    }
    runtime.__dict__["_connection"] = lambda: nullcontext(object())
    runtime.__dict__["_current_ai_runtime"] = lambda *_: {
        "state": "ready_direct",
        "baseUrl": "https://model.invalid/v1",
        "modelName": "strict-model",
    }
    secret_store = MemorySecretStore()
    secret_store.set(runtime._ai_ref(first.sandbox_id), "model-secret")
    runtime.__dict__["secret_store"] = secret_store
    save_called = {"value": False}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json() -> dict[str, Any]:
            return {"choices": [{"message": {"content": "迟到回答"}}]}

    class SwitchingHttpClient:
        def __init__(self, **_kwargs: Any):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def post(self, _url: str, **_kwargs: Any) -> FakeResponse:
            current["context"] = second
            return FakeResponse()

    def unexpected_save(*_args: Any, **_kwargs: Any) -> Any:
        save_called["value"] = True
        raise AssertionError("workspace switch must prevent cloud save")

    runtime.__dict__["_authenticated_cloud_call"] = unexpected_save
    monkeypatch.setattr(
        "backend.app.runtime.httpx.Client",
        SwitchingHttpClient,
    )

    with pytest.raises(LocalRuntimeError) as error:
        WorkspaceRuntime.workbench_chat(
            runtime,
            project_id="project-a",
            question="测试切换",
            mode="balanced",
            idempotency_key="switch-a",
        )
    assert error.value.code == "workspace_context_changed"
    assert save_called["value"] is False


def test_gc15_local_answer_memory_recall_and_revoke(tmp_path: Path) -> None:
    local_database = tmp_path / "local-gc15-memory" / "strict-local.db"
    runtime = WorkspaceRuntime(local_database, MemorySecretStore())
    now = utc_now()
    organization_id = "org_gc15_memory"
    scope_id = "scope_gc15_memory"
    principal_id = "principal_gc15_memory"
    membership_id = "membership_gc15_memory"
    sandbox_id = "sandbox_gc15_memory"
    project_id = "client_gc15_memory"
    cloud_instance_id = "cli_gc15_memory"
    with runtime_connection(local_database, "local") as connection:
        connection.execute(
            "INSERT INTO organizations (id,lifecycle_state,version,updated_at,record_kind,name,created_at,deleted_at,projection_state,projected_at) "
            "VALUES (?,'active',1,?,'organization','GC15 记忆测试组织',?,NULL,'current',?)",
            (organization_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO principals (id,status,identity_version,updated_at,principal_kind,display_name,version,lifecycle_state,created_at,deleted_at,projection_state,projected_at) "
            "VALUES (?,'active',1,?,'person','GC15 管理员',1,'active',?,NULL,'current',?)",
            (principal_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO authorization_scopes (id,scope_kind,organization_id,policy_version,created_at,updated_at,status,version,lifecycle_state,deleted_at,projection_state,projected_at) "
            "VALUES (?,'organization',?,1,?,?,'active',1,'active',NULL,'current',?)",
            (scope_id, organization_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO organization_memberships (id,scope_id,principal_id,role_key,status,version,record_kind,visibility_scope,lifecycle_state,created_at,updated_at,deleted_at,projection_state,projected_at) "
            "VALUES (?,?,?,'admin','active',1,'membership','organization','active',?,?,NULL,'current',?)",
            (membership_id, scope_id, principal_id, now, now, now),
        )
        connection.execute(
            "INSERT INTO sandboxes (id,scope_id,principal_id,membership_id,record_kind,cloud_instance_id,database_generation_id,sandbox_kind,display_name,runtime_status,manifest_hash,version,lifecycle_state,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
            "VALUES (?,?,?,?,'sandbox',?,?,'organization','GC15 工作空间','ready',?,1,'active',?,?,NULL,'local',?)",
            (
                sandbox_id,
                scope_id,
                principal_id,
                membership_id,
                cloud_instance_id,
                runtime.identity.database_generation_id,
                runtime.identity.manifest_hash,
                now,
                now,
                runtime.identity.database_generation_id,
            ),
        )
        connection.execute(
            "INSERT INTO secured_resources (id,scope_id,resource_kind,lifecycle_state,version,resource_type_key,created_at,updated_at,deleted_at,authority_role,origin_instance_id) "
            "VALUES (?,?,'client','active',1,'client',?,?,NULL,'local',?)",
            (project_id, scope_id, now, now, runtime.identity.database_generation_id),
        )
        connection.execute(
            "INSERT INTO clients (id,scope_id,owner_membership_id,lifecycle_state,version,name,created_at,updated_at,deleted_at,sandbox_id,source_version,projection_state,projected_at) "
            "VALUES (?,?,?,'active',1,'GC15 测试项目',?,?,NULL,?,1,'current',?)",
            (project_id, scope_id, membership_id, now, now, sandbox_id, now),
        )
        connection.commit()
    workspace_context = WorkspaceContext(
        sandbox_id=sandbox_id,
        cloud_instance_id=cloud_instance_id,
        organization_id=organization_id,
        cloud_api_url="https://gc15.invalid",
        principal_id=principal_id,
        membership_id=membership_id,
        access_token="access-gc15",
        refresh_token="refresh-gc15",
        access_expires_at=None,
        refresh_expires_at=None,
    )
    pinned_context = PinnedSandboxContext(
        sandbox_id=sandbox_id,
        sandbox_kind="organization",
        cloud_instance_id=cloud_instance_id,
        organization_id=organization_id,
        scope_id=scope_id,
        workspace_context=workspace_context,
    )
    runtime._current_context = lambda require_ready=True: workspace_context  # type: ignore[method-assign]
    runtime.capture_sandbox_context = lambda **_kwargs: pinned_context  # type: ignore[method-assign]
    cloud = nullcontext()
    try:
        repository = LocalWorkbenchChatRepository(runtime)
        context = repository._context()
        provider = {
            "configId": "provider_gc15_local",
            "provider": "doubao",
            "baseUrl": "https://example.invalid/api/v3",
            "modelName": "model-gc15-test",
            "keyFingerprint": "fingerprint-gc15",
            "status": "ready",
            "version": 1,
        }
        bot_id = builtin_agent_id(context.organization_id, "project_workspace")
        repository._project_agent_and_provider(provider=provider, bot_id=bot_id)
        created_at = utc_now()
        marker = "GC15-唯一记忆-青色纸飞机"
        answer_id = "answer_gc15_original"
        repository._persist_pending(
            answer_id=answer_id,
            client_id=project_id,
            thread_id="thread_gc15_original",
            question="请记住一个测试标记",
            answer_markdown=f"需要长期记住：{marker}",
            source_manifest={
                "threadId": "thread_gc15_original",
                "mode": "balanced",
                "memoryState": "ready",
            },
            source_set_id="source_set_gc15_original",
            context_manifest_id="context_gc15_original",
            lineage_id="lineage_gc15_original",
            provider_id=str(provider["configId"]),
            bot_id=bot_id,
            model_name=str(provider["modelName"]),
            sources=[],
            material_access_mode="none",
            boundary_state="no_material_context",
            created_at=created_at,
        )
        repository._mark_ready(answer_id, updated_at=created_at)

        saved = repository.save_answer_memory(
            project_id=project_id,
            answer_id=answer_id,
            memory_kind="favorite",
            idempotency_key="gc15-save-favorite",
        )
        assert saved["memoryKind"] == "favorite"
        presentation = LocalProjectMaterialsRepository(runtime).knowledge_presentation(project_id)
        assert presentation["savedMemories"][0]["sourceAnswerId"] == answer_id
        assert marker in presentation["savedMemories"][0]["summary"]

        cloud_memory_manifest: dict[str, Any] = {
            "clientId": project_id,
            "cloudState": "not_connected",
            "manifestVersion": 0,
            "memoryCount": 0,
            "counts": {"explicitMemory": 0, "favorite": 0, "correction": 0},
            "memoryDigest": sha256_text(canonical_json([])),
            "entries": [],
            "updatedAt": None,
        }
        captured_memory_sync_payloads: list[dict[str, Any]] = []
        runtime.require_project_capability = (  # type: ignore[method-assign]
            lambda requested_project_id, capability="read": {
                "projectId": requested_project_id,
                "viewerCapabilities": [capability],
            }
        )
        runtime.cloud_query = (  # type: ignore[method-assign]
            lambda path, **_kwargs: dict(cloud_memory_manifest)
        )

        def fake_memory_sync_command(
            method: str,
            path: str,
            *,
            payload: dict[str, Any],
            idempotency_key: str,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            assert method == "PUT"
            assert path.endswith(f"/{project_id}/memory-manifest")
            assert idempotency_key
            assert set(payload) == {"entries", "expectedVersion"}
            assert all(
                set(entry)
                == {"memoryId", "memoryKind", "version", "contentHash", "updatedAt"}
                for entry in payload["entries"]
            )
            captured_memory_sync_payloads.append(payload)
            cloud_memory_manifest.update(
                {
                    "cloudState": "ready",
                    "manifestVersion": int(payload["expectedVersion"]) + 1,
                    "memoryCount": len(payload["entries"]),
                    "counts": repository._memory_counts(payload["entries"]),
                    "memoryDigest": sha256_text(canonical_json(payload["entries"])),
                    "entries": payload["entries"],
                    "updatedAt": utc_now(),
                }
            )
            return dict(cloud_memory_manifest)

        runtime.cloud_command = fake_memory_sync_command  # type: ignore[method-assign]

        initial_sync = repository.memory_sync_status(project_id=project_id)
        assert initial_sync["localState"] == "not_connected"
        assert initial_sync["cloudState"] == "not_connected"
        prepared_sync = repository.prepare_memory_sync(
            project_id=project_id,
            idempotency_key="gc15-memory-sync-prepare",
        )
        assert prepared_sync["localState"] == "ready"
        assert prepared_sync["cloudState"] == "ready"
        assert prepared_sync["overallState"] == "ready"
        assert prepared_sync["localSummary"]["counts"] == {
            "explicitMemory": 0,
            "favorite": 1,
            "correction": 0,
        }
        assert prepared_sync["boundary"] == {
            "l0ConversationIncluded": False,
            "answerBodyIncluded": False,
            "fileBodyIncluded": False,
            "localPathIncluded": False,
            "secretIncluded": False,
            "sourceHashesIncluded": True,
        }
        with runtime_connection(runtime.database_path, "local", read_only=True) as connection:
            manifest = connection.execute(
                """
                SELECT storage_key, media_type FROM object_manifests
                WHERE media_type=?
                """,
                (repository.MEMORY_SYNC_MEDIA_TYPE,),
            ).fetchone()
            assert manifest is not None
            safe_payload_text = (
                runtime.database_path.parent / str(manifest["storage_key"])
            ).read_text(encoding="utf-8")
            assert marker not in safe_payload_text
            safe_payload = json.loads(safe_payload_text)
            assert safe_payload["memoryCount"] == 1
            assert "content" not in safe_payload["entries"][0]
            assert connection.execute(
                """
                SELECT status FROM reconciliation_runs
                WHERE reconciliation_kind='member_memory_safe_summary_single_device_v1'
                """
            ).fetchone()[0] == "completed"
        assert captured_memory_sync_payloads
        assert marker not in canonical_json(captured_memory_sync_payloads[-1])

        captured_prompts: list[str] = []

        def fake_completion(*, messages: list[dict[str, str]], temperature: float) -> dict[str, Any]:
            del temperature
            captured_prompts.append(messages[0]["content"])
            return {"content": "测试回答", "provider": provider}

        def fake_cloud_command(
            method: str,
            path: str,
            *,
            payload: dict[str, Any],
            idempotency_key: str,
            **_kwargs: Any,
        ) -> dict[str, Any]:
            assert method == "POST"
            assert idempotency_key
            if path.endswith("/facts/corrections"):
                selected_hash = str(payload["selectedTextHash"])
                action_key = (
                    "answer-remember"
                    if payload["correctionKind"] == "remember"
                    else "answer-correction"
                )
                fact_id = "fact_" + sha256_text(
                    f"{action_key}\x1f{scope_id}\x1f{project_id}\x1f"
                    f"{answer_id}\x1f{selected_hash}"
                )[:30]
                return {
                    "clientId": project_id,
                    "answerId": answer_id,
                    "factId": fact_id,
                    "sourceSetId": (
                        "source_set_gc12_local_remember_test"
                        if payload["correctionKind"] == "remember"
                        else "source_set_gc12_local_test"
                    ),
                    "factObjectManifestId": "manifest_gc12_cloud_test",
                    "correctionKind": payload["correctionKind"],
                    "version": int(payload["expectedVersion"]) + 1,
                    "verificationState": "verified",
                    "cloudState": "ready",
                    "contextInvalidated": True,
                    "updatedAt": utc_now(),
                    "idempotentReplay": False,
                    "consumerPropagation": {
                        "state": "completed",
                        "retryable": False,
                        "message": "相关页面正在整理",
                        "directConsumers": [
                            "project_knowledge_context",
                            "workbench_next_answer",
                            "task_project_background",
                        ],
                        "pendingConsumers": [
                            "strategic_client_profile",
                            "project_reports",
                        ],
                    },
                }
            assert path == "/api/v2/workbench/answers"
            return {
                "answer": {
                    "answerId": payload["answerId"],
                    "threadId": payload["threadId"],
                    "sourceCount": payload["sourceCount"],
                    "answerHash": payload["answerHash"],
                    "version": 1,
                    "updatedAt": utc_now(),
                },
                "idempotentReplay": False,
            }

        runtime.organization_ai_completion = fake_completion  # type: ignore[method-assign]
        runtime.project_knowledge_context = lambda _project_id: {  # type: ignore[method-assign]
            "state": "ready",
            "organizationSharedKnowledge": [],
            "officialWebsiteFacts": [],
            "savedMemories": [],
        }
        runtime.cloud_command = fake_cloud_command  # type: ignore[method-assign]
        recalled = repository.run(
            project_id=project_id,
            question="现在你记得什么？",
            mode="balanced",
            idempotency_key="gc15-recall-before-revoke",
        )
        assert marker in captured_prompts[-1]
        assert recalled["answer"]["sourceManifest"]["localMemoryCount"] == 1
        assert recalled["answer"]["materialAccessMode"] == "memory_context"

        publishable_without_correction = repository.run(
            project_id=project_id,
            question="生成组织共享叙事时能使用什么？",
            mode="balanced",
            memory_policy="organization_publishable",
            idempotency_key="gc15-organization-memory-boundary",
        )
        assert marker not in captured_prompts[-1]
        assert publishable_without_correction["answer"]["sourceManifest"][
            "localMemoryCount"
        ] == 0

        revoked = repository.revoke_answer_memory(
            project_id=project_id,
            answer_id=answer_id,
            memory_kind="favorite",
            expected_version=int(saved["version"]),
            idempotency_key="gc15-revoke-favorite",
        )
        assert revoked["status"] == "archived"
        assert LocalProjectMaterialsRepository(runtime).knowledge_presentation(project_id)[
            "savedMemories"
        ] == []
        assert repository.memory_sync_status(project_id=project_id)["localState"] == "stale"
        repository.run(
            project_id=project_id,
            question="撤回后你还记得什么？",
            mode="balanced",
            idempotency_key="gc15-recall-after-revoke",
        )
        assert marker not in captured_prompts[-1]
        assert repository.answer(answer_id)["answerMarkdown"].endswith(marker)
        with runtime_connection(runtime.database_path, "local", read_only=True) as connection:
            assert connection.execute(
                "SELECT lifecycle_state FROM knowledge_documents WHERE id=?",
                (saved["memoryId"],),
            ).fetchone()[0] == "archived"
            assert connection.execute(
                "SELECT COUNT(*) FROM derivation_lineage WHERE derivative_object_id=? AND invalidated_at IS NOT NULL",
                (saved["memoryId"],),
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT lifecycle_state FROM ai_answers WHERE id=?",
                (answer_id,),
            ).fetchone()[0] == "active"

            correction_statement = "青色纸飞机是本项目环节十一的人工纠错标记。"
            repository.rebuild_strategic_profile = lambda **_kwargs: {  # type: ignore[method-assign]
                "localProjection": {"state": "ready", "projected": True}
            }
            correction = repository.correct_answer_fact(
            project_id=project_id,
            answer_id=answer_id,
            selected_text=f"需要长期记住：{marker}",
            correction_kind="correction",
            statement=correction_statement,
            idempotency_key="gc12-local-correction",
        )
        assert correction["canReanswer"] is True
        assert correction["overallState"] == "ready"
        assert correction["consumerPropagation"]["state"] == "completed"
        assert correction["version"] == 1
        correction_memories = LocalProjectMaterialsRepository(runtime).knowledge_presentation(
            project_id
        )["savedMemories"]
        assert correction_memories[0]["memoryKind"] == "correction"
        assert correction_memories[0]["summary"] == correction_statement
        assert correction_memories[0]["supersededText"] == f"需要长期记住：{marker}"
        assert repository.memory_sync_status(project_id=project_id)["localSummary"][
            "counts"
        ]["correction"] == 1
        repository.run(
            project_id=project_id,
            question="人工纠错后记住了什么？",
            mode="balanced",
            idempotency_key="gc12-recall-correction",
        )
        assert correction_statement in captured_prompts[-1]
        assert f"已被否定的旧表述：需要长期记住：{marker}" in captured_prompts[-1]
        assert "不得复述、括注、比较或暴露上述旧表述" in captured_prompts[-1]
        publishable_correction = repository.run(
            project_id=project_id,
            question="生成组织共享叙事时使用已确认事实。",
            mode="balanced",
            memory_policy="organization_publishable",
            idempotency_key="gc12-organization-correction-boundary",
        )
        assert correction_statement in captured_prompts[-1]
        assert f"需要长期记住：{marker}" not in captured_prompts[-1]
        assert publishable_correction["answer"]["sourceManifest"][
            "localMemoryCount"
        ] == 0
        with runtime_connection(runtime.database_path, "local", read_only=True) as connection:
            fact = connection.execute(
                "SELECT version, verification_state, authority_role FROM atomic_facts "
                "WHERE source_set_id='source_set_gc12_local_test'"
            ).fetchone()
            assert tuple(fact) == (1, "verified", "cloud")
            assert connection.execute(
                "SELECT COUNT(*) FROM ai_proposals"
            ).fetchone()[0] == 0
            assert connection.execute(
                "SELECT COUNT(*) FROM ai_approvals"
            ).fetchone()[0] == 0
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        remembered = repository.remember_answer_fact(
            project_id=project_id,
            answer_id=answer_id,
            statement="青色纸飞机是项目成员明确要求记住的测试标记。",
            idempotency_key="gc12-local-formal-remember",
        )
        assert remembered["correctionKind"] == "remember"
        assert remembered["overallState"] == "ready"
        remembered_items = LocalProjectMaterialsRepository(runtime).knowledge_presentation(
            project_id
        )["savedMemories"]
        assert any(
            item["memoryKind"] == "explicit_memory"
            and item["authority"] == "organization_cloud"
            for item in remembered_items
        )
        with pytest.raises(LocalRuntimeError, match="不支持的记忆类型"):
            repository.revoke_answer_memory(
                project_id=project_id,
                answer_id=answer_id,
                memory_kind="explicit_memory",
                expected_version=1,
                idempotency_key="gc12-formal-memory-no-revoke",
            )
    finally:
        cloud.__exit__(None, None, None)
