"""Where a minted graph stands in its name's lineage."""

from __future__ import annotations

from enum import StrEnum


class GraphLifecycle(StrEnum):
    """A graph is ``retired`` by its own fact, ``effective`` when it is the newest non-retired
    mint of its name, and ``superseded`` when it is neither."""

    EFFECTIVE = "effective"
    RETIRED = "retired"
    SUPERSEDED = "superseded"
