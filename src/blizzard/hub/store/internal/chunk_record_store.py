"""SQLAlchemy adapter for the chunk record seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row or repins a column the domain
already resolved; nothing here derives status. Timestamps arrive already stamped
(``bzh:injected-clock``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, or_, select, update

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.hub.domain.chunks.record import ChunkPage, IWriteChunkRecordRepository
from blizzard.hub.domain.pagination import MalformedCursor, decode_cursor, encode_cursor
from blizzard.hub.domain.work import Chunk, IntendedMigration, WorkRef
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.batching import id_batches
from blizzard.hub.store.internal.chunk_rows import (
    DEFAULT_HARNESSES,
    DEFAULT_MODEL,
    INTENDED_MIGRATION,
    chunk_row,
    ephemeral_ids,
    ephemeral_ids_in,
    graph_id_of_batch,
    insert_chunk_rows,
    is_ephemeral_id,
)

#: `(minted_at, chunk_id)` — the tiebreak `minted_at desc` alone lacks (blizzard#526 D4).
_CURSOR_ARITY = 2


def _encode_chunk_cursor(chunk: Chunk) -> str:
    return encode_cursor(iso_utc(chunk.minted_at), chunk.chunk_id)


def _decode_chunk_cursor(cursor: str) -> tuple[datetime, str]:
    parts = decode_cursor(cursor)
    if len(parts) != _CURSOR_ARITY or not isinstance(parts[0], str) or not isinstance(parts[1], str):
        raise MalformedCursor(cursor)
    try:
        minted_at = as_utc(datetime.fromisoformat(parts[0]))
    except ValueError:
        raise MalformedCursor(cursor) from None
    return minted_at, parts[1]


def _chunk_page_stmt(after: tuple[datetime, str] | None, limit: int) -> Select[Any]:
    stmt = select(s.chunks)
    if after is not None:
        minted_at, chunk_id = after
        c = s.chunks.c
        # The portable spelling of `(minted_at, chunk_id) < (minted_at, chunk_id)` —
        # row-value comparison support varies by backend (`bzh:sql-portable`).
        stmt = stmt.where(or_(c.minted_at < minted_at, and_(c.minted_at == minted_at, c.chunk_id < chunk_id)))
    return stmt.order_by(s.chunks.c.minted_at.desc(), s.chunks.c.chunk_id.desc()).limit(limit)


class ChunkRecordStore:
    """The chunk row itself — mint, listing, and the repin columns."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def get(self, chunk_id: str) -> Chunk | None:
        with self._store.read("get") as conn:
            row = conn.execute(select(s.chunks).where(s.chunks.c.chunk_id == chunk_id)).one_or_none()
            if row is None or is_ephemeral_id(conn, chunk_id):
                return None  # a grouped-away or deleted chunk is ephemeral — gone from every read
            return chunk_row(conn, row)

    def get_many(self, chunk_ids: Sequence[str]) -> dict[str, Chunk]:
        """`get`'s batched sibling — every requested id's row plus its work refs
        (`bzh:bulk-reconstitution`). An id that doesn't exist or is ephemeral
        is silently dropped, the same as `get` returning None for it. Reads each batch's
        full ``chunks`` rows once, via :func:`ephemeral_ids_in`."""
        if not chunk_ids:
            return {}
        result: dict[str, Chunk] = {}
        with self._store.read("get_many") as conn:
            for batch in id_batches(chunk_ids):
                rows = {
                    r.chunk_id: r for r in conn.execute(select(s.chunks).where(s.chunks.c.chunk_id.in_(batch))).all()
                }
                if not rows:
                    continue
                ephemeral = ephemeral_ids_in(conn, batch)
                surviving = [chunk_id for chunk_id in rows if chunk_id not in ephemeral]
                pointers: dict[str, list[WorkRef]] = defaultdict(list)
                for p in conn.execute(
                    select(s.chunk_work_refs).where(s.chunk_work_refs.c.chunk_id.in_(surviving))
                ).all():
                    pointers[p.chunk_id].append(WorkRef(source=p.source, ref=p.ref))
                for chunk_id in surviving:
                    r = rows[chunk_id]
                    result[chunk_id] = Chunk(
                        chunk_id=r.chunk_id,
                        graph_id=r.graph_id,
                        work_refs=pointers[r.chunk_id],
                        minted_at=r.minted_at,
                        default_model=DEFAULT_MODEL.decode(r.default_model),
                        default_effort=r.default_effort,
                        default_harnesses=DEFAULT_HARNESSES.decode(r.default_harnesses),
                        intended_migration=INTENDED_MIGRATION.decode(r.intended_migration),
                    )
        return result

    def graph_id_of_many(self, chunk_ids: Sequence[str]) -> dict[str, str]:
        """`get_many`'s narrow sibling — just the chunk_id -> graph_id pins, ephemeral
        ids excluded, without either table's row read `get_many` also pays for."""
        if not chunk_ids:
            return {}
        result: dict[str, str] = {}
        with self._store.read("graph_id_of_many") as conn:
            for batch in id_batches(chunk_ids):
                result.update(graph_id_of_batch(conn, batch))
        return result

    def list_all(self) -> list[Chunk]:
        """Every non-ephemeral chunk, newest-minted first. Reads ``chunk_work_refs`` with
        one bulk query grouped by chunk id in Python (issue #421) rather than a per-chunk
        query, the same shape the facts seam's ``load_all_facts`` reads its own tables in —
        so the list route's own chunk read is bounded regardless of fleet size too."""
        with self._store.read("list_all") as conn:
            ephemeral = ephemeral_ids(conn)
            rows = [
                r
                for r in conn.execute(select(s.chunks).order_by(s.chunks.c.minted_at.desc())).all()
                if r.chunk_id not in ephemeral  # a grouped-away or deleted chunk is removed from every listing
            ]
            pointers: dict[str, list[WorkRef]] = defaultdict(list)
            for p in conn.execute(select(s.chunk_work_refs)).all():
                pointers[p.chunk_id].append(WorkRef(source=p.source, ref=p.ref))
            return [
                Chunk(
                    chunk_id=r.chunk_id,
                    graph_id=r.graph_id,
                    work_refs=pointers[r.chunk_id],
                    minted_at=r.minted_at,
                    default_model=DEFAULT_MODEL.decode(r.default_model),
                    default_effort=r.default_effort,
                    default_harnesses=DEFAULT_HARNESSES.decode(r.default_harnesses),
                    intended_migration=INTENDED_MIGRATION.decode(r.intended_migration),
                )
                for r in rows
            ]

    def list_page(self, *, cursor: str | None = None, limit: int) -> ChunkPage:
        """`list_all`'s bounded sibling (blizzard#526 D4): a SQL keyset window with
        ephemeral chunks excluded in Python after the read, so a window landing wholly
        on ephemeral rows can come back short of `limit` live chunks. Each retry doubles
        the window rather than stopping there, so a `next_cursor` walk still sees every
        visible chunk exactly once."""
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")
        after = _decode_chunk_cursor(cursor) if cursor is not None else None
        fetch = limit + 1
        with self._store.read("list_page") as conn:
            while True:
                rows = conn.execute(_chunk_page_stmt(after, fetch)).all()
                ephemeral: set[str] = set()
                for batch in id_batches([r.chunk_id for r in rows]):
                    ephemeral |= ephemeral_ids_in(conn, batch)
                surviving = [r for r in rows if r.chunk_id not in ephemeral]
                if len(surviving) > limit or len(rows) < fetch:
                    break
                fetch *= 2
            page_rows = surviving[:limit]
            pointers: dict[str, list[WorkRef]] = defaultdict(list)
            for batch in id_batches([r.chunk_id for r in page_rows]):
                for p in conn.execute(select(s.chunk_work_refs).where(s.chunk_work_refs.c.chunk_id.in_(batch))).all():
                    pointers[p.chunk_id].append(WorkRef(source=p.source, ref=p.ref))
        chunks = [
            Chunk(
                chunk_id=r.chunk_id,
                graph_id=r.graph_id,
                work_refs=pointers[r.chunk_id],
                minted_at=r.minted_at,
                default_model=DEFAULT_MODEL.decode(r.default_model),
                default_effort=r.default_effort,
                default_harnesses=DEFAULT_HARNESSES.decode(r.default_harnesses),
                intended_migration=INTENDED_MIGRATION.decode(r.intended_migration),
            )
            for r in page_rows
        ]
        next_cursor = _encode_chunk_cursor(chunks[-1]) if len(surviving) > limit and chunks else None
        return ChunkPage(chunks=chunks, next_cursor=next_cursor)

    def list_ready(self, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        return self._listed_with_status(ChunkStatus.READY, statuses=statuses)

    def list_not_ready(self, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        return self._listed_with_status(ChunkStatus.NOT_READY, statuses=statuses)

    def _listed_with_status(self, status: ChunkStatus, *, statuses: Mapping[str, ChunkStatus]) -> list[Chunk]:
        """:meth:`list_all` narrowed by ``statuses`` — the caller's own already-derived
        fleet statuses, never this seam's own facts read. Reading the listing first
        excludes a chunk deleted between the two reads, never mistaking it for
        unwritten."""
        chunks = self.list_all()
        return [c for c in chunks if statuses.get(c.chunk_id) is status]

    def mint(self, chunk: Chunk) -> None:
        with self._store.write("mint") as conn:
            insert_chunk_rows(conn, chunk)

    def set_graph(self, chunk_id: str, *, graph_id: str) -> None:
        """Repin a not-ready or ready-unclaimed chunk to a different workflow graph (issue #27, #120)."""
        with self._store.write("set_graph") as conn:
            conn.execute(update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(graph_id=graph_id))

    def set_defaults(
        self, chunk_id: str, *, default_model: list[str], default_effort: str | None, default_harnesses: list[str]
    ) -> None:
        """Repin a not-ready or ready-unclaimed chunk's default model/effort/harnesses
        (issues #27, #120, #144) — all three in one write; see
        :meth:`~blizzard.hub.domain.chunks.record.IWriteChunkRecordRepository.set_defaults`."""
        with self._store.write("set_defaults") as conn:
            conn.execute(
                update(s.chunks)
                .where(s.chunks.c.chunk_id == chunk_id)
                .values(
                    default_model=DEFAULT_MODEL.encode(default_model),
                    default_effort=default_effort,
                    default_harnesses=DEFAULT_HARNESSES.encode(default_harnesses),
                )
            )

    def set_intended_migration(self, chunk_id: str, *, intended: IntendedMigration | None) -> None:
        """Set, overwrite, or clear a chunk's standing migration intent (issue #124).

        A plain column overwrite, editable at any non-terminal status. The column
        carries no timestamp, so this write takes no ``at``."""
        with self._store.write("set_intended_migration") as conn:
            conn.execute(
                update(s.chunks)
                .where(s.chunks.c.chunk_id == chunk_id)
                .values(intended_migration=INTENDED_MIGRATION.encode(intended))
            )


def _conforms_record(x: ChunkRecordStore) -> IWriteChunkRecordRepository:
    return x
