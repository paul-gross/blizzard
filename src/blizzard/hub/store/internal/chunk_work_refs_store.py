"""SQLAlchemy adapter for the chunk work-refs seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.model import ChunkFacts, WorkRef, holds_work_refs
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.work_refs import IWriteChunkWorkRefsRepository, resolve_live_holders
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_rows import conn_of, ephemeral_ids, ephemeral_ids_in
from blizzard.hub.store.internal.chunk_terminal_predicates import maybe_live


class ChunkWorkRefsStore:
    """The chunk's held work refs — the pointers back to the work item(s) it serves."""

    def __init__(self, store: HubStoreConnections, clock: IClock, *, facts: ChunkFactsStore) -> None:
        self._store = store
        self._clock = clock
        self._facts = facts

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        return self.live_holders([pointer]).get(pointer)

    def live_work_refs(self) -> dict[WorkRef, ChunkStatus]:
        """Reads the fleet's statuses, then the pointer rows; a row whose chunk carries
        no status — minted in the gap between the two reads, or ephemeral — is
        excluded."""
        statuses = self._facts.load_live_statuses()
        with self._store.read("live_work_refs") as conn:
            ephemeral = ephemeral_ids(conn)
            rows = conn.execute(
                select(s.chunk_work_refs.c.chunk_id, s.chunk_work_refs.c.source, s.chunk_work_refs.c.ref)
            ).all()
        result: dict[WorkRef, ChunkStatus] = {}
        for row in rows:
            if row.chunk_id in ephemeral:
                continue  # grouped away or deleted; the pointer moved on or is withdrawn
            status = statuses.get(row.chunk_id)
            if status is None or not holds_work_refs(status):
                continue
            result[WorkRef(source=row.source, ref=row.ref)] = status
        return result

    def live_holders(self, pointers: Iterable[WorkRef]) -> dict[WorkRef, str]:
        """`find_live_holder`'s batched sibling — every pointer with a live (non-terminal)
        chunk holding it, keyed by pointer; a pointer with no live holder has no entry at
        all (not mapped to None). Statuses are read after the pointer rows' connection has
        closed, not nested inside it."""
        pointers = list(pointers)
        if not pointers:
            return {}
        with self._store.read("live_holders") as conn:
            pairs, maybe_live_ids = self._maybe_live_holders(conn, pointers)
        facts_by_id = self._facts.status_facts_for(sorted(maybe_live_ids))
        return self._resolve(pairs, maybe_live_ids, facts_by_id)

    def live_holders_conn(self, conn, pointers: Iterable[WorkRef]) -> dict[WorkRef, str]:  # type: ignore[no-untyped-def]
        """`live_holders`, resolved wholly on the caller's connection — the locked-transaction
        seam's own read (``bzh:store-exclusive-write``)."""
        pointers = list(pointers)
        if not pointers:
            return {}
        pairs, maybe_live_ids = self._maybe_live_holders(conn, pointers)
        facts_by_id = self._facts.status_facts_for_conn(conn, sorted(maybe_live_ids))
        return self._resolve(pairs, maybe_live_ids, facts_by_id)

    @staticmethod
    def _maybe_live_holders(conn, pointers: list[WorkRef]) -> tuple[list[tuple[WorkRef, str]], set[str]]:  # type: ignore[no-untyped-def]
        """Every ``(pointer, chunk_id)`` pair naming one of ``pointers``, and the subset of
        those chunk ids that are neither ephemeral nor settled terminal. ``chunk_work_refs``
        carries no cross-source uniqueness on ``ref`` alone, so pointers are grouped by
        ``source`` first and each source's refs batched through a plain single-column ``IN``
        (``bzh:sql-portable``). The pointer set bounds every read: only chunks that held one of
        these pointers are asked about."""
        refs_by_source: dict[str, list[str]] = defaultdict(list)
        for pointer in pointers:
            refs_by_source[pointer.source].append(pointer.ref)

        pairs: list[tuple[WorkRef, str]] = []
        for source, refs in refs_by_source.items():
            for batch in id_batches(refs):
                rows = conn.execute(
                    select(s.chunk_work_refs.c.chunk_id, s.chunk_work_refs.c.ref).where(
                        (s.chunk_work_refs.c.source == source) & (s.chunk_work_refs.c.ref.in_(batch))
                    )
                ).all()
                pairs.extend((WorkRef(source=source, ref=row.ref), row.chunk_id) for row in rows)
        held_ids = sorted({chunk_id for _, chunk_id in pairs})
        ephemeral = ephemeral_ids_in(conn, held_ids) if held_ids else set()
        maybe_live_ids = {
            chunk_id
            for batch in id_batches([c for c in held_ids if c not in ephemeral])
            for chunk_id in conn.execute(
                select(s.chunks.c.chunk_id).where(s.chunks.c.chunk_id.in_(batch), maybe_live())
            ).scalars()
        }
        return pairs, maybe_live_ids

    @staticmethod
    def _resolve(
        pairs: list[tuple[WorkRef, str]], maybe_live_ids: set[str], facts_by_id: Mapping[str, ChunkFacts]
    ) -> dict[WorkRef, str]:
        # A chunk outside `maybe_live_ids` is ephemeral or settled terminal, so its pairs drop
        # here — `resolve_live_holders` would read an absent status as live.
        live_pairs = [(pointer, chunk_id) for pointer, chunk_id in pairs if chunk_id in maybe_live_ids]
        statuses = {chunk_id: facts.status() for chunk_id, facts in facts_by_id.items()}
        return resolve_live_holders(live_pairs, statuses)

    def add_work_refs_locked(
        self, handle: ILockedChunkRead, chunk_id: str, pointers: list[WorkRef], *, at: datetime
    ) -> None:
        """Fold pointers into a group survivor, de-duped by (source, ref) (``bzh:store-exclusive-write``) —
        the group fold's own write, on ``handle``'s already-locked connection."""
        self._add_work_refs_conn(conn_of(handle), chunk_id, pointers)

    def _add_work_refs_conn(self, conn, chunk_id: str, pointers: list[WorkRef]) -> None:  # type: ignore[no-untyped-def]
        existing = {
            (p.source, p.ref)
            for p in conn.execute(
                select(s.chunk_work_refs.c.source, s.chunk_work_refs.c.ref).where(
                    s.chunk_work_refs.c.chunk_id == chunk_id
                )
            ).all()
        }
        for pointer in pointers:
            if (pointer.source, pointer.ref) in existing:
                continue
            conn.execute(s.chunk_work_refs.insert().values(chunk_id=chunk_id, source=pointer.source, ref=pointer.ref))
            existing.add((pointer.source, pointer.ref))


def _conforms_work_refs(x: ChunkWorkRefsStore) -> IWriteChunkWorkRefsRepository:
    return x
