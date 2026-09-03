"""The hub's fleet-service composition (``bzh:dependency-injection``).

One place the store-backed collaborators are constructed and injected. Controllers read
the stores through their **read** Protocols and mutate only through the services
(``bzh:controller-read-only``); every chunk seam below — ``HubServices.chunks``'s bundle and
every domain service's own narrow parameter alike — is the same instance built here."""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.auth_state import IWriteAuthStateRepository
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.identities import IReadIdentityRepository
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.auth_state_repository import AuthStateRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.superuser_bootstrap_repository import SuperuserBootstrapRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.oauth.internal.factory import ProviderEntry
from blizzard.hub.auth.oauth.registry import IOAuthProviderRegistry
from blizzard.hub.auth.service import AuthService
from blizzard.hub.auth.sessions import IReadSessionRepository
from blizzard.hub.auth.signing import SigningKeyService
from blizzard.hub.auth.throttle import IpThrottle
from blizzard.hub.auth.users import IReadUserRepository, IWriteUserRepository
from blizzard.hub.config import OAuthProviderConfig
from blizzard.hub.delivery.command_runner import IHubCommandRunner
from blizzard.hub.delivery.hub_node import HubNodeExecutor
from blizzard.hub.delivery.internal.hub_command_runner import SubprocessHubCommandRunner
from blizzard.hub.delivery.internal.hub_workdir import FilesystemHubWorkdir
from blizzard.hub.delivery.marker_auth import MarkerAuthority
from blizzard.hub.delivery.workdir import IHubWorkdir
from blizzard.hub.domain.analytics.derivation import EventDerivationReconciler, EventDerivationService
from blizzard.hub.domain.analytics.operational import IReadOperationalAnalytics
from blizzard.hub.domain.analytics.queries import IReadAnalyticsEventQueries
from blizzard.hub.domain.apply import ApplyService
from blizzard.hub.domain.chunks.stores import ChunkReadStores
from blizzard.hub.domain.claim import ClaimService
from blizzard.hub.domain.complete import CompleteService
from blizzard.hub.domain.decisions import DecisionService, RequeueService
from blizzard.hub.domain.delete import DeleteService
from blizzard.hub.domain.dependencies import DependencyService
from blizzard.hub.domain.detach import DetachService
from blizzard.hub.domain.edit import EditService
from blizzard.hub.domain.enrollment import RunnerEnrollmentService
from blizzard.hub.domain.facts import FactIngestService, RunnerFactsService
from blizzard.hub.domain.findings import FindingExitService, IReadFindingRepository, IReadFindingSetRepository
from blizzard.hub.domain.garden_delivery import CommitResolver
from blizzard.hub.domain.garden_delivery_materialize import GardenDelivery
from blizzard.hub.domain.garden_proposal_closure import (
    GardenProposalClosureService,
    IReadGardenProposalClosureRepository,
)
from blizzard.hub.domain.garden_proposals import GardenProposalAuthoring, IReadGardenProposalRepository
from blizzard.hub.domain.garden_run import GardenRunService
from blizzard.hub.domain.garden_sweeps import GardenSweepsService
from blizzard.hub.domain.garden_trend import GardenTrendService
from blizzard.hub.domain.graph import GraphDoc, IReadGraphRepository
from blizzard.hub.domain.graph_authoring import GraphMintService
from blizzard.hub.domain.graph_lifecycle import GraphLifecycleService
from blizzard.hub.domain.ingest import IngestService
from blizzard.hub.domain.pause import PauseService
from blizzard.hub.domain.promote import PromoteService
from blizzard.hub.domain.questions import QuestionService
from blizzard.hub.domain.queue import GroupService, QueueService
from blizzard.hub.domain.registry import FleetService, IReadRunnerRegistry
from blizzard.hub.domain.restart import RestartService
from blizzard.hub.domain.routine_baselines import RoutineBaselineService
from blizzard.hub.domain.routine_run import RunService
from blizzard.hub.domain.routines import IReadRoutineRepository, RoutineAuthoring
from blizzard.hub.domain.run_context import IReadRunContextRepository
from blizzard.hub.domain.scopes import IReadScopeRepository, ScopeLifecycle, ScopeRegistry
from blizzard.hub.domain.stop import StopService
from blizzard.hub.domain.transcripts import IReadTranscriptSegments, TranscriptCaps, TranscriptIngestService
from blizzard.hub.domain.work_closure import CloseIntentDrainer
from blizzard.hub.domain.work_item_materialization import WorkItemMaterializationReconciler
from blizzard.hub.domain.work_items import WorkItemEditService
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.forge.internal.commit_resolver import GitHubCommitResolver
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.analytics_event_query_store import AnalyticsEventQueryStore
from blizzard.hub.store.internal.analytics_operational_store import AnalyticsOperationalStore
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.finding_store import FindingSetStore, FindingStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.garden_proposal_closure_store import GardenProposalClosureStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.garden_run_store import GardenRunStore
from blizzard.hub.store.internal.garden_sweeps_store import GardenSweepsStore
from blizzard.hub.store.internal.garden_trend_store import GardenTrendStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from blizzard.hub.system_artifacts import PACKAGED as SYSTEM_ARTIFACTS_PACKAGED
from blizzard.hub.system_artifacts import PackagedSystemArtifacts
from blizzard.hub.work_sources.source import IWorkSourceRegistry


@dataclass(frozen=True)
class HubServices:
    """The wired fleet collaborators, stashed on ``app.state.services``."""

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
    #: Declare/release a dependency edge between two chunks (issue #456) — under the same
    #: shared claim lock ``claim``/``edit``/``restart`` already take.
    dependencies: DependencyService
    #: The unacquired-chunk delete/withdraw service (issue #364) — the composite write
    #: ``WorkItemEditService.withdraw`` also reaches through for an unacquired holder.
    delete: DeleteService
    facts: FactIngestService
    #: The transcript lane's ingest policy (blizzard#247) — the write side; ``transcripts``
    #: above is the same store's read Protocol.
    transcript_ingest: TranscriptIngestService
    graph_mint: GraphMintService
    graph_lifecycle: GraphLifecycleService
    runner_facts: RunnerFactsService
    questions: QuestionService
    queue: QueueService
    group: GroupService
    fleet: FleetService
    enrollment: RunnerEnrollmentService
    #: The fleet registry's read Protocol — the same store instance as ``fleet``'s writes.
    registry: IReadRunnerRegistry
    hub_node: HubNodeExecutor
    #: The mid-run marker-write capability authority (issue #230) — one shared instance,
    #: so every reader agrees on the one live token per (chunk, node, epoch).
    marker_authority: MarkerAuthority
    events: EventBroker
    clock: IClock
    default_graph_doc: GraphDoc
    default_graph_yaml: str
    #: The published ``ArtifactScope.SYSTEM`` set — the loader itself, so a read through it
    #: stays fresh (``bzh:system-scope-reads-live``); injected rather than a module singleton.
    system_artifacts: PackagedSystemArtifacts
    work_sources: IWorkSourceRegistry
    #: The close-intent drain sweep (blizzard#383) — built here because it needs the
    #: write-capable chunk repository, which only the composition root holds.
    close_drain: CloseIntentDrainer
    #: The delivery-materialization reconciler (blizzard#366) — built here for the same
    #: reason: it needs the write-capable chunk and work-item repositories.
    work_item_materialization: WorkItemMaterializationReconciler
    #: The session read repository (issue #91) — reads only (``bzh:controller-read-only``).
    sessions: IReadSessionRepository
    #: The identity-link read repository (issue #92) — a plain read, no domain service.
    identities: IReadIdentityRepository
    #: The user read repository (issue #94) — every write still goes through ``auth``.
    users: IReadUserRepository
    #: The identity domain service — sessions, the first-login linking rule, ``state``.
    auth: AuthService
    #: The configured OAuth provider registry (issue #92) — empty when none is configured.
    oauth_providers: IOAuthProviderRegistry
    #: Per-IP token-bucket throttle (issue #92) shared by the authorize/callback routes.
    auth_throttle: IpThrottle
    #: The non-chunk auth/security fact log (issue #92) — ``login_failed``/``sso_refused``.
    auth_facts: AuthFactsService
    #: The hub's IdP signing-key lifecycle (issue #95) — ``None`` when no keypair exists.
    signing: SigningKeyService | None
    #: The reverse-proxy trust set (issue #130) — empty by default, so forwarded headers
    #: are ignored from every peer.
    trusted_proxies: TrustedProxies
    #: The transcript-segment read Protocol (blizzard#247) — the operator-plane index and
    #: content routes' own seam (``bzh:controller-read-only``).
    transcripts: IReadTranscriptSegments
    #: The transcript-event derivation reconciler (blizzard#254) — built here because it
    #: needs the write-capable event store, which only the composition root holds.
    event_derivation: EventDerivationReconciler
    #: The same reconciler's own service (blizzard#254 D7) — the re-derive route's own
    #: scoped, bounded seam, exposed separately from a full sweep pass.
    event_derivation_service: EventDerivationService
    #: The analytics event query Protocol (blizzard#255 D6) — the events/counts routes'
    #: own read-only seam (``bzh:controller-read-only``); no write repository backs it.
    analytics_events: IReadAnalyticsEventQueries
    #: The operational-datasets query Protocol (blizzard#256 D1) — the durations/spend/
    #: outcomes routes' own read-only seam; no write repository backs it either.
    operational_analytics: IReadOperationalAnalytics
    #: The scope read Protocol (blizzard#389) — the same store instance as the two
    #: services below's writes.
    scopes: IReadScopeRepository
    #: Mint-on-name and edit-description over a scope (blizzard#389 D4).
    scope_registry: ScopeRegistry
    #: The scope retire/enable brake (blizzard#389 D3).
    scope_lifecycle: ScopeLifecycle
    #: The routine read Protocol (blizzard#389) — the same store instance as
    #: ``routine_authoring``'s writes.
    routines: IReadRoutineRepository
    #: Create and edit a routine, minting its default scope on demand (blizzard#389 D4).
    routine_authoring: RoutineAuthoring
    #: Mint, ingest, and promote a hub work item from a routine, in one act (blizzard#392).
    routine_run: RunService
    #: The per-scope delta baseline a routine has swept, and how much has landed since
    #: (D5) — the run dialog's pre-submit read.
    routine_baselines: RoutineBaselineService
    #: The finding read Protocol (blizzard#390).
    findings: IReadFindingRepository
    #: The human-driven exit verbs over findings — resolve/confirm-gone/wont-fix/
    #: not-a-finding/supersede/reopen (blizzard#394 Phase 1).
    finding_exit: FindingExitService
    #: The finding-set read Protocol (blizzard#390) — one set per delivered artifact list.
    finding_sets: IReadFindingSetRepository
    #: The garden-proposal read Protocol (blizzard#390).
    garden_proposals: IReadGardenProposalRepository
    #: Create a garden proposal, rejecting an empty `findings` list (blizzard#390 D7).
    garden_proposal_authoring: GardenProposalAuthoring
    #: The garden-proposal-closure read Protocol (blizzard#395).
    garden_proposal_closures: IReadGardenProposalClosureRepository
    #: Pass or accept a garden proposal, minting a linked hub work item by default
    #: (blizzard#395).
    garden_proposal_closure: GardenProposalClosureService
    #: A run's identity — routine, scope, and mode; read-only (``bzh:controller-read-only``).
    run_context: IReadRunContextRepository
    #: Materialize a validated delivery in one transaction (blizzard#393 Phase 3).
    garden_delivery: GardenDelivery
    #: Resolves a cited commit against the configured forge (blizzard#393 D2).
    commit_resolver: CommitResolver
    #: A routine's finding inflow-against-outflow over a window (blizzard#394 Phase 4).
    garden_trend: GardenTrendService
    #: A routine's per-scope last-swept table and windowed measurement series.
    garden_sweeps: GardenSweepsService
    #: A routine's runs are readable — the run list and one run's own delta.
    garden_run: GardenRunService


def build_services(
    engine: Engine,
    *,
    events: EventBroker,
    work_sources: IWorkSourceRegistry,
    claim_lock: threading.Lock,
    work_item_store: WorkItemStore,
    delete: DeleteService,
    finding_store: FindingStore,
    finding_exit: FindingExitService,
    clock: IClock | None = None,
    users: IWriteUserRepository | None = None,
    base_branch: str = "main",
    hub_command_runner: IHubCommandRunner | None = None,
    hub_workdir: IHubWorkdir | None = None,
    hub_workdir_root: Path | None = None,
    hub_marker_callback_base_url: str = "",
    forge_url: str | None = None,
    forge_token: str | None = None,
    forge_owner: str | None = None,
    oauth_providers: Sequence[OAuthProviderConfig] = (),
    oauth_http_client: httpx.Client | None = None,
    oauth_registry: IOAuthProviderRegistry | None = None,
    signing_keys_dir: Path | None = None,
    trusted_proxies: TrustedProxies | None = None,
    transcript_caps: TranscriptCaps | None = None,
    system_artifacts: PackagedSystemArtifacts | None = None,
) -> HubServices:
    """Construct and wire every fleet service over a migrated store engine.
    ``hub_command_runner``/``hub_workdir`` are the hub command node's mechanism seams
    (#65), left ``None`` for real adapters; an explicit ``oauth_registry`` wins over
    ``oauth_providers``. ``claim_lock``/``work_item_store``/``delete``/``finding_store``/
    ``finding_exit`` are required, not built here, so the built-in hub binding shares the
    same five (issue #364, blizzard#394)."""
    clock = clock or SystemClock()
    # The hub-store seam (issue #413) — one collaborator shared by every
    # ``hub/store/internal/`` adapter, replacing the bare engine (D2).
    store_connections = HubStoreConnections(engine, HubStoreErrorFactory(get_logger("blizzard.hub.store")))
    # The chunk-seam adapters, in the one place their construction order is expressed.
    chunk_stores = build_chunk_stores(store_connections, clock)
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
    graph_store = GraphStore(store_connections)
    registry_store = RunnerRegistryStore(store_connections)
    transcript_store = TranscriptSegmentStore(store_connections)
    event_store = TranscriptEventStore(store_connections)
    event_derivation_service = EventDerivationService(
        events=event_store, facts=chunk_facts, record=chunk_record, clock=clock
    )
    event_derivation = EventDerivationReconciler(service=event_derivation_service, events=event_store)
    analytics_event_queries = AnalyticsEventQueryStore(store_connections)
    operational_analytics = AnalyticsOperationalStore(store_connections)
    marker_authority = MarkerAuthority()
    hub_node = HubNodeExecutor(
        facts=chunk_facts,
        artifacts=chunk_artifacts,
        delivery=chunk_delivery,
        hub_exec=chunk_hub_exec,
        escalations=chunk_escalations,
        events=chunk_events,
        runner=hub_command_runner or SubprocessHubCommandRunner(),
        workdir=hub_workdir
        or FilesystemHubWorkdir(hub_workdir_root or Path(tempfile.gettempdir()) / "blizzard-hub-workdirs"),
        clock=clock,
        marker_authority=marker_authority,
        base_branch=base_branch,
        marker_callback_base_url=hub_marker_callback_base_url,
        forge_url=forge_url,
        forge_token=forge_token,
        forge_owner=forge_owner,
        work_sources=work_sources,
    )
    # One fleet service, shared: the API's pause routes and the fact ingest both land
    # registry facts, and two instances would be two of the same thing (issue #43).
    fleet = FleetService(registry=registry_store, clock=clock)
    enrollment = RunnerEnrollmentService(registry=registry_store, clock=clock)
    # The identity spine (issue #91) — one error factory shared by the SQLAlchemy
    # adapters, so the same instances back both the Write Protocols and the reads.
    auth_errors = RepoErrorFactory(get_logger("blizzard.hub.auth"))
    user_store = users or UserRepository(engine, auth_errors)
    identity_store = IdentityRepository(engine, auth_errors)
    session_store = SessionRepository(engine, auth_errors)
    auth_state_store: IWriteAuthStateRepository = AuthStateRepository(engine, auth_errors)
    superuser_bootstrap_store = SuperuserBootstrapRepository(engine)
    # Built ahead of `auth` below (issue #94), which records role-change facts through
    # this service rather than a raw write repository.
    auth_facts_service = AuthFactsService(facts=AuthFactsRepository(engine), clock=clock)
    auth = AuthService(
        users=user_store,
        identities=identity_store,
        sessions=session_store,
        auth_state=auth_state_store,
        clock=clock,
        superuser_bootstrap=superuser_bootstrap_store,
        auth_facts=auth_facts_service,
    )
    # The provider-login seam (issue #92) — one registry entry per configured
    # ``[[auth.oauth.provider]]``, empty when no providers are configured.
    oauth_registry = oauth_registry or ProviderEntry.registry(oauth_providers, http_client=oauth_http_client)
    # The hub's IdP signing-key lifecycle (issue #95) — constructed only when a keys
    # directory is passed; `None` otherwise.
    signing = SigningKeyService(signing_keys_dir) if signing_keys_dir is not None else None
    auth_throttle = IpThrottle(clock=clock)
    materialization_edits = WorkItemEditService(
        items=work_item_store,
        work_refs=chunk_work_refs,
        record=chunk_record,
        facts=chunk_facts,
        clock=clock,
        delete=delete,
    )
    graph_mint = GraphMintService(graphs=graph_store, clock=clock)
    scope_store = ScopeStore(store_connections)
    scope_registry = ScopeRegistry(scopes=scope_store, clock=clock)
    routine_store = RoutineStore(store_connections)
    finding_set_store = FindingSetStore(store_connections)
    garden_proposal_store = GardenProposalStore(store_connections)
    garden_proposal_closure_store = GardenProposalClosureStore(store_connections)
    run_context_store = RunContextStore(store_connections)
    garden_delivery_store = GardenDeliveryStore(store_connections)
    garden_trend_store = GardenTrendStore(store_connections)
    garden_sweeps_store = GardenSweepsStore(store_connections)
    garden_run_store = GardenRunStore(store_connections)
    # Bound as `.resolve` (a plain `garden_delivery.CommitResolver` callable), not the bare
    # instance, so `HubServices.commit_resolver` carries no dependency on the concrete class.
    commit_resolver = GitHubCommitResolver(
        httpx.Client(timeout=10.0), forge_url=forge_url, forge_token=forge_token, forge_owner=forge_owner
    ).resolve
    return HubServices(
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
        ingest=IngestService(record=chunk_record, work_refs=chunk_work_refs, clock=clock),
        promote=PromoteService(facts=chunk_facts, record=chunk_record, queue=chunk_queue, clock=clock),
        claim=ClaimService(
            route=chunk_route,
            record=chunk_record,
            facts=chunk_facts,
            artifacts=chunk_artifacts,
            dependencies=chunk_dependencies,
            graphs=graph_store,
            registry=registry_store,
            clock=clock,
            claim_lock=claim_lock,
        ),
        apply=ApplyService(
            facts=chunk_facts,
            movement=chunk_movement,
            decisions=chunk_decisions,
            escalations=chunk_escalations,
            route=chunk_route,
            artifacts=chunk_artifacts,
            clock=clock,
            hub_node_executor=hub_node,
        ),
        decisions=DecisionService(facts=chunk_facts, route=chunk_route, decisions=chunk_decisions, clock=clock),
        requeue=RequeueService(facts=chunk_facts, movement=chunk_movement, route=chunk_route, clock=clock),
        restart=RestartService(
            facts=chunk_facts, movement=chunk_movement, graphs=graph_store, clock=clock, claim_lock=claim_lock
        ),
        detach=DetachService(route=chunk_route, clock=clock),
        pause=PauseService(facts=chunk_facts, lifecycle=chunk_lifecycle, clock=clock),
        stop=StopService(facts=chunk_facts, lifecycle=chunk_lifecycle, clock=clock),
        complete=CompleteService(facts=chunk_facts, lifecycle=chunk_lifecycle, clock=clock),
        edit=EditService(facts=chunk_facts, record=chunk_record, graphs=graph_store, claim_lock=claim_lock),
        dependencies=DependencyService(
            facts=chunk_facts,
            lifecycle=chunk_lifecycle,
            dependencies=chunk_dependencies,
            clock=clock,
            claim_lock=claim_lock,
        ),
        delete=delete,
        facts=FactIngestService(
            facts=chunk_facts,
            route=chunk_route,
            escalations=chunk_escalations,
            questions=chunk_questions,
            usage=chunk_usage,
            events=chunk_events,
            fleet=fleet,
            clock=clock,
        ),
        transcript_ingest=TranscriptIngestService(store=transcript_store, clock=clock, caps=transcript_caps),
        graph_mint=graph_mint,
        graph_lifecycle=GraphLifecycleService(graphs=graph_store, clock=clock),
        runner_facts=RunnerFactsService(route=chunk_route, escalations=chunk_escalations, clock=clock),
        questions=QuestionService(questions=chunk_questions, clock=clock),
        queue=QueueService(queue=chunk_queue, record=chunk_record, clock=clock),
        group=GroupService(
            work_refs=chunk_work_refs,
            dependencies=chunk_dependencies,
            record=chunk_record,
            facts=chunk_facts,
            clock=clock,
            claim_lock=claim_lock,
        ),
        fleet=fleet,
        enrollment=enrollment,
        registry=registry_store,
        hub_node=hub_node,
        marker_authority=marker_authority,
        events=events,
        clock=clock,
        default_graph_doc=PACKAGED.default.doc,
        default_graph_yaml=PACKAGED.default.text,
        system_artifacts=system_artifacts or SYSTEM_ARTIFACTS_PACKAGED,
        work_sources=work_sources,
        close_drain=CloseIntentDrainer(
            delivery=chunk_delivery, events=chunk_events, work_sources=work_sources, clock=clock
        ),
        work_item_materialization=WorkItemMaterializationReconciler(
            delivery=chunk_delivery,
            items=work_item_store,
            edits=materialization_edits,
            work_sources=work_sources,
            graph_mint=graph_mint,
            default_graph_doc=PACKAGED.default.doc,
            default_graph_yaml=PACKAGED.default.text,
            clock=clock,
        ),
        sessions=session_store,
        identities=identity_store,
        users=user_store,
        auth=auth,
        oauth_providers=oauth_registry,
        auth_throttle=auth_throttle,
        auth_facts=auth_facts_service,
        signing=signing,
        trusted_proxies=trusted_proxies if trusted_proxies is not None else TrustedProxies(),
        transcripts=transcript_store,
        event_derivation=event_derivation,
        event_derivation_service=event_derivation_service,
        analytics_events=analytics_event_queries,
        operational_analytics=operational_analytics,
        scopes=scope_store,
        scope_registry=scope_registry,
        scope_lifecycle=ScopeLifecycle(scopes=scope_store, clock=clock),
        routines=routine_store,
        routine_authoring=RoutineAuthoring(
            routines=routine_store, graphs=graph_store, scope_registry=scope_registry, clock=clock
        ),
        routine_run=RunService(
            scopes=scope_store,
            scope_registry=scope_registry,
            graphs=graph_store,
            finding_sets=finding_set_store,
            items=work_item_store,
            work_refs=chunk_work_refs,
            record=chunk_record,
            queue=chunk_queue,
            clock=clock,
        ),
        routine_baselines=RoutineBaselineService(finding_sets=finding_set_store, delivery=chunk_delivery),
        findings=finding_store,
        finding_exit=finding_exit,
        finding_sets=finding_set_store,
        garden_proposals=garden_proposal_store,
        garden_proposal_authoring=GardenProposalAuthoring(proposals=garden_proposal_store, clock=clock),
        garden_proposal_closures=garden_proposal_closure_store,
        garden_proposal_closure=GardenProposalClosureService(
            closures=garden_proposal_closure_store, items=materialization_edits, clock=clock
        ),
        run_context=run_context_store,
        garden_delivery=GardenDelivery(delivery=garden_delivery_store, clock=clock),
        commit_resolver=commit_resolver,
        garden_trend=GardenTrendService(repo=garden_trend_store),
        garden_sweeps=GardenSweepsService(repo=garden_sweeps_store, scopes=scope_store),
        garden_run=GardenRunService(repo=garden_run_store, chunk_records=chunk_record, chunk_facts=chunk_facts),
    )
