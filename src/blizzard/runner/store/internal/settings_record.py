"""The runner's private settings SQL: revision, entries, bump, entry write and change row."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict

from sqlalchemy import Connection

from blizzard.foundation.clock import IClock
from blizzard.foundation.settings_record.vocabulary import Door, Entry, FieldChange, Op
from blizzard.runner.store.schema import runner_setting_changes, runner_setting_entries, runner_settings
from blizzard.runner.store.scoped import RunnerStoreConnections


class RunnerSettingsRecord:
    """Statements participate in the supplied transaction and are confined to its runner."""

    def __init__(self, store: RunnerStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def revision(self, conn: Connection) -> int:
        value = conn.execute(self._store.select(runner_settings, runner_settings.c.revision)).scalar_one_or_none()
        return int(value) if value is not None else 0

    def entries(self, conn: Connection) -> Mapping[str, Entry]:
        rows = conn.execute(self._store.select(runner_setting_entries))
        return {
            row.key: Entry(json.loads(row.value) if row.value is not None else None, row.at_default) for row in rows
        }

    def bump_revision(self, conn: Connection) -> int:
        current = self.revision(conn)
        if current == 0:
            conn.execute(self._store.insert(runner_settings).values(revision=1, updated_at=self._clock.now()))
        else:
            conn.execute(self._store.update(runner_settings).values(revision=current + 1, updated_at=self._clock.now()))
        return current + 1

    def write_entries(self, conn: Connection, moved: Mapping[str, Entry], *, revision: int) -> None:
        for key, entry in moved.items():
            values = {
                "value": None if entry.at_default else json.dumps(entry.value),
                "at_default": entry.at_default,
                "revision": revision,
            }
            updated = conn.execute(
                self._store.update(runner_setting_entries).where(runner_setting_entries.c.key == key).values(**values)
            ).rowcount
            if not updated:
                conn.execute(self._store.insert(runner_setting_entries).values(key=key, **values))

    def append_change(
        self,
        conn: Connection,
        *,
        diff: Sequence[FieldChange],
        op: Op,
        door: Door,
        actor: str,
        apply_id: str | None,
        revision: int,
    ) -> None:
        conn.execute(
            self._store.insert(runner_setting_changes).values(
                recorded_at=self._clock.now(),
                revision=revision,
                op=op,
                door=door,
                actor=actor,
                apply_id=apply_id,
                diff=json.dumps([asdict(field) for field in diff]),
            )
        )
