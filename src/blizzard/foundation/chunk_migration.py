"""The chunk-migration vocabulary the wire carries — one definition, shared by both daemons."""

from __future__ import annotations

from enum import StrEnum


class MigrationMode(StrEnum):
    """How a chunk's intended migration fires at its next transition.

    ``AUTO`` fires only when the transition's own destination node name also exists on
    the target graph; ``FORCED`` fires unconditionally onto the intent's ``node_name``."""

    AUTO = "auto"
    FORCED = "forced"
