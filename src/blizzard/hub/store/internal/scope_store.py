"""SQLAlchemy adapter for the scope repository seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). A scope is keyed by
its ``scp_`` id and named by its slug within its tenant: ``ensure`` is a first-write-wins CAS
over ``uq_scopes_tenant_slug``, and every child row links by ``scope_id``, resolving a slug
within the tenant (:func:`scope_id_of`, :func:`scope_ids_of`). Retired derives from the
append-only ``scope_lifecycle_facts`` table, newest-fact-wins per scope. Every write commits
its change row in the same transaction (``bzh:configured-record``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.selectable import ScalarSelect

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.config.work_sources import ConfigRevisionConflict
from blizzard.hub.domain.garden.scopes import IWriteScopeRepository, Scope, ScopeMint
from blizzard.hub.store.internal.config_change_store import append_change
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import scope_lifecycle_facts, scopes
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements, statements_of


def scope_id_of(stmts: ScopedStatements, slug: str) -> ScalarSelect[Any]:
    """The id of the scope named ``slug`` within the tenant, as a scalar subquery a child
    row's write embeds — ``NULL`` when the tenant names no scope so, which the child's
    ``NOT NULL`` foreign key refuses."""
    return stmts.select(scopes, scopes.c.scope_id).where(scopes.c.slug == slug).scalar_subquery()


def scope_ids_of(conn: Connection, slugs: Sequence[str]) -> dict[str, str]:
    """``{slug: scope_id}`` for each of ``slugs`` the tenant stores — one batched read for a
    bulk write's rows; a slug the tenant does not store is absent."""
    stmts = statements_of(conn)
    found: dict[str, str] = {}
    for batch in id_batches(sorted(set(slugs))):
        rows = conn.execute(stmts.select(scopes, scopes.c.slug, scopes.c.scope_id).where(scopes.c.slug.in_(batch)))
        found.update({row.slug: row.scope_id for row in rows})
    return found


def ensure_scope(conn: Connection, mint: ScopeMint) -> bool:
    """The mint body, inside the caller's transaction: insert the scope and its ``create``
    change unless the slug is already stored. ``True`` when it inserted."""
    record, change = mint
    if (
        conn.execute(statements_of(conn).select(scopes, scopes.c.slug).where(scopes.c.slug == record.slug)).first()
        is not None
    ):
        return False
    conn.execute(
        statements_of(conn)
        .insert(scopes)
        .values(
            scope_id=record.scope_id,
            slug=record.slug,
            description=record.description,
            created_at=record.created_at,
            revision=record.revision,
        )
    )
    append_change(conn, change)
    return True


def _revision(conn: Connection, slug: str) -> int:
    return conn.execute(statements_of(conn).select(scopes, scopes.c.revision).where(scopes.c.slug == slug)).scalar_one()


def _move_revision(conn: Connection, record: Scope, from_revision: int, **values: object) -> None:
    moved = conn.execute(
        statements_of(conn)
        .update(scopes)
        .where(scopes.c.slug == record.slug, scopes.c.revision == from_revision)
        .values(revision=record.revision, **values)
    ).rowcount
    if moved != 1:
        raise ConfigRevisionConflict("scope", record.slug, current=_revision(conn, record.slug))


def _is_retired(conn: Connection, slug: str) -> bool:
    """Newest ``scope_lifecycle_facts`` row of the scope named ``slug`` wins; no row reads
    not-retired (a freshly minted scope starts enabled)."""
    stmts = statements_of(conn)
    facts = stmts.scope_join(scope_lifecycle_facts, scopes, scope_lifecycle_facts.c.scope_id == scopes.c.scope_id)
    row = conn.execute(
        stmts.select(facts, scope_lifecycle_facts.c.retired)
        .where(scopes.c.slug == slug)
        .order_by(scope_lifecycle_facts.c.id.desc())
        .limit(1)
    ).first()
    return bool(row.retired) if row is not None else False


def _retired_slugs(conn: Connection) -> set[str]:
    stmts = statements_of(conn)
    retired_ids = newest_retired_select(stmts, scope_lifecycle_facts, scope_lifecycle_facts.c.scope_id)
    return set(conn.execute(stmts.select(scopes, scopes.c.slug).where(scopes.c.scope_id.in_(retired_ids))).scalars())


def update_scope(conn: Connection, record: Scope, from_revision: int, change: ConfigChange) -> None:
    """The edit body, inside the caller's transaction: the compare-and-set and the change."""
    _move_revision(conn, record, from_revision, description=record.description)
    append_change(conn, change)


def record_scope_lifecycle(
    conn: Connection, record: Scope, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
) -> None:
    """The lifecycle body, inside the caller's transaction: the compare-and-set, the fact, the change."""
    _move_revision(conn, record, from_revision)
    conn.execute(
        statements_of(conn)
        .insert(scope_lifecycle_facts)
        .values(scope_id=scope_id_of(statements_of(conn), record.slug), retired=retired, set_at=at, set_by=by)
    )
    append_change(conn, change)


class ScopeStore:
    """Read-write scope adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def ensure(self, record: Scope, *, change: ConfigChange) -> Scope:
        """Insert ``record`` in its own transaction; a racing second mint of the slug gets
        ``IntegrityError`` on ``uq_scopes_tenant_slug`` and reads back the winner instead —
        never overwriting the description the winner minted, and writing no change row."""
        try:
            with self._store.write("ensure", expect=(IntegrityError,)) as conn:
                inserted = ensure_scope(conn, (record, change))
            if inserted:
                return record
        except IntegrityError:
            pass
        with self._store.read("ensure_conflict_lookup") as conn:
            row = conn.execute(self._store.select(scopes).where(scopes.c.slug == record.slug)).one()
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
            row = conn.execute(self._store.select(scopes).where(scopes.c.slug == slug)).one_or_none()
            if row is None:
                return None
            return self._of(row, retired=_is_retired(conn, slug))

    def list_all(self) -> list[Scope]:
        with self._store.read("list_all") as conn:
            rows = conn.execute(self._store.select(scopes).order_by(scopes.c.created_at.desc())).all()
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
            scope_id=row.scope_id,
            slug=row.slug,
            description=row.description,
            created_at=row.created_at,
            revision=row.revision,
            retired=retired,
        )


def _conforms_scope_store(x: ScopeStore) -> IWriteScopeRepository:
    return x
