"""Why a runner is declining — the throttles' refusals as reasons, derived by value over the gate's answer.

A reason names the throttle saying no, never a setting: the reading alone, the subscription it belongs to when it does,
and the instant the hold expects to lift. One home words it, so every surface that shows why a runner is held back says
the same thing. Nothing here is stored — the report is derived from the gate's answer on every ask."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.roles import domain_model
from blizzard.runner.throttle.floor import FloorAnswer
from blizzard.runner.throttle.gate import ClaimsAnswer
from blizzard.runner.throttle.levers import LeverAnswer

__all__ = ["FLOOR_LEVER", "DecliningReason", "declining_report", "floor_reason", "lever_reason"]

#: The lever name the subscription floor reports under.
FLOOR_LEVER = "floor"


@domain_model
@dataclass(frozen=True)
class DecliningReason:
    """One throttle saying no: its ``lever``, the ``detail`` reading alone, the ``subscription`` it concerns when it
    belongs to one, and ``until`` — the instant its hold expects to lift or be opened — when known."""

    lever: str
    detail: str
    subscription: str | None = None
    until: datetime | None = None


def declining_report(answer: ClaimsAnswer, *, now: datetime) -> tuple[DecliningReason, ...]:
    """Every throttle saying no in ``answer``, in the gate's order — empty when a brake holds the runner (its brake
    already reports itself and a throttle governs only a runner no breaker holds) or the gate admits."""
    verdict = answer.declining
    if verdict is None:
        return ()
    return tuple(
        lever_reason(answer) if isinstance(answer, LeverAnswer) else floor_reason(answer, now=now)
        for answer in verdict.declining
    )


def lever_reason(answer: LeverAnswer) -> DecliningReason:
    """The reason a declining lever gives: its name and the reading it judged, never its setting, and no subscription —
    a lever's setting is the runner's own, not the plan's state."""
    if answer.admits:
        raise ValueError("an admitting lever gives no reason")
    return DecliningReason(answer.lever.value, answer.reading, None, answer.until)


def floor_reason(floor: FloorAnswer, *, now: datetime) -> DecliningReason:
    """The reason a holding ``floor`` gives at ``now``. Before the hold's expected reset it is exhausted until that
    reset. Past it, with a sample awaited, the reset is no longer ahead and no instant is named: surfaces word an
    ``until`` as a reset, which the grace's opening is not."""
    hold = floor.hold
    if hold is None:
        raise ValueError("an admitting floor gives no reason")
    if now < hold.expected_reset:
        return DecliningReason(FLOOR_LEVER, "exhausted", floor.slug, hold.expected_reset)
    return DecliningReason(FLOOR_LEVER, "exhausted, reset passed and no sample since", floor.slug)
