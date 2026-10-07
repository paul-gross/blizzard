"""The ``/proc``-backed process probe — the Linux driver behind ``IProcessProbe``."""

from __future__ import annotations

import contextlib
import os
import signal

from blizzard.foundation.process import ProcStat
from blizzard.runner.process.probe import IProcessProbe


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
        # `pgid` is the leader's own pid: reap it first, or an exited-but-unreaped
        # leader is a zombie `killpg`'s probe below still reaches as "alive".
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pgid, os.WNOHANG)
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


def _conforms_process_probe(x: LinuxProcessProbe) -> IProcessProbe:
    return x
