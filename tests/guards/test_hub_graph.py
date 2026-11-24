"""The hub's whole registered graph, store-backed and store-free, validates and keeps every registration rule.

Each gate runs over the real hub registration and over that registration with a planted violation it must fail."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, FastAPI

from blizzard import __version__
from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing, IPlatformTracing
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, ScopeValues, supply
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.hub.api.auth_session import tenant_bound
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.version_window import HubVersionWindow
from blizzard.hub.app import create_app_for_export
from blizzard.hub.auth.hub_admins import HubAdministrators
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.invitations import InvitationService
from blizzard.hub.auth.memberships import TenantMembers
from blizzard.hub.auth.sign_in import SignIn
from blizzard.hub.auth.wiring import BorrowedOAuthClient
from blizzard.hub.composition import build_hub_settings
from blizzard.hub.config import HubConfig
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.delivery.action_registry import SHIPPED_ACTIONS, HubActions
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ports.escalations import IReadChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.events import IReadChunkEventsRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository
from blizzard.hub.domain.chunk.ports.usage import IReadChunkUsageRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import IReadConfigChanges
from blizzard.hub.domain.config.credentials import IReadCredentialRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.config.secrets import ISecretCatalog
from blizzard.hub.domain.config_apply.service import ConfigApplication
from blizzard.hub.domain.execution.fleet import FleetService
from blizzard.hub.domain.execution.host_facts import HostFactIngestService
from blizzard.hub.domain.execution.host_retirement import HostRetirementService
from blizzard.hub.domain.garden.findings.model import FindingExitService, IReadFindingRepository
from blizzard.hub.domain.graph.authoring import DefaultGraphService
from blizzard.hub.domain.observability.analytics.derivation import EventDerivationService
from blizzard.hub.domain.observability.analytics.operational import IReadOperationalAnalytics
from blizzard.hub.domain.observability.analytics.queries import IReadAnalyticsEventQueries
from blizzard.hub.domain.observability.egress.backfill import EgressBackfill
from blizzard.hub.domain.observability.egress.boot import EgressBoot, EgressPathKey
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.reset import EgressReset
from blizzard.hub.domain.observability.egress.status import EgressStatusReader
from blizzard.hub.domain.observability.env_timeline import EnvTimelineReading
from blizzard.hub.domain.observability.hub_events import IReadHubEventLog
from blizzard.hub.domain.observability.invariants import HubInvariantCheck
from blizzard.hub.domain.observability.tracing.replay import TraceReplay
from blizzard.hub.domain.observability.tracing.repository import StepHydrations
from blizzard.hub.domain.observability.tracing.status import TraceStatusReader
from blizzard.hub.domain.observability.transcripts import IReadTranscriptSegments, TranscriptIngestService
from blizzard.hub.domain.projects.authoring import ProjectAuthoring
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.domain.runners.enrollment import RunnerEnrollmentService
from blizzard.hub.domain.runners.host_enrollment import HostEnrollmentService
from blizzard.hub.domain.runners.host_report import HostReportService
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry
from blizzard.hub.domain.tenancy.teardown import ClosedTenantForgetting
from blizzard.hub.domain.urgency.services import UrgencyReading
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.forge.budget import ForgeBudgets
from blizzard.hub.forge.readiness import CredentialReadiness
from blizzard.hub.graph_sync import GraphReconciliations
from blizzard.hub.graphs import PACKAGED as GRAPHS_PACKAGED
from blizzard.hub.graphs import PackagedGraphs
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.process_root import register_hub
from blizzard.hub.scopes.environ import HubEnviron
from blizzard.hub.settings.footing import HubSettingsFooting
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.system_artifacts import PACKAGED as SYSTEM_ARTIFACTS_PACKAGED
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.work_sources.source import IWorkSourceRegistry
from tests.support.config_inputs import VERSION_WINDOW
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
from tests.support.scaffolding import InMemoryTraceExporter, hub_migration_prototype


def _hub(
    *,
    store_backed: bool,
    settings: bool = False,
    versions: bool = False,
    trace_export: bool = False,
    egress_export: bool = False,
) -> LegacyRegistry:
    registry = LegacyRegistry()
    register_hub(
        registry,
        store_backed=store_backed,
        settings=settings,
        versions=versions,
        trace_export=trace_export,
        egress_export=egress_export,
    )
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


def _second_memberships(connections: HubStoreConnections) -> MembershipRepository:
    return MembershipRepository(connections)


@pytest.mark.parametrize(
    ("store_backed", "settings", "versions", "exports"),
    [
        (True, True, True, False),
        (True, True, True, True),
        (True, False, True, False),
        (True, True, False, False),
        (False, False, False, False),
    ],
    ids=[
        "store-backed",
        "store-backed-exporting",
        "store-backed-settings-less",
        "store-backed-windowless",
        "store-free",
    ],
)
def test_the_hub_graph_builds_and_keeps_every_registration_rule(
    store_backed: bool, settings: bool, versions: bool, exports: bool
) -> None:
    registry = _hub(
        store_backed=store_backed, settings=settings, versions=versions, trace_export=exports, egress_export=exports
    )
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
    registry.leaf(_second_memberships, lifetime=Lifetime.PROCESS)
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


@pytest.fixture(params=[False, True], ids=["exports-off", "exports-on"])
def _process(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[OwnedChild]:
    # The egress backfill reads the live config as it is built, so the settings graph reads a migrated store.
    shutil.copyfile(hub_migration_prototype(), tmp_path / "hub.db")
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")
    connections = HubStoreConnections(engine, HubStoreErrorFactory(get_logger("test")))
    clock = SystemClock()
    footing = HubSettingsFooting(Path(), None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    exports: bool = request.param
    exporting = (supply(port(ITraceExporter), InMemoryTraceExporter()),) if exports else ()
    boot = EgressBoot(EgressConfig(directory=tmp_path), EgressPathKey()) if exports else EgressBoot.off()
    owner = build_container(
        _hub(store_backed=True, settings=True, versions=True, trace_export=exports, egress_export=exports),
        HUB_LIFETIMES,
        owner="guard",
        values=ScopeValues(
            (
                supply(port(HubStoreConnections), connections),
                supply(port(IClock), clock),
                supply(port(HubSettingsGraph), build_hub_settings(connections, clock, footing)),
                supply(port(HubConfig), HubConfig(root=Path(), db_url="sqlite://")),
                supply(port(IPlatformTracing), DisabledPlatformTracing()),
                supply(port(HubEnviron), HubEnviron({})),
                supply(port(BorrowedOAuthClient), BorrowedOAuthClient(None)),
                supply(port(HubActions), SHIPPED_ACTIONS),
                supply(port(PackagedGraphs), GRAPHS_PACKAGED),
                supply(port(PackagedSystemArtifacts), SYSTEM_ARTIFACTS_PACKAGED),
                supply(port(HubVersionWindow), HubVersionWindow(__version__, VERSION_WINDOW)),
                supply(port(TracingSettings), TracingSettings("disabled")),
                supply(port(EgressBoot), boot),
                supply(port(StepHydrations), StepHydrations()),
                supply(port(GraphReconciliations), GraphReconciliations()),
                supply(port(ClosedTenantForgetting), ClosedTenantForgetting()),
                *exporting,
            )
        ),
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
        ("GET /api/config/export", UrgencyReading),
        ("POST /api/config/apply", ConfigApplication),
        ("GET /api/config/changes", IReadConfigChanges),
        ("GET /api/secrets", ISecretCatalog),
        ("GET /api/forges/{forge}", ForgeBudgets),
        ("POST /api/forges", ConfigAuthoring),
        ("POST /api/credentials/{credential}/checks", CredentialReadiness),
        ("GET /api/credentials/{credential}", IReadCredentialRepository),
        ("GET /api/repositories/{repository}", IReadRepositoryRecordRepository),
        ("PATCH /api/repositories/{repository}", EventLogService),
        ("GET /api/work-sources", IWorkSourceRegistry),
        ("POST /api/work-sources/{source}/items", DefaultGraphService),
        ("DELETE /api/work-sources/{source}/items/{ref}", ChunkFrameLoader),
        ("GET /api/work-sources/{source}/items", IReadChunkWorkRefsRepository),
        ("POST /api/projects", ProjectAuthoring),
        ("GET /api/projects", IReadProjectRepository),
        ("GET /api/runners", FleetService),
        ("POST /api/runners", RunnerEnrollmentService),
        ("POST /api/hosts", HostEnrollmentService),
        ("POST /api/hosts/{host_id}/retire", HostRetirementService),
        ("POST /api/fleet/host", HostReportService),
        ("POST /api/fleet/host/events", HostFactIngestService),
        ("POST /api/fleet/runners", FleetService),
    }
    narrowed = ("/api/findings", "/api/config", "/api/secrets", "/api/forges", "/api/credentials", "/api/repositories")
    narrowed += (
        "/api/work-sources",
        "/api/projects",
        "/api/hosts",
        "/api/fleet/host",
        "/api/members",
        "/api/users",
        "/api/invitations",
    )
    assert all(each.tenant for each in _hub_routes if each.route.split(" ")[1].startswith(narrowed))
    hub_only = ("/api/auth", "/api/me", "/api/admin/hub-admins", "/api/fleet/identity")
    assert [
        each.route
        for each in _hub_routes
        if each.tenant
        and any(each.route.split(" ")[1] == p or each.route.split(" ")[1].startswith(f"{p}/") for p in hub_only)
    ] == []
    assert {(each.route, each.dependency) for each in _hub_routes} >= {
        ("GET /api/fleet/identity", OpenTenants),
        ("PUT /api/admin/hub-admins/{user_id}", HubAdministrators),
        ("GET /api/auth/{name}/callback", SignIn),
        ("GET /api/members", TenantMembers),
        ("POST /api/users/{user_id}/role", EventBroker),
        ("POST /api/invitations", InvitationService),
    }
    assert {(each.route, each.dependency) for each in _hub_routes} >= {
        ("GET /api/events/stream", EventBroker),
        ("GET /api/events/stream", IReadRunnerRegistry),
        ("GET /api/events", IReadChunkEventsRepository),
        ("GET /api/events", IReadChunkEscalationsRepository),
        ("GET /api/activity", IClock),
        ("GET /api/chunks/{chunk_id}/transcripts", IReadTranscriptSegments),
        ("GET /api/env-timeline", EnvTimelineReading),
        ("GET /api/env-timeline/step/transcript", IReadTranscriptSegments),
        ("GET /api/spend", IReadChunkUsageRepository),
        ("POST /api/analytics/re-derive", EventDerivationService),
        ("GET /api/analytics/events/ndjson", IReadAnalyticsEventQueries),
        ("GET /api/analytics/spend/chunks/ndjson", IReadOperationalAnalytics),
        ("GET /api/admin/events", IReadHubEventLog),
        ("GET /api/admin/invariants", HubInvariantCheck),
        ("GET /api/traces/status", TraceStatusReader),
        ("POST /api/traces/replay", TraceReplay),
        ("GET /api/egress/status", EgressStatusReader),
        ("POST /api/egress/reset", EgressReset),
        ("POST /api/egress/backfill", EgressBackfill),
        ("GET /api/fleet/chunk-statuses", IReadChunkRouteRepository),
        ("POST /api/fleet/transcripts", TranscriptIngestService),
        ("GET /api/fleet/chunks/{chunk_id}/analytics/counts/files", IReadAnalyticsEventQueries),
        ("GET /api/fleet/chunks/{chunk_id}/analytics/spend/nodes", IReadOperationalAnalytics),
    }
    assert [each.route for each in _hub_routes if each.route.split(" ")[1] == "/api/admin/events" and each.tenant] == []
    exports = ("/api/traces", "/api/egress")
    assert [each.route for each in _hub_routes if each.route.split(" ")[1].startswith(exports) and each.tenant] == []
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
