"""Low-level process start-time reading — the reuse-proof half of pid liveness.

The OS reuses pids, so a recorded pid may later name a different process; pairing it
with the process **start time** pins the identity. Linux ``/proc`` is the reference
source; the value is an opaque stable token compared for equality, never interpreted.

Process groups are signalled and probed here too: :func:`signal_group` and :func:`group_exists` are the one home of
``os.killpg``, and :func:`terminate_group` is the TERM, grace, KILL sequence built on them."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class ProcStat:
    """One process's ``/proc/<pid>/stat`` fields after ``comm`` — ``state`` at 0, ``starttime`` at 19.

    Empty for a pid naming no process, or whose stat line is unreadable or malformed."""

    fields: tuple[str, ...]

    @classmethod
    def of(cls, pid: int) -> ProcStat:
        """Read ``pid``'s stat line — a vanished or unreadable pid yields empty fields."""
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
                stat = fh.read()
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return cls(())
        # ``comm`` (field 2) is paren-wrapped and may contain spaces and parens, so split
        # after the *last* ``)``; the remainder begins at field 3 (``state``).
        close = stat.rfind(")")
        return cls(tuple(stat[close + 1 :].split())) if close != -1 else cls(())

    @property
    def start_time(self) -> str | None:  # ast-grep-ignore: bzh:property-delegates
        """The stable start-time token, or ``None`` when this pid named no live process."""
        return self.fields[19] if len(self.fields) >= 20 else None

    @property
    def zombie(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        """True iff the kernel marks the process defunct — exited, not yet reaped.

        Such a process keeps its ``/proc`` entry and start time, so a bare start-time
        match reads it as alive forever."""
        return len(self.fields) >= 1 and self.fields[0] == "Z"


def signal_group(pgid: int, sig: signal.Signals) -> None:
    """Send ``sig`` to the process group ``pgid``; a group that already exited is not an error."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, sig)


def group_exists(pgid: int) -> bool:
    """Whether any process is left in the group ``pgid``; a group signal 0 cannot reach still exists."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_group[T](pgid: int, *, grace: float, wait: Callable[[float], T]) -> T | None:
    """SIGTERM the group, let ``wait`` bound the ``grace`` the leader gets to exit, then SIGKILL the group — a
    member may ignore SIGTERM while the leader exits on it. Returns what ``wait`` returned, or ``None`` when
    ``grace`` ran out (``wait`` raising :class:`subprocess.TimeoutExpired`)."""
    signal_group(pgid, signal.SIGTERM)
    try:
        return wait(grace)
    except subprocess.TimeoutExpired:
        return None
    finally:
        signal_group(pgid, signal.SIGKILL)
