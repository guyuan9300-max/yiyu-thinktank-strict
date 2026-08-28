from __future__ import annotations

import json
from pathlib import Path


manifest = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "legacy_contract_quarantine.json"
)
document = json.loads(manifest.read_text(encoding="utf-8"))
categories = document["categories"]
total = sum(len(entry["tests"]) for entry in categories.values())

print("## Python 历史测试隔离")
print()
print(f"当前共有 **{total}** 个精确 strict-xfail 用例；任何意外通过都会使门禁失败。")
print()
print("| 类别 | 数量 | 处理原则 |")
print("| --- | ---: | --- |")
for category, entry in categories.items():
    rationale = str(entry["rationale"]).replace("|", "\\|")
    print(f"| `{category}` | {len(entry['tests'])} | {rationale} |")
