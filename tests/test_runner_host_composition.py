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
from blizzard.runner.cli.runtime import host_tick_driver
from blizzard.runner.composition import HostProcess, RunnerProcess, add_runner, build_host_process
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.host_config import BuiltRunner, HaltedRunner, ResolvedToken, TokenSource
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.host_identity import HostIdentity
from blizzard.runner.hub.identity import IdentityVerdictHolder, RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.host_tick import HostTick
from blizzard.runner.loop_wiring import PeriodicDriver
from blizzard.runner.store.schema import host_outbound_buffer
from blizzard.runner.stores import RunnerStores
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC, ExternalSubscriptionUsageSnapshot
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass
from tests.host_files import host_config_of
from tests.runner_fakes import FakeSubscriptionSampler, migrated_store_at

pytestmark = pytest.mark.component

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
    built = [BuiltRunner(rid, dataclasses.replace(config, runner_id=rid), ResolvedToken()) for rid in runner_ids]
    return build_host_process(host_config_of(config), built, halted=halted)


def _reachable_runner_owned(root: object) -> list[str]:
    """The paths from ``root`` to a runner-owned object, never walking through ``HostProcess.runners``."""
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
                if isinstance(value, HostProcess) and member.name == "runners":
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
    host = _host(config, "rn_a")
    blocked, free = host.runners[0], add_runner(dataclasses.replace(config, runner_id="rn_b"), host, runner_id="rn_b")
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
    host.host_tick = HostTick(credential_renewal=renewal)
    drivers = [PeriodicDriver(config, interval_seconds=0.01, process_graph=runner) for runner in (blocked, free)]
    host_tick = host_tick_driver(host, tick_seconds=0.01)
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
            host.host_tick.run()
        assert samplers["anthropic"].sample_calls == 1
        assert host.host_stores.external_usage.latest_external_usage_attempt("anthropic") is not None
    finally:
        host.close()


def test_a_subscription_no_runner_spends_is_still_sampled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    samplers = _fake_samplers(monkeypatch)
    host = _host(_sampling_config(tmp_path), "rn_a")
    try:
        _register(host.runners[0])
        host.host_tick.run()
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
        [BuiltRunner("rn_a", dataclasses.replace(config, runner_id="rn_a"), ResolvedToken())],
        events=events,
    )
    try:
        _register(host.runners[0])
        host.host_tick.run()
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
    return build_host_process(
        file, [BuiltRunner("rn_a", dataclasses.replace(config, runner_id="rn_a"), ResolvedToken())]
    )


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
        host.host_tick.run()
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
        host.host_tick.run()
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
        host.host_tick.run()
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
        host.host_tick.run()
        runner = host.runners[0]
        assert runner.stores.outbound.pending_outbound() == []
        assert host.host_stores.external_usage.latest_external_usage_attempt("anthropic") is None

        _register(runner)
        host.host_tick.run()

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
    host = _host(config, "rn_a")
    add_runner(dataclasses.replace(config, runner_id="rn_b"), host, runner_id="rn_b")
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


def test_a_runner_closes_its_host_only_when_it_was_built_over_a_host_of_one(tmp_path: Path) -> None:
    host = _host(_config(tmp_path), "rn_a")
    disposed: list[bool] = []
    dispose = host.engine.dispose

    def tracked_dispose(close: bool = True) -> None:
        disposed.append(True)
        dispose(close)

    host.engine.dispose = tracked_dispose
    [runner] = host.runners

    dataclasses.replace(runner, closes_host=False).close()
    assert disposed == []

    dataclasses.replace(runner, closes_host=True).close()
    assert disposed == [True]


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

    host = build_host_process(host_config, [BuiltRunner("rn_a", first, ResolvedToken())])
    host.close()

    path = launched[0].variables["PATH"].split(":")
    assert "/opt/host" in path and "/opt/first" not in path
