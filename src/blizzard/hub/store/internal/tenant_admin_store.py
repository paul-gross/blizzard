"""SQLAlchemy adapter for tenant administration (package-private).

``tenants`` and ``tenant_deletions`` are on the global list, so the directory reads and renames through the hub-level
connections. A creation runs inside an identity unit of work, the one transaction that may write both the global
identity tables and a tenant's own: the tenant's connections on that transaction insert its ``tenants`` row first
(:meth:`~blizzard.hub.store.scoped.ScopedConnections.founding`), and every later tenant-owned write there — the
packaged graphs, the ``hub`` sequence, the default project — locks the row it inserted. The first admin's membership
is written through the unit's own membership repository, so the tenant and its admin commit or roll back together."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import exists, select, update

from blizzard.auth_core import Role
from blizzard.foundation.tenants import TenantState
from blizzard.hub.auth.identity_transaction import IdentityWrites, IIdentityTransactions
from blizzard.hub.auth.memberships import MembershipFact
from blizzard.hub.domain.graph.model import IWriteGraphRepository
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.projects.model import IWriteProjectRepository
from blizzard.hub.domain.tenancy.administration import (
    ITenantCreation,
    IWriteTenantDirectory,
    Tenant,
    TenantAdminUnknown,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantId

_t = s.tenants.c
#: Whether a deletion fact names the tenant a ``tenants`` row describes.
_DELETING = exists().where(s.tenant_deletions.c.tenant_id == _t.tenant_id)
#: When the tenant a ``tenants`` row describes closed — null while it is open.
_CLOSED_AT = (
    select(s.tenant_deletions.c.closed_at).where(s.tenant_deletions.c.tenant_id == _t.tenant_id).scalar_subquery()
)


def _tenant(row: Any) -> Tenant:
    return Tenant(
        tenant_id=TenantId(row.tenant_id),
        name=row.name,
        created_at=row.created_at,
        created_by=row.created_by,
        state=TenantState.OPEN if row.closed_at is None else TenantState.DELETING,
        closed_at=row.closed_at,
    )


class _TenantCreation:
    """One tenant's creation on its identity unit of work: the tenant's connections there, and the unit's own
    identity repositories."""

    def __init__(self, tenant: TenantId, connections: ScopedConnections, writes: IdentityWrites) -> None:
        self._tenant = tenant
        self._connections = connections
        self._writes = writes
        self._graphs = GraphStore(connections)
        self._projects = ProjectStore(connections)

    @property
    def graphs(self) -> IWriteGraphRepository:
        return self._graphs

    @property
    def projects(self) -> IWriteProjectRepository:
        return self._projects

    def seed_hub_sequence(self) -> None:
        with self._connections.write("seed_hub_sequence") as conn:
            conn.execute(
                self._connections.insert(s.work_item_sequence).values(source=RESERVED_HUB_SOURCE_NAME, next_ref=1)
            )

    def grant_admin(self, user_id: str, *, by: str, at: datetime) -> None:
        if self._writes.users.get(user_id) is None:
            raise TenantAdminUnknown(user_id)
        self._writes.memberships.record(
            MembershipFact(tenant_id=self._tenant, user_id=user_id, role=Role.ADMIN, set_at=at, set_by=by)
        )


class TenantAdministrationStore:
    """Reads, renames and creates the hub's tenants."""

    def __init__(self, store: HubStoreConnections, identity: IIdentityTransactions) -> None:
        self._store = store
        self._identity = identity

    def list_tenants(self) -> list[Tenant]:
        """Every ``tenants`` row with its deletion's ``closed_at``, by id — a ``ten_`` id is a ULID, so that is
        creation order to the millisecond."""
        with self._store.read("list_tenants") as conn:
            rows = conn.execute(
                select(_t.tenant_id, _t.name, _t.created_at, _t.created_by, _CLOSED_AT.label("closed_at")).order_by(
                    _t.tenant_id
                )
            ).all()
        return [_tenant(row) for row in rows]

    def rename(self, tenant: TenantId, name: str) -> Tenant | None:
        with self._store.write("rename_tenant") as conn:
            row = conn.execute(
                update(s.tenants)
                .where(_t.tenant_id == tenant, ~_DELETING)
                .values(name=name)
                .returning(_t.tenant_id, _t.name, _t.created_at, _t.created_by)
            ).one_or_none()
        if row is None:
            return None
        return Tenant(
            tenant_id=TenantId(row.tenant_id),
            name=row.name,
            created_at=row.created_at,
            created_by=row.created_by,
            state=TenantState.OPEN,
        )

    @contextmanager
    def create(self, tenant: TenantId, *, name: str, at: datetime, by: str) -> Generator[ITenantCreation, None, None]:
        with self._identity.begin() as writes:
            connections = writes.tenant_connections(StoreScope(tenant))
            with connections.founding("create_tenant", name=name, created_at=at, created_by=by):
                pass
            yield _TenantCreation(tenant, connections, writes)


_tenant_administration_store_conforms: type[IWriteTenantDirectory] = TenantAdministrationStore
