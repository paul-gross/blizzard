"""The hub's fleet-service composition (``bzh:dependency-injection``).

One place the store-backed collaborators are constructed and injected. Controllers read
the stores through their **read** Protocols and mutate only through the services
(``bzh:controller-read-only``); every chunk seam below — ``HubServices.chunks``'s bundle and
every domain service's own narrow parameter alike — is the same instance built here."""

from __future__ import annotations

# The tenant cache's lock guards this process's memory, not a store decision.
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Protocol

import httpx2
from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.flags import (
    Flags,
    resolve_flags,
    warn_held_keys,
    warn_ignored_flags,
)
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing, IPlatformTracing
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer
from blizzard.foundation.registry.interface import Lifetime, Ref, port
from blizzard.foundation.registry.legacy import LegacyRegistry
from blizzard.foundation.registry.scopes import Resolver
from blizzard.foundation.roles import collaborator
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.version_window import HubVersionWindow
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.identities import IReadIdentityRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.invitations import IWriteInvitationRepository
from blizzard.hub.auth.memberships import IReadMembershipRepository
from blizzard.hub.auth.service import AuthService
from blizzard.hub.auth.sessions import IReadSessionRepository
from blizzard.hub.auth.throttle import LiveIpThrottle
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.action_loop import ActionLoop
from blizzard.hub.delivery.action_pass import ACTION_PASS_GRACE, ActionPassDriver, PassBound
from blizzard.hub.delivery.action_registry import HubActions, build_hub_actions
from blizzard.hub.delivery.flow_driver import FlowDriver, FlowLineStores, ITurnRunner, StepWake
from blizzard.hub.delivery.flow_turn import TurnRunner
from blizzard.hub.delivery.hub_node import HubNodeRouter
from blizzard.hub.documents.codec import IConfigCodec
from blizzard.hub.domain.cadence.sweep import CadenceSweep, TenantCadence
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.model import WorkRefLabel
from blizzard.hub.domain.chunk.ports.commit_links import IReadLandedCommitLinks
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.stores import ChunkReadStores, ChunkStores
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import IReadConfigChanges
from blizzard.hub.domain.config.credentials import IReadCredentialRepository
from blizzard.hub.domain.config.forges import IReadForgeRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.config.secrets import ISecretCatalog
from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository
from blizzard.hub.domain.delivery.lines import FlowLineFactory
from blizzard.hub.domain.delivery.ports.flows import IReadFlows, IReadFlowViews
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.execution.apply import IHubStepWake
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import (
    IReadFindingRepository,
)
from blizzard.hub.domain.garden.review.service import ReviewFindingsRecorder
from blizzard.hub.domain.graph.actions import HubActionCatalogue
from blizzard.hub.domain.graph.authoring import DefaultGraphService, GraphMintService
from blizzard.hub.domain.graph.model import IReadGraphBodies, IReadGraphRepository, IReadNodeExecutors
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.observability.analytics.derivation import (
    EventDerivationReconciler,
    EventDerivationService,
    TenantEventDerivation,
)
from blizzard.hub.domain.observability.analytics.extraction import ITurnEventExtractor
from blizzard.hub.domain.observability.analytics.operational import IReadOperationalAnalytics
from blizzard.hub.domain.observability.analytics.queries import IReadAnalyticsEventQueries
from blizzard.hub.domain.observability.egress.backfill import EgressBackfill
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.event_rows import ExportPaths, export_paths
from blizzard.hub.domain.observability.egress.ports import IEgressWriter
from blizzard.hub.domain.observability.egress.reset import EgressReset
from blizzard.hub.domain.observability.egress.status import EgressStatusReader
from blizzard.hub.domain.observability.egress.sweep import EgressSweep
from blizzard.hub.domain.observability.env_timeline import EnvTimelineReading
from blizzard.hub.domain.observability.forge_status import (
    FULL_PASS_FLOOR_INTERVALS,
    AnnotationReconciler,
    TenantAnnotation,
)
from blizzard.hub.domain.observability.hub_events import HubEventLogService, IReadHubEventLog
from blizzard.hub.domain.observability.invariants import HubInvariantCheck
from blizzard.hub.domain.observability.tracing.replay import TraceReplay
from blizzard.hub.domain.observability.tracing.repository import StepHydration
from blizzard.hub.domain.observability.tracing.status import TraceStatusReader
from blizzard.hub.domain.observability.tracing.sweep import TraceExportSweep
from blizzard.hub.domain.observability.transcripts import (
    IReadTranscriptSegments,
    TranscriptCaps,
    TranscriptIngestService,
)
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.projects.links import IReadProjectLinks
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.domain.projects.targets import ChunkTargetCapture, ChunkTargets
from blizzard.hub.domain.runners.host import IReadHostRegistry
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RetiredRunnerGuard
from blizzard.hub.domain.sweeps.locks import SweepLocks
from blizzard.hub.domain.sweeps.names import SweepName
from blizzard.hub.domain.sweeps.roster import SweepRoster
from blizzard.hub.domain.tenancy.administration import TenantAdministration
from blizzard.hub.domain.tenancy.teardown import TenantTeardown, TenantTeardownSweep
from blizzard.hub.domain.urgency.services import (
    UrgencyReading,
)
from blizzard.hub.domain.work_items.closure import CloseDrainSweep, CloseIntentDrainer
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.egress.factory import ConfiguredWriter, EgressUnavailable, build_egress_writer
from blizzard.hub.egress.space import free_bytes
from blizzard.hub.egress.writer import EgressWriterSettings, mint_process_token
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.forge.budget import ForgeBudgets
from blizzard.hub.forge.budget_notices import BudgetNoticeDrain
from blizzard.hub.forge.commit_links import LandedCommitLinks
from blizzard.hub.forge.registry import ForgeRegistry
from blizzard.hub.graph_sync import packaged_minter
from blizzard.hub.graphs import PACKAGED as GRAPHS_PACKAGED
from blizzard.hub.graphs import PackagedGraphs
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.settings.footing import HubSettingsFooting
from blizzard.hub.settings.graph import HubSettingsGraph
from blizzard.hub.settings.reader import HubSettingsReader
from blizzard.hub.settings.service import HubSettingsAuthoring
from blizzard.hub.settings.source import HubConfigSource
from blizzard.hub.store import wiring as store_wiring
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.analytics_event_query_store import AnalyticsEventQueryStore
from blizzard.hub.store.internal.analytics_operational_store import AnalyticsOperationalStore
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.config_apply_store import ConfigApplyStore
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.config_import_store import ConfigImportStore
from blizzard.hub.store.internal.credential_record_store import CredentialRecordStore
from blizzard.hub.store.internal.credential_referrers import CredentialReferrersStore
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.env_timeline_store import EnvTimelineStore
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.flow_store import FlowStore, tenant_flows
from blizzard.hub.store.internal.forge_annotation_store import ForgeAnnotationStore
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.forge_referrers import ForgeReferrersStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.hub_event_log_store import HubEventLogStore
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads
from blizzard.hub.store.internal.hub_settings_store import HubSettingsStore
from blizzard.hub.store.internal.invariants import HubInvariants, TenantReads
from blizzard.hub.store.internal.invitation_store import InvitationStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.secret_referrers import SecretReferrersStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.tenant_admin_store import TenantAdministrationStore
from blizzard.hub.store.internal.tenant_teardown_store import TenantTeardownStore
from blizzard.hub.store.internal.trace_store import ProjectLabels, TraceStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.internal.urgency_store import UrgencyOrderStore, UrgencyStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.store.tenant_guard import install_tenant_guard
from blizzard.hub.store.transactions import ClosedTenantUnits, HubTransactions
from blizzard.hub.system_artifacts import PACKAGED as SYSTEM_ARTIFACTS_PACKAGED
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.tenancy import STORE_UNAVAILABLE, StoreScope, TenantClosed, TenantId, UnknownTenant
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
    #: The transcript lane's ingest policy — the write side; ``transcripts``
    #: above is the same store's read Protocol.
    transcript_ingest: TranscriptIngestService
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
    #: The transcript-segment read Protocol (``bzh:controller-read-only``).
    transcripts: IReadTranscriptSegments
    #: The event-derivation reconciler's own service — a scoped, bounded re-derive, apart from a full sweep pass.
    event_derivation_service: EventDerivationService
    #: The analytics event query Protocol, read-only (``bzh:controller-read-only``); no write repository backs it.
    analytics_events: IReadAnalyticsEventQueries
    #: The operational-datasets query Protocol, read-only; no write repository backs it.
    operational_analytics: IReadOperationalAnalytics
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
    #: What the surfaces read of the tenant's flows and the newest of their records.
    flow_views: IReadFlowViews
    #: Builds a flow line from its submissions, so a surface judges a place as the driver judges a turn.
    flow_line_factory: FlowLineFactory
    #: Converges the tenant's flow lines on where its chunks stand — driven by the hosted hub's flow driver alone.
    flow_lines: FlowLineReconciler
    #: Answers for each turn the flow driver starts in this tenant.
    turns: TurnRunner
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
    #: Each errand's last run, read in one snapshot or in a caller's unit.
    #: Every node step a routed chunk spent in a runner environment over a window.
    env_timeline: EnvTimelineReading
    #: The finding read Protocol.
    findings: IReadFindingRepository
    #: A run's finding bucket — what it is shown and may cite.
    finding_bucket: FindingBucketReader


@dataclass(frozen=True)
class HubServices:
    """The hub-level services — identity, sessions, signing, exports and sweeps — shared by every
    tenant and stashed on ``app.state.services``; a tenant's own services are opened through
    :attr:`tenants`."""

    #: Opens a tenant's services — the only way from the hub-level services to a tenant's.
    tenants: TenantServicesOpener
    #: The hub's open process container and its tenant scopes — what a request resolves through.
    process: HubProcess
    #: The hub-scoped reads a runner's credential resolves its tenant through — held only by the
    #: runner-auth edge (``bzh:hub-scoped-reads-confined``).
    hub_scoped_reads: HubScopedReads
    #: The one thread every tenant's hub action passes run on.
    action_loop: ActionLoop
    #: What every tenant's services wake the flow driver through, once ``build_flow_driver`` binds it.
    step_wake: StepWake
    #: The hub's own occurrences — its exports', its startup — recorded in ``hub_event_log``, published to no stream.
    hub_event_log: HubEventLogService
    #: Every tenant's live event broker — what ends a logged-out session's streams in every tenant.
    event_brokers: TenantBrokers
    #: The hub's own event log, read newest-first — by hub administrators alone.
    hub_events: IReadHubEventLog
    #: Creates, renames and lists the hub's tenants.
    tenant_admin: TenantAdministration
    #: Deletes tenants — closes, drains and completes them.
    tenant_teardown: TenantTeardown
    #: The ``tenant_teardown`` sweep, which carries on every deletion underway.
    tenant_teardown_sweep: TenantTeardownSweep
    clock: IClock
    #: The close-intent drain sweep — one pass per hub, acting in each intent's tenant.
    close_drain: CloseDrainSweep
    #: The cadence sweep — one pass per hub, judging and minting in each tenant holding a live cadence.
    cadence: CadenceSweep
    #: The fleet-trace export sweep — ``None`` when no trace exporter is wired.
    trace_export: TraceExportSweep | None
    #: The operator's read of fleet tracing — always composed, so status answers with tracing off.
    trace_status: TraceStatusReader
    #: Tells a past window again — always composed; holds the sweep's own exporter, or ``None`` with tracing off.
    trace_replay: TraceReplay
    #: The fact-egress export sweep — ``None`` unless ``[egress] directory`` is set and its writer could be built.
    egress_export: EgressSweep | None
    #: Why the configured export could not start (Parquet without its extra); ``None`` when it could, or none is set.
    egress_unavailable: EgressUnavailable | None
    #: The unset ``path_key_env`` variable that dropped the ``events`` dataset from a running export; else ``None``.
    egress_missing_path_key: str | None
    #: The operator's read of the fact export — always composed, so status answers with the export off.
    egress_status: EgressStatusReader
    #: Moves a dataset's cursor — always composed; it refuses while the export is off.
    egress_reset: EgressReset
    #: Writes a past window — always composed; it refuses while the export is off.
    egress_backfill: EgressBackfill
    #: The session read repository — reads only (``bzh:controller-read-only``).
    sessions: IReadSessionRepository
    #: The identity-link read repository — a plain read, no domain service.
    identities: IReadIdentityRepository
    #: The user read repository — every write still goes through ``auth``.
    users: IReadUserRepository
    #: The membership read repository — whose memberships a request's tenant is resolved against.
    memberships: IReadMembershipRepository
    #: The identity domain service — sessions, the first-login linking rule, ``state``.
    auth: AuthService
    #: Per-IP token-bucket login throttle — ``None`` when ``[auth.login_throttle]`` turns the throttle off.
    auth_throttle: LiveIpThrottle | None
    #: The non-chunk auth/security fact log — ``login_failed``/``sso_refused``.
    auth_facts: AuthFactsService
    #: The hub half of the invariant checker over every tenant, one run at a time — run by hub administrators alone.
    invariants: HubInvariantCheck
    #: The transcript-event derivation sweep — one pass per hub, acting in each segment's tenant.
    event_derivation: EventDerivationReconciler
    #: The forge-status annotation sweep — one pass per hub, acting in each tenant over its own sources.
    annotation: AnnotationReconciler | None
    #: Every sweep built here by name, each pass under its lock — stepped by the timer and run on request.
    sweeps: SweepRoster


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
    #: Sealed secret rows — composition-only, never a ``HubServices`` field.
    secrets: SecretStore
    #: Work-source records — composition-only write half; ``HubServices`` carries the read half.
    work_source_records: WorkSourceRecordStore
    #: Repository records — composition-only write half; ``HubServices`` carries the read half.
    repository_records: RepositoryRecordStore
    #: Forge records — composition-only write half; ``HubServices`` carries the read half.
    forge_records: ForgeRecordStore
    #: Credential records — composition-only write half; ``TenantServices`` carries the read half.
    credential_records: CredentialRecordStore
    #: Each branch's delivery line — composition-only write half; ``TenantServices`` carries the read half.
    flows: FlowStore
    #: Which active configured records name each secret, across every referring kind.
    secret_referrers: SecretReferrersStore
    forge_referrers: ForgeReferrersStore
    credential_referrers: CredentialReferrersStore
    config_changes: ConfigChangeStore
    #: Projects — composition-only write half; ``HubServices`` carries the read half.
    projects: ProjectStore
    #: Project links — composition-only write half; ``HubServices`` carries the read half.
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
        flows=tenant.get(FlowStore),
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

    def open_sweeps(self, scope: StoreScope) -> TenantSweeps:
        """The halves of the hub's sweeps that act on ``scope``'s tenant's rows, refused as :meth:`open`
        refuses."""
        served = self._entry(scope).served
        if served is None:
            raise RuntimeError("the hub serves no tenant's sweeps: none were wired")
        return served.sweeps

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
class TenantSweeps:
    """A tenant's halves of the hub's sweeps, built with its services: what each pass does with the
    rows of that tenant."""

    close_drain: CloseIntentDrainer
    event_derivation: TenantEventDerivation
    annotation: TenantAnnotation


@dataclass(frozen=True)
class ServedTenant:
    """What serving one tenant builds over its :class:`TenantStores`: its services and its sweeps."""

    services: TenantServices
    sweeps: TenantSweeps


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
    """Opens a tenant's services — the hub-level services' only way to a tenant's."""

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
    #: The hub's own event log, and the service recording an occurrence in it.
    hub_events: HubEventLogStore
    hub_event_log: HubEventLogService
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

    def open_tenants(self) -> dict[TenantId, str]:
        """Every open tenant's name by id, read from the store."""
        return self.hub_scoped_reads.tenants.open_tenants()

    def every_open(self) -> list[TenantStores]:
        """Every open tenant's stores, in tenant-id order — how a hub-wide job that is not a sweep (a
        boot check, key rotation) acts in each tenant in turn. A tenant closed since the read is left
        out: nothing acts in it again."""
        opened: list[TenantStores] = []
        for tenant in sorted(self.open_tenants()):
            try:
                opened.append(self.open(StoreScope(tenant)))
            except (TenantClosed, UnknownTenant):
                continue
        return opened


def build_hub_settings(
    connections: HubStoreConnections, clock: IClock, footing: HubSettingsFooting
) -> HubSettingsGraph:
    store = HubSettingsStore(connections, clock, footing.validate)
    reader = HubSettingsReader(store)
    return HubSettingsGraph(store, reader, HubSettingsAuthoring(reader, store, footing.validate), footing)


def build_hub_flags(config: HubConfig) -> Flags:
    """The hub's resolved flags: the resolution ``config``'s load made, or — for a config built in code — every ``hub``
    and ``fleet`` flag of the registry it carries at its default, then its ``[flags]`` overrides. Each override the hub
    ignores, and each guarded key read as absent, is warned of here, once per process."""
    resolution = config.flag_resolution or resolve_flags(config.flag_registry, "hub", config.flags)
    log = get_logger("blizzard.hub")
    warn_ignored_flags(log, resolution)
    warn_held_keys(log, config.held_keys)
    return resolution.flags


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


def open_tenants_leaf(reads: HubScopedReads) -> OpenTenants:
    """The open tenants held in memory, a legacy leaf the hub identity area owns until it adopts them."""
    return OpenTenants(reads.tenants.open_tenants)


def action_loop_leaf() -> ActionLoop:
    """The one action loop every tenant's turns share, a legacy leaf the hub lifecycle owns until it adopts it."""
    return ActionLoop()


def step_wake_leaf() -> StepWake:
    """The flow driver's wake, bound by `build_flow_driver` — a build with no driver wakes nothing. A legacy leaf the
    hub lifecycle owns until it adopts it."""
    return StepWake()


def flow_line_factory_leaf(
    facts: IReadChunkFactsRepository, record: IReadChunkRecordRepository, graphs: GraphStore
) -> FlowLineFactory:
    """The tenant's flow-line builder, a legacy leaf the flow execution area owns until it adopts it."""
    return FlowLineFactory(facts=facts, record=record, graphs=graphs)


def register_legacy(registry: LegacyRegistry, *, flows: Ref[FlowStore]) -> None:
    """Declare the store's adapters and the legacy bridge's leaves, each built as the legacy builder built it: the
    process services a request's admission reads before its tenant is known, and the transitional ports and leaves of
    the areas not yet adopted — invitation writes, flow
    reads and the flow-line builder. The flow reads alias the flow store the root hands in."""
    registry.leaf(open_tenants_leaf, lifetime=Lifetime.PROCESS)
    registry.leaf(action_loop_leaf, lifetime=Lifetime.PROCESS)
    step_wake = registry.leaf(step_wake_leaf, lifetime=Lifetime.PROCESS)
    # The wake a completion's apply names by the domain's own Protocol.
    registry.alias(port(IHubStepWake), step_wake)
    hub_events = store_wiring.register_process(registry)
    registry.alias(port(IReadHubEventLog), hub_events)
    tenant = store_wiring.register_tenant(registry)
    registry.alias(port(IHubTransactions), tenant.of(HubTransactions))
    # The reads a chunk-changed frame is enriched from.
    registry.alias(port(IWriteInvitationRepository), tenant.of(InvitationStore))
    # The flow reads a chunk's flow places are rendered from, until the flow execution area adopts them.
    registry.alias(port(IReadFlows), flows)
    registry.alias(port(IReadFlowViews), flows)
    registry.leaf(flow_line_factory_leaf, lifetime=Lifetime.TENANT)


#: The transitional tenant ports and leaves :func:`register_legacy` declares, for the store-free graph.
_LEGACY_TENANT_KEYS: tuple[type[Any], ...] = (
    IHubTransactions,
    IWriteInvitationRepository,
    IReadFlows,
    IReadFlowViews,
    FlowLineFactory,
)


def register_legacy_absent(registry: LegacyRegistry) -> None:
    """Declare each of the legacy bridge's leaves and transitional ports absent, for the store-free graph."""
    for missing in (
        OpenTenants,
        ActionLoop,
        StepWake,
        IHubStepWake,
        IReadHubEventLog,
    ):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    store_wiring.register_process_absent(registry)
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
    graphs: PackagedGraphs = GRAPHS_PACKAGED,
    system_artifacts: PackagedSystemArtifacts = SYSTEM_ARTIFACTS_PACKAGED,
) -> HubCore:
    """Build the :class:`HubCore` over the hub's process container, opened here over the borrowed inputs; the core's
    ``process`` closes it. A core built without the ``settings`` graph declares the settings ports absent, and one
    without the ``versions`` window, serving no runner, declares the window absent; a lent ``oauth_http_client`` is
    borrowed, and the hub opens its own when none is lent."""
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
    )
    return hub_core_over(process, store_connections, clock=clock)


def hub_core_over(process: HubProcess, store_connections: HubStoreConnections, *, clock: IClock) -> HubCore:
    """The :class:`HubCore` over an opened store-backed ``process``, which the core's ``process`` then closes."""
    store_errors = store_connections.errors
    resolver = process.resolver
    hub_scoped_reads = resolver.get(HubScopedReads)
    hub_events = resolver.get(HubEventLogStore)

    def build(scope: StoreScope) -> TenantStores:
        # The tenant's container scope opens with its legacy entry, whose adopted values and leaves are read from it, so
        # a request admitting a tenant the legacy cache already serves finds its scope open too.
        return build_tenant_stores(process.tenants.open(scope).resolver, clock=clock)

    return HubCore(
        store_connections=store_connections,
        store_errors=store_errors,
        hub_scoped_reads=hub_scoped_reads,
        hub_events=hub_events,
        hub_event_log=HubEventLogService(events=hub_events),
        tenants=TenantCache(build=build, confirm_open=hub_scoped_reads.tenants.confirm_open),
        clock=clock,
        process=process,
    )


def _backfill_writers(config: EgressConfig, settings: EgressWriterSettings) -> Callable[[], IEgressWriter]:
    """A fresh writer per backfill, each with a newly minted process token. The export's own writer was built from
    these same settings, so a backfill's writer can fail to build only if that one did not."""
    directory = config.directory
    assert directory is not None  # composed only once the export itself is on

    def writer() -> IEgressWriter:
        built = build_egress_writer(config.format, directory, settings, mint_process_token())
        assert not isinstance(built, EgressUnavailable)
        return built

    return writer


@dataclass(frozen=True)
class TenantServiceParts:
    """The process-scoped inputs every tenant's services are wired from — one set, shared by every
    tenant, so no adapter or client is built twice."""

    clock: IClock
    public_url: str | None
    follow_latest_default: bool
    transcript_caps: TranscriptCaps | None
    platform_tracer: IPlatformTracer | None
    extractors: Sequence[ITurnEventExtractor]
    action_passes: ActionPassDriver
    #: The config a turn binds: the settings graph's resolution, or the boot config without one.
    pass_config: Callable[[], HubConfig]
    step_wake: StepWake


def build_tenant_services(stores: TenantStores, parts: TenantServiceParts, *, events: EventBroker) -> ServedTenant:
    """Construct and wire one tenant's services and sweeps over its :class:`TenantStores`, publishing to ``events`` —
    the tenant's broker. Every configured adapter is the tenant scope's instance."""
    clock = parts.clock
    tenant = stores.tenant
    actions = tenant.get(HubActions)
    catalogue = tenant.get(HubActionCatalogue)
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
    transcript_caps = parts.transcript_caps
    graph_store = tenant.get(GraphStore)
    transcript_store = tenant.get(TranscriptSegmentStore)
    event_store = tenant.get(TranscriptEventStore)
    event_derivation_service = EventDerivationService(
        events=event_store, facts=chunk_facts, record=chunk_record, clock=clock, extractors=parts.extractors
    )
    analytics_event_queries = tenant.get(AnalyticsEventQueryStore)
    operational_analytics = tenant.get(AnalyticsOperationalStore)
    event_log = tenant.get(EventLogService)
    # One id-keyed retired-runner refusal, shared by every runner-contact operation.
    retired = tenant.get(RetiredRunnerGuard)
    graph_mint = tenant.get(GraphMintService)
    default_graph = tenant.get(DefaultGraphService)
    config_authoring = tenant.get(ConfigAuthoring)
    run_context_store = tenant.get(RunContextStore)
    finding_bucket = tenant.get(FindingBucketReader)
    commit_links = tenant.get(LandedCommitLinks)
    chunk_targets = tenant.get(ChunkTargets)
    chunk_target_capture = tenant.get(ChunkTargetCapture)
    hub_router = HubNodeRouter(
        artifacts=chunk_artifacts,
        hub_exec=chunk_hub_exec,
        escalations=chunk_escalations,
        record=chunk_record,
        transactions=stores.transactions,
        events=event_log,
        clock=clock,
        repositories=stores.repository_records,
        forge_records=stores.forge_records,
        links=stores.project_links,
        targets=chunk_targets,
        projects=stores.projects,
        work_sources=work_sources,
        movement=chunk_movement,
        decisions=chunk_decisions,
        graphs=graph_store,
        facts=chunk_facts,
        follow_latest_default=parts.follow_latest_default,
    )
    turns = TurnRunner(
        flows=stores.flows,
        facts=chunk_facts,
        record=chunk_record,
        graphs=graph_store,
        artifacts=chunk_artifacts,
        transactions=stores.transactions,
        router=hub_router,
        events=event_log,
        publisher=events,
        clock=clock,
        repositories=stores.repository_records,
        forge_records=stores.forge_records,
        links=stores.project_links,
        targets=chunk_targets,
        projects=stores.projects,
        actions=actions,
        passes=parts.action_passes,
        forges=forges,
        review_findings=tenant.get(ReviewFindingsRecorder),
        garden_delivery=tenant.get(GardenDeliveryRecorder),
        run_context=run_context_store,
        tracer=parts.platform_tracer,
        public_url=parts.public_url,
        work_sources=work_sources,
        pass_config=parts.pass_config,
    )
    work_ref_label: WorkRefLabel = tenant.get(WorkRefLabel)
    flow_line_factory = tenant.get(FlowLineFactory)
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
        transcript_ingest=TranscriptIngestService(
            store=transcript_store,
            transactions=stores.transactions,
            retired=retired,
            clock=clock,
            caps=transcript_caps,
        ),
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
        transcripts=transcript_store,
        event_derivation_service=event_derivation_service,
        analytics_events=analytics_event_queries,
        operational_analytics=operational_analytics,
        secret_catalog=stores.secrets,
        config_authoring=config_authoring,
        work_source_records=stores.work_source_records,
        repository_records=stores.repository_records,
        forge_records=stores.forge_records,
        credential_records=stores.credential_records,
        commit_links=commit_links,
        flows=stores.flows,
        flow_views=stores.flows,
        flow_line_factory=flow_line_factory,
        flow_lines=FlowLineReconciler(
            flows=stores.flows,
            facts=chunk_facts,
            record=chunk_record,
            graphs=graph_store,
            artifacts=chunk_artifacts,
            repositories=stores.repository_records,
            forge_records=stores.forge_records,
            projects=stores.projects,
            links=stores.project_links,
            targets=chunk_targets,
            capture=chunk_target_capture,
            catalogue=catalogue,
            refusals=hub_router,
            transactions=stores.transactions,
            lines=flow_line_factory,
            clock=clock,
        ),
        turns=turns,
        config_changes=stores.config_changes,
        projects=stores.projects,
        project_links=stores.project_links,
        chunk_targets=chunk_targets,
        urgency_reading=tenant.get(UrgencyReading),
        env_timeline=EnvTimelineReading(
            facts=tenant.get(EnvTimelineStore), graphs=graph_store, artifacts=chunk_artifacts, clock=clock
        ),
        findings=finding_store,
        finding_bucket=finding_bucket,
    )
    sweeps = TenantSweeps(
        close_drain=CloseIntentDrainer(
            delivery=chunk_delivery,
            artifacts=chunk_artifacts,
            chunks=chunk_record,
            events=event_log,
            work_sources=work_sources,
            clock=clock,
            commit_links=commit_links,
            public_url=parts.public_url,
        ),
        event_derivation=TenantEventDerivation(service=event_derivation_service, events=event_store, clock=clock),
        annotation=TenantAnnotation(
            facts=chunk_facts,
            work_sources=work_sources,
            memory=tenant.get(ForgeAnnotationStore),
            events=event_log,
            clock=clock,
        ),
    )
    return ServedTenant(services=services, sweeps=sweeps)


def build_services(
    core: HubCore,
    *,
    public_url: str | None = None,
    follow_latest_default: bool,
    transcript_caps: TranscriptCaps | None = None,
    trace_exporter: ITraceExporter | None = None,
    tracing_settings: TracingSettings | None = None,
    egress_path_key: bytes | None = None,
    extractors: Sequence[ITurnEventExtractor],
    action_pass_grace: timedelta = ACTION_PASS_GRACE,
    cadences_on: Callable[[], bool],
) -> HubServices:
    """Construct the hub-level services over the shared :class:`HubCore`, its tenant cache serving each tenant's
    (:func:`build_tenant_services`). Each sweep or export is one pass per hub, acting on a row through its tenant's
    services; live config is read through the container's :class:`HubConfigSource`. A pass is let go
    ``action_pass_grace`` past its live bound; ``cadences_on`` answers whether a cadence pass may mint."""
    clock = core.clock
    process = core.process.resolver
    config_source = process.get(HubConfigSource)
    action_loop = process.get(ActionLoop)
    step_wake = process.get(StepWake)
    store_connections = core.store_connections
    brokers = process.get(TenantBrokers)
    color_pick = process.get(ColorPick)

    def action_bound(seconds: float) -> PassBound:
        return PassBound(seconds, action_pass_grace)

    parts = TenantServiceParts(
        clock=clock,
        public_url=public_url,
        follow_latest_default=follow_latest_default,
        transcript_caps=transcript_caps,
        platform_tracer=process.get(IPlatformTracer),
        extractors=extractors,
        action_passes=ActionPassDriver(action_loop, lambda: action_bound(config_source.current().action_pass_seconds)),
        pass_config=config_source.resolved,
        step_wake=step_wake,
    )
    core.tenants.serve(TenantServing(build=lambda stores, events: build_tenant_services(stores, parts, events=events)))
    hub_events = core.hub_events
    hub_event_log = core.hub_event_log
    open_tenants = core.process.resolver.get(OpenTenants)

    def scope_of(tenant: TenantId) -> StoreScope:
        """The scope a hub-level pass or bridge opens a row's tenant in — built in this one place."""
        return StoreScope(tenant)

    def sweeps_of(tenant: TenantId) -> TenantSweeps:
        return core.tenants.open_sweeps(scope_of(tenant))

    def hydration_of(tenant: TenantId) -> StepHydration:
        services = core.open_services(scope_of(tenant))
        return StepHydration(
            graphs=services.graphs,
            names=services.registry,
            projects=ProjectLabels(services.projects),
            label=services.work_ref_label,
            source_label=services.work_sources.source_label,
        )

    trace_store = TraceStore(
        store_connections,
        hub_events=hub_events,
        corpus=core.hub_scoped_reads.corpus,
        hydration=hydration_of,
        tenant_name=open_tenants.name,
    )

    def trace_config_source() -> TracingConfig:
        return config_source.current().tracing

    trace_export = (
        TraceExportSweep(
            steps=trace_store,
            exporter=trace_exporter,
            events=hub_event_log,
            clock=clock,
            config=trace_config_source,
        )
        if trace_exporter is not None
        else None
    )
    trace_replay = TraceReplay(steps=trace_store, exporter=trace_exporter, clock=clock, config=trace_config_source)
    trace_status = TraceStatusReader(
        settings=tracing_settings or TracingSettings("disabled"),
        status=trace_store,
        steps=trace_store,
        clock=clock,
        replay_max_window=lambda: config_source.current().tracing.replay_max_window,
    )
    exported = export_paths(config_source.boot.egress, egress_path_key)
    egress_config, egress_paths, egress_missing_key = exported.config, exported.policy, exported.missing_key_env

    def egress_exported_source() -> ExportPaths:
        return export_paths(config_source.current().egress, egress_path_key)

    def egress_source() -> EgressConfig:
        return egress_exported_source().config

    def writer_settings(config: EgressConfig) -> EgressWriterSettings:
        return EgressWriterSettings(config.max_rows_per_file, config.min_free_bytes)

    egress_writer_settings = writer_settings(egress_config)
    egress_writer = (
        build_egress_writer(
            egress_config.format,
            egress_config.directory,
            egress_writer_settings,
            mint_process_token(),
        )
        if egress_config.directory is not None
        else None
    )
    egress_store = core.process.resolver.get(EgressStore)
    egress_event_reads = core.hub_scoped_reads.corpus
    egress_unavailable = egress_writer if isinstance(egress_writer, EgressUnavailable) else None
    # The missing key matters only to an export that runs; with it off, nothing would have been written anyway.
    egress_missing_path_key = egress_missing_key if egress_writer is not None and egress_unavailable is None else None
    egress_status = EgressStatusReader(
        config=egress_source,
        rejected=egress_unavailable is not None,
        missing_path_key=egress_missing_path_key,
        exported_paths=egress_exported_source,
        egress=egress_store,
        event_reads=egress_event_reads,
        steps=trace_store,
        clock=clock,
        free_space=lambda: free_bytes(egress_config.directory) if egress_config.directory is not None else None,
    )
    configured_writer = (
        ConfiguredWriter(
            egress_writer, egress_writer_settings, lambda settings: _backfill_writers(egress_config, settings)()
        )
        if egress_writer is not None and not isinstance(egress_writer, EgressUnavailable)
        else None
    )
    egress_export = (
        EgressSweep(
            steps=trace_store,
            egress=egress_store,
            event_reads=egress_event_reads,
            paths=egress_paths,
            writer=egress_writer,
            events=hub_event_log,
            clock=clock,
            config=egress_source,
            writer_for=configured_writer.for_config if configured_writer is not None else None,
        )
        if egress_writer is not None and not isinstance(egress_writer, EgressUnavailable)
        else None
    )
    egress_reset = EgressReset(
        egress=egress_store,
        events=hub_event_log,
        clock=clock,
        config=egress_source,
        active=egress_export is not None,
        missing_path_key=egress_missing_path_key,
        exported_paths=egress_exported_source,
    )
    egress_backfill = EgressBackfill(
        steps=trace_store,
        egress=egress_store,
        event_reads=egress_event_reads,
        paths=egress_paths,
        clock=clock,
        config=egress_source,
        writers=None,
        writer_for=(lambda cfg: _backfill_writers(cfg, writer_settings(cfg))()) if egress_export is not None else None,
        missing_path_key=egress_missing_path_key,
        exported_paths=egress_exported_source,
    )
    # The identity area's services, each the process scope's one instance.
    user_store = process.get(UserRepository)
    identity_store = process.get(IdentityRepository)
    session_store = process.get(SessionRepository)
    memberships = process.get(MembershipRepository)
    auth = process.get(AuthService)

    tenant_unit = core.process.resolver.get(ClosedTenantUnits)
    tenant_admin = TenantAdministration(
        tenants=core.process.resolver.get(TenantAdministrationStore),
        units=tenant_unit,
        mint_packaged=packaged_minter(
            clock, core.process.resolver.get(IConfigCodec), core.process.resolver.get(HubActionCatalogue)
        ),
        changed=open_tenants.refresh,
        pick=color_pick,
        clock=clock,
    )

    def forget_closed(tenant: TenantId) -> None:
        # A closed tenant's container scope, its cached stores and services, and its broker go first; the in-memory
        # open set is read again after, so a request resolving the tenant meanwhile misses both caches and is refused
        # by the store. The container scope's finalizers close the tenant's configured clients and drop its broker.
        core.process.tenants.evict(tenant)
        core.tenants.evict(tenant)
        open_tenants.refresh()

    teardown_store = core.process.resolver.get(TenantTeardownStore)
    tenant_teardown = TenantTeardown(
        teardowns=teardown_store,
        units=tenant_unit,
        closed=forget_closed,
        events=hub_event_log,
        clock=clock,
    )
    tenant_teardown_sweep = TenantTeardownSweep(teardown=tenant_teardown, teardowns=teardown_store)
    close_drain = CloseDrainSweep(
        corpus=core.hub_scoped_reads.corpus,
        tenant=lambda tenant: sweeps_of(tenant).close_drain,
        clock=clock,
    )
    cadence = CadenceSweep(
        corpus=core.hub_scoped_reads.corpus,
        tenant=lambda tenant: core.process.tenants.resolve(scope_of(tenant), TenantCadence),
        minting=cadences_on,
    )
    event_derivation = EventDerivationReconciler(
        corpus=core.hub_scoped_reads.corpus,
        tenant=lambda tenant: sweeps_of(tenant).event_derivation,
        clock=clock,
    )
    annotation = AnnotationReconciler(
        corpus=core.hub_scoped_reads.corpus,
        tenants=lambda: open_tenants.every().keys(),
        tenant=lambda tenant: sweeps_of(tenant).annotation,
        clock=clock,
        full_pass_floor=lambda: timedelta(
            seconds=FULL_PASS_FLOOR_INTERVALS * config_source.current().annotation_interval_seconds
        ),
    )
    return HubServices(
        step_wake=step_wake,
        tenants=TenantServicesOpener(
            open_services=core.open_services,
            open_tenants=open_tenants,
        ),
        process=core.process,
        hub_scoped_reads=core.hub_scoped_reads,
        action_loop=action_loop,
        hub_event_log=hub_event_log,
        event_brokers=brokers,
        hub_events=hub_events,
        tenant_admin=tenant_admin,
        tenant_teardown=tenant_teardown,
        tenant_teardown_sweep=tenant_teardown_sweep,
        invariants=HubInvariantCheck(build_hub_invariants(store_connections, clock)),
        clock=clock,
        close_drain=close_drain,
        cadence=cadence,
        trace_export=trace_export,
        trace_status=trace_status,
        trace_replay=trace_replay,
        egress_export=egress_export,
        egress_unavailable=egress_unavailable,
        egress_missing_path_key=egress_missing_path_key,
        egress_status=egress_status,
        egress_reset=egress_reset,
        egress_backfill=egress_backfill,
        sessions=session_store,
        identities=identity_store,
        users=user_store,
        memberships=memberships,
        auth=auth,
        auth_throttle=process.get(LiveIpThrottle),
        auth_facts=process.get(AuthFactsService),
        event_derivation=event_derivation,
        annotation=annotation,
        sweeps=SweepRoster(
            every_tenant={
                SweepName.FORGE_STATUS: annotation,
                SweepName.EVENT_DERIVATION: event_derivation,
                SweepName.CLOSE_DRAIN: close_drain,
                SweepName.TENANT_TEARDOWN: tenant_teardown_sweep,
                **({SweepName.TRACE_EXPORT: trace_export} if trace_export is not None else {}),
                **({SweepName.EGRESS: egress_export} if egress_export is not None else {}),
                SweepName.CADENCE: cadence,
            },
            one_tenant={
                SweepName.FORGE_STATUS: annotation,
                SweepName.EVENT_DERIVATION: event_derivation,
                SweepName.CLOSE_DRAIN: close_drain,
                SweepName.CADENCE: cadence,
            },
            locks=SweepLocks(),
            clock=clock,
        ),
    )


def build_hub_invariants(connections: HubStoreConnections, clock: IClock) -> HubInvariants:
    """The hub half of the invariant checker over ``connections``; each tenant's reads are built over the scoped
    connections the half hands them, which share its one read transaction."""

    def tenant_reads(scoped: ScopedConnections) -> TenantReads:
        chunk_stores = build_chunk_stores(scoped, clock)
        return TenantReads(chunk_stores.facts, chunk_stores.record, tenant_flows(scoped, clock))

    return HubInvariants(connections, tenant_reads, clock)


def build_flow_driver(
    services: HubServices,
    announce: Callable[[TenantServices, ITurnRunner], ITurnRunner] = lambda _services, turns: turns,
) -> FlowDriver:
    """The hosted hub's flow driver, over every open tenant's lines and turns — each tenant's turns wrapped by
    ``announce`` — bound to ``services``' step wake. Built by ``build_hosted_app`` alone, never by
    :func:`build_services`, so no CLI or test build over a live hub's store takes that hub's turns."""
    driver = FlowDriver(
        FlowLineStores(
            corpus=services.hub_scoped_reads.corpus,
            tenants=lambda: services.tenants.open_tenants.every().keys(),
            lines=lambda tenant: services.tenants.open_services(StoreScope(tenant)).flow_lines,
            turns=lambda tenant: _announced(services.tenants.open_services(StoreScope(tenant)), announce),
            notices=lambda tenant: _notices(services.tenants.open_services(StoreScope(tenant))),
            publishers=lambda tenant: services.tenants.open_services(StoreScope(tenant)).events,
        ),
        services.clock,
    )
    services.step_wake.bind(driver)
    return driver


def _notices(tenant: TenantServices) -> BudgetNoticeDrain:
    return BudgetNoticeDrain(tenant.forge_budgets, tenant.event_log)


def _announced(tenant: TenantServices, announce: Callable[[TenantServices, ITurnRunner], ITurnRunner]) -> ITurnRunner:
    return announce(tenant, tenant.turns)
