#!/usr/bin/env python3
"""Promote the 88-table manifests from v7 to the Agent Skill v8 contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = PROJECT_ROOT / "contracts"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def promote(raw: dict[str, Any]) -> dict[str, Any]:
    version = str(raw.get("contractVersion"))
    if version not in {"7", "8"}:
        raise RuntimeError(f"expected v7 or idempotent v8 manifest, got {version}")
    tables = {str(table["name"]): table for table in raw["allowedTables"]}
    if len(tables) != 88:
        raise RuntimeError("Agent Skill contract must preserve exactly 88 tables")
    rules = tables["automation_rules"]
    check = next(
        row for row in rules.get("check_constraints", [])
        if row.get("name") == "ck_record_kind_domain"
    )
    check["expression"] = (
        "record_kind IN ('template','automation','task_control',"
        "'process_template','source_trust_rule','agent_skill')"
    )
    invariants = rules.setdefault("command_invariants", [])
    invariant = (
        "agent_skill 只能保存声明式规则、模板和已登记工具引用；"
        "不得包含可执行脚本，也不得覆盖 bot_definitions 中的内置 Agent 岗位合同。"
    )
    if invariant not in invariants:
        invariants.append(invariant)
    raw["contractVersion"] = "8"
    raw["contractDate"] = "2026-08-06"
    raw.setdefault("commonRules", {})["agentSkillFoundation"] = (
        "Skill 以 automation_rules(record_kind=agent_skill) 为唯一权威；"
        "内置 Agent 核心岗位版本继续由 bot_definitions 权威登记。"
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
        print(f"{side}: version=8 tables={len(raw['allowedTables'])} manifest={digest}")


if __name__ == "__main__":
    main()
