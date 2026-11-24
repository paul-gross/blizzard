"""A dedicated daemon thread running one pass on an interval, off any loop tick — the host for a
lane whose sink must never hold the tick (``bzh:lane-contract`` clause 5)."""

from __future__ import annotations

import random
import threading
from collections.abc import Callable

import structlog

from blizzard.foundation.clock import IMonotonicClock
from blizzard.runner.loop.wake import IntervalWait, Wait, WakeSignal

#: How long ``stop()`` waits for an in-flight pass: a hung pass must not hold shutdown.
STOP_TIMEOUT_SECONDS = 5.0


class PeriodicPassDriver:
    """Runs ``run_pass`` on its own thread, waiting ``interval()`` seconds from the end of each pass to the start of
    the next, the first after a jitter within the interval (``jitter_seconds`` pins it). ``signal`` wakes a wait to
    re-read the interval; ``clock`` and ``wait`` are what the wait is measured and spent on. A pass that raises is
    logged under ``label`` and swallowed, so one bad pass never kills the thread."""

    def __init__(
        self,
        run_pass: Callable[[], None],
        *,
        name: str,
        label: str,
        log: structlog.stdlib.BoundLogger,
        interval: Callable[[], float],
        jitter_seconds: float | None = None,
        stop_timeout_seconds: float = STOP_TIMEOUT_SECONDS,
        signal: WakeSignal | None = None,
        clock: IMonotonicClock,
        wait: Wait | None = None,
    ) -> None:
        self._run_pass = run_pass
        self._label = label
        self._log = log
        self._clock = clock
        self._waits = IntervalWait(
            interval,
            signal=signal or WakeSignal(),
            clock=self._clock,
            log=log,
            label=label,
            wait=wait,
        )
        self._jitter = random.uniform(0, self._waits.interval()) if jitter_seconds is None else jitter_seconds
        self._stop_timeout = stop_timeout_seconds
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    @property
    def thread(self) -> threading.Thread:
        return self._thread

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread and wait at most ``stop_timeout_seconds``; a pass still running is
        left to die with the process rather than hold shutdown."""
        self._waits.stop()
        self._thread.join(self._stop_timeout)
        if self._thread.is_alive():
            self._log.warning(f"{self._label} still running at shutdown; abandoned", waited=self._stop_timeout)

    def _run(self) -> None:
        jitter = self._jitter
        due = self._waits.until_due(self._clock.monotonic(), lambda: jitter)
        while due:
            try:
                self._run_pass()
            except Exception as exc:  # a bad pass must not kill the thread
                self._log.error(f"{self._label} failed", detail=str(exc))
            due = self._waits.until_due(self._clock.monotonic())
