"""SQLAlchemy adapter for the chunk work-refs seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Label, func, select

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.model import ChunkFacts, WorkRef, holds_work_refs
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.work_refs import (
    IWriteChunkWorkRefsRepository,
    WorkRefsSignature,
    resolve_live_holders,
)
from blizzard.hub.store import schema as s

# Legacy work-reference reads compose chunk facts and their status table registry.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore, status_family_tables

# Legacy work-reference writes and reads share chunk row operations.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_rows import conn_of, ephemeral_ids, ephemeral_ids_in

# Legacy work-reference reads share the chunk liveness predicate.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_terminal_predicates import maybe_live
from blizzard.hub.store.scoped import ScopedConnections, statements_of


class ChunkWorkRefsStore:
    """The chunk's held work refs — the pointers back to the work item(s) it serves."""

    def __init__(self, store: ScopedConnections, clock: IClock, *, facts: ChunkFactsStore) -> None:
        self._store = store
        self._clock = clock
        self._facts = facts

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        return self.live_holders([pointer]).get(pointer)

    def live_work_refs(self) -> dict[WorkRef, ChunkStatus]:
        """Reads the tenant's statuses, then its pointer rows; a row whose chunk carries
        no status — minted in the gap between the two reads, or ephemeral — is excluded."""
        statuses = self._facts.load_live_statuses()
        refs = s.chunk_work_refs
        with self._store.read("live_work_refs") as conn:
            ephemeral = ephemeral_ids(conn)
            rows = conn.execute(self._store.select(refs, refs.c.chunk_id, refs.c.source, refs.c.ref)).all()
        result: dict[WorkRef, ChunkStatus] = {}
        for row in rows:
            if row.chunk_id in ephemeral:
                continue  # grouped away or deleted; the pointer moved on or is withdrawn
            status = statuses.get(row.chunk_id)
            if status is None or not holds_work_refs(status):
                continue
            result[WorkRef(source=row.source, ref=row.ref)] = status
        return result

    def live_work_refs_signature(self) -> WorkRefsSignature:
        """One statement of append-only aggregates over the tenant's rows of every table
        :meth:`live_work_refs` reads: the status families' tables, ``chunk_work_refs``, and the
        ephemeral ``chunk_grouped`` / ``chunk_deleted`` — a count each, plus the highest ``id``
        where the table has one."""
        tables = sorted(
            (*status_family_tables(), s.chunk_work_refs, s.chunk_grouped, s.chunk_deleted), key=lambda t: t.name
        )
        columns: list[Label[Any]] = []
        for table in tables:
            columns.append(self._store.select(table, func.count()).scalar_subquery().label(f"{table.name}__count"))
            if "id" in table.c:
                columns.append(
                    self._store.select(table, func.max(table.c.id)).scalar_subquery().label(f"{table.name}__max_id")
                )
        with self._store.read("live_work_refs_signature") as conn:
            row = conn.execute(select(*columns)).mappings().one()
        return WorkRefsSignature(
            marks=tuple((table.name, row[f"{table.name}__count"], row.get(f"{table.name}__max_id")) for table in tables)
        )

    def live_holders(self, pointers: Iterable[WorkRef]) -> dict[WorkRef, str]:
        """See ``src/blizzard/hub/domain/chunk/ports/work_refs.py``'s ``IReadChunkWorkRefsRepository.live_holders``.

        Statuses are read after the pointer rows' connection has closed, not nested inside it."""
        pointers = list(pointers)
        if not pointers:
            return {}
        with self._store.read("live_holders") as conn:
            pairs, maybe_live_ids = self._maybe_live_holders(conn, pointers)
        facts_by_id = self._facts.status_facts_for(sorted(maybe_live_ids))
        return self._resolve(pairs, maybe_live_ids, facts_by_id)

    def live_holders_conn(self, conn: Connection, pointers: Iterable[WorkRef]) -> dict[WorkRef, str]:
        """`live_holders`, resolved wholly on the caller's connection — the locked-transaction
        seam's own read (``bzh:store-exclusive-write``)."""
        pointers = list(pointers)
        if not pointers:
            return {}
        pairs, maybe_live_ids = self._maybe_live_holders(conn, pointers)
        facts_by_id = self._facts.status_facts_for_conn(conn, sorted(maybe_live_ids))
        return self._resolve(pairs, maybe_live_ids, facts_by_id)

    @staticmethod
    def _maybe_live_holders(conn: Connection, pointers: list[WorkRef]) -> tuple[list[tuple[WorkRef, str]], set[str]]:
        """Every ``(pointer, chunk_id)`` pair naming one of ``pointers``, and the subset of
        those chunk ids that are neither ephemeral nor settled terminal. ``chunk_work_refs``
        carries no cross-source uniqueness on ``ref`` alone, so pointers are grouped by
        ``source`` first and each source's refs batched through a plain single-column ``IN``
        (``bzh:sql-portable``). The pointer set bounds every read: only chunks that held one of
        these pointers are asked about."""
        refs_by_source: dict[str, list[str]] = defaultdict(list)
        for pointer in pointers:
            refs_by_source[pointer.source].append(pointer.ref)

        stmts = statements_of(conn)
        pairs: list[tuple[WorkRef, str]] = []
        for source, refs in refs_by_source.items():
            for batch in id_batches(refs):
                rows = conn.execute(
                    stmts.select(s.chunk_work_refs, s.chunk_work_refs.c.chunk_id, s.chunk_work_refs.c.ref).where(
                        (s.chunk_work_refs.c.source == source) & (s.chunk_work_refs.c.ref.in_(batch))
                    )
                ).all()
                pairs.extend((WorkRef(source=source, ref=row.ref), row.chunk_id) for row in rows)
        held_ids = sorted({chunk_id for _, chunk_id in pairs})
        ephemeral: set[str] = ephemeral_ids_in(conn, held_ids) if held_ids else set()
        maybe_live_ids = {
            chunk_id
            for batch in id_batches([c for c in held_ids if c not in ephemeral])
            for chunk_id in conn.execute(
                stmts.select(s.chunks, s.chunks.c.chunk_id).where(s.chunks.c.chunk_id.in_(batch), maybe_live(stmts))
            ).scalars()
        }
        return pairs, maybe_live_ids

    @staticmethod
    def _resolve(
        pairs: list[tuple[WorkRef, str]], maybe_live_ids: set[str], facts_by_id: Mapping[str, ChunkFacts]
    ) -> dict[WorkRef, str]:
        # A chunk outside `maybe_live_ids` is ephemeral or settled terminal, so its pairs drop.
        live_pairs = [(pointer, chunk_id) for pointer, chunk_id in pairs if chunk_id in maybe_live_ids]
        statuses = {chunk_id: facts.status() for chunk_id, facts in facts_by_id.items()}
        return resolve_live_holders(live_pairs, statuses)

    def add_work_refs_locked(
        self, handle: ILockedChunkRead, chunk_id: str, pointers: list[WorkRef], *, at: datetime
    ) -> None:
        """Fold pointers into a group survivor, de-duped by (source, ref) (``bzh:store-exclusive-write``) —
        the group fold's own write, on ``handle``'s already-locked connection."""
        self._add_work_refs_conn(conn_of(handle, self._store, chunk_id), chunk_id, pointers)

    def _add_work_refs_conn(self, conn: Connection, chunk_id: str, pointers: list[WorkRef]) -> None:
        existing = {
            (p.source, p.ref)
            for p in conn.execute(
                self._store.select(s.chunk_work_refs, s.chunk_work_refs.c.source, s.chunk_work_refs.c.ref).where(
                    s.chunk_work_refs.c.chunk_id == chunk_id
                )
            ).all()
        }
        for pointer in pointers:
            if (pointer.source, pointer.ref) in existing:
                continue
            conn.execute(
                self._store.insert(s.chunk_work_refs).values(chunk_id=chunk_id, source=pointer.source, ref=pointer.ref)
            )
            existing.add((pointer.source, pointer.ref))


_work_refs_conforms: type[IWriteChunkWorkRefsRepository] = ChunkWorkRefsStore
