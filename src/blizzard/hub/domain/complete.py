"""Chunk completion — the operator's manual closure of a chunk, reachable from any non-``done``
status including ``stopped``. Appends the ``chunk.completed`` fact, which
``ChunkFacts._operator_completion_outranks_stop`` lets outrank a stop at or before it
(``bzh:facts-not-status``), releasing any live route and held hub-exec slot in the same store
transaction, mirroring ``StopService``. Idempotent by no-op: an already-``done`` chunk writes
no second fact and is never refused."""

from __future__ import annotations

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.errors import ChunkNotFound
from blizzard.hub.domain.work import Chunk


class CompleteService:
    """Manually complete a chunk, from any non-``done`` status — ``blizzard hub chunk done``."""

    def __init__(
        self, *, lifecycle: IWriteChunkLifecycleRepository, exclusive: IChunkExclusiveWrites, clock: IClock
    ) -> None:
        self._lifecycle = lifecycle
        self._clock = clock
        # The locked-transaction seam (``bzh:store-exclusive-write``): the already-``done``
        # guard this completion no-ops on is re-derived here, under the same row lock the
        # write lands under, never trusted from a caller's pre-lock snapshot.
        self._exclusive = exclusive

    def complete(self, chunk: Chunk, *, by: str) -> int | None:
        """Append ``chunk.completed`` and release the chunk's live route (and any held
        hub-exec slot), atomically. Takes the loaded chunk (``bzh:domain-takes-objects``);
        the already-``done`` guard is re-derived fresh under the row lock
        (``bzh:store-exclusive-write``), so a concurrent completion cannot land a second
        fact past a stale read. A no-op on an already-``done`` chunk — returns ``None``;
        otherwise the fresh ``chunk_completed.id``. Raises :class:`ChunkNotFound` for a
        chunk gone under the lock. ``at`` is stamped from the injected clock after the row
        lock is taken, never before it."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            if facts.status() is ChunkStatus.DONE:
                return None
            return self._lifecycle.record_completion_locked(handle, chunk.chunk_id, by=by, at=self._clock.now())
