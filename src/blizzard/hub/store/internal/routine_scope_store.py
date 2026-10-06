"""SQLAlchemy adapter for the routine_scopes join repository seam (package-private).
All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). A link or unlink
is a routine edit: it moves the routine's revision by compare-and-set and commits its
change row in the same transaction as the join write (``bzh:configured-record``)."""

from __future__ import annotations

from sqlalchemy import delete, select

from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.work_sources import ConfigRevisionConflict
from blizzard.hub.domain.garden.routines import IWriteRoutineScopeRepository, Routine
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.routine_store import link_scope, move_revision
from blizzard.hub.store.schema import routine_scopes


class RoutineScopeStore:
    """Read-write ``routine_scopes`` adapter over the hub store engine."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def list_scopes(self, routine_id: str) -> list[str]:
        with self._store.read("list_scopes") as conn:
            rows = conn.execute(
                select(routine_scopes.c.scope_slug)
                .where(routine_scopes.c.routine_id == routine_id)
                .order_by(routine_scopes.c.scope_slug)
            ).all()
        return [row.scope_slug for row in rows]

    def list_routines(self, scope_slug: str) -> list[str]:
        with self._store.read("list_routines") as conn:
            rows = conn.execute(
                select(routine_scopes.c.routine_id)
                .where(routine_scopes.c.scope_slug == scope_slug)
                .order_by(routine_scopes.c.routine_id)
            ).all()
        return [row.routine_id for row in rows]

    def link(self, routine: Routine, scope_slug: str, *, from_revision: int, change: ConfigChange) -> Routine:
        with self._store.write("link", expect=(ConfigRevisionConflict,)) as conn:
            move_revision(conn, routine, from_revision)
            link_scope(conn, routine.routine_id, scope_slug)
            append_change(conn, change)
        return routine

    def unlink(self, routine: Routine, scope_slug: str, *, from_revision: int, change: ConfigChange) -> Routine:
        with self._store.write("unlink", expect=(ConfigRevisionConflict,)) as conn:
            move_revision(conn, routine, from_revision)
            conn.execute(
                delete(routine_scopes).where(
                    routine_scopes.c.routine_id == routine.routine_id, routine_scopes.c.scope_slug == scope_slug
                )
            )
            append_change(conn, change)
        return routine


def _conforms_routine_scope_store(x: RoutineScopeStore) -> IWriteRoutineScopeRepository:
    return x
