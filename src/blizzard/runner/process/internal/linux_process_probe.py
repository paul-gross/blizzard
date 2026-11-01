"""The ``/proc``-backed process probe — the Linux driver behind ``IProcessProbe``."""

from __future__ import annotations

import contextlib
import os
import signal

from blizzard.runner.process.groups import ProcStat, group_exists, signal_group
from blizzard.runner.process.probe import IProcessProbe


def _read_stat(pid: int) -> ProcStat:
    """Read ``pid``'s stat line — a vanished or unreadable pid yields empty fields."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return ProcStat.parse(fh.read())
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return ProcStat(())


class LinuxProcessProbe:
    """``/proc``-backed probe: field-22 ``starttime`` is the reuse-proof identity."""

    def start_time(self, pid: int) -> str | None:
        return _read_stat(pid).start_time

    def is_alive(self, pid: int, process_start_time: str) -> bool:
        # An unreaped child lingers in /proc with the same start time after it exits, so
        # a zombie must read as dead here.
        if _read_stat(pid).zombie:
            return False
        current = self.start_time(pid)
        return current is not None and current == process_start_time

    def group_alive(self, pgid: int) -> bool:
        # `pgid` is the leader's own pid: reap it first, or an exited-but-unreaped
        # leader is a zombie `killpg`'s probe below still reaches as "alive".
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pgid, os.WNOHANG)
        return group_exists(pgid)

    def kill(self, pid: int) -> None:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            return

    def kill_group(self, pgid: int) -> None:
        with contextlib.suppress(PermissionError):
            signal_group(pgid, signal.SIGKILL)

    def interrupt_group(self, pgid: int) -> None:
        with contextlib.suppress(PermissionError):
            signal_group(pgid, signal.SIGINT)


_process_probe_conforms: type[IProcessProbe] = LinuxProcessProbe
