"""The hub's scope inputs: the process values the entrypoint borrows into the container, and the tenant's store scope.

Every process value is context, supplied when the process scope opens and never finalized by the container; the tenant's
``StoreScope`` is supplied when its scope opens. The wall clock is borrowed too, since the entrypoint chooses it (the
system clock, or a test's); the monotonic clock is the system's alone. The packaged graph and system-artifact loaders
are borrowed the same way, so a test can substitute its own; each re-reads its files on access. This module imports no
composition root."""

from __future__ import annotations

from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, IMonotonicClock, SystemMonotonicClock
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.graphs import PackagedGraphs
from blizzard.hub.open_tenants import IReadOpenTenants, OpenTenants
from blizzard.hub.scopes.environ import HubEnviron
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.tenancy import STORE_UNAVAILABLE, StoreScope


def register(registry: Registry) -> None:
    """Declare the hub's borrowed process contexts (the served hub actions among them), its process clocks, and the
    tenant's store scope."""
    registry.context(port(Engine), lifetime=Lifetime.PROCESS)
    registry.context(port(HubStoreConnections), lifetime=Lifetime.PROCESS)
    registry.context(port(HubSettingsGraph), lifetime=Lifetime.PROCESS)
    registry.context(port(IClock), lifetime=Lifetime.PROCESS)
    registry.context(port(PackagedGraphs), lifetime=Lifetime.PROCESS)
    registry.context(port(PackagedSystemArtifacts), lifetime=Lifetime.PROCESS)
    registry.context(port(HubEnviron), lifetime=Lifetime.PROCESS)
    registry.context(port(HubActions), lifetime=Lifetime.PROCESS)
    monotonic = registry.service(SystemMonotonicClock, lifetime=Lifetime.PROCESS)
    registry.alias(port(IMonotonicClock), monotonic)
    registry.context(port(StoreScope), lifetime=Lifetime.TENANT)


def register_tenants(registry: Registry) -> None:
    """Declare the open tenants held in memory over the store's open-tenant read — what a request's tenant is resolved
    against."""
    registry.factory(open_tenants, lifetime=Lifetime.PROCESS)


def register_tenants_absent(registry: Registry) -> None:
    """Declare the open tenants absent, for the store-free graph."""
    registry.absent(port(OpenTenants), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)


def open_tenants(reads: IReadOpenTenants) -> OpenTenants:
    """The open tenants, loaded through ``reads`` on first use and on each refresh."""
    return OpenTenants(reads.open_tenants)
