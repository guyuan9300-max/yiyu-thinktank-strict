from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote

from ..local_asr.engine import transcribe_recording as run_recording_transcription
from ..local_asr.models import SENSE_VOICE_MODEL, model_ready
from ..runtime import LocalRuntimeError
from ..ui_idempotency import replayable_cloud_mutation
from .gc04_tasks import _task_ui as _strict_task_ui
from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("workflow")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _json_object_from_model(content: str) -> dict[str, Any]:
    normalized = content.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*", "", normalized, flags=re.I)
        normalized = re.sub(r"\s*```$", "", normalized)
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError:
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start < 0 or end <= start:
            raise LocalRuntimeError(502, "plan_parse_response_invalid", "组织模型没有返回可识别的计划结构")
        try:
            parsed = json.loads(normalized[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LocalRuntimeError(502, "plan_parse_response_invalid", "组织模型返回的计划结构不完整，请重试") from exc
    if not isinstance(parsed, Mapping):
        raise LocalRuntimeError(502, "plan_parse_response_invalid", "组织模型返回的计划结构无效")
    return dict(parsed)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _cloud_query(
    compatibility: Any,
    path: str,
    query: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(
        f"/api/v2/workflow/{path.strip('/')}",
        query=query,
    )


def _cloud_command(
    compatibility: Any,
    request: UiRequest,
    method: str,
    path: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        method,
        f"/api/v2/workflow/{path.strip('/')}",
        payload=payload,
        idempotency_key=request.idempotency_key,
    )


def _root_command(
    compatibility: Any,
    request: UiRequest,
    method: str,
    path: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return compatibility.runtime.cloud_command(
        method,
        f"/api/v2/{path.strip('/')}",
        payload=payload,
        idempotency_key=request.idempotency_key,
    )


def _root_query(
    compatibility: Any,
    path: str,
    query: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    return compatibility.runtime.cloud_query(
        f"/api/v2/{path.strip('/')}",
        query=query,
    )


def _event_report_artifacts(
    compatibility: Any,
    event_line_id: str,
) -> list[dict[str, Any]]:
    listed = _cloud_query(
        compatibility,
        f"event-lines/{event_line_id}/report-artifacts",
    ).get("artifacts") or []
    artifacts: list[dict[str, Any]] = []
    for item in listed:
        artifact_id = _text(item.get("id"))
        if artifact_id:
            artifacts.append(
                _root_query(
                    compatibility,
                    f"workbench/reports/{artifact_id}",
                )
            )
    return artifacts


def _legacy_report_run(
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    latest = dict(artifact.get("latest") or {})
    content_payload = dict(latest.get("content_payload") or {})
    raw_sections = content_payload.get("sections")
    sections = list(raw_sections) if isinstance(raw_sections, list) else []
    raw_section_status = content_payload.get("sectionsStatus")
    sections_status = (
        list(raw_section_status)
        if isinstance(raw_section_status, list)
        else ["done" for _ in sections]
    )
    event_line_id = _text(artifact.get("event_line_id")) or None
    return {
        "id": artifact.get("id"),
        "client_id": artifact.get("client_id") or "",
        "event_line_id": event_line_id,
        "period_start": content_payload.get("periodStart"),
        "period_end": content_payload.get("periodEnd"),
        "intent_hint": content_payload.get("intentHint"),
        "status": (
            "failed"
            if artifact.get("availability_status") == "blocked"
            else "saved"
        ),
        "blueprint": (
            content_payload.get("blueprint")
            if isinstance(content_payload.get("blueprint"), Mapping)
            else None
        ),
        "sections_status": sections_status,
        "sections": sections,
        "body_markdown": latest.get("content_markdown") or "",
        "warnings": list(artifact.get("stale_reasons") or []),
        "source_set_id": latest.get("source_set_id") or "",
        "narrative_id": latest.get("narrative_id") or artifact.get("id") or "",
        "narrative_rev": int(latest.get("narrative_rev") or 0),
        "event_line_version": int(latest.get("event_line_version") or 0),
        "input_fingerprint": latest.get("input_fingerprint") or "",
        "artifact": dict(artifact),
        "saved_at": latest.get("created_at"),
        "error_message": (
            artifact.get("availability_reason")
            if artifact.get("availability_status") == "blocked"
            else None
        ),
        "output_files": {},
        "total_llm_tokens": int(content_payload.get("totalLlmTokens") or 0),
        "created_at": latest.get("created_at") or artifact.get("updated_at") or _now(),
        "updated_at": artifact.get("updated_at") or _now(),
    }


def _recording_upload_payload(
    compatibility: Any,
    body: Mapping[str, Any],
    *,
    expected_version: int,
) -> dict[str, Any]:
    raw_path = _text(body.get("audioPath"))
    if not raw_path:
        raise LocalRuntimeError(422, "recording_path_required", "录音文件路径不能为空")
    recordings_root = (
        compatibility.runtime.database_path.parent / "recordings"
    ).resolve()
    try:
        path = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LocalRuntimeError(
            404,
            "recording_file_missing",
            "本机录音文件不存在，原件未被上传",
        ) from exc
    if recordings_root != path.parent and recordings_root not in path.parents:
        raise LocalRuntimeError(
            403,
            "recording_path_outside_managed_root",
            "只能归档当前严格新版数据目录中的受管录音",
        )
    if not path.is_file():
        raise LocalRuntimeError(422, "recording_file_invalid", "录音路径不是文件")
    raw = path.read_bytes()
    if len(raw) > 100 * 1024 * 1024:
        raise LocalRuntimeError(413, "attachment_too_large", "单个录音不得超过 100MB")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    session_id = _text(body.get("sessionId"))
    return {
        "fileName": path.name,
        "mediaType": media_type,
        "byteSize": len(raw),
        "contentHash": hashlib.sha256(raw).hexdigest(),
        "contentBase64": base64.b64encode(raw).decode("ascii"),
        "title": _text(body.get("taskTitle")) or path.stem,
        "purpose": "任务录音归档",
        "sourceKind": "task_recording",
        "sourceLocator": f"local-recording-session:{session_id}" if session_id else "",
        "expectedVersion": expected_version,
    }


def _task_context_with_local_knowledge(
    compatibility: Any,
    task_id: str,
) -> dict[str, Any]:
    runtime = compatibility.runtime
    fixed_context = runtime._current_context(  # noqa: SLF001 - fixed context guard
        require_ready=True
    )
    context = _cloud_query(compatibility, f"tasks/{task_id}/context")
    active_context = runtime._current_context(  # noqa: SLF001 - fixed context guard
        require_ready=True
    )
    if (
        not runtime._same_workspace_identity(  # noqa: SLF001 - strict identity guard
            fixed_context,
            active_context,
        )
        or context.get("cloudInstanceId") != fixed_context.cloud_instance_id
        or context.get("organizationId") != fixed_context.organization_id
    ):
        raise LocalRuntimeError(
            409,
            "workspace_context_changed",
            "任务上下文查询期间工作空间身份发生变化",
        )

    task = context.get("task") or {}
    project_id = _text(task.get("projectId"))
    if not project_id:
        return context

    local_context = runtime.project_knowledge_context(project_id)
    active_context = runtime._current_context(  # noqa: SLF001 - fixed context guard
        require_ready=True
    )
    local_project = local_context.get("project") or {}
    if (
        not runtime._same_workspace_identity(  # noqa: SLF001 - strict identity guard
            fixed_context,
            active_context,
        )
        or local_context.get("sandboxId") != fixed_context.sandbox_id
        or local_context.get("cloudInstanceId") != fixed_context.cloud_instance_id
        or local_context.get("organizationId") != fixed_context.organization_id
        or local_project.get("projectId") != project_id
    ):
        raise LocalRuntimeError(
            409,
            "workspace_context_changed",
            "任务本机资料查询期间工作空间身份发生变化",
        )

    local_items: list[dict[str, Any]] = []
    for item in local_context.get("localPrivateKnowledge") or []:
        summary = _text(item.get("summary"))
        if item.get("sourceScope") != "local_private" or not summary:
            continue
        local_items.append(
            {
                "sourceScope": "local_private",
                "sourceType": "local_material",
                "sourceId": item.get("sourceId"),
                "sourceVersion": int(item.get("sourceVersion") or 1),
                "contentHash": item.get("contentHash"),
                "title": item.get("title") or "本机私有资料",
                "summary": summary[:2000],
                "sourceDescription": item.get("sourceDescription") or "",
                "updatedAt": item.get("updatedAt"),
                "processingState": item.get("processingState") or "metadata_only",
            }
        )

    merged = dict(context)
    cloud_project_knowledge = dict(context.get("projectKnowledge") or {})
    cloud_items = [
        dict(item) for item in cloud_project_knowledge.get("items") or []
    ]
    local_excerpts = [
        {
            "sourceScope": item["sourceScope"],
            "sourceType": item["sourceType"],
            "sourceId": item["sourceId"],
            "title": item["title"],
            "summary": item["summary"],
        }
        for item in local_items
    ]
    summary_excerpts = [
        dict(item) for item in context.get("summaryExcerpts") or []
    ] + local_excerpts
    local_state = _text(
        (local_context.get("state") or {}).get("localPrivate")
    ) or ("ready" if local_items else "empty")
    cloud_project_knowledge.update(
        {
            "state": "ready" if summary_excerpts else "empty",
            "items": [*cloud_items, *local_items],
            "summaryExcerpts": summary_excerpts,
            "localPrivateState": local_state,
            "materialBoundary": {
                **dict(cloud_project_knowledge.get("materialBoundary") or {}),
                "localPrivateSource": "current_device_managed_storage",
                "localPrivateUploadedToOrganizationCloud": False,
                "localSourcePathsIncludedInContext": False,
            },
        }
    )
    sources = [dict(item) for item in context.get("sources") or []]
    sources.extend(
        {
            "type": item["sourceType"],
            "scope": item["sourceScope"],
            "id": item["sourceId"],
            "title": item["title"],
            "summary": item["summary"],
            "contentHash": item["contentHash"],
            "version": item["sourceVersion"],
        }
        for item in local_items
    )
    brief = _text(context.get("brief"))
    if local_excerpts:
        local_brief = "本机私有项目背景：\n" + "\n".join(
            f"- {item['title']}：{item['summary']}"
            for item in local_excerpts
        )
        brief = "\n".join(part for part in (brief, local_brief) if part)
    merged.update(
        {
            "projectKnowledge": cloud_project_knowledge,
            "summaryExcerpts": summary_excerpts,
            "sources": sources,
            "brief": brief,
            "materialPackHash": hashlib.sha256(
                json.dumps(
                    sources,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }
    )
    return merged


def _priority(value: Any) -> str:
    normalized = _text(value).lower()
    return {
        "medium": "normal",
        "重要": "high",
        "紧急": "urgent",
    }.get(normalized, normalized if normalized in {"low", "normal", "high", "urgent"} else "normal")


def _task_ai_parse_json(value: Any) -> dict[str, Any]:
    text = _text(value)
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise LocalRuntimeError(
            502,
            "task_ai_parse_response_invalid",
            "组织模型未返回有效的任务结构，请重试",
        ) from exc
    if not isinstance(parsed, dict):
        raise LocalRuntimeError(
            502,
            "task_ai_parse_response_invalid",
            "组织模型返回的任务结构无效，请重试",
        )
    return parsed


def _task_ai_parse_date(value: Any) -> str | None:
    normalized = _text(value)
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise LocalRuntimeError(
            502,
            "task_ai_parse_response_invalid",
            "组织模型返回了无效的任务日期，请重试",
        ) from exc


def _task_ai_parse_time(value: Any) -> str | None:
    normalized = _text(value)
    if not normalized:
        return None
    try:
        return datetime.strptime(normalized, "%H:%M").strftime("%H:%M")
    except ValueError as exc:
        raise LocalRuntimeError(
            502,
            "task_ai_parse_response_invalid",
            "组织模型返回了无效的任务时间，请重试",
        ) from exc


def _project_match_key(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value)).casefold()


def _task_ai_project_match(
    compatibility: Any,
    guessed_name: str | None,
) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    match_key = _project_match_key(guessed_name)
    if not match_key:
        return None, None, []
    matches: list[dict[str, Any]] = []
    projects = compatibility.runtime.cloud_query(
        "/api/v2/domain/project-materials/projects"
    ).get("projects") or []
    for project in projects:
        if _text(project.get("lifecycleState")) not in {"", "active"}:
            continue
        labels = [project.get("name"), project.get("alias")]
        if match_key not in {_project_match_key(label) for label in labels if _text(label)}:
            continue
        project_id = _text(project.get("projectId"))
        project_name = _text(project.get("name"))
        if project_id and project_name:
            matches.append({"id": project_id, "name": project_name, "score": 1.0})
    if len(matches) != 1:
        return None, None, matches
    return matches[0]["id"], matches[0]["name"], matches


def _task_write_payload(
    body: Mapping[str, Any],
    *,
    expected_version: int | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    aliases = {
        "title": "title",
        "desc": "description",
        "description": "description",
        "clientId": "projectId",
        "projectId": "projectId",
        "ownerId": "ownerMembershipId",
        "ownerMembershipId": "ownerMembershipId",
        "collaboratorIds": "collaboratorMembershipIds",
        "collaboratorMembershipIds": "collaboratorMembershipIds",
        "startDate": "startDate",
        "dueDate": "dueDate",
        "scheduledStartAt": "scheduledStartAt",
        "scheduledEndAt": "scheduledEndAt",
        "deadlineAt": "deadlineAt",
        "durationMinutes": "durationMinutes",
    }
    for source, target in aliases.items():
        if source in body:
            result[target] = body[source]
    if "priority" in body:
        result["priority"] = _priority(body.get("priority"))
    if "scopeMode" in body:
        result["visibilityScope"] = (
            "self" if body.get("scopeMode") == "PERSONAL_ONLY" else "participants"
        )
    elif "visibilityScope" in body:
        result["visibilityScope"] = body.get("visibilityScope")
    if expected_version is not None:
        result["expectedVersion"] = expected_version
    return result


def _task_ui(compatibility: Any, item: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = compatibility._snapshot()  # noqa: SLF001 - presentation adapter
    task = compatibility._task(item, snapshot)  # noqa: SLF001
    memberships = item.get("listMemberships") or []
    if memberships:
        task["listId"] = memberships[0].get("taskListId")
    tags = item.get("tags") or []
    task["tags"] = [
        {
            "id": tag.get("taskTagId"),
            "name": tag.get("name"),
            "color": tag.get("color") or "#5B7BFE",
            "scope": (
                "self"
                if tag.get("scopeKind") == "personal"
                else "org"
            ),
            "ownerUserId": tag.get("ownerMembershipId"),
            "createdBy": None,
            "updatedAt": tag.get("updatedAt") or task.get("updatedAt") or _now(),
            "archivedAt": tag.get("archivedAt"),
        }
        for tag in tags
        if tag.get("taskTagId") and tag.get("name")
    ]
    task["tagIds"] = [tag.get("taskTagId") for tag in tags if tag.get("taskTagId")]
    task["attachments"] = list(item.get("attachments") or [])
    task["evidenceCount"] = len(task["attachments"])
    task["eventLineId"] = item.get("eventLineId")
    task["isMilestone"] = bool(item.get("eventLineMilestone"))
    attributes = item.get("attributes") or {}
    task["note"] = attributes.get("note") or ""
    task["reviewStatus"] = attributes.get("reviewStatus")
    task["_strictVersion"] = int(item.get("version") or 1)
    return task


def _attachment_upload_payload(
    body: Mapping[str, Any],
    *,
    expected_version: int,
    markdown: bool = False,
    default_source_kind: str = "task_attachment",
) -> dict[str, Any]:
    if markdown:
        raw = str(body.get("markdown") or "").encode("utf-8")
        title = _text(body.get("title")) or "任务材料"
        file_name = title if title.lower().endswith(".md") else f"{title}.md"
        media_type = "text/markdown"
    else:
        uploaded = body.get("file")
        file_object = getattr(uploaded, "file", None)
        if file_object is None:
            raise LocalRuntimeError(422, "attachment_file_required", "请选择要上传的附件")
        raw = file_object.read(100 * 1024 * 1024 + 1)
        if len(raw) > 100 * 1024 * 1024:
            raise LocalRuntimeError(413, "attachment_too_large", "单个附件不得超过 100MB")
        file_name = _text(getattr(uploaded, "filename", "")) or "attachment.bin"
        media_type = (
            _text(getattr(uploaded, "content_type", ""))
            or "application/octet-stream"
        )
        title = _text(body.get("title")) or file_name
    return {
        "fileName": file_name,
        "mediaType": media_type,
        "byteSize": len(raw),
        "contentHash": hashlib.sha256(raw).hexdigest(),
        "contentBase64": base64.b64encode(raw).decode("ascii"),
        "title": title,
        "purpose": _text(body.get("purpose")),
        "sourceKind": _text(body.get("sourceKind"))
        or ("task_markdown" if markdown else default_source_kind),
        "expectedVersion": expected_version,
    }


def _apply_task_relationships(
    compatibility: Any,
    request: UiRequest,
    body: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any]:
    current = dict(task)
    task_id = _text(current.get("taskId"))
    has_classification = any(
        key in body for key in ("listId", "listIds", "tagIds")
    )
    if has_classification:
        list_ids = body.get("listIds")
        if list_ids is None:
            list_id = _text(body.get("listId"))
            list_ids = [list_id] if list_id else []
        result = compatibility.runtime.cloud_command(
            "PATCH",
            f"/api/v2/workflow/tasks/{task_id}/classification",
            payload={
                "expectedVersion": int(current.get("version") or 1),
                "taskListIds": list_ids,
                "taskTagIds": body.get("tagIds") or [],
            },
            idempotency_key=f"{request.idempotency_key}:classification",
        )
        current = dict(result.get("task") or current)
    event_line_id = _text(body.get("eventLineId"))
    if event_line_id and event_line_id != _text(current.get("eventLineId")):
        event_path = f"/api/v2/workflow/event-lines/{event_line_id}/tasks/{task_id}"
        replayable_cloud_mutation(
            compatibility.runtime,
            idempotency_key=request.idempotency_key,
            command_type="workflow.task_event_line_attach",
            aggregate_type="event_line",
            aggregate_id=event_line_id,
            method="POST",
            path=event_path,
            request_payload={"taskId": task_id, "eventLineId": event_line_id},
            cloud_payload_factory=lambda: {
                "expectedVersion": _event_version(compatibility, event_line_id),
                "allowReassign": True,
            },
        )
        current["eventLineId"] = event_line_id
    return current


def _list_ui(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("taskListId"),
        "name": item.get("name"),
        "color": item.get("color") or "#5B7BFE",
        "sortOrder": int(item.get("sortOrder") or 0),
        "isDefault": bool(item.get("isDefault")),
        "scope": "org" if item.get("scopeKind") == "organization" else "personal",
        "archived": item.get("lifecycleState") == "archived",
        "_strictVersion": int(item.get("version") or 1),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
    }


def _tag_ui(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("taskTagId"),
        "name": item.get("name"),
        "color": item.get("color") or "#5B7BFE",
        "scope": "org" if item.get("scopeKind") == "organization" else "self",
        "archived": item.get("lifecycleState") == "archived",
        "_strictVersion": int(item.get("version") or 1),
        "createdAt": item.get("createdAt"),
        "updatedAt": item.get("updatedAt"),
    }


def _board(compatibility: Any) -> dict[str, Any]:
    payload = _cloud_query(compatibility, "board")
    return {
        "tasks": [_task_ui(compatibility, item) for item in payload.get("tasks") or []],
        "lists": [_list_ui(item) for item in payload.get("lists") or []],
        "tags": [_tag_ui(item) for item in payload.get("tags") or []],
    }


def _task_version(compatibility: Any, task_id: str) -> int:
    detail = _cloud_query(compatibility, f"tasks/{task_id}")
    return int((detail.get("task") or {}).get("version") or 0)


def _event_version(compatibility: Any, event_line_id: str) -> int:
    detail = _cloud_query(compatibility, f"event-lines/{event_line_id}")
    return int((detail.get("eventLine") or {}).get("version") or 0)


def _collection_version(compatibility: Any, kind: str, item_id: str) -> int:
    board = _cloud_query(compatibility, "board")
    collection = board.get("lists" if kind == "list" else "tags") or []
    id_key = "taskListId" if kind == "list" else "taskTagId"
    item = next((entry for entry in collection if entry.get(id_key) == item_id), None)
    if item is None:
        raise LocalRuntimeError(404, f"task_{kind}_missing", "任务分类不存在")
    return int(item.get("version") or 0)


def _expected(
    compatibility: Any,
    body: Mapping[str, Any],
    *,
    task_id: str | None = None,
    event_line_id: str | None = None,
    collection: tuple[str, str] | None = None,
) -> int:
    supplied = body.get(
        "expectedVersion",
        body.get("expected_version", body.get("_strictVersion")),
    )
    try:
        expected = int(supplied)
    except (TypeError, ValueError):
        expected = 0
    if expected > 0:
        return expected
    if task_id:
        return _task_version(compatibility, task_id)
    if event_line_id:
        return _event_version(compatibility, event_line_id)
    if collection:
        return _collection_version(compatibility, collection[0], collection[1])
    raise LocalRuntimeError(428, "expected_version_required", "该写入需要版本信息")


def _event_ui(
    compatibility: Any,
    item: Mapping[str, Any],
) -> dict[str, Any]:
    snapshot = compatibility._snapshot()  # noqa: SLF001
    projects = {
        _text(project.get("projectId")): project
        for project in snapshot.get("projects") or []
    }
    names = compatibility._member_names()  # noqa: SLF001
    project_id = _text(item.get("projectId")) or None
    creator_id = _text(item.get("createdByMembershipId"))
    user = (
        (compatibility.auth_state().get("user") or {})
        if hasattr(compatibility, "auth_state")
        else {}
    )
    viewer_id = _text(user.get("id") or user.get("membershipId"))
    is_admin = _text(user.get("primaryRole")) == "admin"
    participants = {
        _text(value)
        for value in item.get("participantMembershipIds") or []
        if _text(value)
    }
    can_manage = is_admin or bool(viewer_id and viewer_id == creator_id)
    can_contribute = can_manage or viewer_id in participants
    missing = []
    if not _text(item.get("goal")):
        missing.append("目标")
    if not _text(item.get("background")):
        missing.append("背景")
    if int(item.get("milestoneCount") or 0) == 0:
        missing.append("里程碑")
    if int(item.get("attachmentCount") or 0) == 0:
        missing.append("证据材料")
    return {
        "id": item.get("eventLineId"),
        "name": item.get("name") or "未命名事件线",
        "kind": "custom",
        "status": (
            "done"
            if item.get("lifecycleState") == "completed"
            else item.get("lifecycleState") or "active"
        ),
        "visibilityScope": {
            "organization": "project_public",
            "participants": "private",
        }.get(_text(item.get("visibilityScope")), item.get("visibilityScope")),
        "summary": item.get("background") or "",
        "intent": item.get("goal") or "",
        "evidenceCount": int(item.get("attachmentCount") or 0),
        "taskCount": int(item.get("taskCount") or 0),
        "attachmentCount": int(item.get("attachmentCount") or 0),
        "activityCount": int(item.get("taskCount") or 0),
        "ownerId": creator_id or None,
        "ownerName": names.get(creator_id),
        "createdByUserId": creator_id or None,
        "createdByName": names.get(creator_id),
        "primaryClientId": project_id,
        "primaryClientName": (projects.get(project_id or "") or {}).get("name"),
        "primaryDepartmentId": item.get("departmentId"),
        "participantIds": sorted(participants),
        "materialRequirements": [],
        "syncStatus": "synced",
        "cloudId": item.get("eventLineId"),
        "readinessLevel": (
            "substantial" if not missing else "general" if len(missing) <= 2 else "incomplete"
        ),
        "readinessMissingItems": missing,
        "version": int(item.get("version") or 1),
        "_strictVersion": int(item.get("version") or 1),
        "viewerCapabilities": {
            "canView": True,
            "canContribute": can_contribute,
            "canManageStructure": can_manage,
            "canAssignOwner": False,
            "canArchive": can_manage,
            "canReparentProject": can_manage,
            "canAddParticipants": can_manage,
            "canManageParticipants": can_manage,
            "canSetMilestone": can_contribute,
        },
        "createdAt": item.get("createdAt") or item.get("updatedAt") or _now(),
        "updatedAt": item.get("updatedAt") or _now(),
    }


def _event_detail_ui(compatibility: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    line = _event_ui(compatibility, payload.get("eventLine") or {})
    line["activityCount"] = len(payload.get("activities") or [])
    return {
        "eventLine": line,
        "tasks": [_task_ui(compatibility, task) for task in payload.get("tasks") or []],
        "activities": payload.get("activities") or [],
        "attachments": payload.get("attachments") or [],
        "memorySnapshot": None,
        "predictionReadiness": None,
        "clarificationNeeds": line["readinessMissingItems"],
    }


def _task_context_brief(context: Mapping[str, Any]) -> dict[str, Any]:
    task = context.get("task") or {}
    return {
        "id": f"brief-{task.get('taskId')}",
        "taskId": task.get("taskId"),
        "clientId": task.get("projectId"),
        "eventLineId": task.get("eventLineId"),
        "brief": context.get("brief") or "",
        "shouldDisplay": bool(_text(context.get("brief"))),
        "materialPackHash": context.get("materialPackHash") or "",
        "projectKnowledge": dict(context.get("projectKnowledge") or {}),
        "summaryExcerpts": [
            dict(item) for item in context.get("summaryExcerpts") or []
        ],
        "usedProjectSignals": [
            source.get("title")
            for source in context.get("sources") or []
            if source.get("title")
        ],
        "materialBoundary": dict(
            (context.get("projectKnowledge") or {}).get("materialBoundary")
            or {}
        ),
        "qualityFlags": [],
        "generationModel": "deterministic-authority-brief-v2",
        "generationPromptVersion": "strict-workflow-v2",
        "updatedAt": task.get("updatedAt") or _now(),
    }


def _smart_brief(context: Mapping[str, Any]) -> dict[str, Any]:
    task = context.get("task") or {}
    actions = []
    if task.get("deadlineAt") or task.get("dueDate"):
        actions.append(
            {
                "text": f"在截止时间前完成《{task.get('title') or '任务'}》",
                "sourceLabel": "任务截止时间",
                "actionKind": "complete_task",
                "actionKey": "complete",
                "taskTitleSuggestion": task.get("title"),
                "taskDescriptionSuggestion": task.get("description") or "",
            }
        )
    return {
        "taskId": task.get("taskId"),
        "summary": context.get("brief") or task.get("description") or task.get("title") or "",
        "summarySourceLabels": [
            source.get("title")
            for source in context.get("sources") or []
            if source.get("title")
        ],
        "actionItems": actions,
    }


def _page_context_pack(
    *,
    page: str,
    scope_type: str,
    scope_id: str,
    client_id: str | None,
    tasks: list[Mapping[str, Any]],
    documents: list[Mapping[str, Any]],
    sources: list[Mapping[str, Any]],
    context_pack: Mapping[str, Any],
) -> dict[str, Any]:
    source_count: dict[str, int] = {}
    for source in sources:
        source_type = _text(source.get("type")) or "unknown"
        source_count[source_type] = source_count.get(source_type, 0) + 1
    missing = []
    if not client_id:
        missing.append("project")
    if not documents:
        missing.append("documents")
    return {
        "page": page,
        "scopeType": scope_type,
        "scopeId": scope_id,
        "clientId": client_id,
        "intent": "status_brief",
        "officialJudgments": [],
        "candidateJudgments": [],
        "overlayJudgments": [],
        "evidenceCards": [
            {
                "sourceType": source.get("type"),
                "sourceId": source.get("id"),
                "title": source.get("title"),
                "summary": source.get("summary") or "",
                "sourceScope": source.get("scope"),
                "authorityLevel": "strict_v2",
            }
            for source in sources
        ],
        "rawEvidence": [],
        "openQuestions": [],
        "conflicts": [],
        "themeClusters": [],
        "relatedTasks": [dict(task) for task in tasks],
        "relatedMeetings": [],
        "relatedDocuments": [dict(document) for document in documents],
        "notebookSummary": None,
        "memoryFacts": [],
        "contextPack": dict(context_pack),
        "judgmentBundle": None,
        "resolutionTrace": {
            "authority": "strict_v2",
            "legacyRead": False,
            "fallback": False,
        },
        "stateProjection": None,
        "missingContext": missing,
        "boundaryNotes": [
            "上下文只由当前组织云权威事实派生。",
            "未跨越严格新版运行边界。",
            "资料正文未进入页面上下文。",
        ],
        "sourceSummary": source_count,
        "answerPolicy": {
            "mustCiteEvidence": bool(sources),
            "mustDiscloseBoundary": True,
            "allowRawEvidence": False,
        },
        "retrievalPlan": {
            "mode": "authority_metadata",
            "sourceIds": [source.get("id") for source in sources],
        },
        "quality": {
            "score": min(1, len(sources) / 3),
            "complete": not missing,
            "missingCount": len(missing),
        },
        "routeDecision": None,
        "retrievalTrace": None,
    }


def _review_dashboard(
    compatibility: Any,
    query: Mapping[str, str],
) -> dict[str, Any]:
    payload = _cloud_query(compatibility, "reviews", query)
    visible_reviews = payload.get("reviews") or []
    requested = _text(query.get("weekLabel"))
    user = (
        (compatibility.auth_state().get("user") or {})
        if hasattr(compatibility, "auth_state")
        else {}
    )
    viewer_id = _text(user.get("id") or user.get("membershipId"))
    current = next(
        (
            item
            for item in visible_reviews
            if _text(item.get("membershipId")) == viewer_id
            and (not requested or item.get("weekLabel") == requested)
        ),
        None,
    )
    week = (
        requested
        or (current or {}).get("weekLabel")
        or (visible_reviews[0] if visible_reviews else {}).get("weekLabel")
        or ""
    )
    available_perspectives = [{"key": "mine", "label": "我的视角"}]
    visibility_scope = _text(user.get("visibilityScope"))
    is_admin = _text(user.get("primaryRole")) == "admin"
    if (
        is_admin
        or bool(user.get("isDepartmentLead"))
        or visibility_scope in {"department", "organization"}
    ):
        available_perspectives.append(
            {
                "key": "department",
                "label": "部门视角",
                "departmentId": _text(user.get("departmentId")) or None,
                "departmentName": _text(user.get("departmentName")) or None,
            }
        )
    if is_admin or visibility_scope == "organization":
        available_perspectives.append(
            {"key": "organization", "label": "组织视角"}
        )
    requested_perspective = _text(query.get("perspective")) or "mine"
    allowed_perspectives = {
        str(item["key"]) for item in available_perspectives
    }
    active_perspective = (
        requested_perspective
        if requested_perspective in allowed_perspectives
        else "mine"
    )
    session = (
        compatibility._session()  # noqa: SLF001
        if hasattr(compatibility, "_session")
        else {}
    )
    departments = session.get("departments") or []
    department_members: dict[str, set[str]] = {}
    department_names: dict[str, str] = {}
    for department in departments:
        department_id = _text(department.get("departmentId"))
        if not department_id:
            continue
        department_names[department_id] = _text(department.get("name"))
        department_members[department_id] = {
            _text(member.get("membershipId"))
            for member in department.get("members") or []
            if _text(member.get("membershipId"))
        }
    user_department_id = _text(user.get("departmentId"))
    if user_department_id and user_department_id not in department_members:
        department_members[user_department_id] = {viewer_id}
        department_names[user_department_id] = _text(
            user.get("departmentName")
        )
    active_department_id: str | None = None
    if active_perspective == "department":
        requested_department_id = _text(query.get("departmentId"))
        active_department_id = (
            requested_department_id
            if is_admin and requested_department_id in department_members
            else user_department_id or None
        )
    if active_perspective == "mine":
        reviews = [
            item
            for item in visible_reviews
            if _text(item.get("membershipId")) == viewer_id
        ]
    elif active_perspective == "department":
        member_ids = department_members.get(
            active_department_id or "",
            set(),
        )
        reviews = [
            item
            for item in visible_reviews
            if _text(item.get("membershipId")) in member_ids
        ]
    else:
        reviews = list(visible_reviews)
    current = next(
        (
            item
            for item in reviews
            if _text(item.get("membershipId")) == viewer_id
            and (not week or item.get("weekLabel") == week)
        ),
        None,
    )

    board = (
        _board(compatibility)
        if any(review.get("taskLinks") for review in reviews)
        else {"tasks": []}
    )
    tasks_by_id = {
        _text(item.get("id")): item
        for item in board.get("tasks") or []
        if _text(item.get("id"))
    }
    snapshot = compatibility._snapshot()  # noqa: SLF001
    projects = {
        _text(item.get("projectId")): item
        for item in snapshot.get("projects") or []
        if _text(item.get("projectId"))
    }
    event_lines = {
        _text(item.get("eventLineId")): item
        for item in snapshot.get("eventLines") or []
        if _text(item.get("eventLineId"))
    }

    def structured_note(value: Any) -> dict[str, Any]:
        raw = dict(value) if isinstance(value, Mapping) else {}
        completion = _text(raw.get("completionStatus"))
        department_alignment = _text(raw.get("departmentPlanAlignment"))
        organization_alignment = _text(
            raw.get("organizationPlanAlignment")
        )
        lightweight_tag = _text(raw.get("lightweightTag"))
        return {
            "reflection": _text(raw.get("reflection")),
            "lightweightTag": (
                lightweight_tag
                if lightweight_tag
                in {
                    "资料不足",
                    "等待他人",
                    "方向不清",
                    "资源不够",
                    "工作过度饱和",
                }
                else ""
            ),
            "planCommitment": _text(raw.get("planCommitment")),
            "progress": _text(raw.get("progress")),
            "completionStatus": (
                completion
                if completion
                in {
                    "done_on_time",
                    "done_late",
                    "in_progress",
                    "not_done",
                }
                else "in_progress"
            ),
            "departmentPlanId": _text(raw.get("departmentPlanId")) or None,
            "departmentPlanAlignment": (
                department_alignment
                if department_alignment
                in {"aligned", "partial", "misaligned", "unknown"}
                else "unknown"
            ),
            "organizationPlanId": (
                _text(raw.get("organizationPlanId")) or None
            ),
            "organizationPlanAlignment": (
                organization_alignment
                if organization_alignment
                in {"aligned", "partial", "misaligned", "unknown"}
                else "unknown"
            ),
            "successReason": _text(raw.get("successReason")),
            "successExperience": _text(raw.get("successExperience")),
            "blockerReason": _text(raw.get("blockerReason")),
            "failureInsight": _text(raw.get("failureInsight")),
            "supportNeeded": _text(raw.get("supportNeeded")),
            "nextAction": _text(raw.get("nextAction")),
        }

    entries_by_review: dict[str, list[dict[str, Any]]] = {}
    work_items: list[dict[str, Any]] = []
    personal_items: list[dict[str, Any]] = []
    for review in reviews:
        review_id = _text(review.get("weeklyReviewId"))
        review_owner_id = _text(review.get("membershipId"))
        for link in review.get("taskLinks") or []:
            task_id = _text(link.get("taskId"))
            task = tasks_by_id.get(task_id)
            if task is None:
                continue
            content_domain = _text(link.get("contentDomain"))
            if content_domain not in {"work", "personal"}:
                content_domain = (
                    "personal"
                    if task.get("scopeMode") == "PERSONAL_ONLY"
                    else "work"
                )
            if (
                content_domain == "personal"
                and review_owner_id != viewer_id
            ):
                continue
            event_line_id = _text(task.get("eventLineId"))
            event_line = event_lines.get(event_line_id) or {}
            project_id = _text(task.get("clientId"))
            project = projects.get(project_id) or {}
            event_line_context = (
                {
                    "id": event_line_id,
                    "name": (
                        task.get("eventLineName")
                        or event_line.get("name")
                        or None
                    ),
                    "businessCategory": None,
                    "stage": event_line.get("lifecycleState") or None,
                    "summary": event_line.get("background") or None,
                    "intent": event_line.get("goal") or None,
                    "currentBlocker": None,
                    "recentDecision": None,
                    "nextStep": None,
                    "evidenceCount": int(
                        event_line.get("attachmentCount") or 0
                    ),
                    "primaryClientId": project_id or None,
                    "primaryClientName": (
                        task.get("clientName")
                        or project.get("name")
                        or None
                    ),
                    "primaryDepartmentId": (
                        event_line.get("departmentId") or None
                    ),
                    "primaryDepartmentName": department_names.get(
                        _text(event_line.get("departmentId"))
                    )
                    or None,
                }
                if event_line_id
                else None
            )
            entry = {
                "id": (
                    _text(link.get("weeklyReviewTaskLinkId"))
                    or f"{review_id}:{task_id}"
                ),
                "reviewId": review_id or None,
                "taskId": task_id,
                "weekLabel": _text(review.get("weekLabel")) or week,
                "contentDomain": content_domain,
                "note": _text(link.get("note")),
                "structuredNote": structured_note(
                    link.get("structuredNote")
                ),
                "reviewedAt": link.get("reviewedAt"),
                "taskSnapshot": {
                    "title": _text(task.get("title")) or "未命名任务",
                    "status": _text(task.get("status")) or "todo",
                    "startDate": task.get("startDate"),
                    "dueDate": task.get("dueDate"),
                    "deadlineAt": task.get("deadlineAt"),
                    "scheduledStartAt": task.get("scheduledStartAt"),
                    "scheduledEndAt": task.get("scheduledEndAt"),
                    "completedAt": task.get("completedAt"),
                    "createdAt": task.get("createdAt") or _now(),
                    "ownerId": task.get("ownerId"),
                    "ownerName": task.get("ownerName") or "未指定",
                    "clientId": project_id or None,
                    "clientName": (
                        task.get("clientName")
                        or project.get("name")
                        or None
                    ),
                    "eventLineId": event_line_id or None,
                    "eventLineName": (
                        task.get("eventLineName")
                        or event_line.get("name")
                        or None
                    ),
                    "tags": [
                        dict(tag)
                        for tag in task.get("tags") or []
                        if isinstance(tag, Mapping)
                    ],
                    "listName": task.get("listName") or "全部任务",
                    "listColor": task.get("listColor") or "#5B7BFE",
                    "orgContext": task.get("orgContext"),
                    "projectContext": task.get("projectContext"),
                    "eventLineContext": event_line_context,
                },
            }
            entries_by_review.setdefault(review_id, []).append(entry)
            (
                personal_items
                if content_domain == "personal"
                else work_items
            ).append(entry)
    current_entries = entries_by_review.get(
        _text((current or {}).get("weeklyReviewId")),
        [],
    )
    return {
        "weekLabel": week,
        "resolvedWeekLabel": week,
        "currentReview": (
            {
                "id": current.get("weeklyReviewId"),
                "userId": current.get("membershipId"),
                "weekLabel": current.get("weekLabel"),
                "workProgress": current.get("workProgress") or "",
                "workBlocker": current.get("workBlocker") or "",
                "workDirection": current.get("workDirection") or "",
                "workFreeNote": current.get("workFreeNote") or "",
                "personalGrowthNote": current.get("personalGrowthNote") or "",
                "supportNeeded": current.get("supportNeeded") or "",
                "nextWeekFocus": current.get("nextWeekFocus") or "",
                "taskEntries": current_entries,
                "sections": current.get("sections") or [],
                "_strictVersion": int(current.get("version") or 1),
                "createdAt": current.get("updatedAt") or _now(),
                "updatedAt": current.get("updatedAt") or _now(),
            }
            if current
            else None
        ),
        "workItems": work_items,
        "personalItems": personal_items,
        "availablePerspectives": available_perspectives,
        "activePerspective": active_perspective,
        "activeDepartmentId": active_department_id,
        "activeDepartmentName": department_names.get(
            active_department_id or ""
        )
        or None,
        "reviewCount": len(reviews),
        "departmentReports": [],
        "agentDepartmentDigests": [],
        "agentDepartmentPlans": [],
        "plans": compatibility._snapshot().get("plans") or [],  # noqa: SLF001
    }


def _agent_work_projection(
    compatibility: Any,
    query: Mapping[str, str],
) -> dict[str, Any]:
    week_label = _text(query.get("week"))
    month = _text(query.get("month"))
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise LocalRuntimeError(
            422,
            "agent_worklog_month_invalid",
            "机器人工作日志月份必须为 YYYY-MM",
        )
    plan_query = {"weekLabel": week_label} if week_label else None
    raw_plans = _cloud_query(
        compatibility,
        "agent-weekly-plans",
        plan_query,
    ).get("weeklyPlans") or []
    department = _text(query.get("department"))
    if department:
        raw_plans = [
            plan
            for plan in raw_plans
            if _text(plan.get("departmentName")) == department
        ]
    tasks_by_id: dict[str, dict[str, Any]] = {}
    worklogs: list[dict[str, Any]] = []
    digests: list[dict[str, Any]] = []
    weekly_plans: list[dict[str, Any]] = []
    status_map = {
        "active": "planned",
        "completed": "done",
        "cancelled": "blocked",
    }
    for plan in raw_plans:
        plan_tasks: dict[str, dict[str, Any]] = {}
        normalized_items = []
        for item in plan.get("planItems") or []:
            normalized_item = {
                **dict(item),
                "status": status_map.get(
                    _text(item.get("status")),
                    _text(item.get("status")) or "planned",
                ),
            }
            normalized_items.append(normalized_item)
            plan_item_id = _text(item.get("id"))
            if not plan_item_id:
                continue
            linked = _cloud_query(
                compatibility,
                "plan-item-tasks",
                {"planItemId": plan_item_id},
            ).get("tasks") or []
            for raw_task in linked:
                task = _task_ui(compatibility, raw_task)
                task_id = _text(task.get("id"))
                if not task_id:
                    continue
                tasks_by_id[task_id] = task
                plan_tasks[task_id] = task
                updated_at = _text(task.get("updatedAt"))
                date = updated_at[:10]
                if month and not date.startswith(f"{month}-"):
                    continue
                detail_lines = [
                    value
                    for value in (
                        _text(task.get("desc")),
                        _text(task.get("clientName")),
                        _text(task.get("eventLineName")),
                        _text(task.get("note")),
                    )
                    if value
                ][:4]
                worklogs.append(
                    {
                        "id": (
                            f"{_text(plan.get('planId'))}:"
                            f"{plan_item_id}:{task_id}"
                        ),
                        "agentKey": _text(plan.get("agentKey")),
                        "agentName": (
                            _text(plan.get("agentName"))
                            or _text(plan.get("agentKey"))
                        ),
                        "departmentName": _text(
                            plan.get("departmentName")
                        ),
                        "color": _text(plan.get("color")) or "#5B7BFE",
                        "date": date,
                        "weekLabel": _text(plan.get("weekLabel")),
                        "title": _text(task.get("title")),
                        "summary": (
                            _text(task.get("desc"))
                            or _text(task.get("note"))
                            or f"任务状态：{_text(task.get('status'))}"
                        ),
                        "detailLines": detail_lines,
                        "sourceType": "workspace_sync",
                        "createdAt": updated_at,
                    }
                )
        normalized_plan = {
            **dict(plan),
            "planItems": normalized_items,
        }
        weekly_plans.append(normalized_plan)
        digests.append(
            {
                "agentKey": _text(plan.get("agentKey")),
                "agentName": (
                    _text(plan.get("agentName"))
                    or _text(plan.get("agentKey"))
                ),
                "departmentName": _text(plan.get("departmentName")),
                "color": _text(plan.get("color")) or "#5B7BFE",
                "weekLabel": _text(plan.get("weekLabel")),
                "summary": _text(plan.get("summary")),
                "focusItems": [
                    _text(item.get("title"))
                    for item in normalized_items
                    if _text(item.get("title"))
                ],
                "evidenceCount": len(plan_tasks),
                "sourcePolicy": {
                    **dict(plan.get("sourcePolicy") or {}),
                    "taskAuthority": (
                        "organization_plan_items"
                        "+task_records.attributes.departmentPlanItemId"
                    ),
                },
            }
        )
    return {
        "tasks": list(tasks_by_id.values()),
        "worklogs": worklogs,
        "weeklyDigests": digests,
        "weeklyPlans": weekly_plans,
    }


def _dispatch_unpinned(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    path = request.path
    body = dict(request.body)
    if request.method == "GET" and path == "tasks":
        return _board(compatibility)
    if request.method == "POST" and path == "tasks":
        result = _root_command(
            compatibility,
            request,
            "POST",
            "tasks",
            _task_write_payload(body),
        )
        task = _apply_task_relationships(
            compatibility,
            request,
            body,
            result.get("task") or {},
        )
        return _task_ui(compatibility, task)
    task_match = re.fullmatch(r"tasks/([^/]+)", path)
    if task_match and request.method == "PATCH":
        task_id = unquote(task_match.group(1))
        expected = _expected(compatibility, body, task_id=task_id)
        status = _text(body.get("status") or body.get("progressStatus"))
        if status in {"completed", "done"}:
            result = _root_command(
                compatibility,
                request,
                "POST",
                f"tasks/{task_id}/complete",
                {
                    "expectedVersion": expected,
                    "completionNote": _text(body.get("completionNote")),
                },
            )
        elif status in {"todo", "active", "doing"}:
            result = _root_command(
                compatibility,
                request,
                "POST",
                f"tasks/{task_id}/restore",
                {"expectedVersion": expected, "completionNote": ""},
            )
        elif status in {"in_progress", "cancelled"}:
            result = _cloud_command(
                compatibility,
                request,
                "POST",
                (
                    f"tasks/{task_id}/actions/"
                    f"{'started' if status == 'in_progress' else 'cancelled'}"
                ),
                {"expectedVersion": expected},
            )
        else:
            result = _root_command(
                compatibility,
                request,
                "PATCH",
                f"tasks/{task_id}",
                _task_write_payload(body, expected_version=expected),
            )
        task = _apply_task_relationships(
            compatibility,
            request,
            body,
            result.get("task") or {},
        )
        return _task_ui(compatibility, task)
    if task_match and request.method == "DELETE":
        task_id = unquote(task_match.group(1))
        expected = _expected(compatibility, body, task_id=task_id)
        result = _cloud_command(
            compatibility,
            request,
            "POST",
            f"tasks/{task_id}?action=archive",
            {"expectedVersion": expected},
        )
        return {"deleted": bool(result.get("deleted", True))}

    for suffix, action in {
        "confirm": "confirmed",
        "reject": "rejected",
        "review/approve": "review_approved",
        "review/return": "review_returned",
        "note": "note_saved",
    }.items():
        action_match = re.fullmatch(rf"tasks/([^/]+)/{suffix}", path)
        if request.method == "POST" and action_match:
            task_id = unquote(action_match.group(1))
            payload = {
                **body,
                "expectedVersion": _expected(compatibility, body, task_id=task_id),
            }
            result = _cloud_command(
                compatibility,
                request,
                "POST",
                f"tasks/{task_id}/actions/{action}",
                payload,
            )
            if action == "rejected":
                return {
                    "task": _task_ui(compatibility, result.get("task") or {}),
                    "returned": True,
                }
            return _task_ui(compatibility, result.get("task") or {})
    complete_match = re.fullmatch(r"tasks/([^/]+)/complete-with-review", path)
    if request.method == "POST" and complete_match:
        task_id = unquote(complete_match.group(1))
        result = _root_command(
            compatibility,
            request,
            "POST",
            f"tasks/{task_id}/complete",
            {
                "expectedVersion": _expected(compatibility, body, task_id=task_id),
                "completionNote": _text(body.get("reviewNote")),
            },
        )
        return _task_ui(compatibility, result.get("task") or {})
    if request.method == "POST" and path == "tasks/collaboration/batch-handle":
        results = []
        for task_id in body.get("taskIds") or []:
            task_id = _text(task_id)
            try:
                result = compatibility.runtime.cloud_command(
                    "POST",
                    f"/api/v2/tasks/{task_id}/inbox/handle",
                    payload={"expectedVersion": _task_version(compatibility, task_id)},
                    idempotency_key=f"{request.idempotency_key}:{task_id}",
                )
                results.append(
                    {
                        "taskId": task_id,
                        "ok": True,
                        "task": _task_ui(compatibility, result.get("task") or {}),
                    }
                )
            except LocalRuntimeError as exc:
                results.append({"taskId": task_id, "ok": False, "error": exc.code})
        return {
            "results": results,
            "handledCount": sum(1 for item in results if item["ok"]),
            "failedCount": sum(1 for item in results if not item["ok"]),
        }

    collection_match = re.fullmatch(r"task-(lists|tags)(?:/([^/]+))?", path)
    if collection_match:
        kind = "list" if collection_match.group(1) == "lists" else "tag"
        item_id = unquote(collection_match.group(2)) if collection_match.group(2) else None
        payload = dict(body)
        if request.method in {"PATCH", "DELETE"} and item_id:
            payload["expectedVersion"] = _expected(
                compatibility,
                body,
                collection=(kind, item_id),
            )
        cloud_path = f"{'lists' if kind == 'list' else 'tags'}"
        if item_id:
            cloud_path += f"/{item_id}"
        result = _cloud_command(
            compatibility,
            request,
            request.method,
            cloud_path,
            payload,
        )
        if request.method == "DELETE":
            return {"deleted": bool(result.get("deleted", True))}
        item = result.get(kind) or {}
        return _list_ui(item) if kind == "list" else _tag_ui(item)

    if request.method == "GET" and path == "task-views":
        board = _board(compatibility)
        return {
            "views": [
                {"id": "all", "name": "全部任务", "taskIds": [item["id"] for item in board["tasks"]]},
                {
                    "id": "mine",
                    "name": "我的任务",
                    "taskIds": [
                        item["id"]
                        for item in board["tasks"]
                        if item.get("viewerInboxStatus") in {"accepted", "acknowledged"}
                    ],
                },
            ],
            "activeViewId": "all",
        }

    task_page_context = re.fullmatch(r"tasks/([^/]+)/page-context", path)
    if request.method == "GET" and task_page_context:
        task_id = unquote(task_page_context.group(1))
        context = _task_context_with_local_knowledge(compatibility, task_id)
        task = context.get("task") or {}
        return _page_context_pack(
            page="task_detail",
            scope_type="task",
            scope_id=task_id,
            client_id=task.get("projectId"),
            tasks=[_task_ui(compatibility, task)],
            documents=list(context.get("documents") or []),
            sources=list(context.get("sources") or []),
            context_pack={
                "brief": context.get("brief") or "",
                "project": context.get("project"),
                "eventLine": context.get("eventLine"),
                "projectKnowledge": context.get("projectKnowledge"),
                "summaryExcerpts": context.get("summaryExcerpts") or [],
                "materialPackHash": context.get("materialPackHash"),
            },
        )
    meeting_page_context = re.fullmatch(r"meetings/([^/]+)/page-context", path)
    if request.method == "GET" and meeting_page_context:
        meeting_id = unquote(meeting_page_context.group(1))
        context = _cloud_query(compatibility, f"meetings/{meeting_id}/context")
        projects = context.get("projects") or []
        client_id = projects[0].get("project_id") if len(projects) == 1 else None
        return _page_context_pack(
            page="meeting_detail",
            scope_type="meeting",
            scope_id=meeting_id,
            client_id=client_id,
            tasks=[
                _task_ui(compatibility, task)
                for task in context.get("tasks") or []
            ],
            documents=list(context.get("documents") or []),
            sources=list(context.get("sources") or []),
            context_pack={
                "projects": projects,
                "activities": context.get("activities") or [],
                "materialPackHash": context.get("materialPackHash"),
            },
        )

    context_match = re.fullmatch(
        r"tasks/([^/]+)/(context-brief|context-preview|smart-brief|understanding|prep-pack)",
        path,
    )
    if request.method == "GET" and context_match:
        task_id, kind = [unquote(value) for value in context_match.groups()]
        context = _task_context_with_local_knowledge(compatibility, task_id)
        task = context.get("task") or {}
        if kind == "context-brief":
            return _task_context_brief(context)
        if kind == "context-preview":
            return {
                "taskId": task_id,
                "task": _task_ui(compatibility, task),
                "project": context.get("project"),
                "eventLine": context.get("eventLine"),
                "materials": context.get("documents") or [],
                "projectKnowledge": context.get("projectKnowledge"),
                "summaryExcerpts": context.get("summaryExcerpts") or [],
                "brief": context.get("brief") or "",
                "materialBoundary": dict(
                    (context.get("projectKnowledge") or {}).get(
                        "materialBoundary"
                    )
                    or {}
                ),
            }
        if kind == "smart-brief":
            return _smart_brief(context)
        if kind == "understanding":
            return {
                "taskId": task_id,
                "mode": "enhanced" if context.get("sources") else "basic",
                "whatIsThis": task.get("description") or task.get("title") or "",
                "whyItMatters": (context.get("project") or {}).get("summary") or "",
                "progressNow": task.get("lifecycleState") or "todo",
                "unknowns": "",
                "knownFacts": [
                    source.get("title")
                    for source in context.get("sources") or []
                    if source.get("title")
                ],
                "confidence": 1 if context.get("sources") else 0.5,
                "sourceBreakdown": [
                    {
                        "sourceName": source.get("title"),
                        "sourceType": source.get("type"),
                        "available": True,
                        "label": source.get("title"),
                    }
                    for source in context.get("sources") or []
                ],
                "coverage": min(1, len(context.get("sources") or []) / 3),
                "optionalAdvice": None,
            }
        return {
            "taskId": task_id,
            "title": task.get("title") or "",
            "summary": context.get("brief") or "",
            "materials": [
                {
                    "sourceType": "document",
                    "sourceId": item.get("documentId"),
                    "title": item.get("title"),
                    "summary": "",
                    "authorityLevel": "organization",
                }
                for item in context.get("documents") or []
            ],
            "openQuestions": [],
            "judgments": [],
            "risks": [],
            "boundaryNotes": ["资料正文未纳入本次确定性 brief。"],
            "sourceLabels": [
                source.get("title")
                for source in context.get("sources") or []
                if source.get("title")
            ],
            "proposalId": None,
        }
    if request.method == "POST" and path == "tasks/context-briefs/batch":
        return {
            "briefs": [
                _task_context_brief(
                    _task_context_with_local_knowledge(
                        compatibility,
                        _text(task_id),
                    )
                )
                for task_id in body.get("taskIds") or []
                if _text(task_id)
            ]
        }
    if request.method == "POST" and path == "tasks/smart-briefs":
        briefs = []
        for hint in body.get("tasks") or []:
            task_id = _text((hint or {}).get("id"))
            if task_id:
                briefs.append(
                    _smart_brief(
                        _task_context_with_local_knowledge(
                            compatibility,
                            task_id,
                        )
                    )
                )
        return briefs
    smart_adopt = re.fullmatch(
        r"tasks/([^/]+)/smart-brief-actions/([^/]+)/adopt",
        path,
    )
    if request.method == "POST" and smart_adopt:
        task_id, action_key = [unquote(value) for value in smart_adopt.groups()]
        result = _cloud_command(
            compatibility,
            request,
            "POST",
            f"tasks/{task_id}/actions/smart_brief_adopted",
            {
                **body,
                "actionKey": action_key,
                "expectedVersion": _expected(
                    compatibility,
                    body,
                    task_id=task_id,
                ),
            },
        )
        return {
            "ok": True,
            "taskId": task_id,
            "actionKey": action_key,
            "createdTaskId": body.get("createdTaskId"),
            "task": _task_ui(compatibility, result.get("task") or {}),
        }
    if request.method == "POST" and path == "tasks/ai-parse":
        raw = _text(body.get("text"))
        if not raw:
            raise LocalRuntimeError(422, "task_ai_parse_text_required", "请输入要拆解的任务")
        current_date = _text(body.get("currentDate"))
        if current_date:
            try:
                current_date = datetime.strptime(current_date, "%Y-%m-%d").date().isoformat()
            except ValueError as exc:
                raise LocalRuntimeError(
                    422,
                    "task_ai_parse_current_date_invalid",
                    "当前日期格式无效",
                ) from exc
        else:
            current_date = datetime.now(timezone.utc).date().isoformat()
        project_rows = compatibility.runtime.cloud_query(
            "/api/v2/domain/project-materials/projects"
        ).get("projects") or []
        projects = [
            {
                "name": _text(project.get("name")),
                "alias": _text(project.get("alias")) or None,
            }
            for project in project_rows
            if _text(project.get("projectId"))
            and _text(project.get("name"))
            and _text(project.get("lifecycleState")) in {"", "active"}
        ]
        completion = compatibility.runtime.private_ai_completion(
            system_prompt=(
                "你是任务结构化解析器。只返回一个 JSON 对象，不要 Markdown 或解释。"
                "字段必须是 title、desc、dueDate、dueTime、priority、clientName。"
                "title 和 desc 为字符串；dueDate 为 YYYY-MM-DD 或 null；"
                "dueTime 为 HH:MM 或 null；priority 只能是 low、normal、high；"
                "clientName 只能从给定项目名称或别名中原样选择，无法确定时为 null。"
                "不得编造日期、时间或项目。"
            ),
            prompt=json.dumps(
                {
                    "currentDate": current_date,
                    "availableProjects": projects,
                    "taskText": raw,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            creativity_mode="strict",
            capability="fast_structured",
        )
        parsed = _task_ai_parse_json(completion.get("content"))
        title = _text(parsed.get("title"))
        if not title:
            raise LocalRuntimeError(
                502,
                "task_ai_parse_response_invalid",
                "组织模型没有返回任务标题，请重试",
            )
        description = _text(parsed.get("desc"))
        priority = _text(parsed.get("priority")).lower()
        if priority not in {"low", "normal", "high"}:
            raise LocalRuntimeError(
                502,
                "task_ai_parse_response_invalid",
                "组织模型返回了无效的任务优先级，请重试",
            )
        guessed_client_name = _text(parsed.get("clientName")) or None
        client_id, client_name, candidates = _task_ai_project_match(
            compatibility,
            guessed_client_name,
        )
        return {
            "title": title[:300],
            "desc": description or raw,
            "dueDate": _task_ai_parse_date(parsed.get("dueDate")),
            "dueTime": _task_ai_parse_time(parsed.get("dueTime")),
            "priority": priority,
            "clientId": client_id,
            "clientName": client_name,
            "clientCandidates": candidates,
            "rawLlmGuessClientName": guessed_client_name,
        }
    if request.method == "GET" and path in {"tasks/agent-execution", "tasks/agent-worklogs"}:
        projection = _agent_work_projection(
            compatibility,
            request.query,
        )
        if path.endswith("agent-execution"):
            return projection["tasks"]
        return {
            "month": request.query.get("month") or "",
            "worklogs": projection["worklogs"],
            "weeklyDigests": projection["weeklyDigests"],
            "weeklyPlans": projection["weeklyPlans"],
        }

    if request.method == "GET" and path == "event-lines":
        payload = _cloud_query(compatibility, "event-lines")
        return [
            _event_ui(compatibility, item)
            for item in payload.get("eventLines") or []
        ]
    if request.method == "POST" and path == "event-lines":
        result = _root_command(
            compatibility,
            request,
            "POST",
            "event-lines",
            {
                "projectId": body.get("projectId") or body.get("primaryClientId"),
                "name": body.get("name"),
                "goal": body.get("goal") or body.get("intent") or "",
                "background": body.get("background") or body.get("summary") or "",
                "participantMembershipIds": body.get("participantMembershipIds")
                or body.get("participantIds")
                or [],
            },
        )
        return _event_ui(compatibility, result.get("eventLine") or {})
    event_match = re.fullmatch(r"event-lines/([^/]+)", path)
    if event_match and request.method == "GET":
        return _event_detail_ui(
            compatibility,
            _cloud_query(compatibility, f"event-lines/{unquote(event_match.group(1))}"),
        )
    if event_match and request.method in {"PATCH", "DELETE"}:
        event_line_id = unquote(event_match.group(1))
        payload = {
            **body,
            "expectedVersion": _expected(
                compatibility,
                body,
                event_line_id=event_line_id,
            ),
        }
        result = _cloud_command(
            compatibility,
            request,
            request.method,
            f"event-lines/{event_line_id}",
            payload,
        )
        if request.method == "DELETE":
            return {"status": "archived", "counts": {}}
        return _event_ui(compatibility, result.get("eventLine") or {})
    transition_match = re.fullmatch(r"event-lines/([^/]+)/(close|reopen|reparent)", path)
    if request.method == "POST" and transition_match:
        event_line_id, action = [unquote(value) for value in transition_match.groups()]
        payload = {
            **body,
            "expectedVersion": _expected(
                compatibility,
                body,
                event_line_id=event_line_id,
            ),
        }
        result = _cloud_command(
            compatibility,
            request,
            "POST",
            f"event-lines/{event_line_id}?action="
            + {"close": "closed", "reopen": "reopened", "reparent": "reparented"}[action],
            payload,
        )
        line = _event_ui(compatibility, result.get("eventLine") or {})
        return line if action == "reparent" else {"status": line["status"], "version": line["version"]}
    link_match = re.fullmatch(
        r"event-lines/([^/]+)/tasks/([^/]+)/(link|milestone)",
        path,
    )
    if link_match:
        event_line_id, task_id, action = [unquote(value) for value in link_match.groups()]
        payload = {
            **body,
            "expectedVersion": _expected(
                compatibility,
                body,
                event_line_id=event_line_id,
            ),
        }
        result = _cloud_command(
            compatibility,
            request,
            "PATCH" if action == "milestone" else "POST",
            f"event-lines/{event_line_id}/tasks/{task_id}",
            payload,
        )
        return {
            "eventLine": _event_ui(compatibility, result.get("eventLine") or {}),
            "task": _task_ui(compatibility, result.get("task") or {}),
            "activity": None,
        }
    reparent_preview = re.fullmatch(r"event-lines/([^/]+)/reparent-preview", path)
    if request.method == "GET" and reparent_preview:
        event_line_id = unquote(reparent_preview.group(1))
        detail = _cloud_query(compatibility, f"event-lines/{event_line_id}")
        target_id = request.query.get("targetClientId")
        project = next(
            (
                item
                for item in compatibility._snapshot().get("projects") or []  # noqa: SLF001
                if item.get("projectId") == target_id
            ),
            None,
        )
        if project is None:
            raise LocalRuntimeError(404, "project_missing", "目标项目不存在")
        return {
            "eventLineId": event_line_id,
            "fromClientId": (detail.get("eventLine") or {}).get("projectId"),
            "targetClientId": target_id,
            "targetClientName": project.get("name"),
            "affectedTaskCount": len(detail.get("tasks") or []),
            "expectedVersion": (detail.get("eventLine") or {}).get("version"),
        }
    task_candidates = re.fullmatch(r"event-lines/([^/]+)/task-candidates", path)
    if request.method == "GET" and task_candidates:
        event_line_id = unquote(task_candidates.group(1))
        detail = _cloud_query(compatibility, f"event-lines/{event_line_id}")
        project_id = (detail.get("eventLine") or {}).get("projectId")
        term = _text(request.query.get("q")).lower()
        limit = max(1, min(200, int(request.query.get("limit") or 40)))
        return [
            {
                "taskId": task["id"],
                "title": task["title"],
                "clientId": task.get("clientId"),
                "clientName": task.get("clientName"),
                "linkedEventLineId": task.get("eventLineId"),
                "status": task.get("status"),
                "version": task.get("_strictVersion"),
            }
            for task in _board(compatibility)["tasks"]
            if (
                request.query.get("scope") == "organization"
                or task.get("clientId") == project_id
            )
            and (not term or term in _text(task.get("title")).lower())
        ][:limit]
    report_artifacts = re.fullmatch(r"event-lines/([^/]+)/report-artifacts", path)
    if request.method == "GET" and report_artifacts:
        return _event_report_artifacts(
            compatibility,
            unquote(report_artifacts.group(1)),
        )
    event_analysis = re.fullmatch(
        r"event-lines/([^/]+)/(report-snapshot|report-draft|timeline-narrative|"
        r"readiness-analysis|goal-polish|background-draft|clarification-draft|"
        r"timeline-narrative/regenerate|retry-sync)",
        path,
    )
    if event_analysis:
        event_line_id, kind = [unquote(value) for value in event_analysis.groups()]
        detail = _cloud_query(compatibility, f"event-lines/{event_line_id}")
        line = _event_ui(compatibility, detail.get("eventLine") or {})
        if kind == "report-snapshot":
            tasks = [
                _task_ui(compatibility, task)
                for task in detail.get("tasks") or []
            ]
            activities = list(detail.get("activities") or [])
            attachments = list(detail.get("attachments") or [])
            participant_names = sorted(
                {
                    name
                    for name in [
                        *[
                            _text(activity.get("actorName"))
                            for activity in activities
                        ],
                        *[
                            _text(task.get("ownerName"))
                            for task in tasks
                        ],
                    ]
                    if name
                }
            )
            return {
                "eventLine": line,
                "tasks": tasks,
                "activities": activities,
                "attachments": attachments,
                "timelineNodes": [],
                "participantNames": participant_names,
                "snapshotAt": _now(),
                "canEdit": line.get("status") not in {"archived", "done"},
                "sourceState": "cloud_ready",
                "readOnlyReason": (
                    "事件线已归档，只能查看"
                    if line.get("status") in {"archived", "done"}
                    else None
                ),
                "taskMirrorStatus": "ready",
                "taskMirrorError": None,
            }
        if kind == "report-draft":
            artifacts = _event_report_artifacts(
                compatibility,
                event_line_id,
            )
            return artifacts[0] if artifacts else None
        if kind in {"timeline-narrative", "timeline-narrative/regenerate"}:
            activities = list(detail.get("activities") or [])
            nodes = [
                {
                    "id": _text(activity.get("id"))
                    or f"{event_line_id}:activity:{index}",
                    "time": (
                        activity.get("happenedAt")
                        or activity.get("createdAt")
                        or activity.get("updatedAt")
                        or _now()
                    ),
                    "title": activity.get("title") or f"进展 {index + 1}",
                    "narrative": activity.get("summary") or "",
                    "confidence": "medium",
                    "linkedTaskIds": (
                        [activity.get("taskId")]
                        if activity.get("taskId")
                        else []
                    ),
                    "linkedActivityIds": (
                        [activity.get("id")]
                        if activity.get("id")
                        else []
                    ),
                    "linkedAttachmentIds": [],
                    "evidenceSummary": activity.get("summary") or "",
                    "evidenceGaps": [],
                }
                for index, activity in enumerate(activities)
            ]
            has_material = bool(nodes)
            return {
                "eventLineId": event_line_id,
                "rev": max(1, int(line.get("version") or 1)),
                "headline": line.get("name") or "事件线",
                "opening": line.get("summary") or "",
                "closing": (
                    _text(nodes[-1].get("narrative"))
                    if nodes
                    else "当前事件线还没有可生成叙事的进展记录。"
                ),
                "nodes": nodes,
                "overallConfidence": 0.7 if has_material else 0.0,
                "generator": "strict_v2_event_line_adapter",
                "modelName": "",
                "updatedAt": _now(),
                "triggeredByDisplayName": None,
                "outputKind": "material_overview",
                "sourceSetId": None,
                "eventLineVersion": max(1, int(line.get("version") or 1)),
                "milestoneTaskIds": [],
                "isStale": False,
                "formalReady": has_material,
                "missingRequirements": (
                    [] if has_material else ["尚无事件线进展记录"]
                ),
                "availabilityStatus": "ready" if has_material else "blocked",
                "availabilityReason": (
                    None if has_material else "尚无事件线进展记录"
                ),
                "staleReasons": [],
            }
        if kind == "readiness-analysis":
            return {
                "level": line["readinessLevel"],
                "missingItems": line["readinessMissingItems"],
                "ready": not line["readinessMissingItems"],
                "eventLineId": event_line_id,
            }
        if kind == "goal-polish":
            return {"text": _text(body.get("text")) or line.get("intent") or "", "source": "authority"}
        if kind == "background-draft":
            return {"text": line.get("summary") or _text(body.get("instruction")), "source": "authority"}
        if kind == "clarification-draft":
            return {
                "summary": line.get("summary") or _text(body.get("conversationText")),
                "stage": "",
                "intent": line.get("intent") or "",
                "questions": line["readinessMissingItems"],
            }
        compatibility._snapshot(refresh=True)  # noqa: SLF001
        return {"status": line["status"], "syncStatus": "synced", "lastSyncError": None}
    event_attachment_upload = re.fullmatch(r"event-lines/([^/]+)/attachments", path)
    if request.method == "POST" and event_attachment_upload:
        event_line_id = unquote(event_attachment_upload.group(1))
        result = _cloud_command(
            compatibility,
            request,
            "POST",
            f"event-lines/{event_line_id}/attachments",
            _attachment_upload_payload(
                body,
                expected_version=_event_version(compatibility, event_line_id),
                default_source_kind="event_line_attachment",
            ),
        )
        return result.get("attachment") or {}
    event_attachment_retry = re.fullmatch(
        r"event-lines/([^/]+)/attachments/([^/]+)/retry-parse",
        path,
    )
    if request.method == "POST" and event_attachment_retry:
        event_line_id, attachment_id = [
            unquote(value) for value in event_attachment_retry.groups()
        ]
        detail = _cloud_query(compatibility, f"event-lines/{event_line_id}")
        attachment = next(
            (
                item
                for item in detail.get("attachments") or []
                if item.get("eventLineAttachmentId") == attachment_id
            ),
            None,
        )
        if attachment is None:
            raise LocalRuntimeError(404, "event_line_attachment_missing", "事件线附件不存在")
        return _cloud_command(
            compatibility,
            request,
            "POST",
            f"event-lines/{event_line_id}/attachments/{attachment_id}/retry-parse",
            {"expectedVersion": int(attachment.get("version") or 1)},
        )
    event_attachment_retry_all = re.fullmatch(
        r"event-lines/([^/]+)/attachments/retry-failed",
        path,
    )
    if request.method == "POST" and event_attachment_retry_all:
        event_line_id = unquote(event_attachment_retry_all.group(1))
        detail = _cloud_query(compatibility, f"event-lines/{event_line_id}")
        failed = [
            item
            for item in detail.get("attachments") or []
            if item.get("parseStatus") == "failed"
        ]
        processed = 0
        failed_count = 0
        for attachment in failed:
            outcome = compatibility.runtime.cloud_command(
                "POST",
                (
                    f"/api/v2/workflow/event-lines/{event_line_id}/attachments/"
                    f"{attachment['eventLineAttachmentId']}/retry-parse"
                ),
                payload={"expectedVersion": int(attachment.get("version") or 1)},
                idempotency_key=(
                    f"{request.idempotency_key}:"
                    f"{attachment['eventLineAttachmentId']}"
                ),
            )
            if (
                outcome.get("status") == "completed"
                and outcome.get("state") == "ready"
            ):
                processed += 1
            else:
                failed_count += 1
        return {
            "status": (
                "failed"
                if failed_count and not processed
                else "partial"
                if failed_count
                else "completed"
            ),
            "queuedCount": 0,
            "processedCount": processed,
            "failedCount": failed_count,
            "skippedCount": len(detail.get("attachments") or []) - len(failed),
        }
    merge_match = re.fullmatch(r"event-lines/([^/]+)/(merge|merge-preview)", path)
    if request.method == "POST" and merge_match:
        target_id, action = [unquote(value) for value in merge_match.groups()]
        preview = _cloud_command(
            compatibility,
            request,
            "POST",
            f"event-lines/{target_id}/merge-preview",
            {"sourceIds": body.get("sourceIds") or []},
        )
        if action == "merge-preview":
            return preview
        result = _cloud_command(
            compatibility,
            request,
            "POST",
            f"event-lines/{target_id}/merge",
            {
                "expectedVersion": preview["targetVersion"],
                "sourceIds": preview["sourceIds"],
                "sourceExpectedVersions": preview["sourceExpectedVersions"],
            },
        )
        return _event_ui(compatibility, result.get("eventLine") or {})
    legacy_report_runs = re.fullmatch(
        r"event-lines/([^/]+)/legacy-report-runs",
        path,
    )
    if request.method == "GET" and legacy_report_runs:
        return [
            _legacy_report_run(artifact)
            for artifact in _event_report_artifacts(
                compatibility,
                unquote(legacy_report_runs.group(1)),
            )
        ]

    plan_link_match = re.fullmatch(r"tasks/([^/]+)/plan-link(?:/recompute)?", path)
    if plan_link_match:
        task_id = unquote(plan_link_match.group(1))
        strict_path = f"/api/v2/gc06/tasks/{task_id}/plan-link"
        if request.method == "GET":
            return compatibility.runtime.cloud_query(strict_path).get("planLink")
        if path.endswith("/recompute"):
            return compatibility.runtime.cloud_query(strict_path).get("planLink")
        return compatibility.runtime.cloud_command(
            "PATCH",
            strict_path,
            payload=body,
            idempotency_key=request.idempotency_key,
        ).get("planLink")
    plan_tasks = re.fullmatch(r"org-model/plan-items/([^/]+)/tasks", path)
    if request.method == "GET" and plan_tasks:
        payload = compatibility.runtime.cloud_query(
            "/api/v2/gc06/plan-item-tasks",
            query={"planItemId": unquote(plan_tasks.group(1))},
        )
        # GC-06 returns the same strict GC-04 task authority rows used by the
        # task board.  Do not route those rows through the legacy presentation
        # adapter: it reads the frozen business snapshot and makes an otherwise
        # valid action→task relation appear unavailable.
        return [_strict_task_ui(task) for task in payload.get("tasks") or []]
    if request.method == "GET" and path == "org-model/plan-item-task-counts":
        return compatibility.runtime.cloud_query(
            "/api/v2/gc06/plan-item-tasks"
        ).get("counts") or {}
    if request.method == "POST" and path == "plan-link/predict-from-text":
        title = _text(body.get("title")).lower()
        description = _text(body.get("description")).lower()
        exact = next(
            (
                item
                for item in body.get("planItems") or []
                if _text(item.get("title")).lower()
                and _text(item.get("title")).lower() in f"{title}\n{description}"
            ),
            None,
        )
        return {
            "planItemId": (exact or {}).get("id"),
            "confidence": 1 if exact else 0,
            "model": "deterministic-exact-match-v2",
            "reason": "任务文本包含完整计划项标题" if exact else "未发现可确定的计划项",
        }
    if request.method == "POST" and path == "org-model/plans/parse":
        text = _text(body.get("text"))
        if not text:
            raise LocalRuntimeError(422, "plan_parse_text_required", "请先粘贴要解析的计划原文")
        completion = compatibility.runtime.organization_ai_completion(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是益语智库的任务计划 Agent。把用户提供的组织或部门计划原文整理成可编辑计划项。"
                        "不要把章节标题、日期、空行或列表序号单独当成计划项；合并属于同一事项的多行说明；"
                        "不得补造原文没有的目标、数字或承诺。只返回纯 JSON："
                        '{"summary":"不超过120字的计划总述","confidence":"low|medium|high",'
                        '"items":[{"title":"简洁行动标题","statement":"原文依据和必要说明",'
                        '"expectedOutput":"原文明示的交付物；没有则空字符串"}]}。最多30项。'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"组织：{_text(body.get('organizationName')) or '未填写'}\n"
                        f"计划主体：{_text(body.get('scopeName')) or '未填写'}\n"
                        f"周期类型：{_text(body.get('cycleType')) or 'custom'}\n"
                        f"周期：{_text(body.get('periodKey')) or '未填写'}\n\n"
                        f"计划原文：\n{text[:30_000]}"
                    ),
                },
            ],
            temperature=0.1,
            read_timeout_seconds=60.0,
        )
        parsed = _json_object_from_model(_text(completion.get("content")))
        raw_items = parsed.get("items") if isinstance(parsed.get("items"), list) else []
        items = []
        for raw in raw_items[:30]:
            if not isinstance(raw, Mapping):
                continue
            title = _text(raw.get("title"))[:300]
            if not title:
                continue
            items.append(
                {
                    "title": title,
                    "statement": _text(raw.get("statement"))[:4_000],
                    "expectedOutput": _text(raw.get("expectedOutput"))[:2_000],
                }
            )
        confidence = _text(parsed.get("confidence")).lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium" if items else "low"
        provider = dict(completion.get("provider") or {})
        return {
            "items": items,
            "summary": _text(parsed.get("summary"))[:1000],
            "confidence": confidence,
            "agentRun": {
                "agentKind": "task_planning",
                "state": "completed",
                "stage": "plan_draft_parsed",
                "message": "已把原文整理为可编辑草稿",
            },
            "modelName": provider.get("modelName"),
        }
    agent_plan_match = re.fullmatch(
        r"tasks/agent-weekly-plans/([^/]+)/([^/]+)",
        path,
    )
    if request.method == "PUT" and agent_plan_match:
        week_label, agent_key = [
            unquote(value) for value in agent_plan_match.groups()
        ]
        existing = _cloud_query(
            compatibility,
            "agent-weekly-plans",
            {"weekLabel": week_label, "agentKey": agent_key},
        ).get("weeklyPlans") or []
        payload = {
            **body,
            "weekLabel": week_label,
            "agentKey": agent_key,
        }
        if existing and "expectedVersion" not in payload:
            payload["expectedVersion"] = existing[0]["version"]
        return _cloud_command(
            compatibility,
            request,
            "PUT",
            f"agent-weekly-plans/{week_label}/{agent_key}",
            payload,
        ).get("weeklyPlan")

    if request.method == "GET" and path == "reviews":
        return _review_dashboard(compatibility, request.query)
    if request.method == "GET" and path == "reviews/history":
        payload = _cloud_query(compatibility, "reviews")
        return {
            "items": [
                {
                    "weekLabel": item.get("weekLabel"),
                    "submittedAt": item.get("updatedAt"),
                    "workItemCount": sum(
                        1
                        for link in item.get("taskLinks") or []
                        if (
                            link.get("contentDomain")
                            or (link.get("structuredNote") or {}).get(
                                "contentDomain",
                                "work",
                            )
                        )
                        == "work"
                    ),
                    "personalItemCount": sum(
                        1
                        for link in item.get("taskLinks") or []
                        if (
                            link.get("contentDomain")
                            or (link.get("structuredNote") or {}).get(
                                "contentDomain"
                            )
                        )
                        == "personal"
                    ),
                    "version": item.get("version"),
                }
                for item in payload.get("reviews") or []
            ]
        }
    if request.method == "POST" and path in {"reviews/weekly", "reviews/weekly/draft"}:
        payload = dict(body)
        existing = _cloud_query(
            compatibility,
            "reviews",
            {"weekLabel": _text(body.get("weekLabel"))},
        ).get("reviews") or []
        if existing and "expectedVersion" not in payload:
            payload["expectedVersion"] = int(existing[0].get("version") or 1)
        _cloud_command(
            compatibility,
            request,
            "POST",
            "reviews/weekly/draft" if path.endswith("/draft") else "reviews/weekly",
            payload,
        )
        return _review_dashboard(
            compatibility,
            {"weekLabel": _text(body.get("weekLabel"))},
        )
    if request.method == "GET" and path == "reviews/department-signals":
        payload = _cloud_query(compatibility, "reviews", request.query)
        reviews = payload.get("reviews") or []
        return {
            "weekLabel": request.query.get("weekLabel") or "",
            "signals": [
                {
                    "membershipId": item.get("membershipId"),
                    "workBlocker": item.get("workBlocker"),
                    "supportNeeded": item.get("supportNeeded"),
                    "nextWeekFocus": item.get("nextWeekFocus"),
                }
                for item in reviews
            ],
            "generatedAt": _now(),
        }
    if request.method == "GET" and path == "reviews/clients-pulse":
        return _cloud_query(compatibility, "clients-pulse")
    if request.method == "GET" and path == "reviews/dashboard/drill-target":
        target_id = request.query.get("targetId")
        board = _board(compatibility)
        return {
            "targetType": request.query.get("targetType"),
            "targetId": target_id,
            "targetLabel": request.query.get("targetLabel") or "",
            "tasks": [
                task
                for task in board["tasks"]
                if task["id"] == target_id
                or task.get("clientId") == target_id
                or task.get("eventLineId") == target_id
            ],
        }
    if path in {
        "reviews/weekly-overview/refresh",
        "reviews/weekly-overview/status",
    }:
        dashboard = _review_dashboard(
            compatibility,
            {
                key: _text(value)
                for key, value in {**request.query, **body}.items()
                if _text(value)
            },
        )
        user = (
            (compatibility.auth_state().get("user") or {})
            if hasattr(compatibility, "auth_state")
            else {}
        )
        generated_at = _now() if request.method == "POST" else None
        return {
            "status": "succeeded" if request.method == "POST" else "idle",
            "weekLabel": dashboard.get("weekLabel") or "",
            "perspective": dashboard.get("activePerspective") or "mine",
            "departmentId": (
                request.query.get("departmentId")
                or body.get("departmentId")
                or None
            ),
            "viewerUserId": _text(
                user.get("id") or user.get("membershipId")
            ),
            "startedAt": generated_at,
            "generatedAt": generated_at,
            "sourceCounts": {
                "reviews": int(dashboard.get("reviewCount") or 0),
                "workItems": len(dashboard.get("workItems") or []),
                "personalItems": len(dashboard.get("personalItems") or []),
                "plans": len(dashboard.get("plans") or []),
            },
            "cacheKey": (
                f"{dashboard.get('weekLabel') or ''}:"
                f"{dashboard.get('activePerspective') or 'mine'}:"
                f"{request.query.get('departmentId') or body.get('departmentId') or ''}"
            ),
        }

    task_attachment_upload = re.fullmatch(r"tasks/([^/]+)/attachments", path)
    task_markdown_upload = re.fullmatch(
        r"tasks/([^/]+)/attachments/from-markdown",
        path,
    )
    if request.method == "POST" and (task_attachment_upload or task_markdown_upload):
        selected = task_attachment_upload or task_markdown_upload
        task_id = unquote(selected.group(1))
        result = _cloud_command(
            compatibility,
            request,
            "POST",
            f"tasks/{task_id}/attachments",
            _attachment_upload_payload(
                body,
                expected_version=_task_version(compatibility, task_id),
                markdown=task_markdown_upload is not None,
            ),
        )
        return _task_ui(compatibility, result.get("task") or {})
    task_recording = re.fullmatch(r"tasks/([^/]+)/recordings", path)
    if request.method == "POST" and task_recording:
        task_id = unquote(task_recording.group(1))
        result = _cloud_command(
            compatibility,
            request,
            "POST",
            f"tasks/{task_id}/attachments",
            _recording_upload_payload(
                compatibility,
                body,
                expected_version=_task_version(compatibility, task_id),
            ),
        )
        return _task_ui(compatibility, result.get("task") or {})
    task_attachment_delete = re.fullmatch(
        r"tasks/([^/]+)/attachments/([^/]+)",
        path,
    )
    if request.method == "DELETE" and task_attachment_delete:
        task_id, attachment_id = [
            unquote(value) for value in task_attachment_delete.groups()
        ]
        return _cloud_command(
            compatibility,
            request,
            "DELETE",
            f"tasks/{task_id}/attachments/{attachment_id}",
            {
                "expectedVersion": _task_version(compatibility, task_id),
                "syncKnowledge": request.query.get("syncKnowledge") == "true",
            },
        )
    task_attachment_retry = re.fullmatch(
        r"tasks/([^/]+)/attachments/([^/]+)/retry-transcription",
        path,
    )
    if request.method == "POST" and task_attachment_retry:
        task_id, attachment_id = [
            unquote(value) for value in task_attachment_retry.groups()
        ]
        detail = _cloud_query(compatibility, f"tasks/{task_id}")
        attachment = next(
            (
                item
                for item in (detail.get("task") or {}).get("attachments") or []
                if item.get("id") == attachment_id
            ),
            None,
        )
        if attachment is None:
            raise LocalRuntimeError(404, "task_attachment_missing", "任务附件不存在")
        model_root = (
            compatibility.runtime.database_path.resolve().parent / "models"
        )
        if not model_ready(model_root, SENSE_VOICE_MODEL):
            raise LocalRuntimeError(
                409,
                "local_asr_model_missing",
                "请先在语音设置中下载本机转写模型，再重试该附件",
            )
        source = _cloud_query(
            compatibility,
            f"tasks/{task_id}/attachments/{attachment_id}/content",
        )
        try:
            raw = base64.b64decode(
                str(source.get("contentBase64") or ""),
                validate=True,
            )
        except (binascii.Error, ValueError, TypeError) as exc:
            raise LocalRuntimeError(
                502,
                "task_attachment_content_invalid",
                "组织云返回的附件正文无法校验",
            ) from exc
        if (
            len(raw) != int(source.get("byteSize") or -1)
            or hashlib.sha256(raw).hexdigest()
            != _text(source.get("contentHash"))
        ):
            raise LocalRuntimeError(
                502,
                "task_attachment_content_hash_mismatch",
                "组织云附件正文校验失败，未执行本机转写",
            )
        suffix = Path(_text(source.get("fileName"))).suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
            suffix = ".audio"
        temporary_root = (
            compatibility.runtime.database_path.resolve().parent
            / "recordings"
            / "workflow-transcription"
        )
        temporary_root.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            suffix=suffix,
            dir=temporary_root,
        )
        temporary = Path(temporary_name)
        try:
            with open(descriptor, "wb", closefd=True) as stream:
                stream.write(raw)
                stream.flush()
            outcome = run_recording_transcription(
                model_root,
                str(temporary),
                language=_text(request.body.get("language")) or "auto",
            )
            transcript_text = (
                outcome.dialogue_text.strip()
                or outcome.result.text.strip()
            )
            if not transcript_text:
                raise LocalRuntimeError(
                    502,
                    "local_asr_transcript_empty",
                    "本机模型没有识别出可保存文本",
                )
            _cloud_command(
                compatibility,
                request,
                "POST",
                (
                    f"tasks/{task_id}/attachments/{attachment_id}/"
                    "transcription-complete"
                ),
                {
                    "expectedVersion": int(source.get("version") or 1),
                    "text": transcript_text,
                    "modelName": outcome.result.model_name,
                    "language": outcome.result.language,
                    "segmentCount": len(outcome.result.segments),
                },
            )
        except LocalRuntimeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LocalRuntimeError(
                502,
                "local_asr_execution_failed",
                f"本机转写失败，可重试：{exc.__class__.__name__}",
            ) from exc
        finally:
            temporary.unlink(missing_ok=True)
        return _task_ui(
            compatibility,
            _cloud_query(compatibility, f"tasks/{task_id}").get("task") or {},
        )
    task_transcript = re.fullmatch(
        r"tasks/([^/]+)/attachments/([^/]+)/transcript",
        path,
    )
    if task_transcript and request.method in {"GET", "PUT"}:
        task_id, attachment_id = [
            unquote(value) for value in task_transcript.groups()
        ]
        if request.method == "GET":
            return _cloud_query(
                compatibility,
                f"tasks/{task_id}/attachments/{attachment_id}/transcript",
            ).get("transcript")
        current = _cloud_query(
            compatibility,
            f"tasks/{task_id}/attachments/{attachment_id}/transcript",
        ).get("transcript") or {}
        return _cloud_command(
            compatibility,
            request,
            "PUT",
            f"tasks/{task_id}/attachments/{attachment_id}/transcript",
            {
                "text": str(body.get("text") or ""),
                "expectedVersion": _task_version(compatibility, task_id),
                "expectedTranscriptVersion": int(current.get("version") or 0),
            },
        ).get("transcript")
    task_prep_proposal = re.fullmatch(
        r"tasks/([^/]+)/prep-pack/proposals",
        path,
    )
    if request.method == "POST" and task_prep_proposal:
        task_id = unquote(task_prep_proposal.group(1))
        context = _task_context_with_local_knowledge(compatibility, task_id)
        task = dict(context.get("task") or {})
        project_id = _text(task.get("projectId"))
        if not project_id:
            raise LocalRuntimeError(
                422,
                "task_project_required",
                "任务未关联项目，无法创建项目准备提案",
            )
        sources = list(context.get("sources") or [])
        created = _root_command(
            compatibility,
            request,
            "POST",
            f"workbench/projects/{project_id}/proposal-drafts",
            {
                "kind": "task_prep",
                "title": f"任务准备提案：{_text(task.get('title')) or task_id}",
                "summary": (
                    _text(context.get("brief"))
                    or _text(task.get("description"))
                    or _text(task.get("title"))
                ),
                "rationale": "由当前任务及固定项目 WorkspaceContext 显式生成，需审批后执行",
                "riskLevel": "low",
                "targetRefs": [
                    {
                        "targetType": "task",
                        "targetId": task_id,
                        "label": _text(task.get("title")) or task_id,
                    }
                ],
                "sourceRefs": [
                    (
                        f"{_text(source.get('type')) or 'context'}:"
                        f"{_text(source.get('id'))}@{int(source.get('version') or 1)}"
                    )
                    for source in sources
                    if _text(source.get("id"))
                ],
                "boundaryNotes": [
                    "只引用当前组织云权威摘要与当前 sandbox 本机私有摘要。",
                    "本机源文件路径和正文未写入组织云提案。",
                ],
                "scopeType": "client",
                "scopeId": project_id,
                "payload": {
                    "taskId": task_id,
                    "materialPackHash": context.get("materialPackHash"),
                    "taskDrafts": [],
                },
            },
        )
        proposal_id = _text(created.get("proposalId") or created.get("id"))
        if not proposal_id:
            raise LocalRuntimeError(
                502,
                "proposal_receipt_invalid",
                "组织云创建提案后未返回权威 ID",
            )
        return _root_query(
            compatibility,
            "intelligence-growth/query",
            query={"resourcePath": f"proposals/{proposal_id}"},
        )

    compatibility._not_connected(path)  # noqa: SLF001


def _requires_pinned_workspace(method: str, path: str) -> bool:
    del method, path
    return True


def _dispatch(
    compatibility: Any,
    request: UiRequest,
    match: re.Match[str],
) -> Any:
    if not _requires_pinned_workspace(request.method, request.path):
        return _dispatch_unpinned(compatibility, request, match)
    pin = getattr(compatibility.runtime, "pinned_workspace_context", None)
    if pin is None:
        return _dispatch_unpinned(compatibility, request, match)
    with pin():
        return _dispatch_unpinned(compatibility, request, match)


_ROUTES = (
    ("GET", r"event-lines"),
    ("POST", r"event-lines"),
    ("GET", r"event-lines/([^/]+)/legacy-report-runs"),
    ("GET", r"event-lines/([^/]+)/report-artifacts"),
    ("POST", r"event-lines/([^/]+)/reparent"),
    ("GET", r"event-lines/([^/]+)/reparent-preview"),
    ("GET", r"event-lines/([^/]+)/report-draft"),
    ("POST", r"event-lines/([^/]+)/attachments"),
    ("POST", r"event-lines/([^/]+)/attachments/([^/]+)/retry-parse"),
    ("POST", r"event-lines/([^/]+)/attachments/retry-failed"),
    ("POST", r"event-lines/([^/]+)/tasks/([^/]+)/link"),
    ("PATCH", r"event-lines/([^/]+)/tasks/([^/]+)/milestone"),
    ("DELETE", r"event-lines/([^/]+)"),
    ("GET", r"event-lines/([^/]+)"),
    ("PATCH", r"event-lines/([^/]+)"),
    ("POST", r"event-lines/([^/]+)/background-draft"),
    ("POST", r"event-lines/([^/]+)/clarification-draft"),
    ("POST", r"event-lines/([^/]+)/close"),
    ("POST", r"event-lines/([^/]+)/goal-polish"),
    ("POST", r"event-lines/([^/]+)/readiness-analysis"),
    ("POST", r"event-lines/([^/]+)/reopen"),
    ("GET", r"event-lines/([^/]+)/report-snapshot"),
    ("POST", r"event-lines/([^/]+)/retry-sync"),
    ("GET", r"event-lines/([^/]+)/task-candidates"),
    ("GET", r"event-lines/([^/]+)/timeline-narrative"),
    ("POST", r"event-lines/([^/]+)/timeline-narrative/regenerate"),
    ("POST", r"event-lines/([^/]+)/merge"),
    ("POST", r"event-lines/([^/]+)/merge-preview"),
    ("GET", r"meetings/([^/]+)/page-context"),
    ("GET", r"org-model/plan-item-task-counts"),
    ("GET", r"org-model/plan-items/([^/]+)/tasks"),
    ("POST", r"org-model/plans/parse"),
    ("POST", r"plan-link/predict-from-text"),
    ("GET", r"reviews"),
    ("GET", r"reviews/clients-pulse"),
    ("GET", r"reviews/dashboard/drill-target"),
    ("GET", r"reviews/department-signals"),
    ("GET", r"reviews/history"),
    ("POST", r"reviews/weekly"),
    ("POST", r"reviews/weekly-overview/refresh"),
    ("GET", r"reviews/weekly-overview/status"),
    ("POST", r"reviews/weekly/draft"),
    ("POST", r"task-lists"),
    ("DELETE", r"task-lists/([^/]+)"),
    ("PATCH", r"task-lists/([^/]+)"),
    ("POST", r"task-tags"),
    ("DELETE", r"task-tags/([^/]+)"),
    ("PATCH", r"task-tags/([^/]+)"),
    ("GET", r"task-views"),
    ("GET", r"tasks"),
    ("POST", r"tasks"),
    ("GET", r"tasks/([^/]+)/page-context"),
    ("POST", r"tasks/([^/]+)/smart-brief-actions/([^/]+)/adopt"),
    ("DELETE", r"tasks/([^/]+)"),
    ("PATCH", r"tasks/([^/]+)"),
    ("POST", r"tasks/([^/]+)/complete-with-review"),
    ("POST", r"tasks/([^/]+)/confirm"),
    ("POST", r"tasks/([^/]+)/note"),
    ("POST", r"tasks/([^/]+)/reject"),
    ("POST", r"tasks/([^/]+)/review/approve"),
    ("POST", r"tasks/([^/]+)/review/return"),
    ("POST", r"tasks/([^/]+)/recordings"),
    ("POST", r"tasks/([^/]+)/attachments"),
    ("DELETE", r"tasks/([^/]+)/attachments/([^/]+)"),
    ("POST", r"tasks/([^/]+)/attachments/([^/]+)/retry-transcription"),
    ("GET", r"tasks/([^/]+)/attachments/([^/]+)/transcript"),
    ("PUT", r"tasks/([^/]+)/attachments/([^/]+)/transcript"),
    ("POST", r"tasks/([^/]+)/attachments/from-markdown"),
    ("GET", r"tasks/([^/]+)/context-brief"),
    ("GET", r"tasks/([^/]+)/context-preview"),
    ("GET", r"tasks/([^/]+)/plan-link"),
    ("PATCH", r"tasks/([^/]+)/plan-link"),
    ("POST", r"tasks/([^/]+)/plan-link/recompute"),
    ("GET", r"tasks/([^/]+)/prep-pack"),
    ("POST", r"tasks/([^/]+)/prep-pack/proposals"),
    ("GET", r"tasks/([^/]+)/smart-brief"),
    ("GET", r"tasks/([^/]+)/understanding"),
    ("GET", r"tasks/agent-execution"),
    ("PUT", r"tasks/agent-weekly-plans/([^/]+)/([^/]+)"),
    ("GET", r"tasks/agent-worklogs"),
    ("POST", r"tasks/ai-parse"),
    ("POST", r"tasks/collaboration/batch-handle"),
    ("POST", r"tasks/context-briefs/batch"),
    ("POST", r"tasks/smart-briefs"),
)


for _method, _pattern in _ROUTES:
    router.route(_method, _pattern)(_dispatch)
