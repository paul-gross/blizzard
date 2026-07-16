"""UTC-explicit instants, store to wire (``bzh:utc-instants``, issue #28).

Three primitives, one per boundary a naive datetime could otherwise cross:

* :class:`UtcDateTime` — the store column type. Binds an aware datetime as UTC and
  attaches ``UTC`` on the way back out, so a stored instant is aware on both sides of
  the driver regardless of dialect (sqlite drops ``tzinfo`` on write; an
  unqualified postgres ``TIMESTAMP`` shifts it on a non-UTC session).
* :func:`as_utc` — the domain-comparison coercion: idempotent on an already-aware
  value, and a defensive no-op once every column is :class:`UtcDateTime`-typed, kept
  because a domain function's inputs are not guaranteed to have come through the
  store (``bzh:domain-core``).
* :func:`iso_utc` — the wire serializer: the one call every API edge makes instead of
  a raw ``.isoformat()``, so the emitted string always carries an explicit offset.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """A ``DateTime`` column that is UTC-aware on both sides of the driver.

    ``process_bind_param`` normalizes an aware value to UTC before the driver sees it
    (closing the latent postgres session-timezone shift, not just sqlite's naive drop);
    ``process_result_value`` re-attaches ``UTC`` on read, restoring the tzinfo sqlite
    dropped. Portable — ``TypeDecorator`` over the dialect-agnostic ``DateTime``
    (``bzh:sql-portable``); the DDL it emits is byte-identical to a plain ``DateTime``,
    so retyping a column owes no migration.
    """

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

    Every store column is :class:`UtcDateTime`-typed, so this is a no-op on the store
    path. Kept as the comparison-site coercion anyway: :func:`~blizzard.hub.domain.registry.derive_online`
    and its runner-domain sibling are public pure functions whose inputs are not
    guaranteed to come from the store — a wire string parsed with
    ``datetime.fromisoformat`` can still be naive — so the domain stays correct on its
    own terms rather than depending on unnamed adapter behavior (``bzh:domain-core``).
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def iso_utc(value: datetime) -> str:
    """Serialize an instant for the wire — always with an explicit UTC offset.

    A naive ISO string is silently reinterpreted in the reader's local zone
    (``Date.parse`` treats an offset-less stamp as local time), so every API edge
    calls this instead of a raw ``.isoformat()``.
    """
    return as_utc(value).isoformat()
