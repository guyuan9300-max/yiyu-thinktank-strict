from __future__ import annotations

from typing import Any

from fastapi import Body, Depends, FastAPI, Header, Request

from strict_common.ids import new_id

from ..repositories.gc13_growth import (
    confirm_growth_evidence,
    growth_compatibility_view,
    growth_snapshot,
    like_growth_experience_quote,
    publish_growth_rule,
    record_growth_companion_summary,
    rebuild_growth_read_models,
    update_growth_evidence,
)
from ..repositories.gc13_weekly_review_adapter import (
    confirm_candidate_proposal,
    ignore_candidate_proposal,
    list_weekly_review_growth_candidates,
)
from ..repository import CloudRepository, SessionIdentity


def register_gc13_growth_routes(
    app: FastAPI,
    repository: CloudRepository,
    identity_dependency: Any,
) -> None:
    """Register only the isolated GC-13 surface.

    The central domain registrar deliberately remains untouched; thread A can
    mount this registrar during integration after reviewing the hand-off.
    """

    @app.get("/api/v2/gc13/growth")
    def read_growth(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> dict[str, Any]:
        return growth_snapshot(repository, identity)

    @app.post("/api/v2/gc13/growth/evidence")
    def confirm_evidence(
        payload: dict[str, Any] = Body(),
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return confirm_growth_evidence(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/gc13/growth/rules")
    def publish_rule(
        payload: dict[str, Any] = Body(),
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return publish_growth_rule(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/gc13/growth/rebuild")
    def rebuild(
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return rebuild_growth_read_models(
            repository,
            identity,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/gc13/growth/companion-summary")
    def save_growth_companion_summary(
        payload: dict[str, Any] = Body(),
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return record_growth_companion_summary(
            repository,
            identity,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/gc13/growth/evidence/{evidence_id}/{action}")
    def update_evidence(
        evidence_id: str,
        action: str,
        payload: dict[str, Any] = Body(),
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return update_growth_evidence(
            repository,
            identity,
            evidence_id=evidence_id,
            action=action,
            payload=payload,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.get("/api/v2/gc13/growth/weekly-review-candidates")
    def read_weekly_review_candidates(
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> list[dict[str, Any]]:
        return list_weekly_review_growth_candidates(repository, identity)

    @app.post("/api/v2/gc13/growth/weekly-review-candidates/{candidate_id}/confirm")
    def confirm_weekly_review_growth_candidate(
        candidate_id: str,
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return confirm_candidate_proposal(
            repository,
            identity,
            candidate_id=candidate_id,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/gc13/growth/weekly-review-candidates/{candidate_id}/ignore")
    def ignore_weekly_review_growth_candidate(
        candidate_id: str,
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        return ignore_candidate_proposal(
            repository,
            identity,
            candidate_id=candidate_id,
            idempotency_key=idempotency_key or new_id(),
        )

    def _compat_query(
        resource_path: str,
        request: Request,
        identity: SessionIdentity,
    ) -> Any:
        return growth_compatibility_view(
            repository,
            identity,
            view=resource_path.removeprefix("growth/"),
        )

    @app.get("/api/v2/gc13/growth/overview")
    def read_growth_overview(
        request: Request,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> Any:
        return _compat_query("growth/overview", request, identity)

    @app.get("/api/v2/gc13/growth/workbench")
    def read_growth_workbench(
        request: Request,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> Any:
        return _compat_query("growth/workbench", request, identity)

    @app.get("/api/v2/gc13/growth/badges")
    def read_growth_badges(
        request: Request,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> Any:
        return _compat_query("growth/badges", request, identity)

    @app.get("/api/v2/gc13/growth/ledger")
    def read_growth_ledger(
        request: Request,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> Any:
        return _compat_query("growth/ledger", request, identity)

    @app.get("/api/v2/gc13/growth/experience-wall")
    def read_growth_experience_wall(
        request: Request,
        identity: SessionIdentity = Depends(identity_dependency),
    ) -> Any:
        return _compat_query("growth/experience-wall", request, identity)

    def _compat_command(*_: Any, **__: Any) -> Any:
        from ..repository import RepositoryError

        raise RepositoryError(
            409,
            "gc13_legacy_action_not_connected",
            "该成长动作仍待接入GC-13正式证据确认命令；现有成长证据不会被修改",
        )

    @app.post("/api/v2/gc13/growth/experience-wall/{quote_id}/like")
    def like_experience_wall_quote(
        quote_id: str,
        payload: dict[str, Any] = Body(default={}),
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        del payload
        return like_growth_experience_quote(
            repository,
            identity,
            quote_id=quote_id,
            idempotency_key=idempotency_key or new_id(),
        )

    @app.post("/api/v2/gc13/growth/handbook/{entry_id}/mark-reused")
    def mark_handbook_reused(
        entry_id: str,
        payload: dict[str, Any] = Body(default={}),
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        return _compat_command(
            f"growth/handbook/{entry_id}/mark-reused",
            payload,
            identity,
            idempotency_key,
        )

    @app.post("/api/v2/gc13/growth/pending-captures/{capture_id}/state")
    def update_pending_capture(
        capture_id: str,
        payload: dict[str, Any] = Body(default={}),
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        return _compat_command(
            f"growth/pending-captures/{capture_id}/state",
            payload,
            identity,
            idempotency_key,
        )

    @app.post("/api/v2/gc13/growth/recommendations/{recommendation_id}/{action}")
    def decide_recommendation(
        recommendation_id: str,
        action: str,
        payload: dict[str, Any] = Body(default={}),
        identity: SessionIdentity = Depends(identity_dependency),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> Any:
        if action not in {"accept", "dismiss"}:
            from ..repository import RepositoryError

            raise RepositoryError(404, "gc13_action_not_found", "成长建议操作不存在")
        return _compat_command(
            f"growth/recommendations/{recommendation_id}/{action}",
            payload,
            identity,
            idempotency_key,
        )
