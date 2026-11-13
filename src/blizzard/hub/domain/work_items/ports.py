"""Repository ports for hub-owned work items."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.chunk.model import HubWorkItem, WorkItemAuthor, WorkRef
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction
from blizzard.hub.domain.projects.model import ProjectId


class IReadWorkItemRepository(Protocol):
    """Read-only hub-owned work item operations."""

    def get(self, source: str, ref: str, *, transaction: Transaction | None = None) -> HubWorkItem | None:
        """Return the item, open or closed, or None if it was never allocated; joins ``transaction`` when given."""
        ...

    def list(self, source: str, *, limit: int = 200) -> list[HubWorkItem]:
        """Return items newest first, breaking creation-time ties by work item ID."""
        ...

    def get_many(self, pointers: Sequence[WorkRef]) -> dict[WorkRef, HubWorkItem]:
        """Return requested items keyed by pointer, omitting missing items."""
        ...


class IWriteWorkItemRepository(IReadWorkItemRepository, Protocol):
    """Read-write work item operations."""

    def allocate_ref(self, source: str) -> str:
        """Allocate a never-reused ref in its own transaction; gaps are allowed."""
        ...

    def create(
        self,
        *,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: str | None,
        at: datetime,
        project_id: ProjectId | None,
        routine_name: str | None = None,
        scope_slug: str | None = None,
        run_mode: str | None = None,
        transaction: Transaction | None = None,
    ) -> HubWorkItem:
        """Insert the open item at an allocated ref in ``project_id``, its chunk's project, writing only the item;
        ``routine_name``/``scope_slug``/``run_mode`` are a routine run's own columns, ``None`` for every other item.
        Joins ``transaction`` when given, else opens its own write."""
        ...

    def edit(
        self,
        source: str,
        ref: str,
        *,
        title: str,
        body: str,
        stated_priority: str | None,
        at: datetime,
        transaction: LockedTransaction,
    ) -> HubWorkItem:
        """Write the item's fields unconditionally in ``transaction``, which must hold the item's
        ``work_item_lock``: the caller has re-read it open under that key and decided the edit."""
        ...

    def close(
        self, source: str, ref: str, *, closure: WorkItemClosure, at: datetime, transaction: LockedTransaction
    ) -> HubWorkItem:
        """Write the item's closure unconditionally in ``transaction``, which must hold the item's
        ``work_item_lock``: the caller has re-read it open under that key and decided the close."""
        ...

    def withdraw_open(self, pointer: WorkRef, *, at: datetime, transaction: LockedTransaction) -> None:
        """Write the item's withdrawn closure unconditionally in ``transaction``, which must hold the item's
        ``work_item_lock``: the caller has re-read it open under that key."""
        ...
