"""The event broker and the SSE stream — typed emission + live fan-out (component tier)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from blizzard.hub.events.broker import EventBroker
from tests.support import build_hub, drain_stream, emitted_events, pointer_token

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "5"}


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
    broker.publish_answer_delivered("ch_1", "qn_1")
    broker.publish_decision_opened("ch_1", "dec_1")
    broker.publish_decision_resolved("ch_1", "dec_1")
    broker.publish_queue_changed()
    broker.publish_runner_changed("runner-a", kind="heartbeat")
    types = [e.type for e in broker.snapshot()]
    assert types == [
        "question-asked",
        "question-answered",
        "answer-delivered",
        "decision-opened",
        "decision-resolved",
        "queue-changed",
        "runner-changed",
    ]
    # The delivered frame identifies the question the same way its siblings do — the
    # board re-reads the chunk for the trail itself (issue #165).
    assert json.loads(broker.snapshot()[2].data) == {"chunk_id": "ch_1", "question_id": "qn_1"}


def test_runner_changed_carries_by_and_reason_only_where_they_apply() -> None:
    """The frame's optional fields are present-when-meaningful, not always-null (issue
    #151) — a heartbeat has no actor and no note, so it says nothing about either."""
    broker = EventBroker()
    broker.publish_runner_changed("runner-a", kind="heartbeat")
    broker.publish_runner_changed("runner-a", kind="paused", by="alice")
    broker.publish_runner_changed("runner-a", kind="locally-paused", by="runner-ceiling", reason="spend cap hit")
    payloads = [json.loads(e.data) for e in broker.snapshot()]
    assert payloads == [
        {"runner_id": "runner-a", "kind": "heartbeat"},
        {"runner_id": "runner-a", "kind": "paused", "by": "alice"},
        {"runner_id": "runner-a", "kind": "locally-paused", "by": "runner-ceiling", "reason": "spend cap hit"},
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
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub.client.post(
        "/api/fleet/routes",
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
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    # After ingest the latest id is known; a reconnect past it replays only newer events.
    resume_from = hub.events.latest_id()
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    events = await drain_stream(hub.events, last_event_id=resume_from)
    assert events, "reconnect should replay the events published after the cursor"
    assert all(int(e["id"]) > resume_from for e in events)


def test_route_emission_lands_in_the_replay_buffer(tmp_path: Path) -> None:
    """The mutating routes publish typed events — asserted on the broker's replay tail."""
    hub = build_hub(tmp_path)
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    events = emitted_events(hub)
    assert [e["event"] for e in events] == [
        "chunk-changed",  # ingest -> not_ready (no queue-changed: not in the ready queue)
        "chunk-changed",  # claim -> running
        "queue-changed",  # claim removed it from the queue
    ]


def test_every_runner_changed_publish_site_names_its_kind(tmp_path: Path) -> None:
    """All four ``publish_runner_changed`` sites, driven through their own routes (issue
    #151). The kind is what a consumer filters on, so a site that published a bare
    ``runner_id`` would be indistinguishable from the heartbeat flood and silently muted."""
    hub = build_hub(tmp_path)

    # 1. Registration — the runner's pull-loop liveness beat, by far the loudest site.
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201
    # 2. The explicit slow heartbeat.
    assert hub.client.post("/api/fleet/runners/r1/heartbeats").status_code == 204
    # 3. Hub-side pause/resume — the operator's brake, which carries who set it.
    assert hub.client.post("/api/runners/r1/pause", json={"by": "alice"}).status_code == 200
    assert hub.client.post("/api/runners/r1/resume", json={"by": "alice"}).status_code == 200
    # 4. Runner-local pause/resume facts — the runner braked itself, and says why.
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {"seq": 1, "kind": "runner.locally_paused", "payload": {"by": "runner-ceiling", "reason": "cap hit"}},
                {"seq": 2, "kind": "runner.locally_resumed", "payload": {"by": "bob"}},
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    frames = [json.loads(e["data"]) for e in emitted_events(hub) if e["event"] == "runner-changed"]
    assert frames == [
        {"runner_id": "r1", "kind": "registered"},
        {"runner_id": "r1", "kind": "heartbeat"},
        {"runner_id": "r1", "kind": "paused", "by": "alice"},
        {"runner_id": "r1", "kind": "resumed", "by": "alice"},
        {"runner_id": "r1", "kind": "locally-paused", "by": "runner-ceiling", "reason": "cap hit"},
        # No `reason` on the fact — the frame omits it rather than carrying an empty note.
        {"runner_id": "r1", "kind": "locally-resumed", "by": "bob"},
    ]
