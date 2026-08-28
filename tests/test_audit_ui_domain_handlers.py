from __future__ import annotations

from backend.app.ui_domains.routing import UiDomainRouter
from scripts.audit_ui_domain_handlers import matching_registered_routes


def test_constrained_route_matches_renderer_template() -> None:
    router = UiDomainRouter("task_execution")

    @router.post(r"tasks/(?P<task_id>[^/]+)/timer/(?P<action>start|pause|stop)")
    def update_timer(*_args):
        return None

    matches = matching_registered_routes(
        router.routes,
        method="POST",
        template="tasks/:taskId/timer/:action",
    )

    assert [route.handler.__name__ for route in matches] == ["update_timer"]
