"""The work-items area's registrations: the hub work-item repository's ports over the adapter the root hands in, and
the hub work-item edits over them.

The area names no adapter. The store-free graph declares each registration absent, from the same roster."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.domain.work_items.ports import IReadWorkItemRepository, IWriteWorkItemRepository
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def register(registry: Registry, *, items: Ref[IWriteWorkItemRepository]) -> None:
    """Declare the work-item read and write ports over the adapter handed in, and the edits, once per tenant."""
    registry.alias(port(IReadWorkItemRepository), items)
    registry.alias(port(IWriteWorkItemRepository), items)
    registry.service(WorkItemEditService, lifetime=Lifetime.TENANT)


#: Every tenant key :func:`register` declares, for the store-free graph to declare absent.
_TENANT_KEYS: tuple[type[Any], ...] = (IReadWorkItemRepository, IWriteWorkItemRepository, WorkItemEditService)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports and the edits absent, for the store-free graph."""
    for missing in _TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
