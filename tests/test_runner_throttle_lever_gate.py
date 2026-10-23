"""The gate asking the configured levers — its verdict's order, what a continuation reads, and what a lever's
reason carries to the hub and the runner's log.

Driven against a real (in-memory sqlite) runner store, the samples written through the host store the gate reads."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.lifecycle.spawn import Spawner, SuppressionCause
from blizzard.runner.loop.context import LoopConfig, LoopContext, ResolvedSubscription
from blizzard.runner.loop.steps import Fill
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from blizzard.runner.throttle.gate import GateVerdict, StartKind, ThrottleGate
from blizzard.runner.throttle.levers import LeverAnswer, LeverName
from blizzard.runner.throttle.pause import RunnerBrakes
from blizzard.runner.throttle.report import declining_report
from blizzard.runner.throttle.settings import (
    PaceSettings,
    ScheduleSettings,
    Span,
    ThrottleSettings,
    UtilizationSettings,
)
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

_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
_SLUG = "team-plan"
_FREE = RunnerBrakes(local=False, hub=False)


def _ctx(throttle: ThrottleSettings | None) -> LoopContext:
    return make_context(
        make_store("sqlite://"),
        hub=FakeHub(),
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(
            runner_name="r1",
            runner_id=FIXTURE_RUNNER_ID,
            bindings=serving(1),
            agents=1,
            subscription=_SLUG,
            throttle=throttle,
        ),
        subscriptions=(
            ResolvedSubscription(slug=_SLUG, name="Team", provider=PROVIDER_ANTHROPIC, sample_interval_seconds=300),
        ),
    )


def _sample(ctx: LoopContext, pct: float, *, label: str = "5h", seconds: int = 18000, resets_in_h: float = 2.0) -> None:
    window = {
        "window": label,
        "utilization_pct": pct,
        "resets_at": (_NOW + timedelta(hours=resets_in_h)).isoformat(),
        "window_seconds": seconds,
    }
    ctx.host_stores.external_usage.record_external_usage_attempt(
        slug=_SLUG,
        sampled_at=_NOW - timedelta(minutes=1),
        payload=json.dumps({"windows": [window]}),
        report_kind="",
        report_payload="",
    )


_ALL = ThrottleSettings(
    pace=PaceSettings(window="5h", lead_pct=5.0),
    utilization=UtilizationSettings(below_pct=95.0),
    schedule=ScheduleSettings(days=(0, 1, 2, 3, 4, 5, 6), spans=(Span(0, 60),)),
)


def test_a_runner_configuring_no_levers_carries_none_in_its_verdict() -> None:
    ctx = _ctx(None)
    _sample(ctx, 99.0)

    verdict = ThrottleGate(ctx).verdict()

    assert verdict.levers == ()
    assert verdict.admits_claim


def test_the_verdict_holds_the_configured_levers_in_gate_order_and_declines_in_that_order() -> None:
    ctx = _ctx(_ALL)
    # 96% spent two hours into five (60% elapsed): pace and the line decline, and noon is outside the schedule's hour.
    _sample(ctx, 96.0, resets_in_h=3.0)

    verdict = ThrottleGate(ctx).verdict()

    assert [lever.lever for lever in verdict.levers] == [LeverName.PACE, LeverName.UTILIZATION, LeverName.SCHEDULE]
    assert [answer.lever for answer in verdict.declining if isinstance(answer, LeverAnswer)] == [
        LeverName.PACE,
        LeverName.UTILIZATION,
        LeverName.SCHEDULE,
    ]
    assert not verdict.admits_claim and not verdict.admits_step


def test_a_floor_holding_is_declined_first_ahead_of_the_levers() -> None:
    ctx = _ctx(ThrottleSettings(utilization=UtilizationSettings(below_pct=50.0)))
    _sample(ctx, 60.0)
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW - timedelta(minutes=1)))

    declining = ThrottleGate(ctx).verdict().declining

    assert not isinstance(declining[0], LeverAnswer)
    assert isinstance(declining[1], LeverAnswer)


def test_a_continuation_answers_to_the_floor_alone_and_reads_no_lever() -> None:
    ctx = _ctx(ThrottleSettings(utilization=UtilizationSettings(below_pct=50.0)))
    _sample(ctx, 99.0)

    assert not ThrottleGate(ctx).verdict().admits_step
    assert ThrottleGate(ctx).verdict().admits_continuation
    assert Spawner(ctx).suppression(start=StartKind.CONTINUATION, via="test", chunk_id="ch_1") is None
    step = Spawner(ctx).suppression(start=StartKind.STEP, via="test", chunk_id="ch_1")
    assert step is not None and step.cause is SuppressionCause.DECLINED


def test_a_continuations_suppression_never_names_a_lever() -> None:
    ctx = _ctx(ThrottleSettings(utilization=UtilizationSettings(below_pct=50.0)))
    _sample(ctx, 99.0)
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW - timedelta(minutes=1)))

    suppression = Spawner(ctx).suppression(start=StartKind.CONTINUATION, via="test", chunk_id="ch_1")

    assert suppression is not None and suppression.verdict is not None
    assert suppression.verdict.levers == ()
    assert not suppression.verdict.floor.admits


def test_a_lever_reason_carries_its_reading_and_no_setting_or_subscription() -> None:
    ctx = _ctx(ThrottleSettings(utilization=UtilizationSettings(below_pct=95.0)))
    _sample(ctx, 96.0)

    [reason] = declining_report(ThrottleGate(ctx).claims(_FREE), now=_NOW)

    assert reason.lever == "utilization"
    assert reason.detail == "5h: 96%"
    assert reason.subscription is None
    assert reason.until == _NOW + timedelta(hours=2)


def test_fill_logs_every_declining_throttle_and_claims_nothing() -> None:
    ctx = _ctx(ThrottleSettings(utilization=UtilizationSettings(below_pct=95.0)))
    _sample(ctx, 96.0)

    with capture_logs() as logs:
        Fill(ctx).run()

    [entry] = [log for log in logs if log["event"] == "declining — no new claims this tick"]
    assert entry["declining"] == ["utilization"]


def test_an_admitting_sample_after_a_declining_one_admits_again() -> None:
    ctx = _ctx(ThrottleSettings(utilization=UtilizationSettings(below_pct=95.0)))
    _sample(ctx, 96.0)
    assert not ThrottleGate(ctx).verdict().admits_claim

    _sample(ctx, 40.0)

    verdict: GateVerdict = ThrottleGate(ctx).verdict()
    assert verdict.admits_claim
    assert verdict.declining == ()
