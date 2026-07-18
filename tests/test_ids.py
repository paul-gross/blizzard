"""Prefixed-ULID id minting (unit tier) — the id convention.

Pins the two properties the id scheme promises: a type-evident prefix, and lexical
creation-ordering (a later mint sorts after an earlier one).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import CHUNK_PREFIX, has_prefix, mint, minted_at, ulid

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


def test_minted_at_round_trips_the_mint_instant() -> None:
    instant = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=42)
    decoded = minted_at(mint(CHUNK_PREFIX, FixedClock(instant)))
    assert decoded is not None
    # The ULID keeps millisecond precision, so the decode lands on the instant exactly.
    assert decoded == instant


def test_minted_at_accepts_lowercase_ids() -> None:
    chunk_id = mint(CHUNK_PREFIX, _clock())
    assert minted_at(chunk_id.lower()) == minted_at(chunk_id)


def test_minted_at_rejects_malformed_ids() -> None:
    assert minted_at("nounderscore") is None
    assert minted_at("ch_tooshort") is None
    # An `I` is outside the Crockford alphabet — a well-shaped id with an
    # undecodable timestamp is malformed, not zero.
    assert minted_at("ch_" + "I" * 26) is None
