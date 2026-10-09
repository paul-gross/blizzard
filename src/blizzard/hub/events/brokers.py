"""One event broker per tenant: a tenant's stream sees only the events its own services publish."""

from __future__ import annotations

# Guards this process's own broker map, never a store write.
# ast-grep-ignore: bzh:store-exclusive-write
import threading

from blizzard.hub.events.broker import EventBroker
from blizzard.hub.tenancy import TenantId


class TenantBrokers:
    """The live event broker of each tenant, created on its first use and kept until :meth:`drop`."""

    def __init__(self) -> None:
        self._brokers: dict[TenantId, EventBroker] = {}
        self._lock = threading.Lock()

    def for_tenant(self, tenant: TenantId) -> EventBroker:
        """``tenant``'s broker, created on first use."""
        with self._lock:
            broker = self._brokers.get(tenant)
            if broker is None:
                broker = self._brokers[tenant] = EventBroker()
            return broker

    def adopt(self, tenant: TenantId, broker: EventBroker) -> EventBroker:
        """Hold ``broker`` as ``tenant``'s unless it already has one; returns the one it holds. The services
        a hub builds before it knows its only tenant's id publish to a broker made for them, adopted here
        once that id is known."""
        with self._lock:
            return self._brokers.setdefault(tenant, broker)

    def drop(self, tenant: TenantId) -> None:
        """Forget ``tenant``'s broker, if it has one; a later use creates a fresh one."""
        with self._lock:
            self._brokers.pop(tenant, None)
