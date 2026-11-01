"""The runner SSE endpoint — ``GET /api/events/stream`` serves a live ``text/event-stream``,
mirroring the hub's own ``tests/test_events_stream.py``. It is an
infinite live fan-out, unreadable through Starlette's buffering ``TestClient``, so these
tests call the route handler directly; the shared machinery itself is covered generically
in ``tests/test_foundation_events.py``."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi import HTTPException
from starlette.datastructures import State
from starlette.requests import Request
from starlette.types import Message, Scope

from blizzard.foundation.events.broker import EventBroker
from blizzard.foundation.events.stream import DEFAULT_KEEPALIVE_SECONDS, Stream
from blizzard.foundation.leases import LeaseClosureReason
from blizzard.runner.api.events import RESERVED_COMMENT, events_stream
from blizzard.runner.events.broker import EventBroker as RunnerEventBroker
from tests.config_inputs import export_runner_app

pytestmark = pytest.mark.component


def _request(
    broker: EventBroker | None,
    *,
    connected: bool = False,
    query: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> Request:
    """A real request over ``broker`` as ``app.state.event_broker``; ``connected`` keeps ``receive`` pending (the
    client stays), otherwise ``receive`` reports a disconnect."""
    state = State()
    state.event_broker = broker
    never = asyncio.Event()

    async def receive() -> Message:
        if connected:
            await never.wait()
            return {"type": "http.request", "body": b"", "more_body": True}
        return {"type": "http.disconnect"}

    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/events/stream",
        "app": SimpleNamespace(state=state),
        "state": {},
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
        "query_string": urlencode(query or {}).encode(),
    }
    return Request(scope, receive)


async def test_stream_endpoint_returns_an_sse_response() -> None:
    response = await events_stream(_request(EventBroker()))
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
    response = await events_stream(_request(None))
    assert response.status_code == 200
    frames = [chunk async for chunk in response.body_iterator]
    assert frames == [RESERVED_COMMENT.encode()]


async def test_stream_endpoint_binds_the_default_keepalive_cadence(monkeypatch: pytest.MonkeyPatch) -> None:
    """``events_stream`` constructs ``Stream`` with no ``keepalive_seconds`` override — it inherits
    ``DEFAULT_KEEPALIVE_SECONDS``, the one keepalive cadence the hub and runner routes share.
    A route that started passing its own override would flip this assertion false."""
    bound: dict[str, float] = {}

    def _capturing[**P](real_stream: Callable[P, Stream]) -> Callable[P, Stream]:
        def _capturing_stream(*args: P.args, **kwargs: P.kwargs) -> Stream:
            instance = real_stream(*args, **kwargs)
            bound["keepalive_seconds"] = instance.keepalive_seconds
            return instance

        return _capturing_stream

    monkeypatch.setattr("blizzard.runner.api.events.Stream", _capturing(Stream))
    await events_stream(_request(EventBroker()))
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
        request = _request(EventBroker(), query=query)
        with pytest.raises(HTTPException) as exc_info:
            await events_stream(request)
        assert exc_info.value.status_code == 422


async def test_stream_endpoint_from_latest_joins_after_the_ring_but_resumes_a_header() -> None:
    broker = EventBroker()
    for n in range(3):
        broker.publish("fact-changed", {"n": n})

    async def first_frames(header: str | None) -> list[bytes]:
        request = _request(
            broker, query={"from": "latest"}, headers={} if header is None else {"last-event-id": header}
        )
        response = await events_stream(request)
        return [c.encode() if isinstance(c, str) else bytes(c) async for c in response.body_iterator]

    assert await first_frames(None) == [RESERVED_COMMENT.encode()]
    resumed = await first_frames("1")
    assert [f.split(b"\n")[0] for f in resumed[1:]] == [b"id: 2", b"id: 3"]


def _stale_cursor(first: RunnerEventBroker) -> int:
    for cause in ("created", LeaseClosureReason.TRANSITIONED, LeaseClosureReason.RELEASED):
        cursor = first.publish_lease_changed("ls_1", "ch_1", cause=cause, runner_id="rn_1")
    return cursor


async def test_runner_stream_resumes_live_after_a_restart_reset_the_broker_ids() -> None:
    """A stale ``last-event-id`` against a restarted broker still delivers a post-open frame."""
    stale_cursor = _stale_cursor(RunnerEventBroker())
    second = RunnerEventBroker()
    request = _request(second, connected=True, headers={"last-event-id": str(stale_cursor)})

    response = await events_stream(request)
    frames = aiter(response.body_iterator)
    await anext(frames)
    live_id = second.publish_lease_changed("ls_2", "ch_2", cause="created", runner_id="rn_1")
    assert live_id < stale_cursor
    frame = await asyncio.wait_for(anext(frames), timeout=5.0)
    assert isinstance(frames, AsyncGenerator)
    await frames.aclose()

    assert bytes(frame.encode() if isinstance(frame, str) else frame).startswith(
        f"id: {live_id}\nevent: lease-changed".encode()
    )


async def test_runner_stream_replays_a_restarted_brokers_buffered_tail_past_a_stale_cursor() -> None:
    """A restarted broker's buffered tail replays past a stale ``last-event-id``."""
    stale_cursor = _stale_cursor(RunnerEventBroker())
    second = RunnerEventBroker()
    second.publish_lease_changed("ls_2", "ch_2", cause="created", runner_id="rn_1")
    second.publish_lease_changed("ls_2", "ch_2", cause=LeaseClosureReason.TRANSITIONED, runner_id="rn_1")
    assert second.latest_id() < stale_cursor
    request = _request(second, headers={"last-event-id": str(stale_cursor)})

    response = await events_stream(request)
    body = b"".join([c.encode() if isinstance(c, str) else bytes(c) async for c in response.body_iterator])

    assert body.count(b"event: lease-changed") == 2
