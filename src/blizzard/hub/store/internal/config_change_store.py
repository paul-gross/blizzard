"""SQLAlchemy adapter for the change log (package-private).

Writes are never made here on their own: each configured-record adapter calls
:func:`append_change` inside its own write block, so the row commits with the record
(``bzh:configured-record``). This class is the read side."""

from __future__ import annotations

import json

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection

from blizzard.hub.domain.config.changes import (
    ChangeOp,
    ConfigChange,
    Door,
    FieldChange,
    IReadConfigChanges,
    RecordKind,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import config_changes


def append_change(conn: Connection, change: ConfigChange) -> None:
    conn.execute(
        insert(config_changes).values(
            recorded_at=change.recorded_at,
            actor=change.actor,
            door=change.door.value,
            record_kind=change.record_kind.value,
            record_key=change.record_key,
            revision=change.revision,
            op=change.op.value,
            diff=json.dumps([{"field": d.field, "old": d.old, "new": d.new} for d in change.diff]),
            apply_id=change.apply_id,
        )
    )


class ConfigChangeStore:
    """Read adapter over the change log."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def page(
        self, *, before: int | None, limit: int, record_kind: RecordKind | None, record_key: str | None
    ) -> list[ConfigChange]:
        query = select(config_changes).order_by(config_changes.c.id.desc()).limit(limit)
        if before is not None:
            query = query.where(config_changes.c.id < before)
        if record_kind is not None:
            query = query.where(config_changes.c.record_kind == record_kind.value)
        if record_key is not None:
            query = query.where(config_changes.c.record_key == record_key)
        with self._store.read("page") as conn:
            rows = conn.execute(query).all()
        return [self._of(row) for row in rows]

    @staticmethod
    def _of(row) -> ConfigChange:  # type: ignore[no-untyped-def]
        return ConfigChange(
            id=row.id,
            recorded_at=row.recorded_at,
            actor=row.actor,
            door=Door(row.door),
            record_kind=RecordKind(row.record_kind),
            record_key=row.record_key,
            revision=row.revision,
            op=ChangeOp(row.op),
            diff=tuple(FieldChange(d["field"], d["old"], d["new"]) for d in json.loads(row.diff)),
            apply_id=row.apply_id,
        )


def _conforms_config_change_store(x: ConfigChangeStore) -> IReadConfigChanges:
    return x
