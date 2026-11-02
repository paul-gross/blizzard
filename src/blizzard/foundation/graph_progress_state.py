"""How a chunk's visits left one node of its graph."""

from __future__ import annotations

from enum import StrEnum


class GraphProgressState(StrEnum):
    """How a chunk's visits left one node of its graph."""

    CURRENT = "current"
    PASSED = "passed"
    FAILED = "failed"
    UNVISITED = "unvisited"
