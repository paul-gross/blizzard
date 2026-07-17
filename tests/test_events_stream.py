"""The hub SSE endpoint — ``GET /api/events/stream`` serves a live ``text/event-stream``.

The stream is an *infinite* live fan-out (it stays open, streaming events until the
client disconnects), so it cannot be read through Starlette's ``TestClient`` — the httpx
``ASGITransport`` buffers a whole response body and would hang. These tests exercise the
route handler directly: it returns a 200 ``text/event-stream`` response whose generator
opens with the reserved comment. Replay/live semantics are covered in ``test_events.py``
(via ``drain_stream``), and the route's exclusion from OpenAPI is pinned here.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from blizzard.hub.api.events import _stream, events_stream
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
    response = await events_stream(_FakeRequest(EventBroker()))  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    # The generator opens with the reserved comment so an EventSource connects cleanly.
    first = b""
    async for chunk in response.body_iterator:
        first = chunk.encode() if isinstance(chunk, str) else bytes(chunk)
        break
    assert first.startswith(b": blizzard hub event stream")


def test_events_stream_excluded_from_openapi() -> None:
    app = create_app_for_export()
    assert "/api/events/stream" not in app.openapi()["paths"]


async def test_stream_exits_promptly_on_shutdown_signal_not_disconnect() -> None:
    """A shutting-down stream returns as soon as ``shutdown`` fires — not on client
    disconnect (which never happens here) and not on the next 15s keepalive wake
    (issue #47). Bounding the wait at 1s makes this fail if the shutdown signal is not
    wired into the live-wait race: without it the generator only wakes up on its
    keepalive timeout, and the ``wait_for`` below times out first.
    """
    broker = EventBroker()
    shutdown = asyncio.Event()

    class _ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False  # the client stays connected — only the shutdown signal ends this

    async def _drain() -> None:
        async for _ in _stream(broker, _ConnectedRequest(), last_event_id=0, shutdown=shutdown):  # type: ignore[arg-type]
            pass

    task = asyncio.ensure_future(_drain())
    await asyncio.sleep(0.05)  # let the generator subscribe and reach its live wait
    assert broker.subscriber_count() == 1

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)

    # The generator's `finally: broker.unsubscribe(sub)` ran — no leaked subscriber.
    assert broker.subscriber_count() == 0
