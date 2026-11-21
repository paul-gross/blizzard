"""A workspace or subscription record's rules, by value: each write's refusals, its sparse merge and its diff, and a
retire or enable that moves nothing writing nothing."""

from __future__ import annotations

import pytest

from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.foundation.settings_record.vocabulary import Entry, FieldChange
from blizzard.runner.host_records.model import HostRecord, HostRecordsSnapshot, RecordFactOp, RecordKind

_WORKSPACE = HostRecord(
    RecordKind.WORKSPACE, "ws-a", revision=3, settings={"root": "/srv/a", "provider": "basic", "max_environments": 2}
)
_SUBSCRIPTION = HostRecord(
    RecordKind.SUBSCRIPTION, "sub-a", revision=1, settings={"provider": "anthropic", "name": "Team"}
)


def test_a_create_starts_at_revision_one_and_names_every_key_moved_from_absent() -> None:
    write = HostRecord.created(RecordKind.WORKSPACE, " ws-b ", {"root": "/srv/b", "provider": "basic"})

    assert write.record == HostRecord(RecordKind.WORKSPACE, "ws-b", 1, {"root": "/srv/b", "provider": "basic"})
    assert write.creates() and write.prior == 0 and write.op == "create" and write.fact is None
    assert write.diff == (
        FieldChange("provider", None, Entry("basic")),
        FieldChange("root", None, Entry("/srv/b")),
    )
    assert write.record.table() == {"id": "ws-b", "root": "/srv/b", "provider": "basic"}


@pytest.mark.parametrize(
    ("key", "settings", "refused"),
    [
        ("  ", {"root": "/srv"}, "id"),
        ("ws-b", {"id": "ws-b", "root": "/srv"}, "id"),
    ],
)
def test_a_create_refuses_a_blank_key_and_the_key_among_its_settings(
    key: str, settings: dict[str, object], refused: str
) -> None:
    with pytest.raises(SettingsInvalid) as raised:
        HostRecord.created(RecordKind.WORKSPACE, key, settings)

    assert set(raised.value.errors) == {refused}


def test_an_edit_merges_sparsely_bumps_the_revision_and_diffs_only_what_moved() -> None:
    write = _WORKSPACE.edit({"max_environments": 4, "root": "/srv/a"}, frozenset({"provider"}), if_match=3)

    assert write is not None
    assert write.record.revision == 4
    assert write.record.settings == {"root": "/srv/a", "max_environments": 4}
    assert (write.op, write.prior, write.fact) == ("edit", 3, None)
    assert write.diff == (
        FieldChange("max_environments", Entry(2), Entry(4)),
        FieldChange("provider", Entry("basic"), Entry(None, at_default=True)),
    )


def test_an_edit_that_moves_nothing_writes_nothing() -> None:
    assert _WORKSPACE.edit({"root": "/srv/a"}, frozenset({"base_branch"}), if_match=3) is None


@pytest.mark.parametrize("presented", [None, 2, 4])
def test_an_edit_at_a_missing_or_stale_revision_is_refused(presented: int | None) -> None:
    with pytest.raises(SettingsRevisionConflict) as raised:
        _WORKSPACE.edit({"max_environments": 4}, if_match=presented)

    assert (raised.value.current, raised.value.presented) == (3, presented)


@pytest.mark.parametrize(
    ("record", "sets", "unsets", "refused"),
    [
        (_WORKSPACE, {"id": "ws-z"}, frozenset[str](), {"id"}),
        (_SUBSCRIPTION, {"provider": "openai"}, frozenset[str](), {"provider"}),
        (_SUBSCRIPTION, {}, frozenset({"slug", "provider"}), {"slug", "provider"}),
        (_WORKSPACE, {"root": "/x"}, frozenset({"root"}), {"root"}),
    ],
)
def test_an_edit_naming_an_immutable_key_or_both_setting_and_unsetting_one_is_refused(
    record: HostRecord, sets: dict[str, object], unsets: frozenset[str], refused: set[str]
) -> None:
    with pytest.raises(SettingsInvalid) as raised:
        record.edit(sets, unsets, if_match=record.revision)

    assert set(raised.value.errors) == refused


def test_retire_and_enable_bump_the_revision_and_append_their_fact() -> None:
    retired = _WORKSPACE.retire()
    assert retired is not None
    assert (retired.op, retired.fact, retired.diff, retired.record.revision) == ("retire", RecordFactOp.RETIRED, (), 4)
    assert retired.record.retired

    enabled = retired.record.enable()
    assert enabled is not None
    assert (enabled.op, enabled.fact, enabled.record.revision) == ("enable", RecordFactOp.ENABLED, 5)
    assert not enabled.record.retired


def test_retiring_a_retired_record_and_enabling_an_enabled_one_write_nothing() -> None:
    retired = _WORKSPACE.retire()
    assert retired is not None

    assert retired.record.retire() is None
    assert _WORKSPACE.enable() is None


def test_an_edit_of_a_retired_record_is_legal_and_leaves_it_retired() -> None:
    retired = _WORKSPACE.retire()
    assert retired is not None

    write = retired.record.edit({"max_environments": 5}, if_match=4)

    assert write is not None
    assert write.record.retired and write.record.revision == 5


def test_the_snapshots_versions_move_with_any_records_revision() -> None:
    before = HostRecordsSnapshot(workspaces=(_WORKSPACE,), subscriptions=(_SUBSCRIPTION,))
    edited = _WORKSPACE.edit({"max_environments": 9}, if_match=3)
    assert edited is not None

    after = HostRecordsSnapshot(workspaces=(edited.record,), subscriptions=(_SUBSCRIPTION,))

    assert before.versions() == (("workspace:ws-a", 3), ("subscription:sub-a", 1))
    assert after.versions() != before.versions()
    assert HostRecordsSnapshot(subscriptions=(_SUBSCRIPTION,)).versions() != before.versions()


def test_the_snapshots_versions_move_with_a_retired_workspaces_revision() -> None:
    retired = _WORKSPACE.retire()
    assert retired is not None
    edited = retired.record.edit({"max_environments": 9}, if_match=retired.record.revision)
    assert edited is not None

    before = HostRecordsSnapshot(retired_workspaces=(retired.record,))
    after = HostRecordsSnapshot(retired_workspaces=(edited.record,))

    assert after.versions() != before.versions()
