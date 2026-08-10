from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "contracts" / "agent-memory-builtins.v1.json"


@dataclass(frozen=True)
class BuiltinAgentDefinition:
    agent_kind: str
    handle: str
    label: str
    capability_policy_version: str
    service_goal: str
    direct_outputs: tuple[str, ...]
    command_boundaries: tuple[str, ...]
    base_mode: str

    @property
    def description(self) -> str:
        return self.service_goal


def _load_registry() -> tuple[dict[str, Any], tuple[BuiltinAgentDefinition, ...]]:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if raw.get("status") != "FROZEN_FOR_IMPLEMENTATION":
        raise RuntimeError("builtin Agent registry is not frozen")
    if raw.get("scopeKind") != "organization":
        raise RuntimeError("builtin Agents must use organization scope")
    rows = tuple(
        BuiltinAgentDefinition(
            agent_kind=str(row["agentKind"]),
            handle=str(row["handle"]),
            label=str(row["label"]),
            capability_policy_version=str(row["capabilityPolicyVersion"]),
            service_goal=str(row["serviceGoal"]),
            direct_outputs=tuple(str(value) for value in row["directOutputs"]),
            command_boundaries=tuple(str(value) for value in row["commandBoundaries"]),
            base_mode=str(row["baseMode"]),
        )
        for row in raw.get("agents", [])
    )
    if len(rows) != 6:
        raise RuntimeError("builtin Agent registry must contain exactly six Agents")
    if len({row.agent_kind for row in rows}) != len(rows):
        raise RuntimeError("builtin Agent registry contains duplicate agentKind")
    if len({row.handle for row in rows}) != len(rows):
        raise RuntimeError("builtin Agent registry contains duplicate handle")
    if any(
        not row.capability_policy_version
        or not row.service_goal
        or not row.direct_outputs
        or not row.command_boundaries
        or not row.base_mode
        for row in rows
    ):
        raise RuntimeError("builtin Agent registry contains incomplete role contracts")
    if len({row.capability_policy_version for row in rows}) != len(rows):
        raise RuntimeError("builtin Agent registry contains duplicate policy versions")
    return raw, rows


BUILTIN_AGENT_REGISTRY, BUILTIN_AGENT_DEFINITIONS = _load_registry()
BUILTIN_AGENT_KINDS = frozenset(row.agent_kind for row in BUILTIN_AGENT_DEFINITIONS)
AGENT_RUN_STATES = frozenset(
    {"queued", "running", "completed", "blocked", "failed_retryable", "failed"}
)


@dataclass(frozen=True)
class AgentRunReceipt:
    """Small public receipt shared by every built-in Agent entrypoint.

    The receipt intentionally contains no prompt, customer text or model output.
    Those stay in the domain result and the existing manifest objects.
    """

    agent_kind: str
    run_id: str
    state: str
    stage: str
    message: str
    retryable: bool = False
    result_version: int | None = None

    def as_dict(self) -> dict[str, Any]:
        if self.agent_kind not in BUILTIN_AGENT_KINDS:
            raise ValueError("agent_kind must identify a registered built-in Agent")
        if self.state not in AGENT_RUN_STATES:
            raise ValueError("state is not a supported Agent run state")
        if not self.run_id or not self.stage or not self.message:
            raise ValueError("Agent run receipt fields must not be empty")
        return {
            "agentKind": self.agent_kind,
            "runId": self.run_id,
            "state": self.state,
            "stage": self.stage,
            "message": self.message,
            "retryable": bool(self.retryable),
            "resultVersion": self.result_version,
        }


def builtin_agent_id(organization_id: str, agent_kind: str) -> str:
    if not organization_id or agent_kind not in BUILTIN_AGENT_KINDS:
        raise ValueError("organization_id and a registered agent_kind are required")
    digest = hashlib.sha256(f"{organization_id}|{agent_kind}".encode("utf-8")).hexdigest()
    return f"bot_builtin_{digest[:32]}"


def canonical_organization_scope_id(organization_id: str) -> str:
    if not organization_id:
        raise ValueError("organization_id is required")
    return f"scope_organization_{organization_id}"
