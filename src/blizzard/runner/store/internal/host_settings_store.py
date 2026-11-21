"""Transactional host settings adapter (package-private).

Host-scoped: its statements carry no runner equality, and it names no runner-scoped table."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Connection, and_, select

from blizzard.foundation.clock import IClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.settings_record.decision import decide, seed_choice
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.foundation.settings_record.validator import Validate
from blizzard.foundation.settings_record.vocabulary import SEED, Change, Door, Entry, FieldChange
from blizzard.runner.settings.ports import HostSettingsChange, IWriteHostSettings, SettingsSnapshot
from blizzard.runner.store.internal.host_settings_record import HOST_RECORD_KIND, HostSettingsRecord
from blizzard.runner.store.schema import host_setting_changes, host_setting_entries
from blizzard.runner.store.scoped import RunnerStoreConnections


class HostSettingsStore:
    def __init__(self, store: RunnerStoreConnections, clock: IClock) -> None:
        self._store = store
        self._record = HostSettingsRecord(clock)

    def snapshot(self) -> SettingsSnapshot:
        with self._store.read_transaction() as conn:
            return SettingsSnapshot(self._record.revision(conn), self._record.entries(conn))

    def revision(self) -> int:
        with self._store.read_transaction() as conn:
            return self._record.revision(conn)

    def changes(
        self,
        *,
        limit: int,
        before: int | None = None,
        record_kind: str | None = None,
        record_key: str | None = None,
    ) -> tuple[HostSettingsChange, ...]:
        stmt = select(host_setting_changes).order_by(host_setting_changes.c.id.desc()).limit(limit)
        if before is not None:
            stmt = stmt.where(host_setting_changes.c.id < before)
        if record_kind is not None:
            stmt = stmt.where(host_setting_changes.c.record_kind == record_kind)
        if record_key is not None:
            stmt = stmt.where(host_setting_changes.c.record_key == record_key)
        with self._store.read_transaction() as conn:
            return tuple(_change(row) for row in conn.execute(stmt))

    def snapshot_with_provenance(self) -> tuple[SettingsSnapshot, Mapping[str, HostSettingsChange]]:
        with self._store.read_transaction() as conn:
            snapshot = SettingsSnapshot(self._record.revision(conn), self._record.entries(conn))
            # Each entry carries the revision that last moved it, so no history scan finds its newest change.
            joined = host_setting_entries.join(
                host_setting_changes,
                and_(
                    host_setting_entries.c.revision == host_setting_changes.c.revision,
                    host_setting_changes.c.record_kind == HOST_RECORD_KIND,
                ),
            )
            stmt = select(host_setting_entries.c.key, *host_setting_changes.c).select_from(joined)
            return snapshot, {row.key: _change(row) for row in conn.execute(stmt)}

    def _apply(
        self,
        conn: Connection,
        change: Change,
        *,
        if_match: int | None,
        validate: Validate,
        door: Door,
        actor: str,
        apply_id: str | None,
    ) -> tuple[FieldChange, ...]:
        """Decide, validate the whole candidate, then write only a moved diff, on the locked connection."""
        decision = decide(self._record.revision(conn), self._record.entries(conn), change, if_match=if_match)
        errors = validate(decision.candidate)
        if errors:
            raise SettingsInvalid(errors)
        if not decision.diff:
            return ()
        revision = self._record.bump_revision(conn)
        self._record.write_entries(conn, {field.key: field.new for field in decision.diff}, revision=revision)
        self._record.append_change(
            conn,
            diff=decision.diff,
            op=change.op,
            door=door,
            actor=actor,
            apply_id=apply_id,
            revision=revision,
            record_kind=HOST_RECORD_KIND,
            record_key=None,
        )
        return decision.diff

    def seed(self, source: ConfigView, keys: Sequence[str], *, validate: Validate) -> None:
        with self._store.begin() as conn:
            self._record.lock(conn)
            # Validate the complete resulting seed, not transient prefixes: related keys can be valid only together.
            existing = self._record.entries(conn)
            pending = {key: Entry(value) for key, value in seed_choice(existing, keys, source).items()}

            def validate_seed(entries: Mapping[str, Entry]) -> Mapping[str, str]:
                return validate({**pending, **entries})

            for key in keys:
                chosen = seed_choice(self._record.entries(conn), (key,), source)
                if chosen:
                    self._apply(
                        conn,
                        Change("seed", sets=chosen),
                        if_match=self._record.revision(conn),
                        validate=validate_seed,
                        door=SEED,
                        actor=SEED,
                        apply_id=None,
                    )

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
            self._record.lock(conn)
            return self._apply(
                conn, change, if_match=if_match, validate=validate, door=door, actor=actor, apply_id=apply_id
            )


_store_conforms: type[IWriteHostSettings] = HostSettingsStore


def _change(row: Any) -> HostSettingsChange:
    def entry(value: dict[str, Any] | None) -> Entry | None:
        return Entry(**value) if value is not None else None

    fields: list[FieldChange] = []
    for field in json.loads(row.diff):
        new = entry(field["new"])
        assert new is not None
        fields.append(FieldChange(field["key"], entry(field["old"]), new))
    return HostSettingsChange(
        row.id,
        row.recorded_at,
        row.revision,
        row.op,
        row.door,
        row.actor,
        row.apply_id,
        tuple(fields),
        row.record_kind,
        row.record_key,
    )
