"""The hub SSE endpoint — ``GET /api/events/stream`` serves a live ``text/event-stream``.

The stream is an infinite live fan-out, so Starlette's ``TestClient`` (which buffers the whole
body) reads one only once its broker closes and ends it; the other tests call the route handler or
the stream's generator directly. Replay/live semantics are covered in ``tests/test_events.py``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from blizzard.foundation.events.stream import DEFAULT_KEEPALIVE_SECONDS
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.api.events import _RESERVED_COMMENT, Cursor, Stream, events_stream
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.tenancy import TenantId
from tests.config_inputs import export_hub_app
from tests.support import add_tenant, build_hub, default_tenant, parse_sse_frames, runner_token, seed_runner

pytestmark = pytest.mark.component


class _FakeRequest:
    """The minimal request surface ``events_stream`` reads (app.state, headers, query)."""

    def __init__(self) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace())
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return True


def _services(broker: EventBroker) -> SimpleNamespace:
    """The one field of a tenant's services the stream reads — its broker."""
    return SimpleNamespace(events=broker)


async def test_stream_endpoint_returns_an_sse_response() -> None:
    # Direct call bypasses FastAPI's dependency resolution — the tenant's services (normally
    # resolved via `Depends`) are supplied directly here.
    response = await events_stream(_FakeRequest(), _services(EventBroker()))  # type: ignore[arg-type]
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

    def _capturing[**P](make: Callable[P, Stream]) -> Callable[P, Stream]:
        def _capturing_stream(*args: P.args, **kwargs: P.kwargs) -> Stream:
            instance = make(*args, **kwargs)
            bound["keepalive_seconds"] = instance.keepalive_seconds
            return instance

        return _capturing_stream

    monkeypatch.setattr("blizzard.hub.api.events.Stream", _capturing(real_stream))
    await events_stream(_FakeRequest(), _services(EventBroker()))  # type: ignore[arg-type]
    assert bound["keepalive_seconds"] == DEFAULT_KEEPALIVE_SECONDS


def test_events_stream_excluded_from_openapi() -> None:
    app = export_hub_app()
    assert "/api/events/stream" not in app.openapi()["paths"]


async def test_stream_exits_promptly_on_shutdown_signal_not_disconnect() -> None:
    """A shutting-down stream returns as soon as ``shutdown`` fires — not on client
    disconnect or the next 15s keepalive wake. Bounding the wait at 1s fails
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


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False  # the client stays connected — only the broker's close ends the stream


def _until(condition: Callable[[], bool], *, within: float = 5.0) -> None:
    deadline = time.monotonic() + within
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.01)


async def test_dropping_a_tenants_broker_ends_its_open_stream_after_what_it_holds() -> None:
    """``TenantBrokers.drop`` closes the tenant's broker: its stream sends the events already handed to
    it, then returns; another tenant's stream stays open."""
    brokers = TenantBrokers()
    dropped, kept = brokers.for_tenant(TenantId("ten_A")), brokers.for_tenant(TenantId("ten_B"))
    streams = [
        Stream(broker, _ConnectedRequest(), Cursor(0), _RESERVED_COMMENT, keepalive_seconds=30.0).frames()  # type: ignore[arg-type]
        for broker in (dropped, kept)
    ]
    for frames in streams:
        assert await asyncio.wait_for(frames.__anext__(), timeout=1.0) == _RESERVED_COMMENT.encode()
    try:
        dropped.publish_queue_changed()
        brokers.drop(TenantId("ten_A"))

        rest = await asyncio.wait_for(_rest(streams[0]), timeout=1.0)

        assert [frame["event"] for frame in parse_sse_frames(b"".join(rest).decode())] == ["queue-changed"]
        assert dropped.subscriber_count() == 0
        kept.publish_queue_changed()
        assert b"queue-changed" in await asyncio.wait_for(streams[1].__anext__(), timeout=1.0)
    finally:
        await streams[1].aclose()  # type: ignore[attr-defined]


async def _rest(frames: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in frames]


async def test_a_stream_opened_on_a_closed_broker_replays_its_tail_and_ends() -> None:
    broker = EventBroker()
    broker.publish_queue_changed()
    broker.close()

    stream = Stream(broker, _ConnectedRequest(), Cursor(0), _RESERVED_COMMENT)  # type: ignore[arg-type]
    frames = await asyncio.wait_for(_rest(stream.frames()), timeout=1.0)

    assert frames[0] == _RESERVED_COMMENT.encode()
    assert [frame["event"] for frame in parse_sse_frames(b"".join(frames[1:]).decode())] == ["queue-changed"]
    assert broker.subscriber_count() == 0


def test_a_tenants_stream_carries_only_its_own_events(tmp_path: Path) -> None:
    """Two tenants publishing at once: the stream the second tenant opens carries its events alone.
    Closing that tenant's broker ends the stream, which lets the buffering client read it."""
    hub = build_hub(tmp_path)
    other, tenant = default_tenant(hub.engine), add_tenant(hub.engine, "bravo")
    hub.hub_services.tenants.open_tenants.refresh()
    brokers = {named: hub.open_services(named).events for named in (other, tenant)}
    with ThreadPoolExecutor(max_workers=1) as pool:
        stream = pool.submit(hub.client.get, "/api/events/stream", headers={TENANT_HEADER: tenant})
        try:
            _until(lambda: brokers[tenant].subscriber_count() == 1)
            for named, broker in brokers.items():
                broker.publish_runner_changed(f"rn_{named}", kind="added")
        finally:
            brokers[tenant].close()
        resp = stream.result(timeout=5.0)

    assert resp.status_code == 200
    frames = parse_sse_frames(resp.text)
    assert [frame["event"] for frame in frames] == ["runner-changed"]
    assert f'"rn_{tenant}"' in frames[0]["data"]
    assert brokers[other].subscriber_count() == 0


def test_a_runner_token_is_refused_on_the_stream(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, "rn_alpha")

    resp = hub.client.get("/api/events/stream", headers={"Authorization": f"Bearer {runner_token('rn_alpha')}"})

    assert resp.status_code == 403
    assert "not valid on an operator verb" in resp.json()["detail"]
    assert hub.events.subscriber_count() == 0


def test_the_app_holds_no_broker_of_its_own(tmp_path: Path) -> None:
    """Every broker is a tenant's: the app state carries none a publish or a stream could reach past it."""
    hub = build_hub(tmp_path)
    assert hub.app is not None
    assert not hasattr(hub.app.state, "events")
