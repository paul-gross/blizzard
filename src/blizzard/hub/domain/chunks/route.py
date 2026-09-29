"""The chunk-route repository seam — the live runner/workspace/env claim on
a chunk, its capability token, and the per-runner applied-seq high-water mark."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.chunks.exclusive import ILockedChunkRead
from blizzard.hub.domain.fleet import Route


class IReadChunkRouteRepository(Protocol):
    """Read-only chunk-route access."""

    def route_of(self, chunk_id: str) -> Route | None:
        """The chunk's live route (runner/workspace/envs), or None if unclaimed/released."""
        ...

    def load_all_routes(self) -> dict[str, Route]:
        """Every chunk's live route, keyed by chunk id — the bulk counterpart to
        :meth:`route_of`, bounded the way ``load_all_facts`` is. A chunk
        with no live route is absent from the dict, as :meth:`route_of` returns ``None``."""
        ...

    def routes_for(self, chunk_ids: Iterable[str]) -> dict[str, Route]:
        """The given chunks' live routes, keyed by chunk id — the by-id-set bulk read
        between :meth:`route_of`'s one-chunk read and :meth:`load_all_routes`'s
        whole-fleet one. A chunk with no live route is absent from the
        dict, as :meth:`route_of` returns ``None``."""
        ...

    def live_routes_of_runner(self, runner_id: str) -> list[Route]:
        """Every live route ``runner_id`` holds, ordered by chunk id — a runner's holdings,
        each carrying its environments. Not a hot-path read: retirement asks it once per verb,
        so it narrows by the runner's own route history rather than a live-set prefilter."""
        ...

    def runner_high_water(self, runner_id: str) -> int:
        """The greatest per-runner seq the hub has already applied, or 0."""
        ...


class IWriteChunkRouteRepository(IReadChunkRouteRepository, Protocol):
    """Read-write chunk-route access."""

    def record_route_locked(self, handle: ILockedChunkRead, route: Route, *, token_hash: str, at: datetime) -> str:
        """Record the route **and** mint its capability token's fact, atomically, on
        ``handle``'s already-locked connection (``bzh:store-exclusive-write``).

        ``token_hash`` is the sha256 digest of the plaintext token, already hashed by the
        caller (``bzh:domain-takes-objects``); the token fact lands in the same write,
        never as a column on the route fact. Returns the minted ``route_id``."""
        ...

    def record_route_released_locked(self, handle: ILockedChunkRead, chunk_id: str, *, at: datetime) -> int:
        """Append the ``route.released`` fact, on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``) — detach and requeue race claim's own
        ``route_of`` guard read exactly as the migrated writers do. Returns the
        freshly-written ``route_released.id`` (the activity-feed's key)."""
        ...

    def record_route_token(self, chunk_id: str, *, token_hash: str, at: datetime) -> None:
        """Append a fresh :class:`RouteTokenMintedFact` for the chunk's route — the re-key
        path. Never mutates the prior token fact (``bzh:facts-not-status``):
        :attr:`RouteHistory.newest_token` supersedes it with no separate revocation step."""
        ...

    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None: ...
    def set_runner_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        """Advance a runner's applied-seq high-water mark (upsert)."""
        ...
