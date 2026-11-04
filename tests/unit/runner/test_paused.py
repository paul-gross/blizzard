"""The runner adheres to the hub's pause brake — loop component tier.

The declarative pause brake lives at the hub; the runner reads it on PULL, mirrors it
to its store, and FILL adheres: paused = no new claims, in-flight chunks run on. When
the hub is unreachable the runner keeps its last-mirrored directive. Driven against a
real (tmp sqlite) runner store with fakes standing in only at the seams."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import (
    RUNNER_LOCALLY_PAUSED,
)
from blizzard.runner.hub.client import (
    QueueEntry,
)
from blizzard.runner.loop.steps import Fill, SpendCeiling
from blizzard.runner.loop.tick import tick
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    claimed_outcome,
    make_context,
    make_envelope,
)
from tests.support_paused import (
    _CHOICES,  # pyright: ignore[reportPrivateUsage]
    _HANDLE,  # pyright: ignore[reportPrivateUsage]
    _NOW,  # pyright: ignore[reportPrivateUsage]
    _ceiling_config,  # pyright: ignore[reportPrivateUsage]
    _pause_locally,  # pyright: ignore[reportPrivateUsage]
    _record_usage,  # pyright: ignore[reportPrivateUsage]
    _running_chunk,  # pyright: ignore[reportPrivateUsage]
    _seed_running_lease,  # pyright: ignore[reportPrivateUsage]
    _store,  # pyright: ignore[reportPrivateUsage]
)


def test_ceiling_crossing_engages_the_local_brake_and_logs_ceiling_and_spend(tmp_path: Path):
    """Crossing `runner_ceiling_usd` engages the SAME local pause brake `blizzard runner
    pause` sets, and the log line names both the configured ceiling and the spend that
    tripped it (the escalation the plan calls for)."""
    store = _store(tmp_path)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=_ceiling_config(5.0),
    )

    assert store.local_paused() is False
    with capture_logs() as logs:
        SpendCeiling(ctx).run()

    assert store.local_paused() is True
    reports = [f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED]
    assert len(reports) == 1
    payload = json.loads(reports[0].payload)
    assert payload["by"] == "runner-ceiling"
    assert "5.00" in payload["reason"] and "7.00" in payload["reason"]
    warnings = [e for e in logs if "runner locally paused" in e["event"]]
    assert len(warnings) == 1
    assert warnings[0]["ceiling_usd"] == 5.0
    assert warnings[0]["spend_usd"] == 7.0


def test_ceiling_absent_never_engages_regardless_of_spend(tmp_path: Path):
    """`runner_ceiling_usd` unset (today's default) never engages the brake, however much
    this runner has spent — mirroring the per-chunk cap's identical absent-means-off rule."""
    store = _store(tmp_path)
    _record_usage(store, cost=9999.0, recorded_at=_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    SpendCeiling(ctx).run()

    assert store.local_paused() is False
    assert [f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED] == []


def test_ceiling_under_cap_does_not_engage(tmp_path: Path):
    store = _store(tmp_path)
    _record_usage(store, cost=1.0, recorded_at=_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=_ceiling_config(5.0),
    )

    SpendCeiling(ctx).run()

    assert store.local_paused() is False


def test_ceiling_partial_total_trips_the_lower_bound_and_flags_partial(tmp_path: Path):
    """A cost-absent row contributes tokens but $0 to the cost sum; the ceiling trips
    on that lower bound, and both the log line and report mark the figure ``+``."""
    store = _store(tmp_path)
    _record_usage(store, cost=None, recorded_at=_NOW)  # $0 lower bound, cost_partial=True
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=_ceiling_config(0.0),  # any spend at all trips it
    )

    with capture_logs() as logs:
        SpendCeiling(ctx).run()

    assert store.local_paused() is True
    warnings = [e for e in logs if "runner locally paused" in e["event"]]
    assert warnings[0]["cost_partial"] is True
    assert "(spend $0.00+)" in warnings[0]["event"]
    reports = [f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED]
    assert json.loads(reports[0].payload)["reason"].endswith("(spend $0.00+)")


def test_ceiling_engages_once_no_thrash_on_later_ticks(tmp_path: Path):
    """Once engaged, a later tick's check must neither re-engage nor re-log — engaging is a
    one-time transition, not a per-tick assertion, even while the window's sum stays over
    the ceiling for as long as it holds (the engage-once requirement)."""
    store = _store(tmp_path)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=_ceiling_config(5.0),
    )

    SpendCeiling(ctx).run()
    assert store.local_paused() is True
    assert len([f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED]) == 1

    with capture_logs() as logs:
        SpendCeiling(ctx).run()  # a second, and later a third, tick's check
        SpendCeiling(ctx).run()

    assert [e for e in logs if "runner locally paused" in e["event"]] == []  # not re-logged
    # Still exactly one pause report ever buffered — no re-engage fact either.
    assert len([f for f in store.pending_outbound() if f.kind == RUNNER_LOCALLY_PAUSED]) == 1


def test_ceiling_does_not_auto_lift_when_the_window_rolls_the_spend_back_under_cap(tmp_path: Path):
    """Once engaged, the brake stays engaged even after the rolling window later excludes
    the very usage fact that tripped it (the sum genuinely drops back under the cap) —
    `blizzard runner start` is the ONLY conscious clear (the locked design)."""
    store = _store(tmp_path)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    clock = FixedClock(_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=clock,
        config=_ceiling_config(5.0, window_hours=1.0),
    )

    SpendCeiling(ctx).run()
    assert store.local_paused() is True

    # Move the clock two hours on — the 1h window now excludes the tripping fact entirely,
    # so a fresh, unpaused check of the same config would find $0 spend, well under cap.
    clock.advance(timedelta(hours=2))
    assert store.usage_since(clock.now() - timedelta(hours=1)).cost_usd == 0.0  # confirms the rollover

    SpendCeiling(ctx).run()

    assert store.local_paused() is True  # still engaged — nothing lifts it automatically


def test_ceiling_engaged_defers_reap_kill_and_suppresses_fill_in_the_same_tick(tmp_path: Path):
    """Driven as a full tick: a live worker whose runner just crossed its ceiling is left
    running untouched, and a second, otherwise-claimable chunk is not spawned into either."""
    store = _store(tmp_path)
    _seed_running_lease(store)  # lease_1 / ch_1, a live worker (pid 100)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    hub = FakeHub()
    hub.paused = False
    hub.chunks["ch_1"] = _running_chunk()
    hub.queue = [QueueEntry(chunk_id="ch_2", graph_id="gr_1", position=0)]
    ch_2_env = make_envelope("ch_2", "build", node_id="nd_build", choices=_CHOICES)
    hub.claim_outcome = claimed_outcome("ch_2", ch_2_env)
    hub.envelopes["ch_2"] = ch_2_env
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    probe = FakeProbe(alive={(100, "start-100")})  # the ch_1 worker is still running
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=harness,
        probe=probe,
        clock=FixedClock(_NOW),
        config=_ceiling_config(5.0, agents=2),
    )

    tick(ctx)

    assert store.local_paused() is True  # the ceiling engaged this very tick
    assert probe.killed == []  # not a drain — the live worker was left alone
    assert harness.spawns == []  # and nothing new was spawned into the free second env either
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry consumed
    assert hub.claims == []
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.pid == 100  # untouched


def test_runner_start_clears_the_ceiling_brake_exactly_like_a_manual_pause(tmp_path: Path):
    """`blizzard runner start` clears a ceiling-engaged brake exactly as it clears an
    operator's own pause, with no ceiling-specific code path."""
    store = _store(tmp_path)
    _record_usage(store, cost=7.0, recorded_at=_NOW)
    clock = FixedClock(_NOW)
    hub = FakeHub()
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.claim_outcome = claimed_outcome("ch_1", env)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=clock,
        config=_ceiling_config(5.0, window_hours=1.0),
    )

    SpendCeiling(ctx).run()
    assert store.local_paused() is True
    Fill(ctx).run()
    assert hub.claims == []  # suppressed while engaged

    # `blizzard runner start` — the exact `record_local_pause(paused=False)` write
    # `PATCH /api/runner` makes, with no ceiling-aware code anywhere in that path.
    _pause_locally(store, ctx, paused=False)
    assert store.local_paused() is False

    # The window has since moved past the tripping fact, so a fresh ceiling check does not
    # immediately re-engage the brake out from under the operator's own clear.
    clock.advance(timedelta(hours=2))
    SpendCeiling(ctx).run()
    assert store.local_paused() is False

    Fill(ctx).run()

    assert store.local_paused() is False
    assert len(hub.claims) == 1  # FILL claims again — work resumed
