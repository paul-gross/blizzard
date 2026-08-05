"""UTC-explicit instants, store to wire (``bzh:utc-instants``, issue #28).

Three primitives, one per boundary a naive datetime could otherwise cross: the
:class:`UtcDateTime` store column type, the :func:`as_utc` comparison coercion, and
the :func:`iso_utc` wire serializer."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """A ``DateTime`` column that is UTC-aware on both sides of the driver.

    The DDL it emits is byte-identical to a plain ``DateTime`` (``bzh:sql-portable``),
    so retyping a column owes no migration."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def as_utc(value: datetime) -> datetime:
    """Read a datetime back as UTC-aware, idempotent on an already-aware value.

    The comparison-site coercion: a pure domain function's inputs are not guaranteed
    to have come through the store (``bzh:domain-core``).
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def iso_utc(value: datetime) -> str:
    """Serialize an instant for the wire — always with an explicit UTC offset.

    An offset-less stamp is silently reinterpreted in the reader's local zone.
    """
    return as_utc(value).isoformat()
