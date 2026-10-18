"""The project color vocabulary the hub and its wire share."""

from __future__ import annotations

from enum import StrEnum


class ProjectColor(StrEnum):
    """A project's color, by palette name; the board renders each name through its own token."""

    RED = "red"
    ORANGE = "orange"
    AMBER = "amber"
    LIME = "lime"
    GREEN = "green"
    TEAL = "teal"
    CYAN = "cyan"
    BLUE = "blue"
    INDIGO = "indigo"
    VIOLET = "violet"
    PINK = "pink"
    SLATE = "slate"
