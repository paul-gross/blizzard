"""Chunk completion — the operator's manual closure of a chunk, reachable from any non-``done``
status including ``stopped`` (issue #294). Appends the ``chunk.completed`` fact, which
``ChunkFacts._operator_completion_outranks_stop`` lets outrank a stop at or before it
(``bzh:facts-not-status``), releasing any live route and held hub-exec slot in the same store
transaction, mirroring ``StopService``. Idempotent by no-op: an already-``done`` chunk writes
no second fact and is never refused."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work import Chunk, ChunkFacts, ChunkStatus, IWriteChunkRepository


class CompleteService:
    """Manually complete a chunk, from any non-``done`` status — ``blizzard hub chunk done``."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def complete(self, chunk: Chunk, *, by: str) -> int | None:
        """Append ``chunk.completed`` and release the chunk's live route (and any held
        hub-exec slot), atomically. A complete no-op on an already-``done`` chunk — returns
        ``None`` rather than writing a second fact. Otherwise returns the fresh
        ``chunk_completed.id`` (issue #213's activity-feed key)."""
        facts = self._chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
        if facts.status() is ChunkStatus.DONE:
            return None
        return self._chunks.record_completion(chunk.chunk_id, by=by, at=self._clock.now())
