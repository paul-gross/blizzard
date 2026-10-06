from __future__ import annotations

import asyncio
from typing import cast

import pytest
from fastapi import FastAPI
from starlette.types import Receive, Scope, Send

from blizzard.hub.api.trace_continuation import TraceGatedFastAPI

pytestmark = pytest.mark.unit

_TRACE = [(b"traceparent", b"00-a-b-01"), (b"tracestate", b"k=v"), (b"baggage", b"a=b")]
_OTHER = [(b"host", b"hub"), (b"authorization", b"Bearer t")]


class _Gate(TraceGatedFastAPI):
    def __init__(self, resolves: bool) -> None:
        super().__init__()
        self.answer = resolves
        self.asked: list[Scope] = []
        self.seen: list[Scope] = []

    def _resolves(self, scope: Scope) -> bool:
        self.asked.append(scope)
        return self.answer


def _run(gate: _Gate, scope: Scope, monkeypatch: pytest.MonkeyPatch) -> Scope:
    async def downstream(self: FastAPI, scope: Scope, receive: Receive, send: Send) -> None:
        gate.seen.append(scope)

    async def noop(*_: object) -> None:
        return None

    monkeypatch.setattr(FastAPI, "__call__", downstream)
    asyncio.run(gate(scope, cast(Receive, noop), cast(Send, noop)))
    return gate.seen[-1]


def test_a_resolving_http_caller_keeps_every_header(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _Gate(resolves=True)
    seen = _run(gate, {"type": "http", "headers": _OTHER + _TRACE}, monkeypatch)
    assert seen["headers"] == _OTHER + _TRACE
    assert len(gate.asked) == 1


def test_an_unresolved_http_caller_loses_only_the_three_trace_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _Gate(resolves=False)
    seen = _run(gate, {"type": "http", "headers": _OTHER + _TRACE}, monkeypatch)
    assert seen["headers"] == _OTHER


def test_trace_header_names_match_in_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _Gate(resolves=False)
    seen = _run(gate, {"type": "http", "headers": [(b"TraceParent", b"x"), (b"BAGGAGE", b"y"), *_OTHER]}, monkeypatch)
    assert seen["headers"] == _OTHER


def test_an_http_request_without_trace_headers_is_never_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _Gate(resolves=False)
    seen = _run(gate, {"type": "http", "headers": _OTHER}, monkeypatch)
    assert seen["headers"] == _OTHER
    assert gate.asked == []


def test_a_websocket_with_trace_headers_is_stripped_without_resolving(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _Gate(resolves=True)
    seen = _run(gate, {"type": "websocket", "headers": _OTHER + _TRACE}, monkeypatch)
    assert seen["headers"] == _OTHER
    assert gate.asked == []


def test_a_websocket_without_trace_headers_passes_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _Gate(resolves=True)
    seen = _run(gate, {"type": "websocket", "headers": _OTHER}, monkeypatch)
    assert seen["headers"] == _OTHER
    assert gate.asked == []


def test_a_lifespan_scope_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _Gate(resolves=True)
    seen = _run(gate, {"type": "lifespan"}, monkeypatch)
    assert seen == {"type": "lifespan"}
    assert gate.asked == []
