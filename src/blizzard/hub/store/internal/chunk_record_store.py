"""SQLAlchemy adapter for the chunk record seam (package-private, blizzard#411 Phase 3).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row or repins a column the domain
already resolved; nothing here derives status. Timestamps arrive already stamped
(``bzh:injected-clock``)."""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select, update

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.record import IWriteChunkRecordRepository
from blizzard.hub.domain.work import Chunk, IntendedMigration, WorkRef
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import (
    DEFAULT_MODEL,
    INTENDED_MIGRATION,
    chunk_row,
    ephemeral_ids,
    insert_chunk_rows,
)


class ChunkRecordStore:
    """The chunk row itself — mint, listing, and the repin columns."""

    def __init__(self, store: HubStoreConnections, clock: IClock, *, facts: IReadChunkFactsRepository) -> None:
        self._store = store
        self._clock = clock
        self._facts = facts

    def get(self, chunk_id: str) -> Chunk | None:
        with self._store.read("get") as conn:
            row = conn.execute(select(s.chunks).where(s.chunks.c.chunk_id == chunk_id)).one_or_none()
            if row is None or chunk_id in ephemeral_ids(conn):
                return None  # a grouped-away or deleted chunk is ephemeral — gone from every read
            return chunk_row(conn, row)

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
                    intended_migration=INTENDED_MIGRATION.decode(r.intended_migration),
                )
                for r in rows
            ]

    def list_ready(self) -> list[Chunk]:
        return self._listed_with_status(ChunkStatus.READY)

    def list_not_ready(self) -> list[Chunk]:
        return self._listed_with_status(ChunkStatus.NOT_READY)

    def _listed_with_status(self, status: ChunkStatus) -> list[Chunk]:
        """:meth:`list_all` narrowed by derived status, over the facts seam's
        ``load_all_facts`` bulk read rather than a per-chunk fan-out — the queue and
        backlog peeks read the whole fleet, so their cost must not scale with it (issue
        #421's shape). Reading the listing first means a chunk deleted between the two
        reads is excluded, never mistaken for one whose facts are simply unwritten."""
        chunks = self.list_all()
        statuses = {chunk_id: facts.status() for chunk_id, facts in self._facts.load_all_facts().items()}
        return [c for c in chunks if statuses.get(c.chunk_id) is status]

    def mint(self, chunk: Chunk) -> None:
        with self._store.write("mint") as conn:
            insert_chunk_rows(conn, chunk)

    def set_graph(self, chunk_id: str, *, graph_id: str) -> None:
        """Repin a not-ready or ready-unclaimed chunk to a different workflow graph (issue #27, #120)."""
        with self._store.write("set_graph") as conn:
            conn.execute(update(s.chunks).where(s.chunks.c.chunk_id == chunk_id).values(graph_id=graph_id))

    def set_defaults(self, chunk_id: str, *, default_model: list[str], default_effort: str | None) -> None:
        """Repin a not-ready or ready-unclaimed chunk's default model/effort (issues #27,
        #120, #144) — both in one write; see
        :meth:`~blizzard.hub.domain.chunks.record.IWriteChunkRecordRepository.set_defaults`."""
        with self._store.write("set_defaults") as conn:
            conn.execute(
                update(s.chunks)
                .where(s.chunks.c.chunk_id == chunk_id)
                .values(
                    default_model=DEFAULT_MODEL.encode(default_model),
                    default_effort=default_effort,
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
