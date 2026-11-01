"""SQLAlchemy adapter for the chunk escalations seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import EscalationOpen
from blizzard.hub.domain.chunk.ports.escalations import BounceEscalationOutcome, IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import (
    conn_of,
    ephemeral_ids_select,
    fence,
    lock_chunk_row,
    record_hub_lease,
)
from blizzard.hub.store.internal.chunk_terminal_predicates import maybe_live
from blizzard.hub.store.internal.newest_fact import newer_row_exists
from blizzard.hub.store.scoped import ScopedConnections


class ChunkEscalationsStore:
    """The chunk's escalation and delivery-bounce facts."""

    def __init__(self, store: ScopedConnections, clock: IClock, *, facts: IReadChunkFactsRepository) -> None:
        self._store = store
        self._clock = clock
        self._facts = facts

    def list_open_escalations(
        self, *, project: ProjectId | None = None, chunk_id: str | None = None
    ) -> list[EscalationOpen]:
        """Every open escalation fleet-wide, each decided by ``ChunkFacts.open_escalation``
        (#293) — the rule's one implementation, never a second derivation from raw rows."""
        candidates = list(self.newest_escalation_per_chunk(project, chunk_id))
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
                self._store.insert(s.escalations).values(
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

    def record_bounce_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        cause: str,
        envelope: str,
        at: datetime,
    ) -> bool:
        return self._bounce_conn(
            conn_of(handle), chunk_id, epoch=epoch, admission=admission, cause=cause, envelope=envelope, at=at
        )

    def _bounce_conn(
        self,
        conn: Connection,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        cause: str,
        envelope: str,
        at: datetime,
    ) -> bool:
        """The bounce's replay guard and insert, behind the fence."""
        already = conn.execute(
            self._store.select(s.chunk_bounces, s.chunk_bounces.c.id).where(
                (s.chunk_bounces.c.chunk_id == chunk_id) & (s.chunk_bounces.c.epoch == epoch)
            )
        ).first()
        if already is not None:
            return False
        if fence(conn, chunk_id, epoch=epoch, admission=admission) is not None:
            return False
        conn.execute(
            self._store.insert(s.chunk_bounces).values(
                chunk_id=chunk_id, epoch=epoch, cause=cause, envelope=envelope, recorded_at=at
            )
        )
        return True

    def record_bounce_escalation_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        runner_id: str,
        takeover_command: str,
        at: datetime,
        cause: str,
        detail: str,
    ) -> BounceEscalationOutcome:
        return self._bounce_escalation_conn(
            conn_of(handle),
            chunk_id,
            epoch=epoch,
            admission=admission,
            runner_id=runner_id,
            takeover_command=takeover_command,
            at=at,
            cause=cause,
            detail=detail,
        )

    def _bounce_escalation_conn(
        self,
        conn: Connection,
        chunk_id: str,
        *,
        epoch: int,
        admission: EpochAdmission,
        runner_id: str,
        takeover_command: str,
        at: datetime,
        cause: str,
        detail: str,
    ) -> BounceEscalationOutcome:
        lock_chunk_row(conn, chunk_id)
        already = conn.execute(
            self._store.select(s.escalations, s.escalations.c.id).where(
                (s.escalations.c.chunk_id == chunk_id) & (s.escalations.c.epoch == epoch)
            )
        ).first()
        if already is not None:
            return BounceEscalationOutcome.ALREADY_RECORDED
        if fence(conn, chunk_id, epoch=epoch, admission=admission) is not None:
            return BounceEscalationOutcome.REFUSED
        record_hub_lease(conn, chunk_id, epoch=epoch, runner_id=runner_id, at=at)
        conn.execute(
            self._store.insert(s.escalations).values(
                chunk_id=chunk_id,
                epoch=epoch,
                takeover_command=takeover_command,
                cause=cause,
                detail=detail,
                recorded_at=at,
            )
        )
        return BounceEscalationOutcome.RECORDED

    def newest_escalation_per_chunk(
        self, project: ProjectId | None = None, chunk_id: str | None = None
    ) -> dict[str, Row[Any]]:
        """The newest ``escalations`` row per chunk, over the chunks whose newest escalation *might*
        still be open — a chunk a terminal fact settles, or one grouped away or deleted, can never
        hold an open escalation, and neither can one a later lease or requeue supersedes, so each is
        excluded in the query rather than loaded and dropped by the fold.

        A **drop-only** narrowing: each excluding arm must be one that
        ``src/blizzard/hub/domain/chunk/model.py``'s ``ChunkFacts.open_escalation`` also closes on."""
        live_chunk_ids = self._store.select(s.chunks, s.chunks.c.chunk_id).where(
            maybe_live(self._store), s.chunks.c.chunk_id.not_in(ephemeral_ids_select(self._store))
        )
        if project is not None:
            live_chunk_ids = live_chunk_ids.where(s.chunks.c.project_id == project)
        if chunk_id is not None:
            live_chunk_ids = live_chunk_ids.where(s.chunks.c.chunk_id == chunk_id)
        with self._store.read("newest_escalation_per_chunk") as conn:
            newer = s.escalations.alias("newer")
            beaten = newer_row_exists(
                self._store,
                newer,
                partition=[(s.escalations.c.chunk_id, newer.c.chunk_id)],
                order=[(s.escalations.c.recorded_at, newer.c.recorded_at), (s.escalations.c.id, newer.c.id)],
            )
            leased_since = self._store.exists(
                s.lease_facts,
                s.lease_facts.c.chunk_id == s.escalations.c.chunk_id,
                s.lease_facts.c.minted_at > s.escalations.c.recorded_at,
            )
            requeued_since = self._store.exists(
                s.requeues,
                s.requeues.c.chunk_id == s.escalations.c.chunk_id,
                s.requeues.c.requeued_at > s.escalations.c.recorded_at,
            )
            rows = conn.execute(
                self._store.select(s.escalations).where(
                    s.escalations.c.chunk_id.in_(live_chunk_ids), ~beaten, ~leased_since, ~requeued_since
                )
            ).all()
            return {e.chunk_id: e for e in rows}


_escalations_conforms: type[IWriteChunkEscalationsRepository] = ChunkEscalationsStore
