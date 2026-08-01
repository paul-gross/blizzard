"""``sample_external_subscription_usage`` — the tick's last step (issue #218, phase 2).

Unit-drives the step in isolation against a real (tmp sqlite) runner store and a
scriptable :class:`FakeHarness`, then a component tier that runs several full ``tick()``
passes across an advancing :class:`FixedClock` to check the cadence gate holds across a
realistic pass sequence and that the sampler never perturbs the other steps' own
behavior (``bzh:steppable-loop``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.external_usage import ExternalSubscriptionUsageSnapshot, ExternalSubscriptionUsageWindow
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import sample_external_subscription_usage
from blizzard.runner.loop.tick import tick
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

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_SAMPLED_KIND = "external_subscription_usage.sampled"


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _snapshot(*, sampled_at: datetime = _NOW) -> ExternalSubscriptionUsageSnapshot:
    return ExternalSubscriptionUsageSnapshot(
        sampled_at=sampled_at,
        windows=(
            ExternalSubscriptionUsageWindow(
                window="5h", utilization_pct=42.0, resets_at=sampled_at + timedelta(hours=5), window_seconds=18_000
            ),
            ExternalSubscriptionUsageWindow(
                window="7d",
                utilization_pct=8.25,
                resets_at=sampled_at + timedelta(days=7),
                window_seconds=604_800,
            ),
        ),
    )


def _ctx(store, *, harness: FakeHarness, clock: FixedClock, interval_seconds: int = 300):  # type: ignore[no-untyped-def]
    return make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=harness,
        probe=FakeProbe(),
        clock=clock,
        config=LoopConfig(
            runner_id="r1", workspace_id="ws1", max_agents=1, external_usage_sample_interval_seconds=interval_seconds
        ),
    )


def _ctx_with_a_claimable_chunk(store, *, harness: FakeHarness, clock: FixedClock, interval_seconds: int = 300):  # type: ignore[no-untyped-def]
    hub = FakeHub()
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    # The spawned worker's (pid, start_time) reads as alive to the probe, so a full
    # `tick()` call's own ADVANCE never treats this same-tick spawn as an exited
    # worker to judge (which would need `hub.envelopes` wired too, a distraction from
    # what this module actually tests: FILL's own claim+spawn effect surviving a
    # raising external-usage sampler).
    probe = FakeProbe(alive={(_HANDLE.pid, _HANDLE.process_start_time)})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=probe,
        clock=clock,
        config=LoopConfig(
            runner_id="r1", workspace_id="ws1", max_agents=1, external_usage_sample_interval_seconds=interval_seconds
        ),
    )
    return ctx, hub


# --------------------------------------------------------------------------- #
# AC 1 — cadence gate.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_first_ever_attempt_samples_immediately(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_snapshot=_snapshot())
    ctx = _ctx(store, harness=harness, clock=FixedClock(_NOW))

    assert store.last_external_usage_attempt_at() is None
    sample_external_subscription_usage(ctx)

    assert harness.external_usage_calls == 1
    assert store.last_external_usage_attempt_at() == _NOW


@pytest.mark.unit
def test_within_the_interval_the_adapter_is_not_called(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, harness=harness, clock=clock, interval_seconds=300)

    sample_external_subscription_usage(ctx)
    assert harness.external_usage_calls == 1

    clock.advance(timedelta(seconds=299))
    sample_external_subscription_usage(ctx)
    assert harness.external_usage_calls == 1  # still gated — under the interval


@pytest.mark.unit
def test_at_exactly_the_interval_the_adapter_is_called(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, harness=harness, clock=clock, interval_seconds=300)

    sample_external_subscription_usage(ctx)
    assert harness.external_usage_calls == 1

    clock.advance(timedelta(seconds=300))
    sample_external_subscription_usage(ctx)
    assert harness.external_usage_calls == 2  # exactly at the interval — samples


@pytest.mark.unit
def test_past_the_interval_the_adapter_is_called(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, harness=harness, clock=clock, interval_seconds=300)

    sample_external_subscription_usage(ctx)
    clock.advance(timedelta(seconds=301))
    sample_external_subscription_usage(ctx)

    assert harness.external_usage_calls == 2


# --------------------------------------------------------------------------- #
# AC 2 — a successful sample writes one attempt row and enqueues exactly one
# runner-scoped outbound entry.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_a_successful_sample_records_one_attempt_and_enqueues_one_runner_scoped_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    snapshot = _snapshot()
    harness = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_snapshot=snapshot)
    ctx = _ctx(store, harness=harness, clock=FixedClock(_NOW))

    sample_external_subscription_usage(ctx)

    assert store.last_external_usage_attempt_at() == _NOW

    pending = [f for f in store.pending_outbound() if f.kind == _SAMPLED_KIND]
    assert len(pending) == 1
    fact = pending[0]
    # Runner-scoped: no chunk_id/lease_id, mirroring `record_local_pause`'s own report.
    assert fact.chunk_id is None
    assert fact.lease_id is None

    payload = json.loads(fact.payload)
    assert payload["sampled_at"] == "2026-08-01T12:00:00+00:00"
    assert {w["window"] for w in payload["windows"]} == {"5h", "7d"}
    five_hour = next(w for w in payload["windows"] if w["window"] == "5h")
    assert five_hour["utilization_pct"] == 42.0
    assert five_hour["window_seconds"] == 18_000
    assert "resets_at" in five_hour


# --------------------------------------------------------------------------- #
# AC 3 — a None sample writes a NULL-payload attempt row, enqueues nothing, and the
# next (still-within-interval) tick does not re-sample.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_no_sample_records_a_null_payload_attempt_and_enqueues_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_snapshot=None)
    clock = FixedClock(_NOW)
    ctx = _ctx(store, harness=harness, clock=clock, interval_seconds=300)

    sample_external_subscription_usage(ctx)

    assert store.last_external_usage_attempt_at() == _NOW
    assert [f for f in store.pending_outbound() if f.kind == _SAMPLED_KIND] == []

    # The next tick, still within the interval, must not re-sample — the NULL-payload
    # attempt still counts as "tried" for cadence purposes.
    clock.advance(timedelta(seconds=100))
    sample_external_subscription_usage(ctx)
    assert harness.external_usage_calls == 1
    assert store.last_external_usage_attempt_at() == _NOW  # unchanged — no new attempt


# --------------------------------------------------------------------------- #
# AC 4 — a raising adapter leaves the tick completing normally, other steps intact.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_a_raising_adapter_still_lets_the_tick_complete_and_fill_spawn(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_raises=RuntimeError("boom"))
    clock = FixedClock(_NOW)
    ctx, hub = _ctx_with_a_claimable_chunk(store, harness=harness, clock=clock)

    with capture_logs() as logs:
        tick(ctx)  # must not raise

    # FILL's own normal effect (claim + lease mint) still happened this same tick.
    assert len(hub.claims) == 1
    assert len(store.list_active_leases()) == 1
    # The sampler was reached (last step) and its failure was swallowed, logged.
    assert harness.external_usage_calls == 1
    warnings = [e for e in logs if "external subscription usage sample step failed" in e["event"]]
    assert len(warnings) == 1
    # No attempt row survives a raise inside the try (the store write never ran).
    assert store.last_external_usage_attempt_at() is None


@pytest.mark.unit
def test_a_raising_step_called_directly_returns_without_raising(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_raises=ValueError("nope"))
    ctx = _ctx(store, harness=harness, clock=FixedClock(_NOW))

    sample_external_subscription_usage(ctx)  # must not raise

    assert store.last_external_usage_attempt_at() is None


# --------------------------------------------------------------------------- #
# AC 5 — component: several ticks across a virtual clock; adapter-call count matches
# expectation, and disabling the sampler (a very large interval) changes nothing about
# the other steps' behavior.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_several_ticks_sample_at_the_expected_cadence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    harness = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx, _hub = _ctx_with_a_claimable_chunk(store, harness=harness, clock=clock, interval_seconds=300)

    # Ticks at t=0, 100, 200, 300, 400, 500, 600 (7 ticks, 100s apart): the gate fires
    # on t=0, t=300, t=600 — 3 calls.
    for _ in range(7):
        tick(ctx)
        clock.advance(timedelta(seconds=100))

    assert harness.external_usage_calls == 3


@pytest.mark.unit
def test_a_very_large_interval_never_resamples_and_every_other_step_behaves_identically(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """With the sampler effectively disabled (an interval far beyond the run), FILL still
    claims and spawns exactly as it does with the sampler enabled — the step is inert to
    every other step's own decisions."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    clock_a = FixedClock(_NOW)
    store_a = _store(tmp_path / "a")
    harness_a = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_snapshot=_snapshot())
    ctx_a, hub_a = _ctx_with_a_claimable_chunk(store_a, harness=harness_a, clock=clock_a, interval_seconds=300)

    clock_b = FixedClock(_NOW)
    store_b = _store(tmp_path / "b")
    harness_b = FakeHarness(handle=_HANDLE, verdict="pass", external_usage_snapshot=_snapshot())
    ctx_b, hub_b = _ctx_with_a_claimable_chunk(
        store_b, harness=harness_b, clock=clock_b, interval_seconds=1_000_000_000
    )

    for _ in range(5):
        tick(ctx_a)
        tick(ctx_b)
        clock_a.advance(timedelta(seconds=100))
        clock_b.advance(timedelta(seconds=100))

    assert len(hub_a.claims) == len(hub_b.claims) == 1
    assert len(store_a.list_active_leases()) == len(store_b.list_active_leases()) == 1
    assert harness_a.external_usage_calls >= 1
    assert harness_b.external_usage_calls == 1  # the very first, never-attempted-before sample only
