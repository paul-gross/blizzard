"""The hub process's composition root: it registers the hub graph and opens the validated container over it.

Registration and building are two steps, so a stand-in root can reuse the hub's registration and add to it before it
builds. The store-backed graph declares the legacy bridge's leaves; the store-free graph declares each of them absent,
so a route that reaches one answers the store's unavailability rather than serving on nothing.

The process scope borrows every value it is opened with — the store's connections and the clock — and the
:class:`HubProcess` it returns owns only the container: closing it closes every tenant scope, then the process scope,
and leaves the borrowed values to the entrypoint that built them."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import port
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, Resolver, ScopeValue, ScopeValues, supply
from blizzard.foundation.roles import collaborator
from blizzard.hub.composition import register_legacy, register_legacy_absent
from blizzard.hub.domain.garden.findings import wiring as findings
from blizzard.hub.scopes import wiring as scopes
from blizzard.hub.scopes.tenants import TenantContainers
from blizzard.hub.store import wiring as store
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.tenancy import TenantId, UnknownTenant

#: The process scope's owner id.
HUB_OWNER = "hub"


@collaborator
@dataclass(frozen=True)
class HubProcess:
    """The hub's open process scope and the tenant scopes opened beneath it."""

    owner: OwnedChild
    tenants: TenantContainers

    @property
    def resolver(self) -> Resolver:
        """The process scope's guarded resolver."""
        return self.owner.resolver

    def close(self) -> None:
        """Close every tenant scope, then the process scope, attempting the second when the first fails."""
        try:
            self.tenants.close()
        finally:
            self.owner.close()


def register_hub(registry: LegacyRegistry, *, store_backed: bool) -> None:
    """Declare the hub graph: the scope inputs, the legacy bridge's leaves, the store adapters and the adopted areas
    over them — or, store-free, each store-backed registration absent."""
    scopes.register(registry)
    if store_backed:
        register_legacy(registry)
        findings.register(registry, store.register_findings(registry))
    else:
        register_legacy_absent(registry)
        store.register_findings_absent(registry)
        findings.register_absent(registry)


def build_store_backed(registry: LegacyRegistry, connections: HubStoreConnections, *, clock: IClock) -> HubProcess:
    """Open the store-backed graph ``registry`` declares over the borrowed ``connections`` and ``clock``."""
    owner = _opened(registry, supply(port(HubStoreConnections), connections), supply(port(IClock), clock))
    try:
        reads = owner.resolver.get(HubScopedReads)
    except BaseException:
        owner.close()
        raise
    return _process(owner, confirm_open=reads.tenants.confirm_open, open_tenants=reads.tenants.open_tenants)


def build_store_free(registry: LegacyRegistry, *, clock: IClock) -> HubProcess:
    """Open the store-free graph ``registry`` declares; it holds no tenant, so every tenant open is refused."""
    return _process(_opened(registry, supply(port(IClock), clock)), confirm_open=_no_tenant, open_tenants=tuple)


def open_store_backed(connections: HubStoreConnections, *, clock: IClock) -> HubProcess:
    """The hub's store-backed process, registered and opened."""
    return build_store_backed(_registered(store_backed=True), connections, clock=clock)


def open_store_free(*, clock: IClock) -> HubProcess:
    """The hub's store-free process, registered and opened."""
    return build_store_free(_registered(store_backed=False), clock=clock)


def _registered(*, store_backed: bool) -> LegacyRegistry:
    registry = LegacyRegistry()
    register_hub(registry, store_backed=store_backed)
    return registry


def _opened(registry: LegacyRegistry, *values: ScopeValue[Any]) -> OwnedChild:
    return build_container(registry, HUB_LIFETIMES, owner=HUB_OWNER, values=ScopeValues(values))


def _process(
    owner: OwnedChild, *, confirm_open: Callable[[TenantId], None], open_tenants: Callable[[], Iterable[TenantId]]
) -> HubProcess:
    return HubProcess(owner, TenantContainers(owner, confirm_open=confirm_open, open_tenants=open_tenants))


def _no_tenant(tenant: TenantId) -> None:
    raise UnknownTenant(tenant)
