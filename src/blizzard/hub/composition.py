"""The hub's fleet-service composition (``bzh:dependency-injection``).

One place the store-backed collaborators are constructed and injected. Controllers read
the stores through their **read** Protocols and mutate only through the services
(``bzh:controller-read-only``); every chunk seam below — ``TenantServices.chunks``'s bundle and
every domain service's own narrow parameter alike — is the same instance built here."""

from __future__ import annotations

# The tenant cache's lock guards this process's memory, not a store decision.
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import httpx2
from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing, IPlatformTracing
from blizzard.foundation.registry.interface import Lifetime, port
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import Resolver
from blizzard.foundation.roles import collaborator
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.hub.api.chunk_events import AnnouncedTurns
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.version_window import HubVersionWindow
from blizzard.hub.auth.invitations import IWriteInvitationRepository
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_registry import HubActions, build_hub_actions
from blizzard.hub.delivery.flow_driver import FlowDriver, FlowLineStores, ITurnRunner, StepWake
from blizzard.hub.documents.codec import IConfigCodec
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import WorkRefLabel
from blizzard.hub.domain.chunk.ports.commit_links import IReadLandedCommitLinks
from blizzard.hub.domain.chunk.ports.stores import ChunkReadStores, ChunkStores
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import IReadConfigChanges
from blizzard.hub.domain.config.credentials import IReadCredentialRepository
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.config.secrets import ISecretCatalog
from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository
from blizzard.hub.domain.delivery.ports.flows import IFlowChangePublisher, IReadFlowLinesCorpus, IReadFlows
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import (
    IReadFindingRepository,
)
from blizzard.hub.domain.graph.authoring import DefaultGraphService, GraphMintService
from blizzard.hub.domain.graph.model import IReadGraphBodies, IReadGraphRepository, IReadNodeExecutors
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.observability.egress.boot import EgressPathKey
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.domain.projects.targets import ChunkTargets
from blizzard.hub.domain.runners.host import IReadHostRegistry
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry
from blizzard.hub.domain.urgency.services import (
    UrgencyReading,
)
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.egress.factory import egress_boot
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.forge.budget import ForgeBudgets
from blizzard.hub.forge.budget_notices import BudgetNoticeDrain
from blizzard.hub.forge.commit_links import LandedCommitLinks
from blizzard.hub.forge.registry import ForgeRegistry
from blizzard.hub.graphs import PACKAGED as GRAPHS_PACKAGED
from blizzard.hub.graphs import PackagedGraphs
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.settings.footing import HubSettingsFooting
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.settings.reader import HubSettingsReader
from blizzard.hub.settings.service import HubSettingsAuthoring
from blizzard.hub.store import wiring as store_wiring
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.config_apply_store import ConfigApplyStore
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.config_import_store import ConfigImportStore
from blizzard.hub.store.internal.credential_record_store import CredentialRecordStore
from blizzard.hub.store.internal.credential_referrers import CredentialReferrersStore
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.forge_referrers import ForgeReferrersStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.store.internal.hub_settings_store import HubSettingsStore
from blizzard.hub.store.internal.invitation_store import InvitationStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.secret_referrers import SecretReferrersStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.urgency_store import UrgencyOrderStore, UrgencyStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.tenant_guard import install_tenant_guard
from blizzard.hub.store.transactions import HubTransactions
from blizzard.hub.system_artifacts import PACKAGED as SYSTEM_ARTIFACTS_PACKAGED
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.tenancy import STORE_UNAVAILABLE, StoreScope, TenantId
from blizzard.hub.work_sources.source import IWorkSourceRegistry

if TYPE_CHECKING:
    from blizzard.hub.process_root import HubProcess


@dataclass(frozen=True)
class TenantServices:
    """One tenant's wired services — the bag a tenant's requests are served by, built from its
    :class:`TenantStores` by :func:`build_tenant_services` and cached beside them."""

    #: The controller-facing chunk-seam bundle — every field typed to its
    #: concept's read Protocol only (``bzh:controller-read-only``), so a route handler
    #: mutating through it fails type-check; the domain services below hold their own
    #: narrower, write-capable seams directly.
    chunks: ChunkReadStores
    graphs: IReadGraphRepository
    #: Each mint's stored authoring body.
    graph_bodies: IReadGraphBodies
    #: Where each node name runs across the tenant's non-retired graphs — what a runner's gates are flagged against.
    node_executors: IReadNodeExecutors
    graph_mint: GraphMintService
    default_graph: DefaultGraphService
    #: The YAML binding of the config codec a graph definition is decoded and stored through.
    graph_yaml: IConfigCodec
    #: The fleet registry's read Protocol — the same store instance as the fleet service's writes.
    registry: IReadRunnerRegistry
    #: The host registry's read Protocol — the same store instance as the host services' writes.
    hosts: IReadHostRegistry
    #: Wakes the hosted hub's flow driver; nothing elsewhere.
    hub_step_wake: StepWake
    events: EventBroker
    #: The reads a ``chunk-changed`` frame is enriched from, over ``events``.
    chunk_frames: ChunkFrameLoader
    #: The one event-log recorder every hub-side authoring site shares.
    event_log: EventLogService
    clock: IClock
    work_sources: IWorkSourceRegistry
    #: Each repository's forge roles, built from its records.
    forges: ForgeRegistry
    #: Each forge key's rate budget, shared by every caller of that forge and credential.
    forge_budgets: ForgeBudgets
    #: Renders a work ref's source-native token.
    work_ref_label: WorkRefLabel
    #: The user read repository — every write still goes through ``auth``.
    users: IReadUserRepository
    #: Secret metadata only — never a sealed row (``bzh:secret-write-only``).
    secret_catalog: ISecretCatalog
    #: The one writer of configured records — forges, work sources, repositories and secrets, each write with its change
    #: row.
    config_authoring: ConfigAuthoring
    #: Stored work-source records (the read half).
    work_source_records: IReadWorkSourceRepository
    #: Stored repository records (the read half).
    repository_records: IReadRepositoryRecordRepository
    #: Stored forge records (the read half).
    forge_records: IReadForgeRepository
    #: Stored credential records (the read half).
    credential_records: IReadCredentialRepository
    #: Each chunk's landed-commit address on its repository record's forge.
    commit_links: IReadLandedCommitLinks
    #: Each branch's delivery line and the visits submitted to it (the read half).
    flows: IReadFlows
    #: The configuration change log.
    config_changes: IReadConfigChanges
    #: Stored projects (the read half).
    projects: IReadProjectRepository
    #: Each project's links to the tenant's work sources and repositories (the read half).
    project_links: IReadProjectLinks
    #: Each chunk's effective repository targets.
    chunk_targets: ChunkTargets
    #: List and show urgencies in order, with the default marked and their open chunks counted.
    urgency_reading: UrgencyReading
    #: The finding read Protocol.
    findings: IReadFindingRepository
    #: A run's finding bucket — what it is shown and may cite.
    finding_bucket: FindingBucketReader


@dataclass(frozen=True)
class HubServices:
    """The request bridge's way to a tenant's services, built by :func:`build_services` and handed to ``create_app``;
    every hub-level owner is the process container's, resolved from ``app.state.hub_process``."""

    #: Opens a tenant's services — the only way from the request bridge to a tenant's.
    tenants: TenantServicesOpener


@dataclass(frozen=True)
class TenantStores:
    """One tenant's repository set and the leaf services over it, every adapter holding the same
    :class:`ScopedConnections`. Opened through :meth:`HubCore.open`; every other hub wiring takes
    these instances from it rather than building its own copy (pinned by
    ``tests/component/hub/test_core_sharing.py``)."""

    #: The tenant's container scope, the adopted values and the leaves below are read from.
    tenant: Resolver
    connections: ScopedConnections
    transactions: HubTransactions
    registry: RunnerRegistryStore
    hosts: HostRegistryStore
    work_item_store: WorkItemStore
    chunk_stores: ChunkStores
    finding_store: FindingStore
    work_item_edits: WorkItemEditService
    #: Sealed secret rows — composition-only, never a ``TenantServices`` field.
    secrets: SecretStore
    #: Work-source records — composition-only write half; ``TenantServices`` carries the read half.
    work_source_records: WorkSourceRecordStore
    #: Repository records — composition-only write half; ``TenantServices`` carries the read half.
    repository_records: RepositoryRecordStore
    #: Forge records — composition-only write half; ``TenantServices`` carries the read half.
    forge_records: ForgeRecordStore
    #: Credential records — composition-only write half; ``TenantServices`` carries the read half.
    credential_records: CredentialRecordStore
    #: Which active configured records name each secret, across every referring kind.
    secret_referrers: SecretReferrersStore
    forge_referrers: ForgeReferrersStore
    credential_referrers: CredentialReferrersStore
    config_changes: ConfigChangeStore
    #: Projects — composition-only write half; ``TenantServices`` carries the read half.
    projects: ProjectStore
    #: Project links — composition-only write half; ``TenantServices`` carries the read half.
    project_links: ProjectLinkStore
    config_apply: ConfigApplyStore
    config_import: ConfigImportStore
    invitations: InvitationStore
    #: Urgencies — composition-only write half; ``TenantServices`` carries the services over them.
    urgencies: UrgencyStore
    #: The urgency set's order — composition-only write half.
    urgency_order: UrgencyOrderStore
    clock: IClock

    @property
    def scope(self) -> StoreScope:
        return self.connections.scope


def build_tenant_stores(tenant: Resolver, *, clock: IClock) -> TenantStores:
    """Build the :class:`TenantStores` of the tenant whose container scope ``tenant`` resolves through: every adapter
    and every concept service over them is the scope's instance."""
    return TenantStores(
        tenant=tenant,
        connections=tenant.get(ScopedConnections),
        transactions=tenant.get(HubTransactions),
        registry=tenant.get(RunnerRegistryStore),
        hosts=tenant.get(HostRegistryStore),
        work_item_store=tenant.get(WorkItemStore),
        chunk_stores=tenant.get(ChunkStores),
        finding_store=tenant.get(FindingStore),
        secrets=tenant.get(SecretStore),
        work_source_records=tenant.get(WorkSourceRecordStore),
        repository_records=tenant.get(RepositoryRecordStore),
        forge_records=tenant.get(ForgeRecordStore),
        credential_records=tenant.get(CredentialRecordStore),
        secret_referrers=tenant.get(SecretReferrersStore),
        forge_referrers=tenant.get(ForgeReferrersStore),
        credential_referrers=tenant.get(CredentialReferrersStore),
        config_changes=tenant.get(ConfigChangeStore),
        projects=tenant.get(ProjectStore),
        project_links=tenant.get(ProjectLinkStore),
        config_apply=tenant.get(ConfigApplyStore),
        config_import=tenant.get(ConfigImportStore),
        invitations=tenant.get(InvitationStore),
        urgencies=tenant.get(UrgencyStore),
        urgency_order=tenant.get(UrgencyOrderStore),
        work_item_edits=tenant.get(WorkItemEditService),
        clock=clock,
    )


class TenantCache:
    """One entry per open tenant — its :class:`TenantStores` and, once the hub serves tenants
    (:meth:`serve`), the services built over them — built on its first open and kept until
    :meth:`evict` drops it.

    A hit answers from memory and issues no statement: closing a tenant evicts it, and its write
    paths still refuse a closed tenant. A miss confirms the tenant exists and is open
    (``confirm_open`` raises :class:`UnknownTenant` or :class:`TenantClosed`) under the cache's one
    lock before building, so two racing opens of a tenant never yield two entries, and a deletion's
    evict, which waits on that lock, drops an entry its close raced."""

    def __init__(
        self,
        *,
        build: Callable[[StoreScope], TenantStores],
        confirm_open: Callable[[TenantId], None],
    ) -> None:
        self._build = build
        self._confirm_open = confirm_open
        self._serving: TenantServing | None = None
        self._entries: dict[TenantId, _TenantEntry] = {}
        self._lock = threading.Lock()

    def serve(self, serving: TenantServing) -> None:
        """Build each open tenant's services through ``serving`` from now on."""
        with self._lock:
            self._serving = serving

    def open(self, scope: StoreScope) -> TenantStores:
        """The stores of ``scope``'s tenant. Raises :class:`UnknownTenant` or
        :class:`TenantClosed` for a tenant there are no stores to open for."""
        return self._entry(scope).stores

    def open_services(self, scope: StoreScope) -> TenantServices:
        """The services of ``scope``'s tenant, refused as :meth:`open` refuses."""
        served = self._entry(scope).served
        if served is None:
            raise RuntimeError("the hub serves no tenant's services: none were wired")
        return served.services

    def evict(self, tenant: TenantId) -> None:
        """Drop ``tenant``'s entry, if cached; the tenant's deletion calls this when it closes, once its container
        scope — which closes the objects its configured records built and drops its event broker — is evicted."""
        with self._lock:
            self._entries.pop(tenant, None)

    def _entry(self, scope: StoreScope) -> _TenantEntry:
        with self._lock:
            entry = self._entries.get(scope.tenant)
            if entry is None:
                # A deletion commits its close before it evicts, and evicting waits on this lock: a close
                # this check missed is followed by an evict that drops the entry cached here.
                self._confirm_open(scope.tenant)
                entry = _TenantEntry(self._build(scope))
                self._entries[scope.tenant] = entry
            if entry.served is None and self._serving is not None:
                entry.served = self._serving.serve(scope.tenant, entry.stores)
            return entry


@dataclass
class _TenantEntry:
    stores: TenantStores
    served: ServedTenant | None = None


@dataclass(frozen=True)
class ServedTenant:
    """What serving one tenant builds over its :class:`TenantStores`: its services."""

    services: TenantServices


@collaborator
@dataclass(frozen=True)
class TenantServing:
    """How a :class:`TenantCache` serves its tenants: ``build`` wires a tenant's services over its
    stores and the broker its container scope holds."""

    build: Callable[[TenantStores, EventBroker], ServedTenant]

    def serve(self, tenant: TenantId, stores: TenantStores) -> ServedTenant:
        """What serving ``tenant`` builds over its ``stores``, publishing to its scope's broker."""
        return self.build(stores, stores.tenant.get(EventBroker))


class IOpenTenantServices(Protocol):
    """Opens ``scope``'s tenant's services, built once per tenant; raises ``UnknownTenant`` or ``TenantClosed`` for a
    tenant there are none to open for."""

    def __call__(self, scope: StoreScope, /) -> TenantServices: ...


@dataclass(frozen=True)
class TenantServicesOpener:
    """Opens a tenant's services — the request bridge's only way to a tenant's."""

    open_services: IOpenTenantServices
    #: The open tenants and their names, held in memory — what a request's tenant is resolved against.
    open_tenants: OpenTenants


@dataclass(frozen=True)
class HubCore:
    """The process-scoped hub-level stores and the per-tenant store cache, built once by
    :func:`build_hub_core`. A tenant's stores are opened through :meth:`open`."""

    store_connections: HubStoreConnections
    #: The factory the connections wrap a driver fault through, shared with every adapter that opens its own.
    store_errors: HubStoreErrorFactory
    hub_scoped_reads: HubScopedReads
    tenants: TenantCache
    clock: IClock
    #: The process container the core was opened through; whoever disposes the store closes it first.
    process: HubProcess

    def open(self, scope: StoreScope) -> TenantStores:
        """``scope``'s tenant's stores, built once per tenant. Raises :class:`UnknownTenant` or
        :class:`TenantClosed` for a tenant there are no stores to open for."""
        return self.tenants.open(scope)

    def open_services(self, scope: StoreScope) -> TenantServices:
        """``scope``'s tenant's services, refused as :meth:`open` refuses."""
        return self.tenants.open_services(scope)


def build_hub_settings(
    connections: HubStoreConnections, clock: IClock, footing: HubSettingsFooting
) -> HubSettingsGraph:
    store = HubSettingsStore(connections, clock, footing.validate)
    reader = HubSettingsReader(store)
    return HubSettingsGraph(store, reader, HubSettingsAuthoring(reader, store, footing.validate), footing)


def connections_for_engine(engine: Engine, platform_tracing: IPlatformTracing | None = None) -> HubStoreConnections:
    """Build the hub store graph over a borrowed engine."""
    if platform_tracing is not None:
        platform_tracing.instrument_engine(engine)
    return HubStoreConnections(engine, HubStoreErrorFactory(get_logger("blizzard.hub.store")))


def open_hub_engine(db_url: str) -> Engine:
    """Open a hub engine only after installing the tenant guard."""
    install_tenant_guard()
    return create_engine_from_url(db_url)


def open_hub_store(db_url: str, platform_tracing: IPlatformTracing | None = None) -> tuple[Engine, HubStoreConnections]:
    """Acquire an engine and its shared, instrumented connections graph."""
    engine = open_hub_engine(db_url)
    try:
        return engine, connections_for_engine(engine, platform_tracing)
    except BaseException:
        engine.dispose()
        raise


def build_process_core(connections: HubStoreConnections, config: HubConfig, environ: Mapping[str, str]) -> HubCore:
    """:func:`build_hub_core` for an offline verb: the system clock, tracing disabled and the environment's actions."""
    return build_hub_core(
        connections,
        clock=build_system_clock(),
        config=config,
        platform_tracing=DisabledPlatformTracing(),
        environ=environ,
        actions=build_hub_actions(environ),
    )


def build_system_clock() -> IClock:
    """The one wall clock a hub process reads."""
    return SystemClock()


def register_legacy(registry: LegacyRegistry) -> None:
    """Declare the store's tenant adapters and the legacy bridge's transitional ports over them: the tenant's
    transactions and its invitation writes."""
    tenant = store_wiring.register_tenant(registry)
    registry.alias(port(IHubTransactions), tenant.of(HubTransactions))
    # The reads a chunk-changed frame is enriched from.
    registry.alias(port(IWriteInvitationRepository), tenant.of(InvitationStore))


#: The transitional tenant ports :func:`register_legacy` declares, for the store-free graph.
_LEGACY_TENANT_KEYS: tuple[type[Any], ...] = (
    IHubTransactions,
    IWriteInvitationRepository,
)


def register_legacy_absent(registry: LegacyRegistry) -> None:
    """Declare each of the legacy bridge's transitional ports absent, for the store-free graph."""
    store_wiring.register_tenant_absent(registry)
    for missing in _LEGACY_TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)


def build_hub_core(
    store_connections: HubStoreConnections,
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
    egress_path_key: EgressPathKey | None = None,
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubCore:
    """Build the :class:`HubCore` over the hub's process container, opened here over the borrowed inputs; the core's
    ``process`` closes it. A core built without the ``settings`` graph declares the settings ports absent, and one
    without the ``versions`` window, serving no runner, declares the window absent; a lent ``oauth_http_client`` is
    borrowed, and the hub opens its own when none is lent. The trace export runs only over a lent ``trace_exporter``,
    and the fact export as the boot ``[egress]`` config and ``egress_path_key`` decide here."""
    from blizzard.hub.process_root import open_store_backed  # the root registers this module's leaves

    process = open_store_backed(
        store_connections,
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
        egress_boot=egress_boot(config.egress, egress_path_key),
    )
    return hub_core_over(process, store_connections, clock=clock)


def hub_core_over(process: HubProcess, store_connections: HubStoreConnections, *, clock: IClock) -> HubCore:
    """The :class:`HubCore` over an opened store-backed ``process``, which the core's ``process`` then closes."""
    from blizzard.hub.process_root import bind_closed_tenant_forgetting  # the root binds the legacy cache's step

    store_errors = store_connections.errors
    resolver = process.resolver
    hub_scoped_reads = resolver.get(HubScopedReads)

    def build(scope: StoreScope) -> TenantStores:
        # The tenant's container scope opens with its legacy entry, whose adopted values and leaves are read from it, so
        # a request admitting a tenant the legacy cache already serves finds its scope open too.
        return build_tenant_stores(process.tenants.open(scope).resolver, clock=clock)

    cache = TenantCache(build=build, confirm_open=hub_scoped_reads.tenants.confirm_open)
    bind_closed_tenant_forgetting(process, legacy=cache.evict)
    return HubCore(
        store_connections=store_connections,
        store_errors=store_errors,
        hub_scoped_reads=hub_scoped_reads,
        tenants=cache,
        clock=clock,
        process=process,
    )


@dataclass(frozen=True)
class TenantServiceParts:
    """The process-scoped inputs every tenant's services are wired from — one set, shared by every
    tenant, so no adapter or client is built twice."""

    clock: IClock
    step_wake: StepWake


def build_tenant_services(stores: TenantStores, parts: TenantServiceParts, *, events: EventBroker) -> ServedTenant:
    """Construct and wire one tenant's services over its :class:`TenantStores`, publishing to ``events`` —
    the tenant's broker. Every configured adapter is the tenant scope's instance."""
    clock = parts.clock
    tenant = stores.tenant
    graph_yaml = tenant.get(IConfigCodec)
    chunk_stores = stores.chunk_stores
    finding_store = stores.finding_store
    registry_store = tenant.get(IReadRunnerRegistry)
    host_store = tenant.get(IReadHostRegistry)
    chunk_facts = chunk_stores.facts
    chunk_record = chunk_stores.record
    chunk_lifecycle = chunk_stores.lifecycle
    chunk_work_refs = chunk_stores.work_refs
    chunk_queue = chunk_stores.queue
    chunk_route = chunk_stores.route
    chunk_movement = chunk_stores.movement
    chunk_artifacts = chunk_stores.artifacts
    chunk_questions = chunk_stores.questions
    chunk_decisions = chunk_stores.decisions
    chunk_escalations = chunk_stores.escalations
    chunk_events = chunk_stores.events
    chunk_usage = chunk_stores.usage
    chunk_delivery = chunk_stores.delivery
    chunk_hub_exec = chunk_stores.hub_exec
    chunk_dependencies = chunk_stores.dependencies
    forge_budgets = tenant.get(ForgeBudgets)
    work_sources = tenant.get(IWorkSourceRegistry)
    forges = tenant.get(ForgeRegistry)
    graph_store = tenant.get(GraphStore)
    event_log = tenant.get(EventLogService)
    graph_mint = tenant.get(GraphMintService)
    default_graph = tenant.get(DefaultGraphService)
    config_authoring = tenant.get(ConfigAuthoring)
    finding_bucket = tenant.get(FindingBucketReader)
    commit_links = tenant.get(LandedCommitLinks)
    chunk_targets = tenant.get(ChunkTargets)
    work_ref_label: WorkRefLabel = tenant.get(WorkRefLabel)
    services = TenantServices(
        forges=forges,
        forge_budgets=forge_budgets,
        work_ref_label=work_ref_label,
        chunks=ChunkReadStores(
            facts=chunk_facts,
            record=chunk_record,
            lifecycle=chunk_lifecycle,
            work_refs=chunk_work_refs,
            queue=chunk_queue,
            route=chunk_route,
            movement=chunk_movement,
            artifacts=chunk_artifacts,
            questions=chunk_questions,
            decisions=chunk_decisions,
            escalations=chunk_escalations,
            events=chunk_events,
            usage=chunk_usage,
            delivery=chunk_delivery,
            hub_exec=chunk_hub_exec,
            dependencies=chunk_dependencies,
        ),
        graphs=graph_store,
        graph_bodies=graph_store,
        node_executors=graph_store,
        graph_mint=graph_mint,
        default_graph=default_graph,
        graph_yaml=graph_yaml,
        registry=registry_store,
        hosts=host_store,
        hub_step_wake=parts.step_wake,
        events=events,
        chunk_frames=tenant.get(ChunkFrameLoader),
        event_log=event_log,
        clock=clock,
        work_sources=work_sources,
        users=tenant.get(IReadUserRepository),
        secret_catalog=stores.secrets,
        config_authoring=config_authoring,
        work_source_records=stores.work_source_records,
        repository_records=stores.repository_records,
        forge_records=stores.forge_records,
        credential_records=stores.credential_records,
        commit_links=commit_links,
        flows=tenant.get(IReadFlows),
        config_changes=stores.config_changes,
        projects=stores.projects,
        project_links=stores.project_links,
        chunk_targets=chunk_targets,
        urgency_reading=tenant.get(UrgencyReading),
        findings=finding_store,
        finding_bucket=finding_bucket,
    )
    return ServedTenant(services=services)


def build_services(core: HubCore) -> HubServices:
    """Serve each tenant's services from the shared :class:`HubCore`'s tenant cache (:func:`build_tenant_services`),
    and hand back the request bridge's opener over it. Every hub-level owner is the process container's, born in its
    area's wiring."""
    process = core.process.resolver
    parts = TenantServiceParts(clock=core.clock, step_wake=process.get(StepWake))
    core.tenants.serve(TenantServing(build=lambda stores, events: build_tenant_services(stores, parts, events=events)))
    return HubServices(
        tenants=TenantServicesOpener(
            open_services=core.open_services,
            open_tenants=process.get(OpenTenants),
        ),
    )


def build_flow_driver(process: HubProcess) -> FlowDriver:
    """The hosted hub's flow driver, over every open tenant's lines and turns, bound to the process's step wake. Each
    :class:`FlowLineStores` callable is a named accessor resolving one type from the row's tenant scope — the very
    instance a request beneath that tenant resolves — so the driver holds typed callables and no resolver. Built by
    ``build_hosted_app`` alone, never by :func:`build_services`, so no CLI or test build over a live hub's store takes
    that hub's turns."""
    resolver = process.resolver
    open_tenants = resolver.get(OpenTenants)
    tenants = process.tenants

    def reconciler_of(tenant: TenantId) -> FlowLineReconciler:
        return tenants.resolve(StoreScope(tenant), FlowLineReconciler)

    def turns_of(tenant: TenantId) -> ITurnRunner:
        return tenants.resolve(StoreScope(tenant), AnnouncedTurns)

    def notices_of(tenant: TenantId) -> BudgetNoticeDrain:
        return tenants.resolve(StoreScope(tenant), BudgetNoticeDrain)

    def publisher_of(tenant: TenantId) -> IFlowChangePublisher:
        return tenants.resolve(StoreScope(tenant), IFlowChangePublisher)

    driver = FlowDriver(
        FlowLineStores(
            corpus=resolver.get(IReadFlowLinesCorpus),
            tenants=lambda: open_tenants.every().keys(),
            lines=reconciler_of,
            turns=turns_of,
            notices=notices_of,
            publishers=publisher_of,
        ),
        resolver.get(IClock),
    )
    resolver.get(StepWake).bind(driver)
    return driver
