"""The shared settings write and seed rule, against an in-memory record."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

import pytest
from sqlalchemy import Connection

from blizzard.foundation.config_keys import INTEGER, ConfigKey, ConfigRegistry, ConfigView, Lifetime
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Change, Door, Entry, FieldChange, Op
from blizzard.foundation.settings_record.write import Validate, seed_entry, write

_CONN = cast("Connection", None)


class _FakeRecord:
    def __init__(self, entries: Mapping[str, Entry] | None = None) -> None:
        self.stored: dict[str, Entry] = dict(entries or {})
        self.current = 1
        self.changes: list[dict[str, Any]] = []

    def revision(self, conn: Connection) -> int:
        return self.current

    def entries(self, conn: Connection) -> Mapping[str, Entry]:
        return dict(self.stored)

    def has_entry(self, conn: Connection, key: str) -> bool:
        return key in self.stored

    def bump_revision(self, conn: Connection) -> int:
        self.current += 1
        return self.current

    def write_entries(self, conn: Connection, moved: Mapping[str, Entry], *, revision: int) -> None:
        self.stored.update(moved)

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
        self.changes.append({"diff": tuple(diff), "op": op, "door": door, "actor": actor, "revision": revision})


def _ok(entries: Mapping[str, Entry]) -> Mapping[str, str]:
    return {}


def _write(
    record: _FakeRecord, change: Change, *, if_match: int | None = 1, validate: Validate = _ok
) -> tuple[FieldChange, ...]:
    return write(_CONN, record, change, if_match=if_match, validate=validate, door="api", actor="me")


def _bad(entries: Mapping[str, Entry]) -> Mapping[str, str]:
    return {"a": "bad"}


@pytest.mark.parametrize("if_match", [None, 7])
def test_a_missing_or_stale_if_match_is_refused(if_match: int | None) -> None:
    record = _FakeRecord()
    with pytest.raises(SettingsRevisionConflict) as refused:
        _write(record, Change("edit", sets={"a": 1}), if_match=if_match)
    assert (refused.value.current, refused.value.presented) == (1, if_match)
    assert record.stored == {} and record.changes == []


def test_a_refused_validation_writes_and_appends_nothing() -> None:
    record = _FakeRecord()
    with pytest.raises(SettingsInvalid) as refused:
        _write(record, Change("edit", sets={"a": 1}), validate=_bad)
    assert refused.value.errors == {"a": "bad"}
    assert record.stored == {} and record.changes == [] and record.current == 1


def test_validation_sees_the_current_entries_overlaid_with_the_change() -> None:
    record = _FakeRecord({"a": Entry(1)})
    seen: list[Mapping[str, Entry]] = []

    def spy(entries: Mapping[str, Entry]) -> Mapping[str, str]:
        seen.append(entries)
        return {}

    _write(record, Change("edit", sets={"b": 2}), validate=spy)
    assert seen == [{"a": Entry(1), "b": Entry(2)}]


def test_a_write_that_moves_nothing_bumps_no_revision_and_appends_no_change() -> None:
    record = _FakeRecord({"a": Entry(1)})
    assert _write(record, Change("edit", sets={"a": 1})) == ()
    assert record.current == 1 and record.changes == []


def test_a_moving_write_bumps_once_and_appends_one_row_with_its_diff() -> None:
    record = _FakeRecord({"a": Entry(1)})
    diff = _write(record, Change("edit", sets={"a": 2, "b": 3}))
    assert record.current == 2
    assert record.stored == {"a": Entry(2), "b": Entry(3)}
    assert diff == (FieldChange("a", Entry(1), Entry(2)), FieldChange("b", None, Entry(3)))
    assert record.changes == [{"diff": diff, "op": "edit", "door": "api", "actor": "me", "revision": 2}]


def test_an_unset_is_written_at_default_even_with_no_entry() -> None:
    record = _FakeRecord()
    diff = _write(record, Change("edit", unsets=frozenset({"a"})))
    assert record.stored == {"a": Entry(None, at_default=True)}
    assert diff == (FieldChange("a", None, Entry(None, at_default=True)),)


def test_unsetting_an_at_default_entry_moves_nothing() -> None:
    record = _FakeRecord({"a": Entry(None, at_default=True)})
    assert _write(record, Change("edit", unsets=frozenset({"a"}))) == ()


def test_a_key_both_set_and_unset_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="both set and unset"):
        Change("edit", sets={"a": 1}, unsets=frozenset({"a"}))


_REGISTRY = ConfigRegistry(
    "demo", (ConfigKey("a", INTEGER, lifetime=Lifetime.BOOT), ConfigKey("b", INTEGER, lifetime=Lifetime.BOOT))
)


def _file(document: Mapping[str, object]) -> ConfigView:
    return ConfigView.root(_REGISTRY, document, strict=True)


@pytest.mark.parametrize("existing", [Entry(5), Entry(None, at_default=True)])
def test_seed_leaves_an_existing_entry_alone(existing: Entry) -> None:
    record = _FakeRecord({"a": existing})
    seed_entry(_CONN, record, "a", _file({"a": 9}), validate=_ok)
    assert record.stored == {"a": existing} and record.changes == []


def test_seed_leaves_a_key_the_file_does_not_state_unwritten() -> None:
    record = _FakeRecord()
    seed_entry(_CONN, record, "a", _file({"b": 1}), validate=_ok)
    assert record.stored == {} and record.changes == []


def test_seed_writes_a_stated_key_under_the_seed_door_and_actor() -> None:
    record = _FakeRecord()
    seed_entry(_CONN, record, "a", _file({"a": 9}), validate=_ok)
    assert record.stored == {"a": Entry(9)}
    assert [(c["op"], c["door"], c["actor"]) for c in record.changes] == [("seed", "seed", "seed")]
