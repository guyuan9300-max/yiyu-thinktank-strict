from __future__ import annotations

import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strict_common.contracts import CLOUD_CONTRACT, LOCAL_CONTRACT
from strict_common.schema import initialize_database


RUNTIME_DIRS = [
    ROOT / "backend",
    ROOT / "cloud_backend",
    ROOT / "strict_common",
    ROOT / "src",
]

REPOSITORY_MARKER = ROOT / ".yiyu-strict-repository.json"
EXPECTED_REPOSITORY_IDENTITY = {
    "formatVersion": 1,
    "kind": "yiyu-strict-repository",
    "genesisLabel": "blueprint-88-foundation-v8-agent-skill-contract",
    "githubRepository": "guyuan9300-max/yiyu-thinktank-strict",
    "githubRepositoryNumericId": 1316010273,
    "githubRepositoryNodeId": "R_kgDOTnC5IQ",
    "remoteUrl": "https://github.com/guyuan9300-max/yiyu-thinktank-strict.git",
    "targetBranch": "main",
    "localManifestHash": LOCAL_CONTRACT.manifest_hash,
    "cloudManifestHash": CLOUD_CONTRACT.manifest_hash,
}


class AuditFailure(RuntimeError):
    pass


def runtime_files() -> list[Path]:
    files: list[Path] = []
    for directory in RUNTIME_DIRS:
        files.extend(
            path
            for path in directory.rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".sql"}
            and "__pycache__" not in path.parts
        )
    return files


def fail(message: str) -> None:
    raise AuditFailure(message)


def audit_forbidden_runtime_markers() -> None:
    forbidden = {
        "/api/v1": "旧云 API",
        "YiyuThinkTankWorkbench2": "旧数据目录",
        '"app.db"': "旧数据库文件名",
        "'app.db'": "旧数据库文件名",
        "active_business_sandbox_id": "旧活动沙箱旁路",
        "organization_cloud_proxy": "已废弃 AI 代理",
        "import keyring": "会触发系统钥匙串授权的 Python keyring",
        "from keyring": "会触发系统钥匙串授权的 Python keyring",
        "latest.yml": "旧静态更新源",
        "ATTACH DATABASE": "旧库混读",
        "CREATE TABLE IF NOT EXISTS": "运行时补表",
        "event_line_access": "旧事件线权限旁路",
    }
    for path in runtime_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker, meaning in forbidden.items():
            if marker in text:
                fail(f"{meaning}出现在运行时代码：{path.relative_to(ROOT)}")
        for pattern, meaning in (
            (
                re.compile(
                    r"\b(?:legacy|old|fallback)_(?:db|database)(?:_path)?\b",
                    re.IGNORECASE,
                ),
                "旧数据库 fallback",
            ),
            (
                re.compile(r"\bdual_(?:read|write)\b", re.IGNORECASE),
                "双读写旁路",
            ),
        ):
            if pattern.search(text):
                fail(f"{meaning}出现在运行时代码：{path.relative_to(ROOT)}")


def audit_repository_identity() -> None:
    if not REPOSITORY_MARKER.is_file():
        fail("缺少严格仓库身份 marker")
    actual = json.loads(REPOSITORY_MARKER.read_text(encoding="utf-8"))
    if actual != EXPECTED_REPOSITORY_IDENTITY:
        fail(f"严格仓库身份 marker 已漂移：{actual}")


def audit_sql_boundaries() -> None:
    allowed_python = {
        ROOT / "strict_common" / "schema.py",
        ROOT / "strict_common" / "offline_upgrade.py",
        ROOT / "strict_common" / "physical_schema.py",
        ROOT / "strict_common" / "project_scope.py",
        ROOT / "backend" / "app" / "runtime.py",
        ROOT / "backend" / "app" / "platform_integrations_local.py",
        ROOT / "backend" / "app" / "project_materials_local.py",
        ROOT / "backend" / "app" / "gc04_tasks_local.py",
        ROOT / "backend" / "app" / "gc06_planning_local.py",
        ROOT / "backend" / "app" / "gc08_meetings.py",
        ROOT / "backend" / "app" / "workbench_chat_local.py",
        ROOT / "cloud_backend" / "app" / "repository.py",
    }
    allowed_repository_directory = ROOT / "cloud_backend" / "app" / "repositories"
    sql_pattern = re.compile(
        r"""
        \b(?:
            SELECT\s+.{1,500}?\s+FROM\s+["`\[]?[A-Za-z_]
          | INSERT\s+INTO\s+["`\[]?[A-Za-z_]
          | UPDATE\s+["`\[]?[A-Za-z_]\w*["`\]]?\s+SET\s+
          | DELETE\s+FROM\s+["`\[]?[A-Za-z_]
          | CREATE\s+TABLE\s+
          | ALTER\s+TABLE\s+
        )
        """,
        re.IGNORECASE | re.DOTALL | re.VERBOSE,
    )
    for path in runtime_files():
        if (
            path.suffix != ".py"
            or path in allowed_python
            or allowed_repository_directory in path.parents
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if sql_pattern.search(text):
            fail(f"SQL 越过 repository/schema 边界：{path.relative_to(ROOT)}")

    ddl_pattern = re.compile(
        r"\b(?:CREATE|ALTER|DROP)\s+(?:TABLE|INDEX|TRIGGER|VIEW)\b",
        re.IGNORECASE,
    )
    for path in runtime_files():
        if path.suffix == ".sql" or path in {
            ROOT / "strict_common" / "schema.py",
            ROOT / "strict_common" / "physical_schema.py",
        }:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ddl_pattern.search(text):
            fail(f"运行时代码包含 DDL：{path.relative_to(ROOT)}")


def audit_schema_exactness() -> None:
    with tempfile.TemporaryDirectory(prefix="strict-audit-") as temp:
        base = Path(temp)
        for role, contract in (
            ("local", LOCAL_CONTRACT),
            ("cloud", CLOUD_CONTRACT),
        ):
            database = base / f"{role}.db"
            identity = initialize_database(database, role)
            if identity.manifest_hash != contract.manifest_hash:
                fail(f"{role} manifest identity mismatch")
            with sqlite3.connect(database) as connection:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                        """
                    )
                }
                if tables != set(contract.allowed_tables):
                    fail(f"{role} table set mismatch")
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    fail(f"{role} quick_check failed")


def audit_product_identity() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    expected = {
        "name": "yiyu-thinktank-strict",
        "appId": "com.yiyu.thinktank.strict",
        "productName": "益语智库AI（新版）",
        "shortcutName": "益语智库AI（新版）",
    }
    actual = {
        "name": package.get("name"),
        "appId": package.get("build", {}).get("appId"),
        "productName": package.get("build", {}).get("productName"),
        "shortcutName": package.get("build", {}).get("nsis", {}).get("shortcutName"),
    }
    if actual != expected:
        fail(f"应用身份不匹配：{actual}")
    expected_files = [
        "build/main/**/*",
        "dist/renderer/**/*",
        "package.json",
        "!**/*.map",
    ]
    if package.get("build", {}).get("files") != expected_files:
        fail("安装包文件白名单已漂移")
    expected_resources = [
        {
            "from": "backend-dist",
            "to": "backend-dist",
            "filter": ["yiyu-strict-backend*"],
        }
    ]
    if package.get("build", {}).get("extraResources") != expected_resources:
        fail("冻结后端资源边界已漂移")
    if package.get("build", {}).get("asar") is not True:
        fail("主程序必须封装在 app.asar")
    if package.get("dependencies") != {}:
        fail("安装版不得携带未登记的 Node 运行时依赖")
    main_source = (ROOT / "src" / "main" / "main.ts").read_text(encoding="utf-8")
    for marker in (
        "YiyuThinkTankStrictV1",
        "com.yiyu.thinktank.strict.v1",
        "益语智库AI（新版）",
    ):
        if marker not in main_source:
            fail(f"Electron 缺少新身份：{marker}")


def audit_capability_registry() -> None:
    from strict_common.contracts import (
        BUSINESS_CAPABILITIES,
        CONNECTED_CAPABILITIES,
        capability_registry,
    )

    states = {
        item["id"]: item["state"]
        for item in capability_registry(cloud_connected=True)
    }
    for capability in BUSINESS_CAPABILITIES:
        expected = "connected" if capability in CONNECTED_CAPABILITIES else "not_connected"
        if states.get(capability) != expected:
            fail(
                f"业务 capability 状态错误：{capability} "
                f"expected={expected} actual={states.get(capability)}"
            )


def main() -> None:
    checks = [
        audit_repository_identity,
        audit_forbidden_runtime_markers,
        audit_sql_boundaries,
        audit_schema_exactness,
        audit_product_identity,
        audit_capability_registry,
    ]
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print("STRICT CANDIDATE AUDIT PASSED")


if __name__ == "__main__":
    main()
