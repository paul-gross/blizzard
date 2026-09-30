""":meth:`PerSubscriptionUsageView.every` — the read-side per-subscription staleness
gate.

Unit tier: the pure domain derivation in isolation, then its rendering through
``hub/api/runners.py``'s single ``runner_view`` — no store, no HTTP."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.api.runners import runner_view
from blizzard.hub.domain.registry import (
    CREDENTIAL_LAPSED_CONDITION,
    EXTERNAL_USAGE_STALE_AFTER,
    DeclaredSubscription,
    ExternalSubscriptionUsageWindow,
    PerSubscriptionUsageView,
    RunnerCapability,
    RunnerLiveness,
    RunnerRegistration,
    SubscriptionUsageMissRecord,
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
    """One dead sampler must not blank a healthy one — the plan's
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


def _miss(
    slug: str, missed_at: datetime, *, name: str | None = None, reason: str = CREDENTIAL_LAPSED_CONDITION
) -> SubscriptionUsageMissRecord:
    return SubscriptionUsageMissRecord(slug=slug, name=name or slug.title(), missed_at=missed_at, reason=reason)


def _registration(
    *,
    records: tuple[SubscriptionUsageRecord, ...] = (),
    misses: tuple[SubscriptionUsageMissRecord, ...] = (),
    roster: tuple[DeclaredSubscription, ...] | None = None,
) -> RunnerRegistration:
    return RunnerRegistration(
        runner_id="runner-a",
        workspace_id="ws-a",
        registered_at=_NOW,
        last_seen_at=_NOW,
        hub_paused=False,
        subscription_usage=records,
        subscription_usage_misses=misses,
        declared_subscriptions=roster,
    )


# — the `condition` derivation.
# --------------------------------------------------------------------------- #


def test_a_miss_with_no_sample_renders_a_miss_only_lapsed_row() -> None:
    views = PerSubscriptionUsageView.every(
        _registration(misses=(_miss("openai", _NOW - timedelta(minutes=1)),)), now=_NOW
    )
    assert len(views) == 1
    assert views[0].slug == "openai"
    assert views[0].name == "Openai"
    assert views[0].sampled_at is None
    assert views[0].windows == ()
    assert views[0].condition == CREDENTIAL_LAPSED_CONDITION


def test_a_miss_newer_than_the_sample_sets_lapsed_over_the_surviving_sample() -> None:
    sample = _record("openai", _NOW - timedelta(minutes=10))
    miss = _miss("openai", _NOW - timedelta(minutes=1))
    views = PerSubscriptionUsageView.every(_registration(records=(sample,), misses=(miss,)), now=_NOW)

    assert len(views) == 1
    assert views[0].sampled_at == sample.sampled_at
    assert views[0].windows == sample.windows
    assert views[0].condition == CREDENTIAL_LAPSED_CONDITION


def test_a_sample_newer_than_the_miss_clears_the_condition() -> None:
    """A fresh successful sample after a miss reads as healthy again — no `condition`."""
    miss = _miss("openai", _NOW - timedelta(minutes=10))
    sample = _record("openai", _NOW - timedelta(minutes=1))
    views = PerSubscriptionUsageView.every(_registration(records=(sample,), misses=(miss,)), now=_NOW)

    assert len(views) == 1
    assert views[0].condition is None
    assert views[0].sampled_at == sample.sampled_at
    assert views[0].windows == (_WINDOW,)


def test_a_non_lapsed_miss_reason_is_silent_even_when_newer_than_the_sample() -> None:
    """Only `credential_lapsed` ever surfaces as a `condition` — every other reason is
    silent, and the sample (if any and non-stale) renders unaffected."""
    sample = _record("openai", _NOW - timedelta(minutes=10))
    miss = _miss("openai", _NOW - timedelta(minutes=1), reason="endpoint_unreachable")
    views = PerSubscriptionUsageView.every(_registration(records=(sample,), misses=(miss,)), now=_NOW)

    assert len(views) == 1
    assert views[0].condition is None
    assert views[0].sampled_at == sample.sampled_at


def test_a_non_lapsed_miss_reason_with_no_sample_renders_nothing() -> None:
    miss = _miss("openai", _NOW - timedelta(minutes=1), reason="endpoint_unreachable")
    assert PerSubscriptionUsageView.every(_registration(misses=(miss,)), now=_NOW) == ()


def test_a_stale_miss_never_surfaces_the_condition() -> None:
    """A live lapsed slug re-reports every cadence; a decommissioned one ages out just
    like a dead sample does."""
    miss = _miss("openai", _NOW - EXTERNAL_USAGE_STALE_AFTER - timedelta(minutes=1))
    assert PerSubscriptionUsageView.every(_registration(misses=(miss,)), now=_NOW) == ()


def test_a_stale_sample_with_a_stale_or_absent_lapsed_miss_drops_out_as_today() -> None:
    """A stale sample whose newest miss is not a non-stale `credential_lapsed` drops out
    exactly as it did before misses existed."""
    stale_sample = _record("openai", _NOW - EXTERNAL_USAGE_STALE_AFTER - timedelta(minutes=1))
    stale_miss = _miss("openai", _NOW - EXTERNAL_USAGE_STALE_AFTER - timedelta(minutes=1))
    views = PerSubscriptionUsageView.every(_registration(records=(stale_sample,), misses=(stale_miss,)), now=_NOW)
    assert views == ()


def test_a_lapsed_miss_over_a_stale_sample_preserves_the_surviving_sample() -> None:
    """A stale sample outranked by a newer lapsed miss keeps its own fields, at parity with
    the roster path's unconditional preserve — staleness gates membership, not a surviving
    sample's fields once the slug is admitted by the lapsed miss."""
    stale_sample = _record("openai", _NOW - timedelta(minutes=20))
    lapsed_miss = _miss("openai", _NOW - timedelta(minutes=5))
    views = PerSubscriptionUsageView.every(_registration(records=(stale_sample,), misses=(lapsed_miss,)), now=_NOW)

    assert len(views) == 1
    assert views[0].sampled_at == stale_sample.sampled_at
    assert views[0].windows == stale_sample.windows
    assert views[0].condition == CREDENTIAL_LAPSED_CONDITION


def test_a_lapsed_sibling_does_not_blank_a_healthy_ones_view() -> None:
    healthy = _record("anthropic", _NOW - timedelta(minutes=1))
    lapsed_miss = _miss("openai", _NOW - timedelta(minutes=1))
    views = PerSubscriptionUsageView.every(_registration(records=(healthy,), misses=(lapsed_miss,)), now=_NOW)

    assert {v.slug for v in views} == {"anthropic", "openai"}
    lapsed = next(v for v in views if v.slug == "openai")
    healthy_view = next(v for v in views if v.slug == "anthropic")
    assert lapsed.condition == CREDENTIAL_LAPSED_CONDITION
    assert healthy_view.condition is None


# --------------------------------------------------------------------------- #
# The declared-roster path — membership is roster-gated, not age-gated.
# --------------------------------------------------------------------------- #


def _declared(*slugs: str) -> tuple[DeclaredSubscription, ...]:
    return tuple(DeclaredSubscription(slug=slug, name=slug.title(), provider="p") for slug in slugs)


def test_a_declared_slug_stays_a_member_whatever_the_age_of_its_sample_or_miss() -> None:
    stale = _NOW - EXTERNAL_USAGE_STALE_AFTER - timedelta(days=1)
    registration = _registration(
        records=(_record("stale", stale),),
        misses=(_miss("missed", stale, reason="endpoint_unreachable"),),
        roster=_declared("stale", "missed", "never"),
    )
    views = PerSubscriptionUsageView.every(registration, now=_NOW)

    assert [v.slug for v in views] == ["missed", "never", "stale"]
    by_slug = {v.slug: v for v in views}
    assert by_slug["stale"].sampled_at == stale
    assert by_slug["never"].sampled_at is None
    assert by_slug["never"].windows == ()


def test_a_roster_lapsed_miss_outranks_an_older_sample_however_old_both_are() -> None:
    stale = _NOW - EXTERNAL_USAGE_STALE_AFTER - timedelta(days=2)
    registration = _registration(
        records=(_record("openai", stale),),
        misses=(_miss("openai", stale + timedelta(days=1)),),
        roster=_declared("openai"),
    )
    (view,) = PerSubscriptionUsageView.every(registration, now=_NOW)

    assert view.condition == CREDENTIAL_LAPSED_CONDITION


def test_a_roster_lapsed_condition_never_blanks_the_surviving_sample_and_a_non_lapsed_miss_sets_none() -> None:
    sample = _record("openai", _NOW - timedelta(minutes=10))
    lapsed = _registration(
        records=(sample,), misses=(_miss("openai", _NOW - timedelta(minutes=1)),), roster=_declared("openai")
    )
    (lapsed_view,) = PerSubscriptionUsageView.every(lapsed, now=_NOW)
    assert lapsed_view.condition == CREDENTIAL_LAPSED_CONDITION
    assert lapsed_view.sampled_at == sample.sampled_at
    assert lapsed_view.windows == sample.windows

    silent = _registration(
        records=(sample,),
        misses=(_miss("openai", _NOW - timedelta(minutes=1), reason="endpoint_unreachable"),),
        roster=_declared("openai"),
    )
    (silent_view,) = PerSubscriptionUsageView.every(silent, now=_NOW)
    assert silent_view.condition is None
    assert silent_view.windows == sample.windows


def test_a_dropped_slugs_reports_persist_and_resume_when_redeclared() -> None:
    sample = _record("openai", _NOW - timedelta(minutes=1))
    miss = _miss("openai", _NOW - timedelta(minutes=2))
    dropped = _registration(records=(sample,), misses=(miss,), roster=_declared("anthropic"))
    assert [v.slug for v in PerSubscriptionUsageView.every(dropped, now=_NOW)] == ["anthropic"]

    redeclared = _registration(records=(sample,), misses=(miss,), roster=_declared("anthropic", "openai"))
    views = {v.slug: v for v in PerSubscriptionUsageView.every(redeclared, now=_NOW)}
    assert views["openai"].sampled_at == sample.sampled_at
    assert views["openai"].miss_reason == CREDENTIAL_LAPSED_CONDITION


def test_the_rendered_view_carries_the_lapsed_condition_through_runner_view() -> None:
    registration = _registration(misses=(_miss("openai", _NOW - timedelta(minutes=1)),))
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)

    assert len(view.subscriptions) == 1
    assert view.subscriptions[0].condition == CREDENTIAL_LAPSED_CONDITION
    assert view.subscriptions[0].sampled_at is None
    assert view.subscriptions[0].windows == []


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


def test_the_rendered_view_carries_every_registered_capability() -> None:
    registration = _registration(records=())
    capabilities = (
        RunnerCapability(harness_id="claude", version="1.2.3", tiers=("sonnet",), default=True, available=True),
        RunnerCapability(harness_id="codex", version=None, tiers=(), default=False, available=False),
    )
    registration = replace(registration, capabilities=capabilities)
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)

    assert [c.harness_id for c in view.capabilities] == ["claude", "codex"]
    assert view.capabilities[0].version == "1.2.3"
    assert view.capabilities[0].tiers == ["sonnet"]
    assert view.capabilities[0].default is True
    assert view.capabilities[1].available is False


def test_the_rendered_view_has_no_capabilities_when_none_were_registered() -> None:
    registration = _registration(records=())
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)
    assert view.capabilities == []


def test_the_rendered_view_omits_a_stale_sibling_without_affecting_a_healthy_subscription() -> None:
    healthy = _record("anthropic", _NOW - timedelta(minutes=1))
    other = _record("openai", _NOW - timedelta(minutes=20))  # stale
    registration = _registration(records=(healthy, other))
    view = runner_view(RunnerLiveness(registration=registration, online=True), now=_NOW)

    assert [s.slug for s in view.subscriptions] == ["anthropic"]
    assert view.subscriptions[0].windows[0].utilization_pct == 42.5
