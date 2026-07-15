"""The event broker and the SSE stream (D-067) — typed emission + live fan-out (component tier)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from blizzard.hub.events.broker import EventBroker
from tests.support import build_hub, drain_stream, emitted_events

pytestmark = pytest.mark.component

_POINTER = {"provider": "github", "url": "http://forge.local/repos/acme/widget/issues/5"}


def test_broker_frames_events_with_monotonic_ids() -> None:
    broker = EventBroker()
    first = broker.publish_chunk_changed("ch_1", "ready")
    second = broker.publish_chunk_changed("ch_1", "running")
    assert (first, second) == (1, 2)  # monotonic ids so a reconnect can resume
    events = broker.snapshot()
    assert [e.framed().startswith(f"id: {e.id}\nevent: chunk-changed\n") for e in events] == [True, True]
    assert '"status": "running"' in events[1].framed()


def test_broker_replay_since_returns_only_newer_events() -> None:
    broker = EventBroker()
    broker.publish_chunk_changed("ch_1", "ready")
    broker.publish_queue_changed()
    third = broker.publish_chunk_changed("ch_1", "running")
    tail = broker.replay_since(2)
    assert [e.id for e in tail] == [third]
    assert tail[0].type == "chunk-changed"


def test_broker_typed_event_vocabulary() -> None:
    broker = EventBroker()
    broker.publish_question_asked("ch_1", "qn_1")
    broker.publish_question_answered("ch_1", "qn_1")
    broker.publish_decision_opened("ch_1", "dec_1")
    broker.publish_decision_resolved("ch_1", "dec_1")
    broker.publish_queue_changed()
    broker.publish_runner_changed("runner-a")
    types = [e.type for e in broker.snapshot()]
    assert types == [
        "question-asked",
        "question-answered",
        "decision-opened",
        "decision-resolved",
        "queue-changed",
        "runner-changed",
    ]


def test_broker_live_fanout_delivers_to_a_subscriber() -> None:
    """A subscriber captures its loop; a publish fans out live across the thread boundary."""

    async def scenario() -> None:
        broker = EventBroker()
        sub = broker.subscribe()
        # call_soon_threadsafe schedules the put on this same running loop.
        broker.publish_chunk_changed("ch_live", "running")
        event = await asyncio.wait_for(sub.queue.get(), timeout=1.0)
        assert event.type == "chunk-changed"
        assert '"chunk_id": "ch_live"' in event.framed()
        broker.unsubscribe(sub)

    asyncio.run(scenario())


async def test_lifecycle_publishes_events_and_the_stream_replays_them(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]
    hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )

    # Drive the SSE endpoint's own generator (a real stream read of the replay tail): ingest
    # emits chunk-changed(not_ready); the claim emits chunk-changed(running)+queue-changed.
    events = await drain_stream(hub.events, last_event_id=0)
    types = [e["event"] for e in events]
    assert "chunk-changed" in types
    assert "queue-changed" in types
    assert any(chunk_id in e["data"] and '"status": "running"' in e["data"] for e in events)
    # Ids are monotonic and strictly increasing across the replayed tail.
    ids = [int(e["id"]) for e in events]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


async def test_stream_resumes_from_last_event_id(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]
    # After ingest the latest id is known; a reconnect past it replays only newer events.
    resume_from = hub.events.latest_id()
    hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    events = await drain_stream(hub.events, last_event_id=resume_from)
    assert events, "reconnect should replay the events published after the cursor"
    assert all(int(e["id"]) > resume_from for e in events)


def test_route_emission_lands_in_the_replay_buffer(tmp_path: Path) -> None:
    """The mutating routes publish typed events — asserted on the broker's replay tail."""
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"pointers": [_POINTER]}).json()["chunk_id"]
    hub.client.post(
        "/api/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    events = emitted_events(hub)
    assert [e["event"] for e in events] == [
        "chunk-changed",  # ingest -> not_ready (no queue-changed: not in the ready queue, D-103)
        "chunk-changed",  # claim -> running
        "queue-changed",  # claim removed it from the queue
    ]
