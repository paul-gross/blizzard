"""A host's start seeds its record from its file once, and the store's entries stand from then on (unit tier)."""

from __future__ import annotations

from pathlib import Path

import click
import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.cli.runtime import seed_host
from blizzard.runner.config import DEFAULT_TICK_SECONDS
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_settings_reader import HostSettingsReader
from blizzard.runner.settings.authoring import HostSettingsAuthoring
from blizzard.runner.store.internal.host_settings_store import HostSettingsStore
from tests.support.config_inputs import FLAG_REGISTRY, STRICT
from tests.support.host_files import write_minimal_host
from tests.support.runner_fakes import make_store


def _host(tmp_path: Path, top: str = "", *, runner: str = "") -> tuple[HostConfig, HostSettingsStore]:
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"\n{top}', runner=runner)
    store = make_store(url)
    return _load(tmp_path), HostSettingsStore(store.connections, SystemClock())


def _load(root: Path) -> HostConfig:
    return HostConfig.load(root, flag_registry=FLAG_REGISTRY, strict=STRICT, defer_tool_agreement=True)


def _entries(store: HostSettingsStore) -> dict[str, Entry]:
    return dict(store.snapshot().entries)


def test_the_files_tick_seconds_is_seeded_over_the_variable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config, store = _host(tmp_path, "tick_seconds = 2")

    effective = seed_host(config, {"BZ_RUNNER_TICK_SECONDS": "9"})

    assert _entries(store)["tick_seconds"] == Entry(2)
    assert effective.tick_seconds == 2.0
    assert "BZ_RUNNER_TICK_SECONDS is deprecated and ignored" in capsys.readouterr().out


def test_the_variable_is_seeded_where_neither_the_store_nor_the_file_states_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config, store = _host(tmp_path)

    effective = seed_host(config, {"BZ_RUNNER_TICK_SECONDS": "0.5"})

    assert _entries(store)["tick_seconds"] == Entry(0.5)
    assert effective.tick_seconds == 0.5
    assert "seeded as tick_seconds = 0.5" in capsys.readouterr().out
    # A later start finds the store's entry, and ignores the variable rather than seeding it again.
    seed_host(_load(tmp_path), {"BZ_RUNNER_TICK_SECONDS": "7"})
    assert _entries(store)["tick_seconds"] == Entry(0.5)
    assert "ignored: the store holds tick_seconds" in capsys.readouterr().out


def test_the_default_stands_and_nothing_is_written_where_none_states_one(tmp_path: Path) -> None:
    config, store = _host(tmp_path)

    effective = seed_host(config, {})

    assert store.snapshot().revision == 0
    assert "tick_seconds" not in _entries(store)
    assert effective.tick_seconds is None
    assert DEFAULT_TICK_SECONDS == 30.0


def test_the_stores_entry_wins_and_an_edited_seeded_key_in_the_file_changes_nothing(tmp_path: Path) -> None:
    config, store = _host(tmp_path, "tick_seconds = 2\n[transcripts]\nship = false")
    seed_host(config, {})
    revision = store.snapshot().revision

    write_minimal_host(
        tmp_path, top=f'db_url = "{config.db_url}"\ntick_seconds = 3\n[transcripts]\nship = true', runner=""
    )
    effective = seed_host(_load(tmp_path), {"BZ_RUNNER_TICK_SECONDS": "9"})

    assert store.snapshot().revision == revision
    assert effective.tick_seconds == 2.0
    assert effective.transcripts.ship is False


def test_an_unset_survives_a_restart_against_a_file_stating_the_key(tmp_path: Path) -> None:
    config, store = _host(tmp_path, "tick_seconds = 2")
    seed_host(config, {})
    reader = HostSettingsReader(config, store)
    HostSettingsAuthoring(reader, store, HOST_REGISTRY).edit(
        {}, frozenset({"tick_seconds"}), if_match=store.revision(), door="cli", actor="test"
    )

    effective = seed_host(_load(tmp_path), {"BZ_RUNNER_TICK_SECONDS": "9"})

    assert _entries(store)["tick_seconds"] == Entry(None, at_default=True)
    assert effective.tick_seconds is None


def test_the_implicit_tool_profile_is_seeded_when_the_file_has_no_tools(tmp_path: Path) -> None:
    tools = tmp_path / "bin"
    tools.mkdir()
    config, store = _host(
        tmp_path, runner=f'[runner.worker]\npath_prepend = ["{tools}"]\nenv_passthrough = ["SSH_AUTH_SOCK"]'
    )

    effective = seed_host(config, {})

    entries = _entries(store)
    assert entries["tools.path_prepend"] == Entry([str(tools)])
    assert entries["tools.env_passthrough"] == Entry(["SSH_AUTH_SOCK"])
    assert effective.tool_path_prepend == (str(tools),)
    assert effective.tool_env_passthrough == ("SSH_AUTH_SOCK",)


def test_an_empty_implicit_tool_profile_seeds_no_entry(tmp_path: Path) -> None:
    config, store = _host(tmp_path)

    seed_host(config, {})

    assert not any(key.startswith("tools.") for key in _entries(store))


def _two_runners(tmp_path: Path, first: str, second: str) -> None:
    (tmp_path / "blizzard-host.toml").write_text(
        f'db_url = "sqlite:///{tmp_path / "runner.db"}"\n'
        'host_token = "host-token"\n'
        '[[workspace]]\nid = "workspace-local"\n'
        f'[[runner]]\nid = "rn_a"\nname = "a"\nprojects = []\n[runner.worker]\npath_prepend = ["{first}"]\n'
        f'[[runner]]\nid = "rn_b"\nname = "b"\nprojects = []\n[runner.worker]\npath_prepend = ["{second}"]\n'
    )


def test_disagreeing_runners_refuse_a_start_until_the_store_holds_the_hosts_profile(tmp_path: Path) -> None:
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    _two_runners(tmp_path, str(first), str(first))
    store = HostSettingsStore(make_store(f"sqlite:///{tmp_path / 'runner.db'}").connections, SystemClock())
    seed_host(_load(tmp_path), {})
    assert _entries(store)["tools.path_prepend"] == Entry([str(first)])

    # One runner's seeded worker key edited in the file: the host's profile is the store's, so the start goes on.
    _two_runners(tmp_path, str(first), str(second))
    assert _load(tmp_path).tool_profile_conflict is not None
    effective = seed_host(_load(tmp_path), {})
    assert effective.tool_path_prepend == (str(first),)

    # A store holding no profile of the host's still refuses the disagreement, as a boot always has.
    (tmp_path / "runner.db").unlink()
    make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    with pytest.raises(click.ClickException, match=r"\[tools\]"):
        seed_host(_load(tmp_path), {})
