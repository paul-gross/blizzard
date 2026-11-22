"""The hub's whole registered graph, store-backed and store-free, validates and keeps every registration rule.

Each gate runs over the real hub registration and over that registration with a planted violation it must fail."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, FastAPI

from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, supply
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.api.auth_session import tenant_bound
from blizzard.hub.app import create_app_for_export
from blizzard.hub.composition import memberships_leaf
from blizzard.hub.domain.garden.findings.model import FindingExitService, IReadFindingRepository
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.process_root import register_hub
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from tests.support.container_guards import (
    alias_identity_violations,
    default_factory_violations,
    duplicate_owners,
    graph_builds,
    lifetime_violations,
    role_key_violations,
)
from tests.support.legacy_inventory import LEGACY_INVENTORY, inventory_violations
from tests.support.route_resolution import RouteDependency, route_dependencies, unresolved_route_dependencies


def _hub(*, store_backed: bool) -> LegacyRegistry:
    registry = LegacyRegistry()
    register_hub(registry, store_backed=store_backed)
    return registry


class _Unregistered:
    pass


class _NeedsUnregistered:
    def __init__(self, missing: _Unregistered) -> None:
        self.missing = missing


class _Defaulted:
    def __init__(self, reads: HubScopedReads, limit: int = 3) -> None:
        self.reads = reads
        self.limit = limit


def _second_open_tenants(reads: HubScopedReads) -> OpenTenants:
    return OpenTenants(reads.tenants.open_tenants)


@pytest.mark.parametrize("store_backed", [True, False], ids=["store-backed", "store-free"])
def test_the_hub_graph_builds_and_keeps_every_registration_rule(store_backed: bool) -> None:
    registry = _hub(store_backed=store_backed)
    assert graph_builds(registry, HUB_LIFETIMES, ScopeValues()) == []
    assert duplicate_owners(registry) == []
    assert lifetime_violations(registry, HUB_LIFETIMES) == []
    assert alias_identity_violations(registry) == []
    assert role_key_violations(registry) == []
    assert default_factory_violations(registry) == []


@pytest.mark.parametrize("store_backed", [True, False], ids=["store-backed", "store-free"])
def test_the_hub_graph_gate_fails_a_planted_unresolvable_dependency(store_backed: bool) -> None:
    registry = _hub(store_backed=store_backed)
    registry.service(_NeedsUnregistered, lifetime=Lifetime.TENANT)
    assert len(graph_builds(registry, HUB_LIFETIMES, ScopeValues())) == 1


def test_the_hub_graph_gate_fails_a_planted_second_owner_and_role_key() -> None:
    registry = _hub(store_backed=True)
    registry.leaf(_second_open_tenants, lifetime=Lifetime.PROCESS)
    assert duplicate_owners(registry) == ["OpenTenants has 2 owners"]
    assert role_key_violations(registry) == ["OpenTenants is the key of 2 registrations"]


def test_the_hub_graph_gate_fails_a_planted_alias_onto_a_doubled_owner() -> None:
    registry = _hub(store_backed=True)
    registry.leaf(memberships_leaf, lifetime=Lifetime.PROCESS)
    assert alias_identity_violations(registry) == [
        "alias IReadMembershipRepository targets MembershipRepository, which has 2 owners"
    ]


def test_the_hub_graph_gate_fails_a_planted_off_path_lifetime_and_default() -> None:
    registry = _hub(store_backed=True)
    registry.service(_Unregistered, lifetime=Lifetime.RUNNER)
    registry.service(_Defaulted, lifetime=Lifetime.PROCESS)
    assert len(lifetime_violations(registry, HUB_LIFETIMES)) == 1
    assert default_factory_violations(registry) == ["_Defaulted parameter 'limit' has a default"]


@pytest.fixture(scope="module")
def _hub_routes() -> list[RouteDependency]:
    return route_dependencies(create_app_for_export(packaged_flag_registry()))


@pytest.fixture
def _process() -> Iterator[OwnedChild]:
    engine = create_engine_from_url("sqlite://")
    connections = HubStoreConnections(engine, HubStoreErrorFactory(get_logger("test")))
    owner = build_container(
        _hub(store_backed=True),
        HUB_LIFETIMES,
        owner="guard",
        values=ScopeValues((supply(port(HubStoreConnections), connections), supply(port(IClock), SystemClock()))),
    )
    try:
        yield owner
    finally:
        owner.close()
        engine.dispose()


def test_every_routes_container_dependencies_resolve_at_the_lifetime_it_runs_in(
    _hub_routes: list[RouteDependency], _process: OwnedChild
) -> None:
    assert {(each.route, each.dependency) for each in _hub_routes} >= {
        ("GET /api/findings", IReadFindingRepository),
        ("POST /api/findings/resolve", FindingExitService),
    }
    assert all(each.tenant for each in _hub_routes if each.route.split(" ")[1].startswith("/api/findings"))
    assert unresolved_route_dependencies(_hub_routes, _process) == []


def test_the_every_route_gate_fails_a_planted_unregistered_and_a_tenant_service_off_a_tenant(
    _process: OwnedChild,
) -> None:
    planted = [
        RouteDependency("GET /planted/unregistered", tenant=True, dependency=_Unregistered),
        RouteDependency("GET /planted/hub-level", tenant=False, dependency=FindingExitService),
    ]
    assert unresolved_route_dependencies(planted, _process) == [
        "GET /planted/unregistered: _Unregistered in a tenant request: NoFactoryError",
        "GET /planted/hub-level: FindingExitService in the process: NoFactoryError",
    ]


def test_the_every_route_walk_finds_a_container_dependency_on_a_planted_route() -> None:
    app = FastAPI()
    tenant_router = APIRouter(dependencies=[Depends(tenant_bound)])

    @tenant_router.get("/planted/tenant")
    @inject_sync
    def tenant_route(exits: FromDishka[FindingExitService]) -> None:
        del exits

    @app.get("/planted/process")
    @inject_sync
    def process_route(reads: FromDishka[HubScopedReads]) -> None:
        del reads

    app.include_router(tenant_router)
    assert route_dependencies(app) == [
        RouteDependency("GET /planted/process", tenant=False, dependency=HubScopedReads),
        RouteDependency("GET /planted/tenant", tenant=True, dependency=FindingExitService),
    ]


def test_the_registry_declares_only_the_inventoried_legacy_leaves_and_bags() -> None:
    assert inventory_violations(_hub(store_backed=True)) == []


def test_the_inventory_gate_fails_a_planted_unlisted_leaf_and_a_stale_entry() -> None:
    registry = _hub(store_backed=True)
    registry.leaf(_unlisted_leaf, lifetime=Lifetime.PROCESS)
    assert inventory_violations(registry, {**LEGACY_INVENTORY, _Unregistered: "nowhere"}) == [
        "_Defaulted is an unlisted legacy leaf",
        "_Unregistered (nowhere) is listed but no longer declared",
    ]


def _unlisted_leaf(reads: HubScopedReads) -> _Defaulted:
    return _Defaulted(reads)
