"""Global settings SQL adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import MappingProxyType

from sqlalchemy import Connection, delete, insert, select, true, update

from blizzard.foundation.clock import IClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.settings_record.refusals import SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange, Op
from blizzard.foundation.settings_record.write import Validate, seed_entry, write
from blizzard.hub.settings.model import HubSettingsSnapshot
from blizzard.hub.settings.values import freeze
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import hub_setting_changes, hub_setting_entries, hub_settings


class _DryRun(Exception):
    def __init__(self, diff: tuple[FieldChange, ...]) -> None:
        self.diff = diff


class _Record:
    def __init__(self, clock: IClock) -> None:
        self._clock = clock
        self._revision: int | None = None

    def revision(self, conn: Connection) -> int:
        revision: int = conn.execute(select(hub_settings.c.revision).where(hub_settings.c.id == 1)).scalar_one()
        self._revision = revision
        return revision

    def entries(self, conn: Connection) -> Mapping[str, Entry]:
        return {
            row.key: Entry(json.loads(row.value_json), row.at_default)
            for row in conn.execute(select(hub_setting_entries))
        }

    def has_entry(self, conn: Connection, key: str) -> bool:
        return (
            conn.execute(select(hub_setting_entries.c.key).where(hub_setting_entries.c.key == key)).first() is not None
        )

    def bump_revision(self, conn: Connection) -> int:
        expected = self._revision
        result = conn.execute(
            update(hub_settings)
            .where(hub_settings.c.id == 1, hub_settings.c.revision == expected)
            .values(revision=hub_settings.c.revision + 1)
        )
        if result.rowcount != 1:
            raise SettingsRevisionConflict(self.revision(conn), expected)
        assert expected is not None
        return expected + 1

    def write_entries(self, conn: Connection, moved: Mapping[str, Entry], *, revision: int) -> None:
        for key, entry in moved.items():
            conn.execute(delete(hub_setting_entries).where(hub_setting_entries.c.key == key))
            conn.execute(
                insert(hub_setting_entries).values(
                    key=key, value_json=json.dumps(entry.value), at_default=entry.at_default, revision=revision
                )
            )

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
        def entry(value: Entry | None) -> object:
            return None if value is None else {"value": value.value, "at_default": value.at_default}

        conn.execute(
            insert(hub_setting_changes).values(
                revision=revision,
                diff_json=json.dumps([{"key": d.key, "old": entry(d.old), "new": entry(d.new)} for d in diff]),
                op=op,
                door=door,
                actor=actor,
                apply_id=apply_id,
                recorded_at=self._clock.now(),
            )
        )


class HubSettingsStore:
    def __init__(self, store: HubStoreConnections, clock: IClock, validate: Validate) -> None:
        self._store, self._clock, self._validate = store, clock, validate

    def new_apply_id(self) -> str:
        return Id.mint(IdPrefix.CONFIG_APPLY, self._clock).value

    def revision(self) -> int:
        with self._store.read("hub_settings_revision") as conn:
            return _Record(self._clock).revision(conn)

    def snapshot(self) -> HubSettingsSnapshot:
        with self._store.read("hub_settings_snapshot") as conn:
            rows = conn.execute(
                select(
                    hub_settings.c.revision,
                    hub_setting_entries.c.key,
                    hub_setting_entries.c.value_json,
                    hub_setting_entries.c.at_default,
                )
                .select_from(hub_settings.outerjoin(hub_setting_entries, true()))
                .where(hub_settings.c.id == 1)
            ).all()
        revision = rows[0].revision
        entries = {
            row.key: Entry(freeze(json.loads(row.value_json)), row.at_default) for row in rows if row.key is not None
        }
        return HubSettingsSnapshot(revision, MappingProxyType(entries))

    def seed(self, keys: Sequence[str], file: ConfigView) -> None:
        for key in keys:
            if not file.states(key):
                continue
            with self._store.write("hub_settings_seed") as conn:
                conn.execute(
                    update(hub_settings).where(hub_settings.c.id == 1).values(revision=hub_settings.c.revision)
                )
                seed_entry(conn, _Record(self._clock), key, file, validate=self._validate)

    def apply(
        self, change: Change, *, if_match: int | None, door: Door, actor: str, apply_id: str | None = None
    ) -> tuple[FieldChange, ...]:
        with self._store.write("hub_settings_apply") as conn:
            # Acquire the singleton's write lock before reading the revision and entries.
            conn.execute(update(hub_settings).where(hub_settings.c.id == 1).values(revision=hub_settings.c.revision))
            return write(
                conn,
                _Record(self._clock),
                change,
                if_match=if_match,
                validate=self._validate,
                door=door,
                actor=actor,
                apply_id=apply_id,
            )

    def apply_document(
        self, change: Change, *, actor: str, apply_id: str | None, dry_run: bool
    ) -> tuple[FieldChange, ...]:
        """Apply against the revision held under the write lock; a preview rolls the transaction back."""
        try:
            with self._store.write("hub_settings_document") as conn:
                conn.execute(
                    update(hub_settings).where(hub_settings.c.id == 1).values(revision=hub_settings.c.revision)
                )
                record = _Record(self._clock)
                diff = write(
                    conn,
                    record,
                    change,
                    if_match=record.revision(conn),
                    validate=self._validate,
                    door="apply",
                    actor=actor,
                    apply_id=apply_id,
                )
                if dry_run:
                    raise _DryRun(diff)
                return diff
        except _DryRun as preview:
            return preview.diff

    def view(self) -> tuple[HubSettingsSnapshot, dict[str, tuple[int, datetime, str]]]:
        """Read values, revision and provenance from the same database statement."""
        with self._store.read("hub_settings_provenance") as conn:
            rows = conn.execute(
                select(
                    hub_settings.c.revision.label("settings_revision"),
                    hub_setting_entries.c.key,
                    hub_setting_entries.c.revision,
                    hub_setting_entries.c.value_json,
                    hub_setting_entries.c.at_default,
                    hub_setting_changes.c.recorded_at,
                    hub_setting_changes.c.actor,
                )
                .select_from(
                    hub_settings.outerjoin(hub_setting_entries, true()).outerjoin(
                        hub_setting_changes, hub_setting_entries.c.revision == hub_setting_changes.c.revision
                    )
                )
                .where(hub_settings.c.id == 1)
            ).all()
        entries = {
            row.key: Entry(freeze(json.loads(row.value_json)), row.at_default) for row in rows if row.key is not None
        }
        provenance = {row.key: (row.revision, row.recorded_at, row.actor) for row in rows if row.key is not None}
        return HubSettingsSnapshot(rows[0].settings_revision, MappingProxyType(entries)), provenance

    def changes(self, limit: int, before: int | None) -> tuple[list[dict[str, object]], int | None]:
        statement = select(hub_setting_changes).order_by(hub_setting_changes.c.revision.desc()).limit(limit + 1)
        if before is not None:
            statement = statement.where(hub_setting_changes.c.revision < before)
        with self._store.read("hub_settings_changes") as conn:
            rows = conn.execute(statement).all()
        page = rows[:limit]
        result = [
            {
                "id": r.revision,
                "revision": r.revision,
                "recorded_at": r.recorded_at.isoformat(),
                "actor": r.actor,
                "door": r.door,
                "op": r.op,
                "apply_id": r.apply_id,
                "diff": json.loads(r.diff_json),
            }
            for r in page
        ]
        return result, page[-1].revision if len(rows) > limit else None
