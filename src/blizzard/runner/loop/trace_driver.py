"""The lease-trace sweep's own daemon thread in ``runner host`` — never the tick's, never ``runner tick``'s.

Contract: ``blizzard-product:/delivered/tracing/runner-spans/spec/emission.md`` §Where it runs."""

from __future__ import annotations

import random
import threading
from typing import Protocol

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.trace_export")

#: How long ``stop()`` waits for an in-flight pass: a hung export must not hold shutdown.
STOP_TIMEOUT_SECONDS = 5.0


class ISweep(Protocol):
    def sweep(self) -> None: ...


class TraceSweepDriver:
    """Runs one sweep pass every ``interval_seconds``, the first after a jitter within that interval.

    A pass that raises is logged and swallowed so one bad pass never kills the thread."""

    def __init__(
        self,
        sweep: ISweep,
        *,
        interval_seconds: float,
        jitter_seconds: float | None = None,
        stop_timeout_seconds: float = STOP_TIMEOUT_SECONDS,
    ) -> None:
        self._sweep = sweep
        self._interval = interval_seconds
        self._jitter = random.uniform(0, interval_seconds) if jitter_seconds is None else jitter_seconds
        self._stop_timeout = stop_timeout_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="blizzard-runner-trace-sweep", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread and wait at most ``stop_timeout_seconds``; a pass still exporting
        is left to die with the process rather than hold the engine's disposal."""
        self._stop.set()
        self._thread.join(self._stop_timeout)
        if self._thread.is_alive():
            _log.warning("lease trace sweep still running at shutdown; abandoned", waited=self._stop_timeout)

    def _run(self) -> None:
        wait = self._jitter
        while not self._stop.wait(wait):
            try:
                self._sweep.sweep()
            except Exception as exc:  # a bad pass must not kill the thread
                _log.error("lease trace sweep failed", detail=str(exc))
            wait = self._interval
