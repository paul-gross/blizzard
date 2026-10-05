"""Chunk promotion — flip a not-ready chunk to ready.

Appending the ``chunk.promoted`` fact flips a chunk to ``ready``; facts append, status
derives (``bzh:facts-not-status``). Promotion also stamps a tail queue position
in the same transaction — a crash lands both facts or neither, never a stale backlog
position outranking the tail stamp on restart."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.queue import IReadChunkQueueRepository, IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.operations.queue import QueueRanking


class ChunkNotPromotable(Exception):
    """A promote targeted a never-promoted chunk that is already terminal ({done, stopped}) —
    there is no queue left for it to join."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not promotable")
        self.chunk_id = chunk_id
        self.status = status


def promotion_writes(chunk_id: str, facts: ChunkFacts) -> bool:
    """Whether a promote writes: ``False`` replays one already standing — an already-promoted
    chunk writes nothing, at any status — and a never-promoted chunk outside
    :attr:`ChunkVerb.PROMOTE`'s window is refused with :class:`ChunkNotPromotable`."""
    if facts.promoted:
        return False
    if not facts.admits(ChunkVerb.PROMOTE):
        raise ChunkNotPromotable(chunk_id, facts.status())
    return True


def withheld_by_pause(facts_by_id: Mapping[str, ChunkFacts]) -> list[str]:
    """The paused chunks only a pause withholds from ``ready``
    (:meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.is_ready_but_for_pause`) — they rank
    with the ready queue, at the explicit position they resume at."""
    return [chunk_id for chunk_id, facts in facts_by_id.items() if facts.is_ready_but_for_pause()]


def tail_position(
    record: IReadChunkRecordRepository,
    queue: IReadChunkQueueRepository,
    facts: IReadChunkFactsRepository,
    *,
    statuses: Mapping[str, ChunkStatus],
) -> float:
    """The position one past every queue-ranked chunk's own effective position
    (:meth:`QueueRanking.tail`) — the rule :meth:`PromoteService.promote` stamps a fresh tail
    position by, read *before* the write that stamps it. The candidates are the ready chunks
    plus :func:`withheld_by_pause`'s; ``statuses`` is the caller's already-derived fleet statuses."""
    candidates = record.list_ready(statuses=statuses)
    paused_ids = [chunk_id for chunk_id, status in statuses.items() if status is ChunkStatus.PAUSED]
    if paused_ids:
        withheld = withheld_by_pause(facts.status_facts_for(paused_ids))
        candidates = [*candidates, *record.get_many(withheld).values()]
    candidate_ids = [c.chunk_id for c in candidates]
    ranking = QueueRanking(
        positions=queue.queue_positions(candidate_ids), promoted_ats=queue.promoted_ats(candidate_ids)
    )
    return ranking.tail(candidates)


class PromoteService:
    """Promote a not-ready chunk to ready — ``blizzard hub promote``."""

    def __init__(
        self,
        *,
        record: IReadChunkRecordRepository,
        queue: IWriteChunkQueueRepository,
        facts: IReadChunkFactsRepository,
        exclusive: IChunkExclusiveWrites,
        clock: IClock,
    ) -> None:
        self._record = record
        self._queue = queue
        self._facts = facts
        # The locked-transaction seam (``bzh:store-exclusive-write``): promotability is judged
        # from facts read under the chunk's row lock, never a caller's pre-lock snapshot.
        self._exclusive = exclusive
        self._clock = clock

    def promote(self, chunk: Chunk, *, statuses: Mapping[str, ChunkStatus]) -> int | None:
        """Append the ``chunk.promoted`` fact and stamp an explicit tail position, in one
        transaction, as :func:`promotion_writes` decides over facts read under the row lock:
        ``None`` for a replay on an already-promoted chunk, :class:`ChunkNotPromotable` for a
        terminal one, :class:`ChunkNotFound` for a chunk gone under the lock; otherwise stamps
        :func:`tail_position` and returns the fresh ``chunk_promoted.id``.

        The tail ranks other chunks — a different aggregate — so it is computed before the
        lock from ``statuses``; the chunk's own promotability is not."""
        pre_lock = self._facts.load_facts(chunk.chunk_id)
        if pre_lock is not None and pre_lock.promoted:
            return None  # promotion is permanent: a replay needs no lock
        tail = tail_position(self._record, self._queue, self._facts, statuses=statuses)
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            if not promotion_writes(chunk.chunk_id, facts):
                return None
            return self._queue.record_promote_with_tail_position_locked(
                handle, chunk.chunk_id, position=tail, at=self._clock.now()
            )
