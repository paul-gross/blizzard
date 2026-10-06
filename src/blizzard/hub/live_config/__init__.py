"""Objects built from configured records, kept under the revisions they were built from.

The store stays the only answer to what is configured (``bzh:config-read-on-use``): every
hand-out reads the record's current revisions first, so a cold cache, a warm one, and a
second process's cache hand out equivalent objects."""

from __future__ import annotations

# Guards this process's own cache entries, never a store write.
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast, runtime_checkable

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import RecordKind
from blizzard.hub.domain.config.revisions import ConfigRevisions, IReadConfigRevisions


@runtime_checkable
class SupportsClose(Protocol):
    def close(self) -> None: ...


@domain_model
@dataclass(frozen=True)
class CacheKey:
    kind: RecordKind
    key: str
    revision: int
    secret_name: str | None
    secret_revision: int | None

    @classmethod
    def of(cls, kind: RecordKind, key: str, revisions: ConfigRevisions) -> CacheKey:
        return cls(kind, key, revisions.revision, revisions.secret_name, revisions.secret_revision)


class ConfigObjectCache:
    """Process-local; one per process, built in the composition root and shared by every
    store-backed reader. A replaced or vanished entry's object is closed when it has a ``close`` —
    one replacement later, not at once, so a caller still mid-request on the object it was handed
    is not cut off by an edit landing under it."""

    def __init__(self, revisions: IReadConfigRevisions) -> None:
        self._revisions = revisions
        self._entries: dict[tuple[RecordKind, str], tuple[CacheKey, object]] = {}
        self._superseded: dict[tuple[RecordKind, str], object] = {}
        self._lock = threading.Lock()

    def get[T](self, kind: RecordKind, key: str, build: Callable[[], T]) -> T | None:
        """The object built for the record's current revisions, building it on a miss;
        ``None`` when no such record exists. ``build`` reads the record itself."""
        current = self._revisions.revisions(kind, key)
        with self._lock:
            held = self._entries.get((kind, key))
            if current is None:
                if held is not None:
                    del self._entries[(kind, key)]
                    self._supersede(kind, key, held[1])
                return None
            wanted = CacheKey.of(kind, key, current)
            if held is not None and held[0] == wanted:
                return cast(T, held[1])
            built = build()
            self._entries[(kind, key)] = (wanted, built)
            if held is not None:
                self._supersede(kind, key, held[1])
        return built

    def _supersede(self, kind: RecordKind, key: str, replaced: object) -> None:
        """Close what an earlier replacement set aside, and set ``replaced`` aside in its place."""
        older = self._superseded.pop((kind, key), None)
        self._superseded[(kind, key)] = replaced
        if older is not None:
            _close(older)


def _close(obj: object) -> None:
    if isinstance(obj, SupportsClose):
        obj.close()


__all__ = ["CacheKey", "ConfigObjectCache", "SupportsClose"]
