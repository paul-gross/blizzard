"""Chunk promotion — flip a not-ready chunk to ready.

Appending the ``chunk.promoted`` fact flips a chunk to ``ready``; facts append, status
derives (``bzh:facts-not-status``). Promotion also stamps a tail queue position (#137)
in the same transaction — a crash lands both facts or neither, never a stale backlog
position outranking the tail stamp on restart."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.queue import IReadChunkQueueRepository, IWriteChunkQueueRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.queue import QueueService


def tail_position(record: IReadChunkRecordRepository, queue: IReadChunkQueueRepository) -> float:
    """The position one past every currently-ready chunk's own effective position
    (issue #137) — the one rule :meth:`PromoteService.promote` and a routine run's own
    promote-on-mint (blizzard#392) both stamp a fresh tail position by, read *before*
    the write that stamps it."""
    ready = record.list_ready()
    if not ready:
        return 0.0
    positions = queue.queue_positions()
    promoted_ats = queue.promoted_ats()
    return max(QueueService._effective_position(c, positions, promoted_ats) for c in ready) + 1.0


class PromoteService:
    """Promote a not-ready chunk to ready — ``blizzard hub promote``."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        record: IReadChunkRecordRepository,
        queue: IWriteChunkQueueRepository,
        clock: IClock,
    ) -> None:
        self._facts = facts
        self._record = record
        self._queue = queue
        self._clock = clock

    def promote(self, chunk_id: str) -> int | None:
        """Append the ``chunk.promoted`` fact and stamp an explicit tail position, in one
        transaction. A complete no-op on an already-promoted chunk; otherwise stamps
        :func:`tail_position`, read *before* the write, and returns the fresh
        ``chunk_promoted.id``."""
        facts = self._facts.load_facts(chunk_id)
        if facts is not None and facts.promoted:
            return None
        tail = tail_position(self._record, self._queue)
        return self._queue.record_promote_with_tail_position(chunk_id, position=tail, at=self._clock.now())
