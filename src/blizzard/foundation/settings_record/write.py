"""The one settings write and seed rule, over any daemon's settings record."""

from __future__ import annotations

from sqlalchemy import Connection

from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.settings_record.decision import Validate, decide
from blizzard.foundation.settings_record.record import ISettingsRecord
from blizzard.foundation.settings_record.vocabulary import SEED, Change, Door, FieldChange


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
    """Apply a revision-conditioned, validated change in the caller's transaction."""
    current = record.revision(conn)
    # Check the revision before reading entries, preserving refusal precedence.
    if if_match is None or if_match != current:
        from blizzard.foundation.settings_record.refusals import SettingsRevisionConflict

        raise SettingsRevisionConflict(current, if_match)
    diff = decide(current, record.entries(conn), change, if_match=if_match, validate=validate)
    if not diff:
        return ()
    revision = record.bump_revision(conn)
    record.write_entries(conn, {field.key: field.new for field in diff}, revision=revision)
    record.append_change(conn, diff=diff, op=change.op, door=door, actor=actor, apply_id=apply_id, revision=revision)
    return diff


def seed_entry(conn: Connection, record: ISettingsRecord, key: str, file: ConfigView, *, validate: Validate) -> None:
    """Seed a stated key only when no entry, including a tombstone, exists."""
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
