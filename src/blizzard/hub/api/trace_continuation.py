"""The hub continues an incoming trace only for a caller whose credential resolves, never over a websocket;
anyone else starts a fresh root. FastAPI's telemetry extracts the headers before any middleware, so the gate
wraps it."""

from __future__ import annotations

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.auth_session import resolve_identity
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
