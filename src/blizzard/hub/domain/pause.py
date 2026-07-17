"""Chunk pause — the operator's per-chunk brake, orthogonal to detach and the runner
brake (issue #46).

``blizzard hub pause-chunk <chunk_id>`` stamps a ``chunk.paused`` fact; ``resume-chunk``
stamps ``chunk.resumed``. Newest-fact-wins (``PauseFact``/``_is_paused``,
``domain/work.py``), so a re-pause after a resume derives ``paused`` again with no
extra bookkeeping. Unlike detach, pause **keeps the claim** — no route is released, no
epoch bumped; the runner half (killing the worker and parking the lease without
releasing it) lives in ``blizzard.runner.loop.steps``, not here.

Structurally mirrors :class:`~blizzard.hub.domain.detach.DetachService`, but the
refusal mirrors :meth:`~blizzard.hub.domain.queue.QueueService._require_ready`: load
facts, derive status, compare, raise a typed exception. Pause refuses only
``{done, stopped, delivering}`` — a chunk already ``waiting_on_human``/``needs_human``
may still be paused (the lever stays broad); resume is never refused; appending
``paused=False`` to an already-unpaused chunk is a harmless no-op (newest-fact-wins),
matching ``POST /runners/{id}/resume``.

Holds the *write* chunk repository (``bzh:controller-read-only``); the route resolves
the chunk and delegates here.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work import Chunk, ChunkFacts, ChunkStatus, IWriteChunkRepository, derive_chunk_status

_REFUSED = frozenset({ChunkStatus.DONE, ChunkStatus.STOPPED, ChunkStatus.DELIVERING})


class ChunkNotPausable(Exception):
    """A pause targeted a chunk in a status pause can't touch ({done, stopped, delivering})."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not pausable")
        self.chunk_id = chunk_id
        self.status = status


class PauseService:
    """Set or clear a chunk's operator pause brake without touching its route (issue #46)."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def pause(self, chunk: Chunk, *, by: str) -> None:
        """Append ``chunk.paused``; raises :class:`ChunkNotPausable` for done/stopped/delivering.

        No route or lease is touched here — the runner reads the resulting
        :class:`~blizzard.hub.domain.work.PauseFact` off its next ``get_chunk`` and
        kills/parks the worker itself, keeping the claim (issue #46 §3.1)."""
        self._require_pausable(chunk.chunk_id)
        self._chunks.record_pause(chunk.chunk_id, paused=True, by=by, at=self._clock.now())

    def resume(self, chunk: Chunk, *, by: str) -> None:
        """Append ``chunk.resumed`` — idempotent, never refused (matches runner resume)."""
        self._chunks.record_pause(chunk.chunk_id, paused=False, by=by, at=self._clock.now())

    def _require_pausable(self, chunk_id: str) -> None:
        facts = self._chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
        status = derive_chunk_status(facts)
        if status in _REFUSED:
            raise ChunkNotPausable(chunk_id, status)
