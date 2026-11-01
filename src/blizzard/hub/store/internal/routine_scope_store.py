"""SQLAlchemy adapter for the routine_scopes join repository seam (package-private).
All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). A link or unlink
is a routine edit: it moves the routine's revision by compare-and-set and commits its
change row in the same transaction as the join write (``bzh:configured-record``). A link is
stored by ``scope_id`` and read back as the scope's slug, joined within the tenant."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.sql.selectable import Join

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.errors import ConfigRevisionConflict
from blizzard.hub.domain.garden.routines import IWriteRoutineScopeRepository, Routine

# Legacy routine scope writes append config audit records atomically.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.config_change_store import append_change

# Legacy scope moves share the routine adapter's revision and link writes.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.routine_store import link_scope, move_revision

# Legacy routine scope writes resolve scope IDs on the same connection.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.scope_store import scope_id_of
from blizzard.hub.store.schema import routine_scopes, scopes
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements


def _links(stmts: ScopedStatements) -> Join:
    """``routine_scopes`` joined to the scope each link names, tenant equality in ``ON``."""
    return stmts.scope_join(routine_scopes, scopes, routine_scopes.c.scope_id == scopes.c.scope_id)


class RoutineScopeStore:
    """Read-write ``routine_scopes`` adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def list_scopes(self, routine_id: str) -> list[str]:
        with self._store.read("list_scopes") as conn:
            rows = conn.execute(
                self._store.select(_links(self._store), scopes.c.slug)
                .where(routine_scopes.c.routine_id == routine_id)
                .order_by(scopes.c.slug)
            ).all()
        return [row.slug for row in rows]

    def list_scopes_for(self, routine_ids: Sequence[str]) -> dict[str, list[str]]:
        linked: dict[str, list[str]] = {routine_id: [] for routine_id in routine_ids}
        if not routine_ids:
            return linked
        with self._store.read("list_scopes_for") as conn:
            for batch in id_batches(routine_ids):
                rows = conn.execute(
                    self._store.select(_links(self._store), routine_scopes.c.routine_id, scopes.c.slug)
                    .where(routine_scopes.c.routine_id.in_(batch))
                    .order_by(routine_scopes.c.routine_id, scopes.c.slug)
                ).all()
                for row in rows:
                    linked[row.routine_id].append(row.slug)
        return linked

    def list_routines(self, scope_id: str) -> list[str]:
        with self._store.read("list_routines") as conn:
            rows = conn.execute(
                self._store.select(routine_scopes, routine_scopes.c.routine_id)
                .where(routine_scopes.c.scope_id == scope_id)
                .order_by(routine_scopes.c.routine_id)
            ).all()
        return [row.routine_id for row in rows]

    def link(self, routine: Routine, scope_slug: str, *, from_revision: int, change: ConfigChange) -> Routine:
        with self._store.write("link", expect=(ConfigRevisionConflict,)) as conn:
            move_revision(conn, routine, from_revision)
            link_scope(conn, routine, scope_slug)
            append_change(conn, change)
        return routine

    def unlink(self, routine: Routine, scope_slug: str, *, from_revision: int, change: ConfigChange) -> Routine:
        with self._store.write("unlink", expect=(ConfigRevisionConflict,)) as conn:
            move_revision(conn, routine, from_revision)
            conn.execute(
                self._store.delete(routine_scopes).where(
                    routine_scopes.c.routine_id == routine.routine_id,
                    routine_scopes.c.scope_id == scope_id_of(self._store, scope_slug, routine.project_id),
                )
            )
            append_change(conn, change)
        return routine


_routine_scope_store_conforms: type[IWriteRoutineScopeRepository] = RoutineScopeStore
