"""Runner brake and overload-streak rules (unit tier, by value): the local brake's legal verbs
per state, what the two brakes stop between them, the pause park, the engagement reasons, and the
provider-overload streak and its backoff facts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.runner.leases.overload import BACKOFF_LIMIT, OverloadExit, OverloadStreak
from blizzard.runner.throttle.pause import (
    BRAKE_TRANSITIONS,
    BrakeState,
    BrakeVerb,
    LocalBrake,
    LocalPauseFact,
    RunnerBrakes,
    needs_pause_park,
    spend_ceiling_reason,
    usage_limit_reason,
)
from blizzard.wire.facts import RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED

pytestmark = pytest.mark.unit

_AT = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
_RELEASED = LocalBrake(paused=False)
_ENGAGED = LocalBrake(paused=True)
_REASONED = LocalBrake(paused=True, reason="usage limit: claude-code")


def test_engage_released_returns_reasoned_fact() -> None:
    fact = _RELEASED.engage(by="usage-limit", reason="usage limit: claude-code", at=_AT)
    assert fact == LocalPauseFact(paused=True, by="usage-limit", at=_AT, reason="usage limit: claude-code")
    assert fact is not None
    assert fact.report_kind == RUNNER_LOCALLY_PAUSED


@pytest.mark.parametrize("brake", [_ENGAGED, _REASONED])
def test_engage_engaged_returns_none_keeping_reason(brake: LocalBrake) -> None:
    assert brake.engage(by="runner-ceiling", reason="spend ceiling", at=_AT) is None


def test_operator_pause_over_reasoned_brake_writes_nothing() -> None:
    assert _REASONED.set_by_operator(paused=True, by="alice", at=_AT) is None


def test_repause_writes_nothing() -> None:
    assert _ENGAGED.set_by_operator(paused=True, by="alice", at=_AT) is None


def test_start_when_released_writes_nothing() -> None:
    assert _RELEASED.set_by_operator(paused=False, by="alice", at=_AT) is None


def test_pause_released_returns_paused_fact() -> None:
    fact = _RELEASED.set_by_operator(paused=True, by="alice", at=_AT)
    assert fact == LocalPauseFact(paused=True, by="alice", at=_AT, reason=None)
    assert fact is not None
    assert fact.report_kind == RUNNER_LOCALLY_PAUSED


@pytest.mark.parametrize("brake", [_ENGAGED, _REASONED])
def test_start_engaged_returns_resumed_fact(brake: LocalBrake) -> None:
    fact = brake.set_by_operator(paused=False, by="alice", at=_AT)
    assert fact == LocalPauseFact(paused=False, by="alice", at=_AT, reason=None)
    assert fact is not None
    assert fact.report_kind == RUNNER_LOCALLY_RESUMED


def test_brake_has_no_self_lift() -> None:
    """Only the operator's start clears the brake: no self verb exists, and engagement never
    yields a resumed fact from any state."""
    assert set(BrakeVerb) == {BrakeVerb.OPERATOR_PAUSE, BrakeVerb.OPERATOR_START, BrakeVerb.SELF_ENGAGE}
    for brake in (_RELEASED, _ENGAGED, _REASONED):
        fact = brake.engage(by="usage-limit", reason="r", at=_AT)
        assert fact is None or fact.paused


def test_every_state_declares_every_verb() -> None:
    assert set(BRAKE_TRANSITIONS) == set(BrakeState)
    for verbs in BRAKE_TRANSITIONS.values():
        assert set(verbs) == set(BrakeVerb)


def test_brake_state_reads_the_reason_only_while_engaged() -> None:
    assert LocalBrake(paused=False, reason="stale").state is BrakeState.RELEASED
    assert _ENGAGED.state is BrakeState.ENGAGED
    assert _REASONED.state is BrakeState.ENGAGED_REASONED


@pytest.mark.parametrize(("local", "hub"), [(True, False), (False, True), (True, True)])
def test_either_brake_blocks_claims(local: bool, hub: bool) -> None:
    assert RunnerBrakes(local=local, hub=hub).blocks_claims
    assert RunnerBrakes(local=local, hub=hub).effective


def test_no_brake_blocks_nothing() -> None:
    brakes = RunnerBrakes(local=False, hub=False)
    assert not brakes.blocks_claims
    assert not brakes.effective
    assert brakes.starts_processes


def test_only_the_local_brake_stops_process_starts() -> None:
    assert not RunnerBrakes(local=True, hub=False).starts_processes
    assert RunnerBrakes(local=False, hub=True).starts_processes


def test_parked_lease_is_not_reparked() -> None:
    assert not needs_pause_park("lease-1", {"lease-1"})
    assert needs_pause_park("lease-2", {"lease-1"})


def test_spend_ceiling_reason_names_partial() -> None:
    assert (
        spend_ceiling_reason(cap=10.0, window_hours=24.0, spend=12.5, partial=True)
        == "spend ceiling $10.00 reached over the trailing 24h (spend $12.50 (PARTIAL — true spend may be higher))"
    )
    assert (
        spend_ceiling_reason(cap=10.0, window_hours=1.5, spend=10.0, partial=False)
        == "spend ceiling $10.00 reached over the trailing 1.5h (spend $10.00)"
    )


def test_usage_limit_reason_minute_precision() -> None:
    resets_at = datetime(2026, 10, 4, 17, 42, 59, tzinfo=UTC)
    assert usage_limit_reason("claude-code", resets_at) == "usage limit: claude-code (resets 2026-10-04T17:42Z)"
    assert usage_limit_reason("claude-code", None) == "usage limit: claude-code"


def _next(streak: OverloadStreak, *, identity: str = "3") -> OverloadExit:
    return streak.next_fact(
        lease_id="lease-1",
        chunk_id="chunk-1",
        epoch=2,
        generation=3,
        invocation_kind="worker",
        invocation_identity=identity,
        at=_AT,
    )


def test_ordinal_below_limit_backs_off_60s() -> None:
    fact = _next(OverloadStreak(0))
    assert fact.streak_ordinal == 1
    assert fact.backing_off
    assert fact.resume_after == _AT + timedelta(seconds=60)
    assert _next(OverloadStreak(3)).resume_after == _AT + timedelta(seconds=480)


def test_fifth_consecutive_overload_falls_through() -> None:
    fact = _next(OverloadStreak(BACKOFF_LIMIT - 1))
    assert fact.streak_ordinal == BACKOFF_LIMIT
    assert not fact.backing_off
    assert fact.resume_after is None


def test_rerecorded_identity_keeps_its_ordinal() -> None:
    standing = _next(OverloadStreak(3))
    candidate = _next(OverloadStreak(4))
    settled = candidate.settled(standing)
    assert settled.streak_ordinal == 4
    assert settled.backing_off
    assert candidate.settled(None) == candidate


def test_streak_open_only_above_zero() -> None:
    assert not OverloadStreak(0).open
    assert OverloadStreak(1).open


def test_worker_fact_open_only_at_its_generation() -> None:
    fact = _next(OverloadStreak(0), identity="3")
    assert fact.still_open(generation=3, elicitation_launched_at=None)
    assert not fact.still_open(generation=4, elicitation_launched_at=None)
    assert not fact.still_open(generation=None, elicitation_launched_at=None)


def test_judge_fact_open_only_while_launch_matches() -> None:
    launched = datetime(2026, 10, 4, 11, 59, tzinfo=UTC)
    fact = OverloadStreak(0).next_fact(
        lease_id="lease-1",
        chunk_id="chunk-1",
        epoch=2,
        generation=3,
        invocation_kind="judge",
        invocation_identity=launched.isoformat(),
        at=_AT,
    )
    assert fact.still_open(generation=3, elicitation_launched_at=launched)
    assert not fact.still_open(generation=3, elicitation_launched_at=launched + timedelta(seconds=1))
    assert not fact.still_open(generation=3, elicitation_launched_at=None)


def test_due_at_resume_after() -> None:
    fact = _next(OverloadStreak(0))
    assert fact.resume_after is not None
    assert not fact.due(fact.resume_after - timedelta(microseconds=1))
    assert fact.due(fact.resume_after)


def test_fallen_through_fact_never_due() -> None:
    fact = _next(OverloadStreak(BACKOFF_LIMIT - 1))
    assert not fact.due(_AT + timedelta(days=365))
