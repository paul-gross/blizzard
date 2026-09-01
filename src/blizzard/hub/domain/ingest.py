"""Chunk ingest — wrap ``{source, ref}`` pointers into a chunk pinned to a graph, storing the pointer
and never the contents.

The empty-preference default policy is :func:`~blizzard.hub.domain.work.mint_chunk`'s own (issue #144).
**Batch = one chunk.** A pointer already held by a non-terminal chunk rejects the whole ingest ``409``;
re-ingest is legal once its holder is done."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.record import IWriteChunkRecordRepository
from blizzard.hub.domain.chunks.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import WorkRef, mint_chunk


class IngestConflict(Exception):
    """A submitted pointer is already held by a live chunk — the 409 carrier."""

    def __init__(self, *, existing_chunk_id: str, pointer: WorkRef) -> None:
        super().__init__(f"pointer {pointer.source}#{pointer.ref} already held by live chunk {existing_chunk_id}")
        self.existing_chunk_id = existing_chunk_id
        self.pointer = pointer


def require_no_live_holder(work_refs: IReadChunkWorkRefsRepository, pointer: WorkRef) -> None:
    """Raise :class:`IngestConflict` when ``pointer`` is already held by a live chunk —
    the at-most-one-live-holder guard every pointer-minting call site shares."""
    holder = work_refs.find_live_holder(pointer)
    if holder is not None:
        raise IngestConflict(existing_chunk_id=holder, pointer=pointer)


class IngestService:
    """Mint a chunk from work refs, pinned to the default graph."""

    def __init__(
        self, *, record: IWriteChunkRecordRepository, work_refs: IReadChunkWorkRefsRepository, clock: IClock
    ) -> None:
        self._record = record
        self._work_refs = work_refs
        self._clock = clock

    def ingest(self, pointers: list[WorkRef], *, graph: Graph) -> str:
        for pointer in pointers:
            require_no_live_holder(self._work_refs, pointer)
        chunk = mint_chunk(pointers, graph_id=graph.graph_id, at=self._clock.now())
        self._record.mint(chunk)
        return chunk.chunk_id
