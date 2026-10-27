"""What a graph choice's ``to:`` points at."""

from __future__ import annotations

from enum import StrEnum


class ChoiceTargetKind(StrEnum):
    """A same-graph ``node``, the reserved terminal ``done``, or a cross-graph ``graph``."""

    NODE = "node"
    DONE = "done"
    GRAPH = "graph"
