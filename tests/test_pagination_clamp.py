"""`clamp_limit` forces a requested page size into the pagination contract (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.pagination import DEFAULT_LIMIT, MAX_LIMIT, clamp_limit

pytestmark = pytest.mark.unit


def test_none_yields_the_default() -> None:
    assert clamp_limit(None) == DEFAULT_LIMIT


@pytest.mark.parametrize("limit", [0, -1, -MAX_LIMIT])
def test_below_one_clamps_to_one(limit: int) -> None:
    assert clamp_limit(limit) == 1


@pytest.mark.parametrize("limit", [MAX_LIMIT + 1, MAX_LIMIT * 10])
def test_above_max_clamps_to_max(limit: int) -> None:
    assert clamp_limit(limit) == MAX_LIMIT


@pytest.mark.parametrize("limit", [1, 2, DEFAULT_LIMIT, MAX_LIMIT - 1, MAX_LIMIT])
def test_in_range_is_unchanged(limit: int) -> None:
    assert clamp_limit(limit) == limit
