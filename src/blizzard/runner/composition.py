"""The runner host, runner process and store composition root.

The only module under ``src/`` that names a concrete ``runner/store/internal/`` adapter,
asserted by
``tests/test_layering.py::test_composition_is_the_only_module_naming_a_concrete_runner_store_adapter``
— every other collaborator takes a Protocol seam or the
:class:`~blizzard.runner.stores.RunnerStores` bundle this builds."""

from __future__ import annotations

import contextlib
import functools
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import httpx
from sqlalchemy import Engine

from blizzard import __version__
from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import (
    DisabledPlatformTracing,
    IPlatformTracing,
    build_platform_tracing,
)
from blizzard.foundation.roles import collaborator
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.internal.store_status_reader import SqlAlchemyStoreStatusReader
from blizzard.foundation.store.readiness import Readiness, ReadinessService
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.internal.otlp import OtlpTraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.config import HostWorkspace, RunnerConfig, SubscriptionDeclaration
from blizzard.runner.config_table import ConfigError
from blizzard.runner.environments.factory import build_workspace_provider
from blizzard.runner.environments.workspaces import IWorkspaces, WorkspaceEntry, WorkspaceRegistry
from blizzard.runner.events.broker import EventBroker, RunnerEventPublisher
from blizzard.runner.events.publisher import IRunnerEventPublisher
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
from blizzard.runner.host_config import BuiltRunner, HaltedRunner, HostConfig, ResolvedToken
from blizzard.runner.host_layout import HostFileEditor, HostLayout
from blizzard.runner.hub.client import HostReport, HostReportedWorkspace, IHostHubClient
from blizzard.runner.hub.client import SubscriptionDeclaration as HubSubscriptionDeclaration
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.host_identity import HostIdentity, HostIdentityHolder
from blizzard.runner.hub.host_registration import HostRegistration, HostRegistrationHolder, ICurrentHostRegistration
from blizzard.runner.hub.identity import (
    ICurrentRunnerIdentity,
    IdentityVerdictHolder,
    IWriteRetiredRunnerIdentity,
    IWriteRunnerIdentityRepository,
    RunnerIdentityHolder,
)
from blizzard.runner.hub.internal.http_hub import HttpHostHubClient
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.runner_transfer import HostIngest, IRunnerRowRemoval, IRunnerTransfer
from blizzard.runner.hub.served_projects import ServedProjectsAnswer
from blizzard.runner.ingest import HeldBinding, IIngestStores, ITokenFiles, RunnerIngest, RunnerStoreView
from blizzard.runner.lifecycle.host_lane_drain import HostLaneDrain
from blizzard.runner.lifecycle.host_tick import HostTick
from blizzard.runner.listeners import Listeners
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.runtime import IRunnerRowMoves, migration_runner
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
from blizzard.runner.store.internal.host_identity_store import HostIdentityStore
from blizzard.runner.store.internal.host_ingest_store import HostIngestStore
from blizzard.runner.store.internal.host_jti_cache_store import JtiCacheRepository
from blizzard.runner.store.internal.host_lane_store import HostLaneStore
from blizzard.runner.store.internal.host_runner_audit import RunnerAudit
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
from blizzard.runner.store.internal.runner_transfer_store import RunnerRowRemovalStore, RunnerTransferStore
from blizzard.runner.store.internal.selftest_result_store import SelfTestResultStore
from blizzard.runner.store.internal.takeover_store import TakeoverStore
from blizzard.runner.store.internal.token_store import TokenStore
from blizzard.runner.store.internal.transcript_ledger_store import TranscriptLedgerStore
from blizzard.runner.store.internal.usage_store import UsageStore
from blizzard.runner.store.runner_guard import install_runner_guard
from blizzard.runner.stores import HostReadStores, HostStores, IRunnerAudit, RunnerReadStores, RunnerStores
from blizzard.runner.subscriptions.internal.credential_renewer_factory import select_renewer
from blizzard.runner.subscriptions.internal.subprocess_one_shot_process import SubprocessOneShotProcess
from blizzard.runner.subscriptions.internal.subscription_sampler_factory import select_sampler
from blizzard.runner.subscriptions.internal.usage_http_client import LazyUsageHttpClient
from blizzard.runner.tracing.attributes import (
    INSTRUMENTATION_SCOPE,
    INSTRUMENTATION_SCOPE_VERSION,
    resource_attributes,
)
from blizzard.runner.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
    CurrentRunnerIdentity,
    identity_stamp,
    runner_context_stamp,
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
from blizzard.runner.usage.external_usage_sample import ExternalUsageSamplePass, SampledSubscription
from blizzard.runner.usage.repository import IWriteHostLaneRepository


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
    #: Every workspace the host declares, each with its own provider, spawn root, standing prompt and lock.
    workspaces: IWorkspaces
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
    #: The pass the host's own thread runs: usage sampling and credential renewal.
    host_tick: HostTick
    #: The one HTTP client every declared subscription's sampler shares, built on first use and closed with the host.
    usage_http_client: LazyUsageHttpClient
    #: Platform spans — disabled unless a handle is supplied; every collaborator opens spans through it.
    platform_tracing: IPlatformTracing = field(default_factory=DisabledPlatformTracing)
    #: Where admitted metrics and logs leave — disabled unless a handle is supplied.
    received_telemetry: IReceivedTelemetryExport = field(default_factory=DisabledReceivedTelemetryExport)
    runners: tuple[RunnerProcess, ...] = ()
    #: The runners the host file declares that were left unbuilt by their own errors; the host serves without them.
    halted: tuple[HaltedRunner, ...] = ()
    #: The subscriptions the host declares, whichever runners spend them.
    subscriptions: tuple[SubscriptionDeclaration, ...] = ()
    #: Who this host is at its hub; seeded from the store at boot, and handed the identity once it is first recorded.
    host_identity: HostIdentityHolder = field(default_factory=HostIdentityHolder)
    #: The host registration step's latest outcome — unregistered on a host with no host token.
    host_registration: HostRegistrationHolder = field(
        default_factory=lambda: HostRegistrationHolder.for_host(registered=False)
    )
    #: The client the host's own hub calls go through, under its host token — ``None`` on a host with none.
    host_hub_http_client: httpx.Client | None = None

    def registered(self) -> bool:
        """Whether the host holds a host token: it reports itself, and its roster with it, so its runners declare
        no subscriptions of their own."""
        return self.host_registration.current().registered()

    def spenders_of(self, slug: str | None, *, excluding: str) -> tuple[RunnerProcess, ...]:
        """The runners of this host, other than ``excluding``, that spend ``slug`` — none for a runner that spends no
        subscription."""
        if slug is None:
            return ()
        return tuple(
            runner
            for runner in self.runners
            if runner.runner_id != excluding and any(declared.slug == slug for declared in runner.config.subscriptions)
        )

    def close(self) -> None:
        with contextlib.ExitStack() as closing:
            closing.callback(self.platform_tracing.shutdown, PLATFORM_TRACING_SHUTDOWN_SECONDS)
            closing.callback(self.received_telemetry.shutdown, PLATFORM_TRACING_SHUTDOWN_SECONDS)
            closing.callback(self.engine.dispose)
            closing.callback(self.usage_http_client.close)
            if self.host_hub_http_client is not None:
                closing.callback(self.host_hub_http_client.close)
            for runner in self.runners:
                closing.callback(runner.close_runner)


@dataclass(frozen=True)
class RunnerProcess:
    """One runner's own graph over a :class:`HostProcess`: its bound stores, identity, harnesses and spawner.

    Close after the loop has stopped and resume marking has drained workers: the spawner thread is the kernel parent
    of children awaiting durable confirmation. The host's engine outlives it.
    """

    host: HostProcess
    #: The configuration this runner was built from.
    config: RunnerConfig
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
    #: Whether this runner has verified its token names the configured runner — unchecked from start.
    identity_verdict: IdentityVerdictHolder
    #: The token every runner->hub client presents — re-read from ``.env`` while the identity mismatches.
    hub_token: HubTokenHolder
    #: What the enabled bindings together do with each of a worker's telemetry signals, derived once at startup.
    harness_telemetry: HarnessTelemetryPlan = field(default_factory=HarnessTelemetryPlan)
    #: Whether closing this runner closes the host it was built over — the host-of-one a standalone build makes.
    closes_host: bool = False

    @property
    def runner_id(self) -> str:
        """The hub id this runner is configured with, which its stores are bound to."""
        return self.connections.bound_runner_id()

    @property
    def engine(self) -> Engine:
        return self.host.engine

    @property
    def workspaces(self) -> IWorkspaces:
        return self.host.workspaces

    @functools.cached_property
    def events(self) -> IRunnerEventPublisher | None:
        """The host's broker, stamping this runner's id on every runner-scoped event."""
        return RunnerEventPublisher.over(self.host.events, self.runner_id)

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


@dataclass(frozen=True)
class HostReadiness(ReadinessService):
    """The store's readiness with the host's halted runners named in its detail. ``ready`` keeps meaning the store's: a
    halted runner is the host's to report, not a reason it cannot serve."""

    halted: tuple[str, ...] = ()

    def evaluate(self) -> Readiness:
        readiness = super().evaluate()
        if not self.halted:
            return readiness
        detail = "; ".join(filter(None, (readiness.detail, *self.halted)))
        return replace(readiness, detail=detail)


def _pool_held_ids(audit: IRunnerAudit, workspace_id: str) -> Callable[[], list[str]]:
    """The environment ids any of the host's runners holds in ``workspace_id``'s pool, or under no workspace."""

    def held() -> list[str]:
        return list(dict.fromkeys(b.environment_id for b in audit.held_workspace_bindings(workspace_id)))

    return held


def build_workspaces(
    declared: Sequence[HostWorkspace], host_root: Path, *, audit: IRunnerAudit | None = None
) -> WorkspaceRegistry:
    """The host's workspace registry: one provider per declared workspace, each with its spawn root and its standing
    prompt resolved now — a missing prompt file or an unknown packaged sample is a start-up error. With ``audit``,
    each provider reads its own pool's held set host-wide."""
    entries = []
    for workspace in declared:
        held_ids = _pool_held_ids(audit, workspace.id) if audit is not None else None
        provider = build_workspace_provider(workspace.settings(host_root), held_ids=held_ids)
        entries.append(
            WorkspaceEntry(
                workspace.id, provider, spawn_root=provider.spawn_root(), prompt=workspace.resolved_prompt(host_root)
            )
        )
    return WorkspaceRegistry(entries)


def bound_spawn_roots(config: RunnerConfig, workspaces: IWorkspaces) -> tuple[str, ...]:
    """The distinct spawn roots of the workspaces ``config``'s runner binds — what its health probes and
    ambient-conflict checks run over."""
    return tuple(dict.fromkeys(workspaces.spawn_root(workspace.id) for workspace in config.bound_workspaces))


def _credential_renewal_pass(
    declarations: Sequence[SubscriptionDeclaration], host_stores: HostStores, clock: SystemClock
) -> CredentialRenewalPass | None:
    """Every subscription the host declares paired with its provider's renewer binding, sharing one
    one-shot subprocess seam; ``None`` when no provider binds one."""
    subprocess = SubprocessOneShotProcess()
    renewable = [
        RenewableSubscription(
            slug=declaration.slug, sample_interval_seconds=declaration.sample_interval_seconds, renewer=renewer
        )
        for declaration in declarations
        if (renewer := select_renewer(declaration, clock=clock, subprocess=subprocess)) is not None
    ]
    if not renewable:
        return None
    return CredentialRenewalPass(subscriptions=renewable, renewals=host_stores.external_usage, clock=clock)


_log = get_logger("blizzard.runner.composition")


class _HostAttemptSink:
    """Where the host's sampling pass records an attempt: a registered host's own lane — one holding a host token,
    whether or not its hub has answered yet — else, an unregistered host having none, the bound stores and event
    publisher of its first runner whose identity row exists, as a runner's own sample did: a runner-scoped row names a
    registered runner (``runner:rows-name-registered-runner``). With no such runner the attempt is not recorded, and
    the pass samples again on the next host tick, until a runner registers."""

    def __init__(
        self,
        host_lane: IWriteHostLaneRepository,
        runners: Callable[[], tuple[RunnerProcess, ...]],
        *,
        registration: ICurrentHostRegistration,
    ) -> None:
        self._host_lane = host_lane
        self._runners = runners
        self._registration = registration

    def record(
        self,
        *,
        slug: str,
        sampled_at: datetime,
        payload: str | None,
        report_kind: str,
        report_payload: str,
        miss_reason: str | None,
    ) -> None:
        if self._registration.current().registered():
            self._host_lane.record_host_external_usage_attempt(
                slug=slug,
                sampled_at=sampled_at,
                payload=payload,
                report_kind=report_kind,
                report_payload=report_payload,
                miss_reason=miss_reason,
            )
            return
        reporter = next((runner for runner in self._runners() if runner.identity.current() is not None), None)
        if reporter is None:
            _log.warning("external usage attempt not recorded: no runner of the host has registered", slug=slug)
            return
        seq = reporter.host_stores.external_usage.record_external_usage_attempt(
            slug=slug,
            sampled_at=sampled_at,
            payload=payload,
            report_kind=report_kind,
            report_payload=report_payload,
            miss_reason=miss_reason,
        )
        events = reporter.events
        if seq is not None and events is not None:
            events.publish_fact_changed(seq=seq, kind=report_kind, chunk_id=None, lease_id=None)


def _external_usage_sample_pass(
    declarations: Sequence[SubscriptionDeclaration],
    host_stores: HostStores,
    clock: SystemClock,
    http_client: LazyUsageHttpClient,
    runners: Callable[[], tuple[RunnerProcess, ...]],
    *,
    registration: ICurrentHostRegistration,
) -> ExternalUsageSamplePass | None:
    """Every subscription the host declares paired with its provider's sampler, sharing one HTTP client; ``None``
    when the host declares none. ``registration`` says whether the host holds a host token, so its attempts ride its
    own lane."""
    if not declarations:
        return None
    sampled = [
        SampledSubscription(
            slug=declaration.slug,
            name=declaration.name,
            sample_interval_seconds=declaration.sample_interval_seconds,
            sampler=select_sampler(declaration, clock=clock, http_client=http_client),
        )
        for declaration in declarations
    ]
    return ExternalUsageSamplePass(
        subscriptions=sampled,
        attempts=host_stores.external_usage,
        sink=_HostAttemptSink(host_stores.host_lane, runners, registration=registration),
        clock=clock,
    )


#: How long the runner's shutdown waits for buffered platform spans to leave.
PLATFORM_TRACING_SHUTDOWN_SECONDS = 3.0


def build_runner_platform_tracing(
    config: RunnerConfig, environ: Mapping[str, str] | None = None, *, identity: ICurrentRunnerIdentity | None = None
) -> IPlatformTracing:
    """The handle ``[tracing]`` and the OTLP environment decide. With ``identity``, every span it records carries
    the id and name ``identity`` answers as it starts, and none is exported before that runner's first
    registration; without, each span is stamped as the runner whose context opened it, and one opened outside any
    runner's — the host's own — carries no runner identity."""
    env = os.environ if environ is None else environ
    return build_platform_tracing(
        config.tracing,
        env,
        resource=resource_attributes(env, __version__),
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
        stamp=identity_stamp(identity) if identity is not None else runner_context_stamp(),
    )


def build_runner_received_telemetry(
    config: RunnerConfig, environ: Mapping[str, str] | None = None
) -> IReceivedTelemetryExport:
    """The handle Claude Code's received metrics and logs leave through: off unless ``[tracing]
    harness_telemetry`` and platform tracing are both on."""
    env = os.environ if environ is None else environ
    return build_received_telemetry_export(config.tracing, env, resource=resource_attributes(env, __version__))


def held_hub_token(config: RunnerConfig, token: ResolvedToken | None = None) -> HubTokenHolder:
    """The token the runner started with, re-read from its ``.env`` only when ``token`` says ``.env`` was where it came
    from. Without a resolution the token is held as it is."""
    file = HubTokenFile.of(config.root, config.token_env) if token is not None and token.rereads_dotenv() else None
    return HubTokenHolder(config.hub_token, file=file)


def build_host_process(
    host: HostConfig,
    runners: Sequence[BuiltRunner] = (),
    *,
    halted: Sequence[HaltedRunner] = (),
    events: EventBroker | None = None,
    bundles: Mapping[str, BundleSnapshot | None] | None = None,
    environ: Mapping[str, str] | None = None,
    trace_exporter: ITraceExporter | None = None,
    platform_tracing: IPlatformTracing | None = None,
    received_telemetry: IReceivedTelemetryExport | None = None,
    trace_platform: bool = False,
) -> HostProcess:
    """Construct the host graph from its file, and one runner over it for each of ``runners`` — each bound to its
    configured id before anything runs. ``halted`` are the runners left unbuilt; the host keeps them to report.
    Disposes partial resources on failure.

    ``environ`` decides whether tracing is enabled; ``trace_exporter`` replaces the OTLP binding an enabled sweep would
    otherwise build. ``trace_platform`` builds the platform-tracing and received-telemetry handles the config and
    environment decide, stamped from the host's runners; otherwise ``platform_tracing`` and ``received_telemetry`` are
    used as given. ``bundles`` are the harness-config snapshots each runner published, by runner id."""
    graph = _build_host_graph(
        runners[0].config if runners else host.host_view(),
        subscriptions=host.subscriptions,
        events=events,
        environ=environ,
        platform_tracing=platform_tracing,
        received_telemetry=received_telemetry,
        trace_platform=trace_platform,
        host_token=host.host_token.value,
    )
    try:
        for built in runners:
            graph.runners = (
                *graph.runners,
                _build_runner(
                    built.config,
                    graph,
                    bundle=(bundles or {}).get(built.id),
                    environ=environ,
                    trace_exporter=trace_exporter,
                    identity=None,
                    runner_id=built.id,
                    token=built.token,
                    closes_host=False,
                ),
            )
        graph.halted = tuple(halted)
        graph.readiness = HostReadiness(
            reader=graph.readiness.reader,
            expected_revision=graph.readiness.expected_revision,
            halted=tuple(runner.describe() for runner in halted),
        )
        return graph
    except BaseException:
        graph.close()
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
    token: ResolvedToken | None = None,
    host_token: str = "",
) -> RunnerProcess:
    """A host of one and its runner, for the standalone commands and tests that drive one runner: closing the
    returned runner closes the host too. The runner is bound to ``runner_id``, else to the id ``config`` carries;
    ``token`` is where its hub token resolved from, and ``host_token`` the host's own credential, so a runner of a
    registered host leaves its roster to the host. The other arguments are :func:`build_host_process`'s."""
    graph = _build_host_graph(
        config,
        subscriptions=config.subscriptions,
        events=events,
        environ=environ,
        platform_tracing=platform_tracing,
        received_telemetry=received_telemetry,
        trace_platform=False,
        host_token=host_token,
    )
    try:
        runner = _build_runner(
            config,
            graph,
            bundle=bundle,
            environ=environ,
            trace_exporter=trace_exporter,
            identity=identity,
            runner_id=runner_id or config.runner_id,
            token=token,
            closes_host=True,
        )
    except BaseException:
        graph.close()
        raise
    graph.runners = (runner,)
    return runner


def add_runner(
    config: RunnerConfig,
    host: HostProcess,
    *,
    runner_id: str,
    bundle: BundleSnapshot | None = None,
    environ: Mapping[str, str] | None = None,
    trace_exporter: ITraceExporter | None = None,
) -> RunnerProcess:
    """Host one more runner on ``host``, bound to ``runner_id``."""
    runner = _build_runner(
        config,
        host,
        bundle=bundle,
        environ=environ,
        trace_exporter=trace_exporter,
        identity=None,
        runner_id=runner_id,
        token=None,
        closes_host=False,
    )
    host.runners = (*host.runners, runner)
    return runner


def _build_host_graph(
    config: RunnerConfig,
    *,
    subscriptions: Sequence[SubscriptionDeclaration],
    events: EventBroker | None,
    environ: Mapping[str, str] | None,
    platform_tracing: IPlatformTracing | None,
    received_telemetry: IReceivedTelemetryExport | None,
    trace_platform: bool,
    host_token: str = "",
) -> HostProcess:
    """The host's own graph, with no runner on it yet. ``config`` carries the host's keys — listeners, tracing, the
    workspaces, its hub — and ``subscriptions`` are the host's declarations, which its credential renewal covers.
    ``host_token`` is the host's own credential; a host holding one is registered, and reports itself to its hub."""
    install_runner_guard()
    engine = create_engine_from_url(config.db_url)
    host_hub_http_client: httpx.Client | None = None
    try:
        built: list[HostProcess] = []

        def runners() -> tuple[RunnerProcess, ...]:
            return built[0].runners if built else ()

        if trace_platform:
            platform_tracing = build_runner_platform_tracing(config, environ)
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
        usage_http_client = LazyUsageHttpClient()
        span_limiter = SpanRateLimiter(clock)
        receiver_counter = ReceiverCounter()
        harness_span_counter = ReceiverCounter()
        metric_bounds = ReceiverBounds.fresh(clock)
        log_bounds = ReceiverBounds.fresh(clock)
        telemetry_names = declared_telemetry_names()
        workspaces = build_workspaces(config.workspaces, config.root, audit=host_stores.runner_audit)
        host_identity = HostIdentityHolder()
        # The one place a host's registration is derived: everything after reads it from the holder.
        host_registration = HostRegistrationHolder.for_host(registered=bool(host_token))
        host_hub: HttpHostHubClient | None = None
        if host_registration.current().registered():
            host_hub_http_client = httpx.Client(base_url=config.hub_url, timeout=_HOST_HUB_TIMEOUT_SECONDS)
            platform_tracing.instrument_client(host_hub_http_client)
            host_hub = HttpHostHubClient(host_hub_http_client, token=host_token)
        host = HostProcess(
            engine=engine,
            readiness=readiness,
            connections=connections,
            host_stores=host_stores,
            listeners=Listeners.of(config),
            workspaces=workspaces,
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
                identity=CurrentRunnerIdentity(),
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
            host_tick=HostTick(
                registration=(
                    HostRegistration(
                        client=host_hub,
                        report=functools.partial(host_report, config, workspaces, subscriptions),
                        identities=host_stores.host_identity,
                        identity=host_identity,
                        status=host_registration,
                        runners=runners,
                        clock=clock,
                    )
                    if host_hub is not None
                    else None
                ),
                external_usage_sample=_external_usage_sample_pass(
                    subscriptions, host_stores, clock, usage_http_client, runners, registration=host_registration
                ),
                lane_drain=(
                    HostLaneDrain(
                        client=host_hub, lane=host_stores.host_lane, registration=host_registration, clock=clock
                    )
                    if host_hub is not None
                    else None
                ),
                credential_renewal=_credential_renewal_pass(subscriptions, host_stores, clock),
            ),
            usage_http_client=usage_http_client,
            subscriptions=tuple(subscriptions),
            platform_tracing=platform_tracing,
            received_telemetry=received_telemetry,
            host_identity=host_identity,
            host_registration=host_registration,
            host_hub_http_client=host_hub_http_client,
        )
        # Seeded only from a store at the code's schema head: a store mid-migration fails readiness, not boot.
        if readiness.evaluate().ready and (recorded := host_stores.host_identity.host_identity()) is not None:
            host.host_identity.hold(recorded)
        built.append(host)
        return host
    except BaseException:
        if host_hub_http_client is not None:
            host_hub_http_client.close()
        engine.dispose()
        raise


#: How long one of the host's own hub calls waits — its report bounds the start-up wait before the runners tick.
_HOST_HUB_TIMEOUT_SECONDS = 10.0


def host_report(
    config: RunnerConfig, workspaces: IWorkspaces, subscriptions: Sequence[SubscriptionDeclaration]
) -> HostReport:
    """What the host reports about itself: its first public URL, the panel's sign-in callback URIs, each workspace
    with the environments it holds, and the subscriptions it declares."""
    reported = []
    for workspace in config.workspaces:
        provider = workspaces.provider(workspace.id)
        reported.append(
            HostReportedWorkspace(
                workspace_id=workspace.id, env_capacity=provider.capacity() if provider is not None else None
            )
        )
    return HostReport(
        url=config.public_url or None,
        redirect_uris=config.redirect_uris,
        workspaces=tuple(reported),
        subscriptions=tuple(
            HubSubscriptionDeclaration(slug=declared.slug, name=declared.name, provider=declared.provider)
            for declared in subscriptions
        ),
    )


def _identity_remedy(config: RunnerConfig, runner_id: str) -> str:
    """What restores a runner whose token resolves to another runner than the one configured."""
    return (
        f"set {config.token_env} to the token of runner {runner_id}, "
        f"or correct `[[runner]] id` in {HostLayout(config.root).config_path}"
    )


def _build_runner(
    config: RunnerConfig,
    host: HostProcess,
    *,
    bundle: BundleSnapshot | None,
    environ: Mapping[str, str] | None,
    trace_exporter: ITraceExporter | None,
    identity: RunnerIdentityHolder | None,
    runner_id: str | None,
    token: ResolvedToken | None,
    closes_host: bool,
) -> RunnerProcess:
    """One runner over ``host``'s engine, its stores bound to ``runner_id`` before anything reads or writes them."""
    if runner_id is None:
        raise ConfigError(f"runner {config.name} has no id: bind it to the hub-minted id of its [[runner]] entry")
    identity = identity or RunnerIdentityHolder()
    platform_tracing = host.platform_tracing
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")
    try:
        # Seeded only from a store at the code's schema head: a store mid-migration fails readiness, not boot.
        ready = host.readiness.evaluate().ready
        connections = RunnerStoreConnections(host.engine, RunnerStoreErrorFactory(get_logger("blizzard.runner.store")))
        connections.bind(runner_id)
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
                config.harness_settings, spawn_roots=bound_spawn_roots(config, host.workspaces)
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
            config=config,
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
            identity_verdict=IdentityVerdictHolder(_identity_remedy(config, runner_id)),
            hub_token=held_hub_token(config, token),
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
        identity=RunnerIdentityStore(connections),
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


def build_identity_writer(
    engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str
) -> IWriteRunnerIdentityRepository:
    """The identity row's writer, bound to ``runner_id``, for ``init`` to record the runner it joined."""
    return build_stores(engine, errors=errors, runner_id=runner_id).identity


def build_identity_forgetter(
    engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str
) -> IWriteRetiredRunnerIdentity:
    """The identity row's forgetter, bound to ``runner_id``, for ``runner retire`` to forget the runner it retired."""
    return RunnerIdentityStore(RunnerStoreConnections(engine, errors, runner_id))


def build_read_stores(engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str | None) -> RunnerReadStores:
    """The read-only bundle a controller-facing collaborator takes — narrows build_stores's
    bundle over the same adapter instances, never a second one — bound to the configured ``runner_id``, or unbound
    (reading as empty) for a runner that has none yet."""
    return RunnerReadStores.of(build_stores(engine, errors=errors, runner_id=runner_id))


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
        host_lane=HostLaneStore(connections),
        host_identity=HostIdentityStore(connections),
        host_ingests=HostIngestStore(connections),
        runner_audit=RunnerAudit(connections),
    )


def build_runner_transfer(
    source: Engine, destination: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str, clock: IClock
) -> IRunnerTransfer:
    """The copy of ``runner_id`` from the migrated ``source`` store into ``destination``: both stores' connections bound
    to it, and the destination's host member running the host half on the copy's transaction."""
    into = RunnerStoreConnections(destination, errors, runner_id)
    return RunnerTransferStore(
        RunnerStoreConnections(source, errors, runner_id), into, _build_host_stores(into, clock).host_ingests
    )


def build_runner_removal(engine: Engine, *, errors: RunnerStoreErrorFactory, runner_id: str) -> IRunnerRowRemoval:
    """The removal of ``runner_id``'s rows from the store ``engine`` opens, its connections bound to it."""
    return RunnerRowRemovalStore(RunnerStoreConnections(engine, errors, runner_id))


@dataclass(frozen=True)
class RunnerStoreFiles:
    """The offline verbs' reach into store files by URL — each call opens its engines and disposes of them before it
    returns, so a verb holds no store open between its steps."""

    errors: RunnerStoreErrorFactory
    clock: IClock

    def copy_runner(
        self,
        source_url: str,
        destination_url: str,
        runner_id: str,
        *,
        workspace_ids: Mapping[str, str] | None = None,
        bound_workspaces: Sequence[str] = (),
        ingest: HostIngest | None = None,
    ) -> None:
        with _engine(source_url) as source, _engine(destination_url) as destination:
            build_runner_transfer(
                source, destination, errors=self.errors, runner_id=runner_id, clock=self.clock
            ).copy_runner(workspace_ids=workspace_ids, bound_workspaces=bound_workspaces, ingest=ingest)

    def remove_runner_rows(self, url: str, runner_id: str, *, keep_counters: bool) -> None:
        with _engine(url) as engine:
            build_runner_removal(engine, errors=self.errors, runner_id=runner_id).remove_runner_rows(
                keep_counters=keep_counters
            )

    def runner_view(self, url: str, runner_id: str) -> RunnerStoreView:
        with _engine(url) as engine:
            runner = build_read_stores(engine, errors=self.errors, runner_id=runner_id)
            host = build_host_stores(engine, errors=self.errors, clock=self.clock)
            return RunnerStoreView(
                named_runners=host.runner_audit.named_runners(),
                identity=runner.identity.runner_identity(),
                open_elicitation_lease_ids=frozenset(runner.elicitations.in_flight_elicitation_lease_ids()),
                open_takeover_chunk_ids=frozenset(runner.takeover.open_takeover_chunk_ids()),
                held_bindings=tuple(runner.environments.held_bindings()),
                last_alive_at=host.daemon_liveness.last_daemon_liveness(),
                served_projects=_served_slugs(runner.served_projects.served_projects_answer()),
            )

    def named_runners(self, url: str) -> tuple[str, ...]:
        with _engine(url) as engine:
            return build_host_stores(engine, errors=self.errors, clock=self.clock).runner_audit.named_runners()

    def host_identity(self, url: str) -> HostIdentity | None:
        with _engine(url) as engine:
            return build_host_read_stores(engine, errors=self.errors).host_identity.host_identity()

    def held_bindings(self, url: str) -> tuple[HeldBinding, ...]:
        with _engine(url) as engine:
            named = build_host_stores(engine, errors=self.errors, clock=self.clock).runner_audit.named_runners()
            return tuple(
                HeldBinding(runner_id, binding)
                for runner_id in named
                for binding in build_read_stores(
                    engine, errors=self.errors, runner_id=runner_id
                ).environments.held_bindings()
            )

    def latest_host_ingest(self, url: str, runner_id: str) -> HostIngest | None:
        with _engine(url) as engine:
            return build_host_read_stores(engine, errors=self.errors).host_ingests.latest_host_ingest(runner_id)

    def record_host_identity(self, url: str, identity: HostIdentity) -> None:
        with _engine(url) as engine:
            build_host_stores(engine, errors=self.errors, clock=self.clock).host_identity.record_host_identity(identity)


def _served_slugs(answer: ServedProjectsAnswer | None) -> tuple[str, ...]:
    if answer is None:
        return ()
    return tuple(p.slug or p.entry for p in answer.projects if p.resolved())


def build_runner_store_files(clock: IClock | None = None) -> RunnerStoreFiles:
    """The store-file seams an offline verb moves a runner's rows through."""
    return RunnerStoreFiles(RunnerStoreErrorFactory(get_logger("blizzard.runner.store")), clock or SystemClock())


@contextlib.contextmanager
def _engine(url: str) -> Iterator[Engine]:
    engine = create_engine_from_url(url)
    try:
        yield engine
    finally:
        engine.dispose()


def _conforms_runner_row_moves(x: RunnerStoreFiles) -> IRunnerRowMoves:
    return x


def _conforms_ingest_stores(x: RunnerStoreFiles) -> IIngestStores:
    return x


@collaborator
@dataclass(frozen=True)
class DotenvTokenFiles:
    """Runner tokens in host directories' ``.env`` files."""

    def held(self, root: Path, token_env: str) -> str:
        return HubTokenFile.of(root, token_env).held()

    def write(self, root: Path, token_env: str, token: str) -> None:
        HubTokenFile.of(root, token_env).write(token)

    def remove(self, root: Path, token_env: str) -> None:
        HubTokenFile.of(root, token_env).remove()


def _conforms_token_files(x: DotenvTokenFiles) -> ITokenFiles:
    return x


def build_runner_ingest(
    host: HostConfig, hub: IHostHubClient, *, holder: Callable[[Path], str | None], clock: IClock | None = None
) -> RunnerIngest:
    """The ingest into the host ``host``, reaching its hub through ``hub`` and probing sockets with ``holder``."""
    resolved = clock or SystemClock()
    return RunnerIngest(
        host=host,
        stores=build_runner_store_files(resolved),
        host_file=HostFileEditor(),
        tokens=DotenvTokenFiles(),
        hub=hub,
        holder=holder,
        clock=resolved,
    )
