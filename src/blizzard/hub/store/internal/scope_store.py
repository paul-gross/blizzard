"""SQLAlchemy adapter for the scope repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). ``ensure`` is a
first-write-wins CAS over the slug primary key. Retired derives from the append-only
``scope_lifecycle_facts`` table, newest-fact-wins per slug. Every write commits its change
row in the same transaction (``bzh:configured-record``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.work_sources import ConfigRevisionConflict
from blizzard.hub.domain.garden.scopes import IWriteScopeRepository, Scope, ScopeMint
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.schema import scope_lifecycle_facts, scopes


def ensure_scope(conn: Connection, mint: ScopeMint) -> bool:
    """The mint body, inside the caller's transaction: insert the scope and its ``create``
    change unless the slug is already stored. ``True`` when it inserted."""
    record, change = mint
    if conn.execute(select(scopes.c.slug).where(scopes.c.slug == record.slug)).first() is not None:
        return False
    conn.execute(
        insert(scopes).values(
            slug=record.slug, description=record.description, created_at=record.created_at, revision=record.revision
        )
    )
    append_change(conn, change)
    return True


def _revision(conn: Connection, slug: str) -> int:
    return conn.execute(select(scopes.c.revision).where(scopes.c.slug == slug)).scalar_one()


def _move_revision(conn: Connection, record: Scope, from_revision: int, **values: object) -> None:
    moved = conn.execute(
        update(scopes)
        .where(scopes.c.slug == record.slug, scopes.c.revision == from_revision)
        .values(revision=record.revision, **values)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("scope", record.slug, current=_revision(conn, record.slug))


def _is_retired(conn: Connection, slug: str) -> bool:
    """Newest ``scope_lifecycle_facts`` row for ``slug`` wins; no row reads
    not-retired (a freshly minted scope starts enabled)."""
    row = conn.execute(
        select(scope_lifecycle_facts.c.retired)
        .where(scope_lifecycle_facts.c.slug == slug)
        .order_by(scope_lifecycle_facts.c.id.desc())
        .limit(1)
    ).first()
    return bool(row.retired) if row is not None else False


def _retired_slugs(conn: Connection) -> set[str]:
    rows = conn.execute(
        select(scope_lifecycle_facts.c.slug, scope_lifecycle_facts.c.retired).order_by(scope_lifecycle_facts.c.id)
    ).all()
    newest: dict[str, bool] = {}
    for row in rows:
        newest[row.slug] = row.retired  # newest-fact-wins: ascending id order overwrites
    return {slug for slug, retired in newest.items() if retired}


def update_scope(conn: Connection, record: Scope, from_revision: int, change: ConfigChange) -> None:
    """The edit body, inside the caller's transaction: the compare-and-set and the change."""
    _move_revision(conn, record, from_revision, description=record.description)
    append_change(conn, change)


def record_scope_lifecycle(
    conn: Connection, record: Scope, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
) -> None:
    """The lifecycle body, inside the caller's transaction: the compare-and-set, the fact, the change."""
    _move_revision(conn, record, from_revision)
    conn.execute(insert(scope_lifecycle_facts).values(slug=record.slug, retired=retired, set_at=at, set_by=by))
    append_change(conn, change)


class ScopeStore:
    """Read-write scope adapter over the hub store."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def ensure(self, record: Scope, *, change: ConfigChange) -> Scope:
        """Insert ``record`` in its own transaction; a racing second mint gets
        ``IntegrityError`` on the shared primary key and reads back the winner instead —
        never overwriting the description the winner minted, and writing no change row."""
        try:
            with self._store.write("ensure", expect=(IntegrityError,)) as conn:
                inserted = ensure_scope(conn, (record, change))
            if inserted:
                return record
        except IntegrityError:
            pass
        with self._store.read("ensure_conflict_lookup") as conn:
            row = conn.execute(select(scopes).where(scopes.c.slug == record.slug)).one()
            return self._of(row, retired=_is_retired(conn, record.slug))

    def update(self, record: Scope, *, from_revision: int, change: ConfigChange) -> Scope:
        with self._store.write("update", expect=(ConfigRevisionConflict,)) as conn:
            update_scope(conn, record, from_revision, change)
        return record

    def record_lifecycle(
        self, record: Scope, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
    ) -> Scope:
        """Append a ``scope.retired``/``scope.enabled`` fact — newest-fact-wins — with the
        revision move and the change row."""
        with self._store.write("record_lifecycle", expect=(ConfigRevisionConflict,)) as conn:
            record_scope_lifecycle(
                conn, record, retired=retired, from_revision=from_revision, at=at, by=by, change=change
            )
        return record

    def get(self, slug: str) -> Scope | None:
        with self._store.read("get") as conn:
            row = conn.execute(select(scopes).where(scopes.c.slug == slug)).one_or_none()
            if row is None:
                return None
            return self._of(row, retired=_is_retired(conn, slug))

    def list_all(self) -> list[Scope]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(select(scopes).order_by(scopes.c.created_at.desc())).all()
            retired = _retired_slugs(conn)
        return [self._of(row, retired=row.slug in retired) for row in rows]

    def is_retired(self, slug: str) -> bool:
        with self._store.read("is_retired") as conn:
            return _is_retired(conn, slug)

    def retired_slugs(self) -> set[str]:
        """Every slug whose newest lifecycle fact reads retired — mirrors
        ``GraphStore.retired_graph_ids``."""
        with self._store.read("retired_slugs") as conn:
            return _retired_slugs(conn)

    @staticmethod
    def _of(row, *, retired: bool) -> Scope:  # type: ignore[no-untyped-def]
        return Scope(
            slug=row.slug,
            description=row.description,
            created_at=row.created_at,
            revision=row.revision,
            retired=retired,
        )


def _conforms_scope_store(x: ScopeStore) -> IWriteScopeRepository:
    return x
