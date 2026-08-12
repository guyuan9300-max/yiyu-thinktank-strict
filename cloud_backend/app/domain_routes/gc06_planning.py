from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, Query, status

from ..repositories import gc06_planning
from ..repositories.gc06_task_command_port import (
    FormalTaskCommandPort,
    UNAVAILABLE_FORMAL_TASK_COMMAND_PORT,
)
from ..repository import CloudRepository, SessionIdentity


def register_gc06_planning_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
    *,
    task_command_port: FormalTaskCommandPort = UNAVAILABLE_FORMAL_TASK_COMMAND_PORT,
) -> None:
    """Register GC-06 routes without touching the shared domain registrar.

    The integration thread owns calling this function and supplying GC-04's
    formal task-command adapter.
    """

    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]
    Idempotency = Annotated[str, Header(alias="Idempotency-Key")]

    @app.get("/api/v2/gc06/event-lines")
    def list_event_lines(
        identity: Identity,
        client_id: Annotated[str | None, Query(alias="clientId")] = None,
        include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
    ) -> list[dict[str, Any]]:
        return gc06_planning.list_event_lines(
            repository,
            identity,
            client_id=client_id,
            include_archived=include_archived,
        )

    @app.post("/api/v2/gc06/event-lines", status_code=status.HTTP_201_CREATED)
    def create_event_line(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.create_event_line(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v2/gc06/event-lines/{event_line_id}")
    def event_line_detail(event_line_id: str, identity: Identity) -> dict[str, Any]:
        return gc06_planning.event_line_detail(
            repository, identity, event_line_id=event_line_id
        )

    @app.patch("/api/v2/gc06/event-lines/{event_line_id}")
    def update_event_line(
        event_line_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.update_event_line(
            repository,
            identity,
            event_line_id=event_line_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/gc06/event-lines/{event_line_id}/activities")
    def record_event_line_activity(
        event_line_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.record_event_line_activity(
            repository,
            identity,
            event_line_id=event_line_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/gc06/event-lines/{event_line_id}/tasks/{task_id}")
    def attach_task_to_event_line(
        event_line_id: str,
        task_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.attach_task_to_event_line(
            repository,
            identity,
            event_line_id=event_line_id,
            task_id=task_id,
            expected_task_version=payload.get("expectedVersion"),
            allow_reassign=bool(payload.get("allowReassign", False)),
            idempotency_key=idempotency_key,
            task_command_port=task_command_port,
        )

    @app.post("/api/v2/gc06/event-lines/{event_line_id}/reparent")
    def reparent_event_line(
        event_line_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.reparent_event_line(
            repository,
            identity,
            event_line_id=event_line_id,
            target_client_id=str(payload.get("targetClientId") or ""),
            expected_version=payload.get("expectedVersion"),
            idempotency_key=idempotency_key,
            task_command_port=task_command_port,
        )

    @app.post("/api/v2/gc06/event-lines/{event_line_id}/merge")
    def merge_event_lines(
        event_line_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.merge_event_lines(
            repository,
            identity,
            target_event_line_id=event_line_id,
            source_event_line_ids=payload.get("sourceIds") or [],
            expected_version=payload.get("expectedVersion"),
            idempotency_key=idempotency_key,
            task_command_port=task_command_port,
        )

    # Keep the generic lifecycle route after concrete subresources so
    # `/activities` is never interpreted as a transition name by Starlette.
    @app.post("/api/v2/gc06/event-lines/{event_line_id}/{transition}")
    def transition_event_line(
        event_line_id: str,
        transition: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.transition_event_line(
            repository,
            identity,
            event_line_id=event_line_id,
            transition=transition,
            expected_version=payload.get("expectedVersion"),
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v2/gc06/planning-cycles")
    def list_planning_cycles(
        identity: Identity,
        include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
    ) -> list[dict[str, Any]]:
        return gc06_planning.list_planning_cycles(
            repository, identity, include_archived=include_archived
        )

    @app.post("/api/v2/gc06/planning-cycles", status_code=status.HTTP_201_CREATED)
    def create_planning_cycle(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.create_planning_cycle(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.patch("/api/v2/gc06/planning-cycles/{planning_cycle_id}")
    def update_planning_cycle(
        planning_cycle_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.update_planning_cycle(
            repository,
            identity,
            planning_cycle_id=planning_cycle_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.delete("/api/v2/gc06/planning-cycles/{planning_cycle_id}")
    def delete_planning_cycle(
        planning_cycle_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.delete_planning_cycle(
            repository,
            identity,
            planning_cycle_id=planning_cycle_id,
            expected_version=payload.get("expectedVersion"),
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v2/gc06/weekly-reviews")
    def list_weekly_reviews(
        identity: Identity,
        planning_cycle_id: Annotated[str | None, Query(alias="planningCycleId")] = None,
        membership_id: Annotated[str | None, Query(alias="membershipId")] = None,
    ) -> list[dict[str, Any]]:
        return gc06_planning.list_weekly_reviews(
            repository,
            identity,
            planning_cycle_id=planning_cycle_id,
            membership_id=membership_id,
        )

    @app.post("/api/v2/gc06/weekly-reviews/draft")
    def save_weekly_review_draft(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.save_weekly_review_draft(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/gc06/weekly-reviews/{review_id}/{transition}")
    def transition_weekly_review(
        review_id: str,
        transition: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.transition_weekly_review(
            repository,
            identity,
            review_id=review_id,
            transition=transition,
            expected_version=payload.get("expectedVersion"),
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v2/gc06/decision-actions")
    def list_decision_actions(
        identity: Identity,
        planning_cycle_id: Annotated[str | None, Query(alias="planningCycleId")] = None,
    ) -> list[dict[str, Any]]:
        return gc06_planning.list_decision_actions(
            repository, identity, planning_cycle_id=planning_cycle_id
        )

    @app.post("/api/v2/gc06/decision-actions", status_code=status.HTTP_201_CREATED)
    def create_decision_action(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.create_decision_action(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.patch("/api/v2/gc06/decision-actions/{action_id}")
    def update_decision_action(
        action_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.update_decision_action(
            repository,
            identity,
            action_id=action_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/gc06/decision-actions/{action_id}/primary-task")
    def convert_action_to_primary_task(
        action_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.convert_action_to_primary_task(
            repository,
            identity,
            action_id=action_id,
            expected_version=payload.get("expectedVersion"),
            idempotency_key=idempotency_key,
            task_command_port=task_command_port,
        )

    @app.get("/api/v2/gc06/plan-item-tasks")
    def list_plan_item_tasks(
        identity: Identity,
        plan_item_id: Annotated[str | None, Query(alias="planItemId")] = None,
    ) -> dict[str, Any]:
        return gc06_planning.list_plan_item_tasks(
            repository,
            identity,
            plan_item_id=plan_item_id,
        )

    @app.get("/api/v2/gc06/tasks/{task_id}/plan-link")
    def get_task_plan_link(task_id: str, identity: Identity) -> dict[str, Any]:
        return {
            "planLink": gc06_planning.get_task_plan_link(
                repository,
                identity,
                task_id=task_id,
            )
        }

    @app.patch("/api/v2/gc06/tasks/{task_id}/plan-link")
    def set_task_plan_link(
        task_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return {
            "planLink": gc06_planning.set_task_plan_link(
                repository,
                identity,
                task_id=task_id,
                action_id=(
                    str(payload.get("departmentPlanItemId") or "").strip() or None
                ),
                idempotency_key=idempotency_key,
            )
        }

    @app.get("/api/v2/gc06/meetings")
    def list_meetings(
        identity: Identity,
        client_id: Annotated[str | None, Query(alias="clientId")] = None,
    ) -> list[dict[str, Any]]:
        return gc06_planning.list_meetings(
            repository, identity, client_id=client_id
        )

    @app.post("/api/v2/gc06/meetings", status_code=status.HTTP_201_CREATED)
    def create_meeting(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.create_meeting(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.patch("/api/v2/gc06/meetings/{meeting_id}")
    def update_meeting(
        meeting_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.update_meeting(
            repository,
            identity,
            meeting_id=meeting_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/gc06/meetings/{meeting_id}/collaboration/{action}")
    def transition_meeting_collaboration(
        meeting_id: str,
        action: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: Idempotency,
    ) -> dict[str, Any]:
        return gc06_planning.transition_meeting_collaboration(
            repository,
            identity,
            meeting_id=meeting_id,
            action=action,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v2/gc06/calendar")
    def list_calendar_entries(
        identity: Identity,
        starts_from: Annotated[str | None, Query(alias="startsFrom")] = None,
        starts_to: Annotated[str | None, Query(alias="startsTo")] = None,
    ) -> list[dict[str, Any]]:
        return gc06_planning.list_calendar_entries(
            repository,
            identity,
            starts_from=starts_from,
            starts_to=starts_to,
        )

    @app.get("/api/v2/gc06/clients-pulse")
    def clients_pulse(identity: Identity) -> dict[str, Any]:
        return gc06_planning.clients_pulse(repository, identity)
