"""Process-liveness by (pid, start time) — the P6 reap signal (no heartbeats yet).

A bare pid check is unsafe: the OS reuses pids, so a different process may now hold
an old worker's pid. REAP therefore keys on **pid AND the recorded process start
time together** — comparing the recorded start time against
the live process's start time survives pid reuse. Heartbeat-based stall detection is
P7; in P6 a worker's liveness is exactly whether its recorded (pid, start_time) is
still the process at that pid.

The probe is a seam (``bzh:pluggable-seams``) so loop tests inject a fake and never
depend on real pids; the Linux ``/proc`` adapter is the reference binding.
"""

from __future__ import annotations

import os
import signal
from typing import Protocol

from blizzard.foundation.process import is_zombie, read_process_start_time


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
        return read_process_start_time(pid)

    def is_alive(self, pid: int, process_start_time: str) -> bool:
        # A worker the runner spawned fire-and-forget becomes a zombie the instant it
        # exits (nothing wait()s it until P7's REAP); its /proc entry lingers with the
        # same start time, so it must read as dead here or ADVANCE never judges it.
        if is_zombie(pid):
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
