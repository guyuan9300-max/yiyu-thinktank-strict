from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.ui_domains.routing import UiDomainRouter  # noqa: E402


def _renderer_operations() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["node", "scripts/ui_route_inventory.mjs", "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    operations = json.loads(result.stdout)
    if not isinstance(operations, list):
        raise RuntimeError("UI route inventory did not return a list")
    return operations


def _template_regex(template: str) -> re.Pattern[str]:
    escaped = re.escape(template)
    parameterized = re.sub(r":[^/\\]+", r"[^/]+", escaped)
    return re.compile(f"^{parameterized}$")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report which frozen renderer operations are actually invoked "
            "through UiDomainRouter while pytest runs. This proves entry-path "
            "execution only; domain assertions still provide behavior evidence."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/tmp/yiyu-ui-route-runtime-coverage.json"),
    )
    parser.add_argument("--require-all", action="store_true")
    parser.add_argument(
        "pytest_args",
        nargs="*",
        default=["-q"],
    )
    args = parser.parse_args()

    operations = _renderer_operations()
    compiled = [_template_regex(str(item["path"])) for item in operations]
    seen: set[int] = set()
    calls: defaultdict[int, int] = defaultdict(int)
    original_dispatch = UiDomainRouter.dispatch

    def tracked_dispatch(
        router: UiDomainRouter,
        compatibility: Any,
        request: Any,
    ) -> Any:
        for index, operation in enumerate(operations):
            if (
                operation["domain"] == router.domain
                and operation["method"] == request.method
                and compiled[index].fullmatch(request.path)
            ):
                seen.add(index)
                calls[index] += 1
        return original_dispatch(router, compatibility, request)

    UiDomainRouter.dispatch = tracked_dispatch
    try:
        pytest_exit_code = int(pytest.main(args.pytest_args or ["-q"]))
    finally:
        UiDomainRouter.dispatch = original_dispatch

    domain_ids = sorted({str(item["domain"]) for item in operations})
    summary: dict[str, dict[str, int]] = {}
    for domain in domain_ids:
        indexes = [
            index
            for index, operation in enumerate(operations)
            if operation["domain"] == domain
        ]
        tested = sum(index in seen for index in indexes)
        summary[domain] = {
            "tested": tested,
            "total": len(indexes),
            "missing": len(indexes) - tested,
        }

    rows = []
    for index, operation in enumerate(operations):
        rows.append(
            {
                **operation,
                "entryPathInvoked": index in seen,
                "invocationCount": calls[index],
            }
        )
    report = {
        "evidenceBoundary": (
            "Entry-path invocation only. A passing row does not replace "
            "authority, permission, CAS, idempotency, isolation, or restart tests."
        ),
        "pytestExitCode": pytest_exit_code,
        "tested": len(seen),
        "total": len(operations),
        "summary": summary,
        "operations": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "pytestExitCode": pytest_exit_code,
                "tested": len(seen),
                "total": len(operations),
                "summary": summary,
                "report": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    if pytest_exit_code != 0:
        return pytest_exit_code
    if args.require_all and len(seen) != len(operations):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
