"""The host infrastructure graph owns resources and shares store adapters."""

from pathlib import Path
from typing import Any, cast

import pytest

from blizzard.foundation.clock import IClock, IMonotonicClock, SystemClock, SystemMonotonicClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing, IPlatformTracing
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import ScopeValues, supply
from blizzard.runner.composition import LiveRunner, RunnerProcess, build_host_of_one, build_host_process
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_root import HostRoot, open_host_infrastructure, register_host_infrastructure
from blizzard.runner.hub.client import HostReport
from blizzard.runner.hub.host_identity import IReadHostIdentityRepository, IWriteHostIdentityRepository
from blizzard.runner.hub.host_options import HostHubOptions
from blizzard.runner.hub.internal.host_infrastructure import HostHubTransport
from blizzard.runner.loop.host_tick import HostTick, HostTickInForce, register_host_tick
from blizzard.runner.store.host_resources import open_engine
from blizzard.runner.stores import HostStores
from blizzard.runner.subscriptions.internal.usage_http_client import LazyUsageHttpClient
from blizzard.runner.tracing.bounded_export import BoundedTraceExporters
from blizzard.runner.tracing.config import RunnerTracingConfig
from blizzard.runner.tracing.host_options import HostTracingOptions
from blizzard.runner.tracing.internal.host_infrastructure import (
    HarnessSpanCounter,
    LogBounds,
    MetricBounds,
    SpanCounter,
)
from blizzard.runner.tracing.receiving import TelemetryReceiver
from blizzard.runner.wiring import HostRuntimeOptions
from tests.support.container_guards import graph_builds
from tests.support.host_files import host_config_of
from tests.support.host_runner_graph import FakeResume, FakeWiring, built_runner


@pytest.mark.parametrize("registered", [False, True])
def test_infrastructure_graph_builds_and_shares_host_store_ports(tmp_path: Path, registered: bool) -> None:
    registry = LegacyRegistry()
    for key in (RunnerConfig, IClock, IMonotonicClock, HostHubOptions, HostTracingOptions, HostRuntimeOptions):
        registry.context(port(key), lifetime=Lifetime.PROCESS)
    register_host_tick(registry, HostTickInForce[SubscriptionDeclaration])
    register_host_infrastructure(registry, registered=registered)
    values = ScopeValues(
        (
            supply(port(RunnerConfig), RunnerConfig(root=tmp_path, db_url="sqlite://")),
            supply(port(IClock), SystemClock()),
            supply(port(IMonotonicClock), SystemMonotonicClock()),
            supply(port(HostRuntimeOptions), HostRuntimeOptions({})),
            supply(port(HostTickInForce[SubscriptionDeclaration]), HostTickInForce(HostTick(), ())),
            supply(
                port(HostHubOptions),
                HostHubOptions("http://localhost", "token" if registered else "", HostReport, lambda: ()),
            ),
            supply(
                port(HostTracingOptions),
                HostTracingOptions(RunnerTracingConfig(), {}, (), platform_tracing=DisabledPlatformTracing()),
            ),
        )
    )
    assert graph_builds(registry, HOST_LIFETIMES, values) == []
    owner = build_container(registry, HOST_LIFETIMES, owner="host", values=values)
    try:
        stores = owner.resolver.get(HostStores)
        assert owner.resolver.get(IReadHostIdentityRepository) is stores.host_identity
        assert owner.resolver.get(IWriteHostIdentityRepository) is stores.host_identity
        assert owner.resolver.get(LazyUsageHttpClient).built_client is None
    finally:
        owner.close()


@pytest.mark.parametrize("registered", [False, True])
def test_production_opener_acquires_the_complete_graph(tmp_path: Path, registered: bool) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    owner = open_host_infrastructure(
        config,
        SystemClock(),
        SystemMonotonicClock(),
        HostRuntimeOptions({}),
        HostHubOptions("http://localhost", "token" if registered else "", HostReport, lambda: ()),
        HostTracingOptions(config.tracing, {}, ()),
        tick_in_force=HostTickInForce(HostTick(), ()),
    )
    owner.close()


def test_engine_is_disposed_if_instrumentation_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Engine:
        disposed = False

        def dispose(self) -> None:
            self.disposed = True

    class Tracing:
        def instrument_engine(self, engine: object) -> None:
            raise ValueError("instrumentation refused")

    engine = Engine()

    def create_engine(url: str) -> Engine:
        return engine

    monkeypatch.setattr("blizzard.runner.store.host_resources.create_engine_from_url", create_engine)
    opening = open_engine(RunnerConfig(root=tmp_path, db_url="sqlite://"), cast(IPlatformTracing, Tracing()))
    with pytest.raises(ValueError, match="instrumentation refused"):
        next(opening)
    assert engine.disposed


def test_production_host_and_root_share_canonical_infrastructure(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    clock, monotonic = SystemClock(), SystemMonotonicClock()
    host = build_host_of_one(config, clock=clock, monotonic_clock=monotonic)
    root = HostRoot[Any, Any].serving(host, HostConfig(root=tmp_path, db_url=config.db_url))
    try:
        resolver = root.owner.resolver
        assert resolver.get(HostStores) is host.host_stores
        assert resolver.get(IClock) is host.clock is clock
        assert resolver.get(IMonotonicClock) is host.monotonic_clock is monotonic
        assert resolver.get(TelemetryReceiver) is host.telemetry_receiver
        assert resolver.get(HostTickInForce[SubscriptionDeclaration]) is host.tick_in_force
        assert resolver.get(SpanCounter).value is host.telemetry_receiver.span_counter
        assert resolver.get(HarnessSpanCounter).value is host.telemetry_receiver.harness_span_counter
        assert host.telemetry_receiver.span_counter is not host.telemetry_receiver.harness_span_counter
        assert resolver.get(MetricBounds).value is host.telemetry_receiver.metric_bounds
        assert resolver.get(LogBounds).value is host.telemetry_receiver.log_bounds
        assert host.telemetry_receiver.metric_bounds is not host.telemetry_receiver.log_bounds
        exporters = resolver.get(BoundedTraceExporters)
        exporter = cast("Any", object())
        assert exporters.for_exporter(exporter) is exporters.for_exporter(exporter)
    finally:
        root.close()
        host.close()


def test_borrowed_root_has_own_runner_set_and_does_not_close_existing_set(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    host = build_host_of_one(config, clock=SystemClock())
    host_config = HostConfig(root=tmp_path, db_url=config.db_url)
    first = HostRoot[FakeWiring, FakeResume].attach(host.infrastructure, host, host_config, eager=())
    second = HostRoot[FakeWiring, FakeResume].attach(host.infrastructure, host, host_config, eager=())
    try:
        assert first.owner is second.owner
        assert first.runners is not second.runners
        second.close()
        child = first.runners.open_runner(built_runner("rn_other"))
        assert child is not None
        child.close()
    finally:
        first.close()
        host.close()


def test_borrowed_tracing_is_not_shutdown_by_host(tmp_path: Path) -> None:
    class Tracing(DisabledPlatformTracing):
        stopped = False

        def shutdown(self, timeout: float) -> None:
            self.stopped = True

    tracing = Tracing()
    host = build_host_of_one(
        RunnerConfig(root=tmp_path, db_url="sqlite://"), clock=SystemClock(), platform_tracing=tracing
    )
    host.close()
    assert not tracing.stopped


def test_settings_initialization_failure_closes_engine_and_preserves_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from blizzard.foundation.store.engine import create_engine_from_url

    engine = create_engine_from_url("sqlite://")
    disposed: list[str] = []
    original_dispose = engine.dispose

    def dispose() -> None:
        disposed.append("engine")
        original_dispose()
        raise OSError("dispose refused")

    def fail_settings(*args: object, **kwargs: object) -> None:
        raise ValueError("settings refused")

    def open_test_engine(url: str) -> Any:
        return engine

    monkeypatch.setattr(engine, "dispose", dispose)
    monkeypatch.setattr("blizzard.runner.store.host_resources.create_engine_from_url", open_test_engine)
    monkeypatch.setattr("blizzard.runner.composition.HostSettingsInForce", fail_settings)
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with pytest.raises(ValueError, match="settings refused") as failure:
        build_host_process(host_config_of(config))
    assert disposed == ["engine"]
    assert "dispose refused" in "".join(failure.value.__notes__)


def test_runner_closes_before_owned_infrastructure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal: list[str] = []

    class Runner:
        runner_id = "rn_one"

        def close_runner(self) -> None:
            journal.append("runner")

    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="http://localhost")
    host = build_host_of_one(config, clock=SystemClock(), host_token="token")
    transport = host.infrastructure.resolver.get(HostHubTransport | None)
    assert transport is not None
    engine_close, usage_close, hub_close = (
        host.engine.dispose,
        host.usage_http_client.close,
        transport.client.close,
    )

    def engine() -> None:
        journal.append("engine")
        engine_close()

    def usage() -> None:
        journal.append("usage")
        usage_close()

    def hub() -> None:
        journal.append("hub")
        hub_close()

    monkeypatch.setattr(host.engine, "dispose", engine)
    monkeypatch.setattr(host.usage_http_client, "close", usage)
    monkeypatch.setattr(transport.client, "close", hub)
    root = HostRoot[FakeWiring, FakeResume].serving(host, HostConfig(root=tmp_path, db_url=config.db_url))
    root.runners.place(LiveRunner(cast("RunnerProcess", Runner()), FakeResume()))
    root.close()
    assert journal == ["runner"]
    host.close()
    assert set(journal) == {"runner", "engine", "usage", "hub"}
    assert journal[0] == "runner"


@pytest.mark.parametrize("fail_resources", [False, True])
def test_failed_runner_close_still_attempts_every_owned_finalizer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_resources: bool
) -> None:
    journal: list[str] = []

    class Runner:
        runner_id = "rn_one"

        def close_runner(self) -> None:
            journal.append("runner")
            raise ValueError("runner close refused")

    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="http://localhost")
    host = build_host_of_one(config, clock=SystemClock(), host_token="token")
    transport = host.infrastructure.resolver.get(HostHubTransport | None)
    assert transport is not None
    engine_close, usage_close, hub_close = host.engine.dispose, host.usage_http_client.close, transport.client.close

    def engine() -> None:
        journal.append("engine")
        engine_close()
        if fail_resources:
            raise OSError("engine close refused")

    def usage() -> None:
        journal.append("usage")
        usage_close()
        if fail_resources:
            raise OSError("usage close refused")

    def hub() -> None:
        journal.append("hub")
        hub_close()
        if fail_resources:
            raise OSError("hub close refused")

    monkeypatch.setattr(host.engine, "dispose", engine)
    monkeypatch.setattr(host.usage_http_client, "close", usage)
    monkeypatch.setattr(transport.client, "close", hub)
    root = HostRoot[FakeWiring, FakeResume].attach(
        host.infrastructure, host, HostConfig(root=tmp_path, db_url=config.db_url), owns_owner=True
    )
    root.runners.place(LiveRunner(cast("RunnerProcess", Runner()), FakeResume()))
    with pytest.raises(ValueError, match="runner close refused") as failure:
        root.close()
    assert journal[0] == "runner"
    assert set(journal) == {"runner", "engine", "usage", "hub"}
    if fail_resources:
        assert "closing the process scope after it also failed" in "".join(failure.value.__notes__)
