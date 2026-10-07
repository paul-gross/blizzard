"""Where an exited worker's node-step goes next, before any verdict is elicited.

Pure (``bzh:domain-orchestration-split``): :func:`route_exit` takes what the judgement already
loaded and returns the route, so `Judgement` only loads, applies the local spawn brake, and
acts on it."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.leases.model import Lease

__all__ = ["ExitEntry", "ExitNotJudgeable", "ExitRoute", "JudgeableExit", "route_exit"]


class ExitNotJudgeable(Exception):
    """The lease never recorded a session, so there is nothing to elicit a verdict from —
    the lease is left to REAP."""

    def __init__(self, lease_id: str) -> None:
        super().__init__(f"lease {lease_id} has no recorded session to judge")
        self.lease_id = lease_id


@domain_model
@dataclass(frozen=True)
class JudgeableExit:
    """An exited worker's lease and the session its verdict is elicited from."""

    lease: Lease
    session: SessionReference

    @classmethod
    def of(cls, lease: Lease) -> JudgeableExit:
        """Refuse a lease with no recorded session: judging it would record an elicitation
        with no process behind it."""
        session = lease.session
        if session is None:
            raise ExitNotJudgeable(lease.lease_id)
        return cls(lease, session)


class ExitEntry(StrEnum):
    """How judgement is entered: a worker's ``exit`` seen for the first time, or a
    ``judge-resume`` — a verdict elicitation relaunched after a usage-limit park or a
    provider-overload backoff, whose exit already passed the gate and the nudge."""

    EXIT = "exit"
    JUDGE_RESUME = "judge-resume"


class ExitRoute(StrEnum):
    """Where an entry goes: buffer a human's ``decision`` for a gated node, ``nudge`` a
    premature exit back to work, ``elicit`` the verdict, or first ``reconcile-produces`` —
    load which ``produces:`` names are unmet and whether the nudge is spent, then route again."""

    BUFFER_DECISION = "buffer-decision"
    RECONCILE_PRODUCES = "reconcile-produces"
    NUDGE = "nudge"
    ELICIT = "elicit"


def route_exit(
    entry: ExitEntry, *, gated: bool, produces_unmet: bool | None = None, nudge_spent: bool = False
) -> ExitRoute:
    """The route for ``entry``: a judge-resume elicits and a gated exit buffers its decision; any other
    exit reconciles produces while ``produces_unmet`` is ``None`` (not loaded), nudges while produces are
    unmet and the nudge unspent, and elicits otherwise. The local spawn brake sits between the gate and
    the nudge, outside this rule."""
    if entry is ExitEntry.JUDGE_RESUME:
        return ExitRoute.ELICIT
    if gated:
        return ExitRoute.BUFFER_DECISION
    if produces_unmet is None:
        return ExitRoute.RECONCILE_PRODUCES
    if produces_unmet and not nudge_spent:
        return ExitRoute.NUDGE
    return ExitRoute.ELICIT
