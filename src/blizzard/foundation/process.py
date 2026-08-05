"""Low-level process start-time reading — the reuse-proof half of pid liveness.

The OS reuses pids, so a recorded pid may later name a different process; pairing it
with the process **start time** pins the identity. Linux ``/proc`` is the reference
source; the value is an opaque stable token compared for equality, never interpreted.
"""

from __future__ import annotations


def read_process_start_time(pid: int) -> str | None:
    """The process's stable start-time token from ``/proc/<pid>/stat``, or ``None``.

    Returns ``None`` when no process with ``pid`` exists (or is unreadable).
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            stat = fh.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    rest = _stat_fields_after_comm(stat)
    if rest is None or len(rest) < 20:
        return None
    return rest[19]


def is_zombie(pid: int) -> bool:
    """True iff ``pid`` names a defunct (exited-but-unreaped) process.

    An exited-but-unreaped process keeps its ``/proc`` entry and start time, so a bare
    start-time match reads it as alive forever; the kernel marks state ``Z`` (field 3).
    """
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            stat = fh.read()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return False
    rest = _stat_fields_after_comm(stat)
    return rest is not None and len(rest) >= 1 and rest[0] == "Z"


def _stat_fields_after_comm(stat: str) -> list[str] | None:
    """The ``/proc/<pid>/stat`` fields after ``comm`` — ``state`` is index 0, ``starttime`` 19.

    The ``comm`` field (2) is paren-wrapped and may contain spaces and parens, so we
    split after the *last* ``)``; the remainder begins at field 3 (``state``).
    """
    close = stat.rfind(")")
    if close == -1:
        return None
    return stat[close + 1 :].split()
