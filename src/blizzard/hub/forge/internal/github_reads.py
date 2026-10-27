"""What the GitHub adapter keeps between calls (package-private): answers kept under their ``ETag`` so a ``304`` is
answered from them, and comparisons between two fixed revisions, which cannot change. Both are bounded, least recently
used first out, because an adapter lives as long as its repository's record goes unedited."""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass

from blizzard.hub.forge.landing import Comparison

#: Kept answers per adapter.
ANSWERS_MAX = 512
#: Kept comparisons per adapter; a comparison carries its files' patches.
COMPARISONS_MAX = 64

_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def is_object_id(revision: str) -> bool:
    """Whether ``revision`` is a full hex object id, the only spelling that names one revision forever."""
    return _OBJECT_ID.fullmatch(revision) is not None


@dataclass(frozen=True)
class KeptAnswer:
    etag: str
    status_code: int
    content: bytes


class _Lru[V]:
    def __init__(self, bound: int) -> None:
        self._bound = bound
        self._items: OrderedDict[str, V] = OrderedDict()

    def get(self, key: str) -> V | None:
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, key: str, value: V) -> None:
        self._items[key] = value
        self._items.move_to_end(key)
        while len(self._items) > self._bound:
            self._items.popitem(last=False)

    def drop(self, key: str) -> None:
        self._items.pop(key, None)

    def __len__(self) -> int:
        return len(self._items)


class KeptAnswers(_Lru[KeptAnswer]):
    """Each ``GET``'s last 2xx answer that carried an ``ETag``, by URL."""

    def __init__(self, bound: int = ANSWERS_MAX) -> None:
        super().__init__(bound)


class KeptComparisons(_Lru[Comparison]):
    """Comparisons between two fixed revisions, by ``base...head``."""

    def __init__(self, bound: int = COMPARISONS_MAX) -> None:
        super().__init__(bound)
