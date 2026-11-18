"""Startup seeds only a retained identity and resolves through the host parser."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import ManualMonotonicClock, SystemClock
from blizzard.foundation.settings_record.vocabulary import Change, Entry
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.harness.adapter import HarnessVersionChangedError
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID, ClaudeCodeSection
from blizzard.runner.harness.harness_set import HarnessAssemblyKey, HarnessSet, HarnessSets, WorkerInvocation
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.opencode_v2.adapter import OpenCodeTwoAdapter
from blizzard.runner.harness.opencode_v2.driver import ServiceIdentity
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.settings_reader import RunnerSettingsReader
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.internal.settings_store import RunnerSettingsStore
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, UnresolvableSettings, write_minimal_host
from tests.support.runner_fakes import FakeHub, make_store, migrated_store_at
from tests.support.runner_mint import new_mint


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
        unresolvable = UnresolvableSettings(monkeypatch)
        unresolvable.hold()
        assert stepper._context() is first  # pyright: ignore[reportPrivateUsage]
        assert stepper.failed_revision == revision
        assert stepper.settings_failure is not None
        assert stepper.settings_failure[0] == revision
        unresolvable.release()
        assert stepper._context().config.agents == 2  # pyright: ignore[reportPrivateUsage]
        assert stepper.settings_failure is None
    finally:
        stepper.close()
        graph.close()


@pytest.mark.parametrize("method", ["list_active_leases", "lease_generations"])
def test_recovery_read_failure_keeps_context_and_set_atomic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner="agents = 3\n")
    host = load_host_config(tmp_path)
    base = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime.now(UTC))
    )
    ready, _ = seed_runners(host, list(host.runners))
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        first = stepper._context()  # pyright: ignore[reportPrivateUsage]
        old_set = runner.harness_sets.current
        old_client = stepper._client  # pyright: ignore[reportPrivateUsage]
        reader = runner.settings_reader
        assert reader is not None
        settings = runner.stores.settings
        settings.write(
            Change("edit", sets={"claude_code.binary": "/different/claude"}),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        store = runner.stores.lease_record if method == "list_active_leases" else runner.stores.liveness
        original = getattr(type(store), method)
        prepared = False
        original_prepare = HarnessSets.prepare

        def prepare(sets: HarnessSets, key: HarnessAssemblyKey) -> HarnessSet:
            nonlocal prepared
            result = original_prepare(sets, key)
            prepared = True
            return result

        def failing(*args: object, **kwargs: object) -> object:
            if prepared:
                raise RuntimeError("recovery read failed")
            return original(*args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(HarnessSets, "prepare", prepare)
            patch.setattr(type(store), method, failing)
            assert stepper._context() is first  # pyright: ignore[reportPrivateUsage]
        assert runner.harness_sets.current is old_set
        assert stepper._client is old_client  # pyright: ignore[reportPrivateUsage]
        assert old_client is not None and not old_client.is_closed
        assert stepper.failed_revision == settings.snapshot().revision
        assert stepper._context() is not first  # pyright: ignore[reportPrivateUsage]
        assert runner.harness_sets.current is not old_set
    finally:
        stepper.close()
        graph.close()


@pytest.mark.parametrize("initial", [None, "99.0.0"])
def test_disabled_session_recovers_when_binary_becomes_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: str | None
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    binary = tmp_path / "opencode"
    if initial is not None:
        binary.write_text(f"#!/bin/sh\necho {initial}\n")
        binary.chmod(0o755)
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(
        tmp_path,
        top=f'db_url = "{url}"',
        runner=f'[runner.opencode]\nenabled = false\nbinary = "{binary}"\n',
    )
    host = load_host_config(tmp_path)
    migrated_store_at(url)
    base = make_store(url, TEST_RUNNER_ID)
    now = datetime.now(UTC)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, now)
    )
    lease = NewLease("lease_a", "ch_a", "gr_a", "nd_a", "build", 1, 2, now)
    base.record_mint(new_mint(lease, harness_id="opencode"))
    base.record_spawn(
        "lease_a",
        pid=123,
        process_start_time="start",
        spawned_at=now,
        session=SessionReference("opencode", "session-a"),
    )
    other = NewLease("lease_b", "ch_b", "gr_a", "nd_b", "build", 1, 2, now)
    base.record_mint(new_mint(other))
    base.record_spawn(
        "lease_b",
        pid=124,
        process_start_time="start-b",
        spawned_at=now,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "session-b"),
    )
    ready, _ = seed_runners(host, list(host.runners))
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        first = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert runner.harness_sets.find(WorkerInvocation("lease_a", 1)) is None
        held = runner.harness_sets.lookup(WorkerInvocation("lease_b", 1))
        binary.write_text("#!/bin/sh\necho 2.1.0\n")
        binary.chmod(0o755)
        second = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert second is not first
        recovered = runner.harness_sets.lookup(WorkerInvocation("lease_a", 1))
        assert "opencode_v2" in type(recovered.selection.of("opencode")).__module__
        assert second.harnesses_for_lease("lease_a").lifecycle("opencode") is not None
        assert runner.harness_sets.lookup(WorkerInvocation("lease_b", 1)) is held
        assert "opencode" not in runner.harness_sets.current.registry.known_harnesses
    finally:
        stepper.close()
        graph.close()


def test_recovery_retry_preserves_enabled_opencode_version_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    opencode = tmp_path / "opencode"
    opencode.write_text("#!/bin/sh\necho 2.1.0\n")
    opencode.chmod(0o755)
    claude = tmp_path / "claude"
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(
        tmp_path,
        top=f'db_url = "{url}"',
        runner=f'[runner.claude_code]\nenabled = false\nbinary = "{claude}"\n'
        f'[runner.opencode]\nenabled = true\nbinary = "{opencode}"\n',
    )
    host = load_host_config(tmp_path)
    migrated_store_at(url)
    base = make_store(url, TEST_RUNNER_ID)
    now = datetime.now(UTC)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, now)
    )
    lease = NewLease("lease_a", "ch_a", "gr_a", "nd_a", "build", 1, 2, now)
    base.record_mint(new_mint(lease))
    base.record_spawn(
        "lease_a",
        pid=123,
        process_start_time="start",
        spawned_at=now,
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "session-a"),
    )
    ready, _ = seed_runners(host, list(host.runners))
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        stepper._context()  # pyright: ignore[reportPrivateUsage]
        first = runner.harness_sets.current
        adapter = first.registry.lifecycle("opencode")
        assert isinstance(adapter, OpenCodeTwoAdapter)
        keeper = adapter._keeper  # pyright: ignore[reportPrivateUsage]
        identity = ServiceIdentity("service", 17, "3.0.0")
        monkeypatch.setattr(keeper, "answering", lambda: identity)
        monkeypatch.setattr(keeper, "ensure", lambda: identity)
        assert adapter.observe_version() == "3.0.0"
        identity = ServiceIdentity("service", 17, "2.1.0")
        assert adapter.observe_version() == "2.1.0"
        with pytest.raises(HarnessVersionChangedError):
            adapter.resume_with_message(
                str(tmp_path), "session", "continue", session_settings=adapter.launch_settings()
            )

        claude.write_text("#!/bin/sh\necho 1.0.0\n")
        claude.chmod(0o755)
        stepper._context()  # pyright: ignore[reportPrivateUsage]
        current = runner.harness_sets.current
        assert current.registry is first.registry
        assert current.health is first.health and current.versions is first.versions
        assert current.probes is first.probes and current.telemetry is first.telemetry
        assert current.registry.lifecycle("opencode") is adapter
        assert runner.harness_sets.find(WorkerInvocation("lease_a", 1)) is not None
        with pytest.raises(HarnessVersionChangedError, match=r"3\.0\.0"):
            adapter.resume_with_message(
                str(tmp_path), "session", "continue", session_settings=adapter.launch_settings()
            )
    finally:
        stepper.close()
        graph.close()


@pytest.mark.parametrize("initial", ["", "99.0.0"])
@pytest.mark.parametrize("persistent", [False, True])
def test_recovery_retries_unchanged_binary_version_after_transient_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial: str, persistent: bool
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    binary, answer, calls = tmp_path / "opencode", tmp_path / "version", tmp_path / "calls"
    answer.write_text(initial)
    binary.write_text(f"#!/bin/sh\necho x >> '{calls}'\ncat '{answer}'\n")
    binary.chmod(0o755)
    url = f"sqlite:///{tmp_path / 'host.db'}"
    write_minimal_host(
        tmp_path,
        top=f'db_url = "{url}"',
        runner=f'[runner.opencode]\nenabled = false\nbinary = "{binary}"\n',
    )
    host = load_host_config(tmp_path)
    migrated_store_at(url)
    base = make_store(url, TEST_RUNNER_ID)
    now = datetime.now(UTC)
    RunnerIdentityStore(base.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, now)
    )
    lease = NewLease("lease_a", "ch_a", "gr_a", "nd_a", "build", 1, 2, now)
    base.record_mint(new_mint(lease, harness_id="opencode"))
    base.record_spawn(
        "lease_a",
        pid=123,
        process_start_time="start",
        spawned_at=now,
        session=SessionReference("opencode", "session-a"),
    )
    ready, _ = seed_runners(host, list(host.runners))
    clock = ManualMonotonicClock()
    graph = build_host_process(host, ready, monotonic_clock=clock)
    runner = graph.runners[0]
    stepper = RunnerStepper(runner.config, process_graph=runner)
    try:
        first = stepper._context()  # pyright: ignore[reportPrivateUsage]
        selected = runner.harness_sets.current
        assert selected.pending_recovery == frozenset({"opencode"})
        assert runner.harness_sets.find(WorkerInvocation("lease_a", 1)) is None
        before = calls.read_text().splitlines()
        assert before == ["x"]
        for _ in range(3):
            assert stepper._context() is first  # pyright: ignore[reportPrivateUsage]
        assert calls.read_text().splitlines() == before

        if persistent:
            clock.advance(31)
            assert stepper._context() is first  # pyright: ignore[reportPrivateUsage]
            assert len(calls.read_text().splitlines()) == len(before) + 1
            assert runner.harness_sets.current is selected
            assert runner.harness_sets.find(WorkerInvocation("lease_a", 1)) is None

        answer.write_text("2.1.0")
        clock.advance(31)
        second = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert second is not first
        assert runner.harness_sets.current.registry is selected.registry
        assert runner.harness_sets.current.health is selected.health
        assert not runner.harness_sets.current.pending_recovery
        recovered = runner.harness_sets.lookup(WorkerInvocation("lease_a", 1))
        assert "opencode_v2" in type(recovered.selection.of("opencode")).__module__
        assert "opencode" not in runner.harness_sets.current.registry.known_harnesses
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
