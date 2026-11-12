"""The one settings write and the seed rule, over any daemon's :class:`ISettingsRecord`."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from sqlalchemy import Connection

from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.settings_record.record import ISettingsRecord
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import SEED, Change, Door, Entry, FieldChange

#: A daemon's own parse of the resolved entries: every failing key's message, empty when all hold.
type Validate = Callable[[Mapping[str, Entry]], Mapping[str, str]]


def write(
    conn: Connection,
    record: ISettingsRecord,
    change: Change,
    *,
    if_match: int | None,
    validate: Validate,
    door: Door,
    actor: str,
    apply_id: str | None = None,
) -> tuple[FieldChange, ...]:
    """Apply ``change`` on the caller's transaction and return what moved.

    Refuses a missing or stale ``if_match``, then a resolved state that fails ``validate``, before writing anything.
    A write that moves nothing bumps no revision and appends no change row."""
    current = record.revision(conn)
    if if_match is None or if_match != current:
        raise SettingsRevisionConflict(current, if_match)
    existing = record.entries(conn)
    resolved = {**existing, **{key: Entry(value) for key, value in change.sets.items()}}
    resolved.update({key: Entry(None, at_default=True) for key in change.unsets})
    errors = validate(resolved)
    if errors:
        raise SettingsInvalid(errors)
    moved = {key: entry for key, entry in resolved.items() if existing.get(key) != entry}
    if not moved:
        return ()
    diff = tuple(FieldChange(key, existing.get(key), entry) for key, entry in moved.items())
    revision = record.bump_revision(conn)
    record.write_entries(conn, moved, revision=revision)
    record.append_change(conn, diff=diff, op=change.op, door=door, actor=actor, apply_id=apply_id, revision=revision)
    return diff


def seed_entry(conn: Connection, record: ISettingsRecord, key: str, file: ConfigView, *, validate: Validate) -> None:
    """Seed ``key`` from the file: an existing entry, at-default included, is left alone; a key the file does not
    state is left unwritten; a stated key's whole value is written under the seed door and actor."""
    if record.has_entry(conn, key) or not file.states(key):
        return
    write(
        conn,
        record,
        Change("seed", sets={key: file.value(key)}),
        if_match=record.revision(conn),
        validate=validate,
        door=SEED,
        actor=SEED,
    )
