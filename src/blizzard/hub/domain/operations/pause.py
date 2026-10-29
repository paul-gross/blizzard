"""Chunk pause — the operator's per-chunk brake, orthogonal to detach.

Pause stamps a ``chunk.paused`` fact and resume a ``chunk.resumed``; newest-fact-wins, so a re-pause
after a resume derives ``paused`` again. Pause **keeps the claim** — no route released, no epoch bumped.
Which statuses admit each is :data:`~blizzard.hub.domain.chunk.model.CHUNK_VERB_LEGALITY`'s
``PAUSE``/``RESUME`` rows."""

from __future__ import annotations

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.lifecycle import IWriteChunkLifecycleRepository


class ChunkNotPausable(Exception):
    """A pause targeted a chunk in a status pause can't touch ({done, stopped, delivering})."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not pausable")
        self.chunk_id = chunk_id
        self.status = status


def require_pausable(chunk_id: str, facts: ChunkFacts) -> None:
    """Refuse a pause outside :attr:`ChunkVerb.PAUSE`'s window with :class:`ChunkNotPausable`.

    Pinned by ``tests/test_operator_verb_rules.py``'s
    ``test_pause_is_a_brake_not_a_fence_an_open_decision_does_not_refuse_it``."""
    if not facts.admits(ChunkVerb.PAUSE):
        raise ChunkNotPausable(chunk_id, facts.status())


class PauseService:
    """Set or clear a chunk's operator pause brake without touching its route."""

    def __init__(
        self, *, lifecycle: IWriteChunkLifecycleRepository, exclusive: IChunkExclusiveWrites, clock: IClock
    ) -> None:
        self._lifecycle = lifecycle
        self._clock = clock
        # ``bzh:store-exclusive-write``.
        self._exclusive = exclusive

    def pause(self, chunk: Chunk, *, by: str) -> int:
        """Append ``chunk.paused``; raises :class:`ChunkNotPausable` outside :attr:`ChunkVerb.PAUSE`'s window
        and :class:`ChunkNotFound` for a chunk gone under the lock.

        The status is re-derived under the row lock, so a status change since the caller's read is
        honored. Returns the freshly-written ``chunk_pause_facts.id``."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            require_pausable(chunk.chunk_id, facts)
            return self._lifecycle.record_pause_locked(handle, chunk.chunk_id, by=by, at=self._clock.now())

    def resume(self, chunk: Chunk, *, by: str) -> int:
        """Append ``chunk.resumed`` — idempotent, never refused (:attr:`ChunkVerb.RESUME`).

        Returns the freshly-written ``chunk_pause_facts.id`` — always a fresh row, never a
        skipped write."""
        return self._lifecycle.record_pause(chunk.chunk_id, paused=False, by=by, at=self._clock.now())
