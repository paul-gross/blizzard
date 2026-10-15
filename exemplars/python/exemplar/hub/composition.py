"""One tenant's permit stores and the services wired over them."""

# exemplar: bzh:dependency-injection, bzh:controller-read-only.

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.hub import composition as hub
from blizzard.hub.store.scoped import ScopedConnections
from exemplar.hub.domain.permits.model import IReadPermitRepository, IWritePermitRepository
from exemplar.hub.domain.permits.service import PermitService
from exemplar.hub.store.internal.permit_store import PermitStore


@dataclass(frozen=True, kw_only=True)
class TenantStores(hub.TenantStores):
    """One tenant's repository set, its permits among them."""

    permit_store: IWritePermitRepository


@dataclass(frozen=True, kw_only=True)
class TenantServices(hub.TenantServices):
    """One tenant's wired services, its permits among them."""

    permits: IReadPermitRepository
    permit_service: PermitService


def build_tenant_stores(connections: ScopedConnections, *, clock: IClock) -> TenantStores:
    """Build the :class:`TenantStores` of the tenant ``connections`` is scoped to."""
    # exemplar: production declares ``permit_store`` on the real class and builds it beside the other stores.
    stores = hub.build_tenant_stores(connections, clock=clock)
    return TenantStores(**vars(stores), permit_store=PermitStore(connections))


def build_tenant_services(stores: TenantStores, served: hub.TenantServices) -> TenantServices:
    """Wire the tenant's permit services over ``stores``."""
    # exemplar: production declares both fields on the real class and passes one keyword each at its construction.
    permit_store = stores.permit_store
    return TenantServices(
        **vars(served), permits=permit_store, permit_service=PermitService(permits=permit_store, clock=stores.clock)
    )
