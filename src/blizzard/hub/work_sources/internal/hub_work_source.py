"""The built-in ``hub`` work source (issue #357) — always seated, no
``[[work_source]]`` stanza, no credential. Unlike every other binding, this one's own
store is the item's system of record rather than a cache of an external one: nothing
here is fetched from a forge.
"""

from __future__ import annotations

from sqlalchemy import Engine

from blizzard.foundation.clock import SystemClock
from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.work import IReadChunkRepository, IReadWorkItemRepository, WorkItemClosure, WorkRef
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.work_sources.source import IWorkSource, WorkItem, WorkSourceError


class HubWorkSource:
    """Vendor-native reader over the hub's own ``work_items`` table — the built-in
    binding seated outside the configured-entry walk (``bzh:dependency-injection``)."""

    def __init__(self, items: IReadWorkItemRepository, chunks: IReadChunkRepository) -> None:
        self._items = items
        self._chunks = chunks

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
        return WorkItem(body=item.body, title=item.title, comments=[])

    def label(self, pointer: WorkRef) -> str | None:
        return f"{RESERVED_HUB_SOURCE_NAME}:{pointer.ref}"

    def web_url(self, pointer: WorkRef) -> str | None:
        """The board's own chunk deep link — relative, since the hub declares no public
        origin. ``None`` while no live chunk holds the pointer (chunk-minting-on-create
        is a later issue)."""
        chunk_id = self._chunks.find_live_holder(pointer)
        return f"/board/chunk/{chunk_id}" if chunk_id is not None else None

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        """The built-in source names no forge to link a branch through."""
        return None


def seat_hub_work_source(sources: dict[str, IWorkSource], *, engine: Engine) -> None:
    """Seats the built-in ``hub`` binding into ``sources`` in place — reached from both
    :meth:`~blizzard.hub.work_sources.internal.factory.WorkSourceEntry.registry` and
    ``tests/support.py::build_hub`` so the built-in is present in production and under
    test alike: never absent, never configured."""
    sources[RESERVED_HUB_SOURCE_NAME] = HubWorkSource(WorkItemStore(engine), ChunkStore(engine, SystemClock()))


def _conforms_work_source(x: HubWorkSource) -> IWorkSource:
    return x
