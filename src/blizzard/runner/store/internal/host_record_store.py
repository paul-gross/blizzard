"""SQLAlchemy adapters for the host's workspace and subscription records (package-private).

Host-scoped: a workspace or subscription is a fact about this host, never a runner's, so no statement here carries a
runner equality or names a runner-scoped table. Each kind's row, its lifecycle facts and its change row are written in
one transaction; a write decided against a revision the row no longer holds is refused, so no two writers both
commit."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Column, Connection, Row, Select, Table, select

from blizzard.foundation.clock import IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.settings_record.refusals import SettingsRevisionConflict
from blizzard.runner.host_records.model import (
    HostRecord,
    RecordActor,
    RecordExists,
    RecordFactOp,
    RecordKind,
    RecordWrite,
)
from blizzard.runner.host_records.ports import IWriteHostRecordRepository
from blizzard.runner.settings.ports import IApplyHandle, IApplyHostRecords
from blizzard.runner.store.internal.apply_handle import apply_of
from blizzard.runner.store.internal.host_settings_record import HostSettingsRecord
from blizzard.runner.store.schema import (
    host_subscription_facts,
    host_subscriptions,
    host_workspace_facts,
    host_workspaces,
)
from blizzard.runner.store.scoped import RunnerStoreConnections

_log = get_logger("blizzard.runner.store")


class _HostRecordStore:
    """One kind's records over its row table and its fact table; ``columns`` are the settings kept as their own
    columns rather than in the ``settings`` JSON."""

    _kind: RecordKind
    _rows: Table
    _facts: Table
    _key: str
    _columns: tuple[str, ...] = ()

    def __init__(self, store: RunnerStoreConnections, clock: IClock) -> None:
        self._store = store
        self._changes = HostSettingsRecord(clock)

    def record(self, key: str) -> HostRecord | None:
        with self._store.read_transaction() as conn:
            row = conn.execute(self._select().where(self._key_column(self._rows) == key)).first()
        return self._record(row) if row is not None else None

    def records(self, *, retired: bool = False) -> tuple[HostRecord, ...]:
        with self._store.read_transaction() as conn:
            rows = conn.execute(self._select().order_by(self._key_column(self._rows))).all()
        return tuple(record for record in map(self._record, rows) if retired or not record.retired)

    def apply(self, write: RecordWrite, by: RecordActor) -> None:
        with self._store.begin() as conn:
            self._apply(conn, write, by)

    def lock_locked(self, handle: IApplyHandle) -> None:
        """Lock every record of the kind on the apply's transaction."""
        apply_of(handle).conn.execute(self._rows.update().values(revision=self._rows.c.revision))

    def records_locked(self, handle: IApplyHandle) -> tuple[HostRecord, ...]:
        rows = apply_of(handle).conn.execute(self._select().order_by(self._key_column(self._rows))).all()
        return tuple(map(self._record, rows))

    def apply_locked(self, handle: IApplyHandle, write: RecordWrite, by: RecordActor) -> None:
        self._apply(apply_of(handle).conn, write, by)

    def _apply(self, conn: Connection, write: RecordWrite, by: RecordActor) -> None:
        """Write ``write`` on ``conn``: the row, its fact and its change row."""
        record = write.record
        key_column = self._key_column(self._rows)
        if write.creates():
            if conn.execute(select(key_column).where(key_column == record.key)).first() is not None:
                raise RecordExists(self._kind, record.key)
            conn.execute(
                self._rows.insert().values(
                    {self._key: record.key, "created_at": by.at, "revision": record.revision, **self._row(record)}
                )
            )
        else:
            moved = conn.execute(
                self._rows.update()
                .where(key_column == record.key, self._rows.c.revision == write.prior)
                .values(revision=record.revision, **self._row(record))
            ).rowcount
            if not moved:
                held = conn.execute(select(self._rows.c.revision).where(key_column == record.key)).scalar()
                raise SettingsRevisionConflict(int(held or 0), write.prior)
        if write.fact is not None:
            self._append_fact(conn, record.key, write.fact, by)
        self._changes.append_change(
            conn,
            diff=write.diff,
            op=write.op,
            door=by.door,
            actor=by.actor,
            apply_id=by.apply_id,
            revision=record.revision,
            record_kind=str(self._kind),
            record_key=record.key,
        )
        _log.debug("host record stored", kind=str(self._kind), key=record.key, op=write.op, revision=record.revision)

    def _key_column(self, table: Table) -> Column[Any]:
        return table.c[self._key]

    def _select(self) -> Select[Any]:
        newest = (
            select(self._facts.c.op)
            .where(self._key_column(self._facts) == self._key_column(self._rows))
            .order_by(self._facts.c.id.desc())
            .limit(1)
            .scalar_subquery()
            .label("newest_fact")
        )
        return select(self._rows, newest)

    def _row(self, record: HostRecord) -> dict[str, object]:
        settings = dict(record.settings)
        own = {column: str(settings.pop(column, "")) for column in self._columns}
        return {"settings": json.dumps(settings, sort_keys=True), **own}

    def _record(self, row: Row[Any]) -> HostRecord:
        settings: dict[str, object] = json.loads(row.settings)
        own: Mapping[str, object] = {column: getattr(row, column) for column in self._columns}
        return HostRecord(
            kind=self._kind,
            key=str(getattr(row, self._key)),
            revision=int(row.revision),
            settings={**own, **settings},
            retired=row.newest_fact == str(RecordFactOp.RETIRED),
        )

    def _append_fact(self, conn: Connection, key: str, op: RecordFactOp, by: RecordActor) -> None:
        conn.execute(
            self._facts.insert().values(
                {self._key: key, "recorded_at": by.at, "op": str(op), "actor": by.actor, "door": by.door}
            )
        )


class HostWorkspaceStore(_HostRecordStore):
    """Read-write adapter over the host's workspaces and their lifecycle facts."""

    _kind = RecordKind.WORKSPACE
    _rows = host_workspaces
    _facts = host_workspace_facts
    _key = "workspace_id"


class HostSubscriptionStore(_HostRecordStore):
    """Read-write adapter over the host's subscriptions and their lifecycle facts; a subscription's immutable
    ``provider`` is its own column."""

    _kind = RecordKind.SUBSCRIPTION
    _rows = host_subscriptions
    _facts = host_subscription_facts
    _key = "slug"
    _columns = ("provider",)


_workspace_store_conforms: type[IWriteHostRecordRepository] = HostWorkspaceStore
_subscription_store_conforms: type[IWriteHostRecordRepository] = HostSubscriptionStore
_workspace_apply_member_conforms: type[IApplyHostRecords] = HostWorkspaceStore
_subscription_apply_member_conforms: type[IApplyHostRecords] = HostSubscriptionStore
