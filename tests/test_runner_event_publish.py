"""Publish-at-mutation (D4, blizzard#317 Phase 3) — component tier.

One call site per event kind at minimum, more where a kind has several distinct trigger
seams worth covering. Each assertion is on the broker's own recorded frame, never on the
write it followed — the write's own component tests already pin the store side; these pin
that a frame was published, with the right cause."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.domain.takeover import TakeoverService
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ExternalSubscriptionUsageWindow,
)
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.dormant import DormantSession
from blizzard.runner.loop.drain import OutboundDrain
from blizzard.runner.loop.outbound import OutboundFacts
from blizzard.runner.loop.steps import Advance, ContextSample, ExternalUsageSample, Fill, Pull, SpendCeiling
from blizzard.wire.chunk import ChunkDetail, PauseView, RouteView
from blizzard.wire.facts import (
    EVENT_RECORDED,
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
    RUNNER_LOCALLY_PAUSED,
    USAGE_RECORDED,
)
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekEntry
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeSubscriptionSampler,
    FakeTranscriptSource,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
    make_stores,
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
    # The pid-recorded flip to `running` gets its own frame — the 'created' mint alone
    # leaves a re-reader seeing `spawning` until the next backstop poll (blizzard#317 review).
    assert lease_frames[1]["cause"] == "spawned"
    assert lease_frames[1]["lease_id"] == lease_frames[0]["lease_id"]

    env_frames = _frames(events, "environment-changed")
    assert env_frames == [{"chunk_id": "ch_1", "environment_id": "e1", "cause": "bound"}]

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

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default

    lease_frames = _frames(events, "lease-changed")
    assert lease_frames and lease_frames[-1]["cause"] == "escalated"
    assert lease_frames[-1]["lease_id"] == "lease_1"

    escalation_frames = _frames(events, "escalation-changed")
    assert escalation_frames == [{"chunk_id": "ch_1", "cause": "opened", "lease_id": "lease_1"}]


def test_attempt_close_publishes_lease_changed_with_the_closure_reason_as_cause(tmp_path: Path) -> None:
    """A retry closes with the *failure* reason itself (``reason`` threads straight
    through `Attempt.fail`'s retry branch); pinned here against the plain retry
    branch, distinct from the escalate branch above."""
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

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default

    lease_frames = _frames(events, "lease-changed")
    causes = [f["cause"] for f in lease_frames]
    # The closed attempt's own frame, then the fresh retry's mint and its own spawn.
    assert causes == ["failed", "created", "spawned"]
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
    assert env_frames == [{"chunk_id": "ch_1", "environment_id": "e1", "cause": "released"}]
    lease_frames = _frames(events, "lease-changed")
    assert lease_frames[-1]["cause"] == "released"


def test_env_release_release_binding_publishes_environment_released(tmp_path: Path) -> None:
    """`release_binding` (undoing a just-recorded claim that never landed) shares
    `_publish_released` with `release_chunk` above, but census F6 (review round 6) found
    no test drove this call site on its own — pinned directly here."""
    store = _store(tmp_path)
    events = EventBroker()
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        events=events,
    )

    ctx.env_release.release_binding("ch_1", [AcquiredEnvironment(environment_id="e1", workdir="/ws/e1")])

    env_frames = _frames(events, "environment-changed")
    assert env_frames == [{"chunk_id": "ch_1", "environment_id": "e1", "cause": "released"}]


# --- ask-changed(asked) — the API route ------------------------------------------------- #


def test_asks_api_route_publishes_ask_asked(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    token = "the-lease-token"
    store.record_lease_token("lease_1", TokenHash(token).hex, _NOW)
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    app = create_app(config, runner_stores=make_stores(store), events=events)

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
    assert ask_frames == [{"lease_id": "lease_1", "chunk_id": "ch_1", "question_id": "qn_1", "cause": "answered"}]
    # The resumed session's own flip back to a live pid (`DormantSession._wake`) gets the
    # same 'spawned' frame the fresh-spawn path publishes.
    lease_frames = _frames(events, "lease-changed")
    assert lease_frames[-1]["cause"] == "spawned"
    assert lease_frames[-1]["lease_id"] == "lease_1"


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


# --- lease-changed(dormant) — park without closure (review round 3, F1) ----------------- #


def test_dormant_park_on_ask_publishes_lease_changed_dormant(tmp_path: Path) -> None:
    """LeaseActivity.state flips to "parked" the instant `record_park` lands — the leases
    rail needs a frame to catch that, distinct from the ask itself (already covered by
    `record_ask`'s own 'asked' frame)."""
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
    ask = store.unforwarded_ask("lease_1")
    assert ask is not None
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        events=events,
    )
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None

    DormantSession(ctx, lease).park_on_ask(ask)

    lease_frames = _frames(events, "lease-changed")
    assert lease_frames[-1] == {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "cause": "dormant",
    }


def test_pull_reconcile_leases_publishes_lease_changed_dormant_on_operator_pause(tmp_path: Path) -> None:
    """The same LeaseActivity.state flip, reached via the operator-pause path
    (`Attempt.park_paused`) instead of an ask."""
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkDetail(
        chunk_id="ch_1",
        graph_id="gr_1",
        status=ChunkStatus.PAUSED,
        current_node_id="nd_build",
        latest_epoch=1,
        route=RouteView(runner_id="r1", workspace_id="ws1", environment_ids=["e1"]),
        pause=PauseView(by="operator", set_at="2026-07-16T12:00:00Z"),
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

    lease_frames = _frames(events, "lease-changed")
    assert lease_frames[-1] == {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "cause": "dormant",
    }


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
    assert escalation_frames == [{"chunk_id": "ch_1", "cause": "closed", "lease_id": "lease_1"}]


# --- takeover-changed(opened/closed) — via the domain service directly ------------------ #


def test_takeover_open_and_close_publish_takeover_changed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    store.record_park(lease_id="lease_1", chunk_id="ch_1", question_id="qn_1", parked_at=_NOW)
    service = TakeoverService(
        make_stores(store),
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
        }
    ]

    service.close("ch_1", opened.takeover_id)
    all_frames = _frames(events, "takeover-changed")
    assert all_frames[-1] == {
        "chunk_id": "ch_1",
        "takeover_id": opened.takeover_id,
        "cause": "closed",
    }


def test_takeover_force_open_over_a_live_worker_publishes_the_fence_bump_as_fact_changed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)  # active, live worker
    probe = FakeProbe(alive={(100, "start-100")})
    service = TakeoverService(
        make_stores(store),
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
    assert takeover_frames == [{"chunk_id": "ch_1", "takeover_id": "tko_1", "cause": "closed"}]


def test_outbound_drain_ack_republishes_fact_changed_on_the_same_seq(tmp_path: Path) -> None:
    """The enqueue frame alone leaves the fact log's ✓/· flush marker stuck unacked until
    the next backstop poll (blizzard#317 review) — the drain's own ack must re-announce."""
    store = _store(tmp_path)
    events = EventBroker()
    hub = FakeHub()
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        events=events,
    )
    OutboundFacts(ctx).event(chunk_id=None, lease_id=None, payload={"detail": "probe"}, at=_NOW)

    OutboundDrain(ctx).run()

    fact_frames = _frames(events, "fact-changed")
    assert len(fact_frames) == 2, "expected one frame at enqueue and one at ack"
    enqueue_frame, ack_frame = fact_frames
    assert enqueue_frame["seq"] == ack_frame["seq"] == 1
    assert hub.pushed and hub.pushed[0].seq == 1, "the fake hub never actually received the fact"


# --- fact-changed for the outbound_buffer writes that bypassed enqueue_outbound (review round 4, F1) --- #


def test_ceiling_pause_publishes_fact_changed(tmp_path: Path) -> None:
    """`record_local_pause` inserts straight into outbound_buffer, bypassing enqueue_outbound
    (the one member the prior census mapped to fact-changed) — so the fact-log row it always
    buffers went unannounced until the backstop next polled."""
    store = _store(tmp_path)
    events = EventBroker()
    store.record_usage(
        lease_id="lease_1",
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=UsageSample(
            kind="spawn",
            model="m",
            input_tokens=1,
            output_tokens=1,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=7.0,
        ),
        recorded_at=_NOW,
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1, runner_ceiling_usd=5.0),
        events=events,
    )

    SpendCeiling(ctx).run()

    fact_frames = _frames(events, "fact-changed")
    assert len(fact_frames) == 1
    assert fact_frames[0]["kind"] == RUNNER_LOCALLY_PAUSED
    assert fact_frames[0]["chunk_id"] is None
    assert fact_frames[0]["lease_id"] is None


def test_patch_runner_route_publishes_fact_changed(tmp_path: Path) -> None:
    """`record_local_pause`'s other call site (review round 6's F6): `SpendCeiling.run`
    above is pinned, but nothing drove `PATCH /api/runner` itself before this."""
    store = _store(tmp_path)
    events = EventBroker()
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    app = create_app(config, runner_stores=make_stores(store), events=events)

    with TestClient(app) as client:
        resp = client.patch("/api/runner", json={"paused": True, "by": "operator"})

    assert resp.status_code == 200, resp.text
    fact_frames = _frames(events, "fact-changed")
    assert len(fact_frames) == 1
    assert fact_frames[0]["kind"] == RUNNER_LOCALLY_PAUSED
    assert fact_frames[0]["chunk_id"] is None
    assert fact_frames[0]["lease_id"] is None


def test_usage_recorder_publishes_fact_changed_and_an_exact_replay_publishes_nothing(tmp_path: Path) -> None:
    """`record_usage` also inserts straight into outbound_buffer — D7 names only the usage
    sampler's own elapsed-time readout as backstop-bounded, not this fact-log row. An exact
    replay (same lease/generation/kind) enqueues nothing, so nothing is announced either."""
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        events=events,
    )
    lease = store.active_lease_for_chunk("ch_1")
    assert lease is not None
    sample = UsageSample(
        kind="spawn",
        model="m",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=1.0,
    )

    ctx.usage.record_sample(lease, generation=1, sample=sample)
    ctx.usage.record_sample(lease, generation=1, sample=sample)  # exact replay — idempotent no-op

    fact_frames = _frames(events, "fact-changed")
    assert len(fact_frames) == 1
    assert fact_frames[0]["kind"] == USAGE_RECORDED
    assert fact_frames[0]["chunk_id"] == "ch_1"
    assert fact_frames[0]["lease_id"] == "lease_1"


def test_context_sample_crossing_publishes_fact_changed(tmp_path: Path) -> None:
    """`record_context_sample` buffers a report only on a first crossing — this pins that the
    occasional row it does buffer is announced, distinct from D7's elapsed-time-derived cadence."""
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)
    source = FakeTranscriptSource(context_tokens_by_session={"sess-a": 400_000})
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict=None, transcript_source=source),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(
            runner_id="r1",
            workspace_id="ws1",
            max_agents=1,
            context_warn_tokens=300_000,
            context_sample_interval_seconds=60,
        ),
        events=events,
    )

    ContextSample(ctx).run()

    fact_frames = _frames(events, "fact-changed")
    assert len(fact_frames) == 1
    assert fact_frames[0]["kind"] == EVENT_RECORDED
    assert fact_frames[0]["chunk_id"] == "ch_1"
    assert fact_frames[0]["lease_id"] == "lease_1"


def test_external_usage_sample_publishes_fact_changed(tmp_path: Path) -> None:
    """`record_external_usage_attempt` buffers a report only when the harness produced a
    sample — this pins that the occasional row it does buffer is announced."""
    store = _store(tmp_path)
    events = EventBroker()
    snapshot = ExternalSubscriptionUsageSnapshot(
        sampled_at=_NOW,
        windows=(
            ExternalSubscriptionUsageWindow(
                window="5h", utilization_pct=42.0, resets_at=_NOW + timedelta(hours=5), window_seconds=18_000
            ),
        ),
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict=None),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        events=events,
        subscription_sampler=FakeSubscriptionSampler(snapshot=snapshot),
    )

    ExternalUsageSample(ctx).run()

    fact_frames = _frames(events, "fact-changed")
    assert len(fact_frames) == 1
    assert fact_frames[0]["kind"] == EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED
    assert fact_frames[0]["chunk_id"] is None
    assert fact_frames[0]["lease_id"] is None


def test_attempt_retry_closure_publishes_fact_changed_for_its_own_event(tmp_path: Path) -> None:
    """`record_closure`'s optional `event` also bypasses enqueue_outbound — a retry's own
    "attempt failed, retrying" fact-log row went unannounced. Seeds an unrelated fact so
    the expected seq isn't the trivially-correct 1, and checks it against the written row."""
    store = _store(tmp_path)
    events = EventBroker()
    _seed_lease(store, retries_max=2)  # retried=0 < 2 -> retry, closes with an event
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
    OutboundFacts(ctx).event(chunk_id=None, lease_id=None, payload={"detail": "seed"}, at=_NOW)

    Advance(ctx).run()  # launches the detached elicitation
    Advance(ctx).run()  # collects it — the fake pid reads dead by default

    # Filter out the retry's own lease.minted frame and the seed above, to the
    # closure's own event by the chunk/lease it's actually scoped to.
    fact_frames = [
        f
        for f in _frames(events, "fact-changed")
        if f["kind"] == EVENT_RECORDED and f["chunk_id"] == "ch_1" and f["lease_id"] == "lease_1"
    ]
    assert len(fact_frames) == 1
    written = [f for f in store.pending_outbound() if f.kind == EVENT_RECORDED and f.chunk_id == "ch_1"]
    assert len(written) == 1
    assert fact_frames[0]["seq"] == written[0].seq > 1


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
