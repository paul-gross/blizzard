"""Chunk demotion — move an unacquired, promoted chunk back to not-ready.

Appending the ``chunk.demoted`` fact returns a chunk to ``not_ready``: a chunk is currently promoted
only while its newest promotion is later than its newest demotion. Facts append, status derives
(``bzh:facts-not-status``); the chunk keeps its id, history, edges, pin, and defaults."""

from __future__ import annotations

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import ChunkFacts, ChunkVerb


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
