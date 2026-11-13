"""SQLAlchemy adapter for the review-findings-materialization seam (package-private).
All ``sqlalchemy`` usage is confined here (``bzh:dependency-
inversion``). Owns the review delivery's idempotence marker and its fence, read and written in
the caller's locked unit; every row a :class:`ReviewFindingsPlan` mints is written by the
repository that owns it."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, Row

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.review.materialize import (
    IReadReviewFindingsRepository,
    IWriteReviewFindingsRepository,
    ReviewAdmission,
    ReviewFindingsOutcome,
    ReviewFindingsPlan,
)
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.store.internal.chunk_rows import fence, next_artifact_seq
from blizzard.hub.store.schema import artifacts, chunks
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_for

#: Keyed on `chunk_id` alone: a chunk owes at most one review-findings delivery.
_DELIVERED_MARKER_NAME = "review-findings-delivered"


class ReviewFindingsStore:
    """Read-write review-findings-materialization adapter over the hub store engine. No
    clock (`bzh:injected-clock`) — every timestamp arrives already stamped on
    `ReviewFindingsPlan.at`."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def already_delivered(self, *, chunk_id: str) -> bool:
        with self._store.read("already_delivered") as conn:
            return self._marker(conn, chunk_id=chunk_id) is not None

    @staticmethod
    def _marker(conn: Connection, *, chunk_id: str) -> Row[Any] | None:
        return conn.execute(
            statements_of(conn)
            .select(artifacts, artifacts.c.artifact_id)
            .where((artifacts.c.chunk_id == chunk_id) & (artifacts.c.name == _DELIVERED_MARKER_NAME))
        ).first()

    @staticmethod
    def _project_of(conn: Connection, chunk_id: str) -> str:
        """The project of the reviewed chunk, read in the delivery's own transaction."""
        project_id: str = conn.execute(
            statements_of(conn).select(chunks, chunks.c.project_id).where(chunks.c.chunk_id == chunk_id)
        ).scalar_one()
        return project_id

    def admit(
        self, plan: ReviewFindingsPlan, *, admission: EpochAdmission, terminal: bool, transaction: LockedTransaction
    ) -> ReviewFindingsOutcome | ReviewAdmission:
        conn = connection_for(self._store, transaction, chunk=plan.chunk_id)
        if self._marker(conn, chunk_id=plan.chunk_id) is not None:
            return ReviewFindingsOutcome.ALREADY_RECORDED
        if fence(conn, plan.chunk_id, epoch=plan.epoch, admission=admission, terminal=terminal) is not None:
            return ReviewFindingsOutcome.FENCED
        return ReviewAdmission(project_id=self._project_of(conn, plan.chunk_id))

    def record_marker(self, plan: ReviewFindingsPlan, *, transaction: LockedTransaction) -> None:
        conn = connection_for(self._store, transaction, chunk=plan.chunk_id)
        conn.execute(
            self._store.insert(artifacts).values(
                artifact_id=Id.mint_at(IdPrefix.ARTIFACT, plan.at).value,
                chunk_id=plan.chunk_id,
                node_id=plan.node_id,
                node_name=plan.node_name,
                epoch=plan.epoch,
                name=_DELIVERED_MARKER_NAME,
                kind=ArtifactKind.ASSET.value,
                data="",
                repo=None,
                forge=None,
                produced_at=plan.at,
                seq=next_artifact_seq(conn, plan.chunk_id),
            )
        )


_review_findings_store_conforms: type[IWriteReviewFindingsRepository] = ReviewFindingsStore


_review_findings_store_read_conforms: type[IReadReviewFindingsRepository] = ReviewFindingsStore
