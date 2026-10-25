"""The runner SSE endpoint — ``GET /api/events/stream`` serves a live ``text/event-stream``,
mirroring the hub's own ``tests/test_events_stream.py``. It is an
infinite live fan-out, unreadable through Starlette's buffering ``TestClient``, so these
tests call the route handler directly; the shared machinery itself is covered generically
in ``tests/test_foundation_events.py``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from blizzard.foundation.events.broker import EventBroker
from blizzard.foundation.events.stream import DEFAULT_KEEPALIVE_SECONDS, Stream
from blizzard.runner.api.events import _RESERVED_COMMENT, events_stream
from tests.config_inputs import export_runner_app

pytestmark = pytest.mark.component


class _FakeRequest:
    """The minimal request surface ``events_stream`` reads (app.state, headers, query)."""

    def __init__(self, broker: EventBroker | None) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(event_broker=broker))
        self.state = SimpleNamespace()
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return True


async def test_stream_endpoint_returns_an_sse_response() -> None:
    response = await events_stream(_FakeRequest(EventBroker()))  # type: ignore[arg-type]
    assert response.status_code == 200
    assert response.media_type == "text/event-stream"
    # The stream's first bytes are the reserved comment line.
    first = b""
    async for chunk in response.body_iterator:
        first = chunk.encode() if isinstance(chunk, str) else bytes(chunk)
        break
    assert first.startswith(b": blizzard runner event stream")


async def test_stream_endpoint_degrades_cleanly_with_no_broker() -> None:
    """Every composer with no stream to feed (``blizzard runner
    tick``, the store-free/export app) leaves ``app.state.event_broker`` absent — the route
    still opens cleanly rather than 500ing."""
    response = await events_stream(_FakeRequest(None))  # type: ignore[arg-type]
    assert response.status_code == 200
    frames = [chunk async for chunk in response.body_iterator]
    assert frames == [_RESERVED_COMMENT.encode()]


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

    monkeypatch.setattr("blizzard.runner.api.events.Stream", _capturing_stream)
    await events_stream(_FakeRequest(EventBroker()))  # type: ignore[arg-type]
    assert bound["keepalive_seconds"] == DEFAULT_KEEPALIVE_SECONDS


def test_events_stream_excluded_from_openapi() -> None:
    app = export_runner_app()
    paths = app.openapi()["paths"]
    assert "/api/events/stream" not in paths
    # A route this same schema *does* carry — the exclusion is deliberate, not an
    # export that lost its routes wholesale.
    assert "/runners/{runner_id}/api/leases" in paths


async def test_stream_endpoint_refuses_an_unknown_from_and_a_from_latest_with_a_query_cursor() -> None:
    for query in ({"from": "earliest"}, {"from": "latest", "last_event_id": "1"}):
        request = _FakeRequest(EventBroker())
        request.query_params.update(query)
        with pytest.raises(HTTPException) as exc_info:
            await events_stream(request)  # type: ignore[arg-type]
        assert exc_info.value.status_code == 422


async def test_stream_endpoint_from_latest_joins_after_the_ring_but_resumes_a_header() -> None:
    broker = EventBroker()
    for n in range(3):
        broker.publish("fact-changed", {"n": n})

    async def first_frames(header: str | None) -> list[bytes]:
        request = _FakeRequest(broker)
        request.query_params["from"] = "latest"
        if header is not None:
            request.headers["last-event-id"] = header
        response = await events_stream(request)  # type: ignore[arg-type]
        return [c.encode() if isinstance(c, str) else bytes(c) async for c in response.body_iterator]

    assert await first_frames(None) == [_RESERVED_COMMENT.encode()]
    resumed = await first_frames("1")
    assert [f.split(b"\n")[0] for f in resumed[1:]] == [b"id: 2", b"id: 3"]
