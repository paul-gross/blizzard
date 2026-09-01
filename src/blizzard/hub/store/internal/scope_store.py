"""SQLAlchemy adapter for the scope repository seam (package-private, issue #389).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). ``ensure`` is a
first-write-wins CAS over the slug primary key, the ``ChunkQuestionsStore.answer_question`` shape
(D5) rather than ``WorkItemStore.allocate_ref``'s increment-on-conflict. Retired derives
from the append-only ``scope_lifecycle_facts`` table, newest-fact-wins per slug (D3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.scopes import IWriteScopeRepository, Scope
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import scope_lifecycle_facts, scopes


class ScopeStore:
    """Read-write scope adapter over the hub store."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def ensure(self, slug: str, *, description: str, at: datetime) -> Scope:
        """Insert ``slug`` in its own transaction; a racing second mint gets
        ``IntegrityError`` on the shared primary key and reads back the winner instead —
        never overwriting the description the winner minted (D4, D5)."""
        try:
            with self._store.write("ensure", expect=(IntegrityError,)) as conn:
                conn.execute(insert(scopes).values(slug=slug, description=description, created_at=at))
            return Scope(slug=slug, description=description, created_at=at)
        except IntegrityError:
            with self._store.read("ensure_conflict_lookup") as conn:
                row = conn.execute(select(scopes).where(scopes.c.slug == slug)).one()
            return self._of(row)

    def edit_description(self, slug: str, *, description: str) -> Scope:
        with self._store.write("edit_description") as conn:
            conn.execute(update(scopes).where(scopes.c.slug == slug).values(description=description))
            row = conn.execute(select(scopes).where(scopes.c.slug == slug)).one()
        return self._of(row)

    def get(self, slug: str) -> Scope | None:
        with self._store.read("get") as conn:
            row = conn.execute(select(scopes).where(scopes.c.slug == slug)).one_or_none()
        return self._of(row) if row is not None else None

    def list_all(self) -> list[Scope]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(select(scopes).order_by(scopes.c.created_at.desc())).all()
        return [self._of(row) for row in rows]

    def is_retired(self, slug: str) -> bool:
        with self._store.read("is_retired") as conn:
            return self._is_retired(conn, slug)

    def _is_retired(self, conn, slug: str) -> bool:  # type: ignore[no-untyped-def]
        """Newest ``scope_lifecycle_facts`` row for ``slug`` wins; no row reads
        not-retired (a freshly minted scope starts enabled)."""
        row = conn.execute(
            select(scope_lifecycle_facts.c.retired)
            .where(scope_lifecycle_facts.c.slug == slug)
            .order_by(scope_lifecycle_facts.c.id.desc())
            .limit(1)
        ).first()
        return bool(row.retired) if row is not None else False

    def record_lifecycle(self, slug: str, *, retired: bool, at: datetime, by: str) -> None:
        """Append a ``scope.retired``/``scope.enabled`` fact — newest-fact-wins (D3)."""
        with self._store.write("record_lifecycle") as conn:
            conn.execute(insert(scope_lifecycle_facts).values(slug=slug, retired=retired, set_at=at, set_by=by))

    @staticmethod
    def _of(row) -> Scope:  # type: ignore[no-untyped-def]
        return Scope(slug=row.slug, description=row.description, created_at=row.created_at)


def _conforms_scope_store(x: ScopeStore) -> IWriteScopeRepository:
    return x
