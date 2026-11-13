"""Repository ports for hub-owned work items."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.chunk.model import Chunk, ErrandMark, HubWorkItem, WorkItemAuthor, WorkRef
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction


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
    """Read-write work item operations; creation also mints a resting chunk."""

    def allocate_ref(self, source: str) -> str:
        """Allocate a never-reused ref in its own transaction; gaps are allowed."""
        ...

    def create_with_chunk(
        self,
        *,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: str | None,
        at: datetime,
        chunk: Chunk,
        transaction: Transaction | None = None,
    ) -> HubWorkItem:
        """Insert the item and its chunk atomically using an allocated ref; joins ``transaction`` when given, else
        opens its own write."""
        ...

    def create_run_with_chunk(
        self,
        *,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        routine_name: str,
        scope_slug: str,
        run_mode: str,
        project_id: str,
        at: datetime,
        chunk: Chunk,
        errand: ErrandMark | None = None,
        transaction: Transaction | None = None,
    ) -> HubWorkItem:
        """Atomically insert the item, resting chunk and routine run identity, marked with ``errand``
        when a cadence mints it; joins ``transaction`` when given, else opens its own write."""
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

    def accept_create(
        self,
        *,
        proposal_id: str,
        pointer: WorkRef,
        title: str,
        body: str,
        author: WorkItemAuthor,
        at: datetime,
        chunk: Chunk,
        reason: str | None,
        closed_by: str,
        transaction: LockedTransaction,
    ) -> HubWorkItem | None:
        """Atomically mint the item, chunk and proposal closure in ``transaction``, which holds the proposal's
        own key, or return None if already closed (insert-if-absent)."""
        ...
