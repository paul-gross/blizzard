"""Chunk stop — the operator's terminal abandonment of a chunk.

Appends the ``chunk_stopped`` fact (its precedence is ``ChunkFacts.status``'s,
``bzh:facts-not-status``) and conditionally releases a live route — both in one
store transaction, so a ``kill -9`` cannot leave a chunk stopped with its route still
live. Terminal and one-way: an already done or stopped chunk is refused."""

from __future__ import annotations

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.flow_writes import LeverWrite
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.lifecycle import IWriteChunkLifecycleRepository


class ChunkNotStoppable(Exception):
    """A stop targeted a chunk already terminal ({done, stopped}) — not retroactive."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not stoppable")
        self.chunk_id = chunk_id
        self.status = status


def require_stoppable(chunk_id: str, facts: ChunkFacts) -> None:
    """Refuse a stop outside :attr:`ChunkVerb.STOP`'s window with :class:`ChunkNotStoppable`."""
    if not facts.admits(ChunkVerb.STOP):
        raise ChunkNotStoppable(chunk_id, facts.status())


class StopService:
    """Terminally abandon a chunk and release any route it holds — ``blizzard hub stop``."""

    def __init__(
        self, *, lifecycle: IWriteChunkLifecycleRepository, exclusive: IChunkExclusiveWrites, clock: IClock
    ) -> None:
        self._lifecycle = lifecycle
        self._clock = clock
        # ``bzh:store-exclusive-write``.
        self._exclusive = exclusive

    def stop(self, chunk: Chunk, *, by: str) -> LeverWrite:
        """Append ``chunk.stopped``, release the chunk's live route, and close ``stopped`` each of its flow
        submissions waiting its turn, atomically; a submission mid-turn closes at its turn's end. Takes the loaded
        chunk (``bzh:domain-takes-objects``); the terminal-status guard is re-derived fresh under the row lock
        (``bzh:store-exclusive-write``), so a concurrent stop or completion cannot race this one past a stale
        refusal. Raises :class:`ChunkNotStoppable` for a chunk already done/stopped, :class:`ChunkNotFound` for one
        gone under the lock. Returns the ``chunk_stopped`` id and the ``withdrawn`` flow facts, for the caller to
        announce once committed. ``at`` is stamped from the injected clock after the row lock is taken, never before
        it."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            require_stoppable(chunk.chunk_id, facts)
            return self._lifecycle.record_stop_locked(handle, chunk.chunk_id, by=by, at=self._clock.now())
