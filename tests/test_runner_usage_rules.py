"""Runner usage rules (unit tier, by value): the spend total against a cap, the usage kind and
priced model of one invocation, a rejected billed reading, the transcript range an invocation is
charged for, the context-sample cadence and warn crossing, the soonest pending usage-limit reset,
and the attempt row one subscription sample records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.fact_kinds import EXTERNAL_SUBSCRIPTION_USAGE_MISSED, EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED
from blizzard.runner.harness.usage import SessionCostBasis, UsageSample
from blizzard.runner.subscriptions.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ExternalSubscriptionUsageWindow,
    SampleMiss,
    SampleMissReason,
)
from blizzard.runner.usage.repository import (
    ChargeRange,
    ContextSampleState,
    UsageTotals,
    charge_range,
    derive_invocation_cost,
    effective_model,
    external_usage_attempt,
    soonest_exhausted_reset,
    usage_kind_for,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _totals(cost_usd: float) -> UsageTotals:
    return UsageTotals(
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=cost_usd,
        cost_partial=False,
    )


def test_totals_reach_cap_at_equality() -> None:
    assert _totals(10.0).reaches(10.0)
    assert _totals(10.01).reaches(10.0)
    assert not _totals(9.99).reaches(10.0)


def test_first_generation_is_spawn() -> None:
    assert usage_kind_for(0) == "spawn"
    assert usage_kind_for(1) == "spawn"
    assert usage_kind_for(2) == "resume"


def test_requested_model_wins() -> None:
    assert effective_model("opus", "sonnet") == "opus"
    assert effective_model(None, "sonnet") == "sonnet"
    assert effective_model(None, None) is None


def test_rejected_billed_reading_is_flagged() -> None:
    sample = UsageSample(
        kind="resume",
        model="claude-x",
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=3,
        cache_create_tokens=4,
        cost_usd=3.0,
        cost_scope_tokens=74,
    )
    banked = SessionCostBasis(token_total=37, banked_cost_usd=5.0)
    rejected = derive_invocation_cost(sample, banked)
    assert rejected.cost_usd is None
    assert rejected.billed_reading_rejected
    assert not derive_invocation_cost(sample, None).billed_reading_rejected
    unbilled = UsageSample(
        kind="resume",
        model="claude-x",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
    )
    assert not derive_invocation_cost(unbilled, banked).billed_reading_rejected


@dataclass(frozen=True)
class _Start:
    start_position: str | None
    start_unreadable: bool = False


def test_no_start_charges_nothing() -> None:
    assert charge_range(None, None) is None
    assert charge_range(None, _Start("p9")) is None


def test_unreadable_start_or_end_charges_nothing() -> None:
    assert charge_range(_Start(None, start_unreadable=True), None) is None
    assert charge_range(_Start("p1"), _Start(None, start_unreadable=True)) is None


def test_end_is_end_boundary_start_else_tail() -> None:
    assert charge_range(_Start("p1"), _Start("p9")) == ChargeRange(start="p1", end="p9")
    assert charge_range(_Start("p1"), _Start(None)) == ChargeRange(start="p1", end=None)
    assert charge_range(_Start("p1"), None) == ChargeRange(start="p1", end=None)
    assert charge_range(_Start(None), None) == ChargeRange(start=None, end=None)


def test_never_sampled_is_due() -> None:
    assert ContextSampleState.sample_due(None, now=_NOW, interval=timedelta(seconds=60))


def test_not_due_inside_interval() -> None:
    state = ContextSampleState(last_sampled_at=_NOW - timedelta(seconds=59), max_context_tokens=None)
    assert not ContextSampleState.sample_due(state, now=_NOW, interval=timedelta(seconds=60))
    assert ContextSampleState.sample_due(state, now=_NOW + timedelta(seconds=1), interval=timedelta(seconds=60))


def test_warn_only_on_first_crossing() -> None:
    below = ContextSampleState(last_sampled_at=_NOW, max_context_tokens=900)
    above = ContextSampleState(last_sampled_at=_NOW, max_context_tokens=1100)
    assert ContextSampleState.first_crossing(None, tokens=1001, warn_tokens=1000)
    assert ContextSampleState.first_crossing(below, tokens=1001, warn_tokens=1000)
    assert not ContextSampleState.first_crossing(above, tokens=1200, warn_tokens=1000)
    assert not ContextSampleState.first_crossing(below, tokens=1000, warn_tokens=1000)
    assert not ContextSampleState.first_crossing(None, tokens=None, warn_tokens=1000)


def _window(pct: float, resets_at: datetime) -> ExternalSubscriptionUsageWindow:
    return ExternalSubscriptionUsageWindow(window="5h", utilization_pct=pct, resets_at=resets_at, window_seconds=18000)


def test_soonest_exhausted_reset() -> None:
    soon, later = _NOW + timedelta(hours=1), _NOW + timedelta(hours=3)
    windows = {
        "a": (_window(100.0, later), _window(99.9, _NOW + timedelta(minutes=5))),
        "b": (_window(100.0, soon), _window(100.0, _NOW)),
    }
    assert soonest_exhausted_reset(windows, _NOW) == soon
    assert soonest_exhausted_reset({"a": (_window(80.0, soon),)}, _NOW) is None
    assert soonest_exhausted_reset({}, _NOW) is None


def test_miss_records_miss_kind_and_null_payload() -> None:
    attempt = external_usage_attempt(SampleMiss(SampleMissReason.CREDENTIAL_LAPSED), slug="max", at=_NOW)
    assert attempt.missed
    assert attempt.snapshot is None
    assert attempt.report_kind == EXTERNAL_SUBSCRIPTION_USAGE_MISSED
    assert attempt.miss_reason is SampleMissReason.CREDENTIAL_LAPSED
    assert attempt.result is SampleMissReason.CREDENTIAL_LAPSED
    assert (attempt.slug, attempt.sampled_at) == ("max", _NOW)


def test_snapshot_records_sampled_kind() -> None:
    snapshot = ExternalSubscriptionUsageSnapshot(sampled_at=_NOW, windows=(_window(40.0, _NOW + timedelta(hours=2)),))
    attempt = external_usage_attempt(snapshot, slug="max", at=_NOW)
    assert not attempt.missed
    assert attempt.snapshot == snapshot
    assert attempt.report_kind == EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED
    assert attempt.miss_reason is None
    assert attempt.result == snapshot
