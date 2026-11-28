"""The runner host's registered graph validates and keeps every registration rule, and every host-root route's
container dependency resolves from the host's process scope.

Each gate runs over the production host registration and over that registration with a planted violation it must
fail. The legacy leaves the root declares over its borrowed host are inventoried, not refused: the runner children
still hold them."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import FastAPI

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.translation import HOST_LIFETIMES
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, supply
from blizzard.runner import composition
from blizzard.runner.app import create_app_for_export
from blizzard.runner.composition import HostProcess, RunnerCompose
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_root import HostRoot, register_host
from blizzard.runner.throttle.floor_clear import IRecordSubscriptionFloorClear, SubscriptionFloorClearService
from blizzard.runner.throttle.usage_limits import IWriteSubscriptionUsageLimitRepository
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
from tests.support.route_resolution import RouteDependency, route_dependencies, unresolved_process_dependencies
from tests.support.runner_fakes import make_store

#: The context values the host root opens with; validation reads only their keys.
_VALUES = ScopeValues(
    (
        supply(port(HostProcess), object()),
        supply(port(HostConfig), object()),
        supply(port(RunnerCompose), RunnerCompose()),
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
    assert legacy_registrations(_host()) == [
        "RunnerProcess is a legacy leaf",
        "IWriteSubscriptionUsageLimitRepository is a legacy leaf",
        "IClock is a legacy leaf",
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
    assert [each.route for each in _host_routes if each.route.split(" ")[1].startswith("/runners/")] == []
    assert unresolved_process_dependencies(_host_routes, _process) == []


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
