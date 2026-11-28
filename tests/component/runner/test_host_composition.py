"""The runner daemon composed as a host around its runners (component tier): per-runner objects are reachable from the
host only through ``runners``, and each runner's tick and the host's own tick run on threads of their own."""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func, select

import blizzard.runner.composition as composition
import blizzard.runner.loop_wiring as loop_wiring
from blizzard.foundation.clock import SystemMonotonicClock
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.registry.interface import ScopeClosed
from blizzard.runner.app import build_hosted_app_over_process
from blizzard.runner.cli.runtime import host_tick_driver
from blizzard.runner.composition import HostProcess, RunnerProcess
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.config_table import ConfigError
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.harness_set import (
    HarnessAssemblyInputs,
    HarnessAssemblyKey,
    HarnessSet,
    HarnessSets,
    WorkerInvocation,
)
from blizzard.runner.harness.health import HarnessHealthCause
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.harness.wiring import first_bindings
from blizzard.runner.host_config import BuiltRunner, HaltedRunner, ResolvedToken, TokenSource
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.host_identity import HostIdentity
from blizzard.runner.hub.identity import IdentityVerdictHolder, RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.leases.model import NewLease
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.host_tick import HostTick
from blizzard.runner.loop_wiring import PeriodicDriver
from blizzard.runner.node_steps.envelope import EnvelopeNode
from blizzard.runner.store.schema import host_outbound_buffer
from blizzard.runner.stores import RunnerStores
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC, ExternalSubscriptionUsageSnapshot
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass
from tests.support.composed_host import build_host_process
from tests.support.harness_bundle import bundle as harness_bundle
from tests.support.harness_bundle import runtime as harness_runtime
from tests.support.host_files import host_config_of
from tests.support.runner_fakes import FakeSubscriptionSampler, migrated_store_at
from tests.support.runner_mint import new_mint

_RUNNER_OWNED = (
    RunnerStores,
    RunnerIdentityHolder,
    IdentityVerdictHolder,
    HubTokenHolder,
    HarnessRegistry,
    ThreadPoolExecutor,
)


def _config(tmp_path: Path) -> RunnerConfig:
    config = RunnerConfig(root=tmp_path, db_url=HostLayout(tmp_path).default_db_url, hub_url="http://127.0.0.1:9")
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    return config


def _host(config: RunnerConfig, *runner_ids: str, halted: tuple[HaltedRunner, ...] = ()) -> HostProcess:
    """The host the config's file declares, with one built runner for each of ``runner_ids``."""
    built = [BuiltRunner(rid, dataclasses.replace(config, runner_id=rid)) for rid in runner_ids]
    return build_host_process(host_config_of(config), built, halted=halted)


@pytest.mark.parametrize("version, recover", [("2.1.0", True), ("99.0.0", False), (None, False)])
def test_restart_recovers_disabled_session_without_advertising_it(
    tmp_path: Path, version: str | None, recover: bool
) -> None:
    binary = tmp_path / "opencode"
    if version is not None:
        binary.write_text(f"#!/bin/sh\necho '{version}'\n")
        binary.chmod(0o755)
    config = _config(tmp_path)
    host = _host(config, "rn_a")
    now = datetime.now(UTC)
    try:
        stores = host.runners[0].stores
        lease = NewLease("lease_a", "ch_a", "gr_a", "nd_a", "build", 1, 2, now)
        stores.lease_record.record_mint(new_mint(lease, harness_id="opencode"))
        stores.liveness.record_spawn(
            "lease_a",
            pid=123,
            process_start_time="start",
            spawned_at=now,
            session=SessionReference("opencode", "session-a"),
            transcripts_root="/old-transcripts",
        )
    finally:
        host.close()

    section = OpenCodeSection(binary=str(binary), enabled=False)
    config = dataclasses.replace(config, harness_sections=config.harness_sections.replaced(section))
    host = _host(config, "rn_a")
    stepper = loop_wiring.RunnerStepper(
        config, process_graph=dataclasses.replace(host.runners[0], settings_reader=None)
    )
    try:
        ctx = stepper._context()  # pyright: ignore[reportPrivateUsage]
        sets = host.runners[0].harness_sets
        assert "opencode" not in sets.current.registry.known_harnesses
        node = EnvelopeNode(
            "n", "build", Executor.RUNNER, SessionMode.FRESH, JudgedBy.WORKER, session_harnesses=["opencode"]
        )
        assert ctx.harness_selector.select(node).harness_id is None
        owner = sets.find(WorkerInvocation("lease_a", 1))
        assert (owner is not None) is recover
        if owner is not None:
            assert "opencode_v2" in type(owner.selection.of("opencode")).__module__
            assert ctx.harnesses_for_lease("lease_a").lifecycle("opencode") is not None
            assert ctx.invocation_transcript_source(SessionReference("opencode", "session-a"), "lease_a", 1)
    finally:
        stepper.close()
        host.close()


def test_production_set_reuses_last_key_and_rebuilds_for_changed_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys: list[HarnessAssemblyKey] = []
    select = HarnessSets.select

    def capture(sets: HarnessSets, key: HarnessAssemblyKey) -> HarnessSet:
        keys.append(key)
        return select(sets, key)

    monkeypatch.setattr(HarnessSets, "select", capture)
    host = _host(_config(tmp_path), "rn_a")
    try:
        runner = host.runners[0]
        key = keys[0]
        initial = runner.harness_sets.current
        assert runner.harness_sets.select(key) is initial
        inputs = cast(HarnessAssemblyInputs, key.values[0])
        changed = dataclasses.replace(key, values=(dataclasses.replace(inputs, telemetry_enabled=True),))
        replacement = runner.harness_sets.select(changed)
        assert replacement is not initial
        assert replacement.registry is not initial.registry
        assert replacement.health is not initial.health
        assert runner.harnesses is replacement.registry
        assert runner.health is replacement.health
        assert runner.harness_sets.select(changed) is replacement
    finally:
        host.close()


def test_stepper_rebinds_all_harness_consumers_without_replacing_hub_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keys: list[HarnessAssemblyKey] = []
    select = HarnessSets.select

    def capture(sets: HarnessSets, key: HarnessAssemblyKey) -> HarnessSet:
        keys.append(key)
        return select(sets, key)

    monkeypatch.setattr(HarnessSets, "select", capture)
    config = _config(tmp_path)
    host = _host(config, "rn_a")
    # An externally selected set is the source of truth for graphs without a
    # live settings reader; reader-backed graphs instead select from the record.
    stepper = loop_wiring.RunnerStepper(
        config, process_graph=dataclasses.replace(host.runners[0], settings_reader=None)
    )
    try:
        sets = host.runners[0].harness_sets
        original = stepper._context()  # pyright: ignore[reportPrivateUsage]
        client = stepper._client  # pyright: ignore[reportPrivateUsage]
        held = sets.retain("existing")
        inputs = cast(HarnessAssemblyInputs, keys[0].values[0])
        selected = sets.select(composition.harness_assembly_key(dataclasses.replace(inputs, telemetry_enabled=True)))
        current = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert current is not original
        assert stepper._client is client  # pyright: ignore[reportPrivateUsage]
        assert current.harnesses is selected.registry
        assert current.harness_selector.harnesses is selected.registry
        assert current.harness_selector.health is selected.health
        assert current.sessions.harnesses is selected.registry
        assert current.usage.harnesses is selected.registry
        assert current.harness_health is selected.health
        assert current.harness_versions is selected.versions
        assert current.retention_passes is original.retention_passes
        assert sets.lookup("existing") is held
        assert original.harnesses is held.registry
        assert stepper._context() is current  # pyright: ignore[reportPrivateUsage]
        sets.release("existing")
    finally:
        stepper.close()
        host.close()


def test_replacement_with_unchanged_bundle_keeps_snapshot_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = harness_bundle(tmp_path)
    config = dataclasses.replace(_config(harness_runtime(tmp_path, source)), harness_config_dir=source)
    keys: list[HarnessAssemblyKey] = []
    select = HarnessSets.select

    def capture(sets: HarnessSets, key: HarnessAssemblyKey) -> HarnessSet:
        keys.append(key)
        return select(sets, key)

    monkeypatch.setattr(HarnessSets, "select", capture)
    built = BuiltRunner("rn_a", dataclasses.replace(config, runner_id="rn_a"))
    host = build_host_process(host_config_of(config), [built], publish_bundles=True)
    try:
        sets = host.runners[0].harness_sets
        original = sets.current
        assert original.bundle is not None
        inputs = cast(HarnessAssemblyInputs, keys[0].values[0])
        changed = dataclasses.replace(keys[0], values=(dataclasses.replace(inputs, telemetry_enabled=True),))
        replacement = sets.select(changed)
        assert replacement is not original
        assert replacement.bundle is not None
        assert replacement.bundle.path == original.bundle.path
        (source / "claude-code" / "settings.json").write_text('{"note":"changed"}')
        changed_source = composition.harness_assembly_key(dataclasses.replace(inputs, telemetry_enabled=True))
        assert changed_source != changed
        newer = sets.select(changed_source)
        assert newer.bundle is not None
        assert newer.bundle.path != original.bundle.path
    finally:
        host.close()


def test_production_selection_health_and_capabilities_share_binary_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    binary, calls = tmp_path / "opencode", tmp_path / "calls"

    def executable(version: str) -> None:
        binary.write_text(f"#!/bin/sh\necho x >> '{calls}'\necho '{version}'\n")
        binary.chmod(0o755)

    executable("1.2.3")
    config = _config(tmp_path / "runner")
    sections = config.harness_sections.replaced(OpenCodeSection(binary=str(binary)))
    config = dataclasses.replace(config, harness_sections=sections)
    keys: list[HarnessAssemblyKey] = []
    select = HarnessSets.select

    def capture(sets: HarnessSets, key: HarnessAssemblyKey) -> HarnessSet:
        keys.append(key)
        return select(sets, key)

    monkeypatch.setattr(HarnessSets, "select", capture)
    host = _host(config, "rn_a")
    try:
        sets = host.runners[0].harness_sets
        initial = sets.current
        inputs = cast(HarnessAssemblyInputs, keys[0].values[0])
        assert calls.read_text().splitlines() == ["x"]
        lifecycle = initial.registry.lifecycle("opencode")
        assert initial.versions.get("opencode", lifecycle.observe_version) == "1.2.3"
        initial.probes["opencode"].probe_authentication()
        initial.health.invalidate("opencode")
        initial.probes["opencode"].probe_authentication()
        assert calls.read_text().splitlines() == ["x"]
        changed_inputs = dataclasses.replace(inputs, telemetry_enabled=not inputs.telemetry_enabled)
        same_binary = sets.select(composition.harness_assembly_key(changed_inputs))
        assert same_binary is not initial
        assert (
            same_binary.versions.get("opencode", same_binary.registry.lifecycle("opencode").observe_version) == "1.2.3"
        )
        assert calls.read_text().splitlines() == ["x"]
        changed_profile = dataclasses.replace(
            changed_inputs, worker_env=dataclasses.replace(changed_inputs.worker_env, path_prepend=(str(tmp_path),))
        )
        same_binary = sets.select(composition.harness_assembly_key(changed_profile))
        assert (
            same_binary.versions.get("opencode", same_binary.registry.lifecycle("opencode").observe_version) == "1.2.3"
        )
        assert calls.read_text().splitlines() == ["x"]
        same_binary.versions.forget("opencode")
        assert (
            same_binary.versions.get("opencode", same_binary.registry.lifecycle("opencode").observe_version) == "1.2.3"
        )
        assert calls.read_text().splitlines() == ["x", "x"]

        held = sets.retain("running-worker")
        executable("2.0.0")
        replacement_key = composition.harness_assembly_key(inputs)
        assert replacement_key != keys[0]
        replacement = sets.select(replacement_key)
        assert replacement is not initial
        assert sets.lookup("running-worker") is held
        assert sets.lookup("running-worker").registry is held.registry
        sets.release("running-worker")
        assert replacement.selection.of("opencode").service_version
        assert calls.read_text().splitlines() == ["x", "x", "x"]
        assert (
            replacement.versions.get("opencode", replacement.registry.lifecycle("opencode").observe_version) == "2.0.0"
        )
        assert calls.read_text().splitlines() == ["x", "x", "x"]
        probe = replacement.probes["opencode"]
        monkeypatch.setattr(probe, "config_conflicts", lambda: ())
        adapter = replacement.registry.model_resolution("opencode")
        before = replacement.health.refresh("opencode", adapter=adapter, observed_version="2.0.0")
        assert before is not None and before.cause != HarnessHealthCause.MISSING_BINARY
        binary.unlink()
        replacement.health.invalidate("opencode")
        after = replacement.health.refresh("opencode", adapter=adapter, observed_version="2.0.0")
        assert after is not None and after.cause == HarnessHealthCause.MISSING_BINARY
    finally:
        host.close()


def test_worker_config_change_rebuilds_and_republishes_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = harness_bundle(tmp_path)
    root = harness_runtime(tmp_path, source)
    config = dataclasses.replace(_config(root), harness_config_dir=source)
    keys: list[HarnessAssemblyKey] = []
    select = HarnessSets.select

    def capture(sets: HarnessSets, key: HarnessAssemblyKey) -> HarnessSet:
        keys.append(key)
        return select(sets, key)

    monkeypatch.setattr(HarnessSets, "select", capture)
    built = BuiltRunner("rn_a", dataclasses.replace(config, runner_id="rn_a"))
    host = build_host_process(
        host_config_of(config), [built], selections={"rn_a": first_bindings()}, publish_bundles=True
    )
    try:
        sets = host.runners[0].harness_sets
        original = sets.current
        assert original.bundle is not None
        inputs = cast(HarnessAssemblyInputs, keys[0].values[0])
        assert composition.harness_assembly_key(inputs) == keys[0]
        worker_config = root / "opencode-worker-config.json"
        document = json.loads(worker_config.read_text())
        document["plugin"].append("new-worker-plugin@1")
        worker_config.write_text(json.dumps(document))
        changed = composition.harness_assembly_key(inputs)
        assert changed != keys[0]
        replacement = sets.select(changed)
        assert replacement is not original
        assert replacement.bundle is not None
        assert replacement.bundle.path != original.bundle.path
        published = json.loads((replacement.bundle.path / "opencode" / "opencode.json").read_text())
        assert "new-worker-plugin@1" in published["plugin"]
    finally:
        host.close()


def _reachable_runner_owned(root: object) -> list[str]:
    """The paths from ``root`` to a runner-owned object, never walking through the host's runner roster."""
    found: list[str] = []
    seen: set[int] = set()

    def walk(value: object, path: str) -> None:
        if id(value) in seen or isinstance(value, (str, bytes, int, float, bool, type(None), Path)):
            return
        seen.add(id(value))
        if isinstance(value, _RUNNER_OWNED):
            found.append(path)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for index, item in enumerate(cast("Iterable[object]", value)):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, dict):
            for key, item in cast("dict[object, object]", value).items():
                walk(item, f"{path}[{key!r}]")
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            for member in dataclasses.fields(value):
                if isinstance(value, HostProcess) and member.name == "runner_roster":
                    continue
                walk(getattr(value, member.name), f"{path}.{member.name}")
        elif type(value).__module__.startswith("blizzard") and hasattr(value, "__dict__"):
            for name, item in list(vars(value).items()):
                walk(item, f"{path}.{name}")

    walk(root, "host")
    return found


def test_no_runner_owned_object_is_reachable_from_the_host_except_through_its_runners(tmp_path: Path) -> None:
    host = _host(_config(tmp_path), "rn_a")
    try:
        assert len(host.runners) == 1
        assert _reachable_runner_owned(host) == []
        # The walk is not vacuous: the runner's own graph holds every type it forbids elsewhere.
        assert _reachable_runner_owned(host.runners[0]) != []
    finally:
        host.close()


class _CountingRenewal:
    def __init__(self) -> None:
        self.passes = 0

    def run(self) -> None:
        self.passes += 1


def _wait_until(condition: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def test_one_runners_blocked_tick_delays_neither_the_other_runner_nor_the_host_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    host = _host(config, "rn_a", "rn_b")
    blocked, free = host.runners
    release = threading.Event()
    entered = threading.Event()
    ticks: dict[int, int] = {id(blocked.stores): 0, id(free.stores): 0}

    def tick(ctx: LoopContext) -> None:
        owner = id(ctx.stores)
        ticks[owner] += 1
        if owner == id(blocked.stores):
            entered.set()
            release.wait(timeout=30)

    monkeypatch.setattr(loop_wiring, "tick", tick)
    renewal: Any = _CountingRenewal()
    host.tick_in_force.hold(HostTick(credential_renewal=renewal), host.tick_in_force.subscriptions())
    drivers = [
        PeriodicDriver(config, interval=lambda: 0.01, process_graph=runner, clock=SystemMonotonicClock())
        for runner in (blocked, free)
    ]
    host_tick = host_tick_driver(host, interval=lambda: 0.01)
    assert not isinstance(renewal, CredentialRenewalPass)  # a counting double stands in for the renewal pass
    try:
        for driver in drivers:
            driver.start()
        host_tick.start()
        assert host_tick.thread.name == "blizzard-host-tick"
        assert entered.wait(timeout=10), "the blocking runner's tick never ran"
        free_before, host_before = ticks[id(free.stores)], renewal.passes
        _wait_until(
            lambda: ticks[id(free.stores)] >= free_before + 3 and renewal.passes >= host_before + 3,
            "the free runner's tick and the host tick to advance while the other runner's tick is blocked",
        )
        assert ticks[id(blocked.stores)] == 1
    finally:
        release.set()
        host_tick.stop()
        for driver in drivers:
            driver.stop()
        host.close()


def test_each_built_runner_is_bound_to_its_configured_id_and_a_halted_one_is_kept_unbuilt(tmp_path: Path) -> None:
    halted = HaltedRunner("r-broken", "rn_c", ("names subscription 'x', which the host does not declare",))
    host = _host(_config(tmp_path), "rn_a", "rn_b", halted=(halted,))
    try:
        assert [runner.runner_id for runner in host.runners] == ["rn_a", "rn_b"]
        assert [runner.connections.runner_id for runner in host.runners] == ["rn_a", "rn_b"]
        assert host.halted == (halted,)
    finally:
        host.close()


def test_the_readiness_names_each_halted_runner_and_keeps_meaning_the_stores(tmp_path: Path) -> None:
    halted = HaltedRunner("r-broken", "rn_c", ("has no hub token",))
    host = _host(_config(tmp_path), "rn_a", halted=(halted,))
    try:
        readiness = host.readiness.evaluate()
        assert readiness.ready is True
        assert halted.describe() in readiness.detail
        assert "r-broken" in readiness.detail and "rn_c" in readiness.detail and "has no hub token" in readiness.detail
    finally:
        host.close()


_SAMPLED_KIND = "external_subscription_usage.sampled"
_SAMPLED_AT = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _sampling_config(tmp_path: Path) -> RunnerConfig:
    return dataclasses.replace(
        _config(tmp_path),
        subscriptions=(
            SubscriptionDeclaration(slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC),
            SubscriptionDeclaration(slug="spare", name="Spare", provider=PROVIDER_ANTHROPIC),
        ),
    )


def _register(runner: RunnerProcess) -> None:
    """``runner`` as registered: its identity row recorded, and the holder answering it."""
    identity = RunnerIdentity(runner.runner_id, "runner-local", _SAMPLED_AT)
    runner.stores.identity.record_runner_identity(identity)
    runner.identity.hold(identity)


def _fake_samplers(monkeypatch: pytest.MonkeyPatch) -> dict[str, FakeSubscriptionSampler]:
    """Stand a scriptable sampler in for every declared subscription's real one, for each host built after."""
    fakes: dict[str, FakeSubscriptionSampler] = {}

    def select(declaration: SubscriptionDeclaration, **_: object) -> FakeSubscriptionSampler:
        return fakes.setdefault(
            declaration.slug, FakeSubscriptionSampler(snapshot=ExternalSubscriptionUsageSnapshot(_SAMPLED_AT, ()))
        )

    monkeypatch.setattr(composition, "select_sampler", select)
    return fakes


def test_two_runners_spending_one_subscription_share_one_sample_per_cadence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    samplers = _fake_samplers(monkeypatch)
    host = _host(_sampling_config(tmp_path), "rn_a", "rn_b")
    try:
        _register(host.runners[0])
        for _ in range(4):
            host.tick_in_force.current().run()
        assert samplers["anthropic"].sample_calls == 1
        assert host.host_stores.external_usage.latest_external_usage_attempt("anthropic") is not None
    finally:
        host.close()


def test_a_subscription_no_runner_spends_is_still_sampled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    samplers = _fake_samplers(monkeypatch)
    host = _host(_sampling_config(tmp_path), "rn_a")
    try:
        _register(host.runners[0])
        host.tick_in_force.current().run()
        assert samplers["spare"].sample_calls == 1
        assert host.host_stores.external_usage.latest_external_usage_attempt("spare") is not None
    finally:
        host.close()


def test_an_unregistered_hosts_sample_reaches_its_runners_outbound_buffer_and_is_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_samplers(monkeypatch)
    events = EventBroker()
    config = _sampling_config(tmp_path)
    host = build_host_process(
        host_config_of(config),
        [BuiltRunner("rn_a", dataclasses.replace(config, runner_id="rn_a"))],
        events=events,
    )
    try:
        _register(host.runners[0])
        host.tick_in_force.current().run()
        runner = host.runners[0]
        pending = runner.stores.outbound.pending_outbound()
        assert [fact.kind for fact in pending] == [_SAMPLED_KIND, _SAMPLED_KIND]
        assert {json.loads(fact.payload)["slug"] for fact in pending} == {"anthropic", "spare"}
        assert all(fact.chunk_id is None and fact.lease_id is None for fact in pending)
        frames = [json.loads(e.data) for e in events.snapshot() if e.type == "fact-changed"]
        assert [frame["kind"] for frame in frames] == [_SAMPLED_KIND, _SAMPLED_KIND]
        assert {frame["runner_id"] for frame in frames} == {"rn_a"}
        with host.engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(host_outbound_buffer)).scalar_one() == 0
    finally:
        host.close()


def _registered_host(config: RunnerConfig) -> HostProcess:
    """The host the config's file declares, holding a host token, with one built runner ``rn_a``."""
    file = dataclasses.replace(host_config_of(config), host_token=ResolvedToken("host-tok", TokenSource.HOST_FILE))
    return build_host_process(file, [BuiltRunner("rn_a", dataclasses.replace(config, runner_id="rn_a"))])


def test_host_report_reads_the_holder_and_current_runner_roster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_samplers(monkeypatch)
    host = _registered_host(_sampling_config(tmp_path))
    runners = host.runners
    try:
        holder = host.tick_in_force
        tick = holder.current()
        registration = tick.registration
        assert registration is not None
        assert [item.slug for item in registration.report().subscriptions] == ["anthropic", "spare"]
        assert tuple(registration.runners()) == host.runners
        holder.hold(tick, ())
        assert registration.report().subscriptions == ()
        host.runners = ()
        assert tuple(registration.runners()) == ()
    finally:
        host.runners = runners
        host.close()


def test_a_host_identity_row_without_a_host_token_leaves_the_host_unregistered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_samplers(monkeypatch)
    config = _sampling_config(tmp_path)
    first = _host(config, "rn_a")
    first.host_stores.host_identity.record_host_identity(HostIdentity("hs_1", "tn_1", _SAMPLED_AT))
    first.close()
    host = _host(config, "rn_a")
    try:
        assert host.host_identity.current() == HostIdentity("hs_1", "tn_1", _SAMPLED_AT)
        _register(host.runners[0])
        host.tick_in_force.current().run()
        assert [fact.kind for fact in host.runners[0].stores.outbound.pending_outbound()] == [_SAMPLED_KIND] * 2
        with host.engine.connect() as conn:
            assert conn.execute(select(func.count()).select_from(host_outbound_buffer)).scalar_one() == 0
    finally:
        host.close()


def test_a_registered_hosts_sample_reaches_its_own_outbound_lane_and_no_runners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_samplers(monkeypatch)
    # Registered means holding a host token: the lane is the host's own before its hub has ever answered.
    host = _registered_host(_sampling_config(tmp_path))
    try:
        assert host.host_identity.current() is None
        host.tick_in_force.current().run()
        assert host.runners[0].stores.outbound.pending_outbound() == []
        with host.engine.connect() as conn:
            lane = conn.execute(select(host_outbound_buffer).order_by(host_outbound_buffer.c.seq)).all()
        assert [row.kind for row in lane] == [_SAMPLED_KIND, _SAMPLED_KIND]
        assert [json.loads(row.payload)["slug"] for row in lane] == ["anthropic", "spare"]
        assert all(row.acked_at is None for row in lane)
        # The attempt is the host's own: its cadence anchor holds the same either way.
        assert host.host_stores.external_usage.last_external_usage_attempt_at("anthropic") is not None
    finally:
        host.close()


def test_an_unregistered_hosts_sample_is_recorded_under_the_first_runner_that_has_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_samplers(monkeypatch)
    config = _sampling_config(tmp_path)
    host = _host(config, "rn_a", "rn_b")
    try:
        _register(host.runners[1])
        host.tick_in_force.current().run()
        assert host.runners[0].stores.outbound.pending_outbound() == []
        assert [fact.kind for fact in host.runners[1].stores.outbound.pending_outbound()] == [_SAMPLED_KIND] * 2
    finally:
        host.close()


def test_an_unregistered_hosts_sample_is_not_recorded_while_no_runner_has_registered_and_is_taken_again_after(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    samplers = _fake_samplers(monkeypatch)
    host = _host(_sampling_config(tmp_path), "rn_a")
    try:
        host.tick_in_force.current().run()
        runner = host.runners[0]
        assert runner.stores.outbound.pending_outbound() == []
        assert host.host_stores.external_usage.latest_external_usage_attempt("anthropic") is None

        _register(runner)
        host.tick_in_force.current().run()

        assert [fact.kind for fact in runner.stores.outbound.pending_outbound()] == [_SAMPLED_KIND] * 2
        assert samplers["anthropic"].sample_calls == 2
    finally:
        host.close()


class _Recorded:
    def __init__(self, name: str, order: list[str]) -> None:
        self._name, self._order = name, order

    def run(self) -> None:
        self._order.append(self._name)


def test_a_host_tick_renews_credentials_before_sampling_and_drains_after_sampling() -> None:
    order: list[str] = []
    sample: Any = _Recorded("sample", order)
    drain: Any = _Recorded("drain", order)
    renew: Any = _Recorded("renew", order)
    tick = HostTick(registration=None, external_usage_sample=sample, lane_drain=drain, credential_renewal=renew)

    tick.run()

    assert order == ["renew", "sample", "drain"]


def test_a_hosts_close_shuts_every_runners_executor_down_waiting_before_it_disposes_the_engine(tmp_path: Path) -> None:
    config = _config(tmp_path)
    host = _host(config, "rn_a", "rn_b")
    order: list[str] = []
    for runner in host.runners:
        shutdown = runner.executor.shutdown
        waited: list[bool] = []

        def record(
            wait: bool = True,
            *,
            name: str = runner.runner_id,
            original: Any = shutdown,
            seen: list[bool] = waited,
            **kw: Any,
        ) -> None:
            seen.append(wait)
            order.append(f"{name}:{'wait' if wait else 'nowait'}")
            original(wait=wait, **kw)

        runner.executor.shutdown = record
    dispose = host.engine.dispose

    def disposed(*args: Any, **kw: Any) -> None:
        order.append("engine")
        dispose(*args, **kw)

    host.engine.dispose = disposed

    host.close()

    assert order == ["rn_b:wait", "rn_a:wait", "engine"] or order == ["rn_a:wait", "rn_b:wait", "engine"]


def test_the_host_graphs_tool_env_follows_the_host_tool_profile_not_the_first_runners_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import blizzard.runner.composition as composition

    launched: list[Any] = []
    real = composition.WorkspaceLaunch

    def recording(**kwargs: Any) -> Any:
        launched.append(kwargs["env"])
        return real(**kwargs)

    monkeypatch.setattr(composition, "WorkspaceLaunch", recording)
    config = _config(tmp_path)
    first = dataclasses.replace(config, runner_id="rn_a", worker_path_prepend=("/opt/first",))
    host_config = dataclasses.replace(host_config_of(config), tool_path_prepend=("/opt/host",))

    host = build_host_process(host_config, [BuiltRunner("rn_a", first)])
    host.close()

    path = launched[0].current().variables["PATH"].split(":")
    assert "/opt/host" in path and "/opt/first" not in path


def test_the_boot_composes_each_runner_as_its_child_opens_and_closes_child_then_wiring_then_nothing_of_the_host(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    host = composition.build_host_process(host_config_of(config))
    assert host.runners == ()
    hosted = build_hosted_app_over_process(
        config,
        host,
        host_config=host_config_of(config),
        boot=[BuiltRunner(rid, dataclasses.replace(config, runner_id=rid)) for rid in ("rn_a", "rn_b")],
    )
    try:
        live = hosted.runners.live()
        assert [runner.runner_id for runner in host.runners] == ["rn_a", "rn_b"]
        for runner in live:
            assert runner.child is not None and runner.child.owner == runner.runner_id
            assert runner.child.resolver.get(RunnerProcess) is runner.runner
    finally:
        hosted.close()
    for runner in live:
        assert runner.child is not None
        with pytest.raises(ScopeClosed):
            runner.child.resolver.get(RunnerProcess)
        assert runner.runner.identity_client.is_closed
    with host.engine.connect():
        pass
    host.close()


def test_a_boot_compose_failure_fails_the_host_start_and_leaves_no_child_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    compose = composition.compose_runner
    opened: list[RunnerProcess] = []

    def refusing(host_config: Any, host: HostProcess, built: BuiltRunner, **kwargs: Any) -> RunnerProcess:
        if built.id == "rn_b":
            raise ConfigError("runner rn_b: a bad entry")
        runner = compose(host_config, host, built, **kwargs)
        opened.append(runner)
        return runner

    monkeypatch.setattr(composition, "compose_runner", refusing)
    host = composition.build_host_process(host_config_of(config))
    try:
        with pytest.raises(ConfigError, match="a bad entry"):
            build_hosted_app_over_process(
                config,
                host,
                host_config=host_config_of(config),
                boot=[BuiltRunner(rid, dataclasses.replace(config, runner_id=rid)) for rid in ("rn_a", "rn_b")],
            )
        [first] = opened
        assert first.identity_client.is_closed
    finally:
        host.close()
