from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile

from backend.app.config import LocalConfig
from backend.app.main import create_app
from backend.app.ui_compat import StrictUiCompatibility
from backend.app.ui_domains import UiRequest
from strict_common.contracts import BUSINESS_CAPABILITIES, CONNECTED_CAPABILITIES


class _WorkspaceRuntimeStub:
    def list_workspaces(self) -> list[dict[str, Any]]:
        return [
            {
                "sandboxId": "local-draft",
                "kind": "local_draft",
                "runtimeStatus": "local_draft",
                "displayName": "未连接组织",
                "isActive": False,
                "updatedAt": "2026-07-30T00:00:00Z",
            },
            {
                "sandboxId": "organization-a",
                "kind": "organization",
                "runtimeStatus": "ready",
                "displayName": "组织 A",
                "isActive": True,
                "cloudInstanceId": "cloud-a",
                "organizationId": "org-a",
                "cloudApiUrl": "http://cloud-a.invalid",
                "identityState": "verified",
                "updatedAt": "2026-07-30T00:00:00Z",
            },
        ]

    def current(self) -> dict[str, Any]:
        return {
            "runtimeStatus": "ready",
            "statusMessage": "",
            "sessionSnapshot": {},
        }


def test_workspace_switcher_only_returns_real_organizations() -> None:
    compatibility = StrictUiCompatibility(_WorkspaceRuntimeStub())  # type: ignore[arg-type]
    payload = compatibility.workspaces()
    assert [workspace["id"] for workspace in payload["workspaces"]] == [
        "organization-a"
    ]
    assert payload["activeSandboxId"] == "organization-a"
    assert payload["localDraftSummary"]["available"] is False


def test_local_handshake_capability_sets_do_not_contradict(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "local"
    config = LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="foundation-token",
        secret_namespace="test.strict.foundation",
        test_mode=True,
    )
    headers = {"X-Yiyu-Desktop-Token": config.desktop_token}
    with TestClient(create_app(config)) as client:
        response = client.get("/api/v2/handshake", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["connectedCapabilities"]) == set(CONNECTED_CAPABILITIES)
    assert set(payload["notConnectedCapabilities"]) == (
        set(BUSINESS_CAPABILITIES) - set(CONNECTED_CAPABILITIES)
    )
    assert not (
        set(payload["connectedCapabilities"])
        & set(payload["notConnectedCapabilities"])
    )


def test_local_cors_allows_renderer_delete_requests(tmp_path: Path) -> None:
    data_dir = tmp_path / "cors"
    config = LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="cors-token",
        secret_namespace="test.strict.cors",
        test_mode=True,
    )
    with TestClient(create_app(config)) as client:
        response = client.options(
            "/api/v2/ui/clients/example",
            headers={
                "Origin": "http://127.0.0.1:4188",
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": (
                    "X-Yiyu-Desktop-Token,Idempotency-Key"
                ),
            },
        )
    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]


def test_local_ui_adapter_preserves_multipart_fields_and_file(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "multipart"
    config = LocalConfig(
        data_dir=data_dir,
        database_path=data_dir / "strict-local.db",
        host="127.0.0.1",
        port=47929,
        desktop_token="multipart-token",
        secret_namespace="test.strict.multipart",
        test_mode=True,
    )
    app = create_app(config)
    router = next(
        item
        for item in app.state.ui_compat.domain_registry.routers
        if item.domain == "strict_startup_status"
    )

    @router.post(r"test/multipart")
    def multipart_handler(
        _: Any,
        request: UiRequest,
        __: Any,
    ) -> dict[str, Any]:
        upload = request.body["file"]
        assert isinstance(upload, UploadFile)
        return {
            "title": request.body["title"],
            "fileName": upload.filename,
        }

    headers = {"X-Yiyu-Desktop-Token": config.desktop_token}
    with TestClient(app) as client:
        response = client.post(
            "/api/v2/ui/test/multipart",
            headers=headers,
            data={"title": "测试资料"},
            files={"file": ("material.txt", b"strict-v2", "text/plain")},
        )
    assert response.status_code == 200
    assert response.json() == {
        "title": "测试资料",
        "fileName": "material.txt",
    }
