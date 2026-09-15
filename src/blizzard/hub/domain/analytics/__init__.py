"""The hub's analytics domain (blizzard#254/#255/#256) — two query seams over stored
facts. :mod:`.events`/:mod:`.extraction`/:mod:`.derivation` turn transcript segments
into the re-derivable event stream :mod:`.queries` reads; :mod:`.operational` derives
durations/spend/outcomes straight from execution facts. :exc:`MalformedCursor` moved to
:mod:`blizzard.hub.domain.pagination` (blizzard#526 D2), the shared cursor-error type
every paginated hub read raises; re-exported here for this package's own callers."""

from __future__ import annotations

from blizzard.hub.domain.pagination import MalformedCursor

__all__ = ["MalformedCursor"]
