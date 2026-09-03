"""SQLAlchemy adapter for the chunk-dependencies seam (package-private, issue #456).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). One row per
edge, shape owned by ``hub/store/schema.py``: declaring after a release mints a fresh
row rather than reviving the old one. Timestamps arrive already stamped
(``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection, select, update

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import DEPENDENCY_EDGE_PREFIX, Id
from blizzard.hub.domain.chunks.dependencies import IWriteChunkDependenciesRepository
from blizzard.hub.domain.work import DependencyEdge
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import record_grouped_row_conn


class ChunkDependenciesStore:
    """The declared dependency edges between chunks."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def list_standing_edges(self) -> list[DependencyEdge]:
        with self._store.read("list_standing_edges") as conn:
            rows = conn.execute(
                select(s.chunk_dependencies)
                .where(s.chunk_dependencies.c.released_at.is_(None))
                # (declared_at, dependency_id) — an explicit total order (`bzh:sql-portable`).
                .order_by(s.chunk_dependencies.c.declared_at, s.chunk_dependencies.c.dependency_id)
            ).all()
        return [_edge(row) for row in rows]

    def standing_edge(self, dependent_chunk_id: str, prerequisite_chunk_id: str) -> DependencyEdge | None:
        with self._store.read("standing_edge") as conn:
            row = _standing_row(conn, dependent_chunk_id, prerequisite_chunk_id)
        return _edge(row) if row is not None else None

    def declare(self, dependent_chunk_id: str, prerequisite_chunk_id: str, *, by: str, at: datetime) -> DependencyEdge:
        """Mint a fresh standing edge — always a new row, never a revive of a released
        one; see
        :meth:`~blizzard.hub.domain.chunks.dependencies.IWriteChunkDependenciesRepository.declare`."""
        dependency_id = Id.mint_at(DEPENDENCY_EDGE_PREFIX, at).value
        with self._store.write("declare") as conn:
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
        :meth:`~blizzard.hub.domain.chunks.dependencies.IWriteChunkDependenciesRepository.release`."""
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

    def record_fold(
        self,
        chunk_id: str,
        *,
        grouped_into: str,
        release: list[str],
        mint: list[tuple[str, str]],
        by: str,
        at: datetime,
    ) -> int:
        """Record ``chunk_id``'s ``chunk.grouped`` row and rewrite its own dependency edges
        — releasing ``release``'s dependency ids and minting ``mint``'s fresh
        ``(dependent, prerequisite)`` pairs — atomically in one transaction (D1, D4, issue
        #460). ``mint`` never revives a released row, always a fresh insert. Returns the
        freshly-inserted ``chunk_grouped.id``."""
        with self._store.write("record_fold") as conn:
            grouped_id = record_grouped_row_conn(conn, chunk_id, grouped_into=grouped_into, at=at)
            if release:
                conn.execute(
                    update(s.chunk_dependencies)
                    .where(s.chunk_dependencies.c.dependency_id.in_(release))
                    .values(released_at=at, released_by=by)
                )
            for dependent_chunk_id, prerequisite_chunk_id in mint:
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
        return grouped_id


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
    caller-supplied ``conn`` (issue #460) — folded into the delete transaction so a
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
