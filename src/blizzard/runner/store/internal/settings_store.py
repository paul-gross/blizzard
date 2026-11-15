"""Transactional runner settings adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection

from blizzard.foundation.clock import IClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange
from blizzard.foundation.settings_record.write import Validate, seed_entry, write
from blizzard.runner.settings.ports import IWriteRunnerSettings, SettingsChange, SettingsSnapshot
from blizzard.runner.store.internal.settings_record import RunnerSettingsRecord
from blizzard.runner.store.schema import runner_identity, runner_setting_changes, runner_setting_entries
from blizzard.runner.store.scoped import RunnerStoreConnections


class RunnerSettingsStore:
    def __init__(self, store: RunnerStoreConnections, clock: IClock) -> None:
        self._store = store
        self._record = RunnerSettingsRecord(store, clock)

    def snapshot(self) -> SettingsSnapshot:
        with self._store.read_transaction() as conn:
            return SettingsSnapshot(self._record.revision(conn), self._record.entries(conn))

    def changes(self, *, limit: int, before: int | None = None) -> tuple[SettingsChange, ...]:
        stmt = self._store.select(runner_setting_changes).order_by(runner_setting_changes.c.id.desc()).limit(limit)
        if before is not None:
            stmt = stmt.where(runner_setting_changes.c.id < before)
        with self._store.read_transaction() as conn:
            return tuple(_change(row) for row in conn.execute(stmt))

    def provenance(self) -> dict[str, SettingsChange]:
        with self._store.read_transaction() as conn:
            return self._provenance(conn)

    def snapshot_with_provenance(self) -> tuple[SettingsSnapshot, Mapping[str, SettingsChange]]:
        with self._store.read_transaction() as conn:
            snapshot = SettingsSnapshot(self._record.revision(conn), self._record.entries(conn))
            return snapshot, self._provenance(conn)

    def _provenance(self, conn: Connection) -> dict[str, SettingsChange]:
        # Each entry carries the revision that last moved it, so no history scan is
        # needed to find its newest change. Join on both runner and revision.
        joined = self._store.scope_join(
            runner_setting_entries,
            runner_setting_changes,
            runner_setting_entries.c.revision == runner_setting_changes.c.revision,
        )
        stmt = self._store.select(joined, runner_setting_entries.c.key, *runner_setting_changes.c)
        return {row.key: _change(row) for row in conn.execute(stmt)}

    def _lock_identity(self, conn: Connection) -> None:
        locked = conn.execute(
            self._store.update(runner_identity).values(runner_id=runner_identity.c.runner_id)
        ).rowcount
        if not locked:
            raise ValueError("runner settings require a retained runner identity")

    def seed(
        self, file: ConfigView, keys: Sequence[str], *, validate: Validate, aliases: Mapping[str, str] | None = None
    ) -> None:
        with self._store.begin() as conn:
            self._lock_identity(conn)
            # Validate the complete resulting file seed, not transient prefixes:
            # related authored keys can be valid only when considered together.
            existing = self._record.entries(conn)
            if aliases:
                blocked = {alias for alias, key in aliases.items() if key in existing}
                blocked.update(key for alias, key in aliases.items() if alias in existing)
                keys = tuple(key for key in keys if key not in blocked)
            pending = {key: Entry(file.value(key)) for key in keys if key not in existing and file.states(key)}

            def validate_seed(entries: Mapping[str, Entry]) -> Mapping[str, str]:
                return validate({**pending, **entries})

            for key in keys:
                seed_entry(conn, self._record, key, file, validate=validate_seed)

    def write(
        self,
        change: Change,
        *,
        if_match: int | None,
        validate: Validate,
        door: Door,
        actor: str,
        apply_id: str | None = None,
    ) -> tuple[FieldChange, ...]:
        with self._store.begin() as conn:
            # Serialize writers and identity removal, including before a settings row exists.
            self._lock_identity(conn)
            return write(
                conn,
                self._record,
                change,
                if_match=if_match,
                validate=validate,
                door=door,
                actor=actor,
                apply_id=apply_id,
            )


_store_conforms: type[IWriteRunnerSettings] = RunnerSettingsStore


def _change(row: Any) -> SettingsChange:
    def entry(value: dict[str, Any] | None) -> Entry | None:
        return Entry(**value) if value is not None else None

    fields: list[FieldChange] = []
    for field in json.loads(row.diff):
        new = entry(field["new"])
        assert new is not None
        fields.append(FieldChange(field["key"], entry(field["old"]), new))
    return SettingsChange(
        row.id, row.recorded_at, row.revision, row.op, row.door, row.actor, row.apply_id, tuple(fields)
    )
