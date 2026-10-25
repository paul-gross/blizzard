"""SQLAlchemy adapter for the chunk route seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from sqlalchemy import Connection

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.model import RouteReleaseCause
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochOwner, FenceRefusal
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.runners.route import Route
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import (
    conn_of,
    latest_epoch,
    lock_chunk_row,
    mint_admission,
    next_route_seq,
    record_epoch_owner,
    route_of_conn,
    routes_of_conn,
)
from blizzard.hub.store.scoped import ScopedConnections

_ROUTE_PREFIX = "route"


class ChunkRouteStore:
    """The chunk's claim/route lifecycle — created, released, re-keyed."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def route_of(self, chunk_id: str) -> Route | None:
        """The chunk's live route, or ``None`` if its newest release has caught up to it."""
        with self._store.read("route_of") as conn:
            return route_of_conn(conn, chunk_id)

    def load_all_routes(self) -> dict[str, Route]:
        """See :meth:`~blizzard.hub.domain.chunk.ports.route.IReadChunkRouteRepository.load_all_routes` —
        one bounded query per route table, each chunk's newest fact picked in SQL, deferring liveness
        to the same :class:`~blizzard.hub.domain.chunk.model.RouteHistory.newest` tie-break
        :func:`route_of_conn` uses. Shared with :meth:`routes_for` via :meth:`_routes`."""
        with self._store.read("load_all_routes") as conn:
            return self._routes(conn, None)

    def routes_for(self, chunk_ids: Iterable[str]) -> dict[str, Route]:
        """See :meth:`~blizzard.hub.domain.chunk.ports.route.IReadChunkRouteRepository.routes_for` —
        :meth:`load_all_routes`'s own shape, scoped to ``ids`` via
        :meth:`_routes`'s ``chunk_id.in_(ids)`` filters rather than a fleet-wide read."""
        ids = list(chunk_ids)
        if not ids:
            return {}
        with self._store.read("routes_for") as conn:
            return self._routes(conn, ids)

    def live_routes_of_runner(self, runner_id: str) -> list[Route]:
        """See :meth:`~blizzard.hub.domain.chunk.ports.route.IReadChunkRouteRepository.live_routes_of_runner` —
        the chunks this runner ever routed, resolved through :meth:`_routes`, kept where the
        live route is still this runner's."""
        with self._store.read("live_routes_of_runner") as conn:
            chunk_ids = [
                row.chunk_id
                for row in conn.execute(
                    self._store.select(s.route_created, s.route_created.c.chunk_id)
                    .where(s.route_created.c.runner_id == runner_id)
                    .distinct()
                ).all()
            ]
            if not chunk_ids:
                return []
            routes = self._routes(conn, chunk_ids)
        return sorted((r for r in routes.values() if r.runner_id == runner_id), key=lambda r: r.chunk_id)

    def _routes(self, conn, chunk_ids: list[str] | None) -> dict[str, Route]:
        """:meth:`load_all_routes`/:meth:`routes_for`'s shared entry — ``chunk_ids=None``
        reads fleet-wide in one pass; otherwise batches through :func:`id_batches`
        (`bzh:bulk-reconstitution`)."""
        if chunk_ids is None:
            return self._routes_batch(conn, None)
        result: dict[str, Route] = {}
        for batch in id_batches(chunk_ids):
            result.update(self._routes_batch(conn, batch))
        return result

    def _routes_batch(self, conn, chunk_ids: Sequence[str] | None) -> dict[str, Route]:
        """One batch (or the whole fleet, when ``chunk_ids`` is ``None``) — :func:`routes_of_conn`,
        shared by both of :meth:`_routes`'s branches and by :func:`route_of_conn`."""
        return routes_of_conn(conn, chunk_ids)

    def runner_high_water(self, runner_id: str) -> int:
        with self._store.read("runner_high_water") as conn:
            row = conn.execute(
                self._store.select(s.runner_high_water, s.runner_high_water.c.seq).where(
                    s.runner_high_water.c.runner_id == runner_id
                )
            ).one_or_none()
            return int(row.seq) if row is not None else 0

    def record_route_locked(self, handle: ILockedChunkRead, route: Route, *, token_hash: str, at: datetime) -> str:
        """The claim's own route-and-token write
        (``bzh:store-exclusive-write``), on the connection its row lock was already taken
        on, rather than a fresh transaction. It also reserves the chunk's next epoch for the
        claiming runner — itself a fencing write: the prior holder's writes at or below it
        are refused from this commit on."""
        return self._record_route_conn(conn_of(handle), route, token_hash=token_hash, at=at)

    def _record_route_conn(self, conn, route: Route, *, token_hash: str, at: datetime) -> str:
        route_id = Id.mint(_ROUTE_PREFIX, self._clock).value
        conn.execute(
            self._store.insert(s.route_created).values(
                route_id=route_id,
                chunk_id=route.chunk_id,
                runner_id=route.runner_id,
                workspace_id=route.workspace_id,
                created_at=at,
                seq=next_route_seq(conn, route.chunk_id),
            )
        )
        for env_id in route.environment_ids:
            conn.execute(self._store.insert(s.route_environments).values(route_id=route_id, environment_id=env_id))
        conn.execute(
            self._store.insert(s.route_token_minted).values(
                chunk_id=route.chunk_id,
                token_hash=token_hash,
                seq=next_route_seq(conn, route.chunk_id),
                minted_at=at,
            )
        )
        reserved = latest_epoch(conn, route.chunk_id) + 1
        record_epoch_owner(conn, route.chunk_id, reserved, EpochOwner.runner(route.runner_id), at=at)
        return route_id

    def record_route_released_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        at: datetime,
        cause: RouteReleaseCause | None = None,
        reason: str | None = None,
    ) -> int:
        return self._record_route_released_conn(conn_of(handle), chunk_id, at=at, cause=cause, reason=reason)

    def _record_route_released_conn(
        self, conn: Connection, chunk_id: str, *, at: datetime, cause: RouteReleaseCause | None, reason: str | None
    ) -> int:
        result = conn.execute(
            self._store.insert(s.route_released).values(
                chunk_id=chunk_id,
                released_at=at,
                seq=next_route_seq(conn, chunk_id),
                cause=None if cause is None else cause.value,
                reason=reason,
            )
        )
        key = result.inserted_primary_key
        return int(key[0]) if key is not None else 0

    def record_route_token_locked(
        self, handle: ILockedChunkRead, chunk_id: str, *, token_hash: str, at: datetime
    ) -> None:
        """Append a fresh ``route_token_minted`` fact — the re-key path — on ``handle``'s already-locked
        connection (``bzh:store-exclusive-write``). Same allocator as :meth:`record_route_locked`'s own token
        fact, its own call rather than a fixed +1, so it stays correctly ordered against a concurrent
        create/release/re-key on this chunk."""
        conn_of(handle).execute(
            self._store.insert(s.route_token_minted).values(
                chunk_id=chunk_id,
                token_hash=token_hash,
                seq=next_route_seq(conn_of(handle), chunk_id),
                minted_at=at,
            )
        )

    def record_lease_minted(
        self, chunk_id: str, *, epoch: int, claimant: Claimant, at: datetime
    ) -> FenceRefusal | None:
        with self._store.write("record_lease_minted") as conn:
            lock_chunk_row(conn, chunk_id)
            admission = mint_admission(conn, chunk_id, epoch=epoch, runner_id=claimant.runner_id)
            refusal = admission.refusal(claimant)
            if refusal is not None:
                return refusal
            if admission.takes_ownership():
                record_epoch_owner(conn, chunk_id, epoch, EpochOwner.runner(claimant.runner_id), at=at)
            conn.execute(
                self._store.insert(s.lease_facts).values(
                    chunk_id=chunk_id,
                    epoch=epoch,
                    runner_id=claimant.runner_id,
                    minted_at=at,
                    lease_id=claimant.lease_id,
                )
            )
            return None

    def set_runner_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        with self._store.write("set_runner_high_water") as conn:
            existing = conn.execute(
                self._store.select(s.runner_high_water, s.runner_high_water.c.runner_id).where(
                    s.runner_high_water.c.runner_id == runner_id
                )
            ).one_or_none()
            if existing is None:
                conn.execute(
                    self._store.insert(s.runner_high_water).values(runner_id=runner_id, seq=seq, updated_at=at)
                )
            else:
                conn.execute(
                    self._store.update(s.runner_high_water)
                    .where(s.runner_high_water.c.runner_id == runner_id)
                    .values(seq=seq, updated_at=at)
                )


def _conforms_route(x: ChunkRouteStore) -> IWriteChunkRouteRepository:
    return x
