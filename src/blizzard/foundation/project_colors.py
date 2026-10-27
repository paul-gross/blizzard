"""The project color vocabulary."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import StringConstraints

#: The one grammar of a custom project color: a lowercase ``#rrggbb``.
HEX_COLOR_PATTERN = r"^#[0-9a-f]{6}$"

#: A project's custom color, a lowercase ``#rrggbb`` string beside the palette names.
HexColor = Annotated[str, StringConstraints(pattern=HEX_COLOR_PATTERN)]


class ProjectColor(StrEnum):
    """A project's color, by palette name; each name maps to one fixed display color."""

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
