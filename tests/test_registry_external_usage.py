""":meth:`PerSubscriptionUsageView.every` — the read-side per-subscription staleness
gate (issue #218, blizzard#436 phase 3).

Unit tier: the pure domain derivation in isolation, then its rendering through
``hub/api/runners.py``'s single ``runner_view`` — no store, no HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.api.runners import runner_view
from blizzard.hub.domain.registry import (
    EXTERNAL_USAGE_STALE_AFTER,
    ExternalSubscriptionUsageWindow,
    PerSubscriptionUsageView,
    RunnerLiveness,
    RunnerRegistration,
    SubscriptionUsageRecord,
)
from tests.support import assert_utc_iso

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

_WINDOW = ExternalSubscriptionUsageWindow(
    window="5h", utilization_pct=42.5, resets_at=_NOW + timedelta(hours=2), window_seconds=18000
)


def test_a_sample_inside_the_staleness_window_renders() -> None:
    sampled_at = _NOW - timedelta(minutes=14)
    views = PerSubscriptionUsageView.every(_registration(records=(_record("anthropic", sampled_at),)), now=_NOW)
    assert len(views) == 1
    assert views[0].sampled_at == sampled_at
    assert views[0].windows == (_WINDOW,)


def test_a_sample_past_the_staleness_window_renders_none() -> None:
    views = PerSubscriptionUsageView.every(
        _registration(records=(_record("anthropic", _NOW - timedelta(minutes=16)),)), now=_NOW
    )
    assert views == ()


def test_a_sample_exactly_at_the_threshold_still_renders() -> None:
    """The threshold itself is inclusive (``RunnerLiveness.of``'s own ``<=`` convention)."""
    views = PerSubscriptionUsageView.every(
        _registration(records=(_record("anthropic", _NOW - EXTERNAL_USAGE_STALE_AFTER),)), now=_NOW
    )
    assert len(views) == 1


def test_never_sampled_renders_none() -> None:
    assert PerSubscriptionUsageView.every(_registration(records=()), now=_NOW) == ()


def test_a_stale_or_failed_subscription_does_not_blank_a_healthy_sibling() -> None:
    """One dead sampler must not blank a healthy one (blizzard#436 phase 3) — the plan's
    explicit staleness-is-per-subscription acceptance bar."""
    healthy = _record("anthropic", _NOW - timedelta(minutes=1))
    stale = _record("openai", _NOW - timedelta(minutes=16))
    registration = _registration(records=(healthy, stale))

    views = PerSubscriptionUsageView.every(registration, now=_NOW)

    assert [view.slug for view in views] == ["anthropic"]
    assert views[0].windows == (_WINDOW,)


def test_every_renders_only_the_non_stale_subscriptions() -> None:
    healthy = _record("anthropic", _NOW - timedelta(minutes=1))
    stale = _record("openai", _NOW - timedelta(minutes=16))
    registration = _registration(records=(healthy, stale))

    views = PerSubscriptionUsageView.every(registration, now=_NOW)

    assert [v.slug for v in views] == ["anthropic"]
    assert views[0].name == "Anthropic"
    assert views[0].windows == (_WINDOW,)


def test_every_renders_multiple_distinct_healthy_subscriptions() -> None:
    anthropic = _record("anthropic", _NOW - timedelta(minutes=1), name="Anthropic")
    openai = _record("openai", _NOW - timedelta(minutes=2), name="OpenAI")
    registration = _registration(records=(anthropic, openai))

    views = PerSubscriptionUsageView.every(registration, now=_NOW)

    assert {v.slug for v in views} == {"anthropic", "openai"}
    assert {v.name for v in views} == {"Anthropic", "OpenAI"}


def _record(slug: str, sampled_at: datetime, *, name: str | None = None) -> SubscriptionUsageRecord:
    return SubscriptionUsageRecord(slug=slug, name=name or slug.title(), sampled_at=sampled_at, windows=(_WINDOW,))


def _registration(*, records: tuple[SubscriptionUsageRecord, ...]) -> RunnerRegistration:
    return RunnerRegistration(
        runner_id="runner-a",
        workspace_id="ws-a",
        registered_at=_NOW,
        last_seen_at=_NOW,
        hub_paused=False,
        subscription_usage=records,
    )


def test_the_rendered_view_carries_an_explicit_utc_offset_on_every_instant() -> None:
    registration = _registration(records=(_record("anthropic", _NOW - timedelta(minutes=1)),))
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)

    assert_utc_iso(view.subscriptions[0].sampled_at)
    assert_utc_iso(view.subscriptions[0].windows[0].resets_at)
    assert_utc_iso(view.registered_at)
    assert_utc_iso(view.last_seen_at)


def test_the_rendered_view_has_no_subscriptions_when_never_sampled() -> None:
    registration = _registration(records=())
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)
    assert view.subscriptions == []


def test_the_rendered_view_has_no_subscriptions_when_the_sample_is_stale() -> None:
    registration = _registration(records=(_record("anthropic", _NOW - timedelta(minutes=16)),))
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)
    assert view.subscriptions == []


def test_the_rendered_view_omits_a_stale_sibling_without_affecting_a_healthy_subscription() -> None:
    healthy = _record("anthropic", _NOW - timedelta(minutes=1))
    other = _record("openai", _NOW - timedelta(minutes=20))  # stale
    registration = _registration(records=(healthy, other))
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)

    assert [s.slug for s in view.subscriptions] == ["anthropic"]
    assert view.subscriptions[0].windows[0].utilization_pct == 42.5
