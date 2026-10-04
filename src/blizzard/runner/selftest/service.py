"""The selftest job resource's in-memory service — the adapter-drift canary.

Mints and runs a selftest against a chosen coding harness off the request thread, in a
throwaway scratch repo the ``IScratchGit`` seam owns. Run *state* stays process-local, gone
on restart; a run's *terminal outcome* also lands as a durable per-harness fact
when a result repository is wired."""

from __future__ import annotations

import threading

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import SELFTEST_PREFIX, Id
from blizzard.runner.harness.adapter import IHarnessSelfTestSeam
from blizzard.runner.harness.registry import IHarnessRegistry, UnknownHarnessError
from blizzard.runner.harness.selftest_result import IWriteSelfTestResultRepository
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.selftest.checks import SelfTest
from blizzard.runner.selftest.model import SelfTestCheck, SelfTestRun
from blizzard.runner.selftest.scratch_git import IScratchGit

# The whole-run wall-clock budget: a hung check must fail the canary loudly
# rather than wedge it silently.
_DEFAULT_RUN_BUDGET_SECONDS = 300.0

__all__ = ["SelfTestService", "UnknownHarnessError"]


class SelfTestService:
    """Mint selftest runs and execute them off the request thread.

    A ``harness`` outside the injected ``harnesses`` registry raises :class:`UnknownHarnessError`, a
    client error. Concurrent runs for one harness are legal; the last to finish records the result."""

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

    def start(self, harness: str) -> SelfTestRun:
        """Mint a run and begin it in a background thread; returns immediately."""
        adapter = self._harnesses.self_test(harness)
        run = SelfTestRun(id=Id.mint(SELFTEST_PREFIX, self._clock).value, harness=harness)
        with self._lock:
            self._runs[run.id] = run
        threading.Thread(target=self._execute, args=(run.id, adapter), daemon=True).start()
        return run

    def get(self, selftest_id: str) -> SelfTestRun | None:
        """Read back a run's current state — an immutable snapshot, replaced whole on conclusion."""
        with self._lock:
            return self._runs.get(selftest_id)

    def _execute(self, selftest_id: str, adapter: IHarnessSelfTestSeam) -> None:
        # Joined against the budget in its own thread: an overrun cannot be killed, so it
        # is abandoned as a daemon thread and the run resolves anyway.
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
        with self._lock:
            run = self._runs[selftest_id]
        if worker.is_alive():
            self._finish(run.overran(self._run_budget_seconds))
            return
        checks, error = outcome[0]
        self._finish(run.crashed(error) if error is not None else run.conclude(checks))

    def _finish(self, concluded: SelfTestRun) -> None:
        # Durable before visible: a caller that reads the run as terminal can rely on its
        # outcome already being recorded. A failed write still resolves the run.
        try:
            if self._results is not None:
                record = concluded.result_record(self._clock.now())
                self._results.record_selftest_result(
                    harness_id=record.harness_id,
                    status=record.status,
                    error=record.error,
                    recorded_at=record.recorded_at,
                )
        finally:
            with self._lock:
                self._runs[concluded.id] = concluded
