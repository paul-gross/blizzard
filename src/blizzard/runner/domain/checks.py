"""The check-result and produces-nudge repository seam (blizzard#410)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

__all__ = ["CheckResultRecord", "IReadCheckRepository", "IWriteCheckRepository"]


@dataclass(frozen=True)
class CheckResultRecord:
    """One check command's runner-executed outcome, read back from the durable store
    (issue #114). ``output_tail`` is runner-local evidence and never rides the wire."""

    command: str
    passed: bool
    output_tail: str


class IReadCheckRepository(Protocol):
    """Read-only check/nudge queries (held by read-path edges)."""

    def nudge_fired(self, lease_id: str, epoch: int) -> bool:
        """``True`` iff this attempt's `produces`-unmet nudge is already spent
        (issue #113, Phase 4) — the durable guard consulted before resuming a worker
        session to nudge it. Written by :meth:`~IWriteCheckRepository.record_nudge_fired`
        *before* that resume runs, so a crash between the two still leaves this reading
        ``True`` on the next pass."""
        ...

    def checks_ran(self, lease_id: str, epoch: int) -> bool:
        """``True`` iff this attempt's ``checks:`` have already run and their results are
        durable (issue #114). Written *after* the result rows, so ``True`` implies the
        rows exist (``runner:checks-recorded-when-marked``); a crash between them leaves
        this ``False``, which safely re-runs."""
        ...

    def check_results_for_lease(self, lease_id: str, epoch: int) -> list[CheckResultRecord]:
        """This attempt's recorded check results, in run order (issue #114). Empty for an
        attempt whose checks never ran (or a node with no ``checks:``)."""
        ...


class IWriteCheckRepository(IReadCheckRepository, Protocol):
    """Read-write check/nudge store — held only by the domain."""

    def record_nudge_fired(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        """Durably spend this attempt's one `produces`-unmet nudge (issue #113,
        Phase 4). Idempotent by its own check-then-insert, not a DB constraint
        (``bzh:sql-portable``), mirroring :meth:`record_usage`. Called *before* the
        resume that delivers the nudge — the ordering rationale lives at the call site
        in ``runner/loop/steps.py``."""
        ...

    def record_check_results(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        results: list[CheckResultRecord],
        at: datetime,
    ) -> None:
        """Append this attempt's check result rows (issue #114), one committed transaction
        so they survive a ``kill -9`` between the run and the marker that follows. Written
        BEFORE :meth:`record_checks_ran` so a marker never precedes its rows
        (``runner:checks-recorded-when-marked``). Re-run-safe: a recovery that finds
        :meth:`checks_ran` unset re-runs and re-records, latest-wins."""
        ...

    def record_checks_ran(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        """Durably mark this attempt's ``checks:`` as run (issue #114) — the guard
        :meth:`~IReadCheckRepository.checks_ran` reads. Written AFTER
        :meth:`record_check_results` and only for a node with a non-empty ``checks:``, so
        the marker implies its result rows exist. Idempotent by its own check-then-insert
        (``bzh:sql-portable``), mirroring :meth:`record_nudge_fired`."""
        ...
