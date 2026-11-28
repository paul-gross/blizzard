"""UTC-explicit instants at the store boundary (``bzh:utc-instants``).

The :class:`UtcDateTime` column type and the sqlite adapters. The comparison coercion and the wire
serializer need no store, and live in :mod:`blizzard.foundation.instants`."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """A ``DateTime`` column that holds UTC wall time and reads back UTC-aware.

    The DDL it emits is byte-identical to a plain ``DateTime`` (``bzh:sql-portable``),
    so retyping a column owes no migration."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        # Naive UTC, never an aware value: an aware bind reaches Postgres as ``timestamptz``
        # and is rendered into the session's ``TimeZone`` on the cast to the column.
        return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo is not None else value

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def sqlite_adapt_datetime(value: datetime) -> str:
    """The text sqlite3's deprecated default adapter wrote, so stored rows still order against new ones."""
    return value.isoformat(" ")


def sqlite_adapt_date(value: date) -> str:
    return value.isoformat()
