"""The write-half work-source seam's editor capability — full item CRUD (blizzard#358).

A sibling Protocol to ``IWorkAnnotator``/``IWorkCloser``: "this source serves browsable
items" is a *presence* question the registry answers rather than a ``hasattr`` probe. Only
the built-in ``hub`` source seats one (issue #357's own store is the item's system of
record); a configured forge source's registry entry has no editor at all, so a write
against one is a structural refusal, not a permission check."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.work import WorkItemAuthor, WorkItemRecord, WorkRef


class WorkItemRefUnknownError(Exception):
    """A pointer named a ``ref`` this source never allocated."""

    def __init__(self, pointer: WorkRef) -> None:
        super().__init__(f"no {pointer.source}:{pointer.ref} work item exists")
        self.pointer = pointer


class IWorkEditor(Protocol):
    """One work-source binding's full item surface — browsing plus the three write verbs."""

    def list(self) -> list[WorkItemRecord]:
        """Every item at this source, newest first, open and closed alike."""
        ...

    def get(self, pointer: WorkRef) -> WorkItemRecord:
        """One item by its pointer, open or closed.

        Raises :class:`WorkItemRefUnknownError` for an unallocated ``ref``."""
        ...

    def create(self, *, title: str, body: str, author: WorkItemAuthor, stated_priority: str | None) -> WorkItemRecord:
        """Allocate a fresh item at this source, open."""
        ...

    def edit(self, pointer: WorkRef, *, title: str, body: str, stated_priority: str | None) -> WorkItemRecord:
        """Replace ``pointer``'s title/body/stated priority in place and stamp ``edited_at``.

        Raises :class:`WorkItemRefUnknownError` for an unallocated ``ref``, and the
        service's closure-guard error for an item that already carries a closure."""
        ...

    def withdraw(self, pointer: WorkRef) -> WorkItemRecord:
        """Close ``pointer`` as withdrawn.

        Raises :class:`WorkItemRefUnknownError` for an unallocated ``ref``, the service's
        closure-guard error for an already-closed item, and its live-holder refusal while
        a live chunk still holds the pointer."""
        ...
