"""The transcript-event analytics domain (blizzard#254, ``epic:transcripts``).

Turns stored transcript segments into a queryable, versioned, re-derivable event
stream. :mod:`.events` is the store seam and domain types; :mod:`.extraction` is the
pure per-kind recognition over a segment's turns; :mod:`.derivation` is the per-segment
replacement unit and the standing convergence sweep."""

from __future__ import annotations
