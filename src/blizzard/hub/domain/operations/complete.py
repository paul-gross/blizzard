"""Chunk completion — the operator's manual closure of a chunk, reachable from any non-``done``
status including ``stopped``. Appends the ``chunk.completed`` fact (its precedence over a stop is
``ChunkFacts.status``'s, ``bzh:facts-not-status``) and releases any live route and closes ``completed``
each flow submission waiting its turn in the same store transaction. Idempotent by no-op: an already-``done``
chunk writes no second fact and is never refused."""

from __future__ import annotations

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.lifecycle import IWriteChunkLifecycleRepository


def is_completion_replay(facts: ChunkFacts) -> bool:
    """Whether a completion would replay one already standing: at ``done`` it writes nothing."""
    return facts.status() is ChunkStatus.DONE


class CompleteService:
    """Manually complete a chunk, from any non-``done`` status — ``blizzard hub chunk done``."""

    def __init__(
        self, *, lifecycle: IWriteChunkLifecycleRepository, exclusive: IChunkExclusiveWrites, clock: IClock
    ) -> None:
        self._lifecycle = lifecycle
        self._clock = clock
        # ``bzh:store-exclusive-write``.
        self._exclusive = exclusive

    def complete(self, chunk: Chunk, *, by: str) -> int | None:
        """Append ``chunk.completed``, release the chunk's live route, and close ``completed`` each of its
        flow submissions waiting its turn, atomically; a submission mid-turn closes at its turn's end. Takes the
        loaded chunk (``bzh:domain-takes-objects``); the already-``done`` guard is re-derived fresh under the row
        lock (``bzh:store-exclusive-write``), so a concurrent completion cannot land a second fact past a stale read.
        A no-op on an already-``done`` chunk — returns ``None``; otherwise the fresh ``chunk_completed.id``. Raises
        :class:`ChunkNotFound` for a chunk gone under the lock. ``at`` is stamped from the injected clock after the
        row lock is taken, never before it."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            if is_completion_replay(facts):
                return None
            return self._lifecycle.record_completion_locked(handle, chunk.chunk_id, by=by, at=self._clock.now())
