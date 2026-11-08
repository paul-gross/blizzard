"""Marking in-flight leases for same-lease restart-resume — the graceful-shutdown hook and the startup
crash-orphan scan. Store-only: no context, no hub."""

from __future__ import annotations

from collections.abc import Container, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.hub.outbound_buffer import IReadOutboundRepository
from blizzard.runner.leases.activity import Liveness
from blizzard.runner.leases.asks import IReadAskRepository
from blizzard.runner.leases.elicitation import IReadElicitationRepository
from blizzard.runner.leases.liveness import IReadLeaseLivenessRepository
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.overload import IReadOverloadRepository, backing_off_facts
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.leases.resume_intent import IWriteLeaseResumeIntentRepository
from blizzard.runner.leases.session import IReadLeaseSessionRepository
from blizzard.runner.lifecycle.daemon_liveness import IReadDaemonLivenessRepository
from blizzard.runner.lifecycle.model import TakeoverHolds, crash_orphaned, resumable
from blizzard.runner.lifecycle.takeover import IReadTakeoverRepository
from blizzard.runner.process.probe import IProcessProbe

_log = get_logger("blizzard.runner.loop")


class ResumeIntentStores(Protocol):
    @property
    def lease_record(self) -> IReadLeaseRecordRepository: ...
    @property
    def asks(self) -> IReadAskRepository: ...
    @property
    def outbound(self) -> IReadOutboundRepository: ...
    @property
    def elicitations(self) -> IReadElicitationRepository: ...
    @property
    def overload(self) -> IReadOverloadRepository: ...
    @property
    def liveness(self) -> IReadLeaseLivenessRepository: ...
    @property
    def takeover(self) -> IReadTakeoverRepository: ...
    @property
    def session(self) -> IReadLeaseSessionRepository: ...
    @property
    def resume_intent(self) -> IWriteLeaseResumeIntentRepository: ...


@dataclass(frozen=True)
class ResumeIntents:
    """Marking in-flight leases for same-lease restart-resume — the graceful-shutdown hook
    and the startup crash-orphan scan. Store-only: no context, no hub.

    Spans leases, asks (parked) and outbound (pending submission), so it names the stores it reads
    and writes as :class:`ResumeIntentStores`."""

    stores: ResumeIntentStores

    def mark_graceful(self, *, now: datetime) -> int:
        """Mark every in-flight lease, and return the count. One durable row per mark, so a
        crash mid-marking degrades to the ungraceful path."""
        marked = self._mark(self._resumable(), now=now)
        if marked:
            _log.info("marked in-flight leases for restart-resume", count=marked)
        return marked

    def mark_crashed(self, *, process: IProcessProbe, liveness: IReadDaemonLivenessRepository, now: datetime) -> int:
        """Mark the leases a crash orphaned mid-session, and return the count.

        Staleness is measured against ``liveness``'s last beat, not the clock at recovery,
        which at startup is ``downtime + idle-at-crash``."""
        ended = self.stores.session.session_ended_lease_ids()
        # as_utc: this instant is about to be subtracted from, and a naive one compares wrong.
        last_alive = liveness.last_daemon_liveness()
        crashed_at = as_utc(last_alive) if last_alive is not None else now
        marked = self._mark(self._crash_orphaned(ended, process, crashed_at), now=now)
        if marked:
            _log.info("marked crash-interrupted leases for restart-resume", count=marked)
        return marked

    def _resumable(self) -> Iterator[Lease]:
        """The active leases a restart may mark for same-lease resume (``resumable``)."""
        parked = self.stores.asks.parked_lease_ids()
        pending = self.stores.outbound.pending_submission_lease_ids()
        eliciting = self.stores.elicitations.in_flight_elicitation_lease_ids()
        backing_off = backing_off_facts(self.stores.overload, self.stores.liveness, self.stores.elicitations)
        takeovers = TakeoverHolds.of(self.stores.takeover.open_takeovers())
        for lease in self.stores.lease_record.list_active_leases():
            if resumable(
                lease,
                parked=parked,
                pending_submission=pending,
                eliciting=eliciting,
                backing_off=backing_off,
                taken_over=takeovers.holds_lease(lease),
            ):
                yield lease

    def _crash_orphaned(self, ended: Container[str], process: IProcessProbe, crashed_at: datetime) -> Iterator[Lease]:
        candidates = list(self._resumable())
        # One bulk read for every candidate's heartbeat/spawn (`bzh:bulk-reconstitution`).
        facts_by_lease = self.stores.liveness.liveness_facts([lease.lease_id for lease in candidates])
        for lease in candidates:
            facts = facts_by_lease.get(lease.lease_id)
            liveness = Liveness.of(
                lease,
                heartbeat=facts.latest_heartbeat if facts is not None else None,
                spawn=facts.latest_spawn if facts is not None else None,
            )
            if crash_orphaned(
                lease,
                session_ended=lease.lease_id in ended,
                alive=lease.pid is not None and process.is_alive(lease.pid, lease.process_start_time or ""),
                stale=liveness.stale(crashed_at),
            ):
                yield lease

    def _mark(self, leases: Iterator[Lease], *, now: datetime) -> int:
        marked = 0
        for lease in leases:
            self.stores.resume_intent.record_resume_intent(lease_id=lease.lease_id, marked_at=now)
            marked += 1
        return marked
