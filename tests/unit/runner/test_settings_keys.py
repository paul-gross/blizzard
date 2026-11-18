"""Runner registry paths map to record-relative whole settings values."""

from pathlib import Path

from blizzard.foundation.config_keys import Lifetime
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.settings.keys import runner_keys
from tests.support.runner_settings import SnapshotSettingsStore


def test_runner_keys_preserve_whole_arrays_and_exclude_boot_and_aliases() -> None:
    keys = runner_keys(HOST_REGISTRY)
    assert keys["agents"] == Lifetime.PASS
    assert keys["runner_prompt"] == Lifetime.SESSION
    assert keys["projects"] == Lifetime.PASS
    assert keys["throttle.pace.taper"] == Lifetime.PASS
    assert not any("[]" in key for key in keys)
    assert "id" not in keys
    assert "projects.agents" not in keys


def test_loader_retains_only_authored_runner_values(tmp_path: Path) -> None:
    from tests.support.config_inputs import load_host_config
    from tests.support.host_files import write_minimal_host

    write_minimal_host(tmp_path, runner='[runner.worker]\nshell = "/bin/sh"\n')
    (runner,) = load_host_config(tmp_path).runners
    assert runner.authored is not None
    assert runner.authored.states("worker.shell")
    assert runner.authored.value("worker.shell") == "/bin/sh"
    assert not runner.authored.states("worker.env_passthrough")


def test_reader_resolves_stored_entries_and_defaults(tmp_path: Path) -> None:
    from blizzard.foundation.settings_record.vocabulary import Entry
    from blizzard.runner.settings.ports import SettingsSnapshot
    from blizzard.runner.settings_reader import RunnerSettingsReader
    from tests.support.config_inputs import load_host_config
    from tests.support.host_files import write_minimal_host

    write_minimal_host(tmp_path, runner='[runner.worker]\nshell = "/bin/sh"\n')
    host = load_host_config(tmp_path)
    (entry,) = host.runners
    assert entry.authored is not None
    keys = runner_keys(HOST_REGISTRY)
    entries = {key: Entry(entry.authored.value(key)) for key in keys if entry.authored.states(key)}

    class Store(SnapshotSettingsStore):
        def snapshot(self) -> SettingsSnapshot:
            return SettingsSnapshot(1, entries)

    reader = RunnerSettingsReader(host, entry, Store())
    assert not reader.validate(entries)
    assert reader.snapshot()[1].worker_shell == "/bin/sh"
    entries["worker.shell"] = Entry(None, at_default=True)
    assert reader.snapshot()[1].worker_shell != "/bin/sh"
    entries["agents"] = Entry(-1)
    assert reader.validate(entries)
    entries["id"] = Entry("other")
    assert "id" in reader.validate(entries)


def test_unset_last_nested_setting_removes_its_empty_container(tmp_path: Path) -> None:
    from blizzard.foundation.settings_record.vocabulary import Entry
    from blizzard.runner.settings.ports import SettingsSnapshot
    from blizzard.runner.settings_reader import RunnerSettingsReader
    from tests.support.config_inputs import load_host_config
    from tests.support.host_files import write_minimal_host

    write_minimal_host(tmp_path, runner="[runner.throttle.budget]\nusd = 5\n")
    host = load_host_config(tmp_path)

    class Store(SnapshotSettingsStore):
        def snapshot(self) -> SettingsSnapshot:
            return SettingsSnapshot(1, {"projects": Entry([]), "throttle.budget.usd": Entry(None, at_default=True)})

    reader = RunnerSettingsReader(host, host.runners[0], Store())
    assert not reader.validate(Store().snapshot().entries)
    assert reader.snapshot()[1].throttle is None


def test_validation_attributes_parser_errors_to_whole_setting_paths(tmp_path: Path) -> None:
    from blizzard.foundation.settings_record.vocabulary import Entry
    from blizzard.runner.settings.ports import SettingsSnapshot
    from blizzard.runner.settings_reader import RunnerSettingsReader
    from tests.support.config_inputs import load_host_config
    from tests.support.host_files import write_minimal_host

    write_minimal_host(tmp_path)
    host = load_host_config(tmp_path)

    class Store(SnapshotSettingsStore):
        def snapshot(self) -> SettingsSnapshot:
            return SettingsSnapshot(0, {})

    reader = RunnerSettingsReader(host, host.runners[0], Store())
    errors = reader.validate({"agents": Entry(-1), "projects": Entry("bad")})
    assert set(errors) == {"agents", "projects"}
    errors = reader.validate({"projects": Entry([]), "throttle.pace.taper": Entry([{"unexpected": "value"}])})
    assert set(errors) == {"throttle.pace.taper"}
    errors = reader.validate({"projects": Entry([]), "harness.autonomy": Entry("invalid")})
    assert set(errors) == {"harness.autonomy"}
    errors = reader.validate(
        {"projects": Entry([]), "claude_code.enabled": Entry(False), "opencode.enabled": Entry(False)}
    )
    assert set(errors) == {"claude_code.enabled", "opencode.enabled"}
    assert reader._error_keys("unrecognized parser diagnostic", {"agents": Entry(1)}) == {"agents"}  # pyright: ignore[reportPrivateUsage]


def test_host_keys_are_the_top_level_pass_and_session_keys_each_whole() -> None:
    from blizzard.runner.settings.keys import host_keys

    assert host_keys(HOST_REGISTRY) == {
        "tick_seconds": Lifetime.PASS,
        "tracing.sweep_seconds": Lifetime.PASS,
        "tracing.settle_seconds": Lifetime.PASS,
        "tracing.batch_limit": Lifetime.PASS,
        "tracing.max_lag_seconds": Lifetime.PASS,
        "tracing.replay_max_window": Lifetime.PASS,
        "tracing.worker_programs": Lifetime.SESSION,
        "tracing.worker_program_services": Lifetime.SESSION,
        "tracing.harness_telemetry": Lifetime.SESSION,
        "transcripts.ship": Lifetime.PASS,
        "transcripts.record_max_bytes": Lifetime.PASS,
        "transcripts.chunk_max_bytes": Lifetime.PASS,
        "worker_stdout.retention_days": Lifetime.PASS,
        "tools.path_prepend": Lifetime.SESSION,
        "tools.env_passthrough": Lifetime.SESSION,
    }
