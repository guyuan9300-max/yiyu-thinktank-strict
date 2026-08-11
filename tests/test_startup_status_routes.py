from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.app.ui_domains import startup_status
from backend.app.ui_domains.routing import NOT_HANDLED, UiRequest


class _Runtime:
    def __init__(self, root: Path):
        self.database_path = root / "strict-local.db"
        self.identity = SimpleNamespace(
            manifest_hash="strict-manifest-hash",
            build_id="strict-build-id",
        )

    def diagnostics(self) -> dict:
        return {
            "database": {
                "manifestHash": "strict-manifest-hash",
                "buildId": "strict-build-id",
            }
        }

    def cloud_query(self, path: str) -> dict:
        if path == "/api/v2/organization-access/members":
            return {
                "items": [
                    {
                        "id": "membership_startup_status",
                        "fullName": "部门负责人",
                        "primaryRole": "admin",
                        "accountStatus": "approved",
                        "membershipStatus": "active",
                        "departmentId": "department_startup_status",
                        "managementTitleId": "title_ceo",
                        "isDepartmentLead": True,
                        "visibilityScope": "organization",
                        "version": 3,
                    }
                ]
            }
        if path == "/api/v2/organization-access/management-titles":
            return {
                "items": [
                    {
                        "id": "title_ceo",
                        "name": "CEO",
                        "state": "active",
                        "version": 2,
                        "updatedAt": "2026-08-07T00:00:00Z",
                    },
                    {
                        "id": "title_advisor",
                        "name": "顾问",
                        "state": "active",
                        "version": 1,
                        "updatedAt": "2026-08-07T00:00:00Z",
                    },
                ]
            }
        raise AssertionError(path)

    def current(self) -> dict:
        return {
            "sandbox": {"sandboxId": "sandbox_startup_status"},
            "sessionSnapshot": {
                "organization": {
                    "organizationId": "org_startup_status",
                    "name": "启动状态测试组织",
                    "updatedAt": "2026-08-07T00:00:00Z",
                },
                "departments": [
                    {
                        "departmentId": "department_startup_status",
                        "name": "技术创新部",
                        "color": "#2563eb",
                        "lifecycleState": "active",
                        "version": 2,
                    }
                ],
                "members": [
                    {
                        "membershipId": "membership_startup_status",
                        "principalId": "principal_startup_status",
                        "displayName": "部门负责人",
                        "systemRole": "admin",
                        "status": "active",
                        "version": 3,
                    }
                ],
                "departmentAssignments": [
                    {
                        "assignmentId": "assignment_startup_status",
                        "membershipId": "membership_startup_status",
                        "departmentId": "department_startup_status",
                        "assignmentRole": "department_lead",
                        "status": "active",
                        "version": 2,
                        "lifecycleState": "active",
                    }
                ],
            },
            "databaseIdentity": {"buildId": "strict-build-id"},
        }


def _request(path: str, *, query: dict[str, str] | None = None) -> UiRequest:
    return UiRequest(
        method="GET",
        path=path,
        query=query or {},
        body={},
        idempotency_key="",
    )


def test_startup_router_has_only_the_seven_registry_status_routes_and_no_old_table_names() -> None:
    assert {(item.method, item.pattern) for item in startup_status.router.routes} == {
        ("GET", "system/source-integrity"),
        ("GET", "system/active-background-tasks"),
        ("GET", "audio-transcription-jobs/recent"),
        ("GET", "local-asr/model/status"),
        ("GET", "settings/transcription-preference"),
        ("GET", "settings/org-model/profile"),
        ("POST", "settings/org-model/profile"),
    }
    source = Path(startup_status.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "workspace_sandboxes",
        "workspace_bindings",
        "workspace_session_snapshots",
        "command_envelopes",
        "scoped_configuration_records",
        "organization_records",
        "SELECT ",
        "INSERT ",
        "UPDATE ",
    ):
        assert forbidden not in source


def test_startup_statuses_are_responsive_and_do_not_fake_empty_authority(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        startup_status,
        "_sensevoice_status",
        lambda _compatibility: {
            "modelName": "SenseVoiceSmall",
            "installed": False,
            "state": "not_connected",
            "retryable": True,
            "pollingEnabled": False,
        },
    )
    monkeypatch.setattr(
        startup_status,
        "_active_background_tasks",
        lambda _compatibility, _request, _match: {
            "tasks": [{"operationId": "operation-one", "state": "processing"}],
            "count": 1,
            "state": "ready",
            "pollingEnabled": True,
        },
    )
    monkeypatch.setattr(
        startup_status,
        "_recent_audio_jobs",
        lambda _compatibility, _request, _match: {
            "jobs": [{"operationId": "audio-one", "state": "completed"}],
            "state": "ready",
            "pollingEnabled": False,
            "retryable": True,
        },
    )
    compatibility = SimpleNamespace(runtime=_Runtime(tmp_path))

    integrity = startup_status.router.dispatch(
        compatibility,
        _request(
            "system/source-integrity",
            query={"frontendBuildVersion": "renderer-test"},
        ),
    )
    background = startup_status.router.dispatch(
        compatibility,
        _request("system/active-background-tasks"),
    )
    audio = startup_status.router.dispatch(
        compatibility,
        _request("audio-transcription-jobs/recent"),
    )
    asr = startup_status.router.dispatch(
        compatibility,
        _request("local-asr/model/status"),
    )
    transcription = startup_status.router.dispatch(
        compatibility,
        _request("settings/transcription-preference"),
    )
    profile = startup_status.router.dispatch(
        compatibility,
        _request("settings/org-model/profile"),
    )

    assert all(
        item is not NOT_HANDLED
        for item in (integrity, background, audio, asr, transcription, profile)
    )
    assert integrity["runningHash"] == "strict-manifest-hash"
    assert integrity["frontendBuildVersion"] == "renderer-test"
    assert background["state"] == "ready"
    assert background["tasks"][0]["operationId"] == "operation-one"
    assert background["pollingEnabled"] is True
    assert audio["state"] == "ready"
    assert audio["jobs"][0]["operationId"] == "audio-one"
    assert audio["pollingEnabled"] is False
    assert asr["state"] == "not_connected"
    assert transcription == {
        "provider": "local",
        "sandboxId": "sandbox_startup_status",
        "state": "not_connected",
        "message": "个人转写偏好尚未迁入88表；本次按本机转写执行",
        "pollingEnabled": False,
        "retryable": True,
    }
    assert profile["organization"]["organizationId"] == "org_startup_status"
    assert profile["departments"][0]["name"] == "技术创新部"
    assert profile["departments"][0]["leaderUserId"] == "membership_startup_status"
    assert profile["departments"][0]["leaderName"] == "部门负责人"
    assert profile["bindings"] == [
        {
            "userId": "membership_startup_status",
            "version": 3,
            "departmentId": "department_startup_status",
            "primaryRoleId": "title_ceo",
            "managerUserId": None,
            "isManager": True,
            "visibilityScope": "organization",
            "projectRoleLabels": [],
            "currentFocus": "",
            "taskEditScope": "self",
            "canApproveTasks": False,
            "canReassignTasks": False,
            "canChangeDeadline": False,
            "updatedAt": "2026-08-07T00:00:00Z",
        }
    ]
    assert [item["name"] for item in profile["roles"]] == ["CEO", "顾问"]
    assert profile["authorityStates"]["identityStructure"]["state"] == "ready"
    assert (
        profile["authorityStates"]["unfrozenSemanticFields"]["state"]
        == "not_connected"
    )
