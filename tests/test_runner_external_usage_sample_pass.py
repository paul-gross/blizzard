"""``ExternalUsageSamplePass`` — the host tick's sampling pass.

Unit and component tiers against a real (tmp sqlite) store and a scriptable
``FakeSubscriptionSampler``: the per-slug cadence gate, the report each attempt buffers, and several declared
subscriptions sampled independently — a failed one isolated to its own slug, an unbound provider staying declared and
unsampled."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.subscriptions.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ExternalSubscriptionUsageWindow,
    SampleMissReason,
)
from blizzard.runner.usage.external_usage_sample import ExternalUsageSamplePass, SampledSubscription
from tests.runner_fakes import FakeSubscriptionSampler, make_store

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
_SAMPLED_KIND = "external_subscription_usage.sampled"
_MISSED_KIND = "external_subscription_usage.missed"
_SLUG = "anthropic"


class _StoreSink:
    """Records through the store's own runner-lane write, as an unregistered host's first runner does."""

    def __init__(self, store) -> None:  # type: ignore[no-untyped-def]
        self._store = store

    def record(self, **attempt) -> None:  # type: ignore[no-untyped-def]
        self._store.record_external_usage_attempt(**attempt)


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _declared(
    slug: str = _SLUG, *, interval_seconds: int = 300, sampler: FakeSubscriptionSampler | None = None
) -> SampledSubscription:
    return SampledSubscription(slug=slug, name=slug.title(), sample_interval_seconds=interval_seconds, sampler=sampler)


def _pass(store, clock: FixedClock, *subscriptions: SampledSubscription) -> ExternalUsageSamplePass:  # type: ignore[no-untyped-def]
    return ExternalUsageSamplePass(subscriptions=subscriptions, attempts=store, sink=_StoreSink(store), clock=clock)


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
    return _pass(store, clock, _declared(interval_seconds=interval_seconds, sampler=sampler))


# AC 1 — cadence gate.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_first_ever_attempt_samples_immediately(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    assert store.last_external_usage_attempt_at(_SLUG) is None
    ctx.run()

    assert sampler.sample_calls == 1
    assert store.last_external_usage_attempt_at(_SLUG) == _NOW


@pytest.mark.unit
def test_within_the_interval_the_adapter_is_not_called(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_at_exactly_the_interval_the_adapter_is_called(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_past_the_interval_the_adapter_is_called(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_a_successful_sample_records_one_attempt_and_enqueues_one_runner_scoped_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
    tmp_path, invalid_pct: float
) -> None:  # type: ignore[no-untyped-def]
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


def test_an_empty_sample_is_persisted_and_buffered_as_an_empty_windows_collection(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=ExternalSubscriptionUsageSnapshot(sampled_at=_NOW, windows=()))
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ctx.run()

    payload = json.loads(next(f.payload for f in store.pending_outbound() if f.kind == _SAMPLED_KIND))
    assert payload["windows"] == []


# AC 3 — a miss writes a NULL-payload attempt row and enqueues a `missed` report, never a `sampled` one.
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_no_sample_records_a_null_payload_attempt_and_enqueues_no_sampled_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_a_miss_buffers_exactly_one_missed_report_with_the_exact_key_set(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_a_successful_sample_enqueues_no_missed_report(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    sampler = FakeSubscriptionSampler(snapshot=_snapshot())
    ctx = _ctx(store, sampler=sampler, clock=FixedClock(_NOW))

    ctx.run()

    assert [f for f in store.pending_outbound() if f.kind == _MISSED_KIND] == []


@pytest.mark.unit
def test_a_successful_sample_records_an_attempt_with_no_miss_reason(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_a_raising_adapter_is_logged_and_records_nothing(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_several_passes_sample_at_the_expected_cadence(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_two_declarations_with_different_cadences_only_the_due_one_samples(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_a_failed_sample_advances_only_its_own_slugs_cadence_and_leaves_the_other_readable(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
def test_a_declared_provider_with_no_sampler_stays_declared_and_unsampled(tmp_path) -> None:  # type: ignore[no-untyped-def]
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
