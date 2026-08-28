from __future__ import annotations

from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from cloud_backend.app.config import CloudConfig
from cloud_backend.app.main import create_app
from cloud_backend.app.provisioning import provision_cloud_instance
from strict_common.ids import sha256_text, utc_now
from strict_common.schema import database_identity, runtime_connection
from strict_common.security import (
    PASSWORD_SCHEME,
    hash_password,
    normalize_identifier,
)


def strict_cloud_test_client(
    data_dir: Path,
    *,
    bootstrap_token: str,
    cloud_instance_id: str,
    database_name: str = "strict-cloud.db",
    master_key: str | None = None,
) -> tuple[TestClient, Path, CloudConfig]:
    """Build an isolated, explicitly provisioned strict-cloud test runtime.

    Production cloud identity must never be inferred or injected into tests.
    Each caller provides a stable test-only identity and receives a disposable
    database under pytest's temporary directory.
    """

    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / database_name
    provision_cloud_instance(
        database,
        expected_cloud_instance_id=cloud_instance_id,
    )
    config = CloudConfig(
        data_dir=data_dir,
        database_path=database,
        bootstrap_token=bootstrap_token,
        master_key=master_key or Fernet.generate_key().decode(),
        cloud_instance_id=cloud_instance_id,
    )
    return TestClient(create_app(config)), database, config


def provision_test_organization(
    client: TestClient,
    *,
    organization_name: str,
    display_name: str,
    email: str,
    password: str,
    phone: str | None = None,
    create_default_project: bool = True,
) -> dict[str, Any]:
    """Provision an organization fixture outside the disabled product route.

    The strict product intentionally rejects ``bootstrap-organization`` until
    that golden chain is connected. Tests that exercise an already-ready cloud
    must therefore provision authority explicitly instead of restoring a fake
    runtime fallback or calling the disabled user-facing endpoint.
    """

    repository = client.app.state.repository
    database = Path(repository.database_path)
    identity = database_identity(database, "cloud")
    digest = sha256_text(
        f"{repository.cloud_instance_id}\x1f{organization_name}\x1f{email}"
    )[:20]
    organization_id = f"org_test_{digest}"
    scope_id = f"scope_test_{digest}"
    principal_id = f"principal_test_{digest}"
    membership_id = f"membership_test_{digest}"
    credential_id = f"credential_test_{digest}"
    now = utc_now()
    credential_dir = database.parent / ".test-credentials"
    credential_dir.mkdir(parents=True, exist_ok=True)
    credential_path = credential_dir / f"{credential_id}.json"
    credential_path.write_text(
        '{"hashScheme":"'
        + PASSWORD_SCHEME
        + '","secretHash":"'
        + hash_password(password)
        + '"}',
        encoding="utf-8",
    )

    contacts = [("email", email)]
    if phone:
        contacts.append(("phone", phone))

    with runtime_connection(database, "cloud") as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                """
                INSERT INTO organizations (
                    id, lifecycle_state, version, updated_at, record_kind,
                    name, created_at, deleted_at
                ) VALUES (?, 'active', 1, ?, 'organization', ?, ?, NULL)
                """,
                (organization_id, now, organization_name, now),
            )
            connection.execute(
                """
                INSERT INTO authorization_scopes (
                    id, scope_kind, organization_id, policy_version,
                    created_at, updated_at, status, version,
                    lifecycle_state, deleted_at
                ) VALUES (?, 'organization', ?, 1, ?, ?, 'active', 1,
                          'active', NULL)
                """,
                (scope_id, organization_id, now, now),
            )
            connection.execute(
                """
                INSERT INTO principals (
                    id, status, identity_version, updated_at, principal_kind,
                    display_name, version, lifecycle_state, created_at,
                    deleted_at
                ) VALUES (?, 'active', 1, ?, 'person', ?, 1, 'active', ?, NULL)
                """,
                (principal_id, now, display_name, now),
            )
            for index, (contact_kind, raw_value) in enumerate(contacts):
                normalized_kind, normalized_value = normalize_identifier(raw_value)
                if normalized_kind != contact_kind:
                    raise AssertionError("test contact normalization changed contact kind")
                connection.execute(
                    """
                    INSERT INTO principals (
                        id, status, identity_version, updated_at, principal_kind,
                        parent_principal_id, contact_type, normalized_contact,
                        verification_state, version, lifecycle_state, created_at,
                        deleted_at
                    ) VALUES (?, 'active', 1, ?, 'contact', ?, ?, ?,
                              'verified', 1, 'active', ?, NULL)
                    """,
                    (
                        f"contact_test_{digest}_{index}",
                        now,
                        principal_id,
                        contact_kind,
                        normalized_value,
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT INTO principals (
                    id, status, identity_version, updated_at, principal_kind,
                    parent_principal_id, credential_type, secret_reference,
                    credential_state, version, lifecycle_state, created_at,
                    deleted_at
                ) VALUES (?, 'active', 1, ?, 'credential', ?, 'password', ?,
                          'active', 1, 'active', ?, NULL)
                """,
                (credential_id, now, principal_id, str(credential_path), now),
            )
            connection.execute(
                """
                INSERT INTO organization_memberships (
                    id, scope_id, principal_id, role_key, status, version,
                    record_kind, visibility_scope, lifecycle_state, created_at,
                    updated_at, deleted_at
                ) VALUES (?, ?, ?, 'admin', 'active', 1, 'membership',
                          'organization', 'active', ?, ?, NULL)
                """,
                (membership_id, scope_id, principal_id, now, now),
            )
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    session = repository.login(
        identifier=email,
        password=password,
        idempotency_key=f"test-login-{digest}",
    )
    if create_default_project:
        created = client.post(
            "/api/v2/domain/project-materials/projects",
            headers={
                "Authorization": f"Bearer {session['accessToken']}",
                "Idempotency-Key": f"test-default-project-{digest}",
            },
            json={"name": f"{organization_name}内部项目"},
        )
        if created.status_code != 201:
            raise AssertionError(created.text)
    return session
