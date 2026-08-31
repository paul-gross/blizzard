"""SQLAlchemy adapter for the run-context repository seam (package-private,
blizzard#393 Phase 1). All ``sqlalchemy`` usage is confined here
(``bzh:dependency-inversion``). Resolves a chunk's first work ref straight against
``work_items`` rather than composing another store instance (``bzh:repository-split``)
— this store depends only on :class:`HubStoreConnections`."""

from __future__ import annotations

from sqlalchemy import insert, select

from blizzard.hub.domain.run_context import IWriteRunContextRepository, RunContext
from blizzard.hub.domain.work import Chunk
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections


class RunContextStore:
    """Read-write run-context adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def for_chunk(self, chunk: Chunk) -> RunContext | None:
        if not chunk.work_refs:
            return None
        pointer = chunk.work_refs[0]
        with self._store.read("for_chunk") as conn:
            item_row = conn.execute(
                select(s.work_items.c.work_item_id).where(
                    s.work_items.c.source == pointer.source, s.work_items.c.ref == pointer.ref
                )
            ).one_or_none()
            if item_row is None:
                return None
            row = conn.execute(
                select(s.work_item_runs).where(s.work_item_runs.c.work_item_id == item_row.work_item_id)
            ).one_or_none()
        if row is None:
            return None
        return RunContext(routine_name=row.routine_name, scope_slug=row.scope_slug, mode=row.mode)

    def record(self, work_item_id: str, context: RunContext) -> None:
        with self._store.write("record") as conn:
            conn.execute(
                insert(s.work_item_runs).values(
                    work_item_id=work_item_id,
                    routine_name=context.routine_name,
                    scope_slug=context.scope_slug,
                    mode=context.mode,
                )
            )


def _conforms_run_context_store(x: RunContextStore) -> IWriteRunContextRepository:
    return x
