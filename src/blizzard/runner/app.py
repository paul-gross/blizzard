"""Composition root — wire the runner and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed and injected. ``create_app`` builds
the app from resolved config and does **not** open the store — the startup
revision guard (``blizzard runner host``) and the offline ``migrate`` verb own
that. Keeping ``create_app`` store-free lets the OpenAPI exporter and unit tests
build the app without a migrated database.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse

from blizzard import __version__
from blizzard.foundation.assets import frontend_dir
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.web import mount_web_app
from blizzard.runner.api.artifacts import router as artifacts_router
from blizzard.runner.api.asks import router as asks_router
from blizzard.runner.api.attachments import router as attachments_router
from blizzard.runner.api.chunk_detail import router as chunk_detail_router
from blizzard.runner.api.control import router as control_router
from blizzard.runner.api.environments import router as environments_router
from blizzard.runner.api.escalations import router as escalations_router
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
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.attachments import AttachmentService
from blizzard.runner.domain.git_commit_declaration import GitCommitDeclarationService
from blizzard.runner.domain.leases import LocalLeaseService
from blizzard.runner.domain.readiness import ReadinessService
from blizzard.runner.domain.requeue import RequeueService
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.runner.domain.takeover import TakeoverService
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_transcript import ClaudeCodeTranscriptSource
from blizzard.runner.harness.transcript import TranscriptErrorFactory as HarnessTranscriptErrorFactory
from blizzard.runner.loop.process import LinuxProcessProbe
from blizzard.runner.runtime import migration_runner
from blizzard.runner.selftest.internal.subprocess_scratch_git import SubprocessScratchGit
from blizzard.runner.selftest.service import SelfTestService
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import IWriteRunnerStore
from blizzard.runner.transcripts.internal.projected_transcript_repository import ProjectedTranscriptRepository
from blizzard.runner.transcripts.service import LocalTranscriptService

# The one coding-harness name a selftest may target today (issue #54) — OpenCode and
# Codex adapters are out of scope, so the registry `create_app` builds carries at
# most this single entry, bound to whatever `harness` the app was actually built with.
CLAUDE_CODE_HARNESS_NAME = "claude_code"


def create_app(
    config: RunnerConfig,
    *,
    readiness: ReadinessService | None = None,
    workspace_provider: IWorkspaceProvider | None = None,
    harness: IHarnessAdapter | None = None,
    runner_store: IWriteRunnerStore | None = None,
    leases: LocalLeaseService | None = None,
    transcripts: LocalTranscriptService | None = None,
    runner_status: RunnerStatusService | None = None,
    takeover: TakeoverService | None = None,
    requeue: RequeueService | None = None,
    selftests: SelfTestService | None = None,
    attachments: AttachmentService | None = None,
    git_commit_declarations: GitCommitDeclarationService | None = None,
    hub_http_client: httpx.Client | None = None,
    jti_cache: IJtiCache | None = None,
) -> FastAPI:
    """Build a fully wired runner app from resolved config.

    Every store-backed seam — ``readiness`` (the readiness evaluator), ``leases``
    (issue #28), ``transcripts`` (issue #29), ``runner_status`` (issue #51),
    ``takeover`` (issue #52), ``requeue`` (issue #53), ``attachments`` (issue #113),
    ``git_commit_declarations`` (issue #143) — is optional, so the store-free paths
    (the OpenAPI export and unit tests) build the app without opening a database. Their
    routes then answer 503, and ``/api/ready`` reports ``ready=false``, rather than
    pretending. :func:`build_hosted_app` is what wires them for real.

    ``selftests`` (issue #54) needs no store, so it is always wired here — its harness
    registry carries only ``harness`` under :data:`CLAUDE_CODE_HARNESS_NAME` when one was
    passed, empty otherwise, so the store-free app still answers both routes (naming no
    configured harnesses on ``POST`` rather than 503ing).
    """
    log = get_logger("blizzard.runner")

    app = FastAPI(title="blizzard-runner", version=__version__)
    app.state.config = config
    app.state.readiness = readiness
    # The seams below are None on the store-free app; the reconciliation loop and the API
    # routes read them off app.state.
    app.state.workspace_provider = workspace_provider
    app.state.harness = harness
    app.state.runner_store = runner_store
    # Unconditional (unlike the store-backed seams): a stateless wrapper over the wall
    # clock (``bzh:injected-clock``), and issue #95's session-gating dependency needs it
    # regardless of whether a store is wired.
    app.state.clock = SystemClock()
    app.state.leases = leases
    app.state.transcripts = transcripts
    app.state.runner_status = runner_status
    app.state.takeover = takeover
    app.state.requeue = requeue
    app.state.attachments = attachments
    app.state.git_commit_declarations = git_commit_declarations
    # The adapter-drift canary (issue #54): a store-free in-memory job service, wired
    # unconditionally so `POST`/`GET /api/selftests` answer even on the store-free app.
    app.state.selftests = selftests or SelfTestService(
        adapters={CLAUDE_CODE_HARNESS_NAME: harness} if harness is not None else {},
        scratch_git=SubprocessScratchGit(),
        process=LinuxProcessProbe(),
        clock=SystemClock(),
    )
    # The SSO federation seam (issue #95). `create_app`'s own default must **not** reach
    # the real network at `config.hub_url`: a coincidental live listener there (this exact
    # daemon, dogfooded) would flip the human lane's gating on outside the `host`
    # composition root's control. So it is a transport-level double that always answers
    # 404, which `HubAuthModeCache.enabled()` reads as "no IdP surface". Only
    # :func:`build_hosted_app` wires a client that actually reaches the configured hub.
    # Pinned by
    # tests/test_pin_runner_misc.py::test_the_default_hub_client_never_reaches_the_configured_hub_url.
    hub_http_client = hub_http_client or httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(404)),
        # A fixed placeholder, deliberately **not** `config.hub_url` — that may be
        # empty (an unenrolled runner, `hub_url=""`) or otherwise not a valid absolute
        # base for httpx's own URL machinery, and the exact value is irrelevant here
        # since every request against it is answered locally by the transport above.
        base_url="http://runner-hub-client-hermetic-default.invalid",
    )
    app.state.hub_auth_mode = HubAuthModeCache(hub_http_client)
    app.state.jwks_cache = JwksCache(hub_http_client, "/api/auth/jwks.json")
    app.state.jti_cache = jti_cache
    # The reverse-proxy trust set (issue #130) — parsed once here from
    # `config.trusted_proxies`; the SSO callback consults it to resolve the effective
    # cookie scheme. Empty by default, so `X-Forwarded-Proto` is ignored from every peer.
    app.state.trusted_proxies = TrustedProxies.parse(config.trusted_proxies)
    # A per-process secret signing the runner's own session cookie (`runner/auth/
    # session.py`) — minted fresh at every daemon start, so a restart invalidates
    # every live session (see that module's docstring for why this is an accepted
    # tradeoff, not a gap).
    app.state.session_secret = secrets.token_bytes(32)

    @app.exception_handler(NeedsFederationBounce)
    def _bounce_to_login(_: Request, exc: NeedsFederationBounce) -> RedirectResponse:
        return RedirectResponse(f"/api/auth/login?return_to={quote(exc.return_to, safe='')}")

    # The three-tenant partition (issue #95). The runner's API seam is split into three
    # lanes, and only the **human web lane** is session-gated when the hub runs an IdP
    # surface (`auth.mode = "oauth"`):
    #   - **worker-hook lane** (ungated) — workers cannot SSO-bounce, so they keep their
    #     existing lanes (lease-token auth where present).
    #   - **CLI unix-socket lane** (ungated) — the socket file's filesystem permissions
    #     are its access control, so any socket peer gets the implicit identity.
    #   - **human web lane** (gated) — the served web app *and the JSON API it reads*:
    #     the static mount at `/` by the middleware below (a 302 bounce), the panel's own
    #     `/api/*` routes by `Depends(require_human_api)` at their includes (a 401).
    #     Gating the shell alone would leave the JSON API it renders open over TCP.
    # Under `auth.mode = "none"` every lane resolves to the implicit identity.
    @app.middleware("http")
    async def _gate_web_surface(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not request.url.path.startswith("/api"):
            try:
                require_human_session(request)
            except NeedsFederationBounce as exc:
                return RedirectResponse(f"/api/auth/login?return_to={quote(exc.return_to, safe='')}")
        return await call_next(request)

    # The human-web-lane gate the panel's own reads/writes carry (issue #95). Declared
    # once here and attached at each human router's `include_router` below.
    human_api = [Depends(require_human_api)]

    # API routers first, so /api/* always wins over the web mount at /.
    app.include_router(health_router)
    app.include_router(readiness_router)
    # The SSO federation bounce (issue #95) — public: it is what *establishes* a
    # session, so it cannot itself be session-gated.
    app.include_router(auth_router)
    # Worker-hook lane (ungated): workers call these over TCP and cannot SSO-bounce.
    app.include_router(heartbeat_router)
    app.include_router(session_end_router)
    # `asks_router` is the one mixed router: its POST `/leases/{id}/asks` is the
    # worker-hook record (ungated), while its GET `/asks` is a human-lane panel/status
    # read that carries `Depends(require_human_api)` at the route itself (`api/asks.py`).
    app.include_router(asks_router)
    # Worker-hook lane, ungated: attach (issue #113), git-commit declarations (#143), the
    # artifact read (#127), the chunk-history read (#237), and the work-item proxy.
    app.include_router(attachments_router)
    app.include_router(git_commits_router)
    app.include_router(artifacts_router)
    app.include_router(history_router)
    app.include_router(work_items_router)
    # Human web lane (gated under an oauth-mode hub): the panel's own reads/writes,
    # starting with the chunk-detail dock's pass-through proxy (issue #185).
    app.include_router(chunk_detail_router, dependencies=human_api)
    app.include_router(leases_router, dependencies=human_api)
    app.include_router(transcripts_router, dependencies=human_api)
    app.include_router(selftests_router, dependencies=human_api)
    # The fleet-summary pass-through (issue #76), forwarded to the hub.
    app.include_router(fleet_summary_router, dependencies=human_api)
    # The runtime workspace-prompt control (issue #17).
    app.include_router(workspace_prompt_router, dependencies=human_api)
    # The runner's own declarative pause brake (issue #43): local, distinct from the hub's,
    # and reachable with the hub down — the operator contract's standing requirement. Also
    # carries `GET /runner` (issue #51), the status summary.
    app.include_router(control_router, dependencies=human_api)
    # The machine-local status view's remaining list routes (issue #51): held
    # environments and parked escalations. `GET /asks` rides the existing `asks_router`.
    app.include_router(environments_router, dependencies=human_api)
    app.include_router(escalations_router, dependencies=human_api)
    # The local fact log: the outbound buffer read as a ledger, for the local panel.
    app.include_router(facts_router, dependencies=human_api)
    # The operator takeover (issue #52).
    app.include_router(takeovers_router, dependencies=human_api)
    # The operator requeue (issue #53).
    app.include_router(requeues_router, dependencies=human_api)

    # The runner-served web app: the human web lane the middleware above gates
    # (issue #95) — the only browser-facing surface this daemon serves.
    mount_web_app(app, frontend_dir("runner"), app_name="blizzard-runner")

    log.info("runner app created", db_url=config.db_url, readiness_wired=readiness is not None)
    return app


def build_hosted_app(config: RunnerConfig) -> FastAPI:
    """The ``host`` composition root: open the store and wire the readiness seam.

    Constructs the engine and the store-status reader once here (``bzh:dependency-injection``)
    and injects them through the domain :class:`ReadinessService`. Engine creation
    is connection-free, so this stays cheap; the connection is opened lazily on the
    first ``/api/ready`` read.
    """
    engine = create_engine_from_url(config.db_url)
    reader = SqlAlchemyStoreStatusReader(engine)
    expected = migration_runner(config).script_head()
    readiness = ReadinessService(reader=reader, expected_revision=expected)
    runner_store = SqlAlchemyRunnerStore(engine)
    # Bind the reference execution seams (winter workspace, Claude Code) from config —
    # exposed on ``app.state`` for the runner's local API surface.
    workspace_provider: IWorkspaceProvider = WinterWorkspaceProvider(
        workspace_root=config.workspace_root or str(config.root),
        env_pool=config.workspace_envs,
        base_branch=config.base_branch,
    )
    # ``transcripts_root`` empty means ``~/.claude/projects`` (Claude Code's own
    # default) — resolved here, once, never inside the adapter (``config.py``'s
    # standing comment).
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
    # The panel's derived-lease-state read (issue #28) — ``stale_after`` is left at its
    # default (``HEARTBEAT_STALENESS_THRESHOLD``) so the panel and REAP never desync.
    leases = LocalLeaseService(store=runner_store, clock=SystemClock(), process=LinuxProcessProbe())
    # The panel's transcript read (issue #29), projected off the harness's own source
    # — obtained via the accessor, never constructed twice.
    transcript_repository = ProjectedTranscriptRepository(harness.transcript_source())
    transcripts = LocalTranscriptService(
        store=runner_store, transcripts=transcript_repository, workspace_root=config.workspace_root
    )
    # The machine-local status view (issue #51). The clock/probe instances below are
    # per-service rather than shared: both are stateless, so a second instance is
    # equivalent to sharing one.
    runner_status = RunnerStatusService(
        store=runner_store,
        clock=SystemClock(),
        harness=harness,
        runner_id=config.runner_id,
        workspace_id=config.workspace_id,
        max_agents=config.max_agents,
        hub_url=config.hub_url,
        env_pool=config.workspace_envs,
    )
    # ``blizzard runner takeover``'s backing service (issue #52).
    takeover = TakeoverService(
        runner_store,
        SystemClock(),
        harness,
        LinuxProcessProbe(),
        # The same derivation the loop's spawn preamble uses, so a taken-over session's
        # ``BLIZZARD_RUNNER_URL`` matches a daemon-spawned one's.
        local_api_url=config.local_api_url,
    )
    # ``blizzard runner requeue``'s backing service (issue #53).
    requeue = RequeueService(runner_store, SystemClock())
    # ``blizzard runner attach``'s backing service (issue #113).
    attachments = AttachmentService(runner_store, SystemClock())
    # ``blizzard runner artifact commit``'s backing service (issue #143). Takes the
    # workspace provider too: a declaration is checked against the environment's repo
    # manifest, which is the provider's to declare.
    git_commit_declarations = GitCommitDeclarationService(runner_store, SystemClock(), workspace_provider)
    # The SSO federation jti replay cache (issue #95, decision D4) — store-backed over
    # the same engine every other seam above shares.
    jti_cache = JtiCacheRepository(engine)
    # The real, network-reaching hub client (issue #95) — only the `host` composition
    # root wires one; `create_app`'s own default is a hermetic double (see its own
    # docstring for why).
    hub_http_client = httpx.Client(base_url=config.hub_url, timeout=5.0)
    return create_app(
        config,
        readiness=readiness,
        workspace_provider=workspace_provider,
        harness=harness,
        runner_store=runner_store,
        leases=leases,
        transcripts=transcripts,
        runner_status=runner_status,
        takeover=takeover,
        requeue=requeue,
        attachments=attachments,
        git_commit_declarations=git_commit_declarations,
        jti_cache=jti_cache,
        hub_http_client=hub_http_client,
    )


def create_app_for_export() -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs)."""
    return create_app(RunnerConfig(root=Path("."), db_url="sqlite://"))
