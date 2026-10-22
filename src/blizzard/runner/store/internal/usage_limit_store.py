"""SQLAlchemy adapter for the bound runner's own usage-limit marks and floor clears (package-private) — the floor of a
runner spending no subscription. Runner-scoped: every statement is confined to the bound runner. Its host-scoped
sibling is ``host_usage_limit_store``."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Connection, Table, and_, func

from blizzard.foundation.logging import get_logger
from blizzard.runner.store.internal.usage_limit_rows import (
    USAGE_LIMIT_RETENTION_WINDOW,
    clear_of,
    invocation_of,
    mark_of,
    mark_values,
)
from blizzard.runner.store.schema import runner_floor_clears, runner_usage_limits
from blizzard.runner.store.scoped import RunnerStoreConnections
from blizzard.runner.throttle.usage_limits import FloorClear, IWriteRunnerUsageLimitRepository, UsageLimitMark

_log = get_logger("blizzard.runner.store")


class RunnerUsageLimitStore:
    """Read-write adapter over the bound runner's marks and clears."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def latest_usage_limit_mark(self) -> UsageLimitMark | None:
        marks = runner_usage_limits
        rows = self._store.all(self._store.select(marks).order_by(marks.c.marked_at.desc(), marks.c.id.desc()).limit(1))
        return mark_of(rows[0]) if rows else None

    def latest_floor_clear(self) -> FloorClear | None:
        clears = runner_floor_clears
        rows = self._store.all(
            self._store.select(clears).order_by(clears.c.cleared_at.desc(), clears.c.id.desc()).limit(1)
        )
        return clear_of(rows[0]) if rows else None

    def record_usage_limit_mark(self, mark: UsageLimitMark) -> bool:
        marks = runner_usage_limits
        # Check-then-insert (`bzh:sql-portable`); the unique invocation backs it.
        with self._store.begin() as conn:
            held = conn.execute(self._store.select(marks, marks.c.id).where(invocation_of(marks, mark))).first()
            if held is not None:
                return False
            conn.execute(self._store.insert(marks).values(**mark_values(mark)))
        _log.info("usage limit marked", lease_id=mark.lease_id, resets_at=mark.resets_at)
        return True

    def record_floor_clear(self, clear: FloorClear) -> None:
        with self._store.begin() as conn:
            conn.execute(self._store.insert(runner_floor_clears).values(cleared_at=clear.cleared_at, by=clear.by))
        _log.info("floor cleared", by=clear.by)

    def prune_usage_limits(self, *, now: datetime) -> int:
        cutoff = now - USAGE_LIMIT_RETENTION_WINDOW
        with self._store.begin() as conn:
            return self._prune_floor_facts(conn, runner_usage_limits, "marked_at", cutoff) + self._prune_floor_facts(
                conn, runner_floor_clears, "cleared_at", cutoff
            )

    def _prune_floor_facts(self, conn: Connection, table: Table, at: str, cutoff: datetime) -> int:
        newest = table.alias(f"newest_{table.name}")
        latest = self._store.select(newest, func.max(newest.c[at])).scalar_subquery()
        # `< latest` (never `<=`) keeps every row tied for the runner's newest.
        return conn.execute(self._store.delete(table).where(and_(table.c[at] < cutoff, table.c[at] < latest))).rowcount


def _conforms_runner_usage_limit_store(x: RunnerUsageLimitStore) -> IWriteRunnerUsageLimitRepository:
    return x
