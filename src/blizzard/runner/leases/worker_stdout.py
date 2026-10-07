"""The seams over the per-(lease, generation) files a spawned worker's stdout and stderr redirect to.

The driver is ``internal/worker_stdout_files.py``."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from blizzard.runner.leases.model import Lease


class IWorkerStdoutReader(Protocol):
    """The read half: a generation's captured stdout."""

    def read_stdout(self, lease_id: str, generation: int) -> str:
        """That generation's captured stdout, or ``""`` when absent/unreadable."""
        ...


class IWorkerStdoutFiles(IWorkerStdoutReader, Protocol):
    """One runner's worker-output file layout: redirect targets, the stderr tail, and the prune."""

    def stdout_path(self, lease_id: str, generation: int) -> str:
        """This lease's per-generation stdout redirect target, or ``""`` for no redirect."""
        ...

    def stderr_path(self, lease_id: str, generation: int) -> str:
        """This lease's per-generation harness-stderr redirect target, or ``""``."""
        ...

    def stderr_tail(self, lease: Lease, *, limit: int = 2000) -> str:
        """The tail of this lease's most-recent captured spawn-stderr, or ``""``; never raises."""
        ...

    def sweep(self, *, now: datetime, retention: timedelta) -> int:
        """Remove every captured stdout/stderr file older than ``retention``; returns the count."""
        ...
