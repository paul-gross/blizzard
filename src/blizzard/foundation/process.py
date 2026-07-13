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
    close = stat.rfind(")")
    if close == -1:
        return None
    rest = stat[close + 1 :].split()
    if len(rest) < 20:
        return None
    return rest[19]
