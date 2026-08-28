"""Unregistered GC-04/GC-05 routes for integration by the shared entry thread."""

from datetime import datetime
import re
from typing import Annotated, Any
from zoneinfo import ZoneInfo

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

    def task_change_summary(
        before: dict[str, Any], after: dict[str, Any]
    ) -> tuple[list[str], dict[str, dict[str, str]], dict[str, dict[str, str]]]:
        """Describe the four user-visible fields that warrant a notification."""

        labels: list[str] = []
        field_changes: dict[str, dict[str, str]] = {}

        def value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
            return next((record.get(key) for key in keys if key in record), None)

        def compact(raw: Any, *, limit: int = 34) -> str:
            text = str(raw or "").strip()
            if not text:
                return "未设置"
            return text if len(text) <= limit else f"{text[:limit]}…"

        def time_text(raw: Any) -> str:
            text = str(raw or "").strip()
            if not text:
                return "未设置"
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
                    "%Y-%m-%d %H:%M"
                )
            except ValueError:
                return compact(text)

        priority_names = {"low": "低", "normal": "普通", "high": "高"}

        old_title = value(before, ("title",))
        new_title = value(after, ("title",))
        if old_title != new_title:
            labels.append("任务名称")
            field_changes["title"] = {
                "old": compact(old_title),
                "new": compact(new_title),
            }

        def schedule(record: dict[str, Any]) -> tuple[Any, Any, bool]:
            start = value(record, ("scheduled_start_at", "scheduledStartAt"))
            end = value(record, ("scheduled_end_at", "scheduledEndAt"))
            if start and re.search(r"(?:T|\s)\d{1,2}:\d{2}", str(start)):
                return start, end, True
            start_date = value(
                record,
                ("scheduled_start_at", "scheduledStartAt", "start_date", "startDate", "due_date", "dueDate"),
            )
            due_date = value(record, ("due_date", "dueDate", "start_date", "startDate"))
            return start_date, due_date, False

        old_start, old_end, old_timed = schedule(before)
        new_start, new_end, new_timed = schedule(after)

        def time_range(start: Any, end: Any, *, timed: bool) -> str:
            start_text = time_text(start) if timed else compact(start)
            end_text = time_text(end) if timed else compact(end)
            if start_text == "未设置":
                return "未设置"
            if end_text == "未设置":
                return start_text
            if not timed:
                return start_text if start_text == end_text else f"{start_text}—{end_text}"
            if start_text[:10] == end_text[:10]:
                return f"{start_text}—{end_text[11:]}"
            return f"{start_text}—{end_text}"

        if (old_start, old_end, old_timed) != (new_start, new_end, new_timed):
            labels.append("时间")
            field_changes["time"] = {
                "old": time_range(old_start, old_end, timed=old_timed),
                "new": time_range(new_start, new_end, timed=new_timed),
            }

        old_priority = value(before, ("priority",))
        new_priority = value(after, ("priority",))
        if old_priority != new_priority:
            labels.append("优先级")
            field_changes["priority"] = {
                "old": priority_names.get(str(old_priority or ""), compact(old_priority)),
                "new": priority_names.get(str(new_priority or ""), compact(new_priority)),
            }

        def collaborators(record: dict[str, Any]) -> tuple[tuple[str, str, str], ...]:
            rows = record.get("collaborators")
            if not isinstance(rows, list):
                return ()
            return tuple(
                sorted(
                    (
                        str(item.get("subject_membership_id") or ""),
                        str(item.get("role_key") or ""),
                        str(item.get("display_name") or "未命名成员"),
                    )
                    for item in rows
                    if isinstance(item, dict)
                    and str(item.get("assignment_state") or "")
                    in {"assigned", "awaiting_owner", "returned"}
                )
            )

        role_names = {"owner": "负责人", "collaborator": "协作者"}
        before_roles = {
            member_id: role_names.get(role, "未参与")
            for member_id, role, _name in collaborators(before)
        }
        after_roles = {
            member_id: role_names.get(role, "未参与")
            for member_id, role, _name in collaborators(after)
        }
        role_changes = {
            member_id: {
                "old": before_roles.get(member_id, "未参与"),
                "new": after_roles.get(member_id, "未参与"),
            }
            for member_id in set(before_roles) | set(after_roles)
            if before_roles.get(member_id, "未参与")
            != after_roles.get(member_id, "未参与")
        }
        if role_changes:
            labels.append("你的身份")
        return labels, field_changes, role_changes

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
        before = domain.task_detail(identity, task_id=task_id)["task"]
        result = domain.update_task(
            identity,
            task_id=task_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
        after = result.get("task") if isinstance(result, dict) else None
        was_completed = bool(before.get("completed_at") or before.get("completedAt"))
        is_completed = bool(
            isinstance(after, dict)
            and (after.get("completed_at") or after.get("completedAt"))
        )
        event = (
            "completed" if not was_completed and is_completed
            else "reopened" if was_completed and not is_completed
            else "updated"
        )
        if event == "updated" and isinstance(after, dict):
            change_labels, field_changes, role_changes = task_change_summary(before, after)
            result = {
                **result,
                "notificationChanges": change_labels,
                "notificationFieldChanges": field_changes,
                "notificationRoleChanges": role_changes,
            }
            # Description-only edits are intentionally silent on Feishu.
            if not change_labels:
                return {
                    **result,
                    "notificationResult": {
                        "state": "skipped",
                        "reason": "no_notifiable_changes",
                        "message": "任务已保存；本次变化无需发送飞书通知",
                    },
                }
        return finish_with_feishu_notification(
            identity, result, event=event, idempotency_key=idempotency_key
        )

    @app.post("/api/v2/domain/tasks/{task_id}/timer/{action}")
    def update_task_timer(
        task_id: str,
        action: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.update_task_timer(
            identity,
            task_id=task_id,
            action=action,
            expected_timer_version=int(payload.get("expectedTimerVersion") or 0),
            idempotency_key=idempotency_key,
        )

    @app.post("/api/v2/domain/tasks/{task_id}/timer/{action}")
    def update_task_timer(
        task_id: str,
        action: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return domain.update_task_timer(
            identity,
            task_id=task_id,
            action=action,
            expected_timer_version=int(payload.get("expectedTimerVersion") or 0),
            idempotency_key=idempotency_key,
        )

    @app.delete("/api/v2/domain/tasks/{task_id}")
    def delete_task(
        task_id: str,
        payload: Annotated[dict[str, Any], Body()],
        identity: Identity,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        task = domain.task_detail(identity, task_id=task_id)["task"]
        result = domain.delete_task(
            identity,
            task_id=task_id,
            expected_version=int(payload.get("expectedVersion") or 0),
            idempotency_key=idempotency_key,
        )
        try:
            notification = platform_integrations.deliver_task_notifications(
                identity,
                result={**result, "task": task},
                event="deleted",
                idempotency_key=idempotency_key,
            )
        except Exception:
            notification = {
                "state": "failed_retryable",
                "requestedRecipients": 0,
                "deliveryCount": 0,
                "partialSuccess": False,
                "message": "软件任务已删除；飞书通知和投影稍后重试",
            }
        return {**result, "notificationResult": notification}

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
        if action == "accept":
            return {
                **result,
                "notificationResult": {
                    "state": "skipped",
                    "reason": "owner_acceptance_keeps_existing_projection",
                    "message": "负责人已接受；既有飞书任务和通知保持不变",
                },
            }
        return finish_with_feishu_notification(
            identity,
            result,
            event="returned",
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
