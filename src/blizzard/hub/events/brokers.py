"""One event broker per tenant: a tenant's stream sees only the events its own services publish, and ends
when its tenant's broker is dropped."""

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

    def end_session(self, session: str) -> None:
        """End every stream opened under ``session`` on every tenant's broker — a session spans tenants —
        leaving every other stream open."""
        with self._lock:
            brokers = list(self._brokers.values())
        for broker in brokers:
            broker.end_session(session)

    def drop(self, tenant: TenantId) -> None:
        """Forget ``tenant``'s broker, if it has one, closing it so every stream open on it ends; a later
        use creates a fresh one."""
        with self._lock:
            broker = self._brokers.pop(tenant, None)
        if broker is not None:
            broker.close()
