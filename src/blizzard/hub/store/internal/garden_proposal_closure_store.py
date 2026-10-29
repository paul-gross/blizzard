"""SQLAlchemy adapter for the garden-proposal closure repository seam.
All ``sqlalchemy`` usage confined here (``bzh:dependency-inversion``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.garden_proposals import GardenProposalClosureKind, GardenProposalItemOutcome
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.garden.proposals.closure import GardenProposalClosure, IWriteGardenProposalClosureRepository
from blizzard.hub.store.schema import garden_proposal_closures, garden_proposals
from blizzard.hub.store.scoped import ScopedConnections, statements_of


def insert_garden_proposal_closure_row(
    conn: Connection,
    *,
    proposal_id: str,
    closure: GardenProposalClosureKind,
    reason: str | None,
    closed_by: str,
    at: datetime,
    item_outcome: GardenProposalItemOutcome | None,
    pointer: WorkRef | None,
) -> bool:
    """Writes one ``garden_proposal_closures`` row, on the caller's connection, inside
    the caller's transaction. Idempotent per ``proposal_id``: returns ``False`` and
    writes nothing when a closure already exists. Locks the proposal's own row before
    reading closures (``tests/test_garden_proposal_closure_race.py::
    test_insert_closure_row_locks_the_proposal_row_before_reading_closures``)."""
    stmts = statements_of(conn)
    conn.execute(
        stmts.update(garden_proposals)
        .where(garden_proposals.c.proposal_id == proposal_id)
        .values(proposal_id=proposal_id)
    )
    already = conn.execute(
        stmts.select(garden_proposal_closures, garden_proposal_closures.c.id).where(
            garden_proposal_closures.c.proposal_id == proposal_id
        )
    ).first()
    if already is not None:
        return False
    conn.execute(
        stmts.insert(garden_proposal_closures).values(
            proposal_id=proposal_id,
            closure=closure.value,
            reason=reason,
            closed_by=closed_by,
            closed_at=at,
            item_outcome=item_outcome.value if item_outcome is not None else None,
            source=pointer.source if pointer is not None else None,
            ref=pointer.ref if pointer is not None else None,
        )
    )
    return True


class GardenProposalClosureStore:
    """Read-write garden-proposal-closure adapter over the hub store engine — the pass
    and accept-declining-to-mint writes, plus every closure read."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def get(self, proposal_id: str) -> GardenProposalClosure | None:
        with self._store.read("get") as conn:
            row = conn.execute(
                self._store.select(garden_proposal_closures).where(
                    garden_proposal_closures.c.proposal_id == proposal_id
                )
            ).one_or_none()
        return self._of(row) if row is not None else None

    def get_many(self, proposal_ids: Sequence[str]) -> dict[str, GardenProposalClosure]:
        """`get`'s batched sibling (`bzh:bulk-reconstitution`), one query per `id_batches`
        batch over `proposal_ids`."""
        if not proposal_ids:
            return {}
        result: dict[str, GardenProposalClosure] = {}
        with self._store.read("get_many") as conn:
            for batch in id_batches(proposal_ids):
                rows = conn.execute(
                    self._store.select(garden_proposal_closures).where(
                        garden_proposal_closures.c.proposal_id.in_(batch)
                    )
                ).all()
                result.update({row.proposal_id: self._of(row) for row in rows})
        return result

    def find_by_item(self, source: str, ref: str) -> GardenProposalClosure | None:
        """Filtered on `ix_garden_proposal_closures_tenant_source_ref` — `source`/`ref` are
        null on a pass or a declined accept, so an unminted item never joins a row here
        regardless of what a caller passes."""
        with self._store.read("find_by_item") as conn:
            row = conn.execute(
                self._store.select(garden_proposal_closures).where(
                    garden_proposal_closures.c.source == source, garden_proposal_closures.c.ref == ref
                )
            ).one_or_none()
        return self._of(row) if row is not None else None

    def record_pass(self, proposal_id: str, *, reason: str, closed_by: str, at: datetime) -> bool:
        try:
            with self._store.write("record_pass", expect=(IntegrityError,)) as conn:
                return insert_garden_proposal_closure_row(
                    conn,
                    proposal_id=proposal_id,
                    closure=GardenProposalClosureKind.PASSED,
                    reason=reason,
                    closed_by=closed_by,
                    at=at,
                    item_outcome=None,
                    pointer=None,
                )
        except IntegrityError:
            return False

    def record_accept_decline(self, proposal_id: str, *, reason: str | None, closed_by: str, at: datetime) -> bool:
        try:
            with self._store.write("record_accept_decline", expect=(IntegrityError,)) as conn:
                return insert_garden_proposal_closure_row(
                    conn,
                    proposal_id=proposal_id,
                    closure=GardenProposalClosureKind.ACCEPTED,
                    reason=reason,
                    closed_by=closed_by,
                    at=at,
                    item_outcome=GardenProposalItemOutcome.DECLINED,
                    pointer=None,
                )
        except IntegrityError:
            return False

    @staticmethod
    def _of(row: Row[Any]) -> GardenProposalClosure:
        return GardenProposalClosure(
            proposal_id=row.proposal_id,
            closure=GardenProposalClosureKind(row.closure),
            reason=row.reason,
            closed_by=row.closed_by,
            closed_at=row.closed_at,
            item_outcome=GardenProposalItemOutcome(row.item_outcome) if row.item_outcome is not None else None,
            source=row.source,
            ref=row.ref,
        )


_garden_proposal_closure_store_conforms: type[IWriteGardenProposalClosureRepository] = GardenProposalClosureStore
