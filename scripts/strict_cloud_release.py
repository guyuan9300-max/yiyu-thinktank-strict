#!/usr/bin/env python3
"""Build and verify immutable strict-cloud releases from an exact Git commit."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA = "yiyu.strict-cloud-release.v1"
REQUIRED_CAPABILITIES = (
    "taskViewerProjectionV1",
    "taskTimerV1",
    "dateOnlyScheduleV1",
)
RUNTIME_ROOTS = ("cloud_backend", "strict_common")
EXPLICIT_RUNTIME_FILES = (
    "pyproject.toml",
    "uv.lock",
    "scripts/strict_backend_entry.py",
    "scripts/migrate_mobile_recording_v10.py",
    "scripts/strict_cloud_release.py",
)
REQUIRED_CONTRACT_FILES = (
    "contracts/strict-cloud-schema-manifest.v1.json",
    "contracts/strict-cloud-schema-manifest.v1.canonical.sha256",
    "contracts/strict-local-schema-manifest.v1.json",
    "contracts/strict-local-schema-manifest.v1.canonical.sha256",
)


class ReleaseVerificationError(RuntimeError):
    """Raised when a candidate release is incomplete or unverifiable."""


def _run(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source(root: Path, relative_path: str) -> str:
    path = root / relative_path
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def detect_task_capabilities(root: Path) -> dict[str, bool]:
    repository = _source(root, "cloud_backend/app/repositories/gc04_tasks.py")
    routes = _source(root, "cloud_backend/app/domain_routes/gc04_tasks.py")
    return {
        "taskViewerProjectionV1": all(
            marker in repository
            for marker in (
                "yiyu.task-viewer-projection.v1",
                '"viewerProjectionContract"',
                '"viewer_surfaces"',
                '"viewer_capabilities"',
            )
        ),
        "taskTimerV1": (
            "def update_task_timer(" in repository
            and '/timer/{action}' in routes
            and "TASK_FOCUS_RUN_KIND" in repository
        ),
        "dateOnlyScheduleV1": all(
            marker in repository
            for marker in (
                'payload.get("dueDate")',
                'payload.get("scheduledStartAt")',
                '"scheduled_start_at": scheduled_start',
            )
        ),
    }


def _module_candidates(root: Path, module: str) -> tuple[Path, Path]:
    base = root.joinpath(*module.split("."))
    return base.with_suffix(".py"), base / "__init__.py"


def _local_module_exists(root: Path, module: str) -> bool:
    return any(candidate.is_file() for candidate in _module_candidates(root, module))


def inspect_runtime_import_closure(root: Path) -> list[str]:
    """Return local cloud imports whose Python source is absent from the release."""
    missing: set[str] = set()
    sources = [
        path
        for runtime_root in RUNTIME_ROOTS
        for path in (root / runtime_root).rglob("*.py")
        if path.is_file()
    ]
    for path in sources:
        relative = path.relative_to(root)
        module_parts = list(relative.with_suffix("").parts)
        if module_parts[-1] == "__init__":
            module_parts.pop()
        package_parts = module_parts[:-1] if path.name != "__init__.py" else module_parts
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        except SyntaxError as error:
            missing.add(f"{relative}:syntax:{error.lineno}")
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    keep = max(0, len(package_parts) - node.level + 1)
                    prefix = package_parts[:keep]
                    base = prefix + (node.module.split(".") if node.module else [])
                    if node.module:
                        targets.append(".".join(base))
                    targets.extend(
                        ".".join(base + [alias.name])
                        for alias in node.names
                        if alias.name != "*"
                    )
                elif node.module:
                    targets.append(node.module)
                    targets.extend(
                        f"{node.module}.{alias.name}"
                        for alias in node.names
                        if alias.name != "*"
                    )
            for target in targets:
                if not target.startswith(("cloud_backend", "strict_common")):
                    continue
                if _local_module_exists(root, target):
                    continue
                parent = target.rsplit(".", 1)[0] if "." in target else ""
                if parent and _local_module_exists(root, parent):
                    continue
                missing.add(f"{relative}:{target}")
    return sorted(missing)


def _load_manifest(release_dir: Path) -> dict[str, Any]:
    manifest_path = release_dir / "RELEASE_MANIFEST.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseVerificationError(f"release manifest unreadable: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ReleaseVerificationError("release manifest schema mismatch")
    return manifest


def verify_release_directory(
    release_dir: Path, *, expected_sha: str | None = None
) -> dict[str, Any]:
    release_dir = release_dir.resolve()
    manifest = _load_manifest(release_dir)
    git_sha = str(manifest.get("gitSha") or "")
    if len(git_sha) != 40 or any(char not in "0123456789abcdef" for char in git_sha):
        raise ReleaseVerificationError("release manifest gitSha is invalid")
    if expected_sha and git_sha != expected_sha:
        raise ReleaseVerificationError(
            f"release gitSha mismatch: expected {expected_sha}, got {git_sha}"
        )
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, dict):
        raise ReleaseVerificationError("release capabilities are missing")
    for capability in REQUIRED_CAPABILITIES:
        if capabilities.get(capability) is not True:
            raise ReleaseVerificationError(f"required capability missing: {capability}")
    detected = detect_task_capabilities(release_dir)
    for capability in REQUIRED_CAPABILITIES:
        if detected.get(capability) is not True:
            raise ReleaseVerificationError(
                f"release source does not prove capability: {capability}"
            )
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ReleaseVerificationError("release file inventory is empty")
    for relative in REQUIRED_CONTRACT_FILES:
        if relative not in files:
            raise ReleaseVerificationError(f"required contract support missing: {relative}")
    for relative, expected_digest in files.items():
        path = release_dir / str(relative)
        if not path.is_file():
            raise ReleaseVerificationError(f"release file missing: {relative}")
        actual_digest = _sha256(path)
        if actual_digest != expected_digest:
            raise ReleaseVerificationError(f"release file hash mismatch: {relative}")
    missing_imports = inspect_runtime_import_closure(release_dir)
    if missing_imports:
        raise ReleaseVerificationError(
            "release import closure incomplete: " + ", ".join(missing_imports[:10])
        )
    return manifest


def _tracked_runtime_files(repo_root: Path, git_sha: str) -> list[str]:
    names = _run(repo_root, "ls-tree", "-r", "--name-only", git_sha).splitlines()
    selected: list[str] = []
    for name in names:
        if any(name.startswith(f"{root}/") for root in RUNTIME_ROOTS):
            if name.endswith(".py"):
                selected.append(name)
        elif name.startswith("contracts/") and name.endswith((".json", ".sha256")):
            selected.append(name)
        elif name in EXPLICIT_RUNTIME_FILES:
            selected.append(name)
    return sorted(set(selected))


def _write_git_file(repo_root: Path, git_sha: str, relative: str, destination: Path) -> None:
    blob = subprocess.run(
        ["git", "show", f"{git_sha}:{relative}"],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(blob)


def build_release(
    repo_root: Path,
    output_dir: Path,
    *,
    git_ref: str = "HEAD",
    require_remote: str | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    repo_root = repo_root.resolve()
    git_sha = _run(repo_root, "rev-parse", f"{git_ref}^{{commit}}")
    if require_remote:
        remote_sha = _run(repo_root, "rev-parse", f"{require_remote}^{{commit}}")
        if git_sha != remote_sha:
            raise ReleaseVerificationError(
                f"release SHA {git_sha} is not {require_remote} ({remote_sha})"
            )
    git_tree = _run(repo_root, "rev-parse", f"{git_sha}^{{tree}}")
    commit_time = _run(repo_root, "show", "-s", "--format=%cI", git_sha)
    release_id = f"strict-{git_sha[:12]}"
    release_dir = output_dir.resolve() / release_id
    if release_dir.exists():
        raise ReleaseVerificationError(f"release directory already exists: {release_dir}")
    release_dir.mkdir(parents=True)
    files = _tracked_runtime_files(repo_root, git_sha)
    if not files:
        raise ReleaseVerificationError("Git commit contains no cloud runtime files")
    for relative in files:
        _write_git_file(repo_root, git_sha, relative, release_dir / relative)
    capabilities = detect_task_capabilities(release_dir)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "releaseId": release_id,
        "gitSha": git_sha,
        "gitTree": git_tree,
        "sourceRemote": _run(repo_root, "remote", "get-url", "origin"),
        "createdAt": commit_time,
        "files": {relative: _sha256(release_dir / relative) for relative in files},
        "capabilities": capabilities,
    }
    (release_dir / "RELEASE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    verify_release_directory(release_dir, expected_sha=git_sha)
    archive = output_dir.resolve() / f"{release_id}.tar.gz"
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
        for path in sorted(release_dir.rglob("*")):
            if path.is_file():
                bundle.add(path, arcname=str(path.relative_to(release_dir)))
    return release_dir, archive, manifest


def verify_archive(path: Path, *, expected_sha: str | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="strict-cloud-verify-") as temp:
        root = Path(temp)
        with tarfile.open(path, "r:gz") as bundle:
            for member in bundle.getmembers():
                target = (root / member.name).resolve()
                if root.resolve() not in target.parents and target != root.resolve():
                    raise ReleaseVerificationError("archive contains an unsafe path")
            bundle.extractall(root, filter="data")
        return verify_release_directory(root, expected_sha=expected_sha)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--git-ref", default="HEAD")
    build.add_argument("--require-remote")
    verify = commands.add_parser("verify")
    verify.add_argument("path", type=Path)
    verify.add_argument("--expected-sha")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "build":
        release_dir, archive, manifest = build_release(
            args.repo_root,
            args.output_dir,
            git_ref=args.git_ref,
            require_remote=args.require_remote,
        )
        print(json.dumps({
            "releaseDir": str(release_dir),
            "archive": str(archive),
            "gitSha": manifest["gitSha"],
            "capabilities": manifest["capabilities"],
        }, ensure_ascii=False))
        return 0
    manifest = (
        verify_release_directory(args.path, expected_sha=args.expected_sha)
        if args.path.is_dir()
        else verify_archive(args.path, expected_sha=args.expected_sha)
    )
    print(json.dumps({"releaseId": manifest["releaseId"], "gitSha": manifest["gitSha"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
