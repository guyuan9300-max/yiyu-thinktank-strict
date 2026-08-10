#!/usr/bin/env python3
"""Promote the reviewed 88-table physical manifests from v6 to v7."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = PROJECT_ROOT / "contracts"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fk_field(name: str, target_table: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": "TEXT",
        "nullable": True,
        "default": None,
        "primary_key": False,
        "reference": {
            "kind": "foreign_key",
            "target_table": target_table,
            "target_field": "id",
            "on_delete": "RESTRICT",
            "source": "Agent Memory v7 产品裁决",
        },
    }


def _insert_after(table: dict[str, Any], after: str, fields: list[dict[str, Any]]) -> None:
    existing = {str(item["name"]) for item in table["fields"]}
    additions = [field for field in fields if field["name"] not in existing]
    if not additions:
        return
    index = next(i for i, item in enumerate(table["fields"]) if item["name"] == after) + 1
    table["fields"][index:index] = additions


def _replace_check(table: dict[str, Any], name: str, expression: str) -> None:
    checks = table.setdefault("check_constraints", [])
    for check in checks:
        if check.get("name") == name:
            check["expression"] = expression
            return
    checks.append({"name": name, "expression": expression})


def _append_unique(table: dict[str, Any], unique: dict[str, Any]) -> None:
    rows = table.setdefault("unique_constraints", [])
    if not any(row.get("name") == unique["name"] for row in rows):
        rows.append(unique)


def _append_invariant(table: dict[str, Any], text: str) -> None:
    rows = table.setdefault("command_invariants", [])
    if text not in rows:
        rows.append(text)


def promote(raw: dict[str, Any]) -> dict[str, Any]:
    version = str(raw.get("contractVersion"))
    if version not in {"6", "7"}:
        raise RuntimeError(f"expected v6 or idempotent v7 manifest, got {version}")
    tables = {str(table["name"]): table for table in raw["allowedTables"]}
    if len(tables) != 88:
        raise RuntimeError("Agent Memory foundation must preserve exactly 88 tables")

    _insert_after(tables["source_sets"], "scope_id", [_fk_field("client_id", "clients")])
    _append_invariant(
        tables["source_sets"],
        "项目资料、问答或知识加工使用的 source_set 必须写入 client_id；非项目通用集合才允许为空。",
    )

    _insert_after(
        tables["ai_answers"],
        "scope_id",
        [_fk_field("client_id", "clients"), _fk_field("bot_id", "bot_definitions")],
    )
    _append_invariant(
        tables["ai_answers"],
        "项目问答必须同时写入 client_id 与实际执行的 bot_id；operation receipt 不得以线程或当前界面猜归属。",
    )

    agent_kind = {
        "name": "agent_kind",
        "type": "TEXT",
        "nullable": True,
        "default": None,
        "primary_key": False,
        "reference": None,
    }
    _insert_after(tables["bot_definitions"], "scope_id", [agent_kind])
    kinds = (
        "'project_workspace','task_planning','meeting_minutes',"
        "'strategy_companion','intelligence_research','growth_companion'"
    )
    _replace_check(
        tables["bot_definitions"],
        "ck_agent_kind_domain",
        f"agent_kind IS NULL OR agent_kind IN ({kinds})",
    )
    _replace_check(
        tables["bot_definitions"],
        "ck_owner_xor",
        "(agent_kind IS NOT NULL AND owner_principal_id IS NULL AND owner_membership_id IS NULL) "
        "OR (agent_kind IS NULL AND ((owner_principal_id IS NOT NULL) <> (owner_membership_id IS NOT NULL)))",
    )
    _append_unique(
        tables["bot_definitions"],
        {
            "name": "uq_bot_definitions_builtin_agent_kind",
            "fields": ["scope_id", "agent_kind"],
            "where": "agent_kind IS NOT NULL",
        },
    )
    _append_invariant(
        tables["bot_definitions"],
        "内置功能 Agent 使用组织 scope 且 owner 两列为空；普通机器人同事 agent_kind 为空并继续满足 owner XOR。",
    )

    raw["contractVersion"] = "7"
    raw["contractDate"] = "2026-08-05"
    raw.setdefault("commonRules", {})["agentMemoryFoundation"] = (
        "六个内置功能 Agent 以 bot_definitions 组织作用域权威行登记；项目问答显式绑定 client_id 与 bot_id。"
    )
    return raw


def main() -> None:
    for side in ("local", "cloud"):
        manifest_path = CONTRACTS_DIR / f"strict-{side}-schema-manifest.v1.json"
        raw = promote(json.loads(manifest_path.read_text(encoding="utf-8")))
        manifest_path.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(_canonical(raw).encode("utf-8")).hexdigest()
        hash_path = CONTRACTS_DIR / f"strict-{side}-schema-manifest.v1.canonical.sha256"
        hash_path.write_text(digest + "\n", encoding="utf-8")
        print(f"{side}: version=7 tables={len(raw['allowedTables'])} manifest={digest}")


if __name__ == "__main__":
    main()
