"""Chunk demotion — move an unacquired, promoted chunk back to not-ready.

Appending the ``chunk.demoted`` fact returns a chunk to ``not_ready``: a chunk is currently promoted
only while its newest promotion is later than its newest demotion. Facts append, status derives
(``bzh:facts-not-status``); the chunk keeps its id, history, edges, pin, and defaults."""

from __future__ import annotations

from collections.abc import Mapping

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.queue import IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.operations.queue import (
    TAIL_PLACEMENT,
    Placement,
    QueueList,
    read_ranking,
    resolve_landing,
    write_positions_fn,
)


class ChunkNotDemotable(Exception):
    """A demote targeted a currently promoted chunk that is not resting in the ready lane — claimed,
    delivering, gated on a human, paused on a live route, or ended."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not demotable")
        self.chunk_id = chunk_id
        self.status = status


def demotion_writes(chunk_id: str, facts: ChunkFacts) -> bool:
    """Whether a demote writes: ``False`` replays one already standing — a chunk not currently
    promoted writes nothing, at any status. A promoted chunk writes inside :attr:`ChunkVerb.DEMOTE`'s
    window, or when only a pause withholds it from ``ready``
    (:meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.is_ready_but_for_pause`) — pause and
    promotion are independent. Any other is refused with :class:`ChunkNotDemotable`."""
    if not facts.promoted:
        return False
    if not facts.admits(ChunkVerb.DEMOTE) and not facts.is_ready_but_for_pause():
        raise ChunkNotDemotable(chunk_id, facts.status())
    return True


class DemoteService:
    """Demote a ready chunk back to not-ready — ``blizzard hub chunk demote``."""

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
        # The locked-transaction seam (``bzh:store-exclusive-write``): demotability is judged from facts
        # read under the chunk's row lock, so a claim racing a demote resolves one way, never both.
        self._exclusive = exclusive
        self._clock = clock

    def demote(
        self,
        chunk: Chunk,
        *,
        statuses: Mapping[str, ChunkStatus],
        placement: Placement = TAIL_PLACEMENT,
    ) -> int | None:
        """Append the ``chunk.demoted`` fact and stamp an explicit backlog position, in one
        transaction, as :func:`demotion_writes` decides over facts read under the row lock:
        ``None`` for a replay on a chunk not currently promoted, :class:`ChunkNotDemotable` for one
        outside the window, :class:`ChunkNotFound` for a chunk gone under the lock; otherwise stamps the
        landing :func:`~blizzard.hub.domain.operations.queue.resolve_landing` resolves — the tail, the
        top, or right after ``placement``'s anchor — and returns the fresh ``chunk_demoted.id``.

        The landing ranks other chunks — a different aggregate — so it is resolved before the lock
        from ``statuses``; the chunk's own demotability is not."""
        pre_lock = self._facts.load_facts(chunk.chunk_id)
        if pre_lock is not None and not pre_lock.promoted:
            return None  # a replay needs no lock
        now = self._clock.now()
        candidates = self._record.list_not_ready(statuses=statuses)
        landing = resolve_landing(
            read_ranking(self._queue, candidates),
            QueueList.NOT_READY,
            candidates,
            chunk_id=chunk.chunk_id,
            placement=placement,
            statuses=statuses,
        )
        if landing.renormalized:
            write_positions_fn(self._queue, QueueList.NOT_READY)(list(landing.renormalized), at=now)
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            if not demotion_writes(chunk.chunk_id, facts):
                return None
            return self._queue.record_demote_with_position_locked(
                handle, chunk.chunk_id, position=landing.position, at=now
            )
