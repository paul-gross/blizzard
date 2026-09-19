"""The per-tick chunk-status read seam (blizzard#521) — the batch projection every
per-chunk read goes through, so a whole ``tick()`` costs at most one hub round-trip per
distinct chunk id instead of one per read site.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from blizzard.runner.loop.hub import ChunkNotFoundError, HubClientError, IChunkStatusReader
from blizzard.wire.chunk import ChunkStatusView


class IChunkViews(Protocol):
    """One tick's (or one step's, standalone) view onto the hub's chunk statuses."""

    def get(self, chunk_id: str) -> ChunkStatusView:
        """Raises ``ChunkNotFoundError`` if the hub doesn't know this id, ``HubClientError``
        on transport failure."""
        ...

    def prime(self, chunk_ids: Iterable[str]) -> None:
        """Batch-read every id not already held, best-effort — swallow ``HubClientError`` (a
        failed prime just means every ``get()`` below re-tries its own read)."""
        ...

    def invalidate(self, chunk_id: str) -> None: ...


@dataclass(frozen=True)
class ReadThroughChunkViews:
    """The default, non-memoizing binding — ``get``/``prime`` each issue a fresh
    ``hub.chunk_statuses`` call every time.

    Wraps any ``LoopContext`` not built by ``tick()`` itself, so a step driven directly
    (e.g. by a test) reads the hub on every ``get()``."""

    hub: IChunkStatusReader

    def get(self, chunk_id: str) -> ChunkStatusView:
        found = self.hub.chunk_statuses([chunk_id])
        view = found.get(chunk_id)
        if view is None:
            raise ChunkNotFoundError(f"chunk {chunk_id} unknown")
        return view

    def prime(self, chunk_ids: Iterable[str]) -> None:
        pass  # nothing to prime into — this binding memoizes nothing

    def invalidate(self, chunk_id: str) -> None:
        pass  # nothing memoized to drop


@dataclass
class MemoizingChunkViewCache:
    """One tick's own memoized view — every distinct chunk id read at most once, unless a
    write this same tick invalidates it (D5).

    ``None`` in ``_cache`` means confirmed absent from the hub this tick — distinguished from
    "not yet read" (absent key), so a repeated ``get()`` on a genuinely-unknown id does not
    re-read the hub only to raise the same ``ChunkNotFoundError`` again."""

    hub: IChunkStatusReader
    _cache: dict[str, ChunkStatusView | None] = field(default_factory=dict)

    def get(self, chunk_id: str) -> ChunkStatusView:
        if chunk_id not in self._cache:
            # Left uncached on failure (not `except`-caught): a transport failure must not
            # poison the cache with a false absence — the next get() in this tick retries.
            found = self.hub.chunk_statuses([chunk_id])
            self._cache[chunk_id] = found.get(chunk_id)
        view = self._cache[chunk_id]
        if view is None:
            raise ChunkNotFoundError(f"chunk {chunk_id} unknown")
        return view

    def prime(self, chunk_ids: Iterable[str]) -> None:
        missing = [chunk_id for chunk_id in dict.fromkeys(chunk_ids) if chunk_id not in self._cache]
        if not missing:
            return
        try:
            found = self.hub.chunk_statuses(missing)
        except HubClientError:
            return  # best-effort head start — every get() below retries and raises properly
        for chunk_id in missing:
            self._cache[chunk_id] = found.get(chunk_id)

    def invalidate(self, chunk_id: str) -> None:
        self._cache.pop(chunk_id, None)


def _conforms_read_through(x: ReadThroughChunkViews) -> IChunkViews:
    return x


def _conforms_memoizing(x: MemoizingChunkViewCache) -> IChunkViews:
    return x
