"""The hub's shared list-pagination contract (blizzard#526 D1/D2) — the keyset-cursor
shape every bounded hub list read shares. Analytics' own reads (blizzard#255/#256)
originated it; this module is where `MalformedCursor`, the default/ceiling, and the
opaque sort-key cursor codec now live, so a cursor a page never minted means the same
thing everywhere (`canon:one-owner`). A page's own decoder still owns its cursor's
arity and field types — this module only makes the string opaque and unreadable by hand."""

from __future__ import annotations

import base64
import json

#: The page size a caller gets by omitting `limit`.
DEFAULT_LIMIT = 200

#: The largest `limit` a caller may request. Over-ceiling is refused (422), never
#: silently clamped — a caller asking for more than this names a smaller page instead.
MAX_LIMIT = 1000


class MalformedCursor(ValueError):
    """A cursor a page never minted — the declared type callers depend on, so a route
    can tell it from any other read failure. Every keyset-paginated hub read raises
    this one type, never a read's own (`canon:one-owner`)."""

    def __init__(self, cursor: str) -> None:
        super().__init__(f"malformed cursor {cursor!r}")
        self.cursor = cursor


def encode_cursor(*parts: str | int | float) -> str:
    """An opaque cursor over a page's own sort-key tuple (D2) — a caller can only ever
    hand one back verbatim, never construct one by hand. `parts` are whatever the
    caller's own total order needs, positionally, in the order its own decoder expects
    them back."""
    raw = json.dumps(list(parts), separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> list[object]:
    """The inverse of `encode_cursor` — a plain JSON-typed list of whatever parts were
    encoded. The caller's own decoder still validates arity and each part's type; this
    only rejects a string that isn't a cursor this module minted at all."""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        parts = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception:
        raise MalformedCursor(cursor) from None
    if not isinstance(parts, list):
        raise MalformedCursor(cursor)
    return parts
