"""Chunk completion — the operator's manual closure of a chunk, reachable from any non-``done``
status including ``stopped`` (issue #294). Appends the ``chunk.completed`` fact, which
``ChunkFacts._operator_completion_outranks_stop`` lets outrank a stop at or before it
(``bzh:facts-not-status``), releasing any live route and held hub-exec slot in the same store
transaction, mirroring ``StopService``. Idempotent by no-op: an already-``done`` chunk writes
no second fact and is never refused."""

from __future__ import annotations

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.work import Chunk, ChunkFacts


class CompleteService:
    """Manually complete a chunk, from any non-``done`` status — ``blizzard hub chunk done``."""

    def __init__(self, *, lifecycle: IWriteChunkLifecycleRepository, clock: IClock) -> None:
        self._lifecycle = lifecycle
        self._clock = clock

    def complete(self, chunk: Chunk, *, facts: ChunkFacts, by: str) -> int | None:
        """Append ``chunk.completed`` and release the chunk's live route (and any held
        hub-exec slot), atomically. Takes the caller's already-loaded ``facts``
        (``bzh:domain-takes-objects``). A no-op on an already-``done`` chunk — returns
        ``None``; otherwise the fresh ``chunk_completed.id``."""
        if facts.status() is ChunkStatus.DONE:
            return None
        return self._lifecycle.record_completion(chunk.chunk_id, by=by, at=self._clock.now())
