"""SQLAlchemy adapter for the chunk decisions seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Connection, Row, Select, and_

from blizzard.foundation.clock import IClock
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import (
    CloseEnqueueDecision,
    DecisionCheck,
    DecisionChoice,
    GateDecision,
    GateResolution,
)
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository, LiveDecisionStatus
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import (
    conn_of,
    enqueue_close_intents,
    fence,
    is_landing_marker,
    lock_chunk_row,
    next_artifact_seq,
)
from blizzard.hub.store.internal.chunk_terminal_predicates import chunk_is_terminal
from blizzard.hub.store.internal.newest_fact import newer_row_exists
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements, statements_of

#: Every fact table whose ``decision_id`` column closes a decision — the resolving
#: transition, the migration (#90), the unresolvable-target escalation (#110), or the
#: restart (#370). The one source both :meth:`ChunkDecisionsStore._decision_row` (one
#: decision) and :meth:`ChunkDecisionsStore.live_decisions_for` (bulk) read, so a fifth
#: closure fact only needs adding here.
_DECISION_CLOSURE_TABLES = (s.transitions, s.chunk_migrations, s.escalations, s.chunk_restarts)


class ChunkDecisionsStore:
    """The chunk's decision facts."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def get_decision(self, decision_id: str) -> GateDecision | None:
        with self._store.read("get_decision") as conn:
            row = conn.execute(
                self._store.select(s.decisions).where(s.decisions.c.decision_id == decision_id)
            ).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> GateDecision | None:
        with self._store.read("find_decision") as conn:
            row = conn.execute(
                self._store.select(s.decisions).where(
                    (s.decisions.c.chunk_id == chunk_id)
                    & (s.decisions.c.node_id == node_id)
                    & (s.decisions.c.epoch == epoch)
                )
            ).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    def decision_for_chunk(self, chunk_id: str) -> GateDecision | None:
        """The newest not-yet-transitioned decision is live — picked in SQL by
        :meth:`_newest_live_decisions_select`, so only the one surviving row (if any) ever gets
        hydrated."""
        with self._store.read("decision_for_chunk") as conn:
            row = conn.execute(self._newest_live_decisions_select([chunk_id])).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    def resolutions_for_chunk(self, chunk_id: str) -> list[GateResolution]:
        """One join of the chunk's decisions to their resolutions — never a lookup per decision."""
        d, r = s.decisions, s.decision_resolutions
        with self._store.read("resolutions_for_chunk") as conn:
            stmts = statements_of(conn)
            rows = conn.execute(
                stmts.select(
                    stmts.scope_join(d, r, r.c.decision_id == d.c.decision_id),
                    d.c.decision_id,
                    d.c.node_id,
                    d.c.node_name,
                    d.c.epoch,
                    r.c.choice,
                    r.c.resolved_by,
                    r.c.resolved_at,
                    r.c.overrode_red_checks,
                    r.c.note,
                ).where(d.c.chunk_id == chunk_id)
            ).all()
        # Ordered here, not in SQL: an ORDER BY on the resolution's instant would drive the read off the
        # tenant-wide resolutions index instead of the chunk's own decisions.
        rows = sorted(rows, key=lambda row: (row.resolved_at, row.decision_id))
        return [
            GateResolution(
                decision_id=row.decision_id,
                node_id=row.node_id,
                node_name=row.node_name,
                epoch=row.epoch,
                choice=row.choice,
                resolved_by=row.resolved_by,
                resolved_at=row.resolved_at,
                overrode_red_checks=row.overrode_red_checks,
                note=row.note,
            )
            for row in rows
        ]

    def _newest_live_decisions_select(self, chunk_ids: Sequence[str]) -> Select[Any]:
        """The newest not-yet-transitioned decision of each chunk in ``chunk_ids``, one row per chunk:
        a decision no newer not-closed decision of its chunk beats, in ``(submitted_at, decision_id)``
        order. The not-closed rule binds the competing row too — an older open decision under a newer
        closed one is still the answer."""
        rival = s.decisions.alias("rival")
        beaten = newer_row_exists(
            self._store,
            rival,
            partition=[(s.decisions.c.chunk_id, rival.c.chunk_id)],
            order=[
                (s.decisions.c.submitted_at, rival.c.submitted_at),
                (s.decisions.c.decision_id, rival.c.decision_id),
            ],
            where=not_closed_clause(self._store, rival.c.decision_id),
        )
        return self._store.select(s.decisions).where(
            s.decisions.c.chunk_id.in_(chunk_ids),
            not_closed_clause(self._store, s.decisions.c.decision_id),
            ~beaten,
        )

    def live_decisions_for(self, chunk_ids: Iterable[str]) -> dict[str, LiveDecisionStatus]:
        """See ``src/blizzard/hub/domain/chunk/ports/decisions.py``'s
        ``IReadChunkDecisionsRepository.live_decisions_for``."""
        ids = list(chunk_ids)
        if not ids:
            return {}
        with self._store.read("live_decisions_for") as conn:
            result: dict[str, LiveDecisionStatus] = {}
            for batch in id_batches(ids):
                result.update(self._live_decisions_batch(conn, batch))
            return result

    def _live_decisions_batch(self, conn: Connection, chunk_ids: Sequence[str]) -> dict[str, LiveDecisionStatus]:
        rows = conn.execute(self._newest_live_decisions_select(chunk_ids)).all()
        if not rows:
            return {}
        resolved_choice_of: dict[str, str] = {}
        for id_batch in id_batches([row.decision_id for row in rows]):
            resolved_choice_of.update(
                {
                    r.decision_id: r.choice
                    for r in conn.execute(
                        self._store.select(
                            s.decision_resolutions,
                            s.decision_resolutions.c.decision_id,
                            s.decision_resolutions.c.choice,
                        ).where(s.decision_resolutions.c.decision_id.in_(id_batch))
                    ).all()
                }
            )
        return {
            row.chunk_id: LiveDecisionStatus(
                decision_id=row.decision_id,
                node_id=row.node_id,
                epoch=row.epoch,
                resolved_choice=resolved_choice_of.get(row.decision_id),
                transitioned=False,
            )
            for row in rows
        }

    def list_open_decisions(self, *, project: ProjectId | None = None) -> list[GateDecision]:
        """The gates still awaiting a resolution, hydrated in one batched pass through :meth:`_hydrate`.
        The SQL drops resolved gates, fact-closed gates, and gates whose chunk has ended — mirroring
        :meth:`GateDecision.require_resolvable`, pinned by ``tests/test_open_decisions_closure.py`` —
        while :attr:`GateDecision.is_open` remains the exact final check. ``project`` narrows to one project's gates."""
        not_resolved = ~self._store.exists(
            s.decision_resolutions, s.decision_resolutions.c.decision_id == s.decisions.c.decision_id
        )
        if project is not None:
            not_resolved &= s.decisions.c.project_id == project
        with self._store.read("list_open_decisions") as conn:
            rows = conn.execute(
                self._store.select(s.decisions)
                .where(
                    not_resolved,
                    not_closed_clause(self._store, s.decisions.c.decision_id),
                    ~chunk_is_terminal(self._store, s.decisions.c.chunk_id),
                )
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
        check_results: Sequence[DecisionCheck] = (),
        at: datetime,
        artifacts: list[StoredArtifact],
        imposed_by_runner_id: str | None,
        close_decision: CloseEnqueueDecision,
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
                check_results=check_results,
                at=at,
                artifacts=artifacts,
                imposed_by_runner_id=imposed_by_runner_id,
                close_decision=close_decision,
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
        check_results: Sequence[DecisionCheck] = (),
        at: datetime,
        artifacts: list[StoredArtifact],
        imposed_by_runner_id: str | None,
        close_decision: CloseEnqueueDecision,
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
            check_results=check_results,
            at=at,
            artifacts=artifacts,
            imposed_by_runner_id=imposed_by_runner_id,
            close_decision=close_decision,
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
        check_results: Sequence[DecisionCheck] = (),
        at: datetime,
        artifacts: list[StoredArtifact],
        imposed_by_runner_id: str | None,
        close_decision: CloseEnqueueDecision,
    ) -> FenceRefusal | None:
        payload = json.dumps(
            [{"name": c.name, "description": c.description, "requires_checks": c.requires_checks} for c in choices]
        )
        checks_payload = json.dumps([{"command": c.command, "passed": c.passed} for c in check_results])
        refusal = fence(conn, chunk_id, epoch=epoch, admission=admission, claimant=claimant)
        if refusal is not None:
            return refusal
        project_id = conn.execute(
            self._store.select(s.chunks, s.chunks.c.project_id).where(s.chunks.c.chunk_id == chunk_id)
        ).scalar_one()
        conn.execute(
            self._store.insert(s.decisions).values(
                decision_id=decision_id,
                chunk_id=chunk_id,
                project_id=project_id,
                node_id=node_id,
                node_name=node_name,
                epoch=epoch,
                choices=payload,
                check_results=checks_payload,
                submitted_at=at,
                imposed_by_runner_id=imposed_by_runner_id,
            )
        )
        for row in artifacts:
            conn.execute(
                self._store.insert(s.artifacts).values(
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
            enqueue_close_intents(conn, chunk_id, at=at, close_decision=close_decision)
        return None

    @staticmethod
    def _decision_exists(conn: Connection, chunk_id: str, *, node_id: str, epoch: int) -> bool:
        return (
            conn.execute(
                statements_of(conn)
                .select(s.decisions, s.decisions.c.decision_id)
                .where(
                    (s.decisions.c.chunk_id == chunk_id)
                    & (s.decisions.c.node_id == node_id)
                    & (s.decisions.c.epoch == epoch)
                )
            ).first()
            is not None
        )

    def record_decision_resolution(
        self,
        decision_id: str,
        *,
        choice: str,
        resolved_by: str,
        at: datetime,
        overrode_red_checks: bool = False,
        note: str | None = None,
    ) -> bool:
        with self._store.write("record_decision_resolution") as conn:
            existing = conn.execute(
                self._store.select(s.decision_resolutions, s.decision_resolutions.c.decision_id).where(
                    s.decision_resolutions.c.decision_id == decision_id
                )
            ).one_or_none()
            if existing is not None:
                return False  # first-write-wins: the loser is told who won
            conn.execute(
                self._store.insert(s.decision_resolutions).values(
                    decision_id=decision_id,
                    choice=choice,
                    resolved_by=resolved_by,
                    resolved_at=at,
                    overrode_red_checks=overrode_red_checks,
                    note=note,
                )
            )
            return True

    def _decision_row(self, conn: Connection, row: Row[Any]) -> GateDecision:
        resolution = conn.execute(
            self._store.select(s.decision_resolutions).where(s.decision_resolutions.c.decision_id == row.decision_id)
        ).one_or_none()
        transitioned = row.decision_id in decision_closure_ids(conn, [row.decision_id])
        return self._build_row(row, resolution, transitioned)

    def _hydrate(self, conn: Connection, rows: Sequence[Row[Any]]) -> list[GateDecision]:
        """``rows``' resolution/closure state, each read batched once across the
        whole list rather than once per row — :meth:`list_open_decisions`'s own
        hydration, sharing :meth:`_build_row`'s assembly with the single-row
        :meth:`_decision_row`."""
        if not rows:
            return []
        decision_ids = [row.decision_id for row in rows]
        resolutions: dict[str, Row[Any]] = {}
        for batch in id_batches(decision_ids):
            resolutions.update(
                {
                    r.decision_id: r
                    for r in conn.execute(
                        self._store.select(s.decision_resolutions).where(
                            s.decision_resolutions.c.decision_id.in_(batch)
                        )
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
    def _build_row(row: Row[Any], resolution: Row[Any] | None, transitioned: bool) -> GateDecision:
        """The one ``decisions`` row + resolution + transitioned flag ->
        :class:`GateDecision` assembly, shared by :meth:`_decision_row` (one row) and
        :meth:`_hydrate` (batched) so neither keeps its own copy of the shape."""
        choices = [
            DecisionChoice(
                name=c["name"], description=c["description"], requires_checks=c.get("requires_checks", False)
            )
            for c in json.loads(row.choices)
        ]
        check_results = [DecisionCheck(command=c["command"], passed=c["passed"]) for c in json.loads(row.check_results)]
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
            project_id=row.project_id,
            check_results=check_results,
            overrode_red_checks=resolution.overrode_red_checks if resolution is not None else False,
            note=resolution.note if resolution is not None else None,
        )


def not_closed_clause(stmts: ScopedStatements, decision_id: ColumnElement[str]) -> ColumnElement[bool]:
    """True when the decision is live: no scoped closure table holds its id."""
    return and_(*(~stmts.exists(table, table.c.decision_id == decision_id) for table in _DECISION_CLOSURE_TABLES))


def decision_closure_ids(conn: Connection, decision_ids: Sequence[str] | None) -> set[str]:
    """The ids among ``decision_ids`` — every decision when ``None`` — closed by a fact in
    :data:`_DECISION_CLOSURE_TABLES` —
    :func:`not_closed_clause`'s own predicate, inverted, so every closure read (the live
    decision, the bulk status read, the chunk facts) shares one rule."""
    if decision_ids is not None and not decision_ids:
        return set()
    stmts = statements_of(conn)
    closed: set[str] = set()
    for batch in id_batches(decision_ids) if decision_ids is not None else [None]:
        stmt = stmts.select(s.decisions, s.decisions.c.decision_id).where(
            ~not_closed_clause(stmts, s.decisions.c.decision_id)
        )
        if batch is not None:
            stmt = stmt.where(s.decisions.c.decision_id.in_(batch))
        closed |= {r.decision_id for r in conn.execute(stmt).all()}
    return closed


_decisions_conforms: type[IWriteChunkDecisionsRepository] = ChunkDecisionsStore
