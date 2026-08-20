"""Hub-owned work item editing (blizzard#358) plus delivery closure (issue #360) —
create, in-place edit, withdraw, deliver.

Holds the *write* repository (``bzh:controller-read-only``), reached only through a
work-source binding's ``IWorkEditor``/``IWorkCloser``. ``WorkItemClosure`` has exactly
two members, so every closing write — withdraw or deliver — shares one guard."""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.edit import UNSET, UnsetType
from blizzard.hub.domain.work import (
    IReadChunkRepository,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkItemClosure,
    WorkItemPriority,
    WorkItemRecord,
    WorkRef,
)


class WorkItemNotEditable(Exception):
    """An edit or withdrawal targeted a work item that already carries a closure —
    closure is terminal, so neither verb is retroactive."""

    def __init__(self, work_item_id: str, closure: WorkItemClosure) -> None:
        super().__init__(f"work item {work_item_id} is {closure.value}, not editable")
        self.work_item_id = work_item_id
        self.closure = closure


@dataclass(frozen=True)
class WorkItemEdit:
    """The fields a single all-or-nothing item edit request supplies (blizzard#358),
    the same sentinel shape :class:`~blizzard.hub.domain.edit.ChunkEdit` carries: a
    field absent from ``edit`` is left unchanged, distinct from an explicit clear."""

    title: str | UnsetType = field(default=UNSET)
    body: str | UnsetType = field(default=UNSET)
    stated_priority: WorkItemPriority | None | UnsetType = field(default=UNSET)


class WorkItemHeldByLiveChunk(Exception):
    """A withdrawal targeted a pointer a live (non-terminal) chunk still holds — mirrors
    ``IngestConflict`` (``hub/domain/ingest.py``): withdrawing under a running chunk would
    degrade that chunk's work-item read to an unresolvable error."""

    def __init__(self, pointer: WorkRef, chunk_id: str) -> None:
        super().__init__(f"{pointer.source}:{pointer.ref} is held by live chunk {chunk_id}")
        self.pointer = pointer
        self.chunk_id = chunk_id


class WorkItemEditService:
    """Create, edit, withdraw, or deliver a hub-owned work item — the write half a
    work-source binding's editor/closer delegates to once it has resolved a pointer
    to a loaded record."""

    def __init__(self, *, items: IWriteWorkItemRepository, chunks: IReadChunkRepository, clock: IClock) -> None:
        self._items = items
        self._chunks = chunks
        self._clock = clock

    def create(
        self, *, source: str, title: str, body: str, author: WorkItemAuthor, stated_priority: WorkItemPriority | None
    ) -> WorkItemRecord:
        return self._items.create(
            source=source,
            title=title,
            body=body,
            author=author,
            stated_priority=stated_priority.value if stated_priority is not None else None,
            at=self._clock.now(),
        )

    def edit(self, item: WorkItemRecord, edit: WorkItemEdit) -> WorkItemRecord:
        """Resolve ``edit``'s sentinel-tagged fields against ``item`` — the record this
        call itself guards — and replace them in place; raises :class:`WorkItemNotEditable`
        when ``item`` already carries a closure, checked here and re-checked by the store's
        own ``closed_at IS NULL`` guard against a closure racing in between."""
        self._require_open(item)
        title = item.title if edit.title is UNSET else edit.title
        body = item.body if edit.body is UNSET else edit.body
        if edit.stated_priority is UNSET:
            stated_priority = item.stated_priority
        else:
            stated_priority = edit.stated_priority.value if edit.stated_priority is not None else None
        updated = self._items.edit(
            item.source, item.ref, title=title, body=body, stated_priority=stated_priority, at=self._clock.now()
        )
        if updated is None:
            current = self._items.get(item.source, item.ref)
            assert current is not None and current.closure is not None
            raise WorkItemNotEditable(current.work_item_id, current.closure)
        return updated

    def withdraw(self, item: WorkItemRecord) -> WorkItemRecord:
        """Close ``item`` as withdrawn; raises :class:`WorkItemNotEditable` when it
        already carries a closure, :class:`WorkItemHeldByLiveChunk` while a live chunk
        still holds it."""
        self._require_open(item)
        holder = self._chunks.find_live_holder(item.pointer)
        if holder is not None:
            raise WorkItemHeldByLiveChunk(item.pointer, holder)
        return self._items.close(item.source, item.ref, closure=WorkItemClosure.WITHDRAWN, at=self._clock.now())

    def deliver(self, item: WorkItemRecord) -> WorkItemRecord:
        """Close ``item`` as delivered (issue #360) — the delivery-closure sweep's own
        write path. No business rule beyond the store's own idempotency guard: a live
        chunk holding the pointer is the expected caller, not a conflict to block."""
        return self._items.close(item.source, item.ref, closure=WorkItemClosure.DELIVERED, at=self._clock.now())

    def _require_open(self, item: WorkItemRecord) -> None:
        if item.closure is not None:
            raise WorkItemNotEditable(item.work_item_id, item.closure)
