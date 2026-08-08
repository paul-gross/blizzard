"""Chunk ingest — wrap ``{source, ref}`` pointers into a chunk pinned to a graph, storing the pointer
and never the contents.

**Ingest mints neither model nor effort default** (issue #144): both start empty, *expressing no
preference*, so nothing here outranks a later declaration. **Batch = one chunk.** A pointer already held
by a non-terminal chunk rejects the whole ingest ``409``; re-ingest is legal once its holder is done."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import CHUNK_PREFIX, Id
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import Chunk, IWriteChunkRepository, WorkRef


class IngestConflict(Exception):
    """A submitted pointer is already held by a live chunk — the 409 carrier."""

    def __init__(self, *, existing_chunk_id: str, pointer: WorkRef) -> None:
        super().__init__(f"pointer {pointer.source}#{pointer.ref} already held by live chunk {existing_chunk_id}")
        self.existing_chunk_id = existing_chunk_id
        self.pointer = pointer


class IngestService:
    """Mint a chunk from work refs, pinned to the default graph."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def ingest(self, pointers: list[WorkRef], *, graph: Graph) -> str:
        for pointer in pointers:
            holder = self._chunks.find_live_holder(pointer)
            if holder is not None:
                raise IngestConflict(existing_chunk_id=holder, pointer=pointer)
        chunk = Chunk(
            chunk_id=Id.mint(CHUNK_PREFIX, self._clock).value,
            graph_id=graph.graph_id,
            work_refs=list(pointers),
            minted_at=self._clock.now(),
        )
        self._chunks.mint(chunk)
        return chunk.chunk_id
