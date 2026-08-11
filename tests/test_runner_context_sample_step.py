"""``ContextSample`` — the tick's live session-context warn lane.

The bounds a graph declares are evaluated at SPAWN time, against the pool head a node-step
is about to resume; that leaves the inside of a long invocation unobserved, which is where
a runaway session actually spends. This step samples a *running* lease's context on a
cadence and warns once on crossing.

Observation only, and these tests pin that: crossing the line enqueues a report and changes
nothing else — no lease is killed, requeued, or made ineligible by anything here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import ContextSample
from blizzard.runner.store.repository import NewLease
from blizzard.wire.facts import EVENT_RECORDED
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    make_context,
    make_store,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 11, 9, 0, 0, tzinfo=UTC)
_SESSION = "sess-live"
_HANDLE = WorkerHandle(session_id=_SESSION, pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_running_lease(store, *, lease_id: str = "lease_1", session_id: str | None = _SESSION) -> str:
    """One active lease, spawned (so it bears a session) — what the step sweeps."""
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            session_name="code",
            resolved_model="sonnet",
            resolved_effort="medium",
            created_at=_NOW,
        )
    )
    if session_id is not None:
        store.record_spawn(lease_id, pid=1, process_start_time="t", session_id=session_id, spawned_at=_NOW)
    return lease_id


def _ctx(store, *, tokens: int | None, clock: FixedClock, warn: int | None = 300_000, interval: int = 60):  # type: ignore[no-untyped-def]
    source = FakeTranscriptSource(context_tokens_by_session={_SESSION: tokens} if tokens is not None else {})
    harness = FakeHarness(handle=_HANDLE, verdict="pass", transcript_source=source)
    return (
        make_context(
            store,
            hub=FakeHub(),
            provider=FakeProvider({"e1": "/ws/e1"}),
            harness=harness,
            probe=FakeProbe(),
            clock=clock,
            config=LoopConfig(
                runner_id="r1",
                workspace_id="ws1",
                max_agents=1,
                context_warn_tokens=warn,
                context_sample_interval_seconds=interval,
            ),
        ),
        source,
    )


def _warnings(store) -> list[dict]:  # type: ignore[no-untyped-def]
    return [json.loads(f.payload) for f in store.pending_outbound() if f.kind == EVENT_RECORDED]


@pytest.mark.unit
def test_an_unconfigured_warn_line_samples_nothing_at_all(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Absent `context_warn_tokens` is off, not "warn at some default": a runner that never
    opts in must not pay a transcript read per lease per tick for a lane it does not use."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    ctx, source = _ctx(store, tokens=999_999, clock=FixedClock(_NOW), warn=None)

    ContextSample(ctx).run()

    assert source.context_tokens_calls == []
    assert store.context_sample_state("lease_1") is None


@pytest.mark.unit
def test_a_lease_under_the_line_is_sampled_but_never_warned(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    ctx, _ = _ctx(store, tokens=120_000, clock=FixedClock(_NOW))

    ContextSample(ctx).run()

    state = store.context_sample_state("lease_1")
    assert state is not None
    assert state.max_context_tokens == 120_000
    assert state.last_sampled_at == _NOW
    assert _warnings(store) == []


@pytest.mark.unit
def test_crossing_the_line_warns_exactly_once_while_sampling_continues(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The warning is a state change, not a level. A session past the line stays past it for
    the rest of its life, so re-reporting every cadence would bury the crossing it announced —
    while the samples themselves must keep landing, since the curve is the point of the lane."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    clock = FixedClock(_NOW)
    ctx, _ = _ctx(store, tokens=420_000, clock=clock)

    ContextSample(ctx).run()

    warnings = _warnings(store)
    assert len(warnings) == 1
    event = warnings[0]
    # `event.recorded`'s own shape — the lane both the real and the mock hub already ingest.
    assert event["severity"] == "warning"
    assert event["kind"] == "worker-context-warned"
    assert (event["chunk_id"], event["lease_id"], event["node_name"]) == ("ch_1", "lease_1", "build")
    assert event["detail"] == {
        "session_id": _SESSION,
        "context_tokens": 420_000,
        "warn_tokens": 300_000,
        "sampled_at": "2026-08-11T09:00:00+00:00",
    }

    clock.advance(timedelta(seconds=120))
    ContextSample(ctx).run()

    assert len(_warnings(store)) == 1  # still one — the crossing already reported
    state = store.context_sample_state("lease_1")
    assert state is not None
    assert state.last_sampled_at == _NOW + timedelta(seconds=120)  # but the sample landed


@pytest.mark.unit
def test_the_cadence_gate_holds_a_second_read_inside_the_interval(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    clock = FixedClock(_NOW)
    ctx, source = _ctx(store, tokens=120_000, clock=clock, interval=60)

    ContextSample(ctx).run()
    clock.advance(timedelta(seconds=30))
    ContextSample(ctx).run()

    assert source.context_tokens_calls == [_SESSION]  # the second tick never read

    clock.advance(timedelta(seconds=31))
    ContextSample(ctx).run()
    assert source.context_tokens_calls == [_SESSION, _SESSION]


@pytest.mark.unit
def test_an_unmeasurable_context_records_an_attempt_that_measures_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`None` is *unknown*; a 0 in its place would poison the curve and read as reassurance. The
    attempt is still recorded so the cadence anchor advances — else an unreadable transcript is
    re-read every tick instead of once per interval."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    clock = FixedClock(_NOW)
    ctx, source = _ctx(store, tokens=None, clock=clock, interval=60)

    ContextSample(ctx).run()

    state = store.context_sample_state("lease_1")
    assert state is not None
    assert state.max_context_tokens is None  # attempted, measured nothing
    assert state.last_sampled_at == _NOW
    assert _warnings(store) == []

    clock.advance(timedelta(seconds=30))
    ContextSample(ctx).run()
    assert source.context_tokens_calls == [_SESSION]  # held by the cadence gate, not re-read


@pytest.mark.unit
def test_a_lease_with_no_session_yet_is_skipped(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A minted-but-unspawned lease has no session to read — not an error, just nothing yet."""
    store = _store(tmp_path)
    _seed_running_lease(store, session_id=None)
    ctx, source = _ctx(store, tokens=420_000, clock=FixedClock(_NOW))

    ContextSample(ctx).run()

    assert source.context_tokens_calls == []
    assert _warnings(store) == []


@pytest.mark.unit
def test_one_leases_raising_read_does_not_cost_the_others_their_samples(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A diagnostic lane must never be able to end the sweep it rides in. Two leases, the first
    of which raises: the assertion that matters is the SECOND one's sample landing, which a
    total abort would not satisfy."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    _seed_running_lease(store, lease_id="lease_2", session_id="sess-other")
    ctx, source = _ctx(store, tokens=120_000, clock=FixedClock(_NOW))

    def _raise(session_id: str, *, spawn_cwd: str | None) -> int | None:
        if session_id == _SESSION:
            raise RuntimeError("transcript unreadable (scripted)")
        return 140_000

    source.context_tokens = _raise  # type: ignore[method-assign]

    ContextSample(ctx).run()  # must not propagate

    assert store.context_sample_state("lease_1") is None  # the raising lease recorded nothing
    survivor = store.context_sample_state("lease_2")
    assert survivor is not None
    assert survivor.max_context_tokens == 140_000
