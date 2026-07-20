"""Chunk stop — the operator's terminal abandonment of a chunk (issue #118).

``blizzard hub stop <chunk_id>`` appends the ``chunk_stopped`` fact the schema has
reserved since the walking skeleton (``derive_chunk_status`` honors it first, above
every other state — ``bzh:facts-not-status``); nothing wrote it before this service.
Unlike :mod:`~blizzard.hub.domain.pause` (which keeps the claim) and unlike
:mod:`~blizzard.hub.domain.detach` (which only releases the route, no terminal fact),
stop does **both** in one operation: the fact is written and, if the chunk holds a
live route, it is released too — so the holding runner's existing detach-discovery
(``_reconcile_leases`` / ``_abandon_reassigned``, ``blizzard.runner.loop.steps``)
abandons the lease and frees the environments on its own next tick, with no separate
``detach`` call required. A chunk with no live route (``not_ready``, ``ready``, or
already-detached) is stopped just the same — the route release is conditional, not
required, the one way ``stop`` differs from ``detach``'s own ``NotRouted`` refusal.
Both facts (and a held fleet-wide hub-exec slot, if any) land in **one** store
transaction (:meth:`~blizzard.hub.store.internal.chunk_store.ChunkStore.record_stop`)
— a ``kill -9`` cannot leave the chunk stopped with its route still live, the
partial-stop failure mode the verb exists to prevent (issue #118, pre-push must-fix 2).

Stopping is not retroactive un-delivery: a chunk already ``done`` or ``stopped`` is
refused, mirroring :class:`~blizzard.hub.domain.pause.ChunkNotPausable`'s terminal
guard. Reviving a stopped chunk is out of scope (issue #118) — there is no ``un-stop``.

Holds the *write* chunk repository (``bzh:controller-read-only``); the route resolves
the chunk and delegates here.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work import Chunk, ChunkFacts, ChunkStatus, IWriteChunkRepository, derive_chunk_status

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

    def stop(self, chunk: Chunk, *, by: str) -> None:
        """Append ``chunk.stopped`` and release the chunk's live route (and any held
        hub-exec slot), atomically.

        Raises :class:`ChunkNotStoppable` for a chunk already done/stopped — no fact is
        written and no route touched. Otherwise the chunk derives ``stopped`` from here
        on (``derive_chunk_status`` checks it first) and never re-derives ``ready``."""
        self._require_stoppable(chunk.chunk_id)
        self._chunks.record_stop(chunk.chunk_id, by=by, at=self._clock.now())

    def _require_stoppable(self, chunk_id: str) -> None:
        facts = self._chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
        status = derive_chunk_status(facts)
        if status in _REFUSED:
            raise ChunkNotStoppable(chunk_id, status)
