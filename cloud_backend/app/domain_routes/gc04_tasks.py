"""Unregistered GC-04/GC-05 routes for integration by the shared entry thread."""

from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, status

from ..repositories.gc04_tasks import GC04TaskRepository
from ..repositories.platform_integrations import PlatformIntegrationsRepository
from ..repositories.task_planning_agent import TaskPlanningAgentRepository
from ..repository import CloudRepository, SessionIdentity


def register_gc04_task_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    """Mount the strict task authority without importing legacy workflow code."""

    domain = GC04TaskRepository(repository)
    task_planning = TaskPlanningAgentRepository(repository)
    platform_integrations = PlatformIntegrationsRepository(repository)
    Identity = Annotated[SessionIdentity, Depends(identity_dependency)]
    IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key")]

    def finish_with_feishu_notification(
        identity: SessionIdentity,
        result: dict[str, Any],
        *,
        event: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        try:
            notification = platform_integrations.deliver_task_notifications(
                identity,
                result=result,
                event=event,
                idempotency_key=idempotency_key,
            )
        except Exception:  # Task facts already committed; provider work must not falsify that result.
            notification = {
                "state": "failed_retryable",
                "requestedRecipients": 0,
                "deliveryCount": 0,
                "partialSuccess": False,
                "message": "任务已生效；飞书通知暂未送达，可稍后重试",
            }
        return {**result, "notificationResult": notification}

    @app.get("/api/v2/domain/tasks")
    def task_board(identity: Identity) -> dict[str, Any]:
        return domain.board(identity)

    @app.get("/api/v2/domain/task-agents/coordination")
    def task_agent_coordination(
        identity: Identity,
        week: str | None = None,
        month: str | None = None,
        department: str | None = None,
    ) -> dict[str, Any]:
        return domain.agent_coordination(
            identity,
            week_label=week,
            month=month,
            department_name=department,
        )

    @app.put("/api/v2/domain/task-agents/weekly-plans/{week_label}/{agent_key}")
    def save_task_agent_weekly_plan(
        week_label: str,
        agent_key: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.save_agent_weekly_plan(
            identity,
            week_label=week_label,
            agent_key=agent_key,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v2/domain/task-planning/project-keyword-profiles")
    def project_keyword_profiles(identity: Identity) -> list[dict[str, Any]]:
        return task_planning.list_profiles(identity)

    @app.post("/api/v2/domain/task-planning/parse-draft")
    def parse_task_draft(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
    ) -> dict[str, Any]:
        return task_planning.parse_draft(identity, payload=payload)

    @app.post("/api/v2/domain/task-planning/project-keyword-profiles/{client_id}/refresh")
    def refresh_project_keyword_profile(
        client_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return task_planning.refresh_profile(
            identity,
            client_id=client_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/domain/tasks", status_code=status.HTTP_201_CREATED)
    def create_task(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        result = domain.create_task(
            identity, payload=payload, idempotency_key=idempotency_key
        )
        return finish_with_feishu_notification(
            identity, result, event="created", idempotency_key=idempotency_key
        )

    @app.post("/api/v2/domain/tasks/lists", status_code=status.HTTP_201_CREATED)
    def create_task_list(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.create_list(
            identity, payload=payload, idempotency_key=idempotency_key
        )

    @app.patch("/api/v2/domain/tasks/lists/{list_id}")
    def update_task_list(
        list_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.update_list(
            identity,
            list_id=list_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.delete("/api/v2/domain/tasks/lists/{list_id}")
    def delete_task_list(
        list_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.delete_list(
            identity,
            list_id=list_id,
            expected_version=int(payload.get("expectedVersion") or 0),
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/domain/tasks/tags", status_code=status.HTTP_201_CREATED)
    def create_task_tag(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.create_tag(identity, payload=payload, idempotency_key=idempotency_key)

    @app.patch("/api/v2/domain/tasks/tags/{tag_id}")
    def update_task_tag(
        tag_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.update_tag(
            identity, tag_id=tag_id, payload=payload, idempotency_key=idempotency_key
        )

    @app.delete("/api/v2/domain/tasks/tags/{tag_id}")
    def delete_task_tag(
        tag_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.delete_tag(
            identity,
            tag_id=tag_id,
            expected_version=int(payload.get("expectedVersion") or 0),
            idempotency_key=idempotency_key,
        )

    @app.get("/api/v2/domain/tasks/{task_id}")
    def task_detail(task_id: str, identity: Identity) -> dict[str, Any]:
        return domain.task_detail(identity, task_id=task_id)

    @app.get("/api/v2/domain/tasks/{task_id}/context")
    def task_context(task_id: str, identity: Identity) -> dict[str, Any]:
        return domain.task_context(identity, task_id=task_id)

    @app.patch("/api/v2/domain/tasks/{task_id}")
    def update_task(
        task_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        result = domain.update_task(
            identity,
            task_id=task_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        return finish_with_feishu_notification(
            identity, result, event="updated", idempotency_key=idempotency_key
        )

    @app.delete("/api/v2/domain/tasks/{task_id}")
    def delete_task(
        task_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.delete_task(
            identity,
            task_id=task_id,
            expected_version=int(payload.get("expectedVersion") or 0),
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/domain/tasks/{task_id}/inbox/{action}")
    def handle_task_inbox(
        task_id: str,
        action: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        result = domain.handle_inbox(
            identity,
            task_id=task_id,
            action=action,
            expected_version=int(payload.get("expectedVersion") or 0),
            reason=payload.get("reason"),
            idempotency_key=idempotency_key,
        )
        return finish_with_feishu_notification(
            identity,
            result,
            event="accepted" if action == "accept" else "returned",
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/domain/tasks/{task_id}/transfer")
    def transfer_task(
        task_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        result = domain.transfer_task(
            identity,
            task_id=task_id,
            target_membership_id=str(payload.get("targetMembershipId") or ""),
            expected_owner_version=int(payload.get("expectedOwnerVersion") or 0),
            idempotency_key=idempotency_key,
        )
        return finish_with_feishu_notification(
            identity, result, event="transferred", idempotency_key=idempotency_key
        )

    @app.post("/api/v2/domain/tasks/{task_id}/agent-proposals")
    def propose_task_change(
        task_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.create_agent_proposal(
            identity,
            task_id=task_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/domain/task-bulk/preflight")
    def bulk_preflight(
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.bulk_preflight(
            identity, payload=payload, idempotency_key=idempotency_key
        )

    @app.post("/api/v2/domain/task-bulk/{bulk_operation_id}/commit")
    def bulk_commit(
        bulk_operation_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.bulk_commit(
            identity,
            bulk_operation_id=bulk_operation_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )


__all__ = ["register_gc04_task_routes"]
