"""The host's private settings SQL (package-private).

Host-scoped: the host's settings are a fact about this host, never a runner's, so its statements carry no runner
equality."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Final

from sqlalchemy import Connection, insert, select, update

from blizzard.foundation.clock import IClock
from blizzard.foundation.settings_record.vocabulary import Door, Entry, FieldChange, Op
from blizzard.runner.store.schema import host_setting_changes, host_setting_entries, host_settings

#: The kind every change row of the host's own record carries.
HOST_RECORD_KIND: Final = "host"

_ROW = 1


class HostSettingsRecord:
    """Statements participate in the supplied transaction."""

    def __init__(self, clock: IClock) -> None:
        self._clock = clock

    def revision(self, conn: Connection) -> int:
        value = conn.execute(select(host_settings.c.revision).where(host_settings.c.id == _ROW)).scalar_one_or_none()
        return int(value) if value is not None else 0

    def entries(self, conn: Connection) -> Mapping[str, Entry]:
        rows = conn.execute(select(host_setting_entries))
        return {
            row.key: Entry(json.loads(row.value) if row.value is not None else None, row.at_default) for row in rows
        }

    def lock(self, conn: Connection) -> None:
        """Serialize writers on the one row, inserting it at revision 0 before the first write."""
        locked = conn.execute(
            update(host_settings).where(host_settings.c.id == _ROW).values(revision=host_settings.c.revision)
        ).rowcount
        if not locked:
            conn.execute(insert(host_settings).values(id=_ROW, revision=0, updated_at=self._clock.now()))

    def bump_revision(self, conn: Connection) -> int:
        current = self.revision(conn)
        conn.execute(
            update(host_settings)
            .where(host_settings.c.id == _ROW)
            .values(revision=current + 1, updated_at=self._clock.now())
        )
        return current + 1

    def write_entries(self, conn: Connection, moved: Mapping[str, Entry], *, revision: int) -> None:
        for key, entry in moved.items():
            values = {
                "value": None if entry.at_default else json.dumps(entry.value),
                "at_default": entry.at_default,
                "revision": revision,
            }
            updated = conn.execute(
                update(host_setting_entries).where(host_setting_entries.c.key == key).values(**values)
            ).rowcount
            if not updated:
                conn.execute(insert(host_setting_entries).values(key=key, **values))

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
        record_kind: str,
        record_key: str | None,
    ) -> None:
        """Append one change row of the record ``record_kind`` names, keyed within its kind by ``record_key`` —
        ``None`` for the host's own record."""
        conn.execute(
            insert(host_setting_changes).values(
                recorded_at=self._clock.now(),
                revision=revision,
                op=op,
                door=door,
                actor=actor,
                apply_id=apply_id,
                diff=json.dumps([asdict(field) for field in diff]),
                record_kind=record_kind,
                record_key=record_key,
            )
        )
