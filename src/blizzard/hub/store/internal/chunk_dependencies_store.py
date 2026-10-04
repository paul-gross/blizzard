"""SQLAlchemy adapter for the chunk-dependencies seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). One row per
edge, shape owned by ``hub/store/schema.py``: declaring after a release mints a fresh
row rather than reviving the old one. Timestamps arrive already stamped
(``bzh:injected-clock``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Connection, select, update

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import DEPENDENCY_EDGE_PREFIX, Id
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.chunk.model import DependencyEdge
from blizzard.hub.domain.chunk.ports.dependencies import FoldTarget, IWriteChunkDependenciesRepository
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import conn_of, record_grouped_row_conn


class ChunkDependenciesStore:
    """The declared dependency edges between chunks."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def list_standing_edges(self) -> list[DependencyEdge]:
        with self._store.read("list_standing_edges") as conn:
            return self.list_standing_edges_conn(conn)

    def list_standing_edges_conn(self, conn: Connection) -> list[DependencyEdge]:
        """`list_standing_edges`'s already-open-connection sibling — the
        locked-transaction seam's own read (``bzh:store-exclusive-write``), resolved on
        the caller's connection rather than a fresh one."""
        rows = conn.execute(
            select(s.chunk_dependencies)
            .where(s.chunk_dependencies.c.released_at.is_(None))
            # (declared_at, dependency_id) — an explicit total order (`bzh:sql-portable`).
            .order_by(s.chunk_dependencies.c.declared_at, s.chunk_dependencies.c.dependency_id)
        ).all()
        return [_edge(row) for row in rows]

    def standing_edges_for_dependents(self, dependent_chunk_ids: Sequence[str]) -> list[DependencyEdge]:
        edges: list[DependencyEdge] = []
        with self._store.read("standing_edges_for_dependents") as conn:
            for batch in id_batches(dependent_chunk_ids):
                rows = conn.execute(
                    select(s.chunk_dependencies).where(
                        s.chunk_dependencies.c.released_at.is_(None)
                        & s.chunk_dependencies.c.dependent_chunk_id.in_(batch)
                    )
                ).all()
                edges.extend(_edge(row) for row in rows)
        # (declared_at, dependency_id) — `list_standing_edges`'s explicit total order, restored across batches.
        return sorted(edges, key=lambda e: (e.declared_at, e.dependency_id))

    def standing_edge(self, dependent_chunk_id: str, prerequisite_chunk_id: str) -> DependencyEdge | None:
        with self._store.read("standing_edge") as conn:
            row = _standing_row(conn, dependent_chunk_id, prerequisite_chunk_id)
        return _edge(row) if row is not None else None

    def standing_edges_for(self, chunk_id: str) -> list[DependencyEdge]:
        with self._store.read("standing_edges_for") as conn:
            rows = conn.execute(
                select(s.chunk_dependencies)
                .where(
                    s.chunk_dependencies.c.released_at.is_(None)
                    & (
                        (s.chunk_dependencies.c.dependent_chunk_id == chunk_id)
                        | (s.chunk_dependencies.c.prerequisite_chunk_id == chunk_id)
                    )
                )
                # (declared_at, dependency_id) — an explicit total order (`bzh:sql-portable`).
                .order_by(s.chunk_dependencies.c.declared_at, s.chunk_dependencies.c.dependency_id)
            ).all()
        return [_edge(row) for row in rows]

    def declare_locked(
        self, handle: ILockedChunkRead, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime
    ) -> DependencyEdge:
        """Mint a fresh standing edge (``bzh:store-exclusive-write``) — the
        dependency declare's own write, on ``handle``'s already-locked connection."""
        return self._declare_conn(conn_of(handle), dependent_chunk_id, prerequisite_chunk_id, by=by, at=at)

    def _declare_conn(  # type: ignore[no-untyped-def]
        self, conn, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime
    ) -> DependencyEdge:
        dependency_id = Id.mint_at(DEPENDENCY_EDGE_PREFIX, at).value
        conn.execute(
            s.chunk_dependencies.insert().values(
                dependency_id=dependency_id,
                dependent_chunk_id=dependent_chunk_id,
                prerequisite_chunk_id=prerequisite_chunk_id,
                declared_at=at,
                declared_by=by,
                released_at=None,
                released_by=None,
            )
        )
        return DependencyEdge(
            dependency_id=dependency_id,
            dependent_chunk_id=dependent_chunk_id,
            prerequisite_chunk_id=prerequisite_chunk_id,
            declared_at=at,
            declared_by=by,
        )

    def release(
        self, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime
    ) -> DependencyEdge | None:
        """Read-then-write on the same connection, so there is no window between the
        two; see
        :meth:`~blizzard.hub.domain.chunk.ports.dependencies.IWriteChunkDependenciesRepository.release`."""
        with self._store.write("release") as conn:
            row = _standing_row(conn, dependent_chunk_id, prerequisite_chunk_id)
            if row is None:
                return None
            conn.execute(
                update(s.chunk_dependencies)
                .where(s.chunk_dependencies.c.dependency_id == row.dependency_id)
                .values(released_at=at, released_by=by)
            )
        return DependencyEdge(
            dependency_id=row.dependency_id,
            dependent_chunk_id=row.dependent_chunk_id,
            prerequisite_chunk_id=row.prerequisite_chunk_id,
            declared_at=row.declared_at,
            declared_by=row.declared_by,
            released_at=at,
            released_by=by,
        )

    def record_fold_locked(
        self, handle: ILockedChunkRead, targets: list[FoldTarget], *, grouped_into: str, by: str, at: datetime
    ) -> dict[str, int]:
        """Record the group fold's edges and ``chunk.grouped`` rows (``bzh:store-exclusive-write``) —
        the group fold's own write, on ``handle``'s already-locked connection."""
        return self._record_fold_conn(conn_of(handle), targets, grouped_into=grouped_into, by=by, at=at)

    def _record_fold_conn(  # type: ignore[no-untyped-def]
        self, conn, targets: list[FoldTarget], *, grouped_into: str, by: str, at: datetime
    ) -> dict[str, int]:
        grouped_ids: dict[str, int] = {}
        for target in targets:
            grouped_ids[target.chunk_id] = record_grouped_row_conn(
                conn, target.chunk_id, grouped_into=grouped_into, at=at
            )
            if target.release:
                conn.execute(
                    update(s.chunk_dependencies)
                    .where(s.chunk_dependencies.c.dependency_id.in_(target.release))
                    .values(released_at=at, released_by=by)
                )
            for dependent_chunk_id, prerequisite_chunk_id, declared_at in target.mint:
                dependency_id = Id.mint_at(DEPENDENCY_EDGE_PREFIX, at).value
                conn.execute(
                    s.chunk_dependencies.insert().values(
                        dependency_id=dependency_id,
                        dependent_chunk_id=dependent_chunk_id,
                        prerequisite_chunk_id=prerequisite_chunk_id,
                        declared_at=declared_at,
                        declared_by=by,
                        released_at=None,
                        released_by=None,
                    )
                )
        return grouped_ids


def _standing_row(conn: Connection, dependent_chunk_id: str, prerequisite_chunk_id: str):  # type: ignore[no-untyped-def]
    return conn.execute(
        select(s.chunk_dependencies)
        .where(
            (s.chunk_dependencies.c.dependent_chunk_id == dependent_chunk_id)
            & (s.chunk_dependencies.c.prerequisite_chunk_id == prerequisite_chunk_id)
            & (s.chunk_dependencies.c.released_at.is_(None))
        )
        # (declared_at, dependency_id) — an explicit total order (`bzh:sql-portable`).
        .order_by(s.chunk_dependencies.c.declared_at, s.chunk_dependencies.c.dependency_id)
    ).first()


def _edge(row) -> DependencyEdge:  # type: ignore[no-untyped-def]
    return DependencyEdge(
        dependency_id=row.dependency_id,
        dependent_chunk_id=row.dependent_chunk_id,
        prerequisite_chunk_id=row.prerequisite_chunk_id,
        declared_at=row.declared_at,
        declared_by=row.declared_by,
        released_at=row.released_at,
        released_by=row.released_by,
    )


def release_outgoing_edges_conn(conn: Connection, chunk_id: str, *, by: str, at: datetime) -> None:
    """Release every standing edge naming ``chunk_id`` as the dependent, on a
    caller-supplied ``conn`` — folded into the delete transaction so a
    deleted dependent's own edges never survive it, mirroring
    :func:`~blizzard.hub.store.internal.chunk_rows.record_deleted_row`'s shared-connection
    shape."""
    conn.execute(
        update(s.chunk_dependencies)
        .where((s.chunk_dependencies.c.dependent_chunk_id == chunk_id) & (s.chunk_dependencies.c.released_at.is_(None)))
        .values(released_at=at, released_by=by)
    )


def _conforms_dependencies(x: ChunkDependenciesStore) -> IWriteChunkDependenciesRepository:
    return x
