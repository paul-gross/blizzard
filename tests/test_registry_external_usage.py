"""``derive_external_subscription_usage`` — the read-side staleness gate (issue #218, phase 4).

Unit tier: the pure domain function in isolation, then its rendering through
``hub/api/runners.py``'s single ``runner_view`` — no store, no HTTP. The component/service
round trip (ingest through ``POST /api/fleet/events`` to ``GET /api/runners``) lives in
``tests/test_external_usage_facts_ingest.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.api.runners import runner_view
from blizzard.hub.domain.registry import (
    EXTERNAL_USAGE_STALE_AFTER,
    ExternalSubscriptionUsageWindow,
    RunnerLiveness,
    RunnerRegistration,
    derive_external_subscription_usage,
)
from tests.support import assert_utc_iso

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

_WINDOW = ExternalSubscriptionUsageWindow(
    window="5h", utilization_pct=42.5, resets_at=_NOW + timedelta(hours=2), window_seconds=18000
)


def test_a_sample_inside_the_staleness_window_renders() -> None:
    sampled_at = _NOW - timedelta(minutes=14)
    view = derive_external_subscription_usage(sampled_at, (_WINDOW,), now=_NOW)
    assert view is not None
    assert view.sampled_at == sampled_at
    assert view.windows == (_WINDOW,)


def test_a_sample_past_the_staleness_window_renders_none() -> None:
    sampled_at = _NOW - timedelta(minutes=16)
    assert derive_external_subscription_usage(sampled_at, (_WINDOW,), now=_NOW) is None


def test_a_sample_exactly_at_the_threshold_still_renders() -> None:
    """The threshold itself is inclusive (``derive_online``'s own ``<=`` convention)."""
    sampled_at = _NOW - EXTERNAL_USAGE_STALE_AFTER
    assert derive_external_subscription_usage(sampled_at, (_WINDOW,), now=_NOW) is not None


def test_never_sampled_renders_none() -> None:
    assert derive_external_subscription_usage(None, (), now=_NOW) is None


def _registration(*, external_usage_sampled_at: datetime | None, external_usage_windows=()) -> RunnerRegistration:
    return RunnerRegistration(
        runner_id="runner-a",
        workspace_id="ws-a",
        registered_at=_NOW,
        last_seen_at=_NOW,
        hub_paused=False,
        external_usage_sampled_at=external_usage_sampled_at,
        external_usage_windows=external_usage_windows,
    )


def test_the_rendered_view_carries_an_explicit_utc_offset_on_every_instant() -> None:
    registration = _registration(
        external_usage_sampled_at=_NOW - timedelta(minutes=1), external_usage_windows=(_WINDOW,)
    )
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)

    assert view.external_subscription_usage is not None
    assert_utc_iso(view.external_subscription_usage.sampled_at)
    assert_utc_iso(view.external_subscription_usage.windows[0].resets_at)
    assert_utc_iso(view.registered_at)
    assert_utc_iso(view.last_seen_at)


def test_the_rendered_view_omits_the_block_when_never_sampled() -> None:
    registration = _registration(external_usage_sampled_at=None)
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)
    assert view.external_subscription_usage is None


def test_the_rendered_view_omits_the_block_when_the_sample_is_stale() -> None:
    registration = _registration(
        external_usage_sampled_at=_NOW - timedelta(minutes=16), external_usage_windows=(_WINDOW,)
    )
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)
    assert view.external_subscription_usage is None
