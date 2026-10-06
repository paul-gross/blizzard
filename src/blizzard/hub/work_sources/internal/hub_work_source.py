"""The built-in ``hub`` work source — always seated, no
configured record, no credential. Unlike every other binding, this one's own
store is the item's system of record rather than a cache of an external one: nothing
here is fetched from a forge.
"""

from __future__ import annotations

from blizzard.foundation.work_items import WorkItemPriority
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace
from blizzard.hub.domain.chunk.model import HubWorkItem, IReadWorkItemRepository, WorkItemAuthor, WorkRef
from blizzard.hub.domain.garden.proposals.resolution import GardenProposalDeliveryResolution
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.work_items.editing import CreatedWorkItem, WithdrawnWorkItem, WorkItemEditService
from blizzard.hub.domain.work_items.model import WorkItemEdit, is_readable
from blizzard.hub.work_sources.closer import IWorkCloser, WorkItemGoneError
from blizzard.hub.work_sources.editor import IWorkEditor, WorkItemRefUnknownError
from blizzard.hub.work_sources.source import IWorkSource, WorkItem, WorkSourceError, resolve_author_view


class HubWorkSource:
    """Vendor-native reader over the hub's own ``work_items`` table — the built-in
    binding seated outside the configured-entry walk (``bzh:dependency-injection``).
    Implements ``IWorkEditor`` and ``IWorkCloser`` too,
    both delegating their writes to ``edits``, the domain-layer write half."""

    def __init__(
        self,
        items: IReadWorkItemRepository,
        edits: WorkItemEditService,
        users: IReadUserRepository,
        resolution: GardenProposalDeliveryResolution,
    ) -> None:
        self._items = items
        self._edits = edits
        self._users = users
        self._resolution = resolution

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
        if item is None or not is_readable(item):
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

    def web_url(self, pointer: WorkRef, *, live_holder: str | None) -> str | None:
        """The board's own chunk deep link — relative, since the hub declares no public
        origin. ``None`` when ``live_holder`` is ``None``."""
        return f"/board/chunk/{live_holder}" if live_holder is not None else None

    def forge_reference(self, pointer: WorkRef) -> str | None:
        """The built-in source lives on no forge, so there is nothing to cross-link."""
        return None

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        """The built-in source names no forge to link a branch through."""
        return None

    # -- IWorkCloser -----------------------------------------------------------

    def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
        """Mark the item ``delivered`` via ``edits.deliver`` — a no-op on an item already closed,
        withdrawn included; raises only :class:`WorkItemGoneError`, for a ref with no item row. Then
        resolves whichever garden-proposal findings `pointer` answers, safe to repeat:
        :meth:`GardenProposalDeliveryResolution.resolve_for_item` gates on its own durable marker."""
        item = self._items.get(pointer.source, pointer.ref)
        if item is None:
            raise WorkItemGoneError(f"no {RESERVED_HUB_SOURCE_NAME}:{pointer.ref} work item exists")
        self._edits.deliver(item)
        self._resolution.resolve_for_item(pointer)

    # -- IWorkEditor -------------------------------------------------------------

    def list(self, *, limit: int = 200) -> list[HubWorkItem]:
        return self._items.list(RESERVED_HUB_SOURCE_NAME, limit=limit)

    def get(self, pointer: WorkRef) -> HubWorkItem:
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

    def edit(self, pointer: WorkRef, edit: WorkItemEdit) -> HubWorkItem:
        item = self._resolve(pointer)
        return self._edits.edit(item, edit)

    def withdraw(self, pointer: WorkRef, *, by: str) -> WithdrawnWorkItem:
        item = self._resolve(pointer)
        return self._edits.withdraw(item, by=by)

    def _resolve(self, pointer: WorkRef) -> HubWorkItem:
        item = self._items.get(pointer.source, pointer.ref)
        if item is None:
            raise WorkItemRefUnknownError(pointer)
        return item


def _conforms_work_source(x: HubWorkSource) -> IWorkSource:
    return x


def _conforms_work_editor(x: HubWorkSource) -> IWorkEditor:
    return x


def _conforms_work_closer(x: HubWorkSource) -> IWorkCloser:
    return x
