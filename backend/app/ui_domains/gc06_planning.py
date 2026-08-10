"""Detached GC-06 UI adapter; the integration thread owns registry wiring."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import gettempdir
from typing import Any, Mapping
from urllib.parse import quote, unquote

from ..gc04_tasks_local import LocalGC04TaskProjection
from ..gc06_planning_local import LocalGC06PlanningProjection
from ..gc07_sources import GC07LocalProjectMaterialsRepository
from ..project_materials_local import LocalProjectMaterialsRepository
from ..runtime import LocalRuntimeError
from .gc04_tasks import _task_ui
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("gc06_planning", pin_workspace=True)
_CLOUD_ROOT = "/api/v2/gc06"


def _planning_projector(compatibility: Any) -> LocalGC06PlanningProjection:
    return LocalGC06PlanningProjection(compatibility.runtime)


def _task_projector(compatibility: Any) -> LocalGC04TaskProjection:
    return LocalGC04TaskProjection(compatibility.runtime)


def _material_store(compatibility: Any) -> GC07LocalProjectMaterialsRepository:
    return GC07LocalProjectMaterialsRepository(compatibility.runtime)


def _event_line_ui(row: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the strict event-line authority into the retained renderer shape."""

    lifecycle = str(row.get("lifecycleState") or "active")
    status = "archived" if lifecycle == "archived" else lifecycle
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "kind": str(row.get("kind") or "project_line"),
        "status": status,
        "visibilityScope": str(row.get("visibilityScope") or "project_public"),
        "summary": str(row.get("background") or ""),
        "intent": str(row.get("goal") or ""),
        "evidenceCount": 0,
        "taskCount": int(row.get("taskCount") or 0),
        "attachmentCount": 0,
        "activityCount": int(row.get("activityCount") or 0),
        "ownerId": row.get("createdByMembershipId"),
        "ownerName": None,
        "createdByUserId": row.get("createdByMembershipId"),
        "createdByName": None,
        "primaryClientId": row.get("clientId"),
        "primaryClientName": None,
        "primaryDepartmentId": None,
        "primaryDepartmentName": None,
        "participantIds": [],
        "materialRequirements": [],
        "closedAt": row.get("updatedAt") if lifecycle == "archived" else None,
        "closedByUserId": None,
        "syncStatus": "synced",
        "cloudId": row.get("id"),
        "pendingSyncAction": None,
        "lastSyncError": None,
        "readinessLevel": "incomplete",
        "readinessMissingItems": [],
        "version": int(row.get("version") or 1),
        "viewerCapabilities": {
            "canView": True,
            "canContribute": True,
            "canManageStructure": True,
            "canAssignOwner": False,
            "canArchive": True,
            "canReparentProject": True,
            "canAddParticipants": False,
            "canManageParticipants": False,
            "canSetMilestone": True,
        },
        "createdAt": str(row.get("createdAt") or ""),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def _event_line_detail_ui(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eventLine": _event_line_ui(payload.get("eventLine") or {}),
        "tasks": [
            _task_ui(item)
            for item in payload.get("tasks") or []
            if isinstance(item, Mapping)
        ],
        "activities": [
            {
                "id": str(item.get("id") or ""),
                "eventLineId": str(item.get("eventLineId") or ""),
                "sourceType": (
                    "task_activity"
                    if str(item.get("sourceType") or "") == "task"
                    else str(item.get("sourceType") or "manual_note")
                ),
                "sourceId": str(item.get("sourceId") or ""),
                "happenedAt": str(item.get("happenedAt") or ""),
                "actorId": None,
                "actorName": None,
                "title": str(item.get("title") or ""),
                "summary": str(item.get("summary") or ""),
                "isKey": str(item.get("title") or "").startswith("里程碑任务："),
                "keySource": (
                    "human"
                    if str(item.get("title") or "").startswith("里程碑任务：")
                    else ""
                ),
                "associationStatus": str(
                    item.get("associationState") or "confirmed"
                ),
                "includeInNarrative": bool(item.get("includeInNarrative")),
            }
            for item in payload.get("activities") or []
            if isinstance(item, Mapping)
        ],
        "memorySnapshot": None,
        "predictionReadiness": None,
        "clarificationNeeds": [],
    }


def _event_line_report_attachments(
    compatibility: Any,
    payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    event_line = payload.get("eventLine") or {}
    client_id = str(event_line.get("clientId") or "")
    if not client_id or not hasattr(compatibility.runtime, "database_path"):
        return []
    documents = {
        str(item.get("id") or ""): item
        for item in _material_store(compatibility).documents(client_id)
        if str(item.get("id") or "")
    }
    result: list[dict[str, Any]] = []
    for activity in payload.get("activities") or []:
        if not isinstance(activity, Mapping):
            continue
        source_id = str(activity.get("sourceId") or "")
        document = documents.get(source_id)
        if document is None:
            continue
        result.append({
            "id": source_id,
            "taskId": "",
            "documentId": source_id,
            "sourceKind": "event_line_attachment",
            "title": str(activity.get("title") or document.get("title") or "事件线材料"),
            "fileName": str(document.get("title") or ""),
            "kind": str(document.get("kind") or "file"),
            "mimeType": None,
            "sizeBytes": 0,
            "downloadUrl": "",
            "openUrl": None,
            "localPath": document.get("path"),
            "actorName": None,
            "purpose": str(activity.get("summary") or ""),
            "createdAt": str(activity.get("happenedAt") or document.get("importedAt") or ""),
            "parseStatus": document.get("parseStatus") or "uploaded",
            "parseError": document.get("processingMessage"),
            "parseJobStatus": document.get("parseStatus"),
            "parseUpdatedAt": document.get("processedAt"),
            "parsedPreview": document.get("parsedPreview") or "",
            "chunkCount": 0,
            "sectionCount": 0,
        })
    return result


def _event_line_timeline_nodes(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for item in payload.get("activities") or []:
        if not isinstance(item, Mapping):
            continue
        nodes.append({
            "id": str(item.get("id") or ""),
            "kind": "project_review" if item.get("sourceType") == "weekly_review" else "system_trace",
            "title": str(item.get("title") or "事件线进展"),
            "time": str(item.get("happenedAt") or ""),
            "summary": str(item.get("summary") or ""),
            "sourceTaskIds": [str(item.get("sourceId"))] if item.get("sourceType") == "task" else [],
            "sourceActivityIds": [str(item.get("id") or "")],
            "attachments": [],
            "includeInReport": bool(item.get("includeInNarrative")),
            "evidenceSummary": str(item.get("summary") or item.get("title") or ""),
            "warnings": [],
            "tags": [str(item.get("sourceType") or "activity")],
            "actorName": None,
            "ownerName": None,
        })
    for item in payload.get("tasks") or []:
        if not isinstance(item, Mapping):
            continue
        happened_at = str(item.get("completed_at") or item.get("updated_at") or item.get("created_at") or "")
        nodes.append({
            "id": f"task:{str(item.get('id') or '')}",
            "kind": "continuing_task",
            "title": str(item.get("title") or "任务进展"),
            "time": happened_at,
            "summary": str(item.get("description") or ""),
            "sourceTaskIds": [str(item.get("id") or "")],
            "sourceActivityIds": [],
            "attachments": [],
            "includeInReport": True,
            "evidenceSummary": "正式任务事实",
            "warnings": [],
            "tags": ["task"],
            "actorName": None,
            "ownerName": None,
        })
    return sorted(nodes, key=lambda item: (item.get("time") or "", item.get("id") or ""))


def _event_line_narrative(payload: Mapping[str, Any]) -> dict[str, Any]:
    event_line = payload.get("eventLine") if isinstance(payload.get("eventLine"), Mapping) else {}
    nodes = _event_line_timeline_nodes(payload)
    narrative_nodes = [{
        "id": str(item.get("id") or ""),
        "time": str(item.get("time") or ""),
        "title": str(item.get("title") or ""),
        "narrative": str(item.get("summary") or item.get("evidenceSummary") or ""),
        "confidence": "high",
        "linkedTaskIds": list(item.get("sourceTaskIds") or []),
        "linkedActivityIds": list(item.get("sourceActivityIds") or []),
        "linkedAttachmentIds": [],
        "evidenceSummary": str(item.get("evidenceSummary") or ""),
        "evidenceGaps": [],
    } for item in nodes]
    missing = [] if nodes else ["尚无任务或活动事实"]
    return {
        "eventLineId": str(event_line.get("id") or ""),
        "rev": int(event_line.get("version") or 1),
        "headline": str(event_line.get("name") or "事件线"),
        "opening": str(event_line.get("background") or event_line.get("goal") or ""),
        "closing": "已按当前正式任务和活动整理" if nodes else "尚无可整理的进展事实",
        "nodes": narrative_nodes,
        "overallConfidence": 1 if nodes else 0,
        "generator": "strict_deterministic_event_line_v1",
        "modelName": "",
        "updatedAt": str(event_line.get("updatedAt") or ""),
        "outputKind": "formal_mainline",
        "sourceSetId": None,
        "eventLineVersion": int(event_line.get("version") or 1),
        "milestoneTaskIds": [],
        "isStale": False,
        "formalReady": bool(nodes),
        "missingRequirements": missing,
        "availabilityStatus": "ready" if nodes else "blocked",
        "availabilityReason": "" if nodes else missing[0],
        "staleReasons": [],
    }


def _week_label(value: str) -> str:
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return ""
    year, week, _ = parsed.isocalendar()
    return f"{year}-W{week:02d}"


def _week_bounds(label: str) -> tuple[date, date] | None:
    try:
        year_text, week_text = label.split("-W", 1)
        start = date.fromisocalendar(int(year_text), int(week_text), 1)
    except (TypeError, ValueError):
        return None
    iso_year, iso_week, _ = start.isocalendar()
    return start, date.fromisocalendar(iso_year, iso_week, 7)


def _review_content(review: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    versions = [
        item
        for item in review.get("versions") or []
        if isinstance(item, Mapping)
    ]
    selected_id = review.get("currentSubmittedVersionId") or review.get("currentDraftVersionId")
    selected = next(
        (item for item in versions if item.get("id") == selected_id),
        versions[-1] if versions else {},
    )
    content = selected.get("content") if isinstance(selected.get("content"), Mapping) else {}
    return selected, content


def _merge_review_task_entries(
    current_content: Mapping[str, Any],
    incoming_entries: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    merged = {
        str(item.get("taskId") or ""): dict(item)
        for item in current_content.get("taskEntries") or []
        if isinstance(item, Mapping) and str(item.get("taskId") or "")
    }
    for item in incoming_entries:
        task_id = str(item.get("taskId") or "")
        if task_id:
            merged[task_id] = dict(item)
    return list(merged.values())


def _empty_review_note(*, done: bool) -> dict[str, Any]:
    return {
        "reflection": "",
        "lightweightTag": "",
        "planCommitment": "",
        "progress": "",
        "completionStatus": "done_on_time" if done else "in_progress",
        "departmentPlanId": None,
        "departmentPlanAlignment": "unknown",
        "organizationPlanId": None,
        "organizationPlanAlignment": "unknown",
        "successReason": "",
        "successExperience": "",
        "blockerReason": "",
        "failureInsight": "",
        "supportNeeded": "",
        "nextAction": "",
    }


def _basic_review_dashboard(
    *,
    cycles: list[Mapping[str, Any]],
    reviews: list[Mapping[str, Any]],
    task_rows: list[Mapping[str, Any]],
    membership_id: str,
    user_name: str,
    requested_week: str,
) -> dict[str, Any]:
    week = requested_week or next(
        (_week_label(str(item.get("periodStart") or "")) for item in cycles if item.get("periodStart")),
        "",
    ) or _week_label(date.today().isoformat())
    bounds = _week_bounds(week)
    matching_cycles = [
        item
        for item in cycles
        if _week_label(str(item.get("periodStart") or "")) == week
    ]
    cycle_ids = {str(item.get("id") or "") for item in matching_cycles}
    current = next(
        (
            item
            for item in reviews
            if str(item.get("membershipId") or "") == membership_id
            and str(item.get("planningCycleId") or "") in cycle_ids
        ),
        None,
    )
    current_review = None
    current_content: Mapping[str, Any] = {}
    if current is not None:
        version, content = _review_content(current)
        current_content = content
        current_review = {
            "id": str(current.get("id") or ""),
            "userId": membership_id,
            "userName": user_name,
            "weekLabel": week,
            "workProgress": str(content.get("workProgress") or content.get("summary") or ""),
            "workBlocker": str(content.get("workBlocker") or ""),
            "blockerType": str(content.get("blockerType") or ""),
            "workDirection": str(content.get("workDirection") or ""),
            "nextWeekFocus": str(content.get("nextWeekFocus") or ""),
            "supportNeeded": str(content.get("supportNeeded") or ""),
            "relatedPlanIds": [str(current.get("planningCycleId") or "")],
            "workFreeNote": str(content.get("summary") or ""),
            "personalGrowthNote": "",
            "personalPrivateNote": "",
            "personalVisibility": "self",
            "submittedAt": str(version.get("submittedAt") or ""),
            "createdAt": str(current.get("createdAt") or ""),
            "updatedAt": str(current.get("updatedAt") or ""),
        }
    work_items: list[dict[str, Any]] = []
    personal_items: list[dict[str, Any]] = []
    saved_task_entries = {
        str(item.get("taskId") or ""): item
        for item in current_content.get("taskEntries") or []
        if isinstance(item, Mapping) and str(item.get("taskId") or "")
    }
    for raw in task_rows:
        collaborators = [
            item for item in raw.get("collaborators") or [] if isinstance(item, Mapping)
        ]
        if not any(
            str(item.get("subject_membership_id") or "") == membership_id
            and str(item.get("inbox_status") or "accepted") != "rejected"
            for item in collaborators
        ):
            continue
        task = _task_ui(raw)
        timestamp = str(
            task.get("dueDate")
            or task.get("scheduledStartAt")
            or task.get("createdAt")
            or ""
        )
        try:
            task_date = date.fromisoformat(timestamp[:10])
        except ValueError:
            continue
        if bounds is not None and not (bounds[0] <= task_date <= bounds[1]):
            continue
        personal = str(raw.get("visibility_scope") or "") == "self"
        saved_entry = saved_task_entries.get(str(task["id"])) or {}
        saved_structured = (
            saved_entry.get("structuredNote")
            if isinstance(saved_entry.get("structuredNote"), Mapping)
            else None
        )
        entry = {
            "id": f"review-task:{week}:{task['id']}",
            "reviewId": current_review["id"] if current_review else None,
            "taskId": task["id"],
            "weekLabel": week,
            "contentDomain": str(saved_entry.get("contentDomain") or ("personal" if personal else "work")),
            "note": str(saved_entry.get("note") or ""),
            "structuredNote": dict(saved_structured) if saved_structured else _empty_review_note(done=task.get("status") == "done"),
            "reviewedAt": current.get("updatedAt") if current else None,
            "taskSnapshot": {
                "title": task.get("title") or "",
                "status": task.get("status") or "todo",
                "startDate": task.get("startDate"),
                "dueDate": task.get("dueDate"),
                "deadlineAt": task.get("deadlineAt"),
                "scheduledStartAt": task.get("scheduledStartAt"),
                "scheduledEndAt": task.get("scheduledEndAt"),
                "completedAt": task.get("completedAt"),
                "createdAt": task.get("createdAt") or "",
                "ownerId": task.get("ownerId"),
                "ownerName": task.get("ownerName"),
                "clientId": task.get("clientId"),
                "clientName": task.get("clientName"),
                "eventLineId": task.get("eventLineId"),
                "eventLineName": task.get("eventLineName"),
                "tags": [],
                "listName": task.get("listName") or "",
                "listColor": task.get("listColor") or "#5B7BFE",
            },
        }
        (personal_items if personal else work_items).append(entry)
    plans = [
        {
            "id": str(item.get("id") or ""),
            "level": "director" if item.get("recordKind") == "department_plan" else "ceo",
            "title": str(item.get("title") or ""),
            "summary": str(item.get("summary") or ""),
            "status": str(item.get("status") or "draft"),
            "ownerUserId": item.get("ownerMembershipId"),
            "ownerName": None,
            "ownerUnitId": item.get("departmentId"),
            "startsAt": item.get("periodStart"),
            "endsAt": item.get("periodEnd"),
        }
        for item in matching_cycles
    ]
    return {
        "weekLabel": week,
        "resolvedWeekLabel": week,
        "currentReview": current_review,
        "workItems": work_items,
        "personalItems": personal_items,
        "availablePerspectives": [{"key": "mine", "label": "我的视角"}],
        "activePerspective": "mine",
        "activeDepartmentId": None,
        "activeDepartmentName": None,
        "workAnalysis": None,
        "personalAnalysis": None,
        "weeklyMainlineCards": None,
        "weeklyEventReviewCards": None,
        "weeklyOverviewGenerationStatus": {
            "weekLabel": week,
            "perspective": "mine",
            "departmentId": None,
            "viewerUserId": membership_id,
            "status": "idle",
            "startedAt": None,
            "generatedAt": None,
            "sourceCounts": {
                "reviews": 1 if current_review else 0,
                "workItems": len(work_items),
                "personalItems": len(personal_items),
            },
        },
        "selfReport": None,
        "workSignalCard": None,
        "personalGrowthCard": None,
        "teamReport": None,
        "orgReport": None,
        "executiveOrgReport": None,
        "departmentReports": [],
        "agentDepartmentDigests": [],
        "agentDepartmentPlans": [],
        "simulationBundle": None,
        "plans": plans,
    }


def _cycle_for_week(
    cycles: list[Mapping[str, Any]], week: str
) -> Mapping[str, Any] | None:
    bounds = _week_bounds(week)
    if bounds is None:
        return None
    for cycle in cycles:
        try:
            start = date.fromisoformat(str(cycle.get("periodStart") or "")[:10])
            end = date.fromisoformat(str(cycle.get("periodEnd") or "")[:10])
        except ValueError:
            continue
        if start <= bounds[1] and end >= bounds[0]:
            return cycle
    return None


def _retained_review_sources(
    compatibility: Any,
    *,
    membership_id: str,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], dict[str, Any]]:
    cycles = compatibility.runtime.cloud_query(f"{_CLOUD_ROOT}/planning-cycles")
    reviews = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/weekly-reviews",
        query={"membershipId": membership_id},
    )
    task_result = compatibility.runtime.cloud_query("/api/v2/domain/tasks")
    cycle_rows = [item for item in cycles or [] if isinstance(item, Mapping)]
    review_rows = [item for item in reviews or [] if isinstance(item, Mapping)]
    _planning_projector(compatibility).apply_planning_cycles(cycle_rows)
    _planning_projector(compatibility).apply_weekly_reviews(review_rows)
    projection = task_result.get("projection") if isinstance(task_result, Mapping) else None
    if isinstance(projection, Mapping):
        _task_projector(compatibility).apply(projection, replace_snapshot=True)
    return cycle_rows, review_rows, dict(task_result)


def _retained_dashboard(
    compatibility: Any,
    *,
    week: str,
    perspective: str = "mine",
    department_id: str | None = None,
) -> dict[str, Any]:
    context = compatibility.runtime._current_context(require_ready=True)  # noqa: SLF001
    membership_id = str(context.membership_id)
    cycles, reviews, task_result = _retained_review_sources(
        compatibility,
        membership_id=membership_id,
    )
    auth_user = (
        compatibility.auth_state().get("user") or {}
        if hasattr(compatibility, "auth_state")
        else {}
    )
    dashboard = _basic_review_dashboard(
        cycles=cycles,
        reviews=reviews,
        task_rows=[
            item
            for item in task_result.get("tasks") or []
            if isinstance(item, Mapping)
        ],
        membership_id=membership_id,
        user_name=str(
            auth_user.get("fullName")
            or auth_user.get("displayName")
            or auth_user.get("name")
            or "当前成员"
        ),
        requested_week=week,
    )
    primary_role = str(auth_user.get("primaryRole") or auth_user.get("primary_role") or "")
    own_department_id = str(
        auth_user.get("departmentId") or auth_user.get("department_id") or ""
    )
    own_department_name = str(
        auth_user.get("departmentName") or auth_user.get("department_name") or ""
    )
    is_department_lead = bool(
        auth_user.get("isDepartmentLead") or auth_user.get("is_department_lead")
    )
    options: list[dict[str, Any]] = []
    if primary_role == "admin":
        options.append({"key": "organization", "label": "组织视角"})
    if primary_role == "admin" or is_department_lead:
        options.append(
            {
                "key": "department",
                "label": own_department_name or "部门视角",
                "departmentId": department_id or own_department_id or None,
                "departmentName": own_department_name or None,
            }
        )
    options.append({"key": "mine", "label": "我的视角"})
    allowed = {str(item["key"]) for item in options}
    active = perspective if perspective in allowed else "mine"
    dashboard["availablePerspectives"] = options
    dashboard["activePerspective"] = active
    dashboard["activeDepartmentId"] = (
        department_id or own_department_id or None if active == "department" else None
    )
    dashboard["activeDepartmentName"] = (
        own_department_name or None if active == "department" else None
    )
    dashboard["activeDepartmentLeaderName"] = (
        str(
            auth_user.get("fullName")
            or auth_user.get("displayName")
            or auth_user.get("name")
            or ""
        ).strip() or None
        if active == "department" and is_department_lead
        else None
    )
    generation = dict(dashboard.get("weeklyOverviewGenerationStatus") or {})
    generation["perspective"] = active
    generation["departmentId"] = dashboard["activeDepartmentId"]
    saved_overview = _planning_projector(compatibility).load_weekly_overview(
        membership_id=str(generation.get("viewerUserId") or ""),
        week_label=str(dashboard.get("weekLabel") or ""),
        perspective=active,
        department_id=dashboard["activeDepartmentId"],
    )
    if saved_overview:
        dashboard["weeklyMainlineCards"] = saved_overview.get("cards")
        generation.update(dict(saved_overview.get("status") or {}))
    dashboard["weeklyOverviewGenerationStatus"] = generation
    return dashboard


def _query(compatibility: Any, path: str, request: UiRequest) -> Any:
    return compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/{path}",
        query=request.query,
    )


def _command(
    compatibility: Any,
    request: UiRequest,
    method: str,
    path: str,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        method,
        f"{_CLOUD_ROOT}/{path}",
        payload=request.body,
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )


@router.get(r"gc06/event-lines")
def list_event_lines(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _query(compatibility, "event-lines", request)
    _planning_projector(compatibility).apply_event_lines(result)
    return result


@router.post(r"gc06/event-lines")
def create_event_line(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _command(compatibility, request, "POST", "event-lines")
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return result


@router.get(r"gc06/event-lines/(?P<event_line_id>[^/]+)")
def event_line_detail(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _query(
        compatibility,
        f"event-lines/{match.group('event_line_id')}",
        request,
    )
    projector = _planning_projector(compatibility)
    projector.apply_event_lines([result["eventLine"]])
    projector.apply_event_activities(result.get("activities") or [])
    return result


@router.patch(r"gc06/event-lines/(?P<event_line_id>[^/]+)")
def update_event_line(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "PATCH",
        f"event-lines/{match.group('event_line_id')}",
    )
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return result


@router.post(
    r"gc06/event-lines/(?P<event_line_id>[^/]+)/(?P<transition>archive|reopen|delete)"
)
def transition_event_line(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "POST",
        f"event-lines/{match.group('event_line_id')}/{match.group('transition')}",
    )
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return result


@router.post(r"gc06/event-lines/(?P<event_line_id>[^/]+)/activities")
def record_activity(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "POST",
        f"event-lines/{match.group('event_line_id')}/activities",
    )
    _planning_projector(compatibility).apply_event_activities([result["activity"]])
    return result


@router.post(
    r"gc06/event-lines/(?P<event_line_id>[^/]+)/tasks/(?P<task_id>[^/]+)"
)
def attach_task(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "POST",
        f"event-lines/{match.group('event_line_id')}/tasks/{match.group('task_id')}",
    )
    task_receipt = result.get("taskCommandReceipt") or {}
    projection = task_receipt.get("projection") if isinstance(task_receipt, Mapping) else None
    if isinstance(projection, Mapping):
        _task_projector(compatibility).apply(projection)
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return result


@router.get(r"gc06/planning-cycles")
def list_planning_cycles(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _query(compatibility, "planning-cycles", request)
    projector = _planning_projector(compatibility)
    if not result:
        # 冷启动时云会话可能仍在恢复。空回包不能把租约内的严格投影伪装成
        # “组织没有计划”；显示当前 sandbox 最后确认投影，后续成功查询再覆盖。
        return projector.list_planning_cycles()
    projector.apply_planning_cycles(result)
    return result


@router.post(r"gc06/planning-cycles")
def create_planning_cycle(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _command(compatibility, request, "POST", "planning-cycles")
    _planning_projector(compatibility).apply_planning_cycles([result["planningCycle"]])
    return result


@router.patch(r"gc06/planning-cycles/(?P<planning_cycle_id>[^/]+)")
def update_planning_cycle(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "PATCH",
        f"planning-cycles/{match.group('planning_cycle_id')}",
    )
    _planning_projector(compatibility).apply_planning_cycles([result["planningCycle"]])
    return result


@router.get(r"gc06/weekly-reviews")
def list_weekly_reviews(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _query(compatibility, "weekly-reviews", request)
    _planning_projector(compatibility).apply_weekly_reviews(result)
    return result


@router.post(r"gc06/weekly-reviews/draft")
def save_weekly_review_draft(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _command(compatibility, request, "POST", "weekly-reviews/draft")
    _planning_projector(compatibility).apply_weekly_reviews([result["weeklyReview"]])
    return result


@router.post(
    r"gc06/weekly-reviews/(?P<weekly_review_id>[^/]+)/"
    r"(?P<transition>submit|return|reopen)"
)
def transition_weekly_review(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> Any:
    result = _command(
        compatibility,
        request,
        "POST",
        "weekly-reviews/"
        f"{match.group('weekly_review_id')}/{match.group('transition')}",
    )
    _planning_projector(compatibility).apply_weekly_reviews([result["weeklyReview"]])
    return result


@router.get(r"gc06/decision-actions")
def list_decision_actions(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _query(compatibility, "decision-actions", request)
    projector = _planning_projector(compatibility)
    if not result:
        return projector.list_decision_actions()
    projector.apply_decision_actions(result)
    return result


@router.post(r"gc06/decision-actions")
def create_decision_action(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _command(compatibility, request, "POST", "decision-actions")
    _planning_projector(compatibility).apply_decision_actions([result["decisionAction"]])
    return result


@router.patch(r"gc06/decision-actions/(?P<action_id>[^/]+)")
def update_decision_action(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "PATCH",
        f"decision-actions/{match.group('action_id')}",
    )
    _planning_projector(compatibility).apply_decision_actions([result["decisionAction"]])
    return result


@router.post(r"gc06/decision-actions/(?P<action_id>[^/]+)/primary-task")
def convert_action_to_task(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "POST",
        f"decision-actions/{match.group('action_id')}/primary-task",
    )
    task_receipt = result.get("taskCommandReceipt") or {}
    projection = task_receipt.get("projection") if isinstance(task_receipt, Mapping) else None
    if isinstance(projection, Mapping):
        _task_projector(compatibility).apply(projection)
    _planning_projector(compatibility).apply_decision_actions([result["decisionAction"]])
    return result


@router.get(r"gc06/meetings")
def list_meetings(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _query(compatibility, "meetings", request)
    _planning_projector(compatibility).apply_meetings(result)
    return result


@router.post(r"gc06/meetings")
def create_meeting(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _command(compatibility, request, "POST", "meetings")
    _planning_projector(compatibility).apply_meetings([result["meeting"]])
    return result


@router.patch(r"gc06/meetings/(?P<meeting_id>[^/]+)")
def update_meeting(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "PATCH",
        f"meetings/{match.group('meeting_id')}",
    )
    _planning_projector(compatibility).apply_meetings([result["meeting"]])
    return result


@router.post(r"gc06/meetings/(?P<meeting_id>[^/]+)/collaboration/(?P<action>accept|reject)")
def transition_meeting_collaboration(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "POST",
        f"meetings/{match.group('meeting_id')}/collaboration/{match.group('action')}",
    )
    _planning_projector(compatibility).apply_meetings([result["meeting"]])
    return result


@router.get(r"gc06/calendar")
def list_calendar(compatibility: Any, request: UiRequest, _: Any) -> Any:
    result = _query(compatibility, "calendar", request)
    _planning_projector(compatibility).apply_calendar(result)
    return result


# Retained ReviewDashboard read surface. It composes only GC-06 planning/review
# authority and the GC-04 task authority; no legacy workflow or AI analysis.
@router.get(r"reviews")
def basic_review_dashboard(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _retained_dashboard(
        compatibility,
        week=str(request.query.get("weekLabel") or ""),
        perspective=str(request.query.get("perspective") or "mine"),
        department_id=str(request.query.get("departmentId") or "") or None,
    )


@router.get(r"reviews/dashboard/drill-target")
def review_dashboard_drill_target(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    target_type = str(request.query.get("targetType") or "").strip()
    target_id = str(request.query.get("targetId") or "").strip()
    if not target_type or not target_id:
        raise LocalRuntimeError(422, "review_drill_target_required", "缺少下钻目标")
    raw_filters = str(request.query.get("targetFilters") or "").strip()
    try:
        filters = json.loads(raw_filters) if raw_filters else {}
    except json.JSONDecodeError as exc:
        raise LocalRuntimeError(422, "review_drill_filters_invalid", "下钻筛选条件无效") from exc
    if not isinstance(filters, Mapping):
        filters = {}
    board = compatibility.runtime.cloud_query("/api/v2/domain/tasks")
    tasks = [
        _task_ui(compatibility, item)
        for item in board.get("tasks") or []
        if isinstance(item, Mapping)
    ]
    meetings = compatibility.runtime.cloud_query("/api/v2/gc06/meetings")
    if not isinstance(meetings, list):
        meetings = meetings.get("meetings") or []
    event_detail = None
    if target_type == "event_line":
        event_detail = _strict_event_line_detail(compatibility, target_id, request)
        tasks = [item for item in tasks if str(item.get("eventLineId") or "") == target_id]
        meetings = [item for item in meetings if str(item.get("eventLineId") or "") == target_id]
    elif target_type in {"client", "project"}:
        tasks = [item for item in tasks if str(item.get("clientId") or "") == target_id]
        meetings = [item for item in meetings if str(item.get("clientId") or "") == target_id]
    elif target_type == "task":
        tasks = [item for item in tasks if str(item.get("id") or "") == target_id]
        meetings = []
    elif target_type == "task_view":
        status = str(filters.get("status") or "").strip()
        client_id = str(filters.get("clientId") or "").strip()
        if status:
            tasks = [item for item in tasks if str(item.get("status") or "") == status]
        if client_id:
            tasks = [item for item in tasks if str(item.get("clientId") or "") == client_id]
        meetings = []
    else:
        tasks = [item for item in tasks if str(item.get("id") or "") == target_id]
        meetings = [item for item in meetings if str(item.get("id") or "") == target_id]
    attachments = [
        dict(attachment)
        for item in tasks
        for attachment in item.get("attachments") or []
        if isinstance(attachment, Mapping)
    ]
    return {
        "target": {
            "targetType": target_type,
            "targetId": target_id,
            "targetLabel": str(request.query.get("targetLabel") or ""),
            "targetFilters": dict(filters),
        },
        "eventLineDetail": event_detail,
        "eventLineMemory": None,
        "tasks": tasks,
        "meetings": [dict(item) for item in meetings if isinstance(item, Mapping)],
        "supportRequests": [],
        "attachments": attachments,
    }


def _save_retained_review(
    compatibility: Any,
    request: UiRequest,
    *,
    submit: bool,
) -> dict[str, Any]:
    context = compatibility.runtime._current_context(require_ready=True)  # noqa: SLF001
    membership_id = str(context.membership_id)
    week = str(request.body.get("weekLabel") or "") or _week_label(
        date.today().isoformat()
    )
    cycles, reviews, task_result = _retained_review_sources(
        compatibility,
        membership_id=membership_id,
    )
    cycle = _cycle_for_week(cycles, week)
    if cycle is None:
        raise LocalRuntimeError(
            422,
            "weekly_review_planning_cycle_required",
            "本周尚未建立组织或部门计划周期，请先由负责人建立计划后再提交复盘",
        )
    cycle_id = str(cycle.get("id") or "")
    current = next(
        (
            item
            for item in reviews
            if str(item.get("planningCycleId") or "") == cycle_id
            and str(item.get("membershipId") or "") == membership_id
        ),
        None,
    )
    task_versions = {
        str(item.get("id") or ""): max(1, int(item.get("version") or 1))
        for item in task_result.get("tasks") or []
        if isinstance(item, Mapping)
    }
    incoming_task_entries = [
        dict(item)
        for item in request.body.get("taskEntries") or []
        if isinstance(item, Mapping) and str(item.get("taskId") or "")
    ]
    # The retained dashboard posts only rows edited in the current action.
    # Merge them with the current authoritative version; otherwise saving a
    # second row silently erases the first row's review note.
    current_content: Mapping[str, Any] = {}
    if current is not None:
        _, current_content = _review_content(current)
    task_entries = _merge_review_task_entries(
        current_content,
        incoming_task_entries,
    )
    evidence = [
        {
            "sourceObjectKind": "task",
            "sourceObjectId": str(item["taskId"]),
            "sourceVersion": task_versions.get(str(item["taskId"]), 1),
        }
        for item in task_entries
        if str(item.get("taskId") or "") in task_versions
    ]
    draft_result = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/weekly-reviews/draft",
        payload={
            "planningCycleId": cycle_id,
            "membershipId": membership_id,
            "expectedVersion": int(current.get("version") or 1) if current else 0,
            "content": {
                "weekLabel": week,
                "taskEntries": task_entries,
                "workFreeNote": str(
                    request.body.get("workFreeNote")
                    if "workFreeNote" in request.body
                    else current_content.get("workFreeNote")
                    or current_content.get("summary")
                    or ""
                ),
                "personalGrowthNote": str(
                    request.body.get("personalGrowthNote")
                    if "personalGrowthNote" in request.body
                    else current_content.get("personalGrowthNote") or ""
                ),
                "personalPrivateNote": str(
                    request.body.get("personalPrivateNote")
                    if "personalPrivateNote" in request.body
                    else current_content.get("personalPrivateNote") or ""
                ),
                "summary": str(
                    request.body.get("workFreeNote")
                    if "workFreeNote" in request.body
                    else current_content.get("summary") or ""
                ),
            },
            "evidence": evidence,
        },
        idempotency_key=f"{request.idempotency_key}:draft",
        refresh_business=False,
    )
    review = draft_result.get("weeklyReview") or {}
    if submit:
        compatibility.runtime.cloud_command(
            "POST",
            f"{_CLOUD_ROOT}/weekly-reviews/{quote(str(review.get('id') or ''), safe='')}/submit",
            payload={"expectedVersion": int(review.get("version") or 1)},
            idempotency_key=f"{request.idempotency_key}:submit",
            refresh_business=False,
        )
    return _retained_dashboard(compatibility, week=week)


@router.post(r"reviews/weekly/draft")
def save_retained_review_draft(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _save_retained_review(compatibility, request, submit=False)


@router.post(r"reviews/weekly")
def submit_retained_review(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _save_retained_review(compatibility, request, submit=True)


@router.get(r"reviews/history")
def retained_review_history(
    compatibility: Any,
    _: UiRequest,
    __: Any,
) -> dict[str, Any]:
    context = compatibility.runtime._current_context(require_ready=True)  # noqa: SLF001
    membership_id = str(context.membership_id)
    cycles, reviews, _ = _retained_review_sources(
        compatibility,
        membership_id=membership_id,
    )
    cycles_by_id = {str(item.get("id") or ""): item for item in cycles}
    items = []
    for review in reviews:
        cycle = cycles_by_id.get(str(review.get("planningCycleId") or "")) or {}
        _, content = _review_content(review)
        entries = [item for item in content.get("taskEntries") or [] if isinstance(item, Mapping)]
        items.append({
            "weekLabel": str(content.get("weekLabel") or _week_label(str(cycle.get("periodStart") or ""))),
            "submittedAt": str(review.get("updatedAt") or ""),
            "workItemCount": sum(str(item.get("contentDomain") or "work") == "work" for item in entries),
            "personalItemCount": sum(str(item.get("contentDomain") or "") == "personal" for item in entries),
            "version": int(review.get("version") or 1),
        })
    return {"items": items}


@router.get(r"reviews/clients-pulse")
def retained_clients_pulse(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    """Serve the retained pulse card from strict 88-table authorities."""

    return _query(compatibility, "clients-pulse", request)


def _review_refresh_status(compatibility: Any, request: UiRequest) -> dict[str, Any]:
    perspective = str(request.body.get("perspective") or request.query.get("perspective") or "mine")
    department_id = str(request.body.get("departmentId") or request.query.get("departmentId") or "") or None
    dashboard = _retained_dashboard(
        compatibility,
        week=str(request.body.get("weekLabel") or request.query.get("weekLabel") or ""),
        perspective=perspective,
        department_id=department_id,
    )
    return dict(dashboard["weeklyOverviewGenerationStatus"])


def _weekly_agent_json(raw: str) -> Mapping[str, Any]:
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LocalRuntimeError(502, "weekly_review_agent_json_invalid", "自动生成的复盘草稿格式无效，可以重试") from exc
    if not isinstance(value, Mapping):
        raise LocalRuntimeError(502, "weekly_review_agent_json_invalid", "自动生成的复盘草稿格式无效，可以重试")
    return value


def _generate_weekly_overview(compatibility: Any, request: UiRequest) -> dict[str, Any]:
    perspective = str(request.body.get("perspective") or "mine")
    department_id = str(request.body.get("departmentId") or "") or None
    dashboard = _retained_dashboard(
        compatibility,
        week=str(request.body.get("weekLabel") or ""),
        perspective=perspective,
        department_id=department_id,
    )
    work_items = [item for item in dashboard.get("workItems") or [] if isinstance(item, Mapping)]
    plans = [item for item in dashboard.get("plans") or [] if isinstance(item, Mapping)]
    current_review = dashboard.get("currentReview") if isinstance(dashboard.get("currentReview"), Mapping) else {}
    if not work_items and not plans and not current_review:
        return {
            **dict(dashboard["weeklyOverviewGenerationStatus"]),
            "status": "failed",
            "failureReason": "material_pack_empty",
            "generatedAt": None,
        }
    tasks_for_prompt: list[dict[str, Any]] = []
    client_ids: list[str] = []
    for item in work_items[:80]:
        snapshot = item.get("taskSnapshot") if isinstance(item.get("taskSnapshot"), Mapping) else {}
        client_id = str(snapshot.get("clientId") or "")
        if client_id and client_id not in client_ids:
            client_ids.append(client_id)
        tasks_for_prompt.append({
            "taskId": str(item.get("taskId") or ""),
            "title": str(snapshot.get("title") or ""),
            "status": str(snapshot.get("status") or ""),
            "clientId": client_id or None,
            "clientName": snapshot.get("clientName"),
            "note": str(item.get("note") or ""),
            "structuredNote": item.get("structuredNote") or {},
        })
    project_contexts: list[dict[str, Any]] = []
    for client_id in client_ids[:6]:
        try:
            knowledge = compatibility.runtime.project_knowledge_context(client_id)
        except LocalRuntimeError:
            continue
        project_contexts.append({
            "clientId": client_id,
            "sources": _event_line_knowledge_sources(knowledge)[:8],
        })
    material = {
        "weekLabel": dashboard["weekLabel"],
        "perspective": dashboard.get("activePerspective"),
        "departmentName": dashboard.get("activeDepartmentName"),
        "tasks": tasks_for_prompt,
        "plans": plans[:20],
        "memberReview": current_review or None,
        "projectKnowledge": project_contexts,
    }
    started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    completion = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是益语智库任务计划 Agent。依据本周正式任务、成员已写复盘、计划以及项目正式知识，"
            "形成一份可继续由成员修改的周复盘概览。归纳2到6条真实工作主线；只引用输入里的 taskId，"
            "不得虚构成果、人物、数字或项目背景。输出严格 JSON："
            '{"summaryText":"...","mainlines":[{"title":"...","taskIds":["..."],'
            '"progressText":"...","nextGoalText":"..."}]}。不要输出代码围栏或解释。'
        ),
        prompt=json.dumps(material, ensure_ascii=False)[:48_000],
        creativity_mode="strict",
        capability="task_planning_weekly_review",
        read_timeout_seconds=90.0,
    )
    parsed = _weekly_agent_json(str(completion.get("content") or ""))
    by_id = {str(item["taskId"]): item for item in tasks_for_prompt if item.get("taskId")}
    mainlines: list[dict[str, Any]] = []
    used_task_ids: list[str] = []
    for index, raw_line in enumerate(parsed.get("mainlines") or []):
        if not isinstance(raw_line, Mapping):
            continue
        task_ids = [str(value) for value in raw_line.get("taskIds") or [] if str(value) in by_id]
        task_ids = list(dict.fromkeys(task_ids))
        title = str(raw_line.get("title") or "").strip()
        progress = str(raw_line.get("progressText") or "").strip()
        next_goal = str(raw_line.get("nextGoalText") or "").strip()
        if not title or not progress or not next_goal:
            continue
        related = [by_id[value] for value in task_ids]
        completed_count = sum(str(item.get("status") or "") == "done" for item in related)
        used_task_ids.extend(task_ids)
        mainlines.append({
            "id": f"agent-mainline:{dashboard['weekLabel']}:{index + 1}",
            "title": title[:120],
            "taskCount": len(related),
            "completedCount": completed_count,
            "pendingCount": max(0, len(related) - completed_count),
            "progressText": progress[:1200],
            "nextGoalText": next_goal[:1200],
        })
        if len(mainlines) >= 6:
            break
    summary = str(parsed.get("summaryText") or "").strip()
    if not summary or not mainlines:
        raise LocalRuntimeError(502, "weekly_review_agent_empty", "未形成可用的复盘草稿，可以重试")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    cards = {
        "summaryText": summary[:2_400],
        "mainlines": mainlines,
        "generatedBy": "ai",
        "evidenceMeta": {
            "taskIds": list(dict.fromkeys(used_task_ids)),
            "planningCycleIds": [str(item.get("id") or "") for item in plans if item.get("id")],
            "clientIds": client_ids,
            "modelName": completion.get("modelName"),
            "agentKind": "task_planning",
        },
    }
    status = {
        **dict(dashboard["weeklyOverviewGenerationStatus"]),
        "status": "succeeded",
        "startedAt": started_at,
        "generatedAt": generated_at,
        "failureReason": None,
    }
    _planning_projector(compatibility).save_weekly_overview(
        membership_id=str(status.get("viewerUserId") or ""),
        week_label=str(dashboard["weekLabel"]),
        perspective=str(dashboard.get("activePerspective") or "mine"),
        department_id=dashboard.get("activeDepartmentId"),
        payload={"cards": cards, "status": status},
    )
    return status


@router.post(r"reviews/weekly-overview/refresh")
def refresh_retained_review_overview(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _generate_weekly_overview(compatibility, request)


@router.get(r"reviews/weekly-overview/status")
def retained_review_overview_status(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    return _review_refresh_status(compatibility, request)


@router.get(r"reviews/department-signals")
def retained_review_department_signals(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    dashboard = _retained_dashboard(
        compatibility,
        week=str(request.query.get("weekLabel") or ""),
        perspective=str(request.query.get("perspective") or "mine"),
        department_id=str(request.query.get("departmentId") or ""),
    )
    review = dashboard.get("currentReview") or {}
    work_items = [
        item for item in dashboard.get("workItems") or [] if isinstance(item, Mapping)
    ]
    completed = sum(
        str((item.get("taskSnapshot") or {}).get("status") or "") == "done"
        for item in work_items
    )
    total = len(work_items)
    completion_rate = round(completed * 100 / total) if total else 0
    blocker = str(review.get("workBlocker") or "").strip()
    support = str(review.get("supportNeeded") or "").strip()
    next_focus = str(review.get("nextWeekFocus") or "").strip()
    plan_count = len(dashboard.get("plans") or [])
    health_indicators = [
        {
            "key": "weekly_tasks",
            "label": "本周任务",
            "valueText": str(total),
            "unitText": "项",
            "deltaText": None,
            "trendDirection": "flat",
            "accent": "neutral",
            "helperText": "来自当前视角可见任务",
        },
        {
            "key": "completion_rate",
            "label": "完成率",
            "valueText": str(completion_rate),
            "unitText": "%",
            "deltaText": None,
            "trendDirection": "flat",
            "accent": "success" if completion_rate >= 70 else "warning" if total else "neutral",
            "helperText": f"{completed}/{total} 项已完成" if total else "本周尚无任务",
        },
        {
            "key": "active_plans",
            "label": "关联计划",
            "valueText": str(plan_count),
            "unitText": "项",
            "deltaText": None,
            "trendDirection": "flat",
            "accent": "success" if plan_count else "neutral",
            "helperText": "当前周期正式计划",
        },
        {
            "key": "blockers",
            "label": "待处理阻塞",
            "valueText": "1" if blocker else "0",
            "unitText": "项",
            "deltaText": None,
            "trendDirection": "flat",
            "accent": "danger" if blocker else "success",
            "helperText": blocker or "当前复盘未登记阻塞",
        },
    ]
    decisions = []
    for rank, (kind, text, decision, cost) in enumerate(
        [
            ("blocker", blocker, support or "明确负责人和解除条件", "阻塞继续影响本周计划"),
            ("focus", next_focus, "确认下周第一优先行动", "重点可能在任务切换中被稀释"),
        ],
        start=1,
    ):
        if text:
            decisions.append({
                "id": f"{dashboard['weekLabel']}:{kind}",
                "rank": rank,
                "severity": "important" if kind == "blocker" else "normal",
                "title": "处理本周阻塞" if kind == "blocker" else "锁定下周重点",
                "situation": text,
                "decision": decision,
                "cost": cost,
                "actionLabel": None,
                "actionTarget": None,
                "sourceRefs": [],
            })
    department_name = str(dashboard.get("activeDepartmentName") or "当前部门")
    return {
        "weekLabel": dashboard["weekLabel"],
        "viewerRole": "department_lead" if dashboard.get("activePerspective") == "department" else "employee",
        "healthIndicators": health_indicators,
        "executiveDecisions": decisions,
        "departmentScoreboard": ([{
            "departmentId": str(dashboard.get("activeDepartmentId") or "current"),
            "departmentName": department_name,
            "leaderName": dashboard.get("activeDepartmentLeaderName"),
            "valueProductionScore": completion_rate,
            "fulfillmentRatePct": completion_rate,
            "monthlyProgressPct": completion_rate,
            "humanEfficiencyScore": completion_rate,
            "headlineInsight": blocker or next_focus or "本周暂无额外信号",
            "status": "abnormal" if blocker else "normal",
        }] if dashboard.get("activePerspective") == "department" else []),
        "actionAlerts": [],
        "oneOnOneSuggestions": [],
        "departmentSnapshots": [],
    }


# Retained renderer bridge.  These are aliases to the same strict event_lines
# authority, not a second event-line implementation.
@router.get(r"event-lines")
def list_event_lines_legacy_surface(
    compatibility: Any, request: UiRequest, _: Any
) -> list[dict[str, Any]]:
    rows = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/event-lines",
        query={**request.query, "includeArchived": "true"},
    )
    _planning_projector(compatibility).apply_event_lines(rows)
    return [
        _event_line_ui(item)
        for item in rows or []
        if isinstance(item, Mapping)
    ]


@router.post(r"event-lines")
def create_event_line_legacy_surface(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    result = _command(compatibility, request, "POST", "event-lines")
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return _event_line_ui(result.get("eventLine") or {})


def _strict_event_line_detail(
    compatibility: Any,
    event_line_id: str,
    request: UiRequest,
) -> dict[str, Any]:
    result = _query(
        compatibility,
        f"event-lines/{quote(unquote(event_line_id), safe='')}",
        request,
    )
    projector = _planning_projector(compatibility)
    projector.apply_event_lines([result["eventLine"]])
    projector.apply_event_activities(result.get("activities") or [])
    return result


@router.get(r"event-lines/(?P<event_line_id>[^/]+)/report-snapshot")
def event_line_report_snapshot(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    result = _strict_event_line_detail(
        compatibility, match.group("event_line_id"), request
    )
    participants = sorted({
        str(collaborator.get("display_name") or "")
        for task in result.get("tasks") or []
        if isinstance(task, Mapping)
        for collaborator in task.get("collaborators") or []
        if isinstance(collaborator, Mapping) and collaborator.get("display_name")
    })
    attachments = _event_line_report_attachments(compatibility, result)
    event_line = _event_line_ui(result.get("eventLine") or {})
    archived = event_line.get("status") == "archived"
    return {
        "eventLine": event_line,
        "activities": _event_line_detail_ui(result)["activities"],
        "tasks": [
            _task_ui(item)
            for item in result.get("tasks") or []
            if isinstance(item, Mapping)
        ],
        "attachments": attachments,
        "timelineNodes": _event_line_timeline_nodes(result),
        "participantNames": participants,
        "snapshotAt": str((result.get("eventLine") or {}).get("updatedAt") or ""),
        "canEdit": not archived,
        "sourceState": "cloud_ready",
        "readOnlyReason": "事件线已归档；仍可查看、生成和下载报告，但不能再关联新任务或补充事件线事实。" if archived else None,
        "taskMirrorStatus": "ready",
        "taskMirrorError": None,
    }


def _report_event_line_id(report: Mapping[str, Any]) -> str:
    latest = report.get("latest") if isinstance(report.get("latest"), Mapping) else {}
    content = (
        latest.get("content_payload")
        if isinstance(latest.get("content_payload"), Mapping)
        else {}
    )
    return str(
        report.get("event_line_id")
        or report.get("eventLineId")
        or content.get("eventLineId")
        or content.get("event_line_id")
        or ""
    ).strip()


@router.get(r"event-lines/(?P<event_line_id>[^/]+)/report-artifacts")
def event_line_report_artifacts(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> list[dict[str, Any]]:
    event_line_id = unquote(match.group("event_line_id"))
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    client_id = str((detail.get("eventLine") or {}).get("clientId") or "").strip()
    if not client_id:
        raise LocalRuntimeError(409, "event_line_client_missing", "事件线缺少项目归属")
    reports = compatibility.runtime.cloud_query(
        f"/api/v2/workbench/projects/{quote(client_id, safe='')}/reports"
    )
    return [
        dict(item)
        for item in reports or []
        if isinstance(item, Mapping) and _report_event_line_id(item) == event_line_id
    ]


@router.get(r"event-lines/(?P<event_line_id>[^/]+)/legacy-report-runs")
def event_line_private_report_drafts(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> list[dict[str, Any]]:
    event_line_id = unquote(match.group("event_line_id"))
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    client_id = str((detail.get("eventLine") or {}).get("clientId") or "").strip()
    if not client_id:
        raise LocalRuntimeError(409, "event_line_client_missing", "事件线缺少项目归属")
    return LocalProjectMaterialsRepository(
        compatibility.runtime
    ).report_drafts(client_id, event_line_id=event_line_id)


@router.get(r"event-lines/(?P<event_line_id>[^/]+)/report-draft")
def event_line_report_draft(
    _: Any,
    __: UiRequest,
    ___: Any,
) -> None:
    return None


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/goal-polish")
def polish_event_line_goal(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    detail = _strict_event_line_detail(
        compatibility,
        match.group("event_line_id"),
        request,
    )
    event_line = detail.get("eventLine") or {}
    source = str(request.body.get("text") or event_line.get("goal") or "").strip()
    if not source:
        raise LocalRuntimeError(422, "event_line_goal_required", "请先输入事件线目标")
    completion = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是益语智库任务计划 Agent。把成员提供的事件线目标润色成一段清晰、"
            "可执行、可核验的中文目标。保留原意，不增加未提供的客户事实；只输出润色正文。"
        ),
        prompt=source,
        creativity_mode="balanced",
        capability="event_line_goal_polish",
    )
    draft = str(completion.get("content") or "").strip()
    if not draft:
        raise LocalRuntimeError(502, "event_line_goal_empty", "组织模型未返回目标草稿")
    return {"draft": draft, "citations": [], "warning": None}


def _event_line_knowledge_sources(context: Mapping[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for bucket in (
        "organizationSharedKnowledge",
        "officialWebsiteFacts",
        "savedMemories",
    ):
        for item in context.get(bucket) or []:
            if not isinstance(item, Mapping):
                continue
            summary = str(item.get("summary") or "").strip()
            if not summary:
                continue
            result.append({
                "id": str(item.get("sourceId") or item.get("id") or ""),
                "type": bucket,
                "title": str(item.get("sourceDescription") or item.get("title") or bucket),
                "summary": summary,
            })
    return result[:24]


@router.post(r"tasks/agent/plan-step-draft")
def draft_task_from_plan_step(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    body = request.body
    action_id = str(body.get("actionId") or "").strip()
    planning_cycle_id = str(body.get("planningCycleId") or "").strip()
    client_id = str(body.get("clientId") or "").strip()
    title = str(body.get("title") or "").strip()
    statement = str(body.get("statement") or "").strip()
    expected_output = str(body.get("expectedOutput") or "").strip()
    if not action_id or not planning_cycle_id or not title:
        raise LocalRuntimeError(
            422,
            "plan_step_draft_input_required",
            "计划步骤、周期和标题不能为空",
        )
    context = (
        compatibility.runtime.project_knowledge_context(client_id)
        if client_id
        else {}
    )
    sources = _event_line_knowledge_sources(context)
    source_text = "\n".join(
        f"[{index + 1}] {item['title']}：{item['summary']}"
        for index, item in enumerate(sources)
    )
    base_description = "\n\n".join(
        item
        for item in (
            statement,
            f"预期产出：{expected_output}" if expected_output else "",
        )
        if item
    )
    if not sources:
        return {
            "title": title,
            "description": base_description,
            "sources": [],
            "agentRun": {
                "agentKind": "task_planning",
                "state": "blocked",
                "stage": "project_context_missing",
                "message": "项目已关联，但当前没有可使用的正式项目知识；已保留计划步骤原文。",
            },
        }
    completion = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是益语智库任务计划 Agent。根据已经确认的项目知识和一条计划步骤，"
            "整理一段可直接放入任务编辑器的中文任务说明。必须先说明本步骤要完成什么，"
            "再补充与执行直接相关的项目背景和核验依据；不得补造人物、日期、承诺或数字。"
            "只输出任务说明正文，不输出标题、JSON、来源编号或分析过程。"
        ),
        prompt=(
            f"计划步骤：{title}\n"
            f"步骤说明：{statement or '无'}\n"
            f"预期产出：{expected_output or '未明确'}\n"
            f"已确认项目知识：\n{source_text}"
        ),
        creativity_mode="strict",
        capability="task_planning_plan_step_draft",
    )
    description = str(completion.get("content") or "").strip()
    if not description:
        raise LocalRuntimeError(
            502,
            "plan_step_agent_empty",
            "未生成任务说明，可继续使用步骤原文",
        )
    return {
        "title": title,
        "description": description,
        "sources": [
            {"id": item["id"], "title": item["title"], "type": item["type"]}
            for item in sources
            if item["id"]
        ],
        "agentRun": {
            "agentKind": "task_planning",
            "state": "completed",
            "stage": "plan_step_task_drafted",
            "message": "已结合项目正式知识整理任务背景",
        },
    }


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/background-draft")
def draft_event_line_background(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    detail = _strict_event_line_detail(
        compatibility,
        match.group("event_line_id"),
        request,
    )
    event_line = detail.get("eventLine") or {}
    client_id = str(event_line.get("clientId") or "")
    knowledge = (
        compatibility.runtime.project_knowledge_context(client_id)
        if client_id
        else {}
    )
    sources = _event_line_knowledge_sources(knowledge)
    source_text = "\n".join(
        f"[{index + 1}] {item['title']}：{item['summary']}"
        for index, item in enumerate(sources)
    )
    instruction = str(request.body.get("instruction") or "").strip()
    prompt = (
        f"事件线名称：{event_line.get('name') or ''}\n"
        f"事件线目标：{event_line.get('goal') or ''}\n"
        f"成员已有背景：{instruction or event_line.get('background') or '无'}\n"
        f"已确认项目知识：\n{source_text or '无可引用的已确认项目知识'}"
    )
    completion = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是益语智库任务计划 Agent。根据事件线目标、成员文字和已确认项目知识，"
            "整理一段简洁的中文背景草稿。不得补造事实；知识不足时只整理已有文字并明确未知。"
            "只输出草稿正文。"
        ),
        prompt=prompt,
        creativity_mode="strict",
        capability="event_line_background_draft",
    )
    draft = str(completion.get("content") or "").strip()
    if not draft:
        raise LocalRuntimeError(502, "event_line_background_empty", "组织模型未返回背景草稿")
    return {
        "draft": draft,
        "citations": [
            {"id": item["id"], "type": item["type"], "title": item["title"]}
            for item in sources
            if item["id"]
        ],
        "warning": None if sources else "当前没有可引用的项目正式知识，仅整理成员输入。",
    }


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/clarification-draft")
def draft_event_line_clarification(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    detail = _strict_event_line_detail(
        compatibility, match.group("event_line_id"), request
    )
    line = detail.get("eventLine") or {}
    missing = [
        label
        for value, label in (
            (line.get("goal"), "目标"),
            (line.get("background"), "背景"),
            (detail.get("tasks"), "正式任务"),
        )
        if not value
    ]
    conversation = str(request.body.get("conversationText") or "").strip()
    return {
        "summary": str(line.get("background") or conversation),
        "stage": str(line.get("lifecycleState") or "active"),
        "intent": str(line.get("goal") or ""),
        "questions": [f"请补充事件线{item}" for item in missing],
        "source": "strict_event_line_authority",
    }


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/retry-sync")
def retry_event_line_sync(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    detail = _strict_event_line_detail(
        compatibility, match.group("event_line_id"), request
    )
    line = detail.get("eventLine") or {}
    return {
        "status": str(line.get("lifecycleState") or "active"),
        "syncStatus": "synced",
        "lastSyncError": None,
        "version": int(line.get("version") or 1),
        "message": "已从组织云权威事件线重新加载",
    }


def _uploaded_event_line_material(
    compatibility: Any,
    request: UiRequest,
    *,
    client_id: str,
) -> tuple[GC07LocalProjectMaterialsRepository, dict[str, Any]]:
    upload = request.body.get("file")
    if isinstance(upload, list):
        upload = upload[0] if upload else None
    stream = getattr(upload, "file", upload)
    if stream is None or not hasattr(stream, "read"):
        raise LocalRuntimeError(422, "event_line_attachment_required", "请选择要上传的证据材料")
    if hasattr(stream, "seek"):
        stream.seek(0)
    raw = stream.read(100 * 1024 * 1024 + 1)
    if not isinstance(raw, bytes):
        raise LocalRuntimeError(422, "event_line_attachment_invalid", "证据材料无效")
    if len(raw) > 100 * 1024 * 1024:
        raise LocalRuntimeError(413, "event_line_attachment_too_large", "单个证据材料不得超过100MB")
    file_name = Path(str(getattr(upload, "filename", "") or "attachment.bin")).name[:180]
    temp_identity = hashlib.sha256(
        f"{request.idempotency_key}|{file_name}".encode("utf-8")
    ).hexdigest()[:24]
    temporary_dir = Path(gettempdir()) / f"yiyu-event-evidence-{temp_identity}"
    temporary_dir.mkdir(parents=True, exist_ok=True)
    temporary = temporary_dir / file_name
    temporary.write_bytes(raw)
    store = _material_store(compatibility)
    try:
        imported = store.import_paths(
            project_id=client_id,
            mode="file",
            paths=[temporary],
            idempotency_key=f"{request.idempotency_key}:local",
        )
    finally:
        temporary.unlink(missing_ok=True)
        try:
            temporary_dir.rmdir()
        except OSError:
            pass
    materials = [
        dict(item)
        for item in imported.get("materials") or []
        if isinstance(item, Mapping)
    ]
    if not materials:
        raise LocalRuntimeError(502, "event_line_attachment_store_failed", "本机证据材料保存失败")
    return store, materials[0]


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/attachments")
def upload_event_line_attachment(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    event_line_id = match.group("event_line_id")
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    client_id = str((detail.get("eventLine") or {}).get("clientId") or "")
    if not client_id:
        raise LocalRuntimeError(409, "event_line_client_missing", "事件线缺少客户项目归属")
    store, local = _uploaded_event_line_material(
        compatibility,
        request,
        client_id=client_id,
    )
    store.bind_pending_materials(project_id=client_id, local_materials=[local])
    registered = compatibility.runtime.cloud_command(
        "POST",
        f"/api/v2/domain/project-materials/projects/{quote(client_id, safe='')}"
        "/materials/register-metadata",
        payload={"materials": [{
            "localSourceId": local["localSourceId"],
            "fileName": local["fileName"],
            "contentHash": local["contentHash"],
            "byteSize": local["byteSize"],
            "mediaType": local["mediaType"],
            "sourceKind": "event_line_attachment",
        }]},
        idempotency_key=f"{request.idempotency_key}:metadata",
        refresh_business=False,
    )
    cloud_document = dict((registered.get("documents") or [])[0])
    store.bind_cloud_documents(
        project_id=client_id,
        local_materials=[local],
        cloud_documents=[cloud_document],
    )
    document_id = str(cloud_document.get("documentId") or "")
    if not document_id:
        raise LocalRuntimeError(502, "event_line_attachment_metadata_invalid", "组织云未返回证据材料标识")
    title = str(request.body.get("title") or local.get("fileName") or "证据材料").strip()
    purpose = str(request.body.get("purpose") or "事件线补充证据").strip()
    activity = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/event-lines/{quote(event_line_id, safe='')}/activities",
        payload={
            "sourceType": "manual_note",
            "sourceId": document_id,
            "title": title,
            "summary": purpose,
            "includeInNarrative": True,
        },
        idempotency_key=f"{request.idempotency_key}:activity",
        refresh_business=False,
    )
    processing = store.process_pending_documents(
        project_id=client_id,
        document_ids=[document_id],
    )
    item = dict((processing.get("items") or [{}])[0])
    return {
        "id": document_id,
        "documentId": document_id,
        "parseStatus": item.get("parseStatus") or "uploaded",
        "parseError": item.get("processingMessage"),
        "activityId": (activity.get("activity") or {}).get("id"),
        "localState": "ready",
        "cloudMetadataState": "ready",
    }


def _retry_event_line_attachment(
    compatibility: Any,
    request: UiRequest,
    *,
    event_line_id: str,
    attachment_id: str,
) -> dict[str, Any]:
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    client_id = str((detail.get("eventLine") or {}).get("clientId") or "")
    linked = any(
        str(item.get("sourceId") or "") == attachment_id
        for item in detail.get("activities") or []
        if isinstance(item, Mapping)
    )
    if not linked:
        raise LocalRuntimeError(404, "event_line_attachment_missing", "事件线证据材料不存在")
    result = _material_store(compatibility).process_pending_documents(
        project_id=client_id,
        document_ids=[attachment_id],
        force=True,
    )
    item = dict((result.get("items") or [{}])[0])
    state = str(item.get("parseStatus") or "failed_retryable")
    return {
        "status": state,
        "attachmentId": attachment_id,
        "jobId": item.get("processingAttemptId"),
        "message": item.get("processingMessage"),
    }


@router.post(
    r"event-lines/(?P<event_line_id>[^/]+)/attachments/"
    r"(?P<attachment_id>[^/]+)/retry-parse"
)
def retry_event_line_attachment_parse(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _retry_event_line_attachment(
        compatibility,
        request,
        event_line_id=match.group("event_line_id"),
        attachment_id=match.group("attachment_id"),
    )


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/attachments/retry-failed")
def retry_failed_event_line_attachments(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    event_line_id = match.group("event_line_id")
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    attachments = _event_line_report_attachments(compatibility, detail)
    retryable = [
        item
        for item in attachments
        if str(item.get("parseStatus") or "")
        in {"failed", "failed_retryable", "missing_source", "missing_document"}
    ]
    processed = 0
    failed = 0
    for index, item in enumerate(retryable):
        child_request = UiRequest(
            **{
                **request.__dict__,
                "idempotency_key": f"{request.idempotency_key}:{index}",
            }
        )
        try:
            result = _retry_event_line_attachment(
                compatibility,
                child_request,
                event_line_id=event_line_id,
                attachment_id=str(item["id"]),
            )
            processed += str(result.get("status") or "") == "ready"
            failed += str(result.get("status") or "") != "ready"
        except LocalRuntimeError:
            failed += 1
    return {
        "status": "completed" if failed == 0 else "failed_retryable",
        "queuedCount": 0,
        "processedCount": processed,
        "failedCount": failed,
        "skippedCount": max(0, len(attachments) - len(retryable)),
    }


@router.get(r"event-lines/(?P<event_line_id>[^/]+)/timeline-narrative")
def event_line_timeline_narrative(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return _event_line_narrative(
        _strict_event_line_detail(
            compatibility, match.group("event_line_id"), request
        )
    )


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/timeline-narrative/regenerate")
def regenerate_event_line_timeline_narrative(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    return event_line_timeline_narrative(compatibility, request, match)


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/readiness-analysis")
def event_line_readiness_analysis(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    result = _strict_event_line_detail(
        compatibility, match.group("event_line_id"), request
    )
    line = result.get("eventLine") or {}
    missing = [
        label
        for value, label in (
            (line.get("goal"), "目标"),
            (line.get("background"), "背景"),
            (result.get("tasks"), "正式任务"),
        )
        if not value
    ]
    return {
        "summary": "事件线基础事实完整" if not missing else "事件线仍有基础信息待补充",
        "findings": [{
            "title": f"缺少{label}",
            "reason": "当前严格权威对象中尚无该事实",
            "suggestion": "在事件线或正式任务中补充",
        } for label in missing],
        "deterministicMissingItems": missing,
        "analyzedAt": str(line.get("updatedAt") or ""),
    }


@router.get(r"event-lines/(?P<event_line_id>[^/]+)/task-candidates")
def event_line_task_candidates(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> list[dict[str, Any]]:
    detail = _strict_event_line_detail(
        compatibility, match.group("event_line_id"), request
    )
    line = detail.get("eventLine") or {}
    task_result = compatibility.runtime.cloud_query("/api/v2/domain/tasks")
    query = str(request.query.get("q") or "").casefold()
    limit = max(1, min(int(request.query.get("limit") or 40), 100))
    result = []
    for row in task_result.get("tasks") or []:
        if not isinstance(row, Mapping):
            continue
        if request.query.get("scope") != "organization" and str(row.get("client_id") or "") != str(line.get("clientId") or ""):
            continue
        if query and query not in (str(row.get("title") or "") + " " + str(row.get("description") or "")).casefold():
            continue
        task = _task_ui(row)
        result.append({
            "id": task["id"],
            "title": task["title"],
            "description": task.get("desc") or "",
            "clientId": task.get("clientId"),
            "clientName": task.get("clientName"),
            "eventLineId": task.get("eventLineId"),
            "eventLineName": task.get("eventLineName"),
            "progressStatus": task.get("status") or "todo",
            "updatedAt": task.get("updatedAt") or "",
        })
        if len(result) >= limit:
            break
    return result


@router.get(r"event-lines/(?P<event_line_id>[^/]+)")
def event_line_detail_legacy_surface(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    event_line_id = quote(unquote(match.group("event_line_id")), safe="")
    result = _query(compatibility, f"event-lines/{event_line_id}", request)
    projector = _planning_projector(compatibility)
    projector.apply_event_lines([result["eventLine"]])
    projector.apply_event_activities(result.get("activities") or [])
    return _event_line_detail_ui(result)


@router.patch(r"event-lines/(?P<event_line_id>[^/]+)")
def update_event_line_legacy_surface(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    event_line_id = quote(unquote(match.group("event_line_id")), safe="")
    result = _command(
        compatibility, request, "PATCH", f"event-lines/{event_line_id}"
    )
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return _event_line_ui(result.get("eventLine") or {})


def _transition_legacy_event_line(
    compatibility: Any,
    request: UiRequest,
    event_line_id: str,
    transition: str,
) -> dict[str, Any]:
    result = _command(
        compatibility,
        request,
        "POST",
        f"event-lines/{quote(event_line_id, safe='')}/{transition}",
    )
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    event_line = _event_line_ui(result.get("eventLine") or {})
    return {
        "status": event_line["status"],
        "version": event_line["version"],
        "eventLine": event_line,
    }


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/close")
def close_event_line_legacy_surface(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    return _transition_legacy_event_line(
        compatibility, request, unquote(match.group("event_line_id")), "archive"
    )


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/reopen")
def reopen_event_line_legacy_surface(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    return _transition_legacy_event_line(
        compatibility, request, unquote(match.group("event_line_id")), "reopen"
    )


@router.delete(r"event-lines/(?P<event_line_id>[^/]+)")
def delete_event_line_legacy_surface(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    event_line_id = unquote(match.group("event_line_id"))
    expected_version = request.body.get("expectedVersion")
    if not expected_version:
        detail = _query(
            compatibility,
            f"event-lines/{quote(event_line_id, safe='')}",
            request,
        )
        expected_version = (detail.get("eventLine") or {}).get("version")
    delete_request = UiRequest(
        method=request.method,
        path=request.path,
        query=request.query,
        body={"expectedVersion": expected_version},
        idempotency_key=request.idempotency_key,
        expected_sandbox_id=request.expected_sandbox_id,
        request_seq=request.request_seq,
    )
    result = _command(
        compatibility,
        delete_request,
        "POST",
        f"event-lines/{quote(event_line_id, safe='')}/delete",
    )
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    event_line = _event_line_ui(result.get("eventLine") or {})
    return {"status": event_line["status"], "counts": {}}


@router.post(
    r"event-lines/(?P<event_line_id>[^/]+)/tasks/(?P<task_id>[^/]+)/link"
)
def attach_task_legacy_surface(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    event_line_id = unquote(match.group("event_line_id"))
    task_id = unquote(match.group("task_id"))
    result = _command(
        compatibility,
        request,
        "POST",
        "event-lines/"
        f"{quote(event_line_id, safe='')}/tasks/{quote(task_id, safe='')}",
    )
    task_receipt = result.get("taskCommandReceipt") or {}
    projection = task_receipt.get("projection") if isinstance(task_receipt, Mapping) else None
    if isinstance(projection, Mapping):
        _task_projector(compatibility).apply(projection)
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return {
        **result,
        "eventLine": _event_line_ui(result.get("eventLine") or {}),
        "task": _task_ui(result.get("task") or {}),
    }


@router.patch(
    r"event-lines/(?P<event_line_id>[^/]+)/tasks/"
    r"(?P<task_id>[^/]+)/milestone"
)
def set_event_line_task_milestone(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    event_line_id = unquote(match.group("event_line_id"))
    task_id = unquote(match.group("task_id"))
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    event_line = detail.get("eventLine") or {}
    expected = int(request.body.get("expectedVersion") or 0)
    if expected <= 0:
        raise LocalRuntimeError(422, "expected_version_required", "缺少事件线版本")
    if int(event_line.get("version") or 0) != expected:
        raise LocalRuntimeError(409, "event_line_version_conflict", "事件线已被其他成员更新")
    task = next(
        (
            item
            for item in detail.get("tasks") or []
            if isinstance(item, Mapping) and str(item.get("id") or "") == task_id
        ),
        None,
    )
    if task is None:
        raise LocalRuntimeError(404, "event_line_task_missing", "任务尚未正式关联当前事件线")
    enabled = bool(request.body.get("isMilestone"))
    result = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/event-lines/{quote(event_line_id, safe='')}/activities",
        payload={
            "sourceType": "task",
            "sourceId": task_id,
            "title": (
                f"里程碑任务：{str(task.get('title') or '')}"
                if enabled
                else f"任务归入事件线：{str(task.get('title') or '')}"
            ),
            "summary": (
                "由成员确认为事件线里程碑"
                if enabled
                else "由成员取消事件线里程碑"
            ),
            "includeInNarrative": True,
            "expectedVersion": expected,
        },
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    updated = _strict_event_line_detail(compatibility, event_line_id, request)
    activity = next(
        (
            item
            for item in _event_line_detail_ui(updated)["activities"]
            if str(item.get("sourceId") or "") == task_id
        ),
        None,
    )
    if activity is None:
        activity = _event_line_detail_ui({
            "activities": [result.get("activity") or {}],
        })["activities"][0]
    return {
        "eventLine": _event_line_ui(updated.get("eventLine") or {}),
        "task": _task_ui(task),
        "activity": activity,
    }


@router.get(r"event-lines/(?P<event_line_id>[^/]+)/reparent-preview")
def preview_event_line_reparent(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    event_line_id = unquote(match.group("event_line_id"))
    target_client_id = str(request.query.get("targetClientId") or "")
    if not target_client_id:
        raise LocalRuntimeError(422, "event_line_target_client_required", "请选择目标客户项目")
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    target = compatibility.runtime.require_project_capability(target_client_id, "read")
    attachments = _event_line_report_attachments(compatibility, detail)
    return {
        "eventLineId": event_line_id,
        "eventLineName": str((detail.get("eventLine") or {}).get("name") or ""),
        "currentClientId": (detail.get("eventLine") or {}).get("clientId"),
        "currentClientName": None,
        "targetClientId": target_client_id,
        "targetClientName": str(target.get("name") or target.get("projectName") or "目标项目"),
        "taskCount": len(detail.get("tasks") or []),
        "taskAttachmentCount": 0,
        "eventLineAttachmentCount": len(attachments),
        "reportCount": 0,
    }


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/reparent")
def reparent_event_line_legacy_surface(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    event_line_id = unquote(match.group("event_line_id"))
    result = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/event-lines/{quote(event_line_id, safe='')}/reparent",
        payload={
            "targetClientId": request.body.get("targetClientId"),
            "expectedVersion": request.body.get("expectedVersion"),
        },
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    for receipt in result.get("taskCommandReceipts") or []:
        if isinstance(receipt, Mapping):
            projection = receipt.get("projection")
            if isinstance(projection, Mapping):
                _task_projector(compatibility).apply(projection)
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return _event_line_ui(result.get("eventLine") or {})


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/merge-preview")
def preview_event_line_merge(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    target_id = unquote(match.group("event_line_id"))
    target = _strict_event_line_detail(compatibility, target_id, request)
    expected = int(request.body.get("expectedVersion") or 0)
    if expected <= 0 or int((target.get("eventLine") or {}).get("version") or 0) != expected:
        raise LocalRuntimeError(409, "event_line_version_conflict", "目标事件线已被其他成员更新")
    source_ids = sorted({
        str(value)
        for value in request.body.get("sourceIds") or []
        if str(value) and str(value) != target_id
    })
    if not source_ids:
        raise LocalRuntimeError(422, "event_line_merge_sources_invalid", "请选择要合并的事件线")
    source_details = [
        _strict_event_line_detail(compatibility, source_id, request)
        for source_id in source_ids
    ]
    target_client = str((target.get("eventLine") or {}).get("clientId") or "")
    if any(
        str((item.get("eventLine") or {}).get("clientId") or "") != target_client
        for item in source_details
    ):
        raise LocalRuntimeError(409, "event_line_merge_client_mismatch", "只能合并同一客户项目内的事件线")
    task_count = sum(len(item.get("tasks") or []) for item in source_details)
    meeting_count = sum(len(item.get("meetings") or []) for item in source_details)
    activity_count = sum(len(item.get("activities") or []) for item in source_details)
    impact = [
        {"table": "任务", "rows": task_count},
        {"table": "会议", "rows": meeting_count},
        {"table": "事件线活动与证据", "rows": activity_count},
    ]
    impact = [item for item in impact if item["rows"] > 0]
    return {
        "targetId": target_id,
        "targetName": str((target.get("eventLine") or {}).get("name") or ""),
        "sources": [{
            "id": str((item.get("eventLine") or {}).get("id") or ""),
            "name": str((item.get("eventLine") or {}).get("name") or ""),
            "status": str((item.get("eventLine") or {}).get("lifecycleState") or "active"),
        } for item in source_details],
        "impact": impact,
        "totalRows": sum(int(item["rows"]) for item in impact),
    }


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/merge")
def merge_event_lines_legacy_surface(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    target_id = unquote(match.group("event_line_id"))
    result = compatibility.runtime.cloud_command(
        "POST",
        f"{_CLOUD_ROOT}/event-lines/{quote(target_id, safe='')}/merge",
        payload={
            "sourceIds": request.body.get("sourceIds") or [],
            "expectedVersion": request.body.get("expectedVersion"),
        },
        idempotency_key=request.idempotency_key,
        refresh_business=False,
    )
    for receipt in result.get("taskCommandReceipts") or []:
        if isinstance(receipt, Mapping):
            projection = receipt.get("projection")
            if isinstance(projection, Mapping):
                _task_projector(compatibility).apply(projection)
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return _event_line_ui(result.get("eventLine") or {})
