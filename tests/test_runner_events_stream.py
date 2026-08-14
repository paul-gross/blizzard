"""The runner SSE endpoint — ``GET /api/events/stream`` serves a live ``text/event-stream``
(blizzard#317 Phase 2). Mirrors the hub's own ``tests/test_events_stream.py``.

The stream is an infinite live fan-out, so it cannot be read through Starlette's
``TestClient`` (``ASGITransport`` buffers the whole body and would hang) — these tests
call the route handler directly. The shared replay/keepalive/shutdown machinery itself is
covered generically in ``tests/test_foundation_events.py``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from blizzard.foundation.events.broker import EventBroker
from blizzard.runner.api.events import _RESERVED_COMMENT, events_stream
from blizzard.runner.app import create_app_for_export

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
    assert first.startswith(b": blizzard runner event stream")


async def test_stream_endpoint_degrades_cleanly_with_no_broker() -> None:
    """D2, blizzard#317: every composer with no stream to feed (``blizzard runner
    tick``, the store-free/export app) leaves ``app.state.events`` absent — the route
    still opens cleanly rather than 500ing."""
    response = await events_stream(_FakeRequest(None))  # type: ignore[arg-type]
    assert response.status_code == 200
    frames = [chunk async for chunk in response.body_iterator]
    assert frames == [_RESERVED_COMMENT.encode()]


def test_events_stream_excluded_from_openapi() -> None:
    app = create_app_for_export()
    paths = app.openapi()["paths"]
    assert "/api/events/stream" not in paths
    # A route this same schema *does* carry — the exclusion is deliberate, not an
    # export that lost its routes wholesale.
    assert "/api/leases" in paths
