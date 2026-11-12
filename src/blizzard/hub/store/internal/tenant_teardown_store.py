"""SQLAlchemy adapter for tenant deletion (package-private).

The close runs on the tenant's own unit, opened by the domain: the unit holds the tenant's row and ``TENANT_LIFECYCLE``,
so a write in flight either committed before the close or is refused after it, and two closes of different tenants
never both read the open tenants at once. The store judges nothing there — it reads the open tenants and records the
deletion fact; the domain refuses the last tenant and answers a closed one. The drain and the completion are the
tenant's teardown writes (``src/blizzard/hub/store/scoped.py``'s ``ScopedConnections.teardown``).

The drain walks :data:`DRAIN_ORDER` — the reverse of ``schema.metadata.sorted_tables`` restricted to tenant-owned
tables, children before parents — from the top on every batch, deleting each table's rows by primary key. Nothing
relies on a cascade: SQLite runs with ``foreign_keys`` off."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Table, delete, exists, func, insert, literal, select, tuple_, update

from blizzard.hub.domain.kernel.locks import HubLock
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.domain.tenancy.teardown import IWriteTenantTeardowns, TenantDeletion
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.scoped import ScopedConnections, TeardownNotUnderway, is_tenant_owned
from blizzard.hub.store.transactions import connection_for
from blizzard.hub.tenancy import StoreScope, TenantId

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

    def open_tenants(self, transaction: LockedTransaction, tenant: TenantId) -> list[TenantId]:
        conn = self._unit_connection(transaction, tenant)
        closed = exists().where(_d.tenant_id == _t.tenant_id)
        return [
            TenantId(each)
            for each in conn.execute(select(_t.tenant_id).where(~closed).order_by(_t.tenant_id)).scalars()
        ]

    def close(self, transaction: LockedTransaction, tenant: TenantId, *, by: str, at: datetime) -> None:
        conn = self._unit_connection(transaction, tenant)
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

    def _unit_connection(self, transaction: LockedTransaction, tenant: TenantId) -> Connection:
        return connection_for(self._store.scoped(StoreScope(tenant)), transaction, hub=HubLock.TENANT_LIFECYCLE)

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


_tenant_teardown_store_conforms: type[IWriteTenantTeardowns] = TenantTeardownStore
