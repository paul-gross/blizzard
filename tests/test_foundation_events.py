"""The shared SSE core — the kind-agnostic broker, the stream-response
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
from blizzard.foundation.events.stream import Cursor, CursorRefused, Stream

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


def _asking(*, query: dict[str, str] | None = None, header: str | None = None) -> _FakeRequest:
    request = _FakeRequest()
    request.query_params.update(query or {})
    if header is not None:
        request.headers["last-event-id"] = header
    return request


def test_cursor_of_from_latest_asks_to_join_at_the_latest_event() -> None:
    cursor = Cursor.of(_asking(query={"from": "latest"}))  # type: ignore[arg-type]
    assert cursor.from_latest
    assert cursor.resume_from(9) == 9


@pytest.mark.parametrize(
    ("header", "joined_at", "expected"),
    [("4", 9, 4), ("9", 9, 9), ("junk", 9, 9), ("", 9, 9), ("12", 9, 9)],
)
def test_from_latest_resumes_only_from_a_readable_header_at_or_below_the_mark(
    header: str, joined_at: int, expected: int
) -> None:
    cursor = Cursor.of(_asking(query={"from": "latest"}, header=header))  # type: ignore[arg-type]
    assert cursor.resume_from(joined_at) == expected


def test_without_from_a_cursor_above_the_mark_degrades_to_the_whole_tail() -> None:
    assert Cursor.of(_asking(header="12")).resume_from(9) == 0  # type: ignore[arg-type]
    assert Cursor.of(_asking(header="4")).resume_from(9) == 4  # type: ignore[arg-type]
    assert Cursor.of(_asking()).resume_from(9) == 0  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["earliest", "", "LATEST"])
def test_cursor_of_refuses_an_unknown_from(value: str) -> None:
    with pytest.raises(CursorRefused):
        Cursor.of(_asking(query={"from": value}))  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["1", ""])
def test_cursor_of_refuses_from_latest_with_a_last_event_id_query(value: str) -> None:
    with pytest.raises(CursorRefused):
        Cursor.of(_asking(query={"from": "latest", "last_event_id": value}))  # type: ignore[arg-type]


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


async def test_stream_delivers_live_events_past_a_cursor_that_outruns_a_fresh_broker() -> None:
    """A cursor above a fresh broker's own high-water mark belongs to some other
    broker instance — trusting it as this one's last-sent watermark would discard
    every live event until this broker's own ids caught back up to it."""
    broker = EventBroker()  # a fresh broker — as after a daemon restart
    stream = Stream(broker, _ConnectedRequest(), Cursor(500), _RESERVED_COMMENT, keepalive_seconds=30.0)  # type: ignore[arg-type]
    frames = stream.frames()
    try:
        first = await asyncio.wait_for(frames.__anext__(), timeout=1.0)
        assert first == _RESERVED_COMMENT.encode()
        broker.publish("widget-changed", {"id": "w1"})
        live = await asyncio.wait_for(frames.__anext__(), timeout=1.0)
        assert b"widget-changed" in live
    finally:
        await frames.aclose()  # type: ignore[attr-defined]


async def test_stream_replays_a_fresh_brokers_buffered_tail_past_a_stale_cursor() -> None:
    """The replay half of the same clamp: the live-delivery test above
    never publishes before connecting, so it can't catch a stale cursor reaching
    ``replay_since`` unresolved, which silently empties the tail."""
    broker = EventBroker()  # a fresh broker, already holding buffered events before connect
    broker.publish("widget-changed", {"id": "w1"})
    broker.publish("widget-changed", {"id": "w2"})
    stream = Stream(broker, _DisconnectedRequest(), Cursor(500), _RESERVED_COMMENT)  # type: ignore[arg-type]
    frames = [chunk async for chunk in stream.frames()]
    assert frames[0] == _RESERVED_COMMENT.encode()
    assert len(frames) == 3, frames
    assert b"w1" in frames[1]
    assert b"w2" in frames[2]


async def test_stream_emits_a_keepalive_on_an_idle_connection_at_the_injected_interval() -> None:
    """The keepalive interval is an injected value — bounding it well below the
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


async def test_subscribe_records_the_latest_id_it_registered_at() -> None:
    broker = EventBroker()
    broker.publish("widget-changed", {"id": "w1"})
    assert broker.subscribe().joined_at == 1
    broker.close()
    assert broker.subscribe().joined_at == 1


async def test_a_from_latest_stream_sends_only_events_published_after_it_registered() -> None:
    broker = EventBroker()
    broker.publish("widget-changed", {"id": "before"})
    request = _asking(query={"from": "latest"})
    stream = Stream(broker, request, Cursor.of(request), _RESERVED_COMMENT, keepalive_seconds=30.0)  # type: ignore[arg-type]
    frames = stream.frames()
    try:
        assert await asyncio.wait_for(frames.__anext__(), timeout=1.0) == _RESERVED_COMMENT.encode()
        broker.publish("widget-changed", {"id": "after"})
        live = await asyncio.wait_for(frames.__anext__(), timeout=1.0)
        assert b"after" in live
        assert b"before" not in live
    finally:
        await frames.aclose()  # type: ignore[attr-defined]


async def test_an_event_published_around_registration_arrives_exactly_once() -> None:
    class _RacingBroker(EventBroker):
        def subscribe(self, owner: str | None = None, session: str | None = None):
            self.publish("widget-changed", {"id": "pre"})
            sub = super().subscribe(owner, session)
            self.publish("widget-changed", {"id": "post"})
            return sub

    broker = _RacingBroker()
    request = _asking(query={"from": "latest"})
    stream = Stream(broker, request, Cursor.of(request), _RESERVED_COMMENT, keepalive_seconds=30.0)  # type: ignore[arg-type]
    frames = stream.frames()
    try:
        assert await asyncio.wait_for(frames.__anext__(), timeout=1.0) == _RESERVED_COMMENT.encode()
        live = await asyncio.wait_for(frames.__anext__(), timeout=1.0)
        assert b"post" in live
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(frames.__anext__(), timeout=0.1)
    finally:
        await frames.aclose()  # type: ignore[attr-defined]
