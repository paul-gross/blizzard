"""The hub's fleet-service composition (``bzh:dependency-injection``).

One place the store-backed collaborators are constructed and injected: the chunk and
graph stores, the four domain services (ingest, claim, apply, graph-mint) and the
generic hub command node executor, the PM read seam, and the event broker. :func:`build_services`
is called by the ``host`` composition root (:func:`blizzard.hub.app.build_hosted_app`)
and by tests, which swap the PM seam for fakes by type. The store-free export/unit app
builds no services — the fleet routes report the store is unwired rather than serving
on a missing database.

Controllers read the stores through their **read** Protocols and mutate only through
the services (``bzh:controller-read-only``); both variants are the one
:class:`~blizzard.hub.store.internal.chunk_store.ChunkStore` instance, so a request
sees a consistent view.
"""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy import Engine

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.hub.auth.auth_state import IWriteAuthStateRepository
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.facts import AuthFactsService
from blizzard.hub.auth.identities import IReadIdentityRepository
from blizzard.hub.auth.internal.auth_facts_repository import AuthFactsRepository
from blizzard.hub.auth.internal.auth_state_repository import AuthStateRepository
from blizzard.hub.auth.internal.identity_repository import IdentityRepository
from blizzard.hub.auth.internal.session_repository import SessionRepository
from blizzard.hub.auth.internal.user_repository import UserRepository
from blizzard.hub.auth.oauth.internal.factory import build_oauth_registry
from blizzard.hub.auth.oauth.registry import IOAuthProviderRegistry
from blizzard.hub.auth.service import AuthService
from blizzard.hub.auth.sessions import IReadSessionRepository
from blizzard.hub.auth.throttle import IpThrottle
from blizzard.hub.config import OAuthProviderConfig
from blizzard.hub.delivery.command_runner import IHubCommandRunner
from blizzard.hub.delivery.hub_node import HubNodeExecutor
from blizzard.hub.delivery.internal.hub_command_runner import SubprocessHubCommandRunner
from blizzard.hub.delivery.internal.hub_workdir import FilesystemHubWorkdir
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
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.graphs import default_graph_yaml, load_default_graph_doc
from blizzard.hub.pm.source import IPmSourceRegistry
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore


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
    #: The fleet registry's read-only Protocol, held directly by ``HubServices``
    #: (mirroring ``chunks: IReadChunkRepository``) so an edge dependency —
    #: ``require_runner_principal`` (``hub/api/auth.py``) — can resolve a presented
    #: bearer token's hash to its runner without holding a domain service
    #: (``bzh:controller-read-only``: a read-only repository is fine at the edge).
    #: The same underlying store instance as ``fleet``'s write registry.
    registry: IReadRunnerRegistry
    hub_node: HubNodeExecutor
    events: EventBroker
    clock: IClock
    default_graph_doc: GraphDoc
    default_graph_yaml: str
    pm: IPmSourceRegistry
    #: The session read repository (issue #91), held directly by ``HubServices``
    #: (mirroring ``registry: IReadRunnerRegistry``) so the human-plane edge
    #: (``hub/api/auth_session.py``'s ``resolve_identity``) can resolve a presented
    #: session id's hash without holding a domain service (``bzh:controller-read-only``).
    sessions: IReadSessionRepository
    #: The identity-link read repository (issue #92), held directly by ``HubServices``
    #: for the boot-time provider-name-immutability check (``hub/app.py``'s
    #: ``build_hosted_app``), which needs no domain service — a plain read
    #: (``bzh:controller-read-only``).
    identities: IReadIdentityRepository
    #: The identity domain service — mint/resolve/slide sessions, the first-login
    #: linking rule, ``state`` issuance (``bzh:controller-read-only``: only the domain
    #: writes).
    auth: AuthService
    #: The configured OAuth provider registry (issue #92) — empty when
    #: ``[[auth.oauth.provider]]`` carries no entries (including every pre-#92
    #: ``auth.mode = "none"`` deployment). ``hub/api/auth_login.py`` depends only on
    #: :class:`~blizzard.hub.auth.oauth.registry.IOAuthProviderRegistry`.
    oauth_providers: IOAuthProviderRegistry
    #: Per-IP token-bucket throttle (issue #92) shared by the authorize/callback routes.
    auth_throttle: IpThrottle
    #: The non-chunk auth/security fact log (issue #92) — ``login_failed``/``sso_refused``.
    auth_facts: AuthFactsService


def build_services(
    engine: Engine,
    *,
    events: EventBroker,
    pm: IPmSourceRegistry,
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
) -> HubServices:
    """Construct and wire every fleet service over a migrated store engine.

    ``base_branch`` is the branch every PR/merge targets — ``main`` for the
    verification forge's bare origins, set to a real repo's default (e.g. ``master``) at
    the ``host`` composition root from ``BZ_FORGE_BASE_BRANCH``. ``hub_command_runner``
    / ``hub_workdir`` are the generic hub command node's mechanism seams (#65) — tests
    inject fakes; the ``host`` composition root leaves them ``None`` for the real
    subprocess/filesystem adapters, rooted under ``hub_workdir_root``. ``forge_owner``
    is injected into a hub command node's env (``BZ_FORGE_OWNER``) so its own
    ``run:`` script can qualify a bare (owner-less) repo the same way its own
    ``qualify_repo`` does (e.g. ``hub/graphs/scripts/land_default.py``). ``oauth_providers``
    (issue #92) mirrors ``pm`` above: the ``host`` composition root passes
    ``config.auth.oauth_providers`` only under ``auth.mode = "oauth"`` (mirroring #95's
    "no IdP surface under none"); tests inject ``oauth_http_client`` (an
    ``httpx.MockTransport``-backed client) to drive the real conformers with no network,
    or bypass config/secret resolution entirely with an explicit ``oauth_registry`` (a
    fake :class:`~blizzard.hub.auth.oauth.provider.IOAuthProvider`-keyed registry — the
    unit/component tier's own no-network double, mirroring ``pm``'s ``dict[str,
    FakePmSource]`` injection in ``tests/support.py``), which wins over ``oauth_providers``
    when both are given.
    """
    clock = clock or SystemClock()
    chunk_store = ChunkStore(engine, clock)
    graph_store = GraphStore(engine)
    registry_store = RunnerRegistryStore(engine)
    hub_node = HubNodeExecutor(
        chunks=chunk_store,
        runner=hub_command_runner or SubprocessHubCommandRunner(),
        workdir=hub_workdir
        or FilesystemHubWorkdir(hub_workdir_root or Path(tempfile.gettempdir()) / "blizzard-hub-workdirs"),
        clock=clock,
        base_branch=base_branch,
        marker_callback_base_url=hub_marker_callback_base_url,
        forge_url=forge_url,
        forge_token=forge_token,
        forge_owner=forge_owner,
        pm=pm,
    )
    # One fleet service, shared: the API's pause routes and the fact ingest both land
    # registry facts, and two instances would be two of the same thing (issue #43).
    fleet = FleetService(registry=registry_store, clock=clock)
    enrollment = RunnerEnrollmentService(registry=registry_store, clock=clock)
    # The identity spine (issue #91) — one error factory shared by the three
    # SQLAlchemy adapters (mirrors chunk_store/graph_store/registry_store sharing one
    # engine), each satisfying its Write Protocol (a superset of its Read variant, so
    # `AuthService` below is handed the very same instances the edge reads through).
    auth_errors = RepoErrorFactory(get_logger("blizzard.hub.auth"))
    user_store = UserRepository(engine, auth_errors)
    identity_store = IdentityRepository(engine, auth_errors)
    session_store = SessionRepository(engine, auth_errors)
    auth_state_store: IWriteAuthStateRepository = AuthStateRepository(engine, auth_errors)
    auth = AuthService(
        users=user_store, identities=identity_store, sessions=session_store, auth_state=auth_state_store, clock=clock
    )
    # The provider-login seam (issue #92) — one registry entry per configured
    # ``[[auth.oauth.provider]]``, boot-fails on a misconfigured entry (unknown type,
    # missing issuer, unset secret env). Empty when no providers are configured, which
    # is every pre-#92 and every ``auth.mode = "none"`` deployment (the ``host`` root
    # passes an empty sequence in that case).
    oauth_registry = oauth_registry or build_oauth_registry(oauth_providers, http_client=oauth_http_client)
    auth_facts_service = AuthFactsService(facts=AuthFactsRepository(engine), clock=clock)
    auth_throttle = IpThrottle(clock=clock)
    # Shared between ClaimService and EditService (issue #120) — the one in-process
    # lock serializing both services' check-then-act sequences over a chunk's live-route
    # state, so a claim and a graph/model edit racing the same chunk can't interleave.
    # Constructed once here, at the composition root (``bzh:dependency-injection``),
    # rather than either service owning a private lock the other cannot see.
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
        events=events,
        clock=clock,
        default_graph_doc=load_default_graph_doc(),
        default_graph_yaml=default_graph_yaml(),
        pm=pm,
        sessions=session_store,
        identities=identity_store,
        auth=auth,
        oauth_providers=oauth_registry,
        auth_throttle=auth_throttle,
        auth_facts=auth_facts_service,
    )
