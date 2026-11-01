"""Repository ports for hub-owned work items."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.chunk.model import Chunk, HubWorkItem, WorkItemAuthor, WorkRef
from blizzard.hub.domain.kernel.transactions import Transaction


class IReadWorkItemRepository(Protocol):
    """Read-only hub-owned work item operations."""

    def get(self, source: str, ref: str) -> HubWorkItem | None:
        """Return the item, open or closed, or None if it was never allocated."""
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
    ) -> HubWorkItem:
        """Insert the item and its chunk atomically using an allocated ref."""
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
    ) -> HubWorkItem:
        """Atomically insert the item, resting chunk and routine run identity."""
        ...

    def edit(
        self, source: str, ref: str, *, title: str, body: str, stated_priority: str | None, at: datetime
    ) -> HubWorkItem | None:
        """Edit an open item, returning None if it is already closed."""
        ...

    def close(self, source: str, ref: str, *, closure: WorkItemClosure, at: datetime) -> HubWorkItem:
        """Record closure on an open item, once."""
        ...

    def withdraw_open(self, pointer: WorkRef, *, at: datetime, transaction: Transaction | None = None) -> None:
        """Withdraw an open item; missing and closed items are no-ops."""
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
    ) -> HubWorkItem | None:
        """Atomically mint the item, chunk and proposal closure, or return None if already closed."""
        ...
