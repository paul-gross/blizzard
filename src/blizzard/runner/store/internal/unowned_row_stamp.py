"""The start-up stamp: give every runner-scoped row that names no runner the bound runner's id (package-private).

Rows written before the store knew its runner — by the previous release's statements, or migrated from a store that
held no identity — carry a NULL ``runner_id`` and are invisible to the bound runner's scoped reads. The stamp runs when
the runner binds: at start, once the store names its runner, and at registration's first adoption, in the identity
write's own transaction. It is one of the two statements deliberately unscoped by runner — its update matches rows by
the absence of a runner, so it cannot carry a runner equality — and marks itself exempt from the runner statement guard.

It is idempotent and batched: each table is stamped along its primary key, at most :data:`_BATCH_ROWS` rows per
statement, and a crash part-way leaves rows the next start stamps (a no-window write)."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import Connection, Table

from blizzard.foundation.logging import get_logger
from blizzard.runner.store.errors import RUNNER_COLUMN, RUNNER_SCOPED_OPTION, RUNNER_SCOPING, RunnerStoreConnections
from blizzard.runner.store.schema import metadata, runner_identity

_log = get_logger("blizzard.runner.store")

_BATCH_ROWS = 1000

#: Every runner-scoped table whose ``runner_id`` may be NULL — all but ``runner_identity``, keyed by its own id.
_STAMPED: tuple[Table, ...] = tuple(
    table for table in metadata.sorted_tables if RUNNER_SCOPING.is_keyed(table) and table is not runner_identity
)


def stamp_unowned_rows(conn: Connection, runner_id: str) -> int:
    """Stamp ``runner_id`` on every runner-scoped row naming no runner, on ``conn``; answer how many were stamped."""
    exempt = conn.execution_options(**{RUNNER_SCOPED_OPTION: True})
    stamped = 0
    for table in _STAMPED:
        stamped += _stamp_table(exempt, table, runner_id)
    if stamped:
        _log.info("unowned runner rows stamped", runner_id=runner_id, rows=stamped)
    return stamped


def _stamp_table(conn: Connection, table: Table, runner_id: str) -> int:
    """Stamp ``table``'s unowned rows in one pass along its primary key, seeking past each page's last key."""
    key = next(iter(table.primary_key.columns))
    unowned = table.c[RUNNER_COLUMN].is_(None)
    page = sa.select(key).where(unowned).order_by(key).limit(_BATCH_ROWS)
    stamped = 0
    after: sa.ColumnElement[bool] = sa.true()
    while rows := conn.execute(page.where(after)).all():
        last = rows[-1][0]
        stamped += conn.execute(
            sa.update(table).where(unowned, after, key <= last).values({RUNNER_COLUMN: runner_id})
        ).rowcount
        after = key > last
    return stamped


class UnownedRowStamp:
    """The start-up stamp over the runner store: :func:`stamp_unowned_rows` in a transaction of its own."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def stamp(self, runner_id: str) -> int:
        with self._store.begin() as conn:
            return stamp_unowned_rows(conn, runner_id)
