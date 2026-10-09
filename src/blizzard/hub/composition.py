"""The hub's fleet-service composition (``bzh:dependency-injection``).

One place the store-backed collaborators are constructed and injected. Controllers read
the stores through their **read** Protocols and mutate only through the services
(``bzh:controller-read-only``); every chunk seam below — ``HubServices.chunks``'s bundle and
every domain service's own narrow parameter alike — is the same instance built here."""

from __future__ import annotations

import tempfile

# Wires the residual cycle-check lock — debt, blizzard-context:/architecture/system-shape/exclusive-writes.md —
# and the tenant cache's lock, which guards this process's memory, not a store decision
# ast-grep-ignore: bzh:store-exclusive-write
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

import httpx
from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.tracer import IPlatformTracer
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.auth.auth_state import IWriteAuthStateRepository
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.identities import IReadIdentityRepository
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.auth_state_repository import AuthStateRepository
from blizzard.hub.auth.internal.hub_admin_repository import HubAdminRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.identity_transaction import IdentityTransactions
from blizzard.hub.auth.internal.membership_repository import MembershipRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.superuser_bootstrap_repository import SuperuserBootstrapRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.memberships import IReadMembershipRepository
from blizzard.hub.auth.oauth.internal.factory import ProviderEntry
from blizzard.hub.auth.oauth.registry import IOAuthProviderRegistry
from blizzard.hub.auth.service import AuthService
from blizzard.hub.auth.sessions import IReadSessionRepository
from blizzard.hub.auth.signing import SigningKeyService
from blizzard.hub.auth.throttle import IpThrottle
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.config import OAuthProviderConfig
from blizzard.hub.delivery.command_runner import IHubCommandRunner
from blizzard.hub.delivery.hub_node import HubNodeExecutor
from blizzard.hub.delivery.internal.hub_command_runner import SubprocessHubCommandRunner
from blizzard.hub.delivery.internal.hub_workdir import FilesystemHubWorkdir
from blizzard.hub.delivery.marker_auth import MarkerAuthority
from blizzard.hub.delivery.workdir import IHubWorkdir
from blizzard.hub.domain.chunk.dependencies import DependencyService
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ingest import IngestService
from blizzard.hub.domain.chunk.model import WorkRef, WorkRefLabel
from blizzard.hub.domain.chunk.ports.stores import ChunkReadStores, ChunkStores
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import IReadConfigChanges, ISecretReferences
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.config.secrets import IHubKeyProvider, ISecretCatalog, ISecretReader
from blizzard.hub.domain.config.work_sources import IReadWorkSourceRepository
from blizzard.hub.domain.execution.apply import ApplyService
from blizzard.hub.domain.execution.claim import ClaimService
from blizzard.hub.domain.execution.decisions import DecisionService, RequeueService
from blizzard.hub.domain.execution.detach import DetachService
from blizzard.hub.domain.execution.facts import FactIngestService
from blizzard.hub.domain.execution.fleet import FleetService
from blizzard.hub.domain.execution.questions import QuestionService
from blizzard.hub.domain.garden.delivery.materialize import GardenDelivery
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.delivery.validation import CommitResolver
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import (
    FindingExitService,
    IReadFindingRepository,
    IReadFindingSetRepository,
)
from blizzard.hub.domain.garden.findings.trend import GardenTrendService
from blizzard.hub.domain.garden.proposals.closure import (
    GardenProposalClosureService,
    IReadGardenProposalClosureRepository,
)
from blizzard.hub.domain.garden.proposals.model import (
    GardenProposalAuthoring,
    IReadGardenProposalRepository,
    RoutineGardenProposalReader,
)
from blizzard.hub.domain.garden.proposals.resolution import AnsweredFindingsReader, GardenProposalDeliveryResolution
from blizzard.hub.domain.garden.review.materialize import ReviewFindingsMaterialize
from blizzard.hub.domain.garden.review.service import ReviewFindingsRecorder
from blizzard.hub.domain.garden.routines import (
    IReadRoutineRepository,
    IReadRoutineScopeRepository,
    RoutineAuthoring,
    RoutineLifecycle,
    RoutineScopeMembership,
)
from blizzard.hub.domain.garden.run_context import IReadRunContextRepository
from blizzard.hub.domain.garden.runs.baselines import RoutineBaselineService
from blizzard.hub.domain.garden.runs.history import GardenRunService
from blizzard.hub.domain.garden.runs.run import RunService
from blizzard.hub.domain.garden.runs.sweeps import GardenSweepsService
from blizzard.hub.domain.garden.scopes import IReadScopeRepository, ScopeLifecycle, ScopeRegistry
from blizzard.hub.domain.graph.authoring import GraphMintService
from blizzard.hub.domain.graph.lifecycle import GraphLifecycleService
from blizzard.hub.domain.graph.model import GraphDoc, IReadGraphRepository
from blizzard.hub.domain.observability.analytics.derivation import EventDerivationReconciler, EventDerivationService
from blizzard.hub.domain.observability.analytics.operational import IReadOperationalAnalytics
from blizzard.hub.domain.observability.analytics.queries import IReadAnalyticsEventQueries
from blizzard.hub.domain.observability.egress.backfill import EgressBackfill
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.event_rows import FilePathPolicy
from blizzard.hub.domain.observability.egress.reset import EgressReset
from blizzard.hub.domain.observability.egress.status import EgressStatusReader
from blizzard.hub.domain.observability.egress.sweep import EgressSweep
from blizzard.hub.domain.observability.forge_status import FULL_PASS_FLOOR_INTERVALS, AnnotationReconciler
from blizzard.hub.domain.observability.hub_events import HubEventLogService
from blizzard.hub.domain.observability.tracing.replay import TraceReplay
from blizzard.hub.domain.observability.tracing.status import TraceStatusReader
from blizzard.hub.domain.observability.tracing.sweep import TraceExportSweep
from blizzard.hub.domain.observability.transcripts import (
    IReadTranscriptSegments,
    TranscriptCaps,
    TranscriptIngestService,
)
from blizzard.hub.domain.operations.complete import CompleteService
from blizzard.hub.domain.operations.delete import DeleteService
from blizzard.hub.domain.operations.edit import EditService
from blizzard.hub.domain.operations.pause import PauseService
from blizzard.hub.domain.operations.promote import PromoteService
from blizzard.hub.domain.operations.queue import GroupService, QueueService
from blizzard.hub.domain.operations.restart import RestartService
from blizzard.hub.domain.operations.stop import StopService
from blizzard.hub.domain.runners.enrollment import RunnerEnrollmentService
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RetiredRunnerGuard
from blizzard.hub.domain.work_items.closure import CloseIntentDrainer
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.egress.factory import EgressUnavailable, build_egress_writer
from blizzard.hub.egress.space import free_bytes
from blizzard.hub.egress.writer import EgressWriterSettings, IEgressWriter, mint_process_token
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.events.brokers import TenantBrokers
from blizzard.hub.forge.internal.commit_resolver import GitHubCommitResolver
from blizzard.hub.forge.repository_commits import ICommitForge, RepositoryCommitResolver
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.secrets import StoreSecretReader, secret_cipher
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.analytics_event_query_store import AnalyticsEventQueryStore
from blizzard.hub.store.internal.analytics_operational_store import AnalyticsOperationalStore
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.config_apply_store import ConfigApplyStore
from blizzard.hub.store.internal.config_change_store import ConfigChangeStore
from blizzard.hub.store.internal.config_import_store import ConfigImportStore
from blizzard.hub.store.internal.config_revisions_store import ConfigRevisionsStore
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.finding_store import FindingSetStore, FindingStore
from blizzard.hub.store.internal.forge_annotation_store import ForgeAnnotationStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.garden_proposal_closure_store import GardenProposalClosureStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.garden_run_store import GardenRunStore
from blizzard.hub.store.internal.garden_sweeps_store import GardenSweepsStore
from blizzard.hub.store.internal.garden_trend_store import GardenTrendStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.hub_event_log_store import HubEventLogStore
from blizzard.hub.store.internal.hub_scoped_reads import HubScopedReads, build_hub_scoped_reads
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from blizzard.hub.store.internal.routine_scope_store import RoutineScopeStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.secret_referrers import SecretReferrersStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.sole_tenant import SoleTenantConnections, SoleTenantUnresolved
from blizzard.hub.store.internal.trace_store import TraceStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.system_artifacts import PACKAGED as SYSTEM_ARTIFACTS_PACKAGED
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.tenancy import StoreScope, TenantId
from blizzard.hub.work_sources.source import IWorkSourceRegistry


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
    ingest: IngestService
    promote: PromoteService
    claim: ClaimService
    apply: ApplyService
    decisions: DecisionService
    requeue: RequeueService
    restart: RestartService
    detach: DetachService
    pause: PauseService
    stop: StopService
    complete: CompleteService
    edit: EditService
    #: Declare/release a dependency edge between two chunks — declaring under the same
    #: row lock ``claim``/``edit``/``restart``/``delete`` already take, plus a residual
    #: fleet-wide lock the cycle check alone still needs.
    dependencies: DependencyService
    #: The unacquired-chunk delete/withdraw service — the composite write
    #: ``WorkItemEditService.withdraw`` also reaches through for an unacquired holder.
    delete: DeleteService
    facts: FactIngestService
    #: The transcript lane's ingest policy — the write side; ``transcripts``
    #: above is the same store's read Protocol.
    transcript_ingest: TranscriptIngestService
    graph_mint: GraphMintService
    graph_lifecycle: GraphLifecycleService
    questions: QuestionService
    queue: QueueService
    group: GroupService
    fleet: FleetService
    enrollment: RunnerEnrollmentService
    #: The fleet registry's read Protocol — the same store instance as ``fleet``'s writes.
    registry: IReadRunnerRegistry
    hub_node: HubNodeExecutor
    events: EventBroker
    #: The one event-log recorder every hub-side authoring site shares.
    event_log: EventLogService
    clock: IClock
    default_graph_doc: GraphDoc
    default_graph_yaml: str
    #: The published ``ArtifactScope.SYSTEM`` set — the loader itself, so a read through it
    #: stays fresh (``bzh:system-scope-reads-live``); injected rather than a module singleton.
    system_artifacts: PackagedSystemArtifacts
    work_sources: IWorkSourceRegistry
    #: Renders a work ref's source-native token.
    work_ref_label: WorkRefLabel
    #: The user read repository — every write still goes through ``auth``.
    users: IReadUserRepository
    #: The transcript-segment read Protocol — the operator-plane index and
    #: content routes' own seam (``bzh:controller-read-only``).
    transcripts: IReadTranscriptSegments
    #: The same reconciler's own service — the re-derive route's own
    #: scoped, bounded seam, exposed separately from a full sweep pass.
    event_derivation_service: EventDerivationService
    #: The analytics event query Protocol — the events/counts routes'
    #: own read-only seam (``bzh:controller-read-only``); no write repository backs it.
    analytics_events: IReadAnalyticsEventQueries
    #: The operational-datasets query Protocol — the durations/spend/
    #: outcomes routes' own read-only seam; no write repository backs it either.
    operational_analytics: IReadOperationalAnalytics
    #: The scope read Protocol — the same store instance as the two
    #: services below's writes.
    scopes: IReadScopeRepository
    #: Mint-on-name and edit-description over a scope.
    scope_registry: ScopeRegistry
    #: The scope retire/enable brake.
    scope_lifecycle: ScopeLifecycle
    #: Secret metadata only — never a sealed row (``bzh:secret-write-only``).
    secret_catalog: ISecretCatalog
    #: The active configured records naming each secret.
    secret_references: ISecretReferences
    #: The one writer of configured records — work sources, repositories and secrets, each write with its change row.
    config_authoring: ConfigAuthoring
    #: Stored work-source records (the read half).
    work_source_records: IReadWorkSourceRepository
    #: Stored repository records (the read half).
    repository_records: IReadRepositoryRecordRepository
    #: The configuration change log.
    config_changes: IReadConfigChanges
    #: The routine read Protocol — the same store instance as
    #: ``routine_authoring``'s writes.
    routines: IReadRoutineRepository
    #: The routine_scopes join's read Protocol — the declared many-to-many
    #: a routine's own ``default_scope_slug`` is a member of.
    routine_scopes: IReadRoutineScopeRepository
    #: Link/unlink a routine's own `routine_scopes` set, refusing to unlink its default.
    routine_scope_membership: RoutineScopeMembership
    #: Create and edit a routine, minting its default scope on demand.
    routine_authoring: RoutineAuthoring
    #: The routine retire/enable brake.
    routine_lifecycle: RoutineLifecycle
    #: Mint and ingest a hub work item from a routine, in one act.
    routine_run: RunService
    #: The per-scope delta baseline a routine has swept, and how much has landed since
    #: — the run dialog's pre-submit read.
    routine_baselines: RoutineBaselineService
    #: The finding read Protocol.
    findings: IReadFindingRepository
    #: The human-driven exit verbs over findings — resolve/confirm-gone/wont-fix/
    #: not-a-finding/supersede/reopen.
    finding_exit: FindingExitService
    #: The finding-set read Protocol — one set per delivered artifact list.
    finding_sets: IReadFindingSetRepository
    #: The garden-proposal read Protocol.
    garden_proposals: IReadGardenProposalRepository
    #: Create a garden proposal, rejecting an empty `findings` list.
    garden_proposal_authoring: GardenProposalAuthoring
    #: The garden-proposal-closure read Protocol.
    garden_proposal_closures: IReadGardenProposalClosureRepository
    #: Pass or accept a garden proposal, minting a linked hub work item by default.
    garden_proposal_closure: GardenProposalClosureService
    #: A routine's open garden proposals — closed ones filtered out.
    routine_garden_proposals: RoutineGardenProposalReader
    #: A run's identity — routine, scope, and mode; read-only (``bzh:controller-read-only``).
    run_context: IReadRunContextRepository
    #: The findings a chunk's own accepted, minted garden proposal answers
    #: — the worker's per-chunk read, distinct from the routine-bucket read
    #: `findings`/`run_context` back.
    answered_findings: AnsweredFindingsReader
    #: A run's finding bucket — what it is shown and may cite.
    finding_bucket: FindingBucketReader
    #: Records a delivering node-step's garden delivery: validate, then materialize in one transaction.
    garden_delivery: GardenDeliveryRecorder
    #: Record a delivery lane's deferred review findings, once per chunk.
    review_findings: ReviewFindingsRecorder
    #: Resolves a cited commit against the configured forge.
    commit_resolver: CommitResolver
    #: A routine's finding inflow-against-outflow over a window.
    garden_trend: GardenTrendService
    #: A routine's per-scope last-swept table and windowed measurement series.
    garden_sweeps: GardenSweepsService
    #: A routine's runs are readable — the run list and one run's own delta.
    garden_run: GardenRunService


@dataclass(frozen=True)
class HubServices:
    """The hub-level services — identity, sessions, signing, exports and sweeps — shared by every
    tenant and stashed on ``app.state.services``; a tenant's own services are opened through
    :attr:`tenants`."""

    #: Opens a tenant's services — the only way from the hub-level services to a tenant's.
    tenants: TenantServicesOpener
    #: The hub-scoped reads a runner's credential resolves its tenant through — held only by the
    #: runner-auth edge (``bzh:hub-scoped-reads-confined``).
    hub_scoped_reads: HubScopedReads
    #: The read registry of the tenant a runner credential resolved to; raises ``UnknownTenant`` or
    #: ``TenantClosed`` for a tenant there are no stores to open for.
    tenant_registry: Callable[[TenantId], IReadRunnerRegistry]
    #: The mid-run marker-write capability authority — one shared instance,
    #: so every reader agrees on the one live token per (chunk, node, epoch).
    marker_authority: MarkerAuthority
    #: The hub's own occurrences — its exports', its startup — recorded in ``hub_event_log``, published to no stream.
    hub_event_log: HubEventLogService
    clock: IClock
    #: The published ``ArtifactScope.SYSTEM`` set — the loader itself, so a read through it
    #: stays fresh (``bzh:system-scope-reads-live``); injected rather than a module singleton.
    system_artifacts: PackagedSystemArtifacts
    #: The close-intent drain sweep, over the sole tenant's services.
    close_drain: CloseIntentDrainer
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
    #: The configured OAuth provider registry — empty when none is configured.
    oauth_providers: IOAuthProviderRegistry
    #: Per-IP token-bucket throttle shared by the authorize/callback routes.
    auth_throttle: IpThrottle
    #: The non-chunk auth/security fact log — ``login_failed``/``sso_refused``.
    auth_facts: AuthFactsService
    #: The hub's IdP signing-key lifecycle — ``None`` when no keypair exists.
    signing: SigningKeyService | None
    #: The reverse-proxy trust set — empty by default, so forwarded headers
    #: are ignored from every peer.
    trusted_proxies: TrustedProxies
    #: The transcript-event derivation sweep, over the sole tenant's services.
    event_derivation: EventDerivationReconciler
    #: The forge-status annotation sweep, over the sole tenant's services.
    annotation: AnnotationReconciler | None


@dataclass(frozen=True)
class TenantStores:
    """One tenant's repository set and the leaf services over it, every adapter holding the same
    :class:`ScopedConnections`. Opened through :meth:`HubCore.open`; every other hub wiring takes
    these instances from it rather than building its own copy (pinned by
    ``tests/test_hub_core_sharing.py``)."""

    connections: ScopedConnections
    registry: RunnerRegistryStore
    work_item_store: WorkItemStore
    chunk_stores: ChunkStores
    #: The tenant's cycle-check lock — dependencies never cross tenants.
    cycle_lock: threading.Lock
    delete: DeleteService
    finding_store: FindingStore
    finding_exit: FindingExitService
    garden_proposal_store: GardenProposalStore
    garden_proposal_closure_store: GardenProposalClosureStore
    work_item_edits: WorkItemEditService
    garden_proposal_resolution: GardenProposalDeliveryResolution
    #: Sealed secret rows — composition-only, never a ``HubServices`` field.
    secrets: SecretStore
    #: Work-source records — composition-only write half; ``HubServices`` carries the read half.
    work_source_records: WorkSourceRecordStore
    #: Repository records — composition-only write half; ``HubServices`` carries the read half.
    repository_records: RepositoryRecordStore
    #: Which active configured records name each secret, across every referring kind.
    secret_referrers: SecretReferrersStore
    config_changes: ConfigChangeStore
    config_apply: ConfigApplyStore
    config_import: ConfigImportStore
    clock: IClock

    @property
    def scope(self) -> StoreScope:
        return self.connections.scope


def build_tenant_stores(connections: ScopedConnections, *, clock: IClock) -> TenantStores:
    """Build the :class:`TenantStores` of the tenant ``connections`` is scoped to."""
    registry = RunnerRegistryStore(connections)
    work_item_store = WorkItemStore(connections)
    chunk_stores = build_chunk_stores(connections, clock, registry=registry)
    cycle_lock = threading.Lock()
    delete = DeleteService(
        items=work_item_store,
        clock=clock,
        exclusive=chunk_stores.exclusive,
        cycle_lock=cycle_lock,
    )
    finding_store = FindingStore(connections)
    finding_exit = FindingExitService(repo=finding_store, clock=clock)
    garden_proposal_store = GardenProposalStore(connections)
    garden_proposal_closure_store = GardenProposalClosureStore(connections)
    return TenantStores(
        connections=connections,
        registry=registry,
        work_item_store=work_item_store,
        chunk_stores=chunk_stores,
        cycle_lock=cycle_lock,
        delete=delete,
        finding_store=finding_store,
        finding_exit=finding_exit,
        garden_proposal_store=garden_proposal_store,
        garden_proposal_closure_store=garden_proposal_closure_store,
        secrets=SecretStore(connections),
        work_source_records=WorkSourceRecordStore(connections),
        repository_records=RepositoryRecordStore(connections),
        secret_referrers=SecretReferrersStore(connections),
        config_changes=ConfigChangeStore(connections),
        config_apply=ConfigApplyStore(connections),
        config_import=ConfigImportStore(connections),
        work_item_edits=WorkItemEditService(
            items=work_item_store,
            work_refs=chunk_stores.work_refs,
            record=chunk_stores.record,
            facts=chunk_stores.facts,
            clock=clock,
            delete=delete,
        ),
        garden_proposal_resolution=GardenProposalDeliveryResolution(
            closures=garden_proposal_closure_store,
            proposals=garden_proposal_store,
            findings=finding_store,
            exits=finding_exit,
        ),
        clock=clock,
    )


class TenantCache:
    """One entry per open tenant — its :class:`TenantStores` and, once the hub serves tenants
    (:meth:`serve`), the services built over them — built on its first open and kept until
    :meth:`evict` drops it.

    A hit answers from memory and issues no statement: closing a tenant evicts it, and its write
    paths still refuse a closed tenant. A miss confirms the tenant exists and is open
    (``confirm_open`` raises :class:`UnknownTenant` or :class:`TenantClosed`) under the cache's one
    lock before building, so two racing opens of a tenant never yield two entries — nor two of its
    cycle-check locks — and a deletion's evict, which waits on that lock, drops an entry its close
    raced. ``resident`` is the set a single-tenant hub serves before it knows its tenant's id; the
    first open of that tenant adopts it, with the services built for it, rather than building a
    second set."""

    def __init__(
        self,
        *,
        build: Callable[[StoreScope], TenantStores],
        confirm_open: Callable[[TenantId], None],
        resident: TenantStores | None = None,
    ) -> None:
        self._build = build
        self._confirm_open = confirm_open
        self._resident = resident
        self._resident_stores = resident
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
        """Drop ``tenant``'s entry, if cached — closing the objects its configured records built and
        dropping its event broker; the tenant's deletion calls this when it closes."""
        with self._lock:
            entry = self._entries.pop(tenant, None)
            serving = self._serving
        if serving is not None:
            serving.brokers.drop(tenant)
        if entry is not None and entry.served is not None:
            entry.served.live.objects.close()

    def _entry(self, scope: StoreScope) -> _TenantEntry:
        with self._lock:
            entry = self._entries.get(scope.tenant)
            if entry is None:
                # A deletion commits its close before it evicts, and evicting waits on this lock: a close
                # this check missed is followed by an evict that drops the entry cached here.
                self._confirm_open(scope.tenant)
                entry = _TenantEntry(self._adopt_resident(scope) or self._build(scope))
                self._entries[scope.tenant] = entry
            if entry.served is None and self._serving is not None:
                entry.served = self._serve(scope.tenant, entry.stores, self._serving)
            return entry

    def _serve(self, tenant: TenantId, stores: TenantStores, serving: TenantServing) -> ServedTenant:
        if stores is self._resident_stores:
            serving.brokers.adopt(tenant, serving.resident.services.events)
            return serving.resident
        return serving.build(stores, serving.brokers.for_tenant(tenant))

    def _adopt_resident(self, scope: StoreScope) -> TenantStores | None:
        resident = self._resident
        if resident is None:
            return None
        try:
            resident_scope = resident.scope
        except SoleTenantUnresolved:
            # The store holds several tenants, so the resident set names none of them.
            self._resident = None
            return None
        if resident_scope != scope:
            return None
        self._resident = None
        return resident


@dataclass
class _TenantEntry:
    stores: TenantStores
    served: ServedTenant | None = None


@dataclass(frozen=True)
class TenantSweeps:
    """A tenant's sweep reconcilers, built with its services."""

    close_drain: CloseIntentDrainer
    event_derivation: EventDerivationReconciler
    annotation: AnnotationReconciler


@dataclass(frozen=True)
class ServedTenant:
    """What serving one tenant builds over its :class:`TenantStores`: its services, its sweeps, and
    the live configuration its work sources build through."""

    services: TenantServices
    sweeps: TenantSweeps
    live: LiveConfig


@dataclass(frozen=True)
class TenantServing:
    """How a :class:`TenantCache` serves its tenants: ``build`` wires a tenant's services over its
    stores and its broker from ``brokers``; ``resident`` is the sole tenant's, wired before its id is
    known and adopted with the resident stores."""

    build: Callable[[TenantStores, EventBroker], ServedTenant]
    brokers: TenantBrokers
    resident: ServedTenant


@dataclass(frozen=True)
class TenantServicesOpener:
    """Opens a tenant's services — the hub-level services' only way to a tenant's."""

    #: ``scope``'s tenant's services, built once per tenant; raises ``UnknownTenant`` or
    #: ``TenantClosed`` for a tenant there are none to open for.
    open_services: Callable[[StoreScope], TenantServices]
    #: The services of the store's only tenant — the ones a single-tenant hub serves.
    sole_tenant: TenantServices
    #: The store's only tenant — the one a single-tenant hub serves every request in.
    sole_scope: Callable[[], StoreScope]
    #: The open tenants and their names, held in memory — what a request's tenant is resolved against.
    open_tenants: OpenTenants


@dataclass(frozen=True)
class HubCore:
    """The process-scoped hub-level stores and the per-tenant store cache, built once by
    :func:`build_hub_core`. A tenant's stores are opened through :meth:`open`."""

    store_connections: HubStoreConnections
    hub_scoped_reads: HubScopedReads
    auth_errors: RepoErrorFactory
    users: UserRepository
    #: The hub's own event log, and the service recording an occurrence in it.
    hub_events: HubEventLogStore
    hub_event_log: HubEventLogService
    tenants: TenantCache
    #: The stores of the store's only tenant — the set a single-tenant hub serves, and the one
    #: :meth:`open` returns for that tenant.
    sole_tenant: TenantStores
    clock: IClock

    def open(self, scope: StoreScope) -> TenantStores:
        """``scope``'s tenant's stores, built once per tenant. Raises :class:`UnknownTenant` or
        :class:`TenantClosed` for a tenant there are no stores to open for."""
        return self.tenants.open(scope)

    def open_services(self, scope: StoreScope) -> TenantServices:
        """``scope``'s tenant's services, refused as :meth:`open` refuses."""
        return self.tenants.open_services(scope)

    # The sole tenant's members the hub process reads at boot and wires its work-source registry from.

    @property
    def tenant_connections(self) -> ScopedConnections:
        return self.sole_tenant.connections

    @property
    def secrets(self) -> SecretStore:
        return self.sole_tenant.secrets

    @property
    def work_source_records(self) -> WorkSourceRecordStore:
        return self.sole_tenant.work_source_records

    @property
    def repository_records(self) -> RepositoryRecordStore:
        return self.sole_tenant.repository_records

    @property
    def config_import(self) -> ConfigImportStore:
        return self.sole_tenant.config_import

    @property
    def work_item_store(self) -> WorkItemStore:
        return self.sole_tenant.work_item_store

    @property
    def work_item_edits(self) -> WorkItemEditService:
        return self.sole_tenant.work_item_edits

    @property
    def garden_proposal_resolution(self) -> GardenProposalDeliveryResolution:
        return self.sole_tenant.garden_proposal_resolution


@dataclass(frozen=True)
class LiveConfig:
    """One tenant's built-object cache and the one secret reader its builders reveal
    through — the reader reaches nothing else (``bzh:secret-write-only``)."""

    objects: ConfigObjectCache
    secrets: ISecretReader


def build_live_config(stores: TenantStores, *, secret_keys: IHubKeyProvider) -> LiveConfig:
    """The :class:`LiveConfig` of the tenant ``stores`` serve."""
    return LiveConfig(
        objects=ConfigObjectCache(ConfigRevisionsStore(stores.connections)),
        secrets=StoreSecretReader(catalog=stores.secrets, sealed=stores.secrets, cipher=secret_cipher(secret_keys)),
    )


def build_config_authoring(stores: TenantStores, *, secret_keys: IHubKeyProvider) -> ConfigAuthoring:
    """The one configured-record writer of the tenant ``stores`` serve, sealing under ``secret_keys``."""
    return ConfigAuthoring(
        work_sources=stores.work_source_records,
        repositories=stores.repository_records,
        secrets=stores.secrets,
        cipher=secret_cipher(secret_keys),
        apply_writer=stores.config_apply,
        import_writer=stores.config_import,
        clock=stores.clock,
    )


def build_process_core(engine: Engine) -> HubCore:
    """:func:`build_hub_core` on the system clock."""
    return build_hub_core(engine, clock=SystemClock())


def build_hub_core(engine: Engine, *, clock: IClock) -> HubCore:
    """Build the :class:`HubCore`."""
    store_errors = HubStoreErrorFactory(get_logger("blizzard.hub.store"))
    store_connections = HubStoreConnections(engine, store_errors)
    hub_scoped_reads = build_hub_scoped_reads(store_connections)
    sole_tenant = build_tenant_stores(SoleTenantConnections(engine, store_errors), clock=clock)
    auth_errors = RepoErrorFactory(get_logger("blizzard.hub.auth"))
    hub_events = HubEventLogStore(store_connections)

    def build(scope: StoreScope) -> TenantStores:
        return build_tenant_stores(store_connections.scoped(scope), clock=clock)

    return HubCore(
        store_connections=store_connections,
        hub_scoped_reads=hub_scoped_reads,
        auth_errors=auth_errors,
        users=UserRepository(store_connections, auth_errors),
        hub_events=hub_events,
        hub_event_log=HubEventLogService(events=hub_events),
        tenants=TenantCache(build=build, confirm_open=hub_scoped_reads.tenants.confirm_open, resident=sole_tenant),
        sole_tenant=sole_tenant,
        clock=clock,
    )


def _work_ref_label(work_sources: IWorkSourceRegistry) -> WorkRefLabel:
    def label(ref: WorkRef) -> str | None:
        source = work_sources.get(ref.source)
        return source.label(ref) if source is not None else None

    return label


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


def _egress_paths(config: EgressConfig, key: bytes | None) -> tuple[EgressConfig, FilePathPolicy | None, str | None]:
    """The export's file path policy, or none and no ``events`` dataset when the policy needs a key it lacks. The
    third value names the unset key variable when a configured ``events`` dataset was dropped for it."""
    try:
        return config, FilePathPolicy(config.file_paths, key), None
    except ValueError:
        if "events" not in config.datasets:
            return config, None, None
        kept = replace(config, datasets=tuple(name for name in config.datasets if name != "events"))
        return kept, None, config.path_key_env


#: Builds a tenant's work-source registry over its stores and its live configuration.
type WorkSourceRegistryFactory = Callable[[TenantStores, LiveConfig], IWorkSourceRegistry]


@dataclass(frozen=True)
class TenantServiceParts:
    """The process-scoped inputs every tenant's services are wired from — one set, shared by every
    tenant, so no adapter or client is built twice."""

    clock: IClock
    hub_scoped_reads: HubScopedReads
    users: IReadUserRepository
    secret_keys: IHubKeyProvider
    work_sources: WorkSourceRegistryFactory
    marker_authority: MarkerAuthority
    system_artifacts: PackagedSystemArtifacts
    commit_forge: ICommitForge
    hub_command_runner: IHubCommandRunner
    hub_workdir: IHubWorkdir
    hub_marker_callback_base_url: str
    public_url: str | None
    transcript_caps: TranscriptCaps | None
    platform_tracer: IPlatformTracer | None
    annotation_interval_seconds: int


def build_tenant_services(stores: TenantStores, parts: TenantServiceParts, *, events: EventBroker) -> ServedTenant:
    """Construct and wire one tenant's services, sweeps and live configuration over its
    :class:`TenantStores`, publishing to ``events`` — the tenant's broker."""
    clock = parts.clock
    tenant_connections = stores.connections
    chunk_stores = stores.chunk_stores
    work_item_store = stores.work_item_store
    finding_store = stores.finding_store
    garden_proposal_store = stores.garden_proposal_store
    garden_proposal_closure_store = stores.garden_proposal_closure_store
    registry_store = stores.registry
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
    chunk_exclusive = chunk_stores.exclusive
    live = build_live_config(stores, secret_keys=parts.secret_keys)
    work_sources = parts.work_sources(stores, live)
    transcript_caps = parts.transcript_caps
    graph_store = GraphStore(tenant_connections)
    transcript_store = TranscriptSegmentStore(tenant_connections)
    event_store = TranscriptEventStore(tenant_connections)
    event_derivation_service = EventDerivationService(
        events=event_store, facts=chunk_facts, record=chunk_record, clock=clock
    )
    analytics_event_queries = AnalyticsEventQueryStore(tenant_connections)
    operational_analytics = AnalyticsOperationalStore(tenant_connections)
    # Recording an event is what publishes it (``bzh:operational-event-log``) — one
    # service, shared by every event-authoring call site below, over the same store and
    # broker instance every other collaborator holds.
    event_log = EventLogService(events=chunk_events, publisher=events)
    hub_node = HubNodeExecutor(
        facts=chunk_facts,
        artifacts=chunk_artifacts,
        delivery=chunk_delivery,
        hub_exec=chunk_hub_exec,
        escalations=chunk_escalations,
        events=event_log,
        runner=parts.hub_command_runner,
        workdir=parts.hub_workdir,
        clock=clock,
        marker_authority=parts.marker_authority,
        tenant=lambda: stores.scope.tenant,
        repositories=stores.repository_records,
        secrets=live.secrets,
        marker_callback_base_url=parts.hub_marker_callback_base_url,
        tracer=parts.platform_tracer,
        public_url=parts.public_url,
        work_sources=work_sources,
    )
    # One fleet service, shared: the API's pause routes and the fact ingest both land
    # registry facts, and two instances would be two of the same thing.
    detach = DetachService(route=chunk_route, exclusive=chunk_exclusive, clock=clock)
    # One id-keyed retired-runner refusal, shared by every runner-contact operation.
    retired = RetiredRunnerGuard(registry=registry_store)
    fleet = FleetService(
        registry=registry_store,
        routes=chunk_route,
        records=chunk_record,
        facts=chunk_facts,
        detach=detach,
        retired=retired,
        clock=clock,
    )
    enrollment = RunnerEnrollmentService(registry=registry_store, clock=clock)
    graph_mint = GraphMintService(graphs=graph_store, clock=clock)
    scope_store = ScopeStore(tenant_connections)
    scope_registry = ScopeRegistry(scopes=scope_store, clock=clock)
    routine_store = RoutineStore(tenant_connections)
    routine_scope_store = RoutineScopeStore(tenant_connections)
    finding_set_store = FindingSetStore(tenant_connections)
    run_context_store = RunContextStore(tenant_connections)
    garden_delivery_store = GardenDeliveryStore(tenant_connections)
    review_findings_store = ReviewFindingsStore(tenant_connections)
    garden_trend_store = GardenTrendStore(tenant_connections)
    garden_sweeps_store = GardenSweepsStore(tenant_connections)
    garden_run_store = GardenRunStore(tenant_connections)
    finding_bucket = FindingBucketReader(finding_store)
    garden_formats = GardenFormats()
    # Bound as `.resolve` (a plain `delivery.validation.CommitResolver` callable), not the bare
    # instance, so `HubServices.commit_resolver` carries no dependency on the concrete class.
    commit_resolver = RepositoryCommitResolver(
        repositories=stores.repository_records,
        secrets=live.secrets,
        forge=parts.commit_forge,
    ).resolve
    work_ref_label = _work_ref_label(work_sources)
    services = TenantServices(
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
        ingest=IngestService(record=chunk_record, exclusive=chunk_exclusive, clock=clock),
        promote=PromoteService(
            record=chunk_record, queue=chunk_queue, facts=chunk_facts, exclusive=chunk_exclusive, clock=clock
        ),
        claim=ClaimService(
            route=chunk_route,
            artifacts=chunk_artifacts,
            graphs=graph_store,
            registry=registry_store,
            retired=retired,
            exclusive=chunk_exclusive,
            clock=clock,
            label=work_ref_label,
        ),
        apply=ApplyService(
            facts=chunk_facts,
            movement=chunk_movement,
            decisions=chunk_decisions,
            escalations=chunk_escalations,
            exclusive=chunk_exclusive,
            route=chunk_route,
            artifacts=chunk_artifacts,
            retired=retired,
            clock=clock,
            hub_node_executor=hub_node,
            label=work_ref_label,
        ),
        decisions=DecisionService(
            facts=chunk_facts,
            route=chunk_route,
            decisions=chunk_decisions,
            exclusive=chunk_exclusive,
            retired=retired,
            clock=clock,
        ),
        requeue=RequeueService(movement=chunk_movement, route=chunk_route, exclusive=chunk_exclusive, clock=clock),
        restart=RestartService(movement=chunk_movement, graphs=graph_store, clock=clock, exclusive=chunk_exclusive),
        detach=detach,
        pause=PauseService(lifecycle=chunk_lifecycle, exclusive=chunk_exclusive, clock=clock),
        stop=StopService(lifecycle=chunk_lifecycle, exclusive=chunk_exclusive, clock=clock),
        complete=CompleteService(lifecycle=chunk_lifecycle, exclusive=chunk_exclusive, clock=clock),
        edit=EditService(record=chunk_record, graphs=graph_store, exclusive=chunk_exclusive),
        dependencies=DependencyService(
            dependencies=chunk_dependencies,
            exclusive=chunk_exclusive,
            clock=clock,
            cycle_lock=stores.cycle_lock,
        ),
        delete=stores.delete,
        facts=FactIngestService(
            facts=chunk_facts,
            route=chunk_route,
            escalations=chunk_escalations,
            questions=chunk_questions,
            usage=chunk_usage,
            events=event_log,
            fleet=fleet,
            retired=retired,
            clock=clock,
        ),
        transcript_ingest=TranscriptIngestService(
            store=transcript_store, retired=retired, clock=clock, caps=transcript_caps
        ),
        graph_mint=graph_mint,
        graph_lifecycle=GraphLifecycleService(graphs=graph_store, clock=clock),
        questions=QuestionService(questions=chunk_questions, clock=clock),
        queue=QueueService(queue=chunk_queue, record=chunk_record, clock=clock),
        group=GroupService(
            work_refs=chunk_work_refs,
            dependencies=chunk_dependencies,
            exclusive=chunk_exclusive,
            clock=clock,
            cycle_lock=stores.cycle_lock,
        ),
        fleet=fleet,
        enrollment=enrollment,
        registry=registry_store,
        hub_node=hub_node,
        events=events,
        event_log=event_log,
        clock=clock,
        default_graph_doc=PACKAGED.default.doc,
        default_graph_yaml=PACKAGED.default.text,
        system_artifacts=parts.system_artifacts,
        work_sources=work_sources,
        users=parts.users,
        transcripts=transcript_store,
        event_derivation_service=event_derivation_service,
        analytics_events=analytics_event_queries,
        operational_analytics=operational_analytics,
        scopes=scope_store,
        scope_registry=scope_registry,
        scope_lifecycle=ScopeLifecycle(scopes=scope_store, clock=clock),
        secret_catalog=stores.secrets,
        secret_references=stores.secret_referrers,
        config_authoring=build_config_authoring(stores, secret_keys=parts.secret_keys),
        work_source_records=stores.work_source_records,
        repository_records=stores.repository_records,
        config_changes=stores.config_changes,
        routines=routine_store,
        routine_scopes=routine_scope_store,
        routine_scope_membership=RoutineScopeMembership(routine_scopes=routine_scope_store, clock=clock),
        routine_authoring=RoutineAuthoring(
            routines=routine_store,
            graphs=graph_store,
            scope_registry=scope_registry,
            clock=clock,
        ),
        routine_lifecycle=RoutineLifecycle(routines=routine_store, clock=clock),
        routine_run=RunService(
            routines=routine_store,
            scopes=scope_store,
            routine_scopes=routine_scope_store,
            graphs=graph_store,
            finding_sets=finding_set_store,
            items=work_item_store,
            work_refs=chunk_work_refs,
            clock=clock,
        ),
        routine_baselines=RoutineBaselineService(finding_sets=finding_set_store, delivery=chunk_delivery),
        findings=finding_store,
        finding_exit=stores.finding_exit,
        finding_sets=finding_set_store,
        garden_proposals=garden_proposal_store,
        garden_proposal_authoring=GardenProposalAuthoring(
            proposals=garden_proposal_store, closures=garden_proposal_closure_store, clock=clock
        ),
        garden_proposal_closures=garden_proposal_closure_store,
        garden_proposal_closure=GardenProposalClosureService(
            closures=garden_proposal_closure_store,
            items=stores.work_item_edits,
            default_graph=lambda: graph_mint.ensure_default(
                PACKAGED.default.doc, definition_yaml=PACKAGED.default.text
            ),
            clock=clock,
        ),
        routine_garden_proposals=RoutineGardenProposalReader(
            proposals=garden_proposal_store, closures=garden_proposal_closure_store
        ),
        run_context=run_context_store,
        answered_findings=AnsweredFindingsReader(
            closures=garden_proposal_closure_store, proposals=garden_proposal_store, findings=finding_store
        ),
        finding_bucket=finding_bucket,
        garden_delivery=GardenDeliveryRecorder(
            artifacts=chunk_artifacts,
            buckets=finding_bucket,
            materialize=GardenDelivery(delivery=garden_delivery_store, clock=clock),
            formats=garden_formats,
            resolve_commit=commit_resolver,
        ),
        review_findings=ReviewFindingsRecorder(
            artifacts=chunk_artifacts,
            materialize=ReviewFindingsMaterialize(delivery=review_findings_store, clock=clock),
            formats=garden_formats,
        ),
        commit_resolver=commit_resolver,
        garden_trend=GardenTrendService(repo=garden_trend_store),
        garden_sweeps=GardenSweepsService(
            repo=garden_sweeps_store, scopes=scope_store, routine_scopes=routine_scope_store
        ),
        garden_run=GardenRunService(
            repo=garden_run_store,
            chunk_records=chunk_record,
            chunk_facts=chunk_facts,
            findings=finding_store,
            formats=garden_formats,
            clock=clock,
        ),
    )
    sweeps = TenantSweeps(
        close_drain=CloseIntentDrainer(
            corpus=parts.hub_scoped_reads.corpus,
            tenant=lambda: tenant_connections.scope.tenant,
            delivery=chunk_delivery,
            artifacts=chunk_artifacts,
            events=event_log,
            work_sources=work_sources,
            clock=clock,
            public_url=parts.public_url,
        ),
        event_derivation=EventDerivationReconciler(
            corpus=parts.hub_scoped_reads.corpus,
            tenant=lambda: tenant_connections.scope.tenant,
            service=event_derivation_service,
            events=event_store,
            clock=clock,
        ),
        annotation=AnnotationReconciler(
            corpus=parts.hub_scoped_reads.corpus,
            facts=chunk_facts,
            tenant=lambda: tenant_connections.scope.tenant,
            work_sources=work_sources,
            memory=ForgeAnnotationStore(tenant_connections),
            clock=clock,
            full_pass_floor=timedelta(seconds=FULL_PASS_FLOOR_INTERVALS * parts.annotation_interval_seconds),
        ),
    )
    return ServedTenant(services=services, sweeps=sweeps, live=live)


def build_services(
    core: HubCore,
    *,
    work_sources: WorkSourceRegistryFactory,
    secret_keys: IHubKeyProvider,
    brokers: TenantBrokers | None = None,
    hub_command_runner: IHubCommandRunner | None = None,
    hub_workdir: IHubWorkdir | None = None,
    hub_workdir_root: Path | None = None,
    hub_marker_callback_base_url: str = "",
    public_url: str | None = None,
    forge_http_client: httpx.Client | None = None,
    oauth_providers: Sequence[OAuthProviderConfig] = (),
    oauth_http_client: httpx.Client | None = None,
    oauth_registry: IOAuthProviderRegistry | None = None,
    signing_keys_dir: Path | None = None,
    trusted_proxies: TrustedProxies | None = None,
    transcript_caps: TranscriptCaps | None = None,
    system_artifacts: PackagedSystemArtifacts | None = None,
    trace_exporter: ITraceExporter | None = None,
    tracing: TracingConfig | None = None,
    tracing_settings: TracingSettings | None = None,
    platform_tracer: IPlatformTracer | None = None,
    egress: EgressConfig | None = None,
    egress_path_key: bytes | None = None,
    annotation_interval_seconds: int = 120,
) -> HubServices:
    """Construct and wire the hub-level services over the shared :class:`HubCore`, and have its
    tenant cache serve each tenant's services (:func:`build_tenant_services`) — the sole tenant's
    built here, which the sweeps and exports run over. ``work_sources`` builds a tenant's
    work-source registry; ``hub_command_runner``/``hub_workdir`` are the hub command node's
    mechanism seams, left ``None`` for real adapters; an explicit ``oauth_registry`` wins over
    ``oauth_providers``."""
    clock = core.clock
    store_connections = core.store_connections
    brokers = brokers or TenantBrokers()
    marker_authority = MarkerAuthority()
    system_artifacts = system_artifacts or SYSTEM_ARTIFACTS_PACKAGED
    parts = TenantServiceParts(
        clock=clock,
        hub_scoped_reads=core.hub_scoped_reads,
        users=core.users,
        secret_keys=secret_keys,
        work_sources=work_sources,
        marker_authority=marker_authority,
        system_artifacts=system_artifacts,
        # Bound as `.resolve` per tenant (a plain `delivery.validation.CommitResolver` callable), so
        # `TenantServices.commit_resolver` carries no dependency on the concrete class.
        commit_forge=GitHubCommitResolver(forge_http_client or httpx.Client(timeout=10.0)),
        hub_command_runner=hub_command_runner or SubprocessHubCommandRunner(),
        hub_workdir=hub_workdir
        or FilesystemHubWorkdir(hub_workdir_root or Path(tempfile.gettempdir()) / "blizzard-hub-workdirs"),
        hub_marker_callback_base_url=hub_marker_callback_base_url,
        public_url=public_url,
        transcript_caps=transcript_caps,
        platform_tracer=platform_tracer,
        annotation_interval_seconds=annotation_interval_seconds,
    )
    # The sole tenant's id is read from the store on first use, so its services publish to a broker
    # made here, adopted as that tenant's once the cache first opens it.
    sole = build_tenant_services(core.sole_tenant, parts, events=EventBroker())
    core.tenants.serve(
        TenantServing(
            build=lambda stores, events: build_tenant_services(stores, parts, events=events),
            brokers=brokers,
            resident=sole,
        )
    )
    stores = core.sole_tenant
    tenant_connections = stores.connections
    registry_store = stores.registry
    graph_store = sole.services.graphs
    hub_events = core.hub_events
    hub_event_log = core.hub_event_log
    trace_store = TraceStore(
        store_connections,
        hub_events=hub_events,
        tenant=lambda: tenant_connections.scope.tenant,
        corpus=core.hub_scoped_reads.corpus,
        graphs=graph_store,
        names=registry_store,
        label=sole.services.work_ref_label,
    )
    trace_config = tracing or TracingConfig()
    trace_export = (
        TraceExportSweep(
            steps=trace_store,
            exporter=trace_exporter,
            events=hub_event_log,
            clock=clock,
            config=trace_config,
        )
        if trace_exporter is not None
        else None
    )
    trace_replay = TraceReplay(steps=trace_store, exporter=trace_exporter, clock=clock, config=trace_config)
    trace_status = TraceStatusReader(
        settings=tracing_settings or TracingSettings("disabled"),
        status=trace_store,
        steps=trace_store,
        clock=clock,
        replay_max_window=trace_config.replay_max_window,
    )
    egress_config, egress_paths, egress_missing_key = _egress_paths(egress or EgressConfig(), egress_path_key)
    egress_writer_settings = EgressWriterSettings(egress_config.max_rows_per_file, egress_config.min_free_bytes)
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
    egress_store = EgressStore(store_connections, hub_events=hub_events, usage=core.hub_scoped_reads.corpus)
    egress_event_reads = core.hub_scoped_reads.corpus
    egress_pass_lock = threading.Lock()
    egress_unavailable = egress_writer if isinstance(egress_writer, EgressUnavailable) else None
    # The missing key matters only to an export that runs; with it off, nothing would have been written anyway.
    egress_missing_path_key = egress_missing_key if egress_writer is not None and egress_unavailable is None else None
    egress_status = EgressStatusReader(
        config=egress_config,
        rejected=egress_unavailable is not None,
        missing_path_key=egress_missing_path_key,
        egress=egress_store,
        event_reads=egress_event_reads,
        steps=trace_store,
        clock=clock,
        free_space=lambda: free_bytes(egress_config.directory) if egress_config.directory is not None else None,
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
            config=egress_config,
            pass_lock=egress_pass_lock,
        )
        if egress_writer is not None and not isinstance(egress_writer, EgressUnavailable)
        else None
    )
    egress_reset = EgressReset(
        egress=egress_store,
        events=hub_event_log,
        clock=clock,
        config=egress_config,
        active=egress_export is not None,
        missing_path_key=egress_missing_path_key,
        pass_lock=egress_pass_lock,
    )
    egress_backfill = EgressBackfill(
        steps=trace_store,
        egress=egress_store,
        event_reads=egress_event_reads,
        paths=egress_paths,
        clock=clock,
        config=egress_config,
        writers=_backfill_writers(egress_config, egress_writer_settings) if egress_export is not None else None,
        missing_path_key=egress_missing_path_key,
    )
    # The identity spine — one error factory shared by the SQLAlchemy
    # adapters, so the same instances back both the Write Protocols and the reads.
    user_store = core.users
    auth_errors = core.auth_errors
    identity_store = IdentityRepository(store_connections, auth_errors)
    session_store = SessionRepository(store_connections, auth_errors)
    auth_state_store: IWriteAuthStateRepository = AuthStateRepository(store_connections, auth_errors)
    superuser_bootstrap_store = SuperuserBootstrapRepository(store_connections)
    # Built ahead of `auth` below, which records role-change facts through
    # this service rather than a raw write repository.
    auth_facts_service = AuthFactsService(facts=AuthFactsRepository(store_connections), clock=clock)
    memberships = MembershipRepository(store_connections)
    open_tenants = OpenTenants(core.hub_scoped_reads.tenants.open_tenants)
    auth = AuthService(
        users=user_store,
        identities=identity_store,
        sessions=session_store,
        auth_state=auth_state_store,
        clock=clock,
        superuser_bootstrap=superuser_bootstrap_store,
        auth_facts=auth_facts_service,
        memberships=memberships,
        hub_admins=HubAdminRepository(store_connections),
        transactions=IdentityTransactions(store_connections, auth_errors),
    )
    # The provider-login seam — one registry entry per configured
    # ``[[auth.oauth.provider]]``, empty when no providers are configured.
    oauth_registry = oauth_registry or ProviderEntry.registry(oauth_providers, http_client=oauth_http_client)
    # The hub's IdP signing-key lifecycle — constructed only when a keys
    # directory is passed; `None` otherwise.
    signing = SigningKeyService(signing_keys_dir) if signing_keys_dir is not None else None
    auth_throttle = IpThrottle(clock=clock)
    return HubServices(
        tenants=TenantServicesOpener(
            open_services=core.open_services,
            sole_tenant=sole.services,
            sole_scope=lambda: core.sole_tenant.scope,
            open_tenants=open_tenants,
        ),
        hub_scoped_reads=core.hub_scoped_reads,
        tenant_registry=lambda tenant: core.open(StoreScope(tenant)).registry,
        marker_authority=marker_authority,
        hub_event_log=hub_event_log,
        clock=clock,
        system_artifacts=system_artifacts,
        close_drain=sole.sweeps.close_drain,
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
        oauth_providers=oauth_registry,
        auth_throttle=auth_throttle,
        auth_facts=auth_facts_service,
        signing=signing,
        trusted_proxies=trusted_proxies if trusted_proxies is not None else TrustedProxies(),
        event_derivation=sole.sweeps.event_derivation,
        annotation=sole.sweeps.annotation,
    )
