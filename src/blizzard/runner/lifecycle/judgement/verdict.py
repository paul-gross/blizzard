"""A collected verdict, and what it decides for the attempt.

Pure (``bzh:domain-orchestration-split``): the verdict is read against the node's own choices,
so a reply naming no choice — or one the node lacks — is verdict-less before anything is
buffered for the hub."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.completion_gates import ChecksGate
from blizzard.foundation.roles import domain_model
from blizzard.runner.leases.asks import OpenAsk
from blizzard.runner.lifecycle.judgement.checks import ExecutedCheck
from blizzard.runner.node_steps.envelope import Choice

__all__ = ["Verdict", "VerdictOutcome"]


class VerdictOutcome(StrEnum):
    """``accept`` the choice; ``park-on-ask`` when a verdict-less reply holds an
    unforwarded ask; ``fail-verdictless`` without one; ``fail-red-checks`` when a
    ``requires_checks`` choice is taken over a red check."""

    ACCEPT = "accept"
    PARK_ON_ASK = "park-on-ask"
    FAIL_VERDICTLESS = "fail-verdictless"
    FAIL_RED_CHECKS = "fail-red-checks"


@domain_model
@dataclass(frozen=True)
class Verdict:
    """The choice a reply named, and the node's declared choice it names — ``None`` when the
    reply named none or one the node does not declare."""

    choice: str | None
    selected: Choice | None

    @classmethod
    def of(cls, choice: str | None, choices: Sequence[Choice]) -> Verdict:
        return cls(choice, next((c for c in choices if c.name == choice), None) if choice is not None else None)

    def without_choice(self, unforwarded_ask: OpenAsk | None) -> VerdictOutcome:
        """A verdict-less reply: the worker asked instead of choosing, so park on its
        unforwarded ask; with none, the attempt fails (spending a retry)."""
        return VerdictOutcome.FAIL_VERDICTLESS if unforwarded_ask is None else VerdictOutcome.PARK_ON_ASK

    def gated(self, checks: Sequence[ExecutedCheck]) -> VerdictOutcome:
        """A named choice against this attempt's checks: a ``requires_checks`` choice over
        a red check fails the attempt; anything else is accepted."""
        if self.selected is None:
            raise ValueError(f"verdict {self.choice!r} names no declared choice to gate")
        if ChecksGate(self.selected.requires_checks, checks).violated:
            return VerdictOutcome.FAIL_RED_CHECKS
        return VerdictOutcome.ACCEPT
