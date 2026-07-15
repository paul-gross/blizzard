"""Chunk promotion — flip a not-ready chunk to ready (D-103).

The other half of the readiness lifecycle :mod:`blizzard.hub.domain.ingest` opens: ingest
mints a chunk in a NOT-READY resting state (visible on the board, never claimed), and
``POST /chunks/{id}/promote`` appends the ``chunk.promoted`` fact that flips it to ``ready``
so a runner may claim it on a subsequent tick. Facts append, status derives
(``bzh:facts-not-status``): promotion is one fact, and readiness is re-derived from it.

Holds the *write* chunk repository (``bzh:controller-read-only``); the route resolves the
chunk and delegates here. Promotion is idempotent (the store keeps the first fact), so an
already-ready or already-running chunk is a harmless no-op — there is no not-ready
precondition to fail.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work import IWriteChunkRepository


class PromoteService:
    """Promote a not-ready chunk to ready — ``blizzard hub promote`` (D-103)."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def promote(self, chunk_id: str) -> None:
        """Append the ``chunk.promoted`` fact so the chunk re-derives ``ready`` (D-103).

        Idempotent: a chunk already promoted keeps its first fact, so a repeated promote
        changes nothing."""
        self._chunks.record_promote(chunk_id, at=self._clock.now())
