"""The hub's shared list-pagination contract (blizzard#526 D1/D2): the keyset-cursor
shape every bounded hub list read shares, so a cursor a page never minted means the
same thing everywhere (`canon:one-owner`). A page's own decoder still owns arity/types."""

from __future__ import annotations

import base64
import json

#: The page size a caller gets by omitting `limit`.
DEFAULT_LIMIT = 200

#: The largest `limit` a caller may request.
MAX_LIMIT = 1000


class MalformedCursor(ValueError):
    """A cursor a page never minted — the one type every keyset-paginated hub read
    raises, never a read's own, so a route can tell it from any other failure (`canon:one-owner`)."""

    def __init__(self, cursor: str) -> None:
        super().__init__(f"malformed cursor {cursor!r}")
        self.cursor = cursor


def encode_cursor(*parts: str | int | float) -> str:
    """An opaque cursor over a page's own sort-key tuple (D2) — a caller can only ever
    hand it back verbatim. `parts` are positional, in the order its own decoder expects."""
    raw = json.dumps(list(parts), separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> list[object]:
    """The inverse of `encode_cursor`. The caller's own decoder still validates arity
    and type; this only rejects a string that isn't a cursor this module minted."""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        parts = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        raise MalformedCursor(cursor) from None
    if not isinstance(parts, list):
        raise MalformedCursor(cursor)
    return parts
