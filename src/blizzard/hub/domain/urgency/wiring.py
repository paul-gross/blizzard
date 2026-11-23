"""The urgency area's registrations: its read and write ports over the adapters it is handed, and its services.

The ports alias the one adapter instance the root hands in, so every urgency reader and writer in one tenant shares it;
the area names no adapter. The clearings a retire runs are not the area's to build — it declares only the key they
arrive under. The store-free graph declares each registration absent."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.urgency.ports import (
    IReadTenantSettings,
    IReadUrgencyChunks,
    IReadUrgencyOrder,
    IReadUrgencyRepository,
    IWriteTenantSettings,
    IWriteUrgencyChunks,
    IWriteUrgencyOrder,
    IWriteUrgencyRepository,
)
from blizzard.hub.domain.urgency.services import (
    TenantDefault,
    TenantSettingsService,
    UrgencyAuthoring,
    UrgencyLifecycle,
    UrgencyOrdering,
    UrgencyReading,
    UrgencyReferences,
)
from blizzard.hub.tenancy import STORE_UNAVAILABLE, StoreScope

_PORTS: tuple[tuple[type[Any], type[Any]], ...] = (
    (IReadUrgencyRepository, IWriteUrgencyRepository),
    (IReadUrgencyOrder, IWriteUrgencyOrder),
    (IReadTenantSettings, IWriteTenantSettings),
    (IReadUrgencyChunks, IWriteUrgencyChunks),
)
_SERVICES = (
    TenantDefault,
    UrgencyReading,
    UrgencyAuthoring,
    UrgencyLifecycle,
    TenantSettingsService,
    UrgencyReferences,
)


def urgency_ordering(
    order: IWriteUrgencyOrder, transactions: IHubTransactions, clock: IClock, scope: StoreScope
) -> UrgencyOrdering:
    """The tenant's urgency reorders, over its order port and its tenant's id."""
    return UrgencyOrdering(tenant_id=scope.tenant, order=order, transactions=transactions, clock=clock)


def register(
    registry: Registry,
    *,
    urgencies: Ref[IWriteUrgencyRepository],
    order: Ref[IWriteUrgencyOrder],
    settings: Ref[IWriteTenantSettings],
    chunks: Ref[IWriteUrgencyChunks],
) -> None:
    """Declare the urgency read and write ports over the adapters handed in, and the urgency services, each once per
    tenant."""
    for (read, write), target in zip(_PORTS, (urgencies, order, settings, chunks), strict=True):
        registry.alias(port(read), target)
        registry.alias(port(write), target)
    for service in _SERVICES:
        registry.service(service, lifetime=Lifetime.TENANT)
    registry.factory(urgency_ordering, lifetime=Lifetime.TENANT)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports and services absent, for the store-free graph."""
    for read, write in _PORTS:
        for missing in (read, write):
            registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    for missing in (*_SERVICES, UrgencyOrdering):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
