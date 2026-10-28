"""A project's color: the palette name or custom ``#rrggbb`` a raw value must be, and the least-used palette color a
create takes when it states none.

The choice among equally least-used colors goes through a :data:`ColorPick` the composition binds once, so no domain
module draws at random and a test hands in a deterministic pick."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence

from blizzard.foundation.project_colors import HEX_COLOR_PATTERN, HexColor, ProjectColor
from blizzard.hub.domain.config.errors import ConfigFieldError

#: Picks one color among the candidates handed to it, which are never empty.
ColorPick = Callable[[Sequence[ProjectColor]], ProjectColor]


def parse_color(raw: str | None) -> ProjectColor | HexColor:
    """The palette color ``raw`` names, or ``raw`` lowercased when it is a ``#rrggbb``; :class:`ConfigFieldError` on
    ``color``, naming both forms, otherwise."""
    try:
        return ProjectColor(raw)
    except ValueError:
        pass
    # A document's explicit null reaches here as a non-string; it matches neither form.
    if isinstance(raw, str) and re.fullmatch(HEX_COLOR_PATTERN, raw.lower()):
        return raw.lower()
    palette = ", ".join(color.value for color in ProjectColor)
    raise ConfigFieldError("color", f"must be one of {palette}, or a #rrggbb hex color, got {raw!r}")


def least_used(held: Iterable[ProjectColor | HexColor], pick: ColorPick) -> ProjectColor:
    """``pick`` among the palette colors the fewest of ``held`` hold, offered in palette order; a custom color counts
    toward no palette color."""
    counts = Counter(held)
    fewest = min(counts[color] for color in ProjectColor)
    return pick([color for color in ProjectColor if counts[color] == fewest])


def decide_color(
    raw: str | None, siblings: Callable[[], Iterable[ProjectColor | HexColor]], pick: ColorPick
) -> ProjectColor | HexColor:
    """The color a create stores: ``raw`` parsed when stated, else the least-used palette color among ``siblings``,
    which is read only then."""
    return parse_color(raw) if raw is not None else least_used(siblings(), pick)
