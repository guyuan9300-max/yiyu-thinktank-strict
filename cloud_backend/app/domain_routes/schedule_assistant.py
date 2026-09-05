from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI

from ..repository import CloudRepository, SessionIdentity
from ..repositories.schedule_assistant import ScheduleAssistantRepository


def register_schedule_assistant_routes(
    app: FastAPI, repository: CloudRepository, identity_dependency: Any,
) -> None:
    domain = ScheduleAssistantRepository(repository)
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]

    @app.post("/api/v2/ui/tasks/schedule-assistant/ask")
    def ask_schedule_assistant(
        payload: Annotated[dict[str, Any], Body()], identity: Identity,
    ) -> dict[str, Any]:
        return domain.ask(identity, payload=payload)
