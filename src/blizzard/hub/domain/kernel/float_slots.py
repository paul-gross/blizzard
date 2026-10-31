"""Float-slot arithmetic for an explicitly ordered list: a move writes one position, the midpoint between its new
neighbours, and when the doubles between them are spent the list is renumbered to whole numbers first.

Positions are handed in ascending, without the moving member; ``after`` is the index the member lands after, or
``None`` for the top."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class Slot:
    """Where a member lands: its ``position``, and ``renumbered`` — the whole-number positions to write to the list's
    members first, in list order, when the gap was spent (empty when it held)."""

    position: float
    renumbered: tuple[float, ...] = ()


def renumbered(count: int, *, start: float) -> tuple[float, ...]:
    """``count`` whole-number positions from ``start``, one apart."""
    return tuple(start + index for index in range(count))


def slot_after(positions: Sequence[float], after: int | None, *, start: float) -> float | None:
    """The position landing right after ``positions[after]`` — before the first when ``after`` is ``None``, and
    ``start`` in an empty list. ``None`` when the gap to the next position is spent in doubles."""
    if after is None:
        return positions[0] - 1.0 if positions else start
    after_pos = positions[after]
    if after == len(positions) - 1:
        return after_pos + 1.0
    next_pos = positions[after + 1]
    if math.nextafter(after_pos, next_pos) >= next_pos:
        return None
    return (after_pos + next_pos) / 2


def land_after(positions: Sequence[float], after: int | None, *, start: float) -> Slot:
    """The :class:`Slot` right after ``positions[after]``; a spent gap renumbers the list from ``start`` and lands in
    the gap that leaves."""
    position = slot_after(positions, after, start=start)
    if position is not None:
        return Slot(position)
    whole = renumbered(len(positions), start=start)
    position = slot_after(whole, after, start=start)
    assert position is not None  # whole-number positions always leave a gap
    return Slot(position, whole)
