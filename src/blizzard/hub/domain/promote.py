"""Chunk promotion — flip a not-ready chunk to ready.

Appending the ``chunk.promoted`` fact flips a chunk to ``ready``; facts append, status
derives (``bzh:facts-not-status``). Promotion also stamps a tail queue position (#137)
in the same transaction — a crash lands both facts or neither, never a stale backlog
position outranking the tail stamp on restart."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.queue import IReadChunkQueueRepository, IWriteChunkQueueRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.queue import QueueService
from blizzard.hub.domain.work import Chunk, ChunkFacts


def tail_position(
    record: IReadChunkRecordRepository, queue: IReadChunkQueueRepository, *, statuses: Mapping[str, ChunkStatus]
) -> float:
    """The position one past every currently-ready chunk's own effective position
    — the rule :meth:`PromoteService.promote` stamps a fresh tail position
    by, read *before* the write that stamps it. ``statuses`` is the caller's own already-derived fleet
    statuses, never re-derived here."""
    ready = record.list_ready(statuses=statuses)
    if not ready:
        return 0.0
    ready_ids = [c.chunk_id for c in ready]
    positions = queue.queue_positions(ready_ids)
    promoted_ats = queue.promoted_ats(ready_ids)
    return max(QueueService._effective_position(c, positions, promoted_ats) for c in ready) + 1.0


class PromoteService:
    """Promote a not-ready chunk to ready — ``blizzard hub promote``."""

    def __init__(
        self,
        *,
        record: IReadChunkRecordRepository,
        queue: IWriteChunkQueueRepository,
        clock: IClock,
    ) -> None:
        self._record = record
        self._queue = queue
        self._clock = clock

    def promote(self, chunk: Chunk, *, facts: ChunkFacts, statuses: Mapping[str, ChunkStatus]) -> int | None:
        """Append the ``chunk.promoted`` fact and stamp an explicit tail position, in one
        transaction. A complete no-op on an already-promoted chunk; otherwise stamps
        :func:`tail_position`, read *before* the write, and returns the fresh
        ``chunk_promoted.id``. Takes the chunk, its facts, and the caller's own
        already-derived ``statuses`` (``bzh:domain-takes-objects``)."""
        if facts.promoted:
            return None
        tail = tail_position(self._record, self._queue, statuses=statuses)
        return self._queue.record_promote_with_tail_position(chunk.chunk_id, position=tail, at=self._clock.now())
