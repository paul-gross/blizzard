"""The host record resolves over the host file held since boot, and a write is refused as a boot refuses the file."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.refusals import SettingsInvalid, SettingsRevisionConflict
from blizzard.runner.config import DEFAULT_TRANSCRIPTS_SHIP
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_settings_reader import HostSettingsReader
from blizzard.runner.settings.authoring import HostSettingsAuthoring
from blizzard.runner.settings.policy import SettingRefusal
from blizzard.runner.store.internal.host_settings_store import HostSettingsStore
from tests.support.config_inputs import load_host_config
from tests.support.host_files import write_minimal_host
from tests.support.runner_fakes import make_store


def _authoring(tmp_path: Path, top: str = "") -> tuple[HostSettingsAuthoring, HostSettingsReader, HostSettingsStore]:
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"\n{top}')
    store = HostSettingsStore(make_store(url).connections, SystemClock())
    reader = HostSettingsReader(load_host_config(tmp_path), store)
    return HostSettingsAuthoring(reader, store, HOST_REGISTRY), reader, store


def test_entries_lay_over_the_file_and_keep_its_boot_keys(tmp_path: Path) -> None:
    authoring, reader, store = _authoring(tmp_path, "port = 9123\ntick_seconds = 2\n[tracing]\nsettle_seconds = 7")
    authoring.edit({"tick_seconds": 5, "tracing.sweep_seconds": 11}, frozenset(), if_match=0, door="cli", actor="test")

    snapshot, effective = reader.snapshot()

    assert snapshot.revision == 1
    assert (effective.port, effective.tick_seconds) == (9123, 5.0)
    assert (effective.tracing.sweep_seconds, effective.tracing.settle_seconds) == (11, 7)
    assert reader.resolve(snapshot) is effective
    assert store.changes(limit=5)[0].record_kind == "host"


def test_an_unset_resolves_to_the_default_over_a_file_stating_the_key(tmp_path: Path) -> None:
    authoring, reader, _ = _authoring(tmp_path, "[transcripts]\nship = true")
    authoring.edit({}, frozenset({"transcripts.ship"}), if_match=0, door="cli", actor="test")

    assert reader.current().transcripts.ship is DEFAULT_TRANSCRIPTS_SHIP is False


def test_a_host_built_in_code_resolves_to_itself(tmp_path: Path) -> None:
    store = HostSettingsStore(make_store(f"sqlite:///{tmp_path / 'runner.db'}").connections, SystemClock())
    host = HostConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", tick_seconds=3.0)

    assert HostSettingsReader(host, store).current() is host


def test_an_invalid_value_is_refused_with_every_failing_key_and_nothing_committed(tmp_path: Path) -> None:
    authoring, _, store = _authoring(tmp_path)

    with pytest.raises(SettingsInvalid) as invalid:
        authoring.edit(
            {"tick_seconds": -1, "transcripts.record_max_bytes": "many"},
            frozenset(),
            if_match=0,
            door="cli",
            actor="test",
        )

    assert set(invalid.value.errors) == {"tick_seconds", "transcripts.record_max_bytes"}
    assert "positive number of seconds" in invalid.value.errors["tick_seconds"]
    assert store.snapshot().revision == 0


def test_a_stale_revision_is_refused(tmp_path: Path) -> None:
    authoring, _, store = _authoring(tmp_path)
    authoring.edit({"tick_seconds": 5}, frozenset(), if_match=0, door="cli", actor="test")

    with pytest.raises(SettingsRevisionConflict) as conflict:
        authoring.edit({"tick_seconds": 6}, frozenset(), if_match=0, door="cli", actor="test")

    assert (conflict.value.current, conflict.value.presented) == (1, 0)
    assert store.snapshot().entries["tick_seconds"].value == 5


def test_a_key_both_set_and_unset_is_refused(tmp_path: Path) -> None:
    authoring, _, _ = _authoring(tmp_path)

    with pytest.raises(SettingsInvalid) as invalid:
        authoring.edit({"tick_seconds": 5}, frozenset({"tick_seconds"}), if_match=0, door="cli", actor="test")

    assert set(invalid.value.errors) == {"tick_seconds"}


@pytest.mark.parametrize(
    ("key", "code"),
    [("port", "setting_not_live"), ("tracing.platform", "setting_not_live"), ("agents", "setting_unknown")],
)
def test_a_key_the_host_record_does_not_hold_is_refused(tmp_path: Path, key: str, code: str) -> None:
    authoring, _, store = _authoring(tmp_path)

    with pytest.raises(SettingRefusal) as refused:
        authoring.edit({key: 1}, frozenset(), if_match=0, door="cli", actor="test")

    assert refused.value.code == code
    assert store.snapshot().revision == 0


def test_a_write_moving_nothing_advances_nothing(tmp_path: Path) -> None:
    authoring, _, store = _authoring(tmp_path)
    authoring.edit({"tick_seconds": 5}, frozenset(), if_match=0, door="cli", actor="test")

    assert authoring.edit({"tick_seconds": 5}, frozenset(), if_match=1, door="cli", actor="test") == ()
    assert store.snapshot().revision == 1
    assert len(store.changes(limit=5)) == 1
