"""The workspace and subscription record adapters: each write's row, fact and change in one transaction, a write
decided against a moved revision refused, and state read from the newest fact."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.settings_record.refusals import SettingsRevisionConflict
from blizzard.runner.host_records.model import HostRecord, RecordActor, RecordExists, RecordKind
from blizzard.runner.store import schema
from blizzard.runner.store.internal.host_record_store import HostSubscriptionStore, HostWorkspaceStore
from blizzard.runner.store.internal.host_settings_store import HostSettingsStore
from tests.support.runner_fakes import make_store

_AT = datetime(2026, 11, 20, tzinfo=UTC)
_BY = RecordActor(actor="ada", door="api", at=_AT)


def _stores(tmp_path: Path) -> tuple[HostWorkspaceStore, HostSubscriptionStore, HostSettingsStore, sa.Engine]:
    store = make_store(f"sqlite:///{tmp_path / 'host.db'}")
    clock = FixedClock(_AT)
    return (
        HostWorkspaceStore(store.connections, clock),
        HostSubscriptionStore(store.connections, clock),
        HostSettingsStore(store.connections, clock),
        store.engine,
    )


def test_a_workspace_lives_through_create_edit_retire_and_enable(tmp_path: Path) -> None:
    workspaces, _, settings, _ = _stores(tmp_path)
    workspaces.apply(HostRecord.created(RecordKind.WORKSPACE, "ws-a", {"root": "/srv", "provider": "basic"}), _BY)
    created = workspaces.record("ws-a")
    assert created == HostRecord(RecordKind.WORKSPACE, "ws-a", 1, {"root": "/srv", "provider": "basic"})
    assert created is not None

    edited = created.edit({"max_environments": 2}, if_match=1)
    assert edited is not None
    workspaces.apply(edited, _BY)
    retired = edited.record.retire()
    assert retired is not None
    workspaces.apply(retired, _BY)

    assert workspaces.records() == ()
    assert workspaces.records(retired=True) == (retired.record,)
    enabled = retired.record.enable()
    assert enabled is not None
    workspaces.apply(enabled, _BY)
    assert workspaces.records() == (enabled.record,)
    assert enabled.record.revision == 4

    changes = settings.changes(limit=10, record_kind="workspace", record_key="ws-a")
    assert [(change.revision, change.op, change.door, change.actor) for change in changes] == [
        (4, "enable", "api", "ada"),
        (3, "retire", "api", "ada"),
        (2, "edit", "api", "ada"),
        (1, "create", "api", "ada"),
    ]
    assert [field.key for field in changes[2].diff] == ["max_environments"]
    # The host's own record is untouched by a workspace's writes.
    assert settings.revision() == 0
    assert settings.changes(limit=10, record_kind="host") == ()


def test_a_write_decided_against_a_moved_revision_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    workspaces, _, settings, _ = _stores(tmp_path)
    workspaces.apply(HostRecord.created(RecordKind.WORKSPACE, "ws-a", {"root": "/srv"}), _BY)
    held = workspaces.record("ws-a")
    assert held is not None
    first, second = held.edit({"root": "/a"}, if_match=1), held.edit({"root": "/b"}, if_match=1)
    assert first is not None and second is not None
    workspaces.apply(first, _BY)

    with pytest.raises(SettingsRevisionConflict) as raised:
        workspaces.apply(second, _BY)

    assert (raised.value.current, raised.value.presented) == (2, 1)
    assert workspaces.record("ws-a") == first.record
    assert len(settings.changes(limit=10, record_kind="workspace")) == 2


def test_a_create_of_a_held_key_is_refused(tmp_path: Path) -> None:
    workspaces, _, _, _ = _stores(tmp_path)
    write = HostRecord.created(RecordKind.WORKSPACE, "ws-a", {"root": "/srv"})
    workspaces.apply(write, _BY)

    with pytest.raises(RecordExists):
        workspaces.apply(write, _BY)


def test_a_subscriptions_provider_is_its_own_column_and_reads_back_among_its_settings(tmp_path: Path) -> None:
    _, subscriptions, settings, engine = _stores(tmp_path)
    settings_in = {"provider": "anthropic", "name": "Team", "sample_interval_seconds": 60}
    subscriptions.apply(HostRecord.created(RecordKind.SUBSCRIPTION, "sub-a", settings_in), _BY)

    with engine.connect() as conn:
        row = conn.execute(sa.select(schema.host_subscriptions)).one()
    assert row.provider == "anthropic"
    assert json.loads(row.settings) == {"name": "Team", "sample_interval_seconds": 60}
    assert subscriptions.record("sub-a") == HostRecord(RecordKind.SUBSCRIPTION, "sub-a", 1, settings_in)
    assert [change.record_kind for change in settings.changes(limit=5)] == ["subscription"]


def test_the_kinds_are_kept_apart(tmp_path: Path) -> None:
    workspaces, subscriptions, _, _ = _stores(tmp_path)
    workspaces.apply(HostRecord.created(RecordKind.WORKSPACE, "shared", {"root": "/srv"}), _BY)

    assert subscriptions.record("shared") is None
    assert subscriptions.records(retired=True) == ()
