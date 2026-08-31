from __future__ import annotations

from typing import Any, Mapping, Protocol

from ..repository import CloudRepository, RepositoryError, SessionIdentity


class FormalTaskCommandPort(Protocol):
    """The only GC-06 boundary allowed to mutate ``tasks``.

    The GC-04 integration thread must provide an adapter backed by its formal,
    idempotent task commands.  GC-06 deliberately contains no fallback task
    writer.
    """

    def attach_event_line(
        self,
        repository: CloudRepository,
        identity: SessionIdentity,
        *,
        task_id: str,
        event_line_id: str,
        target_client_id: str | None,
        expected_version: int,
        allow_reassign: bool,
        idempotency_key: str,
    ) -> Mapping[str, Any]: ...

    def create_primary_task_for_action(
        self,
        repository: CloudRepository,
        identity: SessionIdentity,
        *,
        action: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]: ...

    def move_task_scope(
        self,
        repository: CloudRepository,
        identity: SessionIdentity,
        *,
        task_id: str,
        client_id: str,
        event_line_id: str | None,
        expected_version: int,
        idempotency_key: str,
    ) -> Mapping[str, Any]: ...


class UnavailableFormalTaskCommandPort:
    def _raise(self) -> None:
        raise RepositoryError(
            501,
            "task_command_not_connected",
            "正式任务命令尚未接入；GC-06 不会建立第二条任务写入线路",
        )

    def attach_event_line(
        self,
        repository: CloudRepository,
        identity: SessionIdentity,
        *,
        task_id: str,
        event_line_id: str,
        target_client_id: str | None,
        expected_version: int,
        allow_reassign: bool,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        del (
            repository,
            identity,
            task_id,
            event_line_id,
            target_client_id,
            expected_version,
            allow_reassign,
            idempotency_key,
        )
        self._raise()

    def create_primary_task_for_action(
        self,
        repository: CloudRepository,
        identity: SessionIdentity,
        *,
        action: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        del repository, identity, action, idempotency_key
        self._raise()

    def move_task_scope(
        self,
        repository: CloudRepository,
        identity: SessionIdentity,
        *,
        task_id: str,
        client_id: str,
        event_line_id: str | None,
        expected_version: int,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        del repository, identity, task_id, client_id, event_line_id, expected_version, idempotency_key
        self._raise()


class GC04FormalTaskCommandPort:
    """Bridge GC-06 actions into the single GC-04 task command authority."""

    def attach_event_line(
        self,
        repository: CloudRepository,
        identity: SessionIdentity,
        *,
        task_id: str,
        event_line_id: str,
        target_client_id: str | None,
        expected_version: int,
        allow_reassign: bool,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        del allow_reassign
        from .gc04_tasks import GC04TaskRepository

        with repository._connection() as connection:  # noqa: SLF001
            task = connection.execute(
                "SELECT client_id FROM tasks WHERE id=? AND scope_id=? "
                "AND lifecycle_state!='deleted'",
                (task_id, identity.scope_id),
            ).fetchone()
        if task is None:
            raise RepositoryError(404, "task_missing", "任务不存在")
        return GC04TaskRepository(repository).update_task(
            identity,
            task_id=task_id,
            payload={
                "expectedVersion": expected_version,
                "clientId": target_client_id or task["client_id"],
                "eventLineId": event_line_id,
            },
            idempotency_key=idempotency_key,
        )

    def create_primary_task_for_action(
        self,
        repository: CloudRepository,
        identity: SessionIdentity,
        *,
        action: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        from .gc04_tasks import GC04TaskRepository

        statement = str(action.get("statement") or "").strip()
        expected_output = str(action.get("expectedOutput") or "").strip()
        description = "\n\n".join(
            item
            for item in (
                statement,
                f"预期产出：{expected_output}" if expected_output else "",
            )
            if item
        )
        return GC04TaskRepository(repository).create_task(
            identity,
            payload={
                "title": str(action.get("title") or "行动任务").strip(),
                "description": description,
                "priority": "normal",
                "clientId": action.get("clientId"),
                "ownerMembershipId": action.get("ownerMembershipId") or identity.membership_id,
                "collaboratorMembershipIds": [],
                "sourceType": "decision_action",
                "sourceId": action.get("id"),
            },
            idempotency_key=idempotency_key,
        )

    def move_task_scope(
        self,
        repository: CloudRepository,
        identity: SessionIdentity,
        *,
        task_id: str,
        client_id: str,
        event_line_id: str | None,
        expected_version: int,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        from .gc04_tasks import GC04TaskRepository

        return GC04TaskRepository(repository).update_task(
            identity,
            task_id=task_id,
            payload={
                "expectedVersion": expected_version,
                "clientId": client_id,
                "eventLineId": event_line_id,
            },
            idempotency_key=idempotency_key,
        )

UNAVAILABLE_FORMAL_TASK_COMMAND_PORT = UnavailableFormalTaskCommandPort()
GC04_FORMAL_TASK_COMMAND_PORT = GC04FormalTaskCommandPort()
