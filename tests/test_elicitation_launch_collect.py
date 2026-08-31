"""The detached judgement-elicitation launch/collect shape (blizzard#443) — unit tier.

Phase 1: a launch is one pass, a collect is a later one, and a still-running elicitation is
passed over untouched. Phase 2: a lost elicitation relaunches without consuming a retry,
abandons past its staleness bound, and every lease-closing path kills what it left running.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.attempt import Attempt
from blizzard.runner.loop.dormant import DormantSession
from blizzard.runner.loop.judgement import ELICITATION_STALENESS_THRESHOLD
from blizzard.runner.loop.steps import Advance
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

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _build_envelope(chunk="ch_1"):  # type: ignore[no-untyped-def]
    return make_envelope(chunk, "build", node_id="nd_build", choices=_CHOICES)


def _seed_running_lease(store, *, chunk="ch_1", lease="lease_1", pid=100, start="start-100", session="sess-a", epoch=1):  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(lease, pid=pid, process_start_time=start, session_id=session, spawned_at=_NOW)
    store.record_binding(chunk_id=chunk, environment_id="e1", workdir="/ws/e1", bound_at=_NOW)


def _ctx(store, *, harness, probe, clock=None):  # type: ignore[no-untyped-def]
    hub = FakeHub()
    hub.envelopes["ch_1"] = _build_envelope()
    hub.claim_outcome = claimed_outcome("ch_1", _build_envelope())
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=probe,
        clock=clock or FixedClock(_NOW),
    )


def test_advance_launches_then_collects_across_two_passes(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    ctx = _ctx(store, harness=harness, probe=FakeProbe())  # worker pid 100 not alive — exited

    Advance(ctx).run()  # launch only

    assert len(harness.judged) == 1
    elicitation = store.in_flight_elicitation("lease_1", 1)
    assert elicitation is not None
    assert elicitation.pid == 8888  # FakeHarness's default judge pid
    assert store.pending_outbound() == []  # nothing buffered yet — not collected

    Advance(ctx).run()  # collect — the fake elicitation pid reads dead by default

    assert store.in_flight_elicitation("lease_1", 1) is None
    kinds = [b.kind for b in store.pending_outbound()]
    assert "completion.submitted" in kinds


def test_collect_passes_over_a_still_running_elicitation(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    probe = FakeProbe()
    ctx = _ctx(store, harness=harness, probe=probe)

    Advance(ctx).run()  # launch
    elicitation = store.in_flight_elicitation("lease_1", 1)
    assert elicitation is not None and elicitation.pid is not None
    probe.alive = {(elicitation.pid, elicitation.process_start_time)}  # now script it as running

    Advance(ctx).run()  # would-be collect pass — must be a no-op

    assert store.in_flight_elicitation("lease_1", 1) is not None
    assert store.pending_outbound() == []


def test_lost_elicitation_relaunches_without_consuming_a_retry(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", judge_output="")  # writes nothing readable
    ctx = _ctx(store, harness=harness, probe=FakeProbe())

    Advance(ctx).run()  # launch
    Advance(ctx).run()  # collect finds no output — lost, relaunches

    assert len(harness.judged) == 2  # the relaunch is a second `judge` call
    elicitation = store.in_flight_elicitation("lease_1", 1)
    assert elicitation is not None
    assert elicitation.relaunch_count == 1
    assert elicitation.first_launched_at == _NOW  # D5 — the baseline never resets
    # No retry consumed: still the one and only attempt for this lease/node.
    assert store.attempt_count("ch_1", "nd_build") == 1


def test_elicitation_past_staleness_bound_fails_the_attempt(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", judge_output="")
    clock = FixedClock(_NOW)
    ctx = _ctx(store, harness=harness, probe=FakeProbe(), clock=clock)

    Advance(ctx).run()  # launch
    clock.advance(ELICITATION_STALENESS_THRESHOLD + timedelta(seconds=1))
    Advance(ctx).run()  # collect finds it lost, past the bound — fails the attempt

    assert store.in_flight_elicitation("lease_1", 1) is None
    assert store.active_lease("lease_1") is None  # closed
    # Retried (within budget): a fresh lease/epoch picks the node back up.
    fresh = store.active_lease_for_chunk("ch_1")
    assert fresh is not None and fresh.epoch == 2


def test_closing_a_lease_kills_its_in_flight_elicitation(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    probe = FakeProbe()
    ctx = _ctx(store, harness=harness, probe=probe)

    Advance(ctx).run()  # launch
    elicitation = store.in_flight_elicitation("lease_1", 1)
    assert elicitation is not None and elicitation.pid is not None
    probe.alive = {(elicitation.pid, elicitation.process_start_time)}

    lease = store.active_lease("lease_1")
    assert lease is not None
    Attempt(ctx, lease).abandon(via="test")

    assert elicitation.pid in probe.killed
    assert store.in_flight_elicitation("lease_1", 1) is None


def test_pause_park_kills_the_in_flight_elicitation_and_the_later_unpause_re_mints_cleanly(tmp_path):  # type: ignore[no-untyped-def]
    """D6/D7: closing on an operator pause kills the elicitation before parking, so the later
    resume re-mints a token for a lease with nothing left in flight to race."""
    store = _store(tmp_path)
    _seed_running_lease(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    probe = FakeProbe()
    ctx = _ctx(store, harness=harness, probe=probe)

    Advance(ctx).run()  # launch
    elicitation = store.in_flight_elicitation("lease_1", 1)
    assert elicitation is not None and elicitation.pid is not None
    probe.alive = {(elicitation.pid, elicitation.process_start_time)}

    lease = store.active_lease("lease_1")
    assert lease is not None
    Attempt(ctx, lease).park_paused(via="test")

    assert elicitation.pid in probe.killed
    assert store.in_flight_elicitation("lease_1", 1) is None

    # The later unpause re-mints the lease token — no in-flight record left to race it.
    probe.alive = set()
    parked_lease = store.active_lease("lease_1")
    assert parked_lease is not None
    DormantSession(ctx, parked_lease).on_unpause()
    assert store.active_lease("lease_1") is not None


def test_preempt_kills_the_in_flight_elicitation(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _seed_running_lease(store)
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    probe = FakeProbe()
    ctx = _ctx(store, harness=harness, probe=probe)

    Advance(ctx).run()  # launch
    elicitation = store.in_flight_elicitation("lease_1", 1)
    assert elicitation is not None and elicitation.pid is not None
    probe.alive = {(elicitation.pid, elicitation.process_start_time)}

    lease = store.active_lease("lease_1")
    assert lease is not None
    Attempt(ctx, lease).preempt(via="test")

    assert elicitation.pid in probe.killed
    assert store.in_flight_elicitation("lease_1", 1) is None
