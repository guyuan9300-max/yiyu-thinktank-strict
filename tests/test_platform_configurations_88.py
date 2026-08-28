from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cloud_backend.app.domain_routes.organization_access import register_routes
from cloud_backend.app.repositories.platform_configurations import (
    PlatformConfigurationRepository,
)
from cloud_backend.app.repositories.platform_integrations import (
    FEISHU_CONFIGURATION_KIND,
    FEISHU_MEMBER_AUTHORIZATION_KIND,
    FEISHU_MEMBER_DELIVERY_PROFILE_KIND,
    PlatformIntegrationsRepository,
)
from cloud_backend.app.repositories.platform_operations import PlatformOperationRepository
from cloud_backend.app.repository import RepositoryError
from strict_common.schema import runtime_connection
from tests.test_gc14_workbench_answer import _repository


class _TenantTokenResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {"code": 0, "tenant_access_token": "temporary-token"}


class _TenantTokenClient:
    def __init__(self, calls: list[dict[str, object]], **_kwargs: object):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, object]) -> _TenantTokenResponse:
        self.calls.append({"url": url, "json": json})
        return _TenantTokenResponse()


def test_platform_configuration_uses_provider_resources_and_external_secret_store(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    configurations = PlatformConfigurationRepository(repository)

    saved = configurations.upsert(
        identity,
        configuration_kind=FEISHU_CONFIGURATION_KIND,
        scope_kind="organization",
        provider="feishu",
        public_config={
            "appId": "cli_platform_88",
            "callbackMode": "cloud_relay",
            "customCallbackUrl": "",
        },
        expected_version=0,
        idempotency_key="platform-88-feishu-organization",
        secret_bundle={"appSecret": "secret-never-in-sqlite"},
        secret_action="replace",
    )
    assert saved["version"] == 1
    assert saved["hasCredentials"] is True
    assert configurations.secret_exact(
        identity,
        configuration_kind=FEISHU_CONFIGURATION_KIND,
        scope_kind="organization",
    ) == {"appSecret": "secret-never-in-sqlite"}

    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT owner_kind,owner_principal_id,owner_membership_id,public_config,"
            "secret_reference,secret_fingerprint FROM provider_resources "
            "WHERE resource_kind=?",
            (FEISHU_CONFIGURATION_KIND,),
        ).fetchone()
        assert tuple(row[:3]) == ("organization", None, None)
        assert "cli_platform_88" in str(row[3])
        assert row[4] and row[5]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("SELECT 1 FROM scoped_configuration_records")
    assert b"secret-never-in-sqlite" not in repository.database_path.read_bytes()


def test_support_and_feedback_are_command_ledger_records_without_frozen_tables(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    platform = PlatformIntegrationsRepository(repository)
    support = platform.create_support_request(
        identity,
        {
            "targetScope": "organization",
            "requestType": "clarification",
            "urgency": "medium",
            "summary": "需要协助确认严格链路",
        },
        "support-create-88",
    )
    resolved = platform.resolve_support_request(
        identity,
        support["id"],
        {"status": "resolved", "resolutionNote": "已确认"},
        "support-resolve-88",
    )
    assert platform.list_support_requests(identity) == [resolved]

    feedback = platform.create_feedback(
        identity,
        {
            "category": "bug",
            "severity": "medium",
            "title": "严格反馈入口验证",
            "description": "只写命令回执，不写冻结反馈表",
        },
        "feedback-create-88",
    )
    listed = platform.list_feedback(identity)
    assert listed["items"] == [feedback["record"]]
    assert listed["centralError"] == "中心反馈平台尚未连接"

    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT count(*) FROM commands WHERE aggregate_type IN "
            "('support_request','software_feedback')"
        ).fetchone()[0] == 3
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            )
        }
        assert "external_provider_resources" not in names
        assert "delivery_outbox" not in names


@pytest.mark.parametrize(
    ("configuration_kind", "public_config", "secret_bundle"),
    (
        (
            FEISHU_MEMBER_AUTHORIZATION_KIND,
            {"linked": True, "authorizationState": "ready", "openId": "ou_test"},
            {"accessToken": "member-access-token"},
        ),
        (
            FEISHU_MEMBER_DELIVERY_PROFILE_KIND,
            {"deliveryStatus": "matched", "receiveIdType": "open_id"},
            {"mobile": "+8613600000000"},
        ),
        (
            "transcription_preference",
            {"provider": "local_asr", "language": "zh"},
            {"localModelToken": "model-token"},
        ),
        (
            "organization_model_profile",
            {"provider": "doubao", "modelName": "Doubao-Seed-2.1-pro"},
            {"apiKey": "model-api-key"},
        ),
    ),
)
def test_member_oauth_model_and_asr_configuration_share_the_88_table_adapter(
    tmp_path: Path,
    configuration_kind: str,
    public_config: dict[str, object],
    secret_bundle: dict[str, object],
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    configurations = PlatformConfigurationRepository(repository)
    result = configurations.upsert(
        identity,
        configuration_kind=configuration_kind,
        scope_kind="personal",
        provider=str(public_config.get("provider") or "feishu"),
        public_config=public_config,
        expected_version=0,
        idempotency_key=f"platform-88-{configuration_kind}",
        secret_bundle=secret_bundle,
        secret_action="replace",
    )
    replay = configurations.upsert(
        identity,
        configuration_kind=configuration_kind,
        scope_kind="personal",
        provider=str(public_config.get("provider") or "feishu"),
        public_config=public_config,
        expected_version=0,
        idempotency_key=f"platform-88-{configuration_kind}",
        secret_bundle=secret_bundle,
        secret_action="replace",
    )
    assert replay == result
    assert configurations.secret_exact(
        identity,
        configuration_kind=configuration_kind,
        scope_kind="personal",
    ) == secret_bundle
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT owner_kind,owner_principal_id,owner_membership_id "
            "FROM provider_resources WHERE resource_kind=?",
            (configuration_kind,),
        ).fetchone()
        assert tuple(row) == (
            "membership",
            identity.principal_id,
            identity.membership_id,
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_unconfigured_feishu_status_is_explicit_on_an_88_table_database(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    status = PlatformIntegrationsRepository(repository).feishu_integration(identity)
    assert status["state"] == "not_connected"
    assert status["authorizationBlockedReason"] == "feishu_not_configured"
    assert status["retryable"] is True


def test_pending_member_oauth_state_resolves_identity_from_88_tables(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    configurations = PlatformConfigurationRepository(repository)
    configurations.upsert(
        identity,
        configuration_kind=FEISHU_MEMBER_AUTHORIZATION_KIND,
        scope_kind="personal",
        provider="feishu",
        public_config={
            "linked": False,
            "authorizationState": "authorization_pending",
            "pendingState": "state-platform-88",
            "pendingStateExpiresAt": "9999-12-31T23:59:59.999Z",
        },
        expected_version=0,
        idempotency_key="platform-88-oauth-state",
        secret_bundle={"pendingRelayClaimSecret": "claim-secret"},
        secret_action="replace",
    )
    resolved, pending, version = PlatformIntegrationsRepository(
        repository
    )._identity_for_feishu_oauth_state("state-platform-88")
    assert resolved.membership_id == identity.membership_id
    assert resolved.principal_id == identity.principal_id
    assert pending["authorizationState"] == "authorization_pending"
    assert version == 1


def test_feishu_save_and_validation_status_never_query_frozen_configuration_tables(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    calls: list[dict[str, object]] = []
    platform = PlatformIntegrationsRepository(
        repository,
        feishu_http_client_factory=lambda **kwargs: _TenantTokenClient(
            calls, **kwargs
        ),
    )
    saved = platform.save_feishu(
        identity,
        {
            "appId": "cli_feishu_88_ready",
            "appSecret": "app-secret-outside-sqlite",
            "scopeKind": "organization",
            "expectedVersion": 0,
        },
        "platform-88-save-and-validate",
    )
    assert saved["state"] == "ready"
    assert saved["authorizationReady"] is True
    assert saved["lastValidationStatus"] == "succeeded"
    assert len(calls) == 1
    assert b"app-secret-outside-sqlite" not in repository.database_path.read_bytes()
    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT public_config,version FROM provider_resources "
            "WHERE resource_kind=?",
            (FEISHU_CONFIGURATION_KIND,),
        ).fetchone()
        assert '"lastValidationStatus":"succeeded"' in str(row["public_config"])
        assert int(row["version"]) == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_model_and_asr_routes_use_provider_resources_on_an_88_table_database(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    app = FastAPI()
    register_routes(app, repository, lambda: identity)
    client = TestClient(app)

    initial = client.get(
        "/api/v2/organization-access/settings/transcription-preference"
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["provider"] == "local"
    saved_preference = client.post(
        "/api/v2/organization-access/settings/transcription-preference",
        headers={"Idempotency-Key": "platform-88-transcription-route"},
        json={"provider": "organization_cloud", "expectedVersion": 0},
    )
    assert saved_preference.status_code == 200, saved_preference.text
    assert saved_preference.json()["provider"] == "organization_cloud"

    saved_speech = client.put(
        "/api/v2/organization-access/settings/speech-model/effective",
        headers={"Idempotency-Key": "platform-88-speech-route"},
        json={
            "provider": "volcano",
            "modelId": "asr-model",
            "enabled": True,
            "credentials": {"apiKey": "asr-api-key-outside-sqlite"},
            "scopeKind": "organization",
            "expectedVersion": 0,
        },
    )
    assert saved_speech.status_code == 200, saved_speech.text
    assert saved_speech.json()["hasCredentials"] is True
    probe = client.post(
        "/api/v2/organization-access/settings/speech-model/test",
        headers={"Idempotency-Key": "platform-88-speech-probe"},
        json={},
    )
    assert probe.status_code == 200, probe.text
    assert probe.json()["state"] == "registered_not_probed"
    assert b"asr-api-key-outside-sqlite" not in repository.database_path.read_bytes()


def test_organization_brand_route_is_versioned_idempotent_and_admin_scoped(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    app = FastAPI()
    register_routes(app, repository, lambda: identity)
    client = TestClient(app)
    logo_data_url = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
        "+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    initial = client.get("/api/v2/organization-access/settings/organization-brand")
    assert initial.status_code == 200, initial.text
    assert initial.json()["displayName"] == ""
    assert initial.json()["expectedVersion"] == 0

    payload = {
        "displayName": "星丛",
        "logoDataUrl": logo_data_url,
        "expectedVersion": 0,
    }
    saved = client.post(
        "/api/v2/organization-access/settings/organization-brand",
        headers={"Idempotency-Key": "organization-brand-save-v1"},
        json=payload,
    )
    replay = client.post(
        "/api/v2/organization-access/settings/organization-brand",
        headers={"Idempotency-Key": "organization-brand-save-v1"},
        json=payload,
    )
    assert saved.status_code == 200, saved.text
    assert replay.status_code == 200, replay.text
    assert saved.json() == replay.json()
    assert saved.json()["version"] == 1

    with runtime_connection(repository.database_path, "cloud") as connection:
        row = connection.execute(
            "SELECT owner_kind,resource_kind,version,public_config FROM provider_resources "
            "WHERE resource_kind='organization_brand'"
        ).fetchone()
        assert tuple(row[:3]) == ("organization", "organization_brand", 1)
        assert '"displayName":"星丛"' in str(row["public_config"])
        connection.execute(
            "UPDATE organization_memberships SET role_key='employee' WHERE id=?",
            (identity.membership_id,),
        )
        connection.commit()

    with pytest.raises(RepositoryError) as denied:
        client.post(
            "/api/v2/organization-access/settings/organization-brand",
            headers={"Idempotency-Key": "organization-brand-non-admin"},
            json={**payload, "displayName": "不应写入", "expectedVersion": 1},
        )
    assert denied.value.status_code == 403
    assert denied.value.code == "admin_required"


def test_organization_brand_route_rejects_mismatched_image_content(tmp_path: Path) -> None:
    repository, identity, _payload = _repository(tmp_path)
    app = FastAPI()
    register_routes(app, repository, lambda: identity)
    client = TestClient(app)
    with pytest.raises(RepositoryError) as invalid:
        client.post(
            "/api/v2/organization-access/settings/organization-brand",
            headers={"Idempotency-Key": "organization-brand-invalid-logo"},
            json={
                "displayName": "星丛",
                "logoDataUrl": "data:image/png;base64,aGVsbG8=",
                "expectedVersion": 0,
            },
        )
    assert invalid.value.status_code == 422
    assert invalid.value.code == "organization_brand_logo_invalid"


def test_platform_operation_receipt_uses_only_88_table_command_objects(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    operations = PlatformOperationRepository(repository)
    first = operations.record(
        identity,
        command_type="feishu.personal_authorization.start",
        aggregate_type="personal_provider_authorization",
        aggregate_id="member-feishu-test",
        payload={"membershipId": identity.membership_id},
        idempotency_key="platform-88-operation",
        provider="feishu",
        resource_kind="member_authorization",
        remote_id="member-feishu-test",
        outcome="queued",
    )
    replay = operations.record(
        identity,
        command_type="feishu.personal_authorization.start",
        aggregate_type="personal_provider_authorization",
        aggregate_id="member-feishu-test",
        payload={"membershipId": identity.membership_id},
        idempotency_key="platform-88-operation",
        provider="feishu",
        resource_kind="member_authorization",
        remote_id="member-feishu-test",
        outcome="queued",
    )
    assert replay == first
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM idempotency_records").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM operation_attempts").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("SELECT 1 FROM command_idempotency")


def test_member_feishu_grant_uses_secured_resource_policy_and_object_grant(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    platform = PlatformIntegrationsRepository(repository)
    platform._write_personal_feishu_authorization_grant(identity)
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM secured_resources "
            "WHERE resource_type_key='feishu_member_authorization'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM policy_versions "
            "WHERE policy_spec_schema_version='yiyu.feishu-capabilities.v1'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM object_grants WHERE status='active'"
        ).fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    platform._revoke_personal_feishu_authorization_grant(identity)
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM object_grants WHERE status='active'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT lifecycle_state FROM secured_resources "
            "WHERE resource_type_key='feishu_member_authorization'"
        ).fetchone()[0] == "archived"


def test_personal_oauth_start_and_revoke_use_88_table_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    calls: list[dict[str, object]] = []
    platform = PlatformIntegrationsRepository(
        repository,
        feishu_http_client_factory=lambda **kwargs: _TenantTokenClient(
            calls, **kwargs
        ),
    )
    platform.save_feishu(
        identity,
        {
            "appId": "cli_feishu_oauth_88",
            "appSecret": "oauth-app-secret",
            "scopeKind": "organization",
            "expectedVersion": 0,
        },
        "platform-88-oauth-application",
    )
    monkeypatch.setattr(
        platform,
        "_register_feishu_oauth_relay_session",
        lambda **_kwargs: None,
    )
    started = platform.start_personal_feishu_authorization(
        identity,
        idempotency_key="platform-88-oauth-start",
    )
    assert started["state"]
    assert started["authorizeUrl"].startswith("https://accounts.feishu.cn/")
    assert platform.personal_feishu_authorization(identity)["state"] == "processing"
    cleared = platform.clear_personal_feishu_authorization(
        identity,
        idempotency_key="platform-88-oauth-clear",
    )
    assert cleared["linked"] is False
    assert cleared["state"] == "not_connected"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_feishu_executors_return_real_preflight_without_frozen_sql(
    tmp_path: Path,
) -> None:
    repository, identity, _payload = _repository(tmp_path)
    platform = PlatformIntegrationsRepository(repository)
    with pytest.raises(RepositoryError) as reverse_sync:
        platform.command(
            identity,
            resource_path="feishu-sync/documents",
            authorization_scope="personal",
            method="POST",
            query={},
            payload={"localId": "document-test"},
            idempotency_key="platform-88-retired-sync",
        )
    assert reverse_sync.value.code == "feishu_document_reverse_projection_retired"
    with pytest.raises(RepositoryError) as keyword_search:
        platform.command(
            identity,
            resource_path="feishu-doc-import/search",
            authorization_scope="personal",
            method="POST",
            query={},
            payload={"query": "test"},
            idempotency_key="platform-88-retired-search",
        )
    assert keyword_search.value.code == "feishu_import_action_invalid"
    with runtime_connection(repository.database_path, "cloud") as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT COUNT(*) FROM operation_attempts WHERE transport_state='blocked'"
        ).fetchone()[0] == 0
