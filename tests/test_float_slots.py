"""The float-slot arithmetic an explicitly ordered list moves by (unit tier)."""

from __future__ import annotations

import math

import pytest

from blizzard.hub.domain.kernel.float_slots import Slot, land_after, renumbered, slot_after

pytestmark = pytest.mark.unit


def test_the_first_member_of_an_empty_list_takes_the_start() -> None:
    assert slot_after([], None, start=1.0) == 1.0
    assert slot_after([], None, start=0.0) == 0.0


def test_the_top_is_one_before_the_first() -> None:
    assert slot_after([3.0, 4.0], None, start=1.0) == 2.0


def test_the_tail_is_one_past_the_last() -> None:
    assert slot_after([1.0, 2.0], 1, start=1.0) == 3.0


def test_between_two_members_is_their_midpoint() -> None:
    assert slot_after([1.0, 2.0], 0, start=1.0) == 1.5


def test_a_spent_gap_answers_none() -> None:
    assert slot_after([1.0, math.nextafter(1.0, 2.0)], 0, start=1.0) is None


def test_renumbered_counts_from_the_start() -> None:
    assert renumbered(3, start=1.0) == (1.0, 2.0, 3.0)
    assert renumbered(2, start=0.0) == (0.0, 1.0)
    assert renumbered(0, start=1.0) == ()


def test_landing_in_a_held_gap_renumbers_nothing() -> None:
    assert land_after([1.0, 2.0], 0, start=1.0) == Slot(1.5)


def test_landing_in_a_spent_gap_renumbers_then_takes_the_midpoint() -> None:
    spent = [1.0, math.nextafter(1.0, 2.0), 3.0]
    assert land_after(spent, 0, start=1.0) == Slot(1.5, (1.0, 2.0, 3.0))
    assert land_after(spent, 0, start=0.0) == Slot(0.5, (0.0, 1.0, 2.0))
