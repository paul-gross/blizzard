"""Prefixed-ULID id minting (unit tier) — the id convention.

Pins the two properties the id scheme promises: a type-evident prefix, and lexical
creation-ordering (a later mint sorts after an earlier one).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import CHUNK_PREFIX, has_prefix, mint, ulid

pytestmark = pytest.mark.unit


def _clock(seconds: int = 0) -> FixedClock:
    return FixedClock(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds))


def test_mint_is_prefixed_and_well_formed() -> None:
    chunk_id = mint(CHUNK_PREFIX, _clock())
    assert chunk_id.startswith("ch_")
    assert has_prefix(chunk_id, CHUNK_PREFIX)


def test_has_prefix_rejects_wrong_prefix_and_malformed() -> None:
    chunk_id = mint(CHUNK_PREFIX, _clock())
    assert not has_prefix(chunk_id, "gr")
    assert not has_prefix("ch_tooshort", CHUNK_PREFIX)
    assert not has_prefix("nounderscore", CHUNK_PREFIX)


def test_ulid_is_lexically_time_ordered() -> None:
    earlier = ulid(_clock(0))
    later = ulid(_clock(60))
    # The leading 10 chars encode the millisecond timestamp, so a later instant
    # sorts strictly after an earlier one regardless of the random tail.
    assert earlier[:10] < later[:10]


def test_ulid_is_26_chars() -> None:
    assert len(ulid(_clock())) == 26
