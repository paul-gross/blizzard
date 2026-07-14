"""The hub SSE endpoint — ``GET /api/events/stream`` serves a live ``text/event-stream``.

The stream is an *infinite* live fan-out (it stays open, streaming events until the
client disconnects), so it cannot be read through Starlette's ``TestClient`` — the httpx
``ASGITransport`` buffers a whole response body and would hang. These tests exercise the
route handler directly: it returns a 200 ``text/event-stream`` response whose generator
opens with the reserved comment. Replay/live semantics are covered in ``test_events.py``
(via ``drain_stream``), and the route's exclusion from OpenAPI is pinned here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from blizzard.hub.api.events import events_stream
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
