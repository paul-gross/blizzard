"""Chunk stop — the operator's terminal abandonment of a chunk (issue #118).

Appends the ``chunk_stopped`` fact, which ``derive_chunk_status`` honors above every other
state (``bzh:facts-not-status``), and conditionally releases a live route — both in one
store transaction, so a ``kill -9`` cannot leave a chunk stopped with its route still
live. Terminal and one-way: an already done or stopped chunk is refused."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work import Chunk, ChunkFacts, ChunkStatus, IWriteChunkRepository

_REFUSED = frozenset({ChunkStatus.DONE, ChunkStatus.STOPPED})


class ChunkNotStoppable(Exception):
    """A stop targeted a chunk already terminal ({done, stopped}) — not retroactive."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not stoppable")
        self.chunk_id = chunk_id
        self.status = status


class StopService:
    """Terminally abandon a chunk and release any route it holds — ``blizzard hub stop``."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def stop(self, chunk: Chunk, *, by: str) -> int:
        """Append ``chunk.stopped`` and release the chunk's live route (and any held
        hub-exec slot), atomically.

        Raises :class:`ChunkNotStoppable` for a chunk already done/stopped — no fact
        written, no route touched. Returns the ``chunk_stopped.id`` (issue #213)."""
        self._require_stoppable(chunk.chunk_id)
        return self._chunks.record_stop(chunk.chunk_id, by=by, at=self._clock.now())

    def _require_stoppable(self, chunk_id: str) -> None:
        facts = self._chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
        status = facts.status()
        if status in _REFUSED:
            raise ChunkNotStoppable(chunk_id, status)
