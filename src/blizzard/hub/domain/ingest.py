"""Chunk ingest — wrap work refs into a chunk.

The ``POST /chunks`` domain rule: a caller submits one or more ``{source, ref}``
pointers and the hub mints a chunk pinned to the configured default graph. Contents are
never stored — only the pointer.

**Ingest mints neither model nor effort default** (issue #144): a concrete hub-side
model would outrank every ``sessions:`` declaration omitting ``model:`` and make a
runner's own ``[models.aliases]`` default unreachable, so both start empty — *express no
preference* (pinned by
tests/test_chunk_edit_api.py::test_a_freshly_ingested_chunk_carries_the_default_graph_and_no_model_preference).
Both are editable later while the chunk rests pre-claim (issue #27, ``domain/edit.py``),
which is where a deliberate preference belongs.

**Batch = one chunk.** The wire response carries a single ``chunk_id``, so a
multi-pointer request mints one chunk holding all its pointers (pinned by
tests/test_ingest_and_queue.py::test_ingest_batches_multiple_pointers_into_one_chunk);
per-pointer fan-out is a P7 wire change. Before minting, every pointer is checked for a
live holder — a pointer already held by a non-terminal chunk rejects the whole ingest
``409``; re-ingest is legal once every prior holder is terminal.

Holds the *write* chunk repository (``bzh:controller-read-only``); the route
resolves the default graph and delegates here.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import CHUNK_PREFIX, mint
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
            chunk_id=mint(CHUNK_PREFIX, self._clock),
            graph_id=graph.graph_id,
            work_refs=list(pointers),
            minted_at=self._clock.now(),
        )
        self._chunks.mint(chunk)
        return chunk.chunk_id
