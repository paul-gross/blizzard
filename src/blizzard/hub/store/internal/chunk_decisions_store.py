"""SQLAlchemy adapter for the chunk decisions seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import Connection, and_, select

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import DecisionChoice, GateDecision
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository, LiveDecisionStatus
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import (
    conn_of,
    enqueue_close_intents,
    fence,
    is_landing_marker,
    lock_chunk_row,
    next_artifact_seq,
)
from blizzard.hub.store.internal.chunk_terminal_predicates import chunk_is_terminal

#: Every fact table whose ``decision_id`` column closes a decision — the resolving
#: transition, the migration (#90), the unresolvable-target escalation (#110), or the
#: restart (#370). The one source both :meth:`ChunkDecisionsStore._decision_row` (one
#: decision) and :meth:`ChunkDecisionsStore.live_decisions_for` (bulk) read, so a fifth
#: closure fact only needs adding here.
_DECISION_CLOSURE_TABLES = (s.transitions, s.chunk_migrations, s.escalations, s.chunk_restarts)


class ChunkDecisionsStore:
    """The chunk's decision facts."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def get_decision(self, decision_id: str) -> GateDecision | None:
        with self._store.read("get_decision") as conn:
            row = conn.execute(select(s.decisions).where(s.decisions.c.decision_id == decision_id)).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> GateDecision | None:
        with self._store.read("find_decision") as conn:
            row = conn.execute(
                select(s.decisions).where(
                    (s.decisions.c.chunk_id == chunk_id)
                    & (s.decisions.c.node_id == node_id)
                    & (s.decisions.c.epoch == epoch)
                )
            ).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    def decision_for_chunk(self, chunk_id: str) -> GateDecision | None:
        """The newest not-yet-transitioned decision is live — filtered in SQL via
        :meth:`_not_closed_clause`, so only the one surviving row (if any) ever gets
        hydrated."""
        with self._store.read("decision_for_chunk") as conn:
            row = conn.execute(
                select(s.decisions)
                .where(s.decisions.c.chunk_id == chunk_id, self._not_closed_clause())
                .order_by(s.decisions.c.submitted_at.desc(), s.decisions.c.decision_id.desc())
                .limit(1)
            ).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    @staticmethod
    def _not_closed_clause():  # type: ignore[no-untyped-def]
        """True when the decision is still live: no closure table holds a row for its id, ANDed across every table."""
        return and_(
            *(
                ~select(table.c.decision_id).where(table.c.decision_id == s.decisions.c.decision_id).exists()
                for table in _DECISION_CLOSURE_TABLES
            )
        )

    def live_decisions_for(self, chunk_ids: Iterable[str]) -> dict[str, LiveDecisionStatus]:
        """See :meth:`~blizzard.hub.domain.chunk.ports.decisions.IReadChunkDecisionsRepository.live_decisions_for` —
        set-based throughout, unlike :meth:`_decision_row`'s per-decision
        choices reads, sharing its closure rule via :func:`decision_closure_ids`.
        Newest-first per chunk, same "newest not-yet-transitioned" semantics as
        :meth:`decision_for_chunk`. Batched (`bzh:bulk-reconstitution`)."""
        ids = list(chunk_ids)
        if not ids:
            return {}
        with self._store.read("live_decisions_for") as conn:
            result: dict[str, LiveDecisionStatus] = {}
            for batch in id_batches(ids):
                result.update(self._live_decisions_batch(conn, batch))
            return result

    def _live_decisions_batch(self, conn: Connection, chunk_ids: Sequence[str]) -> dict[str, LiveDecisionStatus]:
        rows = conn.execute(
            select(s.decisions).where(s.decisions.c.chunk_id.in_(chunk_ids)).order_by(s.decisions.c.submitted_at.desc())
        ).all()
        if not rows:
            return {}
        decision_ids = [row.decision_id for row in rows]
        resolved_choice_of: dict[str, str] = {}
        for id_batch in id_batches(decision_ids):
            resolved_choice_of.update(
                {
                    r.decision_id: r.choice
                    for r in conn.execute(
                        select(s.decision_resolutions.c.decision_id, s.decision_resolutions.c.choice).where(
                            s.decision_resolutions.c.decision_id.in_(id_batch)
                        )
                    ).all()
                }
            )
        transitioned_ids = decision_closure_ids(conn, decision_ids)
        result: dict[str, LiveDecisionStatus] = {}
        for row in rows:  # newest-first; the newest not-yet-transitioned decision is live
            if row.chunk_id in result or row.decision_id in transitioned_ids:
                continue
            result[row.chunk_id] = LiveDecisionStatus(
                decision_id=row.decision_id,
                node_id=row.node_id,
                epoch=row.epoch,
                resolved_choice=resolved_choice_of.get(row.decision_id),
                transitioned=False,
            )
        return result

    def list_open_decisions(self) -> list[GateDecision]:
        """The gates still awaiting a resolution, hydrated in one batched pass through :meth:`_hydrate`.
        The SQL drops resolved gates and gates whose chunk has ended — a read optimization mirroring
        :meth:`GateDecision.require_resolvable`, pinned by ``tests/test_open_decisions_closure.py`` —
        and :attr:`GateDecision.is_open` drops the ones a transition closed undecided."""
        not_resolved = ~(
            select(s.decision_resolutions.c.decision_id)
            .where(s.decision_resolutions.c.decision_id == s.decisions.c.decision_id)
            .exists()
        )
        with self._store.read("list_open_decisions") as conn:
            rows = conn.execute(
                select(s.decisions)
                .where(not_resolved & ~chunk_is_terminal(s.decisions.c.chunk_id))
                .order_by(s.decisions.c.submitted_at, s.decisions.c.decision_id)
            ).all()
            return [d for d in self._hydrate(conn, rows) if d.is_open]

    def record_decision(
        self,
        *,
        decision_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[StoredArtifact],
        imposed_by_runner_id: str | None,
    ) -> FenceRefusal | None:
        with self._store.write("record_decision") as conn:
            lock_chunk_row(conn, chunk_id)
            return self._record_decision_conn(
                conn,
                decision_id=decision_id,
                chunk_id=chunk_id,
                node_id=node_id,
                node_name=node_name,
                epoch=epoch,
                admission=admission,
                claimant=claimant,
                choices=choices,
                at=at,
                artifacts=artifacts,
                imposed_by_runner_id=imposed_by_runner_id,
            )

    def record_decision_locked(
        self,
        handle: ILockedChunkRead,
        *,
        decision_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[StoredArtifact],
        imposed_by_runner_id: str | None,
    ) -> bool | FenceRefusal:
        """:meth:`record_decision` on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``). A decision already open at ``(chunk_id, node_id, epoch)``
        answers ``False`` — the lost-ack replay, nothing written; ``True`` is a fresh write."""
        conn = conn_of(handle)
        if self._decision_exists(conn, chunk_id, node_id=node_id, epoch=epoch):
            return False
        refusal = self._record_decision_conn(
            conn,
            decision_id=decision_id,
            chunk_id=chunk_id,
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            admission=admission,
            claimant=claimant,
            choices=choices,
            at=at,
            artifacts=artifacts,
            imposed_by_runner_id=imposed_by_runner_id,
        )
        return refusal if refusal is not None else True

    def _record_decision_conn(
        self,
        conn: Connection,
        *,
        decision_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        claimant: Claimant | None = None,
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[StoredArtifact],
        imposed_by_runner_id: str | None,
    ) -> FenceRefusal | None:
        payload = json.dumps([{"name": c.name, "description": c.description} for c in choices])
        refusal = fence(conn, chunk_id, epoch=epoch, admission=admission, claimant=claimant)
        if refusal is not None:
            return refusal
        conn.execute(
            s.decisions.insert().values(
                decision_id=decision_id,
                chunk_id=chunk_id,
                node_id=node_id,
                node_name=node_name,
                epoch=epoch,
                choices=payload,
                submitted_at=at,
                imposed_by_runner_id=imposed_by_runner_id,
            )
        )
        for row in artifacts:
            conn.execute(
                s.artifacts.insert().values(
                    artifact_id=row.artifact_id,
                    chunk_id=row.chunk_id,
                    node_id=row.node_id,
                    node_name=row.node_name,
                    epoch=row.epoch,
                    name=row.name,
                    kind=row.kind.value,
                    data=row.data,
                    repo=row.repo,
                    forge=row.forge,
                    produced_at=at,
                    seq=next_artifact_seq(conn, row.chunk_id),
                )
            )
        if any(is_landing_marker(row.name, row.data) for row in artifacts):
            enqueue_close_intents(conn, chunk_id, at=at)
        return None

    @staticmethod
    def _decision_exists(conn: Connection, chunk_id: str, *, node_id: str, epoch: int) -> bool:
        return (
            conn.execute(
                select(s.decisions.c.decision_id).where(
                    (s.decisions.c.chunk_id == chunk_id)
                    & (s.decisions.c.node_id == node_id)
                    & (s.decisions.c.epoch == epoch)
                )
            ).first()
            is not None
        )

    def record_decision_resolution(self, decision_id: str, *, choice: str, resolved_by: str, at: datetime) -> bool:
        with self._store.write("record_decision_resolution") as conn:
            existing = conn.execute(
                select(s.decision_resolutions.c.decision_id).where(s.decision_resolutions.c.decision_id == decision_id)
            ).one_or_none()
            if existing is not None:
                return False  # first-write-wins: the loser is told who won
            conn.execute(
                s.decision_resolutions.insert().values(
                    decision_id=decision_id, choice=choice, resolved_by=resolved_by, resolved_at=at
                )
            )
            return True

    def _decision_row(self, conn: Connection, row) -> GateDecision:  # type: ignore[no-untyped-def]
        resolution = conn.execute(
            select(s.decision_resolutions).where(s.decision_resolutions.c.decision_id == row.decision_id)
        ).one_or_none()
        transitioned = row.decision_id in decision_closure_ids(conn, [row.decision_id])
        return self._build_row(row, resolution, transitioned)

    def _hydrate(self, conn: Connection, rows: Sequence) -> list[GateDecision]:  # type: ignore[no-untyped-def]
        """``rows``' resolution/closure state, each read batched once across the
        whole list rather than once per row — :meth:`list_open_decisions`'s own
        hydration, sharing :meth:`_build_row`'s assembly with the single-row
        :meth:`_decision_row`."""
        if not rows:
            return []
        decision_ids = [row.decision_id for row in rows]
        resolutions = {}
        for batch in id_batches(decision_ids):
            resolutions.update(
                {
                    r.decision_id: r
                    for r in conn.execute(
                        select(s.decision_resolutions).where(s.decision_resolutions.c.decision_id.in_(batch))
                    ).all()
                }
            )
        transitioned_ids = decision_closure_ids(conn, decision_ids)
        return [
            self._build_row(
                row,
                resolutions.get(row.decision_id),
                row.decision_id in transitioned_ids,
            )
            for row in rows
        ]

    @staticmethod
    def _build_row(row, resolution, transitioned: bool) -> GateDecision:  # type: ignore[no-untyped-def]
        """The one ``decisions`` row + resolution + transitioned flag ->
        :class:`GateDecision` assembly, shared by :meth:`_decision_row` (one row) and
        :meth:`_hydrate` (batched) so neither keeps its own copy of the shape."""
        choices = [DecisionChoice(name=c["name"], description=c["description"]) for c in json.loads(row.choices)]
        return GateDecision(
            decision_id=row.decision_id,
            chunk_id=row.chunk_id,
            node_id=row.node_id,
            node_name=row.node_name,
            epoch=row.epoch,
            choices=choices,
            submitted_at=row.submitted_at,
            resolved_choice=resolution.choice if resolution is not None else None,
            resolved_by=resolution.resolved_by if resolution is not None else None,
            resolved_at=resolution.resolved_at if resolution is not None else None,
            transitioned=transitioned,
            imposed_by_runner_id=row.imposed_by_runner_id,
        )


def decision_closure_ids(conn: Connection, decision_ids: Sequence[str]) -> set[str]:
    """The ids among ``decision_ids`` closed by a fact in :data:`_DECISION_CLOSURE_TABLES`
    — :meth:`ChunkDecisionsStore._not_closed_clause`'s own predicate, inverted, so every
    closure read (the live decision, the bulk status read, the chunk facts) shares one rule."""
    if not decision_ids:
        return set()
    closed: set[str] = set()
    for batch in id_batches(decision_ids):
        rows = conn.execute(
            select(s.decisions.c.decision_id).where(
                s.decisions.c.decision_id.in_(batch), ~ChunkDecisionsStore._not_closed_clause()
            )
        ).all()
        closed |= {r.decision_id for r in rows}
    return closed


def _conforms_decisions(x: ChunkDecisionsStore) -> IWriteChunkDecisionsRepository:
    return x
