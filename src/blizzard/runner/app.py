"""Composition root — wire the runner and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed and injected. ``create_app`` does **not**
open the store, which lets the OpenAPI exporter and unit tests build the app without a
migrated database; the startup revision guard and the offline ``migrate`` verb own it."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request, params
from fastapi.responses import RedirectResponse

from blizzard import __version__
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.foundation.web import Frontend
from blizzard.runner.api.artifacts import router as artifacts_router
from blizzard.runner.api.asks import router as asks_router
from blizzard.runner.api.attachments import router as attachments_router
from blizzard.runner.api.chunk_detail import router as chunk_detail_router
from blizzard.runner.api.control import router as control_router
from blizzard.runner.api.dashboard import router as dashboard_router
from blizzard.runner.api.environments import router as environments_router
from blizzard.runner.api.escalations import router as escalations_router
from blizzard.runner.api.events import router as events_router
from blizzard.runner.api.facts import router as facts_router
from blizzard.runner.api.fleet_summary import router as fleet_summary_router
from blizzard.runner.api.git_commits import router as git_commits_router
from blizzard.runner.api.health import router as health_router
from blizzard.runner.api.heartbeat import router as heartbeat_router
from blizzard.runner.api.history import router as history_router
from blizzard.runner.api.leases import router as leases_router
from blizzard.runner.api.readiness import router as readiness_router
from blizzard.runner.api.requeues import router as requeues_router
from blizzard.runner.api.selftests import router as selftests_router
from blizzard.runner.api.session_end import router as session_end_router
from blizzard.runner.api.takeovers import router as takeovers_router
from blizzard.runner.api.transcript_segments import router as transcript_segments_router
from blizzard.runner.api.transcripts import router as transcripts_router
from blizzard.runner.api.work_items import router as work_items_router
from blizzard.runner.api.workspace_prompt import router as workspace_prompt_router
from blizzard.runner.auth.federation import (
    HubAuthModeCache,
    NeedsFederationBounce,
    require_human_api,
    require_human_session,
)
from blizzard.runner.auth.federation import router as auth_router
from blizzard.runner.auth.internal.jti_cache_repository import JtiCacheRepository
from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.auth.jwks_cache import JwksCache
from blizzard.runner.composition import build_stores
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.attachments import AttachmentService
from blizzard.runner.domain.git_commit_declaration import GitCommitDeclarationService
from blizzard.runner.domain.leases import LocalLeaseService
from blizzard.runner.domain.requeue import RequeueService
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.runner.domain.takeover import TakeoverService
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.transcript import TranscriptErrorFactory as HarnessTranscriptErrorFactory
from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.runtime import migration_runner
from blizzard.runner.selftest.internal.subprocess_scratch_git import SubprocessScratchGit
from blizzard.runner.selftest.service import SelfTestService
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.stores import RunnerStores
from blizzard.runner.transcripts.internal.http_archived_transcript_repository import (
    HttpArchivedTranscriptRepository,
)
from blizzard.runner.transcripts.internal.projected_transcript_repository import ProjectedTranscriptRepository
from blizzard.runner.transcripts.service import TranscriptService

# The one coding-harness name a selftest may target today (issue #54).
CLAUDE_CODE_HARNESS_NAME = "claude_code"


@dataclass(frozen=True)
class Lane:
    """One tenant of the three-tenant partition (issue #95) — a router set and the gate it
    mounts behind. Only the human web lane is session-gated, and that gate covers the served
    shell as well as the JSON API it reads; the other two cannot be gated, being respectively
    what *establishes* a session and what workers call over TCP where they cannot bounce."""

    gate: Sequence[params.Depends] | None
    routers: tuple[APIRouter, ...]

    def mount(self, app: FastAPI) -> None:
        for router in self.routers:
            app.include_router(router, dependencies=self.gate)


# Ungated, in mount order: liveness, the SSO bounce, then the worker hooks. `asks_router` is
# the one mixed router — its POST is ungated, its GET carries the human gate at the route.
_UNGATED = (
    health_router,
    readiness_router,
    auth_router,
    heartbeat_router,
    session_end_router,
    asks_router,
    attachments_router,
    git_commits_router,
    artifacts_router,
    history_router,
    work_items_router,
)
# The human web lane: the local panel's own reads and writes (issue #51), the runner's own
# pause brake reachable with the hub down (#43), and the pass-throughs proxied to the hub.
_HUMAN = (
    chunk_detail_router,
    leases_router,
    transcripts_router,
    transcript_segments_router,
    selftests_router,
    fleet_summary_router,
    workspace_prompt_router,
    control_router,
    environments_router,
    escalations_router,
    facts_router,
    takeovers_router,
    dashboard_router,
    requeues_router,
    events_router,
)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Set ``app.state.shutdown`` on the ASGI ``lifespan`` "shutdown" message (D3,
    blizzard#317) — sent *after* uvicorn's own graceful-drain wait, so in the hosted daemon
    ``EarlyShutdownServer.handle_exit`` (``cli.py``) is what actually frees a parked SSE
    response promptly. This hook is the only signal a wrapper-less composer gets — a plain
    ``TestClient``/``uvicorn.Server``, as the test suite uses."""
    yield
    app.state.shutdown.set()


def create_app(
    config: RunnerConfig,
    *,
    readiness: ReadinessService | None = None,
    workspace_provider: IWorkspaceProvider | None = None,
    harness: IHarnessAdapter | None = None,
    runner_stores: RunnerStores | None = None,
    leases: LocalLeaseService | None = None,
    transcripts: TranscriptService | None = None,
    runner_status: RunnerStatusService | None = None,
    takeover: TakeoverService | None = None,
    requeue: RequeueService | None = None,
    selftests: SelfTestService | None = None,
    attachments: AttachmentService | None = None,
    git_commit_declarations: GitCommitDeclarationService | None = None,
    hub_http_client: httpx.Client | None = None,
    jti_cache: IJtiCache | None = None,
    events: EventBroker | None = None,
) -> FastAPI:
    """Build a fully wired runner app from resolved config.

    Every store-backed seam is optional, so a store-free build is possible; those routes
    then answer 503 and ``/api/ready`` reports ``ready=false``. ``selftests`` is always
    wired (issue #54); ``events`` (D2) defaults absent, leaving the route silent."""
    log = get_logger("blizzard.runner")

    app = FastAPI(title="blizzard-runner", version=__version__, lifespan=_lifespan)
    app.state.config = config
    app.state.readiness = readiness
    # The seams below are None on the store-free app.
    app.state.workspace_provider = workspace_provider
    app.state.harness = harness
    app.state.runner_stores = runner_stores
    # The SSE broker (D2) — `None` on every composer with no stream to feed, where
    # :class:`~blizzard.foundation.events.stream.Stream` degrades cleanly.
    app.state.events = events
    # Set on shutdown by `_lifespan` (D3); the stream route's live wait races it.
    app.state.shutdown = asyncio.Event()
    # Unconditional: a stateless wrapper over the wall clock (``bzh:injected-clock``),
    # needed whether or not a store is wired.
    app.state.clock = SystemClock()
    app.state.leases = leases
    app.state.transcripts = transcripts
    app.state.runner_status = runner_status
    app.state.takeover = takeover
    app.state.requeue = requeue
    app.state.attachments = attachments
    app.state.git_commit_declarations = git_commit_declarations
    # The adapter-drift canary (issue #54): store-free, so wired unconditionally.
    app.state.selftests = selftests or SelfTestService(
        adapters={CLAUDE_CODE_HARNESS_NAME: harness} if harness is not None else {},
        scratch_git=SubprocessScratchGit(),
        process=LinuxProcessProbe(),
        clock=SystemClock(),
    )
    # This default must **not** reach the network (issue #95) — pinned by
    # tests/test_pin_runner_misc.py::test_the_default_hub_client_never_reaches_the_configured_hub_url
    hub_http_client = hub_http_client or httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
        # A fixed placeholder: `config.hub_url` may be empty or not a valid absolute
        # base, and every request here is answered locally by the transport above.
        base_url="http://runner-hub-client-hermetic-default.invalid",
    )
    app.state.hub_auth_mode = HubAuthModeCache(hub_http_client)
    app.state.jwks_cache = JwksCache(hub_http_client, "/api/auth/jwks.json")
    app.state.jti_cache = jti_cache
    # The reverse-proxy trust set (issue #130), empty by default — so
    # `X-Forwarded-Proto` is ignored from every peer.
    app.state.trusted_proxies = TrustedProxies.parse(config.trusted_proxies)
    # Minted fresh at every daemon start, so a restart invalidates every live session
    # — an accepted tradeoff, see `runner/auth/session.py`.
    app.state.session_secret = secrets.token_bytes(32)

    @app.exception_handler(NeedsFederationBounce)
    def _bounce_to_login(_: Request, exc: NeedsFederationBounce) -> RedirectResponse:
        return RedirectResponse(f"/api/auth/login?return_to={quote(exc.return_to, safe='')}")

    # The served shell's half of the human web lane's gate — see :class:`Lane`.
    @app.middleware("http")
    async def _gate_web_surface(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not request.url.path.startswith("/api"):
            try:
                require_human_session(request)
            except NeedsFederationBounce as exc:
                return RedirectResponse(f"/api/auth/login?return_to={quote(exc.return_to, safe='')}")
        return await call_next(request)

    # API routers first, so /api/* always wins over the web mount at /.
    Lane(None, _UNGATED).mount(app)
    Lane([Depends(require_human_api)], _HUMAN).mount(app)

    # The runner-served web app: the human web lane the middleware above gates
    # (issue #95) — the only browser-facing surface this daemon serves.
    Frontend.embedded("runner", app_name="blizzard-runner").mount(app)

    log.info("runner app created", db_url=config.db_url, readiness_wired=readiness is not None)
    return app


def build_hosted_app(config: RunnerConfig, *, events: EventBroker | None = None) -> FastAPI:
    """The ``host`` composition root: open the store and wire the readiness seam.

    Engine creation is connection-free, so this stays cheap; the connection is opened
    lazily on the first ``/api/ready`` read. ``events`` (D2) is the process-wide broker
    ``host`` shares with the loop's ``PeriodicDriver``; absent for every other caller."""
    engine = create_engine_from_url(config.db_url)
    reader = SqlAlchemyStoreStatusReader(engine)
    expected = migration_runner(config).script_head()
    readiness = ReadinessService(reader=reader, expected_revision=expected)
    runner_stores = build_stores(engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
    workspace_provider: IWorkspaceProvider = WinterWorkspaceProvider(
        workspace_root=config.workspace_root or str(config.root),
        env_pool=config.workspace_envs,
        base_branch=config.base_branch,
    )
    # Empty ``transcripts_root`` resolves here, once, never inside the adapter.
    projects_root = config.transcripts_root or str(Path.home() / ".claude" / "projects")
    harness_transcript_source = ClaudeCodeTranscriptSource(
        projects_root, HarnessTranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript"))
    )
    harness: IHarnessAdapter = ClaudeCodeAdapter(
        binary=config.harness_binary,
        settings_path=config.worker_settings_path,
        permission_mode=config.harness_permission_mode,
        model_aliases=config.model_aliases,
        effort_aliases=config.effort_aliases,
        transcript_source=harness_transcript_source,
    )
    # ``stale_after`` is left at its default so the two readers never desync (#28).
    leases = LocalLeaseService(stores=runner_stores, clock=SystemClock(), process=LinuxProcessProbe())
    # Projected off the harness's own source, via the accessor — never built twice.
    transcript_repository = ProjectedTranscriptRepository(harness.transcript_source())
    # The archived-transcript seam (blizzard#249, D4) needs its own authenticated client:
    # `hub_http_client` below carries no auth headers (JWKS/hub-auth-mode reads only).
    archived_transcript_client = httpx.Client(base_url=config.hub_url, timeout=15.0, headers=config.auth_headers())
    archived_transcripts = HttpArchivedTranscriptRepository(archived_transcript_client)
    transcripts = TranscriptService(
        leases=runner_stores.leases,
        transcript_ledger=runner_stores.transcript_ledger,
        environments=runner_stores.environments,
        transcripts=transcript_repository,
        archived=archived_transcripts,
        workspace_root=config.workspace_root,
    )
    # The clock/probe instances below are per-service: both are stateless, so a second
    # instance is equivalent to sharing one.
    runner_status = RunnerStatusService(
        stores=runner_stores,
        clock=SystemClock(),
        harness=harness,
        runner_id=config.runner_id,
        workspace_id=config.workspace_id,
        max_agents=config.max_agents,
        hub_url=config.hub_url,
        env_pool=config.workspace_envs,
    )
    takeover = TakeoverService(
        runner_stores,
        SystemClock(),
        harness,
        LinuxProcessProbe(),
        # The same derivation the spawn preamble uses, so the two agree.
        local_api_url=config.local_api_url,
        events=events,
    )
    requeue = RequeueService(
        runner_stores.requeue, SystemClock(), takeover=runner_stores.takeover, escalations=runner_stores.escalations
    )
    attachments = AttachmentService(runner_stores.attachments, SystemClock(), tokens=runner_stores.tokens)
    # Takes the workspace provider too: a declaration is checked against the
    # environment's repo manifest, which is the provider's to declare (issue #143).
    git_commit_declarations = GitCommitDeclarationService(
        runner_stores.git_commit_declarations,
        SystemClock(),
        workspace_provider,
        tokens=runner_stores.tokens,
        environments=runner_stores.environments,
    )
    jti_cache = JtiCacheRepository(engine, SystemClock())
    # The real, network-reaching hub client — only `host` wires one (issue #95).
    hub_http_client = httpx.Client(base_url=config.hub_url, timeout=5.0)
    return create_app(
        config,
        readiness=readiness,
        workspace_provider=workspace_provider,
        harness=harness,
        runner_stores=runner_stores,
        leases=leases,
        transcripts=transcripts,
        runner_status=runner_status,
        takeover=takeover,
        requeue=requeue,
        attachments=attachments,
        git_commit_declarations=git_commit_declarations,
        jti_cache=jti_cache,
        hub_http_client=hub_http_client,
        events=events,
    )


def create_app_for_export() -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs)."""
    return create_app(RunnerConfig(root=Path("."), db_url="sqlite://"))
