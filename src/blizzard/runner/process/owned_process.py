"""Owned-process teardown policy — the liveness-guarded kill and interrupt of a worker's
recorded process identity.

Pure logic over :class:`IOwnedProcessControl`, a seam (``bzh:pluggable-seams``) the
loop's ``/proc`` probe binds; nothing here touches ``os`` or signals itself.
"""

from __future__ import annotations

from typing import Protocol


class IOwnedProcessControl(Protocol):
    """Liveness + best-effort kill, keyed on (pid, start_time) against pid reuse."""

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
        """Best-effort SIGKILL to an entire owned process group — the group a two-phase
        spawn recorded, never one inferred from a bare pid. Never raises if already gone."""
        ...

    def interrupt_group(self, pgid: int) -> None:
        """Best-effort SIGINT to an entire owned process group — the graceful-shutdown
        drain's own signal, distinct from :meth:`kill_group`'s SIGKILL. Never raises if
        already gone."""
        ...


def owned_process_alive(
    process: IOwnedProcessControl, *, pid: int | None, process_start_time: str | None, pgid: int | None
) -> bool:
    """True iff an owned worker's recorded LEADER is alive OR its recorded GROUP still is —
    a dead leader whose descendant still holds the group reads as alive too. The one shared
    owner of this liveness check: every owned-process teardown or settled-probe reaches it
    here rather than reimplementing the check against a possibly-reused pid/pgid."""
    if pid is None or process_start_time is None:
        return False
    return process.is_alive(pid, process_start_time) or (pgid is not None and process.group_alive(pgid))


def kill_owned_process(
    process: IOwnedProcessControl, *, pid: int | None, process_start_time: str | None, pgid: int | None
) -> None:
    """Best-effort teardown of an owned worker process: by recorded pgid when durable,
    else by bare pid, gated on :func:`owned_process_alive`."""
    if pid is None or process_start_time is None:
        return
    if not owned_process_alive(process, pid=pid, process_start_time=process_start_time, pgid=pgid):
        return  # already gone (or replaced by pid/pgid reuse) — nothing of ours to kill
    if pgid is not None:
        process.kill_group(pgid)
    else:
        process.kill(pid)


def interrupt_owned_process(
    process: IOwnedProcessControl, *, pid: int | None, process_start_time: str | None, pgid: int | None
) -> bool:
    """Best-effort SIGINT to an owned worker's recorded group, gated on the same
    leader-identity guard :func:`kill_owned_process` applies — a recycled pgid is never
    signalled. The one shared owner of the guarded interrupt: every caller reaches it here
    rather than keeping its own copy of the guard.
    ``False`` when nothing was signalled: no recorded group, or a leader already dead."""
    if pgid is None or pid is None or process_start_time is None:
        return False
    if not process.is_alive(pid, process_start_time):
        return False
    process.interrupt_group(pgid)
    return True
