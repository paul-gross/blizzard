"""The host's invariant check on request (``bzh:invariant-checker``): the runner half over every runner on the host,
one run at a time."""

from __future__ import annotations

import threading
from typing import Protocol

from blizzard.foundation.invariants import InvariantsInProgress, Violation


class IRunnerInvariantChecker(Protocol):
    def run(self, *, after_recovery: bool = False) -> list[Violation]: ...


class HostInvariantCheck:
    """Runs the runner half of the checker as a live daemon asks it — after recovery, since a daemon answering has
    marked its crash-orphaned leases for resume on start — refusing a run while another holds the lock."""

    def __init__(self, checker: IRunnerInvariantChecker) -> None:
        self._checker = checker
        self._lock = threading.Lock()

    def run(self) -> list[Violation]:
        """Every breach the runner half finds; raises :class:`InvariantsInProgress` at once while another run holds
        the lock."""
        if not self._lock.acquire(blocking=False):
            raise InvariantsInProgress
        try:
            return self._checker.run(after_recovery=True)
        finally:
            self._lock.release()
