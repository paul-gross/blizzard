"""Chunk release — the holding runner's own mid-graph giving back of its route.

Behaves as detach does in everything but who asks: one ``route.released`` fact, recording the
cause and the caller's reason, so the chunk re-derives ``ready`` at its current node
(``bzh:facts-not-status``). The refusals guard only who holds the chunk at which epoch; none
judges whether letting go is wise, and the reason is recorded, never read."""

from __future__ import annotations

from enum import StrEnum

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb, RouteReleaseCause
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.execution.auth.route import RouteToken
from blizzard.hub.domain.execution.detach import releasable_by
from blizzard.hub.domain.runners.route import Route


class ReleaseRefusalKind(StrEnum):
    """Why :func:`refuse_release` refuses."""

    #: The caller does not hold the chunk's live route.
    NOT_HOLDER = "not-holder"
    #: The chunk's derived status is not one :attr:`ChunkVerb.RELEASE` is legal from.
    NOT_RUNNING = "not-running"
    #: The chunk has moved past the epoch the caller last saw it at.
    STALE = "stale"


class ReleaseRefused(Exception):
    """A release the model refused, or whose route token did not match the live route."""

    def __init__(self, chunk_id: str, kind: ReleaseRefusalKind, detail: str) -> None:
        super().__init__(f"chunk {chunk_id}: {detail}")
        self.chunk_id = chunk_id
        self.kind = kind


def refuse_release(route: Route | None, facts: ChunkFacts, *, runner_id: str, epoch: int) -> ReleaseRefusalKind | None:
    """Why ``runner_id`` may not release ``route`` at ``epoch``, or ``None`` when it may: it
    still holds the route, :attr:`ChunkVerb.RELEASE` is legal from the derived status, and
    ``epoch`` is the chunk's latest."""
    status = facts.status()
    if not releasable_by(route, status, runner_id=runner_id):
        return ReleaseRefusalKind.NOT_HOLDER
    if not facts.admits(ChunkVerb.RELEASE):
        return ReleaseRefusalKind.NOT_RUNNING
    if epoch != (facts.latest_epoch() or 0):
        return ReleaseRefusalKind.STALE
    return None


class ReleaseService:
    """Give a chunk's route back at its holder's own request."""

    def __init__(self, *, route: IWriteChunkRouteRepository, exclusive: IChunkExclusiveWrites, clock: IClock) -> None:
        self._route = route
        # ``bzh:store-exclusive-write``.
        self._exclusive = exclusive
        self._clock = clock

    def release(
        self,
        chunk: Chunk,
        *,
        runner_id: str,
        epoch: int,
        route_token: str | None,
        reason: str,
        route_token_mode: str,
    ) -> int:
        """Release ``chunk``'s live route for ``runner_id``, recording ``reason``.

        Raises :class:`ReleaseRefused` on a route token the live route does not match (under
        ``enforce``) or a refusal :func:`refuse_release` decides. Returns the freshly-written
        ``route_released.id``."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            route = handle.route_of(chunk.chunk_id)
            facts = ChunkFacts.or_default(handle.facts(chunk.chunk_id))
            token_rejection = RouteToken(
                facts=facts,
                presented=route_token,
                submission_runner_id=runner_id,
                route_runner_id=route.runner_id if route is not None else None,
            ).rejection(mode=route_token_mode)
            if token_rejection is not None:
                raise ReleaseRefused(chunk.chunk_id, ReleaseRefusalKind.NOT_HOLDER, token_rejection)
            refusal = refuse_release(route, facts, runner_id=runner_id, epoch=epoch)
            if refusal is not None:
                raise ReleaseRefused(chunk.chunk_id, refusal, f"release refused: {refusal}")
            return self._route.record_route_released_locked(
                handle, chunk.chunk_id, at=self._clock.now(), cause=RouteReleaseCause.RELEASE, reason=reason
            )
