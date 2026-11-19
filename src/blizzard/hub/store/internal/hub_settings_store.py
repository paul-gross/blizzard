"""Global settings SQL adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from types import MappingProxyType
from typing import cast

from sqlalchemy import Connection, delete, insert, select, true, update

from blizzard.foundation.clock import IClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.settings_record.decision import decide, seed_choice
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.validator import Validate
from blizzard.foundation.settings_record.vocabulary import SEED, Change, Door, Entry, FieldChange, Op
from blizzard.hub.settings.model import HubSettingsChangeEntry, HubSettingsSnapshot
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


def _apply(
    conn: Connection,
    record: _Record,
    validate: Validate,
    change: Change,
    *,
    if_match: int | None,
    door: Door,
    actor: str,
    apply_id: str | None,
) -> tuple[FieldChange, ...]:
    """Decide, validate the whole candidate, then write only a moved diff, on the caller's locked connection."""
    revision = record.revision(conn)
    decision = decide(revision, record.entries(conn), change, if_match=if_match)
    errors = validate(decision.candidate)
    if errors:
        raise SettingsInvalid(errors)
    if not decision.diff:
        return ()
    moved = record.bump_revision(conn)
    record.write_entries(conn, {field.key: field.new for field in decision.diff}, revision=moved)
    record.append_change(
        conn, diff=decision.diff, op=change.op, door=door, actor=actor, apply_id=apply_id, revision=moved
    )
    return decision.diff


def _lock(conn: Connection) -> None:
    """Take the singleton row's write lock before reading the revision and entries."""
    conn.execute(update(hub_settings).where(hub_settings.c.id == 1).values(revision=hub_settings.c.revision))


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
        with self._store.read("hub_settings_seed_validation") as conn:
            record = _Record(self._clock)
            revision = record.revision(conn)
            existing = record.entries(conn)
            change = Change("seed", sets=seed_choice(existing, keys, file))
            errors = self._validate(decide(revision, existing, change, if_match=revision).candidate)
            if errors:
                raise SettingsInvalid(errors)
        for key in keys:
            if not file.states(key):
                continue
            with self._store.write("hub_settings_seed") as conn:
                _lock(conn)
                record = _Record(self._clock)
                sets = seed_choice(record.entries(conn), (key,), file)
                if sets:
                    _apply(
                        conn,
                        record,
                        self._validate,
                        Change("seed", sets=sets),
                        if_match=record.revision(conn),
                        door=SEED,
                        actor=SEED,
                        apply_id=None,
                    )

    def apply(
        self, change: Change, *, if_match: int | None, door: Door, actor: str, apply_id: str | None = None
    ) -> tuple[FieldChange, ...]:
        with self._store.write("hub_settings_apply") as conn:
            _lock(conn)
            return _apply(
                conn,
                _Record(self._clock),
                self._validate,
                change,
                if_match=if_match,
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
                _lock(conn)
                record = _Record(self._clock)
                diff = _apply(
                    conn,
                    record,
                    self._validate,
                    change,
                    if_match=record.revision(conn),
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

    def changes(self, limit: int, before: int | None) -> tuple[list[HubSettingsChangeEntry], int | None]:
        statement = select(hub_setting_changes).order_by(hub_setting_changes.c.revision.desc()).limit(limit + 1)
        if before is not None:
            statement = statement.where(hub_setting_changes.c.revision < before)
        with self._store.read("hub_settings_changes") as conn:
            rows = conn.execute(statement).all()
        page = rows[:limit]
        result = [
            HubSettingsChangeEntry(
                revision=r.revision,
                recorded_at=r.recorded_at,
                actor=r.actor,
                door=r.door,
                op=r.op,
                apply_id=r.apply_id,
                diff=tuple(_field_change(item) for item in json.loads(r.diff_json)),
            )
            for r in page
        ]
        return result, page[-1].revision if len(rows) > limit else None


def _stored_entry(raw: Mapping[str, object] | None) -> Entry | None:
    return None if raw is None else Entry(freeze(raw["value"]), bool(raw["at_default"]))


def _field_change(item: Mapping[str, object]) -> FieldChange:
    """Read one diff item back the way ``_Record.append_change`` wrote it."""
    new = _stored_entry(cast(Mapping[str, object] | None, item["new"]))
    assert new is not None
    return FieldChange(str(item["key"]), _stored_entry(cast(Mapping[str, object] | None, item["old"])), new)
