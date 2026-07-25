"""The check-runner seam — the runner executes a node's ``checks:`` at worker exit (issue #114).

A node may declare ``checks:`` (deterministic commands — lint, tests). Since #114 the
runner runs them at worker exit, before the judgement is elicited, records each result
as a durable fact, injects the results into the judgement prompt, and lets a
``requires_checks`` choice gate on them. Running a check is deterministic-shell work
(``bzh:deterministic-shell`` — no model call), reached only through this injected seam
(``bzh:pluggable-seams``): the reference binding is the subprocess adapter under
``internal/``, and loop tests inject a fake. The seam mirrors the read-only git seam
(:class:`~blizzard.runner.loop.worktree.IWorktreeGit`) in shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# The per-check timeout a node applies when it authors no ``checks_timeout`` (issue #114).
# A timeout is a red check — a hung check must not wedge the tick forever.
DEFAULT_CHECK_TIMEOUT: int = 600


@dataclass(frozen=True)
class CheckOutcome:
    """One check command's runner-executed outcome.

    ``passed`` is the pass/fail signal (exit 0 ⇒ passed; non-zero **and a timeout** ⇒
    failed). ``output_tail`` is a bounded tail of the command's combined output — the
    durable evidence the judgement-prompt injection renders and the crash-recovery read
    hands back. It is deliberately runner-local (see the store's ``check_results`` table);
    only ``passed`` and ``command`` ride the wire to the hub's gate (issue #114 [MF3])."""

    passed: bool
    output_tail: str


class ICheckRunner(Protocol):
    """Run one deterministic check command in a leased worktree, read-only to the loop."""

    def run(self, command: str, cwd: str, timeout: int) -> CheckOutcome:
        """Run ``command`` in ``cwd`` under a ``timeout`` (seconds), returning its
        pass/fail and a bounded output tail. A non-zero exit is a red check; a timeout is
        a red check too (never a raise — a hung check cannot be allowed to wedge the
        tick). The child environment is built from the worker-env allowlist
        (``bzh:worker-env-allowlist``), never a daemon-secret-carrying ``os.environ`` copy."""
        ...
