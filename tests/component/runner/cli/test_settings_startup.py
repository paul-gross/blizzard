"""Startup seeds only a retained identity and resolves through the host parser."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.vocabulary import Change, Entry
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID, ClaudeCodeSection
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.settings_reader import RunnerSettingsReader
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.internal.settings_store import RunnerSettingsStore
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.runner_fakes import FakeHub, make_store


@pytest.mark.parametrize(
    ("legacy", "canonical", "value", "edited"),
    [
        ('harness_binary = "/old/claude"', "claude_code.binary", "/old/claude", "/new/claude"),
        ('harness_permission_mode = "manual"', "harness.autonomy", "manual", "auto"),
    ],
)
def test_legacy_seed_is_durable_and_canonical_edits_override_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: str, canonical: str, value: str, edited: str
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner=legacy + "\n")
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host, list(host.runners))
    assert ready and not halted
    settings = RunnerSettingsStore(base.connections, SystemClock())
    alias = legacy.split(" = ")[0]
    assert settings.snapshot().entries[alias] == Entry(value)
    reader = RunnerSettingsReader(host, host.runners[0], settings)
    settings.write(
        Change("edit", sets={canonical: edited}),
        if_match=settings.snapshot().revision,
        validate=reader.validate,
        door="api",
        actor="test",
    )
    resolved = reader.snapshot()[1]
    section = resolved.harness_sections.of(CLAUDE_CODE_HARNESS_ID)
    assert isinstance(section, ClaudeCodeSection)
    assert section.binary == edited if canonical == "claude_code.binary" else section.permission_mode is None
    settings.write(
        Change("edit", unsets=frozenset({canonical})),
        if_match=settings.snapshot().revision,
        validate=reader.validate,
        door="api",
        actor="test",
    )
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner=legacy.replace(value, "changed") + "\n")
    reloaded = load_host_config(tmp_path)
    ready, halted = seed_runners(reloaded, list(reloaded.runners))
    assert ready and not halted
    assert settings.snapshot().entries[canonical] == Entry(None, at_default=True)
    section = ready[0].config.harness_sections.of(CLAUDE_CODE_HARNESS_ID)
    assert isinstance(section, ClaudeCodeSection)
    assert section.permission_mode is None
    if canonical == "claude_code.binary":
        assert section.binary != value


def test_live_settings_rebuild_and_failed_prompt_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner="agents = 3\n")
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host, list(host.runners))
    assert not halted
    graph = build_host_process(host, ready)
    try:
        runner = graph.runners[0]
        stepper = RunnerStepper(runner.config, process_graph=runner)
        try:
            first = stepper._context()  # pyright: ignore[reportPrivateUsage]
            assert first.config.agents == 3
            settings = runner.stores.settings
            reader = runner.settings_reader
            assert reader is not None
            settings.write(
                Change("edit", sets={"agents": 2}),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )
            second = stepper._context()  # pyright: ignore[reportPrivateUsage]
            assert second.config.agents == 2
            missing = tmp_path / "prompt.txt"
            settings.write(
                Change("edit", sets={"runner_prompt_file": str(missing)}),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )
            failed = settings.snapshot().revision
            assert stepper._context() is second  # pyright: ignore[reportPrivateUsage]
            assert stepper.failed_revision == failed
            missing.write_text("new prompt")
            recovered = stepper._context()  # pyright: ignore[reportPrivateUsage]
            assert recovered.config.runner_prompt == "new prompt"
            assert stepper.failed_revision is None
        finally:
            stepper.close()
    finally:
        graph.close()


def test_snapshot_resolution_failure_reports_revision_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner="agents = 3\n")
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host, list(host.runners))
    assert not halted
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        first = stepper._context()  # pyright: ignore[reportPrivateUsage]
        reader = runner.settings_reader
        assert reader is not None
        settings = runner.stores.settings
        settings.write(
            Change("edit", sets={"agents": 2}),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        revision = settings.snapshot().revision
        dotenv = tmp_path / ".env"
        dotenv.mkdir()
        assert stepper._context() is first  # pyright: ignore[reportPrivateUsage]
        assert stepper.failed_revision == revision
        assert stepper.settings_failure is not None
        assert stepper.settings_failure[0] == revision
        dotenv.rmdir()
        assert stepper._context().config.agents == 2  # pyright: ignore[reportPrivateUsage]
        assert stepper.settings_failure is None
    finally:
        stepper.close()
        graph.close()


def test_registration_keeps_pass_snapshot_across_a_write_and_retries_failed_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner="agents = 3\n")
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host, list(host.runners))
    assert not halted
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    stepper = RunnerStepper(runner.config, process_graph=runner)
    hub = FakeHub(default_runner_id=TEST_RUNNER_ID)
    settings = runner.stores.settings
    reader = runner.settings_reader
    assert reader is not None

    def write(values: dict[str, object]) -> None:
        settings.write(
            Change("edit", sets=values),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )

    def register_twice(ctx: LoopContext) -> None:
        ctx = replace(ctx, hub=hub)
        Registration(ctx).run()
        if len(hub.registered) == 1:
            write({"agents": 1, "name": "renamed"})
            assert stepper.capacity_config(((), 0))[1] == 3
        Registration(ctx).run()

    monkeypatch.setattr(loop_wiring, "tick", register_twice)
    try:
        stepper.timed_tick()
        assert hub.registered_agents == [3, 3]
        assert hub.registered[0][0] == hub.registered[1][0] == host.runners[0].name
        stepper.timed_tick()
        assert hub.registered_agents[-2:] == [1, 1]
        assert stepper.capacity_config(((), 0))[1] == 1
        assert [name for name, _ in hub.registered[-2:]] == ["renamed", "renamed"]

        missing = tmp_path / "prompt.txt"
        write({"runner_prompt_file": str(missing), "agents": 2})
        failed = settings.snapshot().revision
        stepper.timed_tick()
        assert stepper.failed_revision == failed
        assert hub.registered_agents[-2:] == [1, 1]
        assert stepper.capacity_config(((), 0))[1] == 1
        missing.write_text("ready")
        stepper.timed_tick()
        assert stepper.failed_revision is None
        assert hub.registered_agents[-2:] == [2, 2]
        assert stepper.capacity_config(((), 0))[1] == 2
    finally:
        stepper.close()
        graph.close()


def test_stored_launch_settings_reach_replacement_harness_without_mutating_old_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    ready, halted = seed_runners(host, list(host.runners))
    assert not halted
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        before = stepper._context()  # pyright: ignore[reportPrivateUsage]
        old = before.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID).launch_settings()
        reader = runner.settings_reader
        assert reader is not None
        runner.stores.settings.write(
            Change("edit", sets={"claude_code.binary": "/new/claude", "worker.shell": "/bin/sh"}),
            if_match=runner.stores.settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        after = stepper._context()  # pyright: ignore[reportPrivateUsage]
        current = after.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID).launch_settings()
        assert current.binary == "/new/claude"
        assert current.environment.shell == "/bin/sh"
        assert before.harnesses.lifecycle(CLAUDE_CODE_HARNESS_ID).launch_settings() == old
    finally:
        stepper.close()
        graph.close()


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
    assert ready and not halted and ready[0].config.agents == 3
    settings = RunnerSettingsStore(base.connections, SystemClock())
    assert settings.snapshot().entries == {}
    assert RunnerIdentityStore(base.connections).runner_identity() is None
    graph = build_host_process(host, ready)
    try:
        reader = graph.runners[0].settings_reader
        assert reader is not None
        assert reader.snapshot()[1].agents == 3
        assert settings.snapshot().entries == {}
        RunnerIdentityStore(base.connections).record_runner_identity(
            RunnerIdentity(TEST_RUNNER_ID, entry.name, datetime.now(UTC))
        )
        assert reader.snapshot()[1].agents == 3
        assert settings.snapshot().entries["agents"] == Entry(3)
    finally:
        graph.close()
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


def test_stored_worker_profiles_may_diverge_without_changing_host_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_R_ONE", "test-one")
    monkeypatch.setenv("BZ_HUB_TOKEN_R_TWO", "test-two")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    (tmp_path / "blizzard-host.toml").write_text(
        f'db_url = "{url}"\nhost_token = "host-token"\n'
        '[[workspace]]\nid = "ws"\n'
        '[[runner]]\nid = "rn_one"\nname = "r-one"\nprojects = []\n'
        '[runner.worker]\npath_prepend = ["/file"]\nenv_passthrough = ["FILE_VAR"]\n'
        '[[runner]]\nid = "rn_two"\nname = "r-two"\nprojects = []\n'
        '[runner.worker]\npath_prepend = ["/file"]\nenv_passthrough = ["FILE_VAR"]\n'
    )
    host = load_host_config(tmp_path)
    for entry in host.runners:
        assert entry.id is not None
        base = make_store(url, entry.id)
        RunnerIdentityStore(base.connections).record_runner_identity(
            RunnerIdentity(entry.id, entry.name, datetime.now(UTC))
        )
    ready, halted = seed_runners(host, list(host.runners))
    assert len(ready) == 2 and not halted
    for entry, path in zip(host.runners, ("/stored-one", "/stored-two"), strict=True):
        assert entry.id is not None
        settings = RunnerSettingsStore(make_store(url, entry.id).connections, SystemClock())
        settings.write(
            Change("edit", sets={"worker.path_prepend": [path]}),
            if_match=settings.snapshot().revision,
            validate=lambda _: {},
            door="api",
            actor="test",
        )
    reloaded = load_host_config(tmp_path)
    ready, halted = seed_runners(reloaded, list(reloaded.runners))
    assert not halted
    assert [runner.config.worker_path_prepend for runner in ready] == [("/stored-one",), ("/stored-two",)]
    assert reloaded.host_view().worker_path_prepend == ("/file",)
    assert reloaded.host_view().worker_env_passthrough == ("FILE_VAR",)
