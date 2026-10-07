"""SQLAlchemy adapter for the chunk escalations seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, tuple_

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import EscalationOpen
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import (
    ephemeral_ids_select,
    fence,
    lock_chunk_row,
    record_hub_lease,
)
from blizzard.hub.store.internal.chunk_terminal_predicates import maybe_live


class ChunkEscalationsStore:
    """The chunk's escalation and delivery-bounce facts."""

    def __init__(self, store: HubStoreConnections, clock: IClock, *, facts: IReadChunkFactsRepository) -> None:
        self._store = store
        self._clock = clock
        self._facts = facts

    def list_open_escalations(self) -> list[EscalationOpen]:
        """Every open escalation fleet-wide, each decided by ``ChunkFacts.open_escalation``
        (#293) — the rule's one implementation, never a second derivation from raw rows."""
        candidates = self._escalation_candidates(self._newest_escalation_per_chunk())
        facts_by_id = self._facts.load_facts_for(candidates)
        return [
            EscalationOpen(
                chunk_id=chunk_id,
                recorded_at=open_.recorded_at,
                takeover_command=open_.takeover_command,
                cause=open_.cause,
                detail=open_.detail,
            )
            for chunk_id in candidates
            if (facts := facts_by_id.get(chunk_id)) is not None and (open_ := facts.open_escalation()) is not None
        ]

    def record_escalation(
        self,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        takeover_command: str,
        at: datetime,
        decision_id: str | None = None,
        wrapped_takeover_command: str = "",
        cause: str | None,
        detail: str | None,
    ) -> int | FenceRefusal:
        with self._store.write("record_escalation") as conn:
            lock_chunk_row(conn, chunk_id)
            refusal = fence(conn, chunk_id, epoch=epoch, admission=admission, claimant=claimant)
            if refusal is not None:
                return refusal
            result = conn.execute(
                s.escalations.insert().values(
                    chunk_id=chunk_id,
                    epoch=epoch,
                    takeover_command=takeover_command,
                    wrapped_takeover_command=wrapped_takeover_command,
                    decision_id=decision_id,
                    cause=cause,
                    detail=detail,
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
        self,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        runner_id: str,
        takeover_command: str,
        at: datetime,
        cause: str,
        detail: str,
    ) -> bool:
        """Escalate a bounce-capped chunk **atomically and idempotently** (#64).

        The hub lease and the escalation fact land in one transaction, guarded by the
        escalation's existence at this epoch. No transition: the chunk's held route and
        stuck node are untouched. Returns True iff it wrote."""
        with self._store.write("record_bounce_escalation") as conn:
            lock_chunk_row(conn, chunk_id)
            already = conn.execute(
                select(s.escalations.c.id).where(
                    (s.escalations.c.chunk_id == chunk_id) & (s.escalations.c.epoch == epoch)
                )
            ).first()
            if already is not None:
                return False
            if fence(conn, chunk_id, epoch=epoch, admission=admission) is not None:
                return False
            record_hub_lease(conn, chunk_id, epoch=epoch, runner_id=runner_id, at=at)
            conn.execute(
                s.escalations.insert().values(
                    chunk_id=chunk_id,
                    epoch=epoch,
                    takeover_command=takeover_command,
                    cause=cause,
                    detail=detail,
                    recorded_at=at,
                )
            )
            return True

    def _newest_escalation_per_chunk(self):  # type: ignore[no-untyped-def]
        """The newest ``escalations`` row per chunk, over the chunks that might still be
        live — a chunk a terminal fact settles, or one grouped away or deleted, can never
        hold an open escalation, so it is excluded in the query rather than loaded and
        dropped by the fold."""
        live_chunk_ids = select(s.chunks.c.chunk_id).where(
            maybe_live(), s.chunks.c.chunk_id.not_in(ephemeral_ids_select())
        )
        with self._store.read("_newest_escalation_per_chunk") as conn:
            newer = s.escalations.alias("newer")
            beaten = (
                select(newer.c.id)
                .where(
                    newer.c.chunk_id == s.escalations.c.chunk_id,
                    tuple_(newer.c.recorded_at, newer.c.id) > tuple_(s.escalations.c.recorded_at, s.escalations.c.id),
                )
                .exists()
            )
            rows = conn.execute(
                select(s.escalations).where(s.escalations.c.chunk_id.in_(live_chunk_ids), ~beaten)
            ).all()
            return {e.chunk_id: e for e in rows}

    def _escalation_candidates(self, newest_by_chunk) -> list[str]:  # type: ignore[no-untyped-def]
        """Chunks whose newest escalation *might* still be open — a **drop-only** narrowing that
        trades the two statements above (the lease and requeue reads) for a smaller batch into
        ``load_facts_for``: fewer rows in that one read, not fewer calls. Sound because every arm
        below is one the authoritative rule also has, so a chunk dropped here is one
        ``open_escalation`` would drop too; arms it lacks (completion) only leave extra work for
        the fold, never a wrong answer."""
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
