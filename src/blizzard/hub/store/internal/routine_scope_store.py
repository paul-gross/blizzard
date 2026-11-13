"""SQLAlchemy adapter for the routine_scopes join reads (package-private).
All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). A link or unlink
is a routine edit, written by the routine adapter (``bzh:configured-record``). A link is
stored by ``scope_id`` and read back as the scope's slug, joined within the tenant."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.sql.selectable import Join

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.garden.routines import IReadRoutineScopeRepository
from blizzard.hub.store.schema import routine_scopes, scopes
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements


def _links(stmts: ScopedStatements) -> Join:
    """``routine_scopes`` joined to the scope each link names, tenant equality in ``ON``."""
    return stmts.scope_join(routine_scopes, scopes, routine_scopes.c.scope_id == scopes.c.scope_id)


class RoutineScopeStore:
    """Read-only ``routine_scopes`` adapter over the hub store engine."""

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

    def links_for(self, routine_ids: Sequence[str]) -> dict[str, dict[str, datetime | None]]:
        links: dict[str, dict[str, datetime | None]] = {routine_id: {} for routine_id in routine_ids}
        if not routine_ids:
            return links
        with self._store.read("links_for") as conn:
            for batch in id_batches(routine_ids):
                rows = conn.execute(
                    self._store.select(
                        _links(self._store), routine_scopes.c.routine_id, scopes.c.slug, routine_scopes.c.linked_at
                    )
                    .where(routine_scopes.c.routine_id.in_(batch))
                    .order_by(routine_scopes.c.routine_id, scopes.c.slug)
                ).all()
                for row in rows:
                    links[row.routine_id][row.slug] = row.linked_at
        return links

    def list_routines(self, scope_id: str) -> list[str]:
        with self._store.read("list_routines") as conn:
            rows = conn.execute(
                self._store.select(routine_scopes, routine_scopes.c.routine_id)
                .where(routine_scopes.c.scope_id == scope_id)
                .order_by(routine_scopes.c.routine_id)
            ).all()
        return [row.routine_id for row in rows]


_routine_scope_store_conforms: type[IReadRoutineScopeRepository] = RoutineScopeStore
