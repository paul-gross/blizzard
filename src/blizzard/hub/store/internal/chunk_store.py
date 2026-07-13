"""SQLAlchemy adapter for the chunk repository seam (package-private).

Implements :class:`~blizzard.hub.domain.work.IWriteChunkRepository` over the hub's
fact tables. All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``);
the domain sees only :class:`~blizzard.hub.domain.work.Chunk`,
:class:`~blizzard.hub.domain.work.ChunkFacts`, artifact rows, and routes.

Facts only (``bzh:facts-not-status``): every write appends a row that happened, and
status is **derived** by :func:`~blizzard.hub.domain.work.derive_chunk_status` over
:meth:`load_facts` — never read from a column. The transition-and-artifacts write is
one transaction (D-036 atomicity). Timestamps arrive already stamped from the
injected clock (``bzh:injected-clock``); the store never calls ``datetime.now``
except to source the ULID instant of a surrogate route id.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Engine, insert, select

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import mint
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.graph import Executor
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    ChunkStatus,
    EscalationFact,
    IWriteChunkRepository,
    LeaseFact,
    PmPointer,
    RouteCreatedFact,
    RouteReleasedFact,
    TransitionFact,
    derive_chunk_status,
)
from blizzard.hub.store import schema as s

_ROUTE_PREFIX = "route"
_TERMINAL = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})


class ChunkStore:
    """Read-write chunk-facts adapter over the hub store engine."""

    def __init__(self, engine: Engine, clock: IClock) -> None:
        self._engine = engine
        self._clock = clock

    # --- reads --------------------------------------------------------------

    def get(self, chunk_id: str) -> Chunk | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(s.chunks).where(s.chunks.c.chunk_id == chunk_id)).one_or_none()
            if row is None:
                return None
            return self._chunk(conn, row)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        with self._engine.connect() as conn:
            chunk = conn.execute(select(s.chunks).where(s.chunks.c.chunk_id == chunk_id)).one_or_none()
            if chunk is None:
                return None
            executors = {
                r.node_id: Executor(r.executor)
                for r in conn.execute(
                    select(s.graph_nodes.c.node_id, s.graph_nodes.c.executor).where(
                        s.graph_nodes.c.graph_id == chunk.graph_id
                    )
                ).all()
            }
            transitions = [
                TransitionFact(
                    to_node_id=t.to_node_id,
                    to_node_executor=executors.get(t.to_node_id, Executor.RUNNER),
                    epoch=t.epoch,
                    recorded_at=t.recorded_at,
                )
                for t in conn.execute(select(s.transitions).where(s.transitions.c.chunk_id == chunk_id)).all()
            ]
            leases = [
                LeaseFact(epoch=lease.epoch, minted_at=lease.minted_at)
                for lease in conn.execute(select(s.lease_facts).where(s.lease_facts.c.chunk_id == chunk_id)).all()
            ]
            escalations = [
                EscalationFact(epoch=e.epoch, recorded_at=e.recorded_at)
                for e in conn.execute(select(s.escalations).where(s.escalations.c.chunk_id == chunk_id)).all()
            ]
            routes_created = [
                RouteCreatedFact(created_at=r.created_at)
                for r in conn.execute(select(s.route_created).where(s.route_created.c.chunk_id == chunk_id)).all()
            ]
            routes_released = [
                RouteReleasedFact(released_at=r.released_at)
                for r in conn.execute(select(s.route_released).where(s.route_released.c.chunk_id == chunk_id)).all()
            ]
            return ChunkFacts(
                minted=True,
                stopped=self._exists(conn, s.chunk_stopped, chunk_id),
                delivery_landed=self._exists(conn, s.delivery_landed, chunk_id),
                escalations=escalations,
                leases=leases,
                transitions=transitions,
                routes_created=routes_created,
                routes_released=routes_released,
            )

    def load_artifacts(self, chunk_id: str) -> list[ArtifactRow]:
        with self._engine.connect() as conn:
            return [
                ArtifactRow(
                    kind=ArtifactKind(a.kind),
                    name=a.name,
                    data=a.data,
                    repo=a.repo,
                    artifact_id=a.artifact_id,
                    chunk_id=a.chunk_id,
                    node_id=a.node_id,
                    node_name=a.node_name,
                    epoch=a.epoch,
                )
                for a in conn.execute(select(s.artifacts).where(s.artifacts.c.chunk_id == chunk_id)).all()
            ]

    def route_of(self, chunk_id: str) -> Route | None:
        with self._engine.connect() as conn:
            created = conn.execute(
                select(s.route_created)
                .where(s.route_created.c.chunk_id == chunk_id)
                .order_by(s.route_created.c.created_at.desc())
            ).first()
            if created is None:
                return None
            released = conn.execute(
                select(s.route_released.c.released_at)
                .where(s.route_released.c.chunk_id == chunk_id)
                .order_by(s.route_released.c.released_at.desc())
            ).first()
            if released is not None and released.released_at >= created.created_at:
                return None
            env_ids = [
                e.environment_id
                for e in conn.execute(
                    select(s.route_environments.c.environment_id).where(
                        s.route_environments.c.route_id == created.route_id
                    )
                ).all()
            ]
            return Route(
                chunk_id=chunk_id,
                runner_id=created.runner_id,
                workspace_id=created.workspace_id,
                environment_ids=env_ids,
                created_at=created.created_at,
            )

    def list_all(self) -> list[Chunk]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(s.chunks).order_by(s.chunks.c.minted_at.desc())).all()
            return [self._chunk(conn, row) for row in rows]

    def list_ready(self) -> list[Chunk]:
        return [c for c in self.list_all() if self._status(c.chunk_id) is ChunkStatus.READY]

    def find_live_holder(self, pointer: PmPointer) -> str | None:
        with self._engine.connect() as conn:
            chunk_ids = [
                p.chunk_id
                for p in conn.execute(
                    select(s.chunk_pm_pointers.c.chunk_id).where(
                        (s.chunk_pm_pointers.c.provider == pointer.provider)
                        & (s.chunk_pm_pointers.c.url == pointer.url)
                    )
                ).all()
            ]
        for chunk_id in chunk_ids:
            if self._status(chunk_id) not in _TERMINAL:
                return chunk_id
        return None

    def accepted_transition_target(self, chunk_id: str, *, from_node_id: str, epoch: int) -> str | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(s.transitions.c.to_node_id).where(
                    (s.transitions.c.chunk_id == chunk_id)
                    & (s.transitions.c.from_node_id == from_node_id)
                    & (s.transitions.c.epoch == epoch)
                )
            ).first()
            return row.to_node_id if row is not None else None

    def landed_repos(self, chunk_id: str) -> set[str]:
        with self._engine.connect() as conn:
            return {
                r.repo
                for r in conn.execute(
                    select(s.delivery_repo_landed.c.repo).where(s.delivery_repo_landed.c.chunk_id == chunk_id)
                ).all()
            }

    # --- writes -------------------------------------------------------------

    def mint(self, chunk: Chunk) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.chunks).values(chunk_id=chunk.chunk_id, graph_id=chunk.graph_id, minted_at=chunk.minted_at)
            )
            for pointer in chunk.pm_pointers:
                conn.execute(
                    insert(s.chunk_pm_pointers).values(
                        chunk_id=chunk.chunk_id, provider=pointer.provider, url=pointer.url
                    )
                )

    def record_lease(self, chunk_id: str, *, epoch: int, runner_id: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.lease_facts).values(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at)
            )

    def record_route(self, route: Route, *, at: datetime) -> None:
        route_id = mint(_ROUTE_PREFIX, self._clock)
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.route_created).values(
                    route_id=route_id,
                    chunk_id=route.chunk_id,
                    runner_id=route.runner_id,
                    workspace_id=route.workspace_id,
                    created_at=at,
                )
            )
            for env_id in route.environment_ids:
                conn.execute(insert(s.route_environments).values(route_id=route_id, environment_id=env_id))

    def record_route_released(self, chunk_id: str, *, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(s.route_released).values(chunk_id=chunk_id, released_at=at))

    def record_transition(
        self,
        *,
        transition_id: str,
        chunk_id: str,
        from_node_id: str | None,
        to_node_id: str,
        choice_name: str | None,
        epoch: int,
        runner_id: str,
        at: datetime,
        artifacts: list[ArtifactRow],
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.transitions).values(
                    transition_id=transition_id,
                    chunk_id=chunk_id,
                    from_node_id=from_node_id,
                    to_node_id=to_node_id,
                    choice_name=choice_name,
                    decision_id=None,
                    epoch=epoch,
                    runner_id=runner_id,
                    recorded_at=at,
                )
            )
            for row in artifacts:
                conn.execute(
                    insert(s.artifacts).values(
                        artifact_id=row.artifact_id,
                        chunk_id=row.chunk_id,
                        node_id=row.node_id,
                        node_name=row.node_name,
                        epoch=row.epoch,
                        name=row.name,
                        kind=row.kind.value,
                        data=row.data,
                        repo=row.repo,
                        produced_at=at,
                    )
                )

    def record_delivery_repo_landed(self, chunk_id: str, *, repo: str, commit_hash: str, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(s.delivery_repo_landed).values(
                    chunk_id=chunk_id, repo=repo, commit_hash=commit_hash, landed_at=at
                )
            )

    def record_delivery_landed(self, chunk_id: str, *, at: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(s.delivery_landed).values(chunk_id=chunk_id, landed_at=at))

    # --- helpers ------------------------------------------------------------

    def _chunk(self, conn, row) -> Chunk:  # type: ignore[no-untyped-def]
        pointers = [
            PmPointer(provider=p.provider, url=p.url)
            for p in conn.execute(
                select(s.chunk_pm_pointers).where(s.chunk_pm_pointers.c.chunk_id == row.chunk_id)
            ).all()
        ]
        return Chunk(chunk_id=row.chunk_id, graph_id=row.graph_id, pm_pointers=pointers, minted_at=row.minted_at)

    def _status(self, chunk_id: str) -> ChunkStatus:
        facts = self.load_facts(chunk_id)
        return derive_chunk_status(facts) if facts is not None else ChunkStatus.READY

    @staticmethod
    def _exists(conn, table, chunk_id: str) -> bool:  # type: ignore[no-untyped-def]
        return conn.execute(select(table.c.chunk_id).where(table.c.chunk_id == chunk_id).limit(1)).first() is not None


def _conforms_chunk_store(x: ChunkStore) -> IWriteChunkRepository:
    return x
