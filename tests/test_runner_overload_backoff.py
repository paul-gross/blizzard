"""Provider-overload backoff — loop component tier (blizzard#595).

A worker/judge exit an adapter classifies overloaded is not judged, spends no retry, and
keeps its epoch: short of the streak limit, the lease backs off and wakes the same session
once ``resume_after`` passes. Real (tmp sqlite) store, fakes at the seams — mirrors
``test_runner_paused.py``'s usage-limit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.domain.overload import BACKOFF_LIMIT, backoff_delay
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.overload import ProviderOverload
from blizzard.runner.loop.steps import Advance
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_envelope, make_store

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _seed_exited_lease(store):  # type: ignore[no-untyped-def]
    """A build lease spawned into env e1; the worker has already exited (no probe entry)."""
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
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _ctx(store, harness, *, clock=None):  # type: ignore[no-untyped-def]
    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    return hub, make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=clock if clock is not None else FixedClock(_NOW),
    )


# --- A single overload backs off (blizzard#595) -------------------------------


def test_a_single_overload_backs_off_the_worker_generation_without_judging(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)
    overload = ProviderOverload(detail="Overloaded: 529")
    harness = FakeHarness(handle=_HANDLE, verdict="pass", overload=overload)
    clock = FixedClock(_NOW)
    _hub, ctx = _ctx(store, harness, clock=clock)

    Advance(ctx).run()

    assert harness.judged == []  # never judged — an overload is not a verdict to elicit
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    assert store.overload_streak("lease_1", 1) == 1
    facts = store.open_overload_facts()
    assert len(facts) == 1
    assert facts[0].resume_after == _NOW + backoff_delay(1)
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.epoch == 1 and lease.session_id == "sess-a"  # unmoved


def test_backing_off_is_a_no_op_until_resume_after_elapses(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)
    overload = ProviderOverload(detail="Overloaded: 529")
    harness = FakeHarness(handle=_HANDLE, verdict="pass", overload=overload)
    clock = FixedClock(_NOW)
    _hub, ctx = _ctx(store, harness, clock=clock)

    Advance(ctx).run()  # records the fact, backs off
    Advance(ctx).run()  # still short of resume_after — no wake, nothing re-recorded

    assert harness.resumed == []
    assert store.overload_streak("lease_1", 1) == 1

    clock.advance(backoff_delay(1) - timedelta(seconds=1))
    Advance(ctx).run()  # one second short — still a no-op

    assert harness.resumed == []


def test_a_backed_off_worker_wakes_the_same_lease_epoch_and_session_after_the_delay(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)
    overload = ProviderOverload(detail="Overloaded: 529")
    harness = FakeHarness(handle=_HANDLE, verdict="pass", overload=overload)
    clock = FixedClock(_NOW)
    _hub, ctx = _ctx(store, harness, clock=clock)

    Advance(ctx).run()  # records, backs off
    clock.advance(backoff_delay(1))
    harness.overload = None  # the harness recovered — the resumed generation must not re-overload
    Advance(ctx).run()  # resume_after has passed — wakes in place

    assert harness.resumed == [("/ws/e1", "sess-a", "# The provider was overloaded; retrying automatically.")]
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.epoch == 1 and lease.session_id == "sess-a"  # same epoch, same session
    assert store.attempt_count("ch_1", "nd_build") == 1  # still no retry consumed


# --- Consecutive overloads escalate, then fall through (blizzard#595, D6) -----


def test_consecutive_overloads_double_the_backoff_delay_until_the_limit(tmp_path):  # type: ignore[no-untyped-def]
    """Reconstructs the whole streak: ordinals 1-4 each back off for double the last delay,
    and the 5th (:data:`BACKOFF_LIMIT`) falls through to an ordinary judged completion."""
    store = _store(tmp_path)
    _seed_exited_lease(store)
    overload = ProviderOverload(detail="Overloaded: 529")
    harness = FakeHarness(handle=_HANDLE, verdict="pass", overload=overload)
    clock = FixedClock(_NOW)
    _hub, ctx = _ctx(store, harness, clock=clock)

    for streak_ordinal in range(1, BACKOFF_LIMIT):
        Advance(ctx).run()  # this generation's exit, classified overloaded — backs off
        assert store.overload_streak("lease_1", 1) == streak_ordinal
        facts = store.open_overload_facts()
        assert len(facts) == 1
        assert facts[0].streak_ordinal == streak_ordinal
        assert facts[0].resume_after == clock.now() + backoff_delay(streak_ordinal)
        clock.advance(backoff_delay(streak_ordinal))
        Advance(ctx).run()  # resume_after has passed — wakes the same lease in place

    assert len(harness.resumed) == BACKOFF_LIMIT - 1
    assert store.attempt_count("ch_1", "nd_build") == 1  # still no retry consumed across the whole streak

    Advance(ctx).run()  # the 5th consecutive exit — streak limit reached, falls through and launches

    assert store.overload_streak("lease_1", 1) == BACKOFF_LIMIT
    assert store.open_overload_facts() == []  # the fall-through fact carries no resume_after
    assert len(harness.judged) == 1  # judged as usual — the streak limit's own fall-through

    Advance(ctx).run()  # collects the verdict elicitation launched above

    completions = [f for f in store.pending_outbound() if f.kind == "completion.submitted"]
    assert len(completions) == 1
    assert store.attempt_count("ch_1", "nd_build") == 1  # the fall-through itself spends no retry either


# --- A clean exit resets an open streak (blizzard#595, D5) --------------------


def test_a_clean_exit_resets_an_open_streak_so_a_fresh_overload_starts_at_one(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_exited_lease(store)
    overload = ProviderOverload(detail="Overloaded: 529")
    harness = FakeHarness(handle=_HANDLE, verdict="pass", overload=overload)
    clock = FixedClock(_NOW)
    _hub, ctx = _ctx(store, harness, clock=clock)

    Advance(ctx).run()  # overloaded once — backs off
    clock.advance(backoff_delay(1))
    harness.overload = None  # recovered
    Advance(ctx).run()  # wakes in place

    assert store.overload_streak("lease_1", 1) == 1

    Advance(ctx).run()  # this resumed generation exits clean — no overload this time; launches

    assert store.overload_streak("lease_1", 1) == 0  # the open streak was reset
    assert len(harness.judged) == 1  # the fresh verdict elicitation, launched (not yet collected)

    # A later overload starts a new streak at ordinal 1 — advanced a tick so the reset and
    # this fact never land on the exact same instant (the supersession compares `>=`).
    clock.advance(timedelta(seconds=1))
    harness.overload = overload
    Advance(ctx).run()

    facts = store.open_overload_facts()
    assert len(facts) == 1
    assert facts[0].streak_ordinal == 1


# --- A judge elicitation's own overload (blizzard#595) -------------------------


def test_a_judge_elicitation_overload_backs_off_then_relaunches_a_fresh_elicitation(tmp_path):  # type: ignore[no-untyped-def]
    """The worker's own turn already finished before its verdict elicitation overloaded —
    the wake re-runs `Judgement`, launching a fresh elicitation, never the plain worker
    wake message (mirrors the usage-limit judge park's own D2)."""
    store = _store(tmp_path)
    _seed_exited_lease(store)
    overload = ProviderOverload(detail="Overloaded: 529")
    # 1st classify call: the worker's own, must read unoverloaded so the judge launches;
    # 2nd: the judge's own collect.
    harness = FakeHarness(handle=_HANDLE, verdict="pass", overload=overload, overload_from_call=2)
    clock = FixedClock(_NOW)
    _hub, ctx = _ctx(store, harness, clock=clock)

    Advance(ctx).run()  # launches the detached elicitation — not overloaded yet
    assert store.in_flight_elicitation("lease_1", 1) is not None

    Advance(ctx).run()  # collects it — the fake judge pid reads dead by default; overloaded

    assert len(harness.judged) == 1  # only the first (overloaded) elicitation launch so far
    assert store.overload_streak("lease_1", 1) == 1
    assert store.in_flight_elicitation("lease_1", 1) is not None  # left standing, not cleared
    assert [f for f in store.pending_outbound() if f.kind == "completion.submitted"] == []
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed

    Advance(ctx).run()  # still backing off — re-polled, nothing changes
    assert harness.judged == harness.judged[:1]

    clock.advance(backoff_delay(1))
    harness.overload = None  # the harness recovered — the fresh elicitation must not re-overload
    Advance(ctx).run()  # resume_after has passed — relaunches a fresh elicitation

    assert harness.resumed == []  # never the plain worker wake — nothing left to "continue"
    assert len(harness.judged) == 2  # a fresh elicitation, not a relaunch of the stale one

    Advance(ctx).run()  # collects the fresh elicitation — a real verdict this time

    completions = [f for f in store.pending_outbound() if f.kind == "completion.submitted"]
    assert len(completions) == 1
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.epoch == 1 and lease.session_id == "sess-a"  # unmoved
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed across the whole cycle
