from __future__ import annotations

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

import structlog

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.foundation.sweep_passes import SweepPassOutcome


class TickInProgress(Exception):
    pass


class StepLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    @contextmanager
    def hold(self) -> Generator[None, None, None]:
        if not self._lock.acquire(blocking=False):
            raise TickInProgress
        try:
            yield
        finally:
            self._lock.release()

    def wait_idle(self) -> None:
        with self._lock:
            pass


@domain_model
@dataclass(frozen=True)
class PassResult:
    started_at: datetime
    finished_at: datetime
    outcome: SweepPassOutcome
    error: str | None = None


def ended(clock: IClock, started_at: datetime, error: Exception | None) -> PassResult:
    if error is None:
        return PassResult(started_at, clock.now(), SweepPassOutcome.COMPLETED)
    return PassResult(started_at, clock.now(), SweepPassOutcome.FAILED, str(error) or type(error).__name__)


class HostPassStepper:
    def __init__(
        self, run_pass: Callable[[], None], *, clock: IClock, log: structlog.stdlib.BoundLogger, label: str
    ) -> None:
        self._run_pass = run_pass
        self._clock = clock
        self._log = log
        self._label = label
        self._lock = StepLock()

    def request(self) -> PassResult:
        with self._lock.hold():
            started_at = self._clock.now()
            try:
                self._run_pass()
            except Exception as exc:
                self._log.error(f"{self._label} failed", detail=str(exc))
                return ended(self._clock, started_at, exc)
            return ended(self._clock, started_at, None)

    def timed(self) -> None:
        try:
            self.request()
        except TickInProgress:
            self._log.info(f"{self._label} skipped; another pass is in progress")
