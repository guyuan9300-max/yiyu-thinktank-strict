from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, Query

from strict_common.ids import new_id

from ..repositories import agent_skills
from ..repository import CloudRepository, SessionIdentity


def register_agent_skill_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]

    @app.get("/api/v2/agent-skills")
    def list_skills(
        identity: Identity,
        agent_kind: Annotated[str | None, Query(alias="agentKind")] = None,
        enabled_only: Annotated[bool, Query(alias="enabledOnly")] = True,
    ) -> dict[str, Any]:
        return agent_skills.list_agent_skills(
            repository, identity, agent_kind=agent_kind, enabled_only=enabled_only
        )

    @app.get("/api/v2/agent-skills/{skill_id}")
    def get_skill(skill_id: str, identity: Identity) -> dict[str, Any]:
        return agent_skills.get_agent_skill(repository, identity, skill_id=skill_id)

    @app.post("/api/v2/agent-skills")
    def publish_skill(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return agent_skills.publish_agent_skill(
            repository,
            identity,
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch("/api/v2/agent-skills/{skill_id}")
    def update_skill(
        skill_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return agent_skills.update_agent_skill(
            repository,
            identity,
            skill_id=skill_id,
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )

    @app.patch("/api/v2/agent-skills/{skill_id}/enabled")
    def set_skill_enabled(
        skill_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return agent_skills.set_agent_skill_enabled(
            repository,
            identity,
            skill_id=skill_id,
            enabled=bool((payload or {}).get("enabled")),
            expected_version=int((payload or {}).get("expectedVersion") or 0),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.delete("/api/v2/agent-skills/{skill_id}")
    def delete_skill(
        skill_id: str,
        identity: Identity,
        payload: Annotated[dict[str, Any] | None, Body()] = None,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return agent_skills.delete_agent_skill(
            repository,
            identity,
            skill_id=skill_id,
            expected_version=int((payload or {}).get("expectedVersion") or 0),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/agent-skills/{skill_id}/delete")
    def delete_skill_command(
        skill_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        """Command-style alias for clients/proxies that do not forward DELETE bodies."""
        return agent_skills.delete_agent_skill(
            repository,
            identity,
            skill_id=skill_id,
            expected_version=int((payload or {}).get("expectedVersion") or 0),
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/agent-skills/{skill_id}/runs")
    def record_skill_run(
        skill_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> dict[str, Any]:
        return agent_skills.record_agent_skill_run(
            repository,
            identity,
            skill_id=skill_id,
            payload=payload or {},
            idempotency_key=idempotency_key or new_id(),
        )
