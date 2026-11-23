"""The hub's event brokers: ``TenantBrokers`` owned once for the process, each tenant's broker over it, and the
event log's live publisher the broker answers to.

A tenant's ``EventBroker`` is a resource of its scope whose finalizer drops the broker, so closing the tenant ends every
stream open on it."""

from __future__ import annotations

from collections.abc import Iterator

from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.hub.domain.chunk.ports.events import IEventLogPublisher
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.tenancy import StoreScope


def register(registry: Registry) -> None:
    """Declare ``TenantBrokers`` at the process, the tenant's ``EventBroker`` over it, and the broker as the tenant's
    event-log publisher."""
    registry.service(TenantBrokers, lifetime=Lifetime.PROCESS)
    registry.resource(_open_broker, lifetime=Lifetime.TENANT)
    registry.factory(event_log_publisher, lifetime=Lifetime.TENANT)


def event_log_publisher(events: EventBroker) -> IEventLogPublisher:
    """The live half of recording an event: the tenant's broker."""
    return events


def _open_broker(brokers: TenantBrokers, scope: StoreScope) -> Iterator[EventBroker]:
    broker = brokers.for_tenant(scope.tenant)
    try:
        yield broker
    finally:
        brokers.drop(scope.tenant)
