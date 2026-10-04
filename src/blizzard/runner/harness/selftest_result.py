"""Durable selftest results: the harness-health evaluator's own evidence of
each harness's most recently completed selftest run — its terminal status and when it was
recorded. Latest-wins-per-``harness_id`` (``bzh:facts-not-status``): a completed run is a
definite occurrence at a definite time, superseded only by the next run for the same
harness. :meth:`~blizzard.runner.selftest.model.SelfTestRun.result_record` builds the one record
written; the run's own per-check detail lives only in its in-memory ``SelfTestRun`` — nothing
durable reads it back, so it rides no further than that."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from blizzard.foundation.roles import domain_model

__all__ = [
    "IReadSelfTestResultRepository",
    "IWriteSelfTestResultRepository",
    "LatestSelfTestResult",
    "SelfTestTerminalStatus",
    "selftest_failed",
]

#: A concluded selftest run's status — the only statuses ever recorded; a still-``"running"`` run never is.
SelfTestTerminalStatus = Literal["passed", "failed"]


@domain_model
@dataclass(frozen=True)
class LatestSelfTestResult:
    """A harness's most recently completed selftest run, with its terminal status."""

    harness_id: str
    status: SelfTestTerminalStatus
    error: str | None
    recorded_at: datetime

    def failed(self) -> bool:
        """Whether this run withholds the harness's availability: only a recorded failure
        does, and it keeps withholding — across a harness version change too — until a later
        passing run supersedes it."""
        return self.status == "failed"


def selftest_failed(latest: LatestSelfTestResult | None) -> bool | None:
    """The health evaluator's selftest evidence: ``None`` when the harness never completed a
    run on this runner (unresolved, never itself a failure), else :meth:`LatestSelfTestResult.failed`."""
    return latest.failed() if latest is not None else None


class IReadSelfTestResultRepository(Protocol):
    """Read-only selftest-result queries (held by the harness-health evaluator's own
    evidence-gathering seam)."""

    def latest_selftest_result(self, harness_id: str) -> LatestSelfTestResult | None:
        """``harness_id``'s most recently recorded selftest result, or ``None`` when it has
        never completed one on this runner — unresolved, never itself a failure
        (:mod:`blizzard.runner.harness.health`'s own evaluator draws that distinction)."""
        ...


class IWriteSelfTestResultRepository(IReadSelfTestResultRepository, Protocol):
    """Read-write selftest-result store — held only by
    :class:`~blizzard.runner.selftest.service.SelfTestService`."""

    def record_selftest_result(
        self,
        *,
        harness_id: str,
        status: SelfTestTerminalStatus,
        error: str | None,
        recorded_at: datetime,
    ) -> None:
        """Durably record ``harness_id``'s just-completed run. Append-only,
        latest-wins-per-``harness_id``: a later call for the same harness is a fresh
        occurrence, read back as the replacement, never merged with the one it supersedes."""
        ...
