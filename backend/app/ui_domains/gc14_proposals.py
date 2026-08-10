from __future__ import annotations

from typing import Any

from .routing import UiDomainRouter, UiRequest


router = UiDomainRouter("gc14_ai_proposals", pin_workspace=True)


def _query(compatibility: Any, path: str, query: dict[str, Any] | None = None) -> Any:
    return compatibility.runtime.cloud_query(path, query=query or {})


def _command(
    compatibility: Any,
    request: UiRequest,
    path: str,
    payload: dict[str, Any],
    *,
    key_suffix: str | None = None,
) -> Any:
    key = request.idempotency_key
    if key_suffix:
        key = f"{key}:{key_suffix}"
    return compatibility.runtime.cloud_command(
        "POST",
        path,
        payload=payload,
        idempotency_key=key,
    )


def _draft_ui(item: dict[str, Any]) -> dict[str, Any]:
    status = str(item.get("status") or "draft")
    return {
        **item,
        "requiresApproval": True,
        "status": {
            "approved": "reviewed",
            "rejected": "rejected",
            "executed": "promoted",
        }.get(status, "draft"),
        "scopeType": "client",
        "scopeId": item.get("clientId"),
        "reviewedAt": item.get("decidedAt") if status == "approved" else None,
        "rejectedAt": item.get("decidedAt") if status == "rejected" else None,
        "promotedProposalId": item.get("id") if status == "executed" else None,
        "proposalStatus": status,
    }


@router.get(r"proposals")
def list_proposals(compatibility: Any, request: UiRequest, _: Any) -> Any:
    return _query(compatibility, "/api/v2/ai-proposals", dict(request.query))


@router.get(r"approvals")
def list_approval_queue(compatibility: Any, request: UiRequest, _: Any) -> Any:
    proposals = _query(
        compatibility,
        "/api/v2/ai-proposals",
        {
            "clientId": request.query.get("client_id") or "",
            "status": "draft",
            "limit": request.query.get("limit") or "50",
        },
    )
    return [
        {
            "id": item.get("id"),
            "client_id": item.get("clientId"),
            "action_type": item.get("kind") or "proposal_review",
            "actor_type": "agent",
            "actor_id": item.get("createdBy") or "builtin_agent",
            "target_resource": item.get("id"),
            "payload": item.get("payload") or {},
            "reason": item.get("rationale") or item.get("summary") or "",
            "status": "pending",
            "agent_run_id": item.get("executionTicketId"),
            "created_at": item.get("createdAt"),
        }
        for item in proposals
    ]


@router.post(r"approvals/([^/]+)/(approve|reject)")
def decide_approval_queue(
    compatibility: Any, request: UiRequest, match: Any
) -> Any:
    proposal_id, action = match.groups()
    saved = _decision(compatibility, request, proposal_id, action)
    return {
        "id": saved.get("id") or proposal_id,
        "status": "approved" if action == "approve" else "rejected",
        "decided_by": request.body.get("decided_by") or "current_member",
    }


@router.get(r"proposals/([^/]+)")
def get_proposal(compatibility: Any, _: UiRequest, match: Any) -> Any:
    return _query(compatibility, f"/api/v2/ai-proposals/{match.group(1)}")


def _decision(
    compatibility: Any,
    request: UiRequest,
    proposal_id: str,
    action: str,
) -> Any:
    current = _query(compatibility, f"/api/v2/ai-proposals/{proposal_id}")
    return _command(
        compatibility,
        request,
        f"/api/v2/ai-proposals/{proposal_id}/{action}",
        {**dict(request.body), "expectedVersion": current.get("version")},
    )


@router.post(r"proposals/([^/]+)/approve")
def approve_proposal(compatibility: Any, request: UiRequest, match: Any) -> Any:
    return _decision(compatibility, request, match.group(1), "approve")


@router.post(r"proposals/([^/]+)/reject")
def reject_proposal(compatibility: Any, request: UiRequest, match: Any) -> Any:
    return _decision(compatibility, request, match.group(1), "reject")


@router.get(r"proposals/([^/]+)/execution-preview")
def execution_preview(compatibility: Any, _: UiRequest, match: Any) -> Any:
    return _query(
        compatibility,
        f"/api/v2/ai-proposals/{match.group(1)}/execution-preview",
    )


@router.post(r"proposals/([^/]+)/(execute|execution-ticket)")
def execute_proposal(compatibility: Any, request: UiRequest, match: Any) -> Any:
    result = _command(
        compatibility,
        request,
        f"/api/v2/ai-proposals/{match.group(1)}/execute",
        dict(request.body),
    )
    return {"proposal": result, "executionTicket": result.get("executionTicket")}


@router.get(r"execution-tickets")
def execution_tickets(compatibility: Any, request: UiRequest, _: Any) -> Any:
    return _query(compatibility, "/api/v2/ai-execution-runs", dict(request.query))


@router.post(r"clients/(?P<client_id>[^/]+)/workspace/proposal-drafts")
def create_workspace_proposal_draft(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    saved = _command(
        compatibility,
        request,
        "/api/v2/ai-proposals",
        {**dict(request.body), "clientId": match.group("client_id")},
    )
    return _draft_ui(saved)


@router.get(r"data-center/proposal-drafts")
def list_data_center_proposal_drafts(
    compatibility: Any, request: UiRequest, _: Any
) -> list[dict[str, Any]]:
    return [
        _draft_ui(dict(item))
        for item in _query(
            compatibility, "/api/v2/ai-proposals", dict(request.query)
        )
    ]


@router.post(r"data-center/proposal-drafts/(?P<proposal_id>[^/]+)/mark-reviewed")
def mark_data_center_proposal_reviewed(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    proposal_id = match.group("proposal_id")
    current = _query(compatibility, f"/api/v2/ai-proposals/{proposal_id}")
    saved = _command(
        compatibility,
        request,
        f"/api/v2/ai-proposals/{proposal_id}/approve",
        {
            "expectedVersion": current.get("version"),
            "note": request.body.get("note") or "已人工查看",
        },
    )
    return _draft_ui(saved)


@router.post(r"data-center/proposal-drafts/(?P<proposal_id>[^/]+)/reject")
def reject_data_center_proposal_draft(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    proposal_id = match.group("proposal_id")
    current = _query(compatibility, f"/api/v2/ai-proposals/{proposal_id}")
    saved = _command(
        compatibility,
        request,
        f"/api/v2/ai-proposals/{proposal_id}/reject",
        {
            "expectedVersion": current.get("version"),
            "note": request.body.get("reason") or "暂不推进",
        },
    )
    return _draft_ui(saved)


@router.post(r"data-center/proposal-drafts/(?P<proposal_id>[^/]+)/promote")
def promote_data_center_proposal_draft(
    compatibility: Any, request: UiRequest, match: Any
) -> dict[str, Any]:
    proposal_id = match.group("proposal_id")
    current = _query(compatibility, f"/api/v2/ai-proposals/{proposal_id}")
    if str(current.get("status") or "draft") == "draft":
        current = _command(
            compatibility,
            request,
            f"/api/v2/ai-proposals/{proposal_id}/approve",
            {
                "expectedVersion": current.get("version"),
                "note": request.body.get("note") or "确认执行",
            },
            key_suffix="approve",
        )
    executed = _command(
        compatibility,
        request,
        f"/api/v2/ai-proposals/{proposal_id}/execute",
        {
            "expectedVersion": current.get("version"),
            "options": dict(request.body.get("options") or {}),
        },
        key_suffix="execute",
    )
    return {
        "draft": _draft_ui(executed),
        "proposalId": proposal_id,
        "effectType": "proposal",
    }
