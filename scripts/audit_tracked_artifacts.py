from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_SOURCE_BYTES = 10 * 1024 * 1024
TEXT_SCAN_BYTES = 2 * 1024 * 1024

FORBIDDEN_PARTS = {
    ".runtime-secrets",
    "attachments",
    "backups",
    "recordings",
    "runtime-data",
    "uploads",
    "user-data",
}
FORBIDDEN_SUFFIXES = {
    ".backup",
    ".bak",
    ".blockmap",
    ".bundle",
    ".db",
    ".dmg",
    ".exe",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite3",
    ".tgz",
    ".zip",
}
SECRET_PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(?:ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}


def candidate_paths() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return [
        ROOT / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]


def is_forbidden_path(path: Path) -> str | None:
    relative = path.relative_to(ROOT)
    parts = set(relative.parts)
    lower_name = path.name.lower()
    if parts & FORBIDDEN_PARTS:
        return "运行数据或附件目录"
    if lower_name.startswith(".env") and lower_name != ".env.example":
        return "环境变量文件"
    if any(lower_name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return "数据库、凭据、安装包或备份产物"
    if lower_name.endswith(".tar.gz"):
        return "备份归档"
    return None


def main() -> None:
    failures: list[str] = []
    for path in candidate_paths():
        if not path.exists() or not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        forbidden_reason = is_forbidden_path(path)
        if forbidden_reason:
            failures.append(f"{relative}: {forbidden_reason}")
            continue
        size = path.stat().st_size
        if size > MAX_SOURCE_BYTES:
            failures.append(f"{relative}: 文件超过 10 MiB，需明确审查后另行处理")
            continue
        if size > TEXT_SCAN_BYTES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{relative}: 疑似包含 {label}")
                break
    if failures:
        print("STRICT ARTIFACT AUDIT FAILED")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print("STRICT ARTIFACT AUDIT PASSED")


if __name__ == "__main__":
    main()
