"""Chunk detach — the operator's forcible release of a chunk from its runner.

Stamps one fact, ``route.released``, so the chunk re-derives ``ready`` at its current
node; facts append, status derives (``bzh:facts-not-status``). It writes no
``requeue.recorded`` fact, so it supersedes no escalation and bumps no epoch — pinned by
tests/test_chunk_status_derivation.py::test_detached_route_with_an_open_escalation_still_derives_needs_human."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, holds_claim
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.runners.route import Route

#: The statuses detach may release a live route from: every non-terminal one, ``delivering`` included.
DETACHABLE: frozenset[ChunkStatus] = frozenset(ChunkStatus) - TERMINAL_STATUSES


class NotRouted(Exception):
    """A detach targeted a chunk with no live route, or one whose route holds no claim — there is
    nothing to release."""


def holds(status: ChunkStatus | None) -> bool:
    """Whether a live route on a chunk at ``status`` is still a holding: a terminal chunk's leftover
    route is none. A chunk with no status facts counts as held."""
    return status is None or holds_claim(status)


def held_routes(routes: Sequence[Route], statuses: Mapping[str, ChunkStatus]) -> list[Route]:
    """The routes among ``routes`` that still hold their chunk — retirement's holdings."""
    return [route for route in routes if holds(statuses.get(route.chunk_id))]


def refuse_detach(chunk_id: str, route: Route | None, status: ChunkStatus | None) -> None:
    """Raise :class:`NotRouted` unless the operator may release ``route`` from a chunk at
    ``status`` (:data:`DETACHABLE`)."""
    if route is None:
        raise NotRouted(f"chunk {chunk_id} has no live route")
    if status is not None and status not in DETACHABLE:
        raise NotRouted(f"chunk {chunk_id} is {status}: its route holds no claim")


def releasable_by(route: Route | None, status: ChunkStatus | None, *, runner_id: str) -> bool:
    """Whether retirement's release pass may release ``route`` for ``runner_id``: only while that
    runner still holds it and the chunk still holds a claim."""
    return route is not None and route.runner_id == runner_id and holds(status)


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

        Raises :class:`NotRouted` if the chunk has no live route, or its route is left on a
        terminal chunk — there is nothing to release. Returns the freshly-written
        ``route_released.id`` (the activity-feed's key)."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            route = handle.route_of(chunk.chunk_id)
            status = _status(handle.facts(chunk.chunk_id)) if route is not None else None
            refuse_detach(chunk.chunk_id, route, status)
            return self._route.record_route_released_locked(handle, chunk.chunk_id, at=self._clock.now())

    def release_held(self, chunk: Chunk, *, runner_id: str) -> int | None:
        """:meth:`detach`, only while ``runner_id`` still holds the chunk — retirement's
        release pass. ``None`` when the route is gone, another runner's by now, or left on a
        terminal chunk, which holds no claim to release."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            route = handle.route_of(chunk.chunk_id)
            status = _status(handle.facts(chunk.chunk_id)) if route is not None else None
            if not releasable_by(route, status, runner_id=runner_id):
                return None
            return self._route.record_route_released_locked(handle, chunk.chunk_id, at=self._clock.now())


def _status(facts: ChunkFacts | None) -> ChunkStatus | None:
    return None if facts is None else facts.status()
