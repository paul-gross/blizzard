"""Chunk detach — the operator's forcible release of a chunk from its runner.

``blizzard hub detach <chunk_id>`` stamps one fact, ``route.released``, so the chunk
re-derives ``ready`` and re-enters the ready queue at its current node — the holding
runner learns of the release on its own next tick and closes the lease (the
runner half lives in ``blizzard.runner.loop.steps``, not here). Facts append, status
derives (``bzh:facts-not-status``).

Detach is deliberately **not** requeue (:class:`blizzard.hub.domain.decisions.RequeueService`):
it writes no ``requeue.recorded`` fact, so it supersedes no escalation and bumps no
epoch. A ``needs_human`` chunk detached this way still derives ``needs_human`` — the
runner is released, but the escalation stays open until a human requeues it.
Detach is a fleet/route operation, not a human-gate one, which is also why it lives in
its own module rather than beside :mod:`blizzard.hub.domain.decisions` (that module's
rules are scoped to gate decisions and requeue-supersession closure).

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

    def detach(self, chunk: Chunk) -> None:
        """Release the chunk's live route so it re-derives ``ready``.

        Raises :class:`NotRouted` if the chunk has no live route — there is nothing to
        release. No supersession fact is written and no epoch is bumped: unlike requeue,
        detach never touches an open escalation."""
        if self._chunks.route_of(chunk.chunk_id) is None:
            raise NotRouted(f"chunk {chunk.chunk_id} has no live route")
        self._chunks.record_route_released(chunk.chunk_id, at=self._clock.now())
