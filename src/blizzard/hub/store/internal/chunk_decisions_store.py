"""SQLAlchemy adapter for the chunk decisions seam (package-private, blizzard#411 Phase 3).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, select
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.chunks.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.work import DecisionChoice, DecisionRow, DocketEntry
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import enqueue_close_intents, insert_proposals, proposal_row

# The generic ``merged/<repo>`` landing marker (issue #67) — mirrors domain/work.py's own
# copy (``LandedRepos``'s), which reads it back; each side owns its own constant.
_MARKER_PREFIX = "merged/"


class ChunkDecisionsStore:
    """The chunk's decision facts and their docket of pending proposals."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def get_decision(self, decision_id: str) -> DecisionRow | None:
        with self._store.read("get_decision") as conn:
            row = conn.execute(select(s.decisions).where(s.decisions.c.decision_id == decision_id)).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    def find_decision(self, chunk_id: str, *, node_id: str, epoch: int) -> DecisionRow | None:
        with self._store.read("find_decision") as conn:
            row = conn.execute(
                select(s.decisions).where(
                    (s.decisions.c.chunk_id == chunk_id)
                    & (s.decisions.c.node_id == node_id)
                    & (s.decisions.c.epoch == epoch)
                )
            ).one_or_none()
            return self._decision_row(conn, row) if row is not None else None

    def decision_for_chunk(self, chunk_id: str) -> DecisionRow | None:
        with self._store.read("decision_for_chunk") as conn:
            rows = conn.execute(
                select(s.decisions)
                .where(s.decisions.c.chunk_id == chunk_id)
                .order_by(s.decisions.c.submitted_at.desc())
            ).all()
            for row in rows:  # newest-first; the newest not-yet-transitioned decision is live
                decision = self._decision_row(conn, row)
                if not decision.transitioned:
                    return decision
            return None

    def list_open_decisions(self) -> list[DecisionRow]:
        with self._store.read("list_open_decisions") as conn:
            rows = conn.execute(select(s.decisions).order_by(s.decisions.c.submitted_at)).all()
            decisions = [self._decision_row(conn, row) for row in rows]
            return [d for d in decisions if not d.resolved]

    def record_decision(
        self,
        *,
        decision_id: str,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        choices: list[DecisionChoice],
        at: datetime,
        artifacts: list[ArtifactRow],
        proposals: list[WorkItemProposalRow],
    ) -> None:
        payload = json.dumps([{"name": c.name, "description": c.description} for c in choices])
        with self._store.write("record_decision") as conn:
            conn.execute(
                s.decisions.insert().values(
                    decision_id=decision_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    node_name=node_name,
                    epoch=epoch,
                    choices=payload,
                    submitted_at=at,
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
                    )
                )
            insert_proposals(conn, proposals, at=at)
            if any(row.name.startswith(_MARKER_PREFIX) for row in artifacts):
                enqueue_close_intents(conn, chunk_id, at=at)

    def record_decision_resolution(
        self, decision_id: str, *, choice: str, resolved_by: str, at: datetime, struck: Sequence[str] = ()
    ) -> bool:
        with self._store.write("record_decision_resolution") as conn:
            existing = conn.execute(
                select(s.decision_resolutions.c.decision_id).where(s.decision_resolutions.c.decision_id == decision_id)
            ).one_or_none()
            if existing is not None:
                return False  # first-write-wins: the loser is told who won, and writes no strike
            conn.execute(
                s.decision_resolutions.insert().values(
                    decision_id=decision_id, choice=choice, resolved_by=resolved_by, resolved_at=at
                )
            )
            for proposal_id in struck:
                # `proposal_id` is a bare primary key, not scoped to this decision — a
                # chunk can carry more than one unresolved decision sharing the same
                # docket, and a concurrently resolved sibling may have struck this same
                # id first. A savepoint keeps that a no-op instead of raising and rolling
                # back this decision's own resolution too.
                try:
                    with conn.begin_nested():
                        conn.execute(
                            s.work_item_strikes.insert().values(
                                proposal_id=proposal_id, decision_id=decision_id, struck_by=resolved_by, struck_at=at
                            )
                        )
                except IntegrityError:
                    pass
            return True

    def _decision_row(self, conn: Connection, row) -> DecisionRow:  # type: ignore[no-untyped-def]
        resolution = conn.execute(
            select(s.decision_resolutions).where(s.decision_resolutions.c.decision_id == row.decision_id)
        ).one_or_none()
        # Closed by whichever fact carries this decision_id: the resolving transition, the
        # migration (#90), the unresolvable-target escalation (#110), or the restart (#370).
        transitioned = (
            conn.execute(
                select(s.transitions.c.transition_id).where(s.transitions.c.decision_id == row.decision_id).limit(1)
            ).first()
            is not None
            or conn.execute(
                select(s.chunk_migrations.c.migration_id)
                .where(s.chunk_migrations.c.decision_id == row.decision_id)
                .limit(1)
            ).first()
            is not None
            or conn.execute(
                select(s.escalations.c.id).where(s.escalations.c.decision_id == row.decision_id).limit(1)
            ).first()
            is not None
            or conn.execute(
                select(s.chunk_restarts.c.id).where(s.chunk_restarts.c.decision_id == row.decision_id).limit(1)
            ).first()
            is not None
        )
        choices = [DecisionChoice(name=c["name"], description=c["description"]) for c in json.loads(row.choices)]
        return DecisionRow(
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
            docket=self._pending_proposals(conn, row.chunk_id),
        )

    @staticmethod
    def _pending_proposals(conn: Connection, chunk_id: str) -> list[DocketEntry]:
        """The docket read, on a caller-supplied ``conn`` so :meth:`_decision_row` can
        fold it into its own already-open read."""
        judged = {r.proposal_id for r in conn.execute(select(s.work_item_materializations.c.proposal_id)).all()}
        strikes = {
            r.proposal_id: r
            for r in conn.execute(
                select(s.work_item_strikes).where(
                    s.work_item_strikes.c.proposal_id.in_(
                        select(s.work_item_proposals.c.proposal_id).where(s.work_item_proposals.c.chunk_id == chunk_id)
                    )
                )
            ).all()
        }
        rows = conn.execute(select(s.work_item_proposals).where(s.work_item_proposals.c.chunk_id == chunk_id)).all()
        return [
            DocketEntry(
                proposal=proposal_row(row),
                struck=row.proposal_id in strikes,
                struck_by=strikes[row.proposal_id].struck_by if row.proposal_id in strikes else None,
                struck_at=strikes[row.proposal_id].struck_at if row.proposal_id in strikes else None,
            )
            for row in rows
            if row.proposal_id not in judged
        ]


def _conforms_decisions(x: ChunkDecisionsStore) -> IWriteChunkDecisionsRepository:
    return x
