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

    def group_alive(self, pgid: int) -> bool:
        """True iff process group ``pgid`` still has at least one live member — a
        signal-0 ``killpg`` probe, unlike :meth:`is_alive`'s single-pid, start-time-checked
        one: a group can outlive its recorded leader when a descendant it spawned survives."""
        ...

    def kill(self, pid: int) -> None:
        """Best-effort SIGKILL — never raises if the process is already gone."""
        ...

    def kill_group(self, pgid: int) -> None:
        """Best-effort SIGKILL to an entire owned process group (D3) — the group a two-phase
        spawn recorded, never one inferred from a bare pid. Never raises if already gone."""
        ...

    def interrupt_group(self, pgid: int) -> None:
        """Best-effort SIGINT to an entire owned process group — the graceful-shutdown
        drain's own signal, distinct from :meth:`kill_group`'s SIGKILL. Never raises if
        already gone."""
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

    def group_alive(self, pgid: int) -> bool:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False  # no process in this group at all
        except PermissionError:
            return True  # the group exists — signal 0 just couldn't reach it
        return True

    def kill(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return

    def kill_group(self, pgid: int) -> None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return

    def interrupt_group(self, pgid: int) -> None:
        try:
            os.killpg(pgid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            return


def kill_owned_process(
    process: IProcessProbe, *, pid: int | None, process_start_time: str | None, pgid: int | None
) -> None:
    """Best-effort teardown of an owned worker process (D3): by recorded pgid when durable,
    else by bare pid, gated on the LEADER'S liveness OR the GROUP'S — a dead leader whose
    descendant still holds the group must still have that group killed. The one shared owner
    of this liveness-checked, pgid-preferring kill: every owned-process teardown reaches it
    here rather than reimplementing the check against a possibly-reused pid/pgid."""
    if pid is None or process_start_time is None:
        return
    leader_alive = process.is_alive(pid, process_start_time)
    group_still_alive = pgid is not None and process.group_alive(pgid)
    if not leader_alive and not group_still_alive:
        return  # already gone (or replaced by pid/pgid reuse) — nothing of ours to kill
    if pgid is not None:
        process.kill_group(pgid)
    else:
        process.kill(pid)


def _conforms_process_probe(x: LinuxProcessProbe) -> IProcessProbe:
    return x
