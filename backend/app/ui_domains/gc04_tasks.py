"""Detached GC-04/GC-05 UI adapter; shared registry wiring is external."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping
from urllib.parse import quote, unquote

from strict_common.ids import sha256_text, utc_now

from ..gc04_tasks_local import LocalGC04TaskProjection
from ..runtime import LocalRuntimeError
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("gc04_gc05_tasks", pin_workspace=True)
_CLOUD_TASKS = "/api/v2/domain/tasks"
_CLOUD_TASK_PLANNING = "/api/v2/domain/task-planning"
_LIST_COLOR = "#5B7BFE"


def _projector(compatibility: Any) -> LocalGC04TaskProjection:
    return LocalGC04TaskProjection(compatibility.runtime)


def _query(compatibility: Any, path: str) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(path)


@router.get(r"gc04/project-keyword-profiles")
def project_keyword_profiles(compatibility: Any, _: UiRequest, __: Any) -> Any:
    return compatibility.runtime.cloud_query(
        f"{_CLOUD_TASK_PLANNING}/project-keyword-profiles"
    )


@router.post(r"gc04/project-keyword-profiles/(?P<client_id>[^/]+)/refresh")
def refresh_project_keyword_profile(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> Any:
    return compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_TASK_PLANNING}/project-keyword-profiles/{match.group('client_id')}/refresh",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


def _command(
    compatibility: Any,
    request: UiRequest,
    method: str,
    path: str,
    payload: Mapping[str, Any],
    *,
    key_suffix: str | None = None,
) -> dict[str, Any]:
    key = request.idempotency_key
    if key_suffix:
        key = f"{key}:{key_suffix}"
    return compatibility.runtime.cloud_command(
        method,
        path,
        payload=dict(payload),
        idempotency_key=key,
    )


def _apply(
    compatibility: Any,
    payload: Mapping[str, Any],
    *,
    replace_snapshot: bool = False,
) -> None:
    projection = payload.get("projection")
    if isinstance(projection, Mapping):
        _projector(compatibility).apply(
            projection, replace_snapshot=replace_snapshot
        )


def _collaborator_ui(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    return {
        "userId": str(row.get("subject_membership_id") or ""),
        "fullName": str(row.get("display_name") or "未命名成员"),
        "email": "",
        "orderIndex": index,
        "isOwner": str(row.get("role_key") or "") == "owner",
        "assignmentState": str(row.get("assignment_state") or "assigned"),
        "inboxStatus": str(row.get("inbox_status") or "accepted"),
        "returnReason": None,
        "handledAt": row.get("responded_at"),
        "version": int(row.get("version") or 1),
    }


def _task_ui(
    compatibility: Any | Mapping[str, Any],
    row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    # Keep the one-argument pure presentation form used by focused contract
    # tests; the assembled UI passes compatibility to overlay device-local
    # attachment availability.
    if row is None:
        row = compatibility if isinstance(compatibility, Mapping) else {}
        compatibility = None
    collaborators = [
        _collaborator_ui(item, index)
        for index, item in enumerate(row.get("collaborators") or [])
        if isinstance(item, Mapping)
    ]
    owner = next((item for item in collaborators if item["isOwner"]), None)
    task_list = row.get("list") if isinstance(row.get("list"), Mapping) else {}
    client = row.get("client") if isinstance(row.get("client"), Mapping) else {}
    event_line = (
        row.get("event_line")
        if isinstance(row.get("event_line"), Mapping)
        else {}
    )
    done = bool(row.get("completed_at"))
    task_kind = str(row.get("task_kind") or "task")
    viewer_inbox_status = row.get("viewer_inbox_status")
    viewer_role_key = row.get("viewer_role_key")
    returned_to_creator = bool(row.get("returned_to_creator"))
    counts: dict[str, int] = {}
    for collaborator in collaborators:
        state = str(collaborator.get("inboxStatus") or "accepted")
        counts[state] = counts.get(state, 0) + 1
    cloud_attachments = [
        dict(item) for item in row.get("attachments") or [] if isinstance(item, Mapping)
    ]
    try:
        if compatibility is None:
            raise LocalRuntimeError(404, "local_overlay_unavailable", "")
        from ..project_materials_local import LocalProjectMaterialsRepository

        local_attachments = LocalProjectMaterialsRepository(
            compatibility.runtime
        ).task_attachments(str(row.get("id") or ""))
    except LocalRuntimeError:
        local_attachments = []
    by_id = {str(item.get("id") or ""): item for item in cloud_attachments}
    by_id.update({str(item.get("id") or ""): item for item in local_attachments})
    attachments = [item for key, item in by_id.items() if key]
    tags = [
        {
            "id": str(item.get("taskTagId") or ""),
            "name": str(item.get("name") or ""),
            "color": str(item.get("color") or _LIST_COLOR),
            "scope": "self" if item.get("scopeKind") == "personal" else "org",
            "ownerUserId": item.get("ownerMembershipId"),
            "updatedAt": item.get("updatedAt"),
            "version": int(item.get("version") or 1),
        }
        for item in row.get("tags") or []
        if isinstance(item, Mapping) and item.get("taskTagId")
    ]
    return {
        "id": str(row.get("id") or ""),
        "title": str(row.get("title") or ""),
        "desc": str(row.get("description") or ""),
        "status": (
            "done"
            if done
            else "rejected"
            if returned_to_creator
            else "inbox"
            if viewer_inbox_status == "pending" and viewer_role_key == "owner"
            else "todo"
        ),
        "creatorId": str(row.get("creator_membership_id") or "") or None,
        "creatorName": str(row.get("creator_display_name") or "") or None,
        "priority": str(row.get("priority") or "normal"),
        "listId": str(row.get("task_list_id") or ""),
        "listName": str(task_list.get("name") or ""),
        "listColor": _LIST_COLOR,
        "ddl": str(row.get("due_date") or ""),
        "startDate": row.get("scheduled_start_at"),
        "dueDate": row.get("due_date"),
        "deadlineAt": row.get("due_date"),
        "scheduledStartAt": row.get("scheduled_start_at"),
        "scheduledEndAt": row.get("scheduled_end_at"),
        "durationMinutes": row.get("duration_minutes"),
        "completedAt": row.get("completed_at"),
        "note": row.get("completion_note"),
        "orgContext": {
            "needsReview": task_kind == "review_pending",
            "approvalState": "pending" if task_kind == "review_pending" else None,
            "reviewReturned": task_kind == "review_returned",
        },
        "scopeMode": (
            "PERSONAL_ONLY"
            if str(row.get("visibility_scope") or "") == "self"
            else "COLLAB_SHARED"
        ),
        "clientId": row.get("client_id"),
        "clientName": client.get("name"),
        "eventLineId": row.get("event_line_id"),
        "eventLineName": event_line.get("name"),
        "planningCycleId": row.get("planning_cycle_id"),
        "ownerId": owner.get("userId") if owner else None,
        "ownerName": owner.get("fullName") if owner else "未设置负责人",
        "sourceType": str(row.get("source_type") or "manual"),
        "sourceId": row.get("source_id"),
        "evidenceCount": len(attachments),
        "tags": tags,
        "tagIds": [item["id"] for item in tags],
        "attachments": attachments,
        "collaborators": collaborators,
        "collaborationSummary": counts,
        "viewerInboxStatus": viewer_inbox_status,
        "viewerCollaborationRole": viewer_role_key,
        "syncStatus": "synced",
        "syncError": None,
        "createdAt": str(row.get("created_at") or ""),
        "updatedAt": str(row.get("updated_at") or ""),
        "version": int(row.get("version") or 1),
    }


def _list_ui(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "color": _LIST_COLOR,
        "sortOrder": int(row.get("sort_order") or 0),
        "isDefault": False,
        "scope": (
            "org"
            if str(row.get("visibility_scope") or "") == "organization"
            else "personal"
        ),
        "archivedAt": row.get("archived_at"),
        "version": int(row.get("version") or 1),
        "colorPersisted": False,
    }


def _view_ui(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        spec = json.loads(str(row.get("filter_spec") or "{}"))
    except json.JSONDecodeError:
        spec = {}
    if not isinstance(spec, Mapping):
        spec = {}
    return {
        "id": str(row.get("id") or ""),
        "name": str(spec.get("name") or "未命名任务视图"),
        "kind": str(spec.get("kind") or "custom"),
        "description": str(spec.get("description") or ""),
        "calendarScope": str(spec.get("calendarScope") or "all"),
        "shareability": (
            "org" if not row.get("viewer_membership_id") else "private"
        ),
        "sortBy": str(spec.get("sortBy") or "updatedAt"),
        "sortDirection": str(spec.get("sortDirection") or "desc"),
        "visibleFields": list(spec.get("visibleFields") or []),
        "filterSet": dict(spec.get("filterSet") or {}),
        "builtIn": False,
        "createdAt": str(row.get("created_at") or ""),
        "updatedAt": str(row.get("updated_at") or ""),
        "version": int(row.get("version") or 1),
    }


def _tag_ui(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("tag_name") or ""),
        "color": str(row.get("tag_color") or _LIST_COLOR),
        "scope": "self" if row.get("scope_kind") == "personal" else "org",
        "ownerUserId": row.get("assigned_by_membership_id"),
        "archived": str(row.get("lifecycle_state") or "") != "active",
        "version": int(row.get("version") or 1),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


@router.get(r"tasks")
def task_board(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    result = _query(compatibility, _CLOUD_TASKS)
    _apply(compatibility, result, replace_snapshot=True)
    return {
        "tasks": [_task_ui(compatibility, item) for item in result.get("tasks") or []],
        "lists": [_list_ui(item) for item in result.get("taskLists") or []],
        "tags": [_tag_ui(item) for item in result.get("taskTags") or []],
        "calendarEntries": list(result.get("calendarEntries") or []),
        "notificationConnection": "not_connected",
    }


@router.get(r"tasks/agent-worklogs")
def agent_worklogs(compatibility: Any, request: UiRequest, _: Any) -> dict[str, Any]:
    result = compatibility.runtime.cloud_query(
        "/api/v2/domain/task-agents/coordination",
        query={"month": str(request.query.get("month") or "")},
    )
    return {
        "month": str(request.query.get("month") or ""),
        "worklogs": list(result.get("worklogs") or []),
        "weeklyDigests": list(result.get("weeklyDigests") or []),
        "weeklyPlans": list(result.get("weeklyPlans") or []),
    }


@router.get(r"tasks/agent-execution")
def agent_execution_tasks(compatibility: Any, request: UiRequest, _: Any) -> list[dict[str, Any]]:
    result = compatibility.runtime.cloud_query(
        "/api/v2/domain/task-agents/coordination",
        query={
            "week": str(request.query.get("week") or ""),
            "department": str(request.query.get("department") or ""),
        },
    )
    return [
        _task_ui(compatibility, item)
        for item in result.get("tasks") or []
        if isinstance(item, Mapping)
    ]


@router.put(r"tasks/agent-weekly-plans/(?P<week_label>[^/]+)/(?P<agent_key>[^/]+)")
def save_agent_weekly_plan(compatibility: Any, request: UiRequest, match: Any) -> dict[str, Any]:
    result = compatibility.runtime.cloud_command(
        "PUT",
        "/api/v2/domain/task-agents/weekly-plans/"
        f"{quote(unquote(match.group('week_label')), safe='')}/"
        f"{quote(unquote(match.group('agent_key')), safe='')}",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    return dict(result.get("weeklyPlan") or {})


@router.post(r"tasks")
def create_task(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    result = _command(
        compatibility, request, "POST", _CLOUD_TASKS, request.body
    )
    _apply(compatibility, result)
    return _task_ui(compatibility, result.get("task") or {})


@router.patch(r"tasks/(?P<task_id>[^/]+)")
def update_task(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    payload = dict(request.body)
    projected_client_id, projected_event_line_id = _projector(
        compatibility
    ).task_binding(task_id)
    requested_client_id = (
        (str(payload.get("clientId") or "").strip() or None)
        if "clientId" in payload
        else projected_client_id
    )
    requested_event_line_id = (
        (str(payload.get("eventLineId") or "").strip() or None)
        if "eventLineId" in payload
        else projected_event_line_id
    )
    # 编辑器提交完整表单。没有改变的既有归属不是一次新关联；尤其不能因
    # 事件线后来归档，就阻塞任务描述、优先级或截止日期的正常编辑。
    if requested_client_id == projected_client_id:
        payload.pop("clientId", None)
    if requested_event_line_id == projected_event_line_id:
        payload.pop("eventLineId", None)
    payload["expectedVersion"] = int(
        payload.get("expectedVersion") or _projector(compatibility).task_version(task_id)
    )
    result = _command(
        compatibility,
        request,
        "PATCH",
        f"{_CLOUD_TASKS}/{quote(task_id, safe='')}",
        payload,
    )
    _apply(compatibility, result)
    return _task_ui(compatibility, result.get("task") or {})


def _task_patch_command(
    compatibility: Any,
    request: UiRequest,
    *,
    task_id: str,
    patch: Mapping[str, Any],
) -> dict[str, Any]:
    expected = int(
        request.body.get("expectedVersion")
        or _projector(compatibility).task_version(task_id)
    )
    result = _command(
        compatibility,
        request,
        "PATCH",
        f"{_CLOUD_TASKS}/{quote(task_id, safe='')}",
        {**dict(patch), "expectedVersion": expected},
    )
    _apply(compatibility, result)
    return _task_ui(compatibility, result.get("task") or {})


@router.post(r"tasks/(?P<task_id>[^/]+)/complete-with-review")
def complete_with_review(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    note = str(request.body.get("reviewNote") or "").strip()
    if not note:
        raise LocalRuntimeError(422, "task_review_note_required", "请填写完成复核备注")
    return _task_patch_command(
        compatibility,
        request,
        task_id=unquote(match.group("task_id")),
        patch={"completionNote": note, "taskKind": "review_pending", "status": "todo"},
    )


@router.post(r"tasks/(?P<task_id>[^/]+)/review/approve")
def approve_review(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    return _task_patch_command(
        compatibility,
        request,
        task_id=unquote(match.group("task_id")),
        patch={"taskKind": "standard", "status": "completed"},
    )


@router.post(r"tasks/(?P<task_id>[^/]+)/review/return")
def return_review(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    reason = str(request.body.get("reason") or "").strip()
    if not reason:
        raise LocalRuntimeError(422, "task_review_return_reason_required", "请填写退回复核原因")
    return _task_patch_command(
        compatibility,
        request,
        task_id=unquote(match.group("task_id")),
        patch={"completionNote": reason, "taskKind": "review_returned", "status": "todo"},
    )


@router.post(r"tasks/(?P<task_id>[^/]+)/note")
def save_note(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    note = str(request.body.get("note") or "").strip()
    if not note:
        raise LocalRuntimeError(422, "task_note_required", "任务备注不能为空")
    return _task_patch_command(
        compatibility,
        request,
        task_id=unquote(match.group("task_id")),
        patch={"completionNote": note},
    )


@router.delete(r"tasks/(?P<task_id>[^/]+)")
def delete_task(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    expected = int(
        request.body.get("expectedVersion")
        or _projector(compatibility).task_version(task_id)
    )
    result = _command(
        compatibility,
        request,
        "DELETE",
        f"{_CLOUD_TASKS}/{quote(task_id, safe='')}",
        {"expectedVersion": expected},
    )
    _apply(compatibility, result)
    return result


def _inbox(
    compatibility: Any,
    request: UiRequest,
    task_id: str,
    action: str,
    *,
    key_suffix: str | None = None,
) -> dict[str, Any]:
    expected = int(
        request.body.get("expectedVersion")
        or _projector(compatibility).collaborator_version(task_id)
    )
    result = _command(
        compatibility,
        request,
        "POST",
        f"{_CLOUD_TASKS}/{quote(task_id, safe='')}/inbox/{action}",
        {"expectedVersion": expected, "reason": request.body.get("reason")},
        key_suffix=key_suffix,
    )
    _apply(compatibility, result)
    return {
        **result,
        "task": _task_ui(compatibility, result.get("task") or {}),
    }


@router.post(r"tasks/(?P<task_id>[^/]+)/confirm")
def accept_task(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    return _inbox(
        compatibility, request, unquote(match.group("task_id")), "accept"
    )


@router.post(r"tasks/(?P<task_id>[^/]+)/reject")
def return_task(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    return _inbox(
        compatibility, request, unquote(match.group("task_id")), "return"
    )


@router.post(r"tasks/collaboration/batch-handle")
def accept_tasks_batch(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    accepted: list[str] = []
    skipped: list[dict[str, str]] = []
    for raw_id in request.body.get("taskIds") or []:
        task_id = str(raw_id or "").strip()
        if not task_id:
            continue
        try:
            _inbox(
                compatibility,
                request,
                task_id,
                "accept",
                key_suffix=task_id,
            )
            accepted.append(task_id)
        except LocalRuntimeError as exc:
            skipped.append({"taskId": task_id, "reason": exc.message})
    return {
        "acceptedIds": accepted,
        "acknowledgedIds": [],
        "skippedItems": skipped,
    }


@router.post(r"tasks/(?P<task_id>[^/]+)/transfer")
def transfer_task(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    result = _command(
        compatibility,
        request,
        "POST",
        f"{_CLOUD_TASKS}/{quote(task_id, safe='')}/transfer",
        request.body,
    )
    _apply(compatibility, result)
    return {
        **result,
        "task": _task_ui(compatibility, result.get("task") or {}),
    }


@router.post(r"task-lists")
def create_list(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    result = _command(
        compatibility, request, "POST", f"{_CLOUD_TASKS}/lists", request.body
    )
    _apply(compatibility, result)
    return _list_ui(result.get("taskList") or {})


@router.patch(r"task-lists/(?P<list_id>[^/]+)")
def update_list(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    list_id = unquote(match.group("list_id"))
    payload = dict(request.body)
    payload["expectedVersion"] = int(
        payload.get("expectedVersion") or _projector(compatibility).list_version(list_id)
    )
    result = _command(
        compatibility,
        request,
        "PATCH",
        f"{_CLOUD_TASKS}/lists/{quote(list_id, safe='')}",
        payload,
    )
    _apply(compatibility, result)
    return _list_ui(result.get("taskList") or {})


@router.delete(r"task-lists/(?P<list_id>[^/]+)")
def delete_list(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    list_id = unquote(match.group("list_id"))
    expected = int(
        request.body.get("expectedVersion")
        or _projector(compatibility).list_version(list_id)
    )
    result = _command(
        compatibility,
        request,
        "DELETE",
        f"{_CLOUD_TASKS}/lists/{quote(list_id, safe='')}",
        {"expectedVersion": expected},
    )
    _apply(compatibility, result)
    return result


@router.post(r"task-tags")
def create_tag(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    result = _command(
        compatibility, request, "POST", f"{_CLOUD_TASKS}/tags", request.body
    )
    _apply(compatibility, result)
    return _tag_ui(result.get("taskTag") or {})


def _tag_version(compatibility: Any, tag_id: str) -> int:
    board = _query(compatibility, _CLOUD_TASKS)
    row = next(
        (item for item in board.get("taskTags") or [] if str(item.get("id") or "") == tag_id),
        None,
    )
    if not isinstance(row, Mapping):
        raise LocalRuntimeError(404, "task_tag_missing", "任务标签不存在")
    return int(row.get("version") or 1)


_CONTEXT_GENERIC_TERMS = {
    "任务", "项目", "协作", "完成", "推进", "相关", "当前", "工作",
    "黄金", "验收", "测试", "基金会", "计划", "事项",
}


def _context_terms(hint: Mapping[str, Any]) -> list[str]:
    text = "\n".join(
        str(hint.get(key) or "")
        for key in ("title", "description", "clientName", "eventLineName")
    ).casefold()
    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,24}", text):
        if token.isdigit():
            continue
        if token not in _CONTEXT_GENERIC_TERMS:
            terms.add(token)
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            for size in (2, 3, 4):
                if len(token) < size:
                    continue
                for index in range(len(token) - size + 1):
                    part = token[index : index + size]
                    if part not in _CONTEXT_GENERIC_TERMS:
                        terms.add(part)
    return sorted(terms, key=lambda value: (-len(value), value))[:64]


def _context_summary(item: Mapping[str, Any]) -> str:
    raw = str(
        item.get("summary")
        or item.get("content")
        or item.get("statement")
        or ""
    ).strip()
    return re.sub(r"\s+", " ", raw)[:360]


def _context_title(item: Mapping[str, Any]) -> str:
    return str(
        item.get("sourceDescription")
        or item.get("title")
        or item.get("sourceKind")
        or "项目知识"
    ).strip()[:180]


def _select_context_sources(
    items: list[Any],
    *,
    hint: Mapping[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """Rank a bounded set of real authority summaries for a task brief.

    This is deliberately deterministic and read-only.  It does not invent an
    Agent result: explicit member corrections rank first, then verified website
    facts and published organization summaries, with task/client keyword matches
    deciding among sources of the same authority.
    """
    terms = _context_terms(hint)
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    seen_summary: set[str] = set()
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            continue
        summary = _context_summary(raw)
        if not summary:
            continue
        summary_key = summary.casefold()
        if summary_key in seen_summary:
            continue
        seen_summary.add(summary_key)
        title = _context_title(raw)
        kind = str(raw.get("sourceKind") or raw.get("sourceType") or "").casefold()
        corpus = f"{title}\n{summary}".casefold()
        score = 0.0
        if kind in {"answer_correction", "answer_remember", "strategic_profile_clarification"}:
            score += 80
        elif "official" in kind or "website" in kind:
            score += 30
        else:
            score += 20
        if str(raw.get("verificationState") or "").casefold() == "verified":
            score += 10
        for term in terms:
            if term in corpus:
                score += min(18, 3 + len(term) * 2)
        ranked.append(
            (
                score,
                index,
                {
                    "sourceId": str(raw.get("sourceId") or ""),
                    "title": title,
                    "summary": summary,
                    "sourceKind": kind or "organization_knowledge",
                    "sourceUrl": str(raw.get("sourceUrl") or "") or None,
                    "contentHash": str(raw.get("contentHash") or ""),
                    "version": int(raw.get("version") or raw.get("sourceVersion") or 1),
                    "matchedTerms": [term for term in terms if term in corpus][:12],
                },
            )
        )
    ranked.sort(key=lambda entry: (-entry[0], entry[1], entry[2]["sourceId"]))
    selected: list[dict[str, Any]] = []
    title_counts: dict[str, int] = {}
    for _, _, item in ranked:
        title_key = str(item["title"]).casefold()
        if title_counts.get(title_key, 0) >= 2:
            continue
        selected.append(item)
        title_counts[title_key] = title_counts.get(title_key, 0) + 1
        if len(selected) >= max(1, min(limit, 8)):
            break
    return selected


def _task_specific_terms(hint: Mapping[str, Any]) -> list[str]:
    """Return task intent terms without counting the already-selected project name.

    A task that merely repeats the client name does not yet tell us which part
    of a large project matters.  Treating that name as a strong relationship
    was the reason generic Rici facts previously looked falsely targeted.
    """

    task_terms = set(
        _context_terms(
            {
                "title": hint.get("title"),
                "description": hint.get("description"),
                "clientName": "",
                "eventLineName": "",
            }
        )
    )
    project_terms = set(
        _context_terms(
            {
                "title": hint.get("clientName"),
                "description": "",
                "clientName": "",
                "eventLineName": "",
            }
        )
    )
    return sorted(task_terms - project_terms, key=lambda value: (-len(value), value))


def _relationship_is_clear(
    selected: list[Mapping[str, Any]],
    *,
    hint: Mapping[str, Any],
) -> bool:
    terms = _task_specific_terms(hint)
    if not terms:
        return False
    meaningful = [term for term in terms if not re.fullmatch(r"(?:gc)?\d+|\d{6,}", term)]
    if not meaningful:
        return False
    return any(
        any(term in f"{item.get('title', '')}\n{item.get('summary', '')}".casefold() for term in meaningful)
        for item in selected
    )


def _sentence(text: Any, *, limit: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip(" ；;。")
    return normalized[:limit]


def _deterministic_context_narrative(
    *,
    hint: Mapping[str, Any],
    selected: list[Mapping[str, Any]],
    relationship_clear: bool,
) -> str:
    """Cohesive, evidence-only fallback when the organization model is unavailable."""

    client_name = _sentence(hint.get("clientName"), limit=80) or "当前项目"
    summaries = [_sentence(item.get("summary")) for item in selected[:3]]
    summaries = [item for item in summaries if item]
    if not summaries:
        return f"{client_name}目前还没有足够的正式知识可用于梳理这项任务的前情。"
    evidence_text = "；".join(summaries)
    if relationship_clear:
        return (
            f"结合任务标题和说明，这项工作与{client_name}现有材料中的相关业务直接有关。"
            f"当前可确认的背景是：{evidence_text}。"
            "后续判断应以这些已核实信息为边界，尚无证据的内容不作推断。"
        )
    return (
        f"当前任务标题和说明尚不足以判断它具体对应{client_name}的哪一项业务，"
        f"先提供通用项目背景：{evidence_text}。"
        "补充任务对象、目标或交付物后，前情提要会进一步聚焦。"
    )


def _clean_model_context_brief(raw: Any) -> str:
    text = str(raw or "").strip()
    text = re.sub(r"^```(?:markdown|text)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*•]\s+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:1200]


def _model_context_narrative(
    compatibility: Any,
    *,
    hint: Mapping[str, Any],
    selected: list[Mapping[str, Any]],
    relationship_clear: bool,
) -> tuple[str, str]:
    evidence = [
        {
            "source": str(item.get("title") or f"依据{index + 1}"),
            "content": str(item.get("summary") or ""),
        }
        for index, item in enumerate(selected)
    ]
    task_payload = {
        "title": str(hint.get("title") or ""),
        "description": str(hint.get("description") or ""),
        "project": str(hint.get("clientName") or ""),
        "eventLine": str(hint.get("eventLineName") or ""),
        "relationshipMode": "task_specific" if relationship_clear else "general_project_context",
        "evidence": evidence,
    }
    completion = compatibility.runtime.organization_ai_completion(
        messages=[
            {
                "role": "system",
                "content": (
                    "你是益语智库的任务计划Agent。请只依据提供的项目证据，围绕任务标题和详情"
                    "梳理一段真正有助于执行者理解任务的前情提要。不要逐条罗列事实，不要复述输入，"
                    "而要说明这些背景与当前任务的关系、已有脉络和需要守住的事实边界。"
                    "若relationshipMode为general_project_context，说明任务信息不足以判断具体关系，"
                    "再自然概括通用项目背景；不得假装已经找到直接关系。"
                    "输出2至3个短段落、约120至260个中文字符，不使用标题、项目符号、建议清单，"
                    "不引入证据之外的人名、数字或结论。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(task_payload, ensure_ascii=False, sort_keys=True),
            },
        ],
        temperature=0.1,
        read_timeout_seconds=30.0,
    )
    content = _clean_model_context_brief(completion.get("content"))
    if not content:
        raise LocalRuntimeError(502, "task_context_brief_empty", "任务计划Agent没有返回有效前情提要")
    provider = dict(completion.get("provider") or {})
    return content, str(provider.get("modelName") or "organization-model")


@router.patch(r"task-tags/(?P<tag_id>[^/]+)")
def update_tag(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    tag_id = unquote(match.group("tag_id"))
    payload = {**request.body, "expectedVersion": int(request.body.get("expectedVersion") or _tag_version(compatibility, tag_id))}
    result = _command(
        compatibility, request, "PATCH",
        f"{_CLOUD_TASKS}/tags/{quote(tag_id, safe='')}", payload,
    )
    _apply(compatibility, result)
    return _tag_ui(result.get("taskTag") or {})


@router.delete(r"task-tags/(?P<tag_id>[^/]+)")
def delete_tag(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    tag_id = unquote(match.group("tag_id"))
    return _command(
        compatibility, request, "DELETE",
        f"{_CLOUD_TASKS}/tags/{quote(tag_id, safe='')}",
        {"expectedVersion": int(request.body.get("expectedVersion") or _tag_version(compatibility, tag_id))},
    )


@router.get(r"task-views")
def task_views(compatibility: Any, _: UiRequest, __: Any) -> dict[str, Any]:
    result = _query(compatibility, _CLOUD_TASKS)
    _apply(compatibility, result, replace_snapshot=True)
    views = [
        _view_ui(item)
        for item in result.get("taskViews") or []
        if str(item.get("record_kind") or "") == "view"
    ]
    return {"views": views, "presets": []}


def _build_task_context_brief(
    compatibility: Any,
    *,
    task_id: str,
    use_model: bool,
) -> dict[str, Any]:
    context = _query(
        compatibility,
        f"{_CLOUD_TASKS}/{quote(task_id, safe='')}/context",
    )
    org = list(context.get("organizationProjectKnowledge") or [])
    personal = list(context.get("personalProjectMemory") or [])
    boundary = dict(context.get("materialBoundary") or {})
    try:
        hint = _projector(compatibility).task_context_hint(task_id)
    except LocalRuntimeError:
        hint = {
            "taskId": task_id,
            "title": "",
            "description": "",
            "clientId": context.get("clientId"),
            "clientName": None,
            "eventLineId": None,
            "eventLineName": None,
        }
    selected = _select_context_sources([*org, *personal], hint=hint, limit=8)
    relationship_clear = _relationship_is_clear(selected, hint=hint)
    quality_flags: list[str] = []
    if len(selected) < 2:
        quality_flags.append("thin_context")
    if not relationship_clear and selected:
        quality_flags.append("general_context")
    generation_model = "deterministic-authority-brief-v2"
    brief = _deterministic_context_narrative(
        hint=hint,
        selected=selected,
        relationship_clear=relationship_clear,
    )
    if use_model and selected:
        try:
            brief, generation_model = _model_context_narrative(
                compatibility,
                hint=hint,
                selected=selected,
                relationship_clear=relationship_clear,
            )
        except LocalRuntimeError as exc:
            quality_flags.append(
                "model_failed_retryable" if exc.status_code >= 500 else "model_blocked"
            )
    elif selected:
        quality_flags.append("preview_only")
    now = utc_now()
    return {
        "id": f"brief-{task_id}",
        "taskId": task_id,
        "clientId": hint.get("clientId") or context.get("clientId"),
        "eventLineId": hint.get("eventLineId"),
        "brief": brief,
        "shouldDisplay": bool(brief),
        "materialPackHash": sha256_text(
            json.dumps(
                {
                    "task": hint,
                    "selectedSources": selected,
                    "organizationCount": len(org),
                    "personalCount": len(personal),
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        ),
        "usedProjectSignals": list(
            dict.fromkeys(str(item["title"]) for item in selected)
        ),
        "materialBoundary": boundary,
        "qualityFlags": quality_flags,
        "generationModel": generation_model,
        "generationPromptVersion": "gc04-context-contract-v3",
        "updatedAt": now,
        "taskPlanAgent": context.get("taskPlanAgent"),
    }


@router.get(r"tasks/(?P<task_id>[^/]+)/context-brief")
def task_context_brief(
    compatibility: Any, _: UiRequest, match: Any
) -> dict[str, Any]:
    return _build_task_context_brief(
        compatibility,
        task_id=unquote(match.group("task_id")),
        use_model=True,
    )


@router.post(r"tasks/context-briefs/batch")
def task_context_briefs_batch(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    """Bridge the renderer batch read to the same per-task strict context.

    This is deliberately a bounded read adapter: it creates no second task or
    knowledge authority and preserves the input order for the visible tasks.
    """
    briefs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_task_id in request.body.get("taskIds") or []:
        task_id = str(raw_task_id or "").strip()
        if not task_id or task_id in seen:
            continue
        seen.add(task_id)
        briefs.append(
            _build_task_context_brief(
                compatibility,
                task_id=task_id,
                use_model=False,
            )
        )
    return {"briefs": briefs}


@router.post(r"tasks/(?P<task_id>[^/]+)/agent-proposals")
def task_agent_proposal(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    task_id = unquote(match.group("task_id"))
    payload = dict(request.body)
    payload["expectedVersion"] = int(
        payload.get("expectedVersion") or _projector(compatibility).task_version(task_id)
    )
    return _command(
        compatibility,
        request,
        "POST",
        f"{_CLOUD_TASKS}/{quote(task_id, safe='')}/agent-proposals",
        payload,
    )


@router.post(r"tasks/bulk/preflight")
def bulk_preflight(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    return _command(
        compatibility,
        request,
        "POST",
        "/api/v2/domain/task-bulk/preflight",
        request.body,
    )


@router.post(r"tasks/bulk/(?P<bulk_id>[^/]+)/commit")
def bulk_commit(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    bulk_id = unquote(match.group("bulk_id"))
    result = _command(
        compatibility,
        request,
        "POST",
        f"/api/v2/domain/task-bulk/{quote(bulk_id, safe='')}/commit",
        request.body,
    )
    _apply(compatibility, result)
    return result


__all__ = ["router"]
