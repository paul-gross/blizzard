"""``UtcDateTime`` / ``as_utc`` / ``iso_utc`` — the store-to-wire UTC primitives (unit tier).

``UtcDateTime`` is exercised through a real sqlite engine, not by calling its
``process_*`` hooks directly: the bug this type fixes is specifically that sqlite's
own driver drops ``tzinfo`` on write, so a round-trip through a real connection is
the only test that would fail without the type (issue #28, ``bzh:utc-instants``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, select

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.utc import UtcDateTime, as_utc, iso_utc

pytestmark = pytest.mark.unit

_metadata = MetaData()
_probe = Table(
    "utc_probe",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("stamp", UtcDateTime, nullable=False),
)


def _engine():  # type: ignore[no-untyped-def]
    engine = create_engine_from_url("sqlite://")
    _metadata.create_all(engine)
    return engine


def test_round_trips_an_aware_utc_datetime() -> None:
    engine = _engine()
    written = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    with engine.begin() as conn:
        conn.execute(_probe.insert().values(stamp=written))
        read = conn.execute(select(_probe.c.stamp)).scalar_one()
    assert read == written
    assert read.tzinfo is not None


def test_normalizes_a_non_utc_aware_datetime_on_write() -> None:
    engine = _engine()
    plus_five = timezone(timedelta(hours=5))
    written = datetime(2026, 7, 16, 17, 0, 0, tzinfo=plus_five)  # 12:00 UTC
    with engine.begin() as conn:
        conn.execute(_probe.insert().values(stamp=written))
        read = conn.execute(select(_probe.c.stamp)).scalar_one()
    assert read == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def test_a_naive_write_is_treated_as_utc_not_rejected() -> None:
    # Legacy callers / raw SQL may still pass a naive value; the type must not raise —
    # it is assumed already-UTC (the clock only ever writes UTC, ``bzh:injected-clock``).
    engine = _engine()
    written = datetime(2026, 7, 16, 12, 0, 0)
    with engine.begin() as conn:
        conn.execute(_probe.insert().values(stamp=written))
        read = conn.execute(select(_probe.c.stamp)).scalar_one()
    assert read == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def test_as_utc_is_idempotent_on_an_already_aware_value() -> None:
    value = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    assert as_utc(value) is value


def test_as_utc_attaches_utc_to_a_naive_value() -> None:
    value = datetime(2026, 7, 16, 12, 0, 0)
    assert as_utc(value) == datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
    assert as_utc(value).tzinfo is not None


def test_iso_utc_serializes_with_an_explicit_offset() -> None:
    assert iso_utc(datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)) == "2026-07-16T12:00:00+00:00"


def test_iso_utc_normalizes_a_naive_value_before_serializing() -> None:
    # Store-sourced values are aware once every column is UtcDateTime-typed; this
    # pins the defensive path a legacy or hand-built naive datetime still takes.
    assert iso_utc(datetime(2026, 7, 16, 12, 0, 0)) == "2026-07-16T12:00:00+00:00"
