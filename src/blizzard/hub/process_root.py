"""The hub process's composition root: it registers the hub graph, then opens the validated container over it.

The store-free graph declares each store-backed registration absent, and a graph without settings the settings ports.
Whether the trace and egress exports run is decided once, by the switches the root registers with, from the exporter
and the egress boot outcome it supplies. The process scope borrows every value it opens with; the :class:`HubProcess`
owns only the container, closing its tenant scopes and then the process scope."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx2
from sqlalchemy import Engine

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing import wiring as platform_tracing_wiring
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.foundation.registry.interface import Ref, port
from blizzard.foundation.registry.internal.translation import HUB_LIFETIMES, build_container
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import OwnedChild, Resolver, ScopeValue, ScopeValues, supply
from blizzard.foundation.roles import collaborator
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.hub.api import wiring as api
from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.api.version_window import HubVersionWindow
from blizzard.hub.auth import wiring as auth
from blizzard.hub.auth.wiring import BorrowedOAuthClient
from blizzard.hub.composition import register_legacy, register_legacy_absent
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery import wiring as delivery
from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.documents import wiring as documents
from blizzard.hub.domain.cadence import wiring as cadence_area
from blizzard.hub.domain.chunk import wiring as chunk_area
from blizzard.hub.domain.config import wiring as config_wiring
from blizzard.hub.domain.config_apply import wiring as config_apply
from blizzard.hub.domain.delivery import wiring as delivery_area
from blizzard.hub.domain.execution import wiring as execution
from blizzard.hub.domain.garden import wiring as garden_area
from blizzard.hub.domain.garden.findings import wiring as findings
from blizzard.hub.domain.graph import wiring as graph_area
from blizzard.hub.domain.observability import wiring as observability_area
from blizzard.hub.domain.observability.egress.boot import EgressBoot
from blizzard.hub.domain.observability.tracing.repository import StepHydration, StepHydrations
from blizzard.hub.domain.operations import wiring as operations_area
from blizzard.hub.domain.projects import wiring as projects
from blizzard.hub.domain.runners import wiring as runners
from blizzard.hub.domain.tenancy import wiring as tenancy
from blizzard.hub.domain.tenancy.teardown import ClosedTenantForgetting
from blizzard.hub.domain.urgency import wiring as urgency_area
from blizzard.hub.domain.work_items import wiring as work_items_area
from blizzard.hub.egress import wiring as egress
from blizzard.hub.events import wiring as events
from blizzard.hub.forge import wiring as forge
from blizzard.hub.graph_sync import GraphReconciliation, GraphReconciliations
from blizzard.hub.graphs import PACKAGED as GRAPHS_PACKAGED
from blizzard.hub.graphs import PackagedGraphs
from blizzard.hub.live_config import wiring as live_config
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.scopes import wiring as scopes
from blizzard.hub.scopes.environ import HubEnviron
from blizzard.hub.scopes.tenants import TenantContainers
from blizzard.hub.secrets import wiring as secrets
from blizzard.hub.settings import wiring as settings_wiring
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.store import wiring as store
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.hub_event_log_store import HubEventLogStore
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.system_artifacts import PACKAGED as SYSTEM_ARTIFACTS_PACKAGED
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.tenancy import StoreScope, TenantId, UnknownTenant
from blizzard.hub.work_sources import wiring as work_sources

#: The process scope's owner id.
HUB_OWNER = "hub"


@collaborator
@dataclass(frozen=True)
class HubProcess:
    """The hub's open process scope and the tenant scopes opened beneath it."""

    owner: OwnedChild
    tenants: TenantContainers

    @property
    def resolver(self) -> Resolver:
        """The process scope's guarded resolver."""
        return self.owner.resolver

    @property
    def credentials(self):
        """The hub-scoped credential reads: which tenant a runner or host token, or id, belongs to."""
        return self.resolver.get(HubScopedReads).credentials

    def close(self) -> None:
        """Close every tenant scope, then the process scope, attempting the second when the first fails."""
        try:
            self.tenants.close()
        finally:
            self.owner.close()


def register_hub(
    registry: LegacyRegistry,
    *,
    store_backed: bool,
    settings: bool = False,
    versions: bool = False,
    trace_export: bool = False,
    egress_export: bool = False,
) -> None:
    """Declare the hub graph: the scope inputs, the legacy bridge's transitional ports, the store adapters, the
    process lifecycle owners and the adopted areas over them — or, store-free, each store-backed registration absent.
    The settings ports resolve from the borrowed settings graph when ``settings`` is set, and are absent otherwise,
    whether or not the graph is store-backed. The version window is the supplied one when ``versions`` is set on a
    store-backed graph, and is absent otherwise. The trace export's sweep runs over the supplied exporter when
    ``trace_export`` is set, and the fact export's sweep over its writers when ``egress_export`` is; each sweep is
    ``None`` otherwise."""
    scopes.register(registry)
    documents.register(registry)
    platform_tracing_wiring.register(registry)
    events.register(registry)
    settings_wiring.register_boot(registry)
    forge.register_process(registry)
    if store_backed:
        delivery.register(registry)
        scopes.register_tenants(registry)
        chunk_operations = store.register_chunk_operations(registry)
        hub_events = store.register_process(registry)
        register_legacy(registry)
        delivery.register_tenant(registry)
        delivery_area.register(registry, flows=chunk_operations.flows)
        _register_chunk_operations(registry, chunk_operations)
        auth.register(registry)
        secrets.register(registry)
        findings.register(registry, store.register_findings(registry))
        graph_area.register(registry, store.register_graphs(registry))
        _register_urgency(registry)
        _register_garden(registry, formats=api.register(registry))
        _register_cadence(registry)
        _register_config(registry)
        _register_projects(registry)
        _register_fleet(registry, versions=versions)
        config_apply.register(registry)
        live_config.register(registry)
        forge.register_tenant(registry)
        work_sources.register(registry)
        _register_observability(registry, hub_events=hub_events)
        _register_tenancy(registry)
        _register_exports(registry, trace_export=trace_export, egress_export=egress_export)
    else:
        store.register_process_absent(registry)
        delivery.register_absent(registry)
        scopes.register_tenants_absent(registry)
        register_legacy_absent(registry)
        delivery.register_tenant_absent(registry)
        delivery_area.register_absent(registry)
        store.register_chunk_operations_absent(registry)
        chunk_area.register_absent(registry)
        operations_area.register_absent(registry)
        work_items_area.register_absent(registry)
        auth.register_absent(registry)
        secrets.register_absent(registry)
        store.register_findings_absent(registry)
        findings.register_absent(registry)
        store.register_graphs_absent(registry)
        graph_area.register_absent(registry)
        store.register_urgency_absent(registry)
        urgency_area.register_absent(registry)
        store.register_garden_authoring_absent(registry)
        store.register_garden_materialization_absent(registry)
        store.register_garden_runs_absent(registry)
        garden_area.register_absent(registry)
        store.register_cadence_absent(registry)
        cadence_area.register_absent(registry)
        store.register_config_absent(registry)
        config_wiring.register_absent(registry)
        store.register_projects_absent(registry)
        projects.register_absent(registry)
        store.register_fleet_absent(registry)
        runners.register_absent(registry)
        execution.register_absent(registry)
        api.register_version_window_absent(registry)
        config_apply.register_absent(registry)
        live_config.register_absent(registry)
        forge.register_tenant_absent(registry)
        work_sources.register_absent(registry)
        store.register_observability_absent(registry)
        observability_area.register_absent(registry)
        store.register_exports_absent(registry)
        egress.register_absent(registry)
        observability_area.register_exports_absent(registry)
        api.register_absent(registry)
        store.register_tenancy_absent(registry)
        tenancy.register_absent(registry)
        api.register_graph_sync_absent(registry)
    if store_backed and settings:
        settings_wiring.register(registry)
    else:
        settings_wiring.register_boot_source(registry)
        settings_wiring.register_absent(registry)


def _register_chunk_operations(registry: LegacyRegistry, refs: store.ChunkOperationsRefs) -> None:
    """The chunk, chunk operations and work-items areas, over the tenant's chunk-store bundle and flow and work-item
    stores."""
    chunk_area.register(registry, stores=refs.chunks, submissions=refs.flows, work_items=refs.work_items)
    operations_area.register(registry, withdrawals=refs.work_items)
    work_items_area.register(registry, items=refs.work_items)


def _register_observability(registry: LegacyRegistry, *, hub_events: Ref[HubEventLogStore]) -> None:
    """The observability adapters, and the observability area's ports and services over them and the hub event log."""
    refs = store.register_observability(registry)
    observability_area.register(
        registry,
        segments=refs.segments,
        events=refs.events,
        event_queries=refs.event_queries,
        operational=refs.operational,
        env_timeline=refs.env_timeline,
        annotations=refs.annotations,
        hub_events=hub_events,
        invariants=refs.invariants,
    )


def _register_tenancy(registry: LegacyRegistry) -> None:
    """The tenancy adapters, the tenancy area's services over them, and the graph sync its tenant creation mints
    through."""
    refs = store.register_tenancy(registry)
    tenancy.register(registry, directory=refs.directory, teardowns=refs.teardowns, units=refs.units)
    api.register_graph_sync(registry)


def _register_exports(registry: LegacyRegistry, *, trace_export: bool, egress_export: bool) -> None:
    """The trace and egress stores and the egress adapters, and the observability area's exports over them — each
    export's sweep present only when its switch is set."""
    refs = store.register_exports(registry)
    free_space = egress.register_probe(registry)
    writers: observability_area.EgressWriterRefs | None = None
    if egress_export:
        held = egress.register_writers(registry)
        writers = (held.configured, held.backfill)
    else:
        egress.register_writers_absent(registry)
    observability_area.register_exports(
        registry,
        trace=refs.trace,
        egress=refs.egress,
        project_labels=refs.project_labels,
        free_space=free_space,
        trace_export=trace_export,
        egress_writers=writers,
    )


def _register_projects(registry: LegacyRegistry) -> None:
    """The project adapters, and the projects area's ports and services over them."""
    refs = store.register_projects(registry)
    projects.register(
        registry,
        projects=refs.projects,
        links=refs.links,
        positions=refs.positions,
        chunk_targets=refs.chunk_targets,
    )


def _register_fleet(registry: LegacyRegistry, *, versions: bool) -> None:
    """The fleet registry adapters, the runners area's ports and services over them, and the execution area's fleet
    services over the version window the root supplies — or none, its window absent."""
    refs = store.register_fleet(registry)
    runners.register(registry, runners=refs.runners, hosts=refs.hosts)
    if versions:
        execution.register(registry, versions=api.register_version_window(registry))
    else:
        api.register_version_window_absent(registry)
        execution.register(registry, versions=None)


def _register_garden(registry: LegacyRegistry, *, formats: Ref[GardenFormats]) -> None:
    """The garden authoring adapters, and the garden area's ports and services over them and the API edge's artifact
    formats."""
    refs = store.register_garden_authoring(registry)
    materialization = store.register_garden_materialization(registry)
    runs = store.register_garden_runs(registry)
    garden_area.register(
        registry,
        scopes=refs.scopes,
        routines=refs.routines,
        routine_scopes=refs.routine_scopes,
        run_contexts=refs.run_contexts,
        proposals=refs.proposals,
        closures=refs.closures,
        finding_sets=refs.finding_sets,
        deliveries=materialization.delivery,
        review_findings=materialization.review_findings,
        formats=formats,
        trends=runs.trends,
        sweeps=runs.sweeps,
        runs=runs.runs,
    )


def _register_cadence(registry: LegacyRegistry) -> None:
    """The cadence adapters, and the cadence area's ports and services over them."""
    refs = store.register_cadence(registry)
    cadence_area.register(registry, cadences=refs.cadences, runs=refs.runs)


def _register_urgency(registry: LegacyRegistry) -> None:
    """The urgency adapters, and the urgency area's ports and services over them."""
    refs = store.register_urgency(registry)
    urgency_area.register(
        registry, urgencies=refs.urgencies, order=refs.order, settings=refs.settings, chunks=refs.chunks
    )


def _register_config(registry: LegacyRegistry) -> None:
    """The configured-record adapters, and the config area's ports over them."""
    refs = store.register_config(registry)
    config_wiring.register(
        registry,
        work_sources=refs.work_sources,
        repositories=refs.repositories,
        forges=refs.forges,
        credentials=refs.credentials,
        secrets=refs.secrets,
        sealed=refs.secrets,
        secret_references=refs.secret_references,
        forge_references=refs.forge_references,
        credential_references=refs.credential_references,
        changes=refs.changes,
        apply_writer=refs.apply_writer,
        import_writer=refs.import_writer,
        revisions=refs.revisions,
    )


def process_values(
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> list[ScopeValue[Any]]:
    """The scope values every hub graph opens with."""
    return [
        supply(port(IClock), clock),
        supply(port(HubConfig), config),
        supply(port(IPlatformTracing), platform_tracing),
        supply(port(HubEnviron), HubEnviron(environ)),
        supply(port(HubActions), actions),
        supply(port(PackagedGraphs), graphs),
        supply(port(PackagedSystemArtifacts), system_artifacts),
    ]


def store_backed_values(
    connections: HubStoreConnections,
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    settings: HubSettingsGraph | None = None,
    versions: HubVersionWindow | None = None,
    oauth_http_client: httpx2.Client | None = None,
    trace_exporter: ITraceExporter | None = None,
    tracing: TracingSettings | None = None,
    egress_boot: EgressBoot | None = None,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> ScopeValues:
    """The store-backed graph's scope values; ``settings`` is supplied only to a graph declaring the settings ports, and
    ``versions`` only to one declaring the version window. ``oauth_http_client`` is the OAuth client the caller lends,
    left ``None`` for the hub to open its own. ``trace_exporter`` is supplied only to a graph whose trace export runs;
    without ``tracing`` or ``egress_boot``, tracing is disabled and the fact export off."""
    values: list[ScopeValue[Any]] = [
        supply(port(HubStoreConnections), connections),
        supply(port(Engine), connections.engine),
        supply(port(BorrowedOAuthClient), BorrowedOAuthClient(oauth_http_client)),
        supply(port(TracingSettings), tracing if tracing is not None else TracingSettings("disabled")),
        supply(port(EgressBoot), egress_boot if egress_boot is not None else EgressBoot.off()),
        supply(port(StepHydrations), StepHydrations()),
        supply(port(GraphReconciliations), GraphReconciliations()),
        supply(port(ClosedTenantForgetting), ClosedTenantForgetting()),
        *process_values(
            clock=clock,
            config=config,
            platform_tracing=platform_tracing,
            environ=environ,
            actions=actions,
            graphs=graphs,
            system_artifacts=system_artifacts,
        ),
    ]
    if settings is not None:
        values.append(supply(port(HubSettingsGraph), settings))
    if versions is not None:
        values.append(supply(port(HubVersionWindow), versions))
    if trace_exporter is not None:
        values.append(supply(port(ITraceExporter), trace_exporter))
    return _scoped(values)


def build_store_backed(
    registry: LegacyRegistry,
    connections: HubStoreConnections,
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    settings: HubSettingsGraph | None = None,
    versions: HubVersionWindow | None = None,
    oauth_http_client: httpx2.Client | None = None,
    trace_exporter: ITraceExporter | None = None,
    tracing: TracingSettings | None = None,
    egress_boot: EgressBoot | None = None,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubProcess:
    """Open the store-backed graph ``registry`` declares over the borrowed ``connections`` and process inputs."""
    values = store_backed_values(
        connections,
        clock=clock,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        actions=actions,
        settings=settings,
        versions=versions,
        oauth_http_client=oauth_http_client,
        trace_exporter=trace_exporter,
        tracing=tracing,
        egress_boot=egress_boot,
        graphs=graphs,
        system_artifacts=system_artifacts,
    )
    return store_backed_process(build_container(registry, HUB_LIFETIMES, owner=HUB_OWNER, values=values))


def store_backed_process(owner: OwnedChild) -> HubProcess:
    """The store-backed :class:`HubProcess` over its opened container, which is closed if the tenant reads fail. Its
    step hydration and graph reconciliation accessors are bound over its tenant scopes, and its closed-tenant
    forgetting over them and the open tenants, before it is handed out."""
    try:
        reads = owner.resolver.get(HubScopedReads)
        hydrations = owner.resolver.get(StepHydrations)
        reconciliations = owner.resolver.get(GraphReconciliations)
    except BaseException:
        owner.close()
        raise
    process = _process(owner, confirm_open=reads.tenants.confirm_open, open_tenants=reads.tenants.open_tenants)
    tenants = process.tenants
    hydrations.bind(lambda tenant: tenants.resolve(StoreScope(tenant), StepHydration))
    reconciliations.bind(lambda tenant: tenants.resolve(StoreScope(tenant), GraphReconciliation))
    bind_closed_tenant_forgetting(process)
    return process


def bind_closed_tenant_forgetting(process: HubProcess, *, legacy: Callable[[TenantId], None] | None = None) -> None:
    """Bind ``process``'s closed-tenant forgetting: a closed tenant's container scope goes first, whose finalizers close
    its configured clients and drop its broker; then ``legacy``, the legacy cache's entry, when the legacy core holds
    one; and the in-memory open set is read again last, so a request resolving the tenant meanwhile misses both caches
    and is refused by the store. Binding again replaces the binding."""
    tenants = process.tenants
    open_tenants = process.resolver.get(OpenTenants)

    def forget(tenant: TenantId) -> None:
        tenants.evict(tenant)
        if legacy is not None:
            legacy(tenant)
        open_tenants.refresh()

    process.resolver.get(ClosedTenantForgetting).bind(forget)


def build_store_free(
    registry: LegacyRegistry,
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubProcess:
    """Open the store-free graph ``registry`` declares; it holds no tenant, so every tenant open is refused."""
    values = process_values(
        clock=clock,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        actions=actions,
        graphs=graphs,
        system_artifacts=system_artifacts,
    )
    owner = build_container(registry, HUB_LIFETIMES, owner=HUB_OWNER, values=_scoped(values))
    return _process(owner, confirm_open=_no_tenant, open_tenants=tuple)


def open_store_backed(
    connections: HubStoreConnections,
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    settings: HubSettingsGraph | None = None,
    versions: HubVersionWindow | None = None,
    oauth_http_client: httpx2.Client | None = None,
    trace_exporter: ITraceExporter | None = None,
    tracing: TracingSettings | None = None,
    egress_boot: EgressBoot | None = None,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubProcess:
    """The hub's store-backed process, registered and opened; the settings ports are present only with ``settings``,
    and the version window only with ``versions``. The trace export runs only over a lent ``trace_exporter``, and the
    fact export only when ``egress_boot`` booted it on; a root that lends neither serves both off."""
    return build_store_backed(
        registered(
            store_backed=True,
            settings=settings is not None,
            versions=versions is not None,
            trace_export=trace_exporter is not None,
            egress_export=egress_boot is not None and egress_boot.on(),
        ),
        connections,
        clock=clock,
        graphs=graphs,
        system_artifacts=system_artifacts,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        actions=actions,
        settings=settings,
        versions=versions,
        oauth_http_client=oauth_http_client,
        trace_exporter=trace_exporter,
        tracing=tracing,
        egress_boot=egress_boot,
    )


def open_store_free(
    *,
    clock: IClock,
    config: HubConfig,
    platform_tracing: IPlatformTracing,
    environ: Mapping[str, str],
    actions: HubActions,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubProcess:
    """The hub's store-free process, registered and opened."""
    return build_store_free(
        registered(store_backed=False),
        clock=clock,
        config=config,
        platform_tracing=platform_tracing,
        environ=environ,
        actions=actions,
        graphs=graphs,
        system_artifacts=system_artifacts,
    )


def registered(
    *,
    store_backed: bool,
    settings: bool = False,
    versions: bool = False,
    trace_export: bool = False,
    egress_export: bool = False,
) -> LegacyRegistry:
    """A fresh registry holding the hub graph :func:`register_hub` declares."""
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


def _scoped(values: list[ScopeValue[Any]]) -> ScopeValues:
    return ScopeValues(tuple(values))


def _process(
    owner: OwnedChild, *, confirm_open: Callable[[TenantId], None], open_tenants: Callable[[], Iterable[TenantId]]
) -> HubProcess:
    return HubProcess(owner, TenantContainers(owner, confirm_open=confirm_open, open_tenants=open_tenants))


def _no_tenant(tenant: TenantId) -> None:
    raise UnknownTenant(tenant)
