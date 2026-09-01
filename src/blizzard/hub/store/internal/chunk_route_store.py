"""SQLAlchemy adapter for the chunk route seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id
from blizzard.hub.domain.chunks.route import IWriteChunkRouteRepository
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.work import RouteCreatedFact, RouteHistory, RouteReleasedFact
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import next_route_seq, route_of_conn

_ROUTE_PREFIX = "route"


class ChunkRouteStore:
    """The chunk's claim/route lifecycle — created, released, re-keyed."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def route_of(self, chunk_id: str) -> Route | None:
        """The chunk's live route, or ``None`` if its newest release has caught up to it."""
        with self._store.read("route_of") as conn:
            return route_of_conn(conn, chunk_id)

    def load_all_routes(self) -> dict[str, Route]:
        """See :meth:`~blizzard.hub.domain.chunks.route.IReadChunkRouteRepository.load_all_routes`
        (issue #421) — one bounded query per route table, grouped by chunk id in Python the
        way the facts seam's ``load_all_facts`` is, deferring liveness to the same
        :class:`~blizzard.hub.domain.work.RouteHistory.newest` tie-break :func:`route_of_conn`
        uses."""
        with self._store.read("load_all_routes") as conn:
            newest_created: dict[str, RouteCreatedFact] = {}
            route_id_of: dict[str, str] = {}
            runner_of: dict[str, str] = {}
            workspace_of: dict[str, str] = {}
            for r in conn.execute(select(s.route_created)).all():
                existing = newest_created.get(r.chunk_id)
                if existing is None or (r.created_at, r.seq) > (existing.created_at, existing.seq):
                    newest_created[r.chunk_id] = RouteCreatedFact(created_at=r.created_at, seq=r.seq)
                    route_id_of[r.chunk_id] = r.route_id
                    runner_of[r.chunk_id] = r.runner_id
                    workspace_of[r.chunk_id] = r.workspace_id

            newest_released: dict[str, RouteReleasedFact] = {}
            for r in conn.execute(
                select(s.route_released.c.chunk_id, s.route_released.c.released_at, s.route_released.c.seq)
            ).all():
                existing = newest_released.get(r.chunk_id)
                if existing is None or (r.released_at, r.seq) > (existing.released_at, existing.seq):
                    newest_released[r.chunk_id] = RouteReleasedFact(released_at=r.released_at, seq=r.seq)

            live_chunk_ids = {
                chunk_id
                for chunk_id, created in newest_created.items()
                if RouteHistory([created], [newest_released[chunk_id]] if chunk_id in newest_released else []).newest
                is not None
            }
            if not live_chunk_ids:
                return {}

            route_ids = {route_id_of[chunk_id] for chunk_id in live_chunk_ids}
            env_ids: dict[str, list[str]] = defaultdict(list)
            for e in conn.execute(
                select(s.route_environments.c.route_id, s.route_environments.c.environment_id).where(
                    s.route_environments.c.route_id.in_(route_ids)
                )
            ).all():
                env_ids[e.route_id].append(e.environment_id)

            return {
                chunk_id: Route(
                    chunk_id=chunk_id,
                    runner_id=runner_of[chunk_id],
                    workspace_id=workspace_of[chunk_id],
                    environment_ids=env_ids[route_id_of[chunk_id]],
                    created_at=newest_created[chunk_id].created_at,
                    route_id=route_id_of[chunk_id],
                )
                for chunk_id in live_chunk_ids
            }

    def runner_high_water(self, runner_id: str) -> int:
        with self._store.read("runner_high_water") as conn:
            row = conn.execute(
                select(s.runner_high_water.c.seq).where(s.runner_high_water.c.runner_id == runner_id)
            ).one_or_none()
            return int(row.seq) if row is not None else 0

    def record_route(self, route: Route, *, token_hash: str, at: datetime) -> str:
        """Record the route and mint its capability token's fact, one transaction (issue #84a).

        The token fact is a second row on the same shared per-chunk seq counter
        (:func:`~blizzard.hub.store.internal.chunk_rows.next_route_seq`), allocated by its
        own call to the allocator, never a fixed +1. Returns the freshly-minted
        ``route_created.route_id`` (issue #213)."""
        route_id = Id.mint(_ROUTE_PREFIX, self._clock).value
        with self._store.write("record_route") as conn:
            conn.execute(
                s.route_created.insert().values(
                    route_id=route_id,
                    chunk_id=route.chunk_id,
                    runner_id=route.runner_id,
                    workspace_id=route.workspace_id,
                    created_at=at,
                    seq=next_route_seq(conn, route.chunk_id),
                )
            )
            for env_id in route.environment_ids:
                conn.execute(s.route_environments.insert().values(route_id=route_id, environment_id=env_id))
            conn.execute(
                s.route_token_minted.insert().values(
                    chunk_id=route.chunk_id,
                    token_hash=token_hash,
                    seq=next_route_seq(conn, route.chunk_id),
                    minted_at=at,
                )
            )
            return route_id

    def record_route_released(self, chunk_id: str, *, at: datetime) -> int:
        with self._store.write("record_route_released") as conn:
            result = conn.execute(
                s.route_released.insert().values(chunk_id=chunk_id, released_at=at, seq=next_route_seq(conn, chunk_id))
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0

    def record_route_token(self, chunk_id: str, *, token_hash: str, at: datetime) -> None:
        """Append a fresh ``route_token_minted`` fact — the re-key path (issue #84b).
        Same allocator as :meth:`record_route`'s own token fact, its own call rather
        than a fixed +1, so it stays correctly ordered against a concurrent
        create/release/re-key on this chunk."""
        with self._store.write("record_route_token") as conn:
            conn.execute(
                s.route_token_minted.insert().values(
                    chunk_id=chunk_id,
                    token_hash=token_hash,
                    seq=next_route_seq(conn, chunk_id),
                    minted_at=at,
                )
            )

    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None:
        with self._store.write("record_lease") as conn:
            conn.execute(
                s.lease_facts.insert().values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )

    def set_runner_high_water(self, runner_id: str, *, seq: int, at: datetime) -> None:
        with self._store.write("set_runner_high_water") as conn:
            existing = conn.execute(
                select(s.runner_high_water.c.runner_id).where(s.runner_high_water.c.runner_id == runner_id)
            ).one_or_none()
            if existing is None:
                conn.execute(s.runner_high_water.insert().values(runner_id=runner_id, seq=seq, updated_at=at))
            else:
                conn.execute(
                    s.runner_high_water.update()
                    .where(s.runner_high_water.c.runner_id == runner_id)
                    .values(seq=seq, updated_at=at)
                )


def _conforms_route(x: ChunkRouteStore) -> IWriteChunkRouteRepository:
    return x
