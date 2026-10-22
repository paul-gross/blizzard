"""The declining report — why a runner is held back, by value over the gate's answer, and sent on every registration.

Driven against a real (in-memory sqlite) runner store, the floor's marks written through the store members the gate
reads, with a fake hub standing in at the registration seam."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import RUNNER_LOCALLY_PAUSED
from blizzard.runner.hub.client import DecliningDeclaration
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopConfig, LoopContext, ResolvedSubscription
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from blizzard.runner.throttle.floor import FloorAnswer, FloorHold, HoldKind
from blizzard.runner.throttle.gate import ClaimsAnswer, GateVerdict, ThrottleGate
from blizzard.runner.throttle.pause import RunnerBrakes
from blizzard.runner.throttle.report import DecliningReason, declining_report
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
    serving,
    usage_limit_mark,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
_SLUG = "team-plan"
_FREE = RunnerBrakes(local=False, hub=False)


def _ctx(*, subscription: str | None = _SLUG) -> tuple[Any, FakeHub, LoopContext]:
    store = make_store("sqlite://")
    hub = FakeHub()
    subscriptions = (
        (ResolvedSubscription(slug=_SLUG, name="Team", provider=PROVIDER_ANTHROPIC, sample_interval_seconds=300),)
        if subscription is not None
        else ()
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(
            runner_name="r1", runner_id=FIXTURE_RUNNER_ID, bindings=serving(1), subscription=subscription
        ),
        subscriptions=subscriptions,
    )
    return store, hub, ctx


def _report(ctx: LoopContext, brakes: RunnerBrakes = _FREE) -> tuple[DecliningReason, ...]:
    return declining_report(ThrottleGate(ctx).claims(brakes), now=_NOW)


def _sent(ctx: LoopContext, hub: FakeHub) -> tuple[DecliningDeclaration, ...] | None:
    Registration(ctx).run()
    return hub.registered_declining[-1]


def test_an_admitting_runner_reports_nothing() -> None:
    _store, _hub, ctx = _ctx()

    assert _report(ctx) == ()


def test_a_floor_holding_before_its_reset_is_exhausted_until_that_reset() -> None:
    _store, _hub, ctx = _ctx()
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW - timedelta(minutes=1)))

    assert _report(ctx) == (
        DecliningReason("floor", "exhausted", _SLUG, _NOW - timedelta(minutes=1) + timedelta(hours=1)),
    )


def test_a_floor_past_its_reset_awaiting_a_sample_names_no_instant() -> None:
    _store, _hub, ctx = _ctx()
    reset = _NOW - timedelta(minutes=10)
    ctx.host_stores.usage_limits.record_usage_limit_mark(
        _SLUG, usage_limit_mark(reset - timedelta(hours=1), resets_in=timedelta(hours=1))
    )

    (reason,) = _report(ctx)

    assert reason.lever == "floor"
    assert reason.detail == "exhausted, reset passed and no sample since"
    assert reason.subscription == _SLUG
    assert reason.until is None


def test_a_runner_naming_no_subscription_reports_its_own_floor_with_no_subscription() -> None:
    _store, _hub, ctx = _ctx(subscription=None)
    ctx.stores.usage_limits.record_usage_limit_mark(usage_limit_mark(_NOW))

    (reason,) = _report(ctx)

    assert (reason.lever, reason.subscription) == ("floor", None)


def test_a_braked_runner_reports_nothing_even_with_a_holding_floor() -> None:
    _store, _hub, ctx = _ctx()
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW))

    assert _report(ctx, RunnerBrakes(local=True, hub=False)) == ()
    assert _report(ctx, RunnerBrakes(local=False, hub=True)) == ()


def test_the_report_follows_the_gates_order() -> None:
    held = FloorAnswer(admits=False, slug=_SLUG, hold=_hold())
    answer = ClaimsAnswer(brakes=_FREE, verdict=GateVerdict(floor=held))

    assert [r.lever for r in declining_report(answer, now=_NOW)] == ["floor"]


def _hold():

    return FloorHold(HoldKind.WINDOW, None, "5h", _NOW + timedelta(hours=2), awaits_sample=True)


# --- the registration sends it -----------------------------------------------------------------------------------------


def test_a_registration_sends_what_the_gate_reports() -> None:
    _store, hub, ctx = _ctx()
    assert _sent(ctx, hub) == ()

    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW))

    assert _sent(ctx, hub) == tuple(
        DecliningDeclaration(r.lever, r.detail, r.subscription, r.until) for r in _report(ctx)
    )
    assert hub.registered_declining[-1] != ()


def test_a_registration_from_a_braked_runner_sends_an_empty_report() -> None:
    store, hub, ctx = _ctx()
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW))
    store.record_local_pause(
        paused=True,
        at=_NOW,
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED,
        report_payload=json.dumps({"by": "operator"}),
    )

    assert _sent(ctx, hub) == ()
