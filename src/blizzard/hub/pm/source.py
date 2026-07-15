"""The PM work-source seam (D-047/D-084) — a vendor-native pass-through read.

The hub reads a chunk's PM item (issue body + comment thread) straight from the
forge on demand and **never stores its contents** (D-047): the pointer is the
durable referent, the item is fetched fresh. The domain owns this Protocol
(``bzh:dependency-inversion``); the GitHub-shaped adapter under ``internal/``
implements it against the forge — the ``blizzard-mock`` forge in tests, GitHub in
production — deriving the API calls from the pointer's URL and the hub's own
per-vendor credentials.

``fetch`` returns a small domain :class:`PmItem`; the edge maps it onto a wire
:class:`~blizzard.wire.chunk.PmItemEntry` with the pointer, its label, and a ``fetched_at``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from blizzard.hub.domain.work import PmPointer


@dataclass(frozen=True)
class PmItem:
    """A pass-through PM item — body and comment bodies, vendor-native (D-047)."""

    body: str
    comments: list[str] = field(default_factory=list)


class PmSourceError(Exception):
    """The forge read failed — an unreachable forge or an unresolvable pointer."""


class IPmSource(Protocol):
    """The pass-through read seam the ``pm-items`` route depends on (D-047)."""

    def fetch(self, pointer: PmPointer) -> PmItem:
        """Fetch a pointer's body + comments from the forge, never storing them."""
        ...
