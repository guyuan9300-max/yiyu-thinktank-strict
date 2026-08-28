from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.ui_domains import build_default_registry


def renderer_operations() -> list[dict[str, Any]]:
    result = subprocess.run(
        ["node", "scripts/ui_route_inventory.mjs", "--json"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, list):
        raise RuntimeError("UI route inventory did not return a list")
    return payload


def sample_path(template: str) -> str:
    return re.sub(r":[^/]+", "sample", template)


def _route_sample_path(pattern: str) -> str | None:
    """Build one valid concrete path from the route regex subset we register."""

    value = re.sub(
        r"\(\?P<[^>]+>([^()]*)\)",
        lambda match: (
            "sample"
            if match.group(1) == "[^/]+"
            else match.group(1).split("|", 1)[0]
        ),
        pattern,
    )
    value = value.replace("([^/]+)", "sample")
    value = re.sub(
        r"\(([A-Za-z0-9_/-]+(?:\|[A-Za-z0-9_/-]+)+)\)",
        lambda match: match.group(1).split("|", 1)[0],
        value,
    )
    return None if any(token in value for token in ("(", ")", "[", "]")) else value


def _template_accepts_path(template: str, path: str) -> bool:
    segments = [
        r"[^/]+" if segment.startswith(":") else re.escape(segment)
        for segment in template.split("/")
    ]
    return re.fullmatch("/".join(segments), path) is not None


def matching_registered_routes(
    route_specs: Iterable[Any],
    *,
    method: str,
    template: str,
) -> list[Any]:
    """Return routes whose regex language overlaps a renderer path template."""

    direct_path = sample_path(template)
    matches: list[Any] = []
    for route in route_specs:
        if route.method != method:
            continue
        if route.regex.fullmatch(direct_path):
            matches.append(route)
            continue
        route_sample = _route_sample_path(route.pattern)
        if (
            route_sample is not None
            and route.regex.fullmatch(route_sample)
            and _template_accepts_path(template, route_sample)
        ):
            matches.append(route)
    return matches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--domain")
    args = parser.parse_args()

    registry = build_default_registry()
    # Renderer domains describe product ownership while the strict registry
    # deliberately decomposes a product domain into narrow golden-chain route
    # owners.  A route is connected when its method/path resolves to a real
    # handler anywhere in the assembled registry; requiring the two domain
    # labels to be identical made every decomposed handler look missing.
    route_specs = registry.routes
    operations = renderer_operations()
    if args.domain:
        operations = [
            operation
            for operation in operations
            if operation.get("domain") == args.domain
        ]

    missing: list[dict[str, Any]] = []
    fallback_only: list[dict[str, Any]] = []
    registered = 0
    for operation in operations:
        method = str(operation["method"])
        matching_routes = matching_registered_routes(
            route_specs,
            method=method,
            template=str(operation["path"]),
        )
        if any(
            route.handler.__name__ != "_gap"
            for route in matching_routes
        ):
            registered += 1
        elif matching_routes:
            fallback_only.append(operation)
            missing.append(operation)
        else:
            missing.append(operation)

    print(
        f"UI domain handlers registered={registered} missing={len(missing)} "
        f"total={len(operations)}"
    )
    print(
        "NOTE registration proves route ownership only; "
        "runtime behavior requires domain integration evidence"
    )
    if fallback_only:
        print(
            "FALLBACK_ONLY "
            f"{len(fallback_only)} operation(s) resolve only to an explicit gap handler"
        )
    by_domain: dict[str, int] = {}
    for operation in missing:
        domain = str(operation["domain"])
        by_domain[domain] = by_domain.get(domain, 0) + 1
    for domain, count in sorted(by_domain.items()):
        print(f"MISSING {domain}: {count}")
    if missing and not args.allow_missing:
        for operation in missing[:100]:
            print(
                f"  {operation['method']} {operation['path']} "
                f"({operation['function']}:{operation['line']})"
            )
        if len(missing) > 100:
            print(f"  ... and {len(missing) - 100} more")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
