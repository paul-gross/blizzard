"""Runner subscription rules (unit tier, by value): the sample cadence, a lapsed
credential, a usage window still holding a limit, and credential-renewal timing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.loop.context import ResolvedSubscription
from blizzard.runner.subscriptions.credential_renewer import renewal_due
from blizzard.runner.subscriptions.subscription_sampler import (
    ExternalSubscriptionUsageSnapshot,
    ExternalSubscriptionUsageWindow,
    SampleMiss,
    SampleMissReason,
    credential_lapsed,
    sample_due,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def test_never_attempted_is_due() -> None:
    assert sample_due(None, _NOW, 300)


def test_sample_due_after_interval() -> None:
    assert not sample_due(_NOW - timedelta(seconds=299), _NOW, 300)
    assert sample_due(_NOW - timedelta(seconds=300), _NOW, 300)


class _Sampler:
    def sample(self) -> ExternalSubscriptionUsageSnapshot | SampleMiss:
        return SampleMiss(SampleMissReason.ENDPOINT_UNREACHABLE)


def _resolved(sampler: _Sampler | None) -> ResolvedSubscription:
    return ResolvedSubscription(
        slug="max", name="Max", provider="anthropic", sample_interval_seconds=300, sampler=sampler
    )


def test_lapsed_at_expiry() -> None:
    assert credential_lapsed(_NOW, _NOW)
    assert credential_lapsed(_NOW - timedelta(seconds=1), _NOW)
    assert not credential_lapsed(_NOW + timedelta(seconds=1), _NOW)


def test_exhausted_pending_needs_full_use_and_future_reset() -> None:
    def window(pct: float, resets_at: datetime) -> ExternalSubscriptionUsageWindow:
        return ExternalSubscriptionUsageWindow(window="7d", utilization_pct=pct, resets_at=resets_at, window_seconds=1)

    ahead = _NOW + timedelta(minutes=1)
    assert window(100.0, ahead).exhausted_pending(_NOW)
    assert window(120.0, ahead).exhausted_pending(_NOW)
    assert not window(99.9, ahead).exhausted_pending(_NOW)
    assert not window(100.0, _NOW).exhausted_pending(_NOW)


def test_due_inside_lead_window() -> None:
    lead = timedelta(minutes=10)
    expires_at = _NOW + timedelta(minutes=10)
    assert renewal_due(expires_at, _NOW, lead)
    assert not renewal_due(expires_at + timedelta(seconds=1), _NOW, lead)
    assert renewal_due(_NOW - timedelta(hours=1), _NOW, lead)
