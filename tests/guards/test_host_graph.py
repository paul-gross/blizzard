"""The runner host's registered graph keeps every registration rule, every host-root route's container dependency
resolves from its process scope, and the host-free graph refuses each as absent.

Each gate runs over the production graph and over a planted violation it must fail. The legacy leaves over the
borrowed host are inventoried, not refused: the runner children still hold them."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import FastAPI

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.foundation.clock import IClock, IMonotonicClock
from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.foundation.registry.internal.recording import Kind
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, supply
from blizzard.runner import composition
from blizzard.runner import wiring as runner_wiring
from blizzard.runner.app import create_app_for_export
from blizzard.runner.composition import HostProcess, RunnerCompose
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_root import HostRoot, open_host_free, register_host, register_host_infrastructure
from blizzard.runner.hub.host_options import HostHubOptions
from blizzard.runner.loop.host_tick import HostTick, HostTickInForce
from blizzard.runner.throttle import wiring as throttle
from blizzard.runner.throttle.floor_clear import IRecordSubscriptionFloorClear, SubscriptionFloorClearService
from blizzard.runner.throttle.usage_limits import IWriteSubscriptionUsageLimitRepository
from blizzard.runner.tracing.host_options import HostTracingOptions
from blizzard.runner.tracing.runner_export import RunnerTraceExport, RunnerTraceOptions
from tests.support.container_guards import (
    alias_identity_violations,
    default_factory_violations,
    duplicate_owners,
    graph_builds,
    legacy_registrations,
    lifetime_violations,
    role_key_violations,
)
from tests.support.host_files import host_config_of
from tests.support.route_resolution import (
    RouteDependency,
    route_dependencies,
    runner_route_dependencies,
    unrefused_absent_dependencies,
    unresolved_process_dependencies,
)
from tests.support.runner_fakes import make_store

#: The context values the host root opens with; validation reads only their keys.
_VALUES = ScopeValues(
    (
        supply(port(HostProcess), object()),
        supply(port(HostConfig), object()),
        supply(port(RunnerCompose), RunnerCompose()),
        supply(port(IClock), object()),
        supply(port(IWriteSubscriptionUsageLimitRepository), object()),
    )
)


def _host() -> LegacyRegistry:
    registry = LegacyRegistry()
    register_host(registry)
    return registry


class _Unregistered:
    pass


class _NeedsUnregistered:
    def __init__(self, missing: _Unregistered) -> None:
        self.missing = missing


class _Defaulted:
    def __init__(self, clears: IRecordSubscriptionFloorClear, limit: int = 3) -> None:
        self.clears = clears
        self.limit = limit


def _declares_nothing(registry: Registry) -> None:
    del registry


def _second_floor_clear(clears: IRecordSubscriptionFloorClear) -> SubscriptionFloorClearService:
    raise NotImplementedError


def _second_usage_limits(clock: IClock) -> IWriteSubscriptionUsageLimitRepository:
    raise NotImplementedError


def test_the_host_graph_builds_and_keeps_every_registration_rule() -> None:
    registry = _host()
    assert graph_builds(registry, HOST_LIFETIMES, _VALUES) == []
    assert duplicate_owners(registry) == []
    assert lifetime_violations(registry, HOST_LIFETIMES) == []
    assert alias_identity_violations(registry) == []
    assert role_key_violations(registry) == []
    assert default_factory_violations(registry) == []


def test_the_host_graph_inventories_the_legacy_leaves_over_its_borrowed_host() -> None:
    assert legacy_registrations(_host()) == ["RunnerProcess is a legacy leaf"]


@pytest.mark.parametrize("registered", [False, True])
def test_production_host_graph_inventory_and_validation(registered: bool) -> None:
    registry = LegacyRegistry()
    runner_wiring.register_infrastructure_contexts(registry)
    register_host_infrastructure(registry, registered=registered)
    values = ScopeValues(
        (
            supply(port(RunnerConfig), object()),
            supply(port(IClock), object()),
            supply(port(IMonotonicClock), object()),
            supply(port(runner_wiring.HostRuntimeOptions), object()),
            supply(port(HostHubOptions), object()),
            supply(port(HostTracingOptions), object()),
            supply(port(HostTickInForce[SubscriptionDeclaration]), object()),
        )
    )
    assert graph_builds(registry, HOST_LIFETIMES, values) == []
    assert duplicate_owners(registry) == []
    assert lifetime_violations(registry, HOST_LIFETIMES) == []
    assert role_key_violations(registry) == []
    assert default_factory_violations(registry) == []
    assert legacy_registrations(registry) == [
        "HostProcess is a legacy leaf",
        "HostRunnerSet is a legacy leaf",
        "RunnerProcess is a legacy leaf",
    ]
    assert not any(entry.key is HostTick for entry in registry.entries)
    assert [(entry.kind, entry.lifetime) for entry in registry.entries if entry.key is RunnerTraceOptions] == [
        (Kind.CONTEXT, Lifetime.RUNNER)
    ]
    assert [(entry.kind, entry.lifetime) for entry in registry.entries if entry.key is RunnerTraceExport] == [
        (Kind.RESOURCE, Lifetime.RUNNER)
    ]


def test_the_host_graph_gate_fails_a_planted_unresolvable_dependency() -> None:
    registry = _host()
    registry.service(_NeedsUnregistered, lifetime=Lifetime.PROCESS)
    assert len(graph_builds(registry, HOST_LIFETIMES, _VALUES)) == 1


def test_the_host_graph_gate_fails_a_planted_second_owner_and_role_key() -> None:
    registry = _host()
    registry.factory(_second_floor_clear, lifetime=Lifetime.PROCESS)
    assert duplicate_owners(registry) == ["SubscriptionFloorClearService has 2 owners"]
    assert role_key_violations(registry) == ["SubscriptionFloorClearService is the key of 2 registrations"]


def test_the_host_graph_gate_fails_a_planted_alias_onto_a_doubled_owner() -> None:
    registry = _host()
    registry.factory(_second_usage_limits, lifetime=Lifetime.PROCESS)
    assert alias_identity_violations(registry) == [
        "alias IRecordSubscriptionFloorClear targets IWriteSubscriptionUsageLimitRepository, which has 2 owners"
    ]


def test_the_host_graph_gate_fails_a_planted_off_path_lifetime_and_default() -> None:
    registry = _host()
    registry.service(_Unregistered, lifetime=Lifetime.TENANT)
    registry.service(_Defaulted, lifetime=Lifetime.PROCESS)
    assert len(lifetime_violations(registry, HOST_LIFETIMES)) == 1
    assert default_factory_violations(registry) == ["_Defaulted parameter 'limit' has a default"]


def test_the_host_graph_gate_fails_a_planted_legacy_leaf() -> None:
    registry = _host()
    registry.leaf(_second_floor_clear, lifetime=Lifetime.PROCESS)
    assert "SubscriptionFloorClearService is a legacy leaf" in legacy_registrations(registry)


@pytest.fixture(scope="module")
def _host_routes() -> list[RouteDependency]:
    return route_dependencies(create_app_for_export(packaged_flag_registry()))


@pytest.fixture
def _process(tmp_path: Path) -> Iterator[OwnedChild]:
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    make_store(config.db_url)
    host_config = host_config_of(config)
    host = composition.build_host_process(host_config)
    try:
        root = HostRoot[Any, Any].serving(host, host_config)
        try:
            yield root.owner
        finally:
            root.close()
    finally:
        host.close()


def test_every_host_routes_container_dependencies_resolve_from_the_process_scope(
    _host_routes: list[RouteDependency], _process: OwnedChild
) -> None:
    assert {(each.route, each.dependency) for each in _host_routes} >= {
        ("POST /api/host/subscriptions/{slug}/floor/clear", SubscriptionFloorClearService),
    }
    assert runner_route_dependencies(_host_routes) == []
    assert unresolved_process_dependencies(_host_routes, _process) == []


def test_the_runner_route_gate_fails_a_planted_runner_route_taking_a_container_dependency() -> None:
    planted = [
        RouteDependency("POST /runners/{runner_id}/api/planted", tenant=False, dependency=_Unregistered),
        RouteDependency("POST /api/host/planted", tenant=False, dependency=_Unregistered),
    ]
    assert runner_route_dependencies(planted) == ["POST /runners/{runner_id}/api/planted: _Unregistered"]


def test_the_host_free_graph_refuses_every_host_routes_dependency_as_absent(
    _host_routes: list[RouteDependency],
) -> None:
    host_free = open_host_free()
    try:
        assert unrefused_absent_dependencies(_host_routes, host_free) == []
    finally:
        host_free.close()


def test_the_host_free_gate_fails_an_area_that_declares_nothing_absent(
    _host_routes: list[RouteDependency], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(throttle, "register_absent", _declares_nothing)
    host_free = open_host_free()
    try:
        assert unrefused_absent_dependencies(_host_routes, host_free) == [
            "POST /api/host/subscriptions/{slug}/floor/clear: SubscriptionFloorClearService is not absent: "
            "NoFactoryError"
        ]
    finally:
        host_free.close()


def test_the_host_route_gate_fails_a_planted_unregistered_dependency(_process: OwnedChild) -> None:
    planted = [RouteDependency("GET /planted", tenant=False, dependency=_Unregistered)]
    assert unresolved_process_dependencies(planted, _process) == [
        "GET /planted: _Unregistered in the process: NoFactoryError"
    ]


def test_the_host_route_walk_finds_a_container_dependency_on_a_planted_route() -> None:
    app = FastAPI()

    @app.post("/planted")
    @inject_sync
    def planted(clears: FromDishka[SubscriptionFloorClearService]) -> None:
        del clears

    assert route_dependencies(app) == [
        RouteDependency("POST /planted", tenant=False, dependency=SubscriptionFloorClearService)
    ]
