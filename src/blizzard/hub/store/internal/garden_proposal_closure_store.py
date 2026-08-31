"""SQLAlchemy adapter for the garden-proposal closure repository seam (blizzard#395).
All ``sqlalchemy`` usage confined here (``bzh:dependency-inversion``). The
accept-with-mint write lives in ``WorkItemStore.accept_create`` instead, reaching
:func:`insert_garden_proposal_closure_row` the same way
``blizzard.hub.store.internal.chunk_store.insert_materialization_row`` is reached from
outside its own owning adapter — a deliberate two-adapter split, mirroring
``work_item_materializations``: only the item's own adapter can enclose the item and
chunk inserts in the accept-with-mint transaction."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection, insert, select

from blizzard.hub.domain.garden_proposal_closure import (
    GardenProposalClosure,
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
    IWriteGardenProposalClosureRepository,
)
from blizzard.hub.domain.work import WorkRef
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import garden_proposal_closures


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
    """Insert one ``garden_proposal_closures`` row on a caller-supplied ``conn`` —
    mirrors ``chunk_store.insert_materialization_row``'s shared-connection shape, so a
    composite write can fold this into its own transaction. Idempotent per
    ``proposal_id``: returns ``False`` and writes nothing when a closure already exists."""
    already = conn.execute(
        select(garden_proposal_closures.c.id).where(garden_proposal_closures.c.proposal_id == proposal_id)
    ).first()
    if already is not None:
        return False
    conn.execute(
        insert(garden_proposal_closures).values(
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

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def get(self, proposal_id: str) -> GardenProposalClosure | None:
        with self._store.read("get") as conn:
            row = conn.execute(
                select(garden_proposal_closures).where(garden_proposal_closures.c.proposal_id == proposal_id)
            ).one_or_none()
        return self._of(row) if row is not None else None

    def record_pass(self, proposal_id: str, *, reason: str, closed_by: str, at: datetime) -> bool:
        with self._store.write("record_pass") as conn:
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

    def record_accept_decline(self, proposal_id: str, *, reason: str | None, closed_by: str, at: datetime) -> bool:
        with self._store.write("record_accept_decline") as conn:
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

    @staticmethod
    def _of(row) -> GardenProposalClosure:  # type: ignore[no-untyped-def]
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


def _conforms_garden_proposal_closure_store(x: GardenProposalClosureStore) -> IWriteGardenProposalClosureRepository:
    return x
