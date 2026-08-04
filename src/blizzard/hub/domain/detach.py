"""Chunk detach — the operator's forcible release of a chunk from its runner.

``blizzard hub detach <chunk_id>`` stamps one fact, ``route.released``, so the chunk
re-derives ``ready`` and re-enters the ready queue at its current node — the holding
runner learns of the release on its own next tick and closes the lease (the
runner half lives in ``blizzard.runner.loop.steps``, not here). Facts append, status
derives (``bzh:facts-not-status``).

Detach is deliberately **not** requeue (:class:`blizzard.hub.domain.decisions.RequeueService`):
it writes no ``requeue.recorded`` fact, so it supersedes no escalation and bumps no epoch.
Pinned by
tests/test_chunk_status_derivation.py::test_detached_route_with_an_open_escalation_still_derives_needs_human.

Holds the *write* chunk repository (``bzh:controller-read-only``); the route resolves
the chunk and delegates here.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work import Chunk, IWriteChunkRepository


class NotRouted(Exception):
    """A detach targeted a chunk with no live route — there is nothing to release."""


class DetachService:
    """Release a chunk from its runner without touching any escalation — ``blizzard hub detach``."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def detach(self, chunk: Chunk) -> int:
        """Release the chunk's live route so it re-derives ``ready``.

        Raises :class:`NotRouted` if the chunk has no live route — there is nothing to
        release. Returns the freshly-written ``route_released.id`` (issue #213's
        activity-feed key)."""
        if self._chunks.route_of(chunk.chunk_id) is None:
            raise NotRouted(f"chunk {chunk.chunk_id} has no live route")
        return self._chunks.record_route_released(chunk.chunk_id, at=self._clock.now())
