"""Chunk promotion — flip a not-ready chunk to ready.

Appending the ``chunk.promoted`` fact flips a chunk to ``ready``; facts append, status
derives (``bzh:facts-not-status``). Promotion also stamps a tail queue position (#137)
in the same transaction — a crash lands both facts or neither, never a stale backlog
position outranking the tail stamp on restart."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.queue import IReadChunkQueueRepository, IWriteChunkQueueRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.queue import QueueService
from blizzard.hub.domain.work import Chunk, ChunkFacts


def tail_position(
    record: IReadChunkRecordRepository,
    queue: IReadChunkQueueRepository,
    facts: IReadChunkFactsRepository,
    *,
    statuses: Mapping[str, ChunkStatus],
) -> float:
    """The position one past every queue-ranked chunk's own effective position
    — the rule :meth:`PromoteService.promote` stamps a fresh tail position
    by, read *before* the write that stamps it. The candidates are the ready chunks plus
    the paused chunks only a pause withholds from ready
    (:meth:`~blizzard.hub.domain.work.ChunkFacts.is_ready_but_for_pause`), whose explicit
    position they resume at. Facts are read only for the ids ``statuses`` names ``PAUSED``.
    ``statuses`` is the caller's own already-derived fleet statuses, never re-derived here."""
    candidates = record.list_ready(statuses=statuses)
    paused_ids = [chunk_id for chunk_id, status in statuses.items() if status is ChunkStatus.PAUSED]
    if paused_ids:
        withheld = [cid for cid, f in facts.status_facts_for(paused_ids).items() if f.is_ready_but_for_pause()]
        candidates = [*candidates, *record.get_many(withheld).values()]
    if not candidates:
        return 0.0
    candidate_ids = [c.chunk_id for c in candidates]
    positions = queue.queue_positions(candidate_ids)
    promoted_ats = queue.promoted_ats(candidate_ids)
    return max(QueueService._effective_position(c, positions, promoted_ats) for c in candidates) + 1.0


class PromoteService:
    """Promote a not-ready chunk to ready — ``blizzard hub promote``."""

    def __init__(
        self,
        *,
        record: IReadChunkRecordRepository,
        queue: IWriteChunkQueueRepository,
        facts: IReadChunkFactsRepository,
        clock: IClock,
    ) -> None:
        self._record = record
        self._queue = queue
        self._facts = facts
        self._clock = clock

    def promote(self, chunk: Chunk, *, facts: ChunkFacts, statuses: Mapping[str, ChunkStatus]) -> int | None:
        """Append the ``chunk.promoted`` fact and stamp an explicit tail position, in one
        transaction. A complete no-op on an already-promoted chunk; otherwise stamps
        :func:`tail_position`, read *before* the write, and returns the fresh
        ``chunk_promoted.id``. Takes the chunk, its facts, and the caller's own
        already-derived ``statuses`` (``bzh:domain-takes-objects``)."""
        if facts.promoted:
            return None
        tail = tail_position(self._record, self._queue, self._facts, statuses=statuses)
        return self._queue.record_promote_with_tail_position(chunk.chunk_id, position=tail, at=self._clock.now())
