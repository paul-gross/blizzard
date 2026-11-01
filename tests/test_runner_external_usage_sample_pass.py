"""``ExternalUsageSamplePass`` — the host tick's sampling pass.

Unit and component tiers against a real (tmp sqlite) store and a scriptable
``FakeSubscriptionSampler``: the per-slug cadence gate, the report each attempt buffers, and several declared
subscriptions sampled independently — a failed one isolated to its own slug, an unbound provider staying declared and
unsampled."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.subscriptions.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ExternalSubscriptionUsageWindow,
    SampleMissReason,
)
from blizzard.runner.throttle.resample import FloorResampleTriggers
from blizzard.runner.usage.external_usage_sample import ExternalUsageSamplePass, SampledSubscription
from tests.runner_fakes import (
    FakeSubscriptionSampler,
    SqlAlchemyRunnerStore,
    make_host_stores,
    make_store,
    usage_limit_mark,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
_SAMPLED_KIND = "external_subscription_usage.sampled"
_MISSED_KIND = "external_subscription_usage.missed"
_SLUG = "anthropic"


class _StoreSink:
    """Records through the store's own runner-lane write, as an unregistered host's first runner does."""

    def __init__(self, store: SqlAlchemyRunnerStore) -> None:
        self._store = store

    def record(
        self,
        *,
        slug: str,
        sampled_at: datetime,
        payload: str | None,
        report_kind: str,
        report_payload: str,
        miss_reason: str | None,
    ) -> None:
        self._store.record_external_usage_attempt(
            slug=slug,
            sampled_at=sampled_at,
            payload=payload,
            report_kind=report_kind,
            report_payload=report_payload,
            miss_reason=miss_reason,
        )


def _store(tmp_path: Path):
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _declared(
    slug: str = _SLUG, *, interval_seconds: int = 300, sampler: FakeSubscriptionSampler | None = None
) -> SampledSubscription:
    return SampledSubscription(slug=slug, name=slug.title(), sample_interval_seconds=interval_seconds, sampler=sampler)


def _pass(
    store: SqlAlchemyRunnerStore, clock: FixedClock, *subscriptions: SampledSubscription
) -> ExternalUsageSamplePass:
    host = make_host_stores(store, clock)
    return ExternalUsageSamplePass(
        subscriptions=subscriptions,
        attempts=store,
        early=FloorResampleTriggers(host.usage_limits, host.external_usage),
        sink=_StoreSink(store),
        clock=clock,
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


def _ctx(
    store: SqlAlchemyRunnerStore, *, sampler: FakeSubscriptionSampler, clock: FixedClock, interval_seconds: int = 300
):
    return _pass(store, clock, _declared(interval_seconds=interval_seconds, sampler=sampler))


# AC 1 — cadence gate.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_first_ever_attempt_samples_immediately(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    assert store.last_external_usage_attempt_at(_SLUG) is None
    ctx.run()

    assert sampler.sample_calls == 1
    assert store.last_external_usage_attempt_at(_SLUG) == _NOW


@pytest.mark.unit
def test_within_the_interval_the_adapter_is_not_called(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=300)

    ctx.run()
    assert sampler.sample_calls == 1

    clock.advance(timedelta(seconds=299))
    ctx.run()
    assert sampler.sample_calls == 1  # still gated — under the interval


@pytest.mark.unit
def test_at_exactly_the_interval_the_adapter_is_called(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=300)

    ctx.run()
    assert sampler.sample_calls == 1

    clock.advance(timedelta(seconds=300))
    ctx.run()
    assert sampler.sample_calls == 2  # exactly at the interval — samples


@pytest.mark.unit
def test_past_the_interval_the_adapter_is_called(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=300)

    ctx.run()
    clock.advance(timedelta(seconds=301))
    ctx.run()

    assert sampler.sample_calls == 2


# AC 2 — a successful sample writes one attempt row and enqueues one outbound entry.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_a_successful_sample_records_one_attempt_and_enqueues_one_runner_scoped_report(tmp_path: Path) -> None:
    store = _store(tmp_path)
    snapshot = _snapshot()
    sampler = FakeSubscriptionSampler(snapshot=snapshot)
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ctx.run()

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


@pytest.mark.parametrize(
    "invalid_pct",
    [float("nan"), float("inf"), float("-inf"), -0.1, 100.1],
    ids=["nan", "inf", "-inf", "below", "above"],
)
def test_an_invalid_utilization_window_is_dropped_before_it_is_persisted_or_buffered(
    tmp_path: Path, invalid_pct: float
) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(
        snapshot=ExternalSubscriptionUsageSnapshot(
            sampled_at=_NOW,
            windows=(
                ExternalSubscriptionUsageWindow(
                    window="5h", utilization_pct=42.0, resets_at=_NOW + timedelta(hours=5), window_seconds=18_000
                ),
                ExternalSubscriptionUsageWindow(
                    window="7d", utilization_pct=invalid_pct, resets_at=_NOW + timedelta(days=7), window_seconds=604_800
                ),
                ExternalSubscriptionUsageWindow(window="zero", utilization_pct=10.0, resets_at=_NOW, window_seconds=0),
            ),
        )
    )
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ctx.run()

    payload = json.loads(next(f.payload for f in store.pending_outbound() if f.kind == _SAMPLED_KIND))
    assert payload["windows"] == [
        {
            "window": "5h",
            "utilization_pct": 42.0,
            "resets_at": "2026-08-01T17:00:00+00:00",
            "window_seconds": 18_000,
        }
    ]
    json.dumps(payload, allow_nan=False)


def test_an_empty_sample_is_persisted_and_buffered_as_an_empty_windows_collection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=ExternalSubscriptionUsageSnapshot(sampled_at=_NOW, windows=()))
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ctx.run()

    payload = json.loads(next(f.payload for f in store.pending_outbound() if f.kind == _SAMPLED_KIND))
    assert payload["windows"] == []


def test_a_no_limit_window_reaches_the_levers_sample_and_never_the_hubs_report(tmp_path: Path) -> None:
    store = _store(tmp_path)
    week = _snapshot().windows[1]
    sampler = FakeSubscriptionSampler(
        snapshot=ExternalSubscriptionUsageSnapshot(sampled_at=_NOW, windows=(week,), unlimited=("5h",))
    )
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ctx.run()

    sample = store.latest_external_usage_samples_by_slug((_SLUG,))[_SLUG]
    assert sample.windows == (week,)
    assert sample.unlimited == ("5h",)
    at_now = store.external_usage_sample_at(_SLUG, _NOW)
    assert at_now is not None
    assert at_now.unlimited == ("5h",)
    report = json.loads(next(f.payload for f in store.pending_outbound() if f.kind == _SAMPLED_KIND))
    assert set(report) == {"slug", "name", "sampled_at", "windows"}


# AC 3 — a miss writes a NULL-payload attempt row and enqueues a `missed` report, never a `sampled` one.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_no_sample_records_a_null_payload_attempt_and_enqueues_no_sampled_report(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(miss_reason=SampleMissReason.CREDENTIAL_LAPSED)
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=300)

    ctx.run()

    assert store.last_external_usage_attempt_at(_SLUG) == _NOW
    assert [f for f in store.pending_outbound() if f.kind == _SAMPLED_KIND] == []

    attempt = store.latest_external_usage_attempt(_SLUG)
    assert attempt is not None
    assert attempt.ok is False
    assert attempt.miss_reason == "credential_lapsed"

    # The next tick, still within the interval, must not re-sample — the NULL-payload
    # attempt still counts as "tried" for cadence purposes.
    clock.advance(timedelta(seconds=100))
    ctx.run()
    assert sampler.sample_calls == 1
    assert store.last_external_usage_attempt_at(_SLUG) == _NOW  # unchanged — no new attempt


@pytest.mark.unit
def test_a_miss_buffers_exactly_one_missed_report_with_the_exact_key_set(tmp_path: Path) -> None:
    """The `missed` fact payload carries no token, refresh token, or path — this pins its
    exact key set."""
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(miss_reason=SampleMissReason.CREDENTIAL_LAPSED)
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ctx.run()

    pending = [f for f in store.pending_outbound() if f.kind == _MISSED_KIND]
    assert len(pending) == 1
    fact = pending[0]
    # Runner-scoped: no chunk_id/lease_id, mirroring the sampled report's own shape.
    assert fact.chunk_id is None
    assert fact.lease_id is None

    payload = json.loads(fact.payload)
    assert set(payload) == {"slug", "name", "missed_at", "reason"}
    assert payload["slug"] == _SLUG
    assert payload["name"] == _SLUG.title()
    assert payload["missed_at"] == "2026-08-01T12:00:00+00:00"
    assert payload["reason"] == "credential_lapsed"


@pytest.mark.unit
def test_a_successful_sample_enqueues_no_missed_report(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ctx.run()

    assert [f for f in store.pending_outbound() if f.kind == _MISSED_KIND] == []


@pytest.mark.unit
def test_a_successful_sample_records_an_attempt_with_no_miss_reason(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ctx.run()

    attempt = store.latest_external_usage_attempt(_SLUG)
    assert attempt is not None
    assert attempt.ok is True
    assert attempt.miss_reason is None


# AC 4 — a raising adapter leaves the pass completing normally.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_a_raising_adapter_is_logged_and_records_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(raises=RuntimeError("boom"))
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    with capture_logs() as logs:
        ctx.run()  # must not raise

    assert sampler.sample_calls == 1
    warnings = [e for e in logs if "external subscription usage sample pass failed" in e["event"]]
    assert len(warnings) == 1
    # No attempt row survives a raise inside the try (the store write never ran).
    assert store.last_external_usage_attempt_at(_SLUG) is None


# AC 5 — several passes across a virtual clock match the adapter-call count.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_several_passes_sample_at_the_expected_cadence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    sampling = _ctx(store, sampler=sampler, clock=clock, interval_seconds=300)

    # Passes at t=0, 100, 200, 300, 400, 500, 600 (7 passes, 100s apart): the gate fires
    # on t=0, t=300, t=600 — 3 calls.
    for _ in range(7):
        sampling.run()
        clock.advance(timedelta(seconds=100))

    assert sampler.sample_calls == 3


# AC 6 — several declared subscriptions, sampled independently.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_two_declarations_with_different_cadences_only_the_due_one_samples(tmp_path: Path) -> None:
    """A short-interval and a long-interval declaration both sample on the first ever
    tick (neither has an anchor yet), but only the short one is due on the second."""
    store = _store(tmp_path)
    fast = FakeSubscriptionSampler(snapshot=_snapshot())
    slow = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _pass(
        store,
        clock,
        _declared("fast", interval_seconds=100, sampler=fast),
        _declared("slow", interval_seconds=10_000, sampler=slow),
    )

    ctx.run()
    assert fast.sample_calls == 1
    assert slow.sample_calls == 1

    clock.advance(timedelta(seconds=200))
    ctx.run()

    assert fast.sample_calls == 2  # due again — 200s > its 100s interval
    assert slow.sample_calls == 1  # not due — 200s < its 10_000s interval


@pytest.mark.unit
def test_a_failed_sample_advances_only_its_own_slugs_cadence_and_leaves_the_other_readable(tmp_path: Path) -> None:
    """A slug whose sample this tick failed (``None``) still gets a NULL-payload attempt
    row, advancing only ITS anchor — the other slug's last successful sample and cadence
    anchor are untouched."""
    store = _store(tmp_path)
    ok = FakeSubscriptionSampler(snapshot=_snapshot(sampled_at=_NOW))
    failing = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _pass(
        store,
        clock,
        _declared("good", interval_seconds=100, sampler=ok),
        _declared("bad", interval_seconds=100, sampler=failing),
    )

    ctx.run()  # both sample cleanly the first time
    assert store.last_external_usage_attempt_at("good") == _NOW
    assert store.last_external_usage_attempt_at("bad") == _NOW
    good_reports_after_first = [f for f in store.pending_outbound() if f.kind == _SAMPLED_KIND]
    assert len(good_reports_after_first) == 2

    clock.advance(timedelta(seconds=200))
    failing.snapshot = None  # "bad" now fails this tick; "good" still produces a snapshot
    ctx.run()

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
def test_a_declared_provider_with_no_sampler_stays_declared_and_unsampled(tmp_path: Path) -> None:
    """A slug whose provider names no known sampler binding carries a ``None`` sampler
     — no crash, no attempt row for it, and the other declared subscription
    is sampled normally in the same tick."""
    store = _store(tmp_path)
    known = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _pass(
        store,
        clock,
        _declared("known", interval_seconds=100, sampler=known),
        _declared("no-binding", interval_seconds=100, sampler=None),
    )

    ctx.run()  # must not raise

    assert known.sample_calls == 1
    assert store.last_external_usage_attempt_at("known") == _NOW
    assert store.last_external_usage_attempt_at("no-binding") is None  # never attempted


# The resample — a subscription's exhaustion asking for a sample ahead of its cadence.
# --------------------------------------------------------------------------- #


def _mark(
    store: SqlAlchemyRunnerStore, at: datetime, *, resets_in: timedelta | None = timedelta(hours=1), generation: int = 1
) -> None:
    make_host_stores(store).usage_limits.record_usage_limit_mark(
        _SLUG, usage_limit_mark(at, resets_in=resets_in, generation=generation)
    )


@pytest.mark.unit
def test_a_mark_newer_than_the_newest_attempt_samples_early_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=3600)
    ctx.run()
    assert sampler.sample_calls == 1

    clock.advance(timedelta(minutes=5))
    _mark(store, clock.now())
    clock.advance(timedelta(seconds=1))
    ctx.run()
    assert sampler.sample_calls == 2  # the mark is newer than the newest attempt

    clock.advance(timedelta(minutes=1))
    ctx.run()
    assert sampler.sample_calls == 2  # the early sample is now the newest attempt — fired once


@pytest.mark.unit
def test_a_mark_older_than_the_newest_attempt_waits_for_the_cadence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    _mark(store, _NOW - timedelta(seconds=1), resets_in=timedelta(hours=5))
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=3600)
    ctx.run()

    clock.advance(timedelta(minutes=5))
    ctx.run()

    assert sampler.sample_calls == 1


@pytest.mark.unit
def test_a_passed_reset_with_no_attempt_since_samples_early_once(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    clock = FixedClock(_NOW)
    _mark(store, _NOW - timedelta(minutes=1), resets_in=timedelta(minutes=10))  # resets at _NOW + 9 min
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=3600)
    ctx.run()
    assert sampler.sample_calls == 1  # the first-ever attempt

    clock.advance(timedelta(minutes=8))
    ctx.run()
    assert sampler.sample_calls == 1  # the reset is still ahead

    clock.advance(timedelta(minutes=2))
    ctx.run()
    assert sampler.sample_calls == 2  # the reset passed with no attempt since

    clock.advance(timedelta(minutes=1))
    ctx.run()
    assert sampler.sample_calls == 2  # fired once: the attempt it recorded postdates the reset


@pytest.mark.unit
def test_a_missed_early_sample_counts_as_the_attempt_and_falls_back_to_the_cadence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(miss_reason=SampleMissReason.ENDPOINT_UNREACHABLE)
    clock = FixedClock(_NOW)
    ctx = _ctx(store, sampler=sampler, clock=clock, interval_seconds=3600)
    ctx.run()
    clock.advance(timedelta(minutes=5))
    _mark(store, clock.now(), resets_in=timedelta(minutes=1))
    clock.advance(timedelta(seconds=1))

    ctx.run()
    assert sampler.sample_calls == 2  # the mark's early sample, missed

    clock.advance(timedelta(minutes=5))  # past the mark's reset — the miss postdates the mark, not the reset
    ctx.run()
    assert sampler.sample_calls == 3  # the passed reset's own early sample, missed too

    clock.advance(timedelta(minutes=5))
    ctx.run()
    assert sampler.sample_calls == 3  # both misses were attempts: back on the hour's cadence
