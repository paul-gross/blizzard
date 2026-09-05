"""``ExternalUsageSample`` — the tick's last step (issue #218, blizzard#436).

Unit and component tiers against a real (tmp sqlite) store and a scriptable
``FakeSubscriptionSampler``: the per-slug cadence gate, that a sample never perturbs other
steps, and several declared subscriptions sampled independently — a failed one isolated to
its own slug, an unbound provider staying declared and unsampled."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ExternalSubscriptionUsageWindow,
)
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.steps import ExternalUsageSample
from blizzard.runner.loop.tick import tick
from blizzard.wire.queue import QueuePeekEntry
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeSubscriptionSampler,
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
_SLUG = "anthropic"


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _declaration(
    slug: str = _SLUG, *, interval_seconds: int = 300, provider: str = "anthropic"
) -> SubscriptionDeclaration:
    return SubscriptionDeclaration(
        slug=slug, name=slug.title(), provider=provider, sample_interval_seconds=interval_seconds
    )


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


def _ctx(store, *, sampler: FakeSubscriptionSampler, clock: FixedClock, interval_seconds: int = 300):  # type: ignore[no-untyped-def]
    return make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=clock,
        config=LoopConfig(
            runner_id="r1",
            workspace_id="ws1",
            max_agents=1,
            subscriptions=(_declaration(interval_seconds=interval_seconds),),
        ),
        subscription_samplers={_SLUG: sampler},
    )


def _ctx_with_a_claimable_chunk(
    store, *, sampler: FakeSubscriptionSampler, clock: FixedClock, interval_seconds: int = 300
):  # type: ignore[no-untyped-def]
    hub = FakeHub()
    env = make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES)
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    # The spawned worker's (pid, start_time) reads as alive to the probe, so a full
    # `tick()` call's own ADVANCE never treats this same-tick spawn as an exited worker.
    probe = FakeProbe(alive={(_HANDLE.pid, _HANDLE.process_start_time)})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=probe,
        clock=clock,
        config=LoopConfig(
            runner_id="r1",
            workspace_id="ws1",
            max_agents=1,
            subscriptions=(_declaration(interval_seconds=interval_seconds),),
        ),
        subscription_samplers={_SLUG: sampler},
    )
    return ctx, hub


# AC 1 — cadence gate.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_first_ever_attempt_samples_immediately(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    assert store.last_external_usage_attempt_at(_SLUG) is None
    ExternalUsageSample(ctx).run()

    assert sampler.sample_calls == 1
    assert store.last_external_usage_attempt_at(_SLUG) == _NOW


@pytest.mark.unit
def test_within_the_interval_the_adapter_is_not_called(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=300)

    ExternalUsageSample(ctx).run()
    assert sampler.sample_calls == 1

    clock.advance(timedelta(seconds=299))
    ExternalUsageSample(ctx).run()
    assert sampler.sample_calls == 1  # still gated — under the interval


@pytest.mark.unit
def test_at_exactly_the_interval_the_adapter_is_called(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=300)

    ExternalUsageSample(ctx).run()
    assert sampler.sample_calls == 1

    clock.advance(timedelta(seconds=300))
    ExternalUsageSample(ctx).run()
    assert sampler.sample_calls == 2  # exactly at the interval — samples


@pytest.mark.unit
def test_past_the_interval_the_adapter_is_called(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=300)

    ExternalUsageSample(ctx).run()
    clock.advance(timedelta(seconds=301))
    ExternalUsageSample(ctx).run()

    assert sampler.sample_calls == 2


# AC 2 — a successful sample writes one attempt row and enqueues one outbound entry.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_a_successful_sample_records_one_attempt_and_enqueues_one_runner_scoped_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    snapshot = _snapshot()
    sampler = FakeSubscriptionSampler(snapshot=snapshot)
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ExternalUsageSample(ctx).run()

    assert store.last_external_usage_attempt_at(_SLUG) == _NOW

    pending = [f for f in store.pending_outbound() if f.kind == _SAMPLED_KIND]
    assert len(pending) == 1
    fact = pending[0]
    # Runner-scoped: no chunk_id/lease_id, mirroring `record_local_pause`'s own report.
    assert fact.chunk_id is None
    assert fact.lease_id is None

    payload = json.loads(fact.payload)
    assert payload["slug"] == _SLUG
    assert payload["name"] == _SLUG.title()
    assert payload["sampled_at"] == "2026-08-01T12:00:00+00:00"
    assert {w["window"] for w in payload["windows"]} == {"5h", "7d"}
    five_hour = next(w for w in payload["windows"] if w["window"] == "5h")
    assert five_hour["utilization_pct"] == 42.0
    assert five_hour["window_seconds"] == 18_000
    assert "resets_at" in five_hour


# AC 3 — a None sample writes a NULL-payload attempt row and enqueues nothing.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_no_sample_records_a_null_payload_attempt_and_enqueues_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=None)
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=300)

    ExternalUsageSample(ctx).run()

    assert store.last_external_usage_attempt_at(_SLUG) == _NOW
    assert [f for f in store.pending_outbound() if f.kind == _SAMPLED_KIND] == []

    # The next tick, still within the interval, must not re-sample — the NULL-payload
    # attempt still counts as "tried" for cadence purposes.
    clock.advance(timedelta(seconds=100))
    ExternalUsageSample(ctx).run()
    assert sampler.sample_calls == 1
    assert store.last_external_usage_attempt_at(_SLUG) == _NOW  # unchanged — no new attempt


# AC 4 — a raising adapter leaves the tick completing normally, other steps intact.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_a_raising_adapter_still_lets_the_tick_complete_and_fill_spawn(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(raises=RuntimeError("boom"))
    clock = FixedClock(_NOW)
    ctx, hub = _ctx_with_a_claimable_chunk(store, sampler=sampler, clock=clock)

    with capture_logs() as logs:
        tick(ctx)  # must not raise

    # FILL's own normal effect (claim + lease mint) still happened this same tick.
    assert len(hub.claims) == 1
    assert len(store.list_active_leases()) == 1
    # The sampler was reached (last step) and its failure was swallowed, logged.
    assert sampler.sample_calls == 1
    warnings = [e for e in logs if "external subscription usage sample step failed" in e["event"]]
    assert len(warnings) == 1
    # No attempt row survives a raise inside the try (the store write never ran).
    assert store.last_external_usage_attempt_at(_SLUG) is None


@pytest.mark.unit
def test_a_raising_step_called_directly_returns_without_raising(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(raises=ValueError("nope"))
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ExternalUsageSample(ctx).run()  # must not raise

    assert store.last_external_usage_attempt_at(_SLUG) is None


# AC 5 — component: several ticks across a virtual clock match the adapter-call count.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_several_ticks_sample_at_the_expected_cadence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx, _hub = _ctx_with_a_claimable_chunk(store, sampler=sampler, clock=clock, interval_seconds=300)

    # Ticks at t=0, 100, 200, 300, 400, 500, 600 (7 ticks, 100s apart): the gate fires
    # on t=0, t=300, t=600 — 3 calls.
    for _ in range(7):
        tick(ctx)
        clock.advance(timedelta(seconds=100))

    assert sampler.sample_calls == 3


@pytest.mark.unit
def test_a_very_large_interval_never_resamples_and_every_other_step_behaves_identically(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """With the sampler effectively disabled (an interval far beyond the run), FILL still
    claims and spawns exactly as it does with the sampler enabled — the step is inert to
    every other step's own decisions."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    clock_a = FixedClock(_NOW)
    store_a = _store(tmp_path / "a")
    sampler_a = FakeSubscriptionSampler(snapshot=_snapshot())
    ctx_a, hub_a = _ctx_with_a_claimable_chunk(store_a, sampler=sampler_a, clock=clock_a, interval_seconds=300)

    clock_b = FixedClock(_NOW)
    store_b = _store(tmp_path / "b")
    sampler_b = FakeSubscriptionSampler(snapshot=_snapshot())
    ctx_b, hub_b = _ctx_with_a_claimable_chunk(
        store_b, sampler=sampler_b, clock=clock_b, interval_seconds=1_000_000_000
    )

    for _ in range(5):
        tick(ctx_a)
        tick(ctx_b)
        clock_a.advance(timedelta(seconds=100))
        clock_b.advance(timedelta(seconds=100))

    assert len(hub_a.claims) == len(hub_b.claims) == 1
    assert len(store_a.list_active_leases()) == len(store_b.list_active_leases()) == 1
    assert sampler_a.sample_calls >= 1
    assert sampler_b.sample_calls == 1  # the very first, never-attempted-before sample only


# AC 6 (blizzard#436 phase 2) — several declared subscriptions, sampled independently.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_two_declarations_with_different_cadences_only_the_due_one_samples(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A short-interval and a long-interval declaration both sample on the first ever
    tick (neither has an anchor yet), but only the short one is due on the second."""
    store = _store(tmp_path)
    fast = FakeSubscriptionSampler(snapshot=_snapshot())
    slow = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=clock,
        config=LoopConfig(
            runner_id="r1",
            workspace_id="ws1",
            max_agents=1,
            subscriptions=(
                _declaration("fast", interval_seconds=100),
                _declaration("slow", interval_seconds=10_000),
            ),
        ),
        subscription_samplers={"fast": fast, "slow": slow},
    )

    ExternalUsageSample(ctx).run()
    assert fast.sample_calls == 1
    assert slow.sample_calls == 1

    clock.advance(timedelta(seconds=200))
    ExternalUsageSample(ctx).run()

    assert fast.sample_calls == 2  # due again — 200s > its 100s interval
    assert slow.sample_calls == 1  # not due — 200s < its 10_000s interval


@pytest.mark.unit
def test_a_failed_sample_advances_only_its_own_slugs_cadence_and_leaves_the_other_readable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A slug whose sample this tick failed (``None``) still gets a NULL-payload attempt
    row, advancing only ITS anchor — the other slug's last successful sample and cadence
    anchor are untouched."""
    store = _store(tmp_path)
    ok = FakeSubscriptionSampler(snapshot=_snapshot(sampled_at=_NOW))
    failing = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=clock,
        config=LoopConfig(
            runner_id="r1",
            workspace_id="ws1",
            max_agents=1,
            subscriptions=(
                _declaration("good", interval_seconds=100),
                _declaration("bad", interval_seconds=100),
            ),
        ),
        subscription_samplers={"good": ok, "bad": failing},
    )

    ExternalUsageSample(ctx).run()  # both sample cleanly the first time
    assert store.last_external_usage_attempt_at("good") == _NOW
    assert store.last_external_usage_attempt_at("bad") == _NOW
    good_reports_after_first = [f for f in store.pending_outbound() if f.kind == _SAMPLED_KIND]
    assert len(good_reports_after_first) == 2

    clock.advance(timedelta(seconds=200))
    failing.snapshot = None  # "bad" now fails this tick; "good" still produces a snapshot
    ExternalUsageSample(ctx).run()

    later = _NOW + timedelta(seconds=200)
    # "bad"'s cadence advanced (a NULL-payload attempt was still recorded for it)...
    assert store.last_external_usage_attempt_at("bad") == later
    # ...but "good"'s own anchor and its last successful report are untouched by "bad"'s
    # failure — a fresh, successful report from "good" this same tick.
    assert store.last_external_usage_attempt_at("good") == later
    reports_after_second = [f for f in store.pending_outbound() if f.kind == _SAMPLED_KIND]
    # Only "good" enqueued a NEW report this tick (2 total from the first tick, +1 now).
    assert len(reports_after_second) == 3
    newest = json.loads(reports_after_second[-1].payload)
    assert newest["slug"] == "good"


@pytest.mark.unit
def test_a_declared_provider_with_no_sampler_stays_declared_and_unsampled(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A slug whose provider names no known sampler binding is simply absent from
    ``subscription_samplers`` (blizzard#436) — no crash, no attempt row for it, and the
    other declared subscription is sampled normally in the same tick."""
    store = _store(tmp_path)
    known = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=clock,
        config=LoopConfig(
            runner_id="r1",
            workspace_id="ws1",
            max_agents=1,
            subscriptions=(
                _declaration("known", interval_seconds=100),
                _declaration("no-binding", interval_seconds=100, provider="some-unbound-provider"),
            ),
        ),
        subscription_samplers={"known": known},  # "no-binding" has no entry — no binding
    )

    ExternalUsageSample(ctx).run()  # must not raise

    assert known.sample_calls == 1
    assert store.last_external_usage_attempt_at("known") == _NOW
    assert store.last_external_usage_attempt_at("no-binding") is None  # never attempted


# Parse-contract stability — `slug` is additive JSON (blizzard#436 phase 2).
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_the_payload_is_still_parseable_by_a_reader_ignorant_of_slug(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The wire fact gained ``slug`` additively — a reader written against the pre-slug
    shape, which only ever projected ``sampled_at`` and ``windows``, must still parse
    everything it always did, unaware the field was ever added."""
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ExternalUsageSample(ctx).run()

    fact = next(f for f in store.pending_outbound() if f.kind == _SAMPLED_KIND)
    payload = json.loads(fact.payload)

    def _read_pre_slug_shape(raw: dict[str, object]) -> tuple[str, list[dict[str, object]]]:
        # Exactly what a reader written before `slug` existed would project — no `slug` key.
        return raw["sampled_at"], raw["windows"]  # type: ignore[return-value]

    sampled_at, windows = _read_pre_slug_shape(payload)
    assert sampled_at == "2026-08-01T12:00:00+00:00"
    assert {w["window"] for w in windows} == {"5h", "7d"}
