"""The observability area's registrations: the transcript, analytics, env-timeline, annotation, hub event log and
invariant ports over the adapters it is handed, the services over them, and the trace and egress exports.

Each port aliases the one adapter the root hands in. The event-derivation and forge-status passes and each export are
one per process, reaching a tenant's half through the accessors the root supplies; each export's sweep is a ``T |
None`` decided at registration. The store-free graph declares each registration absent, from the same rosters."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Protocol, cast

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.foundation.trace_export.exporter import ITraceExporter
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.hub.domain.chunk.model import WorkRefLabel
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadWorkRefsCorpus
from blizzard.hub.domain.chunk.ports.work_sources import IWorkAnnotators
from blizzard.hub.domain.graph.model import IReadManyGraphs
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.observability.analytics.derivation import (
    EventDerivationReconciler,
    EventDerivationService,
    IReadDerivationCorpus,
    TenantEventDerivation,
    TenantEventDerivations,
)
from blizzard.hub.domain.observability.analytics.events import IReadTranscriptEvents, IWriteTranscriptEvents
from blizzard.hub.domain.observability.analytics.extraction import TurnEventExtractors, shipped_extractors
from blizzard.hub.domain.observability.analytics.operational import IReadOperationalAnalytics
from blizzard.hub.domain.observability.analytics.queries import IReadAnalyticsEventQueries
from blizzard.hub.domain.observability.egress.backfill import EgressBackfill
from blizzard.hub.domain.observability.egress.boot import (
    EgressBoot,
    IConfiguredEgressWriter,
    IEgressBackfillWriters,
    IEgressFreeSpace,
    LiveEgressConfig,
)
from blizzard.hub.domain.observability.egress.ports import IEgressWriter
from blizzard.hub.domain.observability.egress.repository import IReadEgress, IReadEgressEvents, IWriteEgressCursor
from blizzard.hub.domain.observability.egress.reset import EgressReset
from blizzard.hub.domain.observability.egress.status import EgressStatusReader
from blizzard.hub.domain.observability.egress.sweep import EgressSweep
from blizzard.hub.domain.observability.env_timeline import EnvTimelineReading, IReadEnvTimelineFacts
from blizzard.hub.domain.observability.forge_status import (
    FULL_PASS_FLOOR_INTERVALS,
    AnnotationReconciler,
    IReadAnnotatedSources,
    IWriteAnnotatedSources,
    LiveAnnotationInterval,
    TenantAnnotation,
    TenantAnnotations,
)
from blizzard.hub.domain.observability.hub_events import HubEventLogService, IReadHubEventLog, IWriteHubEventLog
from blizzard.hub.domain.observability.invariants import HubInvariantCheck, IHubInvariantChecker
from blizzard.hub.domain.observability.tracing.replay import TraceReplay
from blizzard.hub.domain.observability.tracing.repository import (
    IReadProjectLabels,
    IReadTraceStatus,
    IReadTraceSteps,
    IWriteTraceCursor,
    LiveTracingConfig,
    StepHydration,
    StepHydrations,
)
from blizzard.hub.domain.observability.tracing.status import TraceStatusReader
from blizzard.hub.domain.observability.tracing.sweep import TraceExportSweep
from blizzard.hub.domain.observability.transcripts import (
    IReadTranscriptSegments,
    IWriteTranscriptSegments,
    TranscriptCaps,
    TranscriptIngestService,
)
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry, RetiredRunnerGuard
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def turn_event_extractors() -> TurnEventExtractors:
    """The shipped extractor set this build derives transcript events with."""
    return TurnEventExtractors(shipped_extractors())


def transcript_ingest(
    store: IWriteTranscriptSegments,
    transactions: IHubTransactions,
    retired: RetiredRunnerGuard,
    clock: IClock,
    caps: TranscriptCaps,
) -> TranscriptIngestService:
    """The tenant's transcript ingest, adjudicating against ``caps`` when a request brings none of its own."""
    return TranscriptIngestService(store=store, transactions=transactions, retired=retired, clock=clock, caps=caps)


def event_derivation_service(
    events: IWriteTranscriptEvents,
    facts: IReadChunkFactsRepository,
    record: IReadChunkRecordRepository,
    clock: IClock,
    extractors: TurnEventExtractors,
) -> EventDerivationService:
    """The tenant's per-segment event derivation, at the extractor version this build ships."""
    return EventDerivationService(events=events, facts=facts, record=record, clock=clock, extractors=extractors)


def event_derivation_reconciler(
    corpus: IReadDerivationCorpus, derivations: TenantEventDerivations, clock: IClock
) -> EventDerivationReconciler:
    """The event-derivation pass, one per hub, handing each segment to its own tenant's half."""
    return EventDerivationReconciler(corpus=corpus, tenant=derivations, clock=clock)


def annotation_reconciler(
    corpus: IReadWorkRefsCorpus, annotations: TenantAnnotations, clock: IClock, interval: LiveAnnotationInterval
) -> AnnotationReconciler:
    """The forge-status pass, one per hub, over every open tenant, its full-pass floor a fixed number of live
    annotation intervals."""
    return AnnotationReconciler(
        corpus=corpus,
        tenants=annotations.every,
        tenant=annotations,
        clock=clock,
        full_pass_floor=lambda: timedelta(seconds=FULL_PASS_FLOOR_INTERVALS * interval.seconds()),
    )


#: The area's passes: the event derivation's, then the forge status's.
type PassRefs = tuple[Ref[EventDerivationReconciler], Ref[AnnotationReconciler]]


#: The tenant ports the area aliases onto the adapters it is handed.
_TENANT_PORTS: tuple[type[Any], ...] = (
    IReadTranscriptSegments,
    IWriteTranscriptSegments,
    IReadTranscriptEvents,
    IWriteTranscriptEvents,
    IReadAnalyticsEventQueries,
    IReadOperationalAnalytics,
    IReadEnvTimelineFacts,
    IReadAnnotatedSources,
    IWriteAnnotatedSources,
)

#: The tenant services registered by class.
_TENANT_SERVICES: tuple[type[Any], ...] = (TenantEventDerivation, TenantAnnotation, EnvTimelineReading)

#: The tenant services registered by explicit factory.
_TENANT_FACTORY_KEYS: tuple[type[Any], ...] = (TranscriptIngestService, EventDerivationService)

#: The process ports the area aliases onto the adapters it is handed.
_PROCESS_PORTS: tuple[type[Any], ...] = (IReadHubEventLog, IWriteHubEventLog, IHubInvariantChecker)

#: The process services, each one instance per hub.
_PROCESS_SERVICES: tuple[type[Any], ...] = (HubEventLogService, HubInvariantCheck)

#: The passes and the tenant accessors they reach each tenant through, each one instance per hub.
_PROCESS_PASSES: tuple[type[Any], ...] = (
    TenantEventDerivations,
    TenantAnnotations,
    EventDerivationReconciler,
    AnnotationReconciler,
)


def register(
    registry: Registry,
    *,
    segments: Ref[IWriteTranscriptSegments],
    events: Ref[IWriteTranscriptEvents],
    event_queries: Ref[IReadAnalyticsEventQueries],
    operational: Ref[IReadOperationalAnalytics],
    env_timeline: Ref[IReadEnvTimelineFacts],
    annotations: Ref[IWriteAnnotatedSources],
    hub_events: Ref[IWriteHubEventLog],
    invariants: Ref[IHubInvariantChecker],
) -> PassRefs:
    """Declare the area's ports over the adapters handed in, its tenant services once per tenant, and the hub event
    log, the invariant check, the shipped extractor set, and the event-derivation and forge-status passes with the
    accessors they reach each tenant through, once per process, handing back each pass's ``Ref``."""
    registry.alias(port(IReadTranscriptSegments), segments)
    registry.alias(port(IWriteTranscriptSegments), segments)
    registry.alias(port(IReadTranscriptEvents), events)
    registry.alias(port(IWriteTranscriptEvents), events)
    registry.alias(port(IReadAnalyticsEventQueries), event_queries)
    registry.alias(port(IReadOperationalAnalytics), operational)
    registry.alias(port(IReadEnvTimelineFacts), env_timeline)
    registry.alias(port(IReadAnnotatedSources), annotations)
    registry.alias(port(IWriteAnnotatedSources), annotations)
    registry.alias(port(IReadHubEventLog), hub_events)
    registry.alias(port(IWriteHubEventLog), hub_events)
    registry.alias(port(IHubInvariantChecker), invariants)
    registry.factory(turn_event_extractors, lifetime=Lifetime.PROCESS)
    registry.factory(transcript_ingest, lifetime=Lifetime.TENANT)
    registry.factory(event_derivation_service, lifetime=Lifetime.TENANT)
    for service in _TENANT_SERVICES:
        registry.service(service, lifetime=Lifetime.TENANT)
    for service in _PROCESS_SERVICES:
        registry.service(service, lifetime=Lifetime.PROCESS)
    registry.context(port(TenantEventDerivations), lifetime=Lifetime.PROCESS)
    registry.context(port(TenantAnnotations), lifetime=Lifetime.PROCESS)
    derivation = registry.factory(event_derivation_reconciler, lifetime=Lifetime.PROCESS)
    return derivation, registry.factory(annotation_reconciler, lifetime=Lifetime.PROCESS)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports, services, extractor set and passes absent, for the store-free graph. The live
    annotation interval is declared by its provider."""
    for missing in (*_TENANT_PORTS, *_TENANT_SERVICES, *_TENANT_FACTORY_KEYS):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    for missing in (*_PROCESS_PORTS, *_PROCESS_SERVICES, *_PROCESS_PASSES, TurnEventExtractors):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)


#: Why an export's sweep or writer is absent: the root booted it off.
EXPORT_OFF = "the export is off"


def step_hydration(
    graphs: IReadManyGraphs,
    names: IReadRunnerRegistry,
    projects: IReadProjectLabels,
    label: WorkRefLabel,
    sources: IWorkAnnotators,
) -> StepHydration:
    """The tenant's reads a chunk's step facts are completed through."""
    return StepHydration(graphs=graphs, names=names, projects=projects, label=label, source_label=sources.source_label)


def trace_export_sweep(
    steps: IWriteTraceCursor,
    exporter: ITraceExporter,
    events: HubEventLogService,
    clock: IClock,
    config: LiveTracingConfig,
) -> TraceExportSweep:
    """The fleet-trace export sweep over the exporter the root lends, reading the live ``[tracing]`` config."""
    return TraceExportSweep(steps=steps, exporter=exporter, events=events, clock=clock, config=config.current)


def trace_replay(
    steps: IReadTraceSteps, exporter: ITraceExporter | None, clock: IClock, config: LiveTracingConfig
) -> TraceReplay:
    """Tells a past window again over the sweep's own exporter, or none with tracing off."""
    return TraceReplay(steps=steps, exporter=exporter, clock=clock, config=config.current)


def trace_status(
    settings: TracingSettings,
    status: IReadTraceStatus,
    steps: IReadTraceSteps,
    clock: IClock,
    config: LiveTracingConfig,
) -> TraceStatusReader:
    """The operator's read of fleet tracing, answering with tracing off too."""
    return TraceStatusReader(
        settings=settings,
        status=status,
        steps=steps,
        clock=clock,
        replay_max_window=lambda: config.current().replay_max_window,
    )


def egress_status(
    boot: EgressBoot,
    config: LiveEgressConfig,
    egress: IReadEgress,
    event_reads: IReadEgressEvents,
    steps: IReadTraceSteps,
    clock: IClock,
    free_space: IEgressFreeSpace,
) -> EgressStatusReader:
    """The operator's read of the fact export, answering with the export off too."""
    exported = boot.live(config)
    return EgressStatusReader(
        config=lambda: exported().config,
        rejected=boot.rejected is not None,
        missing_path_key=boot.missing_path_key(),
        exported_paths=exported,
        egress=egress,
        event_reads=event_reads,
        steps=steps,
        clock=clock,
        free_space=free_space,
    )


def egress_sweep(
    boot: EgressBoot,
    config: LiveEgressConfig,
    steps: IReadTraceSteps,
    egress: IWriteEgressCursor,
    event_reads: IReadEgressEvents,
    writer: IEgressWriter,
    configured: IConfiguredEgressWriter,
    events: HubEventLogService,
    clock: IClock,
) -> EgressSweep:
    """The fact-egress export sweep over the running export's writer."""
    exported = boot.live(config)
    return EgressSweep(
        steps=steps,
        egress=egress,
        event_reads=event_reads,
        paths=boot.exported.policy,
        writer=writer,
        events=events,
        clock=clock,
        config=lambda: exported().config,
        writer_for=configured.for_config,
        missing_key=lambda: exported().missing_key_env,
        announced_missing_key=boot.missing_path_key(),
    )


def egress_reset(
    boot: EgressBoot, config: LiveEgressConfig, egress: IWriteEgressCursor, events: HubEventLogService, clock: IClock
) -> EgressReset:
    """Moves a dataset's cursor; it refuses while the export is off."""
    exported = boot.live(config)
    return EgressReset(
        egress=egress,
        events=events,
        clock=clock,
        config=lambda: exported().config,
        active=boot.on(),
        missing_path_key=boot.missing_path_key(),
        exported_paths=exported,
    )


def egress_backfill(
    boot: EgressBoot,
    config: LiveEgressConfig,
    steps: IReadTraceSteps,
    egress: IReadEgress,
    event_reads: IReadEgressEvents,
    writers: IEgressBackfillWriters | None,
    clock: IClock,
) -> EgressBackfill:
    """Writes a past window through a fresh writer per backfill; it refuses while the export is off."""
    exported = boot.live(config)
    return EgressBackfill(
        steps=steps,
        egress=egress,
        event_reads=event_reads,
        paths=boot.exported.policy,
        clock=clock,
        config=lambda: exported().config,
        writers=None,
        writer_for=writers,
        missing_path_key=boot.missing_path_key(),
        exported_paths=exported,
    )


class ITraceStorePorts(IWriteTraceCursor, IReadTraceStatus, Protocol):
    """Every trace port the one trace store fills."""


#: The running export's writers the area aliases its writer roles onto: the kept writer, then the backfill's factory.
type EgressWriterRefs = tuple[Ref[IConfiguredEgressWriter], Ref[IEgressBackfillWriters]]

#: The running exports' sweeps, each ``None`` while its export is off: the trace export's, then the egress export's.
type ExportSweepRefs = tuple[Ref[TraceExportSweep] | None, Ref[EgressSweep] | None]


def _optional(missing: Any) -> Any:
    """The ``T | None`` key :meth:`Registry.optional` registers ``missing`` under."""
    return cast("Any", missing | None)


#: The process keys the exports always register, past the ports they alias.
_EXPORT_SERVICES: tuple[type[Any], ...] = (
    TraceReplay,
    TraceStatusReader,
    EgressStatusReader,
    EgressReset,
    EgressBackfill,
)

#: The process ports the exports alias onto the adapters they are handed.
_EXPORT_PORTS: tuple[type[Any], ...] = (
    IReadTraceSteps,
    IReadTraceStatus,
    IWriteTraceCursor,
    IReadEgress,
    IWriteEgressCursor,
    IEgressFreeSpace,
)

#: The borrowed values the root supplies the exports.
_EXPORT_CONTEXT: tuple[type[Any], ...] = (TracingSettings, EgressBoot, StepHydrations)

#: The keys registered only while their export is on.
_TRACE_ON_KEYS: tuple[type[Any], ...] = (ITraceExporter, TraceExportSweep)
_EGRESS_ON_KEYS: tuple[type[Any], ...] = (IConfiguredEgressWriter, IEgressBackfillWriters, EgressSweep)


def register_exports(
    registry: Registry,
    *,
    trace: Ref[ITraceStorePorts],
    egress: Ref[IWriteEgressCursor],
    project_labels: Ref[IReadProjectLabels],
    free_space: Ref[IEgressFreeSpace],
    trace_export: bool,
    egress_writers: EgressWriterRefs | None,
) -> ExportSweepRefs:
    """Declare the trace and egress ports over the stores handed in, their services once per process and each
    tenant's step hydration. The trace sweep and its exporter are present only with ``trace_export``, and the egress
    sweep and its writers only with ``egress_writers``; each is otherwise absent, and its ``T | None`` is ``None``.
    Hands back each running sweep's ``Ref``, ``None`` for an export that is off."""
    registry.alias(port(IReadTraceSteps), trace)
    registry.alias(port(IReadTraceStatus), trace)
    registry.alias(port(IWriteTraceCursor), trace)
    registry.alias(port(IReadEgress), egress)
    registry.alias(port(IWriteEgressCursor), egress)
    registry.alias(port(IReadProjectLabels), project_labels)
    registry.alias(port(IEgressFreeSpace), free_space)
    for borrowed in _EXPORT_CONTEXT:
        registry.context(port(borrowed), lifetime=Lifetime.PROCESS)
    registry.factory(step_hydration, lifetime=Lifetime.TENANT)
    for build in (trace_replay, trace_status, egress_status, egress_reset, egress_backfill):
        registry.factory(build, lifetime=Lifetime.PROCESS)
    trace_sweep_ref: Ref[TraceExportSweep] | None = None
    egress_sweep_ref: Ref[EgressSweep] | None = None
    if trace_export:
        exporter = registry.context(port(ITraceExporter), lifetime=Lifetime.PROCESS)
        registry.optional(port(ITraceExporter), exporter, lifetime=Lifetime.PROCESS)
        trace_sweep_ref = registry.factory(trace_export_sweep, lifetime=Lifetime.PROCESS)
        registry.optional(port(TraceExportSweep), trace_sweep_ref, lifetime=Lifetime.PROCESS)
    else:
        for missing in _TRACE_ON_KEYS:
            registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=EXPORT_OFF)
            registry.optional(port(missing), None, lifetime=Lifetime.PROCESS)
    if egress_writers is not None:
        configured, backfill = egress_writers
        registry.alias(port(IConfiguredEgressWriter), configured)
        registry.alias(port(IEgressBackfillWriters), backfill)
        registry.optional(port(IEgressBackfillWriters), backfill, lifetime=Lifetime.PROCESS)
        egress_sweep_ref = registry.factory(egress_sweep, lifetime=Lifetime.PROCESS)
        registry.optional(port(EgressSweep), egress_sweep_ref, lifetime=Lifetime.PROCESS)
    else:
        for missing in _EGRESS_ON_KEYS:
            registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=EXPORT_OFF)
        for missing in (IEgressBackfillWriters, EgressSweep):
            registry.optional(port(missing), None, lifetime=Lifetime.PROCESS)
    return trace_sweep_ref, egress_sweep_ref


def register_exports_absent(registry: Registry) -> None:
    """Declare the exports' ports, services, borrowed values, sweeps and step hydration absent, for the store-free
    graph."""
    for missing in (*_EXPORT_PORTS, *_EXPORT_SERVICES, *_EXPORT_CONTEXT, *_TRACE_ON_KEYS, *_EGRESS_ON_KEYS):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    for missing in (ITraceExporter, TraceExportSweep, IEgressBackfillWriters, EgressSweep):
        registry.absent(port(_optional(missing)), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
    registry.absent(port(IReadProjectLabels), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    registry.absent(port(StepHydration), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
