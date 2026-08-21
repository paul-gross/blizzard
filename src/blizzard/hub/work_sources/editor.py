"""The write-half work-source seam's editor capability — full item CRUD (blizzard#358).

A sibling Protocol to ``IWorkAnnotator``/``IWorkCloser``: "this source serves browsable
items" is a *presence* question the registry answers. Only the built-in ``hub`` source
seats one; a configured forge source's registry entry has none, so a write against one
is a structural refusal, not a permission check."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import WorkItemAuthor, WorkItemPriority, WorkItemRecord, WorkRef
from blizzard.hub.domain.work_items import CreatedWorkItem, WorkItemEdit


class WorkItemRefUnknownError(Exception):
    """A pointer named a ``ref`` this source never allocated."""

    def __init__(self, pointer: WorkRef) -> None:
        super().__init__(f"no {pointer.source}:{pointer.ref} work item exists")
        self.pointer = pointer


class IWorkEditor(Protocol):
    """One work-source binding's full item surface — browsing plus the three write verbs."""

    def list(self, *, limit: int = 200) -> list[WorkItemRecord]:
        """Up to ``limit`` items at this source, newest first, open and closed alike."""
        ...

    def get(self, pointer: WorkRef) -> WorkItemRecord:
        """One item by its pointer, open or closed.

        Raises :class:`WorkItemRefUnknownError` for an unallocated ``ref``."""
        ...

    def create(
        self, *, title: str, body: str, author: WorkItemAuthor, stated_priority: WorkItemPriority | None, graph: Graph
    ) -> CreatedWorkItem:
        """Allocate a fresh item at this source, open, and mint its resting chunk pinned
        to ``graph`` in the same transaction (blizzard#359). Raises
        :class:`~blizzard.hub.domain.ingest.IngestConflict` on an out-of-band pre-empt."""
        ...

    def edit(self, pointer: WorkRef, edit: WorkItemEdit) -> WorkItemRecord:
        """Resolve ``edit``'s sentinel-tagged fields against the record ``pointer`` names
        and replace them in place, stamping ``edited_at``.

        Raises :class:`WorkItemRefUnknownError` for an unallocated ``ref``, and the
        service's closure-guard error for an item that already carries a closure."""
        ...

    def withdraw(self, pointer: WorkRef) -> WorkItemRecord:
        """Close ``pointer`` as withdrawn.

        Raises :class:`WorkItemRefUnknownError` for an unallocated ``ref``, the service's
        closure-guard error for an already-closed item, and its live-holder refusal while
        a live chunk still holds the pointer."""
        ...
