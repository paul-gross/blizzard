"""The chunk-route repository seam — the live runner/workspace/env claim on
a chunk, its capability token, and the per-runner applied-seq high-water mark."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.chunk.model import RouteReleaseCause
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, FenceRefusal
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.runners.lifecycle_version import RunnerLifecycleVersion
from blizzard.hub.domain.runners.route import Route


class IReadChunkRouteRepository(Protocol):
    """Read-only chunk-route access."""

    def route_of(self, chunk_id: str, *, transaction: Transaction | None = None) -> Route | None:
        """The chunk's live route (runner/workspace/envs), or None if unclaimed/released — read in
        ``transaction`` when given (``bzh:transaction-participation``)."""
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
        each carrying its environments. Not a hot-path read."""
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

    def record_route_released_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        at: datetime,
        cause: RouteReleaseCause | None = None,
        reason: str | None = None,
        expected_runner: RunnerLifecycleVersion | None = None,
    ) -> int:
        """Append the ``route.released`` fact, on ``handle``'s already-locked connection
        (``bzh:store-exclusive-write``), recording which write gave tenure back and, on a
        release, the caller's reason. With ``expected_runner`` it lands only while that runner
        stands at that lifecycle version — :class:`~blizzard.hub.domain.runners.registration.RunnerLifecycleConflict`
        otherwise, appending nothing. Returns the freshly-written ``route_released.id``."""
        ...

    def record_route_token_locked(
        self, handle: ILockedChunkRead, chunk_id: str, *, token_hash: str, at: datetime
    ) -> None:
        """Append a fresh :class:`RouteTokenMintedFact` for the chunk's route — the re-key
        path — on ``handle``'s already-locked connection (``bzh:store-exclusive-write``). Never mutates the prior
        token fact (``bzh:facts-not-status``):
        :attr:`RouteHistory.newest_token` supersedes it with no separate revocation step."""
        ...

    def record_lease_minted(
        self, chunk_id: str, *, epoch: int, claimant: Claimant, at: datetime, transaction: Transaction | None = None
    ) -> FenceRefusal | None:
        """Land a runner's ``lease.minted`` under the chunk's row lock, admitted by
        :class:`~blizzard.hub.domain.chunk.ports.fence.MintAdmission` — ``None`` when it landed (the first mint at
        an unowned epoch taking it for ``claimant``'s runner), else the refusal, which lands nothing. Written in
        ``transaction`` when given, a unit that locks the chunk, else in a write of its own that locks it first
        (``bzh:transaction-participation``)."""
        ...

    def set_runner_high_water(
        self, runner_id: str, *, seq: int, expected: int, at: datetime, transaction: Transaction
    ) -> None:
        """Advance a runner's applied-seq high-water mark from ``expected``, the seq its push last read or set, to
        ``seq``, in the unit that lands the fact ``seq`` covers. Raises
        :class:`~blizzard.hub.domain.kernel.high_water.HighWaterConflict` when the mark no longer stands at
        ``expected``, moving nothing (``bzh:write-reestablishes-read``)."""
        ...
