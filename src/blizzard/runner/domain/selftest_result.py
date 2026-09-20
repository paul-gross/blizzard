"""Durable selftest results (blizzard#438): the harness-health evaluator's own evidence of
each harness's most recently completed selftest run — its terminal status and when it was
recorded. Latest-wins-per-``harness_id`` (``bzh:facts-not-status``): a completed run is a
definite occurrence at a definite time, superseded only by the next run for the same
harness. :meth:`~blizzard.runner.selftest.service.SelfTestService._finish` is the one write
site; the run's own per-check detail lives only in its in-memory ``SelfTestRun`` — nothing
durable reads it back, so it rides no further than that."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = [
    "IReadSelfTestResultRepository",
    "IWriteSelfTestResultRepository",
    "SelfTestResultRecord",
]


@dataclass(frozen=True)
class SelfTestResultRecord:
    """A harness's most recently completed selftest run. ``status`` is one of
    :data:`~blizzard.runner.selftest.model.SelfTestStatus`'s terminal values
    (``"passed"``/``"failed"``) — a still-``"running"`` run is never recorded."""

    harness_id: str
    status: str
    error: str | None
    recorded_at: datetime


class IReadSelfTestResultRepository(Protocol):
    """Read-only selftest-result queries (held by the harness-health evaluator's own
    evidence-gathering seam)."""

    def latest_selftest_result(self, harness_id: str) -> SelfTestResultRecord | None:
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
        status: str,
        error: str | None,
        recorded_at: datetime,
    ) -> None:
        """Durably record ``harness_id``'s just-completed run. Append-only,
        latest-wins-per-``harness_id``: a later call for the same harness is a fresh
        occurrence, read back as the replacement, never merged with the one it supersedes."""
        ...
