"""The hub's invariant check on request (``bzh:invariant-checker``): its half of the checker, one run at a time."""

from __future__ import annotations

# The lock orders this process's own check runs; it guards no store decision
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from typing import Protocol

from blizzard.foundation.invariants import InvariantsInProgress, Violation


class IHubInvariantChecker(Protocol):
    def run(self) -> list[Violation]: ...


class HubInvariantCheck:
    """Runs the hub half of the checker over every tenant, refusing a run while another holds the lock."""

    def __init__(self, checker: IHubInvariantChecker) -> None:
        self._checker = checker
        self._lock = threading.Lock()

    def run(self) -> list[Violation]:
        """Every breach the hub half finds; raises :class:`InvariantsInProgress` at once while another run holds
        the lock."""
        if not self._lock.acquire(blocking=False):
            raise InvariantsInProgress
        try:
            return self._checker.run()
        finally:
            self._lock.release()
