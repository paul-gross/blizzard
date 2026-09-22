"""SQLAlchemy adapter for the review-findings-materialization seam (package-private,
blizzard#582). All ``sqlalchemy`` usage is confined here (``bzh:dependency-
inversion``). One ``store.write`` transaction per :meth:`ReviewFindingsStore.deliver`
call — every row a :class:`ReviewFindingsPlan` carries, plus any scope it names and its
own idempotence marker, land together or not at all (D6)."""

from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import ARTIFACT_PREFIX, Id
from blizzard.hub.domain.review_findings_materialize import (
    IWriteReviewFindingsRepository,
    ReviewFindingsOutcome,
    ReviewFindingsPlan,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import artifacts, finding_facts, findings, scopes

#: Keyed on `chunk_id` alone (D6): a chunk owes at most one review-findings delivery.
_DELIVERED_MARKER_NAME = "review-findings-delivered"


class ReviewFindingsStore:
    """Read-write review-findings-materialization adapter over the hub store engine. No
    clock (`bzh:injected-clock`) — every timestamp arrives already stamped on
    `ReviewFindingsPlan.at`."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def already_delivered(self, *, chunk_id: str) -> bool:
        with self._store.read("already_delivered") as conn:
            return self._marker(conn, chunk_id=chunk_id) is not None

    @staticmethod
    def _marker(conn, *, chunk_id: str):  # type: ignore[no-untyped-def]
        return conn.execute(
            select(artifacts.c.artifact_id).where(
                (artifacts.c.chunk_id == chunk_id) & (artifacts.c.name == _DELIVERED_MARKER_NAME)
            )
        ).first()

    @staticmethod
    def _mint_scope_if_unseen(conn, slug: str, description: str, at) -> None:  # type: ignore[no-untyped-def]
        """Mint `slug` if this select finds it unseen. The insert runs in its own
        savepoint, not the outer `deliver` transaction, so a concurrent mint of the same
        slug loses the race with an `IntegrityError` that rolls back only this nested
        write — the rest of the delivery still commits, first-write-wins like
        `ScopeStore.ensure` (D2). Invisible on sqlite, which serializes writers."""
        existing = conn.execute(select(scopes.c.slug).where(scopes.c.slug == slug)).first()
        if existing is not None:
            return
        try:
            with conn.begin_nested():
                conn.execute(insert(scopes).values(slug=slug, description=description, created_at=at))
        except IntegrityError:
            pass

    def deliver(self, plan: ReviewFindingsPlan) -> ReviewFindingsOutcome:
        with self._store.write("deliver") as conn:
            if self._marker(conn, chunk_id=plan.chunk_id) is not None:
                return ReviewFindingsOutcome.ALREADY_RECORDED

            for scope_slug in plan.scope_slugs:
                self._mint_scope_if_unseen(conn, scope_slug, plan.new_scope_description, plan.at)

            if plan.new_findings:
                conn.execute(
                    insert(findings),
                    [
                        {
                            "finding_id": f.finding_id,
                            "routine_name": None,
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
                    insert(finding_facts),
                    [
                        {
                            "finding_id": fact.finding_id,
                            "kind": "add",
                            "recorded_at": plan.at,
                            "note": None,
                            "finding_set_id": None,  # a review delta mints no finding set (D4)
                            "ref": fact.ref,
                        }
                        for fact in plan.facts
                    ],
                )

            conn.execute(
                insert(artifacts).values(
                    artifact_id=Id.mint_at(ARTIFACT_PREFIX, plan.at).value,
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
                )
            )
            return ReviewFindingsOutcome.RECORDED


def _conforms_review_findings_store(x: ReviewFindingsStore) -> IWriteReviewFindingsRepository:
    return x
