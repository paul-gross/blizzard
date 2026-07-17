"""Chunk ingest — wrap PM pointers into a chunk.

The ``POST /chunks`` domain rule: a caller submits one or more ``{source, ref}``
 pointers and the hub mints a chunk pinned to the configured default graph and the
default model (``DEFAULT_MODEL``); both are editable later while the chunk rests
``not_ready`` (issue #27, ``domain/edit.py``). Contents are never stored — only the
pointer.

**Batch = one chunk.** The wire response carries a single ``chunk_id``, so a
multi-pointer request mints one chunk holding all its pointers; per-pointer fan-out
(a response of many ids) is a P7 wire change, not a walking-skeleton shape. Before
minting, every pointer is checked for a live holder — a pointer already held by a
non-terminal chunk rejects the whole ingest ``409``; re-ingest is legal once
every prior holder is terminal.

Holds the *write* chunk repository (``bzh:controller-read-only``); the route
resolves the default graph and delegates here.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import CHUNK_PREFIX, mint
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import DEFAULT_MODEL, Chunk, IWriteChunkRepository, PmPointer


class IngestConflict(Exception):
    """A submitted pointer is already held by a live chunk — the 409 carrier."""

    def __init__(self, *, existing_chunk_id: str, pointer: PmPointer) -> None:
        super().__init__(f"pointer {pointer.source}#{pointer.ref} already held by live chunk {existing_chunk_id}")
        self.existing_chunk_id = existing_chunk_id
        self.pointer = pointer


class IngestService:
    """Mint a chunk from PM pointers, pinned to the default graph."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def ingest(self, pointers: list[PmPointer], *, graph: Graph) -> str:
        for pointer in pointers:
            holder = self._chunks.find_live_holder(pointer)
            if holder is not None:
                raise IngestConflict(existing_chunk_id=holder, pointer=pointer)
        chunk = Chunk(
            chunk_id=mint(CHUNK_PREFIX, self._clock),
            graph_id=graph.graph_id,
            pm_pointers=list(pointers),
            minted_at=self._clock.now(),
            model=DEFAULT_MODEL,
        )
        self._chunks.mint(chunk)
        return chunk.chunk_id
