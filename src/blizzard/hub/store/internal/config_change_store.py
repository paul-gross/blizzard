"""SQLAlchemy adapter for the change log (package-private).

:func:`append_change` writes inside the caller's transaction
(``bzh:configured-record``). This class is the read side."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Row, Select, func, or_
from sqlalchemy.engine import Connection

from blizzard.hub.domain.config.changes import (
    ChangeOp,
    ConfigChange,
    Door,
    FieldChange,
    IReadConfigChanges,
    RecordKind,
)
from blizzard.hub.store.schema import config_changes
from blizzard.hub.store.scoped import ScopedConnections, statements_of


def append_change(conn: Connection, change: ConfigChange) -> None:
    conn.execute(
        statements_of(conn)
        .insert(config_changes)
        .values(
            recorded_at=change.recorded_at,
            actor=change.actor,
            door=change.door.value,
            record_kind=change.record_kind.value,
            record_key=change.record_key,
            revision=change.revision,
            op=change.op.value,
            diff=json.dumps([{"field": d.field, "old": d.old, "new": d.new} for d in change.diff]),
            apply_id=change.apply_id,
            project_id=change.project_id,
        )
    )


class ConfigChangeStore:
    """Read adapter over the change log."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def page(
        self,
        *,
        before: int | None,
        limit: int,
        record_kind: RecordKind | None,
        record_key: str | None,
        project_id: str | None = None,
    ) -> list[ConfigChange]:
        query = self._filtered(
            self._store.select(config_changes).order_by(config_changes.c.id.desc()).limit(limit),
            record_kind=record_kind,
            record_key=record_key,
            project_id=project_id,
        )
        if before is not None:
            query = query.where(config_changes.c.id < before)
        with self._store.read("page") as conn:
            rows = conn.execute(query).all()
        return [self._of(row) for row in rows]

    def count(self, *, record_kind: RecordKind | None, record_key: str | None, project_id: str | None = None) -> int:
        query = self._filtered(
            self._store.select(config_changes, func.count()),
            record_kind=record_kind,
            record_key=record_key,
            project_id=project_id,
        )
        with self._store.read("count") as conn:
            return conn.execute(query).scalar_one()

    @staticmethod
    def _filtered(
        query: Select[Any],
        *,
        record_kind: RecordKind | None,
        record_key: str | None,
        project_id: str | None,
    ) -> Select[Any]:
        """``query`` narrowed by the log's filters; a ``project_id`` keeps the tenant-wide rows beside the project's."""
        if record_kind is not None:
            query = query.where(config_changes.c.record_kind == record_kind.value)
        if record_key is not None:
            query = query.where(config_changes.c.record_key == record_key)
        if project_id is not None:
            query = query.where(or_(config_changes.c.project_id == project_id, config_changes.c.project_id.is_(None)))
        return query

    @staticmethod
    def _of(row: Row[Any]) -> ConfigChange:
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
            project_id=row.project_id,
        )


def _conforms_config_change_store(x: ConfigChangeStore) -> IReadConfigChanges:
    return x
