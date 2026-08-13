"""The hub's analytics domain (blizzard#254/#255/#256) — two query seams over stored
facts. :mod:`.events`/:mod:`.extraction`/:mod:`.derivation` turn transcript segments
into the re-derivable event stream :mod:`.queries` reads; :mod:`.operational` derives
durations/spend/outcomes straight from execution facts. :exc:`MalformedCursor` is this
package's one shared cursor-error type — both query seams raise it."""

from __future__ import annotations


class MalformedCursor(ValueError):
    """A cursor a page never minted — the declared type callers depend on, so a route
    can tell it from any other failure inside a read. Shared by :mod:`.queries` and
    :mod:`.operational` (``canon:one-owner``): a cursor a page never minted means the
    same thing in either seam."""

    def __init__(self, cursor: str) -> None:
        super().__init__(f"malformed cursor {cursor!r}")
        self.cursor = cursor
