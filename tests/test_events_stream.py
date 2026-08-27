"""The hub SSE endpoint — ``GET /api/events/stream`` serves a live ``text/event-stream``.

The stream is an infinite live fan-out, so it cannot be read through Starlette's
``TestClient`` (``ASGITransport`` buffers the whole body and would hang) — these tests
call the route handler directly. Replay/live semantics are covered in ``test_events.py``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from blizzard.foundation.events.stream import DEFAULT_KEEPALIVE_SECONDS
from blizzard.hub.api.auth_session import IMPLICIT_OPERATOR
from blizzard.hub.api.events import _RESERVED_COMMENT, Cursor, Stream, events_stream
from blizzard.hub.app import create_app_for_export
from blizzard.hub.events.broker import EventBroker

pytestmark = pytest.mark.component


class _FakeRequest:
    """The minimal request surface ``events_stream`` reads (app.state, headers, query)."""

    def __init__(self, broker: EventBroker | None) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(events=broker))
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return True


async def test_stream_endpoint_returns_an_sse_response() -> None:
    # Direct call bypasses FastAPI's dependency resolution — `identity` (normally
    # resolved via `Depends`) is supplied directly here.
    response = await events_stream(_FakeRequest(EventBroker()), IMPLICIT_OPERATOR)  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    # The generator opens with the reserved comment so an EventSource connects cleanly.
    first = b""
    async for chunk in response.body_iterator:
        first = chunk.encode() if isinstance(chunk, str) else bytes(chunk)
        break
    assert first.startswith(b": blizzard hub event stream")


async def test_stream_endpoint_binds_the_default_keepalive_cadence(monkeypatch: pytest.MonkeyPatch) -> None:
    """``events_stream`` constructs ``Stream`` with no ``keepalive_seconds`` override — it inherits
    ``DEFAULT_KEEPALIVE_SECONDS``, the one keepalive cadence the hub and runner routes share.
    A route that started passing its own override would flip this assertion false."""
    real_stream = Stream
    bound: dict[str, float] = {}

    def _capturing_stream(*args: object, **kwargs: object) -> Stream:
        instance = real_stream(*args, **kwargs)  # type: ignore[arg-type]
        bound["keepalive_seconds"] = instance.keepalive_seconds
        return instance

    monkeypatch.setattr("blizzard.hub.api.events.Stream", _capturing_stream)
    await events_stream(_FakeRequest(EventBroker()), IMPLICIT_OPERATOR)  # type: ignore[arg-type]
    assert bound["keepalive_seconds"] == DEFAULT_KEEPALIVE_SECONDS


def test_events_stream_excluded_from_openapi() -> None:
    app = create_app_for_export()
    assert "/api/events/stream" not in app.openapi()["paths"]


async def test_stream_exits_promptly_on_shutdown_signal_not_disconnect() -> None:
    """A shutting-down stream returns as soon as ``shutdown`` fires — not on client
    disconnect or the next 15s keepalive wake (issue #47). Bounding the wait at 1s fails
    if the shutdown signal isn't wired into the live-wait race."""
    broker = EventBroker()
    shutdown = asyncio.Event()

    class _ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False  # the client stays connected — only the shutdown signal ends this

    async def _drain() -> None:
        stream = Stream(broker, _ConnectedRequest(), Cursor(0), _RESERVED_COMMENT, shutdown)  # type: ignore[arg-type]
        async for _ in stream.frames():
            pass

    task = asyncio.ensure_future(_drain())
    await asyncio.sleep(0.05)  # let the generator subscribe and reach its live wait
    assert broker.subscriber_count() == 1

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)

    # The generator's `finally: broker.unsubscribe(sub)` ran — no leaked subscriber.
    assert broker.subscriber_count() == 0
