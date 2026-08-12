#!/usr/bin/env python3
"""Promote both 88-table manifests to the flat planning v9 contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def field(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": "TEXT",
        "nullable": True,
        "default": None,
        "primary_key": False,
        "reference": {
            "kind": "foreign_key",
            "target_table": "planning_cycles",
            "target_field": "id",
            "on_delete": "RESTRICT",
            "source": "2026-08-12 产品裁决：任务和会议直接关联独立计划",
        },
    }


def promote(raw: dict[str, Any]) -> dict[str, Any]:
    version = str(raw.get("contractVersion"))
    if version not in {"8", "9"}:
        raise RuntimeError(f"expected v8 or v9, got {version}")
    tables = {str(item["name"]): item for item in raw["allowedTables"]}
    if len(tables) != 88:
        raise RuntimeError("flat planning must preserve exactly 88 tables")
    for name in ("tasks", "meetings"):
        fields = tables[name]["fields"]
        if not any(item["name"] == "planning_cycle_id" for item in fields):
            anchor = next(i for i, item in enumerate(fields) if item["name"] == "event_line_id") + 1
            fields.insert(anchor, field("planning_cycle_id"))
    cycles = tables["planning_cycles"]
    cycles["fields"] = [item for item in cycles["fields"] if item["name"] != "parent_plan_id"]
    cycles["command_invariants"] = [
        "每条 planning_cycles 行即用户可直接选择的独立计划；组织与部门只决定归属和权限，不形成父子计划。",
        "同一计划允许被多条 tasks 或 meetings 关联；单个任务或会议至多关联一个计划。",
    ]
    actions = tables["decision_actions"]
    actions["fields"] = [item for item in actions["fields"] if item["name"] != "task_id"]
    actions["unique_constraints"] = [
        item for item in actions.get("unique_constraints", []) if item.get("name") != "uq_decision_actions_01"
    ]
    actions["check_constraints"] = [
        item for item in actions.get("check_constraints", [])
        if item.get("name") != "ck_primary_task_unique_role"
    ]
    actions["command_invariants"] = [
        "decision_actions 只承载复盘、会议、情报、战略或 AI 形成的待确认行动建议，不作为计划子层级。",
        "行动建议转任务或转计划必须调用各自正式命令；不得用 decision_actions 保存主要任务槽位。",
    ]
    raw["contractVersion"] = "9"
    raw["contractDate"] = "2026-08-12"
    raw.setdefault("commonRules", {})["flatPlanning"] = (
        "计划是 planning_cycles 中可直接选择的独立对象；tasks/meetings 通过 planning_cycle_id 多对一关联。"
        "decision_actions 不再充当计划步骤或主要任务槽位。"
    )
    return raw


def main() -> None:
    for side in ("local", "cloud"):
        path = CONTRACTS / f"strict-{side}-schema-manifest.v1.json"
        raw = promote(json.loads(path.read_text(encoding="utf-8")))
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        digest = hashlib.sha256(canonical(raw).encode()).hexdigest()
        (CONTRACTS / f"strict-{side}-schema-manifest.v1.canonical.sha256").write_text(
            digest + "\n", encoding="utf-8"
        )
        print(f"{side}: v9 tables={len(raw['allowedTables'])} manifest={digest}")


if __name__ == "__main__":
    main()
