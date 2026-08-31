"""Detached GC-06 UI adapter; the integration thread owns registry wiring."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
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


def _event_line_ui(compatibility: Any, row: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the strict event-line authority into the retained renderer shape."""

    lifecycle = str(row.get("lifecycleState") or "active")
    status = "archived" if lifecycle == "archived" else lifecycle
    creator_id = str(row.get("createdByMembershipId") or "").strip()
    client_id = str(row.get("clientId") or "").strip()
    names: dict[str, str] = {}
    client_name: str | None = None
    viewer_id = ""
    is_admin = False
    try:
        names = compatibility._member_names()  # noqa: SLF001
    except (AttributeError, TypeError):
        pass
    if client_id:
        try:
            client_name = _planning_projector(compatibility).client_name(client_id)
        except (AttributeError, TypeError, LocalRuntimeError):
            pass
    try:
        user = compatibility.auth_state().get("user") or {}
        viewer_id = str(user.get("membershipId") or user.get("id") or "").strip()
        is_admin = str(user.get("primaryRole") or user.get("systemRole") or "") == "admin"
        if creator_id == viewer_id and not names.get(creator_id):
            names[creator_id] = str(user.get("fullName") or user.get("displayName") or "").strip()
    except (AttributeError, TypeError):
        pass
    participant_ids = sorted({
        str(item).strip()
        for item in (row.get("participantMembershipIds") or [])
        if str(item).strip() and str(item).strip() != creator_id
    })
    can_manage = bool(is_admin or (creator_id and creator_id == viewer_id))
    is_participant = bool(viewer_id and viewer_id in participant_ids)
    can_add_participants = bool(can_manage or is_participant)
    can_contribute = bool(can_manage or is_participant)
    missing: list[str] = []
    if not str(row.get("goal") or "").strip():
        missing.append("目标")
    if not str(row.get("background") or "").strip():
        missing.append("背景")
    if int(row.get("taskCount") or 0) == 0:
        missing.append("关联任务")
    if int(row.get("activityCount") or 0) == 0:
        missing.append("推进记录")
    readiness = "substantial" if not missing else "general" if len(missing) <= 2 else "incomplete"
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "kind": str(row.get("kind") or "project_line"),
        "status": status,
        "visibilityScope": str(row.get("visibilityScope") or "project_public"),
        "summary": str(row.get("background") or ""),
        "intent": str(row.get("goal") or ""),
        "evidenceCount": int(row.get("attachmentCount") or 0),
        "taskCount": int(row.get("taskCount") or 0),
        "attachmentCount": int(row.get("attachmentCount") or 0),
        "activityCount": int(row.get("activityCount") or 0),
        "ownerId": creator_id or None,
        "ownerName": names.get(creator_id) or None,
        "createdByUserId": creator_id or None,
        "createdByName": names.get(creator_id) or None,
        "primaryClientId": client_id or None,
        "primaryClientName": client_name,
        "primaryDepartmentId": None,
        "primaryDepartmentName": None,
        "participantIds": participant_ids,
        "materialRequirements": [],
        "closedAt": row.get("updatedAt") if lifecycle == "archived" else None,
        "closedByUserId": None,
        "syncStatus": "synced",
        "cloudId": row.get("id"),
        "pendingSyncAction": None,
        "lastSyncError": None,
        "readinessLevel": readiness,
        "readinessMissingItems": missing,
        "version": int(row.get("version") or 1),
        "viewerCapabilities": {
            "canView": True,
            "canContribute": can_contribute,
            "canManageStructure": can_add_participants,
            "canAssignOwner": False,
            "canArchive": can_manage,
            "canReparentProject": can_manage,
            "canAddParticipants": can_add_participants,
            "canManageParticipants": can_manage,
            "canSetMilestone": can_contribute,
        },
        "createdAt": str(row.get("createdAt") or ""),
        "updatedAt": str(row.get("updatedAt") or ""),
    }


def _event_line_detail_ui(compatibility: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eventLine": _event_line_ui_with_report_readiness(compatibility, payload),
        "tasks": [
            _task_ui(item)
            for item in payload.get("tasks") or []
            if isinstance(item, Mapping)
        ],
        "referencedTasks": [
            _task_ui(item)
            for item in payload.get("referencedTasks") or []
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
            "taskId": str(document.get("relatedTaskId") or ""),
            "documentId": source_id,
            "sourceKind": "event_line_attachment",
            "title": str(activity.get("title") or document.get("title") or "事件线材料"),
            "fileName": str(document.get("fileName") or document.get("title") or ""),
            "kind": str(document.get("kind") or "file"),
            "mimeType": str(document.get("mediaType") or "") or None,
            "sizeBytes": int(document.get("byteSize") or 0),
            "downloadUrl": "",
            "openUrl": None,
            "localPath": document.get("managedPath"),
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


def _event_line_report_readiness(
    payload: Mapping[str, Any],
    attachments: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Describe current report completeness without reviving retired fields."""
    event_line = payload.get("eventLine") or {}
    tasks = [
        item for item in payload.get("tasks") or []
        if isinstance(item, Mapping)
    ]
    activities = [
        item for item in payload.get("activities") or []
        if isinstance(item, Mapping)
    ]
    milestone_task_ids = {
        str(item.get("sourceId") or "")
        for item in activities
        if str(item.get("sourceType") or "") == "task"
        and str(item.get("title") or "").startswith("里程碑任务：")
        and str(item.get("sourceId") or "")
    }
    has_progress_fact = any(
        str(item.get("desc") or item.get("description") or "").strip()
        or str(item.get("completedAt") or "").strip()
        or str(item.get("status") or "") in {"done", "completed"}
        for item in tasks
    ) or any(
        str(item.get("summary") or "").strip()
        and str(item.get("sourceType") or "")
        not in {"manual_note", "task_reference"}
        for item in activities
    )
    has_key_evidence = bool(attachments) or any(
        any(isinstance(value, Mapping) for value in item.get("attachments") or [])
        for item in tasks
    ) or any(
        str(item.get("sourceType") or "") in {"meeting_minute", "weekly_review"}
        and bool(str(item.get("summary") or "").strip())
        for item in activities
    )
    dated_facts = {
        str(item.get("happenedAt") or "").strip()
        for item in activities
        if str(item.get("happenedAt") or "").strip()
    }
    dated_facts.update(
        str(value).strip()
        for item in tasks
        for value in (
            item.get("completedAt"),
            item.get("scheduledStartAt"),
            item.get("scheduledEndAt"),
            item.get("dueDate"),
            item.get("createdAt"),
        )
        if str(value or "").strip()
    )
    checks = (
        ("目标", bool(str(event_line.get("goal") or "").strip())),
        ("背景", bool(str(event_line.get("background") or "").strip())),
        ("人工里程碑", bool(milestone_task_ids)),
        ("推进事实", has_progress_fact),
        ("关键证据", has_key_evidence),
        ("时间顺序", len(dated_facts) >= 2),
    )
    missing = [label for label, ready in checks if not ready]
    return {
        "level": (
            "substantial"
            if not missing
            else "general"
            if len(missing) <= 2
            else "incomplete"
        ),
        "missingItems": missing,
    }


def _event_line_ui_with_report_readiness(
    compatibility: Any,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Project one event line with the same current report rules everywhere."""
    event_line = _event_line_ui(compatibility, payload.get("eventLine") or {})
    readiness = _event_line_report_readiness(
        payload,
        _event_line_report_attachments(compatibility, payload),
    )
    event_line["readinessLevel"] = readiness["level"]
    event_line["readinessMissingItems"] = readiness["missingItems"]
    return event_line


def _event_line_timeline_nodes(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    referenced_tasks = {
        str(item.get("id") or ""): item
        for item in payload.get("referencedTasks") or []
        if isinstance(item, Mapping) and str(item.get("id") or "")
    }
    for item in payload.get("activities") or []:
        if not isinstance(item, Mapping):
            continue
        source_type = str(item.get("sourceType") or "")
        referenced_task = (
            referenced_tasks.get(str(item.get("sourceId") or ""))
            if source_type == "task_reference"
            else None
        )
        nodes.append({
            "id": str(item.get("id") or ""),
            "kind": (
                "project_review"
                if source_type == "weekly_review"
                else "continuing_task"
                if referenced_task is not None
                else "system_trace"
            ),
            "title": str(
                (referenced_task or {}).get("title")
                or item.get("title")
                or "事件线进展"
            ),
            "time": str(item.get("happenedAt") or ""),
            "summary": str(
                (referenced_task or {}).get("description")
                or item.get("summary")
                or ""
            ),
            "sourceTaskIds": (
                [str(item.get("sourceId"))]
                if item.get("sourceType") in {"task", "task_reference"}
                else []
            ),
            "sourceActivityIds": [str(item.get("id") or "")],
            "attachments": [],
            "includeInReport": bool(item.get("includeInNarrative")),
            "evidenceSummary": (
                "引用任务事实（未改变原任务归属）"
                if referenced_task is not None
                else str(item.get("summary") or item.get("title") or "")
            ),
            "warnings": [],
            "tags": [source_type or "activity"],
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


def _event_line_value(item: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value is not None:
            return value
    return None


def _event_line_trim(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}…"


def _event_line_task_business_time(item: Mapping[str, Any]) -> str:
    return str(
        _event_line_value(
            item,
            "completedAt",
            "completed_at",
            "dueDate",
            "due_date",
            "scheduledStartAt",
            "scheduled_start_at",
            "createdAt",
            "created_at",
        )
        or ""
    ).strip()


def _event_line_agent_json(raw: str) -> Mapping[str, Any]:
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
        raise LocalRuntimeError(502, "event_line_agent_json_invalid", "主线还原结果格式无效，可以重新生成") from exc
    if not isinstance(value, Mapping):
        raise LocalRuntimeError(502, "event_line_agent_json_invalid", "主线还原结果格式无效，可以重新生成")
    return value


def _event_line_narrative_source_pack(
    compatibility: Any,
    payload: Mapping[str, Any],
    attachments: list[Mapping[str, Any]],
) -> dict[str, Any]:
    event_line = payload.get("eventLine") if isinstance(payload.get("eventLine"), Mapping) else {}
    tasks = [item for item in payload.get("tasks") or [] if isinstance(item, Mapping)]
    referenced_tasks = [
        item for item in payload.get("referencedTasks") or []
        if isinstance(item, Mapping)
    ]
    task_ids = {str(item.get("id") or "") for item in tasks if str(item.get("id") or "")}
    attachment_ids = {str(item.get("id") or "") for item in attachments if str(item.get("id") or "")}
    milestone_task_ids = list(dict.fromkeys(
        str(item.get("sourceId") or "")
        for item in payload.get("activities") or []
        if isinstance(item, Mapping)
        and str(item.get("sourceType") or "") == "task"
        and str(item.get("title") or "").startswith("里程碑任务：")
        and str(item.get("sourceId") or "") in task_ids
    ))
    system_title = re.compile(
        r"^(?:里程碑任务：|任务归入事件线：|合并来源事件线：)|"
        r"(?:上传附件|归档到任务附件|归档到事件线附件|事件线已归档|创建事件线|更新事件线)",
    )
    activities: list[dict[str, Any]] = []
    for item in payload.get("activities") or []:
        if not isinstance(item, Mapping):
            continue
        source_type = str(item.get("sourceType") or "")
        source_id = str(item.get("sourceId") or "")
        title = str(item.get("title") or "").strip()
        if source_type in {"task", "task_reference", "attachment"}:
            continue
        if source_type == "manual_note" and source_id in attachment_ids:
            continue
        if system_title.search(title):
            continue
        activities.append({
            "id": str(item.get("id") or ""),
            "sourceType": source_type,
            "title": _event_line_trim(title, 240),
            "summary": _event_line_trim(item.get("summary"), 800),
            "happenedAt": str(item.get("happenedAt") or ""),
        })

    def task_input(item: Mapping[str, Any], *, relation: str) -> dict[str, Any]:
        task_id = str(item.get("id") or "")
        return {
            "id": task_id,
            "relation": relation,
            "isHumanMilestone": task_id in milestone_task_ids,
            "title": _event_line_trim(item.get("title"), 240),
            "description": _event_line_trim(
                _event_line_value(item, "description", "desc"),
                1_000,
            ),
            "status": str(
                _event_line_value(
                    item,
                    "progressStatus",
                    "progress_status",
                    "status",
                )
                or ""
            ),
            "businessDate": _event_line_task_business_time(item),
            "createdAt": str(_event_line_value(item, "createdAt", "created_at") or ""),
            "completedAt": str(_event_line_value(item, "completedAt", "completed_at") or ""),
        }

    client_id = str(event_line.get("clientId") or "")
    project_knowledge: list[dict[str, str]] = []
    if client_id:
        try:
            project_knowledge = _event_line_knowledge_sources(
                compatibility.runtime.project_knowledge_context(client_id)
            )[:8]
        except LocalRuntimeError:
            project_knowledge = []
    return {
        "eventLine": {
            "id": str(event_line.get("id") or ""),
            "name": _event_line_trim(event_line.get("name"), 240),
            "goal": _event_line_trim(event_line.get("goal"), 1_000),
            "background": _event_line_trim(event_line.get("background"), 1_500),
            "createdAt": str(event_line.get("createdAt") or ""),
            "updatedAt": str(event_line.get("updatedAt") or ""),
            "version": int(event_line.get("version") or 1),
        },
        "milestoneTaskIds": milestone_task_ids,
        "tasks": [task_input(item, relation="formal") for item in tasks],
        "referencedTasks": [
            task_input(item, relation="reference") for item in referenced_tasks
        ],
        "businessActivities": activities,
        "evidence": [{
            "id": str(item.get("id") or ""),
            "taskId": str(item.get("taskId") or ""),
            "title": _event_line_trim(item.get("title"), 240),
            "purpose": _event_line_trim(item.get("purpose"), 400),
            "parsedPreview": _event_line_trim(item.get("parsedPreview"), 1_200),
            "createdAt": str(item.get("createdAt") or ""),
        } for item in attachments],
        "projectKnowledge": project_knowledge,
    }


def _event_line_source_set_id(source_pack: Mapping[str, Any]) -> str:
    serialized = json.dumps(source_pack, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "event-line-mainline:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _normalize_event_line_agent_narrative(
    parsed: Mapping[str, Any],
    *,
    source_pack: Mapping[str, Any],
    source_set_id: str,
    previous_rev: int,
    model_name: str,
) -> dict[str, Any]:
    event_line = source_pack.get("eventLine") or {}
    task_rows = [
        item for bucket in ("tasks", "referencedTasks")
        for item in source_pack.get(bucket) or []
        if isinstance(item, Mapping)
    ]
    task_by_id = {str(item.get("id") or ""): item for item in task_rows}
    activity_by_id = {
        str(item.get("id") or ""): item
        for item in source_pack.get("businessActivities") or []
        if isinstance(item, Mapping)
    }
    attachment_by_id = {
        str(item.get("id") or ""): item
        for item in source_pack.get("evidence") or []
        if isinstance(item, Mapping)
    }
    milestone_ids = [
        str(value) for value in source_pack.get("milestoneTaskIds") or []
        if str(value) in task_by_id
    ]
    used_tasks: set[str] = set()
    nodes: list[dict[str, Any]] = []
    confidence_values = {"high": 1.0, "medium": 0.7, "low": 0.4}
    for index, raw in enumerate(parsed.get("nodes") or []):
        if not isinstance(raw, Mapping) or len(nodes) >= 5:
            continue
        task_ids = [
            str(value) for value in raw.get("linkedTaskIds") or []
            if str(value) in task_by_id and str(value) not in used_tasks
        ]
        task_ids = list(dict.fromkeys(task_ids))
        activity_ids = list(dict.fromkeys(
            str(value) for value in raw.get("linkedActivityIds") or []
            if str(value) in activity_by_id
        ))
        if not task_ids and not activity_ids:
            continue
        attachment_ids = list(dict.fromkeys(
            str(value) for value in raw.get("linkedAttachmentIds") or []
            if str(value) in attachment_by_id
        ))
        attachment_ids.extend(
            attachment_id
            for attachment_id, attachment in attachment_by_id.items()
            if str(attachment.get("taskId") or "") in task_ids
            and attachment_id not in attachment_ids
        )
        title = _event_line_trim(raw.get("title"), 80)
        narrative = _event_line_trim(raw.get("narrative"), 180)
        if not title or not narrative:
            continue
        confidence = str(raw.get("confidence") or "medium").lower()
        if confidence not in confidence_values:
            confidence = "medium"
        source_times = [
            str(task_by_id[task_id].get("businessDate") or "")
            for task_id in task_ids
            if str(task_by_id[task_id].get("businessDate") or "")
        ] + [
            str(activity_by_id[activity_id].get("happenedAt") or "")
            for activity_id in activity_ids
            if str(activity_by_id[activity_id].get("happenedAt") or "")
        ]
        nodes.append({
            "id": f"{event_line.get('id')}:mainline:{index + 1}",
            "time": str(raw.get("time") or (sorted(source_times)[0] if source_times else "")),
            "title": title,
            "narrative": narrative,
            "confidence": confidence,
            "linkedTaskIds": task_ids,
            "linkedActivityIds": activity_ids,
            "linkedAttachmentIds": attachment_ids,
            "evidenceSummary": "",
            "evidenceGaps": [
                _event_line_trim(value, 120)
                for value in raw.get("evidenceGaps") or []
                if str(value or "").strip()
            ][:2],
        })
        used_tasks.update(task_ids)
    if not nodes:
        raise LocalRuntimeError(502, "event_line_agent_nodes_empty", "模型没有还原出可追溯的主线，可以重新生成")
    missing_milestones = [task_id for task_id in milestone_ids if task_id not in used_tasks]
    if missing_milestones:
        raise LocalRuntimeError(502, "event_line_agent_milestone_missing", "模型遗漏了人工确认的里程碑，可以重新生成")
    output_kind = "formal_mainline" if milestone_ids else "material_overview"
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return {
        "eventLineId": str(event_line.get("id") or ""),
        "rev": max(1, previous_rev + 1),
        "headline": _event_line_trim(parsed.get("headline") or event_line.get("name") or "事件线", 100),
        "opening": _event_line_trim(parsed.get("opening") or event_line.get("goal"), 160),
        "closing": _event_line_trim(parsed.get("closing"), 140),
        "nodes": nodes,
        "overallConfidence": round(
            sum(confidence_values[str(item["confidence"])] for item in nodes) / len(nodes),
            2,
        ),
        "generator": "organization_event_line_agent_v1",
        "modelName": model_name,
        "updatedAt": now,
        "outputKind": output_kind,
        "sourceSetId": source_set_id,
        "eventLineVersion": int(event_line.get("version") or 1),
        "milestoneTaskIds": milestone_ids,
        "isStale": False,
        "formalReady": output_kind == "formal_mainline",
        "missingRequirements": [] if milestone_ids else ["人工里程碑"],
        "availabilityStatus": "ready",
        "availabilityReason": "",
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


def _cycle_overlaps_week(cycle: Mapping[str, Any], week: str) -> bool:
    bounds = _week_bounds(week)
    if bounds is None:
        return False
    try:
        start = date.fromisoformat(str(cycle.get("periodStart") or "")[:10])
        end = date.fromisoformat(str(cycle.get("periodEnd") or "")[:10])
    except ValueError:
        return False
    return start <= bounds[1] and end >= bounds[0]


def _previous_week_label(week: str) -> str:
    bounds = _week_bounds(week)
    if bounds is None:
        return ""
    return _week_label((bounds[0] - timedelta(days=7)).isoformat())


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
        if str(item.get("recordKind") or "") in {"organization_plan", "department_plan"}
        and _cycle_overlaps_week(item, week)
    ]
    cycle_ids = {str(item.get("id") or "") for item in matching_cycles}
    current = next(
        (
            item
            for item in reviews
            if str(item.get("membershipId") or "") == membership_id
            and str(_review_content(item)[1].get("weekLabel") or "") == week
        ),
        None,
    )
    if current is None:
        # Reviews created before weekLabel became their stable period identity
        # are still located through their former formal-plan relationship.
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
            "relatedPlanIds": sorted(cycle_ids),
            "workFreeNote": str(content.get("summary") or ""),
            "personalGrowthNote": "",
            "personalPrivateNote": "",
            "personalVisibility": "self",
            "eventGroupingOverrides": [
                dict(item)
                for item in content.get("eventGroupingOverrides") or []
                if isinstance(item, Mapping)
            ],
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
                "description": task.get("desc") or "",
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
                "planningCycleId": task.get("planningCycleId"),
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


def _retained_review_sources(
    compatibility: Any,
    *,
    membership_id: str,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], dict[str, Any]]:
    cycles = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/planning-cycles",
        query={"includeReviewPeriods": True},
    )
    reviews = compatibility.runtime.cloud_query(
        f"{_CLOUD_ROOT}/weekly-reviews",
        query={"membershipId": membership_id},
    )
    task_result = compatibility.runtime.cloud_query("/api/v2/domain/tasks")
    cycle_rows = [item for item in cycles or [] if isinstance(item, Mapping)]
    review_rows = [item for item in reviews or [] if isinstance(item, Mapping)]
    task_payload = dict(task_result) if isinstance(task_result, Mapping) else {}
    _planning_projector(compatibility).apply_planning_cycles(cycle_rows)
    _planning_projector(compatibility).apply_weekly_reviews(review_rows)
    projection = task_payload.get("projection")
    if isinstance(projection, Mapping):
        # Weekly review consumes the authoritative cloud rows below.  The local
        # task tables are a disposable cache, so an FK dependency that has not
        # reached a member's cold sandbox must be observable and retryable, but
        # must never turn valid cloud data into an unavailable dashboard.
        task_payload["localProjection"] = _task_projector(compatibility).apply(
            projection,
            replace_snapshot=True,
        )
    return cycle_rows, review_rows, task_payload


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
    dashboard["localProjectionState"] = dict(
        task_result.get("localProjection") or {"state": "not_requested"}
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
        saved_cards = saved_overview.get("cards")
        evidence_meta = (
            saved_cards.get("evidenceMeta")
            if isinstance(saved_cards, Mapping)
            and isinstance(saved_cards.get("evidenceMeta"), Mapping)
            else {}
        )
        if evidence_meta.get("schemaVersion") == "weekly_review_agent_v3":
            dashboard["weeklyMainlineCards"] = saved_cards
        saved_event_cards = saved_overview.get("eventCards")
        saved_event_meta = (
            saved_event_cards.get("evidenceMeta")
            if isinstance(saved_event_cards, Mapping)
            and isinstance(saved_event_cards.get("evidenceMeta"), Mapping)
            else {}
        )
        if saved_event_meta.get("schemaVersion") == "weekly_review_event_agent_v4":
            dashboard["weeklyEventReviewCards"] = saved_event_cards
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
    # A successful empty cloud result is authoritative.  Falling back to the
    # last local projection here resurrects cloud tombstones after deletion.
    projector.apply_planning_cycles(result)
    projector.reconcile_planning_cycles(result)
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


@router.delete(r"gc06/planning-cycles/(?P<planning_cycle_id>[^/]+)")
def delete_planning_cycle(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "DELETE",
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


@router.post(r"gc06/meetings/(?P<meeting_id>[^/]+)/migrate-to-task")
def migrate_meeting_to_task(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        "POST",
        f"meetings/{match.group('meeting_id')}/migrate-to-task",
    )
    task = result.get("task") if isinstance(result, Mapping) else None
    if isinstance(task, Mapping):
        _task_projector(compatibility).apply({"tasks": [task]})
    meeting = result.get("meeting") if isinstance(result, Mapping) else None
    if isinstance(meeting, Mapping):
        _planning_projector(compatibility).apply_meetings([meeting])
    if isinstance(task, Mapping):
        task_id = str(task.get("id") or "")
        if task_id:
            _planning_projector(compatibility).apply_meeting_migration(
                meeting_id=match.group("meeting_id"),
                task_id=task_id,
            )
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
    current = next(
        (
            item
            for item in reviews
            if str(item.get("membershipId") or "") == membership_id
            and str(_review_content(item)[1].get("weekLabel") or "") == week
        ),
        None,
    )
    if current is None:
        legacy_cycle_ids = {
            str(item.get("id") or "")
            for item in cycles
            if str(item.get("recordKind") or "")
            in {"organization_plan", "department_plan"}
            and _cycle_overlaps_week(item, week)
        }
        current = next(
            (
                item
                for item in reviews
                if str(item.get("membershipId") or "") == membership_id
                and str(item.get("planningCycleId") or "") in legacy_cycle_ids
            ),
            None,
        )
    cycle_id = str(current.get("planningCycleId") or "") if current else ""
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
            **({"planningCycleId": cycle_id} if cycle_id else {}),
            "weekLabel": week,
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
                "eventGroupingOverrides": [
                    dict(item)
                    for item in (
                        request.body.get("eventGroupingOverrides")
                        if "eventGroupingOverrides" in request.body
                        else current_content.get("eventGroupingOverrides") or []
                    )
                    if isinstance(item, Mapping)
                ],
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


def _weekly_review_prompt_task(item: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = item.get("taskSnapshot") if isinstance(item.get("taskSnapshot"), Mapping) else {}
    structured = item.get("structuredNote") if isinstance(item.get("structuredNote"), Mapping) else {}
    return {
        "taskId": str(item.get("taskId") or ""),
        "title": str(snapshot.get("title") or ""),
        "description": str(snapshot.get("description") or "")[:2_000],
        "status": str(snapshot.get("status") or ""),
        "scheduledStartAt": snapshot.get("scheduledStartAt") or snapshot.get("startDate"),
        "scheduledEndAt": snapshot.get("scheduledEndAt") or snapshot.get("dueDate"),
        "completedAt": snapshot.get("completedAt"),
        "clientId": snapshot.get("clientId"),
        "clientName": snapshot.get("clientName"),
        "eventLineId": snapshot.get("eventLineId"),
        "eventLineName": snapshot.get("eventLineName"),
        "planningCycleId": snapshot.get("planningCycleId"),
        "reviewNote": str(item.get("note") or "")[:2_000],
        "reviewFields": {
            key: str(structured.get(key) or "")[:1_000]
            for key in (
                "reflection",
                "progress",
                "successReason",
                "successExperience",
                "blockerReason",
                "failureInsight",
                "supportNeeded",
                "nextAction",
            )
            if str(structured.get(key) or "").strip()
        },
    }


def _weekly_review_event_contexts(
    compatibility: Any,
    request: UiRequest,
    task_items: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    event_line_ids = list(dict.fromkeys(
        str((item.get("taskSnapshot") or {}).get("eventLineId") or "")
        for item in task_items
        if isinstance(item.get("taskSnapshot"), Mapping)
        and str((item.get("taskSnapshot") or {}).get("eventLineId") or "")
    ))
    result: list[dict[str, Any]] = []
    for event_line_id in event_line_ids[:16]:
        try:
            detail = _strict_event_line_detail(compatibility, event_line_id, request)
        except LocalRuntimeError:
            continue
        line = detail.get("eventLine") if isinstance(detail.get("eventLine"), Mapping) else {}
        activities = [
            {
                "id": str(item.get("id") or ""),
                "happenedAt": item.get("happenedAt"),
                "title": str(item.get("title") or "")[:300],
                "summary": str(item.get("summary") or "")[:1_000],
            }
            for item in detail.get("activities") or []
            if isinstance(item, Mapping)
        ][-8:]
        result.append({
            "eventLineId": event_line_id,
            "name": str(line.get("name") or ""),
            "goal": str(line.get("goal") or "")[:2_000],
            "background": str(line.get("background") or "")[:2_000],
            "lifecycleState": line.get("lifecycleState"),
            "clientId": line.get("clientId"),
            "recentActivities": activities,
        })
    return result


def _enforce_weekly_explicit_event_groups(
    raw_groups: Any,
    *,
    tasks: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only explicit event-line groups; unlinked tasks wait for member action."""
    task_by_id = {
        str(item.get("taskId") or ""): item
        for item in tasks
        if str(item.get("taskId") or "")
    }
    result: list[dict[str, Any]] = []
    for raw in raw_groups or []:
        if not isinstance(raw, Mapping):
            continue
        task_ids = list(dict.fromkeys(
            str(value)
            for value in raw.get("taskIds") or []
            if str(value) in task_by_id
        ))
        if not task_ids:
            continue
        explicit_line_ids = {
            str(task_by_id[task_id].get("eventLineId") or "")
            for task_id in task_ids
            if str(task_by_id[task_id].get("eventLineId") or "")
        }
        if len(task_ids) == 1 or (len(explicit_line_ids) == 1 and all(
            str(task_by_id[task_id].get("eventLineId") or "") in explicit_line_ids
            for task_id in task_ids
        )):
            result.append(dict(raw))
            continue
        for task_id in task_ids:
            title = str(task_by_id[task_id].get("title") or "未命名任务")
            result.append({
                "title": title,
                "taskIds": [task_id],
                "groupReason": "尚未明确关联同一事件线，默认单列；由成员决定是否与其他任务合并复盘。",
                "confidence": "low",
                "reflectionPromptText": f"请单独复盘“{title}”的实际结果；如属同一事件，可勾选后手动合并。",
            })
    return result


def _normalize_weekly_event_cards(
    raw_groups: Any,
    *,
    tasks: list[Mapping[str, Any]],
    week_label: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_id = {str(item.get("taskId") or ""): item for item in tasks if item.get("taskId")}
    claimed: set[str] = set()
    normalized: list[dict[str, Any]] = []
    groups = raw_groups if isinstance(raw_groups, list) else []
    for raw in groups:
        if not isinstance(raw, Mapping):
            continue
        task_ids = [
            str(value)
            for value in raw.get("taskIds") or []
            if str(value) in by_id and str(value) not in claimed
        ]
        task_ids = list(dict.fromkeys(task_ids))
        explicit_line_ids = {
            str(by_id[task_id].get("eventLineId") or "")
            for task_id in task_ids
            if str(by_id[task_id].get("eventLineId") or "")
        }
        if explicit_line_ids:
            task_ids.extend(
                task_id
                for task_id, task in by_id.items()
                if task_id not in claimed
                and task_id not in task_ids
                and str(task.get("eventLineId") or "") in explicit_line_ids
            )
        if not task_ids:
            continue
        claimed.update(task_ids)
        related = [by_id[task_id] for task_id in task_ids]
        explicit_lines = {
            str(item.get("eventLineId") or "")
            for item in related
            if str(item.get("eventLineId") or "")
        }
        title = str(raw.get("title") or "").strip() or str(related[0].get("title") or "未命名事件")
        group_reason = str(raw.get("groupReason") or "").strip()
        prompt = str(raw.get("reflectionPromptText") or "").strip()
        confidence = str(raw.get("confidence") or "medium")
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"
        normalized.append({
            "id": f"agent-event:{week_label}:{len(normalized) + 1}",
            "title": title[:160],
            "cardKind": (
                "event_line"
                if len(explicit_lines) == 1
                else "task_cluster"
                if len(task_ids) > 1
                else "single_task"
            ),
            "taskIds": task_ids,
            "taskTitles": [str(item.get("title") or "") for item in related],
            "groupReason": group_reason[:800],
            "reflectionPromptText": prompt[:1_200],
            "confidence": confidence,
            "generatedBy": "ai",
        })
    for task_id, task in by_id.items():
        if task_id in claimed:
            continue
        normalized.append({
            "id": f"unassigned-event:{week_label}:{len(normalized) + 1}",
            "title": str(task.get("title") or "未命名任务")[:160],
            "cardKind": "needs_assignment",
            "taskIds": [task_id],
            "taskTitles": [str(task.get("title") or "")],
            "groupReason": "Agent 没有找到足够证据把这项任务与其他任务归为同一事件。",
            "reflectionPromptText": "请确认这项任务对应的真实事件，以及它是否应该与其他任务合并复盘。",
            "confidence": "low",
            "generatedBy": "fallback",
        })
    return {
        "cards": normalized,
        "generatedBy": "ai" if normalized and all(item["generatedBy"] == "ai" for item in normalized) else "fallback",
        "evidenceMeta": {"schemaVersion": "weekly_review_event_agent_v4"},
    }, normalized


def _apply_weekly_event_grouping_overrides(
    event_cards: dict[str, Any],
    groups: list[dict[str, Any]],
    *,
    overrides: Any,
    tasks: list[Mapping[str, Any]],
    week_label: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(overrides, list) or not overrides:
        return event_cards, groups
    by_id = {str(item.get("taskId") or ""): item for item in tasks if item.get("taskId")}
    claimed: set[str] = set()
    human_groups: list[dict[str, Any]] = []
    for raw in overrides:
        if not isinstance(raw, Mapping):
            continue
        task_ids = list(dict.fromkeys(
            str(value)
            for value in raw.get("taskIds") or []
            if str(value) in by_id and str(value) not in claimed
        ))
        if not task_ids:
            continue
        claimed.update(task_ids)
        related = [by_id[value] for value in task_ids]
        explicit_lines = {
            str(item.get("eventLineId") or "")
            for item in related
            if str(item.get("eventLineId") or "")
        }
        human_groups.append({
            "id": str(raw.get("id") or f"human-event:{week_label}:{len(human_groups) + 1}"),
            "title": str(raw.get("title") or related[0].get("title") or "未命名事件")[:160],
            "cardKind": (
                "event_line"
                if len(explicit_lines) == 1
                else "task_cluster"
                if len(task_ids) > 1
                else "single_task"
            ),
            "taskIds": task_ids,
            "taskTitles": [str(item.get("title") or "") for item in related],
            "groupReason": "成员已在本周复盘中确认这组任务的事件归属。",
            "reflectionPromptText": str(raw.get("reflectionPromptText") or "请围绕这个真实事件记录结果、判断与后续。")[:1_200],
            "confidence": "high",
            "generatedBy": "human",
        })
    if not human_groups:
        return event_cards, groups
    remaining_groups: list[dict[str, Any]] = []
    for group in groups:
        remaining_ids = [value for value in group.get("taskIds") or [] if value not in claimed]
        if not remaining_ids:
            continue
        related = [by_id[value] for value in remaining_ids if value in by_id]
        remaining_groups.append({
            **group,
            "taskIds": remaining_ids,
            "taskTitles": [str(item.get("title") or "") for item in related],
            "cardKind": group.get("cardKind") if len(remaining_ids) > 1 else "single_task",
        })
    next_groups = [*human_groups, *remaining_groups]
    return {
        **event_cards,
        "cards": next_groups,
        "generatedBy": "human",
    }, next_groups


def _weekly_review_evidence_packs(
    *,
    event_groups: list[Mapping[str, Any]],
    tasks: list[Mapping[str, Any]],
    plans: list[Mapping[str, Any]],
    project_contexts: list[Mapping[str, Any]],
    event_contexts: list[Mapping[str, Any]],
    current_review: Mapping[str, Any],
) -> list[dict[str, Any]]:
    tasks_by_id = {str(item.get("taskId") or ""): item for item in tasks}
    plans_by_id = {str(item.get("id") or ""): item for item in plans if item.get("id")}
    projects_by_id = {str(item.get("clientId") or ""): item for item in project_contexts if item.get("clientId")}
    events_by_id = {str(item.get("eventLineId") or ""): item for item in event_contexts if item.get("eventLineId")}
    packs: list[dict[str, Any]] = []
    for group in event_groups:
        related_tasks = [tasks_by_id[value] for value in group.get("taskIds") or [] if value in tasks_by_id]
        plan_ids = list(dict.fromkeys(
            str(item.get("planningCycleId") or "")
            for item in related_tasks
            if str(item.get("planningCycleId") or "") in plans_by_id
        ))
        client_ids = list(dict.fromkeys(
            str(item.get("clientId") or "")
            for item in related_tasks
            if str(item.get("clientId") or "") in projects_by_id
        ))
        event_line_ids = list(dict.fromkeys(
            str(item.get("eventLineId") or "")
            for item in related_tasks
            if str(item.get("eventLineId") or "") in events_by_id
        ))
        packs.append({
            "eventGroupId": group.get("id"),
            "eventTitle": group.get("title"),
            "groupReason": group.get("groupReason"),
            "confidence": group.get("confidence"),
            "tasks": related_tasks,
            "linkedPlans": [plans_by_id[value] for value in plan_ids],
            "linkedProjects": [projects_by_id[value] for value in client_ids],
            "linkedEventLines": [events_by_id[value] for value in event_line_ids],
            "memberReview": dict(current_review) if current_review else None,
        })
    return packs


def _save_weekly_event_grouping(compatibility: Any, request: UiRequest) -> dict[str, Any]:
    perspective = str(request.body.get("perspective") or "mine")
    department_id = str(request.body.get("departmentId") or "") or None
    dashboard = _retained_dashboard(
        compatibility,
        week=str(request.body.get("weekLabel") or ""),
        perspective=perspective,
        department_id=department_id,
    )
    generation = dict(dashboard.get("weeklyOverviewGenerationStatus") or {})
    tasks = [
        _weekly_review_prompt_task(item)
        for item in dashboard.get("workItems") or []
        if isinstance(item, Mapping)
    ][:80]
    overrides = [
        dict(item)
        for item in request.body.get("eventGroupingOverrides") or []
        if isinstance(item, Mapping)
    ]
    event_cards, groups = _normalize_weekly_event_cards(
        [],
        tasks=tasks,
        week_label=str(dashboard.get("weekLabel") or ""),
    )
    event_cards, _groups = _apply_weekly_event_grouping_overrides(
        event_cards,
        groups,
        overrides=overrides,
        tasks=tasks,
        week_label=str(dashboard.get("weekLabel") or ""),
    )
    projector = _planning_projector(compatibility)
    saved = projector.load_weekly_overview(
        membership_id=str(generation.get("viewerUserId") or ""),
        week_label=str(dashboard.get("weekLabel") or ""),
        perspective=str(dashboard.get("activePerspective") or "mine"),
        department_id=dashboard.get("activeDepartmentId"),
    ) or {}
    status = dict(saved.get("status") or generation)
    projector.save_weekly_overview(
        membership_id=str(generation.get("viewerUserId") or ""),
        week_label=str(dashboard.get("weekLabel") or ""),
        perspective=str(dashboard.get("activePerspective") or "mine"),
        department_id=dashboard.get("activeDepartmentId"),
        payload={
            **saved,
            "cards": saved.get("cards") or {},
            "eventCards": event_cards,
            "eventGroupingOverrides": overrides,
            "status": status,
        },
    )
    return {
        **generation,
        **status,
        "status": "succeeded",
        "failureReason": None,
    }


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
    generation_status = dict(dashboard.get("weeklyOverviewGenerationStatus") or {})
    saved_overview = _planning_projector(compatibility).load_weekly_overview(
        membership_id=str(generation_status.get("viewerUserId") or ""),
        week_label=str(dashboard.get("weekLabel") or ""),
        perspective=str(dashboard.get("activePerspective") or "mine"),
        department_id=dashboard.get("activeDepartmentId"),
    ) or {}
    if "eventGroupingOverrides" in request.body:
        raw_event_grouping_overrides = request.body.get("eventGroupingOverrides") or []
    elif current_review.get("eventGroupingOverrides"):
        raw_event_grouping_overrides = current_review.get("eventGroupingOverrides") or []
    else:
        raw_event_grouping_overrides = saved_overview.get("eventGroupingOverrides") or []
    event_grouping_overrides = [
        dict(item)
        for item in raw_event_grouping_overrides
        if isinstance(item, Mapping)
    ]
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
        tasks_for_prompt.append(_weekly_review_prompt_task(item))
    previous_week = _previous_week_label(str(dashboard["weekLabel"]))
    previous_tasks_for_prompt: list[dict[str, Any]] = []
    if previous_week:
        previous_dashboard = _retained_dashboard(
            compatibility,
            week=previous_week,
            perspective=perspective,
            department_id=department_id,
        )
        previous_tasks_for_prompt = [
            _weekly_review_prompt_task(item)
            for item in previous_dashboard.get("workItems") or []
            if isinstance(item, Mapping)
        ][:80]
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
    event_contexts = _weekly_review_event_contexts(compatibility, request, work_items)
    material = {
        "weekLabel": dashboard["weekLabel"],
        "perspective": dashboard.get("activePerspective"),
        "departmentName": dashboard.get("activeDepartmentName"),
        "tasks": tasks_for_prompt,
        "plans": plans[:20],
        "memberReview": current_review or None,
        "projectKnowledge": project_contexts,
        "eventLines": event_contexts,
    }
    started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    grouping_completion = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是益语智库周复盘 Agent。第一步只负责从零散任务中还原真实事件。"
            "优先依据明确事件线、共同计划步骤或交付物、同一项目阶段、共同结果以及前后行动关系归并。"
            "事件复盘中的归并必须保守：只有任务已明确关联同一事件线时才可自动合并；"
            "没有明确事件线关系的任务，即使标题相似或属于同一计划，也要分别输出，交给成员手动选择合并。"
            "日期只用于核验时间是否明显冲突，绝不能因为日期不同就拆开同一事件，也不能因为同一天就合并无关任务。"
            "例如同一验证工作的准备、执行、复核即使安排在不同日期，也应归为一个事件。"
            "只使用输入中的 taskId，不得遗漏或重复任务，不得虚构关系。证据不足时保留单项并降低 confidence。"
            "输出严格 JSON："
            '{"eventGroups":[{"title":"...","taskIds":["..."],"groupReason":"基于哪些关系归并",'
            '"confidence":"high|medium|low","reflectionPromptText":"针对这个真实事件的一句开放式复盘提示"}]}。'
            "不要输出代码围栏或其他解释。"
        ),
        prompt=json.dumps(material, ensure_ascii=False)[:48_000],
        creativity_mode="balanced",
        capability="task_planning_weekly_review",
        read_timeout_seconds=90.0,
    )
    grouping = _weekly_agent_json(str(grouping_completion.get("content") or ""))
    reconciled_groups = _enforce_weekly_explicit_event_groups(
        grouping.get("eventGroups"),
        tasks=tasks_for_prompt,
    )
    event_cards, normalized_event_groups = _normalize_weekly_event_cards(
        reconciled_groups,
        tasks=tasks_for_prompt,
        week_label=str(dashboard["weekLabel"]),
    )
    event_cards, normalized_event_groups = _apply_weekly_event_grouping_overrides(
        event_cards,
        normalized_event_groups,
        overrides=event_grouping_overrides,
        tasks=tasks_for_prompt,
        week_label=str(dashboard["weekLabel"]),
    )
    evidence_packs = _weekly_review_evidence_packs(
        event_groups=normalized_event_groups,
        tasks=tasks_for_prompt,
        plans=plans[:20],
        project_contexts=project_contexts,
        event_contexts=event_contexts,
        current_review=current_review,
    )
    completion = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是益语智库周复盘 Agent。你会收到本周真实事件证据包、上周任务和覆盖本周的周/月计划。"
            "summaryText 是最重要的输出，应写成一段自然、相对完整但不堆砌的周度总述：先概括本周真正的"
            "工作重点，再结合上周任务说明推进是延续、深化、收尾还是转向；若月度或更长周期计划确有依据，"
            "再克制地指出下一周可能承接的方向。通常写3到6句，允许比单条主线更详细。即使成员没有写复盘、"
            "没有关联事件线或项目知识很薄，也必须仅依据任务标题、描述、状态和任务之间的关系形成有用判断。"
            "缺少辅助材料只会降低判断的具体程度，禁止输出“尚不能判断”“材料不足”“待确认”等面向用户的"
            "元提示，也不要为了显得谨慎而反复解释缺了什么。"
            "mainlines 形成1到4条即可，每条只用一两句简明、实事求是地交代这组任务在做什么、当前到哪里；"
            "有成员复盘时吸收其中结论，没有复盘时就简略交代任务事实。主线可以合并多个相互关联的事件，"
            "但不得只把任务状态改写成套话。nextMoveText 只在计划、未完成任务或明确行动提供依据时填写；"
            "openQuestions 固定返回空数组。只能引用输入中的 taskId、eventGroupId 和 evidence ref，"
            "不得虚构成果、人物、数字或背景。输出严格 JSON："
            '{"summaryText":"较完整的本周总述","mainlines":[{"title":"...",'
            '"eventGroupIds":["..."],"taskIds":["..."],"narrativeText":"...",'
            '"nextMoveText":"可选","openQuestions":[],'
            '"evidenceRefs":[{"type":"task|plan|project|event_line|review","id":"...","label":"..."}]}]}。'
            "不要输出代码围栏或其他解释。"
        ),
        prompt=json.dumps({
            "weekLabel": dashboard["weekLabel"],
            "perspective": dashboard.get("activePerspective"),
            "previousWeek": {
                "weekLabel": previous_week,
                "tasks": previous_tasks_for_prompt,
            },
            "plansCoveringThisWeek": plans[:20],
            "evidencePacks": evidence_packs,
        }, ensure_ascii=False)[:48_000],
        creativity_mode="balanced",
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
        narrative = str(raw_line.get("narrativeText") or "").strip()
        next_move = str(raw_line.get("nextMoveText") or "").strip()
        evidence_refs = [
            {
                "type": str(value.get("type") or "")[:40],
                "id": str(value.get("id") or "")[:200],
                "label": str(value.get("label") or "")[:200],
            }
            for value in raw_line.get("evidenceRefs") or []
            if isinstance(value, Mapping)
            and str(value.get("type") or "") in {"task", "plan", "project", "event_line", "review"}
            and str(value.get("id") or "")
        ][:12]
        if not title or not narrative:
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
            "taskIds": task_ids,
            "narrativeText": narrative[:800],
            "nextMoveText": next_move[:400] or None,
            "openQuestions": [],
            "evidenceRefs": evidence_refs,
        })
        if len(mainlines) >= 6:
            break
    summary = str(parsed.get("summaryText") or "").strip()
    if not summary or not mainlines:
        raise LocalRuntimeError(502, "weekly_review_agent_empty", "未形成可用的复盘草稿，可以重试")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    cards = {
        "summaryText": summary[:3_600],
        "mainlines": mainlines,
        "generatedBy": "ai",
        "evidenceMeta": {
            "taskIds": list(dict.fromkeys(used_task_ids)),
            "planningCycleIds": [str(item.get("id") or "") for item in plans if item.get("id")],
            "clientIds": client_ids,
            "modelName": completion.get("modelName"),
            "groupingModelName": grouping_completion.get("modelName"),
            "agentKind": "task_planning",
            "schemaVersion": "weekly_review_agent_v3",
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
        payload={
            "cards": cards,
            "eventCards": event_cards,
            "eventGroupingOverrides": event_grouping_overrides,
            "status": status,
        },
    )
    return status


@router.post(r"reviews/weekly-overview/refresh")
def refresh_retained_review_overview(
    compatibility: Any,
    request: UiRequest,
    _: Any,
) -> dict[str, Any]:
    if bool(request.body.get("groupingOnly")):
        return _save_weekly_event_grouping(compatibility, request)
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
    perspective = str(request.query.get("perspective") or "mine")
    if perspective not in {"organization", "department"}:
        raise LocalRuntimeError(
            403,
            "department_signals_management_scope_required",
            "部门/组织信号只向具备相应管理视角的用户开放",
        )
    dashboard = _retained_dashboard(
        compatibility,
        week=str(request.query.get("weekLabel") or ""),
        perspective=perspective,
        department_id=str(request.query.get("departmentId") or ""),
    )
    available_perspectives = {
        str(item.get("key") or "")
        for item in dashboard.get("availablePerspectives") or []
        if isinstance(item, Mapping)
    }
    if perspective not in available_perspectives:
        raise LocalRuntimeError(
            403,
            "department_signals_scope_forbidden",
            "当前账号无权读取该管理范围的信号",
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
    reviewed_count = sum(
        bool(str(item.get("note") or "").strip())
        or bool(item.get("structuredNote"))
        for item in work_items
    )
    blocker = str(review.get("workBlocker") or "").strip()
    support = str(review.get("supportNeeded") or "").strip()
    next_focus = str(review.get("nextWeekFocus") or "").strip()
    plan_count = len(dashboard.get("plans") or [])
    blocker_count = sum(
        bool(str((item.get("structuredNote") or {}).get("blockerReason") or "").strip())
        for item in work_items
        if isinstance(item.get("structuredNote"), Mapping)
    ) + (1 if blocker else 0)
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
            "key": "completed_tasks",
            "label": "已完成",
            "valueText": str(completed),
            "unitText": "项",
            "deltaText": None,
            "trendDirection": "flat",
            "accent": "success" if completed else "neutral",
            "helperText": "按任务当前状态计数",
        },
        {
            "key": "pending_tasks",
            "label": "未完成",
            "valueText": str(max(0, total - completed)),
            "unitText": "项",
            "deltaText": None,
            "trendDirection": "flat",
            "accent": "warning" if total > completed else "success",
            "helperText": "仍在推进或尚未开始",
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
            "key": "reviewed_tasks",
            "label": "已写复盘",
            "valueText": str(reviewed_count),
            "unitText": "项",
            "deltaText": None,
            "trendDirection": "flat",
            "accent": "success" if reviewed_count else "neutral",
            "helperText": "已有正文或结构化复盘",
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
            "valueText": str(blocker_count),
            "unitText": "项",
            "deltaText": None,
            "trendDirection": "flat",
            "accent": "danger" if blocker_count else "success",
            "helperText": blocker or (f"{blocker_count} 条任务复盘登记了卡点" if blocker_count else "当前复盘未登记阻塞"),
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
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "weekLabel": dashboard["weekLabel"],
        "viewerRole": "department_lead" if perspective == "department" else "admin",
        "perspective": perspective,
        "generatedAt": generated_at,
        "sourceSummary": f"基于当前权限范围内 {total} 条任务、{plan_count} 项正式计划和已提交复盘计算",
        "healthIndicators": health_indicators,
        "executiveDecisions": decisions,
        "departmentScoreboard": ([{
            "departmentId": str(dashboard.get("activeDepartmentId") or "current"),
            "departmentName": department_name if perspective == "department" else "当前组织",
            "leaderName": dashboard.get("activeDepartmentLeaderName"),
            "taskTotalCount": total,
            "taskCompletedCount": completed,
            "taskPendingCount": max(0, total - completed),
            "reviewedTaskCount": reviewed_count,
            "blockerCount": blocker_count,
            "activePlanCount": plan_count,
            "fulfillmentRatePct": completion_rate,
            "headlineInsight": blocker or next_focus or "本周暂无额外信号",
            "status": "abnormal" if blocker_count else "normal",
        }]),
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
    result: list[dict[str, Any]] = []
    for item in rows or []:
        if not isinstance(item, Mapping):
            continue
        detail = _strict_event_line_detail(
            compatibility,
            str(item.get("id") or ""),
            request,
        )
        result.append(_event_line_ui_with_report_readiness(compatibility, detail))
    return result


@router.post(r"event-lines")
def create_event_line_legacy_surface(
    compatibility: Any, request: UiRequest, _: Any
) -> dict[str, Any]:
    result = _command(compatibility, request, "POST", "event-lines")
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return _event_line_ui(compatibility, result.get("eventLine") or {})


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
    event_line = _event_line_ui_with_report_readiness(compatibility, result)
    archived = event_line.get("status") == "archived"
    return {
        "eventLine": event_line,
        "activities": _event_line_detail_ui(compatibility, result)["activities"],
        "tasks": [
            _task_ui(item)
            for item in result.get("tasks") or []
            if isinstance(item, Mapping)
        ],
        "referencedTasks": [
            _task_ui(item)
            for item in result.get("referencedTasks") or []
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
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any] | None:
    event_line_id = unquote(match.group("event_line_id"))
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    client_id = str((detail.get("eventLine") or {}).get("clientId") or "").strip()
    if not client_id:
        raise LocalRuntimeError(409, "event_line_client_missing", "事件线缺少项目归属")
    drafts = LocalProjectMaterialsRepository(
        compatibility.runtime
    ).report_drafts(client_id, event_line_id=event_line_id)
    for draft in drafts:
        template = draft.get("template_manifest") or {}
        if (
            isinstance(template, Mapping)
            and template.get("templateId") == "event_line_mainline_report_v2"
            and str(draft.get("source_set_id") or "").strip()
        ):
            return draft
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
    related_task_id = str(request.body.get("relatedTaskId") or "").strip()
    if related_task_id and related_task_id not in {
        str(item.get("id") or "")
        for item in detail.get("tasks") or []
        if isinstance(item, Mapping)
    }:
        raise LocalRuntimeError(
            409,
            "event_line_attachment_task_mismatch",
            "所选任务不属于当前事件线，无法归入该任务",
        )
    store, local = _uploaded_event_line_material(
        compatibility,
        request,
        client_id=client_id,
    )
    store.bind_pending_materials(project_id=client_id, local_materials=[local])
    store.bind_event_line_attachment(
        project_id=client_id,
        document_id=str(local.get("localSourceId") or ""),
        event_line_id=event_line_id,
        related_task_id=related_task_id,
    )
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
            "includeInNarrative": False,
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
) -> dict[str, Any] | None:
    event_line_id = unquote(match.group("event_line_id"))
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    attachments = _event_line_report_attachments(compatibility, detail)
    source_pack = _event_line_narrative_source_pack(
        compatibility,
        detail,
        attachments,
    )
    saved = _planning_projector(compatibility).load_event_line_narrative(event_line_id)
    if not saved:
        return None
    current_source_set_id = _event_line_source_set_id(source_pack)
    if str(saved.get("sourceSetId") or "") == current_source_set_id:
        return saved
    return {
        **saved,
        "isStale": True,
        "formalReady": False,
        "availabilityStatus": "stale",
        "staleReasons": ["目标、里程碑、任务或证据已经变化"],
    }


@router.post(r"event-lines/(?P<event_line_id>[^/]+)/timeline-narrative/regenerate")
def regenerate_event_line_timeline_narrative(
    compatibility: Any,
    request: UiRequest,
    match: Any,
) -> dict[str, Any]:
    event_line_id = unquote(match.group("event_line_id"))
    detail = _strict_event_line_detail(compatibility, event_line_id, request)
    attachments = _event_line_report_attachments(compatibility, detail)
    source_pack = _event_line_narrative_source_pack(
        compatibility,
        detail,
        attachments,
    )
    milestone_ids = list(source_pack.get("milestoneTaskIds") or [])
    completion = compatibility.runtime.private_ai_completion(
        system_prompt=(
            "你是益语智库的事件线主线还原 Agent。你的任务是把零散事实压缩成简短、清楚、可追溯的推进主线，"
            "不是写项目报告，也不是按时间抄录任务清单。人工确认的里程碑是正式主线骨架：每个里程碑任务必须"
            "且只能出现在一个节点中；语义相近、共同构成一个阶段的多个里程碑可以归入同一节点。其他任务、"
            "会议、复盘、决策和材料只用于说明里程碑的前因、推进与结果。节点按真实推进关系和因果顺序排列，"
            "时间仅用于核验和辅助排序；里程碑的时间使用任务业务日期，不使用成员后来点击确认里程碑的时间。"
            "严禁把上传材料、任务关联、创建更新、事件线合并、同步归档等系统操作写成主线节点。材料只能作为"
            "节点证据；同一事实以任务、会议、活动或材料多次出现时必须合并去重。正式关联任务是主事实，引用"
            "任务只补充语境，不得据此改写原任务。项目知识只帮助理解事实的重要性，不得补造结果。"
            "任务状态为已完成只代表该项动作已经结束，不等于测试通过、功能可用、符合预期或形成正式版本；"
            "只有输入正文明确给出这些结论时才能如此表述。属于同一能力链、同一轮验收或同一阶段的里程碑"
            "应优先合并，禁止机械地为每个里程碑各写一个节点。全文保持主线语言，不用“本报告、综上、建议”"
            "等报告口吻，也不要在closing中解释材料不足、信息未知或为什么不能下结论，只需实事求是说明当前"
            "推进到哪里。通常生成2至4个节点；每个节点标题用4至14个汉字概括阶段，不罗列任务名称；"
            "每个节点代表一个阶段或转折，说明限1至2句，交代发生了什么和推进到哪里，不展开分析。"
            "opening和closing各限1句。"
            "若没有人工里程碑，可以保守归纳阶段线索，但不得声称是正式主线。"
            "只引用输入中真实存在的ID。输出严格JSON："
            '{"headline":"简短主线标题","opening":"一句总体脉络","nodes":['
            '{"title":"阶段标题","time":"主要业务时间","narrative":"1至2句简述",'
            '"confidence":"high|medium|low","linkedTaskIds":["..."],'
            '"linkedActivityIds":["..."],"linkedAttachmentIds":["..."],'
            '"evidenceGaps":[]}],"closing":"一句当前所处位置"}。'
            "不要输出代码围栏或解释。"
        ),
        prompt=json.dumps(source_pack, ensure_ascii=False)[:48_000],
        creativity_mode="strict",
        capability="event_line_mainline_restore",
        read_timeout_seconds=120.0,
        max_output_tokens=3_000,
    )
    parsed = _event_line_agent_json(str(completion.get("content") or ""))
    source_set_id = _event_line_source_set_id(source_pack)
    previous = _planning_projector(compatibility).load_event_line_narrative(
        event_line_id
    )
    narrative = _normalize_event_line_agent_narrative(
        parsed,
        source_pack=source_pack,
        source_set_id=source_set_id,
        previous_rev=int((previous or {}).get("rev") or 0),
        model_name=str(completion.get("modelName") or ""),
    )
    if milestone_ids and narrative.get("outputKind") != "formal_mainline":
        raise LocalRuntimeError(502, "event_line_agent_formal_invalid", "模型未按人工里程碑形成正式主线，可以重新生成")
    return _planning_projector(compatibility).save_event_line_narrative(
        event_line_id,
        narrative,
    )


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
    referenced_task_ids = {
        str(item.get("sourceId") or "")
        for item in detail.get("activities") or []
        if isinstance(item, Mapping)
        and str(item.get("sourceType") or "") == "task_reference"
    }
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
        viewer_is_owner = (
            task.get("viewerCollaborationRole") == "owner"
            and task.get("viewerInboxStatus") == "accepted"
        )
        task_client_id = str(task.get("clientId") or "")
        line_client_id = str(line.get("clientId") or "")
        relation_mode = (
            "formal"
            if viewer_is_owner
            and (not task_client_id or task_client_id == line_client_id)
            else "reference"
        )
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
            "taskVersion": int(task.get("version") or 1),
            "viewerIsOwner": viewer_is_owner,
            "relationMode": relation_mode,
            "alreadyReferenced": task["id"] in referenced_task_ids,
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
    return _event_line_detail_ui(compatibility, result)


@router.patch(r"event-lines/(?P<event_line_id>[^/]+)")
def update_event_line_legacy_surface(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    event_line_id = quote(unquote(match.group("event_line_id")), safe="")
    result = _command(
        compatibility, request, "PATCH", f"event-lines/{event_line_id}"
    )
    _planning_projector(compatibility).apply_event_lines([result["eventLine"]])
    return _event_line_ui(compatibility, result.get("eventLine") or {})


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
    event_line = _event_line_ui(compatibility, result.get("eventLine") or {})
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
    event_line = _event_line_ui(compatibility, result.get("eventLine") or {})
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
        "eventLine": _event_line_ui(compatibility, result.get("eventLine") or {}),
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
            for item in _event_line_detail_ui(compatibility, updated)["activities"]
            if str(item.get("sourceId") or "") == task_id
        ),
        None,
    )
    if activity is None:
        activity = _event_line_detail_ui(compatibility, {
            "activities": [result.get("activity") or {}],
        })["activities"][0]
    return {
        "eventLine": _event_line_ui(compatibility, updated.get("eventLine") or {}),
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
    return _event_line_ui(compatibility, result.get("eventLine") or {})


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
    return _event_line_ui(compatibility, result.get("eventLine") or {})
