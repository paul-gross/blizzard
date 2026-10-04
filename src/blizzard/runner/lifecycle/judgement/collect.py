"""What collecting an in-flight elicitation does, given what the collector observed.

Pure (``bzh:domain-orchestration-split``): :meth:`ElicitationExit.decide` takes the record,
the instant, and the observations the collector made — the process probe, the usage-limit and
overload classifiers' results, the output read back, the harness's usability verdict — and
returns the outcome. `Judgement.collect` only observes and acts on it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from blizzard.foundation.roles import domain_model
from blizzard.runner.leases.elicitation import PendingElicitation

__all__ = ["CollectOutcome", "ElicitationExit"]


class CollectOutcome(StrEnum):
    """One collect pass's outcome. ``classify`` and ``read-harness`` ask the collector for
    the next observation — the throttle classifiers, then the harness's usability verdict —
    before deciding again; the rest are final."""

    WAIT = "wait"
    CLASSIFY = "classify"
    PARK = "park"
    BACK_OFF = "back-off"
    FAIL_STALE = "fail-stale"
    RELAUNCH = "relaunch"
    READ_HARNESS = "read-harness"
    JUDGE = "judge"


@domain_model
@dataclass(frozen=True)
class ElicitationExit:
    """An in-flight elicitation observed at ``at``, its process ``alive`` or exited."""

    record: PendingElicitation
    at: datetime
    alive: bool

    def decide(
        self,
        *,
        usage_limited: bool | None = None,
        overload_backing_off: bool = False,
        output_present: bool = False,
        usable: bool | None = None,
    ) -> CollectOutcome:
        """The outcome given the observations so far — ``None`` marks one not yet made.

        A live process waits, or fails once hung past the staleness bound. An exited one is classified
        for a usage limit, then an overload backoff, ahead of the bound; under it, an exit with nothing
        usable relaunches without spending a retry, and a usable one is judged."""
        if self.alive:
            return CollectOutcome.FAIL_STALE if self.record.stale(self.at) else CollectOutcome.WAIT
        if usage_limited is None:
            return CollectOutcome.CLASSIFY
        if usage_limited:
            return CollectOutcome.PARK
        if overload_backing_off:
            return CollectOutcome.BACK_OFF
        if self.record.stale(self.at):
            return CollectOutcome.FAIL_STALE
        if not output_present:
            return CollectOutcome.RELAUNCH
        if usable is None:
            return CollectOutcome.READ_HARNESS
        return CollectOutcome.JUDGE if usable else CollectOutcome.RELAUNCH
