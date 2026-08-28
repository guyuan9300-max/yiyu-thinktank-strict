from __future__ import annotations

import json
from pathlib import Path


def test_legacy_quarantine_is_exact_strict_and_auditable() -> None:
    path = Path(__file__).with_name("legacy_contract_quarantine.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["policy"] == {
        "strictXfail": True,
        "exactNodeIdsOnly": True,
        "unexpectedPassFailsGate": True,
        "productionFallbackForbidden": True,
    }
    node_ids: list[str] = []
    for category, entry in document["categories"].items():
        assert category
        assert str(entry["rationale"]).strip()
        assert entry["tests"] == sorted(entry["tests"])
        for node_id in entry["tests"]:
            assert node_id.startswith("tests/")
            assert ".py::test_" in node_id
            node_ids.append(node_id)
    assert node_ids
    assert len(node_ids) == len(set(node_ids))
