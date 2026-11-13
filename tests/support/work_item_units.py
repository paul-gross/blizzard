"""A real hub unit holding hub work items' keys, for a test driving a work-item store write directly."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime

from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.chunk.model import HubWorkItem, WorkRef
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.domain.operations.delete import work_item_lock
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.transactions import HubTransactions


@contextmanager
def work_item_unit(connections: ScopedConnections, *pointers: WorkRef) -> Generator[LockedTransaction]:
    """One committed unit declaring every :func:`work_item_lock` of ``pointers``."""
    with HubTransactions(connections).begin() as unit:
        yield unit.lock_set(keys=[work_item_lock(pointer) for pointer in pointers])


def close_item(items: WorkItemStore, source: str, ref: str, *, closure: WorkItemClosure, at: datetime) -> HubWorkItem:
    """Close the item ``(source, ref)`` in a committed unit of its own declaring its key."""
    with work_item_unit(items.store, WorkRef(source=source, ref=ref)) as locked:
        return items.close(source, ref, closure=closure, at=at, transaction=locked)
