"""Chunk promotion — flip a not-ready chunk to ready.

Appending the ``chunk.promoted`` fact flips a chunk to ``ready``; facts append, status
derives (``bzh:facts-not-status``). Promotion also stamps a tail queue position (#137),
so it is two writes guarded on current promoted-ness; a crash between them self-heals —
tests/test_queue_service.py::test_promoted_but_unmoved_chunk_falls_back_to_promoted_at_not_minted_at"""

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
        """Append the ``chunk.promoted`` fact and stamp an explicit tail queue position.

        A complete no-op on an already-promoted chunk. Otherwise stamps
        ``max(effective positions of ready chunks) + 1.0``, read *before* either write so
        the chunk never counts itself, and returns the fresh ``chunk_promoted.id``."""
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
        at = self._clock.now()
        promoted_id = self._chunks.record_promote(chunk_id, at=at)
        self._chunks.record_queue_position(chunk_id, position=tail, at=at)
        return promoted_id
