"""The shared SSE core (D1, blizzard#317) — the kind-agnostic broker, the stream-response
machinery, and the early-shutdown server wrapper both daemons bind. Domain vocabulary and
framing text are exercised through each daemon's own broker/route instead (e.g.
``tests/test_events.py``, ``tests/test_events_stream.py``)."""

from __future__ import annotations

import asyncio
import signal

import pytest
import uvicorn
from fastapi import FastAPI

from blizzard.foundation.events.broker import EventBroker
from blizzard.foundation.events.server import EarlyShutdownServer
from blizzard.foundation.events.stream import Cursor, Stream

pytestmark = pytest.mark.unit

_RESERVED_COMMENT = ": test stream\n\n"


class _FakeRequest:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.query_params: dict[str, str] = {}

    async def is_disconnected(self) -> bool:
        return False


class _ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class _DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


# --- Cursor -------------------------------------------------------------------------


def test_cursor_of_defaults_to_zero_with_no_last_event_id() -> None:
    assert Cursor.of(_FakeRequest()).last_event_id == 0  # type: ignore[arg-type]


def test_cursor_of_reads_the_last_event_id_header() -> None:
    request = _FakeRequest()
    request.headers["last-event-id"] = "7"
    assert Cursor.of(request).last_event_id == 7  # type: ignore[arg-type]


def test_cursor_of_falls_back_to_the_query_param() -> None:
    request = _FakeRequest()
    request.query_params["last_event_id"] = "3"
    assert Cursor.of(request).last_event_id == 3  # type: ignore[arg-type]


def test_cursor_of_degrades_to_zero_on_a_malformed_value() -> None:
    request = _FakeRequest()
    request.headers["last-event-id"] = "not-a-number"
    assert Cursor.of(request).last_event_id == 0  # type: ignore[arg-type]


# --- EventBroker (kind-agnostic core) ------------------------------------------------


def test_publish_mints_monotonic_ids() -> None:
    broker = EventBroker()
    first = broker.publish("widget-changed", {"id": "w1"})
    second = broker.publish("widget-changed", {"id": "w2"})
    assert (first, second) == (1, 2)
    assert broker.latest_id() == second


def test_replay_since_returns_only_newer_events() -> None:
    broker = EventBroker()
    broker.publish("a", {})
    cutoff = broker.publish("b", {})
    third = broker.publish("c", {})
    assert [e.id for e in broker.replay_since(cutoff)] == [third]


def test_snapshot_returns_the_whole_buffered_ring_oldest_first() -> None:
    broker = EventBroker()
    broker.publish("a", {})
    broker.publish("b", {})
    assert [e.type for e in broker.snapshot()] == ["a", "b"]


async def test_subscribe_and_unsubscribe_track_the_live_count() -> None:
    broker = EventBroker()
    sub = broker.subscribe()
    assert broker.subscriber_count() == 1
    broker.unsubscribe(sub)
    assert broker.subscriber_count() == 0


async def test_publish_delivers_live_to_a_subscriber_across_the_thread_boundary() -> None:
    broker = EventBroker()
    sub = broker.subscribe()
    broker.publish("widget-changed", {"id": "w1"})
    event = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
    assert event.type == "widget-changed"
    assert event.framed().startswith(f"id: {event.id}\nevent: widget-changed\n")


# --- Stream (the replay-then-live handoff) -------------------------------------------


async def test_stream_with_no_broker_opens_cleanly_and_idles() -> None:
    stream = Stream(None, _DisconnectedRequest(), Cursor(0), _RESERVED_COMMENT)  # type: ignore[arg-type]
    frames = [chunk async for chunk in stream.frames()]
    assert frames == [_RESERVED_COMMENT.encode()]


async def test_stream_opens_with_the_reserved_comment_then_replays_the_tail() -> None:
    broker = EventBroker()
    broker.publish("widget-changed", {"id": "w1"})
    stream = Stream(broker, _DisconnectedRequest(), Cursor(0), _RESERVED_COMMENT)  # type: ignore[arg-type]
    frames = [chunk async for chunk in stream.frames()]
    assert frames[0] == _RESERVED_COMMENT.encode()
    assert b"widget-changed" in frames[1]


async def test_stream_emits_a_keepalive_on_an_idle_connection_at_the_injected_interval() -> None:
    """The keepalive interval is an injected value (D1) — bounding it well below the
    production default (15s) lets this observe an emission without waiting it out."""
    broker = EventBroker()
    stream = Stream(broker, _ConnectedRequest(), Cursor(0), _RESERVED_COMMENT, keepalive_seconds=0.05)  # type: ignore[arg-type]
    frames = stream.frames()
    try:
        first = await asyncio.wait_for(frames.__anext__(), timeout=1.0)
        assert first == _RESERVED_COMMENT.encode()
        keepalive = await asyncio.wait_for(frames.__anext__(), timeout=1.0)
        assert keepalive == b": keepalive\n\n"
    finally:
        await frames.aclose()  # type: ignore[attr-defined]


async def test_stream_exits_promptly_on_shutdown_signal_not_disconnect() -> None:
    """A shutting-down stream returns as soon as ``shutdown`` fires — not on client
    disconnect or the next keepalive wake. Bounding the wait at 1s (with a keepalive
    well beyond it) fails if the shutdown signal isn't wired into the live-wait race."""
    broker = EventBroker()
    shutdown = asyncio.Event()

    async def _drain() -> None:
        stream = Stream(broker, _ConnectedRequest(), Cursor(0), _RESERVED_COMMENT, shutdown, keepalive_seconds=30.0)  # type: ignore[arg-type]
        async for _ in stream.frames():
            pass

    task = asyncio.ensure_future(_drain())
    await asyncio.sleep(0.05)  # let the generator subscribe and reach its live wait
    assert broker.subscriber_count() == 1

    shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)

    # The generator's `finally: broker.unsubscribe(sub)` ran — no leaked subscriber.
    assert broker.subscriber_count() == 0


# --- EarlyShutdownServer --------------------------------------------------------------


def test_handle_exit_sets_the_shutdown_signal_synchronously() -> None:
    shutdown = asyncio.Event()
    server = EarlyShutdownServer(uvicorn.Config(FastAPI(), log_config=None), shutdown_signal=shutdown)

    server.handle_exit(signal.SIGTERM, None)

    assert shutdown.is_set()
