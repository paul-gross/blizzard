"""The hub's event wiring: ``TenantBrokers`` has one owner, and a tenant's broker is dropped when its scope closes (unit tier)."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.recording import RecordingRegistry
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, build_container
from blizzard.foundation.registry.scopes import ScopeValues, open_child, supply
from blizzard.hub.events import wiring
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.tenancy import StoreScope, TenantId

_A = TenantId("ten_A")


def _process() -> tuple[RecordingRegistry, object]:
    registry = RecordingRegistry()
    registry.context(port(StoreScope), lifetime=Lifetime.TENANT)
    wiring.register(registry)
    return registry, build_container(registry, HUB_LIFETIMES, owner="hub", values=ScopeValues())


def test_tenant_brokers_has_exactly_one_owner() -> None:
    registry, _ = _process()
    owners = [entry for entry in registry.entries if entry.key is TenantBrokers]
    assert len(owners) == 1
    assert owners[0].lifetime is Lifetime.PROCESS


def test_a_tenants_broker_is_the_one_tenant_brokers_holds_and_closing_the_tenant_drops_it() -> None:
    registry = RecordingRegistry()
    registry.context(port(StoreScope), lifetime=Lifetime.TENANT)
    wiring.register(registry)
    process = build_container(registry, HUB_LIFETIMES, owner="hub", values=ScopeValues())
    brokers = process.resolver.get(TenantBrokers)
    tenant = open_child(
        process, lifetime=Lifetime.TENANT, owner=_A, values=ScopeValues((supply(port(StoreScope), StoreScope(_A)),))
    )
    broker = tenant.resolver.get(EventBroker)
    assert broker is brokers.for_tenant(_A)
    tenant.close()
    assert brokers.for_tenant(_A) is not broker
    process.close()
