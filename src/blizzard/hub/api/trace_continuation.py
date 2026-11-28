"""The hub continues an incoming trace only for a caller whose credential resolves, never over a websocket;
anyone else starts a fresh root. FastAPI's telemetry extracts the headers before any middleware, so the gate
wraps it. The hub's app also documents the tenant header its routes read, once, in its spec."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

from blizzard.foundation.registry.interface import ServiceAbsent
from blizzard.hub.api.auth_session import TENANT_HEADER_COMPONENT, TENANT_HEADER_PARAMETER

_TRACE_HEADERS = frozenset({b"traceparent", b"tracestate", b"baggage"})


PrincipalCheck = Callable[[Request], bool]


def principal_check(
    runner_resolves: PrincipalCheck, identity_resolves: PrincipalCheck, *, none_mode: bool
) -> PrincipalCheck:
    """Build the pre-routing gate from named auth capabilities, without retaining a resolver.

    In none mode the implicit operator is not a verified credential: only a runner can continue a trace.
    An unavailable auth service likewise starts a fresh trace.
    """

    def check(request: Request) -> bool:
        try:
            return runner_resolves(request) or (not none_mode and identity_resolves(request))
        except ServiceAbsent:
            return False

    return check


class TraceGatedFastAPI(FastAPI):
    #: Installed at composition and replaced alongside the process in the process-replacement test seam.
    principal_check: PrincipalCheck

    def openapi(self) -> dict[str, Any]:
        """The spec, documenting the tenant header once, under ``components.parameters``."""
        if self.openapi_schema is None:
            spec = super().openapi()
            spec.setdefault("components", {}).setdefault("parameters", {})[TENANT_HEADER_COMPONENT] = (
                TENANT_HEADER_PARAMETER
            )
        return super().openapi()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket") and _carries_trace_headers(scope):
            scope["app"] = self
            if scope["type"] == "websocket" or not await run_in_threadpool(self._resolves, scope):
                scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() not in _TRACE_HEADERS]
        await super().__call__(scope, receive, send)

    def _resolves(self, scope: Scope) -> bool:
        with self.state.platform_tracing.suppressed():
            return self.principal_check(Request(scope))


def _carries_trace_headers(scope: Scope) -> bool:
    return any(k.lower() in _TRACE_HEADERS for k, _ in scope["headers"])
