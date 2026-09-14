"""SQLAlchemy adapter for the chunk work-refs seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.work_refs import IWriteChunkWorkRefsRepository, resolve_live_holders
from blizzard.hub.domain.work import WorkRef
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.batching import id_batches
from blizzard.hub.store.internal.chunk_rows import ephemeral_ids


class ChunkWorkRefsStore:
    """The chunk's held work refs — the pointers back to the work item(s) it serves."""

    def __init__(self, store: HubStoreConnections, clock: IClock, *, facts: IReadChunkFactsRepository) -> None:
        self._store = store
        self._clock = clock
        self._facts = facts

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        return self.live_holders([pointer]).get(pointer)

    def live_work_refs(self) -> dict[WorkRef, ChunkStatus]:
        """Reads the fleet's statuses, then the pointer rows; a row whose chunk carries
        no status — minted in the gap between the two reads, or ephemeral — is
        excluded."""
        statuses = self._facts.load_all_statuses()
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
            if status is None or status in TERMINAL_STATUSES:
                continue
            result[WorkRef(source=row.source, ref=row.ref)] = status
        return result

    def live_holders(self, pointers: Iterable[WorkRef]) -> dict[WorkRef, str]:
        """`find_live_holder`'s batched sibling — every pointer with a live (non-terminal)
        chunk holding it, keyed by pointer; a pointer with no live holder has no entry at
        all (not mapped to None). ``chunk_work_refs`` carries no cross-source uniqueness
        on ``ref`` alone, so pointers are grouped by ``source`` first and each source's
        refs batched through a plain single-column ``IN`` (``bzh:sql-portable``)."""
        pointers = list(pointers)
        if not pointers:
            return {}
        refs_by_source: dict[str, list[str]] = defaultdict(list)
        for pointer in pointers:
            refs_by_source[pointer.source].append(pointer.ref)

        pairs: list[tuple[WorkRef, str]] = []
        with self._store.read("live_holders") as conn:
            # Unfiltered — the same whole-fleet scan `live_work_refs` already pays.
            ephemeral = ephemeral_ids(conn)
            for source, refs in refs_by_source.items():
                for batch in id_batches(refs):
                    rows = conn.execute(
                        select(s.chunk_work_refs.c.chunk_id, s.chunk_work_refs.c.ref).where(
                            (s.chunk_work_refs.c.source == source) & (s.chunk_work_refs.c.ref.in_(batch))
                        )
                    ).all()
                    for row in rows:
                        if row.chunk_id in ephemeral:
                            continue  # grouped away or deleted; the pointer moved on or is withdrawn
                        pairs.append((WorkRef(source=source, ref=row.ref), row.chunk_id))

        # Called after the read connection above has closed, not nested inside it.
        candidate_ids = sorted({chunk_id for _, chunk_id in pairs})
        facts_by_id = self._facts.load_facts_for(candidate_ids)
        statuses = {chunk_id: facts.status() for chunk_id, facts in facts_by_id.items()}
        return resolve_live_holders(pairs, statuses)

    def add_work_refs(self, chunk_id: str, pointers: list[WorkRef], *, at: datetime) -> None:
        """Fold pointers into the survivor of a group, de-duped by (source, ref)."""
        with self._store.write("add_work_refs") as conn:
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
                conn.execute(
                    s.chunk_work_refs.insert().values(chunk_id=chunk_id, source=pointer.source, ref=pointer.ref)
                )
                existing.add((pointer.source, pointer.ref))


def _conforms_work_refs(x: ChunkWorkRefsStore) -> IWriteChunkWorkRefsRepository:
    return x
