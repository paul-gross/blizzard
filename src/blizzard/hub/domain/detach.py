"""Chunk detach — the operator's forcible release of a chunk from its runner.

Stamps one fact, ``route.released``, so the chunk re-derives ``ready`` at its current
node; facts append, status derives (``bzh:facts-not-status``). It writes no
``requeue.recorded`` fact, so it supersedes no escalation and bumps no epoch — pinned by
tests/test_chunk_status_derivation.py::test_detached_route_with_an_open_escalation_still_derives_needs_human."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunks.route import IWriteChunkRouteRepository
from blizzard.hub.domain.work import Chunk


class NotRouted(Exception):
    """A detach targeted a chunk with no live route — there is nothing to release."""


class DetachService:
    """Release a chunk from its runner without touching any escalation — ``blizzard hub detach``."""

    def __init__(self, *, route: IWriteChunkRouteRepository, exclusive: IChunkExclusiveWrites, clock: IClock) -> None:
        self._route = route
        # The locked-transaction seam (``bzh:store-exclusive-write``): the route-of check
        # and the release it guards race claim's own ``route_of`` read, so both run
        # inside the same row-locked transaction rather than two separate connections.
        self._exclusive = exclusive
        self._clock = clock

    def detach(self, chunk: Chunk) -> int:
        """Release the chunk's live route so it re-derives ``ready``.

        Raises :class:`NotRouted` if the chunk has no live route — there is nothing to
        release. Returns the freshly-written ``route_released.id`` (the
        activity-feed's key)."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            if handle.route_of(chunk.chunk_id) is None:
                raise NotRouted(f"chunk {chunk.chunk_id} has no live route")
            return self._route.record_route_released_locked(handle, chunk.chunk_id, at=self._clock.now())
