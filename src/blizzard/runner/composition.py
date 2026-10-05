"""The runner process and store composition root.

The only module under ``src/`` that names a concrete ``runner/store/internal/`` adapter,
asserted by
``tests/test_layering.py::test_composition_is_the_only_module_naming_a_concrete_runner_store_adapter``
— every other collaborator takes a Protocol seam or the
:class:`~blizzard.runner.stores.RunnerStores` bundle this builds.
Mirrors :func:`blizzard.hub.composition.build_services`."""

from __future__ import annotations

import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from sqlalchemy import Engine

from blizzard import __version__
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.platform_tracing.handle import (
    DisabledPlatformTracing,
    IPlatformTracing,
    build_platform_tracing,
)
from blizzard.foundation.platform_tracing.received_export import (
    DisabledReceivedTelemetryExport,
    IReceivedTelemetryExport,
    build_received_telemetry_export,
)
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.internal.otlp import OtlpTraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.config import RunnerConfig
from blizzard.runner.environments.factory import build_workspace_provider
from blizzard.runner.environments.provider import IWorkspaceProvider
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.capability_snapshot import default_harness_id
from blizzard.runner.harness.claude_code.telemetry_plan import plan_harness_telemetry
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.harness.wiring import (
    build_production_harness_health_probes,
    build_production_harness_registry,
    claude_code_section,
    configured_tiers,
)
from blizzard.runner.process.probe import LinuxProcessProbe
from blizzard.runner.store.errors import RunnerStoreConnections, RunnerStoreErrorFactory
from blizzard.runner.store.internal.ask_store import AskStore
from blizzard.runner.store.internal.attachment_store import AttachmentStore
from blizzard.runner.store.internal.check_store import CheckStore
from blizzard.runner.store.internal.elicitation_store import ElicitationStore
from blizzard.runner.store.internal.environment_store import EnvironmentStore
from blizzard.runner.store.internal.escalation_store import EscalationStore
from blizzard.runner.store.internal.git_commit_declaration_store import GitCommitDeclarationStore
from blizzard.runner.store.internal.graph_artifact_store import GraphArtifactStore
from blizzard.runner.store.internal.invocation_boundary_store import InvocationBoundaryStore
from blizzard.runner.store.internal.lease_liveness_store import LeaseLivenessStore
from blizzard.runner.store.internal.lease_record_store import LeaseRecordStore
from blizzard.runner.store.internal.lease_resume_intent_store import LeaseResumeIntentStore
from blizzard.runner.store.internal.lease_session_store import LeaseSessionStore
from blizzard.runner.store.internal.lease_trace_facts_store import LeaseTraceFactsStore
from blizzard.runner.store.internal.outbound_store import OutboundStore
from blizzard.runner.store.internal.overload_store import OverloadStore
from blizzard.runner.store.internal.pause_store import PauseStore
from blizzard.runner.store.internal.requeue_store import RequeueStore
from blizzard.runner.store.internal.selftest_result_store import SelfTestResultStore
from blizzard.runner.store.internal.takeover_store import TakeoverStore
from blizzard.runner.store.internal.token_store import TokenStore
from blizzard.runner.store.internal.transcript_ledger_store import TranscriptLedgerStore
from blizzard.runner.store.internal.usage_store import UsageStore
from blizzard.runner.store.internal.workspace_prompt_store import WorkspacePromptStore
from blizzard.runner.stores import RunnerReadStores, RunnerStores
from blizzard.runner.subscriptions.internal.credential_renewer_factory import select_renewer
from blizzard.runner.subscriptions.internal.subprocess_one_shot_process import SubprocessOneShotProcess
from blizzard.runner.tracing.attributes import (
    INSTRUMENTATION_SCOPE,
    INSTRUMENTATION_SCOPE_VERSION,
    RUNNER_ID,
    resource_attributes,
)
from blizzard.runner.tracing.platform import (
    PLATFORM_INSTRUMENTATION_SCOPE,
    PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
)
from blizzard.runner.tracing.receiver_limits import ReceiverBounds, ReceiverCounter, SpanRateLimiter
from blizzard.runner.tracing.replay import LeaseTraceReplay
from blizzard.runner.tracing.sweep import LeaseTraceSweep
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass, RenewableSubscription


@dataclass(frozen=True)
class RunnerProcess:
    """One owner for the hosted app, loop and recovery hooks' shared collaborators.

    Close after the loop has stopped and resume marking has drained workers: the
    spawner thread is the kernel parent of children awaiting durable confirmation.
    """

    engine: Engine
    stores: RunnerStores
    connections: RunnerStoreConnections
    provider: IWorkspaceProvider
    harnesses: HarnessRegistry
    clock: SystemClock
    process: LinuxProcessProbe
    health: HarnessHealthCache
    events: EventBroker | None
    executor: ThreadPoolExecutor
    #: Whether OpenTelemetry's own variables enable lease tracing, read once for this process.
    trace_settings: TracingSettings
    #: The lease-trace sweep — ``None`` unless tracing is enabled. Only ``runner host`` drives it.
    trace_sweep: LeaseTraceSweep | None
    #: The operator's replay over the same assembly and exporter — dry-run only while tracing is off.
    trace_replay: LeaseTraceReplay
    #: The span receiver's per-lease rate bound and its received/dropped tally, one of each per process.
    span_limiter: SpanRateLimiter
    receiver_counter: ReceiverCounter
    #: Claude Code's spans alone, counted apart from the shared span tally above.
    claude_trace_counter: ReceiverCounter
    #: The metrics and logs receivers' own bound and tally, counted in data points and log records.
    metric_bounds: ReceiverBounds
    log_bounds: ReceiverBounds
    #: Platform spans — off unless the host passed a handle; every collaborator opens spans through it.
    platform_tracing: IPlatformTracing = field(default_factory=DisabledPlatformTracing)
    #: Where admitted metrics and logs leave — off unless the host passed a handle.
    received_telemetry: IReceivedTelemetryExport = field(default_factory=DisabledReceivedTelemetryExport)
    #: What the Claude Code binding does with each of a worker's telemetry signals, derived once at startup.
    harness_telemetry: HarnessTelemetryPlan = field(default_factory=HarnessTelemetryPlan)
    #: The credential-renewal pass, ``None`` when no provider binds a renewer; only ``runner host`` drives it.
    credential_renewal: CredentialRenewalPass | None = None

    def close(self) -> None:
        try:
            self.executor.shutdown(wait=True)
        finally:
            try:
                self.engine.dispose()
            finally:
                try:
                    self.received_telemetry.shutdown(PLATFORM_TRACING_SHUTDOWN_SECONDS)
                finally:
                    self.platform_tracing.shutdown(PLATFORM_TRACING_SHUTDOWN_SECONDS)


def _credential_renewal_pass(
    config: RunnerConfig, stores: RunnerStores, clock: SystemClock
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
    return CredentialRenewalPass(subscriptions=renewable, renewals=stores.usage, clock=clock)


#: How long the runner's shutdown waits for buffered platform spans to leave.
PLATFORM_TRACING_SHUTDOWN_SECONDS = 3.0


def build_runner_platform_tracing(config: RunnerConfig, environ: Mapping[str, str] | None = None) -> IPlatformTracing:
    """The handle ``[tracing]`` and the OTLP environment decide for this runner; every span it
    records carries the runner's id. Only the ``host`` daemon builds one."""
    env = os.environ if environ is None else environ
    return build_platform_tracing(
        config.tracing,
        env,
        resource=resource_attributes(env, __version__),
        scope=PLATFORM_INSTRUMENTATION_SCOPE,
        scope_version=PLATFORM_INSTRUMENTATION_SCOPE_VERSION,
        stamped={RUNNER_ID: config.runner_id},
    )


def build_runner_received_telemetry(
    config: RunnerConfig, environ: Mapping[str, str] | None = None
) -> IReceivedTelemetryExport:
    """The handle Claude Code's received metrics and logs leave through: off unless ``[tracing]
    harness_telemetry`` and platform tracing are both on."""
    env = os.environ if environ is None else environ
    return build_received_telemetry_export(config.tracing, env, resource=resource_attributes(env, __version__))


def build_runner_process(
    config: RunnerConfig,
    *,
    events: EventBroker | None = None,
    bundle: BundleSnapshot | None = None,
    environ: Mapping[str, str] | None = None,
    trace_exporter: ITraceExporter | None = None,
    platform_tracing: IPlatformTracing | None = None,
    received_telemetry: IReceivedTelemetryExport | None = None,
) -> RunnerProcess:
    """Construct the process-scoped graph; dispose partial resources on failure.

    ``environ`` (default ``os.environ``) decides whether tracing is enabled; ``trace_exporter``
    replaces the OTLP binding an enabled sweep would otherwise build. ``platform_tracing`` is off
    unless the daemon host passes a handle; the engine is instrumented through it."""
    platform_tracing = platform_tracing or DisabledPlatformTracing()
    engine = create_engine_from_url(config.db_url)
    platform_tracing.instrument_engine(engine)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="blizzard-spawner")
    try:
        stores, connections = build_stores_and_connections(
            engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.runner.store"))
        )
        clock = SystemClock()
        process = LinuxProcessProbe()
        provider = build_workspace_provider(
            config.workspace_settings, held_ids=stores.environments.held_environment_ids
        )
        plan = plan_harness_telemetry(
            claude_code_section(config.harness_sections),
            worker_env=config.worker_env,
            bundle=bundle,
            runner_environ=os.environ if environ is None else environ,
            enabled=config.tracing.harness_telemetry and platform_tracing.enabled,
        )
        harnesses = build_production_harness_registry(
            config.harness_settings, executor=executor, process=process, bundle=bundle, harness_telemetry=plan
        )
        default_id = default_harness_id(harnesses)
        if default_id is not None:
            harnesses.transcript_source(default_id)
        health = HarnessHealthCache(
            clock=clock,
            probes=build_production_harness_health_probes(config.harness_settings, spawn_root=provider.spawn_root()),
            selftest_results=stores.selftest_results,
            configured_tiers=configured_tiers(config.harness_sections),
        )
        tracing = TracingSettings.of(os.environ if environ is None else environ)
        exporter = (trace_exporter or _otlp_exporter(environ)) if tracing.enabled() else None
        trace_sweep = (
            LeaseTraceSweep(
                leases=stores.lease_traces,
                outbound=stores.outbound,
                exporter=exporter,
                clock=clock,
                config=config.tracing,
            )
            if exporter is not None
            else None
        )
        credential_renewal = _credential_renewal_pass(config, stores, clock)
        trace_replay = LeaseTraceReplay(leases=stores.lease_traces, exporter=exporter, config=config.tracing)
        return RunnerProcess(
            engine,
            stores,
            connections,
            provider,
            harnesses,
            clock,
            process,
            health,
            events,
            executor,
            trace_settings=tracing,
            trace_sweep=trace_sweep,
            trace_replay=trace_replay,
            span_limiter=SpanRateLimiter(clock),
            receiver_counter=ReceiverCounter(),
            claude_trace_counter=ReceiverCounter(),
            metric_bounds=ReceiverBounds.fresh(clock),
            log_bounds=ReceiverBounds.fresh(clock),
            platform_tracing=platform_tracing,
            received_telemetry=received_telemetry or DisabledReceivedTelemetryExport(),
            harness_telemetry=plan,
            credential_renewal=credential_renewal,
        )
    except BaseException:
        try:
            executor.shutdown(wait=True)
        finally:
            engine.dispose()
        raise


def _otlp_exporter(environ: Mapping[str, str] | None) -> OtlpTraceExporter:
    return OtlpTraceExporter(
        resource=resource_attributes(os.environ if environ is None else environ, __version__),
        scope=INSTRUMENTATION_SCOPE,
        scope_version=INSTRUMENTATION_SCOPE_VERSION,
    )


def build_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> RunnerStores:
    """Construct and wire every extracted concept-store adapter over a migrated engine."""
    return _build_stores(RunnerStoreConnections(engine, errors))


def build_stores_and_connections(
    engine: Engine, *, errors: RunnerStoreErrorFactory
) -> tuple[RunnerStores, RunnerStoreConnections]:
    """Build the store bundle and hand back the ``RunnerStoreConnections`` every adapter in it
    shares — for the one caller (``build_hosted_app``) that wires a second ``store/internal/``-
    style collaborator (``JtiCacheRepository``) over the same engine, so it reuses this instance
    instead of building its own."""
    connections = RunnerStoreConnections(engine, errors)
    return _build_stores(connections), connections


def _build_stores(connections: RunnerStoreConnections) -> RunnerStores:
    return RunnerStores(
        lease_record=LeaseRecordStore(connections),
        session=LeaseSessionStore(connections),
        liveness=LeaseLivenessStore(connections),
        resume_intent=LeaseResumeIntentStore(connections),
        environments=EnvironmentStore(connections),
        transcript_ledger=TranscriptLedgerStore(connections),
        tokens=TokenStore(connections),
        workspace_prompt=WorkspacePromptStore(connections),
        outbound=OutboundStore(connections),
        overload=OverloadStore(connections),
        asks=AskStore(connections),
        pause=PauseStore(connections),
        takeover=TakeoverStore(connections),
        requeue=RequeueStore(connections),
        escalations=EscalationStore(connections),
        usage=UsageStore(connections),
        attachments=AttachmentStore(connections),
        git_commit_declarations=GitCommitDeclarationStore(connections),
        checks=CheckStore(connections),
        graph_artifacts=GraphArtifactStore(connections),
        elicitations=ElicitationStore(connections),
        invocation_boundaries=InvocationBoundaryStore(connections),
        selftest_results=SelfTestResultStore(connections),
        lease_traces=LeaseTraceFactsStore(connections),
    )


def build_read_stores(engine: Engine, *, errors: RunnerStoreErrorFactory) -> RunnerReadStores:
    """The read-only bundle a controller-facing collaborator takes — narrows build_stores's
    bundle over the same adapter instances, never a second one."""
    return RunnerReadStores.of(build_stores(engine, errors=errors))
