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
from blizzard.hub.domain.chunks.work_refs import IWriteChunkWorkRefsRepository
from blizzard.hub.domain.work import ChunkFacts, WorkRef
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
        with self._store.read("find_live_holder") as conn:
            ephemeral = ephemeral_ids(conn)
            chunk_ids = [
                p.chunk_id
                for p in conn.execute(
                    select(s.chunk_work_refs.c.chunk_id).where(
                        (s.chunk_work_refs.c.source == pointer.source) & (s.chunk_work_refs.c.ref == pointer.ref)
                    )
                ).all()
            ]
        for chunk_id in chunk_ids:
            if chunk_id in ephemeral:
                continue  # grouped away or deleted; the pointer moved on or is withdrawn
            if self._status(chunk_id) not in TERMINAL_STATUSES:
                return chunk_id
        return None

    def live_work_refs(self) -> dict[WorkRef, ChunkStatus]:
        with self._store.read("live_work_refs") as conn:
            ephemeral = ephemeral_ids(conn)
            rows = conn.execute(
                select(s.chunk_work_refs.c.chunk_id, s.chunk_work_refs.c.source, s.chunk_work_refs.c.ref)
            ).all()
        result: dict[WorkRef, ChunkStatus] = {}
        for row in rows:
            if row.chunk_id in ephemeral:
                continue  # grouped away or deleted; the pointer moved on or is withdrawn
            status = self._status(row.chunk_id)
            if status in TERMINAL_STATUSES:
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

        with self._store.read("live_holders") as conn:
            # Unfiltered — the same whole-fleet scan `live_work_refs` already pays,
            # kept unbatched here too rather than re-derived per pointer.
            ephemeral = ephemeral_ids(conn)
            candidates: dict[WorkRef, list[str]] = defaultdict(list)
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
                        candidates[WorkRef(source=source, ref=row.ref)].append(row.chunk_id)

            candidate_ids = sorted({chunk_id for chunk_ids in candidates.values() for chunk_id in chunk_ids})
            facts_by_id = self._facts.load_facts_for(candidate_ids)

        result: dict[WorkRef, str] = {}
        for pointer, chunk_ids in candidates.items():
            live = sorted(c for c in chunk_ids if self._status_from(facts_by_id, c) not in TERMINAL_STATUSES)
            if live:
                result[pointer] = live[0]  # more than one live holder shouldn't happen; pick deterministically
        return result

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

    def _status(self, chunk_id: str) -> ChunkStatus:
        facts = self._facts.load_facts(chunk_id)
        return facts.status() if facts is not None else ChunkStatus.READY

    @staticmethod
    def _status_from(facts_by_id: dict[str, ChunkFacts], chunk_id: str) -> ChunkStatus:
        """`_status`'s batched sibling — the same ``ChunkStatus.READY`` fallback for a
        chunk id ``load_facts_for`` didn't return (unrecorded facts), reading from an
        already-loaded map instead of issuing its own per-chunk query."""
        facts = facts_by_id.get(chunk_id)
        return facts.status() if facts is not None else ChunkStatus.READY


def _conforms_work_refs(x: ChunkWorkRefsStore) -> IWriteChunkWorkRefsRepository:
    return x
