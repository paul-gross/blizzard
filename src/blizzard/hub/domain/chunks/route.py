"""The chunk-route repository seam — the live runner/workspace/env claim on
a chunk, its capability token, and the per-runner applied-seq high-water mark."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.fleet import Route


class IReadChunkRouteRepository(Protocol):
    """Read-only chunk-route access."""

    def route_of(self, chunk_id: str) -> Route | None:
        """The chunk's live route (runner/workspace/envs), or None if unclaimed/released."""
        ...

    def load_all_routes(self) -> dict[str, Route]:
        """Every chunk's live route, keyed by chunk id — the bulk counterpart to
        :meth:`route_of` (issue #421), bounded the way ``load_all_facts`` is. A chunk
        with no live route is absent from the dict, as :meth:`route_of` returns ``None``."""
        ...

    def runner_high_water(self, runner_id: str) -> int:
        """The greatest per-runner seq the hub has already applied, or 0."""
        ...


class IWriteChunkRouteRepository(IReadChunkRouteRepository, Protocol):
    """Read-write chunk-route access."""

    def record_route(self, route: Route, *, token_hash: str, at: datetime) -> str:
        """Record the route **and** mint its capability token's fact, atomically (issue #84a).

        ``token_hash`` is the sha256 digest of the plaintext token, already hashed by the
        caller (``bzh:domain-takes-objects``); the token fact lands in the same store
        write, never as a column on the route fact. Returns the minted ``route_id``."""
        ...

    def record_route_released(self, chunk_id: str, *, at: datetime) -> int:
        """Append the ``route.released`` fact. Returns the freshly-written
        ``route_released.id`` (issue #213's activity-feed key)."""
        ...

    def record_route_token(self, chunk_id: str, *, token_hash: str, at: datetime) -> None:
        """Append a fresh :class:`RouteTokenMintedFact` for the chunk's route — the re-key
        path (issue #84b). Never mutates the prior token fact (``bzh:facts-not-status``):
        :attr:`RouteHistory.newest_token` supersedes it with no separate revocation step."""
        ...

    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None: ...
    def set_runner_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        """Advance a runner's applied-seq high-water mark (upsert)."""
        ...
