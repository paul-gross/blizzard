"""Chunk demotion — move an unacquired, promoted chunk back to not-ready.

Appending the ``chunk.demoted`` fact returns a chunk to ``not_ready``: a chunk is currently promoted
only while its newest promotion is later than its newest demotion. Facts append, status derives
(``bzh:facts-not-status``); the chunk keeps its id, history, edges, pin, and defaults."""

from __future__ import annotations

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.queue import IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.operations.queue import (
    BOTH_LISTS,
    TAIL_PLACEMENT,
    Placement,
    QueueList,
    ranking_lock_set,
    read_ranking,
    resolve_landing,
    write_positions,
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
        transactions: IHubTransactions,
        clock: IClock,
    ) -> None:
        self._record = record
        self._queue = queue
        self._facts = facts
        self._transactions = transactions
        self._clock = clock

    def demote(self, chunk: Chunk, *, placement: Placement = TAIL_PLACEMENT) -> int | None:
        """Append the ``chunk.demoted`` fact and stamp an explicit backlog position, in one unit, as
        :func:`demotion_writes` decides over the chunk's facts read in it: ``None`` for a replay on a
        chunk not currently promoted, :class:`ChunkNotDemotable` for one outside the window,
        :class:`ChunkNotFound` for a chunk gone; otherwise stamps the landing
        :func:`~blizzard.hub.domain.operations.queue.resolve_landing` resolves — the tail, the top, or
        right after ``placement``'s anchor — and returns the fresh ``chunk_demoted.id``.

        The unit locks the chunk, the anchor, and both lists' rankings — the chunk leaves one and joins
        the other — before any read, so the landing ranks the backlog as it stands when the demote
        commits (``bzh:write-reestablishes-read``); a refusal commits nothing."""
        with self._transactions.begin() as transaction:
            locked = ranking_lock_set(transaction, BOTH_LISTS, [chunk.chunk_id, placement.after_chunk_id])
            facts = self._facts.load_facts(chunk.chunk_id, transaction=locked)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            if not demotion_writes(chunk.chunk_id, facts):
                return None
            statuses = self._facts.load_live_statuses(transaction=locked)
            candidates = self._record.list_not_ready(statuses=statuses, transaction=locked)
            landing = resolve_landing(
                read_ranking(self._queue, candidates, transaction=locked),
                QueueList.NOT_READY,
                candidates,
                chunk_id=chunk.chunk_id,
                placement=placement,
                statuses=statuses,
            )
            now = self._clock.now()
            if landing.renormalized:
                write_positions(self._queue, QueueList.NOT_READY, landing.renormalized, at=now, transaction=locked)
            return self._queue.record_demote_with_position(
                chunk.chunk_id, position=landing.position, at=now, transaction=locked
            )
