"""Wire the served runner app from the host graph (``bzh:dependency-injection``).

The single place collaborators are constructed and injected. ``create_app`` does **not**
open the store, which lets the OpenAPI exporter and unit tests build the app without a
migrated database; the startup revision guard and the offline ``migrate`` verb own it."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote

import httpx2
from fastapi import APIRouter, Depends, FastAPI, Request, params
from fastapi.exceptions import HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import Engine
from starlette.datastructures import State
from starlette.types import ASGIApp, Receive, Scope, Send

from blizzard import __version__
from blizzard.foundation.clock import IClock, IMonotonicClock, SystemClock, SystemMonotonicClock
from blizzard.foundation.flags import FlagOffError, FlagRegistry, Flags, resolve_flags
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.attributes import annotate
from blizzard.foundation.platform_tracing.handle import DISABLED_TELEMETRY, IPlatformTracing
from blizzard.foundation.roles import collaborator
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.foundation.web import Frontend, install_schema_components
from blizzard.runner.api.analytics import router as analytics_router
from blizzard.runner.api.artifacts import router as artifacts_router
from blizzard.runner.api.asks import router as asks_router
from blizzard.runner.api.attachments import router as attachments_router
from blizzard.runner.api.chunk_asks import router as chunk_asks_router
from blizzard.runner.api.chunk_detail import router as chunk_detail_router
from blizzard.runner.api.chunk_notes import router as chunk_notes_router
from blizzard.runner.api.control import router as control_router
from blizzard.runner.api.dashboard import router as dashboard_router
from blizzard.runner.api.escalations import router as escalations_router
from blizzard.runner.api.events import router as events_router
from blizzard.runner.api.facts import router as facts_router
from blizzard.runner.api.federation import (
    FederationSettings,
    HostSignInClient,
    ISignInClient,
    NeedsFederationBounce,
    RunnerSignInClient,
    require_human_api,
    require_human_session,
)
from blizzard.runner.api.federation import router as auth_router
from blizzard.runner.api.finding import router as finding_router
from blizzard.runner.api.flag_guard import refuse_flag_off
from blizzard.runner.api.flags import router as flags_router
from blizzard.runner.api.fleet_summary import router as fleet_summary_router
from blizzard.runner.api.floor import host_router as host_floor_router
from blizzard.runner.api.floor import runner_router as runner_floor_router
from blizzard.runner.api.garden import router as garden_router
from blizzard.runner.api.git_commits import router as git_commits_router
from blizzard.runner.api.harness_health import router as harness_health_router
from blizzard.runner.api.health import router as health_router
from blizzard.runner.api.heartbeat import router as heartbeat_router
from blizzard.runner.api.history import router as history_router
from blizzard.runner.api.host import router as host_router
from blizzard.runner.api.invariants import router as host_invariants_router
from blizzard.runner.api.invocation_usage import router as invocation_usage_router
from blizzard.runner.api.leases import router as leases_router
from blizzard.runner.api.otlp_receiver import router as otlp_receiver_router
from blizzard.runner.api.readiness import router as readiness_router
from blizzard.runner.api.requeues import router as requeues_router
from blizzard.runner.api.scope import router as scope_router
from blizzard.runner.api.selftests import router as selftests_router
from blizzard.runner.api.session_end import router as session_end_router
from blizzard.runner.api.takeovers import router as takeovers_router
from blizzard.runner.api.ticks import host_router as host_ticks_router
from blizzard.runner.api.ticks import router as ticks_router
from blizzard.runner.api.traces import router as traces_router
from blizzard.runner.api.transcript_segments import router as transcript_segments_router
from blizzard.runner.api.transcripts import router as transcripts_router
from blizzard.runner.api.usage import router as usage_router
from blizzard.runner.api.wiring import RunnerRegistry, ServedRunner, ServedWorkspace, bind_runner
from blizzard.runner.api.work_items import router as work_items_router
from blizzard.runner.api.workspace_prompt import router as workspace_prompt_router
from blizzard.runner.auth.internal.http_hub_auth_mode import HttpHubAuthMode
from blizzard.runner.auth.internal.http_jwks_cache import JwksCache
from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.composition import HostProcess, RunnerProcess, build_runner_process
from blizzard.runner.config import HostWorkspace, RunnerConfig, SubscriptionDeclaration
from blizzard.runner.environments.workspaces import IWorkspaces
from blizzard.runner.events.broker import EventBroker, RunnerEventPublisher
from blizzard.runner.harness.health_cache import HarnessHealthCache, IReadHarnessHealth
from blizzard.runner.harness.registry import HarnessRegistry, IHarnessRegistry
from blizzard.runner.harness.workspace_prompts import WorkspacePromptReader, WorkspacePromptService
from blizzard.runner.host_config import HaltedRunner
from blizzard.runner.hub.client import BindingDeclaration, ITokenIdentityReader
from blizzard.runner.hub.held_token import ICurrentHubToken
from blizzard.runner.hub.host_identity import HostIdentityHolder, ICurrentHostIdentity
from blizzard.runner.hub.host_registration import HostRegistrationHolder, ICurrentHostRegistration
from blizzard.runner.hub.identity import ICurrentIdentityVerdict, ICurrentRunnerIdentity, IIdentityOnContact
from blizzard.runner.hub.internal.http_hub import HeldTokenAuth, HttpHubClient
from blizzard.runner.leases.activity import LocalLeaseService
from blizzard.runner.leases.asks import AskService
from blizzard.runner.leases.liveness import LeaseLivenessService
from blizzard.runner.leases.session import LeaseSessionService
from blizzard.runner.lifecycle.identity_check import ProxyIdentityCheck
from blizzard.runner.lifecycle.judgement.git_commit_declaration import GitCommitDeclarationService
from blizzard.runner.lifecycle.judgement.internal.elicitation_files import ElicitationFiles
from blizzard.runner.lifecycle.takeover import TakeoverService
from blizzard.runner.loop.step_passes import HostPassStepper
from blizzard.runner.loop_wiring import ResumeMarking, RunnerStepper, host_pass_stepper
from blizzard.runner.operator.attachments import AttachmentService
from blizzard.runner.operator.requeue import RequeueService
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses
from blizzard.runner.route_paths import RUNNER_ROUTE_PREFIX, RUNNERS_SEGMENT, is_api_path
from blizzard.runner.selftest.internal.subprocess_scratch_git import SubprocessScratchGit
from blizzard.runner.selftest.service import SelfTestService
from blizzard.runner.status.invariants import HostInvariantCheck
from blizzard.runner.status.view import RunnerStatusService
from blizzard.runner.stores import HostReadStores, HostStores, IRunnerBinding, RunnerReadStores, RunnerStores
from blizzard.runner.throttle.floor_clear import RunnerFloorClearService, SubscriptionFloorClearService
from blizzard.runner.throttle.gate import GateReadConfig, GateReadContext, ThrottleGate
from blizzard.runner.throttle.pause import PauseService
from blizzard.runner.tracing.exclusion import is_excluded
from blizzard.runner.tracing.platform import identity_stamp
from blizzard.runner.tracing.receiving import TelemetryReceiver
from blizzard.runner.tracing.replay import LeaseTraceReplay
from blizzard.runner.tracing.status import LeaseTraceStatusReader
from blizzard.runner.transcripts.internal.harness_transcript_repositories import HarnessTranscriptRepositories
from blizzard.runner.transcripts.internal.http_archived_transcript_repository import (
    HttpArchivedTranscriptRepository,
)
from blizzard.runner.transcripts.service import TranscriptService
from blizzard.wire.components import RUNNER_SCHEMA_COMPONENTS


@collaborator
@dataclass(frozen=True)
class Lane:
    """One tenant of the three-tenant partition — a router set and the gate it
    mounts behind. Only the human web lane is session-gated, and that gate covers the served
    shell as well as the JSON API it reads; the other two cannot be gated, being respectively
    what *establishes* a session and what workers call over TCP where they cannot bounce.
    A runner's lane mounts under its runner's prefix, behind the dependency that binds the runner it names."""

    gate: Sequence[params.Depends]
    routers: tuple[APIRouter, ...]
    per_runner: bool = False

    def mount(self, app: FastAPI) -> None:
        dependencies = [*self.gate, Depends(bind_runner)] if self.per_runner else list(self.gate)
        prefix = RUNNER_ROUTE_PREFIX if self.per_runner else ""
        for router in self.routers:
            app.include_router(router, prefix=prefix, dependencies=dependencies)


_HUMAN_GATE = (Depends(require_human_api),)

# The host's own routes, at the root. Ungated: liveness and the SSO bounce.
_HOST_UNGATED = (health_router, flags_router, readiness_router, auth_router)
# The host's human web lane: its own reads, the workspace prompts, and the one event stream.
_HOST_HUMAN = (
    host_router,
    workspace_prompt_router,
    host_floor_router,
    host_ticks_router,
    host_invariants_router,
    events_router,
)
# Each runner's ungated routes, in mount order: the worker hooks. `asks_router` is the one mixed router — its
# POST is ungated, its GET carries the human gate at the route.
_RUNNER_UNGATED = (
    heartbeat_router,
    session_end_router,
    asks_router,
    attachments_router,
    git_commits_router,
    artifacts_router,
    history_router,
    chunk_asks_router,
    chunk_notes_router,
    work_items_router,
    garden_router,
    finding_router,
    scope_router,
    analytics_router,
    otlp_receiver_router,
)
# Each runner's human web lane: the panel's reads and writes of one runner, its own
# pause brake reachable with the hub down, and the pass-throughs proxied to the hub.
_RUNNER_HUMAN = (
    chunk_detail_router,
    leases_router,
    invocation_usage_router,
    transcripts_router,
    usage_router,
    transcript_segments_router,
    selftests_router,
    fleet_summary_router,
    control_router,
    ticks_router,
    runner_floor_router,
    escalations_router,
    facts_router,
    takeovers_router,
    traces_router,
    harness_health_router,
    dashboard_router,
    requeues_router,
)


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Set ``app.state.shutdown`` on the ASGI ``lifespan`` "shutdown" message — sent *after*
    uvicorn's own graceful-drain wait, so this is the only signal a composer without an
    early-shutdown wrapper gets."""
    yield
    app.state.shutdown.set()


def runner_seams(
    config: RunnerConfig,
    *,
    clock: IClock,
    process: LinuxProcessProbe,
    children: IChildProcesses,
    events: EventBroker | None = None,
    harnesses: IHarnessRegistry | None = None,
    runner_stores: RunnerStores | None = None,
    leases: LocalLeaseService | None = None,
    transcripts: TranscriptService | None = None,
    runner_status: RunnerStatusService | None = None,
    trace_status: LeaseTraceStatusReader | None = None,
    trace_replay: LeaseTraceReplay | None = None,
    takeover: TakeoverService | None = None,
    requeue: RequeueService | None = None,
    selftests: SelfTestService | None = None,
    harness_health: IReadHarnessHealth | None = None,
    attachments: AttachmentService | None = None,
    git_commit_declarations: GitCommitDeclarationService | None = None,
    asks: AskService | None = None,
    pause: PauseService | None = None,
    lease_liveness: LeaseLivenessService | None = None,
    lease_sessions: LeaseSessionService | None = None,
    hub_proxy_client: httpx2.Client | None = None,
    identity_verdict: ICurrentIdentityVerdict | None = None,
    identity_check: IIdentityOnContact | None = None,
    token_identity: ITokenIdentityReader | None = None,
    hub_token: ICurrentHubToken | None = None,
    binding: IRunnerBinding | None = None,
    identity: ICurrentRunnerIdentity | None = None,
    stepper: RunnerStepper | None = None,
    tracing: TracingSettings | None = None,
) -> ServedRunner:
    """One runner's seams, served under :attr:`RunnerConfig.served_id()`. Every store-backed seam is optional, so a
    store-free runner is possible; its routes then answer 503. ``selftests`` is always wired. ``identity_check`` gates
    the hub proxy, which forwards only once it verifies — asking ``token_identity`` — that the token names the
    configured runner; absent either, the proxy forwards ungated. ``hub_token`` is the token the proxy presents;
    absent, the proxy presents ``config``'s. ``binding`` is the store's runner binding; should it name no runner, the
    routes that write runner-scoped rows answer 503. ``identity`` is the runner's own holder, which every platform
    span its requests open is stamped from."""
    runner_id = config.served_id()
    publisher = RunnerEventPublisher.over(events, runner_id)
    seams = State()
    seams.config = config
    seams.identity = identity
    seams.stepper = stepper
    seams.identity_verdict = identity_verdict
    seams.identity_check = identity_check
    seams.token_identity = token_identity
    seams.hub_token = hub_token
    seams.binding = binding
    seams.harnesses = harnesses if harnesses is not None else HarnessRegistry({})
    # The controller-facing narrowing of `runner_stores`; the write bundle itself is never stashed.
    seams.runner_read_stores = RunnerReadStores.of(runner_stores) if runner_stores is not None else None
    # The runner's publish seam, stamping its id on every runner-scoped event.
    seams.events = publisher
    seams.leases = leases
    seams.transcripts = transcripts
    seams.runner_status = runner_status
    seams.trace_status = trace_status
    seams.trace_replay = trace_replay
    seams.takeover = takeover
    seams.requeue = requeue
    seams.attachments = attachments
    seams.git_commit_declarations = git_commit_declarations
    # Each single-concept, no cross-collaborator beyond its own write seam and the clock
    # — built here, like `selftests` below, rather than requiring
    # every composer to repeat the derivation `runner_stores` already makes trivial.
    seams.asks = asks or (AskService(runner_stores.asks, clock, events=publisher) if runner_stores else None)
    seams.pause = pause or (PauseService(runner_stores.pause, clock, events=publisher) if runner_stores else None)
    subscription = config.resolved_subscriptions()[0].slug if config.resolved_subscriptions() else None
    seams.runner_floor_clears = (
        RunnerFloorClearService(runner_stores.usage_limits, subscription, clock) if runner_stores else None
    )
    seams.lease_liveness = lease_liveness or (
        LeaseLivenessService(runner_stores.liveness, clock) if runner_stores else None
    )
    seams.lease_sessions = lease_sessions or (
        LeaseSessionService(runner_stores.session, clock) if runner_stores else None
    )
    # The adapter-drift canary: wired unconditionally regardless of `runner_stores` —
    # only `results` needs one, and stays `None` (no durable outcome) without it.
    seams.selftests = selftests or SelfTestService(
        scratch_git=SubprocessScratchGit(children, config.tool_env(tracing or TracingSettings.of(os.environ))),
        process=process,
        elicitation_replies=ElicitationFiles(root=""),
        clock=clock,
        results=runner_stores.selftest_results if runner_stores else None,
    )
    # The runner's own health diagnostics; absent, the route and dashboard refuse.
    seams.harness_health = harness_health
    # This default must **not** reach the network — pinned by
    # tests/test_pin_runner_misc.py::test_the_default_hub_client_never_reaches_the_configured_hub_url
    seams.hub_proxy_client = hub_proxy_client or httpx2.Client(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(404)),
        base_url="http://runner-hub-proxy-client-hermetic-default.invalid",
    )
    return ServedRunner(runner_id, config.name, seams)


def create_app(
    config: RunnerConfig,
    *,
    runners: Sequence[ServedRunner] | None = None,
    halted: Sequence[HaltedRunner] = (),
    workspaces: Sequence[ServedWorkspace] | None = None,
    subscriptions: Sequence[SubscriptionDeclaration] | None = None,
    readiness: ReadinessService | None = None,
    workspace_registry: IWorkspaces | None = None,
    harnesses: IHarnessRegistry | None = None,
    runner_stores: RunnerStores | None = None,
    host_stores: HostStores | None = None,
    host_registration: ICurrentHostRegistration | None = None,
    host_identity: ICurrentHostIdentity | None = None,
    leases: LocalLeaseService | None = None,
    transcripts: TranscriptService | None = None,
    runner_status: RunnerStatusService | None = None,
    trace_status: LeaseTraceStatusReader | None = None,
    trace_replay: LeaseTraceReplay | None = None,
    telemetry_receiver: TelemetryReceiver | None = None,
    takeover: TakeoverService | None = None,
    requeue: RequeueService | None = None,
    selftests: SelfTestService | None = None,
    harness_health: IReadHarnessHealth | None = None,
    attachments: AttachmentService | None = None,
    git_commit_declarations: GitCommitDeclarationService | None = None,
    asks: AskService | None = None,
    pause: PauseService | None = None,
    lease_liveness: LeaseLivenessService | None = None,
    lease_sessions: LeaseSessionService | None = None,
    workspace_prompts: WorkspacePromptService | None = None,
    hub_http_client: httpx2.Client | None = None,
    hub_proxy_client: httpx2.Client | None = None,
    hub_retry_clock: IMonotonicClock | None = None,
    jti_cache: IJtiCache | None = None,
    events: EventBroker | None = None,
    clock: IClock | None = None,
    process: LinuxProcessProbe | None = None,
    children: IChildProcesses | None = None,
    platform_tracing: IPlatformTracing | None = None,
    identity: ICurrentRunnerIdentity | None = None,
    identity_verdict: ICurrentIdentityVerdict | None = None,
    identity_check: IIdentityOnContact | None = None,
    token_identity: ITokenIdentityReader | None = None,
    hub_token: ICurrentHubToken | None = None,
    binding: IRunnerBinding | None = None,
    stepper: RunnerStepper | None = None,
    host_stepper: HostPassStepper | None = None,
    host_invariants: HostInvariantCheck | None = None,
    flags: Flags,
) -> FastAPI:
    """Build the host's app from resolved config: its own routes at the root, and each of ``runners`` under its
    prefix, keyed by id; ``halted`` are the runners the host holds unbuilt.

    With ``runners`` omitted, the app serves one runner composed from the runner seams passed here by
    :func:`runner_seams` — a host of one. ``workspaces`` defaults to each of ``config``'s declared workspaces, over
    ``workspace_registry``'s pool for it, and ``subscriptions`` to ``config``'s. A store-free build is
    possible; the store-backed routes then answer 503 and ``/api/ready`` reports ``ready=false``. ``events`` defaults
    absent, leaving the stream silent. ``platform_tracing`` is off unless the composition root passes a handle.
    ``telemetry_receiver`` is the host's worker telemetry receiver; absent, the OTLP routes answer 503. ``identity`` is
    the process's holder; absent, the federation routes answer 503. ``host_stores`` are the host-scoped stores;
    ``jti_cache`` defaults to theirs. ``flags`` is the host's resolved answer."""
    log = get_logger("blizzard.runner")
    clock = clock or SystemClock()
    process = process or LinuxProcessProbe()
    children = children or SubprocessChildProcesses()
    if runners is None:
        runners = (
            runner_seams(
                config,
                clock=clock,
                process=process,
                children=children,
                events=events,
                harnesses=harnesses,
                runner_stores=runner_stores,
                leases=leases,
                transcripts=transcripts,
                runner_status=runner_status,
                trace_status=trace_status,
                trace_replay=trace_replay,
                takeover=takeover,
                requeue=requeue,
                selftests=selftests,
                harness_health=harness_health,
                attachments=attachments,
                git_commit_declarations=git_commit_declarations,
                asks=asks,
                pause=pause,
                lease_liveness=lease_liveness,
                lease_sessions=lease_sessions,
                hub_proxy_client=hub_proxy_client,
                identity_verdict=identity_verdict,
                identity_check=identity_check,
                token_identity=token_identity,
                hub_token=hub_token,
                binding=binding,
                identity=identity,
                stepper=stepper,
            ),
        )
    if workspaces is None:
        workspaces = tuple(_served_workspace(workspace, workspace_registry) for workspace in config.workspaces)

    # No framework exporters: tests/test_apps.py::test_an_otlp_endpoint_installs_no_framework_exporters.
    app = FastAPI(
        title="blizzard-runner",
        version=__version__,
        lifespan=_lifespan,
        telemetry=platform_tracing.fastapi_telemetry(exclude=is_excluded)
        if platform_tracing is not None
        else DISABLED_TELEMETRY,
    )
    app.state.config = config
    app.state.host_stepper = host_stepper
    # The runner half of the invariant checker; absent, `GET /api/host/invariants` answers 503.
    app.state.host_invariants = host_invariants
    app.state.flags = flags
    app.state.runners = RunnerRegistry(tuple(runners), tuple(halted))
    app.state.workspaces = tuple(workspaces)
    app.state.subscriptions = tuple(subscriptions if subscriptions is not None else config.subscriptions)
    # A registered host signs in as itself; an unregistered one as its first runner.
    sign_in: ISignInClient | None = None
    if host_registration is not None and host_identity is not None and host_registration.current().registered():
        sign_in = HostSignInClient(host_registration, host_identity)
    elif identity is not None:
        sign_in = RunnerSignInClient(identity)
    if sign_in is not None:
        app.state.federation = FederationSettings(
            public_origins=config.public_origins,
            hub_url=config.hub_url,
            client=sign_in,
            role_policy=config.role_policy,
        )
    app.state.readiness = readiness
    # The host's workspace registry; `None` on the store-free app.
    app.state.workspace_registry = workspace_registry
    # The host-scoped stores, narrowed for controllers the way each runner's are.
    app.state.host_read_stores = HostReadStores.of(host_stores) if host_stores is not None else None
    # Where the host stands with its hub, as its registration step last found it.
    app.state.host_registration = host_registration or HostRegistrationHolder.for_host(registered=False)
    app.state.host_identity = host_identity or HostIdentityHolder()
    # The SSE broker; `None` when no stream is wired.
    app.state.event_broker = events
    # Set on shutdown by `_lifespan`; the stream route's live wait races it.
    app.state.shutdown = asyncio.Event()
    # Unconditional: a stateless wrapper over the wall clock (``bzh:injected-clock``),
    # needed whether or not a store is wired.
    app.state.clock = clock
    # The worker telemetry receiver, built once by the composition root and shared by every runner.
    app.state.telemetry_receiver = telemetry_receiver
    app.state.workspace_prompts = workspace_prompts or (
        WorkspacePromptService(host_stores.workspace_prompt, clock) if host_stores is not None else None
    )
    app.state.workspace_prompt_reader = (
        WorkspacePromptReader(host_stores.workspace_prompt, workspace_registry)
        if host_stores is not None and workspace_registry is not None
        else None
    )
    app.state.subscription_floor_clears = (
        SubscriptionFloorClearService(host_stores.usage_limits, clock) if host_stores is not None else None
    )
    # This default must **not** reach the network — pinned by
    # tests/test_pin_runner_misc.py::test_the_default_hub_client_never_reaches_the_configured_hub_url
    hub_http_client = hub_http_client or httpx2.Client(
        transport=httpx2.MockTransport(lambda _request: httpx2.Response(404)),
        # A fixed placeholder: `config.hub_url` may be empty or not a valid absolute
        # base, and every request here is answered locally by the transport above.
        base_url="http://runner-hub-client-hermetic-default.invalid",
    )
    app.state.hub_auth_mode = HttpHubAuthMode(hub_http_client)
    app.state.jwks_cache = JwksCache(hub_http_client, "/api/auth/jwks.json")
    # The monotonic clock the hub retry schedule reads — the system clock unless one is injected.
    app.state.hub_retry_clock = hub_retry_clock or SystemMonotonicClock()
    app.state.jti_cache = jti_cache or (host_stores.jti_cache if host_stores is not None else None)
    # The reverse-proxy trust set, empty by default — so
    # `X-Forwarded-Proto` is ignored from every peer.
    app.state.trusted_proxies = TrustedProxies.parse(config.trusted_proxies)
    # The configured secret keeps sessions alive across a restart; without one, a fresh
    # per-process secret means a restart invalidates every live session.
    if config.session_secret:
        app.state.session_secret = config.session_secret
    else:
        log.warning(
            "session secret unset; sessions will not survive a runner restart",
            env_var=config.session_secret_env,
        )
        app.state.session_secret = secrets.token_bytes(32)

    app.add_exception_handler(FlagOffError, refuse_flag_off)

    @app.exception_handler(NeedsFederationBounce)
    def _bounce_to_login(_: Request, exc: NeedsFederationBounce) -> RedirectResponse:
        return RedirectResponse(f"/api/auth/login?return_to={quote(exc.return_to, safe='')}")

    # An excluded request (the worker heartbeat, an OTLP receiver path) makes no server span, and
    # none of its store reads may start a sampled root of their own either.
    if platform_tracing is not None:

        @app.middleware("http")
        async def _suppress_excluded(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            if is_excluded(request.scope):
                with platform_tracing.suppressed():
                    return await call_next(request)
            return await call_next(request)

    # Every platform span a request under a runner's prefix opens is that runner's; one on the host's own routes
    # carries no runner identity.
    if platform_tracing is not None:
        app.add_middleware(_RunnerStampMiddleware, platform_tracing=platform_tracing)

    # The served shell's half of the human web lane's gate — see :class:`Lane`.
    @app.middleware("http")
    async def _gate_web_surface(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if not is_api_path(request.url.path):
            try:
                require_human_session(request)
            except NeedsFederationBounce as exc:
                return RedirectResponse(f"/api/auth/login?return_to={quote(exc.return_to, safe='')}")
            except HTTPException as exc:
                # Raised inside an `http` middleware, an HTTPException skips FastAPI's handlers.
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
        return await call_next(request)

    # API routers first, so the API always wins over the web mount at /.
    Lane((), _HOST_UNGATED).mount(app)
    Lane(_HUMAN_GATE, _HOST_HUMAN).mount(app)
    Lane((), _RUNNER_UNGATED, per_runner=True).mount(app)
    Lane(_HUMAN_GATE, _RUNNER_HUMAN, per_runner=True).mount(app)

    # The host-served web app: the human web lane the middleware above gates
    # — the only browser-facing surface this daemon serves.
    Frontend.embedded("runner", app_name="blizzard-runner", is_api_path=is_api_path).mount(app)
    install_schema_components(app, RUNNER_SCHEMA_COMPONENTS.models, RUNNER_SCHEMA_COMPONENTS.enums)

    log.info("runner app created", db_url=config.db_url, readiness_wired=readiness is not None)
    return app


class _RunnerStampMiddleware:
    """Stamp a request under ``/runners/{runner_id}`` as that runner — read from the app's registry, so a runner the
    host does not serve stamps nothing: its own server span, which opens before any middleware runs, and every span
    it opens under that."""

    def __init__(self, app: ASGIApp, *, platform_tracing: IPlatformTracing) -> None:
        self.app = app
        self._platform_tracing = platform_tracing

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        identity = _served_identity(scope) if scope["type"] == "http" else None
        if identity is None:
            await self.app(scope, receive, send)
            return
        stamp = identity_stamp(identity)
        if stamped := stamp():
            annotate(stamped)
        with self._platform_tracing.stamping(stamp):
            await self.app(scope, receive, send)


def _served_identity(scope: Scope) -> ICurrentRunnerIdentity | None:
    parts = str(scope.get("path", "")).split("/", 3)
    if len(parts) < 3 or f"/{parts[1]}" != RUNNERS_SEGMENT:
        return None
    registry: RunnerRegistry | None = getattr(scope["app"].state, "runners", None)
    for served in registry.served if registry is not None else ():
        if served.runner_id == parts[2]:
            return getattr(served.seams, "identity", None)
    return None


@dataclass(frozen=True)
class HostedApp:
    """The served app and restart-resume hooks wired from one host graph."""

    app: FastAPI
    #: One resume hook per hosted runner, in the host's runner order.
    resumes: tuple[ResumeMarking, ...]
    engine: Engine
    #: The one `HarnessHealthCache` shared with the app, so a read and the registered availability agree; ``None``
    #: when the host built no runner.
    harness_health: HarnessHealthCache | None
    #: The authenticated hub clients each runner's routes own — its archived-transcript reads and its hub proxy.
    runner_clients: tuple[httpx2.Client, ...]
    hub_http_client: httpx2.Client
    owned_host: HostProcess | None = None
    steppers: tuple[RunnerStepper, ...] = ()
    host_stepper: HostPassStepper | None = None

    @property
    def resume(self) -> ResumeMarking:
        """The sole runner's resume hook."""
        (resume,) = self.resumes
        return resume

    def close(self) -> None:
        with contextlib.ExitStack() as closing:
            if self.owned_host is not None:
                closing.callback(self.owned_host.close)
            for stepper in self.steppers:
                closing.callback(stepper.close)
            for client in self.runner_clients:
                closing.callback(client.close)
            closing.callback(self.hub_http_client.close)


def build_hosted_app(
    config: RunnerConfig,
    host: HostProcess | None = None,
    *,
    events: EventBroker | None = None,
    process_graph: RunnerProcess | None = None,
) -> HostedApp:
    """Wire the served app and recovery hooks from the host graph.

    ``host`` is the host the caller built and owns; ``process_graph`` names a runner whose host serves instead.
    With neither, this builds a host of one over ``config``'s runner and the returned app closes it."""
    owned = host is None and process_graph is None
    if host is None:
        if process_graph is None:
            process_graph = build_runner_process(config, events=events)
        host = process_graph.host
    with contextlib.ExitStack() as startup:
        if owned:
            startup.callback(host.close)
        hosted = _wire_hosted_app(config, host, startup, owned_host=host if owned else None)
        startup.pop_all()  # HostedApp.close now owns every registered resource.
        return hosted


def _wire_hosted_app(
    config: RunnerConfig, host: HostProcess, startup: contextlib.ExitStack, *, owned_host: HostProcess | None
) -> HostedApp:
    engine = host.engine
    readiness = host.readiness
    if not host.runners:
        return _wire_runnerless_app(config, host, startup, owned_host=owned_host)
    # Each runner's routes mount under its own id, over its own seams and its own authenticated hub clients.
    # The first runner keeps the config the caller built the app from; each other runner is served from its own.
    wired = [
        _wire_runner(config if index == 0 else graph.config, host, graph, startup)
        for index, graph in enumerate(host.runners)
    ]
    first = host.runners[0]
    host_stepper = host_pass_stepper(host)
    # The real, network-reaching hub client — only `host` wires one.
    hub_http_client = startup.enter_context(httpx2.Client(base_url=config.hub_url, timeout=5.0))
    host.platform_tracing.instrument_client(hub_http_client)
    app = create_app(
        replace(config, runner_id=first.runner_id),
        runners=tuple(runner.served for runner in wired),
        halted=host.halted,
        subscriptions=host.subscriptions,
        platform_tracing=host.platform_tracing,
        identity=first.identity,
        readiness=readiness,
        workspace_registry=host.workspaces,
        host_stores=host.host_stores,
        host_registration=host.host_registration,
        host_identity=host.host_identity,
        telemetry_receiver=host.telemetry_receiver,
        hub_http_client=hub_http_client,
        events=host.events,
        clock=host.clock,
        process=host.process,
        host_stepper=host_stepper,
        host_invariants=host.invariants,
        flags=host.flags,
    )
    resumes = tuple(ResumeMarking(runner.stores, host.host_stores, host.clock, host.process) for runner in host.runners)
    return HostedApp(
        app,
        resumes,
        engine,
        wired[0].harness_health,
        tuple(client for runner in wired for client in runner.clients),
        hub_http_client,
        owned_host,
        tuple(runner.stepper for runner in wired),
        host_stepper,
    )


@dataclass(frozen=True)
class _WiredRunner:
    """One hosted runner's served seams, its harness health, and the hub clients it owns."""

    served: ServedRunner
    stepper: RunnerStepper
    harness_health: HarnessHealthCache
    clients: tuple[httpx2.Client, ...]


def _wire_runner(
    config: RunnerConfig, host: HostProcess, graph: RunnerProcess, startup: contextlib.ExitStack
) -> _WiredRunner:
    """``graph``'s seams, served under its id, with its own authenticated hub clients entered on ``startup``."""
    config = replace(config, runner_id=graph.runner_id)
    runner_stores = graph.stores
    connections = graph.connections
    harness_health = graph.health
    workspaces = host.workspaces
    # The runner's own workspace, as such: the first its bindings name, else the host's first.
    home = config.home_workspace.id
    harnesses = graph.harnesses
    clock = host.clock
    process = host.process
    events = host.events
    platform_tracing = host.platform_tracing
    read_stores = RunnerReadStores.of(runner_stores)
    # ``stale_after`` is left at its default so the two readers never desync (#28).
    leases = LocalLeaseService(
        clock,
        process,
        lease_record=read_stores.lease_record,
        liveness=read_stores.liveness,
        asks=read_stores.asks,
        environments=read_stores.environments,
        overload=read_stores.overload,
        elicitations=read_stores.elicitations,
        git_commits=read_stores.git_commit_declarations,
        takeovers=read_stores.takeover,
        escalations=read_stores.escalations,
    )
    # The archived-transcript seam needs its own authenticated client:
    # `hub_http_client` below carries no auth headers (JWKS/hub-auth-mode reads only).
    archived_transcript_client = startup.enter_context(
        httpx2.Client(base_url=config.hub_url, timeout=15.0, auth=HeldTokenAuth(graph.hub_token))
    )
    platform_tracing.instrument_client(archived_transcript_client)
    archived_transcripts = HttpArchivedTranscriptRepository(archived_transcript_client)
    # The route-forward seam (`HubProxy`) needs its own authenticated client too — separate
    # from `hub_http_client` (no auth headers) and from `archived_transcript_client` (its own
    # lifetime/timeout concerns).
    hub_proxy_client = startup.enter_context(
        httpx2.Client(base_url=config.hub_url, timeout=15.0, auth=HeldTokenAuth(graph.hub_token))
    )
    platform_tracing.instrument_client(hub_proxy_client)
    transcripts = TranscriptService(
        leases=runner_stores.lease_record,
        transcript_ledger=runner_stores.transcript_ledger,
        environments=runner_stores.environments,
        transcripts=HarnessTranscriptRepositories(harnesses),
        archived=archived_transcripts,
        workspaces=workspaces,
    )
    runner_status = RunnerStatusService(
        clock,
        pause=read_stores.pause,
        daemon_liveness=graph.host.host_stores.daemon_liveness,
        lease_record=read_stores.lease_record,
        outbound=read_stores.outbound,
        environments=read_stores.environments,
        asks=read_stores.asks,
        takeover=read_stores.takeover,
        escalations=read_stores.escalations,
        identity=graph.identity,
        identity_verdict=graph.identity_verdict,
        hub_version=graph.hub_version,
        served_projects=read_stores.served_projects,
        projects=config.projects,
        runner_name=config.name,
        workspace_id=home,
        bindings=tuple(
            BindingDeclaration(b.project, b.workspace, config.binding_agents(b)) for b in config.bindings or ()
        ),
        agents=config.agents,
        hub_url=config.hub_url,
        harnesses=harnesses,
        workspaces=workspaces,
        gates=config.gates,
        runner_dir=str(config.root),
        throttle_gate=ThrottleGate(
            GateReadContext(
                stores=read_stores,
                host_stores=graph.host.host_stores,
                config=GateReadConfig(
                    subscription=config.resolved_subscriptions()[0].slug if config.resolved_subscriptions() else None,
                    throttle=config.throttle,
                    host_zone=host.zone,
                ),
                clock=clock,
                subscriptions=host.subscriptions,
            )
        ),
        throttle=config.throttle,
    )
    takeover = TakeoverService(
        clock,
        process,
        takeover=runner_stores.takeover,
        outbound=runner_stores.outbound,
        tokens=runner_stores.tokens,
        elicitations=runner_stores.elicitations,
        # The same derivation the spawn preamble uses, so the two agree.
        local_api_url=config.local_api_url,
        harnesses=harnesses,
        workspaces=workspaces,
        events=graph.events,
    )
    requeue = RequeueService(runner_stores.requeue, clock)
    attachments = AttachmentService(runner_stores.attachments, clock, runner_stores.outbound)
    # Takes the workspaces too: a declaration is checked against the environment's repo manifest, which the
    # provider of the environment's workspace declares.
    git_commit_declarations = GitCommitDeclarationService(
        runner_stores.git_commit_declarations,
        clock,
        workspaces,
        environments=runner_stores.environments,
        outbound=runner_stores.outbound,
    )
    stepper = RunnerStepper(graph.config, process_graph=graph)
    served = runner_seams(
        config,
        clock=clock,
        process=process,
        children=graph.children,
        tracing=graph.trace_settings,
        events=events,
        binding=connections,
        identity_verdict=graph.identity_verdict,
        # The proxy's forward is a first contact with the hub: it asks which runner its token names.
        identity_check=ProxyIdentityCheck(graph.runner_id, graph.identity_verdict, clock),
        token_identity=HttpHubClient(hub_proxy_client),
        hub_token=graph.hub_token,
        harnesses=harnesses,
        runner_stores=runner_stores,
        leases=leases,
        transcripts=transcripts,
        runner_status=runner_status,
        trace_status=LeaseTraceStatusReader(
            settings=graph.trace_settings,
            leases=read_stores.lease_traces,
            clock=clock,
            receiver=host.receiver_counter,
            replay_max_window=config.tracing.replay_max_window,
            harness_telemetry=graph.harness_telemetry,
            harness_span_receiver=host.harness_span_counter,
            metric_receiver=host.metric_bounds.counter,
            log_receiver=host.log_bounds.counter,
        ),
        trace_replay=graph.trace_replay,
        takeover=takeover,
        requeue=requeue,
        attachments=attachments,
        git_commit_declarations=git_commit_declarations,
        harness_health=harness_health,
        hub_proxy_client=hub_proxy_client,
        identity=graph.identity,
        stepper=stepper,
    )
    return _WiredRunner(served, stepper, harness_health, (archived_transcript_client, hub_proxy_client))


def _wire_runnerless_app(
    config: RunnerConfig, host: HostProcess, startup: contextlib.ExitStack, *, owned_host: HostProcess | None
) -> HostedApp:
    """The app of a host that built no runner: what answers for the host itself — its health, and its readiness,
    which names each halted runner. A halted runner's routes answer 503 naming its reasons."""
    hub_http_client = startup.enter_context(httpx2.Client(base_url=config.hub_url, timeout=5.0))
    host.platform_tracing.instrument_client(hub_http_client)
    app = create_app(
        config,
        runners=(),
        halted=host.halted,
        subscriptions=host.subscriptions,
        platform_tracing=host.platform_tracing,
        readiness=host.readiness,
        workspace_registry=host.workspaces,
        host_stores=host.host_stores,
        hub_http_client=hub_http_client,
        events=host.events,
        clock=host.clock,
        process=host.process,
        host_invariants=host.invariants,
        flags=host.flags,
    )
    return HostedApp(app, (), host.engine, None, (), hub_http_client, owned_host)


def _served_workspace(workspace: HostWorkspace, registry: IWorkspaces | None) -> ServedWorkspace:
    """``workspace`` as the host serves it: its pool from ``registry``, none without one."""
    provider = registry.provider(workspace.id) if registry is not None else None
    return ServedWorkspace(
        workspace_id=workspace.id,
        env_pool=provider.pool() if provider is not None else (),
        max_environments=workspace.max_environments,
    )


def create_app_for_export(flag_registry: FlagRegistry) -> FastAPI:
    """Build the app with throwaway config for OpenAPI export (no store, no dirs), its flags at ``flag_registry``'s
    defaults."""
    return create_app(
        RunnerConfig(root=Path("."), db_url="sqlite://"), flags=resolve_flags(flag_registry, "runner", {}).flags
    )
