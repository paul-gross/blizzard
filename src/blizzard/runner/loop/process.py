"""Process-liveness by (pid, start time) — the reap signal.

A bare pid check is unsafe: the OS reuses pids, so this probe keys on **pid AND the
recorded process start time together**. It is a seam (``bzh:pluggable-seams``); the Linux
``/proc`` adapter is the reference binding.
"""

from __future__ import annotations

import os
import signal
from typing import Protocol

from blizzard.foundation.process import ProcStat


class IProcessProbe(Protocol):
    """Liveness + best-effort kill, keyed on (pid, start_time) against pid reuse."""

    def start_time(self, pid: int) -> str | None:
        """The process's stable start-time token, or ``None`` if no such process."""
        ...

    def is_alive(self, pid: int, process_start_time: str) -> bool:
        """True iff a process with ``pid`` exists *and* its start time still matches."""
        ...

    def kill(self, pid: int) -> None:
        """Best-effort SIGKILL — never raises if the process is already gone."""
        ...


class LinuxProcessProbe:
    """``/proc``-backed probe: field-22 ``starttime`` is the reuse-proof identity."""

    def start_time(self, pid: int) -> str | None:
        return ProcStat.of(pid).start_time

    def is_alive(self, pid: int, process_start_time: str) -> bool:
        # An unreaped child lingers in /proc with the same start time after it exits, so
        # a zombie must read as dead here.
        if ProcStat.of(pid).zombie:
            return False
        current = self.start_time(pid)
        return current is not None and current == process_start_time

    def kill(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return


def _conforms_process_probe(x: LinuxProcessProbe) -> IProcessProbe:
    return x
