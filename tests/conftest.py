from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


_QUARANTINE_PATH = Path(__file__).with_name("legacy_contract_quarantine.json")


def _quarantined_tests() -> dict[str, str]:
    document: dict[str, Any] = json.loads(_QUARANTINE_PATH.read_text(encoding="utf-8"))
    quarantined: dict[str, str] = {}
    for category, entry in document["categories"].items():
        rationale = str(entry["rationale"])
        for node_id in entry["tests"]:
            if node_id in quarantined:
                raise pytest.UsageError(f"duplicate quarantine node id: {node_id}")
            quarantined[str(node_id)] = f"{category}: {rationale}"
    return quarantined


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    quarantined = _quarantined_tests()
    collected = {item.nodeid for item in items}

    # A normal full-suite run must collect every exact quarantined identity.
    # This makes deleted or renamed tests fail the gate until the manifest is
    # deliberately reviewed. Targeted developer runs remain possible.
    if not config.option.file_or_dir and not config.option.keyword:
        stale = sorted(set(quarantined) - collected)
        if stale:
            raise pytest.UsageError(
                "stale legacy quarantine entries:\n" + "\n".join(stale)
            )

    for item in items:
        reason = quarantined.get(item.nodeid)
        if reason is not None:
            item.add_marker(pytest.mark.xfail(reason=reason, strict=True))
