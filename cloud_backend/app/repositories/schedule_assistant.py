"""Grounded schedule assistant shared by desktop and mobile clients.

The model is deliberately limited to ranking task ids. Every displayed fact,
count, role and risk is rebuilt from the authenticated task projection.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import httpx

from strict_common.ids import utc_now

from ..repository import CloudRepository, SessionIdentity
from .gc04_tasks import GC04TaskRepository


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TRADITIONAL_FOLD = str.maketrans(
    {
        "樂": "乐", "與": "与", "協": "协", "會": "会", "務": "务",
        "這": "这", "個": "个", "關": "关", "聯": "联", "開": "开",
        "優": "优", "級": "级", "裡": "里", "時": "时", "間": "间",
    }
)
_PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
_RISK_RECOMMENDATIONS = {
    "time_conflict": "确认冲突事项的先后顺序，必要时调整其中一项时间。",
    "overdue": "确认当前进度，并补充新的完成时间。",
    "missing_owner": "先明确一位负责人，再安排下一步。",
    "unscheduled_high_priority": "尽快补充开始或截止时间。",
}


def _fold(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).translate(_TRADITIONAL_FOLD).strip()


def _datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_SHANGHAI)
    return parsed.astimezone(_SHANGHAI)


def _deadline_datetime(value: Any) -> datetime | None:
    """Interpret a date-only deadline as the end of that local day."""

    text = str(value or "").strip()
    parsed = _datetime(text)
    if parsed and re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _people(row: Mapping[str, Any]) -> tuple[str, list[str]]:
    rows = [
        item for item in row.get("collaborators") or []
        if isinstance(item, Mapping)
        and str(item.get("display_name") or "").strip()
        and str(item.get("assignment_state") or "assigned") == "assigned"
    ]
    owner = next(
        (str(item.get("display_name") or "").strip() for item in rows if item.get("role_key") == "owner"),
        "",
    )
    collaborators = [
        str(item.get("display_name") or "").strip()
        for item in rows
        if item.get("role_key") != "owner"
    ]
    return owner, list(dict.fromkeys(collaborators))


def _time_label(row: Mapping[str, Any]) -> str:
    start = _datetime(row.get("scheduled_start_at"))
    end = _datetime(row.get("scheduled_end_at"))
    due_text = str(row.get("due_date") or "").strip()
    due = _deadline_datetime(due_text)
    if start and end:
        return f"{start:%m月%d日 %H:%M}–{end:%H:%M}"
    if start:
        return f"{start:%m月%d日 %H:%M}"
    if due:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", due_text):
            return f"截止 {due:%m月%d日}"
        return f"截止 {due:%m月%d日 %H:%M}"
    return "未设置时间"


def _validated_ranked_ids(value: Any, valid_ids: set[str]) -> list[str]:
    if not isinstance(value, Mapping) or not isinstance(value.get("taskIds"), list):
        raise ValueError("model returned an invalid ranking contract")
    raw_ids = value["taskIds"]
    if any(not isinstance(task_id, str) or task_id not in valid_ids for task_id in raw_ids):
        raise ValueError("model returned ungrounded task ids")
    if len(raw_ids) != len(set(raw_ids)):
        raise ValueError("model returned duplicate task ids")
    if valid_ids and not raw_ids:
        raise ValueError("model returned no grounded task ids")
    return list(raw_ids)


def build_schedule_fact_pack(
    tasks: list[Mapping[str, Any]],
    *,
    question: str,
    viewer_name: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Select visible task facts with deterministic intent and risk rules."""

    current = (now or datetime.now(tz=_SHANGHAI)).astimezone(_SHANGHAI)
    normalized_question = _fold(question)
    normalized_viewer = _fold(viewer_name)
    pending = [
        row for row in tasks
        if isinstance(row, Mapping)
        and row.get("id")
        and not row.get("completed_at")
        and str(row.get("progress_status") or "todo") not in {"done", "cancelled"}
    ]

    known_people: list[str] = []
    for row in pending:
        owner, collaborators = _people(row)
        for name in [owner, *collaborators]:
            if name and _fold(name) not in {_fold(item) for item in known_people}:
                known_people.append(name)

    positions: list[tuple[int, str]] = []
    if normalized_viewer and "我" in normalized_question:
        positions.append((normalized_question.index("我"), viewer_name))
    for name in known_people:
        index = normalized_question.find(_fold(name))
        if index >= 0:
            positions.append((index, name))
    requested_people: list[str] = []
    for _, name in sorted(positions, key=lambda item: item[0]):
        if _fold(name) not in {_fold(item) for item in requested_people}:
            requested_people.append(name)
    # A generic question such as “今天有哪些重点” is still about the signed-in
    # viewer. Never widen it to every organization task the viewer can read.
    if not requested_people and viewer_name:
        requested_people.append(viewer_name)

    named_others = [name for name in requested_people if _fold(name) != normalized_viewer]
    has_joint_word = bool(re.search(r"(?:跟|和|与|共同|一起|协作|合作)", normalized_question))
    collaboration_query = bool(normalized_viewer and named_others and has_joint_word)
    asks_unknown_collaborator = bool(
        normalized_viewer
        and not named_others
        and re.search(r"我\s*(?:跟|和|与)\s*[^，。？！?\s]{1,12}", normalized_question)
    )

    start_of_today = current.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_today = start_of_today + timedelta(days=1)

    def in_scope(row: Mapping[str, Any]) -> bool:
        if "今天" not in normalized_question:
            return True
        start = _datetime(row.get("scheduled_start_at"))
        end = _datetime(row.get("scheduled_end_at")) or start
        due = _deadline_datetime(row.get("due_date"))
        scheduled_today = bool(start and start < end_of_today and (end or start) >= start_of_today)
        return scheduled_today or bool(due and start_of_today <= due < end_of_today)

    def has_people(row: Mapping[str, Any]) -> bool:
        owner, collaborators = _people(row)
        names = {_fold(owner), *(_fold(item) for item in collaborators)} - {""}
        # The task projection exposes this only to the authenticated creator.
        # It is a pending-decision relation, not an owner/collaborator role.
        if row.get("returned_to_creator") is True and normalized_viewer:
            names.add(normalized_viewer)
        if not requested_people:
            return True
        wanted = {_fold(item) for item in requested_people}
        return wanted.issubset(names) if collaboration_query else bool(wanted & names)

    selected = [] if asks_unknown_collaborator else [
        row for row in pending if in_scope(row) and has_people(row)
    ]

    fallback_time = datetime.max.replace(tzinfo=_SHANGHAI)
    selected.sort(
        key=lambda row: (
            _PRIORITY_ORDER.get(str(row.get("priority") or "normal"), 2),
            _datetime(row.get("scheduled_start_at"))
            or _deadline_datetime(row.get("due_date"))
            or fallback_time,
            str(row.get("title") or ""),
        )
    )

    task_items: list[dict[str, Any]] = []
    for row in selected:
        owner, collaborators = _people(row)
        task_items.append(
            {
                "id": str(row.get("id")),
                "title": str(row.get("title") or "未命名任务"),
                "description": str(row.get("description") or "")[:600],
                "priority": str(row.get("priority") or "normal"),
                "status": str(row.get("progress_status") or row.get("status") or "todo"),
                "owner": owner,
                "collaborators": collaborators,
                "timeLabel": _time_label(row),
                "scheduledStartAt": row.get("scheduled_start_at"),
                "scheduledEndAt": row.get("scheduled_end_at"),
                "dueDate": row.get("due_date"),
                "clientId": row.get("client_id"),
                "clientName": str((row.get("client") or {}).get("name") or "")
                if isinstance(row.get("client"), Mapping) else "",
            }
        )

    available_people = []
    for name in known_people:
        if _fold(name) == normalized_viewer:
            continue
        count = sum(
            1 for row in pending
            if _fold(name) in {_fold(_people(row)[0]), *(_fold(item) for item in _people(row)[1])}
        )
        available_people.append({"name": name, "pendingCount": count})
    available_people.sort(key=lambda item: (-int(item["pendingCount"]), str(item["name"])))

    visible_people = requested_people or ([viewer_name] if viewer_name else [])
    people_summary = {
        name: {
            "pendingCount": sum(
                1 for item in task_items
                if _fold(name) in {_fold(item["owner"]), *(_fold(value) for value in item["collaborators"])}
            ),
            "taskIds": [
                item["id"] for item in task_items
                if _fold(name) in {_fold(item["owner"]), *(_fold(value) for value in item["collaborators"])}
            ],
        }
        for name in visible_people
    }

    selected_person = next((name for name in requested_people if _fold(name) != normalized_viewer), "")
    related_tasks = []
    if collaboration_query and selected_person:
        for item in task_items:
            participants = list(dict.fromkeys([item["owner"], *item["collaborators"]]))
            related_tasks.append(
                {
                    "taskId": item["id"], "title": item["title"],
                    "timeLabel": item["timeLabel"], "owner": item["owner"],
                    "participants": [name for name in participants if name],
                    "viewerRole": "负责人" if _fold(item["owner"]) == normalized_viewer else "协作者",
                    "selectedPersonRole": "负责人" if _fold(item["owner"]) == _fold(selected_person) else "协作者",
                }
            )

    risks: list[dict[str, Any]] = []
    scheduled = [
        (item, _datetime(item["scheduledStartAt"]), _datetime(item["scheduledEndAt"]))
        for item in task_items
    ]
    for index, (left, left_start, left_end) in enumerate(scheduled):
        if not left_start or not left_end:
            continue
        left_people = {_fold(left["owner"]), *(_fold(item) for item in left["collaborators"])} - {""}
        for right, right_start, right_end in scheduled[index + 1:]:
            right_people = {_fold(right["owner"]), *(_fold(item) for item in right["collaborators"])} - {""}
            if not right_start or not right_end or not (left_people & right_people):
                continue
            if left_start < right_end and right_start < left_end:
                risks.append({
                    "kind": "time_conflict",
                    "title": f"{left_start:%H:%M}–{left_end:%H:%M} 与 {right_start:%H:%M}–{right_end:%H:%M} 时间重叠",
                    "taskIds": [left["id"], right["id"]],
                })
    for item in task_items:
        end_or_due = _datetime(item["scheduledEndAt"]) or _deadline_datetime(item["dueDate"])
        if end_or_due and end_or_due < current:
            risks.append({"kind": "overdue", "title": f"{item['title']} 已超过计划时间", "taskIds": [item["id"]]})
        if not item["owner"]:
            risks.append({"kind": "missing_owner", "title": f"{item['title']} 尚未设置负责人", "taskIds": [item["id"]]})
        if (
            item["priority"] in {"urgent", "high"}
            and not item["scheduledStartAt"]
        ):
            risks.append({"kind": "unscheduled_high_priority", "title": f"{item['title']} 是高优先级但尚未安排时间", "taskIds": [item["id"]]})

    return {
        "question": question, "viewerName": viewer_name,
        "scope": "today" if "今天" in normalized_question else "upcoming",
        "requestedPeople": requested_people,
        "unknownCollaborator": asks_unknown_collaborator,
        "availablePeople": available_people, "tasks": task_items,
        "relatedTasks": related_tasks, "people": people_summary,
        "risks": risks, "generatedAt": current.isoformat(),
    }


def _reason_for(item: Mapping[str, Any], risks: list[Mapping[str, Any]]) -> tuple[str, str]:
    related = [risk for risk in risks if item.get("id") in (risk.get("taskIds") or [])]
    if related:
        risk = related[0]
        return str(risk.get("title") or "存在待处理风险"), _RISK_RECOMMENDATIONS.get(str(risk.get("kind")), "确认下一步动作。")
    if item.get("priority") in {"urgent", "high"}:
        return "高优先级任务", "确认负责人、时间和下一步交付。"
    if item.get("timeLabel") != "未设置时间":
        return f"已安排在{item['timeLabel']}", "按计划推进并及时更新状态。"
    return "当前待推进", "确认下一步动作与完成时间。"


def build_schedule_answer(
    pack: Mapping[str, Any], *, ranked_ids: list[str] | None = None,
    model_name: str | None = None, model_succeeded: bool = False,
) -> dict[str, Any]:
    tasks = list(pack.get("tasks") or [])
    risks = [
        {**risk, "recommendation": _RISK_RECOMMENDATIONS.get(str(risk.get("kind")), "确认后再处理。")}
        for risk in pack.get("risks") or []
    ]
    task_by_id = {str(item["id"]): item for item in tasks}
    grounded_order = [task_id for task_id in (ranked_ids or []) if task_id in task_by_id]
    grounded_order.extend(task_id for task_id in task_by_id if task_id not in grounded_order)
    ordered = [task_by_id[task_id] for task_id in grounded_order]
    priorities = []
    for item in ordered[:5]:
        why, next_action = _reason_for(item, risks)
        priorities.append({
            "taskId": item["id"], "title": item["title"],
            "timeLabel": item["timeLabel"], "owner": item["owner"],
            "why": why, "nextAction": next_action,
        })
    people = [
        {"name": name, "summary": "按真实负责人和协作关系整理", **details}
        for name, details in (pack.get("people") or {}).items()
    ]
    if not tasks:
        status = "empty"
        summary = "当前可见任务中没有找到符合条件的事项。"
    else:
        status = "success" if model_succeeded else "partial_success"
        summary = f"已核对 {len(tasks)} 项相关任务"
        if risks:
            summary += f"，发现 {len(risks)} 条风险提示。"
        else:
            summary += "，暂无规则命中的风险。"
    return {
        "status": status,
        "mode": "doubao_grounded" if model_succeeded else "local_evidence",
        "summary": summary, "priorities": priorities, "people": people,
        "availablePeople": list(pack.get("availablePeople") or []),
        "relatedTasks": list(pack.get("relatedTasks") or []), "risks": risks,
        "sourceCount": len(tasks), "sourceTaskIds": [item["id"] for item in tasks],
        "modelName": model_name if model_succeeded else None,
        "generatedAt": pack.get("generatedAt") or utc_now(),
        "qualityChecks": {
            "allTaskIdsGrounded": True, "countsComputedByRules": True,
            "conflictsComputedByRules": True,
        },
        "boundaryNote": "本回答仅基于当前账号可见的任务事实，不代表任务已执行或结果已确认。",
    }


class ScheduleAssistantRepository:
    def __init__(
        self,
        repository: CloudRepository,
        *,
        task_repository: Any | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.repository = repository
        self.tasks = task_repository or GC04TaskRepository(repository)
        self._clock = clock or (lambda: datetime.now(tz=_SHANGHAI))

    def _project_context(self, identity: SessionIdentity, tasks: list[Mapping[str, Any]]) -> list[dict[str, str]]:
        context: list[dict[str, str]] = []
        for project_id in list(dict.fromkeys(str(item.get("clientId") or "") for item in tasks))[:3]:
            if not project_id:
                continue
            try:
                knowledge = self.repository.project_knowledge_context(identity, project_id=project_id)
            except Exception:
                continue
            for bucket in knowledge.values() if isinstance(knowledge, Mapping) else []:
                if not isinstance(bucket, list):
                    continue
                for item in bucket[:3]:
                    if not isinstance(item, Mapping):
                        continue
                    summary = str(item.get("summary") or item.get("statement") or "").strip()
                    if summary:
                        context.append({"projectId": project_id, "summary": summary[:400]})
        return context[:8]

    def _rank_with_model(self, identity: SessionIdentity, pack: Mapping[str, Any]) -> tuple[list[str], str]:
        provider = self.repository.ai_config(identity, include_secret=True)
        if provider.get("status") in {None, "not_configured"} or not provider.get("apiKey"):
            raise RuntimeError("organization model unavailable")
        prompt = {
            "instruction": "只对给定任务ID排序。不得新增、改写或解释任何事实。仅返回JSON：{\"taskIds\":[\"...\"]}",
            "question": pack.get("question"),
            "tasks": [
                {
                    key: item.get(key)
                    for key in (
                        "id", "title", "priority", "status", "timeLabel",
                        "owner", "collaborators", "clientName",
                    )
                }
                for item in pack.get("tasks") or []
            ],
            "projectContext": self._project_context(identity, list(pack.get("tasks") or [])),
        }
        base = str(provider.get("baseUrl") or "").rstrip("/")
        endpoint = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
        with httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0)) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {provider['apiKey']}", "Content-Type": "application/json"},
                json={
                    "model": provider["modelName"],
                    "messages": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
                    "temperature": 0.0, "max_tokens": 500, "stream": False,
                },
            )
        response.raise_for_status()
        content = str(response.json().get("choices", [{}])[0].get("message", {}).get("content") or "").strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I)
        parsed = json.loads(content)
        valid = {str(item["id"]) for item in pack.get("tasks") or []}
        ranked = _validated_ranked_ids(parsed, valid)
        return ranked, str(provider.get("modelName") or "")

    def ask(self, identity: SessionIdentity, *, payload: Mapping[str, Any]) -> dict[str, Any]:
        question = str(payload.get("question") or "").strip()
        if not question:
            from ..repository import RepositoryError
            raise RepositoryError(422, "schedule_assistant_question_required", "请输入问题")
        board = self.tasks.board(identity)
        pack = build_schedule_fact_pack(
            list(board.get("tasks") or []), question=question,
            viewer_name=identity.display_name,
            now=self._clock(),
        )
        if not pack["tasks"]:
            return build_schedule_answer(pack)
        try:
            ranked_ids, model_name = self._rank_with_model(identity, pack)
        except Exception:
            return build_schedule_answer(pack)
        return build_schedule_answer(
            pack, ranked_ids=ranked_ids, model_name=model_name, model_succeeded=True,
        )
