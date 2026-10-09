"""The hub continues an incoming trace only for a caller whose credential resolves, never over a websocket;
anyone else starts a fresh root. FastAPI's telemetry extracts the headers before any middleware, so the gate
wraps it. The hub's app also documents the tenant header its routes read, once, in its spec."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.auth_session import TENANT_HEADER_COMPONENT, TENANT_HEADER_PARAMETER, resolve_identity
from blizzard.hub.config import AUTH_MODE_NONE

_TRACE_HEADERS = frozenset({b"traceparent", b"tracestate", b"baggage"})


def resolves_to_principal(request: Request) -> bool:
    """Under ``auth.mode = none`` only a runner bearer is verified, so only it resolves."""
    services = request.app.state.services
    if services is None:
        return False
    if RunnerAuth.of(request, services).principal is not None:
        return True
    if request.app.state.config.auth.mode == AUTH_MODE_NONE:
        return False
    return resolve_identity(request, services) is not None


class TraceGatedFastAPI(FastAPI):
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
            return resolves_to_principal(Request(scope))


def _carries_trace_headers(scope: Scope) -> bool:
    return any(k.lower() in _TRACE_HEADERS for k, _ in scope["headers"])
