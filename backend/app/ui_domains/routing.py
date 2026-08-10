from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Pattern


@dataclass(frozen=True)
class UiRequest:
    method: str
    path: str
    query: Mapping[str, str]
    body: Mapping[str, Any]
    idempotency_key: str
    expected_sandbox_id: str | None = None
    request_seq: int | None = None


class _NotHandled:
    pass


NOT_HANDLED = _NotHandled()
UiHandler = Callable[[Any, UiRequest, re.Match[str]], Any]


@dataclass(frozen=True)
class UiRouteSpec:
    domain: str
    method: str
    pattern: str
    regex: Pattern[str]
    handler: UiHandler


class UiDomainRouter:
    def __init__(
        self,
        domain: str,
        *,
        pin_workspace: bool | Callable[[UiRequest], bool] = False,
    ):
        self.domain = domain
        self.pin_workspace = pin_workspace
        self._routes: list[UiRouteSpec] = []

    def route(self, method: str, pattern: str) -> Callable[[UiHandler], UiHandler]:
        normalized_method = method.strip().upper()
        compiled = re.compile(pattern)

        def decorate(handler: UiHandler) -> UiHandler:
            self._routes.append(
                UiRouteSpec(
                    domain=self.domain,
                    method=normalized_method,
                    pattern=pattern,
                    regex=compiled,
                    handler=handler,
                )
            )
            return handler

        return decorate

    def get(self, pattern: str) -> Callable[[UiHandler], UiHandler]:
        return self.route("GET", pattern)

    def post(self, pattern: str) -> Callable[[UiHandler], UiHandler]:
        return self.route("POST", pattern)

    def put(self, pattern: str) -> Callable[[UiHandler], UiHandler]:
        return self.route("PUT", pattern)

    def patch(self, pattern: str) -> Callable[[UiHandler], UiHandler]:
        return self.route("PATCH", pattern)

    def delete(self, pattern: str) -> Callable[[UiHandler], UiHandler]:
        return self.route("DELETE", pattern)

    @property
    def routes(self) -> tuple[UiRouteSpec, ...]:
        return tuple(self._routes)

    def requires_workspace_pin(self, request: UiRequest) -> bool:
        for route in self._routes:
            if route.method != request.method:
                continue
            if route.regex.fullmatch(request.path) is None:
                continue
            return (
                self.pin_workspace(request)
                if callable(self.pin_workspace)
                else self.pin_workspace
            )
        return False

    def dispatch(self, compatibility: Any, request: UiRequest) -> Any:
        for route in self._routes:
            if route.method != request.method:
                continue
            match = route.regex.fullmatch(request.path)
            if match is not None:
                should_pin = (
                    self.pin_workspace(request)
                    if callable(self.pin_workspace)
                    else self.pin_workspace
                )
                if should_pin:
                    runtime = getattr(compatibility, "runtime", None)
                    pin = getattr(runtime, "pinned_workspace_context", None)
                    if pin is not None:
                        with pin():
                            return route.handler(
                                compatibility,
                                request,
                                match,
                            )
                return route.handler(compatibility, request, match)
        return NOT_HANDLED
