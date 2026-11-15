"""Startup seeds only a retained identity and resolves through the host parser."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.vocabulary import Change, Entry
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.internal.settings_store import RunnerSettingsStore
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import make_store


def test_seed_requires_identity_and_preserves_unset_after_file_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner="agents = 3\n")
    host = load_host_config(tmp_path)
    entry = host.runners[0]
    assert entry.own is not None
    base = make_store(url, TEST_RUNNER_ID)
    ready, halted = seed_runners(host, [entry])
    assert not ready and "re-run `blizzard runner init" in halted[0].errors[0]
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, entry.name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host, [entry])
    assert not halted and ready[0].config.agents == 3
    settings = RunnerSettingsStore(base.connections, SystemClock())
    snapshot = settings.snapshot()
    assert snapshot.entries["agents"] == Entry(3)
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner='agents = "invalid"\n')
    edited = load_host_config(tmp_path)
    ready, halted = seed_runners(edited, list(edited.runners))
    assert not halted and ready[0].config.agents == 3
    settings.write(
        Change("edit", unsets=frozenset({"agents"})),
        if_match=snapshot.revision,
        validate=lambda _: {},
        door="cli",
        actor="test",
    )
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner='agents = "invalid"\n')
    edited = load_host_config(tmp_path)
    ready, halted = seed_runners(edited, list(edited.runners))
    assert not halted and ready[0].config.agents == 0
    assert settings.snapshot().entries["agents"] == Entry(None, at_default=True)


def test_invalid_initial_seed_and_boot_binding_are_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner='agents = "invalid"\n')
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host, list(host.runners))
    assert not ready and "agents" in halted[0].errors[0]
    assert not RunnerSettingsStore(base.connections, SystemClock()).snapshot().entries

    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner="agents = 3\n")
    host = load_host_config(tmp_path)
    assert seed_runners(host, list(host.runners))[0]
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner='agents = "invalid"\nsubscription = "missing"\n')
    host = load_host_config(tmp_path)
    ready, halted = seed_runners(host, list(host.runners))
    assert not ready and "subscription" in halted[0].errors[0]


def test_seed_validates_related_authored_settings_as_one_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(
        tmp_path, top=f'db_url = "{url}"', runner="[runner.throttle.budget]\nusd = 5\nwindow_hours = 12\n"
    )
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host, list(host.runners))
    assert not halted
    assert ready[0].config.throttle is not None
    assert ready[0].config.throttle.budget is not None
    assert ready[0].config.throttle.budget.usd == 5
    assert ready[0].config.throttle.budget.window_hours == 12
