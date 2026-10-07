"""The check-runner seam — runs one declared check command.

Running a declared check is deterministic-shell work (``bzh:deterministic-shell`` — no
model call), reached only through this injected seam (``bzh:pluggable-seams``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model

# The per-check timeout a node applies when it authors no ``checks_timeout``.
# A timeout is a red check — a hung check must not wedge the tick forever.
DEFAULT_CHECK_TIMEOUT: int = 600


@domain_model
@dataclass(frozen=True)
class CheckOutcome:
    """One check command's runner-executed outcome.

    Exit 0 ⇒ passed; non-zero **and a timeout** ⇒ failed. ``output_tail`` is a bounded
    tail of the combined output, kept runner-local."""

    passed: bool
    output_tail: str


class ICheckRunner(Protocol):
    """Run one deterministic check command in a worktree."""

    def run(self, command: str, cwd: str, timeout: int) -> CheckOutcome:
        """Run ``command`` in ``cwd`` under a ``timeout`` (seconds), returning its
        pass/fail and a bounded output tail. A non-zero exit or a timeout is a failed
        outcome, never a raise. Child environment: ``bzh:worker-env-allowlist``."""
        ...
