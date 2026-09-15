"""Chunk pause — the operator's per-chunk brake, orthogonal to detach (issue #46).

Pause stamps a ``chunk.paused`` fact and resume a ``chunk.resumed``; newest-fact-wins,
so a re-pause after a resume derives ``paused`` again. Pause **keeps the claim** — no
route released, no epoch bumped — and refuses only ``{done, stopped, delivering}``;
resume is never refused. Holds the *write* lifecycle seam (``bzh:controller-read-only``)."""

from __future__ import annotations

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.work import Chunk, ChunkFacts

_REFUSED = frozenset({ChunkStatus.DONE, ChunkStatus.STOPPED, ChunkStatus.DELIVERING})


class ChunkNotPausable(Exception):
    """A pause targeted a chunk in a status pause can't touch ({done, stopped, delivering})."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not pausable")
        self.chunk_id = chunk_id
        self.status = status


class PauseService:
    """Set or clear a chunk's operator pause brake without touching its route (issue #46)."""

    def __init__(self, *, lifecycle: IWriteChunkLifecycleRepository, clock: IClock) -> None:
        self._lifecycle = lifecycle
        self._clock = clock

    def pause(self, chunk: Chunk, *, facts: ChunkFacts, by: str) -> int:
        """Append ``chunk.paused``; raises :class:`ChunkNotPausable` for done/stopped/delivering.

        Takes the caller's already-loaded ``facts`` (``bzh:domain-takes-objects``) rather than
        reloading them. No route or lease is touched here. Returns the freshly-written
        ``chunk_pause_facts.id``."""
        self._require_pausable(chunk.chunk_id, facts)
        return self._lifecycle.record_pause(chunk.chunk_id, paused=True, by=by, at=self._clock.now())

    def resume(self, chunk: Chunk, *, by: str) -> int:
        """Append ``chunk.resumed`` — idempotent, never refused (matches runner resume).

        Returns the freshly-written ``chunk_pause_facts.id`` — always a fresh row, never a
        skipped write."""
        return self._lifecycle.record_pause(chunk.chunk_id, paused=False, by=by, at=self._clock.now())

    def _require_pausable(self, chunk_id: str, facts: ChunkFacts) -> None:
        status = facts.status()
        if status in _REFUSED:
            raise ChunkNotPausable(chunk_id, status)
