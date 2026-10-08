"""SQLAlchemy adapter for the run-context repository seam (package-private).
All ``sqlalchemy`` usage is confined here
(``bzh:dependency-inversion``). Resolves a chunk's first work ref straight against
``work_items`` rather than composing another store instance (``bzh:repository-split``)
— this store depends only on :class:`ScopedConnections`. A run names its scope by
``scope_id``, written from and read back as the slug within the tenant."""

from __future__ import annotations

from sqlalchemy.engine import Connection

from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.garden.run_context import IWriteRunContextRepository, RunContext
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.scope_store import scope_id_of
from blizzard.hub.store.scoped import ScopedConnections, statements_of


def insert_run_context_row(conn: Connection, work_item_id: str, context: RunContext) -> None:
    """Insert one ``work_item_runs`` row on a caller-supplied ``conn`` — mirrors
    :func:`~blizzard.hub.store.internal.chunk_rows.insert_chunk_rows`'s shared-connection
    shape, so a routine run's own composite write folds this into its own transaction."""
    stmts = statements_of(conn)
    conn.execute(
        stmts.insert(s.work_item_runs).values(
            work_item_id=work_item_id,
            routine_name=context.routine_name,
            scope_id=scope_id_of(stmts, context.scope_slug),
            mode=context.mode,
        )
    )


class RunContextStore:
    """Read-write run-context adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def for_chunk(self, chunk: Chunk) -> RunContext | None:
        pointer = chunk.originating_ref()
        if pointer is None:
            return None
        with self._store.read("for_chunk") as conn:
            item_row = conn.execute(
                self._store.select(s.work_items, s.work_items.c.work_item_id).where(
                    s.work_items.c.source == pointer.source, s.work_items.c.ref == pointer.ref
                )
            ).one_or_none()
            if item_row is None:
                return None
            runs = self._store.scope_join(
                s.work_item_runs, s.scopes, s.work_item_runs.c.scope_id == s.scopes.c.scope_id
            )
            row = conn.execute(
                self._store.select(
                    runs, s.work_item_runs.c.routine_name, s.scopes.c.slug.label("scope_slug"), s.work_item_runs.c.mode
                ).where(s.work_item_runs.c.work_item_id == item_row.work_item_id)
            ).one_or_none()
        if row is None:
            return None
        return RunContext(routine_name=row.routine_name, scope_slug=row.scope_slug, mode=row.mode)

    def record(self, work_item_id: str, context: RunContext) -> None:
        with self._store.write("record") as conn:
            insert_run_context_row(conn, work_item_id, context)


def _conforms_run_context_store(x: RunContextStore) -> IWriteRunContextRepository:
    return x
