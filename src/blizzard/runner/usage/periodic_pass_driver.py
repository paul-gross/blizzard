"""A dedicated daemon thread running one pass on an interval, off any loop tick — the host for a
lane whose sink must never hold the tick (``bzh:lane-contract`` clause 5)."""

from __future__ import annotations

import random
import threading
from collections.abc import Callable

import structlog

#: How long ``stop()`` waits for an in-flight pass: a hung pass must not hold shutdown.
STOP_TIMEOUT_SECONDS = 5.0


class PeriodicPassDriver:
    """Runs ``run_pass`` every ``interval_seconds`` on its own thread, the first after a jitter
    within that interval (``jitter_seconds`` pins it). A pass that raises is logged under
    ``label`` and swallowed, so one bad pass never kills the thread."""

    def __init__(
        self,
        run_pass: Callable[[], None],
        *,
        name: str,
        label: str,
        log: structlog.stdlib.BoundLogger,
        interval_seconds: float,
        jitter_seconds: float | None = None,
        stop_timeout_seconds: float = STOP_TIMEOUT_SECONDS,
    ) -> None:
        self._run_pass = run_pass
        self._label = label
        self._log = log
        self._interval = interval_seconds
        self._jitter = random.uniform(0, interval_seconds) if jitter_seconds is None else jitter_seconds
        self._stop_timeout = stop_timeout_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread and wait at most ``stop_timeout_seconds``; a pass still running is
        left to die with the process rather than hold shutdown."""
        self._stop.set()
        self._thread.join(self._stop_timeout)
        if self._thread.is_alive():
            self._log.warning(f"{self._label} still running at shutdown; abandoned", waited=self._stop_timeout)

    def _run(self) -> None:
        wait = self._jitter
        while not self._stop.wait(wait):
            try:
                self._run_pass()
            except Exception as exc:  # a bad pass must not kill the thread
                self._log.error(f"{self._label} failed", detail=str(exc))
            wait = self._interval
