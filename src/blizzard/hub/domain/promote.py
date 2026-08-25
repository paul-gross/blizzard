"""Chunk promotion — flip a not-ready chunk to ready.

Appending the ``chunk.promoted`` fact flips a chunk to ``ready``; facts append, status
derives (``bzh:facts-not-status``). Promotion also stamps a tail queue position (#137)
in the same transaction — a crash lands both facts or neither, never a stale backlog
position outranking the tail stamp on restart."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.queue import QueueService
from blizzard.hub.domain.work import IWriteChunkRepository


class PromoteService:
    """Promote a not-ready chunk to ready — ``blizzard hub promote``."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def promote(self, chunk_id: str) -> int | None:
        """Append the ``chunk.promoted`` fact and stamp an explicit tail position, in one
        transaction. A complete no-op on an already-promoted chunk; otherwise stamps
        ``max(effective positions of ready chunks) + 1.0``, read *before* the write, and
        returns the fresh ``chunk_promoted.id``."""
        facts = self._chunks.load_facts(chunk_id)
        if facts is not None and facts.promoted:
            return None
        ready = self._chunks.list_ready()
        if ready:
            positions = self._chunks.queue_positions()
            promoted_ats = self._chunks.promoted_ats()
            tail = max(QueueService._effective_position(c, positions, promoted_ats) for c in ready) + 1.0
        else:
            tail = 0.0
        return self._chunks.record_promote_with_tail_position(chunk_id, position=tail, at=self._clock.now())
