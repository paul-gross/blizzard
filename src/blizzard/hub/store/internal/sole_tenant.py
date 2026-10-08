"""The connections of a hub that serves one tenant — the store's only one (package-private).

The hub process is composed before its store is confirmed at head, and a store behind the tenant
revision names no tenant, so the scope is read from the store on first use rather than at
construction: a hub started over a stale store still comes up and reports itself not ready."""

from __future__ import annotations

from functools import cached_property

from sqlalchemy import select
from sqlalchemy.engine import Engine

from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.schema import tenants
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantId


class SoleTenantUnresolved(RuntimeError):
    """Raised when the store does not hold exactly one tenant for a single-tenant hub to serve."""


class SoleTenantConnections(ScopedConnections):
    """:class:`ScopedConnections` confined to the store's only tenant, named on first use."""

    def __init__(self, engine: Engine, errors: HubStoreErrorFactory) -> None:
        HubStoreConnections.__init__(self, engine, errors)

    # The attribute every inherited builder, ``scope`` and ``read``/``write`` reach the scope through.
    @cached_property
    def _scope(self) -> StoreScope:
        return StoreScope(self._read_sole_tenant())

    def _read_sole_tenant(self) -> TenantId:
        # The base read: a scoped read binds this very scope, which is what is being resolved.
        with HubStoreConnections.read(self, "read the sole tenant") as conn:
            ids = list(conn.execute(select(tenants.c.tenant_id).limit(2)).scalars())
        if len(ids) != 1:
            raise SoleTenantUnresolved(
                f"a single-tenant hub needs a store holding exactly one tenant; found {len(ids)}"
            )
        return TenantId(ids[0])
