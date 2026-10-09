"""SQLAlchemy adapter for tenant deletion (package-private).

The close is a hub-level write over the global ``tenants`` and ``tenant_deletions``: it locks the tenant's row first —
the same row every scoped write of the tenant locks before its first statement — and only then records the deletion
fact, so a write in flight either committed before the close or is refused after it. The drain and the completion are
the tenant's teardown writes (:meth:`~blizzard.hub.store.scoped.ScopedConnections.teardown`): each locks the deletion
fact, which also serializes two drains of one tenant, and removes rows through the tenant's own builders.

The drain walks :data:`DRAIN_ORDER` — the reverse of ``schema.metadata.sorted_tables`` restricted to tenant-owned
tables, children before parents — from the top on every batch, deleting each table's rows by primary key. Nothing
relies on a cascade: SQLite runs with ``foreign_keys`` off."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Table, delete, exists, func, insert, literal, select, tuple_, update

from blizzard.hub.domain.tenancy.teardown import CloseOutcome, IWriteTenantTeardowns, LastTenant, TenantDeletion
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.scoped import ScopedConnections, TeardownNotUnderway, is_tenant_owned
from blizzard.hub.tenancy import StoreScope, TenantId, UnknownTenant

#: Every tenant-owned table, children before parents — derived from the schema, so a table added later drains too.
DRAIN_ORDER: tuple[Table, ...] = tuple(t for t in reversed(s.metadata.sorted_tables) if is_tenant_owned(t))

_t = s.tenants.c
_d = s.tenant_deletions.c


def _deletion(row: Any) -> TenantDeletion:
    return TenantDeletion(
        tenant_id=TenantId(row.tenant_id),
        name=row.name,
        closed_at=row.closed_at,
        deleted_by=row.deleted_by,
        rows_removed=row.rows_removed or 0,
        deleted_at=row.deleted_at,
    )


def _delete_some(connections: ScopedConnections, conn: Any, table: Table, limit: int) -> int:
    """Delete at most ``limit`` of the tenant's rows of ``table``, picked by primary key."""
    key_columns = list(table.primary_key.columns)
    key = key_columns[0] if len(key_columns) == 1 else tuple_(*key_columns)
    some = connections.select(table, *key_columns).limit(limit)
    return conn.execute(connections.delete(table).where(key.in_(some))).rowcount


class TenantTeardownStore:
    """Closes, drains and completes tenants, and reads their deletion facts."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def underway(self) -> list[TenantId]:
        with self._store.read("tenant_deletions_underway") as conn:
            rows = conn.execute(select(_d.tenant_id).where(_d.deleted_at.is_(None)).order_by(_d.id)).scalars()
            return [TenantId(tenant) for tenant in rows]

    def deletion(self, tenant: TenantId) -> TenantDeletion | None:
        with self._store.read("tenant_deletion") as conn:
            row = conn.execute(select(s.tenant_deletions).where(_d.tenant_id == tenant)).one_or_none()
        return None if row is None else _deletion(row)

    def close(self, tenant: TenantId, *, by: str, at: datetime) -> CloseOutcome:
        with self._store.write("close_tenant") as conn:
            locked = conn.execute(update(s.tenants).where(_t.tenant_id == tenant).values(name=_t.name)).rowcount
            done = conn.execute(select(_d.deleted_at).where(_d.tenant_id == tenant)).one_or_none()
            if done is not None:
                return CloseOutcome.UNDERWAY if done.deleted_at is None else CloseOutcome.DELETED
            if locked != 1:
                raise UnknownTenant(tenant)
            closed = exists().where(_d.tenant_id == _t.tenant_id)
            if not conn.execute(select(exists().where(_t.tenant_id != tenant, ~closed))).scalar_one():
                raise LastTenant(tenant)
            conn.execute(
                insert(s.tenant_deletions).from_select(
                    ["tenant_id", "name", "closed_at", "deleted_by", "rows_removed"],
                    select(
                        _t.tenant_id,
                        _t.name,
                        literal(at, type_=_d.closed_at.type),
                        literal(by),
                        literal(0),
                    ).where(_t.tenant_id == tenant),
                )
            )
        return CloseOutcome.CLOSED

    def drain_batch(self, tenant: TenantId, limit: int) -> int | None:
        connections = self._store.scoped(StoreScope(tenant))
        try:
            with connections.teardown("drain_tenant") as conn:
                removed = 0
                for table in DRAIN_ORDER:
                    if removed >= limit:
                        break
                    removed += _delete_some(connections, conn, table, limit - removed)
                if removed:
                    conn.execute(
                        update(s.tenant_deletions)
                        .where(_d.tenant_id == tenant)
                        .values(rows_removed=func.coalesce(_d.rows_removed, 0) + removed)
                    )
                return removed
        except TeardownNotUnderway:
            return None

    def complete(self, tenant: TenantId, *, at: datetime) -> int | None:
        connections = self._store.scoped(StoreScope(tenant))
        try:
            with connections.teardown("complete_tenant_deletion") as conn:
                memberships = s.membership_facts
                removed = conn.execute(delete(memberships).where(memberships.c.tenant_id == tenant)).rowcount
                removed += conn.execute(delete(s.tenants).where(_t.tenant_id == tenant)).rowcount
                return conn.execute(
                    update(s.tenant_deletions)
                    .where(_d.tenant_id == tenant)
                    .values(deleted_at=at, rows_removed=func.coalesce(_d.rows_removed, 0) + removed)
                    .returning(_d.rows_removed)
                ).scalar_one()
        except TeardownNotUnderway:
            return None


def _conforms_tenant_teardown_store(x: TenantTeardownStore) -> IWriteTenantTeardowns:
    return x
