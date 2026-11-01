"""SQLAlchemy adapter for the review-findings-materialization seam (package-private).
All ``sqlalchemy`` usage is confined here (``bzh:dependency-
inversion``). One ``store.write`` transaction per :meth:`ReviewFindingsStore.deliver`
call — every row a :class:`ReviewFindingsPlan` carries, plus any scope it names (minted in the
reviewed chunk's project, with its ``create`` change) and its own idempotence marker, land together
or not at all."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.review.materialize import (
    IReadReviewFindingsRepository,
    IWriteReviewFindingsRepository,
    ReviewFindingsOutcome,
    ReviewFindingsPlan,
)
from blizzard.hub.domain.garden.scopes import ScopeMint

# Legacy review delivery fences chunk writes and sequences artifacts atomically.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_rows import fence, lock_chunk_row, next_artifact_seq

# Legacy review delivery resolves and creates scopes in its write transaction.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.scope_store import ensure_scope, scope_ids_of
from blizzard.hub.store.schema import artifacts, chunks, finding_facts, findings
from blizzard.hub.store.scoped import ScopedConnections, statements_of

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
    def _mint_scope_if_unseen(conn: Connection, mint: ScopeMint) -> None:
        """Mint the scope — a fresh `scp_` id stamped at the plan's instant — with its `create` change if
        its project holds no scope of its slug. The write runs in its own savepoint, not the outer `deliver`
        transaction, so a concurrent mint of the same slug loses the race on `uq_scopes_project_slug` with an
        `IntegrityError` that rolls back only this nested
        write — the rest of the delivery still commits, first-write-wins like
        `ScopeStore.ensure`. Invisible on sqlite, which serializes writers."""
        try:
            with conn.begin_nested():
                ensure_scope(conn, mint)
        except IntegrityError:
            pass

    @staticmethod
    def _project_of(conn: Connection, chunk_id: str) -> str:
        """The project of the reviewed chunk, read in the delivery's own transaction."""
        project_id: str = conn.execute(
            statements_of(conn).select(chunks, chunks.c.project_id).where(chunks.c.chunk_id == chunk_id)
        ).scalar_one()
        return project_id

    def deliver(self, plan: ReviewFindingsPlan, *, admission: EpochAdmission) -> ReviewFindingsOutcome:
        with self._store.write("deliver") as conn:
            lock_chunk_row(conn, plan.chunk_id)
            if self._marker(conn, chunk_id=plan.chunk_id) is not None:
                return ReviewFindingsOutcome.ALREADY_RECORDED
            if fence(conn, plan.chunk_id, epoch=plan.epoch, admission=admission) is not None:
                return ReviewFindingsOutcome.FENCED

            project_id = self._project_of(conn, plan.chunk_id)
            for scope_slug in plan.scope_slugs:
                self._mint_scope_if_unseen(conn, plan.scope_mint(scope_slug, project_id))

            if plan.new_findings:
                scope_ids = scope_ids_of(conn, [f.scope_slug for f in plan.new_findings], project_id)
                conn.execute(
                    self._store.insert(findings).values(project_id=project_id),
                    [
                        {
                            "finding_id": f.finding_id,
                            "routine_name": None,
                            "scope_id": scope_ids.get(f.scope_slug),
                            "scope_slug": f.scope_slug,
                            "class_": f.class_,
                            "locus": f.locus,
                            "summary": f.summary,
                            "introduced": None,
                            "introduced_at": None,
                            "source": "review",
                            "severity": f.severity,
                            "raised_by_chunk_id": f.raised_by_chunk_id,
                        }
                        for f in plan.new_findings
                    ],
                )
            if plan.facts:
                conn.execute(
                    self._store.insert(finding_facts),
                    [
                        {
                            "finding_id": fact.finding_id,
                            "kind": "add",
                            "recorded_at": plan.at,
                            "note": None,
                            "finding_set_id": None,  # a review delta mints no finding set
                            "ref": fact.ref,
                        }
                        for fact in plan.facts
                    ],
                )

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
            return ReviewFindingsOutcome.RECORDED


_review_findings_store_conforms: type[IWriteReviewFindingsRepository] = ReviewFindingsStore


_review_findings_store_read_conforms: type[IReadReviewFindingsRepository] = ReviewFindingsStore
