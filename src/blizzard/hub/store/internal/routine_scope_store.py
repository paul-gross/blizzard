"""SQLAlchemy adapter for the routine_scopes join repository seam (package-private,
blizzard#488). All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``).
``link`` is idempotent over the composite primary key: a duplicate insert hits
``IntegrityError`` on the shared ``(routine_id, scope_slug)`` key and is swallowed as
already-linked, the ``ScopeStore.ensure`` collision shape without the read-back — there
is nothing to read back when the row is already exactly what was asked for."""

from __future__ import annotations

from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.routines import IWriteRoutineScopeRepository
from blizzard.hub.store.errors import HubStoreConnections
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

    def link(self, routine_id: str, scope_slug: str) -> None:
        try:
            with self._store.write("link", expect=(IntegrityError,)) as conn:
                conn.execute(insert(routine_scopes).values(routine_id=routine_id, scope_slug=scope_slug))
        except IntegrityError:
            pass  # already linked — idempotent (D5's shape)

    def unlink(self, routine_id: str, scope_slug: str) -> None:
        with self._store.write("unlink") as conn:
            conn.execute(
                delete(routine_scopes).where(
                    routine_scopes.c.routine_id == routine_id, routine_scopes.c.scope_slug == scope_slug
                )
            )


def _conforms_routine_scope_store(x: RoutineScopeStore) -> IWriteRoutineScopeRepository:
    return x
