"""The selftest job resource's in-memory service — the adapter-drift canary (issue #54).

Mints and runs a selftest against a chosen coding harness off the request thread, in a
throwaway scratch repo the ``IScratchGit`` seam owns. Run *state* stays process-local and
gone on daemon restart, same as ever; a run's *terminal outcome* also lands as a durable
per-harness fact (blizzard#438) when a result repository is wired, so daemon-start health
recalculation can see the last completed run across a restart.
"""

from __future__ import annotations

import threading
from dataclasses import replace

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import SELFTEST_PREFIX, Id
from blizzard.runner.domain.selftest_result import IWriteSelfTestResultRepository, SelfTestCheckRecord
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.registry import IHarnessRegistry, UnknownHarnessError
from blizzard.runner.loop.process import IProcessProbe
from blizzard.runner.selftest.checks import SelfTest
from blizzard.runner.selftest.model import SelfTestCheck, SelfTestRun, SelfTestStatus
from blizzard.runner.selftest.scratch_git import IScratchGit

# The whole-run wall-clock budget (issue #54): a hung check must fail the canary loudly
# rather than wedge it silently.
_DEFAULT_RUN_BUDGET_SECONDS = 300.0

__all__ = ["SelfTestService", "UnknownHarnessError"]


class SelfTestService:
    """Mint selftest runs and execute them off the request thread.

    A ``harness`` outside the injected ``harnesses`` registry raises
    :class:`UnknownHarnessError` — a client error, never a missing resource."""

    def __init__(
        self,
        *,
        harnesses: IHarnessRegistry,
        scratch_git: IScratchGit,
        process: IProcessProbe,
        clock: IClock,
        run_budget_seconds: float = _DEFAULT_RUN_BUDGET_SECONDS,
        results: IWriteSelfTestResultRepository | None = None,
    ) -> None:
        self._harnesses = harnesses
        self._scratch_git = scratch_git
        self._process = process
        self._clock = clock
        self._run_budget_seconds = run_budget_seconds
        self._results = results
        self._lock = threading.Lock()
        self._runs: dict[str, SelfTestRun] = {}

    @property
    def known_harnesses(self) -> tuple[str, ...]:
        return self._harnesses.known_harnesses

    def start(self, harness: str) -> SelfTestRun:
        """Mint a run and begin it in a background thread; returns immediately."""
        adapter = self._harnesses.adapter(harness)
        run = SelfTestRun(id=Id.mint(SELFTEST_PREFIX, self._clock).value, harness=harness)
        with self._lock:
            self._runs[run.id] = run
        threading.Thread(target=self._execute, args=(run.id, adapter), daemon=True).start()
        return run

    def get(self, selftest_id: str) -> SelfTestRun | None:
        """Read back a run's current state — a snapshot, not the live mutable object.

        ``_finish`` reassigns the run's fields as a whole under the lock, so a caller
        reading outside the lock must not be handed the live instance.
        """
        with self._lock:
            run = self._runs.get(selftest_id)
            if run is None:
                return None
            return replace(run, checks=list(run.checks))

    def _execute(self, selftest_id: str, adapter: IHarnessAdapter) -> None:
        # Joined against the budget in its own thread: an overrun cannot be killed, so it
        # is abandoned as a daemon thread and the run resolves anyway (issue #54).
        outcome: list[tuple[list[SelfTestCheck], str | None]] = []

        def _run() -> None:
            try:
                checks = SelfTest(adapter, self._scratch_git, self._process).run()
            except Exception as exc:  # a checks-runner bug must still resolve the job, never wedge it
                outcome.append(([], str(exc)))
                return
            outcome.append((checks, None))

        worker = threading.Thread(target=_run, daemon=True)
        worker.start()
        worker.join(self._run_budget_seconds)
        if worker.is_alive():
            detail = f"selftest exceeded its {self._run_budget_seconds:g}s wall-clock budget — the harness appears hung"
            self._finish(selftest_id, status="failed", checks=[], error=detail)
            return

        checks, error = outcome[0]
        if error is not None:
            self._finish(selftest_id, status="failed", checks=[], error=error)
            return
        status: SelfTestStatus = "passed" if all(c.passed for c in checks) else "failed"
        self._finish(selftest_id, status=status, checks=checks, error=None)

    def _finish(
        self, selftest_id: str, *, status: SelfTestStatus, checks: list[SelfTestCheck], error: str | None
    ) -> None:
        with self._lock:
            run = self._runs[selftest_id]
            run.checks = checks
            run.status = status
            run.error = error
            harness = run.harness
        if self._results is not None:
            self._results.record_selftest_result(
                harness_id=harness,
                status=status,
                error=error,
                checks=tuple(SelfTestCheckRecord(name=c.name, passed=c.passed, detail=c.detail) for c in checks),
                recorded_at=self._clock.now(),
            )
