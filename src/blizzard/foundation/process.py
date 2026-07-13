"""Low-level process start-time reading — the reuse-proof half of pid liveness.

A pid alone is ambiguous: the OS reuses pids, so a worker's recorded pid may later
name a different process. Pairing the pid with the process **start time** pins the
identity — a reused pid gets a different start time. This is the one primitive both
the runner's REAP liveness probe and the harness adapter's spawn-return stamping
need, so it lives in the shared kernel. Linux ``/proc`` is the reference source;
the value is an opaque stable token (clock-tick start time), compared for equality,
never interpreted.
"""

from __future__ import annotations


def read_process_start_time(pid: int) -> str | None:
    """The process's stable start-time token from ``/proc/<pid>/stat``, or ``None``.

    Returns ``None`` when no process with ``pid`` exists (or is unreadable). The
    ``comm`` field (2) is paren-wrapped and may contain spaces and parens, so we
    split after the *last* ``)``; ``starttime`` is then field 22 (the 20th field of
    the remainder).
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

    A fire-and-forget worker the runner never ``wait()``s becomes a **zombie** the
    instant it exits: ``/proc/<pid>/stat`` still exists and its start time is
    unchanged, so a bare start-time match would read it as alive forever and ADVANCE
    would never judge the finished worker. The kernel marks such a process state
    ``Z`` (field 3), which is the exited-but-unreaped signal the liveness probe needs
    until P7's REAP reaps children explicitly.
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
