from __future__ import annotations

import json
import re
import subprocess
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

from backend.app.runtime import LocalRuntimeError
from backend.app.ui_domains import NOT_HANDLED, build_default_registry
from backend.app.ui_domains.routing import UiRequest


ROOT = Path(__file__).resolve().parents[1]


def _renderer_operations() -> list[dict[str, object]]:
    result = subprocess.run(
        ["node", "scripts/ui_route_inventory.mjs", "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    operations = json.loads(result.stdout)
    assert isinstance(operations, list)
    return operations


def test_every_renderer_operation_invokes_its_owned_domain_entry(
    tmp_path: Path,
) -> None:
    """Exercise the frozen 559-entry denominator through real dispatch.

    This is deliberately entry-path evidence, not a substitute for the domain
    behavior, authority, CAS, idempotency, isolation, and restart tests.
    """

    routers = {
        router.domain: router
        for router in build_default_registry().routers
    }
    operations = _renderer_operations()
    assert len(operations) == 559

    for index, operation in enumerate(operations):
        domain = str(operation["domain"])
        method = str(operation["method"])
        path = re.sub(r":[^/]+", f"matrix-{index}", str(operation["path"]))
        compatibility = MagicMock()
        compatibility.runtime.pinned_workspace_context = lambda: nullcontext()
        compatibility.runtime.database_path = tmp_path / "strict-local.db"
        compatibility._snapshot.return_value = {
            "projects": [],
            "tasks": [],
            "eventLines": [],
            "documents": [],
            "plans": [],
            "reviews": [],
            "intelligence": [],
            "growthSignals": [],
            "growthEvidence": [],
            "aiAnswers": [],
        }
        compatibility._current.return_value = {
            "runtimeStatus": "ready",
            "sessionSnapshot": {},
        }
        compatibility._session.return_value = {}
        compatibility.auth_state.return_value = {
            "authenticated": True,
            "user": {
                "id": "matrix-member",
                "primaryRole": "admin",
                "membershipStatus": "approved",
            },
        }
        compatibility._not_connected.side_effect = LocalRuntimeError(
            501,
            "capability_not_connected",
            "entry matrix reached an explicitly unconnected branch",
        )
        request = UiRequest(
            method=method,
            path=path,
            query={},
            body={},
            idempotency_key=f"entry-matrix-{index}",
        )
        try:
            result = routers[domain].dispatch(compatibility, request)
        except Exception:
            # Input validation, lookup failures, explicit blockers, and mocked
            # external dependencies are expected in an entry-only matrix.
            result = None
        assert not compatibility._not_connected.called, (
            domain,
            method,
            path,
        )
        if result is not None:
            assert result is not NOT_HANDLED, (domain, method, path)
