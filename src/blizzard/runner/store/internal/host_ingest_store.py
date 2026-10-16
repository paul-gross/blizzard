"""SQLAlchemy adapter for the host half of a runner transfer (package-private): its host-row merge and ingest record.

Host-scoped: every statement here names a host table, so none carries a runner equality. Each host table's treatment is
the one it declares (``info["transfer"]``), applied over the schema's host tables in the order their reference
declarations put them; no list of them is kept here."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Connection, Table, and_, select

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.runner.hub.runner_transfer import (
    HostIngest,
    IRunnerTransferHandle,
    IWriteHostIngestRepository,
    WorkspacePromptConflict,
)
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.internal.row_transfer import (
    WORKSPACE_COLUMN,
    TransferHandle,
    handle_of,
    in_reference_order,
    remapped,
    source_rows,
    surrogate_key,
)
from blizzard.runner.store.schema import host_ingests

_log = get_logger("blizzard.runner.store")

#: Every host-scoped table a transfer carries, a referred-to table before each table naming it.
_CARRIED: tuple[Table, ...] = tuple(
    in_reference_order(
        t
        for t in schema.metadata.sorted_tables
        if t.info["scope"] == "host" and t.info["transfer"] != schema.HOST_TRANSFER_NONE
    )
)


class HostIngestStore:
    """Read-write adapter over the host's ingest record, and the host half of every copy into this store."""

    def __init__(self, store: RunnerStoreConnections) -> None:
        self._store = store

    def latest_host_ingest(self, runner_id: str) -> HostIngest | None:
        rows = self._store.all(
            select(host_ingests)
            .where(host_ingests.c.runner_id == runner_id)
            .order_by(host_ingests.c.id.desc())
            .limit(1)
        )
        if not rows:
            return None
        row = rows[0]
        return HostIngest(
            runner_id=str(row.runner_id),
            source=str(row.source),
            source_alive_at=row.source_alive_at,
            ingested_at=row.ingested_at,
        )

    def merge_host_rows_locked(self, handle: IRunnerTransferHandle, *, workspaces: Mapping[str, str]) -> None:
        copy = handle_of(handle)
        carried = 0
        for table in _CARRIED:
            treatment = table.info["transfer"]
            rows = source_rows(table, select(table), copy.source)
            if treatment == schema.HOST_TRANSFER_MERGE:
                carried += sum(_merge_by_key(copy.destination, table, row) for row in rows)
            elif treatment == schema.HOST_TRANSFER_APPEND:
                carried += sum(_append(copy, table, row) for row in rows)
            elif treatment == schema.HOST_TRANSFER_WORKSPACE:
                carried += sum(
                    _carry_workspace_row(copy.destination, table, {**row, WORKSPACE_COLUMN: workspaces[old]})
                    for row in rows
                    if (old := row[WORKSPACE_COLUMN]) in workspaces
                )
        _log.info("host rows merged", runner_id=copy.runner_id, rows=carried)

    def record_host_ingest_locked(self, handle: IRunnerTransferHandle, ingest: HostIngest) -> None:
        handle_of(handle).destination.execute(
            host_ingests.insert().values(
                runner_id=ingest.runner_id,
                source=ingest.source,
                source_alive_at=ingest.source_alive_at,
                ingested_at=ingest.ingested_at,
            )
        )
        _log.info("host ingest recorded", runner_id=ingest.runner_id, source=ingest.source)


def _matching(table: Table, row: Mapping[str, Any]) -> Any:
    """Every column of ``row`` equal to the same column of a destination row, a ``NULL`` matching a ``NULL``."""
    return and_(*(table.c[name].is_(None) if value is None else table.c[name] == value for name, value in row.items()))


def _merge_by_key(destination: Connection, table: Table, row: Mapping[str, Any]) -> bool:
    """Insert ``row`` unless the destination holds a row under its primary key; whether it was inserted."""
    key = {column.name: row[column.name] for column in table.primary_key.columns}
    if destination.execute(select(*table.primary_key.columns).where(_matching(table, key))).first() is not None:
        return False
    destination.execute(table.insert().values(**row))
    return True


def _append(copy: TransferHandle, table: Table, row: Mapping[str, Any]) -> bool:
    """Append ``row`` under a key the destination issues, its references rewritten, unless the destination holds a row
    identical to it apart from that key; record which destination row stands for it, and whether it was appended."""
    key = surrogate_key(table)
    assert key is not None, f"{table.name} declares an appended transfer but has no surrogate key"
    values = {name: value for name, value in remapped(table, row, copy).items() if name != key.name}
    issued = copy.issued.setdefault(table.name, {})
    held = copy.destination.execute(select(key).where(_matching(table, values)).order_by(key).limit(1)).scalar()
    if held is not None:
        issued[row[key.name]] = held
        return False
    inserted = copy.destination.execute(table.insert().values(**values)).inserted_primary_key
    assert inserted is not None
    issued[row[key.name]] = int(inserted[0])
    return True


def _carry_workspace_row(destination: Connection, table: Table, row: Mapping[str, Any]) -> bool:
    """Insert ``row`` unless the destination holds a row for its workspace: that row's content must equal ``row``'s,
    its instants aside, or the transfer refuses. Whether it was inserted."""
    key = {WORKSPACE_COLUMN: row[WORKSPACE_COLUMN]}
    held = destination.execute(select(table).where(_matching(table, key))).first()
    if held is None:
        destination.execute(table.insert().values(**row))
        return True
    content = [c.name for c in table.columns if c.name != WORKSPACE_COLUMN and not isinstance(c.type, UtcDateTime)]
    theirs, ours = {n: row[n] for n in content}, {n: held._mapping[n] for n in content}
    if theirs != ours:
        raise WorkspacePromptConflict(str(row[WORKSPACE_COLUMN]), held=_shown(ours), offered=_shown(theirs))
    return False


def _shown(content: Mapping[str, Any]) -> str:
    return str(next(iter(content.values()))) if len(content) == 1 else str(dict(content))


def _conforms_host_ingest_store(x: HostIngestStore) -> IWriteHostIngestRepository:
    return x
