"""The built-in ``hub`` work source (issue #357) — always seated, no
``[[work_source]]`` stanza, no credential. Unlike every other binding, this one's own
store is the item's system of record rather than a cache of an external one: nothing
here is fetched from a forge.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.delete import DeleteService
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import (
    IReadChunkRepository,
    IReadWorkItemRepository,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkItemClosure,
    WorkItemPriority,
    WorkItemRecord,
    WorkRef,
)
from blizzard.hub.domain.work_items import CreatedWorkItem, WithdrawnWorkItem, WorkItemEdit, WorkItemEditService
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.work_sources.closer import IWorkCloser, WorkItemGoneError
from blizzard.hub.work_sources.editor import IWorkEditor, WorkItemRefUnknownError
from blizzard.hub.work_sources.source import IWorkSource, WorkItem, WorkSourceError, resolve_author_view


class HubWorkSource:
    """Vendor-native reader over the hub's own ``work_items`` table — the built-in
    binding seated outside the configured-entry walk (``bzh:dependency-injection``).
    Implements ``IWorkEditor`` (blizzard#358) and ``IWorkCloser`` (issue #360) too,
    both delegating their writes to ``edits``, the domain-layer write half."""

    def __init__(
        self,
        items: IReadWorkItemRepository,
        chunks: IReadChunkRepository,
        edits: WorkItemEditService,
        users: IReadUserRepository,
    ) -> None:
        self._items = items
        self._chunks = chunks
        self._edits = edits
        self._users = users

    def parse(self, token: str) -> WorkRef | None:
        """``hub:<n>`` only — the reserved name admits no ``#`` form and no URL form,
        since a hub-owned item has no forge issue to link."""
        prefix, sep, ref = token.partition(":")
        if sep and prefix == RESERVED_HUB_SOURCE_NAME and ref.isdigit():
            return WorkRef(source=RESERVED_HUB_SOURCE_NAME, ref=ref)
        return None

    def fetch(self, pointer: WorkRef) -> WorkItem:
        """Read the table fresh — no cache to invalidate, so an edit to an open item is
        visible on the next call. An unknown or withdrawn ref is unresolvable."""
        item = self._items.get(pointer.source, pointer.ref)
        if item is None or item.closure is WorkItemClosure.WITHDRAWN:
            raise WorkSourceError(f"no open {RESERVED_HUB_SOURCE_NAME}:{pointer.ref} work item exists")
        return WorkItem(
            body=item.body,
            title=item.title,
            comments=[],
            author=resolve_author_view(item.author, self._users),
            stated_priority=item.stated_priority,
        )

    def label(self, pointer: WorkRef) -> str | None:
        return f"{RESERVED_HUB_SOURCE_NAME}:{pointer.ref}"

    def web_url(self, pointer: WorkRef) -> str | None:
        """The board's own chunk deep link — relative, since the hub declares no public
        origin. Non-``None`` exactly while a live (non-terminal) chunk holds the pointer
        — from the moment create mints the item's resting chunk (blizzard#359) until
        that chunk reaches a terminal status (``stopped`` or ``done``); ``None`` before
        and after."""
        chunk_id = self._chunks.find_live_holder(pointer)
        return f"/board/chunk/{chunk_id}" if chunk_id is not None else None

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        """The built-in source names no forge to link a branch through."""
        return None

    # -- IWorkCloser -----------------------------------------------------------

    def close(self, pointer: WorkRef) -> None:
        """Mark the item ``delivered`` via ``edits.deliver`` — the only failure this
        raises is :class:`WorkItemGoneError`, for a ref with no item row. Idempotency
        and the withdrawn-item guard both live in the store's own ``closed_at IS NULL``
        update, reached through :meth:`WorkItemEditService.deliver`."""
        item = self._items.get(pointer.source, pointer.ref)
        if item is None:
            raise WorkItemGoneError(f"no {RESERVED_HUB_SOURCE_NAME}:{pointer.ref} work item exists")
        self._edits.deliver(item)

    # -- IWorkEditor -------------------------------------------------------------

    def list(self, *, limit: int = 200) -> list[WorkItemRecord]:
        return self._items.list(RESERVED_HUB_SOURCE_NAME, limit=limit)

    def get(self, pointer: WorkRef) -> WorkItemRecord:
        return self._resolve(pointer)

    def create(
        self, *, title: str, body: str, author: WorkItemAuthor, stated_priority: WorkItemPriority | None, graph: Graph
    ) -> CreatedWorkItem:
        return self._edits.create(
            source=RESERVED_HUB_SOURCE_NAME,
            title=title,
            body=body,
            author=author,
            stated_priority=stated_priority,
            graph=graph,
        )

    def edit(self, pointer: WorkRef, edit: WorkItemEdit) -> WorkItemRecord:
        item = self._resolve(pointer)
        return self._edits.edit(item, edit)

    def withdraw(self, pointer: WorkRef, *, by: str) -> WithdrawnWorkItem:
        item = self._resolve(pointer)
        return self._edits.withdraw(item, by=by)

    def _resolve(self, pointer: WorkRef) -> WorkItemRecord:
        item = self._items.get(pointer.source, pointer.ref)
        if item is None:
            raise WorkItemRefUnknownError(pointer)
        return item


def seat_hub_work_source(
    sources: dict[str, IWorkSource],
    editors: dict[str, IWorkEditor],
    closers: dict[str, IWorkCloser],
    *,
    store: HubStoreConnections,
    clock: IClock,
    users: IReadUserRepository,
    items: IWriteWorkItemRepository,
    delete: DeleteService,
) -> None:
    """Seats the built-in ``hub`` binding in place — reached from both
    :meth:`~blizzard.hub.work_sources.internal.factory.WorkSourceEntry.registry` and
    ``tests/support.py::build_hub``: never absent, never configured. ``users``/``items``/
    ``delete`` are the composition root's own instances (#362, #364), so the same
    claim-locked ``DeleteService`` backs every write path regardless of the door reaching it."""
    chunks = ChunkStore(store, clock)
    edits = WorkItemEditService(items=items, chunks=chunks, clock=clock, delete=delete)
    hub_source = HubWorkSource(items, chunks, edits, users)
    sources[RESERVED_HUB_SOURCE_NAME] = hub_source
    editors[RESERVED_HUB_SOURCE_NAME] = hub_source
    closers[RESERVED_HUB_SOURCE_NAME] = hub_source


def _conforms_work_source(x: HubWorkSource) -> IWorkSource:
    return x


def _conforms_work_editor(x: HubWorkSource) -> IWorkEditor:
    return x


def _conforms_work_closer(x: HubWorkSource) -> IWorkCloser:
    return x
