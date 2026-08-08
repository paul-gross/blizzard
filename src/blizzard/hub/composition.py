"""The hub's fleet-service composition (``bzh:dependency-injection``).

One place the store-backed collaborators are constructed and injected. Controllers read
the stores through their **read** Protocols and mutate only through the services
(``bzh:controller-read-only``); both variants are the one
:class:`~blizzard.hub.store.internal.chunk_store.ChunkStore` instance."""

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
from blizzard.hub.auth.oauth.internal.factory import build_oauth_registry
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
from blizzard.hub.domain.apply import ApplyService
from blizzard.hub.domain.claim import ClaimService
from blizzard.hub.domain.decisions import DecisionService, RequeueService
from blizzard.hub.domain.detach import DetachService
from blizzard.hub.domain.edit import EditService
from blizzard.hub.domain.enrollment import RunnerEnrollmentService
from blizzard.hub.domain.facts import FactIngestService, RunnerFactsService
from blizzard.hub.domain.graph import GraphDoc, IReadGraphRepository
from blizzard.hub.domain.graph_authoring import GraphMintService
from blizzard.hub.domain.graph_lifecycle import GraphLifecycleService
from blizzard.hub.domain.ingest import IngestService
from blizzard.hub.domain.pause import PauseService
from blizzard.hub.domain.promote import PromoteService
from blizzard.hub.domain.questions import QuestionService
from blizzard.hub.domain.queue import GroupService, QueueService
from blizzard.hub.domain.registry import FleetService, IReadRunnerRegistry
from blizzard.hub.domain.stop import StopService
from blizzard.hub.domain.work import IReadChunkRepository
from blizzard.hub.domain.work_closure import DeliveryClosureReconciler
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.work_sources.source import IWorkSourceRegistry


@dataclass(frozen=True)
class HubServices:
    """The wired fleet collaborators, stashed on ``app.state.services``."""

    chunks: IReadChunkRepository
    graphs: IReadGraphRepository
    ingest: IngestService
    promote: PromoteService
    claim: ClaimService
    apply: ApplyService
    decisions: DecisionService
    requeue: RequeueService
    detach: DetachService
    pause: PauseService
    stop: StopService
    edit: EditService
    facts: FactIngestService
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
    work_sources: IWorkSourceRegistry
    #: The delivery closure reconciler (issue #216) — built here because it needs the
    #: write-capable chunk repository, which only the composition root holds.
    delivery_closure: DeliveryClosureReconciler
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


def build_services(
    engine: Engine,
    *,
    events: EventBroker,
    work_sources: IWorkSourceRegistry,
    clock: IClock | None = None,
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
) -> HubServices:
    """Construct and wire every fleet service over a migrated store engine.

    ``hub_command_runner`` / ``hub_workdir`` are the hub command node's mechanism seams
    (#65), left ``None`` for the real subprocess/filesystem adapters. An explicit
    ``oauth_registry`` wins over ``oauth_providers`` when both are given."""
    clock = clock or SystemClock()
    chunk_store = ChunkStore(engine, clock)
    graph_store = GraphStore(engine)
    registry_store = RunnerRegistryStore(engine)
    marker_authority = MarkerAuthority()
    hub_node = HubNodeExecutor(
        chunks=chunk_store,
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
    user_store = UserRepository(engine, auth_errors)
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
    oauth_registry = oauth_registry or build_oauth_registry(oauth_providers, http_client=oauth_http_client)
    # The hub's IdP signing-key lifecycle (issue #95) — constructed only when a keys
    # directory is passed; `None` otherwise.
    signing = SigningKeyService(signing_keys_dir) if signing_keys_dir is not None else None
    auth_throttle = IpThrottle(clock=clock)
    # Shared between ClaimService and EditService (issue #120) — one in-process lock, so a
    # claim and an edit racing the same chunk can't interleave (tests/test_edit_claim_race.py).
    claim_lock = threading.Lock()
    return HubServices(
        chunks=chunk_store,
        graphs=graph_store,
        ingest=IngestService(chunks=chunk_store, clock=clock),
        promote=PromoteService(chunks=chunk_store, clock=clock),
        claim=ClaimService(
            chunks=chunk_store, graphs=graph_store, registry=registry_store, clock=clock, claim_lock=claim_lock
        ),
        apply=ApplyService(chunks=chunk_store, clock=clock, hub_node_executor=hub_node),
        decisions=DecisionService(chunks=chunk_store, clock=clock),
        requeue=RequeueService(chunks=chunk_store, clock=clock),
        detach=DetachService(chunks=chunk_store, clock=clock),
        pause=PauseService(chunks=chunk_store, clock=clock),
        stop=StopService(chunks=chunk_store, clock=clock),
        edit=EditService(chunks=chunk_store, graphs=graph_store, claim_lock=claim_lock),
        facts=FactIngestService(chunks=chunk_store, fleet=fleet, clock=clock),
        graph_mint=GraphMintService(graphs=graph_store, clock=clock),
        graph_lifecycle=GraphLifecycleService(graphs=graph_store, clock=clock),
        runner_facts=RunnerFactsService(chunks=chunk_store, clock=clock),
        questions=QuestionService(chunks=chunk_store, clock=clock),
        queue=QueueService(chunks=chunk_store, clock=clock),
        group=GroupService(chunks=chunk_store, clock=clock),
        fleet=fleet,
        enrollment=enrollment,
        registry=registry_store,
        hub_node=hub_node,
        marker_authority=marker_authority,
        events=events,
        clock=clock,
        default_graph_doc=PACKAGED.default.doc,
        default_graph_yaml=PACKAGED.default.text,
        work_sources=work_sources,
        delivery_closure=DeliveryClosureReconciler(chunks=chunk_store, work_sources=work_sources, clock=clock),
        sessions=session_store,
        identities=identity_store,
        users=user_store,
        auth=auth,
        oauth_providers=oauth_registry,
        auth_throttle=auth_throttle,
        auth_facts=auth_facts_service,
        signing=signing,
        trusted_proxies=trusted_proxies if trusted_proxies is not None else TrustedProxies(),
    )
