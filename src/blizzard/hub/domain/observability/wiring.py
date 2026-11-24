"""The observability area's registrations: the transcript, analytics, env-timeline, annotation, hub event log and
invariant ports over the adapters it is handed, and the services over them.

Each port aliases the one adapter instance the root hands in, so a reader and a writer in one tenant share it; the area
names no adapter. The tenant's transcript ingest and event derivation are built by explicit factory, since their
constructors default what the graph supplies: the ingest's fallback caps are the boot config's ``TranscriptCaps``, and
the derivation's extractors the shipped set this area registers. The hub event log and the invariant check are one
instance per process. The store-free graph declares each registration absent, from the same rosters."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.domain.observability.analytics.derivation import EventDerivationService, TenantEventDerivation
from blizzard.hub.domain.observability.analytics.events import IReadTranscriptEvents, IWriteTranscriptEvents
from blizzard.hub.domain.observability.analytics.extraction import TurnEventExtractors, shipped_extractors
from blizzard.hub.domain.observability.analytics.operational import IReadOperationalAnalytics
from blizzard.hub.domain.observability.analytics.queries import IReadAnalyticsEventQueries
from blizzard.hub.domain.observability.env_timeline import EnvTimelineReading, IReadEnvTimelineFacts
from blizzard.hub.domain.observability.forge_status import (
    IReadAnnotatedSources,
    IWriteAnnotatedSources,
    TenantAnnotation,
)
from blizzard.hub.domain.observability.hub_events import HubEventLogService, IReadHubEventLog, IWriteHubEventLog
from blizzard.hub.domain.observability.invariants import HubInvariantCheck, IHubInvariantChecker
from blizzard.hub.domain.observability.transcripts import (
    IReadTranscriptSegments,
    IWriteTranscriptSegments,
    TranscriptCaps,
    TranscriptIngestService,
)
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard
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
) -> None:
    """Declare the area's ports over the adapters handed in, its tenant services once per tenant, and the hub event
    log, the invariant check and the shipped extractor set once per process."""
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


def register_absent(registry: Registry) -> None:
    """Declare the area's ports, services and extractor set absent, for the store-free graph."""
    for missing in (*_TENANT_PORTS, *_TENANT_SERVICES, *_TENANT_FACTORY_KEYS):
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
    for missing in (*_PROCESS_PORTS, *_PROCESS_SERVICES, TurnEventExtractors):
        registry.absent(port(missing), lifetime=Lifetime.PROCESS, label=STORE_UNAVAILABLE)
