"""The hub's event brokers: ``TenantBrokers`` owned once for the process, and each tenant's broker over it.

A tenant's ``EventBroker`` is a resource of its scope whose finalizer drops the broker, so closing the tenant ends every
stream open on it."""

from __future__ import annotations

from collections.abc import Iterator

from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.tenancy import StoreScope


def register(registry: Registry) -> None:
    """Declare ``TenantBrokers`` at the process and the tenant's ``EventBroker`` over it."""
    registry.service(TenantBrokers, lifetime=Lifetime.PROCESS)
    registry.resource(_open_broker, lifetime=Lifetime.TENANT)


def _open_broker(brokers: TenantBrokers, scope: StoreScope) -> Iterator[EventBroker]:
    broker = brokers.for_tenant(scope.tenant)
    try:
        yield broker
    finally:
        brokers.drop(scope.tenant)
