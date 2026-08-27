from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from scripts.strict_cloud_release import (
    ReleaseVerificationError,
    _tracked_runtime_files,
    detect_task_capabilities,
    inspect_runtime_import_closure,
    verify_release_directory,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_cloud_runtime_import_closure_is_complete_in_git() -> None:
    missing = inspect_runtime_import_closure(REPOSITORY_ROOT)
    assert missing == []


def test_task_release_capabilities_are_cumulative() -> None:
    assert detect_task_capabilities(REPOSITORY_ROOT) == {
        "taskViewerProjectionV1": True,
        "taskTimerV1": True,
        "dateOnlyScheduleV1": True,
    }


def test_cloud_release_inventory_includes_contract_hash_companions() -> None:
    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()
    files = _tracked_runtime_files(REPOSITORY_ROOT, git_sha)
    assert "contracts/strict-cloud-schema-manifest.v1.canonical.sha256" in files
    assert "contracts/strict-local-schema-manifest.v1.canonical.sha256" in files


def test_release_verifier_rejects_a_capability_regression(tmp_path: Path) -> None:
    runtime_file = tmp_path / "cloud_backend" / "app" / "main.py"
    runtime_file.parent.mkdir(parents=True)
    runtime_file.write_text("APP = 'strict'\n", encoding="utf-8")
    digest = hashlib.sha256(runtime_file.read_bytes()).hexdigest()
    manifest = {
        "schema": "yiyu.strict-cloud-release.v1",
        "releaseId": "strict-deadbee",
        "gitSha": "d" * 40,
        "gitTree": "e" * 40,
        "createdAt": "2026-08-27T00:00:00Z",
        "files": {"cloud_backend/app/main.py": digest},
        "capabilities": {
            "taskViewerProjectionV1": True,
            "taskTimerV1": False,
            "dateOnlyScheduleV1": True,
        },
    }
    (tmp_path / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    with pytest.raises(ReleaseVerificationError, match="taskTimerV1"):
        verify_release_directory(tmp_path, expected_sha="d" * 40)
