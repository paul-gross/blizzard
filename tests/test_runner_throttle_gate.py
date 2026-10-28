"""The throttle gate — its verdict by value, and every start asking it: FILL's one ask, the per-start funnel, and the
interrupted claim it holds.

Driven against a real (in-memory sqlite) runner store, the floor's marks written through the same store members the
gate reads, with fakes standing in only at the seams."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import RUNNER_LOCALLY_PAUSED
from blizzard.runner.api.control import _throttle_view
from blizzard.runner.cli.control import _Throttle, _throttle_lines
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.hub.client import QueueEntry
from blizzard.runner.lifecycle.spawn import Spawner, SuppressionCause
from blizzard.runner.loop.context import LoopConfig, LoopContext, ResolvedSubscription
from blizzard.runner.loop.steps import Fill
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.status.view import ThrottleStatus
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from blizzard.runner.throttle.floor import FloorAnswer
from blizzard.runner.throttle.gate import GateVerdict, StartKind, ThrottleGate
from blizzard.runner.throttle.pause import RunnerBrakes
from blizzard.runner.throttle.settings import HoldOrRelease, ThrottleSettings
from blizzard.runner.throttle.usage_limits import FloorClear
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
    serving,
    usage_limit_mark,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_SLUG = "team-plan"
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_DECLINING = "declining — no new claims this tick"
_PAUSED = "paused — no new claims this tick"


# --- the verdict, by value -------------------------------------------------------------------------------------------


def test_an_open_floor_admits_every_kind_of_start_and_declines_nothing() -> None:
    verdict = GateVerdict(floor=FloorAnswer(admits=True, slug=_SLUG))

    assert (verdict.admits_claim, verdict.admits_step, verdict.admits_continuation) == (True, True, True)
    assert verdict.declining == ()
    assert all(verdict.admits(kind) for kind in StartKind)


def test_a_held_floor_declines_claims_steps_and_continuations_but_never_an_escalation() -> None:
    floor = FloorAnswer(admits=False, slug=_SLUG)
    verdict = GateVerdict(floor=floor)

    assert (verdict.admits_claim, verdict.admits_step, verdict.admits_continuation) == (False, False, False)
    assert verdict.declining == (floor,)
    assert [kind for kind in StartKind if verdict.admits(kind)] == [StartKind.ESCALATION]


# --- the gate's readings ---------------------------------------------------------------------------------------------


def _ctx(
    *, subscription: str | None = _SLUG, provider: str = PROVIDER_ANTHROPIC, hub: FakeHub | None = None
) -> tuple[Any, FakeHub, LoopContext]:
    store = make_store("sqlite://")
    fake_hub = hub if hub is not None else FakeHub()
    config = LoopConfig(
        runner_name="r1", runner_id=FIXTURE_RUNNER_ID, bindings=serving(1), agents=1, subscription=subscription
    )
    subscriptions = (
        (ResolvedSubscription(slug=_SLUG, name="Team", provider=provider, sample_interval_seconds=300),)
        if subscription is not None
        else ()
    )
    ctx = make_context(
        store,
        hub=fake_hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=config,
        subscriptions=subscriptions,
    )
    return store, fake_hub, ctx


def _hold_subscription(ctx: LoopContext) -> None:
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW - timedelta(minutes=1)))


def test_status_reads_the_gate_even_under_a_brake_and_projects_utc_instants() -> None:
    _store, _hub, ctx = _ctx()
    gate = ThrottleGate(ctx)
    assert _throttle_view(ThrottleStatus.of(gate.verdict(), braked=False)).state == "admitting"
    _hold_subscription(ctx)
    view = _throttle_view(ThrottleStatus.of(gate.verdict(), braked=True))
    assert view.state == "braked"
    assert view.declining == ["floor"]
    assert view.floor.subscription == _SLUG
    assert view.floor.hold is not None
    assert view.floor.hold.expected_reset.endswith("Z")
    assert view.levers == []
    assert "declining: floor" in _throttle_lines(cast(_Throttle, view.model_dump()))[0]
    ctx.host_stores.usage_limits.record_floor_clear(_SLUG, FloorClear(cleared_at=_NOW, by="operator"))
    cleared = _throttle_view(ThrottleStatus.of(gate.verdict(), braked=False))
    assert cleared.floor.admits
    assert cleared.floor.clear is not None
    assert cleared.floor.clear.cleared_at.endswith("Z")


def test_status_holds_on_both_with_no_settings_and_shows_the_configured_choices() -> None:
    _store, _hub, ctx = _ctx()
    gate = ThrottleGate(ctx)
    held = _throttle_view(ThrottleStatus.of(gate.verdict(), braked=False))
    assert (held.on_decline, held.on_usage_limit) == ("hold", "hold")
    releasing = ThrottleSettings(on_decline=HoldOrRelease.RELEASE)
    view = _throttle_view(ThrottleStatus.of(gate.verdict(), braked=False, settings=releasing))
    assert (view.on_decline, view.on_usage_limit) == ("release", "hold")
    assert "on_decline: release; on_usage_limit: hold" in "\n".join(_throttle_lines(cast(_Throttle, view.model_dump())))


def test_cli_renders_configured_lever_setting_reading_answer_and_until() -> None:
    throttle: _Throttle = {
        "state": "declining",
        "declining": ["pace"],
        "floor": {"subscription": None, "admits": True, "hold": None, "clear": None},
        "levers": [
            {
                "name": "pace",
                "setting": "7d, lead 5 points",
                "reading": "70% spent, 50% elapsed",
                "admits": False,
                "until": "2026-10-22T19:00:00Z",
            },
            {
                "name": "schedule",
                "setting": "weekdays 09:00-17:00 America/New_York",
                "reading": "inside span",
                "admits": True,
                "until": None,
            },
        ],
        "on_decline": "hold",
        "on_usage_limit": "hold",
    }

    lines = _throttle_lines(throttle)
    assert lines[2] == "    pace (7d, lead 5 points): hold — 70% spent, 50% elapsed; until 2026-10-22T19:00:00Z"
    assert lines[3] == "    schedule (weekdays 09:00-17:00 America/New_York): admit — inside span; until none"


def _brake(store: Any) -> None:
    store.record_local_pause(
        paused=True,
        at=_NOW,
        by="operator",
        report_kind=RUNNER_LOCALLY_PAUSED,
        report_payload=json.dumps({"by": "operator"}),
    )


def test_the_gate_reads_a_mark_on_the_subscription_the_runner_spends() -> None:
    _store, _hub, ctx = _ctx()
    assert ThrottleGate(ctx).floor().admits

    _hold_subscription(ctx)

    answer = ThrottleGate(ctx).floor()
    assert not answer.admits
    assert answer.slug == _SLUG


def test_a_mark_on_another_subscription_holds_nothing_here() -> None:
    _store, _hub, ctx = _ctx()
    ctx.host_stores.usage_limits.record_usage_limit_mark("other-plan", usage_limit_mark(_NOW))

    assert ThrottleGate(ctx).floor().admits


def test_a_runner_naming_no_subscription_reads_its_own_mark() -> None:
    _store, _hub, ctx = _ctx(subscription=None)
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW))
    assert ThrottleGate(ctx).floor().admits

    ctx.stores.usage_limits.record_usage_limit_mark(usage_limit_mark(_NOW))

    answer = ThrottleGate(ctx).floor()
    assert not answer.admits
    assert answer.slug is None
    view = _throttle_view(ThrottleStatus.of(ThrottleGate(ctx).verdict(), braked=False))
    assert view.state == "declining"
    assert view.declining == ["floor"]
    assert view.floor.subscription is None
    assert "floor (runner): hold" in _throttle_lines(cast(_Throttle, view.model_dump()))[1]


def test_an_unsampled_providers_mark_opens_at_its_reset_alone() -> None:
    _store, _hub, ctx = _ctx(provider="some-unshipped-provider")
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW - timedelta(hours=1)))

    assert ThrottleGate(ctx).floor().admits


def test_a_sampled_providers_mark_past_its_reset_awaits_a_sample() -> None:
    _store, _hub, ctx = _ctx()
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW - timedelta(hours=1)))

    assert not ThrottleGate(ctx).floor().admits


def _record_sample(ctx: LoopContext, at: datetime, pct: float, resets_at: datetime) -> None:
    window = {
        "window": "five_hour",
        "utilization_pct": pct,
        "resets_at": resets_at.isoformat(),
        "window_seconds": 18000,
    }
    ctx.host_stores.external_usage.record_external_usage_attempt(
        slug=_SLUG, sampled_at=at, payload=json.dumps({"windows": [window]}), report_kind="", report_payload=""
    )


def test_a_clear_sets_aside_the_window_the_sample_standing_at_it_showed_exhausted() -> None:
    _store, _hub, ctx = _ctx()
    reset = _NOW + timedelta(hours=2)
    _record_sample(ctx, _NOW - timedelta(minutes=30), 100.0, reset)
    ctx.host_stores.usage_limits.record_floor_clear(_SLUG, FloorClear(cleared_at=_NOW - timedelta(minutes=20), by="op"))
    _record_sample(ctx, _NOW - timedelta(minutes=10), 100.0, reset)

    assert ThrottleGate(ctx).floor().admits


def test_a_window_under_the_line_at_the_clear_holds_once_exhausted_in_the_same_period() -> None:
    _store, _hub, ctx = _ctx()
    reset = _NOW + timedelta(hours=2)
    _record_sample(ctx, _NOW - timedelta(minutes=30), 95.0, reset)
    ctx.host_stores.usage_limits.record_floor_clear(_SLUG, FloorClear(cleared_at=_NOW - timedelta(minutes=20), by="op"))
    _record_sample(ctx, _NOW - timedelta(minutes=10), 100.0, reset)

    assert not ThrottleGate(ctx).floor().admits


def test_claims_report_the_brakes_refusal_without_reading_the_floor() -> None:
    _store, _hub, ctx = _ctx()
    _hold_subscription(ctx)

    claims = ThrottleGate(ctx).claims(RunnerBrakes(local=False, hub=True))

    assert claims.braked and claims.held_back
    assert claims.verdict is None and claims.declining is None


def test_claims_report_the_declining_verdict_when_no_brake_holds() -> None:
    _store, _hub, ctx = _ctx()
    _hold_subscription(ctx)

    claims = ThrottleGate(ctx).claims(RunnerBrakes(local=False, hub=False))

    assert not claims.braked and claims.held_back
    assert claims.declining is not None and not claims.declining.admits_claim


def test_claims_admit_an_unheld_runner() -> None:
    _store, _hub, ctx = _ctx()

    claims = ThrottleGate(ctx).claims(RunnerBrakes(local=False, hub=False))

    assert not claims.held_back and claims.declining is None


# --- every start asks it ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("start", [StartKind.STEP, StartKind.CONTINUATION])
def test_a_held_floor_suppresses_a_step_and_a_continuation_with_its_verdict(start: StartKind) -> None:
    _store, _hub, ctx = _ctx()
    _hold_subscription(ctx)

    with capture_logs() as logs:
        suppression = Spawner(ctx).suppression(start=start, via="test", chunk_id="ch_1")

    assert suppression is not None and suppression.cause is SuppressionCause.DECLINED
    assert suppression.verdict is not None and not suppression.verdict.floor.admits
    assert [log["via"] for log in logs if log["event"] == "spawn suppressed — throttle declining"] == ["test"]


def test_an_escalation_answers_to_the_breakers_alone() -> None:
    _store, _hub, ctx = _ctx()
    _hold_subscription(ctx)

    assert Spawner(ctx).suppression(start=StartKind.ESCALATION, via="test", chunk_id="ch_1") is None


def test_a_brake_outranks_the_floor_at_a_start() -> None:
    store, _hub, ctx = _ctx()
    _hold_subscription(ctx)
    _brake(store)

    suppression = Spawner(ctx).suppression(start=StartKind.STEP, via="test", chunk_id="ch_1")

    assert suppression is not None
    assert (suppression.cause, suppression.verdict) == (SuppressionCause.BRAKED, None)


def test_an_open_floor_suppresses_nothing() -> None:
    _store, _hub, ctx = _ctx()

    assert not Spawner(ctx).suppressed(start=StartKind.STEP, via="test", chunk_id="ch_1")


# --- FILL ------------------------------------------------------------------------------------------------------------


def _claimable(hub: FakeHub) -> None:
    hub.queue = [QueueEntry(chunk_id="ch_1", graph_id="gr_1", position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES))


def test_fill_declining_neither_peeks_nor_claims_and_logs_once_naming_the_floor() -> None:
    store, hub, ctx = _ctx()
    _claimable(hub)
    _hold_subscription(ctx)

    with capture_logs() as logs:
        Fill(ctx).run()

    assert hub.peek_queue_calls == 0
    assert hub.claims == []
    assert store.list_active_leases() == []
    declining = [log for log in logs if log["event"] == _DECLINING]
    assert len(declining) == 1
    assert (declining[0]["declining"], declining[0]["subscription"]) == (["floor"], _SLUG)


def test_fill_braked_logs_the_brake_not_the_floor() -> None:
    store, hub, ctx = _ctx()
    _claimable(hub)
    _hold_subscription(ctx)
    _brake(store)

    with capture_logs() as logs:
        Fill(ctx).run()

    assert hub.claims == []
    assert [log["event"] for log in logs if log["event"] in {_DECLINING, _PAUSED}] == [_PAUSED]


def test_fill_claims_once_the_floor_opens() -> None:
    store, hub, ctx = _ctx()
    _claimable(hub)
    _hold_subscription(ctx)
    Fill(ctx).run()
    assert hub.claims == []

    assert isinstance(ctx.clock, FixedClock)
    ctx.clock.advance(timedelta(hours=2))  # past the reset and the grace, no sample ever came
    Fill(ctx).run()

    assert len(hub.claims) == 1
    assert len(store.list_active_leases()) == 1


def test_an_interrupted_ready_binding_is_held_not_reclaimed_while_the_gate_declines() -> None:
    store, hub, ctx = _ctx()
    hub.claim_outcome = claimed_outcome("ch_1", make_envelope("ch_1", "build", node_id="nd_build", choices=_CHOICES))
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    hub.chunks["ch_1"] = ChunkState(chunk_id="ch_1", status=ChunkStatus.READY)
    _hold_subscription(ctx)

    Fill(ctx).run()

    assert hub.claims == []
    assert store.held_environment_ids() == ["e1"]
