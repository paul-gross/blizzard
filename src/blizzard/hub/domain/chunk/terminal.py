"""The terminal verdict a fenced chunk write is handed (``bzh:store-refuses-stale``).

The store refuses a chunk write only when it is stale; whether the chunk has ended is decided here, from
:class:`~blizzard.hub.domain.chunk.model.ChunkFacts` read under the chunk's lock after any earlier write in the
same unit (``bzh:write-reestablishes-read``), and handed to the write as ``terminal``."""

from __future__ import annotations

from blizzard.hub.domain.chunk.model import ChunkFacts
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.kernel.transactions import LockedTransaction


def terminal_under_lock(facts: IReadChunkFactsRepository, chunk_id: str, *, transaction: LockedTransaction) -> bool:
    """Whether ``chunk_id`` derives a terminal status, read fresh on ``transaction``, which locks it — the narrowest
    facts read that derives :meth:`ChunkFacts.status`. An unknown or ephemeral chunk is not terminal."""
    return ChunkFacts.terminal_verdict(facts.status_facts_for([chunk_id], transaction=transaction).get(chunk_id))
