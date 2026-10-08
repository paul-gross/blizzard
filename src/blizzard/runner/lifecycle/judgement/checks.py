"""The check-result and produces-nudge repository seam, and the plan a node's ``checks:``
run under."""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.runner.lifecycle.judgement.check_runner import DEFAULT_CHECK_TIMEOUT
from blizzard.runner.node_steps.envelope import EnvelopeNode

__all__ = ["CheckPlan", "ExecutedCheck", "IReadCheckRepository", "IWriteCheckRepository"]


@domain_model
@dataclass(frozen=True)
class CheckPlan:
    """How a node's ``checks:`` run at worker exit: its commands, in order, from ``cwd``
    under a per-check ``timeout`` (seconds). A node that declares no checks runs none."""

    commands: tuple[str, ...]
    cwd: str
    timeout: int

    @classmethod
    def of(cls, node: EnvelopeNode, workdir: str) -> CheckPlan:
        """The plan for ``node`` in the leased worktree ``workdir``: ``checks_cwd`` joined
        onto it when the node names one, and the node's ``checks_timeout`` or the default."""
        return cls(
            commands=tuple(node.checks),
            cwd=posixpath.join(workdir, node.checks_cwd) if node.checks_cwd else workdir,
            timeout=node.checks_timeout or DEFAULT_CHECK_TIMEOUT,
        )


@domain_model
@dataclass(frozen=True)
class ExecutedCheck:
    """One check command's runner-executed outcome, read back from the durable store.
    ``output_tail`` is runner-local evidence and never rides the wire."""

    command: str
    passed: bool
    output_tail: str


class IReadCheckRepository(Protocol):
    """Read-only check/nudge queries."""

    def nudge_fired(self, lease_id: str, epoch: int) -> bool:
        """``True`` iff this attempt's `produces`-unmet nudge is already spent. Written by
        :meth:`~IWriteCheckRepository.record_nudge_fired` *before* the nudge resume runs, so a
        crash between the two still reads ``True``."""
        ...

    def checks_ran(self, lease_id: str, epoch: int) -> bool:
        """``True`` iff this attempt's ``checks:`` have already run and their results are
        durable. Written *after* the result rows, so ``True`` implies the
        rows exist (``runner:checks-recorded-when-marked``); a crash between them leaves
        this ``False``, which safely re-runs."""
        ...

    def check_results_for_lease(self, lease_id: str, epoch: int) -> list[ExecutedCheck]:
        """This attempt's recorded check results, in run order. Empty for an
        attempt whose checks never ran (or a node with no ``checks:``)."""
        ...


class IWriteCheckRepository(IReadCheckRepository, Protocol):
    """Read-write check/nudge store — held only by the domain."""

    def record_nudge_fired(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        """Durably spend this attempt's one `produces`-unmet nudge.
        Idempotent by its own check-then-insert, not a DB constraint
        (``bzh:sql-portable``), mirroring :meth:`record_usage`. Called *before* the
        resume that delivers the nudge — the ordering rationale lives at the call site
        in ``src/blizzard/runner/loop/steps.py``."""
        ...

    def record_check_results(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        node_id: str,
        epoch: int,
        results: list[ExecutedCheck],
        at: datetime,
    ) -> None:
        """Append this attempt's check result rows, one committed transaction
        so they survive a ``kill -9`` between the run and the marker that follows. Written
        BEFORE :meth:`record_checks_ran` so a marker never precedes its rows
        (``runner:checks-recorded-when-marked``). Re-run-safe: a recovery that finds
        :meth:`checks_ran` unset re-runs and re-records, latest-wins."""
        ...

    def record_checks_ran(self, *, lease_id: str, epoch: int, at: datetime) -> None:
        """Durably mark this attempt's ``checks:`` as run — the guard
        :meth:`~IReadCheckRepository.checks_ran` reads. Written AFTER
        :meth:`record_check_results` and only for a node with a non-empty ``checks:``, so
        the marker implies its result rows exist. Idempotent by its own check-then-insert
        (``bzh:sql-portable``), mirroring :meth:`record_nudge_fired`."""
        ...
