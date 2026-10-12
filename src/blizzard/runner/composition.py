"""The runner host, runner process and store composition root.

The only module under ``src/`` that names a concrete ``runner/store/internal/`` adapter,
asserted by
``tests/test_layering.py::test_composition_is_the_only_module_naming_a_concrete_runner_store_adapter``
— every other collaborator takes a Protocol seam or the
:class:`~blizzard.runner.stores.RunnerStores` bundle this builds."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import Engine

from blizzard import __version__
from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import (
    DisabledPlatformTracing,
    IPlatformTracing,
    build_platform_tracing,
)
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.store.readiness import ReadinessService
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.internal.otlp import OtlpTraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.factory import build_workspace_provider
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.capability_snapshot import default_harness_id
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames, HarnessTelemetryPlan
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.harness.wiring import (
    build_production_harness_health_probes,
    build_production_harness_registry,
    combined_telemetry_plan,
    configured_tiers,
    declared_telemetry_names,
)
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.identity import (
    ICurrentRunnerIdentity,
    IdentityVerdictHolder,
    RunnerIdentity,
    RunnerIdentityHolder,
)
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.listeners import Listeners
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.runtime import migration_runner, reset_command
from blizzard.runner.store.errors import RunnerStoreConnections, RunnerStoreErrorFactory
from blizzard.runner.store.internal.ask_store import AskStore
from blizzard.runner.store.internal.attachment_store import AttachmentStore
from blizzard.runner.store.internal.check_store import CheckStore
from blizzard.runner.store.internal.elicitation_store import ElicitationStore
from blizzard.runner.store.internal.environment_store import EnvironmentStore
from blizzard.runner.store.internal.escalation_store import EscalationStore
from blizzard.runner.store.internal.git_commit_declaration_store import GitCommitDeclarationStore
from blizzard.runner.store.internal.host_daemon_liveness_store import DaemonLivenessStore
from blizzard.runner.store.internal.host_external_usage_store import ExternalUsageStore
from blizzard.runner.store.internal.host_graph_artifact_store import GraphArtifactStore
from blizzard.runner.store.internal.host_jti_cache_store import JtiCacheRepository
from blizzard.runner.store.internal.host_runner_audit import RunnerAudit
from blizzard.runner.store.internal.host_unowned_row_stamp import UnownedRowStamp
from blizzard.runner.store.internal.host_unowned_row_stamp import stamp_unowned_rows as stamp_unowned_rows_on
from blizzard.runner.store.internal.host_workspace_prompt_store import WorkspacePromptStore
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from blizzard.runner.store.internal.invocation_boundary_store import InvocationBoundaryStore
from blizzard.runner.store.internal.lease_liveness_store import LeaseLivenessStore
from blizzard.runner.store.internal.lease_record_store import LeaseRecordStore
from blizzard.runner.store.internal.lease_resume_intent_store import LeaseResumeIntentStore
from blizzard.runner.store.internal.lease_session_store import LeaseSessionStore
from blizzard.runner.store.internal.lease_trace_facts_store import LeaseTraceFactsStore
from blizzard.runner.store.internal.outbound_store import OutboundStore
from blizzard.runner.store.internal.overload_store import OverloadStore
from blizzard.runner.store.internal.pause_store import PauseStore
from blizzard.runner.store.internal.projects_answer_store import ServedProjectsStore
from blizzard.runner.store.internal.requeue_store import RequeueStore
from blizzard.runner.store.internal.selftest_result_store import SelfTestResultStore
from blizzard.runner.store.internal.takeover_store import TakeoverStore
from blizzard.runner.store.internal.token_store import TokenStore
from blizzard.runner.store.internal.transcript_ledger_store import TranscriptLedgerStore
from blizzard.runner.store.internal.usage_store import UsageStore
from blizzard.runner.store.runner_guard import install_runner_guard
from blizzard.runner.stores import HostReadStores, HostStores, RunnerReadStores, RunnerStores
from blizzard.runner.subscriptions.internal.credential_renewer_factory import select_renewer
from blizzard.runner.subscriptions.internal.subprocess_one_shot_process import SubprocessOneShotProcess
from blizzard.runner.tracing.attributes import (
    INSTRUMENTATION_SCOPE,
    INSTRUMENTATION_SCOPE_VERSION,
    resource_attributes,
)
from blizzard.runner.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    identity_stamp,
)
from blizzard.runner.tracing.received_export import (
    DisabledReceivedTelemetryExport,
    IReceivedTelemetryExport,
    build_received_telemetry_export,
)
from blizzard.runner.tracing.receiver_limits import ReceiverBounds, ReceiverCounter, SpanRateLimiter
from blizzard.runner.tracing.receiving import TelemetryReceiver
from blizzard.runner.tracing.replay import LeaseTraceReplay
from blizzard.runner.tracing.sweep import LeaseTraceSweep
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass, RenewableSubscription
from blizzard.runner.usage.host_tick import HostTick


@dataclass
class HostProcess:
    """The per-host graph: the pieces one daemon shares across every runner it hosts, and the runners themselves.

    The host reaches a runner only through :attr:`runners`; a runner reaches the host it was built over. Close after
    every serving thread has stopped: closing the host closes each runner first, then disposes the engine and shuts
    tracing down, so a runner's spawner thread and resume drain finish before the engine they write through goes.
    """

    engine: Engine
    readiness: ReadinessService
    #: The host's own connections, never bound to a runner; ``host_stores`` runs over them.
    connections: RunnerStoreConnections
    host_stores: HostStores
    listeners: Listeners
    provider: IWorkspaceProvider
    events: EventBroker | None
    clock: SystemClock
    process: LinuxProcessProbe
    #: The worker telemetry receiver over the host's limiters, tallies, export and identity resolver — one per host,
    #: so every reader and writer of those counters shares them.
    telemetry_receiver: TelemetryReceiver
    #: The span receiver's per-lease rate bound and its received/dropped tally, one of each per host.
    span_limiter: SpanRateLimiter
    receiver_counter: ReceiverCounter
    #: The harness bindings' spans alone, counted apart from the shared span tally above.
    harness_span_counter: ReceiverCounter
    #: The metrics and logs receivers' own bound and tally, counted in data points and log records.
    metric_bounds: ReceiverBounds
    log_bounds: ReceiverBounds
    #: What every declared binding's telemetry arrives under, which the receiver admits and routes by.
    harness_telemetry_names: tuple[HarnessTelemetryNames, ...]
    #: The pass the host's own thread runs; its one step today is credential renewal.
    host_tick: HostTick
    #: Platform spans — disabled unless a handle is supplied; every collaborator opens spans through it.
    platform_tracing: IPlatformTracing = field(default_factory=DisabledPlatformTracing)
    #: Where admitted metrics and logs leave — disabled unless a handle is supplied.
    received_telemetry: IReceivedTelemetryExport = field(default_factory=DisabledReceivedTelemetryExport)
    runners: tuple[RunnerProcess, ...] = ()

    def close(self) -> None:
        with contextlib.ExitStack() as closing:
            closing.callback(self.platform_tracing.shutdown, PLATFORM_TRACING_SHUTDOWN_SECONDS)
            closing.callback(self.received_telemetry.shutdown, PLATFORM_TRACING_SHUTDOWN_SECONDS)
            closing.callback(self.engine.dispose)
            for runner in self.runners:
                closing.callback(runner.close_runner)


@dataclass(frozen=True)
class RunnerProcess:
    """One runner's own graph over a :class:`HostProcess`: its bound stores, identity, harnesses and spawner.

    Close after the loop has stopped and resume marking has drained workers: the spawner thread is the kernel parent
    of children awaiting durable confirmation. The host's engine outlives it.
    """

    host: HostProcess
    #: This runner's connections over the host's engine, bound to the runner it is.
    connections: RunnerStoreConnections
    stores: RunnerStores
    #: A host-scoped view over ``connections``: the tick's cross-kind write reports through the bound runner.
    host_stores: HostStores
    harnesses: HarnessRegistry
    health: HarnessHealthCache
    executor: ThreadPoolExecutor
    #: Whether OpenTelemetry's own variables enable lease tracing, read once for this runner.
    trace_settings: TracingSettings
    #: The lease-trace sweep — ``None`` unless tracing is enabled.
    trace_sweep: LeaseTraceSweep | None
    #: The operator's replay over the same assembly and exporter.
    trace_replay: LeaseTraceReplay
    #: Who this runner is at its hub; seeded from the store at boot and refreshed by every registration.
    identity: RunnerIdentityHolder
    #: Whether this runner has verified its token names the store's runner — unchecked from start.
    identity_verdict: IdentityVerdictHolder
    #: The token every runner->hub client presents — re-read from ``.env`` while the identity mismatches.
    hub_token: HubTokenHolder
    #: What the enabled bindings together do with each of a worker's telemetry signals, derived once at startup.
    harness_telemetry: HarnessTelemetryPlan = field(default_factory=HarnessTelemetryPlan)
    #: Whether closing this runner closes the host it was built over — the host-of-one a standalone build makes.
    closes_host: bool = False

    @property
    def engine(self) -> Engine:
        return self.host.engine

    @property
    def provider(self) -> IWorkspaceProvider:
        return self.host.provider

    @property
    def events(self) -> EventBroker | None:
        return self.host.events

    @property
    def clock(self) -> SystemClock:
        return self.host.clock

    @property
    def process(self) -> LinuxProcessProbe:
        return self.host.process

    @property
    def platform_tracing(self) -> IPlatformTracing:
        return self.host.platform_tracing

    @property
    def received_telemetry(self) -> IReceivedTelemetryExport:
        return self.host.received_telemetry

    @property
    def telemetry_receiver(self) -> TelemetryReceiver:
        return self.host.telemetry_receiver

    @property
    def span_limiter(self) -> SpanRateLimiter:
        return self.host.span_limiter

    @property
    def receiver_counter(self) -> ReceiverCounter:
        return self.host.receiver_counter

    @property
    def harness_span_counter(self) -> ReceiverCounter:
        return self.host.harness_span_counter

    @property
    def metric_bounds(self) -> ReceiverBounds:
        return self.host.metric_bounds

    @property
    def log_bounds(self) -> ReceiverBounds:
        return self.host.log_bounds

    @property
    def harness_telemetry_names(self) -> tuple[HarnessTelemetryNames, ...]:
        return self.host.harness_telemetry_names

    def close_runner(self) -> None:
        """Shut the spawner executor down, waiting for its running work."""
        self.executor.shutdown(wait=True)

    def close(self) -> None:
        try:
            self.close_runner()
        finally:
            if self.closes_host:
                self.host.close()


class _RunnersIdentity:
    """The :class:`~blizzard.runner.hub.identity.ICurrentRunnerIdentity` the host's shared pieces stamp from: the
    identity of the first of the host's runners that holds one. With one runner it answers exactly what that
    runner's holder answers, and nothing before its first registration."""

    def __init__(self, runners: Callable[[], tuple[RunnerProcess, ...]]) -> None:
        self._runners = runners

    def current(self) -> RunnerIdentity | None:
        for runner in self._runners():
            held = runner.identity.current()
            if held is not None:
                return held
        return None


def _held_environment_ids(runners: Callable[[], tuple[RunnerProcess, ...]]) -> Callable[[], list[str]]:
    """The environment ids held by any of the host's runners, in runner order."""

    def held() -> list[str]:
        ids: dict[str, None] = {}
        for runner in runners():
            ids.update(dict.fromkeys(runner.stores.environments.held_environment_ids()))
        return list(ids)

    return held


def _credential_renewal_pass(
    config: RunnerConfig, host_stores: HostStores, clock: SystemClock
) -> CredentialRenewalPass | None:
    """Every declared subscription paired with its provider's renewer binding, sharing one
    one-shot subprocess seam; ``None`` when no provider binds one."""
    subprocess = SubprocessOneShotProcess()
    renewable = [
        RenewableSubscription(
            slug=declaration.slug, sample_interval_seconds=declaration.sample_interval_seconds, renewer=renewer
        )
        for declaration in config.resolved_subscriptions()
        if (renewer := select_renewer(declaration, clock=clock, subprocess=subprocess)) is not None
    ]
    if not renewable:
        return None
    return CredentialRenewalPass(subscriptions=renewable, renewals=host_stores.external_usage, clock=clock)


#: How long the runner's shutdown waits for buffered platform spans to leave.
PLATFORM_TRACING_SHUTDOWN_SECONDS = 3.0


def build_runner_platform_tracing(
    config: RunnerConfig, environ: Mapping[str, str] | None = None, *, identity: ICurrentRunnerIdentity
) -> IPlatformTracing:
    """The handle ``[tracing]`` and the OTLP environment decide for this runner; every span it records
    carries the id and name ``identity`` answers as it starts, and none is exported before the runner's
    first registration."""
    env = os.environ if environ is None else environ
    return build_platform_tracing(
        config.tracing,
        env,
        resource=resource_attributes(env, __version__),
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
        stamp=identity_stamp(identity),
    )


def build_runner_received_telemetry(
    config: RunnerConfig, environ: Mapping[str, str] | None = None
) -> IReceivedTelemetryExport:
    """The handle Claude Code's received metrics and logs leave through: off unless ``[tracing]
    harness_telemetry`` and platform tracing are both on."""
    env = os.environ if environ is None else environ
    return build_received_telemetry_export(config.tracing, env, resource=resource_attributes(env, __version__))


def held_hub_token(config: RunnerConfig) -> HubTokenHolder:
    """The token the runner started with, re-readable from its ``.env`` unless the process environment
    supplied it."""
    file = None if config.token_from_process_env() else HubTokenFile.of(config.root, config.token_env)
    return HubTokenHolder(config.hub_token, file=file)


def build_host_process(
    config: RunnerConfig,
    *,
    events: EventBroker | None = None,
    bundle: BundleSnapshot | None = None,
    environ: Mapping[str, str] | None = None,
    trace_exporter: ITraceExporter | None = None,
    platform_tracing: IPlatformTracing | None = None,
    received_telemetry: IReceivedTelemetryExport | None = None,
    identity: RunnerIdentityHolder | None = None,
    runner_id: str | None = None,
    trace_platform: bool = False,
) -> HostProcess:
    """Construct the host graph and the runner it hosts; dispose partial resources on failure.

    ``environ`` decides whether tracing is enabled; ``trace_exporter`` replaces the OTLP binding an enabled sweep would
    otherwise build. ``trace_platform`` builds the platform-tracing and received-telemetry handles the config and
    environment decide, stamped from the host's runners; otherwise ``platform_tracing`` and ``received_telemetry`` are
    used as given. ``identity`` is the holder the runner keeps, seeded from the store's identity row either way, and
    ``runner_id`` binds the runner to that id instead of the store's newest identity row."""
    return _build_host_process(
        config,
        events=events,
        bundle=bundle,
        environ=environ,
        trace_exporter=trace_exporter,
        platform_tracing=platform_tracing,
        received_telemetry=received_telemetry,
        identity=identity,
        runner_id=runner_id,
        trace_platform=trace_platform,
        closes_host=False,
    )


def _build_host_process(
    config: RunnerConfig,
    *,
    events: EventBroker | None,
    bundle: BundleSnapshot | None,
    environ: Mapping[str, str] | None,
    trace_exporter: ITraceExporter | None,
    platform_tracing: IPlatformTracing | None,
    received_telemetry: IReceivedTelemetryExport | None,
    identity: RunnerIdentityHolder | None,
    runner_id: str | None,
    trace_platform: bool,
    closes_host: bool,
) -> HostProcess:
    install_runner_guard()
    engine = create_engine_from_url(config.db_url)
    try:
        built: list[HostProcess] = []

        def runners() -> tuple[RunnerProcess, ...]:
            return built[0].runners if built else ()

        resolver = _RunnersIdentity(runners)
        if trace_platform:
            platform_tracing = build_runner_platform_tracing(config, environ, identity=resolver)
            received_telemetry = build_runner_received_telemetry(config, environ)
        platform_tracing = platform_tracing or DisabledPlatformTracing()
        received_telemetry = received_telemetry or DisabledReceivedTelemetryExport()
        platform_tracing.instrument_engine(engine)
        readiness = ReadinessService(
            reader=SqlAlchemyStoreStatusReader(engine), expected_revision=migration_runner(config).script_head()
        )
        errors = RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
        connections = RunnerStoreConnections(engine, errors)
        clock = SystemClock()
        host_stores = _build_host_stores(connections, clock)
        span_limiter = SpanRateLimiter(clock)
        receiver_counter = ReceiverCounter()
        harness_span_counter = ReceiverCounter()
        metric_bounds = ReceiverBounds.fresh(clock)
        log_bounds = ReceiverBounds.fresh(clock)
        telemetry_names = declared_telemetry_names()
        host = HostProcess(
            engine=engine,
            readiness=readiness,
            connections=connections,
            host_stores=host_stores,
            listeners=Listeners.of(config),
            provider=build_workspace_provider(config.workspace_settings, held_ids=_held_environment_ids(runners)),
            events=events,
            clock=clock,
            process=LinuxProcessProbe(),
            telemetry_receiver=TelemetryReceiver(
                platform_tracing=platform_tracing,
                received_telemetry=received_telemetry,
                span_limiter=span_limiter,
                span_counter=receiver_counter,
                harness_span_counter=harness_span_counter,
                telemetry_names=telemetry_names,
                metric_bounds=metric_bounds,
                log_bounds=log_bounds,
                clock=clock,
                identity=resolver,
                worker_programs=config.tracing.worker_programs,
                harness_telemetry=config.tracing.harness_telemetry,
                mapped_services=config.tracing.worker_program_services,
            ),
            span_limiter=span_limiter,
            receiver_counter=receiver_counter,
            harness_span_counter=harness_span_counter,
            metric_bounds=metric_bounds,
            log_bounds=log_bounds,
            harness_telemetry_names=telemetry_names,
            host_tick=HostTick(credential_renewal=_credential_renewal_pass(config, host_stores, clock)),
            platform_tracing=platform_tracing,
            received_telemetry=received_telemetry,
        )
        built.append(host)
        host.runners = (
            _build_runner(
                config,
                host,
                bundle=bundle,
                environ=environ,
                trace_exporter=trace_exporter,
                identity=identity,
                runner_id=runner_id,
                closes_host=closes_host,
            ),
        )
        return host
    except BaseException:
        engine.dispose()
        raise


def build_runner_process(
    config: RunnerConfig,
    *,
    events: EventBroker | None = None,
    bundle: BundleSnapshot | None = None,
    environ: Mapping[str, str] | None = None,
    trace_exporter: ITraceExporter | None = None,
    platform_tracing: IPlatformTracing | None = None,
    received_telemetry: IReceivedTelemetryExport | None = None,
    identity: RunnerIdentityHolder | None = None,
    runner_id: str | None = None,
) -> RunnerProcess:
    """A host of one and its runner, for the standalone commands and tests that drive one runner: closing the
    returned runner closes the host too. The arguments are :func:`build_host_process`'s."""
    return _build_host_process(
        config,
        events=events,
        bundle=bundle,
        environ=environ,
        trace_exporter=trace_exporter,
        platform_tracing=platform_tracing,
        received_telemetry=received_telemetry,
        identity=identity,
        runner_id=runner_id,
        trace_platform=False,
        closes_host=True,
    ).runners[0]


def add_runner(
    config: RunnerConfig,
    host: HostProcess,
    *,
    runner_id: str,
    bundle: BundleSnapshot | None = None,
    environ: Mapping[str, str] | None = None,
    trace_exporter: ITraceExporter | None = None,
) -> RunnerProcess:
    """Host one more runner on ``host``, bound to ``runner_id``. The store's newest identity row names one runner
    only, so a second runner on a host is bound by the id it is given."""
    runner = _build_runner(
        config,
        host,
        bundle=bundle,
        environ=environ,
        trace_exporter=trace_exporter,
        identity=None,
        runner_id=runner_id,
        closes_host=False,
    )
    host.runners = (*host.runners, runner)
    return runner


def _build_runner(
    config: RunnerConfig,
    host: HostProcess,
    *,
    bundle: BundleSnapshot | None,
    environ: Mapping[str, str] | None,
    trace_exporter: ITraceExporter | None,
    identity: RunnerIdentityHolder | None,
    runner_id: str | None,
    closes_host: bool,
) -> RunnerProcess:
    """One runner over ``host``'s engine, bound to ``runner_id`` or else the runner the store's identity row names."""
    identity = identity or RunnerIdentityHolder()
    platform_tracing = host.platform_tracing
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")
    try:
        # Bound and seeded only from a store at the code's schema head: a store mid-migration fails readiness, not
        # boot, and binds when registration adopts an id. Rows naming no runner are stamped by the command that starts
        # the process (`stamp_unowned_rows`), before it serves or ticks.
        ready = host.readiness.evaluate().ready
        connections = RunnerStoreConnections(host.engine, RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
        if runner_id is not None:
            connections.bind(runner_id)
        elif ready:
            connections.bind_to_stored()
        stores = _build_stores(connections)
        clock = host.clock
        registered = stores.identity.runner_identity() if ready else None
        if registered is not None:
            identity.hold(registered)
        runner_environ = os.environ if environ is None else environ
        telemetry_enabled = config.tracing.harness_telemetry and platform_tracing.enabled
        plan = combined_telemetry_plan(
            config.harness_settings,
            bundle=bundle,
            harness_telemetry_enabled=telemetry_enabled,
            runner_environ=runner_environ,
        )
        harnesses = build_production_harness_registry(
            config.harness_settings,
            executor=executor,
            process=host.process,
            bundle=bundle,
            harness_telemetry_enabled=telemetry_enabled,
            runner_environ=runner_environ,
        )
        default_id = default_harness_id(harnesses)
        if default_id is not None:
            harnesses.transcript_source(default_id)
        health = HarnessHealthCache(
            clock=clock,
            probes=build_production_harness_health_probes(
                config.harness_settings, spawn_root=host.provider.spawn_root()
            ),
            corpus=CommittedCorpus(),
            selftest_results=stores.selftest_results,
            configured_tiers=configured_tiers(config.harness_sections),
        )
        tracing = TracingSettings.of(runner_environ)
        exporter = (trace_exporter or _otlp_exporter(environ)) if tracing.enabled() else None
        trace_sweep = (
            LeaseTraceSweep(
                leases=stores.lease_traces,
                outbound=stores.outbound,
                exporter=exporter,
                identity=identity,
                clock=clock,
                config=config.tracing,
            )
            if exporter is not None
            else None
        )
        trace_replay = LeaseTraceReplay(
            leases=stores.lease_traces, exporter=exporter, identity=identity, clock=clock, config=config.tracing
        )
        return RunnerProcess(
            host=host,
            connections=connections,
            stores=stores,
            host_stores=_build_host_stores(connections, clock),
            harnesses=harnesses,
            health=health,
            executor=executor,
            trace_settings=tracing,
            trace_sweep=trace_sweep,
            trace_replay=trace_replay,
            identity=identity,
            identity_verdict=IdentityVerdictHolder(reset_command(config.root)),
            hub_token=held_hub_token(config),
            harness_telemetry=plan,
            closes_host=closes_host,
        )
    except BaseException:
        executor.shutdown(wait=True)
        raise


def _otlp_exporter(environ: Mapping[str, str] | None) -> OtlpTraceExporter:
    return OtlpTraceExporter(
        resource=resource_attributes(os.environ if environ is None else environ, __version__),
        scope=INSTRUMENTATION_SCOPE,
        scope_version=INSTRUMENTATION_SCOPE_VERSION,
    )


def build_stores(engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str | None) -> RunnerStores:
    """Construct and wire every extracted concept-store adapter over a migrated engine, bound to ``runner_id`` (or
    unbound, ``None``)."""
    return _build_stores(RunnerStoreConnections(engine, errors, runner_id))


def _build_stores(connections: RunnerStoreConnections) -> RunnerStores:
    return RunnerStores(
        lease_record=LeaseRecordStore(connections),
        session=LeaseSessionStore(connections),
        liveness=LeaseLivenessStore(connections),
        resume_intent=LeaseResumeIntentStore(connections),
        environments=EnvironmentStore(connections),
        transcript_ledger=TranscriptLedgerStore(connections),
        tokens=TokenStore(connections),
        outbound=OutboundStore(connections),
        overload=OverloadStore(connections),
        asks=AskStore(connections),
        pause=PauseStore(connections),
        identity=RunnerIdentityStore(connections, stamp_unowned_rows_on),
        served_projects=ServedProjectsStore(connections),
        takeover=TakeoverStore(connections),
        requeue=RequeueStore(connections),
        escalations=EscalationStore(connections),
        usage=UsageStore(connections),
        attachments=AttachmentStore(connections),
        git_commit_declarations=GitCommitDeclarationStore(connections),
        checks=CheckStore(connections),
        elicitations=ElicitationStore(connections),
        invocation_boundaries=InvocationBoundaryStore(connections),
        selftest_results=SelfTestResultStore(connections),
        lease_traces=LeaseTraceFactsStore(connections),
    )


def build_read_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> RunnerReadStores:
    """The read-only bundle a controller-facing collaborator takes — narrows build_stores's
    bundle over the same adapter instances, never a second one — bound to the runner the store's identity row names,
    or unbound (reading as empty) when it holds none."""
    connections = RunnerStoreConnections(engine, errors)
    connections.bind_to_stored()
    return RunnerReadStores.of(_build_stores(connections))


def stamp_unowned_rows(runner: RunnerProcess) -> int:
    """The start-up stamp: when the runner's store is bound, stamp the bound runner's id on every runner-scoped row that
    names none, through the host's own stores; answer how many were stamped. Run by ``blizzard runner host`` and
    ``blizzard runner tick`` once the graph is built and before either serves or ticks. A store not yet bound stamps
    nothing — registration's first adoption stamps them in the identity write's commit."""
    bound = runner.connections.runner_id
    return runner.host.host_stores.unowned_row_stamp.stamp(bound) if bound is not None else 0


def build_host_stores(
    engine: Engine, *, errors: RunnerStoreErrorFactory, clock: IClock, runner_id: str | None = None
) -> HostStores:
    """The host-scoped stores over a migrated engine, for a collaborator outside a runner process — the invariant
    checker's audit. Host statements carry no runner equality; ``runner_id`` binds the connections only for the
    cross-kind write's runner-scoped outbound report, and is left unbound otherwise."""
    return _build_host_stores(RunnerStoreConnections(engine, errors, runner_id), clock)


def build_host_read_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> HostReadStores:
    """The read-only host-store bundle a short-lived CLI verb takes."""
    return HostReadStores.of(_build_host_stores(RunnerStoreConnections(engine, errors), SystemClock()))


def _build_host_stores(connections: RunnerStoreConnections, clock: IClock) -> HostStores:
    return HostStores(
        workspace_prompt=WorkspacePromptStore(connections),
        graph_artifacts=GraphArtifactStore(connections),
        jti_cache=JtiCacheRepository(connections, clock),
        daemon_liveness=DaemonLivenessStore(connections),
        external_usage=ExternalUsageStore(connections),
        unowned_row_stamp=UnownedRowStamp(connections),
        runner_audit=RunnerAudit(connections),
    )
