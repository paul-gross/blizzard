"""SQLAlchemy adapter for the chunk escalations seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.work import EscalationOpen
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections


class ChunkEscalationsStore:
    """The chunk's escalation and delivery-bounce facts."""

    def __init__(self, store: HubStoreConnections, clock: IClock, *, facts: IReadChunkFactsRepository) -> None:
        self._store = store
        self._clock = clock
        self._facts = facts

    def list_open_escalations(self) -> list[EscalationOpen]:
        """Every open escalation fleet-wide, each decided by ``ChunkFacts.open_escalation``
        (#293) — the rule's one implementation, never a second derivation from raw rows."""
        newest_by_chunk = self._newest_escalation_per_chunk()
        return [
            EscalationOpen(chunk_id=chunk_id, recorded_at=open_.recorded_at, takeover_command=open_.takeover_command)
            for chunk_id in self._escalation_candidates(newest_by_chunk)
            if (facts := self._facts.load_facts(chunk_id)) is not None
            and (open_ := facts.open_escalation()) is not None
        ]

    def record_escalation(
        self,
        chunk_id: str,
        *,
        epoch: int,
        takeover_command: str,
        at: datetime,
        decision_id: str | None = None,
        wrapped_takeover_command: str = "",
    ) -> int:
        with self._store.write("record_escalation") as conn:
            result = conn.execute(
                s.escalations.insert().values(
                    chunk_id=chunk_id,
                    epoch=epoch,
                    takeover_command=takeover_command,
                    wrapped_takeover_command=wrapped_takeover_command,
                    decision_id=decision_id,
                    recorded_at=at,
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_bounce(self, chunk_id: str, *, epoch: int, cause: str, envelope: str, at: datetime) -> bool:
        """Record one delivery kick-back **idempotently by** ``(chunk_id, epoch)`` (#64).

        A pre-check within the same transaction (mirroring
        ``ChunkHubExecStore.record_hub_step_transition``) rather than a DB constraint: a
        redelivery replay at the coordinator's same ``hub_epoch`` re-enters harmlessly.
        Returns True iff it wrote."""
        with self._store.write("record_bounce") as conn:
            already = conn.execute(
                select(s.chunk_bounces.c.id).where(
                    (s.chunk_bounces.c.chunk_id == chunk_id) & (s.chunk_bounces.c.epoch == epoch)
                )
            ).first()
            if already is not None:
                return False
            conn.execute(
                s.chunk_bounces.insert().values(
                    chunk_id=chunk_id, epoch=epoch, cause=cause, envelope=envelope, recorded_at=at
                )
            )
            return True

    def record_bounce_escalation(
        self, chunk_id: str, *, epoch: int, runner_id: str, takeover_command: str, at: datetime
    ) -> bool:
        """Escalate a bounce-capped chunk **atomically and idempotently** (#64).

        The hub lease and the escalation fact land in one transaction, guarded by the
        escalation's existence at this epoch. No transition: the chunk's held route and
        stuck node are untouched. Returns True iff it wrote."""
        with self._store.write("record_bounce_escalation") as conn:
            already = conn.execute(
                select(s.escalations.c.id).where(
                    (s.escalations.c.chunk_id == chunk_id) & (s.escalations.c.epoch == epoch)
                )
            ).first()
            if already is not None:
                return False
            conn.execute(
                s.lease_facts.insert().values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )
            conn.execute(
                s.escalations.insert().values(
                    chunk_id=chunk_id, epoch=epoch, takeover_command=takeover_command, recorded_at=at
                )
            )
            return True

    def _newest_escalation_per_chunk(self):  # type: ignore[no-untyped-def]
        """The newest ``escalations`` row per chunk. Low-volume, so a full scan is fine."""
        with self._store.read("_newest_escalation_per_chunk") as conn:
            newest_by_chunk = {}
            for e in conn.execute(select(s.escalations)).all():
                current = newest_by_chunk.get(e.chunk_id)
                if current is None or e.recorded_at > current.recorded_at:
                    newest_by_chunk[e.chunk_id] = e
            return newest_by_chunk

    def _escalation_candidates(self, newest_by_chunk) -> list[str]:  # type: ignore[no-untyped-def]
        """Chunks whose newest escalation *might* still be open — a **drop-only** narrowing that
        keeps ``load_facts`` off the obviously-closed ones. Sound because every arm below is
        one the authoritative rule also has, so a chunk dropped here is one ``open_escalation``
        would drop too; arms it lacks (completion) only leave extra work for the fold, never a
        wrong answer."""
        if not newest_by_chunk:
            return []
        chunk_ids = list(newest_by_chunk)
        with self._store.read("_escalation_candidates") as conn:
            lease_rows = conn.execute(select(s.lease_facts).where(s.lease_facts.c.chunk_id.in_(chunk_ids))).all()
            requeue_rows = conn.execute(select(s.requeues).where(s.requeues.c.chunk_id.in_(chunk_ids))).all()
        superseding: dict[str, list[datetime]] = {}
        for lease in lease_rows:
            superseding.setdefault(lease.chunk_id, []).append(lease.minted_at)
        for rq in requeue_rows:
            superseding.setdefault(rq.chunk_id, []).append(rq.requeued_at)
        return [
            chunk_id
            for chunk_id, newest in newest_by_chunk.items()
            if not any(at > newest.recorded_at for at in superseding.get(chunk_id, ()))
        ]


def _conforms_escalations(x: ChunkEscalationsStore) -> IWriteChunkEscalationsRepository:
    return x
