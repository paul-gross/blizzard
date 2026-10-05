"""Owned-process teardown policy — the liveness-guarded kill and interrupt of a worker's
recorded process identity.

:class:`OwnedProcess` decides from probe readings what may be signalled; the three free functions read
:class:`IOwnedProcessControl`, the seam the loop's ``/proc`` probe binds, and send the signal it chose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from blizzard.foundation.roles import domain_model


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


@domain_model
@dataclass(frozen=True)
class KillTarget:
    """What a teardown signals: the recorded process ``group`` or, with no group recorded, the
    ``leader`` pid alone."""

    kind: Literal["group", "leader"]
    id: int


@domain_model
@dataclass(frozen=True)
class OwnedProcess:
    """A worker's recorded process identity (leader pid, start time, process group) and its teardown
    policy. An unrecorded identity is never alive and never signalled. A kill reaches whatever of ours
    still lives — the group when recorded, else the bare leader pid — while an interrupt goes only to a
    recorded group whose leader is alive, since a recycled pgid behind a dead leader must never be signalled."""

    pid: int | None
    process_start_time: str | None
    pgid: int | None

    def identity(self) -> tuple[int, str] | None:
        """The recorded leader ``(pid, start time)``, or ``None`` when either was never recorded."""
        if self.pid is None or self.process_start_time is None:
            return None
        return self.pid, self.process_start_time

    def interruptible(self) -> bool:
        """Whether an interrupt could ever reach this process: a recorded leader and group."""
        return self.identity() is not None and self.pgid is not None

    def alive(self, *, leader_alive: bool, group_alive: bool) -> bool:
        """Alive when the recorded leader is, or its recorded group still has a live member — a
        dead leader whose descendant still holds the group reads as alive too."""
        return self.identity() is not None and (leader_alive or (self.pgid is not None and group_alive))

    def kill_target(self, *, leader_alive: bool, group_alive: bool) -> KillTarget | None:
        """What a kill signals — the recorded group when there is one, else the leader pid — or
        ``None`` when nothing of ours is alive (already gone, or replaced by pid/pgid reuse)."""
        identity = self.identity()
        if identity is None or not self.alive(leader_alive=leader_alive, group_alive=group_alive):
            return None
        return KillTarget("group", self.pgid) if self.pgid is not None else KillTarget("leader", identity[0])

    def interrupt_target(self, *, leader_alive: bool) -> int | None:
        """The process group an interrupt signals, or ``None`` when no group was recorded or its
        recorded leader is no longer alive."""
        if not self.interruptible() or not leader_alive:
            return None
        return self.pgid


def _readings(process: IOwnedProcessControl, owned: OwnedProcess) -> tuple[bool, bool] | None:
    """The ``(leader_alive, group_alive)`` readings :meth:`OwnedProcess.alive` judges, or ``None``
    for an unrecorded identity. The group is probed only behind a dead leader: a live leader
    already decides liveness, and the kill target never depends on the group reading."""
    identity = owned.identity()
    if identity is None:
        return None
    leader_alive = process.is_alive(*identity)
    group_alive = not leader_alive and owned.pgid is not None and process.group_alive(owned.pgid)
    return leader_alive, group_alive


def owned_process_alive(
    process: IOwnedProcessControl, *, pid: int | None, process_start_time: str | None, pgid: int | None
) -> bool:
    """True iff an owned worker's recorded LEADER is alive OR its recorded GROUP still is
    (:meth:`OwnedProcess.alive`). The one shared owner of this liveness check: every owned-process
    teardown or settled-probe reaches it here rather than reimplementing the check against a
    possibly-reused pid/pgid."""
    owned = OwnedProcess(pid=pid, process_start_time=process_start_time, pgid=pgid)
    readings = _readings(process, owned)
    return readings is not None and owned.alive(leader_alive=readings[0], group_alive=readings[1])


def kill_owned_process(
    process: IOwnedProcessControl, *, pid: int | None, process_start_time: str | None, pgid: int | None
) -> None:
    """Best-effort teardown of an owned worker process, signalling :meth:`OwnedProcess.kill_target`."""
    owned = OwnedProcess(pid=pid, process_start_time=process_start_time, pgid=pgid)
    readings = _readings(process, owned)
    if readings is None:
        return
    target = owned.kill_target(leader_alive=readings[0], group_alive=readings[1])
    if target is None:
        return
    if target.kind == "group":
        process.kill_group(target.id)
    else:
        process.kill(target.id)


def interrupt_owned_process(
    process: IOwnedProcessControl, *, pid: int | None, process_start_time: str | None, pgid: int | None
) -> bool:
    """Best-effort SIGINT to an owned worker's recorded group, signalling
    :meth:`OwnedProcess.interrupt_target` — a recycled pgid is never signalled. The one shared
    owner of the guarded interrupt: every caller reaches it here rather than keeping its own copy.
    ``False`` when nothing was signalled: no recorded group, or a leader already dead."""
    owned = OwnedProcess(pid=pid, process_start_time=process_start_time, pgid=pgid)
    identity = owned.identity()
    if identity is None or not owned.interruptible():
        return False
    target = owned.interrupt_target(leader_alive=process.is_alive(*identity))
    if target is None:
        return False
    process.interrupt_group(target)
    return True
