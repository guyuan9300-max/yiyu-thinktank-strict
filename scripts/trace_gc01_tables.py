#!/usr/bin/env python3
"""Capture GC-01 SQLite table reads/writes without logging business values."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.runtime import WorkspaceRuntime
from backend.app.secret_store import MemorySecretStore
from cloud_backend.app.repository import CloudRepository
from cloud_backend.app.repositories.gc01_authorization import (
    backfill_authorization_projections,
)
from strict_common.ids import utc_now
from strict_common.schema import runtime_connection
from tests.test_gc01_authorization import _seed_gc01_cloud
from tests.test_gc01_local_login import LoginCloud


REGISTRY_PATH = ROOT / "contracts" / "gc01-registry.v1.json"
MANIFEST_PATH = ROOT / "contracts" / "strict-local-schema-manifest.v1.json"
ALLOWED_TABLES = {
    str(item["name"])
    for item in json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["allowedTables"]
}


def _capture(
    directory: Path,
    name: str,
    role: str,
    operation: Callable[[], Any],
) -> tuple[Any, dict[str, Any]]:
    audit_path = directory / f"{name}.jsonl"
    os.environ["YIYU_STRICT_SQL_AUDIT_FILE"] = str(audit_path)
    try:
        result = operation()
    finally:
        os.environ.pop("YIYU_STRICT_SQL_AUDIT_FILE", None)
    records = [
        json.loads(line)
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    reads: set[str] = set()
    writes: set[str] = set()
    ddl = False
    for record in records:
        tables = {
            str(item)
            for item in record.get("tables") or []
            if str(item) in ALLOWED_TABLES
        }
        kind = str(record.get("statementKind") or "").upper()
        ddl = ddl or bool(record.get("ddl"))
        if kind in {"SELECT", "PRAGMA"}:
            reads.update(tables)
        elif kind in {"INSERT", "UPDATE", "DELETE", "REPLACE"}:
            writes.update(tables)
        elif kind == "WITH":
            reads.update(tables)
    return result, {
        "databaseRole": role,
        "reads": sorted(reads),
        "writes": sorted(writes),
        "statementCount": len(records),
        "ddl": ddl,
    }


def _expected(registry: dict[str, Any], control_id: str, role: str) -> dict[str, set[str]]:
    control = next(
        item for item in registry["controls"] if item["controlId"] == control_id
    )
    prefix = "local" if role == "local" else "cloud"
    return {
        "reads": set(control[f"{prefix}Read"]),
        "writes": set(control[f"{prefix}Write"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    operations: dict[str, dict[str, Any]] = {}
    mappings = {
        "local_login": ("gc01.control.auth.login", "local"),
        "local_refresh": ("gc01.control.auth.refresh", "local"),
        "local_switch": ("gc01.control.workspace.switch", "local"),
        "local_logout": ("gc01.control.auth.logout", "local"),
        "cloud_login": ("gc01.control.auth.login", "cloud"),
        "cloud_refresh": ("gc01.control.auth.refresh", "cloud"),
        "cloud_current": ("gc01.control.workspace.switch", "cloud"),
        "cloud_logout": ("gc01.control.auth.logout", "cloud"),
    }
    with tempfile.TemporaryDirectory(prefix="yiyu-gc01-trace-") as raw:
        directory = Path(raw)
        local_database = directory / "strict-local.db"
        secrets = MemorySecretStore()
        first_cloud = LoginCloud(
            instance="cli_trace_first",
            organization="org_trace_first",
            scope="scope_trace_first",
            email="trace-first@example.com",
        )
        second_cloud = LoginCloud(
            instance="cli_trace_second",
            organization="org_trace_second",
            scope="scope_trace_second",
            email="trace-second@example.com",
        )
        clouds = {
            "http://trace-first.local": first_cloud,
            "http://trace-second.local": second_cloud,
        }
        runtime = WorkspaceRuntime(
            local_database,
            secrets,
            cloud_factory=clouds.__getitem__,
        )
        first, operations["local_login"] = _capture(
            directory,
            "local_login",
            "local",
            lambda: runtime.login(
                cloud_api_url="http://trace-first.local",
                identifier=first_cloud.email,
                password="test-password",
                idempotency_key="trace-local-login",
            ),
        )
        first_cloud.expire_access = True
        _, operations["local_refresh"] = _capture(
            directory,
            "local_refresh",
            "local",
            runtime.restore_at_startup,
        )
        second = runtime.login(
            cloud_api_url="http://trace-second.local",
            identifier=second_cloud.email,
            password="test-password",
            idempotency_key="trace-second-setup",
        )
        first_sandbox_id = str(first["sandbox"]["sandboxId"])
        second_sandbox_id = str(second["sandbox"]["sandboxId"])
        assert first_sandbox_id != second_sandbox_id
        first_cloud.expire_access = True
        _, operations["local_switch"] = _capture(
            directory,
            "local_switch",
            "local",
            lambda: runtime.switch(
                first_sandbox_id,
                idempotency_key="trace-local-switch",
                request_seq=1_900_000_000_001,
            ),
        )
        _, operations["local_logout"] = _capture(
            directory,
            "local_logout",
            "local",
            lambda: runtime.logout(idempotency_key="trace-local-logout"),
        )

        cloud_database = directory / "strict-cloud.db"
        cloud_config, _ = _seed_gc01_cloud(cloud_database)
        with runtime_connection(cloud_database, "cloud") as connection:
            backfill_authorization_projections(
                connection,
                origin_instance_id=cloud_config.cloud_instance_id or "",
            )
        cloud_repository = CloudRepository(
            cloud_database,
            cloud_instance_id=cloud_config.cloud_instance_id,
            master_key=cloud_config.master_key,
        )
        cloud_login, operations["cloud_login"] = _capture(
            directory,
            "cloud_login",
            "cloud",
            lambda: cloud_repository.login(
                identifier="gc01-admin@example.com",
                password="gc01-admin-password",
                idempotency_key="trace-cloud-login",
            ),
        )
        access_token = str(cloud_login["accessToken"])
        refresh_token = str(cloud_login["refreshToken"])
        _, operations["cloud_current"] = _capture(
            directory,
            "cloud_current",
            "cloud",
            lambda: cloud_repository.organization_snapshot(
                cloud_repository.session_from_access(access_token)
            ),
        )
        with runtime_connection(cloud_database, "cloud") as connection:
            connection.execute(
                "UPDATE viewer_projections SET lease_expires_at=? "
                "WHERE viewer_membership_id='membership_admin' "
                "AND invalidated_at IS NULL",
                ("2026-08-01T00:00:00.000Z",),
            )
            connection.commit()
        cloud_refresh, operations["cloud_refresh"] = _capture(
            directory,
            "cloud_refresh",
            "cloud",
            lambda: cloud_repository.refresh(
                refresh_token,
                idempotency_key="trace-cloud-refresh",
            ),
        )
        refreshed_identity = cloud_repository.session_from_access(
            str(cloud_refresh["accessToken"])
        )
        _, operations["cloud_logout"] = _capture(
            directory,
            "cloud_logout",
            "cloud",
            lambda: cloud_repository.logout(
                refreshed_identity,
                idempotency_key="trace-cloud-logout",
            ),
        )

    violations: list[dict[str, Any]] = []
    for name, operation in operations.items():
        control_id, role = mappings[name]
        expected = _expected(registry, control_id, role)
        operation["controlId"] = control_id
        operation["unexpectedReads"] = sorted(
            set(operation["reads"]) - expected["reads"]
        )
        operation["unexpectedWrites"] = sorted(
            set(operation["writes"]) - expected["writes"]
        )
        if operation["ddl"] or operation["unexpectedReads"] or operation["unexpectedWrites"]:
            violations.append(
                {
                    "operation": name,
                    "ddl": operation["ddl"],
                    "unexpectedReads": operation["unexpectedReads"],
                    "unexpectedWrites": operation["unexpectedWrites"],
                }
            )
    evidence = {
        "generatedAt": utc_now(),
        "registryId": registry["registryId"],
        "definitionVersion": registry["definitionVersion"],
        "operations": operations,
        "violations": violations,
        "passed": not violations,
    }
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
