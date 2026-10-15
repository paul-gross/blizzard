"""SQLAlchemy adapters for the runner-transfer seams (package-private): one runner's rows copied between two stores, or
removed from one.

Both stores' connections are bound to the moving runner and build every statement, so the runner statement guard judges
a transfer like any adapter's writes. The tables are the schema's runner-scoped ones in reference order; the outboxes go
through :mod:`.base`'s carry seam, and the host rows through the host member composition hands in."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import CursorResult, Table

from blizzard.foundation.logging import get_logger
from blizzard.runner.hub.runner_transfer import (
    HostIngest,
    IRunnerRowRemoval,
    IRunnerTransfer,
    IWriteHostIngestRepository,
)
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RUNNER_COLUMN, RUNNER_SCOPING, RunnerStoreConnections
from blizzard.runner.store.internal.base import CARRIED_OUTBOX_TABLES, carry_outbox_rows
from blizzard.runner.store.internal.row_transfer import (
    WORKSPACE_COLUMN,
    TransferHandle,
    in_reference_order,
    references,
    remapped,
    source_rows,
    surrogate_key,
)

_log = get_logger("blizzard.runner.store")

#: Every runner-scoped table of the schema, a referred-to table before each table naming it.
_RUNNER_TABLES: tuple[Table, ...] = tuple(
    in_reference_order(t for t in schema.metadata.sorted_tables if RUNNER_SCOPING.is_keyed(t))
)

#: The runner-scoped tables some column refers to, whose issued keys a copy records.
_REFERRED = frozenset(target for t in _RUNNER_TABLES for target in references(t).values())


class RunnerTransferStore:
    """Copies the bound runner from ``source`` into ``destination``; ``host`` is the destination's host member."""

    def __init__(
        self, source: RunnerStoreConnections, destination: RunnerStoreConnections, host: IWriteHostIngestRepository
    ) -> None:
        if source.bound_runner_id() != destination.bound_runner_id():
            raise ValueError("a transfer's two stores must be bound to the one runner it moves")
        self._source = source
        self._destination = destination
        self._host = host

    def copy_runner(self, *, workspace_ids: Mapping[str, str] | None = None, ingest: HostIngest | None = None) -> None:
        renames = dict(workspace_ids or {})
        runner_id = self._source.bound_runner_id()
        counts: dict[str, int] = {}
        with self._source.connect() as source, source.begin(), self._destination.begin() as destination:
            handle = TransferHandle(runner_id=runner_id, source=source, destination=destination)
            workspaces: dict[str, str] = {}
            for table in _RUNNER_TABLES:
                if table in CARRIED_OUTBOX_TABLES:
                    continue
                rows = source_rows(table, self._source.select(table), source)
                for row in rows:
                    old = row.get(WORKSPACE_COLUMN)
                    if old is not None:
                        new = renames.get(str(old), str(old))
                        workspaces[str(old)] = new
                        row[WORKSPACE_COLUMN] = new
                self._insert(handle, table, rows)
                counts[table.name] = len(rows)
            carry_outbox_rows(self._source, source, self._destination, destination)
            self._host.merge_host_rows_locked(handle, workspaces=workspaces)
            if ingest is not None:
                self._host.record_host_ingest_locked(handle, ingest)
        _log.info("runner rows copied", runner_id=runner_id, rows=sum(counts.values()), tables=len(counts))

    def _insert(self, handle: TransferHandle, table: Table, rows: list[dict[str, Any]]) -> None:
        """Insert ``rows`` into ``table`` on the destination, every surrogate key issued by the destination in source
        key order: row by row, each issued key recorded in the handle, where another table refers to the table; in one
        statement otherwise."""
        key = surrogate_key(table)
        values = [
            {
                name: value
                for name, value in remapped(table, row, handle).items()
                if name != RUNNER_COLUMN and (key is None or name != key.name)
            }
            for row in rows
        ]
        if not values:
            return
        if key is None or table.name not in _REFERRED:
            handle.destination.execute(self._destination.insert(table), values)
            return
        issued = handle.issued.setdefault(table.name, {})
        for row, value in zip(rows, values, strict=True):
            result = handle.destination.execute(self._destination.insert(table).values(**value))
            issued[row[key.name]] = _issued_key(result)


def _issued_key(result: CursorResult[Any]) -> int:
    """The surrogate key the destination issued for the row ``result`` inserted."""
    key = result.inserted_primary_key
    assert key is not None
    return int(key[0])


class RunnerRowRemovalStore:
    """Deletes the bound runner's rows from one store."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def remove_runner_rows(self, *, keep_counters: bool) -> None:
        runner_id = self._store.bound_runner_id()
        kept = {schema.outbox_counters} if keep_counters else set()
        deleted = 0
        with self._store.begin() as conn:
            for table in _RUNNER_TABLES:
                if table not in kept:
                    deleted += conn.execute(self._store.delete(table)).rowcount
        _log.info("runner rows removed", runner_id=runner_id, rows=deleted, counters_kept=keep_counters)


def _conforms_runner_transfer_store(x: RunnerTransferStore) -> IRunnerTransfer:
    return x


def _conforms_runner_row_removal_store(x: RunnerRowRemovalStore) -> IRunnerRowRemoval:
    return x
