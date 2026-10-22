"""SQLAlchemy adapter for a subscription's usage-limit marks and floor clears (package-private).

Host-scoped: a subscription is this host's, shared by every runner spending it, so no statement here carries a runner
equality. Its runner-scoped twin, for a runner spending no subscription, is ``usage_limit_store``."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection, Table, and_, func, select

from blizzard.foundation.logging import get_logger
from blizzard.runner.store.internal.usage_limit_rows import (
    USAGE_LIMIT_RETENTION_WINDOW,
    clear_of,
    invocation_of,
    mark_of,
    mark_values,
)
from blizzard.runner.store.schema import subscription_floor_clears, subscription_usage_limits
from blizzard.runner.store.scoped import RunnerStoreConnections
from blizzard.runner.throttle.usage_limits import FloorClear, IWriteSubscriptionUsageLimitRepository, UsageLimitMark

_log = get_logger("blizzard.runner.store")


class SubscriptionUsageLimitStore:
    """Read-write adapter over the host's subscription marks and clears."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def latest_usage_limit_mark(self, slug: str) -> UsageLimitMark | None:
        marks = subscription_usage_limits
        rows = self._store.all(
            select(marks).where(marks.c.slug == slug).order_by(marks.c.marked_at.desc(), marks.c.id.desc()).limit(1)
        )
        return mark_of(rows[0]) if rows else None

    def latest_floor_clear(self, slug: str) -> FloorClear | None:
        clears = subscription_floor_clears
        rows = self._store.all(
            select(clears)
            .where(clears.c.slug == slug)
            .order_by(clears.c.cleared_at.desc(), clears.c.id.desc())
            .limit(1)
        )
        return clear_of(rows[0]) if rows else None

    def record_usage_limit_mark(self, slug: str, mark: UsageLimitMark) -> bool:
        marks = subscription_usage_limits
        # Check-then-insert (`bzh:sql-portable`); the unique invocation backs it.
        with self._store.begin() as conn:
            if conn.execute(select(marks.c.id).where(invocation_of(marks, mark))).first() is not None:
                return False
            conn.execute(marks.insert().values(slug=slug, **mark_values(mark)))
        _log.info("usage limit marked", slug=slug, lease_id=mark.lease_id, resets_at=mark.resets_at)
        return True

    def record_floor_clear(self, slug: str, clear: FloorClear) -> None:
        with self._store.begin() as conn:
            conn.execute(subscription_floor_clears.insert().values(slug=slug, cleared_at=clear.cleared_at, by=clear.by))
        _log.info("floor cleared", slug=slug, by=clear.by)

    def prune_usage_limits(self, *, now: datetime) -> int:
        cutoff = now - USAGE_LIMIT_RETENTION_WINDOW
        with self._store.begin() as conn:
            return _prune(conn, subscription_usage_limits, "marked_at", cutoff) + _prune(
                conn, subscription_floor_clears, "cleared_at", cutoff
            )


def _prune(conn: Connection, table: Table, at: str, cutoff: datetime) -> int:
    newest = table.alias(f"newest_{table.name}")
    latest = select(func.max(newest.c[at])).where(newest.c.slug == table.c.slug).scalar_subquery()
    # `< latest` (never `<=`) keeps every row tied for a slug's newest.
    return conn.execute(table.delete().where(and_(table.c[at] < cutoff, table.c[at] < latest))).rowcount


def _conforms_subscription_usage_limit_store(x: SubscriptionUsageLimitStore) -> IWriteSubscriptionUsageLimitRepository:
    return x
