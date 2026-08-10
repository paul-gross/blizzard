"""Prefixed-ULID id minting (unit tier) — the id convention.

Pins the two properties the id scheme promises: a type-evident prefix, and lexical
creation-ordering (a later mint sorts after an earlier one).
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import CHUNK_PREFIX, Id

pytestmark = pytest.mark.unit


def _clock(seconds: int = 0) -> FixedClock:
    return FixedClock(datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds))


def test_mint_is_prefixed_and_well_formed() -> None:
    chunk_id = Id.mint(CHUNK_PREFIX, _clock())
    assert chunk_id.value.startswith("ch_")
    assert chunk_id.has_prefix(CHUNK_PREFIX)


def test_has_prefix_rejects_wrong_prefix_and_malformed() -> None:
    assert not Id.mint(CHUNK_PREFIX, _clock()).has_prefix("gr")
    assert Id.parse("ch_tooshort") is None
    assert Id.parse("nounderscore") is None


def test_ulid_is_lexically_time_ordered() -> None:
    earlier = Id.mint(CHUNK_PREFIX, _clock(0)).ulid
    later = Id.mint(CHUNK_PREFIX, _clock(60)).ulid
    # The leading 10 chars encode the millisecond timestamp, so a later instant
    # sorts strictly after an earlier one regardless of the random tail.
    assert earlier[:10] < later[:10]


def test_ulid_is_26_chars() -> None:
    assert len(Id.mint(CHUNK_PREFIX, _clock()).ulid) == 26


def test_minted_at_round_trips_the_mint_instant() -> None:
    instant = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=42)
    # The ULID keeps millisecond precision, so the decode lands on the instant exactly.
    assert Id.mint(CHUNK_PREFIX, FixedClock(instant)).minted_at == instant


def test_minted_at_accepts_lowercase_ids() -> None:
    chunk_id = Id.mint(CHUNK_PREFIX, _clock())
    lowered = Id.parse(chunk_id.value.lower())
    assert lowered is not None
    assert lowered.minted_at == chunk_id.minted_at


def test_minted_at_rejects_malformed_ids() -> None:
    assert Id.parse("nounderscore") is None
    assert Id.parse("ch_tooshort") is None
    # An `I` is outside the Crockford alphabet — a well-shaped id with an
    # undecodable timestamp is malformed, not zero.
    undecodable = Id.parse("ch_" + "I" * 26)
    assert undecodable is not None
    assert undecodable.minted_at is None


def test_mint_at_reads_a_naive_instant_as_utc_not_the_hosts_local_zone() -> None:
    """Ids sort by their embedded instant, so a naive stamp read in the host's own zone
    mints an id sorting hours away from an aware one for the same moment. Run under a
    non-UTC zone, since under UTC the two readings coincide and pin nothing."""
    aware = datetime(2026, 1, 1, 12, tzinfo=UTC)
    previous = os.environ.get("TZ")
    os.environ["TZ"] = "America/Chicago"
    time.tzset()
    try:
        assert (
            Id.mint_at(CHUNK_PREFIX, aware.replace(tzinfo=None)).ulid[:10] == Id.mint_at(CHUNK_PREFIX, aware).ulid[:10]
        )
    finally:
        if previous is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = previous
        time.tzset()
