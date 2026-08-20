"""Hub-owned work item editing (blizzard#358) — create, in-place edit, and withdrawal.

Holds the *write* repository (``bzh:controller-read-only``); reached only through a
work-source binding's :class:`~blizzard.hub.work_sources.editor.IWorkEditor`, never
directly by a controller. ``WorkItemClosure`` has exactly two members and no third open
state, so an edit and a withdrawal share one closure guard: "already withdrawn" and
"already delivered" are the same condition, refusing one while silently rewriting the
other would be arbitrary."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work import (
    IReadChunkRepository,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkItemClosure,
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


class WorkItemHeldByLiveChunk(Exception):
    """A withdrawal targeted a pointer a live (non-terminal) chunk still holds — mirrors
    ``IngestConflict`` (``hub/domain/ingest.py``): withdrawing under a running chunk would
    degrade that chunk's work-item read to an unresolvable error."""

    def __init__(self, pointer: WorkRef, chunk_id: str) -> None:
        super().__init__(f"{pointer.source}:{pointer.ref} is held by live chunk {chunk_id}")
        self.pointer = pointer
        self.chunk_id = chunk_id


class WorkItemEditService:
    """Create, edit, and withdraw a hub-owned work item — the write half a work-source
    binding's editor delegates to once it has resolved a pointer to a loaded record."""

    def __init__(self, *, items: IWriteWorkItemRepository, chunks: IReadChunkRepository, clock: IClock) -> None:
        self._items = items
        self._chunks = chunks
        self._clock = clock

    def create(
        self, *, source: str, title: str, body: str, author: WorkItemAuthor, stated_priority: str | None
    ) -> WorkItemRecord:
        return self._items.create(
            source=source,
            title=title,
            body=body,
            author=author,
            stated_priority=stated_priority,
            at=self._clock.now(),
        )

    def edit(self, item: WorkItemRecord, *, title: str, body: str, stated_priority: str | None) -> WorkItemRecord:
        """Replace ``item``'s title/body/stated priority in place; raises
        :class:`WorkItemNotEditable` when ``item`` already carries a closure."""
        self._require_open(item)
        return self._items.edit(
            item.source, item.ref, title=title, body=body, stated_priority=stated_priority, at=self._clock.now()
        )

    def withdraw(self, item: WorkItemRecord) -> WorkItemRecord:
        """Close ``item`` as withdrawn; raises :class:`WorkItemNotEditable` when it
        already carries a closure, :class:`WorkItemHeldByLiveChunk` while a live chunk
        still holds it."""
        self._require_open(item)
        holder = self._chunks.find_live_holder(item.pointer)
        if holder is not None:
            raise WorkItemHeldByLiveChunk(item.pointer, holder)
        return self._items.close(item.source, item.ref, closure=WorkItemClosure.WITHDRAWN, at=self._clock.now())

    def _require_open(self, item: WorkItemRecord) -> None:
        if item.closure is not None:
            raise WorkItemNotEditable(item.work_item_id, item.closure)
