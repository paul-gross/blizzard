"""Process-liveness by (pid, start time) — the reap signal.

A bare pid check is unsafe: the OS reuses pids, so this probe keys on **pid AND the
recorded process start time together**. It is a seam (``bzh:pluggable-seams``).
"""

from __future__ import annotations

from typing import Protocol

from blizzard.runner.process.owned_process import IOwnedProcessControl


class IProcessProbe(IOwnedProcessControl, Protocol):
    """Owned-process control plus the start-time read a recorded identity is taken from."""

    def start_time(self, pid: int) -> str | None:
        """The process's stable start-time token, or ``None`` if no such process."""
        ...
