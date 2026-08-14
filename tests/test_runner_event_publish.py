"""Publish-at-mutation (D4, blizzard#317 Phase 3) — component tier.

One call site per event kind at minimum, more where a kind has several distinct trigger
seams worth covering. Each assertion is on the broker's own recorded frame, never on the
write it followed — the write's own component tests already pin the store side; these pin
that a frame was published, with the right cause, strictly after that write returned."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.enrollment import TokenHash
from blizzard.hub.domain.work import ChunkStatus
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.takeover import TakeoverService
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import Advance, Fill, Pull
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import ChunkDetail, RouteView
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekEntry
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path: Path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _frames(events: EventBroker, kind: str) -> list[dict[str, object]]:
    return [json.loads(e.data) for e in events.snapshot() if e.type == kind]


def _seed_lease(store, *, retries_max: int, chunk="ch_1", lease="lease_1", epoch=1):  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            runner_id="r1",
            retries_max=retries_max,
            created_at=_NOW,
        )
    )
    store.record_spawn(lease, pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


# --- lease-changed + environment-changed(bound) + fact-changed (mint) ------------------ #


def test_fill_claim_publishes_lease_created_environment_bound_and_fact_changed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    hub = FakeHub()
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        events=events,
    )

    Fill(ctx).run()

    lease_frames = _frames(events, "lease-changed")
    assert lease_frames, "no lease-changed frame published on mint"
    assert lease_frames[0]["cause"] == "created"
    assert lease_frames[0]["chunk_id"] == "ch_1"
    assert lease_frames[0]["key"] == f"leases:{lease_frames[0]['lease_id']}"

    env_frames = _frames(events, "environment-changed")
    assert env_frames == [{"chunk_id": "ch_1", "environment_id": "e1", "cause": "bound", "key": "environments:e1"}]

    fact_frames = _frames(events, "fact-changed")
    assert any(f["kind"] == "lease.minted" for f in fact_frames), "the mint's own lease.minted fact never published"


# --- lease-changed(closed) + escalation-changed(opened) --------------------------------- #


def test_attempt_escalate_publishes_lease_escalated_and_escalation_opened(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=0)  # exhausted -> escalate
    hub = FakeHub()
    hub.envelopes = {"ch_1": make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])}
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),  # no parseable verdict -> failure
        probe=FakeProbe(),  # empty alive set -> worker dead
        events=events,
    )

    Advance(ctx).run()

    lease_frames = _frames(events, "lease-changed")
    assert lease_frames and lease_frames[-1]["cause"] == "escalated"
    assert lease_frames[-1]["lease_id"] == "lease_1"

    escalation_frames = _frames(events, "escalation-changed")
    assert escalation_frames == [
        {"chunk_id": "ch_1", "cause": "opened", "lease_id": "lease_1", "key": "escalations:ch_1"}
    ]


def test_attempt_close_publishes_lease_changed_with_the_closure_reason_as_cause(tmp_path: Path) -> None:
    """A retry closes ``transitioned``... no — a retry closes with the *failure* reason
    itself (``reason`` threads straight through `Attempt.fail`'s retry branch); pinned
    here against the plain retry branch, distinct from the escalate branch above."""
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)  # retried=0 < 2 -> retry, closes with reason="failed"
    hub = FakeHub()
    hub.envelopes = {"ch_1": make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])}
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        events=events,
    )

    Advance(ctx).run()

    lease_frames = _frames(events, "lease-changed")
    causes = [f["cause"] for f in lease_frames]
    # The closed attempt's own frame, then the fresh retry's mint.
    assert causes == ["failed", "created"]
    # No escalation on a plain retry.
    assert _frames(events, "escalation-changed") == []


# --- environment-changed(released) ------------------------------------------------------ #


def test_pull_abandon_publishes_environment_released(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.STOPPED,
        current_node_id="nd_build",
        latest_epoch=1,
        route=None,
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        events=events,
    )

    Pull(ctx).run()

    env_frames = _frames(events, "environment-changed")
    assert env_frames == [{"chunk_id": "ch_1", "environment_id": "e1", "cause": "released", "key": "environments:e1"}]
    lease_frames = _frames(events, "lease-changed")
    assert lease_frames[-1]["cause"] == "released"


# --- ask-changed(asked) — the API route ------------------------------------------------- #


def test_asks_api_route_publishes_ask_asked(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    token = "the-lease-token"
    store.record_lease_token("lease_1", TokenHash(token).hex, _NOW)
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    app = create_app(config, runner_store=store, events=events)

    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_1/asks",
            json={"question": "Which API?", "options": ["rest", "graphql"]},
            headers={"X-Blizzard-Lease-Token": token},
        )

    assert resp.status_code == 201, resp.text
    question_id = resp.json()["question_id"]
    ask_frames = _frames(events, "ask-changed")
    assert ask_frames == [
        {
            "lease_id": "lease_1",
            "chunk_id": "ch_1",
            "question_id": question_id,
            "cause": "asked",
            "key": f"asks:{question_id}",
        }
    ]


# --- ask-changed(answered) --------------------------------------------------------------- #


def test_dormant_on_answer_publishes_ask_answered(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Which API?",
        options=["rest", "graphql"],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    hub = FakeHub()
    hub.questions["qn_1"] = QuestionView(
        question_id="qn_1",
        chunk_id="ch_1",
        runner_id="r1",
        epoch=1,
        question="Which API?",
        asked_at="t",
        answered=True,
        answer="rest",
        answered_by="alice",
        answered_at="t2",
    )
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = make_context(
        store, hub=hub, provider=FakeProvider({"e1": "/ws/e1"}), harness=harness, probe=FakeProbe(), events=events
    )

    Advance(ctx).run()

    ask_frames = _frames(events, "ask-changed")
    assert ask_frames == [
        {"lease_id": "lease_1", "chunk_id": "ch_1", "question_id": "qn_1", "cause": "answered", "key": "asks:qn_1"}
    ]


def test_attempt_abandon_retiring_an_open_park_does_not_publish_ask_answered(tmp_path: Path) -> None:
    """The same store method (``record_park_resume``) fires from `Attempt.abandon` too,
    retiring a stranded park with no answer — the census's documented silent call site."""
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="Which API?",
        options=[],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.STOPPED,
        current_node_id="nd_build",
        latest_epoch=1,
        route=None,
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        events=events,
    )

    Pull(ctx).run()

    assert _frames(events, "ask-changed") == []
    # The abandon's own lease-changed(released) frame still fires — the client re-reads
    # through it instead.
    assert _frames(events, "lease-changed")[-1]["cause"] == "released"


# --- escalation-changed(closed) ---------------------------------------------------------- #


def test_pull_reconcile_escalations_publishes_escalation_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.STOPPED,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(alive=set()),
        # Strictly after the escalation's own `closed_at` — the closure mark only
        # supersedes an escalation that precedes it.
        clock=FixedClock(_NOW + timedelta(minutes=5)),
        events=events,
    )

    Pull(ctx).run()

    assert store.open_escalations() == []
    escalation_frames = _frames(events, "escalation-changed")
    assert escalation_frames == [
        {"chunk_id": "ch_1", "cause": "closed", "lease_id": "lease_1", "key": "escalations:ch_1"}
    ]


# --- takeover-changed(opened/closed) — via the domain service directly ------------------ #


def test_takeover_open_and_close_publish_takeover_changed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    service = TakeoverService(
        store,
        FixedClock(_NOW),
        FakeHarness(handle=_HANDLE, verdict=None),
        FakeProbe(),
        local_api_url="http://x",
        events=events,
    )

    opened = service.open("ch_1", force=False)
    open_frames = _frames(events, "takeover-changed")
    assert open_frames == [
        {
            "chunk_id": "ch_1",
            "takeover_id": opened.takeover_id,
            "cause": "opened",
            "key": f"takeovers:{opened.takeover_id}",
        }
    ]

    service.close("ch_1", opened.takeover_id)
    all_frames = _frames(events, "takeover-changed")
    assert all_frames[-1] == {
        "chunk_id": "ch_1",
        "takeover_id": opened.takeover_id,
        "cause": "closed",
        "key": f"takeovers:{opened.takeover_id}",
    }


def test_takeover_force_open_over_a_live_worker_publishes_the_fence_bump_as_fact_changed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)  # active, live worker
    probe = FakeProbe(alive={(100, "start-100")})
    service = TakeoverService(
        store,
        FixedClock(_NOW),
        FakeHarness(handle=_HANDLE, verdict=None),
        probe,
        local_api_url="http://x",
        events=events,
    )

    service.open("ch_1", force=True)

    fact_frames = _frames(events, "fact-changed")
    assert any(f["kind"] == "lease.minted" and f["chunk_id"] == "ch_1" for f in fact_frames)


def test_pull_reconcile_takeovers_publishes_takeover_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id="lease_1",
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.STOPPED,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(alive=set()),
        events=events,
    )

    Pull(ctx).run()

    takeover_frames = _frames(events, "takeover-changed")
    assert takeover_frames == [
        {"chunk_id": "ch_1", "takeover_id": "tko_1", "cause": "closed", "key": "takeovers:tko_1"}
    ]


# --- a broker-less context publishes nothing, degrading cleanly (D2) -------------------- #


def test_no_broker_wired_publishes_nothing_and_raises_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hub = FakeHub()
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        # events omitted — defaults to None
    )

    Fill(ctx).run()  # must not raise

    assert store.list_active_leases()  # the mutation itself still happened
