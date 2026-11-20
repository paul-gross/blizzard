"""The host-record service: a write judged as the host file would be before anything is written, a write that moves
nothing never reaching the store, and the seed never touching a held record."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.runner.host_records.model import (
    HostRecord,
    RecordActor,
    RecordExists,
    RecordKind,
    RecordUnknown,
    RecordWrite,
)
from blizzard.runner.host_records.service import HostRecordService

_AT = datetime(2026, 11, 20, tzinfo=UTC)


@dataclass
class _Records:
    held: dict[str, HostRecord] = field(default_factory=dict[str, HostRecord])
    applied: list[tuple[RecordWrite, RecordActor]] = field(default_factory=list[tuple[RecordWrite, RecordActor]])

    def record(self, key: str) -> HostRecord | None:
        return self.held.get(key)

    def records(self, *, retired: bool = False) -> tuple[HostRecord, ...]:
        return tuple(record for record in self.held.values() if retired or not record.retired)

    def apply(self, write: RecordWrite, by: RecordActor) -> None:
        current = self.held.get(write.record.key)
        if write.creates() and current is not None:
            raise RecordExists(write.record.kind, write.record.key)
        if not write.creates() and (current is None or current.revision != write.prior):
            raise SettingsRevisionConflict(current.revision if current else 0, write.prior)
        self.held[write.record.key] = write.record
        self.applied.append((write, by))


@dataclass
class _Validator:
    refusing: Mapping[str, str] = field(default_factory=dict[str, str])
    judged: list[HostRecord] = field(default_factory=list[HostRecord])

    def problems(self, kind: RecordKind, candidate: HostRecord) -> Mapping[str, str]:
        self.judged.append(candidate)
        return self.refusing


def _service(records: _Records, validator: _Validator | None = None) -> HostRecordService:
    return HostRecordService(RecordKind.WORKSPACE, records, validator or _Validator(), FixedClock(_AT))


def test_a_create_is_judged_then_written_under_its_door_and_actor() -> None:
    records, validator = _Records(), _Validator()

    created = _service(records, validator).create("ws-a", {"root": "/srv"}, actor="ada", door="api")

    assert created == HostRecord(RecordKind.WORKSPACE, "ws-a", 1, {"root": "/srv"})
    assert validator.judged == [created]
    assert [(write.op, by) for write, by in records.applied] == [("create", RecordActor("ada", "api", _AT))]


def test_a_create_of_a_held_key_is_refused_retired_or_not() -> None:
    records = _Records({"ws-a": HostRecord(RecordKind.WORKSPACE, "ws-a", 2, retired=True)})

    with pytest.raises(RecordExists):
        _service(records).create("ws-a", {"root": "/srv"}, actor="ada", door="api")

    assert records.applied == []


def test_a_write_the_host_file_would_fail_is_refused_before_anything_is_written() -> None:
    records = _Records({"ws-a": HostRecord(RecordKind.WORKSPACE, "ws-a", 1, {"root": "/srv"})})
    service = _service(records, _Validator({"provider": "provider must be 'winter' or 'basic'"}))

    with pytest.raises(SettingsInvalid) as raised:
        service.edit(service.held("ws-a"), {"provider": "nope"}, if_match=1, actor="ada", door="api")

    assert raised.value.errors == {"provider": "provider must be 'winter' or 'basic'"}
    assert records.applied == []


def test_an_unknown_key_is_refused() -> None:
    with pytest.raises(RecordUnknown):
        _service(_Records()).held("ws-z")


def test_a_second_retire_and_an_enable_of_an_enabled_record_reach_no_store_and_no_validator() -> None:
    records, validator = _Records({"ws-a": HostRecord(RecordKind.WORKSPACE, "ws-a", 1)}), _Validator()
    service = _service(records, validator)

    retired = service.retire(service.held("ws-a"), actor="ada", door="cli")
    again = service.retire(retired, actor="ada", door="cli")
    enabled = service.enable(again, actor="ada", door="cli")
    still = service.enable(enabled, actor="ada", door="cli")

    assert [write.op for write, _ in records.applied] == ["retire", "enable"]
    assert again is retired and still is enabled
    assert enabled.revision == 3 and not enabled.retired
    assert validator.judged == []


def test_an_edit_that_moves_nothing_writes_nothing() -> None:
    held = HostRecord(RecordKind.WORKSPACE, "ws-a", 1, {"root": "/srv"})
    records = _Records({"ws-a": held})

    assert _service(records).edit(held, {"root": "/srv"}, if_match=1, actor="ada", door="api") is held
    assert records.applied == []


def test_an_edit_racing_another_write_is_refused_by_the_store() -> None:
    held = HostRecord(RecordKind.WORKSPACE, "ws-a", 1, {"root": "/srv"})
    records = _Records({"ws-a": replace(held, revision=2)})

    with pytest.raises(SettingsRevisionConflict):
        _service(records).edit(held, {"root": "/x"}, if_match=1, actor="ada", door="api")


def test_the_seed_writes_an_absent_record_whole_and_never_touches_a_held_one() -> None:
    records, validator = _Records({"ws-b": HostRecord(RecordKind.WORKSPACE, "ws-b", 4, {"root": "/b"})}), _Validator()
    service = _service(records, validator)

    assert service.seed("ws-a", {"root": "/a"}) is True
    assert service.seed("ws-b", {"root": "/elsewhere"}) is False

    assert [(write.op, write.record.key, by.door, by.actor) for write, by in records.applied] == [
        ("seed", "ws-a", "seed", "seed")
    ]
    assert records.held["ws-b"].settings == {"root": "/b"}
    assert validator.judged == []
