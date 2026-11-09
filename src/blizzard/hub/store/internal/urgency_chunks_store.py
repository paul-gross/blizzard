"""SQLAlchemy adapter for the open chunks carrying an urgency (package-private).

A chunk carries its urgency in ``chunks.urgency_id``, read through the tenant-led ``ix_chunks_tenant_urgency_id``. Open
means non-terminal by the status the chunk's facts derive, as the chunk store derives live statuses: the
:func:`maybe_live` prefilter narrows the candidates, ephemeral chunks drop with it, and the status families' facts
settle the prefilter's residue. A move rewrites the column in place and touches nothing else of the chunk."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from sqlalchemy.engine import Connection

from blizzard.foundation.chunk_status import TERMINAL_STATUSES
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.urgency.model import UrgencyId
from blizzard.hub.domain.urgency.ports import IWriteUrgencyChunks
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_fact_loading import STATUS_FAMILIES, load_chunk_facts
from blizzard.hub.store.internal.chunk_rows import ephemeral_ids_select
from blizzard.hub.store.internal.chunk_terminal_predicates import maybe_live
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_in


def _open_holders(conn: Connection, urgency_ids: Sequence[str]) -> dict[str, str]:
    """Each open chunk carrying one of ``urgency_ids``, mapped to the urgency it carries."""
    stmts = statements_of(conn)
    candidates: dict[str, str] = {}
    for batch in id_batches(sorted(set(urgency_ids))):
        rows = conn.execute(
            stmts.select(s.chunks, s.chunks.c.chunk_id, s.chunks.c.urgency_id).where(
                s.chunks.c.urgency_id.in_(batch),
                maybe_live(stmts),
                s.chunks.c.chunk_id.not_in(ephemeral_ids_select(stmts)),
            )
        ).all()
        candidates.update((row.chunk_id, row.urgency_id) for row in rows)
    if not candidates:
        return {}
    facts = load_chunk_facts(conn, sorted(candidates), families=STATUS_FAMILIES)
    return {
        chunk_id: candidates[chunk_id] for chunk_id, held in facts.items() if held.status() not in TERMINAL_STATUSES
    }


class UrgencyChunksStore:
    """Counts and moves the open chunks carrying an urgency."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def open_counts(self, urgency_ids: Sequence[UrgencyId]) -> dict[UrgencyId, int]:
        with self._store.read("urgency_open_counts") as conn:
            counted = Counter(_open_holders(conn, urgency_ids).values())
        return {urgency_id: counted.get(urgency_id, 0) for urgency_id in urgency_ids}

    def open_chunks(self, urgency_id: UrgencyId, *, transaction: Transaction | None = None) -> list[str]:
        with connection_in(self._store, transaction, "urgency_open_chunks") as conn:
            return sorted(_open_holders(conn, [urgency_id]))

    def move(self, chunk_ids: Sequence[str], *, to: UrgencyId, transaction: Transaction | None = None) -> int:
        if not chunk_ids:
            return 0
        moved = 0
        with connection_in(self._store, transaction, "move_urgency_chunks", write=True) as conn:
            stmts = statements_of(conn)
            for batch in id_batches(list(chunk_ids)):
                moved += conn.execute(
                    stmts.update(s.chunks).where(s.chunks.c.chunk_id.in_(batch)).values(urgency_id=to)
                ).rowcount
        return moved


_urgency_chunks_store_conforms: type[IWriteUrgencyChunks] = UrgencyChunksStore
