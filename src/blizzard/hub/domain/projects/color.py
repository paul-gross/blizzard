"""A project's color: the palette name a raw value must be, and the least-used color a create takes when it states
none.

The choice among equally least-used colors goes through a :data:`ColorPick` the composition binds once, so no domain
module draws at random and a test hands in a deterministic pick."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence

from blizzard.foundation.project_colors import ProjectColor
from blizzard.hub.domain.config.work_sources import ConfigFieldError

#: Picks one color among the candidates handed to it, which are never empty.
ColorPick = Callable[[Sequence[ProjectColor]], ProjectColor]


def parse_color(raw: str) -> ProjectColor:
    """:class:`ConfigFieldError` on ``color``, listing the palette, when ``raw`` names no palette color."""
    try:
        return ProjectColor(raw)
    except ValueError:
        palette = ", ".join(color.value for color in ProjectColor)
        raise ConfigFieldError("color", f"must be one of {palette}, got {raw!r}") from None


def least_used(held: Iterable[ProjectColor], pick: ColorPick) -> ProjectColor:
    """``pick`` among the palette colors the fewest of ``held`` hold, offered in palette order."""
    counts = Counter(held)
    fewest = min(counts[color] for color in ProjectColor)
    return pick([color for color in ProjectColor if counts[color] == fewest])


def decide_color(raw: str | None, siblings: Callable[[], Iterable[ProjectColor]], pick: ColorPick) -> ProjectColor:
    """The color a create stores: ``raw`` parsed when stated, else the least-used among ``siblings``, which is read
    only then."""
    return parse_color(raw) if raw is not None else least_used(siblings(), pick)
