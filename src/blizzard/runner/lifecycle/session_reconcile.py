"""Reconciling sessions as the daemon starts: a turn whose driver died with the daemon can keep running where its
harness runs it, so before any restart resume is dispatched each session the runner's own records hold unfinished, with
no live worker and no takeover, is handed to its harness owner to stop. The runner reads only its own records, never a
listing of a service's sessions, so it never touches another runner's sessions or the operator's own."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.registry import IHarnessRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.leases.elicitation import IReadElicitationRepository
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.leases.session import IReadLeaseSessionRepository
from blizzard.runner.lifecycle.model import TakeoverHolds, reconcilable
from blizzard.runner.lifecycle.takeover import IReadTakeoverRepository
from blizzard.runner.process.owned_process import IOwnedProcessControl

_log = get_logger("blizzard.runner.lifecycle")


class SessionReconcileStores(Protocol):
    @property
    def lease_record(self) -> IReadLeaseRecordRepository: ...
    @property
    def session(self) -> IReadLeaseSessionRepository: ...
    @property
    def elicitations(self) -> IReadElicitationRepository: ...
    @property
    def takeover(self) -> IReadTakeoverRepository: ...


@dataclass(frozen=True)
class SessionReconcile:
    """One daemon start's reconcile pass over the runner's own active leases."""

    stores: SessionReconcileStores
    harnesses: IHarnessRegistry
    process: IOwnedProcessControl

    def run(self) -> int:
        """Hand each harness owner its reconcilable sessions, and return how many turns the owners stopped; never
        raises. An owner that cannot be resolved, or fails, stops none."""
        stopped = 0
        for harness_id, session_ids in self.sessions().items():
            try:
                stopped += self.harnesses.lifecycle(harness_id).reconcile(session_ids)
            except (UnknownHarnessError, UnavailableHarnessError) as exc:
                _log.warning(
                    "reconciled sessions' owner unavailable — left running",
                    harness_id=harness_id,
                    sessions=len(session_ids),
                    detail=str(exc),
                )
            except Exception:
                _log.exception("reconciling sessions failed", harness_id=harness_id, sessions=len(session_ids))
        return stopped

    def sessions(self) -> dict[str, tuple[str, ...]]:
        """The reconcilable sessions' ids, by harness owner, in active-lease order."""
        ended = self.stores.session.session_ended_lease_ids()
        judges = self.stores.elicitations.in_flight_elicitations_by_lease()
        holds = TakeoverHolds.of(self.stores.takeover.open_takeovers())
        by_owner: defaultdict[str, list[str]] = defaultdict(list)
        for lease in self.stores.lease_record.list_active_leases():
            judge = judges.get(lease.lease_id)
            alive = self._alive(lease.pid, lease.process_start_time) or (
                judge is not None and self._alive(judge.pid, judge.process_start_time)
            )
            if lease.session is not None and reconcilable(
                lease, session_ended=lease.lease_id in ended, alive=alive, taken_over=holds.holds_lease(lease)
            ):
                by_owner[lease.session.harness_id].append(lease.session.session_id)
        return {harness_id: tuple(session_ids) for harness_id, session_ids in by_owner.items()}

    def _alive(self, pid: int | None, process_start_time: str | None) -> bool:
        return pid is not None and self.process.is_alive(pid, process_start_time or "")


__all__ = ["SessionReconcile", "SessionReconcileStores"]
